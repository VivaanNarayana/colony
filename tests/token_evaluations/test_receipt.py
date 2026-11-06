"""Native declaration consistency controls; fabricated receipts never prove usage."""

import hashlib
import json
import shutil
import sys
from contextlib import nullcontext

import pytest
from test_report import native_receipt_control, put
from test_report import run as run

from scripts.token_evaluation.report import report


@pytest.fixture
def native_run(run):
    root, quality, _ = run
    native_receipt_control(root, quality)
    return root


def override(invocation, key, transform):
    args = invocation["args"]
    for index in range(len(args) - 1):
        if args[index] == "-c" and args[index + 1].startswith(key + "="):
            args[index + 1] = key + "=" + json.dumps(transform(json.loads(args[index + 1].split("=", 1)[1])))
            return
    raise AssertionError("Fixture setting missing")


def test_current_receipts_and_relocated_read_only_copy_pass(native_run):
    root = native_run
    value = report(root, root / "quality.json", root / "valid.json")
    assert value["harness_complete"] and value["native_evidence"]
    # The source root is the pytest root, so copy to its sibling, not inside itself.
    copy = root.parent / (root.name + "-relocated")
    shutil.copytree(root, copy)
    replay = report(copy, copy / "quality.json", copy / "replayed.json")
    assert replay["arms"] == value["arms"]


@pytest.mark.parametrize("change", [
    "model", "effort", "research_limit", "repository_command", "repository_root",
    "baseline_colony_server", "colony_home", "missing_isolation", "duplicate_override",
    "forwarded_environment", "workspace", "configuration_role", "missing_guidance_hash",
])
def test_native_argument_and_context_drift_rejected(native_run, change):
    root = native_run
    identity = "b" if change in {"colony_home", "configuration_role", "missing_guidance_hash"} else "a"
    path = root / "control" / (identity + "-invocation.json")
    value = json.loads(path.read_text())
    if change == "model":
        value["args"][value["args"].index("-m") + 1] = "other-model"
    elif change == "effort":
        override(value, "model_reasoning_effort", lambda _: "high")
    elif change == "research_limit":
        def alter(args):
            args[args.index("--max-calls") + 1] = "7"
            return args
        override(value, "mcp_servers.repository.args", alter)
    elif change == "repository_command":
        override(value, "mcp_servers.repository.command", lambda _: "other-python")
    elif change == "repository_root":
        def alter(args):
            args[args.index("--root") + 1] = str(root / "other")
            return args
        override(value, "mcp_servers.repository.args", alter)
    elif change == "baseline_colony_server":
        value["args"][-1:-1] = ["-c", 'mcp_servers.colony.command="unexpected"']
    elif change == "colony_home":
        def alter(args):
            args[args.index("--home") + 1] = str(root / "private/other")
            return args
        override(value, "mcp_servers.colony.args", alter)
    elif change == "missing_isolation":
        value["args"].remove("--ephemeral")
    elif change == "duplicate_override":
        value["args"][-1:-1] = ["-c", 'model_reasoning_effort="low"']
    elif change == "forwarded_environment":
        def alter(names):
            optional = "COLONY_EMBED_MODEL_DIR"
            return [n for n in names if n != optional] if optional in names else [*names, optional]
        override(value, "mcp_servers.repository.env_vars", alter)
    elif change == "workspace":
        value["client_workspace"] = str(root / "active/no_colony/gateway")
    elif change == "configuration_role":
        value["configuration"]["role"] = "b"
    else:
        value.pop("guidance_sha256")
    put(path, value)
    with pytest.raises(ValueError, match="Native"):
        report(root, root / "quality.json", root / "refused.json")
    assert not (root / "refused.json").exists()


