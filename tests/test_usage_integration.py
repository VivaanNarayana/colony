import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team

from colony.usage import UsageJournal


def test_real_cli_http_stdio_usage_review_disable_and_purge(running_team):
    cli, homes, repo, directory = running_team
    home = homes["b"]
    assert json.loads(cli("--home", home, "usage", "enable").stdout)["enabled"]
    trail = directory / "finding.json"
    trail.write_text(
        json.dumps(
            {
                "id": "c" * 32,
                "question": "Build timeout configuration",
                "content": "The build timeout is TIMEOUT = 30 in settings.py.",
            }
        )
    )
    assert json.loads(
        cli(
            "--home",
            homes["a"],
            "push",
            trail,
            "--ns",
            "team",
            "--repo",
            repo,
            "--source",
            "settings.py",
        ).stdout
    )["stored"]
    assert json.loads(
        cli(
            "--home",
            home,
            "ask",
            "Build timeout configuration",
            "--ns",
            "team",
        ).stdout
    )["hit"]

    async def lookup():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-I", "-m", "colony.cli", "--home", str(home), "mcp"],
            env=dict(os.environ),
        )
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "ask",
                {
                    "question": "Build timeout configuration",
                    "namespace": "team",
                },
            )
            assert not result.isError
            assert json.loads(result.content[0].text)["hit"]

    asyncio.run(asyncio.wait_for(lookup(), timeout=30))
    report = json.loads(cli("--home", home, "report", "--format", "json").stdout)
    assert report["lookups"]["answer_hits"] == 2
    assert report["quality"]["unassessed_hits"] == 2
    event_id = report["recent_lookup_metadata"][0]["event_id"]
    cli("--home", home, "usage", "review", event_id, "--verdict", "useful")
    reviewed = json.loads(cli("--home", home, "report", "--format", "json").stdout)
    assert reviewed["quality"]["assessed_hits"] == 1
    assert reviewed["quality"]["self_reported_usefulness"]["denominator"] == 1
    assert reviewed["model_usage"]["token_savings"] is None
    assert "Build timeout" not in json.dumps(reviewed)
    assert not (homes["a"] / "usage").exists()
    cli("--home", home, "usage", "disable")
    cli("--home", home, "ask", "Build timeout configuration", "--ns", "team")
    assert len(UsageJournal(home).events()) == 2
    assert json.loads(cli("--home", home, "usage", "purge").stdout)["purged"] == 2
    assert UsageJournal(home).events() == []
