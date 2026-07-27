# Build 优化通用方法论 · 全程留痕日志

> **元目标（2026-06-15 用户）**：本次不只是优化现有 build，更是借这次尝试**摸索 + 沉淀一套通用、
> 可复用、可自动化的"优化各种 build"的方法**。SC2 新版本升级后，能自动套用这套方法重新优化所有 build。
> 因此**流程本身**是第一交付物——每一步的指标、诊断、脑洞、变体、A/B、保留/淘汰决策，全部留痕在此。
>
> 配套设计文档（指标定义/沙盒/工具链）：`2026-06-15-build-efficiency-eval-design.md`。
> 本文件 = 活文档，随迭代不断追加。
>
> **★ 蒸馏版 Runbook（重跑就看这个）**：`docs/build-optimization-runbook.md` —— 本日志是逐条
> 时间序 findings，runbook 是可复用方法论（patch 后全量重跑的 checklist + 三大教训 + harness 陷阱
> + 上轮基线锚）。**下次 SC2 patch（版本更新）后大概率重跑一轮，照 runbook 做。**

---

## ★ 锁定的评价方案 + 执行计划（2026-06-15 全部对齐，按此执行）

### 评分 = 多维向量，不合成单一总分

每个 build 一局沙盒（forced-defend + 固定 seed，non-realtime）→ 算下列维度。**只在同一 build 的变体间纵向比，绝不跨 build/族横向。**

**三族共用两维**：
- **M1 余钱**：`Σ(矿+气)·dt`，**仅在成长期（supply_used<180）且有人口余量（used<cap）时累计**。无 floor。
  报告**矿/气分开**（矿囤→补矿产线、气囤→补气产线）。≥180=banking/买活阶段不罚。
- **M3 卡人口**：`used≥cap 且 cap<200 且 矿≥100` 的**连续 ≥4s 段**总时长。和 M1 逐帧互斥不双罚。

**各族产能维（执行机制不同，分别对待）**：
- **人族（最简，先做）**：产能 util = 兵营/工厂/星港 busy 占比（busy=有训练订单；**反应堆要按容量2算**，否则低估）。
- **虫族（要新埋点）**：`产能分 = 助卵覆盖率(inject_coverage) × 卵消耗率(spend_efficiency)` 的**乘积**
  （生成闸×消耗闸，乘积自动解耦：助卵不足→卵少没闲卵→看着好但乘积低，正确罚）。inject 需埋点
  （女王数 vs hatch / 女王能量 banking / hatch 注卵 uptime）；spend≈1−虫卵闲置（gate 成长期+有钱）。
- **神族（已手调好，放最后审）**：折跃门 util（busy=冷却中=刚 warp）+ **ready-idle 执行信号**
  （门 ready 却没 warp = 执行漏产能）。审计 warp 逻辑/chrono 是否打满。

### 三层诊断（util/spend 低时定位到层，决定 Tier1 改代码还是 Tier2 调参）
1. 没在下生产指令 → **结构层**：sustain 没接管 → 改 sustain 按 core_units 持续出兵。
2. 在下指令但产线 ready 却空 → **执行层**：修产能执行逻辑（warp/注卵/反应堆不优）。
3. 产线都满产仍囤钱 → **参数层**：产能不足 → Tier2 GA 按 income-matched 加楼（util 高才加；
   量=产能消耗≥采矿收入；注意卡矿还是卡气；精确数由"囤钱→0 且 util 不掉"推到平衡）。

### 变体接受规则（不用加权总分，避免被钻空子）
同 N seed **配对**比，变体 A vs 当前最优 B：
1. **硬闸**：兵种 roster 不变（静态 diff）+ build_acceptance 胜负/timing 不退步。
2. **修当前瓶颈维**：D=本轮最差维。A 胜当：**A 在 D 上 ≥⌈N·0.8⌉ seed 更好，且其余每维都不在 ≥⌈N·0.8⌉ seed 更差**。
3. **每轮重诊断瓶颈**（修好一维 → 新瓶颈浮现 → 自动转攻）→ 打地鼠式朝多维平衡收敛。
4. **阈值不拍脑袋**：用**配对胜场**（免百分比）；开工每 build 先**实测噪声带**（基线 N seed 的自然波动），
   任何幅度容差设在噪声带之上；噪声带+用的容差/tiebreak 权重**全留痕**。
5. 罕见非支配对（互有胜负）才用 tiebreak：当轮各变体每维 min-max 归一 + 一组权重（默认偏余钱），
   实践调 + 记日志。

### 收敛 + 顺序 + 通知
- **收敛**：连续三轮无变体能在瓶颈维赢过当前最优 → 该 build 封板。
- **顺序**：**人族（正常出兵，最好判断，先把流水线跑顺）→ 虫族（助卵+卵，要新埋点）→ 神族（已手调好，审计）**。
  每族内一个 build 一个 build 串行，做完一个下一个。
- **通知（用户要求）**：**每个种族整族迭代完 → 通知用户一次**（优化了哪些 + 优化前后对比），
  通知时 cloud 手机端**推送提醒**；**通知完不停**，立即接着做下一个种族。

---

## ★★ 人族配比锁定（2026-06-15 用户逐 build 确认）

**通用原则**：除 `marine_rush`（纯枪兵 all-in）外，所有 build 核心 = **枪兵 mass + 医疗艇配合
（医疗艇:枪兵 = 1:8，一个光头按 2 枪兵算）**。**光头（掠夺者）默认玩家控制**（看对面情况、玩家指令
决定加几个）；唯 bio 身份的 build 核心带光头 **4:1**。坦克在 bio 里 `cap 3`（玩家可调）。
**SC2 mech 正确组成** = 火蝠/恶火(Hellbat) + 雷神(Thor) + 少量坦克 + 少量维京/解放者（注：雷车是 SC1）。
**骚扰控场 build（banshee_harass / hellion_expand）必须结合开矿扩张 + 转 bio 或 mech**，非独立终点。

