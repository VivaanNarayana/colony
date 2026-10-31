import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.fernet import Fernet

from colony.client import Client
from colony.mcp import create_mcp
from colony.models import Grant, Scope, Source
from colony.security import generate_keys, issue, private_write
from colony.workflow import Workflow, configure_workflow


@pytest.fixture
def identity(tmp_path):
    admin = tmp_path / "admin"
    generate_keys(admin)
    home = tmp_path / "home"
    grant = Grant(
        member_id="workflow-agent",
        name="Workflow Agent",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
        capture="none",
    )
    private_write(home / "identity.json", issue(admin / "issuer.pem", grant).model_dump_json().encode())
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = []
    for ref in ("settings.py", "README.md"):
        path = repo / ref
        path.write_text("TIMEOUT = 30\n")
        sources.append(Source(ref=ref, content_hash=hashlib.sha256(path.read_bytes()).hexdigest()))
    return Client(home), repo, sources


def call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


def test_canonical_ids_follow_sanitized_payload_and_binding(identity, tmp_path):
    client, repo, sources = identity
    first = Workflow(client, "team", repo).finding("password=first", "Use timeout 30", sources)
    # Different matched secrets become the same sanitized finding; source order/duplicates
    # cannot create a different payload under an already derived ID.
    second = Workflow(Client(client.home), "team", repo / ".").finding(
        "password=second", "Use timeout 30", [sources[1], sources[0], sources[1]]
    )
    assert first == second
    assert first.question == "[REDACTED]"
    assert first.tainted and first.capture == "none"
    assert len(first.id) == 32
    assert Workflow(client, "team", repo).finding("q", "new answer", sources).id != first.id
    changed = sources[0].model_copy(update={"content_hash": "a" * 64})
    assert Workflow(client, "team", repo).finding("password=first", first.content, [changed]).id != first.id
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    for source in sources:
        (other_repo / source.ref).write_text("TIMEOUT = 30\n")
    assert Workflow(client, "team", other_repo).finding("password=first", first.content, sources).id != first.id
    client.keyfile.grant.member_id = "another-producer"
    assert Workflow(client, "team", repo).finding("password=first", first.content, sources).id != first.id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"namespace": "team", "repo": None},
        {"namespace": None, "repo": "repo"},
        {"namespace": None, "repo": None, "read_only": True},
        {"namespace": "bad namespace", "repo": "repo"},
    ],
)
def test_workflow_options_fail_closed(identity, kwargs):
    client, repo, _ = identity
    if kwargs["repo"] == "repo":
        kwargs["repo"] = repo
    with pytest.raises(ValueError):
        configure_workflow(client, **kwargs)


def test_workflow_requires_pull_and_optional_push(identity):
    client, repo, sources = identity
    client.keyfile.grant.scopes[0].push = False
    with pytest.raises(ValueError, match="push denied"):
        Workflow(client, "team", repo)
    read_only = Workflow(client, "team", repo, read_only=True)
    with pytest.raises(ValueError, match="read-only"):
        read_only.finding("q", "c", sources)
    client.keyfile.grant.scopes[0].pull = False
    with pytest.raises(ValueError, match="pull denied"):
        Workflow(client, "team", repo, read_only=True)


@pytest.mark.parametrize("ref", ["../outside", "/absolute", "C:/secret", "..\\secret", "missing"])
def test_workflow_rejects_unavailable_or_escaping_files(identity, ref):
    client, repo, sources = identity
    source = sources[0].model_copy(update={"ref": ref})
    with pytest.raises(ValueError, match="available repository file") as error:
        Workflow(client, "team", repo).finding("q", "c", [source])
    assert ref not in str(error.value)


def test_workflow_bounds_and_requires_file_evidence_without_reread(identity, monkeypatch):
    client, repo, sources = identity
    workflow = Workflow(client, "team", repo)
    with pytest.raises(ValueError, match="file evidence"):
        workflow.finding("q", "c", [])
    with pytest.raises(ValueError, match="file evidence"):
        workflow.finding("q", "c", [sources[0].model_copy(update={"kind": "url"})])
    with pytest.raises(ValueError, match="4,000"):
        workflow.finding("q", "x" * 4001, sources)

    def no_reread(*args, **kwargs):
        pytest.fail("Publishing must reuse fingerprints without reading file bytes")

    monkeypatch.setattr(type(repo), "read_bytes", no_reread)
    assert len(workflow.finding("q", "x" * 4000, sources).content) == 4000


