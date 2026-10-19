"""沙箱管理：按项目懒创建，闲置回收。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ..config import Config
from .base import ExecResult, ManagedProcess, Sandbox, clean_terminal, truncate_middle
from .docker import DockerEnv, DockerSandbox, DockerUnavailable
from .local import LocalSandbox


class SandboxManager:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.sandboxes: dict[str, Sandbox] = {}
        self.lock = threading.Lock()
        self.docker_env = DockerEnv(config) if config.sandbox_backend == "docker" else None
        self.busy: set[str] = set()  # 正在跑 agent 的项目，不回收

    def get(self, project_id: str, workspace: Path) -> Sandbox:
        with self.lock:
            box = self.sandboxes.get(project_id)
            if box is None:
                if self.docker_env is not None:
                    box = DockerSandbox(project_id, workspace, self.docker_env)
                else:
                    env = {"CODELESS_STARTERS": str(self.config.starters_dir)}
                    if self.config.npm_registry:
                        env["npm_config_registry"] = self.config.npm_registry
                    if self.config.pip_index_url:
                        env["PIP_INDEX_URL"] = self.config.pip_index_url
                    box = LocalSandbox(project_id, workspace, env)
                self.sandboxes[project_id] = box
            return box

    def peek(self, project_id: str) -> Sandbox | None:
        return self.sandboxes.get(project_id)

    def destroy(self, project_id: str) -> None:
        with self.lock:
            box = self.sandboxes.pop(project_id, None)
        if box:
            box.destroy()

    def reap_idle(self) -> None:
        """闲置超时的容器停掉，省内存。开发服务器会跟着停，下次打开项目再拉起来。"""
        limit = self.config.sandbox_idle_minutes * 60
        if limit <= 0:
            return
        now = time.time()
        for pid, box in list(self.sandboxes.items()):
            if pid in self.busy or now - box.last_used < limit:
                continue
            if isinstance(box, DockerSandbox):
                box.stop()
            else:
                box.kill_all()
            # 进程记录清掉：之后有人打开预览时会按项目里记下的命令重新拉起来
            box.processes.clear()
            box.last_used = now

    def shutdown(self) -> None:
        for box in list(self.sandboxes.values()):
            try:
                box.kill_all()
            except Exception:
                pass

    def describe(self) -> str:
        if self.docker_env is not None:
            mode = {"allowlist": "仅能访问包管理源", "open": "可访问外网", "none": "断网"}
            return f"Docker 容器（{mode.get(self.config.sandbox_network, '')}）"
        return "本地进程（未隔离）"


__all__ = [
    "DockerUnavailable",
    "ExecResult",
    "ManagedProcess",
    "Sandbox",
    "SandboxManager",
    "clean_terminal",
    "truncate_middle",
]
