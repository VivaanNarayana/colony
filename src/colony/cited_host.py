"""Keep one task adapter alive until a trusted host supplies the final answer's citations."""
import asyncio
import hmac
import json
import os
import secrets
import socket
import sys
import threading
from contextlib import suppress
from pathlib import Path

import anyio

from colony.onboard_io import safe_path, write_new


def serve(server, endpoint: Path):
    """Control stdin/stdout belong to the host. Only MCP bytes reach the worker."""
    endpoint = safe_path(endpoint)
    if endpoint.is_relative_to(server.workflow.repo.resolve()):
        raise ValueError("Use a private endpoint outside the source repository")
    key = secrets.token_hex(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(30)
        write_new(endpoint, json.dumps([listener.getsockname()[1], key]).encode())
        try:
            conn, _ = listener.accept()
            with conn, conn.makefile("r", encoding="utf-8", newline="\n") as reader, \
                    conn.makefile("w", encoding="utf-8", newline="\n") as writer:
                conn.settimeout(5)
                if not hmac.compare_digest(reader.readline(66), key + "\n"):
                    raise ValueError("Invalid task connection")
                conn.settimeout(None)
                disconnected = False
                try:
                    asyncio.run(server.run_stdio_async(anyio.wrap_file(reader), anyio.wrap_file(writer)))
                except* (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    # A native worker may terminate its MCP relay after producing the answer.
                    # Completion still needs the private host signal and acknowledged reads.
                    disconnected = True
        finally:
            endpoint.unlink(missing_ok=True)
    # EOF or a failed worker never publishes. The host supplies only structured citations.
    line = sys.stdin.buffer.readline(131073)
    request = json.loads(line) if line and len(line) <= 131072 else []
    if isinstance(request, dict):
        sources = request.get("sources", [])
        confirmed = request.get("received_refs")
        if not isinstance(confirmed, list):
            confirmed = []
    else:
        sources = request
        confirmed = [] if disconnected else None
    print(json.dumps(server.complete(sources, received_refs=confirmed)), flush=True)


def relay(endpoint: Path):
    """One opaque stdio transport. Completion is never a worker tool or MCP message."""
    port, key = json.loads(safe_path(endpoint).read_bytes())
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.settimeout(None)
        conn.sendall(key.encode("ascii") + b"\n")

        def send():
            try:
                while block := os.read(sys.stdin.fileno(), 65536):
                    conn.sendall(block)
            except OSError:
                pass
            finally:
                with suppress(OSError):
                    conn.shutdown(socket.SHUT_WR)

        threading.Thread(target=send, daemon=True).start()
        while block := conn.recv(65536):
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    relay(Path(sys.argv[1]))
