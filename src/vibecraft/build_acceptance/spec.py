"""Acceptance spec 模型 + yaml loader。

spec 文件 tests/build_acceptance/<strategy_id>.yaml — 每个 build 一份,记录
deep research 出的标准 timing 节点。verifier 据此判定 telemetry。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


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
        "building_started", "building_complete", "upgrade_complete",
        "worker_count", "unit_count", "building_count", "key_unit_at", "army_gather", "attack_moveout",
    ]
    # 时间:at(窗口中心)或 by(上界),至少一个
    at: str | None = None
    by: str | None = None
    tol: float = 20.0  # at 模式的 ±窗口秒
    # 目标参数(按 type 取用)
    unit: str | None = None
    upgrade: str | None = None
    min: int | None = None
    near: str | None = None       # 命名锚点 home/enemy_main/natural
    within: float | None = None   # 距锚点容差

    @property
    def at_s(self) -> float | None:
        return parse_mmss(self.at) if self.at else None

    @property
    def by_s(self) -> float | None:
        return parse_mmss(self.by) if self.by else None

    @model_validator(mode="after")
    def _need_time(self) -> Check:
        if self.at is None and self.by is None:
            raise ValueError(f"check {self.id}: 必须有 at 或 by")
        return self


class AcceptanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    my_race: str
    checks: list[Check]


def load_spec(path: str | Path) -> AcceptanceSpec:
    """从 yaml 文件读 AcceptanceSpec。"""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AcceptanceSpec.model_validate(raw)
