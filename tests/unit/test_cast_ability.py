"""tests/unit/test_cast_ability.py

验证 cast_ability_on_units 路径:
1. FakeFacade 记录 cast_ability_on_units 调用
2. Director._apply_unit_claim 收到 verb=cast_ability + ability_id 非 chrono 时
   调用 facade.cast_ability_on_units（而不是 execute_unit_action）
3. 合白球路径（MORPH_ARCHON）: 偶数 HT 配对、奇数 HT 最后一个跳过
4. LLM prompt / few_shot 包含 MORPH_ARCHON 关键词
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.models import Directive, UnitClaimPayload
from vibecraft.directives.scope import Selector, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=fake_facade, parser=parser, session=session)


def _make_cast_directive(
    ability_id: str,
    unit_type: str = "HighTemplar",
    target_kind: str = "unit_type",
    count: int | None = None,
) -> Directive:
    """构造 unit_claim + cast_ability directive。"""
    # MORPH_ARCHON 合白球：target 指向 HighTemplar 自身（两两配对；后端忽略外部 target）
    target = TargetSpec(
        kind=target_kind, unit_type=unit_type if target_kind == "unit_type" else None
    )
    action = Action(verb=Verb.CAST_ABILITY, target=target, ability_id=ability_id)
    task = Task(primary_action=action)
    selector = Selector(unit_type=unit_type, count=count)
    payload = UnitClaimPayload(selector=selector, task=task, persistent=False)
    return Directive(payload=payload, issued_at=10.0, source_text="合白球")


# ---------------------------------------------------------------------------
# FakeFacade 记录测试
# ---------------------------------------------------------------------------


def test_fake_facade_records_cast_ability_on_units():
    """FakeFacade.cast_ability_on_units 调用被记录到 ability_casts。"""
    f = FakeFacade()
    n = f.cast_ability_on_units(
        ability_id="MORPH_ARCHON",
        unit_type="HighTemplar",
        target_kind="self",
        count=None,
    )
    assert len(f.ability_casts) == 1
    rec = f.ability_casts[0]
    assert rec[0] == "MORPH_ARCHON"
    assert rec[1] == "HighTemplar"
    assert rec[2] == "self"
    assert rec[3] is None
    # mock 对 MORPH_ARCHON + count=None 返回 1
    assert n == 1


def test_fake_facade_cast_ability_non_morph():
    """PSISTORM 路径：mock 返回 count or 1。"""
    f = FakeFacade()
    n = f.cast_ability_on_units(
        ability_id="PSISTORM_PSISTORM",
        unit_type="HighTemplar",
        count=3,
    )
    assert n == 3
    assert f.ability_casts[0][0] == "PSISTORM_PSISTORM"


def test_fake_facade_records_calls_entry():
    """cast_ability_on_units 也出现在通用 calls 列表里。"""
    f = FakeFacade()
    f.cast_ability_on_units(ability_id="MORPH_ARCHON", unit_type="HighTemplar")
    methods = [c.method for c in f.calls]
    assert "cast_ability_on_units" in methods


# ---------------------------------------------------------------------------
# Director dispatch 测试
# ---------------------------------------------------------------------------


def test_director_dispatch_cast_ability_calls_facade(
    director: Director, fake_facade: FakeFacade
) -> None:
    """_apply_unit_claim(verb=cast_ability, ability=MORPH_ARCHON) 调 facade.cast_ability_on_units。"""
    d = _make_cast_directive(ability_id="MORPH_ARCHON", unit_type="HighTemplar")
    director._apply_to_facade(d, now=100.0)

    # cast_ability_on_units 被调用一次
    assert len(fake_facade.ability_casts) == 1
    call = fake_facade.ability_casts[0]
    assert call[0] == "MORPH_ARCHON"
    assert call[1] == "HighTemplar"

    # execute_unit_action 不应被调用（MORPH_ARCHON 不走 move 路径）
    assert len(fake_facade.unit_actions) == 0


def test_director_dispatch_cast_ability_directive_done(
    director: Director, fake_facade: FakeFacade
) -> None:
    """cast_ability 执行后 directive _override_status 标记为 done。"""
    d = _make_cast_directive(ability_id="MORPH_ARCHON", unit_type="HighTemplar")
    director._apply_to_facade(d, now=100.0)
    status_entry = director._override_status.get(d.id, {})
    assert status_entry.get("status") == "done"


def test_director_dispatch_non_morph_ability(director: Director, fake_facade: FakeFacade) -> None:
    """PSISTORM_PSISTORM 走同一通用路径,facade.cast_ability_on_units 被调用。"""
    target = TargetSpec(kind="named_spot", named_spot="enemy_main")
    action = Action(verb=Verb.CAST_ABILITY, target=target, ability_id="PSISTORM_PSISTORM")
    task = Task(primary_action=action)
    selector = Selector(unit_type="HighTemplar")
    payload = UnitClaimPayload(selector=selector, task=task, persistent=False)
    d = Directive(payload=payload, issued_at=10.0, source_text="电兵放风暴")
    director._apply_to_facade(d, now=100.0)

    assert len(fake_facade.ability_casts) == 1
    assert fake_facade.ability_casts[0][0] == "PSISTORM_PSISTORM"
    assert len(fake_facade.unit_actions) == 0


def test_director_cast_ability_point_target_tactical_jump(
    director: Director, fake_facade: FakeFacade
) -> None:
    """大舰传送回家 EFFECT_TACTICALJUMP(对点施放)：director 解析 named_spot→落点，传 target_point 给
    facade（不是走过去）。修 #3：原来 cast_ability 不传点 → 大舰走回基地而非 Tactical Jump。"""
    director._resolve_target_spec_point = lambda spec: (50.0, 60.0)  # type: ignore[assignment]
    target = TargetSpec(kind="named_spot", named_spot="main")
    action = Action(verb=Verb.CAST_ABILITY, target=target, ability_id="EFFECT_TACTICALJUMP")
    task = Task(primary_action=action)
    payload = UnitClaimPayload(
        selector=Selector(unit_type="BattleCruiser"), task=task, persistent=False
    )
    d = Directive(payload=payload, issued_at=10.0, source_text="所有大舰传送回基地")
    director._apply_to_facade(d, now=100.0)

    assert len(fake_facade.ability_casts) == 1
    call = fake_facade.ability_casts[0]
    assert call[0] == "EFFECT_TACTICALJUMP"
    assert call[4] == (50.0, 60.0)  # target_point 被传(对点 Tactical Jump，不是自施放/走回去)
    assert len(fake_facade.unit_actions) == 0  # 不走 move


