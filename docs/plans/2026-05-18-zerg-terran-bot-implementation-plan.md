# 虫族 / 人族 bot 实施计划

> 创建：2026-05-18
> 配套设计文档：`docs/plans/2026-05-18-zerg-terran-bot-design.md`
> 分支：`m6-zerg-terran-bot`
> 总工期估算：**3-5 天**（含端到端）；M6.2 / M6.3 可并行
> 每个 task TDD 循环：写测试 → 跑红 → 写实现 → 跑绿 → 简化重构 → commit

---

## Task 0：M6.0 神族重构（抽 VibeCraftBotBase）

**估时**：4 小时
**并行机会**：无（前置条件）

### Files

- 新建 `src/vibecraft/bot/auto_combat/common_bot.py`
- 改 `src/vibecraft/bot/auto_combat/protoss/bot.py`
- 改 `tests/unit/test_protoss_bot_*.py`（按需）
- 新建 `tests/unit/test_common_bot_base.py`

### Steps

1. **写测试**：`test_common_bot_base.py` 用 fake_sharpy stub，断言 `VibeCraftBotBase.__init__` 设置 `event_bus`、`_llm_controlled_tags`、`named_spots`、`_voice_step_count = 0`、`_sharpy_iteration = 0`。
2. **写测试**：断言 11 个 `_publish_xxx` helper 函数可单独 import + 接 fake_bot 调通。
3. **写实现**：把 `protoss/bot.py` 顶部 11 个 publish helper + `_SharpyFacade`（重命名 `_SharpyFacadeBase`）+ `_VibeCraftProtossBot` 内通用方法搬到 `common_bot.py`。`VibeCraftBotBase` 抽象类签名：

   ```python
   class VibeCraftBotBase(KnowledgeBot):
       EXCLUDE_FROM_ARMY: ClassVar[set] = set()    # 子类必填
       DEFAULT_OPENING_ID: ClassVar[str] = ""      # 子类必填
       async def create_plan(self) -> BuildOrder:
           raise NotImplementedError
   ```
4. **改 `protoss/bot.py`**：薄壳：
   ```python
   from vibecraft.bot.auto_combat.common_bot import VibeCraftBotBase, _SharpyFacadeBase, _ensure_sharpy_on_path

   class _VibeCraftProtossBot(VibeCraftBotBase):
       EXCLUDE_FROM_ARMY = {UnitTypeId.PROBE, UnitTypeId.OBSERVER, UnitTypeId.WARPPRISM}
       DEFAULT_OPENING_ID = "4bg"
       async def create_plan(self) -> BuildOrder:
           # 现有逻辑搬过来
   ```
5. **跑全部 806+ 单测**：必须全绿；ruff、mypy 干净。

### Commit

```
M6.0: 抽 VibeCraftBotBase 出 common_bot.py,神族瘦身为薄壳

为支持虫族/人族 bot,把 _VibeCraftProtossBot 中 race-agnostic 部分
(lifecycle hook publish / EventBus init / down_q 消费 / camera drain /
hang watchdog / tactics 节流 / refresh_llm_controlled_roles)上提到
VibeCraftBotBase(KnowledgeBot 子类)。protoss/bot.py 仅保留 EXCLUDE_FROM_ARMY /
DEFAULT_OPENING_ID / create_plan 三处神族特化。所有现有单测保持通过。
```

---

## Task 1：M6.1 GameConfig + sharpy_adapter race dispatch

**估时**：2 小时
**并行机会**：无（Task 2/3 前置）

### Files

- 改 `src/vibecraft/server/game_process.py`
- 改 `src/vibecraft/bot/sharpy_adapter.py`
- 改 `src/vibecraft/cli.py`
- 新建 `tests/unit/test_game_config_my_race.py`
- 改 `tests/unit/test_sharpy_adapter.py`

### Steps

