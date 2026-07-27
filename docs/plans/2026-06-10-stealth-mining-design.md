# 偷矿（Stealth Mining）设计文档

> 2026-06-10。本文是偷矿功能的真理源（WHY + WHAT）。落地实施 plan 另出
> （`docs/plans/2026-06-10-stealth-mining-implementation-plan.md`，由 writing-plans 生成）。
> 前置实测见本文 §4（运营按钮核实：`set_expansion_override` 空操作已坐实）。

---

## 0. 目标与边界

**一句话**：玩家用语音/文字 + 当前镜头位置指定地图一片区域，bot 在那偷偷开一个隐蔽
基地，**自给自足造农民采矿**，发展成一个能接后续运营（造兵 / 起科技 / 转线）的独立经济
单元；且能**同时偷多片**。

**与"野建筑 / 代理建造"（proxy build）的本质区别**——这是整个设计的出发点：

| 维度 | 代理建造（已实现） | 偷矿（本设计） |
|---|---|---|
| 性质 | 一次性建 1-2 个建筑（前压 BE + 野 BG） | **持续运营的经济单元** |
| 农民 | 1 个农民造完即归还 bot | **常驻 N 个农民，本地自产、就地采矿** |
| 生命周期 | 建筑 settle 即结束 | 长期维持，受击逃散，可被摧毁/重建 |
| 与全局经济 | 无关 | **纳入"够不够农民"全局计算** |
| 与 bot 自动开矿 | 无关 | **必须防止 bot 自动开矿占了偷矿点 / 把偷矿点算进自然扩张** |

所以偷矿**不是**"代理建造多造一个 Nexus"，它的难点全在"建好之后怎么作为隔离经济单元
持续运营、又不暴露主力 / 不被 bot 全局逻辑搅乱"。代理建造只复用在**建造那一步**。

**非目标（YAGNI）**：本期不做偷矿基地的自动转兵营暴兵决策（玩家后续可手动下指令）、不做
偷矿点的自动选址（玩家指定）、不做偷矿之间的农民调度优化。

---

## 1. 核心模型：偷矿 cell = 隔离经济单元

```
   主基地群（bot 全局经济）                偷矿 cell #1（玩家指定点 A）
  ┌────────────────────────┐            ┌───────────────────────────┐
  │ Nexus × N（主+自然分矿） │            │ 隐蔽 Nexus（玩家点 A 附近）│
  │ 主矿农民 role=Gathering  │  ╳ FENCE  │ 本地农民 role=Reserved     │
  │ 全局 DistributeWorkers   │ ←──╳──→   │ 只采本地矿，不外流          │
  │ 管这些农民的分配          │           │ 主矿农民也不会倒灌进来      │
  └────────────────────────┘            └───────────────────────────┘
            ▲                                偷矿 cell #2（玩家点 B）…
            │                              ┌───────────────────────────┐
            └─ 全局农民"够不够"判断 ─────────┤ 独立 FENCE / 产线 / 逃散   │
               = 主矿 ideal vs 主矿农民       └───────────────────────────┘
               （stealth 农民单独算自己的 ideal，不混进主矿账）
```

**FENCE（围栏）是整个设计的承重墙**：把偷矿 cell 的农民和主基地经济在 sharpy 全局
`DistributeWorkers` 层面**双向隔离**。没有它，偷矿就会立刻暴露（见 §6）。

---

## 2. 四条规则（2026-06-08 / 06-09 用户拍板，按这套来）

1. **偷矿基地 = 一个正常基地**：计入 supply、计入全局农民总数、能造兵造建筑（后续运营接得上）。
2. **玩家指定地点**：语音/文字 + 当前镜头位置（复用 camera-as-target，见
   `docs/plans/2026-06-01-camera-target-voice-groups-design.md`）。不自动选址。
3. **农民就地自产**：stealth Nexus 自己造农民，**绝不从主矿抽调**。
   - 理由（关键）：若从主矿派农民过去，主矿农民数下降 → 全局 `DistributeWorkers` 触发
     重分配 / 主矿补农民 → 农民跨地图走动 → **暴露偷矿路径**。就地自产 = 零跨图调度。
4. **受攻击 → 农民就地逃散**：逃到偷矿点附近安全点，**不往主矿跑**（往主矿跑 = 给对方
   指出主力位置）。逃散用普通 `move` 不用 `attack_move`（CLAUDE.md 规则 4）。

---

## 3. 复用与新增（"能组合就不新增"评估）

CLAUDE.md 原则：复杂动作能拆成"现有 directive + activate_when"就拆，不轻易新增类型。
**逐项评估**：

