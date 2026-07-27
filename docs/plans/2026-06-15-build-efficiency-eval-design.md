# 开局 Build 持续运营效率评价 + 进化迭代系统 — 设计文档

> 2026-06-15 brainstorming。用户需求：给三族每个开局 build 的"持续运营效率"打分、定位问题、
> 用类遗传算法不停迭代改进，每个 build 长期维持 2-3 个变体滚动淘汰。

## 0. 目标与核心假设

**核心假设**：一个 build 哪怕玩家一直不切，它也应当一直保持高效运营 —— 钱不囤、产能不空、不卡人口。

**目标**：建一套自验工具链，能①给任一 build 的运营效率打分（三维度+合成总分），②定位它在哪个
时间段、哪个维度掉链子，③驱动一个"诊断→脑洞→变体→对比→保留/淘汰"的进化循环，长期每 build 维持
2-3 个变体迭代。

**用户拍板的关键决策（2026-06-15）**：
- 测试场景 = **纯运营沙盒**（对手最小干扰/不进攻，bot 专注 macro 跑满 ~10min，隔离经济逻辑）。
- 节奏 = **先交基线**（Phase 0-2），用户确认指标合理后再启动 Phase 3 进化循环。
- 打分 = **三维度分开展示 + 可调权重合成总分**。

## 1. 三个评价维度（指标定义）

数据源：`logs/<match>/telemetry.jsonl` 每帧 snapshot（游戏内约每 1s 一帧），字段 `t / minerals /
vespene / supply_used / supply_cap / units / buildings / economy{idle_workers, saturation,
mineral_workers, gas_workers, gas_ideal, mineral_ideal} / workers / bases`。

记快照序列 s₀..s_N，游戏时刻 tᵢ，间隔 dtᵢ=t_{i+1}−tᵢ。评测窗口 **[T_start, T_end]**，默认
T_start=0、T_end=600s（沙盒跑 10min）。后续可改成"从 opening_completed 起"专测 sustain。

### M1 — 余钱效率（bank penalty，越少越好）

囤钱 = 该补的产能没补、没在持续出兵。设矿/气各有一个"合理周转底"（要留点钱付下个建筑）：
`M_floor`（默认 300）、`G_floor`（默认 150）。

```
excess(sᵢ) = max(0, minerals−M_floor) + max(0, vespene−G_floor)
bank_integral = Σ excess(sᵢ)·dtᵢ        # 单位 resource·秒
avg_excess    = bank_integral / (T_end−T_start)
bank_score    = 100 · clamp(1 − avg_excess / REF_BANK, 0, 1)   # REF_BANK 默认 1000
```

用户原话"余 500、每秒扣 X 分"= 这个积分。REF_BANK 是"平均囤这么多就扣到 0 分"的参考刻度。

### M2 — 产能建筑效率（production utilization，越高越好）

**telemetry 当前没有此数据，Phase 0 需补埋点**。每帧记每类产能建筑 busy/idle：
- 折跃门 WarpGate：**冷却中 = busy（刚 warp，发挥了作用）**；冷却好了却空着 = idle（浪费）。
- 普通产能建筑（Gateway 未升级/Robo/Stargate/兵营/工厂/星港）：有活跃训练订单 = busy，否则 idle。
- 虫族特殊：larva 机制。busy ≈ larva 被消耗（在孵）、idle ≈ larva 堆积未用 + 闲置产能；
  另记 macro hatch / 注卵利用率。Zerg 的 util 单独定义（见 §5 风险）。

```
util(sᵢ) = busy_prod_count(sᵢ) / total_prod_count(sᵢ)   # total=0 时该帧跳过
prod_util = Σ util(sᵢ)·dtᵢ / Σ dtᵢ     # 仅累加 total>0 的帧
prod_score = 100 · prod_util
```

埋点新增 snapshot 字段（提案）：
```
"production": {
  "warpgate":   {"total": 8, "busy": 6},   # busy=冷却中
  "gateway":    {"total": 0, "busy": 0},   # 未升级折跃门的 BG
  "robo":       {"total": 1, "busy": 1},
  "stargate":   {"total": 3, "busy": 2},
  ...按种族产能类型
  "util": 0.78                              # 本帧加权利用率（可后端预算，也可打分器现算）
}
```

### M3 — 卡人口惩罚（supply-block penalty，越少越好）

