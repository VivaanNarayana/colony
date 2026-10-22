import hashlib

import pytest

from colony.context import excerpts
from colony.models import Source
from colony.validation import snapshot


def test_claim_bounds_skip_impossible_paragraphs_without_hiding_reranker_winner(
    tmp_path, monkeypatch
):
    import colony.context

    (tmp_path / "guide.md").write_text("strong lexical\nnear lexical\nunrelated\n")
    source = snapshot(tmp_path, "guide.md")
    chunks = [
        ("guide.md", i + 1, text)
        for i, text in enumerate(["strong lexical", "near lexical", "unrelated"])
    ]
    monkeypatch.setattr(colony.context, "source_chunks", lambda *args: chunks)
    monkeypatch.setattr(
        colony.context,
        "keyword_coverage",
        lambda question, passages: [0.0] * 3 if question == "question" else [1.0, 0.95, 0.0],
    )
    monkeypatch.setattr(
        colony.context, "context_window", lambda ref, lines, line, span: (line, lines[line - 1])
    )
    ranked = []

    def rank(question, passages):
        ranked.extend(passages)
        return [30.0 if p == "near lexical" else -30.0 for p in passages]

    monkeypatch.setattr(colony.context, "rank", rank)
    result = excerpts(tmp_path, [source], "question", claims="claim")
    assert ranked == ["strong lexical", "near lexical"]
    assert result[0]["text"] == "near lexical"
    assert result[1]["text"] == "strong lexical"


def test_claim_bound_keeps_candidate_on_selection_boundary(tmp_path, monkeypatch):
    import colony.context

    (tmp_path / "guide.md").write_text("best\nboundary\n")
    source = snapshot(tmp_path, "guide.md")
    monkeypatch.setattr(
        colony.context,
        "source_chunks",
        lambda *args: [("guide.md", 1, "best"), ("guide.md", 2, "boundary")],
    )
    # Bases are 0.7 and 0.45; the boundary's best possible score is exactly
    # the best paragraph's lower bound minus the existing 0.15 window.
    monkeypatch.setattr(
        colony.context,
        "keyword_coverage",
        lambda question, passages: [0.0, 0.0] if question == "question" else [1.0, 0.45 / 0.7],
    )
    ranked = []

    def rank(question, passages):
        ranked.extend(passages)
        return [0.0] * len(passages)

    monkeypatch.setattr(colony.context, "rank", rank)
    excerpts(tmp_path, [source], "question", claims="claim")
    assert ranked == ["best", "boundary"]


def test_only_matching_attached_evidence_is_returned_and_scrubbed(tmp_path):
    text = (
        "# Build\nUse build --release.\n"
        "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----\n"
        "api_key = 'secret-material'\nThe release command produces a package.\n"
    )
    (tmp_path / "guide.md").write_text(text)
    evidence = snapshot(tmp_path, "guide.md")
    result = excerpts(tmp_path, [evidence], "How is a release built?")
    assert result and result[0]["ref"] == "guide.md" and result[0]["line"] == 1
    returned = result[0]["text"]
    assert "build --release" in returned and "[REDACTED]" in returned
    assert "private-material" not in returned and "secret-material" not in returned
    assert returned.splitlines()[6] == "The release command produces a package."
    # A changed file cannot supply context under an earlier validation receipt.
    (tmp_path / "guide.md").write_text("changed")
    assert excerpts(tmp_path, [evidence], "release") == []
    escaped = Source(ref="../outside", content_hash=hashlib.sha256(b"changed").hexdigest())
    assert excerpts(tmp_path, [escaped], "release") == []


