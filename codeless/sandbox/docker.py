"""Docker 沙箱：每个项目一个长驻容器，工作区 bind mount 到 /workspace。

网络：所有沙箱接在一个 internal 网络上，本身不能出网。
- allowlist：经出网代理容器访问包管理源（默认）
- open：额外接到 bridge 网络，可以任意出网
- none：完全断网
编排服务也接在这个网络上，用来反向代理预览端口。
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

from ..config import Config
from .base import PID_MARK, Sandbox

NETWORK = "codeless-sandbox"
EGRESS = "codeless-egress"
EGRESS_PORT = 3128
CONTAINER_PREFIX = "codeless-p-"
PREVIEW_PORT = 3000

KILL_TREE = r"""
import os, signal, sys, time
root = int(sys.argv[1])
def tree():
    kids = {}
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            stat = open('/proc/%s/stat' % d).read()
            ppid = int(stat[stat.rfind(')') + 2:].split()[1])
        except Exception:
            continue
        kids.setdefault(ppid, []).append(int(d))
    out, stack = [], [root]
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out
first = tree()
for sig in (signal.SIGTERM, signal.SIGKILL):
    for p in reversed(sorted(set(first) | set(tree()))):
        try:
            os.kill(p, sig)
        except Exception:
            pass
    time.sleep(0.8)
"""


class DockerUnavailable(RuntimeError):
    pass


def docker(*args: str, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DockerUnavailable("找不到 docker 命令，请先安装 Docker") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerUnavailable(f"docker {args[0]} 超时") from exc
    if check and proc.returncode != 0:
        raise DockerUnavailable(f"docker {' '.join(args[:2])} 失败：{proc.stderr.strip()[:400]}")
    return proc


class DockerEnv:
    """所有沙箱共用的基础设施：网络、出网代理、宿主机路径映射。启动后只做一次。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.ready = False
        self.data_host_dir: Path | None = config.data_host_dir

    def ensure(self) -> None:
        with self.lock:
            if self.ready:
                return
            info = docker("info", "--format", "{{.ServerVersion}}", timeout=20)
            if info.returncode != 0:
                raise DockerUnavailable(
                    "Docker 没有运行。请先启动 Docker Desktop；"
                    "或者设置 SANDBOX_BACKEND=local 用本地进程模式（不隔离，仅限自用）"
                )
            if docker("image", "inspect", self.config.sandbox_image).returncode != 0:
                raise DockerUnavailable(
                    f"沙箱镜像 {self.config.sandbox_image} 不存在，请先执行 make sandbox-image"
                )
            self._ensure_network()
            if self.config.in_docker:
                self._connect_self()
                if self.data_host_dir is None:
                    self.data_host_dir = self._detect_host_dir()
            if self.config.sandbox_network == "allowlist":
                self._ensure_egress()
            self.ready = True

    def _ensure_network(self) -> None:
        if docker("network", "inspect", NETWORK).returncode != 0:
            docker("network", "create", "--internal", NETWORK, check=True)

    def _connect_self(self) -> None:
        me = socket.gethostname()
        proc = docker("network", "connect", NETWORK, me)
        if proc.returncode != 0 and "already exists" not in proc.stderr:
            raise DockerUnavailable(f"编排服务接入沙箱网络失败：{proc.stderr.strip()[:300]}")

    def _detect_host_dir(self) -> Path:
        """编排服务在容器里时，沙箱挂载要用宿主机路径：从自己容器的挂载信息里查。"""
        proc = docker("inspect", socket.gethostname(), "--format", "{{json .Mounts}}")
        target = str(self.config.data_dir)
        try:
            for mount in json.loads(proc.stdout or "[]"):
                if mount.get("Destination") == target:
                    return Path(mount["Source"])
        except json.JSONDecodeError:
            pass
        raise DockerUnavailable(
            f"找不到数据目录 {target} 在宿主机上的路径，请设置 CODELESS_DATA_HOST_DIR"
        )

    def _ensure_egress(self) -> None:
        source = (Path(__file__).parent / "egress.py").read_text(encoding="utf-8")
        allowed = ",".join(self.config.allowed_hosts)
        upstream = self.config.upstream_proxy
        digest = hashlib.sha256(
            f"{source}|{allowed}|{upstream}|{self.config.sandbox_image}".encode()
        ).hexdigest()[:16]
        current = docker("inspect", EGRESS, "--format", '{{index .Config.Labels "codeless.config"}}|{{.State.Running}}')
        if current.returncode == 0:
            label, _, running = current.stdout.strip().partition("|")
            if label == digest and running == "true":
                return
            docker("rm", "-f", EGRESS)
        docker(
            "run", "-d", "--name", EGRESS,
            "--restart", "unless-stopped",
            "--label", f"codeless.config={digest}",
            "--memory", "128m",
            "-e", f"ALLOWED_HOSTS={allowed}",
            "-e", f"UPSTREAM_PROXY={upstream}",
            "-e", f"LISTEN_PORT={EGRESS_PORT}",
            self.config.sandbox_image,
            "python3", "-u", "-c", source,
            check=True,
        )
        docker("network", "connect", NETWORK, EGRESS, check=True)

    def host_path(self, path: Path) -> Path:
        if self.data_host_dir is None:
            return path
        return self.data_host_dir / path.resolve().relative_to(self.config.data_dir.resolve())

    def sandbox_env(self) -> dict[str, str]:
        env = {
            "PORT": str(PREVIEW_PORT),
            "HOST": "0.0.0.0",
            "CI": "1",
            "NO_COLOR": "1",
            "BROWSER": "none",
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_ROOT_USER_ACTION": "ignore",
            "npm_config_update_notifier": "false",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
            "npm_config_prefer_offline": "true",
            "CODELESS_STARTERS": "/opt/codeless/starters",
        }
        if self.config.sandbox_network == "allowlist":
            proxy = f"http://{EGRESS}:{EGRESS_PORT}"
            env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, http_proxy=proxy, https_proxy=proxy,
                       NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
        if self.config.npm_registry:
            env["npm_config_registry"] = self.config.npm_registry
        if self.config.pip_index_url:
            env["PIP_INDEX_URL"] = self.config.pip_index_url
        if self.config.sandbox_ca_bundle:
            ca = "/etc/codeless/ca.crt"
            env.update(NODE_EXTRA_CA_CERTS=ca, PIP_CERT=ca, REQUESTS_CA_BUNDLE=ca, SSL_CERT_FILE=ca)
        return env


