# 驾驶舱同步：剧本可见 + bot 决策流（cockpit-sync）

| 字段 | 值 |
|---|---|
| 起草日期 | 2026-05-15 |
| 状态 | 设计预研，待实现 |
| 触发 | M1.6 真实端到端验证。手机侧没有对局 UI，玩家判断不出宏观剧本切没切（光看游戏微观操作看不出来）|
| 上游真理源 | `docs/plans/2026-05-14-vibecraft-design.md` §9.3（WS schema）/ §9.4（事件 taxonomy）/ §9.5（UI 布局）|
| 范围 | 实现设计文档已规划的 `snapshot` / `event` 帧的**最小可用版**；M3「手机驾驶舱完整」是终态，这里只做能验证的子集 |

> 本文档不重新设计 schema。`snapshot` / `event` 帧 schema 在设计文档 §9.3 已定义，
> 这里只决定 **MVP 推哪些字段、谁产出、怎么经现有上行通道到手机、PWA 怎么渲染**。

---

## 0. 现状盘点（动手前的事实）

读完相关代码后，确认当前管线形态：

### 0.1 上行通道（已有，照搬即可）

```
子进程 bot                      GameProcess(父进程)         WsConnection
─────────                       ─────────────────          ────────────
echo_callback(text, interp)
  → up_q.put_nowait(
      {"kind":"echo", ...})  →  raw_events() yield raw   →  _dispatch_upstream(raw)
                                                              kind=="echo"
status_callback(sc2,bot,d)                                      → send command_echo 帧
  → up_q.put_nowait(
      {"sc2":..,"bot":..})   →  raw_events() yield raw   →  _dispatch_upstream(raw)
                                                              else 分支
                                                              → send game_status 帧
```

关键事实：
- **上行队列 `up_q` 是唯一通道**，子进程往里 `put_nowait` dict，父进程 `raw_events()` 逐条 yield，`WsConnection._dispatch_upstream(raw)` 按 `raw.get("kind")` 分流成下行帧。
- 新增一类上行消息 = 在子进程造 dict 推进 `up_q` + 在 `_dispatch_upstream` 加一个 `kind` 分支。**不需要碰队列机制、不需要新通道。**
- echo 的产出点：`ares_adapter._run_command_with_echo`（闭包持有 `echo_callback`）。`_build_bot_class` 里 `echo_callback = put_echo`，`put_echo` 闭包持有 `up_q`。
- `_dispatch_upstream` 的 `else` 分支会调 `_apply_raw_dict` 更新 `GameProcess._sc2_state/_bot_state` —— 所以**新 kind 必须显式分支处理**，否则会被误当 game_status。

### 0.2 剧本状态从哪读

- `DirectiveBoard.slots: dict[StageKind, StrategySlot | None]`，`StrategySlot` 只有 `stage / strategy_id / set_at / set_by` —— **只有 id，没有 display / phases**。
- 剧本对象（`OpeningBuild` 有 `display_name_zh / phases / steps`）在 `StrategyLibrary` 里，用 `library.get(id)` 取。
- `DirectiveBoard.current_stage` 是当前阶段（opening/midgame/lategame）。
- **Director 当前没有持有 `StrategyLibrary` 引用** —— 这是 P0 要补的一个注入点（见 §3.1）。
- `Director._recent_commands: list[_RecentCommand]`（`text + ts`，buffer 默认 3）—— `recent_commands` 字段现成。

### 0.3 "decision" 概念：当前完全没有

- 全代码搜下来没有任何 `decision` 数据结构。
- `BoardEvent` 是 directive 生命周期事件（committed/released/strategy_changed/phase_transitioned），**它本身就是"决策"的一部分来源**，但目前只 log 进 `GameSession`，不往手机推。
- `Director.on_tick()` 已经拿到 `board.tick(now)` 返回的 `list[BoardEvent]`，**这是天然的埋点位置**。
- auto-pilot 两阶段切换（`_register_auto_pilot` 里 `runner.build_completed` 的 if 分支）目前**没有任何事件**，纯靠 ares 内部状态翻转。

### 0.4 前端现状

