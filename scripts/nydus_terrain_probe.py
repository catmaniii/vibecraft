"""坑道虫落点能力探针（2026-07-12）——真机验证:我到底能不能读到敌方主基高地边缘、
算出可放带大小、判断坑道虫放不放得下。

回答用户三问(用真实数字,不空口):
  ① 敌方主基高地边缘有多大? → terrain_height 扫出同高度连片(高地) + 挨着低一截的边缘格
  ② 视野 ∩ 高地边缘 这块多大? → 上面叠 is_visible(派 OL 过去)
  ③ 够不够放坑道虫? → can_place(NYDUSCANAL, ...) 批量,任一 True 就够放
另附验证:can_place 需不需要视野(关系到它能否预测 BUILD_NYDUSWORM)。

跑法(non-realtime fast,vs VeryEasy 免得被打断,~1-2min):
  .venv/Scripts/python.exe scripts/nydus_terrain_probe.py [--map DaybreakLE]
"""

from __future__ import annotations

import argparse
import math

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

MAP_NAME = "DaybreakLE"

_SCAN_R = 18  # 敌方主基周围扫描半径(格)
_H_TOL = 6  # |高度-基准| <= 此 → 算同一高地
_CLIFF_DROP = 12  # 邻格高度比基准低这么多 → 该格是悬崖下方 → 本格是边缘
_EDGE_NBR = 2  # 边缘判定:查周围这么多格内有没有悬崖下方


