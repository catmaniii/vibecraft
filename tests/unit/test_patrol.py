"""Task F：巡逻两点无限往返 单测。

覆盖：
- _tick_patrol：到达当前目标点 → 切换到另一点 + 发 move_to
- _tick_patrol：途中（未到达）→ 不切换，不发 move
- _tick_patrol：单位死了（get_unit_position None）→ 清除 pending_patrol entry
- _tick_patrol 切换双向（B→A）
- 端到端：persistent unit_claim verb=patrol + waypoints=[A,B] → _pending_patrol 注册
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade, UnitRole
from vibecraft.directives.models import Directive, UnitClaimPayload
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
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


def _make_bare_director(session: GameSession, facade: FakeFacade) -> Director:
    """不需要 LLM 的最简 director，直接测 tick/helper 方法。"""
    provider = MockLLMProvider(scripted=[])
    parser = IntentParser(
        provider,
        StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        ),
        session=session,
        my_race="protoss",
    )
    return Director(facade=facade, parser=parser, session=session)


# ---------------------------------------------------------------------------
# _tick_patrol 基础行为
# ---------------------------------------------------------------------------


def test_patrol_moves_to_first_waypoint_on_arrive(session: GameSession) -> None:
    """单位在 A 点（dist=0<4.0 ARRIVE）→ 切换 idx 1 → 发 move_to B。"""
    facade = FakeFacade()
    facade.unit_positions[6001] = (10.0, 10.0)  # 单位在 A
    director = _make_bare_director(session, facade)

    director._pending_patrol["d1"] = {
        "tag": 6001,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 0,
    }
    director._tick_patrol(now=1.0)

    # 到达 A（dist=0 < 4.0）→ idx 翻转为 1
    assert director._pending_patrol["d1"]["idx"] == 1
    moves = [a for a in facade.unit_actions if a["tag"] == 6001 and a["verb"] == "move_to"]
    assert moves, "应发出一次 move_to"
    assert moves[-1]["target"]["point"] == [90.0, 90.0]


def test_patrol_toggles_back_from_b_to_a(session: GameSession) -> None:
    """单位在 B 点（idx=1，dist=0<4.0）→ 切换 idx 0 → 发 move_to A。"""
    facade = FakeFacade()
    facade.unit_positions[6001] = (90.0, 90.0)  # 单位在 B
    director = _make_bare_director(session, facade)

    director._pending_patrol["d1"] = {
        "tag": 6001,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 1,
    }
    director._tick_patrol(now=1.0)

    assert director._pending_patrol["d1"]["idx"] == 0
    moves = [a for a in facade.unit_actions if a["tag"] == 6001 and a["verb"] == "move_to"]
    assert moves, "应发出一次 move_to"
    assert moves[-1]["target"]["point"] == [10.0, 10.0]


def test_patrol_no_toggle_when_in_transit(session: GameSession) -> None:
    """单位在途中（(50,50)，距 A/B 都远）→ idx 不变，不发 move。"""
    facade = FakeFacade()
    facade.unit_positions[6001] = (50.0, 50.0)  # 途中，离两点都远
    director = _make_bare_director(session, facade)

    director._pending_patrol["d1"] = {
        "tag": 6001,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 0,
    }
    director._tick_patrol(now=1.0)

    assert director._pending_patrol["d1"]["idx"] == 0  # 未到，不切换
    assert facade.unit_actions == []  # 不发 move


def test_patrol_dead_unit_cleanup(session: GameSession) -> None:
    """单位不在 unit_positions（死亡）→ pending_patrol entry 被清除。"""
    facade = FakeFacade()
    # 不注入 6001 位置 → get_unit_position 返回 None
    director = _make_bare_director(session, facade)

    director._pending_patrol["d1"] = {
        "tag": 6001,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 0,
    }
    director._tick_patrol(now=1.0)

    assert "d1" not in director._pending_patrol


def test_patrol_multiple_entries_independent(session: GameSession) -> None:
    """两个 patrol entry：只有到达目标的那个切换，另一个保持原 idx。"""
    facade = FakeFacade()
    facade.unit_positions[6001] = (10.0, 10.0)  # 在 A（idx=0）
    facade.unit_positions[6002] = (50.0, 50.0)  # 途中（idx=0）
    director = _make_bare_director(session, facade)

    director._pending_patrol["d1"] = {
        "tag": 6001,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 0,
    }
    director._pending_patrol["d2"] = {
        "tag": 6002,
        "points": [(10.0, 10.0), (90.0, 90.0)],
        "idx": 0,
    }
    director._tick_patrol(now=1.0)

    assert director._pending_patrol["d1"]["idx"] == 1  # 到达 → 切换
    assert director._pending_patrol["d2"]["idx"] == 0  # 途中 → 不变


# ---------------------------------------------------------------------------
# 端到端：通过 _assign_standing_order_units 注册
# ---------------------------------------------------------------------------


def _make_patrol_directive(
    directive_id: str,
    tag: int,
    pA: tuple[float, float],
    pB: tuple[float, float],
) -> Directive:
    """构造一个 persistent unit_claim verb=patrol + waypoints=[pA,pB] 的 Directive。"""
    ts_a = TargetSpec(kind=TargetKind.POINT, point=pA)
    ts_b = TargetSpec(kind=TargetKind.POINT, point=pB)
    # action.target 本身可以是 pA，waypoints 携带两点
    action_target = TargetSpec(kind=TargetKind.POINT, point=pA, waypoints=[ts_a, ts_b])
    task = Task(primary_action=Action(verb=Verb.PATROL, target=action_target))
    payload = UnitClaimPayload(
        selector=Selector(tag=tag),
        task=task,
        persistent=True,
    )
    return Directive(
        id=directive_id,
        payload=payload,
        issued_at=0.0,
        effective_at=0.0,
        priority=50,
    )


def test_patrol_register_via_unit_claim(session: GameSession) -> None:
    """端到端：_assign_standing_order_units 处理 persistent unit_claim verb=patrol。

    断言：
    - _pending_patrol 出现对应 did，points 正确解析
    - 单位 tag 被设为 LLM_CONTROLLED
    - 立即发出 move_to pA
    """
    facade = FakeFacade()
    # FakeFacade.resolve_selector(tag=6001) 直接返回 [6001]，无需 selector_stub
    director = _make_bare_director(session, facade)
    d = _make_patrol_directive("d_patrol_1", tag=6001, pA=(10.0, 20.0), pB=(80.0, 90.0))

    director._assign_standing_order_units(d)

    assert "d_patrol_1" in director._pending_patrol
    info = director._pending_patrol["d_patrol_1"]
    assert info["tag"] == 6001
    assert info["points"][0] == (10.0, 20.0)
    assert info["points"][1] == (80.0, 90.0)
    assert info["idx"] == 0

    # 确认也设了 LLM_CONTROLLED 角色
    assert facade.unit_roles.get(6001) == UnitRole.LLM_CONTROLLED

    # 确认立即发了 move_to pA
    moves = [a for a in facade.unit_actions if a["tag"] == 6001 and a["verb"] == "move_to"]
    assert moves, "注册时应立即发 move_to A"
    assert moves[0]["target"]["point"] == [10.0, 20.0]
