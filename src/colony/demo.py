"""An isolated, real-transport demonstration using fixture facts and local ONNX models."""

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from colony.onboard_io import check_tree, private_directory, safe_path, write_new
from colony.onboarding import initialize
from colony.provision import model_environment

_MARKER = ".colony-demo.json"


def _cleanup(workspace, owner):
    """Delete only a successful demo's fixed owned layout, without recursive removal."""
    workspace = safe_path(workspace)
    check_tree(workspace)
    if json.loads((workspace / _MARKER).read_bytes()) != owner:
        raise ValueError("Demo ownership changed; workspace retained")
    allowed = {
        _MARKER,
        "repo/settings.py",
        "repo/AGENTS.md",
        "repo/.codex/config.toml",
        "team/.colony-bootstrap.json",
        "team/.colony-bootstrap-plan.json",
        "team/administrator/issuer.pem",
        "team/administrator/issuer.pub",
    }
    for label in ("a", "b"):
        allowed.update(
            f"team/agent-{label}/{name}" for name in ("identity.json", "issuer.pub", "queue.key")
        )
    allowed.update(
        "team/data/" + name
        for name in ("colony.db", "colony.db-wal", "colony.db-shm", "storage.key")
    )
    allowed.add(f"team/data/blobs/{owner['trail_id']}.enc")
    directories = {
        "repo",
        "repo/.codex",
        "team",
        "team/administrator",
        "team/agent-a",
        "team/agent-b",
        "team/data",
        "team/data/blobs",
    }
    files, folders = [], []
    for directory, dirs, names in os.walk(workspace, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            if path.relative_to(workspace).as_posix() not in directories:
                raise ValueError("Unexpected demo files; workspace retained")
            folders.append(path)
        for name in names:
            path = Path(directory) / name
            if path.relative_to(workspace).as_posix() not in allowed:
                raise ValueError("Unexpected demo files; workspace retained")
            files.append(path)
    # All targets are verified under the explicitly named, newly owned workspace.
    for path in files:
        safe_path(path)
        if not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("Unsafe demo cleanup; workspace retained")
    for path in files:
        if path.name != _MARKER:
            path.unlink()
    for path in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        path.rmdir()
    (workspace / _MARKER).unlink()
    workspace.rmdir()


async def _mcp_lookup(python, home, repo, environment, timeout):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def perform():
        parameters = StdioServerParameters(
            command=python,
            args=[
                "-I",
                "-m",
                "colony.cli",
                "--home",
                str(home),
                "mcp",
                "--workflow-namespace",
                "team",
                "--workflow-repo",
                str(repo),
                "--workflow-read-only",
            ],
            cwd=str(repo),
            env=environment,
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            if {tool.name for tool in (await session.list_tools()).tools} != {"ask"}:
                raise ValueError("Demo read-only MCP tools differ")
            result = await session.call_tool("ask", {"question": "Build timeout configuration"})
            if result.isError or not result.content:
                raise ValueError("Demo MCP lookup failed")
            return json.loads(result.content[0].text)

    return await asyncio.wait_for(perform(), timeout=timeout)


def _stale_receipt(result, trail_id):
    return (
        isinstance(result, dict)
        and result.get("hit") is True
        and result.get("id") == trail_id
        and result.get("status") == "stale"
    )


def _stop(process):
    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return process.poll() is not None


def run_demo(
    workspace: Path, *, models_dir=None, configure_codex=False, timeout=90, cleanup=False
) -> dict:
    """Create a new private fixture, stop its processes, and optionally remove owned files.

    Failure reports preserve the workspace for diagnosis. No existing project or
    server is used, and no model-provider, transcript or global config access occurs.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 10 <= timeout <= 300:
        raise ValueError("Demo timeout must be 10..300 seconds")
    workspace = safe_path(workspace)
    if workspace.exists() or not workspace.parent.is_dir():
        raise ValueError("Demo requires a new absolute workspace with an existing parent")
    # Verify before creating a workspace: no download or partial demo for missing models.
    environment = {**os.environ, **model_environment(models_dir, offline=True)}
    python = os.path.abspath(sys.executable)
    deadline = time.monotonic() + timeout
    owner = {"format": "colony-demo-v1", "id": uuid.uuid4().hex, "trail_id": uuid.uuid4().hex}
    private_directory(workspace)
    write_new(workspace / _MARKER, json.dumps(owner).encode())
    repo = workspace / "repo"
    private_directory(repo)
    write_new(repo / "settings.py", b"TIMEOUT = 30\n")
    steps, process, phase = [], None, "bootstrap"
    ok, stopped = False, True

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Demo time limit")
        return seconds

    def cli(*args):
        result = subprocess.run(
            [python, "-I", "-m", "colony.cli", *map(str, args)],
            cwd=repo,
            env=environment,
            capture_output=True,
            timeout=remaining(),
            check=False,
        )
        if result.returncode or len(result.stdout) > 200_000:
            raise ValueError("Demo client operation failed")
        return json.loads(result.stdout)

    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        team = initialize(workspace / "team", repo, server=url, configure_codex=configure_codex)
        if not team["ok"]:
            raise ValueError("Demo project setup failed")
        steps.append({"name": phase, "status": "pass"})
        phase = "server_start"
        process = subprocess.Popen(
            [python, "-I", "-m", "colony.cli", *team["serve_args"], "--offline"],
            cwd=repo,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stopped = False
        with httpx.Client(trust_env=False, follow_redirects=False) as health:
            while True:
                if process.poll() is not None:
                    raise ValueError("Demo server exited")
                try:
                    response = health.get(url + "/health", timeout=min(0.5, remaining()))
                    if response.status_code == 200 and response.json() == {"status": "ok"}:
                        break
                except httpx.TransportError:
                    pass
                remaining()
                time.sleep(0.05)
        steps.append({"name": phase, "status": "pass"})
        phase = "publication"
        # Fixed fixture content only; fingerprints come from the actual generated file.
        from colony.models import Trail
        from colony.validation import snapshot

        home_a, home_b = Path(team["homes"]["a"]), Path(team["homes"]["b"])
        trail = Trail(
            id=owner["trail_id"],
            namespace="team",
            question="Build timeout configuration",
            content="The build timeout is TIMEOUT = 30 in settings.py.",
            sources=[snapshot(repo, "settings.py")],
        )
        # Use the actual CLI to publish without retaining another credential-bearing input.
        finding = workspace / "finding.json"
        write_new(finding, trail.model_dump_json().encode())
        try:
            pushed = cli("--home", home_a, "push", finding, "--ns", "team")
        finally:
            finding.unlink(missing_ok=True)
        if pushed.get("stored") is not True:
            raise ValueError("Demo publication was not accepted")
        steps.append({"name": phase, "status": "pass"})
        phase = "cli_reuse"
        answer = cli("--home", home_b, "ask", trail.question, "--ns", "team")
        if not (
            answer.get("hit")
            and answer.get("status") == "validated"
            and answer.get("id") == trail.id
            and "30" in answer.get("content", "")
        ):
            raise ValueError("Demo reuse did not validate")
        steps.append({"name": phase, "status": "pass"})
        phase = "mcp_reuse"
        answer = asyncio.run(_mcp_lookup(python, home_b, repo, environment, remaining()))
        if not (
            answer.get("hit")
            and answer.get("status") == "validated"
            and answer.get("id") == trail.id
        ):
            raise ValueError("Demo MCP reuse did not validate")
        steps.append({"name": phase, "status": "pass"})
        phase = "changed_evidence"
        (repo / "settings.py").write_bytes(b"TIMEOUT = 60\n")
        changed = cli("--home", home_b, "ask", trail.question, "--ns", "team")
        if not _stale_receipt(changed, trail.id):
            raise ValueError("Demo failed to reject changed evidence")
        remaining()
        steps.append({"name": phase, "status": "pass"})
        ok = True
    except Exception:
        steps.append({"name": phase, "status": "fail"})
    finally:
        if process is not None:
            stopped = _stop(process)
    if not stopped:
        ok = False
        steps.append({"name": "server_stop", "status": "fail"})
    else:
        steps.append({"name": "server_stop", "status": "pass"})
    removed = False
    if cleanup and ok:
        try:
            _cleanup(workspace, owner)
            removed = True
        except (OSError, ValueError):
            ok = False
            steps.append({"name": "cleanup", "status": "fail"})
    return {
        "ok": ok,
        "format": "colony-demo-v1",
        "workspace": str(workspace),
        "workspace_removed": removed,
        "server_stopped": stopped,
        "steps": steps,
        "provider_calls": 0,
        "measurement": "deterministic local fixture; no token-savings claim",
        "next_steps": []
        if ok
        else [
            "Workspace retained. Run colony doctor with its explicit homes, project and model paths."
        ],
    }
