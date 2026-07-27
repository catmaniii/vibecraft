"""WorkerSaturationFloorAct: 通用农民饱和兜底（种族无关，恒生效）。

背景：`OpeningSustainAct` 靠 `knowledge.vibecraft.sustain_uncap_active` flag 触发，
切 persistent_doctrine 后该 flag 永不 set（director.py `persistent_set=True` 分支
主动跳过）→ doctrine 整局零农民兜底，农民卡在 plan 写死的低数或被埋葬在军队 step
之后。本 act 恒生效（不看任何 flag），把农民始终拉向 `sum(ideal_harvesters)`——
"开多少矿最终就得多少农民"是唯一基础规则。

设计 + 评审处置：docs/plans/2026-07-10-worker-saturation-floor-design.md
（尤其「## 评审处置」6 条 must-fix，本文件严格照做）：
  1. 虫族目标封顶（drone_budget，跟 opening_sustain_act.py 的 ZERG_WORKER_CAP/
     NON_ZERG_WORKER_CAP 同源，不各写一份）——虫族农民与军队抢同一 200 人口池。
  2. 挂载点：三族各自 `_wrap()`，顶层 BuildOrder 直接兄弟，绝不进 SequentialList。
  3. 子类化 sharpy `ActUnit`，只覆写动态 `to_count`——复用其 worker 计数 / 虫族
     larva-pending / cooldown / 非-priority 不 reserve（=软地板）语义。
  4. 验收走外部终态 telemetry，per-base 断言，覆盖 doctrine 切换路径。

与 `OpeningSustainAct` 职责正交：Sustain 管"续兵 + 加产能楼"（不动），Floor 只管
农民地板，不依赖 sustain_uncap_active（只作为 grace 门里的一个 OR 条件）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALID_RACES = frozenset({"PROTOSS", "ZERG", "TERRAN"})

# grace 门（评审 #3 / 设计 §A "触发"）：过早期不施压，尊重"短暂停农民"（all-in timing）。
# 2026-07-10（nydus 早期出兵被 Floor 抢 larva 的踩坑）：base>=2 在开二矿(~1:30)就触发、
# 把该出兵的早期窗口的 larva 全铺成农民 → all-in 出不了兵。改保守：base>=3（3 矿=真运营承诺，
# 2 矿 all-in 不算）或时间过 165s（~2:45，早期出兵窗之后）。早期农民由 build 自己的 DRONE 目标控，
# Floor 只做"早期窗之后拉向饱和"的兜底，别抢早期出兵的 larva。
_GRACE_S = 165.0

# sharpy ActUnit: 在 vendor/sharpy 可用时继承真实基类，否则 fallback object
# （同 opening_sustain_act.py 的 _ActBase 套路）。BuildOrder.merge_to_act 需要
# isinstance(act, ActBase) 检查（真实 sharpy 运行时）；ActUnit 是 ActBase 子类。
try:
    from sharpy.plans.acts import ActUnit as _ActUnit
except ImportError:
    _ActUnit = object


class WorkerSaturationFloorAct(_ActUnit):  # type: ignore[misc]
    """种族无关农民饱和地板。

    子类化 ActUnit(worker_type, from_building, to_count=动态)，只覆写 to_count 为
    property：每 tick 按当前 ideal_harvesters 之和（封顶 drone_budget）重新计算。
    `priority=False`（非-priority，不 reserve 资源）——这是**软地板**，军队 sibling
    act 同一帧仍能正常 train，不会因为 Floor 抢资源而卡军队生产。
    """

    def __init__(self, worker_type: Any, from_building: Any, drone_budget: int) -> None:
        # _drone_budget 必须在 super().__init__ 之前设：ActUnit.__init__ 里
        # `self.to_count = to_count` 会触发下面的 setter（no-op），但 is_done/execute
        # 随后随时可能读 to_count getter，getter 依赖 _drone_budget。
        self._drone_budget = drone_budget
        try:
            super().__init__(worker_type, from_building, to_count=9999, priority=False)
        except Exception:
            # sharpy 不可用（_ActUnit fallback=object）：手动设最小属性，供无 SC2 环境
            # 单测探测用（真实 is_done/execute 来自 ActUnit，此路径下不可用）。
            self.unit_type = worker_type
            self.from_building = from_building
            self.priority = False

    # ------------------------------------------------------------------
    # 动态 to_count：ActUnit.is_done 读 `self.get_unit_count() >= self.to_count`，
    # 每 tick 都会重新走 getter，天然做到"每 tick 重算目标"而不需要额外 hook execute()。
    # ------------------------------------------------------------------

    @property
    def to_count(self) -> int:
        ai = getattr(self, "ai", None)
        if ai is None:
            return 0
        if not self._grace_ok():
            # grace 门未过：不额外施压，冻结在当前数量（is_done 立即 True，Floor 本 tick
            # 不下训练指令）。用 get_unit_count() 而非 0，避免 is_done 恒 True 时误伤
            # ActUnit 内部其它读 to_count 的分支语义（目前只有 is_done 读，但保持语义准确）。
            return int(self.get_unit_count())
        # 快攻/all-in build 可声明 knowledge.vibecraft.worker_cap_override 封住农民上限
        # （2026-07-10：狗蟑快攻不该被 Floor 硬铺到饱和、抢走该出兵的 larva/矿。运营 build
        # 不声明 → 走饱和）。target = min(饱和, 种族封顶, build 声明的快攻封顶)。
        return min(self._ideal_sum(), self._drone_budget, self._worker_cap_override())

    def _worker_cap_override(self) -> int:
        """build 通过 knowledge.vibecraft.worker_cap_override 声明的农民封顶（快攻用）。
        未声明 → 大数（不封顶，走饱和）。

        **只在"还在打快攻"时生效**：快攻打完（sustain flag）/ 真转运营（4 矿+）→ 解封顶
        交回饱和（否则快攻没杀死转 doctrine 时农民被卡死在快攻封顶，又变运营缺农民）。

        2026-07-11 Fable5 复盘去掉了"6 分钟墙钟自动解封"：all-in 常常 6min 还没打出去
        （trace 里 canal 都没建成），墙钟一到就误判"转运营"疯铺农民（20→33）→ 一波该爆兵的
        窗口被农民分走矿。解封只认真实信号：sustain flag（一波已结束）或 4 矿（真运营）。
        """
        vc = getattr(getattr(self, "knowledge", None), "vibecraft", None)
        cap = getattr(vc, "worker_cap_override", None)
        if cap is None:
            return 10_000
        ai = getattr(self, "ai", None)
        # hard 封顶(2026-07-12 nydus all-in):sustain fallback@300s 会在 all-in 还没投送出去时就
        # 解封农民 → 农民饱和吃光攒兵 larva、蟑螂出不来。build 声明 worker_cap_hard=True 时,封顶在
        # sustain_uncap 下仍生效(保 larva 给军队),直到 build 自己清 hard(canal 落地=all-in 已投送)。
        if getattr(vc, "worker_cap_hard", False):
            try:
                return int(cap)
            except (TypeError, ValueError):
                return 10_000
        if getattr(vc, "sustain_uncap_active", False):
            return 10_000  # opening 完成/续期 → 解封顶
        if ai is not None and int(ai.townhalls.ready.amount) >= 4:
            return 10_000  # 真转运营（4 矿+）→ 解封顶
        try:
            return int(cap)
        except (TypeError, ValueError):
            return 10_000

    @to_count.setter
    def to_count(self, _value: int) -> None:
        # 动态属性：忽略 ActUnit.__init__ 里 `self.to_count = to_count` 的初值赋值，
        # 真实目标永远由 getter 现算。
        pass

    def _grace_ok(self) -> bool:
        """grace 门：base_count>=2 或 时间过了 grace 或 sustain_uncap_active 已开。"""
        ai = self.ai
        base_count = int(ai.townhalls.ready.amount)
        if base_count >= 3:  # 3 矿=真运营承诺(2 矿 all-in 不算，别抢早期出兵 larva)
            return True
        if float(ai.time) > _GRACE_S:
            return True
        vc = getattr(getattr(self, "knowledge", None), "vibecraft", None)
        return bool(getattr(vc, "sustain_uncap_active", False))

    def _ideal_sum(self) -> int:
        """目标 = sum(己方 townhall.ready.ideal_harvesters) + sum(己方 gas.ready.ideal_harvesters)。

        `.ready` 过滤在建 townhall/gas —— ideal_harvesters 对已完工建筑立即可信
        （sc2 unit.py:1256），在建的还没有稳定 ideal 值，不计入目标（评审确认点 #2）。
        """
        ai = self.ai
        total = sum(int(th.ideal_harvesters) for th in ai.townhalls.ready)
        total += sum(int(g.ideal_harvesters) for g in ai.gas_buildings.ready)
        return total


def make_worker_floor(race: str) -> WorkerSaturationFloorAct:
    """race dispatch：造对应 (worker_type, from_building, drone_budget) 的 Floor 实例。

    drone_budget 复用 opening_sustain_act.py 的 ZERG_WORKER_CAP / NON_ZERG_WORKER_CAP
    （评审 #1，同源，不各写一份）。
    """
    if race not in _VALID_RACES:
        raise ValueError(f"make_worker_floor: invalid race={race!r}; must be one of {_VALID_RACES}")

    from sc2.ids.unit_typeid import UnitTypeId as U

    from vibecraft.bot.auto_combat.opening_sustain_act import (
        NON_ZERG_WORKER_CAP,
        ZERG_WORKER_CAP,
    )

    if race == "PROTOSS":
        return WorkerSaturationFloorAct(U.PROBE, U.NEXUS, NON_ZERG_WORKER_CAP)
    if race == "ZERG":
        return WorkerSaturationFloorAct(U.DRONE, U.LARVA, ZERG_WORKER_CAP)
    return WorkerSaturationFloorAct(U.SCV, U.COMMANDCENTER, NON_ZERG_WORKER_CAP)
