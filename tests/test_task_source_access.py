"""Exercise the restricted adapter through the same tools workers use."""
import asyncio
import json
import os

import httpx
import pytest
from test_compact_delivery import publish
from test_compact_delivery import transport as transport
from test_workflow import identity as identity

from colony.evidence_refs import EvidenceError
from colony.git_client import checked_sources
from colony.models import ContextBudget
from colony.task_mcp import TaskMCP
from colony.workflow import Workflow


def call(server, tool, **arguments):
    return json.loads(asyncio.run(server.call_tool(tool, arguments))[0].text)


def directory_link(link, destination):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(destination), str(link))
    else:
        link.symlink_to(destination, target_is_directory=True)


def test_internal_directory_link_requires_both_names_for_probe_read_and_reference(identity):
    client, repo, _ = identity
    (repo / "modules").mkdir()
    (repo / "modules/value.py").write_text("LIMIT = 7")
    directory_link(repo / "alias", repo / "modules")
    (repo / "caller.py").write_text("from alias.value import LIMIT")
    allowed = {"caller.py", "alias/value.py"}
    server = TaskMCP(Workflow(client, "team", repo), "Trace the limit", trace_reads=True,
                     read_path_allowed=allowed.__contains__)
    trace = call(server, "trace_read", paths=["caller.py"])
    assert trace["unresolved_paths"] == ["alias/value.py"] and not trace["pending_paths"]
    assert trace["read_accounting"] == {"admitted": 1, "denied": 0, "succeeded": 1}
    page = call(server, "read", batch=[{"path": "alias/value.py"}])["results"][0]
    assert page["evidence_ref"] is None and "content" not in page
    allowed.add("modules/value.py")
    page = call(server, "read", path="alias/value.py")
    assert page["path"] == "alias/value.py" and page["content"] == "LIMIT = 7"
    allowed.remove("alias/value.py")
    with pytest.raises(EvidenceError):
        server.evidence.resolve([page["evidence_ref"]])


@pytest.mark.parametrize("git", [False, True])
def test_revoked_reference_cannot_publish_or_queue_and_the_answer_remains_available(
    identity, monkeypatch, git,
):
    client, repo, _ = identity
    allowed = {"settings.py", "README.md"}
    server = TaskMCP(Workflow(client, "team", repo, repository_id="project" if git else None),
                     "Explain timeout", read_path_allowed=allowed.__contains__)
    page = call(server, "read", path="settings.py")
    allowed.remove("settings.py")
    monkeypatch.setattr(client, "push", lambda *a, **kw: pytest.fail("revoked publication"))
    result = call(server, "publish", content="A reusable finding", applicability="Worker defaults",
                  evidence_refs=[page["evidence_ref"]])
    assert result == {"stored": False, "reason": "publication_failed_or_outcome_unknown",
                      "answer_unblocked": True}
    assert not list((client.home / "queue").glob("*.enc"))
    assert call(server, "read", path="README.md")["content"] == "TIMEOUT = 30"


@pytest.mark.parametrize("git", [False, True])
@pytest.mark.parametrize("adaptive", [False, True])
def test_task_lookup_scopes_all_sources_and_keeps_allowed_hits(transport, git, adaptive):
    client, repo, _, responses = transport
    trail = publish(client, repo, count=2, git=git, content="Configured component values.")
    allowed = {s.ref for s in trail.sources}
    workflow = Workflow(client, "team", repo, repository_id="project" if git else None)
    options = {"context_budget": ContextBudget(max_tokens=8192)} if adaptive else {}
    server = TaskMCP(workflow, trail.question, read_path_allowed=allowed.__contains__, **options)
    ordinary = TaskMCP(workflow, trail.question, **options)
    before = len(responses)
    expected = call(ordinary, "research")
    assert expected["hit"]
    assert call(server, "research") == expected
    allowed.remove("part1.py")
    denied = call(server, "research")
    assert not denied["hit"]
    assert not {"content", "evidence", "sources", "current_repository_reads"}.intersection(denied)
    assert len(responses) == before + 3  # one backend request per explicit lookup
    assert call(server, "read", path="part0.py")["evidence_ref"]


def test_git_excerpt_rechecks_policy_after_hash_validation(transport):
    client, repo, _, _ = transport
    trail = publish(client, repo, count=2, git=True, content="Configured component values.")
    checks = []

    def allowed(ref):
        checks.append(ref)
        return ref != "part1.py" or checks.count(ref) == 1

    server = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question,
                     read_path_allowed=allowed)
    result = call(server, "research")
    assert checks.count("part1.py") == 2
    assert not result["hit"] and "content" not in result


