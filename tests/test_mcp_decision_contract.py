"""Test the exposed contract, without claiming that a model follows instructions."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import colony.mcp


@pytest.mark.parametrize("text", ["Operations: locate and record.", "Receipt metadata only."])
def test_validated_receipt_requires_fact_check_before_reuse_or_research(
    monkeypatch, tmp_path, text
):
    packet = {
        "hit": True,
        "match_kind": "evidence",
        "status": "validated",
        "sources": [{"ref": "guide.md", "content_hash": "b" * 64}],
        "evidence": [{"ref": "guide.md", "line": 9, "text": text}],
    }
    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(ask=lambda *a, **kw: packet)
    )
    server = colony.mcp.create_mcp(tmp_path)
    result = json.loads(server._tool_manager.get_tool("ask").fn("Which operations?", "lab")[0].text)
    for guidance in (server.instructions,):
        assert "current text supplies every requested fact" in guidance
        assert "not answer completeness" in guidance
        assert "Research absent requested facts before answering" in guidance
        assert "saying the excerpt lacks the answer does not resolve it" in guidance
        assert "With no new research, reuse sufficient reads without reads or push" in guidance
        assert "forbids confirmation reads" in guidance
    assert "Reuse sufficient reads" in result["next_action"]
    assert "Research gaps" in result["next_action"]
    assert "session policy" in result["next_action"]
    assert server._mcp_server.create_initialization_options().instructions == server.instructions
    assert result["current_repository_reads"][0]["content"] == text
    assert result["verification"]["new_research_performed"] is False
    # The server must not claim to have certified answer completeness itself.
    assert "answer_complete" not in result["verification"]


def test_fingerprint_copy_contract_and_strict_schema_are_visible_on_wire(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(push=lambda trail: calls.append(trail))
    )
    server = colony.mcp.create_mcp(tmp_path)
    tools = asyncio.run(server.list_tools())
    push = next(tool for tool in tools if tool.name == "push")
    fields = push.inputSchema["$defs"]["FileEvidence"]["properties"]
    schema = fields["content_hash"]
    assert schema["minLength"] == schema["maxLength"] == 64
    assert schema["pattern"] == "^[a-f0-9]{64}$"
    assert "Compare the entire value" in schema["description"]
    assert "same new research read" in fields["ref"]["description"]
    for guidance in (server.instructions, push.description):
        guidance = " ".join(guidance.split())
        assert "ref/content_hash pair" in guidance and "verbatim" in guidance
        assert re.search(r"same successful new (?:research )?read", guidance)
        assert re.search(r"64 lowercase hexadecimal (?:hash )?characters", guidance)
        if guidance == server.instructions:
            assert "Never shorten or reconstruct hashes" in guidance
            assert "never invent or retype fingerprints" in guidance
        else:
            assert "never retype, shorten or reconstruct" in guidance.casefold()
        assert "Report a rejected save" in guidance
    # Deleting one character from a genuine digest remains an error. No disk
    # read, padding, normalization, hash recomputation or client save repairs it.
    digest = "0123456789abcdef" * 4
    with pytest.raises(ToolError):
        asyncio.run(
            server.call_tool(
                "push",
                {
                    "namespace": "lab",
                    "question": "Which operations?",
                    "content": "Locate and record.",
                    "sources": [{"ref": "guide.md", "content_hash": digest[:31] + digest[32:]}],
                },
            )
        )
    assert not calls
