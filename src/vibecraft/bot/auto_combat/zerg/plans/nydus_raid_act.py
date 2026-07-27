"""坑道虫突袭投送执行器 —— NydusRaidAct（2026-07-09 精修第一轮）。

把 army（狗/蟑/女王）真正"灌"过坑道网络钻到敌方家，替代 `nydus.py` 原来
"坑道虫建出来没人用、army 走地图正面攻过去"的 gap。

设计权威：`docs/plans/2026-07-09-nydus-raid-polish-design.md`（opus 评审 + venv 真机
核对，评审处置 9 条 must-fix 全照做）。

三态状态机（照 `bc_raid_act.py` GroupHarassAct 的 STAGE/DIVE/HEAL 单状态机范式，
每个单位恰好一个状态，绝不用群体后验推断）：

  STAGE   —— 在自家坑道网络（NydusNetwork）旁 Reserve 集结待命，不走正面。
              坑道虫(NydusCanal) ready + 待命 army supply ≥ 阈值 → 首波整批
              一起装载（不 trickle）；之后新孵出的狗/蟑到齐即随到随灌（增援）。
  TRANSIT —— 已发 SMART 装进坑道网络，读 `network.passengers_tags` /
              `canal.passengers_tags` 判定"确实在坑道里"；坑道虫处持续（节流）
              探测 UNLOAD ability 并下令排空，把乘客倒出敌方家。
  STRIKE  —— 钻出后优先扑最近敌方农民，其次拆最近建筑，都没有则顶向敌方主基地
              （不追远处散兵）。

装载/卸载走 `common_bot.py::load_bunker` 同款 bypass 范式（`_vibecraft_bypass_actions`，
绕开 python-sc2 `prevent_double_actions` 的 orders==[] 静默丢单 bug），直接在本 act
里对 `self.ai._vibecraft_bypass_actions` 追加 `UnitCommand`，由 `common_bot.on_step`
在 `super().on_step()` 之后统一 drain + 记 `ActionResult`。不新增 facade 方法
（评审处置 #2：倾向 act 内直接用 ability + bypass）。

UNLOAD ability **运行时探真名**（评审处置 #1，最大风险点）：
  - NydusCanal 侧 = `UNLOADALL_NYDUSWORM`（已核对，拼写正常）。
  - NydusNetwork 侧 = `UNLOADALL_NYDASNETWORK`（**注意 NYDAS 不是 NYDUS**，SC2 真实
    枚举拼写坑）。
  两者都不硬编码 —— 有 passenger 那一刻 `get_available_abilities(unit)`，取名字含
  "UNLOAD" 的那个真实枚举成员，探到后缓存复用（避免每次重新 await）。

Reserve 独占范式（照 `marine_staging_act.py` / `bc_raid_act.py::_reserve`）：
  - STAGE/STRIKE 单位每帧 `roles.set_task(Reserved, u)`；sharpy `PlanZoneGather` /
    `PlanZoneAttack` 只拉 `free_units`（不含 Reserved），天然不干扰，无需 vendor hook。
  - 招募封顶（评审处置 #4，"macro-tail 的 roach30/queen 绝不 reserve"）：
    `roach_cap`/`zergling_cap`/`queen_cap` 对应 opening 波目标数（ROACH 8 / ZERGLING 16 /
    QUEEN 4，macro tail 追加的 ROACH 30 / QUEEN 5 / DRONE 70 不会被本 act 招募，留给
    默认 PlanZoneAttack/宏观运营）。
  - 硬释放兜底 `release_after_s`（默认 900s）：到点整体 `_release_all`，STAGE/STRIKE
    单位显式 `clear_task` 还给 sharpy（不是只清内部字典，CLAUDE.md 控制权模型规则 3）。
  - 玩家单位级 claim（`ai._llm_controlled_tags`）优先于本 act（控制权模型规则 1）：
    命中的 tag 立即让出管理权，不再招募回来。

三条兜底（评审处置 #6）：
  ① OL/视野 —— 复用现有 `_SendOverlordToEnemy`（本文件不重复，`nydus.py` 里已并行跑）。
  ② army 卡坑道网络（虫洞全灭/久不 ready）—— TRANSIT 单位超时 `_TRANSIT_STUCK_TIMEOUT_S`
     仍无 ready canal 可卸 → 对 NydusNetwork 侧探测 UNLOAD ability 卸回家，释放该批 tag
     （走正面，不烂在坑道里）。
  ③ 虫洞被秒 —— 交给 `nydus.py::_BuildNydusCanalAtEnemy`（落点缓存清空重下），本 act
     只管"有 canal 就用、没 canal 就等/超时兜底"。

已知局限（round 1，真局自验两轮实测记录，留给后续迭代）：
  - `release_after_s` 硬释放那一刻若恰好有单位在 TRANSIT 中（已装载未卸出），这批 tag
    的 STRIKE 转换追踪会被放弃（仍会被 canal 侧持续 unload 逻辑吐出到世界里，但吐出后
    不会自动获得攻击指令，等 sharpy 默认路径接管）——影响面小（release_after_s 默认
    15 分钟，且多数单位早已完成一轮 STRIKE），先记录，多轮记分卡若发现明显影响再补。
  - **prune-order 时序 bug 已修**（见 `docs/pitfalls.md` 2026-07-09 条）：`_prune_dead`
    曾排在乘客判定之前跑，把"这一帧刚装载成功"误判成"死了"，导致 transit 永远为 0；
    已改成 `_promote_stage_to_transit` 优先跑，真局验证前后对比（0 → 13）。

Round 2 精修（2026-07-09 telemetry 复盘 + 落点/视野/前门三处修复，`nydus.py` 侧）：
  - **round1 真根因不是"14s 钻出期被秒"，是视野丢失+死锁定点**：telemetry 复盘显示
    `_SendOverlordToEnemy` 只发一次的 Overlord 在 t≈145s 被打死后**全程零补位**，
    `_BuildNydusCanalAtEnemy` 锁定后从不复查 `is_visible`，导致后续 174 次
    `BUILD_NYDUSWORM` 全部对着一个已失去视野的死点静默空放（SC2 对不可见目标静默拒绝
    该指令），直到前门部队快获胜时顺路带来视野才侥幸成功一次——但仗已经打完了。
  - **修复**：① `_SendOverlordToEnemy` 改成持续保活（敌方附近没 OL/Overseer 就每
    6s 补一只，留 ≥1 只在家）；② `_BuildNydusCanalAtEnemy` 每次真下令前重新
    `is_visible` 复查，不可见就跳过 + 节流警告（不再无声空放）；③ 落点锚点从
    `zone.mineral_line_center`（矿点正中间，正对农民脸）换成
    `zone.behind_mineral_position_center`（矿点背面，sharpy `cannon_rush.py` 同款
    隐蔽点）+ 挑选时动态查附近敌方单位密度规避 + 被毁点拉黑；④ `_tick_stage` 移除
    `canal_ready` 门控，STAGE 阶段就预装进坑道网络（虫洞一 ready 立即排空，不用等
    army 现装）；⑤ `PlanZoneAttack(start_attack_power)` 从 10 提到 30，给坑道虫
    留出先手窗口（round1 前门经常在虫洞真正打通前就单挑赢了，坑道虫从没机会登场）。
  - **真局复验（vs VeryEasy，两轮confirmatory 跑）**：`worm position locked` 3-7 次
    （不再是 174 次死循环）、单次落点成功后再无被毁重下、`transit` confirmed
    28/32、`strike` 10-28 个不同 tag、首次投送 game_time ≈ 5:50-6:10（round1 是
    13:14，快结束才投）、`enemy_workers_harassed`（累计"打到过"的不同农民数，非
    视野瞬时计数）在前门参战前（~13-17min）已从 0 涨到 4-5，证明坑道虫本身真造成
    了经济伤害（不是前门补刀）。六维记分卡①②③④ 硬门全过，⑤⑥ 仍是 TODO
    （兵力效率精确核算 / 转型判定，留后续）。

Round 4「声东击西」战略重构（2026-07-09，用户拍板）：
  Round 2/3 对 VeryHard 仍撞墙——虫洞在敌方家 14s 钻出期被防守军队秒，0 存活。
  根因从"硬抗防守"改成"创造安全窗口"：新增 `feint_squad_act.py::FeintSquadAct`
  （小股速狗持续骚扰敌方二矿，引敌军主力离开主基地矿线）+ `nydus.py::
  _BuildNydusCanalAtEnemy` 的安全窗口检测（敌方主基地附近敌方战斗单位数低于阈值
  才下 BUILD_NYDUSWORM，落点也从矿点背面隐蔽点改回矿线正中——窗口期无人守，
  屠农民更直接）。本 act（NydusRaidAct）职责不变，仍只管"有 canal 就用"；招募
  小狗池子与 FeintSquadAct 分池不重叠（`ai._vibecraft_nydus_feint_tags` 排除，见
  `_recruit`）。详见 `docs/plans/2026-07-09-nydus-raid-polish-design.md`「Round 4」段。
"""

