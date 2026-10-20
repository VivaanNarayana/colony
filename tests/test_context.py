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
