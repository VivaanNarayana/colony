"""Local-only target runtime adapter; never invokes a model client."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path


def environment(run_dir):
    root = Path(run_dir).resolve()
    if os.name == "nt" and root.drive.lower() != "d:":
        raise ValueError("Private runtime storage must be on D")
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
               "COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR", "COLONY_TEST_CONTEXT_TOKENIZER", "COLONY_CONTEXT_TOKENIZER"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    for name, relative in {"TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp", "HOME": "home", "USERPROFILE": "home",
                           "CODEX_HOME": "codex-home", "XDG_CACHE_HOME": "cache", "HF_HOME": "cache/huggingface",
                           "FASTEMBED_CACHE_PATH": "cache/fastembed", "COLONY_MODEL_CACHE": "cache/colony",
                           "PYTHONPYCACHEPREFIX": "cache/python", "TIKTOKEN_CACHE_DIR": "cache/tiktoken"}.items():
        path = root / "runtime" / relative
        path.mkdir(parents=True, exist_ok=True)
        result[name] = str(path)
    tokenizer = result.get("COLONY_CONTEXT_TOKENIZER") or result.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if tokenizer:
        result["COLONY_CONTEXT_TOKENIZER"] = tokenizer
    result.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    return result


def _run(protocol, args, env, *, timeout=60):
    return subprocess.run([protocol["target"]["python"], "-I", *args], env=env, capture_output=True, text=True,
                          timeout=timeout, check=True)


def _roles(team):
    for label in ("a", "b"):
        identity = json.loads((Path(team["homes"][label]) / "identity.json").read_text())
        scopes = identity["grant"]["scopes"]
        if scopes != [{"namespace": "bench", "push": label == "a", "pull": True}]:
            raise ValueError("Fixture identity permission mismatch")
    return {"a": "write", "b": "read_only"}


def provision(run_dir, protocol, workload, repo):
    root = Path(run_dir).resolve()
    identifier = workload["id"] if isinstance(workload, dict) else workload
    directory = (root / "private" / identifier).resolve()
    if not directory.is_relative_to(root / "private"):
        raise ValueError("Invalid workload path")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "team"
    if destination.exists():
        raise ValueError("Fixture team must be fresh")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    code = "import json,sys;from pathlib import Path;from colony.onboarding import initialize;print(json.dumps(initialize(Path(sys.argv[1]),Path(sys.argv[2]),namespace='bench',server=sys.argv[3])))"
    result = _run(protocol, ["-c", code, str(destination), str(Path(repo).resolve()), f"http://127.0.0.1:{port}"], environment(root))
    team = json.loads(result.stdout)
    if not team.get("ok") or Path(team["workspace"]).resolve() != destination:
        raise ValueError("Target onboarding did not produce requested team")
    team["roles"] = _roles(team)
    team["run_dir"] = str(root)
    return team


def trail_count(team):
    path = Path(team["data"]) / "colony.db"
    if not path.is_file():
        raise ValueError("Server database unavailable")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM trails").fetchone()[0]


def _stop(process):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
        else:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


@contextmanager
def server(team, protocol, env):
    root = Path(team["run_dir"])
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / (Path(team["workspace"]).parent.name + "-server.log")
    with log.open("xb") as stream:
        process = subprocess.Popen([protocol["target"]["python"], "-I", "-m", "colony.cli", *team["serve_args"], "--offline", *protocol["target"].get("server_args", [])],
                                   env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned Colony server exited before readiness; see retained server log")
                try:
                    with urllib.request.urlopen(team["server"] + "/health", timeout=1) as response:
                        healthy = response.status == 200
                except OSError:
                    healthy = False
                if healthy:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned Colony server readiness timed out")
                time.sleep(0.1)
            if trail_count(team) != 0:
                raise ValueError("Fresh fixture store contains preexisting trails")
            yield team
        finally:
            _stop(process)


async def _mcp_preflight(team, protocol, repo, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _roles(team)
    roles = {}
    for label in ("a", "b"):
        args = ["-I", "-m", "colony.cli", "--home", team["homes"][label], "mcp", "--workflow-namespace", "bench", "--workflow-repo", str(repo), *protocol["target"].get("mcp_args", [])]
        if label == "b":
            args.append("--workflow-read-only")
        params = StdioServerParameters(command=protocol["target"]["python"], args=args, env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            listing = await session.list_tools()
            names = {tool.name for tool in listing.tools}
            if "ask" not in names or (("push" in names) != (label == "a")) or (label == "b" and names != {"ask"}):
                raise ValueError("Target MCP tools violate role capability contract")
            answer = await session.call_tool("ask", {"question": "Evaluation fresh-store authentication control", "namespace": "bench"})
            if answer.isError:
                raise ValueError("Target MCP empty ask failed")
            payloads = [json.loads(block.text) for block in answer.content if block.type == "text"]
            if len(payloads) != 1 or payloads[0].get("hit") is not False:
                raise ValueError("Fresh target MCP did not return authenticated miss")
            roles[label] = {"tools": [tool.model_dump(mode="json") for tool in listing.tools], "instructions": initialized.instructions,
                            "authenticated_empty_miss": True, "read_only": label == "b"}
    return {"ok": True, "native_ready": False, "roles": roles, "errors": []}


def mcp_preflight(team, protocol, repo, env):
    return asyncio.run(asyncio.wait_for(_mcp_preflight(team, protocol, repo, env), timeout=60))


def configure(team, protocol, repo, read_only):
    _roles(team)
    label = "b" if read_only else "a"
    args = ["-m", "colony.cli", "--home", team["homes"][label], "setup", "codex", "install", "--repo", str(repo), "--ns", "bench"]
    if read_only:
        args.append("--read-only")
    # Setup flags require a distinct allowlist from MCP flags; never silently discard them.
    args.extend(protocol["target"].get("setup_args", []))
    _run(protocol, args, environment(team["run_dir"]))
    artifacts = []
    for relative in ("AGENTS.md", "AGENTS.override.md", ".codex/config.toml"):
        path = Path(repo) / relative
        if path.is_file():
            raw = path.read_bytes()
            artifacts.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    if not artifacts:
        raise ValueError("Target setup produced no managed guidance/configuration")
    return {"mode": "managed", "role": label, "files": artifacts, "native_client_loaded": False}
