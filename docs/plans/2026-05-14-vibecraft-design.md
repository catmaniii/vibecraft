# VibeCraft 设计文档

| 字段 | 值 |
|---|---|
| 项目名 | VibeCraft |
| 起草日期 | 2026-05-14 |
| 状态 | 设计完成，待实现 |
| 主语言 | Python 3.11+ (服务端) / TypeScript + Vue 3 (前端) |
| 主框架 | ares-sc2 / python-sc2 (BurnySc2) |
| 文档配套 | [USER_GUIDE.md](../../USER_GUIDE.md) (玩家手册) |

> 本文档是 VibeCraft 全部架构和实现决策的唯一真理源 (Single Source of Truth)。任何后续修改请同步本文件。

---

## 1. 项目概述

### 1.1 愿景

让"操作不动的老 SC2 玩家"重新回到星际战场。**玩家用语音 + 文字指挥 AI bot 替自己操作 SC2**，自己专注于战略层。

### 1.2 目标用户

- 当年打过 SC1/SC2，战略基础扎实，但年纪增长 / 手速衰减，跟不上 RTS 操作节奏的老玩家
- 兼顾"想和老朋友再来几把但不想拼操作"的休闲 SC 群体

### 1.3 产品边界

| ✅ 做的 | ❌ 不做的 |
|---|---|
| 单机 vs SC2 内置 AI | 上 Battle.net 天梯（反作弊会封号）|
| 两台笔电 PvP（未来）| 三方混战 |
| 神族 MVP，三族未来 | 自定义新种族 |
| 中文为主，英文兼容 | 多语言（暂时）|

### 1.4 核心价值

- **玩家是指挥官**：战略决策 + 关键微操
- **AI 是参谋副官**：后勤 + 基础操作
- **手机是指挥台**：所有输入在这
- **大屏是战场展现**：所有视觉在这
- **完全脱离 PC 外设**：键鼠可有可无

---

## 2. 整体架构

### 2.1 三层结构

```
┌────────────────────────────────────────────────────┐
│ ① 手机客户端 (PWA / 网页, 玩家手机浏览器)             │
│   - 系统输入法（语音 + 文字编辑）                     │
│   - 驾驶舱 UI (剧本 / overlay / rationale / log)    │
│   - 小地图触屏拖拽改 SC2 大屏视野                     │
│   - 10s cooldown + 撤销 + 撤销 standing orders       │
└────────────────────────────────────────────────────┘
              ↓↑ WebSocket (事件驱动 + 5s 心跳)
┌────────────────────────────────────────────────────┐
│ ② Bot 服务进程 (Python, 跑玩家 PC)                   │
│   ├─ HTTP+WS endpoint (serves PWA + 实时通信)        │
│   ├─ Intent Parser (云端 LLM client)                │
│   │    Provider: Claude / GPT, 可换 DeepSeekV4       │
│   ├─ Directive Board (state + 仲裁 + 生命周期)        │
│   ├─ Strategy Library (3 份神族剧本 YAML, 多态)      │
│   ├─ Rationale Logger (Manager hook → 事件流)        │
│   ├─ ViewController (camera 控制)                    │
│   └─ VibeCraftBot (ares-sc2 子类)                  │
│        - Build Runner (剧本切换)                     │
│        - OverrideMediator (拦截关键决策点)            │
│        - Unit Role Manager + LLMControlBehavior     │
└────────────────────────────────────────────────────┘
              ↓ python-sc2 protobuf
┌────────────────────────────────────────────────────┐
│ ③ 本地 SC2 客户端 (Windows retail, 单一实例)         │
│   玩家作为 player slot 加入 (key+mouse 可不用)       │
│   Bot 通过 python-sc2 接管该 slot 的操作              │
│   对手 = SC2 内置 AI (难度可调)                       │
└────────────────────────────────────────────────────┘
```

### 2.2 一条指令的端到端生命周期

| Step | 在哪儿 | 做什么 |
|---|---|---|
| 1 | 手机 | 玩家说"切到双矿凤凰，凤凰好提对方农民" → 输入法转文字 |
| 2 | 手机 | 检查 10s cooldown OK → WS `command` 帧上行 |
| 3 | ② Intent Parser | 拼 prompt = 玩家话语 + 当前 strategy + 游戏摘要 + 战术目录 + 别名表 → 调云端 LLM |
| 4 | ② Intent Parser | LLM 返回 directives JSON 数组（带 confidence、interpretation_zh）|
| 5 | ② → 手机 | echo "我理解为 A, B"（带 1.5s 撤销按钮）|
| 6 | ② Directive Board | issued_at + 1.5s 固定延迟后 commit directives，进入仲裁 |
| 7 | ② VibeCraftBot | Build Runner 切 `2base_phoenix.yaml`；overlay 入队 |
| 8 | ② → ③ | ares Managers 下个 tick 读 Directive Board → 改行为，发 SC2 API |
| 9 | ③ → ② | 凤凰造好 → Unit Role Manager 移到 HARASS_WORKERS role |
| 10 | ② LLMControlBehavior | 该 role 单位执行骚扰行为，到死或撤回归还 |
| 11 | ② Rationale Logger | 整个过程关键决策点推到手机驾驶舱 + logs |

### 2.3 关键架构原则

1. **Directives JSON 是唯一内部语言**：所有输入（语音 / 文字 / 按钮 / 未来快捷键）都最终生成 directives 数组，进同一 Directive Board
2. **人机视角解耦**：bot 不调用 `move_camera()`，玩家相机控制完全独立
3. **绝不冒险执行**：LLM 解析失败 → bot 状态完全不变，玩家自己决定重发
4. **架构纪律预留扩展**：连接协议、剧本目录、recipe store 等接口从第一天起就为远程 / 多用户 / 新模式留出接入点

---

## 3. 基础 bot：ares-sc2 集成

### 3.1 框架选型

