"""bc_depot_target buffer 逻辑单测（纯算术，不拉 SC2/sharpy）。

验证 bc_rush 专用补给楼自动建造在父类平滑增速之上，按产能放大空余人口
buffer，确保不卡人口（用户强规则 2026-06-19）。
"""

from __future__ import annotations

from vibecraft.bot.auto_combat.terran.bc_supply import bc_depot_target


def test_buffer_dominates_when_production_high() -> None:
    """产能高时 buffer 下限盖过父类 base，预留充足空余人口。"""
    # 4 兵营 + 3 反应堆 + 1 工厂 + 1 星港 → prod=4+3+1+1=9, buffer=8+18=26
    result = bc_depot_target(
        base=2,
        supply_used=47,
        supply_cap=47,
        rax=7,  # 4 兵营 + 3 反应堆
        factory=1,
        starport=1,
        depots_ready=4,
    )
    # target_cap = 47 + 26 = 73; needed = ceil((73-47)/8)+4 = ceil(3.25)+4 = 4+4 = 8
    assert result == 8
    assert result > 2  # 盖过 base


def test_base_dominates_when_buffer_small() -> None:
    """父类 base 大时取 base（buffer 不削弱父类增速预测）。"""
    # buffer = 8 + 2*1 = 10; target_cap=40; needed=ceil((40-40)/8)+2=2; max(10,2)=10
    result = bc_depot_target(
        base=10,
        supply_used=30,
        supply_cap=40,
        rax=1,
        factory=0,
        starport=0,
        depots_ready=2,
    )
    assert result == 10


def test_capped_at_200() -> None:
    """人口已满 200 时直接返回父类 base，不再加楼。"""
    result = bc_depot_target(
        base=22,
        supply_used=200,
        supply_cap=200,
        rax=7,
        factory=1,
        starport=1,
        depots_ready=22,
    )
    assert result == 22


def test_buffer_absorbs_bc_pop() -> None:
    """单星港即将出 BC(+6)：buffer 留出足够空余吸收一发。"""
    # 1 星港 only（刚出 BC 阶段）→ prod=1, buffer=8+2=10
    result = bc_depot_target(
        base=1,
        supply_used=44,
        supply_cap=47,
        rax=0,
        factory=0,
        starport=1,
        depots_ready=4,
    )
    # target_cap=44+10=54; needed=ceil((54-47)/8)+4=ceil(0.875)+4=1+4=5
    assert result == 5
    # 建到 5 楼后 cap = 47 + (5-4)*8 = 55，44 用量下空余 11 >= 一发 BC(6)
    cap_after = 47 + (result - 4) * 8
    assert cap_after - 44 >= 6


def test_no_depot_before_supply_14() -> None:
    """supply<14 不建任何补给楼（2026-06-20 用户：14 农民才下第一个房子，否则停农民）。"""
    # 12 SCV 起手, cap 15 —— 14 之前有余量,绝不提前建楼抢矿
    for sup in (12, 13):
        result = bc_depot_target(
            base=5,  # 即使父类 base 想建,也压成 0
            supply_used=sup,
            supply_cap=15,
            rax=0,
            factory=0,
            starport=0,
            depots_ready=0,
        )
        assert result == 0, f"supply={sup} 应不建楼(返 0)，实际 {result}"


def test_first_depot_at_supply_14() -> None:
    """supply 达到 14 → 开始建第一个补给楼（needed=1）。"""
    # 14 SCV, cap 15, buffer=8, target_cap=14+8=22, needed=ceil((22-15)/8)=1
    result = bc_depot_target(
        base=0,
        supply_used=14,
        supply_cap=15,
        rax=0,
        factory=0,
        starport=0,
        depots_ready=0,
    )
    assert result == 1
