"""facade Step 0+1 单测：resolve_selector 扩 structures、filter_tags_in_box、cast_unit_ability。

Step 0：resolve_selector 在 _SharpyFacadeBase 扩 self.bot.structures（真机路径）。
         FakeFacade 走 selector_stub，不区分 unit/structure；
         真机分支在 Protocol audit（test_facade_release_unit_role.py）保证签名一致。
         此处补一条 FakeFacade stub 回归 + _SharpyFacadeBase 层构造验证。

Step 1：
  A. filter_tags_in_box：FakeFacade 注入 _tag_positions 坐标表做盒过滤，
     验 框内保留、框外排除、顺序保持、缺失 tag 跳过、边界包含。
  B. cast_unit_ability：FakeFacade 记录调用正确（tag / ability_id / target）。
"""

from __future__ import annotations

# ==========================================================================
# Step 0：resolve_selector — FakeFacade stub 回归
# ==========================================================================


def test_resolve_selector_stub_returns_tags() -> None:
    """FakeFacade.resolve_selector 按 selector_stub 返回 tag 列表（unit/structure 不区分）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.selector_stub["BUNKER"] = [301, 302]
    result = f.resolve_selector(unit_type="BUNKER")
    assert result == [301, 302]


def test_resolve_selector_unknown_unit_type_returns_empty() -> None:
    """selector_stub 里没有的 unit_type → 返回空列表（不崩）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    result = f.resolve_selector(unit_type="GATEWAY")
    assert result == []


def test_sharpy_resolve_selector_includes_structures() -> None:
    """_SharpyFacadeBase.resolve_selector 扩 structures 后，建筑类型能被选到。

    构造 minimal bot stub：units 空、structures 含一个 BUNKER 类型的建筑。
    验 resolve_selector(unit_type="Bunker") 返回该建筑 tag（不再恒返回 []）。
    """
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    bunker = SimpleNamespace(
        tag=501,
        type_id=SimpleNamespace(name="BUNKER"),
        is_idle=False,
        is_gathering=False,
    )

    class _FakeUnits:
        """minimal Units stub：可迭代，find_by_tag 返 None。"""

        def __iter__(self):
            return iter([])

        def find_by_tag(self, _tag: int):
            return None

    class _FakeStructures:
        """minimal structures stub 含一个 Bunker。"""

        def __iter__(self):
            return iter([bunker])

        def find_by_tag(self, _tag: int):
            return None

    bot = SimpleNamespace(units=_FakeUnits(), structures=_FakeStructures())
    facade = cls(bot)
    result = facade.resolve_selector(unit_type="Bunker")
    assert 501 in result, f"建筑 tag 501 应被 resolve_selector 选到，实际返回 {result}"


def test_sharpy_resolve_selector_units_priority_unaffected() -> None:
    """加 structures 后，原来 units 的 idle>matched>gathering 优先级不被破坏。

    bot.units 有一个 idle probe + 一个 gathering probe；
    bot.structures 无 probe 类型建筑。
    验：结果顺序 idle(tag=1) 先于 gathering(tag=2)，structures 里不会混入杂项。
    """
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    idle_probe = SimpleNamespace(
        tag=1,
        type_id=SimpleNamespace(name="PROBE"),
        is_idle=True,
        is_gathering=False,
    )
    gathering_probe = SimpleNamespace(
        tag=2,
        type_id=SimpleNamespace(name="PROBE"),
        is_idle=False,
        is_gathering=True,
    )

    class _Units:
        def __iter__(self):
            return iter([idle_probe, gathering_probe])

        def find_by_tag(self, _tag: int):
            return None

    class _Structures:
        def __iter__(self):
            return iter([])  # 无建筑

        def find_by_tag(self, _tag: int):
            return None

    bot = SimpleNamespace(units=_Units(), structures=_Structures())
    facade = cls(bot)
    result = facade.resolve_selector(unit_type="PROBE")
    assert result == [1, 2], f"idle(1) 应先于 gathering(2)，实际 {result}"


# ==========================================================================
# Step 1A：filter_tags_in_box（FakeFacade）
# ==========================================================================


