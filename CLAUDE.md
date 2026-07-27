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
- **历史遗留**：2026-05 修正前老 yaml / 注释可能还有错 hotkey（神族 V 系列、人族 Factory/Starport 等曾归错），看到一律以上面真值表 + Liquipedia 为准、顺手改。

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

**⛔ 所有 build 必须适配三族对手（虫/神/人）—— 自验必须三族都测（2026-07-26 用户强铁律）**

任何 build（凤凰/坦克/叉子/坑道…任何 strategy）**必须打三个种族对手都行**，不能"优化到只对某一族有效"就当通用。**自验时对手种族必须三族都覆盖**，不能只测一族。

- 踩过的坑（强教训）：凤凰骚扰整轮优化 + 自验全程设了 `VIBECRAFT_OPPONENT_RACE=Zerg` 只打虫族，
  以为通用；结果玩家 live 打**神族**，凤凰一波出门就撞追猎军、看到农民却杀 0、损 7、6 分钟被 end
  ——虫族女王（慢、可抬清）和神族追猎（blink、成军、抬不完）是**完全不同的对空**，只测一族的优化
  对另一族可能整个不适用（甚至删错了保护，见 F134/D92）。
- **判据**：报"某 build 优化好了 / 验收通过"前，必须确认**三族对手都测过**（各自或 RandomBuild 混测），
  且各族都能打（不是只有虫族好、神族/人族喂兵）。只测一族 = 验收没做完。
- `build_acceptance` 用 `VIBECRAFT_OPPONENT_RACE=<Zerg|Protoss|Terran|Random>` env 指定对手种族
  （不设=Random 混三族）。分族诊断用具体族，验收覆盖用三族分别跑或 Random 多局。
- 战术设计层面：各族对空/军队差异大（虫族女王/hydra/腐化；神族追猎/光子炮/虚空；人族枪兵/导弹塔/
  雷神维京/女妖），避战/抬清/概隐/走位策略可能要**按族分化**，别用一族的假设套三族。

**build_acceptance 验收对手 mix（约定）**

跑一个 strategy 改动的验收时,默认 **1 局 VeryEasy + 3 局 VeryHard** 混合。
不要只跑 VeryEasy —— 它游戏短(~5-7 min),晚期 check(charge_complete /
archon_merge / 攻防 2/2 / 后期 supply 等)的 timing 在游戏结束之后才到 →
无 snapshot,只能 FAIL。VeryHard 局长(10+ min,可能 Tie / Defeat),完整覆盖
中后期 timing。混合跑同时验证早期 build 顺(VeryEasy 一波打完)+ 中后期
timing(VeryHard 局长完整测)。

`scripts/build_acceptance.py` 当前 `--opponent` 只接受单一值,所以分两次跑:

```bash
# 早期 build 验证(1 跑)
.venv/Scripts/python.exe scripts/build_acceptance.py <strategy_id> --runs 1 --opponent veryeasy
# 中后期 timing 验证(3 跑取多数票)
.venv/Scripts/python.exe scripts/build_acceptance.py <strategy_id> --runs 3 --parallel 3 --opponent veryhard
```

只跑一组就报"全 PASS / FAIL 跟实际不符"前,确认是不是漏了另一档对手。

**玩家覆盖 e2e 验收**（Task #311 player override e2e）

验证玩家在游戏中按"全军撤退/进攻/防守"按钮真的让单位执行，不只是 UI 显示。
spec 在 `tests/override_acceptance/<case_id>.yaml`，含 `player_actions` 时间线 +
`army_after_player_action` check。子进程 Director 到点自动 fire 等价 UI 按钮按下。

```bash
# 单 case
.venv/Scripts/python.exe scripts/override_acceptance.py <case_id> --opponent veryeasy
# 8 case 并行(三族 retreat/attack/defend 全覆盖)
.venv/Scripts/python.exe scripts/override_acceptance.py \
  4bg__retreat macro_hatch__retreat bio_stim__retreat \
  1g_robo_immortal__attack_all_in roach_hydra__attack_all_in \
  two_base_tanks__attack_probe \
  phoenix_2base__defend roach_ravager__defend \
  --opponent veryeasy --parallel 4
```

何时跑:玩家 override path 改动后(UI 按钮 / VibeCraftZoneAttack /
combat_intent_override / attack_mode_override)。详细 spec 格式 + 调参法则
见 `docs/override-acceptance-runbook.md`。

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

### 必读文档（四层职责）

| 文档 | 内容 | 何时看 |
|---|---|---|
| `docs/plans/2026-05-14-vibecraft-design.md` | WHY：14 节完整设计真理源 | 任何架构层面工作 |
| `ARCHITECTURE.md` | WHAT IS：当前代码 + 不变量 + 数据流 | **动代码前必看** |
| `TASKS.md` | WHAT NEXT：里程碑 + 当前状态 + 用户环境 | **新 session 起手必看** |
| `USER_GUIDE.md` | 玩家入门 + 话语示例 + FAQ | 改面向玩家功能时 |
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
| 基础 bot 框架 | **sharpy-sc2**（vendored fork）+ python-sc2 (BurnySc2)；M1 已从 ares-sc2 迁到 sharpy，真实 bot 零 ares 代码（旧 M0 `smoke_test.py` 仍用 ares 仅历史遗留）|
| 部署形态 | **单 SC2 客户端**，玩家以 player slot 加入，bot 接管操作 |
| 输入设备 | **手机 PWA** (Vue 3 + Tailwind)，扫码连接 |
| 视野控制 | **手机小地图拖拽** → bot move_camera |
| 玩家在 PC 上 | **不需要键鼠**，物理隔离即可（不屏蔽，自愈机制兜底）|
| LLM 部署 | **纯云端**，provider 可配置切换（当前 DeepSeek V4 走 Anthropic 兼容端点，留官方 Claude 接口；见 ADR 0005）|
| 内部语言 | **Directives JSON** 是唯一中间表示 |
| 优先级机制 | LLM_CONTROLLED role 让 base bot 默认 skip |
| 公平性 | 10s 限频 + APM cap 120(2026-05-26:1.5s commit 延迟去掉,UI 无反悔按钮 → 默认 0;参数化保留,未来恢复改 `DirectorConfig.commit_delay_s`) |
| 剧本表达 | 多态 YAML：opening_build / midgame_stance / lategame_doctrine |
| 时机记法 | build steps 用 supply，timing windows 用 game_time |
| 别名机制 | 中央 YAML 表 + verb 消歧（"造建筑" / "出单位" / "研升级"）|
| 6 个 ares hook 点 | A Build Runner / B OverrideMediator / C Unit Role / D Rationale Logger / E ViewController / F BuildLocationOverride |

---

## 玩家控制权模型（2026-06-08 用户拍板，反复纠结过，按这套来）

**⛔ 最高铁律（2026-07-12 用户强怒拍板）：玩家没确认，绝不自动切战术 / doctrine / 兵种战略。**
bot 不许自己从当前 build 转到另一套打法（如 nydus 坑道 → 自动爆刺蛇/转运营）。`auto_persistent_switch`
在 `opening_completed` 只能 **推荐（swap_plan=False，发 toast）**，玩家在宏观面板**确认**才真换。build 内部
也不许写"到某时间/条件自动改兵种战略"（踩坑：nydus 曾 `time>420` 自动建刺蛇巢 + core_units 含 HYDRALISK
让 sustain 自动爆刺蛇 → 玩家没让切它自己切了，被强怒）。sustain 的 core_units 只配**当前 build 本兵种**
（坑道=蟑螂/狗），转型兵种一律走玩家确认的 `lategame_transitions` 推荐路径。改任何 build 前对照这条。

四条规则，自洽。改任何"玩家指令 vs bot vs 单位归属"逻辑前对照这套：

1. **单位级指令 = 独占 + 最新覆盖**：给一个已被玩家指令控制的单位下**新单位级指令** →
   **抢占**它（旧指令对该单位失效，新指令独占）。一个单位同一时刻只听最新那条。
   - **已实现**（WP-C）：`_assign_standing_order_units` / `_claim_directive_units` 里
     `_current_owner_of` + 从旧主 `_standing_order_tags` discard + `_displaced` 记录 +
     `_supersede_conflicting_moves`。这就是"文字说虚空撤退能覆盖一队进攻"的原理。
2. **全军命令（UI 撤退/进攻/守家按钮、tactical_objective）不碰被 claim 的单位**：
   它走 sharpy `combat_intent_override`，只作用 `free_units`（不含 Reserved）。要让被 claim
   单位听全军命令 → 先取消那条 claim / 解散编队，**或**下单位级新指令覆盖（规则1）。
   - 之前"集中(standby 独占)全军不碰" + 现在"全军撤退不退被 attack-claim 虚空" = **同一条规则**，
     不矛盾。被 claim 就独占，全军只管自由单位。
