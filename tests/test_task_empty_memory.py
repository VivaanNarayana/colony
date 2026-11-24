import asyncio
import json

import httpx
import pytest
from conftest import auth
from test_workflow import identity as identity

from colony.models import Scope
from colony.security import issue
from colony.task_mcp import PRESENT_FOLLOWUP_ROUTE, TaskMCP
from colony.workflow import Workflow


@pytest.mark.parametrize("question,state,path,expect_route", [
    ("For the same deployment, explain its rollout.", "present", None, True),
    ("Following up on the earlier trace, what is the retry limit?", "present", None, True),
    ("For the same deployment, explain its rollout.", "empty", None, False),
    ("For the same deployment, explain its rollout.", "unknown", None, False),
    ("For the same deployment, explain its rollout.", "present", "settings.py", False),
    ("For the same input, compare both implementations.", "present", None, False),
    ("Explain the retry limit.", "present", None, False),
])
def test_saved_work_route_is_scoped_to_first_followup_action(identity, monkeypatch, question, state, path, expect_route):
    client, repo, _ = identity
    monkeypatch.setattr(client, "memory_availability", lambda *args: state)
    monkeypatch.setattr(client, "ask", lambda *a, **kw: pytest.fail("unexpected automatic lookup"))
    server = TaskMCP(Workflow(client, "team", repo), question, direct_path=path)
    server._availability_enabled = server._empty_memory_routing

    async def check():
        async def has_route():
            tools = {t.name: t for t in await server.list_tools()}
            return PRESENT_FOLLOWUP_ROUTE.strip() in tools["research"].description

        assert await has_route() is expect_route
        assert await server.call_tool("read", {"path": "settings.py"})
        server._availability_until = 0
        assert not await has_route()

    asyncio.run(check())


def test_availability_auth_scope_repository_and_no_retrieval(team, monkeypatch):
    app, api, _, admin, key = team
    monkeypatch.setattr(app.state.store, "ask", lambda *a, **k: pytest.fail("ranking"))

    def check(body=None, headers=None):
        return api.post(
            "/memory/availability",
            json=body or {"namespace": "team"},
            headers=auth(key) if headers is None else headers,
        )

    assert check().json() == {
        "contract": "colony.memory-availability/1",
        "namespace": "team",
        "repository_id": None,
        "state": "empty",
    }
    assert check(headers={}).status_code == 401
    assert check({"namespace": "private"}).status_code == 403
    assert check({"namespace": "team", "repository_id": "other"}).status_code == 409
    assert check({"namespace": "team", "question": "forbidden"}).status_code == 422
    expired = issue(admin / "issuer.pem", key.grant.model_copy(update={"expires_at": 1}))
    assert check(headers=auth(expired)).status_code == 401
    push_only = issue(
        admin / "issuer.pem",
        key.grant.model_copy(update={"scopes": [Scope(namespace="team", push=True)]}),
    )
    assert check(headers=auth(push_only)).status_code == 403
    # Insert lifecycle rows without invoking embedding/model code. A stale active
    # record counts as present; a retired one does not. Other namespaces stay private.
    with app.state.store.connect() as db:
        db.execute(
            "INSERT INTO trails VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "a",
                "team",
                "q",
                "blob",
                "a",
                1,
                "stale",
                1,
                None,
                "h",
            ),
        )
        db.execute("INSERT INTO memory_state_v1(trail_id,state,revision) VALUES ('a','active',0)")
    assert check().json()["state"] == "present"
    outsider = issue(
        admin / "issuer.pem",
        key.grant.model_copy(update={"scopes": [Scope(namespace="private", pull=True)]}),
    )
    assert check({"namespace": "private"}, auth(outsider)).json()["state"] == "empty"
    with app.state.store.connect() as db:
        db.execute("UPDATE memory_state_v1 SET state='retracted' WHERE trail_id='a'")
    assert check().json()["state"] == "empty"


