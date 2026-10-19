"""沙箱的公共部分：前台命令、后台进程、输出清洗。具体怎么隔离由 docker.py / local.py 决定。"""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")
PID_MARK = "__CODELESS_PID__="

OnOutput = Callable[[str], None]


def clean_terminal(text: str) -> str:
    """去掉颜色码，回车覆盖的进度条只保留最后一次刷新的内容。"""
    text = ANSI.sub("", text)
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            parts = [p for p in line.split("\r") if p.strip()]
            line = parts[-1] if parts else ""
        lines.append(line)
    return "\n".join(lines)


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.4)
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n…（中间省略 {omitted} 字符）…\n{text[-tail:]}"


@dataclass
class ExecResult:
    code: int
    output: str
    timed_out: bool = False
    cancelled: bool = False
    duration: float = 0.0


class _Pump:
    """从子进程读输出：分块读，不等换行，长时间无换行的进度也能实时看到。"""

    def __init__(self, popen: subprocess.Popen, on_chunk: Callable[[str], None]) -> None:
        self.popen = popen
        self.on_chunk = on_chunk
        self.pid: int | None = None
        self.pid_event = threading.Event()
        self._pending = ""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = self.popen.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 8192)
            except OSError:
                break
            if not data:
                break
            self._feed(decoder.decode(data))
        self._feed(decoder.decode(b"", final=True))
        if self._pending:
            self.on_chunk(self._pending)
            self._pending = ""
        self.pid_event.set()

    def _feed(self, text: str) -> None:
        if not text:
            return
        if not self.pid_event.is_set():
            # 第一行可能是沙箱里的进程号标记，取出来，不进输出
            self._pending += text
            if "\n" not in self._pending and len(self._pending) < 64:
                return
            first, sep, rest = self._pending.partition("\n")
            if first.startswith(PID_MARK):
                try:
                    self.pid = int(first[len(PID_MARK):].strip())
                except ValueError:
                    pass
                text = rest
            else:
                text = self._pending
            self._pending = ""
            self.pid_event.set()
        if text:
            self.on_chunk(text)


class ManagedProcess:
    """后台进程：开发服务器、API 服务等。日志留在内存里，界面和 agent 都能看。"""

    MAX_LOG = 400_000

    def __init__(self, name: str, command: str, port: int | None) -> None:
        self.name = name
        self.command = command
        self.port = port
        self.started_at = time.time()
        self.exit_code: int | None = None
        self.ready = False  # 端口已经能连上
        self.listeners: list[OnOutput] = []
        self._chunks: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self.popen: subprocess.Popen | None = None
        self.pump: _Pump | None = None

    @property
    def running(self) -> bool:
        return self.exit_code is None and self.popen is not None and self.popen.poll() is None

    def attach(self, popen: subprocess.Popen) -> None:
        self.popen = popen
        self.pump = _Pump(popen, self._on_chunk)
        threading.Thread(target=self._wait, daemon=True).start()

    def _wait(self) -> None:
        assert self.popen is not None
        code = self.popen.wait()
        if self.pump:
            self.pump.thread.join(timeout=2)
        self.exit_code = code
        self._on_chunk(f"\n[进程已退出，退出码 {code}]\n")

    def _on_chunk(self, text: str) -> None:
        text = clean_terminal(text)
        with self._lock:
            self._chunks.append(text)
            self._size += len(text)
            while self._size > self.MAX_LOG and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())
        for listener in list(self.listeners):
            try:
                listener(text)
            except Exception:
                pass

    def log(self) -> str:
        with self._lock:
            return "".join(self._chunks)

    def tail(self, lines: int = 80) -> str:
        return "\n".join(self.log().rstrip("\n").split("\n")[-lines:])

    def info(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "port": self.port,
            "running": self.running,
            "exit_code": self.exit_code,
            "ready": self.ready,
            "started_at": self.started_at,
        }


class Sandbox(ABC):
    """一个项目一个沙箱。工作区目录在宿主机上，沙箱里看到的是 /workspace（本地模式就是原路径）。"""

    kind = "base"

    def __init__(self, project_id: str, workspace: Path) -> None:
        self.project_id = project_id
        self.workspace = workspace
        self.processes: dict[str, ManagedProcess] = {}
        self.last_used = time.time()
        self.lock = threading.RLock()

    # --- 由具体实现提供 ---

    @property
    @abstractmethod
    def preview_port(self) -> int: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def _popen(self, command: str, *, marker: bool) -> subprocess.Popen: ...

    @abstractmethod
    def _kill_tree(self, popen: subprocess.Popen, pid: int | None) -> None: ...

    @abstractmethod
    def endpoint(self, port: int) -> tuple[str, int]:
        """编排服务访问沙箱里某个端口用的地址。"""

    @abstractmethod
    def destroy(self) -> None: ...

    def port_open_inside(self, port: int) -> bool:
        """沙箱内部能否连上这个端口：用来分辨"没起来"和"只监听了 127.0.0.1"。"""
        code = self.exec(
            "python3 -c \"import socket,sys; s=socket.socket(); s.settimeout(1); "
            f"sys.exit(s.connect_ex(('127.0.0.1', {port})))\"",
            timeout=10,
        ).code
        return code == 0

    def describe(self) -> str:
        return self.kind

    # --- 公共实现 ---

    def touch(self) -> None:
        self.last_used = time.time()

    def exec(
        self,
        command: str,
        timeout: int = 120,
        on_output: OnOutput | None = None,
        cancel: threading.Event | None = None,
    ) -> ExecResult:
        """前台执行，阻塞到结束、超时或被取消。输出边跑边回调。"""
        self.start()
        self.touch()
        chunks: list[str] = []

        def collect(text: str) -> None:
            chunks.append(text)
            if on_output:
                on_output(clean_terminal(text))

        started = time.time()
        popen = self._popen(command, marker=True)
        pump = _Pump(popen, collect)
        timed_out = cancelled = False
        while popen.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if time.time() - started > timeout:
                timed_out = True
                break
            time.sleep(0.05)
        if timed_out or cancelled:
            pump.pid_event.wait(timeout=2)
            self._kill_tree(popen, pump.pid)
            try:
                popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                popen.kill()
        pump.thread.join(timeout=3)
        code = popen.returncode if popen.returncode is not None else -1
        output = clean_terminal("".join(chunks))
        if timed_out:
            code = 124
            output += f"\n[命令超过 {timeout} 秒被终止。长期运行的服务请用 start_process 后台启动]"
        if cancelled:
            code = 130
            output += "\n[已被用户中断]"
        return ExecResult(code, output, timed_out, cancelled, time.time() - started)

    def spawn(self, name: str, command: str, port: int | None = None) -> ManagedProcess:
        """后台启动。同名进程先停掉再起，用于重启开发服务器。"""
        self.start()
        self.touch()
        with self.lock:
            self.kill(name)
            proc = ManagedProcess(name, command, port)
            proc.attach(self._popen(command, marker=True))
            self.processes[name] = proc
            return proc

    def kill(self, name: str) -> bool:
        with self.lock:
            proc = self.processes.get(name)
            if not proc or not proc.popen:
                return False
            if proc.running:
                if proc.pump:
                    proc.pump.pid_event.wait(timeout=2)
                self._kill_tree(proc.popen, proc.pump.pid if proc.pump else None)
                try:
                    proc.popen.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.popen.kill()
            return True

    def kill_all(self) -> None:
        for name in list(self.processes):
            self.kill(name)
