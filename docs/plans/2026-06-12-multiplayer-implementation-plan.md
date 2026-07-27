# 多人联网阶段 0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> 设计真理源：`docs/plans/2026-06-12-multiplayer-design.md`。

**Goal:** 一台 PC 跑多个 SC2 实例 host/join 成一局，每实例一个 bot，多个玩家各用手机（入口页输用户名+选服务器 → lobby 选位 → 对局）接入指挥。

**Architecture:** 现有「一部手机 ↔ 一个 GameProcess」链路整体保留，server 加 RoomManager（房间状态机+slot）和 MatchOrchestrator（多 GameProcess 编排），消息按 player_id 路由。SC2 跨进程联机走 python-sc2 `Portconfig` + 自写 host/join runner（`_host_game/_join_game` 不透传窗口参数）。

**Tech Stack:** python-sc2 (BurnySc2) Portconfig host/join、multiprocessing spawn、websockets、Vue 3 + Tailwind (PWA)、pytest（mock，绝不拉起 SC2——根级 conftest 有 `_block_sc2_child_entry` 保险）。

**任务依赖：** Task 1（spike，e2e 闸门）→ Task 2 → Task 3/4（并行）→ Task 5 → Task 6 → Task 7 → Task 8/9（PWA，依赖 7）→ Task 10（视频）→ Task 11（selftest+文档）。Task 2/3/4/8 相对独立可并行派发。

---

## ⚠️ 评审修订（2026-06-12 Opus 独立评审，**优先级高于下面各 task 原文**）

独立评审发现 4 个设计级问题（M1-M4，必须按此执行）+ 8 个 task 内修正（S1-S8）。
执行 task 时若与原文冲突，**以本节为准**。

### M1. WebRTC 改 per-player 生命周期（Task 10 范围扩大）
现有 `WebRtcManager.handle_offer` 开头 `if self._pcs: await self.close_all()` —— 任何新
offer 会**关掉所有人**的 PeerConnection（单客户端 supersede 假设）。多人必改：
`_pcs`/`_tracks` 改 `dict[player_id, ...]`，`handle_offer(sdp, type, player_id, sc2_pid)`，
新 offer 只 supersede **同一 player_id** 的旧 PC。每 PC 一个独立
`SC2ScreenCapture(pid_filter=sc2_pid)`。

### M2. match 生命周期归 RoomService 的 per-match monitor，连接只订阅（Task 5/6 架构件）
**禁止**沿用「每条 WS 连接自己跑 status pump」模型（断线即失管；guest 连接没有任何
pump 启动路径）。改为：
- `RoomService.start_match` 为每个 (player_id, GameProcess) 起一个 **connection 无关**的
  asyncio monitor task：唯一消费者消费 `gp.raw_events()`（**不变量：每 GameProcess 恰一个
  消费者**），把帧通过 `registry.connection_of(player_id).send_text(...)` 推给该玩家
  **当前**连接（没连着就丢弃，重连自动续上）。
- monitor 同时驱动房间状态机：首个 `playing` → `room.mark_in_game()` + 广播；任一
  `ended/crashed` → `room_service.stop_match()`（停全部）+ `mark_ended` + 广播回 lobby。
- spawn 失败（S8）：start_match 中任何一路 `start()` 抛错 → 立即 stop 已起的、回 lobby、
  广播 `room_error`。
- WS 连接断开只做 `registry.detach`（lobby 态另加 `room.leave`）；**不再 cancel pump**。
- 单人 legacy `start_game` 帧路径同样迁到 monitor 模型（见 M3）。

### M3. GameProcess 唯一 owner = MatchOrchestrator，删 legacy_gp 双轨
solo 不再有独立 `legacy_gp`：旧 `start_game` 帧改薄 shim —— 把发帧连接的玩家 join 进
房间（若未 join）、按帧 config 加一个电脑、自动 ready、`start_match`。
`RoomService.game_process_for(pid)` 只查 orchestrator。`BotService.game_process` 属性
保留但指向 orchestrator 的 solo 进程（旧单测同步更新）。Task 5 的
`test_solo_room_uses_solo_path` 仍有效（solo 走 `mp_role=""` 原单人 SC2 路径），变化只在
「谁持有进程」。

### M4. realtime 进房间配置，不许写死
`Room` 加 `realtime: bool`（默认取 `ServiceConfig.default_realtime`；`lobby_start` 帧可带
`realtime` 覆盖，仅房主）。`build_plan` 读 `room.realtime`。Task 11 selftest 用
non-realtime + mock LLM（CLAUDE.md 既有纪律）。

### S 系列（在对应 task 里顺手改）
- **S1（Task 1）**：spike 加 `--kill-host-after`（验 host 死后 join 的结局，崩溃不对称）+
  `--join-delay <s>`（非对称启动：join 比 host 晚 N 秒起，验 SC2 join 等待容忍度）。
  spike 结论须写明「v0 实测仅 2 真人」；`build_plan` 对 >2 个 bot slot 先
  `raise RoomError("3+ 真人玩家暂未支持")` 拦住（实测过再放开）。
- **S2（Task 5）**：`_SCREEN_W` 不许硬编码 3440 —— 父进程用 DPI-aware workarea API
  检测（参考 `game_process.py` `_child_entry` 里现成逻辑，抽成共用函数），失败 fallback 1920。
- **S3（Task 2）**：`_focus_sc2_window` 多实例会按标题抓错窗 —— 加 `pid_whitelist`
  参数（本子进程的 SC2 子孙 PID），只 focus 自己的窗。
- **S4（Task 10）**：`sc2_pid` 未就绪时收到 webrtc_offer → 回
  `{"type":"webrtc_answer","error":"sc2 not ready, retry"}`，PWA 收到 error 带 retry（2s 退避）。
- **S5（Task 2）**：多人分支必须包 try/except → `_put("crashed", "error", detail=...)`（对齐单人路径）。
- **S6（Task 6）**：monitor 直接持有自己消费的那个 gp 引用，**不准**在循环内重新解析
  `game_process_for()`。
- **S7（Task 11）**：ARCHITECTURE.md 不变量加两条：「A 的指令绝不进 B 的 down_q」
  「每 GameProcess 的 raw_events 恰一个消费者（monitor）」。
- **S8**：并入 M2。
- 其余评审建议（PlayerChannel 是文档高估、多 audio grabber 浪费、psutil 缺失全局杀兜底、
  极小窗口下限）记 TASKS.md 待办，不阻塞 v0。

**全局约定（每个 task 都适用）：**
- 跑单测：`uv run --no-sync pytest tests/unit/test_<file>.py -x -q`（**必须 `--no-sync`**，否则 uv 会卸 ares）。
- 跑 lint：`uv run --no-sync ruff check . && uv run --no-sync ruff format --check .`
- 每个 commit 前把改动写进 `CHANGELOG.md` 的 `[Unreleased]`（日期块 + 新增/变更/修正），commit message 与 changelog 同源（完整条目，不是一句标题）。
- 单测 mock 一切外部（sc2 / 子进程 / WS）；`filterwarnings = ["error"]` 开着，新依赖先查 deprecation 噪音。
- 中文注释/commit；建筑 hotkey 简称。

---

## Task 1: Spike — 跨进程 host/join 链路验证（e2e 闸门，先行）

**目的：** 证明两个独立进程各跑一个 SC2 实例能 host/join 成一局多人对局。这是全计划最大未知数，**不通过则后续任务全部暂停、回设计**。顺带观察：① 一方被 kill 后另一方的结局（预期 Victory）② 3 人局（2 bot + 1 内置 AI）的敌我关系（验 FFA/同盟，决定 v0 是否能做组队）③ 双实例 realtime 帧率。

