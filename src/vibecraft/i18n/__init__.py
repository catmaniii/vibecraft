"""后端 i18n：从仓库根 `locales/strings.json`（中英唯一真理源）读 UI/消息字符串。

与前端 `web/src/i18n.ts` **共读同一份 strings.json**（无生成器）。提供 `t(key, lang, **params)`。

分工（设计 docs/plans/2026-06-27-i18n-localization-design.md §8）：
  - 本模块 `t()`：管"key→句子/标签模板"（服务端发给手机的用户可见消息：解析反馈、错误、澄清…）。
  - `vibecraft.bot.localization.Localizer`：管"id→单位/建筑/科技专有名词"。
  两者互补——句子里嵌专有名词时，由 `t()` 的调用方先用 `Localizer` 渲染好名词再作参数传入。

回退：lang 缺译 → zh → key 本身（不崩、能看出缺哪条）。模板用命名占位符 `{name}`，各语言占位符名一致。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

Locale = str  # "zh" | "en"（不强约束，便于将来扩语言）

_DEFAULT_LANG: Locale = "zh"
_STRINGS_PATH = Path(__file__).resolve().parents[3] / "locales" / "strings.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, str]]:
    """加载 strings.json（进程内缓存一次）。文件缺失/损坏 → 空表（t 全回退 key）。"""
    try:
        with _STRINGS_PATH.open(encoding="utf-8") as f:
            data: dict[str, dict[str, str]] = json.load(f)
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def t(key: str, lang: Locale = _DEFAULT_LANG, /, **params: Any) -> str:
    """翻译 key 到 lang；可选 `{name}` 模板替换。

    >>> t("history.interpretation", "en")
    'Heard'
    缺 key/缺译多级回退；未知 key 原样返回（便于发现遗漏）。

    回退规则（2026-06-29 修，opus 评审）：某 locale **显式存在**该键（即便值是空串，
    如计数后缀「个」英文有意留空）→ 用它，**不**回退 zh；该 locale **缺键** → 回退 zh →
    key。旧实现用 `entry.get(lang) or ...`，把"有意空译"误当缺译回退中文 → en 模式泄漏中文。
    """
    entry = _load().get(key)
    if not entry:
        text = key
    elif lang in entry and entry[lang] is not None:
        text = entry[lang]  # 显式翻译（允许有意空串）
    else:
        text = entry.get(_DEFAULT_LANG) or key
    if params:
        for name, val in params.items():
            text = text.replace("{" + name + "}", str(val))
    return text


def available_locales() -> list[Locale]:
    """strings.json 里出现过的语言代码（除内部 `_` 前缀键）。"""
    langs: set[str] = set()
    for k, entry in _load().items():
        if k.startswith("_") or not isinstance(entry, dict):
            continue
        langs.update(lc for lc in entry if lc != "context")
    return sorted(langs)
