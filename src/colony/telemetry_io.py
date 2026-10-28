"""Bounded metadata encoding and an explicit local sink. Nothing starts on import."""

import json
import os
import queue
import threading
import time
from pathlib import Path

RECORD_BYTES = 4096
FILE_BYTES = 5 * 1024 * 1024
FOOTER_BYTES = 16 * 1024
STAGES = ("answer", "evidence", "excerpt_claims", "excerpt_plain")
REASONS = (
    "manual",
    "time_limit",
    "ask_limit",
    "rank_limit",
    "sample_limit",
    "event_limit",
    "ask_event_limit",
    "context_limit",
    "queue_full",
    "byte_limit",
    "writer_error",
    "schema_error",
    "stage_error",
    "shape_limit",
    "observer_error",
    "cpu_limit",
    "rank_overhead",
    "ask_overhead",
    "contention",
    "backend_error",
    "nested_ask",
    "flush_timeout",
)
FIELDS = {
    "header": {
        "schema",
        "run",
        "source",
        "observer",
        "models",
        "python",
        "fastembed",
        "onnxruntime",
        "batch_limit",
        "max_tokens",
        "threads",
        "sample_percent",
    },
    "rank": {
        "ask",
        "ordinal",
        "stage",
        "n",
        "chars",
        "tokens",
        "nonpadding",
        "slots",
        "attempts",
        "batches",
        "token_state",
        "outcome",
        "wall_ns",
        "observer_ns",
    },
    "ask": {"ask", "outcome", "ranks", "nonempty", "multi", "wall_ns", "observer_ns"},
    "footer": {
        "reason",
        "asks",
        "finished",
        "raised",
        "selected",
        "admitted",
        "complete_samples",
        "censored",
        "censored_multi",
        "censored_unknown",
        "selected_unobserved",
        "rank_entries",
        "rank_finished",
        "rank_raised",
        "rank_censored",
        "widths",
        "ask_mix",
        "events",
        "unavailable",
        "observer_cpu_ns",
        "queued",
        "written",
        "lost",
        "sample_censored",
        "unobserved_asks",
        "rank_outcomes",
        "token_coverage",
        "selected_rank_stages",
        "unobserved_ranks",
        "unobserved_rank_stages",
        "sample_details_lost",
        "rank_details_lost",
        "sample_rank_censored",
    },
}
ENUMS = {
    "stage": STAGES,
    "token_state": ("ok", "partial", "unavailable", "not_applicable"),
    "outcome": ("completed", "raised", "censored"),
    "reason": REASONS,
}
HEX_FIELDS = {"run": (32,), "ask": (32,), "source": (40, 64), "observer": (64,), "models": (64,)}
HISTOGRAMS = {
    "chars": 7,
    "tokens": 8,
    "widths": 48,
    "ask_mix": 36,
    "rank_outcomes": 12,
    "token_coverage": 16,
    "selected_rank_stages": 4,
    "unobserved_rank_stages": 5,
}
VERSION_FIELDS = {"python", "fastembed", "onnxruntime"}


def encode(kind, fields, *, maximum=RECORD_BYTES):
    """Closed schema: no generic object, string or exception serialization."""
    if kind not in FIELDS or type(fields) is not dict or set(fields) != FIELDS[kind]:
        raise ValueError("Invalid metadata schema")
    for key, value in fields.items():
        if (
            value is None
            and kind == "rank"
            and fields["token_state"] == "unavailable"
            and key
            in {
                "tokens",
                "nonpadding",
                "slots",
                "attempts",
                "batches",
            }
        ):
            valid = True
        elif key in ENUMS:
            valid = type(value) is str and value in ENUMS[key]
        elif key in HEX_FIELDS:
            valid = (
                type(value) is str
                and len(value) in HEX_FIELDS[key]
                and all(c in "0123456789abcdef" for c in value)
            )
        elif key in VERSION_FIELDS:
            valid = (
                type(value) is str
                and 1 <= len(value) <= 64
                and value[0].isdigit()
                and all(c in "0123456789.abrc+-" for c in value)
            )
        elif key in HISTOGRAMS:
            valid = (
                type(value) is list
                and len(value) == HISTOGRAMS[key]
                and all(type(n) is int and 0 <= n < 2**64 for n in value)
            )
        else:
            valid = type(value) is int and 0 <= value < 2**64
        if not valid:
            raise ValueError("Invalid metadata field")
    data = (json.dumps({"kind": kind, **fields}, separators=(",", ":")) + "\n").encode()
    if len(data) > maximum:
        raise ValueError("Metadata record too large")
    return data


