"""Anthropic Messages 协议。官方 Claude，以及提供 Anthropic 兼容入口的服务（DeepSeek、Kimi、智谱等）。

要点：
- assistant 消息的原始内容块（含 thinking 签名）原样保存、原样回放，历史只追加不改，
  否则缓存失效、thinking 校验也会失败。换了模型或换了服务就不回放 thinking，只回放正文和工具调用。
- 系统提示词和最后一个内容块打缓存标记。
"""

from __future__ import annotations

import re
import threading
from typing import Any

import anthropic

from .base import (
    Cancelled,
    ModelResponse,
    Provider,
    ProviderError,
    StreamSink,
    ToolCall,
    ToolSpec,
    Usage,
    describe_http_error,
)

# 这些模型在官方接口上支持服务端兜底：遇到安全分类器拒答时由服务端换模型重试
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# 只接受 budget_tokens 形式思考配置的老模型；4.6 及以后都用 adaptive
LEGACY_THINKING = re.compile(r"claude-(3|haiku-4-5|sonnet-4-5|opus-4-5|opus-4-1|opus-4-0|sonnet-4-0|sonnet-4$|opus-4$)")


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value or "") or "call"


def _image_block(img: dict) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]},
    }


def is_official(base_url: str) -> bool:
    return not base_url or "api.anthropic.com" in base_url


def to_anthropic_messages(
    messages: list[dict], *, model: str, base_url: str, vision: bool
) -> list[dict]:
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for msg in messages:
        role = msg["role"]
        if role == "user":
            blocks: list[dict] = []
            if vision:
                blocks += [_image_block(img) for img in msg.get("images") or []]
            text = msg.get("content") or ""
            if text or not blocks:
                blocks.append({"type": "text", "text": text or "（空消息）"})
            push("user", blocks)
        elif role == "assistant":
            native = msg.get("native") or {}
            if (
                native.get("provider") == "anthropic"
                and native.get("model") == model
                and native.get("base_url", "") == base_url
                and native.get("content")
            ):
                push("assistant", [dict(b) for b in native["content"]])
                continue
            blocks = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": _safe_id(call["id"]),
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            push("assistant", blocks or [{"type": "text", "text": "（无内容）"}])
        elif role == "tool":
            content: list[dict] = [{"type": "text", "text": msg.get("content") or "（无输出）"}]
            if vision:
                content += [_image_block(img) for img in msg.get("images") or []]
            block = {
                "type": "tool_result",
                "tool_use_id": _safe_id(msg["tool_call_id"]),
                "content": content,
            }
            if msg.get("is_error"):
                block["is_error"] = True
            push("user", [block])

    # 缓存标记打在最后一个可缓存块上（thinking 块不能打）
    if out:
        for block in reversed(out[-1]["content"]):
            if block.get("type") in ("text", "tool_result", "image"):
                block["cache_control"] = {"type": "ephemeral"}
                break
    return out


def _clean_block(block: Any) -> dict:
    data = block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)
    # 回放时只带 API 认识的字段，SDK 解析出来的辅助字段去掉
    allowed = {
        "text": {"type", "text"},
        "thinking": {"type", "thinking", "signature"},
        "redacted_thinking": {"type", "data"},
        "tool_use": {"type", "id", "name", "input"},
    }.get(data.get("type"))
    if allowed:
        data = {k: v for k, v in data.items() if k in allowed}
    return data


