"""Provider-free real HTTP/MCP transport regression; never invokes a worker model."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team

from colony.context_budget import _counter
from colony.models import ContextBudget
from colony.task_mcp import READ_ROUTING


def test_live_empty_hint_bundle_quota_and_readonly(running_team):
    _, homes, repo, _ = running_team
    (repo / "handler.py").write_text("import settings\nimport missing\n")

    async def check():
        args = [
            "-m",
            "colony.task_mcp",
            "--home",
            str(homes["b"]),
            "--repo",
            str(repo),
            "--ns",
            "team",
            "--question",
            "Trace handler",
            "--trace-reads",
            "--read-only",
            "--read-unit-limit",
            "2",
        ]
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            init = await session.initialize()
            assert "Memory started empty. Read sources." in init.instructions
            # The pinned native host reserves the tail of its 250-character
            # namespace preview for an ellipsis. All routing rules must fit.
            assert READ_ROUTING.strip() in init.instructions[:247]
            assert "allowance: 2 units total" in init.instructions
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"read", "research", "trace_read"}
            assert "empty at last check" in tools["research"].description
            description = tools["read"].description
            assert "Batch alone, max 8 ordered entries" in description
            assert "max 8 ordered entries share 16000 UTF-8 bytes" in description
            assert "Full coverage, tight quota: read unknown sizes singly." in description
            result = await session.call_tool("trace_read", {"paths": ["handler.py", "settings.py", "missing.py"]})
            assert not result.isError
            value = json.loads(result.content[0].text)
            assert value["read_accounting"] == {"admitted": 2, "denied": 1, "succeeded": 2}
            assert "publication_guidance" not in value
            assert value["results"][2]["evidence_ref"] is None
            denied = await session.call_tool("read", {"path": "settings.py"})
            assert denied.isError

    asyncio.run(check())


def test_initialized_guidance_fits_pr41_budget(running_team, monkeypatch):
    _, homes, repo, _ = running_team
    # Byte and o200k_base ceilings from the preceding PR41 head (5f18db4).
    instruction_limits = {"direct": (708, 123), "research": (1571, 271), "empty": (1648, 284)}
    preview_limits = {"direct": 41, "research": 41, "empty": 40}
    tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if tokenizer:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
        count = _counter(ContextBudget(encoding="o200k_base"))

    async def check(route, adaptive):
        args = ["-m", "colony.task_mcp", "--home", str(homes["b"]), "--repo", str(repo),
                "--ns", "team", "--question", "Explain the setting"]
        if route == "direct":
            args += ["--direct-path", "settings.py"]
        elif route == "research":
            args += ["--no-empty-memory-routing"]
        if adaptive:
            args += ["--context-budget", "3072", "--context-encoding", "utf8_bytes"]
        params = StdioServerParameters(command=sys.executable, args=args,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            instructions = (await session.initialize()).instructions
            tools = (await session.list_tools()).tools
            read = next(t for t in tools if t.name == "read")
            path_rule = "Single read defaults to host path." if route == "direct" else "Single read requires path."
            assert read.description.startswith(path_rule)
            assert "Outer retries require a return or catch leading back to the loop." in read.description[:247]
            result = await session.call_tool("read", {} if route == "direct" else {"path": "settings.py"})
            assert not result.isError
            assert json.loads(result.content[0].text)["complete_file"]
            after_read = (await session.list_tools()).tools
        preview = instructions[:247] + "..."
        assert READ_ROUTING.strip() in preview
        assert preview.startswith("Memory started empty. Read sources.") == (route == "empty")
        assert preview.startswith("Read the explicit path first.") == (route == "direct")
        research = next(t for t in tools if t.name == "research")
        early = "Requested memory or useful repeats first."
        late = "Read first for changes or absence. Skip once sources suffice unless asked."
        assert (early in preview) == (route == "research")
        assert (early in research.description[:247]) == (route != "empty")
        assert late in research.description[:247]
        if route == "empty":
            assert "Explicit lookup remains available." in research.description[:247]
        catalog_limits = {
            "direct": ((1617, 403), (1997, 492)),
            "research": ((1609, 401), (1989, 490)),
            "empty": ((1708, 419), (2088, 508)),
        }[route][int(adaptive)]
        assert len(instructions.encode()) <= instruction_limits[route][0]
        if tokenizer:
            assert count(instructions) <= instruction_limits[route][1]
            assert count(preview) <= preview_limits[route]
        # The previous head exposed publish after eligible evidence, costing
        # 1079 serialized bytes and 226 tokens. A plain direct read adds nothing.
        for observed, publish_visible in ((tools, False), (after_read, route != "direct")):
            assert ("publish" in {t.name for t in observed}) == publish_visible
            catalog = json.dumps([t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in observed],
                                 ensure_ascii=False, separators=(",", ":"))
            assert len(catalog.encode()) <= catalog_limits[0] + (1079 if publish_visible else 0)
            if tokenizer:
                assert count(catalog) <= catalog_limits[1] + (226 if publish_visible else 0)

    for route in instruction_limits:
        for adaptive in (False, True):
            asyncio.run(check(route, adaptive))
