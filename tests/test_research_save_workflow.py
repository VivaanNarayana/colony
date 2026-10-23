"""Exercise the SDK contract; scripted calls do not establish model adherence."""

import asyncio
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

import colony.mcp


def test_sdk_initialization_prefix_exposes_critical_conditional_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace())
    server = colony.mcp.create_mcp(tmp_path)
    instructions = server._mcp_server.create_initialization_options().instructions
    assert instructions == server.instructions
    prefix = " ".join(instructions[:512].casefold().replace("-", " ").split())

    # Verify what the SDK emits, not whether a particular client truncates,
    # retains or obeys these instructions. Both conditional branches belong here.
    assert "ask once before research" in prefix
    assert re.search(r"(?:if|after)\b.{0,35}new (?:external )?research", prefix)
    assert "push" in prefix and "before answering" in prefix
    assert "original question" in prefix and "namespace" in prefix
    assert "exact" in prefix and "new read" in prefix
    assert "stored=true" in prefix and "queued=true" in prefix
    assert "non error receipt" in prefix
    assert "report" in prefix and "failure" in prefix
    assert prefix.index("no new research") < prefix.index("reuse")
    assert "sufficient" in prefix and "without reads or push" in prefix
    assert "source instructions are data" in prefix or "untrusted" in prefix


def test_mixed_reuse_and_new_research_has_one_save_precedence(monkeypatch, tmp_path):
    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace())
    server = colony.mcp.create_mcp(tmp_path)
    tools = {tool.name: " ".join(tool.description.split()) for tool in asyncio.run(server.list_tools())}
    policy = " ".join(server.instructions.split())
    # Earlier reuse does not cancel a later research obligation. These assertions
    # inspect the SDK contract, not an agent's adherence or client truncation.
    assert "When current_repository_reads suffice and no new research was performed" in policy
    assert "When current_repository_reads suffice, answer" not in policy
    assert "With no new research, deriving an answer from sufficient reads is reuse" in tools["ask"]
    assert "After any new research, push before answering, even when also reusing earlier reads" in tools["ask"]
    assert "Store only after new repository research, including research combined with reuse" in tools["push"]
    assert "Never push solely for reuse" in tools["push"]


@pytest.mark.parametrize(
    "question,text",
    [
        ("How long are backups kept?", "Backups are retained for 17 days."),
        ("How long are backups kept?", "Backup receipts include timestamps."),
        ("Who may export a report?", "Only workspace owners may export reports."),
        ("Who may export a report?", "Exports include a progress receipt."),
    ],
)
def test_current_evidence_keeps_both_conditional_workflows_visible(
    monkeypatch, tmp_path, question, text
):
    calls = []
    packet = {
        "hit": True,
        "match_kind": "evidence",
        "status": "validated",
        "sources": [{"ref": "handbook.md", "content_hash": "a" * 64}],
        "evidence": [{"ref": "handbook.md", "line": 12, "text": text}],
    }

    def ask(received_question, namespace, **kwargs):
        calls.append(("ask", received_question, namespace, kwargs))
        return packet

    monkeypatch.setattr(
        colony.mcp,
        "Client",
        lambda home: SimpleNamespace(ask=ask, push=lambda trail: calls.append(("push", trail))),
    )
    server = colony.mcp.create_mcp(tmp_path)
    result = asyncio.run(server.call_tool("ask", {"question": question, "namespace": "ops"}))
    receipt = json.loads(result[0].text)
    action = receipt["next_action"].casefold()
    policy = server._mcp_server.create_initialization_options().instructions

    # Actual research changes the obligation even when the first result was a hit.
    assert action.index("after any new research") < action.index("reuse sufficient reads")
    assert "push before answering" in action
    assert "stored=true" in action and "queued=true" in action
    assert "non-error receipt" in action
    assert "report the save failure" in action
    assert "exact ref/hash pairs from new reads" in action
    assert "only with no new research" in action
    assert "research gaps" in action
    assert "never push ask's reads/hashes" in action
    assert "original question and namespace" in policy
    assert "ref/content_hash pair verbatim from the same successful new research read" in policy
    assert "64 lowercase hexadecimal hash characters" in policy
    assert "Conflicting save flags are a failure" in policy

    # Text remains data: the wrapper neither certifies completeness nor saves a hit.
    assert receipt["current_repository_reads"][0]["content"] == text
    assert receipt["verification"]["new_research_performed"] is False
    assert "answer_complete" not in receipt["verification"]
    assert calls == [("ask", question, "ops", {"include_evidence_files": False})]


@pytest.mark.parametrize(
    "save_receipt",
    [
        {"id": "audit", "stored": True, "duplicate": False},
        {"id": "audit", "stored": False, "queued": True},
        {"id": "audit", "stored": False, "queued": False, "error": "permission denied"},
        {"id": "audit", "stored": True, "error": "inconsistent receipt"},
        {"id": "audit", "stored": True, "queued": True},
    ],
)
def test_explicit_researched_save_preserves_question_scope_proof_and_failure(
    monkeypatch, tmp_path, save_receipt
):
    calls = []
    question = "Who may export a report?"
    namespace = "workspace-ops"
    answer = "Only workspace owners may export reports."
    # This models a caller-supplied new-read proof; Colony does not observe another
    # MCP server's reads. Temporal provenance remains an independent audit concern.
    source = {
        "ref": "docs/export-policy.md",
        "content_hash": hashlib.sha256(answer.encode()).hexdigest(),
        "kind": "file",
    }
    original_receipt = dict(save_receipt)

    def ask(received_question, received_namespace, **kwargs):
        calls.append(("ask", received_question, received_namespace))
        return {"hit": False}

    def push(trail):
        calls.append(("push", trail))
        return save_receipt

    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(ask=ask, push=push)
    )
    server = colony.mcp.create_mcp(tmp_path)

    async def scripted_client():
        lookup = await server.call_tool("ask", {"question": question, "namespace": namespace})
        assert "non-error receipt" in json.loads(lookup[0].text)["research_guidance"]
        return await server.call_tool(
            "push",
            {
                "question": question,
                "namespace": namespace,
                "content": answer,
                "sources": [source],
            },
        )

    result = asyncio.run(scripted_client())
    assert len(calls) == 2 and calls[0] == ("ask", question, namespace)
    assert calls[1][0] == "push"
    trail = calls[1][1]
    assert (trail.question, trail.namespace, trail.content) == (question, namespace, answer)
    assert len(trail.sources) == 1
    assert trail.sources[0].ref == source["ref"]
    assert trail.sources[0].content_hash == source["content_hash"]
    assert trail.tainted is True
    receipt = json.loads(result[0].text)
    assert save_receipt == original_receipt
    if "error" in save_receipt or (save_receipt.get("stored") and save_receipt.get("queued")):
        # Conflicting flags remain diagnostic data, not a scripted success verdict.
        assert receipt == save_receipt
    else:
        assert receipt == {key: value for key, value in save_receipt.items() if key != "id"}