from __future__ import annotations

import contextlib
from typing import Any

from loguru import logger
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit_command import UnitCommand
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.zerg.creep import (
    CREEP_TUMOR_ENERGY,
    existing_tumors,
    pick_creep_tumor_spot,
)
from vibecraft.bot.unit_kind import is_army as _is_army
from vibecraft.bot.unit_kind import is_worker as _is_worker

# ── 招募 / 集结常量 ──────────────────────────────────────────────────────────
_STAGE_ARRIVE_DIST: float = 4.0  # 到集结锚点判定距离（同 marine_staging_act）
# 集结点从坑道网络往外让开这么多格(2026-07-27 用户:"集结的位置不用太靠近主基地")。
# 贴着网络站会堵在基地里、也不在防线上;让开一段既不挡路,装载时多走 2-3 秒也不影响。
_STAGE_OFFSET_FROM_NETWORK: float = 8.0
# 同一只单位重发"去集结点"的最小间隔。**不再每帧发** —— 每帧硬发等于把单位钉在锚点上,
# 家里挨打时它们被战斗推开一步就又被拽回去,参与不了防守(用户 2026-07-27 真机)。
_STAGE_REISSUE_COOLDOWN_S: float = 4.0
# 敌方战斗单位进到自家任一基地这么近 = 家里挨打
_HOME_THREAT_RADIUS: float = 18.0
_STAGE_SUPPLY_THRESHOLD: float = 10.0  # 首波攒够阈值（supply），类比原 start_attack_power=10
_MIN_ROACHES_WAVE1: int = 8  # 首波蟑螂门槛（2026-07-13 用户**收回"狗就发"**:第一波必须 8+ 蟑螂才有
# 威胁,否则打不痛。狗当陪同/续兵,蟑螂是主力）
_WAVE1_SUPPLY_FALLBACK: float = 26.0  # 首波兜底：只在总兵力已很大(supply≥26)才不等蟑螂发（防死锁；
# Fable5：原 26 在招募 cap 8 蟑+16 狗−6 佯攻 ≈ 16 supply 下数学上够不着、不是兜底；降到 20 可达）
_WAVE1_NETWORK_TIMEOUT_S: float = (
    60.0  # 网络就绪超时兜底：坑道网络 ready 后超过这么久还没凑够门槛，
)
# 只要有 ≥4 蟑螂或 ≥3 待命兵就发（防蟑螂产能被骚扰打断时首波死等、canal 永不落——Fable5 #4 防再死锁）
_KEEP_HOME_QUEENS: int = 2  # 留在家不投送的女王数（inject + 反 Banshee）
_ROACH_CAP: int = 12  # 招募封顶：对应 opening 波蟑螂目标(nydus.py ZergUnit ROACH,12 priority)
# 2026-07-11 Fable5：从 8 提到 12 对齐 build 产量，让攒出的蟑螂全加入一波，别卡家里闲置
_ZERGLING_CAP: int = 24  # 招募封顶：对应 opening 波小狗目标(ZergUnit ZERGLING,24；含佯攻队 6 池外)
_QUEEN_CAP: int = 4  # 招募封顶：对应 macro tail 前女王目标(ActUnit QUEEN,4)
_RELEASE_AFTER_S: float = 900.0  # 硬释放兜底（15min，all-in 该结束了）

# ── 装载 / 卸载节流常量 ──────────────────────────────────────────────────────
_LOAD_RETRY_COOLDOWN_S: float = 1.0  # 同一 tag 重发 SMART 装载的最小间隔
_UNLOAD_PROBE_COOLDOWN_S: float = 1.0  # canal 排空指令节流
_TRANSIT_STUCK_TIMEOUT_S: float = 100.0  # 装载中但久无 ready canal 可卸 → 网络侧兜底卸回家
# Round 2（2026-07-09）：从 45s 提到 100s——现在 STAGE 阶段就预装（见 _tick_stage 的
# canal_ready 门控移除），army 可能在虫洞真正建成前就已经在坑道网络里等；45s 对
# "Network 已就绪但 worm 还没打通"这种正常等待窗口太短，容易误触发兜底卸回家。

# ── STRIKE 常量 ──────────────────────────────────────────────────────────────
_STRIKE_TARGET_RADIUS: float = 20.0  # 拆建筑兜底搜索半径（不追远处散兵；农民另走质心逻辑见下）
_STRIKE_THREAT_RADIUS: float = 12.0  # 判"追农民会不会被反打"的敌军检测半径（2026-07-10）
_STRIKE_THREAT_COUNT: int = 3  # 附近敌方战斗单位 ≥ 此数 = 有威胁，别空追农民、先打敌军
_STRIKE_RETARGET_COOLDOWN_S: float = 1.5  # 重新选目标节流
_TRANSFUSE_RANGE: float = 7.0  # 女王 transfuse 施法射程（SC2 实测 7）
_TRANSFUSE_MIN_ENERGY: float = 50.0  # transfuse 耗能 50
# 女王在敌方家铺菌毯（2026-07-26 用户）：菌毯瘤耗能 25，只能种在菌毯上——坑道虫落地自带菌毯，
# 钻出来的女王脚下就是。往外沿种，把菌毯朝敌方矿线推。
_CREEP_TUMOR_COOLDOWN_S: float = 8.0  # 同一只女王两次种毯的最小间隔
_CREEP_TUMOR_MAX_R: int = 6  # 以女王为心往外找可种点的最大半径（菌毯外沿）
_CREEP_TUMOR_SPACING: float = 7.0  # 离已有菌毯瘤至少这么远，别挤一起浪费能量
_TRANSFUSE_COOLDOWN_S: float = 1.0  # 同一女王重发 transfuse 最小间隔（防每帧刷屏）
_WORKER_SEEK_RADIUS: float = 30.0  # 农民质心搜索半径：锚点=矿线中心（不是打击单位当前位置）
# Round 3 真机实测（2026-07-09）：`worm position locked` 在同一个 zone 内多次重下时
# 候选点横跨 behind_mineral_positions 不同锚点，相邻两次落点相差可达 ~19（如
# (58,40)→(60,28) y 差 12、(60,28)→(59,21) 再差 7），矿线本身+散落工人的实际分布比
# mineral_line_center 单点半径 18 覆盖不到；调宽到 30（仍是"矿区附近"量级，不是
# "满地图追"）。

