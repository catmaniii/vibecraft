"""佯攻小队执行器 —— FeintSquadAct（Round 4「声东击西」战略重构，2026-07-09）。

背景（详见 `docs/plans/2026-07-09-nydus-raid-polish-design.md`「Round 4」段）：
前 3 轮坑道虫突袭撞墙的根因是"虫洞钻出 14s 在敌方家太脆、对会防守的对手(VeryHard)
立不住、0 存活"。用户拍板正解（=职业选手做法）：**别硬下坑道，先用小股部队正面
骚扰/佯攻，把敌军主力从矿区引开，等敌方矿线空了（主力远离）再下坑道**。

本 act 是那个"诱饵"——一小股速狗（默认 6 只，可牺牲/可回撤）持续骚扰敌方
**二矿（natural）**（前线，最容易触发敌方 AI 派兵支援的位置），迫使敌方把驻扎在
主基地的机动部队调去支援二矿 → 主基地矿线空虚 → `nydus.py::_BuildNydusCanalAtEnemy`
的窗口检测捕捉到"主基地附近敌方战斗单位数低于阈值" → 虫洞落在无人守卫的主矿线，
army 钻出屠农民。

**这是诱饵不是主力**：poke-retreat 两态（不是 bc_raid_act 的三态 STAGE/DIVE/HEAL，
佯攻不需要"回家修理"这么复杂，退回去等血回满即可再冲）：
  POKE    —— 直推目标（敌方二矿矿线），到位后打工人/建筑/沿途散兵，边打边看血。
  RETREAT —— 血量危急（< bail_hp_ratio）→ 撤退保命（用 move 不用 attack_move，
             控制权模型规则4），回血到 recover_hp_ratio 以上 → 切回 POKE 再冲。

**招募封顶 + 与 NydusRaidAct 分池**：招募 `feint_cap`（默认 6）只小狗（tag 从小到大
确定性选取，避免每帧抖动），把已认领 tag 写进 `self.ai._vibecraft_nydus_feint_tags`
（模块间约定，`nydus_raid_act.py::_recruit` 读它排除，两个 act 分别从小狗池子里
认领不重叠的两批，互不越界抢单位）。

**目标锚点**：敌方二矿（zone rank1，由近及远第二个 expansion）的 `mineral_line_center`；
没有二矿（1v1 单矿图/未侦察，`Zone.behind_mineral_positions` 空退化为 `center_location`，
sharpy 已知行为，见 `nydus_raid_act.py` Round3 注释）则退回敌方主基地
`enemy_start_locations[0]`。**一次锁定缓存，不每帧重算**（CLAUDE.md 强规则：
目标坐标一次规划、锁定、别每帧重选）。

**Reserve 独占**：每帧对已认领的小狗 `roles.set_task(Reserved, u)`，天然不被
`PlanZoneGather`/`PlanZoneAttack` 拽走（同 `HarassWorkerLineAct`/`NydusRaidAct` 范式）。
玩家单位级 claim（`ai._llm_controlled_tags`）优先，命中的 tag 立即让出（控制权模型规则1）。

接线：放进 `nydus.py` 战术 `SequentialList`，**排在 `NydusRaidAct()` 之前**（同一帧内
先跑，认领的 tag 才能被 `NydusRaidAct._recruit` 读到并排除），排在 `PlanZoneGather()`
之前（防止被当 idle 收编）。
"""

from __future__ import annotations

import contextlib
from typing import Any

from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts import ActBase

_FEINT_CAP: int = 6
_BAIL_HP_RATIO: float = 0.35
_RECOVER_HP_RATIO: float = 0.75
_ARRIVE_DIST: float = 8.0  # 离目标锚点小于此距离 → 视为"已到位"，切 hit-and-run 微操
_DANGER_RADIUS: float = 9.0  # 敌方战斗单位在此半径内计入"对方兵多"判定（进退依据）
_RETREAT_BACK: float = (
    20.0  # RETREAT 撤到"离目标锚点这么远(朝家方向)"的固定点，不一路撤回家（2026-07-12）
)
_SQUAD_LOG_COOLDOWN_S: float = 5.0  # 群状态摘要日志节流

