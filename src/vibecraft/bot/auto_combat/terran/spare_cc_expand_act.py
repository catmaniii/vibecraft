"""SpareCcExpandAct — 通用"空闲 CC 飞去开矿"逻辑（#560，2026-06-21 用户 reframe）。

玩家可预先在家/任意处造一个额外 CommandCenter（不在采矿点）。本 act 检测到这种**空闲 spare CC**
后，把它 LIFT 起飞、飞到**最近的未占扩张点**、LAND 落地（= 用现成的 CC 开矿，省去新造）。
没有 spare CC 时**完全 no-op** → 对所有不造额外 CC 的现有 build 零影响。

真机已核对（scripts/cclift_probe.py，PASS）的硬约束：
  - CC 只有 **idle（不在产 SCV）** 时才有 LIFT_COMMANDCENTER ability。主基常年产兵 → 没 LIFT。
    故 spare CC 判定必须含 is_idle；玩家那个额外 CC 不产兵就是 idle。
  - 落点**起飞前锁死**（CLAUDE.md 移动靶铁律）：lift 那一刻就把目标扩张点缓存进 _target_by_tag，
    飞行中每帧幂等 move 同一点，到点 LAND 同一点。绝不每帧重选落点（否则追移动靶落不下，#543 坑）。
  - **卡飞逃生**：flying 超过 _STUCK_FLYING_S 还没落地 → 就地 LAND（current pos），不无限卡飞。

判定：
  spare CC = ready COMMANDCENTER + is_idle + 周围 _MINE_NEAR 内无矿（= 不在采矿点的停放 CC）。
  free expansion = expansion_locations 里 周围有矿、且无己方 townhall 占用 的点。

环境变量：VIBECRAFT_SPARECC_TRACE=1 → 输出 greppable SPARECCTRACE 行（自验用）。
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

_TRACE: bool = bool(os.environ.get("VIBECRAFT_SPARECC_TRACE"))

# spare CC 周围这半径内有矿 = 它其实在采矿点（不是 spare）→ 不碰
_MINE_NEAR: float = 10.0
# 扩张点周围这半径内有己方 townhall = 已占用
_TH_OCCUPIED: float = 6.0
# 扩张点周围这半径内有矿 = 是个真矿点（值得开）
_EXP_HAS_MINE: float = 10.0
# 距落点 ≤ 此值 = 到了 → LAND
_LAND_RADIUS: float = 4.0
# flying 超过这么久（游戏秒）还没落地 → 就地迫降（卡飞逃生）
_STUCK_FLYING_S: float = 25.0
# 锁了 target 但这么久还没起飞 → 放弃（LIFT 真发不出，避免无限重试）
_LIFT_GIVEUP_S: float = 20.0


class SpareCcExpandAct(ActBase):  # type: ignore[misc]
    """空闲 spare CC → 飞到最近未占扩张点落地。无 spare CC 时 no-op。"""

    def __init__(self) -> None:
        super().__init__()
        # per-tag 锁定落点（起飞前锁，飞行中不变）
        self._target_by_tag: dict[int, Point2] = {}
        # per-tag 起飞时刻（卡飞逃生计时）
        self._lift_time_by_tag: dict[int, float] = {}
        self._traced_tags: set[int] = set()
        self._lifted_ok_traced: set[int] = set()
        # 锁了 target 但 LIFT 始终发不出的 CC（放弃，不再重试，避免无限刷）
        self._gave_up: set[int] = set()
        self._diag_traced: set[int] = set()
        self._landing_traced: set[int] = set()

    async def execute(self) -> bool:
        try:
            await self._tick()
        except Exception:
            logger.exception("SpareCcExpandAct._tick error")
        return True  # non-blocking

    def _bypass(self, ability: AbilityId, unit: Any, target: Point2 | None) -> None:
        """经 _vibecraft_bypass_actions 直发命令（prevent_double=False）。

        根因（common_bot.py:740 已记）：python-sc2 prevent_double_actions 对 orders==[] 的单位
        丢弃 UnitCommand → 直接 cc(LIFT)/f(LAND) 永不发到 SC2（spare CC orders=0，必中此坑）。
        修法：构造 UnitCommand(prevent_double=False) 放进 bot 的 bypass 队列，由 common_bot
        on_step 在 super 之后串行 _do_actions(bypass, prevent_double=False) 发出。
        """
        from sc2.unit_command import UnitCommand as _SC2UnitCmd

        if not hasattr(self.ai, "_vibecraft_bypass_actions"):
            self.ai._vibecraft_bypass_actions = []  # type: ignore[attr-defined]
        self.ai._vibecraft_bypass_actions.append(_SC2UnitCmd(ability, unit, target, False))

    async def _tick(self) -> None:
        ai = self.ai
        now = float(ai.time)

        # ── 1. 处理正在飞的 spare CC（落地 / 卡飞逃生）──────────────────────────
        flying = ai.structures(UnitTypeId.COMMANDCENTERFLYING)
        live_flying_tags = {f.tag for f in flying}
        for f in flying:
            tgt = self._target_by_tag.get(f.tag)
            if tgt is None:
                continue  # 不是本 act 起飞的，不管
            if _TRACE and f.tag not in self._lifted_ok_traced:
                self._lifted_ok_traced.add(f.tag)
                logger.warning(
                    "SPARECCTRACE lifted_ok tag=%d pos=(%.1f,%.1f) dist_to_target=%.1f",
                    f.tag,
                    f.position.x,
                    f.position.y,
                    f.distance_to(tgt),
                )
            t0 = self._lift_time_by_tag.get(f.tag, now)
            if now - t0 > _STUCK_FLYING_S:
                # 卡飞逃生：就地迫降（落点被占/够不着 → 别无限卡飞）
                self._bypass(AbilityId.LAND_COMMANDCENTER, f, f.position)
                if _TRACE:
                    logger.warning(
                        "SPARECCTRACE stuck_land tag=%d pos=(%.1f,%.1f)",
                        f.tag,
                        f.position.x,
                        f.position.y,
                    )
                continue
            # LAND 带落点：飞行建筑会自动飞到该点再落地（无需单独 move，省去 move 也踩 orders==[] 坑）。
            # 每帧幂等重发同一锁定落点（移动靶铁律：落点起飞前已锁，不变）。
            self._bypass(AbilityId.LAND_COMMANDCENTER, f, tgt)
            if _TRACE and f.tag not in self._landing_traced:
                self._landing_traced.add(f.tag)
                logger.warning(
                    "SPARECCTRACE landing tag=%d target=(%.1f,%.1f) dist=%.1f",
                    f.tag,
                    tgt.x,
                    tgt.y,
                    f.distance_to(tgt),
                )

        # 清理已落地/消失的 tag 状态
        gone = (
            set(self._target_by_tag)
            - live_flying_tags
            - {c.tag for c in ai.townhalls(UnitTypeId.COMMANDCENTER)}
        )
        for tag in gone:
            self._target_by_tag.pop(tag, None)
            self._lift_time_by_tag.pop(tag, None)

        # ── 2. 找 spare CC（ready + idle + 不在矿区 + 还没飞起来）──────────────
        # 关键（修 lift 被拒后不重试的 bug）：**不**用"已锁定 target"排除 CC。一个已锁 target
        # 但 LIFT 被拒还停在地面的 CC，必须继续重试 LIFT，否则一次拒绝就永远卡地面。
        # 只排除已经在飞的（branch 1 处理）和已放弃的。
        spare = [
            c
            for c in ai.townhalls(UnitTypeId.COMMANDCENTER).ready
            if c.is_idle
            and c.tag not in self._gave_up
            and not ai.mineral_field.closer_than(_MINE_NEAR, c.position).exists
        ]
        if not spare:
            return

        # 未占扩张点（有矿、无 townhall）
        free_exps: list[Point2] = []
        with contextlib.suppress(Exception):
            free_exps = [
                p
                for p in (getattr(ai, "expansion_locations_list", []) or [])
                if not ai.townhalls.closer_than(_TH_OCCUPIED, p).exists
                and ai.mineral_field.closer_than(_EXP_HAS_MINE, p).exists
            ]

        for cc in spare:
            # 锁定落点（仅第一次；CLAUDE.md 移动靶铁律：起飞前锁死，之后不改）
            tgt = self._target_by_tag.get(cc.tag)
            if tgt is None:
                if not free_exps:
                    continue
                target = min(free_exps, key=lambda p: p.distance_to(cc.position))
                tgt = Point2((target.x, target.y))
                self._target_by_tag[cc.tag] = tgt
                self._lift_time_by_tag[cc.tag] = now
                # 该扩张点已被认领，别让同帧另一个 spare CC 抢同一点
                free_exps = [p for p in free_exps if p.distance_to(target) > 1.0]
            # 放弃门：锁了 target 但 _LIFT_GIVEUP_S 内还没飞起来 → 放弃（lift 真发不出）
            if now - self._lift_time_by_tag.get(cc.tag, now) > _LIFT_GIVEUP_S:
                self._gave_up.add(cc.tag)
                self._target_by_tag.pop(cc.tag, None)
                if _TRACE:
                    logger.warning("SPARECCTRACE giveup tag=%d (LIFT 始终发不出)", cc.tag)
                continue
            # 重试式 LIFT：只在 LIFT 真可用时发（CC 必须 idle 才有 LIFT，真机已验）。
            # 每帧重发直到它变成 COMMANDCENTERFLYING（branch 1 接管）。
            with contextlib.suppress(Exception):
                abilities = await self.ai.get_available_abilities(cc)
                has_lift = AbilityId.LIFT_COMMANDCENTER in abilities
                if _TRACE and cc.tag not in self._diag_traced:
                    self._diag_traced.add(cc.tag)
                    logger.warning(
                        "SPARECCTRACE diag tag=%d type=%s is_idle=%s is_flying=%s orders=%d "
                        "has_lift=%s n_ab=%d ab=%s",
                        cc.tag,
                        cc.type_id.name,
                        cc.is_idle,
                        getattr(cc, "is_flying", "?"),
                        len(cc.orders),
                        has_lift,
                        len(abilities),
                        sorted(a.name for a in abilities)[:8],
                    )
                if has_lift:
                    self._bypass(AbilityId.LIFT_COMMANDCENTER, cc, None)
                    if _TRACE and cc.tag not in self._traced_tags:
                        self._traced_tags.add(cc.tag)
                        logger.warning(
                            "SPARECCTRACE lift tag=%d from=(%.1f,%.1f) target=(%.1f,%.1f)",
                            cc.tag,
                            cc.position.x,
                            cc.position.y,
                            tgt.x,
                            tgt.y,
                        )