1. **写测试**：`test_game_config_my_race.py` 断言 `GameConfig(my_race="Zerg").my_race == "Zerg"`；默认 `"Protoss"`。
2. **写测试**：`test_sharpy_adapter.py` 加 case：`make_bot_class(race="Zerg")` 不再抛 NotImplementedError 而是返回类（最初可 stub return type）。
3. **写实现**：`GameConfig` 加 `my_race: str = "Protoss"`；`_child_entry` 用 `Race[config.my_race]` 替换硬编码。
4. **写实现**：`sharpy_adapter.make_bot_class` dispatch 表：
   ```python
   if race == "Protoss":
       from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class
       return make_protoss_bot_class(...)
   if race == "Zerg":
       from vibecraft.bot.auto_combat.zerg.bot import make_zerg_bot_class
       return make_zerg_bot_class(...)
   if race == "Terran":
       from vibecraft.bot.auto_combat.terran.bot import make_terran_bot_class
       return make_terran_bot_class(...)
   ```
5. **改 `game_process._build_bot_class`**：用 `config.my_race` 拼 `strategies_dir` / `aliases_path`。
6. **CLI**：加 `--my-race {Protoss,Zerg,Terran}` flag。
7. **跑测试**：单测全绿。

### Commit

```
M6.1: GameConfig 加 my_race 字段;sharpy_adapter 三族 dispatch

GameConfig 新增 my_race(默认 Protoss)。子进程 _child_entry 用
Race[config.my_race] 实例化;game_process._build_bot_class 用 my_race
拼 strategies/<race>/ + docs/aliases/<race>.yaml 路径。sharpy_adapter
make_bot_class 加 Zerg/Terran 分支,目标模块此时尚未创建,单测 mock import。
```

---

## Task 2a：M6.2a 虫族 alias 表 + strategy yaml

**估时**：4 小时
**并行机会**：与 Task 3a 并行（不同种族）

### Files

- 改 `docs/aliases/zerg.yaml`（取消注释 + 补全 §8.1 列出的全条目）
- 新建 `strategies/zerg/12pool.yaml`
- 新建 `strategies/zerg/macro_hatch.yaml`
- 新建 `strategies/zerg/roach_hydra.yaml`
- 新建 `strategies/zerg/mutalisk_harass.yaml`
- 新建 `strategies/zerg/brood_corruptor.yaml`
- 新建 `tests/unit/test_zerg_aliases.py`
- 新建 `tests/unit/test_zerg_strategies.py`

### Steps

1. **写测试**：`test_zerg_aliases.py` 断言：
   - `AliasTable.from_yaml(zerg.yaml)` 不抛
   - 所有 alias `casefold` 后无歧义（同字符不映射多 canonical）
   - 关键玩家话语能 resolve：`"小狗" → Zergling`、`"妖虫" → Baneling`、`"BL" → BroodLord`、`"BS" → SpawningPool`
2. **写测试**：`test_zerg_strategies.py` 断言：
   - 5 个 yaml 都能 `StrategyLibrary.from_directories(strategies/zerg, docs/aliases/zerg.yaml)` 加载
   - 跨引用通过（每个 opening 的 default_transitions 指向已存在的 midgame）
   - 所有 `sharpy_dummy_class` 字符串语法合法（"module:Class" 格式）
3. **写实现**：填 `zerg.yaml` 按 §8.1 模板逐条加。
4. **写实现**：5 个 yaml 按附录 A 模板写。
5. **跑测试**：全绿。

### Commit

```
M6.2a: 虫族 alias 表填充 + 5 个 strategy yaml 落地

docs/aliases/zerg.yaml 取消注释,按 SC2 grid hotkey 填全 buildings/units/upgrades
(BS=母池,BV=进化腔,VH=刺蛇巢,VL=潜伏者巢等;玩家话语小狗/妖虫/BL/小强等都 alias)。
strategies/zerg/ 加 5 个剧本:12pool/macro_hatch(opening)、roach_hydra/
mutalisk_harass(midgame)、brood_corruptor(lategame),形成完整转移图。
新增 test_zerg_aliases / test_zerg_strategies 验证 round-trip + 无歧义。
```

