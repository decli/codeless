"""模型适配层：两种协议的流式解析、思考内容回放、取消与参数容错。"""

from __future__ import annotations

import threading
import time

import pytest

from codeless.agent.tools import TOOL_SPECS
from codeless.llm import Cancelled, ProviderError, make_provider
from codeless.llm.base import ThinkTagSplitter, parse_arguments
from codeless.llm.openai_compat import to_openai_messages

from conftest import make_profile
from fake_llm import FakeLLM, Reply, Turn


class Sink:
    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.calls: list[str] = []

    def on_text(self, text):
        self.text += text

    def on_thinking(self, text):
        self.thinking += text

    def on_tool_call(self, index, name, chars):
        self.calls.append(name)


def one_step(turn: Turn) -> Reply:
    if turn.steps == 0:
        return Reply(reasoning="先写文件", text="好的。", tool_calls=[("write_file", {"path": "a.txt", "content": "你好"})])
    return Reply(text=f"完成，工具结果：{turn.tool_results[0]}")


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_stream_text_reasoning_and_tool_calls(kind):
    with FakeLLM(one_step) as fake:
        provider = make_provider(make_profile(fake, kind, thinking=True))
        sink = Sink()
        resp = provider.complete("系统提示", [{"role": "user", "content": "写个文件"}], TOOL_SPECS, sink)
        assert resp.text == "好的。" and sink.text == "好的。"
        assert resp.reasoning == "先写文件" and sink.thinking == "先写文件"
        assert resp.stop_reason == "tool_use"
        assert [(c.name, c.arguments) for c in resp.tool_calls] == [("write_file", {"path": "a.txt", "content": "你好"})]
        assert sink.calls and sink.calls[0] == "write_file"
        assert resp.usage.input_tokens > 0 and resp.usage.cached_tokens > 0 and resp.usage.output_tokens > 0


def test_anthropic_thinking_signature_is_replayed_verbatim():
    with FakeLLM(one_step) as fake:
        provider = make_provider(make_profile(fake, "anthropic", thinking=True))
        history = [{"role": "user", "content": "写个文件"}]
        resp = provider.complete("s", history, TOOL_SPECS)
        assert resp.native and resp.native["content"][0]["type"] == "thinking"
        history.append(resp.to_message())
        call = resp.tool_calls[0]
        history.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "已创建 a.txt"})
        final = provider.complete("s", history, TOOL_SPECS)
        assert final.text == "完成，工具结果：已创建 a.txt"
        sent = fake.requests[-1]["body"]
        assert sent["thinking"]["type"] == "adaptive"
        blocks = sent["messages"][1]["content"]
        assert blocks[0] == {"type": "thinking", "thinking": "先写文件", "signature": "sig-0"}
        assert blocks[-1]["type"] == "tool_use" and blocks[-1]["id"] == call.id


def test_anthropic_native_blocks_not_replayed_to_other_model():
    with FakeLLM(one_step) as fake:
        provider = make_provider(make_profile(fake, "anthropic", thinking=True))
        resp = provider.complete("s", [{"role": "user", "content": "x"}], TOOL_SPECS)
        msg = resp.to_message()
        msg["native"]["model"] = "another-model"
        call = resp.tool_calls[0]
        provider.complete("s", [
            {"role": "user", "content": "x"}, msg,
            {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "ok"},
        ], TOOL_SPECS)
        blocks = fake.requests[-1]["body"]["messages"][1]["content"]
        assert [b["type"] for b in blocks] == ["text", "tool_use"]


def test_openai_reasoning_replayed_only_within_current_turn():
    history = [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "答", "reasoning": "旧思考"},
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": "", "reasoning": "新思考",
         "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "ls"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "a.txt"},
    ]
    out = to_openai_messages("s", history, vision=False, replay_reasoning="current")
    assert "reasoning_content" not in out[2]
    assert out[4]["reasoning_content"] == "新思考"
    assert out[4]["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def test_tool_images_follow_tool_results_for_openai():
    img = {"media_type": "image/png", "data": "AAAA"}
    history = [
        {"role": "user", "content": "看看"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "check_preview", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "check_preview", "content": "ok", "images": [img]},
    ]
    out = to_openai_messages("s", history, vision=True, replay_reasoning="current")
    assert out[-2]["role"] == "tool"
    assert out[-1]["role"] == "user" and out[-1]["content"][1]["type"] == "image_url"
    assert to_openai_messages("s", history, vision=False, replay_reasoning="current")[-1]["role"] == "tool"


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_stop_returns_immediately_while_model_is_silent(kind):
    with FakeLLM(lambda t: Reply(text="迟到的回答", delay=20)) as fake:
        provider = make_provider(make_profile(fake, kind))
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.time()
        with pytest.raises(Cancelled):
            provider.complete("s", [{"role": "user", "content": "x"}], [], Sink(), cancel)
        assert time.time() - started < 2


def test_connection_error_is_reported():
    profile = make_profile(FakeLLM(one_step), "openai")
    profile.base_url = "http://127.0.0.1:9/v1"
    provider = make_provider(profile)
    provider.max_attempts = 1
    with pytest.raises(ProviderError):
        provider.complete("s", [{"role": "user", "content": "x"}], [])


def test_parse_arguments_tolerates_common_mistakes():
    assert parse_arguments('{"a": 1}') == ({"a": 1}, None)
    assert parse_arguments('```json\n{"a": 1}\n```') == ({"a": 1}, None)
    assert parse_arguments('{"a": [1, 2,],}') == ({"a": [1, 2]}, None)
    assert parse_arguments('"{\\"a\\": 1}"') == ({"a": 1}, None)
    assert parse_arguments("") == ({}, None)
    value, error = parse_arguments('{"path": "a.py", "content": "unterminated')
    assert value is None and "合法 JSON" in error


def test_think_tags_split_across_chunks():
    splitter = ThinkTagSplitter()
    out = []
    for chunk in ["<thi", "nk>想一", "想</th", "ink>答案", "是 42"]:
        out += splitter.feed(chunk)
    out += splitter.flush()
    thinking = "".join(t for k, t in out if k == "thinking")
    text = "".join(t for k, t in out if k == "text")
    assert thinking == "想一想" and text == "答案是 42"
