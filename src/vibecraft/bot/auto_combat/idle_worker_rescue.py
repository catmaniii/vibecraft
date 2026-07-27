"""通用「闲置农民派去采矿」兜底（2026-07-12 首见于 nydus，2026-07-20 抽为通用 + 全局化）。

背景与根因（务必看，别再踩）：
- **`ai.workers.idle` 只算「完全无 order」的农民**——真机里那些「有 order 却没在干活」的农民
  （发呆的 move/stop、采到一半卡住），SC2 **不**把它们 flag 成 idle → `workers.idle` / telemetry
  `economy.idle_workers` 都测 **0**，肉眼在 UI 却看得见几个闲置农民。**别用 workers.idle 判断
  「有没有闲置农民」——它测不到这种真闲置**（2026-07-20 用户 muta_ling_bane 复盘：曾据此误判
  「干净局 idle≈0 → 无闲置」，其实指标本身瞎）。
- 原来只有 nydus 挂了这个兜底，别的 build（muta_ling_bane 龙狗毒爆等）没有 → 卡住的闲置农民
  无人管、越堆越多，玩家「闲置农民回去采矿」也救不动。2026-07-20 改为**全局**（common_bot
  on_step 里对所有 build 统一跑 `rescue_idle_workers`），任何 build 都不再漏。

做法：**更鲁棒的「有效闲置」检测**——SC2 idle，或（既不采矿 is_gathering、不搬运
is_carrying_resource、不返回 is_returning，且没在建造）持续 `IDLE_PERSIST_S` 秒。抓到就送去
**当前最缺农民的基地**最近矿（修「农民全堆某矿、别的矿饿死」）。**不打断正在建造/采矿/搬运的
农民**、**排除玩家 claim（`_llm_controlled_tags`）的农民**。非阻塞。持续 3s 门槛躲开 SpeedMining
的毫秒级 move 微操（那期间 is_gathering 仍为 True）。

单一源：核心逻辑在 `rescue_idle_workers()` 纯函数；`IdleWorkerToMineAct`（plan-act 形态，
向后兼容）与 common_bot on_step（全局形态）都调它。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

try:
    from sharpy.plans.acts.act_base import ActBase
except Exception:  # sharpy 不可用(无 SC2 的单测)→ fallback。纯函数 rescue_idle_workers
    # 不依赖 sharpy(common_bot on_step 全局路径用它);只有 plan-act 形态 IdleWorkerToMineAct
    # 需要 ActBase,fallback=object 时该 class 仅占位(全局化后已不再挂进任何 build plan)。
    ActBase = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

IDLE_PERSIST_S: float = 3.0  # 连续这么久没在干活才算真闲置（躲开 SpeedMining 的毫秒级 move 微操）
_LOG_GAP_S: float = 5.0  # rescued 日志最小间隔


def rescue_idle_workers(
    ai: Any, state: dict[str, Any], idle_persist_s: float = IDLE_PERSIST_S
) -> list[int]:
    """检测「有效闲置」农民 → 送去最缺农民基地的最近矿。返回本次 rescued 的 tag 列表。

    `state` 是调用方持有的持久 dict（跨帧），本函数原地维护 state["nws"]（tag→起始没干活时刻）
    与 state["last_log"]（日志节流）。见模块 docstring 的检测规则。**异常全吞、返回已处理部分**。
    """
    rescued: list[int] = []
    nws: dict[int, float] = state.setdefault("nws", {})
    with contextlib.suppress(Exception):
        player_tags = set(getattr(ai, "_llm_controlled_tags", None) or set())
        mfs = ai.mineral_field
        if not mfs:
            nws.clear()
            return rescued
        now = float(ai.time)
        ths = list(ai.townhalls.ready)
        # 每基地当前采矿农民数 → 找最缺的送过去（修某矿 [1,16] 饿死）。
        loads = {
            th.tag: ai.workers.filter(
                lambda u, _t=th: u.is_gathering and u.distance_to(_t) < 12
            ).amount
            for th in ths
        }
        alive = {w.tag for w in ai.workers}
        # 清掉已死单位的计时器（避免 dict 无限涨）。
        for t in [t for t in nws if t not in alive]:
            nws.pop(t, None)
        for w in ai.workers:
            if w.tag in player_tags:
                nws.pop(w.tag, None)
                continue
            # 在干活（采矿/搬运/返回）→ 清计时器，跳过。SpeedMining 的 move 微操期间
            # is_gathering 仍为 True（order 是 HARVEST），就算偶尔 False 也是毫秒级、攒不够门槛。
            working = (
                w.is_gathering
                or getattr(w, "is_returning", False)
                or getattr(w, "is_carrying_resource", False)
            )
            if working:
                nws.pop(w.tag, None)
                continue
            # 没在干活：记录起始时刻，连续够 idle_persist_s 秒才算真闲置（躲过渡态/微操）。
            since = nws.setdefault(w.tag, now)
            if now - since < idle_persist_s:
                continue
            # 送最缺农民的基地最近矿；取不到 → 最近矿兜底。
            target_mf = None
            if ths:
                neediest = min(ths, key=lambda t: loads.get(t.tag, 0))
                near = mfs.closer_than(12, neediest)
                target_mf = near.closest_to(w) if near else None
            if target_mf is None:
                target_mf = mfs.closest_to(w)
            w.gather(target_mf)
            nws.pop(w.tag, None)  # 已处理，重新计时
            rescued.append(w.tag)
        if rescued and now - state.get("last_log", -999.0) >= _LOG_GAP_S:
            state["last_log"] = now
            logger.info(
                "IDLEWORKER rescued=%d(持续闲置≥%.0fs) → 送最缺基地采矿 tags=%s",
                len(rescued),
                idle_persist_s,
                rescued[:6],
            )
    return rescued


class IdleWorkerToMineAct(ActBase):  # type: ignore[misc]
    """通用闲置农民兜底（plan-act 形态，向后兼容）。核心逻辑见 `rescue_idle_workers`。

    注：2026-07-20 起已在 common_bot on_step 全局跑 rescue_idle_workers，所有 build 自动覆盖；
    本 act 保留供显式 plan 需要（不再必须挂进 build 的 SequentialList）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._state: dict[str, Any] = {}

    async def execute(self) -> bool:
        rescue_idle_workers(self.ai, self._state)
        return True
