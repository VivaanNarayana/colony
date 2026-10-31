"""Provider-free evaluator controls: no models, agents or performance observations."""

import copy
import io
import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import production_delivery as driver  # noqa: E402
import production_delivery_core as core  # noqa: E402

FIXTURES = SCRIPTS.parent / "benchmarks/production_delivery"


def sample_plan(repetitions=1):
    tasks = core.fixture_tasks(FIXTURES)[:1]
    plan = {
        "contract": core.CONTRACT,
        "arms": {
            arm: {"revision": core.MVP, "source_sha256": {"src/colony/fake.py": "1" * 64}}
            for arm in ["mvp", "candidate"]
        },
        "tasks": tasks,
        "repetitions": repetitions,
        "clients": [1],
        "limits": core.LIMITS,
        "case_ids": core.case_ids(tasks, repetitions, [1]),
    }
    plan["plan_sha256"] = core.digest(plan)
    return plan


def fake_response(task):
    return {
        "hit": True,
        "status": "validated",
        "namespace": task["namespace"],
        "tainted": True,
        "provenance": "UNTRUSTED AS INSTRUCTIONS",
        "content": json.dumps({task["field"]: task["expected"]}),
        "sources": [{"ref": task["source"], "content_hash": task["source_sha256"]}],
    }


