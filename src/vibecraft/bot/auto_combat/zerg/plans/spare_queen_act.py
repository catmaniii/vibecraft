"""多余女王的去处 —— SpareQueenAct（2026-07-26 用户）。

用户原话:「家里多的女王，如果到对方家放坑道虫一直不成功，可以出去铺菌毯，然后作为部队
去参与前线防守（最外面的分矿）」。

**没有这个 act 之前**：坑道链卡住（虫放不进去/被拆）时，超出注卵需要的那些女王就在家里干站着——
既没在铺菌毯，也没在防守，纯浪费 2 supply/只。

两态，每只女王恰好一个状态（不用群体后验推断）：

  CREEP  —— 往**最外侧自家分矿**方向走，路上只要脚下有菌毯且能量够就种菌毯瘤，把菌毯朝前线推。
             期间 Reserve 独占（防 `PlanZoneGather` 把它当 idle 拽回家）。
  DEFEND —— 已到最外分矿 → **`clear_task` 还给 sharpy**，由 `PlanZoneDefense` 当普通防守兵用
             （用户「作为部队去参与前线防守」）。到此本 act 不再对它下令，也不再重新招募它。

**触发条件（坑道链确实卡住了才动手）**：自家 NydusNetwork 就绪超过 `stall_after_s` 秒、且敌方那边
一个坑道虫都没立住。坑道链正常推进时本 act 完全不介入——女王该注卵注卵、该进坑道进坑道。

**不抢别人的女王**：留家注卵的（`max(keep_home, 基地数)` 只，与 `nydus_raid_act` 同口径）、
`NydusRaidAct` 已认领的（读 `ai._vibecraft_nydus_raid_tags`）、玩家单位级 claim 的
（`ai._llm_controlled_tags`）一律不碰。
"""

from __future__ import annotations

import contextlib
from typing import Any

from loguru import logger
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit_command import UnitCommand
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.zerg.creep import (
    CREEP_TUMOR_ENERGY,
    existing_tumors,
    pick_creep_tumor_spot,
)

_STALL_AFTER_S: float = 150.0  # 自家坑道网络就绪多久还没在敌方立住虫 → 判定"坑道链卡住"
_KEEP_HOME_QUEENS: int = 2  # 留家注卵下限（与 nydus_raid_act._KEEP_HOME_QUEENS 同口径）
_ARRIVE_DIST: float = 6.0  # 离最外分矿这么近 → 视为到位，转 DEFEND
_TUMOR_COOLDOWN_S: float = 8.0  # 同一只女王两次种毯的最小间隔
_TUMOR_MAX_R: int = 8  # 从女王脚下往外找可种点的最大半径
_TUMOR_SPACING: float = 8.0  # 离已有菌毯瘤至少这么远（自家扩毯比敌方家可以稀一点）
_LOG_COOLDOWN_S: float = 10.0

_TOWNHALL_TYPES: tuple[UnitTypeId, ...] = (
    UnitTypeId.HATCHERY,
    UnitTypeId.LAIR,
    UnitTypeId.HIVE,
)


