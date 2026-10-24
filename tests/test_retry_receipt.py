"""A successful HTTP exchange must not discard an unconfirmed queued save."""

import json
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from test_client import local_client

from colony.models import Trail
from colony.validation import snapshot


def queued(team, tmp_path, monkeypatch, count=1):
    _, _, repo, admin, key = team
    client = local_client(tmp_path, key, admin)

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", offline)
    trails = []
    for _ in range(count):
        trail = Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="What is the workshop build timeout?",
            content="The workshop build timeout is thirty seconds.",
            sources=[snapshot(repo, "build.py")],
            tainted=True,
        )
        assert client.push(trail)["queued"]
        trails.append(trail)
    trails.sort(key=lambda trail: trail.id)
    files = {trail.id: client.home / "queue" / f"{trail.id}.enc" for trail in trails}
    ciphertexts = {trail_id: path.read_bytes() for trail_id, path in files.items()}
    return client, trails, files, ciphertexts


@pytest.mark.parametrize(
    "case",
    ["empty", "list", "string", "null", "false", "string-flag", "number-flag",
     "queued", "queued-null", "queued-number", "error", "wrong-id", "missing-id"],
)
def test_invalid_success_receipt_retains_exact_ciphertext(team, tmp_path, monkeypatch, case):
    client, trails, files, ciphertexts = queued(team, tmp_path, monkeypatch)
    trail = trails[0]
    receipt = {"id": trail.id, "stored": True}
    overrides = {
        "false": {"stored": False}, "string-flag": {"stored": "true"},
        "number-flag": {"stored": 1}, "queued": {"queued": True},
        "queued-null": {"queued": None}, "queued-number": {"queued": 0},
        "error": {"error": "private receipt material"}, "wrong-id": {"id": uuid.uuid4().hex},
    }
    if case in overrides:
        receipt.update(overrides[case])
    if case in {"empty", "list", "string", "null"}:
        receipt = {"empty": {}, "list": [], "string": "private receipt material", "null": None}[case]
    if case == "missing-id":
        receipt.pop("id")

    def unconfirmed(url, **kwargs):
        return httpx.Response(200, content=json.dumps(receipt).encode(), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", unconfirmed)
    with pytest.raises(ValueError, match="Save acknowledgment not confirmed") as caught:
        client.retry()
    assert caught.value.sent == 0
    assert "private receipt material" not in str(caught.value)
    assert files[trail.id].read_bytes() == ciphertexts[trail.id]
    payload = json.loads(Fernet((client.home / "queue.key").read_bytes()).decrypt(ciphertexts[trail.id]))
    assert payload["trail"] == trail.model_dump()
    with team[0].state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM trails").fetchone()[0] == 0


@pytest.mark.parametrize("malformed_json", [False, True])
def test_partial_success_count_stops_before_later_encrypted_items(team, tmp_path, monkeypatch, malformed_json):
    client, trails, files, ciphertexts = queued(team, tmp_path, monkeypatch, count=3)
    _, api, _, _, _ = team
    submitted = []

    def post(url, **kwargs):
        submitted.append(kwargs["json"]["id"])
        if len(submitted) == 2:
            if malformed_json:
                return httpx.Response(200, content=b"private-invalid-json", request=httpx.Request("POST", url))
            return httpx.Response(200, json={"stored": False}, request=httpx.Request("POST", url))
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    expected = json.JSONDecodeError if malformed_json else ValueError
    with pytest.raises(expected) as caught:
        client.retry()
    if not malformed_json:
        assert caught.value.sent == 1
    assert submitted == [trail.id for trail in trails[:2]]
    assert not files[trails[0].id].exists()
    for trail in trails[1:]:
        assert files[trail.id].read_bytes() == ciphertexts[trail.id]
    with team[0].state.store.connect() as db:
        assert [row[0] for row in db.execute("SELECT id FROM trails")] == [trails[0].id]


@pytest.mark.parametrize("duplicate", [False, True])
def test_actual_matching_storage_receipt_releases_queue(team, tmp_path, monkeypatch, duplicate):
    client, trails, files, _ = queued(team, tmp_path, monkeypatch)
    app, api, _, _, key = team
    if duplicate:
        app.state.store.push(trails[0], key.grant)

    def online(url, **kwargs):
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", online)
    assert client.retry() == {"sent": 1, "pending": False}
    assert not files[trails[0].id].exists()


@pytest.mark.parametrize("status", [403, 409, 503])
def test_permanent_and_transport_failure_contracts_remain(team, tmp_path, monkeypatch, status):
    client, trails, files, ciphertexts = queued(team, tmp_path, monkeypatch)

    def rejected(url, **kwargs):
        return httpx.Response(status, json={"detail": "private error"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", rejected)
    if status == 503:
        assert client.retry() == {"sent": 0, "pending": True}
    else:
        with pytest.raises(httpx.HTTPStatusError):
            client.retry()
    assert files[trails[0].id].read_bytes() == ciphertexts[trails[0].id]
