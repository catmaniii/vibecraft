"""Localizer i18n 接口单测（2026-06-03 用户:预埋多语言接口）。

验证：名称查找 + locale 回退 + 未知 key 回退原 id（不崩）。
加新语言只需往 *_NAMES 表加一列 locale，Localizer 不变。
"""

from __future__ import annotations

from vibecraft.bot.localization import (
    DEFAULT_LOCALE,
    UNIT_NAMES,
    Localizer,
)


class TestLocalizer:
    def test_default_locale_zh(self) -> None:
        assert DEFAULT_LOCALE == "zh"
        loc = Localizer()
        assert loc.locale == "zh"

    def test_unit_zh(self) -> None:
        loc = Localizer()
        assert loc.unit("Zealot") == "叉子"
        assert loc.unit("Immortal") == "不朽"

    def test_upgrade_zh_case_insensitive(self) -> None:
        loc = Localizer()
        assert loc.upgrade("CHARGE") == "冲锋"
        # 小写也命中（内部 upper）
        assert loc.upgrade("charge") == "冲锋"

    def test_structure_tech_then_production(self) -> None:
        loc = Localizer()
        assert loc.structure("TWILIGHTCOUNCIL") == "VC"  # 科技建筑表
        assert loc.structure("GATEWAY") == "BG"  # 产能建筑表

    def test_verb_zh(self) -> None:
        loc = Localizer()
        assert loc.verb("attack") == "进攻"
        assert loc.verb("defend") == "守"

    def test_unknown_key_falls_back_to_id(self) -> None:
        """未知 key → 回退原 id（不崩，且能看出缺哪条翻译）。"""
        loc = Localizer()
        assert loc.unit("Nonexistent") == "Nonexistent"
        assert loc.upgrade("NOSUCHUPGRADE") == "NOSUCHUPGRADE"
        assert loc.structure("NOSUCH") == "NOSUCH"
        assert loc.verb("teleport") == "teleport"

    def test_unknown_locale_falls_back_to_default(self) -> None:
        """未知 locale → 回退 DEFAULT_LOCALE 表（预埋：加语言前先能跑）。"""
        loc = Localizer(locale="fr")
        assert loc.unit("Zealot") == "叉子"  # fr 表不存在 → 回退 zh

    def test_new_locale_table_is_picked_up(self) -> None:
        """预埋接口验证:往表里加一列 locale → Localizer 自动用上，代码不变。"""
        UNIT_NAMES.setdefault("en", {})["Zealot"] = "Zealot"
        try:
            assert Localizer(locale="en").unit("Zealot") == "Zealot"
            assert Localizer(locale="zh").unit("Zealot") == "叉子"
        finally:
            UNIT_NAMES["en"].pop("Zealot", None)
            if not UNIT_NAMES["en"]:
                UNIT_NAMES.pop("en", None)
