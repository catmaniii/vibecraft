"""持续征兵（auto_enroll / recruit_new）单测。

Task #521：
- GroupAssignPayload.auto_enroll=True → directive 留在 _in_flight，watcher 注册，
  每 tick 把新出现的匹配 unit_type 的单位 ADD 进编队。
- UnitClaimPayload.recruit_new=True + persistent=True → watcher 注册，
  每 tick 把新单位并入 standing order 并下发相同 action。
- revoke auto_enroll：watcher 删除、编队保留。
- group_clear：watcher + directive 一起清。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    GroupAssignPayload,
    GroupClearPayload,
    UnitClaimPayload,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _make_director(
    library: StrategyLibrary,
    session: GameSession,
    facade: FakeFacade,
) -> Director:
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw={},
                input_tokens=10,
                output_tokens=5,
                latency_ms=1.0,
            )
        ]
    )
    parser = IntentParser(provider, library, session=session, my_race="protoss")
    return Director(facade=facade, parser=parser, session=session, library=library)


def _standby_task(named_spot: str = "natural") -> Task:
    return Task(
        primary_action=Action(
            verb=Verb.STANDBY,
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot=named_spot),
        )
    )


# ---------------------------------------------------------------------------
# Schema 测试
# ---------------------------------------------------------------------------


class TestSchemaDefaults:
    def test_group_assign_auto_enroll_default_false(self) -> None:
        payload = GroupAssignPayload(
            group_id=1,
            selector=Selector(unit_type="VoidRay"),
        )
        assert payload.auto_enroll is False

    def test_unit_claim_recruit_new_default_false(self) -> None:
        payload = UnitClaimPayload(
            selector=Selector(unit_type="Stalker"),
            task=_standby_task(),
            persistent=True,
        )
        assert payload.recruit_new is False

    def test_group_assign_old_json_no_auto_enroll_parses(self) -> None:
        """旧 JSON 不含 auto_enroll 字段，应正常反序列化（默认 False）。"""
        import json

        old_json = json.dumps(
            {
                "type": "group_assign",
                "group_id": 2,
                "selector": {"unit_type": "Zealot"},
            }
        )
        payload = GroupAssignPayload.model_validate_json(old_json)
        assert payload.auto_enroll is False

    def test_unit_claim_old_json_no_recruit_new_parses(self) -> None:
        """旧 JSON 不含 recruit_new 字段，应正常反序列化（默认 False）。"""
        import json

        old_json = json.dumps(
            {
                "type": "unit_claim",
                "selector": {"unit_type": "Stalker"},
                "task": {
                    "primary_action": {
                        "verb": "standby",
                        "target": {"kind": "named_spot", "named_spot": "natural"},
                    }
                },
                "persistent": True,
            }
        )
        payload = UnitClaimPayload.model_validate_json(old_json)
        assert payload.recruit_new is False


# ---------------------------------------------------------------------------
# auto_enroll submit：留在 _in_flight，watcher 注册，初始单位入队
# ---------------------------------------------------------------------------


class TestAutoEnrollSubmit:
    def test_auto_enroll_stays_in_flight(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101, 102]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # directive 留在 _in_flight（不立即 done）
        assert d.id in director._in_flight

    def test_auto_enroll_watcher_registered(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101, 102]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        assert d.id in director._recruit_watchers
        w = director._recruit_watchers[d.id]
        assert w["kind"] == "group"
        assert w["group_id"] == 1
        assert w["unit_type"] == "VoidRay"

    def test_auto_enroll_initial_units_in_queue(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101, 102]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 初始单位已通过 _apply_group_assign (SET) 入队
        assert 101 in director._voice_groups[1]
        assert 102 in director._voice_groups[1]

    def test_auto_enroll_seen_initialized_with_all_current(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """seen 初始化为当前全量 tags（保证旧单位不被当成新出的）。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101, 102]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        seen = director._recruit_watchers[d.id]["seen"]
        assert 101 in seen
        assert 102 in seen

    def test_non_auto_enroll_is_immediate_done(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """auto_enroll=False（默认）时，submit 即 done，不进 watcher。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=2,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=False,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 普通 group_assign 立即 done，不进 watcher
        assert d.id not in director._recruit_watchers


# ---------------------------------------------------------------------------
# tick：新 tag 进 _voice_groups，seen 更新；再 tick 无重复
# ---------------------------------------------------------------------------


class TestAutoEnrollTick:
    def test_new_unit_added_to_group_on_tick(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)
        assert director._voice_groups[1] == {101}

        # 新单位 102 出现
        facade.selector_stub["VoidRay"] = [101, 102]
        director._tick_recruit_watchers(now=2.0)

        assert 102 in director._voice_groups[1]

    def test_second_tick_no_duplicate(self, library: StrategyLibrary, session: GameSession) -> None:
        """连续 tick 不重复 ADD 已经在 seen 里的 tag。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 第一次 tick：新单位 102
        facade.selector_stub["VoidRay"] = [101, 102]
        director._tick_recruit_watchers(now=2.0)
        assert director._voice_groups[1] == {101, 102}

        # 第二次 tick：同样的列表，不应再 ADD
        director._tick_recruit_watchers(now=3.0)
        assert director._voice_groups[1] == {101, 102}

    def test_watcher_lazily_removed_when_directive_gone(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """directive 被移出 _in_flight 后，watcher 在下一次 tick 懒删除。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)
        assert d.id in director._recruit_watchers

        # 强制把 directive 从 _in_flight 移除（模拟被 revoke 后的状态）
        director._in_flight.pop(d.id, None)

        # tick → watcher 懒清理
        director._tick_recruit_watchers(now=2.0)
        assert d.id not in director._recruit_watchers


# ---------------------------------------------------------------------------
# recruit_new：新 tag 进 _standing_order_tags + execute_unit_action
# ---------------------------------------------------------------------------


class TestRecruitNewSubmit:
    def test_recruit_new_watcher_registered(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [201]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task("natural"),
                persistent=True,
                recruit_new=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        assert d.id in director._recruit_watchers
        w = director._recruit_watchers[d.id]
        assert w["kind"] == "claim"
        assert w["unit_type"] == "Stalker"

    def test_recruit_new_false_persistent_auto_upgrade(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """LLM 给 recruit_new=True + persistent=False → 自动升级为 persistent。"""
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [201]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task("natural"),
                persistent=False,  # 故意 False，但 recruit_new=True 应自动升级
                recruit_new=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 升级后应进 standing_orders
        assert any(s.id == d.id for s in director.standing_orders)

    def test_recruit_new_tick_adds_to_standing_order_tags(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [201]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task("natural"),
                persistent=True,
                recruit_new=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 新追猎 202 出现
        facade.selector_stub["Stalker"] = [201, 202]
        director._tick_recruit_watchers(now=2.0)

        assert 202 in director._standing_order_tags.get(d.id, set())

    def test_recruit_new_tick_calls_execute_unit_action(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """新单位应收到与 primary_action 相同的指令。"""
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [201]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=UnitClaimPayload(
                selector=Selector(unit_type="Stalker"),
                task=_standby_task("natural"),
                persistent=True,
                recruit_new=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)

        # 清除 submit 时产生的 unit_actions，只看 tick 新增
        unit_actions_before = len(facade.unit_actions)

        # 新追猎 202 出现
        facade.selector_stub["Stalker"] = [201, 202]
        director._tick_recruit_watchers(now=2.0)

        new_actions = facade.unit_actions[unit_actions_before:]
        assert any(a.get("tag") == 202 for a in new_actions), (
            f"期望对 tag=202 调用 execute_unit_action，实际 unit_actions={new_actions}"
        )


# ---------------------------------------------------------------------------
# revoke auto_enroll：watcher 删除、编队保留
# ---------------------------------------------------------------------------


class TestAutoEnrollRevoke:
    def test_revoke_removes_watcher(self, library: StrategyLibrary, session: GameSession) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)
        assert d.id in director._recruit_watchers

        director.revoke_directive(d.id, now=2.0)

        assert d.id not in director._recruit_watchers

    def test_revoke_preserves_group(self, library: StrategyLibrary, session: GameSession) -> None:
        """× 撤销持续征兵后，已入队的单位保留在编队里。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101, 102]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)
        # 初始单位进队
        assert 1 in director._voice_groups

        director.revoke_directive(d.id, now=2.0)

        # 编队保留（不被清空）
        assert 1 in director._voice_groups
        assert 101 in director._voice_groups[1]


