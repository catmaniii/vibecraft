# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# VoiceCraft 项目交接文档

> 这份文档由上一次 brainstorming session (2026-05-14) 生成。Claude Code 启动时会自动加载此文件，新会话进项目即拥有完整上下文。

---

## 沟通约定

- **中文沟通**。代码注释、commit message、log 字段名仍可英文（业界惯例），但所有面向用户的解释、设计讨论、PR 描述用中文。
- **建筑用 hotkey 简称**，不写全名。**默认就要这样写**，不需要用户提醒：
  - **BG** = Gateway / 兵营（折跃门同 BG）
  - **BF** = Forge / BF
  - **BC** = Cybernetics Core / BC
  - **VC** = Assimilator / 气矿
  - **VR** = Robotics Facility / VR （兵种 VR = Void Ray 看上下文消歧）
  - **VD** = Robotics Bay / VD
  - **VS** = Stargate / VS
  - **VT** = Twilight Council / VT
  - **VX** = Fleet Beacon / VX
  - **VA** = Templar Archives / VA
  - **VB** = Dark Shrine / VB
  - **NX** = Nexus / 基地
  - **PY** = Pylon
- 单位用中文（叉子 / 不朽 / 追猎 / 闪追 / 凤凰 / 航母 / DT / HT / 母舰 / 高坦 / 暗使 / 探机）。
- 战术黑话保留：4BG / IAC / Skytoss / 12D / 两矿凤凰 / 闪追 timing 等。

---

## 项目速览

**VoiceCraft** —— 用语音 + 文字指挥 AI 替你操作 SC2 神族，给操作不动的老 SC2 玩家。

- **当前状态**：设计完成（2026-05-14），实现进行中 —— 见仓库实际代码状态
- **MVP 范围**：神族 vs SC2 内置 AI，3 个剧本（1门Robo opening / IAC midgame / Skytoss lategame）
- **预估工期**：12-14 周（M0-M5 里程碑）

### 必读文档

| 文档 | 面向谁 | 用途 |
|---|---|---|
| **`docs/plans/2026-05-14-voicecraft-design.md`** | 开发者 | 14 节完整设计，唯一真理源 |
| **`USER_GUIDE.md`** | 玩家 | 入门手册 + 话语示例 + FAQ |

任何后续工作之前，先 Read 上面这两份文件。

---

## 用户画像

- **资深 SC2 玩家**：精通战术黑话（4BG / 闪追 timing / Skytoss / DT 偷家 / IAC / 12D / 两矿飞龙），熟悉建筑 hotkey（BG / BF / VC / VR / VS / VT）。**不要给他翻译这些术语**。
- **软件设计直觉强**：会主动提"双向 streaming / event 驱动"、"recipe store 抽象"、"日志要做好"。关注架构纪律。
- **中文为主**，紧凑直接，不要废话。
- **偏好 trade-off 列举**而非单一推荐 —— 给方案给 3-4 个候选让他选。
- **重视架构灵活性 + 可扩展性** —— 不绑死单一形态。

---

## 工作风格速查

### ✅ DOs

- 设计 trade-off 时**列 3-4 候选 + 各自代价**，让用户选
- 给**具体方案 + 真实例子**（schema / 代码片段 / 真实玩家话语）
- 留**扩展接口给"未来要做的事"**，schema 占位即可
- 用 **ASCII art 画架构图 / UI 布局**，比文字描述快 10 倍
- 任何 LLM 调用 / 关键数据流走**结构化 JSONL 日志**，全量保留
- 设计后端协议时**至少考虑两种部署变体**（本地 vs 远程、单机 vs PvP）
- "MVP 走 ①，但架构预留 ② 的接口"这种**分层路线**是用户最爱

### ❌ DON'Ts

- **不要过度简化** —— 用户多次推翻我"砍掉某层"。当我有冲动砍掉某层时，先想清楚那层解决的是什么问题。
- **不要在已有假设上打多 mode 补丁** —— 用户可能直接换假设，让多 mode 消失。比如"键鼠 vs 手机"的多模式被"PC 当显示器、手机唯一控制器"取代。
- **不要忽视 logging / observability** —— 这是 first-class concern，不是 afterthought。
- **不要给抽象理论** —— 直接给 schema / 代码 / 例子。
- **不要发明用户没说的术语** —— 用 SC2 玩家真实黑话。
- **不要使用 emoji 除非用户明确要求**（系统级偏好）。

---

## 关键架构决策（一句话各）

