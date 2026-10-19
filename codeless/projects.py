"""项目：一个工作区 + 一段对话 + 一个沙箱。把 agent 循环、沙箱、预览、检查点串起来。

目录结构（data/projects/<id>/）：
    meta.json       名称、所用模型、累计用量、预览启动命令
    workspace/      项目代码（git 仓库，每轮结束提交一个检查点）
    messages.json   发给模型的完整历史
    timeline.json   界面上的时间线
    uploads/        用户贴的图片
    shots/          预览截图
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from . import workspace as ws
from .agent.prompts import system_prompt
from .agent.session import AgentLoop
from .agent.timeline import Timeline
from .agent.tools import ToolHost, ToolResult
from .config import Config
from .llm import Usage
from .preview.checker import CheckReport, browser_check, http_check, wait_ready
from .sandbox import ManagedProcess, Sandbox, SandboxManager
from .settings import SettingsStore

PROJECT_ID = re.compile(r"^[a-z0-9]{6,32}$")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def short_title(text: str, limit: int = 24) -> str:
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= limit else line[:limit].rstrip() + "…"


def listening_ports(box: Sandbox) -> list[int]:
    """沙箱里正在监听的端口，用来提示"服务起在了别的端口上"。本地模式会看到整台机器的端口，不用。"""
    if box.kind != "docker":
        return []
    out = box.exec("cat /proc/net/tcp /proc/net/tcp6 2>/dev/null", timeout=10).output
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[3] == "0A" and ":" in parts[1]:
            try:
                ports.add(int(parts[1].rsplit(":", 1)[1], 16))
            except ValueError:
                pass
    return sorted(ports)


class Services:
    """进程级的共享对象。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.settings = SettingsStore(config.settings_file)
        self.sandboxes = SandboxManager(config)
        self.projects = ProjectManager(self)
        self.sandbox_status = "checking"  # checking | ok | 错误说明
        self._browser_failed_at = 0.0

    @property
    def network_mode(self) -> str:
        return "local" if self.config.sandbox_backend == "local" else self.config.sandbox_network

    def check_sandbox(self) -> None:
        if self.sandboxes.docker_env is None:
            self.sandbox_status = "ok"
            return
        try:
            self.sandboxes.docker_env.ensure()
            self.sandbox_status = "ok"
        except Exception as exc:
            self.sandbox_status = str(exc)

    # --- 预览自检用的浏览器 ---

    def browser_enabled(self) -> bool:
        mode = self.config.browser_check
        if mode in ("off", "0", "false", "no"):
            return False
        if mode == "auto" and time.time() - self._browser_failed_at < 600:
            return False
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def browser_failed(self) -> None:
        self._browser_failed_at = time.time()

    def check_url(self, project_id: str, path: str) -> tuple[str, tuple[str, ...]]:
        """浏览器检查走编排服务自己的预览代理，看到的和用户一样（包括注入的错误上报脚本）。"""
        domain = self.config.preview_domain
        url = f"http://{project_id}.{domain}:{self.config.port}{path}"
        target = self.config.host if self.config.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        args: tuple[str, ...] = ()
        if domain != "localhost" or target != "127.0.0.1":
            args = (f"--host-resolver-rules=MAP *.{domain} {target}",)
        return url, args


