"""DtRaidAct: 第一波 DT 直奔敌方矿区杀农民,被反隐/被打则释放给 sharpy 接管。

用户 spec (2026-05-23)
======================
1. 隐刀刷出来不要等其他隐刀,第一时间就应该去前线
2. 第一波 DT 直接去对方矿区杀农民,忽略路上遇到的敌人,除非被反隐检测到或者
   被攻击;被反隐/被攻击则解除"直接去矿区杀农民"的强制要求
3. 第二波出来以后才考虑杀兵
4. 每个 DT 独立判定(用户决策:单个 DT 被打的退,没被打的继续杀农民)

为什么不复用 HarassWorkerLineAct
================================
HarassWorkerLineAct 是 hit-and-run + bail home,符合女妖/死神/恶火/飞龙(暴露在
明面、要风筝、血少回家)。DT 是隐身,语义不同:
  - 没被检测时无敌,不需要 kite(直推矿区即可)
  - 被检测/被打不是 bail home,而是放手让 sharpy ZoneAttack + VibeCraftMicroDarkTemplar
    (后者处理"被检测 → 撤") 接管
  - 没有"撤回家回血再来"——DT 是 all-in 一波,失败就是失败

行为(每 tick,逐 DT)
=====================
新 spawn DT 默认 state="raid":
  - Reserved role 独占(防 sharpy ZoneAttack / PlanZoneGather 抢)
  - 距 enemy_main 远:attack-move 推进
  - 已到矿区(_ARRIVE_DIST 内):打附近农民;无视野农民则 attack-move 到敌方主基地

state 翻转 "raid" → "released" 触发:
  - DT 自己 HP+shield 下降(被攻击 = 被反隐 + 被打的最终表现)
  - 敌方 detector 在 detection range 内(主动放弃 raid,免得白送)

state="released":
  - clear Reserved role
  - 本 act 不再下指令
  - sharpy PlanZoneAttack(已 force_attack=True)接管攻击 + VibeCraftMicroDarkTemplar
    处理被检测后的撤退(往棱镜 / 安全位跑)

集成
====
dt_rush.py SequentialList(tactics) 里,放在 PlanZoneGather 之前。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.detector_data import (
    DETECTOR_RANGES,
    DT_DETECTOR_BUFFER,
)

logger = logging.getLogger(__name__)

# 敌方农民类型 —— raid 目标。
_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)

# 距 enemy_main 此值内 → 视为"已到矿区",开始找农民;否则直推 attack-move。
_ARRIVE_DIST: float = 22.0


class DtRaidAct(ActBase):  # type: ignore[misc]
    """DT 个体微操:第一波直奔矿区杀农民,被反隐/被打则释放给 sharpy 接管。"""

    def __init__(self) -> None:
        super().__init__()
        # tag → "raid" / "released"
        self._dt_state: dict[int, str] = {}
        # tag → 上 tick HP+shield(用于检测被攻击)
        self._dt_last_hp: dict[int, float] = {}

    async def execute(self) -> bool:
        try:
            dts = self.ai.units(UnitTypeId.DARKTEMPLAR).ready
        except Exception:
            return True

        # 清死亡 DT 的 state(避免内存泄漏)
        alive_tags: set[int] = set()
        with contextlib.suppress(Exception):
            alive_tags = {dt.tag for dt in dts}
        self._dt_state = {t: s for t, s in self._dt_state.items() if t in alive_tags}
        self._dt_last_hp = {t: hp for t, hp in self._dt_last_hp.items() if t in alive_tags}

        if not dts:
            return True

        workers = self._visible_enemy_workers()
        raid_target = self._enemy_raid_target()

        for dt in dts:
            # 新 DT:默认 "raid"
            if dt.tag not in self._dt_state:
                self._dt_state[dt.tag] = "raid"
                self._dt_last_hp[dt.tag] = self._dt_total_hp(dt)

            # 被打 → release
            current_hp = self._dt_total_hp(dt)
            last_hp = self._dt_last_hp.get(dt.tag, current_hp)
            if current_hp < last_hp:
                self._release_dt(dt)
            self._dt_last_hp[dt.tag] = current_hp

            # 被 detector 覆盖 → release(主动放弃 raid)
            if self._dt_state.get(dt.tag) == "raid" and self._detector_nearby(dt):
                self._release_dt(dt)

            # 行动
            if self._dt_state.get(dt.tag) == "raid":
                self._raid_command(dt, workers, raid_target)
            # "released":不下命令,sharpy ZoneAttack + MicroDarkTemplar 接管

        return True

    # ------------------------------------------------------------------
    # 行动
    # ------------------------------------------------------------------

    def _raid_command(self, dt: Any, workers: Any, raid_target: Any) -> None:
        """raid state DT:Reserved + 推进 / 打农民。"""
        # 每 tick 重设 Reserved,防 sharpy roles 重建 Idle 时被抢
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, dt)

        if raid_target is None:
            return

        # 距矿区远 → attack-move 推进,不被沿途散兵游勇带偏
        # (DT 隐身,沿路敌人 vs AI 早期无 detection 看不见,直接穿过去)
        try:
            d = float(dt.distance_to(raid_target))
        except Exception:
            d = 0.0
        if d > _ARRIVE_DIST:
            with contextlib.suppress(Exception):
                dt.attack(raid_target)
            return

        # 已到矿区:打附近农民(只考虑 raid_target 附近的,避免去打侦察农民)
        if workers and workers.exists:
            try:
                nearby = workers.closer_than(_ARRIVE_DIST + 5, dt.position)
                if nearby.exists:
                    dt.attack(nearby.closest_to(dt))
                    return
            except Exception:
                pass

        # 矿区没视野到农民 → attack-move 到中心找
        with contextlib.suppress(Exception):
            dt.attack(raid_target)

    def _release_dt(self, dt: Any) -> None:
        """state 翻 released + clear Reserved 让 sharpy 接管。一次性。"""
        if self._dt_state.get(dt.tag) == "released":
            return
        self._dt_state[dt.tag] = "released"
        with contextlib.suppress(Exception):
            self.knowledge.roles.clear_task(dt)
        logger.info(
            "DT %d released to sharpy (被打/被检测) game_t=%.1f",
            dt.tag,
            float(getattr(self.ai, "time", 0.0)),
        )

    # ------------------------------------------------------------------
    # 决策查询
    # ------------------------------------------------------------------

    def _dt_total_hp(self, dt: Any) -> float:
        try:
            return float(dt.health) + float(dt.shield)
        except Exception:
            return 0.0

    def _visible_enemy_workers(self) -> Any:
        try:
            return self.ai.enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES)
        except Exception:
            return None

    def _enemy_raid_target(self) -> Any:
        """raid 目标:敌方主基地(永远 valid 即使无视野)。"""
        try:
            return self.ai.enemy_start_locations[0]
        except Exception:
            return None

    def _detector_nearby(self, dt: Any) -> bool:
        """敌方 detector 在 detection range 内 → DT 可能被反隐。"""
        try:
            for det_type, det_range in DETECTOR_RANGES.items():
                # detector 可能是 unit (overseer / observer / raven) 或 structure (turret / spore / cannon)
                dets_u = self.ai.enemy_units(det_type)
                dets_s = self.ai.enemy_structures(det_type)
                for det in list(dets_u) + list(dets_s):
                    try:
                        if det.distance_to(dt) < det_range - DT_DETECTOR_BUFFER:
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False
