import pytest
from test_workflow import identity as identity

from colony.evidence_refs import EvidenceError, EvidenceSession
from colony.task_mcp import TaskMCP
from colony.workflow import Workflow


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_unicode_partial_spans_and_empty_files(identity, ending):
    client, repo, _ = identity
    (repo / "unicode.txt").write_bytes(ending.join(["café", "second", "third", ""]).encode())
    session = EvidenceSession(Workflow(client, "team", repo))
    result = session.read("unicode.txt", 2, 2)
    assert result["content"] == "second" and not result["complete_file"]
    (repo / "empty.txt").write_bytes(b"")
    result = session.read("empty.txt")
    assert result["content"] == "" and result["end_line"] == 0 and result["complete_file"]


def test_oversized_read_can_be_split_without_losing_content(identity):
    client, repo, _ = identity
    (repo / "large.txt").write_text("x" * 9000 + "\n" + "y" * 9000, encoding="utf8")
    session = EvidenceSession(Workflow(client, "team", repo))
    a = session.read("large.txt")
    b = session.read("large.txt", offset=a["next_offset"])
    assert a["content"] + b["content"] == (repo / "large.txt").read_text()
    assert len(a["content"].encode("utf-8")) <= 16000
    assert b["next_offset"] is None
    assert not a["complete_file"] and not b["complete_file"]
    assert len(session.resolve([a["evidence_ref"], b["evidence_ref"]])) == 1


def test_permission_expiry_prevents_resolution(identity, monkeypatch):
    client, repo, _ = identity
    session = EvidenceSession(Workflow(client, "team", repo))
    ref = session.read("settings.py")["evidence_ref"]
    monkeypatch.setattr("colony.security.time.time", lambda: client.keyfile.grant.expires_at + 1)
    with pytest.raises(EvidenceError, match="permission"):
        session.resolve([ref])


def test_git_binding_is_forwarded_without_inventing_origin(identity, monkeypatch):
    client, repo, _ = identity
    # The repository capture path is an ordinary Git operation, not supplied by source text.
    import subprocess

    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    workflow = Workflow(client, "team", repo, repository_id="project")
    session = EvidenceSession(workflow)
    ref = session.read("settings.py")["evidence_ref"]
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append((trail, kw)) or {"stored": True})
    session.publish("q", "Observed behavior", [ref])
    assert sent[0][0].git_context.repository_id == "project"
    assert sent[0][1] == {"repo": repo.resolve(), "repository_id": "project"}


def test_host_requires_question(identity):
    client, repo, _ = identity
    with pytest.raises(ValueError):
        TaskMCP(Workflow(client, "team", repo), " ")
