"""Recompute retained local benchmark evidence without invoking product models."""

import hashlib
import json
import math
import random
import tempfile
from collections import defaultdict
from pathlib import Path

from benchmark_statistics import reduction_interval, supported_improvement
from lookup_workload import assess, workload


def require(condition, message):
    if not condition:
        raise ValueError("Local raw evidence: " + message)


def audit_local(summary, directory, *, name="lookup"):
    """Verify the frozen workload, every observation, and all derived gate fields."""
    require(directory is not None, "directory is required")
    require(bool(name) and Path(name).name == name, "name must be a plain run name")
    directory = Path(directory)
    plan = json.loads((directory / f"{name}-plan.json").read_text(encoding="utf-8"))
    for field in [
        "schema_version",
        "evaluation_role",
        "baseline_commit",
        "feature_commit",
        "seed",
        "repetitions",
        "workload_sha256",
        "harness_sha256",
    ]:
        require(plan[field] == summary[field], "plan differs from summary: " + field)
    require(type(plan["seed"]) is int, "seed must be an integer")
    require(
        type(plan["repetitions"]) is int and plan["repetitions"] >= 2,
        "at least two repetitions required",
    )
    spec_bytes = (directory / f"{name}-workload.json").read_bytes()
    require(
        hashlib.sha256(spec_bytes).hexdigest() == plan["workload_sha256"],
        "workload fingerprint differs",
    )
    spec = json.loads(spec_bytes)
    require(type(spec["variants"]) is int and spec["variants"] >= 1, "invalid variants")
    require(spec == workload(plan["seed"], spec["variants"]), "workload is not canonical")
    cases = {c["task_id"]: c for c in spec["cases"]}
    require(len(cases) == len(spec["cases"]), "duplicate case identities")
    rows = [
        json.loads(s)
        for s in (directory / f"{name}-measurements.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rng = random.Random(plan["seed"])
    expected_order = []
    for repeat in range(plan["repetitions"]):
        for index, case in enumerate(rng.sample(spec["cases"], len(cases))):
            arms = ["main", "feature"] if (repeat + index) % 2 == 0 else ["feature", "main"]
            expected_order.extend((repeat, case["task_id"], arm) for arm in arms)
    require(
        [(r["repeat"], r["task_id"], r["mode"]) for r in rows] == expected_order,
        "rows are missing, duplicated, reordered or outside the frozen trial",
    )
    totals = {
        arm: {
            "lookups": 0,
            "oracle_passes": 0,
            "seconds": 0.0,
            "query_embeddings": 0,
            "reranker_calls": 0,
            "reranked_passages": 0,
        }
        for arm in ["main", "feature"]
    }
    clusters = defaultdict(lambda: {arm: {"seconds": 0.0} for arm in totals})
    with tempfile.TemporaryDirectory(prefix="colony-local-audit-") as temporary:
        root = Path(temporary)
        for task, case in cases.items():
            repo = root / task
            repo.mkdir()
            # Canonical workload equality is checked before writing any paths.
            for ref, text in {**case["files"], **case.get("mutations", {})}.items():
                if text is not None:
                    path = repo / ref
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(text.encode("utf-8"))
        for row in rows:
            case = cases[row["task_id"]]
            require(row["family"] == case["family"], "case family differs")
            require(
                type(row["seconds"]) in (int, float)
                and math.isfinite(row["seconds"])
                and row["seconds"] > 0,
                "invalid lookup duration",
            )
            for field in ["query_embeddings", "reranker_calls", "reranked_passages"]:
                require(type(row[field]) is int and row[field] >= 0, "invalid " + field)
            errors = assess(case, row["result"], root / row["task_id"])
            require(
                row["oracle_errors"] == errors, "recorded oracle errors differ from source checks"
            )
            total = totals[row["mode"]]
            total["lookups"] += 1
            total["oracle_passes"] += not errors
            for field in ["seconds", "query_embeddings", "reranker_calls", "reranked_passages"]:
                total[field] += row[field]
            clusters[row["family"]][row["mode"]]["seconds"] += row["seconds"]
    require(totals == summary["modes"], "summary counts/timings differ from raw rows")
    interval = reduction_interval(clusters.values(), "seconds", seed=plan["seed"])
    require(
        interval == summary["latency_reduction_95pct"], "summary interval differs from raw rows"
    )
    quality = all(t["oracle_passes"] == t["lookups"] for t in totals.values())
    improved = (
        not summary["same_implementation"]
        and quality
        and supported_improvement(totals["main"]["seconds"], totals["feature"]["seconds"], interval)
    )
    require(summary["quality_passed"] == quality, "summary quality differs")
    require(summary["lookup_improves"] == improved, "summary improvement differs")
    reduction = 1 - totals["feature"]["seconds"] / totals["main"]["seconds"]
    require(summary["lookup_time_reduction"] == reduction, "summary reduction differs")
    return {
        "observations": len(rows),
        "workload_sha256": plan["workload_sha256"],
        "raw_measurements_verified": True,
    }
