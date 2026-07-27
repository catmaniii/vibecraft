"""单元测试：view_follow（镜头跟随）+ production_block（停止造某种兵）。

覆盖：
  view_follow (target_kind=unit):
    - 提交 view_follow → facade.follow_unit 被调用
    - 新 view_follow 到来 → 旧的自动 superseded（_active_view_follow_id 更新）
    - 玩家 × → revoke_directive → active_view_follow_id 清空 + snapshot 推出
    - 每 tick _tick_view_follow → facade.follow_unit 持续调用

  view_follow (target_kind=army):
    - 提交 → facade.move_camera 被调用 + center 来自 _compute_current_army_center
    - 每 tick move_camera 重复调用（持续跟随）
    - 无单位（army center=None）→ 不调 move_camera（防回归）

  view_follow (target_kind=squad):
    - 提交 → facade.move_camera 被调用 + center 来自 squad 质心
    - 多个 squad → 跟第一个
    - squad 全死 → 不调 move_camera（防回归）

  production_block:
    - 提交 production_block → facade.block_production called
    - 玩家 × → revoke_directive → facade.unblock_production called
    - 重复提交同兵种（幂等）→ FakeFacade set 只含 1 条
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from vibecraft.bot.director import Director, DirectorConfig, TacticalSquad
from vibecraft.bot.facade import BotState, FakeFacade
from vibecraft.directives.models import (
    Directive,
    ProductionBlockPayload,
    ViewFollowPayload,
)
from vibecraft.directives.types import DirectiveType
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> GameSession:
    return GameSession(GameSessionConfig(use_null_sinks=True))


def _make_library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _director(facade: FakeFacade, session: GameSession) -> Director:
    """Director 最简构造（用真实 library；只测 directive submit + tick，不走 LLM）。"""
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = _make_library()
    parser = IntentParser(provider, library, session=session)
    director = Director(
        facade=facade,
        parser=parser,
        session=session,
        library=library,
        config=DirectorConfig(commit_delay_s=0.0),
    )
    # 这些测试验证 view_follow 的 refresh 逻辑(单次 on_tick 即应生效),禁掉镜头
    # 1/8 节流(_VIEW_FOLLOW_REFRESH_DIV=8)。节流本身由专门测试覆盖。
    director._VIEW_FOLLOW_REFRESH_DIV = 1
    return director


def _view_follow_directive(
    unit_type: str = "Stalker",
    unit_tag: int | None = None,
    now: float = 1.0,
) -> Directive:
    return Directive(
        payload=ViewFollowPayload(unit_type=unit_type, unit_tag=unit_tag),
        issued_at=now,
    )


def _production_block_directive(
    unit_type: str = "Stalker",
    now: float = 1.0,
) -> Directive:
    return Directive(
        payload=ProductionBlockPayload(unit_type=unit_type),
        issued_at=now,
    )


# ===========================================================================
# view_follow
# ===========================================================================


class TestViewFollow:
    """镜头跟随 directive 全链路单测。"""

    def test_submit_calls_follow_unit(self) -> None:
        """提交 view_follow → commit 后 facade.follow_unit 被调用。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Stalker"] = [11111]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type="Stalker", now=1.0)
        director.submit_directive(d, now=1.0)
        # commit_delay=0 → 立即 commit，_apply_to_facade 调 follow_unit
        director.on_tick(now=1.5)

        assert facade.camera_follows, "follow_unit should have been called"
        assert facade.camera_follows[-1] == 11111

    def test_active_view_follow_id_set(self) -> None:
        """提交后 _active_view_follow_id 设置为该 directive 的 id。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Stalker"] = [11111]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type="Stalker", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert director._active_view_follow_id == d.id

    def test_new_view_follow_supersedes_old(self) -> None:
        """新 view_follow 到来 → 旧的 _active_view_follow_id 被替换，旧 directive 从 _in_flight 移除。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Stalker"] = [11111]
        facade.selector_stub["Phoenix"] = [22222]
        session = _session()
        director = _director(facade, session)

        d1 = _view_follow_directive(unit_type="Stalker", now=1.0)
        director.submit_directive(d1, now=1.0)
        director.on_tick(now=1.5)

        old_id = director._active_view_follow_id
        assert old_id == d1.id

        d2 = _view_follow_directive(unit_type="Phoenix", now=3.0)
        director.submit_directive(d2, now=3.0)
        director.on_tick(now=3.5)

        # 新 id 已设
        assert director._active_view_follow_id == d2.id
        # 旧 directive 已从 _in_flight 移除
        assert old_id not in director._in_flight
        # facade.follow_unit 最后一次应是凤凰的 tag
        assert facade.camera_follows[-1] == 22222

    def test_revoke_clears_active_follow(self) -> None:
        """玩家 × → revoke_directive → _active_view_follow_id 清空。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Immortal"] = [33333]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type="Immortal", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert director._active_view_follow_id == d.id

        result = director.revoke_directive(d.id, now=2.0)

        assert result is True
        assert director._active_view_follow_id is None

    def test_tick_view_follow_repeats_follow_unit(self) -> None:
        """每次 on_tick 都调 facade.follow_unit（持续跟随语义）。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Colossus"] = [44444]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type="Colossus", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)
        director.on_tick(now=2.0)
        director.on_tick(now=2.5)

        # 三次 tick 至少三次 follow_unit（可能第一次在 _apply_view_follow，后面在 _tick）
        assert len(facade.camera_follows) >= 3
        assert all(t == 44444 for t in facade.camera_follows)

    def test_view_follow_refresh_throttled_to_eighth_tick(self) -> None:
        """镜头跟随刷新节流到 1/8：每 8 次 on_tick 才重发一次（2026-06-02 用户）。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Colossus"] = [44444]
        session = _session()
        director = _director(facade, session)
        director._VIEW_FOLLOW_REFRESH_DIV = 8  # _director helper 默认禁了节流，这里恢复
        director._view_follow_tick_count = 0

        d = _view_follow_directive(unit_type="Colossus", now=1.0)
        director.submit_directive(d, now=1.0)
        # 先跑几拍确保 commit + apply，follow 已 active；再重置计数器干净测节流。
        for i in range(3):
            director.on_tick(now=1.5 + i)
        director._view_follow_tick_count = 0
        n0 = len(facade.camera_follows)
        for i in range(7):
            director.on_tick(now=10.0 + i)  # count 1..7，被节流不重发
        assert len(facade.camera_follows) == n0, "前 7 tick 应被节流不重发"
        director.on_tick(now=20.0)  # count=8，重发一次
        assert len(facade.camera_follows) == n0 + 1, "第 8 tick 才重发一次"

    def test_view_follow_command_card_present(self) -> None:
        """view_follow directive commit 后出现在 command_cards 中（type='view_follow'）。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Phoenix"] = [55555]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type="Phoenix", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        snapshot = director.build_snapshot(now=1.5)
        card_types = [c["type"] for c in snapshot["command_cards"]]
        assert "view_follow" in card_types

        card = next(c for c in snapshot["command_cards"] if c["type"] == "view_follow")
        assert card["revokable"] is True
        assert "凤凰" in card["display"]  # 中文显示

    def test_view_follow_unit_tag_precise(self) -> None:
        """unit_tag 精确锁定模式：follow 的是指定 tag，不走 unit_type selector。"""
        facade = FakeFacade(state=BotState())
        # 确保 tag=9999 的单位被认为存在（resolver 直接返回 tag）
        # FakeFacade.resolve_selector: tag 非 None → return [tag]
        session = _session()
        director = _director(facade, session)

        d = _view_follow_directive(unit_type=None, unit_tag=9999, now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert 9999 in facade.camera_follows


# ===========================================================================
# production_block
# ===========================================================================


class TestProductionBlock:
    """产能封锁 directive 全链路单测。"""

    def test_submit_calls_block_production(self) -> None:
        """提交 production_block → facade.block_production called。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d = _production_block_directive(unit_type="Stalker", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert "Stalker" in facade.block_production_calls
        assert "Stalker" in facade.production_blocked

    def test_revoke_calls_unblock_production(self) -> None:
        """玩家 × → revoke_directive → facade.unblock_production called。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d = _production_block_directive(unit_type="Zealot", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert "Zealot" in facade.production_blocked

        result = director.revoke_directive(d.id, now=2.0)

        assert result is True
        assert "Zealot" in facade.unblock_production_calls
        assert "Zealot" not in facade.production_blocked

    def test_production_block_registered_in_director(self) -> None:
        """directive 提交后 _production_blocks dict 含 {id: unit_type}。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d = _production_block_directive(unit_type="Phoenix", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert d.id in director._production_blocks
        assert director._production_blocks[d.id] == "Phoenix"

    def test_revoke_clears_production_blocks(self) -> None:
        """revoke 后 _production_blocks 不再含该 id。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d = _production_block_directive(unit_type="Sentry", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        director.revoke_directive(d.id, now=2.0)

        assert d.id not in director._production_blocks

    def test_production_block_command_card_present(self) -> None:
        """production_block directive commit 后出现在 command_cards（type='production_block'）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d = _production_block_directive(unit_type="Stalker", now=1.0)
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        snapshot = director.build_snapshot(now=1.5)
        card_types = [c["type"] for c in snapshot["command_cards"]]
        assert "production_block" in card_types

        card = next(c for c in snapshot["command_cards"] if c["type"] == "production_block")
        assert card["revokable"] is True
        assert "追猎" in card["display"]  # Stalker 中文化

    def test_multiple_blocks_different_unit_types(self) -> None:
        """多个不同兵种的 production_block 可同时 active。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d1 = _production_block_directive(unit_type="Stalker", now=1.0)
        d2 = _production_block_directive(unit_type="Phoenix", now=1.0)
        director.submit_directive(d1, now=1.0)
        director.submit_directive(d2, now=1.0)
        director.on_tick(now=1.5)

        assert "Stalker" in facade.production_blocked
        assert "Phoenix" in facade.production_blocked

        # 只 revoke 追猎封锁
        director.revoke_directive(d1.id, now=2.0)
        assert "Stalker" not in facade.production_blocked
        assert "Phoenix" in facade.production_blocked  # 凤凰封锁仍生效

    def test_idempotent_block_same_unit_type(self) -> None:
        """同一兵种两次 block_production → set 只含 1 个（幂等）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        d1 = _production_block_directive(unit_type="Zealot", now=1.0)
        d2 = _production_block_directive(unit_type="Zealot", now=2.0)
        director.submit_directive(d1, now=1.0)
        director.on_tick(now=1.5)
        director.submit_directive(d2, now=2.0)
        director.on_tick(now=2.5)

        # facade set 幂等
        assert facade.production_blocked == {"Zealot"}
        # 两个 directive 各有自己的 id
        assert d1.id in director._production_blocks
        assert d2.id in director._production_blocks


# ===========================================================================
# Payload model tests
# ===========================================================================


class TestPayloadModels:
    """ViewFollowPayload / ProductionBlockPayload schema 验证。"""

    def test_view_follow_payload_default_persistent(self) -> None:
        p = ViewFollowPayload(unit_type="Phoenix")
        assert p.persistent is True
        assert p.type == DirectiveType.VIEW_FOLLOW

    def test_view_follow_payload_unit_tag_only(self) -> None:
        p = ViewFollowPayload(unit_type=None, unit_tag=12345)
        assert p.unit_tag == 12345
        assert p.unit_type is None

    def test_production_block_payload_default_persistent(self) -> None:
        p = ProductionBlockPayload(unit_type="Stalker")
        assert p.persistent is True
        assert p.type == DirectiveType.PRODUCTION_BLOCK

    def test_directive_serialization_view_follow(self) -> None:
        """ViewFollowPayload 可以被 Directive envelope 包装并序列化。"""
        d = Directive(
            payload=ViewFollowPayload(unit_type="Immortal", unit_type_hint="不朽"),
            issued_at=5.0,
        )
        assert d.type == DirectiveType.VIEW_FOLLOW
        data = d.model_dump(mode="json")
        assert data["payload"]["type"] == "view_follow"
        assert data["payload"]["unit_type"] == "Immortal"

    def test_directive_serialization_production_block(self) -> None:
        """ProductionBlockPayload 可以被 Directive envelope 包装并序列化。"""
        d = Directive(
            payload=ProductionBlockPayload(unit_type="Zealot"),
            issued_at=5.0,
        )
        assert d.type == DirectiveType.PRODUCTION_BLOCK
        data = d.model_dump(mode="json")
        assert data["payload"]["type"] == "production_block"
        assert data["payload"]["unit_type"] == "Zealot"

    def test_view_follow_payload_target_kind_default(self) -> None:
        """ViewFollowPayload.target_kind 默认值 = 'unit'。"""
        p = ViewFollowPayload(unit_type="Phoenix")
        assert p.target_kind == "unit"

    def test_view_follow_payload_target_kind_army(self) -> None:
        """target_kind='army' 合法，unit_type 可 None。"""
        p = ViewFollowPayload(target_kind="army")
        assert p.target_kind == "army"
        assert p.unit_type is None

    def test_view_follow_payload_target_kind_squad(self) -> None:
        """target_kind='squad' 合法，unit_type 可 None。"""
        p = ViewFollowPayload(target_kind="squad")
        assert p.target_kind == "squad"
        assert p.unit_type is None

    def test_view_follow_payload_serialization_army(self) -> None:
        """army 模式序列化 target_kind 字段存在且正确。"""
        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=5.0,
        )
        data = d.model_dump(mode="json")
        assert data["payload"]["target_kind"] == "army"

    def test_view_follow_payload_serialization_squad(self) -> None:
        """squad 模式序列化 target_kind 字段存在且正确。"""
        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=5.0,
        )
        data = d.model_dump(mode="json")
        assert data["payload"]["target_kind"] == "squad"


# ===========================================================================
# view_follow target_kind=army
# ===========================================================================


def _make_bot_with_army_center(cx: float, cy: float) -> MagicMock:
    """构造 mock bot，其主力单位质心 = (cx, cy)，用于 view_follow army focus 计算。

    2026-06-03 镜头三规则后：army 模式走 strongest_cluster_units → compute_follow_focus，
    需要可迭代的真实单位（带 position / is_moving）。停止 + 无敌情 → focus = 质心。
    """
    from sc2.position import Point2

    bot = MagicMock()
    units = []
    for i, (dx, dy) in enumerate(((-1.0, 0.0), (1.0, 0.0), (0.0, 0.0))):  # 质心 = (cx, cy)
        u = MagicMock()
        u.tag = 9000 + i
        u.position = Point2((cx + dx, cy + dy))
        u.is_moving = False  # 停止 → focus = 质心（不前瞻）
        units.append(u)
    bot.units.filter.return_value = units
    bot.enemy_units = []  # 无敌军 → 不触发交战聚焦
    bot.start_location = Point2((cx, cy))
    return bot


def _make_bot_no_army() -> MagicMock:
    """构造 mock bot，无任何军队单位（filter 返空）。"""
    from sc2.position import Point2

    bot = MagicMock()
    filtered = MagicMock()
    filtered.__bool__ = lambda s: False
    bot.units.filter.return_value = filtered
    bot.start_location = Point2((10.0, 10.0))
    return bot


class _UnitsList(list):
    """最小 Units mock:真实 bot.units.tags_in() 返回 Units(有 .amount),plain list 没有。

    _resolve_squad_center 用 live_units.amount,mock 必须忠实提供,否则 AttributeError
    被 except 吞 → 误判 squad 全死(实测 400c15b 回归)。
    """

    @property
    def amount(self) -> int:
        return len(self)


def _make_bot_with_squads(squads: list[tuple[list[tuple[float, float]], str]]) -> MagicMock:
    """构造 mock bot，支持 units.tags_in(tags) 返回对应单位列表。

    squads: [(unit_positions_list, directive_id_prefix), ...]
    返回 (bot, [tag_sets_per_squad])
    """
    from sc2.position import Point2

    all_units: dict[int, MagicMock] = {}

    class TagsInCollection:
        def __init__(self, unit_map: dict[int, MagicMock]) -> None:
            self._map = unit_map

        def tags_in(self, tags: set[int]) -> _UnitsList:
            return _UnitsList(self._map[t] for t in tags if t in self._map)

        def filter(self, fn: object) -> MagicMock:
            filtered = MagicMock()
            filtered.__bool__ = lambda s: False
            return filtered

    squad_tag_sets = []
    tag_counter = 1000
    for positions, _ in squads:
        tags = set()
        for x, y in positions:
            unit = MagicMock()
            unit.tag = tag_counter
            unit.position = Point2((x, y))
            all_units[tag_counter] = unit
            tags.add(tag_counter)
            tag_counter += 1
        squad_tag_sets.append(tags)

    bot = MagicMock()
    collection = TagsInCollection(all_units)
    bot.units = collection
    bot.start_location = Point2((0.0, 0.0))
    return bot, squad_tag_sets


class TestViewFollowArmy:
    """target_kind='army' — 镜头跟随全军主力质心。"""

    def test_army_calls_move_camera(self) -> None:
        """target_kind=army → 调 facade.move_camera，不调 follow_unit。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_army_center(50.0, 60.0)

        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves, "move_camera should have been called"
        assert facade.camera_follows == [], "follow_unit should NOT be called for army mode"
        # center 坐标应接近 (50.0, 60.0)
        x, y = facade.camera_moves[-1]
        assert abs(x - 50.0) < 0.1
        assert abs(y - 60.0) < 0.1

    def test_army_active_view_follow_id_set(self) -> None:
        """army 模式也正确设置 _active_view_follow_id。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_army_center(30.0, 40.0)

        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert director._active_view_follow_id == d.id

    def test_army_tick_repeats_move_camera(self) -> None:
        """每 tick 都调 move_camera（持续跟随）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_army_center(20.0, 30.0)

        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)
        director.on_tick(now=2.0)
        director.on_tick(now=2.5)

        assert len(facade.camera_moves) >= 3

    def test_army_no_units_no_move_camera(self) -> None:
        """无军队单位 → 静默，不调 move_camera。

        2026-06-03 镜头三规则后：无主力单位时 compute_follow_focus 拿不到单位 → 不动镜头
        （不再像旧逻辑那样把镜头甩到主基地 start_location；主力没了不该突然跳回家）。
        """
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_no_army()

        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        # 无主力单位 → 静默（玩家 × 解除）
        assert facade.camera_moves == []

    def test_army_command_card_display(self) -> None:
        """army 模式 command card display 含'主力部队'。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_army_center(50.0, 50.0)

        d = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        snapshot = director.build_snapshot(now=1.5)
        cards = [c for c in snapshot["command_cards"] if c["type"] == "view_follow"]
        assert cards, "view_follow card should be present"
        assert "主力" in cards[0]["display"]

    def test_army_supersedes_old_follow(self) -> None:
        """army view_follow 到来 → 旧的 unit follow 被 superseded。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Stalker"] = [11111]
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_army_center(50.0, 50.0)

        d1 = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Stalker"),
            issued_at=1.0,
        )
        director.submit_directive(d1, now=1.0)
        director.on_tick(now=1.5)
        assert director._active_view_follow_id == d1.id

        d2 = Directive(
            payload=ViewFollowPayload(target_kind="army"),
            issued_at=3.0,
        )
        director.submit_directive(d2, now=3.0)
        director.on_tick(now=3.5)

        assert director._active_view_follow_id == d2.id
        assert d1.id not in director._in_flight


# ===========================================================================
# view_follow target_kind=squad
# ===========================================================================


class TestViewFollowSquad:
    """target_kind='squad' — 镜头跟随 recon/harass 小队质心。"""

    def _setup_squad_director(
        self,
        squad_positions: list[tuple[float, float]],
        verb: str = "recon",
    ) -> tuple[FakeFacade, Director, str]:
        """构造有一个 active squad 的 Director，返回 (facade, director, squad_directive_id)。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        bot, tag_sets = _make_bot_with_squads([(squad_positions, "squad1")])
        director._bot = bot

        # 手动注入一个 squad（不走 LLM，直接 inject _tactical_squads）
        squad_id = "d_squad01"
        squad = TacticalSquad(
            directive_id=squad_id,
            unit_tags=tag_sets[0],
            target=None,
            move_type=None,
            verb=verb,
            n_wanted=len(squad_positions),
            n_locked=len(squad_positions),
            unit_type="Stalker",
        )
        director._tactical_squads[squad_id] = squad
        return facade, director, squad_id

    def test_squad_calls_move_camera(self) -> None:
        """target_kind=squad → 调 facade.move_camera，不调 follow_unit。"""
        positions = [(30.0, 40.0), (32.0, 38.0)]
        facade, director, _squad_id = self._setup_squad_director(positions)

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves, "move_camera should have been called"
        assert facade.camera_follows == [], "follow_unit should NOT be called for squad mode"
        # 质心应接近 (31.0, 39.0)
        x, y = facade.camera_moves[-1]
        assert abs(x - 31.0) < 0.5
        assert abs(y - 39.0) < 0.5

    def test_squad_tick_repeats_move_camera(self) -> None:
        """每 tick 都调 move_camera（持续跟随）。"""
        positions = [(20.0, 20.0)]
        facade, director, _squad_id = self._setup_squad_director(positions)

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)
        director.on_tick(now=2.0)
        director.on_tick(now=2.5)

        assert len(facade.camera_moves) >= 3

    def test_squad_all_dead_no_move_camera(self) -> None:
        """squad 全死（tags 不存在）→ 不调 move_camera，不 crash。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        # bot 无任何单位：tags_in 返回空 Units(有 .amount=0)
        class EmptyTagsIn:
            def tags_in(self, tags: set[int]) -> _UnitsList:
                return _UnitsList()

            def filter(self, fn: object) -> MagicMock:
                filtered = MagicMock()
                filtered.__bool__ = lambda s: False
                return filtered

        bot = MagicMock()
        bot.units = EmptyTagsIn()
        director._bot = bot

        # 注入一个有 tags 但全死的 squad
        squad_id = "d_dead01"
        squad = TacticalSquad(
            directive_id=squad_id,
            unit_tags={9001, 9002},
            target=None,
            move_type=None,
            verb="recon",
            n_wanted=2,
            n_locked=2,
            unit_type="Stalker",
        )
        director._tactical_squads[squad_id] = squad

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        # 全死：不调 move_camera
        assert facade.camera_moves == [], "no move_camera when all squad units dead"

    def test_squad_multiple_squads_follows_first(self) -> None:
        """多个 squad → 跟第一个（按插入顺序）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        # 两组 squad，第一组在 (10, 10)，第二组在 (90, 90)
        bot, tag_sets = _make_bot_with_squads(
            [
                ([(10.0, 10.0)], "squad_first"),
                ([(90.0, 90.0)], "squad_second"),
            ]
        )
        director._bot = bot

        squad1 = TacticalSquad(
            directive_id="d_squad_a",
            unit_tags=tag_sets[0],
            target=None,
            move_type=None,
            verb="recon",
            n_wanted=1,
            n_locked=1,
            unit_type="Stalker",
        )
        squad2 = TacticalSquad(
            directive_id="d_squad_b",
            unit_tags=tag_sets[1],
            target=None,
            move_type=None,
            verb="harass",
            n_wanted=1,
            n_locked=1,
            unit_type="Phoenix",
        )
        director._tactical_squads["d_squad_a"] = squad1
        director._tactical_squads["d_squad_b"] = squad2

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves, "move_camera should have been called"
        x, y = facade.camera_moves[-1]
        # 第一个 squad 质心 (10, 10)，不是 (90, 90)
        assert abs(x - 10.0) < 0.5, f"expected ~10.0 got {x}"
        assert abs(y - 10.0) < 0.5, f"expected ~10.0 got {y}"

    def test_squad_no_squads_no_move_camera(self) -> None:
        """无任何 squad → 不调 move_camera，静默等待（防空指针回归）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = MagicMock()
        # _tactical_squads 为空（默认）

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves == [], "no move_camera when no squads"

    def test_squad_command_card_display(self) -> None:
        """squad 模式 command card display 含'侦查小队'。"""
        positions = [(50.0, 50.0)]
        _facade, director, _squad_id = self._setup_squad_director(positions)

        d = Directive(
            payload=ViewFollowPayload(target_kind="squad"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        snapshot = director.build_snapshot(now=1.5)
        cards = [c for c in snapshot["command_cards"] if c["type"] == "view_follow"]
        assert cards, "view_follow card should be present"
        assert "侦查" in cards[0]["display"]

    def test_unit_mode_still_uses_follow_unit(self) -> None:
        """回归：target_kind=unit（默认）仍走 follow_unit，不走 move_camera。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Stalker"] = [77777]
        session = _session()
        director = _director(facade, session)

        d = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Stalker"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert 77777 in facade.camera_follows, "follow_unit should be called for unit mode"
        assert facade.camera_moves == [], "move_camera should NOT be called for unit mode"


# ===========================================================================
# view_follow target_kind="task"（2026-06-01：按任务身份识别单位）
# ===========================================================================


def _make_bot_with_scout(
    scout_tag: int | None, pos: tuple[float, float] = (40.0, 50.0)
) -> MagicMock:
    """构造 mock bot，bot.scout_worker.scout_tag = scout_tag，units.tags_in 返回该侦察兵。"""
    from sc2.position import Point2

    bot = MagicMock()
    sw = MagicMock()
    sw.scout_tag = scout_tag
    bot.scout_worker = sw

    umap: dict[int, MagicMock] = {}
    if scout_tag is not None:
        unit = MagicMock()
        unit.tag = scout_tag
        unit.position = Point2(pos)
        umap[scout_tag] = unit

    class Coll:
        def tags_in(self, tags: set[int]) -> _UnitsList:
            return _UnitsList(umap[t] for t in tags if t in umap)

        def filter(self, fn: object) -> MagicMock:
            filtered = MagicMock()
            filtered.__bool__ = lambda s: False
            return filtered

    bot.units = Coll()
    bot.start_location = Point2((0.0, 0.0))
    return bot


class TestViewFollowTask:
    """target_kind='task' — 按任务身份（scout/patrol/watchtower/harass）识别跟随单位。"""

    def test_task_scout_follows_scout_worker_tag(self) -> None:
        """task=scout → 跟 bot.scout_worker.scout_tag（停止侦察兵 → move_camera 聚焦其位置）。

        2026-06-03 镜头三规则后：单兵也走 compute_follow_focus。侦察兵停止 + 无敌情 →
        focus = 其位置（默认 (40,50)）。锁定 tag 保证不在采矿农民间跳。
        """
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_scout(scout_tag=80001)

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="scout"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves, "single scout focuses via move_camera"
        x, y = facade.camera_moves[-1]
        assert abs(x - 40.0) < 0.5 and abs(y - 50.0) < 0.5

    def test_task_scout_tick_keeps_same_tag(self) -> None:
        """每 tick 持续跟同一个侦察兵 tag（不跳）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_scout(scout_tag=80002)

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="scout"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)
        director.on_tick(now=2.0)
        director.on_tick(now=2.5)

        assert len(facade.camera_moves) >= 3
        # 持续聚焦同一侦察兵位置（不跳）
        assert all(abs(x - 40.0) < 0.5 and abs(y - 50.0) < 0.5 for x, y in facade.camera_moves), (
            "scout focus must not jump"
        )

    def test_task_scout_no_scout_silent(self) -> None:
        """无侦察兵（scout_tag=None）→ 不调 facade，静默等待（玩家 × 解除）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_scout(scout_tag=None)

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="scout"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_follows == [], "no follow when no scout"
        assert facade.camera_moves == [], "no move_camera when no scout"

    def test_task_scout_returned_unit_silent(self) -> None:
        """任务结束（scout 归还，scout_tag 清空）→ 下一 tick 不再跟（单位已归还）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        bot = _make_bot_with_scout(scout_tag=80003)
        director._bot = bot

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="scout"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)
        assert facade.camera_moves, "should focus the scout"

        # 模拟 ScoutWorker 任务完成 / 玩家取消 → scout_tag 清空
        bot.scout_worker.scout_tag = None
        before = len(facade.camera_moves)
        director.on_tick(now=2.0)
        assert len(facade.camera_moves) == before, "no new focus after scout returned"

    def test_task_harass_follows_squad_centroid(self) -> None:
        """task=harass → 跟 harass squad 质心（多个 → move_camera）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        bot, tag_sets = _make_bot_with_squads([([(60.0, 60.0), (64.0, 64.0)], "h")])
        director._bot = bot
        director._tactical_squads["d_harass01"] = TacticalSquad(
            directive_id="d_harass01",
            unit_tags=tag_sets[0],
            target=None,
            move_type=None,
            verb="harass",
            n_wanted=2,
            n_locked=2,
            unit_type="Phoenix",
        )

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="harass"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_moves, "harass squad (multi-unit) uses move_camera"
        x, y = facade.camera_moves[-1]
        assert abs(x - 62.0) < 0.5 and abs(y - 62.0) < 0.5

    def test_task_scout_follows_player_scout_directive(self) -> None:
        """task=scout 找玩家 SCOUT directive reserve 的农民。

        真实场景（game_20260601_054636）：玩家"派农民探路"在 t<60 发出，此时内建
        ScoutWorker 还没激活（scout_tag=None）。探路农民 tag 存在 SCOUT directive 的
        _standing_order_tags 里 + directive 在 _committed_directives。resolver 必须读到它，
        而不是漏掉（漏掉 → LLM 退化 unit_type=Probe → 锁采矿农民，正是用户报的 bug）。
        """
        from sc2.position import Point2

        from vibecraft.directives.models import ScoutPayload
        from vibecraft.directives.scope import Selector, TargetSpec

        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        # bot：内建 ScoutWorker 未激活（scout_tag=None），但 units 能解析探路农民 tag=70001
        bot = MagicMock()
        bot.scout_worker = MagicMock()
        bot.scout_worker.scout_tag = None
        scout_unit = MagicMock()
        scout_unit.tag = 70001
        scout_unit.position = Point2((90.0, 88.0))  # 前线敌方主基地附近
        umap = {70001: scout_unit}

        class Coll:
            def tags_in(self, tags: set[int]) -> _UnitsList:
                return _UnitsList(umap[t] for t in tags if t in umap)

        bot.units = Coll()
        director._bot = bot

        # 模拟玩家 SCOUT directive 已 commit + reserve 了探路农民
        scout_d = Directive(
            payload=ScoutPayload(
                target=TargetSpec(kind="named_spot", named_spot="enemy_main"),
                selector=Selector(unit_type="Probe", count=1),
            ),
            issued_at=16.5,
        )
        director._committed_directives[scout_d.id] = scout_d
        director._standing_order_tags[scout_d.id] = {70001}

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task="scout"),
            issued_at=33.0,
        )
        director.submit_directive(d, now=33.0)
        director.on_tick(now=34.0)

        # 停止侦察兵 + 无敌情 → focus = 其位置 (90, 88)
        assert facade.camera_moves, "should focus the player scout worker"
        x, y = facade.camera_moves[-1]
        assert abs(x - 90.0) < 0.5 and abs(y - 88.0) < 0.5, (
            f"must focus the reserved scout at (90,88), got {facade.camera_moves}"
        )

    def test_task_missing_task_field_silent(self) -> None:
        """target_kind=task 但 task=None → 不 crash，不调 facade。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)
        director._bot = _make_bot_with_scout(scout_tag=80004)

        d = Directive(
            payload=ViewFollowPayload(target_kind="task", task=None),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)

        assert facade.camera_follows == []
        assert facade.camera_moves == []


