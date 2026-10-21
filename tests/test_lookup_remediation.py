"""Behavioral checks across independent subjects; no retrieval scores are mocked."""

import uuid

import numpy as np
import pytest

from colony.context import context_window, excerpts, source_chunks
from colony.models import Trail
from colony.retrieval import embed_passages, models
from colony.validation import snapshot


def test_source_embeddings_preserve_values_and_order_across_input_lengths():
    texts = [
        "Short operational note.",
        "Archive retention is thirty-seven days. " * 70,
        "orbitctl verify --strict",
        "Short operational note.",
    ]
    model, _ = models()
    expected = list(model.passage_embed(texts))
    actual = embed_passages(texts)
    assert len(actual) == len(texts)
    for vector, original in zip(actual, expected, strict=True):
        np.testing.assert_allclose(np.frombuffer(vector, dtype=np.float32), original, atol=1e-5)


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


def test_focus_retains_code_block_described_by_selected_prose():
    lines = [
        "## Verification",
        "",
        "```sh",
        "orbitctl verify --strict",
        "orbitctl lint",
        "```",
        "",
        "Verification checks grants, storage integrity and replication.",
        *[f"Documentation note {i}." for i in range(25)],
    ]
    selected = lines.index("Verification checks grants, storage integrity and replication.") + 1
    start, text = context_window("guide.md", lines, selected, 1)
    assert "orbitctl verify --strict" in text
    assert "orbitctl lint" in text
    assert text.count("```") == 2
    assert "Documentation note 24." not in text
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.splitlines())])


def test_positional_question_example_cannot_displace_actual_procedure(tmp_path):
    text = (
        "# Atlas\n\n## Query examples\n\n```sh\n"
        'atlas ask "How do I initialize the workspace?"\n```\n\n'
        "## Workspace preparation\n\n```sh\natlasctl bootstrap --profile development\n```\n"
    )
    (tmp_path / "guide.md").write_text(text)
    source = snapshot(tmp_path, "guide.md")
    chunks = source_chunks(tmp_path, [source])
    assert all('"How do I initialize the workspace?"' not in t for _, _, t in chunks)
    result = excerpts(
        tmp_path,
        [source],
        "How do I initialize the workspace?",
        claims="Run `atlasctl bootstrap --profile development`.",
    )
    assert any("atlasctl bootstrap --profile development" in e["text"] for e in result)