3. **释放 / 解散单位 → 连带撤销该单位身上所有 in-flight 指令**，彻底还给 bot（不只还 role）。
   - **待补**：`unit_release` 当前只 `release_unit_role`，不撤该单位的 directive 卡。
   - **gap**：`group_clear`（取消编队）当前只清编队定义 + 还 role，**不取消**之前下给该编队的
     指令（如"一队进攻"`unit_claim(group_id:1)`）→ 那条指令残留、每帧 re-Reserve 单位
     （玩家报"取消编队+全军撤退，虚空没退"根因）。取消编队应连带终止"针对该编队"的指令。
4. **撤退用 `move` 不用 `attack_move`**：撤退/回家类移动一律普通 move（遇敌不恋战），别 attack_move。

## 指令组合约定（复杂动作优先组合现有 directive，不轻易新增类型）

**原则（2026-06-01 用户）**：一个复杂玩家动作若能拆成"现有 directive + activate_when 串联"，**就拆，不新开 directive 类型**。`activate_when`（DoneWhen 激活门）+ `_tick_pending_activation` 每帧重查，已支持"等条件满足再激活"的顺序编排。

**范例：代理建造（"派农民去对方11点修水晶"）= 两卡组合，零新类型**
- **卡1** `unit_claim`(persistent)：claim 1 农民 → 派去目标点 → 留在那（Reserved，造完也留原地待命）。
- **卡2** `build_at`（本就带 structure_type + point）+ `activate_when=unit_arrived(point)` + `by_probe=true`。农民到点 → 卡2 激活 → 用 `selector.near_point=该点` 选到那个农民 → 下 `order_probe_build`（不是 placement override）。
- 两卡靠 `unit_arrived` 串联，**卡2 不需显式知道农民 tag**（near_point 自动选到刚到的那个）。
- 支撑代码：`BuildAtPayload.by_probe` 字段 + `_is_activation_satisfied` 的 `unit_arrived` 求值 + `facade.order_probe_build` + build_at 的 by_probe 执行分支。

**神族代理建造链通用约定（2026-06-07 用户）：先修水晶 → 在水晶周围修建筑，都走"水晶建好即刷新后续建筑坐标"**
- 凡是"派农民去〈某点〉修水晶,然后(在那)修 N 个〈任何建筑〉"（VS / BG / Robo / 炮 …，**不限 VS**）：
  整条链 = 1 张水晶 `build_at(by_probe)` + N 张后续 `build_at(by_probe, activate_when=chain_structure_ready(同 chain_id))`，全用 card0 claim 的**同一农民**。
- **关键执行机制（已实现，类型无关）**：水晶(Pylon)`settle` 那一刻，`Director._assign_chain_followup_spots(cid, pylon_pos)`
  把本链**所有还在等的后续 by_probe 建筑卡**的落点坐标**提前刷新**成水晶周围**不同方向**的点
  （`_CHAIN_SPOT_OFFSETS`，±4 格仍在能量场半径 6.5 内、互不重叠），写进 `payload.point`。
  → 每张后续卡各占一边、不再各自现找撞同一格(第2个建筑"找不到位置"卡死的根因)。
- 后续建筑**必须在水晶能量场内**：锚点/落点都以**水晶本体**为中心(回退也优先锚 PYLON,不锚链上另一个建筑,
  否则锚到能量场边缘找不到位)。新增任何"先 Pylon 后建筑"的代理链,**自动**走这套,无需特判建筑类型。
- 卡片状态:激活置 `active`(执行中);建好(settle)链式卡标 `done` 消失(农民由 standby 卡持有),单卡保留待命。

**判断"拆 vs 新类型"**：能用 `现有 verb/directive + activate_when(unit_arrived/tech_done/structure_count/...)` 表达的顺序动作 → 拆；只有当需要**全新的执行语义**（既有 act/verb 都覆盖不了，如"两点无限往返巡逻"）才新增执行器。新增前先问"能不能组合现有的"。

---

## 设计原则：推翻 vs 裁剪

- **结构性简化（重组假设、让多 mode 消失）= 用户主导，我配合**。当我在罗列 mode 时停一下问"是不是有更上游的决策能让这些 mode 不存在"
- **细节裁剪（砍某层 / 某模块）= 谨慎，先列 trade-off**。用户多次推翻我"过度简化"（observer 被重新加回 / 三阶段剧本拆开等）

---

## 推理图谱驱动决策（2026-07-12 用户强要求 → 抽成 skill）

做**设计 / 算法 / 战术 / 架构层面的判断**前，以及遇到**真机异常 / 自验失败 / 预期外行为 / 用户指出
反常**时，走 **`reasoning-graph` skill**（全局 `~/.claude/skills/reasoning-graph/`）。

**通用方法论全部以 skill 的 `SKILL.md` 为准，本文件不重复**（单一源、免漂移）：决策前挂靠图谱 /
禁止悬空 / 不确定性沿依赖链传播、遇反常正向加节点 + 反向审查被冲击结论 + 记 changelog、推理住在
边不塞进节点、原子节点 + rubric 自评、每次刷新派**独立 subagent 复评**（含中文地道检查）、**增量
复评**（`--incremental` 只审变动 + 传递下游、`--stamp` 盖戳、水位记 yaml 头 `review.last_reviewed`）、
提炼记坑要脱敏（→ skill `PITFALLS.md`）。何时用、怎么建/校验/可视化、kind×status 状态机全在 skill。

**本项目特有（不属通用方法论，记这里）**：
- 数据单一源 `docs/reasoning-graph.yaml`；首个 domain = `nydus-landing`；人读 md **已废弃**（变更日志迁回 yaml）。
- 可视化：拖 yaml 进 skill 的 `assets/rg-viewer.html`，或本项目 **`/rg` 路由服务端注入**（刻意**无门控、
  公网可见**——用户 2026-07-14 定：推理图谱是内部研发认知、非敏感，接受公网前门任意访问换裸 URL 便利；
  `server/http.py` `_serve_rg` 有 SECURITY 注释）。
- 一致性门 = skill 的 `rg_validate.py`（`tests/unit/test_reasoning_graph.py` 薄壳调它）；检索走
  `python ~/.claude/skills/reasoning-graph/scripts/rg_query.py --yaml docs/reasoning-graph.yaml ...`。
- 相关记忆 [[reasoning_graph_discipline]]。

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
- **架构设计 / 详细方案设计必须独立评审（2026-06-12 用户强要求）**：每次产出架构层面
  设计或详细实施方案（design doc / implementation plan）后，**动手实现前**，派一个
  **独立 subagent（model=opus，Opus 4.8）**评审：架构合理性、风险遗漏、与现有代码/约定
  的冲突、过度设计（YAGNI）。评审意见回主 agent 逐条处理（采纳改文档 / 不采纳给理由），
  重大问题改完设计再开工。
  - **外部对象的"属性名"同样不许望文生义（2026-07-27 `is_worker` 复盘补）**：判断外部库对象
    "是不是某类"时（`unit.is_worker` / `x.is_flying` / `foo.is_ready` …），**先确认该属性真实存在**
    （`dir()` / 查源码），拿不准就用**引擎一定有的字段**（如 `type_id`）自己组判据。属性比 enum 更
    阴险：enum 名写错还会抛异常，属性配上 `getattr(obj, "名字", 默认值)` **连异常都没有**——恒返回
    默认值，代码看着在工作、那条分支其实从没走到过（真机症状：坑道虫钻出后 `tgt=worker` 恒 0%）。
    **同一个假属性会同时污染产品代码和单测 mock**（mock 按同样的假设建模 → 单测绿得很），所以修完
    要加一条**静态门**禁止该写法复现，光靠行为单测挡不住。
  - **评审不得给"望文生义的外部符号"背书（salvage 复盘补，2026-06-19）**：设计/评审里任何**外部引擎
    的 enum / 常量 / ability / API 名**（SC2 AbilityId/UpgradeId、第三方枚举等），必须标来源
    （`get_available_abilities` 真机核对 / 官方文档），没核对的标 `UNVERIFIED`。评审对 UNVERIFIED
    符号**只能提"需真机核对"，不能背书其正确性**——这次评审反而加固了错误的 `SALVAGEBUNKER` 假设。
- **能自己实测的，直接测，不要问（2026-06-11 用户强要求）**：凡是我有自验手段能验证的东西——
  单测、`build_acceptance` / `override_acceptance` / `stealth_mine_selftest` / `proxy_chain_selftest`
  等真局自验脚本（mock LLM → non-realtime 可并行多开）、截图自验（debug draw 判读）、真局注入
  指令自验——**一律自己跑完拿到 PASS/FAIL 再汇报结论，不要先问"要不要我测"**。判据：只要不需要
  用户亲自坐到 PC 前用手机/键鼠做人工操作，就是"我能自己测"。真正需要用户人工端到端（手机连
  PWA 实操、肉眼看手机面板交互）才喊人。别把"能自验的活"包装成问题抛回给用户。

