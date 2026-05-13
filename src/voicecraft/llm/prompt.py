"""Prompt 拼装（设计文档 §7.3）。

4 段：
1. System prompt (静态, cached, ~3K tokens): 角色 + 任务 + 输出 schema + 别名表
2. Strategy Catalog (静态, cached, ~1K tokens): 全部剧本一览
3. Few-shot (静态, cached, ~1K tokens): 8-10 个典型话语 → directives
4. Dynamic context (每次新, ~500-1K tokens): 当前时间 / 剧本 / 摘要 / 最近 3 句

前 3 段拼一次缓存进 provider，第 4 段每次新生成。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from voicecraft.directives.types import StageKind
from voicecraft.strategy.aliases import AliasTable
from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import (
    LategameDoctrine,
    MidgameStance,
    OpeningBuild,
)

# =========================================================================
# 玩家话语执行时的 game-state 摘要（动态 prompt 用）
# =========================================================================


class ParseContext(BaseModel):
    """动态 context：每次 parse 都新生成。"""

    model_config = ConfigDict(extra="forbid")

    game_time: float = Field(description="游戏内秒")
    current_stage: StageKind
    active_strategies: dict[StageKind, str | None] = Field(
        default_factory=lambda: dict.fromkeys(StageKind),
    )
    minerals: int = 0
    gas: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    expansion_count: int = 1
    army_summary: dict[str, int] = Field(default_factory=dict)
    enemy_summary: dict[str, int] = Field(default_factory=dict)
    recent_events: list[str] = Field(
        default_factory=list,
        description="最近若干条事件文本（已 humanize）",
    )
    recent_commands: list[str] = Field(
        default_factory=list,
        description="玩家最近 3 句话",
    )
    standing_orders: list[str] = Field(
        default_factory=list,
        description="当前活跃的 standing order 文本摘要",
    )


# =========================================================================
# Prompt 拼装函数
# =========================================================================


def build_system_prompt(aliases: AliasTable) -> str:
    """第 1 段：System prompt。"""
    building_aliases = ", ".join(sorted(aliases.all_aliases("building")))
    unit_aliases = ", ".join(sorted(aliases.all_aliases("unit")))
    upgrade_aliases = ", ".join(sorted(aliases.all_aliases("upgrade")))

    return f"""你是 VoiceCraft 的语义解析器。你只做一件事：把玩家中文/英文混合的 SC2 神族指令翻译成结构化的 directive 数组。

规则：
1. 输出**必须**通过提供的 tool `emit_directives` 返回。**绝不直接 free-text 回复**。
2. 不发明剧本 id。仅可用 catalog 列出的剧本。
3. 不"近似猜测"半懂半不懂的指令；不确定就给低 confidence，让玩家二次确认。
4. 别名 normalize：玩家说 "VR" / "球塔" / "兵营"，你输出 canonical id。
5. 复合句拆成多个 directive（顺序保留）。
6. 不要下任何 SC2 API；不要评估剧本能不能赢。

别名表（仅供 normalize 用，不是任务清单）：
- 建筑别名：{building_aliases}
- 单位别名：{unit_aliases}
- 升级别名：{upgrade_aliases}

verb 消歧规则：
- 玩家说 "造 / build / 起一个" + 建筑名 → building 表
- 玩家说 "出 / train / 训练" + 单位名 → unit 表
- 玩家说 "研 / 研究 / 升 / research" + 升级名 → upgrade 表
- 当 "VR" 等同形别名出现，必须靠 verb 消歧
"""


def build_strategy_catalog(library: StrategyLibrary) -> str:
    """第 2 段：Strategy Catalog（剧本目录一览）。"""
    parts: list[str] = ["可用剧本目录（仅可用以下 id）：\n"]

    parts.append("### opening_build")
    for s in library.all_strategies():
        if not isinstance(s, OpeningBuild):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh} (aliases: {aliases})")

    parts.append("\n### midgame_stance")
    for s in library.all_strategies():
        if not isinstance(s, MidgameStance):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh} (aliases: {aliases})")

    parts.append("\n### lategame_doctrine")
    for s in library.all_strategies():
        if not isinstance(s, LategameDoctrine):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh} (aliases: {aliases})")

    return "\n".join(parts)


def build_few_shot() -> str:
    """第 3 段：Few-shot 8 例（覆盖四档粒度）。

    M0 阶段最简版；M4 通过测试集驱动迭代扩展。
    """
    return """以下是典型话语 → directives 示例（仅供学习模式，不要照搬 id 到不相关上下文）：