- `App.vue` 是单页面最小壳：状态卡片 + `CommandInput` + 话语示例。没有"对局界面"概念。
- `useWs.ts` 的 `onmessage` 只认 `game_status`，其它帧（含 `command_echo`）**当前被静默丢弃**。
- `types.ts` 只定义了 `GameStatusFrame / PingFrame`，没有 `command_echo / snapshot / event`。
- `App.vue` 已经有 `canStartGame`（`sc2 in [idle,ended]`）和 `canSendCommand`（`sc2 === playing`）两个 computed —— **「未开局 / 对局中」视图切换的判据现成**。

---

## 1. snapshot 帧最小实现

### 1.1 MVP 推什么字段

设计文档 §9.3 的完整 `snapshot` 很大（game / strategy / overlays / standing_orders / recent_decisions / recent_commands / saved_recipes）。**P0 只推 `strategy` + `recent_commands` 两块**，其余字段先不出现（前端按 optional 处理，M3 再补全）。

```jsonc
// 下行帧（Bot → 手机）：snapshot —— MVP 子集
{
  "type": "snapshot",
  "ts": 330.5,                        // 游戏内秒（float(bot.time)）
  "strategy": {
    "current_stage": "opening",       // opening | midgame | lategame，= board.current_stage
    "opening":  {                     // 三档各一个 slot，未设置为 null
      "id": "1g_robo_immortal",
      "display": "1门Robo 不朽开",     // library.get(id).display_name_zh
      "phases": [                     // 仅 opening 有；从 OpeningBuild.phases 直出
        {"id": "opening",  "display": "开局",   "subtitle": "13 农 BG"},
        {"id": "tech",     "display": "上折跃", "subtitle": "WG 研究"},
        {"id": "rallying", "display": "集结追猎","subtitle": "14 追猎"},
        {"id": "executing","display": "出发压制","subtitle": "6:00 出门"}
      ],
      "current_phase_id": "opening"   // ⚠️ 见 §1.4：MVP 用启发式，不精确
    },
    "midgame":  { "id": "iac_2base", "display": "双矿 IAC 重装地面" } | null,
    "lategame": { "id": "skytoss",   "display": "Skytoss 航母流" }   | null
  },
  "recent_commands": [                // 最近 3 条玩家话语回显，= Director._recent_commands
    {"text": "切 IAC", "ts": 312.0},
    {"text": "追猎偷矿", "ts": 320.5}
  ]
}
```

字段裁剪理由：
- `strategy.current_stage` + 三档 `id/display` —— **直接回答用户刚需**："`set_build` 到底切没切？现在跑哪个剧本？"
- `opening.phases` + `current_phase_id` —— 对应设计文档 §9.5 的 phase stepper；phases 是静态数据，直接从 `OpeningBuild` 拷出。
- `recent_commands` —— 玩家发完话语，能在对局界面看到"刚才说了什么"，配合 `command_echo`（已有）形成闭环。
- **不推** `game`（资源/人口）：M1.6 验证刚需是剧本，资源条是 M3 状态条的事，且需要 facade 已有的 `get_state()`，可作为 P0 的低成本附赠（见 §5），但不阻塞。
- **不推** `overlays / standing_orders / saved_recipes / recent_decisions`：P0 不需要；`recent_decisions` 归入 P1（见 §2、§6）。

### 1.2 谁产出

**子进程 bot 内**，新增一个 `SnapshotBuilder`（或直接 Director 方法，见取舍）。需要的三个数据源在子进程里都有：
- `Director.board`（current_stage + slots）
- `StrategyLibrary`（id → display / phases）—— **需要把 library 引用传进 Director**
- `Director._recent_commands`

推送通道：复刻 echo 的 `echo_callback` 模式，新增 `snapshot_callback: Callable[[dict], None]`。

### 1.3 多频繁：状态变化推 + 低频兜底

给出建议（用户偏好列 trade-off，这里给结论 + 理由）：

| 方案 | 代价 | 结论 |
|---|---|---|
| A. 纯周期推（如每 2s） | 简单；但 90% 帧是重复数据，且剧本切换最长 2s 才可见 | ✗ |
| B. 纯状态变化推 | 即时；但首屏要等到第一次变化才有数据，且漏推无兜底 | ✗ |
| **C. 变化推 + 低频兜底** | 略复杂；变化即时可见 + 周期兜底防漏 + 首屏有数据 | ✓ **采用** |

