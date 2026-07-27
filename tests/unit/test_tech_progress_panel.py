"""后端 tech_progress / production_buildings helper 单测。

测试 Director._build_tech_progress / _build_production_buildings
以 mock _bot 注入，验证返回字段结构与逻辑。

注意：sc2 包已安装，所以 _build_tech_progress 内 from sc2.ids.upgrade_id import UpgradeId
会使用真实 UpgradeId。测试里不需要 patch sys.modules；
already_pending_upgrade.side_effect 通过 uid.name 匹配真实 UpgradeId 名称。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sc2.ids.unit_typeid import UnitTypeId

from vibecraft.bot.director import Director
from vibecraft.bot.facade import BotState, FakeFacade
from vibecraft.logging_.session import GameSession, GameSessionConfig


@pytest.fixture(autouse=True)
def _sc2_enum_same_source() -> Any:
    """全量跑时,前面的 fake-env 测试(conftest fake_sharpy_bot_env)会 del 真 sc2 模块致其
    重导 → 本文件顶部 collection 时绑定的 `UnitTypeId`(旧 enum 类)与 `_build_tech_progress`
    运行时 lazy import 的 `UPGRADE_RESEARCHED_FROM` value(重导后的新 enum 类)身份不等 →
    chrono 检测 `bt in chrono_building_types` 失配(连 `==` 都 False,不同 enum 类)→ 3 条
    chrono 测试只在全量跑时假失败,单独跑全过。

    每个测试前把模块全局 `UnitTypeId` 重绑成**当前 sys.modules** 版本,与被测代码 lazy
    import 同源(诊断已证运行时 import 与 dict 同源)。单独跑时当前==顶部,无副作用。
    (2026-06-07 修预存测试隔离污染;根因是 conftest fake-env 删真 sc2 让 enum 重建。)
    """
    import sc2.ids.unit_typeid as _m  # 拿当前 sys.modules 的(被删则触发重导,与 dict 同源)

    globals()["UnitTypeId"] = _m.UnitTypeId
    yield


# ---------------------------------------------------------------------------
# 工具：构造最简 Director（只需要 _bot；其他依赖走 FakeFacade + stub session）
# ---------------------------------------------------------------------------


def _make_director(bot: Any = None) -> Director:
    facade = FakeFacade(state=BotState(game_time=10.0))
    parser = MagicMock()
    session = GameSession(GameSessionConfig(use_null_sinks=True))
    director = Director(facade=facade, parser=parser, session=session)
    director._bot = bot
    return director


def _make_upgrade_id(name: str, value: int) -> Any:
    """伪造一个 UpgradeId 枚举成员（.name / .value 属性）。"""
    upg = MagicMock()
    upg.name = name
    upg.value = value
    return upg


def _pending_by_name(**name_to_progress: float):
    """构造 already_pending_upgrade side_effect：按 uid.name 返回进度（未配则 0）。"""

    def _effect(uid: Any) -> float:
        n = uid.name if hasattr(uid, "name") else str(uid)
        return name_to_progress.get(n, 0.0)

    return _effect


# ===========================================================================
# _build_tech_progress 测试 — 新格式（kind / leveled / single / chrono）
# ===========================================================================


class TestBuildTechProgress:
    """_build_tech_progress 返回新格式（kind='leveled'|'single'）。

    sc2 包已安装，升级检查用真实 UpgradeId；
    通过 side_effect 按 uid.name 匹配控制 already_pending_upgrade 返回值。
    """

    # ------------------------------------------------------------------
    # 分级 track — leveled
    # ------------------------------------------------------------------

    def test_leveled_lv1_done_no_lv2_research(self) -> None:
        """神族+攻 lv1 完成，lv2 不在研究 → leveled level=1 status=done icon=LEVEL1。"""
        upg = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg])
        bot.already_pending_upgrade.side_effect = _pending_by_name()  # 全 0
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "leveled"
        assert item["track_en"] == "PROTOSSGROUNDWEAPONS"
        assert item["name_zh"] == "+攻"  # re.sub(r'\d','','+1攻')
        assert item["level"] == 1
        assert item["status"] == "done"
        assert item["researching_level"] is None
        assert item["icon_en"] == "PROTOSSGROUNDWEAPONSLEVEL1"
        assert item["chrono"] is False

    def test_leveled_lv1_done_lv2_researching(self) -> None:
        """神族+攻 lv1 完成，lv2 研究中 50% → level=1 status=researching progress=50 icon=LEVEL2。"""
        upg = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg])
        bot.already_pending_upgrade.side_effect = _pending_by_name(PROTOSSGROUNDWEAPONSLEVEL2=0.5)
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "leveled"
        assert item["level"] == 1
        assert item["status"] == "researching"
        assert item["researching_level"] == 2
        assert item["progress"] == 50
        assert item["icon_en"] == "PROTOSSGROUNDWEAPONSLEVEL2"
        assert item["chrono"] is False

    def test_leveled_lv2_done_no_lv3_research(self) -> None:
        """神族+攻 lv2 完成，lv3 不在研究 → level=2 status=done icon=LEVEL2。"""
        upg1 = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        upg2 = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL2", 79)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg1, upg2])
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["level"] == 2
        assert item["status"] == "done"
        assert item["icon_en"] == "PROTOSSGROUNDWEAPONSLEVEL2"

    def test_leveled_lv0_no_research_excluded(self) -> None:
        """level=0 且无研究 → 不进结果列表。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert result == []

    def test_leveled_lv0_researching_lv1_included(self) -> None:
        """level=0 但 lv1 在研究中 30% → 纳入（level=0 status=researching）。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name(PROTOSSGROUNDWEAPONSLEVEL1=0.3)
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["level"] == 0
        assert item["status"] == "researching"
        assert item["researching_level"] == 1
        assert item["progress"] == 30
        assert item["icon_en"] == "PROTOSSGROUNDWEAPONSLEVEL1"

    # ------------------------------------------------------------------
    # 非分级单项 — single
    # ------------------------------------------------------------------

    def test_single_done(self) -> None:
        """非分级 CHARGE done → kind=single status=done icon_en=CHARGE。"""
        upg = _make_upgrade_id("CHARGE", 2)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg])
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=("CHARGE",)):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "single"
        assert item["name_en"] == "CHARGE"
        assert item["name_zh"] == "冲锋"
        assert item["status"] == "done"
        assert item["progress"] == 100
        assert item["icon_en"] == "CHARGE"
        assert item["chrono"] is False

    def test_single_researching(self) -> None:
        """非分级 BLINKTECH 研究中 50% → kind=single status=researching progress=50。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name(BLINKTECH=0.5)
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=("BLINKTECH",)):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "single"
        assert item["status"] == "researching"
        assert item["progress"] == 50
        assert item["name_zh"] == "闪现"
        assert item["icon_en"] == "BLINKTECH"

    def test_single_not_started_excluded(self) -> None:
        """非分级 progress=0 且未完成 → 不进结果列表。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=("CHARGE",)):
            result = director._build_tech_progress()

        assert result == []

    # ------------------------------------------------------------------
    # chrono boost 检测
    # ------------------------------------------------------------------

    def _chrono_structure(self, type_id: UnitTypeId) -> Any:
        """伪造一个带 chrono buff + 在研究（有 order）的建筑。"""
        structure = MagicMock()
        structure.type_id = type_id
        structure.orders = [MagicMock()]
        structure.has_buff.return_value = True  # CHRONOBOOSTENERGYCOST buff 存在
        return structure

    def test_chrono_leveled_researching(self) -> None:
        """研究 +2 武器的 Forge 带 chrono buff → chrono=True（按研究建筑类型判定）。"""
        upg1 = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg1])
        bot.already_pending_upgrade.side_effect = _pending_by_name(PROTOSSGROUNDWEAPONSLEVEL2=0.6)
        bot.structures = [self._chrono_structure(UnitTypeId.FORGE)]

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["status"] == "researching"
        assert item["chrono"] is True

    def test_chrono_armor_regression(self) -> None:
        """回归：护甲 ARMOR(ability) vs ARMORS(枚举) 命名不一致，旧字符串匹配会漏检。

        研究 +1 护甲的 Forge 带 chrono → 必须 chrono=True。
        """
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name(PROTOSSGROUNDARMORSLEVEL1=0.5)
        bot.structures = [self._chrono_structure(UnitTypeId.FORGE)]

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDARMORSLEVEL1",
                "PROTOSSGROUNDARMORSLEVEL2",
                "PROTOSSGROUNDARMORSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        item = next(i for i in result if i["kind"] == "leveled")
        assert item["status"] == "researching"
        assert item["chrono"] is True

    def test_chrono_false_when_no_buff(self) -> None:
        """建筑没有 buff → chrono=False。"""
        upg1 = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg1])
        bot.already_pending_upgrade.side_effect = _pending_by_name(PROTOSSGROUNDWEAPONSLEVEL2=0.4)

        structure = MagicMock()
        structure.orders = []
        structure.has_buff.return_value = False
        bot.structures = [structure]

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
            ),
        ):
            result = director._build_tech_progress()

        assert len(result) == 1
        assert result[0]["chrono"] is False

    def test_chrono_single_researching(self) -> None:
        """非分级研究中 + chrono buff → single chrono=True。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name(CHARGE=0.7)

        # CHARGE 在 Twilight Council 研究；该建筑带 chrono buff
        bot.structures = [self._chrono_structure(UnitTypeId.TWILIGHTCOUNCIL)]

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=("CHARGE",)):
            result = director._build_tech_progress()

        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "single"
        assert item["chrono"] is True

    # ------------------------------------------------------------------
    # 边界 case
    # ------------------------------------------------------------------

    def test_done_no_duplicate(self) -> None:
        """done 升级不会重复出现（CHARGE done → 只一条 single done）。"""
        upg = _make_upgrade_id("CHARGE", 2)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg])
        bot.already_pending_upgrade.side_effect = _pending_by_name(CHARGE=0.8)  # 不应影响
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=("CHARGE",)):
            result = director._build_tech_progress()

        assert len(result) == 1
        assert result[0]["status"] == "done"

    def test_unknown_upgrade_name_uses_name_en_as_zh(self) -> None:
        """没有中文名的 done 升级（不在 _KNOWN_UPGRADE_NAMES）→ kind=single name_zh=name_en。"""
        upg = _make_upgrade_id("UNKNOWNUPGRADE_XYZ", 999)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg])
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = []

        director = _make_director(bot=bot)
        # _KNOWN_UPGRADE_NAMES 为空，升级仍在 done_names 里
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()

        # UNKNOWNUPGRADE_XYZ done + 不在 KNOWN → 作为 done extra single 输出
        assert len(result) == 1
        item = result[0]
        assert item["kind"] == "single"
        assert item["name_zh"] == "UNKNOWNUPGRADE_XYZ"
        assert item["status"] == "done"

    def test_bot_state_upgrades_exception_returns_empty(self) -> None:
        """bot.state.upgrades 抛异常 → 降级返回空（不崩）。"""
        bot = MagicMock()
        type(bot.state).upgrades = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no state"))
        )
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()

        assert result == []

    def test_multiple_tracks_and_singles(self) -> None:
        """多 track + 非分级混合场景：+攻 lv1 done + 冲锋 researching 75%。"""
        upg_atk1 = _make_upgrade_id("PROTOSSGROUNDWEAPONSLEVEL1", 78)
        bot = MagicMock()
        bot.state.upgrades = frozenset([upg_atk1])
        bot.already_pending_upgrade.side_effect = _pending_by_name(CHARGE=0.75)
        bot.structures = []

        director = _make_director(bot=bot)
        with patch.object(
            Director,
            "_KNOWN_UPGRADE_NAMES",
            new=(
                "PROTOSSGROUNDWEAPONSLEVEL1",
                "PROTOSSGROUNDWEAPONSLEVEL2",
                "PROTOSSGROUNDWEAPONSLEVEL3",
                "CHARGE",
            ),
        ):
            result = director._build_tech_progress()

        kinds = {item["kind"] for item in result}
        assert kinds == {"leveled", "single"}
        leveled = next(i for i in result if i["kind"] == "leveled")
        single = next(i for i in result if i["kind"] == "single")
        assert leveled["track_en"] == "PROTOSSGROUNDWEAPONS"
        assert leveled["level"] == 1
        assert leveled["status"] == "done"
        assert single["name_en"] == "CHARGE"
        assert single["status"] == "researching"
        assert single["progress"] == 75


