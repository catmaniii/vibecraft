"""LLM eval 详细测试报告 generator。

读 `logs/llm_eval/*.json`(各 model + retry config 的 trial dump),
合并成 markdown 详细报告:
- inject / spec / 每 trial outcome / FAIL 原因 / 耗时

用法:
    .venv/Scripts/python.exe scripts/llm_eval_report.py
    .venv/Scripts/python.exe scripts/llm_eval_report.py --out docs/llm-eval-detailed.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))  # 让 tests/ 包 import 得到

from tests.llm_eval.expected_specs import LLM_EVAL_CASES  # noqa: E402


@dataclass
class RunData:
    """单次 eval run(一个 model + retry config)的数据。"""
    label: str
    model: str
    max_retries: int
    total_pass: int
    total_runs: int
    accuracy: float
    avg_latency_ms: float
    trials: list[dict[str, Any]]


def load_runs(json_dir: Path) -> list[RunData]:
    """读 logs/llm_eval/*.json 加载所有 run。按 model 短名 + retry 标签排序。"""
    runs: list[RunData] = []
    for jpath in sorted(json_dir.glob("*.json")):
        data = json.loads(jpath.read_text(encoding="utf-8"))
        model = data["model"]
        retries = data["max_retries"]
        # 短名:flash / pro
        short = "Flash" if "flash" in model else ("Pro" if "pro" in model else model)
        label = f"{short} (retry={retries})"
        runs.append(RunData(
            label=label,
            model=model,
            max_retries=retries,
            total_pass=data["total_pass"],
            total_runs=data["total_runs"],
            accuracy=data["accuracy"],
            avg_latency_ms=data["avg_latency_ms"],
            trials=data["trials"],
        ))
    return runs


def _spec_summary(case_name: str) -> dict[str, Any]:
    """从 LLM_EVAL_CASES 里找 spec 的字段摘要。"""
    for spec in LLM_EVAL_CASES:
        if spec.name == case_name:
            expect_type = spec.expect_type
            if isinstance(expect_type, list):
                expect_type_str = " | ".join(t.value for t in expect_type)
            else:
                expect_type_str = expect_type.value
            return {
                "expect_type": expect_type_str,
                "must_have": dict(spec.must_have_paths),
                "forbidden": dict(spec.forbidden_paths),
                "allow_extra": spec.allow_extra_directives,
            }
    return {}


def _format_outcome(o: dict[str, Any] | None) -> str:
    """把 trial outcome dump 折叠成短摘要 + (折叠)详细 JSON。"""
    if o is None:
        return "(no outcome)"
    kind = o.get("kind", "?")
    if kind == "ParseError":
        return f"❌ ParseError({o.get('error_kind','?')}): {o.get('message','')[:200]}"
    if kind == "AmbiguousParse":
        return (
            f"⚠️ AmbiguousParse confidence={o.get('confidence','?')} "
            f"interp={o.get('interpretation_zh','')[:80]}"
        )
    if kind == "IntentParseResult":
        directives = o.get("directives", [])
        if not directives:
            return f"✓ IntentParseResult (空 directives, interp={o.get('interpretation_zh','')[:80]})"
        # 抽每条 directive 的 type + 关键字段
        summaries = []
        for d in directives:
            p = d.get("payload", {}) or {}
            t = p.get("type", "?")
            # 抽几个关键字段
            keyfields = {}
            for k in ["stage", "strategy_id", "verb", "stance", "target_area",
                      "unit_type", "count", "upgrade_id", "target_count",
                      "selector", "task", "target", "done_when", "persistent"]:
                if k in p:
                    v = p[k]
                    if isinstance(v, dict):
                        v = {ik: iv for ik, iv in v.items() if ik != "type"}
                    keyfields[k] = v
            summaries.append(f"{t}({_short_fields(keyfields)})")
        return "✓ IntentParseResult: " + " | ".join(summaries)
    return f"? {kind}: {str(o)[:150]}"


def _short_fields(d: dict[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        if isinstance(v, dict):
            inner = ", ".join(f"{ik}={iv!r}" for ik, iv in list(v.items())[:3])
            parts.append(f"{k}={{{inner}}}")
        elif isinstance(v, list):
            parts.append(f"{k}=[{len(v)} items]")
        else:
            sval = repr(v) if not isinstance(v, str) else f'"{v}"'
            if len(sval) > 60:
                sval = sval[:57] + "..."
            parts.append(f"{k}={sval}")
    return ", ".join(parts)


def _trials_for(run: RunData, case_name: str) -> list[dict[str, Any]]:
    return [t for t in run.trials if t["case_name"] == case_name]


def render_report(runs: list[RunData]) -> str:
    """生成 markdown 报告。"""
    lines: list[str] = []
    lines.append("# LLM 指令解析详细测试报告\n")
    lines.append("**测试时间**：自动生成\n")
    lines.append("")

    # ===== 汇总段 =====
    lines.append("## 汇总\n")
    lines.append("| 配置 | Accuracy | 平均耗时 |")
    lines.append("|---|---|---|")
    for run in runs:
        lines.append(
            f"| **{run.label}** | "
            f"{run.total_pass}/{run.total_runs} = **{run.accuracy:.1f}%** | "
            f"**{run.avg_latency_ms:.0f} ms** |"
        )
    lines.append("")

    # ===== per-case accuracy 矩阵 =====
    lines.append("## per-case accuracy 矩阵\n")
    header = "| Case | inject | " + " | ".join(r.label for r in runs) + " |"
    sep = "|---|---|" + "|".join(["---"] * len(runs)) + "|"
    lines.append(header)
    lines.append(sep)
    case_order = [c.name for c in LLM_EVAL_CASES]
    for case_name in case_order:
        per_run_summary = []
        inject = "?"
        for run in runs:
            trials = _trials_for(run, case_name)
            if not trials:
                per_run_summary.append("-")
                continue
            if inject == "?":
                inject = trials[0]["inject"]
            n_pass = sum(1 for t in trials if t["passed"])
            n_total = len(trials)
            mark = "✓" if n_pass == n_total else ("✗" if n_pass == 0 else "~")
            per_run_summary.append(f"{mark} {n_pass}/{n_total}")
        lines.append(f"| {case_name} | `{inject}` | " + " | ".join(per_run_summary) + " |")
    lines.append("")

    # ===== per-case 详细 =====
    lines.append("## 每 case 详细数据\n")
    for case_name in case_order:
        spec = _spec_summary(case_name)
        first_inject = "?"
        for run in runs:
            ts = _trials_for(run, case_name)
            if ts:
                first_inject = ts[0]["inject"]
                break
        lines.append(f"### {case_name}\n")
        lines.append(f"**Inject**：`{first_inject}`\n")
        lines.append("**Expected**：")
        lines.append(f"- type: `{spec.get('expect_type','?')}`")
        if spec.get("must_have"):
            mh = ", ".join(f"`{k}`={v!r}" for k, v in spec["must_have"].items())
            lines.append(f"- must_have: {mh}")
        if spec.get("forbidden"):
            fb = ", ".join(f"`{k}` ∉ {v!r}" for k, v in spec["forbidden"].items())
            lines.append(f"- forbidden: {fb}")
        lines.append("")

        # 每个 run 的 3 个 trial
        for run in runs:
            trials = _trials_for(run, case_name)
            if not trials:
                lines.append(f"**{run.label}**：(no data)\n")
                continue
            n_pass = sum(1 for t in trials if t["passed"])
            n_total = len(trials)
            avg_latency = sum(t["latency_ms"] for t in trials) / max(1, n_total)
            mark = "✓" if n_pass == n_total else ("✗" if n_pass == 0 else "~")
            lines.append(
                f"**{run.label}**：{mark} {n_pass}/{n_total} (avg {avg_latency:.0f} ms)"
            )
            for i, t in enumerate(trials, 1):
                status = "PASS" if t["passed"] else "**FAIL**"
                lines.append(f"- trial {i} {status} ({t['latency_ms']:.0f} ms)")
                lines.append(f"  - outcome: {_format_outcome(t['outcome'])}")
                if not t["passed"]:
                    reason = t.get("reason", "")[:300]
                    lines.append(f"  - reason: {reason}")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json-dir", default="logs/llm_eval")
    p.add_argument("--out", default="docs/llm-eval-detailed-report.md")
    args = p.parse_args()

    json_dir = Path(args.json_dir)
    if not json_dir.exists():
        print(f"ERROR: {json_dir} 不存在", file=sys.stderr)
        return 1

    runs = load_runs(json_dir)
    if not runs:
        print(f"ERROR: {json_dir} 没有 json 文件", file=sys.stderr)
        return 1
    print(f"已加载 {len(runs)} 个 eval run:")
    for r in runs:
        print(f"  - {r.label}: {r.total_pass}/{r.total_runs} = {r.accuracy:.1f}%, {r.avg_latency_ms:.0f}ms")

    report = render_report(runs)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"报告已写到: {out_path} ({len(report.splitlines())} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
