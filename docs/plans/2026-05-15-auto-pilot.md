# 通用 Auto-Pilot 实现方案（2026-05-15）

> 目标：无玩家干预时 `_VibeCraftBot` 自动运营到「普通电脑（Medium AI）」级别 ——
> 采矿、补农民、补 supply、补气、扩张、出兵。opening 期间只跑不冲突的 behavior，
> opening 跑完后启用会造东西的 controller。
>
> 约束铁律：auto-pilot 的所有 behavior **绝对不能动** `UnitRole.CONTROL_GROUP_ONE`
> 的单位（VibeCraft 把它当「被玩家语音接管的特种兵」标记，见 `ares_adapter.py`
> `role_map[UnitRole.LLM_CONTROLLED] = CONTROL_GROUP_ONE`）。

---

## 1. behaviors 注册 + 执行机制

### 结论：用 `self.register_behavior(behavior)`，每 tick 都重新注册

调研自 `ares/behavior_exectioner.py` + `ares/main.py`：

- `AresBot` 在 `on_start()` 末尾构造 `self.behavior_executioner = BehaviorExecutioner(...)`。
- `AresBot.register_behavior(behavior)` 是 `behavior_executioner.register_behavior` 的
  快捷方式，只是 `self.behaviors.append(behavior)`。
- 真正执行在 `AresBot._after_step()` 里：`self.behavior_executioner.execute()` —— 它
  按**注册顺序**逐个 `behavior.execute(ai, config, mediator)`，**执行完把 `behaviors`
  列表清空**。
- `_after_step()` 由 python-sc2 框架在 `on_step()` **返回之后**自动调用。

**含义（落地要点）**：

1. behavior 列表每 tick 被清空 → **必须每个 `on_step` 都重新 `register_behavior`**，
   不能只在 `on_start` 注册一次。
2. **不要**自己调 `behavior.execute(...)`（除非想绕过 executioner，没必要）。统一走
   `self.register_behavior(...)`，让 executioner 在 `_after_step` 统一跑。
3. 注册时机：在 `on_step` 里**什么位置 `register_behavior` 都行**（`super().on_step()`
   之前或之后都可以），因为真正执行是在 `on_step` 整个返回后的 `_after_step`。为可
   读性，建议放在 `await super().on_step(iteration)` **之后**、消费 command queue 与
   `director.on_tick` 附近 —— 逻辑上「先让 ares 跑完 opening build runner，再决定补
   哪些 auto-pilot behavior」。
4. 注册顺序 = 执行优先级。把「保命/基建」类放前面，「出兵」类放后面（虽然多数 macro
   behavior 之间无强耦合，但 `register_behavior` 顺序决定同 tick 内谁先抢资源）。

> ⚠️ spike 验证点 A：确认 `super().on_step()` 内部 `build_order_runner.run_build()`
> 抛异常时不会吞掉我们后面的 `register_behavior`。当前 `_VibeCraftBot.on_step` 直接
> `await super().on_step(iteration)` 无 try/except —— 若 ares 内部炸了，auto-pilot 也
> 不会注册。本方案不改这个行为（保持和现状一致），但端到端 smoke 时留意日志。

---

## 2. 通用 auto-pilot 启用哪些 behavior + 构造参数

### 2.1 army_composition_dict 参数格式（SpawnController / ProductionController 共用）

来自 `spawn_controller.py` / `production_controller.py` docstring + 代码：

```python
army_composition_dict: dict[UnitID, dict[str, float | int]]
# key   = sc2 UnitTypeId
# value = {"proportion": float, "priority": int}
#   proportion: 该兵种占总军队的目标比例，所有 proportion 之和必须 == 1.0
#               （SpawnController 里 assert isclose(proportion_sum, 1.0)，
#                除非 freeflow_mode=True）
#   priority:   0-10 的整数，0 = 最高优先级（资源不够时先满足 priority 小的）
```

- `SpawnController`：从**已有的空闲生产建筑**里训练单位凑比例。**不会造建筑**，
  造不出来的兵种直接跳过。
- `ProductionController`：根据同一个 army_comp_dict **造生产建筑 + tech 建筑**
  （内部调 `TechUp` / `BuildStructure`）。Terran/Protoss only，Zerg 直接 return False。

