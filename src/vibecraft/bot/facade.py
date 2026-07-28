"""Sc2Facade：bot 对 SC2 的全部需求接口。

设计原则：
1. 不暴露任何 sharpy / python-sc2 类型。所有参数都用 stdlib + dataclass。
2. 玩家指令要影响 bot 的每一类动作，都在这里有对应入口：
   - 切剧本            → `set_build`
   - 产能 / 科技覆盖    → `set_production_override` / `set_tech_override`
   - 单位归属          → `set_unit_role`
   - 镜头              → `move_camera` / `follow_unit` / `set_camera_zoom`
   - 建筑落点覆盖      → `set_build_location_override`
3. **查询**接口也走 facade：bot 内部不直接调 SC2 API，便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol

# L2 全军 combat intent 白名单。Protocol 跟 SharpyFacade impl 共用，
# 防止两端 Literal 漂移。
CombatIntent = Literal["attack", "defend", "hold", "retreat", "vision"]


class UnitRole(str, Enum):
    """vibecraft 内部的 unit role；运行时由 `sharpy_adapter` 映射到真实
    `sharpy.managers.core.roles.unit_task.UnitTask` 成员。

    **重要**：sharpy 的 UnitTask 是 IntEnum，固定成员。
    `LLM_CONTROLLED` 实际映射到 sharpy 的 `UnitTask.Reserved`（Reserved=8，
    sharpy 框架无任何 Manager 主动占用 Reserved slot）——
    这就是设计文档 §3.4 假设的 role 排除机制的载体。
    """

    LLM_CONTROLLED = "LLM_CONTROLLED"
    IDLE = "IDLE"
    ARMY = "ARMY"
    DEFENDER = "DEFENDER"
    HARASSER = "HARASSER"
    SCOUT = "SCOUT"


# =========================================================================
# BotState：facade 暴露给上层的只读快照
# =========================================================================


@dataclass
class BotState:
    """Snapshot of in-game state at a tick.

    构造 ParseContext 时用。不包含完整单位列表（开销大），按需查询。
    """

    game_time: float = 0.0
    minerals: int = 0
    gas: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    expansion_count: int = 1
    army_summary: dict[str, int] = field(default_factory=dict)
    enemy_summary: dict[str, int] = field(default_factory=dict)
    # 已造建筑名集合(全大写 UnitTypeId.name,含 ready + pending);用于剧本时机检测
    structures_built: frozenset[str] = field(default_factory=frozenset)
    # 2026-05-28 用户:LLM 解析"补一个 BF"需要知道当前 BF 数才能算 delta。
    # buildings_summary: {name: ready_count}(只 ready,不含 pending,玩家说"有 1 BF"
    # 通常指完工的)。
    buildings_summary: dict[str, int] = field(default_factory=dict)
    # 已完成升级名集合(全大写 UpgradeId.name);transition_cost 算科技缺口用
    upgrades: frozenset[str] = field(default_factory=frozenset)
    # 敌方种族('terran'/'zerg'/'protoss'/None);enemy_tags race-specific 推断用
    enemy_race: str | None = None


# =========================================================================
# Sc2Facade Protocol
# =========================================================================


class Sc2Facade(Protocol):
    """bot 对 SC2 的全部需求。

    ⚠️ 坑(2026-06-07 踩过):这是 `typing.Protocol`,**运行时不强制实现**。新增/改一个方法
    必须**同步两个实现**,否则单测全绿、真局静默失效:
      1. `FakeFacade`(本文件,单测/脚本用的 mock)
      2. `_SharpyFacadeBase`(auto_combat/common_bot.py,**真实游戏跑的就是它**)
    漏掉 (2) → Director 里 `hasattr(facade, "<m>")` 真机恒 False(或裸调 AttributeError)→ 该路径
    悄悄不工作,而单测用 (1) 有此方法 → 测不出。实例:`release_unit_role` 只在 FakeFacade 实现、
    _SharpyFacadeBase 漏了 → 取消任何指令/解散编队/释放单位全失效。
    防回归:`tests/unit/test_facade_release_unit_role.py` 有 Protocol 一致性 audit;
    详见 CLAUDE.md「改 Sc2Facade 接口必须同步两个实现 + 跑 audit」。
    """

    # ---- 写：剧本 / 生产 ----------------------------------------------

    def set_build(self, build_name: str) -> None: ...

    def set_production_override(
        self,
        unit_type: str,
        count: int,
        building_tag: int | None = None,
    ) -> None: ...

    def set_tech_override(
        self,
        upgrade_id: str,
        building_tag: int | None = None,
    ) -> None: ...

    def set_expansion_override(self, target_count: int | None) -> None: ...

    # ---- 写：单位 -----------------------------------------------------

    def set_unit_role(self, unit_tag: int, role: UnitRole) -> None: ...

    def release_unit_role(self, unit_tag: int) -> None:
        """归还 unit role 到 sharpy 默认（LLM_CONTROLLED 的反向操作）。

        实现层映射到 sharpy UnitTask.Idle 或 None，让 sharpy Manager
        在下一轮重新接管该单位（移出 Reserved slot）。
        """
        ...

    def execute_unit_action(
        self,
        unit_tag: int,
        verb: str,
        target: dict[str, object] | None = None,
        ability_id: str | None = None,
    ) -> None: ...

    # ---- 写：建造位置 / engagement -----------------------------------

    def set_build_location_override(
        self,
        structure_type: str,
        point: tuple[float, float],
    ) -> None: ...

    def set_engagement_stance(self, stance: str | None) -> None:
        """全军交战姿态覆盖（None / "free" = 清除，恢复 bot 自主决策）。"""
        ...

    def set_attack_target_override(self, point: tuple[float, float] | None) -> None:
        """L2 全军 attack target 覆盖（None = 清覆盖，恢复 sharpy 默认决策）。"""
        ...

    def set_combat_intent_override(
        self,
        intent: CombatIntent | None,
    ) -> None:
        """L2 全军交战意图覆盖（None = 清覆盖）。
        set_engagement_stance 的同源接口；stance 内部转发到此。"""
        ...

    def set_attack_mode_override(self, mode: str | None) -> None:
        """2026-05-25:战术按钮 attack 模式覆盖。

        "all_in" → ZoneAttack force_attack=True(不撤退);"probe" → False
        (走 sharpy power 判定);None → 用 plan 默认 force_attack。
        """
        ...

    def set_sustain_uncap_active(self, active: bool) -> None:
        """2026-05-27 Task #341:opening 完成超时后由 Director 调,启动 sustain uncap mode。"""
        ...

    def set_mining_priority(self, priority: str | None) -> None:
        """2026-07-06 采矿策略：设置全局采矿农民分配优先级。

        priority:
          "mineral" → 优先水晶（先把矿片采满，多出的才去采气）
          "gas"     → 优先气（先把气井采满，剩下的才采水晶）
          None      → 默认（清除 override，恢复 sharpy 剧本默认 min/max_gas）

        DistributeWorkers.execute patch 读 knowledge.vibecraft.mining_priority
        每帧动态覆写 min_gas/max_gas。
        """
        ...

    def set_upgrade_target(self, family: str, level: int | None) -> None:
        """2026-07-07 攻防升级目标等级：写入 knowledge.vibecraft.upgrade_targets。

        family: 升级线族名（无 LEVEL 后缀），如 'PROTOSSGROUNDWEAPONS'。
        level:
          0-3 → 手动封顶（vendor Tech.execute 门读此值，超出不研究）
          None → 自动（pop key，还给 bot 自决）

        vendor/sharpy tech.py::Tech.execute 封顶门每帧读 upgrade_targets.get(family)。
        """
        ...

    def set_hold_gather_point(self, point: tuple[float, float] | None) -> None:
        """2026-05-28 用户 hold:聚团 + 坚守。Director 算好聚团点(target_area 或
        current army_center 锁住)后调此 setter。vendor zone_gather hook 读
        intent=hold 时 effective_gather_point=此点。None = 清(切战术 / ×)。
        """
        ...

    def set_rally_point(self, point: tuple[float, float] | None) -> None:
        """出兵集结点（2026-06-07 用户）：覆盖 sharpy 全局 gather_point 到玩家设的点,
        之后新出的兵(PlanZoneGather)自动 rally 到此。**必须每帧调**(sharpy set_gather_point
        是一次性 flag,只生效 1 tick)。point=None → no-op(Director 停调即恢复 bot 默认)。
        """
        ...

    def set_regroup_started(self, ts: float | None) -> None:
        """2026-05-28 用户 probe/recon:聚团门 timer。玩家发 attack(probe)/recon
        时 set ts=current game_time;15s 内 vendor zone_attack _should_attack
        check spread 散开 → False(让 PlanZoneGather 集结);超时 bypass。
        None = 清(切其他战术 / × → 取消聚团 timer)。
        """
        ...

    def cast_chrono_boost_on_structure(
        self,
        structure_type: str,
        count: int = 1,
    ) -> int:
        """2026-05-25:Nexus 释放 Chrono Boost 到目标建筑(EFFECT_CHRONOBOOSTENERGYCOST)。

        玩家"给两个BF星空加速" → structure_type="Forge", count=2。
        返回成功 cast 次数(可能少于 count:Nexus 能量不足 / 目标少)。
        """
        ...

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
        MORPH_ARCHON 特殊：需要 2 个 HT 两两配对；count 表示合多少个白球（每个需 2 HT）。
        其他 ability（PSISTORM、FEEDBACK 等）：每个单位单独 cast 一次。
        奇数 HT 时最后一个 HT 不会被 cast（不足 2 个配对）。
        target_point（2026-06-20）：对**点施放**的 ability（如 EFFECT_TACTICALJUMP 大舰传送回家）
        传落点坐标 → `unit(ability, Point2(target_point))`；None = 自施放（archon/storm 等）。
        """
        ...

    # ---- 写：产能封锁（production_block）-----------------------------

    def block_production(self, unit_type: str) -> None:
        """2026-05-30 产能封锁：把 unit_type 加入 knowledge.vibecraft.production_blocked set。

        2026-06-02 机制级拦截：sharpy ActUnit.execute / WarpUnit.execute 在下训练/折跃
        指令前检查此 set，命中该兵种就跳过（return True，不下令也不阻塞 build order）。
        （原注释提到的"每 tick ProductionBlockAct 遍历取消队列"从未实现，已废弃。）
        见 docs/sharpy-patches.md §8。
        """
        ...

    def unblock_production(self, unit_type: str) -> None:
        """2026-05-30 产能封锁解除：从 production_blocked set 移除 unit_type。"""
        ...

    def set_phoenix_harass_active(self, active: bool) -> None:
        """2026-05-30 凤凰骚扰持久指令卡：set False 让 PhoenixSquadAct 释放 Reserved
        凤凰归队主力。玩家点×卡片 / 到硬性截止时间时 Director 调。"""
        ...

    # ---- 写：debug draw（游戏内可视化）-------------------------------

    def set_debug_marks(self, marks: list[dict[str, object]]) -> None:
        """设置本帧想画的 debug mark 清单。每 tick 由 Director 调，覆盖上一帧。

        marks 中每个元素格式：
          {"tag": int, "color": (r, g, b), "label": str | None}
        """
        ...

    def draw_debug_marks(self) -> None:
        """对 _debug_marks 清单中每个存活单位画框+飘字。

        **绝不调 bot.client._send_debug()**——框架每帧自动 flush；
        手动调会先清空绘制列表导致当帧画的全部消失。
        """
        ...

    # ---- 写：视野（不进 Board）---------------------------------------

    def move_camera(self, point: tuple[float, float]) -> None: ...

    def follow_unit(self, unit_tag: int) -> None: ...

    def set_camera_zoom(self, level: float) -> None: ...

    def get_camera_center(self) -> tuple[float, float] | None: ...

    # ---- 读：单位位置 -------------------------------------------------

    def get_unit_position(self, tag: int) -> tuple[float, float] | None:
        """返回 tag 单位当前坐标；单位不存在或死亡返回 None。"""
        ...

    # ---- 写：代理建造 -------------------------------------------------

    def order_probe_build(
        self,
        probe_tag: int,
        structure_type: str,
        point: tuple[float, float],
        cache_key: object = None,
    ) -> None:
        """命令 probe 在 point 处建造 structure_type 建筑。

        cache_key（远程代理建造/偷矿用）：同一张卡每帧重发时复用第一次
        find_placement 的落点，避免落点抖动。FakeFacade / _SharpyFacadeBase 实现已支持。
        """
        ...

    # ---- 偷矿 -----------------------------------------------------------

    def nearest_expansion(self, point: tuple[float, float]) -> tuple[float, float] | None:
        """返回离 point 最近的 expansion location（有矿的开矿点）坐标。

        偷矿 Nexus 是采矿基地，落点必须吸附到合法 expansion（有矿 + 可放 Nexus）；
        直接用玩家点原始坐标会落在无矿/不可建处，建造被 SC2 拒（orders_after=[]）。
        无 expansion 数据时返回 None（调用方退回原 point）。
        """
        ...

    # ---- 写：偷矿 FENCE -----------------------------------------------

    def register_stealth_townhalls(self, tags: set[int]) -> None:
        """偷矿 FENCE：整体覆盖 stealth_townhall_tags 集合（Manager 每 tick 传全集）。

        DistributeWorkers.generate_worker_queue 读此集合排除 stealth 基地，
        防主矿农民倒灌进 stealth Nexus 的空缺。
        Expand.execute 同样读此集合，不将 stealth 基地计入自然扩张账。
        """
        ...

    def ensure_units_reserved(self, tags: set[int]) -> None:
        """偷矿农民防外流：把这些 tag 并入 _llm_controlled_tags，保证每帧被 re-Reserve。

        Manager 每 tick 对本 cell 全部农民调用。即便某农民因瞬时 cache miss / 其它路径
        掉出 _llm_controlled_tags（不再被 _refresh_llm_controlled_roles 保护 → 被
        DistributeWorkers 当空闲工人拉回主矿），下一帧又被并回 → 不外流。
        """
        ...

    def register_stealth_workers(self, tags: set[int]) -> None:
        """整体覆盖 stealth_worker_tags 集合（所有 cell 农民并集，Manager 每帧注册）。

        ScoutWorker 等"挑农民干别的活"的逻辑排除它 —— 比 _llm_controlled_tags 更稳，
        不受瞬时 cache miss 把农民从 _llm_controlled_tags 误删那一帧的 race 影响。
        """
        ...

    def register_stealth_pending(self, count: int) -> None:
        """注册"在建/待建偷矿基地数"到 SNS（2026-06-12 用户）。

        Expand.execute 把它加进 active_bases → 玩家下了偷矿令但偷矿基地还没建好时，bot 也
        当它是一片基地、延后开自己对应的分矿（不开冗余分矿）。MINING（Nexus ready）的 cell
        已被 our_zones_with_minerals 计入，这里只数还没 ready 的，不重复计数。
        """
        ...

    def cast_chrono_on_nexus(self, nexus_tag: int) -> bool:
        """偷矿基地成长期自我星空加速：指定 Nexus 用自己的能量给自己加速产农民。

        前提：energy ≥ 50（chrono 消耗 50）+ 正在产农民（有 order）+ 未被 chrono。
        返回 True = 成功 cast。满采后 Manager 停止调用 → 能量留给 bot 公共 chrono 池
        （ChronoUnit/ChronoTech 给家里科技建筑用）。
        """
        ...

    def set_stealth_chrono_reserved(self, tags: set[int]) -> None:
        """整体覆盖"星空加速预留"Nexus 集合（成长期偷矿 Nexus）。

        bot 的 ChronoUnit 不拿这些 Nexus 当能量源 → 能量留给偷矿基地自我加速。
        满采后 Manager 把该 Nexus 移出 → 能量释放回 bot 公共 chrono 池。
        """
        ...

    def train_probe_at(self, nexus_tag: int) -> bool:
        """在指定 Nexus 训练一个农民（偷矿本地产线）。

        前提：Nexus ready + 空闲（orders 为空）+ can_afford(PROBE)。
        返回 True = 成功下令；False = 条件不满足（不抛异常）。
        """
        ...

    def order_worker_gather(self, worker_tag: int, near_point: tuple[float, float]) -> None:
        """命令 worker 采 near_point 附近最近的矿（偷矿本地产线：新认领农民就地采矿）。

        真机实现：找 near_point 附近最近的 mineral field → worker.gather(mineral)。
        Reserved 农民不会被 DistributeWorkers 自动派矿，必须显式下令。
        """
        ...

    def find_stealth_geysers(
        self, point: tuple[float, float], radius: float
    ) -> list[tuple[int, tuple[float, float]]]:
        """返回 point 半径内、**还没建 assimilator** 的 geyser 的 (tag, position) 列表（WP4b）。

        真机：vespene_geyser.closer_than(radius, Point2(point))，
              过滤掉 gas_buildings.closer_than(1.0, g.position) 非空的（已建/建中）。
        FakeFacade：返回 stealth_geysers_stub（默认 []）。
        """
        ...

    def order_probe_build_gas(self, probe_tag: int, geyser_tag: int) -> None:
        """命令 probe 在 geyser 上建 assimilator（WP4b）。

        真机：unit_cache.by_tag(probe_tag) → worker；
              vespene_geyser.find_by_tag(geyser_tag) → geyser；
              worker.build(UnitTypeId.ASSIMILATOR, geyser)。
        FakeFacade：记录调用至 gas_build_orders。
        """
        ...

    def find_stealth_gas_buildings(
        self, point: tuple[float, float], radius: float
    ) -> list[tuple[int, int, int]]:
        """返回 point 半径内 ready assimilator 的 (tag, assigned_harvesters, ideal_harvesters) 列表（WP4b）。

        真机：gas_buildings.ready.closer_than(radius, Point2(point))。
        FakeFacade：返回 stealth_gas_buildings_stub（默认 []）。
        """
        ...

    def order_worker_gather_gas(self, worker_tag: int, gas_building_tag: int) -> None:
        """命令 worker 采指定 assimilator 的气（WP4b）。

        真机：unit_cache.by_tag(worker_tag) → worker；
              unit_cache.by_tag(gas_building_tag) → gas；
              worker.gather(gas)。
        FakeFacade：记录调用至 gas_gather_orders。
        """
        ...

    def gas_worker_drifted(self, worker_tag: int, gas_tags: set[int]) -> bool:
        """采气农民是否"漂走了"（被登记为采气、实际没在采气循环里）——需重新焊回气上。

        2026-06-12 定位：order_worker_gather_gas 偶尔不生效（农民正钻在 assim/mid-cycle、
        cache-miss → 令丢弃），但 cell 乐观地把它加进 gas_worker_tags → 它继续采矿、矿口超采。
        判定（True=漂走需重派）：
          - 不在 cache（钻进 assim 暂时消失）→ False（正常，别动）
          - is_carrying_vespene（拎气回基地）→ False（采气循环中）
          - order target 在 gas_tags（正在采该气矿）→ False（在采气）
          - 否则（采矿 / idle / 采别的）→ True（漂走，重新焊）
        真机查 worker.orders / is_carrying_vespene；FakeFacade 返回 stub（默认 False）。
        """
        ...

    # ---- 读：游戏状态 -------------------------------------------------

    def get_state(self) -> BotState: ...

    def resolve_selector(
        self,
        unit_type: str | None = None,
        tag: int | None = None,
        tags: list[int] | None = None,
    ) -> list[int]:
        """解析 Selector 为 tag 列表。"""
        ...

    def all_own_unit_tags(self, include_workers: bool = True) -> list[int]:
        """返回所有己方单位的 tag 列表（**不含建筑**）。

        include_workers=False 时排除采矿工人（Probe/SCV/Drone）。
        _SharpyFacadeBase：遍历 self.bot.units（不含 structures）；
          include_workers=False 时按 type_id 过滤三族农民。
        FakeFacade：返回 _own_unit_tags 注入列表；
          include_workers=False 时排除 _worker_tags 集合中的 tag。
        """
        ...

    def filter_tags_in_box(
        self,
        tags: list[int],
        cx: float,
        cy: float,
        half_w: float,
        half_h: float,
    ) -> list[int]:
        """返回 tags 中位于 (cx±half_w, cy±half_h) 矩形框内的 tag 列表（保持入参顺序）。

        找不到坐标的 tag 直接跳过（不报错）。
        _SharpyFacadeBase：从 bot.units + bot.structures 取 position 做盒过滤。
        FakeFacade：从 _tag_positions 注入坐标表做同样过滤，供单测断言。
        """
        ...

    def cast_unit_ability(
        self,
        unit_tag: int,
        ability_id: str,
        target: dict[str, object] | None = None,
    ) -> None:
        """对指定 tag 的单位/建筑下 ability（如 SALVAGEBUNKER_SALVAGE）。

        _SharpyFacadeBase：先在 structures 查，再在 units 查；
          ability_id 字符串转 AbilityId 枚举；无 target → do(unit(ab))；
          有 target → 解析 Point2 后 do(unit(ab, pt))；
          找不到 unit / 非法 ability → log warning return（静默吞错但记日志）。
        FakeFacade：记录 (unit_tag, ability_id, target) 到 casts 列表，供单测断言。
        """
        ...

    def get_unit_type_name(self, unit_tag: int) -> str | None:
        """返回 unit_tag 对应单位/建筑的 type_id 名称（全大写，如 "BUNKER"）。

        找不到（tag 不在 cache / structures）→ 返回 None（不抛异常）。
        _SharpyFacadeBase：先 structures.find_by_tag，再 units.find_by_tag；
          返回 str(unit.type_id.name)（全大写）。
        FakeFacade：从可注入的 _tag_types: dict[int, str] 返回；找不到 → None。
        """
        ...

    def bunker_has_cargo(self, unit_tag: int) -> bool:
        """检查地堡（Bunker）是否有货舱乘员（has_cargo）。

        找不到 tag → False（不抛异常）。
        _SharpyFacadeBase：structures.find_by_tag(tag) → bool(u.has_cargo)；找不到 → False。
        FakeFacade：从可注入的 _tag_cargo: dict[int, bool] 查；找不到 → False。
        """
        ...

    def load_bunker(self, bunker_tag: int, count: int) -> int:
        """找 count 个最近的、不在地堡里的己方 Marine，每个发 SMART(bunker) 进入地堡。

        返回实际下令的 Marine 数（可能 < count，例如 Marine 不够）。
        _SharpyFacadeBase：bot.units(MARINE) 过滤不在 passengers 的，按距离升序取前 count 个，
          各发 UnitCommand(SMART, marine, bunker) 走 _vibecraft_bypass_actions 路径。
        FakeFacade：记录 (bunker_tag, count) 到 load_bunker_calls；返回 min(count, 4)（满载上限）。
        """
        ...

    def get_unit_health_percentage(self, unit_tag: int) -> float | None:
        """返回 unit_tag 对应单位/建筑的血量百分比（0.0–1.0）。

        找不到（tag 不在 cache）→ 返回 None（不抛异常）。
        _SharpyFacadeBase：先 structures.find_by_tag，再 units.find_by_tag；
          返回 float(unit.health_percentage)。
        FakeFacade：从可注入的 _tag_health: dict[int, float] 返回；找不到 → None。
        """
        ...

    def ensure_repair(self, target_tag: int, count: int) -> int:
        """确保 count 个 SCV 在修 target_tag 单位/建筑，返回实际派出数。

        满血（health_percentage >= 0.99）或找不到目标 → 返回 0 不派。
        _SharpyFacadeBase：找目标单位；满血/不存在 → 返回 0；找最近 count 个没在修它的
          己方 SCV，每个 `do(scv.repair(target_unit))`，返回在修数（可能 < count）。
        FakeFacade：记录 (target_tag, count) 到 ensure_repair_calls；如果 _tag_health 中
          target_tag >= 0.99 或不存在 → 返回 0，否则返回 count。
        """
        ...


# =========================================================================
# FakeFacade：单测专用，记录所有调用
# =========================================================================


@dataclass
class FacadeCall:
    method: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class FakeFacade:
    """In-memory fake：所有写操作记到 `calls`；读操作可注入 stub state。"""

    def __init__(self, state: BotState | None = None) -> None:
        self.state = state or BotState()
        self.unit_roles: dict[int, UnitRole] = {}
        self.builds: list[str] = []
        self.engagement_stances: list[str | None] = []
        self.camera_moves: list[tuple[float, float]] = []
        self.camera_follows: list[int] = []
        self.camera_zooms: list[float] = []
        self.camera_center_stub: tuple[float, float] | None = None
        self.production_overrides: list[tuple[str, int, int | None]] = []
        self.tech_overrides: list[tuple[str, int | None]] = []
        self.expansion_overrides: list[int | None] = []
        self.build_location_overrides: list[tuple[str, tuple[float, float]]] = []
        self.unit_actions: list[dict[str, object]] = []
        self.selector_lookups: list[dict[str, object]] = []
        self.attack_target_overrides: list[tuple[float, float] | None] = []
        self.combat_intent_overrides: list[str | None] = []
        self.attack_mode_overrides: list[str | None] = []
        self.sustain_uncap_calls: list[bool] = []
        # 2026-05-28 hold + 聚团 timer:测试断言用
        self.hold_gather_points: list[tuple[float, float] | None] = []
        self.rally_points: list[tuple[float, float] | None] = []
        self.regroup_started_calls: list[float | None] = []
        self.chrono_boost_casts: list[tuple[str, int]] = []
        # 2026-05-30 cast_ability_on_units 记录列表
        self.ability_casts: list[tuple[str, str | None, str, int | None]] = []
        # 2026-05-30 产能封锁
        self.production_blocked: set[str] = set()
        self.block_production_calls: list[str] = []
        self.unblock_production_calls: list[str] = []
        self.phoenix_harass_active_calls: list[bool] = []
        # 2026-06-01 Task E：代理建造
        self.unit_positions: dict[int, tuple[float, float]] = {}
        self.proxy_build_orders: list[dict[str, object]] = []
        self._proxy_place_cache: dict[object, tuple[float, float]] = {}
        # 2026-06-07 玩家折跃"在X刷N兵"
        self.warp_requests: list[dict[str, object]] = []
        self.warp_cancels: list[str] = []
        self._warp_done_stub: set[str] = set()  # 测试注入"已折满"的 key → warp_status 返 done
        self._warp_pending_keys: set[str] = set()  # request_warp 登记过的 key
        self.calls: list[FacadeCall] = []
        # selector 解析 stub：按 unit_type 给定 tag 列表
        self.selector_stub: dict[str, list[int]] = {}
        # 2026-06-04 WP-A：游戏内 debug draw
        self.debug_marks: list[dict[str, object]] = []
        # 2026-06-10 WP3 偷矿 FENCE
        self.stealth_townhall_tags: set[int] = set()
        self.train_probe_calls: list[int] = []
        self.train_probe_at_result: bool = True  # 测试可覆盖（模拟资源不足 → False）
        # 2026-06-10 偷矿落点吸附 stub（None=回显原 point；设了则 nearest_expansion 返回它）
        self.expansion_snap: tuple[float, float] | None = None
        # 2026-06-10 WP4 偷矿采矿令
        self.worker_gather_orders: list[tuple[int, tuple[float, float]]] = []
        # 2026-06-10 WP5 偷矿受击释放
        self.release_unit_role_calls: list[int] = []
        # 2026-06-11 WP4b 偷气
        self.stealth_geysers_stub: list[tuple[int, tuple[float, float]]] = []
        self.stealth_gas_buildings_stub: list[tuple[int, int, int]] = []
        self.gas_build_orders: list[tuple[int, int]] = []  # (probe_tag, geyser_tag)
        self.gas_gather_orders: list[tuple[int, int]] = []  # (worker_tag, gas_building_tag)
        # 2026-06-19 Step 1（镜头框选 + 建筑 ability）
        # filter_tags_in_box：可注入 {tag: (x,y)} 坐标表供单测
        self._tag_positions: dict[int, tuple[float, float]] = {}
        # cast_unit_ability：记录调用 (unit_tag, ability_id, target)
        self.casts: list[tuple[int, str, dict[str, object] | None]] = []
        # get_unit_type_name：可注入 {tag: type_name_str} 映射供单测
        self._tag_types: dict[int, str] = {}
        # 2026-06-19 Step 2（near_camera selector 固化）
        # all_own_unit_tags：注入己方单位 tag 列表 + worker tag 集合
        self._own_unit_tags: list[int] = []
        self._worker_tags: set[int] = set()
        # 2026-06-19 地堡货舱控制
        # bunker_has_cargo：可注入 {tag: bool} 表供单测
        self._tag_cargo: dict[int, bool] = {}
        # load_bunker：记录 (bunker_tag, count) 供单测断言
        self.load_bunker_calls: list[tuple[int, int]] = []
        # 2026-06-19 通用维修
        # get_unit_health_percentage：可注入 {tag: health_pct} 表供单测
        self._tag_health: dict[int, float] = {}
        # ensure_repair：记录 (target_tag, count) 供单测断言
        self.ensure_repair_calls: list[tuple[int, int]] = []
        # 2026-07-06 采矿策略：记录 set_mining_priority 调用值序列
        self.mining_priority_calls: list[str | None] = []
        # 2026-07-07 攻防升级目标等级：记录 (family, level) 调用序列，供单测断言
        self.upgrade_target_calls: list[tuple[str, int | None]] = []
        # upgrade_targets 字典副本（供单测读取当前状态）
        self.upgrade_targets: dict[str, int | None] = {}

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append(FacadeCall(method=method, args=args, kwargs=kwargs))

    # ---- 写 -----------------------------------------------------------

    def set_build(self, build_name: str) -> None:
        self.builds.append(build_name)
        self._record("set_build", build_name)

    def set_production_override(
        self,
        unit_type: str,
        count: int,
        building_tag: int | None = None,
    ) -> None:
        self.production_overrides.append((unit_type, count, building_tag))
        self._record("set_production_override", unit_type, count, building_tag=building_tag)

    def set_tech_override(self, upgrade_id: str, building_tag: int | None = None) -> None:
        self.tech_overrides.append((upgrade_id, building_tag))
        self._record("set_tech_override", upgrade_id, building_tag=building_tag)

    def set_expansion_override(self, target_count: int | None) -> None:
        self.expansion_overrides.append(target_count)
        self._record("set_expansion_override", target_count)

    def set_unit_role(self, unit_tag: int, role: UnitRole) -> None:
        self.unit_roles[unit_tag] = role
        self._record("set_unit_role", unit_tag, role)

    def release_unit_role(self, unit_tag: int) -> None:
        """LLM_CONTROLLED 让位的反向操作：从 unit_roles 移除，还给 sharpy。"""
        self.unit_roles.pop(unit_tag, None)
        self.release_unit_role_calls.append(unit_tag)
        self._record("release_unit_role", unit_tag)

    def execute_unit_action(
        self,
        unit_tag: int,
        verb: str,
        target: dict[str, object] | None = None,
        ability_id: str | None = None,
    ) -> None:
        self.unit_actions.append(
            {"tag": unit_tag, "verb": verb, "target": target, "ability_id": ability_id}
        )
        self._record("execute_unit_action", unit_tag, verb, target=target, ability_id=ability_id)

    def set_build_location_override(
        self,
        structure_type: str,
        point: tuple[float, float],
    ) -> None:
        self.build_location_overrides.append((structure_type, point))
        self._record("set_build_location_override", structure_type, point)

    def set_engagement_stance(self, stance: str | None) -> None:
        self.engagement_stances.append(stance)
        self._record("set_engagement_stance", stance)

    def set_attack_target_override(self, point: tuple[float, float] | None) -> None:
        self.attack_target_overrides.append(point)
        self._record("set_attack_target_override", point)

    def set_combat_intent_override(self, intent: str | None) -> None:
        self.combat_intent_overrides.append(intent)
        self._record("set_combat_intent_override", intent)

    def set_attack_mode_override(self, mode: str | None) -> None:
        self.attack_mode_overrides.append(mode)
        self._record("set_attack_mode_override", mode)

    def set_sustain_uncap_active(self, active: bool) -> None:
        self.sustain_uncap_calls.append(active)
        self._record("set_sustain_uncap_active", active)

    def set_mining_priority(self, priority: str | None) -> None:
        """2026-07-06 采矿策略：记录调用序列供单测断言。"""
        self.mining_priority_calls.append(priority)
        self._record("set_mining_priority", priority)

    def set_upgrade_target(self, family: str, level: int | None) -> None:
        """2026-07-07 攻防升级目标等级：记录 (family, level) 调用序列供单测断言。"""
        self.upgrade_target_calls.append((family, level))
        if level is None:
            self.upgrade_targets.pop(family, None)
        else:
            self.upgrade_targets[family] = level
        self._record("set_upgrade_target", family, level)

    def set_hold_gather_point(self, point: tuple[float, float] | None) -> None:
        self.hold_gather_points.append(point)
        self._record("set_hold_gather_point", point)

    def set_rally_point(self, point: tuple[float, float] | None) -> None:
        self.rally_points.append(point)
        self._record("set_rally_point", point)

    def set_regroup_started(self, ts: float | None) -> None:
        self.regroup_started_calls.append(ts)
        self._record("set_regroup_started", ts)

    def cast_chrono_boost_on_structure(
        self,
        structure_type: str,
        count: int = 1,
    ) -> int:
        self.chrono_boost_casts.append((structure_type, count))
        self._record("cast_chrono_boost_on_structure", structure_type, count)
        return count  # mock 假装全部成功

    def cast_ability_on_units(
        self,
        ability_id: str,
        unit_type: str | None = None,
        target_kind: str = "self",
        count: int | None = None,
        target_point: tuple[float, float] | None = None,
    ) -> int:
        self.ability_casts.append((ability_id, unit_type, target_kind, count, target_point))
        self._record(
            "cast_ability_on_units",
            ability_id,
            unit_type=unit_type,
            target_kind=target_kind,
            count=count,
            target_point=target_point,
        )
        # mock: MORPH_ARCHON 返回配对数(每 2 个 HT 合 1 个), 其他返回 count or 1
        if ability_id.upper() == "MORPH_ARCHON":
            return count if count is not None else 1
        return count if count is not None else 1

    def block_production(self, unit_type: str) -> None:
        """产能封锁：加入 production_blocked set。"""
        self.production_blocked.add(unit_type)
        self.block_production_calls.append(unit_type)
        self._record("block_production", unit_type)

    def unblock_production(self, unit_type: str) -> None:
        """产能封锁解除：从 production_blocked set 移除。"""
        self.production_blocked.discard(unit_type)
        self.unblock_production_calls.append(unit_type)
        self._record("unblock_production", unit_type)

    def set_phoenix_harass_active(self, active: bool) -> None:
        """凤凰骚扰持久指令卡：记录 active 切换。"""
        self.phoenix_harass_active_calls.append(active)
        self._record("set_phoenix_harass_active", active)

    def set_debug_marks(self, marks: list[dict[str, object]]) -> None:
        """记录本帧 debug mark 清单（覆盖写）。"""
        self.debug_marks = list(marks)
        self._record("set_debug_marks", marks)

    def draw_debug_marks(self) -> None:
        """FakeFacade 无 SC2 client，no-op。"""

    def move_camera(self, point: tuple[float, float]) -> None:
        self.camera_moves.append(point)
        self._record("move_camera", point)

    def follow_unit(self, unit_tag: int) -> None:
        self.camera_follows.append(unit_tag)
        self._record("follow_unit", unit_tag)

    def set_camera_zoom(self, level: float) -> None:
        self.camera_zooms.append(level)
        self._record("set_camera_zoom", level)

    def get_camera_center(self) -> tuple[float, float] | None:
        return self.camera_center_stub

    def get_unit_position(self, tag: int) -> tuple[float, float] | None:
        """返回 stub 注入的单位坐标；不存在返回 None。"""
        return self.unit_positions.get(tag)

    def order_probe_build(
        self,
        probe_tag: int,
        structure_type: str,
        point: tuple[float, float],
        cache_key: object = None,
    ) -> None:
        self.proxy_build_orders.append(
            {
                "probe": probe_tag,
                "structure": structure_type,
                "point": point,
                "cache_key": cache_key,
            }
        )
        self._record("order_probe_build", probe_tag, structure_type, point)

    def nearest_expansion(self, point: tuple[float, float]) -> tuple[float, float] | None:
        """偷矿落点吸附（fake）：默认回显 point；设了 expansion_snap 则返回它。"""
        self._record("nearest_expansion", point)
        return self.expansion_snap if self.expansion_snap is not None else point

    def register_stealth_townhalls(self, tags: set[int]) -> None:
        """偷矿 FENCE：整体覆盖写入 stealth_townhall_tags（Manager 每 tick 传全集）。"""
        self.stealth_townhall_tags = set(tags)
        self._record("register_stealth_townhalls", tags)

    def ensure_units_reserved(self, tags: set[int]) -> None:
        """记录 ensure_reserved 调用（fake；真机并入 _llm_controlled_tags）。"""
        self.reserved_ensured: set[int] = getattr(self, "reserved_ensured", set())
        self.reserved_ensured |= set(tags)
        self._record("ensure_units_reserved", tags)

    def register_stealth_workers(self, tags: set[int]) -> None:
        """记录 stealth_worker_tags 注册（fake）。"""
        self.stealth_workers_registered: set[int] = set(tags)
        self._record("register_stealth_workers", tags)

    def register_stealth_pending(self, count: int) -> None:
        """记录"在建偷矿基地数"注册（fake）。"""
        self.stealth_pending_registered: int = int(count)
        self._record("register_stealth_pending", count)

    def cast_chrono_on_nexus(self, nexus_tag: int) -> bool:
        """记录自我 chrono 调用（fake；返回可控值，默认 True）。"""
        self.chrono_nexus_calls: list[int] = getattr(self, "chrono_nexus_calls", [])
        self.chrono_nexus_calls.append(nexus_tag)
        self._record("cast_chrono_on_nexus", nexus_tag)
        return getattr(self, "chrono_on_nexus_result", True)

    def set_stealth_chrono_reserved(self, tags: set[int]) -> None:
        """记录星空加速预留集合（fake）。"""
        self.chrono_reserved: set[int] = set(tags)
        self._record("set_stealth_chrono_reserved", tags)

    def train_probe_at(self, nexus_tag: int) -> bool:
        """偷矿本地产线：记录调用，返回可控值（默认 True）。"""
        self.train_probe_calls.append(nexus_tag)
        self._record("train_probe_at", nexus_tag)
        return self.train_probe_at_result

    def order_worker_gather(self, worker_tag: int, near_point: tuple[float, float]) -> None:
        """偷矿采矿令：记录调用（fake 实现，不真正派令）。"""
        self.worker_gather_orders.append((worker_tag, near_point))
        self._record("order_worker_gather", worker_tag, near_point)

    def find_stealth_geysers(
        self, point: tuple[float, float], radius: float
    ) -> list[tuple[int, tuple[float, float]]]:
        """WP4b：返回 stealth_geysers_stub（可注入测试数据，默认 []）。"""
        self._record("find_stealth_geysers", point, radius)
        return list(self.stealth_geysers_stub)

    def order_probe_build_gas(self, probe_tag: int, geyser_tag: int) -> None:
        """WP4b：记录建 assimilator 令 (probe_tag, geyser_tag)。"""
        self.gas_build_orders.append((probe_tag, geyser_tag))
        self._record("order_probe_build_gas", probe_tag, geyser_tag)

    def find_stealth_gas_buildings(
        self, point: tuple[float, float], radius: float
    ) -> list[tuple[int, int, int]]:
        """WP4b：返回 stealth_gas_buildings_stub（可注入测试数据，默认 []）。"""
        self._record("find_stealth_gas_buildings", point, radius)
        return list(self.stealth_gas_buildings_stub)

    def order_worker_gather_gas(self, worker_tag: int, gas_building_tag: int) -> None:
        """WP4b：记录采气令 (worker_tag, gas_building_tag)。"""
        self.gas_gather_orders.append((worker_tag, gas_building_tag))
        self._record("order_worker_gather_gas", worker_tag, gas_building_tag)

    def gas_worker_drifted(self, worker_tag: int, gas_tags: set[int]) -> bool:
        """单测：返回 worker_tag 是否在 gas_drifted_stub（默认空 → 都没漂）。"""
        self._record("gas_worker_drifted", worker_tag, gas_tags)
        return worker_tag in getattr(self, "gas_drifted_stub", set())

    def filter_tags_in_box(
        self,
        tags: list[int],
        cx: float,
        cy: float,
        half_w: float,
        half_h: float,
    ) -> list[int]:
        """_tag_positions 注入坐标表做盒过滤；表里没有的 tag 跳过。"""
        result = []
        for t in tags:
            pos = self._tag_positions.get(t)
            if pos is None:
                continue
            if abs(pos[0] - cx) <= half_w and abs(pos[1] - cy) <= half_h:
                result.append(t)
        self._record("filter_tags_in_box", tags, cx, cy, half_w, half_h)
        return result

    def cast_unit_ability(
        self,
        unit_tag: int,
        ability_id: str,
        target: dict[str, object] | None = None,
    ) -> None:
        """记录调用到 casts 列表，供单测断言。"""
        self.casts.append((unit_tag, ability_id, target))
        self._record("cast_unit_ability", unit_tag, ability_id, target=target)

    def get_unit_type_name(self, unit_tag: int) -> str | None:
        """从 _tag_types 注入映射返回类型名；找不到 → None。"""
        return self._tag_types.get(unit_tag)

    def bunker_has_cargo(self, unit_tag: int) -> bool:
        """从 _tag_cargo 注入映射返回 has_cargo；找不到 → False。"""
        return self._tag_cargo.get(unit_tag, False)

    def load_bunker(self, bunker_tag: int, count: int) -> int:
        """记录到 load_bunker_calls；返回 min(count, 4)（地堡满载上限）。"""
        self.load_bunker_calls.append((bunker_tag, count))
        self._record("load_bunker", bunker_tag, count)
        return min(count, 4)

    def get_unit_health_percentage(self, unit_tag: int) -> float | None:
        """从 _tag_health 注入映射返回血量百分比；找不到 → None。"""
        return self._tag_health.get(unit_tag)

    def ensure_repair(self, target_tag: int, count: int) -> int:
        """记录到 ensure_repair_calls；目标满血(>=0.99)/不存在 → 0，否则返回 count。"""
        self.ensure_repair_calls.append((target_tag, count))
        self._record("ensure_repair", target_tag, count)
        hp = self._tag_health.get(target_tag)
        if hp is None or hp >= 0.99:
            return 0
        return count

    def request_warp(
        self, key: str, unit_type: str, count: int, target: tuple[float, float]
    ) -> None:
        if key in self._warp_pending_keys or key in self._warp_done_stub:
            return  # 幂等
        self._warp_pending_keys.add(key)
        self.warp_requests.append(
            {"key": key, "unit_type": unit_type, "count": count, "target": target}
        )
        self._record("request_warp", key, unit_type, count, target)

    def cancel_warp(self, key: str) -> None:
        self.warp_cancels.append(key)
        self._warp_pending_keys.discard(key)
        self._warp_done_stub.discard(key)

    def warp_status(self, key: str) -> str:
        if key in self._warp_done_stub:
            return "done"
        if key in self._warp_pending_keys:
            return "producing"
        return "none"

    # ---- 读 -----------------------------------------------------------

    def get_state(self) -> BotState:
        return self.state

    def resolve_selector(
        self,
        unit_type: str | None = None,
        tag: int | None = None,
        tags: list[int] | None = None,
    ) -> list[int]:
        self.selector_lookups.append({"unit_type": unit_type, "tag": tag, "tags": tags})
        if tag is not None:
            return [tag]
        if tags:
            return list(tags)
        if unit_type is not None and unit_type in self.selector_stub:
            return list(self.selector_stub[unit_type])
        return []

    def all_own_unit_tags(self, include_workers: bool = True) -> list[int]:
        """返回 _own_unit_tags 注入列表；include_workers=False 时排除 _worker_tags 中的 tag。"""
        tags = list(self._own_unit_tags)
        if not include_workers:
            tags = [t for t in tags if t not in self._worker_tags]
        self._record("all_own_unit_tags", include_workers)
        return tags
