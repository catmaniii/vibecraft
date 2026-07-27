"""group_harass 语音控制 · 幂等更新 + partial-release + UI 群卡 · 单测（#580 Chunk C）。

覆盖：
1. 幂等更新：submit 第二条 group_harass → 不新建 standing_order，更新 target_count。
2. partial-release target_count 3→1：释放 2 艘，优先满血的，从 seen 移除。
3. target_count=0：释放全部 BC，claim 留存（不删）。
4. UI 群卡：group_harass claim 生成 type="group_harass" 卡，display 含艘数 + i18n。
5. 英文门：group_harass 卡 display 在 en locale 下不含中文。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 辅助：构造最小 Director stub
# ---------------------------------------------------------------------------


def _make_stub_director(bc_tags: list[int] | None = None) -> tuple:
    """构造最小 Director stub，可注入 BC tags。

    返回：(director, facade, release_calls, vibecraft_ns)
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
    d._displaced: dict[str, dict] = {}
    d._bc_harass_group_auto_created: bool = False
    d._lang = "zh"
    d._loc = MagicMock()
    d._loc.unit.return_value = "大件"

    # 跟踪 release_unit_role 调用
    release_calls: list[int] = []
    facade.set_unit_role = MagicMock()

    def _mock_release(tag: int) -> None:
        release_calls.append(tag)

    facade.release_unit_role = _mock_release

    def _supersede(tags, keep_id, now):
        pass

    d._supersede_conflicting_moves = _supersede

    def _restore_unit_to_prior(tag, prior_id):
        return False  # 始终交回 bot

    d._restore_unit_to_prior = _restore_unit_to_prior

    # Inject knowledge.vibecraft
    vibecraft_ns = SimpleNamespace(bc_harass_groups=[])
    d._bot = SimpleNamespace(
        knowledge=SimpleNamespace(vibecraft=vibecraft_ns),
        active_recipe="",
    )

    return d, facade, release_calls, vibecraft_ns


def _make_gh_directive(
    target_named_spot: str | None = None,
    target_count: int | None = None,
    recruit_new: bool = True,
):
    """构造一条 GROUP_HARASS unit_claim directive。"""
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    action_target = None
    if target_named_spot is not None:
        action_target = TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=target_named_spot)

    payload = UnitClaimPayload(
        selector=Selector(unit_type="BattleCruiser"),
        task=Task(primary_action=Action(verb=Verb.GROUP_HARASS, target=action_target)),
        persistent=True,
        recruit_new=recruit_new,
        target_count=target_count,
    )
    return Directive(payload=payload, issued_at=0.0, issued_by="bot_internal", source_text="test")


# ---------------------------------------------------------------------------
# 1. 幂等更新：第二条 group_harass claim 更新现有，不新建
# ---------------------------------------------------------------------------


def test_upsert_does_not_create_second_standing_order() -> None:
    """submit 第二条 group_harass claim → standing_orders 仍只有 1 条，target_count 被更新。"""
    d, _, _, _ = _make_stub_director()

    # 第一条：target_count=None（无上限）
    first = _make_gh_directive(target_count=None)
    d.standing_orders.append(first)
    d._standing_order_tags[first.id] = {101, 202, 303}
    d._recruit_watchers[first.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101, 202, 303},
    }

    # 第二条：target_count=2（减到2艘）
    second = _make_gh_directive(target_count=2)
    upserted = d._try_upsert_group_harass(second, now=10.0)

    assert upserted is True, "应返回 True（找到现有 claim 并更新）"
    assert len(d.standing_orders) == 1, "不得新建第二条 standing_order"
    assert first.payload.target_count == 2, "现有 claim 的 target_count 应更新为 2"


def test_upsert_updates_target_named_spot() -> None:
    """第二条 group_harass 指定新 target → 现有 claim 的 target 被更新。"""
    d, _, _, _ = _make_stub_director()

    first = _make_gh_directive(target_named_spot=None, target_count=None)
    d.standing_orders.append(first)
    d._standing_order_tags[first.id] = {101}
    d._recruit_watchers[first.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101},
    }

    second = _make_gh_directive(target_named_spot="enemy_third", target_count=None)
    d._try_upsert_group_harass(second, now=10.0)

    tgt = first.payload.task.primary_action.target
    assert tgt is not None, "target 应更新为 enemy_third"
    assert getattr(tgt, "named_spot", None) == "enemy_third"