def test_unverified_and_stale_hits_never_include_source_context(team):
    import uuid

    from conftest import auth

    from colony.models import Trail

    _, api, repo, _, key = team
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Build timeout",
        content="TIMEOUT = 30",
        sources=[snapshot(repo, "build.py")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    hit = api.post("/ask", json={"question": "Build timeout"}, headers=auth(key)).json()
    assert hit["status"] == "validated" and hit["evidence"]
    (repo / "build.py").write_text("TIMEOUT = 99")
    hit = api.post("/ask", json={"question": "Build timeout"}, headers=auth(key)).json()
    assert hit["status"] == "stale" and not hit["evidence"]


def test_selected_passage_keeps_its_section_without_second_reranking(tmp_path, monkeypatch):
    import colony.context

    text = (
        "## Release\n\n```sh\n# Prepare artifacts\nship artifacts/release.tar\n```\n"
        + "\n" * 20
        + "## Audit\n\nUnrelated audit instructions belong to another section.\n"
    )
    (tmp_path / "guide.md").write_text(text)
    source = snapshot(tmp_path, "guide.md")

    def unexpected_rerank(*args):
        raise AssertionError("The globally selected passage must be preserved")

    monkeypatch.setattr(colony.context, "rank", unexpected_rerank)
    result = excerpts(tmp_path, [source], "Upload release artifacts", preferred=[("guide.md", 1)])
    assert result[0]["line"] == 1
    assert "ship artifacts/release.tar" in result[0]["text"]
    assert result[0]["text"].startswith("## Release")
    assert "Unrelated audit" not in result[0]["text"]


def test_saved_command_is_grounded_in_usage_section_instead_of_generic_intro(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# Application\n\nA local service for developers. The server starts on demand.\n\n"
        "## Usage\n\n```sh\nlaunch --config service.toml --port 8080\n```\n\n"
        "## Integration\n\nConnect another agent using a stdio adapter.\n"
    )
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "How do I start the local service?",
        claims="Run launch --config service.toml --port 8080.",
    )
    assert result[0]["text"].startswith("## Usage")
    assert "launch --config service.toml --port 8080" in result[0]["text"]


def test_complete_file_context_is_bounded_scrubbed_and_hash_checked(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Setup\n\nlaunch --config service.toml\n\npassword='private-value'\n")
    source = snapshot(tmp_path, "guide.md")
    full = excerpts(tmp_path, [source], "Start the service", whole_files=True)
    assert full[0]["complete_file"] and full[0]["line"] == 1
    assert "launch --config service.toml" in full[0]["text"]
    assert "[REDACTED]" in full[0]["text"] and "private-value" not in full[0]["text"]
    path.write_text("changed")
    assert excerpts(tmp_path, [source], "Start the service", whole_files=True) == []
    path.write_text(
        "# Setup\n\nlaunch --config service.toml\n\n" + "Long unrelated prose.\n" * 1000
    )
    large = excerpts(
        tmp_path, [snapshot(tmp_path, "guide.md")], "Start the service", whole_files=True
    )
    assert large and all(not e["complete_file"] for e in large)
    assert sum(len(e["text"]) for e in large) <= 2800


def test_ranked_sections_keep_order_and_original_source_text(tmp_path):
    from colony.context import source_chunks

    (tmp_path / "guide.md").write_text(
        '# Service\n\n## Logging\n\nlogger --question "Deploy the service?"\n\n'
        "## Deployment\n\nshipctl deploy --target production\n"
    )
    source = snapshot(tmp_path, "guide.md")
    chunks = source_chunks(tmp_path, [source])
    assert any("Section: Service > Deployment" in text for _, _, text in chunks)
    assert all('"Deploy the service?"' not in text for _, _, text in chunks)
    result = excerpts(
        tmp_path, [source], "Deployment procedure", preferred=[("guide.md", 9), ("guide.md", 5)]
    )
    assert result[0]["text"].startswith("## Deployment")
    assert 'logger --question "Deploy the service?"' in result[1]["text"]


def test_remaining_budget_keeps_the_selected_paragraph_in_late_section(tmp_path):
    (tmp_path / "guide.md").write_text(
        "# First\n\n"
        + "A" * 1900
        + "\n\n# Second\n\n"
        + "B" * 1300
        + "\n\nThe selected answer is: deploy the signed artifact.\n"
    )
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "Release procedure",
        preferred=[("guide.md", 3), ("guide.md", 9)],
    )
    assert len(result) == 2
    assert result[1]["line"] == 9
    assert "deploy the signed artifact" in result[1]["text"]
    assert sum(len(e["text"]) for e in result) <= 2800


