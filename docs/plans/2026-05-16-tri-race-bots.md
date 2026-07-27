# tri-race-bots 设计与实现计划

> 创建日期：2026-05-16

## 背景

VibeCraft 目前只有神族 bot（`_VibeCraftBot` 继承裸 `AresBot`）。
本 plan 拆成三步：

- **S1**：重构 `ares_adapter.py`，抽三族共享代码到 `auto_combat/common.py`，`make_bot_class` 加 `race` 参数 dispatch。
- **S2**：vendor `august-k/Aristaeus`，神族 bot 继承 `aristaeus.MyBot` 而非裸 `AresBot`。
- **S3**（留后）：虫族 / 人族 bot，接其他开源 bot。

---

## S1 spike 结论

**完成。** 从 `ares_adapter.py` 抽出到 `src/vibecraft/bot/auto_combat/common.py`：

- `_log_move_camera_done(task)` —— move_camera done callback
- `build_role_map()` —— 构造 vibecraft UnitRole → ares AresUnitRole 映射表（lazy import，ares 装了才能调）
- `run_command_with_echo(director, text, now, echo_callback)` —— echo 辅助协程，echo_callback 改为显式参数（原版是闭包捕获）

`ares_adapter.py` 的 `make_bot_class` 加 `race: str = "Protoss"` 参数；末尾 dispatch：`Protoss` → `_VibeCraftBot`；其他 → `NotImplementedError`。

测试结论：355 tests passed，ruff 干净，mypy 干净。

---

## S2 spike 结论

### vendor 路径 + 版本

- 路径：`vendor/aristaeus/`
- upstream：`https://github.com/august-k/Aristaeus`
- clone commit：`d3d8928b0901ae4aeece261e237dd62a289185b1`
- `.git` 已删；`LICENSE` 已保留；`ATTRIBUTION.md` 已写

### Aristaeus 主类

- 文件：`vendor/aristaeus/bot/main.py`
- 类名：`MyBot(AresBot)`

### CONTROL_GROUP 冲突（重要）

Aristaeus 的 `bot/managers/cannon_rush_manager.py` 使用：

```python
self.cannon_placers = UnitRole.CONTROL_GROUP_ONE   # 炮塔rush探机
self.chaos_probes   = UnitRole.CONTROL_GROUP_TWO   # chaos探机
```

**vibecraft 原映射 `LLM_CONTROLLED → CONTROL_GROUP_ONE` 冲突**。

**决策**：改为 `LLM_CONTROLLED → CONTROL_GROUP_THREE`（Aristaeus 未使用）。
位置：`src/vibecraft/bot/auto_combat/common.py` 的 `build_role_map()`。

### config["Builds"] 注入

Aristaeus 的 `config.yml` / `protoss_builds.yml` 通过 ares 标准路径加载；
`bot/main.py` 没有自己写 `config["Builds"]` 注入逻辑。
vibecraft 的注入用 `.update()`，会覆盖/合并，不会被 Aristaeus 竞争。

### import 解决方案

`vendor/aristaeus/` 不在 Python import 路径里。选择 **sys.path 注入**：
在 `_VibeCraftProtossBot` 模块顶部用 `importlib.util` 或直接 `sys.path.insert`，
在运行时把 `vendor/aristaeus` 注入到 `sys.path`。

具体实现：`src/vibecraft/bot/auto_combat/protoss/bot.py` 顶部做一次
`sys.path.insert(0, str(Path(__file__).parents[5] / "vendor" / "aristaeus"))`，
然后正常 `from bot.main import MyBot`。

### Aristaeus on_step 行为

Aristaeus 的 `on_step` 会：
1. `super().on_step(iteration)` —— ares 自身
2. `self.register_behavior(Mining())` —— 自己管采矿
3. `await self.production_manager.update(iteration)` —— 生产

vibecraft 覆写 `on_step` 时先 `await super().on_step(iteration)`（走 Aristaeus 逻辑），
再加 vibecraft 的 down_q 消费 + minimap 推送 + director.on_tick + facade.drain。

因此 **vibecraft 不再自行注册** Mining / AutoSupply / BuildWorkers 等 macro behavior；
Aristaeus 的 ProductionManager 接管生产。

---

## S2 实现文件清单

| 文件 | 动作 |
|---|---|
| `vendor/aristaeus/` | clone + 删 .git |
| `vendor/aristaeus/ATTRIBUTION.md` | 新建 |
| `scripts/sync_vendor.ps1` | 新建（半自动 upstream sync） |
| `pyproject.toml` | ruff/mypy exclude vendor |
| `src/vibecraft/bot/auto_combat/common.py` | CONTROL_GROUP_THREE 修改 |
| `src/vibecraft/bot/auto_combat/protoss/__init__.py` | 新建 |
| `src/vibecraft/bot/auto_combat/protoss/bot.py` | 新建 `_VibeCraftProtossBot` |
| `src/vibecraft/bot/ares_adapter.py` | Protoss dispatch 指向新类 |
| `tests/unit/test_ares_adapter.py` | fake_ares 加 fake `aristaeus.MyBot` |
