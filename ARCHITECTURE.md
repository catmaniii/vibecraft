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
│   ├── prompt.py        # ParseContext + 拼装入口(rules/few_shot 从 docs/llm_prompt/ 读取)
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
        ├── common.py       # build_role_map() + run_command_with_echo()
        ├── common_bot.py   # VibeCraftBotBase（三族共用基类）+ _make_xxx 工厂函数（M6.0）
        ├── protoss/
        │   ├── bot.py      # make_protoss_bot_class() + _VibeCraftProtossBot（薄壳）
        │   └── plans/      # 8 个 protoss 剧本 plan class（KnowledgeBot 子类）
        ├── zerg/           # M6.2b 新增
        │   ├── bot.py      # make_zerg_bot_class() + _VibeCraftZergBot（薄壳）
        │   └── plans/      # 5 个 zerg 剧本 plan class（sustain/scout_overlord/5剧本）
        └── terran/         # M6.3b 新增
            ├── bot.py      # make_terran_bot_class() + _VibeCraftTerranBot（薄壳）
            └── plans/      # 5 个 terran 剧本 plan class（sustain/scout_scv/5剧本）

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
- **`VibeCraftBotBase` 是三族 bot 的共同基类**（M6.0）：race-agnostic 生命周期
  hook（`_publish_xxx`）、EventBus、`_llm_controlled_tags`、`named_spots`、
  `_voice_step_count`、hang watchdog 全在 `common_bot.py`。新增种族走
  `make_<race>_bot_class(...)` 工厂模板，只需覆盖 `EXCLUDE_FROM_ARMY`、
  `DEFAULT_OPENING_ID`、`create_plan()`。三族 `make_*_bot_class` 共享签名，
  `sharpy_adapter.py` 按 `race` 参数 dispatch。见 ADR 0010。
- **`_VibeCraft{Protoss,Zerg,Terran}Bot` 继承 `KnowledgeBot`，`create_plan()` 返回
  `BuildOrder(IfElse(...))` 树**：`active_recipe` flag 控制路由，`set_build()` 写入
  后下一个 step IfElse 立即生效（lambda 每 step 重新求值）。见 ADR 0009 §Hook A。
- **IntentParser 任何异常都不抛**：失败一律返回 `ParseError`，bot 状态完全不
  动。`anthropic` SDK 异常、`ValidationError`、超时、限频都走这条路。
- **logging 是 first-class**：每条 LLM 调用 / 每个 Board 事件 / 每次 Facade
  写都进 `logs/<game_id>/events.jsonl`。新增 directive 路径时不要忘了 emit
  `Event`。
- **strategies 走 `StrategyLibrary.get(id)`**：不要在业务代码里 `yaml.load()`
  剧本路径。换 store backend（DB / 远程）就是换 library 实现。
- **`Sc2Facade` 是 `typing.Protocol`（运行时不强制实现）**：新增/改一个方法**必须同步两个
  实现** —— `FakeFacade`（单测用 mock）+ `_SharpyFacadeBase`（`common_bot.py`，真实游戏跑
  的）。漏后者 → Director 里 `hasattr(facade, "<m>")` 真机恒 False（或裸调 AttributeError）→
  路径静默失效，而单测用 mock 有此方法 → 测不出。`tests/unit/test_facade_release_unit_role.py`
  有 Protocol 一致性 audit。（踩坑：`release_unit_role` 漏实现 → 取消指令/解散编队/释放单位
  全部不放手、单位永久 Reserved。）
- **释放单位必须从 `_llm_controlled_tags` 移除**：`_refresh_llm_controlled_roles()` 每 step 把
  该集合里的 tag 重设 Reserved。光 `set_unit_role` 成别的 role 没用（下一帧又被 re-Reserve），
  必须 `release_unit_role`（无条件 discard）或 `set_unit_role(非 LLM_CONTROLLED)`（内部 discard）。
- **给 idle 单位/建筑下 ability 必须走 bypass，不能用 `bot.do(unit(ability))`**（2026-06-19 踩坑，
  salvage 地堡）：python-sc2 `prevent_double_actions` 在 `unit.orders==[]`（如刚建好闲置的地堡）时
  **fall through 到隐式 `return None`**，被默认 `prevent_double=True` 的 `filter()` 丢掉 → 命令永远
  发不到 SC2、但**不报错**（`salvaged=1` 之类 trace 照样打）。`cast_unit_ability` 因此把 `UnitCommand`
  收进 `_vibecraft_bypass_actions`，在 `super().on_step()` 之后用 `_do_actions(prevent_double=False)`
  直发。**任何对可能 idle 的单位/建筑施法的新路径都要走这条 bypass**。
