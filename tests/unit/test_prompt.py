"""P0i Task 17: prompt 内容 snapshot 测试。

验证 build_system_prompt / build_few_shot 返回值里包含预期的关键词/片段。
测试不 mock LLM，只检查 prompt 字符串内容。
"""

from __future__ import annotations

import pytest

from vibecraft.llm.prompt import (
    build_few_shot,
    build_few_shot_en_supplement,
    build_system_prompt,
)
from vibecraft.strategy.aliases import AliasTable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def system_prompt() -> str:
    aliases = AliasTable.from_dict({})
    return build_system_prompt(aliases)


@pytest.fixture(scope="module")
def few_shot() -> str:
    return build_few_shot()


# ---------------------------------------------------------------------------
# Task 17: structure_override 在 4 层分类里出现
# ---------------------------------------------------------------------------


def test_prompt_includes_structure_override(system_prompt: str) -> None:
    """structure_override 在 prompt 4 层介绍中出现。"""
    assert "structure_override" in system_prompt


def test_en_supplement_loaded_and_has_english_examples() -> None:
    """英文 few-shot 补充非空且含英文示例标记 + 关键 directive。"""
    supp = build_few_shot_en_supplement()
    assert "English-input examples" in supp
    assert "production_override" in supp
    assert "group_assign" in supp


def test_parser_appends_en_supplement_only_for_en_locale() -> None:
    """IntentParser locale=en 时 few_shot 含英文补充;zh 不含(cache 友好,不污染中文路径)。"""
    from vibecraft.llm.parser import IntentParser
    from vibecraft.llm.provider import MockLLMProvider, ProviderResponse
    from vibecraft.strategy import StrategyLibrary

    lib = StrategyLibrary()
    prov = MockLLMProvider(
        handler=lambda **_: ProviderResponse(raw={}, raw_text="{}", model="mock")
    )
    zh = IntentParser(provider=prov, library=lib, locale="zh")
    en = IntentParser(provider=prov, library=lib, locale="en")
    assert "English-input examples" not in zh._few_shot
    assert "English-input examples" in en._few_shot


# ---------------------------------------------------------------------------
# 2026-06-04: 编队上限可配置 —— {max_voice_groups} 占位符
# ---------------------------------------------------------------------------


def test_prompt_max_voice_groups_default_is_5(system_prompt: str) -> None:
    """默认 build_system_prompt → 编队范围渲染成 1-5，且无占位符残留。"""
    assert "{max_voice_groups}" not in system_prompt
    assert "1-5 整数" in system_prompt
    assert "最多 5 个编队" in system_prompt


def test_prompt_max_voice_groups_configurable() -> None:
    """配置 max_voice_groups=8 → prompt 范围说明变成 1-8（LLM 看到的范围跟 schema 一致）。"""
    aliases = AliasTable.from_dict({})
    prompt8 = build_system_prompt(aliases, max_voice_groups=8)
    assert "{max_voice_groups}" not in prompt8
    assert "1-8 整数" in prompt8
    assert "最多 8 个编队" in prompt8
    assert "1-5 整数" not in prompt8


# ---------------------------------------------------------------------------
# Task 17: 7 个新 done_when kind 在词表中
# ---------------------------------------------------------------------------


def test_prompt_includes_7_new_done_when_kinds(system_prompt: str) -> None:
    """7 个新 done_when kind 在 prompt 词表中。"""
    for kind in [
        "structure_count",
        "own_unit_count",
        "supply_used",
        "supply_cap",
        "minerals",
        "gas",
        "worker_count",
    ]:
        assert kind in system_prompt, f"prompt 缺 done_when kind: {kind}"


# ---------------------------------------------------------------------------
# Task 17: A 系列 done_when 规则
# ---------------------------------------------------------------------------


