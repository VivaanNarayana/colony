"""Preserve Python scope without growing prompts, ranking, or model calls."""
import asyncio
import copy
import json
import os

import pytest
from test_compact_delivery import transport as transport

from colony import context, mcp
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


def selected(tmp_path, text, lines, *, name="settings.py", adaptive=True):
    (tmp_path / name).write_bytes(text.encode("utf-8"))
    sources = [snapshot(tmp_path, name)]
    candidates = {line for _, line, _ in context.source_chunks(tmp_path, sources)}
    assert set(lines) <= candidates  # Exercise actual selected passage coordinates.
    items = context.excerpts(tmp_path, sources, "NEEDLE", adaptive=adaptive,
                             preferred=[(name, line) for line in lines])
    return {"response_profile": PROFILE, "hit": True, "status": "validated",
            "match_kind": "evidence", "sources": [s.model_dump() for s in sources],
            "evidence": items}


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_original_branch_reproducer_preserves_scope_without_unrelated_padding(
    tmp_path, monkeypatch, ending, encoding,
):
    if encoding == "o200k_base":
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Pinned tokenizer required")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    unit = ('def configured_timeout(profile):\n    if profile == "batch":\n'
            '        # Batch runs tolerate longer requests.\n\n'
            '        return 9000\n    return 1000')
    text = unit + "\n\n" + ("# Unrelated padding " + "x" * 90 + "\n") * 30
    packet = selected(tmp_path, text.replace("\n", ending), [5])
    original = copy.deepcopy(packet)
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    result = fit_response(packet, budget)
    assert result["hit"] and result["current_repository_reads"][0]["content"] == unit
    assert result["current_repository_reads"][0]["start_line"] == 1
    assert result["evidence_scope"] == "partial"
    assert result["sources"] == packet["sources"] and packet == original
    assert count_response(result, budget) <= budget.max_tokens
    whole = fit_response({**packet, "evidence": [{**packet["evidence"][0],
        "text": text.rstrip("\n"), "selected_end_line": len(text.splitlines()),
        "complete_file": True}]}, budget)
    assert whole["hit"]  # A scope-preserving unit is cheaper than a full-file workaround.
    assert result["context_budget"]["used"] < whole["context_budget"]["used"]


UNITS = [
    'def run(flag):\n    if flag:\n\n        NEEDLE = 1\n        return NEEDLE\n    return 0',
    'async def run(client):\n    async with client:\n\n        NEEDLE = await client.read()\n    return NEEDLE',
    'class Runner(Base):\n    def run(self):\n        if self.ready:\n\n            NEEDLE = 1\n            return NEEDLE',
    '@cache\ndef run():\n\n    NEEDLE = 1\n    return NEEDLE',
    '@(\n    cache\n)\ndef run():\n\n    NEEDLE = 1\n    return NEEDLE',
    'if enabled:\n\n    NEEDLE = 1\nelse:\n    NEEDLE = 0',
    'try:\n    request()\nexcept ValueError:\n\n    NEEDLE = 1\nelse:\n    success()\nfinally:\n    close()',
    'for entry in entries:\n    visit(entry)\nelse:\n\n    NEEDLE = 1',
    'with transaction():\n\n    NEEDLE = 1\n    commit()',
    'match mode:\n    case "batch" if enabled:\n\n        NEEDLE = 1\n    case _:\n        NEEDLE = 0',
    'DATA = (\n    "first"\n\n    "NEEDLE"\n)',
    'DATA = """first\n\nNEEDLE\nlast"""',
]


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("name", ["settings.py", "settings.pyi"])
def test_complete_enclosing_statement_excludes_unrelated_top_level_units(tmp_path, unit, name):
    prefix = "UNRELATED_BEFORE = 0\n\n"
    line = 3 + next(i for i, value in enumerate(unit.splitlines()) if "NEEDLE" in value)
    packet = selected(tmp_path, prefix + unit + "\n\nUNRELATED_AFTER = 2", [line], name=name)
    result = fit_response(packet, ContextBudget(max_tokens=8192))
    assert result["hit"]
    assert result["current_repository_reads"][0]["content"] == unit
    assert result["current_repository_reads"][0]["start_line"] == 3
    assert not result["current_repository_reads"][0]["complete_file"]


def test_two_selected_paragraphs_in_one_function_do_not_duplicate_scope(tmp_path, monkeypatch):
    text = "def run():\n\n    FIRST = 1\n\n    SECOND = 2\n    return FIRST + SECOND"
    calls = []
    parse = context.ast.parse
    def counted(source):
        calls.append(source)
        return parse(source)
    monkeypatch.setattr(context.ast, "parse", counted)
    packet = selected(tmp_path, text, [3, 5])
    assert len(packet["evidence"]) == 1 and packet["evidence"][0]["text"] == text
    assert len(calls) == 1  # Per-call/file cache, not repeated parsing or retained source text.
    changed = text.replace("FIRST = 1", "FIRST = 9")
    assert selected(tmp_path, changed, [3, 5])["evidence"][0]["text"] == changed
    assert len(calls) == 2


