"""debug_draw_probe.py — 验证 SC2 debug draw 在「单人」vs「2 bot 多人」下显不显示。

背景:控制边界可视化想用 bot 在游戏内给受控单位画圈(debug draw,零漂移)。但 SC2
可能在「多人对战」里禁掉 debug(公平性)。本探针一锤定音。**一台 PC 就够**——两 bot
host/join 在本机开两窗口 = 真正的多人局,和将来局域网双机在"是不是多人"上等价。

用法(在 .venv 里跑):
  # 对照组(单人 vs 内置电脑,debug 应能画 → 证明画圈代码本身好使):
  .venv/Scripts/python.exe scripts/debug_draw_probe.py --mode solo

  # 实验组(2 bot 多人,测多人是否禁 debug):
  .venv/Scripts/python.exe scripts/debug_draw_probe.py --mode versus

判读(盯 SC2 窗口看自己单位身上有没有【绿圈】+ 头顶【MARK】飘字):
  solo 有圈 + versus 有圈  → 多人能用 debug,圈这条路成立(地图改不用碰)
  solo 有圈 + versus 没圈  → 多人禁 debug,转数据面板 + 自制地图路线
  solo 就没圈              → 代码/环境问题(不是多人限制),先修对照组
另外看控制台:若打印 "_send_debug 失败" 说明 SC2 直接拒绝了 debug 请求(程序信号)。
"""

from __future__ import annotations

import argparse

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer

MAP_NAME = "DaybreakLE"  # VibeCraft 默认地图(已装)


# WP-A 控制边界配色(对齐 director.py _CONTROL_COLORS)
_WPA_CYAN = (0, 220, 255)  # 普通指令
_WPA_G1 = (255, 230, 0)  # 1 队


class DrawProbeBot(BotAI):
    """什么都不干,只每帧给自己单位画 debug。两种模式:
    - 普通: 全单位品红方盒 + MARK(验 debug draw 渲不渲染)
    - wpa:  模拟 WP-A 控制边界——前 3 个 cyan 框+"守瞭望塔"、后 3 个黄框+"1队进攻"、
            其余不画(验玩家所见 + 中文 debug 文字能否渲染)。
    """

    def __init__(self, label: str, wpa: bool = False) -> None:
        super().__init__()
        self._label = label
        self._wpa = wpa
        self._send_err_logged = False

    async def on_step(self, iteration: int) -> None:
        # 软上限:realtime 下跑够久够你看了就自己退,免得挂着
        if iteration > 3000:
            await self.client.leave()
            return

        if self._wpa:
            self._draw_wpa()
            return

        # ① 屏幕正中一行大字 HUD —— 最不可能看错的信号(2D 屏幕字,游戏世界里没这东西)。
        #    渲染出来 = debug 在工作;看不到 = debug 没渲染。
        self.client.debug_text_screen(
            f"### DEBUG DRAW TEST [{self._label}] step={iteration} ###",
            (0.18, 0.45),
            color=(255, 255, 0),
            size=30,
        )

        for drawn, u in enumerate(self.units):  # 自己的非建筑单位(开局 12 农民)
            # ② 线框方盒(几何形状,跟瓦斯泉/水晶等地图美术绝不会混) —— 亮品红
            self.client.debug_box2_out(u, 0.5, color=(255, 0, 255))
            self.client.debug_text_world("MARK", u, color=(255, 255, 0), size=16)
            _ = drawn  # 计数只为可读性，未使用
        # ③ 主基地套大红盒(红色地图上没有,不可能是天然物)
        for th in self.townhalls:
            self.client.debug_box2_out(th, 2.5, color=(255, 0, 0))

    def _draw_wpa(self) -> None:
        """模拟 WP-A facade.draw_debug_marks 的玩家所见效果。"""
        self.client.debug_text_screen(
            "### WP-A 控制边界: cyan=守瞭望塔 / 黄=1队进攻 / 其余不画 ###",
            (0.12, 0.04),
            color=(255, 255, 255),
            size=20,
        )
        # 模拟 WP-A 三组(对齐 director 配色表):指令卡=方框+verb色+英文名质心;
        # 编队=圆环+队色+队号质心;有目标的画质心→目标连线。每组文字只一个(质心)。
        from sc2.position import Point2, Point3

        from vibecraft.bot.director import _GROUP_COLORS, _VERB_COLORS

        us = list(self.units)

        def draw_group(units, shape, color, label, target=None):
            alive = [u for u in units if u is not None]
            if not alive:
                return
            for u in alive:
                if shape == "ring":
                    self.client.debug_sphere_out(u, 0.7, color=color)
                else:
                    self.client.debug_box2_out(u, 0.6, color=color)
            cx = sum(u.position3d.x for u in alive) / len(alive)
            cy = sum(u.position3d.y for u in alive) / len(alive)
            cz = sum(u.position3d.z for u in alive) / len(alive)
            c3 = Point3((cx, cy, cz))
            self.client.debug_text_world(label, c3, color=color, size=14)
            if target is not None:
                tz = self.get_terrain_z_height(Point2(target))
                tp = Point3((target[0], target[1], tz))
                self.client.debug_line_out(c3, tp, color=color)
                self.client.debug_sphere_out(tp, 0.9, color=color)

        # 组A: 指令卡 attack(红方框+英文名+目标连线,目标=主基地附近)
        th = self.townhalls.first if self.townhalls else None
        tgt = (th.position.x + 6, th.position.y + 6) if th else None
        draw_group(us[0:3], "box", _VERB_COLORS["attack_move"], "attack", target=tgt)
        # 组B: 1队(黄圆环+队号"1")
        draw_group(us[3:6], "ring", _GROUP_COLORS[1], "1")
        # 组C: 指令卡 hold(蓝方框+英文名)
        draw_group(us[6:9], "box", _VERB_COLORS["hold_position"], "hold")
        # us[9:] 不画 —— bot 自由单位留白
        # 注意:绝不手动调 _send_debug!框架在 on_step 后自动发一次。


