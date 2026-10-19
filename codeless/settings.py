"""用户在界面里改的设置：模型档案和 agent 参数，存在 data/settings.json。

API Key 只存在这个文件里，不会进沙箱、不会写进项目、接口返回时打码。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class ModelProfile:
    id: str = ""
    name: str = ""
    provider: str = "openai"  # openai（OpenAI 兼容协议）| anthropic
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 128_000
    max_output_tokens: int = 0  # 0 表示用服务默认值（Anthropic 协议必须给，默认 32000）
    thinking: bool = False  # Anthropic 协议是否开启思考；OpenAI 兼容协议由模型自己决定
    vision: bool = False  # 能否看图：用户贴的截图、预览截图
    effort: str = ""  # 推理强度：low / medium / high，留空用默认
    price_input: float = 0.0  # 美元 / 百万 token，0 表示不计费只看用量
    price_cached: float = 0.0
    price_output: float = 0.0

    def cost(self, usage) -> float:
        fresh = max(usage.input_tokens - usage.cached_tokens - usage.cache_write_tokens, 0)
        write_rate = self.price_input * (1.25 if self.provider == "anthropic" else 1.0)
        total = (
            fresh * self.price_input
            + usage.cached_tokens * (self.price_cached or self.price_input)
            + usage.cache_write_tokens * write_rate
            + usage.output_tokens * self.price_output
        )
        return total / 1_000_000

    @property
    def priced(self) -> bool:
        return bool(self.price_input or self.price_output)

    def public(self) -> dict:
        data = asdict(self)
        key = data.pop("api_key")
        data["api_key_set"] = bool(key)
        data["api_key_hint"] = f"{key[:3]}…{key[-4:]}" if len(key) > 10 else ("已设置" if key else "")
        return data


@dataclass
class AgentSettings:
    max_steps: int = 80  # 单轮最多调用模型的次数
    autofix_rounds: int = 2  # 每轮结束后自检发现问题，最多自动再修几次
    auto_check: bool = True  # 每轮结束后自动检查预览
    turn_budget_usd: float = 0.0  # 单轮花费上限，0 表示不限（只在配置了价格时生效）
    command_timeout: int = 180


@dataclass
class Preset:
    key: str
    label: str
    provider: str
    base_url: str
    models: list[str]
    context_window: int = 128_000
    max_output_tokens: int = 0
    thinking: bool = False
    vision: bool = False
    docs: str = ""
    note: str = ""
    prices: dict = field(default_factory=dict)  # 默认模型的参考价（美元 / 百万 token）


# 模型名更新很快，这里只放常见的几个作为输入提示，以服务商文档为准
PRESETS: list[Preset] = [
    Preset("deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
           ["deepseek-flash", "deepseek-chat", "deepseek-reasoner"],
           max_output_tokens=8192, docs="https://api-docs.deepseek.com/zh-cn/"),
    Preset("qwen", "通义千问（阿里云百炼）", "openai",
           "https://dashscope.aliyuncs.com/compatible-mode/v1",
           ["qwen3-coder-plus", "qwen-plus", "qwen-max"],
           docs="https://help.aliyun.com/zh/model-studio/"),
    Preset("kimi", "Kimi（月之暗面）", "openai", "https://api.moonshot.cn/v1",
           ["kimi-k2-turbo-preview", "kimi-k2-0905-preview"],
           docs="https://platform.moonshot.cn/docs"),
    Preset("zhipu", "智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4",
           ["glm-4.6", "glm-4.5-air"], docs="https://docs.bigmodel.cn/"),
    Preset("doubao", "豆包（火山方舟）", "openai", "https://ark.cn-beijing.volces.com/api/v3",
           [], note="模型名填推理接入点 ID 或方舟文档里的模型名",
           docs="https://www.volcengine.com/docs/82379"),
    Preset("siliconflow", "硅基流动", "openai", "https://api.siliconflow.cn/v1",
           ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-Coder-480B-A35B-Instruct"],
           docs="https://docs.siliconflow.cn/"),
    Preset("anthropic", "Claude（Anthropic）", "anthropic", "https://api.anthropic.com",
           ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
           context_window=1_000_000, max_output_tokens=32000, thinking=True, vision=True,
           docs="https://docs.claude.com/",
           prices={"claude-opus-5-5": [4.0, 0.2, 20.0], "claude-sonnet-5-5": [2.0, 0.1, 10.0],
                   "claude-haiku-5-5": [0.1, 0.01, 0.5]}),
    Preset("openai", "OpenAI", "openai", "https://api.openai.com/v1",
           ["gpt-5", "gpt-5-mini"], vision=True, docs="https://platform.openai.com/docs"),
    Preset("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
           [], note="模型名形如 厂商/模型，见 openrouter.ai/models",
           docs="https://openrouter.ai/docs"),
    Preset("ollama", "Ollama（本地模型）", "openai", "http://host.docker.internal:11434/v1",
           ["qwen3-coder", "qwen2.5-coder:14b"], context_window=32_000,
           note="不在 Docker 里运行时把地址改成 http://localhost:11434/v1；需要支持工具调用的模型"),
    Preset("custom", "其他 OpenAI 兼容服务", "openai", "", []),
]


@dataclass
class AppSettings:
    profiles: list[ModelProfile] = field(default_factory=list)
    default_profile: str = ""
    agent: AgentSettings = field(default_factory=AgentSettings)

    def profile(self, profile_id: str | None) -> ModelProfile | None:
        for p in self.profiles:
            if p.id == (profile_id or self.default_profile):
                return p
        return self.profiles[0] if self.profiles and not profile_id else None

    def public(self) -> dict:
        return {
            "profiles": [p.public() for p in self.profiles],
            "default_profile": self.default_profile,
            "agent": asdict(self.agent),
        }


def _pick(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> AppSettings:
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return AppSettings(
                    profiles=[_pick(ModelProfile, p) for p in raw.get("profiles", [])],
                    default_profile=raw.get("default_profile", ""),
                    agent=_pick(AgentSettings, raw.get("agent", {})),
                )
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        settings = AppSettings()
        bootstrap = profile_from_env()
        if bootstrap:
            settings.profiles.append(bootstrap)
            settings.default_profile = bootstrap.id
        return settings

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [asdict(p) for p in self.data.profiles],
            "default_profile": self.data.default_profile,
            "agent": asdict(self.data.agent),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def upsert_profile(self, data: dict) -> ModelProfile:
        """data 里 api_key 为 None 表示保留原值，空字符串表示清空。"""
        with self.lock:
            existing = self.data.profile(data.get("id")) if data.get("id") else None
            merged = {**asdict(existing)} if existing else {}
            for key, value in data.items():
                if key == "api_key" and value is None:
                    continue
                merged[key] = value
            profile = _pick(ModelProfile, merged)
            profile.id = profile.id or new_id()
            profile.name = profile.name or profile.model or "未命名模型"
            profile.provider = "anthropic" if profile.provider == "anthropic" else "openai"
            profile.base_url = profile.base_url.strip().rstrip("/")
            profile.model = profile.model.strip()
            if existing:
                self.data.profiles[self.data.profiles.index(existing)] = profile
            else:
                self.data.profiles.append(profile)
            if not self.data.default_profile or not self.data.profile(self.data.default_profile):
                self.data.default_profile = profile.id
            self.save()
            return profile

    def delete_profile(self, profile_id: str) -> None:
        with self.lock:
            self.data.profiles = [p for p in self.data.profiles if p.id != profile_id]
            if self.data.default_profile == profile_id:
                self.data.default_profile = self.data.profiles[0].id if self.data.profiles else ""
            self.save()

    def set_default(self, profile_id: str) -> None:
        with self.lock:
            if any(p.id == profile_id for p in self.data.profiles):
                self.data.default_profile = profile_id
                self.save()

    def update_agent(self, data: dict) -> AgentSettings:
        with self.lock:
            merged = {**asdict(self.data.agent), **(data or {})}
            self.data.agent = _pick(AgentSettings, merged)
            self.save()
            return self.data.agent


def profile_from_env() -> ModelProfile | None:
    """兼容 POC 的环境变量：第一次启动时用它们生成一个默认模型档案。"""
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    base = os.environ.get("LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or ""
    model = os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or ""
    provider = (os.environ.get("LLM_PROVIDER") or "").lower()
    if not (key or base or model):
        return None
    if provider not in ("openai", "anthropic"):
        provider = "anthropic" if "anthropic" in base or model.startswith("claude") else "openai"
    preset = next((p for p in PRESETS if p.base_url and p.base_url == base.rstrip("/")), None)
    if not base and not model:
        preset = PRESETS[0]
    profile = ModelProfile(
        id=new_id(),
        name=(preset.label if preset else "默认模型"),
        provider=provider,
        base_url=(base or (preset.base_url if preset else "")).rstrip("/"),
        api_key=key,
        model=model or (preset.models[0] if preset and preset.models else ""),
    )
    if preset:
        profile.context_window = preset.context_window
        profile.max_output_tokens = preset.max_output_tokens
        profile.thinking = preset.thinking
        profile.vision = preset.vision
    return profile