# ===========================================================================
# 关键科技建筑（kind=building）测试
# ===========================================================================


class _FakeNotReady(list):
    """python-sc2 Units 的 not_ready 既能迭代又有 .amount —— 测试 fake 同时支持二者。"""

    @property
    def amount(self) -> int:
        return len(self)


class _FakeStructures:
    """模拟 python-sc2 Units：既可 bot.structures(tid) 调用，也可迭代（chrono 用）。

    by_name: {UnitTypeId.name: (ready_n, [not_ready_build_progress, ...])}
    """

    def __init__(self, by_name: dict[str, Any], all_list: Any = ()) -> None:
        self._by_name = by_name
        self._all = list(all_list)

    def __call__(self, tid: Any) -> Any:
        name = tid.name if hasattr(tid, "name") else str(tid)
        ready_n, nr_progress = self._by_name.get(name, (0, []))
        r = MagicMock()
        r.ready.amount = ready_n
        r.not_ready = _FakeNotReady(MagicMock(build_progress=p) for p in nr_progress)
        return r

    def __iter__(self) -> Any:
        return iter(self._all)


class TestBuildTechProgressBuildings:
    """_build_tech_progress 的关键科技建筑项：显示已建成数(count) + 建造中数(pending)。

    2026-06-08 用户:科技建筑也要像产能建筑一样显示"有几个、几个在建造中"。
    """

    def _make_bot(self, structures: _FakeStructures) -> Any:
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.side_effect = _pending_by_name()
        bot.structures = structures
        return bot

    def test_ready_building_done(self) -> None:
        """控制核心建成 2 个 → status=done count=2 pending=0。"""
        bot = self._make_bot(_FakeStructures({"CYBERNETICSCORE": (2, [])}))
        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()
        builds = [i for i in result if i["kind"] == "building"]
        assert len(builds) == 1
        item = builds[0]
        assert item["name_en"] == "CYBERNETICSCORE"
        assert item["name_zh"] == "BY"
        assert item["status"] == "done"
        assert item["icon_en"] == "CYBERNETICSCORE"
        assert item["count"] == 2  # 已建成数
        assert item["pending"] == 0  # 无在建

    def test_building_in_progress_percent(self) -> None:
        """议会在建 1 个 60% → status=building progress=60 count=0 pending=1。"""
        bot = self._make_bot(_FakeStructures({"TWILIGHTCOUNCIL": (0, [0.6])}))
        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()
        item = next(i for i in result if i["kind"] == "building")
        assert item["name_en"] == "TWILIGHTCOUNCIL"
        assert item["status"] == "building"
        assert item["progress"] == 60
        assert item["count"] == 0
        assert item["pending"] == 1

    def test_ready_takes_precedence_over_building(self) -> None:
        """已有 1 个建成 + 1 个在建 → status=done count=1 pending=1（既显示总数也显示在建）。"""
        bot = self._make_bot(_FakeStructures({"FORGE": (1, [0.3])}))
        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()
        item = next(i for i in result if i["kind"] == "building")
        assert item["status"] == "done"
        assert item["count"] == 1
        assert item["pending"] == 1

    def test_absent_building_not_shown(self) -> None:
        """没有任何科技建筑 → 不出现 building 项（有/没有靠存在与否体现）。"""
        bot = self._make_bot(_FakeStructures({}))
        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()
        assert [i for i in result if i["kind"] == "building"] == []

    def test_building_in_progress_takes_max(self) -> None:
        """多个在建取最接近完工的进度（0.2 / 0.8 → 80），pending=2。"""
        bot = self._make_bot(_FakeStructures({"ROBOTICSBAY": (0, [0.2, 0.8])}))
        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            result = director._build_tech_progress()
        item = next(i for i in result if i["kind"] == "building")
        assert item["status"] == "building"
        assert item["progress"] == 80
        assert item["count"] == 0
        assert item["pending"] == 2


