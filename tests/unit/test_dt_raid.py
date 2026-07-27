"""DtRaidAct 纯逻辑测试。

ActBase 子类直接构造要 sharpy 环境,这里用 __new__ 绕开 __init__、手动塞字段,
只测 state 翻转 / HP 检测 / detector 检测 / 行动选目标这几段逻辑。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """dt_raid 顶层 import 了 sharpy.plans.acts.ActBase + detector_data。
    注入 fake ActBase 让 import 过,测完清掉。"""
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    # detector_data 是真模块,不用 fake
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.dt_raid", None)
    for name in created:
        sys.modules.pop(name, None)


def _act():
    """绕过 __init__ 构造一个 act。"""
    from vibecraft.bot.auto_combat.protoss.plans.dt_raid import DtRaidAct

    act = DtRaidAct.__new__(DtRaidAct)
    act._dt_state = {}
    act._dt_last_hp = {}
    # ai / knowledge 留 caller 按需挂
    return act


def _dt(tag=1, health=40, shield=80, pos=(50.0, 50.0)):
    """fake DT unit。DT 默认 40 HP + 80 shield。"""
    return SimpleNamespace(
        tag=tag,
        health=health,
        shield=shield,
        position=SimpleNamespace(x=pos[0], y=pos[1]),
        distance_to=lambda _other, _self_pos=pos: (
            ((_other.x - _self_pos[0]) ** 2 + (_other.y - _self_pos[1]) ** 2) ** 0.5
            if hasattr(_other, "x")
            else 0.0
        ),
        attack=MagicMock(),
    )


# --- HP 计算 / state 翻转 -------------------------------------------------


def test_dt_total_hp_full():
    a = _act()
    assert a._dt_total_hp(_dt(health=40, shield=80)) == 120.0


def test_dt_total_hp_damaged():
    a = _act()
    assert a._dt_total_hp(_dt(health=20, shield=0)) == 20.0


def test_release_dt_idempotent():
    """release 已 released 的 DT → 不 clear_task 第二次(避免噪音)。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a.ai = SimpleNamespace(time=10.0)
    a._dt_state[7] = "released"
    a._release_dt(_dt(tag=7))
    a.knowledge.roles.clear_task.assert_not_called()


def test_release_dt_transitions_raid_to_released():
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a.ai = SimpleNamespace(time=10.0)
    a._dt_state[7] = "raid"
    a._release_dt(_dt(tag=7))
    assert a._dt_state[7] == "released"
    a.knowledge.roles.clear_task.assert_called_once()


# --- raid_command 行动选择 -----------------------------------------------


def test_raid_command_far_attack_moves_to_enemy_main():
    """距矿区 > _ARRIVE_DIST(22) → attack-move 推进,不打沿途敌人。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    # DT 在 (10,10),raid_target 在 (100,100),距离 ~127
    dt = _dt(pos=(10.0, 10.0))
    target = SimpleNamespace(x=100.0, y=100.0)
    a._raid_command(dt, workers=None, raid_target=target)
    dt.attack.assert_called_once_with(target)


def test_raid_command_near_picks_nearby_worker():
    """已到矿区 + 附近有 worker → attack worker(不是 raid_target)。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    dt = _dt(pos=(50.0, 50.0))
    raid_target = SimpleNamespace(x=50.0, y=50.0)  # DT 在 raid_target 上(距 0)
    # workers Units-like:exists + closer_than + closest_to
    worker = SimpleNamespace(tag=99, position=SimpleNamespace(x=52.0, y=51.0))
    nearby_units = MagicMock()
    nearby_units.exists = True
    nearby_units.closest_to = MagicMock(return_value=worker)
    workers = MagicMock()
    workers.exists = True
    workers.closer_than = MagicMock(return_value=nearby_units)
    a._raid_command(dt, workers=workers, raid_target=raid_target)
    dt.attack.assert_called_once_with(worker)


def test_raid_command_near_no_workers_fallback_to_target():
    """已到矿区但无视野到 worker → attack-move 到 raid_target 中心找。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    dt = _dt(pos=(50.0, 50.0))
    raid_target = SimpleNamespace(x=50.0, y=50.0)
    a._raid_command(dt, workers=None, raid_target=raid_target)
    dt.attack.assert_called_once_with(raid_target)


def test_raid_command_no_raid_target_noop():
    """raid_target=None(拿不到 enemy_start_locations)→ 不下命令。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    dt = _dt()
    a._raid_command(dt, workers=None, raid_target=None)
    dt.attack.assert_not_called()


# --- detector 检测 -------------------------------------------------------


def test_detector_nearby_no_detectors():
    """敌方无 detector → False。"""
    a = _act()
    a.ai = SimpleNamespace(
        enemy_units=lambda _t: [],
        enemy_structures=lambda _t: [],
    )
    assert a._detector_nearby(_dt()) is False


