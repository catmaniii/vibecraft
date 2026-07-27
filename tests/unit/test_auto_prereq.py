"""2026-05-23 用户:依赖树自动补齐。

测试 Director._auto_build_prereqs_for:用户说"出隐刀",DT 缺 DARKSHRINE → 自动
emit 多个独立 structure_override directive(每个 1 item),按依赖顺序逐个完成。

2026-05-23 v2 改动:1 条多 item directive → 多条独立 directive。让 PWA 上每个
缺失建筑/科技都是一张独立任务卡,玩家清晰看到 chain 进度。

也覆盖:
- 已建好的 structure 不重复 emit
- 已 pending 的不重复
- _auto_prereq_emitted 防重复(同一 missing 第二次不再 emit)
- 走 _submit_directives 路径(append production_overrides,PWA command_cards 可见)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    DirectiveType,
    ProductionItem,
    ProductionOverridePayload,
)
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_mock_bot(
    have: dict[str, int] | None = None,
    pending: dict[str, float] | None = None,
    race: str = "Protoss",
) -> MagicMock:
    """构造 mock bot,have/pending 控制各 structure ready/pending 数量。

    key 用 enum name(UPPER):e.g. {"NEXUS":1, "GATEWAY":1}
    race: "Protoss" / "Zerg" / "Terran"(给 tech_tree 用)
    """
    from sc2.data import Race

    have = have or {"NEXUS": 1}
    pending = pending or {}

    bot = MagicMock()
    bot.time = 60.0
    bot.race = {"Protoss": Race.Protoss, "Zerg": Race.Zerg, "Terran": Race.Terran}[race]

    def _structures(type_id: object) -> MagicMock:
        m = MagicMock()
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        cnt = have.get(name, 0)
        m.amount = cnt
        ready_mock = MagicMock()
        ready_mock.__len__ = MagicMock(return_value=cnt)
        ready_mock.__bool__ = MagicMock(return_value=cnt > 0)
        ready_mock.exists = cnt > 0
        m.ready = ready_mock
        return m

    bot.structures = _structures

    def _already_pending(type_id: object) -> float:
        name = type_id.name if hasattr(type_id, "name") else str(type_id)
        return pending.get(name, 0.0)

    bot.already_pending = _already_pending
    bot.knowledge = MagicMock()
    bot.knowledge.expansion_zones = []
    return bot


def _make_director(session: GameSession, mock_bot: MagicMock) -> Director:
    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, bot=mock_bot)


def _make_production_override(unit_type: str, count: int = 1) -> Directive:
    payload = ProductionOverridePayload(
        items=[ProductionItem(unit_type=unit_type, count=count)],
    )
    return Directive(payload=payload, issued_at=60.0)


class TestArchonMerge:
    """2026-05-24 用户:"合白球"卡住。ARCHON 不能 train,要 2 DT/HT merge。
    director _exec_archon_item:有 DT pair → MORPH_ARCHON;无 → 优先 train DT。"""

    def _setup_bot_with_dts(self, dt_count: int, ht_count: int = 0):
        """造 bot 含 DARKSHRINE + N 个 DT + M 个 HT,units() 可查 DT/HT。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot(
            have={
                "NEXUS": 1,
                "GATEWAY": 1,
                "CYBERNETICSCORE": 1,
                "TWILIGHTCOUNCIL": 1,
                "DARKSHRINE": 1,
                "TEMPLARARCHIVES": 1,
            }
        )

        def _make_unit(tag: int, type_id):
            # UnitCommand assert unit.__class__.__name__ == "Unit",
            # MagicMock 不行 → 用 spec class 名为 Unit 的简单 class。
            class Unit:
                pass

            u = Unit()
            u.tag = tag
            u.type_id = type_id
            return u

        dts = [_make_unit(1000 + i, UnitTypeId.DARKTEMPLAR) for i in range(dt_count)]
        hts = [_make_unit(2000 + i, UnitTypeId.HIGHTEMPLAR) for i in range(ht_count)]

        def _units(t):
            class FakeUnits:
                def __init__(self, units):
                    self._units = list(units)
                    self.amount = len(self._units)

                def __iter__(self):
                    return iter(self._units)

                def __len__(self):
                    return len(self._units)

            m = MagicMock()
            if t == UnitTypeId.DARKTEMPLAR:
                m.ready = FakeUnits(dts)
            elif t == UnitTypeId.HIGHTEMPLAR:
                m.ready = FakeUnits(hts)
            elif t == UnitTypeId.ARCHON:
                m.ready = FakeUnits([])
            return m

        bot.units = _units
        bot.do = MagicMock()
        bot.train = MagicMock(return_value=0)
        return bot

    def test_two_dts_triggers_morph(self, session: GameSession) -> None:
        """2 DT 在场 → 调 MORPH_ARCHON 两次(python-sc2 combine 成一个 raw)。"""
        from sc2.ids.ability_id import AbilityId

        bot = self._setup_bot_with_dts(dt_count=2)
        director = _make_director(session, bot)
        d = _make_production_override("Archon", count=1)
        director._exec_production_override(d, d.payload)

        # bot.do 被调 2 次(每个 DT 一次 UnitCommand)
        assert bot.do.call_count == 2
        for call in bot.do.call_args_list:
            cmd = call.args[0]
            assert cmd.ability == AbilityId.MORPH_ARCHON

    def test_no_dt_trains_dt_first(self, session: GameSession) -> None:
        """0 DT 0 HT → 优先 train DT(不 train HT)。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = self._setup_bot_with_dts(dt_count=0, ht_count=0)
        bot.train = MagicMock(return_value=1)  # train 成功
        director = _make_director(session, bot)
        d = _make_production_override("Archon", count=1)
        director._exec_production_override(d, d.payload)

        # 应该 train DT,不 train HT
        bot.train.assert_called_once()
        assert bot.train.call_args.args[0] == UnitTypeId.DARKTEMPLAR

    def test_two_hts_triggers_morph_when_no_dt(self, session: GameSession) -> None:
        """0 DT + 2 HT → 用 HT pair merge(因为 DT pair 不可用)。"""
        from sc2.ids.ability_id import AbilityId

        bot = self._setup_bot_with_dts(dt_count=0, ht_count=2)
        director = _make_director(session, bot)
        d = _make_production_override("Archon", count=1)
        director._exec_production_override(d, d.payload)

        assert bot.do.call_count == 2
        for call in bot.do.call_args_list:
            assert call.args[0].ability == AbilityId.MORPH_ARCHON


class TestAutoPrereq:
    """用户:出 DT 自动补齐 VC → VD 链。"""

    def test_dt_with_only_nexus_emits_full_chain(self, session: GameSession) -> None:
        """只有 NEXUS,出 DT → emit 4 个独立 directive(GATEWAY/VC/VT/VD 各 1 张卡)。"""
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)
        d = _make_production_override("DarkTemplar", count=2)
        director._exec_production_override(d, d.payload)

        # 2026-05-23 v2:多个独立 directive,各 1 item(每个建筑一张 PWA 卡片)
        # 走 _submit_directives 路径 → append production_overrides → PWA 可见
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        structure_types = [d.payload.items[0].structure_type for d in auto]
        assert "GATEWAY" in structure_types
        assert "CYBERNETICSCORE" in structure_types
        assert "TWILIGHTCOUNCIL" in structure_types
        assert "DARKSHRINE" in structure_types
        # 每个 directive 只 1 item
        for d in auto:
            assert len(d.payload.items) == 1
            assert d.payload.type == DirectiveType.STRUCTURE_OVERRIDE

    def test_dt_with_gateway_skips_gateway(self, session: GameSession) -> None:
        """已有 GATEWAY,链中不应再 emit Gateway。"""
        bot = _make_mock_bot(have={"NEXUS": 1, "GATEWAY": 1})
        director = _make_director(session, bot)
        d = _make_production_override("DarkTemplar", count=1)
        director._exec_production_override(d, d.payload)

        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        structure_types = [d.payload.items[0].structure_type for d in auto]
        assert "GATEWAY" not in structure_types  # 已有,跳过
        assert "CYBERNETICSCORE" in structure_types  # 仍要补
        assert "DARKSHRINE" in structure_types

    def test_pending_structure_skipped(self, session: GameSession) -> None:
        """正在建的 structure(pending > 0)不重复 emit。"""
        bot = _make_mock_bot(
            have={"NEXUS": 1, "GATEWAY": 1},
            pending={"CYBERNETICSCORE": 1.0},
        )
        director = _make_director(session, bot)
        d = _make_production_override("DarkTemplar")
        director._exec_production_override(d, d.payload)

        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        structure_types = [d.payload.items[0].structure_type for d in auto]
        assert "CYBERNETICSCORE" not in structure_types  # pending,跳过
        assert "TWILIGHTCOUNCIL" in structure_types  # 仍缺

    def test_repeated_call_no_duplicate_emit(self, session: GameSession) -> None:
        """同 missing 第二次不再 emit(_auto_prereq_emitted 防重复)。

        DT chain = 4 个 structure → 第一次 emit 4 张卡,第二次 0 张(全 emit 过)。
        """
        bot = _make_mock_bot(have={"NEXUS": 1})
        director = _make_director(session, bot)
        d = _make_production_override("DarkTemplar")

        director._exec_production_override(d, d.payload)
        first_count = len(
            [
                d
                for d in director.production_overrides
                if d.source_text and d.source_text.startswith("auto_prereq:")
            ]
        )
        director._exec_production_override(d, d.payload)
        second_count = len(
            [
                d
                for d in director.production_overrides
                if d.source_text and d.source_text.startswith("auto_prereq:")
            ]
        )
        assert first_count == 4  # GATEWAY + CYBERNETICSCORE + TWILIGHTCOUNCIL + DARKSHRINE
        assert second_count == 4  # 没新增(防重复)

    def test_zealot_no_prereq_no_emit(self, session: GameSession) -> None:
        """Zealot 无 prereq(基础兵)→ 不 emit。"""
        bot = _make_mock_bot(have={"NEXUS": 1, "GATEWAY": 1})
        director = _make_director(session, bot)
        d = _make_production_override("Zealot")
        bot.train = MagicMock(return_value=0)
        bot.already_pending = lambda t: 0.0

        director._exec_production_override(d, d.payload)

        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        assert len(auto) == 0

    # ── 2026-05-23 用户:扩到三族 ────────────────────────────────────

    def test_zergling_emits_spawningpool(self, session: GameSession) -> None:
        """虫族:出小狗 → 自动补 SPAWNINGPOOL (1 张卡)。"""
        bot = _make_mock_bot(have={"HATCHERY": 1}, race="Zerg")
        director = _make_director(session, bot)
        d = _make_production_override("Zergling", count=6)
        bot.train = MagicMock(return_value=0)

        director._exec_production_override(d, d.payload)
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        types = [d.payload.items[0].structure_type for d in auto]
        assert types == ["SPAWNINGPOOL"]

    def test_baneling_emits_full_chain(self, session: GameSession) -> None:
        """虫族:出妖虫 → 自动补 SPAWNINGPOOL + BANELINGNEST (2 张卡)。"""
        bot = _make_mock_bot(have={"HATCHERY": 1}, race="Zerg")
        director = _make_director(session, bot)
        d = _make_production_override("Baneling", count=4)
        bot.train = MagicMock(return_value=0)

        director._exec_production_override(d, d.payload)
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        types = [d.payload.items[0].structure_type for d in auto]
        assert "SPAWNINGPOOL" in types
        assert "BANELINGNEST" in types

    def test_mutalisk_emits_lair_spire_chain(self, session: GameSession) -> None:
        """虫族:出飞龙 → 自动补 SPAWNINGPOOL + LAIR + SPIRE (3 张卡)。"""
        bot = _make_mock_bot(have={"HATCHERY": 1}, race="Zerg")
        director = _make_director(session, bot)
        d = _make_production_override("Mutalisk", count=3)
        bot.train = MagicMock(return_value=0)

        director._exec_production_override(d, d.payload)
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        types = [d.payload.items[0].structure_type for d in auto]
        assert "SPAWNINGPOOL" in types
        assert "LAIR" in types
        assert "SPIRE" in types

    def test_thor_emits_terran_chain(self, session: GameSession) -> None:
        """人族:出雷神 → 自动补 BARRACKS + FACTORY + ARMORY (3 张卡,按依赖顺序)。"""
        bot = _make_mock_bot(have={"COMMANDCENTER": 1}, race="Terran")
        director = _make_director(session, bot)
        d = _make_production_override("Thor", count=1)
        bot.train = MagicMock(return_value=0)

        director._exec_production_override(d, d.payload)
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        types = [d.payload.items[0].structure_type for d in auto]
        assert types == ["BARRACKS", "FACTORY", "ARMORY"]

    def test_carrier_emits_full_chain(self, session: GameSession) -> None:
        """神族:用户原话「我让出航母」→ 自动补 GATEWAY + CYBERNETICSCORE +
        STARGATE + FLEETBEACON (4 张独立卡片,按依赖顺序逐个完成)。"""
        bot = _make_mock_bot(have={"NEXUS": 1}, race="Protoss")
        director = _make_director(session, bot)
        d = _make_production_override("Carrier", count=1)
        bot.train = MagicMock(return_value=0)

        director._exec_production_override(d, d.payload)
        auto = [
            d
            for d in director.production_overrides
            if d.source_text and d.source_text.startswith("auto_prereq:")
        ]
        types = [d.payload.items[0].structure_type for d in auto]
        assert types == [
            "GATEWAY",
            "CYBERNETICSCORE",
            "STARGATE",
            "FLEETBEACON",
        ]
        # 每张卡独立 + priority=80
        for d in auto:
            assert len(d.payload.items) == 1
            assert d.priority == 80


class TestVikingNameNormalization:
    """2026-06-17 真局:玩家"出维京"production_override 解析成功但永不出兵。

    根因:别名 canonical 名 "Viking" → UnitTypeId["VIKING"]=1940 是不可训练的
    占位 enum(trained_from=None)→ bot.train 静默 no-op;真·可训练的飞行模式
    维京是 VIKINGFIGHTER(from STARPORT)。修:_resolve_unit_type_id 加
    VIKING→VIKINGFIGHTER 归一,且 prereq 检查/自动补建也用归一名。
    """

    def test_resolve_viking_maps_to_vikingfighter(self) -> None:
        from sc2.ids.unit_typeid import UnitTypeId

        # 大小写都归一到可训练的 VIKINGFIGHTER
        assert Director._resolve_unit_type_id("Viking") == UnitTypeId.VIKINGFIGHTER
        assert Director._resolve_unit_type_id("VIKING") == UnitTypeId.VIKINGFIGHTER
        # 其它单位不受影响
        assert Director._resolve_unit_type_id("Ghost") == UnitTypeId.GHOST
        assert Director._resolve_unit_type_id("Marine") == UnitTypeId.MARINE

    def test_viking_train_uses_vikingfighter_when_starport_ready(
        self, session: GameSession
    ) -> None:
        """有 Starport → train(VIKINGFIGHTER),不是占位 VIKING。"""
        from sc2.ids.unit_typeid import UnitTypeId

        bot = _make_mock_bot(
            have={"COMMANDCENTER": 1, "BARRACKS": 1, "FACTORY": 1, "STARPORT": 1},
            race="Terran",
        )

        class _Empty:
            ready = ()

        bot.units = lambda _t: _Empty()
        bot.train = MagicMock(return_value=4)
        director = _make_director(session, bot)
        d = _make_production_override("Viking", count=4)
        director._exec_production_override(d, d.payload)

        bot.train.assert_called_once()
        assert bot.train.call_args.args[0] == UnitTypeId.VIKINGFIGHTER

    def test_viking_without_starport_auto_builds_chain(self, session: GameSession) -> None:
        """缺 Starport → 归一后 prereq 检测到 STARPORT 链并自动补建。

        没归一的话占位 VIKING 无 prereq → 不会自动补机场(本测试守归一)。
        """
        bot = _make_mock_bot(have={"COMMANDCENTER": 1}, race="Terran")
        bot.train = MagicMock(return_value=0)
        director = _make_director(session, bot)
        d = _make_production_override("Viking", count=4)
        director._exec_production_override(d, d.payload)

        auto_types = [
            ad.payload.items[0].structure_type
            for ad in director.production_overrides
            if ad.source_text and ad.source_text.startswith("auto_prereq:")
        ]
        assert "STARPORT" in auto_types


class TestAddonPrereq:
    """2026-06-17 用户：① 出坦克但重工没挂 TechLab → 自动补 FACTORYTECHLAB。
    ② 挂件 helper 逻辑（is_addon / requires_techlab / addon 名映射）。"""

    def test_addon_helpers(self) -> None:
        from sc2.ids.unit_typeid import UnitTypeId

        assert Director._is_addon_type("FACTORYTECHLAB")
        assert Director._is_addon_type("BarracksReactor".upper())
        assert not Director._is_addon_type("FACTORY")
        assert Director._unit_requires_techlab(UnitTypeId.SIEGETANK)
        assert Director._unit_requires_techlab(UnitTypeId.MARAUDER)
        assert Director._unit_requires_techlab(UnitTypeId.BANSHEE)
        assert not Director._unit_requires_techlab(UnitTypeId.MARINE)

    def test_addon_name_for_unit(self, session: GameSession) -> None:
        from sc2.ids.unit_typeid import UnitTypeId

        director = _make_director(session, _make_mock_bot(race="Terran"))
        assert director._techlab_addon_name_for(UnitTypeId.SIEGETANK) == "FACTORYTECHLAB"
        assert director._techlab_addon_name_for(UnitTypeId.MARAUDER) == "BARRACKSTECHLAB"
        assert director._techlab_addon_name_for(UnitTypeId.BANSHEE) == "STARPORTTECHLAB"

    def test_tank_without_techlab_auto_emits_addon(self, session: GameSession) -> None:
        """有 Factory 但没挂 TechLab，出坦克 → 自动 emit FACTORYTECHLAB 挂件卡。"""
        bot = _make_mock_bot(have={"COMMANDCENTER": 1, "BARRACKS": 1, "FACTORY": 1}, race="Terran")
        bot.train = MagicMock(return_value=0)
        director = _make_director(session, bot)
        d = _make_production_override("SiegeTank", count=2)
        director._exec_production_override(d, d.payload)

        auto_addons = [
            ad.payload.items[0].structure_type
            for ad in director.production_overrides
            if ad.source_text and ad.source_text.startswith("auto_addon:")
        ]
        assert "FACTORYTECHLAB" in auto_addons
        # 坦克本身没 train（挂起等挂件）
        bot.train.assert_not_called()


class TestAddonRelocate:
    """2026-06-17 #543：挂件位被占 → 起飞→找空位→落下→再挂。验分支逻辑 + ability id。"""

    @staticmethod
    def _builder(is_flying: bool, add_on_tag: int = 0, is_idle: bool = True):
        from sc2.position import Point2

        calls: list = []

        class _B:
            def __call__(self, ability, *a):
                self.calls.append(("cmd", ability, a))

        b = _B()
        b.tag = 12345
        b.is_flying = is_flying
        b.is_idle = is_idle
        b.add_on_tag = add_on_tag
        b.position = Point2((50.0, 50.0))
        b.calls = calls
        b.build = lambda t: calls.append(("build", t))
        return b

    def _bot_with(self, builder, *, space: bool, relocate_spot: bool):
        """mock bot：父楼按落地/飞行变体进 structures；_has_addon_space 走 find_placement
        (SUPPLYDEPOT 空位检查)；_find_relocate_spot 走 can_place_single(网格扫描可落点)。"""
        from types import SimpleNamespace

        from sc2.ids.unit_typeid import UnitTypeId

        bot = MagicMock()

        def _structures(tid):
            # 飞行中 → 只在 FACTORYFLYING 里;落地 → 只在 FACTORY 里(贴近真实 game state)。
            if tid == UnitTypeId.FACTORYFLYING:
                lst = [builder] if builder.is_flying else []
            elif tid == UnitTypeId.FACTORY:
                lst = [] if builder.is_flying else [builder]
            else:
                lst = []
            return SimpleNamespace(ready=lst)

        bot.structures = _structures

        async def _fp(building, near, max_distance=0, random_alternative=True, addon_place=False):
            # 只服务 _has_addon_space 的 SUPPLYDEPOT 当前位空位检查。
            if building == UnitTypeId.SUPPLYDEPOT:
                return SimpleNamespace(x=2.0, y=2.0) if space else None
            return None

        bot.find_placement = _fp

        async def _cps(building, pos):
            # _find_relocate_spot 的双验(楼 + 右侧挂件)都走它;relocate_spot 控制能否找到落点。
            return relocate_spot

        bot.can_place_single = _cps
        return bot

    def test_no_space_lifts(self, session: GameSession) -> None:
        """落地+idle+挂件位被占(无空位)+有可落点 → 起飞 LIFT_FACTORY。"""
        import asyncio

        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId

        b = self._builder(is_flying=False)
        director = _make_director(session, self._bot_with(b, space=False, relocate_spot=True))
        issued = asyncio.run(
            director._build_addon_on_parent(UnitTypeId.FACTORYTECHLAB, "FACTORYTECHLAB")
        )
        assert issued is True
        assert any(c[0] == "cmd" and c[1] == AbilityId.LIFT_FACTORY for c in b.calls)
        assert not any(c[0] == "build" for c in b.calls)

    def test_flying_lands_at_spot(self, session: GameSession) -> None:
        """在飞 + 有带挂件空位的落点 → 落下 LAND_FACTORY @ spot。"""
        import asyncio

        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId

        b = self._builder(is_flying=True)
        director = _make_director(session, self._bot_with(b, space=False, relocate_spot=True))
        issued = asyncio.run(
            director._build_addon_on_parent(UnitTypeId.FACTORYTECHLAB, "FACTORYTECHLAB")
        )
        assert issued is True
        assert any(c[0] == "cmd" and c[1] == AbilityId.LAND_FACTORY for c in b.calls)

    def test_has_space_builds_addon(self, session: GameSession) -> None:
        """落地+idle+有挂件空位 → 直接 build(addon)，不起飞。"""
        import asyncio

        from sc2.ids.unit_typeid import UnitTypeId

        b = self._builder(is_flying=False)
        director = _make_director(session, self._bot_with(b, space=True, relocate_spot=True))
        issued = asyncio.run(
            director._build_addon_on_parent(UnitTypeId.FACTORYTECHLAB, "FACTORYTECHLAB")
        )
        assert issued is True
        assert any(c[0] == "build" and c[1] == UnitTypeId.FACTORYTECHLAB for c in b.calls)

    def test_no_space_no_spot_does_nothing(self, session: GameSession) -> None:
        """挂件位被占 + 也没有可落点 → 不瞎起飞（issued=False）。"""
        import asyncio

        from sc2.ids.unit_typeid import UnitTypeId

        b = self._builder(is_flying=False)
        director = _make_director(session, self._bot_with(b, space=False, relocate_spot=False))
        issued = asyncio.run(
            director._build_addon_on_parent(UnitTypeId.FACTORYTECHLAB, "FACTORYTECHLAB")
        )
        assert issued is False
        assert not any(c[0] == "cmd" for c in b.calls)