# ===========================================================================
# _build_production_buildings 测试
# ===========================================================================


class TestBuildProductionBuildings:
    """_build_production_buildings 返回格式 + 逻辑覆盖。"""

    def _make_building_with_orders(self, ability_name: str, progress: float) -> Any:
        """伪造一个 Unit 实例（有 orders）。"""
        order = MagicMock()
        order.ability.name = ability_name
        order.progress = progress
        unit = MagicMock()
        unit.orders = [order]
        return unit

    def test_single_ready_building_no_queue(self) -> None:
        """1 个 NX 就绪，没在产 → count=1, pending=0, in_production=0, queue=[]。"""
        bot = MagicMock()

        unit = MagicMock()
        unit.orders = []
        ready_mock = MagicMock()
        ready_mock.amount = 1
        ready_mock.__iter__ = MagicMock(return_value=iter([unit]))
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 0
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        # 用真 UnitTypeId 枚举(bot.structures mock 忽略参数)。不要 patch.dict sys.modules
        # 替换整个模块 —— from-import 会把 mock 绑到父包 sc2.ids.unit_typeid 属性上,
        # patch.dict 只还原 sys.modules 字典、不还原父包属性 → 泄漏污染别的测试文件
        # (曾让 test_structure_override_exec 的 grid-path 测试在同跑时失败)。
        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("NEXUS",)):
            result = director._build_production_buildings()

        assert len(result) == 1
        item = result[0]
        assert item["name_en"] == "NEXUS"
        assert item["name_zh"] == "NX"
        assert item["count"] == 1
        assert item["pending"] == 0
        assert item["in_production"] == 0
        assert item["queue"] == []

    def test_under_construction_only(self) -> None:
        """0 ready + 2 建造中 → 仍纳入，count=0, pending=2。"""
        bot = MagicMock()
        ready_mock = MagicMock()
        ready_mock.amount = 0
        ready_mock.__iter__ = MagicMock(return_value=iter([]))
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 2
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("GATEWAY",)):
            result = director._build_production_buildings()

        assert len(result) == 1
        item = result[0]
        assert item["count"] == 0
        assert item["pending"] == 2

    def test_gateway_with_queue(self) -> None:
        """1 个 BG，在产追猎（50%）→ in_production=1, queue 含进度。"""
        bot = MagicMock()

        unit = self._make_building_with_orders("TRAIN_STALKER", 0.5)
        ready_mock = MagicMock()
        ready_mock.amount = 1
        ready_mock.__iter__ = MagicMock(return_value=iter([unit]))
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 0
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("GATEWAY",)):
            result = director._build_production_buildings()

        assert len(result) == 1
        item = result[0]
        assert item["in_production"] == 1
        assert len(item["queue"]) == 1
        assert item["queue"][0]["unit"] == "TRAIN_STALKER"
        assert item["queue"][0]["progress"] == 50

    def test_zero_ready_excluded(self) -> None:
        """ready=0 且 not_ready=0 → 不进结果列表。"""
        bot = MagicMock()
        ready_mock = MagicMock()
        ready_mock.amount = 0
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 0
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("STARGATE",)):
            result = director._build_production_buildings()

        assert result == []

    def test_structures_exception_skips_building(self) -> None:
        """bot.structures() 抛异常 → 跳过该建筑类型，不崩。"""
        bot = MagicMock()
        bot.structures.side_effect = RuntimeError("no structures")

        director = _make_director(bot=bot)

        with patch(
            "vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("ROBOTICSFACILITY",)
        ):
            result = director._build_production_buildings()

        assert result == []

    def _building(self, *, has_techlab: bool = False, has_reactor: bool = False) -> Any:
        """伪造一个已就绪建筑，显式给挂件布尔（MagicMock 默认 truthy 会污染挂件统计）。"""
        unit = MagicMock()
        unit.orders = []
        unit.has_techlab = has_techlab
        unit.has_reactor = has_reactor
        return unit

    def test_addon_breakdown(self) -> None:
        """3 兵营：1 没挂件 / 1 科技 / 1 双倍 → addons={none:1, techlab:1, reactor:1}。"""
        bot = MagicMock()
        units = [
            self._building(),  # none
            self._building(has_techlab=True),  # techlab
            self._building(has_reactor=True),  # reactor
        ]
        ready_mock = MagicMock()
        ready_mock.amount = 3
        ready_mock.__iter__ = MagicMock(return_value=iter(units))
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 0
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("BARRACKS",)):
            result = director._build_production_buildings()

        assert len(result) == 1
        assert result[0]["addons"] == {"none": 1, "techlab": 1, "reactor": 1}

    def test_addon_reactor_priority_over_techlab(self) -> None:
        """同时 has_techlab + has_reactor（异常态）→ 记为 reactor（先判 reactor）。"""
        bot = MagicMock()
        units = [self._building(has_techlab=True, has_reactor=True)]
        ready_mock = MagicMock()
        ready_mock.amount = 1
        ready_mock.__iter__ = MagicMock(return_value=iter(units))
        buildings_mock = MagicMock()
        buildings_mock.ready = ready_mock
        buildings_mock.not_ready.amount = 0
        bot.structures.return_value = buildings_mock

        director = _make_director(bot=bot)

        with patch("vibecraft.bot.director.Director._PRODUCTION_BUILDING_NAMES", new=("FACTORY",)):
            result = director._build_production_buildings()

        assert result[0]["addons"] == {"none": 0, "techlab": 0, "reactor": 1}


