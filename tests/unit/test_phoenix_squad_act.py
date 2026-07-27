"""PhoenixSquadAct 单测。

用 __new__ 绕开 __init__（避免真实 sharpy 依赖），手动塞字段。
sc2 在 vendor 中可用，直接 import。只 fake sharpy.plans.acts.ActBase。
参考 tests/unit/test_phoenix_harass.py 风格。
"""

from __future__ import annotations

import math
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

# ---------------------------------------------------------------------------
# fake sharpy
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """注入 fake sharpy.plans.acts.ActBase，让 import 过，测完清掉。"""
    created = []

    def _ensure(name: str) -> ModuleType:
        if name not in sys.modules:
            m = ModuleType(name)
            sys.modules[name] = m
            created.append(name)
        return sys.modules[name]

    # sharpy
    _ensure("sharpy")
    _ensure("sharpy.plans")
    acts = _ensure("sharpy.plans.acts")
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})

    _ensure("sharpy.managers")
    _ensure("sharpy.managers.core")
    roles_core = _ensure("sharpy.managers.core.roles")
    if not hasattr(roles_core, "UnitTask"):
        roles_core.UnitTask = type("UnitTask", (), {"Reserved": "Reserved"})

    yield

    # 清 cache，下次 import 重新 load
    for mod in [
        "vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act",
        "vibecraft.bot.auto_combat.protoss.phoenix_squad_micro",
    ]:
        sys.modules.pop(mod, None)

    for name in created:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


def _act(
    release_after: float | None = None,
    bail_hp_ratio: float = 0.3,
    recover_hp_ratio: float = 0.6,
    wave_threshold: int = 5,
    harass_duration: float = 300.0,
    recall_threshold: int = 0,
    recall_radius: float = 30.0,
):
    """绕过 __init__ 构造 PhoenixSquadAct，手动塞字段。"""
    from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import PhoenixSquadMicro
    from vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act import PhoenixSquadAct

    act = PhoenixSquadAct.__new__(PhoenixSquadAct)
    act._release_after = release_after
    act._wave_threshold = int(wave_threshold)
    act._harass_duration = float(harass_duration)
    act._recall_threshold = int(recall_threshold)
    act._recall_radius = float(recall_radius)
    act._wave_launched = False
    act._harass_notified = False
    act._harass_deadline = None
    act._regrouping = False
    act._regroup_pt = None
    act._dodge_until = 0.0
    act._highground_cells = None
    act._approach_router = "snap"
    act._lift_defend = False
    act._last_target_reason = "?"
    act._home_rally = None
    act._rally_move_at = {}
    act._micro = PhoenixSquadMicro(
        bail_hp_ratio=bail_hp_ratio,
        recover_hp_ratio=recover_hp_ratio,
    )
    return act


def _knowledge(harass_active: bool = True):
    """构造 knowledge mock（含 vibecraft.phoenix_harass_active + roles）。"""
    roles = MagicMock()
    vibecraft = SimpleNamespace(phoenix_harass_active=harass_active)
    return SimpleNamespace(roles=roles, vibecraft=vibecraft)


class _FakeUnits(list):
    """最小 Units mock，支持 .amount / .filter() / .center / .closest_to()。"""

    @property
    def amount(self):
        return len(self)

    def filter(self, fn):
        return _FakeUnits([u for u in self if fn(u)])

    @property
    def center(self):
        if not self:
            return Point2((0.0, 0.0))
        return Point2(
            (
                sum(u.position.x for u in self) / len(self),
                sum(u.position.y for u in self) / len(self),
            )
        )

    def closest_to(self, target):
        if not self:
            return None
        ox = target.x if hasattr(target, "x") else target.position.x
        oy = target.y if hasattr(target, "y") else target.position.y
        return min(self, key=lambda u: math.hypot(u.position.x - ox, u.position.y - oy))

    @property
    def flying(self):
        return _FakeUnits([u for u in self if getattr(u, "is_flying", False)])

    def closer_than(self, dist, ref):
        ox = ref.position.x if hasattr(ref, "position") else ref.x
        oy = ref.position.y if hasattr(ref, "position") else ref.y
        return _FakeUnits(
            [u for u in self if math.hypot(u.position.x - ox, u.position.y - oy) < dist]
        )

    def __bool__(self):
        return len(self) > 0