- **ability enum 必须 `get_available_abilities` 真机核对，别望文生义**（同上踩坑）：地堡回收的真实
  ability 是通用 `SALVAGEEFFECT_SALVAGE`，不是 `SALVAGEBUNKER_SALVAGE`（后者真机返回
  `ActionResult.NotSupported`）。单测/LLM/设计都可能用错名字而绿，只有真局查 available abilities
  + 看 `ActionResult` 才暴露。`_do_actions` 的返回值含每条 action 的 `ActionResult`，bypass drain 会
  记日志，排查时看它。
- **`salvage` directive + selector `near_camera`**（2026-06-19）：`near_camera` 是 selector 通用字段，
  Director 在 submit 时（`_inject_camera_selectors`，紧随 `_inject_camera_point`）按镜头视口框
  （中心 ±12×±9，对齐 SC2 24×18 FOV）**一次性固化成具体 tags**、清 near_camera —— 之后走普通 tags
  分支，不每帧重过滤（镜头会动、单位会走）。`resolve_selector` 已扩 `self.bot.structures`（建筑在
  structures 不在 units，否则 `unit_type=Bunker` 恒返回 []）。
- **人族建筑起飞/移动 `STRUCTURE_MOVE` + 农民基地调度 `WORKER_TASK`（2026-07-08）**：
  两个持续型 directive，commit 时只注册进各自状态字典，真正执行在 tick 循环：
  - `STRUCTURE_MOVE`（"主基地飞起来" / "主基地飞到二矿" / 已在飞时"降落在这里" / "飞到三矿"）：
    `Director._structure_move_orders` + 每 sharpy step `await Director._tick_structure_move`
    （**async**，因为要 `await bot.can_place_single` 找降落位；挂在 `execute_overrides_step`，
    独立于 `production_overrides` 是否非空）。FIND 阶段 `_find_nearest_townhall` 同时找**落地和
    已在飞**的 CommandCenter/OrbitalCommand（∪ 对应 `*FLYING`；PlanetaryFortress 无飞行变体不纳
    入），按其**真实 type_id** 解析 `LIFT_<TYPE>`/`LAND_<TYPE>`（不硬绑 CommandCenter）；已在飞
    +给了新 to_spot 直接跳过 LIFT 进 landing。PlanetaryFortress 起飞 → 友好拒绝（真机
    `LIFT_PLANETARYFORTRESS` 不存在）。降落点 `_find_structure_land_spot` **必须先
    `closest_expansion_location` 贴矿最优 townhall 采矿位**，被占才退化由近及远网格扫（复用
    #543 `_build_addon_on_parent` 同款 tag 缓存追踪 + 落点一次锁定套路）。`to_spot="camera"`
    （"降落在这里"）由 `_inject_camera_point` 注入真实坐标（字段类型 `str | tuple | None`，同
    `TacticalObjectivePayload.target_area` 模式）。
  - `WORKER_TASK`：`prioritize_minerals`/`prioritize_gas` **复用全局** `facade.set_mining_priority`
    （宏观面板 mining 维度同一开关，**不做 per-base 隔离**）——vendor `DistributeWorkers.execute`
    每帧动态算 `max_gas = max(0, 总农民 - 矿理想采集数)`，这是"矿先填满、剩余农民才去采气"的**动态
    软优先级**，不是硬零气（总农民持续超编时 gas_workers 仍会随 surplus 增长，真局自验已确认）。
    `transfer_to_base`：`Director._select_mining_workers_near` 选中 from_base 附近 Gathering role
    非采气农民 → `set_unit_role(LLM_CONTROLLED)` Reserve 住 + 持续 `order_worker_gather` 钉去
    to_base（`_WORKER_TRANSFER_SETTLE_S=8` 游戏秒，对抗 sharpy `DistributeWorkers` 同帧拉回）→
    settle 后 `release_unit_role` 归还 bot 采矿池。挂在 `on_tick`（同步，不需要 await）。
