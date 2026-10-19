"""agent 能用的工具。文件操作在编排侧对工作区目录直接做（沙箱通过挂载看到同一份），命令在沙箱里跑。

工具出错不抛异常，而是把清楚的错误信息返回给模型，让它自己纠正。
"""

from __future__ import annotations

import ast
import difflib
import fnmatch
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..llm import ToolCall, ToolSpec
from ..sandbox import Sandbox, truncate_middle
from ..workspace import SKIP_DIRS, is_binary, safe_path, text_diff

MAX_READ_LINES = 600
MAX_READ_CHARS = 60_000
MAX_OUTPUT = 16_000
MAX_SEARCH_RESULTS = 120
MAX_LINE = 400


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "list_files",
        "列出项目目录结构，自动跳过 node_modules、.git、dist 等目录。",
        _obj({
            "path": {"type": "string", "description": "相对项目根目录的目录，默认 ."},
            "depth": {"type": "integer", "description": "展开层数，默认 3"},
        }, []),
    ),
    ToolSpec(
        "read_file",
        "读取文本文件，返回带行号的内容。大文件用 offset（起始行号，从 1 开始）和 limit（行数）分段读。改文件前先读。",
        _obj({
            "path": {"type": "string", "description": "相对项目根目录的文件路径"},
            "offset": {"type": "integer", "description": "从第几行开始，默认 1"},
            "limit": {"type": "integer", "description": f"最多读多少行，默认 {MAX_READ_LINES}"},
        }, ["path"]),
    ),
    ToolSpec(
        "write_file",
        "创建文件或整体覆盖文件，父目录自动创建。content 必须是完整的文件内容，不能用省略号或“其余不变”之类的占位。改已有文件的局部请用 edit_file。",
        _obj({
            "path": {"type": "string", "description": "相对项目根目录的文件路径"},
            "content": {"type": "string", "description": "完整的文件内容"},
        }, ["path", "content"]),
    ),
    ToolSpec(
        "edit_file",
        "精确替换文件里的一段文本。old_string 必须和文件内容逐字一致（包括缩进和换行）且在文件中唯一；不唯一时多带几行上下文，或者设 replace_all=true 替换全部。old_string 为空且文件不存在时创建新文件。",
        _obj({
            "path": {"type": "string", "description": "相对项目根目录的文件路径"},
            "old_string": {"type": "string", "description": "要被替换的原文"},
            "new_string": {"type": "string", "description": "替换后的新内容"},
            "replace_all": {"type": "boolean", "description": "是否替换所有出现，默认 false"},
        }, ["path", "old_string", "new_string"]),
    ),
    ToolSpec(
        "search",
        "在项目文件中按正则表达式搜索内容，返回 文件:行号: 内容。",
        _obj({
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "搜索的目录或文件，默认 ."},
            "glob": {"type": "string", "description": "只搜匹配的文件名，比如 *.tsx"},
            "ignore_case": {"type": "boolean", "description": "忽略大小写，默认 false"},
        }, ["pattern"]),
    ),
    ToolSpec(
        "bash",
        "在沙箱里执行 bash 命令，工作目录是项目根目录。用于安装依赖、运行脚本、跑测试、构建等会自己结束的命令。"
        "不要用它启动开发服务器之类的常驻进程，那要用 start_process。",
        _obj({
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 120，最大 600"},
        }, ["command"]),
    ),
    ToolSpec(
        "start_process",
        "在后台启动常驻进程（开发服务器、API 服务等），同名进程会先停掉再启动，可用于重启。"
        "name 为 preview（默认）的进程就是用户在界面右侧看到的预览，它必须监听 0.0.0.0 和环境变量 $PORT 指定的端口。"
        "会等待端口就绪，返回启动日志。",
        _obj({
            "command": {"type": "string", "description": "启动命令，比如 npm run dev 或 uvicorn main:app --host 0.0.0.0 --port $PORT --reload"},
            "name": {"type": "string", "description": "进程名，默认 preview"},
            "port": {"type": "integer", "description": "服务监听的端口；preview 默认取 $PORT"},
        }, ["command"]),
    ),
    ToolSpec(
        "process_logs",
        "查看后台进程最近的输出日志。",
        _obj({
            "name": {"type": "string", "description": "进程名，默认 preview"},
            "lines": {"type": "integer", "description": "最多返回多少行，默认 80"},
        }, []),
    ),
    ToolSpec(
        "stop_process",
        "停止一个后台进程。",
        _obj({"name": {"type": "string", "description": "进程名"}}, ["name"]),
    ),
    ToolSpec(
        "check_preview",
        "检查预览是否正常：请求页面和它引用的脚本、样式，用无头浏览器打开收集控制台报错，"
        "并汇总用户浏览器里上报的运行时错误；模型能看图时还会附上截图。改完界面或接口后用它确认效果。",
        _obj({"path": {"type": "string", "description": "要检查的页面路径，默认 /"}}, []),
    ),
    ToolSpec(
        "todo_write",
        "维护任务清单：3 步以上的任务先列计划，做的过程中随时更新。每次传入完整清单，同一时间只能有一项 in_progress。",
        _obj({
            "todos": {
                "type": "array",
                "items": _obj({
                    "content": {"type": "string", "description": "任务内容"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                }, ["content", "status"]),
            }
        }, ["todos"]),
    ),
]

