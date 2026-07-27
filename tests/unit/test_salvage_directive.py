"""通用建筑回收 directive（SALVAGE）schema + 执行层单测。

覆盖：
1. schema：SalvagePayload 能 parse；进 Payload 联合；额外字段被拒；done_when/activate_when 可带。
2. 执行（FakeFacade + Director mock）：
   - 地堡 tag → 发 SALVAGEEFFECT_SALVAGE（2026-06-19 真机验证的实际可用 ability，单条）。
   - 地堡 has_cargo → 先 UNLOADALL_BUNKER + 加 _pending_salvage_tags（不直接 salvage）。
   - _tick_pending_salvage：cargo 清空 → 发 SALVAGEEFFECT_SALVAGE + 从 pending 移除。
   - _tick_pending_salvage：建筑消失（tag 找不到）→ 移除 pending，不发 ability。
   - 感应塔 → SALVAGEEFFECT_SALVAGE（通用 salvage 效果，同地堡）。
   - 非可回收建筑（SupplyDepot）→ 不发 + 计 unsalvageable + 状态 failed + 友好提示。
3. FakeFacade.get_unit_type_name：_tag_types 注入；找不到 → None。
4. FakeFacade.bunker_has_cargo：_tag_cargo 注入；找不到 → False。
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

# ==========================================================================
# 1. Schema
# ==========================================================================


def test_salvage_payload_parses_minimal() -> None:
    """SalvagePayload 最简结构可 parse（type + selector.tags）。"""
    from vibecraft.directives.models import SalvagePayload

    p = SalvagePayload.model_validate({"type": "salvage", "selector": {"tags": [301, 302]}})
    assert p.type.value == "salvage"
    assert p.selector.tags == [301, 302]


def test_salvage_payload_in_payload_union() -> None:
    """SalvagePayload 能通过 Payload 判别联合路由，type 字段正确区分。"""
    from vibecraft.directives.models import Payload

    adapter = TypeAdapter(Payload)
    obj = adapter.validate_python({"type": "salvage", "selector": {"unit_type": "Bunker"}})
    from vibecraft.directives.models import SalvagePayload

    assert isinstance(obj, SalvagePayload)
    assert obj.selector.unit_type == "Bunker"


def test_salvage_payload_rejects_extra_fields() -> None:
    """_PayloadBase extra=forbid：SalvagePayload 里塞多余字段 → ValidationError。"""
    from vibecraft.directives.models import SalvagePayload

    with pytest.raises(ValidationError):
        SalvagePayload.model_validate(
            {"type": "salvage", "selector": {"tags": [1]}, "unknown_field": "oops"}
        )


def test_salvage_payload_requires_selector() -> None:
    """selector 必填，缺少 → ValidationError。"""
    from vibecraft.directives.models import SalvagePayload

    with pytest.raises(ValidationError):
        SalvagePayload.model_validate({"type": "salvage"})


def test_salvage_payload_accepts_done_when() -> None:
    """done_when 可选字段由 _PayloadBase 提供，SalvagePayload 也支持。"""
    from vibecraft.directives.models import SalvagePayload

    p = SalvagePayload.model_validate(
        {
            "type": "salvage",
            "selector": {"tags": [1]},
            "done_when": {"kind": "supply_used", "op": ">=", "value": 100},
        }
    )
    assert p.done_when is not None
    assert p.done_when.kind == "supply_used"  # type: ignore[union-attr]


def test_salvage_payload_accepts_activate_when() -> None:
    """activate_when 可选字段，SalvagePayload 也支持。"""
    from vibecraft.directives.models import SalvagePayload

    p = SalvagePayload.model_validate(
        {
            "type": "salvage",
            "selector": {"tags": [1]},
            "activate_when": {"kind": "tech_done", "upgrade_id": "TERRANINFANTRYWEAPONSLEVEL1"},
        }
    )
    assert p.activate_when is not None
    assert p.activate_when.kind == "tech_done"  # type: ignore[union-attr]


# ==========================================================================
# 2. FakeFacade.get_unit_type_name
# ==========================================================================


def test_fake_facade_get_unit_type_name_found() -> None:
    """_tag_types 注入后能返回对应名称。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_types = {101: "BUNKER", 202: "SENSORTOWER"}
    assert f.get_unit_type_name(101) == "BUNKER"
    assert f.get_unit_type_name(202) == "SENSORTOWER"


