"""tests/unit/test_facade.py — Sc2Facade / FakeFacade 单测。"""

from vibecraft.bot.facade import FakeFacade


def test_fake_facade_records_attack_target_override():
    f = FakeFacade()
    f.set_attack_target_override((42.0, 100.0))
    assert f.attack_target_overrides == [(42.0, 100.0)]
    f.set_attack_target_override(None)
    assert f.attack_target_overrides == [(42.0, 100.0), None]


def test_fake_facade_records_combat_intent_override():
    f = FakeFacade()
    f.set_combat_intent_override("attack")
    f.set_combat_intent_override("defend")
    f.set_combat_intent_override(None)
    assert f.combat_intent_overrides == ["attack", "defend", None]
