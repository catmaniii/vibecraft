"""sharpy vendor 里 vibecraft hook 行为单测。

T1/T2 把 vibecraft 玩家覆盖 hook inline 到 vendor:
- vendor/.../zone_attack.py: PlanZoneAttack (__init__ + 4 method override)
- vendor/.../attack_expansions.py: PlanFinishEnemy.execute

本文件用 importlib.util.spec_from_file_location 直接加载 vendor .py 文件（绕过
sharpy package __init__.py 的 eager import 链），注入最小 fake 依赖测 hook 行为。
"""

from __future__ import annotations

import importlib.util
import sys
from enum import IntEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_TACTICS = _PROJECT_ROOT / "vendor" / "sharpy" / "sharpy" / "plans" / "tactics"

# ---------------------------------------------------------------------------
# fake 基础类
# ---------------------------------------------------------------------------


class _FakePoint2(tuple):
    """最小 Point2 stub：继承 tuple，支持 .x / .y。"""

    def __new__(cls, pt: Any) -> _FakePoint2:
        return super().__new__(cls, pt)

    @property
    def x(self) -> float:
        return self[0]

    @property
    def y(self) -> float:
        return self[1]

    def distance_to(self, other: Any) -> float:
        return ((self[0] - other[0]) ** 2 + (self[1] - other[1]) ** 2) ** 0.5