def test_workflow_mcp_namespace_read_only_and_manual_compatibility(identity, monkeypatch):
    client, repo, sources = identity
    calls = []

    def request(self, route, payload):
        calls.append((route, payload))
        return {"hit": False} if route == "/ask" else {"id": payload["id"], "stored": True}

    monkeypatch.setattr(Client, "request", request)
    server = create_mcp(client.home, workflow_namespace="team", workflow_repo=repo)
    call(server, "ask", question="q")
    assert calls[-1][1]["namespace"] == "team"
    with pytest.raises(Exception, match="configured workflow namespace"):
        call(server, "ask", question="q", namespace="private")
    finding = {"question": "q", "content": "c", "sources": [s.model_dump() for s in sources]}
    # FileEvidence deliberately accepts only file identities, not advanced assertions.
    finding["sources"] = [
        {key: value for key, value in s.items() if key in {"ref", "content_hash", "kind"}}
        for s in finding["sources"]
    ]
    call(server, "push", **finding, tainted=False)
    first = calls[-1][1]
    assert first["namespace"] == "team" and first["tainted"] and first["capture"] == "none"
    restarted = create_mcp(client.home, workflow_namespace="team", workflow_repo=repo)
    call(restarted, "push", **finding)
    assert calls[-1][1] == first
    with pytest.raises(Exception, match="configured workflow namespace"):
        call(server, "push", **finding, namespace="private")
    tools = asyncio.run(server.list_tools())
    assert "namespace" not in next(t for t in tools if t.name == "push").inputSchema["required"]
    readonly = create_mcp(
        client.home, workflow_namespace="team", workflow_repo=repo, workflow_read_only=True
    )
    assert {t.name for t in asyncio.run(readonly.list_tools())} == {"ask"}
    manual = create_mcp(client.home)
    manual_push = next(t for t in asyncio.run(manual.list_tools()) if t.name == "push")
    assert set(manual_push.inputSchema["required"]) == {"namespace", "question", "content"}
    call(manual, "push", namespace="team", question="q", content="c", tainted=False)
    first_manual = calls[-1][1]
    call(manual, "push", namespace="team", question="q", content="c", tainted=False)
    assert calls[-1][1]["id"] != first_manual["id"]
    assert not calls[-1][1]["tainted"] and calls[-1][1]["sources"] == []


def test_response_loss_queue_and_fresh_process_retry_keep_exact_payload(identity, monkeypatch):
    client, repo, sources = identity
    trail = Workflow(client, "team", repo).finding("password=sentinel", "Use timeout 30", sources)
    committed = {}

    def lost_ack(url, *, json, **kwargs):
        committed.update(json)
        raise httpx.ConnectError("lost acknowledgment")

    monkeypatch.setattr(httpx, "post", lost_ack)
    assert client.push(trail)["queued"]
    restarted = Client(client.home)
    same = Workflow(restarted, "team", repo).finding("password=another", trail.content, sources[::-1])
    assert restarted.push(same)["queued"]
    paths = list((client.home / "queue").glob("*.enc"))
    assert len(paths) == 1 and b"sentinel" not in paths[0].read_bytes()
    queued = json.loads(Fernet((client.home / "queue.key").read_bytes()).decrypt(paths[0].read_bytes()))
    assert queued["trail"] == committed == trail.model_dump()

    def acknowledged(url, *, json, **kwargs):
        assert json == committed
        return httpx.Response(
            200,
            json={"id": json["id"], "stored": True, "duplicate": True},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", acknowledged)
    assert restarted.retry() == {"sent": 1, "pending": False}
    assert not paths[0].exists()


def test_concurrent_workflow_submissions_send_same_id_and_payload(identity, monkeypatch):
    client, repo, sources = identity
    recorded = []
    lock = threading.Lock()

    def post(url, *, json, **kwargs):
        with lock:
            recorded.append(json)
        return httpx.Response(
            200, json={"id": json["id"], "stored": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)

    def submit(index):
        fresh = Client(client.home)
        trail = Workflow(fresh, "team", repo).finding("q", "c", sources[:: (-1 if index % 2 else 1)])
        return fresh.push(trail)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(8)))
    assert len(recorded) == 8 and all(p == recorded[0] for p in recorded)
    assert len({result["id"] for result in results}) == 1