| 子能力 | 能否复用现有 | 结论 |
|---|---|---|
| 建造 stealth Nexus | ✅ 代理建造链（`build_at(by_probe)` + claim probe） | **复用** |
| 农民 production block / saturation | ✅ `production_block` / `_tick_worker_saturation` 机制思路 | **复用思路**，per-cell 化 |
| 受击逃散移动 | ✅ 单位级 `move`（非 attack_move） | **复用** |
| 持续维持一个"有状态经济单元"<br>（补农民 / 受击逃散 / 纳入计数 / 多 cell） | ❌ 现有 verb / directive 无此**执行语义** | **新增**：`stealth_mine` directive + `StealthCellManager` |

**判定**：建造那一步纯复用；但"维持一个隔离经济单元的生命周期"是全新执行语义
（现有 act/verb 都覆盖不了"持续补农民 + FENCE + 受击逃散 + 多 cell 状态"），**需要一个
新 directive + 一个常驻管理器**。这符合 CLAUDE.md 的"只有需要全新执行语义才新增"。

---

## 4. 前置依赖（必须先修，否则偷矿逻辑打架）

### 4.1 `set_expansion_override` 真封顶 —— 实测已坐实失效

2026-06-10 实测（`scripts/macro_action_selftest.py`，iac_2base macro 局 vs VeryHard）：

- baseline 自然开到 **3 矿**（t=614s）。
- 注入"开矿封顶 2"（`macro_action expand=2`）的那局，**照样开到 3 矿**——封顶被完全无视。
- 根因：`common_bot.py:316` 的 `set_expansion_override` 是 `def ...: pass` 空操作；而
  `persistent_macro.py:134` 的 `for i in range(2, expansion_cap+1): Expand(i)` 照样自动开矿。

**为什么偷矿必须先修这个**（两条都成立）：

1. **防 bot 自动开矿占偷矿点**：macro doctrine 局 bot 会自动开到 5 矿。如果不能真正封住
   bot 的自然开矿，bot 可能自己开到玩家圈定的偷矿点附近 / 抢点 / 暴露。玩家需要"我说只
   要 N 个真分矿，bot 就别再自然开了"。
2. **stealth Nexus 不能污染自然开矿账**：stealth Nexus 是真 NEXUS，会被 `Expand(i)` 的
   `UnitExists(NEXUS, n)` 计数 → 一个偷矿基地会"顶替"一个自然分矿名额，让 bot 的自然
   扩张行为变得不可预测。

### 4.2 修法（本设计的 WP0）

```python
# common_bot.py：set_expansion_override 从空操作改成真写状态
def set_expansion_override(self, target_count: int | None) -> None:
    # None = 撤销玩家封顶，回到剧本默认
    self.knowledge.vibecraft.expansion_cap_override = target_count
```

```python
# persistent_macro.py：Expand(i) 循环尊重 override（封顶时不生成 i>cap 的 Expand）
# 注意：自然扩张账只数“非 stealth”的 NEXUS（stealth Nexus 由 StealthCellManager 登记，
#       Expand 的计数器要减去 stealth Nexus 数）
```

`_exec_expansion_override`（强制往上开，已验证生效）保持不动；本期只补"往下封顶"这半边。
配套：单测验证"真机基地数被封在 N"（不能只测 directive 提交——这正是漏掉本 bug 的原因），
+ 一条 `macro_action_selftest` 式真机回归（expand=2 后基地数 ≤ 2）。

---

## 5. 数据模型

### 5.1 Directive schema（新增 `stealth_mine`）

新增 kind 必须**三处同步**（CLAUDE.md 铁律）：models 判别联合 + 求值器 + LLM prompt。

```python
class StealthMinePayload(BaseModel):
    kind: Literal["stealth_mine"]
    point: tuple[float, float]          # 玩家指定点（镜头中心 / 小地图点）
    cell_id: int                        # 多片框区分；由 StealthCellManager 分配回填
    worker_target: int = 16             # 该 cell 目标农民数（1 矿 ~16，可调）
    with_gas: bool = True               # 是否同时偷气
    on_attack: Literal["flee", "hold"] = "flee"   # 受击行为（默认就地逃）
```

### 5.2 运行时状态（`StealthCell`，常驻内存，不进 directive）

```python
@dataclass
class StealthCell:
    cell_id: int
    point: Point2                       # 偷矿锚点（玩家指定）
    state: StealthState                 # 见 §6 状态机
    nexus_tag: int | None = None        # settle 后回填
    worker_tags: set[int] = field(default_factory=set)   # 本 cell 自产农民
    gas_tags: set[int] = field(default_factory=set)
    worker_target: int = 16
    on_attack: str = "flee"
    builder_tag: int | None = None      # 代理建造 claim 的那个农民（建完转本地）
```

