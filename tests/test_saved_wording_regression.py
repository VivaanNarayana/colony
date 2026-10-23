"""Bounded unrelated prose variants, with real-model diagnostics on every case."""

import json
import uuid

import pytest

from colony.models import Trail
from colony.retrieval import local_term_support, rank, terms
from colony.validation import snapshot

CASES = [
    (
        "What is the archive retention interval?",
        "How long do archived records stay available?",
        "Which encryption algorithm protects the archives?",
        "Archive retention interval is fifty-seven days.",
        "The archive service keeps historical records available for fifty-seven days. "
        "This period starts when a record is archived. Records are then removed automatically.",
    ),
    (
        "What is the lifetime of a login session?",
        "When does a login session end?",
        "Does a login session permit deleting other users?",
        "A login session expires after thirty minutes.",
        "After thirty minutes, the login session expires. Users must authenticate again "
        "before continuing. The expiry interval applies to each newly created session.",
    ),
    (
        "What is the maximum attachment size?",
        "How large may an uploaded attachment be?",
        "Which attachment formats can execute scripts?",
        "Attachments are limited to sixteen megabytes.",
        "The upload service accepts attachments no larger than sixteen megabytes. "
        "Larger attachments are rejected before storage, so users should reduce their size.",
    ),
]


@pytest.mark.parametrize("saved_question,question,unrelated,concise,explanatory", CASES)
@pytest.mark.parametrize("wording", ["concise", "explanatory"])
def test_sufficient_saved_wording_and_unsupported_attribute_control(
    team, saved_question, question, unrelated, concise, explanatory, wording
):
    app, _, repo, _, key = team
    content = concise if wording == "concise" else explanatory
    (repo / "policy.md").write_text(content + "\n", encoding="utf-8")
    store = app.state.store
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
    diagnostic = {
        "wording": wording,
        "question": question,
        "literal_support": local_term_support(terms(question), content),
        "answer_and_control_scores": rank(question, [content, saved_question]),
        "unsupported_attribute_question": unrelated,
        "unsupported_attribute_literal_support": local_term_support(terms(unrelated), content),
        "unsupported_attribute_answer_and_control_scores": rank(
            unrelated, [content, saved_question]
        ),
    }
    result = store.ask(question, ["team"], 604800, 0.60)
    negative = store.ask(unrelated, ["team"], 604800, 0.60)
    diagnostic.update(result=result, unsupported_attribute_result=negative)
    print(json.dumps(diagnostic, sort_keys=True))
    # Evaluate the negative control even when a sufficient answer is missed.
    assert not negative["hit"], diagnostic
    assert result["hit"] and result["status"] == "validated", diagnostic
    assert any(content in item["text"] for item in result["evidence"]), diagnostic