### 2.2 推荐的神族通用组合

目标普通电脑级别，地面追猎 + 不朽 + 叉子（闪追的「闪」是升级，普通电脑不需要）：

```python
from sc2.ids.unit_typeid import UnitTypeId as UnitID

GENERIC_PROTOSS_ARMY: dict = {
    UnitID.IMMORTAL:  {"proportion": 0.25, "priority": 0},  # 不朽，最高优先
    UnitID.STALKER:   {"proportion": 0.55, "priority": 1},  # 追猎，主力
    UnitID.ZEALOT:    {"proportion": 0.20, "priority": 2},  # 叉子，肉盾
}
# proportion 之和 = 1.0 ✓
```

理由：
- 三个兵种 tech 需求都不深（BG + BC 出追猎/叉子，VR 出不朽），opening 剧本（1门Robo /
  IAC / Skytoss）跑完后这些建筑大概率已经有了，`ProductionController` 只需补数量。
- 不朽 priority 最高但 proportion 最低 → 资源紧时优先出 1-2 个不朽，富余了才大量追猎。
- 不碰空军 / VT / VX 系，避免 `ProductionController` 去造一堆高科技建筑拖慢运营。

> ⚠️ spike 验证点 B：`SpawnController` 对 Protoss 的 WARPGATE 有特判（见
> `spawn_controller.py` execute 开头：`WARPGATERESEARCH in upgrades` 且有空闲 GATEWAY
> 时 `return False`，让 gateway 先 morph 成 warpgate）。也就是说 **WARPGATE 出兵需要
> `mediator.request_warp_in`**，`SpawnController._morph_units` 里已处理（`unit.type_id
> == WARPGATE` 分支调 `mediator.request_warp_in`）。端到端要确认折跃门出兵正常、不会
> 卡住。`spawn_target` 参数可以传一个集结点 Point2 让折跃门在那附近折跃。

### 2.3 各 behavior 构造参数清单

| Behavior | 构造 | 何时启用 | 备注 |
|---|---|---|---|
| `Mining()` | 无参（全默认即可）| **全程** | `keep_safe=True` / `long_distance_mine=True` 默认就好。只动 `GATHERING` 角色 worker。|
| `AutoSupply(self.start_location)` | 必传 `base_location` | **全程** | 神族会在 `start_location` 附近找位置造 PY。`return_true_if_supply_required` 默认 True。|
| `BuildWorkers(to_count=N)` | 必传 `to_count` | **全程** | 内部用 `SpawnController({worker: ...})` 但只在 `townhalls.idle` 且 `supply_workers < to_count` 时触发。建议 `to_count=66`（约 3 基地饱和）。|
| `GasBuildingController(to_count=M)` | 必传 `to_count` | **opening 后**（保守）/ 见 §3 | `to_count` 建议 `len(self.townhalls) * 2`，每 tick 动态算。会 `select_worker`（只抓 GATHERING）。|
| `ExpansionController(to_count=K, max_pending=1)` | 必传 `to_count` | **opening 后** | `to_count` 建议固定 4（普通电脑级别 3-4 矿够了）。会 `select_worker` + 造 NX。|
| `ProductionController(GENERIC_PROTOSS_ARMY, self.start_location)` | 必传 army_comp + `base_location` | **opening 后** | 造 BG/BC/VR 等生产建筑 + tech。`max_production_structures=12` 默认。|
| `SpawnController(GENERIC_PROTOSS_ARMY, spawn_target=<集结点>)` | 必传 army_comp | **opening 后** | 从空闲生产建筑训练凑比例。|

### 2.4 用 `MacroPlan` 打包？—— 不推荐，直接平铺注册

`macro_plan.py`：`MacroPlan` 是一个**容器 behavior**，`add(behavior)` 进去后
`execute` 时**按顺序跑，第一个返回 True 的就 return（短路）**，即「同一 tick 只让一个
behavior 真正动手」。

