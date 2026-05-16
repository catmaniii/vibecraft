"""LLM 配置加载（设计文档 §7.4）。

读取 `config/llm.yaml` + API key 环境变量（secret 不进 git）。环境变量名按
provider 决定：anthropic → `ANTHROPIC_API_KEY`，deepseek → `DEEPSEEK_API_KEY`
（也可在 yaml 里用 `api_key_env` 显式指定）。
提供 `LLMConfig.build_provider()` 工厂方法，返回 `LLMProvider` 实现。

provider 可配置切换：anthropic 走官方 Claude，deepseek 走 DeepSeek 的
Anthropic 兼容端点（复用 AnthropicProvider + base_url，见 ADR 0005）。

用法::

    config = LLMConfig.from_yaml(Path("config/llm.yaml"))
    provider = config.build_provider()
    parser = IntentParser(provider, library)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vibecraft.llm.errors import LLMError
from vibecraft.llm.provider import LLMProvider

# provider → 默认 base_url（None = anthropic SDK 默认端点，即官方 Anthropic）
_PROVIDER_BASE_URL: dict[str, str | None] = {
    "anthropic": None,
    "deepseek": "https://api.deepseek.com/anthropic",
}

# provider → 默认 API key 环境变量名
_PROVIDER_API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class LLMConfig(BaseModel):
    """config/llm.yaml 的 pydantic 模型。

    API key 永远从环境变量读取，**不**在 yaml 里存放。
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="anthropic", description="provider 名称（anthropic / deepseek）")
    model: str = Field(default="claude-sonnet-4-6", description="模型 id")
    base_url: str | None = Field(
        default=None, description="API 端点；None 时按 provider 取默认（见 _PROVIDER_BASE_URL）"
    )
    api_key_env: str | None = Field(
        default=None, description="API key 环境变量名；None 时按 provider 取默认"
    )
    fallback_provider: str | None = Field(default=None, description="备用 provider（MVP 不用）")
    fallback_model: str | None = Field(default=None)
    timeout_s: float = Field(default=15.0, ge=0.1, le=60.0)
    max_retries: int = Field(default=1, ge=0, le=5)
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_directives_per_call: int = Field(default=10, ge=1, le=50)
    use_prompt_cache: bool = Field(default=True, description="是否给静态段打 cache_control")
    disable_thinking: bool = Field(
        default=False,
        description="是否禁用思考模式（DeepSeek v4 默认思考，不兼容 tool_choice 强制）",
    )
    max_tokens: int = Field(default=1024, ge=64, le=8192)

    # ------------------------------------------------------------------ #
    # 工厂方法                                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_yaml(cls, path: Path) -> LLMConfig:
        """从 YAML 文件加载配置。"""
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def from_yaml_or_defaults(cls, path: Path | None = None) -> LLMConfig:
        """从 YAML 加载；文件不存在时用全部默认值。"""
        if path is not None and path.exists():
            return cls.from_yaml(path)
        return cls()

    def build_provider(self, api_key: str | None = None) -> LLMProvider:
        """根据配置构造 `LLMProvider` 实现。

        `api_key` 参数优先；其次读 provider 对应的环境变量（anthropic →
        `ANTHROPIC_API_KEY`，deepseek → `DEEPSEEK_API_KEY`，或 yaml 的
        `api_key_env` 覆盖）；最后交给 SDK 自己的默认查找逻辑。此处显式读
        是为了在 provider 构造时就报错，而不是等到第一次真实调用时才崩溃。

        anthropic / deepseek 都走 AnthropicProvider（DeepSeek 提供 Anthropic
        兼容端点，靠 base_url 区分，见 ADR 0005）。
        """
        if self.provider in _PROVIDER_API_KEY_ENV:
            return self._build_anthropic_compatible(api_key=api_key)
        supported = " / ".join(repr(p) for p in _PROVIDER_API_KEY_ENV)
        raise LLMError(f"不支持的 provider: {self.provider!r}（当前支持 {supported}）")

    def _build_anthropic_compatible(self, api_key: str | None = None) -> LLMProvider:
        """构造 AnthropicProvider（anthropic 官方 / deepseek 兼容端点共用）。"""
        # 延迟 import，避免 LLMConfig 在没有 anthropic SDK 时也无法 import
        from vibecraft.llm.anthropic_provider import AnthropicProvider

        base_url = self.base_url if self.base_url is not None else _PROVIDER_BASE_URL[self.provider]
        key_env = self.api_key_env or _PROVIDER_API_KEY_ENV[self.provider]
        resolved_key = api_key or os.environ.get(key_env)
        # 注意：key=None 时 SDK 也会自己找 env，这里 None 就直接传过去
        return AnthropicProvider(
            api_key=resolved_key,
            model=self.model,
            max_tokens=self.max_tokens,
            use_prompt_cache=self.use_prompt_cache,
            base_url=base_url,
            provider_name=self.provider,
            disable_thinking=self.disable_thinking,
        )
