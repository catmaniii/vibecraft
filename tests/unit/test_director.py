"""Director 单测：用 FakeFacade + MockLLMProvider 完整 mock。

覆盖：
- on_player_command + tick 推进 → facade 收到正确调用
- strategy_set → facade.set_build()
- production_override → facade.set_production_override()
- engagement_constraint → facade.set_engagement_stance()
- unit_claim → facade.set_unit_role(LLM_CONTROLLED) + execute_unit_action
- unit_release → facade.set_unit_role(IDLE/ARMY)
- view_move → facade.move_camera 立即（不走 Board）
- ParseError → facade 不变 (设计文档 §7.6)
- ParseContext 从 facade.get_state() + board.overlays 正确构造
- standing_orders 路由 + revoke（P1.2）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vibecraft.bot import BotState, Director, FakeFacade, UnitRole
from vibecraft.directives.models import Directive
from vibecraft.llm import (
    IntentParser,
    MockLLMProvider,
    ProviderResponse,
)
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _make_director(
    library: StrategyLibrary,
    session: GameSession,
    facade: FakeFacade,
    provider_response: dict,
    my_race: str = "protoss",
) -> Director:
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw=provider_response,
                input_tokens=100,
                output_tokens=20,
                latency_ms=10.0,
            )
        ]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    # 两层架构（2026-05-19）：Director 也传 library 才能调 pick_best_persistent
    return Director(facade=facade, parser=parser, session=session, library=library)


# =========================================================================
# strategy_set 全链路
# =========================================================================


class TestStrategySetDispatch:
    @pytest.mark.asyncio
    async def test_strategy_set_calls_facade_set_build(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切到 IAC",
                "confidence": 0.95,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )

        await director.on_player_command("切 IAC", now=100.0)
        # 2026-05-26 default commit_delay=0,玩家动作立即 commit(下一 tick 处理)
        director.on_tick(now=100.5)
        assert facade.builds == ["iac_2base"]


class TestUnitClaimDispatch:
    @pytest.mark.asyncio
    async def test_unit_claim_sets_role_and_executes_primary(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(state=BotState(game_time=200.0))
        facade.selector_stub["Phoenix"] = [12345, 12346]
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "凤凰举不朽",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "unit_claim",
                        "payload": {
                            "selector": {"unit_type": "Phoenix"},
                            "task": {
                                "primary_action": {
                                    "verb": "lift_target",
                                    "target": {
                                        "kind": "unit_type",
                                        "unit_type": "Immortal",
                                    },
                                }
                            },
                        },
                    }
                ],
            },
        )
        await director.on_player_command("凤凰举不朽", now=200.0)
        director.on_tick(now=202.0)

        assert facade.unit_roles == {
            12345: UnitRole.LLM_CONTROLLED,
            12346: UnitRole.LLM_CONTROLLED,
        }
        assert len(facade.unit_actions) == 2
        assert all(a["verb"] == "lift_target" for a in facade.unit_actions)


class TestEngagementDispatch:
    @pytest.mark.asyncio
    async def test_engagement_constraint_dispatches(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "守家",
                "confidence": 0.95,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "defend"}}],
            },
        )
        await director.on_player_command("守家", now=50.0)
        director.on_tick(now=52.0)
        assert facade.engagement_stances == ["defend"]


class TestProductionDispatch:
    @pytest.mark.asyncio
    async def test_production_override(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "下个 BG 出俩哨兵",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "production_override",
                        "payload": {"items": [{"unit_type": "Sentry", "count": 2}]},
                        "priority": 70,
                    }
                ],
            },
        )
        await director.on_player_command("出俩哨兵", now=10.0)
        director.on_tick(now=12.0)
        # P2: PRODUCTION_OVERRIDE 进 Director.production_overrides list,不再走
        # facade dispatch (P3 task_monitor 才接 sharpy 实际生产 wire)。
        assert len(director.production_overrides) == 1
        assert facade.production_overrides == []


class TestActivateWhenGate:
    """2026-05-28 用户:directive.activate_when 激活门 — commit 后等条件再激活。

    典型:"1 攻好了再进攻" → tactical_objective(verb=attack,
       activate_when=tech_done(GroundWeaponsLevel1))
       intent 不立即变 attack,直到 +1 完成。
    """

    def test_compare_op_basic(self) -> None:
        from vibecraft.bot import Director

        assert Director._compare_op(2, ">=", 2) is True
        assert Director._compare_op(1, ">=", 2) is False
        assert Director._compare_op(3, ">", 2) is True
        assert Director._compare_op(2, "==", 2) is True
        assert Director._compare_op(2, "!=", 3) is True

    def test_activation_none_always_satisfied(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        director = _make_director(library, session, FakeFacade(), {})
        assert director._is_activation_satisfied(None) is True

    def test_activation_structure_count_via_mock_bot(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """activate_when={kind:structure_count, value:6} — mock bot 有 6 BG → True"""
        from unittest.mock import MagicMock

        from sc2.ids.unit_typeid import UnitTypeId

        director = _make_director(library, session, FakeFacade(), {})
        bot = MagicMock()
        gw_units = MagicMock()
        gw_units.ready.amount = 6
        bot.structures = lambda t: (
            gw_units if t == UnitTypeId.GATEWAY else MagicMock(ready=MagicMock(amount=0))
        )
        director._bot = bot

        cond = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 6}
        assert director._is_activation_satisfied(cond) is True
        cond2 = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 7}
        assert director._is_activation_satisfied(cond2) is False

    def test_activation_tech_done_via_mock_bot(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from unittest.mock import MagicMock

        from sc2.ids.upgrade_id import UpgradeId

        director = _make_director(library, session, FakeFacade(), {})
        bot = MagicMock()
        bot.state.upgrades = {UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1}
        director._bot = bot

        cond = {"kind": "tech_done", "upgrade_id": "ProtossGroundWeaponsLevel1"}
        assert director._is_activation_satisfied(cond) is True
        cond2 = {"kind": "tech_done", "upgrade_id": "BlinkTech"}
        assert director._is_activation_satisfied(cond2) is False

    def test_activation_unsupported_kind_defaults_false(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """2026-06-06:真·未知 activate_when kind → 默认**不激活**(防没实现的门被当场放行,
        如 gateway 没等 pylon 就修)。"""
        from unittest.mock import MagicMock

        director = _make_director(library, session, FakeFacade(), {})
        director._bot = MagicMock()
        cond = {"kind": "some_unimplemented_future_kind", "value": 5}
        assert director._is_activation_satisfied(cond) is False

    def test_chain_structure_ready_gate(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """chain_structure_ready:链上还没记下建筑 → 不激活;记下的建筑 tag 已 ready → 激活。"""
        from unittest.mock import MagicMock

        director = _make_director(library, session, FakeFacade(), {})
        bot = MagicMock()
        director._bot = bot
        cond = {"kind": "chain_structure_ready", "chain_id": "c1"}

        # 1) 链上没记下建筑 → 不激活
        assert director._is_activation_satisfied(cond) is False

        # 2) 链记下 pylon tag 999,但它还没 ready(场上 ready 不含 999) → 不激活
        director._chain_structures["c1"] = {999}
        bot.structures.ready = [MagicMock(tag=111)]
        assert director._is_activation_satisfied(cond) is False

        # 3) 999 进入 ready → 激活
        bot.structures.ready = [MagicMock(tag=999), MagicMock(tag=111)]
        assert director._is_activation_satisfied(cond) is True

    def test_activate_when_defers_then_activates(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """完整 lifecycle:tactical_objective + activate_when=tech_done(blink) →
        - blink 未完成时:不调 facade.set_combat_intent_override(intent 不变)
        - blink 完成 + on_tick 触发 → 走 _apply_to_facade → set intent。
        """
        from unittest.mock import MagicMock

        from sc2.ids.upgrade_id import UpgradeId

        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import (
            Directive,
            TacticalObjectivePayload,
        )
        from vibecraft.directives.types import IssuedBy

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        bot.state.upgrades = set()  # blink 未完成
        bot.time = 100.0
        director._bot = bot
        director.board = DirectiveBoard(commit_delay_s=0.0)

        d = Directive(
            payload=TacticalObjectivePayload(
                verb="attack",
                persistent=True,
                activate_when={"kind": "tech_done", "upgrade_id": "BlinkTech"},
            ),
            issued_at=10.0,
            issued_by=IssuedBy.VOICE,
        )
        director._submit_directives([d], now=10.0)
        director.on_tick(now=10.5)
        # blink 未完成 → intent 不应被设
        assert d.id in director._pending_activation
        assert facade.combat_intent_overrides == []

        # blink 完成
        bot.state.upgrades = {UpgradeId.BLINKTECH}
        director.on_tick(now=15.0)
        # 应激活,intent 被 set
        assert d.id not in director._pending_activation
        assert facade.combat_intent_overrides[-1] == "attack"

    def test_persistent_unit_claim_respects_activate_when(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """2026-06-06 真局 bug:带 activate_when 的 **persistent unit_claim** 必须先过激活门,
        不能在 submit 时立即 _assign(否则链式第二步"等农民到A再去B"在提交时就发了"去B",
        被第一步覆盖丢掉 → 农民到A后干站)。blink 未完成 → 挂 pending;完成 → 激活。"""
        from unittest.mock import MagicMock

        from sc2.ids.upgrade_id import UpgradeId

        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        bot.state.upgrades = set()  # 未完成
        bot.time = 100.0
        director._bot = bot
        director.board = DirectiveBoard(commit_delay_s=0.0)

        payload = UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_right"),
                )
            ),
            persistent=True,
            activate_when={"kind": "tech_done", "upgrade_id": "BlinkTech"},
        )
        d = Directive(payload=payload, issued_at=100.0)
        director._submit_directives([d], now=100.0)
        director.on_tick(now=100.5)
        # 未满足 → 挂 pending,**没**立即执行(facade 没收到 unit action)
        assert d.id in director._pending_activation
        assert facade.unit_actions == []

        # 满足 → 激活(离开 pending)
        bot.state.upgrades = {UpgradeId.BLINKTECH}
        director.on_tick(now=101.0)
        assert d.id not in director._pending_activation


class TestDedupeDirectives:
    """2026-05-28 用户:LLM 偶尔 emit 重复 directive(同升级仅大小写不同等),
    Director._dedupe_directives 在 submit 前去重。"""

    def _make_dir(self):
        return _make_director(
            None,
            None,
            None,
            {},
            parse_error=False,  # type: ignore[arg-type]
        )

    def test_tech_override_dedupe_case_insensitive(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """重现 2026-05-28 bug:玩家'升级1防' LLM 同时 emit Camel + UPPER 两条
        tech_override → 两张卡片重复。dedupe 后只剩 1 条。"""
        from vibecraft.directives.models import Directive, TechOverridePayload

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
        )
        d1 = Directive(
            payload=TechOverridePayload(upgrade_id="ProtossGroundArmorsLevel1"),
            issued_at=10.0,
        )
        d2 = Directive(
            payload=TechOverridePayload(upgrade_id="PROTOSSGROUNDARMORSLEVEL1"),
            issued_at=10.0,
        )
        out = director._dedupe_directives([d1, d2])
        assert len(out) == 1, f"两条同 upgrade(仅大小写不同)应去重剩 1,实际 {len(out)}"
        # 保留第一条
        assert out[0].payload.upgrade_id == "ProtossGroundArmorsLevel1"

    def test_tech_override_different_upgrades_keep_both(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """真正不同的升级不被误 dedupe。"""
        from vibecraft.directives.models import Directive, TechOverridePayload

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
        )
        d1 = Directive(
            payload=TechOverridePayload(upgrade_id="ProtossGroundArmorsLevel1"),
            issued_at=10.0,
        )
        d2 = Directive(
            payload=TechOverridePayload(upgrade_id="ProtossGroundWeaponsLevel1"),
            issued_at=10.0,
        )
        out = director._dedupe_directives([d1, d2])
        assert len(out) == 2

    def test_structure_override_dedupe_by_type(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """重复 structure_override(同 structure_type)dedupe。"""
        from vibecraft.directives.models import (
            Directive,
            StructureItem,
            StructureOverridePayload,
        )

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
        )
        d1 = Directive(
            payload=StructureOverridePayload(
                items=[StructureItem(structure_type="Forge", target_count=2)]
            ),
            issued_at=10.0,
        )
        d2 = Directive(
            payload=StructureOverridePayload(
                items=[
                    StructureItem(structure_type="forge", delta=1)  # 同 type 不同 case
                ]
            ),
            issued_at=10.0,
        )
        out = director._dedupe_directives([d1, d2])
        assert len(out) == 1

    def test_other_types_passthrough(self, library: StrategyLibrary, session: GameSession) -> None:
        """tactical_objective 等不去重(玩家可能就要多条)。"""
        from vibecraft.directives.models import Directive, TacticalObjectivePayload

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
        )
        d1 = Directive(
            payload=TacticalObjectivePayload(verb="attack"),
            issued_at=10.0,
        )
        d2 = Directive(
            payload=TacticalObjectivePayload(verb="retreat"),
            issued_at=10.0,
        )
        out = director._dedupe_directives([d1, d2])
        assert len(out) == 2


class TestStructureDeltaResolve:
    """structure_override delta 解算 + done_when 语义。

    2026-06-02 实测 bug:"补一个by"(delta=1)done_when 被 LLM 设成 count>=1,
    delta 解算只改 target_count 没改 done_when → 已有 1 个 BY 时 count>=1 立刻
    满足 → directive 秒 done 不建造。修复:delta 项 done_when 改成
    structure_count_built_since(数新建成个数,损毁免疫);target 项保持 structure_count。
    """

    def _director(self, session: GameSession, ready_counts: dict[str, int] | None = None):
        from unittest.mock import MagicMock

        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        bot = MagicMock()
        rc = ready_counts or {}

        def _structures(type_id):
            res = MagicMock()
            res.ready.amount = rc.get(getattr(type_id, "name", str(type_id)), 0)
            return res

        bot.structures.side_effect = _structures
        director._bot = bot
        return director

    def test_delta_done_when_becomes_built_since(self, session: GameSession) -> None:
        """补一个by(delta=1),已有 1 个 → target_count=2(执行触发)+ done_when 改成
        built_since>=1(数新建成,不会因已有1个就秒 done)。"""
        director = self._director(session, ready_counts={"CYBERNETICSCORE": 1})
        d = Directive.model_validate(
            {
                "payload": {
                    "type": "structure_override",
                    "items": [{"structure_type": "CyberneticsCore", "delta": 1}],
                    "done_when": {
                        "kind": "structure_count",
                        "structure_type": "CyberneticsCore",
                        "op": ">=",
                        "value": 1,
                    },
                },
                "issued_at": 1.0,
            }
        )
        out = director._resolve_structure_delta(d)
        item = out.payload.items[0]
        assert item.target_count == 2 and item.delta is None  # ready1 + delta1
        dw = out.payload.done_when
        assert dw.kind == "structure_count_built_since"  # 数新建成,非绝对总数
        assert dw.value == 1
        assert dw.structure_type == "CyberneticsCore"

    def test_target_done_when_stays_structure_count(self, session: GameSession) -> None:
        """出到两个by(target=2)无 delta → 原样返回,done_when 保持 count>=2(数总数)。"""
        director = self._director(session)
        d = Directive.model_validate(
            {
                "payload": {
                    "type": "structure_override",
                    "items": [{"structure_type": "CyberneticsCore", "target_count": 2}],
                    "done_when": {
                        "kind": "structure_count",
                        "structure_type": "CyberneticsCore",
                        "op": ">=",
                        "value": 2,
                    },
                },
                "issued_at": 1.0,
            }
        )
        out = director._resolve_structure_delta(d)
        dw = out.payload.done_when
        assert dw.kind == "structure_count"  # 总数语义保持
        assert dw.value == 2

    def test_card_delta_shows_xinzeng(self, session: GameSession) -> None:
        """delta(built_since done_when)卡片显示"新增 N 个"。"""
        from vibecraft.directives.models import (
            StructureCountBuiltSince,
            StructureItem,
            StructureOverridePayload,
        )

        director = self._director(session)
        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="Gateway", target_count=8)],
            done_when=StructureCountBuiltSince(
                kind="structure_count_built_since",
                structure_type="Gateway",
                op=">=",
                value=2,
            ),
        )
        disp = director._format_production_override_display(payload)
        assert disp.startswith("新增") and "2 个" in disp

    def test_card_target_shows_buqi(self, session: GameSession) -> None:
        """target(structure_count done_when)卡片显示"补齐到 N 个"。"""
        from vibecraft.directives.models import (
            StructureCount,
            StructureItem,
            StructureOverridePayload,
        )

        director = self._director(session)
        payload = StructureOverridePayload(
            items=[StructureItem(structure_type="Gateway", target_count=10)],
            done_when=StructureCount(
                kind="structure_count",
                structure_type="Gateway",
                op=">=",
                value=10,
            ),
        )
        disp = director._format_production_override_display(payload)
        assert "补齐到 10 个" in disp


# =========================================================================
# ParseError 不动 bot 状态 (§7.6)
# =========================================================================


class TestParseErrorIsNoop:
    @pytest.mark.asyncio
    async def test_unknown_strategy_does_not_change_facade(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "...",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "nope_typo"},
                    }
                ],
            },
        )
        from vibecraft.llm import ParseError

        outcome = await director.on_player_command("...", now=10.0)
        assert isinstance(outcome, ParseError)
        director.on_tick(now=12.0)
        assert facade.builds == []
        # WP-A: on_tick 末尾每 tick 调 set_debug_marks（纯 debug draw，不影响 bot 操作状态）
        # 排除 debug draw 相关调用，仅断言 bot 操作层面无副作用
        non_debug_calls = [c for c in facade.calls if c.method != "set_debug_marks"]
        assert non_debug_calls == []


# =========================================================================
# ParseContext 构造
# =========================================================================


class TestParseContextBuilding:
    @pytest.mark.asyncio
    async def test_context_pulls_from_facade_state(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(
            state=BotState(
                game_time=300.0,
                minerals=800,
                gas=250,
                supply_used=42,
                supply_cap=50,
                expansion_count=3,
                army_summary={"Stalker": 12, "Sentry": 4},
                enemy_summary={"Marine": 8},
            )
        )
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "ok", "confidence": 0.9, "directives": []},
        )

        ctx = director.build_parse_context(now=300.0)
        assert ctx.minerals == 800
        assert ctx.gas == 250
        assert ctx.expansion_count == 3
        assert ctx.army_summary == {"Stalker": 12, "Sentry": 4}
        assert ctx.enemy_summary == {"Marine": 8}

    @pytest.mark.asyncio
    async def test_context_includes_recent_commands(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "ok", "confidence": 0.9, "directives": []},
        )
        await director.on_player_command("第一句", now=10.0)
        ctx = director.build_parse_context(now=11.0)
        assert "第一句" in ctx.recent_commands


# =========================================================================
# 多 directive 复合句
# =========================================================================


class TestCompoundCommands:
    @pytest.mark.asyncio
    async def test_compound_strategy_plus_engagement(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切剧本然后守家",
                "confidence": 0.92,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    },
                    {
                        "type": "engagement_constraint",
                        "payload": {"stance": "defend"},
                    },
                ],
            },
        )
        await director.on_player_command("切 IAC，守家", now=10.0)
        director.on_tick(now=12.0)
        assert facade.builds == ["iac_2base"]
        assert facade.engagement_stances == ["defend"]


# =========================================================================
# Logging 副作用
# =========================================================================


class TestLoggingIntegration:
    @pytest.mark.asyncio
    async def test_committed_event_logged(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "...",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("...", now=10.0)
        director.on_tick(now=12.0)
        events = session.get_null_records(LogStream.EVENTS)
        kinds = [e["kind"] for e in events]
        assert "directive.committed" in kinds
        assert "strategy.set" in kinds

    @pytest.mark.asyncio
    async def test_directives_stream_has_submitted(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """_submit_directives 应向 directives.jsonl 写 submitted 记录。"""
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切 IAC",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("切 IAC", now=10.0)
        records = session.get_null_records(LogStream.DIRECTIVES)
        events = [r["event"] for r in records]
        assert "submitted" in events

    @pytest.mark.asyncio
    async def test_directives_stream_has_committed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """tick 到 effective_at 后 directives.jsonl 应有 committed 记录。"""
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切 IAC",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("切 IAC", now=10.0)
        director.on_tick(now=12.0)
        records = session.get_null_records(LogStream.DIRECTIVES)
        events = [r["event"] for r in records]
        assert "submitted" in events
        assert "committed" in events

    def test_directives_stream_submitted_on_direct_submit(self, session: GameSession) -> None:
        """Director(session=mock_session) 构造 OK；_submit_directives 时 session.log 被 called。"""
        from unittest.mock import MagicMock

        from vibecraft.directives.models import ProductionOverridePayload
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_session = MagicMock()
        director = Director(facade=facade, parser=parser, session=mock_session)

        # 直接 submit 一个 directive
        from vibecraft.directives.models import Directive, ProductionItem

        payload = ProductionOverridePayload(items=[ProductionItem(unit_type="Stalker", count=3)])
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # session.log 应该被调用（写 directives.jsonl）
        mock_session.log.assert_called()
        # 验证第一个 call 是 DIRECTIVES stream，event=submitted
        from vibecraft.logging_ import LogStream

        call_args = mock_session.log.call_args_list[0]
        assert call_args[0][0] == LogStream.DIRECTIVES
        record = call_args[0][1]
        assert record["event"] == "submitted"


# =========================================================================
# P1.2 Standing Order 路由（persistent=True → standing_orders；False → _in_flight）
# =========================================================================


def _make_unit_claim_directive(persistent: bool) -> Directive:
    """构造一个 UNIT_CLAIM Directive，persistent 按参数。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type="Phoenix"),
        task=Task(
            primary_action=Action(
                verb=Verb.LIFT_TARGET,
                target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="Immortal"),
            )
        ),
        persistent=persistent,
    )
    return Directive(payload=payload, issued_at=10.0)


@pytest.fixture
def director(session: GameSession) -> Director:
    """最小 Director 实例，不需要 LLM provider（直接调 _submit_directives）。"""
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session)


class TestCameraInjection:
    """2026-06-06 真局 bug:camera 类连续指令(在这里修水晶)条件里的 'camera' 没被注入坐标
    → 每帧刷 'camera unknown' + 链断。修:done_when/activate_when 的 area + build_at 位置也注入。"""

    def test_inject_camera_into_conditions_and_build_at(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from vibecraft.directives.models import BuildAtPayload, Directive, MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec

        director = _make_director(library, session, FakeFacade(), {})
        move = Directive(
            payload=MovePayload(
                selector=Selector(unit_type="Probe", count=1),
                target=TargetSpec(kind=TargetKind.CAMERA),
                done_when={"kind": "unit_arrived", "area": "camera", "within_grid": 5.0},
            ),
            issued_at=10.0,
        )
        build = Directive(
            payload=BuildAtPayload(
                structure_type="Pylon",
                by_probe=True,
                activate_when={"kind": "unit_arrived", "area": "camera", "within_grid": 5.0},
            ),
            issued_at=10.0,
        )
        director._inject_camera_point([move, build], (100.0, 200.0))

        # move 的 target + done_when.area 都注入了坐标
        assert move.payload.target.point == (100.0, 200.0)
        assert move.payload.done_when.area == "(100.0, 200.0)"
        # build_at 位置 + activate_when.area 都注入了
        assert build.payload.point == (100.0, 200.0)
        assert build.payload.activate_when.area == "(100.0, 200.0)"


class TestGroupActivateAndRevoke:
    """2026-06-06 真局 bug:群组命令的 activate_when=unit_arrived 用队伍重心(不是只查农民);
    未激活灰卡可被 × 撤掉。"""

    def _group_attack_directive(self) -> Directive:
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        payload = UnitClaimPayload(
            selector=Selector(group_id=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.ATTACK_MOVE,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
                )
            ),
            persistent=True,
            activate_when={"kind": "unit_arrived", "area": "enemy_main_back", "within_grid": 5.0},
        )
        return Directive(payload=payload, issued_at=10.0)

    def test_activate_unit_arrived_uses_group_center(self, director: Director, monkeypatch) -> None:
        """5a:有 directive 上下文 → unit_arrived 判该指令(群组)单位重心,不是只查农民。"""
        from unittest.mock import MagicMock

        class _Pt:
            def __init__(self, dist: float) -> None:
                self._d = dist

            def distance_to(self, _p: object) -> float:
                return self._d

        director._bot = MagicMock()
        monkeypatch.setattr(director, "_resolve_selector_with_count", lambda sel: [301, 302])
        monkeypatch.setattr(director, "_resolve_target_area", lambda a: (100.0, 100.0))
        d = self._group_attack_directive()
        cond = {"kind": "unit_arrived", "area": "enemy_main_back", "within_grid": 5.0}

        # 重心进圈(距 5 < 半径 floor 12) → 激活
        units_near = MagicMock()
        units_near.__bool__ = lambda s: True
        units_near.center = _Pt(5.0)
        director._bot.units.tags_in.return_value = units_near
        assert director._is_activation_satisfied(cond, d) is True

        # 重心还远(50) → 不激活
        units_far = MagicMock()
        units_far.__bool__ = lambda s: True
        units_far.center = _Pt(50.0)
        director._bot.units.tags_in.return_value = units_far
        assert director._is_activation_satisfied(cond, d) is False

    def test_revoke_removes_pending_activation(self, director: Director) -> None:
        """5c:挂在 _pending_activation 的未激活灰卡,× 能撤掉。"""
        d = self._group_attack_directive()
        d.id = "ga1"
        director._pending_activation["ga1"] = d
        director.revoke_directive("ga1", now=10.0)
        assert "ga1" not in director._pending_activation