| build | 配比（mass/cap N/1:N/situational） | 光头 |
|---|---|---|
| marine_rush | MARINE mass | 无 |
| banshee_harass | BANSHEE cap6→trickle · MARINE mass · SIEGETANK cap3 · MEDIVAC 1:8；**必扩张转 bio/mech** | 玩家定 |
| one_one_one | MARINE mass · SIEGETANK cap4 · BANSHEE cap3 · MEDIVAC 1:8 | 玩家定 |
| two_one_one | MARINE mass · 光头 4:1 · MEDIVAC 1:8 · SIEGETANK cap3 | 核心 4:1 |
| bio_stim | MARINE mass · 光头 4:1 · MEDIVAC 1:8 · SIEGETANK cap3 | 核心 4:1 |
| two_base_tanks | SIEGETANK 主体 cap8→续 · MARINE 少量 · VIKING 少量 · **无医疗艇/无光头** · 晚出门 · 后期转 mech | 无 |
| hellion_expand | HELLION cap6→trickle · MARINE mass · MEDIVAC 1:8 · **必扩张** · 后期 mech 或 bio(默认 bio) | 玩家定 |
| reaper_expand | REAPER cap3 → 转 bio：MARINE mass · 光头 4:1 · MEDIVAC 1:8 | 核心 4:1 |
| widow_mine_drop | MARINE mass · WIDOWMINE cap5 · MEDIVAC 1:8 | 玩家定 |

光头别名已加：`光头 → 掠夺者(Marauder)`（docs/aliases/terran.yaml）。

## ★★ 虫族配比锁定 + 实现方案（2026-06-15 用户逐条确认）

**通用原则**：ZERGLING 在非狗 build 里**少量/可不出**（侦查+防守）；**女王每矿至少 1 注卵**，
中后期多补几个铺菌毯+防守；爆虫(baneling)=狗变、飞蛇(ravager)=蟑螂变。**狗与爆虫都要提速**
（ling 代谢 + bane 离心钩）。

| build | 配比（mass/cap/ratio/light） |
|---|---|
| 12pool | ZERGLING mass + QUEEN（速狗 all-in） |
| ling_bane | ZERGLING mass + BANELING ratio 1:1（狗:爆虫）+ QUEEN；**狗速+爆虫速** |
| macro_hatch | ROACH mass + RAVAGER cap（放胆汁技能）+ ZERGLING light + QUEEN |
| roach_allin | ROACH mass + ZERGLING light + QUEEN |
| nydus | ROACH mass + ZERGLING light + QUEEN |
| roach_ravager | ROACH mass + RAVAGER cap + ZERGLING light + QUEEN |
| roach_hydra | ROACH mass + HYDRALISK ratio（蟑:刺 2:1）+ ZERGLING light + QUEEN |
| mutalisk_harass | MUTALISK mass + ZERGLING light + QUEEN |

**虫族 build-aware sustain 实现方案（与人族不同）**：
- 兵从 LARVA 孵（`UNIT_TRAINED_FROM`：狗/蟑螂/刺蛇/飞龙←LARVA、爆虫←狗、飞蛇←蟑螂、女王←基地）。
- **产能瓶颈 = larva = 基地数 + 注卵**，不是 GridBuilding 科技楼（科技楼 1 个够）。
- 所以虫族 sustain = ① 多 hatchery（larva 源）② 女王每矿 1 + 注卵拉满（larva 脉冲）③ 补足 overlord
  （macro_hatch 基线卡人口 118s = overlord 跟不上，这是虫族头号短板）④ ActUnit 把 core_units 从 larva 孵。
- `plan_from_core_units` 要加**虫族分支**：producer 用科技楼（ROACH→ROACHWARREN 等映射），**不 GridBuilding 扩科技楼**；
  larva 扩张交给 ZergPersistentMacro（要补 queen/inject/overlord 逻辑——查它现在缺啥）。
- **评价**：助卵覆盖率(inject_coverage) × 卵消耗率(spend_efficiency) 乘积 + M1 + M3（已对齐）。
- **新埋点**：女王数 / 女王能量(banked vs spent) / hatch 注卵 uptime / larva（larva 已有）。
- **升级**：ling/bane build 要确保狗速+爆虫速研出（sustain 或 opening 管）。

**实现顺序**：① 助卵埋点 → ② 虫族评价(inject×spend) → ③ 虫族专属 build-aware sustain
（ZergPersistentMacro 补 queen/inject/overlord + plan 虫族分支）→ ④ 各 build 落 core_units → ⑤ A/B 验证。
（注：core_units 数据必须等 ③ 的虫族 plan 分支写完再加，否则 GridBuilding(LARVA) 会炸。）

## ★ 新 feature：中期转型提示框（2026-06-15 用户想法，follow-on）

build 进入**中期持续运营阶段**（opening 完成）→ PWA 弹选择框：「已进入【XX】中期运营阶段，要不要转别的？」
+ 几个转型选项（默认 = 上表 build 默认转型；备选 = 其它打法如 mech/bio/skytoss…）。玩家选→切，不选→走默认。
- **复用现有基建**：PWA 已有 recommendation toast + clarification 选项框 + pending_force_strategy。
- **落地分两步**：① 先做 Tier-1 build-aware sustain 按默认配比持续出兵（治"出兵不及时/余钱"，原始目标）；
  ② 再做转型弹框 + mech 备选（火蝠/雷神/解放者等新兵种，新 roster，用户指定不算违约束）。

## 〇、最终执行流程（2026-06-15 用户拍板）：一个 build 一个 build 串行，每个内部两层

**串行**：一次只做一个 build，**做完（Tier 1 + Tier 2 收敛）再下一个**，不跨 build 并行/排名。

**每个 build 内部两层（先结构后参数）**：
1. **Tier 1 结构 + 执行诊断/修复（我 + 用户过目）**：含**两子项**——
   - **① sustain 结构**：过了开局有没有"持续按主力兵种出兵/补产能/把钱花干净"这个**行为**？sustain 认不认该 build？
     缺行为 → 改代码补上（常是**共享改造**，惠及后续 build）。
   - **② 执行逻辑审计（2026-06-15 用户补：bot 核心产能执行逻辑本身也可能不优）**：有在派活，但**派满了没**？
     神=折跃门 CD 到了是否**一刻不等、一个不漏地立刻 warp**（CD 估计留没留余量/资源差一点跳没跳/落点找不到放没放弃/chrono 打没打满）；
     人=兵营工厂反应堆是否不间断生产；虫=注卵/产能 hatch 是否打满。**执行不优 → 加楼/调 sustain 都没用，得修执行逻辑。**
   顺手把可调旋钮暴露成数据。
