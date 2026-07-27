"""MinimapBuilder：从 bot 状态构造 minimap 帧 dict。

MVP 子集字段策略：
- playable / size / viewport / units_own / units_enemy_visible
- terrain:开局只推一次(静态),后续帧 omit;前端缓存
- vision:每帧推(playable 区域切片+base64),0=Hidden / 1=Fogged / 2=Visible
- 每帧重发 map.playable（几十字节，省一类协议）
- viewport.size 固定 [24, 18]（spike S3 验证）
- units_own[i] = [x, y, kind]，kind 一字节区分形状/颜色
- 敌方只推 is_visible=True 的单位（fog 记忆是 M3 的事）

"""

from __future__ import annotations

import base64
from typing import Any


class MinimapBuilder:
    """从 bot 状态构造 minimap 帧 dict。

    地形(terrain_height)+战争迷雾(visibility)从 PixelMap 切 playable 区域,
    base64 编码后塞进帧。data_numpy shape=(map_h, map_w),uint8。
    切片用 numpy[y_start:y_end, x_start:x_end] 取 playable 子区域。
    """

    def __init__(self, bot: Any) -> None:  # AresBot,用 Any 避免顶层 import
        self.bot = bot
        # 静态部分缓存(on_start 之后 game_info 才可访问)
        self._playable: list[int] | None = None
        self._map_size: list[int] | None = None
        # terrain 只推一次,前端缓存
        self._terrain_sent: bool = False
        # 被攻击检测:跟踪上帧每个单位 health+shield 之和
        self._prev_health: dict[int, float] = {}

    def _ensure_static_cached(self) -> None:
        if self._playable is None:
            pa = self.bot.game_info.playable_area
            self._playable = [int(pa.x), int(pa.y), int(pa.width), int(pa.height)]
            ms = self.bot.game_info.map_size
            self._map_size = [int(ms[0]), int(ms[1])]

    def _encode_playable_pixelmap(self, pm: Any) -> dict[str, Any]:
        """切 playable 区域 + base64 编码 uint8 字节流。"""
        assert self._playable is not None
        px, py, pw, ph = self._playable
        sub = pm.data_numpy[py : py + ph, px : px + pw]
        # 内存连续(切片可能非连续),确保 tobytes 顺序正确
        arr = sub.copy() if not sub.flags["C_CONTIGUOUS"] else sub
        return {
            "w": int(pw),
            "h": int(ph),
            "b64": base64.b64encode(arr.tobytes()).decode("ascii"),
        }

    def _collect_under_attack(self) -> list[list[float]]:
        """检测 own units/structures 本帧 (health+shield) < 上帧的,返回它们位置。

        阈值 0.5 过滤掉浮点噪声/护盾自然衰减(神族护盾恢复不会减,只增,所以稳)。
        返回 [[x, y], ...] 世界坐标列表;前端在小地图上画红色脉冲。
        """
        under_attack: list[list[float]] = []
        new_health: dict[int, float] = {}

        # townhalls / workers / structures / units 可能有重叠(workers ⊂ units,
        # townhalls ⊂ structures),用 set 去重 tag
        seen: set[int] = set()
        for source in (
            self.bot.townhalls,
            self.bot.workers,
            self.bot.structures,
            self.bot.units,
        ):
            for u in source:
                if u.tag in seen:
                    continue
                seen.add(u.tag)
                h_total = float(u.health + u.shield)
                new_health[u.tag] = h_total
                prev = self._prev_health.get(u.tag)
                if prev is not None and h_total < prev - 0.5:
                    x, y = u.position_tuple
                    under_attack.append([round(x, 1), round(y, 1)])

        self._prev_health = new_health
        return under_attack

    def _collect_alerts(self) -> list[str]:
        """SC2 全局 alerts(BuildingUnderAttack / NuclearLaunchDetected 等)。

        bot.state.observation.alerts 是 Alert enum int 列表,转字符串名给前端。
        """
        from s2clientprotocol import sc2api_pb2 as sc_pb

        out: list[str] = []
        for alert_int in self.bot.state.observation.alerts:
            try:
                out.append(sc_pb.Alert.Name(alert_int))
            except Exception:
                out.append(f"Unknown_{alert_int}")
        return out

    def build(self, now: float) -> dict[str, Any]:
        """构造一帧 minimap dict。now = bot.time（游戏内秒）。"""
        self._ensure_static_cached()
        cam = self.bot.state.observation_raw.player.camera  # s2clientprotocol Point
        units_own = self._collect_own()
        units_enemy = self._collect_enemy_visible()

        frame: dict[str, Any] = {
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
            # 中立资源点：水晶矿(M) + 气矿(G)，前端配色对齐游戏内小地图。
            # 每帧重发（vision pixelmap 已是大头，resources 几 KB 可忽略），
            # 自动反映采空消失 / 侦察新发现的矿区。
            "resources": self._collect_resources(),
            # 战争迷雾(每帧):0=Hidden / 1=Fogged / 2=Visible
            "vision": self._encode_playable_pixelmap(self.bot.state.visibility),
            # 被攻击位置:[[x, y], ...] 本帧 health 降低的 own 单位/建筑
            "under_attack": self._collect_under_attack(),
            # 全局 alerts(BuildingUnderAttack / NuclearLaunchDetected 等)
            "alerts": self._collect_alerts(),
        }

        # 地形高度图(0-255):静态数据,只第一帧带,前端缓存
        if not self._terrain_sent:
            frame["terrain"] = self._encode_playable_pixelmap(self.bot.game_info.terrain_height)
            self._terrain_sent = True

        return frame

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

    def _collect_resources(self) -> list[list[Any]]:
        """中立资源点：水晶矿(M) + 气矿(G)。

        来自 python-sc2 BotAI.mineral_field / vespene_geyser（已观测到的中立资源，
        含视野内 + 记忆快照）。前端在迷雾遮罩之前画 → 未探索区被 fog 压暗、已探索
        的亮，和游戏内小地图一致。属性缺失（早期 / mock）时返回空，不影响主流程。
        """
        out: list[list[Any]] = []
        try:
            for m in getattr(self.bot, "mineral_field", []):
                x, y = m.position_tuple
                out.append([round(x, 1), round(y, 1), "M"])
        except Exception:
            pass
        try:
            for g in getattr(self.bot, "vespene_geyser", []):
                x, y = g.position_tuple
                out.append([round(x, 1), round(y, 1), "G"])
        except Exception:
            pass
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
