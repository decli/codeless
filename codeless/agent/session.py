"""agent 主循环：调模型 → 执行工具 → 结果喂回去，直到模型不再调工具；结束后自检，有问题自动再修。

一轮（turn）从一条用户消息开始。运行中用户可以随时插话（排队，在下一次工具调用后注入）或停止。
"""

from __future__ import annotations

import time
import traceback
from typing import TYPE_CHECKING

from ..llm import Cancelled, ProviderError, Usage, make_provider
from ..llm.base import ModelResponse
from ..sandbox import DockerUnavailable
from .context import estimate_messages, message_tokens, summarize
from .timeline import Timeline
from .tools import TOOL_SPECS, Toolbox

if TYPE_CHECKING:
    from ..projects import Project

FLUSH_SECONDS = 0.08


class TimelineSink:
    """把模型的流式输出攒成小块写进时间线，避免每个 token 一条推送。"""

    def __init__(self, timeline: Timeline) -> None:
        self.tl = timeline
        self.items: dict[str, str] = {}
        self.kind = ""
        self.buffer = ""
        self.flushed_at = 0.0
        self.tool_chars = 0
        self.tool_reported = 0

    def _write(self, kind: str, text: str) -> None:
        if kind != self.kind:
            self.flush()
            self.kind = kind
        self.buffer += text
        if time.time() - self.flushed_at > FLUSH_SECONDS or len(self.buffer) > 400:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        kind = self.kind
        item_id = self.items.get(kind)
        if item_id is None:
            other = "thinking" if kind == "assistant" else "assistant"
            if other in self.items:
                self.tl.update(self.items.pop(other), streaming=False)
            item = self.tl.add(kind, text="", streaming=True)
            item_id = self.items[kind] = item["id"]
            self.tl.set_state(activity={"kind": "thinking" if kind == "thinking" else "writing"})
        self.tl.append(item_id, "text", self.buffer)
        self.buffer = ""
        self.flushed_at = time.time()

    def on_text(self, text: str) -> None:
        self._write("assistant", text)

    def on_thinking(self, text: str) -> None:
        self._write("thinking", text)

    def on_tool_call(self, index: int, name: str, chars: int) -> None:
        self.flush()
        if chars - self.tool_reported >= 400 or self.tool_reported == 0:
            self.tool_reported = max(chars, 1)
            self.tl.set_state(activity={"kind": "tool_args", "name": name, "chars": chars})

    def finish(self, cancelled: bool = False) -> None:
        self.flush()
        for item_id in self.items.values():
            self.tl.update(item_id, streaming=False, **({"interrupted": True} if cancelled else {}))
        self.items.clear()
        self.kind = ""
        self.tool_reported = 0


def tool_title(name: str, args: dict | None) -> str:
    a = args or {}
    path = str(a.get("path") or "")
    return {
        "list_files": f"查看目录 {path or '.'}",
        "read_file": f"读取 {path}",
        "write_file": f"写入 {path}",
        "edit_file": f"修改 {path}",
        "search": f"搜索 {a.get('pattern', '')}",
        "bash": str(a.get("command", "")),
        "start_process": f"启动 {a.get('name') or 'preview'}：{a.get('command', '')}",
        "process_logs": f"查看日志 {a.get('name') or 'preview'}",
        "stop_process": f"停止 {a.get('name', '')}",
        "check_preview": f"检查预览 {a.get('path') or '/'}",
        "todo_write": "更新任务清单",
    }.get(name, name)


