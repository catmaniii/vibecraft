"""TelemetryLogger: 把 SC2 游戏内状态写成机读 telemetry.jsonl。

两路:
- 离散事件 (build_event_record) — 由 common_bot 的 BotAI 钩子调用
- 周期快照 (build_snapshot_record) — 由 common_bot.on_step 每 ~2s 调用一次

record 构造是纯函数(本模块上半部),便于单测;接线在 common_bot。
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


def _xy(pos: Any) -> list[float]:
    """Point2-like → [x, y] float 列表。"""
    return [float(pos.x), float(pos.y)]


def build_event_record(
    t: float,
    kind: str,
    unit: str | None = None,
    tag: int | None = None,
    pos: Any | None = None,
    upgrade: str | None = None,
) -> dict[str, Any]:
    """离散事件 record。kind ∈ building_started/building_complete/
    unit_created/unit_destroyed/upgrade_complete。"""
    rec: dict[str, Any] = {"t": round(float(t), 2), "kind": kind}
    if unit is not None:
        rec["unit"] = unit
    if tag is not None:
        rec["tag"] = int(tag)
    if pos is not None:
        rec["pos"] = _xy(pos)
    if upgrade is not None:
        rec["upgrade"] = upgrade
    return rec


def build_game_start_record(
    t: float,
    home: Any,
    enemy_main: Any,
    natural: Any | None,
    enemy_natural: Any | None,
    active_recipe: str,
    my_race: str,
    player_name: str = "",
    match_roster_json: str = "",
) -> dict[str, Any]:
    """开局 record — 记地图锚点供 verifier 解析命名位置。

    enemy_natural（敌方二矿)是骚扰/前压验收的关键锚点:L2「前压」判定
    主力是否到过敌方分矿就靠它。

    player_name：玩家昵称（来自 Room.slot.name，经 GameConfig.player_name →
    VIBECRAFT_PLAYER_NAME 环境变量透传；admin dashboard 对局记录显示用）。
    空串 = 未知（旧局 / build_acceptance 沙盒）。
    """
    return {
        "t": round(float(t), 2),
        "kind": "game_start",
        "home": _xy(home),
        "enemy_main": _xy(enemy_main),
        "natural": _xy(natural) if natural is not None else None,
        "enemy_natural": _xy(enemy_natural) if enemy_natural is not None else None,
        "active_recipe": active_recipe,
        "my_race": my_race,
        "player_name": player_name,
        # 整局参战方 roster（全部真人 + 电脑）→ admin 对局记录显示两人/玩家+电脑种族。
        # 解析失败 / 空 → []（旧局 / 沙盒）。
        "roster": _parse_roster(match_roster_json),
    }


def _parse_roster(roster_json: str) -> list[dict[str, Any]]:
    if not roster_json:
        return []
    try:
        data = json.loads(roster_json)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_economy_block(
    mineral_workers: int,
    gas_workers: int,
    idle_workers: int,
    mineral_ideal: int,
    gas_ideal: int,
    base_saturation: list[list[int]],
) -> dict[str, Any]:
    """经济明细子块 —— 农民分配 + 每基地矿饱和度。

    verifier / 人工排查运营质量靠它:gas_workers=0 → 没人采气;
    mineral_workers 远超 mineral_ideal → 农民过饱和;base_saturation
    每项 [assigned, ideal] 能看出哪个基地过饱和、哪个空着没填。
    """
    return {
        "mineral_workers": int(mineral_workers),
        "gas_workers": int(gas_workers),
        "idle_workers": int(idle_workers),
        "mineral_ideal": int(mineral_ideal),
        "gas_ideal": int(gas_ideal),
        "base_saturation": [[int(a), int(b)] for a, b in base_saturation],
    }


def build_enemy_block(
    enemy_workers: int,
    enemy_army_count: int,
    enemy_army_center: Any | None,
    enemy_workers_harassed: int = 0,
    enemy_workers_killed: int = 0,
) -> dict[str, Any]:
    """敌方观测子块 —— 视野内敌方单位 + 累计被骚扰农民数。

    enemy_workers / enemy_army_* 是「当前视野内」的瞬时观测:SC2 战争迷雾下
    bot 看不见全图。enemy_army_center 与自家 army_center 接近 = 双方主力
    接触过 → L2「前压」。

    enemy_workers_harassed 是**累计**值(只增):被我方「打到过」的不同敌方
    农民数(受我方伤害 ∪ 在我方视野内阵亡)。L3「骚扰是否到位」用它判 ——
    不强求击杀,把农民打跑/打残也算骚扰到位。

    enemy_workers_killed 是**累计击杀**值(只增):在我方视野内**阵亡**的不同敌方
    农民数（真击杀，区别于"打到"——凤凰骚扰优化以此为准，杀农民越多越好）。
    """
    return {
        "enemy_workers": int(enemy_workers),
        "enemy_army_count": int(enemy_army_count),
        "enemy_army_center": (_xy(enemy_army_center) if enemy_army_center is not None else None),
        "enemy_workers_harassed": int(enemy_workers_harassed),
        "enemy_workers_killed": int(enemy_workers_killed),
    }


def build_snapshot_record(
    t: float,
    supply_used: int,
    supply_cap: int,
    workers: int,
    army_supply: int,
    minerals: int,
    vespene: int,
    bases: int,
    army_center: Any | None,
    units: dict[str, int],
    buildings: dict[str, int],
    key_units: dict[str, list[Any]],
    active_recipe: str,
    economy: dict[str, Any],
    enemy: dict[str, Any],
    tactical: dict[str, Any] | None = None,
    stealth_cells: list[dict[str, Any]] | None = None,
    production: dict[str, Any] | None = None,
    opening_completed_at: float | None = None,
) -> dict[str, Any]:
    """周期快照 record。economy / enemy 分别由 build_economy_block /
    build_enemy_block 构造。

    tactical(2026-05-28 诊断,可选): per-snapshot vibecraft override 状态。
    None = 不带 tactical(向后兼容旧测试)。结构:
      {intent: str|None, stance: str|None, mode: str|None,
       target_set: bool, plan_status: str|None,
       attack_retreat_started: float|None}
    """
    rec = {
        "t": round(float(t), 2),
        "kind": "snapshot",
        "supply_used": int(supply_used),
        "supply_cap": int(supply_cap),
        "workers": int(workers),
        "army_supply": int(army_supply),
        "minerals": int(minerals),
        "vespene": int(vespene),
        "bases": int(bases),
        "army_center": _xy(army_center) if army_center is not None else None,
        "units": {k: int(v) for k, v in units.items()},
        "buildings": {k: int(v) for k, v in buildings.items()},
        "key_units": {k: [_xy(p) for p in v] for k, v in key_units.items()},
        "active_recipe": active_recipe,
        "economy": economy,
        "enemy": enemy,
    }
    if tactical is not None:
        rec["tactical"] = tactical
    if stealth_cells is not None:
        rec["stealth_cells"] = stealth_cells
    if production is not None:
        rec["production"] = production
    if opening_completed_at is not None:
        rec["opening_completed_at"] = round(float(opening_completed_at), 2)
    return rec


# build 效率评价 M2 数据源（2026-06-15）：army 产能建筑（orders-based busy）。
# 折跃门（cooldown-based）单独传入；虫族 larva 单独。
_ARMY_PROD_BUILDINGS: dict[str, dict[str, str]] = {
    "PROTOSS": {"gateway": "GATEWAY", "robo": "ROBOTICSFACILITY", "stargate": "STARGATE"},
    "TERRAN": {"barracks": "BARRACKS", "factory": "FACTORY", "starport": "STARPORT"},
}


def build_production_block(
    bot: Any, warpgate_total: int = 0, warpgate_busy: int = 0
) -> dict[str, Any]:
    """每帧产能利用率埋点（M2 数据源，build 效率评价系统）。

    - 神/人 army 产能建筑（BG/Robo/VS · 兵营/工厂/星港）：busy = 有训练订单（len(orders)>0）。
    - 折跃门（WarpGate）：busy = **冷却中**（刚 warp 过，发挥了作用）；ready 空着 = idle 浪费。
      冷却态需异步 `get_available_abilities` 读，由调用方算好 warpgate_total/busy 传进来。
    - 虫族：larva 机制，记 larva 数（scorer 据此算 larva 闲置积分）；busy/total 不适用、util=None。
    - util = Σbusy / Σtotal（神/人；total=0 → None）。util 名义 0-1，但**跨族语义不同、不可横向比**。
    """
    from sc2.ids.unit_typeid import UnitTypeId

    race = str(getattr(getattr(bot, "race", None), "name", "")).upper()
    block: dict[str, Any] = {}

    if race == "ZERG":
        # 虫族产能 = larva + 注卵（2026-06-15）。larva 闲置 = spend 不足；注卵覆盖 = 产能生成。
        larva = getattr(getattr(bot, "larva", None), "amount", 0)
        block["larva"] = int(larva)
        with contextlib.suppress(Exception):
            from sc2.ids.buff_id import BuffId
            from sc2.ids.unit_typeid import UnitTypeId

            queens = int(bot.units(UnitTypeId.QUEEN).amount)
            townhalls = bot.townhalls
            hatches = int(townhalls.amount)
            injected = sum(1 for th in townhalls if th.has_buff(BuffId.QUEENSPAWNLARVATIMER))
            block["queens"] = queens
            block["hatches"] = hatches
            block["injected_hatches"] = int(injected)
            # 注卵覆盖率 = 被注卵的基地占比（产能生成闸；与 larva 闲置=消耗闸 乘积 = 虫族产能分）
            block["inject_coverage"] = round(injected / hatches, 3) if hatches else None
        block["util"] = None  # 虫族 util 由 scorer 用 助卵×卵消耗 乘积另算
        return block

    busy_sum = 0
    total_sum = 0
    for key, type_name in _ARMY_PROD_BUILDINGS.get(race, {}).items():
        ut = getattr(UnitTypeId, type_name, None)
        if ut is None:
            continue
        ready = bot.structures(ut).ready
        total = int(ready.amount)
        busy = sum(1 for s in ready if getattr(s, "orders", None))
        block[key] = {"total": total, "busy": int(busy)}
        busy_sum += int(busy)
        total_sum += total
    if race == "PROTOSS":
        block["warpgate"] = {"total": int(warpgate_total), "busy": int(warpgate_busy)}
        busy_sum += int(warpgate_busy)
        total_sum += int(warpgate_total)
    block["util"] = round(busy_sum / total_sum, 4) if total_sum > 0 else None
    return block


def extract_stealth_cells(bot: Any) -> list[dict[str, Any]]:
    """抓偷矿 cell 状态 per snapshot —— **offline 可观测**。

    telemetry.jsonl 历来不带偷矿状态（只在 director UI 快照里、走手机 websocket，易失），
    导致真机偷矿无结构化记录、排障只能靠 self-test（2026-06-11 systematic-debugging 定位）。
    这里把每 cell 落进 telemetry：

      cell_id / state / location / worker_count / mineral_workers / gas_workers / has_gas
      + nexus_assigned —— SC2 引擎视角偷矿 Nexus 上的采矿农民数。
        **nexus_assigned > worker_count = 主矿农民倒灌（DRAIN 信号）**，离线可直接判 FENCE 健康。

    director / manager 不可达或无 cell → 返回 []。纯诊断，永不抛错。
    """
    out: list[dict[str, Any]] = []
    try:
        director = getattr(bot, "director", None)
        mgr = getattr(director, "_stealth_manager", None) if director is not None else None
        if mgr is None:
            return out
        for cell in mgr.cells.values():
            nexus_assigned = -1
            nt = getattr(cell, "nexus_tag", None)
            if nt is not None:
                try:
                    s = bot.structures.find_by_tag(nt)
                    if s is not None:
                        nexus_assigned = int(s.assigned_harvesters)
                except Exception:
                    nexus_assigned = -1
            wt = len(cell.worker_tags)
            gwt = len(cell.gas_worker_tags)
            out.append(
                {
                    "cell_id": cell.cell_id,
                    "state": cell.state.value,
                    "location": [cell.point[0], cell.point[1]],
                    "worker_count": wt,
                    "mineral_workers": max(0, wt - gwt),
                    "gas_workers": gwt,
                    "has_gas": bool(cell.gas_tags),
                    "nexus_assigned": nexus_assigned,
                }
            )
    except Exception:
        pass
    return out


# -----------------------------------------------------------------------
# TelemetryLogger：采集 + 写 telemetry（接线在 common_bot）
# -----------------------------------------------------------------------

_logger = logging.getLogger(__name__)

# 周期快照间隔（game-second）
_SNAPSHOT_INTERVAL_S: float = 2.0


def extract_tactical_state(bot: Any) -> dict[str, Any]:
    """2026-05-28 诊断:抓 vibecraft override + PlanZoneAttack 状态 per snapshot。

    用户反馈"实时战术切换经常失效"。telemetry 每 2s 一帧记 intent/stance/mode +
    PlanZoneAttack.status,出问题时能离线回放 intent 链路:
      - 按 retreat 后 intent 是否变为 retreat?
      - status 是否从 Attacking 转 Retreat?
      - attack_retreat_started 是否被 set?
    无 vibecraft 字段 / 无 sharpy plan 都安全返 dict(None 占位)。
    """
    vbc = getattr(getattr(bot, "knowledge", None), "vibecraft", None)
    tactical: dict[str, Any] = {
        "intent": getattr(vbc, "combat_intent_override", None) if vbc else None,
        "stance": getattr(vbc, "stance_override", None) if vbc else None,
        "mode": getattr(vbc, "attack_mode_override", None) if vbc else None,
        "target_set": getattr(vbc, "attack_target_override", None) is not None if vbc else False,
        "plan_status": None,
        "attack_retreat_started": None,
    }
    # 尝试找 PlanZoneAttack 实例(可能多个,取第一个非 NotActive 的)
    knowledge = getattr(bot, "knowledge", None)
    try:
        plans = _walk_plan_tree(knowledge)
        try:
            from sharpy.plans.tactics.zone_attack import PlanZoneAttack as _PZA
        except Exception:
            _PZA = None  # 导不到(单测/异常 sys.path)→ 纯靠名字兜底

        # #526 诊断:多人局 plan_status 恒 null。isinstance + 名字双判 —— 若 sharpy 经
        # sys.path 注入被双重导入致类身份不一致(isinstance 恒 False),名字兜底仍能命中;
        # 用 pza_via 记录命中方式,selftest 回放时可区分"isinstance 失效(身份不一致)"
        # vs"压根没找到 PlanZoneAttack 节点"vs"找到但 .status is None(plan 没跑过)"。
        candidates: list[tuple[Any, str]] = []
        for p in plans:
            if _PZA is not None and isinstance(p, _PZA):
                candidates.append((p, "isinstance"))
            elif type(p).__name__ in ("PlanZoneAttack", "PlanZoneAttackAllIn"):
                candidates.append((p, "name"))

        def _is_active(c: tuple[Any, str]) -> bool:
            st = getattr(c[0], "status", None)
            return st is not None and getattr(st, "name", None) not in (None, "NotActive")

        # 多 PZA build(skytoss/blink_harass)里优先取真正在战斗的那个(status 非 NotActive);
        # 单 PZA(多数 build)就是它自己。都 NotActive/无 → 取第一个(信号仍非 None)。
        pza: Any = None
        pza_via: str | None = None
        if candidates:
            chosen = next((c for c in candidates if _is_active(c)), candidates[0])
            pza, pza_via = chosen
        if pza is not None:
            status = getattr(pza, "status", None)
            tactical["plan_status"] = status.name if status is not None else None
            ars = getattr(pza, "attack_retreat_started", None)
            tactical["attack_retreat_started"] = round(ars, 2) if ars is not None else None
        # plan_status 仍 None → 落一行诊断面包屑(哪层断的),给多人局排查用
        if tactical["plan_status"] is None:
            ai = getattr(knowledge, "ai", None)
            mgrs = len(getattr(knowledge, "managers", None) or [])
            tactical["plan_dbg"] = (
                f"k={knowledge is not None},ai={ai is not None},"
                f"mgrs={mgrs},nodes={len(plans)},pza={pza_via or 'none'}"
            )
    except Exception as exc:
        tactical["plan_dbg"] = f"exc:{type(exc).__name__}"
    return tactical


# 真实 sharpy build_plan 树是**有限且浅**的(BuildOrder→ActList→Step→act,
# 嵌套深度个位数,总节点数最多几百)。两道防爆守卫针对畸形对象图:
#   _MAX_PLAN_DEPTH —— 主守卫。遍历靠 getattr(node,"act"/"orders") 驱动,若 node
#     不是真 sharpy 对象(例如单测塞的裸 MagicMock —— getattr(.,"act") 永远返回新
#     auto-child,形成无限深链),depth 上界在浅处(64 层)砍断,O(depth) 立即返回。
#   _MAX_PLAN_NODES —— 兜底守卫,防宽度方向异常增长。
# 真实树 depth<20、节点<几百,两个上界都远够;触顶 log warning,绝不静默吞掉。
_MAX_PLAN_DEPTH = 64
_MAX_PLAN_NODES = 10000


def _walk_plan_tree(knowledge: Any) -> list[Any]:
    """递归 BFS sharpy plan tree 找所有 act 实例。容错(missing attr 不抛)。

    三道防爆守卫:visited(按 id 去重,防真实树意外重入)+ _MAX_PLAN_DEPTH(主,
    砍无限深链)+ _MAX_PLAN_NODES(兜底宽度)。详见上方常量注释。
    """
    if knowledge is None:
        return []
    seen: list[Any] = []
    visited: set[int] = set()
    # queue 元素 = (node, depth)
    queue: list[tuple[Any, int]] = []
    # #526 真凶:sharpy 把 build plan 的 BuildOrder 根包在 knowledge.managers 里的
    # ActManager._act(见 vendor/sharpy .../knowledge_bot.py:69 `ActManager(self.create_plan)`
    # + act_manager.py `self._act = await create_plan()`),**不是** bot.build_plan ——
    # 那个属性根本不存在 → 旧代码 getattr(bot,"build_plan") 恒 None → 队列空 → 树根永远
    # 拿不到 → plan_status 恒 None(单/多人都坏;单测用 SimpleNamespace 手搭 build_plan 所以
    # 测试绿真局黑)。改:从 managers 找 ActManager._act 作根;legacy bot.build_plan 保留兜底。
    bot = getattr(knowledge, "ai", None)
    for m in getattr(knowledge, "managers", None) or []:
        act = getattr(m, "_act", None)
        if act is not None:
            queue.append((act, 0))
    legacy = getattr(bot, "build_plan", None) if bot is not None else None
    if legacy is not None:
        queue.append((legacy, 0))
    while queue:
        if len(seen) >= _MAX_PLAN_NODES:
            _logger.warning(
                "_walk_plan_tree 触顶 %d node 上界,提前停止 —— plan tree 异常大或非 sharpy 对象",
                _MAX_PLAN_NODES,
            )
            break
        node, depth = queue.pop(0)
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        seen.append(node)
        if depth >= _MAX_PLAN_DEPTH:
            # 不再下探;真实树到不了这深度,到此多半是畸形对象图(如 MagicMock 深链)。
            _logger.warning(
                "_walk_plan_tree 触顶 %d 层深度,停止下探 —— 非 sharpy 对象或异常嵌套",
                _MAX_PLAN_DEPTH,
            )
            continue
        # sharpy ActList / BuildOrder / SequentialList 都有 .orders 属性
        children = getattr(node, "orders", None)
        if children:
            for c in children:
                queue.append((c, depth + 1))
        # 单 act 包装器的子节点属性名(2026-06-13 修):
        #   - Step / IfElse 把内层 act 存在 `.action`(IfElse 还有 `.action_else`),**不是** `.act`。
        #     旧代码只查 `.act` → 永远进不去 Step/IfElse → PlanZoneAttack(总是
        #     `Step(gate, PlanZoneAttack)` 包在 `IfElse` 分支里)从来找不到 → plan_status 恒 None
        #     (单人/多人都一样,纯 telemetry 盲点,不代表 plan 没跑)。
        #   - `.act` 保留兜底(万一个别 act 真用这名字)。
        for attr in ("act", "action", "action_else"):
            inner = getattr(node, attr, None)
            if inner is not None and not isinstance(
                inner, (list, tuple, set, dict, str, int, float, bool)
            ):
                queue.append((inner, depth + 1))
    return seen


# 快照里要记「坐标」的关键单位 —— 骚扰 / 空投单位。
# units / buildings 只记**计数**走 race-agnostic 枚举（见
# common_bot._write_telemetry_snapshot），不写死种族单位集；这里额外记**坐标**,
# 给 key_unit_at check 用（验「骚扰单位是否到了对方家」）+ 人工排查骚扰执行。
# 选型:各 build 的核心骚扰 / 空投载具,数量少、坐标有判定意义；小狗这类成群
# 单位不记坐标（量大、army_center 已足够,且 12pool 走 L2 前压而非 key_unit_at）。
_KEY_UNIT_TYPES: tuple[str, ...] = (
    # 神族:棱镜(空投) / DT(隐刀偷家) / 凤凰(提农民)
    "WARPPRISM",
    "WARPPRISMPHASING",
    "DARKTEMPLAR",
    "PHOENIX",
    # 人族:死神 / 女妖(隐身偷农民) / 恶火(点农民) / 寡妇雷(含潜地形态) / 医疗船
    "REAPER",
    "BANSHEE",
    "HELLION",
    "WIDOWMINE",
    "WIDOWMINEBURROWED",
    "MEDIVAC",
    # 虫族:飞龙(打了就跑)
    "MUTALISK",
)


# army_center / 主力质心 —— 排除两类单位:
#   1. 工人 + 非战斗支援(永远不算主力,按兵种排)
#   2. 被指派持久任务的单位:sharpy role ∈ {Reserved, Scouting}(按 role 排)。
#      Reserved 涵盖 harass squad(凤凰/DT/Banshee/Muta)、drop、proxy、archon 合体、
#      玩家/LLM unit_claim(巡逻 / 守瞭望塔 / 棱镜回家);Scouting 涵盖侦察兵。这些有
#      独立持久任务,不参与主力 timing,算进质心会拖偏(实测 12 凤凰按 3:1 把质心拖到
#      敌方家 → 玩家 defend 后地面军已回家仍被误判出门)。任务完成/玩家手动取消 →
#      role 归还 Idle/Attacking → 自动重新计入主力。
# 旧实现按兵种硬编码排骚扰兵(DT/Muta/Phoenix...),它们当主力进攻时也被误排;改
# role-based 后只在真正执行持久任务时排除。
_NON_ARMY_UNIT_TYPES: frozenset[str] = frozenset(
    {
        "PROBE",
        "SCV",
        "DRONE",
        "MULE",  # 工人
        "OBSERVER",
        "WARPPRISM",
        "WARPPRISMPHASING",
        "OVERLORD",
        "LARVA",
        "EGG",
        "MEDIVAC",  # 非战斗支援
    }
)


# sharpy UnitTask 整数值(vendor unit_task.py 注释明示数值稳定):Scouting=3, Reserved=8。
# 直接用整数,不 import sharpy —— roles.all_from_task 接受 int,单测环境 sharpy 不在
# import path 上也能跑 role 排除(否则 import 失败会退化成纯兵种排除)。
_PERSISTENT_TASK_IDS: tuple[int, ...] = (3, 8)  # Scouting, Reserved


def _persistent_task_tags(bot: Any) -> set[int]:
    """role ∈ {Reserved, Scouting} 的单位 tag —— 被指派持久任务,排除出主力质心。

    接口异常 / 无 roles manager(单测 mock)→ 返空集,退化为纯兵种排除。
    """
    tags: set[int] = set()
    try:
        roles = bot.knowledge.roles
        for task_id in _PERSISTENT_TASK_IDS:
            tags.update(u.tag for u in roles.all_from_task(task_id))
    except Exception:
        pass
    return tags


def _filter_main_army(bot: Any) -> Any:
    """主力战斗单位过滤(排工人 / 非战斗支援 / 持久任务单位 / 建筑)。

    compute_army_center 和 compute_strongest_cluster_center 共用同一套过滤口径,
    保证 verifier / telemetry / view_follow 对"主力"的定义一致。
    """
    persistent = _persistent_task_tags(bot)
    return bot.units.filter(
        lambda u: (
            str(u.type_id.name).upper() not in _NON_ARMY_UNIT_TYPES
            and not u.is_structure
            and u.tag not in persistent
        )
    )


def compute_army_center(bot: Any) -> Any | None:
    """主力大部队质心(排工人 / 非战斗支援 + 排持久任务单位)。无主力 → None。

    common_bot telemetry snapshot + director hold 聚团点 / view_follow 共用这一处定义,
    保证 verifier 的 attack_moveout / army_after_player_action 与运行时主力判定一致。
    """
    try:
        army = _filter_main_army(bot)
        return army.center if army else None
    except Exception:
        return None


def compute_strongest_cluster_center(
    bot: Any,
    cluster_radius: float = 12.0,
    hysteresis: float = 1.25,
    prev_center: Any | None = None,
) -> Any | None:
    """主力战斗单位按距离聚团，返回造价(矿+气)最强团的质心。无主力 → None。

    - 单链聚团(union-find)：两单位 position 距离 <= cluster_radius 归同团。
    - 团战斗力 = sum(calculate_cost(u.type_id).minerals + .vespene)；
      calculate_cost 抛异常则该单位记为 0、整团兜底用单位数。
    - 迟滞：prev_center 非空时，找出最靠近 prev_center 的当前团作为"当前团"，
      只有别的团战斗力 > 当前团 * hysteresis 才切换，否则继续跟当前团(防镜头横跳)。
    - 主力单位数很少(<50)，O(n²) 无压力。
    """
    try:
        army = _filter_main_army(bot)
        if not army:
            return None

        units = list(army)
        n = len(units)
        if n == 0:
            return None

        # Union-Find 聚团
        parent = list(range(n))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(i: int, j: int) -> None:
            ri, rj = _find(i), _find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(n):
            for j in range(i + 1, n):
                pi = units[i].position
                pj = units[j].position
                dx = pi.x - pj.x
                dy = pi.y - pj.y
                if (dx * dx + dy * dy) <= cluster_radius * cluster_radius:
                    _union(i, j)

        # 按 root 分组
        clusters: dict[int, list[int]] = {}
        for i in range(n):
            root = _find(i)
            clusters.setdefault(root, []).append(i)

        # 计算每团质心 + 战斗力
        def _cluster_center(idxs: list[int]) -> Any:
            xs = [units[i].position.x for i in idxs]
            ys = [units[i].position.y for i in idxs]
            # 复用 army.center 类型：构造与 python-sc2 Point2 兼容的对象
            return SimpleNamespace(x=sum(xs) / len(xs), y=sum(ys) / len(ys))

        def _cluster_power(idxs: list[int]) -> float:
            total = 0.0
            for i in idxs:
                try:
                    cost = bot.calculate_cost(units[i].type_id)
                    total += float(cost.minerals) + float(cost.vespene)
                except Exception:
                    pass
            # 兜底：若全部 calculate_cost 失败 → 用单位数
            if total == 0.0:
                total = float(len(idxs))
            return total

        cluster_list = list(clusters.values())
        centers = [_cluster_center(idxs) for idxs in cluster_list]
        powers = [_cluster_power(idxs) for idxs in cluster_list]

        # 迟滞逻辑
        if prev_center is not None:
            # 找最靠近 prev_center 的团作为"当前团"
            def _dist2(c: Any) -> float:
                dx = float(c.x) - float(prev_center.x)
                dy = float(c.y) - float(prev_center.y)
                return dx * dx + dy * dy

            cur_idx = min(range(len(centers)), key=lambda k: _dist2(centers[k]))
            cur_power = powers[cur_idx]

            best_idx = max(range(len(powers)), key=lambda k: powers[k])
            if best_idx != cur_idx and powers[best_idx] <= cur_power * hysteresis:
                # 当前团足够强，不切换
                return centers[cur_idx]
            return centers[best_idx]

        # 无迟滞：直接返回最强团质心
        best_idx = max(range(len(powers)), key=lambda k: powers[k])
        return centers[best_idx]

    except Exception:
        return None


# ---------------------------------------------------------------------------
# 镜头跟随聚焦点（2026-06-03 用户三规则：移动看前方 / 停止看本身 / 交战看双方团重心）
# ---------------------------------------------------------------------------


def _union_find_clusters(positions: list[tuple[float, float]], radius: float) -> list[list[int]]:
    """按距离 <= radius 把点聚团，返回每团的下标列表（union-find，O(n²)）。"""
    n = len(positions)
    parent = list(range(n))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    r2 = radius * radius
    for i in range(n):
        for j in range(i + 1, n):
            dx = positions[i][0] - positions[j][0]
            dy = positions[i][1] - positions[j][1]
            if dx * dx + dy * dy <= r2:
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(_find(i), []).append(i)
    return list(groups.values())


def _centroid_of(units: list[Any]) -> Any:
    xs = [u.position.x for u in units]
    ys = [u.position.y for u in units]
    return SimpleNamespace(x=sum(xs) / len(xs), y=sum(ys) / len(ys))


def _dist2(a: Any, b: Any) -> float:
    dx = float(a.x) - float(b.x)
    dy = float(a.y) - float(b.y)
    return dx * dx + dy * dy


def strongest_cluster_units(
    bot: Any,
    cluster_radius: float = 12.0,
    hysteresis: float = 1.25,
    prev_center: Any | None = None,
) -> list[Any]:
    """主力按距离聚团，返回造价最强团的**单位列表**（迟滞同 compute_strongest_cluster_center）。

    给 view_follow army 模式用：拿到最强团的实际单位，才能在这些单位上算朝向 / 交战。
    无主力 → 空列表。
    """
    try:
        army = _filter_main_army(bot)
        units = list(army) if army else []
        if not units:
            return []
        positions = [(u.position.x, u.position.y) for u in units]
        groups = _union_find_clusters(positions, cluster_radius)

        def _power(idxs: list[int]) -> float:
            total = 0.0
            for i in idxs:
                try:
                    cost = bot.calculate_cost(units[i].type_id)
                    total += float(cost.minerals) + float(cost.vespene)
                except Exception:
                    pass
            return total if total > 0.0 else float(len(idxs))

        centers = [_centroid_of([units[i] for i in g]) for g in groups]
        powers = [_power(g) for g in groups]

        if prev_center is not None:
            cur = min(range(len(centers)), key=lambda k: _dist2(centers[k], prev_center))
            best = max(range(len(powers)), key=lambda k: powers[k])
            if best != cur and powers[best] <= powers[cur] * hysteresis:
                best = cur
        else:
            best = max(range(len(powers)), key=lambda k: powers[k])
        return [units[i] for i in groups[best]]
    except Exception:
        return []


def _filter_enemy_army(bot: Any) -> list[Any]:
    """可见敌方军队单位（排建筑 / 工人 / 非战斗支援）。失败 → 空。"""
    try:
        return [
            u
            for u in bot.enemy_units
            if str(u.type_id.name).upper() not in _NON_ARMY_UNIT_TYPES
            and not getattr(u, "is_structure", False)
        ]
    except Exception:
        return []


def _order_target_pos(bot: Any, u: Any) -> tuple[float, float] | None:
    """单位当前命令目标的坐标：Point2 直接取；tag 则解析成单位位置；取不到 → None。"""
    try:
        ot = getattr(u, "order_target", None)
        if ot is None:
            return None
        if isinstance(ot, int):  # 目标是单位 tag（攻击/跟随）→ 解析坐标
            try:
                tgt = bot.all_units.by_tag(ot)
            except Exception:
                tgt = None
            if tgt is not None:
                return (float(tgt.position.x), float(tgt.position.y))
            return None
        return (float(ot.x), float(ot.y))  # Point2-like
    except Exception:
        return None


def _follow_heading(
    bot: Any, units: list[Any], centroid: Any, prev_centroid: Any | None
) -> tuple[float, float] | None:
    """移动朝向单位向量：① order_target 方向均值 → ② 质心位移 → ③ facing 均值。取不到 → None。"""
    # ① 各单位 order_target 方向的归一均值（"往哪走"，最贴合意图）
    ax = ay = 0.0
    for u in units:
        if not getattr(u, "is_moving", False):
            continue
        tp = _order_target_pos(bot, u)
        if tp is None:
            continue
        dx, dy = tp[0] - centroid.x, tp[1] - centroid.y
        length = math.hypot(dx, dy)
        if length > 1e-3:
            ax += dx / length
            ay += dy / length
    length = math.hypot(ax, ay)
    if length > 1e-3:
        return (ax / length, ay / length)
    # ② 质心位移（上一帧→当前）
    if prev_centroid is not None:
        dx, dy = centroid.x - prev_centroid.x, centroid.y - prev_centroid.y
        length = math.hypot(dx, dy)
        if length > 0.5:
            return (dx / length, dy / length)
    # ③ facing 均值
    fx = fy = 0.0
    for u in units:
        f = getattr(u, "facing", None)
        if f is None:
            continue
        fx += math.cos(float(f))
        fy += math.sin(float(f))
    length = math.hypot(fx, fy)
    if length > 1e-3:
        return (fx / length, fy / length)
    return None


def _engaged_focus(
    bot: Any,
    units: list[Any],
    centroid: Any,
    combat_radius: float,
    cluster_radius: float,
    engage_radius: float,
) -> Any | None:
    """规则3：停止交战时，敌军聚团→取最近敌团→我方交战子集→双方并集重心。无交战 → None。"""
    enemies = _filter_enemy_army(bot)
    near = [e for e in enemies if _dist2(e.position, centroid) <= combat_radius * combat_radius]
    if not near:
        return None  # 没敌军近身 = 不算交战
    positions = [(e.position.x, e.position.y) for e in near]
    groups = _union_find_clusters(positions, cluster_radius)
    # 离我方质心最近的敌团 = 交战团
    best_g = min(
        groups,
        key=lambda g: _dist2(
            SimpleNamespace(
                x=sum(positions[i][0] for i in g) / len(g),
                y=sum(positions[i][1] for i in g) / len(g),
            ),
            centroid,
        ),
    )
    enemy_engaged = [near[i] for i in best_g]
    enemy_center = _centroid_of(enemy_engaged)
    # 我方在该敌团附近的交战子集（不是全军）
    our_engaged = [
        u for u in units if _dist2(u.position, enemy_center) <= engage_radius * engage_radius
    ]
    if not our_engaged:
        our_engaged = units
    # 交战双方单位并集的重心
    return _centroid_of(our_engaged + enemy_engaged)


def _clamp_to_map(bot: Any, pt: Any) -> Any:
    """把落点夹进可玩区域，避免前瞻偏移把镜头推出地图边界。"""
    try:
        pa = bot.game_info.playable_area
        margin = 2.0
        x = min(max(float(pt.x), pa.x + margin), pa.x + pa.width - margin)
        y = min(max(float(pt.y), pa.y + margin), pa.y + pa.height - margin)
        return SimpleNamespace(x=x, y=y)
    except Exception:
        return pt


def compute_follow_focus(
    bot: Any,
    units: list[Any],
    *,
    prev_centroid: Any | None = None,
    forward_offset: float = 7.0,
    move_units_frac: float = 0.4,
    combat_radius: float = 16.0,
    cluster_radius: float = 12.0,
    engage_radius: float = 14.0,
) -> Any | None:
    """被跟随单位集 → 镜头聚焦点（三规则）。无可用单位 → None。

    规则优先级：
      1. 移动中（≥move_units_frac 单位 is_moving）→ 质心 + 朝向 × forward_offset（看前方战场）。
      2. 停止 + 无敌军近身 → 质心本身。
      3. 停止 + combat_radius 内有敌军 → 交战双方部队团并集的重心。
    返回 SimpleNamespace(x, y)，已夹进地图可玩区域。
    """
    valid = [u for u in units if getattr(u, "position", None) is not None]
    if not valid:
        return None
    centroid = _centroid_of(valid)

    # is True 严格判定：真实 SC2 是 bool；MagicMock 假单位的 is_moving 是 truthy
    # MagicMock（不等于 True）→ 测试里按"停止"处理，不误入前瞻分支。
    moving = sum(1 for u in valid if getattr(u, "is_moving", False) is True) >= max(
        1, move_units_frac * len(valid)
    )
    if moving:
        heading = _follow_heading(bot, valid, centroid, prev_centroid)
        if heading is not None:
            ahead = SimpleNamespace(
                x=centroid.x + heading[0] * forward_offset,
                y=centroid.y + heading[1] * forward_offset,
            )
            return _clamp_to_map(bot, ahead)

    engaged = _engaged_focus(bot, valid, centroid, combat_radius, cluster_radius, engage_radius)
    if engaged is not None:
        return _clamp_to_map(bot, engaged)

    return _clamp_to_map(bot, centroid)


class TelemetryLogger:
    """采集 + 写 telemetry。sink_fn 接收一个 dict record(通常 = session.log 的偏函数)。"""

    def __init__(
        self,
        sink_fn: Callable[[dict[str, Any]], None],
        snapshot_interval_s: float = _SNAPSHOT_INTERVAL_S,
    ) -> None:
        self._sink = sink_fn
        self._snapshot_interval_s = snapshot_interval_s
        self._last_snapshot_t: float = -1000.0

    def due(self, now: float) -> bool:
        """距上次 snapshot 是否已到间隔（供调用方提前 return，省掉每帧 build record +
        每帧异步 get_available_abilities 的开销；只在真要写的那帧才算贵的部分）。"""
        return now - self._last_snapshot_t >= self._snapshot_interval_s

    def write_event(self, record: dict[str, Any]) -> None:
        """离散事件直接落盘,不节流。"""
        try:
            self._sink(record)
        except Exception as exc:
            _logger.warning("telemetry write_event fail: %s", exc)

    def maybe_write_snapshot(self, now: float, record: dict[str, Any]) -> None:
        """节流:距上次 snapshot >= interval 才写。"""
        if now - self._last_snapshot_t < self._snapshot_interval_s:
            return
        self._last_snapshot_t = now
        try:
            self._sink(record)
        except Exception as exc:
            _logger.warning("telemetry snapshot fail: %s", exc)
