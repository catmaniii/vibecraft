"""blink_stalker._attack_gate 单测(2026-05-28 Issue 4 修复)。

修前 bug:
- Step gate 只允许 _ready_to_pressure True 或 intent=="attack" 通过。
- 玩家点 retreat → intent=retreat → gate False → SequentialList 停 →
  PlanZoneAttack 不被调用 → 单位继续之前 combat.execute(Assault)前线推。
- stalker 死光 supply<20 → _ready_to_pressure False → 同上,gate 永久关闭。

修后:
- gate 看 intent in (attack, retreat, defend, hold) 任一 → True。
- 一旦 gate True latch 永久 True → PlanZoneAttack 永远被调用,响应后续切换。

本测试不依赖 sharpy 实例化(避免 config.ini 等环境依赖),直接构 instance + mock ai。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"

# 必须在 import BlinkStalker 之前让 sharpy 可 import
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))


@pytest.fixture
def plan_instance():
    """构造 BlinkStalker instance,绕过 sharpy __init__(避免 config.ini 依赖)。"""
    try:
        from vibecraft.bot.auto_combat.protoss.plans.blink_stalker import BlinkStalker
    except ImportError as e:
        pytest.skip(f"sharpy import 失败: {e}")
    inst = BlinkStalker.__new__(BlinkStalker)
    from vibecraft.bot.auto_combat.intent_gate import AttackGate

    inst._attack_gate = AttackGate(inst._ready_to_pressure)
    return inst


def _make_ai(intent: str | None = None, ready_to_pressure: bool = False) -> MagicMock:
    """构造 mock ai with vibecraft.combat_intent_override + _ready_to_pressure return。"""
    ai = MagicMock()
    ai.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(combat_intent_override=intent))
    # 让 _ready_to_pressure 通过 mock 控制
    if not ready_to_pressure:
        # 让所有 already_pending_upgrade / state.upgrades 都返 False
        ai.already_pending_upgrade.return_value = 0.0
        ai.state.upgrades = set()
        ai.units.return_value.ready.amount = 0
        ai.time = 0
    else:
        # blink + 1攻 + 20 supply 全满足
        ai.already_pending_upgrade.return_value = 1.0
        # 让 state.upgrades check 都 True
        ai.state.upgrades = MagicMock()
        ai.state.upgrades.__contains__ = lambda self, x: True
        ai.units.return_value.ready.amount = 15
        ai.time = 9999
    return ai


class TestAttackGateBasic:
    def test_gate_false_when_no_intent_no_ready(self, plan_instance) -> None:
        """无 intent 且 _ready_to_pressure False → gate False(默认开局,等条件)。"""
        ai = _make_ai(intent=None, ready_to_pressure=False)
        assert plan_instance._attack_gate(ai) is False
        assert plan_instance._attack_gate.latched is False

    def test_gate_true_for_attack_intent(self, plan_instance) -> None:
        """intent=attack → gate True(玩家点强制全体进攻)。"""
        ai = _make_ai(intent="attack")
        assert plan_instance._attack_gate(ai) is True
        assert plan_instance._attack_gate.latched is True

    def test_gate_true_for_retreat_intent(self, plan_instance) -> None:
        """Issue 4 regression:intent=retreat → gate True(让 PlanZoneAttack 跑撤单位)。

        修前 bug:retreat 时 gate False,SequentialList 停,PlanZoneAttack 不被调用,
        retreat intent 无人读,单位不撤。
        """
        ai = _make_ai(intent="retreat")
        assert plan_instance._attack_gate(ai) is True, (
            "intent=retreat 应让 gate True,否则 PlanZoneAttack 不被调用 → 不撤退"
        )
        assert plan_instance._attack_gate.latched is True

    def test_gate_true_for_defend_intent(self, plan_instance) -> None:
        """intent=defend → gate True(stance_override 影响 _should_retreat)。"""
        ai = _make_ai(intent="defend")
        assert plan_instance._attack_gate(ai) is True

    def test_gate_true_for_hold_intent(self, plan_instance) -> None:
        """intent=hold → gate True。"""
        ai = _make_ai(intent="hold")
        assert plan_instance._attack_gate(ai) is True


class TestAttackGateLatch:
    """latch 行为:一旦 True 永久 True,防 stalker 死光 gate 重新关闭。"""

    def test_latched_stays_true_after_intent_cleared(self, plan_instance) -> None:
        """intent=attack 触发 latch,之后 intent 清掉,gate 仍 True。"""
        # 第一次:触发 latch
        ai = _make_ai(intent="attack")
        assert plan_instance._attack_gate(ai) is True
        # 第二次:intent 清掉(玩家 cancel),gate 仍 True(latched)
        ai_cleared = _make_ai(intent=None, ready_to_pressure=False)
        assert plan_instance._attack_gate(ai_cleared) is True, (
            "latch 后 gate 应永久 True,防 stalker 死光 supply<20 → "
            "_ready_to_pressure False → gate 重新关闭 → retreat 无效"
        )

    def test_latched_stays_true_after_ready_dropped(self, plan_instance) -> None:
        """_ready_to_pressure True 触发 latch 后,即使条件转 False(stalker 死光),
        gate 仍 True。这是 Issue 4 的 second-order 防御。"""
        ai_ready = _make_ai(intent=None, ready_to_pressure=True)
        assert plan_instance._attack_gate(ai_ready) is True
        # stalker 全死,_ready_to_pressure 转 False
        ai_no_army = _make_ai(intent=None, ready_to_pressure=False)
        assert plan_instance._attack_gate(ai_no_army) is True

    def test_retreat_intent_also_latches(self, plan_instance) -> None:
        """玩家直接点 retreat(没先 attack)也 latch → 后续 attack/retreat 切换不卡 gate。"""
        ai_retreat = _make_ai(intent="retreat")
        assert plan_instance._attack_gate(ai_retreat) is True
        assert plan_instance._attack_gate.latched is True


class TestAttackGateRegressionVsOld:
    """对比修前行为:验证新逻辑覆盖旧 bug 路径。"""

    def test_old_bug_scenario_retreat_when_supply_low(self, plan_instance) -> None:
        """重现 2026-05-28 玩家 log 场景:
        - 14 minute 玩家点 attack(intent=attack), gate True, PlanZoneAttack 起
        - 单位被打死,stalker_supply<20,_ready_to_pressure False
        - 玩家点 retreat,intent=retreat
        - 修前:gate(retreat AND not ready) False → PlanZoneAttack 停 → 不撤
        - 修后:gate True(intent in set + latched) → PlanZoneAttack 跑 → 撤
        """
        # Step 1: attack 触发 latch
        ai1 = _make_ai(intent="attack", ready_to_pressure=False)
        assert plan_instance._attack_gate(ai1) is True

        # Step 2: 单位死,intent 仍 attack,但 _ready_to_pressure False
        ai2 = _make_ai(intent="attack", ready_to_pressure=False)
        assert plan_instance._attack_gate(ai2) is True  # latch 仍 True

        # Step 3: 玩家点 retreat
        ai3 = _make_ai(intent="retreat", ready_to_pressure=False)
        assert plan_instance._attack_gate(ai3) is True, (
            "Issue 4 修复:retreat 时 gate 必须 True 让 PlanZoneAttack 跑"
        )