def test_upsert_returns_false_when_no_existing_claim() -> None:
    """standing_orders 无 group_harass claim → _try_upsert 返回 False（正常追加路径）。"""
    d, _, _, _ = _make_stub_director()

    new_claim = _make_gh_directive(target_count=3)
    result = d._try_upsert_group_harass(new_claim, now=5.0)

    assert result is False, "无现有 claim 应返回 False"
    assert len(d.standing_orders) == 0, "不应有新的 standing_order 被加入"


# ---------------------------------------------------------------------------
# 2. partial-release：target_count 3→1 释放 2 艘，优先满血
# ---------------------------------------------------------------------------


def test_partial_release_count_and_health_priority() -> None:
    """current=3, target_count=1 → 释放 2 艘；优先释放 health 最高的 2 艘。"""
    d, facade, release_calls, _ = _make_stub_director()

    # 注入血量：tag=101 满血 1.0，202 半血 0.5，303 残血 0.2
    facade._tag_health = {101: 1.0, 202: 0.5, 303: 0.2}

    first = _make_gh_directive(target_count=3)
    d.standing_orders.append(first)
    d._standing_order_tags[first.id] = {101, 202, 303}
    d._displaced[first.id] = {}
    d._recruit_watchers[first.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101, 202, 303},
    }

    # 第二条：target_count=1 → 触发 partial-release(2)
    second = _make_gh_directive(target_count=1)
    d._try_upsert_group_harass(second, now=10.0)

    remaining = d._standing_order_tags[first.id]
    assert len(remaining) == 1, f"应保留 1 艘，实际 {remaining}"
    # 保留的是血量最低的（残血 303）
    assert 303 in remaining, f"残血 303 应保留（不被释放），实际 {remaining}"
    # 释放了满血的 101 和半血的 202
    assert set(release_calls) == {101, 202}, f"应释放 101(满血)+202(半血)，实际 {release_calls}"


def test_partial_release_clears_seen() -> None:
    """partial-release 后，被释放的 BC 从 seen 移除（支持以后重新入伍）。"""
    d, facade, _, _ = _make_stub_director()
    facade._tag_health = {101: 1.0, 202: 0.5}

    first = _make_gh_directive(target_count=2)
    d.standing_orders.append(first)
    d._standing_order_tags[first.id] = {101, 202}
    d._displaced[first.id] = {}
    d._recruit_watchers[first.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101, 202},
    }

    second = _make_gh_directive(target_count=0)
    d._try_upsert_group_harass(second, now=10.0)

    seen = d._recruit_watchers[first.id]["seen"]
    assert len(seen) == 0, f"seen 应清空，实际 {seen}"


# ---------------------------------------------------------------------------
# 3. target_count=0：释放全部 BC，claim 留存
# ---------------------------------------------------------------------------


def test_target_count_zero_releases_all_but_claim_stays() -> None:
    """target_count=0 → 群内全部 BC 被释放；standing_orders 中 claim 仍保留（不删）。"""
    d, facade, release_calls, _ = _make_stub_director()
    facade._tag_health = {}

    first = _make_gh_directive(target_count=None)
    d.standing_orders.append(first)
    d._standing_order_tags[first.id] = {101, 202, 303}
    d._displaced[first.id] = {}
    d._recruit_watchers[first.id] = {
        "kind": "claim",
        "group_id": None,
        "unit_type": "BattleCruiser",
        "seen": {101, 202, 303},
    }

    second = _make_gh_directive(target_count=0)
    upserted = d._try_upsert_group_harass(second, now=15.0)

    assert upserted is True
    assert first.payload.target_count == 0, "target_count 应更新为 0"
    # 全部 BC 被释放
    assert set(release_calls) == {101, 202, 303}, f"全部 BC 应被释放，实际 {release_calls}"
    # claim 仍在 standing_orders
    assert first in d.standing_orders, "claim 应留存（不被删）"
    # tag 集为空
    assert len(d._standing_order_tags[first.id]) == 0


