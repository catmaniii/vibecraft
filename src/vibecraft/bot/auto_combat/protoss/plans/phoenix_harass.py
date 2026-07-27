"""PhoenixHarassAct: 凤凰骚扰微操 act —— Graviton Beam 吊农民骚扰。

立项背景
========
凤凰(PHOENIX)是纯对空单位，直接 attack-move 打不到地面农民。骚扰矿区靠
Graviton Beam(重力光束)：把农民"提"到空中变成空中目标，再射杀它。

行为(每 tick，逐凤凰)
======================
1. release_after 到点 → 放手归队，本 act 不再下指令。
2. 标 Reserved —— 独占控制权，不让 PlanZoneGather / ZoneAttack 拽走。
3. 血量危急(< bail_hp_ratio) → 全撤回家保命(回血滞回防抖)。
4. 离对方主基地远 → move 进场。
5. 已到矿区：
   a. 能量 >= 50 且有可见农民 → 对最近农民施放 GRAVITONBEAM_GRAVITONBEAM。
   b. 否则 → attack(矿区点)，凤凰自动射杀已提起的空中农民。

接线
====
放进 plan tactics SequentialList，排在 DistributeWorkers / SpeedMining 之后、
PlanZoneGather 之前。execute() 恒返回 True。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# 敌方农民类型 —— Graviton Beam 目标。
_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)

# 到对方主基地距离 <= 此值 → 视为"已到矿区"，切骚扰模式；否则直推进场。
_ARRIVE_DIST: float = 22.0

# Graviton Beam 能量消耗。
_BEAM_ENERGY_COST: float = 50.0

# 2026-05-28 用户反馈:出一个凤凰去一个,没攒到一起。
# 默认攒 5 凤凰才 launch 第一波;launch 后新凤凰立即追上,不再等。
_DEFAULT_WAVE_THRESHOLD: int = 5


class PhoenixHarassAct(ActBase):  # type: ignore[misc]
    """凤凰骚扰微操：Graviton Beam 吊农民 + 射杀。

    凤凰是纯对空单位，骚扰地面农民必须先用 Graviton Beam 把农民提到空中，
    才能用普通攻击射杀。本 act 按能量门槛控制施放频率，血量低撤退保命。

    2026-05-28 wave threshold:维护"已 launch"状态 — 第一波必须攒够
    wave_threshold(默认 5)凤凰才一起出门;之后新凤凰自动加入(不再等)。
    防"出一个去一个"被一波一波吃掉。
    """

    def __init__(
        self,
        release_after: float | None = None,
        bail_hp_ratio: float = 0.3,
        recover_hp_ratio: float = 0.6,
        wave_threshold: int = _DEFAULT_WAVE_THRESHOLD,
    ) -> None:
        """
        release_after   : game-seconds；到点后放手归队（凤凰并入主力）。None = 永不放手。
        bail_hp_ratio   : 血量(HP+护盾)比例低于此值 → 全撤回家。
        recover_hp_ratio: 已撤退的凤凰血量回到此值以上才重新出击（回血滞回）。
        wave_threshold  : 第一波 launch 凤凰数下限(默认 5)。未达此数前凤凰 stay
                          home gather;达到后整批 launch,后续新凤凰立即追上。
        """
        super().__init__()
        self._release_after = release_after
        self._bail_hp = float(bail_hp_ratio)
        self._recover_hp = float(recover_hp_ratio)
        # 正在全撤回家的单位 tag —— 维护回血滞回。
        self._bailing: set[int] = set()
        # 2026-05-28: wave gating
        self._wave_threshold = int(wave_threshold)
        self._wave_launched: bool = False

    async def execute(self) -> bool:
        try:
            phoenixes = self.ai.units(UnitTypeId.PHOENIX).ready
        except Exception:
            return True
        if not phoenixes:
            return True

        # 骚扰窗口结束 → 放手：不再 Reserved、不再下指令，凤凰归队主力。
        if self._release_after is not None:
            with contextlib.suppress(Exception):
                if float(self.ai.time) >= self._release_after:
                    return True

        # 2026-05-28: wave gating — 第一波必须攒够 _wave_threshold 才 launch
        # latch:一旦 launched 永远 True(新凤凰立即追上,不再 stay home)
        if not self._wave_launched and phoenixes.amount >= self._wave_threshold:
            self._wave_launched = True
            logger.warning(
                "PhoenixHarass wave LAUNCHED (count=%d ≥ threshold=%d)",
                phoenixes.amount,
                self._wave_threshold,
            )

        enemy_main = self._enemy_main()
        workers = self._visible_enemy_workers()

        for unit in phoenixes:
            self._reserve(unit)
            if self._should_bail(unit):
                with contextlib.suppress(Exception):
                    unit.move(self.ai.start_location)
                continue
            # 未 launch 第一波 → 凤凰 stay home gather,不单独飞出门
            if not self._wave_launched:
                with contextlib.suppress(Exception):
                    unit.move(self.ai.start_location)
                continue
            self._micro(unit, workers, enemy_main)
        return True

    # ------------------------------------------------------------------
    # 微操
    # ------------------------------------------------------------------

    def _micro(self, unit: Any, workers: Any, enemy_main: Any) -> None:
        """离矿区远 → 直推进场；已到矿区 → Graviton Beam 骚扰。"""
        far = self._is_far_from_enemy_main(unit, enemy_main)

        # ---- 进场：离矿区还远 → 直推，不被沿途威胁带偏 ----
        if far:
            if enemy_main is not None:
                with contextlib.suppress(Exception):
                    unit.move(enemy_main)
            return

        # ---- 已到矿区：Graviton Beam 骚扰 ----
        self._harass(unit, workers, enemy_main)

    def _harass(self, unit: Any, workers: Any, enemy_main: Any) -> None:
        """矿区骚扰逻辑：能量足 → Graviton Beam；否则 attack 矿区点杀已提单位。"""
        energy = self._energy(unit)

        if energy >= _BEAM_ENERGY_COST and workers:
            # 能量够 + 有可见农民 → 对最近农民施放 Graviton Beam。
            with contextlib.suppress(Exception):
                target = workers.closest_to(unit)
                unit(AbilityId.GRAVITONBEAM_GRAVITONBEAM, target)
            return

        # 能量不足 / 暂无农民视野 → attack 矿区中心，让凤凰自动射杀已提起的空中农民。
        if enemy_main is not None:
            with contextlib.suppress(Exception):
                unit.attack(enemy_main)

    # ------------------------------------------------------------------
    # 决策 / 查询（可单独测试的小方法）
    # ------------------------------------------------------------------

    def _is_far_from_enemy_main(self, unit: Any, enemy_main: Any) -> bool:
        """凤凰离对方主基地是否还远（True = 进场阶段，False = 已到矿区）。"""
        if enemy_main is None:
            return True
        try:
            return bool(float(unit.distance_to(enemy_main)) > _ARRIVE_DIST)
        except Exception:
            return True

    def _energy(self, unit: Any) -> float:
        """凤凰当前能量；取不到时返回 0（不施放 Beam，保守处理）。"""
        try:
            return float(unit.energy)
        except Exception:
            return 0.0

    def _should_bail(self, unit: Any) -> bool:
        """血量危急 → 全撤回家，带回血滞回防抖。"""
        ratio = self._hp_ratio(unit)
        if unit.tag in self._bailing:
            if ratio >= self._recover_hp:
                self._bailing.discard(unit.tag)
                return False
            return True
        if ratio < self._bail_hp:
            self._bailing.add(unit.tag)
            return True
        return False

    def _hp_ratio(self, unit: Any) -> float:
        """(HP + 护盾) / 满值；取不到时按满血处理（不误撤）。"""
        try:
            mx = float(unit.health_max) + float(unit.shield_max)
            if mx <= 0:
                return 1.0
            return (float(unit.health) + float(unit.shield)) / mx
        except Exception:
            return 1.0

    def _visible_enemy_workers(self) -> Any:
        """当前视野内的敌方农民。"""
        try:
            return self.ai.enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES)
        except Exception:
            return None

    def _enemy_main(self) -> Any:
        """对方主基地位置；取不到时返回 None。"""
        try:
            return self.ai.enemy_start_locations[0]
        except Exception:
            return None

    def _reserve(self, unit: Any) -> None:
        """标 Reserved —— 每 tick 重设，独占控制权。"""
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)
