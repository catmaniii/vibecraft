"""DtHarassMicro: DT 卸下后的 raid 微操 helper。

从 DTPrismHarass 中抽出的 per-DT 状态机,供 PrismWarpDropAct 及未来其他 act 复用。

逻辑（用户 2026-05-23 spec）
============================
- 被 prism 运到的 DT（离 home > _DT_RAID_HOME_DIST）→ state="raid"：
  Reserved + attack 最近敌方农民；无视野到农民则 attack-move 到 enemy_main
- HP+shield 下降（被攻击）或 detector 在 detection range 内 → state="released"：
  clear Reserved，让 sharpy ZoneAttack + VibeCraftMicroDarkTemplar 接管
- 单个 DT 独立判定：被打的 release，没被打的继续 raid
- 只接管离 home > _DT_RAID_HOME_DIST 的 DT；家里待装船的 DT 不动

main_army_is_attacking 函数
===========================
判断主力大部队是否出门一波，供 WarpZealotAtPrism 等 act 使用。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId

from vibecraft.bot.auto_combat.protoss.detector_data import (
    DETECTOR_RANGES,
    DT_DETECTOR_BUFFER,
)

logger = logging.getLogger(__name__)

# DT 离 home > 此距离才被接管 raid 微操；在家附近的留给装船
_DT_RAID_HOME_DIST: float = 30.0
# DT raid 目标：已知敌方农民单位类型
_DT_RAID_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)
# DT raid：距 enemy_main 此值内 → 视为"已到矿区"，找农民；否则直推 attack-move
_DT_RAID_ARRIVE_DIST: float = 22.0

# 主力球 unit types（出门判定）—— 不含 DARKTEMPLAR：
# DT 在敌方基地骚扰，算进来会把"主力球重心"带偏、误判成已出门
_MAIN_BALL_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.ZEALOT,
        UnitTypeId.STALKER,
        UnitTypeId.SENTRY,
        UnitTypeId.ADEPT,
        UnitTypeId.IMMORTAL,
        UnitTypeId.ARCHON,
        UnitTypeId.HIGHTEMPLAR,
        UnitTypeId.COLOSSUS,
    }
)
# 主力球至少这么多单位才算"出门一波"（防零星单位误判）
_MIN_BALL_FOR_ATTACK: int = 8
# 主力球重心离家超过「家→敌」距离的这个比例 → 判定为出门一波
_ATTACK_DISTANCE_FRACTION: float = 0.33


def main_army_is_attacking(ai: Any) -> bool:
    """主力大部队是否已出门一波（区别于前期 DT 骚扰）。

    判据（任一）：
    - 玩家显式 attack override。
    - 主力球（叉子 / Archon / 追猎… 不含 DT，≥ _MIN_BALL_FOR_ATTACK 个）
      重心离家已超过「家→敌」距离的 _ATTACK_DISTANCE_FRACTION。

    用途：棱镜进攻支援（ATTACK_SUPPORT / WarpZealotAtPrism）只在大部队真出门
    后才启动 —— 前期 DT 骚扰阶段绝不触发。
    """
    try:
        intent = getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
        if intent == "attack":
            return True
        # 2026-05-28:玩家明确 retreat/defend/hold → 主力不算在进攻状态,
        # 支援单位(warp zealot at prism 等)停止跟进。
        if intent in ("retreat", "defend", "hold"):
            return False
    except Exception:
        pass
    try:
        ball = ai.units.of_type(set(_MAIN_BALL_TYPES))
        if ball.amount < _MIN_BALL_FOR_ATTACK:
            return False
        center = ball.center
        home = ai.start_location
        enemy = ai.enemy_start_locations[0]
        total = home.distance_to(enemy)
        if total <= 1.0:
            return False
        return bool(center.distance_to(home) > _ATTACK_DISTANCE_FRACTION * total)
    except Exception:
        return False


class DtHarassMicro:
    """DT 卸下后的 raid 微操状态机。

    供 PrismWarpDropAct 等 act compose 使用。每 tick 调 tick(ai, knowledge)。

    只接管离 home > _DT_RAID_HOME_DIST 的 DT（被 prism 运到的）。
    家里待装船的 DT 不动（交给装载阶段 smart(prism)）。
    """

    def __init__(self) -> None:
        # tag → "raid" / "released"，per-DT 独立状态
        self._dt_raid_state: dict[int, str] = {}
        # DT 上 tick HP+shield（检测被攻击）
        self._dt_raid_last_hp: dict[int, float] = {}

    def tick(self, ai: Any, knowledge: Any) -> None:
        """每 tick 调用一次，接管 DT raid 微操。"""
        try:
            dts = ai.units(UnitTypeId.DARKTEMPLAR).ready
            home = ai.start_location
        except Exception:
            return

        # 清死亡 DT 的 state（避免内存泄漏）
        alive_tags: set[int] = set()
        with contextlib.suppress(Exception):
            alive_tags = {dt.tag for dt in dts}
        self._dt_raid_state = {t: s for t, s in self._dt_raid_state.items() if t in alive_tags}
        self._dt_raid_last_hp = {
            t: hp for t, hp in self._dt_raid_last_hp.items() if t in alive_tags
        }

        if not dts:
            return

        workers = self._visible_enemy_workers(ai)
        raid_target = self._enemy_raid_target(ai)

        for dt in dts:
            # 只接管离 home 远的 DT（被 prism 运到的）；家里的不动
            try:
                d_home = float(dt.distance_to(home))
            except Exception:
                continue
            if d_home < _DT_RAID_HOME_DIST:
                continue

            # 新 DT：默认 raid
            if dt.tag not in self._dt_raid_state:
                self._dt_raid_state[dt.tag] = "raid"
                self._dt_raid_last_hp[dt.tag] = self._dt_total_hp(dt)

            # 被打 → release
            current_hp = self._dt_total_hp(dt)
            last_hp = self._dt_raid_last_hp.get(dt.tag, current_hp)
            if current_hp < last_hp:
                self._release_dt(dt, knowledge, ai)
            self._dt_raid_last_hp[dt.tag] = current_hp

            # 被 detector 覆盖 → release（主动放弃 raid）
            if self._dt_raid_state.get(dt.tag) == "raid" and self._detector_nearby(dt, ai):
                self._release_dt(dt, knowledge, ai)

            # 行动
            if self._dt_raid_state.get(dt.tag) == "raid":
                self._raid_command(dt, workers, raid_target, knowledge)

    def _raid_command(self, dt: Any, workers: Any, raid_target: Any, knowledge: Any) -> None:
        """raid state DT：Reserved + 推进/打农民。"""
        try:
            from sharpy.managers.core.roles import UnitTask

            knowledge.roles.set_task(UnitTask.Reserved, dt)
        except Exception:
            pass

        if raid_target is None:
            return

        # 距矿区远 → attack-move 推进（DT 隐身，沿路敌人 vs AI 早期无 detection）
        try:
            d = float(dt.distance_to(raid_target))
        except Exception:
            d = 0.0
        if d > _DT_RAID_ARRIVE_DIST:
            with contextlib.suppress(Exception):
                dt.attack(raid_target)
            return

        # 已到矿区：打附近农民
        if workers and workers.exists:
            try:
                nearby = workers.closer_than(_DT_RAID_ARRIVE_DIST + 5, dt.position)
                if nearby.exists:
                    dt.attack(nearby.closest_to(dt))
                    return
            except Exception:
                pass

        # 矿区没视野到农民 → attack-move 到中心找
        with contextlib.suppress(Exception):
            dt.attack(raid_target)

    def _release_dt(self, dt: Any, knowledge: Any, ai: Any) -> None:
        """state 翻 released + clear Reserved 让 sharpy 接管。一次性。"""
        if self._dt_raid_state.get(dt.tag) == "released":
            return
        self._dt_raid_state[dt.tag] = "released"
        with contextlib.suppress(Exception):
            knowledge.roles.clear_task(dt)
        logger.info(
            "DT %d released to sharpy (被打/被检测) game_t=%.1f",
            dt.tag,
            float(getattr(ai, "time", 0.0)),
        )

    def _dt_total_hp(self, dt: Any) -> float:
        try:
            return float(dt.health) + float(dt.shield)
        except Exception:
            return 0.0

    def _visible_enemy_workers(self, ai: Any) -> Any:
        try:
            return ai.enemy_units.filter(lambda u: u.type_id in _DT_RAID_WORKER_TYPES)
        except Exception:
            return None

    def _enemy_raid_target(self, ai: Any) -> Any:
        try:
            return ai.enemy_start_locations[0]
        except Exception:
            return None

    def _detector_nearby(self, dt: Any, ai: Any) -> bool:
        """敌方 detector 在 detection range 内 → DT 可能被反隐。"""
        try:
            for det_type, det_range in DETECTOR_RANGES.items():
                dets_u = ai.enemy_units(det_type)
                dets_s = ai.enemy_structures(det_type)
                for det in list(dets_u) + list(dets_s):
                    try:
                        if det.distance_to(dt) < det_range - DT_DETECTOR_BUFFER:
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False
