"""Fake complete execution records exercise report/source boundaries, not performance."""

import copy
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import production_delivery as driver  # noqa: E402
import production_delivery_core as core  # noqa: E402
from test_contract import fake_rows, sample_plan  # noqa: E402


@pytest.fixture
def complete_fake_execution(tmp_path):
    plan = sample_plan()
    plan["tasks"] = core.fixture_tasks(driver.FIXTURES)
    plan["case_ids"] = core.case_ids(plan["tasks"], 1, [1])
    revision, archive, manifest = core.source_archive(SCRIPTS.parent, core.MVP)
    plan.update(
        {
            "environment": core.execution_environment(),
            "harness_sha256": {n: core.sha(SCRIPTS / n) for n in driver.SCRIPTS},
            "fixture_sha256": core.file_manifest(driver.FIXTURES),
        }
    )
    for arm in plan["arms"].values():
        arm["source_sha256"] = manifest
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path = tmp_path / "plan.json"
    core.put(path, plan)
    output = tmp_path / "FAKE-replay-inputs"
    output.mkdir()
    rows = fake_rows(plan)
    for arm in plan["arms"]:
        public = output / arm
        public.mkdir()
        private = output / "PRIVATE-runtime" / arm
        private.mkdir(parents=True)
        core.extract_archive(archive, private / "implementation")
        source = private / "implementation/src/colony/store.py"
        core.put(
            public / "loaded-implementation.json",
            {
                "revision": revision,
                "source_sha256": manifest,
                "module_paths": {"colony.store": str(source)},
                "plan_sha256": plan["plan_sha256"],
                "execution_environment": plan["environment"],
                "observations": len(plan["case_ids"]),
            },
        )
        (public / "observations.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows if r["arm"] == arm) + "\n"
        )
    core.put(
        output / "execution.json",
        {
            "state": "completed",
            "plan_sha256": plan["plan_sha256"],
            "execution_environment": plan["environment"],
            "attempts": [{"arm": a, "exit_code": 0} for a in plan["arms"]],
            "whole_process_seconds": 2.0,
        },
    )
    return path, output, plan


def rewrite(path, value):
    """Corrupt disposable FAKE inputs, never historical observations."""
    path.write_text(json.dumps(value), encoding="utf-8")


def test_full_replay_binds_source_proofs_raws_and_report_without_claims(
    complete_fake_execution, tmp_path
):
    path, output, plan = complete_fake_execution
    destination = tmp_path / "new-report.json"
    result = driver.report(path, output, destination)
    assert result["plan_sha256"] == plan["plan_sha256"]
    assert result["observations_sha256"]["mvp"] == core.sha(output / "mvp/observations.jsonl")
    assert result["production_value"] == result["optimization_claim"] == "not_established"
    assert all(a["failed"] == 0 for a in result["arms"].values())
    assert core.read(destination) == result
    with pytest.raises(FileExistsError):
        driver.report(path, output, destination)


@pytest.mark.parametrize(
    "corruption",
    [
        "source",
        "module_path",
        "proof_revision",
        "worker_env",
        "collector_env",
        "attempt",
        "incomplete",
        "order",
        "missing",
        "fact",
        "tasks",
        "fixture",
    ],
)
def test_corrupt_replay_never_reuses_or_overwrites_old_summary(
    complete_fake_execution, tmp_path, corruption
):
    path, output, plan = complete_fake_execution
    old = tmp_path / "old-report.json"
    driver.report(path, output, old)
    old_hash = core.sha(old)
    if corruption == "source":
        source = output / "PRIVATE-runtime/mvp/implementation/src/colony/store.py"
        source.write_text("# forged implementation")
    elif corruption in {"module_path", "proof_revision", "worker_env"}:
        p = output / "mvp/loaded-implementation.json"
        proof = core.read(p)
        if corruption == "module_path":
            proof["module_paths"]["colony.store"] = str(tmp_path / "unrelated.py")
        elif corruption == "proof_revision":
            proof["revision"] = "0" * 40
        else:
            proof["execution_environment"] = {"changed": True}
        rewrite(p, proof)
    elif corruption in {"collector_env", "attempt", "incomplete"}:
        p = output / "execution.json"
        record = core.read(p)
        if corruption == "collector_env":
            record["execution_environment"] = {"changed": True}
        elif corruption == "attempt":
            record["attempts"][0]["exit_code"] = 9
        else:
            record["state"] = "incomplete"
        rewrite(p, record)
    elif corruption in {"order", "missing", "fact"}:
        p = output / "mvp/observations.jsonl"
        rows = [core.decode(line) for line in p.read_text().splitlines()]
        if corruption == "order":
            rows[0], rows[1] = rows[1], rows[0]
        elif corruption == "missing":
            rows.pop()
        else:
            row = next(r for r in rows if "/hit-" in r["case_id"])
            row["result"]["content"] = '{"timeout_seconds":999}'
            row["result_sha256"] = core.digest(row["result"])
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    else:
        altered = copy.deepcopy(plan)
        if corruption == "tasks":
            altered["tasks"][0]["question"] = "Different question"
        else:
            altered["fixture_sha256"]["workload.json"] = "0" * 64
        altered["plan_sha256"] = core.digest(
            {k: v for k, v in altered.items() if k != "plan_sha256"}
        )
        rewrite(path, altered)
    with pytest.raises(ValueError):
        driver.report(path, output, tmp_path / "rejected-new-report.json")
    assert core.sha(old) == old_hash
    assert not (tmp_path / "rejected-new-report.json").exists()
