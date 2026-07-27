"""通用维修指令（REPAIR）schema + 执行层单测。

覆盖：
1. schema：RepairPayload 能 parse（selector 必填，worker_count 默认 None）；进 Payload 联合；
   额外字段被拒；done_when/activate_when 可带。
2. FakeFacade.ensure_repair / get_unit_health_percentage 行为。
3. Director._apply_to_facade REPAIR 分支：存入 _repair_orders + status="维修中"。
4. Director._tick_repair_orders：
   - 有损伤目标 → 调 ensure_repair 派 SCV。
   - 所有目标满血 → 标 done + 从 _repair_orders 移除。
   - selector 找不到任何目标 → 标 done。
   - 部分目标满血部分损伤 → 继续维修损伤目标。
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

# ==========================================================================
# 1. Schema
# ==========================================================================


def test_repair_payload_parses_minimal() -> None:
    """RepairPayload 最简结构可 parse（type + selector.unit_type）。"""
    from vibecraft.directives.models import RepairPayload

    p = RepairPayload.model_validate({"type": "repair", "selector": {"unit_type": "Battlecruiser"}})
    assert p.type.value == "repair"
    assert p.selector.unit_type == "Battlecruiser"
    assert p.worker_count is None


def test_repair_payload_with_worker_count() -> None:
    """worker_count 字段可传整数。"""
    from vibecraft.directives.models import RepairPayload

    p = RepairPayload.model_validate(
        {
            "type": "repair",
            "selector": {"unit_type": "Battlecruiser"},
            "worker_count": 5,
        }
    )
    assert p.worker_count == 5


def test_repair_payload_with_tags_selector() -> None:
    """selector.tags 也可以使用。"""
    from vibecraft.directives.models import RepairPayload

    p = RepairPayload.model_validate({"type": "repair", "selector": {"tags": [101, 202]}})
    assert p.selector.tags == [101, 202]


def test_repair_payload_in_payload_union() -> None:
    """RepairPayload 能通过 Payload 判别联合路由。"""
    from vibecraft.directives.models import Payload, RepairPayload

    adapter = TypeAdapter(Payload)
    obj = adapter.validate_python({"type": "repair", "selector": {"unit_type": "Bunker"}})
    assert isinstance(obj, RepairPayload)
    assert obj.selector.unit_type == "Bunker"


def test_repair_payload_rejects_extra_fields() -> None:
    """_PayloadBase extra=forbid：RepairPayload 里塞多余字段 → ValidationError。"""
    from vibecraft.directives.models import RepairPayload

    with pytest.raises(ValidationError):
        RepairPayload.model_validate(
            {
                "type": "repair",
                "selector": {"unit_type": "Battlecruiser"},
                "unknown_field": "oops",
            }
        )


def test_repair_payload_requires_selector() -> None:
    """selector 必填，缺少 → ValidationError。"""
    from vibecraft.directives.models import RepairPayload

    with pytest.raises(ValidationError):
        RepairPayload.model_validate({"type": "repair"})


# ==========================================================================
# 2. FakeFacade.ensure_repair + get_unit_health_percentage
# ==========================================================================


def test_fake_facade_get_unit_health_percentage_found() -> None:
    """_tag_health 注入后能返回对应血量百分比。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_health = {101: 0.5, 202: 1.0}
    assert f.get_unit_health_percentage(101) == 0.5
    assert f.get_unit_health_percentage(202) == 1.0


def test_fake_facade_get_unit_health_percentage_not_found() -> None:
    """tag 不在 _tag_health → 返回 None，不抛异常。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.get_unit_health_percentage(999) is None


def test_fake_facade_ensure_repair_records_and_returns_count() -> None:
    """损伤目标（hp<0.99）→ 记录 (tag, count) 到 ensure_repair_calls，返回 count。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_health = {101: 0.5}  # 受损
    result = f.ensure_repair(101, 3)
    assert result == 3
    assert (101, 3) in f.ensure_repair_calls


def test_fake_facade_ensure_repair_full_health_returns_zero() -> None:
    """满血目标（hp>=0.99）→ 返回 0，不派。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_health = {101: 1.0}
    result = f.ensure_repair(101, 3)
    assert result == 0


def test_fake_facade_ensure_repair_not_found_returns_zero() -> None:
    """target_tag 不在 _tag_health（找不到）→ 返回 0。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    # _tag_health 为空，找不到 999
    result = f.ensure_repair(999, 3)
    assert result == 0


# ==========================================================================
# 3. Director._apply_to_facade REPAIR 分支
# ==========================================================================


