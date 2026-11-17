import hashlib

import pytest
from test_task_batch_reads import call, make, tools
from test_workflow import identity as identity

from colony.evidence_refs import EvidenceError


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("text,start", [("one\ntwo\n", 1), ("one\ntwo\n", 2), ("", 1)])
def test_oversized_end_matches_omitted_end_and_binds_actual_span(identity, batch, text, start):
    path = identity[1] / "small.txt"
    path.write_text(text, encoding="utf-8")
    server = make(identity)
    expected = call(server, path="small.txt", start=start)
    request = {"path": "small.txt", "start": start, "end": 120}
    actual = (call(server, batch=[request])["results"][0] if batch else call(server, **request))
    metadata = {"evidence_ref", "publication_guidance"}
    assert {k: v for k, v in actual.items() if k not in metadata} == {
        k: v for k, v in expected.items() if k not in metadata}
    observation = server.evidence._observations[actual["evidence_ref"]]
    assert observation.end == len(text.splitlines())
    assert server.evidence.resolve([actual["evidence_ref"]])[0].content_hash == hashlib.sha256(
        path.read_bytes()).hexdigest()
    assert "publish" in tools(server)


def test_clipped_batch_continuation_scrubs_and_preserves_publication(identity, monkeypatch):
    client, repo, _ = identity
    body = "😀é漢" * 4000
    (repo / "long.txt").write_text('first\n' + body + '\npassword="secret"\nlast', encoding="utf-8")
    server = make(identity)
    request = {"path": "long.txt", "start": 2, "end": 120}
    results = call(server, batch=[request, {"path": "../outside", "end": 120}])["results"]
    page = results[0]
    assert results[1]["evidence_ref"] is None and "outside" not in str(results[1])
    content, refs = "", []
    while True:
        assert (page["start_line"], page["end_line"]) == (2, 4)
        assert not page["complete_file"]
        content += page["content"]
        refs.append(page["evidence_ref"])
        if page.get("next_offset") is None:
            break
        page = call(server, **request, offset=page["next_offset"])
    assert content == body + "\n[REDACTED]\nlast"
    assert len(server.evidence._observations) == len(refs)
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    assert call(server, "publish", content="Observed behavior", applicability="long.txt",
                evidence_refs=refs, kind="behavior")["stored"]
    assert sent[0].tainted
    (repo / "long.txt").write_text("changed")
    with pytest.raises(EvidenceError, match="changed"):
        server.evidence.resolve(refs)


@pytest.mark.parametrize("options", [
    {"start": 3, "end": 120}, {"start": 2, "end": 1}, {"start": 0, "end": 120},
    {"end": -1}, {"end": True}, {"end": 120, "offset": 1000},
])
def test_clipping_does_not_accept_invalid_ranges_or_mint_handles(identity, options):
    (identity[1] / "small.txt").write_text("one\ntwo")
    server = make(identity)
    with pytest.raises(EvidenceError):
        server.evidence.read("small.txt", **options)
    assert not server.evidence._observations
    assert "publish" not in tools(server)


def test_clipping_does_not_bypass_read_only_publication(identity):
    server = make(identity, read_only=True)
    observed = call(server, batch=[{"path": "settings.py", "end": 120}])["results"][0]
    assert observed["content"] and "publish" not in tools(server)
    with pytest.raises(EvidenceError):
        server.evidence.resolve([observed["evidence_ref"]])
