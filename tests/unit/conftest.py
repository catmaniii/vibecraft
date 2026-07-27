"""共享 fake sharpy/sc2 注入 —— 防止各 test 文件各写一份。

Task 2 (test_protoss_facade_overrides) + Task 3 (test_vibecraft_zone_attack)
各自维护了一份 FakePoint2，行为不一致：
  - zone_attack: FakePoint2(tuple) 继承 tuple，支持 x/y property
  - facade:      FakePoint2 普通类，只存 _pt

统一到本 conftest，FakePoint2 一律用 tuple 子类（两边兼容）。
"""

from __future__ import annotations

import enum
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 统一 FakePoint2（tuple 子类 + x/y property）
# ---------------------------------------------------------------------------


class FakePoint2(tuple):
    """最小 Point2 stub：继承 tuple，支持 .x / .y 属性访问。

    zone_attack 需要 tuple 语义（Point2((x, y)) 可 index）；
    facade tests 不读 x/y，但 tuple 子类也兼容 _pt 存储模式。
    """

    def __new__(cls, pt: Any) -> FakePoint2:
        return super().__new__(cls, pt)

    @property
    def x(self) -> float:
        return self[0]

    @property
    def y(self) -> float:
        return self[1]


# ---------------------------------------------------------------------------
# Fixture A：轻量 —— zone_attack 用（sc2.position + sharpy.plans.tactics）
# ---------------------------------------------------------------------------

_ZONE_ATTACK_PREFIXES = (
    "sharpy",
    "sc2",
)


def _clean_zone_attack_mods() -> None:
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _ZONE_ATTACK_PREFIXES):
            del sys.modules[key]


def _inject_sharpy_for_zone_attack() -> None:
    """注入 sharpy zone attack test 所需的最小 fake sc2 / sharpy。"""

    # --- sc2.position ---
    for mod_name in ["sc2", "sc2.position"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)
    sys.modules["sc2.position"].Point2 = FakePoint2  # type: ignore[attr-defined]

    # --- sharpy.plans.tactics ---
    class FakeActBase:
        pass

    class FakePlanZoneAttack(FakeActBase):
        def _get_target(self) -> Any:
            return None

        def _should_attack(self, power: Any) -> bool:
            return False

        def _should_retreat(self, fight_center: Any, already_attacking: Any) -> Any:
            return None

    # 模拟真 sharpy AttackStatus enum(zone_attack.py line 36-44):
    # NotActive=0 / Attacking=2 / Retreat=10 / Withdraw=11
    class FakeAttackStatus(enum.IntEnum):
        NotActive = 0
        GatheringForAttack = 1
        Attacking = 2
        Retreat = 10
        Withdraw = 11

    for mod_name in [
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.plans.tactics",
        "sharpy.plans.tactics.zone_attack",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    sys.modules["sharpy.plans.acts"].ActBase = FakeActBase  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.tactics"].PlanZoneAttack = FakePlanZoneAttack  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.tactics.zone_attack"].PlanZoneAttack = FakePlanZoneAttack  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.tactics.zone_attack"].AttackStatus = FakeAttackStatus  # type: ignore[attr-defined]


@pytest.fixture()
def fake_sharpy_zone_attack_env() -> Any:
    """轻量 fake sharpy 环境（目前已无 test 直接使用，保留供未来 wrapper 测试复用）。

    每个 test 独立：setup → yield → teardown。
    """
    _clean_zone_attack_mods()
    _inject_sharpy_for_zone_attack()
    yield
    _clean_zone_attack_mods()


# ---------------------------------------------------------------------------
# Fixture B：重型 —— protoss bot / facade 用（完整 sharpy + KnowledgeBot）
# ---------------------------------------------------------------------------

_BOT_PREFIXES = (
    "sc2",
    "sharpy",
    "vibecraft.bot.auto_combat",
    "vibecraft.bot.sharpy_adapter",
    # 2026-05-23:tech_tree 在 module-level bind sc2.constants 等 dict,fake env
    # 后必须清掉 cached module,否则后续测试 lookup 用旧 dict(key 是 fake enum)。
    "vibecraft.bot.tech_tree",
)


def _clean_bot_mods() -> None:
    # ⚠️ 副作用(2026-06-07 踩过):del 真 sc2 模块后,后续 `import sc2...` 会**重建 enum 类**
    # (UnitTypeId/UpgradeId)。其它 test 文件若在 collection 期把 enum 绑到模块全局(如
    # `from sc2.ids.unit_typeid import UnitTypeId`),那个旧 enum 类 ≠ 重建后的新类 →
    # 跨源比较/字典查找失配(test_tech_progress_panel 的 chrono 检测就这么"全量跑假失败")。
    # 受影响的 test 文件自己加 autouse fixture 重绑 enum(见 test_tech_progress_panel.py);
    # **别**在这里全局 save-restore 真模块——试过,牵连重导连锁反破坏 41 个测试,已回退。
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _BOT_PREFIXES):
            del sys.modules[key]


