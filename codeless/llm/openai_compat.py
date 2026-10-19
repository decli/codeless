"""OpenAI 兼容协议（Chat Completions）。DeepSeek、通义、Kimi、智谱、豆包、OpenRouter、Ollama 都走这里。"""

from __future__ import annotations

import json
import threading
from typing import Any

import openai
from openai import OpenAI

from .base import (
    Cancelled,
    ModelResponse,
    Provider,
    ProviderError,
    StreamSink,
    ThinkTagSplitter,
    ToolCall,
    ToolSpec,
    Usage,
    describe_http_error,
    parse_arguments,
)


def _image_url(img: dict) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}}


def to_openai_messages(
    system: str, messages: list[dict], *, vision: bool, replay_reasoning: str
) -> list[dict]:
    """内部格式 → Chat Completions 格式。

    replay_reasoning：current = 只回传本轮（最后一条用户消息之后）的思考，always / never 同字面意思。
    DeepSeek、Kimi 的思考模式在工具调用循环里要求带上 reasoning_content，跨轮则不需要。
    """
    out: list[dict] = [{"role": "system", "content": system}]
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    pending_images: list[dict] = []

    def flush_images() -> None:
        if pending_images:
            parts: list[dict] = [{"type": "text", "text": "（上面工具结果附带的图片）"}]
            parts += [_image_url(img) for img in pending_images]
            out.append({"role": "user", "content": parts})
            pending_images.clear()

    for i, msg in enumerate(messages):
        role = msg["role"]
        if role != "tool":
            flush_images()
        if role == "user":
            images = msg.get("images") or []
            if images and vision:
                parts = [{"type": "text", "text": msg.get("content") or ""}]
                parts += [_image_url(img) for img in images]
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": "user", "content": msg.get("content") or ""})
        elif role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            calls = msg.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for c in calls
                ]
            reasoning = msg.get("reasoning") or ""
            if reasoning and (
                replay_reasoning == "always" or (replay_reasoning == "current" and i > last_user)
            ):
                item["reasoning_content"] = reasoning
            out.append(item)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg.get("content") or "",
                }
            )
            if vision and msg.get("images"):
                pending_images.extend(msg["images"])
    flush_images()
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def _int(obj: Any, name: str) -> int:
    value = getattr(obj, name, None)
    if value is None and isinstance(getattr(obj, "model_extra", None), dict):
        value = obj.model_extra.get(name)
    return int(value or 0)


def _usage(raw: Any) -> Usage:
    usage = Usage(input_tokens=_int(raw, "prompt_tokens"), output_tokens=_int(raw, "completion_tokens"))
    details = getattr(raw, "prompt_tokens_details", None)
    cached = _int(details, "cached_tokens") if details is not None else 0
    cached = cached or _int(raw, "prompt_cache_hit_tokens")  # DeepSeek 的字段名
    usage.cached_tokens = cached
    return usage


