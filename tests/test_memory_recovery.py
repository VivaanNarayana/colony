import hashlib
import shutil
import sqlite3

from cryptography.fernet import Fernet

from colony.models import MemoryCorrection, Trail
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
