"""人族建筑起飞/移动（STRUCTURE_MOVE）schema + 执行层单测。

覆盖：
1. schema：StructureMovePayload 能 parse；进 Payload 判别联合；额外字段被拒。
2. Director._apply_to_facade STRUCTURE_MOVE 分支：注册进 _structure_move_orders。
3. Director._tick_structure_move 状态机（复用 #543 LIFT→FLY→LAND 套路）：
   - FIND：from_spot 附近找不到 townhall → failed。
   - 落地 + to_spot=None → 发 LIFT，起飞后 → done（悬停）。
   - 落地 + to_spot 给了 → LIFT → 飞 → LAND @ 落点 → 落地后 done。
   - PlanetaryFortress → 友好拒绝，不发 LIFT。
   - OrbitalCommand（非 CommandCenter）→ 按真实 type_id 取 LIFT_ORBITALCOMMAND。
   - 建筑消失 → failed。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import Directive, StructureMovePayload
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ==========================================================================
# 1. Schema
# ==========================================================================


def test_structure_move_payload_parses_minimal() -> None:
    p = StructureMovePayload.model_validate({"type": "structure_move", "from_spot": "main"})
    assert p.type.value == "structure_move"
    assert p.from_spot == "main"
    assert p.to_spot is None
    assert p.structure_type is None


def test_structure_move_payload_with_to_spot() -> None:
    p = StructureMovePayload.model_validate(
        {
            "type": "structure_move",
            "structure_type": "CommandCenter",
            "from_spot": "main",
            "to_spot": "natural",
        }
    )
    assert p.to_spot == "natural"
    assert p.structure_type == "CommandCenter"


def test_structure_move_payload_in_payload_union() -> None:
    from vibecraft.directives.models import Payload

    adapter = TypeAdapter(Payload)
    obj = adapter.validate_python({"type": "structure_move", "from_spot": "main"})
    assert isinstance(obj, StructureMovePayload)


def test_structure_move_payload_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StructureMovePayload.model_validate(
            {"type": "structure_move", "from_spot": "main", "unknown_field": "oops"}
        )


def test_structure_move_payload_requires_from_spot() -> None:
    with pytest.raises(ValidationError):
        StructureMovePayload.model_validate({"type": "structure_move"})


def test_structure_move_payload_to_spot_accepts_camera_string() -> None:
    """LLM 输出 to_spot="camera"（"降落在这里"）合法。"""
    p = StructureMovePayload.model_validate(
        {"type": "structure_move", "from_spot": "main", "to_spot": "camera"}
    )
    assert p.to_spot == "camera"


def test_structure_move_payload_to_spot_accepts_tuple() -> None:
    """to_spot 也接受 tuple（_inject_camera_point 注入后的形态）。"""
    p = StructureMovePayload(from_spot="main", to_spot=(12.0, 34.0))
    assert p.to_spot == (12.0, 34.0)


# ==========================================================================
# 1b. _inject_camera_point：to_spot="camera" → 注入镜头坐标
# ==========================================================================


def test_inject_camera_point_replaces_structure_move_to_spot() -> None:
    """2026-07-08 用户补充1："降落在这里" → to_spot="camera" 被替换成真实坐标 tuple。"""
    from vibecraft.bot import Director, FakeFacade
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
    from vibecraft.logging_ import GameSession, GameSessionConfig

    session = GameSession(GameSessionConfig(use_null_sinks=True))
    try:
        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "terran.yaml",
        )
        parser = IntentParser(provider, library, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_directive(to_spot="camera")
        director._inject_camera_point([d], (40.0, 50.0))

        assert d.payload.to_spot == (40.0, 50.0)
    finally:
        session.close()


def test_inject_camera_point_noop_when_to_spot_not_camera() -> None:
    """to_spot 不是 "camera" 时不动它（如 "natural"）。"""
    from vibecraft.bot import Director, FakeFacade
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
    from vibecraft.logging_ import GameSession, GameSessionConfig

    session = GameSession(GameSessionConfig(use_null_sinks=True))
    try:
        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "terran.yaml",
        )
        parser = IntentParser(provider, library, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_directive(to_spot="natural")
        director._inject_camera_point([d], (40.0, 50.0))

        assert d.payload.to_spot == "natural"
    finally:
        session.close()


# ==========================================================================
# 2. Director fixtures
# ==========================================================================


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_director(session: GameSession, mock_bot) -> Director:
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "terran.yaml",
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_directive(from_spot: str = "main", to_spot: str | None = None) -> Directive:
    payload = StructureMovePayload(from_spot=from_spot, to_spot=to_spot)
    return Directive(payload=payload, issued_at=0.0, source_text="主基地飞起来")


class _Townhall:
    """最小 townhall stub：tag / type_id / position / is_flying。"""

    def __init__(self, tag: int, type_name: str, pos, is_flying: bool = False) -> None:
        from sc2.ids.unit_typeid import UnitTypeId

        self.tag = tag
        self.type_id = UnitTypeId[type_name]
        self.position = pos
        self.is_flying = is_flying


def _bot_with_townhall(townhall, *, can_place: bool = True):
    """mock bot：structures(type_id) 按落地/飞行变体返回；can_place_single 可控。"""
    from sc2.position import Point2

    bot = MagicMock()
    bot.start_location = Point2((10.0, 10.0))
    zone_mgr = SimpleNamespace(
        expansion_zones=[
            SimpleNamespace(center_location=Point2((10.0, 10.0))),
            SimpleNamespace(center_location=Point2((40.0, 40.0))),
        ]
    )
    bot.knowledge = SimpleNamespace(zone_manager=zone_mgr)

    real_type_name = townhall.type_id.name
    flying_name = f"{real_type_name}FLYING"

    def _find_by_tag(units, tag):
        for u in units:
            if int(u.tag) == tag:
                return u
        return None

    class _Units(list):
        def find_by_tag(self, tag):
            return _find_by_tag(self, tag)

        def closest_to(self, point):
            return min(self, key=lambda u: u.position.distance_to(point))

    def _structures(type_id):
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        if name == real_type_name:
            return _Units([] if townhall.is_flying else [townhall])
        if name == flying_name:
            return _Units([townhall] if townhall.is_flying else [])
        return _Units([])

    bot.structures = _structures
    bot.townhalls = _Units([townhall])

    async def _cps(building, pos):
        return can_place

    bot.can_place_single = _cps
    return bot


# ==========================================================================
# 3. _apply_to_facade 注册
# ==========================================================================


def test_apply_to_facade_registers_structure_move(session: GameSession) -> None:
    townhall = _Townhall(999, "COMMANDCENTER", pos=SimpleNamespace())
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive()
    director._apply_to_facade(directive, now=0.0)

    assert directive.id in director._structure_move_orders
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"


# ==========================================================================
# 4. _tick_structure_move 状态机
# ==========================================================================


def test_tick_no_townhall_fails(session: GameSession) -> None:
    """from_spot 能解析出坐标，但附近找不到任何 townhall → failed。

    用 "natural" 当 from_spot：resolve 走 zone_manager（不依赖 townhalls），
    再让 structures() 对所有 townhall 类型都返回空 → _find_nearest_townhall=None。
    """
    from sc2.position import Point2

    bot = MagicMock()
    bot.start_location = Point2((10.0, 10.0))
    zone_mgr = SimpleNamespace(
        expansion_zones=[
            SimpleNamespace(center_location=Point2((10.0, 10.0))),
            SimpleNamespace(center_location=Point2((40.0, 40.0))),
        ]
    )
    bot.knowledge = SimpleNamespace(zone_manager=zone_mgr)

    def _structures(type_id):
        return []

    bot.structures = _structures
    director = _make_director(session, bot)

    directive = _make_directive(from_spot="natural")
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "failed"
    assert directive.id not in director._structure_move_orders


def test_tick_lift_then_hover_done(session: GameSession) -> None:
    """to_spot=None：LIFT → 起飞后即 done（悬停）。"""
    from sc2.position import Point2

    townhall = _Townhall(1001, "COMMANDCENTER", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive(to_spot=None)
    director._apply_to_facade(directive, now=0.0)

    # tick 1：落地 → 发 LIFT
    asyncio.run(director._tick_structure_move(now=1.0))
    casts = director.facade.casts
    assert any(c[0] == 1001 and c[1] == "LIFT_COMMANDCENTER" for c in casts), casts
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"
    assert directive.id in director._structure_move_orders

    # 模拟起飞生效
    townhall.is_flying = True

    # tick 2：已在飞 + to_spot=None → done
    asyncio.run(director._tick_structure_move(now=2.0))
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "done"
    assert directive.id not in director._structure_move_orders


def test_tick_lift_fly_land(session: GameSession) -> None:
    """to_spot 给了：LIFT → 飞 → LAND @ 落点 → 落地后 done。"""
    from sc2.position import Point2

    townhall = _Townhall(1002, "COMMANDCENTER", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall, can_place=True)
    director = _make_director(session, bot)

    directive = _make_directive(to_spot="natural")
    director._apply_to_facade(directive, now=0.0)

    # tick 1：落地 → LIFT
    asyncio.run(director._tick_structure_move(now=1.0))
    assert any(c[1] == "LIFT_COMMANDCENTER" for c in director.facade.casts)

    townhall.is_flying = True

    # tick 2：飞行中 + to_spot 给了 → 进入 landing，算落点 + 发 LAND
    asyncio.run(director._tick_structure_move(now=2.0))
    land_casts = [c for c in director.facade.casts if c[1] == "LAND_COMMANDCENTER"]
    assert land_casts, director.facade.casts
    target = land_casts[-1][2]
    assert target is not None and target["kind"] == "point"
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"

    # 模拟落地
    townhall.is_flying = False

    # tick 3：已落地 → done
    asyncio.run(director._tick_structure_move(now=3.0))
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "done"
    assert directive.id not in director._structure_move_orders


def test_tick_planetary_fortress_rejected(session: GameSession) -> None:
    """PlanetaryFortress 不能起飞 → 友好拒绝，不发任何 LIFT。"""
    from sc2.position import Point2

    townhall = _Townhall(1003, "PLANETARYFORTRESS", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive()
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "failed"
    assert not director.facade.casts
    assert directive.id not in director._structure_move_orders


def test_tick_orbital_command_uses_real_ability(session: GameSession) -> None:
    """升过 OrbitalCommand 的主基 → 用 LIFT_ORBITALCOMMAND（不是 LIFT_COMMANDCENTER）。"""
    from sc2.position import Point2

    townhall = _Townhall(1004, "ORBITALCOMMAND", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive()
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))

    casts = director.facade.casts
    assert any(c[1] == "LIFT_ORBITALCOMMAND" for c in casts), casts
    assert not any(c[1] == "LIFT_COMMANDCENTER" for c in casts)


def test_tick_already_flying_with_target_skips_lift(session: GameSession) -> None:
    """2026-07-08 用户补充1：建筑已经在飞 + 给了新 to_spot → 跳过 LIFT，直接进 landing。"""
    from sc2.position import Point2

    townhall = _Townhall(1006, "COMMANDCENTER", pos=Point2((10.0, 10.0)), is_flying=True)
    bot = _bot_with_townhall(townhall, can_place=True)
    director = _make_director(session, bot)

    directive = _make_directive(to_spot="natural")
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))

    casts = director.facade.casts
    assert not any(c[1] == "LIFT_COMMANDCENTER" for c in casts), casts
    land_casts = [c for c in casts if c[1] == "LAND_COMMANDCENTER"]
    assert land_casts, casts
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"


def test_tick_already_flying_no_target_done_immediately(session: GameSession) -> None:
    """2026-07-08 用户补充1：已经在飞 + 玩家又说"起飞"(无新目标) → 视为已达成，立即 done。"""
    from sc2.position import Point2

    townhall = _Townhall(1007, "COMMANDCENTER", pos=Point2((10.0, 10.0)), is_flying=True)
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive(to_spot=None)
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))

    assert not director.facade.casts
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "done"
    assert directive.id not in director._structure_move_orders


def test_find_structure_land_spot_snaps_to_optimal_expansion(session: GameSession) -> None:
    """2026-07-08 用户补充2：LAND 落点必须 snap 到最优 townhall 采矿位（不是原始点）。

    near 给一个偏离 natural expansion center 几格的点，_find_structure_land_spot
    应该返回 zone_manager 里 natural 的 center_location（(40,40)），不是 near 本身。
    """
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    townhall = _Townhall(1008, "COMMANDCENTER", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall, can_place=True)
    director = _make_director(session, bot)

    # 偏移点(43, 37) 离 natural expansion center (40,40) 更近，不是它本身
    near = Point2((43.0, 37.0))
    result = asyncio.run(director._find_structure_land_spot(UnitTypeId.COMMANDCENTER, near))

    assert result is not None
    assert (float(result.x), float(result.y)) == (40.0, 40.0), (
        f"应 snap 到 natural expansion center (40,40)，实际: ({result.x},{result.y})"
    )


def test_find_structure_land_spot_falls_back_when_anchor_occupied(session: GameSession) -> None:
    """snap 到的最优位被占（can_place_single 恒 False）→ 由近及远退化扫描（不是失败）。"""
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    townhall = _Townhall(1009, "COMMANDCENTER", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall, can_place=False)
    director = _make_director(session, bot)

    near = Point2((40.0, 40.0))
    result = asyncio.run(director._find_structure_land_spot(UnitTypeId.COMMANDCENTER, near))

    # can_place_single 永远 False（mock 固定行为）→ 扫描也找不到，预期 None
    # （这里验证的是：不会因为 anchor 被占就直接失败/崩，而是走了扫描分支不炸）
    assert result is None


def test_tick_structure_gone_fails(session: GameSession) -> None:
    """FIND 后建筑消失（被打掉）→ failed。"""
    from sc2.position import Point2

    townhall = _Townhall(1005, "COMMANDCENTER", pos=Point2((10.0, 10.0)))
    bot = _bot_with_townhall(townhall)
    director = _make_director(session, bot)

    directive = _make_directive()
    director._apply_to_facade(directive, now=0.0)
    asyncio.run(director._tick_structure_move(now=1.0))
    assert directive.id in director._structure_move_orders

    # 建筑消失：structures() 永远返回空（真机 Units 也有 find_by_tag，用同款空壳）
    class _EmptyUnits(list):
        def find_by_tag(self, tag):
            return None

    bot.structures = lambda type_id: _EmptyUnits()
    asyncio.run(director._tick_structure_move(now=2.0))

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "failed"
    assert directive.id not in director._structure_move_orders
