"""Synthetic, provider-free observer checks: no traffic, tokenizer or model sessions."""

import contextvars
import json
import threading

import numpy as np
import pytest

from colony import context, retrieval, store, telemetry
from colony.telemetry_io import FIELDS, JsonlSink, encode


class Clock:
    now = 0

    def __call__(self):
        return self.now


class MemorySink:
    cpu_ns = 0
    error = None

    def __init__(self):
        self.rows = []
        self.reject = None

    def offer(self, data):
        if self.reject:
            return self.reject
        self.rows.append(json.loads(data))
        return None

    def finish(self, footer, *, timeout):
        self.rows.append(json.loads(footer(len(self.rows), 0)))
        return True


@pytest.fixture(autouse=True)
def forbid_model_construction(monkeypatch):
    factory = retrieval.models

    def forbidden(*args, **kwargs):
        pytest.fail("This suite must not construct model sessions")

    monkeypatch.setattr(retrieval, "TextEmbedding", forbidden)
    monkeypatch.setattr(retrieval, "TextCrossEncoder", forbidden)
    factory.cache_clear()
    yield
    factory.cache_clear()


@pytest.fixture
def make_recorder():
    recorders = []

    def make(**kwargs):
        clock, sink = Clock(), MemorySink()
        identity = {
            "source": "0" * 40,
            "observer": "1" * 64,
            "models": "2" * 64,
            "python": "3.13.0",
            "fastembed": "0.7.4",
            "onnxruntime": "1.24.4",
        }
        rec = telemetry.Recorder(
            sink, identity, wall=clock, cpu=lambda: 0, draw=lambda: True, watchdog=False, **kwargs
        )
        recorders.append(rec)
        return rec, sink, clock

    yield make
    for recorder in recorders:
        recorder.close()


def finish(rec, sink):
    rec.close()
    return sink.rows[-1]


