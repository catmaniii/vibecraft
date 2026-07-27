"""WidowMineDropAct: 医疗船 + 寡妇雷 空投骚扰状态机（2026-05-22 v1）。

widow_mine_drop 开局专用。寡妇雷移速极慢，靠医疗船运到对方矿区埋地伏击：

  LOAD → FLY_IN → DROP → RECALL → ESCAPE

行为决策
========
1. 家里装载：等有寡妇雷且医疗船在场 → 把 WidowMine 依次 smart-cast 上船（LOAD）。
2. 飞向对方矿区（FLY_IN）：医疗船飞向敌方主基地附近的矿线落点。
3. DROP：医疗船在矿线卸下所有寡妇雷（UNLOADALLAT_MEDIVAC），每个落地雷立刻
   BURROWDOWN。医疗船在矿线小范围移动，增加骚扰面积。
4. RECALL：检测 burrowed 寡妇雷打过一发（weapon_cooldown > 0 或已存活超时）→
   BURROWUP → mine.smart(medivac) 回船。
5. ESCAPE：所有雷回船（或超时）→ 医疗船飞回家。

v1 可接受的简化
==============
- 「贴边缘」不做，直接飞向敌方矿区落点（enemy_main 朝家偏移几格 = 外围高地边缘）。
- 逐个卸雷太复杂：优先 UNLOADALLAT_MEDIVAC 一次卸完，雷自行就近埋地。
- 「打过一发」判据：burrowed 状态下 weapon_cooldown > 0 → 说明刚开火；
  兜底：burrowed 超过 _RECALL_TIMEOUT_S 秒也收。
"""

from __future__ import annotations

import contextlib
import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# ── 调参常量 ──────────────────────────────────────────────────────────────
# 到达判定：医疗船离目标距离 ≤ 此值认为「到位」。
_ARRIVED_DIST: float = 3.0
# drop 落点：敌方主基地朝家偏移这么多（矿线外围，避免正飞到主基地中心被集火）。
_DROP_OFFSET: float = 8.0
# 埋地超时后强制回收（防永久潜伏）。
_RECALL_TIMEOUT_S: float = 30.0
# 等待寡妇雷上船的硬超时（防单个雷卡住无限等）。
_LOAD_TIMEOUT_S: float = 45.0
# ESCAPE 后等这么久回到 IDLE、可以重新出发（允许医疗船回家）。
_ESCAPE_TIMEOUT_S: float = 20.0
# 卸雷后医疗船在矿线移动的半径（增加骚扰覆盖面）。
_DROP_PATROL_OFFSET: float = 5.0
# 「装载」：每架医疗船想带的寡妇雷数量上限（一架医疗船 cargo_max = 8，寡妇雷 4 格）。
_MINES_PER_MEDIVAC: int = 2


class DropState(str, Enum):
    """医疗船空投状态机。"""

    IDLE = "idle"  # 等待首批寡妇雷就绪
    LOAD = "load"  # 把寡妇雷装上医疗船
    FLY_IN = "fly_in"  # 载雷飞向敌方矿区
    DROP = "drop"  # 卸雷 + 埋地（雷落地后潜伏）
    RECALL = "recall"  # 开火后：BURROWUP + smart 回船
    ESCAPE = "escape"  # 所有雷回收 → 医疗船撤回家


