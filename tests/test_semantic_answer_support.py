"""Descriptive paraphrases and different requested facts use real local models."""

import uuid

import pytest
import sqlite_vec

from colony.models import Trail
from colony.retrieval import local_term_support, rank, terms
from colony.validation import snapshot


@pytest.mark.parametrize(
    "saved_question,content,question,unrelated",
    [
        (
            "What is the archive retention interval?",
            "Archive retention interval is 57 days.",
            "How long do archived records stay available?",
            "Which encryption algorithm protects the archives?",
        ),
        (
            "What is the lifetime of a login session?",
            "A login session expires after thirty minutes.",
            "When does a login session end?",
            "Does a login session permit deleting other users?",
        ),
        (
            "What is the request timeout?",
            "Requests are cancelled after twenty seconds.",
            "How long does a request wait before cancellation?",
            "How many requests can run concurrently?",
        ),
        (
            "What is the maximum attachment size?",
            "Attachments are limited to sixteen megabytes.",
            "How large may an uploaded attachment be?",
            "Which attachment formats can execute scripts?",
        ),
    ],
)
def test_descriptive_paraphrases_reuse_facts_without_accepting_different_attributes(
    team, saved_question, content, question, unrelated
):
    app, _, repo, _, key = team
    store = app.state.store
    path = repo / "policy.md"
    path.write_text(content + "\n", encoding="utf-8")
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question=saved_question,
            content=content,
            sources=[snapshot(repo, "policy.md")],
        ),
        key.grant,
    )
    # Retain semantic diagnostics on failure without recording performance.
    diagnostic = {
        "literal_support": local_term_support(terms(question), content),
        "answer_and_question_scores": rank(question, [content, saved_question]),
    }
    result = store.ask(question, ["team"], 604800, 0.60)
    assert result["hit"], diagnostic
    assert result["status"] == "validated" and result["match_kind"] == "answer"
    assert result["content"] == content
    assert any(content in item["text"] for item in result["evidence"])
    assert not store.ask(unrelated, ["team"], 604800, 0.60)["hit"]
    assert not store.ask(question, ["private"], 604800, 0.60)["hit"]
    # Semantic eligibility must still respect fresh file proofs.
    path.write_text("The documented policy has changed.\n", encoding="utf-8")
    assert store.ask(question, ["team"], 604800, 0.60).get("status") != "validated"


@pytest.mark.parametrize(
    "answer_score,topic_score,expected",
    [
        (4.0, 1.0, True),
        (4.0, 4.0, False),
        (4.0, 6.0, False),
        (0.0, -8.0, False),
        (-2.0, -8.0, False),
    ],
)
def test_nonliteral_answer_requires_positive_discriminating_semantic_support(
    team, monkeypatch, answer_score, topic_score, expected
):
    import colony.store

    app, _, repo, _, key = team
    store = app.state.store
    question = "How large may an uploaded attachment be?"
    saved_question = "What is the maximum attachment size?"
    content = "Attachments are limited to sixteen megabytes."
    (repo / "policy.md").write_text(content, encoding="utf-8")
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question=saved_question,
            content=content,
            sources=[snapshot(repo, "policy.md")],
        ),
        key.grant,
    )
    assert not local_term_support(terms(question), content)
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with store.connect() as db:
        db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
        db.execute("UPDATE trail_questions SET embedding=?", (vector,))
    ranked = []

    def controlled_rank(query, passages):
        assert query == question
        ranked.extend(passages)
        return [topic_score if p == saved_question else answer_score for p in passages]

    monkeypatch.setattr(colony.store, "rank", controlled_rank)
    result = store.find_answer(question, ["team"], 604800, 0.60, query_vector=vector)
    assert result["hit"] == expected
    assert ranked == ([content, saved_question] if answer_score > 0 else [content])