**Files:**
- Create: `src/vibecraft/server/sc2_multiplayer.py`
- Create: `scripts/multiplayer_smoke.py`
- Test: `tests/unit/test_sc2_multiplayer.py`

**Step 1: 写 runner 模块的失败单测**

`tests/unit/test_sc2_multiplayer.py`：

```python
"""sc2_multiplayer runner 的纯逻辑单测（不拉起 SC2）。"""
from vibecraft.server.sc2_multiplayer import build_host_players, portconfig_roundtrip_json


def test_build_host_players_orders_bots_before_computers():
    """host create_game 的 players：本方 bot 在前，guest 占位 bot 居中，Computer 殿后。"""
    players = build_host_players(
        my_race="Protoss",
        my_name="alice",
        guest_names=["bob"],
        computers=[{"race": "Terran", "difficulty": "Hard"}],
    )
    # 3 个玩家：alice(带 ai 占位 None 由调用方填), bob 占位, 1 个 Computer
    assert len(players) == 3
    assert players[0].name == "alice"
    assert players[1].name == "bob"
    from sc2.player import Computer
    assert isinstance(players[2], Computer)


def test_portconfig_roundtrip_json():
    """Portconfig 经 as_json → from_json round-trip，端口不变（跨进程传递的根基）。"""
    pc_json = portconfig_roundtrip_json(guests=1)
    import json
    data = json.loads(pc_json)
    assert len(data["server"]) == 2
    assert len(data["players"]) == 1 and len(data["players"][0]) == 2
```

**Step 2: 跑测确认失败**

Run: `uv run --no-sync pytest tests/unit/test_sc2_multiplayer.py -x -q`
Expected: FAIL `ModuleNotFoundError: vibecraft.server.sc2_multiplayer`

**Step 3: 写 runner 模块**

`src/vibecraft/server/sc2_multiplayer.py`：

```python
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

from typing import Any

from sc2.main import _play_game, _setup_host_game
from sc2.client import Client
from sc2.data import Difficulty, Race
from sc2.player import AbstractPlayer, Bot, Computer
from sc2.portconfig import Portconfig
from sc2.sc2process import SC2Process


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


def portconfig_roundtrip_json(guests: int) -> str:
    """新建一组 Portconfig 并 round-trip 校验后返回 json（spike/单测用）。"""
    pc = Portconfig.contiguous_ports(guests=guests)
    return Portconfig.from_json(pc.as_json).as_json


async def host_game(
    map_settings: Any,
    players: list[AbstractPlayer],
    realtime: bool,
    portconfig: Portconfig,
    *,
    resolution: tuple[int, int] | None = None,
    placement: tuple[int, int] | None = None,
    game_time_limit: int | None = None,
) -> Any:
    """复刻 sc2.main._host_game + 窗口参数。players[0] 是本方 bot。"""
    async with SC2Process(
        fullscreen=players[0].fullscreen,
        resolution=resolution,
        placement=placement,
    ) as server:
        await server.ping()
        client = await _setup_host_game(server, map_settings, players, realtime)
        if getattr(players[0].ai, "raw_affects_selection", None) is not None:
            client.raw_affects_selection = players[0].ai.raw_affects_selection
        result = await _play_game(players[0], client, realtime, portconfig, game_time_limit)
        try:
            await client.leave()
        except Exception:
            pass
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
) -> Any:
    """复刻 sc2.main._join_game + 窗口参数。只需本方 Bot（不需要完整 players 列表）。"""
    async with SC2Process(
        fullscreen=bot_player.fullscreen,
        resolution=resolution,
        placement=placement,
    ) as server:
        await server.ping()
        client = Client(server._ws)
        if getattr(bot_player.ai, "raw_affects_selection", None) is not None:
            client.raw_affects_selection = bot_player.ai.raw_affects_selection
        result = await _play_game(bot_player, client, realtime, portconfig, game_time_limit)
        try:
            await client.leave()
        except Exception:
            pass
        await client.quit()
        return result
```

注意 `_play_game(bot_player, ...)`：sc2.main 里 `_join_game` 用的是 `players[1]`，我们的变体直接收单个 Bot —— `_play_game` 第一个参数就是要玩的 player，行为等价。

**Step 4: 跑测确认通过**

Run: `uv run --no-sync pytest tests/unit/test_sc2_multiplayer.py -x -q`
Expected: 2 passed

**Step 5: 写 spike 脚本**

`scripts/multiplayer_smoke.py`（自验脚本，不进单测）：

```python
"""multiplayer_smoke.py — 跨进程 host/join 链路 spike（阶段 0 多人联网闸门）。

两个独立子进程各起一个 SC2 实例，经共享 Portconfig host/join 成一局多人局，
bot 用最小 BotAI stub（隔离链路问题，不拉 vibecraft 全栈）。

用法（.venv 里跑）：
  # 基本验证：2 bot 互打，non-realtime 跑 300 game-s，窗口并排
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py

  # 实时模式肉眼看（顺带观察双实例帧率）
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --realtime

  # 崩溃行为：60s 后 kill join 方，观察 host 结局（预期 Victory）
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --kill-join-after 60

  # 敌我关系观察：host 建 3 人局（2 bot + 1 VeryEasy 电脑），看是否 FFA
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --with-computer

判读：
  两个子进程都打出 "RESULT role=... result=..." 且无 traceback = PASS。
  --kill-join-after 模式：host 打出 Victory = 崩溃兜底成立。
  --with-computer 模式：观察电脑是否同时攻击两个 bot（FFA 证据）。
"""
from __future__ import annotations

import argparse
import asyncio
import multiprocessing
import sys
import time


def _child(role: str, pc_json: str, realtime: bool, with_computer: bool, seconds: int) -> None:
    from sc2 import maps
    from sc2.bot_ai import BotAI
    from sc2.data import Race
    from sc2.player import Bot
    from sc2.portconfig import Portconfig

    from vibecraft.server.sc2_multiplayer import build_host_players, host_game, join_game

    class SmokeBot(BotAI):
        """攒兵 a 过去的最小 bot：证明真的在对打，不是各自挂机。"""

        async def on_step(self, iteration: int) -> None:
            if self.time > seconds:
                await self.client.leave()
                return
            # 极简行为：补农民 + BE + BG + 叉子 a 中央
            ...  # 实现时填：train probe / build pylon+gateway / 4 兵后 attack 地图中心

    portconfig = Portconfig.from_json(pc_json)
    sc2_map = maps.get("DaybreakLE")
    me = Bot(Race.Protoss, SmokeBot(), name=f"smoke_{role}")

    if role == "host":
        computers = [{"race": "Terran", "difficulty": "VeryEasy"}] if with_computer else []
        players = build_host_players(
            "Protoss", me.name, guest_names=["smoke_join"], computers=computers, my_ai=me.ai
        )
        result = asyncio.run(host_game(
            sc2_map, players, realtime, portconfig,
            resolution=(1280, 720), placement=(0, 0),
        ))
    else:
        result = asyncio.run(join_game(
            me, realtime, portconfig,
            resolution=(1280, 720), placement=(1300, 0),
        ))
    print(f"RESULT role={role} result={result}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--with-computer", action="store_true")
    ap.add_argument("--kill-join-after", type=float, default=0.0)
    ap.add_argument("--seconds", type=int, default=300)
    args = ap.parse_args()

    from sc2.portconfig import Portconfig
    pc_json = Portconfig.contiguous_ports(guests=1).as_json

    ctx = multiprocessing.get_context("spawn")
    host = ctx.Process(target=_child, args=("host", pc_json, args.realtime, args.with_computer, args.seconds))
    join = ctx.Process(target=_child, args=("join", pc_json, args.realtime, args.with_computer, args.seconds))
    host.start()
    join.start()

    if args.kill_join_after > 0:
        time.sleep(args.kill_join_after)
        join.terminate()
        print("KILLED join process", flush=True)

    host.join(timeout=900)
    join.join(timeout=30)
    ok = host.exitcode == 0
    print(f"SMOKE {'PASS' if ok else 'FAIL'} host_exit={host.exitcode} join_exit={join.exitcode}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

（实现时把 SmokeBot.on_step 的 `...` 填成真实极简宏：见 `scripts/headless_smoke.py` 里类似 stub。）

**Step 6: 跑 spike（需 SC2，三个模式都跑）**

```bash
.venv/Scripts/python.exe scripts/multiplayer_smoke.py                       # 基本链路
.venv/Scripts/python.exe scripts/multiplayer_smoke.py --kill-join-after 60  # 崩溃行为
.venv/Scripts/python.exe scripts/multiplayer_smoke.py --with-computer      # 敌我观察
```

Expected: 三跑全 PASS。**把观察结论（崩溃方结局 / FFA 证据 / realtime 帧率）记进本 plan 文件尾部「Spike 结论」节** —— 后续 task 依赖这些事实。

**Step 7: Commit**

```bash
git add src/vibecraft/server/sc2_multiplayer.py scripts/multiplayer_smoke.py tests/unit/test_sc2_multiplayer.py CHANGELOG.md docs/plans/2026-06-12-multiplayer-implementation-plan.md
git commit  # message: feat(multiplayer): 跨进程 host/join runner + smoke spike（含结论）
```

---

## Task 2: GameConfig 多人字段 + `_child_entry` 多人分支

**Files:**
- Modify: `src/vibecraft/server/game_process.py`（GameConfig ~line 52；`_child_entry` 的 run_multiple_games 调用 ~line 441）
- Test: `tests/unit/test_game_process_multiplayer.py`

**Step 1: 失败单测**

```python
"""GameConfig 多人字段 + 子进程多人分支的纯逻辑单测。"""
import pickle