- **自验必须用"最接近真实"的环境，别在复现不了根因的受限环境里空转（2026-07-07 scout/链绑定复盘，强教训）**：
  很多 bug 的真根因**只在最接近真实的环境才出现**——**有对手的 `build_acceptance`（不是无敌方 sandbox）、
  真 LLM（不是 mock）、真局终态（不是中间 trace）**。选自验手段前先问：**"我要验的这个现象，会不会
  只在真实对战/真实 LLM/真机才出现？"** 若会，就**直接上最接近真实的那个**，别图快用 sandbox/mock。
  - **判据（硬信号）**：**同一现象在受限环境反复调参数不收敛**（如 proxy 放置在无敌方 sandbox 里
    间距 4/5/6/7 调了一圈、2/3↔3/3↔2/5 乱跳、久久不收敛）——**这不是参数没调好，是环境错了**：那个
    环境根本观察不到真根因（scout 抢农民只在有对手时发生），你在拟合一个盲的环境。**立刻停止调参，
    换最接近真实的自验手段**（真机反馈说"农民被拉去探路"→ 换 `build_acceptance` 一验就中，5/5 过）。
  - 与已有两条纪律成体系：**①"3+ 次修复各自冒新问题 = 质疑架构"**（症状在对的环境、但改错了层）；
    **②这条 "受限环境反复调不收敛 = 换环境"**（根本没在能观察到根因的环境里）；**③salvage "验终态非
    中间 trace / 内部自洽≠真机生效"**（链绑定 bug 正是单测+mock 全绿、真局+真 LLM 才暴露的又一例）。
    调试卡住时对照这三条判断当前卡在哪一类。

---

## 实现纪律

- **目标坐标一次规划、锁定、别每帧重选（2026-06-17 用户强规则，通用）**：凡是给单位/建筑下
  一个**位置**——建筑落点、飞行建筑降落点、单位移动/集结/待命目标点、挂件挪位落点……——
  **第一次决定时就算好、缓存住，之后每帧发同一个**，**绝不**每帧调 `find_placement` /
  随机挑一个临时位置重发。每帧重选 = 目标点漂移 → 单位/建筑追一个移动靶 → **抽搐、永远到不了/落不下**。
  - **正确套路**：决定动作那一刻把目标点存进缓存（按 tag / directive id key），动作完成（到位 /
    落地 / 建好）再清缓存；中途每帧只是把**同一个**缓存目标幂等重发。
  - **典型坑（都同一个根因）**：①代理建造农民每帧被 `find_placement` 改落点→走不到（`director.py`
    settle 缓存修的就是这个）；②挂件挪位楼起飞后每帧拿漂移中的飞行坐标重算落点→追移动靶落不下
    （#543，落点在**起飞前**基于稳定地面位置定好缓存）；③航母回家待命抽搐（2026-06-17 真因：
    `named_spot._own_main` 用 `townhalls.first`，多基地时帧间顺序不稳→"main"每帧解析到不同 Nexus
    →standby 目标点跳变→航母追跳变目标抖；修成取距 start_location 最近的 townhall）。
  - **连"解析层"都要确定性**：③不是下游每帧重发的错，是**上游 named_spot 解析本身非确定性**。
    凡是把"名字→坐标"的解析（`townhalls.first` / `units.first` / 任何依赖 Units 帧间顺序的取值）
    当目标，必须保证**同输入每帧同输出**（用 `closest_to(锚点)` / 索引固定点，别用 `.first`）。
  - 写任何"给位置"的逻辑前先问：**这个目标点会不会每帧变？会变就得锁（或让解析确定性）。**
- **结构化日志 JSONL**：每次 LLM 调用（prompt 全文 / 响应 / 耗时 / token / 解析后 directives）、每个 directive 进出 Board、每个 Manager hook 触发，都落盘到 `logs/<game_id>/events.jsonl`。
- **两种部署变体接口**：服务端协议必须假定可能被远程客户端连接；不要硬编码 `localhost`。
- **Recipe store 抽象**：剧本不直接 import YAML 路径，走 `StrategyLibrary.get(id)` 接口，未来好替换。
- **不允许 sleep 等真实 SC2**：单测全部 mock python-sc2 / ares 接口。`tests/integration/` 留给端到端，但跳过 default。
- **装 Python 包先确认在 venv 里,不污染全局**：本项目用 `uv`,新依赖一律走 `uv add <pkg>`（写 pyproject）或 `uv pip install <pkg>`（仅 venv,等价 `.venv/Scripts/pip install`）。**严禁** 在系统 Python 跑裸 `pip install`，不管它装到哪里。装新框架前先 `where python` / `Get-Command python` 确认指到 `.venv/Scripts/python.exe`。
- **设计架构时维护单一数据源**（2026-07-14）：任何"同一份信息存在多处、改动需手工同步"的静态数据源
  设计都要严谨对待、能免则免。确需多份（人读版/机读版/索引缓存/前端副本）就：①明确**唯一真理源**，
  其余**由它生成或严格对齐**，绝不手改派生副本；②加一致性门（单测/CI）校验副本与源一致；③构建期从源
  生成的派生产物（如部署用静态拷贝）不算违反，但要可重新生成、不手维护。**能用"生成"消灭的重复源，就别
  用"手工同步"维持**。踩坑：`reasoning-graph.md` 曾是 `reasoning-graph.yaml` 的人读复制品 + 独占变更日志
  → 双源漂移，已废弃 md、changelog 迁回 yaml 单源。

- **改 `Sc2Facade` 接口必须同步两个实现 + 跑 audit**（2026-06-07 踩坑，影响巨大）：
  `Sc2Facade`（`src/vibecraft/bot/facade.py`）是 `typing.Protocol` —— **运行时不强制实现**。
  新增/改一个 facade 方法时，**两个实现缺一不可**，否则单测全绿、真局必炸：
  1. **`FakeFacade`**（同文件，单测/脚本用的 mock）
  2. **`_SharpyFacadeBase`**（`auto_combat/common_bot.py` 的 `_make_sharpy_facade_base_class`，
     **真实游戏跑的就是它**）
  漏掉 (2) 时：Director 里所有 `hasattr(self.facade, "<method>")` 在真机恒 False（或裸调
  AttributeError）→ 该路径静默失效，而单测用 (1) 有此方法 → 测不出。
  踩坑实例：`release_unit_role` 只在 `FakeFacade` 实现、`_SharpyFacadeBase` 漏了 → 真机里
  **取消任何指令/解散编队/释放单位全部不放手**（单位永久 Reserved，不听全军命令）。
  防回归：`tests/unit/test_facade_release_unit_role.py` 有一条 **Protocol 一致性 audit**
  （`_SharpyFacadeBase` 必须实现 `Sc2Facade` 全部公开方法）—— 加完方法跑它确认不红。
- **LLM 系统提示词改文件不改 code**（2026-05-24 用户，方案 B）：
  - **改 rules / 例子** → 编辑 `docs/llm_prompt/rules.md`（含 `{building_aliases}` 等 3 占位符）或 `docs/llm_prompt/few_shot.md`。`prompt.py` 的 `build_system_prompt` / `build_few_shot` 自动 `read_text()` 加载，**不要再去 .py 里加 string literal**。
  - **改完重 dump 完整快照** → `.venv/Scripts/python.exe scripts/dump_llm_prompt.py` 重生 `docs/llm_system_prompt.md`（rules + 动态 catalog + few_shot 三段拼接，给人 review 用）。
  - **aliases / catalog 仍代码动态生成** → 别名改 `docs/aliases/*.yaml`，剧本改 `strategies/*.yaml`，自动跟数据源同步。**不要**把 alias/catalog 内容硬编码进 md。
  - **真 LLM 验证** → `.venv/Scripts/python.exe scripts/voice_spot_check.py`（27 case，~30s，~$0.025，需 DEEPSEEK_API_KEY）。
- **直接修改 CLAUDE.md** 当：决策变更 / 新建一类约定 / 用户给了新的强偏好。

- **新增 done_when / activate_when 条件 kind 必须三处同步**（2026-06-06 踩坑）：
  加一个新的 `kind`（如 `chain_structure_ready`）时，**三处缺一不可，否则会"解析失败"
  或"门控失效"**：
  1. **Schema**：`src/vibecraft/directives/models.py` 加一个 `class XxxCondition(BaseModel)`
     （`kind: Literal["..."]` + 字段）**并加进 `DoneWhen` 判别联合**。漏这步 → pydantic
     校验失败 → LLM 一发该 kind 整条命令 directives 全被丢 = 玩家看到"解析失败"。
  2. **求值**：done_when 走 `task_monitor.py`（`@register("...")` checker）；activate_when
     走 `director.py::_is_activation_satisfied`（加 `if kind == "..."` 分支）。
     **注意两个求值器是分开的**，按条件用途加对地方（有的 kind 两边都要）。
  3. **LLM prompt**：`docs/llm_prompt/rules.md`（支持 kind 列表）+ `few_shot.md`（示例）
     + 重 dump `scripts/dump_llm_prompt.py`。否则 LLM 不知道这个 kind、不会用。
  改完跑 `tests/unit/test_done_when_models.py`（schema 回归）+ 相关求值器单测。