def test_descriptive_receipt_keeps_complete_fact_without_neighboring_topics(tmp_path):
    text = (
        "## Relay\n\nThe relay retains signed archives for 37 days.\n"
        "Expired archives are removed during maintenance.\n\n"
        "## Credentials\n\nAdministrative credentials are distributed through a separate service.\n"
    )
    (tmp_path / "relay.md").write_text(text)
    source = snapshot(tmp_path, "relay.md")
    result = excerpts(
        tmp_path, [source], "What is the relay archive retention?", preferred=[("relay.md", 3)]
    )
    assert len(result) == 1
    assert "37 days" in result[0]["text"] and "Expired archives" in result[0]["text"]
    assert "Administrative credentials" not in result[0]["text"]
    assert result[0]["line"] == 3
    assert result[0]["text"] == "\n".join(text.splitlines()[2:4])
    assert source.content_hash == hashlib.sha256((tmp_path / "relay.md").read_bytes()).hexdigest()
    (tmp_path / "relay.md").write_text(text.replace("37 days", "9 days"))
    assert excerpts(tmp_path, [source], "Retention?", preferred=[("relay.md", 3)]) == []


@pytest.mark.parametrize(
    "before, fact, after, required",
    [
        (
            "",
            "The relay retains archives for 37 days.",
            "However, legal holds suspend deletion.",
            "legal holds",
        ),
        (
            "The following policy applies only to test relays:",
            "The relay retains archives for 37 days.",
            "",
            "test relays",
        ),
        (
            "Production relays use a separate policy.",
            "They retain archives for 37 days.",
            "",
            "Production relays",
        ),
        (
            "```sh\nrelayctl configure --retention 37d\n```",
            "Archive retention requires the preceding configuration.",
            "",
            "relayctl configure",
        ),
        (
            "````text\nExample literal (not active policy).",
            "The relay retains archives for 37 days.",
            "````",
            "````",
        ),
    ],
)
def test_descriptive_receipts_preserve_dependent_context(tmp_path, before, fact, after, required):
    text = "## Relay\n\n" + (before + "\n\n" if before else "") + fact + "\n\n" + after + "\n"
    (tmp_path / "relay.md").write_text(text)
    selected = text.splitlines().index(fact) + 1
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "relay.md")],
        "What is the relay retention policy?",
        preferred=[("relay.md", selected)],
    )
    assert fact in result[0]["text"] and required in result[0]["text"]


def test_procedural_request_keeps_recipe_even_for_prose_selection(tmp_path):
    text = (
        "## Rotation\n\nRotate credentials before restarting the relay.\n\n"
        "Use the administrator-issued credential for the first restart.\n"
    )
    (tmp_path / "relay.md").write_text(text)
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "relay.md")],
        "How do I rotate relay credentials?",
        preferred=[("relay.md", 3)],
    )
    assert "administrator-issued" in result[0]["text"]


@pytest.mark.parametrize(
    "qualification",
    [
        "Production archives require retention for 90 days.",
        "Regional archives require retention for 61 days.",
        "Signed archives remain available until their audit completes.",
    ],
)
@pytest.mark.parametrize("before", [False, True])
def test_descriptive_compaction_preserves_adjacent_scope_qualifications(
    tmp_path, qualification, before
):
    fact = "Archives are retained for 37 days."
    paragraphs = [qualification, fact] if before else [fact, qualification]
    text = "## Retention\n\n" + "\n\n".join(paragraphs) + "\n\nMaintenance windows occur weekly.\n"
    (tmp_path / "policy.md").write_text(text)
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "policy.md")],
        "What is the archive retention policy?",
        claims=fact,
    )
    assert any(fact in e["text"] and qualification in e["text"] for e in result)
    assert sum(len(e["text"]) for e in result) <= 2800
