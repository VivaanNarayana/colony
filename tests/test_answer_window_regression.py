"""A saved answer's unranked tail cannot authorize its ranked prefix."""

import uuid

import pytest
import sqlite_vec

from colony.models import Trail
from colony.validation import snapshot

QUESTION = "Does a login session permit deleting other users?"
SAVED_QUESTION = "What does a login session permit?"
PREFIX = (
    "A login session expires after thirty minutes."
    + " Operational housekeeping rotates diagnostic identifiers daily." * 80
)
UNSUPPORTED = (
    'The phrase "login session permit deleting other users" is an index label; '
    "this record does not document deletion permissions."
)
SUPPORTED = (
    "A login session does not permit deleting other users; deletion requires an administrator."
)


def save(team, content):
    app, _, repo, _, key = team
    (repo / "policy.md").write_text(content + "\n", encoding="utf-8")
    store = app.state.store
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question=SAVED_QUESTION,
            content=content,
            sources=[snapshot(repo, "policy.md")],
        ),
        key.grant,
    )
    return store, repo


def test_unranked_index_label_tail_cannot_authorize_an_answer_hit(team):
    assert len(PREFIX) > 4000
    store, _ = save(team, PREFIX + "\n\n" + UNSUPPORTED)
    assert not store.find_answer(QUESTION, ["team"], 604800, 0.60)["hit"]
    result = store.ask(QUESTION, ["team"], 604800, 0.60)
    # Source-paragraph completeness is a separate issue: do not hide it behind
    # an assertion that all evidence reuse must disappear with answer admission.
    assert not result["hit"] or result["match_kind"] == "evidence"


def test_sufficient_answer_prefix_remains_reusable(team):
    content = SUPPORTED + "\n\n" + PREFIX
    store, _ = save(team, content)
    result = store.ask(QUESTION, ["team"], 604800, 0.60)
    assert result["hit"] and result["match_kind"] == "answer"
    assert result["status"] == "validated" and result["content"] == content


def test_sufficient_unranked_tail_reuses_current_source_instead_of_saved_answer(team):
    store, _ = save(team, PREFIX + "\n\n" + SUPPORTED)
    assert not store.find_answer(QUESTION, ["team"], 604800, 0.60)["hit"]
    result = store.ask(QUESTION, ["team"], 604800, 0.60)
    assert result["hit"] and result["match_kind"] == "evidence"
    assert result["status"] == "validated"
    assert any(SUPPORTED in item["text"] for item in result["evidence"])


@pytest.mark.parametrize("blocked", ["scope", "stale", "expired"])
def test_unranked_tail_cannot_resurrect_disallowed_source_evidence(team, blocked):
    store, repo = save(team, PREFIX + "\n\n" + SUPPORTED)
    namespaces = ["private"] if blocked == "scope" else ["team"]
    if blocked == "stale":
        (repo / "policy.md").write_text("The policy was replaced.\n", encoding="utf-8")
    elif blocked == "expired":
        with store.connect() as db:
            db.execute("UPDATE trails SET created_at=0")
    result = store.ask(QUESTION, namespaces, 604800, 0.60)
    assert not result["hit"]


def test_tail_overlap_does_not_bypass_nonpositive_scored_prefix(team, monkeypatch):
    import colony.store

    store, _ = save(team, PREFIX + "\n\n" + UNSUPPORTED)
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with store.connect() as db:
        db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
        db.execute("UPDATE trail_questions SET embedding=?", (vector,))
    ranked = []

    def irrelevant_prefix(query, passages):
        assert query == QUESTION
        ranked.extend(passages)
        return [-4.0] * len(passages)

    monkeypatch.setattr(colony.store, "rank", irrelevant_prefix)
    result = store.find_answer(QUESTION, ["team"], 604800, 0.60, query_vector=vector)
    assert not result["hit"]
    assert ranked == [(PREFIX + "\n\n" + UNSUPPORTED)[:4000]]
