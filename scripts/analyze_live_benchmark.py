"""Produce auditable benchmark totals after explicit answer/retrieval review."""

import argparse
import csv
import importlib.util
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--results", type=Path, required=True)
args = parser.parse_args()
OUT = args.results.resolve()
rows = [
    json.loads(line)
    for line in (OUT / "raw-measurements.jsonl").read_text(encoding="utf-8").splitlines()
]
reviews = json.loads((OUT / "reviews.json").read_text())
assert len(rows) == 48 and len({(r["task_id"], r["mode"]) for r in rows}) == 48
spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts/benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
measured = []
tool_audit = []
for row in rows:
    review = reviews[row["task_id"] + "." + row["mode"]]
    assert type(review["correct"]) is bool
    events = [
        json.loads(line)
        for line in (OUT / f"{row['task_id']}.{row['mode']}.events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    turns = [e for e in events if e.get("type") == "turn.completed"]
    assert len(turns) == 1 and turns[0]["usage"] == row["usage"]
    calls = [
        e["item"]
        for e in events
        if e.get("type") == "item.completed"
        and e["item"].get("type") in {"mcp_tool_call", "command_execution", "web_search"}
    ]
    assert len(calls) == row["tool_calls"]
    assert all(c.get("server") in {"repo", "colony"} for c in calls)
    repo_calls = sum(c.get("server") == "repo" for c in calls)
    tool_audit.append(
        {
            "task_id": row["task_id"],
            "mode": row["mode"],
            "repo_calls": repo_calls,
            "memory_calls": len(calls) - repo_calls,
            "errors": [c["error"] for c in calls if c.get("error")],
        }
    )
    data = {k: row[k] for k in ["task_id", "mode", "tokens", "seconds", "tool_calls", "ask_calls"]}
    hits = []
    for ask in row["asks"]:
        assert ask.get("error") is None
        hits.append(json.loads(ask["result"]["content"][0]["text"]))
    assert len(hits) == row["ask_calls"]
    raw_returns = sum(bool(hit.get("hit")) for hit in hits)
    labels = review.get("relevant_asks", [review.get("relevant_hit", False)] * len(hits))
    assert len(labels) == len(hits) and all(type(label) is bool for label in labels)
    assert all(not label or hit.get("hit") for label, hit in zip(labels, hits, strict=True))
    relevant = any(labels)
    validated = sum(
        label and hit.get("status") == "validated" for label, hit in zip(labels, hits, strict=True)
    )
    reused = bool(relevant and validated > 0 and review["correct"] and repo_calls == 0)
    assert review.get("reused", False) == reused
    data.update(
        correct=review["correct"],
        matches=sum(labels),
        validated=validated,
        wrong_reused=int(review.get("wrong_reused", False)),
    )
    data.update(
        family=row["family"],
        input_tokens=row["usage"]["input_tokens"],
        output_tokens=row["usage"]["output_tokens"],
        cached_input_tokens=row["usage"]["cached_input_tokens"],
        uncached_tokens=row["tokens"] - row["usage"]["cached_input_tokens"],
        raw_hit=bool(raw_returns),
        raw_returns=raw_returns,
        raw_validated=sum(
            bool(hit.get("hit")) and hit.get("status") == "validated" for hit in hits
        ),
        useful_return=relevant,
        reused=reused,
        repo_calls=repo_calls,
        review_note=review["note"],
        return_kinds=[h.get("match_kind", "answer") for h in hits if h.get("hit")],
        relevant_return_kinds=[
            h.get("match_kind", "answer") for h, label in zip(hits, labels, strict=True) if label
        ],
        saved_answer_relevant=review.get("answer_relevant", relevant),
    )
    measured.append(data)
summary = benchmark.report(measured)
baseline = [r for r in measured if r["mode"] == "baseline"]
colony = [r for r in measured if r["mode"] == "colony"]


def totals(group):
    return {
        k: sum(r[k] for r in group)
        for k in [
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "uncached_tokens",
            "tokens",
            "seconds",
            "tool_calls",
        ]
    }


b, c = totals(baseline), totals(colony)
summary["token_breakdown"] = {"baseline": b, "colony": c}
summary["uncached_token_reduction"] = 1 - c["uncached_tokens"] / b["uncached_tokens"]
summary["ask_calls"] = sum(r["ask_calls"] for r in colony)
summary["raw_hits"] = sum(r["raw_returns"] for r in colony)
summary["raw_validated_hits"] = sum(r["raw_validated"] for r in colony)
summary["relevant_hits"] = sum(r["matches"] for r in colony)
summary["validated_relevant_hits"] = sum(r["validated"] for r in colony)
summary["relevant_query_hits"] = sum(r["useful_return"] for r in colony)
summary["query_reuse_rate"] = summary["relevant_query_hits"] / len(colony)
summary["reused_without_research"] = sum(r["reused"] for r in colony)
summary["retrieval_kinds"] = {
    kind: {
        "raw_return_events": sum(r["return_kinds"].count(kind) for r in colony),
        "relevant_return_events": sum(r["relevant_return_kinds"].count(kind) for r in colony),
        "research_free_queries": sum(
            r["reused"] and kind in r["relevant_return_kinds"] for r in colony
        ),
    }
    for kind in ["answer", "evidence"]
}
summary["relevant_saved_answer_queries"] = sum(r["saved_answer_relevant"] for r in colony)
summary["raw_hit_rate"] = summary["raw_hits"] / sum(r["ask_calls"] for r in colony)
summary["all_returned_validated_rate"] = (
    summary["raw_validated_hits"] / summary["raw_hits"] if summary["raw_hits"] else None
)
summary["families"] = {
    f: benchmark.report([r for r in measured if r["family"] == f])
    for f in sorted({r["family"] for r in measured})
}
summary["review"] = (
    "Primary assistant audit against frozen repo/rubrics; not independent blind human review"
)
summary["scope"] = (
    "One run per arm, 24 read-only questions about Colony itself; not a production coding workload"
)
both_correct = [
    task_id
    for task_id in {r["task_id"] for r in measured}
    if all(r["correct"] for r in measured if r["task_id"] == task_id)
]
successful = (
    benchmark.report([r for r in measured if r["task_id"] in both_correct])
    if both_correct
    else None
)
summary["both_correct_pairs_only"] = successful
# Paired bootstrap is descriptive uncertainty for this small fixed task suite.
pairs = {r["task_id"]: {} for r in baseline}
for r in measured:
    pairs[r["task_id"]][r["mode"]] = r
items = list(pairs.values())
rng = random.Random(20261019)
samples = []
for _ in range(10000):
    chosen = rng.choices(items, k=len(items))
    samples.append(
        1
        - sum(p["colony"]["tokens"] for p in chosen) / sum(p["baseline"]["tokens"] for p in chosen)
    )
samples.sort()
summary["paired_bootstrap_token_reduction_95pct"] = [samples[250], samples[9749]]
(OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(OUT / "tool-audit.json").write_text(json.dumps(tool_audit, indent=2), encoding="utf-8")
(OUT / "measurements.jsonl").write_text(
    "".join(json.dumps(r) + "\n" for r in measured), encoding="utf-8"
)
with (OUT / "measurements.csv").open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(measured[0]))
    writer.writeheader()
    writer.writerows(measured)
print(json.dumps(summary, indent=2))
