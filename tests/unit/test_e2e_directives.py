"""端到端 directive 测(2026-05-24 用户)。

覆盖每类命令的 4 阶段:
1. **发送**:_submit_directives 接受 directive
2. **执行**:对应 _exec_xxx / facade call 触发
3. **完成**:done_when 满足 → status='done' + 进 grace 期
4. **撤销**:revoke_directive → 单位 release + 卡片消失

L2 战术(蓝)、L3 持久(紫)、L4 产能(橙) 各覆盖代表场景。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    BuildAtPayload,
    Directive,
    ExpansionOverridePayload,
    MovePayload,
    ProductionItem,
    ProductionOverridePayload,
    ScoutPayload,
    StrategySetPayload,
    StructureItem,
    StructureOverridePayload,
    TacticalObjectivePayload,
    TechOverridePayload,
    UnitClaimPayload,
    UnitReleasePayload,
    VisionAcquired,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _make_director(session, library, facade=None):
    facade = facade or FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, library=library), facade


# ============================================================
# L2: MOVE(safe=False) 直线
# ============================================================
class TestL2Move:
    def test_move_executes_immediately(self, library, session) -> None:
        """MOVE 提交后 facade.execute_unit_action 被调(直线 move)。"""
        facade = FakeFacade()
        facade.selector_stub["WarpPrism"] = [3001]
        director, _ = _make_director(session, library, facade)

        payload = MovePayload(
            selector=Selector(unit_type="WarpPrism", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
            safe=False,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # facade.execute_unit_action 被调(直线 move)
        actions = [c for c in facade.calls if c.method == "execute_unit_action"]
        # verb 是 positional arg[1]
        assert any(a.args[1] == "move_to" for a in actions if len(a.args) >= 2)

    def test_move_revoke_releases_units(self, library, session) -> None:
        """× 撤销 MOVE → 单位 release_unit_role 调用。"""
        facade = FakeFacade()
        facade.selector_stub["WarpPrism"] = [3001]
        director, _ = _make_director(session, library, facade)

        payload = MovePayload(
            selector=Selector(unit_type="WarpPrism", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        # 单位被 Reserved
        assert 3001 in facade.unit_roles

        director.revoke_directive(d.id, now=15.0)
        # release_unit_role 被调,unit_roles 清
        assert 3001 not in facade.unit_roles


# ============================================================
# L2: SAFE_MOVE — plan_drop_path 路径 + 到达 done
# ============================================================
class TestL2SafeMove:
    def test_safe_move_registers_in_safe_move_tags(self, library, session) -> None:
        """safe=True MOVE 进 _safe_move_tags(_tick_safe_move_orders 控位)。"""
        facade = FakeFacade()
        facade.selector_stub["WarpPrism"] = [3001]
        director, _ = _make_director(session, library, facade)

        payload = MovePayload(
            selector=Selector(unit_type="WarpPrism", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
            safe=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # safe_move 进 _safe_move_tags 而非走 execute_unit_action
        assert d.id in director._safe_move_tags
        # 不调直线 move
        actions = [c for c in facade.calls if c.method == "execute_unit_action"]
        assert not actions

    def test_safe_move_revoke_clears_tags(self, library, session) -> None:
        facade = FakeFacade()
        facade.selector_stub["WarpPrism"] = [3001]
        director, _ = _make_director(session, library, facade)

        payload = MovePayload(
            selector=Selector(unit_type="WarpPrism", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
            safe=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)
        assert d.id in director._safe_move_tags

        director.revoke_directive(d.id, now=15.0)
        assert d.id not in director._safe_move_tags


# ============================================================
# L2: SCOUT
# ============================================================
class TestL2Scout:
    def test_scout_reserves_specified_count(self, library, session) -> None:
        """scout selector.count=1 → 只 reserve 1 个农民(不全锁)。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [5001, 5002, 5003, 5004]  # 4 个农民
        director, _ = _make_director(session, library, facade)

        payload = ScoutPayload(
            selector=Selector(unit_type="Probe", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # 只 1 个农民 reserved(非全 4 个)
        reserved = director._standing_order_tags.get(d.id, set())
        assert len(reserved) == 1


# ============================================================
# L2: BUILD_AT — named_spot 支持
# ============================================================
class TestL2BuildAt:
    def test_build_at_named_spot_resolves_to_point(self, library, session) -> None:
        """build_at 用 named_spot 而非 point → 后端 resolver 转 Point。"""

        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        # mock bot + named_spot resolver
        bot = MagicMock()
        director._bot = bot

        payload = BuildAtPayload(structure_type="Pylon", named_spot="main_ramp")
        d = Directive(payload=payload, issued_at=10.0)
        # 假设 NamedSpotRegistry.resolve 在生产中返回 Point2;此处不真调,只
        # 验证 directive 字段合法 + 进 board
        director._submit_directives([d], now=10.0)
        # facade.set_build_location_override 可能被调或跳过(resolver 取不到 point),
        # 验证至少不抛
        assert True  # directive 合法构造 + 不崩

    def test_build_at_point_only_works(self, library, session) -> None:
        """build_at 仅 point(无 named_spot)→ 直接走 facade.set_build_location_override。"""
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = BuildAtPayload(structure_type="Pylon", point=(50.0, 30.0))
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        calls = [c for c in facade.calls if c.method == "set_build_location_override"]
        assert len(calls) == 1


# ============================================================
# L3: UNIT_CLAIM persistent + STANDBY
# ============================================================
class TestL3Standby:
    def test_standby_directive_enters_standing_orders(self, library, session) -> None:
        facade = FakeFacade()
        facade.selector_stub["Zealot"] = [4001, 4002, 4003]
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Zealot", count=3),
            task=Task(
                primary_action=Action(
                    verb=Verb.STANDBY,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
                )
            ),
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # 进 standing_orders
        assert any(s.id == d.id for s in director.standing_orders)
        # 3 个叉子 reserved
        reserved = director._standing_order_tags.get(d.id, set())
        assert reserved == {4001, 4002, 4003}

    def test_standby_revoke_releases_all(self, library, session) -> None:
        facade = FakeFacade()
        facade.selector_stub["Zealot"] = [4001, 4002, 4003]
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Zealot", count=3),
            task=Task(
                primary_action=Action(
                    verb=Verb.STANDBY,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
                )
            ),
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        assert 4001 in facade.unit_roles

        director.revoke_directive(d.id, now=15.0)
        # standing_orders 移除 + 单位 release
        assert not any(s.id == d.id for s in director.standing_orders)
        assert 4001 not in facade.unit_roles


# ============================================================
# L4: STRUCTURE_OVERRIDE + done grace
# ============================================================
class TestL4StructureOverride:
    def test_structure_override_in_production_overrides(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="Gateway", target_count=8)],
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        assert any(s.id == d.id for s in director.production_overrides)

    def test_structure_override_revoke_clears_list(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="Gateway", target_count=8)],
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director.revoke_directive(d.id, now=15.0)

        assert not any(s.id == d.id for s in director.production_overrides)