2. **Tier 2 多变体参数迭代（后台 GA 自动）**：结构 + 执行能力到位 + 旋钮暴露后，对该 build 跑遗传算法
   （种群=最强 3 变体，随机变异数字旋钮 → 同 seed 配对 A/B → 留最强 3 → 连续三轮无改进则收敛）。
   只动数字、不碰兵种 roster（约束天生满足）。

**诊断树（util 低时定位到层）**：
```
util 低（产能空闲）+ 有钱有人口余量
├─ 没在下生产指令（sustain 没跑）         → 结构层(①)：改 sustain 持续出主力兵种
├─ 在下指令，但门/产线 ready 却空闲         → 执行层(②)：修产能执行逻辑（warp/产线不优）
│   信号：ready-idle 时 = (折跃门 total−busy) 且当时有钱有人口余量，大 = 执行漏产能
└─ 门/产线都在 CD/满产 但仍囤钱             → 参数层：util 其实高 = 楼不够 → Tier 2 GA 按 income-matched 加

补产能"加到多少"：util 高才加；量 = 让产能消耗速率≥采矿收入速率（神追猎 2 矿≈6-7 门；
注意卡矿还是卡气，补对应那条产线），区间给 GA，精确数由"囤钱→0 且 util 不掉"的指标推到平衡。
```

**红利**：Tier 1 多为共享改造，第 1 个 build 啃完，后续 build 的 Tier 1 自动变轻。

---

## 一、通用方法论（提炼中，目标是变成可自动执行的 runbook）

这是我们要沉淀的"标准作业流程（SOP）"。每条都尽量写成**版本无关、可脚本化**的形式：

```
[M0] 准备：固定 random_seed 池（N 个），保证变体 A/B 配对可比。
[M1] 度量：对每个 build，沙盒跑 N seed，采集 telemetry → 算三维度指标
     (M1 余钱积分 / M2 产能利用率 / M3 卡人口) + 合成总分 + 诊断时间线。
[M2] 基线：**每个 build 各自一个基线分**（仅作该 build 自己迭代的起点参照）。
     **不跨 build 排名、不挑最弱**——所有 build 都迭代。比较永远只在同一 build 的变体之间（intra-build，
     同指标语义一致 → 可比）。0-100 只供人读，决策用原始指标配对比。

— 以下 M3-M9 是"每个 build 一条独立进化线"（种群=该 build 当前最强 3 变体的遗传算法），逐个 build 跑 —

[M3] 诊断：打分器给每个变体的最差维度 + 最差时间窗 + 根因假设
     （囤钱→产能没补/出兵不积极；卡人口→补给节奏；产能空→主力兵种产量没拉满）。
[M4] 脑洞（独立 subagent，带随机性）：喂"诊断 + 变体 plan/YAML + 主力兵种"，提随机改进点子
     （调产能 cap / 补给节奏 / 主力兵种产量 / 时机；**绝不增删兵种类型**）。
[M5] 变异落地：在当前 3 个种子基础上各随机变异出后代变体（strategies/<race>/<id>__vN.yaml + 调参）。
     **静态 roster 校验**：后代兵种集合必须 == 原版，否则当场弃（不跑 A/B）。
[M6] A/B：所有变体（种子 + 后代）**同一组 seed 配对**跑沙盒、打分。
[M7] 选择：本轮所有变体里按"同 build 配对 A/B"（变体在 ≥⌈N·0.8⌉ seed 总分胜）留**最强 3 个**当下一代种子；
     其余淘汰。准入闸：build_acceptance 胜负/timing 回归不退步 + 兵种 roster 不变。
[M8] 留痕：把本轮诊断/变异/A/B/保留淘汰记进本文件"迭代记录"，把通用规律回灌"经验与决策规则"。
[M9] 收敛：重复 M3-M8，直到该 build **连续三轮"最强 3 个种子没被任何后代超过"** → 封板，转下一个 build。
     对当前所有 build 逐个做到收敛。

**硬约束（2026-06-15 用户 /goal，全程不可违反）：不引入新兵种类型，不删除兵种类型。** 变体只调
数量/时机/产能建筑数/补给节奏/出兵比例/cap，兵种 roster 必须 == 原版。
**校验 = 静态代码级，不需实测（2026-06-15 用户）**：读 build 的 plan 代码 + YAML 就能看出它训练哪些
兵种 → 提取 `unit_roster` 集合；变体改完静态 diff，roster 增/删 → **跑 A/B 前直接否决**（M5 落地时就拦，
不浪费游戏）。Phase 1 顺手做个 `unit_roster(strategy_id)` 静态提取器。
```

**自动化愿景**：M1/M2/M6 已是脚本（`build_efficiency.py` + 复用 `build_acceptance`）；M3/M4 现在靠
人 + subagent，目标是把"诊断→改进方向"的常见模式固化成规则表（见下"经验与决策规则"），最终 M3-M7
能半自动跑。版本升级后重跑 M1-M2 出新基线，对比旧基线找退化的 build，自动触发 M3-M7。

## 二、指标定义

见设计文档 §1 + §7。一句话：M1 余钱越少越好（钱花干净=兵多）、M2 产能利用率越高越好
（折跃门冷却中=好；虫族看 larva 不堆积）、M3 卡人口时间越少越好。三维度分开看 + 可调权重总分。
**最看重 M1**（用户：主要目标是不要太余钱）。

## 三、经验与决策规则（沉淀通用规律，随迭代追加）

> 这一段是方法论的核心产出：把"什么样的诊断 → 什么样的改法 → 大概率有效/无效"固化下来，
> 让它逐渐变成可自动应用的规则表。

| 诊断信号 | 大概率根因 | 通用改法 | 已验证? |
|---|---|---|---|
| 6:00 后矿囤 >1500 + 产能利用率 <0.6 | sustain 没按主力兵种拉满产量 / 产能建筑数封顶太低 | 认出主力兵种 → 补对应产能建筑 + 抬产量 cap | 待验证 |
| 持续卡人口 >4s | 补给节奏跟不上产能 | 提前/加密补给建筑触发 | 待验证（1g_robo 实测卡口 58s = 主短板） |
| sustain 启动晚（opening_completed+120s）才放开 | 通用 macro 不认具体 build 主力兵种 | 让 sustain 早启 + 按 build core_units 针对性补 | 待验证 |