class _FakeWorker:
    """代理建造测试用最小 worker:tag + position(distance_to)+ is_idle。"""

    def __init__(self, tag: int, x: float, y: float, idle: bool = True) -> None:
        self.tag = tag
        self._x = x
        self._y = y
        self.is_idle = idle

    def distance_to(self, p: object) -> float:
        return ((self._x - p[0]) ** 2 + (self._y - p[1]) ** 2) ** 0.5  # type: ignore[index]


class TestProxyBuildHold:
    """2026-06-06 用户:代理建造农民必须脱离 bot 控制,全程持有到玩家×才放归;
    整条链(去X→修水晶→水晶好了修bg)用同一农民。"""

    def _build_at(self, did: str = "pb1") -> Directive:
        from vibecraft.directives.models import BuildAtPayload

        d = Directive(
            payload=BuildAtPayload(structure_type="Pylon", point=(10.0, 10.0), by_probe=True),
            issued_at=10.0,
        )
        d.id = did
        return d

    def test_pick_prefers_owned_worker_near_point(self, director: Director) -> None:
        """优先选'已被指令持有且离建造点近'的农民(链上同一农民),即便有更近的自由农民。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.workers = [_FakeWorker(1, 10, 10), _FakeWorker(2, 12, 12)]
        director._standing_order_tags = {"card1": {2}}  # tag 2 已被 card1 持有
        assert director._pick_proxy_build_probe((10.0, 10.0)) == 2

    def test_pick_falls_back_closest_when_none_owned(self, director: Director) -> None:
        """没有已持有农民 → 退选离建造点最近的。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.workers = [_FakeWorker(1, 10, 10), _FakeWorker(2, 50, 50)]
        director._standing_order_tags = {}
        assert director._pick_proxy_build_probe((11.0, 11.0)) == 1

    def test_tick_probe_death_closes_card(self, director: Director) -> None:
        """农民死 → 关卡(units_lost)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.units.by_tag.return_value = None
        d = self._build_at("pb1")
        director._in_flight["pb1"] = d
        director._pending_proxy_build["pb1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Pylon",
            "since": 0.0,
        }
        director._tick_proxy_build(now=10.0)
        assert "pb1" not in director._pending_proxy_build
        assert director._override_status.get("pb1", {}).get("status") == "done"

    def test_tick_built_keeps_probe_held_not_released(self, director: Director) -> None:
        """已开始建造 → 停止重发,但农民继续被持有(不放归;玩家×才放)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.units.by_tag.return_value = MagicMock(is_idle=False)
        director._bot.structures.return_value.closer_than.return_value.exists = True
        director._pending_proxy_build["pb1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Pylon",
            "since": 0.0,
        }
        director._standing_order_tags["pb1"] = {5}
        director._tick_proxy_build(now=10.0)
        assert "pb1" not in director._pending_proxy_build
        assert director._standing_order_tags.get("pb1") == {5}
        assert director.facade.unit_roles.get(5) != UnitRole.IDLE

    def test_settle_ignores_preexisting_structure(self, director: Director) -> None:
        """回归(2026-06-07 玩家报"在某点造 VS,卡片秒变已完成"):

        农民从家出发、还没到工地时,身边/途经家里已有的同类建筑(虚空开矿家里 2 个 VS)。
        旧代码 closer_than(3.5,农民位置)命中旧 VS → 误判 settle → 秒"完成"。
        修:发起时快照已有同类 tag,settle 排除它们,只认新出现的建筑。
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        class _FU(list):
            def filter(self, pred):
                return _FU([u for u in self if pred(u)])

            def closer_than(self, r, pos):
                return _FU(
                    [
                        u
                        for u in self
                        if ((u.position.x - pos.x) ** 2 + (u.position.y - pos.y) ** 2) ** 0.5 < r
                    ]
                )

            @property
            def exists(self):
                return len(self) > 0

        old_vs = SimpleNamespace(
            tag=999, position=SimpleNamespace(x=10.0, y=10.0), distance_to=lambda p: 0.0
        )
        director._bot = MagicMock()
        director._bot.units.by_tag.return_value = SimpleNamespace(
            tag=5, position=SimpleNamespace(x=10.0, y=10.0), orders=[]
        )
        director._bot.structures.return_value = _FU([old_vs])  # 只有家里旧 VS
        director._bot.time = 10.0
        director._standing_order_tags["vs1"] = {5}
        director._pending_proxy_build["vs1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Stargate",
            "since": 0.0,
            "preexisting": {999},  # 发起时家里已有的 VS
        }
        director._tick_proxy_build(now=10.0)
        # 旧 VS 被排除 → 不误判 settle → 卡仍在 pending、未标 done
        assert "vs1" in director._pending_proxy_build
        assert director._override_status.get("vs1", {}).get("status") != "done"

    def test_settle_fires_on_new_structure(self, director: Director) -> None:
        """对照:新出现的同类建筑(tag 不在 preexisting)→ 正常 settle。"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        class _FU(list):
            def filter(self, pred):
                return _FU([u for u in self if pred(u)])

            def closer_than(self, r, pos):
                return _FU(
                    [
                        u
                        for u in self
                        if ((u.position.x - pos.x) ** 2 + (u.position.y - pos.y) ** 2) ** 0.5 < r
                    ]
                )

            @property
            def exists(self):
                return len(self) > 0

        old_vs = SimpleNamespace(
            tag=999, position=SimpleNamespace(x=10.0, y=10.0), distance_to=lambda p: 0.0
        )
        new_vs = SimpleNamespace(
            tag=1000, position=SimpleNamespace(x=10.0, y=10.0), distance_to=lambda p: 0.0
        )
        director._bot = MagicMock()
        director._bot.units.by_tag.return_value = SimpleNamespace(
            tag=5, position=SimpleNamespace(x=10.0, y=10.0), orders=[]
        )
        director._bot.structures.return_value = _FU([old_vs, new_vs])
        director._bot.time = 10.0
        director._standing_order_tags["vs1"] = {5}
        director._pending_proxy_build["vs1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Stargate",
            "since": 0.0,
            "preexisting": {999},
        }
        director._tick_proxy_build(now=10.0)
        # 新 VS settle → 从 pending 移除;农民被持有(standing_order)→ 卡保留待命
        assert "vs1" not in director._pending_proxy_build
        assert 1000 in director._proxy_claimed_structs

    def test_committed_chain_card_released_on_settle(self, director: Director) -> None:
        """回归(2026-06-07 玩家报"水晶/VS 修好卡片不消失"):

        链式 build_at 卡 activate 后进 `_committed_directives`(不是 `_in_flight`)。
        本卡不持有农民(农民由链头 standby 卡持有,无 `_standing_order_tags`)。
        建筑 settle → 必须查 `_committed_directives` 才找得到 directive → 标完成。
        旧代码只查 `_in_flight` → d=None → 永不 _release_directive_done → 卡死不消失。
        """
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.units.by_tag.return_value = MagicMock()  # 农民活着
        d = self._build_at("vs1")
        director._committed_directives["vs1"] = d  # 关键:在 committed,不在 in_flight
        director._pending_proxy_build["vs1"] = {
            "tag": 7,
            "point": (40.0, 60.0),
            "structure": "Stargate",
            "since": 0.0,
        }
        # 注意:不设 _standing_order_tags["vs1"](农民属链头卡)
        director._tick_proxy_build(now=12.0)
        assert "vs1" not in director._pending_proxy_build
        assert director._override_status.get("vs1", {}).get("status") == "done"


class TestPendingBuildReservations:
    """问题3:玩家代理建造(by_probe)资源优先 —— pending_build_reservations 返回需锁钱的建筑。"""

    def _build_at(self, did: str, structure: str, by_probe: bool) -> Directive:
        from vibecraft.directives.models import BuildAtPayload

        d = Directive(
            payload=BuildAtPayload(structure_type=structure, point=(10.0, 10.0), by_probe=by_probe),
            issued_at=10.0,
        )
        d.id = did
        return d

    def test_active_proxy_build_reserved(self, director: Director) -> None:
        """正在代理建造的建筑 → 进预留列表。"""
        director._pending_proxy_build["pb1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Stargate",
            "since": 0.0,
        }
        assert director.pending_build_reservations() == ["Stargate"]

    def test_chained_byprobe_pending_activation_reserved(self, director: Director) -> None:
        """链式 by_probe build_at 还在等前一步(挂 _pending_activation)→ 等待期也锁钱。"""
        director._pending_activation["ca1"] = self._build_at("ca1", "Stargate", by_probe=True)
        assert director.pending_build_reservations() == ["Stargate"]

    def test_non_byprobe_pending_not_reserved(self, director: Director) -> None:
        """非 by_probe(走 macro 自己花)→ 不锁,否则死锁。"""
        director._pending_activation["ca1"] = self._build_at("ca1", "Stargate", by_probe=False)
        assert director.pending_build_reservations() == []

    def test_combines_both_sources(self, director: Director) -> None:
        """正在建 + 链式等待 同时存在 → 两者都锁(覆盖'修水晶+等水晶好了修两VS')。"""
        director._pending_proxy_build["pb1"] = {
            "tag": 5,
            "point": (10.0, 10.0),
            "structure": "Pylon",
            "since": 0.0,
        }
        director._pending_activation["ca1"] = self._build_at("ca1", "Stargate", by_probe=True)
        director._pending_activation["ca2"] = self._build_at("ca2", "Stargate", by_probe=True)
        got = sorted(director.pending_build_reservations())
        assert got == ["Pylon", "Stargate", "Stargate"]


class TestProxyChainBuild:
    """代理建造链(先修水晶 → 在周围修 N 个建筑)的执行逻辑(mock,无 SC2)。
    2026-06-07:覆盖卡片创建/链复用同一农民/锚到 Pylon/水晶建好刷新后续坐标/激活与完成状态。
    需真实 SC2 的部分(农民真移动、建筑真修成)见 tests/e2e/test_proxy_chain_e2e.py。"""

    def _build_at(
        self,
        did: str,
        structure: str,
        *,
        chain_id: str | None = None,
        unit_arrived_area: str | None = None,
        point: tuple[float, float] | None = None,
        named_spot: str | None = None,
    ) -> Directive:
        from vibecraft.directives.models import BuildAtPayload

        aw: dict | None = None
        if chain_id is not None:
            aw = {"kind": "chain_structure_ready", "chain_id": chain_id}
        elif unit_arrived_area is not None:
            aw = {"kind": "unit_arrived", "area": unit_arrived_area, "within_grid": 5.0}
        d = Directive(
            payload=BuildAtPayload(
                structure_type=structure,
                by_probe=True,
                point=point,
                named_spot=named_spot,
                activate_when=aw,
            ),
            issued_at=10.0,
        )
        d.id = did
        return d

    # ---- 水晶建好那一刻刷新后续建筑坐标(核心新特性)----

    def test_assign_followup_spots_gives_distinct_points(self, director: Director) -> None:
        """水晶 settle → 本链两张 VS 卡的落点刷新成水晶周围**不同**的点(各占一边、能量场内)。"""
        cid = "proxy_x"
        director._task_chains[cid] = {5}
        vs1 = self._build_at("vs1", "Stargate", chain_id=cid, named_spot="forward")
        vs2 = self._build_at("vs2", "Stargate", chain_id=cid, named_spot="forward")
        director._pending_activation["vs1"] = vs1
        director._pending_activation["vs2"] = vs2

        director._assign_chain_followup_spots(cid, (100.0, 100.0))

        p1, p2 = vs1.payload.point, vs2.payload.point
        assert p1 is not None and p2 is not None
        assert p1 != p2  # 两个 VS 不同点,不撞同一格
        assert vs1.payload.named_spot is None and vs2.payload.named_spot is None
        # 都在水晶能量场(半径 6.5)内
        for p in (p1, p2):
            assert ((p[0] - 100.0) ** 2 + (p[1] - 100.0) ** 2) ** 0.5 <= 6.5

    def test_assign_followup_only_touches_same_chain_byprobe(self, director: Director) -> None:
        """只刷新本链 by_probe build_at;别的链 / 非 by_probe 不动。"""
        cid = "proxy_a"
        director._task_chains[cid] = {5}
        mine = self._build_at("mine", "Stargate", chain_id=cid)
        other_chain = self._build_at("oc", "Stargate", chain_id="proxy_b")
        director._pending_activation["mine"] = mine
        director._pending_activation["oc"] = other_chain

        director._assign_chain_followup_spots(cid, (50.0, 50.0))

        assert mine.payload.point is not None  # 本链刷新了
        assert other_chain.payload.point is None  # 别的链没动

    def test_pick_open_spots_avoids_trapping_side(self, director: Director) -> None:
        """地图感知:一侧是矿/崖(不可寻路)→ 落点选在空旷的另一侧,从布局上不围死农民。"""
        from unittest.mock import MagicMock

        bot = MagicMock()
        # 模拟西侧(x<100)不可寻路(矿/崖),东侧空旷
        bot.in_pathing_grid.side_effect = lambda p: p.x >= 100.0
        director._bot = bot

        spots = director._pick_open_cluster_spots((100.0, 100.0), 2)

        assert len(spots) == 2
        # 两个落点都不在被堵死的深西侧(不会被矿+建筑夹死)
        for sx, _sy in spots:
            assert sx >= 98.0, f"落点 {sx} 选到了堵死的西侧"
        # 互相分开 ≥3.5(不重叠)
        dist = ((spots[0][0] - spots[1][0]) ** 2 + (spots[0][1] - spots[1][1]) ** 2) ** 0.5
        assert dist >= 3.5

    def test_pick_open_spots_spread_not_clustered(self, director: Director) -> None:
        """回归(2026-06-07 玩家"两 VS 重合"):全开地图上两点应**拉开到不同侧**(最远点采样),
        不再聚到同一侧只隔 ~5 格 —— 否则 find_placement 各自往水晶拽后撞一起、第 2 个建不出。
        """
        director._bot = None  # 全开(_pathable 恒 True)
        spots = director._pick_open_cluster_spots((100.0, 100.0), 2)
        assert len(spots) == 2
        dist = ((spots[0][0] - spots[1][0]) ** 2 + (spots[0][1] - spots[1][1]) ** 2) ** 0.5
        # 最远点采样应把两点拉到对侧附近(远大于旧 openness 聚堆的 ~5);留足余量给 find_placement 拽偏
        assert dist >= 7.0, f"两落点只隔 {dist:.1f},太近(会被 find_placement 拽到一起重合)"

    # ---- 链式建造复用同一农民(不重新 _pick)----

    def test_chained_build_reuses_chain_probe(self, director: Director) -> None:
        """链式 VS 用 _task_chains 绑的那个农民(card0 claim 的),不重新 _pick 另选自由农民。"""
        from unittest.mock import MagicMock

        cid = "proxy_y"
        director._task_chains[cid] = {7}
        bot = MagicMock()
        director._bot = bot
        # 有明确 point → 不走锚点;直接用 point。probe 应取链上的 7。
        vs = self._build_at("vsx", "Stargate", chain_id=cid, point=(55.0, 50.0))
        director._apply_to_facade(vs, now=10.0)

        assert director.facade.proxy_build_orders[-1]["probe"] == 7

    # ---- 锚点优先 Pylon(能量源)----

    def test_chained_build_anchors_to_pylon(self, director: Director, monkeypatch: object) -> None:
        """point=None(走 named_spot)时,链式建造锚到链上的 **Pylon**(能量源),不锚另一个建筑。"""
        from unittest.mock import MagicMock

        import vibecraft.bot.named_spot as ns_mod

        # named_spot 解析到一个远点(模拟"forward"),anchor 应把它改写成 Pylon 本体位置
        monkeypatch.setattr(
            ns_mod.NamedSpotRegistry,
            "resolve",
            lambda self, name, bot: MagicMock(x=999.0, y=999.0),
        )

        cid = "proxy_z"
        director._task_chains[cid] = {7}
        director._chain_structures[cid] = {91, 92}
        bot = MagicMock()

        pylon = MagicMock()
        pylon.position.x = 60.0
        pylon.position.y = 40.0
        pylon.type_id.name = "PYLON"
        stargate = MagicMock()
        stargate.position.x = 80.0
        stargate.position.y = 80.0
        stargate.type_id.name = "STARGATE"

        def _by_tag(t: int) -> object:
            return {91: stargate, 92: pylon}.get(int(t))

        bot.structures.find_by_tag.side_effect = _by_tag
        director._bot = bot

        vs = self._build_at("vsa", "Stargate", chain_id=cid, named_spot="forward")
        director._apply_to_facade(vs, now=10.0)

        # 落点锚到 Pylon(60,40),不是 named_spot 远点(999,999)、也不是 Stargate(80,80)
        assert director.facade.proxy_build_orders[-1]["point"] == (60.0, 40.0)

    # ---- 激活 → 状态 active(卡片不再一直"未激活")----

    def test_chain_structure_ready_activation_sets_active(self, director: Director) -> None:
        """链上水晶 ready → VS 卡激活 → 状态置 active(执行中),离开 _pending_activation。"""
        from unittest.mock import MagicMock

        cid = "proxy_w"
        director._task_chains[cid] = {7}
        director._chain_structures[cid] = {99}  # 水晶 tag
        bot = MagicMock()
        bot.time = 50.0
        ready_struct = MagicMock()
        ready_struct.tag = 99
        bot.structures.ready = [ready_struct]  # 水晶已 ready
        bot.structures.find_by_tag.return_value = None
        director._bot = bot

        vs = self._build_at("vsr", "Stargate", chain_id=cid, point=(55.0, 50.0))
        director._pending_activation["vsr"] = vs
        director._in_flight["vsr"] = vs

        director._tick_pending_activation(now=50.0)

        assert "vsr" not in director._pending_activation  # 已激活离开等待
        assert director._override_status.get("vsr", {}).get("status") == "active"
        # 激活后下了 build 命令
        assert any(o["probe"] == 7 for o in director.facade.proxy_build_orders)


