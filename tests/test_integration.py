import asyncio
import json
import os
import socket
import subprocess
import sys
import time

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
    cli, homes, repo, directory = running_team
    # Generic CLI/HTTP storage still accepts source-less notes; MCP can retrieve them.
    note = directory / "unverified-note.json"
    note.write_text(
        json.dumps({"question": "MCP test setup", "content": "Use the stdio transport"}),
        encoding="utf-8",
    )
    assert json.loads(cli("--home", homes["a"], "push", note, "--ns", "team").stdout)["stored"]

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
            initialization = await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"ask", "push"}
            # The client still receives policy/proof rules after description deduplication.
            assert "Evidence-only derivation is reuse" in initialization.instructions
            assert (
                "Only after necessary research supports requested facts/citations"
                in initialization.instructions
            )
            assert "never after sufficient reuse" in next(
                t.description for t in tools.tools if t.name == "push"
            )
            push_schema = next(t.inputSchema for t in tools.tools if t.name == "push")
            assert set(push_schema["$defs"]["FileEvidence"]["properties"]) == {
                "ref",
                "content_hash",
                "kind",
            }
            assert "sources" in push_schema["required"]
            assert push_schema["properties"]["sources"]["minItems"] == 1
            assert (
                "copied unchanged from new research reads"
                in push_schema["properties"]["sources"]["description"]
            )
            hash_schema = push_schema["$defs"]["FileEvidence"]["properties"]["content_hash"]
            assert "Never reconstruct or edit it" in hash_schema["description"]
            assert "memory-receipt hash is not new research" in hash_schema["description"]
            for source_arguments in ({}, {"sources": None}, {"sources": []}):
                rejected = await session.call_tool(
                    "push",
                    {
                        "namespace": "team",
                        "question": "Unsupported MCP save",
                        "content": "This must not be saved.",
                        **source_arguments,
                    },
                )
                assert rejected.isError
                assert "sources" in rejected.content[0].text
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
            # The flat proof is already carried by the displayed current read.
            assert receipt["sources"] == []
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