# ===========================================================================
# build_snapshot 透传验证（_bot=None 时不含字段；_bot 有时含字段）
# ===========================================================================


class TestSnapshotTechFields:
    """build_snapshot 根据 _bot 决定是否含 tech_progress / production_buildings。"""

    def test_no_bot_no_tech_fields(self) -> None:
        """_bot=None → snapshot 不含 tech_progress / production_buildings 字段。"""
        director = _make_director(bot=None)
        snap = director.build_snapshot(now=1.0)
        assert "tech_progress" not in snap
        assert "production_buildings" not in snap

    def test_bot_raises_gracefully_omits_field(self) -> None:
        """_bot 存在但 helper 抛异常 → snapshot 不含该字段（不崩）。"""
        bot = MagicMock()
        bot.state.upgrades = frozenset()
        bot.already_pending_upgrade.return_value = 0.0
        bot.structures.side_effect = RuntimeError("no game")

        director = _make_director(bot=bot)
        with patch.object(Director, "_KNOWN_UPGRADE_NAMES", new=()):
            snap = director.build_snapshot(now=2.0)

        assert "command_cards" in snap  # 基本结构仍在


# ===========================================================================
# _build_army_units 测试
# ===========================================================================


def _make_units_mock(ready_amount: int) -> Any:
    """伪造 bot.units(tid).ready.amount 返回值。"""
    ready = MagicMock()
    ready.amount = ready_amount
    units = MagicMock()
    units.ready = ready
    return units


