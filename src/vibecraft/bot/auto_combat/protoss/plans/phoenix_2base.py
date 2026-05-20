"""vibecraft 两矿凤凰（Phoenix 2-base opener）plan。

战术核心
========
双星门持续 chrono 凤凰，以骚扰 + 吊资源打乱对方节奏：
  - PvZ：飞虫（Mutalisk）出现前先下手，吊 Overlord + 骚扰 drone line
  - PvT：吊 SCV + 骚扰 bio 集结，配合地面 Stalker 守家

关键路径
========
1. 快速双矿（~1:24 NX）+ 折跃
2. 第 1 VS（BY ready 后建，~2:19）→ 凤凰开始产出
3. 4 凤凰后建第 2 VS（双星门拉产能）
4. VR（5 分钟后建，Observer 反隐）
5. 8 凤凰后三矿延续

注：无 VT / Blink（与 Phoenix build 不符，白烧约 300 gas）
   无 Warp Prism（两矿凤凰阶段用不上）

Build 节奏（参考 spawningtool.com 126982，HuShang Double Stargate Phoenix PvZ）
=============================================================================
  1:24  NX（双矿）
  1:34  BY（CyberneticsCore）
  1:43  BA x2（第 2 气）
  2:01  Adept @chrono（保家侦察）
  2:19  VS x1（**第一星门，先建 1 个**）
  2:28  Warp Gate @chrono
  3:31  Phoenix 第 1 个
  3:47  BA x3（三气）
  3:59  VS x2（第二星门，**等凤凰开始产出后加**）
  ~4:30 三气满采
  ~5:00 凤凰集结 8 → 出门骚扰
  ~5:00 NX（三矿，等 8 凤凰后）
  ~5:30 VR（Robo，Observer 反隐）
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
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class Phoenix2Base(KnowledgeBot):  # type: ignore[misc]
    """两矿凤凰（Double Stargate Phoenix）— 双星门 chrono 凤凰骚扰，PvZ/PvT。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Phoenix 2-base")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono ----------
            # 探机 chrono 直到 BY 出现
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # 凤凰 chrono：VS 完成后持续 chrono（核心骚扰单位产速）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ChronoUnit(UnitTypeId.PHOENIX, UnitTypeId.STARGATE),
            ),
            # ---------- 早期主线 ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- 快速双矿（~1:24）+ BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 双矿气矿（凤凰吃气多）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- 第 1 VS（BY ready 后建，~2:19）----------
            # 标准 build：先建 1 VS，等凤凰产出后再加第 2 VS
            # 不同时建 2 VS（矿资源竞争导致凤凰延后）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.STARGATE, 1)),
            # ---------- 第 2 VS（1 号星门一好就连下，撑满双星门产能）----------
            # 2026-05-20：原触发 UnitExists(PHOENIX,4)（~4:35 才下 2 号星门）→ 5:30
            # 只 6 凤凰。改 UnitReady(STARGATE,1)：1 号星门好就连下 2 号（~64s 提前）。
            # 实测 3 跑：stargate_2 稳定达成、phoenix_eight 6→7、首批凤凰 phoenix_four
            # 仍稳过 —— 双星门拉产能有效，"过早挤矿"代价可接受。
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.STARGATE, 2),
            ),
            # ---------- 三气（第 2 VS 建造期间补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(4)),
            # ---------- VR（Robotics，8 凤凰后建 Observer 反隐）----------
            # 标准 build：5:30 才建 VR；早期 VR 挤占凤凰矿资源
            Step(
                UnitExists(UnitTypeId.PHOENIX, 6),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # ---------- 三矿延续（8 凤凰后开，约 ~5:00）----------
            # 过早三矿（二矿存在就开）会在凤凰集结前守家压力过大
            Step(UnitExists(UnitTypeId.PHOENIX, 8), Expand(3)),
            # ---------- 三气（三矿后补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 3), BuildGas(5)),
            # ---------- 单位训练 ----------
            # Observer（VR 完成立刻，反隐必须）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # 凤凰主力（双星门 chrono，target 12）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ProtossUnit(UnitTypeId.PHOENIX, 12),
            ),
            # Stalker 保家（4 个，Stalker 目标降至 4 避免占用 BY 产槽拖慢凤凰）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 4, priority=True),
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
                # 8 凤凰 → 出门骚扰；玩家强制 attack 绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(8),  # 8 凤凰集结出门
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """两矿凤凰出门骚扰：双 VS + 8 凤凰 ready。

        等 8 个凤凰而非 4 个 —— 凤凰对地面没伤，孤注一掷骚扰需要数量保证存活率。
        """
        # 至少 1 VS ready
        stargate_ready = ai.structures(UnitTypeId.STARGATE).ready.exists
        if not stargate_ready:
            return False
        # 8 凤凰 ready
        phoenix_count = ai.units(UnitTypeId.PHOENIX).ready.amount
        return bool(phoenix_count >= 8)
