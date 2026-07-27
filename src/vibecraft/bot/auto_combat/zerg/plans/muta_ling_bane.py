"""虫族飞龙小狗毒爆持续运营 plan。

飞龙 + 小狗 + 毒爆三件套高机动滚雪球：
- 飞龙（VS/刺翼）多线骚扰收割
- 小狗毒爆（BS/母池 + BB/妖虫巢）正面团战
- BV(进化腔)×2 滚飞行攻防 + 地面升级
- 靠机动性蚕食对方，克制地面机械

建筑 hotkey 备注：
  BH=孵化场  BS=母池  BV=进化腔  BB=妖虫巢  VS=刺翼
单位：飞龙 / 小狗 / 毒爆 / 女王

设计参考：strategies/zerg/muta_ling_bane.yaml
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
from sharpy.plans.acts.zerg import AutoOverLord, MorphLair, ZergUnit
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

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


def _make_attack_act() -> PlanZoneAttack:
    """主力 attack act:start_attack_power=35 + 关 attack_on_advantage。

    2026-05-27: 同 macro_hatch/terran mech-bc_late-bio_max 修复模式。
    zerg 后期 doctrine 经济强但军队中等,sharpy 默认 attack_on_advantage=True
    在"经济优势 + 军队劣势"时龟防累积不出门(实测 macro_hatch attack_moveout
    1179s → 426s 验证)。
    muta_ling_bane: 早期 zergling 22 power 即触发 start=20 太早出门(实测
    VeryEasy attack_moveout 165s 完全没等 mutalisk)。调到 35 让 8 飞龙 ×
    2.5 = 20 + zergling 6 power = 26 不够,凑 8 飞龙 + 18 ling = 38 才推。
    """
    attack = PlanZoneAttack(start_attack_power=35)
    attack.attack_on_advantage = False
    return attack


class MutaLingBane(KnowledgeBot):  # type: ignore[misc]
    """飞龙小狗毒爆：高机动三件套，飞龙多线骚扰 + 小狗毒爆正面。

    顺序段（sequential boot）只保留科技必须串行的最少步骤：
      BS → BB + BV×2 → VS（刺翼）
    扩张 / 农民 / 气矿全部移到并行段（BuildOrder），
    避免 SequentialList 的 ActUnit(DRONE) 堵住整条链。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft MutaLingBane")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：VS（刺翼）已完工 + ≥8 飞龙 + ≥12 小狗 → 通知 Director 切持续。

        三件套成型标准：
          刺翼 ready（飞龙前置落地）+ 8 飞龙（骚扰批次到位）+ 12 小狗（地面屏障）。
        """
        try:
            spire_ready = ai.structures(UnitTypeId.SPIRE).ready.exists
            muta = ai.units(UnitTypeId.MUTALISK).amount
            ling = ai.units(UnitTypeId.ZERGLING).amount
        except Exception:
            return False
        return bool(spire_ready and muta >= 8 and ling >= 12)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 飞龙小狗毒爆 build order ─────────────────────────────────────
            #
            # 重构说明：原版把 ActUnit(DRONE,22/28/40)、Expand(3/4/5)、
            # BuildGas(2/4) 全放 SequentialList，导致：
            #   1. ActUnit(DRONE) 串行阻塞—— DRONE 堆到上限才放行下一步；
            #   2. AutoOverLord 锁在末尾，霸主整局不自动造；
            #   3. 扩张 / 气矿排在科技前面，延误 VS 建造时间。
            # 修复策略：顺序段只保留科技链（BS → BB → BV×2 → VS），
            # 其余全进并行段，AutoOverLord 提前到并行段头。
            #
            BuildOrder(
                # ── 科技链（唯一必须串行的部分） ──────────────────────────────
                # BS=母池：飞龙 + 小狗 + 毒爆共同前置
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # BB=妖虫巢（小狗→毒爆变形）+ BV=进化腔×2（飞行攻防 + 地面升级）
                GridBuilding(UnitTypeId.BANELINGNEST, 1),
                GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
                # Lair=刺翼(Spire)的硬前置！2026-07-19 修复真机 bug：原版
                # 直接 GridBuilding(SPIRE) 没先升 Lair → Spire 永远放不下 →
                # 整局 MUTALISK=0(气囤到 1453 却造不出飞龙,ling_bane 转型自验暴露)。
                # Spire 的 tech 要求是 Lair,必须先 MorphLair。
                Step(UnitReady(UnitTypeId.SPAWNINGPOOL, 1), MorphLair()),
                # VS=刺翼（飞龙产线，需 Lair 完成前置）
                Step(UnitReady(UnitTypeId.LAIR, 1), GridBuilding(UnitTypeId.SPIRE, 1)),
                # ── 并行段：VS 完成后所有事同步推进 ──────────────────────────
                BuildOrder(
                    AutoOverLord(),  # 提前到并行段头，避免霸主整局不造
                    # 经济扩张（不阻塞科技链）
                    Expand(3),  # 三矿（含母矿）
                    Expand(4),  # 四矿
                    Expand(5),  # 五矿
                    BuildGas(2),  # 双气（早期）
                    BuildGas(4),  # 四气（支持持续飞龙产能）
                    # 农民 cap（并行拉人口，不卡科技顺序）
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 28),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 40),
                    # 女王：BS 完成后尽早造，注卵 + 血量
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 6),
                    ),
                    # 小狗速度 + 攻速（进化腔研）
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    Tech(UpgradeId.ZERGLINGATTACKSPEED),
                    # 毒爆速度（BB/妖虫巢研）
                    Step(
                        UnitReady(UnitTypeId.BANELINGNEST, 1),
                        Tech(UpgradeId.CENTRIFICALHOOKS),
                    ),
                    # 飞行武器 1/2/3（刺翼完成后开始）
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL1),
                    ),
                    Step(
                        UnitExists(UnitTypeId.MUTALISK, 6),
                        Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL2),
                    ),
                    Step(
                        UnitExists(UnitTypeId.MUTALISK, 10),
                        Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL3),
                    ),
                    # 飞行护甲 1/2/3
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        Tech(UpgradeId.ZERGFLYERARMORSLEVEL1),
                    ),
                    Step(
                        UnitExists(UnitTypeId.MUTALISK, 6),
                        Tech(UpgradeId.ZERGFLYERARMORSLEVEL2),
                    ),
                    Step(
                        UnitExists(UnitTypeId.MUTALISK, 10),
                        Tech(UpgradeId.ZERGFLYERARMORSLEVEL3),
                    ),
                    # 飞龙（刺翼完成后持续生产）
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.MUTALISK, 12),
                    ),
                    # 小狗持续出，妖虫巢完成后会自动变毒爆
                    ZergUnit(UnitTypeId.ZERGLING, 24),
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
                # 闲置农民兜底已全局化(2026-07-20):common_bot on_step 对所有 build 统一跑
                # rescue_idle_workers,不用再在 build plan 里挂 IdleWorkerToMineAct。
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                _make_attack_act(),
                PlanFinishEnemy(),
            ),
        )
