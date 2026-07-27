"""dt_drop_iac plan 使用 PrismWarpDropAct 的 smoke test。

验证 plan 代码结构(不实例化 KnowledgeBot,避免 sharpy 需要 config.ini):
- dt_drop_iac.py import PrismWarpDropAct
- dt_drop_iac.py 不再 instantiate DTPrismHarass()
- dt_drop_iac.py 创建 PrismWarpDropAct(...) 实例
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PLAN_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "vibecraft"
    / "bot"
    / "auto_combat"
    / "protoss"
    / "plans"
    / "dt_drop_iac.py"
)


@pytest.fixture
def plan_src() -> str:
    return _PLAN_PATH.read_text(encoding="utf-8")


@pytest.fixture
def plan_ast(plan_src: str) -> ast.Module:
    return ast.parse(plan_src)


class TestDtDropIacUsesPrismWarpDropAct:
    def test_imports_prism_warp_drop_act(self, plan_src: str) -> None:
        """dt_drop_iac.py 应 import PrismWarpDropAct。"""
        assert "PrismWarpDropAct" in plan_src, "dt_drop_iac.py 应 import PrismWarpDropAct"

    def test_does_not_import_dt_prism_harass_in_plan_body(self, plan_ast: ast.Module) -> None:
        """dt_drop_iac.py 的 create_plan 函数体内不应再有 DTPrismHarass() 调用。"""
        harass_calls: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                # DTPrismHarass() 是 ast.Call, func=ast.Name(id="DTPrismHarass")
                if isinstance(node.func, ast.Name) and node.func.id == "DTPrismHarass":
                    harass_calls.append(f"line {node.lineno}")
                self.generic_visit(node)

        _Visitor().visit(plan_ast)
        assert harass_calls == [], (
            f"dt_drop_iac.py 不应再含 DTPrismHarass() 调用，但发现: {harass_calls}"
        )

    def test_instantiates_prism_warp_drop_act(self, plan_ast: ast.Module) -> None:
        """create_plan 函数体内应有 PrismWarpDropAct(...) 调用。"""
        warp_drop_calls: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "PrismWarpDropAct":
                    warp_drop_calls.append(f"line {node.lineno}")
                self.generic_visit(node)

        _Visitor().visit(plan_ast)
        assert len(warp_drop_calls) >= 1, "dt_drop_iac.py 应包含至少一个 PrismWarpDropAct(...) 调用"
