"""P0d Task 6: 7 个新 done_when checker。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from vibecraft.bot.task_monitor import DONE_CHECKERS


def _make_game_state(**kw):
    """构造 sc2 BotAI-like 状态。

    structures(type_id).amount / units(type_id).amount 通过 MagicMock 返回。
    structures(type_id).ready.amount 用 structure_ready_amount(默认 = structure_amount)。
    already_pending(type_id) 同样 mock。
    """

    def _typed_query(amount, ready_amount):
        result = MagicMock()
        result.amount = amount
        result.ready = MagicMock(amount=ready_amount)
        return MagicMock(return_value=result)

    structure_amount = kw.get("structure_amount", 0)
    structure_ready_amount = kw.get("structure_ready_amount", structure_amount)
    return SimpleNamespace(
        structures=_typed_query(structure_amount, structure_ready_amount),
        units=_typed_query(kw.get("unit_amount", 0), kw.get("unit_amount", 0)),
        workers=MagicMock(amount=kw.get("worker_amount", 0)),
        already_pending=MagicMock(return_value=kw.get("pending", 0)),
        minerals=kw.get("minerals", 0),
        gas=kw.get("gas", 0),
        supply_used=kw.get("supply_used", 0),
        supply_cap=kw.get("supply_cap", 0),
    )


# ---- structure_count ----


def test_structure_count_true_when_ready_meets_threshold():
    """2026-05-25 bug 8:只算 .ready.amount(已建好),不算 building/pending。"""
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_ready_amount=8)
    done_when = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


def test_structure_count_false_when_ready_below():
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_ready_amount=5)
    done_when = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is False


def test_structure_count_excludes_building_and_pending():
    """bug 8 核心:玩家"修两个 BF" → 必须等建好,刚开始建不算。
    structure_amount(含 building)=5,ready=0 → 0 < 2 → 不 done。
    """
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_amount=5, structure_ready_amount=0, pending=3)
    done_when = {"kind": "structure_count", "structure_type": "Forge", "op": ">=", "value": 2}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is False, (
        "building 中的 Forge 不该算 done(玩家期望建好)"
    )


# ---- own_unit_count ----


def test_own_unit_count_true():
    checker = DONE_CHECKERS["own_unit_count"]
    state = _make_game_state(unit_amount=6)
    done_when = {"kind": "own_unit_count", "unit_type": "Immortal", "op": ">=", "value": 6}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


def test_own_unit_count_includes_pending():
    checker = DONE_CHECKERS["own_unit_count"]
    state = _make_game_state(unit_amount=4, pending=2)
    done_when = {"kind": "own_unit_count", "unit_type": "Immortal", "op": ">=", "value": 6}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


# ---- supply_used / supply_cap ----


def test_supply_used_lt():
    checker = DONE_CHECKERS["supply_used"]
    state = _make_game_state(supply_used=60)
    done_when = {"kind": "supply_used", "op": "<", "value": 70}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


def test_supply_cap_ge():
    checker = DONE_CHECKERS["supply_cap"]
    state = _make_game_state(supply_cap=200)
    done_when = {"kind": "supply_cap", "op": ">=", "value": 200}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


# ---- minerals / gas ----


def test_minerals_ge():
    checker = DONE_CHECKERS["minerals"]
    state = _make_game_state(minerals=1200)
    done_when = {"kind": "minerals", "op": ">=", "value": 1000}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


def test_gas_lt():
    checker = DONE_CHECKERS["gas"]
    state = _make_game_state(gas=80)
    done_when = {"kind": "gas", "op": "<", "value": 200}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


# ---- worker_count ----


def test_worker_count_ge():
    checker = DONE_CHECKERS["worker_count"]
    state = _make_game_state(worker_amount=50)
    done_when = {"kind": "worker_count", "op": ">=", "value": 50}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True


# ---- 共通: 不识别的 UnitTypeId 返回 False（structure 类） ----


def test_structure_count_unknown_type_returns_false():
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_amount=99)
    done_when = {
        "kind": "structure_count",
        "structure_type": "NonExistentBuilding",
        "op": ">=",
        "value": 1,
    }
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is False


# ---- game_state is None: 返回 False 不 crash ----


def test_resource_checker_none_state_returns_false():
    checker = DONE_CHECKERS["minerals"]
    done_when = {"kind": "minerals", "op": ">=", "value": 100}
    assert checker(done_when, "d_1", None, MagicMock(), 0.0) is False
