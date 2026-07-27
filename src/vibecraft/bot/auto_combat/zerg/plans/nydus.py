"""虫族尼德斯偷袭 plan —— Fast Roach ZvT (中等激进变体)。

参考 build order（Spawning Tool #93631 ZvT Fast Nydus Queen Roach）:
  14 BS / 15 二矿(BH)
  Lair  ~2:27 / VN(NydusNetwork) ~3:27
  Nydus Canal 插入敌方家 ~4:12（Overlord 提供视野）
  首攻 ~4:27：6-8 蟑螂 + 2 女王 + 速狗

核心偷袭流程：
  BS(母池) → 狗速 + 女王 → Lair(二本) → BR(蟑螂窝) + VN(尼德斯网络)
  → Overlord 预飞敌方主基地视野 → BuildNydusCanalAtEnemy 对该坐标下
    BUILD_NYDUSWORM 命令 → 14s 钻出 → 主力从本方 Network 装载 → 全出

建筑 hotkey（注释用）：
  BH=孵化场(Hatchery)  BS=母池(SpawningPool)  BE=气矿(Extractor)
  BR=蟑螂窝(RoachWarren)  VN=尼德斯网络(NydusNetwork)

Nydus Canal 视野机制：
  - 不需要农民或地面单位在敌方家，Overlord 飞到目标位置即可提供视野
  - NydusNetwork.BUILD_NYDUSWORM(pos) 要求 pos 处 is_visible=True
  - Canal 落地后 14s 钻出，钻出期间有 6 甲护体（HP 仍脆，选死角）
  - Railgan 版本：build 第一步把 Overlord 飞向敌方主基地
  - Serral 版本：Nydus Network 开建时同步 Overlord → Overseer

References:
  - Spawning Tool #93631 (ZvT Fast Nydus Queen Roach)
    https://lotv.spawningtool.com/build/93631/
  - Spawning Tool #98762 (Railgan ZvP 1-Base Speedling Nydus)
    https://lotv.spawningtool.com/build/98762/
  - Spawning Tool #140822 (Serral Queen Roach Nydus ZvP)
    https://lotv.spawningtool.com/build/140822/
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sharpy.knowledges import KnowledgeBot
from sharpy.managers.core.roles import UnitTask
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import (
    ActUnit,
    BuildGas,
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.act_base import ActBase
from sharpy.plans.acts.zerg import AutoOverLord, MorphLair, ZergUnit
from sharpy.plans.require import RequireCustom, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.zerg.plans.feint_squad_act import FeintSquadAct
from vibecraft.bot.auto_combat.zerg.plans.nydus_landing_planner import (
    NydusLandingPlanner,
    enemy_main_center,
    enemy_plateau_edges,
    landing_spots_3x3,
    overlord_station_points,
    small_plateau_perches,
)
from vibecraft.bot.auto_combat.zerg.plans.nydus_raid_act import NydusRaidAct
from vibecraft.bot.auto_combat.zerg.plans.spare_queen_act import SpareQueenAct
from vibecraft.bot.drop_path import plan_avoid_path
from vibecraft.bot.unit_kind import is_army as _is_army
from vibecraft.bot.unit_kind import is_worker as _is_worker

logger = logging.getLogger(__name__)

# Overlord 认定为"在敌方主基地附近"的最大距离（地图单位）
_OVERLORD_NEAR_ENEMY_D: float = 30.0
# 每次尝试 BUILD_NYDUSWORM 之间的最小冷却（秒），防止每帧疯狂下令
_BUILD_WORM_COOLDOWN_S: float = 2.0
# 最多同时挖洞的 Canal 数量（1 条就够，炸了再补）
_MAX_CANALS: int = 1
# 虫被拆后的重投防抖底线。**只是防抖,不是"等窗口"的门**——2026-07-26 用户真机反馈:
# 设 40s 时,佯攻队一被拆就出去引主力、窗口常在 T+5~T+25 就开了,却被定时器压到 T+40 才准下,
# 等冷却完主力已回防、窗口关死 →「狗去引了但虫没跟着下,没配合上」。
# 教训:**定时器会否决佯攻自己创造的那个窗口**。真正的重投判据是 ④ 号门(主力已不在落点区),
# 它本身就等价于"佯攻把人引走了",所以窗口一开就下 = 用户要的"引到的同时下虫"。
# 这里只留几秒防抖:等残骸消失 + 拉黑生效,免得同一帧原地重下。
_RETRY_COOLDOWN_AFTER_LOST_S: float = 8.0
_CANAL_COST_MINERALS: int = 75  # 坑道虫造价(预留用)
_CANAL_COST_GAS: int = 75
# 落点选择/窗口/blacklist 参数已随 2026-07-12 P1 重构移入 NydusLandingPlanner（纯挑点模块）。


class _SetWorkerCap(ActBase):  # type: ignore[misc]
    """声明本 build 是狗蟑快攻、农民封顶 —— 让 WorkerSaturationFloorAct 别把它铺到饱和、
    抢走该出兵的 larva/矿（2026-07-10 用户：本质是狗蟑快攻，别硬塞农民饱和）。
    Floor 读 knowledge.vibecraft.worker_cap_override；快攻打完/转运营会自动解封顶（见 Floor）。
    """

    def __init__(self, cap: int, hard: bool = False) -> None:
        super().__init__()
        self._cap = cap
        # hard=True:封顶在 sustain fallback 解封后仍生效(all-in 攒兵窗口保 larva),
        # 由 build 在 all-in 投送出去(canal 落地)后手动清 vib.worker_cap_hard=False。
        self._hard = hard

    async def execute(self) -> bool:
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if vib is not None:
                vib.worker_cap_override = self._cap
                # 农民封顶保持到**三矿建起**才解封(2026-07-13 用户:canal 后别补农民饱和抢续兵的钱)。
                # canal 落地后是续兵窗口(矿给多波蟑螂/狗),农民维持 20 不涨;拖到开三矿(转运营,time>450)
                # 才解封顶让农民饱和。(旧逻辑 canal 一落就解封 → 农民饱和抢续兵钱,被用户点破。)
                if self._hard:
                    try:
                        has_third = int(self.ai.townhalls.amount) >= 3
                    except Exception:
                        has_third = False
                    vib.worker_cap_hard = not has_third
        return True  # 不阻塞


class _SendOverlordToEnemy(ActBase):  # type: ignore[misc]
    """持续保障敌方主基地视野的 Overlord/Overseer 侦查（Round 2 精修：从"发一次"
    改成"常驻保活"）。

    Round 1 真局教训（2026-07-09 nydus_selftest telemetry 复盘，见
    坑道骚扰 Round 4 精修）：老版本只在 Lair 完成那刻
    派**一只** Overlord 飞过去、`_sent=True` 后再也不管——telemetry 显示那只 OL 在
    t≈145s（刚到位不久）就被敌方防御打死，此后**全程 0 只 OL/Overseer 在敌方附近**。
    `_BuildNydusCanalAtEnemy` 后续 170 次 `BUILD_NYDUSWORM` 全部对着一个**已失去
    视野**的死锁定点空放（SC2 目标不可见会静默拒绝该指令，不报错、不抛异常，只是
    什么都不发生）——直到游戏快结束、前门部队顺路带来视野才侥幸成功一次，但那时
    仗已经打完了。**视野持续 ≠ 发一次命令**，必须每帧巡查"敌方附近现在有没有活的
    OL/Overseer"，没有就立刻补一只，全程保活（同 nydus.py 里 `_BuildNydusCanalAtEnemy`
    的视野二次确认互为兜底）。

    保留至少 1 只 Overlord 在家（避免自己作死断供应）。
    """

    # 侦查 OL 驻守/撤离参数(2026-07-12 用户强怒纠正 + 逻辑链「隐蔽第一」:OL 只在【悬崖外低地】远远
    # 漂浮被动供视野,**一旦对落点有视野就停住绝不再往里冲**,躲开【所有】敌方单位(含农民——被农民看见
    # 一样暴露→主力回防→窗口关死+canal被打)。绝不无脑冲进兵堆送死)
    # 侦查 OL 数:2026-07-13 用户"每次只补一个没冗余"→ 2 只(1 只死了另一只还在供视野,不断供)。
    # 2 只去**两个最隐蔽的不同扇区**(_concealed_floats_sorted),覆盖 + 冗余。
    _SCOUT_COUNT: int = 2
    _SCOUT_HOME_RESERVE: int = 1  # 至少留几只 OL 在家(别倾巢断供应)
    # 隐蔽/避险半径:敌方**战斗单位**进到此(≈女王防空射程7+余量)→ 撤。2026-07-12 真机:设 11(=视野)
    # 时 OL 想看边缘就得进 11 格、一进就因边缘有主力而 flee → 永远给不上视野、落地掉到 33%。降到 7 =
    # 只躲【进射程的近敌】,OL 能站 9 格外(push=9)偷看防守边缘不挨打(女王 7 格够不到 9),给上视野。
    _SCOUT_STEALTH_RADIUS: float = 7.0
    _SCOUT_FLEE_BACK: float = 9.0  # 暴露时顺同方向(远离敌方中心)再往低地外推这么远
    _SCOUT_VISION_HOLD_D: float = 10.0  # OL 离某可放边缘格 ≤ 此且该格可见 → 已够视野 → 停住不再冲
    # 接近路径避障半径:2026-07-12 真机根因——旧默认 13 < 敌方主基高地半径(~15)→ 掠过基地的直线不
    # 触发绕行 → OL 直穿基地中间被发现送死(用户强怒"从中间穿过去,隐蔽第一呢")。调到 22(> 基地半径
    # + 余量)→ 路径绕到基地侧面、从悬崖边逼近落点,绝不穿中间。
    _SCOUT_AVOID_R: float = 22.0
    _SCOUT_FALLBACK_STANDOFF: float = 12.0  # 地形算不出兜底:朝地图中心反方向(基地背面低地)退这么远

    def __init__(self) -> None:
        super().__init__()
        self._scout_tags: set[int] = set()
        # D1/D3:OL 漂浮点纯静态几何(高地边缘外推到低地),一次算好锁住幂等复用(#543,别每帧重扫地形)。
        self._float_cache: list[Point2] | None = None
        # 高地边缘可放格(判 OL 是否已对落点有视野用),一次算好缓存。
        self._edge_cache: list[Point2] | None = None
        # 贴边接近路径:每只 OL 到其漂浮点的 plan_avoid_path(绕敌方主基),一次锁定缓存(#543)。
        self._path_cache: dict[int, list[Point2]] = {}
        self._path_idx: dict[int, int] = {}  # 每只 OL 沿路径推进到第几个拐点(顺序,不回头)
        self._path_at: dict[int, float] = {}  # 每只 OL 路径上次重算时刻(每 ~5s 适配新看到的视野源)
        self._scout_float: dict[int, Point2] = {}  # 每只 OL 选定的隐蔽漂浮点(锁定,不每帧换)

    def _vision_sources(self, center: Point2) -> list[Point2]:
        """敌方【视野源】位置(用户 2026-07-13「绕开所有可能有视野的对方单位/建筑」):主基 + 几何已知
        二矿/三矿 + 已侦察建筑 + 成团军队质心。只取**敌方家一簇**(离主基 ≤35),远处散兵/游军交给
        flee 兜(不为它绕大弯)。"""
        srcs: list[Point2] = [center]
        zm = getattr(self, "zone_manager", None)
        with contextlib.suppress(Exception):
            if zm is not None:
                start = (
                    getattr(zm, "enemy_start_location", None) or self.ai.enemy_start_locations[0]
                )
                zones = sorted(
                    zm.expansion_zones, key=lambda z: z.center_location.distance_to(start)
                )
                for z in zones[1:3]:  # 二矿 + 三矿(几何已知,VeryHard 常占)
                    srcs.append(z.center_location)
        with contextlib.suppress(Exception):
            for s in self.ai.enemy_structures:
                srcs.append(s.position)
        with contextlib.suppress(Exception):
            combat = [e for e in self.ai.enemy_units if _is_army(e)]
            if len(combat) >= 3:
                cx = sum(e.position.x for e in combat) / len(combat)
                cy = sum(e.position.y for e in combat) / len(combat)
                srcs.append(Point2((cx, cy)))
        return [s for s in srcs if s.distance_to(center) <= 35.0]

    def _concealed_floats_sorted(
        self, floats: list[Point2], sources: list[Point2], map_center: Point2
    ) -> list[Point2]:
        """漂浮点按【隐蔽度】从高到低排序(用户 2026-07-13「离敌方视野源最远」):离敌方【其他视野源】
        (除主基外的二矿/军队)最远的排前面;没有其他源时按离地图中心最远(最贴地图边)排。多只 OL 各取
        一个不同的隐蔽扇区(冗余 + 覆盖不同弧)。"""
        if len(floats) <= 1:
            return list(floats)
        others = sources[1:] if len(sources) > 1 else []
        if others:
            return sorted(floats, key=lambda fp: -min(fp.distance_to(c) for c in others))
        return sorted(floats, key=lambda fp: -fp.distance_to(map_center))

    @staticmethod
    def _path_dmin(path: list[Point2], center: Point2) -> float:
        """路径全程离 center 最近距离(采样);<15=擦/穿基地。"""
        dmin = 999.0
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            d = a.distance_to(b)
            for s in range(11):
                p = a.towards(b, d * s / 10.0)
                dmin = min(dmin, p.distance_to(center))
        return dmin

    def _select_clean_float(
        self,
        start: Point2,
        sorted_floats: list[Point2],
        path_avoid: list[Point2],
        playable: Any,
        center: Point2,
        taken: set,
    ) -> tuple[Point2, list[Point2]]:
        """从隐蔽度排序的落点里,挑**第一个路径不穿基地(dCenter≥15)**的(用户 2026-07-13:某些远侧
        隐蔽落点去它必须穿基地被打)。都穿基地则退回最隐蔽那个(尽力)。返回 (落点, 路径),两只 OL 不重叠。"""
        fallback: tuple[Point2, list[Point2]] | None = None
        for fp in sorted_floats:
            key = (round(fp.x), round(fp.y))
            if key in taken:
                continue
            path = None
            with contextlib.suppress(Exception):
                path = plan_avoid_path(start, fp, path_avoid, playable, r_avoid=self._SCOUT_AVOID_R)
            if not path:
                path = [start, fp]
            if fallback is None:
                fallback = (fp, path)
            if self._path_dmin(path, center) >= 15.0:  # 路径不穿基地 → 选它
                return fp, path
        return fallback if fallback is not None else (sorted_floats[0], [start, sorted_floats[0]])

    def _float_points(self, center: Point2) -> list[Point2]:
        """驻守点,缓存复用;地形取不到 → []（上层兜底基地背面）。

        2026-07-26 用户 D102:改用 `overlord_station_points` —— **小高台优先**(高地遮蔽低地视野,
        停那儿更难被发现)、够不着才退回 D1 的"高地边缘外推 9 格到低地"。两类都纯静态几何,开局
        即可算(I7),所以第一只 OL 一出生就能定点出发。
        """
        if self._float_cache is not None:
            return self._float_cache
        fp = overlord_station_points(self.ai, center)
        if fp:
            self._float_cache = fp
            # 记一条:这局到底有没有小高台可站、各类站位几个。事后只看 OLPATH 的坐标分不出
            # 是高台还是外推点,真机复盘会卡在这（诊断信号要在当场就打出来）。
            spots = landing_spots_3x3(self.ai, center)
            n_perch = len(small_plateau_perches(self.ai, center, spots))
            logger.info(
                "OLSTATION 站位候选=%d (小高台=%d 外推低地=%d) 可落格=%d",
                len(fp),
                n_perch,
                len(fp) - n_perch,
                len(spots),
            )
        return fp

    def _edge_tiles(self, center: Point2) -> list[Point2]:
        """高地边缘可放格(判 OL 是否已对落点有视野用),一次算好缓存。"""
        if self._edge_cache is not None:
            return self._edge_cache
        edges, _ = enemy_plateau_edges(self.ai, center)
        if edges:
            self._edge_cache = edges
        return edges

    def _has_vision_on_landing(self, scout: Any, center: Point2) -> bool:
        """OL 是否已对某可放边缘格有视野(离它 ≤ _SCOUT_VISION_HOLD_D 且该格此刻 is_visible)。
        有 → OL 的活已完成,该停住别再往里冲(用户「明明有视野了还往里冲被发现」)。"""
        edges = self._edge_tiles(center)
        if not edges:
            return False
        with contextlib.suppress(Exception):
            near = min(edges, key=lambda t: scout.position.distance_to(t))
            if scout.position.distance_to(near) <= self._SCOUT_VISION_HOLD_D and self.ai.is_visible(
                near
            ):
                return True
        return False

    def _exposed(self, scout: Any) -> bool:
        """OL 是否将暴露/被打:①敌方**战斗单位**(主力,能打能看)进隐蔽半径 → 撤(躲主力,别被引回来
        的大部队打死);②敌方**农民**只在**贴脸**(≤6)才撤(一个农民凑到 OL 脸上才算暴露,别被矿线上
        十几个农民逼得永远稳不住视野落不了 canal——真机实测躲所有农民会把落地率打回 1/4)。
        用户「隐蔽第一、看见兵躲呀」——主要是躲主力兵。"""
        with contextlib.suppress(Exception):
            for e in self.ai.enemy_units:
                if getattr(e, "is_structure", False):
                    continue
                d = scout.distance_to(e)
                if _is_worker(e):
                    if d <= 5.0:  # 农民贴脸才躲
                        return True
                elif d <= self._SCOUT_STEALTH_RADIUS:  # 战斗单位进射程+余量(7)才躲
                    return True
        return False

    async def execute(self) -> bool:
        # D1/D3(2026-07-12 推理图谱决策):OL 的活是"在【悬崖外低地】远远漂浮被动供视野、看住高地边缘
        # 落点",不是进基地被发现。老逻辑 park=矿后死角(基地里/高地上,会被农民看见→主力回防→窗口关死)。
        # 改成漂浮点 = 高地边缘可放格顺悬崖外推 ~10 格(=sight-1)停在低地(J3),按扇区分散(D3)多只驻守
        # 冗余;受威胁只【顺同方向往低地外】再撤,绝不撤回我家前沿。漂浮点纯静态地形算(J7,不需视野)。
        try:
            enemy_pos: Point2 = self.ai.enemy_start_locations[0]
            center = enemy_main_center(self.ai, getattr(self, "zone_manager", None))
            floats = self._float_points(center)
            if not floats:
                # 地形栅格算不出 → 朝地图中心反方向退(基地背面低地方向),绝不进基地
                map_center = self.ai.game_info.map_center
                floats = [enemy_pos.towards(map_center, -self._SCOUT_FALLBACK_STANDOFF)]

            overlords = list(self.cache.own(UnitTypeId.OVERLORD))
            overseers = list(self.cache.own(UnitTypeId.OVERSEER))
            alive = {u.tag for u in overlords + overseers}
            self._scout_tags &= alive  # 清死掉的侦查 OL
            self._path_cache = {t: p for t, p in self._path_cache.items() if t in alive}
            self._path_idx = {t: v for t, v in self._path_idx.items() if t in alive}
            self._path_at = {t: v for t, v in self._path_at.items() if t in alive}
            self._scout_float = {t: v for t, v in self._scout_float.items() if t in alive}
            now_t = float(self.ai.time)
            map_center = self.ai.game_info.map_center
            # 视野源列表:用于**选隐蔽落点**(离二矿/军队最远那侧偷看)。避障只绕**主基**(r_avoid=22)——
            # 2026-07-13 真机:把所有源聚成大包围圈会把「悬崖外低地落点(离中心才 24,概念上隐蔽)」误挡
            # 在圈内(R=27>24)→ OL 停 33 格外够不到视野 → 0/4 落地。落点隐蔽靠"选最远那侧"实现,不靠圈住。
            vsources = self._vision_sources(center)
            # 落点按隐蔽度排序:2 只 OL 各取一个不同的最隐蔽扇区(冗余 + 覆盖)。
            sorted_floats = self._concealed_floats_sorted(floats, vsources, map_center)
            # 路线避障 = 主基 + 【野外敌军团】(用户 2026-07-13"路线要躲对方兵"):敌军团在离主基 >25 的
            # 野外时单独当避障中心(基地内的已被主基圈覆盖;只加野外的,近距会 zigzag)。
            path_avoid = [center]
            with contextlib.suppress(Exception):
                _combat = [e for e in self.ai.enemy_units if _is_army(e)]
                if len(_combat) >= 4:
                    _ac = Point2(
                        (
                            sum(e.position.x for e in _combat) / len(_combat),
                            sum(e.position.y for e in _combat) / len(_combat),
                        )
                    )
                    if _ac.distance_to(center) > 25.0:
                        path_avoid.append(_ac)

            # 目标侦查数:_SCOUT_COUNT,但留 >=_SCOUT_HOME_RESERVE 只 OL 在家。
            # 2026-07-26 用户"开局第一个 OL 就提前过去":只有 1 只 OL 时也要派它走(下限 1)。
            # 供应只看 OL 死没死、不看它停哪,所以"留家"不是供应需要;而开局(敌方还没对空)恰是
            # 送 OL 过去最安全的时间窗,晚了反而飞不进去。
            target = min(self._SCOUT_COUNT, max(1, len(overlords) - self._SCOUT_HOME_RESERVE))
            if len(self._scout_tags) < target:
                free = [u for u in overlords if u.tag not in self._scout_tags]
                free.sort(key=lambda u: u.distance_to(enemy_pos), reverse=True)
                for u in free[: target - len(self._scout_tags)]:
                    self._scout_tags.add(u.tag)
                    logger.info("NydusRush: 指定侦查 OL(tag=%d) 漂浮悬崖外低地供视野", u.tag)
            elif len(self._scout_tags) > target:
                self._scout_tags = set(sorted(self._scout_tags)[:target])

            playable = self.ai.game_info.playable_area
            by_tag = {u.tag: u for u in overlords + overseers}
            scouts = sorted(self._scout_tags)
            # 已被占用的落点(2 只 OL 不选同一个);从已缓存的 scout 落点收集。
            taken_floats = {(round(p.x), round(p.y)) for p in self._scout_float.values()}
            for tag in scouts:
                scout = by_tag.get(tag)
                if scout is None:
                    continue
                # Reserve 独占:侦查 OL 归本 act 管(2026-07-12 真机根因:未 Reserve → PlanZoneGather
                # 每帧把它当 idle 拉回家、本 act 又派出 → 拉扯震荡 + 反复穿危险区送死)。
                with contextlib.suppress(Exception):
                    self.knowledge.roles.set_task(UnitTask.Reserved, scout)
                # 每只 OL 选一个【路径不穿基地的最隐蔽】落点(用户 2026-07-13:远侧隐蔽落点去它得穿基地
                # 被打;改成选路径 dCenter≥15 的隐蔽落点)。首次选定同时算好路径,锁定不每帧换(#543)。
                fp = self._scout_float.get(tag)
                if fp is None:
                    fp, _sel_path = self._select_clean_float(
                        scout.position, sorted_floats, path_avoid, playable, center, taken_floats
                    )
                    self._scout_float[tag] = fp
                    self._path_cache[tag] = _sel_path
                    self._path_at[tag] = now_t
                    self._path_idx[tag] = 1
                    taken_floats.add((round(fp.x), round(fp.y)))
                # ── 逻辑链「隐蔽第一」三态决策(用户 2026-07-12 强怒拍板)──────────────
                # ① 暴露(任何敌方单位含农民进隐蔽半径)→ 顺同方向往低地外撤,**绝不硬凑/无脑冲**
                #    (被发现=引主力回防打 canal,比挨打更致命)。撤远一点保命 + 保持隐蔽。
                if self._exposed(scout):
                    scout.move(fp.towards(center, -self._SCOUT_FLEE_BACK))
                    continue
                # ② 已对某可放落点有视野 → **停住**(hold 当前位置),别再往里冲被发现
                #    (用户「明明有视野了还往里冲结果被敌人看到」的直接修复)。
                if self._has_vision_on_landing(scout, center):
                    scout.move(scout.position)  # 幂等停在原地(不漂移)
                    continue
                # ③ 还没视野且暂时安全 → 绕开**所有视野源**接近(用户 2026-07-13):plan_avoid_path 避开
                #    主基 + 二矿/三矿 + 敌军团 + 已知建筑(avoid_centers),r_avoid=_SCOUT_AVOID_R(>基地
                #    半径,绝不穿中间),拐点推向离高地边缘最远那侧。路径每 ~5s 重算(适配逐渐看到的新视野
                #    源;非每帧→不漂移),沿拐点顺序推进不回头。
                path = self._path_cache.get(tag)
                if path is None or now_t - self._path_at.get(tag, -999.0) >= 5.0:
                    new_path = None
                    with contextlib.suppress(Exception):
                        # 绕主基 + 野外敌军团(path_avoid);r_avoid=22>基地半径(落点在 24 处圈外可达);
                        # 离其他视野源最远那侧接近。
                        new_path = plan_avoid_path(
                            scout.position, fp, path_avoid, playable, r_avoid=self._SCOUT_AVOID_R
                        )
                    path = new_path or path or [fp]
                    self._path_cache[tag] = path
                    self._path_at[tag] = now_t
                    self._path_idx[tag] = 1  # 重算后从新路径起点跟随
                    # 诊断(2026-07-13 用户"第一个OL还是直穿基地"):记路径拐点 + 全程离敌方中心最近距离
                    # (<15=擦/穿基地)。看避障到底触发没。
                    with contextlib.suppress(Exception):
                        _dmin = 999.0
                        for _i in range(len(path) - 1):
                            _a, _b = path[_i], path[_i + 1]
                            for _s in range(11):
                                _p = _a.towards(_b, _a.distance_to(_b) * _s / 10.0)
                                _dmin = min(_dmin, _p.distance_to(center))
                        logger.info(
                            "OLPATH tag=%d pts=%d fp=(%.0f,%.0f) dCenter_min=%.0f avoid=%d 拐点=%s",
                            tag,
                            len(path),
                            fp.x,
                            fp.y,
                            _dmin,
                            len(path_avoid),
                            [(round(p.x), round(p.y)) for p in path[:5]],
                        )
                idx = min(self._path_idx.get(tag, 1), len(path) - 1)
                while idx < len(path) - 1 and scout.distance_to(path[idx]) <= 3.0:
                    idx += 1
                self._path_idx[tag] = idx
                wp = path[idx]
                scout.move(wp)
        except Exception as exc:
            logger.warning("_SendOverlordToEnemy.execute failed: %s", exc)
        return True  # 不阻塞，后续 step 继续


class _ReserveCanalCost(ActBase):  # type: ignore[misc]
    """每帧**最先**预留坑道虫的 75/75，直到虫真的落地（2026-07-26 用户真机反馈）。

    为什么必须单独一个 act 放在最前面:sharpy 的 `knowledge.reserve()` 记的是**本帧**的预留额,
    `knowledge.update()` 在每帧开头把它清零。原来预留写在 `_BuildNydusCanalAtEnemy` 里,而那个 act
    在计划里排**在女王/蟑螂/狗的生产之后** —— 等它跑到时,本帧的钱早被前面的生产 act 按"没有任何
    预留"的账面花掉了。玩家看到的就是"坑道虫好了,钱还是经常掉到 75/75 以下"。

    生产 act(sharpy `ActUnit`)用的是 `knowledge.can_afford`(会扣除预留),所以**只要预留发生在它们
    之前**就管用。本 act 挂在 BuildOrder 最外层第一批,每帧最先跑。
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_log: float = -999.0

    async def execute(self) -> bool:
        try:
            if not self.cache.own(UnitTypeId.NYDUSNETWORK).ready:
                return True  # 网络没好,不占钱
            enemy_pos: Point2 = self.ai.enemy_start_locations[0]
            canals = [
                c
                for c in self.cache.own(UnitTypeId.NYDUSCANAL)
                if c.distance_to(enemy_pos) < _OVERLORD_NEAR_ENEMY_D * 2
            ]
            if len(canals) >= _MAX_CANALS:
                return True  # 虫已经立住,不用再攥着
            self.knowledge.reserve(_CANAL_COST_MINERALS, _CANAL_COST_GAS)
            now = float(self.ai.time)
            if now - self._last_log >= 15.0:
                self._last_log = now
                logger.info(
                    "NydusReserve: 攥住 %d/%d 给坑道虫(m=%.0f g=%.0f)",
                    _CANAL_COST_MINERALS,
                    _CANAL_COST_GAS,
                    self.ai.minerals,
                    self.ai.vespene,
                )
        except Exception as exc:
            logger.warning("_ReserveCanalCost.execute failed: %s", exc)
        return True  # non-blocking


