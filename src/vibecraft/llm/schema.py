"""IntentParser 输出 schema。

对应设计文档 §7.2。

`ParseOutcome` 是三选一 union：
- `IntentParseResult`：解析成功 + confidence >= 阈值
- `AmbiguousParse`：confidence < 阈值 → 手机弹二次确认
- `ParseError`：超时 / JSON 非法 / schema 不匹配 / 未知 strategy
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from vibecraft.directives.models import Directive


class IntentParseResult(BaseModel):
    """LLM 解析成功的输出。"""

    model_config = ConfigDict(extra="forbid")

    interpretation_zh: str = Field(description="给玩家看的中文复述")
    confidence: float = Field(ge=0.0, le=1.0)
    directives: list[Directive] = Field(default_factory=list)
    notes: str | None = None


class ParseErrorKind(str, Enum):
    """解析失败的类型。"""

    TIMEOUT = "timeout"
    INVALID_JSON = "invalid_json"
    SCHEMA_MISMATCH = "schema_mismatch"
    UNKNOWN_STRATEGY = "unknown_strategy"
    PROVIDER_ERROR = "provider_error"
    DIRECTIVE_INVALID = "directive_invalid"


class ParseError(BaseModel):
    """解析失败 envelope。bot 状态不变。"""

    model_config = ConfigDict(extra="forbid")

    kind: ParseErrorKind
    message: str
    candidates: list[str] = Field(
        default_factory=list,
        description="可选的 fuzzy match 候选（用于 UNKNOWN_STRATEGY）",
    )


class AmbiguousParse(BaseModel):
    """confidence 低 → 手机弹模态二次确认。"""

    model_config = ConfigDict(extra="forbid")

    result: IntentParseResult
    interpretations: list[str] = Field(
        default_factory=list,
        description="LLM 给出的多种候选解释（首项 = result.interpretation_zh）",
    )


ParseOutcome = IntentParseResult | AmbiguousParse | ParseError
