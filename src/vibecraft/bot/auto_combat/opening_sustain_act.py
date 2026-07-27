"""OpeningSustainAct: opening 完成超时后启动的 race-default macro act。

跟当前 opening plan 的 tactics 段并行跑(BuildOrder children 各自 execute),
保留 plan 战术性格,只放开经济 cap:
- 农民数 → 80
- 扩张数 → 4-5 矿
- 主兵种 cap → 大数

触发条件: knowledge.vibecraft.sustain_uncap_active == True
(Director 在 opening_completed_signaled + 120s 后 set)。

不冲突 / 不切 plan: Director 已切到 persistent doctrine 时不 trigger
(see director.py opening sustain uncap check)。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALID_RACES = frozenset({"PROTOSS", "ZERG", "TERRAN"})

# build-aware sustain（2026-06-15）：产能建筑 income-matched 起点（GA 后续精调）。
_MASS_PRODUCER_TARGET = 8  # 主产线（mass 兵种的生产建筑）目标座数
_AIR_MASS_PRODUCER_TARGET = 4  # 空军主产线（STARGATE）：兵极贵 + 气瓶颈，8 座必空转/气浮 → 降到 4
_SUPPORT_PRODUCER_TARGET = 2  # 支援产线（cap/ratio/light 兵种）目标座数
_BIG = 9999  # mass：to_count 给大数，受人口/资源/建筑自然限制

# 农民 cap 常量（2026-07-10 worker saturation floor 设计 同源）：虫族农民与军队抢同一
# 200 人口池，80 drone 占满人口没空间出兵（roach_hydra 实测：75drone+28蟑+12刺=200，
# larva 堆 95、矿气全囤）→ 封 66（≈4 矿满采）。神/人 80 ≈ 满饱和无冲突。
# WorkerSaturationFloorAct（worker_saturation_floor.py）的 drone_budget 直接复用这两个
# 常量，不各写一份。
ZERG_WORKER_CAP = 66
NON_ZERG_WORKER_CAP = 80

# 不该被 GridBuilding 当"产能楼"扩建的 producer（townhall：会误盖基地/落位错）。
# 这类兵（如 MOTHERSHIP 从 NEXUS）只续兵不扩楼 —— ptarget 置 0（跳过 GridBuilding）。
_NO_GRID_PRODUCERS = frozenset(
    {"NEXUS", "COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS", "HATCHERY", "LAIR", "HIVE"}
)


# 虫族**科技楼前置**：兵能孵之前必须先有这座楼（GridBuilding 确保存在，只需 1 座）。
# 注意：这是**前置依赖**，不是 ActUnit 的 from_building —— 兵真正从 LARVA/ZERGLING/ROACH 孵
# （见 UNIT_TRAINED_FROM）。曾错把科技楼当 from_building → ActUnit(ROACH, ROACHWARREN) →
# roachwarren.train(ROACH) 无效 → 虫族 sustain 永不出兵、开局后全囤钱（2026-06-15 真局定位）。
_ZERG_TECH_PREREQ: dict[str, str] = {
    "ZERGLING": "SPAWNINGPOOL",
    "ROACH": "ROACHWARREN",
    "HYDRALISK": "HYDRALISKDEN",
    "MUTALISK": "SPIRE",
    "BANELING": "BANELINGNEST",
    "RAVAGER": "ROACHWARREN",
}


def plan_from_core_units(
    core_units: list[Any],
) -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    """纯函数（可单测）：core_units → (产能建筑名→目标座数, [(兵种名, 生产/孵化来源名, to_count)])。

    **ActUnit 的 from_building = 单位真正训练/孵化的来源**（UNIT_TRAINED_FROM），三族统一：
    - 神/人：训练建筑本身（GATEWAY/BARRACKS…），mass 主产线 GridBuilding 扩到 8。
    - 虫族：LARVA（蟑/狗/刺/飞龙）/ ZERGLING（爆虫）/ ROACH（飞蛇）—— **不是科技楼**。
      科技楼是**前置依赖**（_ZERG_TECH_PREREQ），GridBuilding 确保 1 座存在即可；larva 扩张
      靠 hatch+注卵（在 macro）。policy→to_count 同神/人。
    """
    from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
    from sc2.ids.unit_typeid import UnitTypeId

    producer_targets: dict[str, int] = {}
    unit_specs: list[tuple[str, str, int]] = []
    for cu in core_units:
        ut = getattr(UnitTypeId, str(cu.unit).upper(), None)
        if ut is None:
            continue
        is_zerg = cu.unit.upper() in _ZERG_TECH_PREREQ
        # from_building = 真正的孵化/训练来源（LARVA/ZERGLING/ROACH/GATEWAY/BARRACKS…）
        producers = UNIT_TRAINED_FROM.get(ut, set())
        # 神族 gateway 兵：UNIT_TRAINED_FROM 给 {GATEWAY, WARPGATE}（set 无序）。显式选 GATEWAY ——
        # GridBuilding 只能造 GATEWAY（WARPGATE 是折跃研究后 morph，不能直接造）；真正出兵走
        # ProtossUnit，它在折跃完成后自动切 warp-in。曾因 set 乱序选到 WARPGATE → GridBuilding(WARPGATE)
        # 无效 + ActUnit(STALKER, GATEWAY) 折跃后 builders 空 → 神族 gateway 系 sustain 卡死（2026-06-16 定位）。
        if UnitTypeId.GATEWAY in producers:
            producer = UnitTypeId.GATEWAY
        else:
            producer = next(
                (p for p in producers if "REACTOR" not in p.name and "TECHLAB" not in p.name), None
            )
        if producer is None:
            continue
        policy = cu.policy
        if policy == "player":
            continue  # auto 不出，玩家指令控制
        # 空军主产线（STARGATE）降档：兵贵 + 气瓶颈，8 座会空转/气浮 → 4
        mass_ptarget = (
            _AIR_MASS_PRODUCER_TARGET if producer.name == "STARGATE" else _MASS_PRODUCER_TARGET
        )
        if policy == "mass":
            cnt, ptarget = _BIG, mass_ptarget
        elif policy == "cap":
            cnt, ptarget = int(cu.value or 3), _SUPPORT_PRODUCER_TARGET
        elif policy == "light":
            cnt, ptarget = int(cu.value or 4), _SUPPORT_PRODUCER_TARGET
        elif policy == "ratio":
            cnt = 12 if cu.per == "bio" else 20  # v1 静态近似；动态 ratio act 留后
            ptarget = _SUPPORT_PRODUCER_TARGET
        else:
            continue
        if is_zerg:
            # 产能建筑 = 科技楼前置（只确保 1 座；larva 扩张靠 hatch+注卵，不 GridBuilding 扩科技楼）。
            tech = getattr(UnitTypeId, _ZERG_TECH_PREREQ[cu.unit.upper()], None)
            if tech is not None:
                producer_targets[tech.name] = max(producer_targets.get(tech.name, 0), 1)
        elif producer.name not in _NO_GRID_PRODUCERS:
            # 神/人：producer 本身就是训练建筑，按 income 扩到 ptarget 座。
            # townhall 类（MOTHERSHIP 从 NEXUS 等）不 GridBuilding 扩楼（会误盖基地），只续兵。
            producer_targets[producer.name] = max(producer_targets.get(producer.name, 0), ptarget)
        unit_specs.append((ut.name, producer.name, cnt))
    return producer_targets, unit_specs


# sharpy ActBase: 在 vendor/sharpy 可用时继承真实基类,否则 fallback object。
# BuildOrder.merge_to_act 需要 isinstance(act, ActBase) 检查(真实 sharpy 运行时)。
try:
    from sharpy.plans.acts import ActBase as _ActBase  # type: ignore[import-not-found]
except ImportError:
    _ActBase = object  # type: ignore[assignment]


class OpeningSustainAct(_ActBase):  # type: ignore[misc]
    """根据 race 启动 macro logic。flag 没 set 时无害 return True。

    继承 ActBase(sharpy.plans.acts.ActBase)，sharpy BuildOrder.merge_to_act
    的 isinstance 检查通过。sharpy 不可用时 fallback object(单测环境)。
    """

    def __init__(self, race: str) -> None:
        if race not in _VALID_RACES:
            raise ValueError(
                f"OpeningSustainAct: invalid race={race!r}; must be one of {_VALID_RACES}"
            )
        # 调用 ActBase.__init__ (有真实 ActBase 时):
        with contextlib.suppress(Exception):
            super().__init__()
        self.race = race
        self._kicked_off: bool = False
        self._sub_act: Any = None  # lazy init 真正 macro act
        self.knowledge: Any = None

    # ------------------------------------------------------------------
    # ActBase protocol: start(knowledge) + execute()
    # ------------------------------------------------------------------

    async def start(self, knowledge: Any) -> None:
        """sharpy BuildOrder 在首次把 act 加入时调用 start。"""
        self.knowledge = knowledge

    async def execute(self) -> bool:
        """每 tick 调用。flag 未 set 时直接 return True(无害通过)。"""
        vb = getattr(getattr(self.knowledge, "vibecraft", None), "sustain_uncap_active", False)
        if not vb:
            return True  # flag 未 set, 等待中

        if self._sub_act is None:
            try:
                self._sub_act = self._build_sub_act()
                await self._sub_act.start(self.knowledge)
                self._kicked_off = True
                logger.warning("OpeningSustainAct kicked off (race=%s)", self.race)
            except Exception as exc:
                logger.warning("OpeningSustainAct kick off FAIL: %s", exc)
                return True

        # 跑 sub_act; return True 不 block 兄弟 acts(BuildOrder children 各自跑)
        try:
            await self._sub_act.execute()
        except Exception as exc:
            logger.warning("OpeningSustainAct sub_act execute FAIL: %s", exc)
        return True

    # ------------------------------------------------------------------
    # 内部: race dispatch
    # ------------------------------------------------------------------

    def _build_sub_act(self) -> Any:
        # build-aware（2026-06-15）：当前 build 声明了 core_units → 按配比续兵 + 加产能；
        # 否则走旧的种族通用 sustain（向后兼容，只有动过 core_units 的 build 走新路）。
        core_units = self._active_core_units()
        if core_units:
            return self._build_from_core_units(core_units)
        if self.race == "PROTOSS":
            return self._build_protoss()
        if self.race == "ZERG":
            return self._build_zerg()
        if self.race == "TERRAN":
            return self._build_terran()
        raise ValueError(f"unknown race: {self.race}")  # 已在 __init__ 检查,理论不可达

    def _active_core_units(self) -> list[Any]:
        """从当前 active build 取 core_units（拿不到 → 空，走旧逻辑）。"""
        try:
            bot = self.knowledge.ai
            recipe = getattr(bot, "active_recipe", "") or ""
            director = getattr(bot, "director", None)
            lib = getattr(director, "library", None)
            if lib is None or not recipe:
                return []
            build = lib.get(recipe)
            return list(getattr(build, "core_units", []) or [])
        except Exception as exc:
            logger.debug("OpeningSustainAct _active_core_units fail: %s", exc)
            return []

    def _active_baneling_morph_mode(self) -> str:
        """当前 active build 的 baneling_morph_mode（forward=前压护蛹 / home=默认家里变）。"""
        try:
            bot = self.knowledge.ai
            recipe = getattr(bot, "active_recipe", "") or ""
            director = getattr(bot, "director", None)
            lib = getattr(director, "library", None)
            if lib is None or not recipe:
                return "home"
            return str(getattr(lib.get(recipe), "baneling_morph_mode", "home") or "home")
        except Exception as exc:
            logger.debug("OpeningSustainAct _active_baneling_morph_mode fail: %s", exc)
            return "home"

    # 气耗大的虫族兵（作为主力或副兵都吃不少气）。
    _GAS_HEAVY_ZERG = frozenset(
        {
            "HYDRALISK",
            "LURKER",
            "LURKERMP",
            "MUTALISK",
            "CORRUPTOR",
            "BROODLORD",
            "BANELING",
            "ULTRALISK",
            "INFESTOR",
            "SWARMHOSTMP",
            "VIPER",
        }
    )

    def _zerg_gas_per_base(self) -> int:
        """按本 build 的**主力兵种（mass policy）**决定每矿气矿数（2026-06-15 用户）。

        关键：键在 mass 兵种，不是"含任何气耗兵"。否则蟑螂系（roach_hydra/roach_ravager
        带刺蛇/破坏者副兵）会被误判成满气——而那恰是用户实测气浮 5000+ 要减气的 build。
        - 主力 = ROACH（蟑螂系）：吃矿为主、矿瓶颈，**即便带刺/破副兵也减气** → 1。
        - 主力 = ZERGLING（狗）：本身 0 气。带气耗大副兵（爆虫）→ 满气供副兵=2；纯狗(12pool)→ 1。
        - 主力 = 气耗大兵（飞龙/刺蛇主力）或无法判定 → 满气=2（默认安全）。
        """
        core_units = self._active_core_units()
        mass = next((c for c in core_units if str(getattr(c, "policy", "")) == "mass"), None)
        mass_unit = str(getattr(mass, "unit", "") or "").upper() if mass else ""
        if mass_unit == "ROACH":
            return 1
        if mass_unit == "ZERGLING":
            has_gas_secondary = any(
                str(getattr(c, "unit", "") or "").upper() in self._GAS_HEAVY_ZERG
                for c in core_units
                if str(getattr(c, "policy", "")) != "mass"
            )
            return 2 if has_gas_secondary else 1
        return 2

    def _macro_acts(self) -> list[Any]:
        """种族通用 macro（工人/补给/扩张/气，**不含产能建筑**）。"""
        from vibecraft.bot.auto_combat.persistent_macro import (
            MacroConfig,
            ProtossPersistentMacro,
            TerranPersistentMacro,
            ZergPersistentMacro,
        )

        if self.race == "PROTOSS":
            return ProtossPersistentMacro(
                MacroConfig(probe_cap=NON_ZERG_WORKER_CAP, expansion_cap=5)
            ).acts()
        if self.race == "ZERG":
            # 虫族 drone cap 压到 ZERG_WORKER_CAP=66（≈4 矿满采）：80 太多、吃光 200 人口 →
            # 没空间出兵（实测 roach_hydra:75 drone+28 蟑+12 刺=200，larva 堆 95、矿气全囤）。
            # 少 drone 腾人口给兵。build-aware 采气优先级：主力是吃矿为主的兵（蟑/狗）→
            # gas_per_base=1（少造气矿，否则气收入 >> 兵种气耗 → 气浮 5000+、钱花不干净）；
            # 含气耗大的兵（飞蛇/刺蛇/雷兽/爆虫等）→ 维持 2（满气）。判据看本 build 的
            # core_units（2026-06-15 用户：调采气优先级）。
            return ZergPersistentMacro(
                MacroConfig(
                    probe_cap=ZERG_WORKER_CAP,
                    expansion_cap=5,
                    chrono_target="drone",
                    gas_per_base=self._zerg_gas_per_base(),
                )
            ).acts()
        return TerranPersistentMacro(
            MacroConfig(probe_cap=NON_ZERG_WORKER_CAP, expansion_cap=4, chrono_target="scv")
        ).acts()

    def _build_from_core_units(self, core_units: list[Any]) -> Any:
        """build-aware sustain：macro + income-matched 产能建筑 + 按 core_units 续兵。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sharpy.plans import BuildOrder
        from sharpy.plans.acts import ActUnit, GridBuilding

        producer_targets, unit_specs = plan_from_core_units(core_units)
        building_acts = [
            GridBuilding(getattr(UnitTypeId, b), n)
            for b, n in producer_targets.items()
            if getattr(UnitTypeId, b, None) is not None
        ]
        if self.race == "PROTOSS":
            # 神族走 ProtossUnit：折跃门研究完成后自动切 warp-in（gateway 兵），未研究/robo/星门
            # 兵则等价 ActUnit train。比裸 ActUnit(STALKER, GATEWAY) 健壮 —— 后者折跃后 builders 空、
            # 永不出兵（gateway 系 sustain 卡死的根因，2026-06-16）。producer 仅 GridBuilding 用。
            from sharpy.plans.acts.protoss import ProtossUnit

            unit_acts = [
                ProtossUnit(getattr(UnitTypeId, u), c)
                for (u, _p, c) in unit_specs
                if getattr(UnitTypeId, u, None) is not None
            ]
        elif self.race == "ZERG":
            # 虫族走 ZergUnit：按兵种 dispatch —— morph 兵（爆虫/飞蛇/潜伏/BL）走对应 Morph act
            # （+ 从 larva 补源兵），larva 兵（狗/蟑/刺/飞龙）走 ActUnit(LARVA)。裸 ActUnit(BANELING,
            # ZERGLING) 的 zergling.train(BANELING) 对 morph 无效 → 爆虫卡在开局那几个永不增长
            # （ling_bane 爆虫冻结 12 的根因，2026-06-16；同神族 warpgate 类）。producer 不再用。
            # BANELING 特判：build 声明 baneling_morph_mode=forward（如 ling_bane all-in）→ 走共享
            # 前压+护蛹 morph（不在家变，跟开局 plan 一致）；否则默认 home MorphBaneling（宏观预备队）。
            from sharpy.plans.acts.zerg import ZergUnit

            from vibecraft.bot.auto_combat.zerg.baneling_morph import make_baneling_morph

            bane_mode = self._active_baneling_morph_mode()
            unit_acts = []
            for u, _p, c in unit_specs:
                if getattr(UnitTypeId, u, None) is None:
                    continue
                if u == "BANELING":
                    unit_acts.append(make_baneling_morph(c, mode=bane_mode))
                else:
                    unit_acts.append(ZergUnit(getattr(UnitTypeId, u), c))
        else:
            unit_acts = [
                ActUnit(getattr(UnitTypeId, u), getattr(UnitTypeId, p), c)
                for (u, p, c) in unit_specs
                if getattr(UnitTypeId, u, None) is not None
                and getattr(UnitTypeId, p, None) is not None
            ]
        # 虫族：补女王（每矿 1 注卵 + 防守；ZergPersistentMacro 不带女王）。注卵靠 ares/sharpy
        # 的 queen 逻辑（有则自动 inject；inject_coverage 埋点会显示是否真注卵）。
        if self.race == "ZERG":
            unit_acts.append(ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 6))
        logger.warning(
            "OpeningSustainAct build-aware kicked: 产能=%s 出兵=%s",
            producer_targets,
            [(u, c) for u, _p, c in unit_specs],
        )
        return BuildOrder([*self._macro_acts(), *building_acts, *unit_acts])

    def _build_protoss(self) -> Any:
        from sc2.ids.unit_typeid import UnitTypeId as U
        from sharpy.plans import BuildOrder
        from sharpy.plans.acts import ActUnit

        from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, ProtossPersistentMacro

        macro = ProtossPersistentMacro(MacroConfig(probe_cap=NON_ZERG_WORKER_CAP, expansion_cap=5))
        return BuildOrder(
            [
                *macro.acts(),
                ActUnit(U.STALKER, U.GATEWAY, 50),
                ActUnit(U.IMMORTAL, U.ROBOTICSFACILITY, 8),
            ]
        )

    def _build_zerg(self) -> Any:
        from sc2.ids.unit_typeid import UnitTypeId as U
        from sharpy.plans import BuildOrder
        from sharpy.plans.acts import ActUnit

        from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, ZergPersistentMacro

        macro = ZergPersistentMacro(
            MacroConfig(probe_cap=ZERG_WORKER_CAP, expansion_cap=5, chrono_target="drone")
        )
        return BuildOrder(
            [
                *macro.acts(),
                ActUnit(U.ROACH, U.ROACHWARREN, 40),
                ActUnit(U.RAVAGER, U.ROACHWARREN, 8),
            ]
        )

    def _build_terran(self) -> Any:
        from sc2.ids.unit_typeid import UnitTypeId as U
        from sharpy.plans import BuildOrder
        from sharpy.plans.acts import ActUnit

        from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, TerranPersistentMacro

        macro = TerranPersistentMacro(
            MacroConfig(probe_cap=NON_ZERG_WORKER_CAP, expansion_cap=4, chrono_target="scv")
        )
        return BuildOrder(
            [
                *macro.acts(),
                ActUnit(U.MARINE, U.BARRACKS, 60),
                ActUnit(U.MARAUDER, U.BARRACKS, 16),
                ActUnit(U.MEDIVAC, U.STARPORT, 6),
            ]
        )
