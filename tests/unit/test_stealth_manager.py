"""StealthCellManager 单测。

WP1 覆盖（骨架）：
- create_cell 返回自增 id（1, 2, 3 ...）
- cells 里 state=PENDING，字段与 payload 一致
- stealth_townhall_tags / stealth_worker_tags 并集正确
- remove_cell 生效

WP2 覆盖（建造链 PENDING→BUILDING→MINING）：
- PENDING cell + 有可用 Probe → 触发建造（set_unit_role + order_probe_build）→ BUILDING
- PENDING cell + 无可用 Probe → 保持 PENDING（下一帧重试）
- BUILDING cell + 附近有 ready NEXUS → nexus_tag 回填 + builder 入 worker_tags
  + register_stealth_townhalls 调用 + MINING
- BUILDING cell + 附近无 ready NEXUS → 保持 BUILDING

WP4 覆盖（MINING 本地产线）：
- MINING cell，alive < target，Nexus 存在 → train_probe_at(nexus_tag) 被调
- MINING cell，alive == target → 不调 train
- MINING cell，worker_tags 里有死亡 tag → 被移除，存活计数正确
- MINING cell，Nexus 附近有未认领 Probe → 被认领（role=LLM_CONTROLLED + worker_tags + 采矿令）
- MINING cell，已认领 Probe 不被重复认领
"""

from __future__ import annotations

from vibecraft.bot.facade import FakeFacade, UnitRole
from vibecraft.bot.stealth.cell import StealthState
from vibecraft.bot.stealth.manager import StealthCellManager
from vibecraft.directives.models import StealthMinePayload

# ---------------------------------------------------------------------------
# Mock bot helpers（WP2 BUILDING → MINING 检测用）
# ---------------------------------------------------------------------------


class _BotWithAliveAndProbes:
    """WP4 mock bot：提供 _is_unit_alive + _find_nearby_probes 两个 test hook。

    额外支持 _find_nearby_nexus（为 BUILDING 阶段兼容性保留）。
    """

    def __init__(
        self,
        alive_tags: set[int] | None = None,
        nearby_probes: list[int] | None = None,
    ) -> None:
        self._alive_tags: set[int] = alive_tags if alive_tags is not None else set()
        self._nearby_probes: list[int] = nearby_probes or []

    def _is_unit_alive(self, tag: int) -> bool:
        return tag in self._alive_tags

    def _find_nearby_probes(
        self,
        point: tuple[float, float],
        radius: float,
        exclude_tags: set[int],
    ) -> list[int]:
        return [t for t in self._nearby_probes if t not in exclude_tags]

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        return None  # MINING 阶段不需要 nexus 发现


class _BotWithNexus:
    """mock bot：在指定坐标附近有一个 ready NEXUS（通过 _find_nearby_nexus hook）。"""

    def __init__(self, nexus_tag: int, nx: float, ny: float) -> None:
        self._nexus_tag = nexus_tag
        self._nexus_pos = (nx, ny)

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        dx = self._nexus_pos[0] - point[0]
        dy = self._nexus_pos[1] - point[1]
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < radius:
            return self._nexus_tag
        return None


class _BotNoNexus:
    """mock bot：附近没有 ready NEXUS、也没有任何 Nexus 在建。"""

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        return None

    def _any_nexus_near(self, point: tuple[float, float], radius: float) -> bool:
        return False  # 没有任何 Nexus 在建


def _make_payload(
    x: float = 50.0,
    y: float = 60.0,
    worker_target: int = 16,
    on_attack: str = "flee",
) -> StealthMinePayload:
    return StealthMinePayload(point=(x, y), worker_target=worker_target, on_attack=on_attack)


class TestCreateCell:
    def test_first_cell_id_is_1(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        assert cid == 1

    def test_ids_are_auto_increment(self) -> None:
        m = StealthCellManager()
        ids = [m.create_cell(_make_payload(x=float(i))) for i in range(3)]
        assert ids == [1, 2, 3]

    def test_cells_dict_grows(self) -> None:
        m = StealthCellManager()
        m.create_cell(_make_payload())
        m.create_cell(_make_payload())
        assert len(m.cells) == 2

    def test_cell_state_is_pending(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        assert m.cells[cid].state == StealthState.PENDING

    def test_cell_point_from_payload(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=77.0, y=88.0))
        assert m.cells[cid].point == (77.0, 88.0)

    def test_cell_worker_target_from_payload(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(worker_target=8))
        assert m.cells[cid].worker_target == 8

    def test_cell_on_attack_from_payload(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(on_attack="hold"))
        assert m.cells[cid].on_attack == "hold"

    def test_cell_defaults(self) -> None:
        """初始 nexus_tag / worker_tags / gas_tags / builder_tag 为空。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        cell = m.cells[cid]
        assert cell.nexus_tag is None
        assert cell.worker_tags == set()
        assert cell.gas_tags == set()
        assert cell.builder_tag is None

    def test_three_cells_independent_ids(self) -> None:
        """三个 cell 各自 id 不重复，cell_id 与 cells key 一致。"""
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        c3 = m.create_cell(_make_payload(x=30.0))
        assert len({c1, c2, c3}) == 3
        assert set(m.cells.keys()) == {c1, c2, c3}
        for cid, cell in m.cells.items():
            assert cell.cell_id == cid


class TestStealthTagProperties:
    def test_stealth_townhall_tags_empty(self) -> None:
        """无 cell 时并集为空。"""
        m = StealthCellManager()
        assert m.stealth_townhall_tags == set()

    def test_stealth_townhall_tags_none_nexus(self) -> None:
        """PENDING cell nexus_tag=None → 不进并集。"""
        m = StealthCellManager()
        m.create_cell(_make_payload())
        assert m.stealth_townhall_tags == set()

    def test_stealth_townhall_tags_union(self) -> None:
        """多 cell nexus_tag 合并。"""
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        m.cells[c1].nexus_tag = 100
        m.cells[c2].nexus_tag = 200
        assert m.stealth_townhall_tags == {100, 200}

    def test_stealth_townhall_tags_partial_none(self) -> None:
        """部分 cell nexus_tag=None，只含非 None 的。"""
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        _c2 = m.create_cell(_make_payload(x=20.0))
        m.cells[c1].nexus_tag = 100
        # _c2 保持 None
        assert m.stealth_townhall_tags == {100}

    def test_stealth_pending_base_count(self) -> None:
        """在建/待建（PENDING/BUILDING）的 cell 计数；MINING（Nexus ready）不计（已在 active_bases）。"""
        from vibecraft.bot.stealth.cell import StealthState

        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))  # 默认 PENDING
        c2 = m.create_cell(_make_payload(x=20.0))
        c3 = m.create_cell(_make_payload(x=30.0))
        m.cells[c1].state = StealthState.PENDING
        m.cells[c2].state = StealthState.BUILDING
        m.cells[c3].state = StealthState.MINING  # ready → 不计
        assert m.stealth_pending_base_count == 2, "PENDING+BUILDING=2，MINING 不计"

    def test_stealth_townhall_tags_includes_gas(self) -> None:
        """2026-06-12 修倒灌：stealth_townhall_tags 必须含偷矿 assimilator(gas_tags)，
        否则偷矿气矿不被 FENCE 排除 → 主矿农民被派去偷矿基地采气（倒灌）。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=10.0))
        m.cells[cid].nexus_tag = 100
        m.cells[cid].gas_tags = {501, 502}  # 两个偷矿 assimilator
        assert m.stealth_townhall_tags == {100, 501, 502}, (
            "FENCE 集必须含 Nexus + assimilator，否则主矿农民采气倒灌"
        )

    def test_stealth_worker_tags_empty(self) -> None:
        m = StealthCellManager()
        assert m.stealth_worker_tags == set()

    def test_stealth_worker_tags_union(self) -> None:
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        m.cells[c1].worker_tags = {101, 102}
        m.cells[c2].worker_tags = {201}
        assert m.stealth_worker_tags == {101, 102, 201}

    def test_stealth_worker_tags_no_overlap(self) -> None:
        """两个 cell worker_tags 不重叠（业务约束，单测验并集不引入重叠）。"""
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        m.cells[c1].worker_tags = {10, 20}
        m.cells[c2].worker_tags = {30, 40}
        union = m.stealth_worker_tags
        assert len(union) == 4


class TestRemoveCell:
    def test_remove_existing(self) -> None:
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        m.remove_cell(cid)
        assert cid not in m.cells

    def test_remove_nonexistent_no_error(self) -> None:
        """删不存在的 id 不抛异常。"""
        m = StealthCellManager()
        m.remove_cell(999)  # should not raise

    def test_remove_one_of_many(self) -> None:
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        c3 = m.create_cell(_make_payload(x=30.0))
        m.remove_cell(c2)
        assert c2 not in m.cells
        assert c1 in m.cells
        assert c3 in m.cells

    def test_properties_after_remove(self) -> None:
        """remove 后 stealth_townhall_tags 也随之减少。"""
        m = StealthCellManager()
        c1 = m.create_cell(_make_payload(x=10.0))
        c2 = m.create_cell(_make_payload(x=20.0))
        m.cells[c1].nexus_tag = 100
        m.cells[c2].nexus_tag = 200
        m.remove_cell(c1)
        assert m.stealth_townhall_tags == {200}


