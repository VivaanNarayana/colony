"""Dormant reranker metadata instrumentation.

There is deliberately no production activation API, environment switch, CLI,
startup registration or automatic sink. Internal binding is for synthetic tests;
real collection needs a separately reviewed activation change.
"""

import contextvars
import functools
import secrets
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from colony.telemetry_io import FOOTER_BYTES, REASONS, STAGES, encode

_RECORDER = contextvars.ContextVar("colony_metadata_recorder", default=None)
_ASK = contextvars.ContextVar("colony_metadata_ask", default=None)
_RANK = contextvars.ContextVar("colony_metadata_rank", default=None)
_STAGE = contextvars.ContextVar("colony_metadata_stage", default=None)
_BACKEND_LOCK = threading.Lock()
_BACKEND_OWNER = object()
WIDTHS = (0, 1, 2, 4, 8, 12, 16, 32, 48, 64, 96)
CHARS = (0, 64, 256, 1024, 2400, 4000)
TOKENS = (0, 16, 32, 64, 128, 256, 512)


def _bin(value, limits):
    return next((i for i, maximum in enumerate(limits) if value <= maximum), len(limits))


@dataclass(frozen=True)
class Limits:
    seconds: int = 3600
    asks: int = 1000
    ranks: int = 4096
    sampled: int = 128
    events: int = 1024
    per_ask: int = 8
    contexts: int = 64
    selected_contexts: int = 16
    cpu_ns: int = 2_000_000_000
    rank_observer_ns: int = 250_000
    ask_observer_ns: int = 1_000_000

    def __post_init__(self):
        ceilings = (3600, 1000, 4096, 128, 1024, 8, 64, 16, 2_000_000_000, 250_000, 1_000_000)
        if any(
            type(v) is not int or not 1 <= v <= cap
            for v, cap in zip(vars(self).values(), ceilings, strict=True)
        ):
            raise ValueError("Limits may only lower fixed bounds")


@dataclass(eq=False)
class _AskState:
    selected: bool
    identifier: str
    started: int
    ranks: int = 0
    nonempty: int = 0
    multi: bool = False
    observer_ns: int = 0


@dataclass(eq=False)
class _RankState:
    ask: _AskState
    stage: int
    n: int
    started: int
    ordinal: int = 0
    chars: list = field(default_factory=lambda: [0] * 7)
    tokens: list = field(default_factory=lambda: [0] * 8)
    nonpadding: int = 0
    slots: int = 0
    attempts: int = 0
    batches: int = 0
    rows: int = 0
    observer_ns: int = 0


def _event(method):
    """Observer failure affects only observation, never the application operation."""

    @functools.wraps(method)
    def guarded(self, *args):
        if not self.running:
            return None
        if not self._lock.acquire(blocking=False):
            self.stop("contention")
            return None
        if not self.running or self._closed.is_set():
            self._lock.release()
            return None
        wall = cpu = None
        result = None
        try:
            wall, cpu = self._wall(), self._cpu()
            if wall - self._started >= self.limits.seconds * 1_000_000_000:
                self.stop("time_limit")
            elif getattr(self.sink, "error", None):
                self.stop(self.sink.error)
            else:
                result = method(self, *args)
            return result
        except Exception:
            self.stop("observer_error")
            return None
        finally:
            try:
                elapsed = max(0, self._wall() - wall) if wall is not None else 0
                self._cpu_ns += max(0, self._cpu() - cpu) if cpu is not None else 0
                rank = result if isinstance(result, _RankState) else _RANK.get()
                ask = result if isinstance(result, _AskState) else _ASK.get()
                if rank is not None:
                    rank.observer_ns += elapsed
                    ask = rank.ask
                    if rank.observer_ns > self.limits.rank_observer_ns:
                        self.stop("rank_overhead")
                if ask is not None:
                    ask.observer_ns += elapsed
                    if ask.selected and ask.observer_ns > self.limits.ask_observer_ns:
                        self.stop("ask_overhead")
                if self._cpu_ns + getattr(self.sink, "cpu_ns", 0) > self.limits.cpu_ns:
                    self.stop("cpu_limit")
            except Exception:
                self.stop("observer_error")
            finally:
                self._lock.release()

    return guarded


