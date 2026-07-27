"""单测：BcHomeRepairAct（③ 在家修理，#583）。

覆盖：
- 残血在家 BC → ≤3 个空闲 SCV 分发 repair 指令（per-instance 断言）
- 已有 3 个 SCV 在修 → 不再多派
- 满血 BC → 跳过（不修）
- 远离家（离 townhall > _HOME_REPAIR_RADIUS）BC → 不修（MUST-FIX D：显式断言）
  Note（MUST-FIX D）：释放后 BC 能否自己回家由真局验；若 sharpy 主力 plan 把 BC
  带出去打就不在家内，此 act 不会修它（需额外 move(home) 才能触发修理）。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

# ── fake-sharpy fixture（同 test_bc_raid_act.py 模式）───────────────────────────


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """bc_raid_act 顶层 import sharpy.plans.acts.ActBase。注入 fake 让 import 过。"""
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
    sys.modules.pop("vibecraft.bot.auto_combat.terran.bc_raid_act", None)
    for name in created:
        sys.modules.pop(name, None)


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_unit(
    tag: int,
    unit_type: UnitTypeId,
    pos: tuple[float, float] = (10.0, 10.0),
    hp_pct: float = 1.0,
    is_repairing: bool = False,
    order_target: int | None = None,
) -> MagicMock:
    u = MagicMock(
        spec=[
            "tag",
            "unit_type",
            "position",
            "health_percentage",
            "is_repairing",
            "order_target",
            "repair",
            "distance_to",
        ]
    )
    u.tag = tag
    u.unit_type = unit_type
    u.position = Point2(pos)
    u.health_percentage = hp_pct
    u.is_repairing = is_repairing
    u.order_target = order_target
    u.repair = MagicMock()

    def _dist(other: Point2) -> float:
        return u.position.distance_to(other)

    u.distance_to = _dist
    return u


class _FakeReadyUnits:
    """轻量单位容器，支持 list()/iter() 和 .ready 属性。"""

    def __init__(self, units: list):
        self._units = units
        self.ready = self  # .ready 返回自身

    def __iter__(self):
        return iter(self._units)

    def __len__(self):
        return len(self._units)


def _make_act(
    bcs: list,
    scvs: list,
    townhall_pos: list[tuple[float, float]] | None = None,
):
    """构造 BcHomeRepairAct，注入 fake AI / cache / knowledge。"""
    # 延迟 import，等 _fake_sharpy fixture 把模块注好
    from vibecraft.bot.auto_combat.terran.bc_raid_act import BcHomeRepairAct

    if townhall_pos is None:
        townhall_pos = [(10.0, 10.0)]
    townhalls = [SimpleNamespace(position=Point2(p)) for p in townhall_pos]

    def _own_units(unit_type):
        if unit_type == UnitTypeId.BATTLECRUISER:
            return _FakeReadyUnits(bcs)
        if unit_type == UnitTypeId.SCV:
            return _FakeReadyUnits(scvs)
        return _FakeReadyUnits([])

    act = BcHomeRepairAct.__new__(BcHomeRepairAct)
    act.cache = SimpleNamespace(own=_own_units)
    act.knowledge = SimpleNamespace()
    act.ai = SimpleNamespace(townhalls=townhalls)
    return act


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_damaged_bc_at_home_dispatches_up_to_3_scvs():
    """残血 BC 在家 → 最多 3 个空闲 SCV 收到 repair 调用（per-instance 断言）。"""
    bc = _make_unit(tag=1, unit_type=UnitTypeId.BATTLECRUISER, pos=(10.0, 10.0), hp_pct=0.50)
    scvs = [
        _make_unit(tag=10 + i, unit_type=UnitTypeId.SCV, pos=(11.0 + i, 10.0), is_repairing=False)
        for i in range(5)
    ]
    act = _make_act(bcs=[bc], scvs=scvs, townhall_pos=[(10.0, 10.0)])
    await act.execute()

    dispatched = [w for w in scvs if w.repair.called]
    assert len(dispatched) == 3, (
        f"应派恰好 3 个 SCV，实际 {len(dispatched)} 个：{[w.tag for w in dispatched]}"
    )
    # per-instance 断言：每个 SCV repair 调用目标正确
    for w in dispatched:
        w.repair.assert_called_once_with(bc)


@pytest.mark.asyncio
async def test_already_3_scvs_repairing_no_extra_dispatch():
    """已有 3 个 SCV 在修此 BC → 不再增派空闲 SCV。"""
    bc = _make_unit(tag=1, unit_type=UnitTypeId.BATTLECRUISER, pos=(10.0, 10.0), hp_pct=0.50)
    repairing_scvs = [
        _make_unit(tag=10 + i, unit_type=UnitTypeId.SCV, is_repairing=True, order_target=1)
        for i in range(3)
    ]
    free_scvs = [
        _make_unit(tag=20 + i, unit_type=UnitTypeId.SCV, is_repairing=False) for i in range(2)
    ]
    act = _make_act(bcs=[bc], scvs=repairing_scvs + free_scvs, townhall_pos=[(10.0, 10.0)])
    await act.execute()

    for w in free_scvs:
        assert not w.repair.called, f"BC 已有 3 个 SCV 修，空闲 SCV {w.tag} 不应被派"


@pytest.mark.asyncio
async def test_full_health_bc_not_repaired():
    """满血 BC（health_percentage≥0.99）→ 跳过，不派 SCV。"""
    bc = _make_unit(tag=1, unit_type=UnitTypeId.BATTLECRUISER, pos=(10.0, 10.0), hp_pct=1.0)
    scvs = [_make_unit(tag=10 + i, unit_type=UnitTypeId.SCV, is_repairing=False) for i in range(3)]
    act = _make_act(bcs=[bc], scvs=scvs, townhall_pos=[(10.0, 10.0)])
    await act.execute()

    for w in scvs:
        assert not w.repair.called, f"满血 BC 不应触发修理，SCV {w.tag} 不该被派"


@pytest.mark.asyncio
async def test_bc_far_from_townhall_not_repaired():
    """BC 离任意 townhall > _HOME_REPAIR_RADIUS → 在家判定失败 → 不修。

    （#583 MUST-FIX D：显式覆盖"远离家的 BC 不派 SCV"场景。）
    Note：释放后残血 BC 能否自己回家由真局验；若 sharpy 主力 plan 带 BC 出去战斗，
    BC 不在家，此 act 不会修它。需补 move(home) 显式引导才能触发修理。
    """
    from vibecraft.bot.auto_combat.terran.bc_raid_act import _HOME_REPAIR_RADIUS

    th_pos = (10.0, 10.0)
    far_x = th_pos[0] + _HOME_REPAIR_RADIUS + 10.0  # 距离 = 25 > 15（明显超出）
    bc = _make_unit(
        tag=1,
        unit_type=UnitTypeId.BATTLECRUISER,
        pos=(far_x, th_pos[1]),
        hp_pct=0.30,
    )
    scvs = [_make_unit(tag=10 + i, unit_type=UnitTypeId.SCV, is_repairing=False) for i in range(3)]
    act = _make_act(bcs=[bc], scvs=scvs, townhall_pos=[th_pos])
    await act.execute()

    for w in scvs:
        assert not w.repair.called, (
            f"BC 在家外（dist={far_x - th_pos[0]:.0f} > _HOME_REPAIR_RADIUS={_HOME_REPAIR_RADIUS}），"
            f"SCV {w.tag} 不应被派"
        )


@pytest.mark.asyncio
async def test_multiple_damaged_bcs_at_home_each_get_scvs():
    """多艘残血在家 BC，每艘各自按需派 SCV（per-BC per-instance 断言）。"""
    bc1 = _make_unit(tag=1, unit_type=UnitTypeId.BATTLECRUISER, pos=(10.0, 10.0), hp_pct=0.40)
    bc2 = _make_unit(tag=2, unit_type=UnitTypeId.BATTLECRUISER, pos=(12.0, 10.0), hp_pct=0.60)
    # 足够的空闲 SCV
    scvs = [
        _make_unit(tag=10 + i, unit_type=UnitTypeId.SCV, pos=(11.0, 11.0), is_repairing=False)
        for i in range(6)
    ]
    act = _make_act(bcs=[bc1, bc2], scvs=scvs, townhall_pos=[(10.0, 10.0)])
    await act.execute()

    # 统计所有 SCV call_args_list 里指向各 BC 的调用次数（per-instance 断言）
    bc1_repair_count = sum(1 for w in scvs for call in w.repair.call_args_list if call[0][0] is bc1)
    bc2_repair_count = sum(1 for w in scvs for call in w.repair.call_args_list if call[0][0] is bc2)

    # per-instance：每艘残血在家 BC 都应获得修理派遣，且不超过上限 3
    assert 1 <= bc1_repair_count <= 3, f"bc1 应 1-3 次 repair 调用，实际 {bc1_repair_count}"
    assert 1 <= bc2_repair_count <= 3, f"bc2 应 1-3 次 repair 调用，实际 {bc2_repair_count}"