class TestOnTick:
    def test_on_tick_does_not_raise_no_probe(self) -> None:
        """PENDING cell + 无可用 Probe：on_tick 不 raise，无副作用。"""
        m = StealthCellManager()
        m.create_cell(_make_payload())
        # FakeFacade 无 selector_stub["Probe"] → resolve_selector 返回 [] → 保持 PENDING
        m.on_tick(_BotNoNexus(), FakeFacade(), 0.0)

    def test_on_tick_no_probe_keeps_pending(self) -> None:
        """无可用 Probe 时 PENDING cell 状态不变。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        m.on_tick(_BotNoNexus(), FakeFacade(), 0.0)
        assert m.cells[cid].state == StealthState.PENDING


# ---------------------------------------------------------------------------
# WP2 状态机测试（PENDING → BUILDING → MINING）
# ---------------------------------------------------------------------------


class TestOnTickStateMachine:
    """WP2 建造链状态机迁移单测（全 mock，不起 SC2）。"""

    # ------------------------------------------------------------------
    # PENDING → BUILDING
    # ------------------------------------------------------------------

    def test_pending_with_probe_transitions_to_building(self) -> None:
        """PENDING cell + 有可用 Probe → on_tick → BUILDING。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [42]  # 可用 Probe tag=42

        m.on_tick(_BotNoNexus(), facade, 100.0)

        assert m.cells[cid].state == StealthState.BUILDING

    def test_pending_records_builder_tag(self) -> None:
        """PENDING → BUILDING 时 builder_tag 设为所选 Probe。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [99]

        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert m.cells[cid].builder_tag == 99

    def test_pending_sets_probe_role_llm_controlled(self) -> None:
        """PENDING → BUILDING：facade.set_unit_role(probe, LLM_CONTROLLED) 被调。"""
        m = StealthCellManager()
        m.create_cell(_make_payload())

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [77]

        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert facade.unit_roles.get(77) == UnitRole.LLM_CONTROLLED

    def test_pending_calls_order_probe_build_nexus(self) -> None:
        """PENDING → BUILDING：facade.order_probe_build(probe, 'Nexus', point) 被调。"""
        m = StealthCellManager()
        m.create_cell(_make_payload(x=30.0, y=40.0))

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [55]

        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert len(facade.proxy_build_orders) == 1
        order = facade.proxy_build_orders[0]
        assert order["probe"] == 55
        assert order["structure"] == "Nexus"
        assert order["point"] == (30.0, 40.0)

    def test_pending_no_probe_stays_pending(self) -> None:
        """PENDING + 无可用 Probe → 保持 PENDING，不崩，无副作用。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())

        facade = FakeFacade()  # selector_stub 空 → resolve_selector("Probe") == []

        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert m.cells[cid].state == StealthState.PENDING
        assert m.cells[cid].builder_tag is None
        assert facade.proxy_build_orders == []

    def test_building_reissues_every_frame_until_nexus_pending(self) -> None:
        """BUILDING：没 Nexus 在建时每帧重发把 builder 推过去（同 probe/cache_key/point）。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [11]

        m.on_tick(_BotNoNexus(), facade, 0.0)  # PENDING→BUILDING，首发
        assert m.cells[cid].state == StealthState.BUILDING
        assert len(facade.proxy_build_orders) == 1

        m.on_tick(_BotNoNexus(), facade, 1.0)  # 无 Nexus 在建 → 重发
        assert m.cells[cid].builder_tag == 11  # 没换农民
        assert len(facade.proxy_build_orders) == 2
        first, second = facade.proxy_build_orders[0], facade.proxy_build_orders[1]
        assert second["probe"] == first["probe"] == 11
        assert second["cache_key"] == first["cache_key"] == cid
        assert second["point"] == first["point"]

    def test_building_stops_reissue_when_nexus_pending(self) -> None:
        """BUILDING：一旦有 Nexus 在建（warp-in）→ 停止重发（防建第二个）。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [11]

        m.on_tick(_BotNoNexus(), facade, 0.0)  # 首发，进 BUILDING
        assert len(facade.proxy_build_orders) == 1

        # bot：有 pending Nexus（_any_nexus_near True）但还没 ready（_find_nearby_nexus None）
        class _BotNexusPending(_BotNoNexus):  # type: ignore[misc, valid-type]
            def _any_nexus_near(self, point: tuple[float, float], radius: float) -> bool:
                return True

        m.on_tick(_BotNexusPending(), facade, 1.0)
        assert len(facade.proxy_build_orders) == 1  # 停发，没建第二个
        assert m.cells[cid].state == StealthState.BUILDING

    def test_building_reclaims_when_builder_dies(self) -> None:
        """builder 在途中阵亡 → 回退 PENDING 重新认领农民续建（不卡死）。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload())
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [11]

        m.on_tick(_BotNoNexus(), facade, 0.0)
        assert m.cells[cid].state == StealthState.BUILDING

        # builder 11 阵亡的 bot：_is_unit_alive(11) → False
        class _BotBuilderDead(_BotNoNexus):  # type: ignore[misc, valid-type]
            def _is_unit_alive(self, tag: int) -> bool:
                return False

        m.on_tick(_BotBuilderDead(), facade, 1.0)
        assert m.cells[cid].state == StealthState.PENDING
        assert m.cells[cid].builder_tag is None

    def test_pending_snaps_point_to_nearest_expansion(self) -> None:
        """PENDING：cell.point 先吸附到最近 expansion，再用吸附后的点建造。

        玩家点 (75,150) 常落在无矿/不可建处 → SC2 拒建。吸附到 expansion 才是
        合法采矿落点（真机自验定位的根因修复）。
        """
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=75.0, y=150.0))

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [11]
        facade.expansion_snap = (60.0, 64.0)  # 模拟最近 expansion

        m.on_tick(_BotNoNexus(), facade, 0.0)

        # cell.point 已吸附；建造令下在吸附后的点
        assert m.cells[cid].point == (60.0, 64.0)
        assert m.cells[cid].point_snapped is True
        assert facade.proxy_build_orders[0]["point"] == (60.0, 64.0)

    def test_pending_no_snap_when_no_expansion(self) -> None:
        """nearest_expansion 返回 None（无 expansion 数据）→ 保留原 point，不卡死。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=75.0, y=150.0))

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [11]
        # FakeFacade.nearest_expansion 默认回显 point；这里显式造 None 路径
        facade.nearest_expansion = lambda point: None  # type: ignore[method-assign]

        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert m.cells[cid].point == (75.0, 150.0)  # 原点保留
        assert facade.proxy_build_orders[0]["point"] == (75.0, 150.0)

    # ------------------------------------------------------------------
    # BUILDING → MINING
    # ------------------------------------------------------------------

    def test_building_nexus_settled_transitions_to_mining(self) -> None:
        """BUILDING cell + 附近有 ready NEXUS → on_tick → MINING。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 99

        bot = _BotWithNexus(nexus_tag=777, nx=50.0, ny=60.0)  # 正好在 point 上
        facade = FakeFacade()
        m.on_tick(bot, facade, 200.0)

        assert m.cells[cid].state == StealthState.MINING

    def test_building_backfills_nexus_tag(self) -> None:
        """BUILDING → MINING：cell.nexus_tag 被回填。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 99

        bot = _BotWithNexus(nexus_tag=777, nx=51.0, ny=59.0)  # 1.4 格内
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert m.cells[cid].nexus_tag == 777

    def test_building_builder_joins_worker_tags(self) -> None:
        """BUILDING → MINING：builder 农民加入 cell.worker_tags。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 88

        bot = _BotWithNexus(nexus_tag=555, nx=50.0, ny=60.0)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert 88 in m.cells[cid].worker_tags

    def test_building_calls_register_stealth_townhalls(self) -> None:
        """BUILDING → MINING：facade.register_stealth_townhalls 被调，含新 nexus_tag。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 10

        nexus_tag = 333
        bot = _BotWithNexus(nexus_tag=nexus_tag, nx=50.0, ny=60.0)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert nexus_tag in facade.stealth_townhall_tags

    def test_building_no_nexus_stays_building(self) -> None:
        """BUILDING cell + 附近无 ready NEXUS → 保持 BUILDING。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 99

        facade = FakeFacade()
        m.on_tick(_BotNoNexus(), facade, 0.0)

        assert m.cells[cid].state == StealthState.BUILDING
        assert m.cells[cid].nexus_tag is None

    def test_building_nexus_outside_radius_stays_building(self) -> None:
        """cell.point 附近超出半径的 NEXUS 不触发 settle（误判保护）。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(x=50.0, y=60.0))
        cell = m.cells[cid]
        cell.state = StealthState.BUILDING
        cell.builder_tag = 99

        # NEXUS 在 (80.0, 60.0)，距 point 30 格，超出 _NEXUS_SETTLE_RADIUS=8
        bot = _BotWithNexus(nexus_tag=999, nx=80.0, ny=60.0)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert m.cells[cid].state == StealthState.BUILDING
        assert m.cells[cid].nexus_tag is None

    # ------------------------------------------------------------------
    # MINING → 保持 MINING（WP4 产线正常运行时 state 不变）
    # ------------------------------------------------------------------

    def test_mining_state_unchanged(self) -> None:
        """MINING cell on_tick → state 仍为 MINING，nexus_tag 保持。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload(worker_target=1))
        cell = m.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = 555
        cell.builder_tag = 99
        cell.worker_tags = {99}

        # bot：tag 99 存活；无附近未认领 Probe
        bot = _BotWithAliveAndProbes(alive_tags={99}, nearby_probes=[])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert m.cells[cid].state == StealthState.MINING
        assert m.cells[cid].nexus_tag == 555


# ---------------------------------------------------------------------------
# WP4：MINING 本地产线测试
# ---------------------------------------------------------------------------


class TestMiningLocalProduction:
    """WP4 MINING 态本地产线单测（全 mock，不起 SC2）。"""

    def _make_mining_cell(
        self,
        m: StealthCellManager,
        nexus_tag: int = 555,
        worker_tags: set[int] | None = None,
        worker_target: int = 4,
    ) -> int:
        """创建已处于 MINING 状态的 cell（直接设 state/nexus_tag/worker_tags）。"""
        cid = m.create_cell(_make_payload(worker_target=worker_target))
        cell = m.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = nexus_tag
        cell.worker_tags = set(worker_tags) if worker_tags is not None else set()
        return cid

    # ------------------------------------------------------------------
    # 补农民（train_probe_at）
    # ------------------------------------------------------------------

    def test_train_called_when_alive_below_target(self) -> None:
        """MINING cell，alive < target，Nexus 存在 → train_probe_at(nexus_tag) 被调。"""
        m = StealthCellManager()
        nexus_tag = 555
        alive_tags = {101, 102}  # 2 个存活
        self._make_mining_cell(m, nexus_tag=nexus_tag, worker_tags=alive_tags, worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=alive_tags, nearby_probes=[])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert nexus_tag in facade.train_probe_calls, (
            f"train_probe_at({nexus_tag}) 未被调用；实际 train_probe_calls={facade.train_probe_calls}"
        )

    def test_no_train_when_alive_equals_target(self) -> None:
        """MINING cell，alive == target → 不调 train_probe_at。"""
        m = StealthCellManager()
        nexus_tag = 555
        alive_tags = {101, 102, 103, 104}  # 4 个存活 = target
        self._make_mining_cell(m, nexus_tag=nexus_tag, worker_tags=alive_tags, worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=alive_tags, nearby_probes=[])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert facade.train_probe_calls == [], (
            f"alive==target 时不应调 train，实际 calls={facade.train_probe_calls}"
        )

    def test_no_train_when_alive_exceeds_target(self) -> None:
        """MINING cell，alive > target → 不调 train（过满保护）。"""
        m = StealthCellManager()
        nexus_tag = 555
        alive_tags = {101, 102, 103, 104, 105}  # 5 > target=4
        self._make_mining_cell(m, nexus_tag=nexus_tag, worker_tags=alive_tags, worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=alive_tags, nearby_probes=[])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert facade.train_probe_calls == []

    # ---- 出生即认领 adopt_newborn（核心修复：赶在 DistributeWorkers 前）----

    def test_adopt_newborn_near_nexus_claimed(self) -> None:
        """农民生在 MINING cell 的 Nexus 旁（cell.point 半径内）→ 立即认领。"""
        m = StealthCellManager()
        cid = self._make_mining_cell(m, worker_tags=set(), worker_target=4)  # point=(50,60)
        facade = FakeFacade()

        adopted = m.adopt_newborn(200, (50.0, 61.0), facade)

        assert adopted is True
        assert 200 in m.cells[cid].worker_tags
        assert facade.unit_roles.get(200) == UnitRole.LLM_CONTROLLED
        assert (200, (50.0, 60.0)) in facade.worker_gather_orders

    def test_adopt_newborn_far_not_claimed(self) -> None:
        """农民生在远处（半径外，如主矿产的）→ 不认领（返回 False，不动它）。"""
        m = StealthCellManager()
        cid = self._make_mining_cell(m, worker_tags=set(), worker_target=4)  # point=(50,60)
        facade = FakeFacade()

        adopted = m.adopt_newborn(201, (90.0, 90.0), facade)

        assert adopted is False
        assert 201 not in m.cells[cid].worker_tags
        assert facade.unit_roles.get(201) is None

    def test_adopt_newborn_full_cell_rejected(self) -> None:
        """cell 已满 live_total_target（采矿+采气总额）→ 不再收（即便生在旁边）。"""
        m = StealthCellManager()
        full = {1, 2, 3, 4}
        cid = self._make_mining_cell(m, worker_tags=set(full), worker_target=4)
        m.cells[cid].live_total_target = 4  # _tick_mining 会刷新；此处直接设满
        facade = FakeFacade()

        adopted = m.adopt_newborn(200, (50.0, 60.0), facade)

        assert adopted is False
        assert m.cells[cid].worker_tags == full

    # ------------------------------------------------------------------
    # 死亡农民清理
    # ------------------------------------------------------------------

    def test_dead_tag_removed_from_worker_tags(self) -> None:
        """worker_tags 里有死亡 tag → on_tick 后从 worker_tags 移除。"""
        m = StealthCellManager()
        alive_tags = {101, 102}
        dead_tag = 999
        cid = self._make_mining_cell(m, worker_tags={*alive_tags, dead_tag}, worker_target=10)

        bot = _BotWithAliveAndProbes(alive_tags=alive_tags, nearby_probes=[])
        facade = FakeFacade()
        # grace-period（2026-06-11）：首帧仅标记消失，连续消失超 _DEAD_GRACE_S(4s) 才真判死。
        m.on_tick(bot, facade, 0.0)
        assert dead_tag in m.cells[cid].worker_tags, (
            "首帧消失只标记、不立即删（grace 防误删采气农民）"
        )
        m.on_tick(bot, facade, 5.0)  # 已连续消失 5s > 4s grace → 判死

        assert dead_tag not in m.cells[cid].worker_tags, "死亡 tag 应被从 worker_tags 移除"
        assert alive_tags.issubset(m.cells[cid].worker_tags), "存活 tag 不应被移除"

    def test_alive_count_correct_after_pruning(self) -> None:
        """死亡清理后存活计数（len worker_tags）正确。"""
        m = StealthCellManager()
        alive_tags = {10, 20, 30}
        dead_tags = {40, 50}
        cid = self._make_mining_cell(m, worker_tags={*alive_tags, *dead_tags}, worker_target=10)

        bot = _BotWithAliveAndProbes(alive_tags=alive_tags, nearby_probes=[])
        facade = FakeFacade()
        # grace-period：首帧标记，过 grace 才删
        m.on_tick(bot, facade, 0.0)
        m.on_tick(bot, facade, 5.0)

        assert len(m.cells[cid].worker_tags) == 3, (
            f"清理后应剩 3 存活，实际 worker_tags={m.cells[cid].worker_tags}"
        )

    # ------------------------------------------------------------------
    # 认领新孵化农民
    # ------------------------------------------------------------------

    def test_unclaimed_probe_gets_role_llm_controlled(self) -> None:
        """Nexus 附近有未认领 Probe → facade.set_unit_role(tag, LLM_CONTROLLED) 被调。"""
        m = StealthCellManager()
        new_probe_tag = 200
        self._make_mining_cell(m, worker_tags=set(), worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=set(), nearby_probes=[new_probe_tag])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert facade.unit_roles.get(new_probe_tag) == UnitRole.LLM_CONTROLLED, (
            f"新认领 Probe {new_probe_tag} 应被设为 LLM_CONTROLLED"
        )

    def test_unclaimed_probe_added_to_worker_tags(self) -> None:
        """认领后 Probe tag 加入本 cell.worker_tags。"""
        m = StealthCellManager()
        new_probe_tag = 200
        cid = self._make_mining_cell(m, worker_tags=set(), worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=set(), nearby_probes=[new_probe_tag])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert new_probe_tag in m.cells[cid].worker_tags, (
            f"认领后 {new_probe_tag} 应在 worker_tags，实际={m.cells[cid].worker_tags}"
        )

    def test_unclaimed_probe_gets_gather_order(self) -> None:
        """认领后 order_worker_gather(probe_tag, cell.point) 被调。"""
        m = StealthCellManager()
        new_probe_tag = 200
        pt = (50.0, 60.0)
        cid = m.create_cell(_make_payload(x=pt[0], y=pt[1], worker_target=4))
        m.cells[cid].state = StealthState.MINING
        m.cells[cid].nexus_tag = 555

        bot = _BotWithAliveAndProbes(alive_tags=set(), nearby_probes=[new_probe_tag])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert (new_probe_tag, pt) in facade.worker_gather_orders, (
            f"应调 order_worker_gather({new_probe_tag}, {pt})，实际={facade.worker_gather_orders}"
        )

    def test_already_claimed_probe_not_reclaimed(self) -> None:
        """已在 worker_tags 的 Probe tag 不被重复认领（exclude_tags 排除）。"""
        m = StealthCellManager()
        existing_tag = 101
        cid = self._make_mining_cell(m, worker_tags={existing_tag}, worker_target=4)

        # bot 的 nearby_probes 返回该 tag（模拟仍在附近），但 exclude_tags 应排除它
        bot = _BotWithAliveAndProbes(alive_tags={existing_tag}, nearby_probes=[existing_tag])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # set_unit_role 不应被调（tag 已在 worker_tags，_find_unclaimed_probes_near 应排除）
        assert (
            facade.unit_roles.get(existing_tag) is None or existing_tag in m.cells[cid].worker_tags
        )
        # 关键：gather_orders 为空（已认领 tag 不应再下认领令）
        assert facade.worker_gather_orders == [], f"已认领 {existing_tag} 不应再下采矿认领令"

    def test_claim_capped_at_worker_target(self) -> None:
        """认领数封顶 worker_target：已有 3、target=4、附近 5 个未认领 → 只认领 1（封在 4）。

        长局自验定位的 bug：认领无上限 → 涨到 43 远超 target，全堆一个矿。
        """
        m = StealthCellManager()
        existing = {1, 2, 3}
        cid = self._make_mining_cell(m, worker_tags=set(existing), worker_target=4)

        nearby = [201, 202, 203, 204, 205]  # 5 个未认领，但只剩 1 个 slot
        bot = _BotWithAliveAndProbes(alive_tags=existing, nearby_probes=nearby)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # 只认领 1 个，worker_tags 封在 target=4
        assert len(m.cells[cid].worker_tags) == 4, (
            f"认领应封顶在 target=4，实际={m.cells[cid].worker_tags}"
        )
        assert len(facade.worker_gather_orders) == 1, (
            f"只应下 1 条采矿认领令，实际={facade.worker_gather_orders}"
        )

    def test_claim_skipped_when_target_reached(self) -> None:
        """已达 target → 不再认领（slots_left<=0），即便附近有未认领农民。"""
        m = StealthCellManager()
        existing = {1, 2, 3, 4}
        cid = self._make_mining_cell(m, worker_tags=set(existing), worker_target=4)

        bot = _BotWithAliveAndProbes(alive_tags=existing, nearby_probes=[201, 202])
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert m.cells[cid].worker_tags == existing  # 没新增
        assert facade.worker_gather_orders == []


# ---------------------------------------------------------------------------
# WP5：MINING 受击/Nexus 摧毁 → 撤销 stealth 地位
# ---------------------------------------------------------------------------


class _BotWP5:
    """WP5 单 cell mock bot：控制 _enemy_near / _is_structure_alive / _is_unit_alive。

    同时提供 WP4 产线所需的 _find_nearby_probes / _find_nearby_nexus hook。
    """

    def __init__(
        self,
        enemy_near: bool = False,
        nexus_alive: bool = True,
        alive_tags: set[int] | None = None,
    ) -> None:
        self._enemy_near_result = enemy_near
        self._nexus_alive_result = nexus_alive
        self._alive_tags: set[int] = alive_tags if alive_tags is not None else set()

    def _enemy_near(self, point: tuple[float, float], radius: float) -> bool:
        return self._enemy_near_result

    def _is_structure_alive(self, tag: int) -> bool:
        return self._nexus_alive_result

    def _is_unit_alive(self, tag: int) -> bool:
        return tag in self._alive_tags

    def _find_nearby_probes(
        self,
        point: tuple[float, float],
        radius: float,
        exclude_tags: set[int],
    ) -> list[int]:
        return []

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        return None


class _BotWP5MultiCell:
    """WP5 多 cell mock：A 被攻击（attacked_point），B 不被攻击。"""

    def __init__(self, attacked_point: tuple[float, float]) -> None:
        self._attacked_point = attacked_point

    def _enemy_near(self, point: tuple[float, float], radius: float) -> bool:
        dx = point[0] - self._attacked_point[0]
        dy = point[1] - self._attacked_point[1]
        return (dx * dx + dy * dy) < 1.0  # 只有 attacked_point 附近返回 True

    def _is_structure_alive(self, tag: int) -> bool:
        return True  # 两个 Nexus 都存活

    def _is_unit_alive(self, tag: int) -> bool:
        return True  # 所有农民存活

    def _find_nearby_probes(
        self, point: tuple[float, float], radius: float, exclude_tags: set[int]
    ) -> list[int]:
        return []

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        return None


def _make_mining_cell_wp5(
    m: StealthCellManager,
    nexus_tag: int,
    worker_tags: set[int],
    on_attack: str = "flee",
    point: tuple[float, float] = (50.0, 60.0),
    worker_target: int = 16,
) -> int:
    """创建处于 MINING 状态的 cell（WP5 测试专用）。"""
    cid = m.create_cell(
        _make_payload(x=point[0], y=point[1], worker_target=worker_target, on_attack=on_attack)
    )
    cell = m.cells[cid]
    cell.state = StealthState.MINING
    cell.nexus_tag = nexus_tag
    cell.worker_tags = set(worker_tags)
    return cid


class TestMiningReleaseOnAttack:
    """WP5：MINING 受击/Nexus 摧毁 → 撤销 stealth 地位（全 mock，不起 SC2）。"""

    # ------------------------------------------------------------------
    # 测试 1：受击（on_attack=flee）→ RELEASED
    # ------------------------------------------------------------------

    def test_enemy_near_flee_transitions_to_released(self) -> None:
        """MINING cell，敌近，on_attack=flee → state=RELEASED。"""
        m = StealthCellManager()
        worker_tags = {10, 20}
        cid = _make_mining_cell_wp5(m, nexus_tag=100, worker_tags=worker_tags, on_attack="flee")

        bot = _BotWP5(enemy_near=True, nexus_alive=True, alive_tags=worker_tags)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # cell 已从 manager 移除
        assert cid not in m.cells, "受击后 cell 应被移除"

    def test_enemy_near_flee_calls_release_unit_role_for_each_worker(self) -> None:
        """受击释放：每个 worker 的 release_unit_role 被调用。"""
        m = StealthCellManager()
        worker_tags = {10, 20}
        _make_mining_cell_wp5(m, nexus_tag=100, worker_tags=worker_tags, on_attack="flee")

        bot = _BotWP5(enemy_near=True, nexus_alive=True, alive_tags=worker_tags)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        released = {c.args[0] for c in facade.calls if c.method == "release_unit_role"}
        assert worker_tags.issubset(released), (
            f"worker_tags {worker_tags} 应全部被 release_unit_role，实际 released={released}"
        )

    def test_enemy_near_flee_register_townhalls_excludes_nexus(self) -> None:
        """受击释放后，register_stealth_townhalls 传的集合不含该 Nexus。"""
        m = StealthCellManager()
        nexus_tag = 100
        _make_mining_cell_wp5(m, nexus_tag=nexus_tag, worker_tags={10, 20}, on_attack="flee")

        bot = _BotWP5(enemy_near=True, nexus_alive=True, alive_tags={10, 20})
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # register_stealth_townhalls 被调，且传的集合不含被释放 cell 的 nexus_tag
        assert any(c.method == "register_stealth_townhalls" for c in facade.calls), (
            "应调 register_stealth_townhalls（解除 FENCE）"
        )
        assert nexus_tag not in facade.stealth_townhall_tags, (
            f"nexus_tag={nexus_tag} 应从 stealth_townhall_tags 中移除"
        )

    # ------------------------------------------------------------------
    # 测试 2：on_attack=hold，敌近 → 不释放
    # ------------------------------------------------------------------

    def test_on_attack_hold_enemy_near_does_not_release(self) -> None:
        """on_attack=hold 时，即便敌近也不触发 RELEASED（硬守）。"""
        m = StealthCellManager()
        worker_tags = {10, 20}
        cid = _make_mining_cell_wp5(m, nexus_tag=100, worker_tags=worker_tags, on_attack="hold")

        bot = _BotWP5(enemy_near=True, nexus_alive=True, alive_tags=worker_tags)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # cell 仍在
        assert cid in m.cells, "on_attack=hold 时不应释放 cell"
        assert m.cells[cid].state == StealthState.MINING, "状态应保持 MINING"
        # 不调 release
        released = [c for c in facade.calls if c.method == "release_unit_role"]
        assert released == [], f"on_attack=hold 时不应调 release_unit_role，实际={released}"

    # ------------------------------------------------------------------
    # 测试 3：Nexus 被摧毁 → DESTROYED
    # ------------------------------------------------------------------

    def test_nexus_destroyed_transitions_to_destroyed(self) -> None:
        """Nexus 不在 bot.structures → state=DESTROYED，worker 释放，cell 出局，FENCE 更新。"""
        m = StealthCellManager()
        worker_tags = {30, 40}
        nexus_tag = 200
        cid = _make_mining_cell_wp5(
            m, nexus_tag=nexus_tag, worker_tags=worker_tags, on_attack="flee"
        )

        # nexus_alive=False 模拟被摧毁
        bot = _BotWP5(enemy_near=False, nexus_alive=False, alive_tags=worker_tags)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # cell 移除
        assert cid not in m.cells, "Nexus 摧毁后 cell 应被移除"
        # workers 释放
        released = {c.args[0] for c in facade.calls if c.method == "release_unit_role"}
        assert worker_tags.issubset(released), (
            f"worker_tags {worker_tags} 应被 release_unit_role，实际={released}"
        )
        # FENCE 更新
        assert nexus_tag not in facade.stealth_townhall_tags, (
            f"nexus_tag={nexus_tag} 应从 stealth_townhall_tags 中移除"
        )

    # ------------------------------------------------------------------
    # 测试 4：无敌、Nexus 存活 → 正常 MINING，不误释放
    # ------------------------------------------------------------------

    def test_no_enemy_nexus_alive_stays_mining(self) -> None:
        """无敌方、Nexus 在 → 保持 MINING，不调 release_unit_role。"""
        m = StealthCellManager()
        worker_tags = {50, 60}
        cid = _make_mining_cell_wp5(
            m,
            nexus_tag=300,
            worker_tags=worker_tags,
            on_attack="flee",
            worker_target=len(worker_tags),
        )

        bot = _BotWP5(enemy_near=False, nexus_alive=True, alive_tags=worker_tags)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        assert cid in m.cells, "无敌时不应释放 cell"
        assert m.cells[cid].state == StealthState.MINING, "无敌时状态应保持 MINING"
        released = [c for c in facade.calls if c.method == "release_unit_role"]
        assert released == [], f"无敌时不应调 release_unit_role，实际={released}"

    # ------------------------------------------------------------------
    # 测试 5：多 cell，A 受击释放，B 不受影响
    # ------------------------------------------------------------------

    def test_multi_cell_only_attacked_cell_released(self) -> None:
        """cell A 被攻击释放，cell B 不受影响；register 传的集合仍含 B 的 nexus_tag。"""
        m = StealthCellManager()

        point_a = (10.0, 10.0)
        point_b = (50.0, 50.0)
        nexus_a, nexus_b = 100, 200

        cid_a = _make_mining_cell_wp5(
            m, nexus_tag=nexus_a, worker_tags={11}, on_attack="flee", point=point_a
        )
        cid_b = _make_mining_cell_wp5(
            m,
            nexus_tag=nexus_b,
            worker_tags={21},
            on_attack="flee",
            point=point_b,
            worker_target=1,  # alive==target，不触发 train
        )

        # 只有 point_a 附近有敌
        bot = _BotWP5MultiCell(attacked_point=point_a)
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)

        # A 已释放
        assert cid_a not in m.cells, "cell A 受击后应被移除"
        # B 仍在
        assert cid_b in m.cells, "cell B 未受击，应保留"
        assert m.cells[cid_b].state == StealthState.MINING, "cell B 状态应保持 MINING"
        # register 传的集合仍含 B 的 nexus_tag（不含 A 的）
        assert nexus_b in facade.stealth_townhall_tags, f"register 后集合应含 nexus_b={nexus_b}"
        assert nexus_a not in facade.stealth_townhall_tags, (
            f"register 后集合不应含已释放的 nexus_a={nexus_a}"
        )


# ---------------------------------------------------------------------------
# WP6：多 cell 并行验证（无串台）
# ---------------------------------------------------------------------------


class _BotMultiStateMultiCell:
    """WP6 多状态多 cell mock bot。

    - alive_tags：仍存活的单位 tag 集合
    - nexus_map：坐标 (x, y) → nexus_tag（精确匹配，不做距离判断）
    - nearby_probes_map：坐标 (x, y) → 附近未认领 Probe tag 列表
    - enemy_near_points：触发受击的 point 集合

    所有 hook 按坐标精确 dispatch；用 int tuple 精度避免浮点问题。
    """

    def __init__(
        self,
        alive_tags: set[int] | None = None,
        nexus_map: dict[tuple[float, float], int] | None = None,
        nearby_probes_map: dict[tuple[float, float], list[int]] | None = None,
        enemy_near_points: set[tuple[float, float]] | None = None,
    ) -> None:
        self._alive_tags: set[int] = alive_tags or set()
        self._nexus_map: dict[tuple[float, float], int] = nexus_map or {}
        self._nearby_probes_map: dict[tuple[float, float], list[int]] = nearby_probes_map or {}
        self._enemy_near_points: set[tuple[float, float]] = enemy_near_points or set()

    def _is_unit_alive(self, tag: int) -> bool:
        return tag in self._alive_tags

    def _is_structure_alive(self, tag: int) -> bool:
        return True  # WP6 并行测不需要结构摧毁

    def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
        return self._nexus_map.get(point)

    def _find_nearby_probes(
        self,
        point: tuple[float, float],
        radius: float,
        exclude_tags: set[int],
    ) -> list[int]:
        all_nearby = self._nearby_probes_map.get(point, [])
        return [t for t in all_nearby if t not in exclude_tags]

    def _enemy_near(self, point: tuple[float, float], radius: float) -> bool:
        return point in self._enemy_near_points


class TestMultiCellParallelNoConflict:
    """WP6：多 cell 并行同帧 on_tick，各自独立，无串台。"""

    # ------------------------------------------------------------------
    # 测试 1：同时 create 3 个 cell，cell_id 1/2/3 不重复，point 各自正确
    # ------------------------------------------------------------------

    def test_three_cells_unique_ids_and_points(self) -> None:
        """同时 create 3 个不同 point 的 cell → cell_id 1/2/3 不重复，point 与 payload 一致。"""
        m = StealthCellManager()
        points = [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)]
        ids = [m.create_cell(_make_payload(x=p[0], y=p[1])) for p in points]

        # id 不重复
        assert len(set(ids)) == 3, f"3 个 cell_id 应各不相同，实际={ids}"
        assert set(ids) == {1, 2, 3}
        # 每个 cell point 正确
        for cid, pt in zip(ids, points, strict=False):
            assert m.cells[cid].point == pt, (
                f"cell_id={cid} point 应为 {pt}，实际={m.cells[cid].point}"
            )

    # ------------------------------------------------------------------
    # 测试 2：cell A 在 BUILDING，cell B 在 MINING，同帧 on_tick → 各走各分支
    # ------------------------------------------------------------------

    def test_building_and_mining_tick_independently(self) -> None:
        """cell A BUILDING（等 Nexus）+ cell B MINING（有农民，等补员），同帧 on_tick。

        期望：
        - A 因 point 附近有 Nexus → 进入 MINING
        - B 保持 MINING（alive==target 不补员）
        - A/B worker_tags 不重叠（各自独立农民集合）
        """
        m = StealthCellManager()

        # Cell A：BUILDING，point=(10.0,20.0)，builder=tag 101
        point_a = (10.0, 20.0)
        cid_a = m.create_cell(_make_payload(x=point_a[0], y=point_a[1], worker_target=4))
        cell_a = m.cells[cid_a]
        cell_a.state = StealthState.BUILDING
        cell_a.builder_tag = 101

        # Cell B：MINING，point=(50.0,60.0)，worker_tags={201,202}，target=2（alive==target）
        point_b = (50.0, 60.0)
        nexus_b = 999
        cid_b = m.create_cell(_make_payload(x=point_b[0], y=point_b[1], worker_target=2))
        cell_b = m.cells[cid_b]
        cell_b.state = StealthState.MINING
        cell_b.nexus_tag = nexus_b
        cell_b.worker_tags = {201, 202}

        # bot：A 附近有 Nexus（tag=888）；B 所有农民存活；无敌；无额外未认领 probe
        bot = _BotMultiStateMultiCell(
            alive_tags={101, 201, 202},
            nexus_map={point_a: 888},  # A 附近 Nexus settle 了
            nearby_probes_map={},
            enemy_near_points=set(),
        )
        facade = FakeFacade()
        m.on_tick(bot, facade, 300.0)

        # A 应进入 MINING
        assert m.cells[cid_a].state == StealthState.MINING, (
            f"A 附近 Nexus settle 后 A 应进 MINING，实际={m.cells[cid_a].state}"
        )
        assert m.cells[cid_a].nexus_tag == 888, "A.nexus_tag 应回填为 888"
        assert 101 in m.cells[cid_a].worker_tags, "A.builder 应加入 A 的 worker_tags"

        # B 仍 MINING，worker_tags 不变
        assert cid_b in m.cells, "B 应保持存在"
        assert m.cells[cid_b].state == StealthState.MINING, "B 应保持 MINING"
        assert m.cells[cid_b].worker_tags == {201, 202}, "B.worker_tags 不应被 A 的操作影响"

        # A/B worker_tags 不重叠
        overlap = m.cells[cid_a].worker_tags & m.cells[cid_b].worker_tags
        assert overlap == set(), f"A/B worker_tags 不应重叠，overlap={overlap}"

    # ------------------------------------------------------------------
    # 测试 3：cell A RELEASED，cell B 不受影响（nexus_tag / 农民 / FENCE 各自独立）
    # ------------------------------------------------------------------

    def test_cell_a_released_cell_b_unaffected(self) -> None:
        """cell A 受击 RELEASED 后：B 的 nexus_tag 仍在 stealth_townhall_tags，B 的农民不被 release。"""
        m = StealthCellManager()

        # Cell A：MINING，point=(10.0,10.0)，nexus=100，worker={11,12}
        point_a = (10.0, 10.0)
        cid_a = _make_mining_cell_wp5(
            m, nexus_tag=100, worker_tags={11, 12}, on_attack="flee", point=point_a
        )

        # Cell B：MINING，point=(80.0,80.0)，nexus=200，worker={21,22}，target=2（alive==target）
        point_b = (80.0, 80.0)
        cid_b = _make_mining_cell_wp5(
            m, nexus_tag=200, worker_tags={21, 22}, on_attack="flee", point=point_b, worker_target=2
        )

        # 只有 A 点附近有敌，B 无敌
        bot = _BotMultiStateMultiCell(
            alive_tags={11, 12, 21, 22},
            nexus_map={},
            nearby_probes_map={},
            enemy_near_points={point_a},  # 只攻击 A
        )
        facade = FakeFacade()
        m.on_tick(bot, facade, 500.0)

        # A 已释放
        assert cid_a not in m.cells, "A 受击后应被移除"

        # B 仍在，状态不变
        assert cid_b in m.cells, "B 不受 A 的释放影响"
        assert m.cells[cid_b].state == StealthState.MINING, "B 状态应保持 MINING"
        assert m.cells[cid_b].nexus_tag == 200, "B.nexus_tag 不应被清掉"
        assert m.cells[cid_b].worker_tags == {21, 22}, "B 的农民不应被 release"

        # FENCE：stealth_townhall_tags 含 B 的 nexus，不含 A 的
        assert 200 in facade.stealth_townhall_tags, "B 的 nexus_tag 应仍在 stealth_townhall_tags"
        assert 100 not in facade.stealth_townhall_tags, (
            "A 的 nexus_tag 应从 stealth_townhall_tags 移除"
        )

        # B 的农民不被 release_unit_role
        released = {c.args[0] for c in facade.calls if c.method == "release_unit_role"}
        assert not ({21, 22} & released), (
            f"B 的农民 {{21, 22}} 不应被 release_unit_role，实际 released={released}"
        )

    # ------------------------------------------------------------------
    # 测试 4：stealth_townhall_tags / stealth_worker_tags 是多 cell 并集
    # ------------------------------------------------------------------

    def test_union_properties_multi_cell(self) -> None:
        """3 个 MINING cell 的并集属性正确（townhall + worker 各自并集，无重叠）。"""
        m = StealthCellManager()

        tags = [(100, {10, 11}), (200, {20, 21}), (300, {30, 31})]
        points = [(10.0, 10.0), (40.0, 40.0), (70.0, 70.0)]

        for i, (nt, wt) in enumerate(tags):
            cid = m.create_cell(_make_payload(x=points[i][0], y=points[i][1]))
            m.cells[cid].nexus_tag = nt
            m.cells[cid].worker_tags = wt

        # townhall 并集 = {100, 200, 300}
        assert m.stealth_townhall_tags == {100, 200, 300}, (
            f"stealth_townhall_tags 应为 {{100,200,300}}，实际={m.stealth_townhall_tags}"
        )
        # worker 并集 = {10,11,20,21,30,31}
        expected_workers = {10, 11, 20, 21, 30, 31}
        assert m.stealth_worker_tags == expected_workers, (
            f"stealth_worker_tags 应为 {expected_workers}，实际={m.stealth_worker_tags}"
        )

    # ------------------------------------------------------------------
    # 测试 5：3 个 cell（PENDING/BUILDING/MINING）同帧 tick，各走各的分支
    # ------------------------------------------------------------------

    def test_three_cells_different_states_same_tick(self) -> None:
        """3 个 cell 各在不同状态同帧 tick：P 等 Probe，B 等 Nexus，M 正常运营。

        期望：
        - PENDING cell 因无 Probe 保持 PENDING
        - BUILDING cell 因无 Nexus 保持 BUILDING
        - MINING cell 因 alive==target 保持 MINING，不 train
        """
        m = StealthCellManager()

        # Cell 1：PENDING（无 builder，等 Probe）
        cid_p = m.create_cell(_make_payload(x=10.0, y=10.0, worker_target=4))
        # 保持默认 PENDING state

        # Cell 2：BUILDING（有 builder，等 Nexus settle）
        cid_b = m.create_cell(_make_payload(x=40.0, y=40.0, worker_target=4))
        m.cells[cid_b].state = StealthState.BUILDING
        m.cells[cid_b].builder_tag = 50

        # Cell 3：MINING（有 nexus，alive==target，不补员）
        cid_m = m.create_cell(_make_payload(x=70.0, y=70.0, worker_target=2))
        m.cells[cid_m].state = StealthState.MINING
        m.cells[cid_m].nexus_tag = 777
        m.cells[cid_m].worker_tags = {60, 61}

        # bot：无可用 Probe；无 Nexus（B 附近空），M 农民存活；无敌
        bot = _BotMultiStateMultiCell(
            alive_tags={50, 60, 61},
            nexus_map={},  # B 附近没有 Nexus → 保持 BUILDING
            nearby_probes_map={},
            enemy_near_points=set(),
        )
        facade = FakeFacade()  # selector_stub 为空 → resolve_selector("Probe") == []
        m.on_tick(bot, facade, 200.0)

        # Cell 1 仍 PENDING（无 Probe 可用）
        assert cid_p in m.cells, "PENDING cell 应还在"
        assert m.cells[cid_p].state == StealthState.PENDING, "PENDING cell 应保持 PENDING"

        # Cell 2 仍 BUILDING（无 Nexus）
        assert cid_b in m.cells, "BUILDING cell 应还在"
        assert m.cells[cid_b].state == StealthState.BUILDING, "BUILDING cell 应保持 BUILDING"

        # Cell 3 仍 MINING，无 train
        assert cid_m in m.cells, "MINING cell 应还在"
        assert m.cells[cid_m].state == StealthState.MINING, "MINING cell 应保持 MINING"
        assert facade.train_probe_calls == [], "alive==target 时不应 train"


# ---------------------------------------------------------------------------
# WP4b：偷气测试
# ---------------------------------------------------------------------------


def _make_payload_gas(
    x: float = 50.0,
    y: float = 60.0,
    worker_target: int = 16,
    with_gas: bool = True,
) -> StealthMinePayload:
    return StealthMinePayload(point=(x, y), worker_target=worker_target, with_gas=with_gas)


def _make_mining_cell_gas(
    m: StealthCellManager,
    nexus_tag: int = 555,
    worker_tags: set[int] | None = None,
    worker_target: int = 4,
    with_gas: bool = True,
) -> int:
    """创建 MINING 状态 cell，支持 with_gas 参数。"""
    cid = m.create_cell(_make_payload_gas(worker_target=worker_target, with_gas=with_gas))
    cell = m.cells[cid]
    cell.state = StealthState.MINING
    cell.nexus_tag = nexus_tag
    cell.worker_tags = set(worker_tags) if worker_tags is not None else set()
    return cid


class TestGasOperations:
    """WP4b：偷气功能单测（全 mock，不起 SC2）。"""

    # bot mock（MINING 态只需 alive + probe hook）
    class _BotSimple:
        def _is_unit_alive(self, tag: int) -> bool:
            return True  # 默认所有单位存活

        def _find_nearby_probes(
            self, point: tuple[float, float], radius: float, exclude_tags: set[int]
        ) -> list[int]:
            return []  # 默认无附近未认领 probe

        def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
            return None

        def _townhall_ideal(self, tag: int) -> int:
            return 16  # 采矿 ideal（满矿 16）

        def _townhall_assigned(self, tag: int) -> int:
            return 0

        def _main_economy_has_gas(self) -> bool:
            return True  # 默认主经济已在采气（让气门测试按矿工数决定）

    # ------------------------------------------------------------------
    # 测试 1：create_cell 传播 with_gas
    # ------------------------------------------------------------------

    def test_create_cell_propagates_with_gas_true(self) -> None:
        """create_cell 从 payload 拷贝 with_gas=True。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload_gas(with_gas=True))
        assert m.cells[cid].with_gas is True

    def test_create_cell_propagates_with_gas_false(self) -> None:
        """create_cell 从 payload 拷贝 with_gas=False。"""
        m = StealthCellManager()
        cid = m.create_cell(_make_payload_gas(with_gas=False))
        assert m.cells[cid].with_gas is False

    # ------------------------------------------------------------------
    # 测试 2：with_gas=True + geyser stub → 调 order_probe_build_gas
    # ------------------------------------------------------------------

    def test_with_gas_builds_assimilator_when_geyser_available(self) -> None:
        """with_gas=True，有未建 geyser，cell 有农民 → 调 order_probe_build_gas。

        2026-06-13 矿优先：矿工需达 ≥12（threshold = min(12, 16×75%)=12）且主经济有气，
        才过气门。_BotSimple._main_economy_has_gas 返回 True，这里补足 12 个矿工让门开。
        """
        m = StealthCellManager()
        worker_tags = set(range(101, 113))  # 12 个矿工，恰好达到 threshold=12
        _cid = _make_mining_cell_gas(m, worker_tags=worker_tags, with_gas=True)

        facade = FakeFacade()
        # stub：1 个未建 geyser
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert len(facade.gas_build_orders) == 1, (
            f"应调 order_probe_build_gas 1 次，实际={facade.gas_build_orders}"
        )
        probe_tag, geyser_tag = facade.gas_build_orders[0]
        assert geyser_tag == 201, f"geyser_tag 应为 201，实际={geyser_tag}"
        assert probe_tag in worker_tags, f"probe 应来自 cell.worker_tags，实际 probe={probe_tag}"

    def test_gas_target_is_additional_to_mineral(self) -> None:
        """动态双 cap：采气是额外的——mineral_target=16 + gas_target=6 → total=22。
        16 个农民（采矿满）但有气矿缺口 → 仍 train（补到 22），不会停在 16。
        """
        m = StealthCellManager()
        cid = _make_mining_cell_gas(
            m, worker_tags=set(range(101, 117)), worker_target=16, with_gas=True
        )  # 16 个农民

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 3, 3), (302, 3, 3)]  # 2 口气，ideal 共 6

        m.on_tick(self._BotSimple(), facade, 0.0)

        # mineral 16 + gas 6 = 22；当前 16 < 22 → 仍 train 补气矿农民
        assert facade.train_probe_calls, "采矿满 16 但有气矿缺口 → 应继续 train 到 22"
        assert m.cells[cid].live_total_target == 22

    def test_no_gas_target_when_no_assimilator(self) -> None:
        """没有 assimilator（gas_target=0）→ total=mineral_target(16)，16 农民不再 train。"""
        m = StealthCellManager()
        cid = _make_mining_cell_gas(
            m, worker_tags=set(range(101, 117)), worker_target=16, with_gas=True
        )
        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = []  # 无 ready assimilator
        facade.stealth_geysers_stub = []  # 也无 geyser 可建

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert m.cells[cid].live_total_target == 16
        assert not facade.train_probe_calls, "采矿满 16 且无气矿 → 不再 train"

    def test_ensure_reserved_called_each_tick(self) -> None:
        """防外流：每 MINING tick 对本 cell 全部农民 ensure_units_reserved。"""
        m = StealthCellManager()
        workers = {101, 102, 103}
        _make_mining_cell_gas(m, worker_tags=set(workers), worker_target=16)
        facade = FakeFacade()

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert workers <= facade.reserved_ensured, (
            f"本 cell 农民应每帧 ensure_reserved，实际={facade.reserved_ensured}"
        )

    def test_chrono_self_while_growing(self) -> None:
        """成长期（农民 < total_target）→ 偷矿 Nexus 自我星空加速。"""
        m = StealthCellManager()
        _make_mining_cell_gas(m, worker_tags={101, 102}, worker_target=16)  # 2 < 16
        facade = FakeFacade()

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert facade.chrono_nexus_calls, "成长期应自我 chrono"

    def test_no_chrono_when_saturated(self) -> None:
        """满采（农民 ≥ total_target）→ 停止自我加速（能量交还 bot 公共池）。"""
        m = StealthCellManager()
        _make_mining_cell_gas(
            m, worker_tags=set(range(101, 117)), worker_target=16
        )  # 16 == total(16,无气)
        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = []

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert not getattr(facade, "chrono_nexus_calls", []), "满采后不应再自我 chrono"

    def test_with_gas_no_build_when_two_assimilators_ready(self) -> None:
        """已有 2 个 ready assimilator → 不再建（ready_count >= 2）。

        2026-06-13 矿优先：补足 12 个矿工让气门开，验证"已有 2 assimilator → 不建"逻辑
        （而非被气门阻拦产生的错误 pass）。
        """
        m = StealthCellManager()
        # 矿位已饱和：16 个矿工 >= mineral_ideal(16) → 气门无条件开
        _cid = _make_mining_cell_gas(
            m, worker_tags=set(range(101, 117)), worker_target=16, with_gas=True
        )

        facade = FakeFacade()
        # stub：已有 2 个 ready assimilator（达到上限），还有 1 个 geyser（理论上可建但已达上限）
        facade.stealth_gas_buildings_stub = [(301, 3, 3), (302, 3, 3)]
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]  # 还有一个未建 geyser

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert facade.gas_build_orders == [], "已有 2 个 ready assimilator，不应再建"

    def test_with_gas_no_build_when_no_geysers(self) -> None:
        """with_gas=True 但无未建 geyser（stub 返回 []）→ 不调 build_gas。

        2026-06-13 矿优先：矿位饱和（16 矿工 >= ideal 16）让气门开，验证"无 geyser → 不建"
        逻辑（而非气门关闭产生的错误 pass）。
        """
        m = StealthCellManager()
        # 矿位饱和，气门无条件开
        _cid = _make_mining_cell_gas(
            m, worker_tags=set(range(101, 117)), worker_target=16, with_gas=True
        )

        facade = FakeFacade()
        # geysers_stub 为空（默认 []），gas_buildings 也为空

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert facade.gas_build_orders == [], "无 geyser 时不应调 build_gas"

    # ------------------------------------------------------------------
    # 测试 3：with_gas=True + gas_buildings stub (0,3) → 派农民采气
    # ------------------------------------------------------------------

    def test_with_gas_saturates_deficit_workers(self) -> None:
        """find_stealth_gas_buildings 返回 (301, 0, 3)，cell 有足够农民 → 派 3 个采气。

        2026-06-13 矿优先：补足 12 个矿工（threshold=12）让气门开，再验证派采气逻辑。
        worker_tags 为 14 个矿工，deficit=3 → 派 3 个；sent_workers ⊆ worker_tags。
        """
        m = StealthCellManager()
        worker_tags = set(range(101, 115))  # 14 个矿工（14 >= threshold=12，门开）
        cid = _make_mining_cell_gas(m, worker_tags=worker_tags, with_gas=True)

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 0, 3)]  # deficit=3

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert len(facade.gas_gather_orders) == 3, (
            f"deficit=3，应派 3 个农民采气，实际={len(facade.gas_gather_orders)}"
        )
        # 所有采气农民来自本 cell worker_tags
        sent_workers = {w for w, _ in facade.gas_gather_orders}
        assert sent_workers.issubset(worker_tags), (
            f"采气农民应来自 cell.worker_tags，实际={sent_workers}"
        )
        # gas_tag 入 cell.gas_tags
        assert 301 in m.cells[cid].gas_tags, "gas_tag=301 应进 cell.gas_tags"
        # 采气农民加入 gas_worker_tags
        assert m.cells[cid].gas_worker_tags == sent_workers, (
            f"gas_worker_tags 应等于 sent_workers={sent_workers}，"
            f"实际={m.cells[cid].gas_worker_tags}"
        )

    def test_with_gas_gas_building_tag_added_to_gas_tags(self) -> None:
        """find_stealth_gas_buildings 的 tag 进 cell.gas_tags（即使已饱和）。

        2026-06-13 矿优先：补足 12 个矿工让气门开，确保 _tick_gas 被调到，才能测到
        gas_tags 更新逻辑（而非气门关闭导致 _tick_gas 跳过）。
        """
        m = StealthCellManager()
        # 12 个矿工 >= threshold=12，主经济有气（_BotSimple 返回 True）→ 门开
        cid = _make_mining_cell_gas(m, worker_tags=set(range(101, 113)), with_gas=True)

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 3, 3)]  # 已饱和，但 tag 应进 gas_tags

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert 301 in m.cells[cid].gas_tags, "即使 assimilator 已饱和，tag 也应加入 cell.gas_tags"

    # ------------------------------------------------------------------
    # 测试 4：with_gas=False → 完全不碰气矿逻辑
    # ------------------------------------------------------------------

    def test_with_gas_false_no_gas_calls(self) -> None:
        """with_gas=False 的 cell → on_tick 不调任何 gas facade 方法。"""
        m = StealthCellManager()
        _cid = _make_mining_cell_gas(m, worker_tags={101, 102}, with_gas=False)

        facade = FakeFacade()
        # 注入 stub，但 with_gas=False 不应被调用
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]
        facade.stealth_gas_buildings_stub = [(301, 0, 3)]

        m.on_tick(self._BotSimple(), facade, 0.0)

        gas_calls = [
            c.method
            for c in facade.calls
            if c.method.startswith(
                (
                    "find_stealth_geysers",
                    "find_stealth_gas_buildings",
                    "order_probe_build_gas",
                    "order_worker_gather_gas",
                )
            )
        ]
        assert gas_calls == [], f"with_gas=False 不应调气矿方法，实际={gas_calls}"
        assert facade.gas_build_orders == []
        assert facade.gas_gather_orders == []

    # ------------------------------------------------------------------
    # 测试 5：assimilator 已饱和 → 不派农民
    # ------------------------------------------------------------------

    def test_no_dispatch_when_saturated(self) -> None:
        """gas_worker_tags 已达 gas_cap（总气位）→ 不再派采气农民（count-capped，2026-06-12）。

        新语义：不看引擎 assigned（滞后、会导致膨胀），看 cell 自己 gas_worker_tags 计数 vs
        gas_cap=Σideal。已满 → need=0 → 不派。

        2026-06-13 矿优先：补足矿工（12 个矿工 + 3 个已派气工 = 15 总），让气门开，
        确保"已满 gas_cap → 不派"是 _tick_gas 的 need=0 逻辑，而非气门拦截。
        """
        m = StealthCellManager()
        # 3 个气工 + 12 个矿工 = 15 总；mineral_workers = 15-3=12 >= threshold=12
        mineral_workers = set(range(201, 213))  # 12 个矿工
        gas_worker_set = {101, 102, 103}
        all_workers = gas_worker_set | mineral_workers
        cid = _make_mining_cell_gas(m, worker_tags=all_workers, with_gas=True)
        m.cells[cid].gas_worker_tags = gas_worker_set  # 已派满 gas_cap=3

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 3, 3)]  # gas_cap=3

        m.on_tick(self._BotSimple(), facade, 0.0)

        assert facade.gas_gather_orders == [], (
            "gas_worker_tags 已达 gas_cap，不应再派农民（防 gas_worker_tags 膨胀）"
        )

    # ------------------------------------------------------------------
    # 测试 6：气矿农民在两帧内稳定不抖（gas_worker_tags 防重复派）
    # ------------------------------------------------------------------

    def test_gas_assign_balanced_across_two_assims(self) -> None:
        """采气农民按每个 assim 缺口均分（2026-06-12 修 6 个堆一个气矿）。

        两个 assim 各缺 3 → 6 个农民应 3+3 分到两个，而不是 round-robin 偏向第一个。

        2026-06-13 矿优先：用 12 个矿工（threshold=12）让气门开，验证均分逻辑。
        """
        m = StealthCellManager()
        _make_mining_cell_gas(m, worker_tags=set(range(1, 13)), with_gas=True)  # 12 矿工

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 0, 3), (302, 0, 3)]  # 两个 assim 各缺 3

        m.on_tick(self._BotSimple(), facade, 0.0)

        per_assim: dict[int, int] = {}
        for _w, gas in facade.gas_gather_orders:
            per_assim[gas] = per_assim.get(gas, 0) + 1
        assert per_assim.get(301, 0) == 3 and per_assim.get(302, 0) == 3, (
            f"两个气矿应各 3 个农民，实际={per_assim}"
        )

    def test_drifted_gas_workers_get_rewelded(self) -> None:
        """漂走的采气农民（被登记采气、实际在采矿/idle）每帧重新焊回气上（2026-06-12）。

        真机定位：6 个登记采气里 3 个 order 没生效、漂去采矿 → 矿口超采到 19。修法：facade
        gas_worker_drifted 判出漂走的，重派 order_worker_gather_gas；采气循环中的不动。

        2026-06-13 矿优先：3 个气工 + 12 个矿工 = 15，mineral_workers=12 >= threshold=12 → 门开。
        """
        m = StealthCellManager()
        gas_worker_set = {101, 102, 103}
        mineral_worker_set = set(range(201, 213))  # 12 个矿工
        all_workers = gas_worker_set | mineral_worker_set
        cid = _make_mining_cell_gas(m, worker_tags=all_workers, with_gas=True)
        m.cells[cid].gas_worker_tags = gas_worker_set  # 已派满 gas_cap=3
        m.cells[cid].gas_tags = {301}

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 3, 3)]  # gas_cap=3，无补员
        facade.gas_drifted_stub = {102}  # 102 漂走了，101/103 在采气

        m.on_tick(self._BotSimple(), facade, 0.0)

        rewelded = [w for w, _ in facade.gas_gather_orders]
        assert 102 in rewelded, "漂走的 102 应被重新焊回气上"
        assert 101 not in rewelded and 103 not in rewelded, (
            "采气循环中的 101/103 不应被重派（不打断正常采气）"
        )

    def test_gas_workers_stable_no_flicker_over_two_ticks(self) -> None:
        """count-capped（2026-06-12）：连续两帧 gas_worker_tags 封顶在 gas_cap，不膨胀。

        场景：cell 有 16 个农民（= ideal 16，矿位饱和），gas_cap=3。
        Tick 1：矿位饱和 → 气门无条件开 → 补到 gas_cap=3，gas_worker_tags={3 个}。
        Tick 2：已达 gas_cap → need=0 → 不再派。总 gas_gather_orders=3（旧 deficit 逻辑会到 4+）。

        2026-06-13 矿优先：改用 16 个矿工（= mineral_ideal=16），触发"矿位饱和"无条件开门，
        让 count-capped 逻辑在门开后正确运行。
        """
        m = StealthCellManager()
        worker_tags = set(range(101, 117))  # 16 个矿工，mineral_ideal=16 → 矿位饱和 → 门开
        cid = _make_mining_cell_gas(m, worker_tags=worker_tags, with_gas=True)

        facade = FakeFacade()
        facade.stealth_gas_buildings_stub = [(301, 0, 3)]  # gas_cap=3

        # Tick 1：补到 gas_cap=3
        m.on_tick(self._BotSimple(), facade, 0.0)
        orders_after_tick1 = len(facade.gas_gather_orders)
        assert orders_after_tick1 == 3, f"Tick 1 应补到 gas_cap=3，实际={orders_after_tick1}"
        assert len(m.cells[cid].gas_worker_tags) == 3

        # Tick 2：已满 gas_cap → 不再派（不膨胀，第 4 个农民留在矿口）
        m.on_tick(self._BotSimple(), facade, 1.0)
        orders_after_tick2 = len(facade.gas_gather_orders)
        assert orders_after_tick2 == 3, (
            f"Tick 2 已达 gas_cap=3 不应再派，总仍 3 条，实际={orders_after_tick2}"
        )
        assert len(m.cells[cid].gas_worker_tags) == 3, "gas_worker_tags 封顶在 gas_cap，不膨胀"

    # ------------------------------------------------------------------
    # 测试 7：死亡农民同时清出 gas_worker_tags
    # ------------------------------------------------------------------

    def test_dead_gas_worker_removed_from_gas_worker_tags(self) -> None:
        """gas_worker_tags 里有死亡 tag → on_tick 后从 gas_worker_tags 移除。"""
        m = StealthCellManager()
        alive_tag = 101
        dead_tag = 102
        cid = _make_mining_cell_gas(m, worker_tags={alive_tag, dead_tag}, with_gas=True)
        # 直接设 gas_worker_tags（模拟之前已分配）
        m.cells[cid].gas_worker_tags = {dead_tag}

        class _BotDeadTag:
            def _is_unit_alive(self, tag: int) -> bool:
                return tag == alive_tag  # dead_tag 已死

            def _find_nearby_probes(self, point: tuple, radius: float, exclude_tags: set) -> list:
                return []

            def _find_nearby_nexus(self, point: tuple, radius: float) -> int | None:
                return None

        facade = FakeFacade()
        bot = _BotDeadTag()
        # grace-period（2026-06-11）：采气农民钻进 assimilator 会短暂消失，首帧不删；
        # 连续消失超 _DEAD_GRACE_S(4s) 才真判死。
        m.on_tick(bot, facade, 0.0)
        assert dead_tag in m.cells[cid].gas_worker_tags, (
            "首帧消失只标记、不立即删（防误删采气农民）"
        )
        m.on_tick(bot, facade, 5.0)

        assert dead_tag not in m.cells[cid].gas_worker_tags, "死亡 tag 应从 gas_worker_tags 移除"
        assert dead_tag not in m.cells[cid].worker_tags, "死亡 tag 也应从 worker_tags 移除"

    def test_gas_worker_transient_vanish_not_pruned(self) -> None:
        """核心 grace 行为：采气农民"钻进"assimilator 短暂消失（<grace）后又出现 → **不被删**。

        这是真机峰值卡 19（16 矿超采+0 气）到不了 22 的根因修复：旧逻辑 1 帧消失就删，把
        正常采气农民当死亡 → gas_worker_tags 永远清零、采气补不满。
        """
        m = StealthCellManager()
        alive_tag = 101
        gas_tag = 102  # 采气农民，会周期性消失
        cid = _make_mining_cell_gas(m, worker_tags={alive_tag, gas_tag}, with_gas=True)
        m.cells[cid].gas_worker_tags = {gas_tag}

        class _BotFlicker:
            def __init__(self) -> None:
                self.gas_present = True

            def _is_unit_alive(self, tag: int) -> bool:
                if tag == gas_tag:
                    return self.gas_present  # 钻进 assimilator 时 False
                return True

            def _find_nearby_probes(self, point: tuple, radius: float, exclude_tags: set) -> list:
                return []

            def _find_nearby_nexus(self, point: tuple, radius: float) -> int | None:
                return None

        bot = _BotFlicker()
        facade = FakeFacade()
        m.on_tick(bot, facade, 0.0)  # 在
        bot.gas_present = False
        m.on_tick(bot, facade, 1.0)  # 钻进去消失（标记 missing@1.0）
        m.on_tick(bot, facade, 2.5)  # 仍消失，但 1.5s < 4s grace
        bot.gas_present = True
        m.on_tick(bot, facade, 3.0)  # 钻出来又在 → 清计时，绝不该被删

        assert gas_tag in m.cells[cid].worker_tags, "短暂钻进 assimilator 又出来的采气农民不应被删"
        assert gas_tag in m.cells[cid].gas_worker_tags, "采气农民应留在 gas_worker_tags（采气不断）"


