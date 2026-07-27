"""room_token：手机配对凭证 + per-player 多连接管理。

设计文档 §9.2 + 阶段 0 多人联网（2026-06-12）：
- bot service 启动生成一个 room_token（房间码）
- 二维码 / URL 带 token，手机扫码后 WS 连接时带上
- **多人化：同 token 下多玩家各一条连接，同 player_id 重连顶旧**
"""

from __future__ import annotations

import contextlib
import secrets
from typing import Protocol


class Connection(Protocol):
    """一个活跃连接的最小接口。WS 连接实现它。

    放在这里而非 ws.py，是为了 RoomRegistry 能不依赖 websockets 单测。
    """

    async def close(self, reason: str) -> None: ...

    async def send_text(self, frame: str) -> None: ...


def generate_room_token() -> str:
    """生成 room_token：URL-safe，短（要塞进二维码，也要能手输兜底）。"""
    # 9 字节 → base64url 12 字符，足够随机又不至于手输到崩溃
    return secrets.token_urlsafe(9)


class RoomRegistry:
    """单 token + per-player 单活跃连接（2026-06-12 多人化）。

    一个 server 一个 token（房间码）；同 token 下多玩家各一条连接，
    同 player_id 重连顶旧（手机刷新 / PWA 重载场景）。
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token or generate_room_token()
        # player_id → 当前活跃连接
        self._conns: dict[str, Connection] = {}

    @property
    def token(self) -> str:
        return self._token

    def verify(self, token: str) -> bool:
        """验证 WS 连接带来的 token。常数时间比较，防时序侧信道。"""
        return secrets.compare_digest(token, self._token)

    def connection_of(self, player_id: str) -> Connection | None:
        """返回指定玩家当前活跃连接，无则 None。"""
        return self._conns.get(player_id)

    @property
    def player_ids(self) -> list[str]:
        """当前所有在线玩家 id 列表。"""
        return list(self._conns)

    def attach(self, conn: Connection, player_id: str) -> Connection | None:
        """接入新连接（绑定到 player_id）。

        返回被顶掉的同 player_id 旧连接（调用方负责 close 它），没有则 None。
        不同 player_id 之间互不干扰。
        """
        evicted = self._conns.get(player_id)
        self._conns[player_id] = conn
        return evicted

    def detach(self, conn: Connection) -> None:
        """连接断开。

        遍历找到 conn 所属的 player_id 并移除。只清与 conn 完全一致（is）的槽，
        防止已被顶掉的旧连接延迟断开时误清掉刚接上的新连接。
        """
        for pid, c in list(self._conns.items()):
            if c is conn:
                del self._conns[pid]
                return

    async def broadcast(self, frame: str) -> None:
        """给所有活跃连接广播同一帧（room_state 等）。单点失败不阻断其他。"""
        for conn in list(self._conns.values()):
            with contextlib.suppress(Exception):
                await conn.send_text(frame)
