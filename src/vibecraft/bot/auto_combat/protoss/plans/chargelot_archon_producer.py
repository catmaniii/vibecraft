"""ChargelotArchonProducer: dt_drop_iac 主力期 zealot + archon 一体产能。

2026-05-24 用户:"叉子刷得有点多,气多时优先刷 DT 合白球。整合到叉球一波
act 里,不要单独搞合成白球 act"。

设计
====
**替代** `ProtossUnit(ZEALOT, 60) + MergeArchonAtHome` 两 step。**单一 act**
按当前资源 + home DT 数动态决定:
- 优先 home DT pair → MORPH_ARCHON(合白球)
- 气够(>= 125 V) + home DT < 2 → train DT 补充
- 主力 zealot 暴(cap 60,矿够即 train)

为什么单 act 而不是两个独立 act 串
==================================
试过 MergeArchonAtHome + AutoArchonProducer 独立两个 act,实测两者
独立 _merging_tags 列表互相 race,同帧给同对 DT 发 MORPH_ARCHON
两次 → SC2 合不了。整合到一个 act 共用 _merging_tags 解决冲突。

Reserved 防 sharpy 派前线
========================
新 train 的 DT 出生在 BG/WG,sharpy ZoneAttack 每 tick 把 Idle units
标 Attacking 派前线。本 act 每 tick 给 home DT(距 townhall < 30)set
Reserved → ZoneAttack 看 free_units 不含 Reserved → 不抢。

zealot 不 Reserved — 是要去前线打架的。

行为
====
每 tick(gate _army_phase_active 才进):
1. 清理已合并完成(idle/不在场)的 DT tag
2. 数 home DT(距 townhall < _HOME_DIST,排除 merging)+ set Reserved
3. >= 2 home DT → MORPH_ARCHON pair(UnitCommand combine 成 raw)
4. < 2 home DT + 资源够(>= 125 M + 125 V) → train 1 DT
5. zealot 未到 cap + 矿够 → train 1 zealot
   (warpgate done 后 ai.train 自动走 warp_in 路径)
"""

from __future__ import annotations

import contextlib
import logging

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit_command import UnitCommand
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts.act_base import ActBase

logger = logging.getLogger(__name__)

# DT 距 townhall 小于此值算"home"(只合家里 DT,前线偷家 DT 不动)
_HOME_DIST: float = 30.0
# DT cost(python-sc2 cost 数据)
_DT_COST_MINERALS: int = 125
_DT_COST_VESPENE: int = 125
# Zealot cost
_ZEALOT_COST_MINERALS: int = 100
# Zealot 总 cap(原 ProtossUnit(ZEALOT, 60))
_ZEALOT_CAP: int = 60


class ChargelotArchonProducer(ActBase):  # type: ignore[misc]
    """主力期 zealot + archon 一体产能(替代 ProtossUnit ZEALOT + MergeArchonAtHome)。"""

    def __init__(self) -> None:
        super().__init__()
        # 已发出 MORPH_ARCHON 的 DT tag(防同帧重复;合完 idle 后清)
        self._merging_tags: set[int] = set()

    async def execute(self) -> bool:
        # ── 1. 清理已合并完成的 DT(变 idle 或不在场) ──
        try:
            dts = self.cache.own(UnitTypeId.DARKTEMPLAR).ready
        except Exception:
            dts = []
        active_tags = {dt.tag for dt in dts}
        self._merging_tags &= active_tags
        for dt in dts:
            if dt.tag in self._merging_tags and dt.is_idle:
                with contextlib.suppress(Exception):
                    self.roles.clear_task(dt)
                self._merging_tags.discard(dt.tag)

        # ── 2. home DT(距 townhall < _HOME_DIST,排除 merging) + Reserved ──
        try:
            home_dts = [
                dt
                for dt in dts
                if dt.tag not in self._merging_tags
                and any(dt.distance_to(th) < _HOME_DIST for th in self.ai.townhalls)
            ]
        except Exception:
            home_dts = []
        # 标 Reserved 防 sharpy 派前线
        for dt in home_dts:
            with contextlib.suppress(Exception):
                self.roles.set_task(UnitTask.Reserved, dt)

        # ── 3. 合 archon pair ──
        if len(home_dts) >= 2:
            a, b = home_dts[0], home_dts[1]
            self._merging_tags.add(a.tag)
            self._merging_tags.add(b.tag)
            try:
                self.ai.do(UnitCommand(AbilityId.MORPH_ARCHON, a))
                self.ai.do(UnitCommand(AbilityId.MORPH_ARCHON, b))
                logger.info(
                    "ChargelotArchonProducer merging 2 home DT (tags=%d,%d)",
                    a.tag,
                    b.tag,
                )
            except Exception as exc:
                logger.warning("merge MORPH_ARCHON fail: %s", exc)
            # merge 这帧不再 train DT/zealot(把 building action slot 让给 morph)
            return True

        # ── 4. 气多 + home DT < 2 → train 1 DT 给下次合用 ──
        try:
            m = int(self.ai.minerals)
            v = int(self.ai.vespene)
        except Exception:
            m = v = 0
        if len(home_dts) < 2 and m >= _DT_COST_MINERALS and v >= _DT_COST_VESPENE:
            with contextlib.suppress(Exception):
                self.ai.train(
                    UnitTypeId.DARKTEMPLAR,
                    amount=1,
                    train_only_idle_buildings=False,
                )

        # ── 5. zealot 暴(cap 60,矿够即 train) ──
        try:
            existing_zealots = int(self.ai.units(UnitTypeId.ZEALOT).amount) + int(
                self.ai.already_pending(UnitTypeId.ZEALOT)
            )
        except Exception:
            existing_zealots = 0
        if existing_zealots < _ZEALOT_CAP and m >= _ZEALOT_COST_MINERALS:
            with contextlib.suppress(Exception):
                # warpgate done 后 ai.train 走 warp_in 路径
                self.ai.train(
                    UnitTypeId.ZEALOT,
                    amount=1,
                    train_only_idle_buildings=False,
                )

        return True