# ===========================================================================
# WP6 需求2：pending_release_events（_release_cell → drain by director）
# ===========================================================================


class TestPendingReleaseEvents:
    """验证 _release_cell 后 pending_release_events 正确填充，供 director drain。"""

    def _make_mining_cell(self, m: StealthCellManager) -> int:
        payload = StealthMinePayload(point=(55.0, 70.0), worker_target=16, on_attack="flee")
        cid = m.create_cell(payload)
        cell = m.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = 100
        cell.worker_tags = {10, 20, 30}
        return cid

    def test_pending_release_events_initially_empty(self) -> None:
        """新建 manager 的 pending_release_events 为空列表。"""
        m = StealthCellManager()
        assert m.pending_release_events == []

    def test_release_cell_appends_pending_event(self) -> None:
        """_release_cell 后 pending_release_events 有 1 条 dict，字段正确。"""
        m = StealthCellManager()
        cid = self._make_mining_cell(m)
        facade = FakeFacade()
        cell = m.cells[cid]
        m._release_cell(cell, facade, reason="under_attack", new_state=StealthState.RELEASED)

        assert len(m.pending_release_events) == 1
        ev = m.pending_release_events[0]
        assert ev["cell_id"] == cid
        assert ev["reason"] == "under_attack"
        assert ev["state"] == "released"
        assert "location" in ev

    def test_release_destroyed_appends_event_with_destroyed_state(self) -> None:
        """Nexus 摧毁路径（DESTROYED）也进 pending_release_events。"""
        m = StealthCellManager()
        cid = self._make_mining_cell(m)
        facade = FakeFacade()
        cell = m.cells[cid]
        m._release_cell(cell, facade, reason="destroyed", new_state=StealthState.DESTROYED)

        ev = m.pending_release_events[0]
        assert ev["reason"] == "destroyed"
        assert ev["state"] == "destroyed"

    def test_multiple_releases_appends_multiple_events(self) -> None:
        """两个 cell 各自 release → pending_release_events 有 2 条。"""
        m = StealthCellManager()
        cid1 = self._make_mining_cell(m)
        # 手动加第二个 cell
        payload2 = StealthMinePayload(point=(80.0, 20.0), worker_target=16, on_attack="flee")
        cid2 = m.create_cell(payload2)
        m.cells[cid2].state = StealthState.MINING
        m.cells[cid2].nexus_tag = 200

        facade = FakeFacade()
        m._release_cell(
            m.cells[cid1], facade, reason="under_attack", new_state=StealthState.RELEASED
        )
        # cid1 already popped from cells, add cid2 fresh
        m._release_cell(m.cells[cid2], facade, reason="destroyed", new_state=StealthState.DESTROYED)

        assert len(m.pending_release_events) == 2
        cell_ids = {ev["cell_id"] for ev in m.pending_release_events}
        assert cid1 in cell_ids
        assert cid2 in cell_ids


