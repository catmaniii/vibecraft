# L2 Executor + L4 done_when 扩词表 + 命令卡片 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修 L2 `tactical_objective` 死路 + 扩 L4 done_when 词表 + 统一命令卡片 revoke 协议，让玩家"进攻自然/守家/补 8 BG/凤凰骚扰"等指令真正影响 bot 行为 + UI 可视 + 可取消。

**Architecture:** 候选 4 hybrid（design doc §3）—— A 类全军指令走 override flag（facade 新增 2 方法 + sharpy plan fork），B 类 squad 指令走 unit role 抢占（复用现有 `LLM_CONTROLLED` + sharpy `GroupCombatManager`）。L4 done_when 扩 7 个新 kind + 新增 `structure_override` directive type。snapshot 统一新增 `command_cards` array + WS revoke 帧已有，扩到 L2/L1。

**Tech Stack:** Python 3.13 / pydantic v2 / pytest / sharpy-sc2 (vendored) / Vue 3 + Tailwind (PWA)。所有 unit test 走 `FakeFacade` mock，不依赖 SC2 客户端。

**Design 文档**：`docs/plans/2026-05-17-l2-l4-executor-and-cards-design.md`（决策 / schema / 代码骨架真理源）

**实施顺序**：P0a → P0d → P0c → P0e → P0f → P0g → P0b → P0h → P0i → P0j → P0k（每段独立可 ship）

---

## Task 0：拉新分支 + sanity check

**Files:** none

**Step 1:** 起新分支
```bash
git checkout -b m4-l2-l4-executor
```

**Step 2:** 跑全部单测，确认 baseline 干净
```bash
uv run pytest -q
```
Expected: all pass（如有 fail 先 fix，否则后续无法判断回归）

**Step 3:** 跑 lint + type check
```bash
uv run ruff check .
uv run mypy src/vibecraft
```
Expected: 0 error

**Step 4:** 验证 design doc 在
```bash
ls docs/plans/2026-05-17-l2-l4-executor-and-cards-design.md
```

**Step 5:** 标 ADR 占位
```bash
touch docs/adr/0011-l2-tactical-executor.md
git add docs/adr/0011-l2-tactical-executor.md
git commit -m "chore(adr): placeholder 0011 L2 tactical executor (impl 跟进)"
```

---

## P0a：facade 加 2 override flag + 实施 set_engagement_stance

涉及文件：
- `src/vibecraft/bot/facade.py:69-141` —— `Sc2Facade` Protocol（加 2 方法）
- `src/vibecraft/bot/facade.py:155-265` —— `FakeFacade`（加 2 方法 + 状态字段）
- `src/vibecraft/bot/auto_combat/protoss/bot.py:385-387` —— `set_engagement_stance` M1 noop 改实现
- `src/vibecraft/bot/auto_combat/protoss/bot.py:??` —— `set_attack_target_override` / `set_combat_intent_override` 新实现
- `src/vibecraft/bot/auto_combat/protoss/plans/vibecraft_zone_attack.py` —— **新文件**，fork PlanZoneAttack

### Task 1: facade Protocol + FakeFacade 加 2 方法

**Files:**
- Modify: `src/vibecraft/bot/facade.py:111-119` （`# ---- 写：建造位置 / engagement -----------------------------------` 块）
- Modify: `src/vibecraft/bot/facade.py:155-265` （FakeFacade）
- Test: `tests/unit/test_facade.py`（新文件 or 现有）

**Step 1: 写 failing test**

```python
# tests/unit/test_facade.py
from vibecraft.bot.facade import FakeFacade

def test_fake_facade_records_attack_target_override():
    f = FakeFacade()
    f.set_attack_target_override((42.0, 100.0))
    assert f.attack_target_overrides == [(42.0, 100.0)]
    f.set_attack_target_override(None)
    assert f.attack_target_overrides == [(42.0, 100.0), None]

def test_fake_facade_records_combat_intent_override():
    f = FakeFacade()
    f.set_combat_intent_override("attack")
    f.set_combat_intent_override("defend")
    f.set_combat_intent_override(None)
    assert f.combat_intent_overrides == ["attack", "defend", None]
```

**Step 2: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_facade.py -v
```
Expected: FAIL — `AttributeError: 'FakeFacade' object has no attribute 'set_attack_target_override'`

**Step 3: 实现**

修 `src/vibecraft/bot/facade.py` Protocol（在 `set_engagement_stance` 旁边加）：

```python
# 行 119 后插入：
def set_attack_target_override(
    self, point: tuple[float, float] | None
) -> None:
    """L2 全军 attack target 覆盖（None = 清覆盖，恢复 sharpy 默认决策）。"""
    ...

def set_combat_intent_override(
    self,
    intent: Literal["attack", "defend", "hold", "retreat", "vision"] | None,
) -> None:
    """L2 全军交战意图覆盖（None = 清覆盖）。
    set_engagement_stance 的同源接口；stance 内部转发到此。"""
    ...
```

FakeFacade（行 162 附近 `__init__`）：
```python
# 在 __init__ 加：
self.attack_target_overrides: list[tuple[float, float] | None] = []
self.combat_intent_overrides: list[str | None] = []
```

加方法（行 233 附近 `set_engagement_stance` 后）：
```python
def set_attack_target_override(
    self, point: tuple[float, float] | None
) -> None:
    self.attack_target_overrides.append(point)
    self._record("set_attack_target_override", point)

def set_combat_intent_override(self, intent: str | None) -> None:
    self.combat_intent_overrides.append(intent)
    self._record("set_combat_intent_override", intent)
```

顶部 import 补 `Literal`：
```python
from typing import Literal, Protocol
```

**Step 4: 跑测确认 PASS**
```bash
uv run pytest tests/unit/test_facade.py -v
```
Expected: 2 passed

**Step 5: Commit**
```bash
git add src/vibecraft/bot/facade.py tests/unit/test_facade.py
git commit -m "feat(facade): 加 set_attack_target_override / set_combat_intent_override (L2 P0a)"
```

### Task 2: SharpyFacade 实施 3 个方法（noop 改实现）

**Files:**
- Modify: `src/vibecraft/bot/auto_combat/protoss/bot.py:385-387`（`set_engagement_stance` noop）
- Modify: `src/vibecraft/bot/auto_combat/protoss/bot.py:??`（同 facade 实现块尾，加 2 new methods）
- Test: 暂无（无法 unit test 真 sharpy；逻辑很薄，靠 plan fork 的测试间接覆盖）

**Step 1: 摸现有 facade impl 块**
```bash
uv run python -c "
import inspect
from vibecraft.bot.auto_combat.protoss import bot
src = inspect.getsource(bot)
for i, line in enumerate(src.split('\n'), 1):
    if 'def set_' in line or 'M1 noop' in line:
        print(i, line)
"
```
读出 `set_engagement_stance` 等具体所在行，确认要替换的 noop 块边界。

**Step 2: 写 contract test（薄）**

```python
# tests/unit/test_protoss_facade_overrides.py
"""验证 protoss SharpyFacade 实现的 3 个新接口写状态到 knowledge.vibecraft。
真 sharpy 不启动，knowledge 用 SimpleNamespace mock。"""

from types import SimpleNamespace
from vibecraft.bot.auto_combat.protoss.bot import _make_facade  # 假设有工厂；没的话直接 import facade 类

def test_set_attack_target_override_writes_knowledge():
    fake_bot = SimpleNamespace(knowledge=SimpleNamespace(vibecraft=SimpleNamespace()))
    facade = _make_facade(fake_bot)
    facade.set_attack_target_override((10.0, 20.0))
    assert fake_bot.knowledge.vibecraft.attack_target_override == (10.0, 20.0)
    facade.set_attack_target_override(None)
    assert fake_bot.knowledge.vibecraft.attack_target_override is None

def test_set_combat_intent_override_writes_knowledge():
    fake_bot = SimpleNamespace(knowledge=SimpleNamespace(vibecraft=SimpleNamespace()))
    facade = _make_facade(fake_bot)
    facade.set_combat_intent_override("attack")
    assert fake_bot.knowledge.vibecraft.combat_intent_override == "attack"

