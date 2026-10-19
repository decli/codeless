"""模型适配层：同一套 agent 循环可以接任何 OpenAI 兼容服务或 Anthropic 协议服务。"""

from __future__ import annotations

from .base import (
    Cancelled,
    ModelResponse,
    Provider,
    ProviderError,
    StreamSink,
    ToolCall,
    ToolSpec,
    Usage,
)


def make_provider(profile) -> Provider:
    if profile.provider == "anthropic":
        from .anthropic_native import AnthropicProvider

        return AnthropicProvider(profile)
    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(profile)


__all__ = [
    "Cancelled",
    "ModelResponse",
    "Provider",
    "ProviderError",
    "StreamSink",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "make_provider",
]