`StealthCellManager` 持 `dict[cell_id, StealthCell]`，挂在 Director 上，每 tick 驱动所有 cell。

---

## 6. 状态机（每个 cell 独立跑）

```
                 玩家下 stealth_mine 指令
                         │
                         ▼
                  ┌─────────────┐  claim 1 农民 → 派去 point
                  │ PENDING     │
                  └──────┬──────┘
                  农民到点，下 Nexus（by_probe）
                         ▼
                  ┌─────────────┐  代理建造链：Nexus(+ 可选 BA 气矿)
                  │ BUILDING    │
                  └──────┬──────┘
                  Nexus settle（nexus_tag 回填）
                         ▼
              ┌──────────────────┐ ← FENCE 生效 + 本地产线开跑
              │ MINING           │ 每 tick：补农民到 target、就地采矿
              └────┬─────────┬───┘
            敌近   │         │ Nexus 被摧毁
                  ▼         ▼
            ┌───────────┐  ┌────────────┐
            │ RELEASED  │  │ DESTROYED  │
            └───────────┘  └────────────┘
   撤销 stealth 地位：Nexus 解除 FENCE + 农民 role 还 default + cell 出局。
   之后 bot 全局 DistributeWorkers 自动接管（敌方 zone 自带撤离逻辑，不手写 move）。
   受击即放弃该 cell，不回头；想重偷再下新指令。
```

`StealthState = PENDING | BUILDING | MINING | RELEASED | DESTROYED`

---

## 7. 农民模型（最承重，三个子问题）

### 7.1（a）本地自产

`MINING` 态每 tick：`if len(alive worker_tags) < worker_target` 且 stealth Nexus 空闲 →
`facade.train_probe_at(nexus_tag)`。新农民出生即：
- `set_unit_role(tag, Reserved)`（关键，见 7.2）
- 加入 `cell.worker_tags`
- `worker.gather(本地最近矿)` 就地采矿

**绝不** `bot.train(PROBE)`（那会在任意 Nexus 出，可能在主矿出→走过去暴露）。必须指定
stealth Nexus。

### 7.2（b）FENCE —— 双向隔离（设计承重墙）

实测确认的 `DistributeWorkers` 机制（`vendor/.../distribute_workers.py`）：

- `calculate_workers`：只统计 `role ∈ only_roles` 的农民进 `worker_dict[townhall.tag]`。
  Reserved **不在** only_roles → stealth 农民**不被统计** → 不会被全局重分配抽走。✅（外流防住）
- `generate_worker_queue`：每个 townhall `current_workers = len(worker_dict[tag])`，
  `available = ideal_harvesters - current_workers`。stealth Nexus 因为 worker_dict 为空 →
  `available = 16 - 0 = +16`（巨额缺口）→ 全局把**主矿农民抽来填这个缺口** → **倒灌**。❌

所以 **Reserved 只解决一半**（外流），倒灌没解决。FENCE 必须**两件事都做**：

```python
# vibecraft fence patch（distribute_workers.py，# vibecraft: marker + audit + docs/sharpy-patches.md）
# generate_worker_queue 内，遍历 townhall 时跳过 stealth Nexus：
stealth_tags = getattr(getattr(self.ai, "vibecraft", None), "stealth_townhall_tags", set())
if building.tag in stealth_tags:
    continue   # 偷矿基地不进全局工作队列：既不被填（防倒灌），其农民也不被调度
```

- **stealth 农民 role = Reserved** → `calculate_workers` 跳过 → 不外流。
- **stealth townhall（+ gas）从 `generate_worker_queue` 排除** → 不被主矿农民倒灌。
- 两者合起来 = 完全隔离。stealth cell 的农民**只由 StealthCellManager 自己管**（出生即派去
  本地矿，Reserved + 不在 queue → 全局永不碰它们）。

> 副作用红利：Reserved 农民天然不受"全军撤退/进攻"（`combat_intent_override` 只作用
> `free_units`，排除 Reserved）→ 玩家点全军命令不会误拉走偷矿农民。与控制权模型规则 2 一致。

### 7.3（c）纳入全局农民计数（不双重记账）

要求："偷矿农民纳入 bot 运营节奏计算"，但又"不从主矿引入"。两者要靠**账目分离**达成。

**候选方案**：

