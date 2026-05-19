"""StrategyLibrary：剧本目录抽象。

业务层用 `library.get(id)` 取剧本对象，不直接 import YAML 路径。
这条 indirection 留给未来：
- 多种族：每种族一个目录
- 玩家口述新剧本（compile_strategy v2.0）：动态注入
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from vibecraft.strategy.aliases import AliasTable
from vibecraft.strategy.errors import StrategyNotFoundError, StrategyValidationError
from vibecraft.strategy.models import (
    LategameDoctrine,
    LategameTransition,
    MidgameStance,
    OpeningBuild,
    Strategy,
    StrategyKind,
)


class StrategyLibrary:
    """In-memory 剧本仓库。"""

    def __init__(
        self,
        openings: list[OpeningBuild] | None = None,
        midgames: list[MidgameStance] | None = None,
        lategames: list[LategameDoctrine] | None = None,
        aliases: AliasTable | None = None,
        races: dict[str, str] | None = None,
    ) -> None:
        self._openings: dict[str, OpeningBuild] = {s.id: s for s in (openings or [])}
        self._midgames: dict[str, MidgameStance] = {s.id: s for s in (midgames or [])}
        self._lategames: dict[str, LategameDoctrine] = {s.id: s for s in (lategames or [])}
        self.aliases: AliasTable = aliases or AliasTable(buildings=[], units=[], upgrades=[])
        # id → 'protoss' / 'zerg' / 'terran'。from_directories 自动按 YAML 父目录推断；
        # 直接调用构造器（旧测试用法）时为空 dict，race_of 返回 None 表示"未知种族"。
        self._race_of: dict[str, str] = dict(races or {})
        self._validate_cross_references()

    # ------------------------------------------------------------------
    # 加载器
    # ------------------------------------------------------------------

    @classmethod
    def from_directories(
        cls,
        strategies_dir: Path,
        aliases_path: Path,
    ) -> StrategyLibrary:
        """从目录加载所有剧本 + 别名表。

        strategies_dir 下任意 .yaml/.yml 文件，按 kind 字段分类。
        """
        openings: list[OpeningBuild] = []
        midgames: list[MidgameStance] = []
        lategames: list[LategameDoctrine] = []
        races: dict[str, str] = {}

        for path in sorted(strategies_dir.rglob("*.yaml")):
            data = _load_yaml(path)
            obj = _build_strategy(data, source=path)
            if isinstance(obj, OpeningBuild):
                openings.append(obj)
            elif isinstance(obj, MidgameStance):
                midgames.append(obj)
            elif isinstance(obj, LategameDoctrine):
                lategames.append(obj)
            # 种族 = 文件所在直接父目录名（strategies/protoss/foo.yaml → protoss）
            # 仅当父目录名为已知种族时才记录；其它结构留空（race_of 返回 None = 不限种族）
            parent = path.parent.name.lower()
            if parent in {"protoss", "zerg", "terran"}:
                races[obj.id] = parent

        aliases = AliasTable.from_yaml(aliases_path)
        return cls(
            openings=openings, midgames=midgames, lategames=lategames,
            aliases=aliases, races=races,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, strategy_id: str) -> Strategy:
        """统一 id 查询（不区分 kind）。"""
        for table in (self._openings, self._midgames, self._lategames):
            if strategy_id in table:
                return table[strategy_id]
        raise StrategyNotFoundError(f"未注册的剧本 id: {strategy_id!r}")

    def get_opening(self, strategy_id: str) -> OpeningBuild:
        if strategy_id not in self._openings:
            raise StrategyNotFoundError(f"未注册的 opening: {strategy_id!r}")
        return self._openings[strategy_id]

    def get_midgame(self, strategy_id: str) -> MidgameStance:
        if strategy_id not in self._midgames:
            raise StrategyNotFoundError(f"未注册的 midgame: {strategy_id!r}")
        return self._midgames[strategy_id]

    def get_lategame(self, strategy_id: str) -> LategameDoctrine:
        if strategy_id not in self._lategames:
            raise StrategyNotFoundError(f"未注册的 lategame: {strategy_id!r}")
        return self._lategames[strategy_id]

    @property
    def openings(self) -> list[OpeningBuild]:
        return list(self._openings.values())

    @property
    def midgames(self) -> list[MidgameStance]:
        return list(self._midgames.values())

    @property
    def lategames(self) -> list[LategameDoctrine]:
        return list(self._lategames.values())

    def all_ids(self, kind: StrategyKind | None = None) -> list[str]:
        if kind == StrategyKind.OPENING:
            return list(self._openings)
        if kind == StrategyKind.MIDGAME:
            return list(self._midgames)
        if kind == StrategyKind.LATEGAME:
            return list(self._lategames)
        return [*self._openings, *self._midgames, *self._lategames]

    def race_of(self, strategy_id: str) -> str | None:
        """返回该剧本的种族（protoss/zerg/terran），未登记返回 None。"""
        return self._race_of.get(strategy_id)

    def all_ids_for_race(self, race: str) -> list[str]:
        """仅返回属于指定种族（protoss/zerg/terran）的剧本 id。

        用于 IntentParser 跨种族校验：神族玩家说"切 12pool"，LLM emit
        `strategy_set(12pool)` 时应被拒绝，因 12pool 是 zerg 剧本。

        未登记种族的 id 不包含在结果里 —— 直接构造 StrategyLibrary 的
        旧测试不传 races，所以 race_of 全是 None，本方法返回空 list。
        这种用法下应继续使用 `all_ids()` 不限种族。
        """
        race = race.lower()
        return [sid for sid in self.all_ids() if self._race_of.get(sid) == race]

    def all_strategies(self) -> Iterable[Strategy]:
        yield from self._openings.values()
        yield from self._midgames.values()
        yield from self._lategames.values()

    # ------------------------------------------------------------------
    # 转移图
    # ------------------------------------------------------------------

    def transitions_of(self, opening_id: str) -> list[str]:
        """opening 的 default_transitions 指向的 midgame id 列表。"""
        op = self.get_opening(opening_id)
        return [t.midgame_id for t in op.default_transitions]

    def lategame_transitions_of(self, midgame_id: str) -> list[LategameTransition]:
        return list(self.get_midgame(midgame_id).lategame_transitions)

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def _validate_cross_references(self) -> None:
        for op in self._openings.values():
            for opening_tr in op.default_transitions:
                if opening_tr.midgame_id not in self._midgames:
                    raise StrategyValidationError(
                        f"opening {op.id!r} 引用了未注册的 midgame {opening_tr.midgame_id!r}"
                    )
        for mid in self._midgames.values():
            for lategame_tr in mid.lategame_transitions:
                if lategame_tr.lategame_id not in self._lategames:
                    raise StrategyValidationError(
                        f"midgame {mid.id!r} 引用了未注册的 lategame {lategame_tr.lategame_id!r}"
                    )


# ---------------------------------------------------------------------------
# YAML → 对象
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise StrategyValidationError(f"{path}: 顶层必须是 mapping")
    return data


def _build_strategy(data: dict[str, Any], source: Path) -> Strategy:
    kind = data.get("kind")
    if kind == StrategyKind.OPENING.value:
        return OpeningBuild.model_validate(data)
    if kind == StrategyKind.MIDGAME.value:
        return MidgameStance.model_validate(data)
    if kind == StrategyKind.LATEGAME.value:
        return LategameDoctrine.model_validate(data)
    raise StrategyValidationError(f"{source}: 未知 kind {kind!r}")
