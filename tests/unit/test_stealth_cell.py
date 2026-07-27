"""WP1 Task 1.2：StealthCell dataclass + StealthState 枚举单测。"""

from __future__ import annotations

from vibecraft.bot.stealth.cell import StealthCell, StealthState


class TestStealthState:
    def test_all_states_exist(self) -> None:
        """五个状态枚举值都存在。"""
        states = {s.value for s in StealthState}
        assert states == {"pending", "building", "mining", "released", "destroyed"}

    def test_state_is_str(self) -> None:
        """StealthState 继承 str，可以直接做字符串比较。"""
        assert StealthState.PENDING == "pending"
        assert StealthState.MINING == "mining"


class TestStealthCellDefaults:
    def test_minimal_construction(self) -> None:
        """最小构造：cell_id + point + state 三个必填字段。"""
        cell = StealthCell(cell_id=1, point=(50.0, 60.0), state=StealthState.PENDING)
        assert cell.cell_id == 1
        assert cell.point == (50.0, 60.0)
        assert cell.state == StealthState.PENDING

    def test_optional_defaults(self) -> None:
        """可选字段默认值正确。"""
        cell = StealthCell(cell_id=1, point=(50.0, 60.0), state=StealthState.PENDING)
        assert cell.nexus_tag is None
        assert cell.worker_tags == set()
        assert cell.gas_tags == set()
        assert cell.worker_target == 16
        assert cell.on_attack == "flee"
        assert cell.builder_tag is None

    def test_worker_tags_mutable_default_not_shared(self) -> None:
        """不同 cell 的 worker_tags 是独立 set（dataclass field(default_factory) 保证）。"""
        c1 = StealthCell(cell_id=1, point=(10.0, 10.0), state=StealthState.PENDING)
        c2 = StealthCell(cell_id=2, point=(20.0, 20.0), state=StealthState.PENDING)
        c1.worker_tags.add(999)
        assert 999 not in c2.worker_tags

    def test_full_construction(self) -> None:
        """所有字段显式传入。"""
        cell = StealthCell(
            cell_id=3,
            point=(30.0, 40.0),
            state=StealthState.MINING,
            nexus_tag=12345,
            worker_tags={101, 102},
            gas_tags={201},
            worker_target=8,
            on_attack="hold",
            builder_tag=50,
        )
        assert cell.nexus_tag == 12345
        assert cell.worker_tags == {101, 102}
        assert cell.gas_tags == {201}
        assert cell.worker_target == 8
        assert cell.on_attack == "hold"
        assert cell.builder_tag == 50


class TestAliveWorkers:
    def test_all_alive(self) -> None:
        """所有 tag 存活时返回完整集合。"""
        cell = StealthCell(cell_id=1, point=(0.0, 0.0), state=StealthState.MINING)
        cell.worker_tags = {10, 20, 30}
        alive = cell.alive_workers(lambda tag: True)
        assert alive == {10, 20, 30}

    def test_some_dead(self) -> None:
        """部分 tag 已死（is_alive=False）时过滤掉。"""
        cell = StealthCell(cell_id=1, point=(0.0, 0.0), state=StealthState.MINING)
        cell.worker_tags = {10, 20, 30}
        alive_set = {10, 30}
        alive = cell.alive_workers(lambda tag: tag in alive_set)
        assert alive == {10, 30}

    def test_all_dead(self) -> None:
        """所有 tag 都死时返回空集。"""
        cell = StealthCell(cell_id=1, point=(0.0, 0.0), state=StealthState.MINING)
        cell.worker_tags = {10, 20}
        alive = cell.alive_workers(lambda tag: False)
        assert alive == set()

    def test_empty_worker_tags(self) -> None:
        """worker_tags 为空时返回空集（不 crash）。"""
        cell = StealthCell(cell_id=1, point=(0.0, 0.0), state=StealthState.PENDING)
        alive = cell.alive_workers(lambda tag: True)
        assert alive == set()

    def test_does_not_mutate_worker_tags(self) -> None:
        """alive_workers 不修改 worker_tags 本身。"""
        cell = StealthCell(cell_id=1, point=(0.0, 0.0), state=StealthState.MINING)
        cell.worker_tags = {10, 20, 30}
        _ = cell.alive_workers(lambda tag: tag == 10)
        assert cell.worker_tags == {10, 20, 30}  # 原集合不变
