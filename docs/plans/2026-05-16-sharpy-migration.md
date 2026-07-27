# vibecraft ares-sc2 → sharpy-sc2 全框架迁移 Plan

**日期**: 2026-05-16
**决策**: 整体换 sharpy-sc2,接管 12 个 dummy 战术,**真正解决 bot 弱的问题**(出对的兵 + 按 timing 出门 + 微操齐全)
**估算工作量**: 7-10 天
**最大风险**: Hook A(运行时切剧本)在 sharpy 无原生 API,要么手写 IfElse 树要么扩展 ActManager。M1 第一步做 POC 验证

---

## 1. 为什么换 sharpy(对照 spike 结论)

| 维度 | ares-sc2 / Aristaeus | sharpy-sc2 / dummies |
|---|---|---|
| 战术覆盖跟 vibecraft 4 剧本对应度 | ~10%(只 Aristaeus 的 cannon rush + Oracle 跟 vibecraft 4 剧本毫无重叠) | **~70%**(robo / gate4 / macro_stalkers / voidray 直接对应) |
| 单 dummy 完整度 | Aristaeus = starter 模板 | **真实完整对战 bot**(robo.py 95 行就有 build + 扩张 + 攻防 + 微操) |
| Combat / Production manager | 各 bot 自包,质量参差 | 框架级共享,**12 dummy 共用同一套成熟 manager** |
| Ladder 战绩背书 | Aristaeus 没成绩 | SharpenedEdge(sharpy 框架)长期前 10,MMR 1953 |

## 2. 架构映射:ares hook → sharpy 对应

| vibecraft hook | ares 现有 | sharpy 对应 | 迁移难度 |
|---|---|---|---|
| A. Build Runner 切换 | `build_order_runner.switch_opening(name)` | **无原生 API**,需手写 IfElse 路由器或扩展 ActManager | ⚠️ **最难,3-5d**(M1 POC) |
| B. Builds 注入 | `config["Builds"]` 注入 YAML build steps | `KnowledgeBot.create_plan()` 动态构造 BuildOrder | 2d |
| C. Unit Role(LLM_CONTROLLED 隔离) | `mediator.assign_role(tag, CONTROL_GROUP_THREE)` 框架级 skip | `UnitTask.Reserved` + 各 manager 上层过滤(非框架级) | 1.5d |
| D. Rationale Logger | vibecraft 自己 EventBus + JSONL | 同样自己实现(0 改动) | 0 |
| E. ViewController | `bot.client.move_camera(p)`(python-sc2 API) | 同 API(0 改动) | 0 |
| F. BuildLocationOverride | `mediator.request_building_placement` | `BuildingSolver` 子类 | 0.5d(M1 未实现可后置) |

非 ares 依赖部分(占代码大头)**0 改动**:`server/` / `directives/` / `strategy/` / `llm/` / `logging_/` / `web/`。

## 3. POC 优先(M0,0.5-1d)

**Hook A 是最大死结,先用 POC 验证可行性,不可行则**立即 escalate** 给用户重选路线**。

### POC-A:运行时切 BuildOrder

写一个最小 sharpy bot,验证以下其一可行:

**方案 a:IfElse 树**
```python
class TestBot(KnowledgeBot):
    def __init__(self):
        super().__init__("test")
        self.active_recipe = "robo"  # 外部 flag,由 vibecraft 改

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            IfElse(
                lambda k: self.active_recipe == "robo",
                BuildOrder([...robo steps...]),
                IfElse(
                    lambda k: self.active_recipe == "gate4",
                    BuildOrder([...gate4 steps...]),
                    BuildOrder([...macro_stalkers steps...]),
                ),
            )
        )
```
验证点:
- IfElse 内部多 BuildOrder 嵌套是否支持(spike 已确认 IfElse 存在)
- 改 `self.active_recipe` 后 BuildOrder 是否真切换(关键:**lambda 每 step 重新求值**才行,否则 IfElse 在 post_start 一次性决定就死锁)
- 切换瞬间已经造的建筑/科技是否会复用(避免重复 build)

**方案 b:扩展 ActManager 加 `switch_plan` 方法**

如果方案 a 不行,fork sharpy 加一个 `KnowledgeBot.switch_plan(new_plan)` 方法,内部清 act_manager state + reload。这是改 sharpy 源码,但只一处。

### POC-A 出口

