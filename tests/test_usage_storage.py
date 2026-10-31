import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from colony import usage
from colony.usage import UsageJournal


def record(journal, **kwargs):
    return journal.record(operation="lookup", outcome="answer", duration_ms=2.5, **kwargs)


def snapshot(directory):
    return {
        str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_default_opt_out_never_creates_home_or_files(tmp_path):
    home = tmp_path / "missing" / "home"
    journal = UsageJournal(home)
    assert journal.status()["enabled"] is False
    assert record(journal, logical_id="secret") is None
    assert journal.events() == []
    assert journal.disable()["enabled"] is False
    assert journal.purge()["purged"] == 0
    assert not home.exists()


def test_disabled_record_reads_metadata_only_and_does_not_write(tmp_path, monkeypatch):
    journal = UsageJournal(tmp_path)
    journal.enable()
    record(journal)
    journal.disable()
    before = snapshot(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled recorder must not open key or event contents")

    monkeypatch.setattr(journal, "_cipher", forbidden)
    monkeypatch.setattr(journal, "_decoded", forbidden)
    assert record(journal) is None
    assert snapshot(tmp_path) == before


def test_encryption_strict_event_fields_and_private_correlation(tmp_path):
    journal = UsageJournal(tmp_path / "a")
    journal.enable()
    sensitive = "private-namespace/path/question/answer/identity/error"
    first = record(journal, logical_id=sensitive)
    second = record(journal, logical_id=sensitive)
    events = journal.events()
    assert first != second
    assert len(events) == 2
    assert set(events[0]) == {
        "event_id",
        "timestamp",
        "operation",
        "outcome",
        "duration_ms",
        "logical_id",
        "assessment",
    }
    mac = events[0]["logical_id"]
    assert len(mac) == 64 and mac == events[1]["logical_id"]
    assert mac != hashlib.sha256(sensitive.encode()).hexdigest()
    other = UsageJournal(tmp_path / "b")
    other.enable()
    record(other, logical_id=sensitive)
    assert other.events()[0]["logical_id"] != mac
    for path in journal.directory.iterdir():
        content = path.read_bytes()
        assert sensitive.encode() not in content
        assert first.encode() not in content
        assert mac.encode() not in content
        assert b'"operation"' not in content
    if os.name != "nt":
        assert journal.directory.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in journal.directory.iterdir())


def test_restart_concurrent_append_and_review(tmp_path):
    UsageJournal(tmp_path).enable()

    def append(i):
        journal = UsageJournal(tmp_path)
        event_id = record(journal, logical_id=str(i % 3))
        if event_id:
            assert journal.review(event_id, "useful")["assessment"] == "useful"
        return event_id

    with ThreadPoolExecutor(max_workers=6) as executor:
        ids = list(executor.map(append, range(24)))
    restarted = UsageJournal(tmp_path)
    events = restarted.events()
    assert events
    assert {event["event_id"] for event in events} == set(ids) - {None}
    assert all(event["assessment"] == "useful" for event in events)
    assert restarted.status()["health"] == ("degraded" if None in ids else "healthy")


def test_cross_process_append(tmp_path):
    UsageJournal(tmp_path).enable()
    code = """from pathlib import Path
import sys
from colony.usage import UsageJournal
journal = UsageJournal(Path(sys.argv[1]))
ids = []
for _ in range(8):
    event_id = journal.record(operation='retry', outcome='accepted', duration_ms=1)
    if event_id:
        ids.append(event_id)
print(','.join(ids))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    ids = set()
    for process in processes:
        output, error = process.communicate(timeout=45)
        assert process.returncode == 0, error.decode()
        ids.update(filter(None, output.decode().strip().split(",")))
    restarted = UsageJournal(tmp_path)
    assert ids
    assert {event["event_id"] for event in restarted.events()} == ids
    assert restarted.status()["health"] == ("degraded" if len(ids) < 24 else "healthy")


def test_event_cap_and_retention_including_exact_cutoff(tmp_path, monkeypatch):
    now = 2_000_000.0
    monkeypatch.setattr(usage.time, "time", lambda: now)
    journal = UsageJournal(tmp_path)
    journal.enable(retention_days=1, max_events=3)
    ids = [record(journal) for _ in range(5)]
    assert [event["event_id"] for event in journal.events()] == ids[-3:]
    assert journal.status()["capacity_removed_count"] == 2
    now += 86400
    assert len(journal.events()) == 3  # The exact retention boundary is included.
    now += 0.001
    assert journal.events() == []
    assert record(journal)
    status = journal.status()
    assert status["expired_count"] == 3
    assert status["event_count"] == 1
    with sqlite3.connect(journal.database) as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_date_ranges_are_inclusive_since_exclusive_until(tmp_path, monkeypatch):
    journal = UsageJournal(tmp_path)
    journal.enable()
    now = 2_000_000.0
    monkeypatch.setattr(usage.time, "time", lambda: now)
    first = record(journal)
    now += 1
    second = record(journal)
    now += 1
    record(journal)
    assert [e["event_id"] for e in journal.events(since=now - 2, until=now)] == [first, second]
    assert journal.events(since=now, until=now) == []
    for kwargs in (
        {"since": float("nan")},
        {"until": float("inf")},
        {"since": -1},
        {"since": now, "until": now - 1},
    ):
        with pytest.raises(ValueError, match="Invalid usage date range"):
            journal.events(**kwargs)


def test_disk_bound_rejects_record_without_breaking_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "_MAX_PAGES", 4)
    journal = UsageJournal(tmp_path)
    journal.enable()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = [record(journal) for _ in range(30)]
    assert any(result is None for result in results)
    assert all(str(item.message) == usage._SAFE_WARNING for item in caught)
    assert journal.database.stat().st_size <= 4 * usage._PAGE_SIZE
    assert sum(p.stat().st_size for p in journal.directory.iterdir()) <= 4 * usage._PAGE_SIZE + 4096
    assert journal.status()["health"] == "degraded"
    assert UsageJournal(tmp_path).status()["gap"] is True


def test_failure_marker_survives_restart_and_warnings_are_safe(tmp_path, monkeypatch):
    journal = UsageJournal(tmp_path)
    journal.enable()

    def broken(*args, **kwargs):
        raise OSError("private filesystem path, question, token")

    monkeypatch.setattr(journal, "_encode", broken)
    with pytest.warns(
        RuntimeWarning, match="^Local usage recording failed; reports may have gaps.$"
    ):
        assert record(journal) is None
    status = UsageJournal(tmp_path).status()
    assert status["health"] == "degraded" and status["gap"]
    assert "private" not in json.dumps(status)
    assert status["event_count"] == 0


def test_unwritable_marker_reports_unknown_and_warning_filter_never_raises(tmp_path, monkeypatch):
    journal = UsageJournal(tmp_path)
    journal.enable()
    monkeypatch.setattr(
        journal, "_private_file", lambda *args: (_ for _ in ()).throw(OSError("secret"))
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert journal.record(operation="private question", outcome="answer", duration_ms=1) is None
    assert journal.status()["health"] == "unknown"
    assert not (journal.directory / "gap.flag").exists()


def test_record_drops_promptly_when_another_writer_holds_lock(tmp_path):
    journal = UsageJournal(tmp_path)
    journal.enable()
    with journal._lock(), pytest.warns(RuntimeWarning):
        start = time.monotonic()
        assert record(UsageJournal(tmp_path)) is None
        elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert journal.status()["gap"] is True
    assert journal.events() == []


def test_database_lock_wait_is_short_and_failed_append_is_atomic(tmp_path):
    journal = UsageJournal(tmp_path)
    journal.enable()
    connection = sqlite3.connect(journal.database)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        with pytest.warns(RuntimeWarning):
            assert record(journal) is None
        assert time.monotonic() - start < 0.5
    finally:
        connection.close()
    assert journal.events() == []
    assert journal.status()["health"] == "degraded"


def test_note_gap_preserves_opt_out_and_disabled_storage(tmp_path):
    home = tmp_path / "client"
    journal = UsageJournal(home)
    assert journal.note_gap() is None
    assert not home.exists()
    journal.enable()
    journal.disable()
    before = snapshot(home)
    assert journal.note_gap() is None
    assert snapshot(home) == before


def test_note_gap_persists_enabled_observer_failure_across_restart(tmp_path):
    journal = UsageJournal(tmp_path)
    journal.enable()
    with pytest.warns(
        RuntimeWarning, match="^Local usage recording failed; reports may have gaps.$"
    ):
        assert journal.note_gap() is None
    assert UsageJournal(tmp_path).status()["health"] == "degraded"
    assert journal.events() == []


def test_note_gap_malformed_settings_stay_unknown(tmp_path):
    journal = UsageJournal(tmp_path)
    journal.enable()
    with sqlite3.connect(journal.database) as connection:
        connection.execute("UPDATE settings SET enabled=9")
    with pytest.warns(RuntimeWarning):
        assert journal.note_gap() is None
    assert UsageJournal(tmp_path).status()["health"] == "unknown"
    assert (journal.directory / "gap.flag").is_file()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation": "sensitive arbitrary text", "outcome": "answer", "duration_ms": 1},
        {"operation": "lookup", "outcome": "sensitive arbitrary text", "duration_ms": 1},
        {"operation": "lookup", "outcome": "answer", "duration_ms": float("nan")},
        {"operation": "lookup", "outcome": "answer", "duration_ms": -1},
        {
            "operation": "lookup",
            "outcome": "answer",
            "duration_ms": 1,
            "logical_id": {"secret": "question"},
        },
    ],
)
def test_invalid_record_never_persists_arbitrary_fields(tmp_path, kwargs):
    journal = UsageJournal(tmp_path)
    journal.enable()
    with pytest.warns(RuntimeWarning):
        assert journal.record(**kwargs) is None
    assert journal.events() == []
    assert all(
        b"sensitive" not in p.read_bytes() and b"question" not in p.read_bytes()
        for p in journal.directory.iterdir()
    )


def test_review_eligibility_and_purge_keep_configuration_and_other_files(tmp_path):
    journal = UsageJournal(tmp_path)
    journal.enable(retention_days=7, max_events=20)
    answer = record(journal)
    evidence = journal.record(operation="lookup", outcome="evidence", duration_ms=1)
    ineligible = [
        journal.record(operation=operation, outcome=outcome, duration_ms=1)
        for operation, outcome in [
            ("lookup", "miss"),
            ("retry", "answer"),
            ("feedback", "accepted"),
        ]
    ]
    assert journal.review(answer, "incorrect")["assessment"] == "incorrect"
    assert journal.review(evidence, "useful")["assessment"] == "useful"
    for event_id in [*ineligible, "0" * 32]:
        with pytest.raises(ValueError, match="existing retained lookup"):
            journal.review(event_id, "irrelevant")
    with pytest.raises(ValueError, match="Invalid usage review"):
        journal.review(answer, "raw feedback text")
    (tmp_path / "identity.json").write_text("keep")
    (tmp_path / "queue.key").write_text("keep")
    journal.disable()
    key = (journal.directory / "journal.key").read_bytes()
    result = journal.purge()
    assert result["purged"] == 5 and result["enabled"] is False
    assert result["retention_days"] == 7 and result["max_events"] == 20
    assert journal.events() == []
    assert (journal.directory / "journal.key").read_bytes() == key
    assert (tmp_path / "identity.json").read_text() == "keep"
    assert (tmp_path / "queue.key").read_text() == "keep"
    with pytest.raises(ValueError):
        journal.review(answer, "useful")


@pytest.mark.parametrize(
    "corruption",
    ["version", "settings", "schema", "key", "missing_key", "ciphertext", "event_schema"],
)
def test_corruption_fails_safe_and_enable_does_not_replace_data(tmp_path, corruption):
    journal = UsageJournal(tmp_path)
    journal.enable()
    record(journal)
    key_path = journal.directory / "journal.key"
    if corruption == "key":
        key_path.write_bytes(b"bad secret key")
    elif corruption == "missing_key":
        key_path.unlink()
    else:
        with sqlite3.connect(journal.database) as connection:
            if corruption == "version":
                connection.execute("PRAGMA user_version=999")
            elif corruption == "settings":
                connection.execute("UPDATE settings SET max_events='bad'")
            elif corruption == "schema":
                connection.execute("CREATE TABLE unexpected (payload TEXT)")
            elif corruption == "ciphertext":
                connection.execute("UPDATE events SET payload=?", (b"private broken event",))
            else:
                cipher = Fernet(key_path.read_bytes())
                connection.execute(
                    "UPDATE events SET payload=?", (cipher.encrypt(b'{"question":"secret"}'),)
                )
    original_db = journal.database.read_bytes()
    original_key = key_path.read_bytes() if key_path.exists() else None
    assert journal.status()["health"] == "unknown"
    with pytest.raises(ValueError, match="^Local usage journal is unavailable.$"):
        journal.events()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        assert record(journal) is None
        with pytest.raises(ValueError, match="^Local usage journal is unavailable.$"):
            journal.enable()
    assert journal.database.read_bytes() == original_db
    assert (key_path.read_bytes() if key_path.exists() else None) == original_key


def test_abandoned_initialization_is_not_silently_reinitialized(tmp_path):
    (tmp_path / "usage").mkdir()
    journal = UsageJournal(tmp_path)
    with pytest.warns(RuntimeWarning), pytest.raises(ValueError, match="unavailable"):
        journal.enable()
    assert journal.status()["health"] == "unknown"
    assert not journal.database.exists()
    assert not (journal.directory / "journal.key").exists()


@pytest.mark.parametrize(
    "kwargs",
    [{"retention_days": 0}, {"retention_days": 366}, {"max_events": 10001}, {"max_events": True}],
)
def test_invalid_limits_do_not_initialize_storage(tmp_path, kwargs):
    journal = UsageJournal(tmp_path)
    with pytest.raises(ValueError, match="Usage limits"):
        journal.enable(**kwargs)
    assert not journal.directory.exists()


@pytest.mark.parametrize(
    "target", ["directory", "journal.key", "journal.sqlite3", "gap.flag", "journal.lock"]
)
def test_symlink_refused_without_touching_outside(tmp_path, target):
    home = tmp_path / "home"
    journal = UsageJournal(home)
    journal.enable()
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary"
    canary.write_bytes(b"untouched")
    link = journal.directory if target == "directory" else journal.directory / target
    if target == "directory":
        journal.directory.rename(home / "saved-usage")
        destination = outside
    else:
        if link.exists():
            link.unlink()
        destination = canary
    try:
        link.symlink_to(destination, target_is_directory=target == "directory")
    except OSError:
        pytest.skip("Host does not permit symlinks")
    before = snapshot(outside)
    assert journal.status()["health"] == "unknown"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        assert record(journal) is None
        for method in (journal.enable, journal.disable, journal.purge, journal.events):
            with pytest.raises(ValueError):
                method()
    assert snapshot(outside) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_windows_junction_refused(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (outside / "canary").write_text("untouched")
    link = home / "usage"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    if result.returncode:
        pytest.skip("Host does not permit junctions")
    try:
        journal = UsageJournal(home)
        assert journal.status()["health"] == "unknown"
        with pytest.warns(RuntimeWarning), pytest.raises(ValueError):
            journal.enable()
        assert list(outside.iterdir()) == [outside / "canary"]
    finally:
        # rmdir removes the junction itself, with no recursive traversal.
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows ancestor junction")
def test_windows_ancestor_junction_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked-parent"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    if result.returncode:
        pytest.skip("Host does not permit junctions")
    try:
        journal = UsageJournal(link / "not-yet-created-home")
        assert journal.status()["health"] == "unknown"
        with pytest.warns(RuntimeWarning), pytest.raises(ValueError):
            journal.enable()
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()


def test_hardlinked_key_refused(tmp_path):
    home = tmp_path / "home"
    journal = UsageJournal(home)
    journal.enable()
    outside = tmp_path / "outside-key"
    try:
        os.link(journal.directory / "journal.key", outside)
    except OSError:
        pytest.skip("Host does not permit hard links")
    before = outside.read_bytes()
    with pytest.warns(RuntimeWarning):
        assert record(journal) is None
    assert journal.status()["health"] == "unknown"
    assert outside.read_bytes() == before
