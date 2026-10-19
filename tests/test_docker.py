"""Docker 沙箱：隔离（Key 不进沙箱、看不到宿主数据、出网白名单）和在容器里跑预览的完整流程。"""

from __future__ import annotations

import shutil
import subprocess

import httpx
import pytest

import scenarios
from conftest import Live, make_config
from fake_llm import FakeLLM

pytestmark = pytest.mark.docker


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "image", "inspect", "codeless-sandbox:latest"], check=True,
                       capture_output=True, timeout=20)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


@pytest.fixture
def docker_live(tmp_path):
    if not docker_ready():
        pytest.skip("Docker 或沙箱镜像不可用（make sandbox-image）")
    opened = []

    def start(script):
        fake = FakeLLM(script).__enter__()
        config = make_config(tmp_path)
        config.sandbox_backend = "docker"
        server = Live(config, fake, "openai")
        opened.append((server, fake))
        return server

    yield start
    for server, fake in opened:
        for project in server.http.get("/api/projects").json():
            server.http.delete(f"/api/projects/{project['id']}")
        server.close()
        fake.__exit__(None, None, None)


def test_sandbox_is_isolated(docker_live):
    server = docker_live(scenarios.isolation)
    pid = server.http.post("/api/projects", json={"prompt": "探测", "profile_id": server.profile_id}).json()["id"]
    server.wait_idle(pid, timeout=180)
    tool = next(i for i in server.items(pid) if i["kind"] == "tool")
    out = tool["output"]
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    assert lines[0] == "0", out  # API Key 不在沙箱环境变量里
    assert "No such file" in lines[1], out  # 看不到编排服务的数据目录
    assert "example:200" not in out, out  # 白名单之外的网站访问不了
    assert "Python 3.12" in out and "v22" in out


def test_preview_runs_inside_container(docker_live):
    server = docker_live(scenarios.missing_asset)
    pid = server.http.post("/api/projects", json={"prompt": "问候页面", "profile_id": server.profile_id}).json()["id"]
    server.wait_idle(pid, timeout=240)
    items = server.items(pid)
    assert any(i["kind"] == "user" and i.get("source") == "autofix" for i in items)
    assert items[-1]["kind"] == "turn_end" and items[-1]["outcome"] == "done"
    page = httpx.get(server.base + "/app.js", headers={"host": f"{pid}.localhost:{server.config.port}"}, trust_env=False)
    assert page.status_code == 200 and "你好" in page.text