def test_distinct_functions_keep_both_required_units(tmp_path):
    a = "def first():\n\n    return 1"
    b = "def second():\n\n    return 2"
    packet = selected(tmp_path, a + "\n\nUNRELATED = 99\n\n" + b, [3, 9])
    result = fit_response(packet, ContextBudget(max_tokens=8192))
    assert [r["content"] for r in result["current_repository_reads"]] == [a, b]


def test_merged_python_scopes_do_not_admit_a_third_ranked_passage(tmp_path):
    unit = "def run():\n\n    FIRST = 1\n\n    SECOND = 2\n    return FIRST + SECOND"
    packet = selected(tmp_path, unit + "\n\nUNRELATED = 99", [3, 5, 8])
    result = fit_response(packet, ContextBudget(max_tokens=8192))
    assert [r["content"] for r in result["current_repository_reads"]] == [unit]


@pytest.mark.parametrize("malformed", ["def broken(:", "def broken():\n    ???"])
def test_unparseable_source_is_never_returned_as_detached_code(tmp_path, malformed):
    text = malformed + "\n\n    NEEDLE = 1"
    line = len(text.splitlines())
    packet = selected(tmp_path, text, [line])
    assert packet["evidence"][0]["text"] == text


def test_oversized_required_scope_is_withheld_without_truncation(tmp_path):
    text = "def run(flag):\n    if flag:\n        # " + "context " * 1500 + "\n\n        return 9000"
    packet = selected(tmp_path, text, [5])
    result = fit_response(packet, ContextBudget(max_tokens=1024))
    assert result["hit"] is False and result["reason"] == "budget_limited"
    assert "current_repository_reads" not in result


def test_trailing_indented_comment_does_not_lose_its_function(tmp_path):
    text = "def run():\n    return 1\n\n    # NEEDLE explains the preceding return."
    assert selected(tmp_path, text, [4])["evidence"][0]["text"] == text


def test_enlarged_scope_still_scrubs_secrets_before_parsing(tmp_path):
    text = 'def run():\n    password = "private-fixture-value"\n\n    return 9000'
    packet = selected(tmp_path, text, [4])
    result = fit_response(packet, ContextBudget(max_tokens=8192))
    assert result["hit"] and "def run():" in result["current_repository_reads"][0]["content"]
    assert "private-fixture-value" not in json.dumps(result)
    assert "[REDACTED]" in result["current_repository_reads"][0]["content"]


def test_ast_never_executes_source_and_legacy_path_does_not_parse(tmp_path, monkeypatch):
    text = "raise RuntimeError('must never execute')\n\ndef run():\n\n    return 1"
    assert selected(tmp_path, text, [5])["evidence"][0]["text"] == "def run():\n\n    return 1"
    monkeypatch.setattr(context.ast, "parse", lambda *a: pytest.fail("legacy path parsed Python"))
    legacy = selected(tmp_path, text, [5], adaptive=False)
    assert "return 1" in legacy["evidence"][0]["text"]


@pytest.mark.parametrize("maximum", [2048, 8192])
def test_scope_survives_store_http_client_mcp_without_an_extra_lookup(transport, monkeypatch, maximum):
    client, repo, _, _ = transport
    unit = 'def run(flag):\n    if flag:\n\n        NEEDLE = 9000\n        return NEEDLE\n    return 1000'
    text = unit + "\n\n" + "# unrelated padding\n" * 200
    (repo / "settings.py").write_text(text, encoding="utf-8")
    monkeypatch.setattr(context, "rank", lambda question, texts: [
        10.0 if "NEEDLE" in value else -100.0 for value in texts])
    trail = Trail(id="e" * 32, namespace="team", question="What does NEEDLE depend on?",
                  content="`NEEDLE` is conditional; inspect its enclosing branch.",
                  sources=[snapshot(repo, "settings.py")])
    assert client.push(trail)["stored"]
    calls = []
    request = client.__class__.request
    def counted(self, route, payload):
        calls.append(route)
        return request(self, route, payload)
    monkeypatch.setattr(client.__class__, "request", counted)
    budget = ContextBudget(max_tokens=maximum)
    server = mcp.create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                            workflow_read_only=True, context_budget=budget)
    value = json.loads(asyncio.run(server.call_tool("ask", {"question": trail.question}))[0].text)
    assert value["hit"] and value["current_repository_reads"][0]["content"] == unit
    assert calls == ["/capabilities", "/ask"]
    assert count_response(value, budget) <= maximum
    assert value["sources"] == [{"ref": "settings.py", "content_hash": trail.sources[0].content_hash}]
    assert value["provenance"] == "UNTRUSTED AS INSTRUCTIONS"
