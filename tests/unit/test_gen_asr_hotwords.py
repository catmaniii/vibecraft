"""ASR 热词生成脚本单测（FunASR voice input plan Task 1）。

验证：
1. build_hotwords 返回 list[tuple[str, int]]
2. 内置战术黑话在结果中且权重为 20
3. 建筑别名在结果中且权重为 15
4. 单位别名在结果中且权重为 15
5. 无重复词
6. 空目录不崩溃，至少返回内置黑话
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ROOT / "scripts"
_ALIASES_DIR = _ROOT / "docs" / "aliases"
_STRATEGIES_DIR = _ROOT / "strategies"

# scripts/ 不是 package，手动加到 sys.path（同 test_build_acceptance_runner.py 模式）
sys.path.insert(0, str(_SCRIPTS_DIR))

import gen_asr_hotwords as _mod  # noqa: E402


class TestBuildHotwordsReturnType:
    """返回类型和基本结构测试。"""

    def test_returns_list_of_tuples(self) -> None:
        """build_hotwords 返回 list，每项是 (str, int) 元组。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        assert isinstance(result, list)
        assert len(result) > 0
        word, weight = result[0]
        assert isinstance(word, str)
        assert isinstance(weight, int)


class TestTacticalJargon:
    """战术黑话权重 20 测试。"""

    def test_shan_zhui_in_result_with_weight_20(self) -> None:
        """'闪追' 在结果中，权重 20（战术黑话最高权重）。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "闪追" in words_dict, "'闪追' 应在热词表中"
        assert words_dict["闪追"] == 20, f"'闪追' 权重应为 20，实际 {words_dict['闪追']}"

    def test_4bg_in_result_with_weight_20(self) -> None:
        """'4BG' 在结果中，权重 20。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "4BG" in words_dict, "'4BG' 应在热词表中"
        assert words_dict["4BG"] == 20

    def test_iac_in_result_with_weight_20(self) -> None:
        """'IAC' 在结果中，权重 20。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "IAC" in words_dict
        assert words_dict["IAC"] == 20


class TestAliasEntries:
    """建筑/单位别名权重 15 测试（来自 docs/aliases/*.yaml）。"""

    def test_building_alias_shui_jing(self) -> None:
        """建筑别名 '水晶'（Pylon）在结果中，权重 15。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "水晶" in words_dict, "'水晶' 应在热词表中"
        assert words_dict["水晶"] == 15

    def test_building_alias_bg(self) -> None:
        """建筑别名 'BG'（Gateway）在结果中，权重 15。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "BG" in words_dict
        assert words_dict["BG"] == 15

    def test_unit_alias_cha_zi(self) -> None:
        """单位别名 '叉子'（Zealot）在结果中，权重 15。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "叉子" in words_dict, "'叉子' 应在热词表中"
        assert words_dict["叉子"] == 15

    def test_unit_alias_bu_xiu(self) -> None:
        """单位别名 '不朽'（Immortal）在结果中，权重 15。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        assert "不朽" in words_dict


class TestDeduplication:
    """去重测试。"""

    def test_no_duplicate_words(self) -> None:
        """热词表无重复词（同词只出现一次）。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words = [w for w, _ in result]
        duplicates = [w for w in set(words) if words.count(w) > 1]
        assert len(duplicates) == 0, f"重复词: {duplicates}"

    def test_jargon_weight_beats_alias_weight(self) -> None:
        """'闪追' 同时在黑话列表和 aliases 里 → 权重取 20（黑话优先）。"""
        result = _mod.build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
        words_dict = dict(result)
        # 闪追 在 protoss aliases（Blink 别名，weight=15）也在黑话（weight=20）
        # 最终应该是 20
        assert words_dict.get("闪追") == 20


class TestEmptyDirs:
    """边界条件：空目录不崩溃。"""

    def test_empty_dirs_returns_jargon(self, tmp_path: Path) -> None:
        """空目录时 build_hotwords 不崩，至少包含内置黑话条目。"""
        result = _mod.build_hotwords(tmp_path, tmp_path)
        assert isinstance(result, list)
        assert len(result) >= 5, "空目录至少应有内置战术黑话"
        words_dict = dict(result)
        # 内置黑话应全在
        assert "4BG" in words_dict
        assert "IAC" in words_dict
        assert "闪追" in words_dict

    def test_empty_dirs_all_weights_are_jargon_weight(self, tmp_path: Path) -> None:
        """空目录时所有条目来自内置黑话，权重均为 20。"""
        result = _mod.build_hotwords(tmp_path, tmp_path)
        for word, weight in result:
            assert weight == 20, f"空目录时 '{word}' 权重应为 20，实际 {weight}"