def test_prompt_explains_a_verbs_done_when_none(system_prompt: str) -> None:
    """A 系列 verb (attack/defend/retreat/hold/vision) 的 done_when 规则在 prompt 中明确。"""
    # 检查 prompt 教 LLM "A 类 done_when=None"
    assert ("A 系列" in system_prompt and "done_when" in system_prompt) or "A 类" in system_prompt


# ---------------------------------------------------------------------------
# Task 17: B 系列 unit_count_hint 规则
# ---------------------------------------------------------------------------


def test_prompt_explains_b_verbs_unit_count_hint_required(system_prompt: str) -> None:
    """B 系列 verb (harass/scout) 必须给 unit_count_hint，否则 ambiguous。"""
    assert "unit_count_hint" in system_prompt
    # 显式提"必填"或"必给"或"required"
    assert "必填" in system_prompt or "必给" in system_prompt or "required" in system_prompt.lower()


# ---------------------------------------------------------------------------
# Task 17: 5 个新 few_shot 例子
# ---------------------------------------------------------------------------


def test_prompt_has_new_few_shot_examples(few_shot: str) -> None:
    """新增 5 个 few_shot 例子（补 8 BG / ramp 1 cannon / 进攻自然 done_when=None /
    5 凤凰骚扰 / 凤凰骚扰无数量 ambiguous）"""
    # 例 23: 补 8 BG → structure_override
    assert "8 BG" in few_shot or "Gateway" in few_shot
    # 例 24: ramp cannon
    assert "ramp" in few_shot
    # 例 26: 5 凤凰骚扰
    assert "凤凰" in few_shot or "Phoenix" in few_shot
    # 例 25/27: done_when=None 或 ambiguous
    assert "done_when=None" in few_shot or "done_when: null" in few_shot


# ---------------------------------------------------------------------------
# Task 10 review: A 系列 verbs 不含 hold + engagement_constraint 政策段
# ---------------------------------------------------------------------------


def test_prompt_a_verbs_lists_hold_under_tactical(system_prompt: str) -> None:
    """2026-05-28 用户:hold 加进 tactical_objective A 类 verb 列表。

    hold = 聚团到指定点 + 站住不回家(跟 defend 区别:不回家保持前线位置)。
    target_area 给了 → 聚到该点;target_area=None → 当前 army_center 锁住。
    默认 persistent=True,玩家 × 解除。
    """
    import re

    # 找 A 系列规则段:格式 "A 系列 verb (x / y / z)"
    m = re.search(r"A 系列 verb \(([^)]+)\)", system_prompt)
    assert m is not None, "未找到 'A 系列 verb (...)' 规则段"
    verbs_in_a = m.group(1)
    assert "hold" in verbs_in_a.split(" / "), (
        f"A 系列 tactical verb 列表应含 'hold'(2026-05-28 加): {verbs_in_a!r}"
    )


def test_prompt_explains_engagement_constraint_done_when_policy(system_prompt: str) -> None:
    """engagement_constraint 政策段应明确：默认 done_when=None；
    玩家说'直到 X'/'N 秒'才给 done_when。
    """
    assert "engagement_constraint" in system_prompt
    # 政策段应包含"默认"或"直到"或"N 秒"中至少一个
    has_policy = "默认" in system_prompt or "直到" in system_prompt or "N 秒" in system_prompt
    assert has_policy, (
        "prompt 缺 engagement_constraint done_when 政策说明（期望含'默认'/'直到'/'N 秒'）"
    )