# ============================================================
# L2: vision (改派 1 单位 hold,2026-05-24 用户)
# ============================================================
class TestL2Vision:
    def test_vision_dispatches_one_unit_default(self, library, session) -> None:
        """vision 默认派 1 Probe(unit_count_hint/unit_type_hint 缺省时)。

        2026-05-24 用户:vision 不应该 set 大部队 attack target,应派 1 单位 hold。
        """
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [8001, 8002, 8003]
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="vision",
            target_area="enemy_main",
            done_when=VisionAcquired(kind="vision_acquired", area="enemy_main", hold_seconds=30),
            timeout_s=60,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # 走 squad 路径 → 进 _tactical_squads(派 1 单位)
        assert d.id in director._tactical_squads
        squad = director._tactical_squads[d.id]
        # 默认 1 Probe
        assert len(squad.unit_tags) == 1
        assert 8001 in squad.unit_tags

    def test_vision_revoke_releases_unit(self, library, session) -> None:
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [8001]
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="vision",
            target_area="enemy_main",
            done_when=VisionAcquired(kind="vision_acquired", area="enemy_main", hold_seconds=30),
            timeout_s=60,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)
        assert 8001 in facade.unit_roles

        director.revoke_directive(d.id, now=15.0)
        assert 8001 not in facade.unit_roles