**最终选定**：[AresSC2/ares-sc2](https://github.com/AresSC2/ares-sc2) (v3.7+)

理由：
- **Manager + Mediator 架构**：所有决策走 `mediator.xxx()`，对外部 LLM 注入极其友好
- **Build Runner 支持运行时切换 YAML build**：直接对应"剧本切换"需求
- **Unit Role Manager 原生**：天然支持把单位移到 `LLM_CONTROLLED` role
- **生产验证**：Eris bot（虫族）用 ares-sc2 在 2026 Season 2 AI Arena 排第 3
- **活跃维护**：v3.7.2 (2026-04-30)，1200+ commits in 18 月

替代选项调研结果：sharpy-sc2（ares 的精神前身，维护较慢）/ MicroMachine（C++，与 Python 编排层冲突）/ PySC2（feature-layer RL 接口，不适合我们）。

### 3.2 6 个 hook 点

不 fork ares 代码，全部走已有扩展机制：

| Hook | 处理的 directive type | ares 模块 | 实现方式 |
|---|---|---|---|
| **A** Build Runner 切换 | `strategy_set` | `build_runner` | 调 `set_build("name")` |
| **B** OverrideMediator | `production_override` / `tech_override` / `expansion_override` / `engagement_constraint` | Manager via mediator | wrap mediator 拦截关键查询方法 |
| **C** Unit Role + LLMControlBehavior | `unit_claim` / `scout` / `move` | Unit Role Manager | role 改 `LLM_CONTROLLED` + 自写 behavior |
| **D** Rationale Logger | (旁观, 不处理 directive) | 所有 Managers | `@logged` 装饰器 + asyncio.Queue |
| **E** ViewController | `view_move` / `view_follow` / `view_zoom` | `bot.move_camera()` | 直接调 API |
| **F** BuildLocationOverride | `build_at` | `mediator.request_building_placement` | wrap 拦截 placement 查询 |

### 3.3 部署细节

- **操作系统**：仅 Windows（Linux SC2 只有 headless，无法渲染玩家视图）
- **SC2 客户端**：单一实例，由 bot service 启动并接管 player slot
- **资源占用估算**：CPU ~30%（i5-12400 基线）、RAM ~3-4 GB、GPU 中等
- **依赖锁定**：`burnysc2==<commit>`，Blizzard patch 后可能需要手动同步上游修复
- **EULA 风险**：仅自定义房间 / 局域网，绝不接 Battle.net 天梯，规避反作弊

#### 启动时序（两阶段）

玩家全程只需双击 **一个脚本**，其余都在手机 UI 完成。SC2 客户端**不是** bot
service 一启动就拉起，而是分两阶段 —— 这样 WS 连接能在 SC2 启动前建好，启动
进度 / 失败原因才推得到手机。

```
阶段 1：玩家双击一键脚本
  → 起 bot service：HTTP（PWA 静态资源）+ WS endpoint
  → PC 屏幕弹窗 / 极简本地页显示二维码 + IP:port 明文（扫码失败可手输）
  → 此时 SC2 还没启动

阶段 2：玩家手机扫码 → PWA 连上 WS → UI 显示「已连接」
  → 玩家在 UI 里点「开始对局」（上行 start_game 帧）
  → bot service 调 python-sc2 run_game() 拉起 SC2（独立子进程）
  → 启动阶段实时上行 game_status 帧（见 §9.3）：launching → in_game → playing
```

要点：
- **WS 必须在 SC2 启动前建好** —— 否则 SC2 冷启动那十几秒的进度 / 失败原因
  （路径错 / 地图缺 / 版本不匹配）推不到手机，玩家只能干等。
- **`run_game()` 阻塞整局** —— bot service 在独立进程 / 线程跑游戏循环，WS
  server 主循环不被它卡住；游戏结束 / 崩溃要能被检测并上报。
- **对局配置**（地图 / 对手种族 / 难度）：MVP 走 `config/` YAML，UI 只有一个
  「开始」按钮；配置入口在 UI 里预留（灰掉），v0.5 做配置界面。
- **玩家碰 PC 的唯一接触点** = 双击脚本 + 看一眼二维码扫码。物理上无法归零
  （扫码前手机还没连上），但已压到最小。

### 3.4 关键风险（M0 必须验证）

**ares Manager 默认是否真的 respect role exclusion？**

- 理论上是（Eris bot 用了，跑到 top 3）
- 实操可能每个 Manager 行为不同
- M0 smoke test 必须验证 ArmyManager / OffensiveManager / DefensiveManager / ProductionManager 都正确 skip LLM_CONTROLLED 单位
- 修复路径：轻 → 改配置；中 → OverrideMediator wrap query；重 → 继承 Manager 写子类

---

## 4. 战术剧本层

### 4.1 三种 kind

剧本按游戏阶段分三种，schema 多态：

| Kind | 时间 | 性质 | 主驱动力 |
|---|---|---|---|
| `opening_build` | 0-5 min | build order 主导 | supply-keyed 步骤链表 |
| `midgame_stance` | 5-12 min | 科技 + timing + 扩张 | entry conditions → commitments → attack window |
| `lategame_doctrine` | 12+ min | 兵种组合 + 交火条令 | target composition + engagement rules |

### 4.2 YAML schema

#### `opening_build`

```yaml
kind: opening_build
id: 1g_robo_immortal
display_name_zh: "1门Robo 不朽开"
aliases: ["1门Robo", "速不朽", "1G Robo"]
matchup: [PvT, PvZ, PvP]

phases:
  - id: opening
    display: "开局"
    subtitle: "13 农 BG"
  - id: tech
    display: "上折跃"
    subtitle: "WG 研究"
  - id: rallying
    display: "集结追猎"
    subtitle: "14 追猎"
  - id: executing
    display: "出发压制"
    subtitle: "6:00 出门"

steps:                      # supply-keyed, 紧凑三段式 "<supply> <verb> <object> [@modifier]"
  - "13 build Pylon"
  - "14 build BG"
  - "14 build 气矿"
  - "16 build Pylon"
  - "17 build VC"
  - "20 build 基地"
  - "21 build 气矿"
  - "22 research 折跃 @chrono"
  - "24 build VR"
  - "32 train Observer"
  - "34 train Immortal"
scout_at: "17 send_probe enemy_natural"

abort_signals:
  - sees: "enemy.units.phoenix.count >= 2"
    then: "transition:1g_stargate"
  - sees: "enemy.units.zergling.count >= 8 AND game.time < '3:00'"
    then: "transition:4_gateway_pressure"

default_transitions:
  - midgame_id: iac_2base
    when: "default"
  - midgame_id: blink_timing_2base
    when: "enemy.has_mech_units"
```

#### `midgame_stance`

```yaml
kind: midgame_stance
id: iac_2base
display_name_zh: "双矿 IAC 重装地面"
aliases: ["叉光", "IAC", "重装地面", "叉光不朽推"]

enter_when:
  - "self.tech.warpgate.done"
  - "self.expansion_count >= 2"
  - "from_opening in [1g_robo_immortal, 4_gateway_pressure]"

commitments:
  units: { stalker: 8, sentry: 4, immortal: 3, archon: 3, zealot: 8 }
  tech: [warpgate, charge, attack_1]
  structures: { gateway: 6, robo: 2, twilight: 1, templar_archives: 1 }
  expansions: 2

attack_window:
  open_at: "9:30"
  close_at: "11:30"
  target_priority: [army, third_base, production]

micro_doctrine:
  - "archon focus_fire bio_clumps"
  - "immortal target high_hp_armored"
  - "if has_sentry_energy: forcefield split_enemy_army"   # 仅在玩家专门下指令时启用

expire_action:
  - "transition: skytoss"

lategame_transitions:
  - lategame_id: iac_ht_lategame
    when: "successful_attack OR damaged_economy"
  - lategame_id: skytoss
    when: "fallback OR enemy.lategame_composition"
```

#### `lategame_doctrine`

```yaml
kind: lategame_doctrine
id: skytoss
display_name_zh: "Skytoss 航母流"
aliases: ["航母流", "Skytoss", "天空", "航母收"]

target_composition:
  carrier: 12
  tempest: 3
  high_templar: 5
  archon: 4
  mothership: 1
  observer: 2

required_tech: [fleet_beacon, air_weapons_3, air_armor_3, psi_storm, graviton_catapult]
required_structures: { stargate: 4, fleet_beacon: 1, templar_archives: 1 }

engagement_doctrine:
  - "spread_units against=storm radius=6"
  - "feedback priority=[viking, ghost, infestor]"
  - "storm priority=clumped_bio min_targets=4"
  - "carrier_kite max_dist=12 retreat_when=carrier_hp<40%"
  - "mass_recall when=fleet_total_hp<40%"

win_condition:
  type: composition_advantage
  description: "靠航母远程DPS慢推，对面无法过 5 矿经济"

counters_against: [bio, roach_hydra, broodlord_infestor]
weak_against: [mass_viking, mass_corruptor, ghost_emp]
```

### 4.3 条件 DSL

通用形式：`<entity>.<attr> <op> <value>`，**禁止任意函数**（沙箱安全）。

```
左值:
  self.tech.<name>.done | started
  self.units.<type>.count
  self.expansion_count
  self.minerals / self.gas / self.supply_used / self.supply_cap
  enemy.units.<type>.count
  enemy.expansion_count
  enemy.has_<feature>           # 例: enemy.has_mech_units
  game.time                     # "M:SS" 字符串
  from_opening                  # 当前剧本的来源 opening id

运算:
  > >= < <= == !=
  AND OR NOT
  括号 ( )
  in [a, b, c]
```

实现：parse 成 AST → 每个 tick 求值。AST 节点对应 ares `mediator.xxx()` 查询。

### 4.4 别名表

中央配置：`docs/aliases/protoss.yaml`

```yaml
buildings:
  Gateway:
    default_display: "BG"
    aliases: [BG, 折跃门, 兵营]
    hotkey: "B+G"           # 仅信息, 不参与匹配
  Forge:
    default_display: "BF"
    aliases: [BF, 锻炉, 攻防塔]
    hotkey: "B+F"
  CyberneticsCore:
    default_display: "VC"
    aliases: [VC, 模拟芯, 模拟]
    hotkey: "V+C"
  RoboticsFacility:
    default_display: "VR"
    aliases: [VR, Robo, 机械, 机械工厂]
    hotkey: "V+R"
  Stargate:
    default_display: "VS"
    aliases: [VS, 星门]
    hotkey: "V+S"
  TwilightCouncil:
    default_display: "VT"
    aliases: [VT, 议会, 黄昏]
    hotkey: "V+T"
  TemplarArchives:
    default_display: "圣堂塔"
    aliases: [圣堂塔, 高塔, 圣堂, 圣堂档案]
    hotkey: "V+W"
  RoboticsBay:
    default_display: "球塔"
    aliases: [球塔, 巨像塔, Bay]
    hotkey: "V+B"
  FleetBeacon:
    default_display: "航母塔"
    aliases: [航母塔, 航母建筑, 信标, 舰队信标]
    hotkey: "V+X"
  DarkShrine:
    default_display: "隐刀塔"
    aliases: [隐刀塔, 黑塔, 黑暗神殿]
    hotkey: "V+D"
  # ... 其余建筑

units:
  Stalker:
    default_display: "追猎"
    aliases: [追猎, 追猎者, Stalker]
  HighTemplar:
    default_display: "HT"
    aliases: [HT, 高圣堂, 高]
  VoidRay:
    default_display: "虚空"
    aliases: [VR, 虚空, 虚空辐射, 虚空战机]    # ⚠️ 与 RoboticsFacility 重名
  # ...

upgrades:
  Blink: { default_display: "闪烁", aliases: [闪烁, Blink] }
  PsiStorm: { default_display: "风暴", aliases: [风暴, Storm, 灵能风暴] }
  ResonatingGlaives: { default_display: "攻速", aliases: [攻速, Glaive, 使徒攻速] }
  # ...
```

**VR 歧义消解**：靠 verb 判定
- `build VR` → 建筑表 → RoboticsFacility
- `train VR` → 单位表 → VoidRay
- LLM 解析时也用上下文（"出 VR" 偏单位、"造 VR" 偏建筑），二级确认机制 fallback

### 4.5 MVP 剧本选择

按§3.3 的"一条完整链路"选 3 个：

1. `1g_robo_immortal` (opening) —— 最稳健万金油
2. `iac_2base` (midgame) —— 慢推主流
3. `skytoss` (lategame) —— 兜底通杀

这条链路覆盖完整一局游戏的三个阶段。后续版本逐步增加。

---

## 5. Directive Board

### 5.1 数据结构

```python
@dataclass
class DirectiveBoard:
    # 三阶段剧本（任一时刻最多一个 active）
    active_opening: Optional[OpeningBuild]
    active_midgame: Optional[MidgameStance]
    active_lategame: Optional[LategameDoctrine]
    current_kind: Literal["opening", "midgame", "lategame"]

    # 叠加的临时指令
    overlays: list[Directive]                          # 后入栈先生效

    # 单位 claim 账本（互斥）
    unit_claims: dict[UnitTag, ClaimRecord]            # tag → 谁占着、到期条件

    # 事件流（向手机 push）
    event_queue: asyncio.Queue[Event]
```

不变量：
- `active_opening / midgame / lategame` 任一时刻最多一个
- 阶段切换严格 `opening → midgame → lategame` 单向
- overlays 是叠加层，**不替换** active 剧本

### 5.2 Directive 类型枚举

```python
class DirectiveType(Enum):
    # 剧本（粗粒度）
    STRATEGY_SET     = "strategy_set"

    # 中粒度调整
    PRODUCTION_OVERRIDE   = "production_override"      # 含可选 building_tag / building_selector
    TECH_OVERRIDE         = "tech_override"            # 含可选 building_tag
    EXPANSION_OVERRIDE    = "expansion_override"
    ENGAGEMENT_CONSTRAINT = "engagement_constraint"

    # 微粒度
    UNIT_CLAIM            = "unit_claim"               # 临时 + 持久 standing order
    SCOUT                 = "scout"
    MOVE                  = "move"
    BUILD_AT              = "build_at"

    # 释放
    UNIT_RELEASE          = "unit_release"

    # 视野（不限频）
    VIEW_MOVE             = "view_move"
    VIEW_FOLLOW           = "view_follow"
    VIEW_ZOOM             = "view_zoom"
```

通用字段：
```python
@dataclass
class Directive:
    id: str
    type: DirectiveType
    payload: dict
    issued_at: float
    effective_at: float                # = issued_at + 1.5  公平性
    scope: ScopeSpec
    priority: int                       # 0-100
    issued_by: Literal["voice", "auto_transition", "abort"]
    source_text: Optional[str]
```

### 5.3 Task schema（含持久指令 + reactions）

```python
@dataclass
class Task:
    primary_action: Action
    reactions: list[Reaction] = []
    role_hint: Literal["defender", "attacker", "harasser", "scout", "none"] = "none"

@dataclass
class Action:
    verb: Verb                          # 见 Verb 枚举
    target: TargetSpec

@dataclass
class Reaction:
    when: str                           # condition DSL
    do: Action
    cooldown_s: float = 0
    priority_within_task: int = 50

class Verb(Enum):
    # 静止 / 移动
    HOLD_POSITION = "hold_position"
    GUARD_POSITION = "guard_position"
    MOVE_TO = "move_to"
    PATROL = "patrol"
    FOLLOW = "follow"
    RETREAT = "retreat"

    # 战斗
    ATTACK_MOVE = "attack_move"
    FOCUS_FIRE = "focus_fire"
    KITE = "kite"
    HARASS_WORKERS = "harass_workers"
    LIFT_TARGET = "lift_target"

    # 技能
    CAST_ABILITY = "cast_ability"

    # 工人 / 建筑
    GATHER = "gather"
    BUILD = "build"
    CANCEL = "cancel"
```

### 5.4 仲裁规则

```python
def resolve_for_unit(unit: Unit) -> ControlOrder:
    # 1. 单位是否被 overlay claim
    if unit.tag in board.unit_claims:
        claim = board.unit_claims[unit.tag]
        if not claim.expired():
            return llm_control_behavior.tick(unit, claim.directive.payload)
        else:
            release_claim(unit.tag)

    # 2. 当前 kind 的 active 剧本的微操 doctrine
    if board.current_kind == "lategame":
        doctrine = board.active_lategame
        if order := doctrine.engagement_doctrine.match(unit, game_state):
            return order

    # 3. ares 原生 Manager 默认决策
    return ares_default_unit_behavior(unit)


def resolve_production() -> list[ProductionOrder]:
    reserved = []
    for ov in sorted(board.overlays_of_type(PRODUCTION_OVERRIDE), key=lambda o: -o.priority):
        if can_afford(ov.payload):
            reserved.append(ov)

    remaining = total_resources - sum_cost(reserved)
    current_playbook = get_active_playbook(board.current_kind)
    plan = current_playbook.production_plan(remaining, game_state)

    return reserved + plan
```

核心原则：
- 单位 claim **互斥**：一个单位同时只被一个 overlay 持有
- 资源 overlay **优先 reserve**
- 当前 kind 剧本是 default 行为提供者
- 同类 overlay **后下达优先**

### 5.5 阶段转换 / 剧本切换语义

| 切换 | 触发 | 用户感知 |
|---|---|---|
| opening → midgame | opening 的 `default_transitions` 满足 | 自动，push `kind.upgraded` |
| midgame → lategame | midgame 的 `lategame_transitions` 满足 | 自动 |
| abort 切剧本 | `abort_signals` 触发 | push `alert.aborted` + reason |
| voice 切剧本 | 玩家说"切到 X" | push `info.voice_strategy_change` + echo |

冲突优先级：`voice > auto_transition > abort`

### 5.6 1.5s 固定生效延迟

```python
def tick(game_time: float):
    for d in incoming:
        d.effective_at = max(game_time, d.issued_at + 1.5)
        board.overlays.append(d) if d.is_overlay() else apply_strategy_change(d)

    for d in board.overlays_pending:
        if game_time >= d.effective_at:
            commit_overlay(d)
            emit_event("directive.committed", d)

    for d in board.overlays_active:
        if d.scope.expired(game_time, game_state):
            release(d)
            emit_event("directive.released", d, reason=...)

    check_phase_transitions()
    check_abort_conditions()
```

作用：
- LLM 解析快慢不影响公平性
- 给玩家 1.5s 撤销窗口

---

## 6. 基础 bot 能力标定

### 6.1 设计哲学

**bot = 后勤兵 + 普通士兵**；**玩家 = 指挥官 + 关键微操特种兵**。

| 维度 | bot 默认 | 玩家补 |
|---|---|---|
| 后勤 / 经济 | STRONG | — |
| 建造 / 兵种生产 | STRONG（严格按 YAML）| production override |
| 基础战斗 | MEDIUM | 临时夺权 |
| 战略决策 | WEAK / 保守 | **核心价值** |
| 高难微操 | **OFF** | 必须语音 |
| 侦察 | MEDIUM | 话语补 |

### 6.2 bot_difficulty.yaml

```yaml
profile: default

actions:
  apm_cap: 120                          # 人类高手 200-400
  reaction_latency_ms: 300

build_discipline:
  supply_tolerance: ±2
  worker_cap_per_base: 22
  supply_block_recovery_s: 5

micro:
  focus_fire: enabled
  stutter_step: enabled
  blink_retreat: enabled
  blink_harass: disabled
  immortal_barrier: enabled

  # 默认 OFF，留给玩家
  force_field: disabled
  psi_storm: defensive_only_min_4
  phoenix_lift: disabled
  disruptor_nova: disabled
  feedback: enabled_low_priority

strategy_default:
  initial_opening: 1g_robo_immortal
  default_midgame: iac_2base
  default_lategame: skytoss
  attack_decision: defer_to_player

scouting:
  initial_probe_scout: enabled
  observer_at_main_choke: enabled
  recurring_air_scout: disabled
```

### 6.3 玩家沉默时（auto-pilot）

`1门Robo → IAC → Skytoss` 全程默认，不主动出门，资源用完堆人口。

标定目标：vs Easy 稳赢、vs Medium ~70%、vs Hard ~50%、vs Harder/Elite 输。

### 6.4 LLM 错误时

bot 状态完全不变，继续 auto-pilot。手机推 `parse_error.*` 给玩家。

### 6.5 难度档位（未来）

```yaml
profile: easier      # APM 80, 反应 500ms, 不放 Storm
profile: default     # 上面定义
profile: harder      # APM 200, 主动 FF, PvP 公平场景
```

切 profile 不改代码。

---

## 7. LLM Intent Parser

### 7.1 责任边界

| 做 | 不做 |
|---|---|
| 自然语言 → directives JSON | 不下任何 SC2 API 调用 |
| 别名 normalize | 不评估剧本能不能赢 |
| 剧本目录里挑现有 strategy_id | 不发明新 strategy_id（除非 compile_strategy 模式）|
| 错误就报错 | 不"近似猜测"半懂半不懂的指令 |

### 7.2 JSON 输出 schema

```python
class IntentParseResult(BaseModel):
    interpretation_zh: str
    confidence: float                    # 0-1
    directives: list[Directive]
    notes: Optional[str]
```

confidence < 0.6 → 触发 `parse_ambiguous`，手机弹窗二次确认。

### 7.3 Prompt 结构（4 段）

1. **System prompt** (静态, cached, ~3K tokens): 角色 + 任务 + 输出 schema + 别名表 + 不允许行为
2. **Strategy Catalog** (静态, cached, ~1K tokens): 全部可用剧本一览
3. **Few-shot** (静态, cached, ~1K tokens): 8-10 个典型话语 → directives 配对
4. **Dynamic context** (每次新, ~500-1K tokens): 当前游戏内秒 / 剧本 / overlays / 最近事件 / 资源摘要 / 最近 3 句话

走 Anthropic / OpenAI prompt caching，1-3 段缓存，单次 call 主要计费第 4 段。

### 7.4 Provider 抽象

```python
class LLMProvider(Protocol):
    async def parse(
        self,
        system: str,
        context: str,
        user_text: str,
        schema: dict,
    ) -> IntentParseResult: ...

# config/llm.yaml
provider: claude
model: claude-sonnet-4-6
fallback_provider: openai
fallback_model: gpt-4o-mini
timeout_s: 3.0
max_retries: 1
```

支持 Claude / GPT / DeepSeek（OpenAI 兼容协议）。

### 7.5 成本预估（MVP）

| 项 | 数 |
|---|---|
| 静态 prompt (cached) | ~4K tokens |
| 动态 context | ~700 tokens |
| 输出 | ~200 tokens |
| 单次 call cost | ~$0.005 |
| 一场 20 分钟玩家指令数 | 20-60 条 |
| 单场成本 | **$0.10-0.30** |

规模化后 DeepSeekV4 / Haiku 可降到 $0.02-0.05/场。

### 7.6 错误处理

```python
async def parse_intent(text: str, context: Context) -> ParseResult:
    try:
        raw = await provider.parse(system, context, text, schema, timeout=3.0)
    except TimeoutError:
        return ParseError(kind="timeout", message="LLM 响应超时")
    except json.JSONDecodeError:
        return ParseError(kind="invalid_json", message="解析失败")

    try:
        validated = IntentParseResult.model_validate(raw)
    except ValidationError as e:
        return ParseError(kind="schema_mismatch", message=str(e))

    for d in validated.directives:
        if d.type == "strategy_set" and d.payload["strategy_id"] not in strategy_registry:
            return ParseError(kind="unknown_strategy",
                              candidates=fuzzy_match(text, strategy_registry, k=3))

    if validated.confidence < 0.6:
        return AmbiguousParse(result=validated, ask_confirm=True)

    return validated
```

**关键**：任何异常 → bot 状态完全不变。

---

## 8. 指令粒度完整覆盖

### 8.1 四档粒度

| 粒度 | 例 | Directive type | ares 对接 | python-sc2 API |
|---|---|---|---|---|
| A. 大略 (剧本) | "切到双矿凤凰" | `strategy_set` | Hook A | — |
| B. 中略 (全局调参) | "下个 BG 出哨兵" / "先研闪烁" / "守家" / "开三矿" | `production_override` / `tech_override` / `engagement_constraint` / `expansion_override` | Hook B | — |
| C. 具体单位 | "凤凰举不朽" / "DT 偷家" / "圣堂放风暴" / "侦察 11 点" | `unit_claim` (含临时 + 持久) / `scout` / `move` | Hook C | `unit.attack/move/use_ability` |
| D. 具体建筑 | "这 Robo 改造 Observer" / "11 点盖水晶" / "拆这个" | `production_override` (带 building_tag) / `build_at` / `unit_claim` (target=building) | Hook B 扩展 + Hook F | `building.train/research/cancel` |

### 8.2 优先级机制

```
LLM_CONTROLLED role 的单位:
  ares Manager   →  默认 skip
  LLMControlBehavior  →  独占控制权
  army_strength 计算  →  按 role_hint 决定计入哪部分
```

**这是"优先级高于 base bot"的实现** —— base bot 不知道这个单位存在。

### 8.3 归还机制

`unit_release` directive：

```python
class UnitRelease(BaseModel):
    selector: Selector
    return_to_role: Literal["IDLE", "ARMY"] = "IDLE"
```

玩家话语 → LLM 解析：
- "那个叉子回来" → `selector: {tag: 12345}`
- "守家的都解散" → `selector: {claimed: True, primary_verb: hold_*}`
- "全部撤销" → `selector: {claimed: True}`

### 8.4 技术可行性 verify

ares + python-sc2 提供所有需要的原语：
- `unit.use_ability(AbilityId, target)` —— FF / Storm / Lift / Blink / Time Warp
- `unit.hold_position() / patrol / move`
- `bot.enemy_units.closer_than(8, point).filter(...)` —— 范围 + 类型过滤
- `unit.energy / shield / health_percentage` —— 状态属性
- ares MapAnalyzer —— main_ramp / choke / expansion 解析

**唯一存疑点**：ares Manager 默认 respect role exclusion 的程度，M0 验证。

### 8.5 实现工作量盘点

| 模块 | 代码量 | 复杂度 |
|---|---|---|
| LLMControlBehavior (rule engine + DSL evaluator + cooldown) | 300-500 行 | 中 |
| MapLocationResolver | 100-150 行 | 低 |
| UnitStateStore | 100-200 行 | 低 |
| ares Manager exclusion wiring | 50-100 行 | 中 |
| Verb dispatcher | 200-300 行 | 低 |
| **总计** | **750-1250 行** | 3-5 天 |

---

## 9. 手机驾驶舱 + WS 协议

### 9.1 PWA 技术栈

```
框架: Vue 3 + Tailwind CSS
通信: 浏览器原生 WebSocket
本地存储: IndexedDB (小地图地形纹理, recipes)
渲染: HTML5 Canvas (小地图) + Vue components (其余)
分发: Bot 服务的 HTTP server 提供静态资源
```

### 9.2 连接 / 配对 / 开局

完整冷启动两阶段见 §3.3「启动时序」。配对 + 开局链路：

```
玩家双击一键脚本 → bot service 起（HTTP + WS）
  ↓ 生成 room_token
PC 屏幕显示 QR 码 + IP:port 明文:
  http://192.168.x.x:8080/?room=<token>
  ↓
玩家手机扫码 → 浏览器加载 PWA → 建立 WS:
  GET ws://192.168.x.x:8080/ws?room=<token>
  ↓
Bot 验 token → UI 显示「已连接」，推 idle snapshot（SC2 尚未启动）
  ↓
玩家在 UI 点「开始对局」→ 上行 start_game 帧
  ↓
bot service 调 run_game() 拉起 SC2（独立进程）
  ↓ 启动阶段实时上行 game_status: launching → in_game → playing
[正常对话]
  ↓
心跳 5s 无响应 → 自动重连，指数退避 1-8s
```

MVP：一 token 同时仅一活跃连接（重连顶旧）。
未来：多连接 = 主控 + 观战。

### 9.3 完整 WS Schema

#### 上行（手机 → Bot）

```jsonc
// 0. start_game — 玩家在 UI 点「开始对局」，触发 bot service 拉起 SC2
//    MVP：config 可省略，缺省读 config/ YAML；UI 配置界面是 v0.5
{ "type": "start_game",
  "config": { "map": "...", "opponent_race": "Random", "opponent_difficulty": "Easy" } }

// 1. command (rate-limited 10s)
{ "type": "command", "client_id": "c_91f", "issued_at": 345.0, "text": "切到双矿凤凰" }

// 2. recipe (rate-limited 10s, 未来)
{ "type": "recipe", "recipe_id": "r_72b", "issued_at": 345.0 }

// 3. compile_strategy (rate-limited, 未来)
{ "type": "compile_strategy", "text": "..." }

// 4-6. view_move / view_follow / view_zoom (不限频)
{ "type": "view_move", "target_point": [88, 134] }
{ "type": "view_follow", "unit_tag": 12345 }
{ "type": "view_zoom", "level": 0.7 }

// 7. confirm_ambiguous
{ "type": "confirm_ambiguous", "echo_id": "e_a3d", "confirmed": true }

// 8. revoke (1.5s 内)
{ "type": "revoke", "echo_id": "e_a3d" }

// 9. save_recipe
{ "type": "save_recipe", "echo_id": "e_a3d", "name": "..." }

// 10. release_unit
{ "type": "release_unit", "directive_ids": ["d_72f"] }
```

#### 下行（Bot → 手机）

```jsonc
// 1. snapshot
{ "type": "snapshot", "ts": 330.5,
  "game": { ... },
  "strategy": {
    "kind": "opening_build", "id": "...", "display": "...",
    "phases": [{"id", "display", "subtitle"}, ...],
    "current_phase_id": "...",
    "next_step": "...",
    "abort_conditions": [...],
    "composition_progress": { ... }      // 仅 lategame_doctrine
  },
  "overlays": [...], "standing_orders": [...],
  "recent_decisions": [...], "recent_commands": [...],
  "saved_recipes": [...] }

// 2. event
{ "type": "event", "kind": "strategy.phase_change", "ts": 345.1, "payload": {...} }

// 3. minimap (5Hz)
{ "type": "minimap", "ts": 332.4,
  "viewport": {...}, "units_own": [...], "units_enemy_visible": [...] }

// 4. echo
{ "type": "echo", "echo_id": "e_a3d", "source_text": "...", "interpretation_zh": "...",
  "directives_summary": [...], "confidence": 0.92,
  "recipe_savable": true, "revokable_until_ts": 347.0 }

// 5. parse_error
{ "type": "parse_error", "echo_id": "...", "kind": "timeout|invalid_json|...",
  "message": "...", "candidates": [...] }

// 6. parse_ambiguous
{ "type": "parse_ambiguous", "echo_id": "...", "interpretations": [...] }

// 7. ping
{ "type": "ping", "ts": 350.0 }

// 8. game_status — SC2 启动阶段 + 三段式系统状态链（见 §9.6）
{ "type": "game_status", "ts": 12.0,
  "link": "connected",
  "sc2": "idle|launching|in_game|playing|ended|crashed",
  "bot": "idle|running|error",
  "detail": "..." }   // sc2/bot 异常时 detail 给具体原因（路径错 / 地图缺 / ...）
```

### 9.4 事件 taxonomy

| 类别 | kind | 触发 | 优先级 |
|---|---|---|---|
| 战略层 | strategy.set / phase_change / aborted / transitioned | 剧本切换 | 高 / 中 / 高 / 中 |
| 建造 | build.started/completed/cancelled, research.completed, expansion.completed | 各类完成 | 低 / 中 / 中 / 中 / 中 |
| 战斗 | combat.engaged / resolved, alert.attacked / base_harassed / unit_lost | 战斗事件 | 高 / 中 / 高 / 高 / 中 |
| 敌情 | enemy.spotted / tech_revealed | 侦察发现 | 中 / 高 |
| Directive | directive.committed / released / failed | 生命周期 | 低 / 低 / 中 |
| Bot 决策 | decision.delaying_attack / changed_target | rationale | 中 |

防洪规则：同 kind 1s 内合并；alert 不合并；玩家指令产生的事件标 `caused_by`。

### 9.5 UI 终版布局

```
┌─────────────────────────────────────────┐
│ ⏱5:30  💰800/250  👥28/30  ⚔0/0  🟢   │ ← 60dp 状态条 (固定)
├─────────────────────────────────────────┤
│ ┌─────────────────────────────────────┐ │
│ │   🗺️  Mini-Map (drag viewport)       │ │ ← 280dp 小地图 (固定)
│ └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│ ▼ 当前剧本 (phase stepper)               │
│   [✓ 开局][✓ 上折跃][● 集结][○ 出发]    │ ← 中部 scrollable
│   下一步: 14 追猎、6:00 出发              │
│ ─────────────────                       │
│ ▼ Standing Orders (2)        [全撤销]   │
│   叉子 #12345 → hold (∞)                │
│   哨兵 #78912 → hold + 自动 FF (∞)      │
│ ─────────────────                       │
│ ▼ Bot 决策                                │
│   • 6:00 攻击窗口已开                     │
│ ─────────────────                       │
│ ▼ 最近指令                                │
│   ✓ 5:10 4BG 压制       [↩][💾]         │
├─────────────────────────────────────────┤
│ [快捷区: 空] ← 预留                       │
├─────────────────────────────────────────┤
│ [📝 长按下方录音 / 文本框]                 │ ← 120dp 输入区 (固定)
│                              [🚀 6 秒]   │
└─────────────────────────────────────────┘
```

发送键三态：
- **冷却中**：圆形进度环 + 中心读秒，灰色禁用
- **就绪**：图标常亮蓝色
- **空闲**：图标灰色 placeholder

状态条最右的 `🟢` 是**三段式系统状态链**的折叠指示（手机 → 服务端 → SC2 →
Bot，见 §9.6）：全绿时折叠成一个点；任一段异常变红 + 自动展开成一行
`手机●━服务端●━SC2●━Bot●`，点哪一段红的看该层错误详情（连接层 / SC2 启动层 /
运行时层）。开局前（SC2 未启动）SC2 段显示 idle 灰点，属正常。

### 9.6 状态管理 / 重连

```js
state = {
  // 三段式系统状态链：手机 → 服务端 → SC2 → Bot，任一段异常 UI 一眼可见
  status: {
    link: "connecting" | "connected" | "reconnecting" | "disconnected", // 手机↔服务端 WS
    sc2:  "idle" | "launching" | "in_game" | "playing" | "ended" | "crashed", // 服务端↔SC2
    bot:  "idle" | "running" | "error"                                  // bot 运行态
  },
  game, strategy, overlays, standing_orders,
  recent_decisions, recent_commands, minimap_units,
  saved_recipes, cooldown_remaining_s, pending_echo
}

on snapshot:     state = parse(snapshot)
on event:        apply patch by kind
on game_status:  state.status = parse(game_status)  // SC2 启动 / 崩溃 / 阶段切换
on minimap:      update minimap state
on echo:         state.pending_echo = echo + start 1.5s timer
on disconnect:   state.status.link = reconnecting, show overlay
on reconnect:    re-send token, wait for new snapshot
```

重连策略：指数退避 1→2→4→8→8s。保留最后 snapshot 渲染不闪烁。

**错误分三层定位**（对应 §11.1 错误处理全景，UI 按层给不同文案）：
- **连接层**（`link` 异常）：WiFi 抖动 / IP 错 / 端口占用 / 防火墙 → 提示「检查
  手机和 PC 是否同一 WiFi」
- **SC2 启动层**（`sc2 = crashed` 且未进对局）：路径 / 地图 / 版本不匹配 → 显示
  `detail` 里的具体原因 + 修复指引
- **运行时层**（`bot = error`）：LLM 解析失败 / directive 下发失败 → bot 状态
  不变（§2.3 原则 3），提示「指令没生效，重说一次」

---

## 10. 玩家指令手册

详细话语示例 / FAQ / 老玩家技巧请见 [USER_GUIDE.md](../../USER_GUIDE.md)。

设计文档这里只保留 directive 类型枚举（见 §5.2）和 USER_GUIDE 的章节索引。

---

## 11. 错误处理 / 公平性 / 测试 / 日志

### 11.1 错误处理全景

| 层 | 失效模式 | 系统反应 | 玩家感知 |
|---|---|---|---|
| 手机 → Bot 网络 | WiFi 抖动 | 自动重连，指数退避 | "连接中..." 半透明遮罩 |
| LLM 调用 | 超时 / JSON 非法 | `parse_error` 上行，bot 不变 | toast + recent_commands 标红 |
| LLM 输出歧义 | confidence < 0.6 | `parse_ambiguous` + 候选 | 模态二次确认 |
| Directive 执行 | 单位已死 / 位置非法 | `directive.failed` | echo 标灰 + 原因 |
| Bot → SC2 | python-sc2 连接断 | 30s 内重连尝试 | "bot 离线" 红条 |
| SC2 启动 | 路径错 / 地图缺 / 版本不匹配 | 结构化错误上行，不进对局 | UI 显示具体缺什么 + 修复指引 |
| SC2 客户端 | 闪退 | 检测 + 重启 | 该场中止 |
| Bot 服务进程崩 | Python 异常 | 外部 supervisor 重启 | 手机断连后重连 |
| 彻底无响应 | 死锁 / 宕机 | watchdog 10s 判定 | "服务无响应" + 指引 |

**核心承诺**：任何上层失效 → ②③ 不进入不一致状态。

### 11.2 公平性

| 机制 | 作用 | 实现 |
|---|---|---|
| 1.5s 固定生效延迟 | 抵消 LLM 解析快慢差 | `effective_at = issued_at + 1.5` |
| 10s 限频 | 抵消手速差 | 客户端 + 服务端双重 enforce |
| APM cap 120 | 抵消 bot 能力差 | `bot_difficulty.yaml` 对齐 |
| 同 LLM provider | 抵消模型差 | PvP 强制 |
| 回放完整 | 事后争议 | `.SC2Replay` + `commands.jsonl` |

### 11.3 测试策略（5 层金字塔）

| 层 | 测试 | 频率 | 通过标准 |
|---|---|---|---|
| L1 单元 | 别名 / DSL / schema / cooldown | 每次提交 | 覆盖 > 80%, 0 失败 |
| L2 集成 | LLM 解析 / Directive Board 仲裁 / ares Hook | 每 PR | 0 失败 |
| L3 vs AI 回归 | 5 剧本 × 5 地图 × 50 场 vs Hard AI | 每周 | 胜率 ≥ 50% ± 10% |
| L4 端到端脚本 | 5-10 完整对局 replay | nightly | 0 崩溃 |
| L5 真实手测 | 团队 + 内测玩家 vs 内置 AI | 每周 N+ 场 | 反馈循环 < 1 周 |

### 11.4 日志体系

#### 目录结构

```
logs/2026-05-14/game_20260514_153022_a7c9/
  summary.json            # 战果概览
  commands.jsonl          # 玩家话语 + LLM 解析
  directives.jsonl        # Directive 生命周期
  decisions.jsonl         # Bot rationale
  events.jsonl            # 战斗 / 建造 / 警报
  sc2_actions.jsonl       # python-sc2 API 调用
  metrics.jsonl           # 性能 (LLM latency, tick time)
  errors.log              # 异常 + 警告
  ws_traffic.jsonl        # WS 帧 (debug, 可关)
  llm_calls/
    call_001.json         # 完整 prompt + 响应 + tokens + latency
    ...
  game.SC2Replay          # SC2 原生录像（硬链接）
```

#### 关键日志流

**commands.jsonl** —— 玩家话语 + LLM 解析（debug 最重要的入口）：
```jsonc
{ "ts": 345.0, "client_id": "c_91f", "source_text": "...", "echo_id": "...",
  "llm_call_id": "call_017", "parse_result": {...},
  "ws_round_trip_ms": 1840, "effective_at": 346.5 }
```

**llm_calls/call_xxx.json** —— 单次 LLM 调用全量保留：
```jsonc
{ "ts": 345.1, "provider": "claude", "model": "...",
  "system_prompt_tokens": 3982, "context_tokens": 712,
  "system_prompt": "...full text...", "dynamic_context": "...full text...",
  "raw_response": "...", "parsed_json": {...},
  "latency_ms": 1640, "cache_hit": true, "cost_usd": 0.0048 }
```

**sc2_actions.jsonl** —— 每条 SC2 API 调用 + `issued_by` 溯源：
```jsonc
{ "ts": 360.05, "tick": 8043, "unit_tag": 12345, "action": "attack",
  "target_tag": 67890, "issued_by": "LLMControlBehavior:unit_claim:d_72f" }
```

`issued_by` 是关键 —— 任何 SC2 动作能溯源到"LLM 让做的"还是"基础 bot 自己决定的"。

#### 实现栈

- Python: `structlog`（async 友好 + JSON）
- 写盘: 异步 queue + 后台 flush 协程，每 100 条或 500ms 落盘
- Rotation: 单文件 > 50MB 自动 rotate
- 清理: 默认保留 30 天，胜场可 pin

#### 配套工具

| 工具 | 作用 | 优先级 |
|---|---|---|
| `vc-replay <game_id>` | 重建对局时间线 | MVP |
| `vc-llm-inspect <game_id> <echo_id>` | 查看某条话语的完整 LLM 上下文 | MVP |
| Web Inspector `/admin` | 实时浏览 logs | v1.1 |
| 批量统计 | 胜率 by 剧本 / LLM 正确率 by 类别 | v1.1 |

**调试黄金法则**：任何线上异常必须能用 jsonl + llm_calls/ 离线复现。

---

## 12. MVP 里程碑

### 12.1 时间线（12-14 周）

```
M0 ─→ M1 ─→ M2 ─→ M3 ─→ M4 ─→ M5
 1w   3w   3w   2w   2w   3w
```

| Milestone | 周次 | 交付 | demo 看点 |
|---|---|---|---|
| **M0 Smoke Test** | W1 | • ares-sc2 + python-sc2 跑通<br>• Unit Role 排除机制验证<br>• 资源占用 baseline | 不动的叉子证明 base bot 不会动它 |
| **M1 端到端骨架** | W2-4 | • Bot 服务 + WS endpoint<br>• 手机 PWA 框架 + 状态条 + minimap 占位<br>• 1 个剧本（1门Robo）能 set_build<br>• LLM 调通 + 解析 1 条话语 | 手机说话，PC bot 按 1门Robo build 起来 |
| **M2 Directive Board 完整** | W5-7 | • 3 剧本 (1门Robo, IAC, Skytoss)<br>• Production / Tech / Engagement override<br>• Unit Claim + LLMControlBehavior (持久 + reactions)<br>• 别名表 + 条件 DSL | 切剧本 + 临时叠加 + standing order 全部 work |
| **M3 手机驾驶舱完整** | W8-9 | • Mini-map 可拖<br>• Phase stepper<br>• Standing orders 区<br>• 撤销 / 保存为快捷 | 整局过程在手机上完整呈现 |
| **M4 LLM 解析达标** | W10-11 | • 50 条话语测试集，正确率 > 90%<br>• Prompt + few-shot 调优<br>• 边界情况处理 | 复合句正确分解为多 directive |
| **M5 vs Hard AI 调优** | W12-14 | • 玩家不参与时 vs Hard ~50%<br>• 玩家参与时 vs Hard ~70%<br>• L3 回归测试体系 | 完整 demo 视频 |

### 12.2 已知风险 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| ares Manager 不默认 respect role exclusion | 中 | 中 | M0 验证；最坏 Hook B Mediator wrap |
| LLM 解析准确率不达 90% | 中 | 高 | 测试集驱动 prompt 迭代；换 model；fine-tune |
| SC2 暴雪 patch 破坏 python-sc2 | 低 | 中 | 锁 commit；patch 时 1-2 天修复 |
| 玩家话语口语化超出 prompt 覆盖 | 中 | 中 | 测试期收集真实玩家话语扩展 few-shot |
| Bot 默认 auto-pilot 不达 Hard ~50% | 中 | 中 | 调 `bot_difficulty.yaml`；借鉴 Eris bot 等强 bot 逻辑 |
| PvP 公平性争议（未来）| 低 | 中 | commands.jsonl 回放；强制同配 |
| 手机机型兼容性 | 低 | 中 | PWA 兼容性强；MVP 文档建议机型 |

---

## 13. 项目演进 Roadmap

| 版本 | 内容 |
|---|---|
| **MVP (v0.1)** | 神族 3 剧本，vs 内置 AI，单机 |
| **v0.5** | 神族 8+ 剧本，更多 micro 类型，Web Inspector |
| **v1.0** | 神族完整 (15+ 剧本)，两台笔电 PvP，本地 LLM fallback |
| **v1.5** | 加入虫族 / 人族 |
| **v2.0** | `compile_strategy` 玩家口述生成新剧本，云端服务化 |

---

## 14. 附录：核心架构决策摘要

| 决策 | 选择 | 理由 |
|---|---|---|
| SC2 接入方式 | python-sc2 (BurnySc2) | 社区活跃，事实标准 |
| 基础 bot 框架 | ares-sc2 | Manager + Mediator + Build Runner + Unit Role 全配齐 |
| 部署形态 | 单 SC2 客户端 + 玩家 player slot | 资源最省，相机与 bot 决策解耦 |
| 键鼠 | 不屏蔽，但物理隔离 | 信任玩家不碰；冲突自愈 |
| 输入设备 | 手机 PWA | 中文输入法 + 撤销 / 编辑能力 |
| 视野控制 | 手机小地图拖拽 → bot move_camera | 全程脱离 PC 外设 |
| LLM 部署 | 纯云端 (Claude/GPT/DeepSeek) | MVP 解析质量优先 |
| 剧本表达 | 多态 YAML (opening/midgame/lategame) | 三阶段心智模型对齐玩家 |
| Directive 时机 | supply 触发 build，game_time 触发 timing | 玩家肌肉记忆 |
| 别名 | 中央 YAML 表 + verb 消歧 | 玩家黑话直用 |
| 持久任务 | unit_claim + reactions + role_hint | 玩家"作战参谋部"体验 |
| 公平性 | 1.5s 固定延迟 + 10s 限频 | LLM 解析快慢不公 |
| Bot 能力标定 | apm_cap 120 + 默认关高难技能 | 玩家始终能影响胜负 |
| Logging | 结构化 JSONL + 全量 LLM 调用保留 | 离线 debug 一切 |

---

*文档结案。所有后续开发参照本文档；任何修改请同步更新。*
