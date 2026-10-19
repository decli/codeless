"""测试公共夹具：假模型、临时数据目录、真实启动的编排服务。"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from codeless.config import Config
from codeless.settings import ModelProfile

from fake_llm import FakeLLM

ROOT = Path(__file__).resolve().parent.parent
BROWSER_PATH = os.environ.get("BROWSER_PATH") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else ""
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def has_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            kwargs = {"headless": True, "args": ["--no-sandbox"]}
            if BROWSER_PATH:
                kwargs["executable_path"] = BROWSER_PATH
            pw.chromium.launch(**kwargs).close()
        return True
    except Exception:
        return False


def make_profile(fake: FakeLLM, kind: str = "openai", **extra) -> ModelProfile:
    return ModelProfile(
        id="fake", name="假模型", provider=kind,
        base_url=fake.url + ("/v1" if kind == "openai" else ""),
        api_key="sk-test-0123456789", model="fake-model", **extra,
    )


def make_config(tmp_path: Path, *, browser: bool = False) -> Config:
    c = Config(data_dir=tmp_path / "data", sandbox_backend="local", port=free_port())
    c.browser_check = "on" if browser else "off"
    c.browser_path = BROWSER_PATH
    c.starters_dir = ROOT / "sandbox" / "starters"
    c.projects_dir.mkdir(parents=True, exist_ok=True)
    return c


class Live:
    """在后台线程里跑起来的编排服务，加一个指向它的 HTTP 客户端。"""

    def __init__(self, config: Config, fake: FakeLLM, kind: str) -> None:
        import uvicorn

        from codeless.server import build

        self.config = config
        self.fake = fake
        self.app, self.services = build(config)
        self.base = f"http://127.0.0.1:{config.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=config.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("服务没有启动起来")
            time.sleep(0.05)
        self.http = httpx.Client(base_url=self.base, trust_env=False, timeout=30)
        profile = make_profile(fake, kind)
        data = {k: v for k, v in profile.__dict__.items() if k != "id"}
        self.profile_id = self.http.post("/api/settings/profiles", json=data).json()["id"]

    def wait_idle(self, project_id: str, timeout: float = 90) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.http.get(f"/api/projects/{project_id}").json()
            if not info["running"]:
                return info
            time.sleep(0.2)
        raise TimeoutError("agent 一直没有结束")

    def items(self, project_id: str) -> list[dict]:
        return self.http.get(f"/api/projects/{project_id}/timeline").json()["items"]

    def close(self) -> None:
        self.http.close()
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Callable[..., Live]]:
    """live(script, kind="openai", browser=False) 起一套服务，测试结束自动关掉。"""
    opened: list[tuple[Live, FakeLLM]] = []

    def start(script, kind: str = "openai", browser: bool = False) -> Live:
        fake = FakeLLM(script).__enter__()
        server = Live(make_config(tmp_path, browser=browser), fake, kind)
        opened.append((server, fake))
        return server

    yield start
    for server, fake in opened:
        server.close()
        fake.__exit__(None, None, None)
