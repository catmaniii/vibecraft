"""地堡货舱控制 directive（BUNKER_CARGO）schema + 执行层单测。

覆盖：
1. schema：BunkerCargoPayload 能 parse（load / unload 两种 action）；进 Payload 联合；
   额外字段被拒；count 字段可选（默认 None）。
2. 执行（FakeFacade + Director mock）：
   - action="unload" → 每个地堡发 UNLOADALL_BUNKER；非地堡建筑跳过。
   - action="load" → 调 facade.load_bunker(tag, count or 4)；汇总 acted 数。
   - 未找到地堡 → status=failed。
3. FakeFacade.load_bunker：记录 load_bunker_calls；返回 min(count, 4)。
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

# ==========================================================================
# 1. Schema
# ==========================================================================


def test_bunker_cargo_load_parses() -> None:
    """BunkerCargoPayload action=load 最简结构可 parse。"""
    from vibecraft.directives.models import BunkerCargoPayload

    p = BunkerCargoPayload.model_validate(
        {"type": "bunker_cargo", "action": "load", "selector": {"unit_type": "Bunker"}}
    )
    assert p.type.value == "bunker_cargo"
    assert p.action == "load"
    assert p.count is None  # 默认 None


def test_bunker_cargo_unload_parses() -> None:
    """BunkerCargoPayload action=unload 可 parse；count 省略正常。"""
    from vibecraft.directives.models import BunkerCargoPayload

    p = BunkerCargoPayload.model_validate(
        {"type": "bunker_cargo", "action": "unload", "selector": {"tags": [301, 302]}}
    )
    assert p.action == "unload"
    assert p.selector.tags == [301, 302]
    assert p.count is None


def test_bunker_cargo_load_with_count() -> None:
    """count 字段可传整数（如 2）。"""
    from vibecraft.directives.models import BunkerCargoPayload

    p = BunkerCargoPayload.model_validate(
        {
            "type": "bunker_cargo",
            "action": "load",
            "selector": {"unit_type": "Bunker"},
            "count": 2,
        }
    )
    assert p.count == 2


def test_bunker_cargo_in_payload_union() -> None:
    """BunkerCargoPayload 能通过 Payload 判别联合路由。"""
    from vibecraft.directives.models import BunkerCargoPayload, Payload

    adapter = TypeAdapter(Payload)
    obj = adapter.validate_python(
        {"type": "bunker_cargo", "action": "unload", "selector": {"unit_type": "Bunker"}}
    )
    assert isinstance(obj, BunkerCargoPayload)
    assert obj.action == "unload"


def test_bunker_cargo_rejects_extra_fields() -> None:
    """_PayloadBase extra=forbid：BunkerCargoPayload 里塞多余字段 → ValidationError。"""
    from vibecraft.directives.models import BunkerCargoPayload

    with pytest.raises(ValidationError):
        BunkerCargoPayload.model_validate(
            {
                "type": "bunker_cargo",
                "action": "load",
                "selector": {"tags": [1]},
                "unknown_field": "oops",
            }
        )


def test_bunker_cargo_requires_action() -> None:
    """action 必填，缺少 → ValidationError。"""
    from vibecraft.directives.models import BunkerCargoPayload

    with pytest.raises(ValidationError):
        BunkerCargoPayload.model_validate({"type": "bunker_cargo", "selector": {"tags": [1]}})


def test_bunker_cargo_requires_selector() -> None:
    """selector 必填，缺少 → ValidationError。"""
    from vibecraft.directives.models import BunkerCargoPayload

    with pytest.raises(ValidationError):
        BunkerCargoPayload.model_validate({"type": "bunker_cargo", "action": "load"})


# ==========================================================================
# 2. Director dispatch（FakeFacade）
# ==========================================================================


def _make_director_with_fake_facade():
    """构造最小 Director stub 供 _apply_to_facade 单测。"""
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
    return d, facade


def _make_bunker_cargo_directive(action: str, tags: list[int], count: int | None = None):
    """构造 BUNKER_CARGO Directive。"""
    from vibecraft.directives.models import BunkerCargoPayload, Directive

    payload_data: dict = {
        "type": "bunker_cargo",
        "action": action,
        "selector": {"tags": tags},
    }
    if count is not None:
        payload_data["count"] = count

    payload = BunkerCargoPayload.model_validate(payload_data)
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by="voice",
        source_text=f"地堡{action}",
    )


def test_bunker_cargo_unload_sends_unloadall() -> None:
    """action=unload → 每个地堡 tag 发 UNLOADALL_BUNKER。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER", 302: "BUNKER"}

    directive = _make_bunker_cargo_directive("unload", [301, 302])
    d._apply_to_facade(directive, now=0.0)

    for tag in [301, 302]:
        ability_ids = [c[1] for c in facade.casts if c[0] == tag]
        assert "UNLOADALL_BUNKER" in ability_ids, (
            f"tag={tag} 应发 UNLOADALL_BUNKER，实际: {ability_ids}"
        )

    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done"
    assert "卸载" in status_info.get("reason", "")