适用场景：当你想要严格的「补给 > 扩张 > 出兵」单点优先级、每 tick 只做一件大事时。
但本方案的 auto-pilot 希望**同一 tick 内 Mining + AutoSupply + 出兵可以同时发生**
（它们抢的资源不冲突或冲突可接受），所以**直接逐个 `register_behavior` 平铺**即可，
不套 `MacroPlan`。

如果之后发现「同 tick 同时扩张 + 造生产建筑 + 出兵」抢矿太凶，可以把「会花矿造东西」
的那几个（`ExpansionController` / `ProductionController` / `GasBuildingController`）
塞进一个 `MacroPlan` 做软优先级，`Mining` / `AutoSupply` / `SpawnController` 仍平铺。
本方案先平铺，留这个作为调优后路。

---

## 3. 跟 opening build runner 的冲突处理（两阶段）

### 3.1 冲突分析

opening 期间，`build_order_runner.run_build()`（在 `super().on_step()` 内调用）正在
按剧本 steps 造 PY/BG/BC/VR、出 BO 里指定的兵、扩张。会冲突的 auto-pilot behavior：

| Behavior | 是否冲突 opening | 原因 |
|---|---|---|
| `Mining` | **不冲突** | 只管 `GATHERING` worker 采矿，BO runner 不关心采矿。|
| `AutoSupply` | **基本不冲突** | BO 里也常有 PY step；两者都造 PY 最多偶尔多造一个 PY，可接受。注意 `build_order_runner` 自己在 `run_build()` 末尾也有 `if supply_used >= auto_supply_at_supply: AutoSupply(...)` —— 默认 `auto_supply_at_supply=200`，opening 期间几乎不触发，所以我们注册的 `AutoSupply` 是主力。**不冲突，全程开。**|
| `BuildWorkers` | **轻微冲突** | BO runner 有 `ConstantWorkerProductionTill` 机制（`_produce_workers`）也在补农民。两者都补农民 → 可能小幅超产。可接受（普通电脑级别不在乎），但**保守起见 opening 期间不开 `BuildWorkers`**，让 BO 的农民节奏走完。|
| `GasBuildingController` | **冲突** | 会主动造 VC，可能和 BO 的 gas step 抢 geyser / 打乱 gas timing。**opening 后再开。**|
| `ExpansionController` | **冲突** | 会主动造 NX，直接打乱 BO 扩张节奏。**opening 后再开。**|
| `ProductionController` | **冲突** | 会主动造 BG/BC/VR，和 BO 建筑节奏冲突。**opening 后再开。**|
| `SpawnController` | **冲突** | 会从空闲生产建筑出兵 —— opening 期间 BO 自己也用 `SpawnController` 出兵（见 `build_order_runner.do_step`），两者抢同一批空闲建筑会乱。**opening 后再开。**|

### 3.2 怎么判断 opening 跑完

`build_order_runner.py` 有现成的 `build_completed` property：

```python
@property
def build_completed(self) -> bool:
    return self._opening_build_completed
```

`_opening_build_completed` 在 `set_build_completed()` 里被置 True，触发点是
`run_build()` 里 `if not self.build_completed and self.build_step >= len(self.build_order)`。
即 **BO 所有 step 跑完后自动翻 True**。

> ⚠️ 注意：`set_build()`（语音切 build）会调 `switch_opening(name, remove_completed=True)`
> → 重新 `configure_opening_from_yml_file` → `build_step` 归 0、装新的 `build_order`。
> 但 `switch_opening` **不会**把 `_opening_build_completed` 重新置回 False（代码里没
> 这行）。所以一旦 opening 跑完一次，`build_completed` 永远是 True —— 即使玩家中途
> 语音切了一个新 build，新 build 的 steps 会跑（`run_build` 里 `build_step` 重置了，
> step 列表非空就会 `do_step`），但 `build_completed` 仍是 True。
>
> **对 auto-pilot 的影响**：用 `build_completed` 作为「是否启用 §3 第二阶段 behavior」
> 的开关是安全的（一旦进入第二阶段就不退回）。但如果玩家在游戏中后期语音切了一个新
> 的长 opening，会出现「BO runner 在跑新 steps 造建筑」+「ProductionController 也在
> 造建筑」并存的短暂冲突窗口。MVP 阶段可接受（普通电脑级别，玩家也是主动切的）；
> 若要彻底干净，留一个 ⚠️ spike C：在 `_AresFacade.set_build()` 里切 build 时，同时
> 记一个 `self._opening_phase_override_until` 时间戳，auto-pilot 在这段时间内重新降级
> 到第一阶段。**本方案 MVP 不做这个，只标注。**

