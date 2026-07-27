"""P0e Task 9: _exec_structure_override 执行器 + prereq + status 透传。

测试场景：
- build 成功（prereq OK + 当前 < target）
- 已达目标（current >= target_count）
- prereq 缺失（未知 Nexus）
- 未知 structure type
- routing：STRUCTURE_OVERRIDE → production_overrides slot
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 2026-05-28 广义 A:_pick_grid_position 调 `from sharpy.interfaces import
# IBuildingSolver`。test 环境必须可 import sharpy(vendor/sharpy 加到 sys.path)
# 否则 _pick_grid_position 静默捕 ImportError 返 None,新增 grid 路径 test 全 fail。
_VENDOR_SHARPY = PROJECT_ROOT / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import Directive
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

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


def _make_director_with_bot(session: GameSession, mock_bot: MagicMock) -> Director:
    """构造带 mock_bot 的 Director（用于 execute_overrides_step 测试）。"""
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_structure_override(
    structure_type: str = "Gateway",
    target_count: int = 4,
    location_hint: str | None = None,
) -> Directive:
    """构造 STRUCTURE_OVERRIDE Directive。"""
    from vibecraft.directives.models import StructureItem, StructureOverridePayload

    payload = StructureOverridePayload(
        items=[
            StructureItem(
                structure_type=structure_type,
                target_count=target_count,
                location_hint=location_hint,
            )
        ],
    )
    return Directive(payload=payload, issued_at=10.0)


def _make_mock_bot(
    gateway_count: int = 0,
    nexus_count: int = 1,
    pending_gateway: float = 0.0,
    pending_nexus: float = 0.0,
    minerals: int = 500,
) -> MagicMock:
    """构造模拟 bot，能响应 structures(type_id).amount 和 already_pending(type_id)。"""
    from sc2.data import Race

    bot = MagicMock()
    bot.race = Race.Protoss  # 2026-05-23 tech_tree 需要 bot.race
    # bot.build 是 async 方法
    bot.build = AsyncMock(return_value=None)

    # structures(type_id) → mock with .amount and .ready(.amount + __len__)
    # 2026-05-23: 新代码用 .ready.amount(真 sc2 Units API),mock 必须支持
    def _structures(type_id: object) -> MagicMock:
        m = MagicMock()
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        if name == "GATEWAY":
            cnt = gateway_count
        elif name == "NEXUS":
            cnt = nexus_count
        else:
            cnt = 0
        m.amount = cnt
        ready_mock = MagicMock()
        ready_mock.amount = cnt  # 假定 all amount ready(老测试 mock 不区分)
        ready_mock.__len__ = MagicMock(return_value=cnt)
        m.ready = ready_mock
        return m

    bot.structures = _structures

    # already_pending(type_id) → float
    def _already_pending(type_id: object) -> float:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        if name == "GATEWAY":
            return pending_gateway
        if name == "NEXUS":
            return pending_nexus
        return 0.0

    bot.already_pending = _already_pending
    bot.minerals = minerals

    # expansion_zones（用于 _resolve_location_hint，可能被调）
    bot.knowledge = MagicMock()
    bot.knowledge.expansion_zones = []

    return bot


# ---------------------------------------------------------------------------
# Tests: routing（STRUCTURE_OVERRIDE → production_overrides）
# ---------------------------------------------------------------------------


class TestStructureOverrideRouting:
    """STRUCTURE_OVERRIDE directive 经 _submit_directives 路由到 production_overrides。"""

    def test_structure_override_goes_to_production_overrides(self, session: GameSession) -> None:
        """_submit_directives → production_overrides list（不进 _in_flight）。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("Gateway", target_count=4)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_structure_override_not_in_standing_orders(self, session: GameSession) -> None:
        """STRUCTURE_OVERRIDE 不进 standing_orders。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("Forge", target_count=2)
        director._submit_directives([d], now=10.0)
        assert not any(s.id == d.id for s in director.standing_orders)


# ---------------------------------------------------------------------------
# Tests: execute_overrides_step dispatch
# ---------------------------------------------------------------------------


class TestStructureOverrideExec:
    """P0e Task 9: _exec_structure_override 的各种场景。

    直接往 director.production_overrides 追加 directive（绕过 board delay），
    然后调 execute_overrides_step，验证 _override_status 结果。
    """

    @pytest.mark.asyncio
    async def test_structure_override_already_done_releases(self, session: GameSession) -> None:
        """2026-05-23 用户:建造完成后 UI 应消失。ready >= target → release directive。

        2026-05-24 用户:完成后 grace 5s 才真删 → directive 保留 + status='done',
        on_tick 5s 后才真 pop。验证 grace 期标记 + bot.build 不再调。
        """
        bot = _make_mock_bot(gateway_count=8, nexus_count=1, pending_gateway=0.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        # 2026-05-24 新行为:grace 期内 directive 保留 + status='done' + done_at
        assert any(s.id == d.id for s in director.production_overrides), "grace 期保留"
        assert director._override_status[d.id]["status"] == "done"
        assert d.id in director._done_at
        bot.build.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_structure_override_build_called_when_below_target(
        self, session: GameSession
    ) -> None:
        """current < target_count + prereq OK → bot.build 被 await，status=active。"""
        bot = _make_mock_bot(gateway_count=5, nexus_count=1, pending_gateway=0.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "active"
        bot.build.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_structure_override_prereq_missing_gateway(self, session: GameSession) -> None:
        """2026-05-23 用户:依赖自动补齐扩到三族,数据源从手写表换成 python-sc2 内置。

        旧测试用 GATEWAY 缺 NEXUS,但 PROTOSS_TECH_REQUIREMENT[GATEWAY] = PYLON
        (PYLON 在 _BASE_STRUCTURES 过滤),GATEWAY 不再 emit prereq missing。
        改用 CYBERNETICSCORE 缺 GATEWAY 这种真有 chain 的 case。
        """
        bot = _make_mock_bot(gateway_count=0, nexus_count=1)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("CyberneticsCore", target_count=1)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        reason = status_info.get("reason", "")
        assert "GATEWAY" in reason or "Gateway" in reason

    @pytest.mark.asyncio
    async def test_structure_override_unknown_type(self, session: GameSession) -> None:
        """未知 structure_type → status=on_hold，reason 含 '未知'。"""
        bot = _make_mock_bot()
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("NotAStructure", target_count=1)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        assert "未知" in status_info.get("reason", "")

    @pytest.mark.asyncio
    async def test_structure_override_pending_counts_toward_current(
        self, session: GameSession
    ) -> None:
        """structures.amount + already_pending 合计 >= target → 不造新的，status=active。"""
        # 5 ready + 3 pending = 8 = target → 已达成
        bot = _make_mock_bot(gateway_count=5, nexus_count=1, pending_gateway=3.0)
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "active"
        bot.build.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_structure_override_cyberneticscore_prereq_gateway(
        self, session: GameSession
    ) -> None:
        """CYBERNETICSCORE 需要 GATEWAY,若无 Gateway → on_hold(+ 自动 emit auto_prereq)。"""
        from sc2.data import Race

        bot = MagicMock()
        bot.race = Race.Protoss  # 2026-05-23 tech_tree 需 race
        bot.time = 10.0
        bot.build = AsyncMock(return_value=None)
        bot.knowledge = MagicMock()
        bot.knowledge.expansion_zones = []

        def _structures(type_id: object) -> MagicMock:
            m = MagicMock()
            # 无任何建筑
            m.amount = 0
            m.ready = []
            return m

        bot.structures = _structures
        bot.already_pending = MagicMock(return_value=0.0)

        director = _make_director_with_bot(session, bot)
        d = _make_structure_override("CyberneticsCore", target_count=1)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "on_hold"
        reason = status_info.get("reason", "")
        assert "GATEWAY" in reason


# ---------------------------------------------------------------------------
# 2026-05-23 用户:依赖自动补齐扩到三族,_REQUIRED_STRUCTURE 手写表删除,
# 改用 vibecraft.bot.tech_tree(python-sc2 内置数据)。完整覆盖见
# tests/unit/test_tech_tree.py。这里只保留旧的 *struct prereq sanity check*,
# 用 tech_tree.required_for 替换。
# ---------------------------------------------------------------------------


class TestStructPrereqViaTechTree:
    """sanity check:几个常见 protoss 建筑 prereq(数据源已切到 python-sc2 内置)。"""

    def test_cyberneticscore_requires_gateway(self) -> None:
        from sc2.data import Race
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.tech_tree import required_for

        assert required_for(UnitTypeId.CYBERNETICSCORE, Race.Protoss) == UnitTypeId.GATEWAY

    def test_twilightcouncil_requires_cyberneticscore(self) -> None:
        from sc2.data import Race
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.tech_tree import required_for

        assert required_for(UnitTypeId.TWILIGHTCOUNCIL, Race.Protoss) == UnitTypeId.CYBERNETICSCORE

    def test_roboticsbay_requires_roboticsfacility(self) -> None:
        from sc2.data import Race
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.tech_tree import required_for

        assert required_for(UnitTypeId.ROBOTICSBAY, Race.Protoss) == UnitTypeId.ROBOTICSFACILITY


class TestResolveLocationHintFallback:
    """2026-05-27 Issue C regression:`_resolve_location_hint(None, ...)` 必须返
    Point2(不能是 None),否则 _bot.build(near=None) 触发 sc2 内部
    `assert isinstance(near, (Unit, Point2))` 静默 raise,structure 永远不建。

    crash 触发链:玩家"出 1 个 VR" → structure_override(location_hint=None)
    → _resolve_location_hint(None) returned None → bot.build(ROBO, near=None)
    → AssertionError → except Exception 静默吞 → VR 永远不造。
    """

    def test_hint_none_falls_back_to_zone_zero(self, session: GameSession) -> None:
        """hint=None + zones 非空 → 返 zones[0].gather_point(Q2 fix: 不挡矿线)。"""
        bot = _make_mock_bot()
        # Q2 fix: _own_main_fallback 现在优先 gather_point 而非 center_location
        zone0 = MagicMock()
        zone0.gather_point = "ZONE0_GATHER_SENTINEL"
        zone0.center_location = "ZONE0_CENTER_SENTINEL"
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock())
        assert result == "ZONE0_GATHER_SENTINEL"

    def test_hint_none_no_zones_falls_back_to_townhall(self, session: GameSession) -> None:
        """hint=None + zones 空 → fallback townhalls.first.position。"""
        bot = _make_mock_bot()
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = []
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: True
        townhalls.first.position = "TOWNHALL_POS_SENTINEL"
        bot.townhalls = townhalls
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock())
        assert result == "TOWNHALL_POS_SENTINEL"

    def test_hint_none_returns_non_none_for_robotics(self, session: GameSession) -> None:
        """完整 regression:出 VR 时 _resolve_location_hint(None, ROBO) 非 None。
        修前 returns None → bot.build(ROBO, near=None) 静默 assert fail。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot()
        zone0 = MagicMock()
        zone0.center_location = object()
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, UnitTypeId.ROBOTICSFACILITY)
        assert result is not None


