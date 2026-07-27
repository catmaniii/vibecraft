# 镜头即目标 + 语音编队 + 4 个执行/语义修复 — 设计

> 2026-06-01 brainstorming session 产出。两个新功能（镜头即目标、语音编队 1-5）
> 与同批 4 个实测 bug（代理建造 / 出-vs-出到 / 巡逻 / 已先行修复的 release+camera）
> 合并设计。实现走 writing-plans → subagent-driven-development（sonnet 写码，Opus 编排+review）。

---

## 0. 背景与动机

实测玩家话语暴露的需求：

- "派一个农民到**这里**待命" / "在**这里**修个水晶" / "大部队试探进攻**这里**" —— 用**当前游戏镜头位置**作为指令落点。
- "把运输机编成**1队**"、"释放/取消/清除 1队"、"**1队**到这里待命"、"**2队**火力侦查对方三矿" —— **语音编队（最多 5 队）**，UI 要显示每队构成。
- 同批 bug：
  - ① "在对方 11 点修水晶" → 农民走过去但不建（无代理建造执行器）。
  - ②③ "出 8 个 bg" 被当成 "出到 8 个"（delta vs target 不分）。
  - ④ "在 A 和 B 之间巡逻" → 卡片创建但无往返（无巡逻执行器 + 只捕获一个点）。
  - （release 时序误伤 + 镜头一跳一跳 已先行修复并提交，不在本设计范围。）

---

## 1. 统一抽象（核心洞察）

所有需求归结为给指令系统两个维度各加一种新值，复用既有执行管线：

| 维度 | 现有 | 新增 |
|---|---|---|
| **目标 target**（在哪/打哪/建哪）| `named_spot` / `point` / `unit_tag` | **`camera`** = "这里" = 当前镜头中心（说话那刻快照）|
| **选择 selector**（指挥谁）| `unit_type` / `count` / `near_point` / `tag(s)` / `role` / `claimed` | **`group_id`（1-5 队）**、**`near=camera`（镜头视野内）** |

加完这两个值后：「派农民到**这里**待命」「**1队**到**这里**待命」「**镜头内的运输机**编成1队」全部复用现有 `unit_claim` / `tactical_objective` / `build` 管线，**不重写执行层**。新执行器只为代理建造、巡逻两类全新行为而加。

---

## 2. Feature 1：镜头即目标「这里」

### 行为
- "这里/这边/此处" 在任意可定位指令里 = 当前镜头中心：待命(hold/standby)、修建(build)、进攻/防守(attack/defend)、侦查(scout)、巡逻(patrol)、移动(move) 全支持。

### 数据流 / 捕获时机（关键）
- `PlayerRaw.camera` 每帧可读（已验证 protobuf 有该字段）。facade 加 `get_camera_center() -> (x,y) | None`。
- **快照时机 = 玩家说话那一刻**：PWA 发 `command(text)` → bot 在 `_tick_view_channel` 取出该 command 的同一拍，调 `get_camera_center()` 快照，作为 `ParseContext.camera_point` 传给 IntentParser。
  - 理由：LLM 解析有 ~1-2s 延迟，若在执行时才读镜头，玩家可能已移视角 → "这里"漂移。快照在收到指令那刻锁定。
- LLM 听到"这里"→ 产 `target.kind="camera"`（不带具体坐标）。
- Director 在 submit/执行时，把该指令携带的 `camera_point` 快照替换进 `kind="camera"` 的 target → 得到具体 `(x,y)`。
  - 实现：directive 上挂一个 `context_camera_point` 字段（或在 parse 时就把 camera_point 写进 directive 的 target.point，kind 仍标 camera 供 UI 显示"这里"）。**首选：parse 阶段即把快照点写入 target.point，kind=camera 仅作语义标记**，避免 Director 再找快照。

### 与 view_follow 的关系
- 若正 follow 某单位，镜头随单位走，"这里" = 该单位当前所在区域。语义自然，不特殊处理。

---

## 3. Feature 2：语音编队 1-5