@pytest.mark.parametrize(
    "change", ["wrong_namespace", "wrong_repo", "missing", "extra", "miss", "outage"]
)
def test_client_fails_open_to_normal_research_on_invalid_availability(
    identity, monkeypatch, change
):
    client, _, _ = identity
    value = {
        "contract": "colony.memory-availability/1",
        "namespace": "team",
        "repository_id": "repo",
        "state": "empty",
    }
    if change == "wrong_namespace":
        value["namespace"] = "private"
    if change == "wrong_repo":
        value["repository_id"] = "other"
    if change == "missing":
        del value["state"]
    if change == "extra":
        value["private"] = "secret"
    if change == "miss":
        value = {"hit": False, "reason": "no_relevant_trail"}

    def post(url, **kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["timeout"] == 1
        if change == "outage":
            raise httpx.ConnectError("private details")
        return httpx.Response(200, json=value, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert client.memory_availability("team", "repo") == "unknown"


def test_client_checks_credentials_and_never_sends_to_changed_endpoint(identity, monkeypatch):
    client, _, _ = identity

    def post(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "contract": "colony.memory-availability/1",
                "namespace": "team",
                "repository_id": None,
                "state": "empty",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    assert client.memory_availability("team") == "empty"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("credential leak"))
    assert client.memory_availability("private") == "unknown"
    client.url = "https://wrong.invalid"
    assert client.memory_availability("team") == "unknown"


def test_snapshot_expires_and_explicit_lookup_never_uses_cached_empty(identity, monkeypatch):
    client, repo, _ = identity
    state, probes, asks = ["empty"], [], []
    monkeypatch.setattr(client, "memory_availability", lambda *a: probes.append(a) or state[0])
    monkeypatch.setattr(client, "ask", lambda *a, **k: asks.append(a) or {"hit": True})
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True

    async def check():
        first = await server.list_tools()
        description = next(t.description for t in first if t.name == "research")
        assert "empty at last check" in description and "replace source discovery" not in description
        assert len(probes) == 1 and not asks
        state[0] = "present"  # Concurrent publication after snapshot, before TTL.
        result = json.loads((await server.call_tool("research", {}))[0].text)
        assert result["hit"] and len(asks) == 1
        server._availability_until = 0
        second = await server.list_tools()
        assert "empty at last check" not in next(
            t.description for t in second if t.name == "research"
        )
        assert len(probes) == 2
        state[0] = "unknown"
        server._availability_until = 0
        assert {t.name for t in await server.list_tools()} == {"read", "research"}
        assert json.loads((await server.call_tool("read", {"path": "settings.py"}))[0].text)[
            "content"
        ]

    asyncio.run(check())


def test_write_invalidates_hint_even_when_outcome_unknown(identity, monkeypatch):
    client, repo, _ = identity
    monkeypatch.setattr(client, "memory_availability", lambda *a: "empty")
    monkeypatch.setattr(client, "push", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True

    async def check():
        page = json.loads((await server.call_tool("read", {"path": "settings.py"}))[0].text)
        result = json.loads(
            (
                await server.call_tool(
                    "publish",
                    {
                        "content": "Behavior",
                        "applicability": "settings",
                        "evidence_refs": [page["evidence_ref"]],
                    },
                )
            )[0].text
        )
        assert result["answer_unblocked"] and server._availability_state == "unknown"
        assert server._availability_until == 0
        assert json.loads((await server.call_tool("read", {"path": "settings.py"}))[0].text)[
            "content"
        ]

    asyncio.run(check())


def test_inflight_snapshot_cannot_restore_hint_after_write_invalidation(identity, monkeypatch):
    import threading

    client, repo, _ = identity
    started, release = threading.Event(), threading.Event()

    def probe(*args):
        started.set()
        assert release.wait(3)
        return "empty"

    monkeypatch.setattr(client, "memory_availability", probe)
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True

    async def check():
        pending = asyncio.create_task(server._refresh_availability())
        assert await asyncio.to_thread(started.wait, 3)
        server._availability_generation += 1
        server._availability_state = "unknown"
        server._availability_until = 0
        release.set()
        await pending
        assert server._availability_state == "unknown" and server._availability_until == 0

    asyncio.run(check())


def test_probe_failure_and_identity_change_never_preserve_empty_hint(identity, monkeypatch):
    client, repo, _ = identity
    monkeypatch.setattr(client, "memory_availability", lambda *a: "empty")
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True

    async def check():
        await server._refresh_availability()
        assert server._availability_state == "empty"
        server._availability_until = 0
        monkeypatch.setattr(
            client, "memory_availability", lambda *a: (_ for _ in ()).throw(RuntimeError())
        )
        await server._refresh_availability()
        assert server._availability_state == "unknown"
        server._availability_state = "empty"
        client.keyfile.grant.member_id = "changed"
        await server._refresh_availability()
        assert server._availability_state == "unknown"

    asyncio.run(check())


def test_newer_probe_wins_and_catalog_hints_do_not_accumulate(identity, monkeypatch):
    import threading

    client, repo, _ = identity
    first_started, release = threading.Event(), threading.Event()
    calls = []

    def probe(*args):
        calls.append(1)
        if len(calls) == 1:
            first_started.set()
            assert release.wait(3)
            return "empty"
        return "present"

    monkeypatch.setattr(client, "memory_availability", probe)
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True

    async def check():
        first = asyncio.create_task(server._refresh_availability())
        assert await asyncio.to_thread(first_started.wait, 3)
        await server._refresh_availability()
        release.set()
        await first
        assert server._availability_state == "present"
        monkeypatch.setattr(client, "memory_availability", lambda *a: "empty")
        server._availability_until = 0
        a = await server.list_tools()
        b = await server.list_tools()
        assert [t.model_dump() for t in a] == [t.model_dump() for t in b]
        assert (
            next(t.description for t in b if t.name == "research").count("empty at last check") == 1
        )

    asyncio.run(check())


def test_explicit_lookup_skips_probe_and_outage_checks_back_off(identity, monkeypatch):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True
    probes = []
    monkeypatch.setattr(client, "memory_availability", lambda *a: probes.append(1) or "unknown")
    monkeypatch.setattr(
        client, "ask", lambda *a, **k: {"hit": False, "reason": "no_relevant_trail"}
    )

    async def check():
        await server.call_tool("research", {})
        assert not probes and server._availability_state == "unknown"
        server._availability_until = 0
        await server.list_tools()
        await server.list_tools()
        await server.call_tool("read", {"path": "settings.py"})
        assert len(probes) == 1

    asyncio.run(check())


@pytest.mark.parametrize("name,arguments", [
    ("read", {"path": "missing.py"}),
    ("read", {"offset": "invalid"}),
    ("unknown_tool", {}),
])
def test_failed_tool_still_notifies_changed_availability(identity, monkeypatch, name, arguments):
    from types import SimpleNamespace

    from mcp.server.fastmcp.exceptions import ToolError

    client, repo, _ = identity
    state, notifications = ["empty"], []
    monkeypatch.setattr(client, "memory_availability", lambda *a: state[0])
    server = TaskMCP(Workflow(client, "team", repo, read_only=True), "Explain behavior")
    server._availability_enabled = True

    class Session:
        async def send_tool_list_changed(self):
            notifications.append(1)

    monkeypatch.setattr(server, "get_context", lambda: SimpleNamespace(
        request_context=SimpleNamespace(session=Session())))

    async def check():
        await server.list_tools()
        state[0] = "present"
        server._availability_until = 0
        with pytest.raises(ToolError, match="Task tool failed"):
            await server.call_tool(name, arguments)
        assert notifications == [1]
        assert not any("empty at last check" in (t.description or "")
                       for t in await server.list_tools())
        await server.call_tool("read", {"path": "settings.py"})
        assert notifications == [1]  # No redundant catalog event for unchanged state.

    asyncio.run(check())


def test_notification_failure_preserves_tool_error_and_publication_invalidation(identity, monkeypatch):
    from types import SimpleNamespace

    from mcp.server.fastmcp.exceptions import ToolError

    client, repo, _ = identity
    monkeypatch.setattr(client, "memory_availability", lambda *a: "empty")
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server._availability_enabled = True
    notifications = []

    class Session:
        async def send_tool_list_changed(self):
            notifications.append(1)
            raise RuntimeError("transport failure")

    monkeypatch.setattr(server, "get_context", lambda: SimpleNamespace(
        request_context=SimpleNamespace(session=Session())))

    async def check():
        await server.list_tools()
        with pytest.raises(ToolError, match="Task tool failed"):
            await server.call_tool("publish", {})
        assert server._availability_state == "unknown"
        assert server._availability_until == 0
        assert notifications == [1]
        assert not server._publication_attempted
        result = await server.call_tool("read", {"path": "settings.py"})
        assert json.loads(result[0].text)["content"]

    asyncio.run(check())
