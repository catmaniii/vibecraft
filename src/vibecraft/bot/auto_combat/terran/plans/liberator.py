"""人族解放者空优持续运营 plan。

解放者 + 维京制空压制：VS×3-4 量产解放者架防区锁地面、维京吃制空，
女妖偷矿，医疗船救场；舰船攻防升级，靠解放者防区蚕食对方经济基地。

build 思路（VS=星港 / VF=工厂 / VA=军火库 / BB=兵营 / BC=指挥中心）：
  SCV 爬到 ~50；BuildGas 到 4；VF×1（VS 前置）；VS×3（主力量产）；
  VS1 接 Reactor 量产解放者/维京；VS2 接 TechLab 出女妖；VA×1（舰船升级）；
  BB×2（枪兵打底/防空）；Expand 到 3 矿；MorphOrbitals(3)；
  持续出解放者/维京/女妖/医疗船/枪兵；Tech 舰船攻击升级。

设计参考：strategies/terran/liberator.yaml
"""

from __future__ import annotations

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


class LiberatorSky(KnowledgeBot):  # type: ignore[misc]
    """解放者空优：VS×3 量产解放者+维京，女妖偷矿，舰船攻击升级，防区蚕食对方经济。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft LiberatorSky")

    async def create_plan(self) -> BuildOrder:
        # 解放者需要凑数量才形成有效防区；start_attack_power 设高一点，
        # 积累 4+ 解放者 + 维京再出门。attack_on_advantage=False 避免 sharpy
        # 在经济领先时因军队劣势龟缩（zone_attack.py _should_attack）。
        attack = PlanZoneAttack(start_attack_power=20)
        attack.attack_on_advantage = False

        return BuildOrder(
            # ── 补给自动化（顶层兄弟，不被任何串行阻塞） ──────────────────────
            AutoDepot(),
            # ── 农民 ramp：三矿三档阶梯（解放者/维京/女妖都吃气，农民量不能少）──
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 50),
                ),
            ],
            # ── 早期 critical path：depot → BB → 第 1 气矿 ──────────────────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── BB1 好 → VF（VS 唯一前置）+ 二矿 + 第 2 气矿 并行触发 ──────
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            # ── VF 好 → VS×1 立即建（VF 是 VS 唯一前置）─────────────────────
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            # ── VS1 好 → Reactor（双产解放者/维京）+ 第 3/4 气矿 ───────────
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            Step(UnitReady(UnitTypeId.STARPORT, 1), BuildGas(3)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), BuildGas(4)),
            # ── VS2（二矿稳住后扩产）+ VS2 接 TechLab（女妖隐形前置）────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.STARPORT, 2),
            ),
            Step(
                UnitReady(UnitTypeId.STARPORT, 2),
                BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 2),
            ),
            # ── VS3（三矿后再扩，接 Reactor 量产解放者）─────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.STARPORT, 3),
            ),
            Step(
                UnitReady(UnitTypeId.STARPORT, 3),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 3),
            ),
            # ── VA（舰船武器升级前置）────────────────────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.ARMORY, 1),
            ),
            # ── BB2（枪兵 / 防空底盘）────────────────────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 2),
            ),
            # ── 三矿 ─────────────────────────────────────────────────────────
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), Expand(3)),
            # ── 三 BC 升轨道指挥中心（MULE 供气矿/矿物）─────────────────────
            MorphOrbitals(3),
            # ── 女妖隐形研究（VS2 TechLab 好后立即开）──────────────────────
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                Tech(UpgradeId.BANSHEECLOAK),
            ),
            # ── 持续升级舰船攻防 1/2/3(2026-05-23 用户:doctrine 都要持续攻防)─
            # 解放者/维京/女妖都是 SHIP,吃 SHIP weapons + VEHICLEANDSHIP armor。
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL3),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3),
            ),
            # ── 空军单位生产（各独立，不互堵）──────────────────────────────
            # 解放者：主力，VS Reactor 双产；priority 确保优先占产能
            TerranUnit(UnitTypeId.LIBERATOR, 8, priority=True),
            # 维京：制空护卫；priority 防被低优先级单位挤掉
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 6, priority=True),
            # 女妖：偷矿骚扰；VS TechLab 线出
            Step(
                UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                TerranUnit(UnitTypeId.BANSHEE, 4),
            ),
            # 医疗船：救场/接走低血量解放者
            TerranUnit(UnitTypeId.MEDIVAC, 4, priority=True),
            # 枪兵：保家 + 防空地面部队偷袭
            TerranUnit(UnitTypeId.MARINE, 10),
            # ── 家事 + 进攻（tactics，顺序铁律：Attack 必须在 Gather 之后）──
            # 见 bc_late.py 注释：PlanZoneAttack.execute() 每帧 return False，
            # 排在它后面的 DistributeWorkers / Gather 整局不执行。
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # 空军成型前允许防守；army >= 16 后让主力专心出门
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 16),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                attack,
                PlanFinishEnemy(),
            ),
        )
