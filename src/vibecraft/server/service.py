"""BotService：组装 HTTP+WS server + 生命周期管理（M1.1e）。

设计文档 §9.2：
  - bot service 启动 → 生成 room_token → 打印二维码 → run forever
  - HTTP + WS 共端口（process_request 钩子）
  - listen 0.0.0.0（不硬编码 localhost）

BotService 是可测的核心类；CLI 的 serve 子命令只做参数解析 + BotService.run()。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import structlog
from websockets.asyncio.server import serve

from vibecraft.server.game_process import GameProcess
from vibecraft.server.http import make_process_request
from vibecraft.server.qr import print_connect_info
from vibecraft.server.tokens import RoomRegistry, generate_room_token
from vibecraft.server.ws import make_ws_handler

logger = structlog.get_logger(__name__)


@dataclass
class ServiceConfig:
    """BotService 启动配置。"""

    port: int = 8080
    """监听端口，默认 8080。"""

    host: str = "0.0.0.0"
    """监听地址，不硬编码 localhost（设计文档「不硬编码 localhost」要求）。"""

    static_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(__file__).parent / "static"
    )
    """PWA 静态资源目录。"""

    token: str | None = None
    """room_token；None 时自动生成。"""

    display_ip: str | None = None
    """二维码显示用 IP；None 时自动检测局域网 IP。"""


class BotService:
    """HTTP+WS bot service 实例。

    用法::

        config = ServiceConfig(port=8080)
        svc = BotService(config)
        asyncio.run(svc.run())
    """

    def __init__(self, config: ServiceConfig | None = None) -> None:
        self._config = config or ServiceConfig()
        self._registry = RoomRegistry(token=self._config.token or generate_room_token())
        self._game_process = GameProcess()
        self._log = logger.bind(port=self._config.port, host=self._config.host)

    @property
    def registry(self) -> RoomRegistry:
        """暴露 registry，方便测试 / M1.2 扩展注入 SC2 生命周期。"""
        return self._registry

    @property
    def game_process(self) -> GameProcess:
        """暴露 game_process，方便测试检查状态。"""
        return self._game_process

    @property
    def token(self) -> str:
        return self._registry.token

    async def run(self) -> None:
        """启动 server，打印二维码，run forever（收到 SIGINT / CancelledError 退出）。"""
        cfg = self._config
        process_request = make_process_request(static_dir=cfg.static_dir)
        ws_handler = make_ws_handler(self._registry, game_process=self._game_process)

        self._log.info("bot_service_starting")

        async with serve(
            ws_handler,
            cfg.host,
            cfg.port,
            process_request=process_request,
            # 关闭 websockets 内置 ping（业务层 ping 帧由 WsConnection 负责）
            ping_interval=None,
        ) as server:
            self._log.info(
                "bot_service_listening",
                host=cfg.host,
                port=cfg.port,
                token=self._registry.token,
            )
            # 打印二维码（display_ip=None → 自动检测）
            print_connect_info(
                port=cfg.port,
                token=self._registry.token,
                ip=cfg.display_ip,
            )
            await server.serve_forever()