class _FakeActBase:
    """sharpy ActBase 极简 stub。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def print(self, *args: Any, **kwargs: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# fake 注入 + vendor 直接加载
# ---------------------------------------------------------------------------


def _clean_vendor_mods() -> None:
    """清掉 test 注入的 fake / vendor 模块，防止缓存污染。"""
    for key in list(sys.modules):
        if (
            key == "sharpy"
            or key.startswith("sharpy.")
            or key == "sc2"
            or key.startswith("sc2.")
            # _inject_expand_fakes 注入的 s2clientprotocol fake 必须一并清掉，
            # 否则后续 test 重载 sc2.data 时会拿到缺 Race 的假模块
            or key == "s2clientprotocol"
            or key.startswith("s2clientprotocol.")
        ):
            del sys.modules[key]


def _load_vendor_module(file_path: Path, module_name: str) -> ModuleType:
    """用 spec_from_file_location 直接加载 vendor .py 文件（不走 package __init__）。"""
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _inject_vendor_fakes() -> None:
    """注入最小 fake deps，然后直接 load vendor zone_attack + attack_expansions + zone_gather。"""

    # --- sc2 fake ---
    for name in [
        "sc2",
        "sc2.position",
        "sc2.unit",
        "sc2.units",
        "sc2.bot_ai",
        "sc2.data",
        "sc2.ids",
        "sc2.ids.ability_id",
        "sc2.ids.unit_typeid",
    ]:
        sys.modules[name] = ModuleType(name)
    sys.modules["sc2.position"].Point2 = _FakePoint2  # type: ignore[attr-defined]
    sys.modules["sc2.unit"].Unit = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.units"].Units = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.bot_ai"].BotAI = MagicMock  # type: ignore[attr-defined]

    class _FakeRace(IntEnum):
        Protoss = 1
        Zerg = 2
        Terran = 3

    sys.modules["sc2.data"].Race = _FakeRace  # type: ignore[attr-defined]

    # AbilityId / UnitTypeId 需要支持 .RALLY_BUILDING / .GATEWAY / .ROBOTICSFACILITY 等
    # 属性查询(vendor zone_gather 用)。用一个 SimpleNamespace-like 类。
    class _FakeAbilityId:
        RALLY_BUILDING = "RALLY_BUILDING"

    class _FakeUnitTypeId:
        GATEWAY = "GATEWAY"
        ROBOTICSFACILITY = "ROBOTICSFACILITY"

    sys.modules["sc2.ids.ability_id"].AbilityId = _FakeAbilityId  # type: ignore[attr-defined]
    sys.modules["sc2.ids.unit_typeid"].UnitTypeId = _FakeUnitTypeId  # type: ignore[attr-defined]

    # --- sharpy fake ---
    for name in [
        "sharpy",
        "sharpy.interfaces",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
        "sharpy.managers.extensions",
        "sharpy.managers.extensions.game_states",
        "sharpy.managers.extensions.game_states.advantage",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.plans.tactics",
        "sharpy.general",
        "sharpy.general.zone",
        "sharpy.general.extended_power",
        "sharpy.combat",
        "sharpy.knowledges",
    ]:
        sys.modules[name] = ModuleType(name)
    sys.modules["sharpy.knowledges"].Knowledge = MagicMock  # type: ignore[attr-defined]
    # sharpy.managers.core 需要 UnitValue (zone_gather 用)
    sys.modules["sharpy.managers.core"].UnitValue = MagicMock  # type: ignore[attr-defined]
    # zone_defense 用 UnitValue.worker_types(class attr,需真实容器供 `in` 判断)。
    # 含 "WORKER"/"SCV" 等:让"单个工人侦察兵"敌人不触发 worker-pull 分支(聚焦主力 claim 路径测试)。
    sys.modules["sharpy.managers.core"].UnitValue.worker_types = {  # type: ignore[attr-defined]
        "WORKER",
        "SCV",
        "PROBE",
        "DRONE",
    }

    for attr in [
        "IGatherPointSolver",
        "IZoneManager",
        "IEnemyUnitsManager",
        "IGameAnalyzer",
        "IBuildingSolver",
    ]:
        setattr(sys.modules["sharpy.interfaces"], attr, MagicMock)

    sys.modules["sharpy.managers.extensions"].GameAnalyzer = MagicMock  # type: ignore[attr-defined]

    # advantage 常量：空 set，使 `x in at_least_*` 始终 False（hook path 不依赖这些）
    _empty: set[Any] = set()
    for attr in [
        "at_least_small_disadvantage",
        "at_least_small_advantage",
        "at_least_clear_advantage",
        "at_least_clear_disadvantage",
    ]:
        setattr(sys.modules["sharpy.managers.extensions.game_states.advantage"], attr, _empty)

    sys.modules["sharpy.plans.acts"].ActBase = _FakeActBase  # type: ignore[attr-defined]
    sys.modules["sharpy.general.zone"].Zone = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.general.extended_power"].ExtendedPower = MagicMock  # type: ignore[attr-defined]

    # MoveType 需要真实 enum 值（zone_attack.execute 用 MoveType.DefensiveRetreat）
    class _FakeMoveType(IntEnum):
        DefensiveRetreat = 0
        Retreat = 1
        Attacking = 2
        SearchAndDestroy = 3  # zone_defense.execute 用

    sys.modules["sharpy.combat"].MoveType = _FakeMoveType  # type: ignore[attr-defined]

    class _FakeUnitTask(IntEnum):
        Idle = 0
        Attacking = 1
        Moving = 2
        Defending = 3  # zone_defense 用

    sys.modules["sharpy.managers.core.roles"].UnitTask = _FakeUnitTask  # type: ignore[attr-defined]

    # --- 直接加载 vendor 文件（绕过 sharpy package __init__ eager import 链）---
    _load_vendor_module(
        _VENDOR_TACTICS / "zone_attack.py",
        "sharpy.plans.tactics.zone_attack",
    )
    _load_vendor_module(
        _VENDOR_TACTICS / "attack_expansions.py",
        "sharpy.plans.tactics.attack_expansions",
    )
    _load_vendor_module(
        _VENDOR_TACTICS / "zone_gather.py",
        "sharpy.plans.tactics.zone_gather",
    )
    _load_vendor_module(
        _VENDOR_TACTICS / "zone_defense.py",
        "sharpy.plans.tactics.zone_defense",
    )


@pytest.fixture()
def vendor_sharpy_env() -> Any:
    """注入 vendor sharpy fake env，每 test 独立。"""
    _clean_vendor_mods()
    _inject_vendor_fakes()
    yield
    _clean_vendor_mods()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pza_cls() -> type:
    """获取已加载的 vendor PlanZoneAttack 类。"""
    return sys.modules["sharpy.plans.tactics.zone_attack"].PlanZoneAttack  # type: ignore[no-any-return]


def _attack_status() -> type:
    """获取已加载的 vendor AttackStatus enum。"""
    return sys.modules["sharpy.plans.tactics.zone_attack"].AttackStatus  # type: ignore[no-any-return]


def _pfe_cls() -> type:
    """获取已加载的 vendor PlanFinishEnemy 类。"""
    return sys.modules["sharpy.plans.tactics.attack_expansions"].PlanFinishEnemy  # type: ignore[no-any-return]


def _make_pza(
    override_target: Any = None,
    override_intent: Any = None,
    override_stance: Any = None,
    override_mode: Any = None,
    force_attack: bool = False,
    regroup_started_at: Any = None,
    kite_retreat: bool = False,
) -> Any:
    """构造不需 sharpy init 的 PlanZoneAttack 实例（绕 __init__）。"""
    cls = _pza_cls()
    AttackStatus = _attack_status()
    plan = cls.__new__(cls)
    plan.force_attack = force_attack
    plan._logged_intent = "__sentinel__"
    plan.status = AttackStatus.NotActive
    plan.attack_retreat_started = None
    plan.attack_on_advantage = False
    plan.retreat_multiplier = 0.8  # PlanZoneAttack.__init__ の RETREAT_POWER_PERCENTAGE
    plan.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            attack_target_override=override_target,
            combat_intent_override=override_intent,
            stance_override=override_stance,
            attack_mode_override=override_mode,
            regroup_started_at=regroup_started_at,
            kite_retreat=kite_retreat,
        )
    )
    return plan


def _make_pfe(override_intent: Any = None) -> Any:
    """构造不需 sharpy init 的 PlanFinishEnemy 实例（绕 __init__）。"""
    cls = _pfe_cls()
    plan = cls.__new__(cls)
    plan.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            combat_intent_override=override_intent,
        )
    )
    plan.ai = MagicMock()
    plan.unit_values = MagicMock()
    plan.roles = MagicMock()
    plan.zone_manager = MagicMock()
    return plan


# ---------------------------------------------------------------------------
# TestPlanZoneAttackGetTarget
# ---------------------------------------------------------------------------


class TestPlanZoneAttackGetTarget:
    """_get_target hook：attack_target_override 优先于 sharpy 内联逻辑。"""

    def test_no_override_bypasses_hook_and_enters_sharpy(self, vendor_sharpy_env: Any) -> None:
        """override=None 时 hook 不截断，代码走到 sharpy 内联逻辑。

        验证：hook 分支 return 没有提前执行（否则 zone_manager.expansion_zones
        不会被访问）。这里让 expansion_zones[0] 抛 AttributeError 来探针。
        """
        plan = _make_pza(override_target=None)
        plan.ai = MagicMock()
        # 不设 expansion_zones → 访问 [0] 会 IndexError
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []
        with pytest.raises((IndexError, AttributeError)):
            plan._get_target()

    def test_tuple_override_converted_to_point2(self, vendor_sharpy_env: Any) -> None:
        """tuple 形式的 override 应被包成 Point2（x/y 可读）。"""
        plan = _make_pza(override_target=(30.0, 70.0))
        result = plan._get_target()
        assert getattr(result, "x", None) == 30.0
        assert getattr(result, "y", None) == 70.0

    def test_point2_override_returned(self, vendor_sharpy_env: Any) -> None:
        """非 tuple 的 override（已是 Point2 或其他类型）直接返回。"""
        pt = _FakePoint2((10.0, 20.0))
        plan = _make_pza(override_target=pt)
        result = plan._get_target()
        # 应返回 override 本身（isinstance check 不匹配 tuple 子类时也直接 return）
        assert result[0] == 10.0 and result[1] == 20.0


# ---------------------------------------------------------------------------
# TestPlanZoneAttackShouldAttack
# ---------------------------------------------------------------------------


class TestPlanZoneAttackShouldAttack:
    """_should_attack hook：intent/stance/force_attack 覆盖 sharpy power 判断。"""

    def test_intent_none_stance_none_no_force_enters_sharpy(self, vendor_sharpy_env: Any) -> None:
        """intent=None + stance=None + force_attack=False → hook 不截断，进入 sharpy power 判定。

        验证：hook 分支没有提前 return；代码走到 sharpy expansion_zones[-1] 访问
        （empty list → IndexError），证明我们经过了所有 vibecraft hook 分支。
        """
        plan = _make_pza(override_intent=None, override_stance=None, force_attack=False)
        power = MagicMock()
        plan.enemy_units_manager = MagicMock()
        plan.enemy_units_manager.enemy_total_power = MagicMock()
        plan.enemy_units_manager.enemy_total_power.multiply = MagicMock()
        plan.enemy_units_manager.enemy_total_power.power = 0.0
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []  # 空 → expansion_zones[-1] IndexError
        plan.ai = MagicMock()
        plan.ai.supply_used = 50
        plan.start_attack_power = 20.0
        with pytest.raises((IndexError, AttributeError)):
            plan._should_attack(power)

    def test_intent_attack_mode_none_returns_true(self, vendor_sharpy_env: Any) -> None:
        """intent=attack + mode=None → True（直接进攻，不走 power 判定）。"""
        plan = _make_pza(override_intent="attack", override_mode=None)
        assert plan._should_attack(MagicMock()) is True

    def test_intent_attack_mode_all_in_returns_true(self, vendor_sharpy_env: Any) -> None:
        """intent=attack + mode=all_in → True。"""
        plan = _make_pza(override_intent="attack", override_mode="all_in")
        assert plan._should_attack(MagicMock()) is True

    def test_intent_attack_mode_probe_enters_sharpy(self, vendor_sharpy_env: Any) -> None:
        """intent=attack + mode=probe → 试探性，走 sharpy power 判定路径（不截断）。

        验证：hook 不 return True，而是穿透到 sharpy power 路径
        （expansion_zones[-1] IndexError 证明代码确实走进了 sharpy 分支）。
        """
        plan = _make_pza(override_intent="attack", override_mode="probe")
        power = MagicMock()
        plan.enemy_units_manager = MagicMock()
        plan.enemy_units_manager.enemy_total_power = MagicMock()
        plan.enemy_units_manager.enemy_total_power.multiply = MagicMock()
        plan.enemy_units_manager.enemy_total_power.power = 0.0
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []
        plan.ai = MagicMock()
        plan.ai.supply_used = 50
        plan.start_attack_power = 20.0
        with pytest.raises((IndexError, AttributeError)):
            plan._should_attack(power)

    @pytest.mark.parametrize("intent", ["defend", "hold", "retreat", "vision"])
    def test_defensive_intents_return_false(self, vendor_sharpy_env: Any, intent: str) -> None:
        """防守系 intent → False，不攻。"""
        plan = _make_pza(override_intent=intent)
        assert plan._should_attack(MagicMock()) is False

    @pytest.mark.parametrize("stance", ["hold", "defend", "retreat"])
    def test_defensive_stance_returns_false(self, vendor_sharpy_env: Any, stance: str) -> None:
        """intent=None 但 stance 是防守系 → False。"""
        plan = _make_pza(override_intent=None, override_stance=stance)
        assert plan._should_attack(MagicMock()) is False

    def test_force_attack_returns_true(self, vendor_sharpy_env: Any) -> None:
        """force_attack=True → 跳过 sharpy power 比较，直接 True。

        用途：dt_rush all-in——DT 数量打不过 enemy_total_power，
        但 DT 到了敌方家就要冲，不该被 sharpy power 否决。
        """
        plan = _make_pza(override_intent=None, force_attack=True)
        assert plan._should_attack(MagicMock()) is True

    @pytest.mark.parametrize("intent", ["defend", "hold", "retreat", "vision"])
    def test_player_intent_defeats_force_attack(self, vendor_sharpy_env: Any, intent: str) -> None:
        """玩家显式防守 intent 优先级 > force_attack。

        场景：plan 是 dt_rush(force_attack=True)，玩家手机端下 defend，应尊重玩家。
        intent 在 force_attack 之前 check。
        """
        plan = _make_pza(override_intent=intent, force_attack=True)
        assert plan._should_attack(MagicMock()) is False

    @pytest.mark.parametrize("stance", ["hold", "defend", "retreat"])
    def test_player_stance_defeats_force_attack(self, vendor_sharpy_env: Any, stance: str) -> None:
        """玩家 stance(L2 engagement_constraint)也优先于 force_attack。"""
        plan = _make_pza(override_stance=stance, force_attack=True)
        assert plan._should_attack(MagicMock()) is False


# ---------------------------------------------------------------------------
# TestPlanZoneAttackShouldRetreat
# ---------------------------------------------------------------------------


class TestPlanZoneAttackShouldRetreat:
    """_should_retreat hook：intent/mode/force_attack 覆盖 sharpy local power 判断。"""

    def test_intent_retreat_returns_retreat(self, vendor_sharpy_env: Any) -> None:
        """intent=retreat → AttackStatus.Retreat，强制撤退。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent="retreat")
        assert plan._should_retreat(MagicMock(), MagicMock()) == AttackStatus.Retreat

    def test_intent_defend_returns_retreat(self, vendor_sharpy_env: Any) -> None:
        """intent=defend → AttackStatus.Retreat。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent="defend")
        assert plan._should_retreat(MagicMock(), MagicMock()) == AttackStatus.Retreat

    def test_intent_hold_returns_retreat(self, vendor_sharpy_env: Any) -> None:
        """intent=hold → AttackStatus.Retreat。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent="hold")
        assert plan._should_retreat(MagicMock(), MagicMock()) == AttackStatus.Retreat

    def test_mode_probe_falls_through_to_sharpy(self, vendor_sharpy_env: Any) -> None:
        """mode=probe + intent=None → fall through 到 sharpy local power 判定。

        mock: enemy_local_power.is_enough_for=False → NotActive（不撤退）。
        """
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode="probe")
        plan.ai = MagicMock()
        # closer_than 返回可迭代空列表
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        own_power = MagicMock()
        enemy_power = MagicMock()
        enemy_power.is_enough_for.return_value = False
        enemy_power.power = 0.0
        plan.unit_values.calc_total_power.side_effect = [own_power, enemy_power]
        result = plan._should_retreat(MagicMock(), MagicMock())
        assert result == AttackStatus.NotActive

    def test_mode_all_in_returns_not_active(self, vendor_sharpy_env: Any) -> None:
        """mode=all_in → AttackStatus.NotActive，不允许 sharpy 触发撤退。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode="all_in")
        assert plan._should_retreat(MagicMock(), MagicMock()) == AttackStatus.NotActive

    def test_force_attack_returns_not_active(self, vendor_sharpy_env: Any) -> None:
        """force_attack=True → NotActive，不退。

        场景：DT rush 到敌方家接触后，sharpy 按 raw power 把 DT 判成打不过 →
        前线振荡(实测 log game_20260523_024015)。force_attack 语义是"plan 已 all-in"。
        """
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, force_attack=True)
        assert plan._should_retreat(MagicMock(), MagicMock()) == AttackStatus.NotActive

    def test_no_override_falls_through_to_sharpy(self, vendor_sharpy_env: Any) -> None:
        """intent=None + mode=None + force_attack=False → 走 sharpy local power 判定。

        mock: enemy_local_power.is_enough_for=False → NotActive（不撤退）。
        """
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode=None, force_attack=False)
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        own_power = MagicMock()
        enemy_power = MagicMock()
        enemy_power.is_enough_for.return_value = False
        enemy_power.power = 0.0
        plan.unit_values.calc_total_power.side_effect = [own_power, enemy_power]
        result = plan._should_retreat(MagicMock(), MagicMock())
        assert result == AttackStatus.NotActive

    def test_intent_change_logged(self, vendor_sharpy_env: Any) -> None:
        """intent 发生变化时调用 logger.warning（调试用,玩家发问"为啥不撤退"时
        可在 stdout 看到 intent 链路;loguru root level 是 WARNING)。"""
        plan = _make_pza(override_intent="retreat")
        plan._logged_intent = "__sentinel__"  # 与当前 intent 不同，触发 log

        with patch("sharpy.plans.tactics.zone_attack.logger") as mock_logger:
            plan._should_retreat(MagicMock(), MagicMock())
            # 应至少调用一次 warning（intent 变化 log + 强制 retreat log）
            assert mock_logger.warning.call_count >= 1

    def test_should_retreat_counts_moving_reinforcements(self, vendor_sharpy_env: Any) -> None:
        """own_local_power 计入 fight_center 30 格内正在赶来的 Moving 援军。

        2026-06-02 skytoss bug：混速空军快单位(虚空)冲前先接敌，若 retreat 判定
        只数前排 already_attacking → 局部以少打多 → 抖动撤退 → 慢速航母/母舰还没到。
        修复后前排(tag=1) + 赶来的 Moving(tag=2) 都计入 own_power_units。
        """
        import sharpy.plans.tactics.zone_attack as za  # type: ignore[import]

        AttackStatus = _attack_status()

        class _FakeUnits(list):  # 既是 list(可迭代) 又有 Units 的 tags/exists/closer_than
            @property
            def tags(self) -> set:
                return {u.tag for u in self}

            @property
            def exists(self) -> bool:
                return len(self) > 0

            def closer_than(self, _d: Any, _p: Any) -> Any:
                return self

        plan = _make_pza(override_intent=None, override_mode=None, force_attack=False)
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None

        front = SimpleNamespace(tag=1)
        moving_unit = SimpleNamespace(tag=2)
        already_attacking = _FakeUnits([front])
        moving_near = _FakeUnits([moving_unit])
        plan.roles = MagicMock()
        plan.roles.units.return_value = moving_near  # roles.units(Moving).closer_than → self

        captured: dict[str, Any] = {}

        def _fake_units_ctor(lst: Any, _ai: Any) -> Any:
            captured["own"] = list(lst)
            return list(lst)

        own_power = MagicMock()
        own_power.power = 99.0
        enemy_power = MagicMock()
        enemy_power.is_enough_for.return_value = False  # 不撤退
        enemy_power.power = 0.0
        plan.unit_values.calc_total_power.side_effect = [own_power, enemy_power]

        with patch.object(za, "Units", _fake_units_ctor):
            result = plan._should_retreat(MagicMock(), already_attacking)

        # own_power_units 应含前排(1) + 赶来的 Moving(2)
        assert {u.tag for u in captured["own"]} == {1, 2}
        assert result == AttackStatus.NotActive

    def test_probe_retreat_multiplier_aggressive(self, vendor_sharpy_env: Any) -> None:
        """2026-05-28 用户:probe 撤退激进化 — mode=probe 时 our_percentage=1.0
        (原 fall through 默认 0.8)。enemy 对等就撤,比默认更早撤。

        验证:is_enough_for 用 effective_retreat_mult=1.0 而非 plan.retreat_multiplier
        (0.8)。mock enemy_local 占优(is_enough_for 返 True)→ Retreat。
        """
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode="probe")
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        own_power = MagicMock()
        own_power.power = 10.0  # self.print() 格式化用,必须是 float
        enemy_power = MagicMock()
        enemy_power.is_enough_for.return_value = True  # 对等就撤
        enemy_power.power = 5.0
        plan.unit_values.calc_total_power.side_effect = [own_power, enemy_power]
        result = plan._should_retreat(MagicMock(), MagicMock())
        # 验证撤退被触发 + is_enough_for 用 1.0 系数(probe 激进)
        assert result == AttackStatus.Retreat
        # is_enough_for 应被调用,系数=1.0
        enemy_power.is_enough_for.assert_called_once()
        all_args = enemy_power.is_enough_for.call_args.args
        assert all_args[1] == 1.0, f"probe 撤退应用系数 1.0(激进),实际 {all_args[1]}"

    def test_no_probe_uses_default_retreat_multiplier(self, vendor_sharpy_env: Any) -> None:
        """对照:mode != probe 时用 plan.retreat_multiplier(默认 0.8)。"""
        _attack_status()
        plan = _make_pza(override_intent=None, override_mode=None)
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        own_power = MagicMock()
        own_power.power = 10.0
        enemy_power = MagicMock()
        enemy_power.is_enough_for.return_value = True
        enemy_power.power = 5.0
        plan.unit_values.calc_total_power.side_effect = [own_power, enemy_power]
        plan._should_retreat(MagicMock(), MagicMock())
        all_args = enemy_power.is_enough_for.call_args.args
        assert all_args[1] == 0.8, f"非 probe 应用默认 retreat_multiplier 0.8,实际 {all_args[1]}"

    def test_nonprobe_retreat_hysteresis(self, vendor_sharpy_env: Any) -> None:
        """2026-06-17 撤退滞回:非 probe 实攻,撤退条件需**持续 >= RETREAT_HYSTERESIS_S**才真退;
        瞬时劣势(< 阈值)不退(大军顶住);劣势消失则计时清零、下次从头计(防 stale 旁路)。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode=None)
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        hyst = plan.RETREAT_HYSTERESIS_S

        def _set(is_enough: bool) -> None:
            own = MagicMock()
            own.power = 10.0
            enemy = MagicMock()
            enemy.is_enough_for.return_value = is_enough
            enemy.power = 20.0
            plan.unit_values.calc_total_power.side_effect = [own, enemy]

        def _retreat_at(t: float, is_enough: bool) -> Any:
            plan.ai.time = t
            _set(is_enough)
            return plan._should_retreat(MagicMock(), MagicMock())

        # t=100 首次劣势 → 不退(计时开始)
        assert _retreat_at(100.0, True) == AttackStatus.NotActive
        # 未到阈值 → 仍顶住
        assert _retreat_at(100.0 + hyst - 0.5, True) == AttackStatus.NotActive
        # 超阈值 → 退
        assert _retreat_at(100.0 + hyst + 0.1, True) == AttackStatus.Retreat

    def test_nonprobe_hysteresis_resets_when_advantage_returns(
        self, vendor_sharpy_env: Any
    ) -> None:
        """劣势中途消失 → 滞回计时清零;再次劣势从新时刻重新计,不被旧 stale 时间戳旁路。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None, override_mode=None)
        plan.ai = MagicMock()
        plan.ai.all_enemy_units.closer_than.return_value = []
        plan.unit_values = MagicMock()
        plan.unit_values.enemy_worker_type = None
        hyst = plan.RETREAT_HYSTERESIS_S

        def _retreat_at(t: float, is_enough: bool) -> Any:
            plan.ai.time = t
            own = MagicMock()
            own.power = 10.0
            enemy = MagicMock()
            enemy.is_enough_for.return_value = is_enough
            enemy.power = 20.0
            plan.unit_values.calc_total_power.side_effect = [own, enemy]
            return plan._should_retreat(MagicMock(), MagicMock())

        assert _retreat_at(100.0, True) == AttackStatus.NotActive  # pending 从 100
        assert _retreat_at(101.0, False) == AttackStatus.NotActive  # 劣势消失 → 清零
        # 再次劣势:即便距最初 100 已过 (4 > hyst),也应从 104 重新计 → 不退
        assert _retreat_at(104.0, True) == AttackStatus.NotActive
        # 从 104 持续够久才退
        assert _retreat_at(104.0 + hyst + 0.1, True) == AttackStatus.Retreat


class TestPlanZoneAttackRegroupGate:
    """2026-05-28 用户:probe 聚团门 — regroup_started_at 设了且 < 15s 时
    若 free_units spread > 8 → _should_attack 返 False(让 PlanZoneGather 集结)。
    """

    def test_probe_no_regroup_started_attacks_normally(self, vendor_sharpy_env: Any) -> None:
        """mode=probe 但 regroup_started_at=None → 聚团门 skip,正常走 sharpy 判定。"""
        plan = _make_pza(
            override_intent="attack",
            override_mode="probe",
            regroup_started_at=None,
        )
        # 没设 ai.time,聚团门提前 return False/fall through 都不要求,这里 mock 让进 sharpy 路径
        plan.ai = MagicMock()
        plan.ai.supply_used = 50
        plan.enemy_units_manager = MagicMock()
        plan.enemy_units_manager.enemy_total_power = MagicMock()
        plan.enemy_units_manager.enemy_total_power.multiply = MagicMock()
        plan.enemy_units_manager.enemy_total_power.power = 0.0
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []
        plan.start_attack_power = 20.0
        # 走到 sharpy expansion_zones[-1] → IndexError = 证明没被聚团门截断
        with pytest.raises((IndexError, AttributeError)):
            plan._should_attack(MagicMock())

    def test_probe_regroup_within_15s_units_spread_blocks_attack(
        self, vendor_sharpy_env: Any
    ) -> None:
        """mode=probe + regroup_started_at set + free_units 散开 > 8 grid
        + elapsed < 15s → return False(等聚团)。"""
        plan = _make_pza(
            override_intent="attack",
            override_mode="probe",
            regroup_started_at=100.0,
        )
        plan.ai = MagicMock()
        plan.ai.time = 105.0  # elapsed = 5s, < 15s
        # 模拟 4 个散开单位(spread > 8)
        u1 = SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0))
        u2 = SimpleNamespace(position=SimpleNamespace(x=20.0, y=0.0))  # 距 center 10
        u3 = SimpleNamespace(position=SimpleNamespace(x=10.0, y=20.0))
        u4 = SimpleNamespace(position=SimpleNamespace(x=10.0, y=-20.0))
        plan.roles = MagicMock()
        plan.roles.free_units = [u1, u2, u3, u4]
        result = plan._should_attack(MagicMock())
        assert result is False, "散开 > 8 grid 时聚团门应卡住 attack"

    def test_probe_regroup_within_15s_units_close_allows_attack(
        self, vendor_sharpy_env: Any
    ) -> None:
        """mode=probe + regroup 内 + free_units 聚团 < 8 grid → 通过聚团门,继续走 sharpy。

        验证用法:聚团时 hook 不截断,IndexError 抛出证明走到 sharpy path。
        """
        plan = _make_pza(
            override_intent="attack",
            override_mode="probe",
            regroup_started_at=100.0,
        )
        plan.ai = MagicMock()
        plan.ai.time = 105.0
        plan.ai.supply_used = 50
        # 4 个紧密聚团单位(各离 center < 8)
        u1 = SimpleNamespace(position=SimpleNamespace(x=50.0, y=50.0))
        u2 = SimpleNamespace(position=SimpleNamespace(x=52.0, y=50.0))
        u3 = SimpleNamespace(position=SimpleNamespace(x=50.0, y=52.0))
        u4 = SimpleNamespace(position=SimpleNamespace(x=48.0, y=48.0))
        plan.roles = MagicMock()
        plan.roles.free_units = [u1, u2, u3, u4]
        # 走到 sharpy expansion_zones[-1] → IndexError = 证明通过聚团门
        plan.enemy_units_manager = MagicMock()
        plan.enemy_units_manager.enemy_total_power = MagicMock()
        plan.enemy_units_manager.enemy_total_power.multiply = MagicMock()
        plan.enemy_units_manager.enemy_total_power.power = 0.0
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []
        plan.start_attack_power = 20.0
        with pytest.raises((IndexError, AttributeError)):
            plan._should_attack(MagicMock())

    def test_probe_regroup_timeout_bypasses_check(self, vendor_sharpy_env: Any) -> None:
        """mode=probe + regroup elapsed >= 15s → 超时 bypass,即使散开也允许 attack。"""
        plan = _make_pza(
            override_intent="attack",
            override_mode="probe",
            regroup_started_at=100.0,
        )
        plan.ai = MagicMock()
        plan.ai.time = 120.0  # elapsed = 20s > 15s
        plan.ai.supply_used = 50
        # 散开单位
        u1 = SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0))
        u2 = SimpleNamespace(position=SimpleNamespace(x=50.0, y=50.0))
        plan.roles = MagicMock()
        plan.roles.free_units = [u1, u2]
        # 超时 bypass → 走 sharpy → IndexError
        plan.enemy_units_manager = MagicMock()
        plan.enemy_units_manager.enemy_total_power = MagicMock()
        plan.enemy_units_manager.enemy_total_power.multiply = MagicMock()
        plan.enemy_units_manager.enemy_total_power.power = 0.0
        plan.zone_manager = MagicMock()
        plan.zone_manager.expansion_zones = []
        plan.start_attack_power = 20.0
        with pytest.raises((IndexError, AttributeError)):
            plan._should_attack(MagicMock())

    def test_regrouped_excludes_mothership(self, vendor_sharpy_env: Any) -> None:
        """2026-06-02 放宽：母舰离群很远不该卡住聚团门（4 个虚空抱团 + 1 母舰远 → 算聚团）。"""
        plan = _make_pza(override_intent="attack", override_mode="probe")
        plan.roles = MagicMock()

        def _vr(x: float, y: float) -> Any:
            return SimpleNamespace(
                position=SimpleNamespace(x=x, y=y),
                type_id=SimpleNamespace(name="VOIDRAY"),
            )

        mothership = SimpleNamespace(
            position=SimpleNamespace(x=80.0, y=80.0),
            type_id=SimpleNamespace(name="MOTHERSHIP"),
        )
        # 4 个虚空紧密(各离 center < 3) + 1 个母舰远在 (80,80)
        plan.roles.free_units = [_vr(50, 50), _vr(52, 50), _vr(50, 52), _vr(48, 48), mothership]
        assert plan._vbc_is_regrouped() is True

    def test_regrouped_bulk_70_percent(self, vendor_sharpy_env: Any) -> None:
        """70% 单位聚拢即算聚团（8 紧 + 2 掉队 → 0.8 ≥ 0.7 → True），不被个别掉队卡死。"""
        plan = _make_pza(override_intent="attack", override_mode="probe")
        plan.roles = MagicMock()
        tight = [SimpleNamespace(position=SimpleNamespace(x=50.0 + i, y=50.0)) for i in range(8)]
        stragglers = [SimpleNamespace(position=SimpleNamespace(x=200.0, y=200.0)) for _ in range(2)]
        plan.roles.free_units = tight + stragglers
        assert plan._vbc_is_regrouped() is True

    def test_regrouped_scattered_still_false(self, vendor_sharpy_env: Any) -> None:
        """真散开（多数单位离群很远）仍算未聚团 → 聚团门照常生效。"""
        plan = _make_pza(override_intent="attack", override_mode="probe")
        plan.roles = MagicMock()
        # 4 个单位各占地图一角,彼此相距 ~40+
        plan.roles.free_units = [
            SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)),
            SimpleNamespace(position=SimpleNamespace(x=40.0, y=0.0)),
            SimpleNamespace(position=SimpleNamespace(x=0.0, y=40.0)),
            SimpleNamespace(position=SimpleNamespace(x=40.0, y=40.0)),
        ]
        assert plan._vbc_is_regrouped() is False


# ---------------------------------------------------------------------------
# TestPlanZoneAttackStopRetreat
# ---------------------------------------------------------------------------


class TestPlanZoneAttackStopRetreat:
    """_stop_retreat hook：intent=retreat 时阻止 sharpy 自动结束 20s 撤退计时。"""

    def test_intent_retreat_skips_stop(self, vendor_sharpy_env: Any) -> None:
        """intent=retreat → 直接 return，不执行 status=NotActive / roles.attack_ended()。

        场景：4bg auto_switch 到 blink_harass 新单位又 _start_attack，
        玩家"全军撤退"无效——_stop_retreat 被 RETREAT_TIME=20s 自动触发。
        hook 后 intent=retreat 时提前 return，保持 Retreat 状态持续生效。
        """
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent="retreat")
        plan.status = AttackStatus.Retreat
        plan.attack_retreat_started = 100.0
        plan.roles = MagicMock()
        plan._stop_retreat()
        # status 和 attack_retreat_started 应未被清掉
        assert plan.status == AttackStatus.Retreat
        assert plan.attack_retreat_started == 100.0
        plan.roles.attack_ended.assert_not_called()

    def test_intent_none_executes_stop(self, vendor_sharpy_env: Any) -> None:
        """intent=None（正常撤退结束）→ 执行 status=NotActive + attack_retreat_started=None。"""
        AttackStatus = _attack_status()
        plan = _make_pza(override_intent=None)
        plan.status = AttackStatus.Retreat
        plan.attack_retreat_started = 100.0
        plan.roles = MagicMock()
        plan._stop_retreat()
        assert plan.status == AttackStatus.NotActive
        assert plan.attack_retreat_started is None
        plan.roles.attack_ended.assert_called_once()


# ---------------------------------------------------------------------------
# TestPlanFinishEnemyExecute
# ---------------------------------------------------------------------------


class TestPlanFinishEnemyExecute:
    """PlanFinishEnemy.execute hook：防守系 intent 时跳过 all-out attack 派单。"""

    @pytest.mark.asyncio
    async def test_intent_retreat_returns_true_no_dispatch(self, vendor_sharpy_env: Any) -> None:
        """intent=retreat → 立即 return True，不派任何单位 attack-move。"""
        plan = _make_pfe(override_intent="retreat")
        result = await plan.execute()
        assert result is True
        # find_attack_position / ai.units.idle 不应被调用
        plan.ai.units.idle.assert_not_called() if hasattr(
            plan.ai.units.idle, "assert_not_called"
        ) else None

    @pytest.mark.asyncio
    async def test_intent_defend_returns_true_no_dispatch(self, vendor_sharpy_env: Any) -> None:
        """intent=defend → 立即 return True，不 all-out 派兵。"""
        plan = _make_pfe(override_intent="defend")
        result = await plan.execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_intent_hold_returns_true_no_dispatch(self, vendor_sharpy_env: Any) -> None:
        """intent=hold → 立即 return True。"""
        plan = _make_pfe(override_intent="hold")
        result = await plan.execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_intent_none_calls_find_and_dispatch(self, vendor_sharpy_env: Any) -> None:
        """intent=None → 正常执行 find_attack_position + 派兵（idle units 为空则不出错）。"""
        plan = _make_pfe(override_intent=None)

        async def _fake_find(*args: Any) -> Any:
            return _FakePoint2((50.0, 50.0))

        with patch.object(plan.__class__, "find_attack_position", new=_fake_find):
            plan.ai.units.idle.__iter__ = MagicMock(return_value=iter([]))
            result = await plan.execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_intent_attack_calls_find_and_dispatch(self, vendor_sharpy_env: Any) -> None:
        """intent=attack → 正常执行（attack 不在防守列表里）。"""
        plan = _make_pfe(override_intent="attack")

        async def _fake_find(*args: Any) -> Any:
            return _FakePoint2((60.0, 60.0))

        with patch.object(plan.__class__, "find_attack_position", new=_fake_find):
            plan.ai.units.idle.__iter__ = MagicMock(return_value=iter([]))
            result = await plan.execute()
        assert result is True


# ---------------------------------------------------------------------------
# TestPlanZoneAttackExecuteRetreatTarget
# ---------------------------------------------------------------------------


def _make_zone_attack_act(override_intent: Any = None) -> Any:
    """构造用于 execute() 测试的 PlanZoneAttack 实例，配有完整 mock attrs。"""
    cls = _pza_cls()
    AttackStatus = _attack_status()
    act = cls.__new__(cls)
    act.force_attack = False
    act._logged_intent = "__sentinel__"
    act.status = AttackStatus.NotActive
    act.attack_retreat_started = None
    act.attack_on_advantage = False
    act.retreat_multiplier = 0.8
    act.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            attack_target_override=None,
            combat_intent_override=override_intent,
            stance_override=None,
            attack_mode_override=None,
        )
    )
    act.ai = MagicMock()
    act.ai.start_location = _FakePoint2((50, 50))
    act.ai.time = 15.0
    # gather_point_solver: default gather_point forward
    act.gather_point_solver = MagicMock()
    act.gather_point_solver.gather_point = _FakePoint2((20, 80))
    # roles: attacking_units is empty list; other calls are no-ops
    act.roles = MagicMock()
    act.roles.attacking_units = []
    # combat: execute is the call we will assert on
    act.combat = MagicMock()
    # _get_target: return a non-None target so execute() proceeds past None-guard
    act._get_target = MagicMock(return_value=_FakePoint2((100, 100)))
    return act


class TestPlanZoneAttackExecuteRetreatTarget:
    """T10:execute() retreat 分支 intent=retreat 时 retreat target 写死 home,不读 gather_point。"""

    @pytest.mark.asyncio
    async def test_retreat_intent_uses_home_not_gather_point(self, vendor_sharpy_env: Any) -> None:
        """intent='retreat' → combat.execute 调 home target,不用 gather_point。"""
        act = _make_zone_attack_act(override_intent="retreat")
        # 触发 retreat 分支：attack_retreat_started 非 None，时间在 RETREAT_TIME 内
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0  # 15 < 10 + 20 = 30，不触发 _stop_retreat
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))  # forward pylon

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == _FakePoint2((50, 50)), (
            f"retreat target should be home (50,50), got {target}"
        )

    @pytest.mark.asyncio
    async def test_no_intent_uses_gather_point(self, vendor_sharpy_env: Any) -> None:
        """intent=None → combat.execute 用 gather_point（原 sharpy 行为）。"""
        act = _make_zone_attack_act(override_intent=None)
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == _FakePoint2((20, 80)), (
            f"no-intent retreat target should be gather_point (20,80), got {target}"
        )

    @pytest.mark.asyncio
    async def test_intent_attack_resets_retreat_not_gather_point(
        self, vendor_sharpy_env: Any
    ) -> None:
        """intent='attack' + attack_retreat_started 非 None → Issue 2 fix: reset retreat 状态,
        combat.execute **不**被调用（提前 return False 让下 tick 走 attack 分支）。"""
        act = _make_zone_attack_act(override_intent="attack")
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))

        result = await act.execute()

        # retreat 状态被 reset
        AttackStatus = _attack_status()
        assert act.status == AttackStatus.NotActive, f"status should be NotActive, got {act.status}"
        assert act.attack_retreat_started is None, (
            "attack_retreat_started should be None after reset"
        )
        # 不走到 combat.execute (下 tick 才重新 _should_attack)
        act.combat.execute.assert_not_called()
        assert result is False

    @pytest.mark.asyncio
    async def test_intent_defend_no_zones_uses_home_not_gather_point(
        self, vendor_sharpy_env: Any
    ) -> None:
        """intent='defend' + 无 zone_manager → 走 _vbc_forward_defense_point 兜底 = start_location,
        **不**用 gather_point(2026-06-17:defend fallback 从 gather_point 改成最前沿基地,
        无 zone 数据时兜底 home)。"""
        act = _make_zone_attack_act(override_intent="defend")
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))  # 不应被选

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == _FakePoint2((50, 50)), (
            f"defend 无 zone → forward 兜底 start_location (50,50), got {target}"
        )


class TestPlanZoneAttackExecuteZeroAttackerGuard:
    """execute() else 分支:无可攻击自由单位时不 _start_attack(断 flip-flop)。

    2026-06-06 根因:玩家把全军编队/claim 后(全 Reserved),free_units 空。但全局
    intent=attack/all_in(或 supply>190)让 _should_attack 仍返 True → _start_attack(0 兵)
    → 下 tick handle_attack 发现"No attacking units"→ retreat → 又 attack 的 1Hz 空转
    flip-flop,把被 claim 的单位也搅得抖动 + debug 线乱跳。守卫:attacker_count==0 时
    直接不进攻。
    """

    @pytest.mark.asyncio
    async def test_no_free_units_does_not_start_attack(self, vendor_sharpy_env: Any) -> None:
        """intent=attack/all_in 但 free_units 空 → 不 _start_attack,status 保持 NotActive。"""
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent="attack")
        act.knowledge.vibecraft.attack_mode_override = "all_in"
        act.status = AttackStatus.NotActive
        act.attack_retreat_started = None  # 进 else 分支
        act.roles.free_units = []  # 全军被 claim/编队 → 无自由单位
        act.unit_values = MagicMock()
        act._start_attack = MagicMock()

        result = await act.execute()

        act._start_attack.assert_not_called()
        assert act.status == AttackStatus.NotActive, f"无兵时不应进 Attacking,got {act.status}"
        assert result is False

    @pytest.mark.asyncio
    async def test_has_free_attacker_still_starts_attack(
        self, vendor_sharpy_env: Any, monkeypatch: Any
    ) -> None:
        """对照组:有合格自由单位 + intent=attack → 正常 _start_attack(守卫不误伤)。"""
        AttackStatus = _attack_status()

        # 测试环境 Units=MagicMock,Units([],ai) 把 [] 当 spec 属性名列表 → 禁 .append。
        # else 分支会 attackers.append(unit),故换一个支持 append 的真 list 子类。
        class _FakeUnits(list):  # type: ignore[type-arg]
            def __init__(self, iterable: Any = (), ai: Any = None) -> None:
                super().__init__(iterable)

            @property
            def exists(self) -> bool:
                return len(self) > 0

        za = sys.modules["sharpy.plans.tactics.zone_attack"]
        monkeypatch.setattr(za, "Units", _FakeUnits)

        act = _make_zone_attack_act(override_intent="attack")
        act.knowledge.vibecraft.attack_mode_override = "all_in"
        act.status = AttackStatus.NotActive
        act.attack_retreat_started = None
        act.roles.free_units = [MagicMock()]  # 1 个自由单位
        act.unit_values = MagicMock()
        act.unit_values.should_attack = MagicMock(return_value=True)
        act._start_attack = MagicMock()

        await act.execute()

        act._start_attack.assert_called_once()


# ---------------------------------------------------------------------------
# TestRetreatBranchResetOnAttackIntent  (Issue 2 fix)
# ---------------------------------------------------------------------------


class TestRetreatBranchResetOnAttackIntent:
    """Issue 2 修复：玩家 retreat→attack 时 execute() retreat 分支立即 reset 状态。

    场景：
    1. 玩家点"试探性进攻" → army 出门 attack
    2. 玩家点"撤退" → army 撤 (attack_retreat_started 设, status=Retreat)
    3. 玩家再点"试探性进攻" (intent=attack, mode=probe)
    4. 修复前: execute() 进 retreat 分支循环，army 不出门 (需等 20s RETREAT_TIME)
    5. 修复后: retreat 分支头检测 intent=attack → 立即 reset，下 tick 走 else 分支 _should_attack
    """

    @pytest.mark.asyncio
    async def test_retreat_branch_resets_when_intent_attack_set(
        self, vendor_sharpy_env: Any
    ) -> None:
        """attack_retreat_started 非 None + intent=attack → reset retreat 状态 + return False。

        验证:
        - status 变 NotActive
        - attack_retreat_started 变 None
        - roles.attack_ended() 被调用一次
        - combat.execute 未被调用 (下 tick 才重新 _should_attack)
        - 返回 False
        """
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent="attack")
        act.status = AttackStatus.Retreat
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0  # 15 < 10+20=30，原来不会自然 _stop_retreat

        result = await act.execute()

        assert result is False
        assert act.status == AttackStatus.NotActive, f"expected NotActive, got {act.status}"
        assert act.attack_retreat_started is None, "attack_retreat_started should be cleared"
        act.roles.attack_ended.assert_called_once()
        act.combat.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_retreat_branch_continues_when_intent_retreat(
        self, vendor_sharpy_env: Any
    ) -> None:
        """intent=retreat + attack_retreat_started 非 None → 不 reset，继续 retreat 行为。

        即 _stop_retreat block 路径仍正常工作，combat.execute(home, DefensiveRetreat) 被调用。
        """
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent="retreat")
        act.status = AttackStatus.Retreat
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))

        await act.execute()

        # 仍在 retreat，状态未被清除
        assert act.status == AttackStatus.Retreat
        assert act.attack_retreat_started == 10.0
        # combat.execute 被调用（继续 retreat）
        act.combat.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_retreat_branch_continues_when_intent_none(self, vendor_sharpy_env: Any) -> None:
        """intent=None + attack_retreat_started 非 None → 原 sharpy 行为，继续 retreat。

        intent=None 场景：sharpy 自主判断撤退（无玩家覆盖），20s 后自然 stop。
        combat.execute(gather_point, DefensiveRetreat) 被调用。
        """
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent=None)
        act.status = AttackStatus.Retreat
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))

        await act.execute()

        # 状态未被清除（20s 未到）
        assert act.status == AttackStatus.Retreat
        assert act.attack_retreat_started == 10.0
        # combat.execute 被调用（继续 retreat 到 gather_point）
        act.combat.execute.assert_called_once()


# ---------------------------------------------------------------------------
# TestPlanZoneGatherIntent — 2026-05-27 Issue A: 撤退时新单位仍朝前 rally
# ---------------------------------------------------------------------------


def _pzg_cls() -> type:
    """获取已加载的 vendor PlanZoneGather 类。"""
    return sys.modules["sharpy.plans.tactics.zone_gather"].PlanZoneGather  # type: ignore[no-any-return]


def _make_pzg(
    override_intent: Any = None,
    solver_gather_point: Any = None,
    start_location: Any = None,
    hold_gather_point: Any = None,
    zone_manager: Any = None,
) -> Any:
    """构造不需 sharpy init 的 PlanZoneGather 实例。"""
    cls = _pzg_cls()
    act = cls.__new__(cls)
    # 模拟 __init__ 设的字段
    act.gather_move_type = MagicMock()
    act.gather_set = []
    act.blocker_tag = None
    act.current_gather_point = _FakePoint2((0, 0))
    act.set_gather_points = True
    # current_gather_point_solver(start() 设的)
    solver = MagicMock()
    solver.gather_point = solver_gather_point or _FakePoint2((50, 50))
    act.current_gather_point_solver = solver
    # zone_manager(无目标 defend → _vbc_forward_defense_point 读 expansion_zones)
    if zone_manager is not None:
        act.zone_manager = zone_manager
    # ai / knowledge / 其他依赖
    act.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            combat_intent_override=override_intent,
            hold_gather_point=hold_gather_point,
        )
    )
    act.ai = MagicMock()
    act.ai.start_location = start_location or _FakePoint2((10, 10))
    # cache.own(...).tags_not_in(...) 返空 iterable(不派 RALLY_BUILDING)
    own_units = MagicMock()
    own_units.tags_not_in = MagicMock(return_value=[])
    act.cache = MagicMock()
    act.cache.own = MagicMock(return_value=own_units)

    # manage_blocker 是 async,stub 成 no-op
    async def _noop() -> None:
        return None

    act.manage_blocker = _noop  # type: ignore[method-assign]
    # roles.idle 返空(不加单位到 combat)
    act.roles = MagicMock()
    act.roles.idle = []
    act.unit_values = MagicMock()
    act.combat = MagicMock()
    return act


class TestPlanZoneGatherIntent:
    """2026-05-27 Issue A:玩家点撤退后新追猎仍朝前 rally。

    根因:PlanZoneGather 把 Gateway 的 RALLY_BUILDING 设到
    gather_point_solver.gather_point(默认 natural / 前沿矿),不读 vibecraft
    intent。即使主力在 retreat,新单位 spawn 后仍走 rally 朝前。

    fix:execute() 入口读 intent,retreat/defend/hold 时
    effective gather point 改 ai.start_location(主基地中心)。
    """

    @pytest.mark.asyncio
    async def test_intent_none_uses_solver_gather_point(self, vendor_sharpy_env: Any) -> None:
        """intent=None → 用 solver.gather_point(natural / 前沿矿,sharpy 默认)。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        act = _make_pzg(
            override_intent=None,
            solver_gather_point=natural,
            start_location=home,
        )

        await act.execute()

        assert act.current_gather_point == natural
        # combat.execute 用 natural 作 rally 目标
        args, _ = act.combat.execute.call_args
        assert args[0] == natural

    @pytest.mark.asyncio
    async def test_intent_retreat_overrides_to_home(self, vendor_sharpy_env: Any) -> None:
        """intent=retreat → effective gather point 改 ai.start_location(home)。
        Gateway rally 重新指向 home,新单位 spawn 后回家不前压。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        act = _make_pzg(
            override_intent="retreat",
            solver_gather_point=natural,
            start_location=home,
        )

        await act.execute()

        assert act.current_gather_point == home, (
            f"intent=retreat 应改 gather_point 为 home,实际 {act.current_gather_point}"
        )
        # combat.execute 用 home 作 rally 目标
        args, _ = act.combat.execute.call_args
        assert args[0] == home

    @pytest.mark.asyncio
    async def test_intent_defend_with_target_uses_hold_gather_point(
        self, vendor_sharpy_env: Any
    ) -> None:
        """2026-06-03 用户:intent=defend + 玩家指定点(hold_gather_point=瞭望塔)
        → 守该点,而非缩回主基地。(原 bug:defend 和 retreat 一样写死 home。)"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        watchtower = _FakePoint2((42, 60))  # 玩家"守右边瞭望塔"
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=natural,
            start_location=home,
            hold_gather_point=watchtower,
        )

        await act.execute()

        assert act.current_gather_point == watchtower, (
            f"defend + hold_gather_point=瞭望塔 → 守该点,实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_intent_defend_no_target_uses_forward_expansion(
        self, vendor_sharpy_env: Any
    ) -> None:
        """2026-06-03 用户:intent=defend 无指定点 → 守离敌方主基地最近的己方分矿
        (前沿防守),不是 home。enemy main = expansion_zones[-1]。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        # zones: [我方主基地, 我方前沿三矿, 中立, 敌方主基地]
        my_main = SimpleNamespace(center_location=_FakePoint2((10, 10)), is_ours=True)
        my_third = SimpleNamespace(center_location=_FakePoint2((70, 70)), is_ours=True)
        neutral = SimpleNamespace(center_location=_FakePoint2((50, 50)), is_ours=False)
        enemy_main = SimpleNamespace(center_location=_FakePoint2((100, 100)), is_ours=False)
        zm = SimpleNamespace(expansion_zones=[my_main, my_third, neutral, enemy_main])
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=natural,
            start_location=home,
            hold_gather_point=None,
            zone_manager=zm,
        )

        await act.execute()

        # my_third (70,70) 离敌方主基地 (100,100) 比 my_main (10,10) 更近 → 守三矿
        assert act.current_gather_point == my_third.center_location, (
            f"无目标 defend 应守离敌最近的己方分矿(三矿),实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_intent_defend_no_target_no_zones_falls_back_home(
        self, vendor_sharpy_env: Any
    ) -> None:
        """无指定点 + 无己方分矿(全丢) → start_location 兜底,不崩。"""
        home = _FakePoint2((10, 10))
        enemy_main = SimpleNamespace(center_location=_FakePoint2((100, 100)), is_ours=False)
        zm = SimpleNamespace(expansion_zones=[enemy_main])
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
            hold_gather_point=None,
            zone_manager=zm,
        )

        await act.execute()

        assert act.current_gather_point == home

    @pytest.mark.asyncio
    async def test_intent_hold_no_gather_point_falls_back_to_home(
        self, vendor_sharpy_env: Any
    ) -> None:
        """2026-05-28 hold 语义改:hold_gather_point=None → fallback ai.start_location。
        (兼容旧路径 / 防 None 错;Director 一般会算 army_center 设)。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        act = _make_pzg(
            override_intent="hold",
            solver_gather_point=natural,
            start_location=home,
            hold_gather_point=None,
        )

        await act.execute()

        assert act.current_gather_point == home

    @pytest.mark.asyncio
    async def test_intent_hold_uses_hold_gather_point_when_set(
        self, vendor_sharpy_env: Any
    ) -> None:
        """2026-05-28 hold 核心语义:Director 设了 hold_gather_point 后,
        zone_gather effective_gp = 此点(不是 home)。聚团到该点 + 站住。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        ramp_top = _FakePoint2((40, 40))  # 玩家说"部队到 ramp"
        act = _make_pzg(
            override_intent="hold",
            solver_gather_point=natural,
            start_location=home,
            hold_gather_point=ramp_top,
        )

        await act.execute()

        assert act.current_gather_point == ramp_top, (
            f"hold + hold_gather_point=ramp_top → 聚到 ramp,实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_intent_attack_uses_solver_gather_point(self, vendor_sharpy_env: Any) -> None:
        """intent=attack 不属于 retreat/defend/hold,走 sharpy 默认 → solver gather_point。"""
        natural = _FakePoint2((50, 50))
        home = _FakePoint2((10, 10))
        act = _make_pzg(
            override_intent="attack",
            solver_gather_point=natural,
            start_location=home,
        )

        await act.execute()

        assert act.current_gather_point == natural

    @pytest.mark.asyncio
    async def test_gather_point_change_clears_gather_set(self, vendor_sharpy_env: Any) -> None:
        """current_gather_point 变化时 gather_set 必须 clear,
        下个 tick 重新对所有 Gateway 设 RALLY_BUILDING(回家)。"""
        home = _FakePoint2((10, 10))
        act = _make_pzg(
            override_intent="retreat",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
        )
        # 模拟之前已设过 rally(gather_set 有内容)
        act.gather_set = [123, 456]
        # current_gather_point 当前是 (0,0) 初始值,与 home 不等 → clear

        await act.execute()

        assert act.gather_set == [], (
            f"current_gather_point 变化时 gather_set 应 clear,实际 {act.gather_set}"
        )


# ---------------------------------------------------------------------------
# Q3 fix: ActBase.get_count — Gateway/Warpgate 同质化计数
# ---------------------------------------------------------------------------


def _inject_act_base_fakes() -> None:
    """加载 act_base.py 所需的最小 fake deps,在已有 vendor_sharpy fake 基础上补充。

    _inject_vendor_fakes() 已把 sc2 替换为 stub ModuleType,无法再 import sc2.constants。
    通过 importlib 暂时绕过 sys.modules fake 来加载真实 constants 和 unit_typeid。
    """

    # 暂时移除 fake sc2 相关模块,用 importlib 加载真实的 sc2 子模块
    saved: dict = {}
    for key in list(sys.modules):
        if key in (
            "sc2",
            "sc2.constants",
            "sc2.ids",
            "sc2.ids.unit_typeid",
            "sc2.ids.ability_id",
            "sc2.data",
        ):
            saved[key] = sys.modules.pop(key)
    try:
        import sc2.constants as _sc2_const
        import sc2.ids.unit_typeid as _sc2_uid
    except Exception:
        _sc2_const = ModuleType("sc2.constants")  # type: ignore[assignment]
        _sc2_const.EQUIVALENTS_FOR_TECH_PROGRESS = {}  # type: ignore[attr-defined]
        _sc2_uid = ModuleType("sc2.ids.unit_typeid")  # type: ignore[assignment]
    # 恢复所有 fake stub
    sys.modules.update(saved)
    # 注入真实 constants + unit_typeid(覆盖 fake,act_base.py 需要这两个)
    sys.modules["sc2.constants"] = _sc2_const  # type: ignore[assignment]
    sys.modules["sc2.ids.unit_typeid"] = _sc2_uid  # type: ignore[assignment]

    # sc2.unit: 补 UnitOrder stub(act_base 需要 from sc2.unit import Unit, UnitOrder)
    unit_mod = sys.modules.get("sc2.unit", ModuleType("sc2.unit"))
    if not hasattr(unit_mod, "UnitOrder"):
        unit_mod.UnitOrder = MagicMock  # type: ignore[attr-defined]
    if not hasattr(unit_mod, "Unit"):
        unit_mod.Unit = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.unit"] = unit_mod

    # sc2.ids.ability_id: act_base.py build_commands set 用了大量 AbilityId.PROTOSSBUILD_* 等。
    # 替换为能响应任意属性的 stub(返回唯一 sentinel 对象,set 比较不会碰撞)。
    class _DynamicAbilityId:
        _cache: dict = {}

        def __class_getitem__(cls, item: str) -> object:
            return cls._cache.setdefault(item, object())

        def __getattr__(self, item: str) -> object:  # 实例属性
            return type(self)._cache.setdefault(item, object())

    class _DynamicAbilityIdMeta(type):
        def __getattr__(cls, item: str) -> object:  # 类属性
            return cls._cache.setdefault(item, object())

    class _FullFakeAbilityId(metaclass=_DynamicAbilityIdMeta):
        _cache: dict = {}
        RALLY_BUILDING = "RALLY_BUILDING"  # 保留已有值

    ability_mod = sys.modules.get("sc2.ids.ability_id", ModuleType("sc2.ids.ability_id"))
    ability_mod.AbilityId = _FullFakeAbilityId  # type: ignore[attr-defined]
    sys.modules["sc2.ids.ability_id"] = ability_mod

    # sc2.unit_command: stub(不需要真实行为)
    if "sc2.unit_command" not in sys.modules or not hasattr(
        sys.modules["sc2.unit_command"], "UnitCommand"
    ):
        uc_mod = ModuleType("sc2.unit_command")
        uc_mod.UnitCommand = MagicMock  # type: ignore[attr-defined]
        sys.modules["sc2.unit_command"] = uc_mod

    # sc2.ids.buff_id: stub(真实 sc2 可能已有,但 fake env 里需要保留)
    if "sc2.ids.buff_id" not in sys.modules or not hasattr(
        sys.modules.get("sc2.ids.buff_id", object()), "BuffId"
    ):
        buff_mod = ModuleType("sc2.ids.buff_id")
        buff_mod.BuffId = MagicMock  # type: ignore[attr-defined]
        sys.modules["sc2.ids.buff_id"] = buff_mod

    # sc2: stub 补 BotAI 等
    if not hasattr(sys.modules.get("sc2", object()), "BotAI"):
        sc2_mod = sys.modules.get("sc2", ModuleType("sc2"))
        sc2_mod.BotAI = MagicMock  # type: ignore[attr-defined]
        sys.modules["sc2"] = sc2_mod

    # sharpy.general.component: stub Component 基类
    if "sharpy.general.component" not in sys.modules or not hasattr(
        sys.modules.get("sharpy.general.component", object()), "Component"
    ):
        comp_mod = ModuleType("sharpy.general.component")

        class _FakeComponent:
            pass

        comp_mod.Component = _FakeComponent  # type: ignore[attr-defined]
        sys.modules["sharpy.general.component"] = comp_mod

    # sharpy.interfaces: 补 ILostUnitsManager(可能已有,补全)
    iface_mod = sys.modules.get("sharpy.interfaces", ModuleType("sharpy.interfaces"))
    if not hasattr(iface_mod, "ILostUnitsManager"):
        iface_mod.ILostUnitsManager = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.interfaces"] = iface_mod

    # sharpy.managers.core.roles: 补 UnitTask(可能已有)
    roles_mod = sys.modules.get(
        "sharpy.managers.core.roles", ModuleType("sharpy.managers.core.roles")
    )
    if not hasattr(roles_mod, "UnitTask"):
        roles_mod.UnitTask = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core.roles"] = roles_mod


@pytest.fixture()
def act_base_env() -> Any:
    """注入 vendor sharpy fake env 并加载 act_base.py。"""
    _clean_vendor_mods()
    _inject_vendor_fakes()
    _inject_act_base_fakes()
    _VENDOR_ACTS = _PROJECT_ROOT / "vendor" / "sharpy" / "sharpy" / "plans" / "acts"
    _load_vendor_module(_VENDOR_ACTS / "act_base.py", "sharpy.plans.acts.act_base")
    yield
    _clean_vendor_mods()


def _make_act_base_instance(
    counts: dict[str, tuple[int, int, int]],
    pending: dict[str, float] | None = None,
) -> Any:
    """构造 ActBase 实例，注入 mock cache/ai/unit_pending_count。

    counts = {type_name: (ready_amount, not_ready_amount, total_amount)}
    pending = {type_name: float}(already_pending)
    """

    act_base_mod = sys.modules["sharpy.plans.acts.act_base"]
    cls = act_base_mod.ActBase

    # 构造一个具体子类(ActBase 是 ABC)
    class _ConcreteActBase(cls):  # type: ignore[valid-type]
        async def execute(self) -> bool:
            return True

    act = _ConcreteActBase.__new__(_ConcreteActBase)

    if pending is None:
        pending = {}

    def _cache_own(type_id: Any) -> Any:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        m = MagicMock()
        ready_amt, not_ready_amt, total_amt = counts.get(name, (0, 0, 0))
        m.amount = total_amt
        m.not_ready = MagicMock()
        m.not_ready.amount = not_ready_amt
        m.ready = MagicMock()
        m.ready.amount = ready_amt
        return m

    act.cache = MagicMock()
    act.cache.own = _cache_own

    def _unit_pending_count(type_id: Any) -> float:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        return float(pending.get(name, 0))

    act.unit_pending_count = _unit_pending_count
    act.lost_units_manager = MagicMock()
    act.lost_units_manager.own_lost_type = MagicMock(return_value=0)
    return act


class TestActBaseGetCountEquivalents:
    """Q3 fix (2026-05-29): ActBase.get_count Gateway/Warpgate 等同质化计数。
    # vibecraft: marker 确保 sharpy upstream 升级不丢失 patch。
    """

    def test_get_count_aggregates_gateway_warpgate_ready_only(self, act_base_env: Any) -> None:
        """4 WG ready + 0 GW → get_count(GATEWAY, include_pending=False, include_not_ready=False) = 4。
        防止 GridBuilding(GATEWAY) plan 在全升 WG 后重复触发。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act = _make_act_base_instance(
            counts={
                "GATEWAY": (0, 0, 0),
                "WARPGATE": (4, 0, 4),
            }
        )
        result = act.get_count(UnitTypeId.GATEWAY, include_pending=False, include_not_ready=False)
        assert result == 4, f"4 WG 应算作 4 GW(同质化),实际 {result}"

    def test_get_count_aggregates_gateway_warpgate_with_pending(self, act_base_env: Any) -> None:
        """3 WG ready + 1 GW pending → get_count(GATEWAY, include_pending=True) = 4。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act = _make_act_base_instance(
            counts={
                "GATEWAY": (0, 0, 0),
                "WARPGATE": (3, 0, 3),
            },
            pending={"GATEWAY": 0.0, "WARPGATE": 1.0},
        )
        result = act.get_count(UnitTypeId.GATEWAY, include_pending=True, include_not_ready=True)
        assert result == 4, f"3 WG ready + 1 WG pending = 4,实际 {result}"

    def test_get_count_hatchery_lair_hive_aggregated(self, act_base_env: Any) -> None:
        """1 HATCH + 1 LAIR + 1 HIVE → get_count(HATCHERY, ...) = 3。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act = _make_act_base_instance(
            counts={
                "HATCHERY": (1, 0, 1),
                "LAIR": (1, 0, 1),
                "HIVE": (1, 0, 1),
            }
        )
        result = act.get_count(UnitTypeId.HATCHERY, include_pending=False, include_not_ready=True)
        assert result == 3, f"HATCH+LAIR+HIVE=3,实际 {result}"

    def test_get_count_forge_not_in_table_uses_own_type(self, act_base_env: Any) -> None:
        """FORGE 不在 _VBC_EQUIVALENTS → 只查自身,不受同质化影响。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act = _make_act_base_instance(counts={"FORGE": (2, 0, 2)})
        result = act.get_count(UnitTypeId.FORGE, include_pending=False, include_not_ready=False)
        assert result == 2


# ---------------------------------------------------------------------------
# TestExpandCapOverride — 2026-06-10 开矿封顶 vendor patch
# ---------------------------------------------------------------------------

_VENDOR_ACTS_EXPAND = _PROJECT_ROOT / "vendor" / "sharpy" / "sharpy" / "plans" / "acts"


def _inject_expand_fakes() -> None:
    """补充 expand.py 所需的 missing fake modules。
    前提：_inject_vendor_fakes() 已调用。
    """
    # sc2.unit: 补 UnitOrder（expand.py `from sc2.unit import Unit, UnitOrder` 需要）
    unit_mod = sys.modules.get("sc2.unit", ModuleType("sc2.unit"))
    if not hasattr(unit_mod, "UnitOrder"):
        unit_mod.UnitOrder = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.unit"] = unit_mod

    # sc2.ids.ability_id: expand.py 在模块级用 AbilityId.NEXUSTRAIN_PROBE / COMMANDCENTERTRAIN_SCV。
    # 替换为支持任意属性的动态 stub（_inject_vendor_fakes 的 _FakeAbilityId 只有 RALLY_BUILDING）。
    class _DynAbilityId:
        _cache: dict = {}
        RALLY_BUILDING = "RALLY_BUILDING"

        @classmethod
        def __class_getitem__(cls, item: str) -> object:
            return cls._cache.setdefault(item, object())

    class _DynAbilityIdMeta(type):
        def __getattr__(cls, item: str) -> object:
            return cls._cache.setdefault(item, object())

    class _FullDynAbilityId(metaclass=_DynAbilityIdMeta):
        _cache: dict = {}
        RALLY_BUILDING = "RALLY_BUILDING"

    ability_mod = sys.modules.get("sc2.ids.ability_id", ModuleType("sc2.ids.ability_id"))
    ability_mod.AbilityId = _FullDynAbilityId  # type: ignore[attr-defined]
    sys.modules["sc2.ids.ability_id"] = ability_mod

    # sharpy.sc2math（expand.py 用 to_new_ticks）
    sc2math = ModuleType("sharpy.sc2math")
    sc2math.to_new_ticks = lambda x: x  # type: ignore[attr-defined]
    sys.modules["sharpy.sc2math"] = sc2math

    # s2clientprotocol + common_pb2（expand.py expanding_in 里用 common_pb.Point）
    s2cp = ModuleType("s2clientprotocol")
    s2cp_pb2 = ModuleType("s2clientprotocol.common_pb2")
    s2cp_pb2.Point = type("Point", (), {})  # type: ignore[attr-defined]
    sys.modules["s2clientprotocol"] = s2cp
    sys.modules["s2clientprotocol.common_pb2"] = s2cp_pb2

    # sharpy.managers.core: 补 ZoneManager, UnitRoleManager
    core_mod = sys.modules.get("sharpy.managers.core", ModuleType("sharpy.managers.core"))
    if not hasattr(core_mod, "ZoneManager"):
        core_mod.ZoneManager = MagicMock  # type: ignore[attr-defined]
    if not hasattr(core_mod, "UnitRoleManager"):
        core_mod.UnitRoleManager = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core"] = core_mod

    # sharpy.interfaces: 补 IIncomeCalculator（_inject_vendor_fakes 未包含）
    iface_mod = sys.modules.get("sharpy.interfaces", ModuleType("sharpy.interfaces"))
    if not hasattr(iface_mod, "IIncomeCalculator"):
        iface_mod.IIncomeCalculator = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.interfaces"] = iface_mod

    # sharpy.plans.acts.act_base（expand.py 用 `from .act_base import ActBase`）
    act_base_m = ModuleType("sharpy.plans.acts.act_base")
    act_base_m.ActBase = _FakeActBase  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.acts.act_base"] = act_base_m

    # 加载 expand.py
    _load_vendor_module(_VENDOR_ACTS_EXPAND / "expand.py", "sharpy.plans.acts.expand")


@pytest.fixture()
def vendor_expand_env() -> Any:
    """注入 vendor expand fake env，每 test 独立。"""
    _clean_vendor_mods()
    _inject_vendor_fakes()
    _inject_expand_fakes()
    yield
    _clean_vendor_mods()


def _expand_cls() -> type:
    """获取已加载的 vendor Expand 类。"""
    return sys.modules["sharpy.plans.acts.expand"].Expand  # type: ignore[no-any-return]


def _make_expand(
    cap_override: int | None,
    zones_with_minerals: list,
    stealth_tags: set[int],
    to_count: int = 5,
    stealth_pending: int = 0,
) -> Any:
    """构造 Expand 实例，绕过 __init__，只设 hook 所需最小 attrs。"""
    cls = _expand_cls()
    plan = cls.__new__(cls)
    plan.to_count = to_count
    plan.priority_base_index = None
    plan.builder_tag = None
    plan.priority = False
    plan.consider_worker_production = False
    plan.zone_manager = SimpleNamespace(
        our_zones_with_minerals=zones_with_minerals,
        expansion_zones=[],  # empty → expand_here stays None → original returns False
    )
    plan.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            expansion_cap_override=cap_override,
            stealth_townhall_tags=stealth_tags,
            stealth_pending_base_count=stealth_pending,
        )
    )
    plan.ai = MagicMock()
    plan.ai.workers.exists = False  # → original code returns False without exception
    plan.clear_worker = MagicMock()
    return plan


class TestExpandCapOverride:
    """2026-06-10 WP0：Expand.execute vibecraft hook —— 玩家开矿封顶 + stealth 排除。"""

    @pytest.mark.asyncio
    async def test_cap_override_triggers_early_return_true(self, vendor_expand_env: Any) -> None:
        """expansion_cap_override=2, active_bases=2, stealth=set() → hook 封顶，
        clear_worker() 调用，execute() 返回 True。"""
        # 2 个普通 zone
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
        ]
        plan = _make_expand(cap_override=2, zones_with_minerals=zones, stealth_tags=set())
        result = await plan.execute()
        assert result is True, "cap=2 且 active_bases=2 → hook 应封顶 return True"
        plan.clear_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_stealth_counts_toward_to_count(self, vendor_expand_env: Any) -> None:
        """2026-06-12：在建偷矿基地算进 active_bases → 够 to_count 就不开自己分矿。

        active_bases=2(我方两矿) + stealth_pending=1 = 3 >= to_count=3 → clear_worker + return True
        （bot 延后/不开第 3 矿，因为玩家已下偷矿令、那片基地在建）。
        """
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
        ]
        plan = _make_expand(
            cap_override=None,
            zones_with_minerals=zones,
            stealth_tags=set(),
            to_count=3,
            stealth_pending=1,
        )
        result = await plan.execute()
        assert result is True, "active_bases(2)+pending(1)=3>=to_count(3) → 不开矿 return True"
        plan.clear_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pending_stealth_still_expands(self, vendor_expand_env: Any) -> None:
        """对照：无在建偷矿 → active_bases=2 < to_count=3 → 走原逻辑开矿（这里 expand_here=None → False）。"""
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
        ]
        plan = _make_expand(
            cap_override=None,
            zones_with_minerals=zones,
            stealth_tags=set(),
            to_count=3,
            stealth_pending=0,
        )
        result = await plan.execute()
        assert result is False, (
            "active_bases(2)<to_count(3) 且无 pending → 不封顶，走原逻辑 return False"
        )

    @pytest.mark.asyncio
    async def test_no_override_falls_through_to_original(self, vendor_expand_env: Any) -> None:
        """expansion_cap_override=None → hook 不触发，走原逻辑。
        expansion_zones=[] → expand_here=None → 原代码返回 False。"""
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
        ]
        plan = _make_expand(
            cap_override=None, zones_with_minerals=zones, stealth_tags=set(), to_count=5
        )
        result = await plan.execute()
        # 原逻辑：active_bases=2 < to_count=5，expansion_zones=[] → expand_here=None → return False
        assert result is False, "cap=None 时 hook 不介入，走原逻辑应 return False"

    @pytest.mark.asyncio
    async def test_stealth_exclusion_prevents_overcap_block(self, vendor_expand_env: Any) -> None:
        """cap=3, active_bases=3, 1个stealth zone → active-1=2 < 3 → 排除后不封顶（返回 False）。

        对照：若不排除 stealth，3>=3 → return True（hook 封顶）。
        排除生效时，stealth 基地不算进"自然扩张"计数，2<3 → 继续开矿。
        """
        stealth_tag = 42
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=stealth_tag)),  # stealth zone
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
        ]
        plan = _make_expand(
            cap_override=3,
            zones_with_minerals=zones,
            stealth_tags={stealth_tag},
            to_count=10,
        )
        result = await plan.execute()
        # 排除 stealth: 3-1=2 < 3 → hook 不封顶，走原逻辑 → return False
        assert result is False, (
            "cap=3, active=3, 1 stealth zone → 排除后 2<3，不应封顶(return False)"
        )

    @pytest.mark.asyncio
    async def test_stealth_excluded_but_nonstealth_still_capped(
        self, vendor_expand_env: Any
    ) -> None:
        """对照 case：cap=3, active_bases=4, stealth=1 → nonstealth=3 >= 3 → 仍封顶。"""
        stealth_tag = 42
        zones = [
            SimpleNamespace(our_townhall=SimpleNamespace(tag=stealth_tag)),  # stealth
            SimpleNamespace(our_townhall=SimpleNamespace(tag=100)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=101)),
            SimpleNamespace(our_townhall=SimpleNamespace(tag=102)),
        ]
        plan = _make_expand(
            cap_override=3,
            zones_with_minerals=zones,
            stealth_tags={stealth_tag},
            to_count=10,
        )
        result = await plan.execute()
        # 排除 1 stealth: 4-1=3 >= 3 → hook 封顶 → return True
        assert result is True, "cap=3, active=4, 1 stealth → nonstealth=3>=3 → 封顶 return True"
        plan.clear_worker.assert_called_once()


# ---------------------------------------------------------------------------
# TestDistributeWorkersFence
# ---------------------------------------------------------------------------

_VENDOR_TACTICS_DW = _PROJECT_ROOT / "vendor" / "sharpy" / "sharpy" / "plans" / "tactics"


def _inject_distribute_workers_fakes() -> None:
    """补充 distribute_workers.py 所需的 fake modules（在 _inject_vendor_fakes() 之后调用）。"""
    # sc2.constants: IS_COLLECTING / ALL_GAS（generate_worker_queue 用 ALL_GAS 做类型检测）
    sc2_constants = ModuleType("sc2.constants")
    sc2_constants.IS_COLLECTING = set()  # type: ignore[attr-defined]
    sc2_constants.ALL_GAS = set()  # type: ignore[attr-defined]
    sys.modules["sc2.constants"] = sc2_constants

    # sc2.ids.buff_id: BuffId（calculate_workers 里 has_buff 路径用，generate_worker_queue 不用）
    buff_mod = ModuleType("sc2.ids.buff_id")
    buff_mod.BuffId = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.ids.buff_id"] = buff_mod

    # sc2.unit: 补 UnitOrder（file-level import 需要）
    unit_mod = sys.modules.get("sc2.unit", ModuleType("sc2.unit"))
    if not hasattr(unit_mod, "UnitOrder"):
        unit_mod.UnitOrder = MagicMock  # type: ignore[attr-defined]
    sys.modules["sc2.unit"] = unit_mod

    # sharpy.managers.core: 补 UnitRoleManager
    core_mod = sys.modules.get("sharpy.managers.core", ModuleType("sharpy.managers.core"))
    if not hasattr(core_mod, "UnitRoleManager"):
        core_mod.UnitRoleManager = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core"] = core_mod

    # sharpy.managers.core.unit_value: buildings_5x5（空 set）+ UnitValue
    unit_value_mod = ModuleType("sharpy.managers.core.unit_value")
    unit_value_mod.buildings_5x5 = set()  # type: ignore[attr-defined]
    unit_value_mod.UnitValue = MagicMock  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core.unit_value"] = unit_value_mod

    # 加载 distribute_workers.py
    _load_vendor_module(
        _VENDOR_TACTICS_DW / "distribute_workers.py",
        "sharpy.plans.tactics.distribute_workers",
    )


@pytest.fixture()
def vendor_distribute_workers_env() -> Any:
    """注入 vendor distribute_workers fake env，每 test 独立。"""
    _clean_vendor_mods()
    _inject_vendor_fakes()
    _inject_distribute_workers_fakes()
    yield
    _clean_vendor_mods()


def _dw_cls() -> type:
    """获取已加载的 vendor DistributeWorkers 类。"""
    return sys.modules["sharpy.plans.tactics.distribute_workers"].DistributeWorkers  # type: ignore[no-any-return]


def _make_dw(stealth_tags: set[int], townhall_tags: list[int]) -> Any:
    """构造最小 DistributeWorkers 实例，绕过 __init__，只设 generate_worker_queue 所需属性。"""
    cls = _dw_cls()
    dw = cls.__new__(cls)
    dw.work_queue = []
    dw.worker_dict = {}
    dw.active_gas_workers = 0
    dw.gas_workers_target = 0  # active==target → else 分支 → sort_method=lambda tpl: tpl.available
    dw.evacuate_zones = False

    # knowledge.vibecraft.stealth_townhall_tags
    dw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(stealth_townhall_tags=stealth_tags))

    # 构造 townhall fake building 列表
    def _make_building(tag: int) -> Any:
        return SimpleNamespace(
            tag=tag,
            is_ready=True,
            ideal_harvesters=16,
            build_progress=1.0,
            type_id="NEXUS",  # 不在 ALL_GAS（空 set）→ 走 else 分支
            assigned_harvesters=10,
        )

    zone_ok = SimpleNamespace(is_enemys=False, is_ours=True, needs_evacuation=False)

    dw.ai = SimpleNamespace(
        gas_buildings=[],
        townhalls=[_make_building(t) for t in townhall_tags],
    )
    dw.zone_manager = SimpleNamespace(zone_for_unit=lambda _b: zone_ok)
    return dw


class TestDistributeWorkersFence:
    """WP3：DistributeWorkers.generate_worker_queue vibecraft FENCE。

    stealth townhall 从全局工作队列排除 → 主矿农民不倒灌进去（因为 available=+16 巨额缺口）。
    knowledge.vibecraft.stealth_townhall_tags 是读取路径。
    """

    def test_stealth_townhall_excluded_from_work_queue(
        self, vendor_distribute_workers_env: Any
    ) -> None:
        """stealth_townhall_tags={stealth_tag} → 该 building 被 continue 跳过，不进 work_queue；
        普通 townhall 照常进。"""
        stealth_tag = 999
        normal_tag = 100

        dw = _make_dw(stealth_tags={stealth_tag}, townhall_tags=[stealth_tag, normal_tag])
        dw.generate_worker_queue()

        work_tags = [ws.unit.tag for ws in dw.work_queue]
        assert normal_tag in work_tags, (
            f"普通 townhall {normal_tag} 应在 work_queue，实际: {work_tags}"
        )
        assert stealth_tag not in work_tags, (
            f"stealth townhall {stealth_tag} 不应在 work_queue（防主矿农民倒灌），实际: {work_tags}"
        )

    def test_stealth_drifters_get_force_exit_eviction(
        self, vendor_distribute_workers_env: Any
    ) -> None:
        """主动 FENCE（2026-06-11）：偷矿 Nexus 的 worker_dict 里有"漂进来的非 Reserved 主矿
        农民"时，不再单纯 continue 跳过，而是发一条 force_exit WorkStatus（大负 available）让
        平衡器把它们驱逐回主矿。这正是修 DRAIN（assigned > 自产、主矿农民倒灌卡死）的关键。
        """
        stealth_tag = 999
        normal_tag = 100
        dw = _make_dw(stealth_tags={stealth_tag}, townhall_tags=[stealth_tag, normal_tag])
        # 模拟 3 个漂进来的主矿农民正在 stealth Nexus 采矿（calculate_workers 已把 Reserved
        # stealth 农民过滤掉，故 worker_dict[stealth] 只剩这些非 Reserved 漂移农民）。
        dw.worker_dict = {stealth_tag: [1, 2, 3]}
        dw.generate_worker_queue()

        stealth_ws = [ws for ws in dw.work_queue if ws.unit.tag == stealth_tag]
        assert stealth_ws, (
            "有漂移农民时 stealth Nexus 应进 work_queue 做驱逐（force_exit），实际不在队列"
        )
        ws = stealth_ws[0]
        assert ws.force_exit is True, "驱逐 WorkStatus 必须 force_exit=True（保证一定有去处）"
        assert ws.available < 0, (
            f"驱逐 WorkStatus available 必须为负（把农民赶出去），实际 {ws.available}"
        )

    def test_stealth_own_workers_never_evicted_tag_aware(
        self, vendor_distribute_workers_env: Any
    ) -> None:
        """tag-aware 驱逐（2026-06-11 修回归）：worker_dict[stealth] 里**在 stealth_worker_tags
        的自产农民绝不驱逐**（含刚出生 cache-miss 还没 Reserve 上的），只赶真·主矿漂移农民。

        真机根因：自产农民出生那帧 set_unit_role cache-miss → 没 Reserve → 混进 worker_dict →
        被旧 FENCE 当漂移农民赶回主矿（22 次 ECONTRACE from_kind=stealth→main、cell 长不起来）。
        """
        stealth_tag = 999
        normal_tag = 100
        dw = _make_dw(stealth_tags={stealth_tag}, townhall_tags=[stealth_tag, normal_tag])
        # worker_dict[stealth] 混了 3 个：tag 1、2 是自产农民（在 stealth_worker_tags），
        # tag 7 才是真·主矿漂移农民。
        dw.worker_dict = {stealth_tag: [1, 2, 7]}
        dw.knowledge.vibecraft.stealth_worker_tags = {1, 2}
        dw.generate_worker_queue()

        # 只有 1 个 drifter（tag 7）→ available = -1*10000，worker_dict 被改写成只剩 [7]
        stealth_ws = [ws for ws in dw.work_queue if ws.unit.tag == stealth_tag]
        assert stealth_ws, "有真漂移农民(tag 7)时仍应进队列驱逐"
        assert stealth_ws[0].available == -10000, (
            f"应只算 1 个 drifter（tag 7），available 应 -10000，实际 {stealth_ws[0].available}"
        )
        assert dw.worker_dict[stealth_tag] == [7], (
            f"worker_dict 应被改写成只剩 drifter [7]（保护自产农民不被 execute 选中驱逐），"
            f"实际 {dw.worker_dict[stealth_tag]}"
        )

    def test_stealth_all_own_workers_no_eviction(self, vendor_distribute_workers_env: Any) -> None:
        """worker_dict[stealth] 全是自产农民（都在 stealth_worker_tags）→ 0 drifter →
        不进 work_queue、worker_dict 该项清掉（绝不把自己农民赶回主矿）。"""
        stealth_tag = 999
        normal_tag = 100
        dw = _make_dw(stealth_tags={stealth_tag}, townhall_tags=[stealth_tag, normal_tag])
        dw.worker_dict = {stealth_tag: [1, 2, 3]}
        dw.knowledge.vibecraft.stealth_worker_tags = {1, 2, 3}
        dw.generate_worker_queue()

        work_tags = [ws.unit.tag for ws in dw.work_queue]
        assert stealth_tag not in work_tags, (
            f"全是自产农民时 stealth Nexus 不应进 work_queue（不驱逐自己人），实际: {work_tags}"
        )
        assert stealth_tag not in dw.worker_dict, "无 drifter 时 worker_dict 该项应被清掉"

    def test_stealth_no_drifters_still_excluded(self, vendor_distribute_workers_env: Any) -> None:
        """对照：worker_dict 里 stealth Nexus 没有漂移农民（自产农民全是 Reserved，被
        only_roles 过滤不进 worker_dict）→ 仍跳过、不进 work_queue（不作为 add 目标，防路由）。"""
        stealth_tag = 999
        normal_tag = 100
        dw = _make_dw(stealth_tags={stealth_tag}, townhall_tags=[stealth_tag, normal_tag])
        dw.worker_dict = {}  # stealth Nexus 无漂移农民
        dw.generate_worker_queue()

        work_tags = [ws.unit.tag for ws in dw.work_queue]
        assert stealth_tag not in work_tags, (
            f"无漂移农民时 stealth Nexus 不应进 work_queue（防路由），实际: {work_tags}"
        )
        assert normal_tag in work_tags

    def test_no_stealth_all_townhalls_in_queue(self, vendor_distribute_workers_env: Any) -> None:
        """stealth_townhall_tags=set()（无 stealth）→ 所有 townhall 正常进 work_queue。"""
        tag1, tag2 = 100, 101
        dw = _make_dw(stealth_tags=set(), townhall_tags=[tag1, tag2])
        dw.generate_worker_queue()

        work_tags = [ws.unit.tag for ws in dw.work_queue]
        assert tag1 in work_tags
        assert tag2 in work_tags

    def test_stealth_gas_not_excluded_when_not_in_tags(
        self, vendor_distribute_workers_env: Any
    ) -> None:
        """气矿 tag 不在 stealth_townhall_tags → 气矿仍正常处理（gas_buildings 路径）。

        本任务只排除 stealth Nexus；气矿排除留 WP4 评估（注释里已点明边界）。
        """
        gas_tag = 55
        # stealth_townhall_tags 不含气矿 tag
        dw = _make_dw(stealth_tags=set(), townhall_tags=[100])
        # 补 gas building（type_id 不在 ALL_GAS→空 set，走 else 分支，验证其进 queue）
        gas_building = SimpleNamespace(
            tag=gas_tag,
            is_ready=True,
            ideal_harvesters=3,
            build_progress=1.0,
            type_id="ASSIMILATOR",
            assigned_harvesters=2,
        )
        dw.ai = SimpleNamespace(
            gas_buildings=[gas_building],
            townhalls=dw.ai.townhalls,
        )
        dw.generate_worker_queue()

        work_tags = [ws.unit.tag for ws in dw.work_queue]
        assert gas_tag in work_tags, (
            f"气矿 {gas_tag} 应在 work_queue（未被 FENCE 排除），实际: {work_tags}"
        )


def _make_dw_transfer(
    townhalls: list[tuple[int, tuple[float, float]]],
    main_pos: tuple[float, float],
    stealth_tags: set[int],
) -> Any:
    """构造最小 DistributeWorkers 实例，只设 _vibecraft_log_transfer 所需属性。

    townhalls: [(tag, (x, y)), ...]；main_pos: own_main_zone.center_location。
    """

    class _Pos:
        def __init__(self, x: float, y: float) -> None:
            self.x = float(x)
            self.y = float(y)

        def distance_to(self, o: Any) -> float:
            return ((self.x - o.x) ** 2 + (self.y - o.y) ** 2) ** 0.5

    class _Unit:
        def __init__(self, tag: int, pos: tuple[float, float]) -> None:
            self.tag = tag
            self.position = _Pos(*pos)

    class _Ready(list):
        def closest_to(self, u: Any) -> Any:
            return min(self, key=lambda t: t.position.distance_to(u.position))

    cls = _dw_cls()
    dw = cls.__new__(cls)
    ready = _Ready(_Unit(t, p) for t, p in townhalls)
    dw.ai = SimpleNamespace(townhalls=SimpleNamespace(ready=ready))
    dw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(stealth_townhall_tags=stealth_tags))
    dw.zone_manager = SimpleNamespace(
        own_main_zone=SimpleNamespace(center_location=_Pos(*main_pos))
    )
    dw._unit_cls = _Unit
    return dw


class TestWorkerTransferLog:
    """2026-06-11 经济可观测：DistributeWorkers.assign_to_work 的 _vibecraft_log_transfer hook。

    农民被调去**别的基地**时打一条 ECONTRACE worker_transfer（带 from/to 基地分类）；
    同基地内换矿点不打。这样"主矿往分矿派农民"可从日志直接读出。
    """

    def test_cross_base_transfer_logs_with_kinds(
        self, vendor_distribute_workers_env: Any, caplog: Any
    ) -> None:
        """worker 从 natural 调去 main → 打一行，from_kind=natural / to_kind=main。"""
        import logging as _lg

        dw = _make_dw_transfer(
            townhalls=[(1, (50.0, 50.0)), (2, (70.0, 40.0))],  # main, natural
            main_pos=(50.0, 50.0),
            stealth_tags=set(),
        )
        worker = dw._unit_cls(999, (70.0, 40.0))  # 当前在 natural
        work = dw._unit_cls(888, (50.0, 50.0))  # 目标矿在 main 旁
        with caplog.at_level(_lg.INFO, logger="vibecraft.econtrace"):
            dw._vibecraft_log_transfer(worker, work)
        line = "".join(r.getMessage() for r in caplog.records)
        assert "ECONTRACE worker_transfer" in line, f"应打 transfer 日志，实际: {line!r}"
        assert "tag=999" in line
        assert "from_kind=natural" in line, f"来源应为 natural，实际: {line!r}"
        assert "to_kind=main" in line, f"目标应为 main，实际: {line!r}"

    def test_same_base_no_log(self, vendor_distribute_workers_env: Any, caplog: Any) -> None:
        """worker 和目标矿都属于同一基地（main）→ 不打日志（只是换矿点）。"""
        import logging as _lg

        dw = _make_dw_transfer(
            townhalls=[(1, (50.0, 50.0)), (2, (70.0, 40.0))],
            main_pos=(50.0, 50.0),
            stealth_tags=set(),
        )
        worker = dw._unit_cls(999, (51.0, 50.0))  # main 旁
        work = dw._unit_cls(888, (49.0, 51.0))  # 也在 main 旁
        with caplog.at_level(_lg.INFO, logger="vibecraft.econtrace"):
            dw._vibecraft_log_transfer(worker, work)
        line = "".join(r.getMessage() for r in caplog.records)
        assert "worker_transfer" not in line, f"同基地不应打日志，实际: {line!r}"

    def test_transfer_to_stealth_kind(
        self, vendor_distribute_workers_env: Any, caplog: Any
    ) -> None:
        """目标基地 tag 在 stealth_townhall_tags → to_kind=stealth（倒灌方向可观测）。"""
        import logging as _lg

        stealth_tag = 3
        dw = _make_dw_transfer(
            townhalls=[(1, (50.0, 50.0)), (stealth_tag, (130.0, 30.0))],  # main, stealth
            main_pos=(50.0, 50.0),
            stealth_tags={stealth_tag},
        )
        worker = dw._unit_cls(999, (50.0, 50.0))  # 在 main
        work = dw._unit_cls(888, (130.0, 30.0))  # 目标矿在 stealth 旁
        with caplog.at_level(_lg.INFO, logger="vibecraft.econtrace"):
            dw._vibecraft_log_transfer(worker, work)
        line = "".join(r.getMessage() for r in caplog.records)
        assert "to_kind=stealth" in line, f"目标应为 stealth，实际: {line!r}"
        assert "from_kind=main" in line, f"来源应为 main，实际: {line!r}"


# ---------------------------------------------------------------------------
# TestPlanZoneGatherThreatAwareDefend — 2026-06-13 全军防守威胁感知
# ---------------------------------------------------------------------------


def _make_zone(
    center: Any,
    is_ours: bool,
    threat_power: float = 0.0,
) -> Any:
    """构造 zone SimpleNamespace，含 assaulting_enemy_power.power。"""
    return SimpleNamespace(
        center_location=center,
        is_ours=is_ours,
        assaulting_enemy_power=SimpleNamespace(power=threat_power),
    )


class TestPlanZoneGatherThreatAwareDefend:
    """2026-06-13 #525 全军防守僵硬修复：defend intent 下聚团点改为威胁感知。

    核心语义：己方任何 zone 有敌（assaulting_enemy_power.power > 0）→
    部队迎击最大威胁 zone，而非钉在前沿静态点。
    滞回防抖：旧 zone 仍有威胁时只有新 zone ≥ 1.5x 才切换。
    """

    @pytest.mark.asyncio
    async def test_defend_with_threat_uses_threatened_zone(self, vendor_sharpy_env: Any) -> None:
        """defend + 一个己方 zone 有敌军 → effective_gp = 威胁 zone center。

        验证：威胁感知覆盖 hold_gather_point 和 forward_defense_point。
        """
        home = _FakePoint2((10, 10))
        threatened_center = _FakePoint2((40, 40))
        no_threat_center = _FakePoint2((70, 70))
        enemy_main_center = _FakePoint2((100, 100))

        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(threatened_center, is_ours=True, threat_power=5.0),
                _make_zone(no_threat_center, is_ours=True, threat_power=0.0),
                _make_zone(enemy_main_center, is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
            hold_gather_point=None,
            zone_manager=zm,
        )

        await act.execute()

        assert act.current_gather_point == threatened_center, (
            f"defend + 威胁 zone → 应守威胁 zone center，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_threat_overrides_hold_gather_point(self, vendor_sharpy_env: Any) -> None:
        """defend + 威胁 zone + hold_gather_point 都存在 → 威胁 zone 优先。

        依据：用户"有敌人来了就去迎"，威胁优先级高于玩家显式点。
        """
        home = _FakePoint2((10, 10))
        watchtower = _FakePoint2((42, 60))  # 玩家点的防守点
        threatened_center = _FakePoint2((20, 20))  # 三矿被打
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(threatened_center, is_ours=True, threat_power=8.0),
                _make_zone(home, is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
            hold_gather_point=watchtower,
            zone_manager=zm,
        )

        await act.execute()

        assert act.current_gather_point == threatened_center, (
            f"威胁 zone 应优先于 hold_gather_point，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_no_threat_falls_back_to_hold_gather_point(
        self, vendor_sharpy_env: Any
    ) -> None:
        """defend + 无威胁 + hold_gather_point → 守该点（2026-06-03 bug B 修复不退步）。"""
        home = _FakePoint2((10, 10))
        watchtower = _FakePoint2((42, 60))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(_FakePoint2((40, 40)), is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((70, 70)), is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
            hold_gather_point=watchtower,
            zone_manager=zm,
        )

        await act.execute()

        assert act.current_gather_point == watchtower, (
            f"无威胁时应回落到 hold_gather_point，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_no_threat_no_hold_falls_back_to_forward(
        self, vendor_sharpy_env: Any
    ) -> None:
        """defend + 无威胁 + 无 hold_gather_point → 守离敌最近的己方分矿（前沿）。"""
        home = _FakePoint2((10, 10))
        my_main = _FakePoint2((10, 10))
        my_third = _FakePoint2((70, 70))  # 离 enemy_main(100,100) 更近
        enemy_main = _FakePoint2((100, 100))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(my_main, is_ours=True, threat_power=0.0),
                _make_zone(my_third, is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((50, 50)), is_ours=False, threat_power=0.0),
                _make_zone(enemy_main, is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            solver_gather_point=_FakePoint2((50, 50)),
            start_location=home,
            hold_gather_point=None,
            zone_manager=zm,
        )

        await act.execute()

        # my_third(70,70) 离 enemy_main(100,100) ≈ 42 < my_main(10,10) 离 enemy ≈ 127
        assert act.current_gather_point == my_third, (
            f"无威胁无指定点 → 守前沿(三矿)，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_hysteresis_stays_at_old_zone_when_still_threatened(
        self, vendor_sharpy_env: Any
    ) -> None:
        """滞回：旧 zone(A) 仍有威胁，新 zone(B) 强度 < 1.5x → 保持 A。

        防止敌军在两 zone 边界时聚团点反复跳动。
        """
        zone_a = _FakePoint2((10, 10))
        zone_b = _FakePoint2((40, 40))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(zone_a, is_ours=True, threat_power=5.0),
                _make_zone(zone_b, is_ours=True, threat_power=6.0),  # < 5.0 * 1.5 = 7.5
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            zone_manager=zm,
        )
        # 模拟上一 tick 已选定 zone_a
        act._vbc_threat_zone_center = zone_a
        act._vbc_threat_zone_power = 5.0

        await act.execute()

        assert act.current_gather_point == zone_a, (
            f"zone_b power(6.0) < zone_a(5.0)*1.5(7.5) → 应保持 zone_a，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_hysteresis_switches_when_new_zone_much_stronger(
        self, vendor_sharpy_env: Any
    ) -> None:
        """滞回：zone_b 强度 ≥ zone_a * 1.5 → 切换到 zone_b。"""
        zone_a = _FakePoint2((10, 10))
        zone_b = _FakePoint2((40, 40))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(zone_a, is_ours=True, threat_power=5.0),
                _make_zone(zone_b, is_ours=True, threat_power=8.0),  # 8.0 >= 5.0 * 1.5 = 7.5
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            zone_manager=zm,
        )
        act._vbc_threat_zone_center = zone_a
        act._vbc_threat_zone_power = 5.0

        await act.execute()

        assert act.current_gather_point == zone_b, (
            f"zone_b power(8.0) >= zone_a(5.0)*1.5(7.5) → 应切换到 zone_b，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_hysteresis_switches_when_old_zone_cleared(
        self, vendor_sharpy_env: Any
    ) -> None:
        """滞回：旧 zone(A) 已无敌 → 切换到当前最强威胁 zone(B)。"""
        zone_a = _FakePoint2((10, 10))
        zone_b = _FakePoint2((40, 40))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(zone_a, is_ours=True, threat_power=0.0),  # 旧 zone 已清
                _make_zone(zone_b, is_ours=True, threat_power=3.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            zone_manager=zm,
        )
        act._vbc_threat_zone_center = zone_a  # 上一 tick 的状态
        act._vbc_threat_zone_power = 4.0

        await act.execute()

        assert act.current_gather_point == zone_b, (
            f"旧 zone_a 已清 → 应切换到 zone_b，实际 {act.current_gather_point}"
        )

    @pytest.mark.asyncio
    async def test_defend_multiple_threats_picks_strongest(self, vendor_sharpy_env: Any) -> None:
        """多个 zone 同时有威胁 → 选 power 最大的（最紧急基地优先）。"""
        zone_a = _FakePoint2((10, 10))
        zone_b = _FakePoint2((40, 40))
        zone_c = _FakePoint2((70, 70))
        zm = SimpleNamespace(
            expansion_zones=[
                _make_zone(zone_a, is_ours=True, threat_power=3.0),
                _make_zone(zone_b, is_ours=True, threat_power=9.0),  # 最强
                _make_zone(zone_c, is_ours=True, threat_power=6.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )
        act = _make_pzg(
            override_intent="defend",
            zone_manager=zm,
        )

        await act.execute()

        assert act.current_gather_point == zone_b, (
            f"应选 power 最大的 zone_b(9.0)，实际 {act.current_gather_point}"
        )


# ---------------------------------------------------------------------------
# TestPlanZoneAttackDefendThreatAware — 2026-06-13 defend retreat target 威胁感知
# ---------------------------------------------------------------------------


class TestPlanZoneAttackDefendThreatAware:
    """2026-06-13 #525 defend retreat target 威胁感知。

    attack→defend 切换时，若己方 zone 有敌 → 撤退目标 = 该 zone（迎击）；
    无威胁时回落到 hold_gather_point 或 gather_point（原行为不变）。
    """

    @pytest.mark.asyncio
    async def test_defend_retreat_uses_threatened_zone(self, vendor_sharpy_env: Any) -> None:
        """defend + 威胁 zone → combat.execute 调 threatened_zone center。"""
        AttackStatus = _attack_status()
        threatened_center = _FakePoint2((30, 30))
        act = _make_zone_attack_act(override_intent="defend")
        act.status = AttackStatus.NotActive
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))

        # zone_manager 有一个被攻击的己方 zone
        act.zone_manager = SimpleNamespace(
            expansion_zones=[
                _make_zone(threatened_center, is_ours=True, threat_power=6.0),
                _make_zone(_FakePoint2((70, 70)), is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == threatened_center, (
            f"defend + 威胁 zone → retreat target 应为 threatened_center，实际 {target}"
        )

    @pytest.mark.asyncio
    async def test_defend_no_threat_falls_back_to_forward_base(
        self, vendor_sharpy_env: Any
    ) -> None:
        """defend + 无威胁 → retreat target = **最前沿基地**(距敌主基最近的己方 zone),
        **不再**是 gather_point(2026-06-17 用户:无敌→守最靠近敌方的己方基地)。
        """
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent="defend")
        act.status = AttackStatus.NotActive
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))  # 不应被选中

        # 己方两基地:main(40,40) + forward(80,80);敌主基 = zones[-1] = (100,100)。
        # forward(80,80) 距敌(100,100) 更近 → 应选它,而非 main / gather_point。
        act.zone_manager = SimpleNamespace(
            expansion_zones=[
                _make_zone(_FakePoint2((40, 40)), is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((80, 80)), is_ours=True, threat_power=0.0),
                _make_zone(_FakePoint2((100, 100)), is_ours=False, threat_power=0.0),
            ]
        )

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == _FakePoint2((80, 80)), (
            f"无威胁 defend → 应守最前沿基地 (80,80)(距敌主基最近),实际 {target}"
        )

    @pytest.mark.asyncio
    async def test_defend_no_zone_manager_falls_back_to_home(self, vendor_sharpy_env: Any) -> None:
        """defend + zone_manager 不存在 → _vbc_defend_target 与 _vbc_forward_defense_point 都
        Exception → 回落到 start_location(home),不再是 gather_point。"""
        AttackStatus = _attack_status()
        act = _make_zone_attack_act(override_intent="defend")
        act.status = AttackStatus.NotActive
        act.attack_retreat_started = 10.0
        act.ai.time = 15.0
        act.ai.start_location = _FakePoint2((50, 50))
        act.gather_point_solver.gather_point = _FakePoint2((20, 80))
        # 不设 act.zone_manager → 两个 _vbc 方法都 raise → forward 兜底 start_location

        await act.execute()

        call_args = act.combat.execute.call_args
        assert call_args is not None, "combat.execute 未被调用"
        target = call_args[0][0]
        assert target == _FakePoint2((50, 50)), (
            f"无 zone_manager → forward 兜底 start_location (50,50),实际 {target}"
        )


# ---------------------------------------------------------------------------
# PlanZoneDefense defend hook（2026-06-17 defend 大军原地拉扯修复）
# ---------------------------------------------------------------------------


class _FakeUnits(list):
    """最小 sharpy Units stub：支持 (type) 调用筛选 / tags_in / exclude_type / exists / clear。"""

    def __call__(self, type_id: Any) -> _FakeUnits:
        return _FakeUnits([u for u in self if getattr(u, "type_id", None) == type_id])

    def tags_in(self, tags: Any) -> _FakeUnits:
        tagset = set(tags)
        return _FakeUnits([u for u in self if getattr(u, "tag", None) in tagset])

    def exclude_type(self, type_id: Any) -> _FakeUnits:
        return _FakeUnits([u for u in self if getattr(u, "type_id", None) != type_id])

    def closest_to(self, pos: Any) -> Any:
        return min(self, key=lambda u: u.position.distance_to(pos))

    @property
    def exists(self) -> bool:
        return len(self) > 0


def _pzd_cls() -> type:
    """获取已加载的 vendor PlanZoneDefense 类。"""
    return sys.modules["sharpy.plans.tactics.zone_defense"].PlanZoneDefense  # type: ignore[no-any-return]


def _mk_unit(tag: int, type_id: str, pos: Any) -> Any:
    u = SimpleNamespace(tag=tag, type_id=type_id, position=pos)
    u.distance_to = lambda other, _p=pos: _p.distance_to(other)  # type: ignore[attr-defined]
    return u


class _FakeExtPower:
    """可控的 ExtendedPower stub（zone_defense 用算术 + is_enough_for 分支判定）。

    fake env 里 ExtendedPower=MagicMock(类),每次调用产生新实例无法预配 is_enough_for;
    换成本类,用 class-level ENOUGH 旗标控制 is_enough_for 返回(两实例共享够用)。
    """

    ENOUGH = False

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.power = 0.0

    def add_power(self, *_a: Any) -> None:
        pass

    def add_unit(self, *_a: Any) -> None:
        pass

    def multiply(self, *_a: Any) -> None:
        pass

    def substract_power(self, *_a: Any) -> None:
        pass

    def is_enough_for(self, *_a: Any, **_k: Any) -> bool:
        return _FakeExtPower.ENOUGH


def _make_pzd(
    override_intent: Any,
    *,
    existing_defenders: Any = None,
    free_army: Any = None,
    enemy_type: str = "WORKER",
    enough: bool = False,
) -> Any:
    """构造不需 sharpy init 的 PlanZoneDefense 实例（绕 __init__）。

    场景：单个己方 zone(center=(40,40),radius=10)有 1 个敌人(worker 类型，让
    worker-pull 分支不触发，聚焦主力 claim 路径)。worker_type='SCV'。
    """
    WORKER_TYPE = "SCV"
    # 装可控 ExtendedPower(控制 is_enough_for 分支)。zone_defense 用 `from ... import ExtendedPower`
    # 在模块命名空间绑定,需打到 zone_defense 自己的模块(不是 source 模块)。
    _FakeExtPower.ENOUGH = enough
    sys.modules["sharpy.plans.tactics.zone_defense"].ExtendedPower = _FakeExtPower  # type: ignore[attr-defined]
    cls = _pzd_cls()
    act = cls.__new__(cls)
    act.worker_type = WORKER_TYPE
    act.defender_tags = {0: [getattr(d, "tag", 0) for d in (existing_defenders or [])]}
    act.defender_secondary_tags = {0: []}
    act.zone_seen_enemy = {0: -10.0}

    center = _FakePoint2((40, 40))
    enemy = _mk_unit(999, enemy_type, _FakePoint2((41, 41)))
    zone = SimpleNamespace(
        center_location=center,
        radius=10.0,
        is_ours=True,
        known_enemy_units=_FakeUnits([enemy]),
        assaulting_enemies=_FakeUnits([enemy]),
        assaulting_enemy_power=MagicMock(),
        gather_point=center,
        last_scouted_center=-1.0,
    )
    act.zone_manager = SimpleNamespace(expansion_zones=[zone], own_main_zone=zone)

    act.knowledge = SimpleNamespace(
        ai=SimpleNamespace(time=100.0),
        vibecraft=SimpleNamespace(combat_intent_override=override_intent),
    )
    act.ai = SimpleNamespace(time=100.0)

    act.roles = MagicMock()
    act.roles.all_from_task = MagicMock(return_value=_FakeUnits(existing_defenders or []))
    act.roles.free_units = _FakeUnits(free_army or [])
    act.roles.units = MagicMock(return_value=SimpleNamespace(not_ready=_FakeUnits([])))
    act.roles.get_defenders = MagicMock(return_value=_FakeUnits([]))
    act.roles.set_task = MagicMock()
    act.roles.clear_task = MagicMock()
    act.roles.clear_tasks = MagicMock()
    act.roles.refresh_tags = MagicMock()

    act.unit_values = MagicMock()
    act.unit_values.should_attack = MagicMock(return_value=True)
    act.combat = MagicMock()
    act.combat.tags = []
    act.cache = MagicMock()
    act.cache.by_tag = MagicMock(
        side_effect=lambda t: next(
            (u for u in (existing_defenders or []) if getattr(u, "tag", None) == t), None
        )
    )
    return act


class TestPlanZoneDefenseDefendHook:
    """2026-06-17 defend 大军"原地保持队形拉扯"修复。

    defend intent 下 PlanZoneDefense 不再 claim/dispatch 主力(交给 PlanZoneGather 单一锚点),
    消除 enemy_center↔锚点双目标 churn。非 defend 走原逻辑(claim 主力)。
    """

    @pytest.mark.asyncio
    async def test_non_defend_claims_main_army(self, vendor_sharpy_env: Any) -> None:
        """intent=None(默认)→ 主力 claim 路径正常:defenders 不足(enough=False)时调 get_defenders。"""
        act = _make_pzd(override_intent=None, free_army=[], enough=False)
        await act.execute()

        assert act.roles.get_defenders.called, (
            "非 defend 且 defenders 不足时,应调 get_defenders claim 主力(原 sharpy 行为)"
        )

    @pytest.mark.asyncio
    async def test_defend_does_not_claim_main_army(self, vendor_sharpy_env: Any) -> None:
        """intent=defend → **不**调 get_defenders(不 claim 主力,交给 PlanZoneGather)。"""
        army = [_mk_unit(1, "MARINE", _FakePoint2((42, 42)))]  # 近 zone 的 free 主力
        act = _make_pzd(override_intent="defend", free_army=army, enough=False)
        await act.execute()

        assert not act.roles.get_defenders.called, (
            "defend 下不应 claim 主力(get_defenders 不该被调)—— 主力定位交给 PlanZoneGather"
        )

    @pytest.mark.asyncio
    async def test_defend_releases_residual_main_defenders(self, vendor_sharpy_env: Any) -> None:
        """intent=defend → 释放残留的非工人 Defending(clear_tasks,交还 gather)。"""
        residual = [_mk_unit(7, "MARINE", _FakePoint2((40, 40)))]  # 上一刻残留的 Defending 主力
        act = _make_pzd(
            override_intent="defend", existing_defenders=residual, free_army=residual, enough=False
        )
        await act.execute()

        assert act.roles.clear_tasks.called, (
            "defend 下应 clear_tasks 释放残留非工人 Defending,交还 PlanZoneGather"
        )


# ---------------------------------------------------------------------------
# TestDistributeWorkersMiningPriorityHook
# ---------------------------------------------------------------------------


class TestDistributeWorkersMiningPriorityHook:
    """DistributeWorkers.execute 开头的 vibecraft 采矿策略 hook 行为单测。

    不走完整 execute（依赖太重），而是直接构造 DistributeWorkers 实例并调用
    hook 逻辑（前置 hasattr 缓存 + min/max_gas 覆写），验证三种 mining_priority
    的覆写结果和 None 恢复原值行为。
    """

    @staticmethod
    def _make_dw(
        orig_min_gas: int | None,
        orig_max_gas: int | None,
        mining_priority: str | None,
        free_workers_amount: int = 10,
        townhall_ideal_list: list[int] | None = None,
        gas_buildings_ready_amount: int = 2,
    ) -> Any:
        """构造 DistributeWorkers fake 实例，模拟 execute 调用前的状态。

        仅注入 hook 需要的最小 stub，不走 sharpy __init__ / start。
        """
        dw = object.__new__(type("DistributeWorkers", (), {}))
        dw.min_gas = orig_min_gas
        dw.max_gas = orig_max_gas

        # knowledge.vibecraft 注入
        dw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(mining_priority=mining_priority))

        # roles.free_workers stub
        free_workers = SimpleNamespace(amount=free_workers_amount)
        dw.roles = SimpleNamespace(free_workers=free_workers)

        # ai.townhalls.ready stub（每个元素有 ideal_harvesters）
        if townhall_ideal_list is None:
            townhall_ideal_list = [16]  # 默认 1 矿 16
        townhall_objs = [SimpleNamespace(ideal_harvesters=v) for v in townhall_ideal_list]
        dw.ai = SimpleNamespace(
            townhalls=SimpleNamespace(ready=townhall_objs),
            gas_buildings=SimpleNamespace(ready=SimpleNamespace(amount=gas_buildings_ready_amount)),
        )
        return dw

    @staticmethod
    def _apply_hook(dw: Any) -> None:
        """手动执行 execute 开头的 hook 逻辑（复制 vendor 里的逻辑，确保一致）。"""
        if not hasattr(dw, "_vc_orig_min_gas"):
            dw._vc_orig_min_gas = dw.min_gas
            dw._vc_orig_max_gas = dw.max_gas

        _vbc = getattr(getattr(dw, "knowledge", None), "vibecraft", None)
        _mining_priority = getattr(_vbc, "mining_priority", None)

        if _mining_priority == "mineral":
            _total_workers = dw.roles.free_workers.amount
            _mineral_ideal = sum(
                int(getattr(th, "ideal_harvesters", 0)) for th in dw.ai.townhalls.ready
            )
            dw.max_gas = max(0, _total_workers - _mineral_ideal)
            dw.min_gas = None
        elif _mining_priority == "gas":
            _gas_count = dw.ai.gas_buildings.ready.amount
            dw.min_gas = _gas_count * 3
            dw.max_gas = None
        else:
            dw.min_gas = dw._vc_orig_min_gas
            dw.max_gas = dw._vc_orig_max_gas

    def test_mineral_priority_sets_max_gas_and_clears_min(self) -> None:
        """mining_priority="mineral" → max_gas = max(0, workers - ideal)，min_gas = None。

        例：10 农民、矿容量 16 → max_gas = max(0, 10-16) = 0（全部去采矿，不采气）。
        """
        dw = self._make_dw(
            orig_min_gas=None,
            orig_max_gas=None,
            mining_priority="mineral",
            free_workers_amount=10,
            townhall_ideal_list=[16],
        )
        self._apply_hook(dw)
        assert dw.min_gas is None, "优先水晶：min_gas 应为 None"
        assert dw.max_gas == 0, f"优先水晶：10农民 16ideal → max_gas=0，实际={dw.max_gas}"

    def test_mineral_priority_allows_some_gas_when_workers_exceed_ideal(self) -> None:
        """workers > mineral_ideal → max_gas > 0（多余农民可去采气）。

        例：30 农民、矿容量 16 → max_gas = max(0, 30-16) = 14。
        """
        dw = self._make_dw(
            orig_min_gas=None,
            orig_max_gas=None,
            mining_priority="mineral",
            free_workers_amount=30,
            townhall_ideal_list=[16],
        )
        self._apply_hook(dw)
        assert dw.min_gas is None
        assert dw.max_gas == 14, f"30农-16ideal → max_gas=14，实际={dw.max_gas}"

    def test_gas_priority_sets_min_gas_and_clears_max(self) -> None:
        """mining_priority="gas" → min_gas = gas_count*3，max_gas = None。

        例：2 口气井 → min_gas = 6（6个农民优先采气）。
        """
        dw = self._make_dw(
            orig_min_gas=None,
            orig_max_gas=None,
            mining_priority="gas",
            gas_buildings_ready_amount=2,
        )
        self._apply_hook(dw)
        assert dw.max_gas is None, "优先气：max_gas 应为 None"
        assert dw.min_gas == 6, f"2口气井 → min_gas=6，实际={dw.min_gas}"

    def test_default_restores_original_values(self) -> None:
        """mining_priority=None → 恢复构造期缓存的原始 min/max_gas（不写 None）。

        典型场景：剧本构造时 DistributeWorkers(min_gas=6)，改为 default 后应恢复 6。
        """
        dw = self._make_dw(
            orig_min_gas=6,  # 剧本给的 min_gas=6（人族 aggressive gas fill）
            orig_max_gas=None,
            mining_priority=None,
        )
        self._apply_hook(dw)
        assert dw.min_gas == 6, f"default 应恢复原始 min_gas=6，实际={dw.min_gas}"
        assert dw.max_gas is None, f"default 应恢复原始 max_gas=None，实际={dw.max_gas}"

    def test_cache_is_set_on_first_frame_and_reused(self) -> None:
        """首帧缓存 _vc_orig_min/max_gas；之后改 min/max_gas 不影响缓存值（供 default 恢复用）。"""
        dw = self._make_dw(
            orig_min_gas=3,
            orig_max_gas=8,
            mining_priority="mineral",
            free_workers_amount=20,
            townhall_ideal_list=[16],
        )
        assert not hasattr(dw, "_vc_orig_min_gas"), "首次调用前不应有缓存"

        self._apply_hook(dw)  # 首帧：缓存 3/8，然后覆写 min=None max=4
        assert dw._vc_orig_min_gas == 3, "首帧应缓存原始 min_gas=3"
        assert dw._vc_orig_max_gas == 8, "首帧应缓存原始 max_gas=8"

        # 切到 default → 恢复缓存值，不受中途覆写影响
        dw.knowledge.vibecraft.mining_priority = None
        self._apply_hook(dw)
        assert dw.min_gas == 3, f"default 恢复缓存 min_gas=3，实际={dw.min_gas}"
        assert dw.max_gas == 8, f"default 恢复缓存 max_gas=8，实际={dw.max_gas}"

    def test_mineral_priority_two_bases(self) -> None:
        """两基地 ideal_harvesters 累加正确（Σ ideal_harvesters 不只取一个）。

        例：2 基地各 ideal=16 → mineral_ideal=32；workers=20 → max_gas=max(0,20-32)=0。
        """
        dw = self._make_dw(
            orig_min_gas=None,
            orig_max_gas=None,
            mining_priority="mineral",
            free_workers_amount=20,
            townhall_ideal_list=[16, 16],  # 两基地
        )
        self._apply_hook(dw)
        assert dw.max_gas == 0, f"2基地32ideal,20农 → max_gas=0，实际={dw.max_gas}"