def test_set_engagement_stance_delegates_to_combat_intent():
    """stance defend → intent defend；free → intent None。"""
    fake_bot = SimpleNamespace(knowledge=SimpleNamespace(vibecraft=SimpleNamespace()))
    facade = _make_facade(fake_bot)
    facade.set_engagement_stance("defend")
    assert fake_bot.knowledge.vibecraft.combat_intent_override == "defend"
    facade.set_engagement_stance("free")
    assert fake_bot.knowledge.vibecraft.combat_intent_override is None
```

**Step 3: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_protoss_facade_overrides.py -v
```
Expected: 3 FAIL（_make_facade 不存在 或 实现是 noop）

**Step 4: 实现**

在 protoss/bot.py 找到 facade 实现块（`set_engagement_stance` 附近 ~385），改成：

```python
def set_engagement_stance(self, stance: str) -> None:
    # 4 个 stance 收敛到 combat_intent_override
    if stance == "free":
        self.set_combat_intent_override(None)
    elif stance in ("defend", "hold", "retreat"):
        self.set_combat_intent_override(stance)
    else:
        logger.warning("unknown stance %r, no-op", stance)

def set_attack_target_override(
    self, point: tuple[float, float] | None
) -> None:
    """L2 attack target 覆盖；写到 knowledge.vibecraft 给 VibeCraftZoneAttack 读。"""
    self._bot.knowledge.vibecraft.attack_target_override = point

def set_combat_intent_override(self, intent: str | None) -> None:
    self._bot.knowledge.vibecraft.combat_intent_override = intent
```

并在 bot 启动时（`on_start` 或 knowledge init 处）初始化 `knowledge.vibecraft` namespace：

```python
# 找到 on_start 或类 __init__：
from types import SimpleNamespace
self.knowledge.vibecraft = SimpleNamespace(
    attack_target_override=None,
    combat_intent_override=None,
)
```

可能需要把 facade 实现抽到 `_make_facade(bot)` 工厂便于 unit test。如果重构成本高，则改测试用 `monkeypatch` 直接 patch bot 实例。

**Step 5: 跑测 + commit**
```bash
uv run pytest tests/unit/test_protoss_facade_overrides.py -v
```
Expected: 3 passed
```bash
git add src/vibecraft/bot/auto_combat/protoss/bot.py tests/unit/test_protoss_facade_overrides.py
git commit -m "feat(sharpy_facade): 实施 set_engagement_stance + 2 override (P0a Task 2)"
```

### Task 3: VibeCraftZoneAttack plan fork

**Files:**
- Create: `src/vibecraft/bot/auto_combat/protoss/plans/vibecraft_zone_attack.py`
- Test: `tests/unit/test_vibecraft_zone_attack.py`（新文件）

**Step 1: 写 failing test**

```python
# tests/unit/test_vibecraft_zone_attack.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import (
    VibeCraftZoneAttack,
)

def _make_plan(override_target=None, override_intent=None):
    plan = VibeCraftZoneAttack.__new__(VibeCraftZoneAttack)
    plan.knowledge = SimpleNamespace(
        vibecraft=SimpleNamespace(
            attack_target_override=override_target,
            combat_intent_override=override_intent,
        )
    )
    return plan

def test_get_target_uses_override_when_set(monkeypatch):
    plan = _make_plan(override_target=(50.0, 100.0))
    # parent _get_target 返回 sentinel；override 应该胜出
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._get_target",
        lambda self: ("SENTINEL_NATURAL",),
    )
    result = plan._get_target()
    assert result == (50.0, 100.0)

def test_get_target_falls_back_to_parent_when_no_override(monkeypatch):
    plan = _make_plan(override_target=None)
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._get_target",
        lambda self: "DEFAULT",
    )
    assert plan._get_target() == "DEFAULT"

def test_should_attack_intent_attack_returns_true():
    plan = _make_plan(override_intent="attack")
    assert plan._should_attack() is True

def test_should_attack_intent_defend_returns_false():
    plan = _make_plan(override_intent="defend")
    assert plan._should_attack() is False

def test_should_attack_no_intent_falls_back(monkeypatch):
    plan = _make_plan(override_intent=None)
    monkeypatch.setattr(
        "vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack."
        "PlanZoneAttack._should_attack",
        lambda self: "DEFAULT",
    )
    assert plan._should_attack() == "DEFAULT"
```

**Step 2: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_vibecraft_zone_attack.py -v
```
Expected: 5 FAIL — `ModuleNotFoundError: vibecraft_zone_attack`

**Step 3: 实现**

```python
# src/vibecraft/bot/auto_combat/protoss/plans/vibecraft_zone_attack.py
"""sharpy PlanZoneAttack 的 vibecraft 子类：优先读 knowledge.vibecraft 的
attack_target_override / combat_intent_override，无覆盖时走 sharpy 默认。"""

from __future__ import annotations

from sharpy.plans.tactics import PlanZoneAttack
from sc2.position import Point2


class VibeCraftZoneAttack(PlanZoneAttack):
    """覆盖 sharpy 默认 attack target / should_attack 决策。

    L2 attack/defend/hold/retreat/vision 指令都通过这里生效；
    清 override 后自动回到 sharpy 默认。
    """

    def _get_target(self):
        override = getattr(self.knowledge.vibecraft, "attack_target_override", None)
        if override is not None:
            # 兼容 tuple (x, y) 输入
            if isinstance(override, tuple) and len(override) == 2:
                return Point2(override)
            return override
        return super()._get_target()

    def _should_attack(self):
        intent = getattr(self.knowledge.vibecraft, "combat_intent_override", None)
        if intent == "attack":
            return True
        if intent in ("defend", "hold", "retreat", "vision"):
            return False
        return super()._should_attack()
```

**Step 4: 跑测确认 PASS**
```bash
uv run pytest tests/unit/test_vibecraft_zone_attack.py -v
```
Expected: 5 passed

**Step 5: Commit**
```bash
git add src/vibecraft/bot/auto_combat/protoss/plans/vibecraft_zone_attack.py tests/unit/test_vibecraft_zone_attack.py
git commit -m "feat(plans): VibeCraftZoneAttack fork 优先 override flag (P0a Task 3)"
```

### Task 4: 6 个 plan 文件换 PlanZoneAttack → VibeCraftZoneAttack

**Files:**
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/1g_robo_immortal.py`
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/iac_2base.py`
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/sustain.py`
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/skytoss.py`
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/forward_proxy.py`
- Modify: `src/vibecraft/bot/auto_combat/protoss/plans/gate4_pressure.py`

**Step 1: grep 找出所有 PlanZoneAttack 用法**
```bash
uv run python -c "
import subprocess, sys
out = subprocess.run(['grep', '-rn', 'PlanZoneAttack', 'src/vibecraft/bot/auto_combat/protoss/plans/'], capture_output=True, text=True)
print(out.stdout)
"
```
（或用 `Grep` 工具 pattern=`PlanZoneAttack` path=`src/vibecraft/bot/auto_combat/protoss/plans`）

**Step 2: 决定替换策略**

如果 6 个 plan 直接 `from sharpy.plans.tactics import PlanZoneAttack` 然后用：

```python
PlanZoneAttack(zone_index)  # 老
↓
VibeCraftZoneAttack(zone_index)  # 新
```

如果有 plan 用了 PlanZoneAttack 的子类（例如 `sharpy.plans.protoss.protoss_zone_attack.ProtossZoneAttack`）需要另开 ProtossVibeCraftZoneAttack 子类。**先用 Read 看每个 plan 实际用法**再做调整。

**Step 3: 写 smoke test（不启 SC2）**

```python
# tests/unit/test_plans_use_vibecraft_zone_attack.py
"""保证 6 个 plan import 时实例化的是 VibeCraftZoneAttack 而非 PlanZoneAttack。"""

import pytest

@pytest.mark.parametrize("plan_module", [
    "vibecraft.bot.auto_combat.protoss.plans.iac_2base",
    "vibecraft.bot.auto_combat.protoss.plans.sustain",
    "vibecraft.bot.auto_combat.protoss.plans.skytoss",
    # ... 6 全列
])
def test_plan_module_imports_vibecraft_zone_attack(plan_module):
    import importlib
    mod = importlib.import_module(plan_module)
    src = open(mod.__file__).read()
    assert "VibeCraftZoneAttack" in src, f"{plan_module} 仍用 PlanZoneAttack"
    # 也允许 wrapper 类（ProtossVibeCraftZoneAttack 等）；只要不是裸 PlanZoneAttack
    assert "PlanZoneAttack(" not in src or "VibeCraftZoneAttack(" in src
```

