"""F1 镜头框选 selector 单测。

2026-06-19 设计 Step 2：
  - Selector.near_camera 字段守卫（裸 near_camera 无 unit_type/role → 报错）
  - Director._inject_camera_selectors：near_camera=True 时在 submit 前一次固化成 tags
"""

from __future__ import annotations

import pytest

# ==========================================================================
# Selector.near_camera 守卫
# ==========================================================================


def test_near_camera_bare_raises() -> None:
    """near_camera=True 但无 unit_type / role → 守卫拒绝（ValidationError）。"""
    from pydantic import ValidationError

    from vibecraft.directives.scope import Selector

    with pytest.raises(ValidationError):
        Selector(near_camera=True)


def test_near_camera_with_unit_type_passes() -> None:
    """near_camera=True + unit_type → 合法，near_camera 为 True。"""
    from vibecraft.directives.scope import Selector

    sel = Selector(unit_type="Stalker", near_camera=True)
    assert sel.near_camera is True
    assert sel.unit_type == "Stalker"


def test_near_camera_with_role_army_passes() -> None:
    """near_camera=True + role=ARMY → 合法。"""
    from vibecraft.directives.scope import Selector

    sel = Selector(role="ARMY", near_camera=True)
    assert sel.near_camera is True


def test_near_camera_with_role_any_passes() -> None:
    """near_camera=True + role=ANY → 合法。"""
    from vibecraft.directives.scope import Selector

    sel = Selector(role="ANY", near_camera=True)
    assert sel.near_camera is True


def test_near_camera_default_false() -> None:
    """near_camera 默认 False，不影响现有 Selector 用法。"""
    from vibecraft.directives.scope import Selector

    sel = Selector(unit_type="Stalker")
    assert sel.near_camera is False


def test_near_camera_false_no_type_no_role_ok() -> None:
    """near_camera=False 时无 unit_type/role 不触发守卫（普通空 selector 仍合法）。"""
    from vibecraft.directives.scope import Selector

    sel = Selector()
    assert sel.near_camera is False


def test_near_camera_unit_type_and_role_passes() -> None:
    """near_camera=True + unit_type 且 role 同时有（AND）→ 合法（守卫是 OR 检查）。"""
    from vibecraft.directives.scope import Selector

    sel = Selector(unit_type="Bunker", role="ARMY", near_camera=True)
    assert sel.near_camera is True


# ==========================================================================
# Director._inject_camera_selectors（用 FakeFacade 桩）
# ==========================================================================


def _make_inject_target(facade):
    """构造最小 director-like 对象，仅带 facade 属性，供直接调用 _inject_camera_selectors。"""
    from types import SimpleNamespace

    from vibecraft.bot.director import Director

    return SimpleNamespace(
        facade=facade,
        _inject_camera_selectors=lambda directives, cp: Director._inject_camera_selectors(
            SimpleNamespace(facade=facade), directives, cp
        ),
    )


def _make_directive_with_unit_claim(selector):
    """构造带 UnitClaimPayload + 给定 selector 的 Directive。"""
    from vibecraft.directives.models import Directive, UnitClaimPayload
    from vibecraft.directives.scope import TargetSpec
    from vibecraft.directives.task import Task
    from vibecraft.directives.types import DirectiveType

    task = Task(
        primary_action={
            "verb": "attack_move",
            "target": TargetSpec(kind="named_spot", named_spot="enemy_main"),
        }
    )
    payload = UnitClaimPayload(
        type=DirectiveType.UNIT_CLAIM,
        selector=selector,
        task=task,
    )
    return Directive(payload=payload, issued_at=0.0)


def _make_directive_with_group_assign(selector):
    """构造带 GroupAssignPayload + 给定 selector 的 Directive。"""
    from vibecraft.directives.models import Directive, GroupAssignPayload
    from vibecraft.directives.types import DirectiveType

    payload = GroupAssignPayload(
        type=DirectiveType.GROUP_ASSIGN,
        group_id=1,
        selector=selector,
    )
    return Directive(payload=payload, issued_at=0.0)


def _call_inject(facade, directives, camera_point):
    """直接调用 Director._inject_camera_selectors（不启动完整 Director）。"""
    from types import SimpleNamespace

    from vibecraft.bot.director import Director

    stub = SimpleNamespace(facade=facade)
    Director._inject_camera_selectors(stub, directives, camera_point)


# --------------------------------------------------------------------------
# unit_type 路径
# --------------------------------------------------------------------------


