"""Paired local CPU lookup benchmark; no provider calls or synthetic token estimates."""

import argparse
import importlib.util
import json
import statistics
import subprocess
import time
import types
from pathlib import Path

import colony.context as context
import colony.store as optimized
from colony.models import Grant, Scope, Trail
from colony.store import Store
from colony.validation import snapshot

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--name", default="lookup")
parser.add_argument("--repo", type=Path, default=ROOT.parent)
parser.add_argument("--runtime", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--baseline-ref", default="origin/main")
parser.add_argument("--repetitions", type=int, default=5)
args = parser.parse_args()
REPO = args.repo.resolve()
OUTPUT = args.output.resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
baseline = types.ModuleType("baseline_store")
source = subprocess.check_output(
    ["git", "show", f"{args.baseline_ref}:src/colony/store.py"], cwd=REPO, text=True
)
exec(compile(source, "main:src/colony/store.py", "exec"), baseline.__dict__)
baseline_context = types.ModuleType("baseline_context")
exec(
    compile(
        subprocess.check_output(
            ["git", "show", f"{args.baseline_ref}:src/colony/context.py"], cwd=REPO, text=True
        ),
        "main:src/colony/context.py",
        "exec",
    ),
    baseline_context.__dict__,
)
baseline.excerpts = baseline_context.excerpts
baseline.source_chunks = baseline_context.source_chunks
spec = importlib.util.spec_from_file_location("benchmark", REPO / "scripts/benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
tasks = benchmark.tasks()
runtime = args.runtime.resolve()
if runtime.exists():
    raise RuntimeError("Use a fresh runtime directory")
grant = Grant(
    member_id="profile",
    name="Profile",
    server="http://127.0.0.1:7331",
    expires_at=int(time.time()) + 3600,
    scopes=[Scope(namespace="bench", push=True, pull=True)],
)
stores = {
    "main": baseline.Store(runtime / "main", REPO),
    "feature": Store(runtime / "feature", REPO),
}
# Seed one factual answer per family, citing only the real frozen checkout.
# This measures warm retrieval and is separate from the empty-memory live trial.
for i, (_family, ref, answer, questions) in enumerate(benchmark.FAMILIES):
    trail = Trail(
        id=f"{i + 1:032x}",
        namespace="bench",
        question=questions[0],
        content=answer,
        sources=[snapshot(REPO, ref)],
    )
    for store in stores.values():
        store.push(trail, grant)
for store in stores.values():
    with store.connect() as db:
        db.execute("UPDATE trails SET created_at=?", (int(time.time()),))

counts = {"main": 0, "feature": 0}
rank_counts = {mode: {"calls": 0, "passages": 0} for mode in stores}
active_mode = None
for context_module, context_mode in [(baseline_context, "main"), (context, "feature")]:
    original_context_rank = context_module.rank

    def counted_context_rank(
        question, passages, _name=context_mode, _original=original_context_rank
    ):
        if active_mode is not None and passages:
            rank_counts[_name]["calls"] += 1
            rank_counts[_name]["passages"] += len(passages)
        return _original(question, passages)

    context_module.rank = counted_context_rank
for name, module in [("main", baseline), ("feature", optimized)]:
    original = module.embed

    def counted(text, *, query=False, _name=name, _original=original):
        if query:
            counts[_name] += 1
        return _original(text, query=query)

    module.embed = counted
    original_rank = module.rank

    def counted_rank(question, passages, _name=name, _original=original_rank):
        if passages:
            rank_counts[_name]["calls"] += 1
            rank_counts[_name]["passages"] += len(passages)
        return _original(question, passages)

    module.rank = counted_rank


def semantic(result):
    return {k: v for k, v in result.items() if k not in {"created_at", "checks"}}


# Warm both models and SQLite once per implementation before timed repetitions.
for store in stores.values():
    store.ask(tasks[0]["question"], ["bench"], 604800, 0.60)
counts = {"main": 0, "feature": 0}
rank_counts = {mode: {"calls": 0, "passages": 0} for mode in stores}
rows = []
for repeat in range(args.repetitions):
    for i, task in enumerate(tasks):
        modes = ["main", "feature"] if (repeat + i) % 2 == 0 else ["feature", "main"]
        pair = {}
        for mode in modes:
            active_mode = mode
            previous = counts[mode]
            previous_rank = dict(rank_counts[mode])
            start = time.perf_counter()
            result = stores[mode].ask(task["question"], ["bench"], 604800, 0.60)
            elapsed = time.perf_counter() - start
            pair[mode] = semantic(result)
            rows.append(
                {
                    "repeat": repeat,
                    "task_id": task["task_id"],
                    "mode": mode,
                    "seconds": elapsed,
                    "query_embeddings": counts[mode] - previous,
                    "reranker_calls": rank_counts[mode]["calls"] - previous_rank["calls"],
                    "reranked_passages": rank_counts[mode]["passages"] - previous_rank["passages"],
                    "hit": result["hit"],
                    "kind": result.get("match_kind"),
                }
            )
        if pair["main"] != pair["feature"]:
            (OUTPUT / f"{args.name}-mismatch.json").write_text(
                json.dumps(pair, indent=2), encoding="utf-8"
            )
            raise AssertionError(f"Retrieval changed for {task['task_id']}")
    print(f"Completed repetition {repeat + 1}/{args.repetitions}", flush=True)
summary = {
    "baseline_commit": subprocess.check_output(
        ["git", "rev-parse", args.baseline_ref], cwd=REPO, text=True
    ).strip(),
    "feature_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip(),
    "protocol": f"24 regression queries, {args.repetitions} repetitions, paired alternating order; 8 identical seeded trails; warmed local models; same CPU and checkout; no provider calls",
    "identical_retrieval_pairs": len(rows) // 2,
    "modes": {},
}
for mode in stores:
    selected = [r for r in rows if r["mode"] == mode]
    summary["modes"][mode] = {
        "lookups": len(selected),
        "query_embeddings": counts[mode],
        "reranker_calls": rank_counts[mode]["calls"],
        "reranked_passages": rank_counts[mode]["passages"],
        "seconds_total": sum(r["seconds"] for r in selected),
        "seconds_median": statistics.median(r["seconds"] for r in selected),
        "seconds_p95": sorted(r["seconds"] for r in selected)[int(0.95 * len(selected)) - 1],
    }
summary["lookup_time_reduction"] = (
    1 - summary["modes"]["feature"]["seconds_total"] / summary["modes"]["main"]["seconds_total"]
)
(OUTPUT / f"{args.name}-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(OUTPUT / f"{args.name}-measurements.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2), flush=True)
