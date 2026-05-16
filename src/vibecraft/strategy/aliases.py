"""中央别名表 + verb 消歧。

设计文档 §4.4：
- aliases/protoss.yaml 列 building / unit / upgrade 各自的中文 + hotkey 别名
- verb 上下文决定查哪一组：build→建筑、train→单位、research→升级，
  同时起类型校验作用（"造叉子" 这种 verb 与组不匹配会抛错）
- 若同一字面别名在多组同形，verb 用来消歧；verb=ANY 且同形则抛歧义错误
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vibecraft.strategy.errors import StrategyValidationError


class VerbHint(str, Enum):
    """alias 解析时的 verb 上下文。"""

    BUILD = "build"  # 建筑
    TRAIN = "train"  # 单位
    RESEARCH = "research"  # 升级
    ANY = "any"  # 不限定


class AliasEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical: str
    default_display: str
    aliases: list[str] = Field(default_factory=list)
    hotkey: str | None = None


class AliasTable:
    """三组别名（building / unit / upgrade）+ verb 消歧查询。"""

    def __init__(
        self,
        buildings: list[AliasEntry],
        units: list[AliasEntry],
        upgrades: list[AliasEntry],
    ) -> None:
        self.buildings = {e.canonical: e for e in buildings}
        self.units = {e.canonical: e for e in units}
        self.upgrades = {e.canonical: e for e in upgrades}

        # 构建反向索引：alias → list[(canonical, group)]
        # group ∈ {"building", "unit", "upgrade"}
        self._reverse: dict[str, list[tuple[str, str]]] = {}
        for grp_name, grp in (
            ("building", self.buildings),
            ("unit", self.units),
            ("upgrade", self.upgrades),
        ):
            for canonical, entry in grp.items():
                for alias in [entry.canonical, entry.default_display, *entry.aliases]:
                    self._reverse.setdefault(alias.casefold(), []).append((canonical, grp_name))

    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> AliasTable:
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AliasTable:
        return cls(
            buildings=_parse_group(raw.get("buildings", {})),
            units=_parse_group(raw.get("units", {})),
            upgrades=_parse_group(raw.get("upgrades", {})),
        )

    # ------------------------------------------------------------------
    # 解析查询
    # ------------------------------------------------------------------

    def resolve(self, alias: str, *, verb: VerbHint = VerbHint.ANY) -> tuple[str, str]:
        """alias → (canonical, group)。

        - verb=BUILD → 仅 building；
        - verb=TRAIN → 仅 unit；
        - verb=RESEARCH → 仅 upgrade；
        - verb=ANY → 任一；歧义时抛错。

        找不到抛 KeyError。
        """
        key = alias.casefold()
        matches = self._reverse.get(key, [])
        if not matches:
            raise KeyError(f"未知别名: {alias!r}")

        if verb == VerbHint.ANY:
            distinct = {m[0]: m[1] for m in matches}
            if len(distinct) == 1:
                canonical, group = next(iter(distinct.items()))
                return canonical, group
            raise StrategyValidationError(
                f"别名 {alias!r} 有歧义（候选 {sorted(distinct)})，请提供 verb 上下文"
            )

        expected_group = {
            VerbHint.BUILD: "building",
            VerbHint.TRAIN: "unit",
            VerbHint.RESEARCH: "upgrade",
        }[verb]
        for canonical, group in matches:
            if group == expected_group:
                return canonical, group
        raise KeyError(
            f"别名 {alias!r} 在 {expected_group} 表里没有匹配（仅在 "
            f"{ {g for _, g in matches} } 里）"
        )

    def display_of(self, canonical: str) -> str:
        for grp in (self.buildings, self.units, self.upgrades):
            if canonical in grp:
                return grp[canonical].default_display
        raise KeyError(f"未注册的 canonical: {canonical!r}")

    def all_aliases(self, group: str | None = None) -> Iterable[str]:
        """枚举所有 alias 字面值（用于 LLM prompt 拼装）。"""
        groups = {"building": self.buildings, "unit": self.units, "upgrade": self.upgrades}
        if group is not None:
            return self._iter_aliases(groups[group])
        return self._iter_aliases(*groups.values())

    @staticmethod
    def _iter_aliases(*grps: dict[str, AliasEntry]) -> Iterable[str]:
        seen: set[str] = set()
        for grp in grps:
            for entry in grp.values():
                for a in [entry.canonical, entry.default_display, *entry.aliases]:
                    if a not in seen:
                        seen.add(a)
                        yield a


def _parse_group(raw: dict[str, dict[str, Any]]) -> list[AliasEntry]:
    out: list[AliasEntry] = []
    for canonical, payload in raw.items():
        out.append(
            AliasEntry(
                canonical=canonical,
                default_display=payload["default_display"],
                aliases=payload.get("aliases", []),
                hotkey=payload.get("hotkey"),
            )
        )
    return out