具体：
- **变化推**：`Director.on_tick()` 里，`board.tick(now)` 返回的 events 中只要含 `STRATEGY_CHANGED` / `PHASE_TRANSITIONED`，立即 build 一次 snapshot 推送。玩家话语 `set_build` 走完 1.5s commit 后会 emit `STRATEGY_CHANGED` —— 天然覆盖"`set_build` 生效"这个验证点。
- **低频兜底**：每 N tick（realtime 下 `on_step` 约 ~22.4 tick/s，取 N≈45 ≈ 2s）强制推一次，即使无变化。也保证手机重连后能拿到最新 snapshot（重连后 `useWs` 会重新连，下一个兜底周期就刷新）。
- **`current_phase_id` 变化**也算变化推触发条件（见 §1.4，phase 是 bot 侧算的，不经 board event）。

⚠️ **spike 验证点 S1**：确认 realtime 模式下 `on_step` 实际调用频率（用来定兜底周期 N）。`docs/plans/2026-05-15-auto-pilot.md` 或 M0c smoke 日志里可能已有数据；没有就在 smoke 跑一次数 tick。

### 1.4 ⚠️ current_phase_id 的陷阱

`OpeningBuild.phases` 是给玩家看的**展示用阶段标签**，跟 `OpeningBuild.steps`（supply-keyed build steps）**没有结构化对应关系** —— phases 是 4 个，steps 是 11 步，没有字段把某步归到某 phase。ares 的 `build_order_runner` 也只知道"build 跑到第几步 / `build_completed`"，不知道 vibecraft 的 phase。

**P0 处理**：`current_phase_id` 用**粗启发式**，别花时间做精确映射：
- MVP 公式：opening 未完成 → `phases[0]`；`build_order_runner.build_completed` → `phases[-1]`（最后一个 phase，通常是"出发/执行"）。中间 phase 先不点亮。
- 或者更简单：P0 直接**不推 `current_phase_id`**（字段省略），前端 phase stepper 全部显示为"未高亮"，只展示剧本有哪几个阶段。用户当前刚需是"看见在跑哪个剧本"，phase 精确高亮是 M3 的事。
- **推荐 P0 取后者**（不推 current_phase_id），把"phase 精确进度"整个推迟到 M3，与 §9.5 的完整 phase stepper 一起做。本文档 §3 的前端设计按"phases 列表展示、不依赖 current_phase_id"来写。

> 决策：P0 snapshot 的 `opening` 块**包含 `phases` 列表**（静态数据，几乎零成本），**不包含 `current_phase_id`**。phase 进度高亮 = M3。

---

## 2. bot 决策埋点（最需要想清楚的部分）

### 2.1 问题定义

vibecraft 当前没有显式 "decision" 数据源。用户要的是"bot 自己的决策（宏观战术 + 微观决策状态机）实时同步到手机"。但**别过度设计** —— M1.6 阶段 bot 的"决策"其实很少：auto-pilot 是固定的两阶段 + ares 内部黑盒。真正有信息量、玩家需要据此干预的"决策点"是有限的。

### 2.2 要不要新建 decision 数据结构

**不新建独立的持久 "Decision" 领域模型。** 理由：
- 设计文档 §9.3 的 `event` 帧已经是承载决策的载体：`{"type":"event", "kind":"...", "ts":..., "payload":{...}}`。§9.4 taxonomy 里 `decision.delaying_attack / changed_target` 已经是 `event` 的一类 kind。
- `BoardEvent` 已经是结构化的事件流，`Director.on_tick` 已经在消费它。
- 新建 `Decision` 类 = 凭空多一层，违反"别过度设计"。

**做法**：定义一个轻量的 `event` 帧 builder（纯函数 `BoardEvent → event dict` + 几个 bot 侧手动埋点），不引入新领域模型。前端维护一个 `events: EventFrame[]` ring buffer 当"决策流"。

### 2.3 最小但有用的埋点清单

只埋**玩家看得懂、且可能据此干预**的决策点。分两组：

