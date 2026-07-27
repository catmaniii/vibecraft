# Drop Act Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现空投复合指令 — 玩家说"4 叉子棱镜空投对面二矿"→ bot 端解析语义、规划路径、自动补依赖、实例化 ActBase 子类微操执行。

**Architecture:** 新增 `DirectiveType.DROP_ACT` + 2 个 ActBase 子类（`GenericDropAct` simple style / `PrismWarpDropAct` 二段空投）+ 新模块 `drop_path` 递归路径算法 + 扩展 `NamedSpotRegistry` 解析"矿区/产能区/钟点"。神族空投默认 `warp_then_drop`（充分利用 warpgate power field）。

**Tech Stack:** Python (sharpy ActBase + python-sc2 Unit API), sc2.constants 三族 TECH_REQUIREMENT, Pydantic v2 directive schema, Vue 3 PWA 卡片渲染。

**Design source:** `docs/plans/2026-05-23-drop-act-design.md`

---

## 实施顺序

| Task | 内容 | 验证 |
|---|---|---|
| 1 | NamedSpotRegistry 扩展 (resolve_drop_target + clock_X + optimize_drop_pos) | pytest unit |
| 2 | drop_path 递归路径算法 (新模块) | pytest unit |
| 3 | DropActPayload + DirectiveType.DROP_ACT schema | pytest unit |
| 4 | LLM prompt + parser 加 drop_act + 回归 strategy_set/production_override | pytest unit + 1 局 LLM mock acceptance |
| 5 | GenericDropAct (style=simple) ActBase 子类 | pytest unit |
| 6 | PrismWarpDropAct (style=warp_then_drop) ActBase 子类 | pytest unit |
| 7 | Director._exec_drop_act + auto-chain (tech_tree + production_override) | pytest unit |
| 8a | dt_drop_iac.py 切换 PrismWarpDropAct + smoke | pytest unit |
| 8b | 删 DTPrismHarass + cleanup + build_acceptance 回归 | build_acceptance (1 VeryEasy + 3 VeryHard) |
| 9 | PWA DropActCard.vue + CommandCardStack drop_act 路由 | vitest + typecheck |

---

## Task 1: NamedSpotRegistry 扩展

**Files:**
- Modify: `src/vibecraft/bot/named_spot.py` (加 `resolve_drop_target` / `_clock_at_expansion` / `_optimize_drop_pos_to_edge`)
- Create: `tests/unit/test_named_spot_drop_target.py`

**Background:** sharpy `Zone` 已经有 `behind_mineral_position_center` 和 `center_location`,直接复用。`expansion_locations_list` 是 python-sc2 内置静态数据,所有 expansion 在 map load 时已知,无 vision 依赖。

### Step 1: 写 failing test

```python
# tests/unit/test_named_spot_drop_target.py
"""DropTarget 解析 + 钟点位置 + 矿区 drop_pos 圆周贴边优化。"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from vibecraft.bot.named_spot import NamedSpotRegistry, DropTarget


@pytest.fixture
def reg() -> NamedSpotRegistry:
    return NamedSpotRegistry()


def _mock_zone(center: tuple[float, float], behind_mineral: tuple[float, float]):
    """sharpy Zone 的极简 mock。"""
    from sc2.position import Point2

    z = MagicMock()
    z.center_location = Point2(center)
    z.behind_mineral_position_center = Point2(behind_mineral)
    return z


def _mock_bot_with_zones(zones: list, playable_size: tuple[float, float] = (160, 160)):
    """bot 含 knowledge.zone_manager.enemy_expansion_zones + expansion_locations_list。"""
    from sc2.position import Point2

    bot = MagicMock()
    bot.knowledge.zone_manager.enemy_expansion_zones = zones
    bot.game_info.map_center = Point2((playable_size[0] / 2, playable_size[1] / 2))
    # playable_area: x/y/width/height
    bot.game_info.playable_area = MagicMock(x=0, y=0, width=playable_size[0], height=playable_size[1])
    # expansion_locations_list 给 clock_X 用
    bot.expansion_locations_list = [
        z.center_location for z in zones
    ]
    return bot


class TestResolveDropTarget:
    def test_enemy_main_mineral(self, reg: NamedSpotRegistry) -> None:
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),  # enemy_main
        ]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:mineral", bot)
        assert result is not None
        assert result.zone_kind == "mineral"
        assert result.base_index == 0
        assert result.source_spec == "enemy_main:mineral"
        # mineral drop_pos 经过 optimize_drop_pos_to_edge 处理
        # M=(45,31), playable 160x160, 最近边 = bottom (y=31 距 0 是 31, 最近)
        # 实际 dl=45, dr=115, dt=129, db=31 → bottom 最近
        # drop_pos = (45, 31-15) = (45, 16)
        assert result.position.x == pytest.approx(45)
        assert result.position.y == pytest.approx(16)

    def test_enemy_main_production(self, reg: NamedSpotRegistry) -> None:
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:production", bot)
        assert result is not None
        assert result.zone_kind == "production"
        # production = center_location 直接(无 optimize)
        assert result.position.x == pytest.approx(48)
        assert result.position.y == pytest.approx(28)

    def test_enemy_natural_mineral_default(self, reg: NamedSpotRegistry) -> None:
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),
            _mock_zone((30, 49), behind_mineral=(33, 52)),
        ]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_natural:mineral", bot)
        assert result is not None
        assert result.base_index == 1

    def test_clock_11_mineral(self, reg: NamedSpotRegistry) -> None:
        """clock_X 找钟点方向最近 expansion。"""
        from sc2.position import Point2

        # map_center=(80,80). 11 点钟方向(约 60°): 应取左上扩张点。
        # zones[0] enemy_main (48, 28) — 4-5 点方向(下偏左)
        # zones[1] (30, 49)        — 9 点方向(左)
        # zones[2] (50, 130)       — 11 点方向(左上),target_angle ≈ 60° (12点=90°)
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),
            _mock_zone((30, 49), behind_mineral=(33, 52)),
            _mock_zone((50, 130), behind_mineral=(53, 127)),
        ]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("clock_11:mineral", bot)
        assert result is not None
        # 应选 (50, 130) 那个
        # 注意:position 经 optimize_drop_pos_to_edge,但 base_index 要对得上
        # 我们用 source_spec 来检查
        assert result.source_spec == "clock_11:mineral"

    def test_clock_X_production_invalid(self, reg: NamedSpotRegistry) -> None:
        """clock_X 没有 production 概念。"""
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("clock_11:production", bot)
        assert result is None  # 不允许

    def test_unknown_spec(self, reg: NamedSpotRegistry) -> None:
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        assert reg.resolve_drop_target("garbage:mineral", bot) is None
        assert reg.resolve_drop_target("enemy_main:garbage", bot) is None


class TestOptimizeDropPosToEdge:
    """矿区 drop_pos 圆周贴最近地图边."""

    def test_close_to_left(self, reg: NamedSpotRegistry) -> None:
        from sc2.position import Point2
        from vibecraft.bot.named_spot import _optimize_drop_pos_to_edge

        playable = MagicMock(x=0, y=0, width=160, height=160)
        # M=(30, 80), 最近边 left(距 30)
        pos = _optimize_drop_pos_to_edge(Point2((30, 80)), R=15, playable=playable)
        assert pos.x == pytest.approx(15)  # 30-15
        assert pos.y == pytest.approx(80)

    def test_close_to_bottom(self, reg: NamedSpotRegistry) -> None:
        from sc2.position import Point2
        from vibecraft.bot.named_spot import _optimize_drop_pos_to_edge

        playable = MagicMock(x=0, y=0, width=160, height=160)
        # M=(80, 30), 最近边 bottom(距 30)
        pos = _optimize_drop_pos_to_edge(Point2((80, 30)), R=15, playable=playable)
        assert pos.x == pytest.approx(80)
        assert pos.y == pytest.approx(15)  # 30-15


class TestClockAtExpansion:
    def test_clock_0_is_right(self, reg: NamedSpotRegistry) -> None:
        """clock 3 = 正右方 (atan2 angle 0)."""
        from sc2.position import Point2
        from vibecraft.bot.named_spot import _clock_at_expansion

        bot = MagicMock()
        bot.game_info.map_center = Point2((80, 80))
        bot.expansion_locations_list = [
            Point2((150, 80)),  # 3 点钟方向(正右)
            Point2((80, 150)),  # 12 点钟方向(正上,SC2 +y 向上)
        ]
        assert _clock_at_expansion(3, bot) == Point2((150, 80))
        assert _clock_at_expansion(12, bot) == Point2((80, 150))
        # 12 和 0 应该等价
        assert _clock_at_expansion(0, bot) == _clock_at_expansion(12, bot)
```

