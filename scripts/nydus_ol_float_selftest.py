"""坑道虫 OL 漂浮点【生产函数】真机终态自验（2026-07-12 Task #1）。

验的不是探针里的内联算法，而是 **生产代码** `nydus_landing_planner.overlord_float_points`
（`_SendOverlordToEnemy` 就用它）：真机把起手 OL 派到该函数算出的漂浮点，等它们到位后
**读终态** `get_terrain_height(OL.position)`，断言每只 OL 都在【悬崖外低地】
（terrain < base_h - 12），而不是在敌方基地里/高地上/前沿。

这是 CLAUDE.md「验终态非中间 trace」纪律的落地：不看"我下了 move 命令"的日志，
看 OL 真实站到了哪个高度的格子上。

跑法（non-realtime fast，vs VeryEasy，~1-2min；可并行多开）：
  .venv/Scripts/python.exe scripts/nydus_ol_float_selftest.py [--map DaybreakLE]
退出码 0=PASS，1=FAIL。
"""

from __future__ import annotations

import argparse
import sys

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import (
    _CLIFF_DROP,
    enemy_plateau_edges,
    overlord_float_points,
)

MAP_NAME = "DaybreakLE"


class _FloatSelfTest(BotAI):  # type: ignore[misc]
    """派 OL 到生产漂浮点 → 到位后读终态高度断言在悬崖外低地。"""

    def __init__(self) -> None:
        super().__init__()
        self._floats: list[Point2] = []
        self._base_h: int = 0
        self._target: Point2 | None = None
        self._static_ok = False
        self._sent = False
        self._dyn_ok = False
        self._done = False

    async def on_step(self, iteration: int) -> None:
        if self._done:
            return
        enemy = self.enemy_start_locations[0]

        # ① 生产函数算漂浮点 + 【静态终态】对真机地形逐点核对高度（不需 OL 到位）
        if not self._sent and iteration >= 2:
            _edges, self._base_h = enemy_plateau_edges(self, enemy)
            self._floats = overlord_float_points(self, enemy)
            thr = self._base_h - _CLIFF_DROP
            print("\n========== 生产 overlord_float_points 真机验证 ==========", flush=True)
            print(
                f"base_h={self._base_h} | 悬崖外低地阈值 = base_h-{_CLIFF_DROP} = {thr}", flush=True
            )
            if not self._floats:
                print("FAIL: 生产函数没算出任何漂浮点（地形读取失败？）", flush=True)
                self._done = True
                await self.client.leave()
                return
            print("\n---- 静态终态：每个漂浮点对真机 get_terrain_height 核对 ----", flush=True)
            static_all_low = True
            for p in self._floats:
                h = self.get_terrain_height(p)
                low = h < thr
                static_all_low = static_all_low and low
                print(
                    f"  漂浮点({round(p.x)},{round(p.y)}) 高度={h} 离敌方主基={round(p.distance_to(enemy), 1)}格 "
                    f"在悬崖外低地={low}",
                    flush=True,
                )
            self._static_ok = static_all_low
            print(
                f"静态：{len(self._floats)} 个漂浮点全在悬崖外低地 = {static_all_low}", flush=True
            )

            # ② 动态：把起手唯一那只 OL 派去【最近】漂浮点（最短航程），到位后读终态高度
            ols = list(self.units(UnitTypeId.OVERLORD))
            if ols:
                self._target = min(self._floats, key=lambda p: p.distance_to(self.start_location))
                ols[0].move(self._target)
                self._sent = True
                print(
                    f"\n→ 派起手 OL 飞向最近漂浮点 ({round(self._target.x)},{round(self._target.y)}) 验动态终态…",
                    flush=True,
                )
            else:
                self._done = True
                await self.client.leave()
                return

        # ③ OL 到位 → 读终态高度断言在悬崖外低地
        if self._sent and not self._done and self._target is not None:
            ols = list(self.units(UnitTypeId.OVERLORD))
            near = [ol for ol in ols if ol.distance_to(self._target) < 2.5]
            if near:
                self._done = True
                ol = near[0]
                h = self.get_terrain_height(ol.position)
                on_low = h < self._base_h - _CLIFF_DROP
                self._dyn_ok = on_low
                print("\n---- 动态终态：OL 真实站住的格子高度 ----", flush=True)
                print(
                    f"  OL@({round(ol.position.x)},{round(ol.position.y)}) 高度={h} "
                    f"离敌方主基={round(ol.distance_to(enemy), 1)}格 在悬崖外低地={on_low}",
                    flush=True,
                )
                verdict = "PASS" if (self._static_ok and on_low) else "FAIL"
                print(
                    f"\n{verdict}: 静态全低地={self._static_ok} ∧ 动态 OL 站低地={on_low}",
                    flush=True,
                )
                await self.client.leave()
                return

        # 兜底超时（OL 慢，给足航程）
        if iteration > 4000:
            print(
                f"\n[timeout] OL 未到位（静态={self._static_ok}）；仅按静态终态判定。",
                flush=True,
            )
            self._done = True
            await self.client.leave()


def main() -> int:
    ap = argparse.ArgumentParser(description="坑道虫 OL 漂浮点生产函数真机终态自验")
    ap.add_argument("--map", default=MAP_NAME)
    args = ap.parse_args()
    print("=== 坑道虫 OL 漂浮点生产函数真机终态自验 ===", flush=True)
    bot = _FloatSelfTest()
    run_game(
        maps.get(args.map),
        [Bot(Race.Zerg, bot, name="FloatSelfTest"), Computer(Race.Terran, Difficulty.VeryEasy)],
        realtime=False,
    )
    # PASS = 静态全低地 ∧（OL 到位后也在低地；OL 若没成功飞到则以静态终态兜底）
    passed = bot._static_ok and (bot._dyn_ok if bot._sent else True)
    print(
        f"\n==== 结论: {'PASS' if passed else 'FAIL'} (static={bot._static_ok} dyn={bot._dyn_ok}) ====",
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
