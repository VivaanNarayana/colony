import hashlib
import shutil
import sqlite3

from cryptography.fernet import Fernet

from colony.client import Client
from colony.models import MemoryCorrection, Source, Trail
from colony.security import private_write, token
from colony.store import Store
from colony.validation import snapshot


def test_preupgrade_database_blob_key_backup_roundtrip(team, tmp_path):
    app, _, repo, _, key = team
    store = app.state.store
    original = Trail(
        id="b" * 32, namespace="team", question="Build timeout",
        content="The build timeout is TIMEOUT = 30.", sources=[snapshot(repo, "build.py")],
    )
    store.push(original, key.grant)
    # Original tables/payloads are unchanged by the additive feature. Strip only
    # the feature tables to construct the pre-upgrade format for this fixture.
    with store.connect() as db:
        for table in (
            "memory_operations_v1", "memory_history_v1", "memory_feedback_v1",
            "memory_state_v1", "memory_schema",
        ):
            db.execute(f"DROP TABLE {table}")
    backup = tmp_path / "preupgrade-backup"
    backup.mkdir()
    with sqlite3.connect(store.directory / "colony.db") as source, sqlite3.connect(
        backup / "colony.db"
    ) as destination:
        source.backup(destination)  # Includes committed WAL data; never copy a live DB file.
    shutil.copytree(store.blobs, backup / "blobs")
    shutil.copy2(store.directory / "storage.key", backup / "storage.key")

    upgraded = Store(store.directory, repo)
    result = upgraded.correct_memory(MemoryCorrection(
        namespace="team", trail_id=original.id, operation_id="c" * 32, expected_revision=0,
        question=original.question, content="Build timeout is 30 seconds (TIMEOUT = 30).",
        sources=original.sources,
    ), key.grant)
    assert result["memory"]["state"] == "superseded"

    # Restore into a separate stopped-server directory, preserving the upgraded
    # fixture too. The old table layout and original ciphertext/key agree again.
    restored = tmp_path / "restored-preupgrade"
    shutil.copytree(backup, restored)
    with sqlite3.connect(restored / "colony.db") as db:
        assert db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE 'memory_%'"
        ).fetchone()[0] == 0
        rows = db.execute("SELECT id,content_ref,payload_hash FROM trails").fetchall()
    assert len(rows) == 1 and rows[0][0] == original.id
    assert rows[0][2] == hashlib.sha256(original.model_dump_json().encode()).hexdigest()
    cipher = Fernet((restored / "storage.key").read_bytes())
    assert cipher.decrypt((restored / "blobs" / rows[0][1]).read_bytes()).decode() == original.content
    reopened = Store(restored, repo)
    assert reopened.inspect_memory(original.id, "team", key.grant)["memory"] == {
        "state": "active", "revision": 0, "replacement_id": None,
        "feedback": {"helpful": 0, "incorrect": 0, "outdated": 0},
    }
    assert upgraded.inspect_memory(original.id, "team", key.grant)["memory"]["state"] == "superseded"


def test_raw_legacy_redaction_expansion_remains_inspectable(team, tmp_path, monkeypatch):
    app, api, _, admin, key = team
    original = Trail(
        id="f" * 32, namespace="team", question="token=a " * 250, content="Historical answer",
        sources=[Source(
            kind="doc", ref="token=a " * 128, symbol="token=a " * 25,
            contains="token=a " * 125, content_hash="a" * 64,
        )],
    )
    headers = {"Authorization": f"Bearer {token(key)}"}
    assert api.post("/trails", headers=headers, json=original.model_dump()).status_code == 200
    home = tmp_path / "inspection-home"
    private_write(home / "identity.json", key.model_dump_json().encode())
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    client = Client(home)

    def request(route, payload):
        response = api.post(route, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(client, "request", request)
    inspected = client.memory_inspect(original.id, "team")
    assert inspected["question_truncated"] and len(inspected["question"]) == 2000
    source = inspected["sources"][0]
    assert source["metadata_truncated"]
    assert len(source["ref"]) == 1024 and len(source["symbol"]) == 200
    assert len(source["contains"]) == 1000
    assert "token=a" not in str(inspected)
    assert inspected["memory"]["state"] == "active"
    with app.state.store.connect() as db:
        assert db.execute("SELECT question FROM trails WHERE id=?", (original.id,)).fetchone()[0] == original.question
        assert app.state.store.sources(db, original.id) == original.sources