- **plan 里训练单位只能用"可训练 enum"，且 doctrine plan 必须和 opening 一起测**（2026-06-19
  真局崩整局教训，详见 `docs/pitfalls.md`）：sharpy `ActUnit/TerranUnit(unit_type)` 的 `unit_type`
  **不能是不可训练的占位 enum**（如 `UnitTypeId.VIKING` id 1940，`creation_ability=None` → 运行时
  `act_unit.py:131 calculate_ability_cost(None)` AssertionError 杀整局）。占位名清单与
  `Director._UNIT_NAME_MAP` 同源（VIKING→VIKINGFIGHTER…）。**新增/改 plan（含 doctrine！）后**：
  ① 进 `tests/unit/test_terran_plans_construct.py` 的 `_TERRAN_OPENINGS`（**doctrine 也要进**——
  auto-switch 进来的就是 doctrine，只测 opening 会漏）；② placeholder 审计
  `test_terran_plan_no_placeholder_train_unit` 自动 parametrize 把它扫一遍。**构造不报错≠安全**：
  占位 enum 只有 `execute()` 运行时才崩，靠审计拦死。
- **单帧异常已全局兜底,但仍要修根因**（2026-06-19 用户强要求"所有异常都catch写log方便debug"）：
  `common_bot.on_step` 整体 try/except + `super().on_step()`（sharpy plan）单独再包一层，任何单帧
  异常都被吞 + `logger.exception` 落完整 traceback 到 game log，游戏继续跑。**这是保险不是免罪符**——
  game log 里出现 `on_step ... 捕获异常` / `sharpy on_step 抛异常` 就是有真 bug，照样按根因修。

- **新增/删除/调整 build 必须同步刷新游戏宏观策略面板可选项**（2026-06-18 用户强要求）：
  PWA「宏观策略」面板的可选 build 列表来自 `GET /api/strategies`，server **父进程启动时**
  加载 `StrategyLibrary`（`server/service.py::_load_strategy_library`，自动扫
  `strategies/<race>/*.yaml`）。面板/LLM catalog 都是 catalog 动态生成、**无硬编码清单**，
  但 **catalog 只在 server 启动时加载一次、运行中不热重载**。所以动了任何 build 后：
  1. **yaml 必须过 catalog 校验**：`kind`/字段符合 `OpeningBuild`/`PersistentDoctrine` schema
     （`opening_build` **不接受** `gas_intensity` 等 doctrine 字段；`steps` 必须是合法 BuildStep
     `<supply> <build|train|research|send_probe> <单token obj> [@modifier]`）。**构造单测
     （test_*_plans_construct）测不出 yaml schema 错**——它只测 bot class；yaml schema 错只有
     真局 catalog 校验才崩（#549 踩过 gas_intensity + 非法 steps）。加完跑
     `tests/unit/test_<race>_strategies.py`（开局/doctrine **计数 + id 断言**要同步改）确认收录。
  2. **重启 server** 刷新面板：父进程重载 `StrategyLibrary` 后 `/api/strategies` 才返回新 build，
     面板才出现/更新（运行中的旧 server 看不到新 build——这就是"新加的 build 面板里没有"的根因）。
  3. **重 dump LLM prompt** `scripts/dump_llm_prompt.py`（人看的快照；runtime LLM 同走动态 catalog）。
  一句话：build 动了 → ① yaml 过校验 + 改对应 test 计数/id → ② 重启 server 刷面板 → ③ 重 dump prompt。

- **CHANGELOG 维护 + commit 带 changelog**（2026-06-06 用户强要求）：
  - **每次 commit 前**，先把这次改动作为 changelog 条目写进 `CHANGELOG.md` 的
    `## [Unreleased]`（按日期块 + 新增/变更/修正 分组，Keep a Changelog 格式），
    **并把同样的 changelog 内容写进该次 git commit message**（不是只写一句标题，
    要让 message 本身就是完整 changelog 条目：改了什么 + 为什么 + 影响）。
  - 面向用户的功能/修复每条都要进；纯内部重构/测试微调可合并成一条。
  - 发版打 tag 时把 `[Unreleased]` 收敛成对应版本号段（见本文件版本对应表）。
  - 一句话：**commit message 与 CHANGELOG 同源**，先写 changelog 再 commit，
    绝不出现"改了但 CHANGELOG 没记"。

- **新功能落地必须同步刷新四类文档（2026-06-19 用户强要求，纪律化）**：
  每次新增 / 改动**玩家能感知的能力**（新 directive 类型 / 新 selector 字段 / 新 build / 新玩家
  指令话术 / 新 facade 能力 / 新 act 行为 / 新面板项），**完成该功能时就回头审视并同步刷新**下列
  文档（别等用户发现文档过时才补）：
  - **ARCHITECTURE.md**（WHAT IS：当前代码 + 不变量 + 数据流）—— 新增 directive 类型 / facade
    方法 / hook 点 / 数据流路径 → 更新对应小节；动了不变量必记。
  - **USER_GUIDE.md**（玩家入门 + 话语示例 + FAQ）—— 新玩家指令/功能要加**真实话语示例**
    （玩家怎么说能触发），学得会才算交付。
  - **README.md**（功能一览 / 卖点）—— 对外能力清单变了就更新。
  - **CHANGELOG.md / TASKS.md**（各自已有规矩，照旧）。
  判据：只要玩家嘴里能说出的新指令、面板能看到的新东西、新 build —— 就要问"USER_GUIDE 教了吗？
  README 列了吗？ARCHITECTURE 的数据流/不变量变了吗？"纯内部重构/测试微调不必。
  一句话：**功能交付 = 代码 + 测试 + 这几个文档同源更新**（同 "build 动了同步面板/prompt" 那条精神）。

- **踩坑记录纪律化 + 严重 bug 必做复盘（2026-06-19 用户强要求）**：
  - **踩坑记录**：每踩到一个**非显而易见**的坑（尤其"单测绿/看着对、真机或真局却炸"那种），就追加
    一条（症状 → 根因 → 修法/教训 → ref）。**细节都写坑文件，CLAUDE.md 这里只留指针、不展开。**
    开工前扫一眼相关条目别重复踩；调试卡住翻一翻可能正中。新坑往文件最上面加（倒序）。
  - **踩坑先分类，写对文件（2026-07-14 用户强要求）**：记坑前先判它是哪一类，写进对应文件：
    - **软件/工程/架构坑**（SC2 bot、facade、sharpy vendor、server、构建流程、python-sc2/引擎 API
      用法等）→ **`docs/pitfalls.md`**（项目内）。
    - **推理图谱坑**（用推理图谱方法论建图/维护 viewer 踩的：节点分类错、`deductive`↔`defeasible`
      误判、回声式 verified、叙事泄漏、一节点塞整条链、rg-viewer/脚本 bug 等）→ **推理图谱 skill 自己的
      `~/.claude/skills/reasoning-graph/docs/PITFALLS.md`**（随 skill 走，别的项目复用同一方法也会踩）。
    - **判据**：**换个项目还会踩吗？会 → skill 的 PITFALLS.md；只跟本项目代码/数据有关 → docs/pitfalls.md。**
      项目特有的图谱历史遗留（某节点曾错标、某批节点豁免 grandfather）记本项目 `docs/reasoning-graph.yaml`
      changelog，**不**进通用 skill（同 grandfather 分治原则，相关记忆 [[reasoning_graph_discipline]]）。
    - **提炼进 skill 必须脱敏 + 有逃生门**：把本项目踩的推理图谱坑写进通用 skill 的 `PITFALLS.md` 时，
      **必须脱敏**——只留可复用的通用教训，项目专有名词/领域内容换成中性假想例。**若脱敏之后写不出有
      意义的通用版（教训离不开项目上下文），就别硬塞 skill，留在本项目 `docs/pitfalls.md` 即可。**
  - **严重 bug/问题复盘**：每次遇到**比较严重**的 bug 或问题（例：发布了/差点发布了不工作的功能、
    多次修错方向、假阳性测试骗过自己、真机/真局炸而单测照不出、用户多次质疑才发现），**修完后必须
    独立开一个 subagent（model=opus, Opus 4.8）复盘整个过程**：哪里判断错了、根因链、本该更早怎么发现。
    复盘产出**分两处落地，缺一不可**：
    - **坑的记录（事后）→ 按上面分类写对文件**（软件坑 `docs/pitfalls.md` / 推理图谱坑 skill 的
      `PITFALLS.md`）：症状 → 根因 → 修法 → ref，存档备查。
    - **正向规则（以后怎么做来避免）→ 本文件（CLAUDE.md）**：把教训提炼成**可操作、可检索的纪律**
      加进对应章节（如自验/facade/独立评审等），用"以后必须 X / 凡是 Y 就要 Z"的祈使句，**不是**
      复述坑本身。pitfalls.md 是"摔过的跤"，CLAUDE.md 是"以后怎么不摔"——两者互补。
    轻量小修不必。目的：让"我自己骗过自己"这类问题沉淀成**主动执行的纪律**，而不是同一类坑反复踩。

