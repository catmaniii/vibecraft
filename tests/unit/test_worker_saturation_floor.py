"""WorkerSaturationFloorAct 单测（2026-07-10 通用农民饱和兜底）。


覆盖：
1. 动态 to_count 计算：2/3/4 矿 ideal_sum + 虫族 66 封顶 + 神/人 80 封顶
2. grace 门：base<2 且 早期时间 且 无 sustain flag → 不施压（冻结当前数量）
3. race dispatch：make_worker_floor(race) → 正确 (worker_type, from_building, budget)
4. 无效 race 报错
5. 真实 ActUnit 子类化验证（vendor/sharpy 路径插入，走真类不 mock）
6. 构造回归：三族 bot 挂了 Floor 仍能 create_plan() 无异常，且 Floor 是顶层
   BuildOrder 直接兄弟（不在 SequentialList / IfElse 内部）——评审 #2 挂载点验证。

**惰性 import 纪律（全文件统一）**：所有 `vibecraft.bot.auto_combat.*` / `sharpy.*`
符号一律在每个 test 内部临时 import，不放模块顶层。原因：全量 suite 里其它用
`fake_sharpy_bot_env` fixture 的测试在 teardown 会把 sys.modules 里所有匹配前缀的
条目删掉（`conftest._clean_bot_mods`），下次被 lazy re-import 时会创建**身份不同**
的新模块/类对象。模块顶层 import 一次性拿到的类对象在那之后就成了"旧身份"，之后
同一个 session 里任何触发 re-import 的路径（如 `make_protoss_bot_class` 内部
`_wrap` 的 `from ...worker_saturation_floor import make_worker_floor`）会拿到
**不同身份**的新类对象——isinstance 跨源比较失配（同 conftest.py 里 sc2 enum 重建
那个坑的翻版，2026-07-10 全量 suite 跑起来才暴露；单跑本文件不复现，因为没有其它
用 fake_sharpy_bot_env 的文件抢先执行 teardown）。惰性 import 保证同一个 test 内
"构造方"（bot 工厂 → `_wrap` → `make_worker_floor`）和"isinstance 检查方"用的是
当下 sys.modules 里的同一份。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# vendor/sharpy 加入 sys.path，测试才能 import 真实 sharpy.plans.acts.ActUnit
# （sharpy 未装进 venv，靠各测试文件手动加 vendor 路径；同 test_upgrade_target.py /
# test_micro_ht_safe.py 的做法）。这一步只碰 sys.path，不绑死任何类对象，放模块
# 顶层安全。
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytestmark = pytest.mark.skipif(
    not (_VENDOR_SHARPY / "sharpy" / "plans" / "acts" / "act_unit.py").exists(),
    reason="vendor/sharpy not available",
)


def _floor() -> Any:
    """惰性 import：返回一个当下 sys.modules 状态下自洽的符号包。

    必须在任何 `from sharpy...` import 之前调用 `_ensure_sharpy_on_path()`：
    `sharpy.plans.acts.__init__` 会连带 import `sharpy.knowledges`（→
    skeleton_bot.py `from config import get_config`），`get_config()` 找
    config.ini 用相对 cwd 路径，pytest 跑起来 cwd 未必是 vendor/sharpy。
    `_ensure_sharpy_on_path()` 把 `config.get_config` patch 成绝对路径版本——
    必须先 patch 后 import，否则 skeleton_bot 那次 top-level import 已经绑死
    原始（未 patch）版本，真实构造 bot 时 `get_config()` 抛
    `ValueError: Config file(s) not found`（2026-07-10 踩坑，构造测试专属）。
    """
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from vibecraft.bot.auto_combat import opening_sustain_act as _osa
    from vibecraft.bot.auto_combat import worker_saturation_floor as _wsf

    return SimpleNamespace(
        WorkerSaturationFloorAct=_wsf.WorkerSaturationFloorAct,
        make_worker_floor=_wsf.make_worker_floor,
        GRACE_S=_wsf._GRACE_S,
        ZERG_WORKER_CAP=_osa.ZERG_WORKER_CAP,
        NON_ZERG_WORKER_CAP=_osa.NON_ZERG_WORKER_CAP,
    )


# ---------------------------------------------------------------------------
# 测试辅助：最小 ai stub（Units-like，支持 .ready / .amount / 迭代）
# ---------------------------------------------------------------------------


class _UnitsStub(list):
    """最小 Units stub：.ready 返回自身（测试直接构造"已 ready"的列表），.amount = len。"""

    @property
    def ready(self) -> _UnitsStub:
        return self

    @property
    def amount(self) -> int:
        return len(self)


def _make_ai(
    townhall_ideals: list[int],
    gas_ideals: list[int] | None = None,
    time: float = 200.0,
) -> Any:
    return SimpleNamespace(
        townhalls=_UnitsStub(SimpleNamespace(ideal_harvesters=n) for n in townhall_ideals),
        gas_buildings=_UnitsStub(SimpleNamespace(ideal_harvesters=n) for n in (gas_ideals or [])),
        time=time,
    )


def _no_vibecraft_knowledge() -> Any:
    """knowledge 无 vibecraft 属性（getattr 返回 None 走默认路径）。"""
    return SimpleNamespace()


def _cap_knowledge(cap: int | None, sustain: bool = False) -> Any:
    """knowledge.vibecraft.worker_cap_override = cap（快攻封顶测试用）。"""
    return SimpleNamespace(
        vibecraft=SimpleNamespace(worker_cap_override=cap, sustain_uncap_active=sustain)
    )


class TestWorkerCapOverride:
    """快攻 build 声明的农民封顶（2026-07-10）。"""

    def test_cap_respected_during_rush(self) -> None:
        """cap=20 + 2 矿(饱和32) + 早期(<360s,<4矿,非sustain) → to_count 封在 20。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16], time=200.0)  # grace 过(time>165)
        floor.knowledge = _cap_knowledge(20)
        assert floor.to_count == 20

    def test_cap_still_capped_after_360s(self) -> None:
        """时间过 360s 但仍 2 矿、非 sustain → **仍封顶**（2026-07-11 Fable5 去掉 6min 墙钟解封：
        all-in 常常 6min 还没打出去，墙钟一到就误铺农民抢一波该爆兵的矿）。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16], time=400.0)
        floor.knowledge = _cap_knowledge(20)
        assert floor.to_count == 20

    def test_cap_lifted_on_4_bases(self) -> None:
        """4 矿 = 真转运营 → 解封顶回饱和（不看墙钟）。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16, 16, 16], time=400.0)
        floor.knowledge = _cap_knowledge(20)
        assert floor.to_count == 64

    def test_cap_lifted_on_sustain_flag(self) -> None:
        """sustain_uncap_active(opening 完成) → 解封顶。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16], time=200.0)
        floor.knowledge = _cap_knowledge(20, sustain=True)
        assert floor.to_count == 32

    def test_no_cap_saturates(self) -> None:
        """未声明 cap → 走饱和(不封顶)。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16], time=200.0)
        floor.knowledge = _cap_knowledge(None)
        assert floor.to_count == 32