from vibecraft.server.game_process import GameConfig


def test_game_config_multiplayer_fields_default_solo():
    cfg = GameConfig()
    assert cfg.mp_role == ""           # 默认单人，走原 run_multiple_games 路径
    assert cfg.mp_portconfig_json == ""
    assert cfg.mp_computers == []


def test_game_config_multiplayer_picklable():
    """跨 spawn 边界必须可 pickle。"""
    cfg = GameConfig(
        mp_role="host",
        mp_portconfig_json='{"server": [1, 2], "players": [[3, 4]]}',
        mp_guest_names=["bob"],
        mp_computers=[{"race": "Terran", "difficulty": "Hard"}],
        mp_player_name="alice",
    )
    assert pickle.loads(pickle.dumps(cfg)).mp_role == "host"
```

**Step 2: 跑测确认失败**（TypeError: unexpected keyword）

**Step 3: 实现**

GameConfig 加字段（全部基本类型，保 picklable）：

```python
    # ---- 阶段 0 多人联网（2026-06-12 设计）----
    # mp_role: "" = 单人（原 run_multiple_games 路径，完全不变）
    #          "host" = 本进程 create_game + 打 players[0]
    #          "join" = 本进程加入 host 创建的局
    mp_role: str = ""
    mp_portconfig_json: str = ""              # Portconfig.as_json，全部参与进程共享同一份
    mp_player_name: str = "VibeCraft"         # 本方 bot 显示名（lobby 用户名）
    mp_guest_names: list[str] = field(default_factory=list)   # host 用：guest 占位名
    mp_computers: list[dict[str, str]] = field(default_factory=list)  # host 用：内置 AI
    mp_game_time_limit: int = 7200            # 多人局兜底时限（秒），防双方挂机永不结束
```

`_child_entry` 在现有 `run_multiple_games(...)` 调用处加分支（保持 `_put` 状态推送不变）：

```python
        if config.mp_role:
            # ---- 多人分支：host/join 跨进程联机（sc2_multiplayer runner）----
            import asyncio as _asyncio
            from sc2.player import Bot as _Bot
            from sc2.portconfig import Portconfig as _Portconfig

            from vibecraft.server.sc2_multiplayer import (
                build_host_players,
                host_game,
                join_game,
            )

            portconfig = _Portconfig.from_json(config.mp_portconfig_json)
            me = _Bot(Race[config.my_race], bot_instance, name=config.mp_player_name)
            if config.mp_role == "host":
                players = build_host_players(
                    config.my_race, config.mp_player_name,
                    guest_names=list(config.mp_guest_names),
                    computers=list(config.mp_computers),
                    my_ai=bot_instance,
                )
                _asyncio.run(host_game(
                    sc2_map, players, config.realtime, portconfig,
                    resolution=(config.window_width, config.window_height),
                    placement=(config.window_x, config.window_y),
                    game_time_limit=config.mp_game_time_limit,
                ))
            else:
                _asyncio.run(join_game(
                    me, config.realtime, portconfig,
                    resolution=(config.window_width, config.window_height),
                    placement=(config.window_x, config.window_y),
                    game_time_limit=config.mp_game_time_limit,
                ))
            _put("ended", "idle")
        else:
            run_multiple_games([...])  # ←原代码原样保留
            _put("ended", "idle")
```

**Step 4: 跑测确认通过** + 跑全量 `uv run --no-sync pytest tests/unit -x -q`（确认没碰坏原路径）

**Step 5: 真局自验（用 Task 1 的两套设施交叉验证）**

写一个临时驱动（或给 multiplayer_smoke.py 加 `--via-gameprocess` 模式）：父进程构造两个 `GameProcess` + 两份 mp GameConfig（host/join，共享 portconfig json），`start()` 后分别消费 `status_events()`，断言两边都到 `playing`。跑通后这就是 MatchOrchestrator 的雏形证据。

**Step 6: Commit**（feat(multiplayer): GameConfig mp 字段 + 子进程 host/join 分支）

---

## Task 3: Room 模型（房间状态机 + slot，纯逻辑）

**Files:**
- Create: `src/vibecraft/server/room.py`
- Test: `tests/unit/test_room.py`

**Step 1: 失败单测**（核心用例，写全）

```python
"""Room 状态机 + slot 仲裁单测（纯逻辑，无 IO）。"""
import pytest

from vibecraft.server.room import Room, RoomError


def _room() -> Room:
    return Room(map_name="DaybreakLE", max_slots=4)


def test_first_join_becomes_host_and_takes_slot0():
    r = _room()
    r.join("pid_a", "alice")
    assert r.host_player_id == "pid_a"
    assert r.slots[0].kind == "bot" and r.slots[0].player_id == "pid_a"


def test_join_assigns_next_open_slot():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    assert r.slots[1].player_id == "pid_b"


def test_set_race_and_team():
    r = _room()
    r.join("pid_a", "alice")
    r.set_race("pid_a", "Zerg")
    r.set_team("pid_a", 2)
    assert r.slots[0].race == "Zerg" and r.slots[0].team == 2


def test_host_adds_computer():
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Terran", difficulty="Hard")
    comp = [s for s in r.slots if s.kind == "computer"]
    assert len(comp) == 1 and comp[0].difficulty == "Hard"


def test_non_host_cannot_add_computer():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    with pytest.raises(RoomError):
        r.add_computer("pid_b", race="Terran", difficulty="Hard")


def test_start_requires_all_humans_ready_and_two_filled():
    r = _room()
    r.join("pid_a", "alice")
    with pytest.raises(RoomError):
        r.start("pid_a")          # 只有 1 个参与者，不能开
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    with pytest.raises(RoomError):
        r.start("pid_a")          # alice 还没 ready
    r.set_ready("pid_a", True)
    r.start("pid_a")
    assert r.state == "starting"


