"""农民基地调度（WORKER_TASK）schema + 执行层单测。

覆盖：
1. schema：WorkerTaskPayload 能 parse；进 Payload 判别联合；额外字段被拒。
2. Director._apply_to_facade WORKER_TASK 分支：
   - prioritize_minerals/gas → 直接调 facade.set_mining_priority("mineral"/"gas")
     （复用全局，不做 per-base）。
   - transfer_to_base → 选中 from_base 附近正在采矿的农民，Reserve 住 + 立即下
     gather 令，注册进 _worker_task_transfer_orders。
3. Director._tick_worker_task_transfer：
   - settle 期内每 tick 重发 gather 令。
   - 到期 → release_unit_role 释放 + done。
   - 农民全部消失 → 提前 done。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from vibecraft.bot import Director, FakeFacade
from vibecraft.bot.facade import UnitRole
from vibecraft.directives.models import Directive, WorkerTaskPayload
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ==========================================================================
# 1. Schema
# ==========================================================================


def test_worker_task_payload_parses_minimal() -> None:
    p = WorkerTaskPayload.model_validate(
        {"type": "worker_task", "from_base": "main", "action": "prioritize_minerals"}
    )
    assert p.type.value == "worker_task"
    assert p.from_base == "main"
    assert p.action == "prioritize_minerals"
    assert p.to_base is None


def test_worker_task_payload_transfer_with_to_base() -> None:
    p = WorkerTaskPayload.model_validate(
        {
            "type": "worker_task",
            "from_base": "main",
            "action": "transfer_to_base",
            "to_base": "natural",
        }
    )
    assert p.action == "transfer_to_base"
    assert p.to_base == "natural"


def test_worker_task_payload_in_payload_union() -> None:
    from vibecraft.directives.models import Payload

    adapter = TypeAdapter(Payload)
    obj = adapter.validate_python(
        {"type": "worker_task", "from_base": "main", "action": "prioritize_gas"}
    )
    assert isinstance(obj, WorkerTaskPayload)


def test_worker_task_payload_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        WorkerTaskPayload.model_validate(
            {
                "type": "worker_task",
                "from_base": "main",
                "action": "prioritize_minerals",
                "unknown_field": "oops",
            }
        )


def test_worker_task_payload_rejects_bad_action() -> None:
    with pytest.raises(ValidationError):
        WorkerTaskPayload.model_validate(
            {"type": "worker_task", "from_base": "main", "action": "not_a_real_action"}
        )


def test_worker_task_payload_requires_from_base() -> None:
    with pytest.raises(ValidationError):
        WorkerTaskPayload.model_validate({"type": "worker_task", "action": "prioritize_minerals"})


# ==========================================================================
# 2. Director fixtures
# ==========================================================================


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_director(session: GameSession, mock_bot) -> Director:
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "terran.yaml",
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_directive(
    from_base: str = "main", action: str = "prioritize_minerals", to_base: str | None = None
) -> Directive:
    payload = WorkerTaskPayload(from_base=from_base, action=action, to_base=to_base)
    return Directive(payload=payload, issued_at=0.0, source_text="主矿农民优先采水晶")


class _Worker:
    def __init__(self, tag: int, pos, orders=None, is_carrying_vespene: bool = False) -> None:
        self.tag = tag
        self.position = pos
        self.orders = orders or []
        self.is_carrying_vespene = is_carrying_vespene


def _real_unit_task_cls():
    """强制拿到**真实** sharpy UnitTask，不受其它测试文件遗留的 fake sys.modules 影响。

    踩坑(2026-07-08 全量跑发现)：`tests/unit/conftest.py::_inject_sharpy_for_bot`
    等 fixture 会往 `sys.modules["sharpy.managers.core.roles.unit_task"]` 塞一个只有
    `Idle/Reserved` 两个成员的 FakeUnitTask（没有 `Gathering`）。若某次全量跑里这类
    fixture 的清理没抢在本测试前完成（pre-existing 的 test 隔离脆弱区，非本次改动引入
    ——同一 prefix 换个子集就会命中别的文件，`git stash` 复现同样炸），`from
    sharpy...import UnitTask` 会拿到这个残留 fake 模块，`UnitTask.Gathering` 直接
    AttributeError，`_select_mining_workers_near` 静默吞掉异常→返回空列表→
    看着像"没有采矿农民"的假 FAIL。这里显式清掉 `sharpy.*` 缓存,保证拿到真实类。
    """
    import sys

    for key in list(sys.modules):
        if key == "sharpy" or key.startswith("sharpy."):
            del sys.modules[key]
    from vibecraft.bot.auto_combat.common_bot import _ensure_sharpy_on_path

    _ensure_sharpy_on_path()
    from sharpy.managers.core.roles.unit_task import UnitTask

    return UnitTask


def _bot_with_workers(workers: list[_Worker], *, gas_tags: set[int] | None = None):
    """mock bot：workers.closer_than 返回全部 workers；knowledge.roles.unit_role
    对所有单位返回 Gathering；gas_buildings 提供 gas_tags 集合。

    townhalls 放一个 dummy townhall 在 start_location（"main" named_spot 解析靠它,
    ⚠️ 不能漏设 — 漏了 MagicMock 会自动生成假 Point2,后续距离运算炸)。
    """
    from sc2.position import Point2

    bot = MagicMock()
    bot.start_location = Point2((10.0, 10.0))
    zone_mgr = SimpleNamespace(
        expansion_zones=[
            SimpleNamespace(center_location=Point2((10.0, 10.0))),
            SimpleNamespace(center_location=Point2((40.0, 40.0))),
        ]
    )

    UnitTask = _real_unit_task_cls()
    roles = SimpleNamespace(unit_role=lambda u: UnitTask.Gathering)
    bot.knowledge = SimpleNamespace(zone_manager=zone_mgr, roles=roles)

    class _Units(list):
        def closer_than(self, radius, point):
            return _Units([w for w in self if w.position.distance_to(point) <= radius])

        def closest_to(self, point):
            return min(self, key=lambda u: u.position.distance_to(point))

    bot.workers = _Units(workers)
    bot.townhalls = _Units([SimpleNamespace(position=Point2((10.0, 10.0)))])

    gas_class = [SimpleNamespace(tag=t) for t in (gas_tags or set())]
    bot.gas_buildings = gas_class
    return bot


# ==========================================================================
# 3. _apply_to_facade：prioritize_*
# ==========================================================================


def test_prioritize_minerals_calls_set_mining_priority(session: GameSession) -> None:
    bot = _bot_with_workers([])
    director = _make_director(session, bot)

    directive = _make_directive(action="prioritize_minerals")
    director._apply_to_facade(directive, now=0.0)

    assert director.facade.mining_priority_calls == ["mineral"]
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"


def test_prioritize_gas_calls_set_mining_priority(session: GameSession) -> None:
    bot = _bot_with_workers([])
    director = _make_director(session, bot)

    directive = _make_directive(action="prioritize_gas")
    director._apply_to_facade(directive, now=0.0)

    assert director.facade.mining_priority_calls == ["gas"]


def test_prioritize_no_from_base_point_on_hold(session: GameSession) -> None:
    """from_base 解析不出坐标 → on_hold，不调 set_mining_priority。"""
    bot = MagicMock()
    bot.knowledge = SimpleNamespace(zone_manager=SimpleNamespace(expansion_zones=[]))
    bot.townhalls = []
    director = _make_director(session, bot)

    directive = _make_directive(action="prioritize_minerals")
    director._apply_to_facade(directive, now=0.0)

    assert director.facade.mining_priority_calls == []
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "on_hold"


# ==========================================================================
# 4. _apply_to_facade：transfer_to_base
# ==========================================================================


def test_transfer_selects_mining_workers_and_reserves(session: GameSession) -> None:
    from sc2.position import Point2

    w1 = _Worker(1, Point2((10.0, 10.0)))
    w2 = _Worker(2, Point2((10.5, 10.5)))
    # 采气的农民（order target 是 gas 建筑 tag）→ 不该被选中
    w_gas = _Worker(3, Point2((10.0, 11.0)), orders=[SimpleNamespace(target=500)])
    bot = _bot_with_workers([w1, w2, w_gas], gas_tags={500})
    director = _make_director(session, bot)

    directive = _make_directive(action="transfer_to_base", to_base="natural")
    director._apply_to_facade(directive, now=0.0)

    state = director._worker_task_transfer_orders.get(directive.id)
    assert state is not None
    assert set(state["tags"]) == {1, 2}
    assert state["to_point"] == (40.0, 40.0)

    # 立即下 gather 令
    gather_tags = {t for t, _ in director.facade.worker_gather_orders}
    assert gather_tags == {1, 2}

    # Reserve 住（set_unit_role LLM_CONTROLLED）
    assert director.facade.unit_roles.get(1) == UnitRole.LLM_CONTROLLED
    assert director.facade.unit_roles.get(2) == UnitRole.LLM_CONTROLLED

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "active"


def test_transfer_no_workers_fails(session: GameSession) -> None:
    bot = _bot_with_workers([])
    director = _make_director(session, bot)

    directive = _make_directive(action="transfer_to_base", to_base="natural")
    director._apply_to_facade(directive, now=0.0)

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "failed"
    assert directive.id not in director._worker_task_transfer_orders


def test_transfer_no_to_base_dispatch_fails(session: GameSession) -> None:
    from sc2.position import Point2

    w1 = _Worker(1, Point2((10.0, 10.0)))
    bot = _bot_with_workers([w1])
    director = _make_director(session, bot)

    directive = _make_directive(action="transfer_to_base", to_base=None)
    director._apply_to_facade(directive, now=0.0)

    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "failed"
    assert directive.id not in director._worker_task_transfer_orders


# ==========================================================================
# 5. _tick_worker_task_transfer
# ==========================================================================


def test_tick_reissues_gather_during_settle(session: GameSession) -> None:
    from sc2.position import Point2

    w1 = _Worker(1, Point2((10.0, 10.0)))
    bot = _bot_with_workers([w1])
    director = _make_director(session, bot)
    director.facade.unit_positions[1] = (10.0, 10.0)

    directive = _make_directive(action="transfer_to_base", to_base="natural")
    director._apply_to_facade(directive, now=0.0)
    n_orders_after_apply = len(director.facade.worker_gather_orders)

    director._tick_worker_task_transfer(now=1.0)

    assert len(director.facade.worker_gather_orders) > n_orders_after_apply
    assert directive.id in director._worker_task_transfer_orders
    status = director._override_status.get(directive.id, {})
    assert status.get("status") != "done"


def test_tick_releases_after_settle_expires(session: GameSession) -> None:
    from sc2.position import Point2

    w1 = _Worker(1, Point2((10.0, 10.0)))
    bot = _bot_with_workers([w1])
    director = _make_director(session, bot)
    director.facade.unit_positions[1] = (10.0, 10.0)

    directive = _make_directive(action="transfer_to_base", to_base="natural")
    director._apply_to_facade(directive, now=0.0)

    settle_s = director._WORKER_TRANSFER_SETTLE_S
    director._tick_worker_task_transfer(now=settle_s + 1.0)

    assert directive.id not in director._worker_task_transfer_orders
    assert 1 in director.facade.release_unit_role_calls
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "done"


def test_tick_all_workers_gone_finishes_early(session: GameSession) -> None:
    from sc2.position import Point2

    w1 = _Worker(1, Point2((10.0, 10.0)))
    bot = _bot_with_workers([w1])
    director = _make_director(session, bot)
    # 不注入 unit_positions[1] → get_unit_position 返回 None（视为消失）

    directive = _make_directive(action="transfer_to_base", to_base="natural")
    director._apply_to_facade(directive, now=0.0)

    director._tick_worker_task_transfer(now=1.0)

    assert directive.id not in director._worker_task_transfer_orders
    status = director._override_status.get(directive.id, {})
    assert status.get("status") == "done"
