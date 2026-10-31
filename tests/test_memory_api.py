import json
import time
import uuid

import httpx
import pytest
from conftest import auth
from cryptography.fernet import Fernet

from colony.client import Client, MemoryUnavailable
from colony.memory import MemoryConflict, MemoryForbidden, MemoryInvalid, MemoryNotFound
from colony.models import Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


def memory_acknowledgment(route, request):
    response = {
        "id": request["trail_id"],
        "memory": {
            "state": "active",
            "revision": 0,
            "replacement_id": None,
            "feedback": {"helpful": 0, "incorrect": 0, "outdated": 0},
        },
    }
    if route == "/memory/inspect":
        response.update(
            namespace=request["namespace"],
            producer_id="author",
            question="Question",
            content="Answer",
            content_truncated=False,
            sources=[],
            history=[],
        )
    else:
        response.update(operation_id=request["operation_id"], replayed=False)
        for key in ("action", "verdict"):
            if key in request:
                response[key] = request[key]
        if route == "/memory/correct":
            response["replacement_id"] = "f" * 32
    return response


@pytest.fixture
def local_identity(tmp_path):
    admin, home = tmp_path / "issuer", tmp_path / "home"
    generate_keys(admin)
    grant = Grant(
        member_id="client",
        name="Client",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", push=True, pull=True)],
    )
    private_write(
        home / "identity.json", issue(admin / "issuer.pem", grant).model_dump_json().encode()
    )
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    return Client(home)


def seeded(team):
    _, api, repo, _, key = team
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Timeout",
        content="TIMEOUT = 30",
        sources=[snapshot(repo, "build.py")],
    )
    assert api.post("/trails", json=trail.model_dump(), headers=auth(key)).status_code == 200
    return trail


def another_key(team, *, member="reader", push=False, namespace="team"):
    _, _, _, admin, key = team
    grant = key.grant.model_copy(
        update={"member_id": member, "scopes": [Scope(namespace=namespace, pull=True, push=push)]}
    )
    return issue(admin / "issuer.pem", grant)


def test_memory_http_lifecycle_and_explicit_replay(team):
    _, api, repo, _, key = team
    trail = seeded(team)
    target = {"namespace": "team", "trail_id": trail.id}
    inspected = api.post("/memory/inspect", json=target, headers=auth(key))
    assert inspected.status_code == 200
    assert inspected.json()["memory"]["revision"] == 0
    request = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "verdict": "incorrect",
        "note": "password=sentinel",
    }
    feedback = api.post("/memory/feedback", json=request, headers=auth(key))
    assert feedback.status_code == 200
    assert not feedback.json()["replayed"]
    repeated = api.post("/memory/feedback", json=request, headers=auth(key))
    assert repeated.status_code == 200 and repeated.json()["replayed"]
    assert repeated.json()["memory"]["feedback"]["incorrect"] == 1
    assert "sentinel" not in repeated.text
    current = api.post("/memory/inspect", json=target, headers=auth(key)).json()
    correction = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "expected_revision": current["memory"]["revision"],
        "question": "Timeout",
        "content": "Configured timeout is TIMEOUT = 30",
        "sources": [snapshot(repo, "build.py").model_dump()],
    }
    corrected = api.post("/memory/correct", json=correction, headers=auth(key))
    assert corrected.status_code == 200
    replacement = corrected.json()["replacement_id"]
    assert replacement != trail.id
    assert (
        api.post("/memory/correct", json=correction, headers=auth(key)).json()["replacement_id"]
        == replacement
    )
    new_target = {"namespace": "team", "trail_id": replacement}
    state = api.post("/memory/inspect", json=new_target, headers=auth(key)).json()["memory"]
    change = {
        **new_target,
        "operation_id": uuid.uuid4().hex,
        "expected_revision": state["revision"],
        "action": "retract",
    }
    removed = api.post("/memory/change", json=change, headers=auth(key))
    assert removed.status_code == 200 and removed.json()["memory"]["state"] == "retracted"
    restore = {
        **change,
        "operation_id": uuid.uuid4().hex,
        "action": "restore",
        "expected_revision": removed.json()["memory"]["revision"],
    }
    restored = api.post("/memory/change", json=restore, headers=auth(key))
    assert restored.status_code == 200 and restored.json()["memory"]["state"] == "active"
    old_replay = api.post("/memory/change", json=change, headers=auth(key))
    assert old_replay.status_code == 200 and old_replay.json()["replayed"]
    assert old_replay.json()["memory"]["state"] == "active"