- **sharpy vendor patch 规则**（方案 D，2026-05-26 决策）：

  vibecraft 玩家覆盖 path（UI 战术按钮 / `combat_intent_override` /
  `attack_mode_override` / `attack_target_override` / `stance_override`）
  直接在 sharpy combat plan 内部加 hook。**不**通过 subclass swap，
  **不**通过 monkey patch。理由：vendor/sharpy 已是 fork（无 git submodule），
  fork 里加 hook 是最 explicit + 调试自然 + instance state 自然挂 self。
  代价：sharpy upstream 升级要手动 merge —— 用 `# vibecraft:` marker
  + `docs/sharpy-patches.md` checklist 控制。

  **当前 patched method**（完整清单见 `docs/sharpy-patches.md`）：
  - `PlanZoneAttack`：`__init__` / `_get_target` / `_should_attack` /
    `_should_retreat` / `_stop_retreat`（vendor/.../zone_attack.py）
  - `PlanFinishEnemy`：`execute`（vendor/.../attack_expansions.py）

  **加新 hook 的步骤**（看到新 sharpy plan 在 execute 内直接派单位）：
  1. 改 vendor 文件：在派单位 call site 之前 read knowledge.vibecraft
     intent / target override 字段，加 `# vibecraft: ...` 注释 marker
     （用 `getattr(getattr(self.knowledge, "vibecraft", None), "<field>", None)` 兜底）
  2. 加进 `tests/unit/test_sharpy_patch_audit.py::PATCHED_METHODS`（audit 自动 parametrize）
  3. 加进 `docs/sharpy-patches.md` 改动清单 + 升级 checklist
  4. 新增对应 hook 行为单测到 `tests/unit/test_sharpy_vibecraft_hooks.py`
  5. 跑：`uv run pytest tests/unit/test_sharpy_patch_audit.py tests/unit/test_sharpy_vibecraft_hooks.py -v`
  6. 跑 e2e 验证：`.venv/Scripts/python.exe scripts/override_acceptance.py
     <相关 case> --opponent veryeasy`

  **判断 sharpy plan 要不要加 hook**：
  - 在 `execute()` 内直接派单位 attack/move（`ai.units.idle` /
    `roles.free_units` / `unit.attack(` / `unit.move(`）→ **要 wrap**
  - 只 `roles.set_task` 标记角色不直接派 → 通常不用 wrap
  - 拉 idle 单位"回家"性质（`PlanZoneGather`）→ **不用 wrap**
    （intent=retreat 时这就是想要的行为）

  **sharpy upstream 升级**：按 `docs/sharpy-patches.md` 的 checklist 操作。
  每次升级跑 `test_sharpy_patch_audit.py` 确认 marker 还在，跑
  `override_acceptance/4bg__retreat` 确认 e2e 真生效。

---

## PWA 连接 + WebRTC 排错（外网测试）

**启动约定（2026-05-31 用户）：server 起来后必须接公网前门，否则手机外网连不上。**
当前公网前门 = **香港 VPS 反向隧道**（首选，2026-06-13 起，多人阶段1 已完成）；tailscale funnel
是早期/备用通道。两者都靠"PC 主动出站连中转机"绕过家用 CGNAT。

```powershell
# 1. server（固定 dev token，详见「常用命令」/ memory）
D:\code\claudecode\vibecraft\scripts\start.ps1 -Token vibecraft-dev
# 2.（首选）连香港 VPS：SSH 反向隧道把本地 8080 暴露到公网前门。断线自动重连,前台 loop。
#    代用户起时走 run_in_background。VPS=root@<VPS_IP>(阿里云香港),key 在 .secrets/。
D:\code\claudecode\vibecraft\deploy\turn\pc-tunnel.ps1
#    → 公网 URL：https://app.<VPS_IP>.sslip.io/?room=vibecraft-dev
#    （隧道:本地8080 → VPS 127.0.0.1:18080 → VPS nginx 反代到 app.*.sslip.io;
#      VPS 还跑 coturn TURN(turns:443)+ STUN,WebRTC ICE 用它中继穿中国防火墙）
# 2'.（备用）tailscale funnel（后台代理 8080；host 固定 <your-host>.<your-tailnet>.ts.net）
& "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8080
# 查状态：tailscale funnel status  → 应见 "/ proxy http://127.0.0.1:8080"
```

- **VPS 前门 URL（首选,任意手机网络可达,不用装 Tailscale）**：
  `https://app.<VPS_IP>.sslip.io/?room=vibecraft-dev`。video 走 VPS 上的 coturn TURN 中继兜底。
- **funnel URL（备用）**：`https://<your-host>.<your-tailnet>.ts.net/?room=vibecraft-dev`（手机需装
  Tailscale，video 才走 ICE 直连 100.94.x；funnel 只代理 HTTP/WS，不参与 media）。
- VPS 部署细节 + coturn/前门一键脚本见 `deploy/turn/`（`setup-coturn.sh` / `setup-frontdoor.sh` /
  `pc-tunnel.ps1`）。TURN 凭证存
  `.secrets/vibecraft-turn.env`（机密,勿提交）。

### `scripts/start.ps1` 用法 + 参数（2026-06-17 整理，重启 server 照这个来）

**前台运行**（`& uv run vibecraft serve`，**Ctrl+C 停**；不自动停旧实例）。**我代用户重启时**走
PowerShell 工具 `run_in_background` 起，跑完一局也不退。脚本自带：从 user env 复制
`DEEPSEEK_API_KEY`/`SC2PATH`、`--no-sync`（**不**重 sync lock —— 手动装的 torch/funasr/asr 不在
lock 里，sync 会把它们当多余删掉、语音就废了）、UTF-8 console。

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Token <str>` | 自动随机 | room token；**固定用 `vibecraft-dev`**（见 memory `dev_server_token`） |
| `-Port <int>` | 8080 | 监听端口（funnel 也代理这个） |
| `-Ip <str>` | 自动探 LAN IP | QR 码里显示的 IP |
| `-NoRealtime` | off（realtime 1x） | SC2 step-paced（比 1x 快，debug 用）；PWA `start_game.config.realtime` 每局可覆盖 |
| `-Quality` | off | 视频低帧率高画质（烂网更清晰，动作更卡） |
| `-VideoFps <int>` | 0=不设 | 显式目标视频 FPS（盖过 `-Quality`） |
| `-AdminToken <str>` | **默认开**（自动取 token） | admin 面板 token（≥**8** 位，2026-06-20 从 16 降）。**start.ps1 默认开 admin**：`-AdminToken` 空时按 `-AdminToken` → `$env:VIBECRAFT_ADMIN_TOKEN` → `.secrets/admin-token.txt` 顺序自动取 token，无需手动传。**实际值存 `.secrets/`**（机密，.gitignore 已排除；**绝不写进 CLAUDE.md / 提交 git**）。取不到 token 才关 admin |
| `-NoAdmin` | off | 显式关掉 admin 面板（覆盖默认开） |
| `-ServerName <str>` | off | 命名 server：解析到 `config/servers/<name>.yaml` 并传 `--config`（加载 name/token/port/ip）。给了它就**不**用默认 `-Token/-Ip/-Port` 覆盖文件值（除非你显式传）。PWA 首页服务器列表显示 yaml 里的 `name`（不再显示完整 URL；server 经 `GET /api/server-info` 暴露 name）。**admin_token 绝不进该 yaml**（加载器硬报错），仍走 `-AdminToken`。例：`-ServerName close_test` |

**标准启动**：`.\scripts\start.ps1 -ServerName close_test`（PWA 列表显示名称 `close_test`；
**admin 默认开**，自动从 `.secrets/admin-token.txt` 取 token）。也可 `-Token vibecraft-dev`
（旧式，admin 仍默认开）。随后接公网前门 —— **首选**
`.\deploy\turn\pc-tunnel.ps1`（连香港 VPS，run_in_background），备用 `tailscale funnel --bg 8080`。

**重启流程**（代用户重启时）：
1. **杀旧 server 进程树**：监听 8080 的进程往往是 `uv.exe`(包装) → `.venv\Scripts\python.exe vibecraft.exe serve`(启动器) → **WindowsApps `python.exe`(真正监听 8080，是启动器的子进程)** 三层。
   `Get-NetTCPConnection -LocalPort 8080 -State Listen` 找监听 PID，连同它的 uv/启动器父链一起 `Stop-Process -Force`；等 ~1.5s 确认 8080 释放。
2. **后台重起** start.ps1（同上参数）。公网前门都指向**本地 8080 端口**（不绑特定进程）：
   - **VPS 反向隧道**（`pc-tunnel.ps1`）：转发到 localhost:8080，自动重连。server 重起后隧道仍有效，
     **通常不用重开**；隧道进程被杀了才重跑（`Get-Process ssh` 确认在；公网 `https://app.<VPS_IP>.sslip.io/?room=vibecraft-dev` HTTP 200 即通）。
   - **funnel**（备用）：配置持久，server 重起后旧 funnel 自动代理新进程，通常不用重开
     （`tailscale funnel status` 确认 `/ proxy http://127.0.0.1:8080` 还在即可）。
