"""人族枪兵全力速攻 plan。

5 BB all-in：3:20 第一波 ~14 枪兵出门，不开矿，无转型。
打不死就输——这是 all-in 的本质，接受它。

设计参考：strategies/terran/marine_rush.yaml

Build order（supply → action）：
  10 BS（补给站）→ 12 BB1（兵营）→ 13 BR（气矿，仅为 Stim）
  BB1 完成 → TechLab 附加
  TechLab 完成 → Stim 研究（100s，~3:00 完成）
  BB1 good → BB2（尽早，目标 ~1:50）
  BB2 good → BB3（Reactor）
  BB3 good → BB4
  BB4 good → BB5
  BB2-BB5 各上 Reactor（双产枪兵）
  主矿升轨道指挥中心 → 呼 MULE
  ~20 SCV 停产，全力出枪兵
  第一波 ~14 枪兵（supply_army ~10）出门

Note on parallel structure:
  - AutoDepot 顶层兄弟，补给不卡
  - SCV ramp 顶层列表：单矿 all-in，20 封顶
  - 早期 critical path SequentialList（仅 depot→BB1→气矿 3 步，短）
  - BB1 完成后各建筑/科技 = 各自独立顶层 Step（并行触发，互不等待）
  - BB2-BB5 各自独立 Step，矿够即触发
  - TerranUnit 顶层兄弟（5 BB 持续出枪，Reactor 双倍产能）
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class MarineRush(KnowledgeBot):  # type: ignore[misc]
    """5 BB 枪兵全力速攻：3:20 第一波 ~14 枪兵出门，no-expand all-in。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft MarineRush")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥8 Marine 就位 或兜底 ai.time >= 200（all-in 不超 3:20）。"""
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        if marines >= 8:
            return True
        return bool(ai.time >= 200.0)

    async def create_plan(self) -> BuildOrder:
        # 低阈值出门：~6 供给军队即发动（原 8 实测 263s 出门超出 spec 240s 上界 23s）
        # 降到 6：约等于 3 个 Marine 供给，BB1 第 2-3 波兵出来即触发出门。
        attack = PlanZoneAttack(start_attack_power=6)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 补给自动化（顶层兄弟，不被任何串行阻塞）──────────────────────
            AutoDepot(),
            # ── 枪兵放建筑步**前面**抢资源（枪兵 50 矿）──────────────────────
            # 原来 MARINE 排在 5 兵营 + Reactor 后面，被它们的 reserve 把矿吃光、
            # 有兵营有矿却空转 → 早窗 prod_util 0.54（2026-06-16 用户）。一有兵营就填满产线。
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # ── 农民 ramp：单矿 all-in，~20 封顶，矿全投兵营+枪兵 ──────────
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 20),
            ],
            # ── 早期 critical path（3 步短串行，快速解锁 BB1）──────────────────
            # depot → BB1 → 气矿（仅 1 个，为 Stim 储备 100 气）
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),  # 10 BS
                GridBuilding(UnitTypeId.BARRACKS, 1),  # 12 BB1
                BuildGas(1),  # 13 BR（仅 Stim 用）
            ),
            # ── BB1 完成后：TechLab 附加（独立顶层 Step，并行触发）────────────
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            # ── TechLab 完成后：Stim 研究（独立顶层 Step，不串行等待）─────────
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # ── BB2-BB5：BB1 一好就解锁，GridBuilding 按余矿连续铺满 ──────────
            # 5 rax all-in 的命脉是兵营尽早铺满。不要串行等每个 BB ready
            # （那样 BB5 要拖到 ~4:30，赶不上 3:20 出门）；交给
            # GridBuilding(BARRACKS, 5) 一次性按矿 + 空闲 SCV 连续放置。
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                GridBuilding(UnitTypeId.BARRACKS, 5),
            ),
            # ── BB2-BB5 各上 Reactor（双产枪兵，1 个 Reactor 约 50s 建好）────
            Step(
                UnitReady(UnitTypeId.BARRACKS, 2),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 3),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 4),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 3),
            ),
            Step(
                UnitReady(UnitTypeId.BARRACKS, 5),
                BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            ),
            # ── 主矿升轨道指挥中心（呼 MULE 回矿，BB1 完成后触发）─────────────
            MorphOrbitals(1),
            # （枪兵量产已上移到建筑步前面，见顶部 TerranUnit(MARINE, 60, priority)）
            # ── 家事 + 进攻 ──────────────────────────────────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门。军队 ~12 供给成型后 skip，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 12),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # PlanZoneAttack 放最后：execute() 永远 return False（"Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