class TestBuildArmyUnits:
    """_build_army_units 返回格式 + 逻辑覆盖。"""

    def test_count_and_pending_included(self) -> None:
        """count>0 且 pending>0 的兵种都纳入，字段正确。"""
        bot = MagicMock()

        def _units_side_effect(tid: Any) -> Any:
            name = tid.name if hasattr(tid, "name") else str(tid)
            if name == "ZEALOT":
                return _make_units_mock(4)
            return _make_units_mock(0)

        bot.units.side_effect = _units_side_effect

        def _pending_side_effect(tid: Any) -> int:
            name = tid.name if hasattr(tid, "name") else str(tid)
            return 2 if name == "STALKER" else 0

        bot.already_pending.side_effect = _pending_side_effect

        director = _make_director(bot=bot)
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("ZEALOT", "STALKER")):
            result = director._build_army_units()

        assert len(result) == 2
        zealot = next(r for r in result if r["name_en"] == "ZEALOT")
        stalker = next(r for r in result if r["name_en"] == "STALKER")
        assert zealot["count"] == 4
        assert zealot["pending"] == 0
        assert zealot["name_zh"] == "狂热者"  # 面板用官方正式名(2026-06-04)
        assert stalker["count"] == 0
        assert stalker["pending"] == 2
        assert stalker["name_zh"] == "追猎者"

    def test_zero_count_and_pending_excluded(self) -> None:
        """count=0 且 pending=0 → 不进结果列表。"""
        bot = MagicMock()
        bot.units.return_value = _make_units_mock(0)
        bot.already_pending.return_value = 0

        director = _make_director(bot=bot)
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("ZEALOT", "STALKER")):
            result = director._build_army_units()

        assert result == []

    def test_worker_first_ordering(self) -> None:
        """工人排在最前（_ARMY_UNIT_NAMES 顺序保持）。"""
        bot = MagicMock()

        def _units_side_effect(tid: Any) -> Any:
            return _make_units_mock(1)  # 全部 count=1

        bot.units.side_effect = _units_side_effect
        bot.already_pending.return_value = 0

        director = _make_director(bot=bot)
        # PROBE（工人）在 ZEALOT 前
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("PROBE", "ZEALOT", "STALKER")):
            result = director._build_army_units()

        assert len(result) == 3
        assert result[0]["name_en"] == "PROBE"
        assert result[0]["name_zh"] == "探机"

    def test_units_exception_skips_unit(self) -> None:
        """bot.units() 抛异常 → 跳过该兵种，其他正常。"""
        bot = MagicMock()

        def _units_side_effect(tid: Any) -> Any:
            name = tid.name if hasattr(tid, "name") else str(tid)
            if name == "ZEALOT":
                raise RuntimeError("no units")
            return _make_units_mock(3)

        bot.units.side_effect = _units_side_effect
        bot.already_pending.return_value = 0

        director = _make_director(bot=bot)
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("ZEALOT", "STALKER")):
            result = director._build_army_units()

        assert len(result) == 1
        assert result[0]["name_en"] == "STALKER"

    def test_no_bot_returns_empty(self) -> None:
        """_bot=None 时 build_snapshot 不含 army_units 字段。"""
        director = _make_director(bot=None)
        snap = director.build_snapshot(now=1.0)
        assert "army_units" not in snap

    def test_zerg_worker_zh_name(self) -> None:
        """虫族工蜂（DRONE）面板用官方正式名 = 工蜂（2026-06-04）。"""
        bot = MagicMock()
        bot.units.return_value = _make_units_mock(12)
        bot.already_pending.return_value = 0

        director = _make_director(bot=bot)
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("DRONE",)):
            result = director._build_army_units()

        assert len(result) == 1
        assert result[0]["name_zh"] == "工蜂"

    def test_terran_worker_zh_name(self) -> None:
        """人族 SCV 面板名 = SCV（官方名，2026-06-04）。"""
        bot = MagicMock()
        bot.units.return_value = _make_units_mock(14)
        bot.already_pending.return_value = 0

        director = _make_director(bot=bot)
        with patch.object(Director, "_ARMY_UNIT_NAMES", new=("SCV",)):
            result = director._build_army_units()

        assert len(result) == 1
        assert result[0]["name_zh"] == "SCV"
