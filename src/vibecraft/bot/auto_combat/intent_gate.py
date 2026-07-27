"""战术 plan 的 Step gate helper (2026-05-28 Issue 4 修复)。

修前 bug:大部分 protoss plan 用 Step gate 限 PlanZoneAttack 出门 timing:

    Step(
        lambda ai: (
            self._ready_to_pressure(ai)
            or intent == "attack"
        ),
        PlanZoneAttack(N),
    )

sharpy Step.execute() requirement False → Step return False → SequentialList
整段停 → PlanZoneAttack + 后面的 PlanFinishEnemy 都不执行。

玩家点 retreat 时 intent="retreat" 不是 "attack",_ready_to_pressure 可能因
stalker 死光转 False → gate False → PlanZoneAttack 不被调用 →
retreat intent 没人读 → 单位继续之前 combat.execute(Assault)。

修法:`AttackGate` 替代上面的 lambda,两层保护:
  1. 接受任意玩家 intent(attack/retreat/defend/hold)→ True
  2. 一旦 gate True 永久 latch True → 防 stalker 死光 supply<20
     导致 _ready_to_pressure 转 False 让 PlanZoneAttack disable。

用法:
    class MyPlan(KnowledgeBot):
        def __init__(self):
            super().__init__("...")
            self._attack_gate = AttackGate(self._ready_to_pressure)

        async def create_plan(self):
            return BuildOrder(
                ...,
                SequentialList(
                    ...,
                    PlanZoneGather(),
                    Step(self._attack_gate, PlanZoneAttack(N)),
                    PlanFinishEnemy(),
                ),
            )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_INTENT_OPEN_PLAN_ZONE_ATTACK: frozenset[str] = frozenset({"attack", "retreat", "defend", "hold"})

# 2026-06-02 兜底:supply 到这个值还没靠 _ready_fn 出门 → 强制开门。
# 根因(systematic-debugging + telemetry game_20260602_190830/192554):protoss 的
# _ready_to_push 是硬性单位数门(如 8 航母),若 maxed 时凑的是别的兵种混编没达标,
# 门永不开 → 满编大军(实测 200 supply / ~96 兵)在家死坐到 timeout。supply 信号与
# sim 速度无关(build_acceptance 加速 sim,time deadline 不可靠)。与 PlanZoneAttack
# ._should_attack 内部的 supply>190→attack 阈值一致:开门后它自然接管。
_SUPPLY_FORCE_PUSH: float = 190.0


class AttackGate:
    """Step gate 包装 _ready_to_pressure + 玩家 intent 覆盖 + latch。

    callable(ai) → bool — 配合 sharpy `Step(gate, PlanZoneAttack(N))` 使用。
    """

    def __init__(self, ready_fn: Callable[[Any], bool]) -> None:
        self._ready_fn = ready_fn
        self._latched = False

    def __call__(self, ai: Any) -> bool:
        if self._latched:
            return True
        try:
            intent = getattr(
                getattr(ai.knowledge, "vibecraft", None),
                "combat_intent_override",
                None,
            )
        except Exception:
            intent = None
        if intent in _INTENT_OPEN_PLAN_ZONE_ATTACK:
            self._latched = True
            return True
        try:
            if self._ready_fn(ai):
                self._latched = True
                return True
        except Exception:
            pass
        # 兜底:满编(supply >= _SUPPLY_FORCE_PUSH)还没出门 → 强制开门,别在家死坐到
        # timeout(凑不齐 _ready_fn 要的理想 comp 时)。latch 后永久开,后续掉编也不缩回。
        try:
            if float(getattr(ai, "supply_used", 0) or 0) >= _SUPPLY_FORCE_PUSH:
                self._latched = True
                return True
        except Exception:
            pass
        return False

    # 测试用:重置 latch
    def reset(self) -> None:
        self._latched = False

    @property
    def latched(self) -> bool:
        return self._latched
