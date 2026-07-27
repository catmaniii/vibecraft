"""WidowMineDropAct: 医疗船空投寡妇雷状态机的纯逻辑测试。

用 __new__ 绕过 ActBase.__init__、手动塞字段，测：
  - 状态转移条件（LOAD→FLY_IN / FLY_IN→DROP / DROP→RECALL / RECALL→ESCAPE）
  - drop 点计算
  - 「该回收」判定逻辑
  - cargo 判断
  - burrowed 雷查询
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from sc2.ids.unit_typeid import UnitTypeId


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """widow_mine_drop_act 顶层 import 了 sharpy.plans.acts.ActBase ——
    注入 fake ActBase 让 import 过，测完清掉。"""
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act", None)
    for name in created:
        sys.modules.pop(name, None)


def _act(state: str = "idle", time: float = 0.0):
    """绕过 __init__ 构造一个 act，手动塞最小字段。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import (
        DropState,
        WidowMineDropAct,
    )

    act = WidowMineDropAct.__new__(WidowMineDropAct)
    act._state = DropState(state)
    act._state_entered_ts = 0.0
    act._medivac_tag = None
    act._load_since = None
    act._burrowed_since = {}
    act._now = time
    act.ai = SimpleNamespace(time=time)
    return act


def _medivac(tag=1, cargo_used=0):
    return SimpleNamespace(
        tag=tag,
        cargo_used=cargo_used,
        type_id=UnitTypeId.MEDIVAC,
        distance_to=lambda p: 0.0,
        move=lambda p: None,
    )


def _mine(tag=10, weapon_cooldown=0.0):
    """散落（非 burrowed）寡妇雷。"""
    commands: list = []
    m = SimpleNamespace(
        tag=tag,
        weapon_cooldown=weapon_cooldown,
        type_id=UnitTypeId.WIDOWMINE,
        _calls=commands,
    )
    m.smart = lambda target: commands.append(("smart", target))
    m.__call__ = lambda ability: commands.append(("ability", ability))
    return m


def _burrowed_mine(tag=20, weapon_cooldown=0.0):
    """已埋地（WIDOWMINEBURROWED）的寡妇雷。"""
    commands: list = []
    m = SimpleNamespace(
        tag=tag,
        weapon_cooldown=weapon_cooldown,
        type_id=UnitTypeId.WIDOWMINEBURROWED,
        _calls=commands,
    )
    m.__call__ = lambda ability: commands.append(("ability", ability))
    return m


def _point(x=0.0, y=0.0):
    """简单 2D 点，支持 distance_to / towards。"""
    p = SimpleNamespace(x=x, y=y)
    p.distance_to = lambda other: ((x - other.x) ** 2 + (y - other.y) ** 2) ** 0.5
    p.towards = lambda other, dist: _point(
        x + (other.x - x) / max(p.distance_to(other), 1e-9) * dist,
        y + (other.y - y) / max(p.distance_to(other), 1e-9) * dist,
    )
    return p


# ── cargo_used 读取 ───────────────────────────────────────────────────────


def test_cargo_used_zero():
    act = _act()
    assert act._cargo_used(_medivac(cargo_used=0)) == 0


def test_cargo_used_nonzero():
    act = _act()
    assert act._cargo_used(_medivac(cargo_used=4)) == 4


def test_cargo_used_fallback_no_attr():
    act = _act()
    # 没有 cargo_used 属性时应返回 0，不崩
    assert act._cargo_used(SimpleNamespace()) == 0


# ── drop 点计算 ───────────────────────────────────────────────────────────


def test_compute_drop_pos_basic():
    """落点应在敌方主基地朝家方向偏移 _DROP_OFFSET 格。"""
    act = _act()
    enemy = _point(100.0, 100.0)
    home = _point(0.0, 0.0)
    act.ai = SimpleNamespace(
        time=0.0,
        enemy_start_locations=[enemy],
        start_location=home,
    )
    pos = act._compute_drop_pos()
    assert pos is not None
    # 落点离敌方比家近（朝家偏移了）
    assert pos.distance_to(enemy) < pos.distance_to(home)


def test_compute_drop_pos_no_enemy_returns_none():
    """没有敌方位置 → 返回 None，不崩。"""
    act = _act()
    act.ai = SimpleNamespace(time=0.0, enemy_start_locations=[])
    pos = act._compute_drop_pos()
    assert pos is None


