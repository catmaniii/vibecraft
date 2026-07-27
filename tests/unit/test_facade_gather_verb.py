"""_SharpyFacadeBase.execute_unit_action 对 gather verb 的行为单测(F83 修复)。

2026-07-20 玩家报根因:"闲置农民采矿 / 农民回去采矿" → LLM 解析成 unit_claim(gather)。
真机 facade 旧代码 gather verb 落到 else 分支**只 unit.move**(农民走过去、从不发采矿命令);
而 _apply_unit_claim / _assign_standing_order_units 下令前已把农民设 LLM_CONTROLLED
(=sharpy Reserved) → DistributeWorkers 不再自动派这农民采矿 + claim 又只 move →
农民彻底卡 Reserved 闲置(想采矿反被锁死不采矿)。

修复:execute_unit_action 为 gather verb 找矿(mineral field)发 unit.gather(patch),
不再只 move。这里断言真机 facade(_SharpyFacadeBase)对 gather verb 调 unit.gather、
**不调** unit.move(验终态语义:农民真被派去采矿,不是只走过去)。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from sc2.position import Point2


class _Patch:
    """迷你 mineral field mock:只需 tag + position。"""

    def __init__(self, tag: int, x: float, y: float) -> None:
        self.tag = tag
        self.position = Point2((x, y))


class _Fields(list):
    """迷你 Units 集合 mock:支持 __bool__(继承 list)+ closer_than / closest_to。"""

    def closer_than(self, dist: float, point: object) -> _Fields:
        p = Point2(point) if not isinstance(point, Point2) else point
        return _Fields(f for f in self if f.position.distance_to(p) < dist)

    def closest_to(self, point: object) -> _Patch:
        p = Point2(point) if not isinstance(point, Point2) else point
        return min(self, key=lambda f: f.position.distance_to(p))


def _make_facade(unit: object, minerals: _Fields, townhalls: _Fields | None = None):
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()
    bot = SimpleNamespace(
        units=SimpleNamespace(by_tag=lambda t: unit if t == unit.tag else None),
        mineral_field=minerals,
        townhalls=townhalls,
    )
    return cls(bot)


def _target(x: float, y: float) -> dict[str, object]:
    return {"kind": "point", "point": [x, y], "named_spot": None}


def test_gather_verb_issues_gather_not_move() -> None:
    """核心:gather verb → 对目标点附近的矿 unit.gather,绝不 unit.move。"""
    unit = MagicMock()
    unit.tag = 101
    unit.position = Point2((48.0, 48.0))
    minerals = _Fields([_Patch(9001, 51.0, 50.0)])  # 距 (50,50) ~1,在 10 内
    facade = _make_facade(unit, minerals)

    facade.execute_unit_action(101, "gather", target=_target(50.0, 50.0))

    unit.gather.assert_called_once()
    assert unit.gather.call_args.args[0].tag == 9001  # 真派去那块矿
    unit.move.assert_not_called()  # 关键:不再只是 move(旧 bug)


def test_gather_verb_falls_back_to_townhall_minerals() -> None:
    """目标点附近无矿 → 退回离农民最近的己方基地附近的矿(玩家没给具体矿点时回家采)。"""
    unit = MagicMock()
    unit.tag = 101
    unit.position = Point2((30.0, 30.0))
    # 矿只在农民/基地附近;目标点 (80,80) 附近无矿
    minerals = _Fields([_Patch(7001, 31.0, 30.0)])
    townhalls = _Fields([_Patch(1, 30.0, 30.0)])
    facade = _make_facade(unit, minerals, townhalls)

    facade.execute_unit_action(101, "gather", target=_target(80.0, 80.0))

    unit.gather.assert_called_once()
    assert unit.gather.call_args.args[0].tag == 7001  # 回基地那块矿
    unit.move.assert_not_called()


def test_gather_verb_fallback_move_when_no_mineral() -> None:
    """全图无矿(极端)→ fallback move,别 silently 啥都不做。"""
    unit = MagicMock()
    unit.tag = 101
    unit.position = Point2((48.0, 48.0))
    minerals = _Fields([])  # 全图无矿
    facade = _make_facade(unit, minerals)

    facade.execute_unit_action(101, "gather", target=_target(50.0, 50.0))

    unit.gather.assert_not_called()
    unit.move.assert_called_once()  # 兜底 move,不静默丢弃


def test_attack_verb_still_attacks_not_gather() -> None:
    """回归:gather 分支不影响 attack verb(仍 unit.attack,不误发 gather)。"""
    unit = MagicMock()
    unit.tag = 101
    unit.position = Point2((48.0, 48.0))
    minerals = _Fields([_Patch(9001, 51.0, 50.0)])
    facade = _make_facade(unit, minerals)

    facade.execute_unit_action(101, "attack", target=_target(50.0, 50.0))

    unit.attack.assert_called_once()
    unit.gather.assert_not_called()
    unit.move.assert_not_called()


def test_fake_facade_records_gather_verb() -> None:
    """FakeFacade(单测/脚本 mock)对 gather verb 的记录(与真机 facade 同签名同源)。"""
    from vibecraft.bot.facade import FakeFacade

    f = FakeFacade()
    f.execute_unit_action(101, "gather", target=_target(50.0, 50.0))
    assert f.unit_actions[-1]["verb"] == "gather"
    assert f.unit_actions[-1]["tag"] == 101
    assert any(c.method == "execute_unit_action" for c in f.calls)