# ---------------------------------------------------------------------------
# 1. 动态 to_count 计算
# ---------------------------------------------------------------------------


class TestDynamicToCount:
    def test_two_base_protoss_ideal_sum(self) -> None:
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16, 16])  # 2 矿满饱和 = 32
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 32

    def test_three_base_protoss_with_gas(self) -> None:
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16, 16, 16], gas_ideals=[3, 3, 3, 3, 3, 3])  # 48 + 18 = 66
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 66

    def test_non_zerg_capped_at_budget(self) -> None:
        """4 矿+满气 ideal_sum(96+24=120) 远超 NON_ZERG_WORKER_CAP=80 → 封顶 80。"""
        f = _floor()
        floor = f.make_worker_floor("TERRAN")
        floor.ai = _make_ai([24, 24, 24, 24], gas_ideals=[6, 6, 6, 6])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == f.NON_ZERG_WORKER_CAP == 80

    def test_zerg_capped_at_66_even_when_ideal_higher(self) -> None:
        """虫族同样 4 矿+满气 ideal_sum=120 → 封顶 ZERG_WORKER_CAP=66（不是 80）。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([24, 24, 24, 24], gas_ideals=[6, 6, 6, 6])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == f.ZERG_WORKER_CAP == 66

    def test_zerg_under_budget_uses_ideal_not_cap(self) -> None:
        """2 矿 ideal_sum=32 < 66 → 用 ideal_sum，不是硬塞到封顶。"""
        f = _floor()
        floor = f.make_worker_floor("ZERG")
        floor.ai = _make_ai([16, 16])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 32

    def test_in_progress_townhall_not_counted(self) -> None:
        """.ready 过滤在建 townhall —— 只有 1 个 ready townhall 时目标只算那一个。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 16


# ---------------------------------------------------------------------------
# 2. grace 门
# ---------------------------------------------------------------------------


