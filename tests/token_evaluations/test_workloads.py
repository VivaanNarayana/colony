"""Immutable synthetic source/oracle checks; no model quality claim."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures"


def test_catalog_hashes_citations_and_scenarios():
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    assert "synthetic" in catalog["provenance"]
    assert {w["split"] for w in catalog["workloads"]} == {"development", "validation"}
    for workload in catalog["workloads"]:
        assert "synthetic public" in workload["provenance"]
        assert "not held out or representative" in workload["provenance"]
        assert {t["scenario"] for t in workload["tasks"]} >= {
            "cold_learning", "useful_hit", "miss", "stale", "insufficient", "direct_read"}
        for snapshot in workload["snapshots"]:
            for relative, digest in snapshot["files"].items():
                path = ROOT / workload["source_root"] / snapshot["id"] / relative
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for task in workload["tasks"]:
            for citation in task["oracle"]["citations"]:
                path = ROOT / workload["source_root"] / task["snapshot"] / citation["path"]
                lines = path.read_text(encoding="utf-8").splitlines()
                assert 1 <= citation["start"] <= citation["end"] <= len(lines)


def test_effective_config_oracles_are_source_derived():
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    for snapshot, expected in [("v1", 1500), ("v2", 2200)]:
        spec = importlib.util.spec_from_file_location("settings", ROOT / "gateway" / snapshot / "settings.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.settings("production", {}) == {"timeout_ms": expected, "attempts": 3}
        assert module.settings("production", {"timeout_ms": 50})["timeout_ms"] == 50
        task = next(t for t in catalog["workloads"][0]["tasks"]
                    if t["id"] == ("stale" if snapshot == "v2" else "cold_learning"))
        assert task["oracle"]["facts"]["timeout_ms"] == expected


def test_retry_trace_and_partial_evidence(monkeypatch):
    from types import SimpleNamespace

    for snapshot, expected in [("v1", 1500), ("v2", 2200)]:
        modules = {}
        for name in ("settings", "client", "handler"):
            spec = importlib.util.spec_from_file_location(name, ROOT / "gateway" / snapshot / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(__import__("sys").modules, name, module)
            spec.loader.exec_module(module)
            modules[name] = module
        calls = []
        def get(path, timeout_ms, calls=calls):
            calls.append((path, timeout_ms))
            return SimpleNamespace(status=503)
        request = SimpleNamespace(environment="production", overrides={})
        assert modules["handler"].handle(request, SimpleNamespace(get=get)).status == 503
        assert calls == [("/inventory", expected)] * 3
        def raises(path, timeout_ms):
            raise ConnectionError("Synthetic transport failure; no exception retry contract")
        import pytest
        with pytest.raises(ConnectionError):
            modules["handler"].handle(request, SimpleNamespace(get=raises))



def test_validation_worker_retry_policy_oracles(monkeypatch):
    from types import SimpleNamespace

    import pytest

    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    workload = next(w for w in catalog["workloads"] if w["id"] == "worker")
    for snapshot, submissions, delay in [("v1", 4, 250), ("v2", 5, 400)]:
        modules = {}
        for name in ("policy", "executor", "dispatcher"):
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "worker" / snapshot / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(__import__("sys").modules, name, module)
            spec.loader.exec_module(module)
            modules[name] = module
        calls, waits = [], []
        def submit(job_id, calls=calls):
            calls.append(job_id)
            return SimpleNamespace(retryable=True)
        job = SimpleNamespace(id="invoice-7", queue="priority", overrides={})
        result = modules["dispatcher"].dispatch(job, SimpleNamespace(submit=submit), waits.append)
        assert result.retryable
        assert calls == ["invoice-7"] * submissions
        assert waits == [delay] * (submissions - 1)
        task = next(t for t in workload["tasks"]
                    if t["id"] == ("stale" if snapshot == "v2" else "cold_learning"))
        assert task["oracle"]["facts"] == {
            "max_submissions": submissions, "wait_count": len(waits), "delay_ms": delay}
        assert modules["policy"].policy("priority", {"attempts": 1})["attempts"] == 1
        direct = next(t for t in workload["tasks"] if t["id"] == "direct_read")
        assert modules["policy"].policy("unknown", {})["delay_ms"] == direct["oracle"]["facts"]["default_delay_ms"]
        calls.clear()
        waits.clear()
        job.overrides = {"attempts": 1}
        modules["dispatcher"].dispatch(job, SimpleNamespace(submit=submit), waits.append)
        assert len(calls) == 1 and waits == []
        def fail(job_id):
            raise ConnectionError("Synthetic injected backend failure")
        with pytest.raises(ConnectionError):
            modules["dispatcher"].dispatch(job, SimpleNamespace(submit=fail), waits.append)
        insufficient = next(t for t in workload["tasks"] if t["id"] == "insufficient")
        assert insufficient["oracle"]["facts"]["wrapper_exception_retries"] == 0
        assert insufficient["oracle"]["facts"]["internal_backend_attempts"] == "unknown"
