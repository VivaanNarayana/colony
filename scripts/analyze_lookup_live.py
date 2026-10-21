"""Audit native paired lookup trials and explicit per-answer reviewer labels."""

import argparse
import hashlib
import io
import json
import random
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_gates(main, feature, local, verification, implementations_verified):
    return {
        "loaded_implementations_verified": implementations_verified,
        "tests_and_lint_pass": verification["tests_passed"] > 0
        and verification["tests_failed"] == 0
        and verification["ruff_passed"],
        "local_lookup_improves": local["lookup_time_reduction"] > 0,
        "correctness_not_reduced": feature["correct"] >= main["correct"],
        "feature_follows_tool_protocol": feature["protocol_violations"] == 0,
        "observed_live_tokens_improve": feature["tokens"] < main["tokens"],
        "observed_uncached_input_plus_output_improve": feature["uncached_input_plus_output"]
        < main["uncached_input_plus_output"],
        "observed_live_time_improves": feature["seconds"] < main["seconds"],
    }


def compare(results, local):
    protocol = read_json(results / "protocol.json")
    verification = read_json(results / "verification.json")
    assert read_json(results / "completed.json")["sessions"] == 48
    report = {
        "baseline_commit": protocol["baseline_commit"],
        "feature_commit": protocol["feature_commit"],
        "arms": {},
        "limitations": [
            "Every session of this final alternating trial is retained without per-task retries or dropped outcomes. Earlier exploratory revisions are retained separately.",
            "Primary-assistant grading of core facts and citations, not independent blind review.",
            "Repeatedly observed self-repository tasks are not held-out production evidence.",
            "Provider sampling and cache variation remain; bootstrap intervals are descriptive.",
        ],
    }
    paired, prompts, metadata, manifests = {}, {}, {}, {}
    tool_surface = {}
    implementations_verified = True
    for arm in ["main", "feature"]:
        directory = results / arm
        metadata[arm] = read_json(directory / "metadata.json")
        proof_path = directory / "loaded-implementations.json"
        if not proof_path.exists():
            implementations_verified = False
        else:
            revision = protocol["baseline_commit" if arm == "main" else "feature_commit"]
            archive = subprocess.check_output(
                ["git", "archive", "--format=zip", revision, "src"],
                cwd=Path(__file__).resolve().parents[1],
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                expected = {
                    name.removeprefix("src/"): hashlib.sha256(z.read(name)).hexdigest()
                    for name in z.namelist()
                    if name.endswith(".py")
                }
            proofs = read_json(proof_path)
            tool_surface[arm] = {
                name: expected[name]
                for name in ["colony/mcp.py", "colony/client.py", "colony/models.py"]
            }
            assert len(proofs) == 25
            assert proofs[0]["role"] == "server"
            assert [p["task_id"] for p in proofs[1:]] == protocol["tasks"]
            for proof in proofs:
                assert proof["revision"] == revision and proof["source_sha256"] == expected
                assert all(
                    Path(p).is_relative_to(Path(proof["source"]))
                    for p in proof["module_paths"].values()
                )
                assert "colony." + proof["role"] in proof["module_paths"]
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
            for denied in research[6:]:
                text = json.dumps(denied.get("result")) + str(denied.get("error"))
                assert "Research tool budget exhausted" in text
            asks = [c for c in calls if c["server"] == "colony" and c["tool"] == "ask"]
            assert len(asks) == row["ask_calls"] and asks == row["asks"]
            prompt = (directory / f"{name}.prompt.txt").read_text(encoding="utf-8")
            question = prompt.rsplit("\nQuestion: ", 1)[1]
            prompts.setdefault(row["task_id"], {})[arm] = prompt
            compliant = len(research) <= 6 and len(asks) == 1 and calls[0] == asks[0]
            compliant = compliant and all(
                a["arguments"] == {"question": question, "namespace": "bench"} for a in asks
            )
            totals["protocol_violations"] += not compliant
            assert row["answer"] == "\n".join(
                i["text"] for i in items if i["type"] == "agent_message"
            )
            packets = [json.loads(a["result"]["content"][0]["text"]) for a in asks]
            # Retain every attempted call and its cost. A duplicate observation
            # gives no additional data; a different question/result would make
            # this a different research budget and must not pass the audit.
            assert asks and calls[0] == asks[0]
            assert all(
                a["arguments"] == {"question": question, "namespace": "bench"} for a in asks
            ), f"{arm}/{name}: lookup question/namespace differs from the exact requested values"
            assert all(p == packets[0] for p in packets)
            read_sources = {}
            for call in calls:
                if (
                    call["server"] == "repository"
                    and call["tool"] == "read_file"
                    and call.get("status") == "completed"
                ):
                    data = json.loads(call["result"]["content"][0]["text"])
                    if "source" in data:
                        source = data["source"]
                        read_sources[source["ref"]] = source["content_hash"]
                    elif "sha256" in data:  # Retain audit support for earlier trials.
                        read_sources[data["path"]] = data["sha256"]
                if call["server"] == "colony" and call["tool"] == "push":
                    sources = call["arguments"].get("sources", [])
                    assert sources, f"{arm}/{name}: research push has no supporting file source"
                    for source in sources:
                        assert (
                            source["content_hash"]
                            == read_sources.get(source["ref"])
                            == manifests[arm].get(source["ref"])
                        ), f"{arm}/{name}: push fingerprint does not match an actual source read"
            review = reviews[name]
            assert type(review["correct"]) is bool and type(review["citation_valid"]) is bool
            assert len(review["relevant_asks"]) == len(packets)
            labels = review["relevant_asks"]
            assert all(
                type(label) is bool and (not label or packet.get("hit"))
                for label, packet in zip(labels, packets, strict=True)
            )
            totals["correct"] += review["correct"]
            totals["valid_citations"] += review["citation_valid"]
            totals["raw_hits"] += sum(bool(p.get("hit")) for p in packets)
            totals["relevant_hits"] += sum(labels)
            totals["research_free_correct"] += review["correct"] and any(labels) and not research
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
    if "research_adapter_sha256" in protocol:
        adapter = subprocess.check_output(
            ["git", "show", f"{protocol['feature_commit']}:scripts/benchmark_repo_mcp.py"],
            cwd=Path(__file__).resolve().parents[1],
        )
        assert hashlib.sha256(adapter).hexdigest() == protocol["research_adapter_sha256"]
        assert all(
            m["research_adapter_sha256"] == protocol["research_adapter_sha256"]
            for m in metadata.values()
        )
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
    report["gate"] = evaluate_gates(main, feature, local, verification, implementations_verified)
    report["all_gates_passed"] = all(report["gate"].values())
    report["all_predeclared_gates_passed"] = report["all_gates_passed"]
    report["original_performance_gates_passed"] = all(
        report["gate"][key]
        for key in [
            "local_lookup_improves",
            "correctness_not_reduced",
            "observed_live_tokens_improve",
            "observed_live_time_improves",
        ]
    )
    report["data_budget_verified"] = True
    report["mcp_surface_identical"] = (
        len(tool_surface) == 2 and tool_surface["main"] == tool_surface["feature"]
    )
    report["merge_review_ready"] = report["all_gates_passed"]
    report["blocking_gates"] = [name for name, passed in report["gate"].items() if not passed]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.results, read_json(args.local))
    (args.results / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["all_gates_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