### Step 2: 跑测试确认 fail

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_named_spot_drop_target.py -v
```
Expected: FAIL (`AttributeError: ... 'DropTarget'` 或 `resolve_drop_target` not found)

### Step 3: 实现

修改 `src/vibecraft/bot/named_spot.py` 加新内容（保留现有 `resolve` / `_enemy_*`）:

```python
# 加在文件顶部 import 后

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DropTarget:
    """空投目标 — NamedSpotRegistry.resolve_drop_target 返回。"""
    position: Any        # Point2; 矿区已 optimize_drop_pos_to_edge
    zone_kind: str       # "mineral" | "production"
    base_index: int      # 0/1/2/3+ (enemy_main=0)
    source_spec: str     # 原 spec(给日志/PWA 显示)


# ============================================================
# Module-level helpers (testable in isolation)
# ============================================================

def _optimize_drop_pos_to_edge(M: Any, R: float, playable: Any) -> Any:
    """矿区 drop_pos = M 到最近地图边缘方向,距 M = R 的圆周点。

    棱镜在地图边缘 = 远离敌方主力,DT 卸下走 R 格到矿区。
    """
    from sc2.position import Point2

    dl = M.x - playable.x
    dr = playable.x + playable.width - M.x
    dt_ = playable.y + playable.height - M.y
    db = M.y - playable.y
    min_dist = min(dl, dr, dt_, db)
    if min_dist == dl:
        return Point2((M.x - R, M.y))
    if min_dist == dr:
        return Point2((M.x + R, M.y))
    if min_dist == dt_:
        return Point2((M.x, M.y + R))
    return Point2((M.x, M.y - R))


def _clock_at_expansion(clock: int, bot: Any) -> Any | None:
    """钟点方向最近的 expansion (12点=正上,3点=正右,6点=正下,9点=正左)。"""
    center = bot.game_info.map_center
    # 12 点 = π/2(正上); 3 点 = 0(正右); clock 减小角度增加
    target_angle = (math.pi / 2) - ((clock % 12) * math.pi / 6)
    best = None
    best_d = float("inf")
    for p in bot.expansion_locations_list:
        angle = math.atan2(p.y - center.y, p.x - center.x)
        diff = abs((angle - target_angle + math.pi) % (2 * math.pi) - math.pi)
        if diff < best_d:
            best_d = diff
            best = p
    return best


# ============================================================
# NamedSpotRegistry 扩展 method
# ============================================================

# 在 class NamedSpotRegistry 内加:

DROP_ZONE_KINDS = frozenset({"mineral", "production"})
_DROP_R = 15.0  # 矿区圆周半径(棱镜在圆周上,DT 走 R 格到矿心)


