# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# VibeCraft 项目交接文档

> 这份文档由上一次 brainstorming session (2026-05-14) 生成。Claude Code 启动时会自动加载此文件，新会话进项目即拥有完整上下文。

---

## 沟通约定

- **中文沟通**。代码注释、commit message、log 字段名仍可英文（业界惯例），但所有面向用户的解释、设计讨论、PR 描述用中文。
- **建筑用 hotkey 简称**，不写全名。**默认就要这样写**，不需要用户提醒。
  **以下表格全部按 SC2 Standard layout 真实键位**（[Liquipedia 权威](https://liquipedia.net/starcraft2/Hotkeys_per_Race)），
  Workers 先按 B（基础）或 V（高级）开 build 菜单，再按建筑首字母。

  #### 神族建筑 hotkey

  - **B 系列（基础建筑）**:
    - **BG** = Gateway / 兵营 / 折跃门（B+G）
    - **BE** = Pylon / 水晶 / 房子（B+E；**不要**写 PY）
    - **BA** = Assimilator / 气矿（B+A；**不要**写 VC，VC 是议会）
    - **BN** = Nexus / 基地 / 主基地（B+N；**不要**写 NX）
    - **BF** = Forge / 锻炉（B+F）
    - **BY** = Cybernetics Core / 控制核心（B+Y；**不是 BC**！）
    - **BC** = Photon Cannon / 光子炮（B+C；BC 是炮塔，不是核心）
    - **BB** = Shield Battery / 护盾电池（B+B）
  - **V 系列（科技建筑）**:
    - **VR** = Robotics Facility / 机械工厂（仅指建筑；虚空辉光舰不叫 VR，叫"虚空 / 辉光舰"）
    - **VS** = Stargate / 星门
    - **VC** = Twilight Council / 议会 / 暮光议会（V+C；**不是 VT**！）
    - **VT** = Templar Archives / 圣堂档案 / 高塔（V+T；**不是 VA**！）
    - **VF** = Fleet Beacon / 舰队信标（V+F；**不是 VX**）
    - **VB** = Robotics Bay / 球塔 / 巨像塔（V+B；**不是 VD**！）
    - **VD** = Dark Shrine / 黑暗神殿 / 隐刀塔（V+D；**不是 VB**！）

  #### 虫族建筑 hotkey

  - **B 系列（基础建筑）**:
    - **BH** = Hatchery / 孵化场（B+H）
    - **BE** = Extractor / 气矿（B+E）
    - **BS** = Spawning Pool / 母池（B+S）
    - **BV** = Evolution Chamber / 进化腔（B+V）
    - **BR** = Roach Warren / 蟑螂窝（B+R）
    - **BB** = Baneling Nest / 妖虫巢（B+B）
    - **BC** = Spine Crawler / 刺蛇匍匐者（B+C；防御建筑）
    - **BA** = Spore Crawler / 孢子匍匐者（B+A；防空；**不是 BP**！）
  - **V 系列（科技建筑）**:
    - **VH** = Hydralisk Den / 刺蛇巢（V+H）
    - **VD** = Lurker Den / 潜伏者巢（V+D；**不是 VL**！）
    - **VI** = Infestation Pit / 感染坑（V+I）
    - **VS** = Spire / 刺翼（V+S）
    - **VN** = Nydus Network / 尼德斯网络（V+N）
    - **VU** = Ultralisk Cavern / 雷兽洞（V+U）
    - Greater Spire / 大刺翼：刺翼原地升级，没独立 build hotkey

  #### 人族建筑 hotkey

  - **B 系列（基础建筑）**:
    - **BC** = Command Center / 指挥中心（B+C；**不是 BN**！）
    - **BS** = Supply Depot / 补给站（B+S）
    - **BR** = Refinery / 精炼厂（B+R）
    - **BB** = Barracks / 兵营（B+B）
    - **BE** = Engineering Bay / 工程湾（B+E）
    - **BU** = Bunker / 碉堡（B+U）
    - **BT** = Missile Turret / 导弹炮塔（B+T）
    - **BN** = Sensor Tower / 传感器塔（B+N；**不是 BW**！）
  - **V 系列（高级建筑）**:
    - **VF** = Factory / 工厂（V+F；**不是 BF**！）
    - **VS** = Starport / 星港（V+S；**不是 BP**！）
    - **VA** = Armory / 军火库（V+A；**不是 BA**！）
    - **VG** = Ghost Academy / 幽灵学院（V+G；**不是 BG**！）
    - **VC** = Fusion Core / 聚变芯（V+C；**不是 BC**！）

- 单位用中文（叉子 / 不朽 / 追猎 / 闪追 / 凤凰 / 航母 / DT / HT / 母舰 / 高坦 / 暗使 / 探机 / 小狗 / 妖虫 / 蟑螂 / 刺蛇 / 飞龙 / BL / 枪兵 / 坦克 / 医疗船 / 船长）。
- 战术黑话保留：4BG / IAC / Skytoss / 12D / 两矿凤凰 / 闪追 timing / MMM / 12pool 等。
- **跨族 hotkey 歧义**（同字母不同族不同建筑）：
  - **BC**：神族=光子炮 / 虫族=刺蛇匍匐者（Spine） / 人族=指挥中心
  - **VC**：神族=议会 / 人族=聚变芯
  - **VS**：神族=星门 / 虫族=刺翼 / 人族=星港
  - **VD**：神族=黑暗神殿 / 虫族=潜伏者巢
  - **BE**：神族=水晶 / 虫族=气矿 / 人族=工程湾
  
  同一 session 按当前 `--my-race` 种族的 alias 表解析，不会混淆。
- **历史遗留**（2026-05 修正前的"旧约定"，PR 之前的代码注释 / 老 yaml 可能还在用，看到时一起修）：
  - 神族 V 系列曾错为：VT=议会 / VA=圣堂档案 / VX=信标 / VB=黑暗神殿 / VD=球塔（**全错**）
  - 人族曾把 Factory/Starport/Armory/Ghost/Fusion Core 错归到 B 系列：BF/BP/BA/BG/BC
  - 真实键位见上面真值表 + Liquipedia

---

## 常用命令

uv 是主推路径；pip 也能用，见 README。Windows 上 PowerShell 即可。

```bash
uv sync --extra dev                     # 同步开发依赖（首次 / lock 变更后）
uv run pytest                           # 跑全部单测（mock，无 SC2）
uv run pytest tests/unit/test_director.py -x
                                        # 跑单个文件（-x 首失败停）
uv run pytest tests/unit/test_director.py::test_view_directive_bypasses_board
                                        # 跑单条用例
uv run pytest -m integration            # 集成层（mock python-sc2）
uv run pytest -m e2e                    # 端到端（需 SC2 客户端；default 跳过）
uv run pytest --cov=src/vibecraft --cov-report=term-missing
                                        # 覆盖率报告

uv run ruff check .                     # lint
uv run ruff check --fix .               # lint + 自动修
uv run ruff format .                    # 格式化（写回）
uv run ruff format --check .            # 仅检查
uv run mypy src/vibecraft              # 严格类型检查（strict mode）

uv run pre-commit install               # 装 hook（首次 clone）
uv run pre-commit run --all-files       # 在所有文件上跑一次

uv run vibecraft --version             # CLI 占位（M0 stub）
```

**端到端 smoke**（需 Windows + SC2 + `SC2PATH` 环境变量；详见
`docs/m0-smoke-runbook.md`）：

```bash
uv sync --extra dev
uv pip install "git+https://github.com/AresSC2/ares-sc2@main"
uv run python scripts/smoke_test.py
```

pytest 配置（`pyproject.toml`）开了 `filterwarnings = ["error", ...]` —— 任意
未预期 warning 会让测试红。pydantic 自身的 DeprecationWarning 已忽略，新增依赖
前查一下它的 deprecation 噪音。

---

## 项目速览

**VibeCraft** —— 用语音 + 文字指挥 AI 替你操作 SC2 神族，给操作不动的老 SC2 玩家。

- **当前状态**：设计完成（2026-05-14），实现进行中 —— 见仓库实际代码状态
- **MVP 范围**：神族 vs SC2 内置 AI，3 个剧本（1门Robo opening / IAC midgame / Skytoss lategame）
- **预估工期**：12-14 周（M0-M5 里程碑）

### 必读文档（四层职责）

| 文档 | 内容 | 何时看 |
|---|---|---|
| **`docs/plans/2026-05-14-vibecraft-design.md`** | WHY：14 节完整设计真理源 | 任何架构层面工作 |
| **`ARCHITECTURE.md`** | WHAT IS：当前代码实际形态 + 不变量 + 数据流 | 动代码前 |
| **`TASKS.md`** | WHAT NEXT：里程碑拆解 + 当前状态 + 用户环境快照 | **新 session 起手必看** |
| **`USER_GUIDE.md`** | 玩家入门手册 + 话语示例 + FAQ | 改面向玩家功能时 |
| `CHANGELOG.md` | 已发版历史 | 打 tag 时 |

CLAUDE.md 只放**约定 + 指针**，不重复其他文档已有的内容。

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
| LLM 部署 | **纯云端**，provider 可配置切换（当前 DeepSeek V4 走 Anthropic 兼容端点，留官方 Claude 接口；见 ADR 0005）|
| 内部语言 | **Directives JSON** 是唯一中间表示 |
| 优先级机制 | LLM_CONTROLLED role 让 base bot 默认 skip |
| 公平性 | 1.5s 固定生效延迟 + 10s 限频 + APM cap 120 |
| 剧本表达 | 多态 YAML：opening_build / midgame_stance / lategame_doctrine |
| 时机记法 | build steps 用 supply，timing windows 用 game_time |
| 别名机制 | 中央 YAML 表 + verb 消歧（"造建筑" / "出单位" / "研升级"）|
| 6 个 ares hook 点 | A Build Runner / B OverrideMediator / C Unit Role / D Rationale Logger / E ViewController / F BuildLocationOverride |

---

## 代码架构 / 任务进度

为不膨胀 CLAUDE.md（每次 session 都会自动加载），架构与任务追踪拆到单独文档：

- **代码现状 + 不变量 + 数据流** → `ARCHITECTURE.md`（动代码前必看）
- **里程碑 + 当前状态 + 用户环境** → `TASKS.md`（新 session 起手必看）

CLAUDE.md 只保留**约定 / 工作模式 / 关键决策摘要**，详细内容不要复制回来。

---

## 项目名变迁

- 旧：SpeechCraft / speech_craft（废弃，过于功能性）
- 备选：Adjutant（被用户否决，太 geek）
- **新**：VibeCraft / vibecraft（直白，玩家秒懂）

如果根目录仍叫 `speech_craft`，不影响代码 —— 但终态会改为 `vibecraft`。

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

## 模型选择（启 Claude Code session 时挑）

- **设计系统架构 / 方案** → Opus（4.7）
- **写代码 + 单元测试**（按已敲定的设计落地）→ Sonnet（4.6）
- **Debug**（无论代码大小，定位根因要的是推理力）→ Opus

当前 session 如果跟当前阶段不匹配（比如 Opus 在做纯写代码），用户提示前我可以
主动建议"这块建议切 Sonnet 跑"。

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

## 实现纪律

- **结构化日志 JSONL**：每次 LLM 调用（prompt 全文 / 响应 / 耗时 / token / 解析后 directives）、每个 directive 进出 Board、每个 Manager hook 触发，都落盘到 `logs/<game_id>/events.jsonl`。
- **两种部署变体接口**：服务端协议必须假定可能被远程客户端连接；不要硬编码 `localhost`。
- **Recipe store 抽象**：剧本不直接 import YAML 路径，走 `StrategyLibrary.get(id)` 接口，未来好替换。
- **不允许 sleep 等真实 SC2**：单测全部 mock python-sc2 / ares 接口。`tests/integration/` 留给端到端，但跳过 default。
- **装 Python 包先确认在 venv 里,不污染全局**：本项目用 `uv`,新依赖一律走 `uv add <pkg>`（写 pyproject）或 `uv pip install <pkg>`（仅 venv,等价 `.venv/Scripts/pip install`）。**严禁** 在系统 Python 跑裸 `pip install`，不管它装到哪里。装新框架前先 `where python` / `Get-Command python` 确认指到 `.venv/Scripts/python.exe`。
- **直接修改 CLAUDE.md** 当：决策变更 / 新建一类约定 / 用户给了新的强偏好。

---

## 会话交接协议（permanent，不要删）

**交接载体**：`TASKS.md` 顶部的「当前状态」+「用户环境关键事实」两段。这两段
取代了原 CLAUDE.md 末尾 HANDOFF 块的角色 —— TASKS.md 本身就是高频更新的，进度
变动天然落在那里，不污染 CLAUDE.md history。

**新 session 起手流程**：
1. 启动时把 `TASKS.md` 顶部的「当前状态」+「用户环境关键事实」当权威上下文读
2. 至少完成一次有意义的动作（读文件 / 答用户首个问题 / 跑首条命令）

**session 结束 / 切换 / 用户明示要交接时**：
- 更新 `TASKS.md` 顶部「当前状态」段：
  - "最近更新" 日期
  - 当前里程碑 + HEAD commit hash
  - 阻塞 / 等待事项
  - 下一步动作
- 如有新的用户环境事实（路径变了、装了新东西），更新「用户环境关键事实」段
- 当前里程碑的待办勾掉已完成项，列出剩余
- commit message：`TASKS.md：更新 M{n} 状态 / session 交接`

**不要写进交接**：
- 代码细节、架构决策（→ `ARCHITECTURE.md` 或 `docs/adr/`）
- 用户偏好（→ Claude memory）
- 调试日志、思考过程（→ 不要持久化）

