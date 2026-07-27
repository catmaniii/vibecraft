"""虫族蟑螂刺蛇中期 plan。

三矿 +1/+1 Roach Hydra timing（ZvP 主流）：
  17 BH 先于 BS → 单气矿开局 → 三矿经济 → BR → Lair → 蟑螂速
  → 双 BV 同步 +1 攻/+1 甲 → VH → 刺蛇射程 → 7:30-7:45 出门压制。

目标阵容（7:45 出门）：28 蟑螂 + 9-14 刺蛇 + 监察者，150+ supply。
升级状态：蟑螂速 ✓ / +1 攻 ✓ / +1 甲 ✓ / 刺蛇射程 ✓。

升级优先级（ZvP）：
  蟑螂速 > +1 攻 > 刺蛇射程 > +1 甲 > 刺蛇速（ZvP 不研刺蛇速）

建筑 hotkey 备注：
  BH=孵化场  BS=母池  BE=气矿  BR=蟑螂窝  BV=进化腔  VH=刺蛇巢

综合 timing 速查（Spawning Tool #46015 / #158017 / #122388）：
  0:55   17 BH（自然扩张，先于 BS）
  1:05   第一 BE（气矿）
  1:21   BS（母池）
  2:13   代谢加速（小狗速，防守用）
  3:00   第三 BH（三矿）
  3:36   BR（蟑螂窝）
  4:00   BE ×2（共三口气）
  4:10   Lair
  4:30   Glial Reconstitution（蟑螂速）+ 开始出蟑螂
  5:15   BV ×2（双进化腔）
  5:20   +1 地面攻击 + +1 地面护甲（同步双腔并研）
  5:30   VH（刺蛇巢）
  5:38   BE ×2（共五口气）
  5:50   Grooved Spines（刺蛇射程）
  6:00   混产蟑螂+刺蛇（约 2:1）
  7:45   出门——150+ supply，≈28 蟑螂 + 12 刺蛇 + 监察者

设计参考：
  strategies/zerg/roach_hydra.yaml
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


class RoachHydra(KnowledgeBot):  # type: ignore[misc]
    """蟑螂刺蛇中期：三矿 +1/+1 timing 7:30-7:45 出门压制。

    开局走 Hatch First（17 BH 先于 BS）——现代 ZvP 标准路线，
    三矿经济基础是高气矿 Roach Hydra 体系的必要条件。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft RoachHydra")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：28 蟑螂 + 12 刺蛇出门阵容到位 → 通知 Director 切持续。

        7:45 timing push 阵容到位（HYDRALISK≥9 且 ROACH≥20）即触发。
        Director 收到信号 → 推荐 toast 转 persistent_brood_corruptor。
        """
        try:
            hydra = ai.units(UnitTypeId.HYDRALISK).amount
            roach = ai.units(UnitTypeId.ROACH).amount
        except Exception:
            return False
        return bool(hydra >= 9 and roach >= 20)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 顺序段：Hatch First 开局骨架 ──────────────────────────────────
            # 严格顺序建链，确保 BS 前置 / 气矿节奏正确。
            # SequentialList 阻塞直到每个 step 完成，适合硬性前置依赖。
            SequentialList(
                # 17 农后立刻建二矿（Hatch First——先于 BS）
                # 参考：Spawning Tool #46015 / PiG B2GM #158017
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 17),
                Expand(2),  # 0:55  17 BH（自然扩张）
                BuildGas(1),  # 1:05  第一 BE（气矿）
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),  # 1:21  BS（母池）
                # BS 完成后解除顺序段，交给并行 BuildOrder 接管
            ),
            # ── 并行段：科技链 + 暴兵 + 经济 ────────────────────────────────
            # BuildOrder 每帧对所有 act 并行推进，适合多条并行科技线。
            # Step(condition, act) —— 满足条件才激活，不满足时跳过（不阻塞）。
            BuildOrder(
                # 兵力自动补充（女王 / 运输 Overlord）
                AutoOverLord(),
                # ── 女王 ──────────────────────────────────────────────────────
                # BS 完成即训练，最终维持 5 只覆盖三矿注卵
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 3),
                ),
                # ── 防守用升级 ────────────────────────────────────────────────
                # 小狗速（代谢加速）约 2:13 拿，让小狗能防追猎 / 防 DT
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                ),
                # ── 早期防守小狗 ──────────────────────────────────────────────
                # 6 只小狗作开局防守缓冲，不影响气矿用于科技
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ZergUnit(UnitTypeId.ZERGLING, 6),
                ),
                # ── 三矿（3:00）────────────────────────────────────────────────
                # research 指出：三矿应在确认对手占二矿后落地，约 3:00
                # 放在 BS 完成前不依赖任何 condition，靠 Expand 内部资源判断
                Expand(3),  # 3:00  第三 BH
                # ── 蟑螂窝 BR（3:36）─────────────────────────────────────────
                # BS 是硬前置（SC2 引擎层面），Step 确保不会提前发指令
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),  # 3:36  BR
                ),
                # ── 第二 / 三口气矿（4:00，共 3 口）─────────────────────────
                # BR 完成前后补第 2-3 口气，供 Lair + 蟑螂速 + 蟑螂生产
                Step(
                    UnitExists(UnitTypeId.ROACHWARREN, 1),  # 开始建 BR 即触发
                    BuildGas(3),  # 4:00  共三口气矿
                ),
                # ── Lair（4:10）──────────────────────────────────────────────
                # VH 硬前置，越早越好；BS 完成即可升
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    MorphLair(),  # 4:10  Lair
                ),
                # ── 蟑螂速 Glial Reconstitution（4:30）───────────────────────
                # 升级优先级最高（ZvP）：Lair 完成后立刻研，BR 同时完成
                # 参考：research 文档「升级优先级 ZvP #2」
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    Tech(UpgradeId.GLIALRECONSTITUTION),  # 4:30  蟑螂速
                ),
                # ── 双进化腔 BV ×2（5:15）───────────────────────────────────
                # 双腔并研 +1 攻 / +1 甲，节省约 60s 研究时间
                # 两个孵化场以上即可建，Lair 完成后通常已满足
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),  # 5:15  BV ×2
                ),
                # ── +1 地面攻击（5:20）──────────────────────────────────────
                # 升级优先级 ZvP #3：蟑螂速研完后立刻开
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 1),
                    Tech(UpgradeId.ZERGMISSILEWEAPONSLEVEL1),  # 5:20  +1 攻
                ),
                # ── +1 地面护甲（5:20 同步）─────────────────────────────────
                # 双腔并研，第二口腔研护甲；ZvP #5（与攻击同步）
                Step(
                    UnitReady(UnitTypeId.EVOLUTIONCHAMBER, 2),
                    Tech(UpgradeId.ZERGGROUNDARMORSLEVEL1),  # 5:20  +1 甲
                ),
                # ── 刺蛇巢 VH（5:30）────────────────────────────────────────
                # Lair 硬前置；Lair 完成约 1 分钟后建（≈5:28-5:30）
                Step(
                    UnitReady(UnitTypeId.LAIR, 1),
                    GridBuilding(UnitTypeId.HYDRALISKDEN, 1),  # 5:30  VH
                ),
                # ── 第四 / 五口气矿（5:38，共 5 口）─────────────────────────
                # VH 完成后刺蛇产线大量吃气（刺蛇 50/100），补至 5 口
                Step(
                    UnitExists(UnitTypeId.HYDRALISKDEN, 1),  # 开始建 VH 即触发
                    BuildGas(5),  # 5:38  共五口气矿
                ),
                # ── 刺蛇射程 Grooved Spines（5:50）─────────────────────────
                # 升级优先级 ZvP #4：让刺蛇在保持距离时持续输出，对追猎/巨像价值高
                # 注：ZvP 不研刺蛇速（Muscular Augments），ZvT 才需要
                Step(
                    UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                    Tech(UpgradeId.EVOLVEGROOVEDSPINES),  # 5:50  刺蛇射程
                ),
                # ── 蟑螂生产（BR 完成后立刻出，目标 28 只）────────────────
                # BR 完成 ≈ 4:30-4:50，先出 8-10 只防守 + 积累至出门 28 只
                # 不能在 VH 完成前把气全部烧在蟑螂上（刺蛇断气）
                Step(
                    UnitReady(UnitTypeId.ROACHWARREN, 1),
                    ZergUnit(UnitTypeId.ROACH, 28),  # 4:30  开始出蟑螂
                ),
                # ── 刺蛇生产（VH 完成后，目标 12 只）────────────────────────
                # VH 完成 ≈ 6:00；与蟑螂约 2:1 比例混产至出门
                # 出门阵容目标：28 蟑螂 + 9-14 刺蛇
                Step(
                    UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                    ZergUnit(UnitTypeId.HYDRALISK, 12),  # 6:00  开始出刺蛇
                ),
                # ── 三矿宏观农民（目标 ≈60 只，2.5 矿饱和）─────────────────
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 60),
                # ── 女王扩编（三矿后维持 5 只注卵）──────────────────────────
                ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 5),
            ),
            # ── 家事段：注卵 / 防御 / 进攻 ──────────────────────────────────
            # SequentialList 保证 PlanZoneAttack 在 DistributeWorkers / SpeedMining
            # / PlanZoneGather 之后——否则 PlanZoneAttack.execute() 每帧返回 False
            # 会阻断后续 act（sharpy zone_attack.py 源码确认）。
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # start_attack_power=20：约 28 蟑螂 + 12 刺蛇（power≈20+）满足后出门
                # 对应 7:30-7:45 timing，150+ supply
                # 参考：research 「出门 timing 和主力阵容 ZvP 三矿标准型」
                PlanZoneAttack(start_attack_power=20),
                PlanFinishEnemy(),
            ),
        )
