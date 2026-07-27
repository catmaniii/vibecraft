"""人族双基地坦克压制 plan。

双矿坦克 + 枪兵阵地战：坦克展开防守线 + Marine 保护坦克，逐步推进对方。
对付蟑螂 / 不朽的核心手段。

设计参考：strategies/terran/two_base_tanks.yaml

build order：
  depot → BB → 气矿1 → (BB ready) → Expand + Factory1 + 气矿2
  → Factory1 TechLab + BB2 → BB TechLab + Factory2
  → BB2 Reactor + EngineeringBay + Starport + StarportReactor → MorphOrbitals(2)
  出门编制：6 坦克 + 12 兵 + 2 医疗船 + 4 Viking，9:00-11:00 推进
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
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


class TwoBaseTanks(KnowledgeBot):  # type: ignore[misc]
    """双基地坦克压制：坦克阵地战 + Marine 护卫推进。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft TwoBaseTanks")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥2 Tank ready + ≥12 Marine + 二矿稳（2 CC）。"""
        tanks = (
            ai.units(UnitTypeId.SIEGETANK).ready.amount
            + ai.units(UnitTypeId.SIEGETANKSIEGED).ready.amount
        )
        if tanks < 2:
            return False
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        if marines < 12:
            return False
        cc = (
            ai.structures(UnitTypeId.COMMANDCENTER).amount
            + ai.structures(UnitTypeId.ORBITALCOMMAND).amount
            + ai.structures(UnitTypeId.PLANETARYFORTRESS).amount
        )
        return bool(cc >= 2)

    async def create_plan(self) -> BuildOrder:
        # 2026-07-19 用户拍板：two_base_tanks 走「纯 macro = 坦克防守 + 开矿，别进攻」。
        # 打开 attack_on_advantage（sharpy 默认龟防：只在经济+军队领先时才出门，否则
        # 缩家展开坦克线防守），配 Expand(3)/(4) 多矿运营 → 靠 out-macro + 对面撞死在
        # 坦克线上、领先够大再一波清。旧版 attack_on_advantage=False 的强出门 timing
        # push（枪兵球正面崩、重建慢）实测只 2/6，改防守运营。start_attack_power 拉到
        # 60：即便算出优势，也要攒够一大坨（坦克海 + 枪兵）再动，不零敲碎打。
        attack = PlanZoneAttack(start_attack_power=60)
        attack.attack_on_advantage = True
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 补给自动化（与串行 build 并行，不被建筑串行阻塞）
            AutoDepot(),
            # Marine 放建筑步**前面**抢资源（枪兵 50 矿；排 expand/兵营/工厂/add-on 后会被它们
            # reserve 把矿吃光、兵营空转 → 早窗 prod_util 低，2026-06-16 用户）。一有兵营就填满产线。
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # 农民生产（多基地阶梯：单矿 22 → 二矿 50 → 三/四矿 70，防守 macro 要更多农民）
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 50),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 70),
                ),
            ],
            # 早期 critical path（严守顺序）：depot → BB → 气矿
            # BB 一好才能建 Factory；缺少这段是原版 0/16 的根因
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # BB 放置（exists）即立刻开扩：UnitReady 触发时 Expand+Factory1 同帧竞争
            # 400M+150M reserve，CC 建造被 Factory SCV 延迟导致 command_center_2 失败。
            # UnitExists 触发：BB SCV 放置后立即开扩，CC 早 ~50s 完成，落入 spec 窗口。
            Step(UnitExists(UnitTypeId.BARRACKS, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            # 防守 macro：坦克线稳住后持续开矿（三矿 ~2 坦克成型时，四矿 ~4 坦克时）。
            # 别进攻 → 用扩张把经济滚起来，多矿 = 更多枪兵/坦克产能 + 后期一波质变。
            Step(UnitExists(UnitTypeId.SIEGETANK, 2), Expand(3)),
            Step(UnitExists(UnitTypeId.SIEGETANK, 4), Expand(4)),
            # 三/四矿气矿（坦克/维京是气兵，多矿要配气才产得动）
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), BuildGas(4)),
            # Factory1 一好：第 3 气矿（坦克125气+Stim100气消耗大，双矿不够）
            Step(UnitReady(UnitTypeId.FACTORY, 1), BuildGas(3)),
            # Factory1 一好：上 TechLab（坦克前置）
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # Factory1 一好：第 2 兵营（strategy spec: barracks=2）
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            # 双 Factory 是本 build 核心（spec: factory=2 by 5:00）
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.FACTORY, 2)),
            # Factory2 也挂 TechLab（双 Factory 双出坦克，tank_count_2 实测只 1 辆）
            Step(
                UnitReady(UnitTypeId.FACTORY, 2),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
            ),
            # 兵营 TechLab → 研 Stim（spec: STIMPACK by 6:30）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # 战斗护盾:枪兵 45→55hp。vs VeryHard 0/3 补:有 stim/+1/+1 但缺护盾,枪兵被换死。
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),
            ),
            # BB2 一好：上 Reactor（双兵营双产 Marine，spec: barracks=2 双产）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            ),
            # BB2 一好：建工程湾（步兵攻防升级前置）
            Step(UnitReady(UnitTypeId.BARRACKS, 2), GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)),
            # 工程湾一好：步兵攻击 +1
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
            ),
            # 工程湾一好：步兵护甲 +1
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            ),
            # Factory2 一好：建 Starport（spec: starport=1 by 5:30）
            Step(
                UnitReady(UnitTypeId.FACTORY, 2),
                GridBuilding(UnitTypeId.STARPORT, 1),
            ),
            # Starport 上 Reactor（双倍 Viking / Medivac 产能）
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            # 多矿轨道指挥中心（MULE 持续回矿；防守 macro 多基地都升）
            MorphOrbitals(3),
            # 单位生产
            # 坦克：priority=True 预留 125 气，防止气被 Viking/Medivac 抢走
            # 防守 macro 上限拉到 16：坦克海是防线核心，多矿气够撑，越多防线越硬。
            TerranUnit(UnitTypeId.SIEGETANK, 16, priority=True),
            # Marine：多兵营持续生产。防守 macro 上限拉到 60 —— 坦克/维京是气兵，
            # 多矿富余矿物靠枪兵吸，避免后期囤矿数千。
            TerranUnit(UnitTypeId.MARINE, 60),
            # Medivac：priority=True 确保 Medivac 在 Viking 前优先消耗 Starport
            TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True),
            # Viking：等 Medivac 目标满足后接力 Starport 产能
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 6),
            # 家事 + 进攻
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门（见 dt_drop_iac.py / gate4_pressure.py 注释）。
                # 军队成型后 skip 掉，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 24),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                SiegeIdleTanksAct(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
