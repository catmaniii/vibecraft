# vibecraft 虫族 / 人族 bot 支持设计

> 创建：2026-05-18
> 状态：M6 提案（M5 神族 MVP 已交付）
> 真理源：`docs/plans/2026-05-14-vibecraft-design.md`、`ARCHITECTURE.md`

---

## §1 目标与不在范围

### 1.1 目标（M6 必达）

1. vibecraft 可以**以虫族 / 人族出战 SC2 内置 AI**，玩家用 PWA 全套指挥链路与神族 MVP 完全一致：
   - 卡片显示当前剧本（虫/人剧本中文显示 + hotkey 缩写）
   - voice / 文字指令解析（剧本切换、L2 attack/defend、L4 standing order）
   - directive board 仲裁、snapshot/event/minimap 推送、unit_claim 接管
2. 每种族至少 **4 个可玩剧本**：1 个 cheese opening + 1 个标准 opening + 1 个 midgame stance + 1 个 lategame doctrine，构成完整 opening → midgame → lategame 转移链。
3. 完整 sustain 兜底（取消剧本后 bot 不抢决策，等待玩家）。
4. 每种族 dedicated alias 表完整可用（不再是占位 skeleton），LLM prompt 按种族动态加载。
5. 全部单测无 SC2 通过（800+ → 870+ 估计）。新增 `tests/unit/test_zerg_bot_*.py` / `tests/unit/test_terran_bot_*.py` 套件。

### 1.2 不在范围（M6 不做）

- **不做随机族**（bot 自己 race=Random，运行时选定）。M7 课题，需要 prompt / aliases 切换机制。
- **不做 1v1 真人对战**：仍是 vs 内置 AI。
- **不做"虫族玩 PvZ vs 人族玩家"这种 matchup 特化优化**：每个剧本只标 matchup，bot 不自动 counter。
- **不重写 sharpy dummies**：能复用尽量复用，必要时 vibecraft 子类覆盖。
- **PWA 不做种族切换 UI**：service 启动通过 CLI/env 选种族。
- **飞房子 / creep tumor 推进**：M7 课题。

---

## §2 现有神族架构盘点

