"""PersistentMacro：三族共享的持续 macro 层。

各剧本 create_plan 把 worker cap 链 + AutoPylon/AutoDepot/AutoOverLord + Expand
替换为 RacePersistentMacro(config).acts()，让剧本完成后 macro 持续、策略切换不断运营。

用法示例（神族）::

    from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, ProtossPersistentMacro

    class MyProtossPlan(KnowledgeBot):
        async def create_plan(self) -> BuildOrder:
            macro = ProtossPersistentMacro(MacroConfig(probe_cap=80))
            return BuildOrder(
                *macro.acts(),   # worker chrono + AutoPylon + Expand
                # ... 其余战术 acts ...
            )

MacroConfig.probe_cap=80 = 满 4 矿饱和（神族 20×4=80 农）；
默认不卡 22 是因为职业玩家一波失败也要转 macro，统一 80 让 bot 持续出农。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MacroConfig:
    """持续 macro 配置。

    probe_cap: 工人上限（默认 80 = 满 4 矿饱和）。
        神族: 20 农/矿 × 4 矿 = 80
        虫族: 16 工蜂/矿 × 4 矿 ≈ 64；给 80 是宽松上限，不强制停
        人族: 16 SCV/矿 × 4 矿 ≈ 64；同上

    expansion_cap: 自动开矿目标（默认 4 矿）。
        4 矿是主流高端职业标准，足以支撑 Skytoss / 晚期科技。

    chrono_target: chrono boost 的目标单位（默认 "probe" = 神族农民）。
        虫族 / 人族传入自己的 worker type name（不影响 AutoOverLord/AutoDepot 逻辑）。

    auto_pylon: 是否启用 AutoPylon（神族用）/ AutoDepot（人族）/ AutoOverLord（虫族）。
        各族 wrapper 内映射到对应 act，不是同一个 class。
    """

    probe_cap: int = 80
    expansion_cap: int = 4
    chrono_target: str = "probe"
    auto_pylon: bool = True
    # 每矿造几个气矿（默认 2 = 满气）。build-aware 对吃矿为主的兵种（蟑螂/小狗）调成 1，
    # 否则气收入 >> 兵种气耗 → 气浮高、钱花不干净（2026-06-15 用户：调采气优先级）。
    gas_per_base: int = 2

    # 派生字段（由 probe_cap 推断分阶段 cap）
    # 格式：[cap_at_1base, cap_at_2base, cap_at_3base, cap_at_4base]
    # 由工厂方法自动填充；手动指定时覆盖此字段。
    staged_caps: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.staged_caps:
            # 按矿数线性插值：1 矿 cap/4, 2 矿 cap/2, 3 矿 3cap/4, 4 矿 cap
            p = self.probe_cap
            self.staged_caps = [
                max(1, p // 4),
                max(1, p // 2),
                max(1, (p * 3) // 4),
                p,
            ]


# =========================================================================
# 神族 PersistentMacro
# =========================================================================


class ProtossPersistentMacro:
    """神族持续 macro（Probe + AutoPylon + Expand）。

    依赖 sharpy acts，只在子进程里 import（不在顶层 import sharpy）。
    单测通过 .acts() 的返回数量 / 类型验证，不真正 import sc2。
    """

    def __init__(self, config: MacroConfig | None = None) -> None:
        self.config = config or MacroConfig()

    def acts(self) -> list[Any]:
        """返回 sharpy act 列表（供 BuildOrder(*macro.acts(), ...) 解包）。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sharpy.plans import Step, StepBuildGas
        from sharpy.plans.acts import ActUnit, Expand
        from sharpy.plans.acts.protoss import AutoPylon, ChronoUnit
        from sharpy.plans.require import Gas, RequireCustom, UnitExists

        cfg = self.config
        result: list[Any] = []

        # 偷矿前置：农民 staged cap 档按**非 stealth** 基地数解锁。否则 stealth Nexus
        # 让 NEXUS 数 +1 → 解锁更高 cap → 主矿超产堆农民（真机实测主矿 1 基地堆到 35）。
        # 非偷矿局 stealth 集合空 → 等价 townhalls.ready >= n。
        def _non_stealth_nexus_ge(n: int) -> Any:
            def _check(ai: Any) -> bool:
                vc = getattr(getattr(ai, "knowledge", None), "vibecraft", None)
                stealth = getattr(vc, "stealth_townhall_tags", None) or set()
                cnt = sum(1 for th in ai.townhalls.ready if th.tag not in stealth)
                return cnt >= n

            return RequireCustom(_check)

        # 探机 chrono（到 probe_cap 停）
        result.append(
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, cfg.probe_cap, include_pending=True),
            )
        )

        # AutoPylon（自动补人口，永久后台）
        if cfg.auto_pylon:
            result.append(AutoPylon())

        # 探机分阶段 ActUnit cap 链
        caps = cfg.staged_caps
        result.append(ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, caps[0]))
        if len(caps) > 1:
            result.append(
                Step(
                    _non_stealth_nexus_ge(2),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, caps[1]),
                )
            )
        if len(caps) > 2:
            result.append(
                Step(
                    _non_stealth_nexus_ge(3),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, caps[2]),
                )
            )
        if len(caps) > 3:
            result.append(
                Step(
                    _non_stealth_nexus_ge(4),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, caps[3]),
                )
            )

        # 自动扩张到 expansion_cap 矿
        for i in range(2, cfg.expansion_cap + 1):
            result.append(Expand(i))

        # 气矿跟随 NX 数：从第 3 个 NX 起，每多一个 NX 补 2 个 BA
        # opening plan（iac_2base 等）自己显式管前 2 个 NX 的 4 个气矿，
        # PersistentMacro 只兜底 3 矿及之后的气矿，避免重复下气
        # Gas(N×100) = 气矿堆超阈值停采，防 vespene 过剩
        for nex in range(3, cfg.expansion_cap + 1):
            result.append(
                Step(
                    UnitExists(UnitTypeId.NEXUS, nex),
                    StepBuildGas(nex * 2, skip=Gas(nex * 100)),
                )
            )

        return result


