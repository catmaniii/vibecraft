"""MinimapBuilder：从 bot 状态构造 minimap 帧 dict。

MVP 子集字段策略：
- playable / size / viewport / units_own / units_enemy_visible
- 每帧重发 map.playable（几十字节，省一类协议）
- viewport.size 固定 [24, 18]（spike S3 验证）
- units_own[i] = [x, y, kind]，kind 一字节区分形状/颜色
- 敌方只推 is_visible=True 的单位（fog 记忆是 M3 的事）

详见 docs/plans/2026-05-15-minimap.md §1.1 / §1.2。
"""

from __future__ import annotations

from typing import Any


class MinimapBuilder:
    """从 bot 状态构造 minimap 帧 dict。

    MVP 子集：playable / size / viewport / units_own / units_enemy_visible。
    每帧字段策略详见 docs/plans/2026-05-15-minimap.md §1.1。
    """

    def __init__(self, bot: Any) -> None:  # AresBot，用 Any 避免顶层 import
        self.bot = bot
        # 静态部分缓存（on_start 之后 game_info 才可访问）
        self._playable: list[int] | None = None
        self._map_size: list[int] | None = None

    def _ensure_static_cached(self) -> None:
        if self._playable is None:
            pa = self.bot.game_info.playable_area
            self._playable = [int(pa.x), int(pa.y), int(pa.width), int(pa.height)]
            ms = self.bot.game_info.map_size
            self._map_size = [int(ms[0]), int(ms[1])]

    def build(self, now: float) -> dict[str, Any]:
        """构造一帧 minimap dict。now = bot.time（游戏内秒）。"""
        self._ensure_static_cached()
        cam = self.bot.state.observation_raw.player.camera  # s2clientprotocol Point
        units_own = self._collect_own()
        units_enemy = self._collect_enemy_visible()
        return {
            "type": "minimap",
            "ts": round(now, 3),
            "map": {
                "playable": self._playable,
                "size": self._map_size,
            },
            "viewport": {
                "center": [round(cam.x, 2), round(cam.y, 2)],
                "size": [24, 18],  # 固定估算，spike S3 验证
            },
            "units_own": units_own,
            "units_enemy_visible": units_enemy,
        }

    def _collect_own(self) -> list[list[Any]]:
        out: list[list[Any]] = []

        # 基地（Nexus）
        for u in self.bot.townhalls:
            x, y = u.position_tuple
            out.append([round(x, 1), round(y, 1), "N"])

        # 探机（workers）
        for u in self.bot.workers:
            x, y = u.position_tuple
            out.append([round(x, 1), round(y, 1), "P"])

        # 其它建筑（structures - townhalls）
        townhall_tags = {h.tag for h in self.bot.townhalls}
        for u in self.bot.structures:
            if u.tag in townhall_tags:
                continue
            x, y = u.position_tuple
            out.append([round(x, 1), round(y, 1), "B"])

        # 战斗单位（units - workers）
        worker_tags = {w.tag for w in self.bot.workers}
        for u in self.bot.units:
            if u.tag in worker_tags:
                continue
            x, y = u.position_tuple
            out.append([round(x, 1), round(y, 1), "A"])

        return out

    def _collect_enemy_visible(self) -> list[list[Any]]:
        out: list[list[Any]] = []

        for u in self.bot.enemy_units:
            if not u.is_visible:
                continue
            x, y = u.position_tuple
            # 简单工人识别：PROBE / SCV / DRONE
            kind = "W" if u.type_id.name in {"PROBE", "SCV", "DRONE"} else "?"
            out.append([round(x, 1), round(y, 1), kind])

        for u in self.bot.enemy_structures:
            if not u.is_visible:
                continue
            x, y = u.position_tuple
            out.append([round(x, 1), round(y, 1), "?"])

        return out
