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
from colony.validation import snapshot, validate
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
    assert result == {"stored": False, "reason": "scope_evidence_required",
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


def matching_targets(repo, content="LIMIT = 7"):
    for folder in ("first", "second"):
        (repo / folder).mkdir()
        (repo / folder / "value.py").write_text(content)
    alias = repo / "alias"
    directory_link(alias, repo / "first")

    def switch():
        alias.rename(repo / "old-alias")
        directory_link(alias, repo / "second")

    return switch


def test_publication_through_link_pins_the_named_target(identity, monkeypatch):
    client, repo, _ = identity
    switch = matching_targets(repo)
    allowed = {"README.md", "alias/value.py", "first/value.py"}
    server = TaskMCP(Workflow(client, "team", repo), "Explain the limit",
                     read_path_allowed=allowed.__contains__)
    stored = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: stored.append(trail) or {"stored": True})
    pages = call(server, "read", batch=[{"path": "alias/value.py"}, {"path": "README.md"}])["results"]
    result = call(server, "publish", content="The limit is 7.", applicability="first/value.py",
                  evidence_refs=[pages[1]["evidence_ref"]])
    assert result["stored"]
    assert {s.ref for s in stored[0].sources} == allowed
    switch()
    (repo / "first/value.py").write_text("LIMIT = 8")
    assert validate(repo, stored[0].sources)[0] == "stale"


def test_merging_named_aliases_rejects_conflicting_resolved_hashes(identity, monkeypatch):
    from colony import evidence_refs

    client, repo, _ = identity
    path = repo / "settings.py"
    server = TaskMCP(Workflow(client, "team", repo), "Explain the timeout")
    path.write_text("TIMEOUT = 30")
    alias = call(server, "read", path="./settings.py")["evidence_ref"]
    path.write_text("TIMEOUT = 60")
    canonical = call(server, "read", path="settings.py")["evidence_ref"]

    # Each handle verifies individually, but the file changes between the two checks.
    original = evidence_refs.repo_contents
    def concurrent_edit(root, ref, **options):
        if ref == "settings.py":
            path.write_text("TIMEOUT = 60")
        return original(root, ref, **options)

    path.write_text("TIMEOUT = 30")
    monkeypatch.setattr(evidence_refs, "repo_contents", concurrent_edit)
    monkeypatch.setattr(client, "push", lambda *a, **kw: pytest.fail("conflicting sources were published"))
    result = call(server, "publish", content="Timeout behavior", applicability="settings.py",
                  evidence_refs=[alias, canonical])
    assert result["reason"] == "scope_evidence_required"


def test_listed_aliases_share_coverage_only_while_their_targets_match(identity):
    client, repo, _ = identity
    switch = matching_targets(repo)
    paths = ["alias/value.py", "first/value.py", "second/value.py"]
    server = TaskMCP(Workflow(client, "team", repo, read_only=True), "Does the repo define a limit?",
                     scope_files=paths, read_path_allowed=paths.__contains__)
    def plan():
        listed = asyncio.run(server.call_tool("source_files", {}))
        return json.loads(listed[1].text)["scope_read_plan"]

    # Both physical files have identical content. Each needs its own read.
    assert plan()["arguments"]["batch"] == [{"path": paths[0]}, {"path": paths[2]}]
    assert call(server, "read", path=paths[0])["listed_scope"]["unverified_paths"] == [paths[2]]
    switch()
    assert plan()["arguments"]["batch"] == [{"path": paths[0]}]
    assert call(server, "read", path=paths[2])["listed_scope"] == {"status": "complete", "listed_files": 3}