---

## Task 3a：M6.3a 人族 alias 表 + strategy yaml

**估时**：4 小时
**并行机会**：与 Task 2a 并行

### Files

- 改 `docs/aliases/terran.yaml`
- 新建 `strategies/terran/marine_rush.yaml`
- 新建 `strategies/terran/reaper_expand.yaml`
- 新建 `strategies/terran/bio_stim.yaml`
- 新建 `strategies/terran/two_base_tanks.yaml`
- 新建 `strategies/terran/bc_late.yaml`
- 新建 `tests/unit/test_terran_aliases.py`
- 新建 `tests/unit/test_terran_strategies.py`

### Steps + Commit

结构同 Task 2a。按 §8.2 alias 表 + §7 剧本表内容落地。

关键玩家话语断言：`"枪兵" → Marine`、`"船长" → Battlecruiser`、`"医疗船" → Medivac`、`"BB" → Barracks`、`"BC" → FusionCore`（注意跟神族 BC=PhotonCannon 区分，按种族切换 alias 表避免歧义）。

### Commit

```
M6.3a: 人族 alias 表填充 + 5 个 strategy yaml 落地

docs/aliases/terran.yaml 取消注释,按 SC2 grid hotkey 填全(BB=兵营,BF=工厂,
BP=星港,BC=聚变芯)。strategies/terran/ 加 5 个剧本:marine_rush/reaper_expand
(opening)、bio_stim/two_base_tanks(midgame)、bc_late(lategame)。
test_terran_aliases 覆盖船长/枪兵/医疗船等典型话语。
```

---

## Task 2b：M6.2b 虫族 bot class + plans

**估时**：6 小时
**并行机会**：与 Task 3b 并行（不同种族）；依赖 Task 0/1/2a

### Files

- 新建 `src/vibecraft/bot/auto_combat/zerg/__init__.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/bot.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/__init__.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/sustain.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/scout_overlord.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/twelve_pool.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/macro_hatch.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/roach_hydra.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/mutalisk_harass.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/brood_corruptor.py`
- 新建 `src/vibecraft/bot/auto_combat/zerg/plans/vibecraft_zone_attack.py`
- 新建 `tests/unit/test_zerg_bot_smoke.py`
- 新建 `tests/unit/test_zerg_bot_all_strategies_loaded.py`

### Steps

1. **写测试**：`test_zerg_bot_smoke.py`：
   - `make_zerg_bot_class(...)` 返回类，instance 初始化不抛
   - 类型继承链：`issubclass(cls, VibeCraftBotBase)`
   - `cls.EXCLUDE_FROM_ARMY == {DRONE, OVERLORD, OVERSEER}`
   - `cls.DEFAULT_OPENING_ID == "12pool"`
2. **写测试**：`test_zerg_bot_all_strategies_loaded.py`：参考 `test_protoss_bot_all_strategies_loaded.py`，用真 sharpy 跑 `create_plan`，断言 5 个 sharpy_dummy_class 全 import 成功且 IfElse 路由树构造正确。
3. **写实现 (sustain)**：`zerg/plans/sustain.py`：
   ```python
   class ZergSustain(KnowledgeBot):
       async def create_plan(self):
           return BuildOrder([
               ActUnit(DRONE, LARVA, 16),
               GridBuilding(SPAWNINGPOOL, 1),
               Expand(2),
               ActUnit(QUEEN, HATCHERY, 2),
               BuildGas(2),
               GridBuilding(ROACHWARREN, 1),
               ActUnit(ROACH, LARVA, 10),
               AutoOverLord(),
               InjectLarva(),
               DistributeWorkers(),
               PlanWorkerOnlyDefense(),
               PlanZoneDefense(),
               PlanZoneGather(),
               # 注意:无 PlanZoneAttack(sustain 不主动出门)
               PlanFinishEnemy(),
           ])
   ```
