"""人族幽灵核弹持续 doctrine plan。

幽灵主导生化体系：VG 量产幽灵 EMP 清魔法 + 持续核弹骚扰矿区。
枪兵叉子打底，医疗船补给；BB×6 保产能，双 BE 滚步兵攻防到 3/3。

建筑缩写（注释用）：
  BB = Barracks / 兵营      VG = GhostAcademy / 幽灵学院
  BE = EngineeringBay / 工程湾  VS = Starport / 星港
  BC = CommandCenter / 指挥中心

设计参考：strategies/terran/ghost_nuke.yaml
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

from vibecraft.bot.auto_combat.terran.nuke_act import AutoNukeAct


class GhostNuke(KnowledgeBot):  # type: ignore[misc]
    """幽灵核弹持续 doctrine：EMP + 核弹骚扰 + MMM 主力生化体系。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft GhostNuke")

    async def create_plan(self) -> BuildOrder:
        # PlanZoneAttack：关掉 attack_on_advantage 避免龟防，
        # 幽灵流需要主动出门 EMP + 核弹骚扰
        attack = PlanZoneAttack(start_attack_power=22)
        attack.attack_on_advantage = False

        return BuildOrder(
            # ── 补给自动化（顶层兄弟，不被串行阻塞） ─────────────────────────────
            AutoDepot(),
            # ── 农民 ramp：四矿四档阶梯 ──────────────────────────────────────────
            # 一矿 → 22；二矿好 → 44；三矿好 → 55；四矿好 → 66
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 55),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 4),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 66),
                ),
            ],
            # ── 早期 critical path：depot → BB → 气矿 ─────────────────────────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── BB1 一好：快扩（二矿）+ Factory + 第2/3气矿 并行触发 ──────────
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(3)),
            # ── 四矿扩张 ────────────────────────────────────────────────────────
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), Expand(3)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), Expand(4)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), BuildGas(4)),
            # ── Orbital 升级（四矿各升，持续呼 MULE） ────────────────────────
            MorphOrbitals(4),
            # ── BB 扩产能：BB2 → BB6（幽灵 + 叉子 + 枪兵需要大产能） ─────────
            # BB1 ready 后补 BB2；二矿好后补 BB3；三矿好后 BB4/BB5；四矿好后 BB6
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.BARRACKS, 4),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.BARRACKS, 5),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 4),
                GridBuilding(UnitTypeId.BARRACKS, 6),
            ),
            # ── BB1/BB2 接 TechLab（幽灵 / 叉子前置，双线产幽灵） ──────────────
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 2),
            ),
            # ── BB3/BB4/BB5/BB6 接 Reactor（量产枪兵） ────────────────────────
            Step(
                UnitReady(UnitTypeId.BARRACKS, 3),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 4),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 5),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 5),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 6),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 6),
            ),
            # ── Factory 接 TechLab（坦克支援 optional，主要给 gas 用幽灵） ───────
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # ── VS×2（Factory 一好即建，医疗船产线）────────────────────────────
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                GridBuilding(UnitTypeId.STARPORT, 1),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.STARPORT, 2),
            ),
            # ── VG × 1（幽灵前置，三矿后建） ────────────────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.GHOSTACADEMY, 1),
            ),
            # ── 双 BE（步兵攻防前置） ────────────────────────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            ),
            # ── Armory（步兵 +2/+3 升级前置）────────────────────────────────────
            # 2026-06-18 #548 审计发现:下方步兵 +2/+3 升级门控 UnitReady(ARMORY,1),
            # 但原 plan 从没建 Armory → +2/+3 永不研、卡在 +1。补上 Armory（三矿后建）。
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.ARMORY, 1),
            ),
            # ── 核心升级（各独立 Step，不互堵） ──────────────────────────────────
            # Stimpack（兴奋剂）+ ShieldWall（战斗护盾）：BB TechLab 一好即研
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),
            ),
            # PersonalCloaking：幽灵隐身，VG 一好即研
            Step(
                UnitReady(UnitTypeId.GHOSTACADEMY, 1),
                Tech(UpgradeId.PERSONALCLOAKING),
            ),
            # ── 步兵攻击升级（BE 好后持续升） ────────────────────────────────────
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL3),
            ),
            Step(
                UnitReady(UnitTypeId.ARMORY, 1),
                Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL3),
            ),
            # ── VS 接 Reactor（双产医疗船） ───────────────────────────────────────
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            Step(
                UnitReady(UnitTypeId.STARPORT, 2),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2),
            ),
            # ── 单位生产 ─────────────────────────────────────────────────────────
            # Ghost / Marauder 排在 Marine 前：先占 TechLab 产能。
            # 否则 Marine（上限大）会铺满兵营订单，幽灵被饿死。
            TerranUnit(UnitTypeId.GHOST, 8, priority=True),
            TerranUnit(UnitTypeId.MARAUDER, 10, priority=True),
            # Marine：先出 4 个让 BB1 有空档建 TechLab，TechLab 好后持续出
            [
                TerranUnit(UnitTypeId.MARINE, 4),
                Step(
                    UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                    TerranUnit(UnitTypeId.MARINE, 30),
                ),
            ],
            # 医疗船：priority 确保 VS 优先出医疗船
            TerranUnit(UnitTypeId.MEDIVAC, 6, priority=True),
            # ── 家事 + 进攻（tactics 段） ─────────────────────────────────────────
            # 铁律：DistributeWorkers / SpeedMining / PlanZoneGather 必须排在
            # PlanZoneAttack 之前 —— ZoneAttack.execute() 每帧 return False（"Blocks!"），
            # 放中间会 block 掉后面所有 act。
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 24),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                AutoNukeAct(),
                attack,
                PlanFinishEnemy(),
            ),
        )
