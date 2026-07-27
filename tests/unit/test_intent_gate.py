"""AttackGate 单测（含 2026-06-02 supply 兜底）。

AttackGate 不依赖 sharpy，可直接 import 测。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from vibecraft.bot.auto_combat.intent_gate import AttackGate


def _ai(intent: Any = None, supply: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        knowledge=SimpleNamespace(vibecraft=SimpleNamespace(combat_intent_override=intent)),
        supply_used=supply,
    )


def test_closed_when_not_ready_no_intent_low_supply() -> None:
    gate = AttackGate(lambda ai: False)
    assert gate(_ai(intent=None, supply=120)) is False


def test_opens_when_ready_fn_true() -> None:
    gate = AttackGate(lambda ai: True)
    assert gate(_ai(intent=None, supply=50)) is True
    assert gate.latched is True


def test_opens_on_player_attack_intent() -> None:
    gate = AttackGate(lambda ai: False)
    assert gate(_ai(intent="attack", supply=50)) is True


def test_supply_fallback_opens_gate_at_190() -> None:
    """根因修复：_ready_fn 永 False（凑不齐 comp），但 supply 满编 → 强制开门。"""
    gate = AttackGate(lambda ai: False)
    assert gate(_ai(intent=None, supply=190)) is True
    assert gate.latched is True


def test_supply_fallback_just_below_threshold_stays_closed() -> None:
    gate = AttackGate(lambda ai: False)
    assert gate(_ai(intent=None, supply=189)) is False


def test_supply_fallback_latches_permanently() -> None:
    """满编开门后掉编（被打回 150）仍保持开（latch）。"""
    gate = AttackGate(lambda ai: False)
    assert gate(_ai(intent=None, supply=200)) is True
    assert gate(_ai(intent=None, supply=150)) is True  # latched，不缩回


def test_ready_fn_exception_then_supply_fallback() -> None:
    """_ready_fn 抛异常不致命，supply 兜底仍生效。"""

    def _boom(ai: Any) -> bool:
        raise RuntimeError("boom")

    gate = AttackGate(_boom)
    assert gate(_ai(intent=None, supply=195)) is True