4. **写实现 (scout_overlord)**：派第二只 OL 飘到敌方 natural，发现 ATA/cyclone/phoenix → 立即撤。
5. **写实现 (5 个剧本 plans)**：每个 plan 子类化 sharpy dummy 或自写 acts 列表。
6. **写实现 (bot.py)**：
   ```python
   def make_zerg_bot_class(...) -> type:
       _ensure_sharpy_on_path()
       from vibecraft.bot.auto_combat.common_bot import VibeCraftBotBase

       class _VibeCraftZergBot(VibeCraftBotBase):
           EXCLUDE_FROM_ARMY = {UnitTypeId.DRONE, UnitTypeId.OVERLORD, UnitTypeId.OVERSEER}
           DEFAULT_OPENING_ID = "12pool"

           async def create_plan(self):
               # 与神族同样的 IfElse 路由树构造,通用层用 ScoutOverlord
               ...
       return _VibeCraftZergBot
   ```
7. **跑测试**：全绿；ruff / mypy 干净。

### Commit

```
M6.2b: 虫族 bot class + 5 个 plan + Sustain + ScoutOverlord

新建 src/vibecraft/bot/auto_combat/zerg/{bot.py,plans/*}。_VibeCraftZergBot
继承 VibeCraftBotBase,EXCLUDE_FROM_ARMY={DRONE,OVERLORD,OVERSEER},DEFAULT_OPENING_ID
="12pool"。create_plan 走 IfElse 路由,通用层为 ScoutOverlord(OL 飘视野)。5 个
plan 适配 sharpy dummies。Sustain 含 AutoOverLord + InjectLarva 必备组件。
单测覆盖 strategy load / IfElse 构造 / EXCLUDE 集合。
```

---

## Task 3b：M6.3b 人族 bot class + plans

**估时**：6 小时
**并行机会**：与 Task 2b 并行

### Files

结构同 Task 2b，路径换 `terran/`。

### Steps

人族特化点：

- `EXCLUDE_FROM_ARMY = {SCV, MULE}`（Medivac 算 army：能参战 + 治疗）
- `DEFAULT_OPENING_ID = "marine_rush"`
- sustain 必含：`MorphOrbitals` / `CallMule(50)` / `ScanEnemy` / `AutoDepot` / `LowerDepots` / `Repair`
- scout_scv：复用 `protoss/plans/scout_worker.py` 逻辑（探机改 SCV，规则相同）
- 5 个剧本：`bio_stim` / `two_base_tanks` 几乎可直接复用 sharpy dummies

**M6.3b spike**：早期验证 unit_claim 后的 marine 不会被 `ManTheBunkers` 拉进 bunker。写 fake_sharpy 单测覆盖。

### Commit

```
M6.3b: 人族 bot class + 5 个 plan + Sustain + ScoutSCV

新建 src/vibecraft/bot/auto_combat/terran/{bot.py,plans/*}。
_VibeCraftTerranBot 继承 VibeCraftBotBase,EXCLUDE_FROM_ARMY={SCV,MULE},
DEFAULT_OPENING_ID="marine_rush"。Sustain 含 MorphOrbitals + CallMule + AutoDepot +
LowerDepots + Repair 必备组件。5 个 plan 适配 sharpy dummies。
新增 unit_claim 与 ManTheBunkers 互斥的 fake_sharpy 单测。
```

---

## Task 4：M6.4 文档 + ARCH 更新 + ADR

**估时**：2 小时
**并行机会**：无（最后收尾）

### Files

- 改 `ARCHITECTURE.md`
- 改 `USER_GUIDE.md`
- 改 `TASKS.md`
- 改 `CLAUDE.md`（建筑 hotkey 表加虫/人族）
- 改 `CHANGELOG.md`
- 新建 `docs/adr/0010-tri-race-bot-abstraction.md`

### Steps