- **大件（BC）组队骚扰（`group_harass` recruit-claim + `GroupHarassAct`，2026-06-29 #580，取代旧 #561 per-BC 工厂）**：
  新任务 verb `Verb.GROUP_HARASS`（`directives/task.py`）。BC 骚扰 = **一条** `unit_claim(recruit_new=True,
  unit_type=Battlecruiser, task=group_harass(target), target_count)`——复用 #521 持续征兵（新 BC 每帧自动并入
  `_standing_order_tags[did]`）+ 新增 `target_count` 通用 recruit 上限（cap / partial-release）。bc_rush 开局
  director 自动提交（once-flag 防复活）。
  - **关键不变量 `group_harass` ∈ `skip_action`**：director（`_assign_standing_order_units` + `_tick_recruit_watchers`）
    对 group_harass / harass_workers 的 tag **只 `set_unit_role` + 维护 tag 集，绝不下单体 action**，`GroupHarassAct`
    是唯一控制者（否则 director 的 `unit.move(zone)` 和 act 的 posture 调度对冲，#580 opus 评审 A1）。
  - **数据流**：director 每 tick `_publish_bc_harass_groups()` → `knowledge.vibecraft.bc_harass_groups`
    = `[{did, tags:set, target:"enemy_*"|None, target_count}]`。`GroupHarassAct` 读它，每个 group 跑健康分状态机
    （STAGING↔HARASS posture，够数一起出击 / 残血 move 回家修 / 满血再出）+ 优先级行为树（P0 生存 → P1 威胁规避＞杀农民：
    cheap-kill 孤立孢子 / 精确 air_range 拉射程外 / picker 换没防空的矿 → P2 `plan_harass_approach` 接近 → P3 矿后骚扰），
    全程不停 move。
  - **P2 接近 `plan_harass_approach`（#581，2026-07-03，替换绕整圈的旧 `plan_edge_path`）**：直奔矿后点，
    只在「start→矿后点」直线会穿敌方主基地（垂距 < `_HARASS_AVOID_R`）时才沿垂直方向推拐点绕开（drop-path 式
    避障，退化时选贴地图边侧）；且走「场外集结点 stage → 矿后点」保证**从矿背后/外侧切入**、不从基地头顶压过。
    near/far 交接由 `_approach_wp` 的 `_approach_arrived` 闩锁（走到矿后点才置）驱动，**不**用「距矿区中心<24」
    （否则 stage/behind 都在 24 环内、接近段永不执行——#581 opus 评审揪出的结构缺陷）。到位后无威胁 → `attack`
    农民质心 move-attack，有地面威胁 → `_p1_threat_flee` 卡射程外。矿后锚点 `_BEHIND_MINERAL_OFFSET=0.5`（贴矿线）。
  - **副产品 ECA**：`unit_claim(recruit_new) + target_count + 任务 verb` = 通用「新出 X → 编组 → 执行某协同任务」，
    BC 骚扰是其首个实例。
  - **被 claim 单位的微操不读 `combat_intent_override`**（它常被 bot 默认置 "defend"，据此喊停会误停 claim 单位；
    控制权规则 2：claim 独占，停它只能 ❌ 群卡 / 降 target_count）。
- **新增任何玩家可见字符串必须进 `locales/strings.json`（zh+en），不硬编码**：一切面向玩家的 UI
  文案、服务端反馈消息（Toast、解析失败提示、澄清弹窗等）统一从 `locales/strings.json` 读
  （前端 `t(key)` / 后端 `vibecraft.i18n.t(key, lang)`）。**严禁在 `.vue` / `.ts` / `.py` 里
  硬编码显示字符串**——违反此条 = 该串无法切换语言。专有名词（单位名、建筑名）走
  `bot/localization.py` 的 `Localizer`，不进 strings.json。

---

## 代理建造链 + 出兵集结点（玩家"位置/坐标"类指令）

玩家用镜头（"这里"）下达的位置类指令走两套机制，都踩过反复出现的坑，集中记此处。

### 代理建造链（"派农民去 X 修水晶，再修两个 VS"）

- **组合现有 directive，不新增类型**（CLAUDE.md 约定）：
  `unit_claim`(probe, `selector.chain_id`, persistent standby) 持有农民 +
  N 张 `build_at`(`by_probe=true`, `activate_when=chain_structure_ready(同 chain_id)`)。
  靠 `activate_when` 串联，后续卡不需显式知道农民 tag。
- **数据结构**：`_task_chains[cid]={probe_tags}`（claim 时填）、`_chain_structures[cid]={struct_tags}`
  （settle 时填）、`_pending_proxy_build[did]`（每帧重发 build 的活跃卡）。
- **执行循环 `_tick_proxy_build`**：农民空闲（非 PROTOSSBUILD 订单）就每帧 `order_probe_build`
  重发（压过 sharpy auto-mining 抢人）；目标点/农民附近出现该类型建筑 = settle。
- **settle 检测的两道防护（都踩过坑）**：
  1. 排除 `_proxy_claimed_structs`（别的卡已认领的）+ `info["preexisting"]`（本卡发起时**快照**
     的同类旧建筑 tag）→ 防"农民途经家里旧 VS 被当成刚造好、卡片秒完成"。
  2. settle 收尾 release 查 `_in_flight.get(did) or _committed_directives.get(did)` —— 激活的
     build_at 卡在 `_committed_directives`（非 `_in_flight`），漏查则卡片永不消失。
- **水晶建好刷新后续落点 `_assign_chain_followup_spots`**（玩家定的设计：先给模糊点，水晶
  settle 后再算准）：把本链还在等的 N 张后续卡的 `payload.point` 刷成水晶能量场内**互相分散**
  的点。**落点必须分散不重合** —— 否则两个 VS 撞一起、`find_placement` 再把它们往水晶拽 →
  第 2 个建不出（反复踩）。选点用**最远点采样**（第一个最空旷、之后选离已选最远的可走点），
  而非"贪心挑最空旷的 N 个"（后者把多个建筑聚到同一侧 → 重合）。
