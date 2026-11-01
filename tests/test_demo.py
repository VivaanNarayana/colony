import json
import os
import subprocess

import pytest

from colony import demo


@pytest.mark.parametrize(
    "receipt",
    [
        {},
        {"hit": False},
        {"hit": False, "reason": "server_unavailable"},
        {"hit": True, "id": "different", "status": "stale"},
        {"hit": True, "id": "original", "status": "validated"},
    ],
)
def test_demo_needs_actual_stale_receipt_not_miss_or_outage(receipt):
    assert not demo._stale_receipt(receipt, "original")


def test_demo_accepts_only_expected_retired_evidence_receipt():
    assert demo._stale_receipt({"hit": True, "id": "original", "status": "stale"}, "original")


def test_shutdown_remains_bounded_when_kill_or_wait_fails():
    class Process:
        waits = 0

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout):
            self.waits += 1
            raise subprocess.TimeoutExpired("fixture", timeout)

        def kill(self):
            raise OSError("private-error-sentinel")

    process = Process()
    assert demo._stop(process) is False
    assert process.waits == 1


def test_shutdown_reports_second_timeout_instead_of_escaping():
    class Process:
        waits = 0

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout):
            self.waits += 1
            raise subprocess.TimeoutExpired("fixture", timeout)

        def kill(self):
            pass

    process = Process()
    assert demo._stop(process) is False and process.waits == 2


def owned_tree(tmp_path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    owner = {"format": "colony-demo-v1", "id": "owned", "trail_id": "a" * 32}
    (workspace / demo._MARKER).write_text(json.dumps(owner))
    data = workspace / "team/data"
    data.mkdir(parents=True)
    (data / "storage.key").write_bytes(b"synthetic-private-key")
    (data / "colony.db").write_bytes(b"synthetic-database")
    return workspace, owner


def test_cleanup_includes_owned_store_key(tmp_path):
    workspace, owner = owned_tree(tmp_path)
    demo._cleanup(workspace, owner)
    assert not workspace.exists()


def test_cleanup_refuses_unexpected_files_before_deleting_anything(tmp_path):
    workspace, owner = owned_tree(tmp_path)
    extra = workspace / "personal.txt"
    extra.write_text("keep")
    with pytest.raises(ValueError, match="Unexpected"):
        demo._cleanup(workspace, owner)
    assert extra.read_text() == "keep" and (workspace / "team/data/storage.key").exists()


def test_cleanup_link_refusal_preserves_external_directory(tmp_path):
    workspace, owner = owned_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary"
    canary.write_text("keep")
    link = workspace / "repo"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        demo._cleanup(workspace, owner)
    assert canary.read_text() == "keep" and (workspace / demo._MARKER).exists()


def test_missing_models_fail_before_creating_demo_workspace(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise ValueError("Verified local models required")

    monkeypatch.setattr(demo, "model_environment", missing)
    with pytest.raises(ValueError):
        demo.run_demo(tmp_path / "absent")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("timeout", [0, 9, 301, float("inf"), float("nan"), True])
def test_invalid_timeout_has_no_effects(tmp_path, timeout):
    with pytest.raises(ValueError):
        demo.run_demo(tmp_path / "absent", timeout=timeout)
    assert not list(tmp_path.iterdir())


def test_failed_server_retains_private_workspace_and_fixed_step(monkeypatch, tmp_path):
    class Process:
        def poll(self):
            return 1

        def wait(self, timeout):
            return 1

    monkeypatch.setattr(demo, "model_environment", lambda *a, **k: {})
    monkeypatch.setattr(demo.subprocess, "Popen", lambda *a, **k: Process())
    workspace = tmp_path / "failed"
    result = demo.run_demo(workspace, cleanup=True)
    assert not result["ok"] and result["server_stopped"] and not result["workspace_removed"]
    assert {"name": "server_start", "status": "fail"} in result["steps"]
    assert (workspace / demo._MARKER).is_file()
    assert (workspace / "team/agent-a/identity.json").is_file()


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in ("COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR")),
    reason="Real offline demo requires explicitly provisioned pinned model snapshots",
)
def test_real_offline_cli_http_mcp_demo_and_cleanup(tmp_path):
    workspace = tmp_path / "real-demo"
    result = demo.run_demo(workspace, configure_codex=True, timeout=90, cleanup=True)
    assert result["ok"], result
    assert result["workspace_removed"] and result["server_stopped"] and not workspace.exists()
    assert result["provider_calls"] == 0
    assert {step["name"] for step in result["steps"] if step["status"] == "pass"} == {
        "bootstrap",
        "server_start",
        "publication",
        "cli_reuse",
        "mcp_reuse",
        "changed_evidence",
        "server_stop",
    }