### 状态
- Director 加 `_voice_groups: dict[int, set[int]]`（键 1..5 → unit tags）。

### 编队（创建/覆盖）
- "把〈selector〉编成 N 队" → 解析 selector → tags → `_voice_groups[N] = set(tags)`（**SET 语义**：重复编同队 = 替换，类 SC2 Ctrl+数字）。
- selector 复用现有 + 新增维度（见 §1）：
  - 镜头内：`near=camera` + 默认屏幕半径。
  - 全图同类型：`unit_type`。
  - 指定数量：`count`（截断）。
  - 野外/前线：位置/状态限定（near_point 相对敌方，或"非采矿"语义）。
  - 显式 tag(s)：之前 view_follow/claim 选中的。
- 新 directive type：`GROUP_ASSIGN`（payload: group_id + selector）。

### 解散
- "释放 / 取消 / 清除 N 队"（三者同义）→ `GROUP_CLEAR`（payload: group_id）→ 清 `_voice_groups[N]` + 对这些 tags 调 `release_unit_role`（放回 bot 自由控制）。

### 指挥编队
- "N 队〈verb〉〈target〉" → LLM 产带 `selector.group_id=N` 的常规指令（unit_claim / tactical）。
- Director 解析 selector 时：`group_id` 存在 → 用 `_voice_groups[N]` 的 tags 当作 `selector.tags`，再走既有 claim/tactical 路径。
- 例：
  - "1队到这里待命" = `unit_claim(selector.group_id=1, verb=standby/hold, target=camera)`。
  - "2队火力侦查对方三矿" = `tactical_objective(verb=scout/attack-probe, target=enemy_third)` 或 `unit_claim(group_id=2, verb=recon, target=enemy_third)`（B 类 squad）。

### 动态规则
- 死亡单位每帧自动移出队（resolve 时过滤不存在的 tag）。
- 新造单位**不**自动入队。
- 允许跨队重叠（一个单位可同时在多队，类 SC2）。

### UI（PWA）
- 常驻「编队条」组件：1-5 五格，每格显示该队**兵种构成 + 数量**（如 `1队 运输机×1`、`2队 叉子×8 不朽×2`），空队灰显。
- snapshot 加 `voice_groups: [{group_id, units: {UNIT_TYPE: count}}...]` 字段；server 透传；前端渲染。
- 点编队格可作为后续"选中该队"入口（MVP 仅展示，不强求交互）。

---

## 4. 融合 4 个修复

### ① 代理建造（proxy build）
- 新执行器 `proxy_build_act`：claim 一个农民 → move 到目标点（含 camera/named_spot）→ 到位下 `build(structure)` → **造完留原地待命**（standby，玩家另行下令才走）。
- 触发：`unit_claim(verb=build, target=…)` 或 `build_at` 带"派农民去"语义 → Director 路由到 proxy_build_act。
- 容错：农民死了 → 卡片标失败 / 可重派（MVP：标失败，不自动重派）。

### ②③ 出 vs 出到（delta vs target）
- **schema 已支持**（StructureItem 有 target_count XOR delta；ProductionItem 单位侧已是 delta via `unit_count_built_since`）。
- **LLM prompt 修**：严格区分
  - 修 / 出 / 造 / 补 / 刷 / 加 **N** → `delta=N`（新增，不看当前）。
  - 修到 / 出到 / 造到 / 补到 / 补齐 / 到 **N** → `target_count=N`（绝对上限）。
- **卡片显示统一"新增 N"**：建筑卡 delta 情形直显新增；target 情形按 `target_count − 当前 ready` 算出新增再显示。出兵卡（已 delta）文案对齐"新增 N"。

### ④ 巡逻（patrol）
- models：巡逻 target 存**两个点** `waypoints: [A, B]`（A/B 各可为 named_spot/point/camera）。
- 新执行器 `patrol_act`：每帧判断 claim 的单位是否到达当前 waypoint；到了切下一个；**无限往返** A→B→A→B 直到玩家 × 卡片或重新派单位。
- LLM：抓"在 A 和 B 之间巡逻"的两个点，产 `waypoints=[A,B]`。

