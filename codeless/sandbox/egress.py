"""沙箱出网白名单代理：只放行包管理源，其余一律 403。

只用标准库，可以单独跑：`ALLOWED_HOSTS=pypi.org,... python3 egress.py`。
编排服务把这份源码通过 `python3 -c` 传进一个独立容器运行，沙箱容器通过 HTTP(S)_PROXY 指向它。
沙箱所在网络是 internal 的，这个代理是它唯一的出口。

支持 HTTPS 的 CONNECT 隧道和明文 HTTP 转发；UPSTREAM_PROXY 设置后经上游代理出网（企业内网常见）。
"""

import asyncio
import base64
import os
import sys
from urllib.parse import urlsplit

ALLOWED = [h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
UPSTREAM = os.environ.get("UPSTREAM_PROXY", "").strip()
PORT = int(os.environ.get("LISTEN_PORT", "3128"))


def allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in ALLOWED)


def log(*parts: object) -> None:
    print(*parts, flush=True, file=sys.stdout)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def open_upstream(host: str, port: int):
    if not UPSTREAM:
        return await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    proxy = urlsplit(UPSTREAM if "://" in UPSTREAM else f"http://{UPSTREAM}")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy.hostname, proxy.port or 80), 20
    )
    auth = ""
    if proxy.username:
        token = base64.b64encode(f"{proxy.username}:{proxy.password or ''}".encode()).decode()
        auth = f"Proxy-Authorization: Basic {token}\r\n"
    writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{auth}\r\n".encode())
    await writer.drain()
    status = await reader.readline()
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
    if b" 200" not in status:
        writer.close()
        raise ConnectionError(status.decode("latin-1").strip())
    return reader, writer


def deny(writer: asyncio.StreamWriter, host: str) -> None:
    body = (
        f"codeless 沙箱出网代理拦截：{host} 不在白名单。"
        "沙箱只能访问包管理源（npm、PyPI 及其镜像），需要更多域名请在 SANDBOX_ALLOWED_HOSTS 里添加。\n"
    ).encode()
    writer.write(
        b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except Exception:
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return

    if method.upper() == "CONNECT":
        host, _, port_text = target.rpartition(":")
        host = host.strip("[]")
        port = int(port_text or 443)
    else:
        url = urlsplit(target)
        host, port = url.hostname or "", url.port or 80

    if not allowed(host):
        log("deny", method, host)
        deny(writer, host)
        await writer.drain()
        writer.close()
        return

    try:
        up_reader, up_writer = await open_upstream(host, port)
    except Exception as exc:
        log("fail", host, exc)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    log("allow", method, host)
    if method.upper() == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
    else:
        # 明文 HTTP：改写成源站格式的请求行，去掉代理专用头，一个连接只走一个请求
        url = urlsplit(target)
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        headers = [
            line for line in lines[1:]
            if line and not line.lower().startswith(("proxy-", "connection:", "keep-alive:"))
        ]
        request = f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\nConnection: close\r\n\r\n"
        up_writer.write(request.encode("latin-1"))
        await up_writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=1 << 16)
    log(f"egress proxy on :{PORT}, allow: {', '.join(ALLOWED) or '(none)'}",
        f"upstream: {UPSTREAM or 'direct'}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
