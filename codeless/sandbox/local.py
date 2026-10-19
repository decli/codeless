"""本地进程模式：命令直接在本机跑，没有隔离。只用于开发调试或明确接受风险的自用场景。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
from pathlib import Path

from .base import PID_MARK, Sandbox

SECRET_HINTS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def _clean_env() -> dict[str, str]:
    """本地模式没有隔离，至少别把模型 Key 之类的凭证原样传给 agent 跑的命令。"""
    return {
        k: v for k, v in os.environ.items()
        if not any(h in k.upper() for h in SECRET_HINTS) and not k.upper().startswith(("LLM_", "DEEPSEEK_"))
    }


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalSandbox(Sandbox):
    kind = "local"

    def __init__(self, project_id: str, workspace: Path, env: dict[str, str]) -> None:
        super().__init__(project_id, workspace)
        self._port = free_port()
        self.base_env = {**_clean_env(), **env, "PORT": str(self._port), "HOST": "0.0.0.0"}

    @property
    def preview_port(self) -> int:
        return self._port

    def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def is_running(self) -> bool:
        return True

    def _popen(self, command: str, *, marker: bool) -> subprocess.Popen:
        script = f'echo "{PID_MARK}$$"; exec bash -c "$1"' if marker else 'exec bash -c "$1"'
        return subprocess.Popen(
            ["bash", "-c", script, "_", command],
            cwd=self.workspace,
            env=self.base_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def _kill_tree(self, popen: subprocess.Popen, pid: int | None) -> None:
        try:
            os.killpg(os.getpgid(popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            popen.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def endpoint(self, port: int) -> tuple[str, int]:
        return "127.0.0.1", port

    def port_open_inside(self, port: int) -> bool:
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def destroy(self) -> None:
        self.kill_all()

    def describe(self) -> str:
        return "本地进程（未隔离）"