**Step 4: 改 6 个 plan + 跑测**

每个 plan：
1. import 替换：`from sharpy.plans.tactics import PlanZoneAttack` → `from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack`
2. 实例化替换：`PlanZoneAttack(...)` → `VibeCraftZoneAttack(...)`

```bash
uv run pytest tests/unit/test_plans_use_vibecraft_zone_attack.py -v
uv run pytest -q   # full regression
```
Expected: all pass

**Step 5: Commit**
```bash
git add src/vibecraft/bot/auto_combat/protoss/plans/*.py tests/unit/test_plans_use_vibecraft_zone_attack.py
git commit -m "feat(plans): 6 plan 切 VibeCraftZoneAttack (P0a Task 4)"
```

---

## P0d：L4 done_when 扩 7 个 kind

涉及文件：
- `src/vibecraft/directives/models.py:23-107` —— DoneWhen union 加 7 kind
- `src/vibecraft/bot/task_monitor.py:34-` —— DONE_CHECKERS 注册 7 checker
- LLM prompt 在 P0i 改

### Task 5: 7 new DoneWhen 模型 + 加入 union

**Files:**
- Modify: `src/vibecraft/directives/models.py:23-111` （DoneWhen union 段）
- Test: `tests/unit/test_done_when_models.py`（新文件）

**Step 1: 写 failing test**

```python
# tests/unit/test_done_when_models.py
import pytest
from pydantic import TypeAdapter
from vibecraft.directives.models import DoneWhen

ADAPTER = TypeAdapter(DoneWhen)

@pytest.mark.parametrize("payload", [
    {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8},
    {"kind": "own_unit_count", "unit_type": "Immortal", "op": ">=", "value": 6},
    {"kind": "supply_used", "op": ">=", "value": 70},
    {"kind": "supply_cap", "op": ">=", "value": 200},
    {"kind": "minerals", "op": ">=", "value": 1000},
    {"kind": "gas", "op": ">=", "value": 200},
    {"kind": "worker_count", "op": ">=", "value": 50},
])
def test_new_done_when_kinds_validate(payload):
    obj = ADAPTER.validate_python(payload)
    assert obj.kind == payload["kind"]

def test_invalid_op_rejected():
    with pytest.raises(Exception):
        ADAPTER.validate_python({"kind": "structure_count", "structure_type": "Gateway", "op": "!!", "value": 8})
```

**Step 2: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_done_when_models.py -v
```
Expected: 7 FAIL — validator 不识别新 kind

**Step 3: 实现**

在 `src/vibecraft/directives/models.py` line 88 附近（`TimeElapsedSince` 后、`AnyOf` 前）插入：

```python
# ---------------------------------------------------------------------------
# P0d L4 done_when 扩词表（运营类指令）
# ---------------------------------------------------------------------------

_OP = Literal[">=", "<=", "==", ">", "<"]


class StructureCount(BaseModel):
    """当前建筑存量（含 pending）。区别于 unit_count_built_since（增量）。"""
    kind: Literal["structure_count"]
    structure_type: str
    op: _OP
    value: int


class OwnUnitCount(BaseModel):
    """己方某兵种当前存量（含 pending）。"""
    kind: Literal["own_unit_count"]
    unit_type: str
    op: _OP
    value: int


class SupplyUsed(BaseModel):
    """当前人口已用。"""
    kind: Literal["supply_used"]
    op: _OP
    value: int


class SupplyCap(BaseModel):
    """当前人口上限。"""
    kind: Literal["supply_cap"]
    op: _OP
    value: int


class Minerals(BaseModel):
    """当前晶矿。"""
    kind: Literal["minerals"]
    op: _OP
    value: int


class Gas(BaseModel):
    """当前瓦斯。"""
    kind: Literal["gas"]
    op: _OP
    value: int


class WorkerCount(BaseModel):
    """当前工人数。"""
    kind: Literal["worker_count"]
    op: _OP
    value: int
```

修 `DoneWhen` 联合（line 104）：

```python
DoneWhen = Annotated[
    UnitCountBuiltSince | TechDone | ExpansionCount | TargetDestroyed
    | OwnArmySizeRatio | VisionAcquired | EnemyKilledInArea | TimeElapsedSince
    | StructureCount | OwnUnitCount | SupplyUsed | SupplyCap
    | Minerals | Gas | WorkerCount
    | AnyOf | AllOf,
    Field(discriminator="kind"),
]
```

**Step 4: 跑测**
```bash
uv run pytest tests/unit/test_done_when_models.py -v
uv run pytest tests/unit/test_models.py -q   # 防回归
```
Expected: pass

**Step 5: Commit**
```bash
git add src/vibecraft/directives/models.py tests/unit/test_done_when_models.py
git commit -m "feat(done_when): 加 7 个新 kind (structure_count/own_unit_count/supply/资源) (P0d Task 5)"
```

### Task 6: 7 个 DONE_CHECKERS 注册

**Files:**
- Modify: `src/vibecraft/bot/task_monitor.py` （尾部 register 块）
- Test: `tests/unit/test_done_when_checkers_extended.py`（新文件）

**Step 1: 写 failing test**

```python
# tests/unit/test_done_when_checkers_extended.py
"""新 7 个 done_when checker 的 evaluator 逻辑。"""

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from vibecraft.bot.task_monitor import DONE_CHECKERS


def _make_game_state(**kw):
    """构造 sc2 BotAI-like 状态。"""
    structures = MagicMock()
    structures.amount = kw.get("structure_amount", 0)
    units = MagicMock()
    units.amount = kw.get("unit_amount", 0)
    workers = MagicMock()
    workers.amount = kw.get("worker_amount", 0)
    return SimpleNamespace(
        structures=MagicMock(return_value=structures),
        units=MagicMock(return_value=units),
        workers=workers,
        already_pending=MagicMock(return_value=kw.get("pending", 0)),
        minerals=kw.get("minerals", 0),
        gas=kw.get("gas", 0),
        supply_used=kw.get("supply_used", 0),
        supply_cap=kw.get("supply_cap", 0),
    )

def test_structure_count_checker_true():
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_amount=8)
    done_when = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True

def test_structure_count_checker_false():
    checker = DONE_CHECKERS["structure_count"]
    state = _make_game_state(structure_amount=5)
    done_when = {"kind": "structure_count", "structure_type": "Gateway", "op": ">=", "value": 8}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is False

def test_minerals_checker():
    checker = DONE_CHECKERS["minerals"]
    state = _make_game_state(minerals=1200)
    done_when = {"kind": "minerals", "op": ">=", "value": 1000}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True

def test_supply_used_checker_lt():
    checker = DONE_CHECKERS["supply_used"]
    state = _make_game_state(supply_used=60)
    done_when = {"kind": "supply_used", "op": "<", "value": 70}
    assert checker(done_when, "d_1", state, MagicMock(), 0.0) is True

# ... 类似覆盖 own_unit_count / supply_cap / gas / worker_count，每个 2 case
```

**Step 2: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_done_when_checkers_extended.py -v
```
Expected: FAIL — `KeyError: 'structure_count'`

**Step 3: 实现**

在 `src/vibecraft/bot/task_monitor.py` 文件末尾加 7 个 checker：

