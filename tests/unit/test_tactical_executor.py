"""P0b Task 12: L2 tactical_objective executor (A 全军 + B squad 分流)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade, UnitRole
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def fake_facade() -> FakeFacade:
    return FakeFacade(state=BotState(game_time=100.0))


@pytest.fixture
def director(library: StrategyLibrary, session: GameSession, fake_facade: FakeFacade) -> Director:
    """最小 Director 实例，直接调内部方法。"""
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)
        ]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=fake_facade, parser=parser, session=session)


def _make_tactical_directive(
    verb: str,
    target: str | tuple[float, float] | None = None,
    unit_count_hint: int | None = None,
    unit_type_hint: list[str] | None = None,
    done_when: dict | None = None,
    source_text: str = "",
) -> object:
    """造 TACTICAL_OBJECTIVE directive。"""
    from vibecraft.directives.models import Directive, TacticalObjectivePayload

    payload_data: dict = {
        "type": "tactical_objective",
        "verb": verb,
    }
    if target is not None:
        payload_data["target_area"] = target
    if unit_count_hint is not None:
        payload_data["unit_count_hint"] = unit_count_hint
    if unit_type_hint is not None:
        payload_data["unit_type_hint"] = unit_type_hint
    if done_when is not None:
        payload_data["done_when"] = done_when

    payload = TacticalObjectivePayload(**{k: v for k, v in payload_data.items() if k != "type"})
    return Directive(payload=payload, issued_at=10.0, source_text=source_text)


# =========================================================================
# A 类测试（override flag 路径）
# =========================================================================


def test_l2_attack_sets_override_flags(director: Director, fake_facade: FakeFacade) -> None:
    """A 类 attack: facade.set_attack_target_override + set_combat_intent_override('attack')"""
    d = _make_tactical_directive(verb="attack", target="enemy_natural")
    director._exec_tactical_objective(d, d.payload)
    # facade 被调（不强求具体 Point2，只确保至少一条 override 存在）
    assert fake_facade.attack_target_overrides
    assert fake_facade.combat_intent_overrides[-1] == "attack"


def test_l2_defend_sets_intent_defend(director: Director, fake_facade: FakeFacade) -> None:
    d = _make_tactical_directive(verb="defend")
    director._exec_tactical_objective(d, d.payload)
    assert fake_facade.combat_intent_overrides[-1] == "defend"


def test_l2_global_replace_previous(director: Director, fake_facade: FakeFacade) -> None:
    """同一玩家 attack → defend 切换，最后 intent 应是 defend。"""
    d1 = _make_tactical_directive(verb="attack", target="enemy_natural")
    d2 = _make_tactical_directive(verb="defend")
    director._exec_tactical_objective(d1, d1.payload)
    director._exec_tactical_objective(d2, d2.payload)
    assert fake_facade.combat_intent_overrides[-1] == "defend"


# =========================================================================
# B 类测试（squad 抢占路径）
# =========================================================================


def test_l2_harass_locks_squad_units(director: Director, fake_facade: FakeFacade) -> None:
    """派 5 凤凰骚扰，应抓 5 个 free Phoenix → set_unit_role LLM_CONTROLLED"""
    fake_facade.selector_stub["Phoenix"] = [101, 102, 103, 104, 105, 106]  # 6 个空闲
    d = _make_tactical_directive(
        verb="harass",
        target="enemy_main",
        unit_count_hint=5,
        unit_type_hint=["Phoenix"],
        done_when={
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 5,
        },
    )
    director._exec_tactical_objective(d, d.payload)
    locked = [t for t, r in fake_facade.unit_roles.items() if r == UnitRole.LLM_CONTROLLED]
    assert sorted(locked) == [101, 102, 103, 104, 105]
    # squad 注册
    assert d.id in director._tactical_squads
    assert director._tactical_squads[d.id].n_wanted == 5
    assert director._tactical_squads[d.id].n_locked == 5


def test_l2_harass_short_supply(director: Director, fake_facade: FakeFacade) -> None:
    """玩家说 5 凤凰，只有 3 空闲 → 抓 3 个 + status 显示短缺"""
    fake_facade.selector_stub["Phoenix"] = [201, 202, 203]
    d = _make_tactical_directive(
        verb="harass",
        target="enemy_main",
        unit_count_hint=5,
        unit_type_hint=["Phoenix"],
        done_when={
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 5,
        },
    )
    director._exec_tactical_objective(d, d.payload)
    squad = director._tactical_squads[d.id]
    assert squad.n_locked == 3
    assert squad.n_wanted == 5
    # status reason 应含短缺信息
    assert "短缺" in director._override_status[d.id].get("reason", "")


def test_l2_harass_no_units(director: Director, fake_facade: FakeFacade) -> None:
    """无空闲 Phoenix → status=on_hold"""
    fake_facade.selector_stub["Phoenix"] = []
    d = _make_tactical_directive(
        verb="harass",
        target="enemy_main",
        unit_count_hint=3,
        unit_type_hint=["Phoenix"],
        done_when={
            "kind": "enemy_killed_in_area",
            "area": "enemy_main",
            "unit_type": "Probe",
            "op": ">=",
            "value": 3,
        },
    )
    director._exec_tactical_objective(d, d.payload)
    assert director._override_status[d.id]["status"] == "on_hold"


def test_l2_squad_unit_count_hint_required(director: Director, fake_facade: FakeFacade) -> None:
    """B 类无 unit_count_hint → on_hold（LLM 契约要求必填，防御性兜底）"""
    d = _make_tactical_directive(verb="harass", target="enemy_main")  # no hint
    director._exec_tactical_objective(d, d.payload)
    assert director._override_status[d.id]["status"] == "on_hold"


# =========================================================================
# 未支持 verb
# =========================================================================


def test_l2_unsupported_verb_on_hold(director: Director, fake_facade: FakeFacade) -> None:
    """raze/regroup/split/drop MVP 不实现 → on_hold + warning"""
    d = _make_tactical_directive(
        verb="drop",
        target="enemy_main",
        unit_count_hint=2,
        unit_type_hint=["WarpPrism"],
    )
    director._exec_tactical_objective(d, d.payload)
    assert director._override_status[d.id]["status"] == "on_hold"