# ---------------------------------------------------------------------------
# cast_ability 三族 ability_id 真名表覆盖
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ability_id",
    [
        # 神族 — 原有 6 个
        "MORPH_ARCHON",
        "PSISTORM_PSISTORM",
        "FEEDBACK_FEEDBACK",
        "EFFECT_PURIFICATIONNOVA",
        "HALLUCINATION_ARCHON",
        "FORCEFIELD_FORCEFIELD",
        # 神族 — 新增
        "EFFECT_BLINK_STALKER",
        "GUARDIANSHIELD_GUARDIANSHIELD",
        "GRAVITONBEAM_GRAVITONBEAM",
        "HALLUCINATION_PHOENIX",
        "HALLUCINATION_IMMORTAL",
        "HALLUCINATION_STALKER",
        "HALLUCINATION_ZEALOT",
        "ORACLEREVELATION_ORACLEREVELATION",
        "ORACLESTASISTRAP_ORACLEBUILDSTASISTRAP",
        "BEHAVIOR_PULSARBEAMON",
        "BEHAVIOR_PULSARBEAMOFF",
        "MORPH_WARPPRISMTRANSPORTMODE",
        "MORPH_WARPPRISMPHASINGMODE",
        "EFFECT_TIMEWARP",
        "EFFECT_MASSRECALL_MOTHERSHIP",
        "EFFECT_MASSRECALL_NEXUS",
        # 虫族
        "FUNGALGROWTH_FUNGALGROWTH",
        "NEURALPARASITE_NEURALPARASITE",
        "INFESTEDTERRANS_INFESTEDTERRANS",
        "TRANSFUSION_TRANSFUSION",
        "EFFECT_ABDUCT",
        "BLINDINGCLOUD_BLINDINGCLOUD",
        "PARASITICBOMB_PARASITICBOMB",
        "VIPERCONSUMESTRUCTURE_VIPERCONSUME",
        "EFFECT_SPAWNLOCUSTS",
        "CAUSTICSPRAY_CAUSTICSPRAY",
        "BURROWDOWN_LURKER",
        "BURROWUP_LURKER",
        # 人族
        "EFFECT_STIM",
        "EFFECT_STIM_MARAUDER",
        "EMP_EMP",
        "EFFECT_GHOSTSNIPE",
        "TACNUKESTRIKE_NUKECALLDOWN",
        "YAMATO_YAMATOGUN",
        "EFFECT_TACTICALJUMP",
        "CALLDOWNMULE_CALLDOWNMULE",
        "SCANNERSWEEP_SCAN",
        "SUPPLYDROP_SUPPLYDROP",
        "SIEGEMODE_SIEGEMODE",
        "UNSIEGE_UNSIEGE",
        "BURROWDOWN_WIDOWMINE",
        "BURROWUP_WIDOWMINE",
        "MORPH_VIKINGFIGHTERMODE",
        "MORPH_VIKINGASSAULTMODE",
        "BUILDAUTOTURRET_AUTOTURRET",
        "EFFECT_ANTIARMORMISSILE",
    ],
)
def test_cast_ability_ids_in_rules(system_prompt: str, ability_id: str) -> None:
    """cast_ability 真名表中每个 ability_id 都出现在 system_prompt（rules.md 部分）。"""
    assert ability_id in system_prompt, f"system_prompt 缺 ability_id: {ability_id}"


@pytest.mark.parametrize(
    "ability_id",
    [
        # 神族
        "EFFECT_BLINK_STALKER",
        "GUARDIANSHIELD_GUARDIANSHIELD",
        "GRAVITONBEAM_GRAVITONBEAM",
        "MORPH_WARPPRISMTRANSPORTMODE",
        "EFFECT_MASSRECALL_MOTHERSHIP",
        # 虫族
        "EFFECT_ABDUCT",
        "FUNGALGROWTH_FUNGALGROWTH",
        "TRANSFUSION_TRANSFUSION",
        # 人族
        "EFFECT_STIM",
        "EMP_EMP",
        "YAMATO_YAMATOGUN",
        "SIEGEMODE_SIEGEMODE",
    ],
)
def test_cast_ability_ids_in_few_shot(few_shot: str, ability_id: str) -> None:
    """few_shot 中含代表性 ability_id（至少出现在例子或注释中）。"""
    # few_shot 里不一定每个都有例子，这里只检查真名表里新增有 few_shot 例子的
    # 实际上 few_shot 里只有 38/39/40 新增了 STIM/ABDUCT/BLINK；其余在 rules 里
    # 这里只验证新增的 3 个例子
    new_example_ids = {
        "EFFECT_STIM",
        "EFFECT_ABDUCT",
        "EFFECT_BLINK_STALKER",
    }
    if ability_id in new_example_ids:
        assert ability_id in few_shot, f"few_shot 缺新增例子 ability_id: {ability_id}"


