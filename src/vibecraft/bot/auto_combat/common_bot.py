"""VibeCraftBotBase：三族 bot 共享的 lifecycle / 多路复用 / EventBus 基类。

结构：A 抽象基类 + B 工厂函数。

继承层次：
    _VibeCraftProtossBot / _VibeCraftZergBot / _VibeCraftTerranBot
        → VibeCraftBotBase
        → sharpy KnowledgeBot

race-agnostic 部分（本文件）：
    - lifecycle hook 转发（11 个 _publish_xxx helper）
    - EventBus 初始化
    - down_q 消费 + camera drain + minimap 推送
    - tactics 节流 + hang watchdog
    - refresh_llm_controlled_roles / is_vibecraft_controlled
    - _SharpyFacadeBase 类

race-specific（子类实现）：
    - EXCLUDE_FROM_ARMY ClassVar（set[UnitTypeId]）
    - DEFAULT_OPENING_ID ClassVar（str）
    - create_plan() → BuildOrder
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue as queue_module
import sys
from pathlib import Path
from typing import Any, ClassVar

from vibecraft.bot.auto_combat.idle_worker_rescue import rescue_idle_workers
from vibecraft.bot.event_bus import Event, EventBus, EventKind
from vibecraft.bot.named_spot import NamedSpotRegistry
from vibecraft.bot.watchdog import HangWatchdog
from vibecraft.i18n import t as _i18n_t

logger = logging.getLogger(__name__)

# 玩家喊"放坑道虫"时给的强制投放时限:这段时间内 `_BuildNydusCanalAtEnemy` 无视拉黑、
# 按 COMMIT 放宽窗口阈值,尽快把虫下出去(玩家明确要求 > bot 自己的保守判断)。
_NYDUS_FORCE_DROP_WINDOW_S: float = 60.0


def _is_nydus_worm_ability(ability_id: str) -> bool:
    """LLM 给的 ability 名是不是在指"放坑道虫"。

    真名只有 `BUILD_NYDUSWORM` 一个,但 LLM 经常自造(真局里给的是
    `NYDUSWORMLOCATION_NYDUSNETWORK`)。这里按关键词宽松匹配,别让玩家的指令因为一个
    拼错的枚举名就静默失效。
    """
    a = (ability_id or "").upper()
    return "NYDUSWORM" in a or ("NYDUS" in a and ("BUILD" in a or "LOCATION" in a))


# WP-A debug draw 线宽模拟：SC2 的 debug_box2_out/sphere_out 无线宽参数，
# 画 N 条紧贴的同心轮廓线模拟"粗线"（间距 _DEBUG_THICK_STEP，远小于之前 0.6/0.85
# 的双框间距 → 视觉上融成一条粗线，不是多个分开的框）。手机上也看得清。
_DEBUG_THICK_PASSES: int = 3
_DEBUG_THICK_STEP: float = 0.05
# 框/环半径跟随单位碰撞半径(航母/母舰大 → 框也大;探机/枪兵小 → 框小)。
# 实际半径 = max(_DEBUG_MIN_RADIUS, unit.radius + _DEBUG_RADIUS_MARGIN),再叠 3 层线宽。
# 2026-06-06 用户:之前固定 0.7,航母(radius~1.25)框比单位还小、看不清。
# 余量让框/环画在单位边缘外一圈;下限保证小单位仍有可见框。
_DEBUG_RADIUS_MARGIN: float = 0.35
_DEBUG_MIN_RADIUS: float = 0.6
# 游戏内标签字号(team1/team2 + attack/standby 等任务名)。2026-06-06 用户:原 14 太小
# 看不清,放大到 40。
_DEBUG_LABEL_SIZE: int = 40

# 出兵集结点专属环 + 竖线参数(2026-06-10 用户):
# - 6 层同心环(原来跟编队一样 3 层,看着像一团),层数翻倍更醒目;
# - 层距 = 编队层距的 2 倍(_DEBUG_THICK_STEP×2),6 层从 base 向外铺开能分辨;
# - 竖线接近无限高(SC2 地图 z 约 0-15,拉到 1000 ≈ 顶天),地图大范围都能看到集结点。
_RALLY_RING_PASSES: int = 6
_RALLY_RING_STEP: float = _DEBUG_THICK_STEP * 2
_RALLY_RING_BASE: float = 1.6  # 最内环半径
_RALLY_PILLAR_HEIGHT: float = 1000.0

# -----------------------------------------------------------------------
# EventBus publishing helpers（race-agnostic，三族共享）
# -----------------------------------------------------------------------


def _publish_unit_created(bot_self: Any, unit: Any) -> None:
    owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_CREATED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner=owner,
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_unit_destroyed(bot_self: Any, unit_tag: int) -> None:
    enemy_dict: dict[int, Any] = getattr(bot_self, "_enemy_units_dict", {})
    own_dict: dict[int, Any] = getattr(bot_self, "_own_units_dict", {})
    unit = enemy_dict.get(unit_tag) or own_dict.get(unit_tag)
    owner: str | None = None
    unit_type: str | None = None
    position: tuple[float, float] | None = None
    area: str | None = None
    if unit is not None:
        owner = "own" if getattr(unit, "alliance", 0) == 1 else "enemy"
        unit_type = str(unit.type_id)
        position = (float(unit.position.x), float(unit.position.y))
        named_spots = getattr(bot_self, "named_spots", None)
        if named_spots is not None:
            try:
                area = named_spots.closest_named_spot(unit.position, bot_self)
            except Exception:
                area = None
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_DESTROYED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit_tag, "unit_obj": unit, "area": area},
            owner=owner,
            unit_tag=unit_tag,
            unit_type=unit_type,
            position=position,
        )
    )


def _publish_unit_type_changed(bot_self: Any, unit: Any, previous_type: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_TYPE_CHANGED,
            ts=float(bot_self.time),
            payload={
                "unit_tag": unit.tag,
                "previous_type": str(previous_type),
                "current_type": str(unit.type_id),
            },
            owner="own" if getattr(unit, "alliance", 0) == 1 else "enemy",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
        )
    )


def _publish_building_started(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.BUILDING_STARTED,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="own",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_building_complete(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.BUILDING_COMPLETE,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="own",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_upgrade_complete(bot_self: Any, upgrade: Any) -> None:
    upgrade_name = getattr(upgrade, "name", None) or str(upgrade)
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UPGRADE_COMPLETE,
            ts=float(bot_self.time),
            payload={"upgrade_id": upgrade_name},
            owner="own",
        )
    )


def _publish_unit_took_damage(bot_self: Any, unit: Any, amount: Any) -> None:
    area: str | None = None
    named_spots = getattr(bot_self, "named_spots", None)
    if named_spots is not None:
        try:
            area = named_spots.closest_named_spot(unit.position, bot_self)
        except Exception:
            area = None
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.UNIT_TOOK_DAMAGE,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "amount": float(amount), "area": area},
            owner="own",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_enemy_unit_entered_vision(bot_self: Any, unit: Any) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.ENEMY_UNIT_ENTERED_VISION,
            ts=float(bot_self.time),
            payload={"unit_tag": unit.tag, "unit_obj": unit},
            owner="enemy",
            unit_tag=unit.tag,
            unit_type=str(unit.type_id),
            position=(float(unit.position.x), float(unit.position.y)),
        )
    )


def _publish_enemy_unit_left_vision(bot_self: Any, unit_tag: int) -> None:
    bot_self.event_bus.publish(
        Event(
            kind=EventKind.ENEMY_UNIT_LEFT_VISION,
            ts=float(bot_self.time),
            payload={"unit_tag": unit_tag},
            owner="enemy",
            unit_tag=unit_tag,
        )
    )


def _make_event_publisher_placeholder() -> None:
    """占位：让单测可以 from vibecraft.bot.auto_combat.protoss.bot import _make_event_publisher 不报错。

    plan P1.0b 测试 fixture 里有 _make_event_publisher 的 import，实际逻辑由各
    _publish_xxx 函数承担，此处仅作标记性导出。
    """


# -----------------------------------------------------------------------
# vendor path 注入（与 protoss/bot.py 保持一致；两文件都可独立调用）
# -----------------------------------------------------------------------
_VENDOR_SHARPY = Path(__file__).parents[4] / "vendor" / "sharpy"


def _ensure_sharpy_on_path() -> None:
    """把 vendor/sharpy 加进 sys.path + 修正 config.get_config 路径（幂等）。"""
    target = str(_VENDOR_SHARPY)
    if target not in sys.path:
        sys.path.insert(0, target)

    from configparser import ConfigParser

    import config as _sharpy_config

    if getattr(_sharpy_config.get_config, "_vibecraft_patched", False):  # type: ignore[attr-defined]
        return

    def _patched_get_config(local: bool = True) -> ConfigParser:
        paths = [_VENDOR_SHARPY / "config.ini"]
        if local:
            paths.append(_VENDOR_SHARPY / "config-local.ini")
        if any(p.is_file() for p in paths):
            cfg = ConfigParser()
            cfg.read([str(p) for p in paths])
            return cfg
        raise ValueError(f"sharpy config 找不到: {paths}")

    _patched_get_config._vibecraft_patched = True  # type: ignore[attr-defined]
    _sharpy_config.get_config = _patched_get_config  # type: ignore[attr-defined]


# -----------------------------------------------------------------------
# _SharpyFacadeBase：Sc2Facade 的 sharpy 实现（race-agnostic 部分）
# -----------------------------------------------------------------------


def _make_sharpy_facade_base_class() -> type:
    """懒加载：在 sharpy 已注入 sys.path 后才 import BotState/UnitRole。"""
    from vibecraft.bot.facade import BotState, UnitRole

    def _log_move_camera_done(task: Any) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("move_camera_task_failed: %s", exc, exc_info=exc)

    class _SharpyFacadeBase:
        """Sc2Facade 的 sharpy 实现（三族共享基类）。

        camera 操作暂存模式（ADR 0008）：move_camera / follow_unit **不直接**发协议，
        只暂存最新目标点。on_step 末尾调 drain_pending_actions() 在 step await 链内串行发出。

        M4: LLM_CONTROLLED role 隔离。set_unit_role(tag, LLM_CONTROLLED) 同时写入
        bot._llm_controlled_tags，每 step 开头 refresh_llm_controlled_roles() 重新声明
        Reserved role，防止 sharpy UnitRoleManager.update() 每帧清空 had_task_set 后丢失状态。
        """

        def __init__(self, bot: Any) -> None:
            self.bot = bot
            self._pending_camera_point: tuple[float, float] | None = None
            # 代理建造队列:(probe_tag, structure_type, point, cache_key)。order_probe_build 入队,
            # drain_pending_actions(async)用 find_placement 找目标点附近最近合法位再 build。
            self._pending_probe_builds: list[tuple[int, str, tuple[float, float], object]] = []
            # 代理建造落点缓存:cache_key(卡 id)→ 第一次 find_placement 的落点。同卡反复重发复用,
            # 不每帧重算(远程代理建造每帧重发压过 sharpy 抢人,落点必须稳)。settle 时由 director 清。
            self._proxy_place_cache: dict[object, tuple[float, float]] = {}
            # 2026-06-07 玩家"在X刷N兵"折跃请求:key(did:兵种) → {unit_type, remaining, target}。
            # drain_pending_actions 每帧找最近能量场折跃,折满 → 进 _done_warps(持久,供 warp_status 查)。
            self._pending_warps: dict[str, dict[str, object]] = {}
            self._done_warps: set[str] = set()

        # ---- 写 -------------------------------------------------------

        def set_build(self, build_name: str) -> None:
            logger.info("set_build switched to %s", build_name)
            self.bot.active_recipe = build_name

        def set_production_override(
            self,
            unit_type: str,
            count: int,
            building_tag: int | None = None,
        ) -> None:
            pass

        def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
            pass

        def set_expansion_override(self, target_count: int | None) -> None:
            # None = 撤销封顶，回剧本默认 expansion_cap
            self.bot.knowledge.vibecraft.expansion_cap_override = target_count

        def nearest_expansion(self, point: tuple[float, float]) -> tuple[float, float] | None:
            """偷矿落点吸附：返回离 point 最近的 expansion location（有矿可开矿点）。

            偷矿 Nexus 是采矿基地，必须落在合法 expansion；用玩家点原始坐标会落在
            无矿/不可建处，建造被 SC2 拒（real-test orders_after=[]）。无数据返回 None。
            """
            try:
                from sc2.position import Point2

                p2 = Point2(point)
                exps = list(self.bot.expansion_locations_list)
                if not exps:
                    return None
                best = min(exps, key=lambda e: e.distance_to(p2))
                return (float(best.x), float(best.y))
            except Exception as exc:
                logger.debug("nearest_expansion fail: %s", exc)
                return None

        def register_stealth_townhalls(self, tags: set[int]) -> None:
            """偷矿 FENCE：整体覆盖 stealth_townhall_tags（Manager 每 tick 传全集）。

            DistributeWorkers.generate_worker_queue 读此集合排除 stealth 基地（防主矿倒灌）。
            Expand.execute 同样读此集合（stealth 基地不计入自然扩张账）。
            """
            self.bot.knowledge.vibecraft.stealth_townhall_tags = set(tags)
            logger.info("STEALTHTRACE register_stealth_townhalls count=%d tags=%s", len(tags), tags)

        def ensure_units_reserved(self, tags: set[int]) -> None:
            """偷矿农民防外流：并入 _llm_controlled_tags，_refresh_llm_controlled_roles
            每帧（DistributeWorkers 之前）把它们 re-Reserve → 不被当空闲工人拉回主矿。"""
            try:
                self.bot._llm_controlled_tags |= {int(t) for t in tags}
            except Exception as exc:
                logger.debug("ensure_units_reserved fail: %s", exc)

        def register_stealth_workers(self, tags: set[int]) -> None:
            """整体覆盖写 stealth_worker_tags → ScoutWorker 等排除偷矿农民（不受 cache race）。"""
            try:
                self.bot.knowledge.vibecraft.stealth_worker_tags = {int(t) for t in tags}
            except Exception as exc:
                logger.debug("register_stealth_workers fail: %s", exc)

        def register_stealth_pending(self, count: int) -> None:
            """写"在建偷矿基地数"到 SNS → Expand 把它算进基地数（延后自己开分矿）。"""
            try:
                self.bot.knowledge.vibecraft.stealth_pending_base_count = int(count)
            except Exception as exc:
                logger.debug("register_stealth_pending fail: %s", exc)

        def set_stealth_chrono_reserved(self, tags: set[int]) -> None:
            """整体覆盖星空加速预留集合 → bot ChronoUnit 不拿这些 Nexus 当能量源。"""
            try:
                _new = {int(t) for t in tags}
                _old = getattr(self.bot.knowledge.vibecraft, "stealth_chrono_reserved_tags", set())
                if _new != _old:
                    logger.info("STEALTHTRACE chrono_reserved_set tags=%s", _new)
                self.bot.knowledge.vibecraft.stealth_chrono_reserved_tags = _new
            except Exception as exc:
                logger.debug("set_stealth_chrono_reserved fail: %s", exc)

        def cast_chrono_on_nexus(self, nexus_tag: int) -> bool:
            """偷矿基地成长期自我星空加速：Nexus 用自己能量给自己加速产农民。"""
            try:
                from sc2.ids.ability_id import AbilityId
                from sc2.ids.buff_id import BuffId

                nexus = self.bot.knowledge.unit_cache.by_tag(nexus_tag)
                if nexus is None:
                    return False
                if nexus.energy < 50 or not nexus.orders:
                    return False  # 能量不够 / 没在产农民
                if nexus.has_buff(BuffId.CHRONOBOOSTENERGYCOST):
                    return False  # 已被加速，别浪费
                self.bot.do(nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, nexus))
                logger.info(
                    "STEALTHTRACE chrono_self nexus=%d energy=%.0f", nexus_tag, nexus.energy
                )
                return True
            except Exception as exc:
                logger.debug("cast_chrono_on_nexus fail nexus=%d: %s", nexus_tag, exc)
                return False

        def train_probe_at(self, nexus_tag: int) -> bool:
            """在指定 Nexus 训练一个农民（偷矿本地产线）。

            条件：Nexus ready + 空闲(orders 为空) + can_afford(PROBE)。
            返回 True = 成功下令；False = 条件不满足（不抛异常）。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                nexus = self.bot.knowledge.unit_cache.by_tag(nexus_tag)
                if nexus is None:
                    logger.warning("train_probe_at: nexus tag=%d not found in cache", nexus_tag)
                    return False
                if not nexus.is_ready:
                    return False
                if nexus.orders:  # already training something
                    return False
                if not self.bot.can_afford(UnitTypeId.PROBE):
                    return False
                nexus.train(UnitTypeId.PROBE)
                logger.info("STEALTHTRACE train_probe_at nexus_tag=%d", nexus_tag)
                return True
            except Exception as exc:
                logger.warning("train_probe_at fail nexus_tag=%d err=%s", nexus_tag, exc)
                return False

        def order_worker_gather(self, worker_tag: int, near_point: tuple[float, float]) -> None:
            """命令指定农民采 near_point 附近最近的矿（偷矿本地产线：新认领农民就地采矿）。

            Reserved 农民不会被 DistributeWorkers 自动派矿，必须显式下令。
            """
            try:
                from sc2.position import Point2

                worker = self.bot.knowledge.unit_cache.by_tag(worker_tag)
                if worker is None:
                    logger.warning("order_worker_gather: worker tag=%d not found", worker_tag)
                    return
                p2 = Point2(near_point)
                minerals = self.bot.mineral_field.closer_than(10.0, p2)
                if not minerals:
                    logger.warning(
                        "order_worker_gather: no mineral near (%.1f, %.1f)",
                        near_point[0],
                        near_point[1],
                    )
                    return
                mineral = minerals.closest_to(p2)
                worker.gather(mineral)
                logger.info(
                    "STEALTHTRACE gather_ordered worker=%d mineral=%d near=(%.1f,%.1f)",
                    worker_tag,
                    mineral.tag,
                    near_point[0],
                    near_point[1],
                )
            except Exception as exc:
                logger.warning("order_worker_gather fail worker_tag=%d err=%s", worker_tag, exc)

        def find_stealth_geysers(
            self, point: tuple[float, float], radius: float
        ) -> list[tuple[int, tuple[float, float]]]:
            """WP4b：返回 point 半径内、还没建 assimilator 的 geyser 列表。

            过滤条件：gas_buildings.closer_than(1.0, g.position) 非空 → 跳过（已建/建中）。
            """
            try:
                from sc2.position import Point2

                p2 = Point2(point)
                result = []
                for g in self.bot.vespene_geyser.closer_than(radius, p2):
                    # 已有己方 gas building（含建中）→ 跳过
                    if self.bot.gas_buildings.closer_than(1.0, g.position):
                        continue
                    result.append((int(g.tag), (float(g.position.x), float(g.position.y))))
                return result
            except Exception as exc:
                logger.debug("find_stealth_geysers fail: %s", exc)
                return []

        def order_probe_build_gas(self, probe_tag: int, geyser_tag: int) -> None:
            """WP4b：命令 probe 在 geyser 上建 assimilator。"""
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                worker = self.bot.knowledge.unit_cache.by_tag(probe_tag)
                if worker is None:
                    logger.warning("order_probe_build_gas: probe tag=%d not found", probe_tag)
                    return
                geyser = self.bot.vespene_geyser.find_by_tag(geyser_tag)
                if geyser is None:
                    logger.warning(
                        "order_probe_build_gas: geyser tag=%d not found in vespene_geyser",
                        geyser_tag,
                    )
                    return
                worker.build(UnitTypeId.ASSIMILATOR, geyser)
                logger.info(
                    "STEALTHTRACE gas_build_issued probe=%d geyser=%d",
                    probe_tag,
                    geyser_tag,
                )
            except Exception as exc:
                logger.warning(
                    "order_probe_build_gas fail probe=%d geyser=%d err=%s",
                    probe_tag,
                    geyser_tag,
                    exc,
                )

        def find_stealth_gas_buildings(
            self, point: tuple[float, float], radius: float
        ) -> list[tuple[int, int, int]]:
            """WP4b：返回 point 半径内 ready assimilator 的 (tag, assigned, ideal) 列表。"""
            try:
                from sc2.position import Point2

                p2 = Point2(point)
                result = []
                for g in self.bot.gas_buildings.ready.closer_than(radius, p2):
                    result.append((int(g.tag), int(g.assigned_harvesters), int(g.ideal_harvesters)))
                return result
            except Exception as exc:
                logger.debug("find_stealth_gas_buildings fail: %s", exc)
                return []

        def order_worker_gather_gas(self, worker_tag: int, gas_building_tag: int) -> None:
            """WP4b：命令 worker 采指定 assimilator 的气。"""
            try:
                worker = self.bot.knowledge.unit_cache.by_tag(worker_tag)
                if worker is None:
                    logger.warning("order_worker_gather_gas: worker tag=%d not found", worker_tag)
                    return
                gas = self.bot.knowledge.unit_cache.by_tag(gas_building_tag)
                if gas is None:
                    logger.warning(
                        "order_worker_gather_gas: gas building tag=%d not found",
                        gas_building_tag,
                    )
                    return
                worker.gather(gas)
                logger.info(
                    "STEALTHTRACE gas_gather_ordered worker=%d gas_building=%d",
                    worker_tag,
                    gas_building_tag,
                )
            except Exception as exc:
                logger.warning("order_worker_gather_gas fail worker=%d err=%s", worker_tag, exc)

        def gas_worker_drifted(self, worker_tag: int, gas_tags: set[int]) -> bool:
            """采气农民是否漂走（被登记采气、实际没在采气循环）→ 需重新焊回气上。

            False（别动）：不在 cache（钻进 assim）/ carrying vespene（拎气回家）/ order
            target 在 gas_tags（正在采气）。其余（采矿/idle/采别的）→ True（漂走）。
            """
            try:
                w = self.bot.workers.find_by_tag(worker_tag)
                if w is None:
                    return False  # 钻进 assim 暂时消失 → 别动
                if getattr(w, "is_carrying_vespene", False):
                    return False  # 拎气回基地，采气循环中
                orders = getattr(w, "orders", None)
                if orders:
                    tgt = getattr(orders[-1], "target", None)
                    if isinstance(tgt, int) and tgt in gas_tags:
                        return False  # 正在采该气矿
                return True  # 采矿 / idle / 采别的 → 漂走了
            except Exception as exc:
                logger.debug("gas_worker_drifted fail worker=%d err=%s", worker_tag, exc)
                return False

        def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
            try:
                from vibecraft.bot.auto_combat.common import build_role_map

                role_map = build_role_map()
                task = role_map[role]
                unit = self.bot.knowledge.unit_cache.by_tag(unit_tag)
                if unit is None:
                    logger.warning(
                        "set_unit_role: tag=%d not found in cache (role=%s)", unit_tag, role
                    )
                    return
                self.bot.knowledge.roles.set_task(task, unit)
                # _llm_controlled_tags 是"每帧 re-Reserve"的来源(见 _refresh_llm_controlled_roles)。
                # claim=加入(从此 sharpy 不碰它);任何非 LLM_CONTROLLED role=移除(才真的还给 bot)。
                # 还单位走 release_unit_role(无条件 discard)更稳 —— 别只 set role 不 discard。
                if role == UnitRole.LLM_CONTROLLED:
                    self.bot._llm_controlled_tags.add(unit_tag)
                    logger.info("unit_claimed tag=%d added to _llm_controlled_tags", unit_tag)
                else:
                    self.bot._llm_controlled_tags.discard(unit_tag)
            except Exception as exc:
                logger.warning("set_unit_role failed tag=%d role=%s err=%s", unit_tag, role, exc)

        def release_unit_role(self, unit_tag: int) -> None:
            """LLM_CONTROLLED 让位的反向操作:把单位还给 sharpy 自由调度。

            **核心(2026-06-07 玩家"取消集中指令后虚空仍不听全军进攻"根因)**:必须**无条件**
            从 _llm_controlled_tags 移除 —— 否则 _refresh_llm_controlled_roles 每帧把它
            re-Reserve 回 UnitTask.Reserved,PlanZoneAttack 的 free_units 永远拿不到,
            指令取消也放不掉、永久锁死。set IDLE → UnitTask.Idle,sharpy UnitRoleManager
            下帧重新接管。
            (Sc2Facade 是 Protocol 不强制实现;本方法曾漏,而单测一直用 FakeFacade(有此方法)
             → 单测绿、真局炸。test_facade_release_unit_role.py 的 audit 现在挡这类偏差。)
            """
            # 无条件移除(即便单位已死/不在 cache),这是停止每帧 re-Reserve 的关键
            tags_set = getattr(self.bot, "_llm_controlled_tags", None)
            if tags_set is not None:
                tags_set.discard(unit_tag)
            try:
                from vibecraft.bot.auto_combat.common import build_role_map

                unit = self.bot.knowledge.unit_cache.by_tag(unit_tag)
                if unit is None:
                    return
                self.bot.knowledge.roles.set_task(build_role_map()[UnitRole.IDLE], unit)
            except Exception as exc:
                logger.warning("release_unit_role failed tag=%d err=%s", unit_tag, exc)

        def _resolve_target_point(self, target: dict[str, object] | None) -> Any:
            if target is None:
                return None
            kind = target.get("kind")
            if kind == "named_spot":
                name = target.get("named_spot")
                if name:
                    registry = getattr(self.bot, "named_spots", None)
                    if registry is not None:
                        return registry.resolve(str(name), self.bot)
            elif kind == "point":
                pt = target.get("point")
                if pt:
                    from sc2.position import Point2

                    return Point2(pt)
            elif kind == "camera":
                # 2026-06-08 修(玩家报代理建造 standby 农民被拉扯根因之一):camera 目标在
                # Director 侧已被 _inject_camera_point 注入了镜头世界坐标 point,但 kind 仍是
                # camera。这里之前不认 camera → 落空 → "unresolvable target kind=camera" →
                # standby/move 首次下发失败。用注入的 point 解析。
                pt = target.get("point")
                if pt:
                    from sc2.position import Point2

                    return Point2(pt)
            elif kind == "unit_tag":
                tag = target.get("unit_tag")
                if tag:
                    tag_int = int(str(tag))
                    u = self.bot.units.by_tag(tag_int)
                    if u:
                        return u.position
                    u2 = self.bot.enemy_units.by_tag(tag_int)
                    if u2:
                        return u2.position
            return None

        def _closest_mineral_for_gather(self, unit: Any, target_point: Any) -> Any:
            """为 gather verb 选一块矿(mineral field)返回,取不到则 None。

            优先级(2026-07-20 F83):
              1. 目标点附近的矿(玩家在小地图/镜头点了具体矿区 → 就近那块);
              2. 离农民最近的己方基地附近的矿(玩家只说"农民回去采矿"没给点 →
                 回自家基地采,别跑去中立/敌方矿);
              3. 全图离目标点最近的矿(兜底,别 silently 返回 None)。
            全图无矿 → None(上层 fallback move)。
            """
            try:
                minerals = getattr(self.bot, "mineral_field", None)
                if not minerals:
                    return None
                # 1. 目标点附近(能量场半径 ~10 内)的矿
                near = minerals.closer_than(10.0, target_point)
                if near:
                    return near.closest_to(target_point)
                # 2. 离农民最近的己方基地附近的矿
                townhalls = getattr(self.bot, "townhalls", None)
                if townhalls:
                    th = townhalls.closest_to(unit.position)
                    near_th = minerals.closer_than(10.0, th.position)
                    if near_th:
                        return near_th.closest_to(th.position)
                # 3. 全图离目标点最近的矿(兜底)
                return minerals.closest_to(target_point)
            except Exception as exc:
                logger.warning("_closest_mineral_for_gather fail: %s", exc)
                return None

        def execute_unit_action(
            self,
            unit_tag: int,
            verb: str,
            target: dict[str, object] | None = None,
            ability_id: str | None = None,
        ) -> None:
            target_point = self._resolve_target_point(target)
            if target_point is None:
                logger.warning(
                    "execute_unit_action: unresolvable target %r (verb=%s)", target, verb
                )
                return

            if unit_tag == 0:
                unit = None
                for u in self.bot.units:
                    if u.is_idle:
                        if str(u.type_id.name).casefold() == "probe":
                            unit = u
                            break
                        if unit is None:
                            unit = u
            else:
                unit = self.bot.units.by_tag(unit_tag)

            if unit is None:
                logger.warning("execute_unit_action: no unit tag=%d", unit_tag)
                return

            if verb in ("attack_move", "attack"):
                unit.attack(target_point)
            elif verb == "gather":
                # 2026-07-20 F83 根因修:玩家"闲置农民采矿 / 农民回去采矿"→ LLM 解析成
                # unit_claim(gather)。旧代码 gather verb 落到下面 else 只 unit.move(农民走
                # 过去、从不发采矿命令);而 _apply_unit_claim 下令前已把农民设 LLM_CONTROLLED
                # (=sharpy Reserved)→ DistributeWorkers 不再自动派这农民采矿 + claim 又只 move
                # → 农民彻底卡 Reserved 闲置(想采矿反被锁死不采矿)。这里为 gather verb 发**真正
                # 的采矿命令**:找矿(mineral field)→ unit.gather(patch)。
                patch = self._closest_mineral_for_gather(unit, target_point)
                if patch is not None:
                    unit.gather(patch)
                    logger.info(
                        "execute_unit_action gather: worker=%d -> mineral=%d",
                        unit.tag,
                        patch.tag,
                    )
                else:
                    # 全图无矿(极端情况)→ fallback move,别 silently 啥都不做
                    logger.warning(
                        "execute_unit_action gather: no mineral field found, fallback move (tag=%d)",
                        unit.tag,
                    )
                    unit.move(target_point)
            else:
                unit.move(target_point)

        def cast_unit_ability(
            self,
            unit_tag: int,
            ability_id: str,
            target: dict[str, object] | None = None,
        ) -> None:
            """对指定 tag 的单位/建筑下 ability（如 SALVAGEBUNKER_SALVAGE）。

            建筑在 structures（python-sc2 把 is_structure 的放 structures），
            先查 structures 再查 units。ability_id 字符串转 AbilityId 枚举；
            无 target → do(unit(ab))；有 target → 解析 Point2 后 do(unit(ab, pt))。
            找不到 unit / 非法 ability → log warning return（静默吞错但记日志）。
            """
            from sc2.ids.ability_id import AbilityId

            # 建筑优先在 structures 查（find_by_tag 返回 Optional，不抛 KeyError）
            unit = self.bot.structures.find_by_tag(unit_tag)
            if unit is None:
                unit = self.bot.units.find_by_tag(unit_tag)
            if unit is None:
                logger.warning("cast_unit_ability: no unit/structure tag=%d", unit_tag)
                return

            try:
                ab = AbilityId[ability_id]
            except KeyError:
                logger.warning("cast_unit_ability: unknown ability_id=%r", ability_id)
                return

            target_point = self._resolve_target_point(target)
            if target is not None and target_point is None:
                logger.warning(
                    "cast_unit_ability: unresolvable target %r (ability=%s)",
                    target,
                    ability_id,
                )
                return

            try:
                # 根因（2026-06-19 真机验证）：python-sc2 `prevent_double_actions` 当
                # unit.orders==[] 时返回 None（隐式），filter() 视 None 为 falsy 丢掉
                # 该 UnitCommand，导致能力永不发到 SC2（地堡不消失）。
                # 修法：构造 UnitCommand 放进 bot._vibecraft_bypass_actions 列表，
                # 由 _tick_bot_channel 在 super().on_step() 之后串行
                # await bot._do_actions(bypass, prevent_double=False) 发出。
                # 不用 create_task（会触发 "Concurrent call to receive()" websocket 崩溃）。
                from sc2.unit_command import UnitCommand as _SC2UnitCmd

                cmd = _SC2UnitCmd(ab, unit, target_point, False)
                if not hasattr(self.bot, "_vibecraft_bypass_actions"):
                    self.bot._vibecraft_bypass_actions: list = []
                self.bot._vibecraft_bypass_actions.append(cmd)
                logger.info(
                    "cast_unit_ability: bypass_queued tag=%d ability=%s orders=%d game_time=%.1f",
                    unit_tag,
                    ability_id,
                    len(unit.orders),
                    float(self.bot.time),
                )
            except Exception as exc:
                logger.warning(
                    "cast_unit_ability: cast fail tag=%d ability=%s: %s",
                    unit_tag,
                    ability_id,
                    exc,
                )

        def get_unit_type_name(self, unit_tag: int) -> str | None:
            """返回 unit_tag 对应单位/建筑的 type_id 名称（全大写，如 "BUNKER"）。

            先在 structures 查（建筑走 structures 集合），再在 units 查（单位）。
            找不到（tag 不在 cache）→ 返回 None（不抛异常）。
            """
            unit = self.bot.structures.find_by_tag(unit_tag)
            if unit is None:
                unit = self.bot.units.find_by_tag(unit_tag)
            if unit is None:
                return None
            try:
                return str(unit.type_id.name)
            except Exception:
                return None

        def bunker_has_cargo(self, unit_tag: int) -> bool:
            """检查地堡是否有货舱乘员（has_cargo）。找不到 tag → False，不抛异常。"""
            unit = self.bot.structures.find_by_tag(unit_tag)
            if unit is None:
                unit = self.bot.units.find_by_tag(unit_tag)
            if unit is None:
                return False
            try:
                return bool(unit.has_cargo)
            except Exception:
                return False

        def load_bunker(self, bunker_tag: int, count: int) -> int:
            """找 count 个最近的、不在地堡里的己方 Marine 进入地堡，返回实际下令数。

            对每个目标 Marine 发 UnitCommand(SMART, marine, bunker) 走
            _vibecraft_bypass_actions 路径（绕过 prevent_double_actions 静默吞单）。
            """
            from sc2.ids.ability_id import AbilityId
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.unit_command import UnitCommand as _SC2UnitCmd

            bunker = self.bot.structures.find_by_tag(bunker_tag)
            if bunker is None:
                logger.warning("load_bunker: bunker tag=%d not found", bunker_tag)
                return 0

            # 当前在地堡里的 tag 集合（过滤已在里面的）
            try:
                cargo_tags: set[int] = set(bunker.passengers_tags)
            except Exception:
                cargo_tags = set()

            # 候选 Marine：不在货舱里的己方 Marine
            candidates = [u for u in self.bot.units(UnitTypeId.MARINE) if u.tag not in cargo_tags]
            if not candidates:
                logger.info("load_bunker: no available marines for bunker_tag=%d", bunker_tag)
                return 0

            # 按距离升序取前 count 个
            candidates.sort(key=lambda u: u.distance_to(bunker))
            to_load = candidates[:count]

            if not hasattr(self.bot, "_vibecraft_bypass_actions"):
                self.bot._vibecraft_bypass_actions = []

            loaded = 0
            for marine in to_load:
                try:
                    cmd = _SC2UnitCmd(AbilityId.SMART, marine, bunker, False)
                    self.bot._vibecraft_bypass_actions.append(cmd)
                    loaded += 1
                except Exception as exc:
                    logger.warning(
                        "load_bunker: marine tag=%d → bunker tag=%d fail: %s",
                        marine.tag,
                        bunker_tag,
                        exc,
                    )

            logger.info(
                "load_bunker: bunker_tag=%d requested=%d loaded=%d game_time=%.1f",
                bunker_tag,
                count,
                loaded,
                float(self.bot.time),
            )
            return loaded

        def get_unit_health_percentage(self, unit_tag: int) -> float | None:
            """返回 unit_tag 对应单位/建筑的血量百分比（0.0–1.0）。

            先在 structures 查，再在 units 查。找不到 → None。
            """
            unit = self.bot.structures.find_by_tag(unit_tag)
            if unit is None:
                unit = self.bot.units.find_by_tag(unit_tag)
            if unit is None:
                return None
            try:
                return float(unit.health_percentage)
            except Exception:
                return None

        def ensure_repair(self, target_tag: int, count: int) -> int:
            """确保 count 个 SCV 在修 target_tag 单位/建筑，返回实际派出数。

            满血（health_percentage >= 0.99）或找不到目标 → 返回 0 不派。
            找最近 count 个没在修它的己方 SCV，每个下 repair 命令。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            unit = self.bot.structures.find_by_tag(target_tag)
            if unit is None:
                unit = self.bot.units.find_by_tag(target_tag)
            if unit is None:
                return 0
            try:
                if getattr(unit, "health_percentage", 1.0) >= 0.99:
                    return 0
                scvs = self.bot.units(UnitTypeId.SCV)
                if not scvs:
                    return 0
                repairing_this = sum(
                    1
                    for w in scvs
                    if getattr(w, "is_repairing", False)
                    and getattr(w, "order_target", None) == unit.tag
                )
                need = count - repairing_this
                if need <= 0:
                    return repairing_this
                free = [w for w in scvs if not getattr(w, "is_repairing", False)]
                free.sort(key=lambda w: w.distance_to(unit))
                dispatched = 0
                for w in free[:need]:
                    try:
                        w.repair(unit)
                        dispatched += 1
                    except Exception:
                        pass
                return repairing_this + dispatched
            except Exception as exc:
                logger.warning("ensure_repair: tag=%d fail: %s", target_tag, exc)
                return 0

        def cast_chrono_boost_on_structure(
            self,
            structure_type: str,
            count: int = 1,
        ) -> int:
            """2026-05-25 用户:Nexus 释放 Chrono Boost(星空加速)到目标建筑。

            玩家说"给两个BF星空加速" → director 调此方法,structure_type="Forge"
            count=2。从有能量 ≥ 50 的 Nexus 中各 cast 1 次到不同 target 建筑。
            返回实际成功 cast 次数。
            """
            from sc2.ids.ability_id import AbilityId
            from sc2.ids.unit_typeid import UnitTypeId

            tid = getattr(UnitTypeId, structure_type.upper(), None)
            if tid is None:
                logger.warning(
                    "cast_chrono_boost_on_structure: unknown structure_type=%s",
                    structure_type,
                )
                return 0
            try:
                targets = list(self.bot.structures(tid).ready)
            except Exception as exc:
                logger.warning("chrono_boost: target structures lookup fail: %s", exc)
                return 0
            if not targets:
                logger.info(
                    "chrono_boost: no ready %s to cast on",
                    structure_type,
                )
                return 0
            # 找能量 ≥ 50 的 Nexus(chrono boost 消耗 50 能量)
            try:
                nexuses = [n for n in self.bot.structures(UnitTypeId.NEXUS).ready if n.energy >= 50]
            except Exception as exc:
                logger.warning("chrono_boost: nexus lookup fail: %s", exc)
                return 0
            if not nexuses:
                logger.info("chrono_boost: no nexus with energy >= 50")
                return 0
            success = 0
            # 取 min(count, len targets, len nexuses) 次 cast
            for i in range(min(count, len(targets), len(nexuses))):
                try:
                    self.bot.do(nexuses[i](AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, targets[i]))
                    success += 1
                except Exception as exc:
                    logger.warning(
                        "chrono_boost: cast fail nexus=%d target=%s: %s",
                        nexuses[i].tag,
                        targets[i].tag,
                        exc,
                    )
            logger.info(
                "chrono_boost: cast %d/%d on %s",
                success,
                count,
                structure_type,
            )
            return success

        def cast_ability_on_units(
            self,
            ability_id: str,
            unit_type: str | None = None,
            target_kind: str = "self",
            count: int | None = None,
            target_point: tuple[float, float] | None = None,
        ) -> int:
            """2026-05-30:对一批单位释放任意 ability。返回成功 cast 次数。

            主要场景:"所有电兵合成白球" → ability_id="MORPH_ARCHON",
            unit_type="HighTemplar", target_kind="self"。
            MORPH_ARCHON 特殊：2 个 HT 配对 cast；count 表示合多少个白球（每个需 2 HT）。
            其他 ability：每个单位单独 cast 一次；count 限定最多 cast 多少次。
            奇数 HT 时最后一个不会参与配对（不足 2 个）。
            """
            from sc2.ids.ability_id import AbilityId
            from sc2.ids.unit_typeid import UnitTypeId

            # ★ 玩家说"放坑道虫"(2026-07-26 真局):LLM 会把它解析成 cast_ability,并且 ability 名
            # 常常是编出来的(真局里给的是 NYDUSWORMLOCATION_NYDUSNETWORK,SC2 里没这个枚举)→
            # 旧路径只打一条 unknown ability 警告、cast 0 次,玩家看着毫无反应。
            # 而且放坑道虫本就不是"对单位放技能":它是 **NydusNetwork 的建筑能力 + 一个目标坐标**,
            # 由 `_BuildNydusCanalAtEnemy` 按视野/窗口挑点后下达。所以这里不硬凑 cast,而是把它翻译成
            # **玩家强制投放意图**——置一个带时限的 flag,由那个 act 读到后立刻按玩家意图去投
            # (无视拉黑 + 按 COMMIT 放宽窗口阈值)。
            if _is_nydus_worm_ability(ability_id):
                with contextlib.suppress(Exception):
                    now = float(self.bot.time)
                    self.bot.knowledge.vibecraft.nydus_force_drop_until = (
                        now + _NYDUS_FORCE_DROP_WINDOW_S
                    )
                    logger.info(
                        "cast_ability_on_units: '%s' → 玩家强制投放坑道虫,%.0fs 内按玩家意图下虫",
                        ability_id,
                        _NYDUS_FORCE_DROP_WINDOW_S,
                    )
                    return 1
                return 0

            ability_enum = getattr(AbilityId, ability_id.upper(), None)
            if ability_enum is None:
                logger.warning("cast_ability_on_units: unknown ability=%s", ability_id)
                return 0

            # 筛候选单位
            candidates = self.bot.units
            if unit_type:
                tid = getattr(UnitTypeId, unit_type.upper(), None)
                if tid is None:
                    logger.warning("cast_ability_on_units: unknown unit_type=%s", unit_type)
                    return 0
                candidates = candidates(tid).ready

            n_cast = 0
            if ability_enum == AbilityId.MORPH_ARCHON:
                # 两两配对：count = 希望合多少个白球，每个需 2 HT；None = 尽量多合
                units = list(candidates)
                max_pairs = count if count is not None else len(units) // 2
                for i in range(min(max_pairs, len(units) // 2)):
                    idx = i * 2
                    try:
                        units[idx](AbilityId.MORPH_ARCHON, units[idx + 1])
                        n_cast += 1
                    except Exception as exc:
                        logger.warning("cast_ability_on_units: merge archon fail: %s", exc)
            else:
                # 通用：每个单位单独 cast。target_point 给了 → 对点施放(如大舰 EFFECT_TACTICALJUMP
                # 传送回家落点)；否则自施放(archon/storm 等)。
                from sc2.position import Point2

                pt = Point2(target_point) if target_point is not None else None
                for u in candidates:
                    try:
                        if pt is not None:
                            u(ability_enum, pt)
                        else:
                            u(ability_enum)
                        n_cast += 1
                    except Exception as exc:
                        logger.warning("cast_ability_on_units: cast %s fail: %s", ability_id, exc)
                    if count is not None and n_cast >= count:
                        break
            logger.info(
                "cast_ability_on_units: ability=%s unit_type=%s cast=%d",
                ability_id,
                unit_type,
                n_cast,
            )
            return n_cast

        def set_build_location_override(
            self,
            structure_type: str,
            point: tuple[float, float],
        ) -> None:
            pass

        def set_engagement_stance(self, stance: str | None) -> None:
            # 2026-06-13 真机大坑修复：Director revoke_tactical 用 set_engagement_stance(None)
            # 清 stance,原实现 None 落到 else no-op → 玩家 × 防守/撤退后 stance_override
            # 永远卡住 → sharpy _should_attack 恒 False → bot 余生不再自主进攻
            # (实测日志:538s intent 清了 stance 钉死 "defend" 到终局)。
            # FakeFacade 只记录不判断,单测拦不住 —— facade 双实现坑的又一例。
            if stance is None or stance == "free":
                self.bot.knowledge.vibecraft.stance_override = None
            elif stance in ("defend", "hold", "retreat"):
                self.bot.knowledge.vibecraft.stance_override = stance
            else:
                logger.warning("set_engagement_stance: unknown stance %r, no-op", stance)

        def set_attack_target_override(self, point: tuple[float, float] | None) -> None:
            self.bot.knowledge.vibecraft.attack_target_override = point

        def set_combat_intent_override(self, intent: str | None) -> None:
            self.bot.knowledge.vibecraft.combat_intent_override = intent

        def set_attack_mode_override(self, mode: str | None) -> None:
            """2026-05-25 用户:UI 按钮"强制全体进攻"/"试探性进攻"。

            mode: "all_in" / "probe" / None。PlanZoneAttack 优先读此 flag,
            没 set 时回退 plan 默认 force_attack(4bg=True、1g_robo=False)。
            """
            self.bot.knowledge.vibecraft.attack_mode_override = mode

        def set_sustain_uncap_active(self, active: bool) -> None:
            """2026-05-27 Task #341:opening 完成超时后由 Director 调,启动 sustain uncap mode。"""
            self.bot.knowledge.vibecraft.sustain_uncap_active = active

        def set_mining_priority(self, priority: str | None) -> None:
            """2026-07-06 采矿策略：写入 knowledge.vibecraft.mining_priority，
            DistributeWorkers.execute patch 每帧读此字段覆写 min_gas/max_gas。

            priority: "mineral" / "gas" / None（None = 恢复剧本原始 min/max_gas）。
            """
            try:
                self.bot.knowledge.vibecraft.mining_priority = priority
                logger.info("set_mining_priority: %s", priority)
            except Exception as exc:
                logger.warning("set_mining_priority fail: %s", exc)

        def set_upgrade_target(self, family: str, level: int | None) -> None:
            """2026-07-07 攻防升级目标等级：写入 knowledge.vibecraft.upgrade_targets。

            family: 升级线族名（无 LEVEL 后缀），如 'PROTOSSGROUNDWEAPONS'。
            level: 0-3 = 手动封顶；None = 自动（pop key，还给 bot 自决）。
            vendor/sharpy tech.py::Tech.execute 封顶门读 upgrade_targets.get(family)。
            """
            try:
                _vbc = self.bot.knowledge.vibecraft
                _targets = getattr(_vbc, "upgrade_targets", None)
                if _targets is None:
                    _vbc.upgrade_targets = {}
                    _targets = _vbc.upgrade_targets
                if level is None:
                    _targets.pop(family, None)
                else:
                    _targets[family] = level
                logger.info("set_upgrade_target: family=%s level=%s", family, level)
            except Exception as exc:
                logger.warning("set_upgrade_target fail: %s", exc)

        def set_hold_gather_point(self, point: Any) -> None:
            """2026-05-28 用户 hold:聚团 + 坚守。Director 算好聚团点(target_area
            或 current army_center 锁住)后调此 setter。vendor zone_gather hook
            读 intent=hold 时 effective_gather_point=此点。None = 清(切战术/×)。
            point: sc2 Point2 或 None。
            """
            self.bot.knowledge.vibecraft.hold_gather_point = point

        def set_rally_point(self, point: Any) -> None:
            """出兵集结点（2026-06-07 用户）：覆盖 sharpy 全局 gather_point → 新出的兵
            (PlanZoneGather)自动 rally 到此点。**Director 每帧调**(sharpy set_gather_point
            是一次性 flag,只生效 1 tick;forward_rally 同款经验)。point=None → no-op
            (Director 停调即恢复 bot 默认前移逻辑)。point: (x,y) tuple / sc2 Point2 / None。
            """
            # 记玩家 override flag(point 或 None) → 剧本集结逻辑(VoidRayStageRallyAct 等)让位
            _vbc = getattr(getattr(self.bot, "knowledge", None), "vibecraft", None)
            if _vbc is not None:
                _vbc.player_rally_point = point
            if point is None:
                return
            try:
                from sc2.position import Point2
                from sharpy.interfaces import IGatherPointSolver

                solver = self.bot.knowledge.get_required_manager(IGatherPointSolver)
                solver.set_gather_point(point if isinstance(point, Point2) else Point2(point))
            except Exception as exc:
                logger.warning("set_rally_point fail: %s", exc)

        def set_regroup_started(self, ts: float | None) -> None:
            """2026-05-28 用户 probe/recon:聚团门 timer。Director 在玩家发 attack
            (probe) / recon 时 set ts=current game_time;15s 内 _should_attack
            check spread → 散开就 False(让 PlanZoneGather 集结);超时后 bypass。
            None = 清(切其他战术 / × → 取消聚团 timer)。
            """
            self.bot.knowledge.vibecraft.regroup_started_at = ts

        def block_production(self, unit_type: str) -> None:
            """2026-05-30 产能封锁：把 unit_type 加入 knowledge.vibecraft.production_blocked set。"""
            try:
                self.bot.knowledge.vibecraft.production_blocked.add(unit_type)
                logger.info("block_production: blocked %s", unit_type)
            except Exception as exc:
                logger.warning("block_production fail: %s", exc)

        def unblock_production(self, unit_type: str) -> None:
            """2026-05-30 产能封锁解除：从 production_blocked set 移除 unit_type。"""
            try:
                self.bot.knowledge.vibecraft.production_blocked.discard(unit_type)
                logger.info("unblock_production: unblocked %s", unit_type)
            except Exception as exc:
                logger.warning("unblock_production fail: %s", exc)

        def set_phoenix_harass_active(self, active: bool) -> None:
            """2026-05-30 凤凰骚扰持久指令卡：Director 在玩家点×卡片 / 到硬性截止
            时间时调 set False，PhoenixSquadAct 读此 flag → 停止 Reserve 凤凰，
            sharpy free_units 自动把凤凰纳入主力部队。"""
            try:
                self.bot.knowledge.vibecraft.phoenix_harass_active = active
                logger.info("set_phoenix_harass_active: %s", active)
            except Exception as exc:
                logger.warning("set_phoenix_harass_active fail: %s", exc)

        def move_camera(self, point: tuple[float, float]) -> None:
            self._pending_camera_point = point

        def follow_unit(self, unit_tag: int) -> None:
            unit = self.bot.units.find_by_tag(unit_tag)
            if unit is not None:
                self._pending_camera_point = (unit.position.x, unit.position.y)

        # ---- 玩家折跃"在X刷N兵"(2026-06-07)----

        def request_warp(
            self, key: str, unit_type: str, count: int, target: tuple[float, float]
        ) -> None:
            """登记一条折跃请求(**幂等**:已在 pending/done 就不重置,每帧调安全)。
            key=该兵种这张卡的折跃 key,在 target 最近能量场折跃 count 个 unit_type。"""
            if key in self._pending_warps or key in self._done_warps:
                return
            self._pending_warps[key] = {
                "unit_type": str(unit_type),
                "remaining": int(count),
                "target": (float(target[0]), float(target[1])),
            }

        def cancel_warp(self, key: str) -> None:
            self._pending_warps.pop(key, None)
            self._done_warps.discard(key)

        def warp_status(self, key: str) -> str:
            """'done'(折满)/ 'producing'(折跃中)/ 'none'(没登记)。"""
            if key in self._done_warps:
                return "done"
            if key in self._pending_warps:
                return "producing"
            return "none"

        def _nearest_power_source(self, target: tuple[float, float]) -> tuple[object, float]:
            """离 target 最近的能量场:ready 水晶塔(power 6.5)或展开棱镜(3.75)。
            返回 (source_unit, power_radius);没有 → (None, 0.0)。"""
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2

            tp = Point2(target)
            cands: list[tuple[object, float]] = []
            try:
                for p in self.bot.structures(UnitTypeId.PYLON).ready:
                    cands.append((p, 6.5))
            except Exception:
                pass
            try:
                for pr in self.bot.units(UnitTypeId.WARPPRISMPHASING):
                    cands.append((pr, 3.75))
            except Exception:
                pass
            if not cands:
                return None, 0.0
            src, radius = min(cands, key=lambda c: c[0].distance_to(tp))
            return src, radius

        async def _drain_warps(self) -> None:
            """每帧:对每条折跃请求,找最近能量场 → can_place 查落点 → ready warpgate 折跃。
            最近没能量场 → 跳过(等待,不丢请求)。折满 remaining → 进 _done_warps。"""
            if not self._pending_warps:
                return
            import contextlib as _ctx
            import random

            from sc2.ids.ability_id import AbilityId
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2

            from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown

            cm = getattr(self.bot.knowledge, "cooldown_manager", None)
            for did, req in list(self._pending_warps.items()):
                remaining = int(req["remaining"])
                tid = getattr(UnitTypeId, str(req["unit_type"]).upper(), None)
                if remaining <= 0 or tid is None:
                    self._pending_warps.pop(did, None)
                    self._done_warps.add(did)
                    continue
                src, radius = self._nearest_power_source(req["target"])  # type: ignore[arg-type]
                if src is None:
                    continue  # 没能量场 → 等下一帧(不丢)
                try:
                    warpgates = list(self.bot.structures(UnitTypeId.WARPGATE).ready)
                except Exception:
                    warpgates = []
                if not warpgates:
                    continue  # 折跃门没好 → 等
                # 能量场内候选格(扣中心 ±1),batch can_place 查实时可放位
                cx, cy = float(src.position.x), float(src.position.y)
                n = int(radius)
                grid = [
                    Point2((cx + dx, cy + dy))
                    for dx in range(-n, n + 1)
                    for dy in range(-n, n + 1)
                    if (abs(dx) > 1 or abs(dy) > 1) and dx * dx + dy * dy <= radius * radius
                ]
                if not grid:
                    continue
                try:
                    oks = await self.bot.can_place(AbilityId.WARPGATETRAIN_STALKER, grid)
                    valid = [grid[i] for i, ok in enumerate(oks) if ok]
                except Exception:
                    valid = []
                if not valid:
                    continue
                random.shuffle(valid)
                warp_cd = get_warp_cooldown(tid)
                warped = 0
                spot_i = 0
                for wg in warpgates:
                    if warped >= remaining or spot_i >= len(valid):
                        break
                    if cm is not None:
                        # 必须传 cooldown=warp_cd:默认模式信 SC2 get_available_abilities,
                        # 对 WG warp ability 不过滤 cd → 误报 ready → warp 在 cd 上被 SC2 拒。
                        try:
                            if not cm.is_ready(
                                wg.tag, AbilityId.WARPGATETRAIN_ZEALOT, cooldown=warp_cd
                            ):
                                continue
                        except Exception:
                            pass
                    placement = valid[spot_i]
                    spot_i += 1
                    try:
                        res = wg.warp_in(tid, placement)
                    except Exception:
                        res = False
                    if res is False:
                        continue
                    if cm is not None:
                        with _ctx.suppress(Exception):
                            cm.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
                    warped += 1
                if warped > 0:
                    req["remaining"] = remaining - warped
                    logger.info(
                        "PLAYERWARP did=%s warped %d %s @ power(%.1f,%.1f) remaining=%d",
                        did[:8],
                        warped,
                        str(req["unit_type"]),
                        cx,
                        cy,
                        req["remaining"],
                    )
                if int(req["remaining"]) <= 0:
                    self._pending_warps.pop(did, None)
                    self._done_warps.add(did)

        async def drain_pending_actions(self) -> None:
            # 代理建造队列(需 async find_placement)
            await self._drain_probe_builds()
            await self._drain_warps()
            if self._pending_camera_point is None:
                return
            from sc2.position import Point2

            pt = self._pending_camera_point
            self._pending_camera_point = None
            try:
                await self.bot.client.move_camera(Point2(pt))
            except Exception as exc:
                logger.warning("move_camera_failed point=%s err=%s", pt, exc)

        def set_camera_zoom(self, level: float) -> None:
            pass

        def get_camera_center(self) -> tuple[float, float] | None:
            # python-sc2 GameState:bot.state.observation 已是 Observation proto,
            # observation_raw = observation.raw_data。原写法多一层 .observation →
            # 每次抛异常返 None → camera"这里"全失效(2026-06-02 实测部队移动到 camera 失败)。
            try:
                c = self.bot.state.observation_raw.player.camera
                return (float(c.x), float(c.y))
            except Exception:
                return None

        def get_unit_position(self, tag: int) -> tuple[float, float] | None:
            """返回 tag 单位当前坐标；单位不存在或死亡返回 None。"""
            try:
                u = self.bot.units.by_tag(tag)
                return (float(u.position.x), float(u.position.y)) if u is not None else None
            except Exception:
                return None

        def filter_tags_in_box(
            self,
            tags: list[int],
            cx: float,
            cy: float,
            half_w: float,
            half_h: float,
        ) -> list[int]:
            """返回 tags 中位于 (cx±half_w, cy±half_h) 矩形框内的 tag 列表（保持入参顺序）。

            候选源：self.bot.units + self.bot.structures（建筑在 structures，
            python-sc2 两集合互斥）。找不到坐标的 tag 跳过。
            """
            # 构建 {tag: unit} 映射（O(n)）避免对每个 tag 线性扫描
            tag_map: dict[int, Any] = {}
            for u in self.bot.units:
                tag_map[u.tag] = u
            for s in self.bot.structures:
                tag_map[s.tag] = s
            result = []
            for t in tags:
                u = tag_map.get(t)
                if u is None:
                    continue
                try:
                    p = u.position
                    if abs(float(p.x) - cx) <= half_w and abs(float(p.y) - cy) <= half_h:
                        result.append(t)
                except Exception:
                    pass  # 坐标读取异常：跳过
            return result

        def order_probe_build(
            self,
            probe_tag: int,
            structure_type: str,
            point: tuple[float, float],
            cache_key: object = None,
        ) -> None:
            """命令 probe 在 point 处建造 structure_type 建筑。

            2026-06-06 用户:精确点常放不下(挡住/资源/不平整)→ 入队,drain_pending_actions
            用 find_placement 以 point 为圆心找最近合法位再 build,而非死磕原点被游戏拒。
            cache_key(代理建造卡 id):同一张卡反复重发时复用第一次 find_placement 的落点,
            避免每帧 find_placement 抖动导致目标乱跳(远程代理建造必须每帧重发压过 sharpy 抢人,
            落点必须稳定才不抖)。
            """
            self._pending_probe_builds.append(
                (int(probe_tag), str(structure_type), point, cache_key)
            )

        async def _drain_probe_builds(self) -> None:
            """处理代理建造队列:以目标点为圆心 find_placement 找最近合法位,再 u.build。"""
            if not self._pending_probe_builds:
                return
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2

            queue = self._pending_probe_builds
            self._pending_probe_builds = []
            for probe_tag, structure_type, point, cache_key in queue:
                try:
                    u = self.bot.units.by_tag(probe_tag)
                    tid = getattr(UnitTypeId, structure_type.upper(), None)
                    if u is None or tid is None:
                        logger.warning(
                            "order_probe_build: probe tag=%d or structure_type=%s not found",
                            probe_tag,
                            structure_type,
                        )
                        continue
                    near = Point2(point)
                    # townhall(Nexus/CC/Hatch)落点策略:近矿则 snap 到贴矿最优位再 find_placement
                    # ——否则"在这里造基地"会歪在矿区旁(find_placement 只找最近能放下的点),基地离矿
                    # 远 → 新农民没近矿可采、全 idle(2026-06-09 真局根因)。但玩家指定点偏太多
                    # (> TOWNHALL_SNAP_MAX_DIST)时尊重原位,允许故意造偏的挡路/卡口基地(2026-06-09 用户)。
                    from vibecraft.bot.named_spot import (
                        TOWNHALL_SNAP_MAX_DIST,
                        TOWNHALL_TYPE_NAMES,
                        snap_townhall_point,
                    )

                    if structure_type.upper() in TOWNHALL_TYPE_NAMES:
                        snapped_pt, did_snap = snap_townhall_point(point, self.bot)
                        if did_snap:
                            logger.info(
                                "order_probe_build: townhall %s snap 镜头点 %s → 贴矿最优位 (%.1f, %.1f)",
                                structure_type,
                                point,
                                float(snapped_pt.x),
                                float(snapped_pt.y),
                            )
                            near = snapped_pt
                        else:
                            logger.info(
                                "order_probe_build: townhall %s 指定点 %s 离最近矿 > %.0f 格,"
                                "按玩家指定位建(故意造偏的挡路/卡口基地)",
                                structure_type,
                                point,
                                TOWNHALL_SNAP_MAX_DIST,
                            )
                    # 落点缓存:同一张卡复用第一次 find_placement 的落点(稳定,不抖)
                    cached = (
                        self._proxy_place_cache.get(cache_key) if cache_key is not None else None
                    )
                    if cached is not None:
                        target = Point2(cached)
                    else:
                        place = None
                        try:
                            place = await self.bot.find_placement(tid, near=near, placement_step=2)
                        except Exception as exc:
                            logger.debug("find_placement fail: %s", exc)
                        target = place if place is not None else Point2(point)
                        if place is None:
                            logger.warning(
                                "order_probe_build: 目标点附近找不到 %s 合法位,退回原点试",
                                structure_type,
                            )
                        elif cache_key is not None:
                            self._proxy_place_cache[cache_key] = (float(target.x), float(target.y))
                    u.build(tid, target)
                    _o_after = [getattr(getattr(o, "ability", None), "id", None) for o in u.orders]
                    # 落点失效检测:农民已贴到落点(≤3 格)却没接到 PROTOSSBUILD 订单 → 该落点
                    # 被占/非法(常见:同点已有水晶+第1个建筑,第2个建筑挤不下缓存的老点)→
                    # 清缓存,下次 find_placement 重新找一个没被占的新点(2026-06-06 真局自验)。
                    _has_build = any("BUILD" in str(o) for o in _o_after)
                    if (
                        cache_key is not None
                        and not _has_build
                        and u.position.distance_to(target) <= 3.0
                    ):
                        self._proxy_place_cache.pop(cache_key, None)
                    logger.info(
                        "PROXYTRACE build_issued tag=%d type=%s near=(%.1f,%.1f) place=(%.1f,%.1f) orders_after=%s pos=(%.1f,%.1f)",
                        probe_tag,
                        structure_type,
                        float(point[0]),
                        float(point[1]),
                        float(target.x),
                        float(target.y),
                        _o_after,
                        float(u.position.x),
                        float(u.position.y),
                    )
                except Exception as exc:
                    logger.warning("order_probe_build drain fail: %s", exc)

        # ---- debug draw（WP-A 控制边界可视化）--------------------------

        def set_debug_marks(self, marks: list[dict[str, object]]) -> None:
            """记录本帧想画的 debug mark 清单（覆盖写）。每 tick 由 Director 调。"""
            self._debug_marks = list(marks)

        def draw_debug_marks(self) -> None:
            """画 WP-A 控制边界：每组一条 mark。

            mark = {shape("box"/"ring"), color(rgb), label, tags[], target[x,y]|None}
            - shape 画在该组每个存活单位上（box=指令卡 / ring=编队）
            - label 在该组质心飘一个文字（ASCII；SC2 debug 不渲染中文）
            - 有 target 时 质心→target 画连线 + target 处小球

            **绝不调 bot.client._send_debug()**——框架每帧 on_step 后自动 flush；手动调会
            先清空列表，框架那次发现列表空就发空绘制把刚画的擦掉（实测踩过）。
            """
            from sc2.position import Point2, Point3

            bot = self.bot
            for m in getattr(self, "_debug_marks", []):
                color = m.get("color", (0, 220, 255))
                shape = m.get("shape", "box")
                # 2026-06-08 出兵集结点标记:固定**点**锚定(不是单位)→ 地面圆环 + 竖线指天。
                pt = m.get("point")
                if pt is not None:
                    try:
                        px, py = float(pt[0]), float(pt[1])
                        try:
                            pz = float(bot.get_terrain_z_height(Point2((px, py))))
                        except Exception:
                            pz = 10.0
                        ground = Point3((px, py, pz))
                        # 集结点:6 层环(层距=编队 2 倍)向外铺开 + 接近无限高竖线(2026-06-10 用户)
                        for i in range(_RALLY_RING_PASSES):
                            bot.client.debug_sphere_out(
                                ground, _RALLY_RING_BASE + i * _RALLY_RING_STEP, color=color
                            )
                        bot.client.debug_line_out(
                            ground, Point3((px, py, pz + _RALLY_PILLAR_HEIGHT)), color=color
                        )
                    except Exception:
                        pass
                    continue  # 点锚定标记画完,跳过下面的单位锚定逻辑
                alive = []
                for tag in m.get("tags", []):
                    try:
                        u = bot.units.by_tag(int(tag))
                    except Exception:
                        u = None
                    if u is None:
                        continue
                    alive.append(u)
                    # 框/环半径随单位大小:= max(下限, 单位碰撞半径 + 余量)。航母/母舰
                    # radius 大 → 框大、能套住;探机等小单位走下限,仍有可见框。
                    r0 = max(
                        _DEBUG_MIN_RADIUS, float(getattr(u, "radius", 0.5)) + _DEBUG_RADIUS_MARGIN
                    )
                    # 线宽模拟：画 _DEBUG_THICK_PASSES 条紧贴同心线 → 粗线，手机可见
                    if shape == "ring":
                        for i in range(_DEBUG_THICK_PASSES):
                            bot.client.debug_sphere_out(u, r0 + i * _DEBUG_THICK_STEP, color=color)
                    else:
                        for i in range(_DEBUG_THICK_PASSES):
                            bot.client.debug_box2_out(u, r0 + i * _DEBUG_THICK_STEP, color=color)
                if not alive:
                    continue
                # 质心 + 飘字
                cx = sum(u.position3d.x for u in alive) / len(alive)
                cy = sum(u.position3d.y for u in alive) / len(alive)
                cz = sum(u.position3d.z for u in alive) / len(alive)
                centroid = Point3((cx, cy, cz))
                lbl = m.get("label")
                if lbl:
                    bot.client.debug_text_world(
                        str(lbl), centroid, color=color, size=_DEBUG_LABEL_SIZE
                    )
                # 目标连线 + 目标点小球
                target = m.get("target")
                if target:
                    try:
                        tx, ty = float(target[0]), float(target[1])
                        try:
                            tz = bot.get_terrain_z_height(Point2((tx, ty)))
                        except Exception:
                            tz = cz
                        tp = Point3((tx, ty, float(tz)))
                        bot.client.debug_line_out(centroid, tp, color=color)
                        bot.client.debug_sphere_out(tp, 0.9, color=color)
                    except Exception:
                        pass

        # ---- 读 -------------------------------------------------------

        def get_state(self) -> BotState:
            b = self.bot
            try:
                built = frozenset(str(s.type_id.name).upper() for s in b.structures)
            except Exception:
                built = frozenset()
            # 2026-05-28 用户:LLM 需要建筑 count(不只 set)解析"补一个 BF" delta 语义
            buildings_count: dict[str, int] = {}
            try:
                for s in b.structures.ready:
                    name = str(s.type_id.name).upper()
                    buildings_count[name] = buildings_count.get(name, 0) + 1
            except Exception:
                buildings_count = {}
            # 自家战斗单位汇总（去工人；type_id.name 全大写 → count）。
            # transition_cost 算"兵种缺口"靠这个 —— 之前是空 dict，导致成本不区分开局。
            army: dict[str, int] = {}
            try:
                for u in b.units:
                    name = str(u.type_id.name).upper()
                    if name in ("PROBE", "DRONE", "SCV", "MULE"):
                        continue
                    army[name] = army.get(name, 0) + 1
            except Exception:
                army = {}
            # 敌方已侦察到的单位汇总（enemy_tags 推断克制关系用）
            enemy: dict[str, int] = {}
            try:
                for u in b.enemy_units:
                    name = str(u.type_id.name).upper()
                    enemy[name] = enemy.get(name, 0) + 1
            except Exception:
                enemy = {}
            # 已完成升级（全大写 UpgradeId.name）
            try:
                upgrades = frozenset(str(u.name).upper() for u in b.state.upgrades)
            except Exception:
                upgrades = frozenset()
            # 敌方种族（小写，对齐 compute_enemy_composition_tags 的 race 参数）
            try:
                enemy_race = str(b.enemy_race.name).lower() if b.enemy_race else None
            except Exception:
                enemy_race = None
            return BotState(
                game_time=float(b.time),
                minerals=int(b.minerals),
                gas=int(b.vespene),
                supply_used=int(b.supply_used),
                supply_cap=int(b.supply_cap),
                expansion_count=len(b.townhalls),
                army_summary=army,
                enemy_summary=enemy,
                structures_built=built,
                buildings_summary=buildings_count,
                upgrades=upgrades,
                enemy_race=enemy_race,
            )

        def all_own_unit_tags(self, include_workers: bool = True) -> list[int]:
            """返回所有己方单位 tag 列表（**不含建筑**）。

            include_workers=False 时排除三族农民（Probe/SCV/Drone）。
            python-sc2 把单位放 bot.units、建筑放 bot.structures（互斥），
            遍历 bot.units 即可——不含建筑。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            _WORKERS = {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
            result = []
            for u in self.bot.units:
                if not include_workers and u.type_id in _WORKERS:
                    continue
                result.append(u.tag)
            return result

        def resolve_selector(
            self,
            unit_type: str | None = None,
            tag: int | None = None,
            tags: list[int] | None = None,
        ) -> list[int]:
            if tag is not None:
                return [tag]
            if tags:
                return list(tags)
            if unit_type is not None:
                # 2026-05-25 bug 11:Probe 优先选 idle/最远 mineral patch 的(避免
                # sharpy ResourceCollector 每 step 抢回采矿 probe → set_unit_role
                # LLM_CONTROLLED 后 move 命令被 sharpy 覆盖,单位站原地)。
                # 顺序:idle > matched > gathering 对单位；建筑无 idle/gathering 语义直接归 matched。
                # 2026-06-19 Step 0:同时遍历 self.bot.structures（python-sc2 把 is_structure
                # 的单位放 structures、其余放 units，二者互斥；原只遍历 units → 建筑如 Bunker
                # 恒返回 []）。建筑直接归 matched 档（无 idle/gathering 概念）。
                matched = []
                idle = []
                gathering = []
                for u in self.bot.units:
                    if str(u.type_id.name).casefold() == unit_type.casefold():
                        if u.is_idle:
                            idle.append(u.tag)
                        elif getattr(u, "is_gathering", False):
                            gathering.append(u.tag)
                        else:
                            matched.append(u.tag)
                for s in self.bot.structures:
                    if str(s.type_id.name).casefold() == unit_type.casefold():
                        matched.append(s.tag)
                return idle + matched + gathering  # idle 最优,gathering 兜底
            # unit_type/tag/tags 全空 → 匹配不到任何东西，返 []（**绝不隐式返 None**，
            # 否则 _resolve_selector_with_count 透传 None → 下游 for tag in None 崩，
            # 2026-06-03 用户报的"探路追猎火力侦查"崩溃根因之一）。
            return []

    return _SharpyFacadeBase


# -----------------------------------------------------------------------
# VibeCraftBotBase 工厂
# -----------------------------------------------------------------------


def _make_vibecraft_bot_base_class(
    director_factory: Any,
    strategy_library: Any,
    status_callback: Any,
    down_q: Any,
    echo_callback: Any,
    snapshot_callback: Any,
    event_callback: Any,
    minimap_callback: Any,
    run_command_with_echo_fn: Any,
    SharpyFacadeClass: type,
) -> type:
    """返回 VibeCraftBotBase 类（闭包持有所有回调）。

    子类只需继承此类并实现：
      - EXCLUDE_FROM_ARMY: ClassVar[set]
      - DEFAULT_OPENING_ID: ClassVar[str]
      - create_plan() -> BuildOrder
    """
    from sharpy.knowledges.knowledge_bot import KnowledgeBot

    class VibeCraftBotBase(KnowledgeBot):  # type: ignore[misc]
        """vibecraft 三族 bot 基类：sharpy KnowledgeBot + vibecraft 指挥层。

        race-agnostic 部分（lifecycle / EventBus / 多路复用 / LLM_CONTROLLED 隔离）
        全部在本基类实现。各族子类只填 EXCLUDE_FROM_ARMY / DEFAULT_OPENING_ID / create_plan。
        """

        # 子类必须覆盖
        EXCLUDE_FROM_ARMY: ClassVar[set[Any]] = set()
        DEFAULT_OPENING_ID: ClassVar[str] = ""

        director: Any = None
        facade: Any = None
        _cmd_tasks: list[asyncio.Task[Any]]
        _minimap_tick_count: int = 0
        _minimap_builder: Any = None
        _decision_watcher: Any = None
        _hang_watchdog: HangWatchdog | None = None
        active_recipe: str = ""
        _llm_controlled_tags: set[int]
        _tactics_last_s: float = 0.0
        _voice_step_count: int = 0
        _sharpy_iteration: int = 0
        # 默认按 game_step=4 调:重活每 5 个 on_step(=20 帧≈0.9s),小地图每 2 个(=8 帧)。
        # realtime 玩家模式 on_start 里按更小的 game_step 等比放大,保持真实节奏不变,
        # 只让镜头(每 on_step 重发)变快 → 跟随更平滑。见 on_start。
        _SHARPY_STEP_RATIO: int = 5
        _minimap_every: int = 2

        def __init__(self) -> None:
            # 用 DEFAULT_OPENING_ID 作为 bot 名后缀
            race_name = type(self).__name__
            super().__init__(f"VibeCraft {race_name}")
            self._cmd_tasks = []
            self._minimap_tick_count = 0
            self._minimap_builder = None
            self._decision_watcher = None
            self._hang_watchdog = None
            self.active_recipe = self.__class__.DEFAULT_OPENING_ID or ""
            self._llm_controlled_tags = set()
            self._tactics_last_s = 0.0
            self._voice_step_count = 0
            self._sharpy_iteration = 0
            self.event_bus = EventBus()
            self._enemy_units_dict: dict[int, Any] = {}
            self._own_units_dict: dict[int, Any] = {}
            # 被我方「打到过」的敌方农民 tag 集合 —— L3 骚扰验收信号。
            # 收录:① on_unit_took_damage 里受我方伤害的敌方农民;
            #       ② on_unit_destroyed 里我方视野内死亡的敌方农民。
            # 用「打到过(受伤 ∪ 阵亡)」而非「打死」—— 骚扰的价值是干扰对方
            # 经济,把农民打跑/打残也算到位,不强求击杀。
            self._harassed_worker_tags: set[int] = set()
            # 纯击杀计数(区别于"打到"):在我方视野内阵亡的敌方农民 tag。凤凰骚扰优化用它
            # (杀农民越多越好);凤凰累计损失数(损失越少越好)。两者是 phoenix 优化的核心指标。
            self._killed_worker_tags: set[int] = set()
            self._phoenix_lost: int = 0
            self.named_spots = NamedSpotRegistry()

        def _update_tactics_throttled(self, now: float) -> None:
            if now - self._tactics_last_s < 1.0:
                return
            self._tactics_last_s = now
            if self.director is None:
                return
            try:
                from vibecraft.bot.director import Tactics

                stance, label, reason = self._compute_stance()
                self.director._tactics = Tactics(stance=stance, label=label, reason=reason)
            except Exception as exc:
                logger.debug("tactics_compute_failed: %s", exc)

        def _compute_stance(self) -> tuple[str, str, str]:
            """返回 (stance, label, reason)。子类可覆盖以适配不同种族单位类型。"""
            lang = getattr(self.director, "_lang", "zh") or "zh"
            townhalls = self.townhalls
            home = townhalls.first.position if townhalls else self.start_location

            # expanding：有正在建造的基地
            try:
                # 各族主基地类型（Hatchery/CommandCenter/Nexus 都叫 townhalls）
                pending_th = self.townhalls.not_ready.amount
                if pending_th > 0:
                    return (
                        "expanding",
                        _i18n_t("decision.expanding", lang, count=pending_th),
                        _i18n_t(
                            "decision.expandingReason",
                            lang,
                            current=townhalls.amount,
                            target=townhalls.amount + pending_th,
                        ),
                    )
            except Exception:
                pass

            # army（排除种族工人 / 非战斗单位）
            try:
                exclude = self.__class__.EXCLUDE_FROM_ARMY
                if exclude:
                    army = self.units.exclude_type(exclude)
                else:
                    army = self.units
            except Exception:
                army = self.units

            # defending
            for th in townhalls:
                enemies_near = self.enemy_units.closer_than(25.0, th)
                if enemies_near.amount >= 2:
                    return (
                        "defending",
                        _i18n_t("decision.defending", lang, enemies=enemies_near.amount),
                        _i18n_t(
                            "decision.defendingReason",
                            lang,
                            building=th.type_id.name,
                            enemies=enemies_near.amount,
                        ),
                    )

            # attacking —— 2026-06-13 用户实测"面板显示进攻但部队不动"修订:
            # 原判定纯几何(质心离家>25 即"进攻中"),与真实作战层(PlanZoneAttack)
            # 解耦——部队在前沿集结/冻结时面板照样喊进攻。现在以作战层为真值:
            # PlanZoneAttack.status 为 Attacking/MovingToExpansion/ProtectingExpansion
            # 才报"进攻中";质心远但作战层未进攻 → 显示"前沿集结",不再误导。
            # 作战层状态取不到(plan 树异常等)时回退原几何判定(宁可保留旧行为)。
            if army.amount >= 6:
                center = army.center
                dist = center.distance_to(home)
                if dist > 25.0:
                    plan_status: str | None = None
                    try:
                        from vibecraft.bot.telemetry import extract_tactical_state

                        plan_status = extract_tactical_state(self).get("plan_status")
                    except Exception:
                        plan_status = None
                    if plan_status in ("Attacking", "MovingToExpansion", "ProtectingExpansion"):
                        return (
                            "attacking",
                            _i18n_t("decision.attacking", lang, units=army.amount),
                            _i18n_t(
                                "decision.combatLayerDist", lang, status=plan_status, dist=int(dist)
                            ),
                        )
                    if plan_status in ("Retreat", "Withdraw"):
                        return (
                            "retreating",
                            _i18n_t("decision.retreating", lang, units=army.amount),
                            _i18n_t("decision.combatLayerOnly", lang, status=plan_status),
                        )
                    if plan_status is None:
                        # 作战层不可知 → 回退原几何判定(旧行为)
                        return (
                            "attacking",
                            _i18n_t("decision.attacking", lang, units=army.amount),
                            _i18n_t("decision.centroidFallback", lang, dist=int(dist)),
                        )
                    return (
                        "forward",
                        _i18n_t("decision.forward", lang, units=army.amount),
                        _i18n_t(
                            "decision.combatLayerForwardDist",
                            lang,
                            status=plan_status,
                            dist=int(dist),
                        ),
                    )

            return (
                "sustaining",
                _i18n_t("decision.sustaining", lang),
                _i18n_t("decision.basesArmy", lang, bases=townhalls.amount, army=army.amount),
            )

        def is_vibecraft_controlled(self, unit: Any) -> bool:
            return unit.tag in self._llm_controlled_tags

        def _refresh_llm_controlled_roles(self) -> None:
            # ⚠️ 这里**每帧**把 _llm_controlled_tags 里的单位重设 Reserved(防 sharpy 各 Manager
            # 每帧把它们抢回去派别的)。**直接后果**:要把一个被 claim 的单位还给 bot,光 set 一次
            # 别的 role **没用** —— 下一帧这里又 re-Reserve 回去。必须先从 _llm_controlled_tags
            # **移除**(release_unit_role / set_unit_role(非 LLM_CONTROLLED) 会 discard)。
            # 2026-06-07 踩过:_SharpyFacadeBase 漏实现 release_unit_role → tag 留在这 set →
            # 取消指令也放不掉、永久锁死。改这里/claim 生命周期前先看 set_unit_role + release_unit_role。
            tags: set[int] = getattr(self, "_llm_controlled_tags", set())
            if not tags:
                return
            try:
                from sharpy.managers.core.roles.unit_task import UnitTask

                dead_tags: set[int] = set()
                for tag in tags:
                    unit = self.knowledge.unit_cache.by_tag(tag)
                    if unit is None:
                        dead_tags.add(tag)
                        continue
                    self.knowledge.roles.set_task(UnitTask.Reserved, unit)
                if dead_tags:
                    self._llm_controlled_tags -= dead_tags
                    logger.debug("llm_controlled_tags cleanup removed dead tags: %s", dead_tags)
            except Exception as exc:
                logger.warning("refresh_llm_controlled_roles failed: %s", exc)

        async def create_plan(self) -> Any:
            raise NotImplementedError("子类必须实现 create_plan()")

        async def pre_step_execute(self) -> None:
            """每帧 plan(ActManager)跑之前的钩子。做两件事:

            1. **再 Reserve 一次 LLM-controlled 单位**(2026-06-06 真局自验关键修复):
               managers 顺序 = [...,UnitRoleManager,...,CustomFuncManager(本函数),ActManager]。
               UnitRoleManager.update() 每帧 had_task_set.clear() 会把上一帧的 Reserved 清掉,
               而 ActManager 里的 DistributeWorkers 只放过 Reserved 槽的工人 —— 若此刻代理建造
               农民不是 Reserved,就被当空闲工人拉去采矿、**取消它的 build 命令**(农民被拽到矿区、
               PROTOSSBUILD 每帧被 gather 覆盖、永远建不出第2个建筑)。本函数在 UnitRoleManager
               之后、DistributeWorkers 之前重设 Reserved → DistributeWorkers 看到 Reserved 跳过它。
               (super().on_step() 前的那次 refresh 在 UnitRoleManager.update 之前,会被它清掉,
               所以必须在这里再来一次。)
            2. **玩家代理建造资源优先**(问题3):把未完成代理建造 cost 登记进 reserved →
               自主 macro can_afford 让路攒矿。详见 Director.pending_build_reservations。
            """
            # 1. 再 Reserve LLM-controlled(含代理建造农民)—— 关键:在 DistributeWorkers 之前
            try:
                self._refresh_llm_controlled_roles()
            except Exception as exc:
                logger.debug("pre_step_execute refresh roles fail: %s", exc)
            # 2. 资源预留
            if getattr(self, "director", None) is None:
                return
            try:
                from sc2.ids.unit_typeid import UnitTypeId

                reserved_types = list(self.director.pending_build_reservations())
                for st in reserved_types:
                    tid = getattr(UnitTypeId, str(st).upper(), None)
                    if tid is not None:
                        self.knowledge.reserve_costs(tid)
                # 量化"家里让路"(问题3 自验):登记完野外预留后,若某类建筑"原始矿够建、
                # 但扣掉野外预留后买不起了" → 这帧家里那类建筑被预留挡住、把钱让给野外。
                # 打 PROXYRESERVE_BLOCK 证明指令一下家里真让路(没指令时 reserved_types 为空,
                # 永不触发 → 天然 A/B 对照)。
                for st in dict.fromkeys(reserved_types):  # 去重保序
                    tid = getattr(UnitTypeId, str(st).upper(), None)
                    if tid is None:
                        continue
                    try:
                        unit = self._game_data.units[tid.value]
                        cost = self._game_data.calculate_ability_cost(unit.creation_ability)
                    except Exception:
                        continue
                    raw_ok = self.minerals >= cost.minerals and self.vespene >= cost.vespene
                    avail_ok = self.knowledge.can_afford(tid, check_supply_cost=False)
                    if raw_ok and not avail_ok:
                        logger.info(
                            "PROXYRESERVE_BLOCK type=%s 家里让路 raw_min=%d avail_min=%d reserved=%d",
                            st,
                            int(self.minerals),
                            int(self.knowledge.available_mineral),
                            int(self.knowledge.reserved_minerals),
                        )
            except Exception as exc:
                logger.debug("pre_step_execute reserve fail: %s", exc)

        async def on_start(self) -> None:
            await super().on_start()

            # 2026-06-01 用户:realtime 玩家观战下镜头跟随"一跳一跳"——根因是 python-sc2
            # realtime 每 client.game_step(默认4)帧才调一次 on_step,镜头每 on_step 重发,
            # 故 ~5.6Hz。调小 game_step → on_step 更频繁 → 镜头更平滑。同时等比放大重活/
            # 小地图节流,让它们真实节奏不变(只镜头变快)。non-realtime(acceptance)不动,
            # 保持 sim 速度。game_step=2 → 镜头 11.2Hz;重活每10步=20帧≈0.9s(同默认);
            # 小地图每4步=8帧(同默认)。
            # getattr 兜底:python-sc2 在 _prepare_start 才设 self.realtime;单测直接构造
            # bot 调 on_start 时该属性可能不存在。
            if getattr(self, "realtime", False):
                try:
                    # 2026-06-05 用户:debug 画框/镜头跟随还是"一跳一跳"不够丝滑。
                    # game_step=1 → on_step 每 game loop 调一次 = 22.4Hz(realtime 物理
                    # 上限,等于 game loop 率;SC2 debug draw 只能每 step 推一次,帧间无法
                    # 插值,故 22Hz 是天花板,做不到 60)。draw_debug_marks 每 on_step 重读
                    # 单位坐标 → 框位置 22Hz 刷新。sharpy/minimap 节流等比放大保持真实节奏。
                    self.client.game_step = 1
                    self._SHARPY_STEP_RATIO = 20
                    self._minimap_every = 8
                    logger.info(
                        "realtime camera smoothing: game_step=1 (~22Hz), "
                        "sharpy_ratio=20, minimap_every=8"
                    )
                except Exception as exc:
                    logger.warning("set game_step fail: %s", exc)

            from types import SimpleNamespace as _SNS

            self.knowledge.vibecraft = _SNS(
                attack_target_override=None,
                combat_intent_override=None,
                # 2026-05-25 用户:战术按钮拆"强制全体进攻"/"试探性进攻"。
                # 玩家点 UI 按钮时 set 此 flag,优先级 > plan 默认 force_attack。
                # "all_in" → ZoneAttack force_attack=True(不撤退)
                # "probe"  → ZoneAttack force_attack=False(sharpy power 判定)
                # None     → 用 plan 默认 force_attack(4bg=True, 1g_robo=False)
                attack_mode_override=None,
                stance_override=None,
                # 2026-05-19: DT 受伤事件记录（VibeCraftMicroDarkTemplar 读这个判断
                # "最近 2 秒被攻击 → 撤退"）。tag -> last damage timestamp。
                damaged_dts={},
                # 累计训练 DT 数（on_unit_created 递增，永不减）。≥8 时 macro attack ready
                # latched —— DT 死了也不会回退，剧本里 plan 训练 replacement 也算累加。
                dt_trained_count=0,
                # 累计建造 Warp Prism 数（on_unit_created 递增，永不减）。dt_drop_iac
                # 用它限制棱镜替补：≥2（原版 + 1 替补）后改补 Observer。
                prism_built_count=0,
                # 2026-05-27 Task #341: opening 完成 + 120s 超时后 Director set True。
                # OpeningSustainAct 读此 flag → 启动持续 macro（放开 cap）。
                sustain_uncap_active=False,
                # 2026-05-28 用户需求 3:闪追风筝 — 前线 stalker blink CD 都没好 +
                # 平均护盾低 → BlinkKiteRetreatAct set True,vendor zone_attack
                # _should_retreat hook 读此 flag 触发 Retreat 拖 CD。CD 恢复后 False。
                kite_retreat=False,
                # 2026-05-28 用户 hold:聚团 + 坚守 — director set hold_gather_point
                # (target_area 或 current army_center 锁住),vendor zone_gather hook
                # 读此 flag,intent=hold 时 effective_gather=此点。
                hold_gather_point=None,
                # 2026-05-28 用户 probe/recon:聚团门 — director set regroup_started_at
                # (game time secs),vendor zone_attack _should_attack 看 mode=probe 时
                # check free_units spread。15s 超时不强制 attack。切战术 → None 重置。
                regroup_started_at=None,
                # 2026-06-07 出兵集结点:玩家显式设的全局集结点 (x,y) / None。
                # 玩家设了 → 剧本内的集结逻辑(如 VoidRayStageRallyAct)让位(玩家 > bot)。
                # facade.set_rally_point 写入(point 或 None=清)。
                player_rally_point=None,
                # 2026-05-29 iac_2base 叉球一波:电兵安全 micro —— True 时
                # MicroHighTemplars 启用 vibecraft 安全路径（保持安全距离放 Storm，
                # 不主动 attack）。默认 False（不改变其他 plan 的电兵行为）。
                ht_safe_micro=False,
                # 2026-05-30 产能封锁：暂停造某种兵的 unit_type set。
                # Director._apply_production_block 调 facade.block_production(ut) 写入；
                # ProductionBlockAct 每 tick 读此 set 取消队列中的对应单位。
                production_blocked=set(),
                # 2026-06-10 偷矿前置：玩家开矿封顶（None=不封，用剧本 expansion_cap）
                expansion_cap_override=None,
                # 偷矿 FENCE：所有 stealth cell 的 Nexus tag 集合（Expand 自然扩张账排除 + DistributeWorkers 排除）
                stealth_townhall_tags=set(),
                # 偷矿星空加速预留：成长期 stealth Nexus tag 集合。bot 的 ChronoUnit 不拿这些
                # Nexus 当能量源（能量留给偷矿基地自我加速产农民）；满采后移出 → 释放回公共池。
                stealth_chrono_reserved_tags=set(),
                # 偷矿农民 tag 集合（所有 cell 并集，Manager 每帧注册）。ScoutWorker 等"挑农民
                # 干别的活"的逻辑排除它——比 _llm_controlled_tags 更稳（不受瞬时 cache miss
                # 把农民从 _llm_controlled_tags 误删那一帧的 race 影响）。
                stealth_worker_tags=set(),
                # 2026-06-12 在建/待建偷矿基地数（PENDING/BUILDING）。Expand 把它加进 active_bases
                # → 玩家下了偷矿令但偷矿基地还没建好时，bot 也当它是一片基地、延后开自己分矿。
                stealth_pending_base_count=0,
                # 2026-05-30 凤凰骚扰持久指令卡：True = 继续骚扰(PhoenixSquadAct
                # Reserve 凤凰做骚扰 micro)；False = 停止骚扰归队(act 释放 Reserved，
                # sharpy free_units 自动把凤凰纳入主力 PlanZoneAttack/Defense)。
                # Director 在玩家点×卡片 / 到硬性截止时间时 set False(一次性 latch)。
                phoenix_harass_active=True,
                # 2026-07-05 harass_workers player claim：director 每 tick 把 verb==HARASS_WORKERS
                # 的 standing order tags 发布到此 set，_execute_worker_harass_micro 读取驱动微操。
                worker_harass_tags=set(),
                # 2026-07-06 采矿策略：DistributeWorkers.execute patch 读此字段每帧覆写
                # min_gas/max_gas。"mineral"=优先水晶 / "gas"=优先气 / None=默认（恢复剧本值）。
                mining_priority=None,
                # 2026-07-07 攻防升级目标等级：family → int(0-3) / 无 key=auto。
                # vendor/sharpy tech.py::Tech.execute 封顶门读此 dict。
                # Director.set_upgrade_target → facade.set_upgrade_target 写入。
                upgrade_targets={},
            )

            # 2026-05-19: 替换 DT 默认 micro（sharpy 用 MicroZerglings 兜底，无 DT
            # 特化）为 vibecraft 智能版：检测到 detector / 重防御时主动撤回棱镜
            # 或回家。仅对神族 bot 注入（其他 race 没 DT，dict 操作无害但浪费）。
            try:
                from sc2.ids.unit_typeid import UnitTypeId as _UTI

                if hasattr(self, "race") and str(self.race).endswith("Protoss"):
                    from vibecraft.bot.auto_combat.protoss.vibecraft_micro_dt import (
                        VibeCraftMicroDarkTemplar,
                    )

                    self.combat.rules.unit_micros[_UTI.DARKTEMPLAR] = VibeCraftMicroDarkTemplar()
                    logger.info("VibeCraftMicroDarkTemplar 注入成功 (DT 智能微操)")

                    # 2026-06-02: 去掉 sharpy 叉子 group 级聚团（行军逼近敌人前排进射程
                    # → engage_ratio>0.25 → 整团回缩重心 → 前排冲不进，"不停聚团"）。
                    from vibecraft.bot.auto_combat.protoss.vibecraft_micro_zealots import (
                        VibeCraftMicroZealots,
                    )

                    self.combat.rules.unit_micros[_UTI.ZEALOT] = VibeCraftMicroZealots()
                    logger.info("VibeCraftMicroZealots 注入成功 (叉子去聚团)")
            except Exception as exc:
                logger.warning("VibeCraftMicroDarkTemplar 注入失败: %s", exc)

            self.facade = SharpyFacadeClass(self)
            self.director = director_factory(self.facade)

            # telemetry: always-on 游戏内状态采集(项目开发期默认开)
            self._telemetry = None
            try:
                import contextlib
                from functools import partial

                from vibecraft.bot.telemetry import TelemetryLogger, build_game_start_record
                from vibecraft.logging_.types import LogStream

                session = getattr(self.director, "session", None) if self.director else None
                if session is not None:
                    self._telemetry = TelemetryLogger(
                        sink_fn=partial(session.log, LogStream.TELEMETRY)
                    )
                    home = self.start_location
                    enemy_main = self.enemy_start_locations[0]
                    natural = None
                    enemy_natural = None
                    with contextlib.suppress(Exception):
                        exps = list(self.expansion_locations_list)
                        cands = sorted(exps, key=lambda p: p.distance_to(home))
                        for p in cands:
                            if p.distance_to(home) > 1.0:
                                natural = p
                                break
                        # 敌方二矿 = 离敌方主基地最近的非主基地扩张点
                        e_cands = sorted(exps, key=lambda p: p.distance_to(enemy_main))
                        for p in e_cands:
                            if p.distance_to(enemy_main) > 1.0:
                                enemy_natural = p
                                break
                    import os as _os_telemetry

                    self._telemetry.write_event(
                        build_game_start_record(
                            t=float(self.time),
                            home=home,
                            enemy_main=enemy_main,
                            natural=natural,
                            enemy_natural=enemy_natural,
                            active_recipe=str(getattr(self, "active_recipe", "")),
                            my_race=str(self.race).rsplit(".", 1)[-1],
                            # 玩家昵称：由 _child_entry 从 GameConfig.player_name 写入 env，
                            # 空串 = build_acceptance 沙盒 / 旧局，admin 显示"—"即可。
                            player_name=_os_telemetry.environ.get("VIBECRAFT_PLAYER_NAME", ""),
                            # 整局 roster JSON（全部参战方）→ admin 对局记录显示两人/玩家+电脑种族。
                            match_roster_json=_os_telemetry.environ.get(
                                "VIBECRAFT_MATCH_ROSTER", ""
                            ),
                        )
                    )
            except Exception as exc:
                logger.warning("telemetry init fail: %s", exc)

            if self.director is not None:
                self.director._bot = self

            if self.director is not None and hasattr(self, "event_bus"):
                self.director.setup_task_monitor(self.event_bus)

            if minimap_callback is not None:
                from vibecraft.bot.minimap import MinimapBuilder

                self._minimap_builder = MinimapBuilder(self)

            if snapshot_callback is not None and self.director is not None:
                self.director.set_snapshot_callback(snapshot_callback)
            if event_callback is not None and self.director is not None:
                self.director.set_event_callback(event_callback)

            if event_callback is not None:
                from vibecraft.bot.auto_combat.decision_watcher import DecisionWatcher

                self._decision_watcher = DecisionWatcher(event_callback)

            if strategy_library is not None:
                import os
                import random

                from vibecraft.strategy.models import OpeningBuild

                _DEFAULT_OPENING_ID = self.__class__.DEFAULT_OPENING_ID or ""
                openings = [
                    s for s in strategy_library.all_strategies() if isinstance(s, OpeningBuild)
                ]
                if openings:
                    forced_id = os.environ.get("VIBECRAFT_FORCE_INITIAL_OPENING")
                    chosen = None
                    if forced_id:
                        chosen = next((o for o in openings if o.id == forced_id), None)
                        if chosen is None:
                            logger.warning(
                                "forced initial opening %r 不在 catalog,回退默认 %s",
                                forced_id,
                                _DEFAULT_OPENING_ID,
                            )
                    if chosen is None:
                        chosen = next(
                            (o for o in openings if o.id == _DEFAULT_OPENING_ID),
                            None,
                        )
                        if chosen is None:
                            logger.warning(
                                "default opening %r 不在 catalog,回退 random",
                                _DEFAULT_OPENING_ID,
                            )
                            chosen = random.choice(openings)
                    self.active_recipe = chosen.id
                    logger.info("bot 选定开局剧本: %s (%s)", chosen.id, chosen.display_name_zh)

                    if self.director is not None:
                        from vibecraft.directives.types import StageKind

                        self.director.set_initial_strategy(
                            StageKind.OPENING, chosen.id, float(self.time)
                        )

            if status_callback is not None:
                status_callback("in_game", "running", "")
                status_callback("playing", "running", "")

            import os as _os

            if not _os.environ.get("VIBECRAFT_DISABLE_HANG_WATCHDOG"):

                def _on_hang() -> None:
                    if status_callback is not None:
                        status_callback("crashed", "error", "hang_watchdog: bot.time stuck")

                self._hang_watchdog = HangWatchdog(
                    get_bot_time=lambda: float(self.time),
                    on_hang=_on_hang,
                )
                self._hang_watchdog.start()

        async def on_step(self, iteration: int) -> None:
            # 顶层兜底:每帧任何环节(测试钩子 / view / bot channel / telemetry / sharpy plan)
            # 抛异常都在这里被吞 + 落完整 traceback 到 game log,绝不让单帧错误冒泡到
            # sc2.main:run_match 杀整局(2026-06-19 用户强要求"所有异常都catch写log方便debug")。
            # 内层各子步骤仍有各自的 try/except 做细粒度降级;本层是最后一道保险。
            try:
                await self._on_step_body(iteration)
            except Exception:
                logger.exception("on_step 顶层兜底捕获异常(已吞,游戏继续) iter=%d", iteration)

        async def _on_step_body(self, iteration: int) -> None:
            self._voice_step_count += 1
            now_s = float(self.time)

            # 通用闲置农民兜底(2026-07-20):对**所有 build** 每帧检测"有效闲置"农民
            # (ai.workers.idle 测不到的"有 order 却没干活"那种)→ 派最缺矿基地。非阻塞、
            # 排除玩家 claim。见 idle_worker_rescue 模块 docstring(原只在 nydus,现全局化)。
            try:
                _idle_state = self.__dict__.setdefault("_idle_rescue_state", {})
                rescue_idle_workers(self, _idle_state)
            except Exception:
                pass

            # 测试钩子(env 门控,生产环境完全 inert)：VIBECRAFT_ADDON_BLOCK_TEST=1 时，
            # 一次性在一个没挂件的重工的挂件位 debug 生一座建筑堵住，用于真局验证
            # #543「挂件位被占 → 起飞挪位再挂」。同 VIBECRAFT_MOCK_LLM_JSON 的 env 门控套路。
            import os as _os

            if _os.environ.get("VIBECRAFT_ADDON_BLOCK_TEST") and not getattr(
                self, "_addon_block_done", False
            ):
                await self._maybe_block_factory_addon()

            # 测试钩子(env 门控)：VIBECRAFT_CARRIER_STANDBY_TEST=1 → 一次性在远离主基处 debug 生
            # 几艘航母,用于真局验证「航母回家待命不抽搐」(配 mock LLM 注入"所有航母回家待命")。
            if _os.environ.get("VIBECRAFT_CARRIER_STANDBY_TEST") and not getattr(
                self, "_carrier_spawn_done", False
            ):
                await self._maybe_spawn_carriers_far()

            # 测试钩子(env 门控)：VIBECRAFT_BCHARASS_SELFTEST=1 → 一次性 debug 生几艘 BC 在主基,
            # 验"group_harass claim 自动建立 → GroupHarassAct 驱动群 BC 贴边飞向敌矿农民线"(#580)。
            if _os.environ.get("VIBECRAFT_BCHARASS_SELFTEST") and not getattr(
                self, "_bcharass_spawn_done", False
            ):
                await self._maybe_spawn_bcs_for_harass()

            # 测试钩子(env 门控)：VIBECRAFT_WHARASS_SELFTEST=1 → 一次性 debug 生几个死神在主基,
            # 配 mock LLM 注入"派死神去骚扰对方农民"(harass_workers claim) → 验 director 每 tick
            # 的 _execute_worker_harass_micro 驱动被 claim 单位 hit-and-run 打敌矿农民(通用骚扰执行器)。
            if _os.environ.get("VIBECRAFT_WHARASS_SELFTEST") and not getattr(
                self, "_wharass_spawn_done", False
            ):
                await self._maybe_spawn_reapers_for_harass()

            # 测试钩子(env 门控)：VIBECRAFT_REPAIR_SELFTEST=1 → 等 bot 真实建好 1 座地堡后,
            # 一次性 debug 把它打残(life→50),供 #551 维修指令真局自验(注入"派N农民修地堡"→
            # REPAIRTRACE 验 hp 回升到 repair_done_all_healthy = 终态真修好,不只发命令)。
            if _os.environ.get("VIBECRAFT_REPAIR_SELFTEST"):
                await self._maybe_damage_building_for_repair()

            # 测试钩子(env 门控)：VIBECRAFT_CCLIFT_PROBE=1 → 真机核对 CC 起降 ability(#560 linchpin)。
            # 真机 get_available_abilities + LIFT→飞→LAND 全程记 CCLIFTPROBE,验终态(CC 真飞到目标落地)。
            if _os.environ.get("VIBECRAFT_CCLIFT_PROBE"):
                await self._cclift_probe_step()

            # 测试钩子(env 门控)：VIBECRAFT_SPARECC_SELFTEST=1 → debug 在远离矿的地方生一个 idle
            # spare CC(代表玩家预造的额外 CC),交给 SpareCcExpandAct 自动飞去开矿(#560 真局自验)。
            if _os.environ.get("VIBECRAFT_SPARECC_SELFTEST") and not getattr(
                self, "_sparecc_spawned", False
            ):
                await self._maybe_spawn_spare_cc()

            # 测试钩子(env 门控)：VIBECRAFT_DEFEND_TRACE=1 → 每帧记 intent + 各 zone 威胁值 + army 中心,
            # 用于真局验证「全体防守智能选点」(规则1 敌近守该基地 / 规则2 无敌守最前沿基地)。
            if _os.environ.get("VIBECRAFT_DEFEND_TRACE") and self._voice_step_count % 10 == 0:
                self._defend_trace()
            # VIBECRAFT_DEFEND_SPAWN_ENEMY=1 → 游戏时间到点后,在**主基地**附近一次性生一股强敌,
            # 验规则1:army 应从最前沿迁回防被威胁的主基。
            if _os.environ.get("VIBECRAFT_DEFEND_SPAWN_ENEMY") and not getattr(
                self, "_defend_enemy_spawned", False
            ):
                await self._maybe_spawn_enemy_at_main()
            # VIBECRAFT_DEFEND_FORCE_BASES=1 → 在分矿点 debug 生 Nexus,强制稳定多基地
            # (区分 home vs 最前沿基地,验 defend army 去哪)。
            if _os.environ.get("VIBECRAFT_DEFEND_FORCE_BASES") and not getattr(
                self, "_defend_bases_forced", False
            ):
                await self._maybe_force_bases()
            # VIBECRAFT_SPAWN_MARINES=1 → 游戏时间 >40 后在主基 debug 生一堆枪兵(复现"大军在家")。
            if _os.environ.get("VIBECRAFT_SPAWN_MARINES") and not getattr(
                self, "_marines_spawned", False
            ):
                await self._maybe_spawn_marines()
            # VIBECRAFT_DEFEND_FLICKER=1 → 每 ~8s 在主基附近刷一股蟑螂(owner=2),模拟敌人
            # 反复进出骚扰 → PlanZoneDefense claim/release churn(复现 defend "保持队形拉扯")。
            if _os.environ.get("VIBECRAFT_DEFEND_FLICKER"):
                await self._maybe_flicker_enemy()
            # VIBECRAFT_CASTER_SELFTEST=1 → 解锁升级 + debug 生 caster(鬼/女妖)+ 敌人,
            # 验主动技能真触发(配 VIBECRAFT_CASTER_TRACE 看 CASTERTRACE 日志)。
            if _os.environ.get("VIBECRAFT_CASTER_SELFTEST") and not getattr(
                self, "_caster_selftest_done", False
            ):
                await self._maybe_caster_selftest()

            await self._tick_view_channel(now_s)

            if self._voice_step_count % self._SHARPY_STEP_RATIO == 0:
                await self._tick_bot_channel(iteration, now_s)

        async def _maybe_block_factory_addon(self) -> None:
            """测试用(#543 起飞挪位)：debug 生一座空闲重工 + 堵它挂件位,且**保证 relocate 有落点**。

            地形无关搭法(别靠落点运气——find_placement(addon_place) 对地形极敏感):
              phase 0：在主基台地一圈候选点里挑一个,生一座重工,记下生成点。
              phase 1：读重工真实位置,用**产品 relocate 那条 query**
                       (find_placement(FACTORY, pos, max_distance=18, addon_place=True))亲自验:
                       - 返回落点 R → 这座重工挂件位被堵后真能挪过去 → 在它挂件位生补给站堵死,done。
                       - 返回 None → 这块地挪不动(楼能放、楼+挂件放不下) → debug 杀掉重工,
                         换下一个候选点重生(phase 回 0,attempt+1),直到找到能挪的地。
            这样跑出的 LIFT→LAND 是真在能挪的地形上发生,不是测试搭建凑的。
            """
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2

            try:
                if float(self.time) < 8.0 or not self.townhalls.ready:
                    return
            except Exception:
                return
            # 候选点:主基台地一圈(start_location + 半径 7/13 的 8 向偏移)。逐个 attempt 试,
            # 哪个生出来后产品 relocate query 有落点就用哪个。
            if not hasattr(self, "_addon_block_cands"):
                base = self.start_location
                offs = [(0.0, 0.0)]
                for r in (7.0, 13.0):
                    for dx, dy in (
                        (1, 0),
                        (-1, 0),
                        (0, 1),
                        (0, -1),
                        (1, 1),
                        (-1, 1),
                        (1, -1),
                        (-1, -1),
                    ):
                        offs.append((dx * r, dy * r))
                self._addon_block_cands = [base.offset(Point2(o)) for o in offs]
            attempt = getattr(self, "_addon_block_attempt", 0)
            phase = getattr(self, "_addon_block_phase", 0)
            try:
                if phase == 0:
                    # 主基台地一圈候选点逐个试,第一个找到合法落点的就在那生重工(下一帧堵它挂件位)。
                    if attempt >= len(self._addon_block_cands):
                        logger.warning("ADDON_BLOCK_TEST 候选点试尽")
                        self._addon_block_done = True
                        return
                    near = self._addon_block_cands[attempt]
                    a = await self.find_placement(
                        UnitTypeId.FACTORY, near, max_distance=9, addon_place=True
                    )
                    if a is None:
                        self._addon_block_attempt = attempt + 1
                        return
                    await self._client.debug_create_unit([[UnitTypeId.FACTORY, 1, a, 1]])
                    self._addon_block_spot = a
                    self._addon_block_phase = 1
                    return
                # phase 1 → 读重工真实位置,堵它挂件位
                facs = self.structures(UnitTypeId.FACTORY).ready
                if not facs:
                    return  # 重工还没进 game state,下帧再试
                f = facs.closest_to(self._addon_block_spot)
                addon_pos = f.position.offset(Point2((2.5, -0.5)))
                await self._client.debug_create_unit([[UnitTypeId.SUPPLYDEPOT, 1, addon_pos, 1]])
                self._addon_block_done = True
                logger.info(
                    "ADDON_BLOCK_TEST spawned blocker@(%.1f,%.1f) 堵重工@(%.1f,%.1f) attempt=%d",
                    addon_pos.x,
                    addon_pos.y,
                    f.position.x,
                    f.position.y,
                    attempt,
                )
            except Exception as exc:
                logger.warning("ADDON_BLOCK_TEST fail: %s", exc)

        async def _maybe_spawn_carriers_far(self) -> None:
            """测试用(航母 standby 抽搐)：在**远离主基**处 debug 生 4 艘航母。

            配 mock LLM 注入「所有航母回家待命」→ standby tick 把它们从远处拉回主基。
            真局观察:航母是否平滑收敛回家(d_pos 单调降)、move 命令是否每帧重发(抽搐根因)。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 8.0 or not self.townhalls.ready:
                    return
                # 生在主基朝地图中心 ~55 格处(够远,d_pos >> STANDBY_RADIUS=10,必须飞回来)。
                far = self.start_location.towards(self.game_info.map_center, 55.0)
                await self._client.debug_create_unit([[UnitTypeId.CARRIER, 4, far, 1]])
                self._carrier_spawn_done = True
                logger.info(
                    "CARRIER_STANDBY_TEST spawned 4 carriers@(%.1f,%.1f) 远离主基", far.x, far.y
                )
            except Exception as exc:
                logger.warning("CARRIER_STANDBY_TEST fail: %s", exc)

        async def _maybe_spawn_bcs_for_harass(self) -> None:
            """测试用(#580 BC 群骚扰)：游戏早期在主基 debug 生 3 艘 BC。

            配 bc_rush 开局(active_recipe=bc_rush → director 自动建 group_harass claim) →
            GroupHarassAct 健康分状态机驱动群 BC 贴边飞向敌矿农民线。真局验链路 +
            终态(BCRAIDTRACE pos dist 变小 = SC2 真把 BC 飞到敌矿)。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 6.0 or not self.townhalls.ready:
                    return
                near = self.start_location.towards(self.game_info.map_center, 4.0)
                await self._client.debug_create_unit([[UnitTypeId.BATTLECRUISER, 3, near, 1]])
                self._bcharass_spawn_done = True
                logger.info(
                    "BCHARASS_SELFTEST spawned 3 BC@(%.1f,%.1f)(验自动建卡+骚扰微操)",
                    near.x,
                    near.y,
                )
            except Exception as exc:
                logger.warning("BCHARASS_SELFTEST fail: %s", exc)

        async def _maybe_spawn_reapers_for_harass(self) -> None:
            """测试用(harass_workers 通用骚扰执行器)：游戏早期在主基 debug 生 3 个死神。

            配非骚扰开局 + sandbox_macro_only(bot 自身不出门)+ mock LLM 注入
            "派死神去骚扰对方农民"(harass_workers claim)。被 claim 死神 Reserved,
            唯一能驱动它们的就是 director 每 tick 的 _execute_worker_harass_micro →
            干净归因(bot 自身不进攻,死神动=玩家 claim 路径生效)。真局验终态
            (WHARASSTRACE dist 变小 = 死神真飞到敌矿 + telemetry enemy_workers_harassed 上升)。
            """
            import os as _os_wh

            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 6.0 or not self.townhalls.ready:
                    return
                near = self.start_location.towards(self.game_info.map_center, 4.0)
                _n = int(_os_wh.environ.get("VIBECRAFT_WHARASS_COUNT", "6"))
                await self._client.debug_create_unit([[UnitTypeId.REAPER, _n, near, 1]])
                self._wharass_spawn_done = True
                logger.info(
                    "WHARASS_SELFTEST spawned %d REAPER@(%.1f,%.1f)(验 player_claim 骚扰微操)",
                    _n,
                    near.x,
                    near.y,
                )
            except Exception as exc:
                logger.warning("WHARASS_SELFTEST fail: %s", exc)

        async def _cclift_probe_step(self) -> None:
            """#560 linchpin：真机核对 CommandCenter LIFT/LAND ability 是否真能用。

            状态机(per probe，一次性)：
              1. 等主基 CC ready → 记 get_available_abilities(是否含 LIFT_COMMANDCENTER)
                 → 发 LIFT，落点锁定为「离主基最近的未占 expansion」(CLAUDE.md：落点起飞前锁死)。
              2. flying(COMMANDCENTERFLYING) → 每帧幂等 move(锁定落点)；近落点发 LAND(对锁定点)。
              3. 落地(目标点附近出现非飞行 CC) → 记 CCLIFTPROBE landed = 终态成功。
            全程记 CCLIFTPROBE，验终态(CC 真飞到目标落地)，不只看"发了命令"。
            """
            from sc2.ids.ability_id import AbilityId
            from sc2.ids.unit_typeid import UnitTypeId
            from sc2.position import Point2

            try:
                phase = getattr(self, "_cclift_phase", "wait")
                now = float(self.time)
                if phase == "wait":
                    # CC 只有 idle(不在产 SCV)时才有 LIFT —— 真机已验主基 CC 因常产 SCV 永不可 lift。
                    # spare CC 场景 = 玩家额外造的、不产兵的 idle CC。debug 生一个 idle CC 代表它。
                    if now < 6.0:
                        return
                    if not getattr(self, "_cclift_spawned", False):
                        near = self.start_location.towards(self.game_info.map_center, 7.0)
                        await self._client.debug_create_unit(
                            [[UnitTypeId.COMMANDCENTER, 1, near, 1]]
                        )
                        self._cclift_spawned = True
                        return
                    # 找那个 idle 的 spare CC（不是主基那个在产兵的）
                    ccs = self.townhalls(UnitTypeId.COMMANDCENTER).ready.filter(lambda c: c.is_idle)
                    if not ccs:
                        return
                    cc = ccs.first
                    abilities = await self.get_available_abilities(cc)
                    has_lift = AbilityId.LIFT_COMMANDCENTER in abilities
                    # 锁定落点 = 离主基最近的未占 expansion（确定性）
                    locs = list(getattr(self, "expansion_locations_list", []) or [])
                    start = self.start_location
                    free = [p for p in locs if p.distance_to(start) > 5.0]
                    target = min(free, key=lambda p: p.distance_to(start)) if free else start
                    self._cclift_target = Point2((target.x, target.y))
                    self._cclift_tag = cc.tag
                    logger.warning(
                        "CCLIFTPROBE start cc_tag=%d has_lift=%s target=(%.1f,%.1f) n_abilities=%d",
                        cc.tag,
                        has_lift,
                        target.x,
                        target.y,
                        len(abilities),
                    )
                    if has_lift:
                        cc(AbilityId.LIFT_COMMANDCENTER)
                        self._cclift_phase = "lifting"
                    else:
                        names = sorted(a.name for a in abilities)
                        logger.warning(
                            "CCLIFTPROBE FAIL: LIFT_COMMANDCENTER 不在 available abilities; "
                            "cc type=%s is_idle=%s orders=%d available=%s",
                            cc.type_id.name,
                            cc.is_idle,
                            len(cc.orders),
                            names,
                        )
                        self._cclift_phase = "done"
                    return
                if phase in ("lifting", "flying"):
                    tgt = self._cclift_target
                    flying = self.structures(UnitTypeId.COMMANDCENTERFLYING)
                    if flying:
                        if phase == "lifting":
                            logger.warning("CCLIFTPROBE lifted_ok COMMANDCENTERFLYING 出现")
                            self._cclift_phase = "flying"
                        f = flying.first
                        d = f.distance_to(tgt)
                        if d <= 4.0:
                            f(AbilityId.LAND_COMMANDCENTER, tgt)
                        else:
                            f.move(tgt)  # 幂等飞向锁定落点
                    else:
                        # 没 flying 了：要么还没起飞，要么已落地 → 检查终态
                        landed = self.townhalls(UnitTypeId.COMMANDCENTER).ready.closer_than(
                            6.0, tgt
                        )
                        if landed:
                            logger.warning(
                                "CCLIFTPROBE landed_ok CC 落到目标(%.1f,%.1f) dist=%.1f = 终态成功",
                                tgt.x,
                                tgt.y,
                                landed.first.distance_to(tgt),
                            )
                            self._cclift_phase = "done"
                    return
            except Exception as exc:
                logger.warning("CCLIFTPROBE error: %s", exc)
                self._cclift_phase = "done"

        async def _maybe_spawn_spare_cc(self) -> None:
            """测试用(#560)：在远离矿的地方 debug 生 1 个 idle spare CC（代表玩家预造的额外 CC）。

            放在 start_location 朝地图中心偏 12 格（清开主基矿线，使 mineral_field.closer_than(10)
            为空 → SpareCcExpandAct 判定为 spare）。生好后交给 act 自动飞去最近未占扩张点开矿。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 6.0 or not self.townhalls.ready:
                    return
                # 落在主基台地有效地形上(towards center 7，与已验证的 cclift_probe 同点)，
                # 既清开主基矿线(>10 格→判为 spare)，又是合法可起飞地形(12 格可能落到斜坡/悬崖
                # 无效地形 → debug 能生但 CC 处于无效建造态、没有 LIFT ability)。
                near = self.start_location.towards(self.game_info.map_center, 7.0)
                await self._client.debug_create_unit([[UnitTypeId.COMMANDCENTER, 1, near, 1]])
                self._sparecc_spawned = True
                logger.info(
                    "SPARECC_SELFTEST spawned idle spare CC@(%.1f,%.1f)(验自动飞去开矿)",
                    near.x,
                    near.y,
                )
            except Exception as exc:
                logger.warning("SPARECC_SELFTEST fail: %s", exc)

        async def _maybe_damage_building_for_repair(self) -> None:
            """测试用(#551 维修指令)：bot 真实建好 1 座地堡后，把它**持续打残**一个窗口(life→50)。

            为什么持续而非一次性：sharpy 自带 Repair() 会在 1-2 tick 内自动把残血结构修满，
            一次性打残会被 sharpy 抢先修好、看不到我方 repair 指令的 dispatch。持续打残一个窗口
            (首次打残后 _REPAIR_DMG_WINDOW_S 游戏秒内每 tick 重设 life=50)→ 窗口内 hp 一直 <0.99
            → director._tick_repair_orders 必然 dispatch(REPAIRTRACE repair_dispatched，验我方
            指令真机 facade.ensure_repair/get_unit_health_percentage 路径)；窗口结束停止打残 →
            SCV 把它修满 → repair_done_all_healthy(终态)。用真实建造的地堡确保 SCV.repair 真路径。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            _REPAIR_DMG_WINDOW_S = 120.0
            try:
                bunkers = self.structures(UnitTypeId.BUNKER).ready
                if not bunkers:
                    return
                start = getattr(self, "_repair_dmg_start", None)
                now = float(self.time)
                if start is None:
                    self._repair_dmg_start = now
                    start = now
                if now - start > _REPAIR_DMG_WINDOW_S:
                    return  # 窗口已过 → 停止打残，放手让 SCV 修满
                b = bunkers.first
                # life=2(生命值)；地堡满血 400，设 50 = 残血，留足修理空间
                await self._client.debug_set_unit_value([b.tag], 2, 50.0)
                if not getattr(self, "_repair_damage_logged", False):
                    self._repair_damage_logged = True
                    logger.info(
                        "REPAIR_SELFTEST damaged bunker tag=%d life->50 (持续 %.0fs 窗口，验维修 dispatch+终态)",
                        b.tag,
                        _REPAIR_DMG_WINDOW_S,
                    )
            except Exception as exc:
                logger.warning("REPAIR_SELFTEST damage fail: %s", exc)

        async def _maybe_spawn_enemy_at_main(self) -> None:
            """测试用(规则1):游戏时间 > 240 后,在主基地附近 debug 生一股强敌(owner=2)。

            主基地通常是最靠后的基地(army 在最前沿守);生强敌后该 zone assaulting_enemy_power
            应 >3 → army 迁回防主基。验"敌近某基地优先守该基地"。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 240.0:
                    return
                near = self.start_location.towards(self.game_info.map_center, 6.0)
                # 12 蟑螂(地面,power 够过阈值 3.0),owner=2 = 敌方
                await self._client.debug_create_unit([[UnitTypeId.ROACH, 12, near, 2]])
                self._defend_enemy_spawned = True
                logger.info(
                    "DEFEND_SPAWN_ENEMY 12 roaches@(%.1f,%.1f) 主基附近(验 army 迁回防)",
                    near.x,
                    near.y,
                )
            except Exception as exc:
                logger.warning("DEFEND_SPAWN_ENEMY fail: %s", exc)

        async def _maybe_force_bases(self) -> None:
            """测试用:在 natural/third/fourth 分矿点 debug 生 Nexus(owner=1),强制稳定多基地。

            目的:让 home(主基)和最前沿基地(距敌最近)明显分开,验 defend army 去的是哪个。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 20.0:
                    return
                exps = list(getattr(self, "expansion_locations_list", []) or [])
                if len(exps) < 4:
                    return
                # [0]=主基(已有);在 [1..3](natural/third/fourth)各生一个 Nexus
                for p in exps[1:4]:
                    await self._client.debug_create_unit([[UnitTypeId.NEXUS, 1, p, 1]])
                self._defend_bases_forced = True
                logger.info("DEFEND_FORCE_BASES spawned 3 Nexus @ natural/third/fourth")
            except Exception as exc:
                logger.warning("DEFEND_FORCE_BASES fail: %s", exc)

        async def _maybe_spawn_marines(self) -> None:
            """测试用(复现"大军在家出不了门"):游戏 >40s 在主基生 60 枪兵。"""
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 40.0:
                    return
                await self._client.debug_create_unit(
                    [[UnitTypeId.MARINE, 60, self.start_location, 1]]
                )
                self._marines_spawned = True
                logger.info("SPAWN_MARINES 60 枪兵@主基(复现大军在家)")
            except Exception as exc:
                logger.warning("SPAWN_MARINES fail: %s", exc)

        async def _maybe_flicker_enemy(self) -> None:
            """测试用(复现 defend 拉扯):游戏 >60s 起,每 ~8s 在主基附近刷 10 蟑螂(owner=2)。

            模拟敌人反复进出骚扰主基 → PlanZoneDefense 反复 claim 大军回防主基(enemy_center)
            打完 release → PlanZoneGather 拉回前沿守点(effective_gp)→ 大军在 主基↔前沿 间来回
            横跳 = 用户报的"保持队形原地拉扯"。确定性复现(枪兵固定 60 + defend pin + 周期刷敌)。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                t = float(self.time)
                if t < 60.0:
                    return
                last = getattr(self, "_flicker_last_t", -100.0)
                if t - last < 10.0:
                    return
                # 少量(4)防累积:60 枪兵能在 10s 周期内打完一波,不堆积razebase;够过 power 阈值
                # 3.0 触发 PlanZoneDefense claim。生在主基外 ~16 格,与守点拉开距离看 army 来回。
                near = self.start_location.towards(self.game_info.map_center, 16.0)
                await self._client.debug_create_unit([[UnitTypeId.ROACH, 4, near, 2]])
                self._flicker_last_t = t
                logger.info(
                    "DEFEND_FLICKER 4 roaches@(%.0f,%.0f) t=%.0f(周期骚扰)", near.x, near.y, t
                )
            except Exception as exc:
                logger.warning("DEFEND_FLICKER fail: %s", exc)

        async def _maybe_caster_selftest(self) -> None:
            """测试用(验科技单位主动技能真触发):游戏 >30s 解锁全升级(隐形/潜地等研究门)+
            在主基生 caster(鬼/女妖)+ 敌人(感染虫给鬼狙、枪兵给女妖隐形)。caster 是 Idle →
            进 free_units → 被 attack/defense plan 加进 combat group → 跑 micro。敌就在旁边 →
            接敌 → 技能触发。配 VIBECRAFT_CASTER_TRACE=1 看 CASTERTRACE 日志。
            """
            from sc2.ids.unit_typeid import UnitTypeId

            try:
                if float(self.time) < 30.0:
                    return
                # 解锁全部升级(女妖隐形/蟑螂潜地等是研究门;debug_upgrade 给本方全研究 →
                # cd_manager.is_ready 才会对这些技能返 True)
                await self._client.debug_upgrade()
                main = self.start_location
                near = main.towards(self.game_info.map_center, 6.0)
                # 鬼 + 感染虫(生物,给狙击目标)+ 叉子(给 EMP 护盾目标);女妖 + 枪兵(给隐形场景)
                await self._client.debug_create_unit(
                    [
                        [UnitTypeId.GHOST, 3, main, 1],
                        [UnitTypeId.BANSHEE, 3, main, 1],
                        [UnitTypeId.INFESTOR, 2, near, 2],
                        [UnitTypeId.STALKER, 3, near, 2],
                        [UnitTypeId.MARINE, 6, near, 2],
                    ]
                )
                self._caster_selftest_done = True
                logger.info("CASTER_SELFTEST upgrades unlocked + spawned ghost/banshee + enemies")
            except Exception as exc:
                logger.warning("CASTER_SELFTEST fail: %s", exc)

        def _defend_trace(self) -> None:
            """测试用(全体防守智能选点):每帧记 intent + 各己方 zone 威胁值/中心 + army 中心。

            读它判:规则1 敌近某 zone 时 assaulting_enemy_power 是否真>0(信号活)、army 是否去该 zone;
            规则2 无威胁时 army 是否在"距敌主基最近的己方 zone"(最前沿)而非 natural。
            """
            import contextlib

            try:
                vbc = getattr(self.knowledge, "vibecraft", None)
                intent = getattr(vbc, "combat_intent_override", None)
                zm = getattr(self.knowledge, "zone_manager", None)
                zones = getattr(zm, "expansion_zones", None) or []
                parts = []
                for i, z in enumerate(zones):
                    if not getattr(z, "is_ours", False):
                        continue
                    pw = getattr(getattr(z, "assaulting_enemy_power", None), "power", 0.0)
                    c = z.center_location
                    parts.append(f"z{i}@({c.x:.0f},{c.y:.0f})pw={pw:.1f}")
                # army 中心(排除农民)
                from sc2.ids.unit_typeid import UnitTypeId

                army = self.units.exclude_type({UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE})
                ac = army.center if army else None
                ac_s = f"({ac.x:.0f},{ac.y:.0f})" if ac is not None else "none"
                # 最前沿基地 = 距敌主基(expansion_zones[-1])最近的己方 zone(规则2 期望 army 待这)
                fwd_s = "?"
                emain_s = "?"
                d_home = d_fwd = -1.0
                home = self.start_location
                with contextlib.suppress(Exception):
                    enemy_main = zones[-1].center_location
                    emain_s = f"({enemy_main.x:.0f},{enemy_main.y:.0f})"
                    own = [z for z in zones if getattr(z, "is_ours", False)]
                    if own:
                        fwd = min(own, key=lambda z: z.center_location.distance_to(enemy_main))
                        fc = fwd.center_location
                        fwd_s = f"({fc.x:.0f},{fc.y:.0f})"
                        if ac is not None:
                            d_fwd = ac.distance_to(fc)
                if ac is not None:
                    d_home = ac.distance_to(home)
                # d_home/d_fwd:army 离主基 vs 离最前沿基地的距离(谁小说明 army 在谁那)
                logger.info(
                    "DEFENDTRACE t=%.0f intent=%s army=%s home=(%.0f,%.0f) fwd=%s emain=%s "
                    "d_home=%.0f d_fwd=%.0f ours=[%s]",
                    float(self.time),
                    intent,
                    ac_s,
                    home.x,
                    home.y,
                    fwd_s,
                    emain_s,
                    d_home,
                    d_fwd,
                    " ".join(parts),
                )
            except Exception as exc:
                logger.debug("DEFENDTRACE fail: %s", exc)

        async def _tick_view_channel(self, now_s: float) -> None:
            if down_q is not None:
                try:
                    while True:
                        msg: dict[str, Any] = down_q.get_nowait()
                        msg_type = msg.get("type")
                        if msg_type == "command":
                            text = str(msg.get("text", ""))
                            if self.director is not None:
                                task = asyncio.create_task(
                                    run_command_with_echo_fn(
                                        self.director, text, now_s, echo_callback
                                    ),
                                    name=f"cmd-{now_s:.3f}",
                                )
                                self._cmd_tasks.append(task)
                                task.add_done_callback(self._on_cmd_task_done)
                        elif msg_type == "view_move":
                            target = msg.get("target_point", [0.0, 0.0])
                            if self.facade is not None:
                                self.facade.move_camera((float(target[0]), float(target[1])))
                        elif msg_type == "confirm_recommendation":
                            if self.director is not None:
                                self.director.confirm_recommendation(now_s)
                        elif msg_type == "dismiss_recommendation":
                            if self.director is not None:
                                self.director.dismiss_recommendation()
                        elif msg_type == "confirm_force_strategy":
                            if self.director is not None:
                                self.director.confirm_force_strategy(now_s)
                        elif msg_type == "cancel_force_strategy":
                            if self.director is not None:
                                self.director.cancel_force_strategy()
                        elif msg_type == "revoke_directive":
                            directive_id = msg.get("directive_id")
                            if directive_id and self.director is not None:
                                self.director.revoke_directive(directive_id, now_s)
                        elif msg_type == "tactical_action":
                            # UI 战术按钮：绕过 LLM 直接 submit TacticalObjectivePayload
                            verb = msg.get("verb", "")
                            mode = msg.get("mode")  # 2026-05-25:"all_in"|"probe"|None
                            if verb and self.director is not None:
                                self._submit_tactical_action(verb, now_s, mode=mode)
                        elif msg_type == "macro_action":
                            # WP-D 运营策略按钮：绕过 LLM 直接下 macro action（双维度）
                            dim = msg.get("dim")
                            value = msg.get("value")
                            if dim and value is not None and self.director is not None:
                                self._submit_macro_action(dim, value, now_s)
                        elif msg_type == "strategy_action":
                            # UI 剧本 chip：绕过 LLM 直接 submit StrategySetPayload
                            strategy_id = msg.get("strategy_id", "")
                            if strategy_id and self.director is not None:
                                self._submit_strategy_action(strategy_id, now_s)
                        elif msg_type == "confirm_clarification":
                            # 2026-05-24 玩家从 LLM clarification 选项选了一个
                            if self.director is not None:
                                option_index = int(msg.get("option_index", 0))
                                self.director.submit_clarification_choice(option_index, now_s)
                        elif msg_type == "cancel_clarification":
                            # 2026-05-24 玩家点 ❌ 取消 clarification
                            if self.director is not None:
                                self.director.cancel_clarification(now_s)
                        elif msg_type == "leave":
                            logger.info("bot 收到 leave 信号，等待 on_end")
                except queue_module.Empty:
                    pass

            if minimap_callback is not None and self._minimap_builder is not None:
                self._minimap_tick_count += 1
                if self._minimap_tick_count >= self._minimap_every:
                    self._minimap_tick_count = 0
                    try:
                        frame = self._minimap_builder.build(now_s)
                        minimap_callback(frame)
                    except Exception as exc:
                        logger.warning("minimap_build_failed: %s", exc)

            if self.facade is not None:
                await self.facade.drain_pending_actions()

            if getattr(self, "_telemetry", None) is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    await self._write_telemetry_snapshot()

            self._update_tactics_throttled(now_s)
            if self.director is not None:
                self.director.on_tick(now=now_s)
            try:
                self.facade.draw_debug_marks()  # WP-A: 每帧重画受控单位框(debug draw 必须每帧重发)
            except Exception as exc:
                logger.debug("draw_debug_marks fail: %s", exc)
            if self._decision_watcher is not None:
                self._decision_watcher.tick(self, now_s)

        async def _tick_bot_channel(self, py_sc2_iteration: int, now_s: float) -> None:
            if self.director is not None:
                try:
                    await self.director.execute_overrides_step(now_s)
                except Exception as exc:
                    logger.warning("execute_overrides_step fail: %s", exc)
                try:
                    await self.director.execute_tactics_step(now_s)
                except Exception as exc:
                    logger.warning("execute_tactics_step fail: %s", exc)
            # 2026-05-25 bug 11 修复:在 super().on_step() **之前** refresh
            # LLM_CONTROLLED → UnitTask.Reserved。否则 sharpy UnitRoleManager.update
            # 每 step had_task_set.clear() + 自动把 left_over 分给 Gathering,
            # DistributeWorkers 跟着派 unit.gather() 覆盖我们的 unit.move() →
            # LLM-controlled probe 被 sharpy 抢回采矿,不去 watchtower 等目标。
            # 早调一次保证 sharpy update 时看到 probe 已在 Reserved 槽,直接跳过。
            self._refresh_llm_controlled_roles()
            # sharpy plan(build order + tactics)执行。单帧里任何一个 act/plan 抛异常
            # 默认会冒泡到 sc2.main:run_match → 整局崩溃退出(2026-06-19 用户反馈"打到一半
            # 异常退出":bc_late doctrine 里 TerranUnit(VIKING) 占位 enum → AssertionError
            # 杀了整局)。这里兜底:**全 catch + 落完整 traceback 到 game log**,让游戏继续跑,
            # 单帧出错只是这一帧 plan 不动,下一帧重试,事后靠日志定位根因。
            try:
                await super().on_step(self._sharpy_iteration)
            except Exception:
                logger.exception(
                    "sharpy on_step 抛异常(已吞,游戏继续) iter=%d t=%.1f",
                    self._sharpy_iteration,
                    now_s,
                )
            self._sharpy_iteration += 1
            # 保留 after on_step 这次:防 sharpy 内部某 Manager set 我们的 unit 进
            # 其它 slot(防御性兜底,跟 super.on_step 调度顺序无关地保证下 step 起手时
            # had_task_set 仍含我们的 tag)。
            self._refresh_llm_controlled_roles()
            # 2026-06-06 真局自验关键修复:代理建造的 u.build 在 super().on_step() **之前**发,
            # super 里的 DistributeWorkers 之后给同一农民下 gather → gather 是最后一道命令、覆盖
            # build → 远程代理农民被拽回家采矿、野外建筑建不出来(玩家:两个VS都在家里)。
            # 在 super 之后**再 drain 一次**代理建造队列 → build 成为最后一道命令,压过 gather。
            if self.facade is not None:
                try:
                    await self.facade.drain_pending_actions()
                except Exception as exc:
                    logger.debug("post-super proxy drain fail: %s", exc)
            # Bypass actions（如 salvage ability）：用 prevent_double=False 直发，
            # 绕开 python-sc2 prevent_double_actions 的 orders==[] bug。
            # 在 super().on_step() 之后串行 await，无并发 websocket 问题。
            _bypass = getattr(self, "_vibecraft_bypass_actions", None)
            if _bypass:
                try:
                    # result 含每条 action 的 ActionResult；非 Success 说明 ability 被 SC2 拒
                    # （如 NotSupported = ability enum 不对）—— 记日志便于真局排查。
                    _result = await self._do_actions(list(_bypass), prevent_double=False)
                    logger.info(
                        "bypass_actions: drained %d action(s) result=%r", len(_bypass), _result
                    )
                except Exception as exc:
                    logger.warning("bypass_actions: drain fail: %s", exc)
                _bypass.clear()

        async def _write_telemetry_snapshot(self) -> None:
            tel = self._telemetry
            now = float(self.time)
            # 2026-06-15 build 效率埋点：提前 due 判断 → 没到 2s 间隔直接跳过，
            # 省掉每帧 build record + 每帧异步 get_available_abilities（折跃门冷却读）的开销。
            if tel is None or not tel.due(now):
                return

            from sc2.ids.unit_typeid import UnitTypeId

            from vibecraft.bot.telemetry import (
                _KEY_UNIT_TYPES,
                build_economy_block,
                build_enemy_block,
                build_production_block,
                build_snapshot_record,
            )

            # race-agnostic：枚举 bot 实际拥有的单位 / 建筑，按 SDK type 名计数。
            # 不写死种族单位集，人族 / 虫族剧本验收才看得到自己的兵种 / 建筑。
            units_count: dict[str, int] = {}
            for u in self.units:
                units_count[u.type_id.name] = units_count.get(u.type_id.name, 0) + 1
            buildings_count: dict[str, int] = {}
            for s in self.structures.ready:
                buildings_count[s.type_id.name] = buildings_count.get(s.type_id.name, 0) + 1
            key_units: dict[str, list[Any]] = {}
            for name in _KEY_UNIT_TYPES:
                ut = getattr(UnitTypeId, name, None)
                if ut is not None:
                    ku = self.units(ut)
                    if ku:
                        key_units[name] = [u.position for u in ku]
            # army_center 供 verifier 的 attack_moveout / army_after_player_action 判定。
            # 排工人 + 非战斗支援(按兵种) + 持久任务单位(按 role ∈ {Reserved,Scouting},
            # 涵盖 harass/drop/proxy/合体/巡逻/守瞭望塔/侦察)。任务完成或玩家取消 → role
            # 归还 → 自动重新计入。统一定义见 telemetry.compute_army_center。
            from vibecraft.bot.telemetry import compute_army_center

            army_center = compute_army_center(self)
            army_supply = max(0, int(self.supply_army))
            # 经济明细：农民分配（采矿 / 采气 / 空闲）+ 每基地矿饱和度。
            # assigned/ideal_harvesters 是 python-sc2 Unit 属性：townhall 的
            # ideal = 矿点数 ×2，gas building 的 ideal = 3（有气时）。
            townhalls = self.townhalls.ready
            gas_ready = self.gas_buildings.ready
            economy = build_economy_block(
                mineral_workers=sum(int(th.assigned_harvesters) for th in townhalls),
                gas_workers=sum(int(g.assigned_harvesters) for g in gas_ready),
                idle_workers=int(self.workers.idle.amount),
                mineral_ideal=sum(int(th.ideal_harvesters) for th in townhalls),
                gas_ideal=sum(int(g.ideal_harvesters) for g in gas_ready),
                base_saturation=[
                    [int(th.assigned_harvesters), int(th.ideal_harvesters)] for th in townhalls
                ],
            )
            # 敌方观测：当前视野内的敌方农民 + 军队（L2 接触 / L3 农民损失判定）。
            # 战争迷雾下只数得到视野内的 —— 骚扰单位摸进对方矿区那段才有意义。
            enemy_seen = self.enemy_units
            enemy_workers_seen = enemy_seen.filter(
                lambda u: u.type_id in {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
            )
            enemy_army_seen = enemy_seen.exclude_type(
                {
                    UnitTypeId.PROBE,
                    UnitTypeId.SCV,
                    UnitTypeId.DRONE,
                    UnitTypeId.MULE,
                    UnitTypeId.OVERLORD,
                    UnitTypeId.OVERSEER,
                    UnitTypeId.LARVA,
                    UnitTypeId.EGG,
                    UnitTypeId.OBSERVER,
                }
            ).filter(lambda u: not u.is_structure)
            enemy = build_enemy_block(
                enemy_workers=enemy_workers_seen.amount,
                enemy_army_count=enemy_army_seen.amount,
                enemy_army_center=(enemy_army_seen.center if enemy_army_seen else None),
                enemy_workers_harassed=len(self._harassed_worker_tags),
                enemy_workers_killed=len(self._killed_worker_tags),
            )
            # 2026-05-28 诊断:vibecraft override 状态 per snapshot
            try:
                from vibecraft.bot.telemetry import extract_tactical_state

                tactical = extract_tactical_state(self)
            except Exception:
                tactical = None
            # 2026-06-11 偷矿 offline 可观测:每 snapshot 落 cell 状态 + nexus_assigned(DRAIN 信号)
            try:
                from vibecraft.bot.telemetry import extract_stealth_cells

                stealth_cells = extract_stealth_cells(self)
            except Exception:
                stealth_cells = []
            # 2026-06-15 M2 产能利用率埋点：折跃门冷却态需异步 get_available_abilities
            # （ready=可 warp=idle 浪费；不可 warp=冷却中=busy 发挥作用）。仅 due 这帧算一次。
            wg_total = 0
            wg_busy = 0
            try:
                warpgates = self.structures(UnitTypeId.WARPGATE).ready
                wg_total = int(warpgates.amount)
                if wg_total:
                    from sc2.ids.ability_id import AbilityId

                    _warp_set = {
                        AbilityId.WARPGATETRAIN_ZEALOT,
                        AbilityId.WARPGATETRAIN_STALKER,
                        AbilityId.WARPGATETRAIN_SENTRY,
                        AbilityId.WARPGATETRAIN_HIGHTEMPLAR,
                        AbilityId.WARPGATETRAIN_DARKTEMPLAR,
                    }
                    avail = await self.get_available_abilities(warpgates)
                    for abils in avail:
                        if not any(a in _warp_set for a in abils):
                            wg_busy += 1  # 不能 warp = 冷却中 = busy
            except Exception as exc:
                logger.debug("warpgate util read fail: %s", exc)
                wg_total = wg_busy = 0
            try:
                production = build_production_block(
                    self, warpgate_total=wg_total, warpgate_busy=wg_busy
                )
            except Exception as exc:
                logger.debug("build_production_block fail: %s", exc)
                production = None
            # opening_completed_at：Director 在 notify_opening_completed 时 latch（None=未完成）。
            opening_at = getattr(getattr(self, "director", None), "_opening_completed_at", None)
            rec = build_snapshot_record(
                t=now,
                supply_used=int(self.supply_used),
                supply_cap=int(self.supply_cap),
                workers=int(self.supply_workers),
                army_supply=army_supply,
                minerals=int(self.minerals),
                vespene=int(self.vespene),
                bases=int(self.townhalls.amount),
                army_center=army_center,
                units=units_count,
                buildings=buildings_count,
                key_units=key_units,
                active_recipe=str(getattr(self, "active_recipe", "")),
                economy=economy,
                enemy=enemy,
                tactical=tactical,
                stealth_cells=stealth_cells,
                production=production,
                opening_completed_at=opening_at,
            )
            rec["phoenix_lost"] = int(self._phoenix_lost)  # 凤凰累计损失(优化指标:越少越好)
            tel.maybe_write_snapshot(now, rec)

        def _tel_event(self, kind: str, unit: Any) -> None:
            tel = getattr(self, "_telemetry", None)
            if tel is None or getattr(unit, "alliance", 1) != 1:
                return  # 只记己方
            import contextlib

            with contextlib.suppress(Exception):
                from vibecraft.bot.telemetry import build_event_record

                tel.write_event(
                    build_event_record(
                        t=float(self.time),
                        kind=kind,
                        unit=str(unit.type_id).rsplit(".", 1)[-1],
                        tag=int(unit.tag),
                        pos=unit.position,
                    )
                )

        def _tel_event_destroyed(self, unit_tag: int) -> None:
            tel = getattr(self, "_telemetry", None)
            if tel is None:
                return
            import contextlib

            with contextlib.suppress(Exception):
                from vibecraft.bot.telemetry import build_event_record

                tel.write_event(
                    build_event_record(
                        t=float(self.time),
                        kind="unit_destroyed",
                        tag=int(unit_tag),
                    )
                )

        def _tel_event_upgrade(self, upgrade: Any) -> None:
            tel = getattr(self, "_telemetry", None)
            if tel is None:
                return
            import contextlib

            with contextlib.suppress(Exception):
                from vibecraft.bot.telemetry import build_event_record

                tel.write_event(
                    build_event_record(
                        t=float(self.time),
                        kind="upgrade_complete",
                        upgrade=str(upgrade).rsplit(".", 1)[-1],
                    )
                )

        def _submit_tactical_action(self, verb: str, now_s: float, mode: str | None = None) -> None:
            """UI 战术按钮：直接 submit TacticalObjectivePayload，绕过 LLM。

            2026-05-25 用户:mode="all_in"|"probe" 区分"强制全体进攻"/"试探性进攻"。
            UI 按钮战术 persistent=True 让 facade override 持续生效,玩家点 ×
            或选别的战术才换/清。
            """
            try:
                from vibecraft.directives.models import Directive, TacticalObjectivePayload
                from vibecraft.directives.types import IssuedBy

                # UI 按钮 = 玩家明确选的战术,持续生效直到 × 撤销
                # 2026-05-25 attack_mode 编进 payload,snapshot view 区分
                # "强制全体进攻"(all_in) vs "试探性进攻"(probe) 卡片文案
                payload = TacticalObjectivePayload(
                    verb=verb,
                    target_area=None,
                    persistent=True,  # type: ignore[arg-type]
                    attack_mode=mode if mode in ("all_in", "probe") else None,  # type: ignore[arg-type]
                )
                source = f"UI button: {verb}"
                if mode:
                    source = f"{source} mode={mode}"
                directive = Directive(
                    payload=payload,
                    issued_at=now_s,
                    issued_by=IssuedBy.VOICE,
                    source_text=source,
                )
                # 先 set attack_mode_override(directive submit 前),避免 ZoneAttack
                # 同帧读到 intent="attack" 但 mode 还没设导致用错 force_attack
                if mode and self.facade is not None:
                    set_mode = getattr(self.facade, "set_attack_mode_override", None)
                    if set_mode is not None:
                        set_mode(mode)
                self.director.submit_directive(directive, now_s)
                logger.info(
                    "tactical_action submitted via UI button verb=%s mode=%s",
                    verb,
                    mode,
                )
            except Exception as exc:
                logger.warning("tactical_action submit failed verb=%s err=%s", verb, exc)

        def _submit_macro_action(self, dim: str, value: object, now_s: float) -> None:
            """WP-D 运营策略按钮：直接调 director.apply_macro_action（双维度），绕过 LLM。"""
            try:
                if self.director is not None:
                    self.director.apply_macro_action(dim, value, now_s)
                    logger.info("macro_action applied via UI button dim=%s value=%s", dim, value)
            except Exception as exc:
                logger.warning("macro_action apply failed dim=%s value=%s err=%s", dim, value, exc)

        def _submit_strategy_action(self, strategy_id: str, now_s: float) -> None:
            """UI 剧本 chip：直接 submit StrategySetPayload，绕过 LLM / voice。

            stage 通过 strategy_library 反查（每个 strategy_id 对应哪个 stage）。
            """
            try:
                from vibecraft.directives.models import Directive, StrategySetPayload
                from vibecraft.directives.types import IssuedBy
                from vibecraft.strategy.models import (
                    LategameDoctrine,
                    MidgameStance,
                    OpeningBuild,
                    PersistentDoctrine,
                )

                if strategy_library is None:
                    logger.warning("strategy_action: no strategy_library, skip")
                    return

                try:
                    strat = strategy_library.get(strategy_id)
                except Exception:
                    logger.warning("strategy_action: unknown strategy_id=%s", strategy_id)
                    return

                if isinstance(strat, OpeningBuild):
                    stage = "opening"
                elif isinstance(strat, MidgameStance):
                    stage = "midgame"
                elif isinstance(strat, (LategameDoctrine, PersistentDoctrine)):
                    # 2026-05-23 用户:之前漏了 PersistentDoctrine(yaml kind:
                    # persistent_doctrine)→ 玩家点"持续运营"chip silent fail。
                    # 两类 doctrine 都映射到 lategame stage(StrategySetPayload.stage
                    # 只 opening/midgame/lategame 三选,新 PersistentDoctrine 没新增 stage)。
                    stage = "lategame"
                else:
                    logger.warning("strategy_action: unrecognized strategy type %s", type(strat))
                    return

                payload = StrategySetPayload(stage=stage, strategy_id=strategy_id)  # type: ignore[arg-type]
                directive = Directive(
                    payload=payload,
                    issued_at=now_s,
                    issued_by=IssuedBy.VOICE,
                    source_text=f"UI chip: {strategy_id}",
                )
                self.director.submit_directive(directive, now_s)
                logger.info(
                    "strategy_action submitted via UI chip strategy_id=%s stage=%s",
                    strategy_id,
                    stage,
                )
            except Exception as exc:
                logger.warning(
                    "strategy_action submit failed strategy_id=%s err=%s", strategy_id, exc
                )

        async def on_unit_created(self, unit: Any) -> None:
            _publish_unit_created(self, unit)
            self._tel_event("unit_created", unit)
            if getattr(unit, "alliance", 0) == 1:
                self._own_units_dict[unit.tag] = unit
            else:
                self._enemy_units_dict[unit.tag] = unit
            # 2026-05-19: 累加 DT 训练计数（latch，只增不减），macro_attack_ready 用
            import contextlib

            with contextlib.suppress(Exception):
                from sc2.ids.unit_typeid import UnitTypeId as _UTI3

                if getattr(unit, "alliance", 0) == 1:
                    if unit.type_id == _UTI3.DARKTEMPLAR:
                        self.knowledge.vibecraft.dt_trained_count += 1
                    elif unit.type_id == _UTI3.WARPPRISM:
                        self.knowledge.vibecraft.prism_built_count += 1
            # 偷矿出生即认领（2026-06-10 长局自验定位）：偷矿 Nexus 产的农民出生时是普通
            # role，DistributeWorkers 会抢先派去主矿。在出生这一刻（早于任何 plan）立即让
            # StealthCellManager 判断是否生在某 stealth Nexus 旁 → 是则当场 Reserved+认领。
            with contextlib.suppress(Exception):
                if (
                    getattr(unit, "alliance", 0) == 1
                    and unit.type_id == _UTI3.PROBE
                    and self.director is not None
                ):
                    mgr = getattr(self.director, "_stealth_manager", None)
                    if mgr is not None and mgr.cells:
                        _adopted = mgr.adopt_newborn(
                            int(unit.tag),
                            (float(unit.position.x), float(unit.position.y)),
                            self.facade,
                        )
                        # 立即把新农民写进 stealth_worker_tags SNS（不等下一帧 on_tick），
                        # 否则这一帧 ScoutWorker 等会把刚 adopt 的农民当普通工人抓走（实测 race）。
                        if _adopted:
                            self.facade.register_stealth_workers(mgr.stealth_worker_tags)
            if hasattr(super(), "on_unit_created"):
                await super().on_unit_created(unit)

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            if hasattr(self, "event_bus"):
                _publish_unit_destroyed(self, unit_tag)
            self._tel_event_destroyed(unit_tag)
            if unit_tag in self._llm_controlled_tags:
                self._llm_controlled_tags.discard(unit_tag)
                logger.info("unit_destroyed tag=%d removed from _llm_controlled_tags", unit_tag)
            if hasattr(self, "_own_units_dict"):
                own_dead = self._own_units_dict.pop(unit_tag, None)
                # 凤凰累计损失(优化指标:损失越少越好)。
                if own_dead is not None:
                    import contextlib

                    with contextlib.suppress(Exception):
                        from sc2.ids.unit_typeid import UnitTypeId as _UTIp

                        if own_dead.type_id == _UTIp.PHOENIX:
                            self._phoenix_lost += 1
            if hasattr(self, "_enemy_units_dict"):
                # pop 前查类型:视野内死亡的敌方农民计入 L3 骚扰计数 + 纯击杀计数。
                dead = self._enemy_units_dict.pop(unit_tag, None)
                if dead is not None:
                    import contextlib

                    with contextlib.suppress(Exception):
                        from sc2.ids.unit_typeid import UnitTypeId as _UTIw

                        if dead.type_id in {_UTIw.PROBE, _UTIw.SCV, _UTIw.DRONE}:
                            self._harassed_worker_tags.add(unit_tag)
                            self._killed_worker_tags.add(unit_tag)  # 纯击杀(阵亡)
            # 2026-05-19: 清理 damaged_dts 防 dict 泄漏
            import contextlib

            with contextlib.suppress(Exception):
                self.knowledge.vibecraft.damaged_dts.pop(unit_tag, None)
            await super().on_unit_destroyed(unit_tag)

        async def on_unit_type_changed(self, unit: Any, previous_type: Any) -> None:
            _publish_unit_type_changed(self, unit, previous_type)
            if hasattr(super(), "on_unit_type_changed"):
                await super().on_unit_type_changed(unit, previous_type)

        async def on_building_construction_started(self, unit: Any) -> None:
            _publish_building_started(self, unit)
            self._tel_event("building_started", unit)
            if hasattr(super(), "on_building_construction_started"):
                await super().on_building_construction_started(unit)

        async def on_building_construction_complete(self, unit: Any) -> None:
            _publish_building_complete(self, unit)
            self._tel_event("building_complete", unit)
            if hasattr(super(), "on_building_construction_complete"):
                await super().on_building_construction_complete(unit)

        async def on_upgrade_complete(self, upgrade: Any) -> None:
            _publish_upgrade_complete(self, upgrade)
            self._tel_event_upgrade(upgrade)
            if hasattr(super(), "on_upgrade_complete"):
                await super().on_upgrade_complete(upgrade)

        async def on_unit_took_damage(self, unit: Any, amount_damage_taken: Any) -> None:
            _publish_unit_took_damage(self, unit, amount_damage_taken)
            # 2026-05-19: 记 DT 受伤 timestamp 供 VibeCraftMicroDarkTemplar 决策撤退
            import contextlib

            with contextlib.suppress(Exception):
                from sc2.ids.unit_typeid import UnitTypeId as _UTI2

                if unit.type_id == _UTI2.DARKTEMPLAR and amount_damage_taken > 0:
                    self.knowledge.vibecraft.damaged_dts[unit.tag] = self.time
            # 受我方伤害的敌方农民 → 计入 L3 骚扰计数(打到即算,不强求击杀)。
            # python-sc2 的 on_unit_took_damage 对视野内的敌方单位也触发。
            with contextlib.suppress(Exception):
                from sc2.ids.unit_typeid import UnitTypeId as _UTId

                if (
                    getattr(unit, "alliance", 1) != 1
                    and unit.type_id in {_UTId.PROBE, _UTId.SCV, _UTId.DRONE}
                    and amount_damage_taken
                    and float(amount_damage_taken) > 0
                ):
                    self._harassed_worker_tags.add(unit.tag)
            if hasattr(super(), "on_unit_took_damage"):
                await super().on_unit_took_damage(unit, amount_damage_taken)

        async def on_enemy_unit_entered_vision(self, unit: Any) -> None:
            _publish_enemy_unit_entered_vision(self, unit)
            self._enemy_units_dict[unit.tag] = unit
            if hasattr(super(), "on_enemy_unit_entered_vision"):
                await super().on_enemy_unit_entered_vision(unit)

        async def on_enemy_unit_left_vision(self, unit_tag: int) -> None:
            _publish_enemy_unit_left_vision(self, unit_tag)
            if hasattr(super(), "on_enemy_unit_left_vision"):
                await super().on_enemy_unit_left_vision(unit_tag)

        def _on_cmd_task_done(self, task: asyncio.Task[Any]) -> None:
            import contextlib

            with contextlib.suppress(ValueError):
                self._cmd_tasks.remove(task)
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        "cmd_task_failed %s: %s",
                        task.get_name(),
                        exc,
                        exc_info=exc,
                    )

        async def on_end(self, game_result: Any) -> None:
            # 记录胜负到 telemetry（build_acceptance 据此统计真实胜率——2026-07-11）
            try:
                tel = getattr(self, "_telemetry", None)
                if tel is not None:
                    tel.write_event(
                        {
                            "t": float(self.time),
                            "kind": "game_result",
                            "result": str(game_result).rsplit(".", 1)[-1],
                        }
                    )
            except Exception:
                pass
            if self._hang_watchdog is not None:
                self._hang_watchdog.stop()
                self._hang_watchdog = None
            if self._cmd_tasks:
                await asyncio.gather(*self._cmd_tasks, return_exceptions=True)
                self._cmd_tasks.clear()
            if status_callback is not None:
                status_callback("ended", "idle", "")

    return VibeCraftBotBase
