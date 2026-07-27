"""人族生化满升级 persistent doctrine plan。

MMM 生化海开满升级：6-8 BB 量产枪兵 + 叉子，2 BE 滚步兵攻防 3/3，
VG 出幽灵 EMP / 狙杀，医疗船补给，兴奋剂 A 上去打。

build order 思路：
  - SCV 爬 ~60；BuildGas；Expand 到 4 矿
  - 8 BB（4 TechLab 出叉子 / 4 Reactor 量产枪兵）
  - 2 BE 滚步兵攻防 3/3（需 Armory 解锁 2/3 级）
  - VG（幽灵学院）→ Ghost；2 VS 接 Reactor 出医疗船
  - 三件 bio 升级：Stimpack / ShieldWall / PunisherGrenades
  - 持续出 Marine / Marauder / Medivac / Ghost（Marauder 先占 TechLab 产能）

⚠️ Marauder TerranUnit 排 Marine 前：否则 Marine（上限 40）铺满兵营订单饿死 Marauder。

建筑缩写：BB=兵营 BE=工程湾 VG=幽灵学院 VS=星港 BC=指挥中心
单位中文：枪兵 / 叉子 / 医疗船 / 幽灵

设计参考：strategies/terran/bio_max.yaml
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


class BioMax(KnowledgeBot):  # type: ignore[misc]
    """生化满升级：8 BB MMM + 步兵攻防 3/3 + 幽灵 EMP。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BioMax")

    async def create_plan(self) -> BuildOrder:
        # PlanZoneAttack：关掉 attack_on_advantage 避免龟防，
        # bio_max 需要满升 MMM 就位后主动兴奋剂推进
        attack = PlanZoneAttack(start_attack_power=20)
        attack.attack_on_advantage = False

        return BuildOrder(
            # ── 补给自动化（顶层兄弟，不被任何串行阻塞） ─────────────────────
            AutoDepot(),
            # ── SCV 四矿阶梯（~60 人口上限） ────────────────────────────────
            # 一矿 → 22；二矿好 → 44；三矿好 → 52；四矿好 → 60
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 52),
                ),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 4),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60),
                ),
            ],
            # ── 早期 critical path（存粹串行，保住 BB ~1:20 完成） ──────────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── BB1 好：快扩 + Factory + 第 2/3 气矿并行 ───────────────────
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(3)),
            # ── 轨道指挥中心（四矿各升 Orbital，持续呼 MULE） ────────────────
            MorphOrbitals(4),
            # ── BB 扩产能到 8（满 doctrine 要求） ──────────────────────────
            # BB1 ready → BB2；二矿后 → BB3/BB4；三矿后 → BB5/BB6；四矿后 → BB7/BB8
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.BARRACKS, 4),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.BARRACKS, 5),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.BARRACKS, 6),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 4),
                GridBuilding(UnitTypeId.BARRACKS, 7),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 4),
                GridBuilding(UnitTypeId.BARRACKS, 8),
            ),
            # ── 三矿 / 四矿扩张 ─────────────────────────────────────────────
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), Expand(3)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), Expand(4)),
            # ── BB 插件：BB1/BB2/BB3/BB4 → TechLab（叉子 / 升级产线）────────
            # BB5/BB6/BB7/BB8 → Reactor（量产枪兵，双产）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 2),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 3),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 4),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 4),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 5),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 5),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 6),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 6),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 7),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 7),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 8),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 8),
            ),
            # ── Factory + TechLab（VS 的前置；不出坦克，仅用来解锁 VS） ───────
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            ),
            # ── Starport × 2（Factory 一好就建第一个） ──────────────────────
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                GridBuilding(UnitTypeId.STARPORT, 1),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.STARPORT, 2),
            ),
            # ── VS 接 Reactor（每个 VS 双产医疗船） ──────────────────────────
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            ),
            Step(
                UnitReady(UnitTypeId.STARPORT, 2),
                BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2),
            ),
            # ── 工程湾 × 2（步兵攻防 3/3 双线研究）──────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            ),
            # ── Armory（步兵攻防 2/3 级前置） ───────────────────────────────
            Step(
                UnitReady(UnitTypeId.ENGINEERINGBAY, 1),
                GridBuilding(UnitTypeId.ARMORY, 1),
            ),
            # ── VG 幽灵学院（Ghost EMP / 核弹前置） ─────────────────────────
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                GridBuilding(UnitTypeId.GHOSTACADEMY, 1),
            ),
            # ── 第 4 气矿（四矿满气，支撑 Ghost + Medivac + 3/3 升级） ────────
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 4), BuildGas(4)),
            # ── 三件 bio 升级（各独立 Step，互不阻塞） ──────────────────────
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.SHIELDWALL),  # Combat Shield
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.PUNISHERGRENADES),  # Concussive Shells for Marauder
            ),
            # ── 步兵攻击 3/3（工程湾 +1 攻防；Armory 解锁 +2/+3） ──────────
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
            # ── 单位生产 ────────────────────────────────────────────────────
            # Marauder 排 Marine 前：先占 TechLab BB 产能，否则 Marine 把订单铺满饿死叉子
            TerranUnit(UnitTypeId.MARAUDER, 16, priority=True),
            # Marine：先出少量让 BB1 有空间建 TechLab，TechLab 好后持续出到目标上限
            [
                TerranUnit(UnitTypeId.MARINE, 4),
                Step(
                    UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                    TerranUnit(UnitTypeId.MARINE, 40),
                ),
            ],
            # 医疗船：priority 保住 VS 产能不被其他占走
            TerranUnit(UnitTypeId.MEDIVAC, 8, priority=True),
            # 幽灵：VG 建好后持续出，priority 保住气矿
            Step(
                UnitReady(UnitTypeId.GHOSTACADEMY, 1),
                TerranUnit(UnitTypeId.GHOST, 4, priority=True),
            ),
            # ── 家事 + 进攻 ─────────────────────────────────────────────────
            # 铁律：DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack 之前
            # PlanZoneAttack.execute() 每帧 return False（"Blocks!"），排后面的 act 整局不执行
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                attack,
                PlanFinishEnemy(),
            ),
        )