# ---------------------------------------------------------------------------
# group_clear：watcher + directive 一起清
# ---------------------------------------------------------------------------


class TestGroupClearClearsWatcher:
    def test_group_clear_removes_auto_enroll_watcher(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        director = _make_director(library, session, facade)

        d = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d], now=1.0)
        assert d.id in director._recruit_watchers

        # group_clear 1 队
        director._submit_directives(
            [Directive(payload=GroupClearPayload(group_id=1), issued_at=2.0)],
            now=2.0,
        )

        # watcher 应被清除
        assert d.id not in director._recruit_watchers

    def test_group_clear_does_not_affect_other_group_watcher(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """清 1 队不影响其他队的 watcher。"""
        facade = FakeFacade()
        facade.selector_stub["VoidRay"] = [101]
        facade.selector_stub["Stalker"] = [201]
        director = _make_director(library, session, facade)

        d1 = Directive(
            payload=GroupAssignPayload(
                group_id=1,
                selector=Selector(unit_type="VoidRay"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        d2 = Directive(
            payload=GroupAssignPayload(
                group_id=2,
                selector=Selector(unit_type="Stalker"),
                auto_enroll=True,
            ),
            issued_at=1.0,
        )
        director._submit_directives([d1, d2], now=1.0)

        # 只 clear 1 队
        director._submit_directives(
            [Directive(payload=GroupClearPayload(group_id=1), issued_at=2.0)],
            now=2.0,
        )

        # 2 队 watcher 保留
        assert d2.id in director._recruit_watchers
