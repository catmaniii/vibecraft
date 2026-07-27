"""bc_rush 补给楼 buffer 纯算术（无 sharpy 依赖，方便单测）。

把"在 sharpy AutoDepot 平滑增速之上叠加随产能放大的空余人口 buffer"这段纯算术
抽出来，单测可以直接喂 int、不必拉真 sharpy/sc2。BcAutoDepot 负责采集计数后调它。
"""

from __future__ import annotations

from math import ceil


def bc_depot_target(
    *,
    base: int,
    supply_used: int,
    supply_cap: int,
    rax: int,
    factory: int,
    starport: int,
    depots_ready: int,
) -> int:
    """算 bc_rush 需要的补给楼总目标数（to_count）。

    在父类 ``base`` 之上，强制保证空余人口 buffer ≥ ``8 + 2×产能建筑数``，
    覆盖一次 BC(+6) 离散爆发 + 一轮枪兵 ramp + 补给楼 21s 建造延迟，绝不卡人口。

    取 ``max(base, buffer 需求)`` —— buffer 只抬高、绝不削弱父类的增速预测。

    参数全是已 ready 的计数（兵营含反应堆、工厂、星港、已建好补给楼）。

    2026-06-20 用户：**14 农民前不下任何补给楼**（否则提前花 100 矿 → 卡 SCV 生产/停农民）。
    起手 12 农民、CC 自带 15 人口，14 之前有余量不会卡 → 返 0，第一个 depot 卡在 supply 14 下
    （与 plan 里 Step(Supply(14), depot) 一致；之后正常按 buffer 补）。
    """
    if supply_used < 14:
        return 0
    if supply_cap >= 200:
        return base

    buffer = 8 + 2 * (rax + factory + starport)
    target_cap = min(200, supply_used + buffer)
    needed = ceil((target_cap - supply_cap) / 8) + depots_ready
    return max(base, needed)
