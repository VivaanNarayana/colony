"""Opt-in transparent stdio observer. Metadata only; never a model or completion hook."""
import argparse
import json
import queue
import subprocess
import sys
import threading


EVENT_LIMIT = 256


class MetadataSink:
    """Only serialized allowlisted metadata enters this bounded daemon writer."""

    def __init__(self, output):
        self.items = queue.Queue(maxsize=EVENT_LIMIT + 1)  # Events plus final summary.
        self.closed = threading.Event()
        self.failed = threading.Event()
        self.worker = threading.Thread(target=self._write, args=(output,), daemon=True)
        self.worker.start()

    def write(self, value):
        if self.closed.is_set() or self.failed.is_set():
            raise OSError('Diagnostics unavailable')
        try:
            self.items.put_nowait(value)
        except queue.Full:
            self.failed.set()
            raise OSError('Diagnostics capacity exceeded') from None

    def flush(self):
        pass  # The writer owns all filesystem operations, including open/close.

    def close(self):
        self.closed.set()
        self.worker.join(timeout=0.2)

    def _write(self, output):
        stream = None
        try:
            stream = open(output, 'x', encoding='utf-8')
            while not self.failed.is_set():
                try:
                    value = self.items.get(timeout=0.05)
                except queue.Empty:
                    if self.closed.is_set():
                        break
                    continue
                stream.write(value)
                stream.flush()
        except (OSError, ValueError):
            self.failed.set()
        finally:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    self.failed.set()


class Trace:
    def __init__(self, stream, eligible, limit=EVENT_LIMIT):
        self.stream, self.eligible, self.limit = stream, eligible, limit
        self.pending = {}
        self.events = self.dropped = self.attempts = self.outcomes = 0
        self.ready = self.notified = self.refreshed = False
        self.generation = 0
        self.finished = False
        self.lock = threading.Lock()

    def emit(self, value, final=False):
        if not final and self.events >= self.limit:
            self.dropped += 1
            return
        if self.stream is not None:
            try:
                self.stream.write(json.dumps(value, separators=(',', ':')) + '\n')
                self.stream.flush()
            except (OSError, ValueError):
                self.stream = None  # Telemetry cannot break the task transport.
        self.events += not final

    def observe(self, direction, raw):
        with self.lock:
            if self.finished:
                return
            try:
                if len(raw) > 1 << 20:
                    self.dropped += 1
                    return
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError()
                ident = msg.get('id')
                if not isinstance(ident, (int, str)) or isinstance(ident, bool):
                    ident = None
                if isinstance(ident, str) and len(ident) > 128:
                    ident = None
                if direction == 'request':
                    method = msg.get('method')
                    params = msg.get('params', {})
                    name = params.get('name') if isinstance(params, dict) else None
                    operation = method if method in {'initialize', 'tools/list'} else (
                        name if method == 'tools/call' and name in {'read', 'publish'} else None)
                    if operation == 'publish':
                        self.attempts += 1
                        self.emit({'event': 'publication_attempt'})
                    if operation and ident is not None:
                        if len(self.pending) >= 64 or ident in self.pending:
                            self.dropped += 1
                        else:
                            self.pending[ident] = (operation, self.generation)
                    return
                if msg.get('method') == 'notifications/tools/list_changed':
                    self.notified = True
                    self.generation += 1
                    self.refreshed = False
                    self.emit({'event': 'tools_list_changed'})
                    return
                operation, generation = self.pending.pop(ident, (None, 0))
                result = msg.get('result', {})
                if not isinstance(result, dict):
                    raise ValueError()
                if operation == 'tools/list':
                    names = {t.get('name') for t in result.get('tools', []) if isinstance(t, dict)
                             and isinstance(t.get('name'), str)}
                    present = 'publish' in names
                    after_notification = generation > 0 and generation == self.generation
                    self.refreshed |= after_notification and present and 'error' not in msg
                    self.emit({'event': 'tool_catalog', 'publish_present': present,
                               'after_notification': after_notification})
                elif operation in {'read', 'publish'}:
                    data = {}
                    for block in result.get('content', []):
                        if isinstance(block, dict) and isinstance(block.get('text'), str):
                            try:
                                value = json.loads(block['text'])
                            except ValueError:
                                continue
                            if isinstance(value, dict):
                                data.update(value)
                    failed = bool(result.get('isError') or 'error' in msg)
                    if operation == 'read':
                        entries = data.get('results', [data])
                        if (not isinstance(entries, list) or not 1 <= len(entries) <= 8
                                or any(not isinstance(entry, dict) for entry in entries)):
                            entries = []
                            self.dropped += 1
                        referenced = not failed and any('error' not in entry
                            and isinstance(entry.get('evidence_ref'), str) and bool(entry['evidence_ref'])
                            for entry in entries)
                        self.ready |= self.eligible and referenced
                        self.emit({'event': 'source_read', 'reference_returned': bool(referenced)})
                    else:
                        self.outcomes += 1
                        self.emit({'event': 'publication_outcome', 'tool_error': failed,
                                   'stored': data.get('stored') is True, 'queued': data.get('queued') is True})
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                self.dropped += 1  # Never echo malformed/source-controlled input.

    def finish(self):
        with self.lock:
            if self.finished:
                return
            self.finished = True
            status = ('attempted_with_receipt' if self.attempts else
                      'not_eligible' if not self.eligible else
                      'refreshed_with_publish_no_attempt' if self.ready and self.refreshed else
                      'ready_refresh_unobserved' if self.ready else 'no_referenced_read')
            # A started attempt is not necessarily a returned receipt.
            if self.attempts > self.outcomes:
                status = 'attempted_outcome_unobserved'
            self.emit({'event': 'summary', 'contract': 'colony.host-publication-trace/1',
                       'status': status, 'eligible': self.eligible, 'attempts': self.attempts,
                       'metadata_dropped': self.dropped, 'pending_requests': len(self.pending),
                       'model_visible_catalog': 'unobserved',
                       'abstention_reason': 'unobserved'}, final=True)


def relay(command, output, eligible):
    stream = MetadataSink(output)
    trace = Trace(stream, eligible)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=sys.stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except BaseException:
        stream.close()
        raise

    def requests():
        try:
            for line in iter(sys.stdin.buffer.readline, b''):
                trace.observe('request', line)
                process.stdin.write(line)
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threading.Thread(target=requests, daemon=True).start()
    try:
        for line in iter(process.stdout.readline, b''):
            # Record the notification generation before the host can react to it.
            # Observation only enqueues metadata; it never performs filesystem I/O.
            trace.observe('response', line)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        trace.finish()
        stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--eligible', action='store_true', help='Trusted host eligibility, never source intent')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A child MCP command is required')
    raise SystemExit(relay(command, args.output, args.eligible))