TOOL_NAMES = {t.name for t in TOOL_SPECS}

# 常驻进程的典型写法：用 bash 跑会一直卡到超时，提示改用 start_process
LONG_RUNNING = re.compile(
    r"(^|[;&|]\s*)("
    r"(npm|pnpm|yarn)\s+(run\s+)?(dev|start|serve|preview)\b"
    r"|npx\s+(vite|next|serve|http-server)(\s|$)(?!.*\bbuild\b)"
    r"|vite(\s+--|\s*$)|next\s+dev|uvicorn\s|gunicorn\s|flask\s+run|streamlit\s+run|nodemon\s"
    r"|python3?\s+-m\s+http\.server|node\s+--watch|php\s+-S)"
)


@dataclass
class ToolResult:
    content: str
    ok: bool = True
    images: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


class ToolHost:
    """工具需要从外部拿到的能力，由会话层实现。"""

    workspace: Path
    vision: bool = False

    def sandbox(self) -> Sandbox:
        raise NotImplementedError

    def start_process(self, name: str, command: str, port: int | None) -> ToolResult:
        raise NotImplementedError

    def process_logs(self, name: str, lines: int) -> ToolResult:
        raise NotImplementedError

    def stop_process(self, name: str) -> ToolResult:
        raise NotImplementedError

    def check_preview(self, path: str) -> ToolResult:
        raise NotImplementedError

    def set_todos(self, todos: list[dict]) -> None:
        raise NotImplementedError

    def save_output(self, text: str) -> str:
        raise NotImplementedError

    @property
    def command_timeout(self) -> int:
        return 120


def _line_numbers(lines: list[str], start: int) -> str:
    width = len(str(start + len(lines)))
    out = []
    for n, line in enumerate(lines, start):
        if len(line) > 2000:
            line = line[:2000] + "…（本行过长已截断）"
        out.append(f"{str(n).rjust(width)}→{line}")
    return "\n".join(out)


def syntax_warning(rel: str, content: str) -> str:
    """写完立刻做一次最便宜的语法检查，早发现早修，省一轮运行。"""
    try:
        if rel.endswith(".py"):
            ast.parse(content, filename=rel)
        elif rel.endswith(".json") and not rel.endswith((".jsonc", "tsconfig.json")) and "tsconfig" not in rel:
            json.loads(content)
    except SyntaxError as exc:
        return f"\n⚠ 语法检查未通过：第 {exc.lineno} 行 {exc.msg}"
    except json.JSONDecodeError as exc:
        return f"\n⚠ JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}"
    return ""


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def fuzzy_replace(content: str, old: str, new: str) -> tuple[str | None, str]:
    """精确匹配失败时的兜底：先忽略行尾空白，再忽略缩进（并把新内容的缩进按差值修正）。"""
    lines = content.split("\n")
    old_lines = old.strip("\n").split("\n")
    n = len(old_lines)
    if not n or not any(line.strip() for line in old_lines):
        return None, ""
    for mode in ("rstrip", "strip"):
        norm = str.rstrip if mode == "rstrip" else str.strip
        target = [norm(line) for line in old_lines]
        hits = [i for i in range(len(lines) - n + 1) if [norm(x) for x in lines[i:i + n]] == target]
        if len(hits) != 1:
            continue
        i = hits[0]
        new_lines = new.strip("\n").split("\n")
        if mode == "strip":
            have, want = _indent(old_lines[0]), _indent(lines[i])
            fixed = []
            for line in new_lines:
                if line.startswith(have):
                    line = want + line[len(have):]
                fixed.append(line)
            new_lines = fixed
        note = "（按忽略行尾空白匹配）" if mode == "rstrip" else "（按忽略缩进匹配，已修正新内容的缩进）"
        return "\n".join(lines[:i] + new_lines + lines[i + n:]), note
    return None, ""