**A 组：从 `BoardEvent` 直接转译（零新增埋点，Director.on_tick 已有 events）**

| event kind | 来源 BoardEvent | 玩家看到 | 为何有用 |
|---|---|---|---|
| `strategy.set` | `STRATEGY_CHANGED` | "切到 双矿 IAC 重装地面" | 确认 `set_build` 生效（和 snapshot 互为印证）|
| `strategy.phase_change` | `PHASE_TRANSITIONED` | "进入 midgame 阶段" | 阶段推进可见 |
| `directive.committed` | `COMMITTED` | "指令已生效：追猎偷矿" | 1.5s 延迟后确认指令真的下去了 |
| `directive.released` | `RELEASED` | "standing order 结束：叉子 hold" | 知道临时指令到期了 |
| `directive.rejected` | `REJECTED` | "指令被拒：优先级低于 X" | 知道指令没生效 + 原因 |

→ 这组**几乎零实现成本**：`Director._dispatch_event` 已经有 `BoardEventKind → EventKind` 的 `kind_map`，只要在那里**额外**把事件转成 `event` 帧 dict 推上行队列即可。

**B 组：bot 侧手动埋 2 个点（auto-pilot 状态机，当前完全没有事件）**

| event kind | 埋点位置 | 玩家看到 | 为何有用 |
|---|---|---|---|
| `decision.autopilot_phase` | `ares_adapter._register_auto_pilot`，`runner.build_completed` 第一次翻 true 时 | "开局 build 跑完，转入自动运营（造兵/扩张/开矿）" | 这是 bot 宏观行为的一次真实切换，玩家看不到就只能猜 |
| `decision.opening_progress`（可选） | 同上，`build_order_runner` 每推进一步 build step | "build：22 research 折跃" | 让玩家看到 opening 在按 build order 走；可选，B 组先做 autopilot_phase 一个就够 |

→ B 组需要在 bot 类里加一个"上次 build_completed 状态"的 instance 变量做**边沿检测**（只在 false→true 那一 tick 推一次），避免每 tick 重复推。

### 2.4 不埋什么（明确划界，防过度设计）

- ❌ ares Manager 内部的每个微操决策（focus_fire / stutter_step / blink）—— 黑盒，且 §9.4 的 `combat.*` 是 M3+ 的事，M1.6 不做。
- ❌ LLMControlBehavior 的单位级决策 —— LLMControlBehavior 本身 M1 还没实现（`execute_unit_action` 在 ares_adapter 里还是 `pass`）。
- ❌ 战斗 / 敌情 / 建造完成事件（§9.4 的 `combat.* / enemy.* / build.*`）—— 需要 bot 侧大量 on_unit_* 回调埋点，是独立的一块工作，归 M3。

### 2.5 event 帧 schema（照 §9.3）

```jsonc
// 下行帧：event
{
  "type": "event",
  "kind": "strategy.set",        // §9.4 taxonomy 的 kind
  "ts": 345.1,                   // 游戏内秒
  "payload": {                   // kind 相关；前端按 kind 取字段
    "stage": "midgame",
    "strategy_id": "iac_2base",
    "display": "双矿 IAC 重装地面"   // ⚠️ 见下：需要 library 解析 id→display
  }
}
```

⚠️ **`payload` 里要带 `display`**：`BoardEvent.payload` 只有 `strategy_id`，前端需要中文名。在 Director 转译时用 `library.get(id).display_name_zh` 补 `display`（同样依赖 §3.1 的 library 注入）。

⚠️ **spike 验证点 S2**：确认 `directive.committed` 这类事件的频率不会刷屏。一局玩家 20-60 条指令，每条 1 个 committed，可接受。但 §9.4 提到"同 kind 1s 内合并"的防洪规则 —— **P1 阶段先不实现合并**，B 组埋点天然低频；如果验证时发现刷屏，再加合并（合并逻辑放 Director 转译层，不放前端）。

---

## 3. PWA 对局界面

### 3.1 后端先决：把 StrategyLibrary 注入 Director

snapshot 和 event 都需要 `id → display / phases`。当前 `Director.__init__` 没有 library 参数。

