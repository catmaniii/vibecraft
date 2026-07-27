"""MinimapBuilder + _AresFacade.move_camera async 修复的单元测试。

不依赖 ares-sc2：全部用 Mock / FakeBot。

测试覆盖：
1. MinimapBuilder.build() 输出字段正确性（类型/值/结构）
2. _collect_own()：Nexus / 探机 / 建筑 / 战斗单位分类
3. _collect_enemy_visible()：只推 is_visible=True；工人识别（PROBE/SCV/DRONE）
4. _ensure_static_cached()：只算一次
5. spike S1：_AresFacade.move_camera 调 asyncio.create_task（不是直接 await）
6. ws._handle_view_move：验证参数合法校验 + 转发 send_command
7. game_process._dispatch_upstream：minimap 分支正确转发
"""

from __future__ import annotations

import asyncio
import json
import sys
import typing
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# FakeBot：最小 bot stub，不依赖 ares
# ---------------------------------------------------------------------------


def _make_unit(
    tag: int, x: float, y: float, type_name: str = "STALKER", visible: bool = True
) -> MagicMock:
    """构造一个 mock Unit 对象。"""
    u = MagicMock()
    u.tag = tag
    u.position_tuple = (x, y)
    u.type_id = MagicMock()
    u.type_id.name = type_name
    u.is_visible = visible
    return u


class _FakePlayableArea:
    x = 16
    y = 12
    width = 152
    height = 116


class FakePixelMap:
    """模拟 sc2.pixel_map.PixelMap:.data_numpy shape=(h, w) uint8。"""

    def __init__(self, w: int = 168, h: int = 168, fill: int = 100) -> None:
        self.data_numpy = np.full((h, w), fill, dtype=np.uint8)


class FakeGameInfo:
    playable_area = _FakePlayableArea()
    map_size = (168, 168)  # NamedTuple-like：支持 [0] / [1] 索引
    terrain_height = FakePixelMap(168, 168, fill=128)


class FakeCamera:
    x = 88.5
    y = 134.2


class FakeObservationRaw:
    class player:
        camera = FakeCamera()


class FakeObservation:
    alerts: typing.ClassVar[list[int]] = []


class FakeState:
    observation_raw = FakeObservationRaw()
    observation = FakeObservation()
    visibility = FakePixelMap(168, 168, fill=2)  # 全可见


class FakeBot:
    """MinimapBuilder 所需的最小 bot 接口。"""

    def __init__(self) -> None:
        self.game_info = FakeGameInfo()
        self.state = FakeState()
        self.townhalls: list[MagicMock] = []
        self.workers: list[MagicMock] = []
        self.structures: list[MagicMock] = []
        self.units: list[MagicMock] = []
        self.enemy_units: list[MagicMock] = []
        self.enemy_structures: list[MagicMock] = []
        self.mineral_field: list[MagicMock] = []
        self.vespene_geyser: list[MagicMock] = []


# ---------------------------------------------------------------------------
# MinimapBuilder 单测
# ---------------------------------------------------------------------------


