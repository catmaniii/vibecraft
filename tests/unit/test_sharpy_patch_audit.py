"""sharpy vendor patch 审计:确保所有 vibecraft hook marker 未被误删 / sharpy 升级覆盖。

2026-05-26 起因:vendor/sharpy 里 PlanZoneAttack + PlanFinishEnemy 被打了 5+1 处
# vibecraft: hook。sharpy upstream 合并后可能覆盖这些 patch,或者人为误编辑删了 marker。

两个 audit test:
1. test_patched_methods_have_vibecraft_marker  —— 逐 method 检查 marker 存在性
2. test_no_unpatched_dispatch_calls_in_used_plans —— dispatch site 必须在 20 行内有 marker
   (或在 EXEMPT_SITES 白名单中给出原因)
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Test 1:已 patch 的 method 必须包含 # vibecraft: marker
# ---------------------------------------------------------------------------

# (path_relative_to_project, class_name, method_name)
PATCHED_METHODS = [
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "__init__"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_get_target"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_should_attack"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_should_retreat"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_stop_retreat"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "execute"),
    ("vendor/sharpy/sharpy/plans/tactics/attack_expansions.py", "PlanFinishEnemy", "execute"),
    # vibecraft: mineral-line exclusion patch (Issue #3)
    ("vendor/sharpy/sharpy/managers/core/grids/build_grid.py", "BuildGrid", "fill_line"),
    # vibecraft: 2026-05-27 retreat 时新单位仍朝前 rally —— intent=retreat 时
    # effective gather_point 改 start_location,新追猎从 Gateway 出来 rally home
    ("vendor/sharpy/sharpy/plans/tactics/zone_gather.py", "PlanZoneGather", "execute"),
    # vibecraft: 2026-05-28 闪追风筝 — shield 阈值 0.05 → 0.3 提前后撤
    ("vendor/sharpy/sharpy/combat/protoss/micro_stalkers.py", "MicroStalkers", "unit_solve_combat"),
    # vibecraft: 2026-05-28 probe 聚团门 helper(_should_attack 调)
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_vbc_is_regrouped"),
    # vibecraft: 2026-06-13 全军防守威胁感知 — defend intent 迎击任意己方 zone 的敌军
    ("vendor/sharpy/sharpy/plans/tactics/zone_attack.py", "PlanZoneAttack", "_vbc_defend_target"),
    ("vendor/sharpy/sharpy/plans/tactics/zone_gather.py", "PlanZoneGather", "_vbc_threatened_zone"),
    # vibecraft: 2026-06-17 defend 大军原地拉扯修复 — defend intent 下 PlanZoneDefense 不 claim 主力
    # (交给 PlanZoneGather 单一锚点),消除 enemy_center↔锚点双目标 churn
    ("vendor/sharpy/sharpy/plans/tactics/zone_defense.py", "PlanZoneDefense", "execute"),
    # vibecraft: 2026-06-18 科技单位主动技能补全 — 注册 GHOST/BANSHEE/ROACH micro + 改既有 caster micro
    ("vendor/sharpy/sharpy/combat/micro_rules.py", "MicroRules", "load_default_micro"),
    ("vendor/sharpy/sharpy/combat/zerg/micro_roaches.py", "MicroRoaches", "__init__"),
    (
        "vendor/sharpy/sharpy/combat/protoss/micro_sentries.py",
        "MicroSentries",
        "group_solve_combat",
    ),
    ("vendor/sharpy/sharpy/combat/zerg/micro_vipers.py", "MicroVipers", "unit_solve_combat"),
    ("vendor/sharpy/sharpy/combat/terran/micro_ravens.py", "MicroRavens", "unit_solve_combat"),
    # vibecraft: 2026-05-29 Gateway/Warpgate/Hatchery/Lair/Hive 同质化计数 — 防 plan 重复触发
    ("vendor/sharpy/sharpy/plans/acts/act_base.py", "ActBase", "get_count"),
    # vibecraft: 2026-05-29 iac_2base 电兵安全 micro — 保持安全距离放 Storm，不 attack
    (
        "vendor/sharpy/sharpy/combat/protoss/micro_hightemplars.py",
        "MicroHighTemplars",
        "unit_solve_combat",
    ),
    # 2026-06-02 用户:micro_zealots 的 zealot_hold patch 已移除(叉子立刻顶上去),回原版 sharpy
    # 2026-06-02 用户:产能封锁机制级拦截 —— 下训练/折跃指令前检查 production_blocked
    ("vendor/sharpy/sharpy/plans/acts/act_unit.py", "ActUnit", "execute"),
    # vibecraft: 2026-06-11 有偷矿基地时主矿满采就停（is_done）+ 主力产线不在 stealth Nexus 造（builders）
    ("vendor/sharpy/sharpy/plans/acts/act_unit.py", "ActUnit", "is_done"),
    ("vendor/sharpy/sharpy/plans/acts/act_unit.py", "ActUnit", "builders"),
    # vibecraft: 2026-06-11 偷矿成长期 Nexus 能量预留给自我加速，bot 各 chrono plan 不拿它当能量源
    ("vendor/sharpy/sharpy/plans/acts/protoss/chrono_unit.py", "ChronoUnit", "execute"),
    ("vendor/sharpy/sharpy/plans/acts/protoss/chrono_tech.py", "ChronoTech", "execute"),
    ("vendor/sharpy/sharpy/plans/acts/protoss/chrono_building.py", "ChronoBuilding", "execute"),
    ("vendor/sharpy/sharpy/plans/acts/protoss/chrono_any_tech.py", "ChronoAnyTech", "execute"),
    ("vendor/sharpy/sharpy/plans/acts/protoss/warp_unit.py", "WarpUnit", "execute"),
    # vibecraft: 2026-06-10 玩家开矿封顶 + stealth 基地不计入自然扩张账
    ("vendor/sharpy/sharpy/plans/acts/expand.py", "Expand", "execute"),
    # vibecraft: 2026-06-10 WP3 偷矿 FENCE — stealth 基地从全局工作队列排除（防主矿农民倒灌）
    (
        "vendor/sharpy/sharpy/plans/tactics/distribute_workers.py",
        "DistributeWorkers",
        "generate_worker_queue",
    ),
    # vibecraft: 2026-06-11 经济可观测 — worker 跨基地调度结构化日志（"主矿往分矿派农民"可观测）
    (
        "vendor/sharpy/sharpy/plans/tactics/distribute_workers.py",
        "DistributeWorkers",
        "assign_to_work",
    ),
    # vibecraft: 2026-07-06 采矿策略 hook — 每帧根据 mining_priority 覆写 min/max_gas
    (
        "vendor/sharpy/sharpy/plans/tactics/distribute_workers.py",
        "DistributeWorkers",
        "execute",
    ),
    # vibecraft: 2026-07-07 攻防升级目标等级封顶门 — 超出 upgrade_targets 的等级跳过不研究
    ("vendor/sharpy/sharpy/plans/acts/tech.py", "Tech", "execute"),
]


def _find_method_line_range(
    tree: ast.Module, class_name: str, method_name: str
) -> tuple[int, int] | None:
    """在 ast tree 里找 class.method 的 (start_lineno, end_lineno),找不到返回 None。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return (item.lineno, item.end_lineno)
    return None


