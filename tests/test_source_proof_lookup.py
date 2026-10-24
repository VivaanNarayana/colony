"""Source-proof lookup keeps legacy row order and every advanced assertion."""

from colony.store import Store


def test_legacy_proof_rows_migrate_without_reordering_or_losing_assertions(team):
    app, _, repo, _, _ = team
    store = app.state.store
    rows = [
        ("chosen", "file", "policy.py", "a" * 64, "retention", "DAYS = 37"),
        ("other", "file", "private.py", "b" * 64, None, None),
        ("chosen", "url", "https://example.test/policy", "c" * 64, None, None),
        ("other", "file", "extra.py", "d" * 64, None, None),
        ("chosen", "file", "policy.py", "e" * 64, None, "REGION = 'west'"),
    ]
    with store.connect() as db:
        db.execute("DROP INDEX trail_sources_trail")
        db.executemany("INSERT INTO trail_sources VALUES (?,?,?,?,?,?)", rows)
        before = [s.model_dump() for s in Store.sources(db, "chosen")]
        assert Store.sources(db, "absent") == []
    reopened = Store(store.directory, repo)
    with reopened.connect() as db:
        after = [s.model_dump() for s in reopened.sources(db, "chosen")]
        assert after == before == [
            {"kind": kind, "ref": ref, "content_hash": digest, "symbol": symbol, "contains": contains}
            for trail, kind, ref, digest, symbol, contains in rows
            if trail == "chosen"
        ]
        assert reopened.sources(db, "absent") == []
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT kind,ref,content_hash,symbol,contains "
            "FROM trail_sources WHERE trail_id=? ORDER BY rowid", ("chosen",)
        ).fetchall()
        assert any("SEARCH trail_sources USING INDEX" in r[3] for r in plan)
        assert not any("TEMP B-TREE" in r[3] for r in plan)
    # Existing stores can reopen repeatedly without duplicating or losing proofs.
    again = Store(store.directory, repo)
    with again.connect() as db:
        assert [s.model_dump() for s in again.sources(db, "chosen")] == before
