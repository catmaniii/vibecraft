"""把 `nydus_vision_viz_probe.py` dump 的地形/点位渲染成一张**俯视全局图**。

游戏内截图是斜视角局部,看不清"整圈候选点的空间关系";这张俯视图补上:
敌方主基高地、全部高地边缘格、其中真能落坑道虫的格、5 个 OL(房子)站位候选
及其视野圈、配对连线,一图看全。

跑法:
  .venv/Scripts/python.exe scripts/nydus_vision_map.py <dump.json> <out.png>
"""

from __future__ import annotations

import json
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

_PX = 11  # 每格像素
_PAD = 6  # 边距(格)
_FONT_CJK = "C:/Windows/Fonts/msyh.ttc"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_FONT_CJK, size)
    except OSError:
        return ImageFont.load_default()


def render(data: dict, out_path: pathlib.Path) -> None:
    ox, oy = data["origin"]
    heights = data["heights"]
    # ol_points: [x, y, sector_id]（老 dump 只有 x,y → 用下标当 sector_id）
    ol_pts = [(p[0], p[1], int(p[2]) if len(p) > 2 else i) for i, p in enumerate(data["ol_points"])]
    edges = [tuple(p) for p in data["edge_tiles"]]
    placeable = [tuple(p) for p in data["placeable"]]
    pairs = data["pairs"]
    ec = tuple(data["enemy_center"])
    sight = float(data["ol_sight"])

    # 视窗 = 所有关注点的包围盒 + 边距
    xs = [p[0] for p in ol_pts + edges + [ec]]
    ys = [p[1] for p in ol_pts + edges + [ec]]
    x0, x1 = int(min(xs)) - _PAD, int(max(xs)) + _PAD
    y0, y1 = int(min(ys)) - _PAD, int(max(ys)) + _PAD
    w, h = (x1 - x0) * _PX, (y1 - y0) * _PX
    legend_h = 150
    img = Image.new("RGB", (w, h + legend_h), (12, 12, 16))
    d = ImageDraw.Draw(img)

    def sx(wx: float) -> float:
        return (wx - x0) * _PX

    def sy(wy: float) -> float:
        return (y1 - wy) * _PX  # SC2 的 y 向上,图片 y 向下 → 翻转

    # ── 地形高度底图(灰阶:越高越亮)──
    hs = [v for row in heights for v in row if v > 0]
    lo, hi = (min(hs), max(hs)) if hs else (0, 1)
    for gy, row in enumerate(heights):
        wy = oy + gy
        if not (y0 <= wy < y1):
            continue
        for gx, hv in enumerate(row):
            wx = ox + gx
            if not (x0 <= wx < x1):
                continue
            t = 0.0 if hi == lo else (hv - lo) / (hi - lo)
            g = int(30 + 150 * t) if hv > 0 else 8
            d.rectangle([sx(wx), sy(wy) - _PX, sx(wx) + _PX, sy(wy)], fill=(g, g, int(g * 1.05)))

    # ── 高地边缘可放格(坑道虫落点候选全集,暗青)──
    for p in edges:
        d.rectangle(
            [sx(p[0]) - 3, sy(p[1]) - 3, sx(p[0]) + 3, sy(p[1]) + 3],
            outline=(60, 110, 130),
        )
    # ── 真能落的格(品红实心)──
    for p in placeable:
        d.rectangle(
            [
                sx(p[0]) - _PX * 0.9,
                sy(p[1]) - _PX * 0.9,
                sx(p[0]) + _PX * 0.9,
                sy(p[1]) + _PX * 0.9,
            ],
            fill=(220, 0, 220),
            outline=(255, 140, 255),
        )
    # ── 敌方主基中心 ──
    d.ellipse(
        [sx(ec[0]) - 9, sy(ec[1]) - 9, sx(ec[0]) + 9, sy(ec[1]) + 9], outline=(255, 60, 60), width=3
    )
    d.text((sx(ec[0]) + 12, sy(ec[1]) - 8), "敌方主基", font=_font(15), fill=(255, 90, 90))

    # ── OL 站位候选 + 视野圈 + 配对连线 ──
    pair_by_k = {p[0]: p for p in pairs}
    for p in ol_pts:
        k = p[2]
        r = sight * _PX
        d.ellipse([sx(p[0]) - r, sy(p[1]) - r, sx(p[0]) + r, sy(p[1]) + r], outline=(120, 110, 30))
        pr = pair_by_k.get(k)
        if pr is not None:
            n = pr[2]
            d.line([sx(p[0]), sy(p[1]), sx(n[0]), sy(n[1])], fill=(0, 200, 230), width=2)
            d.text(
                ((sx(p[0]) + sx(n[0])) / 2, (sy(p[1]) + sy(n[1])) / 2 - 16),
                f"{pr[3]:.0f}格",
                font=_font(14),
                fill=(0, 220, 255),
            )
        d.ellipse(
            [sx(p[0]) - 8, sy(p[1]) - 8, sx(p[0]) + 8, sy(p[1]) + 8],
            fill=(255, 225, 0),
            outline=(80, 70, 0),
        )
        d.text((sx(p[0]) - 6, sy(p[1]) - 26), f"OL{k}", font=_font(17), fill=(255, 235, 60))

    # ── 图例 ──
    ly = h + 8
    f = _font(16)
    d.text(
        (10, ly),
        f"{data['map']} · 坑道虫落点 与 OL(虫族房子)供视野站位",
        font=_font(19),
        fill=(240, 240, 240),
    )
    rows = [
        (
            (255, 225, 0),
            f"OL 站位候选 {len(ol_pts)} 个（高地边缘顺悬崖外推 push={data['ol_push']:.0f} 格到低地，"
            f"按角度分 {len(ol_pts)} 扇区各一只）；细黄圈 = 它的视野半径 {sight:.0f}",
        ),
        ((220, 0, 220), f"真能落坑道虫的格 {len(placeable)} 个（视野内 ∧ can_place 3x3 放得下）"),
        ((60, 110, 130), f"高地边缘可放格全集 {len(edges)} 个（多数贴崖边，3x3 其实放不下）"),
        ((0, 200, 230), "连线 = 该 OL 站位 ↔ 它视野里最近的可落点（连线长度即两者相距格数）"),
    ]
    for i, (c, t) in enumerate(rows):
        yy = ly + 30 + i * 26
        d.rectangle([12, yy + 3, 28, yy + 15], fill=c)
        d.text((36, yy), t, font=f, fill=(215, 215, 215))

    img.save(out_path)
    print(f"saved {out_path} ({img.width}x{img.height})")


def main() -> None:
    src = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    render(json.loads(src.read_text(encoding="utf-8")), out)


if __name__ == "__main__":
    main()