def _make_phoenix(
    tag=1, pos=(50.0, 50.0), health=100, health_max=100, shield=60, shield_max=60, energy=30.0
):
    position = Point2((float(pos[0]), float(pos[1])))

    def _dist(other):
        ox = other.x if hasattr(other, "x") else other.position.x
        oy = other.y if hasattr(other, "y") else other.position.y
        return math.hypot(position.x - ox, position.y - oy)

    u = MagicMock()
    u.tag = tag
    u.position = position
    u.health = health
    u.health_max = health_max
    u.shield = shield
    u.shield_max = shield_max
    u.energy = energy
    u.is_ready = True
    u.air_range = 5.0
    u.is_flying = False
    u.is_structure = False
    u.distance_to = _dist
    return u


def _make_ai(
    n_phoenixes=0,
    ai_time=120.0,
    start_loc=(10.0, 10.0),
    enemy_main=(150.0, 150.0),
    stargate=None,
):
    """构造 ai mock，units(PHOENIX).ready 返回 n_phoenixes 只凤凰。

    `stargate=(x, y)` 时 `ai.structures(...)` 返回一座星门（未 launch 的集结点取它）；
    默认 None = 没有星门（集结点兜底 start_location）。ai 是 MagicMock，不显式建模的话
    `ai.structures(...)` 会返回真值 mock，集结点会解析成一个 MagicMock。
    """
    phoenixes = _FakeUnits([_make_phoenix(tag=i, pos=(50.0, 50.0)) for i in range(n_phoenixes)])
    ai = MagicMock()
    ai.units = MagicMock(return_value=SimpleNamespace(ready=phoenixes))
    ai.time = ai_time
    ai.start_location = Point2(start_loc)
    ai.enemy_start_locations = [Point2(enemy_main)]
    ai.enemy_units = _FakeUnits()
    gates = _FakeUnits([_make_phoenix(tag=9000, pos=stargate)] if stargate else [])
    ai.structures = MagicMock(return_value=SimpleNamespace(ready=gates))
    return ai


# ---------------------------------------------------------------------------
# wave gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wave_gating_below_threshold():
    """4 凤凰(threshold=5) → wave_launched=False；没有星门时集结点兜底 start_location。"""
    act = _act(wave_threshold=5)
    ai = _make_ai(n_phoenixes=4)
    act.ai = ai
    act.knowledge = _knowledge()

    result = await act.execute()
    assert result is True
    assert act._wave_launched is False
    # 每只凤凰应 move 到 start_location
    phoenixes = ai.units.return_value.ready
    for u in phoenixes:
        u.move.assert_called_with(ai.start_location)


@pytest.mark.asyncio
async def test_wave_gating_above_threshold():
    """5 凤凰 → wave_launched=True（latch）；再下 tick 6 凤凰仍 launched。"""
    act = _act(wave_threshold=5)
    ai = _make_ai(n_phoenixes=5)
    act.ai = ai
    act.knowledge = _knowledge()

    await act.execute()
    assert act._wave_launched is True

    # 再次调用（模拟第 2 tick，6 只凤凰）
    ai2 = _make_ai(n_phoenixes=6)
    ai2.director = ai.director  # 同一 director（director 是 _make_ai 里 MagicMock）
    act.ai = ai2
    await act.execute()
    assert act._wave_launched is True  # latch 保持


@pytest.mark.asyncio
async def test_release_after():
    """ai.time>=release_after → 进入 lift-defend(归队后抬地防守),不再 release 归 sharpy 退却。"""
    act = _act(release_after=200.0)
    act._wave_launched = True
    ai = _make_ai(n_phoenixes=5, ai_time=300.0)
    act.ai = ai
    act.knowledge = _knowledge()

    result = await act.execute()
    assert result is True
    assert act._lift_defend is True  # 进入抬地防守 latch
    act.knowledge.roles.clear_task.assert_not_called()  # 不释放归 sharpy
    assert act.knowledge.roles.set_task.called  # 保留 Reserved 控制


def _enemy(tag, pos, type_id=UnitTypeId.STALKER, is_structure=False):
    """构造一个敌方单位 mock（有 position / type_id / is_structure / distance_to）。"""
    p = Point2((float(pos[0]), float(pos[1])))

    def _dist(other, _p=p):
        ox = other.x if hasattr(other, "x") else other.position.x
        oy = other.y if hasattr(other, "y") else other.position.y
        return math.hypot(_p.x - ox, _p.y - oy)

    return SimpleNamespace(
        tag=tag, position=p, type_id=type_id, is_structure=is_structure, distance_to=_dist
    )