### 3.3 两阶段总表

```
阶段一（opening 期间，build_completed == False）：
    register_behavior(Mining())
    register_behavior(AutoSupply(self.start_location))

阶段二（opening 跑完，build_completed == True）：
    register_behavior(Mining())                                  # 继续
    register_behavior(AutoSupply(self.start_location))           # 继续
    register_behavior(BuildWorkers(to_count=66))
    register_behavior(GasBuildingController(to_count=len(self.townhalls)*2))
    register_behavior(ExpansionController(to_count=4, max_pending=1))
    register_behavior(ProductionController(GENERIC_PROTOSS_ARMY, self.start_location))
    register_behavior(SpawnController(GENERIC_PROTOSS_ARMY, spawn_target=<集结点>))
```

注册顺序即优先级：保命基建（Mining/AutoSupply/BuildWorkers/Gas/Expansion）在前，
造生产建筑 + 出兵在后。

---

## 4. ⚠️ 关键约束 —— 跟 LLM 控制单位（CONTROL_GROUP_ONE）的隔离

### 调研结论：**推荐的所有 behavior 都天然隔离 `CONTROL_GROUP_ONE`，安全。**

逐个核对（源码依据见括号）：

| Behavior | 抓单位的方式 | 是否碰 CONTROL_GROUP_ONE | 结论 |
|---|---|---|---|
| `Mining` | `mediator.get_units_from_role(role=UnitRole.GATHERING, unit_type=ai.worker_type)`（`mining.py:86`）| **否** —— 只取 GATHERING 角色的 worker | ✅ 安全 |
| `AutoSupply` | 非 Zerg 走 `BuildStructure(...)` → `mediator.select_worker(...)` | **否** —— 见下方 `select_worker` 分析 | ✅ 安全 |
| `BuildWorkers` | 内部 `SpawnController({worker: ...})`，只从 `ai.townhalls.idle` 训练 worker | **否** —— 只用 townhall 建筑，不抓 army 单位 | ✅ 安全 |
| `GasBuildingController` | `mediator.select_worker(...)`（`gas_building_controller.py:82`）| **否** —— 见 `select_worker` 分析 | ✅ 安全 |
| `ExpansionController` | `mediator.select_worker(target_position=location)`（`expansion_controller.py:68`）| **否** —— 见 `select_worker` 分析 | ✅ 安全 |
| `ProductionController` | 内部 `TechUp` / `BuildStructure` → `select_worker`；建筑选择走 `ai.get_build_structures`（只看建筑）| **否** | ✅ 安全 |
| `SpawnController` | 通过 `ai.get_build_structures(trained_from, ...)` 拿**空闲生产建筑**（`get_build_structures` 见 `main.py:891`），从建筑训练单位 | **否** —— 只操作生产建筑，不按角色抓 army 单位 | ✅ 安全 |

**核心依据 1 —— `select_worker` 只从 `GATHERING` 取人**
（`resource_manager.py:381-383`）：

```python
workers: Units = self.manager_mediator.get_units_from_roles(
    roles={UnitRole.GATHERING}, unit_type=self.ai.worker_type
)
```

所有「造建筑/扩张/补气」类 behavior 选 worker 都走这个 `select_worker`，它**只会从
`UnitRole.GATHERING` 里挑** worker（外加 `select_persistent_builder` 时挑
`PERSISTENT_BUILDER`）。被玩家语音接管的 worker 是 `CONTROL_GROUP_ONE` —— 不在
`GATHERING` 集合里，**永远不会被 `select_worker` 选中**。

**核心依据 2 —— 新造的兵不会自动进 auto-pilot 的「可控集合」**
（`unit_role_manager.py:149` `catch_unit`）：

```python
def catch_unit(self, unit, type_id, tag):
    if type_id in UNIT_TYPES_WITH_NO_ROLE:
        return
    if tag not in self.all_assigned_tags:
        if type_id == self.ai.worker_type:
            self.assign_role(tag, UnitRole.GATHERING)   # 只给 worker 自动派角色
```

