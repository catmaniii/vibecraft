"""HarassWorkerLineAct: 通用骚扰微操 act 的纯逻辑测试。

ActBase 子类直接构造要 sharpy 环境,这里用 __new__ 绕开 __init__、手动塞字段,
只测撤退判定 / 血量比例 / 升级 gate / 威胁识别 / hit-and-run 选目标这几段逻辑。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """harass_act 顶层 import 了 sharpy.plans.acts.ActBase —— sharpy 是 vendored、
    单测环境不可 import。注入一个 fake ActBase 让 import 过,测完清掉。"""
    created = []
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.harass_act", None)
    for name in created:
        sys.modules.pop(name, None)


def _act(
    bail_hp_ratio: float = 0.25,
    recover_hp_ratio: float = 0.55,
    wait_upgrade=None,
    release_after=None,
):
    """绕过 __init__ 构造一个 act,手动塞字段(lazy import:fixture 先注入 fake sharpy)。"""
    from vibecraft.bot.auto_combat.harass_act import HarassWorkerLineAct

    act = HarassWorkerLineAct.__new__(HarassWorkerLineAct)
    act._types = frozenset({UnitTypeId.BANSHEE})
    act._bail_hp = bail_hp_ratio
    act._recover_hp = recover_hp_ratio
    act._wait_upgrade = wait_upgrade
    act._release_after = release_after
    act._bailing = set()
    return act


def _unit(tag=1, health=100, health_max=100, shield=0, shield_max=0):
    return SimpleNamespace(
        tag=tag,
        health=health,
        health_max=health_max,
        shield=shield,
        shield_max=shield_max,
    )


def _threat(dist, can_ground=True, can_air=False, pos=(0.0, 0.0)):
    """造一个 fake 威胁单位:固定 distance_to + 对空/对地能力 + position。"""
    return SimpleNamespace(
        can_attack_ground=can_ground,
        can_attack_air=can_air,
        position=SimpleNamespace(x=pos[0], y=pos[1]),
        distance_to=lambda _u, _d=dist: _d,
    )


# --- 血量比例 ----------------------------------------------------------


def test_hp_ratio_full():
    assert _act()._hp_ratio(_unit(health=140, health_max=140)) == 1.0


def test_hp_ratio_half_with_shield():
    """HP 满 + 护盾空(满值含护盾)→ 比例 0.5。"""
    u = _unit(health=80, health_max=80, shield=0, shield_max=80)
    assert _act()._hp_ratio(u) == 0.5


def test_hp_ratio_zero_max_is_safe():
    """满值为 0(取数据失败)→ 按满血处理,不崩、不误撤。"""
    assert _act()._hp_ratio(_unit(health=0, health_max=0)) == 1.0


# --- 撤退(bail)判定 --------------------------------------------------


def test_should_bail_critical_hp():
    act = _act(bail_hp_ratio=0.25)
    assert act._should_bail(_unit(tag=7, health=20, health_max=140)) is True
    assert 7 in act._bailing


def test_should_bail_healthy_unit_fights():
    """血量正常 → 不 bail(留在战区 hit-and-run,不回家)。"""
    act = _act(bail_hp_ratio=0.25)
    assert act._should_bail(_unit(tag=7, health=100, health_max=140)) is False


def test_bail_hysteresis_holds_until_recovered():
    """已 bail 的单位:血量没回到 _BAIL_RECOVER(0.55)之前持续 bail,防抖。"""
    act = _act(bail_hp_ratio=0.25)
    assert act._should_bail(_unit(tag=7, health=20, health_max=140)) is True
    # 回到 0.3(过了 bail 阈值 0.25 但没到 0.55)→ 仍 bail
    assert act._should_bail(_unit(tag=7, health=42, health_max=140)) is True
    # 回到 0.55 以上 → 解除 bail、重新出击
    assert act._should_bail(_unit(tag=7, health=90, health_max=140)) is False
    assert 7 not in act._bailing


# --- 升级 gate ---------------------------------------------------------


def test_upgrade_pending_no_gate():
    assert _act(wait_upgrade=None)._upgrade_pending() is False


def test_upgrade_pending_gate_not_researched():
    act = _act(wait_upgrade=UpgradeId.BANSHEECLOAK)
    act.ai = SimpleNamespace(state=SimpleNamespace(upgrades=set()))
    assert act._upgrade_pending() is True


def test_upgrade_pending_gate_researched():
    act = _act(wait_upgrade=UpgradeId.BANSHEECLOAK)
    act.ai = SimpleNamespace(state=SimpleNamespace(upgrades={UpgradeId.BANSHEECLOAK}))
    assert act._upgrade_pending() is False


# --- 威胁识别(按空/地匹配)-------------------------------------------


def test_is_threat_air_unit_only_fears_anti_air():
    """飞行骚扰单位:只有能对空的敌人才算威胁。"""
    act = _act()
    flyer = SimpleNamespace(is_flying=True)
    assert act._is_threat_to(_threat(5, can_ground=True, can_air=False), flyer) is False
    assert act._is_threat_to(_threat(5, can_ground=False, can_air=True), flyer) is True


def test_is_threat_ground_unit_only_fears_anti_ground():
    act = _act()
    walker = SimpleNamespace(is_flying=False)
    assert act._is_threat_to(_threat(5, can_ground=True, can_air=False), walker) is True
    assert act._is_threat_to(_threat(5, can_ground=False, can_air=True), walker) is False


def test_nearest_threat_empty():
    pos, d = _act()._nearest_threat(SimpleNamespace(is_flying=False), [])
    assert pos is None and d == 1e9


def test_nearest_threat_picks_closest_relevant():
    """跳过打不到自己的威胁,取能打到的里最近的。"""
    act = _act()
    walker = SimpleNamespace(is_flying=False)
    threats = [
        _threat(20, can_ground=True),
        _threat(3, can_ground=False, can_air=True),  # 对空 → 对地面单位无威胁,跳过
        _threat(6, can_ground=True),
    ]
    _pos, d = act._nearest_threat(walker, threats)
    assert d == 6


# --- hit-and-run 选目标 ------------------------------------------------


def test_worker_in_range_found():
    act = _act()
    w1, w2 = SimpleNamespace(tag=1), SimpleNamespace(tag=2)
    unit = SimpleNamespace(target_in_range=lambda w: w is w2)
    assert act._worker_in_range(unit, [w1, w2]) is w2


def test_worker_in_range_none_when_no_workers():
    assert _act()._worker_in_range(SimpleNamespace(), None) is None


def test_micro_attacks_in_range_worker_when_at_mineral_line():
    """已到矿区(距主基地近)+ 无威胁 + 射程内有农民 → 直接打那个农民。"""
    act = _act()
    calls: list = []
    worker = SimpleNamespace(tag=9)
    main = SimpleNamespace(x=1, y=1)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=0.0,
        distance_to=lambda t: 5.0,  # 已到矿区
        target_in_range=lambda w: True,
        attack=lambda t: calls.append(("attack", t)),
        move=lambda t: calls.append(("move", t)),
    )
    act._micro(unit, [worker], main, [])
    assert calls == [("attack", worker)]


def test_micro_kites_when_threatened_and_weapon_cooling():
    """已到矿区 + 武器冷却 + 威胁逼近 → 后撤离开威胁。"""
    act = _act()
    calls: list = []
    pos = SimpleNamespace(x=10.0, y=10.0)
    pos.towards = lambda p, dist: ("kited", p, dist)
    threat = _threat(5, can_ground=True, pos=(20.0, 20.0))
    main = SimpleNamespace(x=12, y=12)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=8.0,
        position=pos,
        distance_to=lambda t: 5.0,  # 已到矿区
        move=lambda t: calls.append(("move", t)),
        attack=lambda t: calls.append(("attack", t)),
    )
    act._micro(unit, [SimpleNamespace(tag=1)], main, [threat])
    assert calls and calls[0][0] == "move"


def test_micro_kites_from_workers_when_cooling():
    """2026-06-17 用户：死神 farm 农民时，农民 A 过来也要躲 —— 武器冷却 + 农民贴近
    (无任何战斗单位)→ 仍后撤保持距离，不站着被农民围死。"""
    act = _act()
    calls: list = []
    pos = SimpleNamespace(x=10.0, y=10.0)
    pos.towards = lambda p, dist: ("kited", p, dist)
    # 农民:能对地(can_attack_ground)+ 有 distance_to + position,贴近(d=4)
    worker = SimpleNamespace(
        tag=7,
        can_attack_ground=True,
        can_attack_air=False,
        position=SimpleNamespace(x=13.0, y=13.0),
        distance_to=lambda _u, _d=4.0: _d,
    )
    main = SimpleNamespace(x=12, y=12)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=8.0,
        position=pos,  # 冷却中
        health=100,
        health_max=100,
        shield=0,
        shield_max=0,  # 满血也要躲
        distance_to=lambda t: 5.0,  # 已到矿区
        target_in_range=lambda w: True,
        move=lambda t: calls.append(("move", t)),
        attack=lambda t: calls.append(("attack", t)),
    )
    # threats 为空(没战斗单位),只有农民 → 仍应 kite
    act._micro(unit, [worker], main, [])
    assert calls and calls[0][0] == "move"


def test_micro_kites_when_hurt_even_if_weapon_ready():
    """2026-06-17 用户「撤退不够及时易死」：已到矿区 + 武器**好了** + 但**受伤**
    (血 < recover_hp) + 威胁逼近 → 仍后撤离开威胁，不站着跟战斗单位换血。"""
    act = _act(bail_hp_ratio=0.25, recover_hp_ratio=0.95)
    calls: list = []
    pos = SimpleNamespace(x=10.0, y=10.0)
    pos.towards = lambda p, dist: ("kited", p, dist)
    threat = _threat(5, can_ground=True, pos=(20.0, 20.0))
    main = SimpleNamespace(x=12, y=12)
    worker = SimpleNamespace(tag=9)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=0.0,
        position=pos,  # 武器好了
        health=40,
        health_max=100,
        shield=0,
        shield_max=0,  # 受伤 hp=0.4 < recover 0.95
        distance_to=lambda t: 5.0,  # 已到矿区
        target_in_range=lambda w: True,
        move=lambda t: calls.append(("move", t)),
        attack=lambda t: calls.append(("attack", t)),
    )
    act._micro(unit, [worker], main, [threat])
    # 受伤 → kite 后撤（move），不是 attack 农民
    assert calls and calls[0][0] == "move"


def test_micro_healthy_unit_farms_worker_despite_nearby_threat():
    """满血 + 武器好了 + 威胁逼近 + 射程内有农民 → 仍贴脸 farm 农民（不过度自保）。"""
    act = _act(bail_hp_ratio=0.25, recover_hp_ratio=0.95)
    calls: list = []
    pos = SimpleNamespace(x=10.0, y=10.0)
    pos.towards = lambda p, dist: ("kited", p, dist)
    threat = _threat(5, can_ground=True, pos=(20.0, 20.0))
    main = SimpleNamespace(x=12, y=12)
    worker = SimpleNamespace(tag=9)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=0.0,
        position=pos,
        health=100,
        health_max=100,
        shield=0,
        shield_max=0,  # 满血 hp=1.0 >= recover
        distance_to=lambda t: 5.0,
        target_in_range=lambda w: True,
        move=lambda t: calls.append(("move", t)),
        attack=lambda t: calls.append(("attack", t)),
    )
    act._micro(unit, [worker], main, [threat])
    assert calls == [("attack", worker)]


def test_micro_no_kite_during_approach():
    """进场途中(离矿区远)遇威胁、即便有农民视野也不 kite —— 直推。"""
    act = _act()
    calls: list = []
    threat = _threat(3, can_ground=True, pos=(20.0, 20.0))
    main = SimpleNamespace(x=50, y=50)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=8.0,
        distance_to=lambda t: 80.0,  # 离矿区还远
        move=lambda t: calls.append(t),
        attack=lambda t: calls.append(("attack", t)),
    )
    act._micro(unit, [SimpleNamespace(tag=1)], main, [threat])
    assert calls == [main]  # 远 → 直推,不被沿途威胁/农民带偏


def test_micro_approaches_enemy_main_when_far():
    """离对方矿区远 → move 向对方主基地。"""
    act = _act()
    calls: list = []
    main = SimpleNamespace(x=50, y=50)
    unit = SimpleNamespace(
        is_flying=False,
        weapon_cooldown=0.0,
        distance_to=lambda t: 80.0,
        move=lambda t: calls.append(t),
        attack=lambda t: calls.append(("attack", t)),
    )
    act._micro(unit, None, main, [])
    assert calls == [main]


# --- kite 步长按单位射程自适应(根因 F82)-------------------------------


def test_kite_params_short_range_flyer_step_within_range():
    """射程 3 的飞龙:kite 后撤步长必须 < 射程 3,否则退出自己射程够不着农民。"""
    from vibecraft.bot.auto_combat.harass_act import _pm_kite_params

    muta = SimpleNamespace(is_flying=True, air_range=3.0, ground_range=3.0)
    trigger, step = _pm_kite_params(muta)
    assert step < 3.0  # 关键:不退出射程 3
    assert trigger >= 3.0


def test_kite_params_reaper_close_to_original():
    """射程 5 的死神:trigger/step 接近原来的 11/5 量级(行为不大变,向后兼容)。"""
    from vibecraft.bot.auto_combat.harass_act import _pm_kite_params

    reaper = SimpleNamespace(is_flying=False, ground_range=5.0, air_range=0.0)
    trigger, step = _pm_kite_params(reaper)
    assert 8.0 <= trigger <= 12.0
    assert 3.5 <= step <= 5.0


def test_kite_params_fallback_when_no_range():
    """取不到有效射程(如女妖 is_flying 但 air_range=0)→ fallback 原 _KITE_TRIGGER/_KITE_STEP。"""
    from vibecraft.bot.auto_combat.harass_act import (
        _KITE_STEP,
        _KITE_TRIGGER,
        _pm_kite_params,
    )

    banshee = SimpleNamespace(is_flying=True, air_range=0.0, ground_range=6.0)
    assert _pm_kite_params(banshee) == (_KITE_TRIGGER, _KITE_STEP)


def test_micro_short_range_flyer_kite_step_stays_in_range():
    """飞龙(射程3)已到矿区 + 武器冷却 + 对空威胁逼近 → kite 后撤,且步长 < 射程 3。"""
    act = _act()
    captured: dict = {}
    pos = SimpleNamespace(x=10.0, y=10.0)

    def _towards(p, dist):
        captured["dist"] = dist
        return ("kited", p, dist)

    pos.towards = _towards
    # 对空威胁(如女王):只能对空、逼近 d=4。
    threat = SimpleNamespace(
        can_attack_ground=False,
        can_attack_air=True,
        position=SimpleNamespace(x=14.0, y=14.0),
        distance_to=lambda _u, _d=4.0: _d,
    )
    main = SimpleNamespace(x=12, y=12)
    unit = SimpleNamespace(
        is_flying=True,
        air_range=3.0,
        ground_range=3.0,
        weapon_cooldown=8.0,
        position=pos,  # 冷却中
        health=100,
        health_max=100,
        shield=0,
        shield_max=0,
        distance_to=lambda t: 5.0,  # 已到矿区
        move=lambda t: captured.setdefault("moved", t),
        attack=lambda t: captured.setdefault("attacked", t),
    )
    act._micro(unit, None, main, [threat])
    assert "moved" in captured  # kite 触发(move)
    assert abs(captured["dist"]) < 3.0  # 后撤步长 < 射程 3,不退出自己射程