def test_director_cast_ability_with_count(director: Director, fake_facade: FakeFacade) -> None:
    """selector.count=2 传递到 facade.cast_ability_on_units 的 count 参数。"""
    d = _make_cast_directive(ability_id="MORPH_ARCHON", unit_type="HighTemplar", count=2)
    director._apply_to_facade(d, now=100.0)

    assert fake_facade.ability_casts[0][3] == 2


def test_director_chrono_path_unaffected(director: Director, fake_facade: FakeFacade) -> None:
    """chrono boost 路径(EffectChronoBoostEnergyCost)走 cast_chrono_boost_on_structure,
    不走 cast_ability_on_units。"""
    target = TargetSpec(kind="unit_type", unit_type="Forge")
    action = Action(verb=Verb.CAST_ABILITY, target=target, ability_id="EffectChronoBoostEnergyCost")
    task = Task(primary_action=action)
    selector = Selector(unit_type="Nexus", count=1)
    payload = UnitClaimPayload(selector=selector, task=task, persistent=False)
    d = Directive(payload=payload, issued_at=10.0, source_text="给 BF chrono")

    director._apply_to_facade(d, now=100.0)

    # chrono 走自己的路径
    assert len(fake_facade.chrono_boost_casts) == 1
    assert fake_facade.chrono_boost_casts[0][0] == "Forge"
    # cast_ability_on_units 不应被调用
    assert len(fake_facade.ability_casts) == 0


# ---------------------------------------------------------------------------
# LLM prompt 单测
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def few_shot_text() -> str:
    from vibecraft.llm.prompt import build_few_shot

    return build_few_shot()


@pytest.fixture(scope="module")
def system_prompt_text() -> str:
    from vibecraft.llm.prompt import build_system_prompt
    from vibecraft.strategy.aliases import AliasTable

    aliases = AliasTable.from_dict({})
    return build_system_prompt(aliases)


def test_few_shot_contains_morph_archon(few_shot_text: str) -> None:
    """few_shot 含 MORPH_ARCHON ability_id 真名。"""
    assert "MORPH_ARCHON" in few_shot_text


def test_few_shot_contains_psistorm(few_shot_text: str) -> None:
    """few_shot 含 PSISTORM_PSISTORM ability_id 真名。"""
    assert "PSISTORM_PSISTORM" in few_shot_text


def test_few_shot_contains_highTemplar_selector(few_shot_text: str) -> None:
    """few_shot 含 HighTemplar selector 用法（合白球）。"""
    assert "HighTemplar" in few_shot_text


def test_rules_contains_morph_archon(system_prompt_text: str) -> None:
    """rules.md 含 MORPH_ARCHON 真名。"""
    assert "MORPH_ARCHON" in system_prompt_text


def test_rules_contains_cast_ability_table(system_prompt_text: str) -> None:
    """rules.md 含 cast_ability ability_id 真名表段落。"""
    assert "ability_id" in system_prompt_text
    assert "合白球" in system_prompt_text or "MORPH_ARCHON" in system_prompt_text


def test_rules_cast_ability_verb_includes_archon_hint(system_prompt_text: str) -> None:
    """verb 表中 cast_ability 行含合白球/合 Archon 提示。"""
    assert "合白球" in system_prompt_text or "合 Archon" in system_prompt_text
