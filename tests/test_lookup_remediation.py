"""Behavioral checks across independent subjects; no retrieval scores are mocked."""

import uuid

import pytest

from colony.context import context_window
from colony.models import Trail
from colony.validation import snapshot


@pytest.mark.parametrize(
    "subject,state,question",
    [
        ("relay", "unavailable", "What happens when the relay is down?"),
        ("registry", "offline", "What happens during a registry outage?"),
        ("broker", "down", "What happens when the broker is unavailable?"),
    ],
)
def test_availability_paraphrases_keep_the_same_scoped_source_facts(team, subject, state, question):
    app, _, repo, _, key = team
    fact = f"When the {subject} is {state}, requests are queued and retried after fifteen seconds."
    (repo / "operations.md").write_text(f"# Operations\n\n## Availability\n\n{fact}\n")
    app.state.store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="Audit retention policy",
            content="Keep audit logs for seven days.",
            sources=[snapshot(repo, "operations.md")],
        ),
        key.grant,
    )
    result = app.state.store.ask(question, ["team"], 604800, 0.60)
    assert result["status"] == "validated"
    assert any(fact in e["text"] for e in result["evidence"])
    assert not app.state.store.ask(question, ["private"], 604800, 0.60)["hit"]
    # Matching an outage topic is insufficient to answer unrelated scope policy.
    assert not app.state.store.ask(
        f"Does a {subject} outage change credential scopes?", ["team"], 604800, 0.60
    )["hit"]


def test_focused_context_keeps_original_lines_and_excludes_unselected_prose():
    lines = [
        "## Operations",
        "",
        *[f"Unrelated note {i}." for i in range(30)],
        "",
        "Retain signed archives for 37 days.",
        "The dry-run command is `sweep --dry-run`.",
        "",
        *[f"Other note {i}." for i in range(30)],
    ]
    selected = lines.index("Retain signed archives for 37 days.") + 1
    start, text = context_window("operations.md", lines, selected, 2)
    assert "Retain signed archives for 37 days." in text
    assert "sweep --dry-run" in text
    assert "Unrelated note 0." not in text and "Other note 29." not in text
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.splitlines())])
