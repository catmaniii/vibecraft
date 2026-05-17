"""跑 14 case × N trial 实测 LLM 解析正确率。

每个 case 独立 parametrize,N trial 也 parametrize → pytest 视角是 14 × N
独立 test。terminal_summary 汇总 per-case 命中率(在 conftest.py)。

跑法:
    .venv/Scripts/python.exe -m pytest tests/llm_eval -m llm_eval -v

切 model 对比:
    set VIBECRAFT_LLM_MODEL=deepseek-v4-flash && .venv/Scripts/python.exe -m pytest tests/llm_eval -m llm_eval -v
    set VIBECRAFT_LLM_MODEL=deepseek-v4-pro && .venv/Scripts/python.exe -m pytest tests/llm_eval -m llm_eval -v
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from tests.llm_eval.expected_specs import LLM_EVAL_CASES
from tests.llm_eval.score import ExpectedSpec, score_outcome

if TYPE_CHECKING:
    from vibecraft.llm.parser import IntentParser
    from vibecraft.llm.prompt import ParseContext

    from tests.llm_eval.conftest import _EvalStats


# 每 case 跑 3 次(design 拍板:平衡 accuracy 估计 vs LLM cost)
NUM_TRIALS = 3


@pytest.mark.llm_eval
@pytest.mark.parametrize(
    "spec", LLM_EVAL_CASES, ids=[c.name for c in LLM_EVAL_CASES]
)
@pytest.mark.parametrize("trial", range(NUM_TRIALS), ids=[f"trial{i}" for i in range(NUM_TRIALS)])
async def test_parse_accuracy(
    spec: ExpectedSpec,
    trial: int,
    llm_parser: "IntentParser",
    mock_parse_context: "ParseContext",
    eval_stats: "_EvalStats",
) -> None:
    """单 case 单 trial:调真 LLM → score → 记录到 stats → assert PASS。

    pytest 报错 = trial 失败;汇总在 terminal_summary 输出 per-case 通过率。
    """
    t0 = time.monotonic()
    outcome = await llm_parser.parse(spec.inject, mock_parse_context)
    latency_ms = (time.monotonic() - t0) * 1000

    score = score_outcome(outcome, spec)
    eval_stats.record(spec.name, score.passed, score.reason, latency_ms)

    # 失败不抛(让 terminal_summary 看完整 picture);用 warn + xfail-ish 方式记
    # 实际上 pytest fail 行为更直白,FAIL test count 直接体现 accuracy
    assert score.passed, f"[{spec.name} trial{trial}] {score.reason}"