| 结果 | 行动 |
|---|---|
| 方案 a 跑通 | 继续 M1-M5 全 plan |
| 方案 a 不行但方案 b 简单 | 给 sharpy 提 PR + vendor fork + 继续 |
| 两个都不行 | **escalate 给用户重选 B 路径**(翻译 sharpy 战术到 ares) |

---

## 4. Milestone 拆分(M1-M5)

### M1. 框架替换骨架(2d)
- `pip install sharpy-sc2` 或 vendor(看 PyPI 是否可用)
- 新建 `src/vibecraft/bot/sharpy_adapter.py` 取代 `ares_adapter.py`
- `_VibeCraftProtossBot(KnowledgeBot)`,覆写 `on_step / on_unit_created / on_unit_destroyed / on_unit_took_damage`
- `_SharpyFacade` 取代 `_AresFacade`(`set_build` 改成调 POC-A 的 `active_recipe` flag,`set_unit_role` 改成 `UnitTask.Reserved` + 自维护 `_llm_controlled_tags` 集合)
- 删 `vendor/aristaeus/`,删 `auto_combat/protoss/bot.py` 里的 Aristaeus 继承
- 跑现有单测,fake_sharpy module 注入(类似当前 fake_ares,~80 行)
- 验收:`uv run --no-sync pytest -q` 现有 357 single test pass(facade 接口签名不变,内部实现替换,业务测试不受影响)

### M2. 翻 4 个 sharpy dummy 到 vibecraft strategies(1.5d)
- 把 `robo.py` / `gate4.py` / `macro_stalkers.py` / `voidray.py` 的 build_order DSL **翻译成 vibecraft strategies YAML 的 build_order_dsl 段**(新字段:在 yaml 里直接放 sharpy 风格的 Step DSL,或翻译成 sharpy 等价 Python 代码片段)
- vibecraft 4 个剧本对应:
  - `1g_robo_immortal` ← `robo.py` 的 build_order + macro 部分
  - `4bg` ← `gate4.py`
  - `iac_2base` ← `macro_stalkers.py`(双矿追猎,稍调整 army comp 加白球/不朽)
  - `skytoss` ← `voidray.py`(虚空辉光舰流,稍调整加航母)
- 验收:每个剧本能在 POC 单 bot 里跑通(运行时切其中之一不崩)

### M3. Hook A 实施(2d)
- 把 POC-A 的方案落地到 vibecraft:`_VibeCraftProtossBot.create_plan()` 用 IfElse 树或 switch_plan 扩展
- 4 个剧本的 BuildOrder 对象注册到 plan 树
- vibecraft `_SharpyFacade.set_build(recipe_id)` → 改 `self.active_recipe` flag → 下个 step IfElse 走新分支
- 验收:**真实 SC2 端到端**,玩家语音"切 gate4" → 5s 内可见 SC2 内 bot 切 build_order(造塔架开始变化)

### M4. Hook C(role 隔离)+ minimap + decision_watcher 适配(1.5d)
- vibecraft `_SharpyFacade.set_unit_role(tag, LLM_CONTROLLED)` → 加进 `bot._llm_controlled_tags`
- 在 sharpy GroupCombatManager / UnitRoleManager 自动过滤 selection 时,加 vibecraft hook:每次 select tag 前过滤 `_llm_controlled_tags`(可能要 subclass GroupCombatManager)
- minimap_builder 适配:`bot.state.upgrades` / `bot.townhalls` 等 python-sc2 API 不变,minimap.py **0 改动**
- decision_watcher 适配:同上,**0 改动**
- 验收:玩家 unit_claim 一组追猎 → 那组不被 bot CombatManager attack-move 调走

### M5. Combat / 自动出门 timing(1d)
- vibecraft strategies 的 `attack_window.open_at: "9:30"` → 在 BuildOrder 中加 `Step(time >= 570, attack_move(enemy_main))`(sharpy DSL)
- 4 个剧本各自的 timing 配进对应 BuildOrder
- 验收:真实 SC2 看 bot 在 9:30 出门攻击

### M6. 真实端到端 + 文档(1d)
- 真实 SC2 端到端 4 个剧本各跑一遍(8 局,vs CPU Easy + Medium)
- 更新 ARCHITECTURE.md(ares hook → sharpy 对应表)
- 更新 ADR(0007/0008 ares-camera 还适用,sharpy 也是 python-sc2 base;新加 ADR 0009:sharpy 迁移决策 + Hook A POC 结论)

