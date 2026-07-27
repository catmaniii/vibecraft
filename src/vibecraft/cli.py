"""vibecraft CLI 入口。

子命令：
  serve     启动 bot service（HTTP+WS）、生成 token、显示二维码、run forever

核心逻辑在 vibecraft.server.service，这里只做参数解析薄壳。
（cli.py 在 coverage 配置里是 omit 的，参见 pyproject.toml）
"""

from __future__ import annotations

import asyncio
from typing import Any

import click

from vibecraft import __version__


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(__version__, "-V", "--version")
def cli(ctx: click.Context) -> None:
    """VibeCraft —— 用语音 + 文字指挥 AI 替你操作 SC2 神族。"""
    if ctx.invoked_subcommand is None:
        click.echo(f"vibecraft {__version__}  —— 用 'vibecraft serve' 启动服务")


@cli.command()
@click.option("--port", default=None, type=int, help="监听端口（默认 8080 或来自 --config）")
@click.option("--host", default=None, help="监听地址（默认 0.0.0.0 或来自 --config）")
@click.option("--token", default=None, help="指定 room_token（默认自动生成）")
@click.option("--ip", default=None, help="二维码显示用 IP（默认自动检测局域网 IP）")
@click.option(
    "--config",
    default=None,
    help="服务器配置 YAML 文件路径（加载 name/token/port/ip/host；"
    "显式 CLI 参数优先于配置文件；admin_token 禁止出现在配置文件中）。",
)
@click.option(
    "--realtime/--no-realtime",
    default=True,
    show_default=True,
    help="SC2 是否按 wall-clock 实时跑（--no-realtime 让 SC2 按 step 推进，调试用）。"
    "PWA 在 start_game 帧里显式传 realtime 时优先使用 PWA 值。",
)
@click.option(
    "--my-race",
    default="Protoss",
    show_default=True,
    type=click.Choice(["Protoss", "Zerg", "Terran"], case_sensitive=True),
    help="我方种族（默认 Protoss）。",
)
@click.option(
    "--admin-token",
    default=None,
    envvar="VIBECRAFT_ADMIN_TOKEN",
    help="Admin dashboard 登录口令（SCRAM-SHA-256 鉴权，口令不过线/不存明文）。"
    "不设则 admin dashboard 整体关闭；<8 字符忽略。也可用环境变量 VIBECRAFT_ADMIN_TOKEN。",
)
def serve(
    port: int | None,
    host: str | None,
    token: str | None,
    ip: str | None,
    config: str | None,
    realtime: bool,
    my_race: str,
    admin_token: str | None,
) -> None:
    """启动 bot service：HTTP+WS 同端口，显示二维码，run forever。

    玩家用手机扫码即可连接驾驶舱 PWA。

    加载顺序（低→高优先级）：
      1. ServiceConfig 数据类默认值
      2. --config 配置文件（name/token/port/ip/host）
      3. 显式 CLI 参数（覆盖配置文件）
    """
    from vibecraft.server.server_config import load_server_config_file
    from vibecraft.server.service import BotService, ServiceConfig

    # 从 ServiceConfig 默认值出发，逐层叠加（低→高优先级）
    kwargs: dict[str, Any] = {}

    # 层 2：--config 文件（仅覆盖文件中出现的键）
    if config is not None:
        file_cfg = load_server_config_file(config)
        if "name" in file_cfg:
            kwargs["name"] = file_cfg["name"]
        if "token" in file_cfg:
            kwargs["token"] = file_cfg["token"]
        if "port" in file_cfg:
            kwargs["port"] = int(file_cfg["port"])
        if "ip" in file_cfg:
            kwargs["display_ip"] = file_cfg["ip"]
        if "host" in file_cfg:
            kwargs["host"] = file_cfg["host"]

    # 层 3：显式 CLI 参数（None = 未传，不覆盖文件/默认值）
    if port is not None:
        kwargs["port"] = port
    if host is not None:
        kwargs["host"] = host
    if token is not None:
        kwargs["token"] = token
    if ip is not None:
        kwargs["display_ip"] = ip

    # 这些参数没有配置文件对应，始终来自 CLI
    kwargs["default_realtime"] = realtime
    kwargs["default_my_race"] = my_race
    kwargs["admin_token"] = admin_token

    svc_config = ServiceConfig(**kwargs)
    svc = BotService(svc_config)
    try:
        asyncio.run(svc.run())
    except KeyboardInterrupt:
        click.echo("\nbot service 已停止")


def main(argv: list[str] | None = None) -> int:
    """程序入口，供 pyproject.toml [project.scripts] 调用。"""
    try:
        cli(argv, standalone_mode=False)
        return 0
    except click.exceptions.Abort:
        return 1
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