def test_filter_tags_in_box_keeps_tags_inside() -> None:
    """框内的 tag 应被保留，框外的应被排除。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_positions = {
        1: (10.0, 20.0),  # inside: |10-12|=2≤12, |20-18|=2≤9
        2: (30.0, 20.0),  # outside x: |30-12|=18>12
        3: (12.0, 28.0),  # outside y: |28-18|=10>9
    }
    result = f.filter_tags_in_box([1, 2, 3], cx=12.0, cy=18.0, half_w=12.0, half_h=9.0)
    assert 1 in result
    assert 2 not in result
    assert 3 not in result


def test_filter_tags_in_box_preserves_order() -> None:
    """返回顺序与入参 tags 顺序一致。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_positions = {
        10: (0.0, 0.0),
        20: (1.0, 1.0),
        30: (2.0, 2.0),
    }
    result = f.filter_tags_in_box([30, 10, 20], cx=1.0, cy=1.0, half_w=5.0, half_h=5.0)
    assert result == [30, 10, 20]


def test_filter_tags_in_box_skips_missing_tags() -> None:
    """_tag_positions 里没有的 tag 直接跳过（不报错、不出现在结果中）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_positions = {1: (0.0, 0.0)}
    result = f.filter_tags_in_box([1, 999], cx=0.0, cy=0.0, half_w=5.0, half_h=5.0)
    assert result == [1]
    assert 999 not in result


def test_filter_tags_in_box_boundary_inclusive() -> None:
    """边界点（exactly ±half_w / ±half_h）应被包含（≤ 不是 <）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_positions = {
        1: (12.0, 9.0),  # exactly on boundary → inside
        2: (12.01, 0.0),  # just outside x → excluded
        3: (0.0, 9.01),  # just outside y → excluded
    }
    result = f.filter_tags_in_box([1, 2, 3], cx=0.0, cy=0.0, half_w=12.0, half_h=9.0)
    assert 1 in result
    assert 2 not in result
    assert 3 not in result


def test_filter_tags_in_box_empty_input_returns_empty() -> None:
    """空 tags 列表 → 返回空列表。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    result = f.filter_tags_in_box([], cx=0.0, cy=0.0, half_w=12.0, half_h=9.0)
    assert result == []


def test_filter_tags_in_box_all_outside_returns_empty() -> None:
    """所有 tag 都在框外 → 返回空列表。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_positions = {1: (100.0, 100.0), 2: (-50.0, -50.0)}
    result = f.filter_tags_in_box([1, 2], cx=0.0, cy=0.0, half_w=12.0, half_h=9.0)
    assert result == []


def test_filter_tags_in_box_records_call() -> None:
    """filter_tags_in_box 调用被记录到通用 calls 列表。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.filter_tags_in_box([1, 2], cx=0.0, cy=0.0, half_w=12.0, half_h=9.0)
    assert any(c.method == "filter_tags_in_box" for c in f.calls)


# ==========================================================================
# Step 1B：cast_unit_ability（FakeFacade）
# ==========================================================================


def test_cast_unit_ability_records_call_no_target() -> None:
    """cast_unit_ability 无 target 时记录 (unit_tag, ability_id, None) 到 casts。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.cast_unit_ability(101, "SALVAGEBUNKER_SALVAGE")
    assert f.casts == [(101, "SALVAGEBUNKER_SALVAGE", None)]


def test_cast_unit_ability_records_call_with_target() -> None:
    """带 target dict 时，target 也被完整记录。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    target = {"kind": "point", "point": [50.0, 60.0]}
    f.cast_unit_ability(202, "EFFECT_STIMPACK", target)
    assert f.casts == [(202, "EFFECT_STIMPACK", target)]


def test_cast_unit_ability_multiple_calls_accumulate() -> None:
    """多次调用 → casts 列表追加（不覆盖）。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.cast_unit_ability(1, "ABILITY_A")
    f.cast_unit_ability(2, "ABILITY_B")
    assert len(f.casts) == 2
    assert f.casts[0][0] == 1
    assert f.casts[1][0] == 2


def test_cast_unit_ability_in_calls_log() -> None:
    """cast_unit_ability 调用被记录到通用 calls 列表。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.cast_unit_ability(55, "SALVAGEBUNKER_SALVAGE")
    assert any(c.method == "cast_unit_ability" for c in f.calls)


def test_cast_unit_ability_casts_initial_empty() -> None:
    """FakeFacade 初始化后 casts 为空列表。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.casts == []