def test_bunker_cargo_load_calls_load_bunker() -> None:
    """action=load → 调 facade.load_bunker(tag, count)；count 默认 4。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}

    directive = _make_bunker_cargo_directive("load", [301])
    d._apply_to_facade(directive, now=0.0)

    assert (301, 4) in facade.load_bunker_calls, (
        f"应调 load_bunker(301, 4)，实际: {facade.load_bunker_calls}"
    )

    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done"
    assert "装兵" in status_info.get("reason", "")


def test_bunker_cargo_load_with_count_param() -> None:
    """action=load count=2 → load_bunker 收到 count=2。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}

    directive = _make_bunker_cargo_directive("load", [301], count=2)
    d._apply_to_facade(directive, now=0.0)

    assert (301, 2) in facade.load_bunker_calls, (
        f"应调 load_bunker(301, 2)，实际: {facade.load_bunker_calls}"
    )


def test_bunker_cargo_non_bunker_skipped() -> None:
    """selector 选中非地堡建筑 → 跳过，不发 ability，不调 load_bunker。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {401: "SUPPLYDEPOT"}

    directive = _make_bunker_cargo_directive("unload", [401])
    d._apply_to_facade(directive, now=0.0)

    assert facade.casts == [], f"非地堡不应发 ability，实际: {facade.casts}"
    assert facade.load_bunker_calls == []
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed"


def test_bunker_cargo_unknown_tag_skipped() -> None:
    """tag 不在 _tag_types（找不到）→ 跳过，status=failed。"""
    d, facade = _make_director_with_fake_facade()
    # _tag_types 为空

    directive = _make_bunker_cargo_directive("unload", [999])
    d._apply_to_facade(directive, now=0.0)

    assert facade.casts == []
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed"


def test_bunker_cargo_multiple_bunkers_load() -> None:
    """多地堡 load → 每个分别调 load_bunker，acted 数为 load_bunker 返回值之和。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER", 302: "BUNKER"}

    directive = _make_bunker_cargo_directive("load", [301, 302], count=4)
    d._apply_to_facade(directive, now=0.0)

    # load_bunker 被调两次（每地堡一次）
    assert len(facade.load_bunker_calls) == 2
    assert (301, 4) in facade.load_bunker_calls
    assert (302, 4) in facade.load_bunker_calls

    # FakeFacade.load_bunker 返回 min(4, 4)=4，两地堡 acted=8
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done"
    # reason 含 acted 数（8）
    assert "8" in status_info.get("reason", "")


# ==========================================================================
# 3. FakeFacade.load_bunker
# ==========================================================================


def test_fake_facade_load_bunker_records_and_returns() -> None:
    """FakeFacade.load_bunker 记录 (bunker_tag, count)，返回 min(count, 4)。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    result = f.load_bunker(301, 3)
    assert result == 3  # min(3, 4) = 3
    assert (301, 3) in f.load_bunker_calls

    result2 = f.load_bunker(302, 10)
    assert result2 == 4  # min(10, 4) = 4（满载上限）
    assert (302, 10) in f.load_bunker_calls