# =========================================================================
# 虫族 PersistentMacro
# =========================================================================


class ZergPersistentMacro:
    """虫族持续 macro（Drone + AutoOverLord + Expand）。

    虫族农民是 Drone，补人口是 AutoOverLord，基地是 Hatchery。
    """

    def __init__(self, config: MacroConfig | None = None) -> None:
        self.config = config or MacroConfig(chrono_target="drone")

    def acts(self) -> list[Any]:
        """返回 sharpy act 列表。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sharpy.plans import Step, StepBuildGas
        from sharpy.plans.acts import ActUnit, Expand
        from sharpy.plans.acts.zerg import AutoOverLord
        from sharpy.plans.require import Gas, UnitExists

        cfg = self.config
        result: list[Any] = []

        # AutoOverLord（自动补人口，永久后台）
        if cfg.auto_pylon:
            result.append(AutoOverLord())

        # 工蜂分阶段 ActUnit cap 链
        # 虫族基地升 Lair/Hive 后 UnitExists(HATCHERY, N) 通过 sharpy patch 会合并计数
        caps = cfg.staged_caps
        result.append(ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, caps[0]))
        if len(caps) > 1:
            result.append(
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, caps[1]),
                )
            )
        if len(caps) > 2:
            result.append(
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 3),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, caps[2]),
                )
            )
        if len(caps) > 3:
            result.append(
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 4),
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, caps[3]),
                )
            )

        # 自动扩张到 expansion_cap 矿
        for i in range(2, cfg.expansion_cap + 1):
            result.append(Expand(i))

        # 气矿跟随孵化场数：从第 3 个基地起，每多一个基地补 2 个气矿（BE）
        # opening plan 通常自己管前 2 个基地的气矿，这里只兜底 3 矿及之后
        # Gas(N×100) 防 vespene 过剩
        for hatch in range(3, cfg.expansion_cap + 1):
            result.append(
                Step(
                    UnitExists(UnitTypeId.HATCHERY, hatch),
                    StepBuildGas(hatch * cfg.gas_per_base, skip=Gas(hatch * 100)),
                )
            )

        return result


# =========================================================================
# 人族 PersistentMacro
# =========================================================================


class TerranPersistentMacro:
    """人族持续 macro（SCV + AutoDepot + Expand）。

    人族农民是 SCV，补人口是 AutoDepot，基地是 CommandCenter。
    """

    def __init__(self, config: MacroConfig | None = None) -> None:
        self.config = config or MacroConfig(chrono_target="scv")

    def acts(self) -> list[Any]:
        """返回 sharpy act 列表。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sharpy.plans import Step, StepBuildGas
        from sharpy.plans.acts import ActUnit, Expand
        from sharpy.plans.acts.terran import AutoDepot
        from sharpy.plans.require import Gas, UnitExists

        cfg = self.config
        result: list[Any] = []

        # AutoDepot（自动补人口，永久后台）
        if cfg.auto_pylon:
            result.append(AutoDepot())

        # SCV 分阶段 ActUnit cap 链
        # 人族基地升 OC/PF 后 UnitExists(COMMANDCENTER, N) 通过 sharpy 等价计数
        caps = cfg.staged_caps
        result.append(ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, caps[0]))
        if len(caps) > 1:
            result.append(
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 2),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, caps[1]),
                )
            )
        if len(caps) > 2:
            result.append(
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 3),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, caps[2]),
                )
            )
        if len(caps) > 3:
            result.append(
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, 4),
                    ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, caps[3]),
                )
            )

        # 自动扩张到 expansion_cap 矿
        for i in range(2, cfg.expansion_cap + 1):
            result.append(Expand(i))

        # 气矿跟随指挥中心数：从第 3 个 BC 起，每多一个 BC 补 2 个精炼厂（BR）
        # opening plan 通常自己管前 2 个 BC 的气矿，这里只兜底 3 矿及之后
        # Gas(N×100) 防 vespene 过剩
        for cc in range(3, cfg.expansion_cap + 1):
            result.append(
                Step(
                    UnitExists(UnitTypeId.COMMANDCENTER, cc),
                    StepBuildGas(cc * 2, skip=Gas(cc * 100)),
                )
            )

        return result