@pytest.mark.parametrize("path,cls,method", PATCHED_METHODS)
def test_patched_methods_have_vibecraft_marker(path: str, cls: str, method: str) -> None:
    """已 patch 的 method 函数体内必须包含 '# vibecraft:' 注释。

    失败说明该 marker 被误删,或 sharpy upstream 升级后覆盖了 patch。
    需要重新检查 vendor 文件中的对应方法并补回 hook。
    """
    abs_path = PROJECT_ROOT / path
    assert abs_path.exists(), f"vendor 文件不存在: {abs_path}"

    src = abs_path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    line_range = _find_method_line_range(tree, cls, method)
    assert line_range is not None, (
        f"{path} 中找不到 {cls}.{method} —— 方法被重命名/删除,或 sharpy 升级后 class 结构变化?"
    )

    start, end = line_range
    # ast 行号从 1 开始,lines 下标从 0 开始
    method_lines = lines[start - 1 : end]

    has_marker = any("# vibecraft:" in line for line in method_lines)
    assert has_marker, (
        f"{path}::{cls}.{method} 缺 `# vibecraft:` marker —— "
        f"检查是否被误删 / sharpy 升级后丢失。"
        f"(函数体: 第 {start}–{end} 行)"
    )


# ---------------------------------------------------------------------------
# Test 2:dispatch call site 必须在 20 行内有 # vibecraft: marker,或在白名单中
# ---------------------------------------------------------------------------

# vibecraft 实际 import 用的 sharpy combat plan 文件
USED_VENDOR_FILES = [
    "vendor/sharpy/sharpy/plans/tactics/zone_attack.py",
    "vendor/sharpy/sharpy/plans/tactics/attack_expansions.py",
    # 未来加新 wrap 时在此处追加
]

