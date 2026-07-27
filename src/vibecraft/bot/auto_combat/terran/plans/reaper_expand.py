"""人族死神扩张 plan。

死神骚扰 + 快扩：1 BB 出死神骚扰对方工人 → 同时开扩张，积累经济优势。
稳扎稳打的主流 TvX 开局。

设计参考：strategies/terran/reaper_expand.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair

from vibecraft.bot.auto_combat.harass_act import HarassWorkerLineAct
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class ReaperExpand(KnowledgeBot):  # type: ignore[misc]
    """死神扩张开局：死神骚扰 + 快速开扩张。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft ReaperExpand")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥4 Reaper 已出场 + 二矿 CC done（快扩稳住）。"""
        reapers = ai.units(UnitTypeId.REAPER).amount
        if reapers < 4:
            return False
        cc = (
            ai.structures(UnitTypeId.COMMANDCENTER).amount
            + ai.structures(UnitTypeId.ORBITALCOMMAND).amount
            + ai.structures(UnitTypeId.PLANETARYFORTRESS).amount
        )
        return bool(cc >= 2)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 持续后台（补给自动化 + 农民生产）──────────────────────────────────
            AutoDepot(),
            # 农民阶梯：单矿爬到 22，二矿开后继续爬到 44
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # ── 早期 critical path（depot → BB → 气矿，严守顺序）────────────────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # ── 死神开矿正确节奏（2026-06-17 用户拍板）：先 1 死神 → 升星轨+开二矿 → 补齐 4 死神 ──
            # ① 先出 1 个死神（兵营一好第一时间，barracks 不空转 + 早期侦查骚扰）。只 50 矿微量，
            #    不会饿到后面的星轨/二矿。原版 REAPER 排在 Expand 后面 + cap=1 → Expand 每帧预扣
            #    400 矿饿死死神 → 兵营建好空转 ~47s（真局 match_20260617：BB t=90 好 busy=0 到 137）。
            TerranUnit(UnitTypeId.REAPER, 1),
            # ② 升星轨（经济，呼 MULE）。MorphOrbitals 放 SCV 后面：python-sc2 一帧内最后一条
            #    命令生效，morph 覆盖同帧 SCV 训练，BB 完成后立刻变形。
            MorphOrbitals(2),
            # ③ 开二矿。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), Expand(2)),
            # ④ 二矿/星轨就位后补齐到 4 个死神（对齐 opening_done 的 ≥4）。
            # **关键调参（2026-06-17 实测）**：不能整组 4 死神排星轨/Expand 前、更不能加 priority
            #    —— 会把 150 矿星轨 + 400 矿二矿一起饿死（orbital_command/command_center_2 双 FAIL）。
            #    按用户顺序"1 死神 → 经济 → 补满"两全：兵营不空转，星轨/二矿也不被拖。
            TerranUnit(UnitTypeId.REAPER, 4),
            # ── BB 一好后并行触发（所有步骤等 UnitReady(BARRACKS, 1)）────────────
            # BB 一好：接 TechLab（后续研 Stim）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            # 二矿开出后接第二条气矿
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), BuildGas(2)),
            # 气矿够后开 VF（100 mineral + 100 gas；TechLab 消耗 50 gas，单气矿积累充足后触发）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                GridBuilding(UnitTypeId.FACTORY, 1),
            ),
            # VF 好后出 1 辆追击者 / 工厂单位消耗资源
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                TerranUnit(UnitTypeId.HELLION, 4),
            ),
            # TechLab 好后研 Stim
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # 连续 Marine：放在 Expand **之后**（rule#4：快扩也要一直出兵，死神出完接枪兵
            # 填产线不留空窗）。不能放 Expand 前 —— 30 枪兵持续抽矿会把二矿/轨道拖崩
            # （2026-06-17 实测 command_center_2 FAIL）。放这里：二矿/轨道先成,余矿全投枪兵。
            TerranUnit(UnitTypeId.MARINE, 30),
            # ── 家事 + 进攻（tactics，原样保留）────────────────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常对局每帧 return False（sharpy
                # 源码 zone_attack.py:123 "Blocks!"），SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。曾因顺序颠倒导致 DistributeWorkers
                # 从不运行：气矿无人采、农民全堆主矿过饱和。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 死神单独拉出去骚扰 / 侦查对方矿区 —— 造出兵 ≠ 骚扰到位。
                # 死神主要任务是「保命侦查/骚扰」:bail_hp=0.6 提前全撤回家、
                # recover_hp=0.99 **回满血才再出去**（2026-06-17 用户：跳高地被枪兵打到血低后撤回，
                # 不能 95% 就又冲上去——3-4 枪兵能把 95% 一波带走，要等满血再去看）。
                # 配合 harass_act「冷却+敌人(含农民)逼近就退、绝不站撸」，尽量别死、把侦查持续更久。
                # 排在 Gather 之前先 Reserved 掉死神。
                HarassWorkerLineAct({UnitTypeId.REAPER}, bail_hp_ratio=0.6, recover_hp_ratio=0.99),
                PlanZoneGather(),
                PlanZoneAttack(),
                PlanFinishEnemy(),
            ),
        )
