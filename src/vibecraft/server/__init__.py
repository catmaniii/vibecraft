"""bot service：HTTP（serve PWA）+ WS endpoint，手机驾驶舱的后端。

设计文档 §2.1 ②、§9。M1.1：
- tokens   —— room_token 生成 / 验证 / 单活跃连接
- ws       —— WS endpoint（M1.1b）
- http     —— HTTP static server，process_request 钩子与 WS 共端口（M1.1c）
- qr       —— ASCII 二维码 + 局域网 IP 检测（M1.1d）
- service  —— 组装 + 生命周期，BotService + ServiceConfig（M1.1e）

注:BotService / ServiceConfig 不在此 re-export — 它们传递依赖 `av` (PyAV),
跟 ComfyUI 等同样用 av 的 app 共享 DLL 会有文件锁冲突。caller 显式
`from vibecraft.server.service import BotService` 即可(cli.py 已这么用)。
"""

from __future__ import annotations

from vibecraft.server.tokens import Connection, RoomRegistry, generate_room_token

__all__ = [
    "Connection",
    "RoomRegistry",
    "generate_room_token",
]