```python
# ---------------------------------------------------------------------------
# P0d L4 done_when 扩词表 checker
# ---------------------------------------------------------------------------

_OP_FN = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _resolve_unit_id(name: str) -> Any | None:
    try:
        from sc2.ids.unit_typeid import UnitTypeId
        return UnitTypeId[name.upper()]
    except Exception:
        return None


@register("structure_count")
def _check_structure_count(done_when, directive_id, game_state, monitor, now=0.0):
    type_id = _resolve_unit_id(done_when["structure_type"])
    if type_id is None or game_state is None:
        return False
    current = game_state.structures(type_id).amount + int(game_state.already_pending(type_id))
    return _OP_FN[done_when["op"]](current, done_when["value"])


@register("own_unit_count")
def _check_own_unit_count(done_when, directive_id, game_state, monitor, now=0.0):
    type_id = _resolve_unit_id(done_when["unit_type"])
    if type_id is None or game_state is None:
        return False
    current = game_state.units(type_id).amount + int(game_state.already_pending(type_id))
    return _OP_FN[done_when["op"]](current, done_when["value"])


@register("supply_used")
def _check_supply_used(done_when, directive_id, game_state, monitor, now=0.0):
    if game_state is None:
        return False
    return _OP_FN[done_when["op"]](game_state.supply_used, done_when["value"])


@register("supply_cap")
def _check_supply_cap(done_when, directive_id, game_state, monitor, now=0.0):
    if game_state is None:
        return False
    return _OP_FN[done_when["op"]](game_state.supply_cap, done_when["value"])


@register("minerals")
def _check_minerals(done_when, directive_id, game_state, monitor, now=0.0):
    if game_state is None:
        return False
    return _OP_FN[done_when["op"]](game_state.minerals, done_when["value"])


@register("gas")
def _check_gas(done_when, directive_id, game_state, monitor, now=0.0):
    if game_state is None:
        return False
    return _OP_FN[done_when["op"]](game_state.gas, done_when["value"])


@register("worker_count")
def _check_worker_count(done_when, directive_id, game_state, monitor, now=0.0):
    if game_state is None:
        return False
    return _OP_FN[done_when["op"]](game_state.workers.amount, done_when["value"])
```

**Step 4: 跑测**
```bash
uv run pytest tests/unit/test_done_when_checkers_extended.py -v
uv run pytest tests/unit/test_task_monitor.py -q
```

**Step 5: Commit**
```bash
git add src/vibecraft/bot/task_monitor.py tests/unit/test_done_when_checkers_extended.py
git commit -m "feat(task_monitor): 7 个新 done_when checker (P0d Task 6)"
```

---

## P0c：L1 cancel 走 board.submit（代码统一）

涉及文件：
- `src/vibecraft/bot/director.py:611-612` —— STRATEGY_CANCEL 当前走 `_dispatch_cancel` 旁路
- `src/vibecraft/bot/director.py:1533-1617` —— `_apply_to_facade`（加 STRATEGY_CANCEL 分支）
- `src/vibecraft/bot/director.py:836-877` —— `_dispatch_cancel` 实现（迁移内容）

### Task 7: STRATEGY_CANCEL 进 board + facade 分支

**Files:**
- Modify: `src/vibecraft/bot/director.py:611-612` （拦截 + 直送 dispatch_cancel 那段，改成走 board.submit）
- Modify: `src/vibecraft/bot/director.py:1533-1617` （`_apply_to_facade`，加 STRATEGY_CANCEL 分支）
- Test: `tests/unit/test_director.py`（找 strategy_cancel 相关 test 改 + 新增）

**Step 1: 看现有 strategy_cancel test**
```bash
uv run pytest tests/unit/test_director.py -k cancel -v
```
看哪些已存在的 test 会因为路径变了而要更新。

**Step 2: 写 failing test**

```python
# tests/unit/test_director.py 加：
def test_strategy_cancel_goes_through_board(director, fake_facade):
    """L1 cancel 现在应该走 board.submit，directives.jsonl 有流水。"""
    director.on_player_command("取消所有剧本", now=10.0)
    # 1.5s commit delay 后
    director.on_tick(now=11.5)
    # facade.set_build 应被调过（切 sustain）
    assert "sustain" in fake_facade.builds
    # board 应有 strategy_cancel directive in_flight 或 committed
    # 具体 assert 看 board API
```

**Step 3: 跑测确认 FAIL**
```bash
uv run pytest tests/unit/test_director.py::test_strategy_cancel_goes_through_board -v
```

**Step 4: 实现**

director.py:611 附近：原来
```python
if d_with_ts.type == DirectiveType.STRATEGY_CANCEL:
    self._dispatch_cancel(d_with_ts, now)
```
改成走 board.submit：
```python
# STRATEGY_CANCEL 跟其它 directive 一样走 board，commit 后由 _apply_to_facade 执行
self.board.submit(d_with_ts, now)
# emit "已收到" 事件（占位卡片）
self._push_event({...})
```

`_apply_to_facade`（line 1533）加分支：
```python
if t == DirectiveType.STRATEGY_CANCEL:
    assert isinstance(payload, StrategyCancelPayload)
    self._apply_strategy_cancel(payload, now)  # 把现 _dispatch_cancel 的 stage 清理 + set_build sustain + push snapshot 抽到这里
    return
```

`_apply_strategy_cancel` 从 `_dispatch_cancel`(line 836) 拷贝 stage 清理 + facade.set_build("sustain") 逻辑。`_dispatch_cancel` 保留但只剩 deprecation note 或干脆删除。

**Step 5: 跑测 + commit**
```bash
uv run pytest tests/unit/test_director.py -q
git add src/vibecraft/bot/director.py tests/unit/test_director.py
git commit -m "feat(director): L1 cancel 统一走 board.submit (P0c Task 7)"
```

---

## P0e：structure_override 新 directive type

涉及文件：
- `src/vibecraft/directives/types.py:34` —— 加 enum
- `src/vibecraft/directives/models.py` —— 加 payload
- `src/vibecraft/bot/director.py` —— 加 `_exec_structure_override`
- `src/vibecraft/bot/director.py` —— `_apply_to_facade` 加分支（或直接进 production_overrides slot 走 `execute_overrides_step`）

### Task 8: STRUCTURE_OVERRIDE enum + payload + prereq table 扩

**Files:**
- Modify: `src/vibecraft/directives/types.py:34` （加 enum value）
- Modify: `src/vibecraft/directives/models.py` （加 StructureOverridePayload + 加入 Payload union）
- Test: `tests/unit/test_models.py` 加 case

**Step 1: 写 failing test**

```python
def test_structure_override_payload_validates():
    from vibecraft.directives.models import Directive
    d = Directive(
        payload={
            "type": "structure_override",
            "structure_type": "Gateway",
            "target_count": 8,
            "location_hint": "main",
        },
        issued_at=10.0,
    )
    assert d.payload.target_count == 8
    assert d.payload.structure_type == "Gateway"
```

**Step 2: 跑测确认 FAIL**

**Step 3: 实现**

`directives/types.py` line 34 后加：
```python
STRUCTURE_OVERRIDE = "structure_override"
```

`directives/models.py` 加 payload（紧跟 ExpansionOverridePayload 后）：

```python
class StructureOverridePayload(_PayloadBase):
    """L4 建筑数量目标（"补到 8 BG / ramp 1 cannon"）。
    一次性：达成 target_count 就 done，被打掉不自动补。"""

    type: Literal[DirectiveType.STRUCTURE_OVERRIDE] = DirectiveType.STRUCTURE_OVERRIDE
    structure_type: str
    target_count: int = Field(ge=1)
    location_hint: str | None = None  # "ramp" / "natural" / "front" / "main" / None=bot 自选
    priority: int = 50
```

把它加进 `Payload` union（line 250）：
```python
Payload = Annotated[
    ...
    | StructureOverridePayload
    | ...,
    Discriminator("type"),
]
```

**Step 4: 跑测**

**Step 5: Commit**
```bash
git commit -m "feat(directives): STRUCTURE_OVERRIDE type + payload (P0e Task 8)"
```

### Task 9: _exec_structure_override + 接进 execute_overrides_step

**Files:**
- Modify: `src/vibecraft/bot/director.py` —— `execute_overrides_step` 加分支调 `_exec_structure_override`；同时 `_REQUIRED_STRUCTURE` 加 6-8 个 structure 的 prereq
- Modify: `src/vibecraft/bot/director.py` —— `_apply_to_facade` 加 STRUCTURE_OVERRIDE 分支（同 PRODUCTION_OVERRIDE 风格，进 production_overrides slot）

