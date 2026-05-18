# ADR 0012: 三族 Bot 基类 + 工厂混合模式

**日期**: 2026-05-18
**状态**: 已实施（M6 完成）
**决策者**: catmaniii

---

## 背景

M5 完成后，VibeCraft 只有神族 bot（`_VibeCraftProtossBot`，直接继承 `KnowledgeBot`）。
M6 目标：在**不破坏神族单测 / 不改变现有 Director / Facade 协议**的前提下，用最少代码
重复支持虫族 + 人族。

核心问题：三族 bot 共享大量生命周期逻辑（EventBus 初始化、down_q 消费、相机 drain、
hang watchdog、LLM_CONTROLLED tags refresh、指令节流），但种族特化点只有 3 处：

1. `EXCLUDE_FROM_ARMY`（从主力军池中剔除哪些单位类型）
2. `DEFAULT_OPENING_ID`（默认开局剧本 ID）
3. `create_plan()`（IfElse 路由树 + 种族专属 plans 列表）

---

## 候选方案

### 方案 A：三个独立 bot 类，共享 mixin

```
KnowledgeBot
└── VibeCraftMixin  (mixin，非继承)
    ├── _VibeCraftProtossBot
    ├── _VibeCraftZergBot
    └── _VibeCraftTerranBot
```

- 优：最简单，mixin 无 MRO 问题
- 劣：mixin 无法持有 `__init__` 状态，`_llm_controlled_tags` 等属性初始化分散；
  mypy strict 对 mixin 的 `self` 类型检查复杂

### 方案 B：抽象基类 VibeCraftBotBase，三族继承

```
KnowledgeBot
└── VibeCraftBotBase  (抽象；持有所有共用状态 + 生命周期)
    ├── _VibeCraftProtossBot
    ├── _VibeCraftZergBot
    └── _VibeCraftTerranBot
```

- 优：单一继承链，`__init__` 集中初始化，mypy 可静态验证
- 劣：多一层继承，`KnowledgeBot` 本身不是 ABC，`super().__init__` 链需注意

### 方案 C：组合优于继承 — Director 注入 race strategy object

三族共用同一个 `_VibeCraftBot`，种族差异由注入的 `RaceStrategy` 对象提供。

- 优：纯组合，无继承
- 劣：sharpy 的 `create_plan()` 是框架回调，无法在外部注入；需要大量 monkey-patch，
  破坏 sharpy 生命周期假设

---

## 决策：方案 B（抽象基类 + 工厂函数）

选择方案 B，同时加入**工厂函数**模式（参考 M1 中 `make_protoss_bot_class`）。

完整结构：

```
common_bot.py:
    class VibeCraftBotBase(KnowledgeBot):
        EXCLUDE_FROM_ARMY: ClassVar[set] = set()
        DEFAULT_OPENING_ID: ClassVar[str] = ""
        # 所有共用生命周期 hook / 属性初始化
        async def create_plan(self) -> BuildOrder:
            raise NotImplementedError

protoss/bot.py:
    def make_protoss_bot_class(director_factory, ...) -> type:
        class _VibeCraftProtossBot(VibeCraftBotBase):
            EXCLUDE_FROM_ARMY = {PROBE, OBSERVER, WARPPRISM}
            DEFAULT_OPENING_ID = "4bg"
            async def create_plan(self): ...
        return _VibeCraftProtossBot

zerg/bot.py:
    def make_zerg_bot_class(director_factory, ...) -> type:
        class _VibeCraftZergBot(VibeCraftBotBase):
            EXCLUDE_FROM_ARMY = {DRONE, OVERLORD, OVERSEER}
            DEFAULT_OPENING_ID = "12pool"
            async def create_plan(self): ...
        return _VibeCraftZergBot

terran/bot.py:
    def make_terran_bot_class(director_factory, ...) -> type:
        class _VibeCraftTerranBot(VibeCraftBotBase):
            EXCLUDE_FROM_ARMY = {SCV, MULE}
            DEFAULT_OPENING_ID = "marine_rush"
            async def create_plan(self): ...
        return _VibeCraftTerranBot
```

`sharpy_adapter.make_bot_class(race=...)` 按 `race` 参数 dispatch 到对应工厂函数。

---

## 关键设计细节

### sharpy dummy 复用策略

虫/人族 plans 直接复用 sharpy 的完整 dummy plan 类
（`KnowledgeBot` 子类，含 `create_plan`），每个剧本 yaml 的
`sharpy_dummy_class` 字段指向对应模块路径。IfElse 路由树在 bot 的
`create_plan()` 里构造，`active_recipe` field 每 step 重新求值（lambda）。

这与神族完全相同（见 ADR 0009 §Hook A），无需为虫/人族重写路由逻辑。

### EXCLUDE_FROM_ARMY 种族差异

- 神族：`{PROBE, OBSERVER, WARPPRISM}` — 探机 + 侦察类
- 虫族：`{DRONE, OVERLORD, OVERSEER}` — 工蜂 + 侦察单位
- 人族：`{SCV, MULE}` — 工人（Medivac 是战斗单位，**不**排除）

`PlanZoneAttack` 的 `free_units` 天然不含 `UnitTask.Reserved`，
LLM_CONTROLLED 角色依然通过 `_llm_controlled_tags` + 每 step refresh 实现，
三族行为一致（见 ADR 0009 §Hook C）。

### Sustain plan 种族差异

每族都有一个 `Sustain` plan（路由树的终端 fallback，`PlanZoneGather` 但无 `PlanZoneAttack`）：

- `ZergSustain`：必须含 `AutoOverLord()` + `InjectLarva()`，否则蟑螂 supply 被卡死
- `TerranSustain`：必须含 `MorphOrbitals()` + `CallMule(50)` + `AutoDepot()` + `Repair()`

神族无专门的 `Sustain` 类（由现有 `VibecraftSustain` / `PlanZoneGather` 组合实现）。

### 工厂函数 vs 直接 class

工厂函数（`make_*_bot_class`）允许把 `director_factory`、`strategy_library` 等
运行时依赖通过**闭包**注入到 bot 类，而不需要修改 sharpy 的 `KnowledgeBot.__init__`
签名。这是 M1 确立的模式，M6 三族沿用。

---

## 影响

- `sharpy_adapter.py`：加 `Zerg` / `Terran` dispatch 分支
- `server/game_process.py`：`GameConfig.my_race` 新字段，默认 `"Protoss"`；
  `_child_entry` 用 `Race[config.my_race]`；`_build_bot_class` 按 my_race 拼路径
- CLI：`vibecraft serve --my-race {Protoss,Zerg,Terran}`
- 单测：`test_zerg_bot_smoke.py` / `test_terran_bot_smoke.py` 各 10 条，
  全部基于 `FakeFacade` + mock sharpy，无需真实 SC2

---

## 结论

基类（`VibeCraftBotBase`）集中管理生命周期 + 状态，工厂函数（`make_*_bot_class`）
注入运行时依赖，种族特化仅需覆盖 3 个 ClassVar + `create_plan()`。
新增第四族只需：
1. 新建 `src/vibecraft/bot/auto_combat/<race>/bot.py`（参考现有三族）
2. 在 `sharpy_adapter.py` 加一行 dispatch
3. 补 alias yaml + strategy yamls