class JsonlSink:
    """Explicit construction only; never called by application startup or configuration.

    Caller must supply an existing private directory. No spill files, overwrite,
    network transport, or automatic deletion. A missing footer means incomplete.
    """

    def __init__(self, path: Path, *, byte_limit=FILE_BYTES, capacity=64):
        if not 1 <= capacity <= 64 or not FOOTER_BYTES + RECORD_BYTES <= byte_limit <= FILE_BYTES:
            raise ValueError("Invalid sink bounds")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        self._stream = os.fdopen(os.open(path, flags, 0o600), "wb", buffering=0)
        self._queue = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._footer = None
        self._deadline = None
        self._limit = byte_limit
        self._reserved = 0
        self.written = 0
        self.lost = 0
        self.cpu_ns = 0
        self.error = None
        self._thread = threading.Thread(target=self._write, daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._stream.close()
            raise

    def offer(self, data):
        if type(data) is not bytes or len(data) > RECORD_BYTES:
            return "schema_error"
        if not self._lock.acquire(blocking=False):
            return "contention"
        try:
            if self._closing.is_set() or self.error:
                return self.error or "writer_error"
            if self._reserved + len(data) > self._limit - FOOTER_BYTES:
                return "byte_limit"
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                return "queue_full"
            self._reserved += len(data)
            return None
        finally:
            self._lock.release()

    def finish(self, footer, *, timeout=1.0):
        if not 0 <= timeout <= 1:
            raise ValueError("Invalid flush grace")
        self._footer = footer
        self._deadline = time.monotonic() + timeout
        self._closing.set()
        self._thread.join(timeout)
        return not self._thread.is_alive() and self.error is None

    def _write(self):
        started = time.thread_time_ns()
        pending = False
        try:
            while True:
                self.cpu_ns = time.thread_time_ns() - started
                if self.cpu_ns > 2_000_000_000:
                    self.error = "cpu_limit"
                    break
                if self._closing.is_set() and time.monotonic() >= self._deadline:
                    self.lost += self._queue.qsize()
                    self.error = "flush_timeout"
                    break
                try:
                    data = self._queue.get(timeout=0.01)
                except queue.Empty:
                    if self._closing.is_set():
                        break
                    continue
                pending = True
                if self._stream.write(data) != len(data):
                    raise OSError("Partial metadata write")
                self.written += 1
                pending = False
            if self._closing.is_set() and not self.error:
                if time.monotonic() >= self._deadline:
                    self.error = "flush_timeout"
                    return
                self.cpu_ns = time.thread_time_ns() - started
                footer = self._footer(self.written, self.lost)
                if type(footer) is not bytes or len(footer) > FOOTER_BYTES:
                    raise ValueError("Invalid footer")
                before_footer = self._stream.tell()
                if self._stream.write(footer) != len(footer):
                    raise OSError("Partial footer write")
                if time.monotonic() >= self._deadline:
                    self.error = "flush_timeout"
                    self._stream.truncate(before_footer)
        except Exception:
            self.error = "writer_error"
            self.lost += self._queue.qsize() + pending
        finally:
            try:
                self.cpu_ns = time.thread_time_ns() - started
            except Exception:
                self.error = "writer_error"
            try:
                self._stream.close()
            except Exception:
                self.error = "writer_error"
