"""LLM eval suite 共享 fixture + collection hook(默认 skip)。

默认 `pytest` 不跑 llm_eval(真调 LLM API 烧钱)。需要 `pytest -m llm_eval`
或 `pytest tests/llm_eval -m llm_eval` 才会真跑。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from vibecraft.directives.types import StageKind
from vibecraft.llm.config import LLMConfig
from vibecraft.llm.parser import IntentParser, ParserConfig
from vibecraft.llm.prompt import ParseContext
from vibecraft.strategy.library import StrategyLibrary


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """默认 skip llm_eval marker。

    pytest -m llm_eval(或 -m "llm_eval or ...")时 markexpr 包含 llm_eval → 跑;
    没明确指定 → 全部 skip。
    """
    markexpr = config.getoption("-m", "") or ""
    if "llm_eval" in markexpr:
        return  # 用户明确选,放过
    skip_marker = pytest.mark.skip(
        reason="llm_eval 默认 skip(真调 LLM API);用 -m llm_eval 跑"
    )
    for item in items:
        if "llm_eval" in {m.name for m in item.iter_markers()}:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def strategy_library() -> StrategyLibrary:
    """加载真实 strategies/ + aliases/ —— LLM 解析需要 catalog。"""
    project_root = Path(__file__).parent.parent.parent
    strategies_dir = project_root / "strategies"
    aliases_path = project_root / "aliases" / "protoss.yaml"
    return StrategyLibrary.from_directories(strategies_dir, aliases_path)


@pytest.fixture(scope="session")
def llm_parser(strategy_library: StrategyLibrary) -> IntentParser:
    """真实 LLM provider + IntentParser。

    按 config/llm.yaml 读 provider(可经环境变量 VIBECRAFT_LLM_MODEL 覆盖 model)。
    无 session(不写 jsonl 落盘)。
    """
    project_root = Path(__file__).parent.parent.parent
    llm_config = LLMConfig.from_yaml_or_defaults(project_root / "config" / "llm.yaml")
    # 允许命令行覆盖 model(eval Flash vs Pro 用)
    override_model = os.environ.get("VIBECRAFT_LLM_MODEL")
    if override_model:
        llm_config.model = override_model
    provider = llm_config.build_provider()
    return IntentParser(
        provider=provider,
        library=strategy_library,
        config=ParserConfig(timeout_s=30.0),  # eval 容忍更长 timeout(Pro 慢)
    )


@pytest.fixture
def mock_parse_context() -> ParseContext:
    """默认 mock context:3 分钟,开局过渡到中期,2 基,常见资源/兵力。"""
    return ParseContext(
        game_time=180.0,
        current_stage=StageKind.OPENING,
        active_strategies={
            StageKind.OPENING: "1g_robo_immortal",
            StageKind.MIDGAME: None,
            StageKind.LATEGAME: None,
        },
        minerals=500,
        gas=200,
        supply_used=30,
        supply_cap=40,
        expansion_count=2,
        army_summary={"Probe": 22, "Immortal": 1, "Stalker": 2},
        enemy_summary={"Marine": 8, "Marauder": 2},
        recent_commands=[],
        standing_orders=[],
    )


# 当前 eval round 累计 stats(session-scoped,plugin-style 收集)
class _EvalStats:
    def __init__(self) -> None:
        self.per_case: dict[str, list[bool]] = {}
        self.per_case_reason: dict[str, list[str]] = {}
        self.latencies_ms: list[float] = []

    def record(self, case_name: str, passed: bool, reason: str, latency_ms: float) -> None:
        self.per_case.setdefault(case_name, []).append(passed)
        self.per_case_reason.setdefault(case_name, []).append(reason)
        self.latencies_ms.append(latency_ms)


@pytest.fixture(scope="session")
def eval_stats() -> _EvalStats:
    return _EvalStats()


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    """eval 跑完后输出每 case 的命中率汇总。"""
    stats: _EvalStats | None = getattr(config, "_eval_stats", None)
    if stats is None or not stats.per_case:
        return
    tr = terminalreporter
    tr.section("LLM eval 汇总 (per-case accuracy)")
    total_pass = 0
    total_runs = 0
    for name in sorted(stats.per_case.keys()):
        results = stats.per_case[name]
        n_pass = sum(results)
        n_total = len(results)
        pct = 100 * n_pass / n_total if n_total else 0
        marker = "✓" if n_pass == n_total else ("✗" if n_pass == 0 else "~")
        tr.write_line(f"  {marker} {name:38s} {n_pass}/{n_total} {pct:5.1f}%")
        if n_pass < n_total:
            reasons = stats.per_case_reason[name]
            for i, (p, r) in enumerate(zip(results, reasons, strict=False)):
                if not p:
                    tr.write_line(f"      [trial {i + 1} FAIL] {r}")
        total_pass += n_pass
        total_runs += n_total
    overall_pct = 100 * total_pass / total_runs if total_runs else 0
    tr.write_line("")
    tr.write_line(f"  TOTAL: {total_pass}/{total_runs} ({overall_pct:.1f}%)")
    if stats.latencies_ms:
        avg_ms = sum(stats.latencies_ms) / len(stats.latencies_ms)
        tr.write_line(f"  平均 LLM 耗时: {avg_ms:.0f}ms")


@pytest.fixture(autouse=True)
def _wire_stats(request: pytest.FixtureRequest, eval_stats: _EvalStats) -> None:
    """把 eval_stats 挂到 config 上,terminal_summary 拿得到。"""
    request.config._eval_stats = eval_stats  # type: ignore[attr-defined]