@pytest.mark.parametrize("field", ["question", "content", "ref"])
def test_control_characters_and_invalid_findings_do_not_echo_input(identity, field):
    client, repo, sources = identity
    values = {"question": "q", "content": "c"}
    if field == "ref":
        sources = [sources[0].model_copy(update={"ref": "secret-path\x00"})]
    else:
        values[field] = "secret-text\x00"
    with pytest.raises(ValueError, match="control characters") as error:
        Workflow(client, "team", repo).finding(**values, sources=sources)
    assert "secret" not in str(error.value)
    with pytest.raises(ValueError, match="Invalid workflow finding") as error:
        Workflow(client, "team", repo).finding("secret-question" * 200, "c", identity[2])
    assert "secret" not in str(error.value)


def test_concurrent_offline_queue_publishes_complete_envelope_and_retries(identity, monkeypatch):
    client, repo, sources = identity
    barrier = threading.Barrier(8)

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", offline)

    def submit(index):
        fresh = Client(client.home)
        trail = Workflow(fresh, "team", repo).finding("password=sentinel", "c", sources)
        barrier.wait(timeout=10)
        return fresh.push(trail)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(8)))
    assert all(result["queued"] for result in results)
    assert len({result["id"] for result in results}) == 1
    paths = list((client.home / "queue").iterdir())
    assert len(paths) == 1 and paths[0].suffix == ".enc"
    assert b"sentinel" not in paths[0].read_bytes()
    payload = json.loads(Fernet((client.home / "queue.key").read_bytes()).decrypt(paths[0].read_bytes()))
    assert payload["trail"]["question"] == "[REDACTED]"
    assert payload["trail"]["id"] == results[0]["id"]

    def online(url, *, json, **kwargs):
        assert json == payload["trail"]
        return httpx.Response(
            200, json={"stored": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", online)
    assert Client(client.home).retry() == {"sent": 1, "pending": False}
    assert not list((client.home / "queue").iterdir())


def test_offline_queue_conflicts_and_unsupported_atomic_publish_fail_closed(identity, monkeypatch):
    client, repo, sources = identity

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", offline)
    trail = Workflow(client, "team", repo).finding("q", "c", sources)
    assert client.push(trail)["queued"]
    with pytest.raises(ValueError, match="Queued trail ID conflict"):
        client.push(trail.model_copy(update={"content": "different"}))
    existing = (client.home / "queue" / f"{trail.id}.enc").read_bytes()

    def unsupported(*args, **kwargs):
        raise OSError("hard links unavailable")

    monkeypatch.setattr("colony.client.os.link", unsupported)
    other = Workflow(client, "team", repo).finding("q", "changed", sources)
    with pytest.raises(OSError, match="hard links unavailable"):
        client.push(other)
    assert (client.home / "queue" / f"{trail.id}.enc").read_bytes() == existing
    assert len(list((client.home / "queue").iterdir())) == 1


def test_real_store_duplicate_preserves_age_and_rejects_conflicting_id(team, tmp_path, monkeypatch):
    app, api, repo, admin, key = team
    home = tmp_path / "real-home"
    private_write(home / "identity.json", key.model_dump_json().encode())
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    client = Client(home)

    def post(url, **kwargs):
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    source = Source(ref="build.py", content_hash=hashlib.sha256((repo / "build.py").read_bytes()).hexdigest())
    trail = Workflow(client, "team", repo).finding("Build timeout", "TIMEOUT = 30", [source])
    assert client.push(trail)["stored"]
    old = time.time() - 8 * 86400
    with app.state.store.connect() as db:
        db.execute("UPDATE trails SET created_at=? WHERE id=?", (old, trail.id))
    assert Client(home).push(trail)["duplicate"]
    with app.state.store.connect() as db:
        row = db.execute("SELECT created_at FROM trails WHERE id=?", (trail.id,)).fetchone()
        assert row["created_at"] == old
        assert db.execute("SELECT COUNT(*) FROM trails").fetchone()[0] == 1
    answer = client.ask("Build timeout", "team")
    assert answer["age_flag"] and answer["status"] == "unverified"
    with pytest.raises(httpx.HTTPStatusError):
        client.push(trail.model_copy(update={"content": "conflicting answer"}))
