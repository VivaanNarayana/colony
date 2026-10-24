"""Partial lexical overlap must not authorize a negative semantic answer."""

import uuid

import pytest

from colony.models import Trail
from colony.validation import snapshot


def save(team, saved, content):
    app, _, repo, _, key = team
    (repo / "policy.md").write_text(content + "\n", encoding="utf-8")
    store = app.state.store
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question=saved,
            content=content,
            sources=[snapshot(repo, "policy.md")],
        ),
        key.grant,
    )
    return store, repo


def test_shared_subject_and_modal_words_do_not_authorize_an_unrelated_attribute(team):
    store, _ = save(
        team,
        "When may volunteers enter the greenhouse?",
        "Volunteers may enter the greenhouse from seven until eleven on Saturdays, "
        "provided a supervisor is present.",
    )
    question = "Which fertilizers may volunteers use in the greenhouse?"
    assert not store.find_answer(question, ["team"], 604800, 0.60)["hit"]
    assert not store.ask(question, ["team"], 604800, 0.60)["hit"]


@pytest.mark.parametrize(
    "saved,content,question,qualification",
    [
        (
            "What happens if a school field trip is cancelled?",
            "When a field trip is cancelled, the booking fee is returned. "
            "Transport deposits are refunded only if the operator agrees.",
            "Is the transport deposit always refunded on cancellation?",
            "only if the operator agrees",
        ),
        (
            "What happens when the server is down?",
            "When the server is down, ask returns a miss on transport errors, and push "
            "encrypts the trail into a local queue and returns stored=false and queued=true. "
            "Retry sends queued trails in order until a transport error leaves items pending.",
            "Does an offline push get queued?",
            "transport error leaves items pending",
        ),
    ],
)
def test_partial_wording_keeps_sufficient_answers_and_governing_conditions(
    team, saved, content, question, qualification
):
    store, repo = save(team, saved, content)
    hit = store.ask(question, ["team"], 604800, 0.60)
    assert hit["hit"] and hit["status"] == "validated"
    assert hit["content"] == content
    assert qualification in hit["content"]
    assert any(qualification in e["text"] for e in hit["evidence"])
    assert not store.ask(question, ["private"], 604800, 0.60)["hit"]
    (repo / "policy.md").write_text("The policy has changed.\n", encoding="utf-8")
    assert store.ask(question, ["team"], 604800, 0.60).get("status") != "validated"
