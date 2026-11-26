"""Provider-free real HTTP/MCP transport regression; never invokes a worker model."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team

from colony.client import Client
from colony.models import Trail
from colony.task_mcp import PUBLICATION_CUE, RESEARCH_GUIDANCE
from colony.validation import snapshot


EMPTY_ROUTE = (
    "Memory started empty. Batch likely source reads. Search files for gaps. "
    "Recheck memory on request or after a save."
)


@pytest.mark.parametrize("route", ["empty", "present", "direct", "unknown"])
def test_live_routing_preview_bundle_quota_and_readonly(running_team, route):
    cli, homes, repo, _ = running_team
    (repo / "handler.py").write_text("import settings\nimport missing\n")
    if route == "present":
        saved = cli("--home", homes["a"], "run", "--ns", "team", "--question", "What is the timeout?",
                    "--repo", repo, "--source", "settings.py", "--", sys.executable, "-c", "print('30')")
        assert json.loads(saved.stdout)["stored"]

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
        if route == "direct":
            args += ["--direct-path", "handler.py"]
        if route == "unknown":
            args += ["--no-empty-memory-routing"]
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
            preview = init.instructions[:245]
            if route == "empty":
                assert preview.startswith(EMPTY_ROUTE)
            else:
                lead = "Read the requested path first." if route == "direct" else "Use research when saved findings could help."
                assert preview.startswith(lead + " Read likely files together. Search to fill gaps.")
            assert "Qualify inner limits unless the implementation is selected." in preview
            assert "Count attempts separately from retries and trace exceptional exits." in preview
            assert "allowance: 2 units total" in init.instructions
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"read", "research", "trace_read"}
            assert ("empty at last check" in tools["research"].description) == (route == "empty")
            result = await session.call_tool("trace_read", {"paths": ["handler.py", "settings.py", "missing.py"]})
            assert not result.isError
            value = json.loads(result.content[0].text)
            assert value["read_accounting"] == {"admitted": 2, "denied": 1, "succeeded": 2}
            assert "publication_guidance" not in value
            assert value["results"][2]["evidence_ref"] is None
            denied = await session.call_tool("read", {"path": "settings.py"})
            assert denied.isError

    asyncio.run(check())


@pytest.mark.parametrize("adaptive", [False, True])
def test_compact_task_read_pages_and_lookup_miss(running_team, adaptive):
    _, homes, repo, _ = running_team
    quoted = '  café "one"\nsecond\\line'
    large = "a" * 17000
    for name, content in [("blank.txt", ""), ("quoted.txt", quoted), ("large.txt", large)]:
        (repo / name).write_text(content, encoding="utf-8")
    args = ["-m", "colony.task_mcp", "--home", str(homes["a"]), "--repo", str(repo),
            "--ns", "team", "--question", "Which sources set the timeout?"]
    if adaptive:
        args += ["--context-budget", "8192", "--context-encoding", "utf8_bytes"]
    params = StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))

    def compact(reply):
        assert not reply.isError and reply.structuredContent is None
        assert len(reply.content) == 1 and reply.content[0].type == "text"
        text = reply.content[0].text
        value = json.loads(text)
        assert text == json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value

    async def check():
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            miss = await session.call_tool("research", {})
            if adaptive:
                value = json.loads(miss.content[0].text)
                assert value["context_budget"]["used"] == len(miss.content[0].text.encode())
            else:
                value = compact(miss)
            assert value["hit"] is False
            batch = compact(await session.call_tool("read", {"batch": [
                {"path": path} for path in ("blank.txt", "quoted.txt", "missing.txt", "large.txt")
            ]}))
            assert batch["publication_guidance"] == PUBLICATION_CUE
            blank, quoted_page, error, first = batch["results"]
            assert blank["complete_file"] and blank["content"] == "" and blank["evidence_ref"]
            assert (quoted_page["path"], quoted_page["content"], quoted_page["start_line"],
                    quoted_page["end_line"]) == ("quoted.txt", quoted, 1, 2)
            assert quoted_page["complete_file"] and quoted_page["evidence_ref"]
            assert error == {"error": "Source read failed; adjust path or range", "evidence_ref": None}
            assert not first["complete_file"] and first["next_offset"] == len(first["content"])
            tail = compact(await session.call_tool("read", {"path": "large.txt", "offset": first["next_offset"]}))
            assert first["content"] + tail["content"] == large and tail["next_offset"] is None
            assert "publication_guidance" not in tail
            assert tail["evidence_ref"] and tail["evidence_ref"] != first["evidence_ref"]
            assert (await session.call_tool("read", {"path": "missing.txt"})).isError

    asyncio.run(check())


@pytest.mark.parametrize("adaptive", [False, True])
def test_empty_catalog_and_live_lookup_after_another_client_saves(running_team, adaptive):
    _, homes, repo, _ = running_team
    question = "What is the configured timeout?"
    (repo / "settings.py").write_text("TIMEOUT = 30\nfrom mode import DEFAULT_MODE\n", encoding="utf-8")
    (repo / "mode.py").write_text("DEFAULT_MODE = 'local'\n", encoding="utf-8")
    source = snapshot(repo, "settings.py")
    finding = Trail(id="a" * 32, namespace="team", question=question,
                    content="The configured timeout is 30.", sources=[source])
    args = ["-m", "colony.task_mcp", "--home", str(homes["b"]), "--repo", str(repo),
            "--ns", "team", "--question", question, "--read-only"]
    if adaptive:
        args += ["--context-budget", "8192", "--context-encoding", "utf8_bytes"]
    params = StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))

    async def check():
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            init = await session.initialize()
            assert init.instructions.count(EMPTY_ROUTE) == 1
            catalog = await session.list_tools()
            research = next(t for t in catalog.tools if t.name == "research")
            assert research.description.startswith(
                "Scoped memory was empty at last check. Checks current saved findings for the original task."
            )
            assert RESEARCH_GUIDANCE not in research.description
            page = await session.call_tool("read", {"batch": [{"path": "settings.py", "end": 1}]})
            assert not page.isError
            assert json.loads(page.content[0].text)["results"][0]["evidence_ref"]
            assert EMPTY_ROUTE not in page.content[0].text
            assert (await session.list_tools()).tools == catalog.tools

            assert Client(homes["a"]).push(finding)["stored"]
            response = await session.call_tool("research", {})
            assert not response.isError
            hit = json.loads(response.content[0].text)
            assert hit["hit"] and hit["status"] == "validated" and hit["content"] == finding.content
            assert any(s["ref"] == source.ref and s["content_hash"] == source.content_hash
                       for s in hit["sources"])
            restored = next(t for t in (await session.list_tools()).tools if t.name == "research")
            assert restored.description.startswith(RESEARCH_GUIDANCE)
            assert restored.inputSchema == research.inputSchema
            suffix = restored.description.removeprefix(RESEARCH_GUIDANCE)
            assert ("max_context_tokens" in suffix) == adaptive
            assert research.description.endswith(suffix)
            if not adaptive:
                assert hit["evidence"] and all(not span.get("complete_file") for span in hit["evidence"])
                assert all(span["ref"] == "settings.py" for span in hit["evidence"])
                assert all("'local'" not in span["text"] for span in hit["evidence"])
                gap = await session.call_tool("read", {"path": "mode.py"})
                observed = json.loads(gap.content[0].text)
                assert (observed["start_line"], observed["end_line"], observed["content"]) == (
                    1, 1, "DEFAULT_MODE = 'local'")

    asyncio.run(check())
