"""Directive envelope 模型 + 每个 type 的 payload schema。

设计取舍：payload 用 discriminated union（discriminator='type'），让
单个 `Directive` 类型既能 ser/de，也能在 Python 内类型收窄。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Discriminator, Field, field_validator, model_validator

from vibecraft.directives.scope import ScopeSpec, Selector, TargetSpec, check_group_id_range
from vibecraft.directives.task import Task
from vibecraft.directives.types import DirectiveType, IssuedBy

# =========================================================================
# DoneWhen discriminated union（8 kind + 2 复合）
# =========================================================================


class UnitCountBuiltSince(BaseModel):
    """某兵种产量达到阈值（自 directive 下达以来）。"""

    kind: Literal["unit_count_built_since"]
    unit_type: str
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TechDone(BaseModel):
    """升级/科技研究完成。"""

    kind: Literal["tech_done"]
    upgrade_id: str


class ExpansionCount(BaseModel):
    """己方分基数量满足条件。"""

    kind: Literal["expansion_count"]
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TargetDestroyed(BaseModel):
    """目标建筑/单位被摧毁。"""

    kind: Literal["target_destroyed"]
    target_kind: Literal["natural", "third", "main", "building_at", "unit_type"]
    target_param: str | None = None
    area: str | None = None  # 可选，e.g. "enemy_natural"


class OwnArmySizeRatio(BaseModel):
    """己方军队规模比例满足条件（相对于满编）。"""

    kind: Literal["own_army_size_ratio"]
    op: Literal[">=", "<=", "==", ">", "<"]
    value: float


class VisionAcquired(BaseModel):
    """在指定区域保持视野 N 秒。"""

    kind: Literal["vision_acquired"]
    area: str  # named_spot
    hold_seconds: float


class UnitArrived(BaseModel):
    """2026-05-24 用户:被指令的单位距 target 小于 within_grid 即 done。

    用于 move/safe_move 自动完成判定。selector 内全部单位都满足才 done。
    """

    kind: Literal["unit_arrived"]
    area: str  # named_spot 或坐标字符串 "(x,y)"
    within_grid: float = 5.0  # 距 < 此值算到达


class UnitHeldPosition(BaseModel):
    """2026-05-24 用户:被指令的单位在 target 范围内持续 hold_seconds 秒 → done。

    用于 scout/vision 持续保持判定。selector 内任一单位满足就计时,中断
    重置(目前 task_monitor 简化:每 tick 全员 < within_grid 才累加)。
    """

    kind: Literal["unit_held_position"]
    area: str
    within_grid: float = 5.0
    hold_seconds: float = 5.0


class EnemyKilledInArea(BaseModel):
    """在指定区域击杀敌方单位数量满足条件。"""

    kind: Literal["enemy_killed_in_area"]
    area: str
    unit_type: str
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TimeElapsedSince(BaseModel):
    """自某时间点起经过 N 秒。"""

    kind: Literal["time_elapsed_since"]
    seconds: float
    ref: Literal["directive_issued", "game_start"] = "directive_issued"


# ---------------------------------------------------------------------------
# P0d L4 done_when 扩词表（运营类指令）
# ---------------------------------------------------------------------------

_OP = Literal[">=", "<=", "==", ">", "<"]


class StructureCount(BaseModel):
    """当前建筑存量（含 pending）。区别于 unit_count_built_since（增量）。"""

    kind: Literal["structure_count"]
    structure_type: str
    op: _OP
    value: int


class StructureCountBuiltSince(BaseModel):
    """自 directive 下达起已建成的建筑数量(2026-05-24 用户)。

    类比 unit_count_built_since,但订阅 BUILDING_COMPLETE event。
    典型用 build_at default done_when:建筑成 1 个即关单。
    """

    kind: Literal["structure_count_built_since"]
    structure_type: str
    op: _OP = ">="
    value: int = 1


class OwnUnitCount(BaseModel):
    """己方某兵种当前存量（含 pending）。"""

    kind: Literal["own_unit_count"]
    unit_type: str
    op: _OP
    value: int


class SupplyUsed(BaseModel):
    """当前人口已用。"""

    kind: Literal["supply_used"]
    op: _OP
    value: int


class SupplyCap(BaseModel):
    """当前人口上限。"""

    kind: Literal["supply_cap"]
    op: _OP
    value: int


class Minerals(BaseModel):
    """当前晶矿。"""

    kind: Literal["minerals"]
    op: _OP
    value: int


class Gas(BaseModel):
    """当前瓦斯。"""

    kind: Literal["gas"]
    op: _OP
    value: int


class WorkerCount(BaseModel):
    """当前工人数。"""

    kind: Literal["worker_count"]
    op: _OP
    value: int


class StructureReadyNear(BaseModel):
    """目标点附近已有建好的某类型建筑(2026-06-06 代理建造等位置特定门控)。

    用于"在 X 处已有一个建好的 Pylon"这类位置判定(只数附近的,不看全局)。
    """

    kind: Literal["structure_ready_near"]
    structure_type: str
    area: str  # named_spot 或 "(x, y)" 坐标字符串
    within_grid: float = 8.0


class ChainStructureReady(BaseModel):
    """连续指令链里"前一步农民造出的那一个建筑"已建好(2026-06-06)。

    农民 build 出建筑瞬间后端抓住该建筑 tag 绑到 chain_id,这里按 tag 精确判它是否
    ready —— 不看全局计数/距离。典型:gateway 等"卡上一步那个 pylon"建好(能量场)。
    """

    kind: Literal["chain_structure_ready"]
    chain_id: str


class AnyOf(BaseModel):
    """复合：任意一个子条件满足即完成。"""

    kind: Literal["any_of"]
    conditions: list[DoneWhen]  # forward ref


class AllOf(BaseModel):
    """复合：所有子条件都满足才完成。"""

    kind: Literal["all_of"]
    conditions: list[DoneWhen]


DoneWhen = Annotated[
    UnitCountBuiltSince
    | TechDone
    | ExpansionCount
    | TargetDestroyed
    | OwnArmySizeRatio
    | VisionAcquired
    | UnitArrived
    | UnitHeldPosition
    | EnemyKilledInArea
    | TimeElapsedSince
    | StructureCount
    | StructureCountBuiltSince
    | StructureReadyNear
    | ChainStructureReady
    | OwnUnitCount
    | SupplyUsed
    | SupplyCap
    | Minerals
    | Gas
    | WorkerCount
    | AnyOf
    | AllOf,
    Field(discriminator="kind"),
]

# 解决 forward ref（AnyOf/AllOf 嵌套 DoneWhen）
AnyOf.model_rebuild()
AllOf.model_rebuild()


# =========================================================================
# TacticalVerb
# =========================================================================

TacticalVerb = Literal[
    "attack",
    "defend",
    "scout",
    "recon",  # 火力侦查：中后期成建制小股部队（追猎/不朽/凤凰等有战斗力的兵
    # ~3-8 单位）前压试探，能占便宜就占，不行脱离/撤退。主要目标信息，
    # 次要保留实力。**前期单个农民走 scout 即可；中后期敌兵多，单农民
    # 进去凶多吉少，必须用 recon（带战斗力才能安全脱离）**。区别：
    # - scout：纯视野，不打（前期单农民、Obs 飞越；早期低风险）
    # - recon：边打边看（中后期 4 追猎、5 凤凰；得有战斗力安全撤回）
    # - harass：主求经济伤害（凤凰提农民、追猎压矿）
    # - attack：committed 大军交战（done_when=None，玩家点 × 才停）
    "expand",
    "harass",
    "drop",
    "vision",
    "raze",
    "retreat",
    "regroup",
    "split",
    # 2026-05-28 用户:hold = 聚团到指定点 + 站住(不主动 attack 也不回家)
    # 区别 defend(回家) — hold 保持前线位置;target_area 给了 → 聚到该点,
    # None → 当前 army_center 锁住。done_when=None,玩家 × 解除。
    "hold",
]


# =========================================================================
# Payload models（每个 directive type 一个）
# =========================================================================


class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # P3 新增（可选；L1 STRATEGY_SET 通常保持 None）：
    done_when: DoneWhen | None = None
    timeout_s: int | None = None
    # 2026-05-28 用户:激活门 — directive commit 后并不立刻生效,等 activate_when
    # 条件满足才走 _apply_to_facade。典型场景:玩家"1 攻好了再进攻" → emit
    # tactical_objective(verb=attack, activate_when=tech_done(GroundWeaponsLevel1))。
    # 在 1 攻完成前 intent 不被 set,units 不动;1 攻完成后立即激活 + 出门。
    # None = 立即激活(向后兼容默认行为)。
    activate_when: DoneWhen | None = None


class StrategySetPayload(_PayloadBase):
    """切换某个阶段的剧本（设计文档 §8.1 A）。"""

    type: Literal[DirectiveType.STRATEGY_SET] = DirectiveType.STRATEGY_SET
    stage: Literal["opening", "midgame", "lategame"]
    strategy_id: str


class StrategyCancelPayload(_PayloadBase):
    """取消(单/全)阶段剧本:清掉 board slot,bot 降级 sustain plan(不主动出门)。

    stage="all" 取消所有阶段,bot 完全停在 sustain 模式等下个指令。
    """

    type: Literal[DirectiveType.STRATEGY_CANCEL] = DirectiveType.STRATEGY_CANCEL
    stage: Literal["opening", "midgame", "lategame", "all"] = "all"


class ProductionItem(BaseModel):
    """单个出兵需求：unit_type + count。production_override.items 元素。"""

    model_config = ConfigDict(extra="forbid")

    unit_type: str
    count: int = 1


class ProductionOverridePayload(_PayloadBase):
    """中粒度：override 当前剧本的生产计划。

    `items` 是出兵需求列表，**一条 directive 可含多兵种**（一句话「出 2 个叉子加
    3 个追猎」=>一条 directive 两个 item）。可选 `building_tag` 指定建筑，
    相当于 §8.1 D 的"这 Robo 改造 X"。
    """

    type: Literal[DirectiveType.PRODUCTION_OVERRIDE] = DirectiveType.PRODUCTION_OVERRIDE
    items: list[ProductionItem] = Field(min_length=1)
    building_tag: int | None = None
    building_selector: Selector | None = None
    priority: int = 50
    # 2026-06-07 用户:"在前线/某地点刷 N 兵"。指定折跃落点 → 折跃门兵种折跃在**离该点
    # 最近的能量场**(ready 水晶塔 或 展开棱镜)。None = 不指定,走剧本默认折跃位。
    # 仅折跃门兵种(叉子/追猎/使徒/哨兵/电兵/DT)生效;机械/空军没法折跃,忽略此字段。
    # 落点附近暂无能量场 → 指令挂起等待,等出现能量场再折跃(不丢出兵)。
    warp_at: TargetSpec | None = None


class TechOverridePayload(_PayloadBase):
    """优先研究某科技。"""

    type: Literal[DirectiveType.TECH_OVERRIDE] = DirectiveType.TECH_OVERRIDE
    upgrade_id: str
    building_tag: int | None = None
    priority: int = 50


class ExpansionOverridePayload(_PayloadBase):
    type: Literal[DirectiveType.EXPANSION_OVERRIDE] = DirectiveType.EXPANSION_OVERRIDE
    target_count: int = Field(description="期望分基数")
    priority: int = 50


class StructureItem(BaseModel):
    """单个建筑需求：structure_type + (target_count 或 delta) + 可选 location_hint。

    structure_override.items 元素。location_hint 在 item 级（不同建筑可以放
    不同位置 —— "二矿放 2 BE 1 BF" 中 BE 和 BF 可以同 hint）。

    target_count vs delta(2026-05-28 用户):
      - target_count = 绝对总数目标("补到 / 补齐 / 造到 N 个")
      - delta = 新增 N 个("补 N / 造 N / 再来 N",不参考当前)
      恰好二选一。delta 在 submit 时由 Director 用 buildings_summary 解算成
      effective target_count = current_ready + delta,然后清掉 delta。
    """

    model_config = ConfigDict(extra="forbid")

    structure_type: str
    target_count: int | None = Field(default=None, ge=1)
    delta: int | None = Field(
        default=None,
        ge=1,
        description="新增 N 个(不看当前)。submit 时 Director 解算成绝对 target_count。",
    )
    location_hint: str | None = None
    addon_decided: bool = Field(
        default=False,
        description=(
            "玩家是否已对这批新建产能建筑(兵营/重工/机场)的挂件做了决定"
            "(给了 mix 或明说不挂)。True=直接执行,False=触发弹窗。"
            "仅对人族产能建筑有意义;非产能建筑忽略。"
        ),
    )

    @model_validator(mode="after")
    def _xor_target_delta(self) -> StructureItem:
        has_target = self.target_count is not None
        has_delta = self.delta is not None
        if has_target == has_delta:
            raise ValueError(
                "StructureItem: target_count 与 delta 必须恰好二选一(都给 / 都不给都不允许)"
            )
        return self


class StructureOverridePayload(_PayloadBase):
    """L4 建筑数量目标。**一条 directive 可含多建筑**（一句话"二矿放 2 BE 1 BF"
    = 一条 directive 两个 item，作为单卡跟踪、全部完成才消失）。

    一次性：达成 target_count 就 done，被打掉不自动补
    （MVP 决策，参见 design doc §2 边界 case）。
    location_hint 在 item 级：main / natural / ramp / front / None（None = bot 自选）。
    """

    type: Literal[DirectiveType.STRUCTURE_OVERRIDE] = DirectiveType.STRUCTURE_OVERRIDE
    items: list[StructureItem] = Field(min_length=1)
    priority: int = 50


class EngagementConstraintPayload(_PayloadBase):
    """全局交战策略：`守家` / `不要出门` / `撤退到家`。

    DEPRECATED（P1b）：已合并到 TacticalObjectivePayload(persistent=True)。
    保留此类型纯粹为向后兼容（旧 JSONL log 反序列化 + 现有单测不 break）。
    Director._commit 收到此类型时自动映射为 TacticalObjective(verb=stance, persistent=True)。
    新代码不应再直接生成此 payload；LLM prompt 已改为输出 TacticalObjective。
    """

    type: Literal[DirectiveType.ENGAGEMENT_CONSTRAINT] = DirectiveType.ENGAGEMENT_CONSTRAINT
    stance: Literal["defend", "hold", "retreat", "free"]
    rally_point: TargetSpec | None = None


class UnitClaimPayload(_PayloadBase):
    """临时或持久占住一组单位，让它们按 Task 执行。"""

    type: Literal[DirectiveType.UNIT_CLAIM] = DirectiveType.UNIT_CLAIM
    selector: Selector
    task: Task
    # P1.1 新增：true 进 Director.standing_orders（L3 standing order），false 一次性
    persistent: bool = False
    # 2026-06-13 持续征兵：仅 persistent=True 时有意义。True 时每 tick 把新出现的
    # selector.unit_type 单位并入该 standing order 的 tag 集（独占 claim）。
    # LLM 给 recruit_new=True 但 persistent=False 时，Director 自动升级为 persistent。
    recruit_new: bool = False
    # 2026-06-29 #580 通用 recruit 上限：仅 recruit_new=True 时有意义。征兵入伍前判
    # len(group) < target_count 才并入；None = 无上限（征所有匹配单位，向后兼容旧行为）。
    # 玩家"派 N 个大件骚扰"→ target_count=N；"撤回 2"→ target_count-=2 + partial-release；
    # "停止骚扰"→ target_count=0（暂停征兵 + 释放全部，directive 留着，✗ 才删）。
    target_count: int | None = Field(default=None, ge=0)


class ScoutPayload(_PayloadBase):
    type: Literal[DirectiveType.SCOUT] = DirectiveType.SCOUT
    target: TargetSpec
    selector: Selector | None = None  # 没给则 bot 自选 idle probe


class MovePayload(_PayloadBase):
    type: Literal[DirectiveType.MOVE] = DirectiveType.MOVE
    selector: Selector
    target: TargetSpec
    # 2026-05-24 用户:safe_move = 走 plan_drop_path 递归算法避开敌方主基地。
    # 棱镜/侦察单位回家时玩家会说"贴边回 / 安全回 / 绕路回"。
    safe: bool = Field(default=False, description="True 走 plan_drop_path 避敌")
    # 2026-06-06 用户:沿途用 attack_move(遇敌就打),与 safe 叠加 —— safe 决定走哪条路,
    # engage 决定怎么走。到对方/推进类移动 engage=True;回家/撤退/纯转移 engage=False。
    engage: bool = Field(
        default=False, description="True 沿途 attack-move 遇敌就打;False 普通 move 不主动接敌"
    )


class BuildAtPayload(_PayloadBase):
    """指定位置建造某建筑 / 单位。

    2026-05-24 用户:玩家无法给精确坐标。扩 named_spot 字段支持
    "斜坡下/二矿基地旁/主矿矿区后面" 等模糊位置。point 改 optional。
    至少 point 或 named_spot 之一非空。
    """

    type: Literal[DirectiveType.BUILD_AT] = DirectiveType.BUILD_AT
    structure_type: str
    point: tuple[float, float] | None = None
    named_spot: str | None = Field(
        default=None,
        description="模糊地点(NamedSpotRegistry 解析): main_ramp / natural / "
        "behind_mineral_line / enemy_main_back 等",
    )
    by_probe: bool = Field(
        default=False,
        description="True=代理建造:用目标点附近最近的农民下达 build(不是 placement override)。"
        "配合 unit_claim(persistent) + activate_when=unit_arrived 两卡组合使用。",
    )
    chain_id: str | None = Field(
        default=None,
        description="代理建造链 ID(同 unit_claim.selector.chain_id)。"
        "设此字段后 Director 保证用该链 claim 的同一农民建造,不另选农民。"
        "by_probe=True 的 build_at 卡若属于某条代理链,必须设此字段。",
    )
    placement_confirmed: bool = Field(
        default=False,
        description="内部字段(LLM 不要设):建 townhall 落点 8-13 格模糊时 Director 会弹确认,"
        "玩家选完的 build_at 带 True → 不再二次拦截弹窗。",
    )


class UnitReleasePayload(_PayloadBase):
    """归还 claim。"""

    type: Literal[DirectiveType.UNIT_RELEASE] = DirectiveType.UNIT_RELEASE
    selector: Selector
    return_to_role: Literal["IDLE", "ARMY"] = "IDLE"


class TacticalObjectivePayload(_PayloadBase):
    """L2 战术指令：跨单位的中粒度战术目标（设计文档 §8.1 L2）。

    persistent: 是否持续姿态。
      - False（默认）= 一次性指令（全军进攻/撤退/防守一波），由 task_monitor 判 done 或玩家点 × 解除
      - True = 持续姿态（原 engagement_constraint 语义，写 stance_override 持续生效）
        等价于旧 engagement_constraint(stance=verb)，由 P1b 向后兼容映射路径生成。
    """

    type: Literal[DirectiveType.TACTICAL_OBJECTIVE] = DirectiveType.TACTICAL_OBJECTIVE
    verb: TacticalVerb
    target_area: str | tuple[float, float] | None = None  # named_spot 或坐标
    unit_count_hint: int | None = None  # None = bot 自决
    unit_type_hint: list[str] | None = None  # None = bot 自决
    priority: int = 50
    persistent: bool = False  # P1b: True = 持续姿态（旧 engagement_constraint 语义）
    # 2026-05-25 verb=attack 时的子模式("all_in"=强制不撤,"probe"=试探劣势撤,None=plan 默认)
    # UI 按钮路径由 _submit_tactical_action 透传给 facade 和 snapshot 展示用。
    attack_mode: Literal["all_in", "probe"] | None = None


class DropActPayload(_PayloadBase):
    """L4 复合空投指令（2026-05-23 brainstorming）。

    style:
      simple         → GenericDropAct(load@home → fly → unload@target)
      warp_then_drop → PrismWarpDropAct(fly → warp@frontline → load → fly → unload@deep)
    """

    type: Literal[DirectiveType.DROP_ACT] = DirectiveType.DROP_ACT
    style: Literal["simple", "warp_then_drop"] = "simple"
    cargo_unit: str
    cargo_count: int = Field(ge=1)
    transport: str = "WarpPrism"
    drop_target: str  # "enemy_natural:mineral" 等
    warp_at: str | None = None  # 仅 warp_then_drop 用
    after_unload: Literal["attack_workers", "attack_production", "retreat", "siege"] = (
        "attack_workers"
    )
    priority: int = 60


class ViewFollowPayload(_PayloadBase):
    """镜头持续跟随目标（2026-05-30；target_kind 扩展 2026-05-30）。

    target_kind 决定跟随目标：
      - "unit"（默认）: 跟随指定兵种 / tag 的单个单位
          Director 每 tick 调 facade.follow_unit(resolved_tag)
      - "army": 跟随全军主力质心
          每 tick 用 _compute_current_army_center() 算质心，调 facade.move_camera
      - "squad": 跟随 active recon / harass squad 的中心
          每 tick 取 _tactical_squads 第一个有存活单位的 squad 中心，调 facade.move_camera
      - "task": 跟随正在执行某持久任务的单位（scout/patrol/watchtower/harass）
          每 tick 按 task 找单位（scout→scout_worker.scout_tag / harass→harass squad /
          patrol·watchtower→standing_order claim），单个→follow_unit，多个→move_camera。
          任务结束 / 玩家取消 → 该单位归还 → 下一 tick 静默跳过（玩家 × 解除跟随）。

    selector 字段（unit_type / unit_tag / unit_type_hint）仅在 target_kind="unit" 时使用；
    task 字段仅在 target_kind="task" 时使用。army / squad 模式下两者均忽略（可不填）。

    persistent=True 表示持续跟随，直到：
      1. 玩家手动点 × → revoke
      2. 新的 view_follow 指令到达 → 旧的自动 superseded（同时只允许 1 条 active）
    """

    type: Literal[DirectiveType.VIEW_FOLLOW] = DirectiveType.VIEW_FOLLOW
    target_kind: Literal["unit", "army", "squad", "task", "group"] = Field(
        default="unit",
        description=(
            "跟随目标类型：unit=单个单位 / army=全军主力质心 / "
            "squad=recon/harass小队 / task=正在执行某持久任务的单位 / "
            "group=语音编队(group_id)"
        ),
    )
    unit_type: str | None = Field(
        default=None,
        description="跟随的兵种名(canonical)，如 Stalker / Phoenix / Immortal；仅 target_kind=unit 时用",
    )
    unit_tag: int | None = Field(
        default=None,
        description="直接锁定的单位 tag（精确选取，比 unit_type 优先）；仅 target_kind=unit 时用",
    )
    task: Literal["scout", "patrol", "watchtower", "harass"] | None = Field(
        default=None,
        description=(
            "跟随的任务身份（仅 target_kind=task 时用）：scout=探路农民 / "
            "patrol=巡逻单位 / watchtower=守瞭望塔单位 / harass=骚扰小队。"
            "Director 按 role/squad/standing_order 找正在执行该任务的单位，"
            "单个→follow_unit（平滑），多个→move_camera（质心）。"
        ),
    )
    unit_type_hint: str | None = Field(
        default=None,
        description="玩家原话中的中文单位描述，仅记录，不参与执行",
    )
    group_id: int | None = Field(
        default=None,
        description="跟随的语音编队号 1-5（仅 target_kind=group 时用）。"
        "玩家说'镜头跟随 N 队' → 跟该编队所有单位的质心。",
    )
    persistent: bool = True  # view_follow 始终是持续指令


class ProductionBlockPayload(_PayloadBase):
    """暂停造某种兵的持久指令（2026-05-30）。

    玩家说"暂时不出追猎"/"停止造叉子"/"别造哨兵" →
    directive 把兵种加入 knowledge.vibecraft.production_blocked set；sharpy
    ActUnit.execute / WarpUnit.execute 在下训练/折跃指令前检查该 set，命中就跳过
    （机制级拦截，2026-06-02；见 docs/sharpy-patches.md §8）。

    区别于 production_override（那是"必须出 N 个"，增量）：
    production_block 是"持续抑制，直到玩家 × 才解除"。

    unit_type: canonical 兵种名（Zealot / Stalker / Phoenix 等）。
    persistent=True 表示持续生效（与 view_follow 一致，玩家 × 才解除）。
    """

    type: Literal[DirectiveType.PRODUCTION_BLOCK] = DirectiveType.PRODUCTION_BLOCK
    unit_type: str = Field(description="要封锁的兵种 canonical 名，如 Stalker / Zealot")
    persistent: bool = True  # production_block 始终是持续指令


class GroupAssignPayload(_PayloadBase):
    """语音编队：把 selector 选中的单位编入 group_id(1-5)，SET 语义（替换）。"""

    type: Literal[DirectiveType.GROUP_ASSIGN] = DirectiveType.GROUP_ASSIGN
    group_id: int = Field(description="编队号 1-5；越界(0/6+)报错")
    selector: Selector
    # 2026-06-13 持续征兵：True 时 directive 留在 board，每 tick 把新出现的
    # selector.unit_type 单位 ADD 进该编队，直到玩家 × 撤销或 group_clear。
    # False（默认）：现行为不变（submit 即执行 SET 入队，立即 done）。
    auto_enroll: bool = False

    _check_group_id = field_validator("group_id")(check_group_id_range)


class GroupClearPayload(_PayloadBase):
    """解散 group_id 编队（释放/取消/清除 同义）。"""

    type: Literal[DirectiveType.GROUP_CLEAR] = DirectiveType.GROUP_CLEAR
    group_id: int = Field(description="编队号 1-5；越界(0/6+)报错")

    _check_group_id = field_validator("group_id")(check_group_id_range)


class RallyPointPayload(_PayloadBase):
    """出兵集结点（2026-06-07 用户）：设全局集结点，之后新出的兵自动 rally 到该点。

    persistent 全局状态：Director 每帧把 sharpy `gather_point` 覆盖成此点（sharpy 的
    set_gather_point 是一次性 flag，必须每帧重设）。玩家 × 撤销 → 恢复 bot 默认
    (家门口 ramp，随扩张自动前移)。**不占单位控制权** —— 只管"未来新出的兵去哪"，
    不 claim 现有单位(区别于 unit_claim 的"集中/待命")。
    """

    type: Literal[DirectiveType.RALLY_POINT] = DirectiveType.RALLY_POINT
    target: TargetSpec


class StealthMinePayload(_PayloadBase):
    """偷矿（2026-06-10）：玩家指定地图一片区域，bot 偷偷开隐蔽基地自给自足采矿。

    持续运营的隔离经济单元（不是一次性代理建造）。
    - point：玩家指定锚点（镜头中心 / 小地图点），单位 tile 坐标。
    - cell_id：由 StealthCellManager 分配后回填（提交时为 0 占位）。
    - worker_target：该 cell 目标农民数（1 矿 ~16，可调）。
    - with_gas：是否同时偷气（默认 True，无气点自动跳过）。
    - on_attack：受击行为（默认 flee = 撤销 stealth 地位交还 bot；hold = 硬守，本期未实现）。

    状态机：PENDING → BUILDING → MINING → RELEASED / DESTROYED
    由 StealthCellManager 驱动，不走 task_monitor / done_when 路径。
    """

    type: Literal[DirectiveType.STEALTH_MINE] = DirectiveType.STEALTH_MINE
    point: tuple[float, float] = (0.0, 0.0)  # 玩家指定点，LLM 填真实坐标
    cell_id: int = 0  # Manager 分配回填；提交时为 0
    worker_target: int = 16
    with_gas: bool = True
    on_attack: Literal["flee", "hold"] = "flee"


class SalvagePayload(_PayloadBase):
    """通用建筑回收（2026-06-19）：对选中建筑下 salvage ability。

    一次性动作，fire 完即结束，不占 Reserved。
    - selector：选哪些建筑（near_camera / unit_type=Bunker / tags…）。
    按建筑 type_id 自动映射对应 ability（地堡双变体都发，游戏自动忽略不适用的那个）。
    不可回收建筑（SupplyDepot 等）→ 友好提示，不报错。
    """

    type: Literal[DirectiveType.SALVAGE] = DirectiveType.SALVAGE
    selector: Selector


class BunkerCargoPayload(_PayloadBase):
    """地堡货舱控制（2026-06-19）：装兵进地堡（load）/ 卸出所有兵（unload）。

    一次性动作，不占 Reserved。
    - action="unload"：对每个地堡发 UNLOADALL_BUNKER，把所有乘员弹出。
    - action="load"：找最近的、不在地堡里的己方 Marine，最多 count 个（默认 4=满载），
      对每个 Marine 发 SMART(bunker) 让其进入。
    selector 用于选哪些地堡（unit_type="Bunker" / near_camera / tags）。
    """

    type: Literal[DirectiveType.BUNKER_CARGO] = DirectiveType.BUNKER_CARGO
    action: Literal["load", "unload"]
    selector: Selector
    count: int | None = None  # load 时塞几个兵（默认 4，地堡满载）；unload 时忽略


class RepairPayload(_PayloadBase):
    """通用维修指令（2026-06-19）：派 N 个 SCV 持续维修目标单位/建筑。

    持续型：Director 每 tick 检查目标健康，满血/消失才自动完成。
    - selector：选哪些目标（unit_type=Battlecruiser / near_camera / tags…）。
    - worker_count：每个目标派几个 SCV（默认 None=3）。
    仅人族 SCV 可修；神族/虫族无 repair ability（调用方不强制校验，后端 ensure_repair 静默跳过）。
    """

    type: Literal[DirectiveType.REPAIR] = DirectiveType.REPAIR
    selector: Selector
    worker_count: int | None = None  # 每个目标派几个 SCV（None=3）


class StructureMovePayload(_PayloadBase):
    """人族建筑起飞/移动（2026-07-08）："主基地飞起来" / "主基地飞到二矿"。

    执行走 director 状态机（复用 #543 `_build_addon_on_parent` 同款 LIFT→FLY→LAND
    机制）：FIND 找 from_spot 最近的 townhall（CommandCenter∪OrbitalCommand∪
    PlanetaryFortress，按其**真实 type_id** 取 LIFT_<真type>）→ LIFT → (to_spot=None
    悬停即完成 / to_spot 有则 FLY→LAND)。PlanetaryFortress 不能起飞（真机核对
    LIFT_PLANETARYFORTRESS 不存在）→ 友好拒绝。

    structure_type 仅作 LLM 语义提示，**不硬绑**执行 —— FIND 阶段按实际选中的
    townhall 真实类型解析 ability，None 也可（"主基地"默认找 townhall）。
    """

    type: Literal[DirectiveType.STRUCTURE_MOVE] = DirectiveType.STRUCTURE_MOVE
    structure_type: str | None = None
    from_spot: str
    # str | tuple 同 TacticalObjectivePayload.target_area 的模式：LLM 通常给
    # named_spot 字符串（"natural"/"third"）；玩家说"降落在这里/落这" → LLM 给
    # "camera"，_inject_camera_point 在 submit 前把它替换成真实镜头世界坐标 tuple。
    to_spot: str | tuple[float, float] | None = None


class WorkerTaskPayload(_PayloadBase):
    """农民基地调度（2026-07-08）："主矿农民优先采水晶" / "主矿农民去二矿采矿"。

    action:
      - prioritize_minerals / prioritize_gas：**复用全局** `facade.set_mining_priority`
        （宏观面板 mining 维度同一个开关，语音入口→全局），持续生效直到玩家再改。
        当前单基地/全局阶段 from_base 仅作语义记录，不做 per-base 隔离。
      - transfer_to_base：一次性把 from_base 附近**全部**正在采矿（非采气/非在建/
        非 Reserved）的农民持续钉去 to_base 采矿数秒（对抗 DistributeWorkers 拉回），
        settle 后释放归还 bot 采矿池。
    """

    type: Literal[DirectiveType.WORKER_TASK] = DirectiveType.WORKER_TASK
    from_base: str
    action: Literal["prioritize_minerals", "prioritize_gas", "transfer_to_base"]
    to_base: str | None = None


Payload = Annotated[
    StrategySetPayload
    | StrategyCancelPayload
    | ProductionOverridePayload
    | TechOverridePayload
    | ExpansionOverridePayload
    | StructureOverridePayload  # NEW: P0e Task 8
    | EngagementConstraintPayload
    | TacticalObjectivePayload
    | UnitClaimPayload
    | ScoutPayload
    | MovePayload
    | BuildAtPayload
    | UnitReleasePayload
    | DropActPayload
    | ViewFollowPayload
    | ProductionBlockPayload
    | GroupAssignPayload
    | GroupClearPayload
    | RallyPointPayload
    | StealthMinePayload  # WP1 2026-06-10 偷矿
    | SalvagePayload  # 2026-06-19 通用建筑回收
    | BunkerCargoPayload  # 2026-06-19 地堡货舱控制（装兵/卸载）
    | RepairPayload  # 2026-06-19 通用维修指令
    | StructureMovePayload  # 2026-07-08 人族建筑起飞/移动
    | WorkerTaskPayload,  # 2026-07-08 农民基地调度
    Discriminator("type"),
]


# type 值 → payload 模型类。供 IntentParser 在系统边界过滤 LLM 输出：
# LLM 可能在 payload 里塞 schema 外字段，按 model_fields 白名单过滤，
# 避免 _PayloadBase 的 extra=forbid 把整条 directive 拒掉。
_PAYLOAD_UNION = get_args(Payload)[0]
PAYLOAD_MODELS: dict[str, type[_PayloadBase]] = {
    m.model_fields["type"].default.value: m for m in get_args(_PAYLOAD_UNION)
}


# =========================================================================
# Directive envelope
# =========================================================================


def _gen_id() -> str:
    return f"d_{uuid.uuid4().hex[:6]}"


class Directive(BaseModel):
    """通用 envelope（设计文档 §5.2 通用字段）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_gen_id)
    payload: Payload
    issued_at: float = Field(description="收到时的游戏内秒")
    effective_at: float | None = Field(
        default=None,
        description="commit 起效时刻；None 时 Board 入队即填充 = issued_at + 1.5",
    )
    scope: ScopeSpec = Field(default_factory=ScopeSpec)
    priority: int = Field(default=50, ge=0, le=100)
    issued_by: IssuedBy = IssuedBy.VOICE
    source_text: str | None = Field(
        default=None,
        description="玩家原话（仅记录，不参与执行）",
    )

    @property
    def type(self) -> DirectiveType:
        """便捷访问 payload.type。"""
        return self.payload.type