def test_memory_http_reader_contributor_author_and_namespace_boundaries(team):
    _, api, repo, _, key = team
    trail = seeded(team)
    target = {"namespace": "team", "trail_id": trail.id}
    reader = another_key(team)
    contributor = another_key(team, member="contributor", push=True)
    assert api.post("/memory/inspect", json=target, headers=auth(reader)).status_code == 200
    feedback = {**target, "operation_id": uuid.uuid4().hex, "verdict": "outdated"}
    assert api.post("/memory/feedback", json=feedback, headers=auth(reader)).status_code == 403
    assert api.post("/memory/feedback", json=feedback, headers=auth(contributor)).status_code == 200
    change = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "expected_revision": 0,
        "action": "retract",
    }
    assert api.post("/memory/change", json=change, headers=auth(contributor)).status_code == 403
    correction = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "expected_revision": 0,
        "question": "q",
        "content": "c",
        "sources": [snapshot(repo, "build.py").model_dump()],
    }
    assert (
        api.post("/memory/correct", json=correction, headers=auth(contributor)).status_code == 403
    )
    outside = {**target, "namespace": "private"}
    assert api.post("/memory/inspect", json=outside, headers=auth(key)).status_code == 404
    assert (
        api.post(
            "/memory/inspect", json={**target, "trail_id": "f" * 32}, headers=auth(key)
        ).status_code
        == 404
    )
    assert api.post("/memory/inspect", json=target).status_code == 401


def test_memory_http_validation_and_conflict_errors_never_echo_input(team):
    _, api, _, _, key = team
    trail = seeded(team)
    target = {"namespace": "team", "trail_id": trail.id}
    bad = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "verdict": "incorrect",
        "note": {"password": "private-input-sentinel"},
    }
    response = api.post("/memory/feedback", json=bad, headers=auth(key))
    assert response.status_code == 422 and response.json() == {"detail": "Invalid request"}
    assert "private-input-sentinel" not in response.text
    change = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "action": "retract",
        "expected_revision": 999,
    }
    conflict = api.post("/memory/change", json=change, headers=auth(key))
    assert conflict.status_code == 409 and conflict.json() == {
        "detail": "Memory operation conflict"
    }
    invalid = {
        **target,
        "operation_id": uuid.uuid4().hex,
        "expected_revision": 0,
        "question": "q",
        "content": "c",
        "sources": [{"ref": "../private-input-sentinel", "content_hash": "a" * 64}],
    }
    response = api.post("/memory/correct", json=invalid, headers=auth(key))
    assert response.status_code == 422 and "private-input-sentinel" not in response.text


