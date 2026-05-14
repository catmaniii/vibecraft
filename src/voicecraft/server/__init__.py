"""bot service：HTTP（serve PWA）+ WS endpoint，手机驾驶舱的后端。

设计文档 §2.1 ②、§9。M1.1 起逐步填充：
- tokens   —— room_token 生成 / 验证 / 单活跃连接（本文件已导出）
- ws       —— WS endpoint（M1.1b）
- http     —— HTTP static server（M1.1c）
- service  —— 组装 + 生命周期（M1.1e）
"""

from __future__ import annotations

from voicecraft.server.tokens import Connection, RoomRegistry, generate_room_token

__all__ = ["Connection", "RoomRegistry", "generate_room_token"]
