"""Shared test helpers: a real local HTTP server standing in for model services."""

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@dataclass
class Reply:
    body: object = None
    status: int = 200
    headers: dict = field(default_factory=dict)


class FakeApi:
    """Records requests and answers from a script, over a real socket."""

    def __init__(self):
        self.replies: list[Reply] = []
        self.requests: list[dict] = []
        self.url = ""

    def reply(self, body=None, status=200, headers=None) -> None:
        self.replies.append(Reply(body, status, headers or {}))


@pytest.fixture
def fake_api():
    api = FakeApi()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            api.requests.append(
                {
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": json.loads(self.rfile.read(length) or b"null"),
                }
            )
            reply = api.replies.pop(0) if api.replies else Reply({}, 500)
            raw = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    api.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield api
    server.shutdown()
    server.server_close()
    thread.join()
