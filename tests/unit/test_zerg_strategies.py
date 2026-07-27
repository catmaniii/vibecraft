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

    def test_has_nine_openings(self) -> None:
        """虫族 9 opening：原 4（12pool/macro_hatch/roach_hydra/mutalisk_harass）
        + 4（ling_bane/roach_ravager/nydus/roach_allin）+ zvp_macro（#550 ZvP 运营流）。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.openings) == 9
        assert "zvp_macro" in {o.id for o in lib.openings}

    def test_midgames_empty(self) -> None:
        """两层架构：midgame_stance kind 已废弃；老 yaml 全迁到 opening"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.midgames) == 0

    def test_has_five_persistents(self) -> None:
        """虫族 5 个 persistent doctrine：brood_corruptor + ultralisk /
        lurker_hydra / muta_ling_bane / roach_hydra_viper。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        assert len(lib.lategames) == 0
        assert len(lib.persistents) == 5
        ids = {p.id for p in lib.persistents}
        assert ids == {
            "persistent_brood_corruptor",
            "persistent_ultralisk",
            "persistent_lurker_hydra",
            "persistent_muta_ling_bane",
            "persistent_roach_hydra_viper",
        }

    def test_opening_ids_correct(self) -> None:
        """4 个 opening 的 id（旧 midgame yaml 已迁到 opening kind）"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        ids = {s.id for s in lib.openings}
        for expect in (
            "12pool",
            "macro_hatch",
            "roach_hydra",
            "mutalisk_harass",
            "ling_bane",
            "roach_ravager",
            "nydus",
            "roach_allin",
        ):
            assert expect in ids

    def test_persistent_id_correct(self) -> None:
        """5 个 persistent doctrine 的 id 齐全。"""
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        ids = {s.id for s in lib.persistents}
        for expect in (
            "persistent_brood_corruptor",
            "persistent_ultralisk",
            "persistent_lurker_hydra",
            "persistent_muta_ling_bane",
            "persistent_roach_hydra_viper",
        ):
            assert expect in ids


class TestZergStrategyTransitions:
    """跨引用通过：opening 的 default_transitions 指向已存在的 midgame id。"""

    @pytest.fixture()
    def zerg_lib(self) -> any:
        from vibecraft.strategy.library import StrategyLibrary

        return StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)

    def test_opening_transitions_reference_known_id(self, zerg_lib: any) -> None:
        """两层架构：transition 指向的 id 在 library 任一表存在即可"""
        all_ids = set(zerg_lib.all_ids())
        for opening in zerg_lib.openings:
            for trans in getattr(opening, "default_transitions", []) or []:
                mid_id = getattr(trans, "midgame_id", None)
                if mid_id:
                    assert mid_id in all_ids, (
                        f"opening {opening.id!r} 引用了不存在的 transition {mid_id!r}"
                    )

    def test_opening_lategame_transitions_valid(self, zerg_lib: any) -> None:
        """两层架构：lategame_transitions 挂在 opening 上（midgame 已迁），指向 persistent"""
        all_ids = set(zerg_lib.all_ids())
        for opening in zerg_lib.openings:
            for trans in getattr(opening, "lategame_transitions", []) or []:
                lg_id = getattr(trans, "lategame_id", None)
                if lg_id:
                    assert lg_id in all_ids, (
                        f"opening {opening.id!r} 引用了不存在的 lategame {lg_id!r}"
                    )


class TestZergSharopyDummyClassSyntax:
    """所有 sharpy_dummy_class 字段格式合法（module:Class 格式）。"""

    @pytest.fixture()
    def all_strategies(self) -> list:
        from vibecraft.strategy.library import StrategyLibrary

        lib = StrategyLibrary.from_directories(_STRATS_DIR, _ZERG_YAML)
        return list(lib.openings) + list(lib.midgames) + list(lib.lategames) + list(lib.persistents)

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