- **`find_placement` 的硬限制**：只判"能不能放下 3x3"，**不判可达性**。
  `in_pathing_grid`（单格可走）≠ `can_place`（能建）≠ 农民走得到。预设一个可建但不可达的点 →
  农民死磕、建不出。验证落点改动**必须跑真局自验** `scripts/proxy_chain_selftest.py`
  （验 `vs_distinct==2`），单测（mock 无 `find_placement`）盖不住这类引擎行为。

### 出兵集结点（RALLY_POINT，"集结点设这里 / 出兵都去这"）

- **底层**：sharpy `GatherPointSolver`（`IGatherPointSolver`）维护全局 `gather_point`，
  `PlanZoneGather` 每帧把空闲兵 move 过去（默认家门口、随扩张前移）。玩家指令只是**设这个点**。
- **`RALLY_POINT` directive**（管"未来新出的兵去哪"，不占控制权，区别于 `unit_claim` 集中=拿现有兵）：
  Director `_rally_point` + `_rally_point_id`，`on_tick` **每帧** `facade.set_rally_point` 覆盖
  sharpy `gather_point`。**必须每帧重设** —— sharpy `set_gather_point` 是一次性 flag，不每帧
  会被 `_find_gather_point` 重算回默认（forward_rally 同款坑）。× 撤销 → 清，恢复 bot 默认。
- **玩家 > bot**：`facade.set_rally_point` 同时写 `knowledge.vibecraft.player_rally_point`，
  剧本内的集结逻辑（如 `VoidRayStageRallyAct`）检测到玩家设了就让位，避免两边每帧互抢。

### 攻防升级目标等级封顶（"科技面板每条攻防线设 0/N/自动"，2026-07-07）

- **数据双写**：玩家在科技面板设某条升级线目标 → `macroAction('upgrade_target',{family,level})`
  → Director `apply_macro_action` 同时写 **两个 store**：`self._upgrade_targets`（给面板 view
  `_build_tech_progress` 输出 `target` 字段）+ `facade.set_upgrade_target` → `knowledge.vibecraft.upgrade_targets`
  （给封顶门读）。`level='auto'` = 从两处 pop（None=自动=bot 默认）。**两 store 必须同步**（同 rally_point 的
  facade+knowledge 双写模式）。
- **封顶门（唯一收口）**：sharpy `Tech.execute` 顶部 `# vibecraft:` patch（`vendor/.../acts/tech.py`）。
  所有攻防升级都经 `Tech(UpgradeId.<FAMILY>LEVEL<N>)`——门用 `^(.*)LEVEL([123])$` 抽 family+level，
  `knowledge.vibecraft.upgrade_targets.get(family)` 是手动值 T 且 `level>T` → `return True`（跳过研究、
  不卡后续 step、不预留矿气）；T=None 或非攻防升级 → 不拦。15 族白名单从 `UpgradeId` enum 派生。
- **不变量**：`_KNOWN_UPGRADE_NAMES` 的 family 名必须与真实 `UpgradeId` enum 一致（曾用错名
  `ZERGFLYERATTACK`→真名 `ZERGFLYERWEAPONS`，连累面板当前等级显示 + 封顶双失效）。

---

## 偷矿系统（Stealth Mining，2026-06 落地）

玩家镜头对准远端隐蔽点说"在这偷矿" → bot 建一片**隐蔽自给的封闭经济基地**：自产农民、
自己采矿采气、满采到 22（16 矿 + 6 气）、跟主矿**双向隔离**、支持多片、受击自动撤销交还 bot。

### 核心：`bot/stealth/`（不碰 sharpy，纯状态机）

- **`StealthCellManager`**（`manager.py`）：每片偷矿 = 一个 `StealthCell`，状态机
  `PENDING`（claim 农民派去建）→ `BUILDING`（等 Nexus settle）→ `MINING`（自产农民/采矿采气/受击检测）→
  `RELEASED`（受击/取消，还 bot）/ `DESTROYED`（Nexus 没了）。`on_tick(bot, facade, now)` 每帧驱动所有 cell。
- **`StealthCell`**（`cell.py`）：`worker_tags`（自产农民，Reserved）、`gas_tags`（assimilator）、
  `gas_worker_tags`（采气子集）、`nexus_tag`、`worker_missing_since`（grace 计时）、`gas_builder_since` 等。
- Director 在 `stealth_mine` directive commit 时 `create_cell`，`on_tick` 调 manager；`pending_release_events`
  回流给 Director 弹通知。

### FENCE：偷矿基地与主矿**双向隔离**（最易踩坑，集中记）

