"""兵种 roster 静态提取（M7 准入闸）：army 过滤 + 变体 diff。"""

from __future__ import annotations

from vibecraft.build_efficiency.roster import roster_diff, unit_roster


def test_roster_extracts_army_units_only():
    r = unit_roster("1g_robo_immortal")
    assert "STALKER" in r and "IMMORTAL" in r  # 主力兵种
    # 不含建筑/农民/补给
    assert "GATEWAY" not in r
    assert "ROBOTICSFACILITY" not in r
    assert "PROBE" not in r
    assert "PYLON" not in r


def test_roster_simple_build():
    assert "STALKER" in unit_roster("4bg")


def test_roster_diff_same_build_no_change():
    d = roster_diff("4bg", "4bg")
    assert d["added"] == set()
    assert d["removed"] == set()


def test_roster_diff_detects_difference():
    # 1g_robo(含 IMMORTAL/ZEALOT) vs 4bg(仅 STALKER) → 有增有减
    d = roster_diff("4bg", "1g_robo_immortal")
    assert "IMMORTAL" in d["added"]  # 1g_robo 比 4bg 多不朽
    assert d["added"]  # 非空 → M7 会否决这种"改了兵种"的变体


def test_roster_unknown_build_empty():
    assert unit_roster("nonexistent_build_xyz") == set()
