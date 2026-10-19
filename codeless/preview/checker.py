"""预览自检：进程活着没有、端口通不通、页面和资源能不能正常返回、浏览器里有没有报错。

结果给两处用：agent 主动调用 check_preview 工具；每轮结束后编排侧自动检查，有问题就回灌给 agent 修。
"""

from __future__ import annotations

import base64
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ..sandbox import ManagedProcess, Sandbox


@dataclass
class CheckReport:
    ok: bool = True
    url: str = ""
    status: int | None = None
    title: str = ""
    text: str = ""
    problems: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    screenshot: str | None = None  # base64 JPEG
    browser: bool = False

    def problem(self, text: str) -> None:
        self.ok = False
        self.problems.append(text)

    def render(self) -> str:
        lines = [f"预览地址（沙箱内）：{self.url}"]
        if self.status is not None:
            lines.append(f"HTTP 状态：{self.status}")
        if self.title:
            lines.append(f"页面标题：{self.title}")
        if self.text:
            lines.append(f"页面文字（节选）：{self.text[:600]}")
        if self.browser:
            lines.append("已用无头浏览器打开页面检查控制台。")
        if self.console:
            lines.append("浏览器控制台：\n" + "\n".join(f"- {c}" for c in self.console[:15]))
        if self.problems:
            lines.append("发现的问题：\n" + "\n".join(f"- {p}" for p in self.problems))
        else:
            lines.append("没有发现问题。")
        return "\n".join(lines)


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []
        self.title = ""
        self._in_title = False
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "script" and a.get("src"):
            self.assets.append(a["src"] or "")
        if tag == "link" and (a.get("rel") or "").lower() in ("stylesheet", "modulepreload") and a.get("href"):
            self.assets.append(a["href"] or "")
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_ready(
    sandbox: Sandbox, proc: ManagedProcess, port: int, timeout: float = 90, cancel: threading.Event | None = None
) -> tuple[str, str]:
    """等后台服务把端口监听起来。返回 (状态, 说明)：ready / exited / localhost_only / timeout / cancelled。"""
    started = time.time()
    hinted = False
    while time.time() - started < timeout:
        if cancel is not None and cancel.is_set():
            return "cancelled", "已被用户中断"
        if not proc.running:
            return "exited", f"进程已退出（退出码 {proc.exit_code}）"
        host, p = sandbox.endpoint(port)
        if tcp_open(host, p):
            proc.ready = True
            return "ready", "端口已就绪"
        if not hinted and time.time() - started > 8:
            hinted = True
            if sandbox.port_open_inside(port):
                return "localhost_only", (
                    f"服务只监听了 127.0.0.1:{port}，外部访问不到。请让它监听 0.0.0.0"
                    "（例如 vite 加 --host 0.0.0.0，uvicorn 加 --host 0.0.0.0）"
                )
        time.sleep(0.5)
    return "timeout", f"等了 {int(timeout)} 秒端口 {port} 还没有监听"


def http_check(sandbox: Sandbox, port: int, path: str, report: CheckReport) -> None:
    host, p = sandbox.endpoint(port)
    base = f"http://{host}:{p}"
    report.url = f"http://localhost:{port}{path}"
    headers = {"Host": f"localhost:{port}", "Accept": "text/html,*/*", "Accept-Encoding": "identity"}
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers, trust_env=False) as client:
            resp = client.get(urljoin(base, path))
            report.status = resp.status_code
            body = resp.text
            if resp.status_code >= 400:
                report.problem(f"页面返回 HTTP {resp.status_code}：{_excerpt(body)}")
                return
            if "html" not in resp.headers.get("content-type", ""):
                report.text = _excerpt(body, 600)
                return
            parser = _Assets()
            try:
                parser.feed(body)
            except Exception:
                pass
            report.title = parser.title.strip()[:120]
            report.text = " ".join(parser.text)[:600]
            # 逐个请求页面引用的本站脚本和样式：Vite 编译错误、文件缺失都会在这里暴露
            for asset in parser.assets[:20]:
                url = urljoin(base + path, asset)
                if urlsplit(url).netloc != urlsplit(base).netloc:
                    continue
                r = client.get(url)
                if r.status_code >= 400:
                    report.problem(f"资源 {urlsplit(url).path} 返回 HTTP {r.status_code}：{_excerpt(r.text)}")
    except httpx.HTTPError as exc:
        report.problem(f"请求预览失败：{exc}")


def _excerpt(text: str, limit: int = 1200) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def browser_check(
    url: str, report: CheckReport, executable: str = "", screenshot: bool = True, args: tuple[str, ...] = ()
) -> bool:
    """用无头浏览器打开预览（经过编排服务的代理，和用户看到的一致），收集报错和截图。"""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    origin = urlsplit(url).netloc
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            kwargs = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage", *args]}
            if executable:
                kwargs["executable_path"] = executable
            browser = pw.chromium.launch(**kwargs)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                host = urlsplit(url).hostname or ""
                context.add_cookies([{"name": "codeless_check", "value": "1", "domain": host, "path": "/"}])
                page = context.new_page()
                # "Failed to load resource" 和下面的响应监听重复，而且不带地址，跳过
                page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}"[:500])
                        if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
                page.on("pageerror", lambda e: errors.append(f"页面异常：{e}"[:800]))

                def on_response(resp) -> None:
                    path = urlsplit(resp.url).path
                    if resp.status >= 400 and urlsplit(resp.url).netloc == origin and path != "/favicon.ico":
                        errors.append(f"请求 {path} 返回 {resp.status}")

                page.on("response", on_response)
                page.on("requestfailed", lambda r: errors.append(
                    f"请求失败：{urlsplit(r.url).path} {r.failure}") if urlsplit(r.url).netloc == origin else None)
                try:
                    page.goto(url, wait_until="load", timeout=30_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8_000)
                    except PlaywrightError:
                        pass
                    page.wait_for_timeout(800)
                    if not report.title:
                        report.title = page.title()[:120]
                    body_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                    report.text = re.sub(r"\s+", " ", body_text).strip()[:600]
                    if not body_text.strip() and not errors:
                        errors.append("页面渲染后是空白的（body 里没有任何文字）")
                    if screenshot:
                        shot = page.screenshot(type="jpeg", quality=70)
                        report.screenshot = base64.b64encode(shot).decode()
                except PlaywrightError as exc:
                    errors.append(f"打开页面失败：{str(exc).splitlines()[0][:300]}")
            finally:
                browser.close()
    except Exception as exc:  # 浏览器装得不对时退化成纯 HTTP 检查，不影响主流程
        report.console.append(f"（浏览器检查不可用：{str(exc).splitlines()[0][:200]}）")
        return False
    report.browser = True
    for line in errors:
        if "[vite] connecting" in line or "[vite] connected" in line:
            continue
        report.console.append(line)
        if line.startswith(("页面异常", "console.error", "请求", "打开页面失败", "页面渲染后")):
            report.problem(line)
    return True
