"""语音编队（GROUP_ASSIGN / GROUP_CLEAR）单测。

Task D：Director._voice_groups；GROUP_ASSIGN 存 tags（SET 替换），GROUP_CLEAR
release+pop；_resolve_selector_with_count 支持 selector.group_id；
build_snapshot 透传 voice_groups（经 _bot 查兵种，死单位自然滤掉）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade, UnitRole
from vibecraft.directives.models import (
    Directive,
    GroupAssignPayload,
    GroupClearPayload,
)
from vibecraft.directives.scope import Selector
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
    return Director(facade=facade, parser=parser, session=session, library=library)


# ---------------------------------------------------------------------------
# GROUP_ASSIGN
# ---------------------------------------------------------------------------


class TestGroupAssign:
    def test_group_assign_stores_tags(self, library: StrategyLibrary, session: GameSession) -> None:
        facade = FakeFacade()
        facade.selector_stub["WarpPrism"] = [7001, 7002]
        director = _make_director(library, session, facade, {})

        director._submit_directives(
            [
                Directive(
                    payload=GroupAssignPayload(
                        group_id=1,
                        selector=Selector(unit_type="WarpPrism"),
                    ),
                    issued_at=1.0,
                )
            ],
            now=1.0,
        )

        assert director._voice_groups[1] == {7001, 7002}

    def test_group_assign_set_semantics_replaces(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """新 GROUP_ASSIGN 替换旧 tags，不合并。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [2001]
        director = _make_director(library, session, facade, {})
        director._voice_groups[1] = {9999}  # 先放个旧 tag

        director._submit_directives(
            [
                Directive(
                    payload=GroupAssignPayload(
                        group_id=1,
                        selector=Selector(unit_type="Probe"),
                    ),
                    issued_at=2.0,
                )
            ],
            now=2.0,
        )

        assert director._voice_groups[1] == {2001}  # 替换，不含 9999


# ---------------------------------------------------------------------------
# GROUP_CLEAR
# ---------------------------------------------------------------------------


class TestGroupClear:
    def test_group_clear_releases_and_pops(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[2] = {3001}
        facade.unit_roles[3001] = UnitRole.LLM_CONTROLLED

        director._submit_directives(
            [
                Directive(
                    payload=GroupClearPayload(group_id=2),
                    issued_at=3.0,
                )
            ],
            now=3.0,
        )

        assert 2 not in director._voice_groups
        assert any(c.method == "release_unit_role" and c.args[0] == 3001 for c in facade.calls)

    def test_group_clear_nonexistent_group_is_noop(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """清一个不存在的队不报错。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})

        director._submit_directives(
            [
                Directive(
                    payload=GroupClearPayload(group_id=5),
                    issued_at=1.0,
                )
            ],
            now=1.0,
        )

        assert 5 not in director._voice_groups


# ---------------------------------------------------------------------------
# _resolve_selector_with_count + group_id
# ---------------------------------------------------------------------------


class TestResolveSelectorGroupId:
    def test_command_by_group_id_resolves_tags(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[3] = {8001, 8002}

        tags = director._resolve_selector_with_count(Selector(group_id=3))

        assert set(tags) == {8001, 8002}

    def test_group_id_resolve_caps_count(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[3] = {8001, 8002, 8003}

        tags = director._resolve_selector_with_count(Selector(group_id=3, count=2))

        assert len(tags) == 2

    def test_group_id_empty_group_returns_empty(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """group_id 指向未赋值的队 → 返回空列表。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})

        tags = director._resolve_selector_with_count(Selector(group_id=4))

        assert tags == []


# ---------------------------------------------------------------------------
# build_snapshot 透传 voice_groups
# ---------------------------------------------------------------------------


class TestSnapshotVoiceGroups:
    def test_snapshot_includes_voice_groups(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[1] = {7001}

        snap = director.build_snapshot(now=1.0)

        assert "voice_groups" in snap
        assert any(g["group_id"] == 1 for g in snap["voice_groups"])

    def test_snapshot_voice_groups_unit_breakdown(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """mock _bot.units.by_tag 返回带 type_id.name 的单位；死 tag 返回 None 被滤。"""
        from unittest.mock import MagicMock

        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[1] = {7001, 7002}

        bot = MagicMock()

        def by_tag(t: int) -> object:
            if t == 7001:
                u = MagicMock()
                u.type_id.name = "WARPPRISM"
                return u
            return None  # 7002 死

        bot.units.by_tag.side_effect = by_tag
        bot.knowledge = None  # 避免 _walk_plan_tree 意外 walk
        director._bot = bot

        snap = director.build_snapshot(now=1.0)
        g1 = next(g for g in snap["voice_groups"] if g["group_id"] == 1)

        assert g1["units"] == {"WARPPRISM": 1}  # 只算活单位
        assert g1["count"] == 2  # count 是 tags 总数（含死的）

    def test_snapshot_empty_voice_groups(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """没有编队时 voice_groups 是空 list。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})

        snap = director.build_snapshot(now=1.0)

        assert snap["voice_groups"] == []

    def test_snapshot_voice_groups_sorted_by_group_id(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """多个队按 group_id 升序排列。"""
        facade = FakeFacade()
        director = _make_director(library, session, facade, {})
        director._voice_groups[3] = {100}
        director._voice_groups[1] = {200}

        snap = director.build_snapshot(now=1.0)
        ids = [g["group_id"] for g in snap["voice_groups"]]

        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# 连续指令任务链 chain_id（2026-06-02）
# ---------------------------------------------------------------------------


class TestTaskChain:
    """chain_id 绑定同一单位接力走多步。"""

    def test_chain_first_step_binds_then_reuses(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [100]
        director = _make_director(library, session, facade, {})
        # 第一步:带具体 selector + chain_id → 解析 [100] 并绑定到 chainA
        tags1 = director._resolve_selector_with_count(
            Selector(unit_type="Probe", count=1, chain_id="chainA")
        )
        assert tags1 == [100]
        assert director._task_chains["chainA"] == {100}
        # 后续步骤:只带 chain_id —— 即使 selector_stub 变了,仍返回绑定的同一农民 100
        facade.selector_stub["Probe"] = [200, 300]
        tags2 = director._resolve_selector_with_count(Selector(chain_id="chainA"))
        assert tags2 == [100]

    def test_chain_unbound_resolves_and_binds(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [500]
        director = _make_director(library, session, facade, {})
        tags = director._resolve_selector_with_count(Selector(unit_type="Probe", chain_id="cX"))
        assert tags == [500]
        assert director._task_chains["cX"] == {500}