def resolve_drop_target(self, spec: str, bot: Any) -> DropTarget | None:
    """解析 drop spec → DropTarget。

    spec 格式: <base_ref>:<zone_kind>
      base_ref: enemy_main | enemy_natural | enemy_third | clock_{0..11} | map_center
      zone_kind: mineral | production
        production 仅 enemy_main/natural/third 有效;
        clock_X / map_center 只 mineral
    """
    if ":" not in spec:
        return None
    base_ref, _, zone_kind = spec.partition(":")
    if zone_kind not in DROP_ZONE_KINDS:
        return None

    # 找 zone + base_index
    zone = None
    base_index = -1
    try:
        zones = bot.knowledge.zone_manager.enemy_expansion_zones
    except AttributeError:
        zones = []

    if base_ref == "enemy_main" and len(zones) > 0:
        zone, base_index = zones[0], 0
    elif base_ref == "enemy_natural" and len(zones) > 1:
        zone, base_index = zones[1], 1
    elif base_ref == "enemy_third" and len(zones) > 2:
        zone, base_index = zones[2], 2
    elif base_ref.startswith("clock_"):
        # clock_X / map_center 只 mineral
        if zone_kind != "mineral":
            return None
        try:
            clock = int(base_ref.removeprefix("clock_"))
        except ValueError:
            return None
        # 直接用 expansion 位置(无 zone object)
        exp_pos = _clock_at_expansion(clock, bot)
        if exp_pos is None:
            return None
        playable = bot.game_info.playable_area
        drop_pos = _optimize_drop_pos_to_edge(exp_pos, _DROP_R, playable)
        return DropTarget(
            position=drop_pos,
            zone_kind="mineral",
            base_index=-1,  # clock-based,无 zone index
            source_spec=spec,
        )
    elif base_ref == "map_center":
        if zone_kind != "mineral":
            return None
        # 距 map_center 最近的 expansion
        center = bot.game_info.map_center
        best = None
        best_d = float("inf")
        for p in bot.expansion_locations_list:
            d = ((p.x - center.x) ** 2 + (p.y - center.y) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best = p
        if best is None:
            return None
        playable = bot.game_info.playable_area
        drop_pos = _optimize_drop_pos_to_edge(best, _DROP_R, playable)
        return DropTarget(
            position=drop_pos, zone_kind="mineral", base_index=-1, source_spec=spec
        )
    else:
        return None

    # enemy_main/natural/third 分支:zone 不为 None
    if zone is None:
        return None
    if zone_kind == "mineral":
        M = zone.behind_mineral_position_center
        playable = bot.game_info.playable_area
        drop_pos = _optimize_drop_pos_to_edge(M, _DROP_R, playable)
        return DropTarget(
            position=drop_pos, zone_kind="mineral",
            base_index=base_index, source_spec=spec
        )
    # production
    return DropTarget(
        position=zone.center_location, zone_kind="production",
        base_index=base_index, source_spec=spec
    )
```

### Step 4: 跑测试确认 pass

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_named_spot_drop_target.py -v
```
Expected: 7 passed

### Step 5: Commit

```bash
git add src/vibecraft/bot/named_spot.py tests/unit/test_named_spot_drop_target.py
git commit -m "$(cat <<'EOF'
feat(named_spot): DropTarget + clock_X + 矿区贴边 drop_pos 优化

NamedSpotRegistry.resolve_drop_target(spec, bot) → DropTarget:
- enemy_main/natural/third:mineral|production (复用 sharpy zone)
- clock_{0..11}:mineral (钟点方向最近 expansion)
- map_center:mineral (距图心最近 expansion)
- mineral drop_pos 经 _optimize_drop_pos_to_edge:在矿区圆周上贴最近地图边
  → 棱镜远离敌方主力,DT 走 R=15 格到矿

设计参考 docs/plans/2026-05-23-drop-act-design.md §1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: drop_path 递归路径算法

**Files:**
- Create: `src/vibecraft/bot/drop_path.py`
- Create: `tests/unit/test_drop_path.py`

### Step 1: 写 failing test

```python
# tests/unit/test_drop_path.py
"""路径递归细分算法:A→B 穿过敌方基地时插入转折点 C。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibecraft.bot.drop_path import (
    plan_drop_path,
    project_point_onto_segment,
    first_blocking_zone,
)


def _mock_zone_at(center: tuple[float, float], has_townhall: bool):
    """sharpy Zone mock,可选是否已确定有敌方基地。"""
    from sc2.position import Point2
    from sc2.ids.unit_typeid import UnitTypeId

    z = MagicMock()
    z.center_location = Point2(center)
    z.behind_mineral_position_center = Point2(center)  # 简化
    if has_townhall:
        # known_enemy_units.of_type(...).exists = True
        units = MagicMock()
        units.exists = True
        z.known_enemy_units.of_type = MagicMock(return_value=units)
    else:
        units = MagicMock()
        units.exists = False
        z.known_enemy_units.of_type = MagicMock(return_value=units)
    return z


def _mock_bot(zones: list):
    bot = MagicMock()
    bot.knowledge.zone_manager.enemy_expansion_zones = zones
    return bot


class TestProjectPointOntoSegment:
    def test_midpoint(self) -> None:
        from sc2.position import Point2

        A = Point2((0, 0))
        B = Point2((10, 0))
        M = Point2((5, 5))  # 在 AB 中点正上方
        P = project_point_onto_segment(M, A, B)
        assert P.x == pytest.approx(5)
        assert P.y == pytest.approx(0)

    def test_clamped_to_endpoint(self) -> None:
        """M 投影超出 AB 段 → clamp 到端点。"""
        from sc2.position import Point2

        A = Point2((0, 0))
        B = Point2((10, 0))
        M = Point2((20, 5))  # 在 B 右侧
        P = project_point_onto_segment(M, A, B)
        assert P.x == pytest.approx(10)  # = B.x
        assert P.y == pytest.approx(0)


class TestFirstBlockingZone:
    def test_no_blocker_when_no_zones_have_townhall(self) -> None:
        """zone 没 own townhall → 不算 blocker。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((50, 50), has_townhall=False)]
        bot = _mock_bot(zones)
        result = first_blocking_zone(
            Point2((0, 0)), Point2((100, 100)), bot, R=15
        )
        assert result is None

    def test_blocker_detected(self) -> None:
        """有 townhall 的 zone 在 AB 段距 < R → blocker。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((50, 50), has_townhall=True)]
        bot = _mock_bot(zones)
        # AB 段 (0,0)→(100,100) 经过 (50,50),距离 0
        result = first_blocking_zone(
            Point2((0, 0)), Point2((100, 100)), bot, R=15
        )
        assert result is not None

    def test_far_zone_not_blocker(self) -> None:
        """zone 距 AB 段 > R → 不算 blocker。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((90, 10), has_townhall=True)]
        bot = _mock_bot(zones)
        # AB 段 (0,0)→(0,100) 经过 x=0,zone x=90 距 90 > R=15
        result = first_blocking_zone(
            Point2((0, 0)), Point2((0, 100)), bot, R=15
        )
        assert result is None


