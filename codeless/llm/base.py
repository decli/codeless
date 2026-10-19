"""模型适配层的公共类型。

agent 内部用一种与供应商无关的消息格式，各适配器负责转换：

    {"role": "user", "content": "...", "images": [{"media_type": "image/png", "data": "<b64>"}]}
    {"role": "assistant", "content": "...", "reasoning": "...",
     "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}],
     "native": {...}}            # 供应商原样回放用的数据，比如 Anthropic 的 thinking 签名
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "...", "is_error": False,
     "images": [...]}            # 工具也能回图，比如预览截图

历史只追加不修改：缓存命中和 Anthropic 的 thinking 校验都依赖前缀不变。
"""

from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    raw_arguments: str = ""
    error: str | None = None  # 参数解析失败时的说明，原样反馈给模型让它重试


@dataclass
class Usage:
    input_tokens: int = 0  # 输入总量，含缓存命中
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.cached_tokens += other.cached_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.output_tokens += other.output_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> Usage:
        data = data or {}
        return cls(**{k: int(data.get(k) or 0) for k in cls().to_dict()})


@dataclass
class ModelResponse:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"  # end | tool_use | max_tokens | refusal | cancelled
    usage: Usage = field(default_factory=Usage)
    native: dict | None = None

    def to_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.reasoning:
            msg["reasoning"] = self.reasoning
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments or {}}
                for c in self.tool_calls
            ]
        if self.native:
            msg["native"] = self.native
        return msg


class StreamSink(Protocol):
    def on_text(self, text: str) -> None: ...
    def on_thinking(self, text: str) -> None: ...
    def on_tool_call(self, index: int, name: str, chars: int) -> None: ...


class NullSink:
    def on_text(self, text: str) -> None:
        pass

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_call(self, index: int, name: str, chars: int) -> None:
        pass


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Cancelled(RuntimeError):
    pass


def parse_arguments(raw: str | dict | None) -> tuple[dict | None, str | None]:
    """解析工具参数。弱一些的模型常见几种毛病：双重编码、尾逗号、代码块包裹。"""
    if isinstance(raw, dict):
        return raw, None
    text = (raw or "").strip()
    if not text:
        return {}, None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):  # 双重编码
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if isinstance(value, dict):
            return value, None
        return None, f"参数必须是 JSON 对象，收到的是 {type(value).__name__}"
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"参数不是合法 JSON（{exc.msg}，第 {exc.pos} 个字符附近）。请重新调用并输出完整、合法的 JSON。"
    return None, "参数无法解析"


THINK_TAG = re.compile(r"<think>(.*?)(</think>|$)", re.S)


class ThinkTagSplitter:
    """有些模型（本地部署的 R1、QwQ 等）把思考过程用 <think> 标签写在正文里，流式拆出来。"""

    def __init__(self) -> None:
        self.inside = False
        self.buffer = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.buffer += chunk
        out: list[tuple[str, str]] = []
        while self.buffer:
            tag = "</think>" if self.inside else "<think>"
            pos = self.buffer.find(tag)
            if pos == -1:
                # 留一截尾巴，防止标签被切在两个 chunk 中间
                keep = 0
                for n in range(min(len(tag) - 1, len(self.buffer)), 0, -1):
                    if tag.startswith(self.buffer[-n:]):
                        keep = n
                        break
                emit = self.buffer[: len(self.buffer) - keep]
                if emit:
                    out.append(("thinking" if self.inside else "text", emit))
                self.buffer = self.buffer[len(self.buffer) - keep :]
                break
            if pos:
                out.append(("thinking" if self.inside else "text", self.buffer[:pos]))
            self.buffer = self.buffer[pos + len(tag) :]
            self.inside = not self.inside
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self.buffer:
            return []
        out = [("thinking" if self.inside else "text", self.buffer)]
        self.buffer = ""
        return out


class Provider(ABC):
    """一次 complete 就是一次流式调用。重试、取消在这里统一处理。"""

    max_attempts = 3

    def __init__(self, profile) -> None:
        self.profile = profile

    @abstractmethod
    def _complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        sink: StreamSink,
        cancel: threading.Event,
        max_tokens: int | None,
    ) -> ModelResponse: ...

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        sink: StreamSink | None = None,
        cancel: threading.Event | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        sink = sink or NullSink()
        cancel = cancel or threading.Event()
        guard = _StartedSink(sink)
        last: ProviderError | None = None
        for attempt in range(self.max_attempts):
            if cancel.is_set():
                raise Cancelled()
            try:
                return self._call(system, messages, tools, guard, cancel, max_tokens)
            except ProviderError as exc:
                last = exc
                # 已经往外吐过内容就不重试，否则界面上会出现重复的半截回答
                if not exc.retryable or guard.started or attempt == self.max_attempts - 1:
                    raise
                if cancel.wait(min(2**attempt * 1.5, 10)):
                    raise Cancelled() from exc
        raise last or ProviderError("模型调用失败")

    def _call(self, system, messages, tools, guard: _StartedSink, cancel: threading.Event, max_tokens):
        """在单独的线程里请求，这样点停止能立刻返回：
        模型可能还在长时间思考、一个字都没吐，阻塞的网络读等不到下一个分片就没法检查取消标记。
        被放弃的请求线程会在收到下一个分片时发现已取消，自己退出，输出不再写到界面。"""
        box: dict[str, Any] = {}
        done = threading.Event()

        def work() -> None:
            try:
                box["value"] = self._complete(system, messages, tools, guard, cancel, max_tokens)
            except BaseException as exc:  # 原样交给调用方线程
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name="llm-call").start()
        while not done.wait(0.1):
            if cancel.is_set():
                guard.closed = True
                raise Cancelled()
        if "error" in box:
            if cancel.is_set():
                raise Cancelled()
            raise box["error"]
        return box["value"]


class _StartedSink:
    def __init__(self, inner: StreamSink) -> None:
        self.inner = inner
        self.started = False
        self.closed = False

    def on_text(self, text: str) -> None:
        if self.closed:
            return
        self.started = True
        self.inner.on_text(text)

    def on_thinking(self, text: str) -> None:
        if self.closed:
            return
        self.started = True
        self.inner.on_thinking(text)

    def on_tool_call(self, index: int, name: str, chars: int) -> None:
        if self.closed:
            return
        self.started = True
        self.inner.on_tool_call(index, name, chars)


def describe_http_error(status: int | None, body: str) -> str:
    hints = {
        401: "API Key 无效或已过期",
        403: "没有权限访问这个模型",
        404: "接口地址或模型名不对",
        429: "请求太频繁或额度用完了",
    }
    hint = hints.get(status or 0, "")
    body = (body or "").strip().replace("\n", " ")[:300]
    parts = [p for p in (f"HTTP {status}" if status else "", hint, body) if p]
    return "，".join(parts)
