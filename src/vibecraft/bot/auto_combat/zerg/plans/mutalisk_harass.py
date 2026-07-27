"""虫族飞龙骚扰中期 plan。

Railgan ZvT 2-Base Mutalisk Speed Bane 节奏（主流，最接近 pro 标准）。

关键 timing（Spawning Tool #66228）：
  17  BS（母池）
  17  二矿 Hatchery
  18  气矿 1
  ~   BS 完：Lair 升级、女王 ×2、狗速开研
  ~   双气（飞龙 100 气/只）
  3:05 Lair 完成
  4:24 Spire（Lair 完即建，造时 71 秒）
  5:23 妖虫速（防地面）
  6:05 首批 5 飞龙出门骚扰
  7:00-7:30 凑到 8-12 只

骚扰执行要点（hit-and-run）：
  飞龙 attack-cooldown 1.09s → 打一轮立刻后退，下次 CD 好了再进。
  目标优先级：工人 > 暴露部队 > 防御建筑。
  HarassWorkerLineAct 把飞龙锁定农民线，不会被主力 Gather 吸走。

设计参考：
  strategies/zerg/mutalisk_harass.yaml
  docs/plans/research/zerg_mutalisk_harass_research.md
  https://lotv.spawningtool.com/build/66228/
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

from vibecraft.bot.auto_combat.harass_act import HarassWorkerLineAct
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class MutaliskHarass(KnowledgeBot):  # type: ignore[misc]
    """飞龙骚扰中期：Railgan ZvT 2-Base Mutalisk Speed Bane 节奏。

    顺序段（sequential boot）只包含必须串行的最少步骤：
      17 农 → BS → 二矿 → 一气矿
    其余升级/出兵/科技全部进并行段（BuildOrder），
    避免 SequentialList 强顺序导致 Spire 整体慢 2-3 分钟。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft MutaliskHarass")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：首批 5+ 飞龙出门且刺翼（VS）已完工 → 通知 Director 切持续。

        MUTALISK ≥ 5 且 SPIRE ready：骚扰窗口到位，Director 推荐 toast
        转 persistent_brood_corruptor。
        """
        try:
            muta = ai.units(UnitTypeId.MUTALISK).amount
            spire_ready = ai.structures(UnitTypeId.SPIRE).ready.exists
        except Exception:
            return False
        return bool(muta >= 5 and spire_ready)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 飞龙骚扰 build order ──────────────────────────────────────────
            #
            # 原因：旧版把 BS→二矿→22 农→双气→Lair→Spire 全放 SequentialList，
            # 强顺序导致 Spire 到 8 分钟才开建，飞龙 9 分钟才出 → 骚扰窗口消失。
            # 重写策略：顺序段只保留 4 步开局"地基"，其余全部并行。
            #
            SequentialList(
                # 顺序段瘦身到 2 步:14 农 → BS。Expand/Gas 移到并行段,避免
                # SequentialList 卡 Expand(70s+) 期间没 BS → 防守空窗被 cheese 死。
                # (实测前一版 SequentialList(17农→Expand→Gas→BS) BS 完成晚到 ~185s,
                # 整局没建 BS 就被 VeryEasy AI 打死,0/14 PASS)
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 14),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),  # 14 BS,早建保防守
                # ── 并行段：BS 建完后所有事同步推进 ──────────────────────────
                BuildOrder(
                    AutoOverLord(),
                    Expand(2),  # 17 二矿(并行,不阻塞 BS)
                    BuildGas(1),  # 18 气矿 1
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 17),  # 自动补人口（防人口卡兵）
                    # ── 科技链 ──────────────────────────────────────────────
                    # Lair：BS 完成后立即升，是 Spire 的唯一前置。
                    # MorphLair 内部等 HATCHERY，用 UnitReady(BS) 作 gate 让
                    # 它精确到"BS 完成同帧"触发，不多浪费一帧。
                    # Lair 造时约 100s → 目标 3:05 完成（对应 17 BS @ 0:46）。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        MorphLair(),  # Lair 升级（Spire 前置，早升早到）
                    ),
                    # Spire（刺翼）：Lair 完成后立即开建。
                    # Spire 造时 71s → Lair 3:05 完 → Spire ~4:16 完（目标 4:24）。
                    # 这里用 UnitReady(LAIR) 而非 TechReady，因为 LAIR 是建筑
                    # 形态（MorphLair → LAIR），UnitReady 能检测到变形完成。
                    Step(
                        UnitReady(UnitTypeId.LAIR, 1),
                        GridBuilding(UnitTypeId.SPIRE, 1),  # 刺翼，飞龙前置
                    ),
                    # ── 防守升级 ────────────────────────────────────────────
                    # 狗速（ZERGLINGMOVEMENTSPEED）：BS 完成后研，两用途：
                    #   1. 小狗防地狱犬 / 早期骚扰（ZvT 必需）
                    #   2. Railgan build 在 2:20 出门前完成
                    # 注：此处不研妖虫速（CENTRIFICALHOOKS），飞龙开局不需要妖虫压。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),  # 狗速（防守用）
                    ),
                    # ── 出兵 ────────────────────────────────────────────────
                    # 女王 ×2：BS 完成后立即训练，注卵 + 双矿注卵加速幼虫。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                    ),
                    # 小狗 ×6（防守屏障）：3:30 前必须有 8+ 只小狗在本阵
                    # 防地狱犬 / 海盗船骚扰（Railgan 备注）。
                    # 6 只是轻量防守配比，不抢飞龙气矿。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 6),  # 防守小狗
                    ),
                    # ── 经济 ────────────────────────────────────────────────
                    # 双气：飞龙 100 气/只，6 只需 600 气，单气产能不够。
                    # 双气后约 60s 可积累首批气。Railgan 在 3:21 开三四气矿，
                    # 此处 BuildGas(2) 在 BS 并行段尽早触发，对应 ~1:30 附近。
                    BuildGas(2),  # 双气（飞龙 100 气/只，双气是最低要求）
                    # ── 主力出兵 ────────────────────────────────────────────
                    # 飞龙：Spire 完成后立即暴，目标 6:05 首批 5 只出门。
                    # ZergUnit(MUTALISK, 12) 持续造到 12 只（含第二波 7:00-7:30）。
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.MUTALISK, 12),  # 飞龙（骚扰主力）
                    ),
                    # 飞龙攻防升级(Spire 研):vs VeryHard 0/3 深因之一——裸飞龙被有升级的 AI 防空秒。
                    # +1/+1 飞攻飞甲大幅提飞龙群换血效率(配合下方 release_after 攒成军队一起打)。
                    Step(UnitReady(UnitTypeId.SPIRE, 1), Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL1)),
                    Step(UnitReady(UnitTypeId.SPIRE, 1), Tech(UpgradeId.ZERGFLYERARMORSLEVEL1)),
                    # 农民 cap：飞龙开局经济不过度铺，30 农是双矿合理上限。
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 30),
                    # 女王补充到 3 只（三矿后需要第三只注卵）。
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 3),
                    # 后续地面军 ×16：飞龙骚扰 + 小狗 push 双线威胁。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 16),  # 后续地面军
                    ),
                    # ── Macro tail(2026-05-23 用户:中期持续运营)─────────────
                    # 飞龙骚扰打完 → 不能停手转 macro,否则余矿堆积。
                    # 三矿 + 暴农 70 + 5 蜂后 + 持续兵(刺翼出 Corruptor / 出更多飞龙)。
                    Step(UnitExists(UnitTypeId.HATCHERY, 2), Expand(3)),
                    BuildGas(4),  # 多气支持持续飞龙 + 后续科技
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 5),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 70),
                    # 持续暴兵(刺翼 ready 后无脑刷):
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.MUTALISK, 30),  # 持续飞龙暴到 30
                    ),
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 60),  # 持续小狗
                    ),
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
                # 飞龙骚扰：HarassWorkerLineAct 把飞龙锁定打对方农民线，
                # hit-and-run 节奏（attack → 后撤 → cooldown → 再进），
                # 排在 PlanZoneGather 之前，防止 Gather 把飞龙收进主力集合。
                HarassWorkerLineAct({UnitTypeId.MUTALISK}, release_after=450),
                PlanZoneGather(),
                # start_attack_power=6：首批 5-6 只飞龙凑够即出门（6:05 窗口），
                # 不等凑到 12 才走，缩短骚扰延误。
                PlanZoneAttack(start_attack_power=18),
                PlanFinishEnemy(),
            ),
        )