class TestPlanDropPath:
    def test_no_blocker_returns_AB(self) -> None:
        """无 blocker → 返回 [A, B]。"""
        from sc2.position import Point2

        bot = _mock_bot([])
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        assert len(path) == 2
        assert path[0] == Point2((0, 0))
        assert path[1] == Point2((100, 100))

    def test_one_blocker_inserts_one_point(self) -> None:
        """1 个 blocker → 插入 1 个 C,返回 3 点。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((50, 50), has_townhall=True)]
        bot = _mock_bot(zones)
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        assert len(path) == 3
        # C 应远离 (50, 50)
        C = path[1]
        dist_to_blocker = ((C.x - 50) ** 2 + (C.y - 50) ** 2) ** 0.5
        assert dist_to_blocker >= 15  # R_MINERAL_AVOID

    def test_max_depth_fallback(self) -> None:
        """超 max_depth(=3) fallback 原 AB 直线。"""
        from sc2.position import Point2

        # 故意 blocker 集中 → 递归很深 → max_depth tripped
        zones = [
            _mock_zone_at((25, 25), has_townhall=True),
            _mock_zone_at((50, 50), has_townhall=True),
            _mock_zone_at((75, 75), has_townhall=True),
            _mock_zone_at((40, 60), has_townhall=True),
        ]
        bot = _mock_bot(zones)
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        # 不爆栈;不超 2^max_depth + 1 = 9 个点(实际可能更少)
        assert 2 <= len(path) <= 9
```

### Step 2: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_drop_path.py -v
```
Expected: FAIL (`ModuleNotFoundError: vibecraft.bot.drop_path`)

### Step 3: 实现

```python
# src/vibecraft/bot/drop_path.py
"""空投路径递归细分算法。

设计 §2:plan_drop_path(A, B, bot) 返回 waypoint list (含 A 和 B)。
- A→B 直线穿过"已确定敌方基地"(zone 有 known townhall) → 插入转折点 C
- C = M.position + (P-M).normalized() * (R+push), P 是 M 在 AB 上垂足
- 递归 max_depth=3 兜底,防 loop

参考 docs/plans/2026-05-23-drop-act-design.md §2。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

R_MINERAL_AVOID: float = 15.0  # zone 影响半径
PUSH_DIST: float = 5.0          # C 额外 buffer
MAX_DEPTH: int = 3              # 最多插入 3 个新点

# sharpy zone "has known townhall" 检测的 unit type
_ENEMY_TOWNHALL_NAMES: frozenset[str] = frozenset(
    {
        "NEXUS", "HATCHERY", "LAIR", "HIVE",
        "COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS",
    }
)


def _get_townhall_type_ids() -> set:
    """延迟 import sc2.UnitTypeId(测试 mock 友好)。"""
    try:
        from sc2.ids.unit_typeid import UnitTypeId
        return {getattr(UnitTypeId, n) for n in _ENEMY_TOWNHALL_NAMES if hasattr(UnitTypeId, n)}
    except ImportError:
        return set()


def _zone_has_known_townhall(zone: Any) -> bool:
    """zone.known_enemy_units 含 own townhall(玩家已侦察到对方基地)?"""
    try:
        return bool(zone.known_enemy_units.of_type(_get_townhall_type_ids()).exists)
    except Exception:
        return False


def project_point_onto_segment(M: Any, A: Any, B: Any) -> Any:
    """M 在线段 AB 上的垂足(超出段时 clamp 到端点)。"""
    from sc2.position import Point2

    ax, ay = A.x, A.y
    bx, by = B.x, B.y
    mx, my = M.x, M.y
    abx = bx - ax
    aby = by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq < 1e-9:
        return Point2((ax, ay))  # A==B
    amx = mx - ax
    amy = my - ay
    t = (amx * abx + amy * aby) / ab_len_sq
    t = max(0.0, min(1.0, t))  # clamp [0,1]
    return Point2((ax + t * abx, ay + t * aby))


def first_blocking_zone(
    A: Any, B: Any, bot: Any, R: float = R_MINERAL_AVOID
) -> Any | None:
    """找第一个被 AB 段穿过(距 < R)的"已确定敌方基地"zone。"""
    try:
        zones = bot.knowledge.zone_manager.enemy_expansion_zones
    except AttributeError:
        return None
    for z in zones:
        if not _zone_has_known_townhall(z):
            continue
        try:
            M = z.center_location
        except AttributeError:
            continue
        P = project_point_onto_segment(M, A, B)
        d = ((P.x - M.x) ** 2 + (P.y - M.y) ** 2) ** 0.5
        if d < R:
            return z
    return None


def plan_drop_path(
    A: Any, B: Any, bot: Any, depth: int = 0, max_depth: int = MAX_DEPTH
) -> list:
    """A→B 路径细分。返回 waypoint list (含 A 和 B)。

    递归:AB 穿过 blocker → 插入 C → 拆 A→C, C→B。
    depth 兜底防 loop。
    """
    from sc2.position import Point2

    if depth >= max_depth:
        return [A, B]
    M_zone = first_blocking_zone(A, B, bot)
    if M_zone is None:
        return [A, B]
    M = M_zone.center_location
    P = project_point_onto_segment(M, A, B)
    # C 方向:P-M 归一化(远离 M)。若 P==M(AB 穿过 M)用垂直 AB 方向。
    dx = P.x - M.x
    dy = P.y - M.y
    norm = (dx * dx + dy * dy) ** 0.5
    if norm < 1e-6:
        # 用 AB 垂直方向
        abx = B.x - A.x
        aby = B.y - A.y
        ab_n = (abx * abx + aby * aby) ** 0.5
        if ab_n < 1e-6:
            return [A, B]
        dx, dy = -aby / ab_n, abx / ab_n
    else:
        dx, dy = dx / norm, dy / norm
    push = R_MINERAL_AVOID + PUSH_DIST
    C = Point2((M.x + dx * push, M.y + dy * push))
    left = plan_drop_path(A, C, bot, depth + 1, max_depth)
    right = plan_drop_path(C, B, bot, depth + 1, max_depth)
    return left[:-1] + right  # 去重 C
```

### Step 4: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_drop_path.py -v
```
Expected: 7 passed

### Step 5: Commit

