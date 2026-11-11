"""Budget discovery and delivery controls; no native model or savings claims."""

import asyncio
import copy
import hashlib
import json
import os

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_context_budget_interfaces import client as client

from colony import mcp
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget


@pytest.fixture(params=["utf8_bytes", "o200k_base"])
def encoding(request, monkeypatch):
    if request.param == "o200k_base":
        pytest.importorskip("tiktoken")
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Set COLONY_TEST_CONTEXT_TOKENIZER for pinned o200k checks")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    return request.param


def bundle():
    # Generic multi-file current evidence, independent of evaluator fixtures.
    texts = {
        "defaults.py": "DEFAULT = 17\n",
        "overrides.py": "from defaults import DEFAULT\nACTIVE = DEFAULT + 4\n",
        "handler.py": "from overrides import ACTIVE\ndef handle(run):\n    return run(ACTIVE)\n",
        "worker.py": "def run(limit):\n    return sum(range(limit))\n",
    }
    sources, reads = [], []
    for path, content in texts.items():
        digest = hashlib.sha256(content.encode()).hexdigest()
        sources.append({"ref": path, "content_hash": digest})
        reads.append({"path": path, "sha256": digest, "start_line": 1,
                      "content": content.rstrip("\n"), "complete_file": True})
    return {"response_profile": PROFILE, "hit": True, "status": "validated",
            "match_kind": "evidence", "sources": sources, "current_repository_reads": reads,
            "tainted": True, "memory": {"state": "active", "revision": 0}}


def ask(server, **arguments):
    result = asyncio.run(server.call_tool("ask", {"question": "How is the limit used?",
                                                   **arguments}))
    assert len(result) == 1  # One text payload, no duplicate structured evidence.
    return json.loads(result[0].text)


@pytest.mark.parametrize("maximum", [256, 2048, 8192])
def test_discovery_reports_actual_connection_budget_without_changing_schema(
    client, tmp_path, encoding, maximum,
):
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget(
        max_tokens=maximum, encoding=encoding,
    ))
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "ask")
    units = "UTF-8 bytes (estimate)" if encoding == "utf8_bytes" else "o200k_base tokens"
    assert f"default {maximum} {units}" in tool.description
    assert "caps the full response" in tool.description
    assert "override 256..8192 within your allowance" in tool.description
    assert "Size for needed sources before lookup" in tool.description
    assert "budget_limited isn't proof of no match; no automatic retry" in tool.description
    assert tool.inputSchema["required"] == ["question"]
    override = tool.inputSchema["properties"]["max_context_tokens"]
    assert override["default"] is None
    integer = next(x for x in override["anyOf"] if x.get("type") == "integer")
    assert integer == {"minimum": 256, "maximum": 8192, "type": "integer"}
    assert not client[0]  # Discovery never starts research or lookup.


def test_explicit_override_delivers_all_cores_and_does_not_change_default(
    client, tmp_path, encoding,
):
    original = bundle()
    client[1].update(original)
    budget = ContextBudget(max_tokens=256, encoding=encoding)
    server = mcp.create_mcp(tmp_path, context_budget=budget)
    small = ask(server)
    assert small["hit"] is False and small["reason"] == "budget_limited"
    assert len(client[0]) == 1  # No automatic retry/escalation after a bounded miss.
    large = ask(server, max_context_tokens=8192)
    assert large["hit"] and large["evidence_scope"] == "cited_files"
    assert large["current_repository_reads"] == original["current_repository_reads"]
    assert large["sources"] == original["sources"]
    assert "content" not in large  # No saved prose substituted for source evidence.
    again = ask(server)
    assert again == small
    assert len(client[0]) == 3
    assert [c[2]["context_budget"].max_tokens for c in client[0]] == [256, 8192, 256]
    assert all(c[2]["context_budget"].encoding == encoding for c in client[0])
    assert budget.max_tokens == 256 and client[1] == original
    for result, maximum in [(small, 256), (large, 8192), (again, 256)]:
        used = count_response(result, ContextBudget(max_tokens=maximum, encoding=encoding))
        assert result["context_budget"]["used"] == used <= maximum


def test_default_units_can_change_fit_without_changing_evidence(client, tmp_path, encoding):
    packet = bundle()
    for name, text in {"limits.py": "TIMEOUT = 31", "adapter.py": "def send(x):\n    return x"}.items():
        digest = hashlib.sha256(text.encode()).hexdigest()
        packet["sources"].append({"ref": name, "content_hash": digest})
        packet["current_repository_reads"].append({
            "path": name, "sha256": digest, "start_line": 1,
            "content": text, "complete_file": True,
        })
    client[1].update(packet)
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget(encoding=encoding))
    default = ask(server)
    assert default["context_budget"]["max_tokens"] == 2048
    assert len(client[0]) == 1
    # Identical current evidence can exceed 2048 bytes while fitting 2048 tokens.
    assert default["hit"] is (encoding == "o200k_base")
    if not default["hit"]:
        assert default["reason"] == "budget_limited"
    sufficient = ask(server, max_context_tokens=8192)
    assert sufficient["hit"] and sufficient["evidence_scope"] == "cited_files"
    assert sufficient["current_repository_reads"] == packet["current_repository_reads"]
    assert sufficient["sources"] == packet["sources"]
    assert sufficient["context_budget"]["used"] == count_response(
        sufficient, ContextBudget(max_tokens=8192, encoding=encoding),
    ) <= 8192
    assert len(client[0]) == 2


@pytest.mark.parametrize("invalid", [255, 8193, "4096", True, 4096.0])
def test_override_rejection_occurs_before_lookup(client, tmp_path, invalid):
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    with pytest.raises(ToolError):
        ask(server, max_context_tokens=invalid)
    assert not client[0]


def test_exact_multi_file_boundary_never_silently_drops_selected_cores(encoding):
    original = bundle()
    # Fix the limited flag so true/false JSON length cannot shift the boundary.
    original["context_budget"] = {"limited": True}
    frozen = copy.deepcopy(original)
    maximum = 8192
    for _ in range(5):
        fitted = fit_response(original, ContextBudget(max_tokens=maximum, encoding=encoding))
        assert fitted["hit"]
        used = fitted["context_budget"]["used"]
        if used == maximum:
            break
        maximum = used
    assert maximum == used and 256 < maximum < 8192
    assert fitted["current_repository_reads"] == original["current_repository_reads"]
    too_small = fit_response(original, ContextBudget(max_tokens=maximum - 1, encoding=encoding))
    assert not too_small["hit"] and too_small["reason"] == "budget_limited"
    assert "current_repository_reads" not in too_small
    assert too_small["context_budget"]["used"] <= maximum - 1
    assert original == frozen
