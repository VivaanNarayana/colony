"""Real-store compatibility checks for adaptive packing plus local observations."""

import pytest
from test_compact_budget_fallback import publish_bundle
from test_compact_budget_fallback import transport as transport
from test_git_store import context, finding
from test_git_store import setup_store as setup_store
from test_memory_store import memory_team as memory_team

from colony.compact import PROFILE
from colony.context_budget import count_response
from colony.models import ContextBudget
from colony.retrieval_diagnostics import RetrievalDiagnostics


class BrokenObserver:
    def record(self, *args, **kwargs):
        raise OSError("Observer unavailable")


def observer(mode):
    if mode == "broken":
        return BrokenObserver()
    return RetrievalDiagnostics(limit=1 if mode == "bounded" else 128)


@pytest.mark.parametrize("profile", ["legacy", "budget", "compact"])
@pytest.mark.parametrize("mode", ["normal", "bounded", "broken"])
@pytest.mark.parametrize("fallback", [False, True])
def test_diagnostics_preserve_legacy_and_adaptive_answers(memory_team, profile, mode, fallback):
    database, _, _, trail = memory_team
    options = {} if profile == "legacy" else {"context_budget": ContextBudget(max_tokens=8192)}
    if profile == "compact":
        options["response_profile"] = PROFILE
    question = "Which timeout is configured?" if fallback else trail.question
    threshold = 1.0 if fallback else 0.6
    baseline = database.ask(question, ["team"], 604800, threshold, **options)
    assert baseline["hit"] and baseline["status"] == "validated"
    assert baseline["match_kind"] == ("evidence" if fallback else "answer")
    diagnostics = observer(mode)
    result = database.ask(question, ["team"], 604800, threshold, diagnostics=diagnostics, **options)
    assert result == baseline
    assert "diagnostics" not in result
    if profile != "legacy":
        assert (
            result["context_budget"]["used"]
            == count_response(result, options["context_budget"])
            <= 8192
        )
    if mode == "normal":
        stages = [r["stage"] for r in diagnostics.snapshot()["records"]]
        assert "lookup_started" in stages
        assert stages.count("evidence_fallback") == int(fallback)
    elif mode == "bounded":
        assert diagnostics.snapshot()["dropped"] > 0


@pytest.mark.parametrize("mode", ["normal", "bounded", "broken"])
def test_budget_salvage_preserves_same_finding_and_reports_fallback(transport, mode):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo)
    budget = ContextBudget(max_tokens=2048)
    options = {"context_budget": budget, "response_profile": PROFILE}
    baseline = database.ask(trail.question, ["team"], 604800, 0.6, **options)
    assert baseline["hit"] and baseline["match_kind"] == "evidence"
    assert baseline["id"] == trail.id and baseline["context_budget"]["limited"]
    assert [r["path"] for r in baseline["current_repository_reads"]] == ["part3.py"]
    diagnostics = observer(mode)
    result = database.ask(trail.question, ["team"], 604800, 0.6, diagnostics=diagnostics, **options)
    assert result == baseline
    assert count_response(result, budget) == result["context_budget"]["used"] <= 2048
    if mode == "normal":
        fallback = [
            r for r in diagnostics.snapshot()["records"] if r["stage"] == "evidence_fallback"
        ]
        assert fallback == [
            {"stage": "evidence_fallback", "answer_status": "validated", "reason": "budget_limited"}
        ]


@pytest.mark.parametrize("scopes", [[], ["private"]])
def test_adaptive_diagnostics_preserve_denied_scope_and_no_ids(memory_team, scopes):
    database, _, _, trail = memory_team
    diagnostics = RetrievalDiagnostics()
    options = {"context_budget": ContextBudget(max_tokens=2048), "response_profile": PROFILE}
    baseline = database.ask(trail.question, scopes, 604800, 0.6, **options)
    result = database.ask(trail.question, scopes, 604800, 0.6, diagnostics=diagnostics, **options)
    assert result == baseline and not result["hit"]
    assert all(r.get("trail_id") != trail.id for r in diagnostics.snapshot()["records"])
    stages = [r["stage"] for r in diagnostics.snapshot()["records"]]
    assert ("evidence_fallback" in stages) == bool(scopes)


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_git_adaptive_lookup_and_diagnostics_keep_provenance_rules(setup_store, mismatch, compact):
    create, repo, grant, _ = setup_store
    database = create()
    trail = finding(repo, context())
    database.push(trail, grant)
    options = {
        "context_budget": ContextBudget(max_tokens=8192),
        "git_context": context(branch="other" if mismatch else "main"),
    }
    if compact:
        options["response_profile"] = PROFILE
    baseline = database.ask(trail.question, ["team"], 604800, 0.6, **options)
    diagnostics = RetrievalDiagnostics()
    result = database.ask(trail.question, ["team"], 604800, 0.6, diagnostics=diagnostics, **options)
    assert result == baseline and result["status"] == "validated"
    assert result["match_kind"] == ("evidence" if mismatch else "answer")
    stages = [r["stage"] for r in diagnostics.snapshot()["records"]]
    assert ("git_rejected" in stages) == mismatch
    assert ("evidence_fallback" in stages) == mismatch