# 各兵种 supply 权重（判定"攒够 N supply 才灌"用，够用即可，不查 game_data）
_SUPPLY_BY_TYPE: dict[UnitTypeId, float] = {
    UnitTypeId.ROACH: 2.0,
    UnitTypeId.ZERGLING: 0.5,
    UnitTypeId.QUEEN: 2.0,
}

_RAID_UNIT_TYPES: tuple[UnitTypeId, ...] = (UnitTypeId.ROACH, UnitTypeId.ZERGLING, UnitTypeId.QUEEN)


class NydusRaidAct(ActBase):  # type: ignore[misc]
    """坑道虫突袭投送状态机：STAGE(集结待装) → TRANSIT(坑道内) → STRIKE(钻出打击)。

    non-blocking：`execute()` 每帧独立跑，永远 return True（同 `GroupHarassAct` 范式），
    放 `nydus.py` 战术 `SequentialList` 里 `PlanZoneGather()` 之后、`PlanZoneAttack()`
    之前（跟 `bc_raid_act.py` docstring 建议的位置一致）。
    """

    def __init__(
        self,
        stage_supply_threshold: float = _STAGE_SUPPLY_THRESHOLD,
        min_roaches_wave1: int = _MIN_ROACHES_WAVE1,
        keep_home_queens: int = _KEEP_HOME_QUEENS,
        roach_cap: int = _ROACH_CAP,
        zergling_cap: int = _ZERGLING_CAP,
        queen_cap: int = _QUEEN_CAP,
        release_after_s: float = _RELEASE_AFTER_S,
    ) -> None:
        super().__init__()
        self.stage_supply_threshold = stage_supply_threshold
        self.min_roaches_wave1 = min_roaches_wave1
        self.keep_home_queens = keep_home_queens
        self.roach_cap = roach_cap
        self.zergling_cap = zergling_cap
        self.queen_cap = queen_cap
        self.release_after_s = release_after_s

        # per-unit 单状态机：tag -> "STAGE" / "TRANSIT" / "STRIKE"
        self._state: dict[int, str] = {}
        self._state_since: dict[int, float] = {}
        self._loading_since: dict[int, float] = {}  # tag -> 上次发 SMART 装载指令的时刻（节流）
        self._transit_since: dict[int, float] = {}  # tag -> 进入 TRANSIT 的时刻（超时兜底判定）
        self._strike_retarget_at: dict[int, float] = {}
        self._strike_last_kind: dict[int, str] = {}  # tag -> 上次目标类型（去重日志）

        # 招募封顶计数（历史累计，不因死亡回补 —— 意图是"opening 波"，不是"永久填坑"）
        # 每个已认领 tag 的兵种（TRANSIT 进坑道后从 army 里消失、拿不到 type_id，故认领时记下）。
        self._unit_type: dict[int, UnitTypeId] = {}
        self._creep_at: dict[int, float] = {}  # 每只女王上次种菌毯瘤的时刻（节流）
        self._stage_move_at: dict[int, float] = {}  # 每只集结单位上次被下"去集结点"的时刻(节流)
        self._last_defend_log: float = -999.0
        self._home_queen_tags: set[int] | None = None  # 留家女王名单（一次锁定）
        self._ever_released: set[int] = set()  # 曾被释放/玩家 claim 的 tag，不再招募

        self._stage_anchor: Point2 | None = None
        self._network_ready_since: float | None = None  # 坑道网络首次 ready 的时刻（首波超时兜底）
        self._first_wave_sent: bool = False
        self._pending_release_tags: set[int] = set()

        self._unload_canal_ability: AbilityId | None = None
        self._unload_network_ability: AbilityId | None = None
        self._last_unload_probe: float = -999.0

        self._released: bool = False

    # ------------------------------------------------------------------
    # ActBase entry point
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        try:
            await self._tick()
        except Exception:
            logger.exception("NydusRaidAct._tick error")
        return True

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        now = float(self.ai.time)
        army = self._army_units_by_tag()

        # 发布"首波军队已装载"给 _BuildNydusCanalAtEnemy：军队装进网络了才建虫子
        # （2026-07-10 用户：军队先钻进家里坑道网络等着，虫子在敌方家 morph 的 14s 里军队
        # 已在管道里，一钻出就瞬间弹出；且虫子绝不在军队没装好前空 morph 被秒）。
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            if vib is not None:
                vib.nydus_wave_loaded = self._first_wave_sent

        # 玩家单位级 claim 优先（控制权模型规则 1）：命中的 STAGE/STRIKE 立即让出管理权。
        player_tags: set[int] = set(getattr(self.ai, "_llm_controlled_tags", None) or set())
        self._yield_to_player(player_tags)

        # 佯攻队已认领的狗持续让出（同帧 FeintSquadAct 先跑，见 nydus.py 里两 act 的排序）。
        self._yield_to_feint(set(getattr(self.ai, "_vibecraft_nydus_feint_tags", None) or set()))

        # ⚠️ 真局验出的坑（2026-07-09 nydus_selftest 首跑）：STAGE→TRANSIT 的 passenger_tags
        # 判定**必须在 `_prune_dead` 之前**跑。SMART 装载生效那一刻，单位在**同一帧**从
        # `self.cache.own(ROACH)`（army 快照）里消失、同时出现在 `network.passengers_tags`
        # 里 —— 若先剪切，`_prune_dead` 看到这个 STAGE tag 不在 army 里就当"死了"直接删掉
        # 它的状态，`_tick_transit` 再跑到时 tag 已经没了，永远等不到 TRANSIT（真局症状：
        # NYDUSRAID load 正常触发 + canal.cargo_used>0 确认真装了货，但 transit/strike 事件
        # 永远是 0）。修法：passenger 判定提前，剪切时对"已确认是乘客"的 tag 天然豁免
        # （提前判定后该 tag 已经是 TRANSIT，`_prune_dead` 本就对 TRANSIT 豁免）。
        passenger_tags = self._passenger_tags()
        self._promote_stage_to_transit(now, passenger_tags)

        self._prune_dead(army)

        if not self._released and now >= self.release_after_s:
            self._release_all(reason="release_after_s")

        # 放在硬释放判定之后：release_all 当帧新加进 pending 的 tag 也能立刻处理，
        # 不用等下一帧才还 role。
        self._tick_pending_release(army)

        if not self._released:
            self._recruit(now, player_tags)
            self._tick_stage(now, army)

        # TRANSIT→STRIKE 判定 + 排空/兜底：与 released 无关，已装载的必须能钻出，
        # 已钻出的必须能继续打。
        await self._tick_transit(now, army, passenger_tags)
        self._tick_strike(now, army)

        # 收尾发布本帧持有的 tag，供下一帧 FeintSquadAct 排除（双向互斥的另一半）。
        self._publish_owned_tags()

    # ------------------------------------------------------------------
    # 剪切 / 释放
    # ------------------------------------------------------------------

    def _army_units_by_tag(self) -> dict[int, Any]:
        out: dict[int, Any] = {}
        for t in _RAID_UNIT_TYPES:
            for u in self.cache.own(t):
                out[u.tag] = u
        return out

    def _prune_dead(self, army: dict[int, Any]) -> None:
        """TRANSIT 单位暂时不在 army 属正常（人在坑道里），不剪；其余死了就清。"""
        for tag in list(self._state.keys()):
            if self._state[tag] == "TRANSIT":
                continue
            if tag not in army:
                self._state.pop(tag, None)
                self._state_since.pop(tag, None)
                self._loading_since.pop(tag, None)
                self._strike_retarget_at.pop(tag, None)
                self._strike_last_kind.pop(tag, None)
                self._unit_type.pop(tag, None)

    def _yield_to_player(self, player_tags: set[int]) -> None:
        for tag in list(self._state.keys()):
            if tag not in player_tags:
                continue
            if self._state[tag] == "TRANSIT":
                continue  # 人在坑道里，出来再释放
            self._state.pop(tag, None)
            self._state_since.pop(tag, None)
            self._loading_since.pop(tag, None)
            self._strike_retarget_at.pop(tag, None)
            self._strike_last_kind.pop(tag, None)
            self._pending_release_tags.add(tag)
            self._ever_released.add(tag)

    def _yield_to_feint(self, feint_tags: set[int]) -> None:
        """把 FeintSquadAct 已认领的狗**持续**让出去（2026-07-26 真局 bug 修）。

        原来只在 `_recruit` 里排除 feint tag —— 那只挡住"之后再招募"，挡不住"**之前**已经招募、
        现在被 feint 抢走"的那批：投送窗口一开(`nydus_wave_loaded=True`)FeintSquadAct 才激活并
        一次抓走 6 只狗，其中就有本 act 早已 STAGE 的。两个 act 于是每帧对同一只狗同时下令
        （本 act `move(家里网络)` vs feint `poke(敌方分矿)`）→ 狗在中间来回抽搐、永远进不了坑道。
        真局证据(server_20260726_192600.log):19:58:59 首波装载 → 19:59:01 佯攻队激活 →
        `total_staged` 卡在 **6**(=feint_cap)不动、`load n=2` 每 1.8s 空发 70+ 秒。

        修法:每帧把 feint 已认领的 tag 从本 act 状态里摘掉（TRANSIT 的除外——它在坑道里，
        feint 的候选池只有 ready 单位、本就够不着它）。不加进 `_ever_released`:feint 之后放手
        （窗口关闭/狗回防）本 act 还能重新招募它。
        """
        for tag in list(self._state.keys()):
            if tag not in feint_tags or self._state[tag] == "TRANSIT":
                continue
            self._state.pop(tag, None)
            self._state_since.pop(tag, None)
            self._loading_since.pop(tag, None)
            self._strike_retarget_at.pop(tag, None)
            self._strike_last_kind.pop(tag, None)
            logger.info(f"NYDUSRAID yield_to_feint tag={tag}")

    def _publish_owned_tags(self) -> None:
        """发布本 act 当前持有的 tag，供 FeintSquadAct 招募时排除（模块间约定，双向互斥）。

        `stage` 单独发一份:那是**可让渡**的部分（还在家门口集结，让给佯攻不影响投送）；
        TRANSIT/STRIKE 不可让渡（在坑道里 / 已在敌方家打），佯攻队不许抢。
        """
        with contextlib.suppress(Exception):
            self.ai._vibecraft_nydus_raid_tags = set(self._state)
            self.ai._vibecraft_nydus_raid_yieldable = {
                t for t, s in self._state.items() if s == "STAGE"
            }

    def _tick_pending_release(self, army: dict[int, Any]) -> None:
        if not self._pending_release_tags:
            return
        done: set[int] = set()
        for tag in self._pending_release_tags:
            u = army.get(tag)
            if u is None:
                continue
            with contextlib.suppress(Exception):
                self.knowledge.roles.clear_task(u)
            done.add(tag)
        self._pending_release_tags -= done

    def _release_all(self, reason: str) -> None:
        n = len(self._state)
        for tag in list(self._state.keys()):
            self._ever_released.add(tag)
            if self._state[tag] != "TRANSIT":
                self._pending_release_tags.add(tag)
        self._state.clear()
        self._state_since.clear()
        self._loading_since.clear()
        self._transit_since.clear()
        self._strike_retarget_at.clear()
        self._strike_last_kind.clear()
        self._released = True
        logger.warning(f"NYDUSRAID release reason={reason} n={n}")

    # ------------------------------------------------------------------
    # STAGE：招募 + 集结 + 首波/增援装载
    # ------------------------------------------------------------------

    def _recruit(self, now: float, player_tags: set[int]) -> None:
        # Round 4（声东击西）：FeintSquadAct 先跑一帧内认领的佯攻小狗池子，本 act 排除，
        # 两个 act 从小狗池子里认领不重叠的两批（见 feint_squad_act.py 文件头 docstring）。
        feint_tags: set[int] = set(getattr(self.ai, "_vibecraft_nydus_feint_tags", None) or set())
        queens = list(self.cache.own(UnitTypeId.QUEEN).ready)
        # 留家注卵女王 = max(keep_home_queens, 基地数)，**动态每帧算、不一次锁定**
        # （2026-07-12 用户：女王和注卵不停。旧逻辑第 1 只女王出生时就把留家锁死成 1 只 →
        # 之后每只新女王全被招募进坑道 → 留家永远只 1 只女王注卵 → inject_coverage 上不去、
        # larva 荒、蟑螂/农民全延迟。改成每矿留 1 只女王注卵，基地越多留越多，多出来的才钻坑道）。
        n_bases = 0
        with contextlib.suppress(Exception):
            n_bases = int(self.ai.townhalls.amount)
        keep = max(self.keep_home_queens, n_bases)
        sorted_q = sorted(queens, key=lambda u: u.tag)
        # 已在坑道(_state 里)的女王不撤回；留家名单从"还没被招募"的女王里按 tag 稳定取前 keep 只。
        home_queens = {u.tag for u in sorted_q[:keep]}
        self._home_queen_tags = home_queens

        newly: list[int] = []

        for q in queens:
            if q.tag in player_tags or q.tag in home_queens:
                continue
            if q.tag in self._state or q.tag in self._ever_released:
                continue
            if self._held_count(UnitTypeId.QUEEN) >= self.queen_cap:
                break
            self._state[q.tag] = "STAGE"
            self._state_since[q.tag] = now
            self._unit_type[q.tag] = UnitTypeId.QUEEN
            newly.append(q.tag)

        for r in self.cache.own(UnitTypeId.ROACH).ready:
            if self._held_count(UnitTypeId.ROACH) >= self.roach_cap:
                break
            if r.tag in player_tags or r.tag in self._state or r.tag in self._ever_released:
                continue
            self._state[r.tag] = "STAGE"
            self._state_since[r.tag] = now
            self._unit_type[r.tag] = UnitTypeId.ROACH
            newly.append(r.tag)

        for z in self.cache.own(UnitTypeId.ZERGLING).ready:
            if self._held_count(UnitTypeId.ZERGLING) >= self.zergling_cap:
                break
            if (
                z.tag in player_tags
                or z.tag in self._state
                or z.tag in self._ever_released
                or z.tag in feint_tags
            ):
                continue
            self._state[z.tag] = "STAGE"
            self._state_since[z.tag] = now
            self._unit_type[z.tag] = UnitTypeId.ZERGLING
            newly.append(z.tag)

        if newly:
            kind = "reinforce" if self._first_wave_sent else "stage"
            logger.info(
                f"NYDUSRAID {kind} n={len(newly)} total_staged={self._count_state('STAGE')}"
            )

    def _count_state(self, state: str) -> int:
        return sum(1 for s in self._state.values() if s == state)

    def _held_count(self, tid: UnitTypeId) -> int:
        """本 act **当前还持有**的该兵种数量（招募封顶按它算，不按终身累计）。

        2026-07-26 用户"第一波被打掉后要能来第二波"暴露的 bug：原先用 `_recruited_*` 终身累加、
        单位死了也不减 —— 第一波 12 只蟑螂全死在敌方家之后计数仍是 12 ≥ cap，**再也招不到新兵**，
        第二波在数据结构层面就不可能发生。改成数"现在还在 `_state` 里的"：死亡由 `_prune_dead`
        摘除 → 计数自然回落 → 新产的蟑螂/狗被招进来组第二波。
        """
        return sum(1 for t in self._state if self._unit_type.get(t) == tid)

    def _get_stage_anchor(self) -> Point2 | None:
        """集结点：己方 NydusNetwork **往外让开 8 格**（朝地图中心那侧）。

        2026-07-27 用户："集结的位置不用太靠近主基地"。原来直接取网络本体坐标 → 一堆兵贴着
        基地站、堵在矿线和建筑之间。往地图中心方向让开一段：不挡家里的路，位置也更靠近来敌
        方向（家里真挨打时它们本来就在路上）。装载时多走这 8 格只多 2-3 秒。

        一次算好锁住（静态坐标，满足"目标点一次锁定不漂移"——CLAUDE.md 强规则）；网络还没建好
        则兜底 start_location 同样往外让开。
        """
        if self._stage_anchor is not None:
            return self._stage_anchor
        with contextlib.suppress(Exception):
            base = None
            networks = self.cache.own(UnitTypeId.NYDUSNETWORK).ready
            if networks:
                base = networks.closest_to(self.ai.start_location).position
            else:
                base = self.ai.start_location
            out = base.towards(self.ai.game_info.map_center, _STAGE_OFFSET_FROM_NETWORK)
            if networks:  # 网络没好之前不缓存,等网络建好再定死
                self._stage_anchor = out
            return out
        with contextlib.suppress(Exception):
            return self.ai.start_location
        return None

    def _home_under_attack(self) -> bool:
        """家里是不是正在挨打：敌方**战斗单位**进到自家任一基地 _HOME_THREAT_RADIUS 内。"""
        with contextlib.suppress(Exception):
            halls = list(self.ai.townhalls)
            if not halls:
                return False
            for e in self.ai.enemy_units:
                if not _is_army(e):
                    continue
                if any(e.distance_to(h) <= _HOME_THREAT_RADIUS for h in halls):
                    return True
        return False

    def _tick_stage(self, now: float, army: dict[int, Any]) -> None:
        anchor = self._get_stage_anchor()
        stage_tags = [t for t, s in self._state.items() if s == "STAGE"]
        if not stage_tags:
            return

        # 家里挨打 → 集结中的兵**全部还给 sharpy 防守**(2026-07-27 用户真机:"对面都打到家里来了,
        # 就因为你一直移动到家里这个操作导致他们参与不了防守")。原因有二:①每帧 set_task(Reserved)
        # 让 PlanZoneDefense 拿不到它们(它只用 free_units);②每帧 move(锚点)把被战斗推开的单位又
        # 拽回去。威胁解除后下一帧自动恢复集结,状态仍是 STAGE、不用重新招募。
        if self._home_under_attack():
            if now - self._last_defend_log >= 5.0:
                self._last_defend_log = now
                logger.info(
                    f"NYDUSRAID stage_yield_defense n={len(stage_tags)} 家里挨打,集结兵交给防守"
                )
            for tag in stage_tags:
                u = army.get(tag)
                if u is not None:
                    with contextlib.suppress(Exception):
                        self.knowledge.roles.clear_task(u)
            return

        arrived: list[int] = []
        for tag in stage_tags:
            u = army.get(tag)
            if u is None:
                continue
            self.knowledge.roles.set_task(UnitTask.Reserved, u)
            if anchor is None:
                continue
            if u.distance_to(anchor) > _STAGE_ARRIVE_DIST:
                # **设集结点,不是每帧拽**:只在它闲着(没有指令在执行)或隔了足够久才重发一次。
                # 每帧硬发 = 单位被钉死在锚点上,任何别的行为(躲、还手)都会被下一帧覆盖掉。
                idle = not getattr(u, "orders", None)
                if idle or now - self._stage_move_at.get(tag, -999.0) >= _STAGE_REISSUE_COOLDOWN_S:
                    with contextlib.suppress(Exception):
                        u.move(anchor)  # 集结 = 撤退性质移动,不 attack_move(控制权模型规则4)
                    self._stage_move_at[tag] = now
            else:
                arrived.append(tag)

        if not arrived:
            return

        # Round 2（2026-07-09 真局教训）：不再等 canal_ready 才开始装载。原设计意图
        # 本就是"STAGE 阶段就把 army 预装进自家坑道网络"（`docs/plans/2026-07-09-
        # nydus-raid-polish-design.md` 精修点2），网络自己随时能装人（乘客在网络里
        # 等着，等虫洞打通再排空是 SC2 原生机制），没必要卡 canal_ready 才开始装——
        # 卡了反而拉长"worm ready → army 才开始装 → 才排空"的暴露窗口。真正的排空
        # 门控在 `_tick_transit`（有 cargo 的 canal 才 issue UNLOAD），这里只管把人
        # 先塞进管道里等着。
        networks = self.cache.own(UnitTypeId.NYDUSNETWORK).ready
        if not networks:
            return
        if self._network_ready_since is None:
            self._network_ready_since = now
        network = networks.closest_to(self.ai.start_location)

        if not self._first_wave_sent:
            staged_supply = self._supply_of(arrived, army)
            staged_roaches = sum(
                1
                for t in arrived
                if army.get(t) is not None and army[t].type_id == UnitTypeId.ROACH
            )
            # 等蟑螂潮攒够再投（2026-07-10 用户：狗蟑快攻要七八个蟑螂 + 一堆狗成波再灌，
            # 别 3 个蟑螂 10 supply 就发）。兜底两条（Fable5 2026-07-11 防再死锁）：
            #   ① 总待命兵力已很大（supply≥20）也发；
            #   ② 坑道网络 ready 超过 _WAVE1_NETWORK_TIMEOUT_S 还没凑够门槛，只要有 ≥4 蟑螂或
            #      ≥3 待命兵就发——防蟑螂产能被骚扰打断时首波死等、canal 永不落地。
            net_timed_out = (
                self._network_ready_since is not None
                and now - self._network_ready_since >= _WAVE1_NETWORK_TIMEOUT_S
                and (staged_roaches >= 4 or len(arrived) >= 3)
            )
            if (
                staged_roaches < self.min_roaches_wave1
                and staged_supply < _WAVE1_SUPPLY_FALLBACK
                and not net_timed_out
            ):
                return
            to_load = arrived
            self._first_wave_sent = True
            logger.info(
                f"NYDUSRAID load n={len(to_load)} wave=1 supply={staged_supply:.1f} roaches={staged_roaches}"
            )
        else:
            to_load = arrived
            if to_load:
                logger.info(f"NYDUSRAID load n={len(to_load)} wave=reinforce")

        # 蟑螂先装（先走）——蟑螂慢、扛线在前，狗提速后面追（用户 2026-07-10）。
        to_load = sorted(
            to_load,
            key=lambda t: (
                0 if (army.get(t) is not None and army[t].type_id == UnitTypeId.ROACH) else 1
            ),
        )
        for tag in to_load:
            last = self._loading_since.get(tag, -999.0)
            if now - last < _LOAD_RETRY_COOLDOWN_S:
                continue
            u = army.get(tag)
            if u is None:
                continue
            self._bypass_cmd(AbilityId.SMART, u, network)
            self._loading_since[tag] = now

    @staticmethod
    def _supply_of(tags: list[int], army: dict[int, Any]) -> float:
        total = 0.0
        for tag in tags:
            u = army.get(tag)
            if u is None:
                continue
            total += _SUPPLY_BY_TYPE.get(u.type_id, 1.0)
        return total

    # ------------------------------------------------------------------
    # TRANSIT：坑道内跟踪 + 排空
    # ------------------------------------------------------------------

    def _passenger_tags(self) -> set[int]:
        tags: set[int] = set()
        with contextlib.suppress(Exception):
            for n in self.cache.own(UnitTypeId.NYDUSNETWORK):
                tags |= set(n.passengers_tags)
        with contextlib.suppress(Exception):
            for c in self.cache.own(UnitTypeId.NYDUSCANAL):
                tags |= set(c.passengers_tags)
        return tags

    def _promote_stage_to_transit(self, now: float, passenger_tags: set[int]) -> None:
        """STAGE(已发装载指令) → TRANSIT：确认真的在坑道乘客名单里。

        **必须在 `_prune_dead` 之前调用**（见 `_tick` 内注释，2026-07-09 真局验出的坑）：
        单位装载生效那一帧会同时"从 army 消失 + 出现在 passenger_tags 里"，若先剪切
        会被误判死亡、状态被删，永远等不到这里。
        """
        for tag in list(self._state.keys()):
            if self._state[tag] == "STAGE" and tag in passenger_tags:
                self._state[tag] = "TRANSIT"
                self._state_since[tag] = now
                self._transit_since[tag] = now
                self._loading_since.pop(tag, None)
                logger.info(f"NYDUSRAID transit tag={tag}")

    async def _tick_transit(
        self, now: float, army: dict[int, Any], passenger_tags: set[int]
    ) -> None:
        # TRANSIT → STRIKE：不再是乘客 + 重新出现在 army（钻出）
        for tag in list(self._state.keys()):
            if self._state[tag] != "TRANSIT":
                continue
            if tag in passenger_tags:
                continue
            u = army.get(tag)
            if u is None:
                continue  # 可能只是缓存刷新延迟一帧，下一帧再看，不误判死亡
            self._state[tag] = "STRIKE"
            self._state_since[tag] = now
            self._transit_since.pop(tag, None)
            logger.info(f"NYDUSRAID strike tag={tag} pos=({u.position.x:.1f},{u.position.y:.1f})")

        # 兜底②：卡坑道太久且没 ready canal 可卸 → 网络侧兜底卸回家，释放走正面
        canal_ready = bool(self.cache.own(UnitTypeId.NYDUSCANAL).ready)
        if not canal_ready:
            stuck = [
                t
                for t, since in self._transit_since.items()
                if now - since >= _TRANSIT_STUCK_TIMEOUT_S
            ]
            if stuck:
                await self._bail_unload_network(stuck, now)

        # 主动排空 canal：有乘客就周期性下 UNLOAD（运行时探真名，评审处置 #1）
        canals_with_cargo = [
            c for c in self.cache.own(UnitTypeId.NYDUSCANAL).ready if c.cargo_used > 0
        ]
        if canals_with_cargo and now - self._last_unload_probe >= _UNLOAD_PROBE_COOLDOWN_S:
            self._last_unload_probe = now
            await self._issue_unload_canal(canals_with_cargo)

    async def _probe_unload_ability(self, unit: Any) -> AbilityId | None:
        try:
            results = await self.ai.get_available_abilities([unit])
        except Exception:
            logger.warning("NYDUSRAID get_available_abilities probe failed")
            return None
        if not results or not results[0]:
            return None
        for ab in results[0]:
            with contextlib.suppress(Exception):
                if "UNLOAD" in ab.name:
                    return ab
        return None

    async def _issue_unload_canal(self, canals: list[Any]) -> None:
        ability = self._unload_canal_ability
        if ability is None:
            ability = await self._probe_unload_ability(canals[0])
            if ability is None:
                logger.warning("NYDUSRAID unload probe failed: canal 未列出 UNLOAD ability")
                return
            self._unload_canal_ability = ability
            logger.info(f"NYDUSRAID unload_ability_canal probed={ability.name}")
        for c in canals:
            self._bypass_cmd(ability, c)

    async def _bail_unload_network(self, stuck_tags: list[int], now: float) -> None:
        networks = list(self.cache.own(UnitTypeId.NYDUSNETWORK).ready)
        if not networks:
            return
        ability = self._unload_network_ability
        if ability is None:
            ability = await self._probe_unload_ability(networks[0])
            if ability is None:
                return
            self._unload_network_ability = ability
            logger.info(f"NYDUSRAID unload_ability_network probed={ability.name}")
        for n in networks:
            self._bypass_cmd(ability, n)
        # Round 3 真局验出的坑（2026-07-09 nydus_selftest VeryEasy 首跑）：探到的 ability
        # 是 `UNLOADALL_NYDASNETWORK`——**全体**乘客一次性卸出，SC2 侧没有按 tag 精确卸载
        # 的可靠路径（`UNLOADUNIT_NYDASNETWORK` 需要 passenger 的可寻址 Unit 目标，
        # `.passengers`/`.passengers_tags` 只暴露 tag，不足以稳定构造该目标）。
        # 之前只把 `stuck_tags`（卡够 100s 的那批）从 `self._state` 清掉——但 UNLOADALL
        # 把网络里**所有**乘客（含刚装载不久、根本没卡住的增援）一起吐回自家网络门口，
        # 那些没被清掉的 tag 仍标 "TRANSIT"，下一帧 `_tick_transit` 首段循环看到它们
        # "不再是乘客 + 重新出现在 army" → 误判"钻出敌方家"，提升到 STRIKE、对着敌方
        # 主基地/建筑发起跨地图强攻——但它们其实站在**我方网络门口**，白白送死或
        # 极晚才到（真机实测：10 次 STRIKE 里 6 次落点在己方基地群 (121-125, 111-116)，
        # 只有 4 次落点在敌方矿线附近 (57-61, 21-30)）。
        # 修法：bail 触发那一刻，把**当前所有 TRANSIT 状态**的 tag（不只 stuck_tags）
        # 一起从状态字典清掉——如实反映"UNLOADALL 已经把它们都吐出来了"这一事实，
        # 不留悬空 TRANSIT 条目被误判。清出的单位走 `_pending_release_tags` 还给
        # sharpy 默认路径（走正面），不再被本 act 当"已钻出"处理。
        affected = set(stuck_tags) | {t for t, s in self._state.items() if s == "TRANSIT"}
        logger.warning(
            f"NYDUSRAID bail_transit n={len(stuck_tags)} affected={len(affected)} "
            "reason=canal_not_ready_timeout"
        )
        for tag in affected:
            self._transit_since.pop(tag, None)
            self._state.pop(tag, None)
            self._state_since.pop(tag, None)
            self._ever_released.add(tag)
            self._pending_release_tags.add(tag)

    # ------------------------------------------------------------------
    # STRIKE：钻出打击（优先农民簇，不追散兵）
    # ------------------------------------------------------------------

    def _enemy_worker_anchor(self) -> Point2 | None:
        """农民质心搜索锚点。

        Round 3 真机诊断（2026-07-09 nydus_selftest 排查 worker_share 恒 0%）：最初用
        `zone_manager.expansion_zones` 排序取"离敌方 start_location 最近的 zone"再取其
        `mineral_line_center`——**踩了 sharpy 的坑**：`Zone.behind_mineral_positions`
        在 zone **构造那一刻**用 `self.ai.mineral_field.closer_than(10, center)` 算一次
        就**永久缓存**，若那一刻还没扫到敌方矿（多数游戏里确实还没），列表永远是空的，
        `mineral_line_center` 属性空列表分支直接退化成 `zone.center_location`（约等于敌方
        main 的 townhall 位置本身，不是矿线）。而 `_BuildNydusCanalAtEnemy._pick_worm_position`
        同样因为 `behind_positions` 为空，真正用的是它的**次级兜底候选**
        （`enemy_pos.towards(map_center, d)`，d∈{5,8,12,15,3}）——这批候选跟"矿线锚点"
        毫无关系，只是朝地图中心方向找个能放的点。真机实测两者最终能相差 ~19-21 格
        （worm 真落点 vs 理论 zone 锚点），导致按 zone 锚点搜索农民永远扑空。

        **修法：锚点直接取"当前 ready 的坑道虫(canal)真实位置"**——那正是 STRIKE 单位
        钻出来的地方（ground truth，不依赖 sharpy zone 缓存是否新鲜）。没有 ready canal
        （刚被摧毁重下期间）才退回敌方 start_location 兜底。
        """
        with contextlib.suppress(Exception):
            canals = self.cache.own(UnitTypeId.NYDUSCANAL).ready
            if canals:
                return canals.closest_to(self.ai.start_location).position
        with contextlib.suppress(Exception):
            return self.ai.enemy_start_locations[0]
        return None

    def _enemy_worker_center(self) -> Point2 | None:
        """敌方矿线农民质心（Round 3 精修，2026-07-09）。

        锚点固定取矿线中心（**不是**打击单位当前位置）——同 `bc_raid_act.py::_nearby_worker_center`
        成熟范式：农民沿矿线躲藏/逃跑，锚点跟矿线走比跟单位走更稳，也不会"农民逃出打击单位半径
        就跟丢"。Round 2 遗留 bug 根因正是旧版按**打击单位到农民的距离**做半径过滤——单位钻出/
        转移目标后位置会漂移，半径判定不稳定，容易把矿线农民漏判出搜索范围、回落到"拆建筑"分支。
        现在半径判定固定锚在矿线上，天然规避这个问题。

        没有农民落在矿线附近（清光/全逃）→ 返回 None，上层回退拆建筑（"农民清完/被赶跑再拆建筑"）。
        """
        with contextlib.suppress(Exception):
            anchor = self._enemy_worker_anchor()
            if anchor is None:
                return None
            all_workers = [w for w in self.ai.enemy_units if _is_worker(w)]
            workers = [
                w for w in all_workers if anchor.distance_to(w.position) < _WORKER_SEEK_RADIUS
            ]
            now = float(self.ai.time)
            if now - getattr(self, "_worker_dbg_last", -999.0) >= 5.0:
                self._worker_dbg_last = now
                nearest_d = (
                    min(anchor.distance_to(w.position) for w in all_workers)
                    if all_workers
                    else -1.0
                )
                logger.info(
                    f"NYDUSRAID workerdbg anchor=({anchor.x:.1f},{anchor.y:.1f}) "
                    f"all_enemy_workers={len(all_workers)} in_radius={len(workers)} "
                    f"nearest_d={nearest_d:.1f}"
                )
            if workers:
                n = len(workers)
                return Point2(
                    (
                        sum(w.position.x for w in workers) / n,
                        sum(w.position.y for w in workers) / n,
                    )
                )
        return None

    def _tick_strike(self, now: float, army: dict[int, Any]) -> None:
        strike_tags = [t for t, s in self._state.items() if s == "STRIKE"]
        if not strike_tags:
            return
        # 农民质心每 tick 算一次（不是每单位算一次）：全体 STRIKE 单位共享同一目标点，
        # beeline attack-move 冲过去——沿途 attack-move 天然会打到路过的建筑/散兵，
        # 不需要额外逻辑，这就是"扑农民簇"的beeline 语义。
        worker_center = self._enemy_worker_center()
        for tag in strike_tags:
            u = army.get(tag)
            if u is None:
                continue
            self.knowledge.roles.set_task(UnitTask.Reserved, u)
            last = self._strike_retarget_at.get(tag, -999.0)
            if now - last < _STRIKE_RETARGET_COOLDOWN_S and getattr(u, "orders", None):
                continue
            target, kind = self._pick_strike_target(u, worker_center)
            if target is not None:
                with contextlib.suppress(Exception):
                    u.attack(target)
                if self._strike_last_kind.get(tag) != kind:
                    self._strike_last_kind[tag] = kind
                    logger.info(f"NYDUSRAID strike tag={tag} tgt={kind}")
            self._strike_retarget_at[tag] = now
        # 女王 transfuse 续航（2026-07-11 用户点4：女王钻出后给残血友军/彼此加血 125HP）
        self._cast_transfuse(now, strike_tags, army)
        # 女王在敌方家铺菌毯（2026-07-26 用户）
        self._cast_enemy_creep(now, strike_tags, army)

    def _cast_enemy_creep(self, now: float, strike_tags: list[int], army: dict[int, Any]) -> None:
        """钻出来的女王在**对方家**种菌毯瘤（2026-07-26 用户）。

        为什么在敌方家种得下:女王的菌毯瘤**只能种在菌毯上**，而坑道虫(canal)落地后自带一圈菌毯，
        所以钻出来的女王脚下就是菌毯。往菌毯**边缘**种 → 菌毯继续往敌方矿线扩 → 后续增援有视野、
        单位加速、坑道虫被拆了也还留着这块地盘。

        取点:以女王为心由远及近扫（优先扎在菌毯外沿，扩得最远），要求该点 `has_creep` ∧
        `in_placement_grid` ∧ 离已有菌毯瘤 ≥ `_CREEP_TUMOR_SPACING`（别挤在一起浪费能量）。
        能量优先给 transfuse（救命 > 铺毯）:只有 energy ≥ 两者之和才种。
        """
        cd = self._creep_at
        for tag in strike_tags:
            q = army.get(tag)
            if q is None or getattr(q, "type_id", None) != UnitTypeId.QUEEN:
                continue
            if getattr(q, "energy", 0) < _TRANSFUSE_MIN_ENERGY + CREEP_TUMOR_ENERGY:
                continue  # 先保住一发 transfuse 的能量
            if now - cd.get(q.tag, -999.0) < _CREEP_TUMOR_COOLDOWN_S:
                continue
            spot = self._pick_creep_spot(q.position)
            if spot is None:
                continue
            self._bypass_cmd(AbilityId.BUILD_CREEPTUMOR_QUEEN, q, spot)
            cd[q.tag] = now
            logger.info(f"NYDUSRAID creep tag={q.tag} at=({spot.x:.0f},{spot.y:.0f})")

    def _pick_creep_spot(self, origin: Point2) -> Point2 | None:
        """女王脚下菌毯的**外沿**取一个可种点，优先朝敌方矿线方向扩；挑不到返回 None。"""
        toward = self._enemy_worker_center()
        return pick_creep_tumor_spot(
            self.ai,
            origin,
            existing_tumors(self.cache),
            max_r=_CREEP_TUMOR_MAX_R,
            spacing=_CREEP_TUMOR_SPACING,
            toward=toward,
        )

    def _cast_transfuse(self, now: float, strike_tags: list[int], army: dict[int, Any]) -> None:
        """STRIKE 中的女王 energy>=50 → 给射程内最残血的 raid 友军加血（125HP，AbilityId
        已真机核对=TRANSFUSION_TRANSFUSION）。原先一波无续航，拆 ~8 农民就被清；有了女王
        互奶/奶蟑螂，一波在敌方家存活更久、杀伤更大。"""
        cd = getattr(self, "_transfuse_at", None)
        if cd is None:
            cd = self._transfuse_at = {}
        queens = [
            q
            for q in (army.get(t) for t in strike_tags)
            if q is not None
            and getattr(q, "type_id", None) == UnitTypeId.QUEEN
            and getattr(q, "energy", 0) >= _TRANSFUSE_MIN_ENERGY
        ]
        if not queens:
            return
        # 伤员候选：所有在编 raid 单位（不止 STRIKE）里残血的
        wounded = []
        for t in self._state:
            u = army.get(t)
            if u is not None and getattr(u, "health_percentage", 1.0) < 0.95:
                wounded.append(u)
        if not wounded:
            return
        for q in queens:
            if now - cd.get(q.tag, -999.0) < _TRANSFUSE_COOLDOWN_S:
                continue
            near = [w for w in wounded if q.distance_to(w) <= _TRANSFUSE_RANGE]
            if not near:
                continue
            target = min(near, key=lambda w: getattr(w, "health_percentage", 1.0))
            self._bypass_cmd(AbilityId.TRANSFUSION_TRANSFUSION, q, target)
            cd[q.tag] = now
            logger.info(f"NYDUSRAID transfuse q={q.tag} -> {target.tag}")

    def _pick_strike_target(self, u: Any, worker_center: Point2 | None) -> tuple[Any | None, str]:
        """看情况选目标（2026-07-10 用户：打农民要看情况，别一竿子打死）：
        农民好抓 + 附近没威胁敌军 → 扑农民；附近有威胁敌军（会放风筝空追）→ 先打最近敌军；
        没农民没敌军 → 拆最近建筑 > 敌方主基地兜底。
        """
        threat = self._enemy_combat_near(u, _STRIKE_THREAT_RADIUS)
        # 农民 + 附近没威胁敌军 → 扑农民（经济杀伤）
        if worker_center is not None and threat < _STRIKE_THREAT_COUNT:
            return worker_center, "worker"
        # 有威胁敌军 → 先打最近敌军（反打，别空追躲得快的农民被风筝）
        if threat >= _STRIKE_THREAT_COUNT:
            with contextlib.suppress(Exception):
                combat = [
                    e
                    for e in self.ai.enemy_units
                    if not _is_worker(e)
                    and not getattr(e, "is_structure", False)
                    and u.distance_to(e) <= _STRIKE_THREAT_RADIUS
                ]
                if combat:
                    return min(combat, key=lambda e: u.distance_to(e)), "army"
        # 有农民但也有敌军、敌军却不在打击半径内（远处威胁）→ 仍先扑农民（就近打）
        if worker_center is not None:
            return worker_center, "worker"
        with contextlib.suppress(Exception):
            structures = [
                s
                for s in self.ai.enemy_structures
                if u.distance_to(s) <= _STRIKE_TARGET_RADIUS * 1.5
            ]
            if structures:
                return min(structures, key=lambda s: u.distance_to(s)), "structure"
        with contextlib.suppress(Exception):
            return self.ai.enemy_start_locations[0], "enemy_base"
        return None, "none"

    def _enemy_combat_near(self, u: Any, radius: float) -> int:
        """u 附近 radius 内的敌方战斗单位数（排除农民/建筑）——判"追农民会不会被反打"。"""
        cnt = 0
        with contextlib.suppress(Exception):
            for e in self.ai.enemy_units:
                if _is_worker(e) or getattr(e, "is_structure", False):
                    continue
                if u.distance_to(e) <= radius:
                    cnt += 1
        return cnt

    # ------------------------------------------------------------------
    # bypass 施法（同 common_bot.py::load_bunker 范式）
    # ------------------------------------------------------------------

    def _bypass_cmd(self, ability: AbilityId, unit: Any, target: Any | None = None) -> None:
        if not hasattr(self.ai, "_vibecraft_bypass_actions"):
            self.ai._vibecraft_bypass_actions = []
        with contextlib.suppress(Exception):
            cmd = UnitCommand(ability, unit, target, False)
            self.ai._vibecraft_bypass_actions.append(cmd)