**改 `src/vibecraft/bot/director.py`**：
- `Director.__init__` 增加可选参数 `library: StrategyLibrary | None = None`，存为 `self.library`。
- **改 `src/vibecraft/server/game_process.py`** 的 `director_factory`：`Director(facade=facade, parser=parser, session=session, library=strategy_library)` —— `strategy_library` 在 `_build_bot_class` 里已经构造好了，直接传。
- 单测：`Director` 现有单测不传 library 仍能跑（None 时 snapshot 的 display 字段 fallback 成 id）。

### 3.2 App.vue：拆成「未开局 / 对局中」两视图

当前 `App.vue` 是单页。改成根据 `sc2` 状态切换两个子视图：

- **判据**：复用现有 computed。`sc2 in [idle, launching, in_game, ended, crashed]` → 「启动视图」；`sc2 === 'playing'` → 「对局视图」。
- **改法**：把现状 `App.vue` 的 `<main>` 内容（状态卡片 + 开始对局按钮 + 话语示例）抽成 `views/LaunchView.vue`；新建 `views/CockpitView.vue` 放对局界面。`App.vue` 只留 header（含 `StatusChain`）+ `v-if/v-else` 切两个 view。
- 这样符合设计文档 §3.3 两阶段启动时序：扫码连接 → 启动视图点「开始对局」→ SC2 起来 → 自动切到对局视图。

### 3.3 CockpitView.vue：最小对局界面

P0+P1 完成后的最小形态（ASCII 布局，对齐 §9.5 但砍到 MVP）：

```
┌─────────────────────────────────────────┐
│ (header: VibeCraft + StatusChain)       │  ← App.vue 提供，不在 CockpitView 里
├─────────────────────────────────────────┤
│ ▼ 当前剧本                                │  ← P0：核心，三档剧本卡片
│  ┌─ 开局 ──────────────────────────────┐ │
│  │ 1门Robo 不朽开           [opening]  │ │  ← 高亮：current_stage === 'opening'
│  │ 开局 · 上折跃 · 集结追猎 · 出发压制   │ │  ← phases 横排展示（不高亮单个）
│  └────────────────────────────────────┘ │
│  ┌─ 中期 ──────────────────────────────┐ │
│  │ 双矿 IAC 重装地面        [midgame]  │ │
│  └────────────────────────────────────┘ │
│  ┌─ 后期 ──────────────────────────────┐ │
│  │ （未设置）                          │ │  ← slot 为 null 时灰显
│  └────────────────────────────────────┘ │
│ ─────────────────                       │
│ ▼ Bot 决策流                              │  ← P1：events ring buffer，倒序
│  · 5:45 指令已生效：追猎偷矿              │
│  · 5:30 切到 双矿 IAC 重装地面            │
│  · 5:10 开局 build 跑完，转入自动运营      │
│ ─────────────────                       │
│ ▼ 最近指令                                │  ← P0：snapshot.recent_commands
│  · 5:42 追猎偷矿                          │
│  · 5:28 切 IAC                            │
├─────────────────────────────────────────┤
│ [指令输入区]  ← 复用现有 CommandInput.vue │  ← 「能干预」入口，见 §4
└─────────────────────────────────────────┘
```

组件拆分：
- `CockpitView.vue` —— 容器，竖向滚动。
- `StrategyCard.vue`（新）—— 单张剧本卡片，props: `stage / slot`（slot 可为 null）。三档复用同一组件。
- `DecisionFeed.vue`（新，P1）—— 接 `events: EventFrame[]`，倒序渲染，每条一行 `时间 + 文案`。kind→中文文案的映射表放这个组件里。
- `RecentCommands.vue`（新，可选，P0 也可以直接内联进 CockpitView）—— 接 `recent_commands`。
- `CommandInput.vue` —— 不动，直接放进 CockpitView 底部。

### 3.4 useWs.ts / types.ts：加帧处理

