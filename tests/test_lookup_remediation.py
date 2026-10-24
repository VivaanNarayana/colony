"""Behavioral checks across independent subjects; no retrieval scores are mocked."""

import uuid

import numpy as np
import pytest

from colony.context import context_window, excerpts
from colony.models import Trail
from colony.retrieval import embed_passages, models, rank, terms
from colony.validation import snapshot


def test_empty_permitted_namespaces_do_not_run_inference_and_new_trails_are_visible(
    team, monkeypatch
):
    import colony.store

    app, _, repo, _, key = team
    store = app.state.store
    original = colony.store.embed

    def unexpected_inference(*args, **kwargs):
        raise AssertionError("No permitted candidates exist")

    with monkeypatch.context() as absent:
        absent.setattr(colony.store, "embed", unexpected_inference)
        assert store.ask("Build timeout", ["team"], 3600, 0.60) == {
            "hit": False,
            "reason": "no_relevant_trail",
        }
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="Build timeout",
            content="TIMEOUT = 30",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    with monkeypatch.context() as denied:
        denied.setattr(colony.store, "embed", unexpected_inference)
        assert store.ask("Build timeout", ["private"], 3600, 0.60) == {
            "hit": False,
            "reason": "no_relevant_trail",
        }
    assert colony.store.embed is original
    result = store.ask("Build timeout", ["unused", "team"], 3600, 0.60)
    assert result["status"] == "validated" and "TIMEOUT = 30" in result["content"]
    # Expiry must retain the existing diagnostic answer, not become an empty miss.
    with store.connect() as db:
        db.execute("UPDATE trails SET created_at=0")
    result = store.ask("Build timeout", ["team"], 3600, 0.60)
    assert result["hit"] and result["status"] == "unverified" and result["age_flag"]


@pytest.mark.parametrize("object_name", ["token", "seed", "credentials"])
def test_lexical_terms_keep_substantive_get(object_name):
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
def test_source_answer_keeps_scope_and_unrelated_topic_guards(team, subject, state, question):
    app, _, repo, _, key = team
    direct_question = f"What is the {subject} retry policy?"
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
            question=direct_question,
            content=fact,
            sources=[snapshot(repo, "operations.md")],
        ),
        key.grant,
    )
    result = app.state.store.ask(direct_question, ["team"], 604800, 0.60)
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


def test_claim_window_excludes_an_unrelated_declaration(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Ledger bridge\n\nThe ledger bridge exposes operations via the verified signed adapter.\n\n"
        # A separate section tests score exclusion independently of the rule
        # requiring adjacent qualification paragraphs to remain visible.
        "## History\n\nOperations: historical values are unavailable.\n"
    )
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "Which operations does the ledger bridge expose?",
        claims="The ledger bridge exposes operations via the verified signed adapter.",
    )
    assert "verified signed adapter" in result[0]["text"]
    assert all("historical values" not in e["text"] for e in result)


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


def test_bounded_cpu_parallelism_preserves_two_thread_model_outputs():
    import os

    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    from colony.retrieval import EMBED_MODEL, EMBED_REVISION, RANK_MODEL, RANK_REVISION

    texts = [
        "Signed archives expire after thirty-seven days.",
        "Short operational note.",
        "Unrelated audit logging details. " * 100,
        "```sh\norbitctl verify --strict\n```",
        "Signed archives expire after thirty-seven days.",
    ]
    query = "How long are signed archives retained?"
    baseline_embed = TextEmbedding(
        model_name=EMBED_MODEL,
        revision=EMBED_REVISION,
        specific_model_path=os.environ.get("COLONY_EMBED_MODEL_DIR"),
        threads=2,
    )
    baseline_rank = TextCrossEncoder(
        model_name=RANK_MODEL,
        revision=RANK_REVISION,
        specific_model_path=os.environ.get("COLONY_RANK_MODEL_DIR"),
        threads=2,
    )
    vectors = list(baseline_embed.passage_embed(texts, batch_size=1))
    actual = embed_passages(texts)
    for current, original in zip(actual, vectors, strict=True):
        np.testing.assert_allclose(np.frombuffer(current, dtype=np.float32), original, atol=1e-5)
    expected = list(baseline_rank.rerank(query, texts, batch_size=1))
    scores = rank(query, texts)
    np.testing.assert_allclose(scores, expected, atol=1e-5, rtol=1e-5)
    assert np.argsort(scores).tolist() == np.argsort(expected).tolist()