class AgentLoop:
    def __init__(self, project: Project) -> None:
        self.p = project
        self.tl = project.timeline

    # --- 入口 ---

    def run(self) -> None:
        p = self.p
        started = time.time()
        usage = Usage()
        steps = 0
        outcome = "done"
        try:
            profile = p.profile()
            if profile is None:
                raise ProviderError("还没有配置模型，请先在设置里添加一个模型")
            provider = make_provider(profile)
            p.provider = provider
            p.prepare()
            settings = p.services.settings.data.agent
            toolbox = Toolbox(p, p.cancel, self._tool_output)
            sink = TimelineSink(self.tl)
            autofix = 0
            nudges = 0
            while True:
                if p.cancel.is_set():
                    raise Cancelled()
                p.drain_queue()
                if steps >= settings.max_steps:
                    self.tl.add("notice", level="warn",
                                text=f"本轮已调用模型 {steps} 次，达到上限，先停下来。可以说「继续」接着做。")
                    outcome = "limit"
                    break
                if settings.turn_budget_usd and profile.priced and profile.cost(usage) >= settings.turn_budget_usd:
                    self.tl.add("notice", level="warn",
                                text=f"本轮花费已达上限 ${settings.turn_budget_usd:.2f}，先停下来。")
                    outcome = "limit"
                    break
                self._maybe_compact(provider, profile, usage)

                self.tl.set_state(activity={"kind": "waiting"})
                try:
                    resp = provider.complete(p.system, p.messages, TOOL_SPECS, sink, p.cancel)
                except Cancelled:
                    sink.finish(cancelled=True)
                    raise
                sink.finish()
                steps += 1
                usage.add(resp.usage)
                p.last_prompt_tokens = resp.usage.input_tokens
                p.last_prompt_count = len(p.messages)
                p.add_usage(resp.usage, profile)
                self.tl.set_state(turn_usage=self._usage_view(usage, profile, steps, started))

                if not resp.text and not resp.tool_calls:
                    if resp.stop_reason == "refusal":
                        self.tl.add("notice", level="warn", text="模型拒绝回答这个请求。可以换个说法或换一个模型试试。")
                        outcome = "refused"
                        break
                    resp.text = "（无输出）"
                p.messages.append(resp.to_message())
                p.save_messages()

                if resp.stop_reason == "refusal":
                    self.tl.add("notice", level="warn", text="模型拒绝继续这个请求。")
                    outcome = "refused"
                    break

                if resp.tool_calls:
                    self._run_tools(toolbox, resp)
                    continue

                if resp.stop_reason == "max_tokens" and nudges < 2:
                    nudges += 1
                    p.messages.append({"role": "user", "content": "你的回复因长度上限被截断了，请接着往下写；如果是在写大文件，请拆成几次写入。"})
                    continue

                if p.has_queue():
                    continue

                if settings.auto_check and autofix < settings.autofix_rounds:
                    report = p.auto_verify()
                    if report is not None and not report.ok:
                        autofix += 1
                        text = self._autofix_message(report, autofix, settings.autofix_rounds)
                        self.tl.add("user", text=text, source="autofix")
                        p.messages.append({"role": "user", "content": text})
                        p.save_messages()
                        continue
                    if report is not None and autofix:
                        self.tl.add("notice", level="success", text="自检通过：问题已修复，预览运行正常。")
                    elif report is not None:
                        self.tl.add("notice", level="success", text="自检通过：预览运行正常。")
                elif settings.auto_check and autofix >= settings.autofix_rounds:
                    report = p.auto_verify()
                    if report is not None and not report.ok:
                        self.tl.add("notice", level="warn",
                                    text="自动修复了 {} 次仍有问题，先停下来。问题：{}".format(
                                        autofix, "；".join(report.problems[:3])))
                break
        except Cancelled:
            outcome = "stopped"
            self.tl.add("notice", level="info", text="已停止。")
        except ProviderError as exc:
            outcome = "error"
            self.tl.add("notice", level="error", text=f"模型调用失败：{exc}")
        except DockerUnavailable as exc:
            outcome = "error"
            self.tl.add("notice", level="error", text=f"沙箱不可用：{exc}")
        except Exception as exc:
            outcome = "error"
            traceback.print_exc()
            self.tl.add("notice", level="error", text=f"出错了：{type(exc).__name__}: {exc}")
        finally:
            self._close_dangling_calls()
            p.save_messages()
            profile = p.profile()
            p.finish_turn(outcome, self._usage_view(usage, profile, steps, started))

    # --- 工具 ---

    def _tool_output(self, text: str) -> None:
        item_id = self.p.current_tool_item
        if item_id:
            self.tl.append(item_id, "output", text)

    def _run_tools(self, toolbox: Toolbox, resp: ModelResponse) -> None:
        p = self.p
        for n, call in enumerate(resp.tool_calls):
            if p.cancel.is_set():
                for rest in resp.tool_calls[n:]:
                    p.messages.append({"role": "tool", "tool_call_id": rest.id, "name": rest.name,
                                       "content": "（用户中断，没有执行）", "is_error": True})
                p.save_messages()
                raise Cancelled()
            if call.name == "todo_write":
                result = toolbox.run(call)
            else:
                item = self.tl.add(
                    "tool", name=call.name, title=tool_title(call.name, call.arguments),
                    status="running", output="", args=_public_args(call.name, call.arguments),
                )
                p.current_tool_item = item["id"]
                self.tl.set_state(activity={"kind": "tool", "name": call.name, "title": item["title"]})
                result = toolbox.run(call)
                p.current_tool_item = None
                patch: dict = {"status": "ok" if result.ok else "error", "detail": result.detail, "ended": time.time()}
                if call.name != "bash":
                    patch["output"] = result.content[:6000]
                self.tl.update(item["id"], **patch)
            msg = {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result.content}
            if not result.ok:
                msg["is_error"] = True
            if result.images and p.vision:
                msg["images"] = result.images
            p.messages.append(msg)
        p.save_messages()

    def _close_dangling_calls(self) -> None:
        """中断时最后一条 assistant 的工具调用可能没有结果，补上，保证历史合法。"""
        msgs = self.p.messages
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "assistant":
                answered = {m.get("tool_call_id") for m in msgs[i + 1:] if m["role"] == "tool"}
                for call in msgs[i].get("tool_calls") or []:
                    if call["id"] not in answered:
                        msgs.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                                     "content": "（被中断，没有执行完）", "is_error": True})
                break
            if msgs[i]["role"] == "user":
                break

    # --- 上下文 ---

    def _maybe_compact(self, provider, profile, usage: Usage) -> None:
        p = self.p
        window = max(profile.context_window, 8000)
        reserve = max(profile.max_output_tokens or 8000, 4000)
        threshold = int(window * 0.75) - reserve
        if p.last_prompt_tokens and p.last_prompt_count <= len(p.messages):
            estimate = p.last_prompt_tokens + sum(message_tokens(m) for m in p.messages[p.last_prompt_count:])
        else:
            estimate = estimate_messages(p.messages) + 3000
        if estimate < threshold or len(p.messages) < 4:
            return
        item = self.tl.add("notice", level="info", text="对话太长，正在压缩上下文…")
        self.tl.set_state(activity={"kind": "compacting"})
        summary, used = summarize(provider, p.messages, window, p.cancel)
        usage.add(used)
        p.add_usage(used, profile)
        request = p.last_user_request()
        content = (
            f"<之前的工作摘要>\n{summary}\n</之前的工作摘要>\n\n"
            "以上是之前工作的摘要（对话太长，早先的记录已压缩）。"
            + (f"\n\n当前这一轮用户的要求是：\n{request}\n\n请在此基础上继续完成。" if request else "")
        )
        p.messages = [{"role": "user", "content": content}]
        p.last_prompt_tokens = 0
        p.save_messages()
        self.tl.update(item["id"], text=f"对话太长，已把早先的记录压缩成摘要（约 {estimate // 1000}k → {message_tokens(p.messages[0]) // 1000 + 1}k token）。")

    # --- 其他 ---

    def _autofix_message(self, report, n: int, total: int) -> str:
        problems = "\n".join(f"- {x}" for x in report.problems[:12])
        return (
            f"[自动检查] 你结束后我检查了预览，发现以下问题（第 {n}/{total} 次自动修复）：\n{problems}\n\n"
            "请找到根因并修复，修完用 check_preview 确认没有问题再结束。"
        )

    @staticmethod
    def _usage_view(usage: Usage, profile, steps: int, started: float) -> dict:
        view = {**usage.to_dict(), "steps": steps, "seconds": round(time.time() - started, 1)}
        if profile is not None and profile.priced:
            view["cost"] = round(profile.cost(usage), 5)
        return view


def _public_args(name: str, args: dict | None) -> dict:
    """界面上展示的参数：大段内容不放进去（写文件的内容会在 diff 里看到）。"""
    if not args:
        return {}
    return {k: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…")
            for k, v in args.items() if k not in ("content", "old_string", "new_string")}