def _setup_bot_with_grid(
    bot: MagicMock,
    grid3x3: list,
    grid2x2: list | None = None,
    occupied_points: set | None = None,
) -> None:
    """配 mock bot 让 _pick_grid_position 走 sharpy IBuildingSolver path。

    grid3x3/grid2x2 = MagicMock Point2 列表(每个 Point2 有 .distance_to 方法可调)
    occupied_points = 哪些点应当被 `buildings.closer_than(1, p)` 视为已占用
    """
    occupied = occupied_points or set()
    solver = MagicMock()
    solver.buildings3x3 = grid3x3
    solver.buildings2x2 = grid2x2 if grid2x2 is not None else []
    bot.knowledge.get_required_manager = MagicMock(return_value=solver)

    def _closer_than(_dist, p):
        return p in occupied

    structures = MagicMock()
    structures.closer_than = _closer_than
    bot.structures = structures


def _make_point(label: str, dist_func=None):
    """构造 mock Point2 with .distance_to() method。

    dist_func: callable(other) → float;不传则返身份比较(只跟自己 dist=0,其他 inf)
    """
    p = MagicMock()
    p._label = label  # 调试 / 比较用
    if dist_func is None:

        def _self_dist(other, _p=p):
            return 0.0 if other is _p else 9999.0

        p.distance_to = _self_dist
    else:
        p.distance_to = dist_func
    return p