def test_leave_in_lobby_frees_slot_and_transfers_host():
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.leave("pid_a")
    assert r.host_player_id == "pid_b"
    assert r.slots[0].kind == "open"


def test_rejoin_same_pid_is_idempotent():
    """同 pid 重连（手机刷新）不占第二个 slot。"""
    r = _room()
    r.join("pid_a", "alice")
    r.join("pid_a", "alice")
    assert sum(1 for s in r.slots if s.player_id == "pid_a") == 1


def test_state_transitions():
    r = _room()
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryEasy")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    r.mark_in_game()
    assert r.state == "in_game"
    r.mark_ended()
    assert r.state == "lobby"      # 局终回 lobby，slot 保留、ready 清零
    assert r.slots[0].ready is False


def test_to_frame_shape():
    """room_state 下行帧的形状（PWA 依赖）。"""
    r = _room()
    r.join("pid_a", "alice")
    f = r.to_frame()
    assert f["type"] == "room_state"
    assert f["state"] == "lobby"
    assert f["host_player_id"] == "pid_a"
    assert f["slots"][0]["name"] == "alice"
```

**Step 2: 跑测确认失败**

**Step 3: 实现 `src/vibecraft/server/room.py`**

```python
"""房间状态机 + slot 模型（阶段 0 多人联网，纯逻辑无 IO）。

设计：docs/plans/2026-06-12-multiplayer-design.md §3.2。
状态机：lobby → starting → in_game → (ended 瞬态) → lobby。
v0 一个 server 一个房间；team 字段进模型/UI，引擎层同盟以 Task 1 spike 结论为准。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

SlotKind = Literal["open", "bot", "computer", "closed"]
RoomState = Literal["lobby", "starting", "in_game"]


class RoomError(Exception):
    """房间操作被拒（带玩家可读原因，WS 层转 toast）。"""


@dataclass
class Slot:
    index: int
    kind: SlotKind = "open"
    team: int = 1
    race: str = "Protoss"
    difficulty: str = "VeryHard"   # kind=computer 时有效
    player_id: str = ""            # kind=bot 时绑定的玩家
    name: str = ""                 # 显示名（玩家用户名 / "电脑(Hard)"）
    ready: bool = False

    def clear(self) -> None:
        self.kind = "open"
        self.player_id = ""
        self.name = ""
        self.ready = False


class Room:
    def __init__(self, map_name: str = "DaybreakLE", max_slots: int = 4) -> None:
        self.map_name = map_name
        self.state: RoomState = "lobby"
        self.slots: list[Slot] = [Slot(index=i) for i in range(max_slots)]
        self.host_player_id: str = ""
        self.match_id: str = ""

    # ---- 查询 ----
    def slot_of(self, player_id: str) -> Slot | None:
        for s in self.slots:
            if s.kind == "bot" and s.player_id == player_id:
                return s
        return None

    def bot_slots(self) -> list[Slot]:
        return [s for s in self.slots if s.kind == "bot"]

    def filled_slots(self) -> list[Slot]:
        return [s for s in self.slots if s.kind in ("bot", "computer")]

    # ---- 操作（全部 lobby 态才允许，state 校验统一在 _require_lobby）----
    def _require_lobby(self) -> None:
        if self.state != "lobby":
            raise RoomError("对局进行中，不能改房间设置")

    def _require_host(self, player_id: str) -> None:
        if player_id != self.host_player_id:
            raise RoomError("只有房主能做这个操作")

    def join(self, player_id: str, name: str) -> Slot:
        self._require_lobby()
        existing = self.slot_of(player_id)
        if existing is not None:
            existing.name = name   # 重连：更新显示名即可
            return existing
        for s in self.slots:
            if s.kind == "open":
                s.kind = "bot"
                s.player_id = player_id
                s.name = name
                s.ready = False
                if not self.host_player_id:
                    self.host_player_id = player_id
                return s
        raise RoomError("房间满了")

    def leave(self, player_id: str) -> None:
        s = self.slot_of(player_id)
        if s is None:
            return
        s.clear()
        if player_id == self.host_player_id:
            remaining = self.bot_slots()
            self.host_player_id = remaining[0].player_id if remaining else ""

    def set_race(self, player_id: str, race: str) -> None:
        self._require_lobby()
        if race not in ("Protoss", "Terran", "Zerg", "Random"):
            raise RoomError(f"未知种族 {race}")
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上")
        s.race = race

    def set_team(self, player_id: str, team: int) -> None:
        self._require_lobby()
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上")
        s.team = int(team)

    def set_ready(self, player_id: str, ready: bool) -> None:
        self._require_lobby()
        s = self.slot_of(player_id)
        if s is None:
            raise RoomError("你不在任何 slot 上")
        s.ready = bool(ready)

    def add_computer(self, requester: str, race: str, difficulty: str) -> Slot:
        self._require_lobby()
        self._require_host(requester)
        for s in self.slots:
            if s.kind == "open":
                s.kind = "computer"
                s.race = race
                s.difficulty = difficulty
                s.name = f"电脑({difficulty})"
                s.ready = True
                return s
        raise RoomError("房间满了")

    def remove_slot(self, requester: str, index: int) -> None:
        """房主移除 computer / 踢人。"""
        self._require_lobby()
        self._require_host(requester)
        if not 0 <= index < len(self.slots):
            raise RoomError("slot 不存在")
        s = self.slots[index]
        if s.player_id == requester:
            raise RoomError("不能踢自己")
        s.clear()
        s.kind = "open"

    # ---- 开局 / 状态推进 ----
    def start(self, requester: str) -> None:
        self._require_lobby()
        self._require_host(requester)
        filled = self.filled_slots()
        if len(filled) < 2:
            raise RoomError("至少要 2 个参与者")
        humans = self.bot_slots()
        if not humans:
            raise RoomError("至少要 1 个玩家")
        not_ready = [s.name for s in humans if not s.ready]
        if not_ready:
            raise RoomError(f"还有玩家未准备：{'、'.join(not_ready)}")
        self.state = "starting"
        self.match_id = f"match_{time.strftime('%Y%m%d_%H%M%S')}"

    def mark_in_game(self) -> None:
        self.state = "in_game"

    def mark_ended(self) -> None:
        """局终回 lobby：slot 保留，ready 清零（再来一局少点一次）。"""
        self.state = "lobby"
        self.match_id = ""
        for s in self.bot_slots():
            s.ready = False

    # ---- 序列化（room_state 下行帧）----
    def to_frame(self) -> dict[str, Any]:
        return {
            "type": "room_state",
            "state": self.state,
            "map": self.map_name,
            "host_player_id": self.host_player_id,
            "match_id": self.match_id,
            "slots": [
                {
                    "index": s.index, "kind": s.kind, "team": s.team,
                    "race": s.race, "difficulty": s.difficulty,
                    "player_id": s.player_id, "name": s.name, "ready": s.ready,
                }
                for s in self.slots
            ],
        }
```

**Step 4: 跑测确认通过**

**Step 5: Commit**（feat(multiplayer): Room 状态机 + slot 模型）

---

## Task 4: RoomRegistry 多连接化（per-player 顶旧 + 广播）

**Files:**
- Modify: `src/vibecraft/server/tokens.py`（RoomRegistry，~line 30-71）
- Test: `tests/unit/test_tokens.py`（已有文件，扩展）

**Step 1: 失败单测**（追加到现有 test 文件）

```python
class _FakeConn:
    def __init__(self) -> None:
        self.closed_reason: str | None = None
        self.sent: list[str] = []

    async def close(self, reason: str) -> None:
        self.closed_reason = reason

    async def send_text(self, frame: str) -> None:
        self.sent.append(frame)


async def test_attach_evicts_same_player_only():
    reg = RoomRegistry(token="t")
    a1, a2, b = _FakeConn(), _FakeConn(), _FakeConn()
    assert reg.attach(a1, player_id="pa") is None
    assert reg.attach(b, player_id="pb") is None        # 不同玩家共存
    assert reg.attach(a2, player_id="pa") is a1         # 同玩家顶旧
    assert reg.connection_of("pa") is a2


async def test_detach_only_clears_current():
    reg = RoomRegistry(token="t")
    a1, a2 = _FakeConn(), _FakeConn()
    reg.attach(a1, player_id="pa")
    reg.attach(a2, player_id="pa")
    reg.detach(a1)                       # 已被顶掉的旧连接迟到断开
    assert reg.connection_of("pa") is a2  # 不能误清新连接


async def test_broadcast_sends_to_all():
    reg = RoomRegistry(token="t")
    a, b = _FakeConn(), _FakeConn()
    reg.attach(a, player_id="pa")
    reg.attach(b, player_id="pb")
    await reg.broadcast('{"type":"room_state"}')
    assert a.sent == ['{"type":"room_state"}'] and b.sent == a.sent
```

**Step 2: 跑测确认失败**

**Step 3: 实现**

`Connection` Protocol 加 `async def send_text(self, frame: str) -> None: ...`；RoomRegistry 改：

```python
class RoomRegistry:
    """单 token + per-player 单活跃连接（2026-06-12 多人化）。

    一个 server 一个 token（房间码）；同 token 下多玩家各一条连接，
    同 player_id 重连顶旧（手机刷新场景）。
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token or generate_room_token()
        self._conns: dict[str, Connection] = {}

    @property
    def token(self) -> str:
        return self._token

    def verify(self, token: str) -> bool:
        return secrets.compare_digest(token, self._token)

    def connection_of(self, player_id: str) -> Connection | None:
        return self._conns.get(player_id)

    @property
    def player_ids(self) -> list[str]:
        return list(self._conns)

    def attach(self, conn: Connection, player_id: str) -> Connection | None:
        evicted = self._conns.get(player_id)
        self._conns[player_id] = conn
        return evicted

    def detach(self, conn: Connection) -> None:
        for pid, c in list(self._conns.items()):
            if c is conn:
                del self._conns[pid]
                return

    async def broadcast(self, frame: str) -> None:
        """给所有活跃连接发同一帧（room_state 等）。单连接挂了不影响其他。"""
        for conn in list(self._conns.values()):
            try:
                await conn.send_text(frame)
            except Exception:  # noqa: BLE001 — 单点失败不阻断广播
                pass
