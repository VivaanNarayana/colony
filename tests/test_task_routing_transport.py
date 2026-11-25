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


@pytest.mark.parametrize("route", ["empty", "present", "direct"])
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
            lead = {
                "empty": "Memory was empty at task startup.",
                "present": "Use research when saved findings could help.",
                "direct": "Read the requested path first.",
            }[route]
            assert preview.startswith(lead + " Read likely files together. Search to fill gaps.")
            assert ("Explicit research remains available" in init.instructions) == (route == "empty")
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