class Recorder:
    """Bounded engine, not an activation mechanism. Construct only in synthetic tests.

    Clocks, randomness and sink are injected so tests need no traffic, models,
    tokenizer, files, timers or elapsed-time performance measurements.
    """

    def __init__(
        self,
        sink,
        identity,
        *,
        limits=None,
        wall=time.perf_counter_ns,
        cpu=time.thread_time_ns,
        draw=None,
        watchdog=True,
    ):
        self.sink, self.limits = sink, limits or Limits()
        self._wall, self._cpu = wall, cpu
        self._draw = draw or (lambda: secrets.randbelow(10) == 0)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._started = wall()
        self._reason = None
        self._cpu_ns = 0
        self._asks, self._ranks = set(), set()
        self.asks = self.finished = self.raised = self.selected = self.admitted = 0
        self.complete_samples = self.selected_unobserved = 0
        self.rank_entries = self.rank_finished = self.rank_raised = 0
        self.events = self.unavailable = self.lost = 0
        self.queued = 0
        self.sample_details_lost = self.rank_details_lost = 0
        self.unobserved_rank_stages = [0] * 5
        self.closure_ok = None
        self.widths, self.ask_mix = [0] * 48, [0] * 36
        self.rank_outcomes, self.token_coverage, self.selected_rank_stages = (
            [0] * 12,
            [0] * 16,
            [0] * 4,
        )
        self._run = secrets.token_hex(16)
        self.running = True
        header = {
            "schema": 1,
            "run": self._run,
            "sample_percent": 10,
            "batch_limit": 64,
            "max_tokens": 512,
            "threads": 2,
            **identity,
        }
        if set(header) != {
            "schema",
            "run",
            "sample_percent",
            "batch_limit",
            "max_tokens",
            "threads",
            "source",
            "observer",
            "models",
            "python",
            "fastembed",
            "onnxruntime",
        } or any(
            k in identity
            for k in ("schema", "run", "sample_percent", "batch_limit", "max_tokens", "threads")
        ):
            raise ValueError("Invalid identity fields")
        self._emit("header", header)
        self._watcher = None
        if watchdog:
            self._watcher = threading.Thread(target=self._watch, daemon=True)
            self._watcher.start()

    def _watch(self):
        try:
            while not self._wake.wait(0.05):
                if self._wall() - self._started >= self.limits.seconds * 1_000_000_000:
                    self.stop("time_limit")
                elif getattr(self.sink, "error", None):
                    self.stop(self.sink.error)
                elif self._cpu_ns + getattr(self.sink, "cpu_ns", 0) > self.limits.cpu_ns:
                    self.stop("cpu_limit")
        except Exception:
            self.stop("observer_error")
        finally:
            self.close()

    def stop(self, reason="manual"):
        self.running = False
        if self._reason is None:
            self._reason = reason if reason in REASONS else "observer_error"
        self._wake.set()

    def _emit(self, kind, fields):
        try:
            error = self.sink.offer(encode(kind, fields))
        except Exception:
            error = "schema_error"
        if error:
            self.lost += 1
            self.stop(error)
            return False
        self.queued += 1
        return True

    @_event
    def enter_ask(self):
        if self.asks >= self.limits.asks:
            self.stop("ask_limit")
            return None
        self.asks += 1
        selected = self._draw()
        if type(selected) is not bool:
            raise ValueError("Invalid sample decision")
        self.selected += selected
        if self.selected > self.limits.sampled:
            self.selected_unobserved += 1
            self.stop("sample_limit")
            return None
        if len(self._asks) >= self.limits.contexts or (
            selected and sum(a.selected for a in self._asks) >= self.limits.selected_contexts
        ):
            self.selected_unobserved += selected
            self.stop("context_limit")
            return None
        ask = _AskState(selected, secrets.token_hex(16) if selected else "", self._wall())
        self._asks.add(ask)
        self.admitted += selected
        return ask

    @_event
    def leave_ask(self, ask, raised):
        if ask not in self._asks:
            return
        self._asks.remove(ask)
        self.raised += raised
        self.finished += not raised
        index = int(raised) * 18 + min(ask.ranks, 2) * 6 + min(ask.nonempty, 2) * 2 + ask.multi
        self.ask_mix[index] += 1
        if ask.selected and self._emit(
            "ask",
            {
                "ask": ask.identifier,
                "outcome": "raised" if raised else "completed",
                "ranks": ask.ranks,
                "nonempty": ask.nonempty,
                "multi": int(ask.multi),
                "wall_ns": max(0, self._wall() - ask.started),
                "observer_ns": ask.observer_ns,
            },
        ):
            self.complete_samples += 1
        elif ask.selected:
            self.sample_details_lost += 1

    def _reject_rank(self, ask, stage, reason):
        self.unobserved_rank_stages[STAGES.index(stage) if stage in STAGES else 4] += 1
        self.rank_details_lost += ask.selected
        self.stop(reason)

    @_event
    def enter_rank(self, ask, stage, passages):
        n = len(passages)
        ask.ranks += 1
        ask.nonempty += n > 0
        ask.multi |= n >= 2
        self.rank_entries += 1
        if stage not in STAGES:
            self._reject_rank(ask, stage, "stage_error")
            return None
        self.widths[STAGES.index(stage) * 12 + _bin(n, WIDTHS)] += 1
        self.selected_rank_stages[STAGES.index(stage)] += ask.selected
        if self.rank_entries > self.limits.ranks:
            self._reject_rank(ask, stage, "rank_limit")
            return None
        if ask.selected and ask.ranks > self.limits.per_ask:
            self._reject_rank(ask, stage, "ask_event_limit")
            return None
        if n > 96:
            self._reject_rank(ask, stage, "shape_limit")
            return None
        rank = _RankState(ask, STAGES.index(stage), n, self._wall())
        rank.ordinal = ask.ranks
        if ask.selected:
            for passage in passages:
                if type(passage) is not str:
                    self._reject_rank(ask, stage, "schema_error")
                    return None
                rank.chars[_bin(len(passage), CHARS)] += 1
        self._ranks.add(rank)
        return rank

    @_event
    def leave_rank(self, rank, raised):
        if rank not in self._ranks:
            return
        self._ranks.remove(rank)
        self.rank_raised += raised
        self.rank_finished += not raised
        self.rank_outcomes[rank.stage * 3 + int(raised)] += 1
        if not rank.ask.selected:
            return
        state = (
            "ok"
            if rank.batches and rank.batches == rank.attempts and rank.rows == rank.n
            else "unavailable"
        )
        if not rank.n:
            state = "not_applicable"
        elif rank.attempts and (rank.batches != rank.attempts or rank.rows != rank.n):
            state = "partial"
        self.unavailable += state in {"unavailable", "partial"}
        self.token_coverage[
            rank.stage * 4 + ("ok", "partial", "unavailable", "not_applicable").index(state)
        ] += 1
        if self.events >= self.limits.events:
            self.lost += 1
            self.rank_details_lost += 1
            self.stop("event_limit")
            return
        if self._emit(
            "rank",
            {
                "ask": rank.ask.identifier,
                "ordinal": rank.ordinal,
                "stage": STAGES[rank.stage],
                "n": rank.n,
                "chars": rank.chars,
                "tokens": rank.tokens if state != "unavailable" else None,
                "nonpadding": rank.nonpadding if state != "unavailable" else None,
                "slots": rank.slots if state != "unavailable" else None,
                "attempts": rank.attempts if state != "unavailable" else None,
                "batches": rank.batches if state != "unavailable" else None,
                "token_state": state,
                "outcome": "raised" if raised else "completed",
                "wall_ns": max(0, self._wall() - rank.started),
                "observer_ns": rank.observer_ns,
            },
        ):
            self.events += 1
        else:
            self.rank_details_lost += 1

    @_event
    def input_batch(self, rank, inputs):
        if not rank.ask.selected:
            return
        # This import and all tensor inspection occur only with injected observation.
        import numpy as np

        mask = inputs.get("attention_mask") if type(inputs) is dict else None
        if type(mask) is not np.ndarray or mask.ndim != 2 or mask.dtype.kind not in "biu":
            self.stop("backend_error")
            return
        rows, columns = mask.shape
        if not 1 <= rows <= 96 or not 1 <= columns <= 512:
            self.stop("shape_limit")
            return
        lengths = np.count_nonzero(mask, axis=1)
        rank.attempts += 1
        rank.rows += rows
        rank.slots += rows * columns
        rank.nonpadding += int(lengths.sum())
        for value in lengths:
            rank.tokens[_bin(int(value), TOKENS)] += 1

    @_event
    def complete_batch(self, rank, attempts_before):
        if rank.ask.selected:
            rank.batches += rank.attempts - attempts_before

    def close(self):
        """Explicit teardown, never invoked by serving-path observer callbacks."""
        self.stop()
        if self._closed.is_set():
            return
        if not self._lock.acquire(timeout=1):
            return  # No footer: completeness is unknown, never a successful empty run.
        try:
            if self._closed.is_set():
                return
            self._closed.set()
            snapshot = {
                "reason": self._reason,
                "asks": self.asks,
                "finished": self.finished,
                "raised": self.raised,
                "selected": self.selected,
                "admitted": self.admitted,
                "complete_samples": self.complete_samples,
                "censored": len(self._asks),
                "censored_multi": sum(a.multi for a in self._asks),
                "censored_unknown": sum(not a.multi for a in self._asks),
                "selected_unobserved": self.selected_unobserved,
                "sample_censored": sum(a.selected for a in self._asks),
                "unobserved_asks": self.asks - self.finished - self.raised - len(self._asks),
                "rank_entries": self.rank_entries,
                "rank_finished": self.rank_finished,
                "rank_raised": self.rank_raised,
                "rank_censored": len(self._ranks),
                "unobserved_ranks": self.rank_entries
                - self.rank_finished
                - self.rank_raised
                - len(self._ranks),
                "unobserved_rank_stages": list(self.unobserved_rank_stages),
                "sample_details_lost": self.sample_details_lost,
                "rank_details_lost": self.rank_details_lost,
                "sample_rank_censored": sum(r.ask.selected for r in self._ranks),
                "widths": list(self.widths),
                "ask_mix": list(self.ask_mix),
                "rank_outcomes": [
                    value + sum(r.stage == i // 3 for r in self._ranks) if i % 3 == 2 else value
                    for i, value in enumerate(self.rank_outcomes)
                ],
                "token_coverage": list(self.token_coverage),
                "selected_rank_stages": list(self.selected_rank_stages),
                "events": self.events,
                "unavailable": self.unavailable,
                "observer_cpu_ns": self._cpu_ns + getattr(self.sink, "cpu_ns", 0),
            }
        finally:
            self._lock.release()

        def footer(written, lost):
            return encode(
                "footer",
                {
                    **snapshot,
                    "queued": self.queued,
                    "written": written,
                    "lost": lost + self.lost,
                    "observer_cpu_ns": self._cpu_ns + getattr(self.sink, "cpu_ns", 0),
                },
                maximum=FOOTER_BYTES,
            )

        self.closure_ok = False
        with suppress(Exception):
            self.closure_ok = bool(self.sink.finish(footer, timeout=1.0))


def _current():
    recorder = _RECORDER.get()
    return recorder if recorder is not None and recorder.running else None


@contextmanager
def _bind_for_tests(recorder):
    """Synthetic injection only. No application, CLI or configuration calls this."""
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


def observe_ask(operation):
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        recorder = _current()
        if recorder is None:
            return operation(*args, **kwargs)
        if _ASK.get() is not None:
            recorder.stop("nested_ask")
            return operation(*args, **kwargs)
        ask = recorder.enter_ask()
        if ask is None:
            return operation(*args, **kwargs)
        token = _ASK.set(ask)
        raised = True
        try:
            result = operation(*args, **kwargs)
            raised = False
            return result
        finally:
            try:
                recorder.leave_ask(ask, raised)
            finally:
                _ASK.reset(token)

    return wrapped


def observe_rank(operation):
    @functools.wraps(operation)
    def wrapped(question, passages):
        recorder, ask = _current(), _ASK.get()
        if recorder is None or ask is None:
            return operation(question, passages)
        if _RANK.get() is not None:
            recorder.stop("nested_ask")
            return operation(question, passages)
        rank = recorder.enter_rank(ask, _STAGE.get(), passages)
        if rank is None:
            return operation(question, passages)
        token = _RANK.set(rank)
        raised = True
        try:
            result = operation(question, passages)
            raised = False
            return result
        finally:
            try:
                recorder.leave_rank(rank, raised)
            finally:
                _RANK.reset(token)

    return wrapped


def staged_rank(stage, operation, question, passages):
    if _current() is None:
        return operation(question, passages)
    token = _STAGE.set(stage)
    try:
        return operation(question, passages)
    finally:
        _STAGE.reset(token)


def _prepare_backend_for_tests(backend):
    """Install a run-level dispatcher once, before binding any synthetic requests.

    No production path calls this. Never install/remove a shared wrapper per rank.
    It forwards originals unchanged after a recorder stops, including in-flight work.
    """
    with _BACKEND_LOCK:
        owner = getattr(backend, "_colony_metadata_owner", None)
        if owner is _BACKEND_OWNER:
            return
        if owner is not None:
            raise ValueError("Backend observer already owned")
        preprocess = backend._preprocess_onnx_input
        embed_pairs = backend.onnx_embed_pairs

        def prepare(inputs, **kwargs):
            result = preprocess(inputs, **kwargs)
            recorder, rank = _current(), _RANK.get()
            if recorder is not None and rank is not None:
                recorder.input_batch(rank, result)
            return result

        def run(pairs, **kwargs):
            recorder, rank = _current(), _RANK.get()
            before = rank.attempts if rank is not None else 0
            result = embed_pairs(pairs, **kwargs)
            if recorder is not None and rank is not None:
                recorder.complete_batch(rank, before)
            return result

        backend._preprocess_onnx_input = prepare
        backend.onnx_embed_pairs = run
        backend._colony_metadata_owner = _BACKEND_OWNER