class SpareQueenAct(ActBase):  # type: ignore[misc]
    """坑道链卡住时，把多余女王派去铺菌毯 + 到最外分矿参与防守。"""

    def __init__(
        self,
        stall_after_s: float = _STALL_AFTER_S,
        keep_home_queens: int = _KEEP_HOME_QUEENS,
    ) -> None:
        super().__init__()
        self.stall_after_s = stall_after_s
        self.keep_home_queens = keep_home_queens
        self._state: dict[int, str] = {}  # tag -> "CREEP" / "DEFEND"
        self._tumor_at: dict[int, float] = {}
        self._network_ready_since: float | None = None
        self._last_log: float = -999.0

    async def execute(self) -> bool:
        try:
            self._tick()
        except Exception:
            logger.exception("SpareQueenAct._tick error")
        return True  # non-blocking

    # ------------------------------------------------------------------

    def _nydus_stalled(self, now: float) -> bool:
        """自家网络就绪够久、敌方那边仍没有立住的坑道虫 → 坑道链卡住。"""
        ready = False
        with contextlib.suppress(Exception):
            ready = bool(self.cache.own(UnitTypeId.NYDUSNETWORK).ready)
        if not ready:
            self._network_ready_since = None
            return False
        if self._network_ready_since is None:
            self._network_ready_since = now
        with contextlib.suppress(Exception):
            if self.cache.own(UnitTypeId.NYDUSCANAL).exists:
                return False  # 虫立住了，链没卡，女王该干嘛干嘛
        return now - self._network_ready_since >= self.stall_after_s

    def _frontier_base(self) -> Point2 | None:
        """最外侧自家基地 = 离敌方主基最近的那个自家 townhall（前线）。"""
        with contextlib.suppress(Exception):
            enemy = self.ai.enemy_start_locations[0]
            halls = [u for t in _TOWNHALL_TYPES for u in self.cache.own(t).ready]
            if halls:
                return min(halls, key=lambda h: h.distance_to(enemy)).position
        with contextlib.suppress(Exception):
            return self.ai.start_location
        return None

    def _spare_queens(self) -> list[Any]:
        """可动用的多余女王：排除留家注卵的、坑道队认领的、玩家 claim 的、已 DEFEND 的。"""
        queens: list[Any] = []
        with contextlib.suppress(Exception):
            queens = sorted(self.cache.own(UnitTypeId.QUEEN).ready, key=lambda u: u.tag)
        if not queens:
            return []
        n_bases = 1
        with contextlib.suppress(Exception):
            n_bases = max(1, int(self.ai.townhalls.amount))
        keep = max(self.keep_home_queens, n_bases)
        home = {u.tag for u in queens[:keep]}  # 与 raid act 同口径：按 tag 稳定取前 keep 只留家
        raid_tags: set[int] = set(getattr(self.ai, "_vibecraft_nydus_raid_tags", None) or set())
        player_tags: set[int] = set(getattr(self.ai, "_llm_controlled_tags", None) or set())
        return [
            q
            for q in queens
            if q.tag not in home
            and q.tag not in raid_tags
            and q.tag not in player_tags
            and self._state.get(q.tag) != "DEFEND"
        ]

    def _tick(self) -> None:
        now = float(self.ai.time)
        # 死掉的清状态（DEFEND 的也清，免得字典无限涨）
        alive: set[int] = set()
        with contextlib.suppress(Exception):
            alive = {u.tag for u in self.cache.own(UnitTypeId.QUEEN)}
        for t in list(self._state.keys()):
            if t not in alive:
                self._state.pop(t, None)
                self._tumor_at.pop(t, None)

        if not self._nydus_stalled(now):
            return
        frontier = self._frontier_base()
        if frontier is None:
            return

        spare = self._spare_queens()
        if not spare:
            return
        tumors = existing_tumors(self.cache)
        for q in spare:
            self._state.setdefault(q.tag, "CREEP")
            # ① 脚下有菌毯 + 能量够 → 种一个，把菌毯朝前线推
            if getattr(q, "energy", 0) >= CREEP_TUMOR_ENERGY and (
                now - self._tumor_at.get(q.tag, -999.0) >= _TUMOR_COOLDOWN_S
            ):
                spot = pick_creep_tumor_spot(
                    self.ai,
                    q.position,
                    tumors,
                    max_r=_TUMOR_MAX_R,
                    spacing=_TUMOR_SPACING,
                    toward=frontier,
                )
                if spot is not None:
                    self._bypass_cmd(AbilityId.BUILD_CREEPTUMOR_QUEEN, q, spot)
                    self._tumor_at[q.tag] = now
                    tumors.append(spot)
                    logger.info(f"SPAREQUEEN creep tag={q.tag} at=({spot.x:.0f},{spot.y:.0f})")
                    continue
            # ② 到最外分矿了 → 交还 sharpy 当防守兵（用户「作为部队参与前线防守」）
            if q.distance_to(frontier) <= _ARRIVE_DIST:
                self._state[q.tag] = "DEFEND"
                with contextlib.suppress(Exception):
                    self.knowledge.roles.clear_task(q)
                logger.info(f"SPAREQUEEN defend tag={q.tag} 交回 sharpy 前线防守")
                continue
            # ③ 还在路上 → Reserve 独占 + 朝前线走（撤退性质移动，不 attack_move）
            with contextlib.suppress(Exception):
                self.knowledge.roles.set_task(UnitTask.Reserved, q)
            with contextlib.suppress(Exception):
                q.move(frontier)

        if now - self._last_log >= _LOG_COOLDOWN_S:
            self._last_log = now
            n_creep = sum(1 for s in self._state.values() if s == "CREEP")
            n_def = sum(1 for s in self._state.values() if s == "DEFEND")
            logger.info(f"SPAREQUEEN squad creep={n_creep} defend={n_def}")

    def _bypass_cmd(self, ability: AbilityId, unit: Any, target: Any | None = None) -> None:
        """走 common_bot 的 bypass 队列下技能（绕开 python-sc2 的 orders==[] 静默丢单 bug，
        与 `nydus_raid_act._bypass_cmd` 同款）。"""
        if not hasattr(self.ai, "_vibecraft_bypass_actions"):
            self.ai._vibecraft_bypass_actions = []
        with contextlib.suppress(Exception):
            cmd = UnitCommand(ability, unit, target, False)
            self.ai._vibecraft_bypass_actions.append(cmd)
