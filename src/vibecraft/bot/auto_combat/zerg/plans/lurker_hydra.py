"""虫族潜伏者刺蛇持续运营 plan。

潜伏者埋地控场 + 刺蛇火力 + 毒蛇拉扯的阵地流：
潜伏者埋线封锁地面，刺蛇正面输出，毒蛇 abduct 拉关键单位 + 绿水降甲。

建筑 hotkey（注释用）：
  BH=孵化场(Hatchery)  BS=母池(SpawningPool)  BV=进化腔(EvolutionChamber)
  VH=刺蛇巢(HydraliskDen)  VD=潜伏者巢(LurkerDenMP)  VI=感染坑(InfestationPit)

设计参考：strategies/zerg/lurker_hydra.yaml
"""

from __future__ import annotations

from typing import Any

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

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


def _make_attack_act() -> PlanZoneAttack:
    """主力 attack act:start_attack_power=30 + 关 attack_on_advantage。

    2026-05-27: 同 macro_hatch/terran mech-bc_late-bio_max 修复模式。
    zerg 后期 doctrine 经济强但军队中等,sharpy 默认 attack_on_advantage=True
    在"经济优势 + 军队劣势"时龟防累积不出门(实测 macro_hatch attack_moveout
    1179s → 426s 验证)。
    lurker_hydra: 早期 zergling 凑 22 power 即触发 start=15 太早出门(实测
    VeryEasy attack_moveout 134s 完全没等 hydra/lurker)。调到 30 让 6 hydra
    (12 power) + 4 lurker (8 power) = 20 不够,凑齐 6 hydra + 2 lurker 才能
    满足 30 → 等主力到位才推。
    """
    attack = PlanZoneAttack(start_attack_power=30)
    attack.attack_on_advantage = False
    return attack


class LurkerHydra(KnowledgeBot):  # type: ignore[misc]
    """潜伏者刺蛇阵地流：潜伏者埋线封锁 + 刺蛇输出 + 毒蛇控场。

    顺序段（sequential boot）只包含必须串行的最少步骤：
      BS → MorphLair → VH → VD → VI → MorphHive
    其余 DRONE 补农 / Expand / BuildGas / 升级 / 出兵全部进并行段（BuildOrder），
    避免 SequentialList 强顺序卡死整条链。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft LurkerHydra")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：MorphHive 完成 + 8 潜伏者 ready → 通知 Director 切持续。"""
        try:
            hive_ready = ai.structures(UnitTypeId.HIVE).ready.exists
            lurker_count = ai.units(UnitTypeId.LURKERMP).amount
        except Exception:
            return False
        return bool(hive_ready and lurker_count >= 8)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 潜伏者刺蛇科技树 build order ────────────────────────────────────
            #
            # 旧版把 DRONE×3 串行 + Expand(3/4) + BuildGas 全塞 SequentialList，
            # ActUnit(DRONE) 阻塞 → 后续科技 step 全等 DRONE cap 达到才触发 →
            # 潜伏者 / 毒蛇永远出不来。
            #
            # 修复策略：顺序段瘦到"必须串行的科技链"，其余全进并行段。
            #
            BuildOrder(
                # BS（母池）：整条科技链起点，必须最早建
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # Lair：BS 完成后升级（解锁 VH / VD 前置）
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),
                ),
                # VH 刺蛇巢：Lair 完成后建
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.HYDRALISKDEN, 1),
                ),
                # VD 潜伏者巢：VH 完成后建（硬前置）
                Step(
                    UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                    GridBuilding(UnitTypeId.LURKERDENMP, 1),
                ),
                # VI 感染坑：Lair 完成后建（Hive 前置）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.INFESTATIONPIT, 1),
                ),
                # Hive：VI 完成后升级（解锁毒蛇）
                Step(
                    UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                    MorphHive(),
                ),
                # ── 并行段：科技链跑起来后所有 macro / 出兵同步推进 ──────────────
                BuildOrder(
                    AutoOverLord(),  # 提前防 supply cap，保证 DRONE 能造
                    # ── 经济并行 ────────────────────────────────────────────────
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 28),  # 早期农民 cap
                    BuildGas(2),  # 早期双气
                    Expand(3),  # 三矿扩张（macro，不 block 科技）
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),  # 三矿农民 cap
                    BuildGas(4),  # 四气支持持续刺蛇 + 毒蛇
                    Expand(4),  # 四矿后期经济
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 44),  # 四矿农民 cap
                    # BV×2 进化腔，滚远程攻防
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                    # ── 升级 ────────────────────────────────────────────────────
                    # 刺蛇升级：射程 + 速度
                    Tech(UpgradeId.EVOLVEGROOVEDSPINES),
                    Tech(UpgradeId.EVOLVEMUSCULARAUGMENTS),
                    # 潜伏者射程升级（VD 完成后）
                    Step(
                        UnitReady(UnitTypeId.LURKERDENMP, 1),
                        Tech(UpgradeId.LURKERRANGE),
                    ),
                    # 远程武器 1/2/3（刺蛇 + 潜伏者共用 missile）
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL2),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL3),
                    # 地面护甲 1/2/3
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL2),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL3),
                    # ── 出兵 ────────────────────────────────────────────────────
                    # 持续出刺蛇（VH 完成后）
                    Step(
                        UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                        ZergUnit(UnitTypeId.HYDRALISK, 20),
                    ),
                    # 持续出潜伏者（VD 完成后）
                    Step(
                        UnitReady(UnitTypeId.LURKERDENMP, 1),
                        ZergUnit(UnitTypeId.LURKERMP, 8),
                    ),
                    # 毒蛇（Hive + VI 完成后）
                    Step(
                        UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                        ZergUnit(UnitTypeId.VIPER, 3),
                    ),
                    # 女王持续补充
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 6),
                ),
            ),
            # ── 家事 + 进攻 ─────────────────────────────────────────────────
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
                _make_attack_act(),
                PlanFinishEnemy(),
            ),
        )
