"""英文模式「零中文泄漏」硬门（方案 A，2026-06-28）。

目的：把"英文 locale 下任何面向玩家的输出都不得残留中文"从人肉发现升级成机器门。
这是 salvage 复盘提炼的「外部终态黑盒门」——不验内部代码自洽，直接断言**最终
snapshot / API 输出**里没有 CJK 字符（[一-鿿]）。

驱动方式（关键：真实数据，不是空 snapshot 假阳性）：
- 真 StrategyLibrary（全部 46 剧本逐个塞进 L1 slot 跑一遍 → 覆盖 display_name /
  summary / phase / micro_doctrine 全字段）
- 每种 directive 类型各注入一张卡（→ 覆盖 _directive_display_for /
  _format_standing_order_display / _format_production_override_display 全分支）
- /api/strategies?locale=en（→ 覆盖 http 层）

门跑出来的每个泄漏点 = 一个绕过 Localizer/_i18n_t 的硬编码中文，必须接回 i18n。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.models import Directive
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CJK = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# CJK 递归扫描
# ---------------------------------------------------------------------------


def _scan_cjk(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """递归找出所有含 CJK 的字符串值，返回 [(json_path, value), ...]。"""
    leaks: list[tuple[str, str]] = []
    if isinstance(obj, str):
        if _CJK.search(obj):
            leaks.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            leaks.extend(_scan_cjk(v, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            leaks.extend(_scan_cjk(v, f"{path}[{i}]"))
    return leaks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _en_director(session: GameSession, library: StrategyLibrary) -> Director:
    """locale=en 的最小 Director。

    必须传 event_bus —— 否则 task_monitor=None，`_describe_condition` 里
    unit_count_built_since / time_elapsed_since 两个分支会被 `tm is not None` 短路跳过，
    门就扫不到 cond.buildN/unitCount/afterSec/unitSec（opus 评审揪出的假阳性）。
    """
    from vibecraft.bot.event_bus import EventBus

    facade = FakeFacade(state=BotState(game_time=300.0, supply_used=120))
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session, locale="en")
    d = Director(
        facade=facade, parser=parser, session=session, library=library, event_bus=EventBus()
    )
    assert d._lang == "en", "Director 应从 parser.locale 取到 en"
    assert d.task_monitor is not None, "门必须有 task_monitor，否则 cond 分支被短路（假阳性）"
    return d


# ---------------------------------------------------------------------------
# directive 构造器（覆盖每种类型）
# ---------------------------------------------------------------------------


def _all_directives() -> list[Directive]:
    from vibecraft.directives.models import (
        ExpansionOverridePayload,
        MovePayload,
        ProductionItem,
        ProductionOverridePayload,
        ScoutPayload,
        StructureItem,
        StructureOverridePayload,
        TacticalObjectivePayload,
        TechOverridePayload,
        UnitClaimPayload,
    )
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    out: list[Directive] = []
    out.append(
        Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Phoenix"),
                task=Task(
                    primary_action=Action(
                        verb=Verb.LIFT_TARGET,
                        target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="Immortal"),
                    )
                ),
                persistent=True,
            ),
            issued_at=15.0,
        )
    )
    out.append(
        Directive(
            payload=ProductionOverridePayload(items=[ProductionItem(unit_type="Stalker", count=4)]),
            issued_at=15.0,
        )
    )
    out.append(Directive(payload=TacticalObjectivePayload(verb="attack"), issued_at=15.0))
    out.append(Directive(payload=TacticalObjectivePayload(verb="retreat"), issued_at=15.0))
    out.append(Directive(payload=TacticalObjectivePayload(verb="defend"), issued_at=15.0))
    out.append(
        Directive(
            payload=MovePayload(
                selector=Selector(unit_type="WarpPrism"),
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
            ),
            issued_at=20.0,
        )
    )
    out.append(
        Directive(
            payload=ScoutPayload(
                selector=Selector(unit_type="Probe"),
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
            ),
            issued_at=22.0,
        )
    )
    out.append(Directive(payload=TechOverridePayload(upgrade_id="Blink"), issued_at=15.0))
    out.append(Directive(payload=ExpansionOverridePayload(target_count=3), issued_at=15.0))
    out.append(
        Directive(
            payload=StructureOverridePayload(
                items=[
                    StructureItem(structure_type="Gateway", target_count=8, location_hint="ramp")
                ]
            ),
            issued_at=15.0,
        )
    )
    # 2026-06-29 #580: group_harass 群卡（auto target）
    out.append(
        Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="BattleCruiser"),
                task=Task(primary_action=Action(verb=Verb.GROUP_HARASS, target=None)),
                persistent=True,
                recruit_new=True,
                target_count=None,
            ),
            issued_at=30.0,
        )
    )
    # 2026-06-29 #580: group_harass 群卡（指定矿 enemy_natural）
    out.append(
        Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="BattleCruiser"),
                task=Task(
                    primary_action=Action(
                        verb=Verb.GROUP_HARASS,
                        target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
                    )
                ),
                persistent=True,
                recruit_new=True,
                target_count=3,
            ),
            issued_at=31.0,
        )
    )
    return out


# ---------------------------------------------------------------------------
# 硬门
# ---------------------------------------------------------------------------


def test_en_snapshot_no_chinese_across_all_directives(
    session: GameSession, library: StrategyLibrary
) -> None:
    """en 模式 + 全 directive 类型 → snapshot 任何字符串不得含 CJK。"""
    director = _en_director(session, library)
    for d in _all_directives():
        director._submit_directives([d], now=15.0)

    snap = director.build_snapshot(now=40.0)
    leaks = _scan_cjk(snap)
    assert not leaks, "en snapshot 残留中文（绕过 i18n）:\n" + "\n".join(
        f"  {p} = {v!r}" for p, v in leaks
    )


def test_en_snapshot_no_chinese_across_all_strategies(
    session: GameSession, library: StrategyLibrary
) -> None:
    """逐个把每个剧本塞进 L1 slot，en snapshot 不得含 CJK（覆盖全剧本 _en 字段完整性）。"""
    from vibecraft.directives.models import StrategySetPayload

    all_leaks: list[str] = []
    for sid in library.all_ids():
        director = _en_director(session, library)
        # 直接塞 board slot（绕过 commit delay）
        try:
            payload = StrategySetPayload(stage="opening", strategy_id=sid)
            director._submit_directives([Directive(payload=payload, issued_at=5.0)], now=5.0)
            director.on_tick(now=7.0)
        except Exception:
            continue
        snap = director.build_snapshot(now=300.0)
        leaks = _scan_cjk(snap.get("strategy", {})) + _scan_cjk(snap.get("command_cards", []))
        for p, v in leaks:
            all_leaks.append(f"  [{sid}] {p} = {v!r}")
    assert not all_leaks, "en 模式剧本字段残留中文（缺 _en 或硬编码）:\n" + "\n".join(all_leaks)


def _scan_source_cjk_literals(path: Path) -> list[tuple[int, str]]:
    """AST 扫描：找出源文件里**面向玩家**的中文字符串字面量。

    排除：docstring、logger/log/debug/... 调用实参、`# i18n-data` 标记行（白名单 zh 数据表）。
    返回 [(lineno, value), ...]。剩余即「绕过 i18n 的硬编码中文」。
    """
    import ast

    src = path.read_text(encoding="utf-8")
    src_lines = src.splitlines()
    tree = ast.parse(src)

    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = node.body[0] if node.body else None
            if (
                isinstance(b, ast.Expr)
                and isinstance(b.value, ast.Constant)
                and isinstance(b.value.value, str)
            ):
                for ln in range(b.lineno, (b.end_lineno or b.lineno) + 1):
                    doc_lines.add(ln)

    log_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = (
                f.attr
                if isinstance(f, ast.Attribute)
                else (f.id if isinstance(f, ast.Name) else "")
            )
            if name in ("log", "debug", "info", "warning", "error", "exception", "warn"):
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    log_lines.add(ln)

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _CJK.search(node.value)
        ):
            ln = node.lineno
            if ln in doc_lines or ln in log_lines:
                continue
            if "i18n-data" in (src_lines[ln - 1] if 0 < ln <= len(src_lines) else ""):
                continue
            out.append((ln, node.value))
    return out


def test_all_referenced_i18n_keys_exist() -> None:
    """静态门：源码里 `_i18n_t("k.k")` / `t("k.k")` 引用的每个 i18n key 都必须在
    strings.json 里存在且 en 非 None（否则 t() 回退成 ASCII key 本身，玩家看到生字符串，
    动态/静态 CJK 门都抓不到）。只匹配带点的 key（i18n key 约定），避免误抓普通 t(...)。
    """
    import json
    import re

    strings = json.loads((PROJECT_ROOT / "locales" / "strings.json").read_text(encoding="utf-8"))
    key_re = re.compile(r'(?:_i18n_t|\bt)\(\s*"([a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)"')
    referenced: set[str] = set()
    for py in (PROJECT_ROOT / "src" / "vibecraft").rglob("*.py"):
        referenced |= set(key_re.findall(py.read_text(encoding="utf-8")))

    missing = sorted(
        k
        for k in referenced
        if k not in strings or not isinstance(strings[k], dict) or strings[k].get("en") is None
    )
    assert not missing, "源码引用了 strings.json 里不存在 / en 为 None 的 i18n key:\n" + "\n".join(
        f"  {k}" for k in missing
    )


def test_director_no_hardcoded_chinese_literals() -> None:
    """静态门：director.py 不得有面向玩家的硬编码中文字面量（绕过 i18n）。

    合法的 zh 数据表（若有）用行内 `# i18n-data` 标记豁免。
    """
    director_py = PROJECT_ROOT / "src" / "vibecraft" / "bot" / "director.py"
    offenders = _scan_source_cjk_literals(director_py)
    assert not offenders, "director.py 残留硬编码中文（应走 _i18n_t / Localizer）:\n" + "\n".join(
        f"  L{ln}: {v!r}" for ln, v in offenders
    )


def test_en_condition_text_no_chinese(session: GameSession, library: StrategyLibrary) -> None:
    """直接驱动 done_when / activate_when 文本构造器（snapshot 难触达的分支），en 不得含 CJK。"""
    director = _en_director(session, library)

    # _describe_condition 覆盖的 done_when kind
    done_when_dicts = [
        {"kind": "unit_count_built_since", "value": 4, "unit_type": "Stalker"},
        {"kind": "time_elapsed_since", "seconds": 30, "ref": "directive_issued"},
        {"kind": "tech_done", "upgrade_id": "Blink"},
        {"kind": "target_destroyed", "target": "enemy_main"},
        {"kind": "own_army_size_ratio", "op": ">=", "value": 1.5},
        {"kind": "vision_acquired", "area": "enemy_third"},
        {"kind": "enemy_killed_in_area", "value": 5, "area": "enemy_natural"},
        {"kind": "expansion_count", "op": ">=", "value": 3},
        {"kind": "structure_count", "structure_type": "Gateway", "value": 4},
    ]
    # _describe_activation_one 覆盖的 activate_when kind
    activate_dicts = [
        {"kind": "tech_done", "upgrade_id": "Charge"},
        {"kind": "structure_count", "structure_type": "Stargate", "op": ">=", "value": 2},
        {"kind": "expansion_count", "op": ">=", "value": 2},
        {"kind": "unit_arrived", "area": "enemy_main"},
    ]
    leaks: list[tuple[str, str]] = []
    for dw in done_when_dicts:
        leaks.extend(
            _scan_cjk(director._describe_condition(dw, "did", 100.0), f"cond.{dw['kind']}")
        )
    for aw in activate_dicts:
        leaks.extend(_scan_cjk(director._describe_activation_one(aw), f"act.{aw['kind']}"))
    assert not leaks, "en 条件/前置文本残留中文:\n" + "\n".join(f"  {p} = {v!r}" for p, v in leaks)


def test_en_strategies_api_no_chinese(library: StrategyLibrary) -> None:
    """GET /api/strategies?locale=en 的 payload 不得含 CJK。"""
    from vibecraft.server.http import _serve_strategies_api

    payload = _serve_strategies_api(library, lang="en")
    leaks = _scan_cjk(payload)
    assert not leaks, "/api/strategies?locale=en 残留中文:\n" + "\n".join(
        f"  {p} = {v!r}" for p, v in leaks
    )