**指标方法论铁律（2026-06-15 真局验证血泪）：到 200 引擎人口上限后的"囤钱 + 产能 util=0"是
forced-defend 沙盒的人造现象（满人口造不了兵、又不出门花不掉），不是 build 缺陷。** M1 余钱积分
和 M2 util 都**只在 `supply_cap < 200`（没到引擎上限）时累计**，否则会把每个 build 都误判成"巨量囤钱"
（实测 1g_robo 到 200/200 后矿囤到 19290、未 gate 时 t_end=900 算出 avg_excess=4140 worst=bank，
gate 后 302 worst=supply，真信号才浮出）。加 gate 后三维度对评测窗口（600/750/900）鲁棒（util 恒 0.67）。
→ **新版本重跑这套时，gate 必须在；任何"所有 build 都囤钱"的结论先查是不是 gate 没生效。**

（更多规则随迭代追加。）

## 四、迭代记录（按 build，逐轮追加）

> 每轮格式：build / 轮次 / 基线分(三维+总) / 诊断 / 试的变体 / A/B 结果 / 决策(保留|丢弃) / 学到的通用规律。

### 三族基线验证（2026-06-15，每 build vs VeryEasy 沙盒 seed=1，窗口 0-600s）

流水线**三族全验通**（forced-defend 全程压住、production 埋点 100% 出数、诊断 actionable）：

| build | 种族 | avg_excess_bank | prod_util / larva闲置 | 卡人口 | worst | 诊断 |
|---|---|---|---|---|---|---|
| 1g_robo_immortal | P | 442 | util 0.67 | 58s | supply | 卡人口为主 |
| macro_hatch | Z | 77 | larva闲置 4.4 | 118s | supply | larva堆积 + 卡人口 |
| two_base_tanks | T | **983** | util **0.52** | 9s | **bank** | **钱没花干净 + 产能空 → 主力兵种产量没拉满**（用户原话场景！）|

**关键观察**：
- `two_base_tanks` 是用户描述的"囤钱 + 产能空"典型 → 首个闭环迭代的最佳样本（改法明确：补产能/抬坦克·枪兵产量）。
- **卡人口是跨 build 高频短板**（P 58s / Z 118s）→ bot 自动补给在 ramp 期普遍跟不上，可能是一条**通用改进规律**
  （补给节奏提前/加密），值得作为方法论里"一改多受益"的候选。
- 三族诊断都 actionable、指向"补产能/补给/拉满主力兵种产量"——正是用户要的优化方向，且都不需增删兵种。

### ★ 闭环验证成功：bio_stim Tier-1（build-aware sustain，2026-06-15）

**这是"诊断→改→A/B 真涨分"闭环的首个完整证明。**
- **诊断**：余钱 worst（产能空 util 0.52）。根因 = `TerranPersistentMacro` 不建产能建筑（开局后兵营卡 5）
  + sustain 启动晚（opening_completed+120s）。
- **改（Tier-1 结构）**：① `CoreUnit` schema + bio_stim 配比（枪兵 mass/光头 4:1/医疗船 1:8/坦克 cap3）；
  ② `OpeningSustainAct` build-aware：读 core_units → 按 income 加产能建筑（兵营拉到 8！）+ 按配比续兵；
  ③ build-aware build 的 sustain delay 改 0（opening 一完成立即接管，治"启动晚"）。
- **A/B（同 seed=1，900s 窗口）**：余钱 **1351.9（generic baseline）→ 564.7（build-aware）= -58%**。
  兵营 5→8、工厂→2、星港→2 都按 income 加上了。roster 不变（仍 bio comp）。**闭环成立。**
- **学到的通用规律**（回灌经验表）：所有 build 的"出兵不及时/余钱"大概率 = **PersistentMacro 不建产能 +
  sustain 启动晚** 双因；build-aware sustain（按 core_units 加产能+续兵+早启动）是**共享 Tier-1 修复**，
  三族多数 build 适用。**余剩 564 = opening 自身产能 cap 太低（450-517s opening 期就开始囤）→ 下一步可
  抬 opening 兵营 cap 或让产能 scaling 更早，留 Tier-2/继续迭代。**

### ★★ 人族全族结构修复 + 启动时机精调完成（2026-06-15）

全 9 build build-aware sustain（fallback 300s）最终余钱（sandbox seed1, 900s）：

| build | 改前(generic) | 改后 | | build | 改前 | 改后 |
|---|---|---|---|---|---|---|
| widow_mine_drop | — | **334** | | bio_stim | 1351 | **519** |
| two_one_one | ~1351 | **369** | | one_one_one | — | **653** |
| marine_rush | — | **374** | | hellion_expand | — | **661** |
| banshee_harass | — | **489** | | two_base_tanks | 1234 | 需 Phase-B mech |
| reaper_expand | 8958 | **491** | | | | |

**人族结论**：余钱从基线 1351-8958 全降到 334-661，**"钱没花干净/出兵不及时"结构性根治**。
- **精调（启动时机）**：sustain 兜底 420→300s，reaper -67%（1474→491），有完成信号的 build 不受影响。
- **剩余 polish（Tier-2 GA / 留后）**：① one_one_one(653)/hellion(661) 偏高，产能数/cap 可 GA 精搜；
  ② two_base_tanks 机械化向，需 Phase-B mech 转型才根治 + 中期转型弹框 feature。
- **真实对局也受益**：人族 build 过了开局自动按配比续兵 + 加产能（兵营拉到 8），不再囤几千矿。

### ★ 人族全族应用共享修复 + 多 build 验证（2026-06-15）

build-aware sustain（共享 Tier-1）铺到全人族 + 兜底修复后，余钱大降（sandbox seed1, 900s）：

| build | baseline 余钱 | build-aware 余钱 | 备注 |
|---|---|---|---|
| bio_stim | 1351 | **519** | -58% |
| two_one_one | ~1351 | **369** | bio，同机制 |
| hellion_expand | — | **731** | 骚扰转 bio |
| reaper_expand | — | 8958 → **1474** | **兜底修复**：见下 |

