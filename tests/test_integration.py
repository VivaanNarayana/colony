import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def running_team(tmp_path):
    def cli(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "colony.cli", *map(str, args)],
            capture_output=True,
            text=True,
            timeout=30,
            check=check,
        )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.py").write_text("TIMEOUT = 30\n")
    admin = tmp_path / "admin"
    cli("init", "--dir", admin)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "colony.cli",
            "serve",
            "--repo",
            str(repo),
            "--data",
            str(tmp_path / "data"),
            "--issuer",
            str(admin / "issuer.pub"),
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/health", timeout=0.2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if server.poll() is not None:
                pytest.fail("Server exited during startup")
            time.sleep(0.05)
        else:
            pytest.fail("Server did not become healthy")
        homes = {}
        for name, capture in [("a", "outputs"), ("b", "none")]:
            key = tmp_path / f"{name}.json"
            home = tmp_path / name
            cli(
                "issue",
                "--issuer",
                admin / "issuer.pem",
                "--name",
                name,
                "--server",
                url,
                "--push",
                "team",
                "--pull",
                "team",
                "--capture",
                capture,
                "--out",
                key,
            )
            cli("--home", home, "join", key, "--issuer", admin / "issuer.pub")
            homes[name] = home
        yield cli, homes, repo, tmp_path
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_real_cli_two_agents_capture_and_stale(running_team):
    cli, homes, repo, directory = running_team
    result = cli(
        "--home",
        homes["a"],
        "run",
        "--ns",
        "team",
        "--question",
        "Build timeout configuration",
        "--repo",
        repo,
        "--source",
        "settings.py",
        "--",
        sys.executable,
        "-c",
        "print('password=secretvalue'); raise SystemExit(7)",
        check=False,
    )
    assert result.returncode == 7 and "capture" in result.stderr.lower()
    assert "secretvalue" not in result.stdout
    assert json.loads(result.stdout)["stored"]
    answer = json.loads(cli("--home", homes["b"], "ask", "Build timeout configuration").stdout)
    assert answer["status"] == "validated" and "[REDACTED]" in answer["content"]
    assert answer["tainted"]
    (repo / "settings.py").write_text("TIMEOUT = 60\n")
    answer = json.loads(cli("--home", homes["b"], "ask", "Build timeout configuration").stdout)
    assert answer["status"] == "stale"
    marker = directory / "must-not-execute"
    denied = cli(
        "--home",
        homes["b"],
        "run",
        "--ns",
        "team",
        "--question",
        "q",
        "--repo",
        repo,
        "--",
        sys.executable,
        "-c",
        f"open({str(marker)!r},'w').write('bad')",
        check=False,
    )
    assert denied.returncode == 1 and not marker.exists()


@pytest.mark.parametrize("evidence_files", [False, True])
def test_real_mcp_stdio_push_and_ask(running_team, evidence_files):
    _, homes, repo, _ = running_team

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "colony.cli", "--home", str(homes["a"]), "mcp"]
            + (["--evidence-files"] if evidence_files else []),
            env=dict(os.environ),
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "ask", "push", "memory_inspect", "memory_feedback", "memory_correct", "memory_change"
            }
            push_schema = next(t.inputSchema for t in tools.tools if t.name == "push")
            assert set(push_schema["$defs"]["FileEvidence"]["properties"]) == {
                "ref",
                "content_hash",
                "kind",
            }
            pushed = await session.call_tool(
                "push",
                {
                    "namespace": "team",
                    "question": "MCP test setup",
                    "content": "Use the stdio transport",
                },
            )
            assert not pushed.isError
            result = await session.call_tool("ask", {"question": "MCP test setup"})
            assert not result.isError
            data = json.loads(result.content[0].text)
            assert data["hit"] and data["status"] == "unverified"
            assert "UNTRUSTED" in data["provenance"]
            assert "verification" not in data
            import hashlib

            stored = await session.call_tool(
                "push",
                {
                    "namespace": "team",
                    "question": "MCP evidence setup",
                    "content": "Use stdio",
                    "sources": [
                        {
                            "ref": "settings.py",
                            "content_hash": hashlib.sha256(
                                (repo / "settings.py").read_bytes()
                            ).hexdigest(),
                        }
                    ],
                },
            )
            assert not stored.isError
            validated = await session.call_tool("ask", {"question": "MCP evidence setup"})
            receipt = json.loads(validated.content[0].text)
            assert receipt["status"] == "validated"
            assert receipt["recommendation"] == "reuse_if_relevant"
            assert receipt["sources"][0]["ref"] == "settings.py"
            assert receipt["current_repository_reads"][0]["complete_file"] == evidence_files
            assert receipt["verification"]["file_fingerprints_match"]
            assert receipt["verification"]["source_reads_current"]
            assert "UNTRUSTED" in receipt["provenance"]
            assert "TIMEOUT" in receipt["current_repository_reads"][0]["content"]
            assert receipt["current_repository_reads"][0]["path"] == "settings.py"
            assert (
                receipt["current_repository_reads"][0]["sha256"]
                == hashlib.sha256((repo / "settings.py").read_bytes()).hexdigest()
            )
            assert "checks" not in receipt

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