# 自适应目标(2026-07-12 用户「主力回家救场了,佯攻狗就真打最外面的四矿/三矿/二矿」)──
_TARGET_REEVAL_S: float = 4.0  # 目标每这么久重评一次(不每帧漂移,遵 CLAUDE.md 目标锁定精神)
_MAIN_HOME_RADIUS: float = 18.0  # 敌方战斗单位在敌主基这半径内 → 算"主力在家救场"
_MAIN_HOME_MIN: int = 3  # 主基附近战斗单位 ≥ 此 → 主力回家 → 转打空虚外矿
_BASE_DEFENDED_RADIUS: float = 12.0  # 某外矿这半径内有敌战斗单位 → 算有防守,跳过找更外/更空的

_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.PROBE, UnitTypeId.SCV, UnitTypeId.DRONE}
)
# 敌方基地(resource center)类型:判"敌方已占的外矿"用(打真基地,不打空地)。
_TOWNHALL_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.HATCHERY,
        UnitTypeId.LAIR,
        UnitTypeId.HIVE,
        UnitTypeId.NEXUS,
        UnitTypeId.COMMANDCENTER,
        UnitTypeId.ORBITALCOMMAND,
        UnitTypeId.PLANETARYFORTRESS,
        UnitTypeId.COMMANDCENTERFLYING,
        UnitTypeId.ORBITALCOMMANDFLYING,
    }
)


