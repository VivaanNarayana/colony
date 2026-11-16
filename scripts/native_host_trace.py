"""Opt-in transparent stdio observer. Metadata only; never a model or completion hook."""
import argparse
import json
import subprocess
import sys
import threading


class Trace:
    def __init__(self, stream, eligible, limit=256):
        self.stream, self.eligible, self.limit = stream, eligible, limit
        self.pending = {}
        self.events = self.dropped = self.attempts = self.outcomes = 0
        self.ready = self.notified = self.refreshed = False
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
                            self.pending[ident] = operation
                    return
                if msg.get('method') == 'notifications/tools/list_changed':
                    self.notified = True
                    self.emit({'event': 'tools_list_changed'})
                    return
                operation = self.pending.pop(ident, None)
                result = msg.get('result', {})
                if not isinstance(result, dict):
                    raise ValueError()
                if operation == 'tools/list':
                    names = {t.get('name') for t in result.get('tools', []) if isinstance(t, dict)
                             and isinstance(t.get('name'), str)}
                    present = 'publish' in names
                    self.refreshed |= self.notified and present
                    self.emit({'event': 'tool_catalog', 'publish_present': present,
                               'after_notification': self.notified})
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
                        referenced = not failed and isinstance(data.get('evidence_ref'), str) and bool(data['evidence_ref'])
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
    try:
        stream = open(output, 'x', encoding='utf-8')
    except OSError:
        stream = None
    trace = Trace(stream, eligible)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=sys.stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

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
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            trace.observe('response', line)
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
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


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
