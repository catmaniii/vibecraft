"""vibecraft 炮塔速攻（Cannon Rush）plan。

战术核心（2026-05-20 用户战术细化）
====================================
proxy 探机赶到隐蔽点修 BE（Pylon）+ BC（PhotonCannon）压制对方采矿：
  - **Forge 在家建**（光炮科技前置）；proxy 探机只前出 BE + 光炮
  - main 模式：探机开局即走敌方斜坡上高地、proxy 躲高地边缘、避开主矿矿区
  - natural 模式：proxy 卡二矿背后贴边，第一个光炮赶在对方二矿建好前完成
  - 后手追猎（Stalker）：折跃完成后前出配合炮塔

关键路径
========
1. 开局 ForwardCannonProxy 接管探机（血量低 / 被发现时补到 2 个），立刻赶往 proxy 点
2. proxy 探机：BE（供电）→ BC（前线尽量多修）；家里 BF 一好就拍光炮
3. 家里 BG → BF → BY → 气矿 → 折跃 → 追猎后手
4. 追猎前出支援；失败转运营（光炮没成型也照常开矿运营推）

Build 节奏（参考 spawningtool.com 111586，PvZ Cannon Rush / Proxy Stalkers）
===========================================================================
  12  ForwardCannonProxy 接管探机，立刻出发
  14  BE（home Pylon）
  ~14 BE（forward proxy Pylon —— 光炮供电前置，ForwardCannonProxy 建）
  16  BF（home Forge —— 光炮科技前置，家里建）
  18  BG（home Gateway）
  ~18 BC（forward 尽量多修，proxy Pylon 供电 + 家里 BF 好后连续建）
  20  BY（home CyberneticsCore）→ 折跃 → 追猎后手
  ~24 二矿（晚开 —— 前线先尽量多花钱）
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.forward_cannon_proxy import ForwardCannonProxy
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class CannonRush(KnowledgeBot):  # type: ignore[misc]
    """炮塔速攻（Cannon Rush）— 速 BF + BC 偷家，追猎后手。PvZ/PvP。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Cannon Rush")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_pressure)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_ready_to_pressure 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._ready_to_pressure),
            # ---------- 前线 proxy（核心！proxy 探机建 BE + 光炮）----------
            # ForwardCannonProxy 开局即接管探机赶往隐蔽点：BE（Pylon）供电
            # → BC（PhotonCannon）前线尽量多修。Forge 在家建（下方），是光炮
            # 科技前置，proxy 探机不前出 Forge。main 模式走敌方斜坡上高地、避矿区。
            ForwardCannonProxy(),
            # ---------- chrono ----------
            # 探机 chrono：开局一直开,到 BY 出现停(下一个 step 折跃 chrono 接管)。
            # 2026-05-20 修 bug:原来有 `skip_until=UnitExists(ASSIMILATOR,1)`,但炮塔
            # 速攻**不建气矿**,ASSIMILATOR 永不存在 → skip_until 永不满足 → Step 永远
            # return True → ChronoUnit 整个 act 从未执行。删掉 skip_until,探机 chrono
            # 从开局就跑(skip=CYBERNETICSCORE 保证 BY 一出现就停)。
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 折跃 chrono（BY 完成后，把能量全给折跃研究）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # ---------- 早期主线（严守顺序）----------
            # 炮塔速攻阶段不需要气矿（BF + BC 都是纯矿建筑）：
            # BA 移除，把矿留给 BF 更快建造
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 12),
                GridBuilding(UnitTypeId.PYLON, 1),  # 14 supply BE（家里）
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                # 家里先下 BG（BF 由 ForwardCannonProxy 在前线负责）
                GridBuilding(UnitTypeId.GATEWAY, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 18),
            ),
            # ---------- 家里 BF（光炮科技前置；2026-05-20 用户修正：Forge 在家建）----------
            # 光炮需 psi 供电（proxy Pylon）+ Forge 科技。Forge 在家建、proxy 探机
            # 不前出 Forge —— 家里 BF 一好，ForwardCannonProxy 就在 proxy Pylon
            # 供电范围内连拍光炮。家里 BF 与探机赶路 + 建 proxy Pylon 并行推进。
            Step(UnitExists(UnitTypeId.PYLON, 1), GridBuilding(UnitTypeId.FORGE, 1)),
            # ---------- 家里补 BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 家里气矿（折跃研究 + 追猎后手都吃气，必须建）----------
            # 2026-05-20 修 bug:原 plan 完全删气矿("炮塔速攻纯矿"),但折跃和追猎
            # 都吃气 → 没气矿 = 折跃永远研不了、追猎永远出不了,后手彻底瘫痪。
            # 炮塔(BF/BC)确实纯矿,但 BY→折跃→追猎这条后手线必须有气。
            Step(UnitReady(UnitTypeId.GATEWAY, 1), BuildGas(1)),
            # ---------- 补到 3 门（追猎产能）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 3)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- 二矿延续（晚开：前线先把钱花在光炮上）----------
            # 用户战术：cannon rush 的矿优先砸前线 —— 6 炮建满（前线花完钱）或
            # 4:30 兜底再开二矿（也兼作失败转运营 —— rush 没成也照常开矿运营打）。
            Step(
                lambda ai: (
                    ai.structures(UnitTypeId.PHOTONCANNON).amount >= 6 or ai.time >= 60 * 4.5
                ),
                Expand(2),
            ),
            # ---------- 二矿气矿（家里已 1 气，二矿补到 3）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 后期补 BG（追猎产能）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 单位训练 ----------
            # 追猎（折跃好后前出，配合前线炮塔钳制）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 12),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 折跃完成 + 3+ BC + 6 Stalker → 出门配合炮塔
                # 2026-05-28 Issue 4:AttackGate(含 retreat/defend/hold + latch)
                Step(self._attack_gate, PlanZoneAttack(6)),  # 6 Stalker 出门配合前线炮塔
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """出门 timing：
        - 正常：折跃完成 + 3 BC + 6 追猎 —— 炮塔压住矿线，追猎前出配合封锁。
        - 失败转运营兜底：折跃完成 + 5:00 后 + 10 追猎 —— 光炮没成型时也不干等，
          靠追猎运营推（配合 plan 的失败转运营，家里架子按运营打）。
        """
        warpgate_done = (
            ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) >= 1.0
            or UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        )
        if not warpgate_done:
            return False
        cannon_count = ai.structures(UnitTypeId.PHOTONCANNON).ready.amount
        stalker_count = ai.units(UnitTypeId.STALKER).ready.amount
        # 正常：3 光炮压制成型 + 6 追猎配合
        if cannon_count >= 3 and stalker_count >= 6:
            return True
        # 失败转运营：光炮没成型，5:00 后靠 10 追猎运营推
        return bool(ai.time >= 60 * 5 and stalker_count >= 10)