# ---------------------------------------------------------------------------
# 4. UI 群卡：group_harass claim 生成 type="group_harass" 卡 + display 含 i18n
# ---------------------------------------------------------------------------


def _minimal_director_for_card(target_named_spot: str | None, n: int, lang: str = "zh") -> Any:
    """构造一个能调用 _build_command_cards 的最小 Director。"""
    import pytest

    pytest.importorskip("vibecraft.bot.director")
    from vibecraft.bot.director import Director
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.logging_ import GameSession, GameSessionConfig

    facade = FakeFacade()
    session = GameSession(GameSessionConfig(use_null_sinks=True))
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
    from vibecraft.strategy import StrategyLibrary

    ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
    library = StrategyLibrary.from_directories(
        strategies_dir=ROOT / "strategies",
        aliases_path=ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session, locale=lang)
    from vibecraft.bot.event_bus import EventBus

    d = Director(
        facade=facade, parser=parser, session=session, library=library, event_bus=EventBus()
    )
    d._lang = lang

    # 注入一条 group_harass claim 到 standing_orders
    gh = _make_gh_directive(target_named_spot=target_named_spot, target_count=n)
    d.standing_orders.append(gh)
    d._standing_order_tags[gh.id] = set(range(n))  # n 艘 BC（用 tag 0..n-1 占位）

    return d


def test_command_card_type_group_harass() -> None:
    """group_harass claim → command_cards 中出一张 type='group_harass' 卡。"""
    d = _minimal_director_for_card(target_named_spot=None, n=3, lang="zh")
    cards = d._build_command_cards(now=50.0)
    gh_cards = [c for c in cards if c.get("type") == "group_harass"]
    assert len(gh_cards) == 1, f"应出 1 张 group_harass 群卡，实际 {len(gh_cards)}"
    card = gh_cards[0]
    assert card["revokable"] is True
    assert card["layer"] == "L3"
    # display 应含艘数 "3" 或 "×3"
    assert "3" in card["display"], f"display 应含艘数 3，实际: {card['display']}"


def test_command_card_display_contains_auto_target_zh() -> None:
    """target=None → zh display 含「自动」。"""
    d = _minimal_director_for_card(target_named_spot=None, n=2, lang="zh")
    cards = d._build_command_cards(now=50.0)
    gh_cards = [c for c in cards if c.get("type") == "group_harass"]
    assert gh_cards, "应有 group_harass 卡"
    assert "自动" in gh_cards[0]["display"], (
        f"zh display 应含「自动」，实际: {gh_cards[0]['display']}"
    )


def test_command_card_display_contains_target_zh() -> None:
    """target=enemy_natural → zh display 含「二矿」。"""
    d = _minimal_director_for_card(target_named_spot="enemy_natural", n=2, lang="zh")
    cards = d._build_command_cards(now=50.0)
    gh_cards = [c for c in cards if c.get("type") == "group_harass"]
    assert gh_cards, "应有 group_harass 卡"
    assert "二矿" in gh_cards[0]["display"], (
        f"zh display 应含「二矿」，实际: {gh_cards[0]['display']}"
    )


def test_command_card_display_en_no_chinese() -> None:
    """en locale 下 group_harass 卡 display 不含中文（零泄漏门）。"""
    import re

    _CJK = re.compile(r"[一-鿿]")
    d = _minimal_director_for_card(target_named_spot=None, n=4, lang="en")
    cards = d._build_command_cards(now=50.0)
    gh_cards = [c for c in cards if c.get("type") == "group_harass"]
    assert gh_cards, "应有 group_harass 卡"
    display = gh_cards[0]["display"]
    assert not _CJK.search(display), f"en display 不得含中文，实际: {display!r}"
    # en display 应含 "BC harass group" 或 "auto"
    assert "BC harass group" in display, f"en display 应含 'BC harass group'，实际: {display!r}"
