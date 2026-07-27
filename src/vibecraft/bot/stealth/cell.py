"""偷矿 cell 状态容器（WP1 骨架）。

StealthState：PENDING → BUILDING → MINING → RELEASED / DESTROYED
StealthCell：每个偷矿点的运行时状态（不进 directive，常驻内存）。

point 存 tuple[float, float]（tile 坐标）：
  - 与 Director 内部其他坐标存储一致（_rally_point 等）
  - 单测无需 import sc2.position.Point2
  - 用到 sc2 API 时在调用侧 Point2(cell.point) 转换
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class StealthState(str, Enum):
    """偷矿 cell 状态机枚举。

    PENDING    - 玩家刚下指令，等待分配农民 + 开始建造。
    BUILDING   - 农民已出发/在建，等待 Nexus settle。
    MINING     - Nexus 已建好，本地产线 + FENCE 生效，正常采矿中。
    RELEASED   - 受攻击（on_attack=flee）→ 撤销 stealth 地位交还 bot；或玩家手动 × 解散。
    DESTROYED  - Nexus 被摧毁，残余农民释放后 cell 出局。
    """

    PENDING = "pending"
    BUILDING = "building"
    MINING = "mining"
    RELEASED = "released"
    DESTROYED = "destroyed"


@dataclass
class StealthCell:
    """偷矿经济单元的运行时状态容器（每个 cell 独立）。

    字段说明：
      cell_id         - 全局自增唯一 ID（由 StealthCellManager 分配，从 1 开始）
      point           - 玩家指定锚点，tile 坐标 (x, y)
      state           - 当前状态机状态（见 StealthState）
      nexus_tag       - settle 后回填；None = 尚未建好
      worker_tags     - 本 cell 自产 + 管辖的农民 tag 集合（Reserved role）
      gas_tags        - 本 cell ready assimilator tag 集合（WP4b）
      gas_worker_tags - worker_tags 里被分配去采气的农民子集（WP4b 防矿/气抖动）
      worker_target   - 目标农民数（采矿 + 采气共用总额；1 矿 ~16；来自 payload）
      with_gas        - 是否偷气（WP4b；默认 True；无 geyser 自动跳过）
      on_attack       - 受击行为（"flee"=撤销 stealth；"hold"=硬守，WP5 实现）
      builder_tag     - 代理建造 claim 的那个农民 tag；建完转本地（BUILDING 态使用）
      point_snapped   - 落点是否已吸附到最近 expansion（防每帧重复吸附；PENDING 首次置 True）
    """

    cell_id: int
    point: tuple[float, float]
    state: StealthState
    nexus_tag: int | None = None
    worker_tags: set[int] = field(default_factory=set)
    gas_tags: set[int] = field(default_factory=set)
    gas_worker_tags: set[int] = field(default_factory=set)
    worker_target: int = 16
    with_gas: bool = True
    on_attack: str = "flee"
    builder_tag: int | None = None
    point_snapped: bool = False
    # 动态总额（采矿 ideal + 采气 ideal），_tick_mining 每帧刷新；adopt_newborn 读它封顶
    # （含气矿名额，否则新孵化的采气农民超过 16 不被认领 → 被 DistributeWorkers 抢去主矿）。
    live_total_target: int = 16
    # WP4b 气矿建造：当前在建 assimilator 的 builder（in-flight 时不重复派工，防农民被反复
    # 抽去建同一个气矿 / 路上阵亡 → cell 长不起来）。gas_ready_baseline 记派工时的 ready 数，
    # ready 数增加 = assim 建好 → 释放 builder。
    gas_builder_tag: int | None = None
    gas_ready_baseline: int = 0
    # 偷气 builder gate 占用起始 game_time（2026-06-12）：gate 改用超时释放，不再因 builder
    # 单帧 cache-miss（采气农民钻进 assim）就释放 → 防止反复重派同一 geyser（真机 231 次 churn、
    # 建造令大量 cache-miss 丢弃 → assim 建不成、气矿 0-1 个、cell 到不了 22）。
    gas_builder_since: float = 0.0
    # 死亡判定 grace（2026-06-11）：采气农民会周期性"钻进"assimilator 暂时从 bot.units
    # 消失（SC2 机制），1 帧就删会把采气农民当死亡误删 → gas_worker_tags 永远清零、采气
    # 补不满、矿口反被超采（真机峰值卡在 16 矿超采+0 气 = 19，到不了 16+6=22）。
    # 记每个"当前消失"农民的首次消失 game_time，连续消失超过 grace 才真判死。tag 重现即清。
    worker_missing_since: dict[int, float] = field(default_factory=dict)
    # 矿优先+跟随主经济气门（2026-06-13）：开闸后置 True，用于"首次开闸"日志去重（只记一次）。
    # 闸开后不会再关（矿工数不会倒退），所以一个 bool 足够。
    gas_gate_opened: bool = False

    def alive_workers(self, is_alive: Callable[[int], bool]) -> set[int]:
        """返回当前仍存活的农民 tag 集合。

        参数：
          is_alive: 判断某 unit tag 是否仍存活的函数（避免直接依赖 sc2 bot 对象，
                    方便单测 mock）。

        用法示例（真机）：
          alive = cell.alive_workers(lambda tag: tag in bot.units.tags)
        用法示例（单测）：
          alive = cell.alive_workers(lambda tag: tag in {10, 20})

        WP2+ 的产线补员和受击逻辑通过此 helper 拿到存活集合，不直接 iterate worker_tags。
        """
        return {tag for tag in self.worker_tags if is_alive(tag)}