def test_inactive_paths_have_no_observer_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Inactive hooks must not inspect clocks, samples, sinks or models")

    monkeypatch.setattr(telemetry.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(telemetry.secrets, "randbelow", forbidden)
    monkeypatch.setattr(telemetry.threading, "Thread", forbidden)
    assert retrieval.rank("untouched question", []) == []
    assert store.Store.__new__(store.Store).ask("q", [], 1, 0.6) == {
        "hit": False,
        "reason": "no_pull_scope",
    }
    value = object()
    assert telemetry.staged_rank("answer", lambda q, p: value, "q", []) is value


def test_all_asks_include_empty_singleton_and_unsampled(make_recorder):
    rec, sink, _ = make_recorder()
    decisions = iter([True, False, True])
    rec._draw = lambda: next(decisions)

    @telemetry.observe_rank
    def rank(q, p):
        return [1.0] * len(p)

    @telemetry.observe_ask
    def ask(passages):
        if passages is not None:
            return telemetry.staged_rank("answer", rank, "private question", passages)

    with telemetry._bind_for_tests(rec):
        ask(None)
        ask(["private singleton"])
        ask([])
    footer = finish(rec, sink)
    assert (footer["asks"], footer["finished"], footer["selected"]) == (3, 3, 2)
    assert footer["rank_entries"] == 2
    assert footer["widths"][:3] == [1, 1, 0]
    assert sum(footer["ask_mix"]) == 3
    assert footer["ask_mix"][0] == 1  # No rank entry at all.
    assert footer["ask_mix"][6] == 1  # One empty rank entry.
    assert footer["ask_mix"][8] == 1  # One nonempty rank entry, unsampled.
    rows = [r for r in sink.rows if r["kind"] == "rank"]
    assert len(rows) == 1 and rows[0]["token_state"] == "not_applicable"
    assert "private" not in json.dumps(sink.rows)


def test_empty_actual_rank_does_not_initialize_models(make_recorder):
    rec, sink, _ = make_recorder()

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", store.rank, "q", [])

    with telemetry._bind_for_tests(rec):
        assert ask() == []
    assert finish(rec, sink)["rank_finished"] == 1


def test_aliases_stages_inputs_float_order_and_no_batch_policy_change(make_recorder, monkeypatch):
    rec, sink, _ = make_recorder()
    question, passages = "  original?  ", ["first", " second ", "third"]
    seen = []

    class Fake:
        def rerank(self, q, p, **kwargs):
            seen.append((q, p, kwargs))
            return iter([0, -2, 1.5])

    monkeypatch.setattr(retrieval, "models", lambda: (object(), Fake()))

    @telemetry.observe_ask
    def ask():
        for stage, alias in [
            ("answer", store.rank),
            ("evidence", store.rank),
            ("excerpt_claims", context.rank),
            ("excerpt_plain", context.rank),
        ]:
            result = telemetry.staged_rank(stage, alias, question, passages)
            assert result == [0.0, -2.0, 1.5]
            assert all(type(x) is float for x in result)

    with telemetry._bind_for_tests(rec):
        ask()
    footer = finish(rec, sink)
    assert all(q is question and p is passages and kwargs == {} for q, p, kwargs in seen)
    assert footer["rank_entries"] == 4
    assert [r["stage"] for r in sink.rows if r["kind"] == "rank"] == [
        "answer",
        "evidence",
        "excerpt_claims",
        "excerpt_plain",
    ]
    assert footer["ask_mix"][17] == 1  # Multiple rank/nonempty entries, multi seen.


class Backend:
    def __init__(self, *, fail=None, rows=65, columns=5):
        self.fail, self.calls = fail, 0
        self.mask = np.ones((rows, columns), dtype=np.int64)
        self.inputs = None

    def _preprocess_onnx_input(self, inputs, **kwargs):
        self.inputs = inputs
        return inputs

    def onnx_embed_pairs(self, pairs, **kwargs):
        self.calls += 1
        self._preprocess_onnx_input({"attention_mask": self.mask[: len(pairs)]})
        if self.fail:
            raise self.fail
        return [1.0] * len(pairs)


def test_actual_mask_batches_64_plus_1_without_tokenization(make_recorder):
    rec, sink, _ = make_recorder()
    backend = Backend()
    telemetry._prepare_backend_for_tests(backend)
    first = backend.onnx_embed_pairs
    telemetry._prepare_backend_for_tests(backend)
    assert backend.onnx_embed_pairs is first

    @telemetry.observe_rank
    def rank(q, p):
        return backend.onnx_embed_pairs(p[:64]) + backend.onnx_embed_pairs(p[64:])

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("evidence", rank, "not recorded", ["secret"] * 65)

    with telemetry._bind_for_tests(rec):
        assert ask() == [1.0] * 65
    finish(rec, sink)
    row = next(r for r in sink.rows if r["kind"] == "rank")
    assert (row["attempts"], row["batches"], row["slots"], row["nonpadding"]) == (2, 2, 325, 325)
    assert row["token_state"] == "ok" and sum(row["tokens"]) == 65
    assert backend.calls == 2
    assert "secret" not in json.dumps(sink.rows)
    # Stopped observation leaves original backend operation working exactly once.
    assert backend.onnx_embed_pairs(["q"]) == [1.0]
    assert backend.calls == 3


def test_original_exception_identity_and_partial_token_coverage(make_recorder):
    rec, sink, _ = make_recorder()
    error = RuntimeError("never log this exception payload")
    backend = Backend(fail=error)
    telemetry._prepare_backend_for_tests(backend)

    @telemetry.observe_rank
    def rank(q, p):
        return backend.onnx_embed_pairs(p)

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", rank, "q", ["p"])

    with telemetry._bind_for_tests(rec), pytest.raises(RuntimeError) as caught:
        ask()
    assert caught.value is error and backend.calls == 1
    footer = finish(rec, sink)
    row = next(r for r in sink.rows if r["kind"] == "rank")
    assert (row["attempts"], row["batches"], row["token_state"]) == (1, 0, "partial")
    assert footer["raised"] == 1 and footer["rank_raised"] == 1
    assert "exception payload" not in json.dumps(sink.rows)


def test_shape_limit_checked_before_mask_scan(make_recorder, monkeypatch):
    rec, sink, _ = make_recorder()
    backend = Backend(rows=1, columns=513)
    telemetry._prepare_backend_for_tests(backend)

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized masks must not be scanned")

    monkeypatch.setattr(np, "count_nonzero", forbidden)

    @telemetry.observe_rank
    def rank(q, p):
        return backend.onnx_embed_pairs(p)

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", rank, "q", ["p"])

    with telemetry._bind_for_tests(rec):
        assert ask() == [1.0]
    footer = finish(rec, sink)
    assert footer["reason"] == "shape_limit"
    assert footer["censored"] == 1 and footer["rank_censored"] == 1


@pytest.mark.parametrize("reason", ["queue_full", "byte_limit", "writer_error"])
def test_sink_failure_does_not_change_request_or_fabricate_completion(make_recorder, reason):
    rec, sink, _ = make_recorder()
    result = object()

    @telemetry.observe_rank
    def rank(q, p):
        sink.reject = reason
        return result

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", rank, "q", ["p"])

    with telemetry._bind_for_tests(rec):
        assert ask() is result
    footer = finish(rec, sink)
    assert footer["reason"] == reason and footer["lost"] == 1
    assert footer["finished"] == 0 and footer["censored"] == 1


def test_missing_stage_and_bad_observer_clock_fail_open(make_recorder):
    rec, sink, _ = make_recorder()
    called = []

    @telemetry.observe_rank
    def rank(q, p):
        called.append(True)
        return [3.0]

    @telemetry.observe_ask
    def ask():
        return rank("no stage", ["p"])

    with telemetry._bind_for_tests(rec):
        assert ask() == [3.0]
    assert finish(rec, sink)["reason"] == "stage_error"
    assert called == [True]

    rec2, sink2, _ = make_recorder()
    rec2._wall = lambda: (_ for _ in ()).throw(RuntimeError("observer failure"))
    with telemetry._bind_for_tests(rec2):
        assert ask() == [3.0]
    assert finish(rec2, sink2)["reason"] == "observer_error"
    assert not rec2._lock.locked()


def test_extra_rank_marks_whole_ask_censored(make_recorder):
    rec, sink, _ = make_recorder(limits=telemetry.Limits(per_ask=1))

    @telemetry.observe_rank
    def rank(q, p):
        return []

    @telemetry.observe_ask
    def ask():
        for _ in range(2):
            telemetry.staged_rank("answer", rank, "q", [])

    with telemetry._bind_for_tests(rec):
        ask()
    footer = finish(rec, sink)
    assert footer["reason"] == "ask_event_limit"
    assert footer["rank_entries"] == 2
    assert footer["unobserved_ranks"] == 1
    assert footer["unobserved_rank_stages"] == [1, 0, 0, 0, 0]
    assert footer["complete_samples"] == 0 and footer["censored_unknown"] == 1


def test_cutoff_freezes_inflight_and_no_late_completion(make_recorder):
    rec, sink, clock = make_recorder(limits=telemetry.Limits(seconds=1))

    @telemetry.observe_rank
    def rank(q, p):
        clock.now = 2_000_000_000
        return [4.0]

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", rank, "q", ["p", "p"])

    with telemetry._bind_for_tests(rec):
        assert ask() == [4.0]
    footer = finish(rec, sink)
    assert footer["reason"] == "time_limit"
    assert footer["rank_finished"] == 0 and footer["censored_multi"] == 1
    assert footer["finished"] == 0


def test_cpu_and_aggregate_observer_tripwires(make_recorder):
    rec, sink, _ = make_recorder(limits=telemetry.Limits(cpu_ns=1))
    values = iter([0, 2])
    rec._cpu = lambda: next(values)

    @telemetry.observe_ask
    def ask():
        return "unchanged"

    with telemetry._bind_for_tests(rec):
        assert ask() == "unchanged"
    assert finish(rec, sink)["reason"] == "cpu_limit"


def test_request_local_contexts_and_no_global_stage(make_recorder):
    rec, sink, _ = make_recorder()
    first_waiting, second_done = threading.Event(), threading.Event()
    results = []

    @telemetry.observe_rank
    def rank(q, p):
        return [1.0] * len(p)

    @telemetry.observe_ask
    def ask(first):
        if not first:
            assert first_waiting.wait(2)
        result = telemetry.staged_rank("answer" if first else "evidence", rank, "q", ["p"])
        if first:
            first_waiting.set()
            assert second_done.wait(2)
        results.append(result)

    def worker(ctx, first):
        ctx.run(ask, first)
        if not first:
            second_done.set()

    with telemetry._bind_for_tests(rec):
        ctx1, ctx2 = contextvars.copy_context(), contextvars.copy_context()
        threads = [
            threading.Thread(target=worker, args=(c, first))
            for c, first in [(ctx1, True), (ctx2, False)]
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
    footer = finish(rec, sink)
    assert results == [[1.0], [1.0]] and footer["finished"] == 2
    ranks = [r for r in sink.rows if r["kind"] == "rank"]
    assert {r["stage"] for r in ranks} == {"answer", "evidence"}
    assert len({r["ask"] for r in ranks}) == 2
    assert telemetry._ASK.get() is None and telemetry._RANK.get() is None


@pytest.mark.parametrize(
    "field,value",
    [("asks", 1001), ("seconds", 3601), ("asks", True), ("events", 0), ("contexts", 65)],
)
def test_caps_cannot_be_relaxed(field, value):
    with pytest.raises(ValueError):
        telemetry.Limits(**{field: value})


@pytest.mark.parametrize("limit,reason", [("asks", "ask_limit"), ("sampled", "sample_limit")])
def test_enrollment_caps_preserve_unobserved_denominator(make_recorder, limit, reason):
    rec, sink, _ = make_recorder(limits=telemetry.Limits(**{limit: 1}))
    calls = []

    @telemetry.observe_ask
    def ask():
        calls.append(True)
        return "original"

    with telemetry._bind_for_tests(rec):
        assert ask() == ask() == "original"
    footer = finish(rec, sink)
    assert calls == [True, True]
    assert footer["reason"] == reason
    assert footer["admitted"] == footer["complete_samples"] == 1
    assert footer["unobserved_asks"] == (limit == "sampled")


@pytest.mark.parametrize("limit,reason", [("ranks", "rank_limit"), ("events", "event_limit")])
def test_rank_and_event_caps_stop_without_retries(make_recorder, limit, reason):
    rec, sink, _ = make_recorder(limits=telemetry.Limits(**{limit: 1}))
    calls = []

    @telemetry.observe_rank
    def rank(q, p):
        calls.append(True)
        return []

    @telemetry.observe_ask
    def ask():
        for _ in range(3):
            telemetry.staged_rank("answer", rank, "q", [])

    with telemetry._bind_for_tests(rec):
        ask()
    footer = finish(rec, sink)
    assert len(calls) == 3
    assert footer["reason"] == reason and footer["events"] == 1
    assert footer["complete_samples"] == 0 and footer["sample_censored"] == 1
    if limit == "events":
        assert footer["lost"] == 1


def test_context_limit_counts_selected_but_unadmitted_ask(make_recorder):
    rec, sink, _ = make_recorder(limits=telemetry.Limits(contexts=1))
    first, second = rec.enter_ask(), rec.enter_ask()
    assert first is not None and second is None
    footer = finish(rec, sink)
    assert footer["reason"] == "context_limit"
    assert footer["asks"] == 2 and footer["admitted"] == 1
    assert footer["censored"] == footer["selected_unobserved"] == footer["unobserved_asks"] == 1


def test_completed_ask_detail_rejection_is_not_censored_or_silently_lost(make_recorder):
    rec, sink, _ = make_recorder()

    @telemetry.observe_ask
    def ask():
        sink.reject = "queue_full"
        return "original"

    with telemetry._bind_for_tests(rec):
        assert ask() == "original"
    footer = finish(rec, sink)
    assert footer["finished"] == 1
    assert footer["complete_samples"] == footer["sample_censored"] == 0
    assert footer["sample_details_lost"] == 1
    assert footer["admitted"] == (
        footer["complete_samples"] + footer["sample_censored"] + footer["sample_details_lost"]
    )


def test_callback_paused_before_lock_cannot_mutate_closed_snapshot(make_recorder):
    rec, sink, _ = make_recorder()
    paused, proceed = threading.Event(), threading.Event()
    original_lock = rec._lock

    class Gate:
        def acquire(self, *args, **kwargs):
            if threading.current_thread().name == "paused-observer":
                paused.set()
                assert proceed.wait(2)
            return original_lock.acquire(*args, **kwargs)

        def release(self):
            original_lock.release()

    rec._lock = Gate()
    results = []

    @telemetry.observe_ask
    def ask():
        return "original"

    with telemetry._bind_for_tests(rec):
        copied = contextvars.copy_context()
        thread = threading.Thread(
            target=lambda: results.append(copied.run(ask)), name="paused-observer"
        )
        thread.start()
        assert paused.wait(2)
        rec.close()
        snapshot = list(sink.rows)
        proceed.set()
        thread.join(2)
    assert results == ["original"] and sink.rows == snapshot
    assert sink.rows[-1]["asks"] == 0


@pytest.mark.parametrize("late_footer", [False, True])
def test_late_writer_never_leaves_successful_footer(tmp_path, monkeypatch, late_footer):
    from colony import telemetry_io

    class InertThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    clock = Clock()
    monkeypatch.setattr(telemetry_io.threading, "Thread", InertThread)
    monkeypatch.setattr(telemetry_io.time, "monotonic", clock)
    sink = JsonlSink(tmp_path / "synthetic-late.jsonl")
    original = sink._stream

    class DelayedStream:
        def write(self, data):
            result = original.write(data)
            if (b"footer" in data) == late_footer:
                clock.now = 2
            return result

        def tell(self):
            return original.tell()

        def truncate(self, position):
            original.truncate(position)

        def close(self):
            original.close()

    sink._stream = DelayedStream()
    assert sink.offer(b'{"synthetic":true}\n') is None
    assert not sink.finish(lambda written, lost: b'{"footer":true}\n', timeout=1)
    sink._write()  # Deterministic fake clock/stream; no elapsed-time benchmark.
    assert sink.error == "flush_timeout"
    assert b"footer" not in (tmp_path / "synthetic-late.jsonl").read_bytes()


def test_unknown_tokens_are_null_not_zero(make_recorder):
    rec, sink, _ = make_recorder()

    @telemetry.observe_rank
    def rank(q, p):
        return [1.0]

    @telemetry.observe_ask
    def ask():
        return telemetry.staged_rank("answer", rank, "q", ["p"])

    with telemetry._bind_for_tests(rec):
        ask()
    footer = finish(rec, sink)
    row = next(r for r in sink.rows if r["kind"] == "rank")
    assert row["token_state"] == "unavailable"
    assert all(row[k] is None for k in ["tokens", "nonpadding", "slots", "attempts", "batches"])
    assert footer["selected_rank_stages"] == [1, 0, 0, 0]
    assert footer["token_coverage"][2] == 1


def test_nested_ask_stops_observation_but_preserves_both_operations(make_recorder):
    rec, sink, _ = make_recorder()
    calls = []

    @telemetry.observe_ask
    def inner():
        calls.append("inner")
        return "result"

    @telemetry.observe_ask
    def outer():
        calls.append("outer")
        return inner()

    with telemetry._bind_for_tests(rec):
        assert outer() == "result"
    footer = finish(rec, sink)
    assert calls == ["outer", "inner"]
    assert footer["reason"] == "nested_ask" and footer["asks"] == 1
    assert telemetry._ASK.get() is None


def test_closed_schema_rejects_payload_strings_and_objects(make_recorder):
    _, sink, _ = make_recorder()
    header = dict(sink.rows[0])
    header.pop("kind")
    for fields in [
        {**header, "question": "secret"},
        {**header, "models": object()},
        {**header, "threads": True},
        {**header, "threads": -1},
        {**header, "python": "secret free text"},
    ]:
        with pytest.raises(ValueError):
            encode("header", fields)
    assert set(header) == FIELDS["header"]


def test_explicit_bounded_sink_no_overwrite_and_footer(tmp_path, make_recorder):
    _, memory, _ = make_recorder()
    header = dict(memory.rows[0])
    header.pop("kind")
    path = tmp_path / "synthetic-metadata.jsonl"
    sink = JsonlSink(path)
    assert sink.offer(encode("header", header)) is None
    assert sink.finish(lambda written, lost: b'{"synthetic_footer":true}\n')
    assert len(path.read_bytes()) < 5 * 1024 * 1024
    assert len(path.read_text().splitlines()) == 2
    with pytest.raises(FileExistsError):
        JsonlSink(path)


def test_stream_close_failure_cannot_acknowledge_success(tmp_path):
    sink = JsonlSink(tmp_path / "synthetic-close-error.jsonl")
    original = sink._stream

    class CloseFailure:
        def write(self, data):
            return original.write(data)

        def tell(self):
            return original.tell()

        def close(self):
            original.close()
            raise OSError("Do not persist exception details")

    sink._stream = CloseFailure()
    assert sink.offer(b'{"synthetic":true}\n') is None
    assert not sink.finish(lambda written, lost: b'{"footer":true}\n')
    assert sink.error == "writer_error"
