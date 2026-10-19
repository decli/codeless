"""端到端：真实启动编排服务（本地进程沙箱）+ 假模型，走完写代码、运行、自检、自动修复、回滚的完整流程。"""

from __future__ import annotations

import json
import time

import httpx
import pytest

import scenarios
from conftest import has_browser


def kinds(items: list[dict]) -> list[str]:
    return [i["kind"] for i in items]


def start(server, prompt: str) -> str:
    resp = server.http.post("/api/projects", json={"prompt": prompt, "profile_id": server.profile_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_cli_program_fixes_its_own_error(live, kind):
    server = live(scenarios.python_cli, kind)
    pid = start(server, "写一个统计脚本")
    info = server.wait_idle(pid)
    assert info["name"] == "写一个统计脚本"
    items = server.items(pid)
    tools = [i for i in items if i["kind"] == "tool"]
    assert [t["name"] for t in tools] == ["write_file", "bash", "edit_file", "bash"]
    assert tools[1]["status"] == "error" and "medain" in tools[1]["output"]
    assert tools[3]["status"] == "ok" and "中位数 3.5" in tools[3]["output"]
    end = items[-1]
    assert end["kind"] == "turn_end" and end["outcome"] == "done"
    assert end["checkpoint"]["files"][0]["path"] == "stats.py"
    assert end["usage"]["input_tokens"] > 0
    # 文件接口看到的是修好后的内容
    content = server.http.get(f"/api/projects/{pid}/file", params={"path": "stats.py"}).json()["content"]
    assert "statistics.median" in content
    # 模型看到的第一条用户消息带有项目上下文
    first = server.fake.requests[0]["body"]
    text = json.dumps(first, ensure_ascii=False)
    assert "<context>" in text and "项目目录目前是空的" in text


def test_undo_turn_restores_files(live):
    server = live(scenarios.python_cli)
    pid = start(server, "写一个统计脚本")
    server.wait_idle(pid)
    sha = server.items(pid)[-1]["checkpoint"]["sha"]
    resp = server.http.post(f"/api/projects/{pid}/restore", json={"sha": f"{sha}^"})
    assert resp.status_code == 200, resp.text
    files = [f["path"] for f in server.http.get(f"/api/projects/{pid}/files").json()["files"]]
    assert "stats.py" not in files
    log = server.http.get(f"/api/projects/{pid}/checkpoints").json()
    assert log[0]["message"].startswith("恢复到")
    detail = server.http.get(f"/api/projects/{pid}/checkpoints/{sha}").json()
    assert detail["files"][0]["path"] == "stats.py"
    # 不接受像选项一样的编号
    bad = server.http.get(f"/api/projects/{pid}/diff", params={"sha": "--output=/tmp/x", "path": "a"})
    assert bad.status_code == 400
    assert server.http.post(f"/api/projects/{pid}/restore", json={"sha": "--help"}).status_code == 400


def test_preview_autofix_with_http_check(live):
    server = live(scenarios.missing_asset)
    pid = start(server, "做一个问候页面")
    server.wait_idle(pid, timeout=120)
    items = server.items(pid)
    autofix = [i for i in items if i["kind"] == "user" and i.get("source") == "autofix"]
    assert autofix, kinds(items)
    assert "app.js" in json.dumps(autofix[0], ensure_ascii=False)
    notices = [i["text"] for i in items if i["kind"] == "notice"]
    assert any("自检通过" in n for n in notices), notices
    assert items[-1]["kind"] == "turn_end" and items[-1]["outcome"] == "done"
    state = server.http.get(f"/api/projects/{pid}/timeline").json()["state"]
    assert state["preview"]["status"] == "running"

    # 预览走子域名代理，HTML 里注入了错误上报脚本
    host = {"host": f"{pid}.localhost:{server.config.port}"}
    page = httpx.get(server.base + "/", headers=host, trust_env=False)
    assert page.status_code == 200 and "/__codeless__/inject.js" in page.text
    assert httpx.get(server.base + "/app.js", headers=host, trust_env=False).status_code == 200

    # 页面里的报错上报上来，用户可以一键交给 agent 修
    httpx.post(server.base + "/__codeless__/errors", headers=host, trust_env=False,
               json={"kind": "error", "message": "TypeError: x is undefined"})
    state = server.http.get(f"/api/projects/{pid}/timeline").json()["state"]
    assert state["preview_errors"][0]["message"] == "TypeError: x is undefined"


def test_user_can_steer_while_agent_works(live):
    server = live(scenarios.steer)
    pid = start(server, "跑个命令")
    deadline = time.time() + 10
    while not any(i["kind"] == "tool" for i in server.items(pid)):
        assert time.time() < deadline
        time.sleep(0.1)
    resp = server.http.post(f"/api/projects/{pid}/messages", json={"text": "顺便打印一下日期"})
    assert resp.json()["queued"] is True
    server.wait_idle(pid)
    items = server.items(pid)
    answers = [i["text"] for i in items if i["kind"] == "assistant"]
    assert answers[-1] == "收到插话：顺便打印一下日期"
    assert sum(1 for i in items if i["kind"] == "turn_end") == 1


def test_stop_ends_turn_quickly(live):
    server = live(scenarios.slow)
    pid = start(server, "想一想")
    time.sleep(1)
    started = time.time()
    server.http.post(f"/api/projects/{pid}/stop")
    server.wait_idle(pid, timeout=10)
    assert time.time() - started < 5
    assert server.items(pid)[-1]["outcome"] == "stopped"


def test_follow_up_turn_keeps_history(live):
    server = live(scenarios.chatty)
    pid = start(server, "第一句")
    server.wait_idle(pid)
    server.http.post(f"/api/projects/{pid}/messages", json={"text": "第二句"})
    time.sleep(0.3)
    server.wait_idle(pid)
    answers = [i["text"] for i in server.items(pid) if i["kind"] == "assistant"]
    assert answers == ["收到：第一句", "收到：第二句"]
    last = server.fake.requests[-1]["body"]["messages"]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user"]


def test_events_stream_snapshot_then_ops(live):
    server = live(scenarios.chatty)
    pid = start(server, "你好")
    server.wait_idle(pid)
    with server.http.stream("GET", f"/api/projects/{pid}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = resp.iter_lines()
        event = data = ""
        for line in lines:
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                break
    assert event == "snapshot"
    snap = json.loads(data)
    assert snap["seq"] > 0 and any(i["kind"] == "turn_end" for i in snap["items"])


def test_api_rejects_cross_site_requests(live):
    server = live(scenarios.chatty)
    port = server.config.port
    evil = server.http.post("/api/projects", json={"prompt": ""}, headers={"origin": "http://evil.example"})
    assert evil.status_code == 403
    # 预览页面（子域名）里的脚本也不能调接口
    preview = server.http.post("/api/projects", json={"prompt": ""}, headers={"origin": f"http://abcdef1234.localhost:{port}"})
    assert preview.status_code == 403
    # DNS 重绑定：Host 不是本机就拒绝
    rebinding = server.http.get("/api/projects", headers={"host": f"attacker.example:{port}"})
    assert rebinding.status_code == 403
    ok = server.http.post("/api/projects", json={"prompt": ""}, headers={"origin": f"http://127.0.0.1:{port}"})
    assert ok.status_code == 200


def test_settings_never_return_api_key(live):
    server = live(scenarios.chatty)
    settings = server.http.get("/api/settings").json()
    text = json.dumps(settings)
    assert "sk-test-0123456789" not in text
    assert settings["profiles"][0]["api_key_set"] is True
    tested = server.http.post("/api/settings/test", json={"id": server.profile_id}).json()
    assert tested["ok"] is True, tested


@pytest.mark.browser
def test_browser_check_catches_runtime_error(live):
    if not has_browser():
        pytest.skip("没有可用的无头浏览器")
    server = live(scenarios.counter, browser=True)
    pid = start(server, "做一个计数器网页")
    server.wait_idle(pid, timeout=180)
    items = server.items(pid)
    autofix = [i for i in items if i["kind"] == "user" and i.get("source") == "autofix"]
    assert autofix and "initCounter" in json.dumps(autofix[0], ensure_ascii=False)
    checks = [i for i in items if i["kind"] == "tool" and i["name"] == "check_preview"]
    assert checks[0]["auto"] and checks[0]["status"] == "error"
    assert checks[-1]["auto"] and checks[-1]["status"] == "ok"
    # agent 自己调用的检查会截图给界面看
    manual = [c for c in checks if not c.get("auto")]
    assert manual and manual[-1]["detail"]["screenshot"].startswith(f"/api/projects/{pid}/shots/")
    assert server.http.get(manual[-1]["detail"]["screenshot"]).headers["content-type"] == "image/jpeg"
    assert items[-1]["kind"] == "turn_end" and items[-1]["outcome"] == "done"
