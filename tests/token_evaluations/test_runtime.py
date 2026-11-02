import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.token_evaluation import runtime


def team_fixture(tmp_path):
    homes = {}
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        (home / "identity.json").write_text(json.dumps({"grant": {"scopes": [{"namespace": "bench", "push": label == "a", "pull": True}]}}))
        homes[label] = str(home)
    return {"homes": homes, "run_dir": str(tmp_path), "workspace": str(tmp_path / "team"), "data": str(tmp_path / "data"), "server": "http://127.0.0.1:1", "serve_args": ["serve"]}


def test_environment_strips_secrets_and_isolates_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("COLONY_EMBED_MODEL_DIR", "C:/pinned-readonly")
    env = runtime.environment(tmp_path)
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["COLONY_EMBED_MODEL_DIR"] == "C:/pinned-readonly"
    assert Path(env["CODEX_HOME"]).is_relative_to(tmp_path)
    assert env["HF_HUB_OFFLINE"] == "1"


def test_roles_rejects_writable_b(tmp_path):
    team = team_fixture(tmp_path)
    identity = Path(team["homes"]["b"]) / "identity.json"
    identity.write_text(json.dumps({"grant": {"scopes": [{"namespace": "bench", "push": True, "pull": True}]}}))
    with pytest.raises(ValueError, match="permission"):
        runtime._roles(team)


def test_target_subprocess_always_isolated(monkeypatch):
    called = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, **kw: called.append((args, kw)))
    runtime._run({"target": {"python": "target-python"}}, ["-c", "print(1)"], {"OFFLINE": "1"})
    assert called[0][0] == ["target-python", "-I", "-c", "print(1)"]
    assert called[0][1]["env"] == {"OFFLINE": "1"}


def test_server_failure_stops_owned_process(tmp_path, monkeypatch):
    team = team_fixture(tmp_path)
    process = Mock()
    process.poll.return_value = 7
    stopped = []
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(runtime, "_stop", lambda p: stopped.append(p))
    with pytest.raises(RuntimeError, match="exited"), runtime.server(team, {"target": {"python": "target"}}, {}):
        pytest.fail("unhealthy server must not yield")
    assert stopped == [process]
    assert list((tmp_path / "logs").glob("*-server.log"))


def test_configure_b_uses_readonly_target_setup(tmp_path, monkeypatch):
    team = team_fixture(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []
    def fake_run(protocol, args, env):
        calls.append(args)
        (repo / "AGENTS.md").write_text("managed fixture")
    monkeypatch.setattr(runtime, "_run", fake_run)
    receipt = runtime.configure(team, {"target": {}}, repo, True)
    assert "--read-only" in calls[0]
    assert team["homes"]["b"] in calls[0]
    assert receipt["native_client_loaded"] is False
    assert receipt["files"][0]["bytes"] == len("managed fixture")


def test_server_rejects_preexisting_trails(tmp_path, monkeypatch):
    team = team_fixture(tmp_path)
    process = Mock()
    process.poll.return_value = None
    stopped = []
    response = Mock()
    response.status = 200
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *a, **k: response)
    monkeypatch.setattr(runtime, "trail_count", lambda t: 1)
    monkeypatch.setattr(runtime, "_stop", lambda p: stopped.append(p))
    with pytest.raises(ValueError, match="preexisting"), runtime.server(team, {"target": {"python": "target"}}, {}):
        pytest.fail("nonempty store must not yield")
    assert stopped == [process]


def test_sqlite_inventory_is_readonly(tmp_path):
    team = team_fixture(tmp_path)
    data = Path(team["data"])
    data.mkdir()
    with runtime.sqlite3.connect(data / "colony.db") as db:
        db.execute("CREATE TABLE trails (id TEXT)")
        db.execute("INSERT INTO trails VALUES ('existing')")
    assert runtime.trail_count(team) == 1


def test_tokenizer_forwarding_falls_back_to_explicit_test_fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("COLONY_CONTEXT_TOKENIZER", raising=False)
    monkeypatch.setenv("COLONY_TEST_CONTEXT_TOKENIZER", "D:/pinned/tokenizer")
    assert runtime.environment(tmp_path)["COLONY_CONTEXT_TOKENIZER"] == "D:/pinned/tokenizer"
    monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", "D:/explicit/tokenizer")
    assert runtime.environment(tmp_path)["COLONY_CONTEXT_TOKENIZER"] == "D:/explicit/tokenizer"