1. **`ARCHITECTURE.md`**：directory layout 加 zerg/terran 子树；不变量段加"`VibeCraftBotBase` 是三族基类，新增种族走 `make_<race>_bot_class` 工厂模板"。
2. **`USER_GUIDE.md`**：加"选择种族 — `vibecraft serve --my-race {Protoss|Zerg|Terran}`"；每族剧本简介 + 玩家话语示例。
3. **`CLAUDE.md`**：建筑 hotkey 表加：
   - 虫族 B 系：BH/BE/BS/BV/BR/BB/BC/BP；V 系：VH/VL/VI/VS/VN/VU
   - 人族 B 系：BN/BS/BR/BB/BE/BU/BT/BF/BP/BA/BG/BC
4. **ADR 0010**：记录 §3.3 决策（"基类 + 工厂"混合模式）+ §14 决策日志中 sharpy dummies 复用策略。
5. **CHANGELOG**：M6 发版条目。
6. **TASKS.md**：M6 完成；列 M7 候选（PWA race selector / random race / opponent_race counter / creep tumor / lift Terran building）。

### Commit

```
M6.4: 文档更新 — ARCH/USER_GUIDE/CLAUDE 加虫/人族条目;ADR 0010 决策记录

ARCHITECTURE.md 加 zerg/terran 子树 + VibeCraftBotBase 不变量。
USER_GUIDE 加 --my-race CLI + 各族剧本话语示例。
CLAUDE.md 建筑 hotkey 表补虫/人族 grid。
新增 docs/adr/0010-tri-race-bot-abstraction.md 记录基类+工厂混合决策。
CHANGELOG.md M6 entry。TASKS.md M6 done + M7 候选列表。
```

---

## 并行执行图

```
Task 0 (M6.0 神族重构)         [4h, 串行]
   ↓
Task 1 (M6.1 GameConfig + dispatch)  [2h, 串行]
   ↓
   ├─→ Task 2a (zerg yaml)  [4h, 可并行]
   │      ↓
   │      Task 2b (zerg bot)  [6h, 可并行]
   │
   └─→ Task 3a (terran yaml)  [4h, 可并行]
          ↓
          Task 3b (terran bot)  [6h, 可并行]
   ↓
Task 4 (M6.4 文档)  [2h, 串行]
```

**串行总工期**（无并行）：4+2+4+6+4+6+2 = **28h** ≈ 3.5 天
**并行总工期**（M6.2 / M6.3 拆 2 个 subagent）：4+2+max(4+6, 4+6)+2 = **18h** ≈ 2.5 天

---

## 端到端验证（每个 Task 后）

| Task | 端到端验证（无 SC2 / mock） | 真 SC2 e2e |
|---|---|---|
| 0 | 806+ 单测全绿 + 神族 smoke 跑通 | 玩家跑 1 局神族 vs VeryEasy |
| 1 | `test_game_config_my_race.py` + `test_sharpy_adapter.py` 全绿 | - |
| 2a | `test_zerg_aliases` + `test_zerg_strategies` 全绿 | - |
| 2b | `test_zerg_bot_smoke` + `test_zerg_bot_all_strategies_loaded` 全绿 | 玩家跑 1 局虫族 vs VeryEasy（各剧本至少 1 局） |
| 3a | terran 同上 | - |
| 3b | terran 同上 | 玩家跑 1 局人族 vs VeryEasy |
| 4 | mypy / ruff 干净 | 三族各跑 1 局 verify smoke |

---

## 风险监控

每个 Task 完成后必查的 4 项：

1. **mypy strict 不破**（vendor/sharpy 已 exclude）
2. **ruff format/check 干净**
3. **单测计数不退**（M6 前 806，M6 后 ≥ 870）
4. **pytest filterwarnings = ["error"]** 无新 warning

若发现 sharpy dummy 复用失败（如虫族 PlanZoneAttack2 与 unit_claim 冲突）→ 退到 "自写 BuildOrder 列表 + 复用 sharpy acts" 模式，参考 `protoss/plans/gate4_pressure.py`。