3. **健康检查**：等 8080 监听（重依赖 import 要十几秒）→ `Invoke-WebRequest http://127.0.0.1:8080/?room=vibecraft-dev` 应 **HTTP 200**；启动日志见 `bot_service_started` + `asr_warmup_done` + `event_loop_alive`(心跳, lag~0) 即正常。
4. **重启前**先 `Get-Process SC2_x64` 看有没有在跑的真局——有就是有人在玩，别冒失重启（无 SC2 进程 = 没活局，随便重启）。

**三种 URL 都能 work**，关键不是 URL，是手机和 PC 之间有没有可达的 UDP 路径（WebRTC video 走 UDP）。

| URL | 适用 | 备注 |
|---|---|---|
| `http://192.168.X.X:8080/?room=<tok>` | 手机在同 wifi（LAN 段一致） | UDP 直连 |
| `http://100.94.X.X:8080/?room=<tok>` | 手机在外网 + 装了 Tailscale | UDP 走 Tailnet (WireGuard) |
| `https://<host>.<tailnet>.ts.net/?room=<tok>` (funnel) | 同上（任意手机网络 + Tailscale 装好） | HTTP/WS 走 funnel；video 仍走 ICE 直连 100.94.x（funnel 不参与 media） |

**核心原理**：funnel 只代理 HTTP/HTTPS；WebRTC video 不通过 funnel，靠 ICE 自动选最优候选（LAN / Tailnet / 公网）。**手机装了 Tailscale → ICE 候选里就有 100.94.x，UDP 通；没装 → 只能靠 LAN 同段，否则 fail**。

**误区（曾经踩过）**：以为 "funnel URL = 视频不通"。错。funnel URL 视频也能通，前提是手机有 Tailscale。

**server log 看连接来源**（`ws_connected remote=...`）：
- `192.168.X.X` = LAN 直连
- `100.94.X.X` = Tailnet 直连
- `127.0.0.1` = 走 funnel 反代（funnel HTTPS URL）

**WebRTC 状态**：`webrtc_connection_state state=connecting` 之后看：
- → `connected` 成功
- → `failed`（30-60s 超时）= ICE 没找到可达候选。手机外网 + 没 Tailscale 必然这样。

**PWA Service Worker 缓存坑**：切 URL 不生效时（输入新 URL 但浏览器还跳老的），用**浏览器隐私窗口**，避开 PWA 缓存 / 主屏图标快捷方式。

**web 源码改了 PWA 看不到新功能**：`web/src/` 改完必须 `cd web && npm run build`，bundle 会写到 `src/vibecraft/server/static/assets/`，server 直接 serve 这个目录（不需重启 server）。但**手机 PWA Service Worker 会缓存旧 bundle**，刷新页面看不到新版要么用隐私窗口、要么去手机浏览器设置清站点数据。诊断：`grep -l "<新组件名>" src/vibecraft/server/static/assets/*.js` 没结果 = 还没 build。

---

## Build 执行质量自检标准（每次新增/改 build 后**必须**自查，别等用户发现 —— 2026-06-18 用户强要求）

**铁律**：每动一个 build（新增 / 改 / 调参）后，**自己先按下面这套标准跑一遍自检、发现并修掉问题
再汇报**。不允许"执行过程一堆问题自己发现不了、全等用户去发现"。遇到新类型问题 → **迭代补进本标准**
（这是个持续维护的活清单）。一个 build 执行得好 = 下面六条都过：

### ⓿ 运营基础是评价第一标准（2026-07-12 用户强要求，**先过这条再看下面任何一条**）

**评价一把 build 好不好，第一步永远先看"决定停农民 / 发动一波"那个节点之前的运营。** 在还没到停农民
出兵的节点前，**必须**拉满这三条基础运营（任何一条不满足 = 绝对优先改，**其他维度、胜率、战术全都
先不用看**）：

- **农民不停**：前期农民曲线**单调涨、不卡在某个数不动**（读 telemetry `workers`；出现"农民卡 13-14
  几十秒不涨"就是病）。注意 `WorkerSaturationFloorAct` 的 grace 门 `base_count>=2` 才补 → 二矿 ready
  前 Floor 不补，前期农民全靠 plan 的 DRONE step，plan 出满就停 = 卡住的常见根因。
- **人口不卡**：supply 不 block（AutoOverLord 跟上）。
- **女王 + 注卵不停**：女王**早出**（母池一好就出，别拖到 120s+）、**持续出**、`inject_coverage` **接近 1**、
  larva 引擎不断。女王是 hatchery 产**不吃 larva**（只吃矿 + build slot），所以"女王多"不和农民/蟑螂抢
  larva，反而注卵**产** larva —— 女王注卵是整个虫族 build 的 larva 引擎，注卵不满 = larva 荒 = 一切延迟。

**Why**：一把打差能找无数种理由（战术/落点/胜率），但根因**几乎总在这个节点前的运营**。别再用
build_acceptance 的聚合胜率掩盖前期运营烂账（mock 局把这些平均掉、真人一眼看到）。详见 memory
`[[feedback_operation_baseline_first]]`。**这条不过，下面六维不用看。**

一个 build 执行得好 = ⓿ 先过 + 下面六条都过：

| 维度 | 病征（不该出现） | 信号 / 阈值 | 工具 |
|---|---|---|---|
| **① 农民不闲置** | 早期有农民 idle 杵着——不采矿 / 不侦查 / 不造建筑 | telemetry `idle_workers` 早期应 ~0，长期 >0 = 病；`gas_workers=0` = 没人采气；`mineral_workers` 远超 ideal = 过饱和 | 读 `logs/game_<id>/telemetry.jsonl` |
| **② 资源不堆积** | 气 / 钱攒着没花（banking / floating） | build_efficiency M1 `avg_excess_bank`，>500 明显囤（成长期 supply≥180 后不罚） | `scripts/build_efficiency.py <sid>` |
| **③ 产能利用率高** | 产能建筑（兵营/重工/星港/折跃门/larva）闲着不产 | build_efficiency M2 `prod_util`，<0.6 = 产能空 | 同上 |
| **④ 不卡人口 / 不卡资源** | 有钱有产能却卡人口；或资源够却没下单 | build_efficiency M3 `supply_block_time`，>15s 明显（已滤 <4s JIT 健康重叠）；+ M1 资源 | 同上 |
| **⑤ 科技链第一时间到位** | A 建好不马上接 B→C；建筑/升级 timing 落后 spec、串中卡人口/卡资源 | build_acceptance 各 building/upgrade check 在 `at±tol` 内 PASS | `scripts/build_acceptance.py <sid> --opponent veryeasy + veryhard` |
| **⑥ 后劲充足** | opening 后摆烂：supply 卡死、钱涨几万、兵种卡 plan 的 `to_count` | supply 单调涨 ≥180；核心兵种突破 plan 写死的 N | 手动读 telemetry（下方）|

**标准自检流程（动 build 后照跑）**：
1. `build_acceptance <sid> --opponent veryeasy --runs 1` + `--opponent veryhard --runs 3 --parallel 3`
   → 维度 ⑤（timing / 科技链）+ 早期防守活下来（VeryHard）。
2. `scripts/build_efficiency.py <sid>`（读上面那局 telemetry）→ 维度 ②③④ 三维度打分 + 诊断时间线，
   看 `worst_dimension` 是哪个。
3. 手动扫 telemetry snapshot → 维度 ①（`idle_workers`/`gas_workers` 早期）+ ⑥（后劲 supply/兵种，见下）。
4. 任一维度有病 → **调 plan 修回来，别改 spec 数值掩盖**（memory `feedback_recover_metric_dont_relax_spec`）→ 重跑确认。

**后劲是最容易漏的维度** —— `build_acceptance` 主要验早期 timing，中后期"摆烂"(opening 完成后 supply 卡死、钱涨到几万)它捕捉不到。手动读 snapshot 序列：

