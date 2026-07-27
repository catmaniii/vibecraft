"""人族隐形女妖偷袭 plan。

速 VS + 女妖 + 隐形升级，骚扰对方矿线杀农民，打乱对方节奏争取经济差。
TvP / TvZ 早期偷袭开局。

Build order（策略 yaml 14-30 supply）：
  14 BS → 15 BB → 16 BR(气矿1) → 18 VF → 19 BR(气矿2) → 20 VS →
  21 TechLab@VS → 22 BansheeCloak(研究) → 24 Banshee×2 → 26 CC(二矿) →
  28 Marine × 4 → 轨道指挥中心升级

设计参考：strategies/terran/banshee_harass.yaml
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

from vibecraft.bot.auto_combat.harass_act import HarassWorkerLineAct
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class BansheeHarass(KnowledgeBot):  # type: ignore[misc]
    """隐形女妖偷袭开局：速 VS + TechLab + 隐形研究，出门骚扰对方矿线。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BansheeHarass")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：第一波女妖出来即转（≥1 Banshee ready + ≥6 Marine）。

        2026-07-18 用户原则「第一波出来就马上转型，占不占到便宜都转」：旧条件
        「隐形完成 + 2 女妖 + 6 兵」太苛刻——女妖产线被 priority 枪兵抢矿、整局只造
        出 1 架 → 条件永不满足 → 转型从不触发 → 卡在残废开局囤矿(实测 t=804 M=3848)。
        降到「第一波(1 女妖)出来即转」，让 bio_max 尽早接管、跑满全场经济。
        """
        banshees = ai.units(UnitTypeId.BANSHEE).ready.amount
        if banshees < 1:
            return False
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        return bool(marines >= 6)

    async def create_plan(self) -> BuildOrder:
        # 女妖是骚扰单位：关掉 attack_on_advantage（sharpy 默认「经济领先 + 军队
        # 劣势」就龟防，见 zone_attack.py _should_attack）。低阈值让 2-3 架女妖
        # 即出门偷矿线。
        attack = PlanZoneAttack(start_attack_power=24)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 补给自动化（不被建筑串行阻塞）
            AutoDepot(),
            # Marine 放建筑步**前面**抢资源（枪兵 50 矿；原来排在 VF/VS/Expand 后面，
            # 被它们的 reserve 把矿吃光、有兵营有矿却空转 → 早窗 prod_util 0.67，2026-06-16 用户）。
            # 女妖走 Starport 独立产线、隐形/坦克走 gas，与枪兵抢矿不冲突。
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # 农民生产：双矿阶梯——单矿上限 22，二矿好后继续补到 44
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # 早期 critical path（严守顺序）：depot → BB → 第一气矿
            # 其余建筑拆出去做并行 Step，避免一步卡全卡
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # BB 好 → VF 立即开建（并行，不依赖后续任何串行步骤）
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            # VF 好 → 第二气矿（VS 需要双气）
            Step(UnitReady(UnitTypeId.FACTORY, 1), BuildGas(2)),
            # VF 好 → VS 立即开建（科技前置：VS 需要 VF）
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            # ── bio 关键升级:vs VeryHard 0/3 深因——枪兵海无 stim/护盾/攻防被换死(比 hellion 还惨)──
            # BB2 + techlab 出 stim/护盾;EB×2 滚 +1/+1。枪兵有这些才敢和有升级的 AI 换血。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.SHIELDWALL)),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2), GridBuilding(UnitTypeId.ENGINEERINGBAY, 2)
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            ),
            # VS 好 → TechLab（女妖隐形升级前置；TechLab 需要 VS）
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            ),
            # TechLab 好 → 研究隐形（VS+TechLab 完成后立即开始，研究 ~110s）
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                Tech(UpgradeId.BANSHEECLOAK),
            ),
            # VS 好 → 持续出女妖（VS ready 就开始排队）
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.BANSHEE, 4)),
            # 骚扰同时开二矿（strategy step 26 supply）
            Step(UnitReady(UnitTypeId.STARPORT, 1), Expand(2)),
            # 二矿开好后继续补女妖骚扰产能
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                TerranUnit(UnitTypeId.BANSHEE, 8),
            ),
            # 主矿 + 二矿升轨道指挥中心
            MorphOrbitals(2),
            # （Marine 量产已上移到建筑步前面，见顶部 TerranUnit(MARINE, 60, priority)）
            # 家事 + 进攻（骚扰 / 防守 / 采矿）
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门（见 dt_drop_iac.py / gate4_pressure.py 注释）。
                # 军队成型后 skip 掉，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 8),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 女妖单独拉出去骚扰对方矿区点农民 —— 造出兵 ≠ 骚扰到位。
                # wait_upgrade=BANSHEECLOAK:隐形没研出来之前在家待命,不裸送。
                # 排在 Gather 之前:先 Reserved 掉女妖,Gather 就不会拉它们进主力。
                HarassWorkerLineAct(
                    {UnitTypeId.BANSHEE}, wait_upgrade=UpgradeId.BANSHEECLOAK, release_after=480
                ),
                PlanZoneGather(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