def test_inject_unit_type_selects_in_box() -> None:
    """unit_type 路径：框内的 tag 被选到，框外不选；写回 tags + near_camera 清 False。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    # resolve_selector("Stalker") → [10, 20, 30]
    facade.selector_stub["Stalker"] = [10, 20, 30]
    # 坐标：10 在框内，20/30 在框外
    facade._tag_positions = {
        10: (5.0, 5.0),  # inside: |5-10|=5≤12, |5-8|=3≤9
        20: (30.0, 5.0),  # outside x: |30-10|=20>12
        30: (5.0, 20.0),  # outside y: |20-8|=12>9
    }

    sel = Selector(unit_type="Stalker", near_camera=True)
    d = _make_directive_with_unit_claim(sel)
    _call_inject(facade, [d], camera_point=(10.0, 8.0))

    assert sel.near_camera is False, "near_camera 应被清为 False"
    assert sel.tags == [10], f"只有框内的 tag=10 应被选到，实际 {sel.tags}"


def test_inject_unit_type_out_of_box_empty() -> None:
    """所有候选都在框外 → tags 空列表（不崩）。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    facade.selector_stub["Phoenix"] = [99]
    facade._tag_positions = {99: (100.0, 100.0)}  # 远离镜头

    sel = Selector(unit_type="Phoenix", near_camera=True)
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert sel.tags == []
    assert sel.near_camera is False


def test_inject_unit_type_bunker_structure() -> None:
    """unit_type=BUNKER → 走 resolve_selector stub → 建筑 tag 能被框选（Step0+框选打通）。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    facade.selector_stub["BUNKER"] = [501, 502]
    facade._tag_positions = {
        501: (2.0, 2.0),  # inside
        502: (50.0, 50.0),  # outside
    }

    sel = Selector(unit_type="BUNKER", near_camera=True)
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert 501 in (sel.tags or []), "框内建筑 tag=501 应被选到"
    assert 502 not in (sel.tags or []), "框外建筑 tag=502 不应被选到"
    assert sel.near_camera is False


# --------------------------------------------------------------------------
# role=ARMY 路径（排除农民 + 框过滤）
# --------------------------------------------------------------------------


def test_inject_role_army_excludes_workers() -> None:
    """role=ARMY 路径：all_own_unit_tags(include_workers=False) 排除农民。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    # 注入：tag 1=农民，tag 2=叉子（army），两者都在框内
    facade._own_unit_tags = [1, 2]
    facade._worker_tags = {1}  # tag 1 是农民
    facade._tag_positions = {
        1: (0.0, 0.0),  # inside，但会被农民过滤
        2: (0.0, 0.0),  # inside，army 单位
    }

    sel = Selector(role="ARMY", near_camera=True)
    d = _make_directive_with_unit_claim(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert sel.near_camera is False
    assert 1 not in (sel.tags or []), "农民 tag=1 不应被 ARMY 路径选到"
    assert 2 in (sel.tags or []), "叉子 tag=2 应被 ARMY 路径选到"


def test_inject_role_idle_includes_workers() -> None:
    """role=IDLE/ANY/LLM_CONTROLLED 路径：all_own_unit_tags(include_workers=True) 含农民。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    facade._own_unit_tags = [1, 2]
    facade._worker_tags = {1}
    facade._tag_positions = {1: (0.0, 0.0), 2: (0.0, 0.0)}

    sel = Selector(role="IDLE", near_camera=True)
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert 1 in (sel.tags or []), "IDLE 路径含农民，tag=1 应被选到"
    assert 2 in (sel.tags or [])


# --------------------------------------------------------------------------
# count 截断
# --------------------------------------------------------------------------


def test_inject_count_truncates() -> None:
    """count=2 时只保留框内前 2 个 tag。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()
    facade.selector_stub["Stalker"] = [10, 20, 30]
    facade._tag_positions = {
        10: (0.0, 0.0),
        20: (1.0, 0.0),
        30: (2.0, 0.0),
    }

    sel = Selector(unit_type="Stalker", near_camera=True, count=2)
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert len(sel.tags or []) == 2, f"count=2 应只保留 2 个，实际 {sel.tags}"
    assert sel.near_camera is False


# --------------------------------------------------------------------------
# camera_point=None
# --------------------------------------------------------------------------


def test_inject_camera_point_none_clears_tags() -> None:
    """camera_point=None → tags=[] + near_camera=False，不崩。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()

    sel = Selector(unit_type="Stalker", near_camera=True)
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=None)

    assert sel.tags == []
    assert sel.near_camera is False


# --------------------------------------------------------------------------
# 无 near_camera 的 selector 不被修改
# --------------------------------------------------------------------------


def test_inject_skips_non_near_camera_selectors() -> None:
    """near_camera=False 的 selector 不受影响（原有 tags 保留）。"""
    from vibecraft.bot.facade import FakeFacade
    from vibecraft.directives.scope import Selector

    facade = FakeFacade()

    sel = Selector(unit_type="Stalker", tags=[5, 6])
    d = _make_directive_with_group_assign(sel)
    _call_inject(facade, [d], camera_point=(0.0, 0.0))

    assert sel.tags == [5, 6], "非 near_camera selector 的 tags 不应被修改"
    assert sel.near_camera is False
