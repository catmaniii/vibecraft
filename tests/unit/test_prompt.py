"""P0i Task 17: prompt 内容 snapshot 测试。

验证 build_system_prompt / build_few_shot 返回值里包含预期的关键词/片段。
测试不 mock LLM，只检查 prompt 字符串内容。
"""

from __future__ import annotations

import pytest

from vibecraft.llm.prompt import build_few_shot, build_system_prompt
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
    assert (
        "必填" in system_prompt
        or "必给" in system_prompt
        or "required" in system_prompt.lower()
    )


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


def test_prompt_a_verbs_does_not_list_hold_under_tactical(system_prompt: str) -> None:
    """hold 不在 tactical_objective A 类 verb 列表（TacticalVerb literal 无此值）。
    hold 是 engagement_constraint.stance 的值，不是 tactical_objective 的 verb。
    """
    import re

    # 找 A 系列规则段：格式 "A 系列 verb (x / y / z)"
    m = re.search(r"A 系列 verb \(([^)]+)\)", system_prompt)
    assert m is not None, "未找到 'A 系列 verb (...)' 规则段"
    verbs_in_a = m.group(1)
    # hold 不应出现在 tactical_objective A 系列 verb 列表
    assert "hold" not in verbs_in_a.split(" / "), (
        f"A 系列 tactical verb 列表含 'hold'（TacticalVerb enum 无此值）: {verbs_in_a!r}"
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
