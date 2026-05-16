# ARCHITECTURE.md

VibeCraft 当前代码里**实际**的形态。

- **WHY**（为什么这样设计）→ `docs/plans/2026-05-14-vibecraft-design.md`（14 节真理源）
- **WHAT IS**（代码现状，跟代码同步）→ 本文档
- **WHAT NEXT**（待办 + 进度）→ `TASKS.md`
- **HOW TO WORK**（约定 + 指针）→ `CLAUDE.md`

本文档每次结构性改动（新增子包 / 改变数据流 / 改不变量）都要同步。

---

## 模块图

```
src/vibecraft/
├── directives/          # 纯数据 + 状态机
│   ├── models.py        # Directive + 各 Payload 多态（pydantic v2）
│   ├── types.py         # DirectiveType / StageKind / IssuedBy / 优先级映射
│   ├── scope.py         # ClaimRecord / ScopeKind（unit_claim 互斥账本）
│   ├── task.py          # primary_action / reaction 任务表示
│   └── board.py         # DirectiveBoard：commit delay + 三槽 + overlays + claims
├── strategy/            # 纯数据
│   ├── models.py        # opening_build / midgame_stance / lategame_doctrine
│   ├── library.py       # StrategyLibrary：剧本仓库抽象（不要直接读 YAML 路径）
│   └── aliases.py       # AliasTable：中文 → canonical + verb 消歧
├── dsl/                 # 纯函数
│   ├── lexer.py + parser.py + ast_nodes.py + evaluator.py + errors.py
│   └── # 阶段转移条件 / 剧本里 if 谓词的求值
├── llm/                 # 纯异步（不碰 SC2）
│   ├── provider.py      # LLMProvider Protocol
│   ├── anthropic_provider.py    # Claude 实现
│   ├── prompt.py        # ParseContext + system / few-shot / tool schema 拼装
│   ├── schema.py        # IntentParseResult / AmbiguousParse / ParseError
│   └── parser.py        # IntentParser：编排 + ValidationError 转 ParseError
├── logging_/            # JSONL sinks，async-safe
│   ├── types.py         # Event / EventKind
│   ├── sinks.py         # 文件 sink + 内存 sink（测试用）
│   └── session.py       # GameSession（一局一个 session 目录）
└── bot/                 # 唯一一个会碰 sharpy-sc2 的子包（M1+：ares → sharpy）
    ├── facade.py        # Sc2Facade Protocol + UnitRole + FakeFacade（测试用）
    ├── director.py      # 中央编排器，下面单独讲
    ├── sharpy_adapter.py  # make_bot_class() 工厂；仅真实对局 import（见 ADR 0009）
    └── auto_combat/
        ├── common.py    # build_role_map() + run_command_with_echo()
        └── protoss/
            └── bot.py   # _VibeCraftProtossBot（KnowledgeBot 子类）+ _SharpyFacade

vendor/sharpy/           # sharpy-sc2 源码（MIT，vendor 因不在 PyPI；见 ADR 0009）
```

`bot/sharpy_adapter.py` 之外，**所有模块都不 import sharpy / sc2 / burnysc2**。
mypy override 把它们当 missing-imports，pyproject 已配。所有单测都用
`FakeFacade`，不需要真 SC2。

---

## 运行时数据流

```
玩家话语 ──> IntentParser.parse() ──> Directive[]
                                        │
                                        ▼
                            Director._submit_directives(now)
                                        │
                       ┌─── is_view_directive? ──── yes ──> Facade.move_camera/follow/zoom（**绕过 Board**）
                       │
                       no
                       │
                       ▼
                DirectiveBoard.submit()  ──> _in_flight[id] = directive
                                        │
                                        │ (每 tick)
                                        ▼
                       Director.on_tick(now) ──> board.tick(now) ──> BoardEvent[]
                                        │
                                        ├──> GameSession.log_event(...)        （全量 JSONL）
                                        │
                                        └──> 仅当 COMMITTED ──> Director._apply_to_facade(d)
                                                                       │
                                                                       ▼
                                                            Sc2Facade.set_build / set_unit_role /
                                                            set_production_override / execute_unit_action / ...
```

---

## 关键不变量（坏了任何一条都是 bug）

- **`Director` 是唯一调用 `Sc2Facade` 的地方**。其它模块都通过 Director 间接生效。
  添新 directive 类型时，**必须**在 `Director._apply_to_facade` 加分派分支 +
  在 `directives/types.py` 注册类型枚举 + 在 `directives/models.py` 加 Payload。
