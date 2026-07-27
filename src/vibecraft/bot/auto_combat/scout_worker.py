"""通用探路农民:不论什么宏观策略,都派 1 农民去对面侦察。

策略:
- 保命优先:HP+shield < 50% 起始 → 撤回最近友方基地;> 90% 重新出发
- 巡逻目标:enemy_start_locations[0] + 2-3 个靠近敌方的 expansion
- 每 30s 切下一个目标(避免卡死)
- 死了重派下一个农民(只要还有农民)
- 必要时牺牲:撤退路上无法绕开 → 也接受被打死(SC2 引擎自动 attack-move 中会优先逃,死了就死了)

实现层级:protoss/bot.py + terran/bot.py 的 create_plan() 把 ScoutWorker() 放在
IfElse 路由之外,所有 active_recipe 共享这一个探路农民。act 是 race-agnostic 的
(按 self.ai.workers 取农民,Probe/SCV/Drone 通吃),三族 bot 复用同一份。
Reserved task 不会被 DistributeWorkers 拉回采矿。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


class ScoutWorker(ActBase):  # type: ignore[misc]
    """1 农民巡逻探路,保命 + 必要时牺牲。

    **中后期自动停用**：5 分钟（300s 游戏内）后中后期敌方兵力密集，
    单农民进去十死无生 + 送掉农民经济上也亏。中后期切到火力侦察（recon
    L2 directive，玩家显式发或 bot 内部 recon 触发）。
    """

    # HP+shield 占比阈值
    RETREAT_RATIO: float = 0.5
    REENGAGE_RATIO: float = 0.9
    # 切下一个巡逻目标的间隔(秒,游戏内)
    TARGET_SWITCH_INTERVAL: float = 30.0
    # 中后期停用阈值（游戏内秒）：5 分钟后敌方兵力密集，单农民送菜
    MIDGAME_CUTOFF_S: float = 300.0

    def __init__(self) -> None:
        super().__init__()
        self.scout_tag: int | None = None
        self.targets: list[Point2] = []
        self.target_idx: int = 0
        self.last_switch_s: float = -999.0
        self.retreating: bool = False
        # Task #352: 玩家"让探路农民回来" → Director 调 cancel() 设此 flag → execute 直接 return True。
        self.cancelled: bool = False

    def cancel(self) -> None:
        """玩家显式撤回探路农民。

        调用方：Director._apply_unit_release 检测到 selector 是 worker 类型时调用。
        设 cancelled=True + 清 scout_tag → 下一个 tick execute() 直接 return True（永久结束）。
        当前 scout 已在 _apply_unit_release 里被 facade.set_unit_role(IDLE) 处理，
        sharpy 下一轮接管让它自动回家采矿。
        """
        self.cancelled = True
        self.scout_tag = None
        logger.info("ScoutWorker: cancelled by player directive")

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        try:
            enemy_start = self.ai.enemy_start_locations[0]
            self.targets = [enemy_start]
            # 取 6 个 expansion 候选,选离敌方 5-40 距离内的(偏敌方半场)
            for exp in self.ai.expansion_locations_list[:8]:
                d = exp.distance_to(enemy_start)
                if 5 < d < 40:
                    self.targets.append(exp)
            logger.info("ScoutWorker initialized: %d targets", len(self.targets))
        except Exception as exc:
            logger.warning("ScoutWorker init failed: %s", exc)
            self.targets = []

    def _pick_scout(self) -> Any:
        """选 1 个农民:优先离敌方近的(可能本身就在采远矿)。返回 sc2 Unit 或 None。

        **排除偷矿农民 + 玩家 claim 的农民**（2026-06-11 玩家实测：偷矿农民离敌近被抓去探路）：
        偷矿农民在 `_llm_controlled_tags`（每帧 ensure Reserved）+ `stealth_worker_tags`，
        不能被抓去探路——它们只采偷矿基地的矿。
        """
        if not self.ai.workers:
            return None
        excluded: set[int] = set(getattr(self.ai, "_llm_controlled_tags", set()))
        _vc = getattr(self.knowledge, "vibecraft", None)
        excluded |= set(getattr(_vc, "stealth_worker_tags", set()) or set())
        # 排除 proxy 偷家建造农民（2026-07-07 玩家实测：proxy 农民朝敌方走 = 离敌最近，被抓去
        # 探路 → 反复拉扯到不了 proxy 点、建不了兵营，还把顺序建造门卡死）。
        excluded |= set(getattr(_vc, "proxy_builder_tags", set()) or set())
        candidates = self.ai.workers.filter(lambda w: w.tag not in excluded)
        if not candidates:
            return None  # 只剩偷矿/被 claim 农民 → 不抓去探路
        try:
            enemy_start = self.ai.enemy_start_locations[0]
            return candidates.closest_to(enemy_start)
        except Exception:
            return candidates.first

    def _scout_claimed_by_player(self) -> bool:
        """当前 scout_tag 是否已被玩家指令 claim（在 _llm_controlled_tags 或偷矿 tag 里）。

        与 `_pick_scout` 用同一套排除集合：_llm_controlled_tags（每帧 re-Reserve 的玩家控制单位）
        ∪ stealth_worker_tags（偷矿农民）。用于已持有的 scout 农民被 claim 后主动放手。
        """
        if self.scout_tag is None:
            return False
        excluded: set[int] = set(getattr(self.ai, "_llm_controlled_tags", set()))
        _vc = getattr(self.knowledge, "vibecraft", None)
        excluded |= set(getattr(_vc, "stealth_worker_tags", set()) or set())
        # 排除 proxy 偷家建造农民（2026-07-07 玩家实测：proxy 农民朝敌方走 = 离敌最近，被抓去
        # 探路 → 反复拉扯到不了 proxy 点、建不了兵营，还把顺序建造门卡死）。
        excluded |= set(getattr(_vc, "proxy_builder_tags", set()) or set())
        return self.scout_tag in excluded

    async def execute(self) -> bool:
        # Task #352: 玩家显式撤回 → 永久结束此 act（不再派新 scout）。
        if self.cancelled:
            return True
        if not self.targets:
            return True
        # 开局 60s 内不抢农民:开局 13-14 农 + BG/PYLON build 阶段对 worker 位置敏感,
        # ScoutWorker 抢 1 个 closest_to(enemy) 的农民可能干扰 sharpy BuildingSolver
        # 的 builder 选择(实测 bug:开局一直 "Can't find free position to build PYLON")
        if self.ai.time < 60.0:
            return False
        # **中后期停用**：5 分钟后敌方兵力密集，单农民进去送菜。
        # 此时切到火力侦察（recon L2 directive，玩家显式发或 bot 自决策走 recon）。
        # 若 scout 还在外面，让它回家；新一波不再派。
        if self.ai.time > self.MIDGAME_CUTOFF_S:
            if self.scout_tag is not None:
                # 让现有 scout 回家，然后释放
                try:
                    from sharpy.managers.core.roles import UnitTask

                    scout = self.cache.by_tag(self.scout_tag)
                    if scout is not None:
                        if self.ai.townhalls:
                            home = self.ai.townhalls.first.position
                            if scout.distance_to(home) > 8:
                                scout.move(home)
                                return False  # 等到家了再释放
                        # 到家了，释放 worker 角色让 sharpy 拉回去采矿
                        self.knowledge.roles.clear_task(scout)
                        self.knowledge.roles.set_task(UnitTask.Idle, scout)
                        logger.info(
                            "ScoutWorker midgame cutoff (%.0fs): released scout tag=%d",
                            self.ai.time,
                            self.scout_tag,
                        )
                except Exception as exc:
                    logger.debug("ScoutWorker midgame release fail: %s", exc)
                self.scout_tag = None
            return True  # 任务完成，act 永久结束
        # 4bg 策略下让位给 ForwardSupportPylonGateway(它承担"探路+保命+躲起来修建筑")
        # 不同时派两个农民出去
        if getattr(self.ai, "active_recipe", None) == "4bg" and self.scout_tag is None:
            return False
        # **已持有的 scout 农民被玩家指令 claim(代理建造/待命/偷矿)→ 立即放手,永不再指挥它。**
        # _pick_scout 的排除(_llm_controlled_tags / stealth)只在"重新挑农民"时生效;一旦农民
        # 已存进 self.scout_tag,后续每帧靠 by_tag 直接拿、不再过 _pick_scout → 排除不到它。
        # 若此时玩家把这个农民 claim 去代理建造/待命,本 act 仍每帧 move 它去敌方,跟玩家的
        # build/standby 抢控制权,建完就被拖去敌方基地阵亡(2026-06-14 真局:野水晶农民建完被拉去探路)。
        # 放手后下一 tick _pick_scout(已排除该 tag)会另挑一个自由农民继续探路。
        if self.scout_tag is not None and self._scout_claimed_by_player():
            logger.info("ScoutWorker: scout tag=%d 被玩家 claim → 放手", self.scout_tag)
            self.scout_tag = None
            self.retreating = False
            return False
        try:
            from sharpy.managers.core.roles import UnitTask

            # 拿 / 重派 scout
            scout = self.cache.by_tag(self.scout_tag) if self.scout_tag is not None else None
            if scout is None:
                new = self._pick_scout()
                if new is None:
                    return False  # 没农民可派
                self.scout_tag = new.tag
                scout = new
                self.retreating = False
                logger.info("ScoutWorker assigned tag=%d", new.tag)

            self.knowledge.roles.set_task(UnitTask.Reserved, scout)

            # HP 评估
            hp_max = scout.shield_max + scout.health_max
            hp_now = scout.shield + scout.health
            ratio = hp_now / hp_max if hp_max > 0 else 1.0

            if not self.retreating and ratio < self.RETREAT_RATIO:
                self.retreating = True
                logger.debug("ScoutWorker retreating (hp ratio=%.2f)", ratio)
            elif self.retreating and ratio > self.REENGAGE_RATIO:
                self.retreating = False
                logger.debug("ScoutWorker re-engaging (hp ratio=%.2f)", ratio)

            if self.retreating:
                if self.ai.townhalls:
                    home = self.ai.townhalls.first.position
                    if scout.distance_to(home) > 8:
                        scout.move(home)
                return False

            # 巡逻:每 30s 换下个目标
            now = float(self.ai.time)
            if now - self.last_switch_s > self.TARGET_SWITCH_INTERVAL:
                self.target_idx = (self.target_idx + 1) % len(self.targets)
                self.last_switch_s = now
            target = self.targets[self.target_idx]
            if scout.is_idle or scout.distance_to(target) > 5:
                scout.move(target)
        except Exception as exc:
            logger.warning("ScoutWorker execute failed: %s", exc)
        return False