```python
recs = [json.loads(l) for l in open('logs/game_<id>/telemetry.jsonl')]
snaps = [r for r in recs if r['kind'] == 'snapshot']
for s in snaps[::30]:
    print(f"t={s['t']:.0f} sup={s['supply_used']} ROACH={s['units'].get('ROACH',0)} M={s.get('minerals')}")
```

警示信号:
- supply 长期卡在某数 + 兵种数恰好等于 `ZergUnit(X, N)` 写的 N → opening_completed 信号没触发，`OpeningSustainAct` 永不启动 → 摆烂
- minerals/vespene 攒到 5000+ 持续 → 钱没出口
- BV/EB ready 但 +1/+2 升级链没刷 → tech 链断

典型 case（2026-05-28 macro_hatch 修）:`_opening_done` 用 `ai.structures(HATCHERY)` 没算 Lair morph，主基地升 Lair 后 hatch < 3 → opening_completed 永不 True → 蟑螂卡 28 上限。修法:`ai.townhalls`(合并 HATCH+LAIR+HIVE) + cap 28→80。

战术响应也要测:`scripts/override_acceptance.py <sid>__attack_then_retreat` 验 attack → 90s 后 retreat 切换路径(三族 sequential case 已覆盖)。

---

## 游戏内可视化 · 截图自验法（不用喊用户看手机）

**任何"游戏画面里画的东西"（debug draw 描边/飘字/镜头跟随效果等）我都能自己截 PC 屏判读**，
不用让用户看手机。方法（2026-06-04 验 WP-A 控制边界画框时建立）：

1. **起 realtime 局**：写个最小探针(参考 `scripts/debug_draw_probe.py`)或起真 server，`realtime=True` 让画面渲染。后台跑，等 `in_game` 日志(sc2.main 必打)再截。
2. **PowerShell 抓 SC2 窗口**(全分辨率)：`GetWindowRect` 拿 SC2_x64 主窗口矩形 → `System.Drawing` `CopyFromScreen` 存 PNG。
3. **裁 + 放大**：`System.Drawing` 裁目标区域，`NearestNeighbor` 放大 2-5x(Read 工具会把大图缩小，所以要裁紧 + 放大才看得清)。
4. **Read 那个 PNG 判读**。看够了 `Stop-Process SC2_x64` 清场。

**判读铁律(踩过坑)**：
- **看"不可能是天然美术的形状"，别看颜色**。我曾把**瓦斯泉的绿喷雾**当成画的圈，连续判错两次。要找**线框方盒 / 屏幕 HUD 字 / ASCII 数字**这种地图里绝不会有的东西。绿/红色块很可能是 geyser / 装饰岩 / 矿。
- **先跑对照组验证管线**：solo(单人) 确认画得出来，再测目标场景。不然分不清"没画出来"是功能问题还是截图/代码问题。
- 用**红盒/品红盒/屏幕大字**这种唯一信号做铁证，别用容易混的颜色。

**SC2 debug draw 的硬限制(实测，全部影响功能设计)**：
- **绝不手动调 `client._send_debug()`**：框架每帧 on_step 后自动 flush。手动调会先清空绘制列表，框架那次发现列表空 → 主动发空绘制把你刚画的**擦掉** → 每帧画完即擦，啥都看不到。(WP-A facade.draw_debug_marks 注释里也记了)
- **debug 文字只渲染 ASCII，不渲染中文(CJK)**：`debug_text_world("守瞭望塔",...)` 画框正常但中文标签**全空白**(连缺字框都没有)；`debug_text_world("1",...)` ASCII 数字正常渲染。→ **游戏内标签必须 ASCII/数字**(WP-A 编队用队号"1".."5"、普通指令留空靠框色)，中文名走 snapshot → 手机面板(HTML 渲染中文没问题)。
- **多人(2 bot host/join)局 debug draw 照样渲染**：不受"多人禁 debug"传言影响(实测 solo + 2bot versus 都画得出)。一台 PC 本机双实例 host/join = 真多人，在"是不是多人"维度等价于双机局域网，所以一台机就能验。
- `_send_debug` 里 `except ProtocolError: return` 是**静默吞错**的 → "没报错"不能证明 debug 被接受，只能靠肉眼看渲不渲染。

`scripts/debug_draw_probe.py` 三模式：`--mode solo`(单人对照) / `versus`(2bot 多人) / `wpa`(WP-A 控制边界配色效果)。

---

## 玩家指令链 · 真局自验法（不用喊用户手动测）

**任何"玩家说一句 → bot 执行一串动作"的链路（代理建造、编队指挥、连续指令等），我都能自己起真局注入指令验，不用让用户手动测。** 方法（2026-06-06 建立，验代理建造链拉扯时）：

1. **起真局 + 注入指令**：用 `GameProcess`（见 `scripts/headless_smoke.py` / `scripts/proxy_chain_selftest.py`）起 bot，等 `sc2=playing` 后 `gp.send_command({"type":"command","text":"<玩家话>",...})`，**走完整 LLM→director→facade 真实路径**（不是 mock）。
2. **抓子进程日志**：spawn 前设 `os.environ["VIBECRAFT_SERVER_LOG_PATH"]=<文件>`，子进程 `init_from_env` 会把 logging 镜像进该文件。在要验的代码路径加 **greppable 前缀日志**（如 `PROXYTRACE build_issued/settled ...` 含 tag/type/坐标/chain），跑完 grep 这些行解析、断言（如"水晶 settle 1 次且 chain 绑定 + 2 VS settle + build_issued 不爆"）。
3. **PASS/FAIL 退出码**，测到通过为止。

**空军骚扰 build 必须内置 greppable 走位/路径 trace（2026-07-20 用户强要求，纪律化）**：
凡是空军骚扰 build（凤凰 / 女妖 / 飞龙 / BC 大舰…），骚扰的**走位 + 接近路径几何**（绕后 / 避敌方主基 /
矿后落点 / fight↔flee 切换）是战术核心，且**肉眼、单测、telemetry 都判不了**——单测只证明纯逻辑、
telemetry 只证明"存活 + 杀农民数"，**都证明不了"路径真的绕后、没直穿主基"**。所以每个空军骚扰 build
都要内置一个 **env 开关的 greppable trace**（如 `VIBECRAFT_PHOENIX_TRACE` / `VIBECRAFT_BCRAID_TRACE`），
打出：每帧 **posture**（approach/fight/flee）+ squad 位置 + **到敌方主基距离(dmain)** + 一次算好的**接近
路径 waypoint 列表**。跑真局 grep 判几何：`PHOENIXPATH n_wp>=3`（`plan_avoid_path` 插了避障拐点=绕开
主基）+ `PHOENIXTRACE` approach 阶段 dmain **不塌到很小**（没直穿主基）+ 最终 posture 到 fight/flee
（真抵达矿后区）。**加/改任何空军骚扰 build 时默认就插这类 trace**（照 `bc_raid_act.py` 的
`VIBECRAFT_BCRAID_TRACE` / `phoenix_squad_act.py` 的 `VIBECRAFT_PHOENIX_TRACE` 范式），别等"验不了走位"
才补。相关原则见推理图谱 harass-doctrine 域 D44（空军骚扰通用生存法则）。

**realtime vs non-realtime（关键取舍，踩过坑 2 次）**：
- **non-realtime（`realtime=False`，fast）wall-clock 快 10-100x**，纯 bot build/timing 自验（`build_acceptance` 那类）用它。
- **关键区分:取决于注入用真 LLM 还是 Mock LLM,不是"是否注入指令"**（2026-06-07 又踩,纠正）：
  - **真 LLM 注入 → 必须 realtime（1x）**：fast 太快,真 LLM ~2-3s 延迟期间游戏已过好几分钟,链没时间跑。
  - **Mock LLM 注入（`VIBECRAFT_MOCK_LLM_JSON`,如 `proxy_chain_selftest.py`）→ 用 non-realtime（fast）**：
    mock 0 延迟,没有等 LLM 的问题。注入协程在**父进程** wall-clock sleep(`inject_after`),跟子进程
    游戏速度无关 → fast 下 `inject-after=3s` 注入时游戏已快进到 1-2 分钟(农民/矿都有),链在游戏时间里
    快进完成。**别再给 mock-LLM 自验套 realtime 白等 4 分钟**(realtime 同 wall 只跑到 ~3:17,fast 跑到 7:30+)。
- 典型参数:`proxy_chain_selftest.py`(mock LLM → **non-realtime** + `--inject-after 3 --seconds 150 --no-baseline`,~1-2 min 出结果)。
- **non-realtime 可并行多实例**(用户机器实测同时跑 **4-8 个 SC2 实例**没问题):要验多 case /
  多 seed / inject 多种话语时,**并行**起多局(各自 `GameProcess` 子进程 + 独立 `log_path`)一次跑完,
  别串行干等。realtime 局占满 1x wall 不划算,优先 non-realtime 并行。(同 `build_acceptance --parallel`,
  见 memory「SC2 并行窗口」。)

