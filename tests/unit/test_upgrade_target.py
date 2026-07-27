"""攻防升级目标等级功能单测（2026-07-07）。

覆盖：
1. Director._parse_upgrade — 15 族正确解析 + 非攻防升级 → (None, None)
2. Tech.execute 封顶门逻辑（mock knowledge）
   - target=0 → 全级跳过
   - target=2 → L3 跳 / L1 L2 过
   - target=None(auto) → 不拦
3. facade.set_upgrade_target 写/pop upgrade_targets（FakeFacade + FakeKnowledge mock）
4. Director.apply_macro_action dim="upgrade_target" 校验 + 写入 + 调 facade
5. Director._build_tech_progress target 字段（None / int）
6. facade Protocol audit（通过 test_facade_release_unit_role.py 覆盖；此处跑相同逻辑做冒烟）
7. sharpy patch audit（Tech.execute 进 PATCHED_METHODS，通过 test_sharpy_patch_audit.py）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# vendor/sharpy 加入 sys.path，测试才能 import sharpy.plans.acts.tech（sharpy 未装进 venv，
# 靠各测试文件手动加 vendor 路径；同 test_micro_ht_safe.py 的做法）。
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

from vibecraft.bot.director import Director
from vibecraft.bot.facade import BotState, FakeFacade
from vibecraft.logging_.session import GameSession, GameSessionConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_director(bot: Any = None) -> Director:
    facade = FakeFacade(state=BotState(game_time=10.0))
    parser = MagicMock()
    session = GameSession(GameSessionConfig(use_null_sinks=True))
    director = Director(facade=facade, parser=parser, session=session)
    director._bot = bot
    return director


# ---------------------------------------------------------------------------
# 1. _parse_upgrade
# ---------------------------------------------------------------------------


class TestParseUpgrade:
    """Director._parse_upgrade 解析 UpgradeId.name 的 15 族白名单覆盖。"""

    @pytest.mark.parametrize(
        "name, expected_family, expected_level",
        [
            # 神族 5 条
            ("PROTOSSGROUNDWEAPONSLEVEL1", "PROTOSSGROUNDWEAPONS", 1),
            ("PROTOSSGROUNDWEAPONSLEVEL3", "PROTOSSGROUNDWEAPONS", 3),
            ("PROTOSSGROUNDARMORSLEVEL2", "PROTOSSGROUNDARMORS", 2),
            ("PROTOSSSHIELDSLEVEL1", "PROTOSSSHIELDS", 1),
            ("PROTOSSAIRWEAPONSLEVEL2", "PROTOSSAIRWEAPONS", 2),
            ("PROTOSSAIRARMORSLEVEL3", "PROTOSSAIRARMORS", 3),
            # 虫族 5 条
            ("ZERGMELEEWEAPONSLEVEL1", "ZERGMELEEWEAPONS", 1),
            ("ZERGMISSILEWEAPONSLEVEL2", "ZERGMISSILEWEAPONS", 2),
            ("ZERGGROUNDARMORSLEVEL3", "ZERGGROUNDARMORS", 3),
            (
                "ZERGFLYERWEAPONSLEVEL1",
                "ZERGFLYERWEAPONS",
                1,
            ),  # must-fix：旧名 ZERGFLYERATTACK 是错误的
            ("ZERGFLYERARMORSLEVEL2", "ZERGFLYERARMORS", 2),
            # 人族 5 条
            ("TERRANINFANTRYWEAPONSLEVEL1", "TERRANINFANTRYWEAPONS", 1),
            ("TERRANINFANTRYARMORSLEVEL2", "TERRANINFANTRYARMORS", 2),
            ("TERRANVEHICLEWEAPONSLEVEL3", "TERRANVEHICLEWEAPONS", 3),
            ("TERRANSHIPWEAPONSLEVEL1", "TERRANSHIPWEAPONS", 1),
            ("TERRANVEHICLEANDSHIPARMORSLEVEL2", "TERRANVEHICLEANDSHIPARMORS", 2),
        ],
    )
    def test_attack_defense_families(
        self, name: str, expected_family: str, expected_level: int
    ) -> None:
        family, level = Director._parse_upgrade(name)
        assert family == expected_family
        assert level == expected_level

    @pytest.mark.parametrize(
        "name",
        [
            "BLINKTECH",
            "CHARGE",
            "PSISTORMTECH",
            "WARPGATERESEARCH",
            "STIMPACK",
            "SIEGETECH",
            "ZERGLINGATTACKSPEED",
            "BURROW",
            # 旧错误名（ZERGFLYERATTACK）已不在白名单，_parse_upgrade 返回 (None, None)
            "ZERGFLYERATTACKLEVEL1",
            "ZERGFLYERATTACKLEVEL2",
            "ZERGFLYERATTACKLEVEL3",
        ],
    )
    def test_non_attack_defense_returns_none(self, name: str) -> None:
        family, level = Director._parse_upgrade(name)
        assert family is None
        assert level is None


# ---------------------------------------------------------------------------
# 2. Tech.execute 封顶门逻辑（不运行真实 sharpy，mock 最小接口）
# ---------------------------------------------------------------------------


class _FakeKnowledge:
    """模拟 sharpy knowledge 最小接口供 Tech.execute 测试。"""

    def __init__(self, upgrade_targets: dict[str, int | None]) -> None:
        self.vibecraft = SimpleNamespace(upgrade_targets=upgrade_targets)
        self.version_manager = MagicMock()
        self.version_manager.disabled_upgrades = set()
        self.version_manager.moved_upgrades = {}

    def can_afford(self, upgrade_type: Any) -> bool:
        return True

    def reserve(self, minerals: Any, vespene: Any) -> None:
        pass


def _purge_fake_sharpy() -> None:
    """清掉 sys.modules 里的 sharpy 缓存，保证这里 import 到 vendor 里的真模块。

    2026-07-27 开源前修:多个测试文件(test_nydus_raid / test_phoenix_squad_act 等)会往
    sys.modules 注入 `ModuleType("sharpy.plans.acts")` 之类的空壳好让被测模块 import 得过。
    它们各自会在 teardown 清理自己创建的那批,但只要有一个残留,本文件的
    `from sharpy.plans.acts.tech import Tech` 就会拿到空壳里的东西 —— 本文件单跑绿、全量跑红
    (真机症状:封顶门"没生效",其实是被测的根本不是真 Tech)。
    **整包清掉重新导入**,不能只清空壳:`sharpy.plans.acts.tech` 这个真模块虽然有 `__file__`,
    但它在导入时把基类 `ActBase` 从当时的父包取走了 —— 若那一刻父包是空壳,`Tech` 就继承了假
    ActBase(没有 `enabled` 等属性),`execute()` 第一行 `self.enabled` 直接 AttributeError,被
    测试 runner 吞成 False,看起来就是"封顶门没生效"。清掉整个 sharpy 命名空间让它重新解析。
    """
    for name in [n for n in list(sys.modules) if n == "sharpy" or n.startswith("sharpy.")]:
        del sys.modules[name]


def _make_tech_act(upgrade_name: str, from_building: Any = None) -> Any:
    """构造 Tech act 实例并 mock 掉 sharpy 运行时依赖。"""
    _purge_fake_sharpy()
    # 动态 import（vendor 路径）
    from sc2.ids.upgrade_id import UpgradeId
    from sharpy.plans.acts.tech import Tech

    upg = UpgradeId[upgrade_name]

    # mock from_building（避免真实 UPGRADE_RESEARCHED_FROM 查表）
    if from_building is None:
        from sc2.ids.unit_typeid import UnitTypeId

        from_building = UnitTypeId.FORGE  # 占位，不影响门逻辑测试

    tech = Tech(upg, from_building=from_building)
    tech.enabled = True
    return tech


def _run_execute_with_knowledge(tech: Any, knowledge: _FakeKnowledge) -> bool:
    """同步跑 Tech.execute（绑定 knowledge + 最小 mock，返回 bool）。

    只测试"封顶门"路径：门在顶部，若触发直接 return True，不进到真实 builders 逻辑。
    若不触发封顶，让 execute 继续走 builders 路径（会 AttributeError，我们捕获并返回 False）。
    """
    tech.knowledge = knowledge

    # mock cache 让 builders 为空（绕过真实 sc2 units 查询）
    builders_mock = MagicMock()
    builders_mock.ready = MagicMock()
    builders_mock.ready.exists = False
    builders_mock.ready.idle = MagicMock()
    builders_mock.ready.idle.exists = False

    # mock already_pending_upgrade → 0（没在研究）
    tech.already_pending_upgrade = MagicMock(return_value=0)
    tech.cache = MagicMock()
    tech.cache.own = MagicMock(return_value=builders_mock)

    # from_buildings
    tech.from_buildings = {MagicMock()}

    # ai（reserve 需要）
    ai_mock = MagicMock()
    ai_mock.unit_tags_received_action = set()
    tech.ai = ai_mock

    try:
        # 2026-07-27 开源前修:原来用 `asyncio.get_event_loop().run_until_complete(...)`。
        # 3.11 起,若之前的异步测试已把当前 loop 关掉/清掉,这句自己就会抛 —— 而它恰好被下面的
        # `except Exception` 吞成 False,表现为"封顶门没生效"(本文件单跑绿、全量跑红的真凶)。
        # `asyncio.run` 每次自建新 loop,不依赖全局状态。
        return asyncio.run(tech.execute())
    except Exception:
        # 门未触发，走到了 builders 真实路径，但 mock 不完整，捕获后按"未完成"处理
        return False


class TestTechGate:
    """封顶门逻辑 — mock knowledge.vibecraft.upgrade_targets。"""

    def test_target_0_blocks_all_levels(self) -> None:
        """target=0 → L1/L2/L3 全部跳过（return True）。"""
        targets = {"PROTOSSGROUNDWEAPONS": 0}
        knowledge = _FakeKnowledge(targets)

        for level in (1, 2, 3):
            tech = _make_tech_act(f"PROTOSSGROUNDWEAPONSLEVEL{level}")
            result = _run_execute_with_knowledge(tech, knowledge)
            assert result is True, f"target=0, level={level} 应被跳过(return True)"

    def test_target_2_allows_l1_l2_blocks_l3(self) -> None:
        """target=2 → L1/L2 正常（不被门拦，返回 False=未完成）；L3 跳过（True）。"""
        targets = {"PROTOSSGROUNDWEAPONS": 2}
        knowledge = _FakeKnowledge(targets)

        # L1 → 门不触发（level 1 ≤ target 2），但 builders 空 → return False
        tech_l1 = _make_tech_act("PROTOSSGROUNDWEAPONSLEVEL1")
        result_l1 = _run_execute_with_knowledge(tech_l1, knowledge)
        assert result_l1 is False, "target=2, level=1 不该被封顶门拦"

        # L2 → 门不触发
        tech_l2 = _make_tech_act("PROTOSSGROUNDWEAPONSLEVEL2")
        result_l2 = _run_execute_with_knowledge(tech_l2, knowledge)
        assert result_l2 is False, "target=2, level=2 不该被封顶门拦"

        # L3 → 门触发（level 3 > target 2）→ True
        tech_l3 = _make_tech_act("PROTOSSGROUNDWEAPONSLEVEL3")
        result_l3 = _run_execute_with_knowledge(tech_l3, knowledge)
        assert result_l3 is True, "target=2, level=3 应被封顶门跳过"

    def test_target_none_auto_does_not_block(self) -> None:
        """target=None(auto, key 不存在) → 不拦任何级别。"""
        targets: dict[str, int | None] = {}  # 无 key = auto
        knowledge = _FakeKnowledge(targets)

        for level in (1, 2, 3):
            tech = _make_tech_act(f"PROTOSSGROUNDWEAPONSLEVEL{level}")
            result = _run_execute_with_knowledge(tech, knowledge)
            # 门不触发 → return False（builders 空）
            assert result is False, f"auto target, level={level} 不应被门拦"

    def test_non_attack_defense_upgrade_not_blocked(self) -> None:
        """非攻防升级（如 CHARGE）即使 family 名恰巧像攻防，也不被封顶门拦。"""
        # 给所有可能 family 设 target=0，确保非攻防升级不受影响
        from sharpy.plans.acts.tech import _VIBECRAFT_UPGRADE_CAP_FAMILIES

        targets = dict.fromkeys(_VIBECRAFT_UPGRADE_CAP_FAMILIES, 0)
        knowledge = _FakeKnowledge(targets)

        # CHARGE 不在白名单 → 门 parse 到 (None, None) → 不拦
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.ids.upgrade_id import UpgradeId
        from sharpy.plans.acts.tech import Tech

        tech = Tech(UpgradeId.CHARGE, from_building=UnitTypeId.TWILIGHTCOUNCIL)
        tech.enabled = True
        result = _run_execute_with_knowledge(tech, knowledge)
        assert result is False, "非攻防升级 CHARGE 不应被封顶门拦（应不拦）"

    def test_zerg_flyer_weapons_correct_enum(self) -> None:
        """验证虫族空攻用 ZERGFLYERWEAPONS（而非旧错名 ZERGFLYERATTACK）封顶有效。"""
        targets = {"ZERGFLYERWEAPONS": 0}
        knowledge = _FakeKnowledge(targets)

        from sc2.ids.unit_typeid import UnitTypeId

        tech = _make_tech_act(
            "ZERGFLYERWEAPONSLEVEL1",
            from_building=UnitTypeId.GREATERSPIRE,
        )
        result = _run_execute_with_knowledge(tech, knowledge)
        assert result is True, "ZERGFLYERWEAPONS target=0 应封顶 L1"


# ---------------------------------------------------------------------------
# 3. FakeFacade.set_upgrade_target — 写/pop upgrade_targets
# ---------------------------------------------------------------------------


class TestFakeFacadeSetUpgradeTarget:
    def test_set_level_writes_to_dict(self) -> None:
        facade = FakeFacade(state=BotState(game_time=0.0))
        facade.set_upgrade_target("PROTOSSGROUNDWEAPONS", 2)
        assert facade.upgrade_targets == {"PROTOSSGROUNDWEAPONS": 2}
        assert facade.upgrade_target_calls == [("PROTOSSGROUNDWEAPONS", 2)]

    def test_set_auto_pops_key(self) -> None:
        facade = FakeFacade(state=BotState(game_time=0.0))
        facade.set_upgrade_target("PROTOSSGROUNDWEAPONS", 1)
        facade.set_upgrade_target("PROTOSSGROUNDWEAPONS", None)  # auto
        assert "PROTOSSGROUNDWEAPONS" not in facade.upgrade_targets
        assert facade.upgrade_target_calls == [
            ("PROTOSSGROUNDWEAPONS", 1),
            ("PROTOSSGROUNDWEAPONS", None),
        ]

    def test_multiple_families(self) -> None:
        facade = FakeFacade(state=BotState(game_time=0.0))
        facade.set_upgrade_target("TERRANINFANTRYWEAPONS", 0)
        facade.set_upgrade_target("TERRANINFANTRYARMORS", 3)
        assert facade.upgrade_targets == {
            "TERRANINFANTRYWEAPONS": 0,
            "TERRANINFANTRYARMORS": 3,
        }


# ---------------------------------------------------------------------------
# 4. Director.apply_macro_action dim="upgrade_target"
# ---------------------------------------------------------------------------


class TestDirectorApplyUpgradeTarget:
    def test_valid_family_level_int(self) -> None:
        director = _make_director()
        director.apply_macro_action(
            "upgrade_target",
            {"family": "PROTOSSGROUNDWEAPONS", "level": 2},
            now=10.0,
        )
        assert director._upgrade_targets["PROTOSSGROUNDWEAPONS"] == 2
        # facade 也收到了调用
        assert isinstance(director.facade, FakeFacade)
        assert ("PROTOSSGROUNDWEAPONS", 2) in director.facade.upgrade_target_calls

    def test_level_auto_removes_key(self) -> None:
        director = _make_director()
        director._upgrade_targets["PROTOSSGROUNDWEAPONS"] = 1
        director.apply_macro_action(
            "upgrade_target",
            {"family": "PROTOSSGROUNDWEAPONS", "level": "auto"},
            now=10.0,
        )
        assert "PROTOSSGROUNDWEAPONS" not in director._upgrade_targets
        assert ("PROTOSSGROUNDWEAPONS", None) in director.facade.upgrade_target_calls  # type: ignore[union-attr]

    def test_level_zero(self) -> None:
        director = _make_director()
        director.apply_macro_action(
            "upgrade_target",
            {"family": "ZERGFLYERWEAPONS", "level": 0},
            now=10.0,
        )
        assert director._upgrade_targets["ZERGFLYERWEAPONS"] == 0

    def test_invalid_family_logs_warning(self, caplog: Any) -> None:
        import logging

        director = _make_director()
        with caplog.at_level(logging.WARNING):
            director.apply_macro_action(
                "upgrade_target",
                {"family": "NOT_A_REAL_FAMILY", "level": 1},
                now=10.0,
            )
        assert "NOT_A_REAL_FAMILY" in caplog.text or "白名单" in caplog.text

    def test_invalid_level_out_of_range_logs_warning(self, caplog: Any) -> None:
        import logging

        director = _make_director()
        with caplog.at_level(logging.WARNING):
            director.apply_macro_action(
                "upgrade_target",
                {"family": "PROTOSSGROUNDWEAPONS", "level": 99},
                now=10.0,
            )
        assert "99" in caplog.text or "超范围" in caplog.text

    def test_value_not_dict_logs_warning(self, caplog: Any) -> None:
        import logging

        director = _make_director()
        with caplog.at_level(logging.WARNING):
            director.apply_macro_action("upgrade_target", "bad_value", now=10.0)
        # 不应写入
        assert director._upgrade_targets == {}


# ---------------------------------------------------------------------------
# 5. _build_tech_progress target 字段
# ---------------------------------------------------------------------------


class TestBuildTechProgressTarget:
    """_build_tech_progress 的 leveled track 应携带 target 字段。"""

    def _make_bot_with_upgrade(self, upgrade_name: str) -> MagicMock:
        """构造一个 mock bot，state.upgrades 含给定升级，已完成 L1。"""
        from sc2.ids.upgrade_id import UpgradeId

        upg = UpgradeId[upgrade_name]
        bot = MagicMock()
        bot.state = MagicMock()
        bot.state.upgrades = frozenset({upg})
        bot.already_pending_upgrade = MagicMock(return_value=0)
        bot.structures = []
        return bot

    def test_target_none_when_auto(self) -> None:
        """未设 target → target 字段为 None（auto）。"""
        bot = self._make_bot_with_upgrade("PROTOSSGROUNDWEAPONSLEVEL1")
        director = _make_director(bot)
        # 不设 upgrade_targets

        tracks = director._build_tech_progress()
        leveled = [t for t in tracks if t.get("kind") == "leveled"]
        ground_wpn = next((t for t in leveled if t.get("track_en") == "PROTOSSGROUNDWEAPONS"), None)
        assert ground_wpn is not None, "升级完成后应有 PROTOSSGROUNDWEAPONS track"
        assert "target" in ground_wpn
        assert ground_wpn["target"] is None  # auto

    def test_target_int_when_set(self) -> None:
        """设 target=2 → target 字段为 2。"""
        bot = self._make_bot_with_upgrade("PROTOSSGROUNDWEAPONSLEVEL1")
        director = _make_director(bot)
        director._upgrade_targets["PROTOSSGROUNDWEAPONS"] = 2

        tracks = director._build_tech_progress()
        leveled = [t for t in tracks if t.get("kind") == "leveled"]
        ground_wpn = next((t for t in leveled if t.get("track_en") == "PROTOSSGROUNDWEAPONS"), None)
        assert ground_wpn is not None
        assert ground_wpn["target"] == 2

    def test_target_zero_when_set_zero(self) -> None:
        """设 target=0 → target 字段为 0（不视为 falsy/None）。"""
        bot = self._make_bot_with_upgrade("PROTOSSGROUNDWEAPONSLEVEL1")
        director = _make_director(bot)
        director._upgrade_targets["PROTOSSGROUNDWEAPONS"] = 0

        tracks = director._build_tech_progress()
        leveled = [t for t in tracks if t.get("kind") == "leveled"]
        ground_wpn = next((t for t in leveled if t.get("track_en") == "PROTOSSGROUNDWEAPONS"), None)
        assert ground_wpn is not None
        assert ground_wpn["target"] == 0  # 明确 0，不是 None


# ---------------------------------------------------------------------------
# 6. facade Protocol audit 冒烟（正式 audit 在 test_facade_release_unit_role.py）
# ---------------------------------------------------------------------------


def test_set_upgrade_target_in_sc2_facade_protocol() -> None:
    """Sc2Facade Protocol 必须声明 set_upgrade_target。"""
    import inspect

    from vibecraft.bot.facade import Sc2Facade

    methods = {name for name, _ in inspect.getmembers(Sc2Facade, inspect.isfunction)}
    assert "set_upgrade_target" in methods, "set_upgrade_target 未在 Sc2Facade Protocol 中声明"


def test_sharpy_facade_has_set_upgrade_target() -> None:
    """_SharpyFacadeBase 必须实现 set_upgrade_target（防 Protocol 不强制实现导致漏）。"""
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    impl = _make_sharpy_facade_base_class()
    assert hasattr(impl, "set_upgrade_target"), "_SharpyFacadeBase 未实现 set_upgrade_target"


# ---------------------------------------------------------------------------
# 7. _UPGRADE_CAP_FAMILIES 15 族覆盖 + UpgradeId 真机核对
# ---------------------------------------------------------------------------


def test_upgrade_cap_families_count() -> None:
    """Director._UPGRADE_CAP_FAMILIES 必须恰好 15 条。"""
    assert len(Director._UPGRADE_CAP_FAMILIES) == 15


def test_upgrade_cap_families_all_exist_in_upgrade_id() -> None:
    """每个 family 的 LEVEL1 在真实 UpgradeId enum 中必须存在（真机核对）。"""
    from sc2.ids.upgrade_id import UpgradeId

    missing = [
        fam for fam in Director._UPGRADE_CAP_FAMILIES if not hasattr(UpgradeId, f"{fam}LEVEL1")
    ]
    assert not missing, f"以下 family 的 LEVEL1 在 UpgradeId enum 中不存在: {missing}"


def test_zerg_flyer_weapons_exists_not_attack() -> None:
    """must-fix 1 核对：ZERGFLYERWEAPONSLEVEL1 存在，ZERGFLYERATTACKLEVEL1 不存在。"""
    from sc2.ids.upgrade_id import UpgradeId

    assert hasattr(UpgradeId, "ZERGFLYERWEAPONSLEVEL1"), "ZERGFLYERWEAPONSLEVEL1 应存在于 UpgradeId"
    assert not hasattr(UpgradeId, "ZERGFLYERATTACKLEVEL1"), (
        "ZERGFLYERATTACKLEVEL1 不应存在（旧错名）"
    )


def test_known_upgrade_names_no_zergflyerattack() -> None:
    """_KNOWN_UPGRADE_NAMES 不得含旧错名 ZERGFLYERATTACK。"""
    for name in Director._KNOWN_UPGRADE_NAMES:
        assert "ZERGFLYERATTACK" not in name, f"_KNOWN_UPGRADE_NAMES 仍含旧错名: {name!r}"
