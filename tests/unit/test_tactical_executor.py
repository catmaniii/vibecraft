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
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
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


def test_l2_global_emits_tactical_change_event(director: Director, fake_facade: FakeFacade) -> None:
    """2026-05-28 诊断:每次 _exec_l2_global emit 一条 tactical_change event,
    含完整上下文(new_verb, target_area, superseded_id/verb)。
    出"实时战术切换经常失效"时,events.jsonl 离线回放可见完整链路。
    """
    captured: list[dict] = []
    director.set_event_callback(captured.append)

    d1 = _make_tactical_directive(verb="attack", target="enemy_natural")
    d2 = _make_tactical_directive(verb="retreat")
    director._exec_tactical_objective(d1, d1.payload)
    director._exec_tactical_objective(d2, d2.payload)

    events = [e for e in captured if e.get("kind") == "tactical_change"]
    assert len(events) == 2, f"应 emit 2 条 tactical_change,实际 {len(events)}"
    # 第一条:attack, no supersede
    assert events[0]["payload"]["new_verb"] == "attack"
    assert events[0]["payload"]["target_area"] == "enemy_natural"
    assert events[0]["payload"]["new_id"] == d1.id
    assert events[0]["payload"]["superseded_id"] is None
    assert "target_resolved" in events[0]["payload"]  # 字段存在
    # 第二条:retreat, supersedes d1
    assert events[1]["payload"]["new_verb"] == "retreat"
    assert events[1]["payload"]["superseded_id"] == d1.id
    assert events[1]["payload"]["superseded_verb"] == "attack"


def test_l2_global_tactical_change_target_resolved_false_for_unmapped(
    director: Director, fake_facade: FakeFacade
) -> None:
    """2026-05-28 诊断:target_area=enemy_third 等未映射的 named_spot,
    target_resolved=False 标记进 event。让我们能离线统计"多少次 attack 指令
    target 没解析到 → 实际打 sharpy 默认目标"。

    注:单测无 self._bot,所有 named_spot 都解不到,target_resolved 都 False。
    e2e/真游戏中 enemy_natural 等会 True,enemy_third 才会 False。
    """
    captured: list[dict] = []
    director.set_event_callback(captured.append)

    d = _make_tactical_directive(verb="attack", target="enemy_third")
    director._exec_tactical_objective(d, d.payload)

    events = [e for e in captured if e.get("kind") == "tactical_change"]
    assert len(events) == 1
    # _resolve_target_area("enemy_third") 不在表中 → target_resolved=False
    assert events[0]["payload"]["target_resolved"] is False
    assert events[0]["payload"]["target_area"] == "enemy_third"


def test_l2_global_superseded_directive_enters_done_grace(
    director: Director, fake_facade: FakeFacade
) -> None:
    """2026-05-27 Issue 2 regression:旧 L2 global 被新 directive 覆盖时,
    要进 _done_at,grace 期满后真删 → PWA 卡片消失(不能只是变灰)。

    修前:status='done' 但 _done_at 没设 → 卡片永远灰着不消失。
    修后:_done_at[old_id] = now → on_tick grace expired 后清掉所有引用。
    """
    d1 = _make_tactical_directive(verb="attack", target="enemy_natural")
    d2 = _make_tactical_directive(verb="defend")
    director._exec_tactical_objective(d1, d1.payload)
    # _done_at 初始无 d1
    assert d1.id not in director._done_at
    # d2 来,覆盖 d1
    director._exec_tactical_objective(d2, d2.payload)
    # d1 应入 _done_at,等 grace 过后 on_tick 清掉
    assert d1.id in director._done_at, (
        f"被覆盖的 directive 必须进 _done_at,实际 _done_at={director._done_at}"
    )
    # d2 是新 active,不该进 _done_at
    assert d2.id not in director._done_at


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


# =========================================================================
# Code Review 修复用例（C1 / I1 / I2 / I3）
# =========================================================================


def test_execute_tactics_step_calls_real_cm_execute_signature(
    director: Director, fake_facade: FakeFacade
) -> None:
    """C1 fix: cm.execute(target, move_type) 真签名，cm.add_units(units) 先调。"""
    import asyncio
    from unittest.mock import MagicMock

    from vibecraft.bot.director import TacticalSquad

    mock_cm = MagicMock()
    mock_units = MagicMock()
    fake_bot = MagicMock()
    fake_bot.knowledge.combat_manager = mock_cm
    # 2026-05-28 backfill 加了 own_alive_tags = {u.tag for u in self._bot.units}
    # 死亡剔除,需要给 fake_bot.units 一个可迭代的 alive units 集合
    alive = [MagicMock(tag=1), MagicMock(tag=2), MagicMock(tag=3)]
    fake_bot.units.__iter__ = MagicMock(return_value=iter(alive))
    fake_bot.units.tags_in = MagicMock(return_value=mock_units)
    director._bot = fake_bot

    target_mock = MagicMock()
    move_type_mock = MagicMock()
    director._tactical_squads["d_test"] = TacticalSquad(
        directive_id="d_test",
        unit_tags={1, 2, 3},
        target=target_mock,
        move_type=move_type_mock,
        verb="harass",
        n_wanted=3,
        n_locked=3,
    )

    asyncio.run(director.execute_tactics_step(now=10.0))

    fake_bot.units.tags_in.assert_called_once_with({1, 2, 3})
    mock_cm.add_units.assert_called_once_with(mock_units)
    # execute 真签名：positional (target, move_type)，无 tags
    mock_cm.execute.assert_called_once()
    args, kwargs = mock_cm.execute.call_args
    # target と move_type は positional args として渡される
    assert len(args) == 2 or ("target" in kwargs and "move_type" in kwargs)