ares 自动派角色**只对 worker 生效**（→ GATHERING）。`SpawnController` 训练出来的
追猎/不朽/叉子**不会被自动派任何角色**。`SpawnController` 自身也不维护「已生产单位
的控制集合」—— 它每 tick 只是「看空闲生产建筑 → 训练 → 完事」。所以 auto-pilot 出的
兵生产完就处于「无角色 idle」状态，**不会和 `CONTROL_GROUP_ONE` 混淆**。

**反过来的隔离也成立**：玩家语音接管某个单位时，`_AresFacade.set_unit_role` 调
`mediator.assign_role(tag, CONTROL_GROUP_ONE)`。`assign_role` 内部先 `clear_role(tag)`
把它从原角色（比如 GATHERING）移除。从这一刻起：
- 该 worker 不再在 `GATHERING` → `Mining` 不再控它、`select_worker` 不再选它。
- 该单位本来就没在 auto-pilot 的任何控制路径里。

### 唯一需要注意的边界情况（⚠️ spike D）

`SpawnController._morph_units`（`spawn_controller.py:329`）对**已经被它放进 build_dict
的建筑**会 `mediator.clear_role(tag=unit.tag)` —— 这里 `unit` 是**生产建筑**（GATEWAY
/ ROBOTICSFACILITY 等），不是 army 单位。清的是建筑的角色，不影响 `CONTROL_GROUP_ONE`
的 army 单位。**确认安全**，但端到端 smoke 时建议打一条日志：每 tick 打印
`len(mediator.get_units_from_role(CONTROL_GROUP_ONE))`，确保玩家接管的单位数在
auto-pilot 跑起来后不会莫名减少。

### 如果将来换 behavior 的排查口诀

> 任何新增的 macro behavior，下井之前先 grep 它 `execute` 里的
> `get_units_from_role` / `get_units_from_roles` / `select_worker` / `get_all_*` ——
> 只要它取的角色集合不包含 `CONTROL_GROUP_ONE`，就是安全的。ares 内置 macro
> behavior 目前**没有任何一个**会主动取 `CONTROL_GROUP_ONE`（这正是设计文档 §3.4
> 选它作「排除单元」载体的原因）。

---

## 5. `_VibeCraftBot.on_step` 落地伪代码

> 改动只在 `make_bot_class` 的 `_VibeCraftBot` 类内。不动 `_AresFacade` / director /
> command queue 逻辑。下面用 `# === AUTO-PILOT ===` 标注新增块。

### 5.1 模块顶部新增常量（放在 `make_bot_class` 内、`_VibeCraftBot` 定义之前）

```python
# === AUTO-PILOT === 通用神族军队组合（追猎为主 + 不朽 + 叉子，普通电脑级别）
# 在 make_bot_class 闭包内 import，保持「仅装了 ares 才 import」的约定
from sc2.ids.unit_typeid import UnitTypeId as _UnitID
from ares.behaviors.macro import (
    AutoSupply,
    BuildWorkers,
    ExpansionController,
    GasBuildingController,
    Mining,
    ProductionController,
    SpawnController,
)

_GENERIC_PROTOSS_ARMY: dict = {
    _UnitID.IMMORTAL: {"proportion": 0.25, "priority": 0},
    _UnitID.STALKER:  {"proportion": 0.55, "priority": 1},
    _UnitID.ZEALOT:   {"proportion": 0.20, "priority": 2},
}
_TARGET_WORKER_COUNT = 66
_TARGET_BASE_COUNT = 4
```

### 5.2 `on_step` 改造

