"""Corrupt retained raw evidence rather than mocking evaluator decisions."""

import copy
import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    source = root / "docs/results/benchmark-remediation/attempt-22"
    for name in ["lookup-plan.json", "lookup-workload.json", "lookup-measurements.jsonl"]:
        shutil.copyfile(source / name, tmp_path / name)
    summary = json.loads((source / "lookup-summary.json").read_text())
    return summary, tmp_path


def test_genuine_raw_evidence_recomputes_exactly(retained):
    from audit_lookup_local import audit_local

    summary, directory = retained
    assert audit_local(summary, directory)["observations"] == 234


@pytest.mark.parametrize(
    "mutation",
    ["fabricated-summary", "interval", "missing", "duplicate", "family", "packet", "workload"],
)
def test_raw_evidence_disagrees_with_corrupt_summary_or_rows(retained, mutation):
    from audit_lookup_local import audit_local

    summary, directory = retained
    summary = copy.deepcopy(summary)
    path = directory / "lookup-measurements.jsonl"
    rows = [json.loads(s) for s in path.read_text().splitlines()]
    if mutation == "fabricated-summary":
        summary["modes"] = {
            arm: {"lookups": 1, "oracle_passes": 1, "seconds": seconds}
            for arm, seconds in [("main", 1000), ("feature", 1)]
        }
        summary["latency_reduction_95pct"] = [0.99, 0.999]
    elif mutation == "interval":
        summary["latency_reduction_95pct"] = [0.99, 0.999]
    elif mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "family":
        rows[0]["family"] = "different"
    elif mutation == "packet":
        row = next(r for r in rows if r["result"].get("status") == "validated")
        row["result"]["evidence"][0]["text"] = "Forged source fact."
    else:
        (directory / "lookup-workload.json").write_text("{}")
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(ValueError, match="Local raw evidence"):
        audit_local(summary, directory)


def test_summary_alone_cannot_satisfy_local_gate(retained):
    from audit_lookup_local import audit_local

    summary, _ = retained
    with pytest.raises(ValueError, match="directory is required"):
        audit_local(summary, None)