class _BuildNydusCanalAtEnemy(ActBase):  # type: ignore[misc]
    """在敌方主基地附近建 Nydus Canal（虫洞出口）——2026-07-12 P1 精简为薄 act。

    落点选择/门控/blacklist/lock 全委托 `NydusLandingPlanner`（纯挑点模块）。本 act 只管：
      1. 已有 Canal 在敌方家 → 完成 return True（被拆则通知 planner 拉黑换点）
      2. NydusNetwork 没好 / army 没装进家网络 → 等（return False，不阻塞并行 act）
      3. planner 给出可落点（门 = 有视野 ∧ 主力不在落点区）+ 能买单 → 发 BUILD_NYDUSWORM

    下 canal 的门（用户 2026-07-12 定死，见设计文档 §三·五）：**最本质 = 敌方主力不在落点区**。
    主力在家即使有视野也别强下（14s 钻出必被秒）；主力不在哪怕佯攻没到位也下。佯攻不是门、是
    "主力在家时才用的引离手段"。wave_intent 读 `attack_mode_override`（all_in=容忍残敌硬下）。

    Nydus Canal 机制（LotV）：75 矿 75 气，NydusNetwork 下 `BUILD_NYDUSWORM`（目标坐标，须 is_visible），
    落地 14s 钻出（6 甲护体），钻出后从本方任意 NydusNetwork 装载。
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_build_attempt: float = -999.0
        self._planner = NydusLandingPlanner()
        # 曾有 canal 标记（供"被秒→通知 planner 拉黑换点"判定）
        self._had_canal: bool = False
        # 第 N 次投放（2026-07-26 用户"第一波虫被打掉,过一会再来第二次,但要配合佯攻吸引火力"）：
        # 被拆计数 + 下一次可尝试的时刻。冷却期不重下——让佯攻队重新把主力引开、新一波兵攒起来。
        self._canal_lost_n: int = 0
        self._retry_ready_at: float = -1.0

    def _count_enemy_side_canals(self) -> int:
        """敌方主基地附近 _OVERLORD_NEAR_ENEMY_D*2 范围内的 Canal 数（含建造中）。"""
        try:
            enemy_pos: Point2 = self.ai.enemy_start_locations[0]
            canals = self.cache.own(UnitTypeId.NYDUSCANAL)
            count = 0
            for c in canals:
                if c.distance_to(enemy_pos) < _OVERLORD_NEAR_ENEMY_D * 2:
                    count += 1
            return count
        except Exception:
            return 0

    def _read_wave_all_in(self) -> bool:
        """wave_intent = 玩家 attack_mode_override（all_in→COMMIT 硬下 / probe|None→PROBE）。

        玩家喊"放坑道虫"(facade 置的 `nydus_force_drop_until`)在时限内同样按 COMMIT 处理——
        玩家明确要求现在下,就别再用保守阈值等完美窗口。
        """
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if getattr(vib, "attack_mode_override", None) == "all_in":
                return True
            if self._player_force_drop():
                return True
        return False

    def _player_force_drop(self) -> bool:
        """玩家是否在时限内明确要求"现在放坑道虫"。"""
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            until = float(getattr(vib, "nydus_force_drop_until", 0.0) or 0.0)
            return float(self.ai.time) < until
        return False

    async def execute(self) -> bool:
        # 1. 已有够多 Canal → 完成（记住曾有过，供被秒判定）
        if self._count_enemy_side_canals() >= _MAX_CANALS:
            self._had_canal = True
            return True

        now0 = float(self.ai.time)
        # 1b. 曾有 canal、现在没了 → 被秒：拉黑该点 + 重选（声东击西换点）+ **进入重投冷却**。
        #     2026-07-26 用户:"第一波没放成功被打掉的话,过一会还要再第二次,但需要配合佯攻部队
        #     吸引火力"。原来 2s 就重下、且 25s 后还会绕过"主力不在"硬落 → 第二个虫直接落回刚
        #     把第一个拆掉的那堆兵里,再被秒一次。改成:冷却 _RETRY_COOLDOWN_AFTER_LOST_S 秒不重下,
        #     期间把 `nydus_retry_pending` 发布给佯攻队(它据此继续/加紧引主力),冷却完再按正常门重投。
        if self._had_canal:
            self._had_canal = False
            self._planner.notify_canal_lost(now0)
            self._canal_lost_n += 1
            self._retry_ready_at = now0 + _RETRY_COOLDOWN_AFTER_LOST_S
            logger.warning(
                "NydusLanding: canal lost (第%d次) → 拉黑该点,防抖 %.0fs 后随时重投"
                "(等佯攻把主力引开的窗口,不再靠定时器)",
                self._canal_lost_n,
                _RETRY_COOLDOWN_AFTER_LOST_S,
            )

        # 1c. 重投期:虫被拆过、现在场上又没有 → 整段都告诉佯攻队"继续吸引火力",直到新虫真落地。
        #     (原来只在冷却那几十秒里发布,冷却一过就撤掉 → 佯攻和投放各干各的,配合不上。)
        retrying = self._canal_lost_n > 0
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if vib is not None:
                vib.nydus_retry_pending = retrying
        # 防抖窗内不重下;之后每帧都试,由 ④ 号门(主力不在落点区)决定何时真下 ——
        # 那一刻正是佯攻把主力引走的时刻,即用户要的"引到的同时下虫"。
        force = self._player_force_drop()
        if now0 < self._retry_ready_at and not force:
            if now0 - getattr(self, "_last_blk", -999.0) >= 3.0:
                self._last_blk = now0
                logger.info("NydusLanding: 重投防抖中(还剩 %.1fs)", self._retry_ready_at - now0)
            return False

        # 2. NydusNetwork 没建好 → 等
        networks = self.cache.own(UnitTypeId.NYDUSNETWORK).ready
        if not networks:
            return False

        # 2a. 预留 75/75 已移到 `_ReserveCanalCost`(BuildOrder 最外层、每帧最先跑)——
        #     在这里预留没用:本 act 排在生产 act 之后,而 reserve 是本帧账、每帧清零。
        # 2b. army 还没装进自家坑道网络 → 先不建虫子（2026-07-10 用户：军队先钻进家里网络等着，
        #     虫子 morph 的 14s 里军队已在管道，一钻出瞬间弹出；且虫子绝不在军队没装好前空 morph 被秒）。
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if vib is not None and not getattr(vib, "nydus_wave_loaded", False):
                _n = float(self.ai.time)
                if _n - getattr(self, "_last_blk", -999.0) >= 3.0:
                    self._last_blk = _n
                    logger.info(
                        "NydusLanding BLOCKED: army 未装进坑道网络(nydus_wave_loaded=False)"
                    )
                return False

        # 3. 冷却中 → 不重复下令
        now = self.ai.time
        if now - self._last_build_attempt < _BUILD_WORM_COOLDOWN_S:
            return False

        # 4. 委托 planner 挑点（门：② 有视野 ∧ ④ 主力不在落点区；命中即锁坐标快照，幂等复用）
        wave_all_in = self._read_wave_all_in()
        scout_units = list(self.cache.own(UnitTypeId.OVERLORD)) + list(
            self.cache.own(UnitTypeId.OVERSEER)
        )
        worm_pos = await self._planner.pick_available_now(
            self.ai,
            getattr(self, "zone_manager", None),
            scout_units=scout_units,
            wave_all_in=wave_all_in,
            # 第一次可以"等不到窗口就硬落"(有个虫总比没有强);**被拆过之后不许再硬落** ——
            # 硬落等于把第二个虫扔回刚拆掉第一个的那堆兵里。重投必须等真窗口(佯攻把主力引开)。
            allow_gate_bypass=self._canal_lost_n == 0 or force,
            ignore_blacklist=force,
        )
        if worm_pos is None:
            # 主力在家 / 无可见可放点 → 等（佯攻继续引 / 主力自己走）
            _n = float(self.ai.time)
            if _n - getattr(self, "_last_blk", -999.0) >= 3.0:
                self._last_blk = _n
                logger.info(
                    "NydusLanding BLOCKED: 无可落点(主力在落点区 或 无可见可放格;OL=%d)",
                    len(scout_units),
                )
            return False

        # 5. 买单检查（75 矿 75 气）
        if not self.ai.can_afford(UnitTypeId.NYDUSCANAL):
            _n = float(self.ai.time)
            if _n - getattr(self, "_last_blk", -999.0) >= 3.0:
                self._last_blk = _n
                logger.info(
                    "NydusLanding BLOCKED: 钱不够 75/75 (m=%.0f g=%.0f)",
                    self.ai.minerals,
                    self.ai.vespene,
                )
            return False

        # 6. 找一个已完成的 NydusNetwork 发 BUILD_NYDUSWORM
        try:
            network = networks.closest_to(self.ai.start_location)
            # salvage 教训:核对 ability 真在 Network 可用列表里(不在=引擎不受理,发了也白发)。
            worm_available: bool | None = None
            with contextlib.suppress(Exception):
                avail = await self.ai.get_available_abilities(network)
                worm_available = AbilityId.BUILD_NYDUSWORM in avail
            network(AbilityId.BUILD_NYDUSWORM, worm_pos)
            self._last_build_attempt = now
            logger.info(
                "NydusLanding: BUILD_NYDUSWORM @ (%.0f, %.0f) via Network(tag=%d) commit=%s "
                "worm_available=%s m=%.0f g=%.0f",
                worm_pos.x,
                worm_pos.y,
                network.tag,
                wave_all_in,
                worm_available,
                self.ai.minerals,
                self.ai.vespene,
            )
        except Exception as exc:
            logger.warning("_BuildNydusCanalAtEnemy BUILD_NYDUSWORM failed: %s", exc)

        return False  # 等 Canal 落地再 return True（由 _count_enemy_side_canals 触发）


class NydusRush(KnowledgeBot):  # type: ignore[misc]
    """尼德斯偷袭：虫洞投送蟑螂/女王打对方基地。

    采用 Fast Roach ZvT 变体（Spawning Tool #93631）：
      14 BS → 15 二矿 → 狗速 + 女王 + BR + Lair → VN → Canal 插入敌方家
      首攻 ~4:27 minute：6-8 蟑螂 + 2 女王经尼德斯虫洞卸载

    all-in 性质：不成功则陷入经济劣势，部队应全送。
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft NydusRush")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：Canal 投放成功 + 蟑螂量产到位 → 通知 Director 切持续。

        条件：NYDUSCANAL ≥ 1（虫洞已打通对方家）且 ROACH ≥ 6（偷家主力到位）。
        Director 收到信号 → 推荐 toast 转 persistent_roach_hydra_viper。
        """
        try:
            canals = ai.structures(UnitTypeId.NYDUSCANAL).amount
            roaches = ai.units(UnitTypeId.ROACH).amount
        except Exception:
            return False
        return bool(canals >= 1 and roaches >= 6)

    async def create_plan(self) -> BuildOrder:
        # AutoOverLord 提到最外层 BuildOrder（第 0 帧就持续补 OL）+ priority=True。
        # 2026-07-11 复盘：原先它埋在下方 SequentialList 阶段1 的 parallel block 里，被
        # 阶段0 串行前缀（DRONE14→BS→BR→Expand2→Gas1，~100s）完全阻塞 → 开局唯一那只起手
        # OL 撑到 14/14 后无人补，供应卡 14 长达 89s（telemetry t=9→103），全程供应卡顿 51%。
        # priority=True：供应是虫族最高优先级资源，OL 不该被女王/蟑螂的 priority reserve 饿到
        # 抢不到矿（act_unit.py:146 缺 builder 时 priority 会 reserve 住 cost）。
        auto_overlord = AutoOverLord()
        auto_overlord.priority = True
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 供应兜底：顶层持续生效（见上方复盘），不再被串行前缀阻塞
            auto_overlord,
            # 坑道虫的 75/75 每帧最先攥住(直到虫落地)。必须排在生产 act 之前,原因见该类 docstring。
            _ReserveCanalCost(),
            # 侦查 OL 常驻供视野：**开局第 0 帧就派**（2026-07-26 用户："开局第一个 overlord 提前
            # 到那两个点位之一，不要死，一直保持视野"）。原来卡在 `Step(UnitReady(LAIR,1), ...)`
            # 里、Lair 好了才出发——那时敌方对空已成型，OL 飞过去要么进不去要么被打掉；而开局这段
            # **敌方还没有任何对空**，是把 OL 送到位的唯一安全窗口，到位后就一直蹲着不动。
            # 驻守点纯静态地形算得出（I7），不需要先有视野，所以第 0 帧就能定点。
            _SendOverlordToEnemy(),
            # 开局供应保底（2026-07-12 真机 telemetry 实锤"人口卡=一切延迟根因"）：sharpy AutoOverLord
            # 算法 `bonus=min(larva*2,(minerals-300)/50)` 在开局 minerals<300 时 bonus 为**负**→
            # predicted_supply 被压低 → 算出只需 1 个 OL、**不提前补** → 供应死卡 14/14 达 30s（农民
            # 出不来卡 14 → 采矿慢 → 母池 83s、女王 121s、注卵荒）。加一个保底 ZergUnit(OVERLORD,2,
            # priority) 保证开局第 2 个 OL 尽早出、cap 早到 22，不卡人口；AutoOverLord 管后续增量。
            # 开局供应保底 4（AutoOverLord 在 minerals<300 时低估 → 需保底防供应卡）。注:农民卡 14
            # 不是这里的锅——是 DRONE 目标就 14(下面阶段0 ActUnit(DRONE,14) + DRONE20 被军队串行堵着),
            # 见下方 #4 修:提高早期 DRONE 目标让农民不停(2026-07-12 用户)。
            ZergUnit(UnitTypeId.OVERLORD, 4, priority=True),
            # 2026-07-12 用户真机反馈"蟑螂半天不出、农民涨到36、气飘700":larva 全被农民吃了。
            # 根因① 原 top-level ActUnit(DRONE,20) 排在 SequentialList(含蟑螂)之前 → 每帧先抢 larva、
            #       蟑螂 priority 只锁矿不锁 larva → 蟑螂饿死(整局 0-2)。**删掉这个 top-level 农民目标**,
            #       农民改由下方 Floor(非priority软地板,吃军队剩的 larva)填到封顶,蟑螂优先吃 larva。
            # 根因② sustain fallback@300s 强制解封农民封顶 → 农民饱和 → canal(~6min)前的攒兵窗口被农民
            #       吃光。改 _SetWorkerCap(hard=True):canal 落地前封顶不被 sustain_uncap 解除(见 Floor)。
            # 农民封顶 24（2026-07-12 真机反馈：二矿前补农民、二矿后**停一波农民把 larva 转出蟑螂狗**）。
            # 真机 telemetry 实锤：农民从 14 一路 Floor 补到 40 → 把 larva 全吃了 → 蟑螂/女王缺 larva、
            # canal 前只 1-3 蟑螂（KPI 8-10）。封 24（≈二矿采矿够）让二矿后 larva 让给女王(注卵引擎)+
            # 蟑螂。这是开局快攻封顶，opening 完成/转运营后 WorkerSaturationFloorAct 自动解封补到饱和，
            # 不伤后期刺蛇运营。
            # 农民封顶 28 + hard(2026-07-13 用户强纠正:"别卡单矿上限、两矿要补满,开二矿/气矿/蟑螂池都
            # 吃农民,前面运营要搞好")。20 太少 → 两矿长期不满采。28≈两矿满采(每矿~14+气)。改用"先用狗
            # 打一波"后早期不靠蟑螂,larva 可多给农民把两矿铺满;蟑螂当续兵。hard=True → 保持到**三矿**建起
            # 才解封(canal 后续兵窗口农民不涨、矿给多波狗/蟑螂,见 _SetWorkerCap.execute)。
            _SetWorkerCap(28, hard=True),
            # ── 科技树 build order ──────────────────────────────────────────
            # 14 农 BS → 蟑螂窝 → 二矿 → 1气 → 狗蟑快攻 → Lair → 坑道投送
            SequentialList(
                # 阶段 0：**只留 DRONE14 → BS 两步硬顺序**（2026-07-12 真机 telemetry 实锤：
                # 阶段0串行会卡住等每一步 done 才进阶段1 → 女王1/农民/Lair/VN 全被推迟。BR/二矿/气
                # 全部移到阶段1并行，阶段0极短 → 女王母池一好(~81s)就出、注卵早、larva 引擎早转，
                # 前期农民也不再卡）。
                # 14 农（二矿经济更稳，Nydus 需要 200 气）
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 14),
                # BS 母池（14 supply），是 Lair / BR / 女王 的前置
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                # 阶段 1：并行暴兵 + 科技（二矿/气/BR 都在这里，靠 priority + children 顺序排矿）
                BuildOrder(
                    # AutoOverLord 已上移至最外层 BuildOrder（见 create_plan 顶部复盘）——
                    # 原先在此处被阶段0 串行前缀阻塞到 ~100s，是开局卡供应的结构根因。
                    # ── 坑道链提速重排（2026-07-11 Fable5 三修，3 局逐帧数据实锤真凶）──
                    # 真凶不是女王的矿，是**狗速抢走第一个 100 气** + **主基被女王订单占用**：
                    # ① Tech(狗速) 缺气时每帧 reserve 100 气，排在 MorphLair 前把第一个 100 气锁死，
                    #    Lair 只能等第二个 100 气(~205s) → morph 卡到 ~3:30。② MorphBuilding 对**有订单**
                    #    的孵化场直接跳过不 morph（morph_building.py patch），2 只女王都占主基 → Lair 连
                    #    reserve 都发不出。修法（零经济代价，纯 children 重排）：
                    #    女王1 → 早狗 → MorphLair 上移 → 女王2(挂二矿) → 狗速下移。
                    # 女王1（主矿注卵命根，priority 最高；母池一好立刻出 = larva 引擎第一时间启动）
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 1, priority=True),
                    ),
                    # 二矿（从阶段0移来，2026-07-12）：排女王1后 = 女王1 先锁矿启动注卵引擎，二矿紧跟。
                    # 不再在阶段0串行阻塞阶段1（原 Expand2 在阶段0 → 女王1 被推到 120s+ 才出）。
                    Expand(2),
                    # 第 1 口气（从阶段0移来）：供狗速 100 + Lair 100。第 2 口气不连踩（见下方 parallel）。
                    BuildGas(1),
                    # 二矿前农民放开早出到 22（2026-07-13 用户"农民一开始早出、别卡上限,canal 部署时农民
                    # 已达较多数量经济才好"）：蟑螂已 gate 在 VN 之后,早期 larva 不跟蟑螂抢 → 农民可放开
                    # 冲到 22 把两矿早早铺起来;之后 Floor(封顶 28)补满两矿。蟑螂在 VN 好后才产、当主力。
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                    # 早期速狗 6（BS 完成就出,少量防守——用户"蟑螂先出8个再考虑别的",狗别抢太多 larva；
                    # 主防守/前压交给蟑螂）。gate 母池,只吃矿不抢气。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 6),
                    ),
                    # 蟑螂窝 BR（2026-07-12 从阶段0串行移来）：gate 母池，母池一好(~90s)就建、~125s 好，
                    # 和女王1/狗8 并行、不阻塞 Lair/VN。原先在阶段0串行 → SequentialList 卡等 BR 建好
                    # 才进阶段1 → 女王1/Lair/VN 全被推迟（女王1 拖到 145s、canal 前仅 3-4 蟑螂）。移来后
                    # 时机不变但不阻塞 → 女王早出注卵早、蟑螂链提前。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        GridBuilding(UnitTypeId.ROACHWARREN, 1),
                    ),
                    # Lair **上移到狗速前 + 女王2 前**（抢第一个 100 气 + 女王1 造完主基就 morph）：
                    # → Lair morph ~2:50 起、坑道链整体提前 ~90-110s（canal 500-629→~350-400s）。
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        MorphLair(),
                    ),
                    # ── VN 尼德斯网络：all-in 命脉，Lair 一好立刻建、抢在蟑螂/女王/农民之前 ──
                    # 2026-07-13 真机复盘（用户"6:30 连坑道虫都没造出来"）：VN 是 GridBuilding **无
                    # priority**，而蟑螂12/女王6 都 priority=True → 我修好经济后蟑螂真的产出来、每帧
                    # reserve 光矿 → 矿死死压在 150 以下 → VN 攒不够、整局(6:30)都没建起来 → **绝无
                    # canal**。**光排在蟑螂前不够——priority 才锁矿**。改 priority=True:VN 缺矿时
                    # reserve 住 150,抢在蟑螂前第一个建起来(Lair 好即 ~15s 后 VN 好)。all-in 命脉必须最高优先。
                    Step(
                        UnitReady(UnitTypeId.LAIR, 1),
                        GridBuilding(UnitTypeId.NYDUSNETWORK, 1, priority=True),
                    ),
                    # 女王持续出到 4（2026-07-12 用户：女王和注卵不停，女王也当兵钻坑道，充分利用
                    # 基地产能）。2 矿时 = 2 只留家注卵（每矿 1，inject 不停，larva 荒的解药）+ 2 只
                    # 钻坑道当兵。挂二矿 ready 自然从二矿出，priority=True 保证及时（女王产 larva 是
                    # 整个 build 的产能命根，别让狗/蟑螂缺矿时把女王挤后）。
                    Step(
                        UnitReady(UnitTypeId.HATCHERY, 2),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 6, priority=True),
                    ),
                    # 狗速**下移**（让出第一个 100 气给 Lair；佯攻小狗晚 ~50s 拿到速度，可接受）
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    # overlord 速度(Pneumatized Carapace,2026-07-11 用户点1):让侦查 OL 能 kite、
                    # 不再送死。100/100 便宜,省下的"OL 一只只送死"的矿/供应远超它。
                    Tech(UpgradeId.OVERLORDSPEED),
                    # 蟑螂 12 **priority=True + 等 VN 放好再产**（2026-07-13 真机复盘,关键）：
                    # sharpy GridBuilding 的 priority reserve 是**有条件的**(只在预测「农民走到落点的时间内
                    # income 能攒够 cost」时才 reserve,grid_building.py:204)。蟑螂(75 矿)一直吃矿 →
                    # available_minerals 压低 → VN 预测攒不够 150 → 永不 reserve → 蟑螂继续吃 → VN 整局
                    # 建不起来(6:30 无 canal)。解:蟑螂 gate 在 **VN 已放置(structures≥1)** 之后——VN 先
                    # 独占矿流攒够 150 放下(Lair 好即 ~15s),放下后蟑螂才产(VN 15s 建好期间蟑螂已在攒)。
                    # 蟑螂本身仍 priority(斗过狗/农民)。VN 是 all-in 命脉,必须先于军队(见 D11/F15)。
                    Step(
                        RequireCustom(
                            lambda ai: (
                                ai.structures(UnitTypeId.ROACHWARREN).ready.amount >= 1
                                and ai.structures(UnitTypeId.NYDUSNETWORK).amount >= 1
                            )
                        ),
                        ZergUnit(UnitTypeId.ROACH, 12, priority=True),
                    ),
                    # 第 2 口气**在 VN 建起时起**（2026-07-13 用户"在合适时候起二气,第一波保证 8+ 蟑螂"）：
                    # VN 好=蟑螂开始产(蟑螂 gate 在 VN),8 蟑螂需 ~200 气,1 气(狗速/Lair/VN/canal 已花)撑
                    # 不起 8 蟑螂 → 二气跟上供蟑螂。VN 后开=蟑螂真吃气、不飘(之前 700 飘是蟑螂没产;现在产了)。
                    Step(
                        UnitExists(UnitTypeId.NYDUSNETWORK, 1),
                        BuildGas(2),
                    ),
                    # 速狗补到 24（含佯攻小队 6 池，见 feint_squad_act.py）
                    ZergUnit(UnitTypeId.ZERGLING, 24),
                    # 农民铺到 20（**排在军队之后**——2026-07-11 Fable5：larva 争抢看 children 顺序、
                    # priority 只锁矿不锁 larva；农民排蟑螂/狗前会先抢光 larva，蟑螂拿不到卵。
                    # 放最后 = 军队优先吃 larva，农民用 2 女王注卵的富余 larva 填到 20。快攻窗口
                    # 内 _SetWorkerCap(22) 封顶不铺满，一波打完转运营 Floor 再拉到饱和）
                    ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 20),
                    # （VN 尼德斯网络已上移到 MorphLair 之后、抢在蟑螂前建，见上方）
                    # 蟑螂速度升级（Glial Reconstitution）**延到 canal 落地之后**（2026-07-13 用户：
                    # 提蟑螂速度那 100 气早期消耗太大、抢了坑道虫(75 气)的气 → 坑道链被拖慢。早期气
                    # 全留给 狗速/Lair/VN/canal;蟑螂速度是"卸载后追击更快"的锦上添花,canal 投出去了再研）。
                    Step(
                        UnitExists(UnitTypeId.NYDUSCANAL, 1),
                        Tech(UpgradeId.GLIALRECONSTITUTION),
                    ),
                    # ── 关键新增：Canal 投放 ────────────────────────────────
                    # （侦查 OL 已提到最外层 BuildOrder、开局第 0 帧就出发，见 create_plan 顶部）
                    # Step B：NydusNetwork 完成后持续检查视野 + 下 BUILD_NYDUSWORM
                    # Overlord 约 15-25s 飞到位，Canal 落地后 14s 钻出
                    # 整个窗口：VN 完成 → Overlord 到位 → Canal 钻出 ≈ 30-40s
                    Step(
                        UnitReady(UnitTypeId.NYDUSNETWORK, 1),
                        _BuildNydusCanalAtEnemy(),
                    ),
                    # （农民已在爆狗前铺到 22，见上方；此处不再重复低目标）
                    # 女王持续补充（注射幼虫 + 提供反制 Banshee 的肿瘤）
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 4),
                    # ── Macro tail(2026-05-23 中期持续运营;2026-07-10 减气+去冗余农民)─
                    # Nydus all-in 没赢 → 拉持续运营,免得余矿堆积。
                    # 三矿 + 三气(2026-07-10 用户:战术吃矿为主,5气必堆——roach/ling 不吃那么多气;
                    # 后期要不要转吃气兵种由玩家决定,build 不自堆后期经济)。
                    # 农民由 WorkerSaturationFloorAct 统一兜底到饱和(虫族封 66),此处不再写 DRONE 目标
                    # (原 DRONE 70 会盖过 Floor 的 66 封顶、抢走留给兵的人口)。
                    # 三矿挂在 **canal 落地后 + 7.5min 后**（2026-07-13 用户强纠正）：canal 一落就开三矿
                    # 会抢走**续兵(狗/蟑螂)的钱** → 第一波打出优势后续不上。改成 canal 落地 **且** time>450：
                    # 先把 canal 后的续兵(多波蟑螂/狗灌坑道)钱管够,拖到 7.5min 才开三矿转运营。
                    # (all-in 没投送出去 canal 未落绝不扩张;快攻早赢则三矿门根本不触发。)
                    Step(
                        UnitExists(UnitTypeId.NYDUSCANAL, 1),
                        Expand(3),
                        None,
                        lambda ai: ai.time > 450,
                    ),
                    # 第 3 口气挂在三矿真起来之后（Fable5：上局 BuildGas(3) 裸的、无门 →
                    # t=243 蟑螂 0 只时就踩第 3 口气 → 气一路鱼到 1108。三矿存在=已转运营，才配它）。
                    Step(UnitExists(UnitTypeId.HATCHERY, 3), BuildGas(3)),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 5),
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 30),
                    ),
                    # ── Point2 T3 转运营宏观骨架（攻防升级 + 配套气矿曲线）2026-07-11 ────────
                    # VeryHard 基线诊断（24 局）：坑道虫落地也骚扰到农民了，但游戏拖到 20-40 分钟，
                    # 一波流 0/0 部队被 AI 用运营磨死——长局败占绝大多数（胜局全是 <15min 快杀）。
                    # 用户："再不行慢慢运营这样更稳"。给长局的持续部队攻防升级 + 配套气矿（Fable5：
                    # 没气升级就是空门控）。全部时间门控在 wave1 窗口之后（time>360~6:00 起，用第 4
                    # 参 skip_until），不抢首波；wave1 快杀赢了这些门根本不触发。
                    # 升级 enum 必带 LEVEL 后缀（Opus 评审：裸名不存在会构造崩）；蟑螂/刺蛇是远程，
                    # 升远程攻(MISSILE)+地甲(ARMORS 共享)，砍近战(MELEE)。双进化腔 ~6:00（Fable5：
                    # 别等 7:30，VeryHard 宏观 AI 那时已 +2）。
                    # ── 进化腔 + 攻防升级已移除（2026-07-12 用户#2：一波坑道打法下 2 个进化腔 + 4 个升级
                    # 纯抢 canal 的 75/75 + larva，把坑道拖慢）。一波 all-in 不需要攻防；要转运营(建腔升级)
                    # 由玩家在面板确认 doctrine 才做（同#切战术铁律）。
                    # 气矿曲线（Fable5）：升级 + 后期兵种吃气，一波低气 build 撑不起三线。
                    # 4 口 ~7:00、5 口 ~9:00，门控三矿存在（已转运营）+ 时间，不抢首波。
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 3),
                        BuildGas(4),
                        None,
                        lambda ai: ai.time > 420,
                    ),
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 3),
                        BuildGas(5),
                        None,
                        lambda ai: ai.time > 540,
                    ),
                    # ── 刺蛇自动转型已移除（2026-07-12 用户铁律：玩家没确认，不准自动切战术）──
                    # 原 T4 会 time>420 自动建刺蛇巢 + core_units 含 HYDRALISK 让 sustain 自动爆刺蛇 →
                    # 玩家没确认就"切到吃蛇"。删掉：本 build 保持**纯坑道**（狗蟑女王多波），中期靠 sustain
                    # 继续爆蟑螂/狗（core_units 已去 HYDRALISK）。要转刺蛇/运营，玩家在宏观面板**确认**
                    # 推荐的 persistent_roach_hydra_viper doctrine（lategame_transitions 保留=只推荐）才切。
                ),
            ),
            # ── 家事 + 进攻 ─────────────────────────────────────────────────
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常每帧 return False（sharpy
                # zone_attack.py:123 "Blocks!"），SequentialList 遇 False 就停 ——
                # 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # ── Round 4「声东击西」（2026-07-09 用户拍板）───────────────
                # FeintSquadAct 必须排在 NydusRaidAct 之前：同一 tick 内先跑，
                # 认领的佯攻小狗 tag 写进 ai._vibecraft_nydus_feint_tags，
                # NydusRaidAct._recruit 才能在本帧读到并排除（两 act 分池不重叠）。
                # 小股速狗持续骚扰敌方二矿，引敌军主力离开主基地矿线，为
                # _BuildNydusCanalAtEnemy 的窗口检测创造"矿线附近敌军数低于阈值"
                # 的安全落点窗口。non-blocking，永远 return True。
                FeintSquadAct(),
                # ── 精修（2026-07-09 #NydusRaidPolish）──────────────────────
                # NydusRaidAct：真正的"投送" —— STAGE(集结)→TRANSIT(坑道内)→
                # STRIKE(钻出打击) 状态机，把 army 灌过坑道网络钻到敌方家，替代
                # 旧版"坑道虫建出来没人用、army 走正面"的 gap。它把狗/蟑/女王
                # Reserve 独占（PlanZoneGather/PlanZoneAttack 只拉 free_units，
                # 天然不冲突，无需 vendor hook）。non-blocking，永远 return True。
                NydusRaidAct(),
                # 多余女王的去处（2026-07-26 用户）：坑道链卡住（自家网络就绪很久、敌方那边
                # 一个虫都没立住）时，超出注卵需要的女王出去把菌毯往最外分矿方向铺，到位后
                # clear_task 交回 sharpy 当前线防守兵。链正常推进时它完全不介入。
                # 必须排在 NydusRaidAct 之后：读它发布的 _vibecraft_nydus_raid_tags 排除
                # 已被坑道队认领的女王（同 Feint↔Raid 那套分池约定）。
                SpareQueenAct(),
                # PlanZoneAttack 现在主要处理：① 招募封顶之外的 macro-tail 单位
                # （ROACH 30 那批）② NydusRaidAct 兜底释放回来的单位（走正面）。
                # Round 2（2026-07-09 真局教训）：start_attack_power 从旧阈值 10 提到
                # 30——round1 真机实测 threshold=10 太低，macro-tail 溢出的几只 roach
                # 就够触发前门进攻,前门经常在坑道虫真正打通之前就把敌人打死收工，
                # 演示核心（坑道虫突袭）根本没机会登场。提高阈值让坑道虫先手，前门
                # 仅作为"偷袭没打死→macro-tail 累积够多再补刀"的兜底（设计评审处置 #6）。
                PlanZoneAttack(start_attack_power=30),
                PlanFinishEnemy(),
            ),
        )