偷矿农民全是 `LLM_CONTROLLED`（=sharpy `Reserved`），sharpy `DistributeWorkers.calculate_workers`
的 `only_roles={Idle,Gathering}` 天然不含 Reserved → 偷矿农民不被全局调度。在此之上：

- **`stealth_townhall_tags`（SNS）= 偷矿 Nexus + assimilator 全部 tag**。manager 每 property 实时算
  （含 `gas_tags`），**每 tick 重注册**到 SNS（assim 是 Nexus 建好后才陆续建的，只在 settle 注册一次会漏）。
  vendor `DistributeWorkers.generate_worker_queue` 读它把偷矿 Nexus + 气矿排除出 work_queue
  → 主矿农民不被派去偷矿采矿/采气（**防倒灌**）。
- **主动 FENCE（tag-aware 驱逐）**：work_queue 里偷矿基地若混进"非 stealth_worker_tags 的漂移农民"
  （主矿农民飘进来）→ 发 `force_exit` 把它们赶回主矿。**按 tag 判**（不只按 role），否则 cache-miss
  未 Reserve 上的自产农民会被误赶。
- **`stealth_worker_tags`（SNS）= 所有 cell 农民并集**，每 tick 注册。ScoutWorker 等"挑农民干别的活"
  排除它（比 `_llm_controlled_tags` 稳，不受 cache-miss 误删那帧 race）。
- **`is_done`（`ActUnit`）账目分离**：主矿满采判定排除 `stealth_townhall_tags`（含气矿），否则偷矿气位
  被算进主矿 ideal → 主矿过量造农民 → 多的又倒灌。
- **在建偷矿算进基地数**：manager 注册 `stealth_pending_base_count`（PENDING/BUILDING 的 cell），
  vendor `Expand.execute` 加进 `active_bases` → 玩家下偷矿令后 bot 延后开自己分矿（MINING 的 Nexus ready
  已被 `our_zones_with_minerals` 计入，pending 只数没 ready 的，不重复）。

### 关键不变量（偷矿）

- **自产农民不外流、主矿农民不倒灌**：`outflow=0` / `to_kind=stealth≈0`（telemetry / ECONTRACE 验）。
- **农民出生那帧 cache-miss**：newborn 不在 `bot.units` cache → `set_unit_role` 失败但仍入 worker_tags；
  靠 `worker_missing_since` **grace-period**（连续消失 `_DEAD_GRACE_S`=4 游戏秒才判死）+ tag-aware FENCE 兜住。
- **采气农民会周期性"钻进"assimilator**（暂时离开 `bot.units`）→ 同 grace 不误判死。
- **采气分配**：`gas_worker_tags` 按总气位 `gas_cap` **封顶**（不按滞后的引擎 assigned 无限塞）；
  按每个 assim 缺口**均分**（两个气矿各 3，不堆一个）；**漂走的每帧重焊**回气（`facade.gas_worker_drifted`
  判：排除钻进/carrying/正在采气的循环中状态）。
- **offline 可观测**：`telemetry.jsonl` 每帧带 `stealth_cells`（state / worker_count / mineral_workers /
  gas_workers / nexus_assigned）；`nexus_assigned > tracked` = DRAIN（倒灌）、`<` = OUTFLOW（外流）。

### vendor sharpy patch（偷矿相关）

`generate_worker_queue`（FENCE）/ `assign_to_work`（ECONTRACE 调度日志）/ `Expand.execute`（在建偷矿算账）/
`ActUnit.is_done`+`builders`（账目分离）。完整清单 + 升级 checklist 见 `docs/sharpy-patches.md`。
**改 `Sc2Facade` 必须同步 `FakeFacade` + `_SharpyFacadeBase` 双实现**（偷矿加了一串 facade 方法：
`register_stealth_townhalls/workers/pending` / `train_probe_at` / `order_worker_gather[_gas]` /
`gas_worker_drifted` / `cast_chrono_on_nexus` 等）。

---

## 多人联网（阶段 0，2026-06-12 落地）

设计 `docs/plans/2026-06-12-multiplayer-design.md`；实施+spike 结论
`docs/plans/2026-06-12-multiplayer-implementation-plan.md`。一台 PC 跑多个 SC2 实例
LAN host/join 成一局，每实例一个 bot，多个玩家各用手机指挥。

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⛔ 引擎硬限制：多 agent 局**仅纯 1v1**（2 个真人 bot 头对头）。              │
│    create_game 拒绝"多 agent + 内置电脑/3+ 人/2v2/组队/FFA"：              │
│      InvalidPlayerSetup: Only 1v1 is supported when using multiple agents   │
│    → Blizzard 引擎边界，非我方 bug，绕不过。双真人不能加电脑。              │
│    （单真人 + 任意电脑 = 1 个 agent，走原单人路径，不受此限）              │
└──────────────────────────────────────────────────────────────────────────┘
手机A(入口页:用户名+服务器→lobby) ─┐                ┌─ GameProcess A(bot+SC2 host)
手机B ──────────────────────────┤─ BotService ──┤─ GameProcess B(bot+SC2 join)
   ws?room=<token>&player=&pid=  │  RoomService   └─（共享散点 Portconfig 组一局）
   （最多 2 真人 = 1v1 上限）     │   ├ Room(状态机 lobby→starting→in_game→回lobby)
                                 │   ├ MatchOrchestrator(per-player monitor task)
                                 │   └ RoomRegistry(per-player 连接+broadcast)
                                 └ WebRtcManager(per-player PC,按 SC2 窗口 PID 抓屏)
