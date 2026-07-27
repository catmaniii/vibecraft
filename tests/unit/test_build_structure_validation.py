"""build_at / structure_override 的 structure_type 校验单测。

覆盖：
1. build_at + structure_type 是单位（Battlecruiser）→ _reject_if_invalid_structure_type 返回 True，
   状态 = "failed"，reason 含"不是农民能建造的建筑"。
2. structure_override + items[*].structure_type 含单位 → 同上被拒。
3. build_at + structure_type 是合法建筑（Barracks）→ 放行（返回 False）。
4. build_at + structure_type 未知 canonical（unknown_xyz）→ 放行（不拒）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

# ---- helpers ----------------------------------------------------------------


def _make_director_stub():
    """构造最小 Director stub，用于测 _reject_if_invalid_structure_type。"""
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
    d._repair_orders: dict[str, object] = {}
    d._in_flight: dict[str, object] = {}
    return d


def _make_build_at_directive(structure_type: str):
    """构造 build_at Directive（structure_type 可自定）。"""
    from vibecraft.directives.models import BuildAtPayload, Directive

    payload = BuildAtPayload.model_validate(
        {
            "type": "build_at",
            "structure_type": structure_type,
            "point": [10.0, 20.0],
        }
    )
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by="voice",
        source_text=f"造{structure_type}",
    )


def _make_structure_override_directive(structure_types: list[str]):
    """构造 structure_override Directive（items 可自定）。"""
    from vibecraft.directives.models import Directive, StructureOverridePayload

    payload = StructureOverridePayload.model_validate(
        {
            "type": "structure_override",
            "items": [{"structure_type": st, "target_count": 1} for st in structure_types],
        }
    )
    return Directive(
        payload=payload,
        issued_at=0.0,
        issued_by="voice",
        source_text=f"build {structure_types}",
    )


# ==========================================================================
# 1. build_at + 单位 → 被拒
# ==========================================================================


def test_build_at_with_unit_structure_type_is_rejected() -> None:
    """build_at(structure_type='Battlecruiser') → rejected（大舰是单位不是建筑）。"""
    d = _make_director_stub()
    directive = _make_build_at_directive("Battlecruiser")

    result = d._reject_if_invalid_structure_type(directive)

    assert result is True, "Battlecruiser 是单位，应被拒绝"
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed", f"拒绝后 status 应为 failed，实际: {status_info}"
    reason = status_info.get("reason", "")
    assert "不是农民能建造的建筑" in reason, f"reason 应含提示语，实际: {reason}"


def test_build_at_with_unit_includes_repair_hint() -> None:
    """build_at(structure_type='Battlecruiser') reason 应含'你是想维修吗'。"""
    d = _make_director_stub()
    directive = _make_build_at_directive("Battlecruiser")
    d._reject_if_invalid_structure_type(directive)

    reason = d._override_status.get(directive.id, {}).get("reason", "")
    assert "你是想维修吗" in reason, f"单位类型应提示 repair，实际 reason: {reason}"


# ==========================================================================
# 2. structure_override + 单位 → 被拒
# ==========================================================================


def test_structure_override_with_unit_structure_type_is_rejected() -> None:
    """structure_override items 含 Battlecruiser → rejected。"""
    d = _make_director_stub()
    directive = _make_structure_override_directive(["Battlecruiser"])

    result = d._reject_if_invalid_structure_type(directive)

    assert result is True, "structure_override 含单位 structure_type 应被拒绝"
    status_info = d._override_status.get(directive.id, {})
    assert status_info.get("status") == "failed"


def test_structure_override_with_mixed_types_is_rejected() -> None:
    """structure_override 同时含建筑(Barracks)和单位(Marine) → 遇到单位即拒。"""
    d = _make_director_stub()
    # Marine 是单位
    directive = _make_structure_override_directive(["Barracks", "Marine"])

    result = d._reject_if_invalid_structure_type(directive)

    assert result is True, "含单位的 structure_override 应被拒绝"


# ==========================================================================
# 3. build_at + 合法建筑 → 放行
# ==========================================================================


def test_build_at_with_valid_building_passes() -> None:
    """build_at(structure_type='Barracks') → 放行（返回 False）。"""
    d = _make_director_stub()
    directive = _make_build_at_directive("Barracks")

    result = d._reject_if_invalid_structure_type(directive)

    assert result is False, "Barracks 是合法建筑，应放行"
    # 不应修改 _override_status
    assert directive.id not in d._override_status


def test_build_at_with_command_center_passes() -> None:
    """build_at(structure_type='CommandCenter') → 放行。"""
    d = _make_director_stub()
    directive = _make_build_at_directive("CommandCenter")

    result = d._reject_if_invalid_structure_type(directive)

    assert result is False, "CommandCenter 是建筑，应放行"


# ==========================================================================
# 4. build_at + 未知 canonical → 放行（不拒）
# ==========================================================================


def test_build_at_with_unknown_canonical_passes() -> None:
    """build_at(structure_type='UnknownXyz') → 放行（未知 canonical 不拒绝）。"""
    d = _make_director_stub()
    directive = _make_build_at_directive("UnknownXyz")

    result = d._reject_if_invalid_structure_type(directive)

    assert result is False, "未知 canonical 应放行（宽容策略）"


# ==========================================================================
# 5. 非 build_at / 非 structure_override → 直接放行（不检查）
# ==========================================================================


def test_non_build_payload_passes_without_check() -> None:
    """REPAIR 等其他 payload 类型 → _reject_if_invalid_structure_type 直接返回 False。"""
    from vibecraft.directives.models import Directive, RepairPayload

    d = _make_director_stub()
    payload = RepairPayload.model_validate(
        {"type": "repair", "selector": {"unit_type": "Battlecruiser"}}
    )
    directive = Directive(payload=payload, issued_at=0.0, issued_by="voice", source_text="修大舰")

    result = d._reject_if_invalid_structure_type(directive)

    assert result is False, "非 build 类型应直接放行"
