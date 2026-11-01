"""Opt-in, bounded local usage metadata. Never accepts questions or memory content.

The key and encrypted database live together under the supplied client's home;
this protects accidental disclosure of the journal, not a compromised account.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import time
import uuid
import warnings
from contextlib import closing, contextmanager, suppress
from pathlib import Path

from cryptography.fernet import Fernet

OPERATIONS = frozenset(
    {"lookup", "publish", "retry", "inspect", "feedback", "correct", "retract", "restore", "change"}
)
OUTCOMES = frozenset(
    {
        "answer",
        "evidence",
        "miss",
        "stale",
        "unverified",
        "unavailable",
        "denied",
        "error",
        "queued",
        "accepted",
        "unknown",
        "not_found",
        "conflict",
        "invalid",
    }
)
VERDICTS = frozenset({"useful", "incorrect", "irrelevant"})
_VERSION = 1
_MAX_PAGES = 2048
_PAGE_SIZE = 4096
_SAFE_ERROR = "Local usage journal is unavailable."
_SAFE_WARNING = "Local usage recording failed; reports may have gaps."
_FILES = {"journal.sqlite3", "journal.sqlite3-journal", "journal.key", "journal.lock", "gap.flag"}
_FIELDS = {
    "event_id",
    "timestamp",
    "operation",
    "outcome",
    "duration_ms",
    "logical_id",
    "assessment",
}


def _plain_int(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _hex(value, length):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def _eligible(event):
    return event["operation"] == "lookup" and event["outcome"] in {"answer", "evidence"}


class UsageJournal:
    """A local journal with fixed-size records, authenticated encryption and sticky gaps.

    ``record`` is deliberately best effort. Management/read methods return fixed
    errors rather than including potentially sensitive filesystem/SQLite details.
    Time ranges use inclusive ``since`` and exclusive ``until`` epoch seconds.
    Record lock waits are short and bounded; total recording time also includes
    authenticating all retained ciphertext, which grows with the event count.
    """

    def __init__(self, home: Path):
        self.home = Path(home)
        self.directory = self.home / "usage"
        self.database = self.directory / "journal.sqlite3"
        self._unknown = False
        self._warned = False

    def _guard(self):
        # lstat also recognizes Windows directory junctions/reparse points.
        for path in (*reversed(self.home.absolute().parents), self.home, self.directory):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(_SAFE_ERROR)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(_SAFE_ERROR)
        if self.directory.exists():
            for path in self.directory.iterdir():
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    if path.name == "journal.sqlite3-journal":
                        continue  # A concurrent SQLite commit removed its sidecar.
                    raise
                if (
                    path.name not in _FILES
                    or not stat.S_ISREG(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                    or info.st_nlink != 1
                ):
                    raise ValueError(_SAFE_ERROR)
                cap = (
                    _MAX_PAGES * _PAGE_SIZE + 65536
                    if path.name.startswith("journal.sqlite3")
                    else 4096
                )
                if info.st_size > cap:
                    raise ValueError(_SAFE_ERROR)

    def _private_file(self, path, data):
        self._guard()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)

    @contextmanager
    def _lock(self, *, create=False, timeout=1.0):
        self._guard()
        path = self.directory / "journal.lock"
        if create:
            with suppress(FileExistsError):
                self._private_file(path, b"0")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "r+b", buffering=0) as stream:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError(_SAFE_ERROR) from None
                    time.sleep(0.002)
            try:
                if stream.read() != b"0":
                    raise ValueError(_SAFE_ERROR)
                self._guard()
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _connection(self, *, write=False, timeout=0.25):
        self._guard()
        uri = self.database.absolute().as_uri() + ("?mode=rw" if write else "?mode=ro")
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            if write:
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute(f"PRAGMA max_page_count={_MAX_PAGES}")
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        finally:
            connection.close()

    def _settings(self, connection):
        if connection.execute("PRAGMA user_version").fetchone()[0] != _VERSION:
            raise ValueError(_SAFE_ERROR)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables != {"settings", "events"}:
            raise ValueError(_SAFE_ERROR)
        columns = {
            "settings": [
                "singleton",
                "enabled",
                "retention_days",
                "max_events",
                "gap",
                "expired_count",
                "capacity_removed_count",
            ],
            "events": ["sequence", "payload"],
        }
        for table, expected in columns.items():
            if [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] != expected:
                raise ValueError(_SAFE_ERROR)
        if connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')"
        ).fetchone()[0]:
            raise ValueError(_SAFE_ERROR)
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise ValueError(_SAFE_ERROR)
        if connection.execute("PRAGMA page_size").fetchone()[0] != _PAGE_SIZE:
            raise ValueError(_SAFE_ERROR)
        if connection.execute("PRAGMA page_count").fetchone()[0] > _MAX_PAGES:
            raise ValueError(_SAFE_ERROR)
        rows = connection.execute("SELECT * FROM settings").fetchall()
        if len(rows) != 1 or len(rows[0]) != 7:
            raise ValueError(_SAFE_ERROR)
        singleton, enabled, days, maximum, gap, expired, removed = rows[0]
        if (
            singleton != 1
            or not _plain_int(enabled, 0, 1)
            or not _plain_int(days, 1, 365)
            or not _plain_int(maximum, 1, 10000)
            or not _plain_int(gap, 0, 1)
            or not _plain_int(expired, 0, 2**63 - 1)
            or not _plain_int(removed, 0, 2**63 - 1)
        ):
            raise ValueError(_SAFE_ERROR)
        return {
            "enabled": bool(enabled),
            "retention_days": days,
            "max_events": maximum,
            "gap": bool(gap),
            "expired_count": expired,
            "capacity_removed_count": removed,
        }

    def _cipher(self):
        self._guard()
        key = (self.directory / "journal.key").read_bytes()
        if len(key) != 44 or base64.urlsafe_b64encode(base64.urlsafe_b64decode(key)) != key:
            raise ValueError(_SAFE_ERROR)
        return Fernet(key), key

    def _decoded(self, connection, cipher):
        rows = connection.execute(
            "SELECT sequence, payload FROM events ORDER BY sequence"
        ).fetchall()
        if len(rows) > 10000:
            raise ValueError(_SAFE_ERROR)
        events = []
        ids = set()
        for sequence, payload in rows:
            if not isinstance(payload, bytes) or len(payload) > 1024:
                raise ValueError(_SAFE_ERROR)
            event = json.loads(cipher.decrypt(payload))
            if (
                not isinstance(event, dict)
                or set(event) != _FIELDS
                or not _hex(event["event_id"], 32)
                or event["event_id"] in ids
                or not _number(event["timestamp"])
                or not _number(event["duration_ms"])
                or event["operation"] not in OPERATIONS
                or event["outcome"] not in OUTCOMES
                or event["logical_id"] is not None
                and not _hex(event["logical_id"], 64)
                or event["assessment"] is not None
                and event["assessment"] not in VERDICTS
                or event["assessment"] is not None
                and not _eligible(event)
            ):
                raise ValueError(_SAFE_ERROR)
            ids.add(event["event_id"])
            events.append((sequence, event))
        return events

    @staticmethod
    def _encode(cipher, event):
        token = cipher.encrypt(json.dumps(event, separators=(",", ":"), allow_nan=False).encode())
        if len(token) > 1024:
            raise ValueError(_SAFE_ERROR)
        return token

    def _prune(self, connection, settings, events, now, *, reserve=0):
        cutoff = now - settings["retention_days"] * 86400
        expired = [sequence for sequence, event in events if event["timestamp"] < cutoff]
        retained = [(sequence, event) for sequence, event in events if event["timestamp"] >= cutoff]
        excess = max(0, len(retained) + reserve - settings["max_events"])
        removed = [sequence for sequence, _ in retained[:excess]]
        connection.executemany(
            "DELETE FROM events WHERE sequence=?", [(n,) for n in expired + removed]
        )
        connection.execute(
            "UPDATE settings SET expired_count=expired_count+?, capacity_removed_count=capacity_removed_count+?",
            (len(expired), len(removed)),
        )

    def _failure(self):
        self._unknown = True
        try:
            self._guard()
            if self.database.is_file() and (self.directory / "journal.key").is_file():
                marker = self.directory / "gap.flag"
                try:
                    self._private_file(marker, b"usage-gap-v1\n")
                except FileExistsError:
                    if marker.read_bytes() != b"usage-gap-v1\n":
                        return
                self._unknown = False
        except Exception:
            pass
        if not self._warned:
            self._warned = True
            with suppress(Exception):
                warnings.warn(_SAFE_WARNING, RuntimeWarning, stacklevel=3)

    def _status(self, settings, count):
        marker = self.directory / "gap.flag"
        gap = settings["gap"] or marker.exists()
        if marker.exists() and marker.read_bytes() != b"usage-gap-v1\n":
            raise ValueError(_SAFE_ERROR)
        health = "unknown" if self._unknown else "degraded" if gap else "healthy"
        return {
            **settings,
            "event_count": count,
            "health": health,
            "gap": gap or self._unknown,
            "warning": _SAFE_WARNING if gap or self._unknown else None,
        }

    def note_gap(self):
        """Best-effort health marker for failures in client-side observation.

        Never initialize storage or write to a known-disabled journal. Existing
        malformed metadata remains unknown rather than silently healthy.
        """
        with suppress(Exception):
            self._guard()
            if not self.directory.exists():
                return
            with self._connection(timeout=0.025) as connection:
                if not self._settings(connection)["enabled"]:
                    return
        self._failure()

    def status(self):
        try:
            self._guard()
            if not self.directory.exists():
                return self._status(
                    {
                        "enabled": False,
                        "retention_days": 30,
                        "max_events": 10000,
                        "gap": False,
                        "expired_count": 0,
                        "capacity_removed_count": 0,
                    },
                    0,
                )
            with self._connection() as connection:
                settings = self._settings(connection)
                cipher, _ = self._cipher()
                events = self._decoded(connection, cipher)
            cutoff = time.time() - settings["retention_days"] * 86400
            return self._status(settings, sum(event["timestamp"] >= cutoff for _, event in events))
        except Exception:
            return {
                "enabled": False,
                "retention_days": None,
                "max_events": None,
                "event_count": None,
                "expired_count": None,
                "capacity_removed_count": None,
                "health": "unknown",
                "gap": True,
                "warning": _SAFE_ERROR,
            }

    def enable(self, *, retention_days=30, max_events=10000):
        if not _plain_int(retention_days, 1, 365) or not _plain_int(max_events, 1, 10000):
            raise ValueError("Usage limits must be 1..365 days and 1..10000 events.")
        try:
            self._guard()
            created = False
            try:
                self.directory.mkdir(parents=True, mode=0o700)
                created = True
            except FileExistsError:
                pass
            with self._lock(create=True):
                if created:
                    self._private_file(self.directory / "journal.key", Fernet.generate_key())
                    self._private_file(self.database, b"")
                    with closing(sqlite3.connect(self.database)) as connection, connection:
                        connection.execute(f"PRAGMA page_size={_PAGE_SIZE}")
                        connection.execute(f"PRAGMA max_page_count={_MAX_PAGES}")
                        connection.execute(f"PRAGMA user_version={_VERSION}")
                        connection.execute(
                            "CREATE TABLE settings (singleton INTEGER PRIMARY KEY, enabled INTEGER NOT NULL, retention_days INTEGER NOT NULL, max_events INTEGER NOT NULL, gap INTEGER NOT NULL, expired_count INTEGER NOT NULL, capacity_removed_count INTEGER NOT NULL)"
                        )
                        connection.execute(
                            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
                        )
                        connection.execute(
                            "INSERT INTO settings VALUES (1,1,?,?,0,0,0)",
                            (retention_days, max_events),
                        )
                with self._connection(write=True) as connection:
                    settings = self._settings(connection)
                    cipher, _ = self._cipher()
                    events = self._decoded(connection, cipher)
                    settings.update(retention_days=retention_days, max_events=max_events)
                    connection.execute(
                        "UPDATE settings SET enabled=1, retention_days=?, max_events=?",
                        (retention_days, max_events),
                    )
                    self._prune(connection, settings, events, time.time())
            return self.status()
        except Exception:
            self._failure()
            raise ValueError(_SAFE_ERROR) from None

    def disable(self):
        try:
            self._guard()
            if not self.directory.exists():
                return self.status()
            with self._lock(), self._connection(write=True) as connection:
                self._settings(connection)
                self._cipher()
                connection.execute("UPDATE settings SET enabled=0")
            return self.status()
        except Exception:
            raise ValueError(_SAFE_ERROR) from None

    def purge(self):
        try:
            self._guard()
            if not self.directory.exists():
                return {**self.status(), "purged": 0}
            with self._lock(), self._connection(write=True) as connection:
                self._settings(connection)
                self._cipher()
                count = connection.execute("SELECT count(*) FROM events").fetchone()[0]
                connection.execute("DELETE FROM events")
            return {**self.status(), "purged": count}
        except Exception:
            raise ValueError(_SAFE_ERROR) from None

    def events(self, *, since=None, until=None):
        if (
            since is not None
            and not _number(since)
            or until is not None
            and not _number(until)
            or since is not None
            and until is not None
            and since > until
        ):
            raise ValueError("Invalid usage date range.")
        try:
            self._guard()
            if not self.directory.exists():
                return []
            with self._connection() as connection:
                settings = self._settings(connection)
                cipher, _ = self._cipher()
                events = self._decoded(connection, cipher)
            cutoff = time.time() - settings["retention_days"] * 86400
            return [
                event
                for _, event in events
                if event["timestamp"] >= cutoff
                and (since is None or event["timestamp"] >= since)
                and (until is None or event["timestamp"] < until)
            ]
        except Exception:
            raise ValueError(_SAFE_ERROR) from None

    def record(self, *, operation, outcome, duration_ms, logical_id=None):
        try:
            self._guard()
            if not self.directory.exists():
                return None
            # A disabled recorder reads configuration only, without opening keys,
            # decrypting records, making lock files or performing any writes.
            with self._connection(timeout=0.025) as connection:
                if not self._settings(connection)["enabled"]:
                    return None
            if (
                operation not in OPERATIONS
                or outcome not in OUTCOMES
                or not _number(duration_ms)
                or logical_id is not None
                and not isinstance(logical_id, str)
            ):
                raise ValueError(_SAFE_ERROR)
            with (
                self._lock(timeout=0.125),
                self._connection(write=True, timeout=0.025) as connection,
            ):
                settings = self._settings(connection)
                if not settings["enabled"]:
                    return None
                cipher, key = self._cipher()
                events = self._decoded(connection, cipher)
                now = time.time()
                if not _number(now):
                    raise ValueError(_SAFE_ERROR)
                correlation = (
                    None
                    if logical_id is None
                    else hmac.new(
                        base64.urlsafe_b64decode(key),
                        b"colony-usage-logical-id-v1\x00" + logical_id.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                )
                event = {
                    "event_id": uuid.uuid4().hex,
                    "timestamp": now,
                    "operation": operation,
                    "outcome": outcome,
                    "duration_ms": float(duration_ms),
                    "logical_id": correlation,
                    "assessment": None,
                }
                self._prune(connection, settings, events, now, reserve=1)
                connection.execute(
                    "INSERT INTO events(payload) VALUES (?)", (self._encode(cipher, event),)
                )
            return event["event_id"]
        except Exception:
            self._failure()
            return None

    def review(self, event_id, verdict):
        if not _hex(event_id, 32) or not isinstance(verdict, str) or verdict not in VERDICTS:
            raise ValueError("Invalid usage review.")
        try:
            with self._lock(), self._connection(write=True) as connection:
                settings = self._settings(connection)
                cipher, _ = self._cipher()
                events = self._decoded(connection, cipher)
                cutoff = time.time() - settings["retention_days"] * 86400
                for sequence, event in events:
                    if (
                        event["event_id"] == event_id
                        and event["timestamp"] >= cutoff
                        and _eligible(event)
                    ):
                        event["assessment"] = verdict
                        connection.execute(
                            "UPDATE events SET payload=? WHERE sequence=?",
                            (self._encode(cipher, event), sequence),
                        )
                        return event
            raise ValueError(_SAFE_ERROR)
        except Exception:
            raise ValueError(
                "Usage review requires an existing retained lookup answer or evidence event."
            ) from None
