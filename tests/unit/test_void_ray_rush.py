"""VoidRayHarass plan 单测。

覆盖：
  - _ready_to_harass：VS ready + 4 虚空舰 ready → True；缺任一 → False
  - 时间兜底：5:30 → True（VS ready + 虚空舰不足时）
  - _VR_WAVE_THRESHOLD == 4（用户明确：攒 4 个出门骚扰）
  - _TIME_FALLBACK_S == 330（5:30 兜底）
  - _ZONE_ATTACK_POWER == 0（不靠 supply 启发）
  - AttackGate latch 行为
  - plan 内 VOIDRAY ProtossUnit 存在（target=12）
  - plan 内 STALKER ProtossUnit 存在（target=1，只 1 只保家）
  - plan 内 STARGATE GridBuilding(2) 存在（2 VS 同时建）
  - plan 内 GATEWAY GridBuilding(1) 存在（初期只 1 BG）
  - plan 内 GATEWAY GridBuilding(4) 存在（后期扩到 4 BG）
  - plan 内 CyberneticsCore GridBuilding 存在
  - plan 内 WARPGATERESEARCH Tech 存在
  - plan 内 EmitOpeningCompleteAct 存在（与 _ready_to_harass 绑定）
  - plan 内 Expand(2) 存在（开二矿）
  - plan 内 FORGE GridBuilding 存在（后期 BF 攻防升级）
  - attack_on_advantage：_make_harass_attack_act 构造的 act attack_on_advantage=True
  - plan 不含 _START_ATTACK_POWER（旧 all-in 痕迹已删）
  - 追猎只出 1 个（不是 6 个）
  - plan 不含 4:30 兜底（新 build 兜底 5:30）

本测试不依赖 sharpy 实例化（避免 config.ini 等环境依赖），
用 __new__ 绕开 __init__ + 手动注入 mock ai。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"

# 必须在 import VoidRayHarass 之前让 sharpy 可 import
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def plan_instance():
    """构造 VoidRayHarass instance，绕过 sharpy __init__（避免 config.ini 依赖）。"""
    try:
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass
    except ImportError as e:
        pytest.skip(f"sharpy import 失败: {e}")
    inst = VoidRayHarass.__new__(VoidRayHarass)
    from vibecraft.bot.auto_combat.intent_gate import AttackGate

    inst._attack_gate = AttackGate(VoidRayHarass._ready_to_harass)
    return inst


def _make_ai(
    *,
    stargate_ready: bool = False,
    vr_count: int = 0,
    game_time: float = 0.0,
    intent: str | None = None,
) -> MagicMock:
    """构造 mock ai 控制 _ready_to_harass 各维度。"""
    ai = MagicMock()

    # structures(STARGATE).ready.exists
    stargate_mock = MagicMock()
    stargate_mock.ready.exists = stargate_ready
    ai.structures.return_value = stargate_mock

    # units(VOIDRAY).ready.amount
    vr_mock = MagicMock()
    vr_mock.ready.amount = vr_count
    ai.units.return_value = vr_mock

    ai.time = game_time

    # intent
    ai.knowledge = SimpleNamespace(vibecraft=SimpleNamespace(combat_intent_override=intent))
    return ai


# ============================================================
# _ready_to_harass 核心逻辑
# ============================================================


class TestReadyToHarass:
    """_ready_to_harass 出门骚扰 timing 判定。"""

    def test_false_when_no_stargate(self) -> None:
        """VS 未完成 → False（无论虚空舰数量）。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=False, vr_count=5, game_time=100.0)
        assert VoidRayHarass._ready_to_harass(ai) is False

    def test_false_when_stargate_ready_but_zero_vr(self) -> None:
        """VS ready 但虚空舰=0 → False。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=0, game_time=100.0)
        assert VoidRayHarass._ready_to_harass(ai) is False

    def test_false_when_only_three_vr(self) -> None:
        """VS ready 但虚空舰=3（< wave_threshold=4）→ False。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=3, game_time=100.0)
        assert VoidRayHarass._ready_to_harass(ai) is False

    def test_true_when_four_vr_ready(self) -> None:
        """VS ready + 虚空舰=4（wave threshold 满足）→ True。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=4, game_time=100.0)
        assert VoidRayHarass._ready_to_harass(ai) is True

    def test_true_when_five_vr_ready(self) -> None:
        """VS ready + 虚空舰=5 → True（超过 threshold 也 True）。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=5, game_time=100.0)
        assert VoidRayHarass._ready_to_harass(ai) is True

    def test_time_fallback_at_5min30(self) -> None:
        """5:30 时间兜底：VS ready + 虚空舰=0 + time=330 → True。

        骚扰 build 兜底从旧版 4:30 放宽到 5:30（不是 all-in）。
        """
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=0, game_time=330.0)
        assert VoidRayHarass._ready_to_harass(ai) is True

    def test_time_fallback_no_stargate_still_false_at_5min30(self) -> None:
        """时间兜底先过 VS 检查：VS 未完成时即使 5:30 也 False。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=False, vr_count=0, game_time=330.0)
        assert VoidRayHarass._ready_to_harass(ai) is False

    def test_just_below_5min30_no_vr_false(self) -> None:
        """5:29 + VS ready + 虚空舰=0 → False（还没到 5:30 兜底）。"""
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=0, game_time=329.0)
        assert VoidRayHarass._ready_to_harass(ai) is False

    def test_not_triggered_by_two_vr_old_threshold(self) -> None:
        """旧 build 的 2 VR 阈值不再触发：虚空舰=2 → False（新 build 需要 4 个）。

        这是与旧版区别的核心：新版不是 all-in，需要 4 个虚空舰才出门骚扰。
        """
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayHarass

        ai = _make_ai(stargate_ready=True, vr_count=2, game_time=200.0)
        assert VoidRayHarass._ready_to_harass(ai) is False


# ============================================================
# 常量验证
# ============================================================


def test_vr_wave_threshold_is_four() -> None:
    """_VR_WAVE_THRESHOLD == 4：骚扰出门需要 4 虚空舰（用户明确）。"""
    from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import _VR_WAVE_THRESHOLD

    assert _VR_WAVE_THRESHOLD == 4


def test_time_fallback_is_5min30() -> None:
    """_TIME_FALLBACK_S == 330（5:30）：骚扰 build 兜底，不是 all-in。"""
    from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import _TIME_FALLBACK_S

    assert pytest.approx(330.0) == _TIME_FALLBACK_S


def test_zone_attack_power_is_zero() -> None:
    """_ZONE_ATTACK_POWER == 0：不靠 supply 启发，由 4 虚空舰显式判定出门。

    新 build 不用 _START_ATTACK_POWER（旧 all-in 痕迹），出门完全由
    _ready_to_harass 的虚空舰数量条件控制。
    """
    from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import _ZONE_ATTACK_POWER

    assert _ZONE_ATTACK_POWER == 0


def test_no_start_attack_power_constant() -> None:
    """plan 模块不应包含 _START_ATTACK_POWER（旧 all-in 痕迹已删除）。"""
    import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

    assert not hasattr(mod, "_START_ATTACK_POWER"), (
        "_START_ATTACK_POWER 是旧版 all-in build 的遗留常量，新版骚扰 build 已不使用"
    )


# ============================================================
# AttackGate — intent 覆盖 + latch 行为
# ============================================================


class TestAttackGate:
    """VoidRayHarass._attack_gate intent 覆盖 + latch 行为。"""

    def test_gate_false_when_no_intent_no_ready(self, plan_instance) -> None:
        """无 intent + _ready_to_harass False → gate False。"""
        ai = _make_ai(intent=None, stargate_ready=False, vr_count=0)
        assert plan_instance._attack_gate(ai) is False
        assert plan_instance._attack_gate.latched is False

    def test_gate_true_for_attack_intent(self, plan_instance) -> None:
        """intent=attack → gate True（玩家强制进攻）。"""
        ai = _make_ai(intent="attack")
        assert plan_instance._attack_gate(ai) is True
        assert plan_instance._attack_gate.latched is True

    def test_gate_true_for_retreat_intent(self, plan_instance) -> None:
        """intent=retreat → gate True（让 PlanZoneAttack 跑，处理撤退）。"""
        ai = _make_ai(intent="retreat")
        assert plan_instance._attack_gate(ai) is True

    def test_gate_true_for_defend_intent(self, plan_instance) -> None:
        """intent=defend → gate True。"""
        ai = _make_ai(intent="defend")
        assert plan_instance._attack_gate(ai) is True

    def test_gate_true_for_hold_intent(self, plan_instance) -> None:
        """intent=hold → gate True。"""
        ai = _make_ai(intent="hold")
        assert plan_instance._attack_gate(ai) is True

    def test_gate_true_when_four_vr_ready(self, plan_instance) -> None:
        """_ready_to_harass True（4 虚空舰 + VS ready）→ gate True + latch。"""
        ai = _make_ai(stargate_ready=True, vr_count=4, game_time=280.0)
        assert plan_instance._attack_gate(ai) is True
        assert plan_instance._attack_gate.latched is True

    def test_gate_false_with_only_two_vr_no_intent(self, plan_instance) -> None:
        """虚空舰=2 + 无 intent → gate False（新 build 需要 4 个）。"""
        ai = _make_ai(stargate_ready=True, vr_count=2, game_time=200.0, intent=None)
        assert plan_instance._attack_gate(ai) is False

    def test_latch_persists_after_vr_die(self, plan_instance) -> None:
        """latch 后虚空舰被打死（vr_count=0），gate 仍 True（防 gate 永久关闭）。"""
        # 触发 latch
        ai_ready = _make_ai(stargate_ready=True, vr_count=4, game_time=280.0)
        assert plan_instance._attack_gate(ai_ready) is True
        # 虚空舰全死
        ai_no_vr = _make_ai(stargate_ready=True, vr_count=0, game_time=300.0)
        assert plan_instance._attack_gate(ai_no_vr) is True, (
            "latch 后 gate 必须永久 True，防虚空舰死光 gate 重新关闭"
        )

    def test_retreat_intent_latches_gate(self, plan_instance) -> None:
        """玩家直接点 retreat（没先 attack）也 latch。"""
        ai_retreat = _make_ai(intent="retreat")
        assert plan_instance._attack_gate(ai_retreat) is True
        assert plan_instance._attack_gate.latched is True


# ============================================================
# plan 结构检查（source code 静态检查）
# ============================================================


class TestPlanStructure:
    """检查 plan 文件包含预期的关键 act 和单位定义。"""

    def test_plan_module_has_void_ray_harass_class(self) -> None:
        """plan 模块有 VoidRayHarass 类。"""
        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        assert hasattr(mod, "VoidRayHarass")

    def test_plan_has_voidray_typeid(self) -> None:
        """plan 模块用到 VOIDRAY（虚空舰）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "VOIDRAY" in src, "plan 中必须有 VOIDRAY 单位 id"

    def test_plan_has_two_stargates(self) -> None:
        """plan 源码包含 STARGATE GridBuilding(…, 2)（2 VS 同时建）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "STARGATE" in src
        assert "GridBuilding" in src
        # 验 2 VS 同时建（GridBuilding(UnitTypeId.STARGATE, 2)）
        assert "STARGATE, 2)" in src, "plan 必须同时建 2 VS（GridBuilding(STARGATE, 2)）"

    def test_plan_initial_gateway_is_one(self) -> None:
        """plan 初期只建 1 BG（1 BG + 2 VS 路线）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        # GridBuilding(UnitTypeId.GATEWAY, 1) 存在（初期 1 BG）
        assert "GATEWAY, 1)" in src, "plan 初期必须只建 1 BG（用户明确：1 BG）"

    def test_plan_has_four_gateways_late(self) -> None:
        """plan 后期扩到 4 BG（二矿农民补满后 4 BG 爆兵）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "GATEWAY, 4)" in src, "plan 后期必须有 4 BG（用户明确：二矿后 4 BG 爆兵）"

    def test_plan_stalker_target_is_one(self) -> None:
        """plan 初期追猎 target=1（只 1 只守门，用户明确）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "STALKER" in src
        # ProtossUnit(UnitTypeId.STALKER, 1) — 初期 1 追猎
        assert "STALKER, 1)" in src, "追猎初期 target 必须是 1（用户明确：只 1 只守门）"

    def test_plan_has_cyberneticscore(self) -> None:
        """plan 源码包含 CYBERNETICSCORE。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "CYBERNETICSCORE" in src

    def test_plan_has_warpgate_research(self) -> None:
        """plan 源码包含 WARPGATERESEARCH（折跃研究）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "WARPGATERESEARCH" in src

    def test_plan_has_emit_opening_complete(self) -> None:
        """plan 源码包含 EmitOpeningCompleteAct（开局完成通知 Director）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "EmitOpeningCompleteAct" in src

    def test_plan_has_attack_gate(self) -> None:
        """plan 源码包含 AttackGate（统一 intent 覆盖 + latch）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "AttackGate" in src

    def test_plan_has_expand_2(self) -> None:
        """plan 包含 Expand(2)（骚扰同时开二矿，用户明确）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "Expand(2)" in src, "骚扰 build 必须开二矿（Expand(2)）"

    def test_plan_has_forge(self) -> None:
        """plan 包含 FORGE GridBuilding（后期 BF 攻防升级，用户明确）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "FORGE" in src, "骚扰 + 运营 build 后期需 BF 地空攻防升级"

    def test_plan_has_chrono_voidray(self) -> None:
        """plan 有 ChronoUnit(VOIDRAY)（加速虚空舰产出是 build 关键）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "ChronoUnit" in src
        assert "VOIDRAY" in src

    def test_plan_no_start_attack_power(self) -> None:
        """plan 不含 _START_ATTACK_POWER（旧 all-in 痕迹已删除）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        assert "_START_ATTACK_POWER" not in src, (
            "_START_ATTACK_POWER 是旧版 all-in 的遗留，新版骚扰 build 不用"
        )

    def test_plan_no_4min30_fallback(self) -> None:
        """plan 不含 4:30 兜底（旧 all-in 的时间门；新 build 兜底 5:30）。"""
        import inspect

        import vibecraft.bot.auto_combat.protoss.plans.void_ray_rush as mod

        src = inspect.getsource(mod)
        # 4:30 = 270s；旧版写法
        assert "60 * 4 + 30" not in src, (
            "4:30 兜底是旧版 all-in 的遗留；新版骚扰 build 兜底是 5:30（330s）"
        )

    def test_make_harass_attack_act_sets_attack_on_advantage(self) -> None:
        """_make_harass_attack_act 构造的 act attack_on_advantage=True。

        骚扰 + 经济 build：优势时顺势推进（不是 all-in 强推）。
        """
        from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import (
            _ZONE_ATTACK_POWER,
            _make_harass_attack_act,
        )

        act = _make_harass_attack_act(_ZONE_ATTACK_POWER)
        assert act.attack_on_advantage is True, (
            "骚扰 build 必须 attack_on_advantage=True，优势时顺势推"
        )


