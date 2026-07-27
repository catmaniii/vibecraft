"""静态防复发门：build plan「结构冻结」/「农民埋葬」反模式检测。

背景（docs/plans/2026-07-10-worker-saturation-floor-design.md「治本三件套 C」）：
2026-07-10 修完 4 个虫/人族 build（roach_hydra_viper / ultralisk / bc_late / mech）的
「结构冻结」病 —— 军队/科技/农民全塞进单条阻塞 `SequentialList`，前面的经济/科技建筑
步骤没走完，后面（往往是军队生产）就被永久冻住（sharpy `SequentialList.execute()` 每帧
从头遍历，遇到一项 `execute()` 返回 False 立刻停，后面的项那一帧完全不会被调用）。
这个门走查每个 build 的 `create_plan()` plan 树（不拉起 SC2，纯构造 + 递归走查，同
`test_terran_plans_construct.py`/`test_zerg_plans_construct.py` 套路），防止这个反模式
复发。

判据校准（见本文件 `_SEQ_LIST_SIZE_THRESHOLD` / `_ARMY_COUNT_THRESHOLD` 注释）：
实测枚举了当前全部 47 个 build 的每个 SequentialList，健康 build 里"含大数量军队
（to_count>=6）"的 SequentialList 直接子项数最大是 7（roach_allin/roach_ravager）；
4 个已修复病 build 的旧写法（git 历史）直接子项数是 10（bc_late）/15（roach_hydra_viper）
/17（ultralisk）。取阈值 8 能干净切开健康/病态两组，零误报。

已知局限（写在这别掩盖）：mech.py 的历史病根是 `BuildAddon(FACTORYTECHLAB)` 被冻结
（建筑 addon，不是军队训练 act）间接饿死下游 `Step(UnitReady(FACTORYTECHLAB), ...)`
军队产出 —— 这个门专门抓"军队 act 直接被埋进大阻塞 SequentialList"，不覆盖
"建筑 act 被冻结、间接饿死别处军队"这种更深的间接链条。mech.py 当前 SequentialList
都不含军队 act（另一层已抽干净），所以门对它当前状态是绿的；这条局限记录给未来
一个更通用的"SequentialList 过长"门做铺垫，不在本次范围内。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 阈值（校准依据见文件头注释）
# ---------------------------------------------------------------------------

# SequentialList 直接子项数门槛：> 此值才有资格触发"结构冻结"。健康 build 实测最大 7。
_SEQ_LIST_SIZE_THRESHOLD = 8

# 军队 act to_count 门槛：>= 此值才算"大数量军队"（design doc「如 N≥6」）。
_ARMY_COUNT_THRESHOLD = 6

_WORKER_NAMES = {"DRONE", "SCV", "PROBE"}

# 纯 all-in（1-2 矿不再扩）合法阻塞特例的豁免清单：build_id -> 理由。
# 当前全部 build 过审计后为空 —— 若未来新增纯 all-in build 因本门误报，把 id 加进来
# 并写清楚为什么这是"合法阻塞"（如整条链就是打完即完，SequentialList 后面确实没有
# 会被冻结的东西）。
_EXEMPT_BUILD_IDS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# plan 树走查（不拉起 SC2，同 test_terran_plans_construct.py 套路）
# ---------------------------------------------------------------------------


def _walk_acts(node: object, seen: set[int] | None = None):
    """递归遍历 sharpy plan 树（BuildOrder/SequentialList.orders + IfElse.action/action_else）。"""
    if seen is None:
        seen = set()
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("orders", "action", "action_else"):
        child = getattr(node, attr, None)
        if child is None:
            continue
        if isinstance(child, (list, tuple)):
            for c in child:
                yield from _walk_acts(c, seen)
        else:
            yield from _walk_acts(child, seen)


def _army_unit_names() -> set[str]:
    """SC2 army 兵种 UnitTypeId 名集合（排除农民/超能量单位/建筑）。

    与 vibecraft.build_efficiency.roster._army_unit_names 判定同源，但本文件独立实现
    一份 —— 只读 sc2 SDK 数据，不 import 其它 vibecraft 内部模块，保持这个防复发门
    自包含（改 build_efficiency 内部实现不该影响这个门）。
    """
    from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
    from sc2.ids.unit_typeid import UnitTypeId

    workers = {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
    non_army = workers | {
        UnitTypeId.OVERLORD,
        UnitTypeId.OVERLORDTRANSPORT,
        UnitTypeId.OVERSEER,
        UnitTypeId.LARVA,
        UnitTypeId.EGG,
        UnitTypeId.MULE,
        UnitTypeId.BROODLING,
        UnitTypeId.CHANGELING,
    }
    out: set[str] = set()
    for unit, producers in UNIT_TRAINED_FROM.items():
        if unit in non_army:
            continue
        if producers & workers:  # 农民造的 = 建筑，排除
            continue
        out.add(unit.name)
    return out


def _find_army_acts(node: object, army_names: set[str]) -> list[tuple[str, int]]:
    """在 node 子树内找所有"训练非农民军队单位"的 act 节点，返回 (unit_name, to_count)。"""
    from sc2.ids.unit_typeid import UnitTypeId

    found: list[tuple[str, int]] = []
    for n in _walk_acts(node):
        ut = getattr(n, "unit_type", None)
        to_count = getattr(n, "to_count", None)
        if (
            isinstance(ut, UnitTypeId)
            and isinstance(to_count, int)
            and ut.name in army_names
            and ut.name not in _WORKER_NAMES
        ):
            found.append((ut.name, to_count))
    return found


def _find_worker_acts(node: object) -> list[tuple[str, int]]:
    """在 node 子树内找所有农民训练 act，返回 (unit_name, to_count)。"""
    from sc2.ids.unit_typeid import UnitTypeId

    found: list[tuple[str, int]] = []
    for n in _walk_acts(node):
        ut = getattr(n, "unit_type", None)
        to_count = getattr(n, "to_count", None)
        if isinstance(ut, UnitTypeId) and isinstance(to_count, int) and ut.name in _WORKER_NAMES:
            found.append((ut.name, to_count))
    return found


def structural_freeze_violations(plan: object, army_names: set[str]) -> list[str]:
    """核心检测器：走查 plan 树里所有 SequentialList，抓"结构冻结"反模式。

    规则：一个 SequentialList 直接子项数 > `_SEQ_LIST_SIZE_THRESHOLD`，且它的子树里
    存在 to_count >= `_ARMY_COUNT_THRESHOLD` 的军队训练 act —— 判定为结构冻结：
    该军队 act（或它前面排队的经济/科技步骤）会被 SequentialList 的阻塞语义卡住
    （`SequentialList.execute()` 每帧从头遍历，任一项当帧 return False 就停，
    后面的项那一帧完全不会被调用；vendor/sharpy/sharpy/plans/sequential_list.py）。

    返回违规描述字符串列表（空列表 = 干净）。这个函数是本门的核心逻辑，也被
    `test_detector_catches_synthetic_structural_freeze` 直接调用做门有效性自证。
    """
    from sharpy.plans.sequential_list import SequentialList

    violations: list[str] = []
    for node in _walk_acts(plan):
        if not isinstance(node, SequentialList):
            continue
        n_children = len(node.orders)
        if n_children <= _SEQ_LIST_SIZE_THRESHOLD:
            continue
        army_acts = _find_army_acts(node, army_names)
        big_army = [(name, cnt) for name, cnt in army_acts if cnt >= _ARMY_COUNT_THRESHOLD]
        if big_army:
            violations.append(
                f"SequentialList(子项数={n_children}) 含大数量军队 {big_army} —— "
                f"阻塞语义下前面的经济/科技步骤没走完，军队生产会被冻结数分钟。"
                f"改并行 BuildOrder + Step(UnitReady(...)) 门控（参照 roach_hydra.py/"
                f"widow_mine_drop.py 骨架）。"
            )
    return violations


def worker_burial_warnings(plan: object, army_names: set[str]) -> list[str]:
    """软提示（不 fail）：同容器（BuildOrder 或 SequentialList）里，农民训练 act
    排在大数量军队 act 之后 —— 埋葬反模式的静态影子。

    评审已定性：这条的执行期风险已被 `WorkerSaturationFloorAct` 从执行上中和
    （Floor 恒生效，不管 plan 内部怎么排，独立把农民拉回饱和），静态检查价值降低，
    只做提示不做硬门（design doc「治本三件套 C」）。
    """
    warnings_: list[str] = []
    for node in _walk_acts(plan):
        orders = getattr(node, "orders", None)
        if not isinstance(orders, list) or len(orders) < 2:
            continue
        seen_big_army_at: int | None = None
        for idx, child in enumerate(orders):
            big_army = [
                (name, cnt)
                for name, cnt in _find_army_acts(child, army_names)
                if cnt >= _ARMY_COUNT_THRESHOLD
            ]
            if big_army and seen_big_army_at is None:
                seen_big_army_at = idx
                continue
            if seen_big_army_at is not None:
                worker_acts = _find_worker_acts(child)
                if worker_acts:
                    warnings_.append(
                        f"{type(node).__name__} 第 {idx} 项农民训练 {worker_acts} "
                        f"排在第 {seen_big_army_at} 项大数量军队之后（同容器）——"
                        f"埋葬风险（已被 WorkerSaturationFloorAct 兜底中和，仅提示）。"
                    )
    return warnings_


# ---------------------------------------------------------------------------
# 枚举全部 build（opening + persistent doctrine）
# ---------------------------------------------------------------------------


def _iter_build_plans() -> list[tuple[str, str, str]]:
    """遍历 strategies/ 下所有 opening + persistent doctrine 的 (build_id, module_path, cls_name)。"""
    from vibecraft.strategy import StrategyLibrary

    library = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    # all_strategies() 覆盖全部 4 种 kind（当前 yaml 只用了 opening_build /
    # persistent_doctrine，但用全量遍历，未来加 midgame/lategame yaml 也自动跟上，
    # 不用回来改这个门）。Strategy = Annotated[Union[OpeningBuild, MidgameStance,
    # LategameDoctrine, PersistentDoctrine], ...]，四个成员都有 id/sharpy_dummy_class
    # 字段，mypy 直接认得，不需要 isinstance 窄化。
    for strat in library.all_strategies():
        sdc = strat.sharpy_dummy_class
        if not sdc or strat.id in seen:
            continue
        seen.add(strat.id)
        module_path, _, cls_name = sdc.partition(":")
        out.append((strat.id, module_path, cls_name))
    return sorted(out)


_BUILD_PLANS = _iter_build_plans()

_VENDOR_SHARPY = PROJECT_ROOT / "vendor" / "sharpy"


def _construct_plan(module_path: str, cls_name: str) -> object:
    """构造 create_plan()，对 sharpy `config.get_config()` 的 CWD 依赖免疫。

    踩坑记录（本文件写这个 helper 时现场发现，不是道听途说）：`sharpy.knowledges.
    skeleton_bot.py` 顶部 `from config import get_config` 是一次性 name binding——
    谁在本进程里第一个 `import sharpy.knowledges` 就把这个名字锁死。
    `vibecraft.bot.auto_combat.common_bot._ensure_sharpy_on_path()` 会 monkeypatch
    `config.get_config`（改成用 vendor/sharpy 绝对路径找 config.ini，不依赖 CWD），
    但如果全量跑 `pytest tests/unit` 时 `test_plan_create_plan_smoke.py` 等文件的
    module-scoped fixture 抢在本文件前先 `import sharpy.knowledges`（它们走的是
    "chdir 到 vendor/sharpy 再 import" 的路子，不经过 vibecraft 的 monkeypatch），
    `skeleton_bot.get_config` 就永久锁死成**原始、按 CWD 相对路径找 config.ini**的
    版本——teardown 把 CWD 换回仓库根目录后，仓库根目录没有 config.ini，本文件
    构造任何 plan 都会 `ValueError: Config file(s) not found!`（单独跑本文件不
    触发，因为那时是本文件自己第一个 import，monkeypatch 生效）。
    实测复现：`pytest tests/unit/test_plan_create_plan_smoke.py tests/unit/
    test_build_structure_audit.py` 48/49 FAIL；同样的顺序也能让已有的
    `test_terran_plans_construct.py`/`test_zerg_plans_construct.py` 炸，
    证明这是测试套件里预先存在的、跟 import 顺序有关的脆弱点，不是这个新文件带来的。

    修法用最小改动、不碰其它文件：无论 `get_config` 锁死成哪个版本，两个版本都是
    "在给定目录找 config.ini"——`vendor/sharpy/` 下真的放着 config.ini，构造期间
    临时把 CWD 切过去就对两个版本都免疫，不用赌 import 顺序或去改
    conftest.py/test_plan_create_plan_smoke.py 那些不在本任务范围内的文件。
    """
    import os

    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    mod = __import__(module_path, fromlist=[cls_name])
    old_cwd = Path.cwd()
    os.chdir(_VENDOR_SHARPY)
    try:
        return asyncio.run(getattr(mod, cls_name)().create_plan())
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 门 1（硬）：结构冻结 —— 全部 build 都要过
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("build_id", "module_path", "cls_name"), _BUILD_PLANS)
def test_no_structural_freeze(build_id: str, module_path: str, cls_name: str) -> None:
    """每个 build 的 create_plan() 都不含"大数量军队塞进阻塞 SequentialList"反模式。

    2026-07-10 roach_hydra_viper/ultralisk/bc_late/mech 4 个 build 都踩过这个坑
    （单条 SequentialList 把经济/科技/军队全串起来，军队生产被前面步骤冻结数分钟）。
    刚修完，这个门防止以后新增/改 build 时再犯。
    """
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    army_names = _army_unit_names()
    plan = _construct_plan(module_path, cls_name)

    if build_id in _EXEMPT_BUILD_IDS:
        pytest.skip(f"豁免（{_EXEMPT_BUILD_IDS[build_id]}）")

    violations = structural_freeze_violations(plan, army_names)
    assert not violations, f"{cls_name}({build_id}) 检测到结构冻结反模式：\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_worker_burial_heuristic_report() -> None:
    """软提示（不 fail）：扫全部 build 打印"农民排在大数量军队之后"的静态影子。

    仅供人工复查，不作为回归门（执行期风险已被 WorkerSaturationFloorAct 中和，
    见 worker_burial_warnings 文档字符串）。这个测试恒 PASS，只是把提示打印出来。
    """
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    army_names = _army_unit_names()
    total_warnings = 0
    for build_id, module_path, cls_name in _BUILD_PLANS:
        plan = _construct_plan(module_path, cls_name)
        for w in worker_burial_warnings(plan, army_names):
            total_warnings += 1
            print(f"[worker-burial-heuristic] {build_id}: {w}")
    print(f"[worker-burial-heuristic] 共 {total_warnings} 条提示（不影响 PASS/FAIL）")


# ---------------------------------------------------------------------------
# 门有效性自证：喂一个仿造的"结构冻结" plan，确认门真的 FAIL
# ---------------------------------------------------------------------------


def test_detector_catches_synthetic_structural_freeze() -> None:
    """有效性自证：构造一个仿 roach_hydra_viper 旧写法（阻塞 SequentialList 塞满
    经济步骤 + 尾部大数量军队）的 fake plan，确认 `structural_freeze_violations`
    真的报警；再构造等价的并行 BuildOrder 写法（= 治本后的正确写法），确认干净。

    不是摆设：这条证明门不是"永远绿的假门"（同 i18n 假阳性门教训，CLAUDE.md
    「防复发」纪律要求）。用真 sharpy act 类构造，不拉起 SC2、不碰任何真实 build 源码。
    """
    from sc2.ids.unit_typeid import UnitTypeId
    from sharpy.plans import BuildOrder, SequentialList
    from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding
    from sharpy.plans.acts.zerg import ZergUnit

    army_names = _army_unit_names()

    def _make_body(container: type) -> object:
        # 10 个"经济/科技"填充步骤（仿 roach_hydra_viper 旧版 22 农→双气→BS→…→VI→Hive）
        # + 尾部大数量军队训练 —— 共 11 项，> _SEQ_LIST_SIZE_THRESHOLD(8)。
        fillers = [
            ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
            BuildGas(2),
            GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
            Expand(3),
            ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 30),
            GridBuilding(UnitTypeId.ROACHWARREN, 1),
            GridBuilding(UnitTypeId.EVOLUTIONCHAMBER, 2),
            Expand(4),
            BuildGas(4),
            ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 40),
        ]
        army = ZergUnit(UnitTypeId.ROACH, 16)
        return container([*fillers, army])

    # 旧写法：外层 SequentialList（阻塞）—— 应该被门抓到。
    broken_plan = BuildOrder(_make_body(SequentialList))
    broken_violations = structural_freeze_violations(broken_plan, army_names)
    assert broken_violations, (
        "门没抓到仿造的结构冻结 plan —— 检测器失效（不是摆设检查，见测试文档字符串）"
    )

    # 治本后写法：外层换成 BuildOrder（并行）—— 同样的子项，不该再报警。
    fixed_plan = BuildOrder(_make_body(BuildOrder))
    fixed_violations = structural_freeze_violations(fixed_plan, army_names)
    assert not fixed_violations, (
        f"并行 BuildOrder 写法不该触发结构冻结门，实际报警：{fixed_violations}"
    )