| 决策 | 选择 |
|---|---|
| 基础 bot 框架 | **ares-sc2** + python-sc2 (BurnySc2) |
| 部署形态 | **单 SC2 客户端**，玩家以 player slot 加入，bot 接管操作 |
| 输入设备 | **手机 PWA** (Vue 3 + Tailwind)，扫码连接 |
| 视野控制 | **手机小地图拖拽** → bot move_camera |
| 玩家在 PC 上 | **不需要键鼠**，物理隔离即可（不屏蔽，自愈机制兜底）|
| LLM 部署 | **纯云端** (Claude Sonnet 4.6 起步，留接口接 DeepSeekV4) |
| 内部语言 | **Directives JSON** 是唯一中间表示 |
| 优先级机制 | LLM_CONTROLLED role 让 base bot 默认 skip |
| 公平性 | 1.5s 固定生效延迟 + 10s 限频 + APM cap 120 |
| 剧本表达 | 多态 YAML：opening_build / midgame_stance / lategame_doctrine |
| 时机记法 | build steps 用 supply，timing windows 用 game_time |
| 别名机制 | 中央 YAML 表 + verb 消歧（VR 建筑 vs VR 单位）|
| 6 个 ares hook 点 | A Build Runner / B OverrideMediator / C Unit Role / D Rationale Logger / E ViewController / F BuildLocationOverride |

---

## 项目演进 Roadmap

| 版本 | 内容 |
|---|---|
| **MVP (v0.1)** | 神族 3 剧本 vs 内置 AI |
| v0.5 | 神族 8+ 剧本 + Web Inspector |
| v1.0 | 神族完整 + 两笔电 PvP + 本地 LLM fallback |
| v1.5 | 加虫族 / 人族 |
| v2.0 | `compile_strategy` 玩家口述生成新剧本 |

---

## 项目名变迁

- 旧：SpeechCraft / speech_craft（废弃，过于功能性）
- 备选：Adjutant（被用户否决，太 geek）
- **新**：VoiceCraft / voicecraft（直白，玩家秒懂）

如果根目录仍叫 `speech_craft`，不影响代码 —— 但终态会改为 `voicecraft`。

---

## 上一次会话的关键过程教训

设计过程中**用户两次做了高维度的结构性简化**，把我的"多 mode 兼容"推翻：

1. **观察者 / 输入设备模式**：我罗列了 4 种"键鼠 + 手机"组合 mode → 用户提议"手机当唯一控制器、PC 当纯显示器"，整个 mode 树消失
2. **SC2 客户端数量**：我设计了两个客户端（observer + bot）→ 用户进一步推到"单客户端 + player slot"

**经验**：当我在罗列 mode 时，停一下问"是不是有一个更上游的决策能让这些 mode 不存在"。退回最高决策点重新组织，而不是在低层方案上拼装兼容。

另一类教训：**用户也多次推翻"过度简化"**（参见架构决策摘要中的 "observer 重回"、"三阶段剧本分离"）。所以原则是：
- "结构性简化"（重新组织假设）= 用户主导，我配合
- "细节裁剪"（砍掉某层 / 某模块）= 谨慎，先列 trade-off

---

## 工作模式：用户偏好自驱动

用户已授权：**从设计 → 代码尽量不打扰**，目标是把功能写完 + 单测写完，到必须真实启动 SC2 客户端做端到端验证时再喊人。具体含义：

- 不要反复问"要不要这样做"。设计文档已经是真理源，按文档执行。
- 当文档真的有歧义时才问，问得**具体**（"A 还是 B，原因是 X"），不要开放式问。
- 任何**架构层面**的偏离（动了设计文档的决策表）必须问。**实现层面**的选择（库、目录结构、单测用例边界）自己决定，但留 ADR 记录在 `docs/adr/`。
- 自己用 TaskCreate 跟踪进度，让用户随时能看到现在卡在哪。
- 测试驱动：每个模块都要有对应 pytest 单测；core 数据流（Directive Board / 别名解析 / DSL）要覆盖到分支。
- 完成某个 milestone 的"无 SC2"部分（mock 跑通全部单测）后，**才**找用户做端到端。

---

## 任何后续工作的下一步

设计文档 → 代码已开始。当前阶段拆解：

| 阶段 | 范围 | 出口 |
|---|---|---|
| **M0a** 脚手架 | pyproject / 目录 / lint / mypy / pytest / pre-commit / CI 模板 | `pytest` 跑空通过 |
| **M0b** Smoke 代码 | 最小 VoiceCraftBot + Unit Role 排除 demo + mock 单测 | mock 验证 role 隔离 + 一份给用户跑的 SC2 启动脚本 |
| **M0c** 端到端 smoke | 用户启 SC2，验证 "不动的叉子" | 4 个 ares Manager 都 skip LLM_CONTROLLED |
| **M1** 端到端骨架 | Bot service + WS endpoint + PWA 框架 + 1 剧本 + LLM 1 条话语 | 手机说话 → bot 切 1门Robo build |

---

## 实现纪律

