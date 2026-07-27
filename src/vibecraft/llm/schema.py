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


# =========================================================================
# Clarification (2026-05-24 用户): LLM 不确定时给玩家几个具体选项 + 取消
# =========================================================================


class ClarificationOption(BaseModel):
    """玩家可选的一个具体解释 + 选中后会 submit 的 directive 列表。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="选项短文本(玩家点选的按钮 label,≤20 字)")
    interpretation_zh: str = Field(description="该选项的中文复述,辅助说明")
    directives: list[Directive] = Field(
        default_factory=list,
        description="选中此项后将被 submit 的 directive 列表(可为空表示该选项不动作)",
    )


class ClarificationRequest(BaseModel):
    """LLM 解析不确定 → 给玩家 2-4 个具体选项让其选择 / 取消。

    跟 AmbiguousParse 区别:
    - AmbiguousParse: LLM 没法给具体 directive(模糊到不知道走哪条路)
    - ClarificationRequest: LLM 能列出几个候选 directive,但拿不准玩家本意
      → PWA 弹层显示 options,玩家点选 → submit 该 option 的 directives
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(description="给玩家看的提问(如 '你是指哪个农民?')")
    options: list[ClarificationOption] = Field(
        min_length=2,
        max_length=4,
        description="2-4 个候选选项,玩家从中选 1 个或 ❌ 取消",
    )
    source_text: str = Field(
        default="",
        description="玩家原话,用于显示 + 关联(Director 填充,LLM 可省略)",
    )


ParseOutcome = IntentParseResult | AmbiguousParse | ClarificationRequest | ParseError