def test_fake_facade_get_unit_type_name_not_found() -> None:
    """tag 不在 _tag_types → 返回 None，不抛异常。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.get_unit_type_name(999) is None


# ==========================================================================
# 3. _SharpyFacadeBase.get_unit_type_name
# ==========================================================================


def test_sharpy_get_unit_type_name_from_structures() -> None:
    """建筑在 structures → 能找到并返回 type_id.name。"""
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    bunker = SimpleNamespace(tag=501, type_id=SimpleNamespace(name="BUNKER"))

    class _Structures:
        def find_by_tag(self, tag: int):
            return bunker if tag == 501 else None

    class _Units:
        def find_by_tag(self, _tag: int):
            return None

    bot = SimpleNamespace(structures=_Structures(), units=_Units())
    facade = cls(bot)
    assert facade.get_unit_type_name(501) == "BUNKER"


def test_sharpy_get_unit_type_name_from_units() -> None:
    """不在 structures 但在 units → 仍能返回 type_id.name。"""
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    marine = SimpleNamespace(tag=999, type_id=SimpleNamespace(name="MARINE"))

    class _Structures:
        def find_by_tag(self, _tag: int):
            return None

    class _Units:
        def find_by_tag(self, tag: int):
            return marine if tag == 999 else None

    bot = SimpleNamespace(structures=_Structures(), units=_Units())
    facade = cls(bot)
    assert facade.get_unit_type_name(999) == "MARINE"


def test_sharpy_get_unit_type_name_not_found() -> None:
    """两边都找不到 → 返回 None，不抛异常。"""
    from types import SimpleNamespace

    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    class _Empty:
        def find_by_tag(self, _tag: int):
            return None

    bot = SimpleNamespace(structures=_Empty(), units=_Empty())
    facade = cls(bot)
    assert facade.get_unit_type_name(12345) is None


# ==========================================================================
# 4. Director SALVAGE dispatch（用 FakeFacade + 最小 Director stub）
# ==========================================================================


def _make_director_with_fake_facade():
    """构造一个最小 Director 供 _apply_to_facade / _tick_pending_salvage 单测。

    不依赖 SC2 / sharpy / 真实 GameSession。
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from vibecraft.bot.director import Director
    from vibecraft.bot.facade import FakeFacade

    facade = FakeFacade()

    # Director.__init__ 需要较多依赖，改用 object.__new__ 绕过，手动注入最小字段。
    d = object.__new__(Director)
    d.facade = facade
    d._override_status: dict[str, dict[str, str]] = {}
    d._event_queue: list[dict] = []
    d._event_callback = None  # _push_event 需要此字段（None=无 callback，直接丢弃 event）
    d.session = MagicMock()
    # _resolve_selector_with_count 需要 _voice_groups / _task_chains / _unit_semantics
    d._voice_groups: dict[int, set[int]] = {}
    d._task_chains: dict[str, set[int]] = {}
    d._chain_structures: dict[str, int] = {}
    d._unit_semantics: dict[int, dict] = {}
    d._unit_states: dict[int, str] = {}
    d.parser = SimpleNamespace(my_race=None)
    d._stealth_manager = MagicMock()
    d._directive_to_cell_id: dict[str, int] = {}
    d._cell_id_to_directive_id: dict[int, str] = {}
    # 2026-06-19 占用地堡预备队
    d._pending_salvage_tags: set[int] = set()
    return d, facade


def _make_salvage_directive(tags: list[int]):
    """构造一个带 selector.tags 的 SALVAGE Directive。"""
    from vibecraft.directives.models import Directive, SalvagePayload

    payload = SalvagePayload.model_validate({"type": "salvage", "selector": {"tags": tags}})
    # Directive: payload 携带 type，issued_at 必填（游戏内秒，单测用 0.0）
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by="voice",
        source_text="回收地堡",
    )


