"""人族单矿 4 兵营 proxy rush plan。

1 家兵营（正常位置，迷惑 + 早期枪兵守家）+ 3 野兵营（隐藏 proxy 点）全 pump 枪兵。
3 个建野兵营的 SCV 建完后加入第一波前压（不回采矿）。
无气，单矿 all-in：第一波 ~8-12 枪兵 + 3 SCV ~2:30-3:00 出门，打不死就输。

家兵营作用：
  - 视觉迷惑：敌方 scout 看到 1 BB 在家，以为是正常开局，不去搜地图边缘找野营
  - 早期枪兵：~1:20 起持续出兵守家 + 攒一波

开关：
  - ProxyBarracksAct(blockade_enabled=False)：默认关。
    开启后 3 SCV 建完 → 走到敌方斜坡底修地堡封口（激进封锁变体）。

设计参考：本模块 docstring
策略 yaml：strategies/terran/proxy_4rax.yaml
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, GridBuilding, MineOpenBlockedBase
from sharpy.plans.acts.terran import AutoDepot, MorphOrbitals, TerranUnit
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
from vibecraft.bot.auto_combat.terran.plans.marine_staging_act import MarineStagingAct
from vibecraft.bot.auto_combat.terran.plans.proxy_rax_act import ProxyBarracksAct


class Proxy4Rax(KnowledgeBot):  # type: ignore[misc]
    """1 家 BB + 3 野 BB proxy rush：4 兵营全 pump 枪兵，~2:30-3:00 第一波出门。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Proxy4Rax")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：≥8 Marine 就位 或兜底 ai.time >= 180（all-in 不超 3:00）。"""
        marines = ai.units(UnitTypeId.MARINE).ready.amount
        if marines >= 8:
            return True
        return bool(ai.time >= 180.0)

    async def create_plan(self) -> BuildOrder:
        # 低阈值出门：proxy 兵营在敌方门口，6 supply 即可出门
        attack = PlanZoneAttack(start_attack_power=6)
        attack.attack_on_advantage = False

        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # ── 补给自动化（顶层兄弟，不被串行阻塞）────────────────────────────
            AutoDepot(),
            # ── 枪兵放建筑步前面抢资源（priority 确保有产能就出兵）──────────────
            TerranUnit(UnitTypeId.MARINE, 60, priority=True),
            # ── 农民 ramp：单矿 all-in，~16 封顶 ────────────────────────────────
            # 3 SCV 被 ProxyBarracksAct 抢走做 proxy，留 ~13 挖矿；
            # 再补到 16 凑 4 兵营产能所需采矿量
            [
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16),
            ],
            # ── 早期 critical path：补给站 → 家兵营（严守顺序，保证迷惑位置） ─────
            SequentialList(
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
            ),
            # ── 野兵营 proxy（与家兵营并行，第一次 execute 就派 3 SCV 出发） ──────
            # SCV 提前走去 proxy 点：行进时间 ≈ 矿从 ~150 攒到 450 的时间，
            # 到点即各建 1 个兵营，无需精确计时（到了没钱就等，有钱立刻建）。
            ProxyBarracksAct(target_rax=3, blockade_enabled=False),
            # ── 枪兵前向集结（顶层兄弟，不进 SequentialList——否则会 block 后面的
            # PlanZoneAttack）：头几个枪兵不回家，去野兵营锚点附近集结，攒到 ~6 个
            # 再一起出发；玩家一下全军指令立即释放（用户 2026-07-09 需求）────────
            MarineStagingAct(),
            # ── 主矿升轨道指挥中心（呼 MULE，BB1 完成后触发）────────────────────
            Step(UnitReady(UnitTypeId.BARRACKS, 1), MorphOrbitals(1)),
            # ── 家事 + 进攻 ─────────────────────────────────────────────────────
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                # 军队 ≥ 12 supply 成型后 skip PlanZoneDefense，让主力专心出门
                Step(None, PlanZoneDefense(), skip=lambda ai: ai.supply_army >= 12),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # PlanZoneAttack 放最后：execute() 永远 return False（"Blocks!"）
                attack,
                PlanFinishEnemy(),
            ),
        )