class FeintSquadAct(ActBase):  # type: ignore[misc]
    """佯攻小队：小股速狗持续骚扰敌方二矿，引敌军主力离开主基地矿线。"""

    def __init__(
        self,
        feint_cap: int = _FEINT_CAP,
        bail_hp_ratio: float = _BAIL_HP_RATIO,
        recover_hp_ratio: float = _RECOVER_HP_RATIO,
    ) -> None:
        super().__init__()
        self.feint_cap = feint_cap
        self.bail_hp_ratio = bail_hp_ratio
        self.recover_hp_ratio = recover_hp_ratio

        self._tags: set[int] = set()
        self._state: dict[int, str] = {}  # tag -> "POKE" / "RETREAT"
        self._target_anchor: Point2 | None = None
        self._target_since: float = -999.0  # 目标上次重评时刻(每 _TARGET_REEVAL_S 自适应重评)
        self._target_mode: str = "pull"  # "pull"(引主力,打二矿) / "punish"(主力回家,打空虚外矿)
        self._last_squad_log: float = -999.0

    # ------------------------------------------------------------------
    # ActBase entry point
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        try:
            self._tick()
        except Exception:
            logger.exception("FeintSquadAct._tick error")
        return True  # non-blocking，同 NydusRaidAct/HarassWorkerLineAct 范式

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        now = float(self.ai.time)
        lings = {u.tag: u for u in self.cache.own(UnitTypeId.ZERGLING).ready}
        player_tags: set[int] = set(getattr(self.ai, "_llm_controlled_tags", None) or set())

        # 前期(未到投送窗口)**根本不管佯攻狗**(用户 2026-07-12「前期别送狗」+ 真机:前期把狗送出去/
        # staging 到中场,家里没人防守 → 被 VeryHard 早压死、攒不出蟑螂)。释放它们**不 Reserve** →
        # 回落到 PlanZoneDefense/PlanZoneGather 帮**防守家里**;到投送窗口再收编去佯攻引主力。
        if not self._drop_imminent():
            self._tags.clear()
            self._state.clear()
            self.ai._vibecraft_nydus_feint_tags = set()
            return

        # 玩家 claim 优先（控制权模型规则1）：命中的立即放弃管理权。
        self._tags = {t for t in self._tags if t in lings and t not in player_tags}
        for t in list(self._state.keys()):
            if t not in self._tags:
                self._state.pop(t, None)

        # 招募封顶：tag 从小到大确定性选取，与 NydusRaidAct **双向互斥**（2026-07-26 真局 bug 修）。
        # 旧版只有单向:raid 排除本 act 已认领的 tag。但本 act 到投送窗口才激活，激活那刻 raid 早已
        # 把一批狗认领去集结了 —— 本 act 不看 raid 的池子就把它们抢过来，两个 act 每帧对同一只狗
        # 下相反的命令（raid `move(家里网络)` / 本 act `poke(敌方分矿)`）→ 狗在中间来回抽搐。
        # 现在:优先拿**自由狗**；不够再从 raid 标记为"可让渡"的集结狗(STAGE)里补——绝不碰它
        # TRANSIT(坑道内)/STRIKE(已在敌方家打)的单位。raid 侧 `_yield_to_feint` 每帧把被本 act
        # 认领的 tag 摘出自己的状态，形成交接闭环。
        if len(self._tags) < self.feint_cap:
            raid_owned: set[int] = set(
                getattr(self.ai, "_vibecraft_nydus_raid_tags", None) or set()
            )
            yieldable: set[int] = set(
                getattr(self.ai, "_vibecraft_nydus_raid_yieldable", None) or set()
            )
            free = sorted(
                t
                for t in lings
                if t not in self._tags and t not in player_tags and t not in raid_owned
            )
            spare = sorted(
                t for t in lings if t not in self._tags and t not in player_tags and t in yieldable
            )
            for t in free + spare:
                if len(self._tags) >= self.feint_cap:
                    break
                self._tags.add(t)
                self._state[t] = "POKE"

        # 发布认领结果，供 nydus_raid_act.py::_recruit 排除（模块间约定，见文件头 docstring）。
        self.ai._vibecraft_nydus_feint_tags = set(self._tags)

        if not self._tags:
            return

        target = self._get_target_anchor()
        workers = self._visible_enemy_workers()
        threats = self._threats()

        for tag in list(self._tags):
            u = lings.get(tag)
            if u is None:
                self._state.pop(tag, None)
                continue
            self.knowledge.roles.set_task(UnitTask.Reserved, u)
            self._tick_unit(u, tag, now, target, workers, threats, lings)

        if now - self._last_squad_log >= _SQUAD_LOG_COOLDOWN_S:
            self._last_squad_log = now
            n_poke = sum(1 for s in self._state.values() if s == "POKE")
            n_retreat = sum(1 for s in self._state.values() if s == "RETREAT")
            logger.info(f"NYDUSFEINT squad n={len(self._tags)} poke={n_poke} retreat={n_retreat}")

    def _tick_unit(
        self,
        u: Any,
        tag: int,
        now: float,
        target: Point2 | None,
        workers: Any,
        threats: list[Any],
        lings: dict[int, Any],
    ) -> None:
        # 到此已是投送窗口(前期由 _tick 提前 return、狗回防守,见 _tick 注释)。
        hp = self._hp_ratio(u)
        state = self._state.get(tag, "POKE")
        # 进退适度(用户「你看着对方兵多,你退呀,右键上去干啥」):对方战斗单位在身边比我方佯攻狗多
        # → 撤,别硬凑送死。不再只看血<35%(那时已在死了)。
        outnumbered = self._outnumbered(u, threats, lings)

        if state == "POKE" and (hp < self.bail_hp_ratio or outnumbered):
            state = "RETREAT"
            self._state[tag] = state
            logger.info(f"NYDUSFEINT retreat tag={tag} hp={hp:.2f} outnumbered={outnumbered}")
        elif state == "RETREAT" and hp >= self.recover_hp_ratio and not outnumbered:
            state = "POKE"
            self._state[tag] = state
            logger.info(f"NYDUSFEINT poke tag={tag} hp={hp:.2f}")

        if state == "RETREAT":
            with contextlib.suppress(Exception):
                # 撤退用 move 不用 attack_move（CLAUDE.md 控制权模型规则4：遇敌不恋战）。
                # 撤到"离目标锚点一小段(朝家方向)的固定点"就地待,兵少/血够再 POKE。
                rp = (
                    target.towards(self.ai.start_location, _RETREAT_BACK)
                    if target is not None
                    else self.ai.start_location
                )
                u.move(rp)
            return

        # POKE(安全,不被兵多)：离目标远 → 直推；到位 → hit-and-run 打工人（偷经济 + 引主力）。
        if target is None:
            return
        with contextlib.suppress(Exception):
            far = u.distance_to(target) > _ARRIVE_DIST
            if far:
                u.move(target)
                return
            # 到位后只扑农民(偷经济 + 引敌);没农民视野也别去 attack 战斗单位送死(outnumbered 已在上面
            # 拦截兵多的情况;这里若身边有散兵、打不打都行,优先扑农民,没农民就原地骚扰姿态推进矿线)。
            atk_target = self._worker_in_range(u, workers) or (
                workers.closest_to(u) if workers else None
            )
            if atk_target is not None:
                u.attack(atk_target)
            else:
                u.move(target)  # 没农民视野 → 保持逼近矿线的佯攻姿态(不 attack 散兵送死)

    def _outnumbered(self, u: Any, threats: list[Any], lings: dict[int, Any]) -> bool:
        """对方战斗单位在 _DANGER_RADIUS 内比我方佯攻狗多 → 视为"兵多"→ 该退。
        佯攻是诱饵不是主力,兵多就 hit-and-run 撤,绝不硬拼送死(用户 2026-07-12)。"""
        enemy_near = 0
        with contextlib.suppress(Exception):
            enemy_near = sum(1 for e in threats if u.distance_to(e) <= _DANGER_RADIUS)
        if enemy_near == 0:
            return False
        friends_near = 0
        with contextlib.suppress(Exception):
            friends_near = sum(
                1
                for t in self._tags
                if (o := lings.get(t)) is not None and u.distance_to(o) <= _DANGER_RADIUS
            )
        return enemy_near > friends_near

    def _drop_imminent(self) -> bool:
        """投送窗口是否临近:army 已装进坑道网络(nydus_wave_loaded) → 该去佯攻引主力开窗。
        未装好 → 前期,狗待命别送。读不到标志(mock/早期)默认 False(先待命,更保守)。"""
        with contextlib.suppress(Exception):
            vib = getattr(self.knowledge, "vibecraft", None)
            # 投送窗口 = 首波已装载,**或**虫被拆后正在等重投(2026-07-26 用户:第二次投放要配合
            # 佯攻吸引火力)。重投冷却期照样出去引主力,别让主力赖在家把第二个虫也秒了。
            return bool(
                getattr(vib, "nydus_wave_loaded", False)
                or getattr(vib, "nydus_retry_pending", False)
            )
        return False

    # ------------------------------------------------------------------
    # 目标锚点（一次锁定，CLAUDE.md 强规则）
    # ------------------------------------------------------------------

    def _get_target_anchor(self) -> Point2 | None:
        """佯攻目标(自适应,每 _TARGET_REEVAL_S 重评一次,不每帧漂移)：

        - **pull**(默认)：敌方主力**不在家** → 骚扰敌方二矿(natural)矿线，把主力引出去开 canal 窗口。
        - **punish**(2026-07-12 用户)：敌方主力**回家救场** → 二矿/外矿空了 → 佯攻狗**真去打最外面
          没防守的敌方基地**(四矿→三矿→二矿，由外及内取第一个已占且无防守的)吃经济。
        主力在不在家 = 敌方战斗单位在敌主基半径内数量 ≥ _MAIN_HOME_MIN。
        """
        now = -999.0
        with contextlib.suppress(Exception):
            now = float(self.ai.time)
        if self._target_anchor is not None and now - self._target_since < _TARGET_REEVAL_S:
            return self._target_anchor  # 缓存期内不重算(不漂移)

        chosen: Point2 | None = None
        with contextlib.suppress(Exception):
            zm = self.zone_manager
            start = getattr(zm, "enemy_start_location", None)
            if start is None:
                start = self.ai.enemy_start_locations[0]
            zones = sorted(zm.expansion_zones, key=lambda z: z.center_location.distance_to(start))
            main_c = zones[0].center_location if zones else start
            natural = (
                zones[1].mineral_line_center
                if len(zones) > 1
                else (zones[0].mineral_line_center if zones else start)
            )
            combat = self._threats()  # 敌方非农民非建筑机动单位
            near_main = sum(1 for e in combat if e.distance_to(main_c) <= _MAIN_HOME_RADIUS)
            if near_main >= _MAIN_HOME_MIN:
                # 主力回家救场 → 找最外面(离主基最远)已占且无防守的敌方基地真打
                punish = self._outermost_undefended_base(zones, combat, start)
                if punish is not None:
                    chosen = punish
                    if self._target_mode != "punish":
                        logger.info(
                            "NYDUSFEINT mode→punish: 主力回家,真打空虚外矿 @ (%.1f,%.1f)",
                            chosen.x,
                            chosen.y,
                        )
                    self._target_mode = "punish"
            if chosen is None:
                chosen = natural
                if self._target_mode != "pull":
                    logger.info(
                        "NYDUSFEINT mode→pull: 骚扰二矿引主力 @ (%.1f,%.1f)", chosen.x, chosen.y
                    )
                self._target_mode = "pull"

        if chosen is None:  # zone_manager 不可用兜底
            with contextlib.suppress(Exception):
                chosen = self.ai.enemy_start_locations[0]
        self._target_anchor = chosen
        self._target_since = now
        return chosen

    def _outermost_undefended_base(
        self, zones: list[Any], combat: list[Any], start: Any
    ) -> Point2 | None:
        """由外及内(离敌主基最远优先)找第一个【敌方已占(有敌 townhall)且无防守】的外矿矿线。

        已占 = 该 zone 附近有敌方 resource-center 结构(打真基地不打空地)；无防守 = 附近无敌战斗单位。
        没有任何已占无防守外矿 → None(退回 pull 引主力)。
        """
        enemy_ths: list[Any] = []
        with contextlib.suppress(Exception):
            enemy_ths = [s for s in self.ai.enemy_structures if s.type_id in _TOWNHALL_TYPES]
        # 外矿候选 = zones[1:](排除主基),由外及内(离 start 最远优先)
        for z in sorted(zones[1:], key=lambda z: -z.center_location.distance_to(start)):
            c = z.center_location
            occupied = any(th.distance_to(c) <= _BASE_DEFENDED_RADIUS for th in enemy_ths)
            if not occupied:
                continue
            defended = any(e.distance_to(c) <= _BASE_DEFENDED_RADIUS for e in combat)
            if defended:
                continue
            with contextlib.suppress(Exception):
                return z.mineral_line_center
        return None

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _hp_ratio(unit: Any) -> float:
        try:
            mx = float(unit.health_max) + float(unit.shield_max)
            if mx <= 0:
                return 1.0
            return (float(unit.health) + float(unit.shield)) / mx
        except Exception:
            return 1.0

    def _visible_enemy_workers(self) -> Any:
        try:
            return self.ai.enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES)
        except Exception:
            return None

    def _threats(self) -> list[Any]:
        with contextlib.suppress(Exception):
            return list(
                self.ai.enemy_units.filter(
                    lambda u: u.type_id not in _WORKER_TYPES and not u.is_structure
                )
            )
        return []

    @staticmethod
    def _worker_in_range(unit: Any, workers: Any) -> Any | None:
        if not workers:
            return None
        try:
            for w in workers:
                if unit.target_in_range(w):
                    return w
        except Exception:
            return None
        return None

    @staticmethod
    def _nearest_enemy(unit: Any, threats: list[Any]) -> Any | None:
        if not threats:
            return None
        try:
            return min(threats, key=lambda e: unit.distance_to(e))
        except Exception:
            return None
