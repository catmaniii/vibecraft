"""虫族蟑螂刺蛇毒蛇中后期通用 plan。

蟑螂顶肉 + 刺蛇火力 + 毒蛇拉扯：蟑螂顶前排承伤，刺蛇拉开射程持续输出，
毒蛇 Abduct 拉出对方关键单位进军队打死、绿水废掉对方远程火力。
虫族「我要一支能打的军队」中后期首选。

科技链：BS(母池) → BR(蟑螂窝) → Lair → VH(刺蛇巢) → VI(感染坑) → Hive
建筑：BH(孵化场)×4, BS(母池), BR(蟑螂窝), VH(刺蛇巢), BV(进化腔)×2, VI(感染坑)
升级：蟑螂速 / 刺蛇射程+速 / 远程攻击 1-3 / 地面护甲 1-3
单位：蟑螂×16, 刺蛇×16, 毒蛇×3（Hive 解锁）, 女王×6

设计参考：strategies/zerg/roach_hydra_viper.yaml
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


def _make_attack() -> PlanZoneAttack:
    """蟑刺毒蛇 attack:start_attack_power=28 + 关 attack_on_advantage(军队成群再打)。

    裸 PlanZoneAttack() → 蟑刺零敲碎打送、attrition 输(roach_allin→本 doctrine 转型 t=558
    团战 roach 16→1 崩就是这个;同 roach_ravager/ultralisk I31 修法)。蟑刺毒蛇军队更大,门槛 28。
    """
    attack = PlanZoneAttack(start_attack_power=28)
    attack.attack_on_advantage = False
    return attack


class RoachHydraViper(KnowledgeBot):  # type: ignore[misc]
    """蟑螂刺蛇毒蛇：蟑螂肉盾 + 刺蛇输出 + 毒蛇控场的中后期通用地面军队。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft RoachHydraViper")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 蟑螂刺蛇毒蛇科技树 build order
            # BH(孵化场) → BS(母池) → BR(蟑螂窝) → Lair → VH(刺蛇巢) → VI(感染坑) → Hive
            # BV(进化腔)×2 并行滚远程攻防；Hive 完成后毒蛇解锁
            #
            # 2026-07-10 结构冻结修复：这层原来是 SequentialList（阻塞）——军队/
            # 科技/女王全塞进最后一个内嵌 BuildOrder，导致蟑螂/刺蛇要等 MorphHive
            # 完成（整条链走完）才开始出兵，慢 3-4 分钟。改 BuildOrder（并行，不
            # 阻塞）：硬前置全部已用 Step(UnitReady(...)) 门控（Pool→BR /
            # Pool→MorphLair / Lair→VH / Lair→VI / VI→MorphHive，见下），并行化
            # 后这些门控继续生效，只是不再互相阻塞经济/军队生产。
            BuildOrder(
                # 农民爬到 22，开双气
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                BuildGas(2),
                # BS(母池)：女王 / 小狗前置
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # Lair：孵化场形态升级（需 BS），VH / VI / Hive 前置 —— 必须 MorphLair
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # 三矿扩张
                Expand(3),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 30),
                # BR(蟑螂窝)：蟑螂前置
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                # BV(进化腔)×2：远程攻防双线滚
                Step(
                    UnitExists(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                ),
                # 四矿扩张
                Expand(4),
                BuildGas(4),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 40),
                # VH(刺蛇巢)：刺蛇前置（真实前置是 Lair 完成,不是 RoachWarren）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.HYDRALISKDEN, 1),
                ),
                # VI(感染坑)：Hive 前置（需 Lair 完成）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.INFESTATIONPIT, 1),
                ),
                # Hive：Lair 形态升级（需 VI 感染坑），解锁毒蛇 —— 必须用 MorphHive
                Step(
                    UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                    MorphHive(),
                ),
                BuildOrder(
                    AutoOverLord(),
                    # 蟑螂速（BR 研究）
                    Tech(UpgradeId.GLIALRECONSTITUTION),
                    # 刺蛇射程 + 速（VH 研究）
                    Step(
                        UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                        Tech(UpgradeId.EVOLVEGROOVEDSPINES),
                    ),
                    Step(
                        UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                        Tech(UpgradeId.EVOLVEMUSCULARAUGMENTS),
                    ),
                    # 远程攻击 1-3（BV 研究）
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL2),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL3),
                    # 地面护甲 1-3（BV 研究）
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL2),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL3),
                    # 女王注射保供应
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.QUEEN, 6),
                    ),
                    # 蟑螂主力
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 16),
                    ),
                    # 刺蛇跟进
                    Step(
                        UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                        ZergUnit(UnitTypeId.HYDRALISK, 16),
                    ),
                    # 毒蛇：Hive 完成后解锁，持续出 3 条控场
                    Step(
                        UnitReady(UnitTypeId.HIVE, 1),
                        ZergUnit(UnitTypeId.VIPER, 3),
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
                _make_attack(),
                PlanFinishEnemy(),
            ),
        )
