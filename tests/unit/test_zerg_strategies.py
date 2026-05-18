"""虫族 strategy yaml 单测（Task 2a M6.2a）。

测试范围：
1. 5 个 yaml 都能通过 StrategyLibrary 加载
2. 跨引用通过（opening → midgame → lategame 形成完整转移图）
3. 所有 sharpy_dummy_class 字符串语法合法（"module:Class" 格式）
"""

from __future__ import annotations

from pathlib import Path

import pytest

_STRATS_DIR = Path(__file__).parents[2] / "strategies" / "zerg"
_ZERG_YAML = Path(__file__).parents[2] / "docs" / "aliases" / "zerg.yaml"


class TestZergStrategyLoad:
    """5 个虫族 strategy yaml 全部可通过 StrategyLibrary 加载。"""

    def test_strategies_dir_exists(self) -> None:
        assert _STRATS_DIR.exists(), f"strategies/zerg/ 目录不存在: {_STRATS_DIR}"

    def test_library_loads_from_zerg_dir(self) -> None:
        """StrategyLibrary.from_directories 不抛，成功加载虫族剧本。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert lib is not None

    def test_has_two_openings(self) -> None:
        """有 2 个 opening（12pool / macro_hatch）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.openings) == 2

    def test_has_two_midgames(self) -> None:
        """有 2 个 midgame（roach_hydra / mutalisk_harass）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.midgames) == 2

    def test_has_one_lategame(self) -> None:
        """有 1 个 lategame（brood_corruptor）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.lategames) == 1

    def test_opening_ids_correct(self) -> None:
        """两个 opening 的 id 分别是 12pool 和 macro_hatch。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        ids = {s.id for s in lib.openings}
        assert "12pool" in ids
        assert "macro_hatch" in ids

    def test_midgame_ids_correct(self) -> None:
        """两个 midgame 的 id 分别是 roach_hydra 和 mutalisk_harass。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        ids = {s.id for s in lib.midgames}
        assert "roach_hydra" in ids
        assert "mutalisk_harass" in ids

    def test_lategame_id_correct(self) -> None:
        """lategame 的 id 是 brood_corruptor。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        ids = {s.id for s in lib.lategames}
        assert "brood_corruptor" in ids


class TestZergStrategyTransitions:
    """跨引用通过：opening 的 default_transitions 指向已存在的 midgame id。"""

    @pytest.fixture()
    def zerg_lib(self) -> any:
        from vibecraft.strategy.library import StrategyLibrary

        return StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)

    def test_opening_transitions_reference_valid_midgame(self, zerg_lib: any) -> None:
        """每个 opening 的 default_transitions 引用的 midgame_id 在 lib.midgames 中存在。"""
        midgame_ids = {s.id for s in zerg_lib.midgames}
        for opening in zerg_lib.openings:
            for trans in getattr(opening, "default_transitions", []) or []:
                mid_id = getattr(trans, "midgame_id", None)
                if mid_id:
                    assert mid_id in midgame_ids, (
                        f"opening {opening.id!r} 引用了不存在的 midgame {mid_id!r}"
                    )

    def test_midgame_lategame_transitions_valid(self, zerg_lib: any) -> None:
        """midgame 的 lategame_transitions 引用的 lategame_id 在 lib.lategames 中存在。"""
        lategame_ids = {s.id for s in zerg_lib.lategames}
        for midgame in zerg_lib.midgames:
            for trans in getattr(midgame, "lategame_transitions", []) or []:
                lg_id = getattr(trans, "lategame_id", None)
                if lg_id:
                    assert lg_id in lategame_ids, (
                        f"midgame {midgame.id!r} 引用了不存在的 lategame {lg_id!r}"
                    )


class TestZergSharopyDummyClassSyntax:
    """所有 sharpy_dummy_class 字段格式合法（module:Class 格式）。"""

    @pytest.fixture()
    def all_strategies(self) -> list:
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        return list(lib.openings) + list(lib.midgames) + list(lib.lategames)

    def test_sharpy_dummy_class_syntax(self, all_strategies: list) -> None:
        """所有有 sharpy_dummy_class 的策略，格式为 module.path:ClassName。"""
        for strat in all_strategies:
            dummy_spec = getattr(strat, "sharpy_dummy_class", None)
            if dummy_spec:
                assert ":" in dummy_spec, (
                    f"{strat.id!r} sharpy_dummy_class={dummy_spec!r} 格式应为 module:Class"
                )
                module_part, class_part = dummy_spec.rsplit(":", 1)
                assert module_part, f"{strat.id!r} sharpy_dummy_class module 部分为空"
                assert class_part, f"{strat.id!r} sharpy_dummy_class class 部分为空"
                assert class_part[0].isupper(), (
                    f"{strat.id!r} class 名 {class_part!r} 应以大写字母开头"
                )
