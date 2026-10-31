import asyncio
import hashlib
import json
import os
import sqlite3
import tomllib
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team

from colony.client import Client
from colony.memory import MemoryForbidden
from colony.models import Source


def test_real_memory_feedback_correction_retirement_and_replay(running_team):
    cli, homes, repo, directory = running_team
    question = "What is the build timeout setting?"
    original_id = "e" * 32
    original = directory / "original.json"
    original.write_text(json.dumps({
        "id": original_id, "question": question,
        "content": "The build timeout is TIMEOUT = 99 in settings.py.",
    }), encoding="utf-8")
    pushed = json.loads(cli(
        "--home", homes["a"], "push", original, "--ns", "team", "--repo", repo,
        "--source", "settings.py",
    ).stdout)
    assert pushed["stored"]
    source = Source(
        ref="settings.py", content_hash=hashlib.sha256((repo / "settings.py").read_bytes()).hexdigest()
    )
    owner, peer = Client(homes["a"]), Client(homes["b"])
    before = peer.ask(question, "team")
    assert before["status"] == "validated" and "99" in before["content"]
    assert "30" in before["evidence"][0]["text"]  # Valid hashes do not prove answer truth.
    feedback = peer.memory_feedback(
        original_id, "team", verdict="incorrect", note="password=sentinel", operation_id="a" * 32
    )
    assert feedback["memory"]["feedback"]["incorrect"] == 1
    assert owner.memory_inspect(original_id, "team")["memory"]["revision"] == 0
    with pytest.raises(MemoryForbidden):
        peer.memory_correct(
            original_id, "team", expected_revision=0, question=question,
            content="The build timeout is TIMEOUT = 30 in settings.py.", sources=[source],
        )
    cli("--home", homes["a"], "setup", "codex", "install", "--repo", repo, "--ns", "team")
    transport = tomllib.loads((repo / ".codex/config.toml").read_text(encoding="utf-8"))[
        "mcp_servers"
    ]["colony"]

    @asynccontextmanager
    async def connect(read_only=False):
        args = list(transport["args"])
        if read_only:
            args[args.index("--home") + 1] = str(homes["b"])
            args.append("--workflow-read-only")
        parameters = StdioServerParameters(
            command=transport["command"], args=args, cwd=transport["cwd"], env=dict(os.environ)
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session

    async def ask_current():
        async with connect(read_only=True) as session:
            assert {t.name for t in (await session.list_tools()).tools} == {"ask"}
            response = await session.call_tool("ask", {"question": question})
            assert not response.isError
            return json.loads(response.content[0].text)

    async def exercise():
        async with connect() as session:
            corrected = await session.call_tool("memory_correct", {
                "trail_id": original_id, "expected_revision": 0, "question": question,
                "content": "The build timeout is TIMEOUT = 30 in settings.py.",
                "sources": [{"ref": source.ref, "content_hash": source.content_hash}],
                "note": "Checked current settings.py", "operation_id": "1" * 32,
            })
            assert not corrected.isError, corrected.content
            result = json.loads(corrected.content[0].text)
            replacement_id = result["replacement_id"]
        retired = owner.memory_inspect(original_id, "team")
        assert retired["memory"]["state"] == "superseded"
        assert retired["memory"]["replacement_id"] == replacement_id
        assert "sentinel" not in json.dumps(retired)
        hit = await ask_current()
        assert hit["id"] == replacement_id and "30" in hit["content"]
        assert hit["memory"]["state"] == "active"
        replay = owner.memory_correct(
            original_id, "team", expected_revision=0, question=question,
            content="The build timeout is TIMEOUT = 30 in settings.py.", sources=[source],
            note="Checked current settings.py", operation_id="1" * 32,
        )
        assert replay["replayed"] and replay["replacement_id"] == replacement_id
        owner.memory_change(
            replacement_id, "team", expected_revision=0, action="retract", operation_id="2" * 32
        )
        assert not (await ask_current())["hit"]  # Neither authored answer nor evidence fallback.
        restored = json.loads(cli(
            "--home", homes["a"], "memory", "restore", replacement_id, "--ns", "team",
            "--expected-revision", "1", "--operation-id", "3" * 32,
        ).stdout)
        assert restored["memory"]["state"] == "active"
        replay = owner.memory_change(
            replacement_id, "team", expected_revision=0, action="retract", operation_id="2" * 32
        )
        assert replay["replayed"] and replay["memory"]["state"] == "active"
        duplicate = json.loads(cli(
            "--home", homes["a"], "push", original, "--ns", "team", "--repo", repo,
            "--source", "settings.py",
        ).stdout)
        assert duplicate["duplicate"]
        assert owner.memory_inspect(original_id, "team")["memory"]["state"] == "superseded"
        assert (await ask_current())["id"] == replacement_id
        with sqlite3.connect(directory / "data/colony.db") as db:
            assert db.execute("SELECT COUNT(*) FROM trails").fetchone()[0] == 2

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