def _inject_sharpy_for_bot() -> None:
    """注入 protoss/bot.py 顶层 import 所需的完整 fake sharpy / sc2。"""

    class FakeUnitTask(enum.IntEnum):
        Idle = 0
        Reserved = 8

    for mod_name in [
        "sharpy",
        "sharpy.knowledges",
        "sharpy.knowledges.knowledge_bot",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
        "sharpy.managers.core.roles.unit_task",
        "sharpy.managers.extensions",
        "sharpy.managers.extensions.build_order_manager",
        "sharpy.plans",
        "sharpy.plans.if_else",
        "sharpy.plans.acts",
        "sharpy.plans.acts.act_unit_once",
        "sharpy.plans.require",
        "sharpy.plans.require.supply",
        "sharpy.plans.require.custom_requirement",
        "sharpy.plans.require.require_base",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    sys.modules["sharpy.managers.core.roles.unit_task"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]
    sys.modules["sharpy.managers.core.roles"].UnitTask = FakeUnitTask  # type: ignore[attr-defined]

    class FakeBuildOrder:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeIfElse:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeActUnitOnce:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeRequireBase:
        pass

    class FakeRequireSupply:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeCustomRequirement:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    sys.modules["sharpy.plans"].BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    sys.modules["sharpy.plans"].IfElse = FakeIfElse  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.if_else"].IfElse = FakeIfElse  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.acts.act_unit_once"].ActUnitOnce = FakeActUnitOnce  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.require_base"].RequireBase = FakeRequireBase  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.supply"].RequireSupply = FakeRequireSupply  # type: ignore[attr-defined]
    sys.modules["sharpy.plans.require.custom_requirement"].CustomRequirement = FakeCustomRequirement  # type: ignore[attr-defined]

    class FakeKnowledge:
        def __init__(self) -> None:
            self.roles = MagicMock()
            self.unit_cache = MagicMock()

        def pre_start(self, *a: Any, **kw: Any) -> None:
            pass

        async def start(self) -> None:
            pass

        async def update(self, iteration: int) -> None:
            pass

        async def post_update(self) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            pass

        async def on_end(self, result: Any) -> None:
            pass

        def print(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeKnowledgeBot:
        """sharpy KnowledgeBot 极简 stub。"""

        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = FakeKnowledge()

        def create_plan(self) -> Any:
            return None

        async def on_start(self) -> None:
            pass

        async def on_step(self, iteration: int) -> None:
            pass

    sys.modules["sharpy.knowledges.knowledge_bot"].KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

    for mod_name in ["sc2", "sc2.position", "sc2.ids", "sc2.ids.unit_typeid"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = ModuleType(mod_name)

    # 统一 FakePoint2：tuple 子类（facade 不读 x/y，但 tuple 也兼容 _pt 语义）
    sys.modules["sc2.position"].Point2 = FakePoint2  # type: ignore[attr-defined]

    class FakeUnitTypeId:
        NEXUS = "NEXUS"
        # 神族常用 unit type（EXCLUDE_FROM_ARMY + _compute_stance 用）
        PROBE = "PROBE"
        OBSERVER = "OBSERVER"
        WARPPRISM = "WARPPRISM"
        GATEWAY = "GATEWAY"
        WARPGATE = "WARPGATE"
        CYBERNETICSCORE = "CYBERNETICSCORE"
        ASSIMILATOR = "ASSIMILATOR"
        PYLON = "PYLON"
        STALKER = "STALKER"
        # 虫族常用 unit type（EXCLUDE_FROM_ARMY 用）
        DRONE = "DRONE"
        OVERLORD = "OVERLORD"
        OVERSEER = "OVERSEER"
        # 人族常用 unit type（EXCLUDE_FROM_ARMY 用）
        SCV = "SCV"
        MULE = "MULE"

    sys.modules["sc2.ids.unit_typeid"].UnitTypeId = FakeUnitTypeId  # type: ignore[attr-defined]


@pytest.fixture()
def fake_sharpy_bot_env() -> Any:
    """重型 fake sharpy 环境，供 protoss bot / facade 相关 test 用。

    注入完整 sharpy（KnowledgeBot、BuildOrder、IfElse 等）+ sc2。
    每个 test 独立：setup → yield → teardown。
    """
    _clean_bot_mods()
    _inject_sharpy_for_bot()
    yield
    _clean_bot_mods()