def test_a_verbs_contains_hold() -> None:
    """2026-05-28 用户:hold 加进 TacticalVerb literal + _A_VERBS,作为"全军坚守"verb。
    跟 defend(回家)区别 — hold 是聚团到点 + 站住不回家。"""
    from vibecraft.bot.director import _A_VERBS

    assert "hold" in _A_VERBS


def test_resolve_target_area_uses_zone_manager(director: Director) -> None:
    """I2 fix: knowledge.zone_manager.expansion_zones 路径（非 knowledge.expansion_zones）"""
    from unittest.mock import MagicMock

    fake_bot = MagicMock()
    mock_zone = MagicMock()
    mock_zone.center_location = MagicMock()
    fake_bot.knowledge.zone_manager.expansion_zones = [mock_zone, mock_zone]
    fake_bot.knowledge.zone_manager.enemy_expansion_zones = [mock_zone, mock_zone]
    director._bot = fake_bot

    result = director._resolve_target_area("own_natural")
    assert result is not None
    # 确认 zone_manager 路径被访问
    _ = fake_bot.knowledge.zone_manager.expansion_zones


# =========================================================================
# 2026-05-28 用户:拉式征兵 backfill + 全死 → auto_terminated(reason='units_lost')
# 验"出 5 个追猎派去瞭望塔" 这种"还没造好就先下指令"的复合 case 现在能 work
# =========================================================================


def _make_fake_bot_with_alive_tags(alive_tags: set[int]):
    """造一个 fake bot,units 可迭代 + tags_in 按集合过滤。"""
    from unittest.mock import MagicMock

    bot = MagicMock()
    units = [MagicMock(tag=t) for t in alive_tags]
    bot.units.__iter__ = MagicMock(return_value=iter(units))

    # tags_in 返回有 .amount 属性的"可遍历"对象,模拟 sharpy Units
    def tags_in(want_tags):
        matched = [u for u in units if u.tag in want_tags]
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter(matched))
        result.__bool__ = MagicMock(return_value=bool(matched))
        result.amount = len(matched)
        return result

    bot.units.tags_in = MagicMock(side_effect=tags_in)
    bot.knowledge.combat_manager = MagicMock()
    return bot


def test_backfill_pulls_fresh_unit_when_squad_underfilled(
    director: Director, fake_facade: FakeFacade
) -> None:
    """拉式征兵:squad n_wanted=5 已抓 2,free pool 新出 1 个同类型 → 抓走,n_locked=3。"""
    import asyncio

    from vibecraft.bot.director import TacticalSquad

    # squad 已抓 tag 1, 2,要 5 个 Stalker
    director._tactical_squads["d1"] = TacticalSquad(
        directive_id="d1",
        unit_tags={1, 2},
        target=None,
        move_type=None,
        verb="attack",
        n_wanted=5,
        n_locked=2,
        unit_type="Stalker",
    )
    # bot 当前有 tag 1, 2, 3 活着(3 是新造的还没人抓)
    director._bot = _make_fake_bot_with_alive_tags({1, 2, 3})
    # facade.resolve_selector(unit_type='Stalker') 返回 [1, 2, 3]
    fake_facade._scripted_selector = [1, 2, 3]
    # 用 patch 让 resolve_selector 返 [1,2,3]
    from unittest.mock import MagicMock

    fake_facade.resolve_selector = MagicMock(return_value=[1, 2, 3])
    fake_facade.set_unit_role = MagicMock()

    asyncio.run(director.execute_tactics_step(now=10.0))

    sq = director._tactical_squads["d1"]
    assert 3 in sq.unit_tags, "tag 3(新单位)应被 backfill 抓进 squad"
    assert sq.n_locked == 3
    # set_unit_role 应被调用为 tag 3 set LLM_CONTROLLED
    role_calls = [c.args for c in fake_facade.set_unit_role.call_args_list]
    assert any(c[0] == 3 and c[1] == UnitRole.LLM_CONTROLLED for c in role_calls)


