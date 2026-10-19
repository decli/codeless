"""预览反向代理：按 Host 头分流，<项目 id>.localhost:8000 转发到该项目沙箱里的开发服务器。

用子域名而不是子路径，站点在自己的根目录下运行，绝对路径不会失效（POC 踩过这个坑）。
HTML 响应里注入错误上报脚本；WebSocket 原样转发，Vite 的热更新可以直接用。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

INJECT_PATH = "/__codeless__/inject.js"
ERRORS_PATH = "/__codeless__/errors"
INJECT_JS = (Path(__file__).parent / "inject.js").read_bytes()
INJECT_TAG = f'<script src="{INJECT_PATH}"></script>'.encode()
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "content-length",
}
DROP_RESPONSE = HOP_HEADERS | {"x-frame-options"}
LABEL = re.compile(r"^([a-z0-9]{6,32})(?:-(\d{2,5}))?$")


@dataclass
class Target:
    project_id: str
    host: str
    port: int


Resolver = Callable[[str, int | None], Target | None]
ErrorSink = Callable[[str, dict, bool], None]


def _waiting_page(project_id: str, detail: str) -> bytes:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="3">
<title>预览未运行</title><style>
body{{margin:0;height:100vh;display:grid;place-items:center;font:14px/1.6 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#52525b;background:#fafafa}}
.box{{text-align:center;max-width:420px;padding:24px}}h1{{font-size:16px;font-weight:600;color:#18181b;margin:0 0 8px}}
code{{font:12px/1.5 ui-monospace,Menlo,monospace;color:#71717a}}</style></head>
<body><div class="box"><h1>预览还没有运行</h1><p>开发服务器启动后这里会自动刷新。</p><code>{detail}</code></div></body></html>""".encode()


class PreviewProxy:
    def __init__(self, app: ASGIApp, domain: str, resolve: Resolver, on_error: ErrorSink) -> None:
        self.app = app
        self.suffix = "." + domain.strip(".").lower()
        self.resolve = resolve
        self.on_error = on_error
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), trust_env=False)

    def _match(self, scope: Scope) -> tuple[str, int | None] | None:
        host = ""
        for key, value in scope.get("headers") or []:
            if key == b"host":
                host = value.decode("latin-1").lower()
                break
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if not name.endswith(self.suffix):
            return None
        match = LABEL.match(name[: -len(self.suffix)])
        if not match:
            return None
        return match.group(1), int(match.group(2)) if match.group(2) else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            hit = self._match(scope)
            if hit is not None:
                target = self.resolve(*hit)
                if scope["type"] == "http":
                    await self._http(scope, receive, send, hit[0], target)
                else:
                    await self._ws(scope, receive, send, target)
                return
        await self.app(scope, receive, send)

    # --- HTTP ---

    async def _http(self, scope: Scope, receive: Receive, send: Send, project_id: str, target: Target | None) -> None:
        path = scope.get("path", "/")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if path == INJECT_PATH:
            await _respond(send, 200, INJECT_JS, "application/javascript; charset=utf-8", cache=True)
            return
        if path == ERRORS_PATH:
            body = await _read_body(receive, limit=64_000)
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                data = {}
            from_check = "codeless_check=1" in headers.get("cookie", "")
            if isinstance(data, dict):
                self.on_error(project_id, data, from_check)
            await _respond(send, 204, b"", "text/plain")
            return
        if target is None:
            await _respond(send, 404, _waiting_page(project_id, "项目不存在"), "text/html; charset=utf-8")
            return

        query = scope.get("query_string", b"").decode("latin-1")
        url = f"http://{target.host}:{target.port}{path}" + (f"?{query}" if query else "")
        fwd = [(k, v) for k, v in headers.items() if k not in HOP_HEADERS and k != "accept-encoding"]
        fwd += [
            ("host", f"localhost:{target.port}"),
            ("accept-encoding", "identity"),
            ("x-forwarded-host", headers.get("host", "")),
            ("x-forwarded-proto", scope.get("scheme", "http")),
        ]
        body = await _read_body(receive, limit=50_000_000)
        request = self.client.build_request(scope["method"], url, headers=fwd, content=body)
        try:
            upstream = await self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            detail = "开发服务器没有在运行" if isinstance(exc, httpx.ConnectError) else str(exc)[:200]
            await _respond(send, 502, _waiting_page(project_id, detail), "text/html; charset=utf-8")
            return

        try:
            out_headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in upstream.headers.multi_items()
                if k.lower() not in DROP_RESPONSE
            ]
            ctype = upstream.headers.get("content-type", "")
            if "text/html" in ctype and scope["method"] != "HEAD":
                # aread 拿到的是解压后的内容，压缩头要去掉
                html = _inject(await upstream.aread())
                out_headers = [h for h in out_headers if h[0].lower() != b"content-encoding"]
                out_headers.append((b"content-length", str(len(html)).encode()))
                await send({"type": "http.response.start", "status": upstream.status_code, "headers": out_headers})
                await send({"type": "http.response.body", "body": html})
                return
            length = upstream.headers.get("content-length")
            if length:
                out_headers.append((b"content-length", length.encode()))
            await send({"type": "http.response.start", "status": upstream.status_code, "headers": out_headers})
            async for chunk in upstream.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        except httpx.HTTPError:
            pass
        finally:
            await upstream.aclose()

    # --- WebSocket ---

    async def _ws(self, scope: Scope, receive: Receive, send: Send, target: Target | None) -> None:
        websocket = WebSocket(scope, receive, send)
        if target is None:
            await websocket.close(code=1008)
            return
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        protocols = [p.strip() for p in (scope.get("subprotocols") or []) if p.strip()]
        query = scope.get("query_string", b"").decode("latin-1")
        uri = f"ws://{target.host}:{target.port}{scope.get('path', '/')}" + (f"?{query}" if query else "")
        try:
            upstream = await connect(
                uri,
                subprotocols=protocols or None,
                additional_headers={"Host": f"localhost:{target.port}", "Origin": f"http://localhost:{target.port}"},
                max_size=None,
                open_timeout=10,
                proxy=None,
            )
        except (TimeoutError, OSError, WebSocketException):
            await websocket.close(code=1011)
            return
        await websocket.accept(subprotocol=upstream.subprotocol)

        async def client_to_upstream() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
            except (WebSocketDisconnect, WebSocketException, RuntimeError):
                pass
            finally:
                await upstream.close()

        async def upstream_to_client() -> None:
            try:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            except (WebSocketException, RuntimeError):
                pass
            finally:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass

        await asyncio.gather(client_to_upstream(), upstream_to_client())

    async def aclose(self) -> None:
        await self.client.aclose()


def _inject(html: bytes) -> bytes:
    if INJECT_TAG in html:
        return html
    match = re.search(rb"<head[^>]*>", html, re.I)
    if match:
        return html[: match.end()] + INJECT_TAG + html[match.end():]
    return INJECT_TAG + html


async def _read_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size <= limit:
            chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


async def _respond(send: Send, status: int, body: bytes, ctype: str, cache: bool = False) -> None:
    headers = [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode())]
    headers.append((b"cache-control", b"max-age=300" if cache else b"no-store"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})

