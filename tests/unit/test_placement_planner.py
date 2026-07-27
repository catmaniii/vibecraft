"""Unit tests for placement_planner.plan_building_cluster (incremental adjacent placement).

All tests use mock objects -- no SC2 process is started.
asyncio_mode=auto (configured in pyproject.toml), no @pytest.mark.asyncio needed.

The planner uses **incremental adjacent placement**: building 1 seeds near the
anchor at the nearest placeable point; buildings 2..N hug the existing cluster,
sliding until placeable. Three hard checks remain: engine can_place per building,
connectivity re-flood (MUST-FIX 3), and per-spot query_pathing (MUST-FIX 1).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from vibecraft.bot.placement_planner import plan_building_cluster

# ---------------------------------------------------------------------------
# MockPixelMap
# ---------------------------------------------------------------------------


class MockPixelMap:
    """Minimal PixelMap stub backed by a 2-D list (data[y][x]).

    Matches the real PixelMap API used by placement_planner:
      __getitem__((x, y)) / __setitem__((x, y), v) / width / height / copy() /
      flood_fill(start: Point2, pred) -> set[Point2]  (8-connected).
    """

    def __init__(self, data: list[list[int]]):
        self._data = [row[:] for row in data]

    @property
    def height(self) -> int:
        return len(self._data)

    @property
    def width(self) -> int:
        return len(self._data[0]) if self._data else 0

    def __getitem__(self, pos) -> int:
        x, y = int(pos[0]), int(pos[1])
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise AssertionError(f"Out of bounds: ({x}, {y})")
        return self._data[y][x]

    def __setitem__(self, pos, value: int) -> None:
        x, y = int(pos[0]), int(pos[1])
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise AssertionError(f"Out of bounds: ({x}, {y})")
        self._data[y][x] = int(value)

    def copy(self) -> MockPixelMap:
        return MockPixelMap(self._data)

    def flood_fill(self, start_point: Point2, pred: Callable[[int], bool]) -> set[Point2]:
        nodes: set[Point2] = set()
        queue: list[Point2] = [start_point]
        while queue:
            p = queue.pop()
            x, y = int(p[0]), int(p[1])
            if not (0 <= x < self.width and 0 <= y < self.height):
                continue
            pt = Point2((x, y))
            if pt in nodes:
                continue
            if pred(self[x, y]):
                nodes.add(pt)
                queue += [
                    Point2((x + a, y + b))
                    for a in (-1, 0, 1)
                    for b in (-1, 0, 1)
                    if not (a == 0 and b == 0)
                ]
        return nodes


# ---------------------------------------------------------------------------
# MockAI: engine can_place + query_pathing driven by grids/predicates
# ---------------------------------------------------------------------------


def _footprint_buildable(placement: list[list[int]], c: Point2, half: int = 1) -> bool:
    """Engine-style can_place: the whole footprint must be in-bounds + placement==1."""
    cx = math.floor(c.x)
    cy = math.floor(c.y)
    h = len(placement)
    w = len(placement[0]) if placement else 0
    for dx in range(-half, half + 1):
        for dy in range(-half, half + 1):
            x, y = cx + dx, cy + dy
            if not (0 <= x < w and 0 <= y < h):
                return False
            if placement[y][x] == 0:
                return False
    return True


class _Client:
    def __init__(self, reachable: Callable[[Point2, Point2], bool]):
        self._reachable = reachable

    async def query_pathing(self, start, end):
        return 10.0 if self._reachable(start, end) else None


class MockAI:
    def __init__(
        self,
        pathing: list[list[int]],
        placement: list[list[int]],
        reachable: Callable[[Point2, Point2], bool] | None = None,
    ):
        self.game_info = type("_GI", (), {})()
        self.game_info.pathing_grid = MockPixelMap(pathing)
        self.game_info.placement_grid = MockPixelMap(placement)
        self._placement = placement
        self.can_place_calls = 0
        self.query_pathing_calls = 0
        if reachable is None:
            reachable = lambda s, e: True  # noqa: E731
        client = _Client(reachable)

        # wrap query_pathing to count calls
        _orig = client.query_pathing

        async def _counted(start, end):
            self.query_pathing_calls += 1
            return await _orig(start, end)

        client.query_pathing = _counted  # type: ignore[assignment]
        self._client = client

    async def can_place(self, building, positions):
        self.can_place_calls += 1
        return [_footprint_buildable(self._placement, p) for p in positions]


def _all_ones(w: int, h: int) -> list[list[int]]:
    return [[1] * w for _ in range(h)]


def make_open_ground_ai(width: int, height: int) -> MockAI:
    return MockAI(pathing=_all_ones(width, height), placement=_all_ones(width, height))


def _pairwise_min(spots: list[Point2]) -> float:
    m = 1e9
    for i in range(len(spots)):
        for j in range(i + 1, len(spots)):
            m = min(m, spots[i].distance_to(spots[j]))
    return m


# ---------------------------------------------------------------------------
# Test 1: open ground -> 3 compact, non-overlapping spots hugging the cluster
# ---------------------------------------------------------------------------


async def test_open_ground_returns_three_spots():
    ai = make_open_ground_ai(30, 30)
    anchor = Point2((15.0, 15.0))
    result = await plan_building_cluster(
        ai, anchor, UnitTypeId.BARRACKS, 3, scv_origin=Point2((5.0, 5.0))
    )
    assert result is not None
    assert len(result) == 3
    # No overlap: pairwise >= footprint+1 = 4 (allow float slack)
    assert _pairwise_min(result) >= 3.9
    # Compact: every building is within ~9 of the cluster centroid (hugging)
    cx = sum(p.x for p in result) / 3
    cy = sum(p.y for p in result) / 3
    centroid = Point2((cx, cy))
    for p in result:
        assert p.distance_to(centroid) <= 9.0


# ---------------------------------------------------------------------------
# Test 2: can_place all False -> None (no placeable seed)
# ---------------------------------------------------------------------------


async def test_can_place_all_false_returns_none():
    # Nothing buildable anywhere.
    ai = MockAI(pathing=_all_ones(30, 30), placement=[[0] * 30 for _ in range(30)])
    result = await plan_building_cluster(
        ai, Point2((15.0, 15.0)), UnitTypeId.BARRACKS, 3, scv_origin=Point2((5.0, 5.0))
    )
    assert result is None


# ---------------------------------------------------------------------------
# Test 3: query_pathing None (SCV can't path) -> None
# ---------------------------------------------------------------------------


async def test_query_pathing_unreachable_returns_none():
    ai = MockAI(
        pathing=_all_ones(30, 30),
        placement=_all_ones(30, 30),
        reachable=lambda s, e: False,  # every spot unreachable
    )
    result = await plan_building_cluster(
        ai, Point2((15.0, 15.0)), UnitTypeId.BARRACKS, 3, scv_origin=Point2((5.0, 5.0))
    )
    assert result is None
    assert ai.query_pathing_calls > 0  # the final engine path-check did run


# ---------------------------------------------------------------------------
# Test 4: power source constraint -> None when no placeable spot within radius
# ---------------------------------------------------------------------------


async def test_power_source_constraint_filters_all():
    ai = make_open_ground_ai(30, 30)
    anchor = Point2((15.0, 15.0))
    result = await plan_building_cluster(
        ai,
        anchor,
        UnitTypeId.BARRACKS,
        3,
        scv_origin=Point2((5.0, 5.0)),
        power_source=Point2((0.0, 0.0)),  # far from anchor
        power_radius=2.0,
    )
    assert result is None


async def test_power_source_within_radius_places_all_inside():
    """Protoss: with a nearby pylon, all 3 spots must sit within power_radius."""
    ai = make_open_ground_ai(40, 40)
    anchor = Point2((20.0, 20.0))
    power = Point2((20.0, 20.0))
    result = await plan_building_cluster(
        ai,
        anchor,
        UnitTypeId.BARRACKS,
        3,
        scv_origin=Point2((5.0, 5.0)),
        power_source=power,
        power_radius=6.5,
    )
    assert result is not None
    for p in result:
        assert p.distance_to(power) <= 6.5 + 1e-6


# ---------------------------------------------------------------------------
# Test 5: MUST-FIX 3 - mutual blockage in a narrow 3-high corridor -> None
#
# Buildable/pathable only in a 3-high corridor (y=0..2), 13 wide.
# Any 3 barracks fill the corridor width; after blocking all footprints the
# corridor is severed -> middle/far buildings' adjacent cells unreachable from
# the SCV origin at (0,1) -> connectivity fails for every seed -> None.
# ---------------------------------------------------------------------------


async def test_mutual_blockage_corridor_returns_none():
    W = 13
    # 3-high buildable+pathable corridor embedded in a taller blocked grid
    H = 3
    pathing = _all_ones(W, H)
    placement = _all_ones(W, H)
    ai = MockAI(pathing=pathing, placement=placement)
    result = await plan_building_cluster(
        ai,
        Point2((2.0, 1.0)),
        UnitTypeId.BARRACKS,
        3,
        scv_origin=Point2((0.0, 1.0)),
    )
    assert result is None, (
        "3 barracks fill the 3-high corridor; a middle building has no reachable "
        "adjacent cell once all footprints are blocked -> connectivity must reject"
    )


# ---------------------------------------------------------------------------
# Test 6: tight vertical pocket - rigid line wouldn't fit, sliding does
#
# Buildable only in a narrow vertical strip (x centers 10..12), tall (y 2..37).
# A horizontal line (span 8) can't fit the 3-wide-center strip, but incremental
# placement slides DOWN to stack vertically -> 3 spots, all inside the strip.
# Whole grid is pathable so entrances (left/right of the strip) stay reachable.
# ---------------------------------------------------------------------------


async def test_tight_vertical_pocket_slides_to_fit():
    W, H = 40, 40
    pathing = _all_ones(W, H)  # walkable everywhere (open sides = entrances)
    # Buildable only where 9 <= x <= 13 (footprint keeps centers to 10..12)
    placement = [[1 if 9 <= x <= 13 else 0 for x in range(W)] for _ in range(H)]
    ai = MockAI(pathing=pathing, placement=placement)
    result = await plan_building_cluster(
        ai,
        Point2((11.0, 20.0)),
        UnitTypeId.BARRACKS,
        3,
        scv_origin=Point2((2.0, 20.0)),
    )
    assert result is not None, "sliding should fit 3 barracks in the narrow vertical pocket"
    assert len(result) == 3
    # Every spot must be a buildable footprint inside the strip
    for p in result:
        assert _footprint_buildable(placement, p), f"{p} not in buildable strip"
    assert _pairwise_min(result) >= 3.9


# ---------------------------------------------------------------------------
# Test 7: no scv_origin -> connectivity + query_pathing skipped
# ---------------------------------------------------------------------------


async def test_no_scv_origin_skips_path_checks():
    ai = MockAI(
        pathing=_all_ones(30, 30),
        placement=_all_ones(30, 30),
        reachable=lambda s, e: False,  # would fail if consulted
    )
    result = await plan_building_cluster(
        ai, Point2((15.0, 15.0)), UnitTypeId.BARRACKS, 3, scv_origin=None
    )
    assert result is not None
    assert len(result) == 3
    assert ai.query_pathing_calls == 0  # never consulted without scv_origin


# ---------------------------------------------------------------------------
# Test 8: cannot extend cluster (only 2 buildable spots exist) -> None
# ---------------------------------------------------------------------------


async def test_cannot_extend_cluster_returns_none():
    """Only a tiny buildable island fits at most 1 barracks -> can't reach count=3."""
    W, H = 30, 30
    pathing = _all_ones(W, H)
    # Buildable only in a 4x4 island around (15,15): fits 1 footprint, not 3 spaced.
    placement = [[0] * W for _ in range(H)]
    for y in range(14, 17):
        for x in range(14, 17):
            placement[y][x] = 1
    ai = MockAI(pathing=pathing, placement=placement)
    result = await plan_building_cluster(
        ai, Point2((15.0, 15.0)), UnitTypeId.BARRACKS, 3, scv_origin=Point2((2.0, 2.0))
    )
    assert result is None