**关键二次发现 + 修复**：reaper_expand 的 `_opening_done` 要 ≥4 reaper，**沙盒里永不满足 →
opening_completed 永不 fire → build-aware sustain 永不接管 → 余钱爆 8958**。
→ 加 `_SUSTAIN_FALLBACK_S=420s` 兜底：到点仍没 opening_completed 也强制 kick sustain。
reaper 8958→1474（-84%）。**通用规律（回灌）：build-aware sustain 不能只依赖 per-build
opening_completed 信号（不可靠），必须有时间兜底——这条对三族都适用。**
**两个建议优化（留后/GA）**：① reaper 还剩 1474（420s 前的 opening 期囤）→ 兜底可更早或修
`_opening_done` 在沙盒也能 fire；② 各 build 余剩多在 opening 自身产能 cap 期。

### 人族 #1 two_base_tanks — 第 0 轮（Tier-1 结构诊断，2026-06-15）

**基线**（0-600s sandbox seed1）：余钱 1234（worst）/ 产能 util 0.52 / 卡人口 9s。
**时序铁证**：t=446 min=1005 factory=1/2 → t=502 **min=2765 factory=0/2(全空)** → t=558 **min=3600** 仍 8 坦克 26 枪兵。
**结构根因（读 `OpeningSustainAct._build_terran` 确认）**：通用人族 sustain 硬编码
`ActUnit(MARINE/MARAUDER/MEDIVAC)`——**只续 bio、不续坦克**！tank build 过了开局主力兵种(坦克)停产
（8 个就不动），产能也没按收入加，且 opening_completed+120s(=541s) 才启动、太晚。
→ **三层定位 = 结构层**（行为不存在/出错兵种），Tier-1 改代码，不是调参。
**附带风险**：通用 sustain 出 MARAUDER/MEDIVAC 可能给 tank build 加 roster 外兵种（违约束）。

**提议 Tier-1 修复（共享改造，待用户 review）**：`OpeningSustainAct` 改 **build-aware**——读该 build 的
`core_units`（新 YAML 字段，可用 `roster.unit_roster()` 自动种子）→ 续造**这些**兵种（而非硬编码 bio）
+ 按收入加产能 + 早点启动。一处改，三族多数 build 的"出兵不及时/出错兵种"一起好（共享红利）。

**深挖到的精确代码级根因（2026-06-15）**：`TerranPersistentMacro.acts()`（persistent_macro.py:251）
**只建 工人/补给/扩张/气矿，根本不建产能建筑（兵营/工厂/星港）**！所以开局 plan 造完 ~2 兵营 2 工厂后，
**全局没有任何东西再加产能** → 收入吃不掉 → 余钱 3600。三族 PersistentMacro 同理。
→ **build-aware sustain 必须 (1) 按收入加产能建筑（income-matched）+ (2) 按 core_units 续造主力兵种。**
**已落地基础（commit）**：`CoreUnit` schema 进 strategy 模型（mass/cap/ratio/light/player 五种 policy）+
`bio_stim` 加 core_units（枪兵 mass / 光头 ratio 4:1 / 医疗船 ratio 1:8 / 坦克 cap3）。
**待实现**：sustain 读 active build 的 core_units → 加产能建筑 + ActUnit 续兵（ratio 需动态 act，
ActUnit.to_count 是静态 int）→ bio_stim 配对 A/B 验余钱降。先在 bio 系验机制（mass 兵种花得掉钱），
two_base_tanks 因需 mech 转型(Phase B)才能花钱、留后。

### ★ 虫族指标门 + 采气优先级（2026-06-15 用户拍板）

**两条修正，针对"蟑螂系又余钱又囤 larva"的诊断：**

1. **指标门统一回 180（用户最终口径）**：虫族**人口没到 180** → 余钱/余气/余 larva 都扣分；
   **人口 ≥180** → 钱/气/larva 全不扣（这是买活储备 + 满编阶段，不算浪费）。
   - 之前一版误把虫族门改成 `supply_used < supply_cap`（人口没满才扣）→ 飞龙局 237→4470 分暴涨（沙盒
     200 上限前一直在扣）。用户纠正阈值是 **180 不是 cap**。`scorer.py` 三处门（M1 余钱 in_growth /
     虫卵闲置 / 神人成长期）统一 `supply_used < cfg.growth_supply_max(180)`，三族同一口径。
2. **采气 build-aware（治蟑螂系气浮）**：诊断 roach_hydra 时序铁证——
   `t=625 min=85 gas=1959`、`t=893 min=14635 gas=5471 larva=95 ROACH=28`：
   **矿被 morph 吃光（蟑螂 75 矿/25 气）、气收入 >> 兵种气耗 → 气浮 5000+、人口被兵卡死**。
   根因不是缺气，是**气过剩 + 矿不够**。
   → `MacroConfig.gas_per_base`（默认 2）：`OpeningSustainAct._zerg_gas_per_base()` 读本 build 的
   core_units，**含气耗大的兵（飞蛇/刺蛇/雷兽/爆虫/感染/飞龙…）→ 2（满气）；纯蟑/狗 → 1（减气矿）**。
   `ZergPersistentMacro` 的 `StepBuildGas(hatch * cfg.gas_per_base, ...)` 据此少造气矿，把工蜂留在矿上采矿，
   缓解"矿不够→兵卡人口"。**通用规律（回灌经验表）：吃矿为主的兵种（mineral-heavy comp）应降采气优先级，
   否则气浮 + 矿荒双输。**

### ★★★ 虫族头号根因：sustain ActUnit from_building 用错科技楼（2026-06-15 真局逐帧定位）

**这是整个虫族优化的关键发现，推翻了"采气优先级"那条改法的前提。**

- **现象**：fresh 跑 6 个虫族 build，蟑螂系不降反爆（roach_hydra bank 256→3318、larva 闲置 2→17.5，
  人口卡 161 never-180；4 个首测 build 全 4900-5845）。两次跑（gas=2/gas=1）telemetry **逐字节相同**
  → 采气改动**零影响**（之前那个 "OLD baseline 256" 是误读未跑完的 fresh 局）。