- **结构化日志 JSONL**：每次 LLM 调用（prompt 全文 / 响应 / 耗时 / token / 解析后 directives）、每个 directive 进出 Board、每个 Manager hook 触发，都落盘到 `logs/<game_id>/events.jsonl`。
- **两种部署变体接口**：服务端协议必须假定可能被远程客户端连接；不要硬编码 `localhost`。
- **Recipe store 抽象**：剧本不直接 import YAML 路径，走 `StrategyLibrary.get(id)` 接口，未来好替换。
- **不允许 sleep 等真实 SC2**：单测全部 mock python-sc2 / ares 接口。`tests/integration/` 留给端到端，但跳过 default。
- **直接修改 CLAUDE.md** 当：决策变更 / 新建一类约定 / 用户给了新的强偏好。

---

## 会话交接协议（permanent，不要删）

CLAUDE.md 末尾可能存在一个**临时**交接块，边界明确：

```markdown
<!-- HANDOFF-START: ... -->
## 上次会话进度（...）
...
<!-- HANDOFF-END -->
```

这是上一个 session 留给本 session 的 brief（commit hash / 待办 / 用户环境快照
等不在代码里、但本 session 起手就要知道的事）。

**新 session 起手的处理流程**：
1. 启动时把 HANDOFF 块当作权威上下文读
2. 至少完成一次有意义的动作（读文件 / 答用户首个问题 / 跑首条命令），确认已经
   "接住"
3. **接住后立即删除整段 HANDOFF 块**（从 `<!-- HANDOFF-START` 注释那一行到
   `<!-- HANDOFF-END -->` 这一行整段，包含中间所有内容），用 commit message
   `清理 HANDOFF 块（已接住 session N→N+1）` commit
4. 本 session 结束 / 切换 / 用户明示要 handoff 时，**重新**生成新的 HANDOFF 块
   写入 CLAUDE.md 末尾 + commit

**HANDOFF 块的内容规范**（保持紧凑，<60 行）：
- 最近 3-5 个 commit hash + 一句话描述
- 当前里程碑状态 / 等待事项
- 用户环境关键事实（路径、账号、装了什么）—— 仅写"不在代码里、问一遍要花时间"的
- 下一步动作

**不要写进 HANDOFF**：
- 代码细节、架构决策（这些进 CLAUDE.md 永久段或 ADR）
- 用户偏好（这些进 memory）
- 调试日志、思考过程

---

<!-- HANDOFF-START: 2026-05-14 目录从 voice_craft 改名为 voicecraft 后接续。
     本段为临时交接信息，新 session 按"会话交接协议"接住后请整段删除并 commit。 -->

## 上次会话进度（2026-05-14，目录改名后留给新 session 接续）

**已完成 commits**（已 push 到 https://github.com/catmaniii/voicecraft）：
- `44159e1` M0a + M0b 完成：脚手架 + 全部无 SC2 模块 + 126 单测全过
- `47d2b1e` 修正 LLM_CONTROLLED 映射 ares `CONTROL_GROUP_ONE`（ares UnitRole 是
  固定 StrEnum 加不了成员，必须复用 `CONTROL_GROUP_ONE` 这个"留给用户的空槽"）

**当前状态**：等待玩家 M0c 端到端 smoke 验证（"不动的叉子"）。详见
`docs/m0-smoke-runbook.md` 与本仓库根部 `scripts/smoke_test.py`。

**用户环境**（不要再问，直接用）：
- SC2 装在 `D:\StarCraft II\`，最新版本 `Base94137`。**必须设环境变量**
  `SC2PATH=D:\StarCraft II`，python-sc2 默认只找 C:\Program Files 路径
- 用户的 `Documents\StarCraft II\Maps\` 和 `D:\StarCraft II\Maps\` 都不存在，
  需要先下载 1v1 ladder 地图才能跑 smoke
- 用户已装 uv + Python 3.11；本地仓库目录从 `voice_craft` 改名为 `voicecraft`，
  与 GitHub repo 一致；`.venv` 改名后需重建（`rm -rf .venv && uv sync --extra dev`）
- 用户的 GitHub 账户：`catmaniii`，gh CLI 已认证（HTTPS + keyring token）

**版本号 / 里程碑映射**（见 CHANGELOG）：
- `0.1.0a1` ← 当前 HEAD（M0b 完成）
- `0.1.0a2` ← M0c smoke 通过后打 tag
- `0.1.0` ← M5 MVP RC

**下一步**：用户跑完 smoke 给反馈 → 通过则打 tag `v0.1.0a2` + 开 M1
（Bot service + WS endpoint + 手机 PWA 框架 + 1 个剧本 set_build + LLM 单条话语
解析），不通过看 `smoke_report.json` 的 `anomalies_by_kind` 决定回退方案。

<!-- HANDOFF-END -->