- **VIEW directive 绕过 Board**：相机操作不走 1.5s commit delay，不占 overlay 槽。
  `directives/types.py::is_view_directive()` 是判定函数。
- **`LLM_CONTROLLED` UnitRole 映射到 sharpy `UnitTask.Reserved`**（M1+，原 ares
  `CONTROL_GROUP_ONE` 已废弃）：`set_unit_role(tag, LLM_CONTROLLED)` 同时写入
  `bot._llm_controlled_tags`，每 step `_refresh_llm_controlled_roles()` 重新声明
  Reserved，确保 sharpy `UnitRoleManager.update()` 每帧清 `had_task_set` 后角色不丢。
  `PlanZoneAttack` 的 `free_units`（Idle+Moving）天然不含 Reserved，Reserved 单位
  不会被拉去出门攻击或守基地（见 ADR 0009 §Hook C）。
- **`_VibeCraftProtossBot` 继承 `KnowledgeBot`，`create_plan()` 返回
  `BuildOrder(IfElse(...))` 树**：`active_recipe` flag 控制路由，`set_build()` 写入
  后下一个 step IfElse 立即生效（lambda 每 step 重新求值）。见 ADR 0009 §Hook A。
- **IntentParser 任何异常都不抛**：失败一律返回 `ParseError`，bot 状态完全不
  动。`anthropic` SDK 异常、`ValidationError`、超时、限频都走这条路。
- **logging 是 first-class**：每条 LLM 调用 / 每个 Board 事件 / 每次 Facade
  写都进 `logs/<game_id>/events.jsonl`。新增 directive 路径时不要忘了 emit
  `Event`。
- **strategies 走 `StrategyLibrary.get(id)`**：不要在业务代码里 `yaml.load()`
  剧本路径。换 store backend（DB / 远程）就是换 library 实现。

---

## 6 个 hook 点（设计文档 §3.2）与 sharpy 实现映射（M1+ 已迁移）

| Hook | 设计文档概念 | facade 上的方法 | sharpy 实现 | 状态 |
|---|---|---|---|---|
| A Build Runner | 切换当前 build order | `set_build(build_name)` | `active_recipe` flag + IfElse 路由树（ADR 0009） | ✅ M3 完成 |
| B OverrideMediator | 强制某种单位 / 升级 | `set_production_override` / `set_tech_override` / `set_expansion_override` | noop，留 M5+接 sharpy ActUnit override | 占位 |
| C Unit Role | 把单位拉出 Manager 视野 | `set_unit_role(tag, role)` | `UnitTask.Reserved` + `_llm_controlled_tags` 每 step refresh（ADR 0009 §Hook C） | ✅ M4 完成 |
| D Rationale Logger | 记录决策原因 | （Director 自用 GameSession）| 同 M1，0 改动 | ✅ |
| E ViewController | 相机操作 | `move_camera` / `follow_unit` / `set_camera_zoom` | python-sc2 `client.move_camera`（ADR 0008，暂存+drain） | ✅ |
| F BuildLocationOverride | 指定建造点 | `set_build_location_override` | noop，留 M5+接 sharpy BuildingSolver | 占位 |

vendor 段：sharpy-sc2 MIT，vendor 路径 `vendor/sharpy/`（不在 PyPI），
`sys.path` 注入（lazy，单测 mock sys.modules 绕开）。见 `vendor/sharpy/ATTRIBUTION.md`。

---

## 测试组织

- `tests/unit/test_smoke.py`：装得上 + 能 import。
- `tests/unit/test_directives.py`：Board 状态机、三槽切换、commit delay、
  overlay 优先级、unit_claim 互斥。
- `tests/unit/test_director.py`：Director ↔ Board ↔ FakeFacade 端到端
  （仍是 mock；FakeFacade 全程记录调用做断言）。
- `tests/unit/test_llm_parser.py`：IntentParser 用 stub provider 跑各类
  outcome（success / ambiguous / error / 超时 / schema 失败）。
- `tests/unit/test_{dsl, strategy, logging}.py`：纯模块单测。
- `tests/integration/`、`tests/e2e/`：default 跳过（pytest mark），分别要
  python-sc2 mock 和真实 SC2 客户端。M0b 暂时没用上。
- `scripts/smoke_test.py`：M0c 端到端，单独脚本（不是 pytest），详见
  `docs/m0-smoke-runbook.md`。