- **逐帧定位**（systematic-debugging，没瞎改）：
  1. 时间线：roach 冻结在 28、t=625 起军队纹丝不动，84 larva / 12430 矿 / 5571 气全闲，人口 161/200 有 39 空间。
  2. 最后一次 ROACH morph 在 game 09:24（564s），之后 5 分钟**零生产**；run log 无任何报错/Traceback。
  3. sustain **确实 kick 了**（fallback@300，log 印出目标 ROACH→9999）；`sustain_uncap_active` 锁存 True 永不复位；
     `BuildOrder.execute` 非阻塞跑全部 children → sustain sub_act 每帧都执行。
  4. 读 `ActUnit` 源码：`builders = cache.own(from_building)` 然后 `builder.train(unit_type)`。
  5. **真凶**：`plan_from_core_units` 把虫族 `from_building` 设成**科技楼**（ROACHWARREN/SPIRE…）。
     `ActUnit(ROACH, ROACHWARREN, 9999)` → `roachwarren.train(ROACH)` = 无效（蟑螂从 LARVA 孵，
     不是从蟑螂窝训练）→ **sustain 一只兵都没产过**，军队永远停在 opening plan 的上限（蟑螂 28）。
- **为何之前"虫族验证通过"是假阳性**：飞龙/macro_hatch 等开局本身就把人口堆到接近满（mineral-light），
  在 0-180 窗口里 bank 看着不高，掩盖了"sustain 不工作"。蟑螂系开局只到 28、靠 sustain 续命 → 一测就露馅。
- **修法**：`ActUnit.from_building` 统一用 `UNIT_TRAINED_FROM`（LARVA/ZERGLING/ROACH/GATEWAY/BARRACKS，
  三族一致的真实孵化/训练来源）；科技楼降级为**前置依赖** `_ZERG_TECH_PREREQ`（GridBuilding 确保 1 座）。
- **通用规律（回灌经验表）**：① 三族 ActUnit 的 from_building 必须是 `UNIT_TRAINED_FROM` 的真实来源，
  虫族尤其不能用科技楼；② **"机制验证通过"要看主力兵数是否真突破 opening cap**，不能只看 bank——
  mineral-light build 会掩盖 sustain 失效；③ 改动前先 fresh 跑、确认 telemetry 真变了再下结论
  （seed 固定时两次相同 = 改动没生效，别误读半截数据）。

### ★★★ 修复后验证：虫族全族余钱暴降（2026-06-15，from_building 修复 A/B）

同 seed=1、900s 沙盒、0-900 窗口，修 from_building 前后对比（前=破损 sustain，后=修复）：

| build | 改前余钱 | 改后余钱 | 主力兵(终局) | 终局人口 | larva闲置 |
|---|---|---|---|---|---|
| roach_hydra | 3318 | **330** | 蟑 47 | 199 | 17.5→1.5 |
| roach_ravager | 4201 | **241** | 蟑 41 | 199 | 21.4→0.7 |
| roach_allin | 5845 | **306** | 蟑 61 | 200 | 29.4→0.6 |
| 12pool | 5304 | **617** | 狗 156 | 200 | 23.3→1.3 |
| ling_bane | 5128 | **882** | 狗 231 | 200 | 24.6→1.4 |
| macro_hatch | 173 | 165 | 蟑 47 | 200 | — |
| mutalisk_harass | 238 | 264 | — | 200 | — |
| nydus | 4917 | **411** | 蟑 55 | 200 | 0.82 |

**结论**：① 蟑螂系/狗系从"卡 28、人口 161 摆烂、囤 3000-5800"→"军队顶到 199-200、余钱 240-880"，
根因修复决定性有效；② macro_hatch/mutalisk 改前改后几乎不变（173→165 / 238→264）—— 实锤它们之前
是**假阳性**（开局自然填满人口，掩盖 sustain 失效），现在是真靠 sustain 续兵填满；③ larva 闲置全线
17-29 → 0.5-1.5，larva 真被消耗了。**剩余偏高**：12pool(617)/ling_bane(882) 狗系略高（156/231 狗），
留 Tier-2 看是否多出爆虫/调气/调狗数。

## 五、Phase 0-1 真局验证结论（2026-06-15，关键里程碑）

第一局真沙盒(`1g_robo_immortal` vs VeryEasy, seed=1, sandbox_macro_only) 跑通，整条流水线验证：
- ✅ **production 埋点出数**：930/930 snapshot 都带 production 块，prod_util=0.64 算得出。
- ✅ **opening_completed 落 telemetry**：314.29s，切窗可用。
- ✅ **forced-defend 真压住**：全程 tactical_intents 只有 `defend`，bot 没出门 → 沙盒隔离战斗噪声成立。
- ✅ **打分器三维度合理**：窗口 0-600s 算出 avg_excess_bank=435 / prod_util=0.64 / 卡人口 69s（worst=supply）。

**两个关键发现（写进经验规则）**：
1. **晚期巨量囤钱**：这个 build 在 forced-defend 下跑到 34 分钟自然 Victory 时，矿囤到 **84895**、
   supply 195/200。"钱没花干净"的信号主要在 **600s 之后** 才爆 → **评测窗口 0-600s 偏短，抓不到 sustain
   囤钱**。需要更长窗口（或 from_opening_completed + 拉到 ~900-1200s）才测得出用户说的"到一定程度后囤钱"。
   （TODO：基线时对比 600 vs 900 vs 1200 窗口，定一个能抓 sustain 又不太耗时的。）
2. **raw_events 的 snapshot-stop 不可靠**：靠 raw_events 监 game-time 提前 stop **没生效**（游戏跑到 34min
   自然结束）。改用 SC2 自身 `GameMatch.game_time_limit`（GameConfig.game_time_limit_s）钉死每局长度 →
   稳、省 wall-clock。

## 五·五、神族 17 build 效率审计 + warpgate sustain 修复（2026-06-16）

**审计（17 build，单 seed VeryEasy 900s，t_end=900）**：只有 3 个健康（robo 系：
1g_robo_immortal 423 / iac_2base 723 / dt_drop_iac 833），14 个摆烂或余钱高。摆烂特征
高度一致：opening_completed=null、产能~0.086、余钱~5800、人口卡 28、追猎卡 3。

**根因（★★★ 与虫族 from_building 同类，但机制不同）**：
神族全部没有 `core_units` → 走通用 `_build_protoss`，它有两个致命缺陷：
1. **`ActUnit(STALKER, GATEWAY)` 不会 warp-in**：sharpy `ActUnit.builders = cache.own(from_building)`，
   对 COMMANDCENTER/HATCHERY 有同质化特判、**对 GATEWAY 没有**。折跃门研究完成后 GATEWAY 全
   morph 成 WARPGATE → `cache.own(GATEWAY)` 恒空 → 不再出兵。且 `builder.train()` 对 warpgate
   本就无效（warpgate 要 warp-in 不是 train）。→ gateway 系卡在折跃前那 3 个兵。
