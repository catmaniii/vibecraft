"""Prompt 拼装（设计文档 §7.3）。

4 段：
1. System prompt (静态, cached, ~3K tokens): 角色 + 任务 + 输出 schema + 别名表
2. Strategy Catalog (静态, cached, ~1K tokens): 全部剧本一览
3. Few-shot (静态, cached, ~1K tokens): 8-10 个典型话语 → directives
4. Dynamic context (每次新, ~500-1K tokens): 当前时间 / 剧本 / 摘要 / 最近 3 句

前 3 段拼一次缓存进 provider，第 4 段每次新生成。

2026-05-24 用户:rules + few_shot 从 `docs/llm_prompt/*.md` 读取(方案 B)。
改 prompt 文本只需编辑 md,不动 code。aliases / catalog 仍代码动态生成,
跟数据源(yaml)自动同步。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vibecraft.directives.scope import DEFAULT_MAX_VOICE_GROUPS
from vibecraft.directives.types import StageKind
from vibecraft.strategy.aliases import AliasTable
from vibecraft.strategy.library import StrategyLibrary
from vibecraft.strategy.models import (
    LategameDoctrine,
    MidgameStance,
    OpeningBuild,
    PersistentDoctrine,
)

# Prompt 模板路径(repo root/docs/llm_prompt/)
_PROMPT_DIR = Path(__file__).resolve().parents[3] / "docs" / "llm_prompt"
_RULES_PATH = _PROMPT_DIR / "rules.md"
_FEW_SHOT_PATH = _PROMPT_DIR / "few_shot.md"

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
    # 2026-05-28 用户:让 LLM 看到当前建筑/升级,正确解析"补一个 BF" /
    # "升级地面攻击"(已有 +1 时输出 +2 而非 +1)的 delta 语义。
    buildings_summary: dict[str, int] = Field(
        default_factory=dict,
        description="当前 ready 建筑 count(全大写 UnitTypeId.name)",
    )
    upgrades_done: list[str] = Field(
        default_factory=list,
        description="已完成升级名(全大写 UpgradeId.name);LLM 据此判定升级'下一级'",
    )
    recent_events: list[str] = Field(
        default_factory=list,
        description="最近若干条事件文本（已 humanize）",
    )
    recent_commands: list[str] = Field(
        default_factory=list,
        description="玩家最近 3 句话(原文)",
    )
    recent_outcomes: list[str] = Field(
        default_factory=list,
        description=(
            "B 局内 memory(2026-05-17):每条对应 recent_commands 同 index 的"
            "解析摘要(directive 摘要 / parse error / ambiguous)。让 LLM 下次"
            "parse 看到自己上次输出的什么(摘要,不是 JSON)"
        ),
    )
    standing_orders: list[str] = Field(
        default_factory=list,
        description="当前活跃的 standing order 文本摘要",
    )
    camera_point: tuple[float, float] | None = Field(
        default=None,
        description="说话那刻镜头中心(x,y)；LLM 把'这里/这边'解析为 target.kind=camera",
    )


# =========================================================================
# Prompt 拼装函数
# =========================================================================


def build_system_prompt(
    aliases: AliasTable | None = None,
    max_voice_groups: int = DEFAULT_MAX_VOICE_GROUPS,
) -> str:
    """第 1 段:System prompt(从 docs/llm_prompt/rules.md 读取)。

    2026-05-25 用户:rules 不再含 aliases 表(race-specific),aliases / catalog
    挪到 build_race_block 给后段 cache(切种族失效,但 rules 永久 cache 命中)。

    `aliases` 参数保留向后兼容,实际不再使用。

    2026-06-04:编队上限可配置。rules.md 用 `{max_voice_groups}` 占位符，
    这里填入配置值(默认 5)，让 LLM 看到的合法范围跟 schema 校验一致。
    """
    text = _RULES_PATH.read_text(encoding="utf-8")
    return text.replace("{max_voice_groups}", str(max_voice_groups))


def build_race_block(
    aliases: AliasTable,
    library: StrategyLibrary,
    my_race: str | None = None,
) -> str:
    """种族特定段(2026-05-25):种族声明 + alias 表 + strategy catalog。

    放在 system_prompt 之后、few_shot 之前,作为独立 cache block。
    切种族时此段 invalid,但前面的 rules 仍命中。
    """
    race_display = (my_race or "Protoss").capitalize()
    parts = [
        f"### 玩家当前种族:{race_display}",
        "",
        # 别名按"别名…→规范名"**分组**展示（不是扁平词表）：扁平列表只告诉 LLM"这些是建筑词"，
        # 不告诉它每个词映射到哪个建筑 → 语音误转的同音词（如"地堡"被听成"低保"）LLM 会凭自己的
        # 先验乱猜（低保→补给站/房子）。分组后映射显式可见，LLM 直接照表 normalize。
        "别名表(别名…→规范名;仅供 normalize 用,不是任务清单):",
        f"- 建筑:{_grouped_aliases(getattr(aliases, 'buildings', {}))}",
        f"- 单位:{_grouped_aliases(getattr(aliases, 'units', {}))}",
        f"- 升级:{_grouped_aliases(getattr(aliases, 'upgrades', {}))}",
        "",
        build_strategy_catalog(library, my_race),
    ]
    return "\n".join(parts)


def _grouped_aliases(group: dict) -> str:
    """把 {canonical: AliasEntry} 渲染成 "别名1/别名2→Canonical; ..." 的分组串。

    展示每个规范名的全部别名（含 default_display），让 alias→canonical 映射对 LLM 显式可见。
    去重保序，跳过与 canonical 完全相同的词。
    """
    out: list[str] = []
    for canonical, entry in group.items():
        words: list[str] = []
        for a in [getattr(entry, "default_display", None), *getattr(entry, "aliases", [])]:
            if a and a != canonical and a not in words:
                words.append(a)
        if words:
            out.append(f"{'/'.join(words)}→{canonical}")
        else:
            out.append(canonical)
    return "; ".join(out)


def build_strategy_catalog(library: StrategyLibrary, my_race: str | None = None) -> str:
    """第 2 段：Strategy Catalog（剧本目录一览）。

    my_race 给定时（"protoss" / "zerg" / "terran"），仅列出属于当前种族的剧本，
    避免 LLM 把跨种族 id 当作合法选择 emit 出来。未登记种族的剧本（race_of=None）
    保留显示，保持向后兼容（旧 fixture 直接构造 StrategyLibrary 时）。
    """
    parts: list[str] = ["可用剧本目录（仅可用以下 id）：\n"]
    race = my_race.lower() if my_race else None

    def _keep(sid: str) -> bool:
        if race is None:
            return True
        r = library.race_of(sid)
        return r is None or r == race

    parts.append("### opening_build")
    for s in library.all_strategies():
        if not isinstance(s, OpeningBuild) or not _keep(s.id):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    parts.append("\n### midgame_stance")
    for s in library.all_strategies():
        if not isinstance(s, MidgameStance) or not _keep(s.id):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    parts.append("\n### lategame_doctrine")
    for s in library.all_strategies():
        if not isinstance(s, LategameDoctrine) or not _keep(s.id):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    # 两层架构（2026-05-19）：持续运营策略（取代 lategame_doctrine + midgame_stance）
    parts.append("\n### persistent_doctrine (持续运营策略 - 开局完成后切入)")
    for s in library.all_strategies():
        if not isinstance(s, PersistentDoctrine) or not _keep(s.id):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    return "\n".join(parts)


def build_few_shot() -> str:
    """第 3 段：Few-shot 示例(从 docs/llm_prompt/few_shot.md 读取)。

    无占位符,直接返回文件内容。新增示例请直接编辑 md。
    """
    return _FEW_SHOT_PATH.read_text(encoding="utf-8")


def build_few_shot_en_supplement() -> str:
    """英文输入补充示例(从 docs/llm_prompt/few_shot.en.md 读取)。

    只在玩家 UI 语言=英文时追加到 few_shot 块尾(IntentParser 构造时按 locale 拼)。
    它**不替换**中文主示例,只补"英文话语→同一套 directives"的样例提升英文解析一致性。
    文件缺失时返回空串(graceful，不崩)。
    """
    p = _PROMPT_DIR / "few_shot.en.md"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


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
    # 2026-05-28 让 LLM 看到当前建筑/升级,解析 delta 语义
    buildings = ", ".join(f"{k}:{v}" for k, v in sorted(ctx.buildings_summary.items())) or "(无)"
    upgrades = ", ".join(sorted(ctx.upgrades_done)) or "(无)"

    recent_evt = "\n  - ".join(ctx.recent_events) or "(无)"
    # B 局内 memory:有 recent_outcomes 时把"text → outcome 摘要"配对展开,
    # 让 LLM 看到自己上次解出来什么(防止反复变风格 / 让 LLM 能引用上次 directive_id)。
    # 没 outcomes 就退化只显示文本(向后兼容旧单测构造的 ParseContext)。
    if ctx.recent_outcomes and len(ctx.recent_outcomes) == len(ctx.recent_commands):
        recent_cmd_lines = [
            f"{cmd}\n    → {outcome}"
            for cmd, outcome in zip(ctx.recent_commands, ctx.recent_outcomes, strict=True)
        ]
        recent_cmd = "\n  - ".join(recent_cmd_lines) or "(无)"
    else:
        recent_cmd = "\n  - ".join(ctx.recent_commands) or "(无)"
    standing = "\n  - ".join(ctx.standing_orders) or "(无)"

    return f"""当前游戏状态：