```

### 关键不变量（多人新增）

- **A 的指令绝不进 B 的 down_q**：WsConnection 持 player_id，`_gp()` 只查
  orchestrator.process_for(自己)。selftest 有路由隔离断言。
- **每 GameProcess 的 raw_events 恰一个消费者**：orchestrator 的 per-match monitor
  task（connection 无关）；WS 连接只订阅（断线不失管、重连续上）。没有
  per-connection status pump 了。
- **端口必须散点 `Portconfig()`**（`sc2_multiplayer.new_portconfig_json`），绝不可
  `contiguous_ports`——连号端口会被 Windows 顺序端口游标撞上（子进程 SC2 的 ws 端口
  压在游戏 P2P 端口上 → join 被拒 NetworkError）。spike E1-E7 实锤。
- **引擎硬限制：多 agent 局仅纯 1v1**（2026-06-12 spike 实测，Blizzard 引擎边界非我方 bug）：
  create_game 对"多 agent + Computer / 2v2 / FFA / 组队"直接拒绝
  `InvalidPlayerSetup: Only 1v1 is supported when using multiple agents` → 双真人不能加电脑、
  做不了 2v2/3+ 人/组队（Room 双重校验 + lobby UI 置灰）；solo + 任意电脑 = 单 agent，走原单人
  路径不受限。**底层引擎限制，patch 改它概率极低；真要复验跑 `multiplayer_smoke.py` with-computer 模式。**
- python-sc2 `client.join_game` 吞错误（player_id=0 即失败）→ runner
  `_checked_join_and_play` 检查+重试+显式 raise。
- 一方进程死 → 对方引擎判 Victory；monitor 看到任一 ended/crashed → 全停收场回 lobby。

### 自验

`scripts/multiplayer_smoke.py`（host/join 链路 spike，多诊断开关）；
`scripts/multiplayer_selftest.py`（端到端 14 项：lobby 流/双实例成局/路由隔离/收场/
无孤儿，mock LLM + non-realtime）。

---

## i18n / 本地化（中英双语）

### 字符串单一真理源

`locales/strings.json`（`{id: {zh, en, context?}}`，500+ key）是**前后端共读的唯一真理源**，无生成器：

- **前端**：`web/src/i18n.ts`（`t(key, params?)`）直接 import，reactive locale + `{name}` 插值 +
  `localStorage` 持久化 + 浏览器语言默认探测。`LanguageSwitcher.vue` 提供中/EN 两档切换。
- **后端**：`src/vibecraft/i18n/__init__.py`（`t(key, lang, **params)`）在 ws 序列化层按 per-player
  locale 调用，下发给手机的用户可见消息（解析反馈、错误、澄清文字）按玩家语言出。
  **回退规则**：某 locale **显式存在**该键（含有意空串，如计数后缀「个」en 留空）→ 用它、不回退 zh；
  该 locale **缺键** → 回退 zh → key。（旧 `entry.get(lang) or ...` 把空译误当缺译回退中文，2026-06-29 修。）
- **专有名词**（单位/建筑名）走 `bot/localization.py` 的 `Localizer`（en canonical = SC2 官方英文名），
  不进 strings.json。建筑显示用 hotkey（`structure()`）；少数自然语言语境（澄清问句）要全名的，用
  `structFull.<UPPER>` 键（如 重工/Factory）。`Localizer.race()` 给种族名（神族/Protoss）。
- **director 所有玩家可见 display/卡片/条件/状态原因/命令反馈**（`_directive_display_for` /
  `_describe_condition` / production status_reason / 各 ack）一律走 `_i18n_t(key, self._lang)` +
  `self._loc.*`，**无硬编码中文**；由静态门 `test_locale_snapshot_gate.py::test_director_no_hardcoded_chinese_literals`
  把守（AST 扫源码，排除 docstring/注释/logger）。doctrine 的 micro 要点用 `engagement_doctrine_en`（yaml 平行字段）。

### i18n 硬门（`tests/unit/test_locale_snapshot_gate.py`）

「英文模式零中文」靠三道机器门把守，取代人肉发现：
1. **动态 snapshot 门**：en locale 构造 Director（**必须带 `event_bus`**，否则 task_monitor=None 会短路
   条件分支 → 假阳性），跑全 directive 类型 + 全剧本 slot + 条件/前置文本构造器，递归扫 snapshot 断言无 CJK。
2. **静态门**：AST 扫 `director.py`，断言无面向玩家的中文字面量。
3. **key 存在门**：扫源码所有 `_i18n_t("k.k")` 引用，断言 key 在 strings.json 且 en 非 None（堵
   「缺 key → t() 回退 ASCII key → 玩家看到生字符串」，CJK 门照不到这类）。

### locale 全链路数据流

```
PWA i18n.locale → WS URL &locale= → 握手 → WsConnection._locale
  → room.join(locale) → Slot.locale → match.py per-player GameConfig.locale
  → 子进程 env VIBECRAFT_LOCALE → IntentParser.locale