```bash
git add src/vibecraft/bot/drop_path.py tests/unit/test_drop_path.py
git commit -m "$(cat <<'EOF'
feat(drop_path): 递归路径细分算法(避开已确定敌方基地)

plan_drop_path(A, B, bot) → list[Point2] waypoint:
- 默认直线 [A, B]
- AB 穿过已确定敌方基地(zone 有 known townhall) → 插入 C
- C = M.position + (P-M).normalized * (R+push), P = M 在 AB 垂足
- 递归 max_depth=3 兜底防 loop

R_MINERAL_AVOID=15(zone 影响半径), PUSH_DIST=5(buffer)。
"已确定"用 zone.known_enemy_units.of_type(townhalls).exists 判定。

参考 docs/plans/2026-05-23-drop-act-design.md §2。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: DropActPayload + DirectiveType.DROP_ACT

**Files:**
- Modify: `src/vibecraft/directives/types.py` (加 `DirectiveType.DROP_ACT`)
- Modify: `src/vibecraft/directives/models.py` (加 `DropActPayload`)
- Create: `tests/unit/test_drop_act_payload.py`

### Step 1: 写 failing test

```python
# tests/unit/test_drop_act_payload.py
"""DropActPayload schema 验证。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibecraft.directives.models import Directive, DropActPayload
from vibecraft.directives.types import DirectiveType


class TestDropActPayload:
    def test_simple_style_default(self) -> None:
        p = DropActPayload(
            cargo_unit="Zealot",
            cargo_count=4,
            drop_target="enemy_natural:mineral",
        )
        assert p.style == "simple"
        assert p.transport == "WarpPrism"
        assert p.after_unload == "attack_workers"
        assert p.priority == 60
        assert p.type == DirectiveType.DROP_ACT

    def test_warp_then_drop_requires_warp_at(self) -> None:
        """style=warp_then_drop 但没 warp_at → schema 允许(运行时再校验)."""
        # Pydantic 不强制 warp_at(simple 不需要)。Director 运行时拒绝。
        p = DropActPayload(
            style="warp_then_drop",
            cargo_unit="DarkTemplar",
            cargo_count=4,
            drop_target="enemy_main:production",
            warp_at="enemy_main:ramp_outside",
        )
        assert p.style == "warp_then_drop"
        assert p.warp_at == "enemy_main:ramp_outside"

    def test_unknown_style_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DropActPayload(
                style="invalid",
                cargo_unit="Zealot",
                cargo_count=1,
                drop_target="enemy_main:mineral",
            )

    def test_cargo_count_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            DropActPayload(
                cargo_unit="Zealot",
                cargo_count=0,
                drop_target="enemy_main:mineral",
            )

    def test_directive_wrap(self) -> None:
        """payload 能正常装进 Directive envelope。"""
        p = DropActPayload(
            cargo_unit="Zealot", cargo_count=4,
            drop_target="enemy_natural:mineral"
        )
        d = Directive(payload=p, issued_at=10.0)
        assert d.type == DirectiveType.DROP_ACT
```

### Step 2: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_drop_act_payload.py -v
```
Expected: FAIL (`DirectiveType.DROP_ACT not found`)

### Step 3: 实现

**modify `src/vibecraft/directives/types.py`** 加 DROP_ACT 到 DirectiveType enum:

```python
class DirectiveType(str, Enum):
    # ...现有列表...
    DROP_ACT = "drop_act"  # 2026-05-23 复合空投(simple/warp_then_drop)
```

**modify `src/vibecraft/directives/models.py`** 加 DropActPayload 类（StructureOverridePayload 附近）:

```python
class DropActPayload(_PayloadBase):
    """L4 复合空投指令(2026-05-23 brainstorming)。

    style:
      simple         → GenericDropAct(load@home → fly → unload@target)
      warp_then_drop → PrismWarpDropAct(fly → warp@frontline → load → fly → unload@deep)
    """

    type: Literal[DirectiveType.DROP_ACT] = DirectiveType.DROP_ACT
    style: Literal["simple", "warp_then_drop"] = "simple"
    cargo_unit: str
    cargo_count: int = Field(ge=1)
    transport: str = "WarpPrism"
    drop_target: str  # "enemy_natural:mineral" 等
    warp_at: str | None = None  # 仅 warp_then_drop 用
    after_unload: Literal[
        "attack_workers", "attack_production", "retreat", "siege"
    ] = "attack_workers"
    priority: int = 60
```

把 `DropActPayload` 加进 `Payload` Union（同文件后面有 Union 定义）。

### Step 4: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_drop_act_payload.py -v
```
Expected: 5 passed

### Step 5: Commit

```bash
git add src/vibecraft/directives/types.py src/vibecraft/directives/models.py tests/unit/test_drop_act_payload.py
git commit -m "$(cat <<'EOF'
feat(directives): DropActPayload + DirectiveType.DROP_ACT

L4 复合空投指令 schema:
- style: simple | warp_then_drop
- cargo_unit + cargo_count + transport(默认 WarpPrism)
- drop_target (spec 字符串如 "enemy_natural:mineral")
- warp_at (仅 warp_then_drop 用)
- after_unload: attack_workers|attack_production|retreat|siege

Director._exec_drop_act 在后续 task 实现。

参考 docs/plans/2026-05-23-drop-act-design.md §4。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: LLM prompt + parser

**Files:**
- Modify: `src/vibecraft/llm/prompt.py` (加 DropTarget spec 说明 + drop_act 示例)
- Modify: `src/vibecraft/llm/parser.py` (确认能 parse drop_act payload, 多半 Pydantic discriminated union 自动 work)
- Create: `tests/unit/test_llm_drop_act_parse.py`
- 验证: 跑 1 局 acceptance 确认 LLM 没退步

### Step 1: 写 failing test

```python
# tests/unit/test_llm_drop_act_parse.py
"""LLM 解析"出 4 叉子棱镜空投对面二矿"类话语。

用 MockLLMProvider(scripted)模拟 LLM 输出 JSON,验证 IntentParser 能拿到正确
DropActPayload。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.directives.models import DropActPayload
from vibecraft.directives.types import DirectiveType
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.llm.parser import IntentParseResult
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
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


def _scripted_provider(raw_json: dict) -> MockLLMProvider:
    """让 mock LLM 第一个响应返回指定 JSON。"""
    return MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw=raw_json,
                input_tokens=10, output_tokens=20, latency_ms=50.0,
            )
        ]
    )