---

## 5. 数据流 + 改动模块

```
说话 → PWA command(text) ──► bot 收到那刻: 快照 camera_center
        │
        ▼
   IntentParser(ParseContext + camera_point) ──LLM──► directives
        │  target.kind=camera / selector.group_id=N / verb=build|patrol
        │  / structure delta|target / waypoints=[A,B]
        ▼
   Director: camera 点注入 / group_id→tags / 存编队 / 派执行器
        │
        ├─ proxy_build_act(新)  ├─ patrol_act(新)  ├─ 既有 hold/attack/scout/squad
        ▼
   snapshot: + voice_groups  ──► PWA 编队条 UI(新组件)
```

涉及模块：
- `directives/models.py`：target kind `camera`；patrol `waypoints`；`GROUP_ASSIGN`/`GROUP_CLEAR` payload；structure 卡显"新增"。
- `directives/scope.py`：`Selector.group_id`、`near=camera`。
- `directives/task.py`：（patrol verb 已有）。
- `docs/llm_prompt/rules.md` + `few_shot.md`：camera"这里" / 编队 / delta-vs-target / 代理建造 / 巡逻两点 五块 + 重 dump。
- `bot/facade.py`：`get_camera_center()`。
- `bot/director.py`：camera 快照注入、`_voice_groups` 编队/解散/`group_id` 解析、proxy/patrol 派发、卡片"新增"显示、snapshot 透传 `voice_groups`。
- 新 `bot/auto_combat/protoss/plans/proxy_build_act.py` + `patrol_act.py`（或通用 act 目录）。
- `server/ws.py`：snapshot 含 `voice_groups`。
- `web/`：编队条组件。
- 单测全覆盖 + `voice_spot_check.py` 加 camera/编队/delta-target/patrol case。

---

## 6. 拆给 subagent 的工作包（sonnet 写码，Opus 编排+review）

| 包 | 内容 | 依赖 |
|---|---|---|
| **A** | models/scope/task schema：camera target / `Selector.group_id` / patrol `waypoints` / GROUP_ASSIGN+GROUP_CLEAR / structure 卡显新增 + 单测 | 无（先行，其余依赖） |
| **B** | LLM prompt 五块改 + `dump_llm_prompt.py` 重生 + voice_spot_check 加 case | A |
| **C** | `facade.get_camera_center` + Director camera 快照注入 + `kind=camera`→点 解析 + 单测 | A |
| **D** | Director `_voice_groups` 编队/解散/`group_id` 解析 + snapshot 透传 + 单测 | A |
| **E** | `proxy_build_act`（造完留原地待命）+ Director 派发 + 单测 | A |
| **F** | `patrol_act`（无限往返两点）+ Director 派发 + 单测 | A |
| **G** | PWA 编队条组件 + camera 联动展示 | D |

执行顺序：**A 先做完** → B/C/D/E/F **并行**（都只依赖 A）→ G 依赖 D。每包独立 TDD（先失败测试 → 实现 → 验证 → commit）。

---

## 7. 已决策点（避免实现期再问）

- "这里" = 镜头**中心**；快照于**收到指令那刻**（非执行时）。
- 编队选单位 = 复用 Selector 全维度 + 镜头区域。
- 释放=取消=清除 = **解散编队**（单位放回 bot）。
- 代理建造造完 = **留原地待命**。
- 巡逻 = **无限往返**直到取消。
- 编队 SET 语义（重复编=替换）；死单位自动移出；新单位不自动入；允许跨队重叠。
- 编队上限 5（1-5）。

## 8. 非目标（YAGNI）
- 不做编队"追加单位"（只 SET 替换；要追加重新编）。
- 不做巡逻定圈数 / 路径点 >2。
- 代理建造死亡不自动重派（标失败）。
- 编队条 UI MVP 只展示，不强求点选交互。