```python
async def on_step(self, iteration: int) -> None:
    if hasattr(super(), "on_step"):
        await super().on_step(iteration)   # ares 跑 build_order_runner + managers

    # === AUTO-PILOT === 注册通用运营 behavior
    # 必须每 tick 重新注册：behavior_executioner 在 _after_step 执行后会清空列表。
    self._register_auto_pilot()

    # ---- 以下为现状逻辑，不变 ----
    if down_q is not None:
        try:
            while True:
                msg = down_q.get_nowait()
                ...   # 原样保留
        except queue_module.Empty:
            pass

    if self.director is not None:
        self.director.on_tick(now=float(self.time))


def _register_auto_pilot(self) -> None:
    """注册通用 auto-pilot behavior。两阶段：
    - 阶段一（opening 未跑完）：只跑不和 build_order_runner 冲突的 Mining / AutoSupply
    - 阶段二（opening 跑完）：追加会主动造东西 / 出兵的 controller

    隔离保证：所有这些 behavior 选 worker 都走 mediator.select_worker（只取
    UnitRole.GATHERING），出兵只操作生产建筑 —— 不会碰 CONTROL_GROUP_ONE
    （= vibecraft 的 LLM_CONTROLLED 特种兵）。详见 docs/plans/2026-05-15-auto-pilot.md §4。
    """
    # build_order_runner 在 super().on_start() 末尾才构造；防御性 hasattr。
    runner = getattr(self, "build_order_runner", None)
    if runner is None:
        return

    # ---- 阶段一：全程开 ----
    self.register_behavior(Mining())
    self.register_behavior(AutoSupply(self.start_location))

    # ---- 阶段二：opening 跑完才开 ----
    if runner.build_completed:
        self.register_behavior(BuildWorkers(to_count=_TARGET_WORKER_COUNT))
        # 气矿目标随基地数动态算（每基地 2 个气）
        self.register_behavior(
            GasBuildingController(to_count=len(self.townhalls) * 2)
        )
        self.register_behavior(
            ExpansionController(to_count=_TARGET_BASE_COUNT, max_pending=1)
        )
        self.register_behavior(
            ProductionController(_GENERIC_PROTOSS_ARMY, self.start_location)
        )
        # spawn_target：让折跃门 / 生产建筑在主基地附近集结；
        # 也可换成自然分等更靠前的集结点。
        self.register_behavior(
            SpawnController(_GENERIC_PROTOSS_ARMY, spawn_target=self.start_location)
        )
```

### 5.3 实现注意

- `_register_auto_pilot` 放 `_VibeCraftBot` 类内即可，不需要进 `_AresFacade`
  （它操作的是 ares behavior，是 bot 层的事，不是 facade 抽象的事）。
- `self.start_location` / `self.townhalls` / `self.time` 都是 `AresBot` 继承自
  python-sc2 的属性，`on_start` 后随时可读。
- 不需要改 `on_start`：`behavior_executioner` 由 `super().on_start()` 构造好；
  `register_behavior` 第一次被调用是在第一个 `on_step`，那时 executioner 已就位。
- **单测**：`tests/unit/` 全程 mock，不 import `ares_adapter`（文件头注释已说明）。
  本方案的逻辑（两阶段切换、build_completed 判断）建议在 `tests/integration/`
  用 mock 的 `build_order_runner`（`build_completed` 属性 stub False/True）验证
  `_register_auto_pilot` 注册了正确的 behavior 集合。纯单测层可对 `_GENERIC_PROTOSS_ARMY`
  的 proportion 和（== 1.0）加一个断言用例，防手滑。

---

## 6. 待 spike 验证清单（端到端 smoke 时逐条确认）

| 编号 | 验证点 |
|---|---|
| ⚠️ A | `super().on_step()` 内部异常不会导致 auto-pilot 永远不注册（保持现状无 try/except，仅观察日志）。|
| ⚠️ B | Protoss WARPGATE 出兵：`SpawnController` 走 `request_warp_in` 分支，折跃门正常折跃、不卡住。|
| ⚠️ C | 玩家中后期语音切新 long opening 时，`build_completed` 仍为 True → BO runner 跑新 steps 与 `ProductionController` 并存的短暂冲突（MVP 接受，不做降级）。|
| ⚠️ D | 跑起来后每 tick 打印 `len(get_units_from_role(CONTROL_GROUP_ONE))`，确认玩家接管的单位数不被 auto-pilot 影响。|
| 通用 | opening 跑完后，3-4 基地饱和采矿、农民补到 ~66、supply 不卡、有稳定的追猎/不朽/叉子流出 —— 达到「普通电脑级别」。|