def test_few_shot_has_cast_ability_examples_38_39_40(few_shot: str) -> None:
    """few_shot 包含例 38(枪兵兴奋剂) / 例 39(飞蛇拉) / 例 40(追猎闪烁) 三个新例子。"""
    assert "枪兵嗑药" in few_shot or "EFFECT_STIM" in few_shot, "few_shot 缺例 38 枪兵兴奋剂"
    assert "飞蛇拉" in few_shot or "EFFECT_ABDUCT" in few_shot, "few_shot 缺例 39 飞蛇拉"
    assert "叉子闪过去" in few_shot or "EFFECT_BLINK_STALKER" in few_shot, (
        "few_shot 缺例 40 追猎闪烁"
    )


# ---------------------------------------------------------------------------
# #553: 人族气矿 few_shot 例（修"下二气/补一个气矿"解析失败 + 二/两=2 歧义消解）
# ---------------------------------------------------------------------------


def test_few_shot_has_terran_gas_example_553(few_shot: str) -> None:
    """few_shot 含例 24c：人族气矿 = Refinery，且教 "二气/两个气" = 数量 2。

    回归守卫（#553）：缺这条 → 终端 "下二气/补二气/下两个气" 的数量解析会回到
    flaky（二 被当序数 +1 / done_when.value=None）。"""
    assert "24c" in few_shot, "few_shot 缺例 24c（人族气矿）"
    assert "Refinery" in few_shot, "few_shot 缺人族气矿 structure_type=Refinery"
    # 数量歧义消解关键句：二/两 = 基数 2（不是序数、不是 natural）
    assert "下二气" in few_shot, "few_shot 缺 '下二气' 示例话语"
    assert "基数 2" in few_shot, "few_shot 缺 '二/两=基数2' 歧义消解说明"


# ---------------------------------------------------------------------------
# 别名分组渲染（别名→规范名）：修"地堡"被 ASR 听成"低保"后误判补给站
# ---------------------------------------------------------------------------


def test_race_block_aliases_grouped_mapping_visible() -> None:
    """build_race_block 的别名按"别名…→规范名"分组，让 alias→canonical 映射对 LLM 显式可见。

    回归守卫：扁平别名词表只告诉 LLM"这些是建筑词"、不给映射 → 同音误转词(低保=地堡)
    LLM 会乱猜成补给站。分组后 "低保…→Bunker" 显式，LLM 照表 normalize。
    """
    from pathlib import Path

    from vibecraft.llm.prompt import build_race_block
    from vibecraft.strategy import StrategyLibrary
    from vibecraft.strategy.aliases import AliasTable

    root = Path(__file__).resolve().parents[2]
    at = AliasTable.from_yaml(root / "docs" / "aliases" / "terran.yaml")
    lib = StrategyLibrary.from_directories(
        root / "strategies", root / "docs" / "aliases" / "terran.yaml"
    )
    blk = build_race_block(at, lib, "terran")
    # 分组格式：别名…→Bunker，且"低保"(地堡的 ASR 同音误转)归到 Bunker
    assert "→Bunker" in blk, "建筑别名未按 →规范名 分组"
    assert "低保" in blk and "地堡" in blk, "Bunker 别名缺 低保/地堡"
    # 同一段里 低保 应出现在 Bunker 的分组里（→Bunker 之前的那段含低保）
    seg = blk.split("→Bunker")[0].rsplit(";", 1)[-1]
    assert "低保" in seg, f"低保 未归到 Bunker 分组: {seg!r}"
