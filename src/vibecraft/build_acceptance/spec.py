"""Acceptance spec 模型 + yaml loader。

spec 文件 tests/build_acceptance/<strategy_id>.yaml — 每个 build 一份,记录
deep research 出的标准 timing 节点。verifier 据此判定 telemetry。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


def parse_mmss(s: str) -> float:
    """'M:SS' → 秒。"""
    parts = str(s).split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 M:SS, got {s!r}")
    return int(parts[0]) * 60 + int(parts[1])


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[
        "building_started",
        "building_complete",
        "upgrade_complete",
        "worker_count",
        "unit_count",
        "building_count",
        "key_unit_at",
        "army_gather",
        "attack_moveout",
        # 骚扰 / 前压验收：
        #   pressure_contact (L2) — 主力到过敌方分矿,或与敌方主力接触过
        #   harass_damage    (L3) — 被我方打到过(受伤∪阵亡)的敌方农民 >=min
        #   scout_value           — 骚扰单位在对方基地内最长连续存活 >=min 秒
        #     (给单兵骚扰用：单个死神难稳定杀农民,但能在对方家里活着就有
        #      侦查 / 牵制价值)
        "pressure_contact",
        "harass_damage",
        "scout_value",
        # 玩家覆盖 e2e(Task #311):验证玩家手动按 retreat/attack/defend 按钮后
        # 单位真的服从,不是 UI 假动作。配合 AcceptanceSpec.player_actions 一起用。
        "army_after_player_action",
    ]
    # 时间:at(窗口中心)或 by(上界),至少一个
    at: str | None = None
    by: str | None = None
    tol: float = 20.0  # at 模式的 ±窗口秒
    # 目标参数(按 type 取用)
    unit: str | None = None
    upgrade: str | None = None
    min: int | None = None
    max: int | None = None  # 上界（building_count/unit_count: 窗口内最大值 <= max）
    near: str | None = None  # 命名锚点 home/enemy_main/natural
    within: float | None = None  # 距锚点容差
    # army_after_player_action 专用:
    #   action_idx — 关联到 spec.player_actions[action_idx] 这个玩家操作
    #   after_s    — 操作触发后等几秒查 army_center
    #   op         — 距离与 within 的比较运算(默认 "<="; 检验"撤回离敌方远"
    #                这种相反方向时用 ">")
    action_idx: int | None = None
    after_s: float | None = None
    op: Literal["<", "<=", ">", ">=", "==", "!="] = "<="

    @property
    def at_s(self) -> float | None:
        return parse_mmss(self.at) if self.at else None

    @property
    def by_s(self) -> float | None:
        return parse_mmss(self.by) if self.by else None

    @model_validator(mode="after")
    def _validate(self) -> Check:
        # scout_value 扫全局 telemetry 取「最后一次进对方基地」时刻,不需要 at/by。
        # army_after_player_action 时机由 player_actions[action_idx].at_s + after_s
        # 推导出来,也不需要 at/by。
        if (
            self.at is None
            and self.by is None
            and self.type not in ("scout_value", "army_after_player_action")
        ):
            raise ValueError(f"check {self.id}: 必须有 at 或 by")
        if self.type in ("army_gather", "key_unit_at") and (
            self.near is None or self.within is None
        ):
            raise ValueError(f"check {self.id}: {self.type} 必须有 near 和 within")
        if self.type == "pressure_contact" and self.within is None:
            raise ValueError(
                f"check {self.id}: pressure_contact 必须有 within"
                "（到敌方分矿 / 与敌方主力接触的判定距离）"
            )
        if self.type == "harass_damage" and self.min is None:
            raise ValueError(f"check {self.id}: harass_damage 必须有 min（被骚扰农民数门槛）")
        if self.type == "scout_value" and (
            self.unit is None or self.near is None or self.within is None or self.min is None
        ):
            raise ValueError(
                f"check {self.id}: scout_value 必须有 unit/near/within/min"
                "（min = 在对方基地内最长连续存活的秒数门槛）"
            )
        if self.type == "army_after_player_action" and (
            self.action_idx is None
            or self.after_s is None
            or self.near is None
            or self.within is None
        ):
            raise ValueError(
                f"check {self.id}: army_after_player_action 必须有 action_idx/after_s/near/within"
            )
        return self


class EconomyCheckpoint(BaseModel):
    """经济曲线一个关键时间点的标准值。

    一次 run 与标准值的偏差越小 → 经济执行越贴近预期。standard 值是
    **迭代改进**的：先填粗略估计，观察跑得好的 run 后再校准回写。

    每个 build 自己的标准曲线天然编码了它的意图——all-in 的标准就是
    "低农民"，照着跑偏差自然小，不需要额外区分 all-in / macro。
    """

    model_config = ConfigDict(extra="forbid")

    at: str  # "M:SS"
    workers: int | None = None  # 标准农民数
    minerals: int | None = None  # 标准余矿
    vespene: int | None = None  # 标准余气

    @property
    def at_s(self) -> float:
        return parse_mmss(self.at)

    @model_validator(mode="after")
    def _validate(self) -> EconomyCheckpoint:
        if self.workers is None and self.minerals is None and self.vespene is None:
            raise ValueError(
                f"economy checkpoint at {self.at}: workers/minerals/vespene 至少填一个"
            )
        return self


class PlayerAction(BaseModel):
    """玩家在游戏中按 UI 战术按钮触发的时间线项 (Task #311 player override e2e)。

    `verbs` 与 cockpit / Director 的 tactical verbs 对齐:attack / defend /
    retreat / vision。`mode` 仅 verb=attack 时有效(all_in 跳 power check,
    probe 兵足攻劣势撤)。
    """

    model_config = ConfigDict(extra="forbid")

    at: str  # M:SS — 子进程 game_time 到此触发
    verb: Literal["attack", "defend", "retreat", "vision"]
    mode: Literal["all_in", "probe"] | None = None
    target_area: str | None = None  # 命名锚点;None = facade 默认目标

    @property
    def at_s(self) -> float:
        return parse_mmss(self.at)


class AcceptanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    my_race: str
    checks: list[Check]
    # 经济曲线标准值（可选）——verifier 算偏差分，纯分数不做 pass/fail
    economy_profile: list[EconomyCheckpoint] = []
    # 玩家覆盖 e2e(Task #311):游戏中要 Director 自动触发的玩家操作时间线。
    # 子进程入口把 spec.player_actions 拷进 GameConfig.player_actions → 子进程
    # 设到 director._scheduled_player_actions → on_tick 到点 submit_directive
    # 模拟玩家按 UI 按钮。Verifier 用 army_after_player_action check 验位移。
    player_actions: list[PlayerAction] = []
    # Task #350: persistent_doctrine 验收用。opening_completed 后 N 秒自动
    # set_build 到此 doctrine（模拟玩家 PWA toast confirm）。N 由
    # auto_switch_delay_s 控制（默认 10s）。空串 = 不切（默认，普通 opening 验收）。
    # 典型用法:
    #   strategy_id: macro_hatch   # 起步 opening（zerg）
    #   auto_switch_to: persistent_lurker_hydra
    #   auto_switch_delay_s: 10.0
    auto_switch_to: str | None = Field(
        default=None,
        description=(
            "opening_completed 后 auto_switch_delay_s 秒自动 set_build 到这个"
            " doctrine（模拟玩家 PWA toast confirm）。用于测 persistent_doctrine"
            " kind 的 plan，build_acceptance 直接 force opening 不能跑到。"
        ),
    )
    auto_switch_delay_s: float = Field(
        default=10.0,
        description="opening_completed 后等多久切（秒，默认 10s）",
    )

    @model_validator(mode="after")
    def _validate(self) -> AcceptanceSpec:
        for check in self.checks:
            if check.type == "army_after_player_action":
                idx = check.action_idx
                assert idx is not None  # 已被 Check._validate 强制
                if idx < 0 or idx >= len(self.player_actions):
                    raise ValueError(
                        f"check {check.id}: action_idx={idx} 超出 "
                        f"player_actions 长度 {len(self.player_actions)}"
                    )
        return self


def load_spec(path: str | Path) -> AcceptanceSpec:
    """从 yaml 文件读 AcceptanceSpec。"""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AcceptanceSpec.model_validate(raw)
