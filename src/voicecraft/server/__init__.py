"""bot service：HTTP（serve PWA）+ WS endpoint，手机驾驶舱的后端。

设计文档 §2.1 ②、§9。M1.1：
- tokens   —— room_token 生成 / 验证 / 单活跃连接
- ws       —— WS endpoint（M1.1b）
- http     —— HTTP static server，process_request 钩子与 WS 共端口（M1.1c）
- qr       —— ASCII 二维码 + 局域网 IP 检测（M1.1d）
- service  —— 组装 + 生命周期，BotService + ServiceConfig（M1.1e）
"""

from __future__ import annotations

from voicecraft.server.service import BotService, ServiceConfig
from voicecraft.server.tokens import Connection, RoomRegistry, generate_room_token

__all__ = [
    "BotService",
    "Connection",
    "RoomRegistry",
    "ServiceConfig",
    "generate_room_token",
]
