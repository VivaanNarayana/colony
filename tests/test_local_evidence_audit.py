"""Corrupt retained raw evidence rather than mocking evaluator decisions."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    source = root / "tests/fixtures/benchmark-audit/trial-22"
    for name in ["lookup-plan.json", "lookup-workload.json", "lookup-measurements.jsonl"]:
        shutil.copyfile(source / name, tmp_path / name)
    summary = json.loads((source / "lookup-summary.json").read_text())
    return summary, tmp_path


def test_genuine_raw_evidence_recomputes_exactly(retained):
    from audit_lookup_local import audit_local

    summary, directory = retained
    assert audit_local(summary, directory)["observations"] == 234


@pytest.mark.parametrize("name", ["experiment", "trial[1]"])
def test_named_raw_evidence_is_audited_without_using_default_sidecars(retained, name):
    from audit_lookup_local import audit_local

    summary, directory = retained
    for suffix in ["plan.json", "workload.json", "measurements.jsonl"]:
        original = directory / f"lookup-{suffix}"
        original.rename(directory / f"{name}-{suffix}")
        original.write_text("Corrupt decoy from a different run.", encoding="utf-8")
    assert audit_local(summary, directory, name=name)["observations"] == 234


@pytest.mark.parametrize("name", ["lookup", "experiment", "trial[1]", "experiment-summary"])
def test_analyzer_command_uses_selected_summary_prefix(retained, monkeypatch, name):
    import analyze_lookup_live as analyzer
    from audit_lookup_local import audit_local

    summary, directory = retained
    if name != "lookup":
        for suffix in ["plan.json", "workload.json", "measurements.jsonl"]:
            (directory / f"lookup-{suffix}").rename(directory / f"{name}-{suffix}")
    summary_path = directory / f"{name}-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    audited = []

    def compare(results, local, *, local_directory, local_name):
        assert results == directory and local_directory == directory
        audited.append(audit_local(local, local_directory, name=local_name))
        return {"all_gates_passed": False, "blocking_gates": ["local_lookup_improves"]}

    monkeypatch.setattr(analyzer, "compare", compare)
    monkeypatch.setattr(
        sys, "argv", ["analyze", "--results", str(directory), "--local", str(summary_path)]
    )
    with pytest.raises(SystemExit) as error:
        analyzer.main()
    assert error.value.code == 1
    assert len(audited) == 1 and audited[0]["observations"] == 234


@pytest.mark.parametrize("filename", ["local.json", "-summary.json"])
def test_schema_v2_summary_requires_an_unambiguous_run_prefix(retained, monkeypatch, filename):
    import analyze_lookup_live as analyzer

    summary, directory = retained
    summary_path = directory / filename
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["analyze", "--results", str(directory), "--local", str(summary_path)]
    )
    with pytest.raises(SystemExit) as error:
        analyzer.main()
    assert error.value.code == 1
    report = analyzer.read_json(directory / "comparison.json")
    assert report["all_gates_passed"] is False
    assert report["blocking_gates"] == ["evidence_integrity"]
    assert "Local summary" in report["integrity_error"]


@pytest.mark.parametrize("name", ["", "../escape", "nested/run"])
def test_audit_requires_a_plain_artifact_name(retained, name):
    from audit_lookup_local import audit_local

    summary, directory = retained
    with pytest.raises(ValueError, match="plain run name"):
        audit_local(summary, directory, name=name)


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