@pytest.mark.asyncio
async def test_recall_on_home_threat_enters_lift_defend():
    """敌方 6 战斗单位逼近我方基地 → recall 触发 → **临时** lift-defend 防守(抬敌方地面单位),
    不 release 归 sharpy；但**不永久 latch**(敌退了恢复骚扰，评审①③)。"""
    act = _act(recall_threshold=6, recall_radius=30.0)
    act._wave_launched = True
    ai = _make_ai(n_phoenixes=5, ai_time=120.0)
    ai.townhalls = _FakeUnits([Point2((50.0, 50.0))])
    ai.enemy_units = _FakeUnits([_enemy(800 + i, (52.0, 50.0)) for i in range(6)])
    act.ai = ai
    act.knowledge = _knowledge()

    result = await act.execute()
    assert result is True
    assert act._lift_defend is False  # recall 临时,不永久 latch
    act.knowledge.roles.clear_task.assert_not_called()  # 不释放,保留控制抬地
    assert act.knowledge.roles.set_task.called  # 保留 Reserved


@pytest.mark.asyncio
async def test_recall_ignores_workers():
    """敌方 8 农民贴近基地(骚扰农民)不算大部队 → 不召回,继续骚扰。"""
    act = _act(recall_threshold=6, recall_radius=30.0)
    act._wave_launched = True
    ai = _make_ai(n_phoenixes=5, ai_time=120.0)
    ai.townhalls = _FakeUnits([Point2((50.0, 50.0))])
    ai.enemy_units = _FakeUnits(
        [_enemy(800 + i, (52.0, 50.0), type_id=UnitTypeId.PROBE) for i in range(8)]
    )
    act.ai = ai
    act.knowledge = _knowledge()

    result = await act.execute()
    assert result is True
    # 农民不触发召回 → 未 clear_task 释放（仍在骚扰,Reserved）
    assert not act.knowledge.roles.clear_task.called


def test_regroup_when_squad_below_floor():
    """兵力打散到 < _REGROUP_FLOOR → 进重整(reason=regroup),退同一个安全集结点(不回家)。"""
    from vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act import _REGROUP_FLOOR

    act = _act()
    ai = MagicMock()
    ai.enemy_start_locations = [Point2((150.0, 150.0))]
    ai.start_location = Point2((10.0, 10.0))
    act.ai = ai
    # 打散到 < floor → 重整
    phoenixes = _FakeUnits([_make_phoenix(tag=i) for i in range(_REGROUP_FLOOR - 1)])
    anchor, approach = act._harass_anchor_and_approach(phoenixes, 100.0)
    assert act._regrouping is True
    assert act._last_target_reason == "regroup"
    assert anchor is approach  # 退同一个安全集结点(不回家)


def test_regroup_hysteresis_resumes_at_resume_count():
    """重整中,凤凰攒到 >= _REGROUP_RESUME 才恢复出击(滞回)。"""
    from vibecraft.bot.auto_combat.protoss.plans.phoenix_squad_act import _REGROUP_RESUME

    act = _act()
    ai = MagicMock()
    ai.enemy_start_locations = [Point2((150.0, 150.0))]
    ai.start_location = Point2((10.0, 10.0))
    act.ai = ai
    act._regrouping = True
    # 攒到 resume 数 → 退出重整
    phoenixes = _FakeUnits([_make_phoenix(tag=i) for i in range(_REGROUP_RESUME)])
    act._harass_anchor_and_approach(phoenixes, 100.0)
    assert act._regrouping is False


@pytest.mark.asyncio
async def test_reserved():
    """harass active → 凤凰被 set_task(Reserved) assert called。"""
    act = _act(wave_threshold=5)
    act._wave_launched = True  # 跳过 wave gating
    ai = _make_ai(n_phoenixes=3)
    act.ai = ai
    act.knowledge = _knowledge(harass_active=True)

    await act.execute()

    # 每只凤凰都应被 set_task 调用一次
    assert act.knowledge.roles.set_task.call_count == 3


@pytest.mark.asyncio
async def test_no_phoenixes():
    """空 units → return True，不崩不报错。"""
    act = _act()
    act._wave_launched = True
    ai = _make_ai(n_phoenixes=0)
    act.ai = ai
    act.knowledge = _knowledge()

    result = await act.execute()
    assert result is True


# ---------------------------------------------------------------------------
# 骚扰持久指令卡：notify director + flag 控制归队
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_director_on_launch():
    """wave launch 那一刻 → 调 director.notify_phoenix_harass_started(start, deadline)。"""
    act = _act(wave_threshold=5, harass_duration=300.0)
    ai = _make_ai(n_phoenixes=5, ai_time=270.0)
    act.ai = ai
    act.knowledge = _knowledge()

    await act.execute()

    assert act._wave_launched is True
    assert act._harass_notified is True
    # director.notify_phoenix_harass_started 应被调用，参数 = (270, 570)
    ai.director.notify_phoenix_harass_started.assert_called_once_with(270.0, 570.0)