def test_salvage_bunker_single_ability_sent() -> None:
    """地堡 tag → 只发 SALVAGEEFFECT_SALVAGE（2026-06-19 真机验证：地堡实际可用的 salvage
    ability 是通用的 SALVAGEEFFECT_SALVAGE，不是 SALVAGEBUNKER_SALVAGE（后者真机 NotSupported）。
    一个 tag 只发一条 ability（第二条会覆盖第一条致都失效）。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}

    directive = _make_salvage_directive([301])
    d._apply_to_facade(directive, now=0.0)

    ability_ids_sent = [c[1] for c in facade.casts if c[0] == 301]
    assert ability_ids_sent == ["SALVAGEEFFECT_SALVAGE"], (
        f"地堡应只发一条 SALVAGEEFFECT_SALVAGE，实际: {ability_ids_sent}"
    )


def test_salvage_sensortower_ability_sent() -> None:
    """感应塔 → SALVAGEEFFECT_SALVAGE 发出（通用 salvage 效果，同地堡）。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {401: "SENSORTOWER"}

    directive = _make_salvage_directive([401])
    d._apply_to_facade(directive, now=0.0)

    ability_ids_sent = [c[1] for c in facade.casts if c[0] == 401]
    assert "SALVAGEEFFECT_SALVAGE" in ability_ids_sent


def test_salvage_unsalvageable_building_not_cast() -> None:
    """不可回收建筑（SupplyDepot）→ 不发 ability，status=failed，提示含建筑名。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {501: "SUPPLYDEPOT"}

    directive = _make_salvage_directive([501])
    d._apply_to_facade(directive, now=0.0)

    # 没有任何 cast
    assert facade.casts == [], f"不可回收建筑不应发 ability，实际: {facade.casts}"
    # 状态为 failed
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed", f"状态应为 failed，实际: {status_info}"
    # reason 提示含 SUPPLYDEPOT
    assert "SUPPLYDEPOT" in status_info.get("reason", ""), (
        f"reason 应含 SUPPLYDEPOT，实际: {status_info}"
    )


def test_salvage_unknown_tag_not_cast() -> None:
    """tag 不在 _tag_types（找不到建筑）→ 不发 ability，status=failed。"""
    d, facade = _make_director_with_fake_facade()
    # _tag_types 为空，tag 999 不存在

    directive = _make_salvage_directive([999])
    d._apply_to_facade(directive, now=0.0)

    assert facade.casts == []
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed"


def test_salvage_mixed_bunker_and_unsalvageable() -> None:
    """地堡 + 补给站混选 → 地堡发 ability（salvaged=1），补给站计入 unsalvageable，status=done。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER", 501: "SUPPLYDEPOT"}

    directive = _make_salvage_directive([301, 501])
    d._apply_to_facade(directive, now=0.0)

    # 地堡有 cast（单条 SALVAGEEFFECT_SALVAGE）
    bunker_casts = [c for c in facade.casts if c[0] == 301]
    assert len(bunker_casts) == 1
    assert bunker_casts[0][1] == "SALVAGEEFFECT_SALVAGE"

    # 补给站没有 cast
    depot_casts = [c for c in facade.casts if c[0] == 501]
    assert depot_casts == []

    # status=done（有至少一个成功回收）
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done"
    # reason 同时提及"回收 1 个"和"SUPPLYDEPOT 不支持回收"
    reason = status_info.get("reason", "")
    assert "1" in reason
    assert "SUPPLYDEPOT" in reason


def test_salvage_status_done_when_all_salvaged() -> None:
    """全部 tag 都可回收 → status=done，reason 含 salvaged 数量。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER", 302: "BUNKER"}

    directive = _make_salvage_directive([301, 302])
    d._apply_to_facade(directive, now=0.0)

    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done"
    assert "2" in status_info.get("reason", "")


def test_salvage_empty_selector_fails() -> None:
    """selector.tags=[] → 没有 cast，status=failed（无可回收建筑）。"""
    d, facade = _make_director_with_fake_facade()

    directive = _make_salvage_directive([])
    d._apply_to_facade(directive, now=0.0)

    assert facade.casts == []
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed"


# ==========================================================================
# 5. 占用地堡回收：先卸载、再回收（_pending_salvage_tags 状态机）
# ==========================================================================


def test_salvage_occupied_bunker_defers_to_pending() -> None:
    """地堡 has_cargo=True → 先发 UNLOADALL_BUNKER，不直接 salvage，tag 进 _pending_salvage_tags。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}
    facade._tag_cargo = {301: True}  # 地堡有兵

    directive = _make_salvage_directive([301])
    d._apply_to_facade(directive, now=0.0)

    # 应发 UNLOADALL_BUNKER，不发 SALVAGEEFFECT_SALVAGE
    ability_ids = [c[1] for c in facade.casts if c[0] == 301]
    assert "UNLOADALL_BUNKER" in ability_ids, f"应先发 UNLOADALL_BUNKER，实际: {ability_ids}"
    assert "SALVAGEEFFECT_SALVAGE" not in ability_ids, (
        f"占用地堡本帧不应发 salvage，实际: {ability_ids}"
    )

    # tag 应在 pending 里
    assert 301 in d._pending_salvage_tags, "占用地堡应进 _pending_salvage_tags"

    # 状态应为 done（deferred > 0 = 计入成功路径）
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "done", f"状态应 done，实际: {status_info}"
    assert "卸载" in status_info.get("reason", ""), f"reason 应含'卸载'，实际: {status_info}"


