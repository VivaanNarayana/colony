"""Provider-free native-boundary controls; every model-dispatch seam is intercepted."""

import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import colony


@pytest.fixture
def prepared_native_control(evaluator, tmp_path):
    protocol = importlib.import_module("scripts.token_evaluation.protocol")
    runner = importlib.import_module("scripts.token_evaluation.runner")
    workload = copy.deepcopy(evaluator.catalog["workloads"][0])
    workload["source_root"] = str(evaluator.product.FIXTURES / workload["id"])
    runtime = Path(colony.__file__).parent
    executable = Path(sys.executable)
    manifest = {
        "contract": protocol.CONTRACT, "track": "controlled",
        "evaluator_overlay": dict(protocol.EVALUATOR_OVERLAY),
        "collector": {"files": protocol.active_collector()},
        "target": {
            "revision": "a" * 40, "python": str(executable),
            "runtime_hashes": {
                "colony/" + p.relative_to(runtime).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in runtime.rglob("*.py")
            },
        },
        # Python is a hash-bound stand-in, never invoked as a native model client.
        "client": {
            "executable": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "version": "offline-control", "model": "offline-control", "effort": "low",
        },
        "limits": {
            "max_attempts": 2 * len(workload["tasks"]), "session_seconds": 10,
            "wall_seconds": 60, "gross_token_stop": 100, "disk_bytes": 100000000,
            "min_free_bytes": 1, "retries": 0,
        },
        "research": {"max_calls": 6},
        "output": {"answer_policy": "last_agent_message", "format": "json"},
        "workloads": [workload],
    }

    def prepare(corruption=None):
        value = copy.deepcopy(manifest)
        if corruption == "missing_collector":
            value.pop("collector")
        elif corruption == "empty_files":
            value["collector"]["files"] = {}
        elif corruption == "null_collector":
            value["collector"] = None
        elif corruption == "missing_overlay":
            value.pop("evaluator_overlay")
        elif corruption == "wrong_collector":
            name = next(iter(value["collector"]["files"]))
            value["collector"]["files"][name] = "0" * 64
        source = tmp_path / "manifest.json"
        source.write_text(json.dumps(value), encoding="utf-8")
        output = tmp_path / "run"
        runner.prepare(source, output)
        run = json.loads((output / "run.json").read_text(encoding="utf-8"))
        authorization = tmp_path / "offline-authorization-control.json"
        authorization.write_text(json.dumps({
            "protocol_sha256": run["protocol_sha256"], "revision": value["target"]["revision"],
            "model": value["client"]["model"], "limits": value["limits"],
            "owner_intent": "Offline intercepted control only; no model launch authorized.",
        }), encoding="utf-8")
        return output, authorization

    return prepare


@pytest.mark.parametrize("corruption", [
    "missing_collector", "empty_files", "null_collector", "missing_overlay", "wrong_collector",
])
@pytest.mark.parametrize("endpoint", ["preflight", "execute", "direct_preflight", "direct_execute"])
def test_invalid_collector_rejected_before_any_live_boundary(
    evaluator, prepared_native_control, monkeypatch, corruption, endpoint
):
    runner = importlib.import_module("scripts.token_evaluation.runner")
    native = importlib.import_module("scripts.token_evaluation.native")
    output, authorization = prepared_native_control(corruption)
    forbidden = Mock(side_effect=AssertionError("live boundary must not be reached"))
    if endpoint.startswith("direct_"):
        monkeypatch.setattr(native.subprocess, "run", forbidden)
        monkeypatch.setattr(native.subprocess, "Popen", forbidden)
        monkeypatch.setattr(native.runtime, "provision", forbidden)
    else:
        monkeypatch.setattr(native, "preflight_live", forbidden)
        monkeypatch.setattr(native, "execute_live", forbidden)
    if endpoint == "preflight":
        result = runner.preflight(output)
        assert result["ok"] is False and result["native_ready"] is False
        assert any("collector" in error or "overlay protocol" in error for error in result["errors"])
    else:
        with pytest.raises(ValueError, match="collector|overlay protocol"):
            if endpoint == "execute":
                runner.execute(output, authorization, output / "unused-auth", execute_native=True)
            elif endpoint == "direct_preflight":
                native.preflight_live(output)
            else:
                native.execute_live(output, {}, output / "unused-auth")
    forbidden.assert_not_called()
    assert not list((output / "control").iterdir())
    assert not list((output / "logs").iterdir())
    assert not (output / "private").exists()


@pytest.mark.parametrize("endpoint", ["preflight", "execute"])
def test_exact_current_collector_reaches_only_intercepted_live_boundary(
    evaluator, prepared_native_control, monkeypatch, endpoint
):
    runner = importlib.import_module("scripts.token_evaluation.runner")
    native = importlib.import_module("scripts.token_evaluation.native")
    output, authorization = prepared_native_control()
    # Fixture/source/runtime hashes are verified for real; only live work is intercepted.
    intercepted = Mock(return_value={"ok": True, "native_ready": True, "native_sessions_started": 0})
    monkeypatch.setattr(native, "preflight_live", intercepted)
    monkeypatch.setattr(native, "execute_live", intercepted)
    if endpoint == "preflight":
        result = runner.preflight(output)
        assert result["ok"] and result["native_ready"]
    else:
        result = runner.execute(output, authorization, output / "unused-auth", execute_native=True)
    assert result["native_sessions_started"] == 0
    intercepted.assert_called_once()