**`types.ts` 新增类型**：
```ts
// 下行帧
export interface SnapshotFrame {
  type: 'snapshot'
  ts: number
  strategy: {
    current_stage: 'opening' | 'midgame' | 'lategame'
    opening: StrategySlotView | null
    midgame: StrategySlotView | null
    lategame: StrategySlotView | null
  }
  recent_commands: { text: string; ts: number }[]
}
export interface StrategySlotView {
  id: string
  display: string
  phases?: { id: string; display: string; subtitle: string }[]  // 仅 opening
}
export interface EventFrame {
  type: 'event'
  kind: string
  ts: number
  payload: Record<string, unknown>
}
export interface CommandEchoFrame {        // ⚠️ 现在就存在但 types.ts 漏了，顺手补
  type: 'command_echo'
  user_text: string
  interpretation: string
  ts: number
}
// DownFrame union 追加 SnapshotFrame | EventFrame | CommandEchoFrame
```

**`useWs.ts` 改 `onmessage`**：当前只处理 `game_status`。改成：
- 新增 `snapshot` ref（`ref<SnapshotFrame['strategy'] | null>(null)` + `recentCommands`）。
- 新增 `events` ref（`ref<EventFrame[]>([])`，push 进来时裁到最近 ~30 条 ring buffer）。
- 新增 `lastEcho` ref（`command_echo` 帧）。
- `onmessage` 里 `switch(frame.type)`：`game_status` 不变；`snapshot` → 更新 strategy/recentCommands；`event` → push 进 events；`command_echo` → 更新 lastEcho；`ping` 忽略。
- `useWs` 返回值追加 `snapshotStrategy / recentCommands / events / lastEcho`。
- ⚠️ **重连处理**：`onclose` 重连后，旧 `snapshot/events` 应保留渲染（§9.6"保留最后 snapshot 渲染不闪烁"），下一个 snapshot 兜底周期会自动刷新 strategy；events 是历史流，不清空。

⚠️ **spike 验证点 S3**：现有 `__tests__/useWs.test.ts` 和 `types.test.ts` 会因为 union 类型扩展和 onmessage 分支变化而需要补用例。改前先跑一遍现有测试，确认 baseline 绿。

---

## 4. 「能干预」：MVP 走现有 command，不加新控件

**结论：MVP 阶段干预完全复用现有语音/文字 command，不需要新 UI 控件。**

理由：
- 设计文档 §2.3 / §5.2：所有输入最终都生成 directives 数组进同一个 Directive Board。"看到剧本卡片上写着 opening 还是 IAC" → 玩家说"切到 Skytoss" → 走已有的 `command` 帧 → 子进程下行队列 → `_run_command_with_echo` → Director → Board。这条链路 M1.6 已经打通。
- 干预的"看 → 想 → 说"闭环里，UI 的职责是**看**（snapshot/event 让玩家看到状态）和**说的入口**（CommandInput 已有）。"看到了"本身就让玩家能精准下指令，不需要按钮。
- §9.5 终版布局里的「快捷区」是预留灰掉的，§9.3 的 `recipe` 帧标着"未来"。MVP 不碰。
- 唯一值得 P1 顺带做的小增强：剧本卡片可以做成"点一下把剧本名填进 CommandInput 输入框"（不是直接发帧，只是填充，玩家仍要确认/编辑后发）—— 但这是 nice-to-have，**不列入 P0/P1 必做项**，验证完用户觉得需要再加。

---

## 5. 分阶段：P0 剧本可见 / P1 bot 决策流

### P0 — 剧本可见（解决用户当前验证刚需，独立可交付）

**目标**：玩家在手机对局界面能看到当前三档宏观剧本各是什么，发完 `set_build` 后能看到剧本卡片变化，从而确认指令生效。

**交付物**：snapshot 帧（仅 `strategy` + `recent_commands`）+ 对局界面剧本卡片。

