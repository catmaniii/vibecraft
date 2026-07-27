"""虫族雷兽流后期 plan。

雷兽 + 小狗 + 毒爆的地面碾压死阵：雷兽顶前排吃伤害，小狗毒爆贴脸，女王补血。
升满近战攻防，Hive 科技，正面无解。

科技链：BS(母池) → Lair → VI(感染坑) → Hive → VU(雷兽洞)
建筑：BH(孵化场)×5, BS(母池), BB(妖虫巢), BV(进化腔)×2, VI(感染坑), VU(雷兽洞)
升级：小狗速 / 肾上腺素 / 毒爆速 / 雷兽护甲+速 / 近战攻防 1-3

设计参考：strategies/zerg/ultralisk.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import (
    ActUnit,
    BuildGas,
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.zerg import AutoOverLord, MorphHive, MorphLair, ZergUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva


def _make_deathball_attack() -> PlanZoneAttack:
    """雷兽死阵 attack:高 start_attack_power + 关 attack_on_advantage。

    实测(macro_hatch→ultralisk vs VeryHard 打到 40 分仍输):裸 PlanZoneAttack() 门槛太低 +
    attack_on_advantage=True → 军队没攒成死阵就 piecemeal 送出去打、雷兽从没攒过 2 只、attrition 输。
    死阵要 5+ 雷兽(win_condition)才成立,故门槛拉到 34(高于 zvp 蟑螂的 22:雷兽死阵更贵更晚),
    先攒成再打,别零敲碎打喂对面。
    """
    attack = PlanZoneAttack(start_attack_power=34)
    attack.attack_on_advantage = False
    return attack


class Ultralisk(KnowledgeBot):  # type: ignore[misc]
    """雷兽流：雷兽 + 小狗 + 毒爆地面碾压死阵。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Ultralisk")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 雷兽流科技树 build order
            # BH(孵化场) → BS(母池) → Lair → VI(感染坑) → Hive → VU(雷兽洞)
            # BB(妖虫巢) + BV(进化腔)×2 并行推进
            #
            # 2026-07-10 结构冻结修复：这层原来是 SequentialList（阻塞）——小狗/
            # 毒爆/雷兽全塞进最后一个内嵌 BuildOrder，导致前中期零地面兵（要等
            # VU 雷兽洞完成，即整条 Hive 科技链走完才出第一只单位）。改 BuildOrder
            # （并行）：硬前置全部已用 Step(UnitReady(...)) 门控（Pool→MorphLair /
            # Pool→BB / Lair→VI / VI→MorphHive / Hive→VU，见下），并行化后继续生效，
            # 小狗（Pool 门控）/毒爆（BB 门控）现在能早出，不用等雷兽洞。
            BuildOrder(
                # 农民爬到 22，开双气
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                BuildGas(2),
                # BS(母池)：小狗 / 女王前置
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # Lair：孵化场形态升级（需 BS）。必须用 MorphLair —— Expand 是建
                # 新孵化场、不会 morph 已有的;不 morph Lair 则后续 VI/Hive 全卡死。
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # 三矿扩张
                Expand(3),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),
                # 四矿扩张
                Expand(4),
                BuildGas(4),
                # BB(妖虫巢)：毒爆前置
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.BANELINGNEST, 1),
                ),
                # BV(进化腔)×2：近战攻防
                Step(
                    UnitExists(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                ),
                # 五矿扩张
                Expand(5),
                BuildGas(6),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 48),
                # Hive 科技链：VI(感染坑) 需 Lair → Hive 需 VI → VU 需 Hive
                # VI(感染坑)：Hive 前置（需 Lair 完成）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.INFESTATIONPIT, 1),
                ),
                # Hive：Lair 形态升级（需 VI 感染坑）—— 必须用 MorphHive
                Step(
                    UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                    MorphHive(),
                ),
                # VU(雷兽洞)：雷兽前置（需 Hive）
                Step(
                    UnitReady(UnitTypeId.HIVE, 1),
                    GridBuilding(UnitTypeId.ULTRALISKCAVERN, 1),
                ),
                BuildOrder(
                    AutoOverLord(),
                    # 近战攻防 1-3（BV×2 双线滚）
                    Tech(UpgradeId.ZERGMELEEWEAPONSLEVEL1),
                    Tech(UpgradeId.ZERGMELEEWEAPONSLEVEL2),
                    Tech(UpgradeId.ZERGMELEEWEAPONSLEVEL3),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL2),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL3),
                    # 小狗速 + 肾上腺素
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    Tech(UpgradeId.ZERGLINGATTACKSPEED),
                    # 毒爆速（BB 研究）
                    Step(
                        UnitReady(UnitTypeId.BANELINGNEST, 1),
                        Tech(UpgradeId.CENTRIFICALHOOKS),
                    ),
                    # 雷兽护甲 +2（VU 研究）
                    Step(
                        UnitReady(UnitTypeId.ULTRALISKCAVERN, 1),
                        Tech(UpgradeId.CHITINOUSPLATING),
                    ),
                    # 雷兽速（VU 研究）
                    Step(
                        UnitReady(UnitTypeId.ULTRALISKCAVERN, 1),
                        Tech(UpgradeId.ANABOLICSYNTHESIS),
                    ),
                    # 女王注射保供应
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.QUEEN, 6),
                    ),
                    # 小狗:30→80。雷兽 gas 受限、矿/larva 富余无处去(larva_idle 2.76,F50),
                    # 小狗 mineral-only 当出口吸掉富余,顺带把死阵的小狗海拉满(思路仍是雷兽+狗+毒爆)。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 80),
                    ),
                    Step(
                        UnitReady(UnitTypeId.BANELINGNEST, 1),
                        ZergUnit(UnitTypeId.BANELING, 12),
                    ),
                    # VU 完成后持续暴雷兽
                    Step(
                        UnitReady(UnitTypeId.ULTRALISKCAVERN, 1),
                        ZergUnit(UnitTypeId.ULTRALISK, 6),
                    ),
                ),
            ),
            # 家事 + 进攻
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常对局每帧 return False（sharpy
                # 源码 zone_attack.py:123 "Blocks!"），SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                _make_deathball_attack(),
                PlanFinishEnemy(),
            ),
        )
