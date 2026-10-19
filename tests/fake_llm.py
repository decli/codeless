"""假的大模型服务：同时模拟 OpenAI Chat Completions 和 Anthropic Messages 的流式接口。

测试用剧本决定每次回什么，不需要真实的 API Key，也能把 agent 的完整流程（写代码、起服务、
自检、自动修复）跑一遍。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Reply:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    finish: str = ""  # 留空自动判断；可以设成 length 模拟截断
    delay: float = 0.0  # 第一个分片前等多久，用来测停止


@dataclass
class Turn:
    """从请求里提炼出来、剧本需要的信息。"""

    kind: str  # openai | anthropic
    user_text: str  # 最后一条真正的用户消息（不含工具结果）
    steps: int  # 这条用户消息之后已经有几条 assistant 消息
    tool_results: list[str]  # 最近一批工具结果
    body: dict
    has_tools: bool


Script = Callable[[Turn], Reply]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def parse_openai(body: dict) -> Turn:
    msgs = body.get("messages", [])
    last_user = 0
    for i, m in enumerate(msgs):
        if m["role"] == "user" and not _text_of(m["content"]).startswith("（上面工具结果附带的图片）"):
            last_user = i
    steps = sum(1 for m in msgs[last_user + 1:] if m["role"] == "assistant")
    results: list[str] = []
    for m in reversed(msgs):
        if m["role"] == "tool":
            results.insert(0, _text_of(m["content"]))
        elif m["role"] == "assistant":
            break
    return Turn("openai", _text_of(msgs[last_user]["content"]) if msgs else "", steps, results, body,
                bool(body.get("tools")))


def parse_anthropic(body: dict) -> Turn:
    msgs = body.get("messages", [])
    last_user = 0
    for i, m in enumerate(msgs):
        if m["role"] != "user":
            continue
        blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        if any(b.get("type") == "text" for b in blocks):
            last_user = i
    steps = sum(1 for m in msgs[last_user + 1:] if m["role"] == "assistant")
    results: list[str] = []
    if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
        for b in msgs[-1]["content"]:
            if b.get("type") == "tool_result":
                results.append(_text_of(b.get("content")))
    user = msgs[last_user] if msgs else {"content": ""}
    blocks = user["content"] if isinstance(user["content"], list) else [{"type": "text", "text": user["content"]}]
    text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return Turn("anthropic", text, steps, results, body, bool(body.get("tools")))


class FakeLLM:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.requests: list[dict] = []
        self.counter = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeLLM:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()

    def _next_id(self) -> int:
        self.counter += 1
        return self.counter

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append({"path": self.path, "body": body})
                if self.path.endswith("/chat/completions"):
                    reply = fake.script(parse_openai(body))
                    events = fake._openai_events(reply, body)
                elif self.path.endswith("/messages"):
                    reply = fake.script(parse_anthropic(body))
                    events = fake._anthropic_events(reply)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                if reply.delay:
                    import time

                    time.sleep(reply.delay)
                try:
                    for event in events:
                        self.wfile.write(event.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler

    # --- OpenAI ---

    def _openai_events(self, reply: Reply, body: dict) -> list[str]:
        def chunk(delta: dict, finish: str | None = None) -> str:
            data = {
                "id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0,
                "model": body.get("model", "fake"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        out = [chunk({"role": "assistant", "content": ""})]
        for piece in _pieces(reply.reasoning):
            out.append(chunk({"reasoning_content": piece}))
        for piece in _pieces(reply.text):
            out.append(chunk({"content": piece}))
        for index, (name, args) in enumerate(reply.tool_calls):
            raw = json.dumps(args, ensure_ascii=False)
            call_id = f"call_{self._next_id()}"
            out.append(chunk({"tool_calls": [{"index": index, "id": call_id, "type": "function",
                                              "function": {"name": name, "arguments": ""}}]}))
            for piece in _pieces(raw, 40):
                out.append(chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]}))
        finish = reply.finish or ("tool_calls" if reply.tool_calls else "stop")
        out.append(chunk({}, finish))
        usage = {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
                 "prompt_tokens_details": {"cached_tokens": 600}}
        out.append(f"data: {json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fake', 'choices': [], 'usage': usage})}\n\n")
        out.append("data: [DONE]\n\n")
        return out

    # --- Anthropic ---

    def _anthropic_events(self, reply: Reply) -> list[str]:
        def ev(name: str, data: dict) -> str:
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        out = [ev("message_start", {"type": "message_start", "message": {
            "id": f"msg_{self._next_id()}", "type": "message", "role": "assistant", "model": "fake",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 400, "output_tokens": 1, "cache_read_input_tokens": 500,
                      "cache_creation_input_tokens": 100}}})]
        index = 0
        if reply.reasoning:
            out.append(ev("content_block_start", {"type": "content_block_start", "index": index,
                                                  "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            for piece in _pieces(reply.reasoning):
                out.append(ev("content_block_delta", {"type": "content_block_delta", "index": index,
                                                      "delta": {"type": "thinking_delta", "thinking": piece}}))
            out.append(ev("content_block_delta", {"type": "content_block_delta", "index": index,
                                                  "delta": {"type": "signature_delta", "signature": f"sig-{index}"}}))
            out.append(ev("content_block_stop", {"type": "content_block_stop", "index": index}))
            index += 1
        if reply.text:
            out.append(ev("content_block_start", {"type": "content_block_start", "index": index,
                                                  "content_block": {"type": "text", "text": ""}}))
            for piece in _pieces(reply.text):
                out.append(ev("content_block_delta", {"type": "content_block_delta", "index": index,
                                                      "delta": {"type": "text_delta", "text": piece}}))
            out.append(ev("content_block_stop", {"type": "content_block_stop", "index": index}))
            index += 1
        for name, args in reply.tool_calls:
            out.append(ev("content_block_start", {"type": "content_block_start", "index": index,
                                                  "content_block": {"type": "tool_use", "id": f"toolu_{self._next_id()}",
                                                                    "name": name, "input": {}}}))
            for piece in _pieces(json.dumps(args, ensure_ascii=False), 40):
                out.append(ev("content_block_delta", {"type": "content_block_delta", "index": index,
                                                      "delta": {"type": "input_json_delta", "partial_json": piece}}))
            out.append(ev("content_block_stop", {"type": "content_block_stop", "index": index}))
            index += 1
        stop = reply.finish or ("tool_use" if reply.tool_calls else "end_turn")
        out.append(ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                        "usage": {"output_tokens": 60}}))
        out.append(ev("message_stop", {"type": "message_stop"}))
        return out


def _pieces(text: str, size: int = 12) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] if text else []