def closest_snippet(content: str, old: str) -> str:
    lines = content.split("\n")
    old_lines = old.strip("\n").split("\n")
    n = max(len(old_lines), 1)
    best, best_i = 0.0, -1
    probe = "\n".join(old_lines)
    for i in range(0, max(len(lines) - n + 1, 1)):
        ratio = difflib.SequenceMatcher(None, probe, "\n".join(lines[i:i + n])).quick_ratio()
        if ratio > best:
            best, best_i = ratio, i
    if best_i < 0 or best < 0.5:
        return ""
    snippet = _line_numbers(lines[best_i:best_i + n + 2], best_i + 1)
    return f"\n文件里最接近的片段（第 {best_i + 1} 行附近）：\n{snippet}"


class Toolbox:
    def __init__(self, host: ToolHost, cancel: threading.Event, on_output: Callable[[str], None]) -> None:
        self.host = host
        self.cancel = cancel
        self.on_output = on_output

    @property
    def root(self) -> Path:
        return self.host.workspace

    def run(self, call: ToolCall) -> ToolResult:
        if call.name not in TOOL_NAMES:
            return ToolResult(f"没有名为 {call.name} 的工具。可用工具：{', '.join(sorted(TOOL_NAMES))}", ok=False)
        if call.error or call.arguments is None:
            return ToolResult(call.error or "参数解析失败", ok=False)
        args = call.arguments
        try:
            return getattr(self, f"t_{call.name}")(**self._clean(call.name, args))
        except TypeError as exc:
            return ToolResult(f"参数不对：{exc}", ok=False)
        except (ValueError, FileNotFoundError, IsADirectoryError, PermissionError) as exc:
            return ToolResult(str(exc), ok=False)
        except Exception as exc:  # 工具内部的意外错误也反馈给模型
            return ToolResult(f"工具执行出错：{type(exc).__name__}: {exc}", ok=False)

    def _clean(self, name: str, args: dict) -> dict:
        spec = next(t for t in TOOL_SPECS if t.name == name)
        props = spec.parameters["properties"]
        missing = [k for k in spec.parameters["required"] if k not in args]
        if missing:
            raise TypeError(f"缺少必填参数 {', '.join(missing)}")
        out: dict[str, Any] = {}
        for key, value in args.items():
            if key not in props:
                continue
            kind = props[key].get("type")
            if kind == "integer" and not isinstance(value, int):
                try:
                    value = int(value)
                except (TypeError, ValueError) as exc:
                    raise TypeError(f"{key} 应为整数") from exc
            elif kind == "boolean" and not isinstance(value, bool):
                value = str(value).lower() in ("true", "1", "yes")
            elif kind == "string" and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            out[key] = value
        return out

    # --- 文件 ---

    def t_list_files(self, path: str = ".", depth: int = 3) -> ToolResult:
        base = safe_path(self.root, path)
        if not base.is_dir():
            return ToolResult(f"目录不存在：{path}", ok=False)
        depth = max(1, min(depth, 8))
        lines: list[str] = []
        skipped: set[str] = set()

        def visit(directory: Path, level: int) -> None:
            if len(lines) >= 400:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                if entry.name in SKIP_DIRS:
                    if entry.is_dir():
                        skipped.add(entry.name)
                    continue
                pad = "  " * level
                if entry.is_dir():
                    lines.append(f"{pad}{entry.name}/")
                    if level + 1 < depth:
                        visit(entry, level + 1)
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{pad}{entry.name}  ({_size(size)})")
                if len(lines) >= 400:
                    lines.append("…（条目太多，已截断，请指定子目录）")
                    return

        visit(base, 0)
        text = "\n".join(lines) or "（空目录）"
        if skipped:
            text += f"\n（已跳过：{', '.join(sorted(skipped))}）"
        return ToolResult(text)

    def t_read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> ToolResult:
        target = safe_path(self.root, path)
        if target.is_dir():
            return ToolResult(f"{path} 是目录，请用 list_files", ok=False)
        if not target.is_file():
            return ToolResult(f"文件不存在：{path}", ok=False)
        data = target.read_bytes()
        if is_binary(data):
            return ToolResult(f"{path} 是二进制文件（{_size(len(data))}），无法按文本读取")
        lines = data.decode("utf-8", errors="replace").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        total = len(lines)
        start = max(1, offset)
        count = max(1, min(limit, 2000))
        chunk = lines[start - 1:start - 1 + count]
        text = _line_numbers(chunk, start)
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n…（内容过长已截断，请用 offset/limit 缩小范围）"
        end = start + len(chunk) - 1
        if total == 0:
            text = "（空文件）"
        elif start > 1 or end < total:
            text += f"\n（共 {total} 行，本次显示第 {start}-{end} 行）"
        return ToolResult(text, detail={"path": path, "lines": total})

    def t_write_file(self, path: str, content: str) -> ToolResult:
        target = safe_path(self.root, path)
        if target.is_dir():
            return ToolResult(f"{path} 是目录", ok=False)
        old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        rel = target.relative_to(self.root.resolve()).as_posix()
        line_count = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        diff = text_diff(old or "", content, rel)
        adds, dels = _diff_stat(diff)
        verb = "覆盖" if old is not None else "创建"
        msg = f"已{verb} {rel}（{line_count} 行）" + syntax_warning(rel, content)
        return ToolResult(msg, detail={
            "path": rel, "created": old is None, "diff": _cap(diff), "additions": adds, "deletions": dels,
        })

    def t_edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> ToolResult:
        target = safe_path(self.root, path)
        rel = target.relative_to(self.root.resolve()).as_posix()
        if not target.exists():
            if old_string == "":
                return self.t_write_file(path, new_string)
            return ToolResult(f"文件不存在：{path}。新建文件请用 write_file", ok=False)
        if old_string == new_string:
            return ToolResult("old_string 和 new_string 相同，没有可改的", ok=False)
        if old_string == "":
            return ToolResult("old_string 不能为空（文件已存在），请给出要替换的原文", ok=False)
        content = target.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)
        note = ""
        if count == 0:
            updated, note = fuzzy_replace(content, old_string, new_string)
            if updated is None:
                return ToolResult(
                    f"在 {rel} 中没找到 old_string，文件可能已经变了。请先用 read_file 重新查看再改。"
                    + closest_snippet(content, old_string),
                    ok=False,
                )
        elif count > 1 and not replace_all:
            return ToolResult(
                f"old_string 在 {rel} 中出现了 {count} 次，不唯一。请多带几行上下文，或设 replace_all=true。",
                ok=False,
            )
        else:
            updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        target.write_text(updated, encoding="utf-8")
        diff = text_diff(content, updated, rel)
        adds, dels = _diff_stat(diff)
        times = f"，替换了 {count} 处" if replace_all and count > 1 else ""
        msg = f"已修改 {rel}{times}{note}" + syntax_warning(rel, updated)
        return ToolResult(msg, detail={"path": rel, "diff": _cap(diff), "additions": adds, "deletions": dels})

    def t_search(self, pattern: str, path: str = ".", glob: str = "", ignore_case: bool = False) -> ToolResult:
        base = safe_path(self.root, path)
        flags = re.I if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            regex = re.compile(re.escape(pattern), flags)
        files = [base] if base.is_file() else _iter_files(base)
        results: list[str] = []
        root = self.root.resolve()
        for file in files:
            if glob and not fnmatch.fnmatch(file.name, glob) and not fnmatch.fnmatch(file.relative_to(root).as_posix(), glob):
                continue
            try:
                data = file.read_bytes()
            except OSError:
                continue
            if len(data) > 2_000_000 or is_binary(data):
                continue
            for n, line in enumerate(data.decode("utf-8", errors="replace").split("\n"), 1):
                if regex.search(line):
                    shown = line.strip()
                    if len(shown) > MAX_LINE:
                        shown = shown[:MAX_LINE] + "…"
                    results.append(f"{file.relative_to(root).as_posix()}:{n}: {shown}")
                    if len(results) >= MAX_SEARCH_RESULTS:
                        break
            if len(results) >= MAX_SEARCH_RESULTS:
                results.append(f"…（结果超过 {MAX_SEARCH_RESULTS} 条，请缩小范围）")
                break
        return ToolResult("\n".join(results) or "没有匹配的结果", detail={"matches": len(results)})

    # --- 命令与进程 ---

    def t_bash(self, command: str, timeout: int = 120) -> ToolResult:
        command = command.strip()
        if not command:
            return ToolResult("命令为空", ok=False)
        if LONG_RUNNING.search(command) and not command.startswith("timeout "):
            return ToolResult(
                "这看起来是一个常驻服务（开发服务器、Web 服务等），用 bash 运行会一直卡住。"
                "请改用 start_process 启动，它会在后台运行并等端口就绪。"
                "如果只是想短暂试运行，可以在命令前加 timeout 10。",
                ok=False,
            )
        if re.search(r"(?<![&|>])&\s*$", command) or "nohup " in command:
            return ToolResult("不要用 & 或 nohup 把进程放到后台，请用 start_process 启动常驻进程。", ok=False)
        timeout = max(5, min(int(timeout or self.host.command_timeout), 600))
        result = self.host.sandbox().exec(command, timeout=timeout, on_output=self.on_output, cancel=self.cancel)
        output = result.output.strip()
        saved = ""
        if len(output) > MAX_OUTPUT:
            saved = self.host.save_output(output)
            output = truncate_middle(output, MAX_OUTPUT)
            output += f"\n（完整输出 {len(result.output)} 字符已保存到 {saved}，需要时用 read_file 查看）"
        header = f"[退出码 {result.code}，耗时 {result.duration:.1f} 秒]"
        return ToolResult(
            f"{header}\n{output}" if output else f"{header}（无输出）",
            ok=result.code == 0,
            detail={"exit_code": result.code, "duration": round(result.duration, 1)},
        )

    def t_start_process(self, command: str, name: str = "preview", port: int | None = None) -> ToolResult:
        name = (name or "preview").strip() or "preview"
        if not re.fullmatch(r"[\w.-]{1,40}", name):
            return ToolResult("进程名只能包含字母、数字、下划线、点和横线", ok=False)
        return self.host.start_process(name, command.strip(), port)

    def t_process_logs(self, name: str = "preview", lines: int = 80) -> ToolResult:
        return self.host.process_logs(name or "preview", max(10, min(lines, 400)))

    def t_stop_process(self, name: str) -> ToolResult:
        return self.host.stop_process(name)

    def t_check_preview(self, path: str = "/") -> ToolResult:
        path = path if path.startswith("/") else "/" + path
        return self.host.check_preview(path)

    def t_todo_write(self, todos: list) -> ToolResult:
        clean = []
        for item in todos if isinstance(todos, list) else []:
            if isinstance(item, dict) and str(item.get("content", "")).strip():
                status = item.get("status")
                clean.append({
                    "content": str(item["content"]).strip()[:200],
                    "status": status if status in ("pending", "in_progress", "completed") else "pending",
                })
        self.host.set_todos(clean)
        done = sum(1 for t in clean if t["status"] == "completed")
        return ToolResult(f"任务清单已更新（{done}/{len(clean)} 完成）。继续执行。")


def _iter_files(base: Path):
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                yield entry


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _diff_stat(diff: str) -> tuple[int, int]:
    adds = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return adds, dels


def _cap(diff: str, limit: int = 30_000) -> str:
    return diff if len(diff) <= limit else diff[:limit] + "\n…（diff 过长已截断）"