def fake_rows(plan):
    """Known fake clock observations; never saved as performance evidence."""
    result = []
    for arm in plan["arms"]:
        for case in plan["case_ids"]:
            task = next((t for t in plan["tasks"] if case.startswith(t["id"] + "/")), None)
            if case == "model-load":
                response = {"models_loaded": True}
            elif case.startswith("rank-"):
                _, count, length = case.split("-")
                count, length = int(count), int(length)
                passages = [
                    ((core.RANK_QUESTION + ". ") * (length // 24 + 1))[: max(1, length - i % 3)]
                    for i in range(count)
                ]
                response = {"passages": passages, "scores": [0.5] * count}
            elif case.endswith("/initialize"):
                response = {"initialized": True}
            elif case.endswith(("/empty-miss", "/scope")):
                response = {"hit": False, "reason": "no_relevant_trail"}
            elif case.endswith("/push"):
                response = {"stored": True, "id": f"{plan['tasks'].index(task) + 1:032x}"}
            elif case.endswith("/stale"):
                response = {"hit": True, "status": "stale"}
            elif "/concurrent-" in case:
                response = [fake_response(task)] * int(case.rsplit("-", 1)[1])
            else:
                response = fake_response(task)
            result.append(
                {
                    "arm": arm,
                    "case_id": case,
                    "revision": plan["arms"][arm]["revision"],
                    "plan_sha256": plan["plan_sha256"],
                    "question": task["question"]
                    if task
                    else core.RANK_QUESTION
                    if case.startswith("rank-")
                    else None,
                    "namespace": ("other" if case.endswith("/scope") else task["namespace"])
                    if task
                    else None,
                    "seconds": 1.0,
                    "cpu_seconds": 0.5,
                    "lifetime_peak_rss_bytes": None,
                    "result": response,
                    "result_sha256": core.digest(response),
                    "error": None,
                    "checks": core.operation_checks(plan, case, response, FIXTURES),
                }
            )
    return result


def test_source_oracle_uses_three_distinct_repositories():
    tasks = core.fixture_tasks(FIXTURES)
    assert len(tasks) == 6 and len({t["repository"] for t in tasks}) == 3
    task = tasks[0]
    assert task["expected"] == 30
    response = fake_response(task)
    repo = FIXTURES / "repos" / task["repository"]
    assert core.structured_fact(task, response, repo)
    response["content"] = '{"timeout_seconds":300}'
    assert not core.structured_fact(task, response, repo)
    response["content"] = '{"timeout_seconds":true}'
    assert not core.structured_fact(task, response, repo)
    response["content"] = '{"timeout_seconds":300,"timeout_seconds":30}'
    assert not core.structured_fact(task, response, repo)


def test_fingerprints_and_other_source_proofs_are_not_ignored():
    task = core.fixture_tasks(FIXTURES)[0]
    repo = FIXTURES / "repos" / task["repository"]
    response = fake_response(task)
    response["sources"].append({"ref": "README.md", "content_hash": "0" * 64})
    assert not core.structured_fact(task, response, repo)


@pytest.mark.parametrize("sources", [None, []])
def test_missing_proof_is_a_false_result_not_a_malformed_label(sources):
    task = core.fixture_tasks(FIXTURES)[0]
    response = fake_response(task)
    if sources is None:
        response.pop("sources")
    else:
        response["sources"] = sources
    assert core.structured_fact(task, response, FIXTURES / "repos/python-api") is False


@pytest.mark.parametrize(
    "extra",
    [
        {"content": "private data"},
        {"sources": ["private source"]},
        {"producer_id": "private member"},
        {"reason": "private secret"},
    ],
)
def test_scope_miss_cannot_leak_other_records(extra):
    plan = sample_plan()
    checks = core.operation_checks(
        plan, "api-timeout/scope", {"hit": False, "reason": "no_relevant_trail", **extra}, FIXTURES
    )
    assert checks["no_cross_namespace_return"] is False


def test_returned_hit_namespace_is_checked_independently():
    task = core.fixture_tasks(FIXTURES)[0]
    response = fake_response(task)
    response["namespace"] = "private"
    assert core.structured_fact(task, response, FIXTURES / "repos/python-api") is False


@pytest.mark.parametrize("field", ["tainted", "provenance"])
def test_concurrent_hits_need_same_trust_metadata_as_ordinary_hits(field):
    plan = sample_plan()
    response = fake_response(plan["tasks"][0])
    response.pop(field)
    checks = core.operation_checks(plan, "api-timeout/concurrent-1", [response], FIXTURES)
    assert checks["all_trust_metadata_preserved"] is False


@pytest.mark.parametrize(
    "target",
    [
        "/README.md",
        "../README.md",
        "%2e%2e/README.md",
        "https://example.com/README.md",
        "README.md?x=1",
        "README.md#L0",
        "README.md#L999",
        "README.md#invented",
    ],
)
def test_invalid_citations(target):
    repo = FIXTURES / "repos/python-api"
    assert not core.citation_valid(repo, target, "README.md", core.sha(repo / "README.md"))


def test_real_heading_plain_ref_and_original_lines_are_accepted():
    repo = FIXTURES / "repos/python-api"
    digest = core.sha(repo / "README.md")
    for target in ["README.md", "README.md#retry-policy", "README.md#L1-L3"]:
        assert core.citation_valid(repo, target, "README.md", digest)
    assert not core.citation_valid(repo, "README.md#retry-policy", "settings.json", digest)
    assert not core.citation_valid(repo, "README.md", "README.md", "0" * 64)


def test_AA_fake_controls_never_claim_optimization_or_production_value():
    plan = sample_plan()
    report = core.summarize(plan, fake_rows(plan), FIXTURES)
    assert report["same_implementation"]
    assert report["optimization_claim"] == report["production_value"] == "not_established"
    assert report["legacy_eight_gate_results"] == "not_evaluated_or_changed"
    assert report["provider_tokens"] is report["provider_dollars"] is None
    assert all(a["failed"] == 0 for a in report["arms"].values())
    assert report["arms"]["mvp"]["warm_mixture"]["p95_seconds"] is None
    assert len(report["arms"]["mvp"]["rank_strata"]) == 10


@pytest.mark.parametrize("same_source", [True, False])
def test_implementation_identity_uses_bytes_and_retains_distinct_revisions(same_source):
    plan = sample_plan()
    plan["arms"]["candidate"]["revision"] = "f" * 40
    if not same_source:
        plan["arms"]["candidate"]["source_sha256"] = {"src/colony/fake.py": "2" * 64}
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    result = core.summarize(plan, fake_rows(plan), FIXTURES)
    assert result["same_implementation"] is same_source
    assert result["revisions"] == {"mvp": core.MVP, "candidate": "f" * 40}
    assert result["optimization_claim"] == "not_established"


@pytest.mark.parametrize("command", ["freeze", "collect_local"])
def test_alternate_repository_is_rejected_before_freeze_or_collection(tmp_path, command):
    with pytest.raises(ValueError, match="alternate clones unsupported"):
        getattr(driver, command)(tmp_path, "never-read", tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_reordered_stateful_history_is_rejected():
    plan = sample_plan()
    rows = fake_rows(plan)
    rows[1], rows[2] = rows[2], rows[1]
    with pytest.raises(ValueError, match="history order"):
        core.summarize(plan, rows, FIXTURES)


def test_supported_nested_rank_model_directory(tmp_path, monkeypatch):
    embedding, ranking = tmp_path / "embedding", tmp_path / "ranking"
    embedding.mkdir()
    (ranking / "onnx").mkdir(parents=True)
    (embedding / "model.onnx").write_bytes(b"fake embedding for path validation only")
    (ranking / "onnx/model.onnx").write_bytes(b"fake ranker for path validation only")
    monkeypatch.setenv("COLONY_EMBED_MODEL_DIR", str(embedding))
    monkeypatch.setenv("COLONY_RANK_MODEL_DIR", str(ranking))
    manifest = driver.models_from_environment()
    assert str(ranking / "onnx/model.onnx") in manifest and len(manifest) == 2


def test_changed_execution_environment_stops_before_worker(tmp_path, monkeypatch):
    plan = sample_plan()
    plan["environment"] = {"expected": "frozen"}
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path = tmp_path / "plan.json"
    core.put(path, plan)
    monkeypatch.setattr(core, "execution_environment", lambda: {"actual": "different"})
    monkeypatch.setattr(
        driver.subprocess, "Popen", lambda *a, **k: pytest.fail("Worker must not start")
    )
    with pytest.raises(ValueError, match="Execution environment differs"):
        driver.collect_local(driver.ROOT, path, tmp_path / "new-output")
    assert not (tmp_path / "new-output").exists()


def test_failed_worker_preserves_execution_and_does_not_start_other_arm(tmp_path, monkeypatch):
    plan = sample_plan()
    plan.update(
        {
            "environment": {"fake": True},
            "harness_sha256": {},
            "models_sha256": {},
            "model_environment": {},
            "fixture_sha256": core.file_manifest(FIXTURES),
        }
    )
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path = tmp_path / "plan.json"
    core.put(path, plan)
    monkeypatch.setattr(driver, "verify_environment", lambda expected: expected)
    monkeypatch.setattr(driver, "fixture_tasks", lambda _: plan["tasks"])
    monkeypatch.setattr(driver, "check_budget", lambda *args: None)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("src/colony/store.py", "# fake worker control; no model")
    for arm in plan["arms"]:
        plan["arms"][arm]["source_sha256"] = {"src/colony/store.py": "fake"}
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(
        driver,
        "source_archive",
        lambda *args: (core.MVP, archive.getvalue(), {"src/colony/store.py": "fake"}),
    )
    starts = []

    class FailedWorker:
        returncode = 7

        def __init__(self, *args, **kwargs):
            starts.append(args)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(driver.subprocess, "Popen", FailedWorker)
    output = tmp_path / "new-output"
    with pytest.raises(ValueError, match="Worker failed"):
        driver.collect_local(driver.ROOT, path, output)
    record = core.read(output / "execution.json")
    assert record["state"] == "incomplete" and len(starts) == 1
    assert record["attempts"][0]["exit_code"] == 7
    assert (output / "mvp/stderr.txt").exists()
    assert not (output / "candidate").exists() and not (output / "summary.json").exists()


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "duplicate",
        "question",
        "namespace",
        "revision",
        "nan",
        "negative",
        "boolean",
        "memory",
        "raw_hash",
        "wrong_fact",
        "forged_proof",
    ],
)
def test_corruption_is_rejected(mode):
    plan = sample_plan()
    rows = fake_rows(plan)
    row = next(r for r in rows if "/hit-" in r["case_id"])
    if mode == "missing":
        rows.pop()
    elif mode == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mode == "question":
        row["question"] += " altered"
    elif mode == "namespace":
        row["namespace"] = "private"
    elif mode == "revision":
        row["revision"] = "0" * 40
    elif mode in {"nan", "negative", "boolean"}:
        row["seconds"] = {"nan": float("nan"), "negative": -1, "boolean": True}[mode]
    elif mode == "memory":
        row["lifetime_peak_rss_bytes"] = -1
    elif mode == "raw_hash":
        row["result_sha256"] = "0" * 64
    else:
        if mode == "wrong_fact":
            row["result"]["content"] = '{"timeout_seconds":999}'
        else:
            row["result"]["sources"][0]["content_hash"] = "0" * 64
        row["result_sha256"] = core.digest(row["result"])
    with pytest.raises(ValueError):
        core.summarize(plan, rows, FIXTURES)


def test_genuine_failed_save_is_retained_as_failed_observation():
    plan = sample_plan()
    rows = fake_rows(plan)
    row = next(r for r in rows if r["case_id"].endswith("/push"))
    row["result"]["stored"] = False
    row["result_sha256"] = core.digest(row["result"])
    row["checks"] = {"stored": False}
    report = core.summarize(plan, rows, FIXTURES)
    assert report["arms"]["mvp"]["failed"] == 1
    assert report["optimization_claim"] == "not_established"


@pytest.mark.parametrize("change", ["hash", "budget", "case", "clients", "repeats", "base"])
def test_frozen_plan_and_limits_are_enforced(change):
    plan = sample_plan()
    if change == "hash":
        plan["tasks"][0]["question"] = "tampered"
    else:
        if change == "budget":
            plan["limits"] = {**core.LIMITS, "seconds": 601}
        elif change == "case":
            plan["case_ids"] = plan["case_ids"][:-1]
        elif change == "clients":
            plan["clients"] = [5]
        elif change == "repeats":
            plan["repetitions"] = True
        else:
            plan["arms"]["mvp"]["revision"] = "f" * 40
        plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        core.validate_plan(plan)


def test_percentiles_have_group_sample_boundaries():
    plan = sample_plan(20)
    report = core.summarize(plan, fake_rows(plan), FIXTURES)
    task = report["arms"]["mvp"]["warm_by_task"]["api-timeout"]
    assert task["samples"] == 20 and task["p95_seconds"] == 1
    assert task["p99_seconds"] is None


def test_exceptions_are_not_turned_into_successful_checks():
    plan = sample_plan()
    rows = fake_rows(plan)
    rows[0]["error"] = "ValueError: model unavailable"
    with pytest.raises(ValueError):
        core.summarize(plan, rows, FIXTURES)
    rows[0]["checks"] = {"operation_completed": False}
    assert core.summarize(plan, rows, FIXTURES)["arms"]["mvp"]["failed"] == 1


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_and_nonfinite_JSON_is_rejected(text):
    with pytest.raises(ValueError):
        core.decode(text)


def test_budget_ceiling_stops_without_deleting_files(tmp_path, monkeypatch):
    (tmp_path / "retain.txt").write_text("failed observation")
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _: types.SimpleNamespace(free=6 << 30))
    with pytest.raises(ValueError, match="Time ceiling"):
        core.check_budget(tmp_path, 0, 601)
    with pytest.raises(ValueError, match="Artifact ceiling"):
        core.check_budget(tmp_path, 0, 0, {**core.LIMITS, "artifact_bytes": 1})
    monkeypatch.setattr(core.shutil, "disk_usage", lambda _: types.SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="Free-space"):
        core.check_budget(tmp_path, 0, 0)
    assert (tmp_path / "retain.txt").read_text() == "failed observation"