class TestGeneralizedAGridPath:
    """2026-05-28 广义 A:hint=None/main/natural/ramp 都走 sharpy
    IBuildingSolver grid,挑「距 anchor 最近的空位」。
    避免修 Issue C 时 fallback own_main center_location 导致建筑堆矿区。
    """

    def test_hint_none_picks_first_grid_position(self, session: GameSession) -> None:
        """hint=None + grid 非空 + 全未占用 → 返 grid[0](默认排序第一个)。"""
        bot = _make_mock_bot()
        p0, p1, p2 = _make_point("p0"), _make_point("p1"), _make_point("p2")
        _setup_bot_with_grid(bot, grid3x3=[p0, p1, p2])
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock(name="GATEWAY"))
        assert result is p0, "hint=None 应取 grid 第一个空位,不是 zones[0].center_location"

    def test_hint_none_skips_occupied_grid_positions(self, session: GameSession) -> None:
        """hint=None + grid[0] 已被占用 → 跳到第一个未占用的 grid[1]。"""
        bot = _make_mock_bot()
        p0, p1, p2 = _make_point("p0"), _make_point("p1"), _make_point("p2")
        _setup_bot_with_grid(bot, grid3x3=[p0, p1, p2], occupied_points={p0})
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock(name="GATEWAY"))
        assert result is p1

    def test_hint_main_picks_grid_position_nearest_to_main_center(
        self, session: GameSession
    ) -> None:
        """hint=main + grid 多个空位 → 返「距 zones[0].center_location 最近」的空位。

        广义 A 核心:hint=main 不再返矿区中心,而是返主基地区域内合理 grid。
        """
        bot = _make_mock_bot()
        main_center = MagicMock(name="MAIN_CENTER")
        # 构造 3 个 grid 点,距 main_center 距离分别 30 / 5 / 20 → 应选 p_close
        p_far = _make_point("p_far", dist_func=lambda anchor: 30.0)
        p_close = _make_point("p_close", dist_func=lambda anchor: 5.0)
        p_mid = _make_point("p_mid", dist_func=lambda anchor: 20.0)
        _setup_bot_with_grid(bot, grid3x3=[p_far, p_close, p_mid])

        zone0 = MagicMock()
        zone0.center_location = main_center
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("main", MagicMock(name="GATEWAY"))
        assert result is p_close, (
            "hint=main 应取距 main_center 最近的 grid 空位(广义 A),不是 main_center 本身"
        )

    def test_hint_natural_picks_nearest_to_natural(self, session: GameSession) -> None:
        """hint=natural + grid 多个空位 → 返「距 zones[1].center_location 最近」的空位。"""
        bot = _make_mock_bot()
        nat_center = MagicMock(name="NAT_CENTER")
        p_near_main = _make_point("near_main", dist_func=lambda anchor: 40.0)
        p_near_nat = _make_point("near_nat", dist_func=lambda anchor: 6.0)
        _setup_bot_with_grid(bot, grid3x3=[p_near_main, p_near_nat])

        zone0, zone1 = MagicMock(), MagicMock()
        zone0.center_location = MagicMock()
        zone1.center_location = nat_center
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0, zone1]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("natural", MagicMock(name="GATEWAY"))
        assert result is p_near_nat

    def test_pylon_uses_2x2_grid(self, session: GameSession) -> None:
        """PYLON(2x2 建筑)应走 buildings2x2 list,不是 buildings3x3。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot()
        p_in_3x3 = _make_point("3x3_only")
        p_in_2x2 = _make_point("2x2_only")
        _setup_bot_with_grid(bot, grid3x3=[p_in_3x3], grid2x2=[p_in_2x2])
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, UnitTypeId.PYLON)
        assert result is p_in_2x2, "PYLON 应用 buildings2x2 grid"

    def test_gateway_uses_3x3_grid(self, session: GameSession) -> None:
        """GATEWAY(3x3 建筑)应走 buildings3x3 list。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot()
        p_in_3x3 = _make_point("3x3_only")
        p_in_2x2 = _make_point("2x2_only")
        _setup_bot_with_grid(bot, grid3x3=[p_in_3x3], grid2x2=[p_in_2x2])
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, UnitTypeId.GATEWAY)
        assert result is p_in_3x3

    def test_supplydepot_uses_2x2_grid(self, session: GameSession) -> None:
        """人族 SUPPLYDEPOT(2x2)应走 buildings2x2 grid。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot()
        p_in_3x3 = _make_point("3x3_only")
        p_in_2x2 = _make_point("2x2_only")
        _setup_bot_with_grid(bot, grid3x3=[p_in_3x3], grid2x2=[p_in_2x2])
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, UnitTypeId.SUPPLYDEPOT)
        assert result is p_in_2x2

    def test_grid_all_occupied_falls_back(self, session: GameSession) -> None:
        """所有 grid 点都被占 → fallback own_main gather_point(防退化到 None)。

        Issue C 防 None 兜底必须仍生效 — 不能让 _bot.build(near=None) 炸。
        Q2 fix: fallback 现在用 gather_point 而非 center_location。
        """
        bot = _make_mock_bot()
        p0, p1 = _make_point("p0"), _make_point("p1")
        zone_gather = MagicMock(name="ZONE_GATHER")
        _setup_bot_with_grid(bot, grid3x3=[p0, p1], occupied_points={p0, p1})
        zone0 = MagicMock()
        zone0.gather_point = zone_gather
        zone0.center_location = MagicMock(name="ZONE_CENTER")
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock(name="GATEWAY"))
        assert result is zone_gather, "grid 满 → fallback own_main gather_point 而非 None"

    def test_front_hint_bypasses_grid(self, session: GameSession) -> None:
        """hint=front(proxy 用,刻意远离己方 grid)→ enemy_main_base_ramp.top_center,
        不走 building_solver。"""
        bot = _make_mock_bot()
        enemy_ramp_top = MagicMock(name="ENEMY_RAMP_TOP")
        bot.knowledge.enemy_main_base_ramp = MagicMock()
        bot.knowledge.enemy_main_base_ramp.top_center = enemy_ramp_top
        # 即使 grid 有点也不应被选(front 走另一条 path)
        _setup_bot_with_grid(bot, grid3x3=[_make_point("grid_pt")])
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("front", MagicMock(name="GATEWAY"))
        assert result is enemy_ramp_top

    def test_building_solver_unavailable_falls_back(self, session: GameSession) -> None:
        """sharpy IBuildingSolver 不可用(get_required_manager raise)→ fallback 旧路径。
        Q2 fix: fallback 用 gather_point 而非 center_location。"""
        bot = _make_mock_bot()
        bot.knowledge.get_required_manager = MagicMock(side_effect=Exception("no solver"))
        zone0 = MagicMock()
        zone0.gather_point = "ZONE0_GATHER_SENTINEL"
        zone0.center_location = "ZONE0_CENTER_SENTINEL"
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint(None, MagicMock(name="GATEWAY"))
        assert result == "ZONE0_GATHER_SENTINEL"


class TestStructureItemDeltaSchema:
    """2026-05-28 用户:structure_override 加 delta 字段(增量语义)。

    schema 约束:target_count 与 delta 必须二选一(都给/都不给都不允许)。
    """

    def test_delta_only_valid(self) -> None:
        from vibecraft.directives.models import StructureItem

        it = StructureItem(structure_type="Forge", delta=1)
        assert it.delta == 1
        assert it.target_count is None

    def test_target_only_valid(self) -> None:
        from vibecraft.directives.models import StructureItem

        it = StructureItem(structure_type="Forge", target_count=2)
        assert it.target_count == 2
        assert it.delta is None

    def test_both_set_raises(self) -> None:
        from pydantic import ValidationError

        from vibecraft.directives.models import StructureItem

        with pytest.raises(ValidationError):
            StructureItem(structure_type="Forge", delta=1, target_count=2)

    def test_neither_set_raises(self) -> None:
        from pydantic import ValidationError

        from vibecraft.directives.models import StructureItem

        with pytest.raises(ValidationError):
            StructureItem(structure_type="Forge")


class TestResolveStructureDelta:
    """Director._resolve_structure_delta:submit 时把 delta 解算成绝对 target_count。"""

    def test_delta_one_with_existing_one_becomes_target_two(self, session: GameSession) -> None:
        """玩家"补一个 BF"(delta=1),当前已 1 BF → target_count=2(1+1)。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot()
        # mock bot.structures(FORGE).ready.amount = 1
        forge_units = MagicMock()
        forge_units.amount = 1
        forge_ready = MagicMock()
        forge_ready.amount = 1
        forge_units.ready = forge_ready

        orig_structures = bot.structures

        def _structures(type_id):
            if type_id == UnitTypeId.FORGE:
                return forge_units
            return orig_structures(type_id)

        bot.structures = _structures

        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Forge", delta=1)]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        item = d_resolved.payload.items[0]
        assert item.delta is None  # cleared
        assert item.target_count == 2  # 1 ready + 1 delta

    def test_delta_two_with_existing_six_becomes_target_eight(self, session: GameSession) -> None:
        """玩家"补 2 个 BG"(delta=2),当前已 6 BG → target_count=8。"""

        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot(gateway_count=6)
        # _make_mock_bot 用 ready.amount = count(gateway_count=6)
        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Gateway", delta=2)]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        item = d_resolved.payload.items[0]
        assert item.target_count == 8
        assert item.delta is None

    def test_delta_when_none_built_becomes_just_delta(self, session: GameSession) -> None:
        """当前没造过 X(ready=0),delta=1 → target_count=1。"""
        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot()  # 默认 FORGE 不在 mock 表里 → ready=0
        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Forge", delta=1)]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        assert d_resolved.payload.items[0].target_count == 1

    def test_delta_counts_warpgate_as_gateway(self, session: GameSession) -> None:
        """中后期 BG 全升 WARPGATE 后"新增 3 个 BG"(delta=3):应数等价体。

        踩坑(2026-06-08 真局):玩家 4bg 开局,后期 7 个 BG 全升 WARPGATE,说"新增3个bg"。
        修前:_resolve_structure_delta 用 structures(GATEWAY).ready.amount=0(全是 WARPGATE 了)
        → target=0+3=3 → 执行层 _count_equivalent 数到 7 个 WG ≥ 3 → 秒判 structure_done,
        一个都没造。修后:用 _count_equivalent → ready=7 → target=7+3=10,执行层补到 10 才停。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot()
        # GATEWAY 全升 WARPGATE:structures(GATEWAY).ready=0, structures(WARPGATE).ready=7
        gw = MagicMock()
        gw.amount = 0
        gw.ready = MagicMock()
        gw.ready.amount = 0
        wg = MagicMock()
        wg.amount = 7
        wg.ready = MagicMock()
        wg.ready.amount = 7
        orig_structures = bot.structures

        def _structures(type_id):
            if type_id == UnitTypeId.GATEWAY:
                return gw
            if type_id == UnitTypeId.WARPGATE:
                return wg
            return orig_structures(type_id)

        bot.structures = _structures
        bot.already_pending = lambda _t: 0

        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Gateway", delta=3)]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        item = d_resolved.payload.items[0]
        assert item.delta is None
        assert item.target_count == 10  # 7 等价体 + 3 delta(不是 0+3=3)

    def test_target_count_passthrough(self, session: GameSession) -> None:
        """target_count 设了直接用,不被 delta 路径影响。"""
        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot(gateway_count=6)
        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Gateway", target_count=4)]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        # 没 delta → 原样返
        assert d_resolved.payload.items[0].target_count == 4

    def test_mixed_items_resolved_individually(self, session: GameSession) -> None:
        """多 item:delta 的 resolve,target_count 的不动。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        bot = _make_mock_bot()
        forge_units = MagicMock()
        forge_ready = MagicMock()
        forge_ready.amount = 1
        forge_units.ready = forge_ready
        orig_structures = bot.structures

        def _structures(type_id):
            if type_id == UnitTypeId.FORGE:
                return forge_units
            return orig_structures(type_id)

        bot.structures = _structures

        director = _make_director_with_bot(session, bot)
        d = Directive(
            payload=StructureOverridePayload(
                items=[
                    StructureItem(structure_type="Forge", delta=1),  # → target=2
                    StructureItem(structure_type="Gateway", target_count=4),  # 保持
                ]
            ),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        items = d_resolved.payload.items
        assert items[0].structure_type == "Forge"
        assert items[0].target_count == 2
        assert items[0].delta is None
        assert items[1].structure_type == "Gateway"
        assert items[1].target_count == 4

    def test_non_structure_override_passthrough(self, session: GameSession) -> None:
        """非 STRUCTURE_OVERRIDE directive(production_override 等)原样返。"""
        from vibecraft.directives.models import (
            Directive,
            ProductionItem,
            ProductionOverridePayload,
        )

        director = _make_director_with_bot(session, _make_mock_bot())
        d = Directive(
            payload=ProductionOverridePayload(items=[ProductionItem(unit_type="Stalker", count=4)]),
            issued_at=10.0,
        )
        d_resolved = director._resolve_structure_delta(d)
        assert d_resolved is d  # 原对象返回


# ---------------------------------------------------------------------------
# Q1 fix: _count_equivalent — Gateway/Warpgate 同质化计数
# ---------------------------------------------------------------------------


class TestCountEquivalent:
    """Q1 fix (2026-05-29): _count_equivalent 把升级体算在内,防止 GATEWAY 全升 WG 后
    director 误判"还需补建"重复造 BG。"""

    def _make_bot_with_types(
        self,
        counts: dict,
        pending: dict | None = None,
    ) -> MagicMock:
        """构造 bot mock,counts = {type_name: (ready_amount, total_amount)},
        pending = {type_name: float}。"""
        from sc2.data import Race

        bot = MagicMock()
        bot.race = Race.Protoss

        if pending is None:
            pending = {}

        def _structures(type_id: object) -> MagicMock:
            name = type_id.name if hasattr(type_id, "name") else str(type_id)
            m = MagicMock()
            ready_amt, total_amt = counts.get(name, (0, 0))
            m.amount = total_amt
            ready_mock = MagicMock()
            ready_mock.amount = ready_amt
            m.ready = ready_mock
            return m

        def _already_pending(type_id: object) -> float:
            name = type_id.name if hasattr(type_id, "name") else str(type_id)
            return float(pending.get(name, 0))

        bot.structures = _structures
        bot.already_pending = _already_pending
        bot.build = AsyncMock(return_value=None)
        bot.knowledge = MagicMock()
        bot.knowledge.expansion_zones = []
        bot.minerals = 500
        return bot

    def test_count_equivalent_aggregates_gateway_warpgate(self, session: GameSession) -> None:
        """4 WG + 0 raw GW → ready=4, total=4。
        防止 GATEWAY 全升 WARPGATE 后 director 再造 4 个 BG。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = self._make_bot_with_types(
            counts={"GATEWAY": (0, 0), "WARPGATE": (4, 4)},
        )
        director = _make_director_with_bot(session, bot)
        ready, total = director._count_equivalent(UnitTypeId.GATEWAY)
        assert ready == 4
        assert total == 4

    def test_count_equivalent_zerg_hatch_lair_hive(self, session: GameSession) -> None:
        """1 HATCHERY + 2 LAIR + 0 HIVE → ready=3, total=3。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = self._make_bot_with_types(
            counts={"HATCHERY": (1, 1), "LAIR": (2, 2), "HIVE": (0, 0)},
        )
        director = _make_director_with_bot(session, bot)
        ready, total = director._count_equivalent(UnitTypeId.HATCHERY)
        assert ready == 3
        assert total == 3

    def test_count_equivalent_type_not_in_table_falls_through(self, session: GameSession) -> None:
        """不在 _STRUCTURE_EQUIVALENTS 表里的类型(如 FORGE) → 只查自身。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = self._make_bot_with_types(counts={"FORGE": (2, 2)})
        director = _make_director_with_bot(session, bot)
        ready, total = director._count_equivalent(UnitTypeId.FORGE)
        assert ready == 2
        assert total == 2

    @pytest.mark.asyncio
    async def test_exec_structure_override_skips_when_warpgates_satisfy_target(
        self, session: GameSession
    ) -> None:
        """LLM target=8 个 BG,但已有 8 WG → 不 build,directive 标 done。

        Q1 修前:structures(GATEWAY)=0 → total=0 → 重复造 8 个 BG。
        Q1 修后:_count_equivalent(GATEWAY) = (8, 8) → all_ready=True → release。
        """
        bot = self._make_bot_with_types(
            counts={"GATEWAY": (0, 0), "WARPGATE": (8, 8)},
        )
        # 补充 bot.time 和 knowledge.zone_manager(执行路径需要)
        bot.time = 10.0
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = []
        director = _make_director_with_bot(session, bot)

        d = _make_structure_override("Gateway", target_count=8)
        director.production_overrides.append(d)
        await director.execute_overrides_step(now=10.0)

        # Q1 fix: 8 WG 算数 → all_ready=True → release + bot.build 不被调
        status_info = director._override_status.get(d.id, {})
        assert status_info.get("status") == "done", (
            f"8 WG 满足 target=8 GW,应 done,实际 {status_info}"
        )
        bot.build.assert_not_awaited()


# ---------------------------------------------------------------------------
# Q2 fix: _resolve_location_hint — 用 gather_point 而非 center_location 当锚点
# ---------------------------------------------------------------------------


class TestResolveLocationHintGatherPoint:
    """Q2 fix (2026-05-29): hint=main/natural 时 anchor 用 gather_point(Nexus 后院),
    不用 center_location(矿区中心),防止建筑落到 Nexus↔mineral 通道挡 probe。"""

    def test_hint_main_uses_gather_point_as_anchor(self, session: GameSession) -> None:
        """hint=main + zone0 有 gather_point/center_location 两个不同点
        → fallback path 返 gather_point 而非 center_location。"""

        bot = _make_mock_bot()
        zone0 = MagicMock()
        zone0.gather_point = "GATHER_SENTINEL"
        zone0.center_location = "CENTER_SENTINEL"
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        # 让 _pick_grid_position 失败(IBuildingSolver 不可用),强制走 legacy_fallback
        bot.knowledge.get_required_manager = MagicMock(side_effect=Exception("no solver"))
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("main", MagicMock())
        assert result == "GATHER_SENTINEL", (
            f"hint=main 应用 gather_point 当 anchor/fallback,实际返 {result!r}"
        )

    def test_hint_natural_uses_gather_point_of_zone1(self, session: GameSession) -> None:
        """hint=natural + zone1 有 gather_point → legacy fallback 返 zone1.gather_point。"""
        bot = _make_mock_bot()
        zone0 = MagicMock()
        zone0.gather_point = "Z0_GATHER"
        zone0.center_location = "Z0_CENTER"
        zone1 = MagicMock()
        zone1.gather_point = "Z1_GATHER"
        zone1.center_location = "Z1_CENTER"
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0, zone1]
        bot.knowledge.get_required_manager = MagicMock(side_effect=Exception("no solver"))
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("natural", MagicMock())
        assert result == "Z1_GATHER"

    def test_hint_main_zone_no_gather_point_falls_back_to_center(
        self, session: GameSession
    ) -> None:
        """gather_point 属性不存在(旧 sharpy zone mock)→ fallback center_location。"""
        bot = _make_mock_bot()
        zone0 = MagicMock(spec=["center_location"])  # 无 gather_point 属性
        zone0.center_location = "CENTER_ONLY"
        bot.knowledge.zone_manager = MagicMock()
        bot.knowledge.zone_manager.expansion_zones = [zone0]
        bot.knowledge.get_required_manager = MagicMock(side_effect=Exception("no solver"))
        director = _make_director_with_bot(session, bot)

        result = director._resolve_location_hint("main", MagicMock())
        assert result == "CENTER_ONLY"
