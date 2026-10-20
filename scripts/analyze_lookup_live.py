"""Audit native paired lookup trials and explicit per-answer reviewer labels."""

import argparse
import json
import random
import subprocess
from collections import defaultdict
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compare(results, local):
    protocol = read_json(results / "protocol.json")
    verification = read_json(results / "verification.json")
    assert read_json(results / "completed.json")["sessions"] == 48
    report = {
        "baseline_commit": protocol["baseline_commit"],
        "feature_commit": protocol["feature_commit"],
        "arms": {},
        "limitations": [
            "One alternating 24-question regression trial; no best-of selection or retries.",
            "Primary-assistant grading of core facts and citations, not independent blind review.",
            "Repeatedly observed self-repository tasks are not held-out production evidence.",
            "Provider sampling and cache variation remain; bootstrap intervals are descriptive.",
        ],
    }
    paired, prompts, metadata, manifests = {}, {}, {}, {}
    for arm in ["main", "feature"]:
        directory = results / arm
        metadata[arm] = read_json(directory / "metadata.json")
        manifests[arm] = read_json(directory / "corpus-sha256.json")
        reviews = read_json(directory / "reviews.json")
        rows = [
            json.loads(s) for s in (directory / "raw-measurements.jsonl").read_text().splitlines()
        ]
        assert len(rows) == 24
        assert [r["task_id"] for r in rows] == protocol["tasks"]
        totals = defaultdict(int)
        for row in rows:
            name = row["task_id"] + ".colony"
            events = [
                json.loads(s)
                for s in (directory / f"{name}.events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            completed = [e for e in events if e.get("type") == "turn.completed"]
            assert len(completed) == 1 and completed[0]["usage"] == row["usage"]
            assert row["tokens"] == row["usage"]["input_tokens"] + row["usage"]["output_tokens"]
            items = [e["item"] for e in events if e.get("type") == "item.completed"]
            calls = [
                i
                for i in items
                if i["type"] in {"command_execution", "mcp_tool_call", "web_search"}
            ]
            assert all(
                c["type"] == "mcp_tool_call" and c.get("server") in {"repository", "colony"}
                for c in calls
            )
            assert len(calls) == row["tool_calls"]
            research = [c for c in calls if c["server"] == "repository"]
            assert len(research) <= 6
            asks = [c for c in calls if c["server"] == "colony" and c["tool"] == "ask"]
            assert len(asks) == row["ask_calls"] == 1
            assert calls[0] == asks[0] and asks == row["asks"]
            question = asks[0]["arguments"]["question"]
            prompt = (directory / f"{name}.prompt.txt").read_text(encoding="utf-8")
            assert prompt.endswith("Question: " + question)
            prompts.setdefault(row["task_id"], {})[arm] = prompt
            assert asks[0]["arguments"]["namespace"] == "bench"
            assert row["answer"] == "\n".join(
                i["text"] for i in items if i["type"] == "agent_message"
            )
            packet = json.loads(asks[0]["result"]["content"][0]["text"])
            review = reviews[name]
            assert type(review["correct"]) is bool and type(review["citation_valid"]) is bool
            assert len(review["relevant_asks"]) == 1
            relevant = review["relevant_asks"][0]
            assert type(relevant) is bool and (not relevant or packet.get("hit"))
            totals["correct"] += review["correct"]
            totals["valid_citations"] += review["citation_valid"]
            totals["raw_hits"] += bool(packet.get("hit"))
            totals["relevant_hits"] += relevant
            totals["research_free_correct"] += review["correct"] and relevant and not research
            totals["tokens"] += row["tokens"]
            totals["cached_input_tokens"] += row["usage"]["cached_input_tokens"]
            totals["tool_calls"] += len(calls)
            totals["research_calls"] += len(research)
            totals["ask_calls"] += len(asks)
            totals["seconds"] += row["seconds"]
            paired.setdefault(row["task_id"], {})[arm] = row
        totals["tasks"] = len(rows)
        totals["uncached_input_plus_output"] = totals["tokens"] - totals["cached_input_tokens"]
        totals["seconds"] = round(totals["seconds"], 3)
        report["arms"][arm] = dict(totals)
    assert manifests["main"] == manifests["feature"]
    for field in ["commit", "model", "reasoning_effort", "cli", "task_order", "prompt_sha256"]:
        assert metadata["main"][field] == metadata["feature"][field]
    assert all(p["main"] == p["feature"] for p in prompts.values())
    assert metadata["main"]["implementation_commit"] == protocol["baseline_commit"]
    assert metadata["feature"]["implementation_commit"] == protocol["feature_commit"]
    assert local["baseline_commit"] == protocol["baseline_commit"]
    # MCP presentation/tooling may change between local and live trials. Verify
    # every runtime dependency exercised by the provider-free Store benchmark.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            local["feature_commit"],
            protocol["feature_commit"],
            "--",
            "src/colony/store.py",
            "src/colony/context.py",
            "src/colony/retrieval.py",
            "src/colony/models.py",
            "src/colony/security.py",
            "src/colony/validation.py",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
    )
    report["local_lookup_code_unchanged"] = True
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            verification["commit"],
            protocol["feature_commit"],
            "--",
            "src/colony",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
    )
    report["verification"] = verification
    main, feature = report["arms"]["main"], report["arms"]["feature"]
    report["observed_reductions"] = {
        field: 1 - feature[field] / main[field]
        for field in ["tokens", "seconds", "tool_calls", "uncached_input_plus_output"]
    }
    families = defaultdict(lambda: {arm: defaultdict(float) for arm in ["main", "feature"]})
    for pair in paired.values():
        for arm, row in pair.items():
            for field in ["tokens", "seconds"]:
                families[row["family"]][arm][field] += row[field]
    report["families"] = dict(families)
    rng = random.Random(20261020)
    intervals = {}
    for field in ["tokens", "seconds"]:
        samples = []
        for _ in range(10000):
            chosen = rng.choices(list(families.values()), k=len(families))
            samples.append(
                1 - sum(p["feature"][field] for p in chosen) / sum(p["main"][field] for p in chosen)
            )
        samples.sort()
        intervals[field] = [samples[250], samples[9749]]
    report["descriptive_family_bootstrap_reduction_95pct"] = intervals
    report["same_prompts_order_and_corpus"] = True
    report["gate"] = {
        "tests_and_lint_pass": verification["tests_passed"] > 0
        and verification["tests_failed"] == 0
        and verification["ruff_passed"],
        "local_lookup_improves": local["lookup_time_reduction"] > 0,
        "correctness_not_reduced": feature["correct"] >= main["correct"],
        "observed_live_tokens_improve": feature["tokens"] < main["tokens"],
        "observed_live_time_improves": feature["seconds"] < main["seconds"],
    }
    report["all_gates_passed"] = all(report["gate"].values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.results, read_json(args.local))
    (args.results / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
