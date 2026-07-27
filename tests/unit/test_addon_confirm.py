"""P1 addon decision 单测(2026-06-18).

覆盖:
1. StructureItem.addon_decided schema 回归(默认 False,可设 True,序列化正确)
2. Director._recommend_addon_mix 三组断言(无 SC2 时走默认; 有 mock 时走算法)
3. Director._maybe_build_addon_confirm 触发/不触发 + 3 选项内容
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    StructureItem,
    StructureOverridePayload,
)
from vibecraft.directives.types import IssuedBy
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.llm.schema import ClarificationRequest
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def terran_library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "terran.yaml",
    )


def _make_director(
    session: GameSession, library: StrategyLibrary, my_race: str = "terran"
) -> Director:
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    return Director(facade=FakeFacade(), parser=parser, session=session, library=library)


def _structure_directive(
    structure_type: str,
    delta: int = 1,
    addon_decided: bool = False,
    issued_by: IssuedBy = IssuedBy.VOICE,
) -> Directive:
    return Directive(
        payload=StructureOverridePayload(
            items=[
                StructureItem(
                    structure_type=structure_type, delta=delta, addon_decided=addon_decided
                )
            ],
        ),
        issued_at=10.0,
        issued_by=issued_by,
    )


# ---------------------------------------------------------------------------
# 1. Schema: addon_decided field
# ---------------------------------------------------------------------------


class TestStructureItemAddonDecided:
    def test_default_is_false(self) -> None:
        item = StructureItem(structure_type="Barracks", delta=1)
        assert item.addon_decided is False

    def test_can_set_true(self) -> None:
        item = StructureItem(structure_type="Barracks", delta=1, addon_decided=True)
        assert item.addon_decided is True

    def test_serialization_false(self) -> None:
        item = StructureItem(structure_type="Gateway", delta=2)
        d = item.model_dump()
        assert "addon_decided" in d
        assert d["addon_decided"] is False

    def test_serialization_true(self) -> None:
        item = StructureItem(structure_type="Barracks", delta=4, addon_decided=True)
        d = item.model_dump()
        assert d["addon_decided"] is True

    def test_roundtrip_via_payload(self) -> None:
        """StructureOverridePayload 含 addon_decided=True 的 item 序列化/反序列化正常。"""
        payload = StructureOverridePayload(
            items=[
                StructureItem(structure_type="Barracks", delta=4, addon_decided=True),
                StructureItem(structure_type="BarracksTechLab", delta=2),
            ]
        )
        d = payload.model_dump()
        payload2 = StructureOverridePayload.model_validate(d)
        assert payload2.items[0].addon_decided is True
        assert payload2.items[1].addon_decided is False  # default

    def test_extra_fields_forbidden(self) -> None:
        """StructureItem 有 extra='forbid'，未知字段应报错。"""
        with pytest.raises(Exception):
            StructureItem(structure_type="Barracks", delta=1, unknown_field=99)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. _recommend_addon_mix — 基本约束(SC2 在此环境可用,算法走真实数据)
# ---------------------------------------------------------------------------


class TestRecommendAddonMixDefaults:
    """不 mock 特定兵种信息,验证基本约束。

    SC2 在此环境可用,算法使用真实 TRAIN_INFO 数据。测试仅验证
    "techlab+reactor==count 且都 >= 0" 这类不变量,不锁死具体数字。
    """

    def test_barracks_4_sum_is_count(self, session, terran_library) -> None:
        director = _make_director(session, terran_library)
        techlab, reactor = director._recommend_addon_mix("BARRACKS", 4)
        assert techlab >= 0
        assert reactor >= 0
        assert techlab + reactor == 4

    def test_barracks_4_has_at_least_1_techlab(self, session, terran_library) -> None:
        """兵营有 MARAUDER/GHOST 需 TechLab → techlab >= 1。"""
        director = _make_director(session, terran_library)
        techlab, _reactor = director._recommend_addon_mix("BARRACKS", 4)
        assert techlab >= 1, f"兵营 4bb 应至少 1 科技, got techlab={techlab}"

    def test_factory_3_sum_is_count(self, session, terran_library) -> None:
        director = _make_director(session, terran_library)
        techlab, reactor = director._recommend_addon_mix("FACTORY", 3)
        assert techlab >= 0
        assert reactor >= 0
        assert techlab + reactor == 3

    def test_factory_has_at_least_1_techlab(self, session, terran_library) -> None:
        """重工有 SIEGETANK/THOR 需 TechLab → techlab >= 1。"""
        director = _make_director(session, terran_library)
        techlab, _reactor = director._recommend_addon_mix("FACTORY", 3)
        assert techlab >= 1, f"重工应至少 1 科技, got techlab={techlab}"

    def test_starport_4_sum_is_count(self, session, terran_library) -> None:
        director = _make_director(session, terran_library)
        techlab, reactor = director._recommend_addon_mix("STARPORT", 4)
        assert techlab >= 0
        assert reactor >= 0
        assert techlab + reactor == 4

    def test_single_barracks_sum_is_1(self, session, terran_library) -> None:
        director = _make_director(session, terran_library)
        techlab, reactor = director._recommend_addon_mix("BARRACKS", 1)
        assert techlab + reactor == 1
        assert techlab >= 0
        assert reactor >= 0


# ---------------------------------------------------------------------------
# 3. _recommend_addon_mix — mock _unit_requires_techlab 走算法路径
# ---------------------------------------------------------------------------


class TestRecommendAddonMixWithMock:
    """mock _unit_requires_techlab + UNIT_TRAINED_FROM + board overlays 验三场景。"""

    def _make_director_with_overlays(
        self,
        session: GameSession,
        library: StrategyLibrary,
        unit_types: list[str],
    ) -> Director:
        from vibecraft.directives.models import ProductionItem, ProductionOverridePayload

        director = _make_director(session, library)
        # 注入 active ProductionOverride overlay
        for ut in unit_types:
            d = Directive(
                payload=ProductionOverridePayload(
                    items=[ProductionItem(unit_type=ut, count=10)],
                ),
                issued_at=0.0,
            )
            director.board.overlays.append(d)
        return director

    def test_pure_marine_build_techlab_1(self, session, terran_library) -> None:
        """纯枪兵 build(活跃 MARINE)→ MARINE 不需 TechLab → 默认兜底 1 科技其余双倍。"""
        # MARINE doesn't require techlab, so techlab_units is empty → default=1
        director = self._make_director_with_overlays(session, terran_library, ["MARINE"])

        # Mock UnitTypeId enum, UNIT_TRAINED_FROM, and _unit_requires_techlab
        class FakeUID:
            def __init__(self, name: str):
                self._name = name
                self.__members__ = {}

            def __eq__(self, other):
                return self._name == getattr(other, "_name", None)

            def __hash__(self):
                return hash(self._name)

        # Patch _unit_requires_techlab to return False for all (MARINE-like)
        with patch.object(Director, "_unit_requires_techlab", return_value=False):
            techlab, reactor = director._recommend_addon_mix("BARRACKS", 4)

        # With all units returning False for techlab, techlab_units is empty → default=1
        assert techlab == 1
        assert reactor == 3

    def test_bio_4bb_techlab_ge_2(self, session, terran_library) -> None:
        """bio build(MARINE+MARAUDER active) → MARAUDER 需 TechLab → techlab>=1.

        由于 UNIT_TRAINED_FROM SC2 不可用,退回 default=1.
        但 mock 场景下: 2 个 techlab 兵种(MARAUDER+GHOST) → techlab=2.
        """
        director = self._make_director_with_overlays(
            session, terran_library, ["MARINE", "MARAUDER", "GHOST"]
        )

        # Simulate SC2 available: BARRACKS produces MARINE/MARAUDER/GHOST/REAPER
        # MARAUDER and GHOST require techlab
        class FakeUnitTypeId:
            BARRACKS = "BARRACKS"
            MARINE = "MARINE"
            MARAUDER = "MARAUDER"
            GHOST = "GHOST"
            REAPER = "REAPER"
            __members__ = {
                "BARRACKS": "BARRACKS",
                "MARINE": "MARINE",
                "MARAUDER": "MARAUDER",
                "GHOST": "GHOST",
                "REAPER": "REAPER",
                **{k: k for k in Director._ADDON_REACTOR_UNITS},
            }

            def __class_getitem__(cls, item: str):
                return item  # just return the string

        fake_unit_trained_from = {
            "MARINE": {"BARRACKS"},
            "MARAUDER": {"BARRACKS"},
            "GHOST": {"BARRACKS"},
            "REAPER": {"BARRACKS"},
        }

        def fake_unit_requires_techlab(uid: object) -> bool:
            return uid in {"MARAUDER", "GHOST"}

        with (
            patch("sc2.ids.unit_typeid.UnitTypeId", FakeUnitTypeId),
            patch.dict(
                "sys.modules",
                {
                    "sc2": MagicMock(),
                    "sc2.ids": MagicMock(),
                    "sc2.ids.unit_typeid": MagicMock(UnitTypeId=FakeUnitTypeId),
                    "sc2.dicts": MagicMock(),
                    "sc2.dicts.unit_trained_from": MagicMock(
                        UNIT_TRAINED_FROM=fake_unit_trained_from
                    ),
                },
            ),
            patch.object(
                Director, "_unit_requires_techlab", side_effect=fake_unit_requires_techlab
            ),
        ):
            techlab, reactor = director._recommend_addon_mix("BARRACKS", 4)

        assert techlab >= 2, f"bio build 应 >=2 科技, got techlab={techlab}"
        assert techlab + reactor == 4

    def test_single_factory_tank(self, session, terran_library) -> None:
        """单 factory 坦克(SIEGETANK active) → SIEGETANK 需 TechLab → techlab=1."""
        director = self._make_director_with_overlays(session, terran_library, ["SIEGETANK"])

        fake_unit_trained_from = {
            "SIEGETANK": {"FACTORY"},
            "HELLION": {"FACTORY"},
            "WIDOWMINE": {"FACTORY"},
        }

        class FakeUnitTypeId:
            FACTORY = "FACTORY"
            SIEGETANK = "SIEGETANK"
            HELLION = "HELLION"
            WIDOWMINE = "WIDOWMINE"
            __members__ = {
                "FACTORY": "FACTORY",
                "SIEGETANK": "SIEGETANK",
                "HELLION": "HELLION",
                "WIDOWMINE": "WIDOWMINE",
                **{k: k for k in Director._ADDON_REACTOR_UNITS},
            }

            def __class_getitem__(cls, item: str):
                return item

        def fake_unit_requires_techlab(uid: object) -> bool:
            return uid == "SIEGETANK"

        with (
            patch.dict(
                "sys.modules",
                {
                    "sc2": MagicMock(),
                    "sc2.ids": MagicMock(),
                    "sc2.ids.unit_typeid": MagicMock(UnitTypeId=FakeUnitTypeId),
                    "sc2.dicts": MagicMock(),
                    "sc2.dicts.unit_trained_from": MagicMock(
                        UNIT_TRAINED_FROM=fake_unit_trained_from
                    ),
                },
            ),
            patch.object(
                Director, "_unit_requires_techlab", side_effect=fake_unit_requires_techlab
            ),
        ):
            techlab, reactor = director._recommend_addon_mix("FACTORY", 1)

        assert techlab == 1
        assert reactor == 0

    def test_existing_techlab_reduces_increment(self, session, terran_library) -> None:
        """场上已有同类 TechLab 挂件 → 推荐 techlab 增量相应减少(§5 减去已有挂件).

        bio build 需 2 个 techlab 兵种(MARAUDER+GHOST),但场上已有 3 个
        BarracksTechLab → 需求已满足 → 新 4bb 增量 techlab=0,全挂 reactor。
        """
        director = self._make_director_with_overlays(
            session, terran_library, ["MARINE", "MARAUDER", "GHOST"]
        )

        # mock 场上已有 3 个 BARRACKSTECHLAB
        class _FakeUnits:
            def __init__(self, n: int) -> None:
                self.amount = n

        fake_bot = MagicMock()
        fake_bot.structures.return_value = _FakeUnits(3)
        director._bot = fake_bot

        fake_unit_trained_from = {
            "MARINE": {"BARRACKS"},
            "MARAUDER": {"BARRACKS"},
            "GHOST": {"BARRACKS"},
            "REAPER": {"BARRACKS"},
        }

        class FakeUnitTypeId:
            __members__ = {
                "BARRACKS": "BARRACKS",
                "BARRACKSTECHLAB": "BARRACKSTECHLAB",
                "MARINE": "MARINE",
                "MARAUDER": "MARAUDER",
                "GHOST": "GHOST",
                "REAPER": "REAPER",
                **{k: k for k in Director._ADDON_REACTOR_UNITS},
            }

            def __class_getitem__(cls, item: str):
                return item

        def fake_unit_requires_techlab(uid: object) -> bool:
            return uid in {"MARAUDER", "GHOST"}

        with (
            patch.dict(
                "sys.modules",
                {
                    "sc2": MagicMock(),
                    "sc2.ids": MagicMock(),
                    "sc2.ids.unit_typeid": MagicMock(UnitTypeId=FakeUnitTypeId),
                    "sc2.dicts": MagicMock(),
                    "sc2.dicts.unit_trained_from": MagicMock(
                        UNIT_TRAINED_FROM=fake_unit_trained_from
                    ),
                },
            ),
            patch.object(
                Director, "_unit_requires_techlab", side_effect=fake_unit_requires_techlab
            ),
        ):
            techlab, reactor = director._recommend_addon_mix("BARRACKS", 4)

        # 需求 2 - 已有 3 = -1 → clamp 0;全 4 双倍
        assert techlab == 0, f"已有挂件满足需求, 增量应为 0, got techlab={techlab}"
        assert reactor == 4
        # 数的是 BARRACKSTECHLAB
        fake_bot.structures.assert_called_with("BARRACKSTECHLAB")

    def test_existing_techlab_partial_reduction(self, session, terran_library) -> None:
        """场上已有 1 个 techlab,需求 2 → 增量 techlab=1(2-1)。"""
        director = self._make_director_with_overlays(
            session, terran_library, ["MARINE", "MARAUDER", "GHOST"]
        )

        class _FakeUnits:
            def __init__(self, n: int) -> None:
                self.amount = n

        fake_bot = MagicMock()
        fake_bot.structures.return_value = _FakeUnits(1)
        director._bot = fake_bot

        fake_unit_trained_from = {
            "MARINE": {"BARRACKS"},
            "MARAUDER": {"BARRACKS"},
            "GHOST": {"BARRACKS"},
        }

        class FakeUnitTypeId:
            __members__ = {
                "BARRACKS": "BARRACKS",
                "BARRACKSTECHLAB": "BARRACKSTECHLAB",
                "MARINE": "MARINE",
                "MARAUDER": "MARAUDER",
                "GHOST": "GHOST",
                **{k: k for k in Director._ADDON_REACTOR_UNITS},
            }

            def __class_getitem__(cls, item: str):
                return item

        def fake_unit_requires_techlab(uid: object) -> bool:
            return uid in {"MARAUDER", "GHOST"}

        with (
            patch.dict(
                "sys.modules",
                {
                    "sc2": MagicMock(),
                    "sc2.ids": MagicMock(),
                    "sc2.ids.unit_typeid": MagicMock(UnitTypeId=FakeUnitTypeId),
                    "sc2.dicts": MagicMock(),
                    "sc2.dicts.unit_trained_from": MagicMock(
                        UNIT_TRAINED_FROM=fake_unit_trained_from
                    ),
                },
            ),
            patch.object(
                Director, "_unit_requires_techlab", side_effect=fake_unit_requires_techlab
            ),
        ):
            techlab, reactor = director._recommend_addon_mix("BARRACKS", 4)

        assert techlab == 1, f"需求 2 - 已有 1 = 1, got techlab={techlab}"
        assert reactor == 3


# ---------------------------------------------------------------------------
# 4. _maybe_build_addon_confirm — 触发 / 不触发
# ---------------------------------------------------------------------------


class TestMaybeBuildAddonConfirm:
    def test_triggers_for_voice_barracks_addon_not_decided(self, session, terran_library) -> None:
        """VOICE + BARRACKS + addon_decided=False → 返回 3 选项 ClarificationRequest。"""
        director = _make_director(session, terran_library, my_race="terran")
        d = _structure_directive("Barracks", delta=4, addon_decided=False, issued_by=IssuedBy.VOICE)
        result = director._maybe_build_addon_confirm([d])

        assert isinstance(result, ClarificationRequest)
        assert len(result.options) == 3
        labels = [o.label for o in result.options]
        assert "不挂附件" in labels
        assert "取消" in labels
        # 推荐选项含科技+双倍数字
        recommend_label = next(lb for lb in labels if "推荐" in lb)
        assert "科技" in recommend_label
        assert "双倍" in recommend_label

    def test_option_a_sets_addon_decided_true_no_addon_items(self, session, terran_library) -> None:
        """选项 a(不挂附件)的 directives 里 Barracks item addon_decided=True,无挂件 item。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Barracks", delta=4, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])

        assert result is not None
        opt_a = result.options[0]  # 不挂附件
        assert opt_a.label == "不挂附件"
        assert len(opt_a.directives) == 1
        payload = opt_a.directives[0].payload
        assert isinstance(payload, StructureOverridePayload)
        barracks_items = [i for i in payload.items if i.structure_type.upper() == "BARRACKS"]
        assert len(barracks_items) == 1
        assert barracks_items[0].addon_decided is True
        # 无挂件 item
        addon_items = [i for i in payload.items if i.structure_type.upper() != "BARRACKS"]
        assert len(addon_items) == 0

    def test_option_b_has_techlab_and_reactor_items(self, session, terran_library) -> None:
        """选项 b(推荐)的 directives 里包含 BarracksTechLab/BarracksReactor item(n>0 时)。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Barracks", delta=4, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])

        assert result is not None
        opt_b = next(o for o in result.options if "推荐" in o.label)
        assert len(opt_b.directives) == 1
        payload = opt_b.directives[0].payload
        assert isinstance(payload, StructureOverridePayload)
        types = {i.structure_type.upper() for i in payload.items}
        # 应含产能楼本体
        assert "BARRACKS" in types
        # Barracks item addon_decided=True
        bar_item = next(i for i in payload.items if i.structure_type.upper() == "BARRACKS")
        assert bar_item.addon_decided is True
        # 推荐应含至少一种挂件(默认 1 科技)
        addon_types = types - {"BARRACKS"}
        assert len(addon_types) >= 1  # 至少有 techlab 或 reactor

    def test_option_c_is_cancel_empty_directives(self, session, terran_library) -> None:
        """选项 c(取消)的 directives 为空列表。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Barracks", delta=4, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])

        assert result is not None
        opt_c = next(o for o in result.options if o.label == "取消")
        assert opt_c.directives == []

    def test_no_trigger_for_non_voice(self, session, terran_library) -> None:
        """非 VOICE 来源不触发弹窗。"""
        director = _make_director(session, terran_library)
        d = _structure_directive(
            "Barracks", delta=4, addon_decided=False, issued_by=IssuedBy.BOT_INTERNAL
        )
        result = director._maybe_build_addon_confirm([d])
        assert result is None

    def test_no_trigger_when_addon_decided_true(self, session, terran_library) -> None:
        """addon_decided=True 时不触发弹窗(玩家已决定)。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Barracks", delta=4, addon_decided=True)
        result = director._maybe_build_addon_confirm([d])
        assert result is None

    def test_no_trigger_for_non_production_building(self, session, terran_library) -> None:
        """非产能建筑(如 SupplyDepot)不触发弹窗。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("SupplyDepot", delta=2, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])
        assert result is None

    def test_no_trigger_for_non_terran_race(self, session, terran_library) -> None:
        """非人族种族不触发弹窗。"""
        director = _make_director(session, terran_library, my_race="protoss")
        d = _structure_directive("Barracks", delta=4, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])
        assert result is None

    def test_factory_triggers_confirm(self, session, terran_library) -> None:
        """重工(Factory)也触发弹窗。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Factory", delta=2, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])

        assert isinstance(result, ClarificationRequest)
        assert "重工" in result.question
        labels = [o.label for o in result.options]
        assert "不挂附件" in labels
        assert "取消" in labels

    def test_starport_triggers_confirm(self, session, terran_library) -> None:
        """机场(Starport)也触发弹窗。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Starport", delta=3, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])

        assert isinstance(result, ClarificationRequest)
        assert "机场" in result.question

    def test_question_contains_count(self, session, terran_library) -> None:
        """弹窗 question 里含数量信息。"""
        director = _make_director(session, terran_library)
        d = _structure_directive("Barracks", delta=4, addon_decided=False)
        result = director._maybe_build_addon_confirm([d])
        assert result is not None
        assert "4" in result.question
