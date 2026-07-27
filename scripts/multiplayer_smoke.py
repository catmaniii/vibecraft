"""multiplayer_smoke.py — 跨进程 host/join 链路 spike（阶段 0 多人联网闸门）。

两个独立子进程各起一个 SC2 实例，经共享 Portconfig host/join 成一局多人局，
bot 用最小 BotAI stub（隔离链路问题，不拉 vibecraft 全栈）。

用法（.venv 里跑）：
  # 基本验证：2 bot 互打，non-realtime 跑 300 game-s，窗口并排
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py

  # 实时模式肉眼看（顺带观察双实例帧率）
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --realtime

  # 崩溃行为：60s 后 kill host 方，观察 join 结局（预期 Victory）
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --kill-host-after 60

  # join 方比 host 晚 5s 起，验 SC2 join 等待容忍度（S1 非对称启动）
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --join-delay 5

  # 敌我关系观察：host 建 3 人局（2 bot + 1 VeryEasy 电脑），看是否 FFA
  .venv/Scripts/python.exe scripts/multiplayer_smoke.py --with-computer

判读：
  两个子进程都打出 "RESULT role=... result=..." 且无 traceback = PASS。
  --kill-host-after 模式：join 打出 Victory = host 崩溃后 join 胜出。
  --with-computer 模式：观察电脑是否同时攻击两个 bot（FFA 证据）。

⚠️ 2026-06-12 spike 排坑实录（端口！端口！端口！）：
  跨进程 host/join 最初必败（join 被引擎拒 NetworkError(12)
  'Failed to join game: 537001988'，且 python-sc2 吞错 → 表现为
  "A game has not been started yet"）。二分实验（E1-E7）单变量锁定：
  **Portconfig.contiguous_ports 连号端口被 Windows 顺序分配的临时端口游标撞上**
  （子进程 SC2 自己的 ws 端口压在游戏 P2P 端口上）。修复 = 散点 Portconfig()。
  栅栏/时序/窗口参数/realtime 全部无罪（join 相差几秒引擎会等，无需同步）。

Spike 结论：
  v0 实测仅验证 2 真人（2 bot slot）。3+ 真人 slot 的 Portconfig 已预留
  但未实测，MatchOrchestrator.build_plan 对 >2 bot slot 先 raise RoomError。
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing
import sys
import time
from typing import Any


def _make_smoke_bot_class(seconds: int) -> type:
    """构造极简 SmokeBot 类（_child 跨进程模式与 --single-process 对照组共用）。"""
    from sc2.bot_ai import BotAI  # type: ignore[import-untyped]
    from sc2.ids.unit_typeid import UnitTypeId  # type: ignore[import-untyped]

    class SmokeBot(BotAI):
        """攒兵后 attack 地图中心的最小 bot：证明真的在对打，不是各自挂机。

        - 补农民到 16
        - BE（Pylon / 水晶）防卡人口
        - BG（Gateway / 兵营）建 1 个
        - 出叉子（Zealot），攒 4 个后 attack 地图中心
        """

        async def on_step(self, iteration: int) -> None:  # type: ignore[override]
            # 超时则主动退出（防止 SC2 窗口永远挂着）
            if self.time > seconds:
                await self.client.leave()
                return

            # 补农民到 16（Protoss 探机，BotAI.supply_workers = 当前工人数）
            if self.supply_workers < 16 and self.can_afford(UnitTypeId.PROBE):
                for nexus in self.townhalls.idle:
                    nexus.train(UnitTypeId.PROBE)

            # BE（Pylon / 水晶）：剩余人口 < 4 时补房子
            if (
                self.supply_left < 4
                and not self.already_pending(UnitTypeId.PYLON)
                and self.can_afford(UnitTypeId.PYLON)
                and self.townhalls
            ):
                await self.build(UnitTypeId.PYLON, near=self.townhalls.random)

            # BG（Gateway / 兵营）：有水晶后建 1 个
            if (
                not self.structures(UnitTypeId.GATEWAY)
                and not self.already_pending(UnitTypeId.GATEWAY)
                and self.structures(UnitTypeId.PYLON)
                and self.can_afford(UnitTypeId.GATEWAY)
            ):
                await self.build(
                    UnitTypeId.GATEWAY,
                    near=self.structures(UnitTypeId.PYLON).first,
                )

            # 出叉子（Zealot）
            if self.can_afford(UnitTypeId.ZEALOT):
                for gw in self.structures(UnitTypeId.GATEWAY).idle:
                    gw.train(UnitTypeId.ZEALOT)

            # 攒 4 叉后全军 attack 地图中心（验证两 bot 真实交战）
            zealots = self.units(UnitTypeId.ZEALOT)
            if len(zealots) >= 4:
                for z in zealots.idle:
                    z.attack(self.game_info.map_center)

    return SmokeBot


def _child(
    role: str,
    pc_json: str,
    realtime: bool,
    with_computer: bool,
    seconds: int,
    join_barrier: Any = None,
    bare_sc2: bool = False,
) -> None:
    """子进程入口：起一个极简 SmokeBot，走 host/join 路径对打。

    join_barrier：跨进程 join 会合栅栏（multiprocessing.Barrier），可选。
    E7 实证端口修对后**不需要**栅栏（join 相差几秒引擎会等）；保留作诊断开关
    （--no-barrier 关）。
    """
    from sc2 import maps  # type: ignore[import-untyped]
    from sc2.data import Race  # type: ignore[import-untyped]
    from sc2.player import Bot  # type: ignore[import-untyped]
    from sc2.portconfig import Portconfig  # type: ignore[import-untyped]

    from vibecraft.server.sc2_multiplayer import build_host_players, host_game, join_game

    SmokeBot = _make_smoke_bot_class(seconds)

    # JOINDBG 探针（临时诊断）：python-sc2 的 client.join_game **不检查**响应里的
    # join_game.error 字段——join 失败时静默返回 player_id=0，后续 get_game_data 才炸
    # "A game has not been started yet"。这里 wrap Protocol._execute 把 join_game
    # 响应的 error/error_details 打出来，定位 join 为什么被拒。
    from sc2 import protocol  # type: ignore[import-untyped]

    _orig_execute = protocol.Protocol._execute

    async def _execute_dbg(self: object, **kwargs: object) -> object:
        resp = await _orig_execute(self, **kwargs)
        try:
            if resp.HasField("join_game"):
                err = resp.join_game.error
                details = getattr(resp.join_game, "error_details", "")
                print(
                    f"JOINDBG role={role} player_id={resp.join_game.player_id} "
                    f"error={err} details={details!r}",
                    flush=True,
                )
        except Exception:
            pass
        return resp

    protocol.Protocol._execute = _execute_dbg  # type: ignore[method-assign]

    portconfig = Portconfig.from_json(pc_json)
    sc2_map = maps.get("DaybreakLE")
    me = Bot(Race.Protoss, SmokeBot(), name=f"smoke_{role}")

    # join 会合：両边都到栅栏才同时发 join（180s 超时防一侧崩溃后另一侧挂死）
    _before_join = None
    if join_barrier is not None:

        def _before_join() -> None:
            print(f"[{role}] 到达 join 栅栏,等对方就位…", flush=True)
            join_barrier.wait(timeout=180)
            print(f"[{role}] 栅栏放行,发 join", flush=True)

    try:
        if role == "host":
            computers = [{"race": "Terran", "difficulty": "VeryEasy"}] if with_computer else []
            players = build_host_players(
                "Protoss",
                me.name,
                guest_names=["smoke_join"],
                computers=computers,
                my_ai=me.ai,
            )
            result = asyncio.run(
                host_game(
                    sc2_map,
                    players,
                    realtime,
                    portconfig,
                    resolution=None if bare_sc2 else (1280, 720),
                    placement=None if bare_sc2 else (0, 0),
                    before_join=_before_join,
                )
            )
        else:
            result = asyncio.run(
                join_game(
                    me,
                    realtime,
                    portconfig,
                    resolution=None if bare_sc2 else (1280, 720),
                    placement=None if bare_sc2 else (1300, 0),
                    before_join=_before_join,
                )
            )
        print(f"RESULT role={role} result={result}", flush=True)
    except Exception as exc:
        print(f"ERROR role={role} exc={exc}", flush=True)
        raise


def main() -> None:
    ap = argparse.ArgumentParser(description="多人联网 spike：两进程 host/join 对打")
    ap.add_argument(
        "--realtime",
        action="store_true",
        help="实时模式（1x 速度，肉眼可看）",
    )
    ap.add_argument(
        "--with-computer",
        action="store_true",
        help="host 建 3 人局（2 bot + 1 VeryEasy 电脑），验敌我关系",
    )
    ap.add_argument(
        "--kill-host-after",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="N 秒后 kill host 子进程，观察 join 结局（预期 Victory）",
    )
    ap.add_argument(
        "--kill-join-after",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="N 秒后 kill join 子进程，观察 host 结局（预期 Victory）",
    )
    ap.add_argument(
        "--join-delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="join 子进程晚 N 秒启动（验 SC2 join 等待容忍度，S1 非对称启动）",
    )
    ap.add_argument(
        "--seconds",
        type=int,
        default=300,
        help="bot 跑多少 game-s 后主动退出（默认 300）",
    )
    ap.add_argument(
        "--contiguous-ports",
        action="store_true",
        help="复现坑用：用 contiguous_ports 连号端口（必败——Windows 顺序分配的临时"
        "端口游标会把子进程 SC2 的 ws 端口压在游戏 P2P 端口上 → join NetworkError。"
        "2026-06-12 spike 二分实锤，生产一律散点 Portconfig()）",
    )
    ap.add_argument(
        "--host-delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="host 子进程晚 N 秒启动 —— 让 join 方先就位（验证时序反转假设：单进程"
        " run_game 里 join 请求先于/同时于 host 建局，跨进程后 host 跑得快导致顺序倒挂）",
    )
    ap.add_argument(
        "--single-process",
        action="store_true",
        help="对照组：同 bot 同图走单进程 run_game([Bot,Bot])（debug_draw_probe versus"
        " 同款，2026-06-04 在本机验证过能跑）。用来区分'跨进程才坏' vs '环境退化全坏'",
    )
    ap.add_argument(
        "--bare-sc2",
        action="store_true",
        help="不传 resolution/placement（单进程对照组没传这些；归零'窗口参数'变量）",
    )
    ap.add_argument(
        "--no-barrier",
        action="store_true",
        help="去掉 join 会合栅栏（测栅栏是否必要——端口修对后可能根本不需要同步）",
    )
    args = ap.parse_args()

    if args.single_process:
        # 对照组：单进程 run_game([Bot, Bot])，python-sc2 自己管两个 SC2 实例。
        # 能跑 = 环境没退化、问题出在跨进程形态；不能跑 = 环境/版本退化，全坏。
        from sc2 import maps  # type: ignore[import-untyped]
        from sc2.data import Race  # type: ignore[import-untyped]
        from sc2.main import run_game  # type: ignore[import-untyped]
        from sc2.player import Bot  # type: ignore[import-untyped]

        SmokeBot = _make_smoke_bot_class(args.seconds)
        print("[smoke] 对照组：单进程 run_game 双 bot", flush=True)
        result = run_game(
            maps.get("DaybreakLE"),
            [
                Bot(Race.Protoss, SmokeBot(), name="smoke_sp1"),
                Bot(Race.Protoss, SmokeBot(), name="smoke_sp2"),
            ],
            realtime=args.realtime,
        )
        print(f"SINGLE_PROCESS RESULT: {result}", flush=True)
        sys.exit(0)

    if args.contiguous_ports:
        # 复现坑路径（必败，见 --contiguous-ports help）
        from sc2.portconfig import Portconfig  # type: ignore[import-untyped]

        pc_json = Portconfig.contiguous_ports(guests=1).as_json
    else:
        from vibecraft.server.sc2_multiplayer import new_portconfig_json

        pc_json = new_portconfig_json(guests=1)
    print(f"[smoke] portconfig = {pc_json}", flush=True)

    ctx = multiprocessing.get_context("spawn")
    # join 会合栅栏：host 在 create_game 后、join 前在此等 guest（--no-barrier 关掉）。
    join_barrier = None if args.no_barrier else ctx.Barrier(2)
    host_proc = ctx.Process(
        target=_child,
        args=(
            "host",
            pc_json,
            args.realtime,
            args.with_computer,
            args.seconds,
            join_barrier,
            args.bare_sc2,
        ),
        name="mp_smoke_host",
    )
    join_proc = ctx.Process(
        target=_child,
        args=(
            "join",
            pc_json,
            args.realtime,
            args.with_computer,
            args.seconds,
            join_barrier,
            args.bare_sc2,
        ),
        name="mp_smoke_join",
    )

    def _cleanup_children() -> None:
        """杀残留：先趁子进程活着 scoped 清它们的 SC2 子孙，再 terminate 自己。

        没有这步，kill/异常路径会留下黑屏 SC2 僵尸窗口（2026-06-12 用户报）。
        """
        for proc in (host_proc, join_proc):
            if proc.pid is not None:
                try:
                    from vibecraft.bot.watchdog import kill_sc2_by_parent_pid

                    kill_sc2_by_parent_pid(proc.pid)
                except Exception:
                    pass
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)

    try:
        if args.host_delay > 0:
            # 时序反转验证：join 先就位，host 晚 N 秒建局
            join_proc.start()
            print(f"[smoke] host 子进程将在 {args.host_delay:.1f}s 后启动", flush=True)
            time.sleep(args.host_delay)
            host_proc.start()
        else:
            host_proc.start()
            # S1: --join-delay：join 子进程晚 N 秒启动
            if args.join_delay > 0:
                print(f"[smoke] join 子进程将在 {args.join_delay:.1f}s 后启动", flush=True)
                time.sleep(args.join_delay)
            join_proc.start()

        # S1: --kill-host-after：N 秒后 kill host，验证 join 的结局
        if args.kill_host_after > 0:
            time.sleep(args.kill_host_after)
            # 先清 host 的 SC2 子孙再 terminate（不然 SC2 成孤儿黑屏挂着）
            try:
                from vibecraft.bot.watchdog import kill_sc2_by_parent_pid

                kill_sc2_by_parent_pid(host_proc.pid)
            except Exception:
                pass
            host_proc.terminate()
            print(f"[smoke] KILLED host process after {args.kill_host_after:.1f}s", flush=True)

        # --kill-join-after（原版保留）：N 秒后 kill join，验证 host 的结局
        if args.kill_join_after > 0:
            deadline = time.time() + args.kill_join_after
            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(remaining)
            try:
                from vibecraft.bot.watchdog import kill_sc2_by_parent_pid

                kill_sc2_by_parent_pid(join_proc.pid)
            except Exception:
                pass
            join_proc.terminate()
            print(f"[smoke] KILLED join process after {args.kill_join_after:.1f}s", flush=True)

        host_proc.join(timeout=900)
        join_proc.join(timeout=900)
    finally:
        _cleanup_children()

    host_ok = host_proc.exitcode == 0
    join_ok = join_proc.exitcode == 0

    # kill 模式下被 kill 的那方 exitcode 非 0，不算 FAIL
    if args.kill_host_after > 0:
        overall_ok = join_ok  # 只看存活方
    elif args.kill_join_after > 0:
        overall_ok = host_ok  # 只看存活方
    else:
        overall_ok = host_ok and join_ok

    print(
        f"SMOKE {'PASS' if overall_ok else 'FAIL'} "
        f"host_exit={host_proc.exitcode} join_exit={join_proc.exitcode}",
        flush=True,
    )
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