class TestPlayerWarp:
    """玩家"在X刷N兵"折跃(2026-06-07):折跃门兵种折跃在离落点最近的能量场。
    真折跃(找能量场/can_place/warp_in)需 SC2,见 e2e;这里验路由/完成/取消逻辑。"""

    def _prod(
        self, did: str, items: list[tuple[str, int]], warp_at: tuple[float, float] | None = None
    ) -> Directive:
        from vibecraft.directives.models import (
            Directive,
            ProductionItem,
            ProductionOverridePayload,
        )
        from vibecraft.directives.scope import TargetKind, TargetSpec

        wp = TargetSpec(kind=TargetKind.POINT, point=warp_at) if warp_at is not None else None
        d = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type=u, count=c) for u, c in items],
                warp_at=wp,
            ),
            issued_at=10.0,
        )
        d.id = did
        return d

    def test_warp_capable_routes_to_warp(self, director: Director) -> None:
        """带 warp_at 的折跃门兵种 → 走 request_warp(不 train,避免家里翻倍出)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        d = self._prod("p1", [("Stalker", 4)], warp_at=(50.0, 50.0))
        director._exec_production_override(d, d.payload)
        req = director.facade.warp_requests
        assert any(r["key"] == "p1:Stalker" and r["count"] == 4 for r in req)
        # 折跃门兵种走折跃,不 train
        assert not director.facade.train_calls if hasattr(director.facade, "train_calls") else True
        assert "p1" in director._warp_registered

    def test_forward_warp_uses_enemy_ref_for_power_field(self, director: Director) -> None:
        """ "刷到前线"(named_spot=forward):参考点改用敌方主基地 → facade 选离敌最近的能量场
        (最靠前野水晶/已展开棱镜),不是我方前沿基地附近的家里水晶(2026-06-09 真局踩坑)。"""
        from unittest.mock import MagicMock

        from sc2.position import Point2

        from vibecraft.directives.models import (
            Directive,
            ProductionItem,
            ProductionOverridePayload,
        )
        from vibecraft.directives.scope import TargetKind, TargetSpec

        bot = MagicMock()
        bot.enemy_start_locations = [Point2((100.0, 100.0))]
        director._bot = bot
        wp = TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="forward")
        d = Directive(
            payload=ProductionOverridePayload(
                items=[ProductionItem(unit_type="Zealot", count=2)], warp_at=wp
            ),
            issued_at=10.0,
        )
        d.id = "pf1"
        director._exec_production_override(d, d.payload)
        fwd = [r for r in director.facade.warp_requests if r["key"] == "pf1:Zealot"]
        assert fwd, "Zealot 应走折跃"
        # 关键:target 是敌方点 (100,100),不是 _forward 的我方前沿基地
        assert fwd[0]["target"] == (100.0, 100.0)

    def test_forward_warp_reference_point_returns_enemy_start(self, director: Director) -> None:
        """_forward_warp_reference_point 返回敌方起始点;无 bot/无敌方点 → None。"""
        from unittest.mock import MagicMock

        from sc2.position import Point2

        director._bot = None
        assert director._forward_warp_reference_point() is None
        bot = MagicMock()
        bot.enemy_start_locations = [Point2((77.0, 88.0))]
        director._bot = bot
        assert director._forward_warp_reference_point() == (77.0, 88.0)

    def test_warp_request_idempotent_across_ticks(self, director: Director) -> None:
        """_exec 每 tick 调,request_warp 幂等 → 只登记一次(不每帧重置 remaining)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        d = self._prod("p1b", [("Stalker", 4)], warp_at=(50.0, 50.0))
        director._exec_production_override(d, d.payload)
        director._exec_production_override(d, d.payload)
        director._exec_production_override(d, d.payload)
        assert len(director.facade.warp_requests) == 1  # 只登记一次

    def test_non_warp_unit_not_routed_to_warp(self, director: Director) -> None:
        """不朽不能折跃 → 即便带 warp_at 也不走 request_warp。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.already_pending.return_value = 0
        director._bot.train.return_value = 0
        d = self._prod("p2", [("Immortal", 1)], warp_at=(50.0, 50.0))
        director._exec_production_override(d, d.payload)
        assert director.facade.warp_requests == []  # 不朽没走折跃

    def test_no_warp_at_no_warp(self, director: Director) -> None:
        """不带地点 → 不折跃(走正常 train)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        director._bot.already_pending.return_value = 0
        director._bot.train.return_value = 0
        d = self._prod("p3", [("Stalker", 4)])
        director._exec_production_override(d, d.payload)
        assert director.facade.warp_requests == []

    def test_warp_status_done_marks_item_done(self, director: Director) -> None:
        """facade 报折满(warp_status=done)→ item 状态 done(卡可完成)。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        d = self._prod("p4", [("Stalker", 2)], warp_at=(50.0, 50.0))
        director._exec_production_override(d, d.payload)  # 登记,producing
        director.facade._warp_done_stub.add("p4:Stalker")  # 模拟折满
        director._exec_production_override(d, d.payload)  # 重评估
        assert director._production_item_status["p4"]["Stalker"]["state"] == "done"

    def test_revoke_cancels_pending_warp(self, director: Director) -> None:
        """× 掉卡 → 取消还没折完的折跃请求。"""
        from unittest.mock import MagicMock

        director._bot = MagicMock()
        d = self._prod("p5", [("Stalker", 2)], warp_at=(50.0, 50.0))
        director._in_flight["p5"] = d
        director._exec_production_override(d, d.payload)
        director.revoke_directive("p5", now=11.0)
        assert "p5:Stalker" in director.facade.warp_cancels


class TestSupersedeAndTerminate:
    """2026-06-06 issue #3 + 单位全死终止:
    C 新指令抢单位 → 取消旧冲突的 MOVE 指令(_safe_move_tags 不在 WP-C 覆盖内);
    D 指令单位全死光 → 标已终止消失(standing order 已有 _prune_dead_tags)。
    """

    def _move_directive(self, did: str = "m1") -> Directive:
        from vibecraft.directives.models import Directive, MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec

        payload = MovePayload(
            selector=Selector(unit_type="VoidRay"),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
            safe=True,
            engage=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        d.id = did  # 固定 id 方便断言
        return d

    def test_supersede_terminates_emptied_move(self, director: Director) -> None:
        """新指令抢走 MOVE 的全部单位 → 该 move 从 _safe_move_tags 移除 + 标已终止。"""
        d = self._move_directive("m1")
        director._in_flight["m1"] = d
        director._safe_move_tags["m1"] = ({7001, 7002}, {"named_spot": "enemy_main"}, True)

        director._supersede_conflicting_moves({7001, 7002}, keep_id="claim1", now=20.0)

        assert "m1" not in director._safe_move_tags, "丢光单位的 move 应从 _safe_move_tags 移除"
        assert director._override_status.get("m1", {}).get("status") == "done"
        assert "m1" in director._done_at

    def test_supersede_partial_keeps_move(self, director: Director) -> None:
        """只抢走部分单位 → move 保留剩余单位,不终止。"""
        d = self._move_directive("m1")
        director._in_flight["m1"] = d
        director._safe_move_tags["m1"] = ({7001, 7002, 7003}, {"named_spot": "enemy_main"}, True)

        director._supersede_conflicting_moves({7001}, keep_id="claim1", now=20.0)

        assert "m1" in director._safe_move_tags, "还有剩余单位,move 不应终止"
        assert director._safe_move_tags["m1"][0] == {7002, 7003}
        assert director._safe_move_tags["m1"][2] is True  # engage 保留

    def test_supersede_skips_self(self, director: Director) -> None:
        """keep_id 自己的 move 不被自己抢。"""
        d = self._move_directive("m1")
        director._in_flight["m1"] = d
        director._safe_move_tags["m1"] = ({7001}, {"named_spot": "enemy_main"}, True)
        director._supersede_conflicting_moves({7001}, keep_id="m1", now=20.0)
        assert "m1" in director._safe_move_tags

    def test_standing_order_all_dead_terminated(self, director: Director) -> None:
        """D:standing order 单位全死光 → _prune_dead_tags 标已终止(units_lost)。"""
        from unittest.mock import MagicMock

        d = self._move_directive("so1")  # 借用构造,改成 standing
        director.standing_orders.append(d)
        director._standing_order_tags["so1"] = {8001, 8002}
        bot = MagicMock()
        bot.units = []  # 场上无单位 = 全死
        bot.time = 30.0
        director._bot = bot

        director._tick_standing_order_deaths(now=30.0)

        assert director._override_status.get("so1", {}).get("status") == "done"
        assert director._override_status.get("so1", {}).get("reason") == "units_lost"


class TestPullbackClearsGlobalAttack:
    """2026-06-06 虚空 dancing 修复:玩家把主力'回家防守/撤退回家'(standby→己方主基地)
    时,自动清掉过期的全局'强制全体进攻'意图。

    根因:玩家早先按了全局 attack/all_in,之后只用语音编队(unit_claim)拉部队回家 ——
    unit_claim 不碰全局意图 → 全局 attack 持续强攻没编队的 free_units(尤其新造单位)往前,
    与回家指令撕扯 → 部队脱节 + 跳舞。修法:standby 到 home named_spot 时复用 revoke_tactical
    清掉 active 的全局 attack(等价自动按掉那张'强制全体进攻'卡片)。
    """

    def _active_global(self, director: Director, verb: str, now: float = 100.0) -> Directive:
        from vibecraft.directives.models import TacticalObjectivePayload
        from vibecraft.directives.types import IssuedBy

        payload = TacticalObjectivePayload(verb=verb, persistent=True)  # type: ignore[arg-type]
        d = Directive(payload=payload, issued_at=now, issued_by=IssuedBy.VOICE)
        director._exec_l2_global(d, payload)
        return d

    def _standby_claim(self, named_spot: str, now: float = 110.0) -> Directive:
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        payload = UnitClaimPayload(
            selector=Selector(group_id=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.STANDBY,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot),
                )
            ),
            persistent=True,
        )
        return Directive(payload=payload, issued_at=now)

    def test_standby_home_clears_global_attack(self, director: Director) -> None:
        """回家(standby→main)→ 清掉 active 的全局 attack 意图。"""
        gd = self._active_global(director, "attack")
        assert director._current_l2_global_id == gd.id
        assert director._tactical_overrides.get(gd.id) == "attack"

        d2 = self._standby_claim("main")
        director._apply_unit_claim(d2, d2.payload, now=110.0)

        assert director._current_l2_global_id is None, "回家应清全局 attack"
        assert director.facade.combat_intent_overrides[-1] is None
        assert director.facade.attack_mode_overrides[-1] is None

    def test_persistent_standby_home_clears_global_attack(self, director: Director) -> None:
        """真局回归:persistent '回家防守' 走 _assign_standing_order_units(不经
        _apply_unit_claim)也要清全局 attack。2026-06-06 真局发现此路径漏清。"""
        from vibecraft.directives.models import Directive, UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        gd = self._active_global(director, "attack")
        assert director._current_l2_global_id == gd.id
        payload = UnitClaimPayload(
            selector=Selector(group_id=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.STANDBY,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main"),
                )
            ),
            persistent=True,
        )
        d = Directive(payload=payload, issued_at=110.0)
        director._assign_standing_order_units(d)  # persistent 路径

        assert director._current_l2_global_id is None, "persistent 回家也应清全局 attack"
        assert director.facade.combat_intent_overrides[-1] is None

    def test_standby_forward_spot_keeps_global_attack(self, director: Director) -> None:
        """standby 到前压点(瞭望塔)不是回家 → 不清全局 attack。"""
        gd = self._active_global(director, "attack")
        d2 = self._standby_claim("watchtower_right")
        director._apply_unit_claim(d2, d2.payload, now=110.0)
        assert director._current_l2_global_id == gd.id, "前压 standby 不应清全局 attack"

    def test_standby_home_keeps_global_defend(self, director: Director) -> None:
        """全局是 defend(非 attack)时,回家 standby 不动它(只清 attack)。"""
        gd = self._active_global(director, "defend")
        d2 = self._standby_claim("main")
        director._apply_unit_claim(d2, d2.payload, now=110.0)
        assert director._current_l2_global_id == gd.id, "只清 attack,defend 不动"


class TestScoutSingleUnitDefault:
    """SCOUT 默认单单位，不能把所有匹配 unit_type 的兵都派出去。

    2026-05-18 实战 bug：用户说"一个农民去探路"，LLM 输出 scout selector={unit_type:Probe}（无 count），
    director 用 resolve_selector 拿到全部探机 tag → 全派出去。
    """

    def _make_scout_directive(
        self, unit_type: str | None = "Probe", tag: int | None = None, tags: list[int] | None = None
    ) -> Directive:
        from vibecraft.directives.models import ScoutPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec

        selector = None
        if unit_type or tag is not None or tags:
            selector = Selector(unit_type=unit_type, tag=tag, tags=tags)
        return Directive(
            payload=ScoutPayload(
                selector=selector,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
            ),
            issued_at=5.0,
        )

    def test_scout_with_unit_type_only_picks_one(self, director: Director) -> None:
        """selector={unit_type:Probe} 即使匹配多个 → 只派 1 个，不是全部。"""
        # facade.selector_stub 模拟"unit_type=Probe → 多个探机 tag"
        director.facade.selector_stub["Probe"] = [101, 102, 103, 104, 105, 106]
        d = self._make_scout_directive(unit_type="Probe")
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)
        # 应该只 execute_unit_action 一次
        scout_actions = [a for a in director.facade.unit_actions if a["verb"] == "scout"]
        assert len(scout_actions) == 1, (
            f"期望 1 个 scout action，实际 {len(scout_actions)}：{scout_actions}"
        )

    def test_scout_with_explicit_tag_uses_that_tag(self, director: Director) -> None:
        """selector.tag=X → 用指定 tag。"""
        d = self._make_scout_directive(unit_type=None, tag=12345)
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)
        scout_actions = [a for a in director.facade.unit_actions if a["verb"] == "scout"]
        assert len(scout_actions) == 1
        assert scout_actions[0]["tag"] == 12345

    def test_scout_with_explicit_tags_uses_all_of_them(self, director: Director) -> None:
        """selector.tags=[a,b,c] → 全部使用（显式列表 = 玩家想全用）。"""
        d = self._make_scout_directive(unit_type=None, tags=[201, 202, 203])
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)
        scout_actions = [a for a in director.facade.unit_actions if a["verb"] == "scout"]
        assert len(scout_actions) == 3
        assert sorted(a["tag"] for a in scout_actions) == [201, 202, 203]

    def test_scout_no_match_falls_back_to_tag_zero(self, director: Director) -> None:
        """selector 匹配 0 个 → fallback execute_unit_action(unit_tag=0) 让 facade 自选。"""
        # selector_stub 不设 Probe key → resolve_selector 返回 []
        d = self._make_scout_directive(unit_type="Probe")
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)
        scout_actions = [a for a in director.facade.unit_actions if a["verb"] == "scout"]
        assert len(scout_actions) == 1
        assert scout_actions[0]["tag"] == 0  # fallback


class TestStandingOrderRouting:
    """P1.2 Director 按 persistent 路由 directive 到 standing_orders 或 _in_flight。"""

    def test_persistent_false_goes_to_in_flight(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=False)
        director._submit_directives([d], now=10.0)
        # 进 pending → 还没 committed，但 _in_flight 里已有（board.submit 之后）
        assert d.id in director._in_flight
        assert not any(s.id == d.id for s in director.standing_orders)

    def test_persistent_true_goes_to_standing_orders(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        assert d.id not in director._in_flight

    def test_revoke_standing_order_removes(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        result = director.revoke_standing_order(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.standing_orders)


# =========================================================================
# P2: Production Override 路由（PRODUCTION_OVERRIDE/TECH_OVERRIDE/EXPANSION_OVERRIDE
#     → production_overrides 列表，不进 _in_flight）
# =========================================================================


def _make_production_override_directive() -> Directive:
    """构造一个 PRODUCTION_OVERRIDE Directive（出 2 哨兵）。"""
    from vibecraft.directives.models import ProductionItem, ProductionOverridePayload

    payload = ProductionOverridePayload(items=[ProductionItem(unit_type="Sentry", count=2)])
    return Directive(payload=payload, issued_at=10.0)


def _make_tech_override_directive() -> Directive:
    """构造一个 TECH_OVERRIDE Directive（研 Blink）。"""
    from vibecraft.directives.models import TechOverridePayload

    payload = TechOverridePayload(upgrade_id="Blink")
    return Directive(payload=payload, issued_at=10.0)


def _make_expansion_override_directive() -> Directive:
    """构造一个 EXPANSION_OVERRIDE Directive（开 3 矿）。"""
    from vibecraft.directives.models import ExpansionOverridePayload

    payload = ExpansionOverridePayload(target_count=3)
    return Directive(payload=payload, issued_at=10.0)


class TestProductionOverrideRouting:
    """P2 Director 把 PRODUCTION/TECH/EXPANSION override 路由到 production_overrides。"""

    def test_production_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_production_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_tech_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_tech_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_expansion_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_expansion_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight


# =========================================================================
# P2: revoke_directive unified（撤 standing + production override）
# =========================================================================


class TestRevokeDirectiveUnified:
    """P2 revoke_directive(id, now) 统一撤销 standing_orders 和 production_overrides。"""

    def test_revoke_directive_removes_standing_order(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        result = director.revoke_directive(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.standing_orders)

    def test_revoke_directive_removes_production_override(self, director: Director) -> None:
        d = _make_production_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        result = director.revoke_directive(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.production_overrides)


# =========================================================================
# P3.2: TaskMonitor wiring
# =========================================================================


def _make_director_with_task_monitor(session: GameSession) -> Director:
    """构造带 task_monitor 的 Director（传入 EventBus）。"""
    from vibecraft.bot.event_bus import EventBus
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    event_bus = EventBus()
    return Director(facade=facade, parser=parser, session=session, event_bus=event_bus)


def _make_tactical_objective_directive(done_when_dict: dict | None = None) -> Directive:
    """构造一个 TACTICAL_OBJECTIVE Directive，可选带 done_when。"""
    from vibecraft.directives.models import TacticalObjectivePayload, TimeElapsedSince

    if done_when_dict is not None:
        dw = TimeElapsedSince(
            kind="time_elapsed_since", seconds=float(done_when_dict.get("seconds", 30))
        )
    else:
        dw = None

    payload = TacticalObjectivePayload(verb="attack", done_when=dw, timeout_s=None)
    return Directive(payload=payload, issued_at=10.0)


class TestTaskMonitorWire:
    """P3.2: task_monitor wiring 单测。"""

    def test_no_event_bus_task_monitor_is_none(self, session: GameSession) -> None:
        """不传 event_bus → task_monitor 为 None，Director 不崩。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d.task_monitor is None
        # on_tick 不崩
        d.on_tick(now=10.0)

    def test_attach_called_when_done_when_set(self, session: GameSession) -> None:
        """_submit_directives 对有 done_when 的 directive 调 task_monitor.attach_directive。"""
        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        # mock task_monitor.attach_directive
        original_attach = director.task_monitor.attach_directive
        attach_calls: list[dict] = []

        def _recording_attach(**kwargs: object) -> None:  # type: ignore[override]
            attach_calls.append(dict(kwargs))
            original_attach(**kwargs)  # type: ignore[arg-type]

        director.task_monitor.attach_directive = _recording_attach  # type: ignore[method-assign]

        d = _make_tactical_objective_directive(
            done_when_dict={"kind": "time_elapsed_since", "seconds": 90}
        )
        director._submit_directives([d], now=10.0)

        assert len(attach_calls) == 1
        assert attach_calls[0]["directive_id"] == d.id

    def test_no_attach_when_done_when_none(self, session: GameSession) -> None:
        """done_when=None 时不调 attach_directive。"""
        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        attach_calls: list[object] = []
        original_attach = director.task_monitor.attach_directive

        def _spy(**kwargs: object) -> None:  # type: ignore[override]
            attach_calls.append(kwargs)
            original_attach(**kwargs)  # type: ignore[arg-type]

        director.task_monitor.attach_directive = _spy  # type: ignore[method-assign]

        d = _make_tactical_objective_directive(done_when_dict=None)
        director._submit_directives([d], now=10.0)

        assert len(attach_calls) == 0

    def test_tick_completed_id_triggers_complete_and_detach(self, session: GameSession) -> None:
        """task_monitor.tick 返回的 id 触发 board.complete + detach + 从 _in_flight 移除。"""
        from unittest.mock import patch

        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        # 先 submit 一个有 done_when 的 directive
        d = _make_tactical_objective_directive(
            done_when_dict={"kind": "time_elapsed_since", "seconds": 30}
        )
        director._submit_directives([d], now=10.0)
        # 确认进了 _in_flight（还在 board.pending 里，key=d.id）
        assert d.id in director._in_flight

        # mock task_monitor.tick 返回这个 id（模拟 checker 判定已完成）
        completed_ids = [d.id]
        with (
            patch.object(director.task_monitor, "tick", return_value=completed_ids) as mock_tick,
            patch.object(director.task_monitor, "detach") as mock_detach,
        ):
            director.on_tick(now=40.0)
            mock_tick.assert_called_once()
            mock_detach.assert_called_once_with(d.id)

        # directive 应该从 _in_flight 移除
        assert d.id not in director._in_flight

    def test_tick_completed_production_override_removed(self, session: GameSession) -> None:
        """task_monitor 完成 → grace 期内保留显示"已完成",grace 后真删。

        2026-05-24 用户:完成后延迟 _DONE_GRACE_S=5s 才真删,让卡片
        显示"已完成"绿色再消失。
        """
        from unittest.mock import patch

        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        from vibecraft.directives.models import (
            ProductionItem,
            ProductionOverridePayload,
            TimeElapsedSince,
        )

        payload = ProductionOverridePayload(
            items=[ProductionItem(unit_type="Sentry", count=2)],
            done_when=TimeElapsedSince(kind="time_elapsed_since", seconds=30),
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)

        # Tick 1: 完成时间到 → mark done + done_at,但还在 list(grace 期内)
        completed_ids = [d.id]
        with (
            patch.object(director.task_monitor, "tick", return_value=completed_ids),
            patch.object(director.task_monitor, "detach"),
        ):
            director.on_tick(now=40.0)
        assert any(s.id == d.id for s in director.production_overrides), (
            "grace 期内卡片应保留(显示'已完成')"
        )
        assert director._override_status[d.id]["status"] == "done"

        # Tick 2: now=46s(超 5s grace)→ 真删
        with (
            patch.object(director.task_monitor, "tick", return_value=[]),
            patch.object(director.task_monitor, "detach"),
        ):
            director.on_tick(now=46.0)
        assert not any(s.id == d.id for s in director.production_overrides), "grace 期过 → 真删"

    def test_setup_task_monitor_works(self, session: GameSession) -> None:
        """setup_task_monitor 事后注入 event_bus，task_monitor 从 None 变为有效实例。"""
        from vibecraft.bot.event_bus import EventBus
        from vibecraft.bot.task_monitor import TaskMonitor
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d.task_monitor is None

        bus = EventBus()
        d.setup_task_monitor(bus)
        assert isinstance(d.task_monitor, TaskMonitor)


# ---------------------------------------------------------------------------
# P5.C: Director bot backref
# ---------------------------------------------------------------------------


class TestDirectorBotBackref:
    def test_director_accepts_bot_kwarg(self, session: GameSession) -> None:
        """Director(bot=mock_bot) 构造 OK，_bot 保存 bot 引用。"""
        from unittest.mock import MagicMock

        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_bot = MagicMock()
        d = Director(facade=facade, parser=parser, session=session, bot=mock_bot)
        assert d._bot is mock_bot

    def test_director_bot_none_by_default(self, session: GameSession) -> None:
        """不传 bot 时 _bot 为 None（向后兼容）。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d._bot is None

    def test_on_tick_passes_bot_to_task_monitor(self, session: GameSession) -> None:
        """on_tick 时把 _bot 传给 task_monitor.tick 作为 game_state。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.event_bus import EventBus
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_bot = MagicMock()
        d = Director(facade=facade, parser=parser, session=session, bot=mock_bot)

        # 注入 task_monitor
        bus = EventBus()
        d.setup_task_monitor(bus)

        with patch.object(d.task_monitor, "tick", return_value=[]) as mock_tick:
            d.on_tick(now=10.0)
            mock_tick.assert_called_once_with(10.0, game_state=mock_bot)


# =========================================================================
# P5.E: Standing order unit assign + sharpy 让位 + revoke release
# =========================================================================