- 游戏时间：{time_str} (内秒 {ctx.game_time:.1f})
- 当前阶段：{ctx.current_stage.value}
- 活跃剧本：{active_str}
- 资源：晶矿 {ctx.minerals}, 瓦斯 {ctx.gas}, 人口 {ctx.supply_used}/{ctx.supply_cap}, 扩张 {ctx.expansion_count}
- 我方军队摘要：{army}
- 我方已造建筑(ready count)：{buildings}
- 我方已完成升级：{upgrades}
- 已知敌情：{enemy}
- 当前 standing orders：
  - {standing}
- 最近事件：
  - {recent_evt}
- 玩家最近指令(含你上次解析摘要,延续风格 + 可回引 directive_id)：
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
                                    "strategy_cancel / "
                                    "production_override / tech_override / "
                                    "expansion_override / structure_override / "
                                    "tactical_objective / "
                                    "unit_claim / scout / move / build_at / unit_release / "
                                    "drop_act"
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
                "clarification": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": (
                        "2026-05-24:玩家话语指向多种合理解读时,给 2-4 个候选选项 + ❌。"
                        "PWA 弹层显示,玩家点选 → submit 该选项的 directives。"
                        "directives 应留空(等玩家选)。typical use: 单位指代不明确"
                        '("那个农民" 指多个 Probe?)、modifier 缺失("出 4 个" 不知出啥)。'
                    ),
                    "required": ["question", "options"],
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "给玩家看的提问(如 '你是指哪个农民?')",
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["label", "interpretation_zh", "directives"],
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "选项按钮 label(≤20 字)",
                                    },
                                    "interpretation_zh": {
                                        "type": "string",
                                        "description": "该选项的中文解释,辅助说明",
                                    },
                                    "directives": {
                                        "type": "array",
                                        "description": "选中此项后 submit 的 directive 列表",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["type", "payload"],
                                            "properties": {
                                                "type": {"type": "string"},
                                                "payload": {
                                                    "type": "object",
                                                    "additionalProperties": True,
                                                },
                                                "priority": {"type": "integer"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
