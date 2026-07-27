"""单元测试：compute_strongest_cluster_center — 聚团 + 造价加权 + 迟滞。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from vibecraft.bot.telemetry import compute_strongest_cluster_center

# ---------------------------------------------------------------------------
# Fake bot helpers
# ---------------------------------------------------------------------------


def _pt(x: float, y: float) -> Any:
    return SimpleNamespace(x=x, y=y)


def _cost(minerals: int, vespene: int) -> Any:
    return SimpleNamespace(minerals=minerals, vespene=vespene)


class _FakeUnit:
    """最小单位 mock：有 tag / position / type_id / is_structure。"""

    def __init__(
        self,
        tag: int,
        x: float,
        y: float,
        type_name: str = "STALKER",
        minerals: int = 125,
        vespene: int = 50,
        is_structure: bool = False,
    ) -> None:
        self.tag = tag
        self.position = _pt(x, y)
        self.type_id = SimpleNamespace(name=type_name)
        self.is_structure = is_structure
        self._minerals = minerals
        self._vespene = vespene


class _FakeUnits(list):
    """最小 Units mock：支持 .filter(fn) 返回 _FakeUnits，模拟 python-sc2 Units。"""

    def filter(self, fn: Any) -> _FakeUnits:
        return _FakeUnits(u for u in self if fn(u))

    @property
    def center(self) -> Any:
        if not self:
            return None
        cx = sum(u.position.x for u in self) / len(self)
        cy = sum(u.position.y for u in self) / len(self)
        return _pt(cx, cy)

    def __bool__(self) -> bool:
        return len(self) > 0


def _make_bot(units: list[_FakeUnit]) -> Any:
    """构造 fake bot：提供 units.filter + calculate_cost + start_location + knowledge。"""
    fake_units = _FakeUnits(units)

    def _calculate_cost(type_id: Any) -> Any:
        # 从 tag→unit 查找（type_id 直接就是 type_id）
        for u in units:
            if u.type_id is type_id:
                return _cost(u._minerals, u._vespene)
        return _cost(0, 0)

    return SimpleNamespace(
        units=fake_units,
        calculate_cost=_calculate_cost,
        start_location=_pt(0.0, 0.0),
        # knowledge.roles 不存在 → _persistent_task_tags 返空集（走 except）
    )


# ---------------------------------------------------------------------------
# 测试：基本聚团 + 最强团选择
# ---------------------------------------------------------------------------


class TestStrongestClusterCenter:
    """compute_strongest_cluster_center 核心逻辑。"""

    def test_no_units_returns_none(self) -> None:
        """无主力单位 → 返回 None。"""
        bot = _make_bot([])
        result = compute_strongest_cluster_center(bot)
        assert result is None

    def test_single_cluster_returns_center(self) -> None:
        """所有单位在同一团 → 返回质心。"""
        units = [
            _FakeUnit(1, 10.0, 10.0, minerals=100, vespene=0),
            _FakeUnit(2, 11.0, 10.0, minerals=100, vespene=0),
            _FakeUnit(3, 10.5, 11.0, minerals=100, vespene=0),
        ]
        bot = _make_bot(units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        assert result is not None
        # 质心应接近 (10.5, 10.33)
        assert abs(result.x - 10.5) < 0.1
        assert abs(result.y - 10.33) < 0.2

    def test_two_clusters_picks_stronger_one(self) -> None:
        """两团：A 团 3 个便宜单位，B 团 2 个贵单位 → 返回 B 团质心。

        A 团：3 × (100矿+0气) = 300 战斗力，质心 ≈ (5, 5)
        B 团：2 × (200矿+150气) = 700 战斗力，质心 ≈ (100, 100)
        """
        a_units = [
            _FakeUnit(1, 4.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(2, 5.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(3, 6.0, 5.0, minerals=100, vespene=0),
        ]
        b_units = [
            _FakeUnit(4, 100.0, 100.0, minerals=200, vespene=150),  # Colossus-ish
            _FakeUnit(5, 101.0, 100.0, minerals=200, vespene=150),
        ]
        bot = _make_bot(a_units + b_units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        assert result is not None
        # 应落在 B 团质心附近 (100.5, 100.0)
        assert result.x > 50.0, f"应选 B 团(x~100)，实际 x={result.x}"
        assert result.y > 50.0, f"应选 B 团(y~100)，实际 y={result.y}"

    def test_two_clusters_a_stronger_picks_a(self) -> None:
        """A 团战斗力更高 → 返回 A 团质心。"""
        a_units = [
            _FakeUnit(1, 5.0, 5.0, minerals=300, vespene=200),  # Carrier
            _FakeUnit(2, 6.0, 5.0, minerals=300, vespene=200),
        ]
        b_units = [
            _FakeUnit(3, 100.0, 100.0, minerals=50, vespene=0),  # Zealot
            _FakeUnit(4, 101.0, 100.0, minerals=50, vespene=0),
        ]
        bot = _make_bot(a_units + b_units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        assert result is not None
        assert result.x < 50.0, f"应选 A 团(x~5)，实际 x={result.x}"


# ---------------------------------------------------------------------------
# 测试：迟滞逻辑
# ---------------------------------------------------------------------------


class TestHysteresis:
    """prev_center 迟滞防止镜头横跳。"""

    def test_slight_stronger_cluster_stays_due_to_hysteresis(self) -> None:
        """B 团略强但未超 hysteresis 倍 → 仍留 A 团（prev_center 在 A 团）。

        A 团：3 × 100矿 = 300，B 团：300 × 1.1 = 330 (< 300 × 1.25=375) → 不切换。
        """
        a_units = [
            _FakeUnit(1, 5.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(2, 6.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(3, 5.5, 6.0, minerals=100, vespene=0),
        ]
        b_units = [
            _FakeUnit(4, 100.0, 100.0, minerals=110, vespene=0),  # 330 total
            _FakeUnit(5, 101.0, 100.0, minerals=110, vespene=0),
            _FakeUnit(6, 100.5, 101.0, minerals=110, vespene=0),
        ]
        bot = _make_bot(a_units + b_units)
        # prev_center 在 A 团附近
        prev_center = _pt(5.5, 5.5)
        result = compute_strongest_cluster_center(
            bot, cluster_radius=12.0, hysteresis=1.25, prev_center=prev_center
        )
        assert result is not None
        # 应继续跟 A 团（x < 50）
        assert result.x < 50.0, f"应留 A 团(x~5)，实际 x={result.x}"

    def test_much_stronger_cluster_switches_despite_hysteresis(self) -> None:
        """B 团战斗力远超 A 团 * hysteresis → 切换到 B 团。

        A 团：300，B 团：600 (= 300 × 2 > 300 × 1.25) → 切换。
        """
        a_units = [
            _FakeUnit(1, 5.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(2, 6.0, 5.0, minerals=100, vespene=0),
            _FakeUnit(3, 5.5, 6.0, minerals=100, vespene=0),
        ]
        b_units = [
            _FakeUnit(4, 100.0, 100.0, minerals=200, vespene=0),  # 600 total
            _FakeUnit(5, 101.0, 100.0, minerals=200, vespene=0),
            _FakeUnit(6, 100.5, 101.0, minerals=200, vespene=0),
        ]
        bot = _make_bot(a_units + b_units)
        prev_center = _pt(5.5, 5.5)
        result = compute_strongest_cluster_center(
            bot, cluster_radius=12.0, hysteresis=1.25, prev_center=prev_center
        )
        assert result is not None
        # 应切到 B 团（x > 50）
        assert result.x > 50.0, f"应切 B 团(x~100)，实际 x={result.x}"

    def test_no_prev_center_picks_strongest(self) -> None:
        """无 prev_center → 无迟滞，直接选最强团。"""
        a_units = [
            _FakeUnit(1, 5.0, 5.0, minerals=100, vespene=0),
        ]
        b_units = [
            _FakeUnit(2, 100.0, 100.0, minerals=500, vespene=300),
        ]
        bot = _make_bot(a_units + b_units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0, prev_center=None)
        assert result is not None
        assert result.x > 50.0, "无迟滞应选 B 团(最强)"


# ---------------------------------------------------------------------------
# 测试：calculate_cost 异常兜底
# ---------------------------------------------------------------------------


class TestCalculateCostFallback:
    """calculate_cost 抛异常时兜底用单位数。"""

    def test_cost_exception_fallback_to_unit_count(self) -> None:
        """calculate_cost 全部失败 → 战斗力退化为单位数，较大团获胜。

        A 团 3 个单位，B 团 1 个单位，calculate_cost 全抛 → 3 > 1 → 选 A 团。
        """
        a_units = [
            _FakeUnit(1, 5.0, 5.0),
            _FakeUnit(2, 6.0, 5.0),
            _FakeUnit(3, 5.5, 6.0),
        ]
        b_units = [
            _FakeUnit(4, 100.0, 100.0),
        ]
        all_units = a_units + b_units

        fake_units = _FakeUnits(all_units)

        def _bad_cost(type_id: Any) -> Any:
            raise RuntimeError("calculate_cost not available")

        bot = SimpleNamespace(
            units=fake_units,
            calculate_cost=_bad_cost,
            start_location=_pt(0.0, 0.0),
        )
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        assert result is not None
        # A 团 3 个，B 团 1 个 → 选 A 团（x~5）
        assert result.x < 50.0, f"兜底用单位数时应选 A 团，实际 x={result.x}"


# ---------------------------------------------------------------------------
# 测试：建筑 / 工人过滤（_filter_main_army 透过聚团函数验证）
# ---------------------------------------------------------------------------


class TestArmyFilter:
    """结构体和非战斗单位被过滤，不参与聚团。"""

    def test_structure_excluded(self) -> None:
        """is_structure=True 的单位不算主力，只有非建筑单位被聚团。"""
        units = [
            _FakeUnit(1, 5.0, 5.0, minerals=100, is_structure=False),
            _FakeUnit(2, 6.0, 5.0, minerals=100, is_structure=False),
            # 建筑：远离主力但造价极高
            _FakeUnit(3, 100.0, 100.0, minerals=9999, is_structure=True),
        ]
        bot = _make_bot(units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        assert result is not None
        # 建筑被排除，质心在 (5.5, 5.0)
        assert result.x < 50.0, f"建筑应被排除，实际 x={result.x}"

    def test_worker_excluded(self) -> None:
        """PROBE / SCV / DRONE 被 _NON_ARMY_UNIT_TYPES 过滤，不进聚团。

        注：_NON_ARMY_UNIT_TYPES 包含 PROBE；若只有工人 → None。
        """
        units = [
            _FakeUnit(1, 5.0, 5.0, type_name="PROBE", minerals=50),
            _FakeUnit(2, 6.0, 5.0, type_name="PROBE", minerals=50),
        ]
        bot = _make_bot(units)
        result = compute_strongest_cluster_center(bot, cluster_radius=12.0)
        # PROBE 被过滤 → 无主力 → None
        assert result is None
