"""HTTP 接口：项目、对话、文件、检查点、设置，外加预览反向代理和前端静态文件。

时间线用 SSE 推送：每个事件带递增序号作为 id，断线后浏览器自动带上 Last-Event-ID 续传。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import __version__
from . import workspace as ws
from .config import ROOT, Config
from .llm import Cancelled, ProviderError, make_provider
from .preview.proxy import PreviewProxy, Target
from .projects import Project, Services
from .sandbox import DockerUnavailable
from .settings import PRESETS, ModelProfile, _pick

WEB_DIST = ROOT / "web" / "dist"


# --- 请求体 ---


class ImageIn(BaseModel):
    media_type: str
    data: str


class MessageIn(BaseModel):
    text: str = ""
    images: list[ImageIn] = []


class ProjectIn(BaseModel):
    name: str = ""
    prompt: str = ""
    images: list[ImageIn] = []
    profile_id: str = ""
    starter: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    profile_id: str | None = None


class FileIn(BaseModel):
    path: str
    content: str


class RestoreIn(BaseModel):
    sha: str


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(services: Services) -> PreviewProxy:
    config = services.config

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(housekeeping())
        asyncio.get_running_loop().run_in_executor(None, services.check_sandbox)
        try:
            yield
        finally:
            task.cancel()
            for project in services.projects.loaded():
                project.stop()
                with contextlib.suppress(Exception):
                    project.timeline.save(force=True)
            await run_in_threadpool(services.sandboxes.shutdown)
            await proxy.aclose()

    app = FastAPI(title="codeless", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)

    def need(project_id: str) -> Project:
        project = services.projects.get(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        return project

    # --- 安全：只接受同源的写请求，防止预览页面里的脚本调用接口 ---

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            host = (request.headers.get("host") or "").lower()
            hostname = (host.rsplit(":", 1)[0] if not host.endswith("]") else host).strip("[]")
            if "*" not in config.trusted_hosts and hostname not in config.trusted_hosts:
                return JSONResponse(
                    {"detail": f"不允许的 Host：{hostname}。如需从其他地址访问，请把它加到 CODELESS_TRUSTED_HOSTS"},
                    status_code=403,
                )
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and origin.split("://", 1)[-1].lower() != host:
                    return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(DockerUnavailable)
    async def docker_error(_: Request, exc: DockerUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    # --- 基本信息与设置 ---

    @app.get("/api/info")
    def info() -> dict:
        return {
            "version": __version__,
            "sandbox": services.sandboxes.describe(),
            "sandbox_backend": config.sandbox_backend,
            "sandbox_status": services.sandbox_status,
            "network": services.network_mode,
            "preview_domain": config.preview_domain,
            "browser_check": services.browser_enabled(),
            "has_model": bool(services.settings.data.profiles),
        }

    @app.get("/api/settings")
    def get_settings() -> dict:
        return {
            **services.settings.data.public(),
            "presets": [p.__dict__ for p in PRESETS],
        }

    @app.post("/api/settings/profiles")
    def save_profile(data: dict) -> dict:
        if "api_key" in data and data["api_key"] in ("", None) and data.get("id") and not data.get("clear_key"):
            data["api_key"] = None  # 编辑时不填 Key 表示保留原来的
        data.pop("clear_key", None)
        profile = services.settings.upsert_profile(data)
        return profile.public()

    @app.delete("/api/settings/profiles/{profile_id}")
    def delete_profile(profile_id: str) -> dict:
        services.settings.delete_profile(profile_id)
        return {"ok": True}

    @app.post("/api/settings/profiles/{profile_id}/default")
    def default_profile(profile_id: str) -> dict:
        services.settings.set_default(profile_id)
        return {"ok": True}

    @app.put("/api/settings/agent")
    def save_agent(data: dict) -> dict:
        return services.settings.update_agent(data).__dict__

    @app.post("/api/settings/test")
    async def test_profile(data: dict) -> dict:
        """用一次很短的请求测试模型配置能不能用。没填 Key 时沿用已保存档案的 Key。"""
        merged: dict = {}
        if data.get("id"):
            existing = services.settings.data.profile(data["id"])
            if existing:
                merged = dict(existing.__dict__)
        merged.update({k: v for k, v in data.items() if not (k == "api_key" and not v)})
        profile = _pick(ModelProfile, merged)
        if not profile.model:
            raise HTTPException(400, "请填写模型名")

        def run() -> dict:
            started = time.time()
            provider = make_provider(profile)
            provider.max_attempts = 1
            resp = provider.complete(
                "你是连通性测试助手。", [{"role": "user", "content": "请只回复两个字：你好"}], [],
                max_tokens=2048 if profile.thinking else 64,
            )
            return {
                "ok": True,
                "reply": (resp.text or resp.reasoning or "").strip()[:200],
                "seconds": round(time.time() - started, 2),
                "usage": resp.usage.to_dict(),
            }

        try:
            return await asyncio.wait_for(run_in_threadpool(run), timeout=90)
        except ProviderError as exc:
            return {"ok": False, "error": str(exc)}
        except (TimeoutError, Cancelled):
            return {"ok": False, "error": "等了 90 秒没有响应"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # --- 项目 ---

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return services.projects.list()

    @app.post("/api/projects")
    def create_project(body: ProjectIn) -> dict:
        project = services.projects.create(body.name, body.starter)
        if body.profile_id:
            project.update({"profile_id": body.profile_id})
        if body.prompt.strip() or body.images:
            project.send(body.prompt, [i.model_dump() for i in body.images])
        return project.public()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return need(project_id).public()

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch) -> dict:
        project = need(project_id)
        project.update(body.model_dump(exclude_none=True))
        return project.public()

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict:
        need(project_id)
        await run_in_threadpool(services.projects.delete, project_id)
        return {"ok": True}

    # --- 对话 ---

    @app.get("/api/projects/{project_id}/timeline")
    def timeline(project_id: str) -> dict:
        return need(project_id).timeline.snapshot()

    @app.get("/api/projects/{project_id}/events")
    async def events(project_id: str, request: Request, after: int = -1):
        project = need(project_id)
        tl = project.timeline
        last = request.headers.get("last-event-id")
        seq = int(last) if last and last.isdigit() else after

        async def stream():
            loop = asyncio.get_running_loop()
            wake = asyncio.Event()

            def notify() -> None:
                loop.call_soon_threadsafe(wake.set)

            tl.listeners.add(notify)
            nonlocal seq
            try:
                yield "retry: 2000\n\n"
                if seq < 0:
                    snap = tl.snapshot()
                    seq = snap["seq"]
                    yield _sse("snapshot", snap, seq)
                while True:
                    wake.clear()
                    ops = tl.ops_after(seq)
                    if ops is None:
                        snap = tl.snapshot()
                        seq = snap["seq"]
                        yield _sse("snapshot", snap, seq)
                    elif ops:
                        seq = ops[-1]["seq"]
                        yield _sse("ops", ops, seq)
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=15)
                        await asyncio.sleep(0.03)  # 攒一下，流式输出时少发几次
                    except TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": ping\n\n"
            finally:
                tl.listeners.discard(notify)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/projects/{project_id}/messages")
    def send_message(project_id: str, body: MessageIn) -> dict:
        return need(project_id).send(body.text, [i.model_dump() for i in body.images])

    @app.post("/api/projects/{project_id}/stop")
    def stop(project_id: str) -> dict:
        need(project_id).stop()
        return {"ok": True}

    @app.post("/api/projects/{project_id}/fix")
    def fix(project_id: str) -> dict:
        return need(project_id).fix_errors()

    @app.post("/api/projects/{project_id}/errors/clear")
    def clear_errors(project_id: str) -> dict:
        need(project_id).clear_errors()
        return {"ok": True}

    @app.get("/api/projects/{project_id}/uploads/{name}")
    def upload(project_id: str, name: str):
        return _asset(need(project_id).root / "uploads", name)

    @app.get("/api/projects/{project_id}/shots/{name}")
    def shot(project_id: str, name: str):
        return _asset(need(project_id).root / "shots", name)

    # --- 文件 ---

    @app.get("/api/projects/{project_id}/files")
    def files(project_id: str) -> dict:
        project = need(project_id)
        return {"files": ws.walk(project.workspace)}

    @app.get("/api/projects/{project_id}/file")
    def read_file(project_id: str, path: str) -> dict:
        project = need(project_id)
        try:
            content, size = ws.read_text(project.workspace, path)
        except FileNotFoundError as exc:
            raise HTTPException(404, "文件不存在") from exc
        return {"path": path, "content": content, "size": size, "binary": content is None}

    @app.get("/api/projects/{project_id}/raw")
    def raw_file(project_id: str, path: str):
        project = need(project_id)
        target = ws.safe_path(project.workspace, path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # 只有图片在本站内联显示；其他类型（尤其是 HTML）一律下载，避免在界面的源里执行
        inline = media.startswith("image/") and media != "image/svg+xml"
        return FileResponse(
            target, media_type=media if inline else "application/octet-stream",
            filename=None if inline else target.name,
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    @app.put("/api/projects/{project_id}/file")
    def write_file(project_id: str, body: FileIn) -> dict:
        project = need(project_id)
        if project.running:
            raise HTTPException(409, "agent 正在工作，请等它结束或先停止再编辑文件")
        project.write_file(body.path, body.content)
        return {"ok": True}

    @app.get("/api/projects/{project_id}/download")
    async def download(project_id: str):
        project = need(project_id)
        data = await run_in_threadpool(ws.zip_workspace, project.workspace)
        safe = "".join(c for c in project.name if c.isalnum() or c in "-_ ").strip() or project.id
        return Response(
            data, media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(safe)}.zip"},
        )

    # --- 检查点 ---

    @app.get("/api/projects/{project_id}/checkpoints")
    def checkpoints(project_id: str) -> list[dict]:
        return ws.checkpoints(need(project_id).workspace, limit=100)

    @app.get("/api/projects/{project_id}/checkpoints/{sha}")
    def checkpoint(project_id: str, sha: str) -> dict:
        project = need(project_id)
        if not ws.REV.match(sha) or sha.endswith("^"):
            raise HTTPException(400, "检查点编号不对")
        parent = ws.git(project.workspace, "rev-parse", "--verify", "-q", f"{sha}^", check=False).stdout.strip()
        base = parent or "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git 的空树
        return {"sha": sha, "files": ws._numstat(project.workspace, base, sha)}

    @app.get("/api/projects/{project_id}/diff")
    def diff(project_id: str, sha: str, path: str) -> dict:
        project = need(project_id)
        return {"diff": ws.file_diff(project.workspace, sha, path)}

    @app.post("/api/projects/{project_id}/restore")
    def restore(project_id: str, body: RestoreIn) -> dict:
        project = need(project_id)
        try:
            info = project.restore(body.sha)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "checkpoint": info}

    # --- 进程与预览 ---

    @app.get("/api/projects/{project_id}/processes")
    def processes(project_id: str) -> list[dict]:
        box = services.sandboxes.peek(need(project_id).id)
        return [p.info() for p in box.processes.values()] if box else []

    @app.post("/api/projects/{project_id}/preview/restart")
    def restart_preview(project_id: str) -> dict:
        project = need(project_id)
        if not project.meta.get("preview"):
            raise HTTPException(400, "还没有预览进程。让 agent 把程序跑起来后就能预览了。")
        return {"ok": project.restart_preview()}

    @app.post("/api/projects/{project_id}/processes/{name}/stop")
    def stop_process(project_id: str, name: str) -> dict:
        project = need(project_id)
        result = project.stop_process(name)
        return {"ok": result.ok, "message": result.content}

    @app.get("/api/projects/{project_id}/processes/{name}/logs")
    async def process_logs(project_id: str, name: str, request: Request):
        project = need(project_id)

        async def stream():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[str] = asyncio.Queue()
            current = None
            yield "retry: 3000\n\n"
            try:
                while True:
                    box = services.sandboxes.peek(project.id)
                    proc = box.processes.get(name) if box else None
                    if proc is not current:
                        if current is not None:
                            current.listeners[:] = [x for x in current.listeners if getattr(x, "_q", None) is not queue]
                        current = proc
                        while not queue.empty():
                            queue.get_nowait()
                        if proc is not None:
                            def listener(text: str) -> None:
                                loop.call_soon_threadsafe(queue.put_nowait, text)
                            listener._q = queue  # type: ignore[attr-defined]
                            proc.listeners.append(listener)
                        yield _sse("reset", {"log": proc.log()[-200_000:] if proc else "", "info": proc.info() if proc else None})
                    try:
                        text = await asyncio.wait_for(queue.get(), timeout=2)
                        chunks = [text]
                        while not queue.empty() and len(chunks) < 200:
                            chunks.append(queue.get_nowait())
                        yield _sse("log", "".join(chunks))
                    except TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": ping\n\n"
            finally:
                if current is not None:
                    current.listeners[:] = [x for x in current.listeners if getattr(x, "_q", None) is not queue]

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- 前端 ---

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        if not WEB_DIST.is_dir():
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>codeless</title>"
                "<p style='font:14px system-ui;padding:24px'>前端还没有构建，请先执行 <code>make web</code>。</p>"
            )
        target = (WEB_DIST / path).resolve()
        if path and target.is_file() and WEB_DIST.resolve() in target.parents:
            cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    # --- 后台任务 ---

    async def housekeeping() -> None:
        tick = 0
        while True:
            await asyncio.sleep(2)
            tick += 1
            for project in services.projects.loaded():
                with contextlib.suppress(Exception):
                    await run_in_threadpool(project.timeline.save)
            if tick % 30 == 0:
                with contextlib.suppress(Exception):
                    await run_in_threadpool(services.sandboxes.reap_idle)

    # --- 预览代理 ---

    def resolve(project_id: str, port: int | None) -> Target | None:
        project = services.projects.get(project_id)
        if project is None:
            return None
        box = project.sandbox()
        box.touch()
        if port is None:
            proc = box.processes.get("preview")
            if proc is None:
                project.ensure_preview()
            port = (proc.port if proc else None) or box.preview_port
        host, real_port = box.endpoint(port)
        return Target(project_id, host, real_port)

    def on_error(project_id: str, data: dict, from_check: bool) -> None:
        if from_check:
            return
        project = services.projects.get(project_id)
        if project is not None:
            project.record_runtime_error(data)

    proxy = PreviewProxy(app, config.preview_domain, resolve, on_error)
    return proxy


def _asset(folder: Path, name: str):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "文件不存在")
    target = folder / name
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=86400"})


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text)


def build(config: Config | None = None) -> tuple[PreviewProxy, Services]:
    services = Services(config or Config.load())
    return create_app(services), services