```

`WsConnection` 加 `send_text`（`await self._ws.send(frame)` 包一层）；`ws.py` handler 的 `registry.attach(conn)` 调用点同步改成带 player_id（Task 6 完成完整接入，此处先让旧调用编译过：握手暂用 `player_id="default"`）。检查 `tests/unit` 里现有用到 `attach/detach/active_connection` 的测试并同步更新（`active_connection` 属性删除 → 改用 `connection_of`）。

**Step 4: 跑测确认通过** + 全量 unit

**Step 5: Commit**（feat(multiplayer): RoomRegistry per-player 多连接 + 广播）

---

## Task 5: MatchOrchestrator（房间配置 → 多 GameProcess 编排）

**Files:**
- Create: `src/vibecraft/server/match.py`
- Test: `tests/unit/test_match_orchestrator.py`

**Step 1: 失败单测**

```python
"""MatchOrchestrator 启动计划生成 + 生命周期单测（注入 fake GameProcess，绝不 spawn）。"""
from vibecraft.server.match import MatchOrchestrator
from vibecraft.server.room import Room


class _FakeGameProcess:
    def __init__(self) -> None:
        self.started_with = None
        self.stopped = False
        self.is_running = False

    def start(self, config) -> None:
        self.started_with = config
        self.is_running = True

    async def stop(self) -> None:
        self.stopped = True
        self.is_running = False


def _room_2p_1c() -> Room:
    r = Room(max_slots=4)
    r.join("pid_a", "alice")
    r.join("pid_b", "bob")
    r.set_race("pid_b", "Zerg")
    r.add_computer("pid_a", race="Terran", difficulty="Hard")
    r.set_ready("pid_a", True)
    r.set_ready("pid_b", True)
    r.start("pid_a")
    return r


def test_build_plan_first_bot_slot_is_host():
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    plan = orch.build_plan(_room_2p_1c())
    assert plan[0].player_id == "pid_a" and plan[0].config.mp_role == "host"
    assert plan[1].player_id == "pid_b" and plan[1].config.mp_role == "join"
    # host 拿到 guest 名单 + 电脑名单；join 不带
    assert plan[0].config.mp_guest_names == ["bob"]
    assert plan[0].config.mp_computers == [{"race": "Terran", "difficulty": "Hard"}]
    assert plan[1].config.mp_computers == []


def test_build_plan_shares_portconfig_and_distinct_game_ids():
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    plan = orch.build_plan(_room_2p_1c())
    assert plan[0].config.mp_portconfig_json == plan[1].config.mp_portconfig_json != ""
    assert plan[0].config.game_id != plan[1].config.game_id
    assert plan[0].config.game_id.startswith(_room_2p_1c().match_id[:5] or "match")


def test_build_plan_window_tiling_no_overlap():
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    plan = orch.build_plan(_room_2p_1c())
    assert plan[0].config.window_x != plan[1].config.window_x
    assert all(p.config.focus_window is False for p in plan[1:])  # 只有 host 抢焦点
    assert plan[0].config.focus_window is True


def test_start_match_spawns_one_process_per_bot_slot():
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    room = _room_2p_1c()
    procs = orch.start_match(room)
    assert set(procs) == {"pid_a", "pid_b"}
    assert all(p.is_running for p in procs.values())
    assert orch.process_for("pid_a") is procs["pid_a"]


async def test_stop_match_stops_all():
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    room = _room_2p_1c()
    procs = orch.start_match(room)
    await orch.stop_match()
    assert all(p.stopped for p in procs.values())
    assert orch.process_for("pid_a") is None


def test_solo_room_uses_solo_path():
    """单玩家 + 电脑 → mp_role 留空（走原 run_multiple_games 单人路径，兼容现有一切）。"""
    r = Room(max_slots=4)
    r.join("pid_a", "alice")
    r.add_computer("pid_a", race="Random", difficulty="VeryHard")
    r.set_ready("pid_a", True)
    r.start("pid_a")
    orch = MatchOrchestrator(game_process_factory=_FakeGameProcess)
    plan = orch.build_plan(r)
    assert len(plan) == 1
    assert plan[0].config.mp_role == ""
    assert plan[0].config.opponent_race == "Random"
    assert plan[0].config.opponent_difficulty == "VeryHard"
