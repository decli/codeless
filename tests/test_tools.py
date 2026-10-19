"""agent 工具：文件读写改、搜索、命令执行，以及对模型不规范输入的容错。"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from codeless.agent.tools import TOOL_SPECS, Toolbox, ToolHost, fuzzy_replace
from codeless.llm import ToolCall
from codeless.sandbox.local import LocalSandbox


class Host(ToolHost):
    def __init__(self, root: Path) -> None:
        self.workspace = root
        self.box = LocalSandbox("test", root, {})
        self.todos: list[dict] = []
        self.saved: list[str] = []

    def sandbox(self):
        return self.box

    def set_todos(self, todos):
        self.todos = todos

    def save_output(self, text):
        self.saved.append(text)
        return ".codeless/outputs/out.log"


@pytest.fixture
def box(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    host = Host(root)
    return host, Toolbox(host, threading.Event(), lambda _: None)


def call(toolbox: Toolbox, name: str, **args):
    return toolbox.run(ToolCall(id="c1", name=name, arguments=args))


def test_specs_are_valid_json_schema():
    names = [t.name for t in TOOL_SPECS]
    assert len(names) == len(set(names))
    for spec in TOOL_SPECS:
        assert spec.parameters["type"] == "object"
        assert set(spec.parameters["required"]) <= set(spec.parameters["properties"])
        assert spec.description


def test_write_then_read_with_line_numbers(box):
    host, tb = box
    r = call(tb, "write_file", path="src/app.py", content="print('a')\nprint('b')\n")
    assert r.ok and "创建" in r.content
    assert r.detail["created"] and r.detail["additions"] == 2
    r = call(tb, "read_file", path="src/app.py")
    assert "1→print('a')" in r.content and "2→print('b')" in r.content


def test_write_reports_python_syntax_error(box):
    _, tb = box
    r = call(tb, "write_file", path="bad.py", content="def f(:\n    pass\n")
    assert r.ok
    assert "语法检查未通过" in r.content


def test_edit_exact_and_ambiguous(box):
    host, tb = box
    (host.workspace / "a.js").write_text("let x = 1\nlet y = 1\n")
    r = call(tb, "edit_file", path="a.js", old_string="= 1", new_string="= 2")
    assert not r.ok and "出现了 2 次" in r.content
    r = call(tb, "edit_file", path="a.js", old_string="let x = 1", new_string="let x = 2")
    assert r.ok and r.detail["additions"] == 1 and r.detail["deletions"] == 1
    r = call(tb, "edit_file", path="a.js", old_string="= 1", new_string="= 3", replace_all=True)
    assert r.ok
    assert (host.workspace / "a.js").read_text() == "let x = 2\nlet y = 3\n"


def test_edit_missing_text_shows_closest_snippet(box):
    host, tb = box
    (host.workspace / "a.py").write_text("def hello():\n    return 'hi'\n")
    r = call(tb, "edit_file", path="a.py", old_string="def helo():\n    return 'hi'", new_string="x")
    assert not r.ok
    assert "最接近的片段" in r.content


def test_fuzzy_replace_fixes_indentation():
    content = "class A:\n    def f(self):\n        return 1\n"
    updated, note = fuzzy_replace(content, "def f(self):\n    return 1", "def f(self):\n    return 2")
    assert updated == "class A:\n    def f(self):\n        return 2\n"
    assert "缩进" in note


def test_paths_cannot_escape_workspace(box, tmp_path):
    host, tb = box
    (tmp_path / "secret.txt").write_text("top secret")
    r = call(tb, "read_file", path="../secret.txt")
    assert not r.ok and "越界" in r.content
    os.symlink(tmp_path / "secret.txt", host.workspace / "link.txt")
    r = call(tb, "read_file", path="link.txt")
    assert not r.ok and "越界" in r.content
    # 模型常写成容器里的绝对路径
    (host.workspace / "ok.txt").write_text("fine\n")
    assert call(tb, "read_file", path="/workspace/ok.txt").ok


def test_search_and_list(box):
    host, tb = box
    (host.workspace / "src").mkdir()
    (host.workspace / "src" / "main.py").write_text("import os\nTODO = 1\n")
    (host.workspace / "node_modules").mkdir()
    (host.workspace / "node_modules" / "x.js").write_text("TODO")
    r = call(tb, "search", pattern="TODO")
    assert "src/main.py:2: TODO = 1" in r.content
    assert "node_modules" not in r.content
    r = call(tb, "list_files")
    assert "src/" in r.content and "已跳过：node_modules" in r.content


def test_bash_runs_in_workspace(box):
    host, tb = box
    r = call(tb, "bash", command="pwd && echo hello")
    assert r.ok and "hello" in r.content and str(host.workspace) in r.content
    r = call(tb, "bash", command="exit 3")
    assert not r.ok and "退出码 3" in r.content


def test_bash_refuses_long_running_servers(box):
    _, tb = box
    for cmd in ("npm run dev", "python -m http.server 8000", "node server.js &"):
        r = call(tb, "bash", command=cmd)
        assert not r.ok and "start_process" in r.content


def test_bash_long_output_is_saved(box):
    host, tb = box
    r = call(tb, "bash", command="seq 1 20000")
    assert r.ok and "完整输出" in r.content
    assert host.saved and host.saved[0].endswith("20000")


def test_bash_can_be_cancelled(box):
    host, _ = box
    cancel = threading.Event()
    tb = Toolbox(host, cancel, lambda _: None)
    threading.Timer(0.5, cancel.set).start()
    r = call(tb, "bash", command="sleep 30")
    assert not r.ok
    assert r.detail["duration"] < 10


def test_bad_arguments_are_reported_to_model(box):
    _, tb = box
    assert "没有名为" in call(tb, "rm_rf").content
    assert "缺少必填参数" in call(tb, "write_file", path="x").content
    r = tb.run(ToolCall(id="c", name="bash", arguments=None, error="参数不是合法的 JSON"))
    assert not r.ok and "JSON" in r.content
    # 数字写成字符串也能用
    assert call(tb, "bash", command="echo ok", timeout="30").ok


def test_todo_write_normalizes_items(box):
    host, tb = box
    r = call(tb, "todo_write", todos=[
        {"content": "写页面", "status": "completed"},
        {"content": "  ", "status": "pending"},
        {"content": "测试", "status": "weird"},
    ])
    assert r.ok and "1/2" in r.content
    assert host.todos == [{"content": "写页面", "status": "completed"}, {"content": "测试", "status": "pending"}]


def test_local_sandbox_hides_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-not-leak")
    host = Host(tmp_path)
    tb = Toolbox(host, threading.Event(), lambda _: None)
    r = call(tb, "bash", command="env")
    assert r.ok and "should-not-leak" not in r.content and "PORT=" in r.content
