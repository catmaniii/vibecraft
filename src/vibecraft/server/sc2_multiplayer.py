"""跨进程 SC2 多人对局 runner（阶段 0 多人联网）。

python-sc2 的 `_host_game` / `_join_game` 支持 Portconfig 跨进程联机，但
**不透传窗口 resolution/placement**（只透传 fullscreen）——多实例必须摆窗口，
所以这里复刻两个带窗口参数的变体。逻辑与 sc2.main 同名函数一致，仅
SC2Process(...) 多了 resolution/placement 两个 kwargs。

事实依据（.venv sc2/main.py 实读，2026-06-12）：
- _host_game: SC2Process(fullscreen=...) → _setup_host_game(create_game) → _play_game(players[0])
- _join_game: SC2Process(fullscreen=...) → Client(server._ws) → _play_game(players[1])
- controller.create_game 对 Participant 只设 p.type（race 由各 client join_game 时自报）
  → host 端 guest 占位 Bot 的 race 随便填、ai 传 None 都行。
- create_game 的 PlayerSetup **没有 team 字段** → 多人局敌我关系由引擎默认（melee
  地图 = FFA），组队是否可行由本 spike 观察判定。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from sc2.client import Client  # type: ignore[import-untyped]
from sc2.data import Difficulty, Race  # type: ignore[import-untyped]
from sc2.main import _play_game_ai, _setup_host_game  # type: ignore[import-untyped]
from sc2.player import AbstractPlayer, Bot, Computer  # type: ignore[import-untyped]
from sc2.portconfig import Portconfig  # type: ignore[import-untyped]
from sc2.sc2process import SC2Process  # type: ignore[import-untyped]


def build_host_players(
    my_race: str,
    my_name: str,
    guest_names: list[str],
    computers: list[dict[str, str]],
    my_ai: Any = None,
) -> list[AbstractPlayer]:
    """组 host create_game 的 players 列表：[本方 bot, guest 占位..., Computer...]。

    guest 占位 Bot 的 ai=None / race=Random 均可（create_game 只读 p.type，
    真实 race 由 guest 进程自己 join_game 时上报）。
    """
    players: list[AbstractPlayer] = [Bot(Race[my_race], my_ai, name=my_name)]
    for name in guest_names:
        players.append(Bot(Race.Random, None, name=name))
    for c in computers:
        players.append(Computer(Race[c["race"]], Difficulty[c["difficulty"]]))
    return players


def new_portconfig_json(guests: int) -> str:
    """生成一组多人对局端口配置（round-trip 校验后返回 json）。

    跨进程传递 Portconfig 的根基：父进程生成、序列化成 json，
    各子进程 from_json 重建，保证端口一致。

    ⚠️ **必须用散点 `Portconfig()`，绝不可用 `Portconfig.contiguous_ports()`**
    （2026-06-12 spike 二分实锤）：contiguous 挑连号端口只做"空闲检查"、不推进
    Windows 顺序分配的临时端口游标 → 约 1s 后子进程里 SC2 自己的 websocket 端口
    被 OS 顺序分配到正好压在游戏 P2P 端口上 → 引擎绑不了端口，join 被拒
    NetworkError(12) 'Failed to join game: 537001988'（且 python-sc2 吞掉该错误）。
    散点 Portconfig() 的 4+ 个端口都经 pick_unused_port 真实 bind 过、游标已推过，
    不会被后续分配撞上。
    """
    pc = Portconfig(guests=guests)
    return Portconfig.from_json(pc.as_json).as_json


# join 重试参数：python-sc2 的 client.join_game **不检查**响应里的 join_game.error
# 字段——引擎拒绝时静默返回 player_id=0，后续 get_game_data 才炸出莫名其妙的
# "A game has not been started yet"（2026-06-12 spike 踩坑实录）。这里以
# player_id==0 为失败信号，重试几次。
_JOIN_RETRIES: int = 3
_JOIN_RETRY_WAIT_S: float = 2.0


async def _checked_join_and_play(
    client: Client,
    player: Bot,
    realtime: bool,
    portconfig: Portconfig,
    game_time_limit: int | None,
) -> Any:
    """join（带错误检查 + 重试）然后进 bot 主循环。

    复刻 sc2.main._play_game 的 Bot 分支，多了 join 失败检测：
    引擎拒绝 join（NetworkError 等）时 player_id=0，原版会带着无效 id 一头撞进
    get_game_data。这里检测 + 重试 + 最终显式 raise。
    """
    player_id = 0
    for attempt in range(1, _JOIN_RETRIES + 1):
        player_id = await client.join_game(player.name, player.race, portconfig=portconfig)
        if player_id and player_id > 0:
            break
        if attempt < _JOIN_RETRIES:
            await asyncio.sleep(_JOIN_RETRY_WAIT_S)
    if not player_id or player_id <= 0:
        raise RuntimeError(
            f"join_game 被引擎拒绝（player_id={player_id}；多为 LAN 握手 NetworkError，"
            "python-sc2 吞掉了具体错误码）"
        )
    return await _play_game_ai(client, player_id, player.ai, realtime, game_time_limit)


async def _wait_signal(before_join: Callable[[], Any] | None) -> None:
    """join 前的可选同步点（multiprocessing.Barrier.wait 等阻塞调用）。

    2026-06-12 spike 结论：端口修对（散点 Portconfig，见 new_portconfig_json）后
    join **不需要**跨进程同步——相差几秒引擎会等（E7 无栅栏 PASS）。此钩子保留给
    编排层做可选的会合/启动顺序控制（如等全部 SC2 实例就绪再统一放行），不是
    正确性必需。阻塞调用走 executor，不卡事件循环。
    """
    if before_join is None:
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, before_join)


async def host_game(
    map_settings: Any,
    players: list[AbstractPlayer],
    realtime: bool,
    portconfig: Portconfig,
    *,
    resolution: tuple[int, int] | None = None,
    placement: tuple[int, int] | None = None,
    game_time_limit: int | None = None,
    before_join: Callable[[], Any] | None = None,
) -> Any:
    """复刻 sc2.main._host_game + 窗口参数 + join 同步/检查。players[0] 是本方 bot。

    与原版差异：
    - SC2Process 多了 resolution / placement（多实例窗口平铺）
    - create_game 之后、join 之前等 before_join（跨进程 join 会合，见 _wait_signal）
    - join 带错误检查 + 重试（见 _checked_join_and_play）
    """
    # players[0] 必须是 Bot；fullscreen 从它取
    fullscreen: bool = getattr(players[0], "fullscreen", False)
    async with SC2Process(
        fullscreen=fullscreen,
        resolution=resolution,
        placement=placement,
    ) as server:
        await server.ping()
        # _setup_host_game 发 create_game 请求，返回 Client(server._ws)
        client = await _setup_host_game(server, map_settings, players, realtime)
        # BotAI 子类可选 raw_affects_selection；兜底 getattr 防 AttributeError
        _ai = getattr(players[0], "ai", None)
        if _ai is not None and getattr(_ai, "raw_affects_selection", None) is not None:
            client.raw_affects_selection = _ai.raw_affects_selection  # type: ignore[assignment]
        await _wait_signal(before_join)
        result = await _checked_join_and_play(
            client, players[0], realtime, portconfig, game_time_limit
        )
        with contextlib.suppress(Exception):
            await client.leave()
        await client.quit()
        return result


async def join_game(
    bot_player: Bot,
    realtime: bool,
    portconfig: Portconfig,
    *,
    resolution: tuple[int, int] | None = None,
    placement: tuple[int, int] | None = None,
    game_time_limit: int | None = None,
    before_join: Callable[[], Any] | None = None,
) -> Any:
    """复刻 sc2.main._join_game + 窗口参数 + join 同步/检查。只需本方 Bot。

    与原版差异：
    - 直接收单个 Bot（原版收 players 列表取 [1]）
    - SC2Process 多了 resolution / placement
    - join 前等 before_join（与 host 会合）；join 带错误检查 + 重试
    """
    fullscreen: bool = getattr(bot_player, "fullscreen", False)
    async with SC2Process(
        fullscreen=fullscreen,
        resolution=resolution,
        placement=placement,
    ) as server:
        await server.ping()
        # join 方不调 create_game，直接建 Client 等 host 先建好局
        client = Client(server._ws)
        _ai = getattr(bot_player, "ai", None)
        if _ai is not None and getattr(_ai, "raw_affects_selection", None) is not None:
            client.raw_affects_selection = _ai.raw_affects_selection  # type: ignore[assignment]
        await _wait_signal(before_join)
        result = await _checked_join_and_play(
            client, bot_player, realtime, portconfig, game_time_limit
        )
        with contextlib.suppress(Exception):
            await client.leave()
        await client.quit()
        return result