class _AliveAwareFacade(FakeFacade):
    """resolve_selector(tag=) 按 alive 集合判定存活，用于测 lock-on-resolve 的死后重选。"""

    def __init__(self, alive: set[int], **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.alive = alive

    def resolve_selector(
        self,
        unit_type: str | None = None,
        tag: int | None = None,
        tags: list[int] | None = None,
    ) -> list[int]:
        if tag is not None:
            self.selector_lookups.append({"unit_type": None, "tag": tag, "tags": None})
            return [tag] if tag in self.alive else []
        return super().resolve_selector(unit_type=unit_type, tags=tags)


class TestViewFollowLockOnResolve:
    """target_kind='unit' 只给 unit_type 时锁定首次 tag，防每 tick 重选跳到另一个同型单位。"""

    def test_unit_type_locks_tag_no_jump(self) -> None:
        """selector 顺序变化（采矿农民移动）→ 镜头仍跟首次锁定的那个，不跳。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Probe"] = [100, 200, 300]
        session = _session()
        director = _director(facade, session)

        d = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Probe"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)  # 锁定 tags[0]=100
        # 模拟 units 集合重排（农民移动后 resolve 顺序变）
        facade.selector_stub["Probe"] = [200, 300, 100]
        director.on_tick(now=2.0)
        director.on_tick(now=2.5)

        assert facade.camera_follows, "follow_unit should be called"
        assert all(t == 100 for t in facade.camera_follows), (
            f"locked tag must stay 100, got {facade.camera_follows}"
        )

    def test_unit_type_reresolves_after_locked_unit_dies(self) -> None:
        """锁定单位死亡 → 释放锁 → 重新 resolve 到下一个存活同型单位。"""
        facade = _AliveAwareFacade(alive={100, 200, 300}, state=BotState())
        facade.selector_stub["Probe"] = [100, 200, 300]
        session = _session()
        director = _director(facade, session)

        d = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Probe"),
            issued_at=1.0,
        )
        director.submit_directive(d, now=1.0)
        director.on_tick(now=1.5)  # 锁 100
        assert facade.camera_follows[-1] == 100

        # 100 死亡，从存活集 + selector 移除
        facade.alive.discard(100)
        facade.selector_stub["Probe"] = [200, 300]
        director.on_tick(now=2.0)

        assert facade.camera_follows[-1] == 200, "should re-resolve to 200 after 100 died"
        assert director._view_follow_locked_tag == 200

    def test_new_follow_resets_lock(self) -> None:
        """新 view_follow 到来 → 锁定 tag 重置（不被旧锁污染）。"""
        facade = FakeFacade(state=BotState())
        facade.selector_stub["Probe"] = [100]
        facade.selector_stub["Stalker"] = [500]
        session = _session()
        director = _director(facade, session)

        d1 = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Probe"),
            issued_at=1.0,
        )
        director.submit_directive(d1, now=1.0)
        director.on_tick(now=1.5)
        assert director._view_follow_locked_tag == 100

        d2 = Directive(
            payload=ViewFollowPayload(target_kind="unit", unit_type="Stalker"),
            issued_at=3.0,
        )
        director.submit_directive(d2, now=3.0)
        director.on_tick(now=3.5)
        assert director._view_follow_locked_tag == 500, "lock must reset to new follow's target"


class TestViewFollowTaskModel:
    """ViewFollowPayload target_kind='task' + task 字段的模型校验。"""

    def test_task_payload_valid(self) -> None:
        p = ViewFollowPayload(target_kind="task", task="scout")
        assert p.target_kind == "task"
        assert p.task == "scout"
        assert p.persistent is True

    def test_task_defaults_none(self) -> None:
        """默认 target_kind=unit 时 task 留空。"""
        p = ViewFollowPayload(unit_type="Stalker")
        assert p.target_kind == "unit"
        assert p.task is None

    def test_all_task_values_accepted(self) -> None:
        for task in ("scout", "patrol", "watchtower", "harass"):
            p = ViewFollowPayload(target_kind="task", task=task)  # type: ignore[arg-type]
            assert p.task == task
