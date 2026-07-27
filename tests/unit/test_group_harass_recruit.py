"""group_harass recruit watcher 接线单测（#580）。

覆盖：
1. group_harass claim 新征募的 tag **不被下单体 action**（skip_action 接线）。
2. target_count cap 生效（target_count=2 时第 3 艘不入伍）。
3. bc_harass_groups 正确发布（tags / target / target_count）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 辅助：构造最小 Director stub
# ---------------------------------------------------------------------------


def _make_stub_director(bc_tags: list[int] | None = None):
    """构造最小 Director stub，可注入 BC tags。

    返回：(director, facade, execute_calls_log, vibecraft_ns)
    """
    from vibecraft.bot.director import Director
    from vibecraft.bot.facade import FakeFacade

    facade = FakeFacade()
    if bc_tags is not None:
        facade.selector_stub["BattleCruiser"] = list(bc_tags)

    d: Any = object.__new__(Director)
    d.facade = facade
    d._override_status: dict[str, dict] = {}
    d._event_queue: list[dict] = []
    d._event_callback = None
    d.session = MagicMock()
    d._voice_groups: dict[int, set] = {}
    d._task_chains: dict[str, set] = {}
    d._chain_structures: dict[str, int] = {}
    d._unit_semantics: dict[int, dict] = {}
    d._unit_states: dict[int, str] = {}
    d.parser = SimpleNamespace(my_race=None)
    d._stealth_manager = MagicMock()
    d._directive_to_cell_id: dict[str, int] = {}
    d._cell_id_to_directive_id: dict[int, str] = {}
    d._pending_salvage_tags: set[int] = set()
    d._repair_orders: dict[str, object] = {}
    d.standing_orders: list = []
    d._standing_order_tags: dict[str, set] = {}
    d._recruit_watchers: dict[str, dict] = {}
    d._in_flight: dict[str, object] = {}
    d._bc_harass_group_auto_created: bool = False

    # 跟踪 execute_unit_action 调用
    execute_calls: list[dict] = []

    def _mock_execute(unit_tag: int, verb: str, target=None, ability_id=None):
        execute_calls.append({"unit_tag": unit_tag, "verb": verb})

    facade.execute_unit_action = _mock_execute
    facade.set_unit_role = MagicMock()

    def _supersede(tags, keep_id, now):
        pass

    d._supersede_conflicting_moves = _supersede

    # Inject knowledge.vibecraft
    vibecraft_ns = SimpleNamespace(bc_harass_groups=[])
    d._bot = SimpleNamespace(
        knowledge=SimpleNamespace(vibecraft=vibecraft_ns),
        active_recipe="",
    )

    return d, facade, execute_calls, vibecraft_ns


def _make_group_harass_directive(
    bc_tags: list[int] | None = None,
    target_named_spot: str | None = None,
    target_count: int | None = None,
    recruit_new: bool = True,
):
    """構造一条 GROUP_HARASS unit_claim directive。"""
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    action_target = None
    if target_named_spot is not None:
        action_target = TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=target_named_spot)

    sel = Selector(unit_type="BattleCruiser") if bc_tags is None else Selector(tags=bc_tags)

    payload = UnitClaimPayload(
        selector=sel,
        task=Task(primary_action=Action(verb=Verb.GROUP_HARASS, target=action_target)),
        persistent=True,
        recruit_new=recruit_new,
        target_count=target_count,
    )
    return Directive(payload=payload, issued_at=0.0, issued_by="bot_internal", source_text="test")


# ---------------------------------------------------------------------------
# 1. group_harass 新征募不下单体 action
# ---------------------------------------------------------------------------


def test_group_harass_recruit_skips_execute_unit_action() -> None:
    """group_harass claim 招募新 BC → set_unit_role 被调，但 execute_unit_action **不被调**。"""
    d, facade, execute_calls, _ = _make_stub_director(bc_tags=[101, 202])

    directive = _make_group_harass_directive(recruit_new=True)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = set()
    d._recruit_watchers[directive.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": set(),  # 两个 BC 都是新的
    }

    d._tick_recruit_watchers(now=5.0)

    # execute_unit_action 不应被调（group_harass 必须进 skip_action）
    assert execute_calls == [], f"group_harass 不得下单体 action，实际调用: {execute_calls}"
    # set_unit_role 应被调（标 LLM_CONTROLLED）
    assert facade.set_unit_role.call_count == 2, (
        f"2 个新 BC 应各调一次 set_unit_role，实际 {facade.set_unit_role.call_count}"
    )
    # tag 集里有两艘 BC
    enrolled = d._standing_order_tags[directive.id]
    assert enrolled == {101, 202}, f"两艘 BC 应入伍，实际 {enrolled}"


# ---------------------------------------------------------------------------
# 2. target_count cap 生效
# ---------------------------------------------------------------------------


def test_group_harass_recruit_cap_target_count_2() -> None:
    """target_count=2：3 艘 BC 只入伍 2 艘，第 3 艘不入伍。"""
    d, _facade, execute_calls, _ = _make_stub_director(bc_tags=[101, 202, 303])

    directive = _make_group_harass_directive(recruit_new=True, target_count=2)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = set()
    d._recruit_watchers[directive.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": set(),
    }

    d._tick_recruit_watchers(now=5.0)

    enrolled = d._standing_order_tags[directive.id]
    assert len(enrolled) == 2, f"cap=2 时只能入伍 2 艘，实际 {len(enrolled)}"
    # group_harass 不下 action
    assert execute_calls == []


def test_group_harass_recruit_cap_zero_enrolls_nothing() -> None:
    """target_count=0：watcher 暂停，不入伍任何 BC（resolve_selector 也跳过）。"""
    d, facade, _execute_calls, _ = _make_stub_director(bc_tags=[101, 202])

    directive = _make_group_harass_directive(recruit_new=True, target_count=0)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = set()
    d._recruit_watchers[directive.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": set(),
    }

    # Stub resolve_selector to track calls
    resolve_calls: list[str] = []
    original_resolve = facade.resolve_selector

    def _track_resolve(unit_type=None, **kwargs):
        resolve_calls.append(unit_type or "")
        return original_resolve(unit_type=unit_type, **kwargs)

    facade.resolve_selector = _track_resolve

    d._tick_recruit_watchers(now=5.0)

    enrolled = d._standing_order_tags[directive.id]
    assert len(enrolled) == 0, f"target_count=0 不应入伍，实际 {enrolled}"
    assert resolve_calls == [], f"target_count=0 应跳过 resolve_selector，实际调了 {resolve_calls}"


def test_group_harass_recruit_cap_partially_full() -> None:
    """已有 1 艘入伍，target_count=2：3 艘新 BC 只补 1 艘。"""
    d, _facade, _execute_calls, _ = _make_stub_director(bc_tags=[101, 202, 303])

    directive = _make_group_harass_directive(recruit_new=True, target_count=2)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = {101}  # 已有 1 艘
    d._recruit_watchers[directive.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101},  # 101 已知
    }

    d._tick_recruit_watchers(now=5.0)

    enrolled = d._standing_order_tags[directive.id]
    assert len(enrolled) == 2, f"已有1 + 补1 = 2，实际 {len(enrolled)}"


# ---------------------------------------------------------------------------
# 3. bc_harass_groups 正确发布
# ---------------------------------------------------------------------------


def test_publish_bc_harass_groups_single_claim() -> None:
    """一条 group_harass claim → bc_harass_groups = [{"did":..., "tags":..., "target":..., "target_count":...}]。"""
    d, _, _, vibecraft_ns = _make_stub_director()

    directive = _make_group_harass_directive(target_named_spot="enemy_third", target_count=3)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = {101, 202}

    d._publish_bc_harass_groups()

    groups = vibecraft_ns.bc_harass_groups
    assert len(groups) == 1, f"期望 1 个群，实际 {len(groups)}"
    g = groups[0]
    assert g["did"] == directive.id
    assert g["tags"] == {101, 202}
    assert g["target"] == "enemy_third"
    assert g["target_count"] == 3


def test_publish_bc_harass_groups_auto_target() -> None:
    """target=None（auto picker）→ bc_harass_groups[0]['target'] == None。"""
    d, _, _, vibecraft_ns = _make_stub_director()

    directive = _make_group_harass_directive(target_named_spot=None, target_count=None)
    d.standing_orders.append(directive)
    d._standing_order_tags[directive.id] = {101}

    d._publish_bc_harass_groups()

    groups = vibecraft_ns.bc_harass_groups
    assert len(groups) == 1
    assert groups[0]["target"] is None
    assert groups[0]["target_count"] is None


def test_publish_bc_harass_groups_empty_when_no_claim() -> None:
    """无 group_harass claim → bc_harass_groups == []。"""
    d, _, _, vibecraft_ns = _make_stub_director()

    d._publish_bc_harass_groups()

    assert vibecraft_ns.bc_harass_groups == [], (
        f"无 claim 应为 []，实际 {vibecraft_ns.bc_harass_groups}"
    )


def test_publish_bc_harass_groups_excludes_harass_workers() -> None:
    """harass_workers claim 不进 bc_harass_groups（只收 group_harass）。"""
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector
    from vibecraft.directives.task import Action, Task, Verb

    d, _, _, vibecraft_ns = _make_stub_director()

    # harass_workers claim（凤凰骚扰）
    hw_payload = UnitClaimPayload(
        selector=Selector(unit_type="Phoenix"),
        task=Task(primary_action=Action(verb=Verb.HARASS_WORKERS, target=None)),
        persistent=True,
    )
    hw_dir = Directive(payload=hw_payload, issued_at=0.0, issued_by="voice", source_text="凤凰")
    d.standing_orders.append(hw_dir)
    d._standing_order_tags[hw_dir.id] = {999}

    d._publish_bc_harass_groups()

    assert vibecraft_ns.bc_harass_groups == [], "harass_workers 不得进 bc_harass_groups"
