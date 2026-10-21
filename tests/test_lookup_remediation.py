"""Behavioral checks across independent subjects; no retrieval scores are mocked."""

import uuid

import numpy as np
import pytest

from colony.context import context_window, excerpts
from colony.models import Trail
from colony.retrieval import embed_passages, models, rank, terms
from colony.validation import snapshot


@pytest.mark.parametrize("object_name", ["token", "seed", "credentials"])
def test_lexical_normalization_keeps_substantive_get(object_name):
    assert "get" in terms(f"How can I get {object_name}?")


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


def test_reranking_preserves_logits_and_order_across_input_lengths():
    query = "How long are signed archives retained?"
    passages = [
        "Signed archives are retained for thirty-seven days.",
        "Unrelated configuration notes. " * 90,
        "Retain signed archives for thirty-seven days, then remove them.",
        "Signed archives are retained for thirty-seven days.",
    ]
    _, model = models()
    expected = list(model.rerank(query, passages))
    actual = rank(query, passages)
    np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-5)
    assert np.argsort(actual).tolist() == np.argsort(expected).tolist()


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
    (repo / "operations.md").write_text(
        "# Operations\n\n## Availability\n\n"
        "Logs use a structured format. Authentication errors fail immediately. "
        f"{fact} The audit log is retained for seven days. "
        "Metrics are sampled once per minute.\n"
    )
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
    passive = f"Do requests get queued while the {subject} is {state}?"
    result = app.state.store.ask(passive, ["team"], 604800, 0.60)
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
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.split("\n"))])


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
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.split("\n"))])


def test_claims_select_actual_procedure_instead_of_question_example(tmp_path):
    text = (
        "# Atlas\n\n## Query examples\n\n```sh\n"
        'atlas ask "How do I initialize the workspace?"\n```\n\n'
        "## Workspace preparation\n\n```sh\natlasctl bootstrap --profile development\n```\n"
    )
    (tmp_path / "guide.md").write_text(text)
    source = snapshot(tmp_path, "guide.md")
    result = excerpts(
        tmp_path,
        [source],
        "How do I initialize the workspace?",
        claims="Run `atlasctl bootstrap --profile development`.",
    )
    assert any("atlasctl bootstrap --profile development" in e["text"] for e in result)


@pytest.mark.parametrize(
    "question, paragraph, expected",
    [
        (
            "Which operations does the ledger bridge support?",
            "The ledger bridge supports operations `read_entry` and `append_entry`.",
            "append_entry",
        ),
        (
            "How long does the ledger bridge retain archives?",
            "The ledger bridge retains signed archives for 37 days.",
            "37 days",
        ),
        (
            "What is the ledger bridge archive size limit?",
            "The ledger bridge archive size limit is 19 MiB.",
            "19 MiB",
        ),
    ],
)
def test_related_question_reads_its_fact_instead_of_only_an_old_summary(
    tmp_path, question, paragraph, expected
):
    (tmp_path / "guide.md").write_text(
        "# Ledger bridge\n\n## Connection\n\n"
        "Configure `ledger-agent --workspace /srv/ledger stdio`.\n\n"
        f"## Reference\n\n{paragraph}\n"
    )
    source = snapshot(tmp_path, "guide.md")
    result = excerpts(
        tmp_path,
        [source],
        question,
        claims="Configure `ledger-agent --workspace /srv/ledger stdio`.",
        prefer_question=True,
    )
    assert any(expected in e["text"] for e in result)
    lines = (tmp_path / "guide.md").read_text().splitlines()
    for e in result:
        assert e["text"] == "\n".join(
            lines[e["line"] - 1 : e["line"] - 1 + len(e["text"].split("\n"))]
        )


def test_related_question_does_not_let_keyword_rich_metadata_hide_the_answer(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Ledger bridge\n\n## Connection\n\n"
        "Configure `ledger-agent --workspace /srv/ledger stdio`.\n\n"
        "## Diagnostics\n\nLedger bridge operation support metadata is logged.\n\n"
        "## Operations\n\nOperations: `read_entry` and `append_entry`.\n"
    )
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "Which operations does the ledger bridge support?",
        claims="Configure `ledger-agent --workspace /srv/ledger stdio`.",
        prefer_question=True,
    )
    assert any("append_entry" in e["text"] for e in result)


def test_recipe_retains_prerequisites_for_later_invocation():
    lines = [
        "## Bootstrap",
        "",
        "```sh",
        "orbitctl bootstrap --profile development",
        "```",
        "",
        "Then query the configured service:",
        "",
        "```sh",
        'orbitctl ask "How is the workspace configured?"',
        "```",
        "",
        "## Unrelated",
        *[f"Other note {i}." for i in range(25)],
    ]
    selected = lines.index("```sh", 5) + 1
    start, text = context_window("guide.md", lines, selected, 3)
    assert "orbitctl bootstrap --profile development" in text
    assert "How is the workspace configured?" in text
    assert "Unrelated" not in text
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.split("\n"))])


def test_prose_does_not_expand_into_an_unselected_recipe():
    lines = [
        "## Operations",
        "",
        "```sh",
        "orbitctl bootstrap --profile development",
        "```",
        "",
        "Additional documentation.",
        "",
        "Unavailable relays queue requests for fifteen seconds.",
        *[f"Other note {i}." for i in range(25)],
    ]
    start, text = context_window("guide.md", lines, 9, 1)
    assert "Unavailable relays queue requests for fifteen seconds." in text
    assert "orbitctl bootstrap" not in text
    assert text == "\n".join(lines[start - 1 : start - 1 + len(text.split("\n"))])