class AnthropicProvider(Provider):
    def __init__(self, profile) -> None:
        super().__init__(profile)
        self.client = anthropic.Anthropic(
            api_key=profile.api_key or "none",
            base_url=profile.base_url or None,
            timeout=600,
            max_retries=0,
        )
        self.use_fallbacks = is_official(profile.base_url) and profile.model in FALLBACK_MODELS

    def _thinking(self, max_tokens: int) -> dict | None:
        if not self.profile.thinking:
            return None
        if LEGACY_THINKING.search(self.profile.model):
            return {"type": "enabled", "budget_tokens": max(1024, min(8000, max_tokens // 2))}
        return {"type": "adaptive", "display": "summarized"}

    def _complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec],
        sink: StreamSink,
        cancel: threading.Event,
        max_tokens: int | None,
    ) -> ModelResponse:
        p = self.profile
        limit = max_tokens or p.max_output_tokens or 32000
        kwargs: dict[str, Any] = {
            "model": p.model,
            "max_tokens": limit,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": to_anthropic_messages(
                messages, model=p.model, base_url=p.base_url, vision=p.vision
            ),
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        thinking = self._thinking(limit)
        if thinking:
            kwargs["thinking"] = thinking
        if p.effort:
            kwargs["output_config"] = {"effort": p.effort}

        for _ in range(2):
            try:
                return self._stream(kwargs, sink, cancel)
            except anthropic.BadRequestError as exc:
                text = str(exc).lower()
                if self.use_fallbacks and ("fallback" in text or "beta" in text):
                    self.use_fallbacks = False
                    continue
                raise ProviderError(describe_http_error(400, _body(exc)), status=400) from exc
        raise ProviderError("请求参数与该服务不兼容")

    def _stream(self, kwargs: dict, sink: StreamSink, cancel: threading.Event) -> ModelResponse:
        resp = ModelResponse()
        tool_names: dict[int, str] = {}
        tool_chars: dict[int, int] = {}
        try:
            if self.use_fallbacks:
                manager = self.client.beta.messages.stream(
                    **kwargs, betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"}
                )
            else:
                manager = self.client.messages.stream(**kwargs)
            with manager as stream:
                for event in stream:
                    if cancel.is_set():
                        raise Cancelled()
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", "") == "tool_use":
                            tool_names[event.index] = block.name
                            tool_chars[event.index] = 0
                            sink.on_tool_call(event.index, block.name, 0)
                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            resp.text += delta.text
                            sink.on_text(delta.text)
                        elif dtype == "thinking_delta":
                            resp.reasoning += delta.thinking
                            sink.on_thinking(delta.thinking)
                        elif dtype == "input_json_delta":
                            tool_chars[event.index] = tool_chars.get(event.index, 0) + len(delta.partial_json)
                            sink.on_tool_call(
                                event.index, tool_names.get(event.index, ""), tool_chars[event.index]
                            )
                final = stream.get_final_message()
        except Cancelled:
            raise
        except anthropic.BadRequestError:
            raise
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            retryable = status in (408, 409, 429, 529) or status >= 500
            raise ProviderError(describe_http_error(status, _body(exc)), retryable=retryable, status=status) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise ProviderError(f"连不上模型服务：{exc}", retryable=True) from exc

        blocks = [_clean_block(b) for b in final.content]
        resp.text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        for b in blocks:
            if b.get("type") == "tool_use":
                args = b.get("input")
                resp.tool_calls.append(
                    ToolCall(
                        id=b["id"],
                        name=b["name"],
                        arguments=args if isinstance(args, dict) else None,
                        error=None if isinstance(args, dict) else "参数必须是 JSON 对象",
                    )
                )
        stop = final.stop_reason or "end_turn"
        if stop == "max_tokens":
            resp.stop_reason = "max_tokens"
            for call in resp.tool_calls:
                call.error = "输出长度达到上限，参数被截断了。请把内容拆成更小的几次写入。"
        elif stop == "refusal":
            resp.stop_reason = "refusal"
        elif resp.tool_calls:
            resp.stop_reason = "tool_use"
        u = final.usage
        cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        resp.usage = Usage(
            input_tokens=int(u.input_tokens or 0) + cache_read + cache_write,
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
            output_tokens=int(u.output_tokens or 0),
        )
        # 按请求的模型名记：兼容服务返回的模型名常带版本后缀；官方兜底换了模型时，
        # 原样回放也没问题，接口会自动丢弃当前模型读不了的 thinking 块
        resp.native = {
            "provider": "anthropic",
            "model": self.profile.model,
            "base_url": self.profile.base_url,
            "content": blocks,
        }
        return resp


def _body(exc: anthropic.APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err)
    return str(getattr(exc, "message", "") or exc)
