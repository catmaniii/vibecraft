"""凤凰安全集结点/矿后 pathing 语义探针（2026-07-26，图谱 D80/F114）。

回答独立评审 F1/F19 的地基问题(不验就别写地形层)：
  ① in_pathing_grid 在**矿脉格**上返回啥? → 矿脉挡不挡地面寻路?
  ② 矿脉背后(矿后点)地面**可不可达**(从敌基做地面 BFS)? → 决定 _ground_unreachable 判定基
  ③ 悬崖外低地点 vs 矿后点的 pathing/height 各是啥?
  ④ 平坦矿(自然分基)的"矿后"靠矿脉挡还是悬崖挡?

结论直接决定 _ground_unreachable 判定基:能靠悬崖(terrain_height,稳)就靠悬崖;
必须靠矿脉挡的口袋要显式纳入 mineral_field + 标"随矿量退化"。

跑法(non-realtime fast, vs VeryEasy, ~1-2min)：
  .venv/Scripts/python.exe scripts/phoenix_terrain_probe.py [--map DaybreakLE]
"""

from __future__ import annotations

import argparse
from collections import deque

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

MAP_NAME = "DaybreakLE"


def _pg(ai: BotAI, p: Point2) -> int:
    """in_pathing_grid 原始:1=可走 0=不可走(用 game_info.pathing_grid 直接读，绕过 rounded 断言)。"""
    try:
        gi = ai.game_info
        x, y = round(p.x), round(p.y)
        return int(gi.pathing_grid[(x, y)])
    except Exception:
        return -1


def _reachable_from(ai: BotAI, start: Point2, targets: list[Point2], max_r: int = 40) -> dict:
    """从 start 对 pathing_grid(==1 可走)做 BFS(限 max_r),返回每个 target 是否地面可达。"""
    gi = ai.game_info
    sx, sy = round(start.x), round(start.y)
    seen = {(sx, sy)}
    dq = deque([(sx, sy)])
    tgt_cells = {(round(t.x), round(t.y)): t for t in targets}
    reached: dict = {}
    while dq:
        x, y = dq.popleft()
        if (x, y) in tgt_cells:
            reached[tgt_cells[(x, y)]] = True
        if abs(x - sx) > max_r or abs(y - sy) > max_r:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen:
                continue
            try:
                if int(gi.pathing_grid[(nx, ny)]) == 1:
                    seen.add((nx, ny))
                    dq.append((nx, ny))
            except Exception:
                pass
    return {t: (t in reached) for t in targets}


class PhoenixTerrainProbe(BotAI):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self._done = False

    async def on_step(self, iteration: int) -> None:
        if self._done or iteration < 5:
            return
        self._done = True
        lines: list[str] = ["\n===== PHOENIX TERRAIN PROBE ====="]

        # 探**我方自己**基地(矿脉可见、可靠;敌方矿脉在迷雾里采不到样)。矿脉挡地面的机制
        # 每个基地一样,结论通用到敌方矿后。用 start_location(可靠),expansion 有就加自然。
        home = self.start_location
        try:
            n_mf = self.mineral_field.amount
        except Exception:
            n_mf = -1
        try:
            n_exp = len(self.expansion_locations_list)
        except Exception:
            n_exp = -1
        lines.append(
            f"[诊断] mineral_field.amount={n_mf} expansion_locations={n_exp} home=({home.x:.0f},{home.y:.0f})"
        )
        mines = [home]
        try:
            exps = sorted(self.expansion_locations_list, key=lambda p: p.distance_to(home))
            for e in exps:
                if e.distance_to(home) > 5:
                    mines.append(e)
                    break
        except Exception:
            pass

        for idx, th in enumerate(mines):
            tag = "我方主矿" if idx == 0 else "我方二矿(自然)"
            mfs = self.mineral_field.closer_than(10.0, th)
            if not mfs:
                lines.append(f"\n[{tag} @({th.x:.0f},{th.y:.0f})] 附近无矿脉(未侦察?),跳过")
                continue
            mcenter = mfs.center
            base_h = self.get_terrain_height(th)
            # 矿后方向 = 矿线中心远离基地
            back_dir = mcenter - th
            n = (back_dir.x**2 + back_dir.y**2) ** 0.5 or 1.0
            ux, uy = back_dir.x / n, back_dir.y / n
            behind4 = Point2((mcenter.x + ux * 4, mcenter.y + uy * 4))
            behind8 = Point2((mcenter.x + ux * 8, mcenter.y + uy * 8))
            behind12 = Point2((mcenter.x + ux * 12, mcenter.y + uy * 12))

            lines.append(f"\n[{tag} @({th.x:.0f},{th.y:.0f}) 基地高度={base_h:.1f}]")
            lines.append(f"  基地位:        pg={_pg(self, th)} h={self.get_terrain_height(th):.1f}")
            # 矿脉格(取 3 个矿的格)
            for mf in list(mfs)[:3]:
                p = mf.position
                lines.append(
                    f"  矿脉格({p.x:.0f},{p.y:.0f}): pg={_pg(self, p)} h={self.get_terrain_height(p):.1f}  ← 矿脉挡不挡pathing看这"
                )
            lines.append(
                f"  矿线中心:      pg={_pg(self, mcenter)} h={self.get_terrain_height(mcenter):.1f}"
            )
            lines.append(
                f"  矿后+4:        pg={_pg(self, behind4)} h={self.get_terrain_height(behind4):.1f}"
            )
            lines.append(
                f"  矿后+8:        pg={_pg(self, behind8)} h={self.get_terrain_height(behind8):.1f}"
            )
            lines.append(
                f"  矿后+12:       pg={_pg(self, behind12)} h={self.get_terrain_height(behind12):.1f}"
            )

            # 从基地做地面 BFS,看矿后点可不可达(F2:地面够不到才是安全口袋)
            reach = _reachable_from(self, th, [mcenter, behind4, behind8, behind12], max_r=40)
            lines.append(
                f"  地面BFS可达: 矿线中心={reach[mcenter]} 矿后+4={reach[behind4]} "
                f"矿后+8={reach[behind8]} 矿后+12={reach[behind12]}  ← False=地面够不到=安全口袋候选"
            )

        lines.append("\n===== PROBE END =====\n")
        print("\n".join(lines))
        await self.client.leave()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=MAP_NAME)
    args = ap.parse_args()
    run_game(
        maps.get(args.map),
        [Bot(Race.Protoss, PhoenixTerrainProbe()), Computer(Race.Terran, Difficulty.VeryEasy)],
        realtime=False,
    )


if __name__ == "__main__":
    main()