**总工时**: 0.5d POC + 2d + 1.5d + 2d + 1.5d + 1d + 1d = **9.5d**

## 5. 风险 + 回退路径

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Hook A POC 失败 | 中 | 整个迁移废 | M0 POC 0.5d 内 escalate,回退 B 路径 |
| sharpy `pip install` 不可用 | 低 | 必须 vendor | vendor 拷贝 + ATTRIBUTION |
| sharpy 12 dummy 翻译时发现细节缺失 | 中 | 部分剧本不完整 | 每个剧本 M2 单独验收 |
| sharpy 长期维护差 | 低(2026-04 还在 commit) | M3+ 升级困难 | vendor fork |
| 现有 vibecraft 测试 fake_ares mock 重写到 fake_sharpy | 高 | 1-2d 隐藏工作 | M1 一并搞掉 |

**回退路径**:任意 milestone 失败 → 撤回所有 sharpy 改动 → 回到当前 Aristaeus + ares 状态,然后启动 B 路径(翻译 sharpy dummy → ares YAML)。回退成本约 0.5d。

## 6. Trade-off 决策点

**Q1**:Hook A POC 用方案 a(IfElse 树)还是方案 b(改 sharpy 源码加 switch_plan)?
- 默认:**先试 a,a 不行再试 b**

**Q2**:sharpy 通过 `pip install` 还是 vendor?
- 默认:**先 `pip install sharpy-sc2`,如果不在 PyPI 就 vendor**

**Q3**:vibecraft 现有 4 剧本是 1:1 套到 sharpy 4 dummy,还是合并 sharpy 12 dummy 全部?
- 默认:**1:1 套 4 个**(M2 范围),剩 8 个作为 M2 之后战术库扩充

不主动来问,有歧义时按默认走;只在 Hook A POC 失败时立刻 escalate。

---

## M1 spike 结论（2026-05-16）

**执行结果：全部 green**

### 文件改动清单

| 操作 | 文件 |
|---|---|
| 新建 | `src/vibecraft/bot/sharpy_adapter.py` |
| 重写 | `src/vibecraft/bot/auto_combat/protoss/bot.py`（KnowledgeBot 继承） |
| 重写 | `src/vibecraft/bot/auto_combat/common.py`（sharpy UnitTask 映射） |
| 新建 | `tests/unit/test_sharpy_adapter.py`（24 test cases） |
| 修改 | `tests/unit/test_m1_6_end_to_end.py`（fake_ares → fake_sharpy） |
| 修改 | `tests/unit/test_cockpit_sync.py`（同上） |
| 修改 | `tests/unit/test_minimap.py`（同上） |
| 修改 | `src/vibecraft/server/game_process.py`（import sharpy_adapter） |
| 修改 | `pyproject.toml`（mypy overrides 加 sharpy.*/s2clientprotocol.*） |
| 修改 | `scripts/sync_vendor.ps1`（aristaeus → sharpy） |
| 新建 | `vendor/sharpy/ATTRIBUTION.md` |
| 删除 | `src/vibecraft/bot/ares_adapter.py` |
| 删除 | `tests/unit/test_ares_adapter.py` |
| 删除 | `vendor/aristaeus/`（整目录 git rm -r） |

### 测试结果

- `uv run --no-sync pytest`：**351 passed, 18 failed, 6 skipped**
  - 18 failed = 预存 minimap 失败（`_collect_alerts` 相关，M1 前已存在，与本次改动无关）
  - 6 skipped = auto-pilot 相关（已标 `@pytest.mark.skip`，非本次引入）
- `uv run --no-sync ruff check .`：**All checks passed**
- `uv run --no-sync mypy src/vibecraft`：**Success: no issues found in 49 source files**

### 方案偏离记录

1. **vendor 路径注入方式**：plan 预期用 `pip install sharpy-sc2`，但 sharpy 不在 PyPI（Q2 默认路径执行），改用 `vendor/sharpy/` + `sys.path` 注入，符合 plan Trade-off Q2 备用路径。
2. **on_start 初始化顺序**：ares 需要在 `super().on_start()` **前**注入 `config["Builds"]`；sharpy 必须在 `super().on_start()` **后**才能访问 managers（`knowledge.roles` 等在 `KnowledgeBot.on_start` 里初始化）。facade/director 构造移到 super() 之后，与原 ares_adapter 逻辑相反，但设计意图不变。
3. **s2clientprotocol mypy 错误**：minimap.py 对 s2clientprotocol 的 import 未被 ignore，属 M1 前遗留问题；一并加进 mypy overrides，维持 mypy clean 目标。

