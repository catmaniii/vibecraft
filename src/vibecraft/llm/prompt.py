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

from vibecraft.directives.types import StageKind
from vibecraft.strategy.aliases import AliasTable
from vibecraft.strategy.library import StrategyLibrary
from vibecraft.strategy.models import (
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

    return f"""你是 VibeCraft 的语义解析器。你只做一件事：把玩家中文/英文混合的 SC2 神族指令翻译成结构化的 directive 数组。

规则：
1. 输出**必须**通过提供的 tool `emit_directives` 返回。**绝不直接 free-text 回复**。
2. 不发明剧本 id。仅可用 catalog 列出的剧本。
3. 不"近似猜测"半懂半不懂的指令；不确定就给低 confidence，让玩家二次确认。
4. 别名 normalize：玩家说 "VR" / "球塔" / "兵营"，你输出 canonical id。
5. 复合句拆成多个 directive（顺序保留）。但若玩家整句话整体就是某个 catalog 剧本——哪怕用 build 步骤的口语描述（如「单BG VR出不朽」对应 `1g_robo_immortal`）——只输出**单条** strategy_set，**不要**再把其中的建筑/单位拆成额外的 production_override / tech_override。判断依据：对照 catalog 里每个剧本的内容摘要。
6. 不要下任何 SC2 API；不要评估剧本能不能赢。

别名表（仅供 normalize 用，不是任务清单）：
- 建筑别名：{building_aliases}
- 单位别名：{unit_aliases}
- 升级别名：{upgrade_aliases}

verb 消歧规则：
- 玩家说 "造 / build / 起一个" + 建筑名 → building 表
- 玩家说 "出 / train / 训练" + 单位名 → unit 表
- 玩家说 "研 / 研究 / 升 / research" + 升级名 → upgrade 表
- "VR" 仅指机械工厂（建筑 RoboticsFacility）；虚空辉光舰不叫 VR

====== unit_claim.task.primary_action.verb 白名单（15 个，严格字面值） ======

**只允许下表 15 个值,不允许变体。常见错误:**
- 错:`"move"`(✗) → 对:`"move_to"`
- 错:`"scout"`(✗,verb 没这个;侦察走顶层 scout directive 或 tactical_objective verb=scout)
- 错:`"hold_position"` 用在 stance 字段(✗,那是 verb 不是 stance)
- 错:`"guard"`(✗) → 对:`"guard_position"`

| enum 字面值 | 玩家口语常说法 |
|---|---|
| `hold_position` | 守住别动 / 原地不动 / 钉死 / 待原地 / 站桩 / 守这里别走 |
| `guard_position` | 守某点 / 守这块地 / 卡位 / 警戒某处 |
| `move_to` | 去 / 移到 / 过去 / 派去 / 移动到 / 到某处 / 让 X 去 Y |
| `patrol` | 巡逻 / 来回走 / 来回探 |
| `follow` | 跟着 / 跟上 / 紧跟 |
| `retreat` | 回来 / 撤回 / 撤回基地 / 回家 |
| `attack_move` | A 过去 / 边走边打 / 推过去 |
| `focus_fire` | 集火 / 集火打 / 锁这个 |
| `kite` | 风筝 / 放风筝 / 边跑边打 |
| `harass_workers` | 骚扰农民 / 提农民 / 打他工人 / 拆农民 |
| `lift_target` | 举起来 / 提起来 / 把这个举了（凤凰举不朽/坦克）|
| `cast_ability` | 放技能 / 用 PsiStorm / 放风暴 / 放 FF |
| `gather` | 去采矿 / 回去挖矿 |
| `build` | 让这个农民去造 |
| `cancel` | 取消这个 / 别造了 |

====== engagement_constraint.stance 白名单（4 个，严格字面值） ======

**只允许 4 个值,不允许变体。常见错误:**
- 错:`"hold_position"`(✗) → 对:`"hold"`
- 错:`"guard"`(✗) → 对:`"defend"`
- 错:`"守家"`(✗,要用 enum 英文字面值)

| enum 字面值 | 玩家口语常说法 |
|---|---|
| `defend` | 守家 / 防守 / 防 / 守住自己基地 |
| `hold` | 原地待命别动 / 别动 / 停下 / 静止 / 按兵不动 / 全员别走 |
| `retreat` | 撤 / 撤退 / 全部撤回基地 |
| `free` | 随便打 / 自由发挥 / 自由攻击 |

**关键区分**:
- `engagement_constraint(stance=hold)` 影响**整支军队**的 stance(全局静止)
- `unit_claim(task.verb=hold_position)` 影响 selector 指定的**特定单位**(单位级)
- 玩家说"所有人原地别动" → engagement_constraint(stance=hold)
- 玩家说"那个叉子守住别动" → unit_claim(selector={unit_type:Zealot}, task.verb=hold_position, persistent=true)

====== scout 路由消歧 ======

侦察类话语有 3 种合法路由:
1. **顶层 scout directive**（推荐,玩家没指定 unit 时）:
   - "侦察一下 11 点" / "侦察对方主基地" → `scout(target={...})`
2. **tactical_objective(verb=scout)**（也合法,等价于 1）:
   - 同上指令也可以走这条
3. **unit_claim(verb=move_to)**（玩家指定 unit 去某地）:
   - "派那个探机去 11 点" → unit_claim verb=move_to(不是 scout)
**任何情况下 unit_claim.task.verb 都不能是 `"scout"`**（Verb enum 没此值）。

====== build_at.point 字段规则 ======

`build_at.point` 必须是 `[float, float]` 坐标元组,**不能是字符串**（"11 点" /
"natural" / "natural_third" 都会校验失败）。如果你算不出精确坐标,
**给 ambiguous 让玩家点击地图**,不要硬塞字符串。

TacticalObjective verb 白名单（11 个，仅此 11 个）：
- attack    进攻敌方目标区域
- defend    守卫己方区域
- scout     侦察目标区域
- expand    开矿 / 扩张
- harass    骚扰敌方（凤凰提农民、追猎压矿等）
- drop      载入空投目标
- vision    在指定区域获得视野并保持
- raze      彻底摧毁目标建筑群
- retreat   撤退回安全位置
- regroup   在指定点集结部队
- split     分兵多路

done_when 完成条件 kind 白名单（8 种基础 + 2 种复合）：
- unit_count_built_since  自指令下达以来产出某兵种数量达到阈值
- tech_done               升级 / 科技研究完成
- expansion_count         己方分基数量满足条件
- target_destroyed        目标建筑 / 单位被摧毁
- own_army_size_ratio     己方军队规模比例满足条件（相对满编）
- vision_acquired         在指定区域保持视野 N 秒
- enemy_killed_in_area    在指定区域击杀敌方单位数量满足条件
- time_elapsed_since      自某时间点起经过 N 秒（ref: directive_issued / game_start）
- any_of                  [复合] 任意子条件满足即完成
- all_of                  [复合] 所有子条件都满足才完成

done_when 语义规则：
- L2（tactical_objective）和 L4（production_override / tech_override 等精粒度）指令
  必须带 done_when 字段（结构化完成条件）+ timeout_s 兜底（单位：秒）。
- L1（strategy_set）和 L3（unit_claim standing order）通常 done_when=null。
- 每个 directive 只允许一个 done_when；复杂条件用 any_of / all_of 组合。
- timeout_s 是兜底，无论 done_when 是否满足，超时后 directive 自动结束。

====== 指令的 4 层分类 (优先级金字塔) ======

每条话语解析时, 你要判断属于哪一层(可一句话拆多条不同层):

L1 宏观策略 (整阶段持续):
- "切 4BG" / "上 Skytoss" / "切叉球一波" → strategy_set(stage, strategy_id)
- "撤" / "取消剧本" / "停" → strategy_cancel(stage="all" 或 specific)
- L1 通常 done_when=None (剧本 phase 系统自己管)

L2 战术指令 (阶段性 objective, 不指定 unit):
- "进攻自然" / "守家" / "探中场" / "凤凰骚扰对面" →
  tactical_objective(verb, target_area, ...) + done_when
- "守家 / 撤" → engagement_constraint(stance) + done_when (timing/condition)
- L2 必带 done_when (任务完成判定),timeout_s 兜底

L3 单兵 / Standing order (指定单位干啥, 可一次性可持久):
- 一次性: "凤凰举不朽" / "DT 偷家" → unit_claim(selector, task, persistent=false)
- 持久 (standing order): "叉子守这里别动" / "凤凰巡逻一二线" →
  unit_claim(..., persistent=true)
- 撤销: "那个叉子回来" → unit_release(selector)
- "11 点放水晶" → build_at
- L3 done_when:一次性可加(如 "凤凰举完就回" = harass+done),
  standing order 通常 None (玩家撤销才完)

L4 产能调整 (改造兵 / 升科技 / 开矿):
- "下个 BG 出 2 哨兵" → production_override(unit_type, count) +
  done_when=unit_count_built_since
- "先研闪烁" → tech_override(upgrade_id) + done_when=tech_done
- "开三矿" → expansion_override(target_count) +
  done_when=expansion_count(op=">=", value=3)
- L4 必带 done_when

判断规则:
- 玩家提到具体剧本名 (4BG/IAC/Skytoss) → L1
- 提到 verb (进攻/守/探/骚扰) 但不指定具体 unit → L2
- 指定具体 unit (那个叉子/凤凰/DT) → L3
- 提到生产/升级/扩张 → L4
- 复合指令: 一句话多层, 拆成多条 directive
"""


def build_strategy_catalog(library: StrategyLibrary) -> str:
    """第 2 段：Strategy Catalog（剧本目录一览）。"""
    parts: list[str] = ["可用剧本目录（仅可用以下 id）：\n"]

    parts.append("### opening_build")
    for s in library.all_strategies():
        if not isinstance(s, OpeningBuild):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    parts.append("\n### midgame_stance")
    for s in library.all_strategies():
        if not isinstance(s, MidgameStance):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

    parts.append("\n### lategame_doctrine")
    for s in library.all_strategies():
        if not isinstance(s, LategameDoctrine):
            continue
        aliases = ", ".join(f'"{a}"' for a in s.aliases) or "(无)"
        parts.append(f"- `{s.id}` —— {s.display_name_zh}：{s.summary_zh} (aliases: {aliases})")

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
→ unit_claim: selector={unit_type:"Phoenix"}, task={primary_action:{verb:"lift_target", target:{kind:"unit_type", unit_type:"Immortal"}}}, persistent=false

例 5b：「那个探机守气矿别动」
→ unit_claim: selector={unit_type:"Probe"}, task={primary_action:{verb:"hold_position", target:{kind:"named_spot", named_spot:"enemy_main_gas"}}}, persistent=true
（persistent=true 表示 standing order；玩家明确说"一直守"/"别动"/"持续"时使用）

例 6：「11 点盖水晶」
→ build_at: structure_type=Pylon, point=[11克坐标]   (M0：若给不出精确点，confidence 降低)

例 7：「那个叉子回来」
→ unit_release: selector={...}, return_to_role=IDLE

例 8：「切到双矿凤凰，然后凤凰好提对方农民」
→ [strategy_set, unit_claim(selector=phoenix, task=harass_workers)]

例 9：「取消当前剧本」/「停下」/「等等」/「先别按剧本走」/「取消所有剧本」/「停止刷兵」
→ strategy_cancel: stage=all
（玩家想清掉 bot 当前的宏观策略,bot 切到 sustain 模式：只 macro/守家,不主动出门。
  若玩家明确指定 stage：「取消开局剧本」→ stage=opening；「取消中期」→ stage=midgame）

--- done_when 典型 pattern ---

例 10：「进攻对方自然」
→ [tactical_objective: verb="attack", target_area="enemy_natural",
   done_when={kind:"any_of", conditions:[
     {kind:"target_destroyed", target_kind:"natural"},
     {kind:"own_army_size_ratio", op:"<=", value:0.3}
   ]},
   timeout_s: 120]
（任意子条件：自然基被摧毁，或己方军队损耗超 70%，完成）

例 11：「下个 BG 出 2 哨兵」
→ [production_override: unit_type="Sentry", count=2,
   done_when={kind:"unit_count_built_since", unit_type:"Sentry", op:">=", value:2},
   timeout_s: 60]
（自指令下达起，产出 2 个哨兵即完成）

例 12：「先研闪烁」
→ [tech_override: upgrade_id="Blink",
   done_when={kind:"tech_done", upgrade_id:"BlinkTech"},
   timeout_s: 90]
（闪烁研究完成即完成）

例 13：「看一眼对方主基地」
→ [tactical_objective: verb="scout", target_area="enemy_main",
   done_when={kind:"vision_acquired", area:"enemy_main", hold_seconds:5},
   timeout_s: 30]
（在对方主基地保持视野 5 秒即完成）

例 14：「凤凰打死对方 5 个农民就回」
→ [tactical_objective: verb="harass", target_area="enemy_main",
   unit_type_hint:["Phoenix"],
   done_when={kind:"enemy_killed_in_area",
              area:"enemy_main", unit_type:"Probe", op:">=", value:5},
   timeout_s: 90]
（在主基地区域击杀 5 个探机即完成）

例 15：「30 秒后撤」
→ [engagement_constraint: stance="retreat",
   done_when={kind:"time_elapsed_since", seconds:30, ref:"directive_issued"},
   timeout_s: 60]
（自指令下达起经过 30 秒即完成）

--- 边界 case ---

例 16 (复合 L1+L3): 「切凤凰运营,凤凰好骚扰对面农民」
→ [
    strategy_set(stage=midgame, strategy_id=phoenix_2base),  # L1
    unit_claim(selector={unit_type:"Phoenix"},
               task={primary_action:{verb:"harass",
                     target:{kind:"named_spot", named_spot:"enemy_main"}}},
               persistent=true,
               done_when={kind:"enemy_killed_in_area", area:"enemy_main",
                          unit_type:"Probe", op:">=", value:5},
               timeout_s:120)   # L3 standing + done
  ]

例 17 (L2 engagement + done): 「守家直到闪烁好」
→ [engagement_constraint(stance="defend"),
   done_when={kind:"tech_done", upgrade_id:"BlinkTech"},
   timeout_s:300]
（done_when 用 tech_done 把 stance lifecycle 绑定到科技完成）

例 18 (撤销所有 standing): 「全部撤销 / 守家的都解散」
→ [strategy_cancel(stage="all")]
注:standing order 撤销由 PWA UI 处理 (revoke_directive 帧),不进 LLM directive

例 19 (无法解析 / 含糊): 「打吧」
→ confidence < 0.5,空 directives list,interpretation_zh 说明"指令含糊,
   建议:'打哪'/'打谁'/'什么时候'"
注:LLM 不猜测玩家本意,低置信走 ambiguous 路径

例 20 (单位类型推断): 「3 个凤凰巡逻自然」
→ [unit_claim(selector={unit_type:"Phoenix"},
               task={primary_action:{verb:"patrol",
                     target:{kind:"named_spot", named_spot:"natural"}}},
               persistent=true,
               unit_count_hint:3,
               timeout_s:99999)]
注:selector 不带 count (bot 自己挑数量),unit_count_hint 仅作提示
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
                                    "strategy_cancel / "
                                    "production_override / tech_override / "
                                    "expansion_override / engagement_constraint / "
                                    "tactical_objective / "
                                    "unit_claim / scout / move / build_at / unit_release"
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