# ============================================================
# 策略 yaml 结构检查
# ============================================================


class TestStrategyYaml:
    """验证 void_ray_rush.yaml 关键字段存在。"""

    @pytest.fixture(autouse=True)
    def _load_yaml(self) -> None:
        import yaml

        yaml_path = _PROJECT_ROOT / "strategies" / "protoss" / "void_ray_rush.yaml"
        assert yaml_path.exists(), f"strategy yaml 不存在: {yaml_path}"
        with yaml_path.open(encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def test_id_field(self) -> None:
        assert self._data["id"] == "void_ray_rush"

    def test_display_name_zh_mentions_void_ray(self) -> None:
        """display_name_zh 包含"虚空"。"""
        assert "虚空" in self._data["display_name_zh"]

    def test_sharpy_class_points_to_void_ray_harass(self) -> None:
        cls = self._data["sharpy_dummy_class"]
        assert "VoidRayHarass" in cls
        assert "void_ray_rush" in cls

    def test_matchup_includes_pvz(self) -> None:
        assert "PvZ" in self._data["matchup"]

    def test_matchup_includes_pvp(self) -> None:
        assert "PvP" in self._data["matchup"]

    def test_matchup_includes_pvt(self) -> None:
        """新 build 通用性强，PvT 也支持（旧版只 PvZ/PvP）。"""
        assert "PvT" in self._data["matchup"]

    def test_aliases_not_empty(self) -> None:
        aliases = self._data.get("aliases", [])
        assert len(aliases) >= 8, "aliases 至少 8 条（骚扰 build 多种叫法）"

    def test_aliases_contain_key_phrases(self) -> None:
        aliases = self._data.get("aliases", [])
        alias_set = set(aliases)
        # 保留旧别名兼容（LLM 语义覆盖）
        assert "速虚空" in alias_set, "必须保留'速虚空'别名（常用叫法）"
        assert "虚空骚扰" in alias_set, "必须有'虚空骚扰'（新 build 核心特征）"
        assert "两矿虚空" in alias_set, "必须有'两矿虚空'（经济 build 标识）"
        assert "void ray rush" in alias_set, "保留英文别名向后兼容"

    def test_phases_exist_and_at_least_four(self) -> None:
        """新 build 有 4+ 个阶段（opening / tech / harass / expand / scale_up）。"""
        phases = self._data.get("phases", [])
        assert len(phases) >= 4, "骚扰 + 运营 build 至少 4 个阶段"

    def test_kind_is_opening_build(self) -> None:
        assert self._data.get("kind") == "opening_build"

    def test_default_transitions_contains_skytoss(self) -> None:
        """default_transitions 含 persistent_skytoss（虚空舰 → 天空神族最顺接）。"""
        transitions = self._data.get("default_transitions", [])
        midgame_ids = [t.get("midgame_id", "") for t in transitions]
        assert "persistent_skytoss" in midgame_ids, (
            "虚空骚扰开矿后最顺接 persistent_skytoss（天空神族延续）"
        )


# ============================================================
# VoidRayStageRallyAct（2026-06-07 用户：集结到离敌最近 VS / 时间交回 / 玩家让位）
# ============================================================


def _make_rally_act():
    from vibecraft.bot.auto_combat.protoss.plans.void_ray_rush import VoidRayStageRallyAct

    act = VoidRayStageRallyAct.__new__(VoidRayStageRallyAct)
    act._launched = False
    act._staged_logged = False
    return act


class _FakeStructs:
    """mock sc2 Units:.exists + .closest_to(point)（按欧氏距离）。"""

    def __init__(self, units):
        self._u = units

    @property
    def exists(self):
        return len(self._u) > 0

    def closest_to(self, p):
        return min(self._u, key=lambda s: (s.position.x - p.x) ** 2 + (s.position.y - p.y) ** 2)


def _vs(x, y):
    return SimpleNamespace(position=SimpleNamespace(x=x, y=y))


def _setup_rally_ai(act, *, game_time=100.0, stargates=None, attacking=None, player_rally=None):
    from sc2.ids.unit_typeid import UnitTypeId

    solver = MagicMock()
    ai = MagicMock()
    ai.time = game_time
    ai.start_location = SimpleNamespace(x=0.0, y=0.0)
    ai.enemy_start_locations = [SimpleNamespace(x=100.0, y=100.0)]
    ai.structures.return_value = SimpleNamespace(ready=_FakeStructs(stargates or []))
    knowledge = MagicMock()
    knowledge.vibecraft = SimpleNamespace(player_rally_point=player_rally)
    knowledge.roles.attacking_units = attacking or []
    knowledge.get_required_manager.return_value = solver
    act.ai = ai
    act.knowledge = knowledge
    # voidray type 给 wave 检测用
    return solver, UnitTypeId


class TestVoidRayStageRally:
    def test_rally_to_enemy_closest_vs(self) -> None:
        import asyncio

        act = _make_rally_act()
        vs_home = _vs(10.0, 10.0)  # 离家近
        vs_fwd = _vs(90.0, 90.0)  # 离敌近(野 VS)
        solver, _ = _setup_rally_ai(act, stargates=[vs_home, vs_fwd])
        asyncio.run(act.execute())
        # 集结点设到离对方主基地最近的那个 VS(野 VS)
        solver.set_gather_point.assert_called_once()
        arg = solver.set_gather_point.call_args[0][0]
        assert arg is vs_fwd.position

    def test_time_handback_latches(self) -> None:
        import asyncio

        act = _make_rally_act()
        solver, _ = _setup_rally_ai(act, game_time=500.0, stargates=[_vs(90, 90)])  # > 420
        asyncio.run(act.execute())
        assert act._launched is True
        solver.set_gather_point.assert_not_called()  # 交回 bot 默认,不覆盖

    def test_player_rally_yields(self) -> None:
        import asyncio

        act = _make_rally_act()
        solver, _ = _setup_rally_ai(act, stargates=[_vs(90, 90)], player_rally=(42.0, 42.0))
        asyncio.run(act.execute())
        solver.set_gather_point.assert_not_called()  # 玩家设了 → 让位
        assert act._launched is False  # 未 latch(玩家清掉后剧本可恢复)

    def test_wave_out_latches(self) -> None:
        import asyncio

        act = _make_rally_act()
        solver, UnitTypeId = _setup_rally_ai(act, stargates=[_vs(90, 90)])
        far_vr = SimpleNamespace(type_id=UnitTypeId.VOIDRAY, distance_to=lambda p: 99.0)
        act.knowledge.roles.attacking_units = [far_vr]
        asyncio.run(act.execute())
        assert act._launched is True
        solver.set_gather_point.assert_not_called()