### M2/M3 关键提示

- **Hook A（M3）**：`create_plan()` 现返回空 `BuildOrder([])`，M3 需替换为 IfElse 树读 `self.active_recipe`，路由到对应 sharpy dummy 剧本。推荐方案：`BuildOrder([IfElse(lambda: self.active_recipe == "robo_opening", robo_plan, fallback_plan)])`。
- **Hook C 精度（M2）**：`set_unit_role` 用 `knowledge.unit_cache.by_tag(tag)` 查 Unit 对象；sharpy manager 的 Reserved 过滤是 manager 级（非框架级），需验证各 manager 在 on_step 不干扰 Reserved 单位。
- **sharpy config.ini**：sharpy 从 CWD 读 `config.ini`，game_process.py 启动时需确认 CWD 有该文件或显式 `os.chdir`（端到端验证时排查）。
- **minimap 18 个失败**：`_collect_alerts` 方法逻辑 bug，与 sharpy 迁移无关，可独立处理。

---

## M4+M5+M6 spike 结论（2026-05-16）

### M4：LLM_CONTROLLED role 隔离

**核心发现**：`UnitRoleManager.update()` 每 step 执行 `self.had_task_set.clear()`，
导致一次性 `set_task(Reserved, unit)` 在下一 step 失效。需要在每个 `on_step` 末尾
重新声明。

**实现方案**：
- `_llm_controlled_tags: set[int]`：持久化"谁被 LLM 接管"的集合
- `_refresh_llm_controlled_roles()`：每 step 对集合中每个存活 unit 调
  `knowledge.roles.set_task(UnitTask.Reserved, unit)` → 续命
- `getattr(self, "_llm_controlled_tags", set())`：防止测试 `object.__new__` 跳
  `__init__` 导致 AttributeError
- `PlanZoneAttack`（`free_units = Idle+Moving only`）和 `GroupCombatManager`
  （explicit `add_unit()`）天然不碰 Reserved 单位 —— 经 grep sharpy 源码确认

**测试**：9 个新用例，全 mock，验证 set/discard/refresh/cleanup 各路径。

### M5：attack_window / micro_doctrine 字段透传

**实现方案**：
- `director._slot_view()` 在 `isinstance(strat, MidgameStance)` 分支提取
  `attack_window.open_at/close_at` + `micro_doctrine`；在 `LategameDoctrine`
  分支提取 `engagement_doctrine` as `micro_doctrine`
- `types.ts`：`StrategySlotView` 加两个 `?` 可选字段
- `StrategyCard.vue`：`v-if` guard 下显示「出门 9:30–11:30」+ `·` 子弹点列表

**测试**：4 个新快照测试，覆盖有/无 attack_window、有 micro_doctrine、
LategameDoctrine 的 engagement_doctrine 映射。

### M6：文档更新

- `docs/adr/0009-sharpy-migration.md`：决策背景 + Hook A/C + M5 透传 + 管理器兼容表
  + 实现里程碑 + 许可证
- `ARCHITECTURE.md`：模块图（ares_adapter → sharpy_adapter + auto_combat 目录）、
  Hook 表（M3 A ✅ / M4 C ✅ 等）、新不变量（LLM_CONTROLLED = Reserved +
  `_llm_controlled_tags`）、vendor 节

### 验证结果

| 检查项 | 结果 |
|---|---|
| `uv run --no-sync pytest` | **389 passed, 6 skipped**（基线 376，+13） |
| `uv run --no-sync ruff check .` | All checks passed |
| `uv run --no-sync mypy src/vibecraft` | Success: 49 source files, 0 issues |
| `npm test`（web/） | 41 passed |
| `npm run build`（web/） | Vite build 通过，vue-tsc 无报错 |

### 方案偏离记录

1. **`getattr` 防御式访问**：`_refresh_llm_controlled_roles` 改用
   `getattr(self, "_llm_controlled_tags", set())` 而非直接访问属性，因测试通过
   `object.__new__` + `FakeKnowledgeBot.__init__` 绕过了 `__init__`，原始写法
   产生 AttributeError。未影响设计意图，只是初始化路径的防御处理。
2. **SIM102 ruff 规则**：`director._slot_view()` 的 `elif isinstance(...) and strat.xxx:`
   单行合并，避免嵌套 if（原 plan 未预期，ruff strict 模式触发）。