class TestGraceGate:
    def test_base1_early_time_no_flag_blocks(self) -> None:
        """1 矿 + 早期时间 + 无 sustain flag → grace 不满足，不额外施压。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16], time=10.0)
        floor.knowledge = _no_vibecraft_knowledge()
        floor.get_unit_count = lambda: 12  # type: ignore[method-assign]
        assert floor._grace_ok() is False
        assert floor.to_count == 12  # 冻结在当前数量，不拉向 16

    def test_base3_grace_ok_even_early_time(self) -> None:
        """base_count>=3 → grace 立即满足（3 矿=真运营承诺），不管时间早晚。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16, 16, 16], time=5.0)
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor._grace_ok() is True
        assert floor.to_count == 48

    def test_base2_early_time_grace_blocked(self) -> None:
        """base_count==2 且时间早 → grace 不放行（2 矿 all-in 早期不抢出兵 larva，2026-07-10）。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16, 16], time=5.0)
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor._grace_ok() is False

    def test_base1_past_grace_time_ok(self) -> None:
        """base=1 但时间已过 _GRACE_S → grace 满足。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16], time=f.GRACE_S + 1)
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor._grace_ok() is True
        assert floor.to_count == 16

    def test_base1_before_grace_time_blocks(self) -> None:
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16], time=f.GRACE_S - 1)
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor._grace_ok() is False

    def test_sustain_uncap_active_forces_grace_ok(self) -> None:
        """sustain_uncap_active=True（哪怕 base=1 早期）→ grace 满足（评审 #4 OR 条件之一）。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16], time=5.0)
        floor.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(sustain_uncap_active=True))
        assert floor._grace_ok() is True
        assert floor.to_count == 16

    def test_sustain_uncap_inactive_no_effect(self) -> None:
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16], time=5.0)
        floor.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(sustain_uncap_active=False))
        assert floor._grace_ok() is False


# ---------------------------------------------------------------------------
# 3. race dispatch
# ---------------------------------------------------------------------------


class TestRaceDispatch:
    def test_protoss_probe_nexus_budget_80(self) -> None:
        f = _floor()
        from sc2.ids.unit_typeid import UnitTypeId as U

        floor = f.make_worker_floor("PROTOSS")
        assert floor.unit_type == U.PROBE
        assert floor.from_building == U.NEXUS
        assert floor._drone_budget == f.NON_ZERG_WORKER_CAP

    def test_zerg_drone_larva_budget_66(self) -> None:
        f = _floor()
        from sc2.ids.unit_typeid import UnitTypeId as U

        floor = f.make_worker_floor("ZERG")
        assert floor.unit_type == U.DRONE
        assert floor.from_building == U.LARVA
        assert floor._drone_budget == f.ZERG_WORKER_CAP

    def test_terran_scv_commandcenter_budget_80(self) -> None:
        f = _floor()
        from sc2.ids.unit_typeid import UnitTypeId as U

        floor = f.make_worker_floor("TERRAN")
        assert floor.unit_type == U.SCV
        assert floor.from_building == U.COMMANDCENTER
        assert floor._drone_budget == f.NON_ZERG_WORKER_CAP

    def test_soft_floor_priority_false(self) -> None:
        """priority=False —— 软地板，不 reserve 资源，军队 sibling act 同帧仍能 train。"""
        f = _floor()
        for race in ("PROTOSS", "ZERG", "TERRAN"):
            assert f.make_worker_floor(race).priority is False

    def test_invalid_race_raises(self) -> None:
        f = _floor()
        with pytest.raises(ValueError, match="invalid race"):
            f.make_worker_floor("RANDOM")

    def test_invalid_race_raises_empty(self) -> None:
        f = _floor()
        with pytest.raises(ValueError, match="invalid race"):
            f.make_worker_floor("")


# ---------------------------------------------------------------------------
# 4. 真实 ActUnit 子类化
# ---------------------------------------------------------------------------


class TestRealActUnitSubclass:
    def test_is_instance_of_real_act_unit(self) -> None:
        f = _floor()
        from sharpy.plans.acts import ActUnit

        assert isinstance(f.make_worker_floor("PROTOSS"), ActUnit)

    def test_to_count_setter_is_noop_ignores_init_value(self) -> None:
        """ActUnit.__init__ 里 `self.to_count = to_count(9999)` 必须被 setter 吞掉，
        不会让 getter 之后被 9999 污染。"""
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.ai = _make_ai([16, 16])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 32  # 不是 9999

    def test_explicit_setter_call_also_noop(self) -> None:
        f = _floor()
        floor = f.make_worker_floor("PROTOSS")
        floor.to_count = 12345  # 应被忽略
        floor.ai = _make_ai([16, 16])
        floor.knowledge = _no_vibecraft_knowledge()
        assert floor.to_count == 32

    def test_to_count_before_start_returns_zero(self) -> None:
        """ai 未设置（start() 未调用）时 to_count 安全返回 0，不崩。"""
        f = _floor()
        floor = f.WorkerSaturationFloorAct.__new__(f.WorkerSaturationFloorAct)
        floor._drone_budget = 80
        assert floor.to_count == 0


# ---------------------------------------------------------------------------
# 5. 构造回归：三族 bot 挂了 Floor 仍能 create_plan()，且 Floor 是顶层兄弟
#    （评审 #2 挂载点验证：绝不进 SequentialList）
# ---------------------------------------------------------------------------


class _TrivialDummy:
    """最小 dummy plan：create_plan() 返回空 BuildOrder，供构造回归测试用。"""

    def create_plan(self) -> Any:
        from sharpy.plans import BuildOrder

        return BuildOrder([])


def _make_dummy_library() -> Any:
    from vibecraft.strategy.models import OpeningBuild

    opening = OpeningBuild(
        id="dummy_x",
        display_name_zh="x",
        sharpy_dummy_class="tests.unit.test_worker_saturation_floor:_TrivialDummy",
    )
    return SimpleNamespace(all_strategies=lambda: [opening])


class TestBotWiringConstructionRegression:
    """真实 sharpy + 真实 bot 工厂：construct → create_plan() → 检查顶层 orders。"""

    def _common_kwargs(self) -> dict[str, Any]:
        return {
            "director_factory": lambda f: None,
            "strategy_library": _make_dummy_library(),
            "status_callback": None,
            "down_q": None,
            "echo_callback": None,
            "snapshot_callback": None,
            "event_callback": None,
            "minimap_callback": None,
            "run_command_with_echo_fn": lambda *a: None,
        }

    def test_protoss_floor_top_level_sibling(self) -> None:
        f = _floor()
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        cls = make_protoss_bot_class(**self._common_kwargs())
        inst = cls()
        inst.active_recipe = "dummy_x"
        plan = asyncio.run(inst.create_plan())

        assert any(isinstance(o, f.WorkerSaturationFloorAct) for o in plan.orders)
        floor = next(o for o in plan.orders if isinstance(o, f.WorkerSaturationFloorAct))
        assert floor.unit_type.name == "PROBE"
        assert floor._drone_budget == f.NON_ZERG_WORKER_CAP

    def test_zerg_floor_top_level_sibling(self) -> None:
        f = _floor()
        from vibecraft.bot.auto_combat.zerg.bot import make_zerg_bot_class

        cls = make_zerg_bot_class(**self._common_kwargs())
        inst = cls()
        inst.active_recipe = "dummy_x"
        plan = asyncio.run(inst.create_plan())

        assert any(isinstance(o, f.WorkerSaturationFloorAct) for o in plan.orders)
        floor = next(o for o in plan.orders if isinstance(o, f.WorkerSaturationFloorAct))
        assert floor.unit_type.name == "DRONE"
        assert floor.from_building.name == "LARVA"
        assert floor._drone_budget == f.ZERG_WORKER_CAP

    def test_terran_floor_top_level_sibling(self) -> None:
        f = _floor()
        from vibecraft.bot.auto_combat.terran.bot import make_terran_bot_class

        cls = make_terran_bot_class(**self._common_kwargs())
        inst = cls()
        inst.active_recipe = "dummy_x"
        plan = asyncio.run(inst.create_plan())

        assert any(isinstance(o, f.WorkerSaturationFloorAct) for o in plan.orders)
        floor = next(o for o in plan.orders if isinstance(o, f.WorkerSaturationFloorAct))
        assert floor.unit_type.name == "SCV"
        assert floor._drone_budget == f.NON_ZERG_WORKER_CAP

    def test_dummy_import_failure_still_wires_floor(self) -> None:
        """候选 dummy_spec 指向不存在的模块 → import 失败落 fallback 分支，仍要走
        `_wrap(result)` 主路径——Floor 不能只在"dummy 正常加载"时才挂上。"""
        f = _floor()
        from vibecraft.strategy.models import OpeningBuild

        opening = OpeningBuild(
            id="broken_dummy",
            display_name_zh="x",
            sharpy_dummy_class="vibecraft.bot.auto_combat.does_not_exist:Nope",
        )
        lib = SimpleNamespace(all_strategies=lambda: [opening])

        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        kwargs = self._common_kwargs()
        kwargs["strategy_library"] = lib
        cls = make_protoss_bot_class(**kwargs)
        inst = cls()
        inst.active_recipe = "broken_dummy"
        plan = asyncio.run(inst.create_plan())

        assert any(isinstance(o, f.WorkerSaturationFloorAct) for o in plan.orders)