| 方案 | 做法 | 代价 |
|---|---|---|
| **① 账目分离（推荐）** | 主矿产线目标 = `Σ ideal(非 stealth townhall)` vs `supply_workers - Σ stealth 农民`；<br>cell 产线目标 = `ideal(stealth Nexus)` vs `len(cell.worker_tags)` | 要在 `_tick_worker_saturation` / ActUnit cap 里减去 stealth 部分；最干净，无双重记账 |
| ② 靠总农民 cap 自平衡 | 不改账，依赖 `ActUnit(PROBE,NEXUS,cap)` 用总农民数 + stealth Nexus 抬高 staged cap 自动少在主矿造 | 简单，但 stealth 农民会"顶掉"主矿产能（主矿可能欠饱和），且 cap staged on NEXUS count 被 stealth Nexus 干扰 |
| ③ 完全独立两套账 | 主矿完全无视 stealth；stealth 自己一套 | 违反"纳入全局计数"——bot 会以为农民不够继续在主矿狂造 |

**定稿 ①（2026-06-10）**：主矿只对"自己的 ideal"负责，stealth cell 只对"自己的 ideal"
负责，全局 `supply_workers` 自然 = 两者之和（满足"纳入计数"），但产能各管各、不跨图
（满足"不引入"）。**这不是"更优"，是被"§2 规则 3 就地自产"逼出来的唯一自洽解**：既然 cell
自己造自己的农民，方案② 的全局 cap 必然把同一批缺口在主矿再造一遍 → 双重生产、主矿膨胀。
实现：给 `_tick_worker_saturation` 和 PersistentMacro 的 PROBE cap 传入"排除 stealth"的
townhall 集合 + 农民集合（stealth_townhall_tags / stealth_worker_tags 由 Manager 维护）。

---

## 8. 受攻击 → 取消 stealth 地位、整体交还 bot（2026-06-10 用户定稿）

**核心**：偷矿点被攻击 → **直接撤销该 cell 的 stealth 地位，把整个基地（Nexus + 农民）原样
交还 bot 接管**，之后一切由 bot 既有逻辑处理。**我们不手动编排"农民往哪逃"**——bot 自己会
处理（见下"为什么不用手写逃散"）。

- **检测**：`MINING` 态每 tick 查 stealth Nexus 半径 R 内是否有敌方非农民单位（
  `enemy_units.exclude_type(workers)`）。有 → 进 `RELEASED`（一步到位，无中间态）。
- **撤销 stealth 地位（= RELEASED 要做的三件事）**：
  1. **解除 FENCE**：该 Nexus 从 `stealth_townhall_tags` 移除 → 重新进入全局
     `DistributeWorkers` 的 work_queue（不再被排除）。
  2. **农民还 role**：`cell.worker_tags` 全部 `set_unit_role(tag, default)`（Reserved → 默认）
     → 进入 bot 全局管理；**连带撤销农民身上的 in-flight 指令**（控制权模型规则 3）。
  3. **cell 出局**：从 StealthCellManager 移除，结束生命周期。
- **之后完全由 bot 接管**：bot 把这个 Nexus 当普通基地，农民当普通农民。
- **为什么不用手写"逃去某矿区"**（关键，比原方案简洁）：sharpy `DistributeWorkers` 本就带
  **危险/敌方 zone 自动撤离**逻辑——`generate_worker_queue` 里 `zone.is_enemys` →
  `available = -current*10000`（强力驱赶），`zone.needs_evacuation` → 负值撤离。所以一旦解除
  FENCE、农民还 default，bot 的全局调度**自动**把这个被攻击矿区的农民撤到别处安全矿区采矿，
  无需我们 scripted move。"交还 bot"四个字就够了，逃散是 bot 的既有能力。
- **不回头**：`RELEASED` 后该 cell 不复活。玩家想重偷该点 → 再下一条新 `stealth_mine`。
- `on_attack="hold"` 保留字段（玩家明确要硬守时不撤销 stealth），本期默认走 `flee`=上述撤销流程。

---

## 9. 多 cell 并行（用户明确要求）

- `StealthCellManager` 维护多 cell；`cell_id` 自增分配。
- 每 cell **独立** FENCE / 产线 / 逃散；`stealth_townhall_tags` / `stealth_worker_tags`
  是所有 cell 的并集（喂给 fence patch 和账目分离）。
- 一个农民只属一个 cell（`worker_tags` 不重叠）；建造 claim 的 builder 建完转本地农民。
- **UI**：snapshot 带 `stealth_cells: [{cell_id, location, worker_count, state, has_gas}]`
  → PWA 显示"偷矿点 1：12 农民 采矿中 / 偷矿点 2：建造中"。中文标签走 snapshot→手机
  （游戏内 debug draw 不渲染中文，CLAUDE.md；地图上只画 ASCII cell 号 + 框）。

