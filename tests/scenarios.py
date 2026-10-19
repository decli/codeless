"""测试剧本：模拟模型在典型任务里的行为。"""

from __future__ import annotations

from fake_llm import Reply, Turn

# 故意留一个 bug：调用了不存在的 initCounter，页面一打开就抛 ReferenceError
BUGGY_PAGE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>计数器</title></head>
<body>
  <h1>计数器</h1>
  <button id="btn">加一</button>
  <span id="n">0</span>
  <script>
    function increment() {
      const el = document.getElementById('n')
      el.textContent = Number(el.textContent) + 1
    }
    document.getElementById('btn').onclick = increment
    initCounter();
  </script>
</body>
</html>
"""

SERVE = "python3 -m http.server $PORT --bind 0.0.0.0"


def counter(turn: Turn) -> Reply:
    """做一个计数器网页：写代码 → 起服务 → 结束；自检发现报错 → 自动修复 → 再检查 → 结束。"""
    if turn.user_text.startswith("[自动检查]"):
        if turn.steps == 0:
            return Reply(
                reasoning="自动检查说页面里 initCounter 未定义，去掉这个调用。",
                tool_calls=[("edit_file", {
                    "path": "index.html",
                    "old_string": "    initCounter();",
                    "new_string": "    document.getElementById('n').textContent = '0'",
                })],
            )
        if turn.steps == 1:
            return Reply(tool_calls=[("check_preview", {})])
        return Reply(text="修好了：去掉了对不存在的 initCounter 的调用，页面不再报错。")
    if turn.steps == 0:
        return Reply(
            reasoning="用户要一个计数器网页，静态页面就够了。",
            text="好的，做一个简单的计数器页面。",
            tool_calls=[("todo_write", {"todos": [
                {"content": "写计数器页面", "status": "in_progress"},
                {"content": "启动预览", "status": "pending"},
            ]})],
        )
    if turn.steps == 1:
        return Reply(tool_calls=[("write_file", {"path": "index.html", "content": BUGGY_PAGE})])
    if turn.steps == 2:
        return Reply(tool_calls=[("start_process", {"command": SERVE})])
    return Reply(text="计数器做好了，点按钮数字加一。")


def python_cli(turn: Turn) -> Reply:
    """命令行程序：写脚本 → 运行 → 第一次运行报错 → 修 → 再运行 → 结束。不涉及预览。"""
    if turn.steps == 0:
        return Reply(tool_calls=[("write_file", {
            "path": "stats.py",
            "content": "import statistics\n\nnums = [3, 1, 4, 1, 5, 9, 2, 6]\nprint('平均数', statistics.mean(nums))\nprint('中位数', statistics.medain(nums))\n",
        })])
    if turn.steps == 1:
        return Reply(tool_calls=[("bash", {"command": "python3 stats.py"})])
    if turn.steps == 2:
        return Reply(
            reasoning="拼写错了 medain。",
            tool_calls=[("edit_file", {"path": "stats.py", "old_string": "statistics.medain", "new_string": "statistics.median"})],
        )
    if turn.steps == 3:
        return Reply(tool_calls=[("bash", {"command": "python3 stats.py"})])
    return Reply(text="脚本可以运行了，平均数 3.875，中位数 3.5。")


def slow(turn: Turn) -> Reply:
    """一直在"思考"、迟迟不出字，用来测停止。"""
    return Reply(text="终于想好了", delay=30)


def chatty(turn: Turn) -> Reply:
    return Reply(text=f"收到：{turn.user_text.split('<context>')[0].strip()[:40]}")


STATIC_PAGE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>问候</title></head>
<body><h1 id="msg">加载中</h1><script src="app.js"></script></body>
</html>
"""


def missing_asset(turn: Turn) -> Reply:
    """页面引用的 app.js 忘了写：不用浏览器、只靠 HTTP 检查也能发现并自动补上。"""
    if turn.user_text.startswith("[自动检查]"):
        if turn.steps == 0:
            return Reply(tool_calls=[("write_file", {
                "path": "app.js", "content": "document.getElementById('msg').textContent = '你好'\n",
            })])
        return Reply(text="补上了 app.js。")
    if turn.steps == 0:
        return Reply(tool_calls=[("write_file", {"path": "index.html", "content": STATIC_PAGE})])
    if turn.steps == 1:
        return Reply(tool_calls=[("start_process", {"command": SERVE})])
    return Reply(text="页面做好了。")


def steer(turn: Turn) -> Reply:
    """第一步跑一个要几秒的命令，用户在这期间插话，下一步应该能看到插话内容。"""
    if "[用户插话]" in turn.user_text:
        return Reply(text="收到插话：" + turn.user_text.split("[用户插话]", 1)[1].strip()[:20])
    if turn.steps == 0:
        return Reply(tool_calls=[("bash", {"command": "sleep 2 && echo done"})])
    return Reply(text="没有收到插话")


PROBE = (
    "env | grep -c sk-test || true; "
    "ls /data 2>&1 | head -1; "
    "curl -s -m 8 -o /dev/null -w 'example:%{http_code}\\n' https://example.com || echo example:blocked; "
    "python3 --version; node --version"
)


def isolation(turn: Turn) -> Reply:
    """在沙箱里探测：环境变量里有没有 Key、能不能看到宿主数据目录、能不能随便访问外网。"""
    if turn.steps == 0:
        return Reply(tool_calls=[("bash", {"command": PROBE})])
    return Reply(text="探测完毕")
