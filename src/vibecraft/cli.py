"""vibecraft CLI 入口。

子命令：
  serve     启动 bot service（HTTP+WS）、生成 token、显示二维码、run forever

核心逻辑在 vibecraft.server.service，这里只做参数解析薄壳。
（cli.py 在 coverage 配置里是 omit 的，参见 pyproject.toml）
"""

from __future__ import annotations

import asyncio

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
@click.option("--port", default=8080, show_default=True, help="监听端口")
@click.option("--host", default="0.0.0.0", show_default=True, help="监听地址")
@click.option("--token", default=None, help="指定 room_token（默认自动生成）")
@click.option("--ip", default=None, help="二维码显示用 IP（默认自动检测局域网 IP）")
@click.option(
    "--realtime/--no-realtime",
    default=True,
    show_default=True,
    help="SC2 是否按 wall-clock 实时跑（--no-realtime 让 SC2 按 step 推进，调试用）。"
    "PWA 在 start_game 帧里显式传 realtime 时优先使用 PWA 值。",
)
def serve(
    port: int, host: str, token: str | None, ip: str | None, realtime: bool
) -> None:
    """启动 bot service：HTTP+WS 同端口，显示二维码，run forever。

    玩家用手机扫码即可连接驾驶舱 PWA。
    """
    from vibecraft.server.service import BotService, ServiceConfig

    config = ServiceConfig(
        port=port,
        host=host,
        token=token,
        display_ip=ip,
        default_realtime=realtime,
    )
    svc = BotService(config)
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