def test_codex_setup_two_agents_workflow_and_restart_dedup(running_team):
    cli, homes, repo, directory = running_team
    preview = json.loads(
        cli(
            "--home",
            homes["a"],
            "setup",
            "codex",
            "install",
            "--repo",
            repo,
            "--ns",
            "team",
            "--dry-run",
        ).stdout
    )
    assert not (repo / ".codex" / "config.toml").exists()
    assert preview["changed"]
    cli("--home", homes["a"], "setup", "codex", "install", "--repo", repo, "--ns", "team")
    installed = json.loads(cli("setup", "codex", "status", "--repo", repo).stdout)
    assert installed["installed"]

    other_repo = directory / "repo-b"
    other_repo.mkdir()
    (other_repo / "settings.py").write_bytes((repo / "settings.py").read_bytes())
    cli(
        "--home",
        homes["b"],
        "setup",
        "codex",
        "install",
        "--repo",
        other_repo,
        "--ns",
        "team",
        "--read-only",
    )

    @asynccontextmanager
    async def connection(project):
        config = tomllib.loads((project / ".codex" / "config.toml").read_text(encoding="utf-8"))
        server = config["mcp_servers"]["colony"]
        parameters = StdioServerParameters(
            command=server["command"],
            args=server["args"],
            cwd=server["cwd"],
            env=dict(os.environ),
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    def stored_rows():
        with sqlite3.connect(directory / "data" / "colony.db") as db:
            return db.execute("SELECT id, created_at FROM trails ORDER BY id").fetchall()

    async def exercise():
        question = "What is the build timeout setting?"
        async with connection(repo) as agent_a:
            missed = await agent_a.call_tool("ask", {"question": question})
            assert not missed.isError and not json.loads(missed.content[0].text)["hit"]
            # Simulate ordinary research, retaining its actual fingerprint for publication.
            source = (repo / "settings.py").read_bytes()
            assert b"TIMEOUT = 30" in source
            finding = {
                "question": question,
                "content": "The build timeout is configured as TIMEOUT = 30 in settings.py.",
                "sources": [
                    {"ref": "settings.py", "content_hash": hashlib.sha256(source).hexdigest()}
                ],
            }
            published = await agent_a.call_tool("push", finding)
            assert not published.isError
            first = json.loads(published.content[0].text)
            assert first["stored"] and not first["duplicate"]
            denied = await agent_a.call_tool("ask", {"question": question, "namespace": "private"})
            assert denied.isError

        rows = stored_rows()
        assert len(rows) == 1
        async with connection(repo) as restarted_a:
            again = await restarted_a.call_tool("push", finding)
            assert not again.isError
            duplicate = json.loads(again.content[0].text)
            assert duplicate["duplicate"] and duplicate["id"] == first["id"]
            assert stored_rows() == rows  # Duplicate submission does not refresh age.

        async with connection(other_repo) as agent_b:
            assert {t.name for t in (await agent_b.list_tools()).tools} == {"ask"}
            hit = await agent_b.call_tool("ask", {"question": question})
            assert not hit.isError
            receipt = json.loads(hit.content[0].text)
            assert receipt["status"] == "validated" and receipt["tainted"]
            assert "TIMEOUT = 30" in receipt["current_repository_reads"][0]["content"]
            assert receipt["verification"]["source_reads_current"]
            # A sufficient hit is answered from its source text; there is no publication.
            assert stored_rows() == rows
            (repo / "settings.py").write_text("TIMEOUT = 60\n")
            stale = await agent_b.call_tool("ask", {"question": question})
            assert not stale.isError
            assert json.loads(stale.content[0].text)["status"] == "stale"

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    cli("setup", "codex", "uninstall", "--repo", repo)
    assert not json.loads(cli("setup", "codex", "status", "--repo", repo).stdout)["installed"]
    assert (homes["a"] / "identity.json").exists()


def test_setup_cli_errors_explain_conflict_without_echoing_configuration(tmp_path):
    project = tmp_path / "project"
    config = project / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    secret = "private-config-sentinel"
    original = f'password = "{secret}"\ninvalid !'.encode()
    config.write_bytes(original)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "colony.cli",
            "setup",
            "codex",
            "install",
            "--repo",
            str(project),
            "--ns",
            "team",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert "Invalid Codex TOML configuration" in result.stderr
    assert secret not in result.stderr + result.stdout
    assert config.read_bytes() == original and not (project / "AGENTS.md").exists()
