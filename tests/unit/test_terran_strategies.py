"""人族 strategy yaml 单测（Task 3a M6.3a）。

测试范围：
1. 5 个 yaml 都能通过 StrategyLibrary 加载
2. 跨引用通过（opening → midgame → lategame 形成完整转移图）
3. 所有 sharpy_dummy_class 字符串语法合法（"module:Class" 格式）
"""

from __future__ import annotations

from pathlib import Path

import pytest

_STRATS_DIR = Path(__file__).parents[2] / "strategies" / "terran"
_TERRAN_YAML = Path(__file__).parents[2] / "docs" / "aliases" / "terran.yaml"


class TestTerranStrategyLoad:
    """5 个人族 strategy yaml 全部可通过 StrategyLibrary 加载。"""

    def test_strategies_dir_exists(self) -> None:
        assert _STRATS_DIR.exists(), f"strategies/terran/ 目录不存在: {_STRATS_DIR}"

    def test_library_loads_from_terran_dir(self) -> None:
        """StrategyLibrary.from_directories 不抛，成功加载人族剧本。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        assert lib is not None

    def test_has_two_openings(self) -> None:
        """有 2 个 opening（marine_rush / reaper_expand）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        assert len(lib.openings) == 2

    def test_has_two_midgames(self) -> None:
        """有 2 个 midgame（bio_stim / two_base_tanks）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        assert len(lib.midgames) == 2

    def test_has_one_lategame(self) -> None:
        """有 1 个 lategame（bc_late）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        assert len(lib.lategames) == 1

    def test_opening_ids_correct(self) -> None:
        """两个 opening 的 id 分别是 marine_rush 和 reaper_expand。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        ids = {s.id for s in lib.openings}
        assert "marine_rush" in ids
        assert "reaper_expand" in ids

    def test_midgame_ids_correct(self) -> None:
        """两个 midgame 的 id 分别是 bio_stim 和 two_base_tanks。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        ids = {s.id for s in lib.midgames}
        assert "bio_stim" in ids
        assert "two_base_tanks" in ids

    def test_lategame_id_correct(self) -> None:
        """lategame 的 id 是 bc_late。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
        ids = {s.id for s in lib.lategames}
        assert "bc_late" in ids


class TestTerranStrategyTransitions:
    """跨引用通过：opening 的 default_transitions 指向已存在的 midgame id。"""

    @pytest.fixture()
    def terran_lib(self) -> any:
        from vibecraft.strategy.library import StrategyLibrary

        return StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)

    def test_opening_transitions_reference_valid_midgame(self, terran_lib: any) -> None:
        """每个 opening 的 default_transitions 引用的 midgame_id 在 lib.midgames 中存在。"""
        midgame_ids = {s.id for s in terran_lib.midgames}
        for opening in terran_lib.openings:
            for trans in getattr(opening, "default_transitions", []) or []:
                mid_id = getattr(trans, "midgame_id", None)
                if mid_id:
                    assert mid_id in midgame_ids, (
                        f"opening {opening.id!r} 引用了不存在的 midgame {mid_id!r}"
                    )

    def test_midgame_lategame_transitions_valid(self, terran_lib: any) -> None:
        """midgame 的 lategame_transitions 引用的 lategame_id 在 lib.lategames 中存在。"""
        lategame_ids = {s.id for s in terran_lib.lategames}
        for midgame in terran_lib.midgames:
            for trans in getattr(midgame, "lategame_transitions", []) or []:
                lg_id = getattr(trans, "lategame_id", None)
                if lg_id:
                    assert lg_id in lategame_ids, (
                        f"midgame {midgame.id!r} 引用了不存在的 lategame {lg_id!r}"
                    )


class TestTerranSharopyDummyClassSyntax:
    """所有 sharpy_dummy_class 字段格式合法（module:Class 格式）。"""

    @pytest.fixture()
    def all_strategies(self) -> list:
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _TERRAN_YAML)
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
