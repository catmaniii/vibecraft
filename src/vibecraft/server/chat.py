"""全局文字聊天 hub：内存 ring buffer 历史 + 自增消息 id。

聊天经 RoomRegistry.broadcast 推给所有在线连接（room-global = 所有玩家互相可见）。
历史只内存保留最近 N 条（重启丢，聊天不需持久化）。每条带：
  - 自增 id —— 客户端据此去重 + 排序（防重连/历史与实时消息竞态导致重复乱序）
  - pid（player_id）—— 客户端标注"自己"的消息；昵称可被 URL ?player= 伪造，pid 是防伪抓手
  - ts —— server 端时间戳（不信客户端 ts：时区乱 + 可作弊）
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any


class ChatHub:
    """进程内全局聊天历史 + id 分配。单主机够用；多主机阶段2 再搬 VPS。"""

    def __init__(self, max_history: int = 50) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._next_id = 1

    def add(self, *, name: str, pid: str, text: str) -> dict[str, Any]:
        """记一条消息（server 时间戳 + 自增 id），存进历史，返回可广播的 chat_msg dict。"""
        msg: dict[str, Any] = {
            "type": "chat_msg",
            "id": self._next_id,
            "name": name,
            "pid": pid,
            "text": text,
            "ts": int(time.time()),
        }
        self._next_id += 1
        self._history.append(msg)
        return msg

    def history(self) -> list[dict[str, Any]]:
        """返回最近 N 条（按 id 升序，即时间顺序）。"""
        return list(self._history)
