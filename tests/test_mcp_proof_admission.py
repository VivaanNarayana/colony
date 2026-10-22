"""The compact tool schema must not broaden executed file-proof admission."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import colony.mcp


@pytest.mark.parametrize(
    "proof",
    [
        {},
        {"ref": "policy.py"},
        {"content_hash": "a" * 64},
        {"ref": "", "content_hash": "a" * 64},
        {"ref": "p" * 1025, "content_hash": "a" * 64},
        *[
            {"ref": "policy.py", "content_hash": h}
            for h in ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "\n", 17, None]
        ],
        *[
            {"ref": "policy.py", "content_hash": "a" * 64, "kind": k}
            for k in ["url", "doc", "FILE", None]
        ],
        {"ref": "policy.py", "content_hash": "a" * 64, "symbol": "invented"},
        {"ref": "policy.py", "content_hash": "a" * 64, "contains": "invented"},
        {"ref": "policy.py", "content_hash": "a" * 64, "extra": "ignored?"},
    ],
)
def test_malformed_or_advanced_proofs_never_reach_client(monkeypatch, tmp_path, proof):
    calls = []
    monkeypatch.setattr(
        colony.mcp, "Client", lambda home: SimpleNamespace(push=lambda trail: calls.append(trail))
    )
    server = colony.mcp.create_mcp(tmp_path)
    with pytest.raises(ToolError):
        asyncio.run(
            server.call_tool(
                "push",
                {
                    "namespace": "lab",
                    "question": "Retention?",
                    "content": "Keep archives.",
                    "sources": [proof],
                },
            )
        )
    assert calls == []


@pytest.mark.parametrize("explicit_kind", [False, True])
def test_copied_file_proofs_preserve_exact_values_and_client_taint(
    monkeypatch, tmp_path, explicit_kind
):
    calls = []

    def save(trail):
        calls.append(trail)
        return {"id": trail.id, "stored": True, "duplicate": False}

    monkeypatch.setattr(colony.mcp, "Client", lambda home: SimpleNamespace(push=save))
    proof = {"ref": "docs/règles.py", "content_hash": "0123456789abcdef" * 4}
    if explicit_kind:
        proof["kind"] = "file"
    server = colony.mcp.create_mcp(tmp_path)
    asyncio.run(
        server.call_tool(
            "push",
            {
                "namespace": "lab",
                "question": "Retention?",
                "content": "Keep archives.",
                "sources": [proof],
            },
        )
    )
    assert len(calls) == 1 and calls[0].tainted is True
    assert calls[0].sources[0].ref == proof["ref"]
    assert calls[0].sources[0].content_hash == proof["content_hash"]
    assert calls[0].sources[0].kind == "file"
    assert calls[0].sources[0].symbol is None and calls[0].sources[0].contains is None
