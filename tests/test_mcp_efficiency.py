"""Research taint enforcement and bounded MCP policy repetition."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from test_client import local_client

import colony.mcp


@pytest.mark.parametrize("requested", [True, False])
@pytest.mark.parametrize("queued", [True, False])
def test_mcp_research_cannot_downgrade_taint(monkeypatch, tmp_path, requested, queued):
    received = []

    def save(trail):
        received.append(trail)
        return {"stored": not queued, "queued": queued}

    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace(push=save))
    server = colony.mcp.create_mcp(tmp_path)
    result = asyncio.run(
        server.call_tool(
            "push",
            {
                "namespace": "lab",
                "question": "Retention?",
                "content": "17 days.",
                "tainted": requested,
            },
        )
    )
    assert received[0].tainted is True
    receipt = json.loads(result[0].text)
    assert receipt["stored"] is not queued and receipt["queued"] is queued
    if not requested:
        assert receipt["tainted"] is True


@pytest.mark.parametrize(
    "flags",
    [
        {"stored": False, "queued": False, "error": "denied"},
        {"stored": True, "error": "inconsistent receipt"},
    ],
)
def test_taint_enforcement_never_hides_failed_receipts_or_mutates_client_audit(
    monkeypatch, tmp_path, flags
):
    packet = {"id": "audit-id", "warning": "Retain diagnostics.", **flags}
    original = dict(packet)
    received = []

    def save(trail):
        received.append(trail)
        return packet

    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace(push=save))
    result = (
        colony.mcp.create_mcp(tmp_path)
        ._tool_manager.get_tool("push")
        .fn("lab", "Retention?", "17 days.", tainted=False)
    )
    assert received[0].tainted is True and len(received) == 1
    assert json.loads(result[0].text) == {**original, "tainted": True}
    assert packet == original


@pytest.mark.parametrize("offline", [False, True])
def test_mcp_taint_persists_through_scrubbing_storage_and_encrypted_queue(
    team, tmp_path, monkeypatch, offline
):
    app, api, _, admin, key = team
    client = local_client(tmp_path, key, admin)
    monkeypatch.setattr(colony.mcp, "Client", lambda home: client)

    def post(url, **kwargs):
        if offline:
            raise httpx.ConnectError("offline")
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    server = colony.mcp.create_mcp(client.home)
    result = asyncio.run(
        server.call_tool(
            "push",
            {
                "namespace": "team",
                "question": "Retention?",
                "content": "password=synthetic-secret",
                "tainted": False,
            },
        )
    )
    assert json.loads(result[0].text)["tainted"] is True
    if offline:
        path = next((client.home / "queue").glob("*.enc"))
        assert b"synthetic-secret" not in path.read_bytes()
        payload = json.loads(
            Fernet((client.home / "queue.key").read_bytes()).decrypt(path.read_bytes())
        )
        assert payload["trail"]["tainted"] is True
        assert payload["trail"]["content"] == "[REDACTED]"
        offline = False
        assert client.retry() == {"sent": 1, "pending": False}
    with app.state.store.connect() as db:
        row = db.execute("SELECT tainted,content_ref FROM trails").fetchone()
    assert row["tainted"] == 1
    assert app.state.store.read_content(row["content_ref"]) == "[REDACTED]"


@pytest.mark.parametrize("status", [None, "stale", "unverified", "validated"])
def test_receipts_reference_the_complete_initialized_policy_without_repeating_it(
    monkeypatch, tmp_path, status
):
    packet = (
        {"hit": False}
        if status is None
        else {
            "hit": True,
            "status": status,
            "match_kind": "answer",
            "content": "17 days.",
            "sources": [{"ref": "policy.md", "content_hash": "a" * 64}],
            "evidence": [{"ref": "policy.md", "line": 1, "text": "17 days."}]
            if status == "validated"
            else [],
        }
    )
    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(ask=lambda *a, **kw: packet)
    )
    server = colony.mcp.create_mcp(tmp_path)
    assert server._mcp_server.create_initialization_options().instructions == server.instructions
    result = json.loads(server._tool_manager.get_tool("ask").fn("Retention?", "lab")[0].text)
    action = result["next_action" if status == "validated" else "research_guidance"]
    assert len(action.encode("utf-8")) <= 430
    assert "session policy" in action
    # The initialized policy still carries the complete obligations.
    for clause in [
        "Research missing/contradictory facts",
        "without a leading slash",
        "ask's reads/hashes never qualify",
        "actual fingerprint",
        "64 lowercase hexadecimal",
        "stored=true",
        "queued=true",
        "otherwise report the save failure",
        "Keep required steps, conditions, exceptions and source citations",
        "MCP research is always marked tainted",
    ]:
        assert clause in server.instructions