例 1：「切到双矿凤凰」
→ strategy_set: stage=midgame, strategy_id=iac_2base  (示意：若 catalog 里有 phoenix 版本则替换)

例 2：「下个 BG 出俩哨兵」
→ production_override: unit_type=Sentry, count=2

例 3：「先研闪烁」
→ tech_override: upgrade_id=Blink, priority=80

例 4：「守家」
→ engagement_constraint: stance=defend

例 5：「凤凰举不朽」
→ unit_claim: selector={unit_type:"Phoenix"}, task={primary_action:{verb:"lift_target", target:{kind:"unit_type", unit_type:"Immortal"}}}

例 6：「11 点盖水晶」
→ build_at: structure_type=Pylon, point=[11克坐标]   (M0：若给不出精确点，confidence 降低)

例 7：「那个叉子回来」
→ unit_release: selector={...}, return_to_role=IDLE

例 8：「切到双矿凤凰，然后凤凰好提对方农民」
→ [strategy_set, unit_claim(selector=phoenix, task=harass_workers)]
"""


def build_dynamic_context(ctx: ParseContext) -> str:
    """第 4 段：每次 parse 都新生成。"""
    mins = int(ctx.game_time // 60)
    secs = int(ctx.game_time % 60)
    time_str = f"{mins}:{secs:02d}"

    active = []
    for stage in StageKind:
        sid = ctx.active_strategies.get(stage)
        if sid is not None:
            active.append(f"{stage.value}={sid}")
    active_str = ", ".join(active) or "(无)"

    army = ", ".join(f"{k}:{v}" for k, v in sorted(ctx.army_summary.items())) or "(无)"
    enemy = ", ".join(f"{k}:{v}" for k, v in sorted(ctx.enemy_summary.items())) or "(未侦察)"

    recent_evt = "\n  - ".join(ctx.recent_events) or "(无)"
    recent_cmd = "\n  - ".join(ctx.recent_commands) or "(无)"
    standing = "\n  - ".join(ctx.standing_orders) or "(无)"

    return f"""当前游戏状态：
- 游戏时间：{time_str} (内秒 {ctx.game_time:.1f})
- 当前阶段：{ctx.current_stage.value}
- 活跃剧本：{active_str}
- 资源：晶矿 {ctx.minerals}, 瓦斯 {ctx.gas}, 人口 {ctx.supply_used}/{ctx.supply_cap}, 扩张 {ctx.expansion_count}
- 我方军队摘要：{army}
- 已知敌情：{enemy}
- 当前 standing orders：
  - {standing}
- 最近事件：
  - {recent_evt}
- 玩家最近指令：
  - {recent_cmd}
"""


# =========================================================================
# Tool schema：强制 LLM 走 tool_use 输出 IntentParseResult
# =========================================================================


def build_tool_schema() -> dict[str, Any]:
    """提供给 Anthropic tool_use 的 JSON Schema。

    LLM 必须调 `emit_directives` 工具一次（且只一次）来返回结果。
    """
    return {
        "name": "emit_directives",
        "description": "把解析结果作为结构化数据返回。仅可用此工具响应。",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["interpretation_zh", "confidence", "directives"],
            "properties": {
                "interpretation_zh": {
                    "type": "string",
                    "description": "你对玩家话语的中文复述（给玩家二次确认用）。",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "0-1 置信度。低于 0.6 玩家会被弹模态确认。",
                },
                "directives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "payload"],
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": (
                                    "DirectiveType enum value: strategy_set / "
                                    "production_override / tech_override / "
                                    "expansion_override / engagement_constraint / "
                                    "unit_claim / scout / move / build_at / unit_release / "
                                    "view_move / view_follow / view_zoom"
                                ),
                            },
                            "payload": {
                                "type": "object",
                                "description": "对应 type 的 payload。结构见 schema。",
                                "additionalProperties": True,
                            },
                            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                            "source_text": {"type": "string"},
                        },
                    },
                },
                "notes": {"type": "string"},
            },
        },
    }
