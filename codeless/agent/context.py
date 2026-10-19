"""上下文管理：估算 token、超过阈值时把历史压缩成摘要。

压缩采用"整段摘要"：把全部历史换成一条摘要消息，再接上当前的用户要求。
不做"保留最近几轮原文"的半压缩——保留下来的 thinking 块是在完整历史下生成的，
接在摘要后面回放会被 Anthropic 判定为篡改。
"""

from __future__ import annotations

import json
import re

from ..llm import Provider, Usage
from .prompts import COMPACT_PROMPT, COMPACT_SYSTEM

CJK = re.compile(r"[⺀-鿿가-힯＀-￯]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    return int(cjk * 0.8 + (len(text) - cjk) / 3.6) + 1


def message_tokens(msg: dict) -> int:
    total = estimate_tokens(msg.get("content") or "") + 4
    for call in msg.get("tool_calls") or []:
        total += estimate_tokens(json.dumps(call.get("arguments") or {}, ensure_ascii=False)) + 10
    total += 1200 * len(msg.get("images") or [])
    return total


def estimate_messages(messages: list[dict]) -> int:
    return sum(message_tokens(m) for m in messages)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 3
    return f"{text[:head]}\n…（省略 {len(text) - limit} 字）…\n{text[-(limit - head):]}"


def render_transcript(messages: list[dict], budget_chars: int) -> str:
    """把历史渲染成纯文本给摘要模型看。越新的内容保留得越完整，太旧的工具输出只留头尾。"""
    parts: list[str] = []
    used = 0
    for age, msg in enumerate(reversed(messages)):
        role = msg["role"]
        tool_cap = 4000 if age < 12 else 800 if age < 60 else 200
        if role == "user":
            block = f"### 用户\n{_clip(msg.get('content') or '', 6000)}"
        elif role == "assistant":
            lines = []
            if msg.get("content"):
                lines.append(_clip(msg["content"], 3000))
            for call in msg.get("tool_calls") or []:
                args = json.dumps(call.get("arguments") or {}, ensure_ascii=False)
                lines.append(f"[调用 {call['name']}] {_clip(args, 600 if age < 12 else 200)}")
            block = "### 助手\n" + "\n".join(lines)
        else:
            block = f"### 工具结果（{msg.get('name', '')}）\n{_clip(msg.get('content') or '', tool_cap)}"
        if used + len(block) > budget_chars:
            parts.append("（更早的记录过长，已省略）")
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(reversed(parts))


def summarize(provider: Provider, messages: list[dict], context_window: int, cancel) -> tuple[str, Usage]:
    budget = max(20_000, min(int(context_window * 1.5), 360_000))
    prompt = COMPACT_PROMPT.format(transcript=render_transcript(messages, budget))
    resp = provider.complete(
        COMPACT_SYSTEM, [{"role": "user", "content": prompt}], [], cancel=cancel, max_tokens=4096
    )
    return resp.text.strip(), resp.usage