def test_detector_nearby_in_range_returns_true():
    """detector(turret)在 detection range 内 → True。MISSILETURRET range = 11。"""
    a = _act()
    # turret 在 (53, 50),DT 在 (50, 50),距离 3 < 11-1 buffer
    turret = SimpleNamespace(
        distance_to=lambda _dt: 3.0,
    )

    def _enemy_structures(t):
        from sc2.ids.unit_typeid import UnitTypeId as U

        return [turret] if t == U.MISSILETURRET else []

    a.ai = SimpleNamespace(
        enemy_units=lambda _t: [],
        enemy_structures=_enemy_structures,
    )
    assert a._detector_nearby(_dt(pos=(50.0, 50.0))) is True


def test_detector_nearby_far_returns_false():
    """detector 在 detection range 之外 → False(没必要主动 release)。"""
    a = _act()
    turret = SimpleNamespace(distance_to=lambda _dt: 30.0)

    def _enemy_structures(t):
        from sc2.ids.unit_typeid import UnitTypeId as U

        return [turret] if t == U.MISSILETURRET else []

    a.ai = SimpleNamespace(
        enemy_units=lambda _t: [],
        enemy_structures=_enemy_structures,
    )
    assert a._detector_nearby(_dt()) is False


# --- execute 端到端片段 --------------------------------------------------


def _make_ai_for_execute(dts: list, workers_units: list | None = None):
    """造一个 minimal ai,够 execute 走通主路径。"""
    workers_mock = MagicMock()
    if workers_units:
        workers_mock.exists = True
        workers_mock.filter = MagicMock(return_value=workers_mock)
    else:
        workers_mock.exists = False
        workers_mock.filter = MagicMock(return_value=workers_mock)

    units_obj = MagicMock()
    units_obj.ready = dts
    units_call_result = MagicMock(return_value=units_obj)

    enemy_main = SimpleNamespace(x=100.0, y=100.0)

    return SimpleNamespace(
        units=units_call_result,
        enemy_units=workers_mock,
        enemy_structures=lambda _t: [],
        enemy_start_locations=[enemy_main],
        time=10.0,
    )


@pytest.mark.asyncio
async def test_execute_new_dt_starts_as_raid_and_records_hp():
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    dt = _dt(tag=7, health=40, shield=80, pos=(10.0, 10.0))
    a.ai = _make_ai_for_execute([dt])
    await a.execute()
    assert a._dt_state[7] == "raid"
    assert a._dt_last_hp[7] == 120.0
    # 应当下了 attack 命令(距 enemy_main 远 → attack-move)
    dt.attack.assert_called_once()


@pytest.mark.asyncio
async def test_execute_hp_drop_triggers_release():
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a._dt_state[7] = "raid"
    a._dt_last_hp[7] = 120.0  # 上 tick 满血
    dt = _dt(tag=7, health=10, shield=0, pos=(10.0, 10.0))  # 这 tick 受伤
    a.ai = _make_ai_for_execute([dt])
    await a.execute()
    assert a._dt_state[7] == "released"


@pytest.mark.asyncio
async def test_execute_released_dt_no_command():
    """released 状态 DT:本 act 不下指令。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a._dt_state[7] = "released"
    a._dt_last_hp[7] = 120.0
    dt = _dt(tag=7, health=40, shield=80)
    a.ai = _make_ai_for_execute([dt])
    await a.execute()
    dt.attack.assert_not_called()


@pytest.mark.asyncio
async def test_execute_independent_dt_state():
    """两个 DT 独立判定:一个被打转 released,另一个仍 raid。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a._dt_state[7] = "raid"
    a._dt_state[8] = "raid"
    a._dt_last_hp[7] = 120.0
    a._dt_last_hp[8] = 120.0
    dt7 = _dt(tag=7, health=10, shield=0, pos=(10.0, 10.0))  # 受伤
    dt8 = _dt(tag=8, health=40, shield=80, pos=(11.0, 11.0))  # 满血
    a.ai = _make_ai_for_execute([dt7, dt8])
    await a.execute()
    assert a._dt_state[7] == "released"
    assert a._dt_state[8] == "raid"  # 用户决策:独立判定,8 没受伤继续 raid
    dt8.attack.assert_called_once()


@pytest.mark.asyncio
async def test_execute_cleans_dead_dt_state():
    """已死亡 DT(不在 ai.units 里)的 state 字典被清理。"""
    a = _act()
    a.knowledge = SimpleNamespace(roles=MagicMock())
    a._dt_state[99] = "raid"
    a._dt_last_hp[99] = 120.0
    # ai.units 只返回 DT 7
    dt = _dt(tag=7, health=40, shield=80)
    a.ai = _make_ai_for_execute([dt])
    await a.execute()
    assert 99 not in a._dt_state
    assert 99 not in a._dt_last_hp
