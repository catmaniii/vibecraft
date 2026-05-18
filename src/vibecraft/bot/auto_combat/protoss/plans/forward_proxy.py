"""4 BG 早压前线支援：派 1 农民兼做"探路 + 保命 + 隐蔽地点修 BE + 野 BG"。

设计目标
========
- proxy 点尽可能离敌方主基地近（折跃 timing 短），但不被敌方视野看到
- 候选点：敌方扩张点（含 natural/third/...）+ 环形点兜底
- 评分：距离越近越高分；硬过滤掉敌方视野/placement 不可行的
- top 3 候选随机选（防被对手摸到规律）

判定 forward 建筑 vs 家里建筑
=============================
- 空间过滤：靠近 proxy（< 30）且远离所有自家 Nexus（> 25）
- tag 跟踪：一次识别后用 tag 锁定，避免与其它建筑混淆
- 5 状态：none / ordering / in_progress / ready / destroyed

完成判定（5 重 OR）
==================
A. PYLON ready + GATEWAY ready
B. PYLON 和 GATEWAY 都曾经 ready（destroyed 也算 —— 不重建被拆的）
C. 超时 90s
D. worker 死亡 ≥ 2 次（不再砸农民）
E. 主力已出门压制（VibeCraftZoneAttack 已触发，proxy 已失去意义）

worker 管理
===========
- worker 死了/丢了 → 重新指派 + 死亡计数 +1
- 死亡计数 ≥ 2 → 整个任务终止
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# proxy 周围检测半径（forward 建筑必须在 proxy 这个范围内）
_PROXY_R: float = 30.0
# 自家所有 Nexus 这个距离内的建筑视为"家里"，不算 forward
_MIN_HOME_DIST: float = 25.0
# 距离敌方主基地的硬下限（太近敌方视野必看到 + 撞 main NEXUS/gas 区域 placement fail）
# 历史:
# - 12/18: 撞 main / natural 占地范围
# - 28: 仍选到敌方 natural/third 区域被发现
# - 40: 用户反馈"proxy 离对方基地可以更近一点 — 贴边走廊已经救了路上安全"
# - 30(当前): 配合贴边评分(权重 50),贴边走廊优先,允许 ring 30/35 距敌方更近
#   但 natural ~16/third ~25 仍被避开
_MIN_DIST_TO_ENEMY: float = 30.0
# 距离敌方主基地的硬上限（太远没折跃 timing 价值）
_MAX_DIST_TO_ENEMY: float = 60.0
# 任务超时（秒，游戏内）
# 历史:90 - 走路 ~30s + PYLON 18s + GATEWAY 35s = 83s,余量 7s,placement 一次失败
# 就崩。提到 150:走路 + 双建造 + 失败重试余量充足。
_TASK_TIMEOUT_S: float = 150.0
# worker 死亡次数上限（再多就放弃）
# 历史:2 - 实战 log(game_20260518_043040)PYLON 修完 + GATEWAY 修了 ~10s 就 worker 死,
# 第 2 个 worker 接力又死,卡 2 死阈值放弃 → GATEWAY 没修完任务失败。
# 提到 4:多让几个农民送也值得换 GATEWAY 修完(50 矿 vs forward BG timing 价值)
_MAX_WORKER_DEATHS: int = 4
# HP+shield 撤退/复出阈值
_RETREAT_RATIO: float = 0.5
_REENGAGE_RATIO: float = 0.9

# 建筑 type → 对应 BUILD AbilityId（用来识别 worker 是否在 ordering 该建筑）
_BUILD_ABILITIES: dict[UnitTypeId, set[AbilityId]] = {
    UnitTypeId.PYLON: {AbilityId.PROTOSSBUILD_PYLON},
    UnitTypeId.GATEWAY: {AbilityId.PROTOSSBUILD_GATEWAY},
}


class ForwardSupportPylonGateway(ActBase):  # type: ignore[misc]
    """4bg 早压前线支援：1 农民造 1 BE + 1 野 BG，保命优先，必要时牺牲。"""

    def __init__(self) -> None:
        super().__init__()
        self.proxy_worker_tag: int | None = None
        self.proxy_location: Point2 | None = None
        self.hide_location: Point2 | None = None  # retreat 时躲的位置
        self._completed: bool = False
        self.retreating: bool = False

        # ---- tag 跟踪 ----
        # type → 锁定的 forward 建筑 tag（None 表示还没出现 / 还没锁定）
        self._proxy_tags: dict[UnitTypeId, int] = {}
        # 历史曾 ready 过的 tag（区分 destroyed vs 没造完就被拆）
        self._ever_ready: set[int] = set()

        # ---- worker 死亡跟踪 ----
        self._worker_death_count: int = 0

        # ---- 任务起始 ts（超时判断）----
        self._start_time: float | None = None

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        # proxy 选点延后到第一次 execute（需要 game_info + zone_manager 完整 init）
        self.proxy_location = None
        self.hide_location = None

    # ------------------------------------------------------------------
    # proxy 选点
    # ------------------------------------------------------------------

    def _pick_proxy_location(self) -> Point2 | None:
        """从候选点里选最好的（top 3 随机）。

        Fallback 链：
        1. 优先：自定义候选（敌方扩张 zones + 环形）按评分排序，top 3 随机
        2. 兜底：所有候选硬过滤后空（极端地图 / 敌方视野全覆盖）→
           **沿用 sharpy 标准算法**（参考 sharpy/dummies/protoss/proxy_zealot_rush.py:73）
           `map_center.towards(enemy_start, 25)` —— 经过 sharpy 实战验证的兜底点
        """
        candidates = self._generate_candidates()
        if not candidates:
            return self._sharpy_fallback_proxy()

        # 评分 + 过滤负分（硬约束违反）
        scored = [(p, self._score_pos(p)) for p in candidates]
        scored = [(p, s) for p, s in scored if s > 0]
        if not scored:
            logger.info("ForwardSupport: 0 valid candidates after hard filter → sharpy fallback")
            return self._sharpy_fallback_proxy()

        scored.sort(key=lambda x: x[1], reverse=True)
        # top 3 随机
        top_n = scored[: min(3, len(scored))]
        chosen, score = random.choice(top_n)
        logger.info(
            "ForwardSupport picked proxy=%s (score=%.1f, %d/%d candidates valid)",
            chosen,
            score,
            len(scored),
            len(candidates),
        )
        return chosen

    def _sharpy_fallback_proxy(self) -> Point2 | None:
        """sharpy 标准算法：map_center 朝敌方主基地 25 距离。

        参考 sharpy/dummies/protoss/proxy_zealot_rush.py:73 的实现，
        这是 sharpy 实战 proxy zealot rush 用的固定算法，在大多数 SC2 标准
        地图上是合理的中场盲区。
        """
        try:
            return self.ai.game_info.map_center.towards(self.ai.enemy_start_locations[0], 25)
        except Exception:
            return None

    def _generate_candidates(self) -> list[Point2]:
        """生成候选 proxy 点：敌方主基地外围中距环形点(避开二矿等已知 scout 必经点)。

        历史:
        - v1: 含敌方扩张点 zones[1:]+ ring 20-35 → 实测(game_20260518_043040)选了
          (145.5, 98.5)= 敌方 natural,worker 修建被对方 worker/zealot 立刻发现 + 围殴
        - v2: 去掉 expansion zones,ring 30-45 → 仍选到 natural/third 附近
          (game_20260518_044000 用户反馈"出生在左下时 proxy 修到了对方二矿")
        - v3(当前): ring 40-55,加"地图中线候选" — proxy 落在中线偏敌方一侧,
          安全性大幅提升(距敌方扩张点 ≥40),折跃 timing 多走 ~3-4s 可接受
        """
        candidates: list[Point2] = []
        try:
            enemy = self.ai.enemy_start_locations[0]
            own = self.ai.start_location
        except (IndexError, AttributeError):
            return []

        # A. 中线候选:自家 → 敌家直线上偏敌方一侧的几个点,加上侧向偏移
        # 这是"地图中线 + 偏敌方"的安全玩家手法 — 距双方家都远,不在已知扩张点连线上
        dx, dy = enemy.x - own.x, enemy.y - own.y
        path_len = math.hypot(dx, dy)
        if path_len > 1e-3:
            ux, uy = dx / path_len, dy / path_len  # 主轴单位向量
            # 垂直向量(顺时针 90°)
            vx, vy = -uy, ux
            # 沿主轴在 60%-75% 位置(偏敌方一侧),3 个进度点 × 3 个侧向偏移(0/+15/-15)
            for t in (0.60, 0.67, 0.75):
                base_x = own.x + dx * t
                base_y = own.y + dy * t
                for offset in (0.0, 15.0, -15.0):
                    candidates.append(Point2((base_x + vx * offset, base_y + vy * offset)))

        # B. 环形点：敌方主基地外环 30/40/50/55 距离 × 6 方向
        # 30 起点 = 与 _MIN_DIST_TO_ENEMY 配套,贴边走廊可以更靠近敌方
        # 55 上限仍小于 _MAX_DIST_TO_ENEMY=60
        for dist in (30, 40, 50, 55):
            for angle_deg in range(0, 360, 60):
                angle_rad = math.radians(angle_deg)
                candidates.append(
                    Point2(
                        (
                            enemy.x + dist * math.cos(angle_rad),
                            enemy.y + dist * math.sin(angle_rad),
                        )
                    )
                )
        return candidates

    def _score_pos(self, pos: Point2) -> float:
        """评分 proxy 候选点。负分 = 硬约束违反（必排除）。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return -1.0

        # —— 硬约束 0:地图边界 ——
        # ring 在某些角度可能越过地图边界(实战 log game_20260518_044957:
        # proxy (28.5, -6.14) ring angle=240°,负 Y!)。playable_area Rect
        # 在 in_placement_grid 检查前先过滤,避免 grid 抛 IndexError 被 except 吞掉。
        if not self._in_map_bounds(pos):
            return -1.0

        dist = pos.distance_to(enemy)
        # —— 硬约束 ——
        if dist < _MIN_DIST_TO_ENEMY:
            return -1.0  # 太近，敌方主基地必看到
        if dist > _MAX_DIST_TO_ENEMY:
            return -1.0  # 太远没意义
        if self._in_enemy_vision(pos):
            return -1.0  # 当前被敌方单位/建筑看到
        try:
            if not self.ai.in_placement_grid(pos):
                return -1.0  # 放不了建筑
        except Exception:
            return -1.0  # placement 查询失败说明位置异常(out of bounds 等),保守过滤

        # —— 软评分：距离越近越好 ——
        s = 100.0
        s -= (dist - _MIN_DIST_TO_ENEMY) * 2.0

        # 偏离敌方"主路"加分（侧翼隐蔽）
        try:
            off_path = self._off_main_path(pos)
            s += min(off_path, 20)
        except Exception:
            pass

        # **农民走路时不被发现**：偏离"自家 → 敌家"直线加分。
        # 实战 log(2026-05-18) 选 (112.5, 119.5) 距家 110,走直线必经地图中央敌方
        # scout 巡逻区,2 worker 被打死任务终止。此项加分鼓励选地图边缘点 — 农民
        # 沿边走绕过敌方探路视野。权重 1.5(比 off_main_path 高,因为"走路安全"
        # 比"建好后隐蔽"更关键 — 死了就修不成)
        try:
            off_attack = self._off_attack_axis(pos)
            s += min(off_attack * 1.5, 30)
        except Exception:
            pass

        # **贴地图边加分**：距离 playable_area 边界越近,加分越多。
        # 实战 log(2026-05-18 04:55): 自家左下时选 (90.1, 99.3) 距 edge ~30,
        # 在敌方下二矿必经路被发现 → done(C: timeout 152s)。
        # 玩家手法:proxy 永远贴边走廊 → 远离 scout 巡逻区 + 远离扩张连线。
        # 权重 50 max,比其他评分项都高 — 这是最关键的安全因素。
        try:
            edge_d = self._edge_distance(pos)
            # 距边 0 → +50, 距边 25+ → 0(线性衰减)
            s += max(0.0, 50.0 - edge_d * 2.0)
        except Exception:
            pass

        # 低地小加分（敌方主基地通常在高地）
        try:
            enemy_height = self.ai.get_terrain_height(enemy)
            pos_height = self.ai.get_terrain_height(pos)
            if pos_height < enemy_height:
                s += 8
        except Exception:
            pass

        return s

    def _edge_distance(self, pos: Point2) -> float:
        """pos 到 playable_area 最近一条边的距离(越小 = 越贴边)。

        贴边 proxy 的优势:
        - 远离敌方 scout/probe 巡逻区(SCV/probe 通常走 main → expansion 直线)
        - 远离敌方扩张点连线
        - worker 走过去可沿边缘走廊,被发现概率最低
        """
        try:
            area = self.ai.game_info.playable_area
            return min(
                pos.x - area.x,
                area.x + area.width - pos.x,
                pos.y - area.y,
                area.y + area.height - pos.y,
            )
        except Exception:
            return 100.0  # 不能查时按"非边缘"处理(不加分)

    def _in_map_bounds(self, pos: Point2) -> bool:
        """pos 是否在 playable_area 矩形内(防止 ring 越过地图边界生成负坐标点)。

        playable_area 是 SC2 给出的"地图可玩区域"矩形(剔除四周不可达边缘),
        Point2 在此矩形外的查询(in_placement_grid 等)会 IndexError 或返回不准。
        """
        try:
            area = self.ai.game_info.playable_area
            return bool(
                area.x <= pos.x <= area.x + area.width and area.y <= pos.y <= area.y + area.height
            )
        except Exception:
            return True  # 不能查 area 时不否决,留给 in_placement_grid 处理

    async def _safe_find_placement(self, unit_type: UnitTypeId, near: Point2) -> Point2 | None:
        """python-sc2 find_placement 包装,handle async + exception 兜底。

        find_placement 实际会向 SC2 client query "这里能放吗",所以是 async。
        返回 None = SC2 拒绝(没有合法 placement 在 near 周围 20 格内)。
        """
        try:
            result = await self.ai.find_placement(unit_type, near, max_distance=20)
            return result  # 可能 None
        except Exception as exc:
            logger.warning(
                "ForwardSupport find_placement fail for %s near (%.1f,%.1f): %s",
                unit_type.name,
                near.x,
                near.y,
                exc,
            )
            return None

    def _off_attack_axis(self, pos: Point2) -> float:
        """pos 到 own_main→enemy_main 直线的垂直距离。

        农民从自家走到 proxy 这一段路要远离主进攻轴线,否则路上必被敌方 scout/
        zealot 拦截。返回值越大 = 越偏轴 = 走路越安全。
        """
        try:
            own = self.ai.start_location
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return 0.0
        dx, dy = enemy.x - own.x, enemy.y - own.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        # 投影 + 垂直距离
        px, py = pos.x - own.x, pos.y - own.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        proj_x = own.x + t * dx
        proj_y = own.y + t * dy
        return math.hypot(pos.x - proj_x, pos.y - proj_y)

    def _in_enemy_vision(self, pos: Point2) -> bool:
        """pos 是否在任何已见敌方单位/建筑的视野半径内。"""
        try:
            for e in self.ai.enemy_units | self.ai.enemy_structures:
                sight = getattr(e, "sight_range", 8.0)
                if pos.distance_to(e) < sight + 2.0:
                    return True
        except Exception:
            pass
        return False

    def _off_main_path(self, pos: Point2) -> float:
        """pos 到 enemy_main→map_center 直线的垂直距离。越大越偏路。"""
        enemy = self.ai.enemy_start_locations[0]
        center = self.ai.game_info.map_center
        # 点到线段的垂直距离（线段：enemy → center）
        dx, dy = center.x - enemy.x, center.y - enemy.y
        line_len_sq = dx * dx + dy * dy
        if line_len_sq < 1e-6:
            return 0.0
        px, py = pos.x - enemy.x, pos.y - enemy.y
        t = max(0.0, min(1.0, (px * dx + py * dy) / line_len_sq))
        proj_x = enemy.x + t * dx
        proj_y = enemy.y + t * dy
        return math.hypot(pos.x - proj_x, pos.y - proj_y)

    # ------------------------------------------------------------------
    # 建筑状态 + tag 跟踪
    # ------------------------------------------------------------------

    def _is_forward_building(self, struct: Any) -> bool:
        """这栋建筑是不是 forward proxy 的（不是家里的）。"""
        if self.proxy_location is None:
            return False
        if struct.distance_to(self.proxy_location) > _PROXY_R:
            return False
        for nx in self.ai.townhalls:
            if struct.distance_to(nx) < _MIN_HOME_DIST:
                return False
        return True

    def _building_state(self, unit_type: UnitTypeId) -> str:
        """检测 forward 建筑当前状态：ready / in_progress / ordering / destroyed / none。"""
        # 跟踪 GATEWAY 时也算 WARPGATE（升级后 tag 不变，但 type 切换）
        types_to_check = {unit_type}
        if unit_type == UnitTypeId.GATEWAY:
            types_to_check.add(UnitTypeId.WARPGATE)

        tag = self._proxy_tags.get(unit_type)

        # —— 已 tagged：用 tag 跟到底 ——
        if tag is not None:
            struct = self.ai.structures.find_by_tag(tag)
            if struct is None:
                # tag 实体没了
                if tag in self._ever_ready:
                    return "destroyed"  # 建完后被拆
                # 没造完就被拆：清 tag 重新派
                self._proxy_tags.pop(unit_type, None)
                return "none"
            if struct.is_ready:
                self._ever_ready.add(tag)
                return "ready"
            return "in_progress"

        # —— 没 tagged：找一个 forward 建筑锁定 ——
        try:
            in_range = self.ai.structures.of_type(types_to_check)
        except Exception:
            in_range = []
        for s in in_range:
            if self._is_forward_building(s):
                self._proxy_tags[unit_type] = s.tag
                if s.is_ready:
                    self._ever_ready.add(s.tag)
                    return "ready"
                return "in_progress"

        # —— 没建筑实体，看 worker 是否在 ordering 该类型 ——
        worker = self._get_proxy_worker()
        if worker is not None:
            build_abils = _BUILD_ABILITIES.get(unit_type, set())
            # python-sc2 的 UnitOrder.ability 是 AbilityData(不是 AbilityId);
            # 真正的 AbilityId 在 .ability.id —— 写 .ability_id 会 AttributeError
            for order in getattr(worker, "orders", []):
                ability = getattr(order, "ability", None)
                if ability is not None and getattr(ability, "id", None) in build_abils:
                    return "ordering"

        return "none"

    # ------------------------------------------------------------------
    # worker 管理
    # ------------------------------------------------------------------

    def _get_proxy_worker(self) -> Any:
        """获取当前 proxy worker（None 表示丢/死，需要重新指派）。"""
        if self.proxy_worker_tag is None:
            return None
        try:
            w = self.cache.by_tag(self.proxy_worker_tag)
        except Exception:
            w = None
        return w

    def _assign_new_worker(self) -> Any:
        """重新指派一个 proxy worker。返回 worker 或 None（没农民可用）。"""
        try:
            if not self.ai.workers:
                return None
            if self.proxy_location is None:
                return None
            w = self.ai.workers.closest_to(self.proxy_location)
        except Exception:
            return None
        self.proxy_worker_tag = w.tag
        logger.info(
            "ForwardSupport assigned worker tag=%d (death_count=%d)",
            w.tag,
            self._worker_death_count,
        )
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, w)
        except Exception:
            pass
        return w

    def _release_worker(self) -> None:
        """完成 / 终止时释放 worker。"""
        if self.proxy_worker_tag is None:
            return
        try:
            from sharpy.managers.core.roles import UnitTask

            w = self.cache.by_tag(self.proxy_worker_tag)
            if w is not None:
                self.knowledge.roles.clear_task(w)
                self.knowledge.roles.set_task(UnitTask.Idle, w)
        except Exception:
            pass
        self.proxy_worker_tag = None

    # ------------------------------------------------------------------
    # 任务完成判定
    # ------------------------------------------------------------------

    def _is_done(self) -> bool:
        """5 重 OR 完成条件，任一满足返回 True。"""
        py_state = self._building_state(UnitTypeId.PYLON)
        bg_state = self._building_state(UnitTypeId.GATEWAY)

        # A. 双 ready
        if py_state == "ready" and bg_state == "ready":
            logger.info("ForwardSupport done (A: both ready)")
            return True

        # B. 都曾 ready（destroyed 也算 —— 不重建被拆的）
        if py_state in ("ready", "destroyed") and bg_state in ("ready", "destroyed"):
            logger.info("ForwardSupport done (B: both ever ready)")
            return True

        # C. 超时
        if self._start_time is not None:
            elapsed = self.ai.time - self._start_time
            if elapsed > _TASK_TIMEOUT_S:
                logger.info("ForwardSupport done (C: timeout %.0fs)", elapsed)
                return True

        # D. worker 死太多
        if self._worker_death_count >= _MAX_WORKER_DEATHS:
            logger.info("ForwardSupport done (D: %d worker deaths)", self._worker_death_count)
            return True

        # E. 主力已出门 —— 通过 knowledge.vibecraft.combat_intent_override 推断
        try:
            intent = getattr(self.ai.knowledge.vibecraft, "combat_intent_override", None)
            if intent == "attack":
                logger.info("ForwardSupport done (E: main army attacking)")
                return True
        except Exception:
            pass

        return False

    # ------------------------------------------------------------------
    # 主 execute
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        if self._completed:
            return True

        # —— 1. 第一次 execute：初始化 ——
        if self._start_time is None:
            self._start_time = self.ai.time
        if self.proxy_location is None:
            self.proxy_location = self._pick_proxy_location()
            if self.proxy_location is None:
                # 选不出来，跳过此帧再试
                return False
            self.hide_location = self.proxy_location.towards(self.ai.start_location, 8)

        # —— 2. 完成判定（5 重 OR）——
        if self._is_done():
            self._release_worker()
            self._completed = True
            return True

        try:
            # —— 3. worker 管理 ——
            worker = self._get_proxy_worker()
            if worker is None and self.proxy_worker_tag is not None:
                # 上一次有 tag，现在 by_tag 没了 → 死了
                self._worker_death_count += 1
                self.proxy_worker_tag = None
                if self._worker_death_count >= _MAX_WORKER_DEATHS:
                    return False  # 下一帧 _is_done 触发 D 终止

            if worker is None:
                worker = self._assign_new_worker()
            if worker is None:
                return False  # 没农民可用

            # 保命评估
            hp_max = worker.shield_max + worker.health_max
            hp_now = worker.shield + worker.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < _RETREAT_RATIO:
                self.retreating = True
                logger.debug("ForwardSupport retreating (hp=%.2f)", ratio)
            elif self.retreating and ratio > _REENGAGE_RATIO:
                self.retreating = False
                logger.debug("ForwardSupport re-engaging (hp=%.2f)", ratio)

            if self.retreating and self.hide_location is not None:
                if worker.distance_to(self.hide_location) > 4:
                    worker.move(self.hide_location)
                return False

            # —— 4. 下一步动作 —— 完全基于游戏实际状态（无 boolean flag）——
            py_state = self._building_state(UnitTypeId.PYLON)
            bg_state = self._building_state(UnitTypeId.GATEWAY)

            # 没 PYLON 也没在 build → 造 PYLON
            if py_state == "none":
                if self.ai.can_afford(UnitTypeId.PYLON):
                    # find_placement 自动找邻近合法 2x2 placement(避开占地建筑/矿块)
                    # 历史:直接 worker.build(proxy_location) 在 placement fail 时 sc2
                    # 拒绝,worker.orders 不会有 PROTOSSBUILD_PYLON → 每帧重发都 fail →
                    # 92s timeout 0 个 PYLON。
                    place = await self._safe_find_placement(UnitTypeId.PYLON, self.proxy_location)
                    if place is not None:
                        logger.info(
                            "ForwardSupport build PYLON at (%.1f, %.1f)",
                            place.x,
                            place.y,
                        )
                        worker.build(UnitTypeId.PYLON, place)
                    elif worker.is_idle:
                        worker.move(self.proxy_location)
                elif worker.is_idle:
                    worker.move(self.proxy_location)  # 走过去等钱
                return False

            # PYLON ready 但 GATEWAY none → 造 GATEWAY
            if py_state == "ready" and bg_state == "none":
                if self.ai.can_afford(UnitTypeId.GATEWAY):
                    # 在 PYLON 附近找位置造 BG（psi matrix 内）
                    py_tag = self._proxy_tags.get(UnitTypeId.PYLON)
                    py_struct = (
                        self.ai.structures.find_by_tag(py_tag) if py_tag is not None else None
                    )
                    if py_struct is not None:
                        # GATEWAY 放 PYLON 后方(towards 自家)而非前方(towards 敌方),
                        # 让 PYLON 当屏障。前方放法实测(game_20260518_043040)GATEWAY
                        # 修了 ~10s worker 就被敌方探机/zealot 打死。
                        bg_near = py_struct.position.towards(self.ai.start_location, 3)
                        # find_placement 找 3x3 合法 placement(psi matrix 内 + 不撞)
                        bg_pos = await self._safe_find_placement(UnitTypeId.GATEWAY, bg_near)
                        if bg_pos is not None:
                            logger.info(
                                "ForwardSupport build GATEWAY at (%.1f, %.1f)",
                                bg_pos.x,
                                bg_pos.y,
                            )
                            worker.build(UnitTypeId.GATEWAY, bg_pos)
                elif worker.is_idle:
                    py_tag = self._proxy_tags.get(UnitTypeId.PYLON)
                    py_struct = (
                        self.ai.structures.find_by_tag(py_tag) if py_tag is not None else None
                    )
                    if py_struct is not None:
                        worker.move(py_struct.position)
                return False

            # PYLON ordering / in_progress 中或 GATEWAY ordering / in_progress 中 → 等着
            if worker.is_idle and self.proxy_location is not None:
                worker.move(self.proxy_location)
        except Exception as exc:
            logger.warning("ForwardSupport execute failed: %s", exc)

        return False
