"""SC2 detector + anti-air 范围数据（Liquipedia 权威 LotV）。

vibecraft_micro_dt + dt_micro 两个模块的数据底座，避免硬编码散落。
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId

# =========================================================================
# Detector 范围（探测到 cloak DT 所需距离）
# =========================================================================
# 数据来源: https://liquipedia.net/starcraft2/Detector
# 所有标准 detector range 11；Observer/Overseer 升级后 sight +25%（不能移动）
DETECTOR_RANGES: dict[UnitTypeId, float] = {
    UnitTypeId.OBSERVER: 11.0,
    UnitTypeId.OBSERVERSIEGEMODE: 13.75,
    UnitTypeId.OVERSEER: 11.0,
    UnitTypeId.OVERSEERSIEGEMODE: 13.75,
    UnitTypeId.RAVEN: 11.0,
    UnitTypeId.SPORECRAWLER: 11.0,
    UnitTypeId.MISSILETURRET: 11.0,
    UnitTypeId.PHOTONCANNON: 11.0,
}
# Scanner Sweep (Orbital Command 能力) 半径 13，持续 9s
SCANNER_SWEEP_RADIUS: float = 13.0

# =========================================================================
# 反空威胁范围（棱镜需要保持距离的敌方单位）
# =========================================================================
# Liquipedia 各单位 weapon range；棱镜距 AA 单位 > AA_THREAT_RANGES[type] + 3
# 才视为安全（3 缓冲给反应 + sight 滞后）
AA_THREAT_RANGES: dict[UnitTypeId, float] = {
    # Zerg
    UnitTypeId.QUEEN: 7.0,
    UnitTypeId.MUTALISK: 3.0,
    UnitTypeId.HYDRALISK: 6.0,  # 升级 +1
    UnitTypeId.CORRUPTOR: 5.0,
    UnitTypeId.SPORECRAWLER: 7.0,
    # Terran
    UnitTypeId.MARINE: 5.0,  # 6 with stim 偶发，保守按 5
    UnitTypeId.WIDOWMINE: 5.0,
    UnitTypeId.THOR: 11.0,  # Thor anti-air 11
    UnitTypeId.VIKINGFIGHTER: 9.0,
    UnitTypeId.MISSILETURRET: 7.0,
    UnitTypeId.CYCLONE: 6.0,
    UnitTypeId.BUNKER: 5.0,  # 装枪兵 5 距
    UnitTypeId.BATTLECRUISER: 6.0,
    # Protoss
    UnitTypeId.STALKER: 6.0,
    UnitTypeId.PHOENIX: 5.0,  # 范围 weapon
    UnitTypeId.PHOTONCANNON: 7.0,
    UnitTypeId.SENTRY: 5.0,
    UnitTypeId.ARCHON: 3.0,
    UnitTypeId.VOIDRAY: 6.0,
    UnitTypeId.TEMPEST: 14.0,
    UnitTypeId.CARRIER: 8.0,  # interceptors range ~8
}

# =========================================================================
# Warp Prism load (pickup) range —— LotV patch 4.10.1
# =========================================================================
# 数据来源: https://liquipedia.net/starcraft2/Warp_Prism_(Legacy_of_the_Void)
# DT smart-cast prism 时，DT 走到距 prism < PRISM_PICKUP_RANGE 时自动装载
PRISM_PICKUP_RANGE: float = 5.0

# =========================================================================
# 安全缓冲（防 sight 滞后 + 反应时间）
# =========================================================================
PRISM_AA_BUFFER: float = 3.0  # 棱镜距 AA 单位的额外安全距离
DT_DETECTOR_BUFFER: float = 1.0  # DT 距 detector 的额外安全距离
