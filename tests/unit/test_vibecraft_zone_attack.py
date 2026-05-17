"""VibeCraftZoneAttack: PlanZoneAttack 子类，优先读 knowledge.vibecraft override。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# fixture：从 conftest 的 fake_sharpy_zone_attack_env 派发（autouse）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_env(fake_sharpy_zone_attack_env: Any) -> Any:
    """autouse wrapper：让本文件所有 test 自动走 fake_sharpy_zone_attack_env。"""
    return fake_sharpy_zone_attack_env


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _import_plan():
    """每次从干净的 sys.modules 状态 import VibeCraftZoneAttack。"""
    mod = importlib.import_module(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack"
    )
    return mod.VibeCraftZoneAttack


def _make_plan(override_target=None, override_intent=None):
    """构造一个不需 sharpy init 的 plan 实例（绕 __init__）。"""
    cls = _import_plan()
    plan = cls.__new__(cls)
    plan.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            attack_target_override=override_target,
            combat_intent_override=override_intent,
        )
    )
    return plan


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_target_uses_override_when_set(monkeypatch):
    plan = _make_plan(override_target=(50.0, 100.0))
    # parent _get_target 返回 sentinel；override 应该胜出
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._get_target",
        lambda self: ("SENTINEL_NATURAL",),
    )
    result = plan._get_target()
    # override 应该被包成 Point2 或保持 tuple；只要 != sentinel 就行
    assert result != ("SENTINEL_NATURAL",)
    # 用 attr 而非 equality 防 Point2 vs tuple 差异
    assert getattr(result, "x", result[0]) == 50.0


def test_get_target_falls_back_to_parent_when_no_override(monkeypatch):
    plan = _make_plan(override_target=None)
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._get_target",
        lambda self: "DEFAULT",
    )
    assert plan._get_target() == "DEFAULT"


def test_should_attack_intent_attack_returns_true():
    plan = _make_plan(override_intent="attack")
    assert plan._should_attack(MagicMock()) is True


@pytest.mark.parametrize("intent", ["defend", "hold", "retreat", "vision"])
def test_should_attack_defensive_intents_return_false(intent):
    plan = _make_plan(override_intent=intent)
    assert plan._should_attack(MagicMock()) is False


def test_should_attack_no_intent_falls_back(monkeypatch):
    plan = _make_plan(override_intent=None)
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._should_attack",
        lambda self, power: "DEFAULT",
    )
    assert plan._should_attack(MagicMock()) == "DEFAULT"