```

英文玩家全链路：界面英文 + 英文文字/语音指令 → LLM 英文 few-shot 解析 + 英文 interpretation +
服务端消息英文 + 单位名用 SC2 官方英文名 + 建筑仍用 hotkey（键位与语言无关）。

测试：`tests/unit/test_locale_penetration.py`（locale 链路端到端，mock bot）。

### 双模型 ASR（按 locale 路由）

| locale | 模型 | 模式 | 备注 |
|---|---|---|---|
| `zh` | `paraformer-zh-streaming` | 流式（real-time partial）| 现状不动 |
| `en` | `iic/SenseVoiceSmall` | 离线（松手后整段解码）| ~1 GB，首次需预拉 |

- `AsrEngine` 惰性加载双模型（per-locale `_ensure_loaded`），`create_session(locale)` 按 locale 路由；
  ws 握手见 `locale=en` 即后台 `warmup_en()`，不阻塞首句。
- en 模型**首次部署需提前预拉**（`scripts/prefetch_asr_en.py`，约 6 分钟）；之后缓存秒载。
- 混合 locale 多人局（zh+en 玩家同局）两模型同时常驻，**额外约 1–1.5 GB 内存**（叠加在 SC2 之上）。
- en session：`feed(chunk)` 只追 buffer，`finalize()` 整段一次 `model.generate` → `_strip_sensevoice`
  剥标签 → 出纯文本喂 LLM；前端语音条显示占位文字，松手即转写。
- 自验：`scripts/asr_en_selftest.py`（提交的 wav fixture，hermetic，不依赖 live 合成音频）。

---

## 6 个 hook 点（设计文档 §3.2）与 sharpy 实现映射（M1+ 已迁移）

| Hook | 设计文档概念 | facade 上的方法 | sharpy 实现 | 状态 |
|---|---|---|---|---|
| A Build Runner | 切换当前 build order | `set_build(build_name)` | `active_recipe` flag + IfElse 路由树（ADR 0009） | ✅ M3 完成 |
| B OverrideMediator | 强制某种单位 / 升级 / 开矿封顶 | `set_production_override` / `set_tech_override` / `set_expansion_override` | `set_expansion_override` → `Expand.execute` 读 `expansion_cap_override`（玩家开矿封顶，+ 偷矿在建算账）✅；production/tech override 仍占位 | 部分 ✅ |
| C Unit Role | 把单位拉出 Manager 视野 | `set_unit_role(tag, role)` | `UnitTask.Reserved` + `_llm_controlled_tags` 每 step refresh（ADR 0009 §Hook C） | ✅ M4 完成 |
| D Rationale Logger | 记录决策原因 | （Director 自用 GameSession）| 同 M1，0 改动 | ✅ |
| E ViewController | 相机操作 | `move_camera` / `follow_unit` / `set_camera_zoom` | python-sc2 `client.move_camera`（ADR 0008，暂存+drain） | ✅ |
| F BuildLocationOverride | 指定建造点 | `set_build_location_override` | noop，留 M5+接 sharpy BuildingSolver | 占位 |

vendor 段：sharpy-sc2 MIT，vendor 路径 `vendor/sharpy/`（不在 PyPI），
`sys.path` 注入（lazy，单测 mock sys.modules 绕开）。见 `vendor/sharpy/ATTRIBUTION.md`。

---

## 测试组织

全套单测（800+），`tests/unit/` 下跑（无需 SC2，全用 `FakeFacade`）。

- `tests/conftest.py`：session-scope `_block_sc2_child_entry`，防止任何测试意外
  spawn 真实 SC2 子进程。所有注入 `WsConnection` / `GameProcess` 的测试必须传 mock。
- `tests/unit/test_smoke.py`：装得上 + 能 import。
- `tests/unit/test_directives.py`：Board 状态机、三槽切换、commit delay、
  overlay 优先级、unit_claim 互斥。
- `tests/unit/test_director.py`：Director ↔ Board ↔ FakeFacade 端到端
  （FakeFacade 全程记录调用做断言）。
- `tests/unit/test_llm_parser.py`：IntentParser 用 stub provider 跑各类
  outcome（success / ambiguous / error / 超时 / schema 失败）。
- `tests/unit/test_{dsl, strategy, logging}.py`：纯模块单测。
- `tests/unit/test_cockpit_sync.py`：WS ↔ GameProcess 状态推送 + status_events
  过滤 + sharpy_adapter bot 生命周期。
- `tests/unit/test_m1_6_end_to_end.py`：make_protoss_bot_class 工厂、
  bot 生命周期、watchdog、SC2 facade 分派的 mock-bot 端到端。
- `tests/integration/`、`tests/e2e/`：default 跳过（pytest mark），需真实 SC2 客户端。
- `scripts/smoke_test.py`：端到端冒烟，单独脚本，详见 `docs/m0-smoke-runbook.md`。

## 部署架构

**不变量：server 永远跟 SC2 在同一台 PC 上**——它要启动/操作 SC2、按 PID 抓 SC2 窗口的画面、
跑 bot。视频源头就是 PC 的屏幕，server 没法搬到没有 SC2 的云上。云只做"把手机连到 PC"。

两块平面：**控制面**（网页 + WS 信令 + 指令，流量极小）和**媒体面**（WebRTC 视频/音频，
PC→手机，P2P 优先、打不通走 TURN 中继）。

### 本地单机（同 wifi / Tailscale）

```
  手机 ──WSS 信令──┐
                   ├──► PC server (8080)：HTTP+WS 同端口 + WebRTC + 多人 Room
  PC(SC2+bot) ─────┘
   └════ WebRTC 媒体 P2P（host 候选：同 wifi 192.168.x / Tailnet 100.94.x）════► 手机