---

## 10. 接线（facade / sharpy / director）

### 10.1 Facade 新方法（**FakeFacade + _SharpyFacadeBase 双实现，CLAUDE.md 铁律**）

```python
def train_probe_at(self, nexus_tag: int) -> bool: ...        # 指定 Nexus 造农民
def set_unit_role(self, tag: int, role: str) -> None: ...     # 已有 set/release role，确认覆盖
def register_stealth_townhalls(self, tags: set[int]) -> None: # 写 ai.vibecraft.stealth_townhall_tags
```

改 facade 后跑 `tests/unit/test_facade_release_unit_role.py` 的 Protocol 一致性 audit。

### 10.2 sharpy vendor patch（方案 D，`# vibecraft:` marker）

- `distribute_workers.py::generate_worker_queue`：加 stealth townhall 排除（§7.2）。
- 进 `tests/unit/test_sharpy_patch_audit.py::PATCHED_METHODS` + `docs/sharpy-patches.md` + 行为单测。

### 10.3 Director

- `StealthCellManager` 挂 Director；`apply` 时把 `stealth_mine` directive 转成新 cell（PENDING）。
- `on_tick` 驱动所有 cell 状态机；维护 `stealth_townhall_tags`/`stealth_worker_tags` 并喂给
  fence + 账目分离 + `set_expansion_override` 的"自然开矿排除 stealth"。

---

## 11. 日志 / observability（first-class）

每个 cell 的状态变迁、产线（train_probe_at）、FENCE 注册、逃散触发、Nexus 摧毁，全部落
`logs/<game_id>/events.jsonl`（带 cell_id / nexus_tag / worker_count / state）。真机自验靠
greppable 前缀（如 `STEALTHTRACE`）。

---

## 12. 测试策略

- **单测**：状态机迁移；FENCE（mock DistributeWorkers，断言 stealth townhall 不进 work_queue +
  主矿农民不被抽）；本地产线（< target 时在指定 Nexus train，role=Reserved，入 worker_tags）；
  账目分离（主矿 ideal 不含 stealth）；受击逃散（move 非 attack_move）；多 cell 不串台。
- **真机自验**（`scripts/stealth_mine_selftest.py`，proxy_chain_selftest 式，mock LLM +
  non-realtime 并行）：注入"去 X 偷矿" → grep 验：Nexus 在 X settle + 本地农民自产到 target +
  **主矿农民数不掉（无倒灌）** + 模拟敌近时农民 move 逃 + 多 cell 各自独立。
- **WP0 回归**：`macro_action_selftest` 加 expand 封顶 case（expand=2 后基地 ≤ 2）。

---

## 13. 实施工作包（给 writing-plans 拆 5-step；此处只列依赖图）

```
WP0  修 set_expansion_override 真封顶（前置，独立可先合）
      └─ persistent_macro Expand 尊重 cap + 自然开矿排除 stealth + 单测 + 真机回归
WP1  schema（stealth_mine 三处同步）+ StealthCell/Manager 骨架 + 状态机    ← 依赖 WP0
WP2  建造链：复用代理建造起 stealth Nexus（claim→build_at by_probe→settle 回填）  ← 依赖 WP1
WP3  FENCE：distribute_workers vendor patch + facade register_stealth_townhalls  ← 依赖 WP1
WP4  本地产线 + 账目分离（train_probe_at + 主矿 ideal 排除 stealth）          ← 依赖 WP2,WP3
WP5  受击逃散 + Nexus 摧毁释放（move 非 attack_move + 撤销在身指令）          ← 依赖 WP2
WP6  多 cell 并行 + snapshot stealth_cells + PWA 显示                       ← 依赖 WP4,WP5
WP7  LLM prompt（few_shot：去X偷矿 / 偷两个点）+ 真机自验 harness            ← 依赖 WP6
```

---

## 14. 子决策（2026-06-10 全部定稿）

1. **§7.3 全局农民计数** → **方案① 账目分离**（被"就地自产"逼出的唯一自洽解，见 §7.3）。
2. **§8 受击逃散** → **逃去自己最近的其他矿区 + role 还 default 交还 bot**；受击即放弃该
   cell，不回头（见 §8 已改）。不做玩家指定逃散点。
3. **气矿** → `with_gas` 默认 **True**（偷矿同时偷气；无气点自动跳过）。
4. **worker_target 默认 16** → **不设全局上限**，每 cell 各自 target，信任玩家掌握偷几片。
