import json
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet

from colony.client import Client
from colony.models import Trail
from colony.security import issue, private_write


def local_client(tmp_path, key, admin):
    home = tmp_path / "agent"
    private_write(home / "identity.json", key.model_dump_json().encode())
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    return Client(home)


def test_offline_queue_scrub_encryption_retry_idempotent(team, tmp_path, monkeypatch):
    _, api, _, admin, key = team
    client = local_client(tmp_path, key, admin)

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", offline)
    assert client.ask("hello") == {"hit": False, "reason": "server_unavailable"}
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="password=superprivate",
        content="sk-proj-" + "a" * 40,
    )
    assert client.push(trail)["queued"]
    queue = next((client.home / "queue").glob("*.enc"))
    assert b"superprivate" not in queue.read_bytes()
    payload = json.loads(
        Fernet((client.home / "queue.key").read_bytes()).decrypt(queue.read_bytes())
    )
    assert payload["trail"]["content"] == "[REDACTED]"
    assert payload["trail"]["question"] == "[REDACTED]"
    assert client.retry() == {"sent": 0, "pending": True}

    def online(url, **kwargs):
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", online)
    assert client.retry() == {"sent": 1, "pending": False}
    assert not queue.exists()
    assert client.push(trail)["duplicate"]


def test_auth_errors_are_not_hidden_as_outages(team, tmp_path, monkeypatch):
    _, _, _, admin, key = team
    client = local_client(tmp_path, key, admin)

    def denied(*args, **kwargs):
        return httpx.Response(403, request=httpx.Request("POST", "http://localhost/ask"))

    monkeypatch.setattr(httpx, "post", denied)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask("hello")
    with pytest.raises(ValueError):
        client.push(Trail(id=uuid.uuid4().hex, namespace="private", question="q", content="c"))


def test_join_rejects_forgery_and_remote_plaintext_before_network(team, tmp_path, monkeypatch):
    _, _, _, admin, key = team

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid join must not contact any server")

    monkeypatch.setattr(httpx, "post", unexpected)
    bad = key.model_copy(deep=True)
    bad.grant.name = "forged"
    insecure = issue(
        admin / "issuer.pem", key.grant.model_copy(update={"server": "http://remote.example.com"})
    )
    home = tmp_path / "new-home"
    keyfile = tmp_path / "incoming.json"
    for candidate in (bad, insecure):
        keyfile.write_text(candidate.model_dump_json())
        with pytest.raises(ValueError):
            Client.join(home, keyfile, admin / "issuer.pub")
        assert not home.exists()
