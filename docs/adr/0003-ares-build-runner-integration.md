# ADR 0003：ares build runner 集成方案（M1.5）

**日期**：2026-05-14
**状态**：已接受

---

## 背景

M0b 的 `_AresFacade.set_build` 写的是 `self.bot.build_runner.set_build(build_name)`，
这是对 ares API 的错误假设。M1.5 通过 spike 确认了真实 API，并完成了翻译层实现。

---

## Spike A 结论：ares step 命令格式

**问题**：ares build order parser 里，protoss 的 supply / gas / expand 应用语义命令
（`SUPPLY`/`GAS`/`EXPAND`）还是直接结构名（`PYLON`/`ASSIMILATOR`/`NEXUS`）？

**结论**：读 `build_order_parser.py:_parse_string_command` 确认，ares parser 的解析
优先级为：
1. `UnitID[cmd.upper()]`（直接结构/单位名）
2. `UpgradeId[cmd.upper()]`（升级 ID）
3. `BuildOrderOptions[cmd.upper()]`（`SUPPLY`/`GAS`/`EXPAND`/`WORKER_SCOUT`/`CHRONO` 等）

vibecraft 的 step 里已经用了 ares 的 UnitID 名（`Pylon`/`Assimilator`/`Nexus`），
**直接大写后传给 ares**，不需要语义别名。例外：
- `send_probe` → `WORKER_SCOUT`（ares BuildOrderOptions）
- `research X @chrono` → `X` + 单独的 `CHRONO @ <building>` 步骤

---

## Spike B 结论：config["Builds"] 注入时序

**问题**：`BuildOrderRunner` 是什么时候构造的？config 注入必须在它之前。

**结论**：读 `ares/main.py:332` 确认，`BuildOrderRunner.__init__` 在 `AresBot.on_start()`
的末尾调用（`super().on_start()` 的末尾）。因此注入必须在 `super().on_start()` 之前：

```python
async def on_start(self) -> None:
    # 1. 先注入 config["Builds"]
    if strategy_library is not None:
        ...inject openings...
    # 2. 再调 super()，BuildOrderRunner 在此处构造并读 config
    await super().on_start()
    # 3. super() 之后 facade + director 才创建
    self.facade = _AresFacade(self)
    self.director = director_factory(self.facade)
```

---

## 实现决策

### 1. 翻译层独立模块（`bot/build_translator.py`）

将翻译逻辑放在独立的纯函数模块，**不 import ares/sc2**，完全可单测。
三个公开函数：
- `translate_opening_to_ares_steps(opening)` → `list[str]`
- `opening_to_ares_builds_entry(opening)` → `dict`
- `openings_to_ares_config_builds(openings)` → `dict`（即 `config["Builds"]` 的内容）

### 2. @chrono modifier 翻译

vibecraft schema 用 `@chrono` 作为步骤 modifier，ares 用独立的 `CHRONO @ <building>` 步骤。
翻译时，遇到 `research X @chrono` 插入两条 ares 步骤：

```
"22 research WarpGateResearch @chrono"
  → ["22 WARPGATERESEARCH", "22 CHRONO @ CYBERNETICSCORE"]
```

映射表（upgrade → 研究建筑）硬编码在 `build_translator.py:_UPGRADE_TO_BUILDING`，
未知 upgrade 保底回 `NEXUS`。

### 3. make_bot_class 新增可选参数 strategy_library

`make_bot_class(director_factory, strategy_library=None)` —— `strategy_library=None`
时行为与 M0c smoke 相同（向后兼容）。传入后在 `on_start` 注入所有 `OpeningBuild`。

### 4. midgame/lategame 不注入

M1.5 仅接 `opening_build` kind，这是 ares build runner 能管的。
`MidgameStance` / `LategameDoctrine` 由 vibecraft 自己的 Board/DSL 管理（M2+）。

---

## 遗留（M1.6 / M2+）

- `switch_opening` 的 `remove_completed=True` 默认行为：切换 build 时 ares 会试图
  移除已完成步骤。真实游戏中的时序/行为需 M1.6 端到端校准。
- `set_build` 被调时 `build_order_runner` 必须已构造（只在 `on_start` 之后有效）。
  `Director._apply_to_facade` 调 `set_build` 的时机是 Board commit 后，此时
  bot 已在运行，安全。
- scout_at 的 `send_probe` target（`enemy_natural`）被忽略了 —— ares `WORKER_SCOUT`
  自动寻路到敌方主基地附近，不能精确指定"自然扩张"位置。M2+ 如需精确控制，改用
  `mediator.select_worker` + 手动 move 指令。
- `build @chrono` modifier（在 build 结构本身上打 chrono）目前翻译为 `CHRONO @ <structure>`，
  ares 的 chrono 步骤需要结构已就绪。边缘情况未在 M1.5 端到端验证，留 M1.6 校准。
