import time
import uuid

from conftest import auth

from colony.models import Scope, Trail
from colony.security import issue
from colony.validation import snapshot


def payload(repo, **changes):
    data = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Build timeout configuration",
        content="Use TIMEOUT = 30",
        sources=[snapshot(repo, "build.py")],
    ).model_dump()
    return {**data, **changes}


def test_core_loop_validates_each_pull_and_encrypts(team):
    app, api, repo, admin, key = team
    headers = auth(key)
    assert api.post("/join", headers=headers).status_code == 200
    data = payload(repo)
    assert api.post("/trails", json=data, headers=headers).status_code == 200
    # Independent read-only agent reuses A's answer.
    reader = issue(
        admin / "issuer.pem",
        key.grant.model_copy(
            update={"member_id": "b", "scopes": [Scope(namespace="team", pull=True)]}
        ),
    )

    def ask():
        return api.post("/ask", json={"question": data["question"]}, headers=auth(reader)).json()

    result = ask()
    assert result["hit"] and result["status"] == "validated" and result["tainted"]
    assert result["producer_id"] == "a" and "UNTRUSTED" in result["provenance"]
    assert data["content"].encode() not in next(app.state.store.blobs.glob("*.enc")).read_bytes()
    (repo / "build.py").write_text("TIMEOUT = 60\n")
    assert ask()["status"] == "stale"
    assert api.post("/trails", json=data, headers=auth(reader)).status_code == 403


def test_scope_filtering_before_ranking_and_explicit_denial(team):
    _, api, repo, admin, key = team
    api.post("/trails", json=payload(repo), headers=auth(key))
    outsider = issue(
        admin / "issuer.pem",
        key.grant.model_copy(
            update={"member_id": "outsider", "scopes": [Scope(namespace="private", pull=True)]}
        ),
    )
    assert not api.post(
        "/ask", json={"question": "Build timeout configuration"}, headers=auth(outsider)
    ).json()["hit"]
    assert (
        api.post(
            "/ask", json={"question": "Build", "namespace": "team"}, headers=auth(outsider)
        ).status_code
        == 403
    )
    assert (
        api.post("/trails", json=payload(repo, namespace="private"), headers=auth(key)).status_code
        == 403
    )
    # A closer inaccessible result must not suppress a permitted relevant result.
    both = issue(
        admin / "issuer.pem",
        key.grant.model_copy(
            update={
                "scopes": [Scope(namespace=n, push=True, pull=True) for n in ["team", "private"]]
            }
        ),
    )
    api.post("/trails", json=payload(repo, namespace="private"), headers=auth(both))
    assert (
        api.post(
            "/ask", json={"question": "Build timeout configuration"}, headers=auth(key)
        ).json()["namespace"]
        == "team"
    )


def test_forgery_expiry_capture_and_request_limits(team):
    _, api, repo, admin, key = team
    forged = key.model_copy(deep=True)
    forged.grant.scopes.append(Scope(namespace="secrets", pull=True))
    expired = issue(admin / "issuer.pem", key.grant.model_copy(update={"expires_at": 1}))
    for headers in [{}, {"Authorization": "Bearer !!!"}, auth(forged), auth(expired)]:
        assert api.post("/ask", json={"question": "hi"}, headers=headers).status_code == 401
    restricted = issue(admin / "issuer.pem", key.grant.model_copy(update={"capture": "none"}))
    assert (
        api.post(
            "/trails", json=payload(repo, capture="outputs"), headers=auth(restricted)
        ).status_code
        == 403
    )
    result = api.post("/trails", content=b"x" * 200001, headers=auth(key))
    assert result.status_code == 413
    result = api.post("/trails", json={"password": "do-not-echo"}, headers=auth(key))
    assert result.status_code == 422 and "do-not-echo" not in result.text


def test_idempotency_conflict_and_capture_record(team):
    app, api, repo, _, key = team
    data = payload(repo, capture="outputs")
    assert api.post("/trails", json=data, headers=auth(key)).json()["duplicate"] is False
    assert api.post("/trails", json=data, headers=auth(key)).json()["duplicate"] is True
    assert (
        api.post("/trails", json={**data, "content": "other"}, headers=auth(key)).status_code == 409
    )
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM captures").fetchone()[0] == 1


def test_misses_unsupported_evidence_and_age(team):
    app, api, repo, _, key = team
    data = payload(repo, sources=[])
    api.post("/trails", json=data, headers=auth(key))

    def ask(q):
        return api.post("/ask", json={"question": q}, headers=auth(key)).json()

    assert ask(data["question"])["status"] == "unverified"
    assert not ask("zoology aardvark migration")["hit"]
    second = payload(repo, question="Install package dependencies")
    api.post("/trails", json=second, headers=auth(key))
    with app.state.store.connect() as db:
        db.execute("UPDATE trails SET created_at=?", (time.time() - 900000,))
    result = ask(second["question"])
    assert result["age_flag"] and result["status"] == "unverified"
