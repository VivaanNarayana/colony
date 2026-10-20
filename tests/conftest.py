import time

import pytest
from fastapi.testclient import TestClient

from colony.models import Grant, Scope
from colony.security import generate_keys, issue, token
from colony.server import create_app


@pytest.fixture
def team(tmp_path):
    admin = tmp_path / "admin"
    generate_keys(admin)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.py").write_text("TIMEOUT = 30\ndef build():\n    return True\n")
    app = create_app(tmp_path / "data", repo, admin / "issuer.pub")
    grant = Grant(
        member_id="a",
        name="Agent A",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", push=True, pull=True)],
    )
    key = issue(admin / "issuer.pem", grant)
    return app, TestClient(app), repo, admin, key


def auth(key):
    return {"Authorization": f"Bearer {token(key)}"}