2. **通用 macro 不补任何产能楼**：只能从"已存在的楼"练兵。robo 系开局留了 robo →
   `ActUnit(IMMORTAL, ROBO)`（robo 永不 morph）能续 → 健康；纯 gateway / 8 个 doctrine
   （沙盒无具体建楼步骤）→ 无楼可练 → 全摆烂。

**定位手法（取证而非猜）**：读 telemetry 单位时间线（STALKER 卡 3 不动 + 余钱单调涨 +
人口卡 28），确认"opening 后停产"；读 sharpy `act_unit.py` builders/execute 源码，确认
GATEWAY 无 warpgate 等价 + `builder.train()` 不 warp-in；找到 sharpy 已有的正确抽象
`ProtossUnit`（折跃完成自动切 `WarpUnit` warp-in，否则等价 ActUnit train）。

**修法**：build-aware 神族路径用 `ProtossUnit(unit, count)` 代 `ActUnit(unit, producer, count)`；
`plan_from_core_units` 给 gateway 兵 producer 显式规范成 GATEWAY（`UNIT_TRAINED_FROM` 给
`{GATEWAY, WARPGATE}` set 无序，曾可能选到 WARPGATE → GridBuilding(WARPGATE) 无效）。

**proof（4bg = warpgate 最坏 case，A/B 单 seed）**：

| 指标 | 改前(通用 sustain) | 改后(core_units+ProtossUnit) |
|---|---|---|
| 余钱 | 5789 | 857 |
| 产能 | 0.086 | 0.523 |
| opening 完成 | null | 305s |
| 追猎数 | 卡 3 | 3→7→12→30→60 |
| 人口 | 卡 28 | 顶满 200 |

**剩余 16 build rollout**（含 8 doctrine schema 用 `target_composition` 没 core_units —— 需单独
设计 core_units 来源；见 rollout plan + 独立评审）。**经验规则**：给任何新种族接 build-aware
sustain 前，先确认该族训练建筑有没有"会 morph/升级导致 cache.own 落空"的坑（神族 GATEWAY→WARPGATE、
虫族 HATCH→LAIR→HIVE），有就找/写对应的健壮 act，别裸 ActUnit。

### 神族 9 opening rollout 结果（2026-06-16，A/B vs 审计基线）

| build | 余钱 base→fix | 产能 base→fix | 卡人口 base→fix | 备注 |
|---|---|---|---|---|
| 1g_robo_immortal | 423→273/289 | 0.65→0.75 | 60→49/60 | 健康，改后更优 |
| iac_2base | 723→131/144 | 0.67→0.83 | 38→34/49 | 健康，改后更优 |
| dt_drop_iac | 833→295/223 | 0.41→0.53/0.44 | 11→11/13 | 健康 |
| blink_stalker | 4295→158 | 0.40→0.70 | 9→9 | 余钱高→治好 |
| void_ray_rush | 4129→470/448/473 | 0.29→0.45 | 0→0 | 空军，gas 仅 3154(降档生效) |
| phoenix_2base | 6084→379/387/653 | 0.43→0.47/0.52 | 9→9/18 | 空军，gas 不浮 |
| cannon_rush | 6548→300 | 0.18→0.55 | 16→9 | 余钱高→治好 |
| dt_rush | 7672→709 | 0.066→0.45 | 9→31 | 摆烂→治好，卡人口略升(Tier-2 微调) |
| 4bg | 5789→857 | 0.086→0.52 | 9→9 | warpgate proof（前 commit） |

**结论**：9 个 opening 余钱全线大降、产能全升，3 个健康 build 不退反进；多 seed 一致。
warpgate 修复 + 空军降档 + townhall skip 全部生效。**唯一软肋** dt_rush 卡人口 9→31（从彻底
摆烂变能跑，可留 Tier-2）。

**doctrine（8 个）审计无效 + 移出（★ 评审抓出）**：`forced_opening` 只匹配 `OpeningBuild`，
doctrine id 回退默认 4bg → 之前 8 个 doctrine"审计"全跑成 4bg（余钱雷同 5789 是铁证）。且真局
玩家切 doctrine 填 slot → sustain 被 `persistent_set` gate、产兵归 `sharpy_dummy_class` plan。
**经验规则**：给某 build kind 做 sustain 优化前，先确认①harness 真能驱动它（不是静默回退）；
②真局产兵归属地是不是 sustain（被 gate 的路径优化 = 假绿）。doctrine 需另立项：先让 harness
支持填-slot 真局路径驱动 doctrine，摆烂则修对应 sharpy plan。

### 持续运营 doctrine 审计（2026-06-16，全类 18 个，--auto-switch-to 真测）

**背景**：之前"8 神族 doctrine 摆烂"结论作废（4bg 顶替的假数据）。给 build_efficiency 加
`--auto-switch-to`（开局 → opening 完成后切 doctrine，复用 director 现成 auto_switch 机制）后
真测。注意：测的是 **doctrine plan + 开局 sustain 并行**（sustain 在 opening_completed latch
在切换前）—— 这正是真局主流场景（玩家开局打完后切定式，sustain 早 latch）。

**结果（13 个真测，余钱=avg_excess_bank，单 seed 除 mech 双 seed）**：

| race | doctrine | 余钱 | supply | 末兵种 | 判定 |
|---|---|---|---|---|---|
| P | skytoss | 453 | 198 | VOIDRAY13/CARRIER7/HT5 | 健康 |
| P | colossus_immortal | 261 | 200 | STALK25/IMMO6/HT5 | 健康 |
| P | blink_harass | 251 | 200 | STALK46/DISRUPTOR4 | 健康 |
| P | colossus_no_ht | 483 | 200 | STALK32/IMMO6 | 健康 |
| P | immortal_archon | 134 | 200 | ZEAL31/ARCHON7 | 健康 |
| P | immortal_archon_no_ht | 149 | 200 | ZEAL33/ARCHON3 | 健康 |
| P | phoenix_control | 380 | 200 | PHOENIX42/COLO3 | 健康 |
| P | skytoss_no_ht | 424 | 200 | CARRIER12/VOID8 | 健康 |
| Z | ultralisk | 837 | 200 | LING168/ULTRA6 | 健康 |
| Z | lurker_hydra | 390 | 200 | ROACH34/HYDRA17/LURKER7 | 健康 |
| T | bio_max | 523 | 200 | MARINE54/MARA17/MEDI9 | 健康 |
| T | skyterran | 469 | (早结束) | MARINE/TANK 正常 buildup | 健康 |
| **T** | **mech** | **5015/5139** | **178** | TANK10/HELLION6/MARINE14 卡死 | **摆烂** |