@pytest.mark.parametrize("change", ["missing", "bytes", "question_rebound", "guidance_rebound"])
def test_native_prompt_file_and_captured_guidance_binding(native_run, change):
    root = native_run
    identity = "b" if change == "guidance_rebound" else "a"
    path = root / "prompts" / (identity + ".txt")
    invocation_path = root / "control" / (identity + "-invocation.json")
    invocation = json.loads(invocation_path.read_text())
    if change == "missing":
        path.unlink()
    else:
        text = path.read_text()
        if change == "guidance_rebound":
            text = text.replace("Fixture installed guidance.", "Different guidance.")
        else:
            text += "\nUnfrozen task text.\n"
        path.write_text(text, encoding="utf-8")
        if change in {"question_rebound", "guidance_rebound"}:
            invocation["prompt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            put(invocation_path, invocation)
    with pytest.raises(ValueError, match="Native"):
        report(root, root / "quality.json", root / "refused.json")
    assert not (root / "refused.json").exists()


def test_native_joint_arm_order_is_not_only_per_arm_task_order(native_run):
    root = native_run
    journal = root / "control/attempts.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    journal.write_text("\n".join(map(json.dumps, reversed(rows))) + "\n")
    with pytest.raises(ValueError, match="Native.*order"):
        report(root, root / "quality.json", root / "refused.json")


def test_mocked_native_writer_round_trips_learn_and_read_only_receipts(tmp_path, monkeypatch):
    """Real collector/argument/prompt writer; only processes/server I/O are simulated."""
    from colony.onboarding import initialize
    from scripts.token_evaluation import native, runner
    from scripts.token_evaluation.output import OUTPUT_CONTRACT
    from scripts.token_evaluation.protocol import CONTRACT, collector_hashes, digest
    from scripts.token_evaluation.receipt import validate_native_receipts

    files = {"value.py": hashlib.sha256(b"VALUE = 42\n").hexdigest()}
    workload = {"id": "sample", "snapshots": [{"id": "v1", "files": files}],
                "tasks": [{"id": phase, "phase": phase, "snapshot": "v1",
                           "question": "What is the value?", "expected_use": "optional"}
                          for phase in ("learn", "reuse")]}
    protocol = {"contract": CONTRACT, "collector": {"files": collector_hashes()},
                "track": "natural", "output": {"contract": OUTPUT_CONTRACT,
                "format": "json", "answer_policy": "last_agent_message"},
                "client": {"executable": str(tmp_path / "client.exe"), "sha256": "c" * 64,
                           "model": "fixture-model", "effort": "low"},
                "target": {"python": sys.executable, "mcp_args": []},
                "research": {"max_calls": 6}, "workloads": [workload],
                "limits": {"max_attempts": 4, "gross_token_stop": 1000,
                           "wall_seconds": 60, "session_seconds": 5}}
    run = {"run_dir": str(tmp_path), "protocol": protocol, "protocol_sha256": digest(protocol),
           "arms": {arm: {"workspace": str(tmp_path / "workspaces" / arm)}
                    for arm in ("no_colony", "colony")}}
    for arm in run["arms"]:
        source = tmp_path / "workspaces" / arm / "sample/v1/value.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"VALUE = 42\n")
    for name in ("control", "logs", "auth"):
        (tmp_path / name).mkdir()
    put(tmp_path / "auth/auth.json", {})
    put(tmp_path / "run.json", run)
    put(tmp_path / "preflight.json", {"ok": True, "native_ready": True,
                                       "protocol_sha256": run["protocol_sha256"]})

    def provision(root, protocol, workload, repo):
        (root / "private/sample").mkdir(parents=True)
        return initialize(root / "private/sample/team", repo, namespace="bench",
                          server="http://127.0.0.1:1")

    def configure(team, protocol, repo, read_only):
        path = repo / "AGENTS.md"
        path.write_text("Captured read-only guidance." if read_only else "Captured writable guidance.")
        return {"mode": "managed", "role": "b" if read_only else "a", "files": [{"path": str(path)}]}

    class Process:
        def __init__(self, args, **options):
            self.stdout = options["stdout"]
            self.returncode = None

        def communicate(self, prompt, timeout):
            assert prompt and timeout > 0
            events = [{"type": "turn.started"}, {"type": "item.completed", "item": {
                "type": "agent_message", "text": '{"facts":{},"sources":[]}'}},
                {"type": "turn.completed", "usage": {"input_tokens": 10,
                 "cached_input_tokens": 4, "output_tokens": 2}}]
            self.stdout.write(("\n".join(map(json.dumps, events)) + "\n").encode())
            self.returncode = 0

    monkeypatch.setattr(native, "validate_authorization", lambda *args: None)
    monkeypatch.setattr(native, "_resources", lambda *args: None)
    monkeypatch.setattr(runner, "_verify", lambda *args: None)
    monkeypatch.setattr(native.runtime, "provision", provision)
    monkeypatch.setattr(native.runtime, "server", lambda *args: nullcontext())
    monkeypatch.setattr(native.runtime, "mcp_preflight", lambda *args: {"ok": True})
    monkeypatch.setattr(native.runtime, "trail_count", lambda *args: 0)
    monkeypatch.setattr(native.runtime, "configure", configure)
    monkeypatch.setattr(native.subprocess, "Popen", Process)
    collection = native.execute_live(tmp_path, {}, tmp_path / "auth")
    assert collection["status"] == "complete" and collection["attempts"] == 4, collection["error"]
    rows = [json.loads(line) for line in (tmp_path / "control/attempts.jsonl").read_text().splitlines()]
    validate_native_receipts(tmp_path, run, protocol, rows)
    assert [r["arm"] for r in rows] == ["no_colony", "colony", "colony", "no_colony"]
    assert collection["inventory"]["known_gross_tokens"] == 48