def _geom_act():
    """构造 act + mock 两个矿几何(rank0 behind=50,50 农民少;rank1 behind=20,20 农民多)。"""
    act = _act()
    geom0 = (Point2((50.0, 50.0)), Point2((55.0, 55.0)), Point2((52.0, 52.0)))
    geom1 = (Point2((20.0, 20.0)), Point2((25.0, 25.0)), Point2((22.0, 22.0)))
    zmap = {0: object(), 1: object()}
    gmap = {id(zmap[0]): geom0, id(zmap[1]): geom1}
    act._enemy_zone_by_rank = lambda r: zmap.get(r)  # type: ignore[method-assign]
    act._harass_geom = lambda z: gmap.get(id(z)) if z is not None else None  # type: ignore[method-assign]
    act._workers_near = lambda ml: 10 if ml.x < 30 else 1  # type: ignore[method-assign]
    act._aa_dps_near = lambda ml: 0.0  # type: ignore[method-assign]
    act._zone_rank = 0
    act._zone_since = 0.0
    return act, geom0, geom1


def test_pick_harass_geom_locks_until_arrived():
    """到达门(2026-07-20 trace 抓的 bug 修):squad 没抵达当前目标矿前锁死不切,别追移动靶。

    - squad 远离 rank0(没抵达)→ 即便 rank1 分更高也**锁死 rank0**(否则接近途中横跳)。
    - squad 抵达 rank0 + 停留够 → 才允许切到高分 rank1(正常穿梭)。
    """
    act, geom0, geom1 = _geom_act()
    act._aa_count_near = lambda ml: 0  # type: ignore[method-assign]  # 两矿都能打

    # squad 远离 rank0 behind(50,50)→ 锁死 rank0,不切高分 rank1
    far = Point2((150.0, 150.0))
    g, reason = act._pick_harass_geom(100.0, far, 5, 5)
    assert reason == "ok" and g is geom0

    # squad 抵达 rank0(距 behind=0)+ 停留够 → 切到高分 rank1
    near = Point2((50.0, 50.0))
    g2, reason2 = act._pick_harass_geom(200.0, near, 5, 5)
    assert reason2 == "ok" and g2 is geom1


def test_pick_harass_geom_all_defended_when_aa_over_cap():
    """要么打要么走(D89/D90):所有矿可抬对空都 >= 凤凰数(没富余) → 'all_defended'(退待命),不选矿。"""
    act, _geom0, _geom1 = _geom_act()
    # 两矿都 10 只可抬对空(女王),凤凰 5 只 → 10 >= 5,抬不完没富余 → 都打不过
    act._aa_split_near = lambda ml: (0, 10)  # type: ignore[method-assign]
    g, reason = act._pick_harass_geom(100.0, Point2((150.0, 150.0)), 5, 5)
    assert g is None and reason == "all_defended"


def test_pick_harass_geom_static_over_budget_all_defended():
    """静态防空 > 预算(3 座)的矿都算打不过(硬闯会损失,D90)→ all_defended。"""
    act, _geom0, _geom1 = _geom_act()
    act._aa_split_near = lambda ml: (3, 0)  # type: ignore[method-assign]
    g, reason = act._pick_harass_geom(100.0, Point2((150.0, 150.0)), 5, 5)
    assert g is None and reason == "all_defended"


def test_pick_harass_geom_skips_defended_picks_open():
    """一个矿设防打不过、另一个空虚 → 只选能打的那个(不硬去设防矿)。"""
    act, geom0, _geom1 = _geom_act()
    act._zone_rank = None  # 无当前锁定
    # rank1(ml.x=22<30)可抬对空 10 打不过;rank0(ml.x=52)对空 0 能打 → 选 rank0
    act._aa_split_near = lambda ml: (0, 10) if ml.x < 30 else (0, 0)  # type: ignore[method-assign]
    g, reason = act._pick_harass_geom(100.0, Point2((150.0, 150.0)), 5, 5)
    assert reason == "ok" and g is geom0  # 选能打的 rank0,不去设防的 rank1


@pytest.mark.asyncio
async def test_player_cancel_releases_phoenix():
    """玩家×卡片早收（harass_active=False 且 deadline 没到）→ **真 release 归还**给玩家/主力
    （评审⑥：尊重玩家显式收回意图，不 latch lift-defend）。"""
    act = _act(wave_threshold=5)
    act._wave_launched = True
    act._harass_deadline = 999.0  # deadline 远未到 → 属"玩家×"非"自动超时"
    ai = _make_ai(n_phoenixes=3, ai_time=100.0)
    act.ai = ai
    act.knowledge = _knowledge(harass_active=False)

    result = await act.execute()
    assert result is True
    assert act.knowledge.roles.clear_task.call_count == 3  # 真释放归还
    assert act._lift_defend is False  # 不 latch