def _make_persistent_unit_claim_directive(unit_type: str = "Phoenix") -> Directive:
    """构造 persistent=True 的 unit_claim Directive，用于 standing order 测试。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type=unit_type),
        task=Task(
            primary_action=Action(
                verb=Verb.PATROL,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
            )
        ),
        persistent=True,
    )
    return Directive(payload=payload, issued_at=10.0)


def _make_group_command_directive(
    group_id: int,
    verb,  # vibecraft.directives.task.Verb（避免顶层导入）
    *,
    named_spot: str | None = "enemy_third",
    camera: bool = False,
    persistent: bool = True,
) -> Directive:
    """构造"N 队〈做什么〉"指令：unit_claim(selector.group_id) + 某 verb。

    这是编队指挥的标准形态（2026-06-04）：进攻/火力侦查 → attack_move，
    待命/防守/撤退 → standby。selector 只填 group_id，Director 解析为该队 tags。
    """
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task

    if camera:
        target = TargetSpec(kind=TargetKind.CAMERA)
    else:
        target = TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot)
    payload = UnitClaimPayload(
        selector=Selector(group_id=group_id),
        task=Task(primary_action=Action(verb=verb, target=target)),
        persistent=persistent,
    )
    return Directive(payload=payload, issued_at=10.0)


class TestGroupCommand:
    """语音编队指挥（2026-06-04）：编队后"N 队进攻/火力侦查/待命/撤退"。

    根因回溯：tactical_objective(全军指令)无 group_id 字段，"一队进攻"被降级成
    全军 all_in → 编了队的单位反被排除、待原地（实战 log game_20260604_002300）。
    正解：一律走 unit_claim + selector.group_id + 对应 verb，selector 在
    _resolve_selector_with_count 里查 _voice_groups → 只对该队 tags 下令。
    """

    @pytest.fixture(autouse=True)
    def _reset_max_groups(self):
        """编队上限是模块全局可变状态，每个用例前后复位到默认 5，避免跨用例泄漏。"""
        from vibecraft.directives.scope import set_max_voice_groups

        set_max_voice_groups(5)
        yield
        set_max_voice_groups(5)

    def test_resolve_group_id_returns_group_tags(self, director: Director) -> None:
        """selector.group_id → _resolve_selector_with_count 查 _voice_groups 返回该队 tags。"""
        from vibecraft.directives.scope import Selector

        director._voice_groups[1] = {501, 502, 503}
        tags = director._resolve_selector_with_count(Selector(group_id=1))
        assert sorted(tags) == [501, 502, 503]

    def test_resolve_empty_group_returns_empty(self, director: Director) -> None:
        """空/不存在的队 → 返回 []（不崩、不退化成全军）。"""
        from vibecraft.directives.scope import Selector

        assert director._resolve_selector_with_count(Selector(group_id=4)) == []

    def test_group_attack_dispatches_attack_move_to_group_only(self, director: Director) -> None:
        """ "1 队进攻三矿" → 只对该队 tags 下 attack_move，不波及别的单位。"""
        from vibecraft.directives.task import Verb

        director._voice_groups[1] = {601, 602}
        # 另放一队 2，确认不会被波及
        director._voice_groups[2] = {701, 702}
        d = _make_group_command_directive(1, Verb.ATTACK_MOVE, named_spot="enemy_third")
        director._submit_directives([d], now=10.0)

        attack_actions = [a for a in director.facade.unit_actions if a["verb"] == "attack_move"]
        attacked_tags = sorted(a["tag"] for a in attack_actions)
        assert attacked_tags == [601, 602], (
            f"应只对 1 队(601,602)下 attack_move，实际 {attacked_tags}"
        )
        # 2 队不应被波及
        assert 701 not in attacked_tags and 702 not in attacked_tags
        # 编队单位被接管为 LLM_CONTROLLED
        roled = {c.args[0] for c in director.facade.calls if c.method == "set_unit_role"}
        assert {601, 602} <= roled

    def test_group_standby_dispatches_to_group(self, director: Director) -> None:
        """ "3 队回防" → standby 到 main，对该队 tags 下发。"""
        from vibecraft.directives.task import Verb

        director._voice_groups[3] = {801}
        d = _make_group_command_directive(3, Verb.STANDBY, named_spot="main")
        director._submit_directives([d], now=10.0)
        dispatched = [
            a for a in director.facade.unit_actions if a["tag"] == 801 and a["verb"] == "standby"
        ]
        assert dispatched, f"3 队应被下 standby，facade.unit_actions={director.facade.unit_actions}"

    def test_group_id_out_of_range_raises(self) -> None:
        """编队号超出 1-5 → 报错（中文友好消息），不静默 clamp、不执行。

        2026-06-04 用户：玩家说"第 7 队 / 第 0 队"必须报错。三处入口全覆盖：
        group_assign / group_clear / selector(编队指挥)。
        """
        import pytest
        from pydantic import ValidationError

        from vibecraft.directives.models import GroupAssignPayload, GroupClearPayload
        from vibecraft.directives.scope import Selector

        for bad in (0, 6, 7, 99):
            with pytest.raises(ValidationError, match="编队号只能是 1-5"):
                GroupAssignPayload(group_id=bad, selector=Selector(unit_type="VoidRay"))
            with pytest.raises(ValidationError, match="编队号只能是 1-5"):
                GroupClearPayload(group_id=bad)
            with pytest.raises(ValidationError, match="编队号只能是 1-5"):
                Selector(group_id=bad)
        # 边界内不报错
        for ok in (1, 5):
            GroupAssignPayload(group_id=ok, selector=Selector(unit_type="VoidRay"))
            GroupClearPayload(group_id=ok)
            Selector(group_id=ok)
        # group_id=None（不指挥编队的普通 selector）不受影响
        assert Selector(unit_type="Zealot").group_id is None

    def test_max_voice_groups_configurable(self) -> None:
        """编队上限可配置：set_max_voice_groups(N) → schema 校验范围跟着变到 1-N。

        2026-06-04 用户：编队总数做成可配置选项（默认 5）。配置入口
        ParserConfig.max_voice_groups → set_max_voice_groups → schema 校验。
        """
        import pytest
        from pydantic import ValidationError

        from vibecraft.directives.models import GroupAssignPayload
        from vibecraft.directives.scope import Selector, set_max_voice_groups

        # 上限调到 8：第 7、8 队现在合法
        set_max_voice_groups(8)
        assert Selector(group_id=8).group_id == 8
        GroupAssignPayload(group_id=7, selector=Selector(unit_type="VoidRay"))
        # 第 9 队仍越界报错（中文消息跟随新上限）
        with pytest.raises(ValidationError, match="编队号只能是 1-8"):
            Selector(group_id=9)
        # 上限调回 3：第 4 队变越界
        set_max_voice_groups(3)
        with pytest.raises(ValidationError, match="编队号只能是 1-3"):
            Selector(group_id=4)
        # 硬上限保护：超出 MAX_VOICE_GROUPS_LIMIT 直接拒绝配置
        with pytest.raises(ValueError, match="max_voice_groups 必须在"):
            set_max_voice_groups(99)

    def test_parser_config_applies_max_voice_groups(self, session: GameSession) -> None:
        """ParserConfig.max_voice_groups 经 IntentParser 构造应用到 schema 全局。"""
        from vibecraft.directives import scope
        from vibecraft.llm import IntentParser, MockLLMProvider, ParserConfig, ProviderResponse

        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        IntentParser(
            provider,
            library_inst,
            session=session,
            config=ParserConfig(max_voice_groups=6),
        )
        assert scope.MAX_VOICE_GROUPS == 6

    def test_ephemeral_group_claim_reserves_at_submit(self, director: Director) -> None:
        """submit 预留路径(_claim_directive_units)认 group_id：ephemeral 编队指挥也能预留。

        修前 _claim_directive_units 直接调 facade.resolve_selector(只认 unit_type/tag/tags)，
        带 group_id 的 selector 解析为空 → 单位不被预留。修后走 group-aware helper。
        """
        from vibecraft.directives.task import Verb

        director._voice_groups[2] = {901, 902}
        d = _make_group_command_directive(
            2, Verb.ATTACK_MOVE, named_spot="enemy_third", persistent=False
        )
        director._submit_directives([d], now=10.0)
        assert director._standing_order_tags.get(d.id) == {901, 902}, (
            "ephemeral 编队指挥应在 submit 时把该队 tags 记入 _standing_order_tags(预留)"
        )


class TestStandingOrderUnitAssign:
    """P5.E: persistent unit_claim 进 standing_orders 时 resolve selector + 通知 sharpy 让位。"""

    def test_persistent_claim_calls_set_unit_role_on_submit(self, session: GameSession) -> None:
        """submit persistent unit_claim → facade.set_unit_role(LLM_CONTROLLED) 被调用。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [1001, 1002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        # standing_orders に入っていること
        assert any(s.id == d.id for s in director.standing_orders)
        # set_unit_role(LLM_CONTROLLED) が両 tag に呼ばれること
        assert facade.unit_roles == {1001: UnitRole.LLM_CONTROLLED, 1002: UnitRole.LLM_CONTROLLED}
        set_role_calls = [c for c in facade.calls if c.method == "set_unit_role"]
        assert len(set_role_calls) == 2
        tags_called = {c.args[0] for c in set_role_calls}
        assert tags_called == {1001, 1002}

    def test_ephemeral_card_stays_after_commit(self, session: GameSession) -> None:
        """2026-05-25 bug 5:ephemeral unit_claim / MOVE / SCOUT 等 commit 后,
        snapshot.command_cards 仍含此 directive(玩家能看到卡片 + 点 × 撤销)。

        重现:用户报"农民还没到对面基地,指令卡就消失了"。根因
        _dispatch_committed_to_facade.pop(_in_flight) → _build_command_cards
        line 547 for _in_flight 找不到 → 卡片消失。
        """
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [5001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)
        payload = UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.MOVE_TO,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
                )
            ),
            persistent=False,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)
        director._submit_directives([d], now=10.0)
        # commit 前:卡片可见(_in_flight 路径)
        before = director.build_snapshot(now=10.0)
        assert any(c["id"] == d.id for c in before["command_cards"])
        # 触发 commit
        for ev in director.board.tick(10.5):
            director._dispatch_event(ev)
        # commit 后:卡片仍然可见(新行为)
        after = director.build_snapshot(now=11.0)
        ids = [c["id"] for c in after["command_cards"]]
        assert d.id in ids, f"commit 后 command_cards 应含 ephemeral directive,得到 {ids}"

    def test_ephemeral_claim_respects_selector_count(self, session: GameSession) -> None:
        """2026-05-25 bug 4:ephemeral unit_claim 必须尊重 selector.count
        (否则 LLM "一个农民" + count=1,但 _apply_unit_claim 忽略 → 全军农民
        被拉走;实际 user 报"探路农民去对方家 → 所有农民被拉到对面基地")。

        对照 _assign_standing_order_units(persistent 路径)已 cap,_apply_unit_claim
        (ephemeral 路径)漏 cap → 跟 bug 1 同 pattern 的 spec 漂移。
        """
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        # 模拟"游戏里有 30 个 Probe"(开局 12 农民 + ...)
        facade.selector_stub["Probe"] = list(range(8000, 8030))
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)
        # 跟实际 user 场景一致:ephemeral unit_claim count=1
        payload = UnitClaimPayload(
            selector=Selector(unit_type="Probe", count=1),
            task=Task(
                primary_action=Action(
                    verb=Verb.MOVE_TO,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
                )
            ),
            persistent=False,  # ephemeral 路径
        )
        d = Directive(payload=payload, issued_at=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)  # 立即 commit
        director._submit_directives([d], now=10.0)
        # 触发 commit dispatch
        for ev in director.board.tick(10.5):
            director._dispatch_event(ev)
        # 关键:只能有 1 个 execute_unit_action(对应 count=1)
        moved_tags = [a["tag"] for a in facade.unit_actions if a["verb"] == "move_to"]
        assert len(moved_tags) == 1, (
            f"selector.count=1 应只派 1 个农民,实际 {len(moved_tags)} 个被派去 "
            f"(tags={moved_tags})。bug 4:所有农民被拉走根因。"
        )

    def test_move_directive_respects_selector_count(self, session: GameSession) -> None:
        """2026-05-25 bug 4 同 pattern:MOVE directive 必须尊重 selector.count
        (否则 LLM count=1 → 所有同 unit_type 单位全 move_to target)。"""
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Probe"] = list(range(9000, 9030))
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        payload = MovePayload(
            selector=Selector(unit_type="Probe", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_left"),
            safe=False,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)
        director._submit_directives([d], now=10.0)
        for ev in director.board.tick(10.5):
            director._dispatch_event(ev)
        moved = [a for a in facade.unit_actions if a["verb"] == "move_to"]
        assert len(moved) == 1, f"MOVE selector.count=1 应只移动 1 个,实际 {len(moved)} 个被 move"

    def _dispatch_move(self, session: GameSession, *, engage: bool, safe: bool = False):
        """helper:提交一条 MOVE(units 已 resolve)并 dispatch,返回 facade。"""
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [7001, 7002, 7003]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)
        payload = MovePayload(
            selector=Selector(unit_type="VoidRay"),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
            safe=safe,
            engage=engage,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)
        director._submit_directives([d], now=10.0)
        for ev in director.board.tick(10.5):
            director._dispatch_event(ev)
        return facade

    def test_move_engage_field_default_false(self) -> None:
        """MovePayload.engage 默认 False(普通 move,不主动接敌)。"""
        from vibecraft.directives.models import MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec

        p = MovePayload(
            selector=Selector(unit_type="VoidRay"),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
        )
        assert p.engage is False

    def test_move_engage_true_dispatches_attack_move(self, session: GameSession) -> None:
        """engage=True → 沿途 attack_move(facade verb=attack_move)。"""
        facade = self._dispatch_move(session, engage=True)
        verbs = {a["verb"] for a in facade.unit_actions}
        assert "attack_move" in verbs, f"engage=True 应发 attack_move,实际 {verbs}"
        assert "move_to" not in verbs, f"engage=True 不应发普通 move_to,实际 {verbs}"

    def test_move_engage_false_dispatches_move_to(self, session: GameSession) -> None:
        """engage=False → 普通 move_to(不主动接敌)。"""
        facade = self._dispatch_move(session, engage=False)
        verbs = {a["verb"] for a in facade.unit_actions}
        assert "move_to" in verbs, f"engage=False 应发 move_to,实际 {verbs}"
        assert "attack_move" not in verbs

    def test_move_directive_pending_when_no_units(self, session: GameSession) -> None:
        """2026-05-27 Issue 3 regression:MOVE commit 时 selector 解析到 0 unit
        (典型场景:"出棱镜然后飞到 enemy_third" 复合指令,production_override + move
        同帧 commit,棱镜还在 produce)应进 _pending_move 等 unit 出现,不能立刻
        no-op 让 directive 永远不动。"""
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, MovePayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        # 关键:selector_stub 不放 WarpPrism → resolve 返空
        facade.selector_stub["WarpPrism"] = []
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        payload = MovePayload(
            selector=Selector(unit_type="WarpPrism", count=1),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
            safe=True,
        )
        d = Directive(payload=payload, issued_at=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)
        director._submit_directives([d], now=10.0)
        for ev in director.board.tick(10.5):
            director._dispatch_event(ev)

        # 修前:directive 立刻 mark done,_safe_move_tags 进了 set(),tick 看 0 unit 触发 release_done
        # 修后:directive 进 _pending_move,等 unit 出现
        assert d.id in director._pending_move, (
            f"MOVE 0 unit 应进 _pending_move 等 unit 出现,实际 _pending_move={director._pending_move}"
        )
        # safe_move_tags 不应有(直到 unit 出现才接管)
        assert d.id not in director._safe_move_tags

    def test_persistent_claim_dispatches_primary_action(self, session: GameSession) -> None:
        """2026-05-25 bug 1 修复:persistent unit_claim 必须立即下发首条
        primary_action(execute_unit_action),否则单位被 reserved 但不动。

        重现:玩家"农民去占瞭望塔" → LLM 解析 unit_claim(hold_position, named_spot)
        persistent=True → 路由进 standing_orders 不进 _in_flight →
        _dispatch_committed_to_facade.pop 找不到 → 不调 _apply_unit_claim →
        没下 execute_unit_action → 单位站原地不动。
        """
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Probe"] = [7001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Probe")
        director._submit_directives([d], now=10.0)

        # 关键:必须有 execute_unit_action 调用,verb 跟 primary_action 一致
        dispatched = [a for a in facade.unit_actions if a["tag"] == 7001 and a["verb"] == "patrol"]
        assert dispatched, (
            f"persistent unit_claim 应立即下发 primary_action,"
            f"facade.unit_actions={facade.unit_actions}"
        )

    def test_tags_tracked_in_standing_order_tags(self, session: GameSession) -> None:
        """_standing_order_tags directive_id → assigned tags 被正确记录。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [2001, 2002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        assert d.id in director._standing_order_tags
        assert director._standing_order_tags[d.id] == {2001, 2002}

    def test_revoke_standing_order_calls_release_unit_role(self, session: GameSession) -> None:
        """revoke_standing_order → facade.release_unit_role 被每个 tag 调用。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [3001, 3002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        # revoke 前 unit_roles 已记录
        assert 3001 in facade.unit_roles
        assert 3002 in facade.unit_roles

        result = director.revoke_standing_order(d.id, now=15.0)
        assert result is True

        # release_unit_role 被调用，unit_roles 从 FakeFacade 移除
        assert 3001 not in facade.unit_roles
        assert 3002 not in facade.unit_roles
        release_calls = [c for c in facade.calls if c.method == "release_unit_role"]
        assert len(release_calls) == 2
        released_tags = {c.args[0] for c in release_calls}
        assert released_tags == {3001, 3002}

    def test_revoke_clears_standing_order_tags(self, session: GameSession) -> None:
        """revoke 后 _standing_order_tags 中移除该 directive_id。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [4001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)
        assert d.id in director._standing_order_tags

        director.revoke_standing_order(d.id, now=15.0)
        assert d.id not in director._standing_order_tags

    def test_non_persistent_claim_reserves_units_immediately(self, session: GameSession) -> None:
        """2026-05-24 用户:所有有 selector 的 directive 提交时都 reserve units
        (防 sharpy 派别的)。non-persistent claim 也立即 set_unit_role。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [5001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_unit_claim_directive(persistent=False)
        director._submit_directives([d], now=10.0)

        # _standing_order_tags 应记录(覆盖通用 directive units,不只 standing)
        assert d.id in director._standing_order_tags
        assert 5001 in director._standing_order_tags[d.id]
        # 应调 set_unit_role(LLM_CONTROLLED)
        set_role_calls = [c for c in facade.calls if c.method == "set_unit_role"]
        assert len(set_role_calls) >= 1

    def test_release_executes_at_submit_not_clobbering_sibling_claim(
        self, session: GameSession
    ) -> None:
        """2026-06-01 用户实测 bug:"探路农民回来吧，去占右边瞭望塔" → 瞭望塔不执行。

        根因(时序不对称):同一句话产 release + claim(瞭望塔)。claim 在 submit 立即生效
        派农民去瞭望塔;但 release 的效果 _apply_unit_release 延迟到 commit 才执行,那时
        瞭望塔农民已"移动中",泛化 release(Probe count=1)经 sharpy resolve_selector
        (idle+matched+gathering)第一个抓到的正是它 → 设回 IDLE → 抢回采矿 → 到不了瞭望塔。

        方案3 修复:release 效果提前到 submit、且 batch 内 release 先于 claim 处理,commit
        不再重复执行。于是 release 跑时 claim 还没生效、抓不到瞭望塔农民;claim 随后接管;
        commit 不再 clobber。本测试用对抗顺序 [claim, release] 提交,验证 reorder + 提前执行
        + commit 不重复 三者合力让瞭望塔农民最终保持被 claim(LLM_CONTROLLED)且收到 move。
        """
        from vibecraft.directives.models import (
            Directive,
            UnitClaimPayload,
            UnitReleasePayload,
        )
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        probe = 4345823233
        facade.selector_stub["Probe"] = [probe]

        claim = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Probe", count=1),
                task=Task(
                    primary_action=Action(
                        verb=Verb.HOLD_POSITION,
                        target=TargetSpec(
                            kind=TargetKind.NAMED_SPOT, named_spot="watchtower_right"
                        ),
                    )
                ),
                persistent=True,
            ),
            issued_at=102.7,
        )
        release = Directive(
            payload=UnitReleasePayload(
                selector=Selector(unit_type="Probe", count=1), return_to_role="IDLE"
            ),
            issued_at=102.7,
        )
        # 对抗顺序:claim 在前、release 在后。reorder 必须把 release 提到前面执行。
        director._submit_directives([claim, release], now=102.7)
        # submit 后:release 先跑(抓不到尚未 claim 的农民)→ claim 接管农民去瞭望塔。
        assert facade.unit_roles[probe] == UnitRole.LLM_CONTROLLED
        wt_moves = [
            a for a in facade.unit_actions if a["tag"] == probe and a["verb"] == "hold_position"
        ]
        assert wt_moves, "瞭望塔农民应收到 hold_position move"

        # commit:release 走 _apply_to_facade 只 mark done,绝不重复执行 _apply_unit_release。
        # 旧 bug 在此处把农民设回 IDLE;方案3 后农民仍保持被 claim。
        director.on_tick(now=104.0)
        assert facade.unit_roles[probe] == UnitRole.LLM_CONTROLLED


# =========================================================================
# P0c Task 7: strategy_cancel 统一走 board.submit
# =========================================================================


def _make_strategy_cancel_directive(stage: str = "all") -> Directive:
    """构造一个 STRATEGY_CANCEL Directive。"""
    from vibecraft.directives.models import StrategyCancelPayload

    payload = StrategyCancelPayload(stage=stage)  # type: ignore[arg-type]
    return Directive(payload=payload, issued_at=10.0)


class TestStrategyCancelViaBoard:
    """P0c Task 7: strategy_cancel 跟其它 directive 一样走 board.submit → _apply_to_facade。"""

    def test_strategy_cancel_goes_via_board_submit(self, director: Director) -> None:
        """submit 后 directive 应在 board.pending (还没到 effective_at)。"""
        d = _make_strategy_cancel_directive(stage="all")
        director._submit_directives([d], now=10.0)
        # 还在 pending 中（1.5s delay 还没过）
        pending_ids = [p.id for p in director.board.pending]
        assert d.id in pending_ids

    def test_strategy_cancel_not_immediately_applied(self, director: Director) -> None:
        """submit 后未到 effective_at：facade.set_build 还没被调。"""
        facade = director.facade
        assert isinstance(facade, FakeFacade)
        d = _make_strategy_cancel_directive(stage="all")
        director._submit_directives([d], now=10.0)
        # 0.5s 后 tick，还没 commit
        director.on_tick(now=10.5)
        assert "sustain" not in facade.builds

    def test_strategy_cancel_applied_after_commit_delay(self, director: Director) -> None:
        """两层架构（2026-05-19）：cancel 不再切 sustain，改为 _apply_auto_persistent_switch。
        测试 library 没 persistent doctrine → 无操作；facade.builds 不含 sustain。"""
        facade = director.facade
        assert isinstance(facade, FakeFacade)
        d = _make_strategy_cancel_directive(stage="all")
        director._submit_directives([d], now=10.0)
        # 推过 effective_at (10 + 1.5 = 11.5)
        director.on_tick(now=12.0)
        # 旧行为已废弃：sustain 不再被调
        assert "sustain" not in facade.builds

    def test_strategy_cancel_clears_board_slot(self, director: Director) -> None:
        """commit 后 board.slots 对应 stage 被清 None。"""
        from vibecraft.directives.types import StageKind

        # 先手动在 board.slots 设置一个 opening slot（bypass delay）
        director.board.slots[StageKind.OPENING] = None
        # 用 set_initial_slot 绕过 delay
        director.board.set_initial_slot(StageKind.OPENING, "gate1_robo_opening", now=0.0)
        assert director.board.slots[StageKind.OPENING] is not None

        d = _make_strategy_cancel_directive(stage="all")
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)

        assert director.board.slots[StageKind.OPENING] is None

    def test_strategy_cancel_logged_in_directives_stream(
        self, session: GameSession, director: Director
    ) -> None:
        """board.submit 路径：directives.jsonl 有 STRATEGY_CANCEL submitted 记录。"""
        from vibecraft.logging_ import LogStream

        d = _make_strategy_cancel_directive()
        director._submit_directives([d], now=10.0)
        records = session.get_null_records(LogStream.DIRECTIVES)
        types = [r["type"] for r in records]
        assert "strategy_cancel" in types


# =========================================================================
# 两层架构（2026-05-19 P3 Step 10）：auto_persistent_switch
# =========================================================================


class TestAutoPersistentSwitch:
    """Director._apply_auto_persistent_switch + 集成测试。

    覆盖：
    - cancel 触发 set_build(persistent_id)，不再 set_build("sustain")
    - revoke_strategy 同上
    - no library / no my_race 时安全降级（warning log，no crash）
    - 选 cost 最低 doctrine（worked example 单测在 test_transition_cost.py）
    - STRATEGY_AUTO_SWITCH 事件被 log
    """

    def test_apply_auto_switch_picks_persistent_doctrine(self, session: GameSession) -> None:
        """直接调 _apply_auto_persistent_switch → facade.set_build(persistent_skytoss)"""
        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
            my_race="protoss",
        )
        chosen = director._apply_auto_persistent_switch(
            now=300.0, reason="cancel_redirected", caused_by="test"
        )
        # 库里现有多个 protoss persistent doctrine，按 transition_cost 选最低成本那个
        assert chosen is not None and chosen.startswith("persistent_")
        assert chosen in facade.builds

    def test_apply_auto_switch_no_library_no_crash(self, session: GameSession) -> None:
        """library / persistent doctrine 都空时不挂"""
        facade = FakeFacade()
        empty_lib = StrategyLibrary()  # 完全空 library
        director = _make_director(empty_lib, session, facade, {}, my_race="protoss")
        chosen = director._apply_auto_persistent_switch(now=300.0, reason="cancel_redirected")
        assert chosen is None  # 无 doctrine 可选
        assert "sustain" not in facade.builds  # 也不退到 sustain

    def test_cancel_triggers_auto_switch_event(self, session: GameSession) -> None:
        """完整流程：cancel directive commit → STRATEGY_AUTO_SWITCH 事件被 log"""
        from vibecraft.directives.models import Directive, StrategyCancelPayload

        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
            my_race="protoss",
        )
        d = Directive(payload=StrategyCancelPayload(stage="all"), issued_at=10.0)  # type: ignore[arg-type]
        director._submit_directives([d], now=10.0)
        director.on_tick(now=12.0)  # 推过 effective_at
        # auto_switch 事件被 log
        from vibecraft.logging_ import LogStream

        events = session.get_null_records(LogStream.EVENTS)
        kinds = [e.get("kind") for e in events]
        assert "strategy.auto_switch" in kinds
        # 事件 payload 含 chosen_id + cost + alternatives
        auto_switch_evt = next(e for e in events if e.get("kind") == "strategy.auto_switch")
        assert auto_switch_evt["payload"]["chosen_id"].startswith("persistent_")
        assert auto_switch_evt["payload"]["reason"] == "cancel_redirected"
        assert "cost" in auto_switch_evt["payload"]


# =========================================================================
# P0g Task 11: revoke_directive 扩 L2 (tactical override / squad) + L1 strategy
# =========================================================================


def _make_tactical_directive_a(verb: str = "attack") -> Directive:
    """构造 A 类 L2 TACTICAL_OBJECTIVE Directive（attack/defend 等 → override flag 路径）。"""
    from vibecraft.directives.models import TacticalObjectivePayload

    payload = TacticalObjectivePayload(verb=verb, target_area="enemy_natural")  # type: ignore[arg-type]
    return Directive(payload=payload, issued_at=10.0)


def _make_tactical_directive_b(unit_type: str = "Phoenix", count: int = 3) -> Directive:
    """构造 B 类 L2 TACTICAL_OBJECTIVE Directive（harass → squad 路径）。"""
    from vibecraft.directives.models import TacticalObjectivePayload

    payload = TacticalObjectivePayload(
        verb="harass",  # type: ignore[arg-type]
        target_area="enemy_main",
        unit_count_hint=count,
        unit_type_hint=[unit_type],
    )
    return Directive(payload=payload, issued_at=10.0)


class TestDefendHoldGatherPoint:
    """2026-06-03 用户:defend 也走 hold_gather_point —— 修"守瞭望塔却回家"。

    有目标(瞭望塔/分矿)→ set_hold_gather_point(该点);无目标→None
    (vendor zone_gather 自己挑离敌最近的己方分矿)。
    """

    def _director(self, session: GameSession, facade: FakeFacade) -> Director:
        return _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )

    def test_defend_with_target_sets_hold_gather_point(self, session: GameSession) -> None:
        """defend + 指定点 → set_hold_gather_point(该点),而非 None。"""
        facade = FakeFacade()
        director = self._director(session, facade)
        d = _make_tactical_directive_a(verb="defend")  # target_area="enemy_natural"
        # 隔离 target 解析,固定到已知点(瞭望塔)
        director._resolve_target_area = lambda area: (42.0, 60.0)  # type: ignore[method-assign]
        director._exec_tactical_objective(d, d.payload)
        assert facade.combat_intent_overrides[-1] == "defend"
        assert facade.hold_gather_points[-1] == (42.0, 60.0), (
            f"defend 有目标应 set_hold_gather_point(瞭望塔),实际 {facade.hold_gather_points}"
        )

    def test_defend_no_target_sets_hold_gather_point_none(self, session: GameSession) -> None:
        """defend 无指定点 → set_hold_gather_point(None)(vendor 挑前沿分矿)。"""
        from vibecraft.directives.models import TacticalObjectivePayload

        facade = FakeFacade()
        director = self._director(session, facade)
        payload = TacticalObjectivePayload(verb="defend", target_area=None)  # type: ignore[arg-type]
        d = Directive(payload=payload, issued_at=10.0)
        director._exec_tactical_objective(d, d.payload)
        assert facade.combat_intent_overrides[-1] == "defend"
        assert facade.hold_gather_points[-1] is None

    def test_attack_clears_hold_gather_point(self, session: GameSession) -> None:
        """非 hold/defend verb(attack)→ set_hold_gather_point(None),不残留旧点。"""
        facade = FakeFacade()
        director = self._director(session, facade)
        d = _make_tactical_directive_a(verb="attack")
        director._resolve_target_area = lambda area: (80.0, 80.0)  # type: ignore[method-assign]
        director._exec_tactical_objective(d, d.payload)
        assert facade.hold_gather_points[-1] is None


class TestPrerequisites:
    """2026-06-03 用户:指令卡展示前置条件(activate_when) —— 后端 _build_prerequisites。"""

    def _director(self, session: GameSession) -> Director:
        d = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            FakeFacade(),
            {},
        )
        d._bot = None  # 无 bot → 独立 check met 全 False(只测文案 + 展开)
        return d

    def test_describe_activation_tree_flattens_all_of(self, session: GameSession) -> None:
        director = self._director(session)
        aw = {
            "kind": "all_of",
            "conditions": [
                {
                    "kind": "structure_count",
                    "structure_type": "TWILIGHTCOUNCIL",
                    "op": ">=",
                    "value": 1,
                },
                {"kind": "tech_done", "upgrade_id": "CHARGE"},
            ],
        }
        out = director._describe_activation_tree(aw)
        # #5 i18n:CHARGE → 冲锋、TWILIGHTCOUNCIL → VC（走 Localizer，不露英文 id）
        assert [c["text"] for c in out] == ["已有 VC >=1", "完成升级 冲锋"]
        assert all(c["met"] is False for c in out)

    def test_build_prerequisites_unit_arrived(self, session: GameSession) -> None:
        from types import SimpleNamespace

        director = self._director(session)
        d = SimpleNamespace(
            payload=SimpleNamespace(activate_when={"kind": "unit_arrived", "area": "(42, 60)"})
        )
        out = director._build_prerequisites(d, now=0.0)
        assert len(out) == 1
        assert "到达" in out[0]["text"]  # 2026-06-06:通用文字(群组也用),不写死"农民"

    def test_build_prerequisites_none_returns_empty(self, session: GameSession) -> None:
        from types import SimpleNamespace

        director = self._director(session)
        d = SimpleNamespace(payload=SimpleNamespace(activate_when=None))
        assert director._build_prerequisites(d, now=0.0) == []