def _make_director_with_fake_facade():
    """构造最小 Director stub 供 _apply_to_facade / _tick_repair_orders 单测。"""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from vibecraft.bot.director import Director
    from vibecraft.bot.facade import FakeFacade

    facade = FakeFacade()
    d = object.__new__(Director)
    d.facade = facade
    d._override_status: dict[str, dict[str, str]] = {}
    d._event_queue: list[dict] = []
    d._event_callback = None
    d.session = MagicMock()
    d._voice_groups: dict[int, set[int]] = {}
    d._task_chains: dict[str, set[int]] = {}
    d._chain_structures: dict[str, int] = {}
    d._unit_semantics: dict[int, dict] = {}
    d._unit_states: dict[int, str] = {}
    d.parser = SimpleNamespace(my_race=None)
    d._stealth_manager = MagicMock()
    d._directive_to_cell_id: dict[str, int] = {}
    d._cell_id_to_directive_id: dict[int, str] = {}
    d._pending_salvage_tags: set[int] = set()
    from vibecraft.bot.director import Directive as _D  # noqa: F401

    d._repair_orders: dict[str, object] = {}
    return d, facade


def _make_repair_directive(selector_data: dict, worker_count: int | None = None):
    """构造 REPAIR Directive。"""
    from vibecraft.directives.models import Directive, RepairPayload

    data: dict = {"type": "repair", "selector": selector_data}
    if worker_count is not None:
        data["worker_count"] = worker_count

    payload = RepairPayload.model_validate(data)
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by="voice",
        source_text="修理大舰",
    )


def test_apply_to_facade_repair_registers_order() -> None:
    """REPAIR _apply_to_facade → 存入 _repair_orders，status='维修中'。"""
    d, _facade = _make_director_with_fake_facade()

    directive = _make_repair_directive({"tags": [101]}, worker_count=3)
    d._apply_to_facade(directive, now=0.0)

    # 存入 _repair_orders
    assert directive.id in d._repair_orders, "REPAIR 应存入 _repair_orders"

    # status 变成"维修中"
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "维修中", f"status 应为维修中，实际: {status_info}"


# ==========================================================================
# 4. Director._tick_repair_orders
# ==========================================================================


def test_tick_repair_orders_dispatches_repair_for_damaged() -> None:
    """有损伤目标 → 调 ensure_repair。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_health = {101: 0.5}  # 受损

    directive = _make_repair_directive({"tags": [101]}, worker_count=3)
    d._apply_to_facade(directive, now=0.0)

    d._tick_repair_orders(now=1.0)

    assert (101, 3) in facade.ensure_repair_calls, (
        f"应调 ensure_repair(101, 3)，实际: {facade.ensure_repair_calls}"
    )
    # 仍在 _repair_orders（还没满血）
    assert directive.id in d._repair_orders


def test_tick_repair_orders_done_when_all_healthy() -> None:
    """所有目标满血 → status=done，从 _repair_orders 移除。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_health = {101: 1.0}  # 满血

    directive = _make_repair_directive({"tags": [101]})
    d._apply_to_facade(directive, now=0.0)

    d._tick_repair_orders(now=1.0)

    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done", f"满血应标 done，实际: {status_info}"
    assert directive.id not in d._repair_orders, "done 后应从 _repair_orders 移除"


def test_tick_repair_orders_done_when_no_targets() -> None:
    """selector 找不到任何目标（全消失）→ 标 done，移除。"""
    d, _facade = _make_director_with_fake_facade()
    # _tag_health 为空，tag 999 找不到（消失）

    directive = _make_repair_directive({"tags": [999]})
    d._apply_to_facade(directive, now=0.0)

    d._tick_repair_orders(now=1.0)

    status_info = d._override_status.get(directive.id, {})
    # selector tags=[999]，但 _tag_health[999] = None，resolve_selector_with_count 会返回 [999]
    # (tags 直接返回，不过滤不存在的)；但 get_unit_health_percentage(999) = None → 跳过
    # all_healthy = True（没有任何 hp < 0.99 的目标）→ done
    assert status_info.get("status") == "done"
    assert directive.id not in d._repair_orders


def test_tick_repair_orders_partial_done() -> None:
    """部分目标满血、部分损伤 → 继续维修损伤目标，不标 done。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_health = {101: 1.0, 202: 0.3}  # 101 满血，202 受损

    directive = _make_repair_directive({"tags": [101, 202]}, worker_count=2)
    d._apply_to_facade(directive, now=0.0)

    d._tick_repair_orders(now=1.0)

    # 202 受损 → 调 ensure_repair
    assert (202, 2) in facade.ensure_repair_calls, (
        f"受损目标应调 ensure_repair，实际: {facade.ensure_repair_calls}"
    )
    # 还没 done
    assert directive.id in d._repair_orders

    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") != "done"


def test_tick_repair_orders_uses_default_worker_count() -> None:
    """worker_count=None → 默认 3 个 SCV。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_health = {101: 0.5}

    directive = _make_repair_directive({"tags": [101]})  # worker_count=None
    d._apply_to_facade(directive, now=0.0)

    d._tick_repair_orders(now=1.0)

    # 默认 worker_count or 3 = 3
    assert (101, 3) in facade.ensure_repair_calls
