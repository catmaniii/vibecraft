"""人族战列巡洋舰后期 plan。

后期战巡 + 渡鸦控场：4-6 BC + Raven 干扰 + Ghost EMP。
靠 BC 超级武器 + Raven 干扰拿下后期资源战。

设计参考：strategies/terran/bc_late.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.terran.bc_raid_act import BcHomeRepairAct, GroupHarassAct


class BcLate(KnowledgeBot):  # type: ignore[misc]
    """战列巡洋舰后期：BC + Raven 控场制空。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BcLate")

    async def create_plan(self) -> BuildOrder:
        # PlanZoneAttack：关掉 attack_on_advantage 避免龟防，
        # 战巡流需要 4-6 BC 就位后主动出门控制空域
        attack = PlanZoneAttack(start_attack_power=18)
        attack.attack_on_advantage = False

        return BuildOrder(
            # 战巡科技 build order
            #
            # 2026-07-10 结构冻结修复：这层原来是 SequentialList（阻塞）——BC/渡鸦/
            # 幽灵/维京/攻防升级全塞进最后一个内嵌 BuildOrder，等 Expand(4)/双星港/
            # 幽灵学院/军火库×2 等经济建筑全部建完才开始出兵，科技链被冻住。改
            # BuildOrder（并行）：硬前置全部已用 Step(UnitReady(...)) 门控
            # （Starport→StarportTechLab→FusionCore→BC / 2×Armory→攻防，见下），
            # 并行化后继续生效，BC 该出的 timing 不再被经济建筑卡住。
            BuildOrder(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 36),
                BuildGas(4),
                Expand(4),  # 四矿后期经济
                GridBuilding(UnitTypeId.STARPORT, 2),
                GridBuilding(UnitTypeId.GHOSTACADEMY, 1),
                Step(
                    UnitReady(UnitTypeId.STARPORT, 1),
                    BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
                ),
                Step(
                    UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                    GridBuilding(UnitTypeId.FUSIONCORE, 1),
                ),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                # 攻防升级前置:2 Armory(ship weapons 用 ship armor 用,并行研究)
                GridBuilding(UnitTypeId.ARMORY, 2),
                BuildOrder(
                    AutoDepot(),
                    MorphOrbitals(4),
                    Step(
                        UnitReady(UnitTypeId.FUSIONCORE, 1),
                        TerranUnit(UnitTypeId.BATTLECRUISER, 6),
                    ),
                    TerranUnit(UnitTypeId.RAVEN, 3),
                    TerranUnit(UnitTypeId.GHOST, 4),
                    # VIKINGFIGHTER(空战形态)才是可训练 enum；裸 VIKING(id 1940)是
                    # 不可训练占位 enum(creation_ability=None)，TerranUnit 走到
                    # calculate_ability_cost 会 AssertionError 崩整局(2026-06-19 真局踩,
                    # 同 #534)。星港只能训 VIKINGFIGHTER。
                    TerranUnit(UnitTypeId.VIKINGFIGHTER, 4),
                    TerranUnit(UnitTypeId.MARINE, 8),
                    # 持续升级攻防 1/2/3(2026-05-23 用户:doctrine 都要持续攻防)。
                    # 战巡是 SHIP 单位:SHIP weapons 给 BC/Viking 攻击,
                    # VEHICLEANDSHIP armor 给 BC/Viking/Raven/Medivac/Banshee 等护甲。
                    Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1)),
                    Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL2)),
                    Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL3)),
                    Step(
                        UnitReady(UnitTypeId.ARMORY, 2),
                        Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
                    ),
                    Step(
                        UnitReady(UnitTypeId.ARMORY, 2),
                        Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
                    ),
                    Step(
                        UnitReady(UnitTypeId.ARMORY, 2),
                        Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3),
                    ),
                ),
            ),
            # 家事 + 进攻
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常对局每帧 return False（sharpy
                # 源码 zone_attack.py:123 "Blocks!"），SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 在家修理（non-blocking；#583 单一归属）
                BcHomeRepairAct(),
                # BC 骚扰微操执行器：读 bc_harass_groups，健康分状态机驱动整组 BC 骚扰敌矿。
                # group_harass claim 在 bc_rush 开局已建、auto-switch 到本 doctrine 后仍存活，
                # 这里必须有 act 接着调度，否则切 doctrine 后骚扰断档（#561）。
                GroupHarassAct(),
                attack,
                PlanFinishEnemy(),
            ),
        )
