"""虫族 12pool 开局 plan。

12 农民就开 BS（母池），gasless 变体：无气矿，极速出狗 cheese 压制对方早期扩张。

Pro 基准时间节点（Spawning Tool #153110 ZvZ / #166685 Lambo ZvP）：
  0:17 @ supply 12 → BS 开建
  1:43 @ ——       → BS 完成（建造约 65 s）
  1:04 @ supply 14 → 首批 6 叉子出门（BS 完成后幼虫积压即刻排队）
  1:05 @ supply 17 → Queen 开训（BS 完成后即刻）
  1:38 @ supply 21 → Hatchery 2（二矿）开建

关键设计原则：
  - 顺序段 SequentialList 仅含「12 农 → BS」两步，缩到最短。
    旧版把 Queen / Zergling(6) / Expand(2) 串进 SequentialList，
    导致 Queen 等 BS 完成（65s 阻塞）→ Zergling 等 Queen（50s 阻塞）
    → 首批 ling 145s+ 才出（pro 是 64s），二矿 ~180s 才开（pro ~100s）。
  - 并行段 BuildOrder：AutoOverLord / Queen / ZergUnit / Expand 同时活跃，
    资源到位谁先 reserve 谁先建，不再串行等待。
  - gasless：不建 BE（气矿），首攻靠数量和时机取胜（ZvZ 最常见变体）。
  - PlanZoneAttack(start_attack_power=6)：6 只叉子即出门，贴近 pro 1:04 timing。

建筑 hotkey 备注：
  BH=孵化场  BS=母池  BE=气矿（本 plan 不用）
单位：叉子/小狗(Zergling) / 女王(Queen)

设计参考：strategies/zerg/12pool.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase
from sharpy.plans.acts.zerg import AutoOverLord, ZergUnit
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


class TwelvePool(KnowledgeBot):  # type: ignore[misc]
    """12pool 速狗 cheese 开局（gasless 变体）。

    目标：首批 6 叉子 ~1:04 出门，二矿 ~1:38 开建，2:00–2:20 到对面主基地。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft 12pool")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：3:00 后 + 二矿落地 → 通知 Director 切持续 doctrine。

        12pool cheese 出 6 ling 后二矿落地表示已进入持续运营阶段。
        Director 收到信号 → 推荐 toast 转 persistent_roach_hydra_viper。

        2026-05-28: HATCHERY → townhalls(含 LAIR/HIVE)防御性修复。
        12pool cheese 通常不升 Lair,但若 cheese 没死 + 进入持续阶段会升,
        升级后 HATCHERY 数 -1 永远卡 < 2(同 macro_hatch bug)。
        """
        try:
            t = ai.time
            townhalls = ai.townhalls.amount
        except Exception:
            return False
        return bool(t >= 180 and townhalls >= 2)

    async def create_plan(self) -> BuildOrder:
        # cheese 语义：出门就死也要冲，跳过 sharpy power 比较（参考 gate4_pressure.py）。
        _atk = PlanZoneAttack(start_attack_power=6)
        _atk.force_attack = True
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 顺序段：仅保留硬顺序的两步 ──────────────────────────────────
            # Spawning Tool #153110 / #166685：supply 12 @ 0:17 → BS 开建。
            # 顺序段越短越好：之后的所有 act 放进并行 BuildOrder，
            # 让 Queen / ZergUnit / Expand 在 BS 完成那一刻同时解锁，
            # 不再互相等待。
            SequentialList(
                # 12 农：12pool 标志性节点，第 12 只 drone 出来后立刻建 BS。
                # （实战是第 12 只 drone 出去采矿的同时派一只去建 BS）
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 12),
                # BS @ supply 12，建造约 65s → 完成约 1:43。
                # gasless：不建 BE，节省 drone + 时间，首波叉子靠数量取胜。
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # ── 并行段：BS 完成后所有 act 同时竞争资源 ──────────────────
                # BuildOrder 内部每帧遍历所有 act，资源够谁先 reserve 谁先建，
                # 不再串行阻塞。
                BuildOrder(
                    # AutoOverLord：supply 不够时自动训练霸主，防卡人口。
                    # Spawning Tool #153110: 0:40 @ supply 14 → OL。
                    AutoOverLord(),
                    # Queen @ BS 完成即刻（pro ~1:05）。
                    # 用 Step(UnitReady BS) 做 gate：BS 完成前这条 act
                    # 每帧 return True（跳过）不阻塞其他 act。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                    ),
                    # Zergling 持续刷，不等 Queen。
                    # BS 完成后幼虫积压即刻排队 → 首批 6 只 ~1:04 出门。
                    # (旧版把 ZergUnit(6) 放 SequentialList：等 BS(65s) + 等
                    #  Queen(50s) = 145s+ 才出，现在去掉串行等待回到 pro timing。)
                    # 持续排到 30 只 —— 足够整局 cheese + 后续骚扰。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 30, priority=True),
                    ),
                    # Hatchery 2（二矿）@ supply 21 / ~1:38（pro timing）。
                    # 旧版放 SequentialList ZergUnit(6) 之后 → ~180s 才开。
                    # 现在并行：BS 完成 + 资源到位即开建，不等叉子出完。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        Expand(2),
                    ),
                    # 二矿落地后补第 2 只 Queen（注卵 + 防守）。
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                    ),
                    # 农民压到 18：cheese 早期不铺太多 drone。
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 18),
                    # ── Macro tail(2026-05-23 用户:中期持续运营)─────────────
                    # cheese 没赢 → fall back 到宏观运营,免得余矿堆积。
                    # 三矿 + 五气 + 70 农 + 4 蜂后,蟑螂窝(转 mid game)。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        GridBuilding(UnitTypeId.ROACHWARREN, 1),
                    ),
                    # gasless cheese 原则：蟑螂窝建好才需要气矿，压制阶段不建气。
                    Step(UnitReady(UnitTypeId.ROACHWARREN, 1), BuildGas(2)),
                    Step(UnitExists(UnitTypeId.HATCHERY, 2), Expand(3)),
                    Step(UnitReady(UnitTypeId.ROACHWARREN, 1), BuildGas(5)),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 4),
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 20),
                    ),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 70),
                ),
            ),
            # ── 家事段 + 出门骚扰 ─────────────────────────────────────────────
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
                # start_attack_power=6：6 只叉子即出门 cheese（pro ~1:04 timing）。
                # force_attack=True：cheese 语义，出门就死也要冲，跳过 sharpy power 比较。
                # 旧版用默认值（通常 20+），等太久才出门。
                _atk,
                PlanFinishEnemy(),
            ),
        )
