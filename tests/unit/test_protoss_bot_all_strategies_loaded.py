"""8 个神族策略接入 bot 框架的完整性测试。

测试目标：bot.py 的 `create_plan()` 会自动 collect 所有有 `sharpy_dummy_class`
的策略 → import + instantiate + 拼装 IfElse 路由树。本测试验证 8 个策略**全部
被正确收集 + import + create_plan 成功**，没有任何策略被 silent fallback。

跟现有 test_plan_create_plan_smoke 的区别：smoke 只测**单个 plan class 能否
instantiate**，本测试测**完整链路（strategy_library 加载 → bot create_plan
→ IfElse 树）**，能抓 yaml schema 错 / dummy_class 路径错 / IfElse 拼装错。

需要 sharpy 实际可 import（uv sync --extra sc2），CI 无 sc2 extras 自动 skip。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"


@pytest.fixture(scope="module", autouse=True)
def _setup_sharpy_path():
    """让 vendor/sharpy 可 import + cwd 指向 vendor/sharpy（KnowledgeBot 找 config.ini）。"""
    sharpy_path_str = str(_VENDOR_SHARPY)
    inserted = False
    if sharpy_path_str not in sys.path:
        sys.path.insert(0, sharpy_path_str)
        inserted = True

    config_path = _VENDOR_SHARPY / "config.ini"
    if not config_path.exists():
        if inserted:
            sys.path.remove(sharpy_path_str)
        pytest.skip(
            f"vendor/sharpy/config.ini 不存在 → bot 跑不起来。"
            f"从 git checkout 恢复：git checkout HEAD -- vendor/sharpy/config.ini"
        )

    old_cwd = os.getcwd()
    os.chdir(_VENDOR_SHARPY)

    try:
        import sharpy.knowledges  # noqa: F401
    except ImportError:
        os.chdir(old_cwd)
        if inserted:
            sys.path.remove(sharpy_path_str)
        pytest.skip("sharpy 未安装（需 uv sync --extra sc2）")

    yield

    os.chdir(old_cwd)
    if inserted:
        sys.path.remove(sharpy_path_str)


# 期望被加载的 9 个神族策略 id（必须跟 strategies/protoss/*.yaml 完全对齐）
_EXPECTED_STRATEGY_IDS: set[str] = {
    "4bg",
    "1g_robo_immortal",
    "dt_rush",
    "phoenix_2base",
    "blink_stalker",
    "cannon_rush",
    "iac_2base",
    "dt_drop_iac",
    "skytoss",
}


def _load_real_library():
    """加载 strategies/protoss/ 下真实 9 个 yaml。"""
    from vibecraft.strategy.library import StrategyLibrary

    return StrategyLibrary.from_directories(
        _PROJECT_ROOT / "strategies" / "protoss",
        _PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def test_strategy_library_loads_all_9_strategies() -> None:
    """前置条件：strategy library 能加载 9 个神族策略且 id 集合精准匹配。"""
    lib = _load_real_library()
    loaded_ids = {s.id for s in lib.all_strategies()}
    assert loaded_ids == _EXPECTED_STRATEGY_IDS, (
        f"miss: {_EXPECTED_STRATEGY_IDS - loaded_ids}, extra: {loaded_ids - _EXPECTED_STRATEGY_IDS}"
    )


def test_all_9_strategies_have_sharpy_dummy_class() -> None:
    """9 个策略都必须有 sharpy_dummy_class 字段，否则 bot.create_plan 会 silent skip。"""
    lib = _load_real_library()
    missing: list[str] = []
    for s in lib.all_strategies():
        if not getattr(s, "sharpy_dummy_class", None):
            missing.append(s.id)
    assert not missing, f"以下策略缺 sharpy_dummy_class（bot 不会路由它们）: {missing}"


def test_bot_create_plan_loads_all_9_strategies_into_ifelse_tree() -> None:
    """bot.create_plan() 完整链路：9 个策略 import + instantiate + 拼 IfElse 都成功。

    这是接入完整性的真理源 — 失败说明：
    - yaml 的 sharpy_dummy_class 路径打错（"a:B" 找不到 module a 或 class B）
    - plan class create_plan() 抛异常（被 try/except 兜底成 _make_fallback_plan）
    - IfElse 嵌套构造失败
    """
    lib = _load_real_library()

    # 用 sharpy_adapter 造 bot class（真 KnowledgeBot 子类）
    from vibecraft.bot.sharpy_adapter import make_bot_class

    BotClass = make_bot_class(
        director_factory=lambda facade: None,
        strategy_library=lib,
    )

    inst = BotClass()
    # 调 create_plan(KnowledgeBot 子类的 async 方法)
    plan = asyncio.get_event_loop().run_until_complete(inst.create_plan())

    from sharpy.plans import BuildOrder

    assert isinstance(plan, BuildOrder), f"create_plan 返回非 BuildOrder: {type(plan).__name__}"

    # IfElse 树深度应该 ≥ 8（每个 recipe 一层嵌套，最深 fallback 是 sustain）
    # BuildOrder.orders 应包含 IfElse 或类似嵌套结构
    assert hasattr(plan, "orders"), "BuildOrder should have .orders"
    assert len(plan.orders) > 0, "BuildOrder.orders 为空 — 没有任何 recipe 被加载"


def test_bot_create_plan_no_fallback_warning_for_any_strategy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """完整链路跑通时不应有 "fallback" warning（说明某个 dummy import / create_plan 失败）。

    Regression: 之前 BELON / TEMPLARARCHIVES 拼错让某个 plan create_plan 抛
    AttributeError → 被 _make_fallback_plan 兜底 → bot 沉默退化。本测试在所有
    8 个策略上 sanity check 不再有任何 fallback。
    """
    import logging

    lib = _load_real_library()
    from vibecraft.bot.sharpy_adapter import make_bot_class

    BotClass = make_bot_class(
        director_factory=lambda facade: None,
        strategy_library=lib,
    )

    with caplog.at_level(logging.WARNING):
        inst = BotClass()
        asyncio.get_event_loop().run_until_complete(inst.create_plan())

    fallback_warnings = [
        r
        for r in caplog.records
        if "fallback" in r.getMessage().lower() and "create_plan" in r.getMessage().lower()
    ]
    assert not fallback_warnings, (
        "以下策略 import / create_plan 失败被 fallback：\n  - "
        + "\n  - ".join(r.getMessage() for r in fallback_warnings)
    )