有资源、有产能、本可招兵，却因卡人口造不出 = 纯浪费时间。

```
blocked(sᵢ) = 1 if (supply_used ≥ supply_cap) and (supply_cap < 200)
                   and (minerals ≥ M_can_build)        # 有钱本可出兵，默认 M_can_build=100
              else 0
supply_block_time = Σ blocked(sᵢ)·dtᵢ                  # 秒
supply_score = 100 · clamp(1 − supply_block_time / REF_BLOCK, 0, 1)   # REF_BLOCK 默认 60s
```

### 合成总分

```
total = w1·bank_score + w2·prod_score + w3·supply_score
默认权重 w1=0.35, w2=0.40, w3=0.25（和=1，可调）
```

三个子分 + 总分都输出。打分器同时产出**诊断时间线**：首次持续囤钱的时刻、产能利用率低谷区间、
卡人口区间、最差维度 + 最差时间窗。

## 2. 纯运营沙盒（测试场景）

要求：build 自己跑满 ~10min、不因战斗损耗扭曲经济指标。

方案：bot vs **VeryEasy Computer**（或更被动设置），且**强制 bot 进入纯 macro 模式（不主动出兵）**，
全程只补农/补产能/扩张/原地防守，不 moveout 送兵。这样：
- 没有自杀式 moveout → 不丢兵 → 不触发"重建兵 + 重新分配农民"的噪声。
- 经济/产能/人口指标纯粹反映 build 自身的运营逻辑（OpeningSustainAct、产能节奏、扩张时机）。

Phase 0 调研落地手段（候选，二选一）：
- (a) 复用现有"全军防守/不进攻"路径：测试 harness 给子进程 Director 注入一条持久 defend/hold，
  让 bot 不出门。
- (b) build_acceptance 加一个 `--sandbox` 旗：跳过 moveout 决策（强制 macro-only）。

非 realtime + 并行多开（用户机实测同时 4-8 局没问题），跑全量基线快。

## 3. 工具链（Phase 0-2）

### Phase 0 — 产能利用率埋点
- 在 telemetry snapshot 生成处（`src/vibecraft/bot/telemetry.py`）加 `production` 字段：
  遍历产能建筑读 busy/idle（折跃门读 cooldown，普通建筑读 orders，虫族读 larva/注卵）。
- 三族各自的产能类型映射表。
- 单测：构造 fake 建筑状态 → 断言 util 计算正确（含折跃门冷却、虫族 larva）。

### Phase 1 — 打分器 `scripts/build_efficiency.py`
- 输入：一个 match 的 telemetry.jsonl（或一组，多 seed 取均值）。
- 输出：三子分 + 总分 + 诊断时间线（JSON + 人读摘要）。
- 参数（floor/REF/权重/窗口）集中在一个 config dataclass，便于调。
- 先在 2-3 个 build 上验证：肉眼直觉差的（如囤钱那个）应在对应维度拿低分。

### Phase 2 — 每个 build 各自基线（**不跨 build 排名**）

**纠正（2026-06-15 用户抓到的矛盾）**：之前写"全量排行榜 + 挑最弱 build 去迭代"是**错的**，
跟"分数不能跨 build/跨族比"自相矛盾（util 语义不同，横向排名不成立）。**删掉排行榜/挑最弱**。

- runner 沙盒跑三族所有 build，每 build 跑 N seed。产出的是**每个 build 自己的基线分**
  （三子分 + 总分 + 诊断时间线），**仅作该 build 自己进化迭代的起点参照**，绝不互相排名。
- 比较**永远只在同一个 build 的变体之间**（intra-build，同指标语义一致 → 可比）。
- 0-100 归一化只为人读，**决策不用它**——决策用同 build 变体间的**配对原始指标**比较（见 Phase 3）。

## 4. 进化循环（Phase 3）：每个 build 一条独立进化线，全做、不挑

**对当前每一个 build**（不挑最弱，全做），各跑一条独立的"种群=3 变体"遗传算法：

