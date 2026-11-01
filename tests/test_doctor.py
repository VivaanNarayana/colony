import json
import time

import httpx
import pytest

from colony import doctor
from colony.models import Grant, Scope
from colony.security import generate_keys, issue


def checks(report):
    return {check["name"]: check for check in report["checks"]}


@pytest.fixture
def identity(tmp_path):
    home = tmp_path / "home"
    generate_keys(home)
    grant = Grant(
        member_id="a",
        name="Private Name",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    (home / "identity.json").write_text(issue(home / "issuer.pem", grant).model_dump_json())
    return home


def test_default_does_not_create_home_or_use_network(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network"))
    missing = tmp_path / "missing"
    report = doctor.diagnose(missing)
    assert not report["ok"] and not missing.exists()
    assert checks(report)["identity"]["status"] == "fail"
    assert checks(report)["public_health"]["status"] == "skipped"
    assert checks(report)["models"]["status"] == "skipped"


def test_valid_identity_and_setup_status_are_read_only(identity, tmp_path, monkeypatch):
    from colony.client import Client
    from colony.store import Store

    monkeypatch.setattr(Client, "__init__", lambda *a, **k: pytest.fail("Client startup"))
    monkeypatch.setattr(Store, "__init__", lambda *a, **k: pytest.fail("Store startup"))
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network"))
    repo = tmp_path / "repo"
    repo.mkdir()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = doctor.diagnose(identity, repo=repo, namespace="team")
    assert report["ok"]
    assert checks(report)["identity"]["status"] == "pass"
    assert checks(report)["scopes"]["status"] == "pass"
    assert checks(report)["sqlite_vec"]["status"] == "pass"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert "Private Name" not in json.dumps(report)


@pytest.mark.parametrize("change", ["expired", "signature", "scope"])
def test_identity_expiry_signature_and_scope_checked(identity, change):
    document = json.loads((identity / "identity.json").read_text())
    if change == "expired":
        grant = Grant.model_validate(document["grant"])
        grant.expires_at = int(time.time()) - 1
        document = issue(identity / "issuer.pem", grant).model_dump()
    elif change == "signature":
        document["signature"] = "bad secret token"
    else:
        grant = Grant.model_validate(document["grant"])
        grant.scopes = [Scope(namespace="other", pull=True)]
        document = issue(identity / "issuer.pem", grant).model_dump()
    (identity / "identity.json").write_text(json.dumps(document))
    report = doctor.diagnose(identity, namespace="team")
    assert not report["ok"]
    assert checks(report)["scopes" if change == "scope" else "identity"]["status"] == "fail"
    assert "secret" not in json.dumps(report)


def test_models_fail_without_explicit_snapshots(identity, monkeypatch):
    monkeypatch.delenv("COLONY_EMBED_MODEL_DIR", raising=False)
    monkeypatch.delenv("COLONY_RANK_MODEL_DIR", raising=False)
    report = doctor.diagnose(identity, role="server")
    assert not report["ok"] and checks(report)["models"]["status"] == "fail"


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.test",
        "https://user:password@example.test",
        "https://example.test/?token=secret",
        "https://example.test/#secret",
        "https://example.test:invalid",
        "https:///missing",
    ],
)
def test_unsafe_health_urls_do_not_connect(identity, monkeypatch, url):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network"))
    report = doctor.diagnose(identity, server=url)
    assert checks(report)["public_health"]["status"] == "fail"
    assert "password" not in json.dumps(report) and "secret" not in json.dumps(report)


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (200, b'{"status":"ok"}', "pass"),
        (302, b"secret", "fail"),
        (200, b'{"status":"secret"}', "fail"),
        (200, b"x" * 5000, "fail"),
    ],
)
def test_explicit_public_health_has_no_auth_redirects_and_is_conservative(
    identity, monkeypatch, status, payload, expected
):
    requests, options = [], []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, content=payload, headers={"location": "https://evil.test"})

    original = httpx.Client

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(doctor.httpx, "Client", client)
    report = doctor.diagnose(identity, server="http://127.0.0.1:7331", namespace="team")
    assert len(requests) == 1
    assert requests[0].method == "GET" and requests[0].url.path == "/health"
    assert "authorization" not in requests[0].headers
    assert options == [{"trust_env": False, "follow_redirects": False, "timeout": 5}]
    result = checks(report)["public_health"]
    assert result["status"] == expected and "untested" in result["detail"]
    assert "secret" not in json.dumps(report)


def test_raw_exception_text_is_never_reported(identity, monkeypatch):
    monkeypatch.setattr(doctor, "verify", lambda *a: (_ for _ in ()).throw(ValueError("secret")))
    report = doctor.diagnose(identity)
    assert not report["ok"] and "secret" not in json.dumps(report)
