import json
import math

import pytest
import sqlite_vec
from test_git_store import context, finding
from test_git_store import setup_store as setup_store
from test_memory_store import memory_team as memory_team

import colony.store
from colony.retrieval_diagnostics import RetrievalDiagnostics


def lookup(database, diagnostics=None, question="Which timeout is configured?", threshold=.60):
    return database.find_answer(question, ["team"], 604800, threshold, diagnostics=diagnostics)


def test_diagnostics_preserve_answer_and_only_report_declared_coverage(memory_team):
    database, repo, _, trail = memory_team
    (repo / "uncited.py").write_text("UNOBSERVED = True")
    baseline = lookup(database)
    diagnostic = RetrievalDiagnostics()
    observed = lookup(database, diagnostic)
    assert observed == baseline
    records = diagnostic.snapshot()["records"]
    admission = next(r for r in records if r["stage"] == "admission_gates")
    assert admission["relevance_passed"] and admission["question_passed"]
    checked = next(r for r in records if r["stage"] == "source_validation")
    assert checked["declared_source_count"] == 1 and checked["status"] == "validated"
    assert checked["trail_id"] == trail.id
    text = json.dumps(diagnostic.snapshot())
    assert "build.py" not in text and "TIMEOUT = 30" not in text and "uncited.py" not in text


@pytest.mark.parametrize("mode", ["dense", "question", "relevance", "procedure"])
def test_rejection_stage_explains_existing_filters(memory_team, monkeypatch, mode):
    database, _, _, _ = memory_team
    query = "Which timeout is configured?"
    if mode in {"dense", "question"}:
        cosine = .3 if mode == "dense" else .6
        vector = sqlite_vec.serialize_float32([cosine, math.sqrt(1-cosine*cosine)] + [0.] * 382)
        with database.connect() as db:
            db.execute("UPDATE trail_questions SET embedding=?", (vector,))
            if mode == "dense":
                db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
    if mode == "relevance":
        monkeypatch.setattr(colony.store, "rank", lambda q, texts: [-10.] * len(texts))
    if mode == "procedure":
        query = "How do I configure the timeout?"
        monkeypatch.setattr(database, "read_content", lambda ref: "The timeout is described here.")
    diagnostic = RetrievalDiagnostics()
    baseline = lookup(database, question=query, threshold=.95 if mode == "relevance" else .6)
    result = lookup(database, diagnostic, query, .95 if mode == "relevance" else .6)
    assert result == baseline and not result["hit"]
    records = diagnostic.snapshot()["records"]
    if mode == "dense":
        assert not next(r for r in records if r["stage"] == "dense_gate")["passed"]
    else:
        assert not next(r for r in records if r["stage"] == "admission_gates")[mode+"_passed"]
    assert records[-1]["stage"] == "no_admitted_candidates"


def test_exact_question_and_stale_evidence_keep_existing_behavior(memory_team):
    database, repo, _, trail = memory_team
    diagnostic = RetrievalDiagnostics()
    assert lookup(database, diagnostic, trail.question, 1.0)["hit"]
    admission = next(r for r in diagnostic.snapshot()["records"] if r["stage"] == "admission_gates")
    assert admission["exact_question"] and admission["relevance_passed"]
    (repo / "build.py").write_text("TIMEOUT = 60")
    diagnostic = RetrievalDiagnostics()
    result = lookup(database, diagnostic)
    assert result["status"] == "stale"
    assert next(r for r in diagnostic.snapshot()["records"] if r["stage"] == "source_validation")["status"] == "stale"


def test_no_scope_or_retired_candidates_leak_no_ids(memory_team):
    database, _, _, trail = memory_team
    for scopes in ([], ["private"]):
        diagnostic = RetrievalDiagnostics()
        assert not database.find_answer("q", scopes, 604800, .6, diagnostics=diagnostic)["hit"]
        assert trail.id not in json.dumps(diagnostic.snapshot())
    with database.connect() as db:
        db.execute("UPDATE memory_state_v1 SET state='retracted'")
    diagnostic = RetrievalDiagnostics()
    assert not lookup(database, diagnostic)["hit"]
    assert trail.id not in json.dumps(diagnostic.snapshot())


def test_git_rejection_is_visible_without_changing_result(setup_store):
    create, repo, grant, _ = setup_store
    database = create()
    trail = finding(repo, context())
    database.push(trail, grant)
    diagnostic = RetrievalDiagnostics()
    kwargs = {"git_context": context(branch="other")}
    baseline = database.find_answer(trail.question, ["team"], 604800, .6, **kwargs)
    result = database.find_answer(trail.question, ["team"], 604800, .6, diagnostics=diagnostic, **kwargs)
    assert result == baseline and not result["hit"]
    assert any(r["stage"] == "git_rejected" for r in diagnostic.snapshot()["records"])


def test_bounded_snapshot_and_observer_failure_do_not_change_answers(memory_team):
    database, _, _, _ = memory_team
    diagnostic = RetrievalDiagnostics(limit=1)
    baseline = lookup(database)
    assert lookup(database, diagnostic) == baseline
    snap = diagnostic.snapshot()
    assert len(snap["records"]) == 1 and snap["dropped"] > 0
    snap["records"].clear()
    assert len(diagnostic.snapshot()["records"]) == 1
    class Broken:
        def record(self, *args, **kwargs):
            raise OSError("Diagnostic unavailable")
    assert lookup(database, Broken()) == baseline
    assert database.ask("Which timeout is configured?", ["team"], 604800, .6,
                        diagnostics=Broken()) == baseline
