"""人族 2BB 两矿 5:30 压制（2-1-1）plan。

标准 2-1-1：2 BB + 1 VF + 1 VS。双 BB(TechLab + Reactor) 双产 枪兵 + 掠夺者，
VF 挂 TechLab 出攻城坦克，VS Reactor 出 2 医疗船，
兴奋剂 + 战斗护盾 + 减速弹 研完 ~5:30 带 兵 + 坦克 出门压制。

坦克 timing（2-1-1 into tank push）：
  工厂约 2:30 完成 → 立刻挂 TechLab（约 3:10 好）→ 开始出攻城坦克。
  第 1 坦克约 3:50、第 2 约 4:40；~5:30 出门时带 2 坦克，
  配生化做 timing 压制，后续坦克接力支援阵地推进。

Build order：
  14 BS → 15 BB1 → 16 气矿 → BB1 好开二矿 + BB2 →
  BB1 TechLab（兴奋剂 / 战斗护盾 / 减速弹）+ BB2 Reactor →
  VF → VF TechLab（坦克）+ VS → VS Reactor（双 Medivac）→ ~5:30 出门

设计参考：strategies/terran/two_one_one.yaml
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


class TwoOneOne(KnowledgeBot):  # type: ignore[misc]
    """2BB 两矿 5:30 压制（2-1-1）：Marine + Marauder + 2 坦克 + 2 Medivac → ~5:30 出门。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft TwoOneOne")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：bio 2-1-1 三件套就位 + supply >= 40。

        条件：Stim 完成 + ≥1 Tank ready + ≥1 Medivac ready + supply_army >= 12。
        supply 兜底避免过早 trigger（兜底 5:30 = 330s）。
        """
        stim_done = UpgradeId.STIMPACK in ai.state.upgrades
        if not stim_done:
            return False
        tanks = (
            ai.units(UnitTypeId.SIEGETANK).ready.amount
            + ai.units(UnitTypeId.SIEGETANKSIEGED).ready.amount
        )
        if tanks < 1:
            return False
        medivacs = ai.units(UnitTypeId.MEDIVAC).ready.amount
        if medivacs < 1:
            return False
        return bool(ai.supply_army >= 12)

    async def create_plan(self) -> BuildOrder:
        # 关掉 attack_on_advantage：sharpy 默认「经济领先 + 军队劣势」龟防
        # （zone_attack.py _should_attack），2BB 早压是 timing 一波流，必须出门。
        # payload：Marine + Marauder + 2 攻城坦克 + 2 Medivac，start_attack_power=18
        attack = PlanZoneAttack(start_attack_power=18)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ---- 持续后台（不被 build 串行阻塞）----
            AutoDepot(),
            # Marine 放建筑步**前面**抢资源（枪兵 50 矿；原来排在 Expand/BB2/VF/VS 后面，
            # 被它们的 reserve 把矿吃光、有兵营有矿却空转 → 早窗 prod_util 0.65，2026-06-16 用户）。
            # 双兵营持续填满产能；坦克/掠夺者走 gas（priority reserve），与枪兵抢矿不冲突。
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # 农民 ramp：单矿 20 → 双矿 44（两阶段，对齐 economy_profile）
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 20),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # ---- 早期 critical path（严守顺序）: BS → BB1 → Refinery1 ----
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ---- 扩张：BB1 好即开二矿（Expand 必须排在 BB2 前）----
            # 试过 BB2 排 Expand 前：barracks_2 过了，但 Expand 被饿、
            # command_center_2 / factory / medivac 全崩（9/11 → 7/11）。Expand 优先更稳。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            # 双矿升轨道（CC1 + CC2 都升）
            MorphOrbitals(2),
            # ---- BB2：BB1 完成后触发。试过 UnitExists(BB1) 提前抢建 —— barracks_2 能过，
            # 但 BB2 早抢资源把 Expand / Factory / Medivac 一起拖崩（command_center_2 /
            # factory_1 / medivac_count 同时 0/3）。宁可 barracks_2 晚点，保经济 + 科技链。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            # ---- BB1 上 TechLab（用于 Stimpack 研究）----
            # BB2 放置即触发（UnitExists），不等 BB2 建完：BB2 放置 ~1:40，TechLab 放置 ~1:55
            # ready ~2:31，Stimpack 可在 2:31+gas 时开始，完成 2:31+110=4:21 < 5:15 spec
            Step(
                UnitExists(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            # ---- BB2 上 Reactor（双倍 Marine 产能）----
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            ),
            # ---- 三升级：各独立 Step，并行触发，不串行互堵 ----
            # Stimpack（~2:55 开始，~4:47 完成）
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # Combat Shield（掠夺者血量 +10，Marine +10）
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),
            ),
            # Concussive Shells（掠夺者减速大型 + 中型）
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.PUNISHERGRENADES),
            ),
            # ---- 第 2 气矿（VF 开建前需要；BB1 好即并行开）----
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            # ---- VF：BB2 放置即触发（UnitExists，不等 BB2 建完）----
            # 等 BB2 ready 才触发会让 VF 晚 ~46s（实测 factory 275s）。BB2 放置后矿够、
            # 且无同帧 3x3 抢位（TechLab 是 addon 不占格），VF 能提前 ~46s。
            Step(UnitExists(UnitTypeId.BARRACKS, 2), GridBuilding(UnitTypeId.FACTORY, 1)),
            # ---- VF TechLab：工厂一好就挂（攻城坦克前置）----
            # 工厂约 2:30 完成 → TechLab 约 3:10 好 → 坦克约 3:50 起
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # ---- VS（VF 一好即建；SC2 中 Starport 只需 Factory 存在，不需 addon）----
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                GridBuilding(UnitTypeId.STARPORT, 1),
            ),
            # ---- VS Reactor（VS 一好上 Reactor，双 Medivac 同时产）----
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            # ---- 军队生产（各自独立与 build 并行）----
            # （Marine 量产已上移到建筑步前面，见顶部 TerranUnit(MARINE, 60, priority)）
            # 掠夺者：BB1 TechLab 好即出，priority=True 预留气体（50气/个）
            # 上限提到 6（原 4 实测只出 3）：TechLab 产线有时被其他 gas 消费竞争，
            # 提高上限确保 spec 窗口内至少凑够 4 个。
            TerranUnit(UnitTypeId.MARAUDER, 6, priority=True),
            # 攻城坦克：VF TechLab 一好就出，priority=True 预留 125 气。
            # timing：第 1 坦克 ~3:50、第 2 ~4:40，~5:30 出门带 2 坦克。
            Step(
                UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
                TerranUnit(UnitTypeId.SIEGETANK, 2, priority=True),
            ),
            # 医疗船：VS Reactor ready 后双出
            Step(
                UnitReady(UnitTypeId.STARPORTREACTOR, 1),
                TerranUnit(UnitTypeId.MEDIVAC, 2),
            ),
            # ---- 家事 + 进攻 ----
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门（见 dt_drop_iac.py / gate4_pressure.py 注释）。
                # 军队成型后 skip 掉，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 14),
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