@pytest.mark.asyncio
async def test_parse_zealot_drop_natural(library, session) -> None:
    """模拟 LLM 输出 → DropActPayload."""
    raw = {
        "interpretation_zh": "4 个叉子棱镜空投对面二矿(矿区)",
        "directives": [
            {
                "type": "drop_act",
                "style": "simple",
                "cargo_unit": "Zealot",
                "cargo_count": 4,
                "transport": "WarpPrism",
                "drop_target": "enemy_natural:mineral",
                "after_unload": "attack_workers",
            }
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("4 叉子棱镜空投对面二矿", ctx=None)
    assert isinstance(outcome, IntentParseResult)
    assert len(outcome.directives) == 1
    payload = outcome.directives[0].payload
    assert isinstance(payload, DropActPayload)
    assert payload.cargo_unit == "Zealot"
    assert payload.cargo_count == 4
    assert payload.drop_target == "enemy_natural:mineral"


@pytest.mark.asyncio
async def test_parse_dt_warp_drop_production(library, session) -> None:
    raw = {
        "interpretation_zh": "棱镜带 4 DT 前线 warp + 二段空投主基地产能",
        "directives": [
            {
                "type": "drop_act",
                "style": "warp_then_drop",
                "cargo_unit": "DarkTemplar",
                "cargo_count": 4,
                "transport": "WarpPrism",
                "warp_at": "enemy_main:ramp_outside",
                "drop_target": "enemy_main:production",
            }
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("棱镜前线 warp 4 DT 再空投主基地", ctx=None)
    assert isinstance(outcome, IntentParseResult)
    payload = outcome.directives[0].payload
    assert isinstance(payload, DropActPayload)
    assert payload.style == "warp_then_drop"
    assert payload.warp_at == "enemy_main:ramp_outside"


@pytest.mark.asyncio
async def test_strategy_set_still_works(library, session) -> None:
    """回归:strategy_set / production_override 不受新 drop_act 影响。"""
    raw = {
        "interpretation_zh": "切 4bg 开局",
        "directives": [
            {"type": "strategy_set", "stage": "opening", "strategy_id": "4bg"}
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("切 4bg", ctx=None)
    assert isinstance(outcome, IntentParseResult)
    assert outcome.directives[0].type.value == "strategy_set"
```

### Step 2: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_llm_drop_act_parse.py -v
```
Expected: 2 fail（drop_act 测试），1 pass（strategy_set 回归）

### Step 3: 实现

**modify `src/vibecraft/llm/prompt.py`** 在 system prompt 加：

```
## 空投复合指令 (drop_act)

玩家说"空投对方 X 矿"类话语 → directive type "drop_act"。

字段:
  style: simple (默认) | warp_then_drop
    - simple: 家里装兵 → 飞 → 卸下(适合人族 medivac / 虫族 overlord)
    - warp_then_drop: 棱镜飞到敌方高地前 → phasing warp 兵 → 装船 → 二段深入
      (神族棱镜默认推荐 warp_then_drop,充分利用 warpgate)
  cargo_unit: "Zealot" / "DarkTemplar" / "Marine" 等
  cargo_count: 整数 >= 1
  transport: WarpPrism(神族默认) | Medivac(人族) | Overlord(虫族,需 cargo upgrade)
  drop_target: "<base_ref>:<zone_kind>"
    base_ref: enemy_main | enemy_natural | enemy_third | clock_{0..11} | map_center
    zone_kind: mineral (默认) | production
      production 只对 enemy_main/natural/third 有效
      clock_X / map_center 只 mineral
    "二矿" = enemy_natural;不带后缀默认 mineral
    "X 矿产能/基地/建筑" → production
  warp_at: (仅 style=warp_then_drop) 同 drop_target spec 格式
    典型值: "enemy_main:ramp_outside"
  after_unload: attack_workers (默认) | attack_production | retreat | siege

示例:
  "4 叉子棱镜空投对面二矿" → 
    {type:drop_act, style:simple, cargo_unit:Zealot, cargo_count:4,
     drop_target:"enemy_natural:mineral"}
  "棱镜前线 warp 4 DT 再空投主基地" →
    {type:drop_act, style:warp_then_drop, cargo_unit:DarkTemplar, cargo_count:4,
     warp_at:"enemy_main:ramp_outside", drop_target:"enemy_main:production"}
```

**modify `src/vibecraft/llm/parser.py`** 确认 `_normalize_directive_raw` 把 `{type:"drop_act", ...}` → `DropActPayload` 实例。如果用 Pydantic discriminated union (`Payload`)，自动 work；不行需在 union 中显式列。

### Step 4: 跑测试

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_llm_drop_act_parse.py -v
# + 跑现有 LLM 测试回归
.venv/Scripts/python.exe -m pytest tests/unit -k "llm or parser" -x -q
```
Expected: 全 pass。如果 LLM 回归测试退步,查 _normalize_directive_raw。

### Step 5: Commit

```bash
git add src/vibecraft/llm/prompt.py src/vibecraft/llm/parser.py tests/unit/test_llm_drop_act_parse.py
git commit -m "$(cat <<'EOF'
feat(llm): drop_act directive 解析 + system prompt

加 drop_act 类型到 LLM system prompt:
- style/cargo_unit/cargo_count/transport/drop_target/warp_at/after_unload
- 默认规则: "二矿"→enemy_natural, 不带后缀=mineral, 神族棱镜推荐 warp_then_drop

parser 通过 Pydantic discriminated union 自动 parse DropActPayload。

回归 strategy_set / production_override 单测仍 pass。

参考 docs/plans/2026-05-23-drop-act-design.md §3。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: GenericDropAct (style=simple)

**Files:**
- Create: `src/vibecraft/bot/auto_combat/protoss/plans/generic_drop_act.py`
- Create: `tests/unit/test_generic_drop_act.py`

**Approach:** 从现有 `DTPrismHarass` 改造,参数化 cargo_unit/transport/target/after_unload。
- LOAD_AT_HOME → FLY_TO_DROP (用 `drop_path.plan_drop_path`) → UNLOAD → HOVER_FINAL
- 不含 warp(那是 PrismWarpDropAct)

### Step 1: 写 failing test

```python
# tests/unit/test_generic_drop_act.py
"""GenericDropAct state machine 单测(mock prism + units)。"""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock

import pytest

from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
    GenericDropAct,
    GenericDropState,
)
from vibecraft.bot.named_spot import DropTarget


def _mock_position(x: float, y: float):
    from sc2.position import Point2
    return Point2((x, y))


@pytest.fixture
def drop_target() -> DropTarget:
    return DropTarget(
        position=_mock_position(48, 28),
        zone_kind="mineral",
        base_index=0,
        source_spec="enemy_main:mineral",
    )


def _mock_bot_with_prism_and_zealots(prism_pos, zealot_count: int):
    """bot 含 1 棱镜 + N 叉子在家附近。"""
    from sc2.position import Point2
    from sc2.ids.unit_typeid import UnitTypeId

    bot = MagicMock()
    bot.start_location = Point2((127, 119))
    bot.time = 100.0
    bot.knowledge.zone_manager.enemy_expansion_zones = []
    bot.game_info.map_center = Point2((80, 80))
    bot.game_info.playable_area = MagicMock(x=0, y=0, width=160, height=160)

    prism = MagicMock()
    prism.type_id = UnitTypeId.WARPPRISM
    prism.position = prism_pos
    prism.cargo_used = 0
    prism.health = 100
    prism.shield = 50
    prism.tag = 1
    prism.distance_to = lambda p: ((prism.position.x - p.x) ** 2 + (prism.position.y - p.y) ** 2) ** 0.5

    zealots = []
    for i in range(zealot_count):
        z = MagicMock()
        z.type_id = UnitTypeId.ZEALOT
        z.position = Point2((127 + i, 119))
        z.tag = 100 + i
        zealots.append(z)

    def _units_of_type(types):
        m = MagicMock()
        result = []
        if UnitTypeId.WARPPRISM in types or UnitTypeId.WARPPRISMPHASING in types:
            result.append(prism)
        m.__iter__ = lambda self: iter(result)
        m.__bool__ = lambda self: len(result) > 0
        m.amount = len(result)
        return m

    bot.units.of_type = _units_of_type
    bot.units = MagicMock()
    bot.units.of_type = _units_of_type
    # for ai.units(UnitTypeId.ZEALOT) 用法
    def _units_call(t):
        if t == UnitTypeId.ZEALOT:
            m = MagicMock()
            m.__iter__ = lambda self: iter(zealots)
            m.__bool__ = lambda self: len(zealots) > 0
            m.amount = len(zealots)
            m.ready = zealots
            return m
        return MagicMock(amount=0, ready=[])
    bot.units = _units_call
    return bot, prism, zealots


class TestGenericDropActInit:
    def test_initial_state_idle(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
            after_unload="attack_workers",
        )
        assert act._state == GenericDropState.IDLE


class TestGenericDropActLoadAtHome:
    """LOAD_AT_HOME 阶段:smart-cast cargo 上船,装齐后切 FLY_TO_DROP。"""

    @pytest.mark.asyncio
    async def test_load_smart_cast_zealots_onto_prism(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        bot, prism, zealots = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(127, 119), zealot_count=4
        )
        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT, cargo_count=4,
            transport=UnitTypeId.WARPPRISM, drop_target=drop_target,
            after_unload="attack_workers",
        )
        # 接 ai backref(sharpy ActBase 模式)
        act.ai = bot
        act.knowledge = MagicMock()
        act.knowledge.roles.set_task = MagicMock()
        act.cache = MagicMock()
        act.cache.own = MagicMock(return_value=MagicMock(amount=4))

        # 第一帧 execute → 切 LOAD_AT_HOME + smart-cast
        await act.execute()
        # 每个 zealot 应被 smart(prism)
        for z in zealots:
            z.smart.assert_called_with(prism)
```

### Step 2-5: 跑测试 / 实现 / commit

完整实现代码量比较大（~250 行），骨架如下：

```python
# src/vibecraft/bot/auto_combat/protoss/plans/generic_drop_act.py
"""GenericDropAct: 通用单段空投(style=simple)。

状态机:LOAD_AT_HOME → FLY_TO_DROP → UNLOAD → HOVER_FINAL
FLY 用 drop_path.plan_drop_path 规划路径,unload 后按 after_unload 微操。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.drop_path import plan_drop_path
from vibecraft.bot.named_spot import DropTarget

logger = logging.getLogger(__name__)

_ARRIVED_DISTANCE: float = 3.0
_LOADING_MIN_CARGO: int = 2
_LOADING_HARD_TIMEOUT_S: float = 60.0


class GenericDropState(str, Enum):
    IDLE = "idle"
    LOAD_AT_HOME = "load_at_home"
    FLY_TO_DROP = "fly_to_drop"
    UNLOAD = "unload"
    HOVER_FINAL = "hover_final"


class GenericDropAct(ActBase):
    """通用单段空投。"""

    def __init__(
        self,
        cargo_unit: UnitTypeId,
        cargo_count: int,
        transport: UnitTypeId,
        drop_target: DropTarget,
        after_unload: str = "attack_workers",
    ) -> None:
        super().__init__()
        self.cargo_unit = cargo_unit
        self.cargo_count = cargo_count
        self.transport = transport
        self.drop_target = drop_target
        self.after_unload = after_unload
        self._state = GenericDropState.IDLE
        self._state_entered_ts = 0.0
        self._transport_tag: int | None = None
        self._loading_since: float | None = None
        self._waypoints: list[Point2] | None = None
        self._wp_idx = 0

    async def execute(self) -> bool:
        prism = self._find_transport()
        if prism is None:
            return False
        self._transport_tag = prism.tag

        if self._state == GenericDropState.IDLE:
            self._set_state(GenericDropState.LOAD_AT_HOME)

        if self._state == GenericDropState.LOAD_AT_HOME:
            await self._handle_load_at_home(prism)
        elif self._state == GenericDropState.FLY_TO_DROP:
            await self._handle_fly_to_drop(prism)
        elif self._state == GenericDropState.UNLOAD:
            await self._handle_unload(prism)
        elif self._state == GenericDropState.HOVER_FINAL:
            await self._handle_hover_final(prism)

        return False

    # ... handlers/helpers(类似 DTPrismHarass,但参数化)...

    def _set_state(self, new_state: GenericDropState) -> None:
        if new_state != self._state:
            logger.debug(
                "GenericDropAct state: %s → %s (t=%.1fs)",
                self._state.value, new_state.value, self.ai.time,
            )
            self._state = new_state
            self._state_entered_ts = self.ai.time

    # 完整实现见后续 commit。
```

跑测试 → impl → commit:

```bash
git add src/vibecraft/bot/auto_combat/protoss/plans/generic_drop_act.py tests/unit/test_generic_drop_act.py
git commit -m "feat(generic_drop_act): 通用单段空投 ActBase 子类"
```

---

## Task 6: PrismWarpDropAct (style=warp_then_drop)

**Files:**
- Create: `src/vibecraft/bot/auto_combat/protoss/plans/prism_warp_drop_act.py`
- Create: `tests/unit/test_prism_warp_drop_act.py`

**State machine (9 states):**
```
IDLE → FLY_TO_WARP_SPOT → DEPLOY_PHASING → WARP_UNITS →
WAIT_WARP_COMPLETE → MORPH_TRANSPORT → LOAD_CARGO →
FLY_TO_FINAL → UNLOAD_FINAL → DONE
```

实现 + 单测 pattern 同 Task 5。重点：
- WARP_UNITS 阶段调 sharpy 的 warp gate API(或复用 `WarpDTAtPrism` if applicable)
- 2 段路径都用 `plan_drop_path`

Commit:
```bash
git add src/vibecraft/bot/auto_combat/protoss/plans/prism_warp_drop_act.py tests/unit/test_prism_warp_drop_act.py
git commit -m "feat(prism_warp_drop_act): 二段空投 ActBase 子类(神族默认空投行为)"
```

---

## Task 7: Director._exec_drop_act + auto-chain

**Files:**
- Modify: `src/vibecraft/bot/director.py` (加 `_exec_drop_act` 方法 + dispatch 路由)
- Create: `tests/unit/test_drop_act_director_chain.py`

**核心逻辑:**
```python
async def _exec_drop_act(self, d: Directive, payload: DropActPayload) -> None:
    """drop_act 执行:auto-chain 补依赖 + 出兵 + 实例化 ActBase 注入 plan."""
    # 1. resolve drop_target → DropTarget
    if self._bot is None:
        return
    reg = NamedSpotRegistry()
    target = reg.resolve_drop_target(payload.drop_target, self._bot)
    if target is None:
        self._set_override_status(d, "on_hold", f"drop_target 解析失败: {payload.drop_target}")
        return

    # 2. auto_prereq for cargo + transport(复用现有机制)
    now_ts = float(getattr(self._bot, "time", 0.0))
    for unit_name in (payload.cargo_unit.upper(), payload.transport.upper()):
        self._auto_build_prereqs_for(unit_name, now_ts)

    # 3. auto production_override for cargo + transport(emit ProductionOverride 自动补单位)
    # ... 类似 #205 production prereq logic ...

    # 4. 单位齐 → 实例化 ActBase 注入 sharpy plan tree
    # 检查 ready cargo + transport count
    # if ready: instantiate GenericDropAct or PrismWarpDropAct 注入 self._bot 的 plan
    # set status="active"
    # 真正完成时 _release_directive_done
```

单测 + commit.

---

## Task 8a: dt_drop_iac 切换 PrismWarpDropAct

**Files:**
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/dt_drop_iac.py`

把现有的 `DTPrismHarass` 实例化点替换成 `PrismWarpDropAct(cargo=DarkTemplar, count=8, warp_at=..., drop_target=enemy_main:production)`。

Smoke test + 单测验证 import 没破。
Commit。

---

## Task 8b: 删 DTPrismHarass + cleanup + build_acceptance 回归

**Files:**
- Delete: `src/vibecraft/bot/auto_combat/protoss/plans/dt_prism_harass.py`
- Delete: `tests/unit/test_dt_prism_harass.py` (如有)
- Modify: 其他 import DTPrismHarass 的地方

**验证：**
```bash
.venv/Scripts/python.exe -m pytest tests/unit -x -q
.venv/Scripts/python.exe scripts/build_acceptance.py dt_drop_iac --runs 1 --opponent veryeasy
.venv/Scripts/python.exe scripts/build_acceptance.py dt_drop_iac --runs 3 --parallel 3 --opponent veryhard
```
预期：VeryEasy 主要 check pass; VeryHard ≥ 17/21 (与 #204 baseline 持平或更好)。

Commit。

---

## Task 9: PWA DropActCard.vue

**Files:**
- Create: `web/src/components/DropActCard.vue`
- Modify: `web/src/components/CommandCardStack.vue` (drop_act 路由到 DropActCard)
- Create: `web/src/components/__tests__/DropActCard.test.ts`

**显示样式:**
- Header: "空投 X×N → <target_display>"
- target_display 用 source_spec 中文化(如 "二矿矿区")
- 状态: pending(等单位) / executing(空投中) / done(自动消失)
- 关联子卡片: auto_prereq 的 structure_override(灰色, 缩进显示)

**TDD:** vitest 测渲染 props + status 切换。

Commit。

---

## 整体验证

```bash
# 单测全过
.venv/Scripts/python.exe -m pytest tests/unit -x -q

# typecheck + 前端测试
cd web; npm run typecheck && npm test -- --run

# build_acceptance 关键回归(神族 dt_drop_iac 重做)
.venv/Scripts/python.exe scripts/build_acceptance.py dt_drop_iac --runs 1 --opponent veryeasy
.venv/Scripts/python.exe scripts/build_acceptance.py dt_drop_iac --runs 3 --parallel 3 --opponent veryhard
```

全部 pass 后,在 PWA 上手动测 3 句话:
- "出 4 叉子棱镜空投对面二矿"
- "棱镜空投对面三矿矿区"
- "棱镜前线 warp 4 DT 再空投主基地"

观察 PWA 上看到完整 chain(出兵 + 补建筑 + 空投卡片),棱镜实际飞 + 路径绕开已知敌方基地。

---

## 总结

10 个 task,每 task 完成后 commit。Task 1-6 是新增模块/类(完全独立),Task 7-8b 是接入现有 Director + plan 改造(risk 集中),Task 9 是前端。

实施过程中若发现 task 拆分不够细(单 step 超 5 分钟),用 superpowers:executing-plans 的"按需拆分"原则。

如果 Task 8b 的 build_acceptance VeryHard 退步明显(<14/21),回头审 PrismWarpDropAct 状态机或 path 算法,而不是放宽 spec。
