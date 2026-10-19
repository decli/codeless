"""时间线、检查点、上下文估算这些不依赖模型的基础部件。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from codeless import workspace as ws
from codeless.agent.context import estimate_tokens, render_transcript
from codeless.agent.timeline import Timeline


def test_timeline_ops_and_resume(tmp_path: Path):
    tl = Timeline(tmp_path / "timeline.json")
    seen: list[int] = []
    tl.listeners.add(lambda: seen.append(tl.seq))
    item = tl.add("assistant", text="", streaming=True)
    tl.append(item["id"], "text", "你好")
    tl.append(item["id"], "text", "，世界")
    tl.update(item["id"], streaming=False)
    tl.set_state(running=True, _private="x")
    assert tl.get(item["id"])["text"] == "你好，世界"
    assert seen == [1, 2, 3, 4, 5]
    ops = tl.ops_after(2)
    assert [op["op"] for op in ops] == ["append", "update", "state"]
    assert tl.ops_after(tl.seq) == []
    snap = tl.snapshot()
    assert snap["seq"] == 5 and "_private" not in snap["state"]


def test_timeline_reload_marks_interrupted(tmp_path: Path):
    tl = Timeline(tmp_path / "timeline.json")
    a = tl.add("assistant", text="半截", streaming=True)
    t = tl.add("tool", name="bash", status="running")
    tl.save(force=True)
    again = Timeline(tmp_path / "timeline.json")
    assert again.get(a["id"])["streaming"] is False
    assert again.get(t["id"])["status"] == "interrupted"
    assert again.seq == tl.seq
    # 重启后旧的增量没有了，前端要重拉快照
    assert again.ops_after(1) is None


def test_timeline_trims_huge_tool_output(tmp_path: Path):
    tl = Timeline(tmp_path / "timeline.json")
    t = tl.add("tool", name="bash", output="")
    for _ in range(400):
        tl.append(t["id"], "output", "x" * 100 + "\n")
    assert len(tl.get(t["id"])["output"]) < 25_000


def test_checkpoints_and_restore(tmp_path: Path):
    root = tmp_path / "ws"
    ws.init_workspace(root)
    assert (root / ".gitignore").read_text().count("node_modules/") == 1
    (root / "a.txt").write_text("one\n")
    first = ws.commit_all(root, "第一版")
    assert first and first["files"] == [
        {"path": "a.txt", "additions": 1, "deletions": 0, "status": "added", "binary": False}
    ]
    assert ws.commit_all(root, "没有改动") is None
    (root / "a.txt").write_text("two\n")
    (root / "b.txt").write_text("new\n")
    second = ws.commit_all(root, "第二版")
    assert {f["path"]: f["status"] for f in second["files"]} == {"a.txt": "modified", "b.txt": "added"}
    # 撤销第二版：回到它的父提交，作为一个新检查点
    undo = ws.restore(root, f"{second['sha']}^", "撤销第二版")
    assert (root / "a.txt").read_text() == "one\n" and not (root / "b.txt").exists()
    assert undo and undo["sha"] != first["sha"]
    log = ws.checkpoints(root)
    assert [c["message"] for c in log[:3]] == ["撤销第二版", "第二版", "第一版"]
    assert "+two" in ws.file_diff(root, second["sha"], "a.txt")


def test_walk_and_zip_skip_dependencies(tmp_path: Path):
    root = tmp_path / "ws"
    ws.init_workspace(root)
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print(1)\n")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x")
    paths = {e["path"] for e in ws.walk(root)}
    assert "src/main.py" in paths and not any(p.startswith("node_modules") for p in paths)
    names = zipfile.ZipFile(io.BytesIO(ws.zip_workspace(root))).namelist()
    assert any(n.endswith("src/main.py") for n in names)
    assert not any("node_modules" in n or "/.git/" in n for n in names)


def test_token_estimate_handles_chinese():
    assert estimate_tokens("") == 0
    assert 70 <= estimate_tokens("你" * 100) <= 90
    assert 20 <= estimate_tokens("hello world " * 8) <= 35


def test_transcript_keeps_recent_details():
    old_output = "旧输出" * 2000
    messages = [
        {"role": "user", "content": "做个网页"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "bash", "arguments": {"command": "ls"}}]},
        {"role": "tool", "name": "bash", "content": old_output},
    ] + [{"role": "user", "content": f"第 {i} 个要求"} for i in range(70)]
    text = render_transcript(messages, 200_000)
    assert "第 69 个要求" in text and "做个网页" in text
    assert "省略" in text and len(text) < len(old_output)
    assert "更早的记录过长" in render_transcript(messages, 300)