```
种群 = 该 build 的当前最强 3 个变体（初代 = 原版 ×1，逐步繁殖到 3）。
每轮：
  1. 诊断：打分器给每个变体的最差维度 + 最差时间窗（如"6:00 后矿囤 2000、产能利用率 0.5"）。
  2. 脑洞（独立 subagent，随机性）：喂"诊断 + 变体 plan/YAML + 主力兵种"，提随机改进点子
     （调产能 cap / 补给节奏 / 主力兵种产量 / 时机；**不增删兵种类型**）。
  3. 变异落地：在当前 3 个种子基础上各随机变异出后代变体（strategies/<race>/<id>__vN.yaml +
     调 plan/sustain 参数）。**静态 roster 校验**：后代兵种集合必须 == 原版，否则当场弃。
  4. 测：所有变体（种子 + 后代）**同一组 seed 配对**跑沙盒、打分。
  5. 选择：本轮所有变体里，按"同 build 配对 A/B"留**最强的 3 个**当下一代种子；其余淘汰。
     （+ 准入闸：build_acceptance 胜负/timing 回归不退步、兵种 roster 不变。）
  6. 留痕：记进方法论日志（诊断/变异/A/B/保留淘汰/学到的通用规律）。
收敛：连续三轮"最强 3 个种子没被任何后代超过" → 该 build 封板，转下一个 build。
```

**配对 A/B 判优**（同 build 两变体）：跑同一组 N seed（默认 5），变体 A 在 ≥⌈N·0.8⌉ 个 seed 上
总分胜 B → A 更强。同 seed 下两者面对相同地图/出生/AI，运气消掉，差异≈纯 build 改动。

变体存储（先手工验闭环，证伪后再自动化，见 §7-K）：
- strategy 变体 YAML 放 **`strategies/<race>/<base_id>__v<N>.yaml`**（不开独立 variants 目录，
  否则 `_detect_race`/race 推断找不到，见 §7-H），走现有 `StrategyLibrary`。
- 谱系/分数：`logs/build_efficiency/<base_id>.json` 记每个 build 自己的种群历史
  {variant_id, parent, 三子分+总, diff_summary, kept/discarded, round}。

进化是多 session 长任务，逐个 build 推进，每个 build 给用户可见进度。

## 5. 风险与未决（交 Opus 评审重点看）

1. **虫族产能利用率定义**：larva 机制和神/人完全不同，util 公式要单独设计，否则三族不可比。
   是否需要三族各自的 REF 刻度？还是 util 本身已归一化到 0-1 可比？
2. **沙盒"不进攻"会不会扭曲 build 本意**：有些 build 的运营节奏依赖打出去（打了才扩/才转科技）。
   纯 macro 沙盒下它可能"测得很好但实战拉胯"。是否要 sandbox + vs-VeryEasy 两套都测、交叉看？
3. **指标 vs 实战脱节**：高运营分 ≠ 会赢。本系统只优化"运营效率"，不优化"打赢"。要不要给每个变体
   同时留一个 build_acceptance 的胜负/timing 回归，防止"为了运营分牺牲战斗力"？
4. **打分对噪声的鲁棒性**：单局 RNG（农民被打、建筑摆位失败）会不会让分数抖动大到掩盖真实改进？
   N seed 取中位够不够？阈值怎么定才不会把噪声当改进收下。
5. **过度设计**：进化框架是不是太重？能不能先手工迭代几个代表性 build 验证"诊断→改→测"闭环有效，
   再决定要不要把 scoreboard/变体注册做成完整框架。
6. **评测窗口**：全程 0-600s vs 仅 opening_completed 之后。前者含开局 ramp（天然低 util），
   后者纯测 sustain 但要先可靠检测 opening_completed（多人局曾恒 null，见 #526 教训）。

## 7. 评审后修订（2026-06-15 独立 Opus 评审，逐条采纳）

**A.〔2026-06-15 用户推翻评审,改回来〕评价目标对所有 build 通用,含 all-in。** 评审原建议"排除
all-in"被否决,理由:all-in 出兵不及时就打不出压制 —— **及时出兵本身就是 all-in 的成败标准**,测它的
产能/出兵/人口效率恰恰是在测它执行得好不好。

**统一改进目标(north-star)**:玩家选了**任何** build、基本不操作,它也该自动"该补人口补人口、该补产能
补产能、该出兵出兵,钱花得比较干净"。**唯一可选项 = 开矿**(两矿快攻/all-in 不强求主动扩张);但
**产能和出兵要尽量自动拉满**。最看重的就是 **M1 余钱低**(钱花干净=兵出得多)。