class TestStandingOrderUnitsLost:
    """#3 用户:持久指令(L3)认领的单位全死 → 卡片暗红"单位全失"(units_lost)后消失。"""

    def _setup(self, session: GameSession, tags: set[int]) -> tuple[Director, Directive]:
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = list(tags)
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)
        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)
        return director, d

    def _bot_with_tags(self, tags: set[int]) -> Any:
        from unittest.mock import MagicMock

        bot = MagicMock()
        bot.units = [MagicMock(tag=t) for t in tags]
        return bot

    def test_units_alive_keeps_active(self, session: GameSession) -> None:
        director, d = self._setup(session, {2001, 2002})
        director._bot = self._bot_with_tags({2001, 2002})
        director._tick_standing_order_deaths(now=11.0)
        assert director._override_status.get(d.id, {}).get("status") != "done"
        assert director._standing_order_tags[d.id] == {2001, 2002}

    def test_all_units_dead_marks_units_lost(self, session: GameSession) -> None:
        director, d = self._setup(session, {2001, 2002})
        director._bot = self._bot_with_tags(set())  # 全死
        director._tick_standing_order_deaths(now=12.0)
        st = director._override_status.get(d.id, {})
        assert st.get("status") == "done"
        assert st.get("reason") == "units_lost"
        assert d.id in director._done_at

    def test_units_lost_card_shows_dark_red_status(self, session: GameSession) -> None:
        director, d = self._setup(session, {2001, 2002})
        director._bot = self._bot_with_tags(set())
        director._tick_standing_order_deaths(now=12.0)
        cards = director._build_command_cards(now=12.0)
        l3 = [c for c in cards if c["id"] == d.id]
        assert l3, "L3 卡片应仍在(grace 期暗红显示)"
        assert l3[0]["status"] == "done"
        assert l3[0]["status_reason"] == "units_lost"

    def test_partial_death_prunes_dead_tags(self, session: GameSession) -> None:
        director, d = self._setup(session, {2001, 2002})
        director._bot = self._bot_with_tags({2001})  # 2002 死
        director._tick_standing_order_deaths(now=11.0)
        assert director._standing_order_tags[d.id] == {2001}
        assert director._override_status.get(d.id, {}).get("status") != "done"

    def test_never_claimed_no_termination(self, session: GameSession) -> None:
        """从未认领到单位(selector 空)→ tags 空 → 不算"单位死光",不终止。"""
        director, d = self._setup(session, set())
        director._bot = self._bot_with_tags(set())
        director._tick_standing_order_deaths(now=11.0)
        assert director._override_status.get(d.id, {}).get("status") != "done"

    def test_grace_expiry_removes_from_standing_orders(self, session: GameSession) -> None:
        """全死 → grace 过后从 standing_orders 删 → 卡片消失。"""
        director, d = self._setup(session, {2001, 2002})
        director._bot = self._bot_with_tags(set())
        director._tick_standing_order_deaths(now=12.0)
        assert any(s.id == d.id for s in director.standing_orders)
        # grace(_DONE_GRACE_S=2s)过后 on_tick 清理
        director.on_tick(now=12.0 + director._DONE_GRACE_S + 1.0)
        assert not any(s.id == d.id for s in director.standing_orders)


class TestDirectiveExecutionSafety:
    """2026-06-03 用户:指令执行出错应报错(卡片)不应崩整局。

    根因:'探路的追猎火力侦查' → unit_claim selector 解析不到单位,
    facade.resolve_selector 返 None → `for tag in None` TypeError → bot 抛异常 → match 结束。
    """

    def _director(self, session: GameSession) -> Director:
        return _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            FakeFacade(),
            {},
        )

    def test_resolve_selector_none_returns_empty(self, session: GameSession) -> None:
        """facade.resolve_selector 返 None → helper 返 []（永不 None,防下游裸迭代崩）。"""
        from types import SimpleNamespace

        director = self._director(session)
        director.facade.resolve_selector = lambda **kw: None  # type: ignore[method-assign]
        sel = SimpleNamespace(
            unit_type="Stalker", tag=None, tags=None, count=None, group_id=None, chain_id=None
        )
        assert director._resolve_selector_with_count(sel) == []

    def test_dispatch_catches_apply_exception_no_crash(self, session: GameSession) -> None:
        """_apply_to_facade 抛异常 → _dispatch_committed_to_facade 吞掉 + 卡片标'执行出错',不冒泡。"""
        from vibecraft.directives.models import ViewFollowPayload

        director = self._director(session)
        d = Directive(payload=ViewFollowPayload(unit_type="Stalker"), issued_at=1.0)
        director._in_flight[d.id] = d

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("boom")

        director._apply_to_facade = _boom  # type: ignore[method-assign]
        director._dispatch_committed_to_facade(d.id, now=1.0)  # 不应抛
        st = director._override_status.get(d.id)
        assert st is not None
        assert st["status"] == "on_hold"
        assert "执行出错" in st["reason"]

    def _sel(self, **kw: object) -> object:
        from types import SimpleNamespace

        base = {
            "unit_type": None,
            "tag": None,
            "tags": None,
            "count": None,
            "group_id": None,
            "chain_id": None,
            "assigned_spot": None,
            "primary_verb_prefix": None,
        }
        base.update(kw)
        return SimpleNamespace(**base)

    def test_semantics_reselect_by_assigned_spot(self, session: GameSession) -> None:
        """assigned_spot 按"指派时记下的守的地点"重选 tag；'watchtower' 模糊命中 'watchtower_left'。"""
        from types import SimpleNamespace

        director = self._director(session)
        # 注册表：111 守 watchtower_left 的追猎；222 守 own_clock_7 的叉子
        director._unit_semantics = {
            111: {"spot": "watchtower_left", "verb": "hold_position", "unit_type": "Stalker"},
            222: {"spot": "own_clock_7", "verb": "guard_position", "unit_type": "Zealot"},
        }
        director._bot = SimpleNamespace(units=[SimpleNamespace(tag=111), SimpleNamespace(tag=222)])
        # "守瞭望塔的追猎"：assigned_spot=watchtower(模糊) + unit_type=Stalker → 111
        assert director._resolve_selector_with_count(
            self._sel(unit_type="Stalker", assigned_spot="watchtower")
        ) == [111]
        # "守 7 点的叉子" → 222
        assert director._resolve_selector_with_count(
            self._sel(unit_type="Zealot", assigned_spot="own_clock_7")
        ) == [222]

    def test_semantics_reselect_by_verb_prefix(self, session: GameSession) -> None:
        """primary_verb_prefix='guard_' 按任务类型重选；死单位被剔除。"""
        from types import SimpleNamespace

        director = self._director(session)
        director._unit_semantics = {
            111: {"spot": "watchtower_left", "verb": "hold_position", "unit_type": "Stalker"},
            222: {"spot": "own_clock_7", "verb": "guard_position", "unit_type": "Zealot"},
            333: {"spot": "own_clock_5", "verb": "guard_position", "unit_type": "Zealot"},
        }
        # 333 已死(不在 bot.units) → 应剔除
        director._bot = SimpleNamespace(units=[SimpleNamespace(tag=111), SimpleNamespace(tag=222)])
        assert director._resolve_selector_with_count(self._sel(primary_verb_prefix="guard_")) == [
            222
        ]

    def test_record_unit_semantics_from_standing_order(self, session: GameSession) -> None:
        """指派 standing order → 把 spot/verb/unit_type 语意挂到每个 tag。"""
        from vibecraft.directives.models import Directive, UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        director = self._director(session)
        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker", count=1),
                task=Task(
                    primary_action=Action(
                        verb=Verb.HOLD_POSITION,
                        target=TargetSpec(kind="named_spot", named_spot="watchtower_left"),
                    )
                ),
                persistent=True,
            ),
            issued_at=1.0,
        )
        director._record_unit_semantics(d, [4355784705])
        sem = director._unit_semantics[4355784705]
        assert sem["spot"] == "watchtower_left"
        assert sem["verb"] == "hold_position"
        assert sem["unit_type"] == "Stalker"


class TestHistoryThreeLayer:
    """#4 用户:历史指令三层展开 —— 输入文本 + 识别解读 + directive 状态。"""

    def _director(self, session: GameSession) -> Director:
        return _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            FakeFacade(),
            {},
        )

    def test_build_recent_commands_resolves_live_terminal_ended(self, session: GameSession) -> None:
        from vibecraft.bot.director import _RecentCommand

        director = self._director(session)
        director._recent_commands = [
            _RecentCommand(
                text="派农民修水晶",
                ts=10.0,
                interpretation_zh="代理建造",
                directive_ids=["d1", "d2", "d3"],
            )
        ]
        # d1 live(active + 进度)、d2 终态 cancelled、d3 既无卡也无终态 → ended
        card_by_id = {
            "d1": {
                "id": "d1",
                "display": "建 BE 在 (42,60)",
                "status": "active",
                "status_reason": "",
                "conditions": [
                    {
                        "text": "造 4 个 叉子",
                        "met": False,
                        "progress": {"current": 1, "target": 4, "unit": "个"},
                    }
                ],
            }
        }
        director._directive_terminal = {"d2": {"status": "cancelled", "display": "进攻 二矿"}}

        out = director._build_recent_commands(card_by_id)
        assert len(out) == 1
        rc = out[0]
        assert rc["text"] == "派农民修水晶"
        assert rc["interpretation_zh"] == "代理建造"
        ds = {d["id"]: d for d in rc["directives"]}
        assert ds["d1"]["status"] == "active"
        assert ds["d1"]["progress"] == {"current": 1, "target": 4, "unit": "个"}
        assert ds["d2"]["status"] == "cancelled"
        assert ds["d2"]["display"] == "进攻 二矿"
        assert ds["d3"]["status"] == "ended"
        # 聚合状态：d1 active → 整条 active
        assert rc["status"] == "active"

    def test_recent_command_status_failed(self, session: GameSession) -> None:
        """ParseError → _RecentCommand.failed=True → 整条 status='failed'（识别失败标红）。"""
        from vibecraft.bot.director import _RecentCommand

        director = self._director(session)
        director._recent_commands = [_RecentCommand(text="阿巴阿巴", ts=5.0, failed=True)]
        out = director._build_recent_commands({})
        assert out[0]["status"] == "failed"

    def test_aggregate_command_status_priority(self, session: GameSession) -> None:
        director = self._director(session)
        agg = director._aggregate_command_status
        assert agg(True, [{"status": "completed"}]) == "failed"  # failed 最优先
        assert agg(False, [{"status": "active"}, {"status": "completed"}]) == "active"
        assert agg(False, [{"status": "pending"}, {"status": "completed"}]) == "pending"
        assert agg(False, [{"status": "waiting"}]) == "pending"
        assert agg(False, [{"status": "terminated"}, {"status": "completed"}]) == "terminated"
        assert agg(False, [{"status": "cancelled"}]) == "cancelled"
        assert agg(False, [{"status": "completed"}, {"status": "ended"}]) == "completed"
        assert agg(False, []) == "completed"

    def test_normalize_done_units_lost_terminated(self, session: GameSession) -> None:
        director = self._director(session)
        st, _prog = director._normalize_history_status(
            {"status": "done", "status_reason": "units_lost"}
        )
        assert st == "terminated"
        st2, _ = director._normalize_history_status({"status": "done", "status_reason": "已完成"})
        assert st2 == "completed"
        st3, _ = director._normalize_history_status({"status": "waiting"})
        assert st3 == "waiting"

    def test_release_records_terminal_completed(self, session: GameSession) -> None:
        """_release_directive_done(非 units_lost) → 终态 completed。"""
        from vibecraft.directives.models import TacticalObjectivePayload

        director = self._director(session)
        payload = TacticalObjectivePayload(verb="defend", target_area=None)  # type: ignore[arg-type]
        d = Directive(payload=payload, issued_at=10.0)
        director._release_directive_done(d, now=11.0, reason="done")
        assert director._directive_terminal[d.id]["status"] == "completed"

    def test_release_records_terminal_terminated_on_units_lost(self, session: GameSession) -> None:
        from vibecraft.directives.models import TacticalObjectivePayload

        director = self._director(session)
        payload = TacticalObjectivePayload(verb="attack", target_area=None)  # type: ignore[arg-type]
        d = Directive(payload=payload, issued_at=10.0)
        director._release_directive_done(d, now=11.0, reason="units_lost")
        assert director._directive_terminal[d.id]["status"] == "terminated"

    def test_directive_display_for_tactical_chinese(self, session: GameSession) -> None:
        from vibecraft.directives.models import TacticalObjectivePayload

        director = self._director(session)
        payload = TacticalObjectivePayload(verb="attack", target_area="enemy_natural")  # type: ignore[arg-type]
        d = Directive(payload=payload, issued_at=10.0)
        # #5 联动:中文 verb（进攻），不露英文 id
        assert director._directive_display_for(d).startswith("进攻")


class TestRevokeDirectiveExtended:
    """P0g Task 11: revoke_directive 扩 L2 + L1。"""

    # ------------------------------------------------------------------
    # L2 A 类: attack → override flag
    # ------------------------------------------------------------------

    def test_revoke_l2_tactical_global_clears_facade_overrides(self, session: GameSession) -> None:
        """L2 A 类 revoke: 清 facade.set_attack_target_override(None) + set_combat_intent_override(None)。"""
        facade = FakeFacade()
        # 注入 selector stub 防止 resolve_selector 出错
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        d = _make_tactical_directive_a(verb="attack")
        # 直接调 _exec_tactical_objective（绕过 board 延迟）
        director._exec_tactical_objective(d, d.payload)
        # 确认 override 已记录
        assert facade.combat_intent_overrides and facade.combat_intent_overrides[-1] == "attack"

        result = director.revoke_directive(d.id, now=20.0)

        assert result is True
        # facade 被调清
        assert facade.attack_target_overrides[-1] is None
        assert facade.combat_intent_overrides[-1] is None
        # _tactical_overrides 清掉
        assert d.id not in director._tactical_overrides
        assert director._current_l2_global_id is None

    def test_revoke_l2_tactical_global_returns_false_if_unknown(self, session: GameSession) -> None:
        """未知 id revoke_tactical 返 False。"""
        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        assert director.revoke_tactical("nonexistent_id", now=10.0) is False

    # ------------------------------------------------------------------
    # L2 B 类: harass → squad
    # ------------------------------------------------------------------

    def test_revoke_l2_tactical_squad_releases_unit_roles(self, session: GameSession) -> None:
        """L2 B 类 revoke: 释放 unit_role 还给 sharpy + 清 _tactical_squads。"""
        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [101, 102, 103]
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        d = _make_tactical_directive_b(unit_type="Phoenix", count=3)
        director._exec_tactical_objective(d, d.payload)
        # 确认 squad 已建立 + 单位被接管
        assert d.id in director._tactical_squads
        assert 101 in facade.unit_roles
        assert 102 in facade.unit_roles
        assert 103 in facade.unit_roles

        result = director.revoke_directive(d.id, now=20.0)

        assert result is True
        # release_unit_role 被调：unit_roles 被清
        assert 101 not in facade.unit_roles
        assert 102 not in facade.unit_roles
        assert 103 not in facade.unit_roles
        # squad 清掉
        assert d.id not in director._tactical_squads

    # ------------------------------------------------------------------
    # L1 strategy
    # ------------------------------------------------------------------

    def test_revoke_l1_strategy_clears_board_slot(self, session: GameSession) -> None:
        """两层架构（2026-05-19）：revoke_strategy 清 board slot + 调
        _apply_auto_persistent_switch（不再 set_build 'sustain'）。

        真实 protoss library 有多个 persistent doctrine，会切到 transition_cost 最低的那个。"""
        from vibecraft.directives.types import StageKind

        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        # 注入 midgame slot（bypass delay）
        director.board.set_initial_slot(StageKind.MIDGAME, "iac_2base", now=0.0)
        assert director.board.slots[StageKind.MIDGAME] is not None

        result = director.revoke_directive("l1_midgame", now=20.0)

        assert result is True
        assert director.board.slots[StageKind.MIDGAME] is None
        # 新行为：facade.set_build 被调，且参数是 persistent doctrine id（不是 sustain）
        assert "sustain" not in facade.builds
        # 真实 library 有多个 persistent doctrine，按 transition_cost 选最低成本那个
        assert any(b.startswith("persistent_") for b in facade.builds)

    def test_revoke_l1_strategy_empty_slot_returns_false(self, session: GameSession) -> None:
        """revoke_strategy 对 None slot 返 False。"""
        from vibecraft.directives.types import StageKind

        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        # LATEGAME slot 本来就是 None
        assert director.board.slots[StageKind.LATEGAME] is None

        result = director.revoke_directive("l1_lategame", now=20.0)

        assert result is False

    def test_revoke_unknown_id_returns_false(self, session: GameSession) -> None:
        """完全不存在的 id revoke_directive 返 False。"""
        facade = FakeFacade()
        director = _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            facade,
            {},
        )
        assert director.revoke_directive("d_doesntexist", now=10.0) is False


# =========================================================================
# Task #311 player override e2e: scheduled player action(模拟玩家按 UI 按钮)
# =========================================================================