```

**Step 2: 跑测确认失败**

**Step 3: 实现 `src/vibecraft/server/match.py`**

```python
"""MatchOrchestrator：房间配置 → SC2 启动计划 → 多 GameProcess 生命周期。

设计 docs/plans/2026-06-12-multiplayer-design.md §3.3。
关键决策：
- 第一个 bot slot = host（create_game + 代填电脑），其余 bot slot = join。
- 单玩家 + 电脑 = 原单人路径（mp_role=""），现有行为零变化。
- 窗口平铺：N 路实例横向均分主屏宽（参考 stealth_saturation_selftest 网格）。
- portconfig 在父进程生成（contiguous_ports），以 json 进各子进程的 GameConfig。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import structlog

from vibecraft.server.game_process import GameConfig, GameProcess
from vibecraft.server.room import Room, Slot

logger = structlog.get_logger(__name__)

# 主屏可用宽度（用户屏 3440；窗口高 0 = 自动 workarea）
_SCREEN_W = 3440


@dataclass
class PlayerPlan:
    player_id: str
    config: GameConfig


class MatchOrchestrator:
    def __init__(self, game_process_factory: Callable[[], Any] = GameProcess) -> None:
        self._factory = game_process_factory
        self._procs: dict[str, Any] = {}
        self._log = logger.bind(component="match_orchestrator")

    # ---- 计划生成（纯函数，单测主战场）----
    def build_plan(self, room: Room) -> list[PlayerPlan]:
        bots: list[Slot] = room.bot_slots()
        computers = [
            {"race": s.race, "difficulty": s.difficulty}
            for s in room.slots
            if s.kind == "computer"
        ]
        match_id = room.match_id or f"match_{time.strftime('%Y%m%d_%H%M%S')}"

        if len(bots) == 1:
            # 单人路径：原 run_multiple_games（电脑作 opponent）。多电脑时取第一个，
            # 其余忽略并 log（单人 vs 多电脑 = 未来增强，今天的单人体验保持原样）。
            comp = computers[0] if computers else {"race": "Random", "difficulty": "VeryHard"}
            if len(computers) > 1:
                self._log.warning("solo_path_extra_computers_ignored", count=len(computers) - 1)
            cfg = GameConfig(
                map_name=room.map_name,
                my_race=bots[0].race,
                opponent_race=comp["race"],
                opponent_difficulty=comp["difficulty"],
                game_id=f"{match_id}_p0",
                focus_window=True,
            )
            return [PlayerPlan(bots[0].player_id, cfg)]

        # 多人路径：共享 portconfig + 窗口平铺
        from sc2.portconfig import Portconfig

        pc_json = Portconfig.contiguous_ports(guests=len(bots) - 1).as_json
        win_w = max(800, _SCREEN_W // len(bots))
        plans: list[PlayerPlan] = []
        for i, s in enumerate(bots):
            cfg = GameConfig(
                map_name=room.map_name,
                my_race=s.race,
                realtime=True,
                game_id=f"{match_id}_p{s.index}",
                mp_role="host" if i == 0 else "join",
                mp_portconfig_json=pc_json,
                mp_player_name=s.name or f"player{s.index}",
                mp_guest_names=[g.name for g in bots[1:]] if i == 0 else [],
                mp_computers=computers if i == 0 else [],
                window_x=i * win_w,
                window_y=0,
                window_width=win_w,
                window_height=0,
                focus_window=(i == 0),  # 只有 host 抢一次焦点（声音）
            )
            plans.append(PlayerPlan(s.player_id, cfg))
        return plans

    # ---- 生命周期 ----
    def start_match(self, room: Room) -> dict[str, Any]:
        plans = self.build_plan(room)
        self._procs = {}
        for p in plans:
            gp = self._factory()
            gp.start(p.config)
            self._procs[p.player_id] = gp
            self._log.info("match_player_started", player_id=p.player_id,
                           role=p.config.mp_role or "solo", game_id=p.config.game_id)
        return dict(self._procs)

    def process_for(self, player_id: str) -> Any | None:
        return self._procs.get(player_id)

    @property
    def processes(self) -> dict[str, Any]:
        return dict(self._procs)

    async def stop_match(self) -> None:
        for pid, gp in list(self._procs.items()):
            try:
                await gp.stop()
            except Exception as exc:  # noqa: BLE001
                self._log.warning("match_player_stop_failed", player_id=pid, error=str(exc))
        self._procs = {}
```

**Step 4: 跑测确认通过**

**Step 5: Commit**（feat(multiplayer): MatchOrchestrator 编排）

---

## Task 6: WS 协议——玩家身份握手 + lobby 帧 + 按 player_id 路由

**Files:**
- Modify: `src/vibecraft/server/ws.py`（握手 ~line 799-838；`WsConnection.__init__` / `_dispatch` / `_handle_command` 等所有 `self._game_process` 使用点）
- Create: `src/vibecraft/server/room_service.py`
- Test: `tests/unit/test_room_service.py`、`tests/unit/test_ws_multiplayer.py`

**核心结构：** 新建 `RoomService`（server 级单例）聚合 Room + MatchOrchestrator + legacy 单人 GameProcess，WsConnection 不再直接持有 GameProcess，改持 `room_service + player_id`，所有 `self._game_process` 改为 `self._gp()`：

```python
# room_service.py
"""RoomService：Room + MatchOrchestrator + legacy 单人 GameProcess 的聚合根。

