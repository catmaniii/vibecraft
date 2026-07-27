"""人族真善美一波 plan。

1 BB + 1 VF + 1 VS：枪兵 + 坦克 + 女妖 三件套单基地 all-in 一波 ——
速科技凑齐 Marine + SiegeTank + Banshee（含隐形），一波出门偷家。TvP 经典强势 timing。

Build order（供给数参考，不硬编码）：
  14 depot → 16 BB + 16 gas → 18 Reactor@BB → 19 VF → 20 2nd gas →
  21 TechLab@VF → 22 VS → VS 好即 TechLab@VS → 研隐形 + 坦克 + 女妖 + 枪兵
  BB2 / BB3 + Reactor → 50 人口加多兵营量产枪兵

注：真善美招牌的"拉农民修前线地堡"前压微操暂未实现，第 2 步 build_acceptance
调优时再补；当前为骨架版（凑三件套含隐形 + 出门）。

设计参考：strategies/terran/one_one_one.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.terran.siege_idle_tanks import SiegeIdleTanksAct


class OneOneOne(KnowledgeBot):  # type: ignore[misc]
    """真善美一波：1-1-1 速出 枪兵 + 坦克 + 女妖（隐形）单基地 all-in。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft OneOneOne")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：三件套 Tank + Banshee cloak + 三个建筑科技就位 + ≥8 Marine。

        简化：1 Tank ready + 1 Banshee ready + Banshee cloak 完成 + ≥8 Marine。
        """
        tanks = (
            ai.units(UnitTypeId.SIEGETANK).ready.amount
            + ai.units(UnitTypeId.SIEGETANKSIEGED).ready.amount
        )
        if tanks < 1:
            return False
        banshees = ai.units(UnitTypeId.BANSHEE).ready.amount
        if banshees < 1:
            return False
        cloak_done = UpgradeId.BANSHEECLOAK in ai.state.upgrades
        if not cloak_done:
            return False
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        return bool(marines >= 8)

    async def create_plan(self) -> BuildOrder:
        # 关掉 attack_on_advantage：1-1-1 all-in 必须按时出门，不能因经济劣势龟防。
        attack = PlanZoneAttack(start_attack_power=14)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 补给自动化（与所有 Step 并行，不被建筑串行阻塞）
            AutoDepot(),
            # 枪兵放建筑步**前面**抢资源（枪兵 50 矿；原来排在 VF/VS/多兵营后面，
            # 被它们的 reserve 把矿吃光、有兵营有矿却空转 → 早窗 prod_util 0.55，2026-06-16 用户）。
            # 一有兵营就填满产线；坦克/女妖走 gas（priority reserve），与枪兵抢矿不冲突。
            TerranUnit(UnitTypeId.MARINE, 50, priority=True),
            # 农民生产：单基地 all-in，约 19 封顶，停产全出单位
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 19),
            ],
            # 早期 critical path（严守顺序）：depot → BB → 双气矿（1→2 串行）
            # Gas1 + Gas2 都在关键路径里，保证早期双气矿到位；
            # VF 需要 100 gas，单气矿 gas 积累极慢，必须双矿才能打出 timing。
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
                BuildGas(2),
            ),
            # BB 一好：上 Reactor（双 Marine 产能）——
            # skip_until 2 Marines：保证 BB 开始产兵后才占用 36s Reactor 时间，
            # 不阻塞后续 VF 并行触发（Reactor 是独立 Step，不在 SequentialList 内）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
                skip_until=UnitExists(UnitTypeId.MARINE, 2),
            ),
            # BB 一好：VF（不等 Reactor，并行触发）
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            # VF 一好：立刻挂 TechLab（坦克前置，18s）
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # VF 一好：VS 并行开建（女妖前置）
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            # VS 一好：上 TechLab（女妖需要 StarportTechLab）
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            ),
            # StarportTechLab 一好：研女妖隐形（Cloaking Field，必须）——
            # 无隐形的女妖在 1-1-1 all-in 里是废的，会被炮塔/地面防空秒掉。
            # 研究时间 ~110s（约 1:50），和女妖建造时间 43s 接近，可并行。
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                Tech(UpgradeId.BANSHEECLOAK),
            ),
            # 矿多够了建第 2 兵营（50 人口后扩枪兵产能）——
            # 需要 BB1 好之后才建，满足 BB2 依赖关系。
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                GridBuilding(UnitTypeId.BARRACKS, 2),
                skip_until=UnitExists(UnitTypeId.FACTORY, 1),
            ),
            # BB2 上 Reactor（双产枪兵，提升 push 时枪兵数量）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            ),
            # 第 3 兵营（进一步提升枪兵产能，矿多时建）——
            # 单基地 all-in 矿紧，BB3 靠后建，等 VF+VS 科技链完成后余矿才够。
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                GridBuilding(UnitTypeId.BARRACKS, 3),
                skip_until=UnitExists(UnitTypeId.STARPORT, 1),
            ),
            # 单基地 all-in，只升主矿轨道指挥中心
            MorphOrbitals(1),
            # ── 三件套生产（各自独立 Step，并行触发）──
            # （枪兵量产已上移到建筑步前面，见顶部 TerranUnit(MARINE, 50, priority)）
            # 坦克：VF TechLab 一好即产（32s/辆，125 gas）——
            # priority=True：坦克是 1/1/1 push 核心，gas 紧张时优先 reserve gas，
            # 防止女妖（100 gas）抢走坦克的气。
            # 目标只 3 辆：单基地双气矿 gas 产能有限（~5.6 gas/s），坦克满 3 辆即
            # 满足 push 数量，is_done 后停止 reserve，把 gas 让给女妖。
            Step(
                UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
                TerranUnit(UnitTypeId.SIEGETANK, 3, priority=True),
            ),
            # 女妖：VS TechLab 一好即产，priority=True 与坦克并行抢 gas（43s/架）——
            # 原版无 priority，坦克 priority=True 把气全吃光，女妖等到 ~6:26 才出第 2 架
            # （实测只有 1 架在 6:00 窗口内）。
            # 改为 priority=True：女妖与坦克同优先级竞争 gas，双线并行产出；
            # 单基地双气矿 ~11.2 gas/s，足以同时供坦克(125gas/32s) + 女妖(100gas/43s)。
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                TerranUnit(UnitTypeId.BANSHEE, 3, priority=True),
            ),
            # 后段持续补 Marine（余矿 sink，多兵营后产能提升）
            TerranUnit(
                UnitTypeId.MARINE, 50
            ),  # 24→50: 桥接 opening→sustain 空窗(原 24 到顶停产兵营闲)
            # 家事 + 进攻
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门。军队成型后 skip 掉，让主力专心 all-in 出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 14),
                # min_gas=6：1/1/1 三件套对 gas 需求极高（坦克 125 + 女妖 100 + 隐形 100），
                # 强制双气矿满采（2×3=6）撑起科技+单位产线。
                DistributeWorkers(min_gas=6),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                SiegeIdleTanksAct(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
