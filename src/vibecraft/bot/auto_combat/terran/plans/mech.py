"""人族机械化阵地 plan。

坦克 + 雷神 + 雷车 + 维京的机械化流：
  - VF（工厂）×3 各接 FACTORYTECHLAB，持续出坦克 + 雷神
  - VA（军火库）×2 滚机械武器 / 护甲攻防升级
  - VS（星港）×1 出维京制空护卫
  - 三矿 MorphOrbitals(3) 经济
  - 坦克架炮线步步推进，地雷埋守，阵地正面无解

设计参考：strategies/terran/mech.yaml
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

from vibecraft.bot.auto_combat.terran.siege_idle_tanks import SiegeIdleTanksAct


class Mech(KnowledgeBot):  # type: ignore[misc]
    """机械化阵地：坦克 + 雷神 + 雷车 + 维京，双 VA 攻防，三矿滚机械阵地流。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Mech")

    async def create_plan(self) -> BuildOrder:
        # PlanZoneAttack：关掉 attack_on_advantage 避免龟防，
        # 机械流需要 8 坦克 + 维京就位后主动推进阵地
        attack = PlanZoneAttack(start_attack_power=24)
        attack.attack_on_advantage = False

        return BuildOrder(
            # ── 补给自动化（顶层兄弟，不被串行阻塞） ──────────────────────────
            AutoDepot(),
            # ── 机械科技 build order（并行，Step/引擎自身门控保证前置顺序） ────
            #
            # 2026-07-10 结构冻结修复：这层原来是 SequentialList（阻塞）——5 座
            # 工厂/2 座军火库/星港/三矿扩张/50 农民 ramp 全部塞在同一条链里，
            # TechLab 加装（VF1/VF2/VF3）是链上最后 3 项，只要前面任一步（比如第
            # 5 座工厂）因经济紧张迟迟建不出来，坦克/雷神生产就全冻住（它们的
            # `Step(UnitReady(FACTORYTECHLAB, N), ...)` 门在顶层，但 TechLab 加装
            # 本身出不来）。改 BuildOrder（并行）：Terran 建筑的 SC2 引擎前置链
            # （BC→BB→VF→VA/VS→VC）本就由 sharpy `GridBuilding.prequisite_progress()`
            # 内部自动等待（不受并行/串行影响），TechLab Step 门控保留不动，攻防/
            # 出兵各自独立 Step 也保留不动 —— 并行化后科技链不再被经济建筑卡死。
            BuildOrder(
                # 农民 ramp 到 ~50
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 24),
                # 早建 BC（指挥中心）+ 气矿前置
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),  # BB 是 VS（星港）前置
                BuildGas(2),  # 两气矿：坦克/雷神需要气
                Expand(2),  # 二矿经济
                # VF（工厂）× 5：VF1-3 接 TechLab 出坦克/雷神，VF4/5 裸厂狂出火车(矿口)。
                # 5 厂是治"supply 卡 178 / 余钱 5000+"的关键：3 厂造到兵种上限就停、产能吞不掉钱。
                GridBuilding(UnitTypeId.FACTORY, 1),
                # VA（军火库）提前到 VF1 之后：早升机械攻防（用户要求"早升攻防"）。
                GridBuilding(UnitTypeId.ARMORY, 1),
                GridBuilding(UnitTypeId.FACTORY, 2),
                GridBuilding(UnitTypeId.FACTORY, 3),
                GridBuilding(UnitTypeId.FACTORY, 4),
                GridBuilding(UnitTypeId.FACTORY, 5),
                # VA2：双 VA 同时滚 LEVEL2/3 攻防
                GridBuilding(UnitTypeId.ARMORY, 2),
                # VS（星港）× 1：出维京制空
                GridBuilding(UnitTypeId.STARPORT, 1),
                # 三矿经济 + 气矿扩充
                BuildGas(4),
                Expand(3),
                # 农民继续爬
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 50),
                # VF1 接 TechLab（出坦克 / 雷神）
                Step(
                    UnitReady(UnitTypeId.FACTORY, 1),
                    BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
                ),
                # VF2 接 TechLab
                Step(
                    UnitReady(UnitTypeId.FACTORY, 2),
                    BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
                ),
                # VF3 接 TechLab
                Step(
                    UnitReady(UnitTypeId.FACTORY, 3),
                    BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
                ),
            ),
            # ── 升级（各独立 Step，不串行互堵） ─────────────────────────────
            # 持续升级机械攻防 1/2/3(2026-05-23 用户:doctrine 都要持续攻防)。
            # 坦克 / 雷神 / 雷车 / 维京都吃 VEHICLE weapons + VEHICLEANDSHIP armor。
            # 第 2 个 Armory(LEVEL2/3 前置)— LEVEL2 需 1 个 Armory ready,但
            # 实际 SC2 引擎只看 Armory 是否完成,不分第几个。这里 gate 都 ARMORY 1 ready 即可。
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL3),
            ),
            # 机械护甲升级（需 VA）
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
            # ── 升轨道指挥中心（三矿持续呼 MULE） ────────────────────────────
            MorphOrbitals(3),
            # ── 单位生产（各独立，不互堵） ──────────────────────────────────
            # 雷神：排在坦克**前面**第一钱口（用户要求"多出雷神"）。priority + 列首 → 先抢气，
            # 否则坦克(也 priority)先把气抢光、雷神只出 3 个、气还浮 4000+。雷神从 Factory 出
            # (需 Armory 已建,无需 TechLab) → VF4/5 裸厂也能出。3→12 priority 列首
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                TerranUnit(UnitTypeId.THOR, 12, priority=True),
            ),
            # 坦克：VF TechLab 好后持续出（主力，气矿口）。8→14
            Step(
                UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
                TerranUnit(UnitTypeId.SIEGETANK, 14, priority=True),
            ),
            # 火车（雷车）：VF 一好即可出，无 TechLab 前置（用户要求"火车"；纯矿口，吞矿填人口）。
            # 6→12（不再 24 —— 留人口给雷神/坦克这些气钱口，否则火车独占 200 人口、气花不掉）。
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                TerranUnit(UnitTypeId.HELLION, 12),
            ),
            # 地雷：VF TechLab 后出（埋守坡道）。4→6
            Step(
                UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
                TerranUnit(UnitTypeId.WIDOWMINE, 6),
            ),
            # 维京：VS 一好出（制空护卫）。6→8
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                TerranUnit(UnitTypeId.VIKINGFIGHTER, 8),
            ),
            # ── 家事 + 进攻（tactics SequentialList）────────────────────────
            # ⚠️ 铁律：DistributeWorkers / SpeedMining / PlanZoneGather 必须排在
            # PlanZoneAttack 之前 —— PlanZoneAttack.execute() 正常对局每帧
            # return False（sharpy zone_attack.py "Blocks!"），放在后面的 act
            # 整局不执行。照抄 bc_late.py 第 66-81 行顺序。
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                SiegeIdleTanksAct(),
                attack,
                PlanFinishEnemy(),
            ),
        )
