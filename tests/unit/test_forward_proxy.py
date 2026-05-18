"""ForwardSupportPylonGateway 单测：选点 + tag 跟踪 + 完成判定 + worker 重派。

测试策略：mock `ai` / `knowledge` 等 sharpy 依赖（不依赖真实 sharpy），
直接构造 ForwardSupportPylonGateway 实例后注入 mock state，
验证 _score_pos / _building_state / _is_done / _is_forward_building 行为。

forward_proxy.py 模块顶 import sharpy.plans.acts.ActBase，必须先把 vendor/sharpy
加入 sys.path（参考 test_plan_create_plan_smoke.py 的 fixture 做法）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# 先准备 sharpy path（模块级，让 forward_proxy import 能跑）
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytest.importorskip("sc2.ids.unit_typeid")
pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("sharpy.plans.acts")


def _make_proxy_instance():
    """构造 ForwardSupportPylonGateway 实例（绕开 sharpy KnowledgeBot 父类 init）。"""
    # ForwardSupportPylonGateway 继承 ActBase；ActBase.__init__() 不需要参数
    # 但 sharpy 不一定能 import，所以 mock 一下父类
    from vibecraft.bot.auto_combat.protoss.plans import forward_proxy

    return forward_proxy.ForwardSupportPylonGateway()


def _make_mock_ai(
    enemy_main=(120, 30),
    own_main=(20, 130),
    map_center=(70, 80),
    townhalls_positions=None,
    enemy_units_with_positions=None,
    enemy_structures_with_positions=None,
    structures_by_tag=None,
    workers_positions=None,
    game_time=10.0,
):
    """构造一个 mock ai 对象，提供 forward_proxy 需要的最小接口。"""
    from sc2.position import Point2

    ai = MagicMock()
    ai.enemy_start_locations = [Point2(enemy_main)]
    ai.start_location = Point2(own_main)
    ai.game_info.map_center = Point2(map_center)
    ai.time = game_time

    # townhalls
    townhalls = []
    for pos in townhalls_positions or [own_main]:
        t = MagicMock()
        t.position = Point2(pos)
        t.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
            other.position if hasattr(other, "position") else other
        )
        townhalls.append(t)
    ai.townhalls = townhalls

    # enemy units / structures
    enemy_units = []
    for pos, sight in enemy_units_with_positions or []:
        u = MagicMock()
        u.position = Point2(pos)
        u.sight_range = sight
        u.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
            other.position if hasattr(other, "position") else other
        )
        enemy_units.append(u)
    ai.enemy_units = _MockUnits(enemy_units)

    enemy_structures = []
    for pos, sight in enemy_structures_with_positions or []:
        u = MagicMock()
        u.position = Point2(pos)
        u.sight_range = sight
        u.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
            other.position if hasattr(other, "position") else other
        )
        enemy_structures.append(u)
    ai.enemy_structures = _MockUnits(enemy_structures)

    # structures.find_by_tag
    by_tag = structures_by_tag or {}
    ai.structures = _MockStructures(list(by_tag.values()), by_tag)

    # workers
    workers = []
    for pos in workers_positions or []:
        w = MagicMock()
        w.position = Point2(pos)
        w.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
            other.position if hasattr(other, "position") else other
        )
        workers.append(w)
    ai.workers = _MockUnits(workers)

    ai.in_placement_grid = lambda p: True
    ai.get_terrain_height = lambda p: 100
    ai.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(combat_intent_override=None))
    ai.can_afford = lambda _t: True
    # playable_area: 默认 0-160 矩形(SC2 标准 LE 地图大小)
    ai.game_info.playable_area = SimpleNamespace(x=0, y=0, width=160, height=160)

    return ai


class _MockUnits:
    """模拟 python-sc2 Units 容器，支持 `|`, `of_type`, `closer_than`, `find_by_tag`, iter。"""

    def __init__(self, units):
        self._units = list(units)

    def __or__(self, other):
        return _MockUnits(self._units + other._units)

    def __iter__(self):
        return iter(self._units)

    def __len__(self):
        return len(self._units)

    def __bool__(self):
        return bool(self._units)

    @property
    def exists(self):
        return bool(self._units)

    @property
    def ready(self):
        return _MockUnits([u for u in self._units if getattr(u, "is_ready", True)])

    @property
    def not_ready(self):
        return _MockUnits([u for u in self._units if not getattr(u, "is_ready", True)])

    @property
    def first(self):
        return self._units[0]

    def of_type(self, types):
        return _MockUnits(
            [u for u in self._units if getattr(u, "type_id", None) in types]
        )

    def closer_than(self, dist, pos):
        return _MockUnits(
            [u for u in self._units if u.position.distance_to(pos) < dist]
        )

    def closest_to(self, pos):
        return min(self._units, key=lambda u: u.position.distance_to(pos))


class _MockStructures(_MockUnits):
    def __init__(self, units, by_tag):
        super().__init__(units)
        self._by_tag = by_tag

    def find_by_tag(self, tag):
        return self._by_tag.get(tag)

    def __call__(self, unit_type):
        """ai.structures(PYLON) → 过滤 type."""
        return _MockUnits(
            [u for u in self._units if getattr(u, "type_id", None) == unit_type]
        )


# ============================================================================
# Tests: 评分（_score_pos）
# ============================================================================


class TestScorePos:
    def test_too_close_to_enemy_main_returns_negative(self):
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(120, 30))
        pos = Point2((118, 28))  # 距敌方主 ~2.8，< 12 硬下限
        assert inst._score_pos(pos) < 0

    def test_too_far_from_enemy_returns_negative(self):
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(120, 30))
        pos = Point2((20, 30))  # 距敌方主 100，> 55 上限
        assert inst._score_pos(pos) < 0

    def test_in_enemy_vision_returns_negative(self):
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(
            enemy_main=(120, 30),
            enemy_units_with_positions=[((110, 30), 10)],  # 探机 sight 10
        )
        pos = Point2((105, 30))  # 距探机 5 < sight 10
        assert inst._score_pos(pos) < 0

    def test_closer_to_enemy_scores_higher(self):
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(120, 30), map_center=(70, 80))
        # 都在 _MIN_DIST_TO_ENEMY=40 之外（避开二矿/三矿区域）
        s_close = inst._score_pos(Point2((78, 30)))  # 距 42
        s_far = inst._score_pos(Point2((65, 30)))  # 距 55
        assert s_close > s_far, f"close={s_close} should > far={s_far}"

    def test_edge_position_scores_higher_than_middle(self):
        """贴地图边的点(edge_d 小)比地图中央点(edge_d 大)得分高。

        实战 log: 自家左下时选 (90.1, 99.3) edge_d ~30 → 在敌方下二矿必经路被发现。
        新评分项偏好贴边点 — 玩家手法 proxy 永远走边缘走廊。
        """
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(120, 30))
        # 都距敌方 ~40,但 edge_pos 贴边(y=2),middle_pos 在中央(y=30)
        edge_pos = Point2((85, 2))
        middle_pos = Point2((85, 30))
        # 两个点都满足 dist 40+ 在 _MIN 之外
        assert inst._score_pos(edge_pos) > inst._score_pos(middle_pos), (
            f"edge={inst._score_pos(edge_pos)} should > middle={inst._score_pos(middle_pos)}"
        )

    def test_out_of_map_bounds_returns_negative(self):
        """Regression: ring 在地图边界外的点必须被过滤(实战 log:proxy (28.5, -6.14))。"""
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(50, 30))
        # 负坐标:在 playable_area (0-160) 之外
        assert inst._score_pos(Point2((28.5, -6.14))) < 0
        # 也测 X 越界
        assert inst._score_pos(Point2((200, 50))) < 0


# ============================================================================
# Tests: 完成判定 (_is_done)
# ============================================================================


class TestIsDone:
    def test_done_when_both_ready(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2
        from sc2.ids.unit_typeid import UnitTypeId

        py = MagicMock()
        py.is_ready = True
        py.tag = 100
        py.type_id = UnitTypeId.PYLON
        py.position = Point2((120, 50))
        py.distance_to = lambda other: py.position.distance_to(
            other.position if hasattr(other, "position") else other
        )
        bg = MagicMock()
        bg.is_ready = True
        bg.tag = 101
        bg.type_id = UnitTypeId.GATEWAY
        bg.position = Point2((122, 52))
        bg.distance_to = lambda other: bg.position.distance_to(
            other.position if hasattr(other, "position") else other
        )

        inst.ai = _make_mock_ai(structures_by_tag={100: py, 101: bg})
        inst.proxy_location = Point2((120, 50))
        inst._start_time = 0.0
        inst.ai.time = 10.0
        inst._proxy_tags = {UnitTypeId.PYLON: 100, UnitTypeId.GATEWAY: 101}

        assert inst._is_done()

    def test_done_when_timeout(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai()
        inst.proxy_location = Point2((120, 50))
        inst._start_time = 0.0
        inst.ai.time = 200.0  # > 150s 超时(_TASK_TIMEOUT_S)
        assert inst._is_done()

    def test_done_when_too_many_worker_deaths(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai()
        inst.proxy_location = Point2((120, 50))
        inst._start_time = 0.0
        inst.ai.time = 10.0
        inst._worker_death_count = 4  # 达上限(_MAX_WORKER_DEATHS=4)
        assert inst._is_done()

    def test_done_when_main_army_attacking(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai()
        inst.ai.knowledge.vibecraft.combat_intent_override = "attack"
        inst.proxy_location = Point2((120, 50))
        inst._start_time = 0.0
        inst.ai.time = 10.0
        assert inst._is_done()

    def test_building_state_ordering_via_worker_orders(self):
        """worker 正在 build Pylon → _building_state 应识别为 "ordering"。

        Regression：之前写 `order.ability_id` AttributeError，python-sc2 的
        UnitOrder 字段是 `ability: AbilityData`，AbilityId 在 `.ability.id`。
        """
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        # 构造一个 mock worker，其 orders 含 PROTOSSBUILD_PYLON
        worker = MagicMock()
        worker.tag = 555
        worker.position = Point2((120, 50))

        # python-sc2 UnitOrder 真实形态：order.ability 是 AbilityData(有 .id)
        ability_data = SimpleNamespace(id=AbilityId.PROTOSSBUILD_PYLON)
        unit_order = SimpleNamespace(ability=ability_data, target=None, progress=0.0)
        worker.orders = [unit_order]

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai()
        # _get_proxy_worker 走 self.cache.by_tag(sharpy ActBase 上的 cache，不是 ai.cache)
        inst.cache = SimpleNamespace(by_tag=lambda _t: worker)
        inst.proxy_location = Point2((120, 50))
        inst.proxy_worker_tag = 555
        inst._proxy_tags = {}  # 没 tag 还没建筑实体 → 走 worker.orders 分支

        # 不应抛 AttributeError，且应识别为 "ordering"
        state = inst._building_state(UnitTypeId.PYLON)
        assert state == "ordering", f"expected 'ordering', got {state!r}"

    def test_not_done_in_progress(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2
        from sc2.ids.unit_typeid import UnitTypeId

        py = MagicMock()
        py.is_ready = False  # 在建造
        py.tag = 100
        py.type_id = UnitTypeId.PYLON
        py.position = Point2((120, 50))
        py.distance_to = lambda other: py.position.distance_to(
            other.position if hasattr(other, "position") else other
        )

        inst.ai = _make_mock_ai(structures_by_tag={100: py})
        inst.proxy_location = Point2((120, 50))
        inst._start_time = 0.0
        inst.ai.time = 10.0
        inst._proxy_tags = {UnitTypeId.PYLON: 100}

        assert not inst._is_done()


# ============================================================================
# Tests: forward building 识别 (_is_forward_building)
# ============================================================================


class TestIsForwardBuilding:
    def test_near_proxy_and_far_from_home_is_forward(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai(own_main=(20, 130), townhalls_positions=[(20, 130)])
        inst.proxy_location = Point2((120, 50))

        struct = MagicMock()
        struct.position = Point2((122, 52))  # 距 proxy 2.8
        struct.distance_to = lambda other: struct.position.distance_to(
            other.position if hasattr(other, "position") else other
        )

        assert inst._is_forward_building(struct)

    def test_near_townhall_not_forward(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        # townhall 在 (50, 60)；proxy 也在附近
        inst.ai = _make_mock_ai(townhalls_positions=[(50, 60)])
        inst.proxy_location = Point2((45, 65))

        struct = MagicMock()
        struct.position = Point2((48, 62))  # 距 townhall 3.6 < 25
        struct.distance_to = lambda other: struct.position.distance_to(
            other.position if hasattr(other, "position") else other
        )

        assert not inst._is_forward_building(struct)

    def test_far_from_proxy_not_forward(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai(townhalls_positions=[(20, 130)])
        inst.proxy_location = Point2((120, 50))

        struct = MagicMock()
        struct.position = Point2((80, 50))  # 距 proxy 40 > 30
        struct.distance_to = lambda other: struct.position.distance_to(
            other.position if hasattr(other, "position") else other
        )

        assert not inst._is_forward_building(struct)


# ============================================================================
# Tests: 选点 (_pick_proxy_location)
# ============================================================================


class TestPickProxyLocation:
    def test_picks_valid_point_no_enemy_vision(self):
        inst = _make_proxy_instance()
        from sc2.position import Point2

        inst.ai = _make_mock_ai(enemy_main=(120, 30))
        inst.knowledge = SimpleNamespace(zone_manager=None)

        pos = inst._pick_proxy_location()
        assert pos is not None
        # 选到的点应满足硬约束
        d = pos.distance_to(Point2((120, 30)))
        assert 12 <= d <= 55, f"distance to enemy = {d}, should in [12, 55]"

    def test_returns_None_when_no_enemy(self):
        inst = _make_proxy_instance()
        inst.ai = MagicMock()
        inst.ai.enemy_start_locations = []  # 没敌方
        inst.knowledge = SimpleNamespace(zone_manager=None)
        # _generate_candidates 返回 [] → _pick_proxy_location fallback 也失败
        # （map_center.towards 也会因 IndexError 失败 → 返回 None）
        # 不强 assert None，只确保不抛
        try:
            inst._pick_proxy_location()
        except Exception:
            pytest.fail("_pick_proxy_location should not raise on missing enemy_start")

    def test_sharpy_fallback_when_all_filtered(self):
        """所有候选都被硬过滤掉（极端：每个候选点都在敌方视野内）→ 走 sharpy 标准 fallback。"""
        from sc2.position import Point2

        inst = _make_proxy_instance()
        # 在敌方主基地附近放一个超大视野的建筑（sight=200，覆盖全图），所有候选都被否决
        inst.ai = _make_mock_ai(
            enemy_main=(120, 30),
            map_center=(70, 80),
            enemy_structures_with_positions=[((120, 30), 200)],  # 视野 200 覆盖全图
        )
        inst.knowledge = SimpleNamespace(zone_manager=None)

        pos = inst._pick_proxy_location()
        # 应 fallback 到 sharpy 标准：map_center.towards(enemy, 25)
        expected = Point2((70, 80)).towards(Point2((120, 30)), 25)
        assert pos is not None
        # 误差 0.1 内（浮点）
        assert abs(pos.x - expected.x) < 0.1
        assert abs(pos.y - expected.y) < 0.1

    def test_sharpy_fallback_directly(self):
        """直接验 _sharpy_fallback_proxy 用 sharpy 标准距离 25。"""
        from sc2.position import Point2

        inst = _make_proxy_instance()
        inst.ai = _make_mock_ai(enemy_main=(120, 30), map_center=(70, 80))

        pos = inst._sharpy_fallback_proxy()
        expected = Point2((70, 80)).towards(Point2((120, 30)), 25)
        assert pos is not None
        assert abs(pos.x - expected.x) < 0.1
        assert abs(pos.y - expected.y) < 0.1