**沙盒角色重定位**:forced-defend 不再是"哪些 build 够资格评测"的闸,只是个**降噪手段**——对能压住
moveout 的纯运营 build,它隔离掉战斗损耗;对 all-in / 自定义派兵 plan(它们直接 `unit.attack`,压不住),
**就让它自然打**,在它自然执行的窗口里测三维度。所以:
- `GameConfig.sandbox_macro_only`(forced-defend)仍做,但是**可选/按 build 档位**,不是全局前提。
- **all-in 局短**(自然打完/被打完游戏就结束)→ 评测窗口取 **min(600s, 游戏结束)**,测的正好是它活跃窗口。
- 落地走第三条路:`GameConfig.sandbox_macro_only` → 子进程 `on_start` 一次性
  `facade.set_combat_intent_override("defend") + set_engagement_stance("defend")`(确定性、t=0 生效,
  不动 N 个 plan)。纯运营 build 开它降噪;all-in build 关它、自然打。

**改进的核心杠杆 = 参考 build 主力兵种补产能**(2026-06-15 用户)。每个 build 有它的主力兵种(现在写在
strategy YAML 的 `summary_zh` / `sharpy_dummy_class` 指向的 plan 里,**没有结构化字段**)。诊断到"囤钱 +
产能空"时,修法 = **认出该 build 主力兵种 → 补对应产能建筑(不朽流补 Robo 产能、追猎流补 BG、虚空流补 VS)
+ 让 opening/sustain 跟着收入自动拉满该兵种的产量 + 补人口**,而不是去开矿。Phase 2/3 落地时可考虑给
strategy YAML 加一个结构化 `core_units: [...]` 字段(供诊断/修复引用),或从 plan 推断。这条对 opening
(all-in 的 opening 就是它全部)和 sustain(运营 build 的后劲)都适用。

**B. 加 `random_seed` 穿透 `GameConfig` → python-sc2 `run_game(random_seed=)`**（现在完全没有）。
这是进化循环地基：变体 A/B **同 seed 配对跑**消掉地图/spawn/AI 变量。基线 N=5 报 median+IQR；
变体接受判据 = **配对同 seed 下"变体 ≥4/5 seed 胜"**（抗噪，免拍"显著阈值"魔数）。

**C. `opening_completed` 落 telemetry 事件**。澄清：它走 `EmitOpeningCompleteAct`→
`director.notify_opening_completed`（`director.py:4568`），是普通方法调用，**跟 #526 的 plan-tree-walk
无关，单人局可靠**。但目前只进 director 内存、不写盘。Phase 0 在 `notify_opening_completed` 里
`write_event({"kind":"opening_completed","t":now})`，打分器才能据此切窗。`completion_check` 是状态谓词，
forced-defend 下照样 fire。

**D. 决策用原始积分配对比，0-100 只是人读装饰**。打分器输出**原始积分**（bank_integral/
supply_block_time/larva_idle/prod_util）。**进化决策 = 同 build 变体间配对原始指标比较**（同 seed 下
谁的 bank_integral 更低/prod_util 更高），不需要 REF 归一。0-100 总分仅供人读概览，REF 可用全 build 分布
P90 拍一个**装饰刻度**，但**不进任何保留/淘汰判据**（避免重新引入跨 build 可比性的错误假设）。废弃手填
`REF_BANK=1000` 等魔数。

**E. M1 复用 verifier 已有刻度**。floor 起点用 `verifier.py:25 _ECONOMY_FLOOR{minerals:200, vespene:120}`，
不另立 `M_floor=300/G_floor=150` 两套不一致常量。

**F. M3 滤短 block**。`supply_cap` 只算已建成 supply，JIT 补人口天然有 1-2 帧 used≥cap（补给在飞），
且 bot 结构性有 `supply_block_recovery_s:5`。M3 只计**持续 ≥4s（≥2 快照）**的 block，滤掉单/双帧健康重叠。

**G. 虫族 M2 改"larva 闲置积分"**（跟 M1 同构）：`larva_idle_integral = Σ max(0, larva−floor)·dtᵢ`
→ clamp(1−avg/REF_LARVA)，叠注卵覆盖率。**不跨族横向比分数**（util 语义不同），只同 build 跨变体纵向比。

**H. 变体留在 `strategies/<race>/<id>__vN.yaml`**，不开独立 `variants/` 目录。否则
`StrategyLibrary.from_directories`（library.py:88 按父目录名推断种族）+ `build_acceptance._detect_race`
（build_acceptance.py:54 只扫 `strategies/<race>/`）都找不到 → 变体用错种族跑全废。

