import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from scripts.native_host_trace import Trace


def observe(trace, direction, **value):
    trace.observe(direction, json.dumps(value).encode())


def response(trace, ident, value):
    observe(trace, 'response', id=ident, result={'content': [{'text': json.dumps(value)}]})


def read(trace):
    observe(trace, 'request', id=1, method='tools/call', params={'name': 'read', 'arguments': {'path': 'SECRET'}})
    response(trace, 1, {'evidence_ref': 'PRIVATE_REFERENCE', 'content': 'SECRET_SOURCE'})


def refresh(trace):
    observe(trace, 'response', method='notifications/tools/list_changed')
    observe(trace, 'request', id=2, method='tools/list')
    observe(trace, 'response', id=2, result={'tools': [{'name': 'publish'}, {'name': 'SECRET_TOOL'}]})


def summary(trace, stream):
    trace.finish()
    text = stream.getvalue()
    assert 'SECRET' not in text and 'PRIVATE_REFERENCE' not in text
    return json.loads(text.splitlines()[-1])


@pytest.mark.parametrize('eligible,read_done,refreshed,status', [
    (False, True, True, 'not_eligible'), (True, False, False, 'no_referenced_read'),
    (True, True, False, 'ready_refresh_unobserved'),
    (True, True, True, 'refreshed_with_publish_no_attempt'),
])
def test_observations_do_not_invent_abstention_or_model_visibility(eligible, read_done, refreshed, status):
    stream = io.StringIO()
    trace = Trace(stream, eligible)
    if read_done:
        read(trace)
    if refreshed:
        refresh(trace)
    value = summary(trace, stream)
    assert value['status'] == status
    assert value['abstention_reason'] == value['model_visible_catalog'] == 'unobserved'


@pytest.mark.parametrize('outcome', [None, {'stored': True}, {'stored': False, 'queued': True}])
def test_attempts_and_receipts_are_distinct(outcome):
    stream = io.StringIO()
    trace = Trace(stream, True)
    observe(trace, 'request', id=3, method='tools/call', params={'name': 'publish'})
    if outcome is not None:
        response(trace, 3, outcome)
    expected = 'attempted_outcome_unobserved' if outcome is None else 'attempted_with_receipt'
    assert summary(trace, stream)['status'] == expected


def test_malformed_and_oversized_metadata_is_bounded_and_never_echoed():
    stream = io.StringIO()
    trace = Trace(stream, True, limit=3)
    for value in (b'SECRET', b'[]', b'x' * ((1 << 20) + 1)):
        trace.observe('request', value)
    for i in range(300):
        observe(trace, 'request', id=i, method='tools/list')
        observe(trace, 'response', method='notifications/tools/list_changed')
    value = summary(trace, stream)
    assert value['metadata_dropped'] > 0 and len(trace.pending) == 64
    assert len(stream.getvalue().splitlines()) == 4


def test_failed_or_unmatched_reads_do_not_claim_readiness():
    stream = io.StringIO()
    trace = Trace(stream, True)
    response(trace, 4, {'evidence_ref': 'unmatched'})
    observe(trace, 'request', id=1, method='tools/call', params={'name': 'read'})
    observe(trace, 'response', id=1, result={'isError': True, 'content': []})
    assert summary(trace, stream)['status'] == 'no_referenced_read'


@pytest.mark.parametrize('results,ready,dropped', [
    ([{'error': 'SECRET', 'evidence_ref': None}, {'evidence_ref': 'PRIVATE_REFERENCE'}], True, 0),
    ([{'error': 'SECRET', 'evidence_ref': 'PRIVATE_REFERENCE'}], False, 0),
    ([{'evidence_ref': None}], False, 0), ([], False, 1), ([None], False, 1),
    ([{'evidence_ref': 'PRIVATE_REFERENCE'}] * 9, False, 1),
])
def test_batch_readiness_is_bounded_metadata_not_source_evidence(results, ready, dropped):
    stream = io.StringIO()
    trace = Trace(stream, True)
    observe(trace, 'request', id=1, method='tools/call', params={'name': 'read'})
    response(trace, 1, {'results': results})
    value = summary(trace, stream)
    assert value['status'] == ('ready_refresh_unobserved' if ready else 'no_referenced_read')
    assert value['metadata_dropped'] == dropped


@pytest.mark.parametrize('blocked_log', [False, True])
def test_real_child_transport_is_unchanged_even_when_trace_unwritable(tmp_path, blocked_log):
    script = Path(__file__).resolve().parents[1] / 'scripts/native_host_trace.py'
    log = tmp_path / 'metadata.jsonl'
    if blocked_log:
        log.write_bytes(b'previous-evidence')
    # A provider-free fake MCP peer. The observer must relay bytes exactly.
    child = "import sys;data=sys.stdin.buffer.read();sys.stdout.buffer.write(data);sys.stdout.buffer.flush()"
    raw = b'{"id":1,"method":"tools/list"}\r\n{"secret":"SECRET"}\n'
    result = subprocess.run([sys.executable, str(script), '--output', str(log), '--eligible', '--',
                             sys.executable, '-c', child], input=raw, capture_output=True, timeout=15)
    assert result.returncode == 0 and result.stdout == raw
    if blocked_log:
        assert log.read_bytes() == b'previous-evidence'
    else:
        assert 'SECRET' not in log.read_text()
        assert json.loads(log.read_text().splitlines()[-1])['event'] == 'summary'


def test_logging_failure_is_nonblocking():
    class Broken:
        def write(self, value):
            raise OSError('private error')
    trace = Trace(Broken(), True)
    read(trace)
    refresh(trace)
    trace.finish()
    assert trace.ready and trace.refreshed and trace.stream is None


