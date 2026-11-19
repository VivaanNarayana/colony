"""Evidence priority and explicit budget choices, without model sessions."""
import asyncio
import copy
import json
import os
import subprocess

import pytest
from test_compact_delivery import transport as transport

from colony.cli import _context_budget, parser
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.mcp import create_mcp
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_saved_prose_cannot_displace_complete_source(encoding, monkeypatch):
    if encoding == "o200k_base":
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Pinned tokenizer required")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    value = {
        "response_profile": PROFILE, "hit": True, "status": "validated",
        "match_kind": "answer", "content": "Saved interpretation. " * 3,
        "tainted": True, "memory": {"state": "active", "revision": 2},
        "sources": [{"ref": name, "content_hash": digest * 64}
                    for name, digest in (("config.py", "a"), ("caller.py", "b"))],
        "evidence": [
            {"ref": "config.py", "line": 1, "text": "# Scope café " * 30 + "\nBASE = 730\nVALUE = BASE",
             "selected_line": 3, "selected_end_line": 3, "complete_file": True},
            {"ref": "caller.py", "line": 1, "text": "run(VALUE)",
             "selected_line": 1, "selected_end_line": 1, "complete_file": True},
        ],
    }
    original = copy.deepcopy(value)
    roomy = ContextBudget(max_tokens=8192, encoding=encoding)
    evidence = fit_response({**value, "match_kind": "evidence"}, roomy)
    budget = ContextBudget(max_tokens=max(256, evidence["context_budget"]["used"]), encoding=encoding)
    answer = fit_response(value, budget)
    assert answer["hit"] and answer["match_kind"] == "evidence" and "content" not in answer
    assert answer["current_repository_reads"] == evidence["current_repository_reads"]
    assert all(r["complete_file"] for r in answer["current_repository_reads"])
    assert answer["sources"] == value["sources"] and answer["memory"] == value["memory"]
    assert answer["tainted"] and answer["context_budget"]["limited"]
    assert count_response(answer, budget) <= budget.max_tokens
    assert value == original
    assert fit_response(answer, budget) == answer
    # Saved interpretations remain available when they do not displace evidence.
    full = fit_response(value, roomy)
    assert full["content"] == value["content"] and full["match_kind"] == "answer"
    assert full["current_repository_reads"] == evidence["current_repository_reads"]
    # Unprofiled interfaces retain their original answer-first ordering.
    legacy = {k: v for k, v in value.items() if k != "response_profile"}
    legacy_result = fit_response(legacy, roomy)
    assert legacy_result["content"] == value["content"]


@pytest.mark.parametrize("git", [False, True])
@pytest.mark.parametrize("maximum", [None, 2048, 3072, 4096])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_explicit_budget_matrix_over_http_client_mcp(transport, monkeypatch, maximum, ending, git):
    client, repo, _, _ = transport
    names = ["config.py", "caller.py", "loop.py", "backend.py"]
    for index, name in enumerate(names):
        text = f"# Observed component {index}: café\n" + "# dependency context\n" * 7 + f"VALUE_{index} = {index}\n"
        (repo / name).write_bytes(text.replace("\n", ending).encode())
    if git:
        for args in [["init", "-b", "fixture"], ["add", "--", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    trail = Trail(id="d" * 32, namespace="team", question="Explain the configured values",
                  content="Saved interpretation of the configured values. " * 20,
                  sources=[snapshot(repo, name) for name in names])
    assert client.push(trail, **({"repo": repo, "repository_id": "project"} if git else {}))["stored"]
    calls = []
    request = client.__class__.request

    def counted(self, route, payload):
        calls.append(route)
        return request(self, route, payload)

    monkeypatch.setattr(client.__class__, "request", counted)
    budget = ContextBudget() if maximum is None else ContextBudget(max_tokens=maximum)
    maximum = budget.max_tokens
    server = create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                        workflow_repository_id="project" if git else None,
                        workflow_read_only=True, context_budget=budget)
    value = json.loads(asyncio.run(server.call_tool("ask", {"question": trail.question}))[0].text)
    assert calls.count("/ask") == 1 and "/push" not in calls
    assert value["context_budget"]["used"] == count_response(value, budget) <= maximum
    if maximum >= (4096 if git else 3072):
        assert value["hit"] and value["evidence_scope"] == "cited_files"
        assert value["sources"] == [{"ref": s.ref, "content_hash": s.content_hash} for s in trail.sources]
        for read in value["current_repository_reads"]:
            assert read["complete_file"]
            assert read["content"] == (repo / read["path"]).read_text(encoding="utf-8").rstrip("\n")
    elif maximum == 2048:
        assert not value["hit"] or value["evidence_scope"] == "partial"


@pytest.mark.parametrize("command", [["ask", "question"], ["mcp"],
                                      ["setup", "codex", "install", "--repo", ".", "--ns", "team"]])
def test_opt_in_default_and_explicit_ceiling(command):
    assert _context_budget(parser().parse_args(command)) is None
    assert _context_budget(parser().parse_args([*command, "--context-budget"])) == ContextBudget(max_tokens=3072)
    assert _context_budget(parser().parse_args([*command, "--context-budget", "2048"])) == ContextBudget(max_tokens=2048)