**判读铁律**：grep 计数要对得上**实际日志格式**（`build_issued tag=.. type=..` 中间有 `tag=`，`grep -c "build_issued type=Stargate"` 会漏匹配 → 用 `grep -c "type=Stargate"` 之类对齐真实串）。

**验终态，别只验中间 trace（2026-06-19 salvage 踩坑，强教训）**：自验脚本断言的必须是**世界真实
终态**（地堡真从 telemetry 里消失、单位真到点、矿真采到），**不能**只看 bot 自己打的"我做了 X"
trace（`SALVAGETRACE salvaged=1` 这种）。trace 只证明"代码走到了那一步并打了日志"，**不证明 SC2
真的执行了**——salvage 那次 `salvaged=1` 照打、`cast fail=False`，但地堡根本没拆掉（ability 被
python-sc2 filter 丢了 + ability enum 用错，真机 `ActionResult.NotSupported`）。凡是"发个 ability/
命令给 SC2"的自验，终态信号（telemetry 建筑/单位计数变化）才是铁证；顺带把 `_do_actions` 的
`ActionResult` 返回值记日志，非 Success 就是被 SC2 拒了。**单测绿 + 中间 trace 绿 ≠ 真机生效。**

**配套的三条正向纪律（salvage 复盘提炼，2026-06-19，主动执行）**：
- **验收必须有一个"外部终态黑盒门"**：单测 / 真 LLM 解析 / 独立评审都是"内部自洽"检查（验我方代码/解析/设计互相一致），**全绿只是内部回声**。凡是把命令发给外部引擎（SC2 / LLM / API / DB）的功能，DoD 固定两条——①断言**外部世界终态**变化（不依赖任何内部假设的黑盒）②核对**引擎返回**（`ActionResult==Success` / 目标在 `get_available_abilities` 里）。两条都过才算完。
- **同源的多个验证是回声、不是独立证据**：单测 mock、LLM prompt、设计文档全从同一个假设长出来时，一致通过**不增加置信度**（这次 `SALVAGEBUNKER` 错 enum 骗过了全部三道门）。别靠"叠加同源验证"刷安全感，要补一条**正交**的外部验证。
- **"风险低 / 用户自己试就知道"是危险信号，不是发布信号**：当结论里冒出"应该没问题 / 残留风险低 / 用户下一把自己试就能确认"，而这功能我**本可自验终态**——那是"我还没验终态"的自白，**立刻自验，别等用户质疑**（salvage 这次等了用户质疑两次才转向真局验证）。

**覆盖类硬门必须断言「被声称覆盖的分支真的执行了」，不能只把 case 喂进去就信（2026-06-29 i18n 门假阳性）**：
写「零泄漏 / 全覆盖」类硬门（i18n 零中文、全 directive 扫描、全分支覆盖…）时，**把一个 case 列进输入
列表 ≠ 它在运行时被执行**。这次本地化门把 `unit_count_built_since` 等 case 列进了 done_when 列表，但
被测对象的前置条件没满足（Director 没建 `task_monitor` → `if ... and tm is not None:` 整段短路跳过），
case 静默落到通用分支返回 ASCII，门绿但真局泄漏中文。**正向规则**：①门里**断言每个分支真被触达**——
要么断言其输出非空/非占位（`assert "重工" in out` 而非只 `assert no_cjk`，因为缺失分支返回 ASCII 也能过
no_cjk），要么让被测对象处于**能走到该分支的完整状态**（这次：`event_bus=EventBus()` 让 task_monitor 非
None + `assert d.task_monitor is not None`）。②对"缺数据就静默回退/跳过"的代码，门要先确保数据齐备，否则
门测的是回退路径不是目标路径。③再叠一条**正交静态门**（如"所有 `_i18n_t` 引用的 key 必须存在且 en 非
None"）堵动态门照不到的回退泄漏。详见 `docs/pitfalls.md` 2026-06-29 条。

**自验聚合判据不能用 best/min-over-all 掩盖 per-instance 失败（2026-06-20 BC 骚扰二矿踩坑）**：
多实例 / 多目标的自验，断言**绝不能**用"所有实例里最好的那个"（`best_min_dist < X`、`any(...)`、
全局 min/max）做门——一个达标就 PASS，会**完整掩盖其余实例的失败**。BC 骚扰旧自验只断言"所有 BC 里
离敌矿最近的那艘 < 15"，主矿那艘 BC 达标就绿，**整整掩盖了二矿那艘被挡在矿外**，导致同一 bug 反复发布。
**正向规则**：N 个单位 / N 个目标 / N 个矿区的行为自验，要 **per-instance / per-target 分别断言**
（每个矿都要有某 BC 真飞到、每个单位都要到位），而不是聚合成一个数。聚合判据 = 假阳性温床。

**同一现象反复修不好 → 停下来质疑"我盯的是不是病根"（2026-06-20）**：BC"矿后点太远"我连改三次
**锚点**都没解决——因为真凶根本不在锚点，在**接近寻路用了个"避开 zone"的工具去够一个"在 zone 内"的
目标**。教训：当**同一个用户现象**改了 2-3 次仍复现，**别再调同一个旋钮**（那说明假设错了）；回到
Phase 1 重新定位，尤其检查"这条数据流里有没有哪一段的**目的和我的目标相反**"（避敌寻路 vs 要冲进敌矿
= 方向自相矛盾）。配合上面"自验要 per-instance"——若早用分矿判据，第一次就会看到二矿 FAIL、不会误以为修好了。

`scripts/proxy_chain_selftest.py`：代理建造链真局自验（派农民去分矿修水晶→修两个 VS，验链不断/不拍重/不抢家里）。
`scripts/salvage_selftest.py`：建筑回收真局自验（structure_override 建**真**地堡 → salvage → 验 telemetry BUNKER 1→0；debug 生的地堡 SC2 拒 salvage，必须真实建造）。

### debug 生单位自验铁律（2026-06-17 验 #543 挂件挪位时建立，下次必再踩）

要验"某建筑在某状态下被 bot 怎么处理"（如挂件位被占→起飞挪位），常用 `debug_create_unit`
凭空生一座建筑摆出场景。三条铁律：

1. **fast 把 ~10min 游戏压成 ~27s wall-clock** → 注入的 `inject_after`（父进程 wall sleep）
   必须 **< 这个墙钟时长**（用 ~6-10s）。设大了（如 30s）= **游戏先结束、注入根本没发**，
   日志里连 `INJECT` 都没有。sandbox + `game_time_limit_s` 决定墙钟时长，先估一下再设 inject_after。
2. **debug 生建筑别往 `map_center` 方向生**：`start_location.towards(center, N)` 会下主基台阶
   落到斜坡/窄过道，那里 `find_placement(addon_place=True)` / 可建格天然稀缺 → 看着像产品 bug，
   其实是落点选址坑。要生在**主基台地本身**（`find_placement(start_location, ...)` 就近），那里
   建造/挂件空间天然富余。更稳的做法：候选点取主基台地一圈偏移，逐个试到能满足场景的那个。
3. **`find_placement(addon_place=True)` 网格分支不可信**（python-sc2 `bot_ai.py:719-722` 走
   `TERRANBUILDDROP_SUPPLYDEPOTDROP` query，对地形过度严格，明明放得下也常返回 None）。验挂件位
   能不能放，用 **`can_place_single(SUPPLYDEPOT, pos.offset((2.5,-0.5)))`**（find_placement 自身
   快路径用的就是它，可靠）。#543 产品 `_find_relocate_spot` 就是因此从 find_placement 改成
   `can_place_single` 由近及远网格扫描。
   - 连带坑：建筑**一起飞就变 `<PARENT>FLYING`**（另一个 UnitTypeId）→ `structures(FACTORY)`
     不含它，遍历父楼时必须**带上飞行变体**否则 LIFT 后永远等不到 LAND。

`scripts/addon_selftest.py --block-addon`：#543 挂件位被占→起飞挪位→落下→挂上 真局自验
（debug 生孤立重工 + 堵挂件位 + 注入"重工下科技挂件"，验 LIFT/LAND/挂件真挂上）。

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
  - 当前里程碑（"现在在哪个 commit" 用 `git log` 看，不在 TASKS.md 里记 hash）
  - 阻塞 / 等待事项
  - 下一步动作
- 如有新的用户环境事实（路径变了、装了新东西），更新「用户环境关键事实」段
- 当前里程碑的待办勾掉已完成项，列出剩余
- commit message：`TASKS.md：更新 M{n} 状态 / session 交接`

**不要写进交接**：
- 代码细节、架构决策（→ `ARCHITECTURE.md` 或 `docs/adr/`）
- 用户偏好（→ Claude memory）
- 调试日志、思考过程（→ 不要持久化）

