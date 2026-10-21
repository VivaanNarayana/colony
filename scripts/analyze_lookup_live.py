"""Audit native paired lookup trials and explicit per-answer reviewer labels."""

import argparse
import hashlib
import io
import json
import math
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path

from benchmark_statistics import reduction_interval, supported_improvement


def require(condition, message="Benchmark evidence failed integrity validation"):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_gates(main, feature, local, verification, implementations_verified, intervals=None):
    intervals = intervals or {}
    return {
        "loaded_implementations_verified": implementations_verified,
        "tests_and_lint_pass": verification["tests_passed"] > 0
        and verification["tests_failed"] == 0
        and verification["ruff_passed"],
        "local_lookup_improves": local.get("schema_version") == 2
        and local.get("implementation_isolated") is True
        and local.get("quality_passed") is True
        and local.get("lookup_improves") is True
        and local.get("same_implementation") is False,
        "correctness_not_reduced": main["tasks"] == feature["tasks"] > 0
        and feature["correct"] == feature["tasks"]
        and feature["valid_citations"] == feature["tasks"]
        and feature["valid_citations"] >= main["valid_citations"],
        "feature_follows_tool_protocol": main["protocol_violations"] == 0
        and feature["protocol_violations"] == 0,
        "observed_live_tokens_improve": supported_improvement(
            main["tokens"], feature["tokens"], intervals.get("tokens")
        ),
        "observed_uncached_input_plus_output_improve": supported_improvement(
            main["uncached_input_plus_output"],
            feature["uncached_input_plus_output"],
            intervals.get("uncached_input_plus_output"),
        ),
        "observed_live_time_improves": supported_improvement(
            main["seconds"], feature["seconds"], intervals.get("seconds")
        ),
    }