def main() -> None:
    ap = argparse.ArgumentParser(description="SC2 debug draw 单人/多人验证探针")
    ap.add_argument(
        "--mode",
        choices=["solo", "versus", "wpa"],
        default="versus",
        help="solo=1bot 对照组; versus=2bot 多人(默认); wpa=WP-A 控制边界配色效果",
    )
    ap.add_argument("--map", default=MAP_NAME, help=f"地图名(默认 {MAP_NAME})")
    args = ap.parse_args()

    sc2_map = maps.get(args.map)

    if args.mode == "wpa":
        print("=== WP-A 控制边界效果: cyan 框=守瞭望塔 / 黄框=1队进攻 / 其余留白 ===", flush=True)
        run_game(
            sc2_map,
            [
                Bot(Race.Protoss, DrawProbeBot("WPA", wpa=True), name="ProbeWPA"),
                Computer(Race.Protoss, Difficulty.VeryEasy),
            ],
            realtime=True,
        )
    elif args.mode == "solo":
        print("=== SOLO 对照组: 1 bot vs 内置电脑(单人)。期望: 能看到绿圈 ===", flush=True)
        run_game(
            sc2_map,
            [
                Bot(Race.Protoss, DrawProbeBot("SOLO"), name="ProbeSolo"),
                Computer(Race.Protoss, Difficulty.VeryEasy),
            ],
            realtime=True,
        )
    else:
        print(
            "=== VERSUS 实验组: 2 bot host/join(多人,本机两窗口)。"
            "盯任一窗口看自己单位有没有绿圈 ===",
            flush=True,
        )
        run_game(
            sc2_map,
            [
                Bot(Race.Protoss, DrawProbeBot("P1"), name="ProbeP1"),
                Bot(Race.Protoss, DrawProbeBot("P2"), name="ProbeP2"),
            ],
            realtime=True,
        )


if __name__ == "__main__":
    main()