class WidowMineDropAct(ActBase):  # type: ignore[misc]
    """医疗船 + 寡妇雷 空投骚扰，widow_mine_drop 专用。"""

    def __init__(self) -> None:
        super().__init__()
        self._state: DropState = DropState.IDLE
        self._state_entered_ts: float = 0.0
        # 绑定的医疗船 tag（每次出发绑一架）。
        self._medivac_tag: int | None = None
        # 装载开始时刻（硬超时用）。
        self._load_since: float | None = None
        # burrowed 寡妇雷 tags 及各自进入潜地的时刻（RECALL 超时计时）。
        self._burrowed_since: dict[int, float] = {}

    async def execute(self) -> bool:
        """每 tick 调用；恒返回 True（不阻塞后续 plan step）。"""
        medivac = self._find_medivac()

        if medivac is None:
            # 没有医疗船 → 重置到 IDLE，等医疗船生产出来
            if self._state != DropState.IDLE:
                self._set_state(DropState.IDLE)
            return True

        self._medivac_tag = medivac.tag

        # 每 tick 把医疗船标 Reserved
        self._reserve(medivac)

        if self._state == DropState.IDLE:
            self._set_state(DropState.LOAD)

        # 主状态机分发
        with contextlib.suppress(Exception):
            if self._state == DropState.LOAD:
                await self._handle_load(medivac)
            elif self._state == DropState.FLY_IN:
                await self._handle_fly_in(medivac)
            elif self._state == DropState.DROP:
                await self._handle_drop(medivac)
            elif self._state == DropState.RECALL:
                await self._handle_recall(medivac)
            elif self._state == DropState.ESCAPE:
                await self._handle_escape(medivac)

        return True

    # ── State handlers ────────────────────────────────────────────────────

    async def _handle_load(self, medivac: Any) -> None:
        """把已有的寡妇雷依次 smart 上船；装够或超时 → FLY_IN。"""
        mines = self._free_mines()
        if not mines:
            # 还没有寡妇雷，等待生产
            return

        cargo = self._cargo_used(medivac)
        if cargo == 0 and self._load_since is None:
            self._load_since = self.ai.time

        # 把所有散落的寡妇雷标 Reserved 并 smart 上船
        for mine in mines:
            self._reserve(mine)
            with contextlib.suppress(Exception):
                mine.smart(medivac)

        timed_out = (
            self._load_since is not None and self.ai.time - self._load_since > _LOAD_TIMEOUT_S
        )
        ready = cargo >= _MINES_PER_MEDIVAC * 4  # 每个寡妇雷占 4 cargo 格
        all_aboard = len(mines) == 0  # 所有散落的雷都上了船

        if timed_out or (cargo > 0 and (ready or all_aboard)):
            logger.info(
                "WidowMineDrop LOAD done: cargo=%d timeout=%s",
                cargo,
                timed_out,
            )
            self._load_since = None
            self._set_state(DropState.FLY_IN)

    async def _handle_fly_in(self, medivac: Any) -> None:
        """飞向敌方矿区落点，到位 → DROP。"""
        drop_pos = self._compute_drop_pos()
        if drop_pos is None:
            return
        if medivac.distance_to(drop_pos) > _ARRIVED_DIST:
            medivac.move(drop_pos)
        else:
            self._set_state(DropState.DROP)

    async def _handle_drop(self, medivac: Any) -> None:
        """一次卸下所有寡妇雷（UNLOADALLAT_MEDIVAC），落地雷各自埋地。"""
        cargo = self._cargo_used(medivac)
        if cargo > 0:
            # 卸载所有 cargo
            drop_pos = self._compute_drop_pos()
            if drop_pos is not None:
                with contextlib.suppress(Exception):
                    medivac(AbilityId.UNLOADALLAT_MEDIVAC, drop_pos)
            return

        # cargo 空了 = 雷已落地 → 让每个 burrowed 或散落的雷立刻埋地
        self._burrow_all_mines()
        # 医疗船在矿线小范围游走，增大覆盖
        patrol = self._compute_patrol_pos()
        if patrol is not None:
            with contextlib.suppress(Exception):
                medivac.move(patrol)

        # 切换到 RECALL 阶段，开始追踪开火状态
        self._set_state(DropState.RECALL)

    async def _handle_recall(self, medivac: Any) -> None:
        """检测 burrowed 雷打过一发（weapon_cooldown > 0 或超时）→ BURROWUP + 上船。"""
        now = self.ai.time
        burrowed = self._get_burrowed_mines()

        # 追踪新进入 burrowed 的雷（记录进入时刻）
        burrowed_tags = {m.tag for m in burrowed}
        for tag in burrowed_tags:
            if tag not in self._burrowed_since:
                self._burrowed_since[tag] = now

        # 清理已不在 burrowed 中的雷（已起身 / 死亡）
        stale = [t for t in self._burrowed_since if t not in burrowed_tags]
        for t in stale:
            del self._burrowed_since[t]

        # 对已开火 / 超时的 burrowed 雷：起身 + smart 上船
        for mine in burrowed:
            entered_ts = self._burrowed_since.get(mine.tag, now)
            fired = float(getattr(mine, "weapon_cooldown", 0.0)) > 0.0
            timeout = (now - entered_ts) > _RECALL_TIMEOUT_S
            if fired or timeout:
                with contextlib.suppress(Exception):
                    mine(AbilityId.BURROWUP_WIDOWMINE)
            else:
                self._reserve(mine)

        # 检查起身但还没上船的雷：smart 向医疗船
        for mine in self._free_mines():
            with contextlib.suppress(Exception):
                mine.smart(medivac)

        # ESCAPE 条件：没有 burrowed 雷 + 没有散落的雷（全回船或死了）
        if not burrowed and not self._free_mines():
            logger.info("WidowMineDrop RECALL done → ESCAPE")
            self._burrowed_since.clear()
            self._set_state(DropState.ESCAPE)

    async def _handle_escape(self, medivac: Any) -> None:
        """所有雷回收 → 医疗船飞回家。"""
        with contextlib.suppress(Exception):
            home = self.ai.start_location
            medivac.move(home)

        # 回家超时后重置 IDLE，允许下一波出发
        elapsed = self.ai.time - self._state_entered_ts
        if elapsed > _ESCAPE_TIMEOUT_S:
            logger.info("WidowMineDrop ESCAPE done → IDLE (next wave)")
            self._medivac_tag = None
            self._set_state(DropState.IDLE)

    # ── 辅助：寡妇雷/医疗船查找 ──────────────────────────────────────────

    def _find_medivac(self) -> Any:
        """找一架绑定或任意可用的医疗船。"""
        try:
            medivacs = self.ai.units(UnitTypeId.MEDIVAC)
        except Exception:
            return None
        if not medivacs:
            return None
        if self._medivac_tag is not None:
            same = medivacs.tags_in([self._medivac_tag])
            if same:
                return same[0]
        # 没有绑定 / 绑定的死了 → 取第一架
        return medivacs[0] if medivacs else None

    def _free_mines(self) -> list[Any]:
        """场上未上船（非 cargo）的 WidowMine（未潜地，可被指令移动）。"""
        try:
            return list(self.ai.units(UnitTypeId.WIDOWMINE).ready)
        except Exception:
            return []

    def _get_burrowed_mines(self) -> list[Any]:
        """当前 burrowed（WIDOWMINEBURROWED）的寡妇雷。"""
        try:
            return list(self.ai.units(UnitTypeId.WIDOWMINEBURROWED))
        except Exception:
            return []

    def _cargo_used(self, medivac: Any) -> int:
        return int(getattr(medivac, "cargo_used", 0))

    # ── 辅助：位置计算 ───────────────────────────────────────────────────

    def _compute_drop_pos(self) -> Point2 | None:
        """落点：敌方主基地朝家偏移 _DROP_OFFSET 格（矿线外围，减少集火）。"""
        try:
            enemy: Point2 = self.ai.enemy_start_locations[0]
            home: Point2 = self.ai.start_location
            return enemy.towards(home, _DROP_OFFSET)
        except Exception:
            return None

    def _compute_patrol_pos(self) -> Point2 | None:
        """卸雷后医疗船游走点：落点横向偏移一点，扩大骚扰面积。"""
        drop = self._compute_drop_pos()
        if drop is None:
            return None
        try:
            # 朝敌方主基地方向再往里走一点（跨越矿线）
            enemy: Point2 = self.ai.enemy_start_locations[0]
            return drop.towards(enemy, _DROP_PATROL_OFFSET)
        except Exception:
            return drop

    # ── 辅助：埋地 ───────────────────────────────────────────────────────

    def _burrow_all_mines(self) -> None:
        """对所有散落的寡妇雷下 BURROWDOWN 指令。"""
        for mine in self._free_mines():
            with contextlib.suppress(Exception):
                mine(AbilityId.BURROWDOWN_WIDOWMINE)

    # ── 辅助：reserved ───────────────────────────────────────────────────

    def _reserve(self, unit: Any) -> None:
        """标 Reserved —— 每 tick 重设，独占控制权。"""
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)

    # ── 状态机工具 ───────────────────────────────────────────────────────

    def _set_state(self, new_state: DropState) -> None:
        if new_state != self._state:
            logger.debug(
                "WidowMineDrop state: %s → %s (t=%.1fs)",
                self._state.value,
                new_state.value,
                self.ai.time,
            )
            self._state = new_state
            self._state_entered_ts = self.ai.time

    def _should_recall_mine(self, mine: Any, entered_ts: float) -> bool:
        """判断 burrowed 寡妇雷是否该回收：开火了（weapon_cooldown > 0）或超时。

        这个方法把决策逻辑提取为纯函数，方便单测。
        """
        fired = float(getattr(mine, "weapon_cooldown", 0.0)) > 0.0
        elapsed = float(getattr(self, "_now", 0.0)) - entered_ts
        return fired or elapsed > _RECALL_TIMEOUT_S
