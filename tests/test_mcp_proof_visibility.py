"""Proof-copy visibility and SDK rejection, without a model-adherence claim."""

import asyncio
import hashlib
import json
import re
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import colony.mcp


def normalized(text):
    return " ".join(text.casefold().replace("-", " ").split())


def test_full_proof_copy_rule_is_early_in_initialized_and_push_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace())
    server = colony.mcp.create_mcp(tmp_path)
    initialized = server._mcp_server.create_initialization_options().instructions
    push = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "push")

    # The SDK exposes both strings. This does not establish how any particular
    # client renders them or whether a subject obeys the instructions.
    for prefix in (initialized[:512], push.description[:512]):
        prefix = normalized(prefix)
        assert re.search(r"(?:all|full) 64 lowercase hexadecimal", prefix)
        assert "verbatim" in prefix and re.search(r"new (?:research )?read", prefix)
        assert "shorten" in prefix and "reconstruct" in prefix

    initialized_prefix = normalized(initialized[:512])
    assert "repository relative paths" in initialized_prefix
    assert "without a leading slash" in initialized_prefix

    policy = normalized(initialized)
    assert "do not repeat rejected arguments" in policy
    assert "reread" in policy


def test_miss_repeats_complete_proof_requirement_within_receipt_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(
        colony.mcp,
        "Client",
        lambda home: SimpleNamespace(ask=lambda *args, **kwargs: {"hit": False}),
    )
    server = colony.mcp.create_mcp(tmp_path)
    result = asyncio.run(
        server.call_tool(
            "ask", {"question": "How often are archive keys rotated?", "namespace": "ops"}
        )
    )
    guidance = json.loads(result[0].text)["research_guidance"]
    assert len(guidance.encode("utf-8")) <= 430
    assert re.search(r"(?:all|full) 64", normalized(guidance))
    assert "new read" in normalized(guidance)
    assert "stored=true" in guidance and "queued=true" in guidance
    assert "non-error receipt" in guidance


def test_repeated_40_character_prefix_never_reaches_client(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(push=lambda trail: calls.append(trail))
    )
    server = colony.mcp.create_mcp(tmp_path)
    digest = hashlib.sha256(b"Archive keys rotate every 29 days.").hexdigest()
    shortened = digest[:40]
    arguments = {
        "namespace": "ops",
        "question": "How often are archive keys rotated?",
        "content": "Archive keys rotate every 29 days.",
        "sources": [{"ref": "policies/archive-keys.md", "content_hash": shortened}],
        "tainted": False,
    }

    # Script the repeated mistake explicitly: rejection is deterministic, not a
    # test that an agent chooses to stop retrying or copies the full proof next.
    for _ in range(2):
        with pytest.raises(ToolError, match="64"):
            asyncio.run(server.call_tool("push", arguments))
        assert calls == []
        assert arguments["sources"][0]["content_hash"] == shortened
    assert len(digest) == 64 and len(shortened) == 40
