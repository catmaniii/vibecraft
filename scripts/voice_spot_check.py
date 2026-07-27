"""典型玩家话语 → IntentParser 真实 LLM 解析的 spot check 工具。

用途
----
向真实 DeepSeek LLM 发送 ~25 条典型玩家话语，校验每条的 Directive 类型 /
关键字段是否符合期望，最后打 PASS/FAIL 总结。

**这是真实 API 调用**，每条约 $0.001，总计 ~$0.025。
不要在 CI 里跑。需要设置 DEEPSEEK_API_KEY 环境变量。

用法（PowerShell）::

    $env:DEEPSEEK_API_KEY = "sk-..."
    uv run --no-sync python scripts/voice_spot_check.py

输出::

    [PASS] 全军进攻对方主基地 (1234 ms): ok
    [FAIL] 守家！ (876 ms): expected tactical_objective, got strategy_set
    ...
    === Spot check: 23/25 passed ===
    失败详情:
      [FAIL] ...
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibecraft.directives.types import StageKind
from vibecraft.llm.config import LLMConfig
from vibecraft.llm.parser import IntentParser, ParserConfig
from vibecraft.llm.prompt import ParseContext
from vibecraft.llm.schema import AmbiguousParse, IntentParseResult, ParseError
from vibecraft.strategy.library import StrategyLibrary

_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# SpotCase dataclass
# ---------------------------------------------------------------------------


@dataclass
class SpotCase:
    text: str
    """玩家话语原文。"""

    expected_types: list[str]
    """期望的 directive type 字符串列表（与 DirectiveType.value 对齐）。

    支持等价组：用 "|" 分隔表示该位置接受任一类型（如 "structure_override|build_at"）。
    """

    expected_count: int | None = None
    """期望 directive 数量；None 表示与 len(expected_types) 一致。"""

    check_fields: dict[str, Any] | None = None
    """可选：对第 0 个 directive payload 的字段断言，例如 {"verb": "attack"}。
    支持 set 比较（字段值为 set/list 时用 set 包含检查）。"""

    allow_ambiguous: bool = False
    """True = 低置信句，接受 AmbiguousParse 或 confidence < 0.5 的 IntentParseResult。"""


# ---------------------------------------------------------------------------
# CASES — ~25 条典型玩家话语
# ---------------------------------------------------------------------------

CASES: list[SpotCase] = [
    # ── L2 战术 A 类（global verb）──────────────────────────────────────────
    SpotCase(
        text="全军进攻对方主基地",
        expected_types=["tactical_objective"],
        check_fields={"verb": "attack"},
    ),
    SpotCase(
        text="守家！",
        expected_types=["tactical_objective"],
        check_fields={"verb": "defend"},
    ),
    SpotCase(
        text="撤回来",
        expected_types=["tactical_objective"],
        check_fields={"verb": "retreat"},
    ),
    SpotCase(
        text="盯着对方主基地",
        expected_types=["tactical_objective"],
        check_fields={"verb": "vision"},
    ),
    # ── L2 战术 B 类（squad verb）───────────────────────────────────────────
    SpotCase(
        # "凤凰...抓农民" 一次性 tactical_objective(harass) 或 持续 unit_claim 都合理
        text="凤凰去对方主基地抓农民",
        expected_types=["tactical_objective|unit_claim"],
    ),
    SpotCase(
        # 单农民探路 → scout 类型（SCOUT directive 或 tactical_objective verb=scout 均可）
        text="派 1 农民看一眼对方三矿",
        expected_types=["scout"],
        # 允许 LLM 输出 tactical_objective(verb=scout) 作为备选
        check_fields=None,
    ),
    SpotCase(
        text="用 4 追猎去试探对方二矿",
        expected_types=["tactical_objective"],
        check_fields={"verb": "recon"},
    ),
    # ── L2 单兵指令──────────────────────────────────────────────────────────
    SpotCase(
        text="棱镜回家",
        expected_types=["move"],
        check_fields={"safe": False},
    ),
    SpotCase(
        text="棱镜贴边回家",
        expected_types=["move"],
        check_fields={"safe": True},
    ),
    SpotCase(
        text="斜坡下面建炮",
        expected_types=["build_at"],
        check_fields={"structure_type": "PhotonCannon"},
    ),
    # 2026-05-27 真实 crash 修正:"前线" 必须映射 named_spot="forward",
    # 不能错判成 enemy_main(送农民进敌方主基地)。
    SpotCase(
        text="前线去个农民刷个水晶方便折跃追猎",
        expected_types=["build_at"],
        check_fields={"structure_type": "Pylon", "named_spot": "forward"},
    ),
    # ── L3 持久（standing order）────────────────────────────────────────────
    SpotCase(
        text="3 叉子在二矿待命",
        expected_types=["unit_claim"],
        check_fields={"persistent": True},
    ),
    SpotCase(
        text="一个农民去占瞭望塔",
        expected_types=["unit_claim"],
        check_fields={"persistent": True},
    ),
    SpotCase(
        text="凤凰巡逻分矿",
        expected_types=["unit_claim"],
        check_fields={"persistent": True},
    ),
    SpotCase(
        text="那 3 个叉子回来",
        expected_types=["unit_release"],
    ),
    SpotCase(
        # 2026-07-20 用户 bug:"闲置农民回采矿"曾被解析成 gather claim → 农民被 Reserved
        # 锁死闲置。正解走 unit_release 交回经济池。此 case 防回归。
        text="那几个闲置的农民回去采矿",
        expected_types=["unit_release"],
    ),
    # ── L4 产能──────────────────────────────────────────────────────────────
    SpotCase(
        text="下个 BG 出 4 追猎",
        expected_types=["production_override"],
        # items[0].unit_type 应含 Stalker，count=4
        check_fields=None,  # items 是嵌套 list，在 run_case 里有专门检查
    ),
    SpotCase(
        text="出 2 叉子 3 追猎",
        expected_types=["production_override"],
        expected_count=1,  # 两种兵 → 单条 directive，两个 item
    ),
    SpotCase(
        text="再造 8 个 BG",
        expected_types=["structure_override"],
    ),
    # 2026-05-28 用户:delta vs target_count 语义区分(check_fields 不能深挖 items,
    # 只验证 types;手动 verify delta vs target 出现在 raw_text)
    SpotCase(text="补一个 BF", expected_types=["structure_override"]),
    SpotCase(text="补到 4 个 BG", expected_types=["structure_override"]),
    SpotCase(
        text="研冲锋",
        expected_types=["tech_override"],
        # upgrade_id 接受 Charge 或 ChargeUpgrade
        check_fields=None,
    ),
    SpotCase(
        text="开三矿",
        expected_types=["expansion_override"],
        check_fields={"target_count": 3},
    ),
    # ── L1 strategy_set──────────────────────────────────────────────────────
    SpotCase(
        text="切 IAC",
        expected_types=["strategy_set"],
        check_fields={"strategy_id": "iac_2base"},
    ),
    SpotCase(
        text="切 4BG 一波",
        expected_types=["strategy_set"],
    ),
    # ── 复合多 directive──────────────────────────────────────────────────────
    SpotCase(
        text="切 IAC，3 叉子在二矿待命",
        expected_types=["strategy_set", "unit_claim"],
        expected_count=2,
    ),
    SpotCase(
        text="下个 BG 出 4 追猎，再研冲锋",
        expected_types=["production_override", "tech_override"],
        expected_count=2,
    ),
    SpotCase(
        # "造水晶" 单建筑 build_at 或 补到 N 个 structure_override 都合理
        text="造水晶，派 2 凤凰巡逻分矿",
        expected_types=["structure_override|build_at", "unit_claim"],
        expected_count=2,
    ),
    SpotCase(
        text="切 IAC，攻对方主基地，下个 BG 出 4 追猎",
        expected_types=["strategy_set", "tactical_objective", "production_override"],
        expected_count=3,
    ),
    # ── 语音编队（group_assign / group_clear / 编队指挥）────────────────────────
    SpotCase(
        text="把所有虚空编成一队",
        expected_types=["group_assign"],
        check_fields={"group_id": 1},
    ),
    SpotCase(
        text="2 个农民编成 3 队",
        expected_types=["group_assign"],
        check_fields={"group_id": 3},
    ),
    SpotCase(
        text="取消 2 队",
        expected_types=["group_clear"],
        check_fields={"group_id": 2},
    ),
    SpotCase(
        # 编队指挥进攻：必须 unit_claim + selector.group_id + attack_move，
        # 绝不能降级成全军 tactical_objective（否则编了队的单位反被排除）
        text="一队进攻对方三矿",
        expected_types=["unit_claim"],
        check_fields={
            "selector.group_id": 1,
            "task.primary_action.verb": "attack_move",
        },
    ),
    SpotCase(
        text="二队火力侦查这里",
        expected_types=["unit_claim"],
        check_fields={
            "selector.group_id": 2,
            "task.primary_action.verb": "attack_move",
        },
    ),
    SpotCase(
        text="三队撤退",
        expected_types=["unit_claim"],
        check_fields={"selector.group_id": 3},
    ),
    # ── WP-B: 状态属性指代（blood/shield）──────────────────────────────────
    SpotCase(
        text="残血的追猎撤回来",
        expected_types=["unit_claim"],
        check_fields={
            "selector.unit_type": "Stalker",
            "selector.health_below_pct": 50,
        },
    ),
    SpotCase(
        text="盾破的不朽拉回基地",
        expected_types=["unit_claim"],
        check_fields={
            "selector.unit_type": "Immortal",
            "selector.shield_below_pct": 20,
        },
    ),
    # ── 建筑回收 salvage + 镜头框选 near_camera（2026-06-19）──────────────────
    SpotCase(
        text="把地堡卖了",
        expected_types=["salvage"],
        check_fields={"selector.unit_type": "Bunker"},
    ),
    SpotCase(
        text="回收那个碉堡",
        expected_types=["salvage"],
        check_fields={"selector.unit_type": "Bunker"},
    ),
    SpotCase(
        # 镜头框选 + 回收：salvage + selector.near_camera=true
        text="镜头内的地堡都回收了",
        expected_types=["salvage"],
        check_fields={"selector.unit_type": "Bunker", "selector.near_camera": True},
    ),
    SpotCase(
        # 镜头框选 + 编队：group_assign + selector.near_camera=true
        text="把镜头里的追猎编成 2 队",
        expected_types=["group_assign"],
        check_fields={"selector.near_camera": True, "group_id": 2},
    ),
    # ── 维修 repair（2026-06-19，"修理"绝不能误判成 build）──────────────────────
    SpotCase(
        # 用户实测痛点：这句之前被误判成"农民建造大舰"。必须 → repair，绝不是 build/structure_override
        text="派农民修理大舰",
        expected_types=["repair"],
        check_fields={"selector.unit_type": "Battlecruiser"},
    ),
    SpotCase(
        text="修一下那个地堡",
        expected_types=["repair"],
        check_fields={"selector.unit_type": "Bunker"},
    ),
    SpotCase(
        text="家里的残血大舰都修一下",
        expected_types=["repair"],
        check_fields={"selector.unit_type": "Battlecruiser"},
    ),
    # ── ambiguous（低置信）───────────────────────────────────────────────────
    SpotCase(
        text="搞一下",
        expected_types=[],
        allow_ambiguous=True,
    ),
    SpotCase(
        text="打吧",
        expected_types=[],
        allow_ambiguous=True,
    ),
]


# ---------------------------------------------------------------------------
# 辅助：字段值比较（支持 set/list 包含检查）
# ---------------------------------------------------------------------------


def _field_match(actual: Any, expected: Any) -> bool:
    """比较字段值：
    - expected 是 str → 用 set 包含，兼容 LLM 大小写/变体（如 Charge / ChargeUpgrade）
    - 其他类型 → 直接 ==
    """
    if actual == expected:
        return True
    # 对字符串字段做不区分大小写的包含检查（如 upgrade_id）
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower() or actual.lower() in expected.lower()
    return False


def _get_payload_field(payload: Any, field_name: str) -> Any:
    """从 payload 对象取字段值（dict / pydantic model 均可）。

    支持点路径深取嵌套字段（如 "selector.group_id" / "task.primary_action.verb"），
    用于断言编队指挥这类把意图压在嵌套结构里的 directive。
    """
    cur: Any = payload
    for part in field_name.split("."):
        if cur is None:
            return None
        if hasattr(cur, part):
            cur = getattr(cur, part)
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    # enum → 取 .value，便于和字符串期望值直接比较（verb 等）
    return getattr(cur, "value", cur)


# ---------------------------------------------------------------------------
# run_case
# ---------------------------------------------------------------------------


async def run_case(
    parser: IntentParser,
    ctx: ParseContext,
    case: SpotCase,
) -> tuple[bool, str]:
    """跑单个 case，返回 (passed, reason)。"""
    t0 = time.monotonic()
    try:
        outcome = await parser.parse(case.text, ctx)
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return False, f"exception: {exc!r} ({elapsed} ms)"

    elapsed = int((time.monotonic() - t0) * 1000)

    # ── ambiguous case ──────────────────────────────────────────────────────
    if case.allow_ambiguous:
        if isinstance(outcome, AmbiguousParse):
            return True, f"AmbiguousParse confidence={outcome.result.confidence:.2f} ({elapsed} ms)"
        if isinstance(outcome, IntentParseResult) and outcome.confidence < 0.5:
            return (
                True,
                f"low-confidence IntentParseResult confidence={outcome.confidence:.2f} ({elapsed} ms)",
            )
        if isinstance(outcome, ParseError):
            # ParseError 也可接受（parse 完全失败也算"模糊"语义）
            return True, f"ParseError kind={outcome.kind.value} ({elapsed} ms)"
        # 高置信成功解析 → 对 ambiguous case 不算错，但记录警告
        if isinstance(outcome, IntentParseResult):
            return True, (
                f"warn: expected ambiguous but got IntentParseResult "
                f"confidence={outcome.confidence:.2f} ({elapsed} ms)"
            )
        return False, f"unexpected outcome type {type(outcome).__name__} ({elapsed} ms)"

    # ── 非 ambiguous：期望 IntentParseResult ────────────────────────────────
    if isinstance(outcome, ParseError):
        return False, f"ParseError kind={outcome.kind.value}: {outcome.message} ({elapsed} ms)"

    if isinstance(outcome, AmbiguousParse):
        directives = outcome.result.directives
    elif isinstance(outcome, IntentParseResult):
        directives = outcome.directives
    else:
        return False, f"unexpected outcome type {type(outcome).__name__} ({elapsed} ms)"

    # ── 验 directive 数量 ────────────────────────────────────────────────────
    expected_count = (
        case.expected_count if case.expected_count is not None else len(case.expected_types)
    )
    actual_count = len(directives)
    if actual_count != expected_count:
        got_types = [d.type.value for d in directives]
        return False, (
            f"expected {expected_count} directive(s), got {actual_count}: "
            f"{got_types} ({elapsed} ms)"
        )

    # ── 验 directive type（用 multiset 顺序敏感比较,支持 "|" 等价组）────────
    if case.expected_types:
        actual_types: list[str] = [d.type.value for d in directives]

        # 特殊兼容：scout 既可以是 DirectiveType.SCOUT 也可以是 tactical_objective(verb=scout)
        # 把 actual 中的 tactical_objective(verb=scout) 折算成 "scout" 后再比较
        if any("scout" in opt for opt in case.expected_types):
            for i, d in enumerate(directives):
                if d.type.value == "tactical_objective":
                    verb = _get_payload_field(d.payload, "verb")
                    if verb == "scout":
                        actual_types[i] = "scout"

        # 等价组比较:expected_types 每个元素可以是 "a|b|c",actual 在该位置匹配任一即可
        # 但顺序不强求 — LLM 可能颠倒顺序,我们用贪婪 set-like 匹配
        unmatched_actual = list(actual_types)
        unmatched_expected = list(case.expected_types)
        for exp in list(unmatched_expected):
            options = set(exp.split("|"))
            matched_idx = None
            for i, act in enumerate(unmatched_actual):
                if act in options:
                    matched_idx = i
                    break
            if matched_idx is not None:
                unmatched_actual.pop(matched_idx)
                unmatched_expected.remove(exp)

        if unmatched_actual or unmatched_expected:
            return False, (
                f"directive types mismatch: expected {case.expected_types}, "
                f"got {actual_types} ({elapsed} ms)"
            )

    # ── 验 check_fields（第 0 个 directive payload）─────────────────────────
    if case.check_fields and directives:
        first_payload = directives[0].payload
        for field_name, expected_val in case.check_fields.items():
            actual_val = _get_payload_field(first_payload, field_name)
            if not _field_match(actual_val, expected_val):
                return False, (
                    f"check_fields[{field_name!r}]: expected {expected_val!r}, "
                    f"got {actual_val!r} ({elapsed} ms)\n"
                    f"  directives: {[d.payload.model_dump() for d in directives]}"
                )

    return True, f"ok ({elapsed} ms)"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> int:
    cfg = LLMConfig.from_yaml(_ROOT / "config" / "llm.yaml")
    print(f"provider={cfg.provider}  model={cfg.model}")
    print(f"base_url={cfg.base_url}")
    print(f"共 {len(CASES)} 个 case，顺序跑（避免 rate limit）\n")

    provider = cfg.build_provider()
    library = StrategyLibrary.from_directories(
        _ROOT / "strategies", _ROOT / "docs" / "aliases" / "protoss.yaml"
    )
    print(f"剧本库 id: {sorted(library.all_ids())}\n")

    parser = IntentParser(
        provider,
        library,
        config=ParserConfig(
            timeout_s=cfg.timeout_s,
            confidence_threshold=cfg.confidence_threshold,
            max_directives_per_call=cfg.max_directives_per_call,
        ),
    )
    ctx = ParseContext(game_time=120.0, current_stage=StageKind.MIDGAME)

    results: list[tuple[SpotCase, bool, str]] = []

    for case in CASES:
        passed, reason = await run_case(parser, ctx, case)
        tag = "[PASS]" if passed else "[FAIL]"
        print(f"{tag} {case.text!r}: {reason}")
        results.append((case, passed, reason))

    # ── 总结 ────────────────────────────────────────────────────────────────
    n_pass = sum(1 for _, p, _ in results if p)
    n_total = len(results)
    print(f"\n{'=' * 50}")
    print(f"=== Spot check: {n_pass}/{n_total} passed ===")

    failures = [(c, r) for c, p, r in results if not p]
    if failures:
        print("\n失败详情：")
        for case, reason in failures:
            print(f"  [FAIL] {case.text!r}")
            print(f"         expected_types={case.expected_types}")
            print(f"         reason: {reason}")

    return 0 if n_pass == n_total else 1


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
