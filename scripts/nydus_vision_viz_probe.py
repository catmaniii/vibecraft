"""坑道虫「OL 供视野点 ↔ 坑道虫落点」可视化探针（2026-07-26）。

回答用户问题:**给坑道虫开视野的那只"房子"(OL)到底有哪些点可以选?** 并把
「OL 站位」和「坑道虫落点」两个点**同屏画出来 + 真放一只 OL**,好截图看。

它跑的就是产品里那套点(同源函数,不是另写一份):
  - OL 站位候选 = `overlord_float_points()`  → D1(高地边缘顺悬崖外推 _OL_PUSH 格到低地)
                                              + D3(按角度分 5 扇区,每扇区一只)
  - 坑道虫落点   = `enemy_plateau_edges()` 的高地边缘可放格 snap 到格心(F35)
  - 配对         = D6「落点耦合到 OL 真实视野」:每只 OL 取离它最近的可放边缘格

流程:算点 → debug 真生一只 OL 到每个候选点(真视野) → 等视野 → 对落点跑
`is_visible` + `can_place(NYDUSCANAL)` 双验 → 画框标字 → 镜头逐扇区巡览(供截图)。

画的东西(全 ASCII,CJK 在 debug draw 不渲染):
  黄框 + "OL<k>"     = 那只房子(OL)站的点,悬崖外低地
  品红框 + "NYDUS<k>" = 该 OL 视野内、可放坑道虫的格(3x3 footprint 大小)
  黄→品红连线        = 这一对的对应关系(线长 = OL 离落点几格)
  红框 + "ENEMY BASE" = 敌方主基中心,给方位参照

跑法(realtime,画面要渲染才截得到图):
  .venv/Scripts/python.exe scripts/nydus_vision_viz_probe.py [--map DaybreakLE] [--hold 14]
控制台每切一个扇区打 `CAMSECTOR k ...`,外部截图脚本据此给图命名。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2, Point3

from vibecraft.bot.auto_combat.zerg.plans import nydus_landing_planner as nlp
from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import (
    _OL_PUSH,
    _OL_SIGHT,
    _SECTOR_COUNT,
    enemy_plateau_edges,
    mineral_centroid,
    off_cliff_dir,
    overlord_float_points,
)

MAP_NAME = "DaybreakLE"

_C_OL = (255, 230, 0)  # 黄:OL(房子)站位
_C_NYDUS = (255, 0, 255)  # 品红:本扇区选中的坑道虫落点
_C_NYDUS_DIM = (140, 0, 140)  # 暗品红:其余可落格(全集)
_C_BASE = (255, 0, 0)  # 红:敌方主基中心
_C_LINE = (0, 220, 255)  # 青:配对连线


class NydusVisionViz(BotAI):  # type: ignore[misc]
    def __init__(self, hold_s: float, only: set[int] | None = None) -> None:
        super().__init__()
        self._hold_s = hold_s
        self._only = only  # 只保留这些扇区的 OL 站位（None=全部）
        self._center: Point2 | None = None
        self._floats: list[Point2] = []
        self._sector_ids: list[int] = []
        self._fit_spots: list[Point2] = []
        self._station_spot: dict[int, Point2] = {}
        self._edges: list[Point2] = []
        self._spawned = False
        self._spawn_t = 0.0
        self._paired = False
        # (idx, ol_point, nydus_tile, dist, visible, can_place)
        self._pairs: list[tuple[int, Point2, Point2, float, bool, bool]] = []
        self._placeable: list[Point2] = []
        self._cam_idx = -1
        self._cam_t = 0.0

    # ── 几何:与产品同源 ────────────────────────────────────────────────
    def _landing_tiles(self, center: Point2) -> list[Point2]:
        """高地边缘可放格 → snap 格心(F35) → 按离矿脉质心近排序(D2 屠农民优先)。"""
        edge, _h = enemy_plateau_edges(self, center)
        if not edge:
            return []
        snapped = [Point2((math.floor(p.x) + 0.5, math.floor(p.y) + 0.5)) for p in edge]
        mc = mineral_centroid(self, center)
        ref = mc if mc is not None else center
        snapped.sort(key=lambda p: p.distance_to(ref))
        return snapped

    def _fits_3x3(self, t: Point2) -> bool:
        """静态判「3x3 放得下」:自己 + 周围 8 格都在 placement grid 内。

        产品现在只查单格 `in_placement_grid` 就当候选，但坑道虫 footprint 是 3x3——
        最外围那圈贴崖边的格单格合法、3x3 却悬空 → `can_place` 必 False。这个静态判据
        不需要视野，能在开局就把「其实放不下的格」剔掉（下面用真 can_place 校验它准不准）。
        """
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                if not self.in_placement_grid(Point2((t.x + ox, t.y + oy))):
                    return False
        return True

    def _stations_from_spots(
        self, spots: list[Point2], base_h: int, sectors: int
    ) -> list[tuple[int, Point2, Point2]]:
        """由【真能放下的落点】反推 OL 站位:按角度分扇区,每扇区取最外围的可落格,
        顺悬崖外推 push 格到低地 → 该站位视野圈里必然含这个落点(push 9 < 视野 11)。

        与产品现行做法相反:产品从「最外围边缘格」推站位(那格常放不下)，这里从
        「放得下的格」推站位，保证站位有用。返回 [(扇区号, OL 站位, 对应落点)]。
        """
        center = self._center
        assert center is not None
        best: dict[int, tuple[Point2, float]] = {}
        for p in spots:
            ang = math.atan2(p.y - center.y, p.x - center.x)
            sec = int((ang + math.pi) / (2 * math.pi) * sectors) % sectors
            d = p.distance_to(center)
            if sec not in best or d > best[sec][1]:
                best[sec] = (p, d)
        out: list[tuple[int, Point2, Point2]] = []
        for sec in sorted(best):
            spot = best[sec][0]
            direction = off_cliff_dir(self, spot, base_h)
            if direction is None:
                continue
            out.append(
                (
                    sec,
                    Point2((spot.x + direction[0] * _OL_PUSH, spot.y + direction[1] * _OL_PUSH)),
                    spot,
                )
            )
        return out

    def _p3(self, p: Point2) -> Point3:
        return Point3((p.x, p.y, self.get_terrain_z_height(p)))

    # ── 主循环 ───────────────────────────────────────────────────────
    async def on_step(self, iteration: int) -> None:
        if iteration < 2:
            return
        if self._center is None:
            await self._compute_and_spawn()
            return
        if not self._paired and self.time - self._spawn_t > 3.0:
            await self._pair_and_report()
        self._draw()
        await self._cycle_camera()
        if iteration > 40000:
            await self.client.leave()

    async def _compute_and_spawn(self) -> None:
        center = self.enemy_start_locations[0]
        self._center = center
        self._edges = self._landing_tiles(center)
        base_h = int(self.get_terrain_height(center))
        # 静态先筛「3x3 放得下」的格,再由它们反推 OL 站位（保证站位圈里有可落点）
        self._fit_spots = [t for t in self._edges if self._fits_3x3(t)]
        stations = self._stations_from_spots(self._fit_spots, base_h, _SECTOR_COUNT)
        stations = [s for s in stations if self._only is None or s[0] in self._only]
        self._sector_ids = [s[0] for s in stations]
        self._floats = [s[1] for s in stations]
        self._station_spot = {s[0]: s[2] for s in stations}
        print("\n========== ① OL(房子)供视野候选点 ==========", flush=True)
        print(
            f"敌方主基中心 = ({center.x:.1f}, {center.y:.1f}) 高度={base_h} | "
            f"高地边缘格 = {len(self._edges)} 个,其中【3x3 真放得下】= {len(self._fit_spots)} 个 | "
            f"OL 站位候选 = {len(self._floats)} 个(由可落格反推,分 {_SECTOR_COUNT} 扇区, "
            f"顺悬崖外推 push={_OL_PUSH:.0f} 格, OL 视野={_OL_SIGHT:.0f})",
            flush=True,
        )
        for i, fp in enumerate(self._floats):
            k = self._sector_ids[i]
            h = int(self.get_terrain_height(fp))
            print(
                f"  OL{k}: ({fp.x:.1f}, {fp.y:.1f}) 高度={h} "
                f"(在悬崖外低地={h < base_h - 12}) 离主基中心={fp.distance_to(center):.1f}",
                flush=True,
            )
        # 真放房子:每个候选点生一只 OL(真视野);顺带无敌,免得被 VeryEasy 打掉影响截图
        if self._floats:
            await self.client.debug_create_unit(
                [[UnitTypeId.OVERLORD, 1, fp, 1] for fp in self._floats]
            )
            await self.client.debug_god()
            self._spawned = True
            self._spawn_t = self.time
            print(f"→ 已在 {len(self._floats)} 个候选点各放一只真 OL,等视野…", flush=True)

    async def _pair_and_report(self) -> None:
        self._paired = True
        center = self._center
        assert center is not None
        # 先把「此刻真能落」的格全解出来:视野内 + can_place(3x3 footprint 放得下)
        vis_edges = [t for t in self._edges if self.is_visible(t)]
        fits = await self.can_place(UnitTypeId.NYDUSCANAL, vis_edges) if vis_edges else []
        self._placeable = [t for t, f in zip(vis_edges, fits, strict=False) if f]
        print("\n========== ② 视野 ∩ 可放 ==========", flush=True)
        print(
            f"{len(self._floats)} 只 OL 一起覆盖 {len(vis_edges)}/{len(self._edges)} 个高地边缘格; "
            f"其中 {len(self._placeable)} 个真能落坑道虫",
            flush=True,
        )
        # 校验静态 3x3 判据准不准(它是否等价于真 can_place)
        vis_set = {(t.x, t.y) for t in vis_edges}
        pred_yes = [t for t in self._fit_spots if (t.x, t.y) in vis_set]
        real_yes = {(t.x, t.y) for t in self._placeable}
        hit = sum(1 for t in pred_yes if (t.x, t.y) in real_yes)
        miss = len(real_yes) - hit
        print(
            f"静态判据校验:视野内被静态判为「3x3 放得下」{len(pred_yes)} 个,真 can_place 通过 "
            f"{hit} 个(误报 {len(pred_yes) - hit});真能放但静态漏判 {miss} 个",
            flush=True,
        )
        # 配对(D6):每只 OL 取【它自己视野圈内】离它最近的可落格。圈里一个都没有 →
        # 该站位对落坑道虫没用（别连到圈外那个它根本看不见的点上，那是假配对）。
        pairs: list[tuple[int, Point2, Point2, float, bool, bool]] = []
        print("\n========== ③ 配对:每只 OL(房子) ↔ 它视野圈内的坑道虫落点 ==========", flush=True)
        for i, fp in enumerate(self._floats):
            k = self._sector_ids[i]
            in_sight = [t for t in self._placeable if fp.distance_to(t) <= _OL_SIGHT]
            if not in_sight:
                nearest_out = (
                    min(self._placeable, key=lambda t: fp.distance_to(t))
                    if self._placeable
                    else None
                )
                d_out = fp.distance_to(nearest_out) if nearest_out else -1
                print(
                    f"  OL{k} ({fp.x:.1f},{fp.y:.1f}) → 视野圈({_OL_SIGHT:.0f}格)内【没有】可落格; "
                    f"最近的可落格在 {d_out:.1f} 格外(它看不见)→ 此站位落不了坑道虫",
                    flush=True,
                )
                continue
            near = min(in_sight, key=lambda t: fp.distance_to(t))
            pairs.append((k, fp, near, fp.distance_to(near), True, True))
            print(
                f"  OL{k} ({fp.x:.1f},{fp.y:.1f}) → NYDUS{k} ({near.x:.1f},{near.y:.1f}) "
                f"相距={fp.distance_to(near):.1f}格(视野{_OL_SIGHT:.0f}) "
                f"圈内可落格共 {len(in_sight)} 个",
                flush=True,
            )
        self._pairs = pairs
        print("\n全部可落格坐标:", flush=True)
        for t in self._placeable:
            print(f"  ({t.x:.1f}, {t.y:.1f}) 离主基中心={t.distance_to(center):.1f}", flush=True)
        self._dump_json()
        print("\n========== ④ 镜头逐扇区巡览(截图) ==========", flush=True)

    def _dump_json(self) -> None:
        """把地形高度 + 各类点位落盘,供 nydus_vision_map.py 画俯视全局图。"""
        center = self._center
        assert center is not None
        pa = self.game_info.playable_area
        x0, y0 = int(pa.x), int(pa.y)
        x1, y1 = int(pa.x + pa.width), int(pa.y + pa.height)
        heights = [
            [int(self.get_terrain_height(Point2((x, y)))) for x in range(x0, x1)]
            for y in range(y0, y1)
        ]
        data = {
            "map": self.game_info.map_name,
            "origin": [x0, y0],
            "heights": heights,
            "enemy_center": [center.x, center.y],
            "my_start": [self.start_location.x, self.start_location.y],
            "ol_points": [
                [p.x, p.y, k] for p, k in zip(self._floats, self._sector_ids, strict=False)
            ],
            "edge_tiles": [[p.x, p.y] for p in self._edges],
            "placeable": [[p.x, p.y] for p in self._placeable],
            "fit_spots": [[p.x, p.y] for p in self._fit_spots],
            "product_ol_points": [[p.x, p.y] for p in overlord_float_points(self, center)],
            "pairs": [[k, [f.x, f.y], [n.x, n.y], d] for k, f, n, d, _v, _c in self._pairs],
            "ol_sight": _OL_SIGHT,
            "ol_push": _OL_PUSH,
        }
        out = pathlib.Path(os.environ.get("NYDUS_VIZ_JSON", "nydus_viz.json"))
        out.write_text(json.dumps(data), encoding="utf-8")
        print(f"→ 地形/点位已 dump 到 {out}", flush=True)

    def _draw(self) -> None:
        center = self._center
        assert center is not None
        self.client.debug_text_screen(
            "YELLOW box=OL(vision house) spot   MAGENTA box=NYDUS canal spot   RED=enemy base",
            (0.02, 0.02),
            color=(255, 255, 255),
            size=18,
        )
        self.client.debug_box2_out(self._p3(center), 2.5, color=_C_BASE)
        self.client.debug_text_world("ENEMY BASE", self._p3(center), color=_C_BASE, size=16)
        # 全部「视野内 + 放得下」的格画暗品红小框 = 可选落点全集
        for t in self._placeable:
            self.client.debug_box2_out(self._p3(t), 1.5, color=_C_NYDUS_DIM)
        hot_k = self._pairs[self._cam_idx][0] if 0 <= self._cam_idx < len(self._pairs) else -1
        for k, fp, near, d, _vis, _can in self._pairs:
            hot = k == hot_k
            self.client.debug_box2_out(self._p3(fp), 1.2, color=_C_OL)
            self.client.debug_text_world(
                f"OL{k} (vision house)" if hot else f"OL{k}",
                self._p3(fp),
                color=_C_OL,
                size=18 if hot else 14,
            )
            # 坑道虫 footprint ~3x3 → 半边长 1.5
            self.client.debug_box2_out(self._p3(near), 1.5, color=_C_NYDUS)
            self.client.debug_text_world(
                f"NYDUS{k} CAN PLACE  d={d:.0f}" if hot else f"NYDUS{k}",
                self._p3(near),
                color=_C_NYDUS,
                size=18 if hot else 14,
            )
            self.client.debug_line_out(self._p3(fp), self._p3(near), color=_C_LINE)

    async def _cycle_camera(self) -> None:
        if not self._pairs:
            return
        if self._cam_idx < 0 or self.time - self._cam_t >= self._hold_s:
            self._cam_idx = (self._cam_idx + 1) % len(self._pairs)
            self._cam_t = self.time
            k, fp, near, d, vis, can = self._pairs[self._cam_idx]
            mid = Point2(((fp.x + near.x) / 2, (fp.y + near.y) / 2))
            # 镜头对准两点中点:OL 与落点相距约 push=9 格,各离中点 ~4.5 格,正好同框。
            # (别再朝地图中心让位——那会把两点一起挤出画面,踩过。)
            await self.client.move_camera(mid)
            print(
                f"CAMSECTOR {k} ol=({fp.x:.0f},{fp.y:.0f}) nydus=({near.x:.0f},{near.y:.0f}) "
                f"d={d:.1f} vis={vis} place={can} t={self.time:.0f}",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="坑道虫 OL 供视野点 / 落点 可视化探针")
    ap.add_argument("--map", default=MAP_NAME)
    ap.add_argument("--hold", type=float, default=14.0, help="每个扇区镜头停留秒数")
    ap.add_argument(
        "--seed", type=int, default=20260726, help="固定随机种子:同种子出生点一样,截图可复现"
    )
    ap.add_argument("--opponent", default="Terran", choices=["Terran", "Protoss", "Zerg"])
    ap.add_argument("--only", default="", help="只保留这些扇区的 OL 站位,逗号分隔(如 1,2)")
    ap.add_argument(
        "--scan-r",
        type=int,
        default=0,
        help="敌方主基周围地形扫描半径(格);0=用产品默认。放大可覆盖离基地更远的高地边缘",
    )
    args = ap.parse_args()
    if args.scan_r:
        nlp._SCAN_R = args.scan_r  # 探针专用:放大扫描半径看远处边缘（不改产品默认）
        print(f"[probe] 扫描半径覆写为 {args.scan_r} 格", flush=True)
    only = {int(x) for x in args.only.split(",") if x.strip()} or None
    print("=== 坑道虫: OL(房子)供视野点 ↔ 坑道虫落点 可视化探针 ===", flush=True)
    run_game(
        maps.get(args.map),
        [
            Bot(Race.Zerg, NydusVisionViz(args.hold, only), name="NydusViz"),
            Computer(Race[args.opponent], Difficulty.VeryEasy),
        ],
        realtime=True,
        random_seed=args.seed,
    )


if __name__ == "__main__":
    main()
