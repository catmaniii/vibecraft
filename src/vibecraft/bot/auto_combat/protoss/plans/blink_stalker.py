"""vibecraft 两矿闪追开局（6 BG Blink Stalker）plan。

战术核心
========
两矿 + 6 BG + 单 BF + 无 Robo，持续刷追猎：
  - 主力：Stalker 持续生产（全 6 BG 刷）
  - 升级：+1 攻（BF/Forge）+ Blink（VC → 闪烁）
  - 出门 timing：Blink 完成 + +1 攻完成 + 兵力足 → 出门
  - 无 Observer / Warp Prism / Adept（资源全压经济 + 追猎 + 升级）

设计理念（用户 2026-05-27 修正）
====================================
旧版：4 BG + Robo（Observer + WP）→ 资源分散，兵力爬坡慢，VC 太早挤矿。
新版：
  1. 6 BG（而非 4 BG）— 更强的持续生产能力
  2. 删 Robo（VR）— Observer / Immortal 全删，gas 全压追猎 + 升级
  3. 加 BF（Forge）— +1 武器必升，追猎 DPS 提升显著
  4. VC 时机稍晚 — BY ready 后先稳经济，等二矿起稳（UnitExists NEXUS 2）才建 VC
  5. attack_on_advantage=False — 靠 _ready_to_pressure 条件触发，不靠 sharpy power

关键路径
========
  ~1:25  BY（CyberneticsCore）+ NX（二矿 natural）
  ~1:49  二矿气矿
  ~2:00  Warpgate research（BY ready → 研）
  ~2:45  VC（TwilightCouncil）—— 二矿存在后才建，稍晚于旧版
  ~3:00  BF（Forge）—— VC 同期，不抢矿窗口
  ~3:20  +1 武器研究（BF ready → 立刻研）
  ~3:30  Blink 研究（VC ready → 立刻研 + chrono 加速）
  ~3:40  补 BG 到 4（BY ready 时建）
  ~4:10  补 BG 到 6（Time 4:10 兜底 — 经济稳了才补）
  出门 timing：Blink 完成 + +1 攻完成 + ≥ 20 supply 兵力 → PlanZoneAttack
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import Time, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.blink_kite_retreat_act import BlinkKiteRetreatAct
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class BlinkStalker(KnowledgeBot):  # type: ignore[misc]
    """两矿闪追（6 BG Blink Stalker）— 无 Robo，+1 攻 + Blink，持续刷追猎。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Blink Stalker")
        # 2026-05-28 Issue 4 修复:Step gate 用 AttackGate 包装,intent in
        # (attack/retreat/defend/hold) → True;一旦 True latch 永久 True。
        # 防 stalker 死光 supply<20 → _ready_to_pressure False → gate 关 →
        # PlanZoneAttack disable → retreat intent 没人读 → 单位不撤。
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_pressure)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_ready_to_pressure 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._ready_to_pressure),
            # ---------- chrono ----------
            # 探机 chrono 到 BY 出现
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono（BY 一好立刻）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # Blink chrono（VC 完成后立刻 chrono，timing 关键）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_BLINK, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # +1 武器 chrono（BF 好了立刻 chrono 加速）
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL1, UnitTypeId.FORGE),
            ),
            # +2 武器 chrono(2026-05-28 用户:+1 升好后马上接 +2)。
            # ChronoTech 仅在 Forge 真在 research LEVEL2 时 cast,空 idle 不浪费 chrono。
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL2, UnitTypeId.FORGE),
            ),
            # ---------- 早期主线（严守顺序）----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- BG ready → BY + 二矿（BY 先建，NX 跟在 BY 之后）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            Step(UnitExists(UnitTypeId.CYBERNETICSCORE, 1), Expand(2)),
            # ---------- 双矿气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- VC（TwilightCouncil）— 二矿存在后才建，优先运营稳经济 ----------
            # 旧版：BY ready 就建 VC（挤矿窗口）。
            # 新版：等二矿 NX exists 才建，让前期矿优先给二矿 + 农民爬坡。
            Step(
                UnitExists(UnitTypeId.NEXUS, 2),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- BF（Forge）— +1 武器前置，VC 同期不抢矿 ----------
            Step(
                UnitExists(UnitTypeId.NEXUS, 2),
                GridBuilding(UnitTypeId.FORGE, 1),
            ),
            # ---------- 升级 ----------
            # +1 武器（BF 好了立刻研）
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
            # +2 武器(2026-05-28 用户:+1 升好后马上接 +2)。
            # Tech 内部检查 +1 完成 + TwilightCouncil ready 才真下单,否则 idle 等。
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2)),
            # Blink（VC 好了立刻研）
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.BLINKTECH)),
            # ---------- 补 BG（目标 6）----------
            # 第一波补到 4（BY ready 时）：经济基础稳后补产能
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # 第二波补到 6（4:10 时间兜底）：经济爬起来后继续补
            Step(Time(60 * 4 + 10), GridBuilding(UnitTypeId.GATEWAY, 6)),
            # ---------- 三矿延后(2026-05-28 用户:5:30 三矿太早,挤掉 2 矿满采 + 出门 timing) ----------
            # 旧:Time(5:30) → Expand(3) 时间兜底,但用户实测此时 +1 攻 / Blink 还没好,
            # 三矿强开会挤掉产能,而且不出门(supply 增长更慢)。
            # 新:三矿等 _ready_to_pressure 满足(blink + +1 + 20 supply)后才开,
            # 这时已出门一波,该补三矿延续。9 分钟兜底防永远不出门。
            Step(
                lambda ai: BlinkStalker._ready_to_pressure(ai) or ai.time >= 60 * 9,
                Expand(3),
            ),
            # ---------- 三气(三矿 ready 后才上;旧 Time(4:30) 三气强行触发会浪费工程) ----------
            Step(UnitExists(UnitTypeId.NEXUS, 3), BuildGas(3)),
            # ---------- 单位训练（持续刷追猎，target 大值让 ProtossUnit 持续生产）----------
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 40),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 60),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 2026-05-28 用户需求 3:闪追风筝战术撤退 — 前线 stalker blink CD
                # 都没好 + 平均护盾低 → set vibecraft.kite_retreat=True → vendor
                # zone_attack._should_retreat 读 flag 触发 retreat 拖 CD。
                # CD 恢复后清 flag,重新前压。
                BlinkKiteRetreatAct(),
                # 出门条件：Blink 完成 + +1 攻完成 + ≥ 20 supply 兵力；玩家任意 intent
                # (attack/retreat/defend/hold)绕过。
                # 2026-05-28 Issue 4 修复:
                # 1. 加 retreat/defend/hold intent 到 gate(原只有 attack)→ 玩家
                #    retreat 时 PlanZoneAttack 仍被调用,内部 _should_retreat 触发撤退。
                # 2. 一旦 gate True latch True 永久 → 即使 stalker 死光 supply<20
                #    导致 _ready_to_pressure 转 False,PlanZoneAttack 也持续运行响应
                #    后续 retreat/attack 切换。
                Step(self._attack_gate, PlanZoneAttack(20)),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """闪追出门 timing：Blink 完成 + +1 攻完成 + 兵力 ≥ 20 supply。

        新逻辑（6 BG 版本）：
        - Blink 必须完成（没闪烁 = 追猎价值减半）
        - +1 武器完成（DPS 提升显著，是 Forge 的存在价值）
        - army supply ≥ 20（等兵凑够才出门，比旧版 10 stalker 条件更灵活）
        - time 兜底：7:00（Blink + +1 攻都好的情况下最多等到这时）
        """
        # Blink 必须完成
        blink_done = (
            ai.already_pending_upgrade(UpgradeId.BLINKTECH) >= 1.0
            or UpgradeId.BLINKTECH in ai.state.upgrades
        )
        if not blink_done:
            return False
        # +1 武器必须完成
        weapon_up_done = (
            ai.already_pending_upgrade(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1) >= 1.0
            or UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1 in ai.state.upgrades
        )
        if not weapon_up_done:
            return False
        # 兵力 supply ≥ 20（追猎 2 supply/unit，即 10 只追猎）
        stalker_supply = ai.units(UnitTypeId.STALKER).ready.amount * 2
        if stalker_supply >= 20:
            return True
        # 时间兜底：7:00
        return bool(ai.time >= 60 * 7)
