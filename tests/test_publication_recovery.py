"""Source rejection is atomic; an explicit corrected push preserves every binding."""
import asyncio
import copy
import hashlib

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_compact_delivery import transport as transport

from colony import mcp
from colony.models import ContextBudget


@pytest.mark.parametrize("workflow", [False, True])
def test_invalid_source_then_explicit_correction_keeps_all_sources(transport, workflow):
    client, repo, database, _ = transport
    sources = []
    for name in ("config.py", "caller.py", "loop.py"):
        path = repo / name
        path.write_text("VALUE = 100\n", encoding="utf-8")
        sources.append({"ref": name, "content_hash": hashlib.sha256(path.read_bytes()).hexdigest()})
    options = {"workflow_namespace": "team", "workflow_repo": repo} if workflow else {}
    server = mcp.create_mcp(client.home, context_budget=ContextBudget(max_tokens=2048), **options)
    payload = {"namespace": "team", "question": "Explain config propagation",
               "content": "Config is passed by caller into loop.", "sources": sources}
    bad = copy.deepcopy(payload)
    bad["sources"][1]["content_hash"] = "private-value-injection"
    frozen = copy.deepcopy(bad)
    with pytest.raises(ToolError) as caught:
        asyncio.run(server.call_tool("push", bad))
    assert "sources[1].content_hash" in str(caught.value)
    assert "no publication attempted" in str(caught.value)
    assert "dependent claims" in str(caught.value)
    assert "private-value-injection" not in str(caught.value)
    assert bad == frozen
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM trails").fetchone()[0] == 0
    asyncio.run(server.call_tool("push", payload))
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM trails").fetchone()[0] == 1
        actual = {r["ref"]: r["content_hash"] for r in db.execute("SELECT ref,content_hash FROM trail_sources")}
    assert actual == {s["ref"]:s["content_hash"] for s in sources}
    assert payload["sources"] == sources


def test_unknown_publication_failure_never_retries(transport, monkeypatch):
    client, repo, _, _ = transport
    calls = []
    def fail(self, trail, **kwargs):
        calls.append(trail)
        raise RuntimeError("Unknown outcome")
    monkeypatch.setattr(mcp.Client, "push", fail)
    server = mcp.create_mcp(client.home, context_budget=ContextBudget(max_tokens=2048))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("push", {"namespace":"team", "question":"q", "content":"finding"}))
    assert len(calls) == 1
    # Error handling does not add a completion gate or disable ordinary lookups.
    assert asyncio.run(server.call_tool("ask", {"question":"unrelated"}))
