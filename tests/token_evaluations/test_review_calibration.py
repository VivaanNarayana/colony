"""Executable review counterexamples, not a semantic grader or token evaluation.

These controls demonstrate why extra dependency/termination claims need evidence
in either arm. The grader still reviews the actual answer and observed sources.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.token_evaluation.research import Research

FIXTURES = Path(__file__).parent / "fixtures"


def load_modules(monkeypatch, workload, snapshot):
    names = (("settings", "client", "handler", "transport") if workload == "gateway"
             else ("policy", "executor", "dispatcher", "backend"))
    modules = {}
    for name in names:
        spec = importlib.util.spec_from_file_location(
            name, FIXTURES / workload / snapshot / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def invoke(modules, workload, dependency, waits):
    if workload == "gateway":
        request = SimpleNamespace(environment="production", overrides={})
        return modules["handler"].handle(request, dependency)
    job = SimpleNamespace(id="calibration", queue="priority", overrides={})
    return modules["dispatcher"].dispatch(job, dependency, waits.append)


def response(workload):
    return SimpleNamespace(status=503) if workload == "gateway" else SimpleNamespace(retryable=True)


def outer_count(workload, snapshot):
    return 3 if workload == "gateway" else (4 if snapshot == "v1" else 5)


@pytest.mark.parametrize("workload", ["gateway", "worker"])
@pytest.mark.parametrize("snapshot", ["v1", "v2"])
def test_injected_dependency_does_not_inherit_concrete_wire_ceiling(monkeypatch, workload, snapshot):
    modules = load_modules(monkeypatch, workload, snapshot)
    sends, waits = [], []

    def different_implementation(*args, **kwargs):
        sends.extend(range(7))
        return response(workload)

    dependency = (SimpleNamespace(get=different_implementation) if workload == "gateway"
                  else SimpleNamespace(submit=different_implementation))
    invoke(modules, workload, dependency, waits)
    outer = outer_count(workload, snapshot)
    assert len(sends) == 7 * outer
    concrete_limit = (modules["transport"].CONNECT_ATTEMPTS if workload == "gateway"
                      else modules["backend"].WIRE_ATTEMPTS)
    assert len(sends) > outer * concrete_limit
    if workload == "worker":
        assert waits == [250 if snapshot == "v1" else 400] * (outer - 1)


@pytest.mark.parametrize("workload", ["gateway", "worker"])
@pytest.mark.parametrize("snapshot", ["v1", "v2"])
@pytest.mark.parametrize("wire_limit", [1, 4])
@pytest.mark.parametrize("failure", ["always_raises", "last_outer_call_raises"])
def test_qualified_wire_counts_follow_actual_exception_control_flow(
    monkeypatch, workload, snapshot, wire_limit, failure,
):
    modules = load_modules(monkeypatch, workload, snapshot)
    module = modules["transport" if workload == "gateway" else "backend"]
    monkeypatch.setattr(module, "CONNECT_ATTEMPTS" if workload == "gateway" else "WIRE_ATTEMPTS", wire_limit)
    outer = outer_count(workload, snapshot)
    sends, waits = [], []

    def send(*args, **kwargs):
        sends.append(args)
        # Earlier outer calls return a retryable result on their last inner send.
        # On the last outer call, even that final send can raise.
        if (failure == "last_outer_call_raises" and len(sends) < outer * wire_limit
                and len(sends) % wire_limit == 0):
            return response(workload)
        raise ConnectionError("source-derived calibration")

    dependency = (module.RetryingTransport(send) if workload == "gateway"
                  else module.RetryingBackend(send))
    with pytest.raises(ConnectionError, match="source-derived calibration"):
        invoke(modules, workload, dependency, waits)
    assert len(sends) == (wire_limit if failure == "always_raises" else outer * wire_limit)
    if workload == "worker":
        expected_waits = 0 if failure == "always_raises" else outer - 1
        assert waits == [250 if snapshot == "v1" else 400] * expected_waits


def test_empty_literal_or_search_is_not_proof_individual_terms_are_absent(tmp_path):
    # Positive counterexample: an empty result for this compound literal can
    # coexist with the requested measurement in the allowed source itself.
    (tmp_path / "measurements.txt").write_text(
        "Sample X measured 12 percent mineral A.\n", encoding="utf-8")
    research = Research(tmp_path, max_calls=3, files=["measurements.txt"])
    assert research.search("Sample X|measurement") == []
    assert research.search("Sample X")
    assert "12 percent" in research.read("measurements.txt")[0].text
    assert research.calls == 3