class DockerSandbox(Sandbox):
    kind = "docker"

    def __init__(self, project_id: str, workspace: Path, env: DockerEnv) -> None:
        super().__init__(project_id, workspace)
        self.env = env
        self.name = f"{CONTAINER_PREFIX}{project_id}"
        self._checked = 0.0
        self._ip: str | None = None
        self._adopted = False  # 编排服务重启后第一次用到已在运行的容器

    @property
    def preview_port(self) -> int:
        return PREVIEW_PORT

    def _state(self) -> str:
        proc = docker("inspect", self.name, "--format", "{{.State.Status}}")
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def is_running(self) -> bool:
        return self._state() == "running"

    def start(self) -> None:
        with self.lock:
            if time.time() - self._checked < 10:
                return
            self.env.ensure()
            state = self._state()
            if state == "running" and not self._adopted:
                # 上次编排服务退出时留下的后台进程已经管不到了，还占着端口，重启容器清掉
                docker("restart", "-t", "2", self.name, check=True)
                self._ip = None
            elif state == "running":
                pass
            elif state:
                docker("start", self.name, check=True)
                self._ip = None
            else:
                self._run()
            self._adopted = True
            self._checked = time.time()

    def _run(self) -> None:
        cfg = self.env.config
        self.workspace.mkdir(parents=True, exist_ok=True)
        args = [
            "run", "-d", "--init",
            "--name", self.name,
            "--hostname", f"p-{self.project_id}",
            "--label", f"codeless.project={self.project_id}",
            "--network", NETWORK,
            "--cpus", cfg.sandbox_cpus,
            "--memory", cfg.sandbox_memory,
            "--pids-limit", "1024",
            "--security-opt", "no-new-privileges",
            "-v", f"{self.env.host_path(self.workspace)}:/workspace",
            "-w", "/workspace",
        ]
        if cfg.sandbox_ca_bundle:
            args += ["-v", f"{cfg.sandbox_ca_bundle}:/etc/codeless/ca.crt:ro"]
        for key, value in self.env.sandbox_env().items():
            args += ["-e", f"{key}={value}"]
        args += [cfg.sandbox_image, "sleep", "infinity"]
        docker(*args, check=True)
        if cfg.sandbox_network == "open":
            docker("network", "connect", "bridge", self.name, check=True)
        self._ip = None

    def _popen(self, command: str, *, marker: bool) -> subprocess.Popen:
        script = f'echo "{PID_MARK}$$"; exec bash -c "$1"' if marker else 'exec bash -c "$1"'
        return subprocess.Popen(
            ["docker", "exec", "-w", "/workspace", self.name, "bash", "-c", script, "_", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def _kill_tree(self, popen: subprocess.Popen, pid: int | None) -> None:
        if pid:
            docker("exec", self.name, "python3", "-c", KILL_TREE, str(pid), timeout=20)
        if popen.poll() is None:
            popen.terminate()

    def endpoint(self, port: int) -> tuple[str, int]:
        if self.env.config.in_docker:
            return self.name, port
        if not self._ip:
            proc = docker(
                "inspect", self.name, "--format",
                f'{{{{(index .NetworkSettings.Networks "{NETWORK}").IPAddress}}}}',
            )
            self._ip = proc.stdout.strip() or None
        return self._ip or "127.0.0.1", port

    def destroy(self) -> None:
        self.kill_all()
        docker("rm", "-f", self.name)
        self._checked = 0.0

    def stop(self) -> None:
        """闲置回收：停容器但保留，下次用时 docker start，装过的依赖还在。"""
        self.kill_all()
        docker("stop", "-t", "3", self.name)
        self._checked = 0.0

    def describe(self) -> str:
        mode = {"allowlist": "仅能访问包管理源", "open": "可访问外网", "none": "断网"}
        return f"Docker 容器（{mode.get(self.env.config.sandbox_network, '')}）"
