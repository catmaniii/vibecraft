"""虫族小狗毒爆 1-base all-in plan(4:30 timing)。

2026-05-23 重写:用户反馈旧版 "狗出得太慢 / 二矿开得太早 / 农民太少"。
按 1-base bane bust 标准节奏(对应 Lowko ZvZ Timing Push + Liquipedia 2 Base
Ling/Bane Bust 的紧凑 1-base 变体)重写。**不开二矿 → 把矿全砸在 ling/bane 上**,
4:30 第一波 6 ling + 6 bane 直接攻门;没赢再 fallback Expand(2) + 持续 bane。

Pro 节奏对照(综合 Spawning Tool 14 pool all-in / Lowko ZvZ Timing Push):
  0:00  12 drone + 1 OL 起手
  0:25  14 BS(母池,继续 drone)
  0:30  14 BE(气矿,提速 + bane 都要气)
  1:30  BS 完 → Queen + ling speed + 6 ling 同时排
  1:35  17 BB(妖虫巢,bane 前置)
  2:30  BB 完 → 6 bane morph(需 8+ ling 在场,避免抢早期狗)
  3:25  ling speed 完(走对面)
  4:00  6 bane + 6-8 ling 集结中线
  4:30  attack 到对方家:bust wall + 杀农民
  没赢 → Expand(2) + 持续 bane(macro tail)

设计 vs 旧版的关键区别:
  ❌ 旧版:14 BS → 立刻 Expand(2) → BB → 4 bane → Lair → 12 bane
     问题:二矿吃掉 300 矿,BB 推后,bane 太晚,drone 永远停 14,Lair 在 all-in 节奏里没用
  ✅ 新版:14 BS + BE → drone 继续到 17 → ling priority + Queen + 提速 → BB → 6 bane
     all-in push 之后才考虑 Expand(2);Lair 移到 macro tail(没赢才升)

建筑 hotkey 备注:
  BH=孵化场  BS=母池  BE=气矿  BB=妖虫巢  BV=进化腔
单位:小狗(Zergling) / 毒爆(Baneling) / 女王(Queen)

设计参考:strategies/zerg/ling_bane.yaml
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
from vibecraft.bot.auto_combat.zerg.baneling_morph import ForwardBanelingZergUnit


class _BanelingMorph(ForwardBanelingZergUnit):  # type: ignore[misc]
    """小狗→毒爆 morph，毒爆前压+护蛹在前沿变。

    实现抽到共享模块 `zerg/baneling_morph.py`（开局 plan 与 build-aware sustain 共用）：
    护蛹 gate（≥6 ling + 中心 8 格内 ≥4 ling，防 2026-05-23 cocoon 裸死坑）+ 前压 gate
    （群推进过中点才变）+ 超时 latch 兜底（推不出去回退就地变）。
    """


class LingBane(KnowledgeBot):  # type: ignore[misc]
    """小狗毒爆 1-base 4:30 all-in:14 BS + BE → 6 ling + 6 bane → 4:30 attack。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft LingBane")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成:首波 6 bane + 8+ ling 集结到位 → 通知 Director 切持续。

        ling_bane 1-base 4:30 all-in 节奏:首攻在 BANELING ≥ 4 + ZERGLING ≥ 10
        时机已到。Director 收到信号 → 推荐 toast 转 persistent_muta_ling_bane
        (默认延续 ling/bane 风格)。Push 没赢由 macro tail 自动接管,玩家也可
        手动切 LLM 推荐的其它 doctrine。
        """
        try:
            bane = ai.units(UnitTypeId.BANELING).amount
            ling = ai.units(UnitTypeId.ZERGLING).amount
        except Exception:
            return False
        return bool(bane >= 4 and ling >= 10)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 1-base bane bust 4:30 all-in build order(2026-05-23 重写)──
            #
            # 用户原话三点(旧版全错):
            #   "狗出得太慢" → 旧版 ling 排末位 + 没 priority → larva 被抢
            #   "二矿开得太早" → 旧版 BS 完立刻 Expand(2) → 300 矿被吃 → bane 推后
            #   "农民太少" → 旧版 drone 停 14 → 一矿都采不饱
            #
            # 新设计(对照 Lowko ZvZ Timing Push / Spawning Tool 14 pool all-in):
            #   - 14 BS + 14 BE(双前置) → BS 完同时解锁 Queen / 提速 / ling / BB
            #   - drone 继续到 17(一矿满采 16 工 + 1 气矿 3 工 = ~17)
            #   - ling priority=True 抢 larva,首批 6 只 BS 完即出
            #   - BB 在 BS 完后立刻建(不等二矿)
            #   - 6 bane gate 在"有 ≥8 ling 在场"避免抢早期狗
            #   - **不开二矿** → 资源全砸在 ling/bane 上
            #   - 4:30 push 后 macro tail(Expand(2) + 持续 bane + 升 Lair)
            #
            SequentialList(
                # 14 农 → BS(0:25 开建,1:30 完成)
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 14),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # 1 气矿(提速 100/100,bane 25/25,都要气)
                BuildGas(1),
                # 阶段并行:BS 完成后 Queen / 提速 / ling / BB 同时解锁
                BuildOrder(
                    AutoOverLord(),
                    # ─── ling 排第一位 + priority=True ────────────────────
                    # 抢 larva + 抢矿。BS 完成后 larva 积压,priority 让 ling
                    # 在毒爆 morph / drone 之前先吃掉幼虫。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 20, priority=True),
                    ),
                    # Queen priority(BS 完即训,从 HATCHERY 出,不抢 larva)
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 1, priority=True),
                    ),
                    # 小狗提速(BS 研,100/100,~3:25 完成 → ling 走对面)
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    # BB(妖虫巢):BS 完成后立刻建(不等二矿)。
                    # 旧版排在 Expand(2) 之后 → bane 推迟。新版直接 BS 完即建。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        GridBuilding(UnitTypeId.BANELINGNEST, 1),
                    ),
                    # drone 继续到 17(一矿满采 + 1 气满工)。
                    # 用户原话"农民太少" → 不能停在 14。
                    # 排在 ling/Queen 之后:larva 先满足攻击单位,余下幼虫补 drone。
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 17),
                    # 首批 6 bane:gate 在"有 ≥8 ling 在场"。
                    # 关键:这是 all-in 的核心 timing,需要 6 bane + 6-8 ling 一起出门。
                    # 旧版 gate 只看 BB ready → 前 4 ling 立刻 morph,屏幕没狗。
                    Step(
                        lambda ai: (
                            ai.structures(UnitTypeId.BANELINGNEST).ready.exists
                            and ai.units(UnitTypeId.ZERGLING).amount >= 8
                        ),
                        _BanelingMorph(6),
                    ),
                    # ── 4:30 push 后 macro tail(没赢就转持续运营,免余矿堆积)────
                    # 触发条件:有 6 bane 在场(说明 4:30 push 已发起)。
                    # 之后才考虑 Expand(2) + Lair + 持续 bane,避免 all-in 节奏被打乱。
                    Step(
                        UnitExists(UnitTypeId.BANELING, 4),
                        Expand(2),
                    ),
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        BuildGas(2),
                    ),
                    # 二矿落地后第 2 蜂后
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                    ),
                    # 持续暴狗(macro 阶段大量补)
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        ZergUnit(UnitTypeId.ZERGLING, 40, priority=True),
                    ),
                    # 持续 bane(第二波 + 第三波)
                    Step(
                        lambda ai: (
                            ai.structures(UnitTypeId.HATCHERY).amount >= 2
                            and ai.units(UnitTypeId.ZERGLING).amount >= 12
                        ),
                        _BanelingMorph(12),
                    ),
                    # 升 Lair(转中期:Hive / 飞龙都要 Lair)
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        MorphLair(),
                    ),
                    # 毒爆速(LAIR 后才能研)
                    Step(
                        UnitReady(UnitTypeId.LAIR, 1),
                        Tech(UpgradeId.CENTRIFICALHOOKS),
                    ),
                    # 暴农 30(二矿满采) + 三矿
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 30),
                    Step(UnitExists(UnitTypeId.HATCHERY, 2), Expand(3)),
                    Step(UnitExists(UnitTypeId.HATCHERY, 3), BuildGas(4)),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 4),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 50),
                ),
            ),
            # 家事 + 进攻
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前:PlanZoneAttack.execute() 正常对局每帧 return False(sharpy
                # 源码 zone_attack.py:123 "Blocks!"),SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 1-base all-in:start_attack_power=8 → 6 ling + 2 bane (power ~8)
                # 即可出门,贴近 4:30 timing。
                PlanZoneAttack(start_attack_power=8),
                PlanFinishEnemy(),
            ),
        )