**Step 1: 写 failing test**

```python
def test_structure_override_executes_build_when_prereq_ready(director, fake_facade, fake_bot):
    """补到 8 BG，当前 5 个，应该调 bot.build(Gateway) 凑差额。"""
    fake_bot.set_structure_count("GATEWAY", 5)
    fake_bot.set_minerals(300)
    fake_bot.set_prereq_satisfied("GATEWAY", True)

    d = make_structure_override_directive(structure_type="Gateway", target_count=8)
    director._exec_structure_override(d, d.payload)

    assert fake_bot.build_calls == [("GATEWAY", "main")]
    assert director._override_status[d.id]["status"] == "active"

def test_structure_override_on_hold_when_resource_low(director, fake_facade, fake_bot):
    fake_bot.set_structure_count("GATEWAY", 0)
    fake_bot.set_minerals(50)  # 不够造 Gateway(150)
    d = make_structure_override_directive(structure_type="Gateway", target_count=2)
    director._exec_structure_override(d, d.payload)
    assert director._override_status[d.id]["status"] == "on_hold"
```

**Step 2: 跑测 FAIL**

**Step 3: 实现 `_exec_structure_override`（参考 design doc §4.2）**

放在 director.py `_exec_expansion_override`（line 1079）后：

```python
async def _exec_structure_override(self, d: Directive, payload: Any) -> None:
    """L4 建筑目标:bot.build(structure_id, near=location)。
    一次性达标(被打掉不自动补)。"""
    from sc2.ids.unit_typeid import UnitTypeId
    type_name = payload.structure_type.upper()
    try:
        type_id = UnitTypeId[type_name]
    except KeyError:
        logger.warning("structure_override 未知 structure %r", payload.structure_type)
        self._set_override_status(d, "on_hold", f"未知建筑 {payload.structure_type}")
        return
    current = (
        self._bot.structures(type_id).amount + int(self._bot.already_pending(type_id))
    )
    if current >= payload.target_count:
        self._set_override_status(d, "active", f"{current}/{payload.target_count} 已达成")
        return
    ready, missing = self._check_prereq_ready(type_name)
    if not ready:
        self._set_override_status(d, "on_hold", f"需要 {missing}")
        return
    pos = self._resolve_location_hint(payload.location_hint, type_id)
    try:
        await self._bot.build(type_id, near=pos)
        logger.info(
            "structure_override BUILD %s near=%s (current=%d, target=%d, id=%s)",
            type_id, pos, current, payload.target_count, d.id[:8],
        )
        self._set_override_status(d, "active", f"造 {payload.structure_type} ({current+1}/{payload.target_count})")
    except Exception as exc:
        logger.debug("structure_override build fail: %s", exc)
        self._set_override_status(d, "on_hold", f"build 失败: {exc}")

def _resolve_location_hint(self, hint: str | None, type_id):
    """hint(main/natural/ramp/front) → Point2。复用 sharpy zone 系统。"""
    if hint is None:
        return None
    zones = self._bot.knowledge.expansion_zones
    if hint == "main":
        return zones[0].center_location
    if hint == "natural":
        return zones[1].center_location if len(zones) > 1 else zones[0].center_location
    if hint == "ramp":
        return self._bot.main_base_ramp.top_center
    if hint == "front":
        return self._bot.knowledge.enemy_main_base_ramp.top_center  # 或近敌点
    return None  # 未知 hint 让 sharpy 自选
```

在 `_apply_to_facade` 加分支：
```python
if t == DirectiveType.STRUCTURE_OVERRIDE:
    assert isinstance(payload, StructureOverridePayload)
    self._production_overrides[d.id] = d  # 入 L4 slot，PWA 卡片显示
    return
```

在 `execute_overrides_step` 加分发：
```python
elif isinstance(payload, StructureOverridePayload):
    await self._exec_structure_override(d, payload)
```

`_REQUIRED_STRUCTURE`（director.py 现有 mapping）加 6-8 项：
```python
"GATEWAY": "Nexus",  # BG 需要 NX
"FORGE": "Nexus",
"PHOTONCANNON": "Forge",
"CYBERNETICSCORE": "Gateway",
"ROBOTICSFACILITY": "CyberneticsCore",
"STARGATE": "CyberneticsCore",
"TWILIGHTCOUNCIL": "CyberneticsCore",
# ... 按 SC2 tech tree
```

**Step 4: 跑测**
```bash
uv run pytest tests/unit/test_director.py -k structure_override -v
```

**Step 5: Commit**
```bash
git commit -m "feat(director): _exec_structure_override + prereq table 扩 (P0e Task 9)"
```

---

## P0f：snapshot 统一 command_cards 字段

涉及文件：
- `src/vibecraft/bot/director.py:build_snapshot` —— 新增 `command_cards` array

### Task 10: build_snapshot 增加统一 command_cards

**Files:**
- Modify: `src/vibecraft/bot/director.py:build_snapshot`（看具体行号；约 L233-377）
- Test: `tests/unit/test_director.py` 加 case

**Step 1: 找 build_snapshot**
```bash
uv run python -c "
import inspect
from vibecraft.bot.director import Director
print(inspect.getsourcelines(Director.build_snapshot)[1])
"
```

**Step 2: 写 failing test**

```python
def test_snapshot_command_cards_unifies_4_layers(director, fake_facade):
    # 注入：1 strategy + 1 tactical + 1 standing + 1 production_override
    inject_l1_strategy(director, "iac_2base")
    inject_l2_tactical(director, verb="attack", target="enemy_natural")
    inject_l3_standing(director, "Probe", verb="patrol")
    inject_l4_production(director, "Sentry", count=2)
    director.on_tick(now=20.0)
    snap = director.build_snapshot(now=20.0)
    assert "command_cards" in snap
    cards = snap["command_cards"]
    assert len(cards) >= 4
    layers = {c["layer"] for c in cards}
    assert layers == {"L1", "L2", "L3", "L4"}
    for c in cards:
        assert c["revokable"] is True
        assert c["status"] in {"pending", "active", "on_hold", "done"}
```

**Step 3: FAIL**

**Step 4: 实现**

`build_snapshot` 加：

```python
def build_snapshot(self, now: float) -> dict:
    snap = ...  # 原有字段保留（向后兼容）
    snap["command_cards"] = self._build_command_cards(now)
    return snap

def _build_command_cards(self, now: float) -> list[dict]:
    cards: list[dict] = []
    # L1 strategy slots
    for stage, slot in self.board.slots.items():
        if slot is None:
            continue
        cards.append({
            "id": slot.directive_id,
            "layer": "L1",
            "type": "strategy_set",
            "display": f"{stage.value}: {slot.strategy_id}",
            "issued_at": slot.issued_at,
            "status": "active",
            "status_reason": "",
            "revokable": True,
        })
    # L2 active tactics
    for d in self._in_flight.values():
        if d.type == DirectiveType.TACTICAL_OBJECTIVE:
            p = d.payload
            cards.append({
                "id": d.id,
                "layer": "L2",
                "type": "tactical_objective",
                "display": _format_tactical(p),
                "issued_at": d.issued_at,
                "status": self._get_status(d.id, default="active"),
                "status_reason": self._get_status_reason(d.id, default=""),
                "revokable": True,
            })
        elif d.type == DirectiveType.ENGAGEMENT_CONSTRAINT:
            cards.append({
                "id": d.id,
                "layer": "L2",
                "type": "engagement_constraint",
                "display": f"stance: {d.payload.stance}",
                "issued_at": d.issued_at,
                "status": "active",
                "status_reason": "",
                "revokable": True,
            })
    # L3 standing orders
    for d in self.standing_orders:
        cards.append({
            "id": d.id,
            "layer": "L3",
            "type": "unit_claim",
            "display": _format_unit_claim(d.payload),
            "issued_at": d.issued_at,
            "status": "active",
            "status_reason": "",
            "revokable": True,
        })
    # L4 production overrides
    for d in self._production_overrides.values():
        st = self._override_status.get(d.id, {})
        cards.append({
            "id": d.id,
            "layer": "L4",
            "type": d.type.value,
            "display": _format_production(d.payload),
            "issued_at": d.issued_at,
            "status": st.get("status", "pending"),
            "status_reason": st.get("reason", ""),
            "revokable": True,
        })
    return cards
```

