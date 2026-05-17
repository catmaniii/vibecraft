# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循
[PEP 440](https://peps.python.org/pep-0440/)。

VibeCraft 的 milestone 与版本对应（详见 `docs/plans/2026-05-14-vibecraft-design.md` §12）：

| Milestone | 版本号 | 含义 |
|---|---|---|
| M0a/M0b 完成 | `0.1.0a1` | 脚手架 + 全部"无 SC2"模块 + 单测 |
| M0c 完成 | `0.1.0a2` | 真实 SC2 smoke 验证通过（"不动的叉子"）|
| M1 完成 | `0.1.0a3` | 手机说话 → bot 切 1 个剧本 |
| M2 完成 | `0.1.0a4` | Directive Board 完整 + 3 剧本 |
| M3 完成 | `0.1.0a5` | 手机驾驶舱完整 |
| M4 完成 | `0.1.0b1` | LLM 解析 > 90% 正确率 |
| M5 完成 | `0.1.0` | MVP RC：vs Hard AI 调优达标 |

---

## [Unreleased]

M3：完整驾驶舱（剩余 PWA UI 精修）+ L4 production override sharpy 真出兵 wire +
phase stepper 精确进度。

---

## [0.1.0a4] - 2026-05-17

**M2 完成。** four-layer 指令架构（L1 宏观 / L2 战术 / L3 standing / L4 产能）
全套链路实施完成。done_when 8-kind discriminated union + task_monitor 完成判定 +
LLM prompt 教 4 层分类 + 4 个 PWA cards + EventBus + NamedSpotRegistry + sharpy
让位机制 全部 work。**`v0.1.0a4`** = M2 出口（设计文档 §13 / ADR 0010 phasing 表）。

本次发布单 session 一气呵成（2026-05-17，~5h wall-clock），主 agent (Opus) +
14 个 Sonnet subagent (worktree isolation) 协作模式 verified scalable。

### 新增 (Added)

- **L2 `TacticalObjective` directive type**（11 verb：attack/defend/scout/expand/
  harass/drop/vision/raze/retreat/regroup/split）
- **DoneWhen discriminated union（10-kind）**：`unit_count_built_since` /
  `tech_done` / `expansion_count` / `target_destroyed` / `own_army_size_ratio` /
  `vision_acquired` / `enemy_killed_in_area` / `time_elapsed_since` + 复合
  `any_of` / `all_of`
- **`task_monitor` 完整实现**：每 sharpy step 检查 in-flight directive 完成判定 +
  EventBus-driven (UNIT_CREATED/UPGRADE_COMPLETE) 高效累计 counter + game-state
  polling (vision/army_ratio/target_destroyed/expansion_count/time_elapsed)
- **`EventBus`**：vibecraft 自建独立 pub/sub 层，11 个 python-sc2 lifecycle hook
  publish 到统一 bus，task_monitor / DecisionWatcher 等 subscriber 用 filter 订阅
- **`NamedSpotRegistry`**：15 个已知 spot（natural/third/main/enemy_* + *_ramp +
  *_gas 变种）+ `resolve(name, bot)` 走 sharpy zone_manager + `closest_named_spot`
  反向查找（publisher area inference 用）
- **sharpy 让位机制**：persistent unit_claim (standing order) 在 Director 端
  resolve selector → set_unit_role(LLM_CONTROLLED) → revoke 时 release_unit_role
  归还。`board.revoke()` 扩支持 committed overlay
- **Director `production_overrides` / `standing_orders` lists**：按 directive type
  + persistent 字段路由
- **3 个新 PWA cards**：`StandingOrdersCard.vue` / `ProductionOverridesCard.vue` /
  `TacticsCard.vue`，每张含撤销按钮（emit revoke → ws revoke_directive 帧）
- **snapshot 4 新字段**：`standing_orders` / `production_overrides` /
  `active_tactics` / 各 directive 的 `done_when`
- **`revoke_directive` 上行帧** + ws/bot wire（玩家撤销路径完整）
- **LLM prompt 教 done_when**：System 段 加 11 verb + 10 kind 白名单 + 4 层分类
  规则 + 11 个 few-shot 例子（覆盖 done_when 典型 pattern + 边界 case：复合 L1+L3
  / L2 engagement+done / 撤销 / 含糊 / unit_count_hint）
- **IntentParser validate retry**：done_when 字段 ValidationError 时回灌 LLM
  重写 1 次；2 次仍失败降级 EPHEMERAL + echo 告诉玩家
- **directives.jsonl 生命周期落盘**：submitted / committed / released / rejected /
  revoked，加 JsonlSink `buffering=1` line-buffered 修子进程空 bug
- **ADR 0010 完整记录**：4 决策 + 30+ corner case Implementation Notes

### 修正 (Fixed)

- **M4 e2e schema gap**（v0.1.0a3 验证发现）：`UnitClaimPayload` 加 `persistent: bool`，
  `Target.kind` 接受 `building_tag` / `named_spot`，`Selector` `extra="forbid"`
  禁 `count` 字段。LLM prompt 同步用合法字段
- **vision_acquired 22x bug**：原用 step count 累加（sharpy step ≈ 0.045s），
  改用 wall-clock ts diff（`_vision_first_visible_ts[id]`）
- **enemy_killed_in_area filter 缺 payload.area**：`_publish_unit_destroyed`
  加 area inference（closest_named_spot max_distance=15）
- **target_destroyed natural/third/main P3 hardcoded 返回 False**：P5 改成走
  NamedSpotRegistry resolve `enemy_natural` 等 + enemy_structures.closer_than
- **CockpitView 资源条占位删除**（SC2 游戏内置 HUD 已有）
- **JsonlSink `buffering=1` line-buffered**：修子进程 spawn 时 jsonl 一直 0 字节
  bug（block buffered + kill 前没 flush）
- **顺手修 baseline RUF012**：`_UNIT_ZH` + `_TACTICAL_VERB_ZH` 改 ClassVar

### 已知未做 / known issues

- **3 个 cross-test pollution flaky tests**：`test_loads_real_strategies` /
  `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`
  单跑永远 PASS，full suite 偶发 fail。不阻塞产线。**未来用** pytest-forked
  或 grep module-level mutable state
- **真实长 SC2 对局 `directive_completed` event verify**：fast mode bot 在 30s
  内被 VeryHard AI 打死，timer-based directive 没机会触发。需要真实 SC2 +
  surviving 几分钟的对局验
- **L4 production override sharpy 真出兵 → M3 范围**：P3 task_monitor 检测
  L4 done_when，sharpy 端不主动响应 production_override。需要 wire
  `bot.facade.set_production_target`

### 验证

```bash
# 全单测
.venv/Scripts/python.exe -m pytest        # 597 passed, 6 skipped
cd web && npm test                         # 50 passed
cd web && npm run typecheck                # clean

# e2e schema gap fix（P1.6 verify）
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal --inject "那个农民守气矿别动" \
  --inject-after 5 --seconds 60
# → ECHO 不再 [解析失败]，LLM 输出 persistent=true + named_spot

# e2e jsonl content（P6 sink fix verify）
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal --inject "30秒后撤" \
  --inject-after 5 --seconds 60
# → directives.jsonl 真有 submitted + committed 记录
```

---

## [0.1.0a3] - 2026-05-17

**M1 完成。** 真实 SC2 端到端验证通过 —— 切剧本端到端链路成立。**fast mode** smoke
跑 ~60s wall-clock，force `1g_robo_immortal` 默认 opening + inject「切 4BG」→
SNAPSHOT 从 `opening=1g_robo_immortal` → `opening=4bg`，配套 `strategy.set` +
`directive.committed` 两条事件全到位。inject「切叉球一波」→ `strategy.phase_change`
(opening→midgame) + `strategy.set` + SNAPSHOT `midgame.attack_window={6:15-7:30}` +
5 条 `micro_doctrine` 完整透传。链路 `down_q → IntentParser → LLM (DeepSeek V4) →
Director → board commit (1.5s grace) → STRATEGY_CHANGED → snapshot push` 全通。

### 新增 (Added)

- **sharpy 迁移完整**（M1-M6，全合并 main）：sharpy KnowledgeBot 替代 ares-sc2 作为 bot
  框架；LLM_CONTROLLED role 隔离（M4，9 个 mock 单测）；attack_window / micro_doctrine
  字段透传到 snapshot（M5）；ADR 0009 记录决策
- **WS 多路复用 + auto-pilot + cockpit-sync + minimap 拖拽视野**：view 通道（高频，
  view_move + minimap + drain）/ bot 通道（低频，sharpy super + ratio=5），iteration
  remap 给 sharpy 自己的 namespace；PWA 驾驶舱按 §9.5 重排 + 推荐 / 硬转确认 / 多卡片
- **4bg 流程优化**（gate4_pressure 自定义 plan）：3 BG 等折跃 ≥50% + 矿 ≥450 一次性下；
  ForwardSupportPylonGateway（农民前线修 PY+BG）；首波 4 追猎即出门火力侦察
- **iac_2base 数据对齐 Spawning Tool**（叉球一波 all-in）：6:15 timing + 7 BG +
  chargelot 主力 + 2 不朽 + 2-4 白球；加别名「叉球一波」「IAC一波」「白球冲锋叉一波」
- **四层指令架构设计**（M2/M3 蓝图）：
  `docs/plans/2026-05-16-four-layer-commands-design.md` 定义 L1 宏观 / L2 战术 /
  L3 standing / L4 产能 四层 directive；P1-P6 分期实施
- **headless_smoke 测试基础设施**：`--fast` / `--initial-opening <id>` /
  `--inject <text>` / `--inject-after N` / snapshot + event 帧解析
- **驾驶舱真实截图嵌入 USER_GUIDE**（780×1908，mock 数据演示）；
  md_to_pdf 加 base href + img CSS 让 PDF 正确渲染

### 修正 (Fixed)

- **项目改名 voicecraft → vibecraft**：源码包路径、import、CLI、pyproject、
  scripts、web build、PWA 资源、设计文档全部刷新；GitHub repo
  `catmaniii/voicecraft` → `catmaniii/vibecraft`
- **CockpitView 资源条占位删除**：SC2 内置 HUD 已有，手机端不重复占屏
- **README / USER_GUIDE 弱化"语音"主线**：VibeCraft 自己不做语音识别，录音/转字
  外包给手机系统输入法，文本框是核心
- **TASKS.md 顶部「当前状态」段刷新**：之前的"worktree 待合并"已 stale

### 已知未做 / known issues

- **LLM prompt ↔ Pydantic schema 不匹配**：M4 e2e 跑 inject standing order 类指令
  暴露 schema 拒绝 `selector.count` / `target.structure_type` 字段。属 M3/four-layer
  P1 standing order 实施范围
- **完整驾驶舱**：Standing Orders / 快捷栏 / phase stepper 精确进度 / 撤销机制 — M3
- **midgame/lategame 剧本自动转**：当前 auto-pilot 只是通用兜底，不按剧本切 — 转
  four-layer P3 范围
- **造建筑指令** schema — 同上，P1/P3 范围
- **Windows + retail SC2 不能真 headless**：D3D9 在 non-interactive desktop 立刻 Lost；
  Linux SC2 永久卡 4.10。本次 hidden 调研结论 — 接受 SC2 可见，smoke 走"弹窗 + 自动 kill"

---

## [0.1.0a2] - 2026-05-14

**M0c 完成。** 真实 SC2 客户端端到端 smoke 通过 ——「不动的叉子」验证成立：
2 个探机置入 `CONTROL_GROUP_ONE` role 并 `stop()` 后，60 秒监测窗口内零指令、
零移动、`in_role` 全程保持；ares 结算 `Idle worker time: 168.0` 反证 WorkerManager
没有重新接管。设计文档 §3.4 的「唯一存疑点」—— Hook C (Unit Role) 的 role 隔离
机制 —— 核心假设确认成立。

### 修正 (Fixed)

- **`LLM_CONTROLLED` 映射到 ares 的 `CONTROL_GROUP_ONE`**：ares 的 `UnitRole`
  是固定 StrEnum 无法动态加成员；先前假设可以直接传字符串 `"LLM_CONTROLLED"`
  会立即挂。`ares_adapter` 现内置 vibecraft UnitRole → ares UnitRole 映射表。
- **`scripts/smoke_test.py` 用真实 ares API**：`mediator.assign_role(tag=, role=)`
  + `mediator.get_units_from_role(role=, unit_type=)`（按 role 反查池），且 role
  传 ares 真实 enum 而非字符串。
- **`smoke_test.py` 传 Map 对象**：`run_game()` 在 burnysc2 7.1.0 要的是
  `maps.get(name)` 返回的 Map 对象，先前直接传字符串会
  `AttributeError: 'str' object has no attribute 'relative_path'`。
- **`smoke_test.py` enroll 后 `unit.stop()`**：探机开局 0s 自动采矿，不清掉这条
  SC2 引擎默认 order 会被误判成 `received_orders` 异常。enroll 进 role 后立刻
  stop，之后再出现的 order 才真正意味着有 Manager 主动接管。

### 环境校准（端到端踩坑记录）

- **Python 必须 3.11**：`sc2-helper`（ares 间接依赖）只发布到 `cp311` wheel，
  3.12 装不上。已加 `.python-version` 锁定 3.11。
- **ares-sc2 3.7.2 src-layout 打包问题**：`uv_build` backend 把包装进
  `site-packages/src/ares/` 而非 `site-packages/ares/`，`import ares` 找不到。
  修法：在 site-packages 放一个内容为 `src` 的 `.pth` 文件。
- **`sc2_helper` 需手动安装**：不在 ares-sc2 的依赖声明里，但 ares 的
  `combat_sim_manager` 直接 `import sc2_helper`。需 `uv pip install sc2-helper`。
- **Windows Defender 文件锁**：新解压的 `.exe` / `.dll` 会被实时扫描短暂锁住，
  紧接着的命令报 `os error 32` / `DLL load failed`，重试即可（非真错误）。

### 新增 (Added)

- **`.python-version`** —— 锁定 Python 3.11，避免 uv 误用 3.12。

详细安装 / smoke 流程见 `docs/m0-smoke-runbook.md`。

---

## [0.1.0a1] - 2026-05-14

**M0a / M0b 完成。** 所有不依赖真实 SC2 客户端的模块全部实现，126 个单测全过；
`ruff check` / `mypy strict` 干净；测试覆盖率 83.2%。

### 新增 (Added)

- **脚手架**：`pyproject.toml`（uv + hatchling）、`src/` layout、ruff + mypy strict
  + pytest + pre-commit + GitHub Actions CI 模板
- **`directives/`** —— Directive schema + Board
  - 13 种 DirectiveType（strategy_set / production_override / unit_claim / build_at /
    view_move 等）的 discriminated union payload
  - DirectiveBoard：1.5s 固定生效延迟、阶段单向（opening → midgame → lategame）、
    overlay 叠加、unit_claim 互斥、按 issued_by 优先级仲裁
  - ScopeSpec 四种 kind（ephemeral / until / duration / persistent）的过期判定
- **`dsl/`** —— 沙箱安全的条件 DSL（剧本 YAML 里 enter_when / abort_signals / reactions
  用）。手写 recursive descent parser，禁任意函数；支持 `>=`/`<=`/`AND`/`OR`/`NOT`/`in [...]`；
  `game.time` 字符串 (`'M:SS'`) 与浮点秒自动互转
- **`strategy/`** —— 剧本库
  - 三种 kind（OpeningBuild / MidgameStance / LategameDoctrine）的 pydantic schema
  - BuildStep 紧凑三段式 `"<supply> <verb> <object> [@modifier]"` 解析
  - AliasTable：建筑 / 单位 / 升级三组别名 + verb 消歧（如 `build VR` →
    RoboticsFacility，`train VR` → VoidRay）
  - 3 个 MVP 剧本 YAML：`1g_robo_immortal` / `iac_2base` / `skytoss`
  - StrategyLibrary 跨引用校验（opening → midgame → lategame）
- **`logging_/`** —— 结构化 JSONL 日志层
  - GameSession：一场对局一个 `logs/<game_id>/` 目录
  - 8 条 stream（events / commands / directives / decisions / sc2_actions /
    metrics / errors / ws_traffic），每条 JSONL
  - `llm_calls/call_NNN.json` 全量保留每次 LLM 调用的 prompt + response + tokens + latency
- **`llm/`** —— Intent Parser
  - `IntentParser`：4 段 prompt 拼装（System / Strategy Catalog / Few-shot / Dynamic
    context），通过 Anthropic tool_use 强制 JSON 输出 schema
  - `LLMProvider` Protocol + `AnthropicProvider`（`claude-sonnet-4-6` 默认）+
    `MockLLMProvider`（单测专用）
  - 错误处理：timeout / invalid_json / schema_mismatch / unknown_strategy /
    directive_invalid 全部返回 `ParseError`，**bot 状态绝不变**（设计文档 §7.6）
  - `AmbiguousParse`：confidence < 0.6 弹二次确认
  - prompt 缓存：system / catalog / few-shot 三段标记 `cache_control: ephemeral`
- **`bot/`** —— 编排层
  - `Sc2Facade` Protocol：定义 bot 对 SC2 的全部需求；ares-sc2 / python-sc2 完全
    隔离在 `ares_adapter.py` 里，主模块不依赖 ares
  - `FakeFacade`：单测专用全 mock 实现，记录所有调用
  - `Director`：串起 Parser + Board + Facade，每 tick 调度 committed directive 到
    facade；玩家话语入口 `on_player_command` / `confirm_ambiguous`
  - `ares_adapter.make_bot_class()`：工厂返回继承 AresBot 的 bot 类，运行时 lazy import ares
- **配置**：`config/llm.yaml`、`config/bot_difficulty.yaml`、`aliases/protoss.yaml`
- **M0c smoke 脚本**：`scripts/smoke_test.py`，在真实 SC2 环境验证"不动的叉子"，
  输出 `smoke_report.json`（verdict pass/fail + anomaly 分类）
- **文档**：
  - `CLAUDE.md` —— Claude Code 启动指引（沟通约定 + 实现纪律 + 后续步骤）
  - `README.md` —— 开发者快速开始
  - `docs/m0-smoke-runbook.md` —— M0c 端到端测试玩家手册
  - `docs/adr/0001-tooling.md` —— 工具链选型记录

### 已知风险

- ares-sc2 实际 API 名（`mediator.assign_role` / `build_runner.set_build` 等）未端到端
  校准，M0c smoke 会暴露差异
- `anthropic_provider.py` 0% 测试覆盖（依赖真实 API；M1 实接时验证）

### 安装

```powershell
uv sync --extra dev
# 端到端 smoke 额外：
uv pip install "git+https://github.com/AresSC2/ares-sc2@main"
```