| # | 改哪个文件 | 加什么 | ⚠️ spike |
|---|---|---|---|
| P0-1 | `src/vibecraft/bot/director.py` | `__init__` 加 `library` 参数存 `self.library`；新增 `build_snapshot(now) -> dict` 方法（读 `board.current_stage` + `board.slots` + `library.get(id)` + `_recent_commands`，组装 §1.1 的 dict）；`current_phase_id` 不推（§1.4）| — |
| P0-2 | `src/vibecraft/bot/director.py` | `on_tick` 里：若本 tick events 含 `STRATEGY_CHANGED`/`PHASE_TRANSITIONED` → 标记需推；另维护 tick 计数器做 ~2s 兜底；需推时调 `self._snapshot_callback(self.build_snapshot(now))`（callback 可为 None） | S1（兜底周期 N）|
| P0-3 | `src/vibecraft/bot/ares_adapter.py` | `make_bot_class` 加参数 `snapshot_callback`；`director_factory` 调用处不变（director 自己持 callback）—— 实际把 `snapshot_callback` 透传给 `Director`：改 `director_factory` 签名让它能拿到 callback，或 `make_bot_class` 构造 Director 后 `director.set_snapshot_callback(cb)`。**推荐后者**（`Director.set_snapshot_callback`），避免改 `director_factory` 协议 | — |
| P0-4 | `src/vibecraft/server/game_process.py` | `_child_entry` 加 `_put_snapshot(d)` 闭包（`up_q.put_nowait({"kind":"snapshot", **d})`）；`_build_bot_class` 把它透传给 `make_bot_class(snapshot_callback=...)`；`director_factory` 里 `Director(... library=strategy_library)` | — |
| P0-5 | `src/vibecraft/server/ws.py` | `_dispatch_upstream` 加 `kind == "snapshot"` 分支 → `json.dumps({"type":"snapshot", ...})` 发给手机（payload 直接转发，子进程已组好）| — |
| P0-6 | `web/src/types.ts` | 加 `SnapshotFrame / StrategySlotView / CommandEchoFrame`；`DownFrame` union 扩展 | S3 |
| P0-7 | `web/src/composables/useWs.ts` | `onmessage` 改 `switch(frame.type)`；加 `snapshotStrategy / recentCommands` ref；返回值暴露 | S3 |
| P0-8 | `web/src/views/LaunchView.vue`（新）| 把现 `App.vue` 的 `<main>` 内容搬过来（状态卡片 + 开始对局 + 话语示例）| — |
| P0-9 | `web/src/views/CockpitView.vue`（新）| 三档剧本卡片区 + 最近指令区 + 底部 `CommandInput` | — |
| P0-10 | `web/src/components/StrategyCard.vue`（新）| 单张剧本卡片，props `stage / slot`，slot null 时灰显，phases 横排展示 | — |
| P0-11 | `web/src/App.vue` | 删 `<main>` 内容，改成 `v-if (sc2==='playing')` → `CockpitView` `v-else` → `LaunchView`；header 不动 | — |

**P0 验证脚本**（用户手测）：扫码 → 开始对局 → SC2 起来后 UI 切到对局界面，剧本卡片显示 `1门Robo 不朽开 / 双矿 IAC（或 null）/ null` → 说"切到 Skytoss" → 等 ~2s → lategame 卡片出现 `Skytoss 航母流`。**这一步能独立验证，不依赖 P1。**

⚠️ **P0 兼容性**：`snapshot_callback` / `library` 全部 `Optional`，缺省 None —— ares 未装的 `_M12Bot` fallback 路径、Director 现有单测都不受影响。

### P1 — bot 决策流（decision 埋点 + event 帧 + 决策流 UI）

**目标**：玩家能在对局界面看到一条"bot 决策流"，包含剧本切换、指令生效/拒绝、auto-pilot 阶段切换。

| # | 改哪个文件 | 加什么 | ⚠️ spike |
|---|---|---|---|
| P1-1 | `src/vibecraft/bot/director.py` | `_dispatch_event` 里：把 `BoardEvent` 转 `event` 帧 dict（kind 用 §9.4 taxonomy，strategy 类的 payload 补 `display`），调 `self._event_callback(event_dict)`（A 组埋点，§2.3）| S2（频率/防洪）|
| P1-2 | `src/vibecraft/bot/ares_adapter.py` | `_VibeCraftBot` 加 instance 变量 `_autopilot_started: bool = False`；`_register_auto_pilot` 里 `runner.build_completed` 从 false→true 的边沿，调一次 `event_callback({"type":"event","kind":"decision.autopilot_phase",...})`（B 组埋点）| — |
| P1-3 | `src/vibecraft/bot/ares_adapter.py` + `director.py` | 新增 `event_callback` 透传链：`make_bot_class(event_callback=...)` → `Director.set_event_callback(cb)`（同 P0-3 的模式）| — |
| P1-4 | `src/vibecraft/server/game_process.py` | `_put_event` 闭包（`up_q.put_nowait({"kind":"event", ...})`）；透传给 `make_bot_class(event_callback=...)` | — |
| P1-5 | `src/vibecraft/server/ws.py` | `_dispatch_upstream` 加 `kind == "event"` 分支 → 发 `{"type":"event",...}` 给手机 | — |
| P1-6 | `web/src/types.ts` | 加 `EventFrame`；`DownFrame` union 扩展 | — |
| P1-7 | `web/src/composables/useWs.ts` | `onmessage` 加 `event` 分支 → push 进 `events` ring buffer（裁到 ~30 条）；重连不清空 | — |
| P1-8 | `web/src/components/DecisionFeed.vue`（新）| 接 `events`，倒序渲染；内含 `kind → 中文文案` 映射表 | — |
| P1-9 | `web/src/views/CockpitView.vue` | 在剧本卡片和最近指令之间插入 `DecisionFeed` | — |

