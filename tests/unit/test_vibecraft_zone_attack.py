"""VibeCraftZoneAttack: PlanZoneAttack 子类，优先读 knowledge.vibecraft override。"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# fake sharpy / sc2 注入 —— 与 test_protoss_facade_overrides.py 同模式
# ---------------------------------------------------------------------------


def _inject_fake_sharpy() -> None:
    """注入最小 fake sharpy / sc2，让 vibecraft_zone_attack 顶层 import 通过。"""

    # --- sc2.position ---
    for mod_name in ["sc2", "sc2.position"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    class FakePoint2(tuple):
        """最小 Point2 stub：继承 tuple，支持 x/y 属性访问。"""

        def __new__(cls, pt: Any) -> "FakePoint2":
            return super().__new__(cls, pt)

        @property
        def x(self) -> float:
            return self[0]

        @property
        def y(self) -> float:
            return self[1]

    sys.modules["sc2.position"].Point2 = FakePoint2  # type: ignore[attr-defined]

    # --- sharpy ---
    class FakeActBase:
        """ActBase 极简 stub。"""

        pass

    class FakePlanZoneAttack(FakeActBase):
        """PlanZoneAttack 极简 stub，只暴露被 VibeCraftZoneAttack 覆盖的两个方法。"""

        def _get_target(self) -> Any:
            return None

        def _should_attack(self, *args: Any, **kwargs: Any) -> Any:
            return False

    for mod_name in [
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.plans.tactics",
        "sharpy.plans.tactics.zone_attack",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    sys.modules["sharpy.plans.acts"].ActBase = FakeActBase  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.tactics"].PlanZoneAttack = FakePlanZoneAttack  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.tactics.zone_attack"].PlanZoneAttack = FakePlanZoneAttack  # type: ignore[attr-defined]


_PREFIXES = ("sharpy", "sc2", "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack")


def _clean_mods() -> None:
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _PREFIXES):
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _fake_env():
    _clean_mods()
    _inject_fake_sharpy()
    yield
    _clean_mods()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _import_plan():
    """每次从干净的 sys.modules 状态 import VibeCraftZoneAttack。"""
    import importlib

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
    assert plan._should_attack() is True


@pytest.mark.parametrize("intent", ["defend", "hold", "retreat", "vision"])
def test_should_attack_defensive_intents_return_false(intent):
    plan = _make_plan(override_intent=intent)
    assert plan._should_attack() is False


def test_should_attack_no_intent_falls_back(monkeypatch):
    plan = _make_plan(override_intent=None)
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._should_attack",
        lambda self: "DEFAULT",
    )
    assert plan._should_attack() == "DEFAULT"