`_format_tactical` / `_format_unit_claim` / `_format_production` 是简单中文摘要 helper。

**Step 5: 跑测 + commit**
```bash
git commit -m "feat(snapshot): 统一 command_cards array 透传 4 层 (P0f Task 10)"
```

---

## P0g：WS revoke 全链路（director.revoke_directive 扩到 L2/L1）

涉及文件：
- `src/vibecraft/server/ws.py:246` —— `_handle_revoke_directive` 已存在
- `src/vibecraft/bot/director.py:826` —— `revoke_directive` 已 cover standing_order + production_override；扩到 L2 + L1

### Task 11: director.revoke_directive 支持 L1/L2

**Files:**
- Modify: `src/vibecraft/bot/director.py:826-834` —— `revoke_directive` 加 L2 / L1 分支
- Test: `tests/unit/test_director.py` 加 revoke L2/L1 case

**Step 1: 写 failing test**

```python
def test_revoke_tactical_clears_override(director, fake_facade):
    inject_l2_tactical(director, verb="attack", target="enemy_natural")
    director.on_tick(now=15.0)
    # 找出 directive id
    d_id = next(iter(director._tactical_overrides))
    assert director.revoke_directive(d_id, now=16.0) is True
    # facade 应被调清 override
    assert fake_facade.attack_target_overrides[-1] is None
    assert fake_facade.combat_intent_overrides[-1] is None

def test_revoke_strategy_clears_slot(director, fake_facade):
    inject_l1_strategy(director, "iac_2base", stage="midgame")
    director.on_tick(now=15.0)
    d_id = director.board.slots[StageKind.MIDGAME].directive_id
    assert director.revoke_directive(d_id, now=16.0) is True
    assert director.board.slots[StageKind.MIDGAME] is None
```

**Step 2: FAIL（revoke_directive 返 False，找不到 L2/L1）**

**Step 3: 实现**

director.py:826 `revoke_directive` 改：

```python
def revoke_directive(self, directive_id: str, now: float) -> bool:
    # L3 standing
    if self.revoke_standing_order(directive_id, now):
        return True
    # L4 production / structure / tech / expansion
    if self.revoke_production_override(directive_id, now):
        return True
    # L2 tactical（override flag 或 squad）
    if self.revoke_tactical(directive_id, now):
        return True
    # L1 strategy
    if self.revoke_strategy(directive_id, now):
        return True
    return False

def revoke_tactical(self, directive_id: str, now: float) -> bool:
    """L2 撤销：清 override flag + 释放 squad unit。"""
    cleared = False
    # override flag 路径
    if directive_id in getattr(self, "_tactical_overrides", {}):
        self._tactical_overrides.pop(directive_id, None)
        self.facade.set_attack_target_override(None)
        self.facade.set_combat_intent_override(None)
        cleared = True
    # squad 路径
    if directive_id in getattr(self, "_tactical_squads", {}):
        squad = self._tactical_squads.pop(directive_id)
        for tag in squad.unit_tags:
            self.facade.release_unit_role(tag)
        cleared = True
    if cleared:
        self.board.revoke(directive_id, now)
        self._push_event({"kind": "directive.revoked", "ts": now,
                          "payload": {"directive_id": directive_id, "reason": "player_x"}})
        self._push_snapshot(now)
    return cleared

def revoke_strategy(self, directive_id: str, now: float) -> bool:
    """L1 撤销：清 board slot + facade.set_build("sustain")。"""
    for stage, slot in list(self.board.slots.items()):
        if slot and slot.directive_id == directive_id:
            self.board.slots[stage] = None
            with contextlib.suppress(Exception):
                self.facade.set_build("sustain")
            self._push_event({"kind": "directive.revoked", "ts": now,
                              "payload": {"directive_id": directive_id, "reason": "player_x"}})
            self._push_snapshot(now)
            return True
    return False
```

**Step 4: 跑测**

**Step 5: Commit**
```bash
git commit -m "feat(director): revoke_directive 扩到 L2/L1 全层 (P0g Task 11)"
```

---

## P0b：squad 抢占（B 类 harass / scout）

涉及文件：
- `src/vibecraft/bot/director.py` —— `_exec_tactical_objective` + 分流 + `_exec_l2_squad` + `_exec_l2_global` + `execute_tactics_step`
- `src/vibecraft/bot/auto_combat/protoss/bot.py` —— `_tick_bot_channel` 加 `await self.director.execute_tactics_step(now)`

### Task 12: TacticalSquad dataclass + _exec_tactical_objective 分流

**Files:**
- Modify: `src/vibecraft/bot/director.py` —— 加 TacticalSquad dataclass + `_exec_tactical_objective` + 2 sub-routine
- Test: `tests/unit/test_director.py` 加 4 case（A 类 attack / A 类 defend / B 类 harass / B 类 scout）

**Step 1: 写 failing test**

```python
def test_l2_attack_sets_override_flags(director, fake_facade):
    """A 类: 进攻自然 → facade.set_attack_target_override + set_combat_intent_override('attack')"""
    inject_l2_tactical(director, verb="attack", target="enemy_natural")
    director.on_tick(now=15.0)
    assert fake_facade.attack_target_overrides[-1] is not None
    assert fake_facade.combat_intent_overrides[-1] == "attack"

def test_l2_harass_locks_squad_units(director, fake_facade, fake_bot):
    """B 类: 派 5 个凤凰骚扰 enemy_main → 抓 5 个 free Phoenix tag 设 LLM_CONTROLLED"""
    fake_facade.selector_stub["Phoenix"] = [101, 102, 103, 104, 105, 106]  # 6 个空闲
    inject_l2_tactical(director, verb="harass", unit_count_hint=5,
                       unit_type_hint=["Phoenix"], target="enemy_main",
                       done_when={"kind": "enemy_killed_in_area",
                                  "area": "enemy_main", "unit_type": "Probe",
                                  "op": ">=", "value": 5})
    director.on_tick(now=15.0)
    # 5 个 tag 应 set 成 LLM_CONTROLLED（不是 6 个）
    locked = [t for t, r in fake_facade.unit_roles.items() if r == UnitRole.LLM_CONTROLLED]
    assert sorted(locked) == [101, 102, 103, 104, 105]

def test_l2_harass_short_supply_shows_status(director, fake_facade):
    """短缺: 玩家说 5 个，只有 3 个 → 抓 3 个 + status 显示短缺"""
    fake_facade.selector_stub["Phoenix"] = [201, 202, 203]  # 仅 3 个
    inject_l2_tactical(director, verb="harass", unit_count_hint=5,
                       unit_type_hint=["Phoenix"], target="enemy_main",
                       done_when={"kind": "enemy_killed_in_area", ...})
    director.on_tick(now=15.0)
    d_id = next(iter(director._tactical_squads))
    assert director._tactical_squads[d_id].n_locked == 3
    assert "短缺" in director._override_status[d_id]["reason"]
```

**Step 2: FAIL**

**Step 3: 实现（参考 design doc §3.1-3.2 代码骨架）**

