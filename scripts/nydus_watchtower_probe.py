"""瞭望塔(小高塔)能不能给坑道虫供视野 —— 真机探针(2026-07-26)。

用户设想:除了「高地边缘外推 9 格」那种站位，OL 还可以去敌方主基旁边的**瞭望塔**
(Xel'Naga Watch Tower)，塔的视野大(半径 22 > OL 自己的 11)，足够覆盖坑道虫落点。

但 SC2 规则里瞭望塔通常**只有地面单位占得住**，飞行单位路过不算占领。这条不能凭印象
下结论，本探针一锤定音:

  ① 列出地图上所有瞭望塔坐标 + 离敌方主基多远
  ② 在离敌方主基最近的那座塔上生一只 **OL(飞行)**，量它周围可见格数
  ③ 再在同一座塔上生一只 **小狗(地面)**，量同样的可见格数
  ④ 两者对比:地面显著大于飞行 → 塔只认地面单位，OL 站塔上没有额外视野
  ⑤ 顺带报:该塔视野覆盖了多少个敌方高地边缘落点、其中几个 can_place

跑法(realtime 不需要，fast 即可):
  .venv/Scripts/python.exe scripts/nydus_watchtower_probe.py [--map DaybreakLE]
"""

from __future__ import annotations

import argparse

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2

from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import enemy_plateau_edges

MAP_NAME = "DaybreakLE"
_MEASURE_R = 26  # 量视野时扫塔周围多大半径


class WatchtowerProbe(BotAI):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self._phase = 0
        self._t_mark = 0.0
        self._tower: Point2 | None = None
        self._air_vis = -1
        self._ground_vis = -1

    def _visible_count(self, around: Point2) -> int:
        n = 0
        for dx in range(-_MEASURE_R, _MEASURE_R + 1):
            for dy in range(-_MEASURE_R, _MEASURE_R + 1):
                p = Point2((around.x + dx, around.y + dy))
                if p.distance_to(around) <= _MEASURE_R and self.is_visible(p):
                    n += 1
        return n

    async def on_step(self, iteration: int) -> None:
        if iteration < 3:
            return
        enemy = self.enemy_start_locations[0]

        if self._phase == 0:
            towers = list(self.watchtowers)
            print("\n========== ① 地图上的瞭望塔 ==========", flush=True)
            if not towers:
                print("这张图没有瞭望塔,退出。", flush=True)
                await self.client.leave()
                return
            for t in towers:
                print(
                    f"  塔 @ ({t.position.x:.1f}, {t.position.y:.1f}) "
                    f"离敌方主基={t.position.distance_to(enemy):.1f} "
                    f"离我方主基={t.position.distance_to(self.start_location):.1f}",
                    flush=True,
                )
            tw = min(towers, key=lambda u: u.position.distance_to(enemy))
            self._tower = tw.position
            print(
                f"→ 取离敌方主基最近的那座 ({self._tower.x:.1f},{self._tower.y:.1f}) 做实验",
                flush=True,
            )
            await self.client.debug_create_unit([[UnitTypeId.OVERLORD, 1, self._tower, 1]])
            await self.client.debug_god()
            self._phase = 1
            self._t_mark = self.time
            return

        if self._phase == 1 and self.time - self._t_mark > 3.0:
            assert self._tower is not None
            self._air_vis = self._visible_count(self._tower)
            print("\n========== ② OL(飞行)站塔上 ==========", flush=True)
            print(f"塔周围半径 {_MEASURE_R} 内可见格 = {self._air_vis}", flush=True)
            # 再放一只地面单位(小狗)到同一座塔
            await self.client.debug_create_unit([[UnitTypeId.ZERGLING, 1, self._tower, 1]])
            self._phase = 2
            self._t_mark = self.time
            return

        if self._phase == 2 and self.time - self._t_mark > 4.0:
            assert self._tower is not None
            self._ground_vis = self._visible_count(self._tower)
            print("\n========== ③ 再加一只小狗(地面)站塔上 ==========", flush=True)
            print(f"塔周围半径 {_MEASURE_R} 内可见格 = {self._ground_vis}", flush=True)
            gain = self._ground_vis - self._air_vis
            print(
                f"\n【判定】地面上塔后多看到 {gain} 格 → "
                + (
                    "塔只认地面单位,OL 飞上去拿不到塔视野"
                    if gain > 200
                    else "地面/飞行差别不大,OL 站塔上也能拿到塔视野"
                ),
                flush=True,
            )
            # ④ 这座塔的视野对坑道虫落点有没有用
            edges, _h = enemy_plateau_edges(self, enemy)
            snapped = [Point2((int(p.x) + 0.5, int(p.y) + 0.5)) for p in edges]
            vis_edges = [p for p in snapped if self.is_visible(p)]
            fits = await self.can_place(UnitTypeId.NYDUSCANAL, vis_edges) if vis_edges else []
            d_tower = self._tower.distance_to(enemy)
            print("\n========== ④ 该塔视野对落点的价值 ==========", flush=True)
            print(
                f"塔离敌方主基 {d_tower:.1f} 格;此刻敌方高地边缘格可见 {len(vis_edges)}/{len(snapped)} 个,"
                f"其中 can_place 通过 {sum(fits)} 个",
                flush=True,
            )
            if vis_edges:
                nearest = min(vis_edges, key=lambda p: self._tower.distance_to(p))  # type: ignore[union-attr]
                print(
                    f"离塔最近的可见边缘格 ({nearest.x:.1f},{nearest.y:.1f}) 距塔 "
                    f"{self._tower.distance_to(nearest):.1f} 格",
                    flush=True,
                )
            await self.client.leave()

        if iteration > 2000:
            await self.client.leave()


def main() -> None:
    ap = argparse.ArgumentParser(description="瞭望塔供视野探针")
    ap.add_argument("--map", default=MAP_NAME)
    ap.add_argument("--seed", type=int, default=20260726)
    args = ap.parse_args()
    print("=== 瞭望塔(小高塔)能不能给坑道虫供视野 ===", flush=True)
    run_game(
        maps.get(args.map),
        [
            Bot(Race.Zerg, WatchtowerProbe(), name="WTProbe"),
            Computer(Race.Terran, Difficulty.VeryEasy),
        ],
        realtime=False,
        random_seed=args.seed,
    )


if __name__ == "__main__":
    main()