WsConnection 通过它解析"我的 GameProcess"：
- 多人局开了 → orchestrator.process_for(player_id)
- 否则 → legacy 单人共享 GameProcess（现有 start_game 流程零变化）
这是设计文档「PlayerChannel 抽象」的落点——阶段 1 换传输层时本类不动。
"""
from __future__ import annotations
from typing import Any
import structlog
from vibecraft.server.game_process import GameProcess
from vibecraft.server.match import MatchOrchestrator
from vibecraft.server.room import Room, RoomError

logger = structlog.get_logger(__name__)


class RoomService:
    def __init__(self, legacy_game_process: GameProcess | None = None,
                 orchestrator: MatchOrchestrator | None = None,
                 map_name: str = "DaybreakLE", max_slots: int = 4) -> None:
        self.room = Room(map_name=map_name, max_slots=max_slots)
        self.orchestrator = orchestrator or MatchOrchestrator()
        self.legacy_gp = legacy_game_process or GameProcess()
        self._log = logger.bind(component="room_service")

    def game_process_for(self, player_id: str) -> Any:
        gp = self.orchestrator.process_for(player_id)
        return gp if gp is not None else self.legacy_gp

    def start_match(self, requester: str) -> dict[str, Any]:
        self.room.start(requester)                     # 校验 + 状态 → starting
        procs = self.orchestrator.start_match(self.room)
        return procs

    async def stop_match(self) -> None:
        await self.orchestrator.stop_match()
        self.room.mark_ended()
```

**WS 协议增量：**

1. **握手**：URL 加 `&player=<昵称>&pid=<设备id>`。缺省 fallback `pid="default"`、`player="玩家"`（旧 PWA / 单人流程兼容）。握手成功后：`room_service.room.join(pid, player)` + `registry.attach(conn, player_id=pid)` + 立即下发 `room.to_frame()` + 广播给其他人。
2. **上行新帧**（`_dispatch` 加分支，每个 handler 都是「调 Room 方法 → 成功广播 room_state / RoomError 回 `room_error` 帧」的模板）：
   - `lobby_set_race {race}` / `lobby_set_team {team}` / `lobby_ready {ready}`
   - `lobby_add_computer {race, difficulty}` / `lobby_remove_slot {index}`（房主）
   - `lobby_start {}`（房主）→ `room_service.start_match` → 各连接启动自己的 status pump
   - `lobby_leave {}`
3. **路由**：`_handle_command` / `_handle_view_move` / tactical/macro/strategy/webrtc/end_game 等所有 `self._game_process` 替换为 `self._gp()`（= `self._room_service.game_process_for(self._player_id)`）。
4. **status pump**：`lobby_start` 成功后**每条连接**对自己的 GameProcess 启 pump（host 连接那条额外负责把 `playing` 状态翻译成 `room.mark_in_game()` + 广播；任一 GameProcess `ended/crashed` → `room_service.stop_match()` 收场 + 广播回 lobby）。
5. **断线**：lobby 态断线 → `room.leave(pid)` + 广播；in_game 断线 → slot 保留（手机重连续命，对局不中断）。

**单测**（fake GameProcess / fake ws，关键用例）：

```python
async def test_command_routes_to_own_process_only():
    """玩家 A 的指令绝不能进玩家 B 的 down_q —— 多人路由的根本不变量。"""
    # 两个 fake gp 注入 orchestrator._procs；两条 WsConnection 各绑 pid
    # connA._handle_command({"text": "全军进攻"})
    # assert gpA.sent_commands == [...] and gpB.sent_commands == []

async def test_lobby_start_broadcasts_starting_state(): ...
async def test_lobby_frame_from_non_host_rejected_with_room_error(): ...
async def test_legacy_solo_start_game_still_works():
    """不碰 lobby 帧、直接 start_game（旧 PWA 流程）→ legacy_gp.start 被调。"""
```

**TDD 循环同前**（失败 → 实现 → 通过 → 全量 unit → commit）。

**Commit**：feat(multiplayer): WS 玩家身份 + lobby 帧 + per-player 路由

---

## Task 7: BotService 装配 + CLI

**Files:**
- Modify: `src/vibecraft/server/service.py`（注入 RoomService，~line 87-101 / make_ws_handler 调用 ~line 187）
- Modify: `src/vibecraft/server/ws.py`（make_ws_handler 签名）
- Test: `tests/unit/test_service.py`（已有，扩展）

BotService 持有 `RoomService`（legacy_gp 复用现有 `self._game_process`），传给 `make_ws_handler(room_service=...)`。`ServiceConfig` 加 `max_players: int = 4`。单测：BotService 构造后 `room_service.legacy_gp is service.game_process`；ws_handler 能拿到 room_service。

**Commit**：feat(multiplayer): BotService 装配 RoomService

---

## Task 8: PWA 入口页（用户名 + 服务器列表）

**Files:**
- Create: `web/src/composables/useProfile.ts`
- Create: `web/src/components/EntryView.vue`
- Modify: `web/src/composables/useWs.ts`（buildWsUrl/getRoomToken，~line 49-57）
- Modify: `web/src/App.vue`（顶层 gate）
- Test: `web/src/__tests__/useProfile.test.ts`

**useProfile.ts**（完整实现）：

```typescript
// 玩家本地档案：用户名 + 服务器列表（localStorage 持久化，无账号系统）。
// 设计 docs/plans/2026-06-12-multiplayer-design.md §3.1。
import { ref } from 'vue'

export interface ServerEntry {
  name: string      // 显示名（"我家 PC"）
  url: string       // http(s)://host:port origin
  token: string     // 房间码
}

const LS_KEY = 'vibecraft_profile_v1'

interface Profile {
  username: string
  deviceId: string          // 首次生成，同名玩家靠它区分
  servers: ServerEntry[]
  selectedIndex: number
}

function load(): Profile {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw) as Profile
  } catch { /* 损坏则重置 */ }
  return {
    username: '',
    deviceId: crypto.randomUUID().slice(0, 8),
    servers: [],
    selectedIndex: -1,
  }
}

const profile = ref<Profile>(load())

function persist(): void {
  localStorage.setItem(LS_KEY, JSON.stringify(profile.value))
}

export function useProfile() {
  function setUsername(name: string): void {
    profile.value.username = name.trim()
    persist()
  }
  function addServer(entry: ServerEntry): void {
    // 同 url+token 去重：已存在则选中它
    const i = profile.value.servers.findIndex(
      (s) => s.url === entry.url && s.token === entry.token,
    )
    if (i >= 0) { profile.value.selectedIndex = i } else {
      profile.value.servers.push(entry)
      profile.value.selectedIndex = profile.value.servers.length - 1
    }
    persist()
  }
  function removeServer(index: number): void {
    profile.value.servers.splice(index, 1)
    if (profile.value.selectedIndex >= profile.value.servers.length) {
      profile.value.selectedIndex = profile.value.servers.length - 1
    }
    persist()
  }
  function selectServer(index: number): void {
    profile.value.selectedIndex = index
    persist()
  }
  function selectedServer(): ServerEntry | null {
    return profile.value.servers[profile.value.selectedIndex] ?? null
  }
  /** 扫码/带 ?room= 打开 → 把当前 origin 自动注册成一条服务器并选中。 */
  function adoptUrlRoom(): void {
    const token = new URLSearchParams(location.search).get('room')
    if (!token) return
    addServer({ name: location.host, url: location.origin, token })
  }
  /** 入口页是否已可跳过（有用户名 + 有选中的服务器）。 */
  function isComplete(): boolean {
    return !!profile.value.username && !!selectedServer()
  }
  return {
    profile, setUsername, addServer, removeServer, selectServer,
    selectedServer, adoptUrlRoom, isComplete,
  }
}
```

**useWs.ts 改动**：`buildWsUrl` 改为从 useProfile 的 selectedServer 取目标（`url` 转 ws(s) + `?room=token&player=<username>&pid=<deviceId>`）；保留旧 `?room=` 兜底（adoptUrlRoom 在 App 挂载时先跑）。

**EntryView.vue**：用户名输入 + 服务器卡片列表（点选/删除）+ 「添加服务器」表单（名称/地址/房间码）+ 大按钮 [连接]。样式贴现有 Tailwind 风格（深色、紧凑）。

**App.vue gate**：`isComplete() === false → 渲染 EntryView`；点 [连接] 后才挂 useWs 开始连接。

**测试**：`useProfile.test.ts`（localStorage mock：add/dedupe/select/adoptUrlRoom/isComplete 5 条用例）。跑 `cd web; npm run test`。

**构建提醒**：改完必须 **PowerShell** 跑 `cd web; npm run build`（Bash 会中途杀 vite → assets 清空白屏）。

**Commit**：feat(pwa): 入口页——用户名 + 多服务器列表

---

## Task 9: PWA lobby 视图

**Files:**
- Create: `web/src/components/RoomLobby.vue`
- Modify: `web/src/composables/useWs.ts`（处理 `room_state` / `room_error` 下行帧 + lobby 上行帧 send 方法）
- Modify: `web/src/App.vue`（state gate：entry → lobby → cockpit）
- Test: `web/src/components/__tests__/RoomLobby.test.ts`

**useWs 增量**：`roomState = ref<RoomStateFrame | null>(null)`；收 `room_state` 更新；`sendLobby(frame)` 透传。`room_error` → 复用现有 toast 通道。

**RoomLobby.vue 功能清单**（贴 SC2 经典 lobby）：
- slot 表：每行 = 状态色点 + 名字 + 队伍下拉(1/2/FFA) + 种族下拉(P/T/Z/随机) + ready 标
- 自己那行可改队伍/种族 + [准备] 切换;房主额外:[+电脑](种族/难度选择)、slot 行 [×] 移除、[开始对局]（全员 ready 才亮）
- state=starting → 全屏进度（"正在拉起 N 个 SC2 实例…"）；state=in_game → App 切 cockpit
- 局终（room_state 回 lobby）→ 自动回 lobby 页

**App.vue gate 链**：`!isComplete() → EntryView`；`roomState===null || state==='lobby'|'starting' → RoomLobby`；`state==='in_game' → 现有 CockpitView`。**兼容**：旧单人流程 = lobby 里只有自己 + 加电脑 + 开始（替代原「开始游戏」按钮入口，原 start_game 帧路径由 lobby_start 单人分支覆盖——PWA 不再发裸 start_game，但 server 保留该帧处理向后兼容）。

**测试**：RoomLobby 渲染 slots / 非房主看不到房主按钮 / ready 状态切换 emit 正确帧（3-4 条，参照现有组件测试风格）。

**Commit**：feat(pwa): 房间 lobby 视图（slot/分队/电脑/开始）

---

## Task 10: 视频按窗口分流（PID-scoped 抓屏）

**Files:**
- Modify: `src/vibecraft/server/game_process.py`（子进程推 `{"kind":"sc2_pid","pid":N}` 上行；GameProcess 记录 `sc2_pid` 属性）
- Modify: `src/vibecraft/server/webrtc.py`（`_find_sc2_window` 加 `pid_filter` 参数；`SC2ScreenCapture(pid_filter=...)`；`WebRtcManager.handle_offer(..., sc2_pid=None)` 每 offer 一个独立 capture）
- Modify: `src/vibecraft/server/ws.py`（`_handle_webrtc_offer` 把 `self._gp().sc2_pid` 传给 handle_offer）
- Test: `tests/unit/test_webrtc_pid_filter.py`

**子进程找自己的 SC2 PID**：`_child_entry` 多人/单人分支起一个 daemon 线程，轮询 `psutil.Process(os.getpid()).children(recursive=True)` 找 `SC2_x64.exe`，找到即 `up_q.put({"kind":"sc2_pid","pid":...})` 后退出线程。`GameProcess.raw_events` 看到该 kind 记录到 `self.sc2_pid`（不下发手机）。

**webrtc.py**：`_find_sc2_window(pid_filter: int | None = None)` —— 枚举到的窗口先按现有 title hints 过滤，再 `pid_filter` 不为 None 时只留 `_window_pid(hwnd) == pid_filter`（工具函数已存在 ~line 144）。`WebRtcManager` 从「单 capture 单例」改为「per-offer capture」：`handle_offer(sdp, type, sc2_pid=None)` 为本 PeerConnection 创建独立 `SC2ScreenCapture(pid_filter=sc2_pid)`。**单人路径 sc2_pid=None → 行为与现在完全一致**。

**音频限制（v0 接受）**：系统回环采集是全局混音，多实例时各玩家听到的是混音。缓解：只有 host 实例抢焦点出声。per-process loopback（WASAPI）记 TASKS.md 待办，不在本 task 做。

**单测**：fake 窗口枚举（monkeypatch pygetwindow + `_window_pid`）验证 pid_filter 命中/不命中/None 透传 3 条。

**Commit**：feat(multiplayer): 视频按 SC2 窗口 PID 分流

---

## Task 11: multiplayer_selftest（端到端路由自验）+ 文档刷新

**Files:**
- Create: `scripts/multiplayer_selftest.py`
- Modify: `TASKS.md`（当前状态 + 待办）、`ARCHITECTURE.md`（多人节）、`docs/plans/2026-06-12-multiplayer-design.md`（如 spike 推翻了什么，回写）

**selftest 流程**（mock LLM → non-realtime，可并行，参考 `stealth_saturation_selftest.py` 骨架）：
1. 起 BotService（测试端口 18080，固定 token）+ `VIBECRAFT_MOCK_LLM_JSON` 注入（防守/进攻两条 match 规则）。
2. 两个 `websockets` 客户端连 `?room=...&player=alice&pid=a1` / `bob&pid=b1`，走完整 lobby 流程（set_race / ready / lobby_start）。
3. 等两边 `game_status sc2=playing`。
4. alice 发 `command "全军防守"`；断言 **alice 收到 command_echo、bob 没收到**；grep 两个 game 日志目录确认 directive 只落在 alice 的 `logs/<match>_p0/`。
5. 跑 5 game-min 后 `end_game`，断言 room_state 回 lobby、两子进程都清干净（无 SC2_x64 孤儿）。
6. PASS/FAIL 退出码。

**判读铁律**：grep 串对齐真实日志格式（先跑一遍看格式再写断言）。

**文档**：
- `TASKS.md`「当前状态」：多人阶段 0 完成项 + 已知限制（音频混流 / 组队同盟按 spike 结论 / 3+ 人未实测）+ 阶段 1（VPS 会合服务）为下一步。
- `ARCHITECTURE.md` 新增「多人联网（阶段 0）」节：RoomService/MatchOrchestrator/路由不变量（A 的指令绝不进 B 的 down_q）/ host-join 端口共享图。
- `USER_GUIDE.md`：入口页 + lobby 的玩家操作说明。

**Commit**：feat(multiplayer): 端到端自验脚本 + 三文档刷新

---

## 验收总清单（全部跑完才算阶段 0 完成）

| # | 验收 | 命令 |
|---|---|---|
| 1 | 全量单测绿 | `uv run --no-sync pytest tests/unit -q` |
| 2 | lint/type | `uv run --no-sync ruff check . && uv run --no-sync mypy src/vibecraft` |
| 3 | spike 三模式 | `multiplayer_smoke.py`（基本 / kill / with-computer） |
| 4 | 端到端路由 | `multiplayer_selftest.py` PASS |
| 5 | PWA 构建 | PowerShell `cd web; npm run build` + `npm run test` |
| 6 | 真人验收 | 用户 + 本地朋友两台手机同 WiFi 实测（唯一需要喊用户的步骤） |

## Spike 结论（2026-06-12 回填，E1-E7 + 观察模式实测）

- [x] **跨进程 host/join 成立**（E4/E5/E7 多次 PASS，双方打满正常退出），但踩了两个大坑：
  1. **`Portconfig.contiguous_ports` 必败**：连号端口只做"空闲检查"不推进 Windows
     顺序分配的临时端口游标 → 子进程里 SC2 自己的 ws 端口被 OS 顺序分配到正好压在
     游戏 P2P 端口上 → join 被引擎拒 `NetworkError(12) 'Failed to join game:
     537001988'`。**修复 = 散点 `Portconfig()`**（`new_portconfig_json` helper）。
  2. **python-sc2 吞 join 错误**：`client.join_game` 不检查响应 error 字段，失败
     静默返回 player_id=0，后续才炸 "A game has not been started yet"。runner 已补
     检查+重试+显式 raise（`_checked_join_and_play`）。
- [x] **join 时序无要求**：E7 无栅栏 PASS、join 晚 8s 起也 PASS（引擎会等）。
  `before_join` 会合钩子保留但非必需。调研佐证：Blizzard protocol.md 双客户端互同步
  语义、pysc2 并行 join、aiarena coordinator 转发模式。
- [x] **引擎硬限制：多 agent 局仅纯 1v1**——create_game 直接拒绝
  `InvalidPlayerSetup: Only 1v1 is supported when using multiple agents`。
  → **双真人局不能加内置 AI**（"2 人合作打电脑"在多实例路线上不可行）；
  → 分队/FFA 问题消失（双真人=纯 1v1）；
  → Room 已加两道校验（add_computer 拦 + start 拦），lobby UI 相应置灰。
  → 单真人 + 电脑 = 原单人路径，不受影响。
- [x] **一方 kill 后另一方结局**：局中 kill host（@35s wall，3600 game-s 局）→
  **join 方引擎立刻判 `Result.Victory`**（对面掉线=存活方胜，语义干净）。monitor
  的"任一方崩 → 全停收场"有引擎层背书。注意 non-realtime 下 600 game-s 约 60s wall
  就打完，kill 测试要把 kill 点放局中（早期两次"PASS"实为局已结束的空跑）。
- [x] 双实例 non-realtime 跑满全程稳定；realtime 双实例启动正常（E1，帧率长测待
  真局 e2e 阶段观察）。
- [x] v0 实测范围 = **2 真人纯 1v1** + 单真人 vs 电脑。3+ 真人未实测（Room/
  orchestrator 双重拦截）。