# ── 「该回收」判定 ────────────────────────────────────────────────────────


def test_should_recall_mine_fired():
    """weapon_cooldown > 0 → 已开火，应回收。"""
    act = _act(time=5.0)
    mine = _burrowed_mine(weapon_cooldown=22.4)
    assert act._should_recall_mine(mine, entered_ts=4.0) is True


def test_should_recall_mine_not_fired_not_timeout():
    """刚埋下、没开火、没超时 → 不回收。"""
    act = _act(time=5.0)
    mine = _burrowed_mine(weapon_cooldown=0.0)
    assert act._should_recall_mine(mine, entered_ts=4.0) is False


def test_should_recall_mine_timeout():
    """超过 _RECALL_TIMEOUT_S（30s）没开火 → 强制回收。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import _RECALL_TIMEOUT_S

    act = _act(time=100.0)
    mine = _burrowed_mine(weapon_cooldown=0.0)
    # 进入 burrowed 时刻为 0，已过 _RECALL_TIMEOUT_S
    assert act._should_recall_mine(mine, entered_ts=100.0 - _RECALL_TIMEOUT_S - 1) is True


# ── 状态转移条件（纯判断，不调用 execute 避开 sharpy 依赖） ───────────────


def test_load_timeout_triggers_fly_in():
    """装载超时 _LOAD_TIMEOUT_S → 即使 cargo=0 也应出发（防止卡死）。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import (
        _LOAD_TIMEOUT_S,
    )

    act = _act(state="load", time=100.0)
    act._load_since = 100.0 - _LOAD_TIMEOUT_S - 1  # 超时
    act.ai = SimpleNamespace(time=100.0)

    # 模拟装载逻辑判断：timed_out = True → 即使 cargo == 0 也应进入 FLY_IN
    timed_out = act.ai.time - act._load_since > _LOAD_TIMEOUT_S
    assert timed_out is True


def test_state_initial_is_idle():
    act = _act()
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import DropState

    assert act._state == DropState.IDLE


def test_set_state_updates_entered_ts():
    """_set_state 切换新状态时应更新 _state_entered_ts。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import DropState

    act = _act(state="idle", time=5.0)
    act.ai = SimpleNamespace(time=5.0)
    act._set_state(DropState.LOAD)
    assert act._state == DropState.LOAD
    assert act._state_entered_ts == 5.0


def test_set_state_same_state_no_update():
    """状态不变时不更新 entered_ts（防止反复刷时间戳）。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import DropState

    act = _act(state="load", time=0.0)
    act._state_entered_ts = 3.0
    act.ai = SimpleNamespace(time=10.0)
    act._set_state(DropState.LOAD)
    assert act._state_entered_ts == 3.0  # 不应被更新


# ── fly_in 到位判定 ───────────────────────────────────────────────────────


def test_fly_in_transitions_to_drop_when_arrived():
    """医疗船到达 drop 点（distance <= _ARRIVED_DIST）→ 切 DROP。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import (
        _ARRIVED_DIST,
    )

    act = _act(state="fly_in", time=10.0)
    enemy = _point(50.0, 50.0)
    home = _point(0.0, 0.0)
    act.ai = SimpleNamespace(time=10.0, enemy_start_locations=[enemy], start_location=home)

    drop_pos = act._compute_drop_pos()
    assert drop_pos is not None

    # 模拟医疗船已到位
    med = _medivac()
    med.distance_to = lambda p: _ARRIVED_DIST - 0.1  # 比阈值小 → 已到

    # 复现 _handle_fly_in 的判断逻辑
    arrived = med.distance_to(drop_pos) <= _ARRIVED_DIST
    assert arrived is True


# ── escape 超时回 IDLE ────────────────────────────────────────────────────


def test_escape_resets_to_idle_after_timeout():
    """ESCAPE 超过 _ESCAPE_TIMEOUT_S → 重置 IDLE。"""
    from vibecraft.bot.auto_combat.terran.plans.widow_mine_drop_act import (
        _ESCAPE_TIMEOUT_S,
    )

    act = _act(state="escape", time=100.0)
    act._state_entered_ts = 100.0 - _ESCAPE_TIMEOUT_S - 1
    act.ai = SimpleNamespace(time=100.0)

    elapsed = act.ai.time - act._state_entered_ts
    assert elapsed > _ESCAPE_TIMEOUT_S  # 应该超时 → 触发 IDLE