# ===========================================================================
# TestGasGate：矿优先 + 跟随主经济气门（2026-06-13）
# ===========================================================================


class TestGasGate:
    """偷气开闸规则：矿工数达阈值 + 主经济有气 才开；矿位饱和无条件开。"""

    class _BotGasGate:
        """气门控单测专用 bot mock：可注入 _main_economy_has_gas + _townhall_ideal 返回值。"""

        def __init__(
            self,
            main_economy_has_gas: bool = True,
            mineral_ideal: int = 16,
        ) -> None:
            self._has_gas = main_economy_has_gas
            self._ideal = mineral_ideal

        def _main_economy_has_gas(self) -> bool:
            return self._has_gas

        def _townhall_ideal(self, tag: int) -> int:
            return self._ideal

        def _townhall_assigned(self, tag: int) -> int:
            return 0

        def _is_unit_alive(self, tag: int) -> bool:
            return True

        def _find_nearby_probes(
            self, point: tuple[float, float], radius: float, exclude_tags: set[int]
        ) -> list[int]:
            return []

        def _find_nearby_nexus(self, point: tuple[float, float], radius: float) -> int | None:
            return None

    def _make_gas_gate_cell(
        self,
        m: StealthCellManager,
        mineral_workers: int,
        gas_workers: int = 0,
    ) -> int:
        """创建 MINING 状态 cell，含 mineral_workers 个矿工 + gas_workers 个气工。

        矿工 tag 范围 100..(100+mineral_workers)；气工 tag 范围 200..(200+gas_workers)。
        """
        mineral_tags = set(range(100, 100 + mineral_workers))
        gas_tags_set = set(range(200, 200 + gas_workers))
        all_tags = mineral_tags | gas_tags_set
        cid = m.create_cell(StealthMinePayload(point=(50.0, 60.0), worker_target=16))
        cell = m.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = 555
        cell.worker_tags = all_tags
        cell.gas_worker_tags = gas_tags_set
        return cid

    # ------------------------------------------------------------------
    # 测试 1：矿工 8 < threshold(12) → 门关，不建 assimilator、不派气工
    # ------------------------------------------------------------------

    def test_gas_gate_hold_when_few_mineral_workers(self) -> None:
        """矿工数 8 < threshold=min(12, 16×0.75)=12 → 偷气门关闭。

        条件：主经济有气（_BotGasGate 返回 True），有 geyser 可建，但矿工不足 → 门仍关。
        """
        m = StealthCellManager()
        self._make_gas_gate_cell(m, mineral_workers=8)

        facade = FakeFacade()
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]  # 有 geyser 可建

        bot = self._BotGasGate(main_economy_has_gas=True, mineral_ideal=16)
        m.on_tick(bot, facade, 0.0)

        assert facade.gas_build_orders == [], (
            "矿工 8 < threshold=12 时不应建 assimilator（气门关闭）"
        )
        assert facade.gas_gather_orders == [], "矿工 8 < threshold=12 时不应派气工"

    # ------------------------------------------------------------------
    # 测试 2：矿工 12 >= threshold + 主经济有气 → 门开，建 assimilator
    # ------------------------------------------------------------------

    def test_gas_gate_open_when_workers_12_and_main_economy_has_gas(self) -> None:
        """矿工数 12（恰好达到 threshold=12）且主经济有气 → 门开，开始建 assimilator。"""
        m = StealthCellManager()
        self._make_gas_gate_cell(m, mineral_workers=12)

        facade = FakeFacade()
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]

        bot = self._BotGasGate(main_economy_has_gas=True, mineral_ideal=16)
        m.on_tick(bot, facade, 0.0)

        assert len(facade.gas_build_orders) == 1, "矿工 12 且主经济有气 → 气门开，应建 assimilator"

    # ------------------------------------------------------------------
    # 测试 3：矿工达阈值但主经济无气 → 门关（跟随 bot 策略）
    # ------------------------------------------------------------------

    def test_gas_gate_hold_when_main_economy_no_gas(self) -> None:
        """矿工数达阈值（14 >= threshold=12）但主经济没有在采气 → 门继续关。

        设 mineral_ideal=20，threshold=min(12, 20×75%)=min(12,15)=12。
        mineral_workers=14 >= 12（过矿工数关），但 14 < 20（未饱和）。
        _BotGasGate.has_gas=False → 门关，不建 assimilator。

        语义：偷矿跟随 bot 采气策略，bot 纯矿阶段偷矿也不提前开气。
        """
        m = StealthCellManager()
        # _make_gas_gate_cell 里 worker_target 只做 fallback，bot._townhall_ideal 返回 20
        self._make_gas_gate_cell(m, mineral_workers=14)

        facade = FakeFacade()
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]

        # mineral_ideal=20，14 未饱和（< 20）；主经济无气
        bot = self._BotGasGate(main_economy_has_gas=False, mineral_ideal=20)
        m.on_tick(bot, facade, 0.0)

        assert facade.gas_build_orders == [], "主经济无气时偷矿不应抢先开气（门关）"

    # ------------------------------------------------------------------
    # 测试 4：矿位饱和 → 无条件开气（不管主经济是否采气）
    # ------------------------------------------------------------------

    def test_gas_gate_open_unconditional_when_mineral_saturated(self) -> None:
        """矿位饱和（mineral_workers >= mineral_ideal=16）→ 无条件开气。

        即使主经济没在采气（has_gas=False），矿没地方派 → 剩余农民应去采气。
        """
        m = StealthCellManager()
        self._make_gas_gate_cell(m, mineral_workers=16)  # 16 == mineral_ideal=16 → 饱和

        facade = FakeFacade()
        facade.stealth_geysers_stub = [(201, (52.0, 58.0))]

        # 主经济无气，但矿已饱和 → 无条件开
        bot = self._BotGasGate(main_economy_has_gas=False, mineral_ideal=16)
        m.on_tick(bot, facade, 0.0)

        assert len(facade.gas_build_orders) == 1, "矿位饱和时无条件开气（即便主经济无气）"
