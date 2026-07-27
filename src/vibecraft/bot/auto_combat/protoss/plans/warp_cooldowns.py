"""神族 Warp Gate 各兵种 warp-in cooldown(秒,Faster game speed)。

数据源:Liquipedia LotV Warp Gate page。规律 = Gateway build time − 7s
(Sentry 例外 −3s)。

参考:https://liquipedia.net/starcraft2/Warp_Gate_(Legacy_of_the_Void)

为什么独立成 module
====================
4 个 plan(ForwardWarpStalker / WarpZealotAtPrism / WarpDTAtPrism /
PrismWarpDropAct)之前各自硬编码 `_WARP_COOLDOWN_S` 常量,实际值不一致(23/28/28)
而真实 cd 又 per-unit 不同(Z/A=20, S/Sen=23, HT/DT=32)。统一查表 + 给
sharpy cd_manager.is_ready(cooldown=N) 用,消除"哪个常量对哪个兵种"的认知负担。
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId

# 各兵种 WG warp-in cooldown(秒,Faster speed)。
# 注意:跟 warp-in time(11.4s 单位实体化时间)不同 — 这个是 WG 自身的 cd,
# 决定"下一次同 WG 能 warp 任何兵种"的最早时间(per-WG-per-ability)。
WARP_COOLDOWN_S: dict[UnitTypeId, float] = {
    UnitTypeId.ZEALOT: 20.0,
    UnitTypeId.ADEPT: 20.0,
    UnitTypeId.STALKER: 23.0,
    UnitTypeId.SENTRY: 23.0,
    UnitTypeId.HIGHTEMPLAR: 32.0,
    UnitTypeId.DARKTEMPLAR: 32.0,
}

# 表里没有的兜底值。取最大(32s,DT/HT)偏保守 — 不会出现"误报 ready 浪费 warp_in call"。
_FALLBACK_COOLDOWN_S: float = 32.0


def get_warp_cooldown(unit_type: UnitTypeId) -> float:
    """返回该兵种的 WG cooldown 秒数。未知单位返回保守兜底(32s)。"""
    return WARP_COOLDOWN_S.get(unit_type, _FALLBACK_COOLDOWN_S)