@pytest.mark.parametrize("method", ["feedback", "correct", "change", "inspect"])
def test_memory_client_online_only_unknown_outcome_and_no_push_queue(
    local_identity, monkeypatch, method
):
    client = local_identity
    recorded = []

    def offline(route, payload):
        recorded.append((route, payload))
        raise httpx.ReadTimeout("private-transport-sentinel")

    monkeypatch.setattr(client, "request", offline)
    target = "a" * 32
    with pytest.raises(MemoryUnavailable) as caught:
        if method == "feedback":
            client.memory_feedback(target, "team", verdict="helpful", note="password=sentinel")
        elif method == "correct":
            client.memory_correct(
                target,
                "team",
                expected_revision=0,
                question="q",
                content="c",
                sources=[{"ref": "file.py", "content_hash": "a" * 64}],
            )
        elif method == "change":
            client.memory_change(target, "team", expected_revision=0, action="retract")
        else:
            client.memory_inspect(target, "team")
    assert len(recorded) == 1 and "private-transport" not in str(caught.value)
    assert not (client.home / "queue").exists()
    if method != "inspect":
        operation = recorded[0][1]["operation_id"]
        assert caught.value.operation_id == operation and len(operation) == 32
        assert operation in str(caught.value)
    else:
        assert caught.value.operation_id is None
    assert "sentinel" not in json.dumps(recorded)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, MemoryForbidden),
        (403, MemoryForbidden),
        (404, MemoryNotFound),
        (409, MemoryConflict),
        (422, MemoryInvalid),
    ],
)
def test_memory_client_safe_http_errors_and_explicit_operation_replay(
    local_identity, monkeypatch, status, error
):
    payloads = []

    def denied(url, **kwargs):
        payloads.append(kwargs["json"])
        return httpx.Response(
            status, json={"detail": "private-response-sentinel"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", denied)
    operation = "b" * 32
    for _ in range(2):
        with pytest.raises(error) as caught:
            local_identity.memory_feedback(
                "a" * 32,
                "team",
                verdict="incorrect",
                note="password=sentinel",
                operation_id=operation,
            )
        assert "sentinel" not in str(caught.value)
    assert payloads[0] == payloads[1] and payloads[0]["operation_id"] == operation
    assert payloads[0]["note"] == "[REDACTED]"


def test_memory_client_safe_validation_and_local_scope_denial(local_identity, monkeypatch):
    def forbidden_network(*args, **kwargs):
        pytest.fail("Invalid or unauthorized memory request must not be sent")

    monkeypatch.setattr(local_identity, "request", forbidden_network)
    with pytest.raises(MemoryInvalid) as caught:
        local_identity.memory_feedback("private-input-sentinel", "team", verdict="helpful")
    assert "sentinel" not in str(caught.value)
    local_identity.keyfile.grant.scopes[0].push = False
    with pytest.raises(MemoryForbidden):
        local_identity.memory_change("a" * 32, "team", expected_revision=0, action="retract")


@pytest.mark.parametrize("response_kind", ["server_error", "invalid_acknowledgment"])
def test_memory_client_unconfirmed_ack_is_unknown_and_does_not_touch_existing_queue(
    local_identity, monkeypatch, response_kind
):
    client = local_identity
    queue = client.home / "queue"
    queue.mkdir()
    existing = queue / ("c" * 32 + ".enc")
    existing.write_bytes(b"existing-push-queue-sentinel")

    def post(url, **kwargs):
        if response_kind == "server_error":
            return httpx.Response(
                503, text="private-response-sentinel", request=httpx.Request("POST", url)
            )
        return httpx.Response(
            200, text="invalid private-response-sentinel", request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(MemoryUnavailable) as caught:
        client.memory_change(
            "a" * 32, "team", expected_revision=0, action="retract", operation_id="b" * 32
        )
    assert caught.value.operation_id == "b" * 32
    assert "sentinel" not in str(caught.value)
    assert list(queue.iterdir()) == [existing]
    assert existing.read_bytes() == b"existing-push-queue-sentinel"


@pytest.mark.parametrize(
    "method,defect",
    [
        ("feedback", "empty"),
        ("feedback", "null"),
        ("feedback", "wrong_target"),
        ("feedback", "wrong_operation"),
        ("feedback", "missing_memory"),
        ("feedback", "invalid_state"),
        ("feedback", "bool_revision"),
        ("feedback", "negative_revision"),
        ("feedback", "invalid_replacement"),
        ("feedback", "bool_feedback"),
        ("feedback", "missing_feedback_count"),
        ("feedback", "missing_replayed"),
        ("feedback", "integer_replayed"),
        ("feedback", "wrong_verdict"),
        ("change", "wrong_action"),
        ("correct", "missing_replacement"),
        ("correct", "same_replacement"),
        ("inspect", "null"),
        ("inspect", "wrong_namespace"),
        ("inspect", "missing_content"),
        ("inspect", "invalid_sources"),
        ("inspect", "invalid_history"),
    ],
)
def test_memory_client_rejects_malformed_json_ack_and_preserves_replay_id(
    local_identity, monkeypatch, method, defect
):
    sent = []
    malformed = True

    def post(url, *, json, **kwargs):
        sent.append(json)
        route = httpx.URL(url).path
        result = memory_acknowledgment(route, json)
        if not malformed:
            if method != "inspect":
                result["replayed"] = True
            return httpx.Response(200, json=result, request=httpx.Request("POST", url))
        if defect == "empty":
            result = {}
        elif defect == "null":
            result = None
        elif defect == "wrong_target":
            result["id"] = "e" * 32
        elif defect == "wrong_operation":
            result["operation_id"] = "e" * 32
        elif defect == "missing_memory":
            del result["memory"]
        elif defect == "invalid_state":
            result["memory"]["state"] = {"private-response-sentinel": True}
        elif defect == "bool_revision":
            result["memory"]["revision"] = True
        elif defect == "negative_revision":
            result["memory"]["revision"] = -1
        elif defect == "invalid_replacement":
            result["memory"]["replacement_id"] = "private-response-sentinel"
        elif defect == "bool_feedback":
            result["memory"]["feedback"]["helpful"] = True
        elif defect == "missing_feedback_count":
            del result["memory"]["feedback"]["incorrect"]
        elif defect == "missing_replayed":
            del result["replayed"]
        elif defect == "integer_replayed":
            result["replayed"] = 1
        elif defect == "wrong_verdict":
            result["verdict"] = "outdated"
        elif defect == "wrong_action":
            result["action"] = "restore"
        elif defect == "missing_replacement":
            del result["replacement_id"]
        elif defect == "same_replacement":
            result["replacement_id"] = json["trail_id"]
        elif defect == "wrong_namespace":
            result["namespace"] = "private"
        elif defect == "missing_content":
            del result["content"]
        elif defect == "invalid_sources":
            result["sources"] = [{"ref": "private-response-sentinel"}]
        elif defect == "invalid_history":
            result["history"] = ["private-response-sentinel"]
        if result is None:
            return httpx.Response(200, content=b"null", request=httpx.Request("POST", url))
        return httpx.Response(200, json=result, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)

    def call(operation_id=None):
        if method == "inspect":
            return local_identity.memory_inspect("a" * 32, "team")
        kwargs = {"operation_id": operation_id}
        if method == "feedback":
            return local_identity.memory_feedback("a" * 32, "team", verdict="helpful", **kwargs)
        if method == "change":
            return local_identity.memory_change(
                "a" * 32, "team", expected_revision=0, action="retract", **kwargs
            )
        return local_identity.memory_correct(
            "a" * 32,
            "team",
            expected_revision=0,
            question="q",
            content="c",
            sources=[{"ref": "file.py", "content_hash": "a" * 64}],
            **kwargs,
        )

    with pytest.raises(MemoryUnavailable) as caught:
        call()
    operation_id = sent[0].get("operation_id")
    assert caught.value.operation_id == operation_id
    assert "sentinel" not in str(caught.value)
    assert not (local_identity.home / "queue").exists()
    malformed = False
    result = call(operation_id)
    assert result["id"] == "a" * 32 and sent[0] == sent[1]
    if method != "inspect":
        assert result["operation_id"] == operation_id and result["replayed"]


def test_memory_client_accepts_additive_ack_fields(local_identity, monkeypatch):
    def post(url, *, json, **kwargs):
        result = memory_acknowledgment(httpx.URL(url).path, json)
        result["future_extension"] = {"kept": True}
        result["memory"]["future_extension"] = "also kept"
        return httpx.Response(200, json=result, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    result = local_identity.memory_feedback("a" * 32, "team", verdict="helpful")
    assert result["future_extension"] == {"kept": True}
    assert result["memory"]["future_extension"] == "also kept"