def test_batch_rechecks_staged_entries_before_delivering_text_or_handles(identity):
    client, repo, _ = identity
    allowed = {"README.md", "settings.py"}

    def permit(ref):
        if ref == "settings.py":
            allowed.discard("README.md")
        return ref in allowed

    server = TaskMCP(Workflow(client, "team", repo), "Read both files", read_path_allowed=permit)
    result = call(server, "read", batch=[{"path": "README.md"}, {"path": "settings.py"}])
    first, second = result["results"]
    assert first["evidence_ref"] is None and "content" not in first
    assert second["evidence_ref"] and second["content"] == "TIMEOUT = 30"
    assert server._read_access.admitted == 2


def test_git_push_rechecks_sources_after_reference_resolution(identity, monkeypatch):
    client, repo, _ = identity
    allowed = {"settings.py"}
    server = TaskMCP(Workflow(client, "team", repo, repository_id="project"), "Explain settings",
                     read_path_allowed=allowed.__contains__)
    page = call(server, "read", path="settings.py")
    checks = []

    def revoke(root, sources, **options):
        allowed.clear()
        checks.append(True)
        return checked_sources(root, sources, **options)

    monkeypatch.setattr("colony.git_client.checked_sources", revoke)
    monkeypatch.setattr(client, "request", lambda *a: pytest.fail("unauthorized network call"))
    result = call(server, "publish", content="Behavior", applicability="Settings",
                  evidence_refs=[page["evidence_ref"]])
    assert checks == [True] and not result["stored"]
    assert not list((client.home / "queue").glob("*.enc"))


def test_lost_connection_cannot_queue_a_now_revoked_source(identity, monkeypatch):
    client, repo, _ = identity
    allowed = {"settings.py"}
    server = TaskMCP(Workflow(client, "team", repo), "Explain settings",
                     read_path_allowed=allowed.__contains__)
    page = call(server, "read", path="settings.py")

    def offline(route, payload):
        assert route == "/trails"
        allowed.clear()
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(client, "request", offline)
    result = call(server, "publish", content="Behavior", applicability="Settings",
                  evidence_refs=[page["evidence_ref"]])
    assert not result["stored"] and result["answer_unblocked"] and not allowed
    assert not list((client.home / "queue").glob("*.enc"))


@pytest.mark.parametrize("git", [False, True])
def test_allowed_finding_still_publishes_once_with_its_reference(transport, git):
    client, repo, _, _ = transport
    publish(client, repo, count=1, git=git)
    server = TaskMCP(Workflow(client, "team", repo, repository_id="project" if git else None),
                     "Explain component zero", read_path_allowed={"part0.py"}.__contains__)
    page = call(server, "read", path="part0.py")
    payload = dict(content="Component zero uses value zero.", applicability="Current configuration",
                   evidence_refs=[page["evidence_ref"]])
    assert call(server, "publish", **payload)["stored"] is True
    assert not call(server, "publish", **payload)["stored"]
    assert server._read_access.admitted == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows handle validation")
def test_mismatched_open_handle_is_rejected_before_content_delivery(identity, monkeypatch):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Explain settings",
                     read_path_allowed={"settings.py"}.__contains__)
    monkeypatch.setattr("colony.validation._windows_handle_path", lambda stream: "unverified target")
    page = call(server, "read", batch=[{"path": "settings.py"}])["results"][0]
    assert page["evidence_ref"] is None and "content" not in page
    assert not server.evidence._observations


def test_retargeted_alias_cannot_release_staged_content_or_rebind_a_reference(identity):
    client, repo, _ = identity
    for folder in ("first", "second"):
        (repo / folder).mkdir()
        (repo / folder / "value.py").write_text("LIMIT = 7")
    alias = repo / "alias"
    directory_link(alias, repo / "first")
    allowed = {"alias/value.py", "first/value.py", "second/value.py", "README.md"}

    def permit(ref):
        if ref == "README.md" and "first/value.py" in allowed:
            alias.rename(repo / "old-alias")
            directory_link(alias, repo / "second")
            allowed.remove("first/value.py")
        return ref in allowed

    server = TaskMCP(Workflow(client, "team", repo), "Find limit", read_path_allowed=permit)
    original = call(server, "read", path="alias/value.py")
    pages = call(server, "read", batch=[{"path": "alias/value.py"}, {"path": "README.md"}])["results"]
    assert pages[0]["evidence_ref"] is None and "content" not in pages[0]
    assert pages[1]["evidence_ref"]
    with pytest.raises(EvidenceError):
        server.evidence.resolve([original["evidence_ref"]])
    assert call(server, "read", path="alias/value.py")["content"] == "LIMIT = 7"
