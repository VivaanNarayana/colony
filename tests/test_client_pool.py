"""Real keep-alive reuse and MCP lifetime/credential boundaries; no timing tests."""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from test_client import local_client

import colony.mcp
from colony.models import Trail


def test_explicit_client_lifetime_reuses_one_connection_without_changing_requests(
    team, tmp_path, monkeypatch
):
    _, _, _, admin, key = team
    client = local_client(tmp_path, key, admin)
    accepted, requests = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers["Authorization"], body))
            data = json.dumps({"hit": False} if self.path == "/ask" else {"stored": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        def get_request(self):
            result = super().get_request()
            accepted.append(result[1])
            return result

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client.url = f"http://127.0.0.1:{server.server_port}"
    try:
        with client.pooled():
            transport = client._http
            assert client.ask("Retention?", "team") == {"hit": False}
            with client.pooled():
                assert client._http is transport  # Nested use does not close the outer pool.
                result = client.push(
                    Trail(
                        id=uuid.uuid4().hex,
                        namespace="team",
                        question="Retention?",
                        content="17 days.",
                        tainted=False,
                    )
                )
                assert result == {"stored": True}
            assert not transport.is_closed
        assert transport.is_closed and client._http is None
        assert len(accepted) == 1
        assert [r[0] for r in requests] == ["/ask", "/trails"]
        assert all(r[1] == client.headers["Authorization"] for r in requests)
        assert requests[0][2] == {"question": "Retention?", "namespace": "team"}
        # Trusted authored CLI/API trails retain their explicit taint choice.
        assert requests[1][2]["tainted"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mcp_pool_closes_on_failure_and_never_follows_credential_redirects(
    team, tmp_path, monkeypatch
):
    _, _, _, admin, key = team
    client = local_client(tmp_path, key, admin)
    monkeypatch.setattr(colony.mcp, "Client", lambda home: client)
    original = httpx.Client
    transports, requests = [], []
    code = 302

    def handle(request):
        requests.append(request)
        return httpx.Response(code, headers={"Location": "https://other.invalid/ask"})

    def factory(**kwargs):
        result = original(transport=httpx.MockTransport(handle), **kwargs)
        transports.append(result)
        return result

    monkeypatch.setattr(httpx, "Client", factory)
    server = colony.mcp.create_mcp(client.home)

    async def exercise():
        nonlocal code
        async with server.settings.lifespan(server):
            for status in [302, 403]:
                code = status
                with pytest.raises(httpx.HTTPStatusError):
                    client.ask("Retention?", "team")
            raise RuntimeError("synthetic shutdown")

    with pytest.raises(RuntimeError, match="synthetic shutdown"):
        asyncio.run(exercise())
    assert len(transports) == 1 and transports[0].is_closed and client._http is None
    assert len(requests) == 2
    assert all(str(r.url) == client.url + "/ask" for r in requests)
    assert all(r.headers["Authorization"] == client.headers["Authorization"] for r in requests)