@pytest.mark.asyncio
async def test_auto_deadline_enters_lift_defend():
    """自动超时（harass deadline 到点）→ latch lift-defend(抬地防守)，不 release 归 sharpy（评审①）。"""
    act = _act(wave_threshold=5)
    act._wave_launched = True
    act._harass_deadline = 50.0  # deadline 已过
    ai = _make_ai(n_phoenixes=3, ai_time=100.0)
    act.ai = ai
    act.knowledge = _knowledge(harass_active=False)

    result = await act.execute()
    assert result is True
    assert act._lift_defend is True  # latch
    act.knowledge.roles.clear_task.assert_not_called()  # 不释放


# ---------------------------------------------------------------------------
# 未 launch 的集结点（2026-07-27 用户:"凤凰的集结点也不要放到主基地上面,机场出来在哪
# 就在那个位置集结"）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prelaunch_rally_at_stargate_not_main_base():
    """有星门时,未 launch 的凤凰集结到**星门旁**,不是主基地。"""
    act = _act(wave_threshold=5)
    ai = _make_ai(n_phoenixes=3, start_loc=(10.0, 10.0), stargate=(40.0, 40.0))
    act.ai = ai
    act.knowledge = _knowledge()

    await act.execute()
    assert act._wave_launched is False
    for u in ai.units.return_value.ready:
        u.move.assert_called_with(Point2((40.0, 40.0)))


@pytest.mark.asyncio
async def test_prelaunch_does_not_reissue_move_every_frame():
    """集结 = 设个点,不是每帧拽:有指令在执行且冷却没到 → 不重发。"""
    act = _act(wave_threshold=5)
    ai = _make_ai(n_phoenixes=1, ai_time=100.0, stargate=(40.0, 40.0))
    act.ai = ai
    act.knowledge = _knowledge()
    u = ai.units.return_value.ready[0]
    u.orders = ["moving"]

    await act.execute()
    assert u.move.call_count == 1
    ai.time = 101.0  # 冷却(4s)内
    await act.execute()
    assert u.move.call_count == 1, "冷却内不该重发"


@pytest.mark.asyncio
async def test_prelaunch_yields_to_defense_when_home_attacked():
    """家里挨打 → 未 launch 的凤凰交还 sharpy 防守(clear_task),不再被拽去集结点。"""
    # recall_threshold=0(召回关掉)也要生效 —— "还没出门就被打家"跟召回开关无关
    act = _act(wave_threshold=5, recall_threshold=0, recall_radius=30.0)
    ai = _make_ai(n_phoenixes=2, start_loc=(10.0, 10.0), stargate=(40.0, 40.0))
    hall = SimpleNamespace(position=Point2((10.0, 10.0)))
    ai.townhalls = _FakeUnits([hall])
    enemies = []
    for t, pos in ((777, (12.0, 12.0)), (778, (13.0, 11.0))):  # 2 个 = 独立阈值
        e = _make_phoenix(tag=t, pos=pos)
        e.is_structure = False
        e.type_id = "MARINE"
        enemies.append(e)
    ai.enemy_units = _FakeUnits(enemies)
    act.ai = ai
    act.knowledge = _knowledge()

    await act.execute()
    act.knowledge.roles.clear_task.assert_called()
    for u in ai.units.return_value.ready:
        u.move.assert_not_called()


@pytest.mark.asyncio
async def test_prelaunch_single_scout_does_not_release_phoenixes():
    """家门口只有 1 个敌方单位(侦查兵) → 不该放手,凤凰继续集结。"""
    act = _act(wave_threshold=5, recall_threshold=0, recall_radius=30.0)
    ai = _make_ai(n_phoenixes=2, start_loc=(10.0, 10.0), stargate=(40.0, 40.0))
    ai.townhalls = _FakeUnits([SimpleNamespace(position=Point2((10.0, 10.0)))])
    scout = _make_phoenix(tag=777, pos=(12.0, 12.0))
    scout.is_structure = False
    scout.type_id = "MARINE"
    ai.enemy_units = _FakeUnits([scout])
    act.ai = ai
    act.knowledge = _knowledge()

    await act.execute()
    for u in ai.units.return_value.ready:
        u.move.assert_called_with(Point2((40.0, 40.0)))
