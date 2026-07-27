"""Acceptance spec 模型 + loader。"""

from __future__ import annotations

import pytest

from vibecraft.build_acceptance.spec import AcceptanceSpec, parse_mmss


def test_parse_mmss():
    assert parse_mmss("0:35") == 35.0
    assert parse_mmss("3:14") == 194.0
    assert parse_mmss("10:06") == 606.0


def test_spec_loads_from_dict():
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Protoss",
            "checks": [
                {"id": "g1", "type": "building_started", "unit": "GATEWAY", "by": "0:35"},
                {
                    "id": "ds",
                    "type": "building_complete",
                    "unit": "DARKSHRINE",
                    "at": "3:14",
                    "tol": 25,
                },
            ],
        }
    )
    assert spec.strategy_id == "demo"
    assert len(spec.checks) == 2
    assert spec.checks[0].by_s == 35.0
    assert spec.checks[1].at_s == 194.0
    assert spec.checks[1].tol == 25


def test_spec_check_needs_at_or_by():
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Protoss",
                "checks": [{"id": "bad", "type": "building_started", "unit": "GATEWAY"}],
            }
        )


def test_pressure_contact_needs_within():
    """pressure_contact 缺 within → 校验失败。"""
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Zerg",
                "checks": [{"id": "p", "type": "pressure_contact", "by": "6:00"}],
            }
        )


def test_pressure_contact_valid():
    """pressure_contact 有 within → 校验通过；near 可缺省。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Zerg",
            "checks": [{"id": "p", "type": "pressure_contact", "by": "6:00", "within": 20}],
        }
    )
    assert spec.checks[0].type == "pressure_contact"
    assert spec.checks[0].near is None


def test_harass_damage_needs_min():
    """harass_damage 缺 min → 校验失败。"""
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Terran",
                "checks": [{"id": "h", "type": "harass_damage", "by": "5:00"}],
            }
        )


def test_harass_damage_valid():
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Terran",
            "checks": [{"id": "h", "type": "harass_damage", "by": "5:00", "min": 4}],
        }
    )
    assert spec.checks[0].min == 4


def test_scout_value_needs_all_fields():
    """scout_value 缺 near/within/min 任一 → 校验失败。"""
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Terran",
                "checks": [{"id": "s", "type": "scout_value", "unit": "REAPER"}],
            }
        )


def test_scout_value_valid_without_at_or_by():
    """scout_value 扫全局 telemetry,不需要 at/by;有 unit/near/within/min 即合法。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Terran",
            "checks": [
                {
                    "id": "s",
                    "type": "scout_value",
                    "unit": "REAPER",
                    "near": "enemy_main",
                    "within": 40,
                    "min": 360,
                }
            ],
        }
    )
    assert spec.checks[0].type == "scout_value"
    assert spec.checks[0].min == 360


# ---- Task #311 player override e2e: PlayerAction + army_after_player_action ----


def test_player_action_parse():
    """spec.player_actions 解析:at 转 at_s,默认 mode/target_area 为 None。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Protoss",
            "player_actions": [
                {"at": "5:00", "verb": "retreat"},
                {"at": "4:00", "verb": "attack", "mode": "all_in", "target_area": "enemy_main"},
            ],
            "checks": [
                {"id": "noop", "type": "building_started", "unit": "GATEWAY", "by": "0:35"},
            ],
        }
    )
    assert len(spec.player_actions) == 2
    assert spec.player_actions[0].at_s == 300.0
    assert spec.player_actions[0].verb == "retreat"
    assert spec.player_actions[0].mode is None
    assert spec.player_actions[0].target_area is None
    assert spec.player_actions[1].at_s == 240.0
    assert spec.player_actions[1].verb == "attack"
    assert spec.player_actions[1].mode == "all_in"
    assert spec.player_actions[1].target_area == "enemy_main"


def test_player_actions_default_empty():
    """没写 player_actions 字段时默认空列表(向后兼容现有 spec)。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Protoss",
            "checks": [
                {"id": "g", "type": "building_started", "unit": "GATEWAY", "by": "0:35"},
            ],
        }
    )
    assert spec.player_actions == []


def test_player_action_verb_must_be_known():
    """verb 只能是 attack/defend/retreat/vision。"""
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Protoss",
                "player_actions": [{"at": "5:00", "verb": "harass"}],  # 不在白名单
                "checks": [
                    {"id": "g", "type": "building_started", "unit": "GATEWAY", "by": "0:35"}
                ],
            }
        )


def test_army_after_player_action_check_valid():
    """army_after_player_action check 配齐 action_idx/after_s/near/within/op 通过。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Protoss",
            "player_actions": [{"at": "5:00", "verb": "retreat"}],
            "checks": [
                {
                    "id": "retreated",
                    "type": "army_after_player_action",
                    "action_idx": 0,
                    "after_s": 30,
                    "near": "home",
                    "within": 25.0,
                    "op": "<=",
                },
            ],
        }
    )
    c = spec.checks[0]
    assert c.type == "army_after_player_action"
    assert c.action_idx == 0
    assert c.after_s == 30
    assert c.near == "home"
    assert c.within == 25.0
    assert c.op == "<="


def test_army_after_player_action_check_missing_fields():
    """army_after_player_action 缺 action_idx/after_s/near/within 任一 → 校验失败。"""
    base = {
        "strategy_id": "demo",
        "my_race": "Protoss",
        "player_actions": [{"at": "5:00", "verb": "retreat"}],
    }
    # 缺 action_idx
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                **base,
                "checks": [
                    {
                        "id": "x",
                        "type": "army_after_player_action",
                        "after_s": 30,
                        "near": "home",
                        "within": 25,
                    }
                ],
            }
        )
    # 缺 after_s
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                **base,
                "checks": [
                    {
                        "id": "x",
                        "type": "army_after_player_action",
                        "action_idx": 0,
                        "near": "home",
                        "within": 25,
                    }
                ],
            }
        )
    # 缺 near
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                **base,
                "checks": [
                    {
                        "id": "x",
                        "type": "army_after_player_action",
                        "action_idx": 0,
                        "after_s": 30,
                        "within": 25,
                    }
                ],
            }
        )
    # 缺 within
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                **base,
                "checks": [
                    {
                        "id": "x",
                        "type": "army_after_player_action",
                        "action_idx": 0,
                        "after_s": 30,
                        "near": "home",
                    }
                ],
            }
        )


def test_army_after_player_action_action_idx_out_of_range():
    """action_idx 超出 player_actions 长度 → 校验失败。"""
    with pytest.raises(ValueError):
        AcceptanceSpec.model_validate(
            {
                "strategy_id": "demo",
                "my_race": "Protoss",
                "player_actions": [{"at": "5:00", "verb": "retreat"}],
                "checks": [
                    {
                        "id": "x",
                        "type": "army_after_player_action",
                        "action_idx": 1,
                        "after_s": 30,
                        "near": "home",
                        "within": 25,
                    }
                ],
            }
        )


def test_army_after_player_action_op_default_le():
    """op 缺省 = "<="。"""
    spec = AcceptanceSpec.model_validate(
        {
            "strategy_id": "demo",
            "my_race": "Protoss",
            "player_actions": [{"at": "5:00", "verb": "retreat"}],
            "checks": [
                {
                    "id": "x",
                    "type": "army_after_player_action",
                    "action_idx": 0,
                    "after_s": 30,
                    "near": "home",
                    "within": 25,
                },
            ],
        }
    )
    assert spec.checks[0].op == "<="