# 派单位 sentinel 正则(任一命中即为 dispatch site)
DISPATCH_PATTERNS: list[str] = [
    r"\bai\.units\.idle\b",
    r"\bself\.roles\.free_units\b",
    r"\bunit\.attack\(",
    r"\bunit\.move\(",
]

# 明确豁免的 dispatch site,说明不需要额外 hook 的原因
# 格式: path -> [(line_number, reason), ...]
EXEMPT_SITES: dict[str, list[tuple[int, str]]] = {
    "vendor/sharpy/sharpy/plans/tactics/zone_attack.py": [
        (
            157,
            "execute() else 分支收集 attackers list,随后走 _should_attack() 判定 —— "
            "intent/stance 覆盖已在 _should_attack marker 处理,此处无需重复 hook;"
            "2026-06-06 加 attacker_count==0 守卫断 flip-flop(同分支 # vibecraft marker);"
            "行号随 2026-06-17 defend fallback 改 forward + _vbc_forward_defense_point 新增更新为 157",
        ),
        (
            213,
            "handle_attack() 内收集 free_units 加入 combat group —— "
            "handle_attack 只在 status==Attacking 时调用;"
            "进入 Attacking 的判定由 _should_attack(marker) 控制;"
            "retreat 判定由 _should_retreat(marker) 控制;"
            "此处 free_units 迭代是 group 维护逻辑,不是独立 dispatch 决策点;"
            "行号随 2026-06-17 defend fallback 改 forward 更新为 213",
        ),
    ],
}


def _build_exempt_set(path_rel: str) -> set[int]:
    """把 EXEMPT_SITES[path] 转为豁免行号集合。"""
    entries = EXEMPT_SITES.get(path_rel, [])
    return {lineno for lineno, _ in entries}


def _get_exempt_reason(path_rel: str, lineno: int) -> str:
    """返回某行的豁免原因,找不到返回空字符串。"""
    for ln, reason in EXEMPT_SITES.get(path_rel, []):
        if ln == lineno:
            return reason
    return ""


def test_no_unpatched_dispatch_calls_in_used_plans() -> None:
    """vendor 用到的 combat plan 文件里所有 dispatch site 必须在上下 20 行内有
    `# vibecraft:` marker,或明确列在 EXEMPT_SITES 白名单中。

    失败说明新增了派单位逻辑但没打 hook —— 需要:
    A) 加 `# vibecraft:` marker 并实现 intent 覆盖逻辑,或
    B) 将该行加入 EXEMPT_SITES 并写明原因(说明已有更上游的 hook 覆盖)。
    """
    compiled_patterns = [re.compile(p) for p in DISPATCH_PATTERNS]
    violations: list[str] = []

    for path_rel in USED_VENDOR_FILES:
        abs_path = PROJECT_ROOT / path_rel
        assert abs_path.exists(), f"vendor 文件不存在: {abs_path}"

        src = abs_path.read_text(encoding="utf-8")
        lines = src.splitlines()
        exempt_lines = _build_exempt_set(path_rel)

        for lineno_1based, line in enumerate(lines, start=1):
            # 跳过注释行(整行以 # 开头)和字符串字面量粗过滤
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue

            # 检查是否命中任一 dispatch pattern
            matched_pattern: str | None = None
            for pat in compiled_patterns:
                if pat.search(line):
                    matched_pattern = pat.pattern
                    break

            if matched_pattern is None:
                continue  # 本行不是 dispatch site

            # 在豁免白名单里 → skip
            if lineno_1based in exempt_lines:
                continue

            # 检查上下 20 行内是否有 # vibecraft: marker
            context_start = max(0, lineno_1based - 1 - 20)  # 转 0-indexed
            context_end = min(len(lines), lineno_1based - 1 + 20 + 1)
            context_lines = lines[context_start:context_end]
            has_nearby_marker = any("# vibecraft:" in ctx_line for ctx_line in context_lines)

            if not has_nearby_marker:
                violations.append(
                    f"{path_rel}:{lineno_1based}: {line.strip()!r}\n"
                    f"  命中 dispatch pattern: {matched_pattern!r}\n"
                    f"  上下 20 行内无 `# vibecraft:` marker —— "
                    f"加 marker 并实现 intent 覆盖,或将此行加入 EXEMPT_SITES 并说明原因"
                )

    assert not violations, "以下 dispatch site 未受 vibecraft hook 保护:\n\n" + "\n\n".join(
        violations
    )
