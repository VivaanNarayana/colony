import hashlib

from colony.context import excerpts
from colony.models import Source
from colony.validation import snapshot


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