```python
# director.py 加
from dataclasses import dataclass
from enum import Enum

class TacticalVerbCategory(Enum):
    GLOBAL = "global"
    SQUAD = "squad"

@dataclass
class TacticalSquad:
    directive_id: str
    unit_tags: set[int]
    target: object  # Point2
    move_type: object  # sharpy MoveType
    verb: str
    n_wanted: int
    n_locked: int


_A_VERBS = {"attack", "defend", "retreat", "hold", "vision"}
_B_VERBS = {"harass", "scout", "raze", "regroup", "split", "drop"}


class Director:
    # ... 新加 ...
    _tactical_overrides: dict[str, str]  # directive_id → verb（占位单个 active L2 global）
    _tactical_squads: dict[str, TacticalSquad]
    _current_l2_global_id: str | None

    def _exec_tactical_objective(self, d, payload):
        verb = payload.verb
        if verb in _A_VERBS:
            self._exec_l2_global(d, payload)
        elif verb in _B_VERBS:
            self._exec_l2_squad(d, payload)
        else:
            logger.warning("L2 verb %r 未实现 (id=%s)", verb, d.id[:8])
            self._set_override_status(d, "on_hold", f"verb {verb} 未支持")

    def _exec_l2_global(self, d, payload):
        # 之前的 L2 global override 还在 active 的话先清
        if self._current_l2_global_id and self._current_l2_global_id != d.id:
            old = self._current_l2_global_id
            self._tactical_overrides.pop(old, None)
        point = self._resolve_target_area(payload.target_area)
        self.facade.set_attack_target_override(point)
        self.facade.set_combat_intent_override(payload.verb)
        self._tactical_overrides[d.id] = payload.verb
        self._current_l2_global_id = d.id
        self._set_override_status(d, "active", f"{payload.verb} {payload.target_area}")

    def _exec_l2_squad(self, d, payload):
        if payload.unit_count_hint is None:
            self._set_override_status(d, "on_hold", "缺 unit_count_hint")
            return
        n_wanted = payload.unit_count_hint
        unit_type = (payload.unit_type_hint or [self._infer_unit_type(payload)])[0]
        free_tags = self.facade.resolve_selector(unit_type=unit_type)
        tags = free_tags[:n_wanted]
        if not tags:
            self._set_override_status(d, "on_hold", f"无空闲 {unit_type}")
            return
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
        target_pt = self._resolve_target_area(payload.target_area)
        # sharpy MoveType 延迟 import
        try:
            from sharpy.combat.move_type import MoveType
        except ImportError:
            MoveType = None
        move_type = MoveType.Harass if MoveType and payload.verb == "harass" else (MoveType.Assault if MoveType else None)
        squad = TacticalSquad(
            directive_id=d.id,
            unit_tags=set(tags),
            target=target_pt,
            move_type=move_type,
            verb=payload.verb,
            n_wanted=n_wanted,
            n_locked=len(tags),
        )
        self._tactical_squads[d.id] = squad
        if len(tags) == n_wanted:
            msg = f"已接管 {len(tags)} 个 {unit_type}"
        else:
            msg = f"已接管 {len(tags)}/{n_wanted} 个 {unit_type}（短缺）"
        self._set_override_status(d, "active", msg)

    async def execute_tactics_step(self, now: float):
        """每 sharpy step 调，给 active squad 派活。"""
        if not self._tactical_squads:
            return
        for squad in list(self._tactical_squads.values()):
            try:
                self._bot.combat_manager.execute(
                    list(squad.unit_tags), squad.target, squad.move_type,
                )
            except Exception as exc:
                logger.debug("execute_tactics_step squad %s fail: %s", squad.directive_id[:8], exc)

    def _resolve_target_area(self, area):
        """area: str (named_spot) 或 (x, y) 元组 → Point2。"""
        from sc2.position import Point2
        if area is None:
            return None
        if isinstance(area, (tuple, list)) and len(area) == 2:
            return Point2((float(area[0]), float(area[1])))
        zones = self._bot.knowledge.expansion_zones
        if area == "enemy_main":
            return self._bot.enemy_start_locations[0]
        if area == "enemy_natural":
            return self._bot.knowledge.enemy_expansion_zones[1].center_location
        if area == "own_main":
            return zones[0].center_location
        if area == "own_natural":
            return zones[1].center_location if len(zones) > 1 else zones[0].center_location
        return None
```

`_apply_to_facade` 加分支：
```python
if t == DirectiveType.TACTICAL_OBJECTIVE:
    assert isinstance(payload, TacticalObjectivePayload)
    self._exec_tactical_objective(d, payload)
    return
```

**Step 4: 跑测**
```bash
uv run pytest tests/unit/test_director.py -k "l2_" -v
```

**Step 5: Commit**
```bash
git commit -m "feat(director): L2 tactical_objective executor (A 全军 + B squad) (P0b Task 12)"
```

### Task 13: bot._tick_bot_channel 接 execute_tactics_step

**Files:**
- Modify: `src/vibecraft/bot/auto_combat/protoss/bot.py:_tick_bot_channel`（找 `execute_overrides_step` 调用位置，在它旁边加）

**Step 1: 找现有调用位置**
```bash
uv run python -c "
import inspect
from vibecraft.bot.auto_combat.protoss import bot
src = inspect.getsource(bot)
for i, ln in enumerate(src.split('\n'), 1):
    if 'execute_overrides_step' in ln or '_tick_bot_channel' in ln:
        print(i, ln)
"
```

**Step 2: 写 contract test（mock bot.on_step）**

很难单测真 bot；省略此步，靠 e2e（P0k）覆盖。

**Step 3: 实现**

```python
async def _tick_bot_channel(self, now_s):
    await self.director.execute_overrides_step(now_s)
    await self.director.execute_tactics_step(now_s)  # NEW
    await super().on_step()
```

**Step 4: 全单测回归**
```bash
uv run pytest -q
```
Expected: 全过

**Step 5: Commit**
```bash
git commit -m "feat(bot): _tick_bot_channel 接 execute_tactics_step (P0b Task 13)"
```

---

## P0h：PWA CommandCardStack

涉及文件：
- `web/src/components/CommandCard.vue`（新）
- `web/src/components/CommandCardStack.vue`（新）
- `web/src/views/CockpitView.vue:107-162`（替换 4 个独立卡片）
- `web/src/types.ts:81-163`（加 CommandCardView interface）
- `web/src/composables/useWebSocket.ts` 或类似（看 revoke 帧发送是否已 wire）

### Task 14: types.ts 加 CommandCardView

**Files:**
- Modify: `web/src/types.ts`

```typescript
export interface CommandCardView {
  id: string
  layer: "L1" | "L2" | "L3" | "L4"
  type: string
  display: string
  issued_at: number
  status: "pending" | "active" | "on_hold" | "done"
  status_reason: string
  revokable: boolean
}

export interface Snapshot {
  // ... 现有字段 ...
  command_cards: CommandCardView[]
}
```

```bash
cd web && npm run typecheck
git commit -m "feat(web): CommandCardView type (P0h Task 14)"
```

### Task 15: CommandCard.vue + CommandCardStack.vue 组件

**Files:**
- Create: `web/src/components/CommandCard.vue`
- Create: `web/src/components/CommandCardStack.vue`

CommandCard.vue：
```vue
<template>
  <div :class="['card', 'card-' + card.status]">
    <div class="header">
      <span class="layer-tag">[{{ card.layer }}]</span>
      <span class="display">{{ card.display }}</span>
      <button v-if="card.revokable" class="x-btn" @click="$emit('revoke', card.id)">×</button>
    </div>
    <div class="status">
      {{ card.status }} <span v-if="card.status_reason"> — {{ card.status_reason }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { CommandCardView } from '@/types'
defineProps<{ card: CommandCardView }>()
defineEmits<{ revoke: [id: string] }>()
</script>

<style scoped>
.card { padding: 8px; margin-bottom: 4px; border-radius: 4px; }
.card-active { background: #1f3a1f; }
.card-on_hold { background: #3a3a1f; }
.card-pending { background: #2a2a2a; }
.x-btn { float: right; cursor: pointer; }
</style>
```

CommandCardStack.vue：
```vue
<template>
  <div class="card-stack">
    <CommandCard
      v-for="card in cards"
      :key="card.id"
      :card="card"
      @revoke="$emit('revoke', $event)"
    />
  </div>
</template>

<script setup lang="ts">
import type { CommandCardView } from '@/types'
import CommandCard from './CommandCard.vue'
defineProps<{ cards: CommandCardView[] }>()
defineEmits<{ revoke: [id: string] }>()
</script>
```

```bash
cd web && npm run typecheck && npm run lint
git commit -m "feat(web): CommandCard + CommandCardStack 组件 (P0h Task 15)"
```

### Task 16: CockpitView 接入 + revoke 帧

**Files:**
- Modify: `web/src/views/CockpitView.vue:107-162`

替换 4 个 Card 组件成 1 个 CommandCardStack。revoke handler 调用现有 WS sender 发送 `revoke_directive` 帧。

