"""FeintSquadAct 单测（2026-07-09 坑道虫突袭精修 Round 4「声东击西」）。

覆盖：招募封顶（tag 从小到大确定性选取）/ 发布认领集合供 NydusRaidAct 排除 /
玩家单位级 claim 立即让出 / POKE→RETREAT 低血量触发 + RETREAT 用 move 不用
attack_move（控制权模型规则4）/ RETREAT→POKE 回血阈值切回 / 目标锚点取敌方
二矿(rank1) mineral_line_center，无 zone_manager 时兜底 enemy_start_locations。

不拉起 SC2：mock ai/knowledge/cache/zone_manager，同 `test_nydus_raid.py` 范式。
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


@pytest.fixture(autouse=True)
def _sc2_enum_same_source() -> None:
    """同 test_nydus_raid.py：全量跑时把模块全局 UnitTypeId 重绑成当前 sys.modules 版本，
    防止 conftest 重导致 enum 类身份不等导致的假失败。"""
    import sc2.ids.unit_typeid as _m

    globals()["UnitTypeId"] = _m.UnitTypeId
    yield


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
    sys.modules.pop("vibecraft.bot.auto_combat.zerg.plans.feint_squad_act", None)
    for name in created:
        sys.modules.pop(name, None)


class _Units:
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

    def filter(self, fn):
        return _Units([u for u in self._items if fn(u)])

    def closest_to(self, pos):
        p = pos if isinstance(pos, Point2) else pos.position
        return min(self._items, key=lambda u: u.position.distance_to(p))


def _ling(tag, pos=(0.0, 0.0), health=35.0, health_max=35.0, shield=0.0, shield_max=0.0):
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        type_id=UnitTypeId.ZERGLING,
        health=health,
        health_max=health_max,
        shield=shield,
        shield_max=shield_max,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
        target_in_range=lambda *_a, **_k: False,
        move=MagicMock(),
        attack=MagicMock(),
    )


def _act(**overrides):
    from vibecraft.bot.auto_combat.zerg.plans.feint_squad_act import FeintSquadAct

    kwargs = {"feint_cap": 2, "bail_hp_ratio": 0.35, "recover_hp_ratio": 0.75}
    kwargs.update(overrides)
    a = FeintSquadAct.__new__(FeintSquadAct)
    FeintSquadAct.__init__(a, **kwargs)
    return a


def _wire(
    a,
    *,
    lings=None,
    player_tags=None,
    time=100.0,
    enemy_units=None,
    enemy_start=(100.0, 100.0),
    wave_loaded=True,
):
    # wave_loaded=True → drop_imminent → 佯攻狗进入 POKE/RETREAT 微操(默认;测 staging 传 False)。
    a.knowledge = SimpleNamespace(
        roles=MagicMock(), vibecraft=SimpleNamespace(nydus_wave_loaded=wave_loaded)
    )
    type_map = {UnitTypeId.ZERGLING: lings or []}
    cache = MagicMock()
    cache.own.side_effect = lambda t: _Units(type_map.get(t, []))
    a.cache = cache
    a.ai = SimpleNamespace(
        time=time,
        _llm_controlled_tags=player_tags or set(),
        enemy_units=_Units(enemy_units or []),
        enemy_start_locations=[Point2(enemy_start)],
        start_location=Point2((0.0, 0.0)),
    )
    return a


# ══════════════════════════════════════════════════════════════════════════
# 招募封顶 + 发布认领集合
# ══════════════════════════════════════════════════════════════════════════


def test_recruit_caps_and_publishes_claimed_tags_for_nydus_raid_act_to_exclude():
    a = _act(feint_cap=2)
    lings = [_ling(3), _ling(1), _ling(2)]
    _wire(a, lings=lings)
    asyncio.run(a.execute())
    assert a._tags == {1, 2}  # tag 从小到大确定性选取，第 3 只(tag=3)不招募
    assert a.ai._vibecraft_nydus_feint_tags == {1, 2}


def test_recruit_skips_player_claimed_units():
    a = _act(feint_cap=2)
    lings = [_ling(1), _ling(2)]
    _wire(a, lings=lings, player_tags={1})
    asyncio.run(a.execute())
    assert 1 not in a._tags
    assert 2 in a._tags


def test_reserves_all_claimed_units_each_tick():
    a = _act(feint_cap=2)
    lings = [_ling(1), _ling(2)]
    _wire(a, lings=lings)
    asyncio.run(a.execute())
    assert a.knowledge.roles.set_task.call_count == 2


# ══════════════════════════════════════════════════════════════════════════
# POKE ↔ RETREAT 状态机
# ══════════════════════════════════════════════════════════════════════════


def test_low_hp_triggers_retreat_using_move_not_attack():
    a = _act(feint_cap=1, bail_hp_ratio=0.35)
    low_hp = _ling(1, pos=(50.0, 50.0), health=5.0, health_max=35.0)  # 14% < 35% bail
    _wire(a, lings=[low_hp])
    asyncio.run(a.execute())
    assert a._state[1] == "RETREAT"
    # 2026-07-12:撤退撤到"离目标锚点一小段(朝家方向)的固定点",不一路撤回家(防看着像被坑道拉扯震荡)。
    from vibecraft.bot.auto_combat.zerg.plans.feint_squad_act import _RETREAT_BACK

    target = a.ai.enemy_start_locations[0]  # 无 zone_manager → 目标锚点退回敌方主基
    expected = target.towards(a.ai.start_location, _RETREAT_BACK)
    low_hp.move.assert_called_once_with(expected)
    assert expected != a.ai.start_location  # 用 move 撤，但不撤回家
    low_hp.attack.assert_not_called()


def test_retreat_recovers_to_poke_above_recover_threshold():
    a = _act(feint_cap=1, bail_hp_ratio=0.35, recover_hp_ratio=0.75)
    ling = _ling(1, pos=(50.0, 50.0), health=30.0, health_max=35.0)  # 86% >= 75% recover
    _wire(a, lings=[ling])
    a._tags = {1}
    a._state = {1: "RETREAT"}
    asyncio.run(a.execute())
    assert a._state[1] == "POKE"


def test_poke_unit_far_from_target_moves_toward_it():
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0), health=35.0, health_max=35.0)
    _wire(a, lings=[ling], enemy_start=(100.0, 100.0))
    asyncio.run(a.execute())
    assert a._state[1] == "POKE"
    ling.move.assert_called_once()


def _combat(pos):
    """敌方战斗单位(非农民非建筑)——用于 outnumbered 判定。"""
    p = Point2(pos)
    return SimpleNamespace(
        type_id=UnitTypeId.ROACH,
        is_structure=False,
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def test_retreat_when_outnumbered_even_at_full_hp():
    """进退适度(2026-07-12 用户「对方兵多就退」):满血也撤,不再只看 HP<35%。"""
    a = _act(feint_cap=1, bail_hp_ratio=0.35)
    ling = _ling(1, pos=(50.0, 50.0), health=35.0, health_max=35.0)  # 满血
    enemies = [
        _combat((52.0, 50.0)),
        _combat((53.0, 50.0)),
        _combat((51.0, 50.0)),
    ]  # 3 战斗单位贴身
    _wire(a, lings=[ling], enemy_units=enemies)
    asyncio.run(a.execute())
    assert a._state[1] == "RETREAT"  # 满血但 3 敌 > 1 己 → 撤
    ling.move.assert_called()  # 撤用 move
    ling.attack.assert_not_called()  # 绝不硬拼


def test_no_feint_management_before_drop_imminent():
    """前期(未到投送窗口)别送狗(2026-07-12 用户「前期别他妈送狗了」):根本不管这些狗——不 Reserve、
    不认领、不下命令,让它们回落到 PlanZoneDefense 帮防守家里。"""
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0))
    _wire(a, lings=[ling], enemy_start=(100.0, 100.0), wave_loaded=False)  # 投送窗口未到
    asyncio.run(a.execute())
    assert a._tags == set()  # 没认领任何狗
    assert a.ai._vibecraft_nydus_feint_tags == set()  # 发布空集(raid act 也不排除)
    a.knowledge.roles.set_task.assert_not_called()  # 没 Reserve
    ling.move.assert_not_called()
    ling.attack.assert_not_called()


def test_poke_unit_near_target_attacks_worker_in_range():
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(100.0, 100.0), health=35.0, health_max=35.0)
    ling.target_in_range = lambda *_a, **_k: True
    worker = SimpleNamespace(
        type_id=UnitTypeId.DRONE,
        position=Point2((101.0, 100.0)),
        distance_to=lambda o: Point2((101.0, 100.0)).distance_to(
            o if isinstance(o, Point2) else o.position
        ),
    )
    _wire(a, lings=[ling], enemy_units=[worker], enemy_start=(100.0, 100.0))
    asyncio.run(a.execute())
    ling.attack.assert_called_once_with(worker)


# ══════════════════════════════════════════════════════════════════════════
# 目标锚点：二矿(rank1) mineral_line_center，无 zone_manager 兜底 enemy_start
# ══════════════════════════════════════════════════════════════════════════


def test_target_anchor_prefers_natural_zone_mineral_line_center():
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0))
    _wire(a, lings=[ling])
    main_zone = SimpleNamespace(
        center_location=Point2((100.0, 100.0)), mineral_line_center=Point2((100.0, 100.0))
    )
    natural_zone = SimpleNamespace(
        center_location=Point2((80.0, 80.0)), mineral_line_center=Point2((80.0, 80.0))
    )
    a.zone_manager = SimpleNamespace(
        enemy_start_location=Point2((100.0, 100.0)),
        expansion_zones=[main_zone, natural_zone],
    )
    anchor = a._get_target_anchor()
    assert anchor == Point2((80.0, 80.0))  # rank1（第二近）= natural


def _townhall(pos, type_id=None):
    p = Point2(pos)
    return SimpleNamespace(
        type_id=type_id or UnitTypeId.HATCHERY,
        position=p,
        distance_to=lambda o, _p=p: _p.distance_to(o if isinstance(o, Point2) else o.position),
    )


def test_punish_mode_targets_outermost_undefended_enemy_base_when_army_home():
    """2026-07-12 用户「主力回家救场了,佯攻狗就真打最外面的四矿/三矿/二矿」:
    主基附近 ≥3 战斗单位=主力回家 → 打最外(离主基最远)已占且无防守的敌方矿。"""
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0))
    main_z = SimpleNamespace(
        center_location=Point2((100.0, 100.0)), mineral_line_center=Point2((100.0, 100.0))
    )
    nat_z = SimpleNamespace(
        center_location=Point2((80.0, 80.0)), mineral_line_center=Point2((82.0, 82.0))
    )
    third_z = SimpleNamespace(
        center_location=Point2((60.0, 60.0)), mineral_line_center=Point2((62.0, 62.0))
    )
    # 主力(3 战斗单位)在主基(100,100)附近 → 回家救场
    army = [_combat((101.0, 100.0)), _combat((100.0, 101.0)), _combat((99.0, 100.0))]
    _wire(a, lings=[ling], enemy_units=army)
    # 敌方在三矿(60,60)有 townhall = 已占;三矿附近无战斗单位 = 无防守
    a.ai.enemy_structures = _Units([_townhall((60.0, 60.0))])
    a.zone_manager = SimpleNamespace(
        enemy_start_location=Point2((100.0, 100.0)),
        expansion_zones=[main_z, nat_z, third_z],
    )
    anchor = a._get_target_anchor()
    assert anchor == Point2((62.0, 62.0))  # 最外已占无防守 = 三矿矿线
    assert a._target_mode == "punish"


def test_target_anchor_falls_back_to_enemy_start_without_zone_manager():
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0))
    _wire(a, lings=[ling], enemy_start=(120.0, 130.0))
    anchor = a._get_target_anchor()
    assert anchor == Point2((120.0, 130.0))


def test_target_anchor_locked_once_not_recomputed():
    a = _act(feint_cap=1)
    ling = _ling(1, pos=(0.0, 0.0))
    _wire(a, lings=[ling], enemy_start=(120.0, 130.0))
    first = a._get_target_anchor()
    a.ai.enemy_start_locations = [Point2((999.0, 999.0))]  # 后续变化不应影响已锁定锚点
    second = a._get_target_anchor()
    assert first == second == Point2((120.0, 130.0))


# ══════════════════════════════════════════════════════════════════════════
# 与 NydusRaidAct 的双向互斥（2026-07-26 真局"狗被反复拉扯"bug）
# ══════════════════════════════════════════════════════════════════════════
def test_recruit_skips_lings_owned_by_raid_act():
    """raid 已认领且不可让渡（TRANSIT/STRIKE）的狗，佯攻队绝不能抢。"""
    a = _act(feint_cap=3)
    lings = [_ling(1), _ling(2), _ling(3), _ling(4)]
    _wire(a, lings=lings)
    a.ai._vibecraft_nydus_raid_tags = {1, 2}  # raid 持有
    a.ai._vibecraft_nydus_raid_yieldable = set()  # 都不可让渡
    a._tick()
    assert a._tags == {3, 4}  # 只拿自由狗


def test_recruit_takes_yieldable_staged_lings_when_free_ones_run_out():
    """自由狗不够时，才从 raid 标记为可让渡（STAGE）的里补，避免佯攻队被饿死。"""
    a = _act(feint_cap=3)
    lings = [_ling(1), _ling(2), _ling(3)]
    _wire(a, lings=lings)
    a.ai._vibecraft_nydus_raid_tags = {1, 2, 3}
    a.ai._vibecraft_nydus_raid_yieldable = {2, 3}  # 这两只在集结、可让渡
    a._tick()
    assert a._tags == {2, 3}


def test_drop_imminent_also_true_while_retry_pending():
    """虫被拆后的重投冷却期，佯攻队要继续出去吸引火力（2026-07-26 用户）。"""
    a = _act()
    _wire(a, lings=[_ling(1)], wave_loaded=False)
    a.knowledge.vibecraft.nydus_retry_pending = True
    a._tick()
    assert a._tags == {1}