**P1 验证**：开局后决策流出现"开局 build 跑完，转入自动运营"；说"切 IAC" → 1.5s 后出现"指令已生效：..." + "切到 双矿 IAC 重装地面"。

### 阶段依赖

- P0 完全独立，不依赖 P1。P0 交付后用户即可验证"剧本可见"刚需。
- P1 复用 P0 建立的全部管线模式（callback 透传 / `_dispatch_upstream` 分支 / useWs switch / CockpitView），增量小。
- `command_echo` 帧前端处理（types 补类型 + useWs 接住 + 在 CockpitView 或最近指令区显示 interpretation）：当前 `command_echo` 已经在发但前端丢弃。**建议挂在 P0 顺手做**（P0-6 已经要补 `CommandEchoFrame` 类型），让"发指令 → 看到 echo 解析结果"闭环 —— 这跟 snapshot 的 recent_commands 配合，强化"指令收到了"的反馈。不单列阶段。

---

## 6. 关键风险 / spike 汇总

| ID | spike 内容 | 阻塞谁 | 怎么验 |
|---|---|---|---|
| S1 | realtime 模式 `on_step` 实际 tick 频率（定 snapshot 兜底周期 N）| P0-2 | M0c smoke 日志或 auto-pilot 文档查；没有就 smoke 跑一次数 |
| S2 | `event` 帧频率会不会刷屏（尤其 `directive.committed`）| P1-1 | 端到端跑一局观察；超出预期再加 1s 合并（合并放 Director 转译层）|
| S3 | 前端 union 类型扩展 + onmessage 分支改动对现有 `__tests__/` 的影响 | P0-6/P0-7 | 改前 `npm test` 跑 baseline，改后补 snapshot/event 帧解析用例 |
| S4 | `current_phase_id` 与 build steps 无结构化对应（§1.4）| —（已决策规避）| P0 不推该字段，phase 精确进度推迟到 M3 |
| S5 | `_dispatch_upstream` 的 `else` 分支会改 `GameProcess` 内部状态 —— 新 kind 必须显式分支，不能落到 else | P0-5 / P1-5 | 加分支即可；写一个 ws 层单测断言 snapshot/event kind 不污染 sc2_state |

---

## 7. 与设计文档的一致性确认

- snapshot / event 帧名与字段：直接取自 §9.3，本文档只做**字段裁剪**（推子集），未改 schema 形状。
- event kind：取自 §9.4 taxonomy（`strategy.set / strategy.phase_change / directive.committed/released/rejected / decision.*`）。
- 防洪规则（§9.4"同 kind 1s 合并"）：P1 先不实现，列为 S2 的应对手段。
- UI 布局：CockpitView 是 §9.5 终版布局的 MVP 子集（剧本卡片 + 决策流 + 输入区；省略状态条资源/人口、minimap、standing orders 区、撤销/保存）。
- 两阶段启动（§3.3）：「未开局/对局中」视图切换正是该时序的前端落地。
- 无任何架构层面偏离 —— 没动设计文档决策表，纯实现层细节。本文档作为 M1.6 的实现细化，不需要回写设计文档；如 P0/P1 落地后有 ADR 级取舍（如 event 合并策略），单独记 `docs/adr/`。
