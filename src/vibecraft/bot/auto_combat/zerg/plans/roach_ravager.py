"""虫族蟑螂破坏者 timing plan。

3 矿蟑螂 + 破坏者：母池 → 蟑螂窝 → 双气 → 持续蟑螂 + Ravager morph +
提速狗，地面攻防 +1，~7:00 带 ~150 人口（10 蟑 + 10 破坏者 + 30 狗）出门。

设计参考：strategies/zerg/roach_ravager.yaml
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
from sharpy.plans.acts.zerg import AutoOverLord, ZergUnit
from sharpy.plans.require import UnitReady
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
    """蟑螂破坏者 attack:start_attack_power=22 + 关 attack_on_advantage。

    实测(vs VeryHard 1/4):裸 PlanZoneAttack() 门槛低 + attack_on_advantage=True → 军队没攒成
    就 piecemeal 送、attrition 输(同 ultralisk 0/3→5/6 修法)。提门槛让蟑螂破坏者成群再压。
    """
    attack = PlanZoneAttack(start_attack_power=22)
    attack.attack_on_advantage = False
    return attack


class RoachRavager(KnowledgeBot):  # type: ignore[misc]
    """蟑螂破坏者 timing：三矿暴兵 + Ravager morph，7:00 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft RoachRavager")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 蟑螂破坏者 build order
            SequentialList(
                # 早期关键建筑序列（不可分的串行前置）。
                # drone 13 是 SequentialList "起点":只占早期 larva,之后 BS/Expand/
                # ROACHWARREN/Gas 立刻接管。如果拉到 22 会锁死整个 SequentialList
                # (Queen 在 BuildOrder 内,没启动就没 inject → larva 不够 → drone
                # 永远到不了 22 → SequentialList 永远卡)。
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 13),
                BuildGas(1),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                Expand(2),  # 自然矿
                # BS 好后建蟑螂窝
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                BuildGas(2),
                # 阶段2：并行暴兵 + 科技 + 扩张
                BuildOrder(
                    AutoOverLord(),
                    # 三矿优先 —— 3 hatch 是本 build 核心,排第一拿资源优先级,
                    # 否则被女王/科技挤到 4 分多才落地(hatchery_3 验收过不了)。
                    Expand(3),
                    # 第 3 气矿 —— 3 矿 build 要够气供狗速 + glial + 攻防,
                    # 只 2 气会把 glial 饿到整局研不出来。
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        BuildGas(3),
                    ),
                    # 女王注卵(BS 完成即可,priority=True:不抢 larva,从 HATCHERY 训)
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2, priority=True),
                    ),
                    # 狗速 —— 需 BS 完成
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    ),
                    # 进化腔（升地面攻防）—— 排在农民爬坡前,早 ~8s 完成
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    # 农民爬坡到稳定产能(36 是 timing push 合理上限,3 矿部分饱和)。
                    # 实验过 drone 50 priority=True 抢 larva → 但导致 ROACH/ZERGLING
                    # 都出不来(zero-sum game)。timing push 不是 macro,drone 不该堆。
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),
                    # 注:不研蟑螂速(Glial)—— 它需 Lair 前置,而升 Lair 会占用主巢
                    # ~57s morph 时间、明显掉幼虫产能(实测 roach/zergling 数下降)。
                    # 这是 7 分一波 timing,牺牲 Lair 时间不值,保留一本快速暴兵。
                    # 地面导弹武器 +1（飞弹武器，蟑螂 / 破坏者 / 刺蛇共用）
                    Step(
                        UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                        Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),
                    ),
                    # 地面护甲 +1
                    Step(
                        UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                        Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                    ),
                    # 持续出蟑螂(目标 18 = 10 留场 + 8 morph 成 Ravager 的 buffer)。
                    # priority=True 抢 larva,确保蟑螂暴够 —— ling_bane 同样修法。
                    # 18 是因为 Ravager morph 会消耗 8 只蟑螂,剩下 10 才匹配
                    # spec roach_10 need>=10 @ 6:30。
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 18, priority=True),
                    ),
                    # Ravager morph(从已造蟑螂 morph,不 priority —— 让 ROACH 先暴够)。
                    # 实测如果 RAVAGER 也 priority,蟑螂还没 10 只就被 morph 走 8 只 →
                    # ROACH count 反而不够,触发 verifier roach_10 FAIL。
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.RAVAGER, 8),
                    ),
                    # 小狗持续补产(gate 在 BS ready;priority 不开 —— 蟑螂先 larva)。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 30),
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