| 层 | 文件 | race-specific 程度 | 备注 |
|---|---|---|---|
| Sc2Facade Protocol | `src/vibecraft/bot/facade.py` | **race-agnostic** | UnitRole / BotState / 接口签名零神族特化 |
| Director | `src/vibecraft/bot/director.py` | **race-agnostic** | Board / overlay / standing order 全通用 |
| EventBus | `src/vibecraft/bot/event_bus.py` | **race-agnostic** | unit/building lifecycle event 三族通用 |
| MinimapBuilder | `src/vibecraft/bot/minimap.py` | **基本通用**，色板可能要按种族微调 | own/enemy 颜色按 alliance，没硬编码神族 |
| NamedSpotRegistry | `src/vibecraft/bot/named_spot.py` | **race-agnostic** | 按地图 zone |
| TaskMonitor | `src/vibecraft/bot/task_monitor.py` | **基本通用** | 监控 build/train/upgrade 完成 |
| sharpy_adapter | `src/vibecraft/bot/sharpy_adapter.py` | **race dispatcher**（已经准备好分支） | 当前 race != Protoss → NotImplementedError |
| auto_combat/common.py | 同名 | **race-agnostic**（build_role_map / run_command_with_echo） | UnitTask 映射三族共享 |
| auto_combat/protoss/bot.py | `_VibeCraftProtossBot` | **80% race-agnostic + 20% race-specific** | 见 §3 拆分 |
| auto_combat/protoss/plans/* | 13 个 plan 模块 | **100% race-specific** | scout_worker（探机）、forward_proxy（修水晶折跃）等都是神族行为 |
| strategies/protoss/*.yaml | 8 个剧本 | **100% race-specific** | aliases / sharpy_dummy_class / steps 全神族 |
| docs/aliases/protoss.yaml | alias 表 | **100% race-specific** | 字段格式三族一致 |

**关键发现**：`_VibeCraftProtossBot` 类身躯大（约 700 行），但其中只有 `create_plan` / `_compute_stance` / `_refresh_llm_controlled_roles` 真正涉及种族特化。lifecycle hook publish / EventBus / down_q 消费 / camera drain / tactics 节流 / hang watchdog 全部 race-agnostic。

---

## §3 race-agnostic vs race-specific 边界

### 3.1 race-agnostic（提到 `auto_combat/common/` 或保留在 `bot/`）

| 组件 | 当前位置 | 建议位置 |
|---|---|---|
| 11 个 `_publish_xxx` lifecycle hook | `protoss/bot.py` 模块顶 | `auto_combat/common.py` 或新建 `auto_combat/lifecycle.py` |
| `_SharpyFacade` 类 | `protoss/bot.py` 内嵌 | `auto_combat/common_facade.py`，由各族 bot 继承 |
| `_VibeCraftProtossBot` 中 lifecycle 转发、down_q 消费、tactics 节流、minimap 推送、hang watchdog、refresh_llm_controlled_roles | 同上 | `auto_combat/common_bot.py` 抽象基类 |
| `_compute_stance` | `protoss/bot.py` | 可上提到基类（exclude 集合参数化）|

### 3.2 race-specific（每族独立）

| 组件 | 神族 | 虫族 | 人族 |
|---|---|---|---|
| `create_plan` 入口 | IfElse 路由 + ScoutWorker 通用层 | IfElse + ScoutOverlord | IfElse + ScoutSCV |
| `EXCLUDE_FROM_ARMY` | `{PROBE, OBSERVER, WARPPRISM}` | `{DRONE, OVERLORD, OVERSEER}` | `{SCV, MULE}` |
| `DEFAULT_OPENING_ID` | `4bg` | `12pool` 或 `macro_hatch` | `marine_rush` |
| sustain plan | `BG → BY → 折跃 → 2 矿 + Stalker 守家` | `12 池 → 蟑螂巢 → 2 矿 + 蟑螂守家 + AutoOverlord` | `BB → BE → 2 矿 + Marine 守家 + AutoDepot + MULE` |
| 通用 scout layer | `ScoutWorker`（probe） | `ScoutOverlord` | `ScoutSCV` |
| forward_proxy / forward_warp | 神族独有（修水晶折跃） | M7（creep tumor） | M7（proxy Barracks） |

### 3.3 共享方式：选 A + B 混合

**A 抽象基类 `VibeCraftBotBase(KnowledgeBot)`**：
- lifecycle hook 转发
- EventBus 初始化
- down_q 消费
- tactics 节流 + camera drain + hang watchdog
- `_refresh_llm_controlled_roles` / `is_vibecraft_controlled`
- `_SharpyFacade` 基类

**B 工厂函数 `make_zerg_bot_class(...) / make_terran_bot_class(...)`**：
- 签名与现有 `make_protoss_bot_class` 完全一致
- 子类只填 `EXCLUDE_FROM_ARMY` / `DEFAULT_OPENING_ID` / `create_plan` 三处

**重构 ordering**：M6.0 先做"神族重构"——把 `_VibeCraftProtossBot` 内通用部分上提到基类，神族跑通所有现有单测 → 才能添加新种族（先把契约固化下来再扩展）。

---

## §4 directory layout

```
src/vibecraft/bot/
├── facade.py                       # race-agnostic（已存在）
├── director.py                     # race-agnostic
├── event_bus.py                    # race-agnostic
├── minimap.py                      # race-agnostic
├── named_spot.py                   # race-agnostic
├── task_monitor.py                 # race-agnostic
├── watchdog.py                     # race-agnostic
├── sharpy_adapter.py               # 改：3 个 race dispatch
└── auto_combat/
    ├── common.py                   # 已存在
    ├── common_bot.py               # NEW：VibeCraftBotBase + _SharpyFacadeBase
    ├── decision_watcher.py         # race-agnostic
    ├── protoss/
    │   ├── bot.py                  # 改：薄壳
    │   └── plans/                  # 不动
    ├── zerg/                       # NEW
    │   ├── bot.py                  # _VibeCraftZergBot + make_zerg_bot_class
    │   └── plans/
    │       ├── sustain.py
    │       ├── scout_overlord.py
    │       ├── twelve_pool.py
    │       ├── macro_hatch.py
    │       ├── roach_hydra.py
    │       ├── mutalisk_harass.py
    │       └── brood_corruptor.py
    └── terran/                     # NEW
        ├── bot.py                  # _VibeCraftTerranBot + make_terran_bot_class
        └── plans/
            ├── sustain.py
            ├── scout_scv.py
            ├── marine_rush.py
            ├── reaper_expand.py
            ├── bio_stim.py
            ├── two_base_tanks.py
            └── bc_late.py

strategies/
├── protoss/                        # 已存在 8 个 yaml
├── zerg/                           # NEW
│   ├── 12pool.yaml                 # opening cheese
│   ├── macro_hatch.yaml            # opening 标准
│   ├── roach_hydra.yaml            # midgame
│   ├── mutalisk_harass.yaml        # midgame
│   └── brood_corruptor.yaml        # lategame
└── terran/                         # NEW
    ├── marine_rush.yaml            # opening cheese
    ├── reaper_expand.yaml          # opening 标准
    ├── bio_stim.yaml               # midgame
    ├── two_base_tanks.yaml         # midgame
    └── bc_late.yaml                # lategame

docs/aliases/
├── protoss.yaml                    # 已存在（满）
├── zerg.yaml                       # 改：取消注释，填满 §8.1
├── terran.yaml                     # 改：取消注释，填满 §8.2
└── system.yaml                     # 已存在
```

---

## §5 GameConfig + race 路由

### 5.1 GameConfig 新字段

```python
@dataclass
class GameConfig:
    map_name: str = "DaybreakLE"
    my_race: str = "Protoss"          # NEW
    opponent_race: str = "Random"
    opponent_difficulty: str = "VeryHard"
    realtime: bool = True
    # ...
```

`_child_entry` 改：`Bot(Race[config.my_race], bot_instance, ...)`。

### 5.2 sharpy_adapter dispatch

```python
def make_bot_class(..., race: str = "Protoss") -> type:
    if race == "Protoss":
        return make_protoss_bot_class(...)
    if race == "Zerg":
        return make_zerg_bot_class(...)
    if race == "Terran":
        return make_terran_bot_class(...)
    raise ValueError(f"unknown race: {race!r}")
```

### 5.3 service 入口

CLI: `vibecraft serve --my-race {Protoss|Zerg|Terran}`（默认 Protoss）。

### 5.4 LLM prompt + StrategyLibrary 切换

`game_process._build_bot_class`：
```python
strategies_dir = _project_root / "strategies" / config.my_race.lower()
aliases_path = _project_root / "docs" / "aliases" / f"{config.my_race.lower()}.yaml"
```

---

## §6 虫族剧本选型

| ID | kind | display_name_zh | sharpy dummy | 简述 |
|---|---|---|---|---|
| `12pool` | opening | 12 池小狗 rush | `dummies.zerg.twelve_pool:TwelvePool` | 12 农下 BS，小狗 6 只出门骚扰 |
| `macro_hatch` | opening | 标准三矿 macro | 自写（参考 `dummies.zerg.macro_zerg_v2`） | 17 池 → 三矿 → 蟑螂或飞龙转 |
| `roach_hydra` | midgame | 蟑螂刺蛇推进 | `dummies.zerg.roach_hydra:RoachHydra` | 4-5 矿地面海 |
| `mutalisk_harass` | midgame | 飞龙骚扰 | `dummies.zerg.mutalisk:Mutalisk` | 三矿飞龙 |
| `brood_corruptor` | lategame | 巢虫领主 + 腐化 | 自写 | 雷兽塔升级 → BL + 腐化 + 感染虫 |

**转移**：12pool → roach_hydra（默认）；macro_hatch → roach_hydra/mutalisk_harass；midgame → brood_corruptor。

---

## §7 人族剧本选型

| ID | kind | display_name_zh | sharpy dummy | 简述 |
|---|---|---|---|---|
| `marine_rush` | opening | 双兵营 marine rush | `dummies.terran.marine_rush:MarineRush` | 一矿双 BB，stim 后 4 分压制 |
| `reaper_expand` | opening | 死神扩张 | 自写 | 1 死神骚扰 + 二矿 |
| `bio_stim` | midgame | bio 推进 | `dummies.terran.bio:BioBot` | 双兵营 + medivac + stim |
| `two_base_tanks` | midgame | 双矿坦克 | `dummies.terran.two_base_tanks:TwoBaseTanks` | 工厂坦克 + bunker |
| `bc_late` | lategame | 战巡终结 | 复用 `dummies.terran.battle_cruisers` | BC + Liberator 后期 |

---

## §8 术语表设计

### 8.1 虫族 aliases（取消注释 + 补全 docs/aliases/zerg.yaml）

完整建筑表（SC2 grid hotkey）：
```yaml
buildings:
  Hatchery:        { default_display: "BH", aliases: [BH, 孵化, 基地, 一本], hotkey: "B+H" }
  Lair:            { default_display: "Lair", aliases: [虫穴, 二本] }
  Hive:            { default_display: "Hive", aliases: [主巢, 三本] }
  Extractor:       { default_display: "BE", aliases: [BE, 气矿], hotkey: "B+E" }
  SpawningPool:    { default_display: "BS", aliases: [BS, 母池, 狗洞], hotkey: "B+S" }
  EvolutionChamber:{ default_display: "BV", aliases: [BV, 进化腔, 升级巢], hotkey: "B+V" }
  RoachWarren:     { default_display: "BR", aliases: [BR, 蟑螂巢], hotkey: "B+R" }
  BanelingNest:    { default_display: "BB", aliases: [BB, 爆虫巢, 自杀虫巢], hotkey: "B+B" }
  SpineCrawler:    { default_display: "BC", aliases: [BC, 刺蛇塔, 防御塔], hotkey: "B+C" }
  SporeCrawler:    { default_display: "BP", aliases: [BP, 孢子塔, 防空塔], hotkey: "B+P" }
  HydraliskDen:    { default_display: "VH", aliases: [VH, 刺蛇巢, 龙穴], hotkey: "V+H" }
  LurkerDen:       { default_display: "VL", aliases: [VL, 潜伏者巢, 地刺巢], hotkey: "V+L" }
  InfestationPit:  { default_display: "VI", aliases: [VI, 感染深渊], hotkey: "V+I" }
  Spire:           { default_display: "VS", aliases: [VS, 尖塔, 飞龙塔], hotkey: "V+S" }
  GreaterSpire:    { default_display: "GreaterSpire", aliases: [大尖塔] }
  NydusNetwork:    { default_display: "VN", aliases: [VN, 虫巢网络], hotkey: "V+N" }
  UltraliskCavern: { default_display: "VU", aliases: [VU, 雷兽巢], hotkey: "V+U" }

units:
  Drone:      { default_display: "农民", aliases: [农民, 工蜂, Drone] }
  Overlord:   { default_display: "OL", aliases: [OL, 王虫, 监工] }
  Overseer:   { default_display: "OS", aliases: [OS, 监察王虫] }
  Larva:      { default_display: "幼虫", aliases: [幼虫] }
  Queen:      { default_display: "女王", aliases: [女王, 后] }
  Zergling:   { default_display: "小狗", aliases: [小狗, 跳虫, 狗, Ling] }
  Baneling:   { default_display: "爆虫", aliases: [爆虫, 自杀虫, 妖虫] }
  Roach:      { default_display: "蟑螂", aliases: [蟑螂, 小强] }
  Ravager:    { default_display: "破坏者", aliases: [破坏者, 大蟑螂] }
  Hydralisk:  { default_display: "刺蛇", aliases: [刺蛇, 龙, Hydra] }
  Lurker:     { default_display: "潜伏者", aliases: [潜伏者, 地刺] }
  Infestor:   { default_display: "感染虫", aliases: [感染虫, 感染] }
  SwarmHost:  { default_display: "蝗虫", aliases: [蝗虫, 母虫] }
  Mutalisk:   { default_display: "飞龙", aliases: [飞龙, Muta] }
  Corruptor:  { default_display: "腐化者", aliases: [腐化者, 腐化] }
  Viper:      { default_display: "毒蛇", aliases: [毒蛇] }
  NydusWorm:  { default_display: "虫洞", aliases: [虫洞] }
  Ultralisk:  { default_display: "雷兽", aliases: [雷兽, 雷] }
  BroodLord:  { default_display: "BL", aliases: [BL, 巢虫领主, 巢虫] }

upgrades:
  Burrow:                { default_display: "钻地", aliases: [钻地] }
  MetabolicBoost:        { default_display: "狗速", aliases: [狗速, 小狗速度] }
  AdrenalGlands:         { default_display: "狗攻速", aliases: [狗攻速] }
  CentrifugalHooks:      { default_display: "爆虫速", aliases: [爆虫速] }
  GlialReconstitution:   { default_display: "蟑螂速", aliases: [蟑螂速] }
  GroovedSpines:         { default_display: "刺蛇射程", aliases: [刺蛇射程] }
  MuscularAugments:      { default_display: "刺蛇速", aliases: [刺蛇速] }
  PneumatizedCarapace:   { default_display: "OL速", aliases: [OL速] }
  ChitinousPlating:      { default_display: "雷兽甲", aliases: [雷兽甲] }
  AnabolicSynthesis:     { default_display: "雷兽速", aliases: [雷兽速] }
  ZergMeleeWeapons:      { default_display: "近战攻击", aliases: [近战攻, 狗攻] }
  ZergMissileWeapons:    { default_display: "远程攻击", aliases: [远程攻, 蟑攻] }
  ZergGroundArmor:       { default_display: "地面护甲", aliases: [地甲, 虫族地甲] }
  ZergFlyerWeapons:      { default_display: "空军攻击", aliases: [空攻, 飞龙攻] }
  ZergFlyerArmor:        { default_display: "空军护甲", aliases: [空甲] }
```

### 8.2 人族 aliases（取消注释 docs/aliases/terran.yaml）

```yaml
buildings:
  CommandCenter:    { default_display: "BN", aliases: [BN, CC, 基地, 主基地], hotkey: "B+N" }
  OrbitalCommand:   { default_display: "OC", aliases: [OC, 轨道指挥] }
  PlanetaryFortress:{ default_display: "PF", aliases: [PF, 行星要塞] }
  SupplyDepot:      { default_display: "BS", aliases: [BS, 房子, 补给, Depot], hotkey: "B+S" }
  Refinery:         { default_display: "BR", aliases: [BR, 气矿], hotkey: "B+R" }
  Barracks:         { default_display: "BB", aliases: [BB, 兵营, Rax], hotkey: "B+B" }
  EngineeringBay:   { default_display: "BE", aliases: [BE, 工程站, EBay], hotkey: "B+E" }
  Bunker:           { default_display: "BU", aliases: [BU, 碉堡, 地堡], hotkey: "B+U" }
  MissileTurret:    { default_display: "BT", aliases: [BT, 防空塔, 飞弹塔], hotkey: "B+T" }
  SensorTower:      { default_display: "感应塔", aliases: [感应塔, 雷达塔], hotkey: "B+N" }
  Factory:          { default_display: "BF", aliases: [BF, 工厂], hotkey: "B+F" }
  Starport:         { default_display: "BP", aliases: [BP, 星港, 机场], hotkey: "B+P" }
  Armory:           { default_display: "BA", aliases: [BA, 升级厂, 兵工厂], hotkey: "B+A" }
  GhostAcademy:     { default_display: "BG", aliases: [BG, 幽灵学院], hotkey: "B+G" }
  FusionCore:       { default_display: "BC", aliases: [BC, 聚变芯, 战巡塔], hotkey: "B+C" }

units:
  SCV:        { default_display: "农民", aliases: [农民, 工人, SCV] }
  MULE:       { default_display: "MULE", aliases: [骡子, MULE] }
  Marine:     { default_display: "枪兵", aliases: [枪兵, 机枪兵, M] }
  Marauder:   { default_display: "掠夺者", aliases: [掠夺者, 大兵] }
  Reaper:     { default_display: "死神", aliases: [死神] }
  Ghost:      { default_display: "幽灵", aliases: [幽灵] }
  Hellion:    { default_display: "恶火", aliases: [恶火, 火蜥蜴] }
  Hellbat:    { default_display: "蝙蝠", aliases: [蝙蝠, 地狱火蝙蝠] }
  WidowMine:  { default_display: "寡妇雷", aliases: [寡妇, 寡妇雷, 蜘蛛雷] }
  Cyclone:    { default_display: "旋风", aliases: [旋风] }
  SiegeTank:  { default_display: "坦克", aliases: [坦克, 攻城坦克, Tank] }
  Thor:       { default_display: "雷神", aliases: [雷神] }
  Viking:     { default_display: "维京", aliases: [维京] }
  Medivac:    { default_display: "医疗船", aliases: [医疗船, 运输船, MV] }
  Liberator:  { default_display: "解放者", aliases: [解放者] }
  Raven:      { default_display: "渡鸦", aliases: [渡鸦] }
  Banshee:    { default_display: "女妖", aliases: [女妖] }
  Battlecruiser:{ default_display: "战巡", aliases: [战巡, 船长, 战列巡洋舰, BC] }

upgrades:
  CombatShield:       { default_display: "枪兵盾", aliases: [枪兵盾, 战斗护盾] }
  Stimpack:           { default_display: "兴奋剂", aliases: [兴奋剂, Stim, 嗑药] }
  ConcussiveShells:   { default_display: "减速弹", aliases: [减速弹] }
  YamatoCannon:       { default_display: "大和炮", aliases: [大和炮, 大和] }
  TerranInfantryWeapons:{ default_display: "步兵攻击", aliases: [步兵攻, 兵攻] }
  TerranInfantryArmor:  { default_display: "步兵护甲", aliases: [步兵甲, 兵甲] }
  TerranVehicleWeapons: { default_display: "机械攻击", aliases: [机攻] }
  TerranVehiclePlating: { default_display: "机械护甲", aliases: [机甲] }
  TerranShipWeapons:    { default_display: "空军攻击", aliases: [空攻] }
  TerranShipPlating:    { default_display: "空军护甲", aliases: [空甲] }
```

---

## §9 sharpy 集成（race-specific）

### 9.1 复用矩阵

| sharpy 组件 | 神族 | 虫族 | 人族 |
|---|---|---|---|
| `KnowledgeBot` base | ✓ | ✓ | ✓ |
| `BuildOrder` / `Step` / `IfElse` | ✓ | ✓ | ✓ |
| `DistributeWorkers` / `SpeedMining` | ✓ | ✓ | ✓ |
| `PlanZoneAttack` / `PlanZoneDefense` / `PlanZoneGather` / `PlanFinishEnemy` | ✓ | ✓ | ✓ |
| `AutoOverLord` / `InjectLarva` / `MorphLair` / `MorphHive` | - | **必用** | - |
| `MorphOrbitals` / `CallMule` / `ScanEnemy` / `LowerDepots` / `Repair` / `ManTheBunkers` / `AutoDepot` | - | - | **必用** |
| `dummies.zerg.*` 11 个 | - | 复用 5 个 | - |
| `dummies.terran.*` 11 个 | - | - | 复用 5 个 |

### 9.2 关键 race-specific 注意

**虫族**：
- `InjectLarva()` 必入 sustain + 每个 plan tactics（女王自动 inject 经济）
- `AutoOverLord()` 必入（防 supply block）
- creep tumor 推进不实现（M7）

**人族**：
- `MorphOrbitals()` 必入（升级 OC 才有 MULE + scan）
- `CallMule(50)` / `AutoDepot` / `LowerDepots` / `Repair` / `ManTheBunkers` 必入 sustain
- 飞房子不实现（玩家想飞就 unit_claim 后手动）

**unit_claim 兼容性 spike**（M6.3 必跑）：
- 人族 `ManTheBunkers` 把 marines 自动塞 bunker，需要确认 LLM_CONTROLLED marine 是否被拉走 → fake_sharpy 单测覆盖
- 虫族 `PlanZoneAttack2` (twelve_pool 用) 显式 `set_task(Attacking, drone)` → 我们改用 `PlanZoneAttack` 基类 + VibeCraftZoneAttack

---

## §10 PWA UI 改动（M6 最小集）

### 10.1 game_status 加 my_race 字段

服务端 game_status 帧加 `my_race: "Protoss"|"Zerg"|"Terran"`，PWA 在 lobby 拿到。

### 10.2 显示

| 元素 | 神族 | 虫族 | 人族 |
|---|---|---|---|
| 顶部 race badge | 蓝 | 紫 | 红 |
| 卡片中的建筑缩写 | BG/BY/VR | BS/BV/VL | BB/BE/BF |

display 数据全部来自 bot 推送的 snapshot（display_name_zh + phases），PWA 直接渲染。

### 10.3 race 选择

M6: CLI `vibecraft serve --my-race Zerg`
M7: PWA lobby 选种族

---

## §11 测试策略

```
tests/unit/
├── test_protoss_bot_*.py             # 已存在
├── test_common_bot_base.py           # NEW: VibeCraftBotBase 抽出
├── test_zerg_bot_smoke.py            # NEW
├── test_zerg_bot_all_strategies_loaded.py  # NEW
├── test_zerg_aliases.py              # NEW
├── test_terran_bot_smoke.py          # NEW
├── test_terran_bot_all_strategies_loaded.py  # NEW
├── test_terran_aliases.py            # NEW
└── test_sharpy_adapter_dispatch.py   # 改: 3 个 race 都能 dispatch
```

**关键不变量**：
- `strategies/zerg/*.yaml` 中所有 `sharpy_dummy_class` 都能 import + 实例化 + create_plan 不抛
- `docs/aliases/zerg.yaml` 中所有 alias 无歧义
- unit_claim 后的 zerg drone / terran scv 不被 sharpy DistributeWorkers / Repair 拉回

---

## §12 实施里程碑

| 里程碑 | 工时 | 描述 |
|---|---|---|
| **M6.0** | 4h | 神族重构 — 抽 `VibeCraftBotBase` 出 `common_bot.py`，神族瘦身为薄壳 |
| **M6.1** | 2h | `GameConfig.my_race` + `sharpy_adapter` 三族 dispatch |
| **M6.2a** | 4h | 虫族 alias 表 + 5 个 strategy yaml |
| **M6.2b** | 6h | 虫族 bot class + plans（含 sustain + ScoutOverlord）|
| **M6.3a** | 4h | 人族 alias 表 + 5 个 strategy yaml |
| **M6.3b** | 6h | 人族 bot class + plans（含 sustain + ScoutSCV）|
| **M6.4** | 2h | 文档：ARCH / USER_GUIDE / CLAUDE / ADR 0010 |

**串行总工期**：~28h ≈ 3.5 天
**并行总工期**（M6.2 / M6.3 拆 2 个 subagent）：~18h ≈ 2.5 天

---

## §13 风险与未决问题

| 风险 | 概率 | 缓解 |
|---|---|---|
| sharpy dummies 在 vibecraft 共享层下行为变异（InjectLarva 与 unit_claim 冲突） | 中 | M6.2b 早期 spike 单测 |
| 人族 ManTheBunkers 把 LLM_CONTROLLED marine 拉进 bunker | 中 | M6.3 spike 单测 |
| 虫族剧本切换后 larva morphing 中的单位无法回退 | 高 | 接受 transition tax，PWA 提示玩家 |
| sharpy `realtime_worker = False`（twelve_pool 配置） | 低 | vibecraft 子类 __init__ 显式覆盖 |

**未决问题**：
1. 剧本切换的沉没成本（已下蛋）：默认不取消
2. opponent_race counter：M6 不做
3. race hot-reload：不做，每次新 game_process

---

## §14 决策日志

| # | 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|---|
| 1 | 共享代码组织 | A.基类 / B.工厂 / C.mixin | A+B 混合 | 神族当前是工厂，基类抽 lifecycle 减重复，mypy 友好 |
| 2 | 先做哪个种族 | 串行 / 并行 | 并行 | 文件 0 重叠 |
| 3 | sharpy dummies 复用 | 全自写 / 全复用 / 子类化 | 子类化 | 抄 BuildOrder，PlanZoneAttack 子类覆盖 vibecraft override |
| 4 | alias 合并加载 | 三族合一 / 按种族切 | 按种族切 | 阻止 BC / BB 跨族歧义 |
| 5 | opponent_race 感知 | 是 / 否 | 否（M6）| MVP 简化 |
| 6 | sustain 共享 | 三族 1 个 / 每族独立 | 每族独立 | 虫/人必备 Manager 不同 |
| 7 | 飞房子 | 是 / 否 | 否（M6）| sharpy 无实现，unit_claim workaround |
| 8 | creep tumor | 是 / 否 | 否（M6）| M7 课题 |
| 9 | PWA race 选择 | CLI / PWA lobby | CLI（M6），PWA（M7）| MVP 不动 PWA |
| 10 | `active_recipe` 默认 | 写死 / yaml | 写死 in bot class | 与神族一致，env var 仍可 override |
| 11 | sharpy_adapter dispatch | if/elif / dict | if/elif（3 个分支）| 三族封闭集 |
| 12 | vendor/sharpy 复制 | 复制 / import | import | vendor 已在 sys.path |

---

## 附录 A：strategy yaml 模板（虫族 12pool）

```yaml
kind: opening_build
id: 12pool
display_name_zh: "12 池小狗 rush"
summary_zh: "12 农下 BS,小狗暴出门骚扰;打死=赢,打不死开二矿转飞龙"
sharpy_dummy_class: "vibecraft.bot.auto_combat.zerg.plans.twelve_pool:TwelvePool"
aliases:
  - "12pool"
  - "12 pool"
  - "12 池"
  - "12池"
  - "小狗 rush"
  - "狗 rush"
matchup: [ZvT, ZvZ, ZvP]

phases:
  - id: opening
    display: "12 池 + 6 狗"
    subtitle: "12 农下 BS,出 6 小狗集合"
    start_at_supply: 12
  - id: pressuring
    display: "首波小狗骚扰"
    subtitle: "小狗到对方家送 + 火力侦察"
    start_at_supply: 14
    start_at_time: 130
  - id: transition
    display: "未杀死则转飞龙"
    subtitle: "拉气 + Lair + Spire"
    start_at_supply: 24
    start_at_time: 240

steps:
  - "12 build BS"
  - "13 train OL"
  - "14 train 小狗"
  - "14 train 小狗"
  - "14 train 小狗"
  - "16 build BE"
  - "20 build Lair"
  - "24 build VS"
  - "30 train 飞龙"

scout_at: "13 send_probe enemy_natural"

abort_signals:
  - sees: "enemy.units.cyclone.count >= 1"
    then: "transition:macro_hatch"

default_transitions:
  - midgame_id: roach_hydra
    when: "default"
```

---

## 附录 B：bot class 工厂签名（统一）

```python
def make_zerg_bot_class(
    director_factory: Any,
    strategy_library: Any,
    status_callback: Any,
    down_q: Any,
    echo_callback: Any,
    snapshot_callback: Any,
    event_callback: Any,
    minimap_callback: Any,
    run_command_with_echo_fn: Any,
) -> type:
    """工厂：返回 _VibeCraftZergBot(VibeCraftBotBase) 类。

    签名与 make_protoss_bot_class 完全一致(便于 sharpy_adapter dispatch 表)。
    """
```

terran 同。