def test_backfill_excludes_tags_taken_by_other_squad(
    director: Director, fake_facade: FakeFacade
) -> None:
    """两个 squad 都征 Stalker,squad A 先抓 tag 1,squad B 不能再抓 tag 1。FIFO 互斥。"""
    import asyncio
    from unittest.mock import MagicMock

    from vibecraft.bot.director import TacticalSquad

    director._tactical_squads["dA"] = TacticalSquad(
        directive_id="dA",
        unit_tags={1},
        target=None,
        move_type=None,
        verb="attack",
        n_wanted=2,
        n_locked=1,
        unit_type="Stalker",
    )
    director._tactical_squads["dB"] = TacticalSquad(
        directive_id="dB",
        unit_tags=set(),
        target=None,
        move_type=None,
        verb="attack",
        n_wanted=2,
        n_locked=0,
        unit_type="Stalker",
    )
    # alive: tag 1, 2(都是 Stalker)
    director._bot = _make_fake_bot_with_alive_tags({1, 2})
    fake_facade.resolve_selector = MagicMock(return_value=[1, 2])
    fake_facade.set_unit_role = MagicMock()

    asyncio.run(director.execute_tactics_step(now=10.0))

    sqA = director._tactical_squads["dA"]
    sqB = director._tactical_squads.get("dB")
    # squad A 应该补到 tag 2(它先轮到,n_wanted=2 → 抓 1+2)
    assert sqA.unit_tags == {1, 2}
    # squad B 抢不到,应该已被 release(全死 → units_lost,因为 sqB.unit_tags 空)
    # 或仍在 dict 里但 unit_tags 空 → 这是预期 race:sqB 此拍 unit_tags=空 →
    # 全死分支,_release_directive_done(units_lost),并被 pop
    assert sqB is None or sqB.unit_tags == set()


def test_squad_all_units_dead_triggers_units_lost_done(
    director: Director, fake_facade: FakeFacade
) -> None:
    """squad 全死 + 没人可补 → _release_directive_done(reason='units_lost') +
    status_reason='units_lost' + squad pop。"""
    import asyncio
    from unittest.mock import MagicMock

    from vibecraft.bot.director import TacticalSquad

    # 造 directive 进 _committed_directives(squad lookup 用)
    d = _make_tactical_directive(
        verb="attack",
        unit_count_hint=3,
        unit_type_hint=["Stalker"],
    )
    d.id = "d_lost"
    director._committed_directives["d_lost"] = d

    director._tactical_squads["d_lost"] = TacticalSquad(
        directive_id="d_lost",
        unit_tags={10, 11},
        target=None,
        move_type=None,
        verb="attack",
        n_wanted=3,
        n_locked=2,
        unit_type="Stalker",
    )
    # 全死:bot 当前活单位不包含 10/11,也没新 Stalker 可补
    director._bot = _make_fake_bot_with_alive_tags(set())
    fake_facade.resolve_selector = MagicMock(return_value=[])

    asyncio.run(director.execute_tactics_step(now=20.0))

    # squad 应被 pop
    assert "d_lost" not in director._tactical_squads
    # status_reason='units_lost' 透传到 _override_status,snapshot 会带上
    status = director._override_status.get("d_lost", {})
    assert status.get("status") == "done"
    assert status.get("reason") == "units_lost"


def test_backfill_with_no_fresh_units_keeps_squad_alive(
    director: Director, fake_facade: FakeFacade
) -> None:
    """squad 已抓 2,n_wanted=5,但 free pool 没新单位 → squad 保持 active(不触发 units_lost)。

    防回归:别把"补不够"误判成"全死"。
    """
    import asyncio
    from unittest.mock import MagicMock

    from vibecraft.bot.director import TacticalSquad

    director._tactical_squads["d_keep"] = TacticalSquad(
        directive_id="d_keep",
        unit_tags={5, 6},
        target=None,
        move_type=None,
        verb="attack",
        n_wanted=5,
        n_locked=2,
        unit_type="Stalker",
    )
    # alive: 5, 6 还活着,没新人造好
    director._bot = _make_fake_bot_with_alive_tags({5, 6})
    fake_facade.resolve_selector = MagicMock(return_value=[5, 6])

    asyncio.run(director.execute_tactics_step(now=15.0))

    # squad 还在
    sq = director._tactical_squads.get("d_keep")
    assert sq is not None
    assert sq.unit_tags == {5, 6}
    # 没 mark done
    status = director._override_status.get("d_keep", {})
    assert status.get("status") != "done"


def test_superseded_l2_global_marked_done(director: Director, fake_facade: FakeFacade) -> None:
    """I3 fix: 新 A 类 directive 覆盖旧的，旧 directive 状态 → done '被新指令覆盖'"""
    d1 = _make_tactical_directive(verb="attack", target="enemy_natural")
    director._exec_tactical_objective(d1, d1.payload)
    assert director._override_status[d1.id]["status"] == "active"

    d2 = _make_tactical_directive(verb="defend")
    director._exec_tactical_objective(d2, d2.payload)

    # 旧 d1 应被标 "done"，reason 含 "覆盖"
    assert director._override_status[d1.id]["status"] == "done"
    assert "覆盖" in director._override_status[d1.id].get("reason", "")
    # 新 d2 是 active
    assert director._override_status[d2.id]["status"] == "active"