class TestScheduledPlayerAction:
    """Director.on_tick 到点自动 fire 玩家时间线项,等价 UI 按钮按下。"""

    def test_action_fires_at_game_time(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """now < at_s 不 fire;now >= at_s 触发 combat_intent_override。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._scheduled_player_actions = [
            {"at_s": 100.0, "verb": "retreat", "mode": None, "target_area": None},
        ]

        director.on_tick(now=99.0)
        # 没到点:facade 没收到 combat_intent_override / attack_mode_override
        assert facade.combat_intent_overrides == []
        assert facade.attack_mode_overrides == []
        assert director._fired_player_actions == set()

        director.on_tick(now=100.0)
        # 到点:Director 已 fire,Board commit_delay=0,在同一 on_tick 内的
        # board.tick() 会 dispatch COMMITTED → facade.set_combat_intent_override
        assert "retreat" in facade.combat_intent_overrides
        assert 0 in director._fired_player_actions

    def test_action_only_fires_once(self, library: StrategyLibrary, session: GameSession) -> None:
        """同 action 跨多个 tick 只 fire 一次(防同帧重触发)。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._scheduled_player_actions = [
            {"at_s": 50.0, "verb": "defend", "mode": None, "target_area": None},
        ]

        director.on_tick(now=60.0)
        first_intents = list(facade.combat_intent_overrides)
        assert "defend" in first_intents

        director.on_tick(now=70.0)
        director.on_tick(now=80.0)
        # 后续 tick 不再额外 set retreat/defend(可能因 standing order persistent
        # 在 tick 内被 re-applied,但 _fired set 拦住了再次 submit_directive)
        assert director._fired_player_actions == {0}

    def test_attack_mode_set_before_directive_submit(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """mode='all_in' 时 facade.set_attack_mode_override 在 submit_directive 前调,
        防 ZoneAttack 同帧读到 intent=attack 但 mode 还没设导致 force_attack 漏判。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._scheduled_player_actions = [
            {"at_s": 10.0, "verb": "attack", "mode": "all_in", "target_area": "enemy_main"},
        ]

        director.on_tick(now=10.0)
        # attack_mode_override 收到 all_in
        assert "all_in" in facade.attack_mode_overrides
        # combat_intent_override 收到 attack
        assert "attack" in facade.combat_intent_overrides
        # 调用顺序:attack_mode_override 必须在 combat_intent_override 之前出现
        # (set_attack_mode_override 直接调,combat_intent_override 经 board commit 后 dispatch)
        records = [c.method for c in facade.calls]
        i_mode = next(i for i, m in enumerate(records) if m == "set_attack_mode_override")
        i_intent = next(i for i, m in enumerate(records) if m == "set_combat_intent_override")
        assert i_mode < i_intent

    def test_no_actions_no_change(self, library: StrategyLibrary, session: GameSession) -> None:
        """空 _scheduled_player_actions on_tick 完全无副作用(生产路径不受影响)。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        # 不设 _scheduled_player_actions(默认 [])
        director.on_tick(now=100.0)
        director.on_tick(now=200.0)
        assert facade.combat_intent_overrides == []
        assert facade.attack_mode_overrides == []
        assert director._fired_player_actions == set()

    def test_multiple_actions_fire_in_order(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """多个 action,各自到点都 fire。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._scheduled_player_actions = [
            {"at_s": 100.0, "verb": "attack", "mode": "all_in", "target_area": None},
            {"at_s": 200.0, "verb": "retreat", "mode": None, "target_area": None},
        ]

        director.on_tick(now=100.0)
        assert director._fired_player_actions == {0}

        director.on_tick(now=200.0)
        assert director._fired_player_actions == {0, 1}
        # combat_intent 收到了两个 intent(attack 然后 retreat)
        intents = [i for i in facade.combat_intent_overrides if i is not None]
        assert intents[0] == "attack"
        assert "retreat" in intents


# ---------------------------------------------------------------------------
# WP-A: 控制边界可视化 — _build_controlled_units_view
# ---------------------------------------------------------------------------


def _make_fake_bot_units(owned: dict[int, str]):
    """构造带 .units（可迭代 + by_tag）的 fake bot，供 _build_controlled_units_view 测试用。

    owned: {tag: type_name_str}，例如 {101: "STALKER", 200: "IMMORTAL"}。
    """
    from types import SimpleNamespace

    def mk(tag: int, name: str):
        return SimpleNamespace(tag=tag, type_id=SimpleNamespace(name=name))

    units = [mk(t, n) for t, n in owned.items()]
    by = {t: mk(t, n) for t, n in owned.items()}

    class _UnitCollection:
        def __init__(self, items, bytag):
            self._i = items
            self._b = bytag

        def __iter__(self):
            return iter(self._i)

        def by_tag(self, t):
            return self._b.get(t)

    c = _UnitCollection(units, by)
    return SimpleNamespace(units=c)


class TestControlledUnitsView:
    """控制边界数据：哪些单位归玩家指令、哪些 bot 自由（WP-A）。"""

    def _director_with_units(self, session, owned: dict[int, str]) -> Director:
        """构造带 fake bot.units 的 director。owned: {tag: type_name}。"""
        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        d._bot = _make_fake_bot_units(owned)
        return d

    def test_command_units_grouped_with_label_and_composition(self, session) -> None:
        """命令组：_standing_order_tags 里的 tags 按存活 tag 聚合 composition，source=command，color=cyan。"""
        d = self._director_with_units(
            session,
            {101: "STALKER", 102: "STALKER", 200: "IMMORTAL", 201: "IMMORTAL"},
        )
        d._standing_order_tags["d_aa"] = {101, 102}  # 一条指令控 2 追猎
        d._override_status["d_aa"] = {"status": "active", "reason": ""}
        # 直接注入 label 返回函数（覆盖方法），模拟 card label 存在
        d._controlled_label_for = lambda did: "守瞭望塔" if did == "d_aa" else ""  # type: ignore[method-assign]
        view = d._build_controlled_units_view()
        ctrl = view["controlled"]
        assert len(ctrl) == 1
        assert ctrl[0]["directive_id"] == "d_aa"
        assert ctrl[0]["source"] == "command"
        assert ctrl[0]["color"] == "cyan"
        assert ctrl[0]["composition"] == {"STALKER": 2}
        assert ctrl[0]["count"] == 2
        # 200/201 不在任何指令 → bot_free
        assert view["bot_free"]["composition"] == {"IMMORTAL": 2}
        assert view["bot_free"]["count"] == 2

    def test_group_command_uses_group_color(self, session) -> None:
        """编队指挥：_group_command_gid 注入 → source=group，color=g{gid}。"""
        d = self._director_with_units(session, {301: "VOIDRAY", 302: "VOIDRAY"})
        d._standing_order_tags["d_g"] = {301, 302}
        d._group_command_gid = {"d_g": 1}  # 直接注入 group_id 反查表
        view = d._build_controlled_units_view()
        e = view["controlled"][0]
        assert e["source"] == "group"
        assert e["group_id"] == 1
        assert e["color"] == "g1"
        assert e["count"] == 2

    def test_dead_tags_excluded(self, session) -> None:
        """死亡 tag（bot.units 无此 tag）不计入 count/composition。"""
        d = self._director_with_units(session, {101: "STALKER"})  # 102 已死（未在 owned 里）
        d._standing_order_tags["d_aa"] = {101, 102}
        view = d._build_controlled_units_view()
        assert view["controlled"][0]["count"] == 1  # 只算存活的 101
        assert view["controlled"][0]["composition"] == {"STALKER": 1}

    def test_bot_free_excludes_workers(self, session) -> None:
        """bot_free 排除农民（PROBE/DRONE/SCV），只统计非农民军队单位。"""
        d = self._director_with_units(
            session,
            {1: "STALKER", 2: "PROBE", 3: "IMMORTAL"},
        )
        # 无任何指令 → 全部 bot_free 候选
        view = d._build_controlled_units_view()
        # PROBE 排除，STALKER + IMMORTAL 留下
        comp = view["bot_free"]["composition"]
        assert "PROBE" not in comp
        assert comp.get("STALKER") == 1
        assert comp.get("IMMORTAL") == 1
        assert view["bot_free"]["count"] == 2

    def test_empty_standing_orders_gives_empty_controlled(self, session) -> None:
        """无任何 _standing_order_tags → controlled 为空列表，bot_free 含全部非农民单位。"""
        d = self._director_with_units(session, {50: "ZEALOT", 51: "ZEALOT"})
        view = d._build_controlled_units_view()
        assert view["controlled"] == []
        assert view["bot_free"]["count"] == 2

    def test_claimed_tags_not_in_bot_free(self, session) -> None:
        """受 standing_order 控制的 tags 不出现在 bot_free 里。"""
        d = self._director_with_units(
            session,
            {10: "STALKER", 20: "STALKER", 30: "STALKER"},
        )
        d._standing_order_tags["d_x"] = {10, 20}  # 10/20 被指令控
        view = d._build_controlled_units_view()
        # controlled 里有 10/20
        ctrl_tags_in_comp = view["controlled"][0]["composition"]
        assert ctrl_tags_in_comp == {"STALKER": 2}
        # bot_free 只有 30
        assert view["bot_free"]["count"] == 1
        assert view["bot_free"]["composition"] == {"STALKER": 1}

    # ------------------------------------------------------------------
    # Task 2: snapshot 透传 controlled_units
    # ------------------------------------------------------------------

    def test_snapshot_includes_controlled_units(self, session) -> None:
        """build_snapshot 返回值包含 controlled_units，有 controlled/bot_free 两个 key。"""
        d = self._director_with_units(session, {101: "STALKER"})
        d._standing_order_tags["d_aa"] = {101}
        snap = d.build_snapshot(now=10.0)
        assert "controlled_units" in snap
        cu = snap["controlled_units"]
        assert "controlled" in cu and "bot_free" in cu

    def test_snapshot_controlled_units_label_uses_card_index(self, session) -> None:
        """build_snapshot 调用后 _card_label_index 已填充；若有同 id 的 command card，
        _controlled_label_for 应能复用其 display 标签。"""
        d = self._director_with_units(session, {101: "STALKER"})
        d._standing_order_tags["d_aa"] = {101}
        # 手动注入 label index（模拟 build_snapshot 建好 cmd_cards 后的结果）
        d._card_label_index = {"d_aa": "守瞭望塔"}
        label = d._controlled_label_for("d_aa")
        assert label == "守瞭望塔"

    # ------------------------------------------------------------------
    # Task 7: _push_debug_marks
    # ------------------------------------------------------------------

    def test_push_debug_marks_from_controlled(self, session) -> None:
        """_push_debug_marks() 后 facade.debug_marks 每组一条，含 shape/color/tags。"""
        d = self._director_with_units(session, {101: "STALKER", 102: "STALKER"})
        d._standing_order_tags["d_aa"] = {101, 102}
        d._push_debug_marks()
        assert len(d.facade.debug_marks) == 1
        mark = d.facade.debug_marks[0]
        assert mark["shape"] == "box"  # 指令卡(无 group_id) → 方框
        assert isinstance(mark["color"], tuple)
        assert sorted(mark["tags"]) == [101, 102]

    def test_push_debug_marks_group_uses_ring(self, session) -> None:
        """编队单位(_voice_groups) → shape=ring + 队色 + 队号 label。"""
        d = self._director_with_units(session, {301: "VOIDRAY", 302: "VOIDRAY"})
        d._voice_groups[1] = {301, 302}
        d._push_debug_marks()
        rings = [m for m in d.facade.debug_marks if m["shape"] == "ring"]
        assert len(rings) == 1
        assert rings[0]["label"] == "team1"
        assert sorted(rings[0]["tags"]) == [301, 302]

    def test_push_debug_marks_pure_group_without_directive(self, session) -> None:
        """纯编队(无任何 standing 指令) → 仍出圆环。

        回归:修复"编了队但游戏里没圆环"——旧逻辑只从指令(_standing_order_tags)
        构建,group_assign 提交后即 released 不留指令 → 环画不出。新逻辑直接从
        _voice_groups 出环。
        """
        d = self._director_with_units(session, {401: "PHOENIX", 402: "PHOENIX"})
        d._voice_groups[2] = {401, 402}  # 纯编队,无 _standing_order_tags
        d._push_debug_marks()
        assert len(d.facade.debug_marks) == 1
        m = d.facade.debug_marks[0]
        assert m["shape"] == "ring"
        assert m["label"] == "team2"
        assert sorted(m["tags"]) == [401, 402]

    def test_push_debug_marks_grouped_unit_ring_not_box_keeps_line(
        self, session, monkeypatch
    ) -> None:
        """单位既在编队又有指令 → 只出圆环(不重复出框),但指令目标线挂到环上。"""
        d = self._director_with_units(session, {501: "STALKER", 502: "STALKER"})
        d._voice_groups[1] = {501, 502}
        d._standing_order_tags["d_go"] = {501, 502}  # 同一批单位也被指令控
        monkeypatch.setattr(
            d, "_directive_verb_and_target", lambda did: ("attack_move", (50.0, 60.0))
        )
        d._push_debug_marks()
        # 编队单位不重复出框:只有 ring,没有 box
        assert all(m["shape"] == "ring" for m in d.facade.debug_marks)
        assert len(d.facade.debug_marks) == 1
        ring = d.facade.debug_marks[0]
        assert ring["label"] == "team1"
        assert ring["target"] == [50.0, 60.0]  # 指令目标线照画

    def test_push_debug_marks_ungrouped_directive_unit_is_box(self, session) -> None:
        """不在编队 + 有指令 → 方框(与编队单位互斥)。"""
        d = self._director_with_units(session, {601: "IMMORTAL", 701: "ZEALOT"})
        d._voice_groups[1] = {701}  # 701 在编队
        d._standing_order_tags["d_b"] = {601}  # 601 只有指令,不在编队
        d._push_debug_marks()
        boxes = [m for m in d.facade.debug_marks if m["shape"] == "box"]
        rings = [m for m in d.facade.debug_marks if m["shape"] == "ring"]
        assert len(boxes) == 1 and boxes[0]["tags"] == [601]
        assert len(rings) == 1 and rings[0]["tags"] == [701]

    def test_push_debug_marks_disabled_clears_marks(self, session) -> None:
        """debug_draw_control_boundary=False 时 _push_debug_marks() 推空 list（清屏）。"""
        from vibecraft.bot.director import DirectorConfig

        d = self._director_with_units(session, {101: "STALKER"})
        d._standing_order_tags["d_aa"] = {101}
        d.config = DirectorConfig(debug_draw_control_boundary=False)
        # 先推一次有内容的 marks
        d.facade.set_debug_marks([{"tag": 101, "color": (0, 220, 255), "label": "x"}])
        d._push_debug_marks()
        assert d.facade.debug_marks == []

    def test_push_debug_marks_dead_tags_not_in_marks(self, session) -> None:
        """死亡 tag（bot.units 无）不出现在某组的 tags 里。"""
        d = self._director_with_units(session, {101: "STALKER"})  # 102 已死
        d._standing_order_tags["d_aa"] = {101, 102}
        d._push_debug_marks()
        tags = d.facade.debug_marks[0]["tags"]
        assert 101 in tags
        assert 102 not in tags


# ---------------------------------------------------------------------------
# WP-B: 状态属性指代 — _filter_by_unit_state
# ---------------------------------------------------------------------------


def _make_fake_bot_with_hp(units_data: dict[int, dict]) -> object:
    """构造带 health_percentage / shield_percentage 的 fake bot。

    units_data: {tag: {"type": str, "hp": float, "shield": float}}
    hp/shield 是 0-1 之间的浮点（python-sc2 convention）。
    """
    from types import SimpleNamespace

    def mk(tag: int, info: dict):
        return SimpleNamespace(
            tag=tag,
            type_id=SimpleNamespace(name=info.get("type", "STALKER")),
            health_percentage=info.get("hp", 1.0),
            shield_percentage=info.get("shield", 1.0),
        )

    units_list = [mk(t, d) for t, d in units_data.items()]
    by_tag = {t: mk(t, d) for t, d in units_data.items()}

    class _UC:
        def __init__(self, items, bt):
            self._i = items
            self._b = bt

        def __iter__(self):
            return iter(self._i)

        def by_tag(self, t):
            return self._b.get(t)

    return SimpleNamespace(units=_UC(units_list, by_tag))


def _director_for_state_test(session) -> Director:
    """构造用于 WP-B 单测的 Director，带 FakeFacade + MockLLMProvider（空回复）。"""
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session)


class TestUnitStateSelector:
    """WP-B: selector.health_below_pct / shield_below_pct 状态过滤。"""

    def _sel(self, **kw):
        """快速构造带血量/盾量字段的 Selector。"""
        from vibecraft.directives.scope import Selector

        return Selector(**kw)

    # ------------------------------------------------------------------
    # _filter_by_unit_state 直接单测
    # ------------------------------------------------------------------

    def test_health_below_pct_keeps_only_low_health(self, session) -> None:
        """health_below_pct=50 → 只保留血量 < 50% 的 tag。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp(
            {
                101: {"hp": 0.3, "shield": 1.0},  # 血量 30% → 低于 50 → 保留
                102: {"hp": 0.8, "shield": 1.0},  # 血量 80% → 高于 50 → 丢弃
                103: {"hp": 0.49, "shield": 0.0},  # 血量 49% → 低于 50 → 保留
            }
        )
        sel = self._sel(health_below_pct=50.0)
        result = d._filter_by_unit_state([101, 102, 103], sel)
        assert sorted(result) == [101, 103]

    def test_shield_below_pct_keeps_only_low_shield(self, session) -> None:
        """shield_below_pct=20 → 只保留护盾 < 20% 的 tag。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp(
            {
                201: {"hp": 1.0, "shield": 0.1},  # 盾 10% → 低于 20 → 保留
                202: {"hp": 1.0, "shield": 0.5},  # 盾 50% → 高于 20 → 丢弃
                203: {"hp": 0.2, "shield": 0.19},  # 盾 19% → 低于 20 → 保留
            }
        )
        sel = self._sel(shield_below_pct=20.0)
        result = d._filter_by_unit_state([201, 202, 203], sel)
        assert sorted(result) == [201, 203]

    def test_health_and_shield_and_condition(self, session) -> None:
        """两个字段同时填 → AND：血量 AND 护盾都低才保留。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp(
            {
                301: {"hp": 0.3, "shield": 0.1},  # 血低 + 盾低 → 保留
                302: {"hp": 0.3, "shield": 0.6},  # 血低 + 盾高 → 丢弃
                303: {"hp": 0.8, "shield": 0.1},  # 血高 + 盾低 → 丢弃
                304: {"hp": 0.8, "shield": 0.6},  # 两者都高 → 丢弃
            }
        )
        sel = self._sel(health_below_pct=50.0, shield_below_pct=20.0)
        result = d._filter_by_unit_state([301, 302, 303, 304], sel)
        assert result == [301]

    def test_no_fields_passthrough(self, session) -> None:
        """health_below_pct=None 且 shield_below_pct=None → 原样返回，不影响现有行为。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp({1: {"hp": 1.0, "shield": 1.0}})
        sel = self._sel()
        tags = [1, 2, 3]
        result = d._filter_by_unit_state(tags, sel)
        assert result == tags

    def test_unknown_tag_discarded(self, session) -> None:
        """by_tag 返回 None（tag 已死/不存在）→ 该 tag 丢弃。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp(
            {
                401: {"hp": 0.2, "shield": 0.0},
            }
        )
        # tag 999 不在 bot.units
        sel = self._sel(health_below_pct=50.0)
        result = d._filter_by_unit_state([401, 999], sel)
        assert result == [401]

    def test_bot_none_returns_empty(self, session) -> None:
        """_bot 为 None（游戏未开始）→ 全部丢弃（无法确认血量状态）。"""
        d = _director_for_state_test(session)
        d._bot = None
        sel = self._sel(health_below_pct=50.0)
        result = d._filter_by_unit_state([1, 2, 3], sel)
        assert result == []

    # ------------------------------------------------------------------
    # _resolve_selector_with_count 集成：先过滤后 count 截断
    # ------------------------------------------------------------------

    def test_filter_before_count_truncation(self, session) -> None:
        """_resolve_selector_with_count: 先状态过滤再 count 截断（"残血里选 2 个"）。"""
        d = _director_for_state_test(session)
        d._bot = _make_fake_bot_with_hp(
            {
                501: {"hp": 0.2},
                502: {"hp": 0.3},
                503: {"hp": 0.4},
                504: {"hp": 0.9},  # 血量高 → 过滤掉
            }
        )

        class _FakeSelector:
            unit_type = None
            tag = None
            tags: list[int] = [501, 502, 503, 504]
            count = 2
            group_id = None
            chain_id = None
            assigned_spot = None
            primary_verb_prefix = None
            health_below_pct = 50.0
            shield_below_pct = None

        result = d._resolve_selector_with_count(_FakeSelector())
        # 应先从 [501,502,503] 里截 2 个（504 被过滤），不是先截 2 个再过滤
        assert len(result) == 2
        assert all(t in [501, 502, 503] for t in result)
        assert 504 not in result

    # ------------------------------------------------------------------
    # Selector 字段校验
    # ------------------------------------------------------------------

    def test_health_below_pct_validation_error(self) -> None:
        """health_below_pct 越界（>100）→ pydantic ValidationError。"""
        import pytest
        from pydantic import ValidationError

        from vibecraft.directives.scope import Selector

        with pytest.raises(ValidationError):
            Selector(health_below_pct=101.0)

    def test_health_below_pct_negative_validation_error(self) -> None:
        """health_below_pct 越界（<0）→ pydantic ValidationError。"""
        import pytest
        from pydantic import ValidationError

        from vibecraft.directives.scope import Selector

        with pytest.raises(ValidationError):
            Selector(health_below_pct=-1.0)

    def test_shield_below_pct_validation_error(self) -> None:
        """shield_below_pct 越界（>100）→ pydantic ValidationError。"""
        import pytest
        from pydantic import ValidationError

        from vibecraft.directives.scope import Selector

        with pytest.raises(ValidationError):
            Selector(shield_below_pct=200.0)


# =========================================================================
# WP-C 撤销恢复栈（displacement recovery）
# =========================================================================


def _make_d_old(unit_type: str = "Stalker") -> Directive:
    """构造 persistent unit_claim，用作 D_old（守瞭望塔）。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type=unit_type),
        task=Task(
            primary_action=Action(
                verb=Verb.GUARD_POSITION,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_1"),
            )
        ),
        persistent=True,
    )
    return Directive(payload=payload, issued_at=5.0)


def _make_d_new(unit_type: str = "Stalker") -> Directive:
    """构造 ephemeral unit_claim，用作 D_new（抢占 tag）。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type=unit_type),
        task=Task(
            primary_action=Action(
                verb=Verb.ATTACK_MOVE,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
            )
        ),
        persistent=False,
    )
    return Directive(payload=payload, issued_at=10.0)


def _make_displacement_director(session: GameSession) -> Director:
    """构造带 StrategyLibrary 的 Director，供 displacement 测试用。"""
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session)