```

- 入口：`start.ps1` 打印的 `http://<LAN-IP>:8080/?room=<token>`，手机同 wifi 直连。
- 外网但不搭公网：两端装 Tailscale，手机走 Tailnet（funnel 仅代理 HTTP/WS，媒体仍 ICE 直连）。

### 公网（国内手机直连，无需 Tailscale）

香港 VPS 做**媒体中继 + 公网前门**；PC 在 NAT 后**主动出站**连 VPS（反向隧道），手机连 VPS
公网地址，VPS 把控制面桥接回 PC、媒体面在 P2P 失败时经 coturn 中继。

```
  手机(国内 4G/5G) ──► https://app.<ip>.sslip.io/?room=<token>
   │
  香港 VPS :443 ── nginx 按 SNI 分流 ──────────────────────────────┐
   │  SNI=app.* / base → nginx 终止 TLS → 反向隧道(127.0.0.1:18080) │ 控制面
   │  SNI=turn.*       → TCP 透传 → coturn:5349                      │ 媒体面(turns:443)
   └─────────────────────────────────────────────────────────────────┘
        ▲ SSH -R 反向隧道(pc-tunnel.ps1)        ▲ relay 媒体(UDP 49160-49260)
   PC server(8080) + SC2 ───────────────────────┘
        └═══ 媒体 P2P 优先(同 wifi/Tailnet) ═══► 手机；打不通 → 落 turns:443 中继
```

- **为什么 443 要 SNI 分流**：媒体中继 `turns:443`（穿中国防火墙，看着像 HTTPS）和控制面
  HTTPS/WSS 都想用 443，一个 IP 一个 443 → nginx `ssl_preread` 按 SNI 主机名分流：
  `turn.<ip>.sslip.io` 透传给 coturn（coturn 自己终止 TLS）、其余给 nginx 的 app server
  （终止 TLS → 反向隧道到 PC）。
- **为什么用反向隧道**：PC 在家用 NAT 后无公网 IP，VPS 无法主动连进 PC；改为 PC 用 SSH `-R`
  主动连 VPS、把本机 8080 映到 VPS `127.0.0.1:18080`，nginx 反代它。零新开端口（走 SSH 22）。
- **TURN 凭证**：coturn `use-auth-secret`（REST 短期凭证）；PC server 与浏览器各用同一 secret
  现签 `username=expiry:name` + `HMAC` 短期凭证配 iceServers（详见 `server/turn_config.py`）。
- **graceful**：未配置 TURN（`.secrets/vibecraft-turn.env` 缺失）→ `_ICE_SERVERS` 空、纯
  P2P，行为与本地单机完全一致（不变量）。
- 部署脚本：`deploy/turn/{setup-coturn,setup-frontdoor}.sh` + `pc-tunnel.ps1`；采购规格
  `docs/ops/vps-purchase-spec.md`；详细方案 `docs/plans/2026-06-14-turn-integration-plan.md`。
