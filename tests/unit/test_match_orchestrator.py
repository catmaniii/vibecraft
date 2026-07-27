"""MatchOrchestrator 启动计划生成 + 生命周期单测（注入 fake GameProcess，绝不 spawn）。

覆盖（11 条）：
原始 7 条（按评审修订调整）：
1. build_plan 第一个 bot slot = host，其余 = join，guest/computer 分配正确
2. build_plan 共享同一 portconfig json + game_id 各不相同
3. build_plan 窗口平铺无重叠（注入 screen_size=(3440, 1440)）
4. build_plan realtime 来自 room.realtime（M4，不写死 True）
5. start_match 为每个 bot slot spawn 一个进程（async）
6. stop_match 停止全部进程 + 清 process_for
7. solo（1 bot）走原单人路径（mp_role=""，不再有 legacy 概念）

额外 4 条（评审修订新增）：
8.  monitor：fake raw_events yield 序列 → on_player_frame 带 player_id / playing 触发 mark_in_game
9.  monitor：crashed 触发全停 + mark_ended + on_match_ended 回调
10. spawn 失败（第二个 gp.start() 抛）→ 第一个被 stop + room 回 lobby + 异常上抛
11. build_plan 3 真人直接 raise（S1 第二道防线）

asyncio_mode = "auto"（pyproject.toml）— async def 自动作为协程用例运行，无需 @pytest.mark.asyncio。
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from typing import Any

import pytest

from vibecraft.server.match import MatchOrchestrator
from vibecraft.server.room import Room, RoomError

# ------------------------------------------------------------------ #
# 测试用 fake GameProcess（注入，绝不 spawn 真子进程）
# ------------------------------------------------------------------ #


class _FakeGameProcess:
    """最简 GameProcess stub：记录 start/stop 调用，raw_events yield 预设事件列表。"""

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        start_fail: bool = False,
    ) -> None:
        self.started_with: Any = None
        self.stopped = False
        self.is_running = False
        self._events: list[dict[str, Any]] = list(events or [])
        self._start_fail = start_fail

    def start(self, config: Any) -> None:
        if self._start_fail:
            raise RuntimeError("fake_start_failed")
        self.started_with = config
        self.is_running = True

    async def stop(self) -> None:
        self.stopped = True
        self.is_running = False

    async def raw_events(self) -> AsyncIterator[dict[str, Any]]:
        """异步生成器：yield 预设事件列表后结束。"""
        for e in self._events:
            yield e


# ------------------------------------------------------------------ #
# 辅助：构造各种 Room 状态
# ------------------------------------------------------------------ #


def _room_2p() -> Room:
    """2 真人的 starting 房间（alice=host, bob=guest，无电脑）。

    注：SC2 多 agent 局仅支持纯 1v1，有 2 个真人时 Room.add_computer 会拒绝。
    所以 2p 多人局不含电脑（spike 2026-06-12 实测结论）。
    """
    r = Room(max_slots=4)
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.set_race("pid_b", "Zerg")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    r.start("pid_a")
    return r


def _room_solo() -> Room:
    """1 真人 + 1 电脑的 starting 房间（solo 单人路径）。"""
    r = Room(max_slots=4)
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    return r


def _make_factory(*events_per_player: list[dict[str, Any]]) -> Any:
    """创建按顺序返回 _FakeGameProcess 的工厂（第 i 次调用用第 i 组事件）。"""
    event_lists = list(events_per_player)
    idx = [0]

    def factory() -> _FakeGameProcess:
        i = idx[0]
        idx[0] += 1
        events = event_lists[i] if i < len(event_lists) else []
        return _FakeGameProcess(events=events)

    return factory


# ------------------------------------------------------------------ #
# 原始 7 条测试（评审修订后调整）
# ------------------------------------------------------------------ #


def test_build_plan_first_bot_slot_is_host() -> None:
    """第一个 bot slot = host，其余 = join；guest 名单分配正确；join 方不拿 guest 列表。

    注：SC2 多 agent 仅支持 1v1，2p 房间不含电脑（Room.add_computer 已拦截）。
    """
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(3440, 1440))
    plan = orch.build_plan(_room_2p())

    assert len(plan) == 2
    assert plan[0].player_id == "pid_a" and plan[0].config.mp_role == "host"
    assert plan[1].player_id == "pid_b" and plan[1].config.mp_role == "join"
    # host 拿到 guest 名单（bob）；join 方不带
    assert plan[0].config.mp_guest_names == ["bob"]
    assert plan[1].config.mp_guest_names == []
    # 2p 无电脑（SC2 多 agent 1v1 约束）
    assert plan[0].config.mp_computers == []
    assert plan[1].config.mp_computers == []


def test_build_plan_shares_portconfig_and_distinct_game_ids() -> None:
    """两个 PlayerPlan 共享同一 portconfig json，且 game_id 各不相同。"""
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(3440, 1440))
    plan = orch.build_plan(_room_2p())

    # 共享同一 portconfig（跨进程必须一致）
    assert plan[0].config.mp_portconfig_json == plan[1].config.mp_portconfig_json
    assert plan[0].config.mp_portconfig_json != ""

    # game_id 各不相同，且都以 match_ 开头
    assert plan[0].config.game_id != plan[1].config.game_id
    assert plan[0].config.game_id.startswith("match")
    assert plan[1].config.game_id.startswith("match")


def test_build_plan_window_tiling_no_overlap() -> None:
    """窗口横向平铺无重叠 + 保持 4:3 + 不超屏；只有 host（plan[0]）抢焦点。屏 3440×1440。"""
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(3440, 1440))
    plan = orch.build_plan(_room_2p())

    c0, c1 = plan[0].config, plan[1].config
    assert c0.window_x == 0  # host 在左
    assert c1.window_x == 1720  # join 在右（3440 // 2）
    # #586：显式 4:3 尺寸（不再是 window_height=0 全高 sentinel）
    assert (c0.window_width, c0.window_height) == (1720, 1290)  # 1720×3/4=1290，4:3
    assert (c1.window_width, c1.window_height) == (1720, 1290)
    assert c0.window_x + c0.window_width <= c1.window_x  # 无重叠
    assert c1.window_x + c1.window_width <= 3440  # 不超屏（右）
    assert c0.window_height <= 1440  # 不超屏（下）

    # 只有 host 抢焦点（声音）
    assert c0.focus_window is True
    assert all(p.config.focus_window is False for p in plan[1:])


def test_tile_windows_keeps_4_3_and_no_overlap() -> None:
    """_tile_windows 纯函数：保持 4:3、不重叠、不超屏，多种分辨率 + 窗口数。"""
    from vibecraft.server.match import _tile_windows

    for sw, sh in [(3440, 1440), (2560, 1440), (1920, 1080), (1366, 768), (3840, 2160)]:
        for n in (2, 3, 4):
            tiles = _tile_windows(sw, sh, n)
            assert len(tiles) == n
            for x, y, w, h in tiles:
                assert abs(w / h - 4 / 3) < 0.02, f"{sw}x{sh} n={n}: {w}x{h} 非 4:3"
                assert y == 0
                assert x + w <= sw + 1  # 不超屏（右）
                assert h <= sh  # 不超屏（下）
            xs = sorted(tiles, key=lambda t: t[0])
            for a, b in itertools.pairwise(xs):
                assert a[0] + a[2] <= b[0], f"{sw}x{sh} n={n}: 窗口重叠"


def test_build_plan_realtime_from_room() -> None:
    """M4：build_plan 的 realtime 来自 room.realtime，不写死 True。"""
    r = Room(max_slots=4, realtime=False)  # 非实时（调试 / selftest 用）
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    r.start("pid_a")

    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(1920, 1080))
    plan = orch.build_plan(r)

    assert all(p.config.realtime is False for p in plan)


def test_solo_room_uses_solo_path() -> None:
    """单玩家 + 电脑 → mp_role="" (原 run_multiple_games 路径)；无 legacy 概念。"""
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(1920, 1080))
    plan = orch.build_plan(_room_solo())

    assert len(plan) == 1
    # mp_role="" → 走原单人路径
    assert plan[0].config.mp_role == ""
    # 电脑配置进 opponent 字段（run_multiple_games 用）
    assert plan[0].config.opponent_race == "Random"
    assert plan[0].config.opponent_difficulty == "VeryHard"
    # 多人字段全空
    assert plan[0].config.mp_portconfig_json == ""
    assert plan[0].config.mp_guest_names == []


async def test_start_match_spawns_one_process_per_bot_slot() -> None:
    """start_match 为每个 bot slot spawn 一个进程；process_for 可查到。"""
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(3440, 1440))
    room = _room_2p()
    procs = await orch.start_match(room)

    assert set(procs) == {"pid_a", "pid_b"}
    assert all(p.is_running for p in procs.values())
    assert orch.process_for("pid_a") is procs["pid_a"]
    assert orch.process_for("pid_b") is procs["pid_b"]

    # 清理 monitor tasks（空 events 列表，monitor 会立刻结束）
    await asyncio.gather(*list(orch._monitors), return_exceptions=True)


async def test_stop_match_stops_all() -> None:
    """stop_match 停止全部进程；process_for 返回 None。"""
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(3440, 1440))
    room = _room_2p()
    procs = await orch.start_match(room)
    await orch.stop_match()

    assert all(p.stopped for p in procs.values())
    assert orch.process_for("pid_a") is None
    assert orch.process_for("pid_b") is None


# ------------------------------------------------------------------ #
# 额外 4 条测试（评审修订 M2 / S1 / S8）
# ------------------------------------------------------------------ #


async def test_monitor_on_player_frame_and_playing_triggers_mark_in_game() -> None:
    """monitor: on_player_frame 收到带 player_id 的帧；playing 触发 mark_in_game（首次）。

    fake gp 事件序列：
      pid_a → [playing]
      pid_b → [in_game]（不触发 mark_in_game，room 已是 in_game）
    """
    frames_received: list[tuple[str, dict[str, Any]]] = []

    async def on_player_frame(player_id: str, raw: dict[str, Any]) -> None:
        frames_received.append((player_id, raw))

    factory = _make_factory(
        [{"sc2": "playing", "bot": "running"}],  # pid_a 的事件
        [{"sc2": "in_game", "bot": "running"}],  # pid_b 的事件
    )
    room = _room_2p()
    orch = MatchOrchestrator(game_process_factory=factory, screen_size=(3440, 1440))
    await orch.start_match(room, on_player_frame=on_player_frame)

    # 等全部 monitor task 结束
    monitors = list(orch._monitors)
    await asyncio.gather(*monitors, return_exceptions=True)

    # room.mark_in_game() 被 pid_a 的 playing 事件触发
    assert room.state == "in_game"
    # on_player_frame 收到了来自 pid_a 的 playing 帧
    assert any(pid == "pid_a" and raw.get("sc2") == "playing" for pid, raw in frames_received)
    # on_player_frame 也收到了 pid_b 的帧
    assert any(pid == "pid_b" for pid, _ in frames_received)


async def test_monitor_crashed_triggers_stop_and_mark_ended() -> None:
    """monitor: 任一进程 crashed → 全停 + room.mark_ended() + on_match_ended 回调。

    fake gp 事件序列：
      pid_a → [playing]
      pid_b → [playing, crashed]
    pid_b 的 crashed 应触发全停收场。
    """
    match_ended_calls: list[bool] = []
    gp_instances: list[_FakeGameProcess] = []

    async def on_match_ended() -> None:
        match_ended_calls.append(True)

    def factory() -> _FakeGameProcess:
        i = len(gp_instances)
        events_map = [
            [{"sc2": "playing", "bot": "running"}],  # pid_a
            [{"sc2": "playing", "bot": "running"}, {"sc2": "crashed", "bot": "error"}],  # pid_b
        ]
        gp = _FakeGameProcess(events=events_map[i] if i < len(events_map) else [])
        gp_instances.append(gp)
        return gp

    room = _room_2p()
    orch = MatchOrchestrator(game_process_factory=factory, screen_size=(3440, 1440))
    await orch.start_match(room, on_match_ended=on_match_ended)

    # 捕获 monitors 引用（stop_match 会清 orch._monitors）
    monitors = list(orch._monitors)
    # 等所有 monitor 完成（crashed monitor 触发 stop → 其他 monitor 被 cancel）
    await asyncio.gather(*monitors, return_exceptions=True)

    # on_match_ended 被调用一次
    assert len(match_ended_calls) == 1
    # room 回 lobby
    assert room.state == "lobby"
    # 全部进程被 stop
    assert all(gp.stopped for gp in gp_instances)
    # process_for 返回 None（_procs 已清空）
    assert orch.process_for("pid_a") is None
    assert orch.process_for("pid_b") is None


async def test_spawn_failure_stops_first_and_marks_ended() -> None:
    """S8：第二个 gp.start() 抛错 → 第一个被 stop + room 回 lobby + 异常上抛。"""
    instances: list[_FakeGameProcess] = []

    def factory() -> _FakeGameProcess:
        # 第 1 个成功，第 2 个失败
        fail = len(instances) >= 1
        gp = _FakeGameProcess(start_fail=fail)
        instances.append(gp)
        return gp

    room = _room_2p()
    orch = MatchOrchestrator(game_process_factory=factory, screen_size=(3440, 1440))

    with pytest.raises(RuntimeError, match="fake_start_failed"):
        await orch.start_match(room)

    # 第一个进程被 stop（清场）
    assert instances[0].stopped is True
    # room 回 lobby（mark_ended 被调）
    assert room.state == "lobby"
    # _procs 被清空
    assert orch.process_for("pid_a") is None


def test_build_plan_three_bots_raises() -> None:
    """S1 第二道防线：直接调 build_plan 时 3+ 真人直接 raise。

    Room.start() 已经在 room 层拦截，这里测 build_plan 自身的 guard。
    直接设置 room.state = "starting" 绕过 Room.start() 的 3 人限制。
    """
    r = Room(max_slots=4)
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.join("pid_c", "charlie")
    # 直接设 starting 绕过 Room.start() 限制（测 build_plan 自己的 guard）
    r.state = "starting"
    r.match_id = "match_test_3p"

    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess, screen_size=(1920, 1080))
    with pytest.raises(RoomError, match="3\\+"):
        orch.build_plan(r)
