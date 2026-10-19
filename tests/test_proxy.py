"""预览代理：按子域名分流、HTML 注入、去掉 X-Frame-Options、WebSocket 转发（Vite 热更新）。"""

from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from codeless.preview.proxy import PreviewProxy, Target

from conftest import free_port


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            body = b"<html><head><title>t</title></head><body>hi</body></html>"
            ctype = "text/html; charset=utf-8"
        else:
            body = f"path={self.path} host={self.headers['host']}".encode()
            ctype = "text/plain"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("x-frame-options", "DENY")
        self.end_headers()
        self.wfile.write(body)


async def fallback(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"main app"})


@pytest.fixture
def proxy():
    http_server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    ws_port = free_port()
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()

    async def echo(ws):
        async for message in ws:
            await ws.send(f"echo:{message}")

    async def run_ws():
        async with serve(echo, "127.0.0.1", ws_port):
            await stop.wait()

    ws_thread = threading.Thread(target=lambda: loop.run_until_complete(run_ws()), daemon=True)
    ws_thread.start()

    errors: list[tuple[str, dict, bool]] = []

    def resolve(project_id: str, port: int | None):
        if project_id != "abcdef1234":
            return None
        return Target(project_id, "127.0.0.1", ws_port if port == 5173 else http_server.server_address[1])

    app = PreviewProxy(fallback, "localhost", resolve, lambda pid, data, check: errors.append((pid, data, check)))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield port, errors
    server.should_exit = True
    http_server.shutdown()
    loop.call_soon_threadsafe(stop.set)
    ws_thread.join(timeout=5)


def get(port: int, path: str, host: str) -> httpx.Response:
    return httpx.get(f"http://127.0.0.1:{port}{path}", headers={"host": host}, trust_env=False)


def test_routes_by_subdomain(proxy):
    port, _ = proxy
    assert get(port, "/", f"127.0.0.1:{port}").text == "main app"
    page = get(port, "/", f"abcdef1234.localhost:{port}")
    assert '<head><script src="/__codeless__/inject.js"></script>' in page.text
    assert "x-frame-options" not in page.headers
    other = get(port, "/api/x?a=1", f"abcdef1234.localhost:{port}")
    # 开发服务器看到的 Host 是 localhost，和直接在沙箱里访问一样
    assert other.text.startswith("path=/api/x?a=1 host=localhost:")
    assert get(port, "/", f"zzzzzz9999.localhost:{port}").status_code == 404
    assert get(port, "/__codeless__/inject.js", f"abcdef1234.localhost:{port}").status_code == 200


def test_error_reports_flag_checker_requests(proxy):
    port, errors = proxy
    url = f"http://127.0.0.1:{port}/__codeless__/errors"
    host = f"abcdef1234.localhost:{port}"
    httpx.post(url, headers={"host": host}, json={"message": "boom"}, trust_env=False)
    httpx.post(url, headers={"host": host, "cookie": "codeless_check=1"}, json={"message": "x"}, trust_env=False)
    assert errors == [("abcdef1234", {"message": "boom"}, False), ("abcdef1234", {"message": "x"}, True)]


def test_websocket_is_forwarded(proxy):
    port, _ = proxy

    async def talk():
        async with connect(f"ws://abcdef1234-5173.localhost:{port}/", host="127.0.0.1", port=port, proxy=None) as ws:
            await ws.send("ping")
            return await asyncio.wait_for(ws.recv(), 5)

    assert asyncio.run(talk()) == "echo:ping"
