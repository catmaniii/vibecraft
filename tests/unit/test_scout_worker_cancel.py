"""Task #352: ScoutWorker 玩家撤回探路农民单测。

覆盖:
1. ScoutWorker.cancel() 设 cancelled=True + 清 scout_tag（fake_sharpy_scout_env 环境）
2. execute() 在 cancelled=True 时直接 return True（永久结束）
3. Director._apply_unit_release(Probe selector) → 调 bot.scout_worker.cancel()
4. 非 worker 类型的 unit_release 不触发 cancel
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import Director, FakeFacade, UnitRole
from vibecraft.directives.models import Directive, UnitReleasePayload
from vibecraft.directives.scope import Selector
from vibecraft.directives.types import IssuedBy
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def fake_sharpy_scout_env():
    """为 ScoutWorker 注入最小 fake sharpy（ActBase + sc2.position.Point2）。

    ScoutWorker 顶层 import `from sharpy.plans.acts import ActBase` +
    `from sc2.position import Point2`。fake_sharpy_scout_env 没有注入 ActBase。
    这里独立注入，只覆盖 ScoutWorker 需要的最小集合。
    """
    to_clean: list[str] = []

    def _ensure(name: str) -> ModuleType:
        if name not in sys.modules:
            mod = ModuleType(name)
            sys.modules[name] = mod
            to_clean.append(name)
            return mod
        return sys.modules[name]

    # sc2.position.Point2
    _ensure("sc2")
    sc2_pos = _ensure("sc2.position")

    class FakePoint2(tuple):
        @property
        def x(self) -> float:
            return self[0]

        @property
        def y(self) -> float:
            return self[1]

    sc2_pos.Point2 = FakePoint2  # type: ignore[attr-defined]

    # sharpy.plans.acts.ActBase
    _ensure("sharpy")
    _ensure("sharpy.plans")
    acts_mod = _ensure("sharpy.plans.acts")

    class FakeActBase:
        def __init__(self) -> None:
            pass

    acts_mod.ActBase = FakeActBase  # type: ignore[attr-defined]

    # 清掉 scout_worker 缓存（它在 module level import sc2 + sharpy）
    for key in list(sys.modules):
        if "scout_worker" in key:
            del sys.modules[key]

    yield

    # teardown
    for key in to_clean:
        sys.modules.pop(key, None)
    for key in list(sys.modules):
        if "scout_worker" in key:
            del sys.modules[key]


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


def _make_director(session, library, facade=None, bot=None):
    facade = facade or FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    director = Director(facade=facade, parser=parser, session=session, bot=bot)
    return director, facade


# ============================================================
# ScoutWorker.cancel() 直接单测（需要 fake_sharpy_scout_env 注入 ActBase）
# ============================================================


class TestScoutWorkerExcludesStealthWorkers:
    """_pick_scout 排除偷矿/玩家 claim 农民（在 _llm_controlled_tags 里）。"""

    def _make_units(self):
        class W:
            def __init__(self, tag: int, dist: float) -> None:
                self.tag = tag
                self._dist = dist

            def distance_to(self, _p: object) -> float:
                return self._dist

        class Units:
            def __init__(self, items: list) -> None:
                self.items = list(items)

            def __bool__(self) -> bool:
                return bool(self.items)

            def filter(self, f) -> Units:
                return Units([w for w in self.items if f(w)])

            def closest_to(self, p: object):
                return min(self.items, key=lambda w: w.distance_to(p))

            @property
            def first(self):
                return self.items[0]

        # w1=偷矿农民(离敌最近,本会被选)，w2=普通农民
        return Units([W(1, 1.0), W(2, 100.0)]), W

    def test_pick_scout_skips_llm_controlled(self, fake_sharpy_scout_env):
        from types import SimpleNamespace

        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        units, _ = self._make_units()
        sw.ai = SimpleNamespace(  # type: ignore[attr-defined]
            workers=units,
            _llm_controlled_tags={1},  # 偷矿农民 tag=1
            enemy_start_locations=[(0.0, 0.0)],
        )
        sw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace())  # type: ignore[attr-defined]

        pick = sw._pick_scout()
        assert pick is not None and pick.tag == 2, "偷矿农民(tag=1)不应被选去探路"

    def test_pick_scout_none_when_only_stealth(self, fake_sharpy_scout_env):
        from types import SimpleNamespace

        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        units, _ = self._make_units()
        sw.ai = SimpleNamespace(  # type: ignore[attr-defined]
            workers=units,
            _llm_controlled_tags={1, 2},  # 全是偷矿农民
            enemy_start_locations=[(0.0, 0.0)],
        )
        sw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace())  # type: ignore[attr-defined]

        assert sw._pick_scout() is None, "只剩偷矿农民 → 不派探路"


class TestScoutWorkerRelinquishClaimed:
    """2026-06-14 真局 bug：已持有的 scout 农民被玩家 claim（代理建造/待命/偷矿）后，
    ScoutWorker 必须放手（scout_tag 是存下来的，_pick_scout 的排除只在重挑时生效；
    已持有的若不主动放手 → 每帧 move 它去敌方、跟玩家 build/standby 抢、把农民拖去送死）。
    """

    def _make_sw(self, claimed_tags: set[int], *, scout_tag: int = 555):
        from types import SimpleNamespace

        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        sw.scout_tag = scout_tag
        sw.targets = [(9.0, 9.0)]  # 非空，跨过 targets 早退
        sw.ai = SimpleNamespace(  # type: ignore[attr-defined]
            time=120.0,  # >60(开局) 且 <300(中后期停用) → 进主逻辑
            active_recipe="void_ray_rush",  # 非 4bg
            _llm_controlled_tags=set(claimed_tags),
            enemy_start_locations=[(0.0, 0.0)],
        )
        sw.knowledge = SimpleNamespace(vibecraft=SimpleNamespace())  # type: ignore[attr-defined]
        return sw

    @pytest.mark.asyncio
    async def test_relinquishes_when_scout_claimed(self, fake_sharpy_scout_env):
        """scout_tag 进了 _llm_controlled_tags → execute() 放手（scout_tag=None）+ return False。"""
        sw = self._make_sw(claimed_tags={555})
        result = await sw.execute()
        assert sw.scout_tag is None, "被玩家 claim 的 scout 农民必须放手"
        assert result is False, "放手后本帧 return False（下帧重挑别的农民）"

    @pytest.mark.asyncio
    async def test_relinquishes_when_scout_in_stealth_tags(self, fake_sharpy_scout_env):
        """scout_tag 进了 stealth_worker_tags（偷矿）也放手。"""
        from types import SimpleNamespace

        sw = self._make_sw(claimed_tags=set())
        sw.knowledge = SimpleNamespace(  # type: ignore[attr-defined]
            vibecraft=SimpleNamespace(stealth_worker_tags={555})
        )
        result = await sw.execute()
        assert sw.scout_tag is None
        assert result is False

    @pytest.mark.asyncio
    async def test_keeps_scout_when_not_claimed(self, fake_sharpy_scout_env):
        """未被 claim → 不放手（scout_tag 保留）。"""
        sw = self._make_sw(claimed_tags={999})  # 别的 tag，不含 555
        await sw.execute()
        assert sw.scout_tag == 555, "未被 claim 的 scout 农民不应被放手"


class TestScoutWorkerCancelMethod:
    def test_cancel_sets_cancelled_flag(self, fake_sharpy_scout_env):
        """cancel() → cancelled=True + scout_tag=None。"""
        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        sw.scout_tag = 12345
        sw.cancel()

        assert sw.cancelled is True
        assert sw.scout_tag is None

    def test_cancel_idempotent(self, fake_sharpy_scout_env):
        """多次 cancel() 不报错。"""
        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        sw.cancel()
        sw.cancel()

        assert sw.cancelled is True

    @pytest.mark.asyncio
    async def test_execute_returns_true_when_cancelled(self, fake_sharpy_scout_env):
        """cancelled=True 时 execute() 必须 return True，不访问 self.ai。"""
        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        sw.cancelled = True
        # targets 非空，但 cancelled 优先 → 应 return True 不抛 AttributeError（没 self.ai）
        sw.targets = [MagicMock()]

        result = await sw.execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_execute_returns_true_when_cancelled_empty_targets(self, fake_sharpy_scout_env):
        """cancelled=True + targets 空也 return True（不依赖 targets 判断）。"""
        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        sw.cancelled = True
        sw.targets = []

        result = await sw.execute()
        assert result is True

    def test_not_cancelled_by_default(self, fake_sharpy_scout_env):
        """新建 ScoutWorker 默认 cancelled=False。"""
        from vibecraft.bot.auto_combat.scout_worker import ScoutWorker

        sw = ScoutWorker()
        assert sw.cancelled is False


# ============================================================
# Director._apply_unit_release → ScoutWorker.cancel()
# （使用 MagicMock 模拟 scout_worker，不需要 fake_sharpy_scout_env）
# ============================================================


class TestDirectorUnitReleaseScoutCancel:
    def _make_unit_release_directive(self, unit_type: str, count: int | None = 1) -> Directive:
        payload = UnitReleasePayload(
            selector=Selector(unit_type=unit_type, count=count),
            return_to_role="IDLE",
        )
        return Directive(payload=payload, issued_at=10.0, issued_by=IssuedBy.VOICE)

    def test_probe_release_cancels_scout_worker(self, library, session):
        """unit_release(Probe) → bot.scout_worker.cancel() 被调。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [101]

        scout_worker = MagicMock()
        scout_worker.cancelled = False
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)

        d = self._make_unit_release_directive("Probe", count=1)
        director._submit_directives([d], now=10.0)
        # 直接调 _apply_to_facade 绕过 board commit 时序（等价 TestUnitRelease 做法）
        director._apply_to_facade(d, now=10.0)

        scout_worker.cancel.assert_called_once()

    def test_probe_release_no_bot_does_not_crash(self, library, session):
        """_bot=None 時 unit_release(Probe) 不崩溃。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [101]

        director, _ = _make_director(session, library, facade=facade, bot=None)
        d = self._make_unit_release_directive("Probe", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)
        # 无 bot → 不应抛异常，正常执行

    def test_non_worker_release_does_not_cancel_scout_worker(self, library, session):
        """unit_release(Stalker) → scout_worker.cancel() 不被调。"""
        facade = FakeFacade()
        facade.selector_stub["Stalker"] = [201]

        scout_worker = MagicMock()
        scout_worker.cancelled = False
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)
        d = self._make_unit_release_directive("Stalker", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        scout_worker.cancel.assert_not_called()

    def test_scv_release_cancels_scout_worker(self, library, session):
        """人族 unit_release(SCV) 也触发 cancel。"""
        facade = FakeFacade()
        facade.selector_stub["SCV"] = [301]

        scout_worker = MagicMock()
        scout_worker.cancelled = False
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)
        d = self._make_unit_release_directive("SCV", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        scout_worker.cancel.assert_called_once()

    def test_drone_release_cancels_scout_worker(self, library, session):
        """虫族 unit_release(Drone) 也触发 cancel。"""
        facade = FakeFacade()
        facade.selector_stub["Drone"] = [401]

        scout_worker = MagicMock()
        scout_worker.cancelled = False
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)
        d = self._make_unit_release_directive("Drone", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        scout_worker.cancel.assert_called_once()

    def test_probe_release_sets_unit_role_idle(self, library, session):
        """unit_release 执行后 facade.set_unit_role(tag, IDLE) 仍然调了。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [101]

        scout_worker = MagicMock()
        scout_worker.cancelled = False
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)
        d = self._make_unit_release_directive("Probe", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        assert facade.unit_roles.get(101) == UnitRole.IDLE

    def test_already_cancelled_scout_worker_not_cancelled_again(self, library, session):
        """scout_worker 已 cancelled=True 时，不重复调 cancel()。"""
        facade = FakeFacade()
        facade.selector_stub["Probe"] = [101]

        scout_worker = MagicMock()
        scout_worker.cancelled = True  # 已撤销
        bot_mock = MagicMock()
        bot_mock.scout_worker = scout_worker

        director, _ = _make_director(session, library, facade=facade, bot=bot_mock)
        d = self._make_unit_release_directive("Probe", count=1)
        director._submit_directives([d], now=10.0)
        director._apply_to_facade(d, now=10.0)

        # cancelled 已是 True → 跳过 cancel()
        scout_worker.cancel.assert_not_called()
