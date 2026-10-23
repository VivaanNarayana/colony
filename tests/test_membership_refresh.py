"""Membership refresh preserves authorization while avoiding identical writes."""

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest
from conftest import auth

import colony.server
from colony.models import Scope
from colony.security import fingerprint, issue


def membership(store, member_id):
    with store.connect() as db:
        db.execute("BEGIN")
        member = db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        scopes = db.execute(
            "SELECT * FROM scopes WHERE member_id=? ORDER BY namespace", (member_id,)
        ).fetchall()
    return (tuple(member) if member else None, tuple(tuple(scope) for scope in scopes))


def expected_membership(key):
    grant = key.grant
    return (
        (grant.member_id, grant.name, "agent", fingerprint(key)),
        tuple(sorted((grant.member_id, s.namespace, int(s.push), int(s.pull)) for s in grant.scopes)),
    )


def record_membership_writes(monkeypatch, store):
    statements = []
    connect = store.connect

    def trace(statement):
        if re.match(r"\s*(?:INSERT|UPDATE|DELETE|REPLACE)\b", statement, re.IGNORECASE) and re.search(
            r"\b(?:members|scopes)\b", statement, re.IGNORECASE
        ):
            statements.append(statement)

    @contextmanager
    def traced_connect():
        with connect() as db:
            db.set_trace_callback(trace)
            yield db

    monkeypatch.setattr(store, "connect", traced_connect)
    return statements


def test_unchanged_authenticated_requests_verify_each_time_without_membership_dml(team, monkeypatch):
    app, api, _, _, key = team
    assert api.post("/join", headers=auth(key)).status_code == 200
    before = membership(app.state.store, key.grant.member_id)
    statements = record_membership_writes(monkeypatch, app.state.store)
    verified = []
    verify = colony.server.verify

    def counted_verify(received, issuer):
        verified.append(received)
        return verify(received, issuer)

    monkeypatch.setattr(colony.server, "verify", counted_verify)
    for _ in range(2):
        response = api.post(
            "/ask", json={"question": "Archive rotation policy?"}, headers=auth(key)
        )
        assert response.status_code == 200
        assert response.json() == {"hit": False, "reason": "no_relevant_trail"}
    assert api.post("/join", headers=auth(key)).status_code == 200
    assert len(verified) == 3
    assert statements == []
    assert membership(app.state.store, key.grant.member_id) == before == expected_membership(key)


def test_changed_signed_grant_refreshes_member_hash_name_and_complete_scopes(team):
    app, api, _, admin, key = team
    assert api.post("/join", headers=auth(key)).status_code == 200
    changed = issue(
        admin / "issuer.pem",
        key.grant.model_copy(
            update={
                "name": "Archive operator",
                "scopes": [
                    Scope(namespace="reports", pull=True),
                    Scope(namespace="archives", push=True),
                ],
            }
        ),
    )
    assert api.post("/join", headers=auth(changed)).status_code == 200
    assert membership(app.state.store, key.grant.member_id) == expected_membership(changed)
    # The previous scope is gone, not merged into the refreshed grant.
    assert api.post(
        "/ask", json={"question": "Retention?", "namespace": "team"}, headers=auth(changed)
    ).status_code == 403


@pytest.mark.parametrize("invalid_kind", ["forged", "expired"])
def test_invalid_grants_cannot_mutate_existing_membership(team, monkeypatch, invalid_kind):
    app, api, _, admin, key = team
    assert api.post("/join", headers=auth(key)).status_code == 200
    before = membership(app.state.store, key.grant.member_id)
    statements = record_membership_writes(monkeypatch, app.state.store)
    if invalid_kind == "forged":
        invalid = key.model_copy(deep=True)
        invalid.grant.name = "Forged operator"
        invalid.grant.scopes.append(Scope(namespace="private", pull=True))
    else:
        invalid = issue(admin / "issuer.pem", key.grant.model_copy(update={"expires_at": 1}))
    assert api.post("/join", headers=auth(invalid)).status_code == 401
    assert statements == []
    assert membership(app.state.store, key.grant.member_id) == before


def test_duplicate_scope_grant_still_fails_atomically_instead_of_becoming_a_noop(team):
    app, api, _, admin, key = team
    assert api.post("/join", headers=auth(key)).status_code == 200
    before = membership(app.state.store, key.grant.member_id)
    duplicate = issue(
        admin / "issuer.pem", key.grant.model_copy(update={"scopes": key.grant.scopes * 2})
    )
    with pytest.raises(sqlite3.IntegrityError):
        app.state.store.register(duplicate)
    assert membership(app.state.store, key.grant.member_id) == before


def test_concurrent_refreshes_never_expose_mixed_member_and_scope_snapshots(team):
    app, api, _, admin, key = team
    assert api.post("/join", headers=auth(key)).status_code == 200
    alternatives = [
        issue(
            admin / "issuer.pem",
            key.grant.model_copy(
                update={"name": name, "scopes": [Scope(namespace=scope, pull=True)]}
            ),
        )
        for name, scope in [("Archive reader", "archives"), ("Report reader", "reports")]
    ]
    allowed = {expected_membership(k) for k in [key, *alternatives]}
    start = Barrier(3)

    def refresh(candidate):
        start.wait(timeout=10)
        for _ in range(3):
            app.state.store.register(candidate)

    def observe():
        start.wait(timeout=10)
        return [membership(app.state.store, key.grant.member_id) for _ in range(6)]

    with ThreadPoolExecutor(max_workers=3) as executor:
        writers = [executor.submit(refresh, candidate) for candidate in alternatives]
        snapshots = executor.submit(observe)
        for writer in writers:
            writer.result(timeout=15)
        assert all(snapshot in allowed for snapshot in snapshots.result(timeout=15))
    assert membership(app.state.store, key.grant.member_id) in {
        expected_membership(candidate) for candidate in alternatives
    }