class OpenAICompatProvider(Provider):
    def __init__(self, profile) -> None:
        super().__init__(profile)
        self.client = OpenAI(
            api_key=profile.api_key or "none",
            base_url=profile.base_url or None,
            timeout=600,
            max_retries=0,  # 重试由 Provider.complete 统一做
        )
        self.replay_reasoning = "current"
        self.stream_usage = True

    def _request(
        self, system: str, messages: list[dict], tools: list[ToolSpec], max_tokens: int | None
    ) -> dict:
        p = self.profile
        kwargs: dict[str, Any] = {
            "model": p.model,
            "messages": to_openai_messages(
                system, messages, vision=p.vision, replay_reasoning=self.replay_reasoning
            ),
            "stream": True,
        }
        if self.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        limit = max_tokens or p.max_output_tokens
        if limit:
            # OpenAI 官方的新模型只认 max_completion_tokens，其余兼容服务基本只认 max_tokens
            key = "max_completion_tokens" if "api.openai.com" in (p.base_url or "") else "max_tokens"
            kwargs[key] = limit
        if p.effort:
            kwargs["reasoning_effort"] = p.effort
        return kwargs

    def _complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        sink: StreamSink,
        cancel: threading.Event,
        max_tokens: int | None,
    ) -> ModelResponse:
        for _ in range(3):
            try:
                return self._stream(system, messages, tools, sink, cancel, max_tokens)
            except openai.BadRequestError as exc:
                # 兼容服务对少数字段的支持不一致：报错里点名了就换个方式再请求一次
                text = str(exc).lower()
                if "stream_options" in text and self.stream_usage:
                    self.stream_usage = False
                    continue
                if "reasoning_content" in text:
                    if "missing" in text or "required" in text or "must" in text:
                        if self.replay_reasoning != "always":
                            self.replay_reasoning = "always"
                            continue
                    elif self.replay_reasoning != "never":
                        self.replay_reasoning = "never"
                        continue
                raise ProviderError(
                    describe_http_error(exc.status_code, _body(exc)), status=exc.status_code
                ) from exc
        raise ProviderError("请求参数与该服务不兼容")

    def _stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        sink: StreamSink,
        cancel: threading.Event,
        max_tokens: int | None,
    ) -> ModelResponse:
        resp = ModelResponse()
        calls: dict[int, dict] = {}
        splitter = ThinkTagSplitter()
        finish = ""

        def emit(kind: str, text: str) -> None:
            if kind == "thinking":
                resp.reasoning += text
                sink.on_thinking(text)
            else:
                resp.text += text
                sink.on_text(text)

        try:
            stream = self.client.chat.completions.create(**self._request(system, messages, tools, max_tokens))
            with stream:
                for chunk in stream:
                    if cancel.is_set():
                        raise Cancelled()
                    if getattr(chunk, "usage", None):
                        resp.usage = _usage(chunk.usage)
                    for choice in chunk.choices or []:
                        delta = choice.delta
                        if delta is None:
                            continue
                        reasoning = getattr(delta, "reasoning_content", None) or getattr(
                            delta, "reasoning", None
                        )
                        if isinstance(reasoning, str) and reasoning:
                            emit("thinking", reasoning)
                        if delta.content:
                            for kind, text in splitter.feed(delta.content):
                                emit(kind, text)
                        for tc in delta.tool_calls or []:
                            index = tc.index
                            if index is None:
                                # 个别服务不给 index：按 id 区分并行的多个调用
                                last = max(calls, default=None)
                                fresh = last is None or (tc.id and calls[last]["id"] and calls[last]["id"] != tc.id)
                                index = len(calls) if fresh else last
                            slot = calls.setdefault(index, {"id": "", "name": "", "args": ""})
                            if tc.id:
                                slot["id"] = tc.id
                            fn = tc.function
                            if fn is not None and fn.name:
                                # 正常只给一次全名；有的服务每个分片都重复全名，有的把名字切片
                                if not slot["name"]:
                                    slot["name"] = fn.name
                                elif fn.name != slot["name"] and not slot["name"].endswith(fn.name):
                                    slot["name"] += fn.name
                            if fn is not None and fn.arguments:
                                slot["args"] += fn.arguments
                            sink.on_tool_call(index, slot["name"], len(slot["args"]))
                        if choice.finish_reason:
                            finish = choice.finish_reason
        except Cancelled:
            raise
        except openai.BadRequestError:
            raise
        except openai.APIStatusError as exc:
            status = exc.status_code
            retryable = status in (408, 409, 429) or status >= 500
            raise ProviderError(describe_http_error(status, _body(exc)), retryable=retryable, status=status) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ProviderError(f"连不上模型服务：{exc}", retryable=True) from exc

        for kind, text in splitter.flush():
            emit(kind, text)

        for n, index in enumerate(sorted(calls)):
            slot = calls[index]
            args, error = parse_arguments(slot["args"])
            if error and finish == "length":
                error = "输出长度达到上限，参数被截断了。请把内容拆成更小的几次写入（先写骨架，再用 edit_file 补充）。"
            resp.tool_calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{n}",
                    name=slot["name"],
                    arguments=args,
                    raw_arguments=slot["args"],
                    error=error,
                )
            )
        if resp.tool_calls:
            resp.stop_reason = "tool_use"
        elif finish == "length":
            resp.stop_reason = "max_tokens"
        elif finish == "content_filter":
            resp.stop_reason = "refusal"
        if not resp.usage.input_tokens:
            # 服务没回用量就粗估，宁可高估也不要显示成 0
            sent = sum(len(str(m.get("content") or "")) for m in messages) + len(system)
            resp.usage = Usage(input_tokens=sent // 3, output_tokens=(len(resp.text) + len(resp.reasoning)) // 3)
        return resp


def _body(exc: openai.APIStatusError) -> str:
    try:
        body = exc.body
        if isinstance(body, dict):
            err = body.get("error", body)
            if isinstance(err, dict):
                return str(err.get("message") or err)
            return str(err)
        return str(body or exc.message)
    except Exception:
        return str(exc)