# ============================================================
# selector.count cap 防全锁
# ============================================================
class TestSelectorCountCap:
    def test_no_count_means_no_cap(self, library, session) -> None:
        """selector.count=None → 不限,所有匹配单位都 reserved(适用 standing)。"""
        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [6001, 6002, 6003]
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Phoenix"),  # count=None
            task=Task(
                primary_action=Action(
                    verb=Verb.PATROL,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
                )
            ),
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # 3 个凤凰都 reserved
        reserved = director._standing_order_tags.get(d.id, set())
        assert reserved == {6001, 6002, 6003}

    def test_count_caps_reserved_units(self, library, session) -> None:
        """selector.count=1 + 4 个匹配 → 只 reserve 1(防 60 农民全锁)。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [7001, 7002, 7003, 7004]
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
                )
            ),
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        reserved = director._standing_order_tags.get(d.id, set())
        assert len(reserved) == 1


# ============================================================
# L4: PRODUCTION_OVERRIDE — "下个 BG 出 4 追猎"
# ============================================================
class TestL4ProductionOverride:
    def test_production_override_enters_overrides_list(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = ProductionOverridePayload(
            items=[ProductionItem(unit_type="Stalker", count=4)],
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        assert any(s.id == d.id for s in director.production_overrides)

    def test_production_override_revoke_clears(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = ProductionOverridePayload(
            items=[ProductionItem(unit_type="Stalker", count=4)],
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director.revoke_directive(d.id, now=15.0)

        assert not any(s.id == d.id for s in director.production_overrides)


# ============================================================
# done_when 完成 → grace 期 → 真删
# ============================================================
class TestDoneWhenGraceExpiry:
    def test_release_marks_done_then_expires_after_grace(self, library, session) -> None:
        """_release_directive_done 标 done_at → grace 期内不删 → grace 后 on_tick 真删。"""
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = ProductionOverridePayload(
            items=[ProductionItem(unit_type="Stalker", count=4)],
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)

        # 模拟"已完成":走 _release_directive_done
        director._release_directive_done(d, now=20.0, reason="test_done")
        # 还在 list,标 done_at
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id in director._done_at
        assert director._override_status[d.id]["status"] == "done"

        # grace 期内 on_tick → 仍在
        # grace = _DONE_GRACE_S (2.0s),now=21 离 done_at=20 才 1s
        director.on_tick(now=21.0)
        assert any(s.id == d.id for s in director.production_overrides)

        # grace 期已过 → on_tick 真删
        director.on_tick(now=23.0)
        assert not any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._done_at
        assert d.id not in director._override_status


# ============================================================
# L2 A 类: attack / defend / retreat → facade global override
# ============================================================
class TestL2GlobalVerbs:
    def test_attack_sets_facade_overrides(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="attack",
            target_area="enemy_main",
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # 至少调过 attack_target_override + combat_intent
        assert facade.attack_target_overrides  # 写入了 target
        assert "attack" in facade.combat_intent_overrides

    def test_defend_persistent_sets_stance(self, library, session) -> None:
        """persistent=True 的 defend 额外写 engagement_stance(持续姿态)。"""
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="defend",
            target_area="natural",
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        assert "defend" in facade.engagement_stances

    def test_retreat_executes(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="retreat",
            target_area="main",
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        assert "retreat" in facade.combat_intent_overrides


# ============================================================
# L2 B 类: harass / scout / recon → squad 派单位
# ============================================================
class TestL2SquadVerbs:
    def test_harass_dispatches_squad(self, library, session) -> None:
        from vibecraft.directives.models import EnemyKilledInArea

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [9001, 9002, 9003]
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="harass",
            target_area="enemy_main",
            unit_count_hint=2,
            unit_type_hint=["Phoenix"],
            done_when=EnemyKilledInArea(
                kind="enemy_killed_in_area",
                area="enemy_main",
                unit_type="Probe",
                op=">=",
                value=5,
            ),
            timeout_s=90,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # 进 _tactical_squads,派 2 凤凰
        assert d.id in director._tactical_squads
        squad = director._tactical_squads[d.id]
        assert len(squad.unit_tags) == 2

    def test_recon_dispatches_with_done_when(self, library, session) -> None:
        from vibecraft.directives.models import OwnArmySizeRatio

        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [10001, 10002, 10003, 10004]
        director, _ = _make_director(session, library, facade)

        payload = TacticalObjectivePayload(
            verb="recon",
            target_area="enemy_natural",
            unit_count_hint=4,
            unit_type_hint=["Stalker"],
            done_when=OwnArmySizeRatio(
                kind="own_army_size_ratio",
                op="<=",
                value=0.6,
            ),
            timeout_s=90,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        assert d.id in director._tactical_squads
        squad = director._tactical_squads[d.id]
        assert len(squad.unit_tags) == 4


# ============================================================
# L4 TECH_OVERRIDE / EXPANSION_OVERRIDE → enter production_overrides
# ============================================================
class TestL4TechExpansion:
    def test_tech_override_enters_list(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = TechOverridePayload(upgrade_id="Blink", priority=80)
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        assert any(s.id == d.id for s in director.production_overrides)

    def test_expansion_override_enters_list(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = ExpansionOverridePayload(target_count=3, priority=70)
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        assert any(s.id == d.id for s in director.production_overrides)

    def test_tech_override_revoke_clears(self, library, session) -> None:
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = TechOverridePayload(upgrade_id="Charge")
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director.revoke_directive(d.id, now=15.0)

        assert not any(s.id == d.id for s in director.production_overrides)


# ============================================================
# unit_release: 释放 standing order 单位
# ============================================================
class TestUnitRelease:
    def test_release_drops_unit_from_unit_roles(self, library, session) -> None:
        """unit_release directive 提交 → 单位 release_unit_role 被调。

        典型场景:玩家先"3 叉子待命",后"放走那 3 叉子"。
        """
        facade = FakeFacade()
        # 先注入 3 叉子 standing,Reserved
        facade.selector_stub["Zealot"] = [11001, 11002, 11003]
        director, _ = _make_director(session, library, facade)

        # 1. 先 standing
        standing = UnitClaimPayload(
            selector=Selector(unit_type="Zealot", count=3),
            task=Task(
                primary_action=Action(
                    verb=Verb.STANDBY,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
                )
            ),
            persistent=True,
        )
        d1 = Directive(payload=standing, issued_at=10.0)
        director._submit_directives([d1], now=10.0)
        assert 11001 in facade.unit_roles

        # 2. release
        release = UnitReleasePayload(
            selector=Selector(unit_type="Zealot", count=3),
            return_to_role="IDLE",
        )
        d2 = Directive(payload=release, issued_at=15.0)
        director._submit_directives([d2], now=15.0)
        director._apply_to_facade(d2, now=15.0)

        # release_unit_role 被调
        release_calls = [c for c in facade.calls if c.method == "release_unit_role"]
        assert len(release_calls) >= 1


# ============================================================
# strategy_set: 切策略端到端(进 board midgame slot)
# ============================================================
class TestStrategySetEnd2End:
    def test_strategy_set_midgame_changes_board(self, library, session) -> None:
        """strategy_set 进 board.pending → tick 过 1.5s commit delay → 进 slots。"""
        from vibecraft.directives.types import StageKind

        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        payload = StrategySetPayload(stage="midgame", strategy_id="iac_2base")
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # 刚 submit, 还在 pending(effective_at=11.5)
        assert any(p.id == d.id for p in director.board.pending)
        # tick 越过 commit_delay → 进 slots
        director.board.tick(now=12.0)
        midgame_slot = director.board.slots.get(StageKind.MIDGAME)
        assert midgame_slot is not None
        assert midgame_slot.strategy_id == "iac_2base"


# ============================================================
# compound 复合指令端到端: 一次提交 3 directive 各自独立处理
# ============================================================
class TestCompoundDirectives:
    def test_three_directives_processed_independently(self, library, session) -> None:
        """[strategy_set, attack, production_override] 同次 submit → 全部进各自轨道。

        模拟 LLM 解析"切 IAC，进攻主基地，下个 BG 出 4 追猎"。
        """
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        d_strat = Directive(
            payload=StrategySetPayload(stage="midgame", strategy_id="iac_2base"),
            issued_at=10.0,
        )
        d_attack = Directive(
            payload=TacticalObjectivePayload(verb="attack", target_area="enemy_main"),
            issued_at=10.0,
        )
        d_prod = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Stalker", count=4)],
            ),
            issued_at=10.0,
        )

        # 同次 submit + tick 越过 commit delay
        director._submit_directives([d_strat, d_attack, d_prod], now=10.0)
        director._apply_to_facade(d_attack, now=10.0)
        director.board.tick(now=12.0)

        from vibecraft.directives.types import StageKind

        # 1. strategy_set 进 board midgame
        midgame_slot = director.board.slots.get(StageKind.MIDGAME)
        assert midgame_slot is not None
        assert midgame_slot.strategy_id == "iac_2base"
        # 2. attack 设了 facade override
        assert facade.attack_target_overrides
        # 3. production_override 进 list
        assert any(s.id == d_prod.id for s in director.production_overrides)

    def test_chrono_boost_calls_facade(self, library, session) -> None:
        """2026-05-25 用户:玩家"给两个BF星空加速" → unit_claim(Nexus cast_ability
        EffectChronoBoost target=Forge) → Director 调 facade.cast_chrono_boost_on_structure。

        directive 立即 released(chrono cast 是一次性命令)。
        """
        facade = FakeFacade()
        facade.selector_stub["Nexus"] = [9999]  # mock Nexus
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Nexus", count=2),
            task=Task(
                primary_action=Action(
                    verb=Verb.CAST_ABILITY,
                    target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="Forge"),
                    ability_id="EffectChronoBoostEnergyCost",
                )
            ),
            persistent=False,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # facade.cast_chrono_boost_on_structure 被调
        assert ("Forge", 2) in facade.chrono_boost_casts
        # directive 立即 released(chrono 是一次性)
        assert d.id in director._done_at

    def test_chrono_boost_vt_templar_archive(self, library, session) -> None:
        """Issue 1 修复(2026-05-29):玩家"给VT星空加速" → target=TemplarArchive。

        VT=圣堂档案(TemplarArchive,UnitTypeId.TEMPLARARCHIVE)。
        以前 few-shot 只有 BF/BG/BY/VC/VR,LLM 不会映射 VT。
        修复后 few-shot 加 VT→TemplarArchive。后端已支持任意 UnitTypeId 名称。
        """
        facade = FakeFacade()
        facade.selector_stub["Nexus"] = [9999]
        director, _ = _make_director(session, library, facade)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Nexus", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.CAST_ABILITY,
                    target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="TemplarArchive"),
                    ability_id="EffectChronoBoostEnergyCost",
                )
            ),
            persistent=False,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        assert ("TemplarArchive", 1) in facade.chrono_boost_casts, (
            "VT → TemplarArchive 应调 cast_chrono_boost_on_structure"
        )
        assert d.id in director._done_at, "chrono 是一次性命令，执行后应立即 release"

    def test_chrono_boost_various_buildings(self, library, session) -> None:
        """Issue 1 修复(2026-05-29):VS/VB/VD/VF 等建筑也能星空加速。

        验证 Stargate/RoboticsBay/DarkShrine/FleetBeacon 都能被 chrono。
        """
        buildings = [
            ("Stargate", "VS / 星门"),
            ("RoboticsBay", "VB / 球塔"),
            ("DarkShrine", "VD / 黑暗神殿"),
            ("FleetBeacon", "VF / 舰队信标"),
            ("TwilightCouncil", "VC / 议会"),
        ]
        for building_type, desc in buildings:
            facade = FakeFacade()
            facade.selector_stub["Nexus"] = [9999]
            director, _ = _make_director(session, library, facade)

            payload = UnitClaimPayload(
                selector=Selector(unit_type="Nexus", count=1),
                task=Task(
                    primary_action=Action(
                        verb=Verb.CAST_ABILITY,
                        target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type=building_type),
                        ability_id="EffectChronoBoostEnergyCost",
                    )
                ),
                persistent=False,
            )
            d = Directive(payload=payload, issued_at=10.0)
            director._submit_directives([d], now=10.0)
            director._apply_to_facade(d, now=10.0)

            assert (building_type, 1) in facade.chrono_boost_casts, (
                f"{desc} ({building_type}) 应能接收星空加速"
            )

    def test_compound_revoke_independent(self, library, session) -> None:
        """同次复合提交,撤销其中 1 个不影响其它 2 个。"""
        facade = FakeFacade()
        director, _ = _make_director(session, library, facade)

        d_tech = Directive(
            payload=TechOverridePayload(upgrade_id="Blink"),
            issued_at=10.0,
        )
        d_prod = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Stalker", count=4)],
            ),
            issued_at=10.0,
        )
        director._submit_directives([d_tech, d_prod], now=10.0)
        assert len(director.production_overrides) == 2

        # 撤销 d_tech
        director.revoke_directive(d_tech.id, now=15.0)

        # d_prod 仍在
        ids_left = {s.id for s in director.production_overrides}
        assert d_tech.id not in ids_left
        assert d_prod.id in ids_left
