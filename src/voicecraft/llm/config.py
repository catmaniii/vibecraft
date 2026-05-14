"""LLM 配置加载（设计文档 §7.4）。

读取 `config/llm.yaml` + 环境变量 `ANTHROPIC_API_KEY`（secret 不进 git）。
提供 `LLMConfig.build_provider()` 工厂方法，返回 `LLMProvider` 实现。

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

from voicecraft.llm.errors import LLMError
from voicecraft.llm.provider import LLMProvider


class LLMConfig(BaseModel):
    """config/llm.yaml 的 pydantic 模型。

    API key 永远从环境变量读取，**不**在 yaml 里存放。
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="anthropic", description="provider 名称")
    model: str = Field(default="claude-sonnet-4-6", description="模型 id")
    fallback_provider: str | None = Field(default=None, description="备用 provider（MVP 不用）")
    fallback_model: str | None = Field(default=None)
    timeout_s: float = Field(default=3.0, ge=0.1, le=60.0)
    max_retries: int = Field(default=1, ge=0, le=5)
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_directives_per_call: int = Field(default=10, ge=1, le=50)
    use_prompt_cache: bool = Field(default=True, description="是否给静态段打 cache_control")
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

        `api_key` 参数优先；其次读环境变量 `ANTHROPIC_API_KEY`；
        最后交给 SDK 自己的默认查找逻辑（SDK 也会读 env，此处显式读是为了
        在 provider 构造时就报错，而不是等到第一次真实调用时才崩溃）。
        """
        if self.provider == "anthropic":
            return self._build_anthropic(api_key=api_key)
        raise LLMError(f"不支持的 provider: {self.provider!r}（当前仅支持 'anthropic'）")

    def _build_anthropic(self, api_key: str | None = None) -> LLMProvider:
        """构造 AnthropicProvider。"""
        # 延迟 import，避免 LLMConfig 在没有 anthropic SDK 时也无法 import
        from voicecraft.llm.anthropic_provider import AnthropicProvider

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # 注意：key=None 时 SDK 也会自己找 env，这里 None 就直接传过去
        return AnthropicProvider(
            api_key=resolved_key,
            model=self.model,
            max_tokens=self.max_tokens,
            use_prompt_cache=self.use_prompt_cache,
        )