**I. 每个变体硬挂 `build_acceptance` 胜负/timing 回归作准入闸**：运营分涨但 build_acceptance 退步 → 否决
（对齐"指标变差要调优不要放宽 spec"纪律）。

**J. runner 复用 build_acceptance**（像 `override_acceptance.py:35` 那样 import，不复制起局/并行/_detect_race/
_load_telemetry）。**但要补 game-time 早停**：build_acceptance 不按游戏时间早停，forced-defend 打不死
对手会跑满 wall-timeout → 照搬 `override_acceptance._compute_stop_game_time` + snapshot t≥T_end 提前 stop
（override_acceptance.py:142-163），T_end=600 game-time 收手。

**K. Phase 3 进化框架先砍到"手工验闭环"**。Phase 2 出基线后手工挑 2-3 个**代表性** build 走一遍
"读诊断→改 yaml/sustain→配对重测"，确认分数真能被推动 + 不破坏 build_acceptance 回归后，**再**做
scoreboard.json/变体自动注册/subagent 编排。先删 Phase 4 谱系存储和 subagent 自动循环。

**L. 工程细节**：snapshot 间隔实为 **2s**（`telemetry._SNAPSHOT_INTERVAL_S=2.0`，§1 文档笔误已知）；
dt 积分最后一帧无 dt 要丢弃不进分母；M2 折跃门 busy 读 **ability cooldown** 非 orders，埋点单测覆盖
"折跃门冷却=busy/好了空着=idle/未升级 BG 读 orders"三态。

**M. 承认 M1⊥M2 不正交**：浮矿(M1) 和折跃门空闲(M2 idle) 常同根因（钱够没出兵），加权和会双重惩罚同一
问题——排序仍单调、不致命，但绝对分偏低，文档明示不当正交维度解读。

**N（可选第 4 维度，数据现成）**：`economy.idle_workers` + `base_saturation` 过饱和 = 农民过剩浪费，
telemetry 已有，可作低成本的第 4 维度，本期先不纳入合成总分，仅诊断时参考。

## 7b. 硬约束 + 收敛条件（2026-06-15 用户 /goal）

**硬约束（优化全程不可违反）：不允许引入新兵种类型，也不允许删除兵种类型。** 变体只能调
数量 / 时机 / 产能建筑数 / 补给节奏 / 出兵比例 / cap，**兵种 roster 必须和原版完全一致**。
"补"= 补现有主力兵种的产量，不是加新兵种；也不能砍掉某个原有兵种。
- **准入闸 = 静态代码级校验，不需实测（2026-06-15 用户）**：一个 build 训练哪些兵种，**读它的
  plan 代码 + YAML 就看得出来**，不必跑游戏看 telemetry。做法：写个静态提取器，给定 strategy_id →
  扫它的 `sharpy_dummy_class` plan 代码（train/warp/BuildOrder 里的 `UnitTypeId.*`）+ YAML `steps`，
  得到"兵种 roster 集合"；可一次性固化成每 build 的 `unit_roster` manifest。变体改完做**静态 diff**，
  roster 增/删任一兵种 → **在跑 A/B 之前直接否决**，不浪费游戏。比 telemetry 事后校验更省、确定、提前。
- 改 plan/YAML 时人/subagent 也守此约束（diff 评审时检查没动兵种 roster，只动量/时机/产能/补给）。
- 理由：保持每个 build 的"兵种身份/战术性格"不变，只优化运营效率，避免把 A build 改成 B build。

**收敛条件：每个 build 迭代到「连续三轮都没有任何改进」为止**（3 轮 no-improvement → 该 build 收敛、
封板）。对当前所有 build 逐个做到收敛。"一轮"= 一次完整 M3-M7（诊断→脑洞→变体→A/B→决策）。
"无改进"= 该轮所有变体在配对 A/B 下都没赢过当前基准（按 M7 判据）。

## 6. 不做（YAGNI）
- 不做实时在线进化（玩家对局中改 build）。纯离线自验迭代。
- 不做跨 build 的自动"杂交"（取两个 build 的优点合并）—— 先单 build 内变体迭代。
- 不在本阶段碰 midgame/lategame doctrine 的效率评价 —— 先只搞 opening build 的持续运营。
