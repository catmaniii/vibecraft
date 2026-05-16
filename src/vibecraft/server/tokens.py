"""room_token：手机配对凭证 + 单活跃连接管理。

设计文档 §9.2：
- bot service 启动生成一个 room_token
- 二维码 / URL 带 token，手机扫码后 WS 连接时带上
- **MVP：一 token 同时仅一活跃连接，重连顶旧**
"""

from __future__ import annotations

import secrets
from typing import Protocol


class Connection(Protocol):
    """一个活跃连接的最小接口。M1.1b 的 WS 连接实现它。

    放在这里而非 ws.py，是为了 RoomRegistry 能不依赖 websockets 单测。
    """

    async def close(self, reason: str) -> None: ...


def generate_room_token() -> str:
    """生成 room_token：URL-safe，短（要塞进二维码，也要能手输兜底）。"""
    # 9 字节 → base64url 12 字符，足够随机又不至于手输到崩溃
    return secrets.token_urlsafe(9)


class RoomRegistry:
    """单 token + 单活跃连接。

    MVP 形态（设计文档 §9.2）：一个 bot service 实例 = 一个 room，一个 token，
    同时只有一个手机连着；新连接顶掉旧的。未来多连接（主控 + 观战）时这个类
    扩展成多 slot。
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token or generate_room_token()
        self._active: Connection | None = None

    @property
    def token(self) -> str:
        return self._token

    @property
    def active_connection(self) -> Connection | None:
        return self._active

    def verify(self, token: str) -> bool:
        """验证 WS 连接带来的 token。常数时间比较，防时序侧信道。"""
        return secrets.compare_digest(token, self._token)

    def attach(self, conn: Connection) -> Connection | None:
        """接入新连接。

        返回被顶掉的旧连接（调用方负责 close 它），没有则 None。
        """
        evicted = self._active
        self._active = conn
        return evicted

    def detach(self, conn: Connection) -> None:
        """连接断开。

        只有当 conn 确实是当前 active 才清空 —— 否则一个已被顶掉的旧连接
        延迟断开时，会误清掉刚接上的新连接。
        """
        if self._active is conn:
            self._active = None