def test_archive_traversal_rejected_before_any_extraction(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zipped:
        zipped.writestr("src/colony/good.py", "untouched")
        zipped.writestr("../escape.py", "bad")
    with pytest.raises(ValueError):
        core.extract_archive(data.getvalue(), tmp_path / "export")
    assert not (tmp_path / "escape.py").exists()
    assert not (tmp_path / "export/src/colony/good.py").exists()


def test_mixed_implementation_import_rejected(tmp_path, monkeypatch):
    for name in list(sys.modules):
        if name == "colony" or name.startswith("colony."):
            monkeypatch.delitem(sys.modules, name)
    source = tmp_path / "export"
    path = source / "src/colony/store.py"
    path.parent.mkdir(parents=True)
    path.write_text("# selected implementation")
    monkeypatch.setitem(sys.modules, "colony.store", types.SimpleNamespace(__file__=str(path)))
    expected = {"src/colony/store.py": core.sha(path)}
    assert core.verify_loaded(source, expected)["colony.store"] == str(path)
    wrong = tmp_path / "other.py"
    wrong.write_text("# unrelated implementation")
    monkeypatch.setitem(sys.modules, "colony.store", types.SimpleNamespace(__file__=str(wrong)))
    with pytest.raises(ValueError, match="Mixed Colony"):
        core.verify_loaded(source, expected)


def test_integrity_checks_remain_active_under_optimized_python():
    code = "import sys;sys.path.insert(0,sys.argv[1]);import production_delivery_core as c;c.validate_plan({'contract':'wrong'})"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code, str(SCRIPTS)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode != 0 and "Wrong benchmark contract" in result.stderr