def compare(results, local):
    protocol = read_json(results / "protocol.json")
    verification = read_json(results / "verification.json")
    require(
        read_json(results / "completed.json")["sessions"] == 48,
        "Expected 48 completed paired sessions",
    )
    require(
        len(protocol["tasks"]) == len(set(protocol["tasks"])) == 24,
        "Expected 24 distinct task identities",
    )
    task_spec = {t["task_id"]: t for t in protocol.get("task_spec", [])}
    if protocol.get("schema_version") == 2:
        require(
            set(task_spec) == set(protocol["tasks"]) and len(protocol["task_spec"]) == 24,
            "Frozen task specification incomplete",
        )
    report = {
        "schema_version": 2,
        "evaluation_role": "public-development-only",
        "baseline_commit": protocol["baseline_commit"],
        "feature_commit": protocol["feature_commit"],
        "arms": {},
        "limitations": [
            "Every session of this final alternating trial is retained without per-task retries or dropped outcomes. Earlier exploratory revisions are retained separately.",
            "Primary-assistant grading of core facts and citations, not independent blind review.",
            "Repeatedly observed self-repository tasks are not held-out production evidence.",
            "Provider sampling and cache variation remain; bootstrap intervals are descriptive, not a correction for repeated selection or a production-readiness claim.",
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
            require(len(proofs) == 25)
            require(proofs[0]["role"] == "server")
            require([p["task_id"] for p in proofs[1:]] == protocol["tasks"])
            for proof in proofs:
                require(proof["revision"] == revision and proof["source_sha256"] == expected)
                require(
                    all(
                        (
                            Path(p).is_relative_to(Path(proof["source"]))
                            for p in proof["module_paths"].values()
                        )
                    )
                )
                require("colony." + proof["role"] in proof["module_paths"])
        manifests[arm] = read_json(directory / "corpus-sha256.json")
        reviews = read_json(directory / "reviews.json")
        rows = [
            json.loads(s) for s in (directory / "raw-measurements.jsonl").read_text().splitlines()
        ]
        require(len(rows) == 24)
        require([r["task_id"] for r in rows] == protocol["tasks"])
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
            require(len(completed) == 1 and completed[0]["usage"] == row["usage"])
            require(row["tokens"] == row["usage"]["input_tokens"] + row["usage"]["output_tokens"])
            require(
                all(
                    type(row["usage"][key]) is int and row["usage"][key] >= 0
                    for key in ["input_tokens", "output_tokens", "cached_input_tokens"]
                ),
                "Invalid native token accounting",
            )
            require(
                row["usage"]["cached_input_tokens"] <= row["usage"]["input_tokens"],
                "Cached input exceeds total input",
            )
            require(
                type(row["seconds"]) in (int, float)
                and math.isfinite(row["seconds"])
                and row["seconds"] > 0,
                "Invalid session duration",
            )
            items = [e["item"] for e in events if e.get("type") == "item.completed"]
            calls = [
                i
                for i in items
                if i["type"] in {"command_execution", "mcp_tool_call", "web_search"}
            ]
            require(
                all(
                    (
                        c["type"] == "mcp_tool_call" and c.get("server") in {"repository", "colony"}
                        for c in calls
                    )
                )
            )
            require(len(calls) == row["tool_calls"])
            research = [c for c in calls if c["server"] == "repository"]
            for denied in research[6:]:
                text = json.dumps(denied.get("result")) + str(denied.get("error"))
                require("Research tool budget exhausted" in text)
            asks = [c for c in calls if c["server"] == "colony" and c["tool"] == "ask"]
            require(len(asks) == row["ask_calls"] and asks == row["asks"])
            prompt = (directory / f"{name}.prompt.txt").read_text(encoding="utf-8")
            question = prompt.rsplit("\nQuestion: ", 1)[1]
            if task_spec:
                expected_task = task_spec[row["task_id"]]
                require(
                    row["family"] == expected_task["family"]
                    and question == expected_task["question"],
                    "Measured question/family differs from frozen task specification",
                )
            prompts.setdefault(row["task_id"], {})[arm] = prompt
            compliant = len(research) <= 6 and len(asks) == 1 and calls[0] == asks[0]
            compliant = compliant and all(
                a["arguments"] == {"question": question, "namespace": "bench"} for a in asks
            )
            compliant = compliant and len(row["answer"].split()) <= 130
            totals["protocol_violations"] += not compliant
            require(
                row["answer"]
                == "\n".join((i["text"] for i in items if i["type"] == "agent_message"))
            )
            packets = [json.loads(a["result"]["content"][0]["text"]) for a in asks]
            # Retain every attempted call and its cost. A duplicate observation
            # gives no additional data; a different question/result would make
            # this a different research budget and must not pass the audit.
            require(asks and calls[0] == asks[0])
            require(
                all((a["arguments"] == {"question": question, "namespace": "bench"} for a in asks)),
                f"{arm}/{name}: lookup question/namespace differs from the exact requested values",
            )
            require(all((p == packets[0] for p in packets)))
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
                        require(
                            hashlib.sha256(data["content"].encode("utf-8")).hexdigest()
                            == source["content_hash"]
                            == manifests[arm].get(source["ref"]),
                            f"{arm}/{name}: returned source bytes do not match corpus",
                        )
                        read_sources[source["ref"]] = source["content_hash"]
                    elif "sha256" in data:  # Retain audit support for earlier trials.
                        require(
                            hashlib.sha256(data["content"].encode("utf-8")).hexdigest()
                            == data["sha256"]
                            == manifests[arm].get(data["path"]),
                            f"{arm}/{name}: returned source bytes do not match corpus",
                        )
                        read_sources[data["path"]] = data["sha256"]
                if call["server"] == "colony" and call["tool"] == "push":
                    require(
                        call["arguments"].get("question") == question
                        and call["arguments"].get("namespace") == "bench",
                        f"{arm}/{name}: push changed the question or namespace",
                    )
                    sources = call["arguments"].get("sources", [])
                    require(sources, f"{arm}/{name}: research push has no supporting file source")
                    for source in sources:
                        require(
                            source["content_hash"]
                            == read_sources.get(source["ref"])
                            == manifests[arm].get(source["ref"]),
                            f"{arm}/{name}: push fingerprint does not match an actual source read",
                        )
            review = reviews[name]
            require(type(review["correct"]) is bool and type(review["citation_valid"]) is bool)
            require(len(review["relevant_asks"]) == len(packets))
            labels = review["relevant_asks"]
            require(
                all(
                    (
                        type(label) is bool and (not label or packet.get("hit"))
                        for label, packet in zip(labels, packets, strict=True)
                    )
                )
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
    require(manifests["main"] == manifests["feature"])
    require(
        all(pair["main"]["family"] == pair["feature"]["family"] for pair in paired.values()),
        "Paired task family differs between arms",
    )
    if "research_adapter_sha256" in protocol:
        adapter = subprocess.check_output(
            ["git", "show", f"{protocol['feature_commit']}:scripts/benchmark_repo_mcp.py"],
            cwd=Path(__file__).resolve().parents[1],
        )
        require(hashlib.sha256(adapter).hexdigest() == protocol["research_adapter_sha256"])
        require(
            all(
                (
                    m["research_adapter_sha256"] == protocol["research_adapter_sha256"]
                    for m in metadata.values()
                )
            )
        )
    for field in ["commit", "model", "reasoning_effort", "cli", "task_order", "prompt_sha256"]:
        require(metadata["main"][field] == metadata["feature"][field])
    require(all((p["main"] == p["feature"] for p in prompts.values())))
    require(metadata["main"]["implementation_commit"] == protocol["baseline_commit"])
    require(metadata["feature"]["implementation_commit"] == protocol["feature_commit"])
    require(local["baseline_commit"] == protocol["baseline_commit"])
    if local.get("schema_version") == 2:
        require(
            local["same_implementation"]
            == (
                local["loaded_implementations"]["main"]["source_sha256"]
                == local["loaded_implementations"]["feature"]["source_sha256"]
            ),
            "Local A/A identity flag inconsistent",
        )
        for arm, key in [("main", "baseline_commit"), ("feature", "feature_commit")]:
            archive = subprocess.check_output(
                ["git", "archive", "--format=zip", local[key], "src"],
                cwd=Path(__file__).resolve().parents[1],
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                expected = {
                    name.removeprefix("src/"): hashlib.sha256(z.read(name)).hexdigest()
                    for name in z.namelist()
                    if name.endswith(".py")
                }
            proof = local["loaded_implementations"][arm]
            require(proof["source_sha256"] == expected, "Local source revision mismatch")
            require(
                all(
                    Path(p).is_relative_to(Path(proof["source"]))
                    for p in proof["module_paths"].values()
                ),
                "Local import escaped source",
            )
            require(
                all(
                    "colony." + module in proof["module_paths"]
                    for module in [
                        "store",
                        "context",
                        "retrieval",
                        "models",
                        "security",
                        "validation",
                    ]
                ),
                "Local module proof incomplete",
            )
        require(
            local["quality_passed"]
            == all(m["oracle_passes"] == m["lookups"] > 0 for m in local["modes"].values()),
            "Local quality totals inconsistent",
        )
        require(
            local["lookup_improves"]
            == (
                not local["same_implementation"]
                and local["quality_passed"]
                and supported_improvement(
                    local["modes"]["main"]["seconds"],
                    local["modes"]["feature"]["seconds"],
                    local["latency_reduction_95pct"],
                )
            ),
            "Local improvement totals inconsistent",
        )
    # Verify the entire package, including transitive dependencies. Legacy hybrid
    # local runs remain readable but cannot satisfy the independent local gate.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            local["feature_commit"],
            protocol["feature_commit"],
            "--",
            "src/colony",
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
        if main[field] > 0
    }
    families = defaultdict(lambda: {arm: defaultdict(float) for arm in ["main", "feature"]})
    for pair in paired.values():
        for arm, row in pair.items():
            row = {
                **row,
                "uncached_input_plus_output": row["tokens"] - row["usage"]["cached_input_tokens"],
            }
            for field in ["tokens", "seconds", "uncached_input_plus_output"]:
                families[row["family"]][arm][field] += row[field]
    report["families"] = dict(families)
    intervals = {
        field: reduction_interval(families.values(), field)
        for field in ["tokens", "seconds", "uncached_input_plus_output"]
    }
    report["descriptive_family_bootstrap_reduction_95pct"] = intervals
    report["same_prompts_order_and_corpus"] = True
    report["gate"] = evaluate_gates(
        main, feature, local, verification, implementations_verified, intervals
    )
    report["all_gates_passed"] = all(report["gate"].values())
    report["data_budget_verified"] = True
    report["mcp_surface_identical"] = (
        len(tool_surface) == 2 and tool_surface["main"] == tool_surface["feature"]
    )
    report["blocking_gates"] = [name for name, passed in report["gate"].items() if not passed]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare(args.results, read_json(args.local))
    except (
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        report = {
            "schema_version": 2,
            "all_gates_passed": False,
            "blocking_gates": ["evidence_integrity"],
            "integrity_error": str(error),
        }
    (args.results / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["all_gates_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