class TerrainProbe(BotAI):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self._static_done = False
        self._near_done = False  # OL 贴敌方主基:确认可放落点 + 选最外围
        self._ol_sent = False
        self._far_target: Point2 | None = None  # OL 最远站位目标
        self._t_far: Point2 | None = None  # 最外围可放落点
        self._far_measured = False

    def _scan(self):
        """返回 (plateau, edge_placeable) 两个 Point2 列表 + 诊断串。"""
        enemy = self.enemy_start_locations[0]
        base_h = self.get_terrain_height(enemy)
        area: list[tuple[Point2, int]] = []
        for dx in range(-_SCAN_R, _SCAN_R + 1):
            for dy in range(-_SCAN_R, _SCAN_R + 1):
                p = Point2((round(enemy.x) + dx, round(enemy.y) + dy))
                if p.distance_to(enemy) > _SCAN_R:
                    continue
                area.append((p, self.get_terrain_height(p)))

        plateau = [p for p, h in area if abs(h - base_h) <= _H_TOL]
        plateau_set = {(p.x, p.y): h for p, h in area}

        def is_edge(p: Point2) -> bool:
            for ox in range(-_EDGE_NBR, _EDGE_NBR + 1):
                for oy in range(-_EDGE_NBR, _EDGE_NBR + 1):
                    nh = plateau_set.get((p.x + ox, p.y + oy))
                    if nh is None:
                        nh = self.get_terrain_height(Point2((p.x + ox, p.y + oy)))
                    if nh < base_h - _CLIFF_DROP:
                        return True
            return False

        plateau_placeable = [p for p in plateau if self.in_placement_grid(p)]
        edge = [p for p in plateau if is_edge(p)]
        edge_placeable = [p for p in edge if self.in_placement_grid(p)]

        # 高度分布(看清高地/悬崖分几级)
        buckets: dict[int, int] = {}
        for _p, h in area:
            buckets[h] = buckets.get(h, 0) + 1
        top = sorted(buckets.items(), key=lambda kv: -kv[1])[:6]
        diag = (
            f"base_h={base_h} | 扫描格={len(area)} | "
            f"高地(±{_H_TOL})={len(plateau)} 其中可放={len(plateau_placeable)} | "
            f"高地边缘={len(edge)} 其中可放={len(edge_placeable)} | "
            f"高度分布top6={top}"
        )
        return plateau, edge_placeable, diag

    def _off_cliff_dir(self, tile: Point2, base_h: int):
        """从高地边缘格 tile 指向【悬崖外低地】的单位方向(顺地形高度下降,不用基地中心)。
        扫 tile 周围低一截(悬崖下)的格,取它们方向的平均 = 往低地那侧。"""
        vx = vy = 0.0
        n = 0
        for ox in range(-4, 5):
            for oy in range(-4, 5):
                if ox == 0 and oy == 0:
                    continue
                q = Point2((tile.x + ox, tile.y + oy))
                if self.get_terrain_height(q) < base_h - _CLIFF_DROP:
                    vx += ox
                    vy += oy
                    n += 1
        if n == 0:
            return None
        norm = math.hypot(vx, vy) or 1.0
        return (vx / norm, vy / norm)

    async def on_step(self, iteration: int) -> None:
        enemy = self.enemy_start_locations[0]

        # ── Phase STATIC:不需视野,证明能读地形 ──
        if not self._static_done and iteration >= 2:
            self._static_done = True
            _plateau, edge_placeable, diag = self._scan()
            print("\n========== ① 静态地形读取(无视野) ==========", flush=True)
            print(diag, flush=True)
            # can_place 无视野时能不能判(验证 can_place 是否需要视野)
            sample = edge_placeable[:40]
            fits_novis = await self.can_place(UnitTypeId.NYDUSCANAL, sample) if sample else []
            print(
                f"③a can_place(NYDUSCANAL) 无视野时: {sum(fits_novis)}/{len(sample)} 边缘可放格返回 True",
                flush=True,
            )
            # 派 OL 去敌方主基供视野
            ols = self.units(UnitTypeId.OVERLORD)
            if ols:
                ols.first.move(enemy)
                self._ol_sent = True
                print(f"→ 派 OL 飞向敌方主基 {enemy} 取视野…", flush=True)

        # ── Phase NEAR:OL 贴敌方主基 → 确认可放落点 + 选最外围那个 + 推 OL 到最远站位 ──
        if self._static_done and not self._near_done and self._ol_sent:
            ols = self.units(UnitTypeId.OVERLORD)
            ol_near = any(u.distance_to(enemy) < 11 for u in ols)
            if ol_near and self.is_visible(enemy):
                self._near_done = True
                sight = round(ols.first.sight_range, 1) if ols else 11.0
                _plateau, edge_placeable, _diag = self._scan()
                visible_edge = [p for p in edge_placeable if self.is_visible(p)]
                fits = (
                    await self.can_place(UnitTypeId.NYDUSCANAL, visible_edge)
                    if visible_edge
                    else []
                )
                drops = [p for p, f in zip(visible_edge, fits, strict=False) if f]
                print("\n========== ②③ OL 贴脸(有视野) ==========", flush=True)
                print(f"OL 视野半径 sight_range = {sight}", flush=True)
                print(f"② 视野∩高地边缘可放格 = {len(visible_edge)} 个", flush=True)
                print(f"③ 能放下坑道虫 = {len(drops)}/{len(visible_edge)} 个", flush=True)
                if drops:
                    base_h = self.get_terrain_height(enemy)
                    # 按角度分 6 扇区,每扇区取最外围可放落点 → 几个分散的安全 OL 漂浮点
                    sectors: dict[int, Point2] = {}
                    for p in drops:
                        ang = math.atan2(p.y - enemy.y, p.x - enemy.x)
                        sec = int((ang + math.pi) / (2 * math.pi) * 6) % 6
                        if sec not in sectors or p.distance_to(enemy) > sectors[sec].distance_to(
                            enemy
                        ):
                            sectors[sec] = p
                    print(
                        "\n安全 OL 漂浮点(每扇区最外围可放落点 → 顺悬崖外推 10 格到低地):",
                        flush=True,
                    )
                    for sec in sorted(sectors):
                        tile = sectors[sec]
                        d = self._off_cliff_dir(tile, base_h)
                        if d is None:
                            continue
                        fp = Point2((tile.x + d[0] * 10, tile.y + d[1] * 10))
                        fh = self.get_terrain_height(fp)
                        print(
                            f"  扇区{sec}: 落点{(round(tile.x), round(tile.y))} → "
                            f"OL 漂浮点{(round(fp.x), round(fp.y))} "
                            f"(高度{fh},在悬崖外低地={fh < base_h - _CLIFF_DROP})",
                            flush=True,
                        )
                    # 取一个做 FAR 实地验证(移动 OL 真过去)
                    self._t_far = sectors[sorted(sectors)[0]]
                    # OL 站位 = 从该边缘落点【顺悬崖往低地那侧】推 (sight-1) 格 → OL 在边缘外 ~10 格,
                    #           落点刚好在视野边缘。方向来自地形高度下降,不是从基地中心往外(用户纠正)。
                    outdir = self._off_cliff_dir(self._t_far, base_h)
                    if outdir is None:
                        outdir = (
                            (self._t_far.x - enemy.x),
                            (self._t_far.y - enemy.y),
                        )
                        nrm = math.hypot(*outdir) or 1.0
                        outdir = (outdir[0] / nrm, outdir[1] / nrm)
                    push = sight - 1.0
                    self._far_target = Point2(
                        (self._t_far.x + outdir[0] * push, self._t_far.y + outdir[1] * push)
                    )
                    print(
                        f"选一个边缘可放落点 {(round(self._t_far.x), round(self._t_far.y))} → "
                        f"顺悬崖往低地方向推 OL 到 "
                        f"{(round(self._far_target.x), round(self._far_target.y))}"
                        f"(离边缘 {push:.0f} 格)验证…",
                        flush=True,
                    )
                    ols.first.move(self._far_target)
                else:
                    print("无可放落点,退出。", flush=True)
                    await self.client.leave()

        # ── Phase FAR:OL 推到最远后 → 验证它离得那么远还看不看得见+放不放得下 ──
        if self._near_done and not self._far_measured and self._far_target is not None:
            ols = self.units(UnitTypeId.OVERLORD)
            arrived = any(u.distance_to(self._far_target) < 2.0 for u in ols)
            if arrived and self._t_far is not None:
                self._far_measured = True
                ol = min(ols, key=lambda u: u.distance_to(self._far_target))
                vis = self.is_visible(self._t_far)
                can = (await self.can_place(UnitTypeId.NYDUSCANAL, [self._t_far]))[0]
                # 关键指标:OL 离【高地边缘落点】几格(=在悬崖外多远),不是离基地中心
                gap_edge = round(ol.distance_to(self._t_far), 1)
                ol_h = self.get_terrain_height(ol.position)
                base_h = self.get_terrain_height(enemy)
                on_low = ol_h < base_h - _CLIFF_DROP  # OL 是否已在低地(悬崖外)
                _p, edge_placeable, _d = self._scan()
                still_vis = [p for p in edge_placeable if self.is_visible(p)]
                print("\n========== ④ OL 边缘外站位验证 ==========", flush=True)
                print(
                    f"OL 离高地边缘落点 = {gap_edge} 格(视野半径 {round(ols.first.sight_range, 1)});"
                    f"OL 所在高度={ol_h}(基地高度={base_h},在悬崖外低地={on_low});"
                    f"落点还看得见={vis}、还放得下={can}",
                    flush=True,
                )
                print(
                    f"此边缘外站位下,视野内仍能放坑道虫的边缘格还有 {len(still_vis)} 个",
                    flush=True,
                )
                print(
                    f"\n结论:OL 站在高地边缘外 {gap_edge:.0f} 格(视野半径 11)、"
                    f"{'已在悬崖外低地' if on_low else '仍在高地上'},仍能落坑道虫。探针退出。",
                    flush=True,
                )
                await self.client.leave()

        # 兜底:太久没到位也退
        if iteration > 1200:
            print("\n[timeout] 探针退出。", flush=True)
            await self.client.leave()


def main() -> None:
    ap = argparse.ArgumentParser(description="坑道虫落点能力探针")
    ap.add_argument("--map", default=MAP_NAME)
    args = ap.parse_args()
    print("=== 坑道虫落点能力探针: 读地形/算边缘/判可放 ===", flush=True)
    run_game(
        maps.get(args.map),
        [
            Bot(Race.Zerg, TerrainProbe(), name="TerrainProbe"),
            Computer(Race.Terran, Difficulty.VeryEasy),
        ],
        realtime=False,
    )


if __name__ == "__main__":
    main()