**结论**：持续运营 doctrine 这一整类**绝大多数健康**——靠"开局 build-aware sustain（本轮修好）+
doctrine plan"并行，钱花得干净、顶到 200 人口、出对兵。**唯一真问题 = 人族 mech**（慢科技重型流）：
supply 卡 178（<180 还罚 bank）、余钱 5000+、TANK/HELLION 不再增。双 seed 一致。skyterran（战巡，
另一慢科技嫌疑）实测健康 → 不是"所有慢科技流"通病，是 mech 特有。

**经验规则**：①doctrine 真测必须用 `--auto-switch-to`（forced_opening 不认 doctrine id）；②测出的
"健康"含开局 sustain 并行兜底的功劳（真局主流如此）；③util 指标在虫族 doctrine 路径返回 null
（scorer 的 zerg larva-util 在 auto-switch 路径没算）—— 读 bank+supply+末兵种判定。

### 人族 mech doctrine 修复（2026-06-16，唯一摆烂 doctrine）

**根因**：mech sharpy plan 每种兵写死低上限（坦克 8/雷神 3/火车 6/维京 6），3 工厂造满即停 →
supply 卡 178、余钱 5000+。切 mech 后通用 terran sustain 回退 bio（错兵不扩兵营）→ 不顶人口。

**修法**（用户方向：多雷神火车 / 前期防守 / 早升攻防）：工厂 3→5（VF4/5 裸厂出火车吞矿）；
上限 坦克 8→14 / 雷神 3→12 / 火车 6→12 / 地雷 4→6 / 维京 6→8；**雷神 priority + Step 列首抢气**
（关键：否则便宜火车填满 200 人口、雷神挤成 0、气浮 4000；列首后雷神 0→7）；军火库提前到 VF1 后。

**A/B（两 seed 一致）**：余钱 5139/5015→523/533、产能 0.31→0.45、supply 178→200、雷神 0→7。

**调参留痕（3 轮）**：v1 抬上限+扩厂(火车 24) → 余钱治好但雷神=0、气浮（火车独占人口）；
v2 雷神 priority + 火车 24→12 → 雷神 0→3（仍少，坦克 Step 在前先抢气）；v3 雷神 Step 提到坦克
**前面**列首 → 雷神 7、坦克让到 8。**经验**：sharpy 同 priority 多兵种按 Step 列序抢资源，
要"多出某兵"就把它的生产 Step 排前面 + priority。gas float 在 supply=200 时不罚（180 门），不追。

### 虫族 morph 兵 sustain 修复 + ling_bane 爆虫优化（2026-06-16）

**根因（★★★ 同神族 warpgate / 虫族 from_building 一族：act 不匹配生产机制）**：build-aware sustain
用裸 `ActUnit(BANELING, ZERGLING)` 出爆虫，但爆虫是 zergling **morph**（`zergling.train(BANELING)`
对 morph 无效）→ 爆虫冻结在开局那几个（ling_bane 卡 12，气浮 5733、狗 231 爆虫不动）。同类影响
所有 morph 兵（飞蛇/潜伏/BL）。叠加 vendored `MorphBaneling` 用了失效旧 ability。

**修法**：① vendor 修 `MorphBaneling` ability `MORPHZERGLINGTOBANELING_BANELING`→
`MORPHTOBANELING_BANELING`（docs/sharpy-patches.md §6）；② sustain 虫族用 sharpy `ZergUnit`
（按兵种 dispatch morph + 从 larva 补源兵）代裸 ActUnit。larva 兵不变（nydus 411→411 /
mutalisk 264→264 回归吻合证明无 morph 兵零改动）。

**ling_bane A/B（双 seed，用户授权"可全变爆虫"）**：爆虫 ratio(静态 20)→ cap 250。
余钱 882/948→342/388（降 60%）、爆虫 12→250、气浮 5733→1890/2412（腰斩）、≈全爆虫（LING1+BANE250）。
中间 cap 150 试过：余钱同 343/388 但气浮 4400（150 爆虫吃气不够）→ 250 把气也榨干。

**经验规则（写死）**：给 build-aware sustain 接任何兵种前，先查它的**生产机制**——larva train /
建筑 train / morph / warp-in？错配 act（用 train 出 morph 兵、用 ActUnit 出 warp 兵）= 静默冻结。
三族都踩过：神族 warpgate(ProtossUnit)、虫族 morph(ZergUnit)、虫族 from_building(LARVA)。

**12pool（纯狗 12D）现状**：余钱 617（中等）。瓶颈分析：矿 bank 大半来自冲满 200 人口前的窗口
（supply 131→176 时矿从 20 飙到 1235，农民饱和+larva 节流跟不上）；顶 200 后矿狂囤(13835)+larva
堆 82 闲置，但 supply≥180 不罚。纯狗无气单位 → 气恒浮 7436（不可避，气非评分项）。lever 弱：
617 大半是纯狗冲人口前的自然累积，要再降只能加 macro 孵化场提 larva 吞吐（共享 macro 改动）或
给它加爆虫吃气（改纯狗身份）—— 待用户定。

## 六、进度

- [x] Phase 0 埋点 + 地基（random_seed / sandbox_macro_only / production telemetry / opening_completed）
- [x] Phase 1 打分器 `src/vibecraft/build_efficiency/scorer.py` + `scripts/build_efficiency.py`（真局验通）
- [x] game_time_limit 钉死每局长度（沙盒不会自然停）
- [ ] roster 静态提取器（unit_roster(strategy_id)，M7 准入闸）
- [ ] 评测窗口定型（600 vs 900 vs 1200，抓 sustain 囤钱）
- [ ] Phase 2 每 build 各自基线（不排名）
- [ ] Phase 3 手工验闭环（2-3 个代表性 build），证伪后再做自动化 + workflow 全量 sweep