class TestDisplacementRecovery:
    """WP-C 单位级 displacement 恢复栈。

    覆盖 case 1-6, 9(不做), 10 + 无抢占基线。
    """

    def test_case1_release_dNew_restores_tag_to_dOld(self, session: GameSession) -> None:
        """case 1: D_old claim tag → D_new claim 同 tag → release(D_new) → tag 回 D_old + 重发 action。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 1001
        facade.selector_stub["Stalker"] = [TAG]

        # D_old: persistent unit_claim (守瞭望塔)
        d_old = _make_d_old("Stalker")
        director._submit_directives([d_old], now=5.0)
        # D_old 应在 standing_orders + _standing_order_tags
        assert d_old.id in director._standing_order_tags
        assert TAG in director._standing_order_tags[d_old.id]

        # D_new: ephemeral unit_claim (抢 TAG)
        d_new = _make_d_new("Stalker")
        # 清掉旧 action 记录，方便后面只看恢复后的
        facade.unit_actions.clear()
        director._submit_directives([d_new], now=10.0)
        # on_tick 触发 _claim_directive_units（ephemeral 在 committed 后执行）
        director.on_tick(now=11.0)

        # D_new 应该拥有 TAG；D_old 的集合里 TAG 被移除
        assert d_new.id in director._standing_order_tags
        assert TAG in director._standing_order_tags[d_new.id]
        assert TAG not in director._standing_order_tags.get(d_old.id, set())
        # _displaced 记录了 prior = d_old.id
        assert director._displaced[d_new.id][TAG] == d_old.id

        # release D_new → tag 应该恢复给 D_old
        facade.unit_actions.clear()
        director._release_standing_order_units(d_new.id)

        # TAG 回到 D_old 集合
        assert TAG in director._standing_order_tags.get(d_old.id, set())
        # 重发了 D_old 的 primary_action (guard_position)
        reissued = [a for a in facade.unit_actions if a["tag"] == TAG]
        assert len(reissued) >= 1
        assert reissued[0]["verb"] == "guard_position"
        # 没有调 release_unit_role（不交 bot）
        release_calls = [
            c for c in facade.calls if c.method == "release_unit_role" and c.args[0] == TAG
        ]
        assert len(release_calls) == 0

    def test_case2_no_prior_owner_goes_back_to_bot(self, session: GameSession) -> None:
        """case 2: D_new 抢无主 tag → release(D_new) → facade.release_unit_role(tag) 调。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 2001
        facade.selector_stub["Stalker"] = [TAG]

        d_new = _make_d_new("Stalker")
        director._submit_directives([d_new], now=10.0)
        director.on_tick(now=11.0)

        # TAG 无主 → prior=None
        assert director._displaced[d_new.id][TAG] is None

        facade.calls.clear()
        director._release_standing_order_units(d_new.id)

        release_calls = [
            c for c in facade.calls if c.method == "release_unit_role" and c.args[0] == TAG
        ]
        assert len(release_calls) == 1

    def test_case3_prior_already_released_goes_back_to_bot(self, session: GameSession) -> None:
        """case 3: D_old → D_new 抢同 tag → 先 release(D_old) → 再 release(D_new) → 交 bot。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 3001
        facade.selector_stub["Stalker"] = [TAG]

        d_old = _make_d_old("Stalker")
        director._submit_directives([d_old], now=5.0)

        d_new = _make_d_new("Stalker")
        director._submit_directives([d_new], now=10.0)
        director.on_tick(now=11.0)

        # 先释放 D_old（prior 结束）
        director._release_standing_order_units(d_old.id)
        assert d_old.id not in director._standing_order_tags

        # 再释放 D_new → prior 已结束，交 bot
        facade.calls.clear()
        director._release_standing_order_units(d_new.id)

        release_calls = [
            c for c in facade.calls if c.method == "release_unit_role" and c.args[0] == TAG
        ]
        assert len(release_calls) == 1

    def test_case4_dead_unit_silent_skip(self, session: GameSession) -> None:
        """case 4: 单位已死 → release 时不崩、不重发、不交 bot。"""
        from types import SimpleNamespace

        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 4001
        facade.selector_stub["Stalker"] = [TAG]

        d_old = _make_d_old("Stalker")
        director._submit_directives([d_old], now=5.0)

        d_new = _make_d_new("Stalker")
        director._submit_directives([d_new], now=10.0)
        director.on_tick(now=11.0)

        # 注入 fake bot，by_tag 返回 None（单位已死）
        director._bot = SimpleNamespace(units=SimpleNamespace(by_tag=lambda t: None))

        facade.unit_actions.clear()
        facade.calls.clear()
        # 不应崩溃
        director._release_standing_order_units(d_new.id)

        # 不重发 action
        assert len(facade.unit_actions) == 0
        # 不交 bot（release_unit_role 没调 TAG）
        release_calls = [
            c for c in facade.calls if c.method == "release_unit_role" and c.args[0] == TAG
        ]
        assert len(release_calls) == 0

    def test_case5_lifo_three_layers(self, session: GameSession) -> None:
        """case 5 LIFO: A→B→C 三层抢同 tag → release(C) 恢复 B；release(B) 恢复 A。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 5001

        # 先手动建 A 的 standing_order_tags（用不同 unit_type 避免 selector_stub 干扰）
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        def _make_so(verb: Verb, named_spot: str) -> Directive:
            payload = UnitClaimPayload(
                selector=Selector(unit_type="Phoenix"),
                task=Task(
                    primary_action=Action(
                        verb=verb,
                        target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot),
                    )
                ),
                persistent=True,
            )
            return Directive(payload=payload, issued_at=1.0)

        d_a = _make_so(Verb.GUARD_POSITION, "watchtower_1")
        d_b = _make_so(Verb.PATROL, "enemy_natural")
        d_c = _make_so(Verb.ATTACK_MOVE, "enemy_main")

        # 手动按 claim 顺序写入（绕过 selector_stub 解析，直接模拟 claim 场景）
        # A 先 claim TAG
        director._standing_order_tags[d_a.id] = {TAG}
        director._displaced[d_a.id] = {TAG: None}
        director.standing_orders.append(d_a)

        # B claim TAG（抢 A）
        prior_a = director._current_owner_of(TAG, exclude_id=d_b.id)
        assert prior_a == d_a.id
        director._standing_order_tags[d_a.id].discard(TAG)
        director._standing_order_tags[d_b.id] = {TAG}
        director._displaced[d_b.id] = {TAG: d_a.id}
        director.standing_orders.append(d_b)

        # C claim TAG（抢 B）
        prior_b = director._current_owner_of(TAG, exclude_id=d_c.id)
        assert prior_b == d_b.id
        director._standing_order_tags[d_b.id].discard(TAG)
        director._standing_order_tags[d_c.id] = {TAG}
        director._displaced[d_c.id] = {TAG: d_b.id}
        director.standing_orders.append(d_c)

        # release C → 恢复 B
        facade.unit_actions.clear()
        director._release_standing_order_units(d_c.id)

        assert TAG in director._standing_order_tags.get(d_b.id, set())
        assert d_c.id not in director._standing_order_tags
        reissued_b = [a for a in facade.unit_actions if a["tag"] == TAG]
        assert len(reissued_b) >= 1
        assert reissued_b[0]["verb"] == "patrol"

        # release B → 恢复 A
        facade.unit_actions.clear()
        director._release_standing_order_units(d_b.id)

        assert TAG in director._standing_order_tags.get(d_a.id, set())
        assert d_b.id not in director._standing_order_tags
        reissued_a = [a for a in facade.unit_actions if a["tag"] == TAG]
        assert len(reissued_a) >= 1
        assert reissued_a[0]["verb"] == "guard_position"

    def test_case6_per_tag_mixed_owners(self, session: GameSession) -> None:
        """case 6: D_new 抢 3 tag（1 归 D_old、2 无主）→ release(D_new) → 1 回 D_old、2 交 bot。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG_OLD = 6001
        TAG_FREE1 = 6002
        TAG_FREE2 = 6003

        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        # D_old 持有 TAG_OLD
        d_old_payload = UnitClaimPayload(
            selector=Selector(unit_type="Stalker"),
            task=Task(
                primary_action=Action(
                    verb=Verb.GUARD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_1"),
                )
            ),
            persistent=True,
        )
        d_old = Directive(payload=d_old_payload, issued_at=5.0)
        director._standing_order_tags[d_old.id] = {TAG_OLD}
        director._displaced[d_old.id] = {TAG_OLD: None}
        director.standing_orders.append(d_old)

        # D_new 直接手动注入（避免 selector_stub 解析）：抢 3 个 tag
        d_new_payload = UnitClaimPayload(
            selector=Selector(unit_type="Phoenix"),
            task=Task(
                primary_action=Action(
                    verb=Verb.ATTACK_MOVE,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
                )
            ),
            persistent=False,
        )
        d_new = Directive(payload=d_new_payload, issued_at=10.0)
        # 手动模拟三个 tag 的 displacement：TAG_OLD 来自 d_old，其余无主
        director._standing_order_tags[d_old.id].discard(TAG_OLD)
        director._standing_order_tags[d_new.id] = {TAG_OLD, TAG_FREE1, TAG_FREE2}
        director._displaced[d_new.id] = {
            TAG_OLD: d_old.id,
            TAG_FREE1: None,
            TAG_FREE2: None,
        }

        facade.calls.clear()
        facade.unit_actions.clear()
        director._release_standing_order_units(d_new.id)

        # TAG_OLD 恢复给 D_old
        assert TAG_OLD in director._standing_order_tags.get(d_old.id, set())
        # TAG_FREE1, TAG_FREE2 交 bot
        freed = {c.args[0] for c in facade.calls if c.method == "release_unit_role"}
        assert TAG_FREE1 in freed
        assert TAG_FREE2 in freed
        assert TAG_OLD not in freed

    def test_case10_ephemeral_move_displacement_recorded(self, session: GameSession) -> None:
        """case 10: 一次性 MOVE directive 也走 displacement 记录逻辑（_claim_directive_units 路径）。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 10001
        facade.selector_stub["Stalker"] = [TAG]

        # 先手动注入一个现有 standing order 持有 TAG
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        d_existing_payload = UnitClaimPayload(
            selector=Selector(unit_type="Stalker"),
            task=Task(
                primary_action=Action(
                    verb=Verb.GUARD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_1"),
                )
            ),
            persistent=True,
        )
        d_existing = Directive(payload=d_existing_payload, issued_at=3.0)
        director._standing_order_tags[d_existing.id] = {TAG}
        director._displaced[d_existing.id] = {TAG: None}
        director.standing_orders.append(d_existing)

        # 提交一次性 MOVE（走 _claim_directive_units）
        from vibecraft.directives.models import MovePayload

        move_payload = MovePayload(
            selector=Selector(unit_type="Stalker"),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
        )
        d_move = Directive(payload=move_payload, issued_at=10.0)
        director._submit_directives([d_move], now=10.0)
        director.on_tick(now=11.0)

        # _displaced 应记录 d_move → {TAG: d_existing.id}
        assert d_move.id in director._displaced
        assert director._displaced[d_move.id].get(TAG) == d_existing.id
        # TAG 从 d_existing 的集合移除
        assert TAG not in director._standing_order_tags.get(d_existing.id, set())

    def test_case10_persistent_unit_claim_displacement_recorded(self, session: GameSession) -> None:
        """case 10: persistent unit_claim 也走 displacement 记录（_assign_standing_order_units 路径）。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG = 10002
        facade.selector_stub["Phoenix"] = [TAG]

        # 先放一个现有 standing order 持有 TAG
        from vibecraft.directives.models import UnitClaimPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
        from vibecraft.directives.task import Action, Task, Verb

        d_first_payload = UnitClaimPayload(
            selector=Selector(unit_type="Phoenix"),
            task=Task(
                primary_action=Action(
                    verb=Verb.PATROL,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="watchtower_1"),
                )
            ),
            persistent=True,
        )
        d_first = Directive(payload=d_first_payload, issued_at=3.0)
        director._standing_order_tags[d_first.id] = {TAG}
        director._displaced[d_first.id] = {TAG: None}
        director.standing_orders.append(d_first)

        # 提交第二个 persistent unit_claim（会抢 TAG）
        d_second = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d_second], now=10.0)

        # _assign_standing_order_units 立即执行（persistent 路径不走 on_tick）
        assert d_second.id in director._displaced
        assert director._displaced[d_second.id].get(TAG) == d_first.id
        # TAG 从 d_first 的集合移除
        assert TAG not in director._standing_order_tags.get(d_first.id, set())

    def test_no_displacement_baseline(self, session: GameSession) -> None:
        """无抢占基线: claim 一批无主单位 → _displaced 全 None → release 全交回 bot。"""
        director = _make_displacement_director(session)
        facade = director.facade
        assert isinstance(facade, FakeFacade)

        TAG1, TAG2 = 9001, 9002
        facade.selector_stub["Stalker"] = [TAG1, TAG2]

        d = _make_d_new("Stalker")
        director._submit_directives([d], now=10.0)
        director.on_tick(now=11.0)

        # _displaced 全 None（无主）
        assert d.id in director._displaced
        for tag in [TAG1, TAG2]:
            assert director._displaced[d.id].get(tag) is None

        facade.calls.clear()
        director._release_standing_order_units(d.id)

        # 全交 bot
        freed = {c.args[0] for c in facade.calls if c.method == "release_unit_role"}
        assert TAG1 in freed
        assert TAG2 in freed


# =========================================================================
# WP-E: bot 关键动作自评
# =========================================================================


class TestBotSelfEval:
    """WP-E bot 自评：丢分矿 / 大波损兵 → _bot_self_eval 正确设置，限频，快照 TTL。"""

    from typing import Any

    def _make_fake_bot(self, bases: int, army: int) -> Any:
        """构造轻量 fake bot：带 townhalls.amount 和 supply_army。"""
        from types import SimpleNamespace

        townhalls = SimpleNamespace(amount=bases)
        return SimpleNamespace(townhalls=townhalls, supply_army=army)

    def _director(self, session: GameSession) -> Director:
        return _make_director(
            StrategyLibrary.from_directories(
                strategies_dir=PROJECT_ROOT / "strategies",
                aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
            ),
            session,
            FakeFacade(),
            {},
        )

    def test_first_call_stores_prev_no_eval(self, session: GameSession) -> None:
        """首次调用只存 prev，不产生自评。"""
        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)
        assert director._bot_self_eval is None
        assert director._self_eval_prev_bases == 3
        assert director._self_eval_prev_army == 20

    def test_lost_base_triggers_eval(self, session: GameSession) -> None:
        """bases 3→2 → _bot_self_eval kind=lost_base，text 含"分矿"。"""
        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)  # 首次存 prev

        director._bot = self._make_fake_bot(bases=2, army=20)
        director._maybe_self_eval(now=35.0)  # 超过 cooldown

        assert director._bot_self_eval is not None
        assert director._bot_self_eval["kind"] == "lost_base"
        assert "分矿" in director._bot_self_eval["text"]
        assert director._last_self_eval_t == 35.0

    def test_lost_army_triggers_eval_with_count(self, session: GameSession) -> None:
        """army 20→12（掉 8 >= 6）→ kind=lost_army，text 含"8"。"""
        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)  # 首次存 prev

        director._bot = self._make_fake_bot(bases=3, army=12)
        director._maybe_self_eval(now=35.0)

        assert director._bot_self_eval is not None
        assert director._bot_self_eval["kind"] == "lost_army"
        assert "8" in director._bot_self_eval["text"]

    def test_small_army_drop_no_eval(self, session: GameSession) -> None:
        """army 掉 5（<6）→ 不发自评。"""
        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)

        director._bot = self._make_fake_bot(bases=3, army=15)
        director._maybe_self_eval(now=35.0)

        assert director._bot_self_eval is None

    def test_cooldown_prevents_second_eval(self, session: GameSession) -> None:
        """cooldown 内第二次不覆盖旧自评。"""
        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)  # 首次 prev

        # 第一条自评（丢分矿）
        director._bot = self._make_fake_bot(bases=2, army=20)
        director._maybe_self_eval(now=35.0)
        first_eval = director._bot_self_eval

        # cooldown 内再次丢分矿 → 不覆盖
        director._bot = self._make_fake_bot(bases=1, army=20)
        director._maybe_self_eval(now=40.0)  # 40-35=5 < 25s cooldown
        assert director._bot_self_eval is first_eval  # 同一对象

    def test_player_attacking_suppresses_army_eval(self, session: GameSession) -> None:
        """玩家全军进攻时损兵不评（_is_player_attacking=True）。"""

        from vibecraft.directives.models import Directive, TacticalObjectivePayload

        director = self._director(session)
        director._bot = self._make_fake_bot(bases=3, army=20)
        director._maybe_self_eval(now=10.0)  # 首次 prev

        # 模拟玩家全军进攻 directive
        payload = TacticalObjectivePayload(verb="attack", target_area=None)  # type: ignore[arg-type]
        attack_d = Directive(payload=payload, issued_at=10.0)
        director._current_l2_global_directive = attack_d

        director._bot = self._make_fake_bot(bases=3, army=12)
        director._maybe_self_eval(now=35.0)

        assert director._bot_self_eval is None

    def test_snapshot_bot_self_eval_within_ttl(self, session: GameSession) -> None:
        """_bot_self_eval ts 在 TTL 内 → snapshot 带 bot_self_eval。"""
        director = self._director(session)
        director._bot_self_eval = {"text": "丢了个分矿，没守住", "kind": "lost_base", "ts": 30.0}
        snap = director.build_snapshot(now=35.0)  # 35-30=5 < 8s TTL
        assert snap["bot_self_eval"] is not None
        assert snap["bot_self_eval"]["kind"] == "lost_base"

    def test_snapshot_bot_self_eval_expired_is_none(self, session: GameSession) -> None:
        """_bot_self_eval ts 超过 TTL → snapshot 发 null。"""
        director = self._director(session)
        director._bot_self_eval = {"text": "丢了个分矿，没守住", "kind": "lost_base", "ts": 10.0}
        snap = director.build_snapshot(now=25.0)  # 25-10=15 > 8s TTL
        assert snap["bot_self_eval"] is None

    def test_no_eval_when_bot_is_none(self, session: GameSession) -> None:
        """_bot 为 None 时 _maybe_self_eval 静默 return，不崩。"""
        director = self._director(session)
        director._bot = None
        director._maybe_self_eval(now=10.0)
        assert director._bot_self_eval is None
        assert director._self_eval_prev_bases is None


# =============================================================================
# WP-D 实时运营策略层 — apply_macro_action（双维度）
# =============================================================================


class TestMacroAction:
    """WP-D macro action 双维度全覆盖：
    expand(1-5/max/clear) / workers(stop/max/default) / 切换撤旧 / 满采 tick / snapshot。
    """

    @staticmethod
    def _director(session: GameSession) -> Director:
        from unittest.mock import MagicMock

        director = Director(
            facade=FakeFacade(),
            parser=None,  # type: ignore[arg-type]
            session=session,
        )
        # 注入假 bot
        bot = MagicMock()
        bot.townhalls.ready = []  # ideal_harvesters sum → 0 by default
        bot.gas_buildings.ready = []
        bot.supply_workers = 0
        director._bot = bot
        return director

    # ------------------------------------------------------------------
    # 维度1：扩张矿数
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 维度2：农民生产
    # ------------------------------------------------------------------

    def test_workers_stop_submits_production_block(self, session: GameSession) -> None:
        """workers="stop" → ProductionBlockPayload(Probe)，_worker_mode="stop"，dir_id 有值。"""
        from vibecraft.directives.models import ProductionBlockPayload

        d = self._director(session)
        d.apply_macro_action("workers", "stop", now=10.0)

        assert d._worker_mode == "stop"
        assert d._worker_block_dir_id is not None
        did = d._worker_block_dir_id
        assert did in d._in_flight
        payload = d._in_flight[did].payload
        assert isinstance(payload, ProductionBlockPayload)
        assert payload.unit_type == "Probe"

    def test_workers_max_sets_mode_no_directive(self, session: GameSession) -> None:
        """workers="max" → _worker_mode="max"，不下 production_block directive。"""
        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)

        assert d._worker_mode == "max"
        assert d._worker_block_dir_id is None
        # production_overrides 不变（无新 directive 进去）
        assert len(d.production_overrides) == 0

    def test_workers_default_clears_mode(self, session: GameSession) -> None:
        """workers="stop" → workers="default"：撤 production_block，_worker_mode=None。"""
        d = self._director(session)
        d.apply_macro_action("workers", "stop", now=10.0)
        old_dir_id = d._worker_block_dir_id
        assert old_dir_id is not None

        d.apply_macro_action("workers", "default", now=20.0)

        assert d._worker_mode is None
        assert d._worker_block_dir_id is None
        # 旧 production_block 已撤（不在 _in_flight）
        assert old_dir_id not in d._in_flight

    def test_workers_max_to_default_stops_saturation(self, session: GameSession) -> None:
        """workers="max" → "default"：_worker_mode=None，_tick_worker_saturation 不再训练。"""
        from unittest.mock import MagicMock

        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)
        assert d._worker_mode == "max"

        d.apply_macro_action("workers", "default", now=20.0)
        assert d._worker_mode is None

        # 确认 tick 不再调 train
        d._bot.train = MagicMock()
        d._tick_worker_saturation()
        d._bot.train.assert_not_called()

    # ------------------------------------------------------------------
    # 满采 tick 逻辑
    # ------------------------------------------------------------------

    def test_tick_worker_saturation_trains_when_need(self, session: GameSession) -> None:
        """_worker_mode=="max"，supply_workers < cap → train(PROBE, amount=need)。"""
        from unittest.mock import MagicMock, patch

        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)

        # 伪造 bot：2 个主基地各 ideal=8，1 个气矿 ideal=3 → cap=19；supply_workers=12 → need=7
        th1 = MagicMock()
        th1.ideal_harvesters = 8
        th2 = MagicMock()
        th2.ideal_harvesters = 8
        gas1 = MagicMock()
        gas1.ideal_harvesters = 3
        d._bot.townhalls.ready = [th1, th2]
        d._bot.gas_buildings.ready = [gas1]
        d._bot.supply_workers = 12
        d._bot.train = MagicMock()

        # UnitTypeId 是在 _tick_worker_saturation 内部 local import；
        # 打 sc2.ids.unit_typeid.UnitTypeId 才能覆盖到它。
        with patch("sc2.ids.unit_typeid.UnitTypeId") as mock_uid:
            mock_uid.PROBE = "PROBE_MOCK"
            d._tick_worker_saturation()

        # 验证 train 被调用且 amount=7（cap=19, cur=12）
        d._bot.train.assert_called_once()
        call_kwargs = d._bot.train.call_args
        assert call_kwargs.kwargs.get("amount") == 7
        assert call_kwargs.kwargs.get("train_only_idle_buildings") is False

    def test_tick_worker_saturation_noop_when_full(self, session: GameSession) -> None:
        """supply_workers >= cap → 不调 train。"""
        from unittest.mock import MagicMock

        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)

        th1 = MagicMock()
        th1.ideal_harvesters = 8
        d._bot.townhalls.ready = [th1]
        d._bot.gas_buildings.ready = []
        d._bot.supply_workers = 10  # >= cap=8
        d._bot.train = MagicMock()

        d._tick_worker_saturation()  # UnitTypeId 能 import 到但 need<=0，不 train

        d._bot.train.assert_not_called()

    def test_tick_worker_saturation_noop_when_mode_not_max(self, session: GameSession) -> None:
        """_worker_mode != 'max' → tick 什么都不做。"""
        from unittest.mock import MagicMock

        d = self._director(session)
        d._bot.train = MagicMock()

        d._tick_worker_saturation()  # mode=None, no call
        d._bot.train.assert_not_called()

    def test_tick_worker_saturation_account_separation(self, session: GameSession) -> None:
        """WP4 账目分离：有 stealth townhall + stealth 农民时，cap/cur/need 正确排除 stealth。

        具体数值：
          主矿 townhall(tag=1) ideal=32；stealth townhall(tag=99999) ideal=16
          stealth 农民数=10；supply_workers=40（含 stealth 农民）
          期望：cap=32（不含 stealth townhall）, cur=40-10=30, need=2
          不应是 (32+16)-40=8（含 stealth townhall 的错误计算）
        """
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.stealth.cell import StealthCell, StealthState

        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)

        # 主矿 townhall（不是 stealth）
        main_th = MagicMock()
        main_th.tag = 1
        main_th.ideal_harvesters = 32

        # stealth townhall
        stealth_nexus_tag = 99999
        stealth_th = MagicMock()
        stealth_th.tag = stealth_nexus_tag
        stealth_th.ideal_harvesters = 16

        d._bot.townhalls.ready = [main_th, stealth_th]
        d._bot.gas_buildings.ready = []
        d._bot.supply_workers = 40
        d._bot.train = MagicMock()

        # 注入 stealth cell：nexus_tag=99999，10 个 stealth 农民
        stealth_workers = {101, 102, 103, 104, 105, 106, 107, 108, 109, 110}
        cell = StealthCell(
            cell_id=1,
            point=(100.0, 100.0),
            state=StealthState.MINING,
            nexus_tag=stealth_nexus_tag,
            worker_tags=stealth_workers,
        )
        d._stealth_manager.cells[1] = cell

        with patch("sc2.ids.unit_typeid.UnitTypeId") as mock_uid:
            mock_uid.PROBE = "PROBE_MOCK"
            d._tick_worker_saturation()

        # cap=32（stealth ideal 16 被排除）；cur=40-10=30；need=2
        d._bot.train.assert_called_once()
        call_kwargs = d._bot.train.call_args
        assert call_kwargs.kwargs.get("amount") == 2, (
            f"期望 need=2（主矿 cap=32，cur=30），实际 amount={call_kwargs.kwargs.get('amount')}"
        )

    def test_tick_worker_saturation_no_stealth_unchanged(self, session: GameSession) -> None:
        """无 stealth cell 时，账目分离不影响现有行为（与原来等价）。"""
        from unittest.mock import MagicMock, patch

        d = self._director(session)
        d.apply_macro_action("workers", "max", now=10.0)

        th1 = MagicMock()
        th1.tag = 1
        th1.ideal_harvesters = 8
        th2 = MagicMock()
        th2.tag = 2
        th2.ideal_harvesters = 8
        gas1 = MagicMock()
        gas1.ideal_harvesters = 3
        d._bot.townhalls.ready = [th1, th2]
        d._bot.gas_buildings.ready = [gas1]
        d._bot.supply_workers = 12
        d._bot.train = MagicMock()

        # stealth_manager 为空（无 stealth cell）
        assert d._stealth_manager.stealth_townhall_tags == set()
        assert d._stealth_manager.stealth_worker_tags == set()

        with patch("sc2.ids.unit_typeid.UnitTypeId") as mock_uid:
            mock_uid.PROBE = "PROBE_MOCK"
            d._tick_worker_saturation()

        d._bot.train.assert_called_once()
        call_kwargs = d._bot.train.call_args
        assert call_kwargs.kwargs.get("amount") == 7, (
            f"无 stealth 时 cap=19，cur=12，need=7，实际={call_kwargs.kwargs.get('amount')}"
        )

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------

    def test_snapshot_contains_worker_mode_field(self, session: GameSession) -> None:
        """build_snapshot 包含 worker_mode 字段。

        2026-07-27:`macro_expand_target` 随开矿封顶(expand=N)一起下架 —— 面板的开矿维度
        只剩「多开一个矿」,它是 fire-and-forget、不留封顶状态,所以快照里不再有这个字段。
        """
        d = self._director(session)
        snap0 = d.build_snapshot(now=5.0)
        assert "macro_expand_target" not in snap0
        assert "worker_mode" in snap0
        assert snap0["worker_mode"] is None

        d.apply_macro_action("workers", "stop", now=10.0)
        snap1 = d.build_snapshot(now=10.0)
        assert snap1["worker_mode"] == "stop"

        d.apply_macro_action("workers", "max", now=20.0)
        snap2 = d.build_snapshot(now=20.0)
        assert snap2["worker_mode"] == "max"

    # ------------------------------------------------------------------
    # 新增 — mining 维度 + expand one_more
    # ------------------------------------------------------------------

    def test_mining_mineral_sets_state_and_calls_facade(self, session: GameSession) -> None:
        """mining=mineral → _mining_priority="mineral" + facade.set_mining_priority("mineral")。"""
        d = self._director(session)
        d.apply_macro_action("mining", "mineral", now=10.0)

        assert d._mining_priority == "mineral"
        assert "mineral" in d.facade.mining_priority_calls, (
            f"facade.set_mining_priority('mineral') 未被调用，calls={d.facade.mining_priority_calls}"
        )

    def test_mining_gas_sets_state_and_calls_facade(self, session: GameSession) -> None:
        """mining=gas → _mining_priority="gas" + facade.set_mining_priority("gas")。"""
        d = self._director(session)
        d.apply_macro_action("mining", "gas", now=10.0)

        assert d._mining_priority == "gas"
        assert "gas" in d.facade.mining_priority_calls

    def test_mining_default_clears_state_and_calls_facade_none(self, session: GameSession) -> None:
        """mining=default → _mining_priority=None + facade.set_mining_priority(None)。"""
        d = self._director(session)
        d.apply_macro_action("mining", "mineral", now=10.0)
        d.apply_macro_action("mining", "default", now=20.0)

        assert d._mining_priority is None
        assert None in d.facade.mining_priority_calls, (
            f"mining=default 应调 facade.set_mining_priority(None)，calls={d.facade.mining_priority_calls}"
        )

    def test_mining_does_not_submit_directive_card(self, session: GameSession) -> None:
        """mining 维度是持续状态，不应下 directive 卡（不进 board，不产生 production_overrides）。"""
        d = self._director(session)
        before_overrides = len(d.facade.production_overrides)
        d.apply_macro_action("mining", "mineral", now=10.0)
        assert len(d.facade.production_overrides) == before_overrides, (
            "mining=mineral 不应发 production_override directive 卡"
        )

    def test_snapshot_contains_mining_priority_field(self, session: GameSession) -> None:
        """build_snapshot 包含 mining_priority 字段，跟随 _mining_priority 状态变化。"""
        d = self._director(session)
        snap0 = d.build_snapshot(now=5.0)
        assert "mining_priority" in snap0, "snapshot 应包含 mining_priority 字段"
        assert snap0["mining_priority"] is None

        d.apply_macro_action("mining", "gas", now=10.0)
        snap1 = d.build_snapshot(now=10.0)
        assert snap1["mining_priority"] == "gas"

        d.apply_macro_action("mining", "default", now=20.0)
        snap2 = d.build_snapshot(now=20.0)
        assert snap2["mining_priority"] is None

    def test_expand_one_more_submits_directive_no_facade_override(
        self, session: GameSession
    ) -> None:
        """expand=one_more → 提交 expansion_override 卡（current+1），不调 facade.set_expansion_override（封顶 API）。"""
        from unittest.mock import MagicMock

        from vibecraft.directives.models import ExpansionOverridePayload

        d = self._director(session)
        # 注入有 2 个 ready townhalls 的 bot，already_pending 返回 0
        # (NEXUS import 失败时也会 fallback pending=0，这里让 mock 覆盖直接走的路径)
        bot = MagicMock()
        bot.townhalls.ready = [MagicMock(), MagicMock()]  # len=2
        bot.already_pending.return_value = 0
        d._bot = bot

        before_overrides = len(d.facade.expansion_overrides)

        d.apply_macro_action("expand", "one_more", now=10.0)

        # 不调 facade.set_expansion_override（那是封顶）
        assert len(d.facade.expansion_overrides) == before_overrides, (
            "one_more 不应调 facade.set_expansion_override（避免冻死运营扩张）"
        )

        # 应提交了一张 ExpansionOverridePayload 卡，进 production_overrides
        expansion_cards = [
            c
            for c in d.production_overrides
            if isinstance(getattr(c, "payload", None), ExpansionOverridePayload)
        ]
        assert expansion_cards, (
            "one_more 应提交 ExpansionOverridePayload directive 卡（进 production_overrides）"
        )
        # current = 2 ready + 0 pending → target = 3（UnitTypeId 导入失败时 pending fallback=0 同样 target=3）
        assert expansion_cards[-1].payload.target_count >= 2, (
            f"target_count 应 ≥ 2（至少 current+1=2+0+1=3 或 fallback 1+0+1=2），"
            f"实际={expansion_cards[-1].payload.target_count}"
        )


class TestRallyPoint:
    """出兵集结点（RALLY_POINT，2026-06-07 用户）：设全局集结点,新兵自动 rally。

    覆盖:apply → _rally_point + facade.set_rally_point;on_tick 每帧续设(sharpy 一次性
    flag);revoke 清 + set_rally_point(None);新点覆盖旧卡;camera 注入;命令卡片显示。
    """

    def _mk_rally(self, point=(50.0, 60.0)):
        from vibecraft.directives.models import Directive, RallyPointPayload
        from vibecraft.directives.scope import TargetKind, TargetSpec
        from vibecraft.directives.types import IssuedBy

        return Directive(
            payload=RallyPointPayload(target=TargetSpec(kind=TargetKind.POINT, point=point)),
            issued_at=10.0,
            issued_by=IssuedBy.VOICE,
        )

    def _setup(self, library, session):
        from unittest.mock import MagicMock

        from vibecraft.directives.board import DirectiveBoard

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._bot = MagicMock(time=10.0)
        director.board = DirectiveBoard(commit_delay_s=0.0)
        return director, facade

    def test_set_and_per_frame_tick(self, library: StrategyLibrary, session: GameSession) -> None:
        director, facade = self._setup(library, session)
        d = self._mk_rally((50.0, 60.0))
        director._submit_directives([d], now=10.0)
        director.on_tick(now=10.5)  # commit → _apply_to_facade
        assert director._rally_point == (50.0, 60.0)
        assert director._rally_point_id == d.id
        assert (50.0, 60.0) in facade.rally_points
        # 每帧续设(sharpy set_gather_point 一次性 flag)
        n = len(facade.rally_points)
        director.on_tick(now=11.0)
        assert len(facade.rally_points) > n

    def test_revoke_clears_and_resets_default(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        director, facade = self._setup(library, session)
        d = self._mk_rally()
        director._submit_directives([d], now=10.0)
        director.on_tick(now=10.5)
        assert director._rally_point is not None
        ok = director.revoke_directive(d.id, now=12.0)
        assert ok
        assert director._rally_point is None
        assert director._rally_point_id is None
        assert facade.rally_points[-1] is None  # set_rally_point(None) 恢复默认
        # 撤销后 on_tick 不再续设
        n = len(facade.rally_points)
        director.on_tick(now=13.0)
        assert len(facade.rally_points) == n

    def test_new_rally_supersedes_old(self, library: StrategyLibrary, session: GameSession) -> None:
        director, _ = self._setup(library, session)
        d1 = self._mk_rally((10.0, 10.0))
        director._submit_directives([d1], now=10.0)
        director.on_tick(now=10.5)
        first_id = director._rally_point_id
        d2 = self._mk_rally((90.0, 90.0))
        director._submit_directives([d2], now=11.0)
        director.on_tick(now=11.5)
        assert director._rally_point == (90.0, 90.0)
        assert director._rally_point_id == d2.id
        # 旧卡被标 done(被覆盖)
        assert director._override_status.get(first_id, {}).get("status") == "done"

    def test_camera_target_injected(self, library: StrategyLibrary, session: GameSession) -> None:
        from vibecraft.directives.models import Directive, RallyPointPayload
        from vibecraft.directives.scope import TargetKind, TargetSpec

        director, _ = self._setup(library, session)
        d = Directive(
            payload=RallyPointPayload(target=TargetSpec(kind=TargetKind.CAMERA)),
            issued_at=10.0,
        )
        director._inject_camera_point([d], camera_point=(42.0, 84.0))
        assert d.payload.target.point == (42.0, 84.0)

    def test_command_card_shows_rally(self, library: StrategyLibrary, session: GameSession) -> None:
        director, _ = self._setup(library, session)
        d = self._mk_rally((50.0, 60.0))
        director._submit_directives([d], now=10.0)
        director.on_tick(now=10.5)
        cards = director._build_command_cards(now=10.5)
        rally_cards = [c for c in cards if c["type"] == "rally_point"]
        assert len(rally_cards) == 1
        assert "集结点" in rally_cards[0]["display"]
        assert rally_cards[0]["revokable"] is True


class TestReleaseCancelsControllingDirectives:
    """规则3(2026-06-08 用户):释放/解散一批单位 → 连带撤销控制它们的 directive。"""

    def _mk_claim(self, unit_type: str):
        from vibecraft.directives.models import Directive

        return Directive.model_validate(
            {
                "payload": {
                    "type": "unit_claim",
                    "selector": {"unit_type": unit_type},
                    "task": {
                        "primary_action": {
                            "verb": "standby",
                            "target": {"kind": "named_spot", "named_spot": "main"},
                        }
                    },
                    "persistent": True,
                },
                "issued_at": 5.0,
            }
        )

    def test_cancel_only_directives_controlling_those_units(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from unittest.mock import MagicMock

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._bot = MagicMock(time=10.0)
        d1 = self._mk_claim("VoidRay")  # 控制虚空 501
        d2 = self._mk_claim("Zealot")  # 控制叉子 999
        director.standing_orders.extend([d1, d2])
        director._standing_order_tags[d1.id] = {501}
        director._standing_order_tags[d2.id] = {999}

        director._cancel_directives_controlling_units({501}, now=10.0)

        assert d1.id not in director._standing_order_tags  # 控制 501 的被撤
        assert director._standing_order_tags.get(d2.id) == {999}  # 不控制 501 的不动

    def test_unit_release_cancels_controlling_directive(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """释放虚空 → 之前控制虚空的撤退/待命指令被连带撤销(P6)。"""
        from unittest.mock import MagicMock

        from vibecraft.directives.models import UnitReleasePayload
        from vibecraft.directives.scope import Selector

        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [501]
        director = _make_director(library, session, facade, {})
        director._bot = MagicMock(time=10.0)
        old = self._mk_claim("VoidRay")
        director.standing_orders.append(old)
        director._standing_order_tags[old.id] = {501}

        director._apply_unit_release(
            UnitReleasePayload(selector=Selector(unit_type="VoidRay"), return_to_role="IDLE")
        )

        assert old.id not in director._standing_order_tags  # 旧指令被撤


class TestSortByPosition:
    """P2(2026-06-08):position=forward/back 按单位实际位置离敌远近排序选。"""

    def test_forward_picks_closest_to_enemy(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from vibecraft.directives.scope import Selector

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        enemy = SimpleNamespace(x=100.0, y=100.0)
        bot.enemy_start_locations = [enemy]

        def _mk(x):
            return SimpleNamespace(
                position=SimpleNamespace(distance_to=lambda e, _x=x: abs(_x - e.x))
            )

        units = {1: _mk(10.0), 2: _mk(90.0), 3: _mk(50.0)}  # 2 最靠敌(前线)
        bot.units.by_tag = lambda t: units.get(int(t))
        director._bot = bot

        fwd = director._sort_by_position([1, 2, 3], Selector(position="forward"))
        assert fwd[0] == 2  # 离敌最近=最前线
        back = director._sort_by_position([1, 2, 3], Selector(position="back"))
        assert back[0] == 1  # 离敌最远=最靠后
        # 不填 position → 原序不动
        assert director._sort_by_position([1, 2, 3], Selector()) == [1, 2, 3]


class TestStandbyTickTravelVsDefend:
    """P10(2026-06-08):standby 单位回家途中(超半径)只管走,不被沿途敌人勾住;到点才守位接敌。"""

    def _standby_dir(self, director, unit, point=(0.0, 0.0)):
        from vibecraft.directives.models import Directive

        d = Directive.model_validate(
            {
                "payload": {
                    "type": "unit_claim",
                    "selector": {"unit_type": "Carrier"},
                    "task": {
                        "primary_action": {
                            "verb": "standby",
                            "target": {"kind": "point", "point": list(point)},
                        }
                    },
                    "persistent": True,
                },
                "issued_at": 1.0,
            }
        )
        director.standing_orders.append(d)
        director._standing_order_tags[d.id] = {unit.tag}
        return d

    def _carrier(self, dist):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        return SimpleNamespace(
            tag=701,
            type_id=SimpleNamespace(name="CARRIER"),
            distance_to=lambda p: dist,
            move=MagicMock(),
            attack=MagicMock(),
        )

    def _setup(self, library, session, dist, enemies_near=True):
        from unittest.mock import MagicMock

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        carrier = self._carrier(dist)
        bot.units.tags_in = lambda tags: [carrier] if carrier.tag in tags else []
        ev = MagicMock()
        ev.__bool__ = lambda self: enemies_near
        ev.closest_to = lambda u: MagicMock()
        bot.enemy_units.closer_than = lambda r, u: ev
        director._bot = bot
        self._standby_dir(director, carrier)
        return director, carrier

    def test_traveling_home_moves_not_attacks(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        # 超半径(50>10)+ 有敌 → 只 move 回家,不 attack(P10 抽搐根因修复)
        director, carrier = self._setup(library, session, dist=50.0, enemies_near=True)
        director._tick_standby_orders()
        assert carrier.move.called
        assert not carrier.attack.called

    def test_at_point_engages_enemy(self, library: StrategyLibrary, session: GameSession) -> None:
        # 到点(5<=10)+ 有敌 → 守位接敌
        director, carrier = self._setup(library, session, dist=5.0, enemies_near=True)
        director._tick_standby_orders()
        assert carrier.attack.called
        assert not carrier.move.called


class TestScoutSemanticFallback:
    """P1(2026-06-08):primary_verb_prefix=scout 补上 bot ScoutWorker 真 tag(选对探路农民)。"""

    def test_scout_prefix_uses_scout_worker_tag(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from vibecraft.directives.scope import Selector

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        bot.scout_worker = SimpleNamespace(scout_tag=4339269633)
        bot.units = [SimpleNamespace(tag=4339269633)]  # 探路农民活着
        director._bot = bot
        director._unit_semantics = {}  # bot 自动探路,没 vibecraft 语意记录

        tags = director._resolve_by_semantics(Selector(primary_verb_prefix="scout", count=1))
        assert 4339269633 in tags  # 选到真探路农民(ScoutWorker tag)

    def test_no_scout_worker_no_crash(self, library: StrategyLibrary, session: GameSession) -> None:
        from unittest.mock import MagicMock

        from vibecraft.directives.scope import Selector

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        bot = MagicMock()
        bot.scout_worker = None
        bot.units = []
        director._bot = bot
        director._unit_semantics = {}
        # 没 scout_worker → 空,不崩
        assert director._resolve_by_semantics(Selector(primary_verb_prefix="scout")) == []


# =========================================================================
# townhall 落点 8-13 格模糊 → 弹确认(2026-06-09 用户)
# =========================================================================


class TestTownhallPlacementConfirm:
    """_maybe_build_townhall_confirm:建 townhall by_probe 落点离最近矿 8-13 格 → 弹二选一。

    ≤8 静默 snap / >13 静默原地(都返 None,走 facade);8-13 返 ClarificationRequest。
    """

    def _director(self, library: StrategyLibrary, session: GameSession) -> Director:
        return _make_director(
            library,
            session,
            FakeFacade(state=BotState(game_time=100.0)),
            {"interpretation_zh": "x", "confidence": 0.9, "directives": []},
        )

    def _bot_with_expansion(self, center: object) -> object:
        from unittest.mock import MagicMock

        bot = MagicMock(spec=[])
        knowledge = MagicMock(spec=[])
        zm = MagicMock(spec=[])
        zone = MagicMock(spec=[])
        zone.center_location = center
        zm.expansion_zones = [zone]
        knowledge.zone_manager = zm
        bot.knowledge = knowledge
        return bot

    def _build_at(
        self, point: tuple[float, float], confirmed: bool = False, stype: str = "Nexus"
    ) -> Directive:
        from vibecraft.directives.models import BuildAtPayload

        return Directive(
            payload=BuildAtPayload(
                structure_type=stype, point=point, by_probe=True, placement_confirmed=confirmed
            ),
            issued_at=100.0,
            source_text="在这开矿",
        )

    def test_ambiguous_distance_returns_clarification(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """点 (60,50) 离 expansion (50,50) = 10 格(8<10≤13) → 弹确认,两选项点不同。"""
        from sc2.position import Point2

        from vibecraft.directives.models import BuildAtPayload

        director = self._director(library, session)
        director._bot = self._bot_with_expansion(Point2((50.0, 50.0)))
        cr = director._maybe_build_townhall_confirm([self._build_at((60.0, 50.0))])
        assert cr is not None
        assert len(cr.options) == 2
        # 选项0 修正到矿区 → build_at point = expansion(50,50),且 placement_confirmed
        p0 = cr.options[0].directives[0].payload
        assert isinstance(p0, BuildAtPayload)
        assert p0.point == (50.0, 50.0)
        assert p0.placement_confirmed is True
        # 选项1 就在原地 → build_at point = 原点(60,50)
        p1 = cr.options[1].directives[0].payload
        assert p1.point == (60.0, 50.0)
        assert p1.placement_confirmed is True

    def test_close_point_no_confirm(self, library: StrategyLibrary, session: GameSession) -> None:
        """点 (54,50) 离 expansion (50,50) = 4 格(≤8) → None(静默 snap,走 facade)。"""
        from sc2.position import Point2

        director = self._director(library, session)
        director._bot = self._bot_with_expansion(Point2((50.0, 50.0)))
        assert director._maybe_build_townhall_confirm([self._build_at((54.0, 50.0))]) is None

    def test_far_point_no_confirm(self, library: StrategyLibrary, session: GameSession) -> None:
        """点 (66,50) 离 expansion (50,50) = 16 格(>13) → None(静默原地,挡路基地)。"""
        from sc2.position import Point2

        director = self._director(library, session)
        director._bot = self._bot_with_expansion(Point2((50.0, 50.0)))
        assert director._maybe_build_townhall_confirm([self._build_at((66.0, 50.0))]) is None

    def test_already_confirmed_no_reconfirm(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """placement_confirmed=True(玩家已选过)→ None,防二次拦截死循环。"""
        from sc2.position import Point2

        director = self._director(library, session)
        director._bot = self._bot_with_expansion(Point2((50.0, 50.0)))
        d = self._build_at((60.0, 50.0), confirmed=True)
        assert director._maybe_build_townhall_confirm([d]) is None

    def test_non_townhall_no_confirm(self, library: StrategyLibrary, session: GameSession) -> None:
        """非 townhall(Pylon)→ None,只对基地落点弹确认。"""
        from sc2.position import Point2

        director = self._director(library, session)
        director._bot = self._bot_with_expansion(Point2((50.0, 50.0)))
        d = self._build_at((60.0, 50.0), stype="Pylon")
        assert director._maybe_build_townhall_confirm([d]) is None

    def test_no_bot_no_confirm(self, library: StrategyLibrary, session: GameSession) -> None:
        """无 _bot → None(不崩)。"""
        director = self._director(library, session)
        director._bot = None
        assert director._maybe_build_townhall_confirm([self._build_at((60.0, 50.0))]) is None


# ==========================================================================
# WP1 偷矿：Director 接线 StealthCellManager（2026-06-10）
# ==========================================================================


class TestStealthMineDirectorIntegration:
    """验证：玩家下 stealth_mine directive → Director 在 on_tick 后创建 PENDING cell。

    不依赖 SC2 客户端；全程 FakeFacade + MockLLMProvider。
    """

    @pytest.mark.asyncio
    async def test_stealth_mine_creates_pending_cell(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """一条 stealth_mine directive → manager.cells 多一个 PENDING cell。"""
        from vibecraft.bot.stealth.cell import StealthState

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "去对方三矿偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {
                            "point": [55.0, 70.0],
                        },
                    }
                ],
            },
        )

        await director.on_player_command("去对方三矿偷矿", now=100.0)
        director.on_tick(now=100.5)

        assert len(director._stealth_manager.cells) == 1
        cell = next(iter(director._stealth_manager.cells.values()))
        assert cell.state == StealthState.PENDING
        assert cell.point == (55.0, 70.0)

    @pytest.mark.asyncio
    async def test_stealth_mine_cell_id_assigned(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """Manager 分配 cell_id=1 给第一条指令。"""
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [50.0, 50.0]},
                    }
                ],
            },
        )

        await director.on_player_command("偷矿", now=100.0)
        director.on_tick(now=100.5)

        assert 1 in director._stealth_manager.cells

    @pytest.mark.asyncio
    async def test_two_stealth_mine_directives_two_cells(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """两条 stealth_mine directive → 两个独立 cell（id=1, id=2）。"""
        from vibecraft.bot.stealth.cell import StealthState

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷两个点",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [50.0, 50.0]},
                    },
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [80.0, 20.0]},
                    },
                ],
            },
        )

        await director.on_player_command("偷两个点", now=100.0)
        director.on_tick(now=100.5)

        assert len(director._stealth_manager.cells) == 2
        for cell in director._stealth_manager.cells.values():
            assert cell.state == StealthState.PENDING

    def test_director_has_stealth_manager_attr(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """Director 实例有 _stealth_manager 属性。"""
        from vibecraft.bot.stealth.manager import StealthCellManager

        director = _make_director(library, session, FakeFacade(), {})
        assert hasattr(director, "_stealth_manager")
        assert isinstance(director._stealth_manager, StealthCellManager)

    @pytest.mark.asyncio
    async def test_on_tick_calls_manager_on_tick(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """Director.on_tick 会调用 manager.on_tick（骨架阶段无副作用，只验不崩）。"""
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(library, session, facade, {})
        # 骨架阶段 on_tick 是 pass，调用不应 raise
        director.on_tick(now=100.0)  # should not raise

    # ---- 2026-06-12 用户反馈 #6：非神族偷矿友好拒绝 ----

    @pytest.mark.asyncio
    async def test_stealth_mine_zerg_race_no_cell_created(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """虫族下 stealth_mine 指令 → 无 cell 创建（偷矿暂只支持神族）。"""
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "去偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [55.0, 70.0]},
                    }
                ],
            },
            my_race="zerg",  # 虫族
        )

        await director.on_player_command("去偷矿", now=100.0)
        director.on_tick(now=100.5)

        # 无 cell 创建
        assert len(director._stealth_manager.cells) == 0, "非神族不应创建偷矿 cell"

    @pytest.mark.asyncio
    async def test_stealth_mine_zerg_race_override_status_failed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """虫族下 stealth_mine → directive override_status 为 'failed'（拒绝上报）。"""
        from vibecraft.directives.models import Directive, StealthMinePayload

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
            my_race="zerg",
        )

        d = Directive(payload=StealthMinePayload(point=(55.0, 70.0)), issued_at=100.0)
        director.submit_directive(d, now=100.0)
        director.on_tick(now=100.5)

        status_entry = director._override_status.get(d.id)
        assert status_entry is not None, "应有 override_status 条目"
        assert status_entry.get("status") == "failed", (
            f"拒绝时状态应为 'failed'，实际={status_entry}"
        )
        assert "神族" in status_entry.get("reason", ""), f"拒绝原因应提及神族，实际={status_entry}"

    @pytest.mark.asyncio
    async def test_stealth_mine_terran_race_no_cell_created(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """人族下 stealth_mine 指令 → 无 cell 创建。"""
        from vibecraft.directives.models import Directive, StealthMinePayload

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "x", "confidence": 1, "directives": []},
            my_race="terran",
        )

        d = Directive(payload=StealthMinePayload(point=(55.0, 70.0)), issued_at=100.0)
        director.submit_directive(d, now=100.0)
        director.on_tick(now=100.5)

        assert len(director._stealth_manager.cells) == 0, "人族也不应创建偷矿 cell"


# ==========================================================================
# WP6：build_snapshot 透传 stealth_cells
# ==========================================================================


class TestSnapshotStealthCells:
    """验证 build_snapshot 输出的 stealth_cells 字段内容与 StealthCellManager 一致。"""

    def _make_director_plain(self, library: StrategyLibrary, session: GameSession) -> object:
        """不带 LLM provider，直接用 _make_director 空 mock。"""
        return _make_director(library, session, FakeFacade(), {})

    def test_snapshot_has_stealth_cells_empty(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """无 cell 时 stealth_cells 为空列表。"""
        director = self._make_director_plain(library, session)
        snap = director.build_snapshot(now=10.0)
        assert "stealth_cells" in snap, "snapshot 应有 stealth_cells 字段"
        assert snap["stealth_cells"] == [], (
            f"无 cell 时 stealth_cells 应为 []，实际={snap['stealth_cells']}"
        )

    def test_snapshot_stealth_cells_reflects_pending_cell(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """有 1 个 PENDING cell 时，stealth_cells 有 1 项，字段正确。"""
        from vibecraft.directives.models import StealthMinePayload

        director = self._make_director_plain(library, session)
        payload = StealthMinePayload(point=(55.0, 70.0), worker_target=16, on_attack="flee")
        cid = director._stealth_manager.create_cell(payload)

        snap = director.build_snapshot(now=10.0)
        cells = snap["stealth_cells"]
        assert len(cells) == 1, f"应有 1 个 cell，实际={cells}"

        item = cells[0]
        assert item["cell_id"] == cid
        assert item["location"] == [55.0, 70.0]
        assert item["worker_count"] == 0  # PENDING 阶段无农民
        assert item["state"] == "pending"
        assert item["has_gas"] is False

    def test_snapshot_stealth_cells_reflects_mining_cell(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """MINING cell 有 3 个农民 → worker_count=3，state='mining'。"""
        from vibecraft.bot.stealth.cell import StealthState
        from vibecraft.directives.models import StealthMinePayload

        director = self._make_director_plain(library, session)
        payload = StealthMinePayload(point=(30.0, 40.0), worker_target=16, on_attack="flee")
        cid = director._stealth_manager.create_cell(payload)
        cell = director._stealth_manager.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = 123
        cell.worker_tags = {10, 20, 30}

        snap = director.build_snapshot(now=20.0)
        cells = snap["stealth_cells"]
        assert len(cells) == 1
        item = cells[0]
        assert item["state"] == "mining"
        assert item["worker_count"] == 3
        assert item["has_gas"] is False

    def test_snapshot_stealth_cells_multi_cell(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """多 cell 时 stealth_cells 列表长度 = cell 数，cell_id 均出现。"""
        from vibecraft.directives.models import StealthMinePayload

        director = self._make_director_plain(library, session)
        for x in [10.0, 30.0, 50.0]:
            payload = StealthMinePayload(point=(x, 10.0), worker_target=16, on_attack="flee")
            director._stealth_manager.create_cell(payload)

        snap = director.build_snapshot(now=5.0)
        cells = snap["stealth_cells"]
        assert len(cells) == 3, f"应有 3 个 cell，实际={cells}"
        cell_ids = {c["cell_id"] for c in cells}
        assert cell_ids == {1, 2, 3}, f"cell_id 应为 {{1,2,3}}，实际={cell_ids}"

    def test_snapshot_stealth_cells_mineral_gas_workers(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """MINING cell 有 3 采矿 + 2 采气农民 → mineral_workers=3, gas_workers=2。"""
        from vibecraft.bot.stealth.cell import StealthState
        from vibecraft.directives.models import StealthMinePayload

        director = self._make_director_plain(library, session)
        payload = StealthMinePayload(point=(30.0, 40.0), worker_target=16, on_attack="flee")
        cid = director._stealth_manager.create_cell(payload)
        cell = director._stealth_manager.cells[cid]
        cell.state = StealthState.MINING
        cell.nexus_tag = 123
        cell.worker_tags = {10, 20, 30, 40, 50}  # 5 農民
        cell.gas_worker_tags = {40, 50}  # 2 採氣

        snap = director.build_snapshot(now=20.0)
        item = snap["stealth_cells"][0]
        assert item["mineral_workers"] == 3, f"mineral_workers 应为 3，实际={item}"
        assert item["gas_workers"] == 2, f"gas_workers 应为 2，实际={item}"


# ==========================================================================
# WP6 需求1：stealth_mine 指令卡在 command_cards 中显示
# ==========================================================================


class TestStealthMineCommandCard:
    """验证 stealth_mine directive 进 command_cards + 农民数正确。"""

    @pytest.mark.asyncio
    async def test_stealth_mine_appears_in_command_cards(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stealth_mine directive commit 后，command_cards 里出现类型为 stealth_mine 的卡。"""
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [55.0, 70.0]},
                    }
                ],
            },
        )

        await director.on_player_command("偷矿", now=100.0)
        director.on_tick(now=100.5)

        snap = director.build_snapshot(now=100.5)
        cards = snap["command_cards"]
        stealth_cards = [c for c in cards if c.get("type") == "stealth_mine"]
        assert len(stealth_cards) == 1, f"应有 1 张 stealth_mine 卡，实际 cards={cards}"
        card = stealth_cards[0]
        assert card["layer"] == "L2"
        assert card["revokable"] is True
        assert "55" in card["display"] or "偷矿" in card["display"]

    @pytest.mark.asyncio
    async def test_stealth_mine_card_shows_worker_counts(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stealth_mine 卡片包含实时农民数（stealth_workers 字段）。"""
        from vibecraft.bot.stealth.cell import StealthState

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [55.0, 70.0]},
                    }
                ],
            },
        )

        await director.on_player_command("偷矿", now=100.0)
        director.on_tick(now=100.5)

        # 手动设置农民数（绕过真实 SC2）
        cell_id = (
            director._directive_to_cell_id[next(iter(director._committed_directives))]
            if director._committed_directives
            else next(iter(director._directive_to_cell_id.values()))
        )
        cell = director._stealth_manager.cells[cell_id]
        cell.state = StealthState.MINING
        cell.worker_tags = {1, 2, 3, 4, 5}
        cell.gas_worker_tags = {4, 5}

        snap = director.build_snapshot(now=100.5)
        card = next(c for c in snap["command_cards"] if c.get("type") == "stealth_mine")
        sw = card.get("stealth_workers")
        assert sw is not None, "stealth_mine 卡应有 stealth_workers 字段"
        assert sw["mineral"] == 3
        assert sw["gas"] == 2


# ==========================================================================
# WP6 需求2：cell release 时推 event + 清 directive 卡
# ==========================================================================


class TestStealthCellReleaseEvent:
    """验证 cell 被 release 时：event 被推送 + directive card 被清理。"""

    @pytest.mark.asyncio
    async def test_stealth_cell_release_pushes_event(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """_stealth_manager._release_cell 后 on_tick drain → event_callback 收到 stealth.cell_released。"""

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [55.0, 70.0]},
                    }
                ],
            },
        )

        # 收集 event 回调
        pushed_events: list[dict] = []
        director.set_event_callback(lambda ev: pushed_events.append(ev))

        await director.on_player_command("偷矿", now=100.0)
        director.on_tick(now=100.5)

        # 手动注入一个 pending release event（模拟 manager 被攻击）
        director._stealth_manager.pending_release_events.append(
            {
                "cell_id": 1,
                "reason": "under_attack",
                "location": [55.0, 70.0],
                "state": "released",
            }
        )

        director.on_tick(now=101.0)

        release_events = [e for e in pushed_events if e.get("kind") == "stealth.cell_released"]
        assert len(release_events) == 1, (
            f"应有 1 条 stealth.cell_released event，实际={pushed_events}"
        )
        ev = release_events[0]
        assert ev["payload"]["cell_id"] == 1
        assert ev["payload"]["reason"] == "under_attack"
        assert ev["payload"]["reason_zh"] == "被攻击"

    @pytest.mark.asyncio
    async def test_stealth_cell_release_clears_directive_card(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """cell release 后，对应 directive 从 _committed_directives 中清除（卡片消失）。"""

        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "偷矿",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "stealth_mine",
                        "payload": {"point": [55.0, 70.0]},
                    }
                ],
            },
        )
        director.set_event_callback(lambda _: None)

        await director.on_player_command("偷矿", now=100.0)
        director.on_tick(now=100.5)

        # directive 应在 _committed_directives 中
        assert len(director._committed_directives) >= 1

        # 注入 pending release event
        director._stealth_manager.pending_release_events.append(
            {
                "cell_id": 1,
                "reason": "under_attack",
                "location": [55.0, 70.0],
                "state": "released",
            }
        )
        director.on_tick(now=101.0)

        # directive 卡应被清除
        stealth_cards = [
            c
            for c in director.build_snapshot(now=101.0)["command_cards"]
            if c.get("type") == "stealth_mine"
        ]
        assert len(stealth_cards) == 0, f"release 后不应有 stealth_mine 卡，实际={stealth_cards}"
