"""后端 i18n `t()` 单测：与前端共读 locales/strings.json，验回退/插值/无缺译。"""

from __future__ import annotations

from vibecraft import i18n


def test_zh_en_lookup() -> None:
    assert i18n.t("history.interpretation", "zh") == "识别"
    assert i18n.t("history.interpretation", "en") == "Heard"


def test_default_lang_is_zh() -> None:
    assert i18n.t("panel.tech") == "科技"


def test_missing_lang_falls_back_to_zh() -> None:
    # 不存在的语言 → 回退 zh
    assert i18n.t("panel.tech", "fr") == "科技"


def test_unknown_key_returns_key() -> None:
    assert i18n.t("__no_such_key__", "en") == "__no_such_key__"


def test_template_interpolation() -> None:
    # 临时构造：用现有 key 验插值机制不破坏无占位符串
    assert i18n.t("panel.tech", "en") == "Tech"
    # 直接验替换逻辑（假 key 走 key 本身 + 替换）
    assert i18n.t("{n} units", "en", n=3) == "3 units"


def test_available_locales_has_zh_en() -> None:
    locs = i18n.available_locales()
    assert "zh" in locs and "en" in locs


def test_strings_json_no_missing_translation() -> None:
    """真理源里每个非 `_` 键都必须 zh+en 齐全（与前端同一把守）。

    例外：少数键的 en **有意为空**（如计数后缀「个」英文无对应量词，前端 preview
    亦用 `L('个','')`，渲染成 "3/4"）。列入白名单，仍要求 zh 非空。
    """
    intentional_empty_en = {"cond.unitCount"}
    data = i18n._load()
    missing = [
        k
        for k, v in data.items()
        if not k.startswith("_")
        and isinstance(v, dict)
        and (not v.get("zh") or (not v.get("en") and k not in intentional_empty_en))
    ]
    assert missing == [], f"缺译: {missing}"
