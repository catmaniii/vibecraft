"""人族空投地雷 plan。

Marine + 寡妇雷 医疗船多线空投：速 BB → VF + VF Reactor → VS，~4:00 起医疗船满编后
多线空投骚扰对方矿线，打乱运营节奏。适用 TvP / TvZ。

Build order 参考（交叉验证 spawningtool #138779 / #41682 / #115729）：
  14 BS → 15 BB → 16 BR → 18 BB → 20 VF → 21 Reactor@VF → 22 VS
  → 24 CC → 26 WidowMine → 28 Medivac → 30 Marine

关键修正（原版问题）：
  - Reactor 挂在兵营(BARRACKSREACTOR)→ 错误！寡妇雷是工厂单位，量产靠工厂 Reactor。
    此版改为 BuildAddon(FACTORYREACTOR, FACTORY, 1)，gate = UnitReady(FACTORY, 1)。
  - 新增 BB1 TechLab + Stimpack：TechLab gate = UnitReady(BARRACKS, 1)，
    Stimpack gate = UnitReady(BARRACKSTECHLAB, 1)。
  - VS 与 VF Reactor 并行触发（都只需 VF 完成）
  - 二矿 Expand 从 SequentialList 提出为独立 Step（BB#1 好即开）
  - MorphOrbitals / 气矿#2 也独立，不受任何 addon 阻塞
  - 每个建筑/科技各自独立 Step 兄弟，互不阻塞

设计参考：strategies/terran/widow_mine_drop.yaml
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

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import WidowMineDropAct


class WidowMineDrop(KnowledgeBot):  # type: ignore[misc]
    """空投地雷开局：Marine + 寡妇雷 医疗船多线空投骚扰矿线。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft WidowMineDrop")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥2 WidowMine + ≥1 Medivac ready + ≥4 Marine。"""
        mines = (
            ai.units(UnitTypeId.WIDOWMINE).ready.amount
            + ai.units(UnitTypeId.WIDOWMINEBURROWED).amount
        )
        if mines < 2:
            return False
        medivacs = ai.units(UnitTypeId.MEDIVAC).ready.amount
        if medivacs < 1:
            return False
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        return bool(marines >= 4)

    async def create_plan(self) -> BuildOrder:
        # 关掉 attack_on_advantage：sharpy 默认「经济领先 + 军队劣势」龟防
        # （zone_attack.py _should_attack），空投流要按战术出门骚扰。
        # start_attack_power=8（原 12）：实测 337s 出门超出 spec 300s 上界 37s；
        # 降低阈值让 2 寡妇雷 + 2 医疗船凑齐后即出发（约供给 8），加速首波空投时机。
        attack = PlanZoneAttack(start_attack_power=8)
        attack.attack_on_advantage = False
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 补给自动化（持续，不被 build 串行阻塞）
            AutoDepot(),
            # 早期少量枪兵填兵营（非 priority、小量）：原 `MARINE 60 priority=True` 是**毒**——
            # 枪兵只吃矿、priority=True 会 reserve-ahead 把矿全占，饿死 Factory/Starport/Expand/
            # 寡妇雷/医疗船（它们都要矿），导致 gas 堆到 1000+ 没人用、0 寡妇雷 0 医疗船（本 build
            # 的命根子全废）。#564 triage 实测 t=357 gas=1069 MAR=23 MINE=0 MED=0。核心(寡妇雷+
            # 医疗船)必须优先于枪兵填充（同 bc_rush「VF 优先于枪兵」铁律）。枪兵只做余矿 filler。
            TerranUnit(UnitTypeId.MARINE, 6),
            # 农民 ramp：单矿先到 22，二矿建好后追到 44
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                ),
            ],
            # 早期 critical path：depot → BB#1 → 气矿#1（三步严守顺序）
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
            ),
            # BB#1 好 → 立即开 BB#2（~1:40 触发，完成 ~2:26）
            Step(UnitReady(UnitTypeId.BARRACKS, 1), GridBuilding(UnitTypeId.BARRACKS, 2)),
            # BB#2 放置（exists）即触发 VF：避免与 BB#2 同帧争抢 3x3 建筑位置
            # 关键：VF 和 BB#2 都需要 3x3 slot；同帧触发时 position_terran 两次返回同一位置
            # 导致 SCV 冲突、建筑被取消、VF 反复重试，实测延迟至 ~225s 才放置（应 ~105s）
            # UnitExists(BB2) 触发：BB2 放置后 VF 立即跟进，两者占不同 slot，~107s 放置 → 完成 ~172s
            Step(UnitExists(UnitTypeId.BARRACKS, 2), GridBuilding(UnitTypeId.FACTORY, 1)),
            # VF 放下（exists）即快扩二矿：UnitReady 触发会让 Expand 拖到 ~3:30 才下令、
            # CC 完成 ~4:40 错过验收窗口。改 UnitExists 让 Expand 早 ~50s 抢 400M。
            Step(UnitExists(UnitTypeId.FACTORY, 1), Expand(2)),
            # VF 放置（exists）即触发 VS 建造，与 Factory 建造重叠：
            # UnitReady(FACTORY) 触发时 Starport 和 FactoryReactor 同帧竞争 SCV，
            # 实测第 2 架 Medivac 落入 spec 窗口外（只有 1 架）。
            # 改 UnitExists 让 Starport SCV 更早出发，比 Factory Reactor 先抢位置。
            Step(UnitExists(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            # VF 好 → 工厂 Reactor（核心修复！寡妇雷是工厂单位，量产靠 VF Reactor，不是兵营 Reactor）
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            ),
            # BB#1 好 → 兵营 TechLab（Stimpack 前置）
            Step(
                UnitReady(UnitTypeId.BARRACKS, 1),
                BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            ),
            # 气矿#2 提前到 BB1 好就建：VS 需 100 气，gas2 太晚会把 VS 饿到 ~295s。
            Step(UnitReady(UnitTypeId.BARRACKS, 1), BuildGas(2)),
            # 二矿建好 → 升轨道（主矿 + 二矿各一）
            MorphOrbitals(2),
            # 研 Stimpack（兵营 TechLab 好即触发，与空投主线并行）
            Step(
                UnitReady(UnitTypeId.BARRACKSTECHLAB, 1),
                Tech(UpgradeId.STIMPACK),
            ),
            # 军队生产（各自独立，与 build 并行）
            # VF 好即出寡妇雷（目标 8 颗：3-4 颗装船用，其余留守；VF Reactor 双产）。
            # priority=True：寡妇雷是本 build 核心战斗单位，必须保证拿到矿/气产出，不被枪兵 filler 抢光
            # （#564：原来无 priority + 枪兵 priority 抢矿 → 寡妇雷 0 颗）。
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                TerranUnit(UnitTypeId.WIDOWMINE, 8, priority=True),
            ),
            # VS 好即出医疗船（首波 2 架）。priority=True 必须有 ——
            # 医疗船是空投载具(本 build 核心)，不 priority 会被寡妇雷抢光气体 → 0 医疗船。
            Step(
                UnitReady(UnitTypeId.STARPORT, 1),
                TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True),
            ),
            # Marine 持续生产（枪兵基础力量）
            TerranUnit(UnitTypeId.MARINE, 20),  # 8→20: 早期填满兵营(二矿前)
            # 二矿成型后扩大生产规模（持续空投节奏）
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                TerranUnit(UnitTypeId.WIDOWMINE, 8),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                TerranUnit(UnitTypeId.MEDIVAC, 6, priority=True),
            ),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                TerranUnit(UnitTypeId.MARINE, 24),
            ),
            # 家事 + 进攻
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # PlanZoneDefense 会从 free_units 抽兵标 Defending → PlanZoneAttack
                # 看不见这些兵 → 永不出门（见 dt_drop_iac.py / gate4_pressure.py 注释）。
                # 军队成型后 skip 掉，让主力专心出门。
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 12),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                # 空投地雷骚扰：把医疗船 + 寡妇雷送进对方矿区埋地伏击。
                # 排在 Gather 之前：先 Reserved 掉它们，Gather 就不会拉进主力。
                WidowMineDropAct(),
                PlanZoneGather(),
                # PlanZoneAttack 放最后：execute() 永远 return False（源码 "Blocks!"），
                # 放中间会 block 掉后面的 DistributeWorkers / Gather。
                attack,
                PlanFinishEnemy(),
            ),
        )