class Project(ToolHost):
    def __init__(self, services: Services, root: Path, meta: dict) -> None:
        self.services = services
        self.root = root
        self.meta = meta
        self.id: str = meta["id"]
        self.workspace = root / "workspace"
        self.timeline = Timeline(root / "timeline.json")
        self.messages: list[dict] = _read_json(root / "messages.json", [])
        self.system = system_prompt(services.network_mode)
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        self.queue: list[dict] = []
        self.provider = None
        self.last_prompt_tokens = 0
        self.last_prompt_count = 0
        self.current_tool_item: str | None = None
        self.turn_request = ""
        self.turn_started = 0.0
        self.turn_touched = False
        self.notes: list[str] = []  # 下一轮要告诉模型的事：回滚、用户手动改了文件……
        self.runtime_errors: deque[dict] = deque(maxlen=30)
        self.errors_told = 0.0  # 用户浏览器报错里，已经在上下文里告诉过模型的截止时间
        self._running = False
        self._preview_starting = False
        self._preview_tried = 0.0
        self._shot_counter = 0
        AgentLoop(self)._close_dangling_calls()  # 上次进程在一轮中途退出的话，补齐历史
        self.timeline.set_state(running=False, activity=None)
        self._update_preview_state()

    # --- 基本信息 ---

    @property
    def name(self) -> str:
        return self.meta.get("name") or "未命名项目"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def vision(self) -> bool:
        profile = self.profile()
        return bool(profile and profile.vision)

    @property
    def command_timeout(self) -> int:
        return self.services.settings.data.agent.command_timeout

    def profile(self):
        data = self.services.settings.data
        return data.profile(self.meta.get("profile_id") or None) or data.profile(None)

    def public(self) -> dict:
        profile = self.profile()
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.meta.get("created_at"),
            "updated_at": self.meta.get("updated_at"),
            "profile_id": self.meta.get("profile_id") or "",
            "profile_name": profile.name if profile else "",
            "usage": self.meta.get("usage") or Usage().to_dict(),
            "cost": round(self.meta.get("cost", 0.0), 5),
            "running": self.running,
            "preview": self.preview_info(),
        }

    def save_meta(self) -> None:
        _write_json(self.root / "meta.json", self.meta)

    def save_messages(self) -> None:
        _write_json(self.root / "messages.json", self.messages)

    def update(self, data: dict) -> None:
        if "name" in data:
            name = str(data["name"] or "").strip()[:60]
            if name:
                self.meta["name"] = name
                self.meta["auto_name"] = False
        if "profile_id" in data:
            self.meta["profile_id"] = str(data["profile_id"] or "")
        self.save_meta()

    def sandbox(self) -> Sandbox:
        return self.services.sandboxes.get(self.id, self.workspace)

    # --- 对话 ---

    def _store_images(self, images: list[dict] | None) -> list[dict]:
        stored = []
        for img in (images or [])[:MAX_IMAGES]:
            media = str(img.get("media_type") or "")
            data = str(img.get("data") or "")
            if media not in IMAGE_TYPES or not data:
                raise ValueError("只支持 PNG、JPEG、GIF、WebP 图片")
            if "," in data[:100] and data.startswith("data:"):
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=False)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("图片太大了，单张不能超过 5 MB")
            name = hashlib.sha1(raw).hexdigest()[:16] + "." + IMAGE_TYPES[media]
            target = self.root / "uploads" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(raw)
            stored.append({"media_type": media, "data": base64.b64encode(raw).decode(), "name": name})
        return stored

    def _image_urls(self, stored: list[dict]) -> list[str]:
        return [f"/api/projects/{self.id}/uploads/{img['name']}" for img in stored]

    def send(self, text: str, images: list[dict] | None = None) -> dict:
        text = (text or "").strip()
        stored = self._store_images(images)
        if not text and not stored:
            raise ValueError("消息不能为空")
        with self.lock:
            if self._running:
                item = self.timeline.add("user", text=text, images=self._image_urls(stored), queued=True)
                self.queue.append({"text": text, "images": stored, "item": item["id"]})
                return {"queued": True}
            self.timeline.add("user", text=text, images=self._image_urls(stored))
            self._start_turn(text, stored)
        return {"queued": False}

    def _start_turn(self, text: str, images: list[dict]) -> None:
        if self.meta.get("auto_name", True) and not self.messages:
            self.meta["name"] = short_title(text) or self.name
        self.cancel = threading.Event()
        self.turn_request = text
        self.turn_started = time.time()
        self.turn_touched = False
        self._commit_manual_changes()
        msg: dict = {"role": "user", "content": text + self._context_block()}
        if images:
            msg["images"] = [{"media_type": i["media_type"], "data": i["data"]} for i in images]
        self.messages.append(msg)
        self.save_messages()
        self._running = True
        self.services.sandboxes.busy.add(self.id)
        self.timeline.set_state(running=True, activity={"kind": "waiting"}, turn_usage=None, todos=None)
        threading.Thread(target=AgentLoop(self).run, daemon=True, name=f"agent-{self.id}").start()

    def stop(self) -> None:
        self.cancel.set()

    def drain_queue(self) -> None:
        with self.lock:
            items, self.queue = self.queue, []
        for q in items:
            msg: dict = {"role": "user", "content": f"[用户插话] {q['text']}"}
            if q["images"]:
                msg["images"] = [{"media_type": i["media_type"], "data": i["data"]} for i in q["images"]]
            self.messages.append(msg)
            self.timeline.update(q["item"], queued=False)
            self.turn_request += f"\n（补充）{q['text']}"
        if items:
            self.save_messages()

    def has_queue(self) -> bool:
        return bool(self.queue)

    def last_user_request(self) -> str:
        return self.turn_request

    def prepare(self) -> None:
        """一轮开始前把沙箱拉起来（第一次可能要几秒），Docker 不可用时尽早报错。"""
        self.timeline.set_state(activity={"kind": "preparing"})
        self.sandbox().start()

    def add_usage(self, usage: Usage, profile) -> None:
        total = Usage.from_dict(self.meta.get("usage"))
        total.add(usage)
        self.meta["usage"] = total.to_dict()
        if profile is not None and profile.priced:
            self.meta["cost"] = self.meta.get("cost", 0.0) + profile.cost(usage)
        self.timeline.set_state(usage_total={**total.to_dict(), "cost": round(self.meta.get("cost", 0.0), 5)})
        self.save_meta()

    def finish_turn(self, outcome: str, usage_view: dict) -> None:
        checkpoint = None
        try:
            checkpoint = ws.commit_all(self.workspace, short_title(self.turn_request, 60) or "agent 修改")
        except Exception as exc:
            self.timeline.add("notice", level="warn", text=f"保存检查点失败：{exc}")
        if checkpoint:
            checkpoint["files"] = checkpoint["files"][:80]
            # 代码改过了，预览会重新加载；之前上报的错误已经过时，还在的话会被重新上报
            if self.runtime_errors:
                self.runtime_errors.clear()
                self._publish_errors()
        self.timeline.add("turn_end", outcome=outcome, usage=usage_view, checkpoint=checkpoint)
        with self.lock:
            pending = self.queue
            self.queue = []
            if outcome == "stopped":
                for q in pending:
                    self.timeline.update(q["item"], queued=False, discarded=True)
                pending = []
            self._running = False
            self.services.sandboxes.busy.discard(self.id)
            self.meta["updated_at"] = time.time()
            self.save_meta()
            self.timeline.set_state(running=False, activity=None)
            self.timeline.save(force=True)
            if pending:
                # 刚好在本轮收尾时发来的消息：接着开一轮
                for q in pending:
                    self.timeline.update(q["item"], queued=False)
                text = "\n\n".join(q["text"] for q in pending)
                self._start_turn(text, [img for q in pending for img in q["images"]])

    def _commit_manual_changes(self) -> None:
        try:
            if ws.commit_all(self.workspace, "手动修改"):
                self.timeline.add("notice", level="info", text="检测到项目文件有手动修改，已保存为检查点。")
        except Exception:
            pass

    def _context_block(self) -> str:
        parts: list[str] = []
        if self.notes:
            parts += self.notes
            self.notes = []
        files = [e["path"] for e in ws.walk(self.workspace, max_entries=300) if e["type"] == "file"]
        files = [f for f in files if f != ".gitignore"]
        if not files:
            parts.append("项目目录目前是空的。")
        else:
            shown = "\n".join(files[:80])
            more = f"\n…等共 {len(files)} 个文件" if len(files) > 80 else ""
            parts.append(f"项目文件：\n{shown}{more}")
        agents = self.workspace / "AGENTS.md"
        if agents.is_file():
            text = agents.read_text(encoding="utf-8", errors="replace")
            parts.append("AGENTS.md：\n" + (text[:4000] + "\n…" if len(text) > 4000 else text))
        box = self.services.sandboxes.peek(self.id)
        procs = list(box.processes.values()) if box else []
        if procs:
            parts.append("后台进程：\n" + "\n".join(
                f"- {p.name}：{'运行中' if p.running else f'已退出（退出码 {p.exit_code}）'}，命令 {p.command}"
                for p in procs))
        elif self.meta.get("preview"):
            parts.append(f"预览进程当前没有运行，之前的启动命令是：{self.meta['preview'].get('command')}")
        fresh = [e for e in self.runtime_errors if e["at"] > self.errors_told]
        if fresh:
            self.errors_told = time.time()
            parts.append("用户浏览器里上报的运行时错误：\n" + "\n".join(
                f"- [{e['kind']}] {e['message'][:300]}" for e in fresh[-8:]))
        return "\n\n<context>\n" + "\n\n".join(parts) + "\n</context>"

    # --- 预览 ---

    def preview_info(self) -> dict:
        box = self.services.sandboxes.peek(self.id)
        proc = box.processes.get("preview") if box else None
        cfg = self.meta.get("preview") or {}
        info: dict = {"command": (proc.command if proc else cfg.get("command")) or "", "exit_code": None}
        if proc is None:
            info["status"] = "starting" if self._preview_starting else ("idle" if cfg else "none")
        elif proc.running:
            info["status"] = "running" if proc.ready else "starting"
            info["port"] = proc.port
        else:
            info["status"] = "exited"
            info["exit_code"] = proc.exit_code
        return info

    def _update_preview_state(self) -> None:
        self.timeline.set_state(preview=self.preview_info())

    def _watch(self, proc: ManagedProcess) -> None:
        def wait() -> None:
            if proc.popen is not None:
                proc.popen.wait()
            time.sleep(0.3)
            self._update_preview_state()

        threading.Thread(target=wait, daemon=True).start()

    def preview_port(self) -> int:
        box = self.sandbox()
        proc = box.processes.get("preview")
        return (proc.port if proc else None) or box.preview_port

    def ensure_preview(self) -> None:
        """有人打开预览但进程不在（编排服务重启过或闲置被回收）：按记下的命令在后台重新拉起来。
        进程自己崩溃退出的不自动拉，让用户看到出错了。"""
        cfg = self.meta.get("preview")
        if not cfg or self._running or self._preview_starting:
            return
        box = self.services.sandboxes.peek(self.id)
        if box is not None and "preview" in box.processes:
            return
        if time.time() - self._preview_tried < 15:
            return
        self._preview_tried = time.time()
        self.restart_preview()

    def restart_preview(self) -> bool:
        cfg = self.meta.get("preview")
        if not cfg or self._preview_starting:
            return False
        self._preview_starting = True
        self._update_preview_state()

        def run() -> None:
            try:
                box = self.sandbox()
                port = cfg.get("port") or box.preview_port
                proc = box.spawn("preview", cfg["command"], port)
                self._watch(proc)
                self._preview_starting = False
                self._update_preview_state()
                wait_ready(box, proc, port, timeout=120)
            except Exception as exc:
                self.timeline.add("notice", level="error", text=f"启动预览失败：{exc}")
            finally:
                self._preview_starting = False
                self._update_preview_state()

        threading.Thread(target=run, daemon=True).start()
        return True

    def record_runtime_error(self, data: dict) -> None:
        message = str(data.get("message") or "")[:2000]
        if not message:
            return
        now = time.time()
        for entry in self.runtime_errors:
            if entry["message"] == message:
                entry["count"] += 1
                entry["at"] = now
                break
        else:
            self.runtime_errors.append({
                "kind": str(data.get("kind") or "error")[:20],
                "message": message,
                "stack": str(data.get("stack") or "")[:4000],
                "url": str(data.get("url") or "")[:300],
                "at": now,
                "count": 1,
            })
        self._publish_errors()

    def _publish_errors(self) -> None:
        self.timeline.set_state(preview_errors=[
            {k: e[k] for k in ("kind", "message", "url", "at", "count")} for e in list(self.runtime_errors)[-10:]
        ])

    def clear_errors(self) -> None:
        self.runtime_errors.clear()
        self._publish_errors()

    def fix_errors(self) -> dict:
        errors = list(self.runtime_errors)
        if not errors:
            raise ValueError("目前没有收到预览里的报错")
        lines = []
        for e in errors[-8:]:
            line = f"- [{e['kind']}] {e['message'][:500]}"
            if e["stack"] and e["stack"] not in e["message"]:
                line += "\n  " + e["stack"][:600].replace("\n", "\n  ")
            lines.append(line)
        self.errors_told = time.time()
        self.clear_errors()
        return self.send("预览页面里出现了下面这些错误，请找到原因并修复：\n" + "\n".join(lines))

    # --- 检查 ---

    def _run_check(self, path: str, screenshot: bool, cancel: threading.Event | None = None) -> CheckReport:
        report = CheckReport()
        box = self.sandbox()
        proc = box.processes.get("preview")
        if proc is None:
            report.problem("预览进程没有在运行。有界面或接口的程序请用 start_process 启动（name 用 preview）。")
            return report
        port = proc.port or box.preview_port
        if not proc.running:
            report.problem(f"预览进程已退出（退出码 {proc.exit_code}），最近日志：\n{proc.tail(40)}")
            return report
        status, detail = wait_ready(box, proc, port, timeout=45, cancel=cancel)
        if status != "ready":
            report.problem(f"{detail}。最近日志：\n{proc.tail(30)}")
            return report
        http_check(box, port, path, report)
        if self.services.browser_enabled():
            url, args = self.services.check_url(self.id, path)
            if not browser_check(url, report, self.services.config.browser_path, screenshot, args):
                self.services.browser_failed()
        if not report.ok:
            tail = proc.tail(20).strip()
            if tail:
                report.console.append("预览进程最近日志：\n" + tail)
        return report

    def auto_verify(self) -> CheckReport | None:
        """每轮结束后的自检：只在有预览进程、而且这一轮动过代码或进程时才查。"""
        box = self.services.sandboxes.peek(self.id)
        if box is None or "preview" not in box.processes:
            return None
        dirty = bool(ws.git(self.workspace, "status", "--porcelain", check=False).stdout.strip())
        if not dirty and not self.turn_touched:
            return None
        self.timeline.set_state(activity={"kind": "checking"})
        item = self.timeline.add("tool", name="check_preview", title="自动检查预览", status="running",
                                 output="", args={}, auto=True)
        report = self._run_check("/", screenshot=False, cancel=self.cancel)
        self.timeline.update(item["id"], status="ok" if report.ok else "error", output=report.render()[:6000],
                             detail={"ok": report.ok, "problems": report.problems[:10]}, ended=time.time())
        return report

    # --- 工具宿主 ---

    def start_process(self, name: str, command: str, port: int | None) -> ToolResult:
        if not command:
            return ToolResult("命令为空", ok=False)
        box = self.sandbox()
        if name == "preview" and port is None:
            port = box.preview_port
        self.turn_touched = True
        proc = box.spawn(name, command, port)
        if name == "preview":
            self.meta["preview"] = {"command": command, "port": port}
            self.save_meta()
            self._watch(proc)
            self._update_preview_state()
        if port:
            status, detail = wait_ready(box, proc, port, timeout=120, cancel=self.cancel)
        else:
            self.cancel.wait(3)
            status, detail = ("started", "") if proc.running else ("exited", f"进程已退出（退出码 {proc.exit_code}）")
        if name == "preview":
            self._update_preview_state()
        tail = proc.tail(40).strip() or "（暂无输出）"
        if status == "ready":
            text = f"{name} 已启动，端口 {port} 就绪。"
            if name == "preview":
                text += "用户现在能在右侧看到预览，可以用 check_preview 检查页面。"
        elif status == "started":
            text = f"{name} 已在后台启动。"
        else:
            text = f"{name} 启动失败：{detail}。"
            if status == "timeout":
                others = [p for p in listening_ports(box) if p != port]
                if others:
                    text += f"沙箱里正在监听的端口有 {', '.join(map(str, others))}，服务可能没有用 $PORT（{port}）。"
        return ToolResult(f"{text}\n最近日志：\n{tail}", ok=status in ("ready", "started"),
                          detail={"status": status, "port": port})

    def process_logs(self, name: str, lines: int) -> ToolResult:
        box = self.services.sandboxes.peek(self.id)
        proc = box.processes.get(name) if box else None
        if proc is None:
            names = ", ".join(box.processes) if box and box.processes else "无"
            return ToolResult(f"没有名为 {name} 的后台进程。当前进程：{names}", ok=False)
        state = "运行中" if proc.running else f"已退出（退出码 {proc.exit_code}）"
        return ToolResult(f"[{name}：{state}，命令 {proc.command}]\n{proc.tail(lines).strip() or '（暂无输出）'}")

    def stop_process(self, name: str) -> ToolResult:
        box = self.services.sandboxes.peek(self.id)
        if box is None or not box.kill(name):
            return ToolResult(f"没有名为 {name} 的后台进程", ok=False)
        self.turn_touched = True
        if name == "preview":
            self.meta["preview"] = None
            self.save_meta()
            box.processes.pop("preview", None)
            self._update_preview_state()
        return ToolResult(f"已停止 {name}")

    def check_preview(self, path: str) -> ToolResult:
        report = self._run_check(path, screenshot=True, cancel=self.cancel)
        detail: dict = {"ok": report.ok, "problems": report.problems[:10]}
        images = []
        if report.screenshot:
            detail["screenshot"] = self._save_shot(report.screenshot)
            if self.vision:
                images.append({"media_type": "image/jpeg", "data": report.screenshot})
        text = report.render()
        recent = [e for e in self.runtime_errors if time.time() - e["at"] < 180]
        if recent:
            text += "\n用户浏览器里最近 3 分钟上报的运行时错误（可能已经修复）：\n" + "\n".join(
                f"- [{e['kind']}] {e['message'][:300]}" for e in recent[-6:])
        return ToolResult(text, ok=report.ok, images=images, detail=detail)

    def _save_shot(self, data: str) -> str:
        self._shot_counter += 1
        name = f"{int(time.time())}-{self._shot_counter}.jpg"
        target = self.root / "shots" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data))
        shots = sorted(target.parent.glob("*.jpg"))
        for old in shots[:-60]:
            old.unlink(missing_ok=True)
        return f"/api/projects/{self.id}/shots/{name}"

    def set_todos(self, todos: list[dict]) -> None:
        self.timeline.set_state(todos=todos)

    def save_output(self, text: str) -> str:
        folder = self.workspace / ".codeless" / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"output-{int(time.time() * 1000)}.log"
        (folder / name).write_text(text, encoding="utf-8")
        return f".codeless/outputs/{name}"

    # --- 文件与检查点 ---

    def write_file(self, rel: str, content: str) -> None:
        target = ws.safe_path(self.workspace, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        note = f"用户手动修改了文件 {target.relative_to(self.workspace.resolve()).as_posix()}，修改前请重新读取。"
        if note not in self.notes:
            self.notes.append(note)

    def restore(self, sha: str) -> dict | None:
        if self._running:
            raise RuntimeError("agent 正在工作，请先停止再恢复")
        subject = ws.git(self.workspace, "log", "-1", "--format=%s", ws.check_rev(sha), "--", check=False).stdout.strip()
        if not subject:
            raise ValueError("找不到这个检查点")
        self._commit_manual_changes()
        info = ws.restore(self.workspace, sha, f"恢复到「{subject}」")
        self.notes.append(
            f"用户把代码恢复到了检查点「{subject}」，这之后的改动已撤销。请以当前文件内容为准，修改前重新读取。"
        )
        self.timeline.add("notice", level="info", text=f"已把代码恢复到「{subject}」。", restore=sha)
        return info

    def delete(self) -> None:
        self.stop()
        self.services.sandboxes.destroy(self.id)
        shutil.rmtree(self.root, ignore_errors=True)


class ProjectManager:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.dir = services.config.projects_dir
        self.cache: dict[str, Project] = {}
        self.lock = threading.Lock()

    def list(self) -> list[dict]:
        out = []
        for meta_file in self.dir.glob("*/meta.json"):
            pid = meta_file.parent.name
            if pid in self.cache:
                out.append(self.cache[pid].public())
                continue
            meta = _read_json(meta_file, None)
            if not isinstance(meta, dict):
                continue
            out.append({
                "id": pid, "name": meta.get("name") or "未命名项目",
                "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
                "usage": meta.get("usage") or Usage().to_dict(), "cost": round(meta.get("cost", 0.0), 5),
                "running": False, "preview": {"status": "idle" if meta.get("preview") else "none"},
            })
        return sorted(out, key=lambda p: p.get("updated_at") or 0, reverse=True)

    def get(self, project_id: str) -> Project | None:
        if not PROJECT_ID.match(project_id or ""):
            return None
        with self.lock:
            project = self.cache.get(project_id)
            if project is not None:
                return project
            root = self.dir / project_id
            meta = _read_json(root / "meta.json", None)
            if not isinstance(meta, dict):
                return None
            meta["id"] = project_id
            project = self.cache[project_id] = Project(self.services, root, meta)
            return project

    def create(self, name: str = "", starter: str = "") -> Project:
        project_id = uuid.uuid4().hex[:10]
        root = self.dir / project_id
        starter_dir = None
        if starter:
            if not re.fullmatch(r"[\w-]+", starter) or not (self.services.config.starters_dir / starter).is_dir():
                raise ValueError(f"没有这个起步模板：{starter}")
            starter_dir = self.services.config.starters_dir / starter
        ws.init_workspace(root / "workspace", starter_dir)
        now = time.time()
        meta = {
            "id": project_id,
            "name": (name or "").strip()[:60] or "新项目",
            "auto_name": not (name or "").strip(),
            "created_at": now,
            "updated_at": now,
            "profile_id": "",
        }
        _write_json(root / "meta.json", meta)
        return self.get(project_id)  # type: ignore[return-value]

    def delete(self, project_id: str) -> bool:
        project = self.get(project_id)
        if project is None:
            return False
        with self.lock:
            self.cache.pop(project_id, None)
        project.delete()
        return True

    def loaded(self) -> list[Project]:
        return list(self.cache.values())