```vue
<template>
  <!-- 旧 4 个 Card 删 -->
  <CommandCardStack
    :cards="snapshot.command_cards"
    @revoke="onRevoke"
  />
</template>

<script setup lang="ts">
function onRevoke(directive_id: string) {
  ws.send({
    type: "revoke_directive",
    directive_id,
    client_id: clientId.value,
    issued_at: Date.now() / 1000,
  })
}
</script>
```

```bash
cd web && npm run typecheck && npm run build
git commit -m "feat(web): CockpitView 切 CommandCardStack + revoke 帧 (P0h Task 16)"
```

---

## P0i：LLM prompt 改

涉及文件：
- `src/vibecraft/llm/prompt.py`

### Task 17: prompt 加 structure_override + 7 done_when + A/B 类规则 + 5 few_shot

**Files:**
- Modify: `src/vibecraft/llm/prompt.py`
- Test: `tests/llm_eval/expected_specs.py`（新 case）+ `tests/unit/test_prompt.py`（snapshot prompt 出现关键字）

**Step 1: 写 prompt snapshot test**

```python
# tests/unit/test_prompt.py 加
def test_prompt_includes_new_directive_types():
    from vibecraft.llm.prompt import SYSTEM_PROMPT
    assert "structure_override" in SYSTEM_PROMPT
    assert "structure_count" in SYSTEM_PROMPT
    assert "own_unit_count" in SYSTEM_PROMPT
    assert "minerals" in SYSTEM_PROMPT
    # A 类 done_when=None 规则
    assert "A 系列" in SYSTEM_PROMPT or "attack/defend/retreat/hold/vision" in SYSTEM_PROMPT
```

**Step 2: FAIL**

**Step 3: 改 prompt**

`prompt.py` 改：
1. 加 STRUCTURE_OVERRIDE 进 4 层指令分类块
2. done_when 词表加 7 个新 kind 简介
3. A/B 类 done_when 规则段：
   ```
   L2 tactical_objective done_when 规则:
   - A 系列(attack/defend/retreat/hold/vision): done_when 必须 None,
     由玩家在 PWA 点 X 解除。不允许设 done_when，否则 task_monitor 会立即判 done。
   - B 系列(harass/scout): done_when 必须给 + unit_count_hint 必填。
     玩家没给数量(如"凤凰骚扰")→ 走 ambiguous，二次确认"几个凤凰"。
   ```
4. 加 5 个 few_shot（design doc §4.3 例 23-27）

**Step 4: 跑测**

**Step 5: Commit**
```bash
git commit -m "feat(prompt): 加 structure_override + 7 done_when + A/B 规则 + 5 few_shot (P0i Task 17)"
```

---

## P0j：llm_eval 跑全套

涉及文件：
- `tests/llm_eval/expected_specs.py`

### Task 18: 加 6 个新 case 跑 llm_eval

**Files:**
- Modify: `tests/llm_eval/expected_specs.py`

加 6 case：
1. "家里补到 8 BG" → structure_override(Gateway, 8) + done_when=structure_count(Gateway, >=, 8)
2. "ramp 放 1 cannon" → structure_override(PhotonCannon, 1, location_hint="ramp")
3. "进攻对方自然" → tactical_objective(attack, enemy_natural)，**done_when=None** ← 关键测试
4. "派 5 个凤凰去骚扰对方主基地" → tactical_objective(harass, unit_count_hint=5, unit_type_hint=[Phoenix], done_when=enemy_killed_in_area)
5. "凤凰骚扰对面"（无数量）→ ambiguous（confidence < 0.5）
6. "5 不朽就出门" → 复合：等条件 + 切意图（MVP 拆两步）

```bash
DEEPSEEK_API_KEY=$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')" | tr -d '\r') \
  uv run pytest tests/llm_eval/ -v -m llm_eval
```
Expected: 18-20/24 PASS（旧 14 case + 6 new）；至少 4/6 新 case 通过。失败的 case 调 prompt。

**Commit:**
```bash
git commit -m "test(llm_eval): 加 6 个新指令 case (P0j Task 18)"
```

---

## P0k：e2e_4 重跑 + 加 verify_log

涉及文件：
- `scripts/e2e_4_directive_types.py`

### Task 19: driver 加 verify_log + grep bot stdout

**Files:**
- Modify: `scripts/e2e_4_directive_types.py`

加 `verify_log_patterns` 字段到 Case：
```python
@dataclass
class Case:
    name: str
    inject: str
    inject_after: int
    verify_field: str
    verify_log_patterns: list[str] = field(default_factory=list)  # NEW
```

verify 时同时 grep bot stdout：
```python
def _verify_log_patterns(stdout: str, patterns: list[str]) -> tuple[bool, str]:
    missing = [p for p in patterns if p not in stdout]
    if missing:
        return False, f"stdout 缺 log: {missing}"
    return True, "log patterns 全命中"
```

加 patterns 到现有 case：
- L1a: `["set_build switched to iac_2base"]`
- L1b: `["set_build switched to sustain"]`
- L2a (attack): `["set_attack_target_override", "set_combat_intent_override.*attack"]`
- L2d (defend): `["set_combat_intent_override.*defend"]`
- L4a (Sentry): `["production_override TRAIN.*SENTRY"]`

加 4 个新 O 系列 case（补 8 BG / ramp cannon / 8 BG 转 IAC（两步）/ 凤凰骚扰 5）。

```bash
uv run --extra dev --extra sc2 python scripts/e2e_4_directive_types.py --seconds 90
```
Expected: ≥ 16/18 PASS（含 verify_log）。

**Commit:**
```bash
git commit -m "test(e2e): driver 加 verify_log + 4 个 O 系列 case (P0k Task 19)"
```

---

## Final：合 PR + 写 ADR 0011

### Task 20: 完成 ADR 0011 + push PR

**Files:**
- Modify: `docs/adr/0011-l2-tactical-executor.md` （把 design doc §3 的决策摘要写进 ADR）

ADR 包含：
- Context: e2e 报告暴露 L2 死路
- Decision: 候选 4 hybrid (override flag + squad 抢占)
- Consequences: A 类立刻 ship 价值高；B 类 squad 跟 sharpy GroupCombatManager 集成有 open question
- 关联：design doc, this plan, P0k 测试结果

```bash
git add docs/adr/0011-l2-tactical-executor.md
git commit -m "docs(adr): 0011 L2 tactical executor (落地决策摘要)"
git push -u origin m4-l2-l4-executor
gh pr create --title "M4: L2 tactical executor + L4 done_when 扩词表 + 命令卡片统一" \
  --body "..."
```

---

## 风险 & 中断恢复

| 风险 | 缓解 |
|---|---|
| Task 4（6 plan 改）发现某些 plan 不用 PlanZoneAttack 而是别的 act | per-file 适配；或 fork 多个 act 子类 |
| sharpy combat_manager.execute API 跟 design doc 假设不符 | P0b Task 12 实施时先 spike 30min 真 sharpy 接口；若签名不对调 design |
| structure_override 的 `_resolve_location_hint` 依赖 sharpy zone 系统 | 写 unit test 时 mock zones；e2e 时观察实际 build 位置正确性 |
| LLM 还是把"补 8 BG"解成 production_override | P0i prompt 例 23 明确；P0j 跑测确认 |
| revoke 跨 stage（slot 已 commit 但 directive_id 不在 _in_flight）| revoke_strategy 直接遍历 board.slots，不依赖 _in_flight |

---

## 完成标准

P0a-P0k 全部 commit + push 后：
1. `uv run pytest -q` 全过
2. `uv run pytest -m llm_eval` ≥ 18/24 PASS
3. `uv run --extra dev --extra sc2 python scripts/e2e_4_directive_types.py` ≥ 16/18 PASS（含 verify_log）
4. 手动 PWA 测：
   - "进攻自然" → CommandCardStack 出 L2 卡片 + 主力真去自然
   - 卡片点 X → bot 停止进攻 + 卡片消失
   - "补 8 BG" → L4 卡片 status=active 或 on_hold(资源不足) + bot 真造 BG
   - "派 5 凤凰骚扰对面" → 抓 5 个 Phoenix 单独行动 + 主力不变