class TestMinimapBuilderBuild:
    """build() 输出字段基本正确性。"""

    def _make_builder(self) -> Any:
        from vibecraft.bot.minimap import MinimapBuilder

        bot = FakeBot()
        return MinimapBuilder(bot), bot

    def test_build_type_field(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(42.5)
        assert frame["type"] == "minimap"

    def test_build_ts_rounded(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(42.555555)
        assert frame["ts"] == pytest.approx(42.556, abs=0.001)

    def test_build_map_playable(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        assert frame["map"]["playable"] == [16, 12, 152, 116]

    def test_build_map_size(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        assert frame["map"]["size"] == [168, 168]

    def test_build_viewport_center(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        center = frame["viewport"]["center"]
        assert center[0] == pytest.approx(88.5, abs=0.01)
        assert center[1] == pytest.approx(134.2, abs=0.01)

    def test_build_viewport_size_fixed(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        assert frame["viewport"]["size"] == [24, 18]

    def test_build_units_own_empty_bot(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        assert frame["units_own"] == []
        assert frame["units_enemy_visible"] == []

    def test_build_keys_present(self) -> None:
        builder, _ = self._make_builder()
        frame = builder.build(0.0)
        assert "type" in frame
        assert "ts" in frame
        assert "map" in frame
        assert "viewport" in frame
        assert "units_own" in frame
        assert "units_enemy_visible" in frame


class TestMinimapBuilderCollectOwn:
    """_collect_own：Nexus / 探机 / 建筑 / 战斗单位分类。"""

    def _make_builder(self) -> Any:
        from vibecraft.bot.minimap import MinimapBuilder

        return MinimapBuilder(FakeBot())

    def test_nexus_kind(self) -> None:
        builder = self._make_builder()
        nexus = _make_unit(1, 100.0, 100.0)
        builder.bot.townhalls = [nexus]
        frame = builder.build(0.0)
        nexus_points = [p for p in frame["units_own"] if p[2] == "N"]
        assert len(nexus_points) == 1
        assert nexus_points[0][0] == pytest.approx(100.0, abs=0.2)

    def test_worker_kind(self) -> None:
        builder = self._make_builder()
        probe = _make_unit(2, 90.0, 90.0, "PROBE")
        builder.bot.workers = [probe]
        frame = builder.build(0.0)
        worker_points = [p for p in frame["units_own"] if p[2] == "P"]
        assert len(worker_points) == 1

    def test_building_kind_excludes_townhall(self) -> None:
        builder = self._make_builder()
        nexus = _make_unit(1, 100.0, 100.0)
        gateway = _make_unit(2, 95.0, 95.0)
        builder.bot.townhalls = [nexus]
        builder.bot.structures = [nexus, gateway]
        frame = builder.build(0.0)
        building_points = [p for p in frame["units_own"] if p[2] == "B"]
        # 只有 gateway 是 B，nexus 是 N
        assert len(building_points) == 1

    def test_army_kind_excludes_workers(self) -> None:
        builder = self._make_builder()
        probe = _make_unit(1, 90.0, 90.0, "PROBE")
        stalker = _make_unit(2, 80.0, 80.0, "STALKER")
        builder.bot.workers = [probe]
        builder.bot.units = [probe, stalker]
        frame = builder.build(0.0)
        army_points = [p for p in frame["units_own"] if p[2] == "A"]
        worker_points = [p for p in frame["units_own"] if p[2] == "P"]
        assert len(army_points) == 1
        assert len(worker_points) == 1


class TestMinimapBuilderCollectEnemy:
    """_collect_enemy_visible：is_visible 过滤 + 工人识别。"""

    def _make_builder(self) -> Any:
        from vibecraft.bot.minimap import MinimapBuilder

        return MinimapBuilder(FakeBot())

    def test_invisible_unit_not_included(self) -> None:
        builder = self._make_builder()
        marine = _make_unit(10, 50.0, 50.0, "MARINE", visible=False)
        builder.bot.enemy_units = [marine]
        frame = builder.build(0.0)
        assert frame["units_enemy_visible"] == []

    def test_visible_unit_included(self) -> None:
        builder = self._make_builder()
        marine = _make_unit(10, 50.0, 50.0, "MARINE", visible=True)
        builder.bot.enemy_units = [marine]
        frame = builder.build(0.0)
        assert len(frame["units_enemy_visible"]) == 1
        assert frame["units_enemy_visible"][0][2] == "?"

    def test_enemy_worker_types(self) -> None:
        """PROBE / SCV / DRONE 识别为工人 W。"""
        builder = self._make_builder()
        for worker_type in ("PROBE", "SCV", "DRONE"):
            builder2 = type(builder)(FakeBot())
            worker = _make_unit(1, 40.0, 40.0, worker_type, visible=True)
            builder2.bot.enemy_units = [worker]
            frame = builder2.build(0.0)
            assert frame["units_enemy_visible"][0][2] == "W", f"{worker_type} should be W"

    def test_enemy_structure_visible(self) -> None:
        builder = self._make_builder()
        barracks = _make_unit(20, 30.0, 30.0, "BARRACKS", visible=True)
        builder.bot.enemy_structures = [barracks]
        frame = builder.build(0.0)
        assert len(frame["units_enemy_visible"]) == 1
        assert frame["units_enemy_visible"][0][2] == "?"


class TestMinimapBuilderCollectResources:
    """_collect_resources：水晶矿(M) + 气矿(G)，属性缺失安全兜底。"""

    def _make_builder(self) -> Any:
        from vibecraft.bot.minimap import MinimapBuilder

        return MinimapBuilder(FakeBot())

    def test_empty_when_no_resources(self) -> None:
        builder = self._make_builder()
        frame = builder.build(0.0)
        assert frame["resources"] == []

    def test_mineral_field_kind_M(self) -> None:
        builder = self._make_builder()
        builder.bot.mineral_field = [_make_unit(1, 40.0, 50.0), _make_unit(2, 42.0, 50.0)]
        frame = builder.build(0.0)
        minerals = [r for r in frame["resources"] if r[2] == "M"]
        assert len(minerals) == 2
        assert minerals[0][:2] == [40.0, 50.0]

    def test_vespene_geyser_kind_G(self) -> None:
        builder = self._make_builder()
        builder.bot.vespene_geyser = [_make_unit(3, 60.0, 70.0)]
        frame = builder.build(0.0)
        geysers = [r for r in frame["resources"] if r[2] == "G"]
        assert len(geysers) == 1
        assert geysers[0] == [60.0, 70.0, "G"]

    def test_resources_attr_missing_safe(self) -> None:
        """bot 没有 mineral_field/vespene_geyser 属性（早期/旧 mock）→ 空列表不崩。"""
        from vibecraft.bot.minimap import MinimapBuilder

        bot = FakeBot()
        del bot.mineral_field
        del bot.vespene_geyser
        builder = MinimapBuilder(bot)
        frame = builder.build(0.0)
        assert frame["resources"] == []


class TestMinimapBuilderStaticCache:
    """_ensure_static_cached：playable/map_size 只算一次。"""

    def test_static_cached_after_first_build(self) -> None:
        from vibecraft.bot.minimap import MinimapBuilder

        bot = FakeBot()
        builder = MinimapBuilder(bot)
        assert builder._playable is None
        builder.build(0.0)
        assert builder._playable is not None
        # 修改 bot.game_info 不影响已缓存的值
        original = builder._playable[:]
        builder.build(1.0)  # 再次 build
        assert builder._playable == original


class TestMinimapBuilderCoordRounding:
    """unit 坐标四舍五入到小数点后一位。"""

    def test_coords_rounded_to_one_decimal(self) -> None:
        from vibecraft.bot.minimap import MinimapBuilder

        bot = FakeBot()
        builder = MinimapBuilder(bot)
        nexus = _make_unit(1, 88.1234, 99.9876)
        bot.townhalls = [nexus]
        frame = builder.build(0.0)
        pt = frame["units_own"][0]
        # 小数点后一位
        assert pt[0] == pytest.approx(88.1, abs=0.05)
        assert pt[1] == pytest.approx(100.0, abs=0.05)


# ---------------------------------------------------------------------------
# _SharpyFacade.move_camera 暂存模式（ADR 0008）
# ---------------------------------------------------------------------------


# 注入 fake sharpy（M1：sharpy_adapter 取代 ares_adapter）
def _inject_fake_sharpy_for_move_camera() -> type:
    """注入 fake sharpy，返回 FakeKnowledgeBot 类（M1 sharpy 迁移后替代原 FakeAresBot）。"""
    import enum

    for key in list(sys.modules.keys()):
        if (
            key.startswith("sharpy")
            or key.startswith("vibecraft.bot.sharpy_adapter")
            or key.startswith("vibecraft.bot.auto_combat")
        ):
            del sys.modules[key]

    class _FakeUnitTask(enum.IntEnum):
        Idle = 0
        Reserved = 8

    fake_unit_task_mod = ModuleType("sharpy.managers.core.roles.unit_task")
    fake_unit_task_mod.UnitTask = _FakeUnitTask  # type: ignore[attr-defined]
    fake_roles_mod = ModuleType("sharpy.managers.core.roles")
    fake_roles_mod.UnitTask = _FakeUnitTask  # type: ignore[attr-defined]

    class FakeBuildOrder:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    fake_plans_mod = ModuleType("sharpy.plans")
    fake_plans_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]

    class FakeKnowledge:
        def __init__(self) -> None:
            self.roles = MagicMock()
            self.unit_cache = MagicMock()

        def pre_start(self, *a: Any, **kw: Any) -> None:
            pass

        async def start(self) -> None:
            pass

        async def update(self, iteration: int) -> None:
            pass

        async def post_update(self) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            pass

        async def on_end(self, result: Any) -> None:
            pass

        def print(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeKnowledgeBot:
        """sharpy KnowledgeBot 极简 stub。"""

        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = FakeKnowledge()
            self.time = 0.0
            self.minerals = 0
            self.vespene = 0
            self.supply_used = 0
            self.supply_cap = 0
            self.townhalls: list[Any] = []
            self.units = MagicMock()
            self.client = MagicMock()
            self.client.move_camera = AsyncMock(return_value=None)
            self.state = MagicMock()
            self.last_game_loop = -1
            self.realtime = False
            self.active_recipe = ""

        async def on_start(self) -> None:
            pass

        async def on_step(self, iteration: int) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            await self.knowledge.on_unit_destroyed(unit_tag)

        async def on_end(self, result: Any) -> None:
            await self.knowledge.on_end(result)

    fake_sharpy_mod = ModuleType("sharpy")
    fake_knowledges_mod = ModuleType("sharpy.knowledges")
    fake_knowledges_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]
    fake_knowledges_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    fake_kb_mod = ModuleType("sharpy.knowledges.knowledge_bot")
    fake_kb_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

    if "sc2" not in sys.modules:
        fake_sc2 = ModuleType("sc2")
        fake_sc2_position = ModuleType("sc2.position")
        fake_sc2_position.Point2 = lambda t: t  # type: ignore[attr-defined]
        fake_sc2_ids = ModuleType("sc2.ids")
        fake_sc2_unit_typeid = ModuleType("sc2.ids.unit_typeid")
        fake_sc2_unit_typeid.UnitTypeId = MagicMock()  # type: ignore[attr-defined]
        sys.modules["sc2"] = fake_sc2
        sys.modules["sc2.position"] = fake_sc2_position
        sys.modules["sc2.ids"] = fake_sc2_ids
        sys.modules["sc2.ids.unit_typeid"] = fake_sc2_unit_typeid

    sys.modules["sharpy"] = fake_sharpy_mod
    sys.modules["sharpy.knowledges"] = fake_knowledges_mod
    sys.modules["sharpy.knowledges.knowledge_bot"] = fake_kb_mod
    sys.modules["sharpy.plans"] = fake_plans_mod
    sys.modules["sharpy.managers"] = ModuleType("sharpy.managers")
    sys.modules["sharpy.managers.core"] = ModuleType("sharpy.managers.core")
    sys.modules["sharpy.managers.core.roles"] = fake_roles_mod
    sys.modules["sharpy.managers.core.roles.unit_task"] = fake_unit_task_mod
    sys.modules["sharpy.managers.extensions"] = ModuleType("sharpy.managers.extensions")

    return FakeKnowledgeBot


@pytest.fixture(autouse=True)
def _clean_ares_modules_minimap() -> Any:
    _prefixes = ("sharpy", "vibecraft.bot.sharpy_adapter", "vibecraft.bot.auto_combat")
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]


class TestAresFacadeMoveCameraStaged:
    """ADR 0008:move_camera 暂存 + on_step 末尾 drain（sharpy 版）。

    撤回 ADR 0007 的 fire-and-forget(会与 step 主请求并发写 ws,SC2 客户端崩)。
    """

    async def test_move_camera_stages_point_not_immediate_call(self) -> None:
        """move_camera 只暂存 point,**不**立即调 client.move_camera。"""
        FakeKnowledgeBot = _inject_fake_sharpy_for_move_camera()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        facade = instance.facade
        assert facade is not None

        facade.move_camera((50.0, 50.0))
        await asyncio.sleep(0)

        # 关键回归断言:client.move_camera **没**被调用(还没 drain)
        instance.client.move_camera.assert_not_called()
        assert facade._pending_camera_point == (50.0, 50.0)

    async def test_drain_pending_actions_awaits_move_camera(self) -> None:
        """drain_pending_actions 应 await client.move_camera 并清空 pending。"""
        FakeKnowledgeBot = _inject_fake_sharpy_for_move_camera()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        facade = instance.facade
        facade.move_camera((50.0, 50.0))
        await facade.drain_pending_actions()

        instance.client.move_camera.assert_called_once()
        assert facade._pending_camera_point is None

    async def test_drain_with_no_pending_is_noop(self) -> None:
        """无暂存时 drain 不调 client.move_camera。"""
        FakeKnowledgeBot = _inject_fake_sharpy_for_move_camera()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        await instance.facade.drain_pending_actions()
        instance.client.move_camera.assert_not_called()

    async def test_move_camera_does_not_raise_sync(self) -> None:
        """move_camera 是同步方法,调用不抛异常。"""
        FakeKnowledgeBot = _inject_fake_sharpy_for_move_camera()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]

        with patch.object(FakeKnowledgeBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        instance.facade.move_camera((88.0, 90.0))

    async def test_on_step_view_move_stages_then_drains(self) -> None:
        """on_step 消费 view_move:先 facade.move_camera 暂存,末尾 drain_pending_actions。"""
        import queue

        FakeKnowledgeBot = _inject_fake_sharpy_for_move_camera()
        from vibecraft.bot.sharpy_adapter import make_bot_class

        move_camera_calls: list[tuple[float, float]] = []
        drain_calls = 0

        async def fake_drain() -> None:
            nonlocal drain_calls
            drain_calls += 1

        director_mock = MagicMock()
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "view_move", "target_point": [88.5, 134.2]})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )

        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock

        fake_facade = MagicMock()
        fake_facade.move_camera = lambda pt: move_camera_calls.append(pt)
        fake_facade.drain_pending_actions = fake_drain
        instance.facade = fake_facade

        with patch.object(FakeKnowledgeBot, "on_step", new_callable=AsyncMock):
            await instance.on_step(0)

        assert len(move_camera_calls) == 1
        assert move_camera_calls[0] == (88.5, 134.2)
        assert drain_calls == 1


# ---------------------------------------------------------------------------
# WS _handle_view_move 单测
# ---------------------------------------------------------------------------


class TestWsHandleViewMove:
    """ws._handle_view_move：参数校验 + send_command 转发。"""

    async def test_valid_point_sent_to_game_process(self) -> None:
        """合法 target_point → send_command view_move。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True
        gp._proc = fake_proc

        sent_cmds: list[dict[str, Any]] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        await conn._handle_view_move({"type": "view_move", "target_point": [88.5, 134.2]})

        assert len(sent_cmds) == 1
        assert sent_cmds[0]["type"] == "view_move"
        assert sent_cmds[0]["target_point"] == [88.5, 134.2]

    async def test_invalid_point_dropped(self) -> None:
        """target_point 不合法时静默丢弃。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True
        gp._proc = fake_proc

        sent_cmds: list[Any] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        # 非列表、长度不对、字符串值
        for bad_pt in [None, [1], [1, 2, 3], ["a", "b"], "badpoint"]:
            await conn._handle_view_move({"type": "view_move", "target_point": bad_pt})

        assert sent_cmds == []

    async def test_no_game_running_dropped(self) -> None:
        """对局未在跑时 view_move 静默丢弃。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()  # _proc=None → is_running=False

        sent_cmds: list[Any] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        await conn._handle_view_move({"type": "view_move", "target_point": [50.0, 50.0]})

        assert sent_cmds == []

    async def test_dispatch_view_move_calls_handle(self) -> None:
        """_dispatch 收到 view_move 帧时调 _handle_view_move。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()

        conn = WsConnection(ws_mock, registry, game_process=gp)

        called: list[dict[str, Any]] = []
        orig = conn._handle_view_move

        async def _capture(frame: dict[str, Any]) -> None:
            called.append(frame)
            await orig(frame)

        conn._handle_view_move = _capture  # type: ignore[method-assign]

        await conn._dispatch("view_move", {"type": "view_move", "target_point": [10.0, 20.0]})

        assert len(called) == 1


# ---------------------------------------------------------------------------
# _dispatch_upstream minimap 分支
# ---------------------------------------------------------------------------


class TestDispatchUpstreamMinimap:
    """_dispatch_upstream：minimap 分支正确转发帧。"""

    async def test_minimap_frame_forwarded(self) -> None:
        """kind=minimap 消息 → 取 raw['frame'] 直接转发给手机。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"
        gp._bot_state = "running"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        minimap_frame = {
            "type": "minimap",
            "ts": 42.5,
            "map": {"playable": [16, 12, 152, 116], "size": [168, 168]},
            "viewport": {"center": [88.5, 134.2], "size": [24, 18]},
            "units_own": [[88.0, 130.5, "N"]],
            "units_enemy_visible": [],
        }

        await conn._dispatch_upstream({"kind": "minimap", "frame": minimap_frame})

        ws_mock.send.assert_called_once()
        sent_frame = json.loads(ws_mock.send.call_args[0][0])
        assert sent_frame["type"] == "minimap"
        assert sent_frame["ts"] == pytest.approx(42.5, abs=0.001)
        assert sent_frame["map"]["playable"] == [16, 12, 152, 116]
        assert len(sent_frame["units_own"]) == 1

    async def test_minimap_not_treated_as_game_status(self) -> None:
        """minimap 消息不被当 game_status 处理（不改 sc2_state）。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        await conn._dispatch_upstream(
            {
                "kind": "minimap",
                "frame": {
                    "type": "minimap",
                    "ts": 1.0,
                    "map": {"playable": [0, 0, 100, 100], "size": [100, 100]},
                    "viewport": {"center": [50.0, 50.0], "size": [24, 18]},
                    "units_own": [],
                    "units_enemy_visible": [],
                },
            }
        )

        # sc2_state 应保持 playing，不被 minimap 消息改变
        assert gp._sc2_state == "playing"
        # 只发了 minimap 帧，没发 game_status 帧
        ws_mock.send.assert_called_once()
        frame = json.loads(ws_mock.send.call_args[0][0])
        assert frame["type"] == "minimap"