def test_deeply_nested_input_cannot_break_observation_or_echo_source():
    stream = io.StringIO()
    trace = Trace(stream, True)
    trace.observe('response', b'[' * 4000 + b'"SECRET"' + b']' * 4000)
    read(trace)
    refresh(trace)
    result = summary(trace, stream)
    assert result['metadata_dropped'] == 1
    assert trace.ready and trace.refreshed


def test_log_close_failure_does_not_change_child_exit_or_bytes(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import scripts.native_host_trace as module

    class BadClose:
        def write(self, value):
            pass

        def flush(self):
            pass

        def close(self):
            raise OSError('private close failure')

    output = io.BytesIO()
    with monkeypatch.context() as patch:
        patch.setattr(module, 'open', lambda *a, **k: BadClose(), raising=False)
        patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}\n')))
        patch.setattr(sys, 'stdout', SimpleNamespace(buffer=output))
        code = module.relay([sys.executable, '-c',
            'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'], tmp_path / 'trace', True)
    assert code == 0 and output.getvalue() == b'{}\n'


def test_catalog_requested_before_notification_does_not_confirm_refresh():
    stream = io.StringIO()
    trace = Trace(stream, True)
    read(trace)
    observe(trace, 'request', id=2, method='tools/list')
    observe(trace, 'response', method='notifications/tools/list_changed')
    observe(trace, 'response', id=2, result={'tools': [{'name': 'publish'}]})
    assert summary(trace, stream)['status'] == 'ready_refresh_unobserved'
    catalog = json.loads(stream.getvalue().splitlines()[-2])
    assert catalog['publish_present'] and not catalog['after_notification']


def test_refresh_must_match_latest_notification_and_successful_catalog():
    stream = io.StringIO()
    trace = Trace(stream, True)
    read(trace)
    refresh(trace)
    assert trace.refreshed
    observe(trace, 'request', id=3, method='tools/list')
    observe(trace, 'response', method='notifications/tools/list_changed')
    assert not trace.refreshed
    observe(trace, 'response', id=3, result={'tools': [{'name': 'publish'}]})
    assert not trace.refreshed  # The older generation cannot satisfy this notification.
    observe(trace, 'request', id=4, method='tools/list')
    observe(trace, 'response', id=4, error={'message': 'SECRET'})
    assert not trace.refreshed
    observe(trace, 'request', id=5, method='tools/list')
    observe(trace, 'response', id=5, result={'tools': [{'name': 'read'}]})
    assert not trace.refreshed
    observe(trace, 'request', id=6, method='tools/list')
    observe(trace, 'response', id=6, result={'tools': [{'name': 'publish'}]})
    assert summary(trace, stream)['status'] == 'refreshed_with_publish_no_attempt'


@pytest.mark.parametrize('blocked_operation', ['open', 'write', 'flush', 'close'])
def test_stalled_sidecar_cannot_hold_transport_or_shutdown(monkeypatch, tmp_path, blocked_operation):
    from types import SimpleNamespace
    import scripts.native_host_trace as module

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    results, sinks = [], []

    def stall(operation):
        if operation == blocked_operation:
            entered.set()
            assert release.wait(5)

    class SlowFile:
        def write(self, value):
            stall('write')

        def flush(self):
            stall('flush')

        def close(self):
            stall('close')

    def open_log(*args, **kwargs):
        stall('open')
        return SlowFile()

    class CapturedSink(module.MetadataSink):
        def __init__(self, *args):
            super().__init__(*args)
            sinks.append(self)

    raw = b'{"id":1,"method":"tools/call","params":{"name":"publish","arguments":{"secret":"SECRET"}}}\n'
    output = io.BytesIO()
    monkeypatch.setattr(module, 'open', open_log, raising=False)
    monkeypatch.setattr(module, 'MetadataSink', CapturedSink)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(sys, 'stdout', SimpleNamespace(buffer=output))

    def run():
        try:
            results.append(module.relay([sys.executable, '-c',
                'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.exit(7)'],
                tmp_path / 'trace', True))
        finally:
            finished.set()

    runner = threading.Thread(target=run)
    runner.start()
    try:
        assert entered.wait(2)
        assert finished.wait(2), 'Diagnostic I/O blocked child delivery or relay shutdown'
        assert results == [7] and output.getvalue() == raw
    finally:
        release.set()
        runner.join(5)
        for sink in sinks:
            sink.worker.join(5)
        assert not runner.is_alive()
        assert all(not sink.worker.is_alive() for sink in sinks)


def test_stalled_sink_remains_bounded_and_only_queues_allowlisted_metadata(monkeypatch, tmp_path):
    import scripts.native_host_trace as module

    entered, release = threading.Event(), threading.Event()
    original_open = open

    def stalled_open(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(module, 'open', stalled_open, raising=False)
    log = tmp_path / 'metadata.jsonl'
    sink = module.MetadataSink(log)
    trace = Trace(sink, True)
    try:
        assert entered.wait(2)
        read(trace)
        for _ in range(1000):
            observe(trace, 'response', method='notifications/tools/list_changed', secret='SECRET')
        trace.finish()
        assert sink.items.qsize() == module.EVENT_LIMIT + 1
        # Finishing is idempotent, and late requests cannot follow the final summary.
        trace.finish()
        observe(trace, 'request', id=100, method='tools/call', params={'name': 'publish'})
        assert sink.items.qsize() == module.EVENT_LIMIT + 1 and trace.attempts == 0
    finally:
        release.set()
        sink.close()
        sink.worker.join(5)
    text = log.read_text()
    assert 'SECRET' not in text and 'PRIVATE_REFERENCE' not in text
    values = [json.loads(line) for line in text.splitlines()]
    assert len(values) == module.EVENT_LIMIT + 1
    assert values[-1]['event'] == 'summary' and values[-1]['metadata_dropped'] > 0