def test_salvage_empty_bunker_no_defer() -> None:
    """地堡 has_cargo=False → 直接发 SALVAGEEFFECT_SALVAGE，不进 pending。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}
    facade._tag_cargo = {301: False}  # 地堡无兵

    directive = _make_salvage_directive([301])
    d._apply_to_facade(directive, now=0.0)

    ability_ids = [c[1] for c in facade.casts if c[0] == 301]
    assert "SALVAGEEFFECT_SALVAGE" in ability_ids
    assert "UNLOADALL_BUNKER" not in ability_ids
    assert 301 not in d._pending_salvage_tags


def test_tick_pending_salvage_fires_when_cargo_cleared() -> None:
    """_tick_pending_salvage：cargo 清空（bunker_has_cargo=False）→ 发 SALVAGEEFFECT_SALVAGE + 移出 pending。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}
    # 先模拟有兵 → 进 pending
    facade._tag_cargo = {301: True}
    d._pending_salvage_tags.add(301)

    # 模拟兵已出来（cargo 变 False）
    facade._tag_cargo = {301: False}
    d._tick_pending_salvage(now=10.0)

    ability_ids = [c[1] for c in facade.casts if c[0] == 301]
    assert "SALVAGEEFFECT_SALVAGE" in ability_ids, f"cargo 清空后应发 salvage，实际: {ability_ids}"
    assert 301 not in d._pending_salvage_tags, "发完后应从 pending 移除"


def test_tick_pending_salvage_removes_gone_structure() -> None:
    """_tick_pending_salvage：建筑已消失（get_unit_type_name=None）→ 移出 pending，不发 ability。"""
    d, facade = _make_director_with_fake_facade()
    # _tag_types 为空，tag 301 找不到（模拟建筑已被打掉）
    d._pending_salvage_tags.add(301)

    d._tick_pending_salvage(now=10.0)

    assert facade.casts == [], f"建筑消失不应发 ability，实际: {facade.casts}"
    assert 301 not in d._pending_salvage_tags, "消失的建筑应从 pending 移除"


def test_tick_pending_salvage_waits_while_cargo() -> None:
    """_tick_pending_salvage：cargo 仍有兵 → 不发 salvage，保持在 pending 等下次 tick。"""
    d, facade = _make_director_with_fake_facade()
    facade._tag_types = {301: "BUNKER"}
    facade._tag_cargo = {301: True}  # 还有兵
    d._pending_salvage_tags.add(301)

    d._tick_pending_salvage(now=10.0)

    ability_ids = [c[1] for c in facade.casts if c[0] == 301]
    assert "SALVAGEEFFECT_SALVAGE" not in ability_ids, "还有兵不应发 salvage"
    assert 301 in d._pending_salvage_tags, "还有兵应留在 pending 等下次"


# ==========================================================================
# 6. FakeFacade.bunker_has_cargo
# ==========================================================================


def test_fake_facade_bunker_has_cargo_true() -> None:
    """_tag_cargo 注入 True → bunker_has_cargo 返回 True。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f._tag_cargo = {301: True}
    assert f.bunker_has_cargo(301) is True


def test_fake_facade_bunker_has_cargo_false() -> None:
    """_tag_cargo 注入 False / 未注入 → bunker_has_cargo 返回 False。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    assert f.bunker_has_cargo(999) is False
    f._tag_cargo = {301: False}
    assert f.bunker_has_cargo(301) is False