@pytest.mark.parametrize("max_files", [2, 6])
def test_trace_delivers_only_hints_reachable_from_still_authorized_roots(identity, max_files):
    client, repo, _ = identity
    switch = matching_targets(repo, "import private_child\nimport private_missing\nimport shared")
    (repo / "private_child.py").write_text("import private_grandchild")
    (repo / "shared.py").write_text("LIMIT = 9")
    (repo / "caller.py").write_text("import shared")
    attempts = []

    def admit():
        attempts.append(1)
        if len(attempts) == 2:
            switch()
        return True

    server = TaskMCP(Workflow(client, "team", repo), "Trace the limits", trace_reads=True,
                     read_path_allowed=lambda ref: True, read_admission=admit)
    server.evidence._capacity = 1
    trace = call(server, "trace_read", paths=["alias/value.py", "caller.py"], max_files=max_files)
    pages = trace["results"]
    assert pages[0].get("error") and pages[0]["evidence_ref"] is None
    assert pages[1]["content"] == "import shared" and pages[1]["evidence_ref"]
    assert "private_" not in json.dumps(trace)
    assert {obs.ref for obs in server.evidence._observations.values()} == {"caller.py"}
    if max_files == 2:
        assert trace["pending_paths"] == ["shared.py"] and len(pages) == 2
    else:
        assert not trace["pending_paths"] and len(pages) == 3
        assert pages[2]["content"] == "LIMIT = 9" and pages[2]["evidence_ref"] is None
    assert trace["read_accounting"] == {
        "admitted": len(attempts), "denied": 0, "succeeded": len(pages) - 1,
    }


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("stage", ["excerpt", "delivery"])
def test_git_lookup_keeps_source_targets_through_delivery(transport, monkeypatch, adaptive, stage):
    from colony import git_client

    client, repo, _, _ = transport
    switch = matching_targets(repo)
    (repo / "stable.py").write_text("STABLE = 3")
    refs = ["stable.py", "alias/value.py"]
    workflow = Workflow(client, "team", repo, repository_id="project")
    trail = workflow.finding("Explain the two limits", "The configured limits are 3 and 7.",
                             [snapshot(repo, ref) for ref in refs])
    assert client.push(trail, repo=repo, repository_id="project")["stored"]
    options = {"context_budget": ContextBudget(max_tokens=8192)} if adaptive else {}
    server = TaskMCP(workflow, trail.question, read_path_allowed=lambda ref: True, **options)
    assert call(server, "research")["hit"]
    boundary = "_local_reads" if stage == "excerpt" else "verify_lookup"
    original = getattr(git_client, boundary)
    changes = []

    def change_target(*args, **kwargs):
        changes.append(stage)
        if stage == "excerpt":
            switch()
        result = original(*args, **kwargs)
        if stage == "delivery":
            assert result["hit"]
            switch()
        return result

    monkeypatch.setattr(git_client, boundary, change_target)
    result = call(server, "research")
    assert changes == [stage] and not result["hit"]
    assert not {"content", "evidence", "current_repository_reads", "sources"}.intersection(result)
    assert call(server, "read", path=refs[1])["content"] == "LIMIT = 7"


@pytest.mark.parametrize("git", [False, True])
@pytest.mark.parametrize("stage", ["send", "queue", "queue_commit", "stable_offline"])
def test_publication_pins_target_through_send_and_queue(identity, monkeypatch, git, stage):
    client, repo, _ = identity
    switch = matching_targets(repo)
    workflow = Workflow(client, "team", repo, repository_id="project" if git else None)
    server = TaskMCP(workflow, "Explain the limit", read_path_allowed=lambda ref: True)
    page = call(server, "read", path="alias/value.py")
    requests = []
    monkeypatch.setattr(client, "_git_supported", lambda *args: None)
    push = client.push

    def publish_trail(*args, **options):
        if stage == "send":
            switch()
        return push(*args, **options)

    def offline(route, payload):
        requests.append(route)
        if stage == "queue":
            switch()
        raise httpx.ConnectError("offline")

    if stage == "queue_commit":
        from colony import client as client_module
        write = client_module.private_write

        def stage_queue(path, data):
            write(path, data)
            switch()

        monkeypatch.setattr(client_module, "private_write", stage_queue)
    monkeypatch.setattr(client, "push", publish_trail)
    monkeypatch.setattr(client, "request", offline)
    result = call(server, "publish", content="The limit is 7.", applicability="Current settings",
                  evidence_refs=[page["evidence_ref"]])
    assert requests == ([] if stage == "send" else ["/trails"])
    assert bool(list((client.home / "queue").glob("*.enc"))) == (stage == "stable_offline")
    assert not list((client.home / "queue").glob("*.tmp"))
    assert not result["stored"]
    if stage == "stable_offline":
        assert result["queued"]
    else:
        assert result["answer_unblocked"]
    assert call(server, "read", path="alias/value.py")["content"] == "LIMIT = 7"


def test_restricted_publication_cannot_substitute_another_observed_name_during_scrubbing(
    identity, monkeypatch,
):
    client, repo, _ = identity
    names = ["password=testing", "[REDACTED]"]
    for name in names:
        (repo / name).write_text("LIMIT = 7")
    server = TaskMCP(Workflow(client, "team", repo), "Explain the limit",
                     read_path_allowed=names.__contains__)
    pages = call(server, "read", batch=[{"path": name} for name in names])["results"]
    monkeypatch.setattr(client, "push", lambda *a, **kw: pytest.fail("rewritten publication"))
    result = call(server, "publish", content="Limit behavior", applicability="Current settings",
                  evidence_refs=[page["evidence_ref"] for page in pages])
    assert all(page["content"] == "LIMIT = 7" for page in pages)
    assert not result["stored"] and result["answer_unblocked"]


def test_direct_git_verification_also_rechecks_unexcerpted_support(identity, monkeypatch):
    from test_git_client import response

    from colony import git_client, git_context

    _, repo, _ = identity
    switch = matching_targets(repo)
    context = git_context.capture(repo, "project")
    receipt = response(context, snapshot(repo, "settings.py"))
    receipt["sources"].append(snapshot(repo, "alias/value.py").model_dump())
    allowed = {"settings.py", "alias/value.py", "first/value.py", "second/value.py"}
    assert git_client.verify_lookup(receipt, repo, context, path_allowed=allowed.__contains__)["hit"]

    def final_git_context(*args):
        switch()
        return context

    monkeypatch.setattr(git_client, "capture", final_git_context)
    assert git_client.verify_lookup(receipt, repo, context, path_allowed=allowed.__contains__) == {
        "hit": False, "reason": "git_local_evidence_not_verified",
    }
