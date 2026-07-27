"""NydusRaidAct 单测（2026-07-09 坑道虫突袭精修第一轮）。

覆盖：STAGE 招募封顶 + 留家女王 / 集结锚点 + Reserve / 首波攒够阈值门 /
worm 未 ready 也预装进坑道网络（Round 2）/ bypass SMART 装载 /
passengers 判进度(TRANSIT) / TRANSIT→STRIKE 钻出转移 / STRIKE 优先扑农民、
无目标兜底敌方主基地 / 玩家单位级 claim 立即让出管理权 / release_after_s
硬释放停止招募 + 显式还 role / TRANSIT 卡住超时(100s)网络侧兜底卸回家。

不拉起 SC2：mock ai/knowledge/cache，UnitCommand 注入 fake（真 UnitCommand
assert unit.__class__.__name__=="Unit"，SimpleNamespace 过不了）——同
`test_spare_cc_expand_act.py` 范式。
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


@pytest.fixture(autouse=True)
def _sc2_enum_same_source() -> None:
    """全量跑时 conftest `fake_sharpy_bot_env`（其它文件用）会 del 真 `sc2.*` 模块致其
    重导 → 本文件顶部 collection 时绑定的 `UnitTypeId`（旧 enum 类）与 `nydus_raid_act.py`
    （每个 test 因 `_fake_sharpy` 而重新 import，lazy 拿到重导后的新 enum 类）身份不等 →
    dict key 查找失配（`cache.own(UnitTypeId.ROACH)` 用新类，`_wire` 的 `type_map` 用旧类）
    → 全量跑时假失败（单独跑该文件全过）。同 `test_tech_progress_panel.py::_sc2_enum_same_source`
    范式：每个测试前把模块全局 `UnitTypeId` 重绑成当前 sys.modules 版本。
    """
    import sc2.ids.unit_typeid as _m

    globals()["UnitTypeId"] = _m.UnitTypeId
    yield


def _live_ability_id():
    """运行时解析 AbilityId（同 test_spare_cc_expand_act.py：免疫全量跑下的模块重导入
    order-isolation flake，见该文件 `_live_ability_id` 注释）。"""
    from sc2.ids.ability_id import AbilityId

    return AbilityId


@pytest.fixture(autouse=True)
def _fake_unit_command():
    mod = sys.modules.get("sc2.unit_command")
    created = False
    if mod is None:
        mod = ModuleType("sc2.unit_command")
        sys.modules["sc2.unit_command"] = mod
        created = True
    orig = getattr(mod, "UnitCommand", None)

    def _fake(ability, unit, target=None, queue=False):
        return SimpleNamespace(ability=ability, unit=unit, target=target, queue=queue)

    mod.UnitCommand = _fake  # type: ignore[attr-defined]
    yield
    if created:
        sys.modules.pop("sc2.unit_command", None)
    elif orig is not None:
        mod.UnitCommand = orig  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _fake_sharpy():
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    roles = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles, "UnitTask"):
        roles.UnitTask = SimpleNamespace(Reserved="Reserved", Idle="Idle")  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.zerg.plans.nydus_raid_act", None)
    for name in created:
        sys.modules.pop(name, None)


# ── fake collections / units ────────────────────────────────────────────────


class _Units:
    """最小 fake `Units`：迭代 + `.ready` 过滤 + `.closest_to`。"""

    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)

    @property
    def ready(self):
        return _Units([u for u in self._items if getattr(u, "is_ready", True)])

    def closest_to(self, pos):
        p = pos if isinstance(pos, Point2) else pos.position
        return min(self._items, key=lambda u: u.position.distance_to(p))


def _unit(tag, pos, type_id, orders=None):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        type_id=type_id,
        is_structure=False,  # 真 Unit 一定有这个字段,mock 同口径
        orders=orders if orders is not None else [],
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
        move=MagicMock(),
        attack=MagicMock(),
    )


def _roach(tag, pos=(10.0, 10.0)):
    return _unit(tag, pos, UnitTypeId.ROACH)


def _zergling(tag, pos=(10.0, 10.0)):
    return _unit(tag, pos, UnitTypeId.ZERGLING)


def _queen(tag, pos=(10.0, 10.0)):
    return _unit(tag, pos, UnitTypeId.QUEEN)


def _nydus_struct(tag, pos=(10.0, 10.0), passengers_tags=None, cargo_used=0):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        passengers_tags=set(passengers_tags or set()),
        cargo_used=cargo_used,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _worker(pos):
    p = Point2(pos)
    return SimpleNamespace(
        type_id=UnitTypeId.DRONE,  # 农民按真实 type_id 建模(Unit 无 is_worker)
        is_structure=False,
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def _act(**overrides):
    from vibecraft.bot.auto_combat.zerg.plans.nydus_raid_act import NydusRaidAct

    kwargs = {
        "stage_supply_threshold": 4.0,
        "keep_home_queens": 1,
        "roach_cap": 2,
        "zergling_cap": 2,
        "queen_cap": 1,
        "release_after_s": 300.0,
    }
    kwargs.update(overrides)
    a = NydusRaidAct.__new__(NydusRaidAct)
    NydusRaidAct.__init__(a, **kwargs)
    return a


def _wire(
    a,
    *,
    roaches=None,
    zerglings=None,
    queens=None,
    networks=None,
    canals=None,
    player_tags=None,
    time=100.0,
    enemy_units=None,
    enemy_structures=None,
    enemy_start=(100.0, 100.0),
    unload_abilities=None,
):
    a.knowledge = SimpleNamespace(roles=MagicMock())
    type_map = {
        UnitTypeId.ROACH: roaches or [],
        UnitTypeId.ZERGLING: zerglings or [],
        UnitTypeId.QUEEN: queens or [],
        UnitTypeId.NYDUSNETWORK: networks or [],
        UnitTypeId.NYDUSCANAL: canals or [],
    }
    cache = MagicMock()
    cache.own.side_effect = lambda t: _Units(type_map.get(t, []))
    a.cache = cache
    a.ai = SimpleNamespace(
        game_info=SimpleNamespace(map_center=Point2((64.0, 64.0))),
        townhalls=[],
        time=time,
        _llm_controlled_tags=player_tags or set(),
        enemy_units=enemy_units or [],
        enemy_structures=enemy_structures or [],
        enemy_start_locations=[Point2(enemy_start)],
        start_location=Point2((0.0, 0.0)),
        get_available_abilities=AsyncMock(return_value=[unload_abilities or []]),
    )
    return a


# ══════════════════════════════════════════════════════════════════════════
# 招募：封顶 + 留家女王
# ══════════════════════════════════════════════════════════════════════════


def test_recruit_stages_up_to_cap_and_no_more():
    a = _act(roach_cap=2)
    roaches = [_roach(1), _roach(2), _roach(3)]
    _wire(a, roaches=roaches)
    a._recruit(now=10.0, player_tags=set())
    staged = [t for t, s in a._state.items() if s == "STAGE"]
    assert len(staged) == 2  # cap=2，第 3 只不招募
    assert a._held_count(UnitTypeId.ROACH) == 2


def test_recruit_cap_frees_up_after_wave_dies():
    """第一波全灭后必须能招第二波（2026-07-26 用户"第一波被打掉可以来第二波"）。

    旧实现用终身累计计数、死了不减 → 打满 cap 之后永远招不到新兵。现在按"当前还持有的"
    计数：`_prune_dead` 摘掉阵亡 tag → 计数回落 → 新孵的蟑螂能补进来组第二波。
    """
    a = _act(roach_cap=2)
    _wire(a, roaches=[_roach(1), _roach(2)])
    a._recruit(now=10.0, player_tags=set())
    assert a._held_count(UnitTypeId.ROACH) == 2
    a._prune_dead(army={})  # 第一波全灭
    assert a._held_count(UnitTypeId.ROACH) == 0
    _wire(a, roaches=[_roach(3), _roach(4)])  # 新孵出来的
    a._recruit(now=60.0, player_tags=set())
    assert sorted(t for t, s in a._state.items() if s == "STAGE") == [3, 4]


def test_recruit_keeps_home_queens_out_of_state():
    a = _act(keep_home_queens=1, queen_cap=5)
    queens = [_queen(5), _queen(2), _queen(9)]  # 排序按 tag：2 留家
    _wire(a, queens=queens)
    a._recruit(now=10.0, player_tags=set())
    assert a._home_queen_tags == {2}
    assert 2 not in a._state
    assert 5 in a._state and 9 in a._state


def test_recruit_skips_player_claimed_units():
    a = _act(roach_cap=2)
    roaches = [_roach(1), _roach(2)]
    _wire(a, roaches=roaches, player_tags={1})
    a._recruit(now=10.0, player_tags={1})
    assert 1 not in a._state
    assert 2 in a._state


# ══════════════════════════════════════════════════════════════════════════
# STAGE：集结锚点 + Reserve + 首波攒够阈值门
# ══════════════════════════════════════════════════════════════════════════


def test_stage_moves_units_to_network_anchor_and_reserves():
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    roach = _roach(1, pos=(10.0, 10.0))
    _wire(a, roaches=[roach], networks=[net])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._tick_stage(now=10.0, army={1: roach})
    roach.move.assert_called_once()
    a.knowledge.roles.set_task.assert_called()


def test_wave1_blocked_when_below_supply_threshold():
    """threshold=4.0（roach=2.0/只），只招 1 只到位 → 不够，不该发 bypass 装载。"""
    a = _act(stage_supply_threshold=4.0)
    net = _nydus_struct(100, pos=(50.0, 50.0))
    canal = _nydus_struct(200, pos=(90.0, 90.0))
    roach = _roach(1, pos=(50.0, 50.0))  # 已在锚点附近
    _wire(a, roaches=[roach], networks=[net], canals=[canal])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._tick_stage(now=10.0, army={1: roach})
    assert not getattr(a.ai, "_vibecraft_bypass_actions", [])
    assert a._first_wave_sent is False


def test_wave1_loads_even_when_canal_not_ready():
    """Round 2（2026-07-09 真局教训）：worm(canal) 没 ready 也照样预装进坑道网络。

    旧版行为是"canal 没 ready 就不装载"，但真局实测这拉长了"worm 打通 → army 才开始
    装 → 才排空"的暴露窗口。改成 STAGE 阶段就把 army 灌进自家坑道网络（网络在家，
    天然安全），虫洞一 ready 就能立刻排空（见 `_tick_stage` 里 canal_ready 门控移除
    的注释 + 坑道骚扰 Round 4 精修点 2）。
    """
    AbilityId = _live_ability_id()
    a = _act(stage_supply_threshold=2.0, min_roaches_wave1=1)
    net = _nydus_struct(100, pos=(50.0, 50.0))
    roach = _roach(1, pos=(50.0, 50.0))
    _wire(a, roaches=[roach], networks=[net], canals=[])  # 没有 canal，仍应预装
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._stage_anchor = Point2((50.0, 50.0))  # 钉住集结点=单位所在处;本例测装载,不测集结点选址
    a._tick_stage(now=10.0, army={1: roach})
    assert a._first_wave_sent is True
    bypass = a.ai._vibecraft_bypass_actions
    assert len(bypass) == 1
    assert bypass[0].ability == AbilityId.SMART
    assert bypass[0].unit.tag == 1
    assert bypass[0].target.tag == 100  # 目标是 network（预装进自家网络，不是 canal）


def test_wave1_load_issues_bypass_smart_when_ready():
    """threshold=2.0，1 只蟑螂到位 + worm ready → 首波整批经 bypass 发 SMART。"""
    AbilityId = _live_ability_id()
    a = _act(stage_supply_threshold=2.0, min_roaches_wave1=1)
    net = _nydus_struct(100, pos=(50.0, 50.0))
    canal = _nydus_struct(200, pos=(90.0, 90.0))
    roach = _roach(1, pos=(50.0, 50.0))
    _wire(a, roaches=[roach], networks=[net], canals=[canal])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._stage_anchor = Point2((50.0, 50.0))  # 钉住集结点=单位所在处;本例测装载,不测集结点选址
    a._tick_stage(now=10.0, army={1: roach})
    assert a._first_wave_sent is True
    bypass = a.ai._vibecraft_bypass_actions
    assert len(bypass) == 1
    cmd = bypass[0]
    assert cmd.ability == AbilityId.SMART
    assert cmd.unit.tag == 1
    assert cmd.target.tag == 100  # 目标是 network


# ══════════════════════════════════════════════════════════════════════════
# TRANSIT：passengers 判进度 + 钻出转 STRIKE
# ══════════════════════════════════════════════════════════════════════════


def test_stage_transitions_to_transit_when_tag_in_passengers():
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0), passengers_tags={1})
    roach = _roach(1)
    _wire(a, roaches=[roach], networks=[net])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._promote_stage_to_transit(now=10.0, passenger_tags=a._passenger_tags())
    assert a._state[1] == "TRANSIT"
    assert 1 in a._transit_since


def test_promote_stage_to_transit_runs_before_prune_dead_in_full_tick():
    """回归（2026-07-09 真局 nydus_selftest 首跑抓到的坑）：SMART 装载生效那一刻，
    单位在**同一帧**从 army 消失 + 出现在 passenger_tags 里。若 `_prune_dead` 先跑，
    会把这个 STAGE tag 当"死了"直接删掉状态，`_tick_transit` 再也等不到 TRANSIT
    （真机症状：NYDUSRAID load 正常触发 + canal.cargo_used>0 确认真装了货，但
    transit/strike 事件永远是 0）。全量 `_tick()` 跑一遍必须正确升到 TRANSIT，
    不能被剪成"查无此 tag"。
    """
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0), passengers_tags={1})
    # army 快照里**没有**这个 tag（模拟"这一帧它已经从 cache.own(ROACH) 消失，
    # 因为它已经变成坑道乘客"）——只有 network.passengers_tags 能看到它。
    _wire(a, roaches=[], networks=[net])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    asyncio.run(a._tick())
    assert a._state.get(1) == "TRANSIT", (
        "STAGE 单位刚变乘客的那一帧必须升到 TRANSIT，不能被 _prune_dead 误剪掉状态"
    )
    assert 1 in a._transit_since


def test_transit_promotes_to_strike_once_unloaded_and_visible():
    a = _act()
    canal = _nydus_struct(200, pos=(90.0, 90.0), passengers_tags=set())  # 已不在乘客名单
    roach = _roach(1, pos=(90.0, 90.0))  # 钻出后重新出现在 army
    _wire(a, roaches=[roach], canals=[canal])
    a._state[1] = "TRANSIT"
    a._state_since[1] = 5.0
    a._transit_since[1] = 5.0
    asyncio.run(a._tick_transit(now=10.0, army={1: roach}, passenger_tags=set()))
    assert a._state[1] == "STRIKE"
    assert 1 not in a._transit_since


def test_transit_issues_unload_when_canal_has_cargo():
    """canal.cargo_used>0 → 探测 UNLOAD ability 并对 canal 下 bypass 指令。"""
    AbilityId = _live_ability_id()
    a = _act()
    canal = _nydus_struct(200, pos=(90.0, 90.0), cargo_used=3)
    _wire(a, canals=[canal], unload_abilities=[AbilityId.UNLOADALL_NYDUSWORM])
    asyncio.run(a._tick_transit(now=10.0, army={}, passenger_tags=set()))
    bypass = a.ai._vibecraft_bypass_actions
    assert len(bypass) == 1
    assert bypass[0].ability == AbilityId.UNLOADALL_NYDUSWORM
    assert bypass[0].unit.tag == 200


def test_transit_stuck_timeout_bails_via_network_unload():
    """卡坑道超时 + 没 ready canal → 网络侧兜底卸回家 + 释放该 tag。

    Round 2：超时阈值从 45s 提到 100s（`_TRANSIT_STUCK_TIMEOUT_S`，理由见 nydus_raid_act.py
    该常量旁注释 —— 现在 STAGE 阶段就预装，45s 对"Network 已就绪但 worm 还没打通"这种正常
    等待窗口太短，容易误触发兜底）。这里用 110s（> 100s 新阈值）触发。
    """
    AbilityId = _live_ability_id()
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    _wire(a, networks=[net], canals=[], unload_abilities=[AbilityId.UNLOADALL_NYDASNETWORK])
    a._state[1] = "TRANSIT"
    a._state_since[1] = 0.0
    a._transit_since[1] = 0.0  # 已经过去 110s（下面 now=110）> 100s 新超时阈值
    asyncio.run(a._tick_transit(now=110.0, army={}, passenger_tags=set()))
    bypass = a.ai._vibecraft_bypass_actions
    assert any(c.ability == AbilityId.UNLOADALL_NYDASNETWORK for c in bypass)
    assert any(c.unit.tag == 100 for c in bypass)
    assert 1 not in a._state
    assert 1 in a._ever_released
    assert 1 in a._pending_release_tags


def test_bail_transit_clears_all_transit_tags_not_just_stuck_ones():
    """Round 3 真机踩坑修复：`UNLOADALL_NYDASNETWORK` 卸出网络里**全体**乘客，不止
    卡够 100s 的那批——若只清 stuck_tags，其余仍标 TRANSIT 的 tag（刚装载不久、没超时）
    次帧被误判"钻出敌方家"→在自家网络门口被提为 STRIKE，攻向敌方主基地/建筑（真局实测
    10 次 STRIKE 里 6 次是这个假阳性）。bail 触发时必须把**当前所有** TRANSIT tag 一起
    清出状态字典，不只 stuck_tags。"""
    AbilityId = _live_ability_id()
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    _wire(a, networks=[net], canals=[], unload_abilities=[AbilityId.UNLOADALL_NYDASNETWORK])
    # tag=1 卡够 100s（真正触发 bail 的那个）
    a._state[1] = "TRANSIT"
    a._state_since[1] = 0.0
    a._transit_since[1] = 0.0
    # tag=2 刚装载 5s，远未超时——但会被同一次 UNLOADALL 一起吐出来
    a._state[2] = "TRANSIT"
    a._state_since[2] = 105.0
    a._transit_since[2] = 105.0
    asyncio.run(a._tick_transit(now=110.0, army={}, passenger_tags=set()))
    assert 1 not in a._state
    assert 2 not in a._state
    assert 1 in a._pending_release_tags
    assert 2 in a._pending_release_tags


# ══════════════════════════════════════════════════════════════════════════
# STRIKE：优先扑农民 / 兜底敌方主基地
# ══════════════════════════════════════════════════════════════════════════


def test_strike_targets_worker_cluster_centroid_near_anchor():
    """Round 3：worker 目标改成矿线锚点(无 zone_manager 时兜底 enemy_start_locations)
    附近的农民质心 beeline attack-move，不是"打击单位半径内最近那个农民对象"。"""
    a = _act()
    roach = _roach(1, pos=(90.0, 90.0))
    # enemy_start=(100,100) 默认（_wire）→ 锚点即此，near_worker 在 _WORKER_SEEK_RADIUS(18) 内
    near_worker = _worker((92.0, 90.0))
    far_worker = _worker((150.0, 150.0))  # 远离锚点，超出搜索半径，不该被选进质心
    _wire(a, roaches=[roach], enemy_units=[near_worker, far_worker])
    a._state[1] = "STRIKE"
    a._state_since[1] = 10.0
    a._tick_strike(now=10.0, army={1: roach})
    # 只有 near_worker 落在锚点半径内 → 质心即它自身坐标
    roach.attack.assert_called_once_with(Point2((92.0, 90.0)))


def test_strike_targets_cluster_centroid_of_multiple_workers():
    a = _act()
    roach = _roach(1, pos=(90.0, 90.0))
    # enemy_start=(100,100) 锚点；两个农民对称分布 → 质心落在锚点正上方
    w1 = _worker((95.0, 100.0))
    w2 = _worker((105.0, 100.0))
    _wire(a, roaches=[roach], enemy_units=[w1, w2])
    a._state[1] = "STRIKE"
    a._state_since[1] = 10.0
    a._tick_strike(now=10.0, army={1: roach})
    roach.attack.assert_called_once_with(Point2((100.0, 100.0)))


def test_strike_worker_anchor_prefers_ready_canal_position_over_enemy_start():
    """Round 3 真机诊断修复：农民搜索锚点优先取 ready canal 的真实位置（ground truth，
    STRIKE 单位钻出的地方），不是 zone_manager/enemy_start_locations 这类可能跟 worm
    真实落点相差 ~20 格的理论锚点。canal 落在远离 enemy_start 的地方时，农民质心应该
    按 canal 位置算，不是 enemy_start。"""
    a = _act()
    roach = _roach(1, pos=(15.0, 15.0))
    canal = _nydus_struct(50, pos=(10.0, 10.0))
    near_worker = _worker((12.0, 10.0))  # 离 canal 近，离 enemy_start(100,100) 远
    _wire(a, roaches=[roach], canals=[canal], enemy_units=[near_worker], enemy_start=(100.0, 100.0))
    a._state[1] = "STRIKE"
    a._state_since[1] = 10.0
    a._tick_strike(now=10.0, army={1: roach})
    roach.attack.assert_called_once_with(Point2((12.0, 10.0)))


def test_strike_falls_back_to_structure_when_no_workers_near_anchor():
    """所有农民都在锚点搜索半径外（跑光/清光）→ 回退拆最近建筑，不空等。"""
    a = _act()
    roach = _roach(1, pos=(90.0, 90.0))
    far_worker = _worker((150.0, 150.0))  # 远离锚点 (100,100)，不计入质心
    structure = _nydus_struct(9, pos=(91.0, 91.0))
    _wire(a, roaches=[roach], enemy_units=[far_worker], enemy_structures=[structure])
    a._state[1] = "STRIKE"
    a._state_since[1] = 10.0
    a._tick_strike(now=10.0, army={1: roach})
    roach.attack.assert_called_once_with(structure)


def test_strike_falls_back_to_enemy_base_when_no_visible_targets():
    a = _act()
    roach = _roach(1, pos=(90.0, 90.0))
    _wire(a, roaches=[roach], enemy_units=[], enemy_structures=[], enemy_start=(120.0, 120.0))
    a._state[1] = "STRIKE"
    a._state_since[1] = 10.0
    a._tick_strike(now=10.0, army={1: roach})
    roach.attack.assert_called_once_with(Point2((120.0, 120.0)))


# ══════════════════════════════════════════════════════════════════════════
# 玩家单位级 claim + release_after_s 硬释放
# ══════════════════════════════════════════════════════════════════════════


def test_player_claim_yields_management_immediately():
    a = _act()
    roach = _roach(1)
    _wire(a, roaches=[roach])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    a._yield_to_player({1})
    assert 1 not in a._state
    assert 1 in a._pending_release_tags
    assert 1 in a._ever_released


def test_release_after_s_stops_recruit_and_clears_role():
    a = _act(release_after_s=50.0)
    roach = _roach(1)
    _wire(a, roaches=[roach])
    a._state[1] = "STAGE"
    a._state_since[1] = 0.0
    asyncio.run(a._tick())  # ai.time=100.0 由 _wire 默认 >= release_after_s=50
    assert a._released is True
    assert a._state == {}
    a.knowledge.roles.clear_task.assert_called_with(roach)


def test_release_after_s_prevents_new_recruits():
    a = _act(release_after_s=50.0)
    roach = _roach(1)
    _wire(a, roaches=[roach], time=100.0)
    asyncio.run(a._tick())
    assert a._state == {}  # 没有被招募（released 直接跳过 _recruit）


# ══════════════════════════════════════════════════════════════════════════
# 与 FeintSquadAct 的双向互斥（2026-07-26 真局"狗被反复拉扯"bug）
# ══════════════════════════════════════════════════════════════════════════
def test_yield_to_feint_drops_staged_ling_claimed_by_feint():
    """佯攻队认领的狗必须持续让出，否则两个 act 每帧对同一只狗下相反命令 → 来回抽搐。"""
    a = _act(zergling_cap=4)
    _wire(a, zerglings=[_zergling(1), _zergling(2)])
    a._recruit(now=10.0, player_tags=set())
    assert {1, 2} <= set(a._state)
    a._yield_to_feint({2})  # 佯攻队后来抓走了 2
    assert 2 not in a._state and 1 in a._state
    # 让出的不进 _ever_released：佯攻队放手后本 act 还能再招募它
    assert 2 not in a._ever_released


def test_yield_to_feint_never_pulls_unit_out_of_nydus():
    """TRANSIT（人在坑道里）不让渡——佯攻队的候选池只有 ready 单位，本就够不着它。"""
    a = _act()
    _wire(a, zerglings=[_zergling(7)])
    a._state[7] = "TRANSIT"
    a._yield_to_feint({7})
    assert a._state[7] == "TRANSIT"


def test_publish_owned_tags_marks_only_stage_as_yieldable():
    """发布给佯攻队的两份集合：全部持有 / 仅可让渡(STAGE)。"""
    a = _act()
    _wire(a, zerglings=[_zergling(1)])
    a._state = {1: "STAGE", 2: "TRANSIT", 3: "STRIKE"}
    a._publish_owned_tags()
    assert a.ai._vibecraft_nydus_raid_tags == {1, 2, 3}
    assert a.ai._vibecraft_nydus_raid_yieldable == {1}


# ══════════════════════════════════════════════════════════════════════════
# 女王在敌方家铺菌毯（2026-07-26 用户）
# ══════════════════════════════════════════════════════════════════════════
def _queen_e(tag, pos=(100.0, 100.0), energy=100.0):
    q = _unit(tag, pos, UnitTypeId.QUEEN)
    q.energy = energy
    return q


def test_strike_queen_plants_creep_tumor_on_creep():
    a = _act()
    q = _queen_e(1)
    _wire(a, queens=[q])
    a.ai.has_creep = lambda p: True
    a.ai.in_placement_grid = lambda p: True
    a.ai._vibecraft_bypass_actions = []
    a._state[1] = "STRIKE"
    a._cast_enemy_creep(now=200.0, strike_tags=[1], army={1: q})
    assert a.ai._vibecraft_bypass_actions, "应发出种菌毯瘤指令"


def test_strike_queen_skips_creep_without_creep_under_foot():
    """没菌毯就种不了（女王的瘤只能种在菌毯上）——不该空发指令。"""
    a = _act()
    q = _queen_e(1)
    _wire(a, queens=[q])
    a.ai.has_creep = lambda p: False
    a.ai.in_placement_grid = lambda p: True
    a.ai._vibecraft_bypass_actions = []
    a._state[1] = "STRIKE"
    a._cast_enemy_creep(now=200.0, strike_tags=[1], army={1: q})
    assert a.ai._vibecraft_bypass_actions == []


def test_strike_queen_keeps_energy_for_transfuse():
    """能量只够一发 transfuse 时不铺毯（救命优先于铺毯）。"""
    a = _act()
    q = _queen_e(1, energy=60.0)  # < 50(transfuse) + 25(tumor)
    _wire(a, queens=[q])
    a.ai.has_creep = lambda p: True
    a.ai.in_placement_grid = lambda p: True
    a.ai._vibecraft_bypass_actions = []
    a._state[1] = "STRIKE"
    a._cast_enemy_creep(now=200.0, strike_tags=[1], army={1: q})
    assert a.ai._vibecraft_bypass_actions == []


# ══════════════════════════════════════════════════════════════════════════
# 集结点语义（2026-07-27 用户:"集结的位置不用太靠近主基地" + "你就设个集结点就完了嘛"）
# ══════════════════════════════════════════════════════════════════════════
def test_stage_anchor_offset_away_from_network():
    """集结点要从坑道网络往外让开,不贴着基地站。"""
    from vibecraft.bot.auto_combat.zerg.plans.nydus_raid_act import _STAGE_OFFSET_FROM_NETWORK

    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    _wire(a, networks=[net])
    anchor = a._get_stage_anchor()
    assert anchor is not None
    d = anchor.distance_to(Point2((50.0, 50.0)))
    assert abs(d - _STAGE_OFFSET_FROM_NETWORK) < 0.5, (
        f"应让开 ~{_STAGE_OFFSET_FROM_NETWORK} 格,实际 {d:.1f}"
    )


def test_stage_does_not_reissue_move_every_frame():
    """设集结点 ≠ 每帧拽:单位有指令在执行且冷却没到 → 不重发。

    每帧硬发会把单位钉死在锚点上,任何别的行为(躲、还手)都被下一帧覆盖 —— 这正是家里挨打时
    它们参与不了防守的机制之一。
    """
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    roach = _roach(1, pos=(80.0, 80.0))  # 离集结点很远
    roach.orders = ["moving"]  # 已经在执行移动
    _wire(a, roaches=[roach], networks=[net])
    a._state[1] = "STAGE"
    a._tick_stage(now=10.0, army={1: roach})
    assert roach.move.call_count == 1  # 首次发一条
    a._tick_stage(now=11.0, army={1: roach})  # 冷却内、且有指令在执行
    assert roach.move.call_count == 1, "冷却内不该重发"


def test_stage_yields_units_to_defense_when_home_attacked():
    """家里挨打 → 集结兵 clear_task 还给 sharpy 防守,且不再被拽回集结点。"""
    a = _act()
    net = _nydus_struct(100, pos=(50.0, 50.0))
    roach = _roach(1, pos=(80.0, 80.0))
    _wire(a, roaches=[roach], networks=[net])
    hall = SimpleNamespace(
        position=Point2((50.0, 50.0)),
        distance_to=lambda o: Point2((50.0, 50.0)).distance_to(
            o if isinstance(o, Point2) else o.position
        ),
    )
    a.ai.townhalls = [hall]
    enemy = _unit(999, (52.0, 52.0), UnitTypeId.MARINE)
    a.ai.enemy_units = [enemy]
    a._state[1] = "STAGE"
    a._tick_stage(now=10.0, army={1: roach})
    a.knowledge.roles.clear_task.assert_called()
    roach.move.assert_not_called()
    assert a._state[1] == "STAGE", "威胁解除后要能自动恢复集结,状态不该被清"
