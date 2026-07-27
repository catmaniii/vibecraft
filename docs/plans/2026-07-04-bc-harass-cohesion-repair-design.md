# BC 群体骚扰 3 修：单艘出门 + 群体协同移动 + 释放后回家修理

> 用户 2026-07-04 真机反馈。群骚扰整体不错，3 点要修。

**Goal**：①第一个大件单独出门骚扰（不等集合）；②整群协同移动（一起出发/转移，不各控各的）；
③解除骚扰指令后传回家的残血大件也被 ≥3 农民修理、修完农民回采矿。

**Tech Stack**：`src/vibecraft/bot/auto_combat/terran/bc_raid_act.py`（GroupHarassAct）+ bc_rush plan tactics。

---

## ① 第一个大件单独出门（状态机放行单艘）

**现状 bug**：`_tick_group` 健康分状态机 `if len(able) < _ABS_RETREAT_FLOOR(2): want="STAGING"`
→ 单艘 BC（able=1 < 2）被**强制留家**，永不出门。

**用户要**：1 艘就单独出门骚扰；2-3 艘才"集合了一起出门"。

**改**：**不加特判**——通用公式本就对，只需让 `_ABS_RETREAT_FLOOR` 减员保护**只对 ≥2 艘生效**：
```python
if len(alive) >= 2 and len(able) < _ABS_RETREAT_FLOOR:
    want = "STAGING"  # 减员太惨(仅 ≥2 艘时防"添油送死")；单艘不受此挡
elif prev == "STAGING":
    want = "HARASS" if len(fit) >= threshold else "STAGING"
else:  # prev == "HARASS"
    want = "STAGING" if len(able) < threshold else "HARASS"
```
- **单艘走通用公式**：`threshold = min(1, max(2,0)) = 1`。
  - STAGING→HARASS 需 `fit≥1`（fit=血 **≥ `_SALLY_HP` 0.95**）→ **满血 95% 才出门**（和群一致，不是 40%）。
  - HARASS→STAGING 需 `able<1`（able=血 **> `_RETURN_BAR` 0.40**）→ **掉到 40% 才回家**（滞回低门）。
  - 所以单艘 = "满血出门骚扰 → 掉到 40% 回家养 → per-BC jump-heal 满血再来"。正是用户要的"第一个大件出门"。
- alive≥2 逻辑完全不变（fit≥threshold 集合出击 + 减员保护）。改动只是**把减员保护的适用范围加了 `alive≥2` 前置**。

---

## ② 群体协同移动（接近/转移共用一条路径，不各控各的）

**现状 bug**：接近路径 `_approach_wp(bc, ...)` **按 bc.tag 各自缓存**，每艘从自己位置独立算
`plan_harass_approach` → 避障退化选边可能不同、起点不同 → **有的往左有的往右、散着走**。

**用户要**：整群一起出发、一起转移移动；例外——没血的先单独传回家养、等其他人、养好归队。

**改：接近/转移走「群路径」（按 did 缓存，从群质心算一次），全群非养血 BC 走同一条**：

1. **群质心** `centroid` = 非 healing 的 HARASS BC 位置质心（healing 的不算，它们在回家）。
2. **群路径** `_group_approach_wp(did, centroid, behind, ml, th, enemy_main)`：
   - 缓存 key = `(did, target_key)`（**per-group，非 per-tag**）。
   - 首次从 **centroid** 算 `plan_harass_approach` 整条 waypoint 链，锁定缓存（CLAUDE.md 强规则）。
   - 推进：**群质心**到达当前 waypoint（<5 格）→ 前进下一个（不是单艘到达就推进，等整群到）。
   - arrived：群质心到 behind < `_ENGAGE_RADIUS` → 置群 arrived 闩锁 → 放行 near-micro。
3. **per-BC 行为**：
   - healing（残血跳家养）：不变，单独回家（peel off），养好清 healing → 归队走群路径。
   - 非 healing、群未 arrived：**move 到群当前 waypoint**（全群同一个点 → 一起走）。
     - 轻微散开防重叠：可给每艘按 tag 加极小固定偏移（±1~2 格，稳定不漂）——或直接同点（BC 是空军可叠，简单起见先同点，真局看要不要散开）。
   - 群 arrived（整群到矿后点）：near-micro **per-BC**（贴农民质心 sweep 追杀，各打各的农民 OK，
     因为都在同一条矿线；这不违背"一起走"——"转移"才要一起）。
   - 转移（矿间游走切 rank / target 变）：target_key 变 → 群路径重算（从新 centroid）→ 整群一起转移。✓

**cohesion 不变量**：出发（STAGING→HARASS 是 group posture，全群同时切）+ 接近 + 转移都走群路径（同 waypoint）
→ 一起走；只有 near-micro（到矿后贴农民）和 healing（残血回家）是 per-BC。

### ②+ 集结点 gather gate（用户 2026-07-04 补充：关键节点收紧再走）

散着走骚扰打折扣。要在**关键节点先集结收紧**再一起动。落点就是 `plan_harass_approach` 的
**场外集结点 stage**（矿后点前、还没被发现的那个点）——把它当 **rally gate**：

- **群路径推进到 stage waypoint 后，不立刻进最后一段（扎矿后点），先等**：所有**非 healing** BC
  离 stage < `_RALLY_RADIUS`（≈4~5 格，收紧）→ 才放行最后一段一起扎进矿后点。
- **出发集结**：STAGING→HARASS 由状态机在家 gather（已有），出门那一刻本就聚在一起，出发天然收紧。
- **转移集结**：矿间游走切目标 → 新路径的新 stage 同样是 rally gate → 转移途中散开的，到新 stage 先收紧再一起进。
- **防卡死**：加超时兜底——在 stage 等 > `_RALLY_TIMEOUT_S`（≈4 游戏秒）仍没收齐（个别掉队/被拖住）→ 也放行，
  别为一艘掉队卡住整群（掉队那艘走 healing 或自己追）。healing 的不计入收紧判定（它在回家，等它没意义）。
- **确定性**：收紧判定用「非 healing BC 到 stage 的最大距离 < radius」，同输入同输出，不引入抖动。

一句话：**stage = 集结点**。群到 stage 先收紧（或超时）再一起扎矿后 → 到矿线时是抱团的、骚扰不打折。

**缓存迁移**：`_approach_wps`/`_approach_wp_idx`/`_approach_arrived` 从 `(tag,target_key)` 改 `(did,target_key)`。
per-BC 的 healing/dodge/jump 状态仍 per-tag（不变）。

---

## ③ 释放后回家的残血大件也要 ≥3 农民修

**现状**：GroupHarassAct `_ensure_repair`（3 SCV）**只对群内 healing BC**。解除骚扰指令→BC 释放→
不在群里→`_ensure_repair` 不跑。sharpy `Repair` 对机械<75% 会修但**只派 1 个 SCV**（无敌时
`solve_scv_count` power_max=1）→ 修得慢/看着像没修。

**用户要**：只要大件传回家（残血），≥3 农民修，修完农民回采矿。**不管在不在骚扰群**。

**改：加独立 `BcHomeRepairAct`（vibecraft act，放 bc_rush + 持续 doctrine 的 tactics）**，
对**所有** ready 的 BC（不限骚扰群）：血量 < `_REPAIR_TRIGGER`(0.95) 且在**己方 zone 附近**（离任一
townhall < N 格，即"在家"）→ 拉 ≤3 个空闲 SCV `repair(bc)`；BC 满血(≥0.99)→ 不再补新 SCV
（SCV 修完自动 idle→DistributeWorkers 收回采矿）。逻辑复用 GroupHarassAct `_ensure_repair` 的模板
（数正在修的、补到 3、不抢已在修的）。
- 幂等：每帧只补到 3，不重复派；SCV `is_repairing` 判重。
- "修完回采矿"：不主动派 SCV 回矿——SCV `repair` 完成后自然 idle，sharpy DistributeWorkers/SpeedMining
  下一帧收回。（若发现修完滞留再显式还 role。）
- 放 tactics SequentialList（非 blocking，return True）。

---

## 测试计划

### 单测（test_bc_raid_act.py + 新 test_bc_home_repair.py）
- ①：alive==1 且 hp>0.4 → posture HARASS（不再 STAGING）；hp≤0.4 → STAGING。alive==2 fit<2 → STAGING（不变）。
- ②：群质心到达才推进 waypoint（单艘到达不推进）；缓存 key 是 (did,target_key) 非 tag；healing BC 不算入质心。
- ③：damaged BC 在家 → 派到 3 个 SCV repair；已 3 个在修 → 不再派；BC 满血 → 不派。

### 真局 trace 验（hard_bc_game.py，BCRAID_TRACE）
- ①：**第一艘 BC 出现即 posture=HARASS 出门**（trace posture + dmain 下降），不在家 STAGING 干等。
- ②：多艘时 BC 位置**聚拢同走**（记群质心 + 各 BC 到质心距离，接近段应 < ~8 格抱团；aim 收敛同向），
  不再一半 aim 左一半 aim 右。
- ③：注入"解除骚扰" → BC 释放残血传家 → telemetry/日志确认 **≥3 SCV is_repairing 该 BC** + BC 血量回升 +
  修完 SCV 回采矿（gas/mineral workers 恢复）。外部终态验证（非中间 trace）。

---

## 评审处置（opus 独立评审 2026-07-04，全部采纳）

### ② 群体协同：改用 home 锚当路径起点（消除移动质心的所有 corner case）
评审 P0：原设计「从移动质心锁路径」+「缓存 key 改 did 但剪切谓词没同步」→ **每帧路径被清、从漂移质心重算 → 追移动靶抽搐**（踩中 CLAUDE.md 强规则）。采纳评审的更简方案：

- **路径起点 = `_get_home_anchor()`（稳定、确定、已锁）**，不是移动质心。骚扰都从家出击，home 是固定点。
  `plan_harass_approach(home_anchor, ml, th, behind, enemy_main)` → 一条链，**缓存 per `(did, target_key)`**。
  → **全群共用同一条路径**（这就是治「各控各的、有的左有的右」的根：路径同一条，不再各从各自位置算）。
- **per-BC 沿共享链推进**：`_approach_wp_idx`/`_approach_arrived` 仍 **per `(tag, target_key)`**（每艘自己的进度）。
  BC 都在**同一条**链上、只是进度不同 → 一起出发就一起走；新 BC 从家 idx=0 起沿链追（**走安全链、不直穿主基地**、不发散）。
- **剪切谓词必须分开改**（评审揪出的致命点）：`_approach_wps` 按 **did** 剪（`k[0] in live_dids`）；
  `_approach_wp_idx`/`_approach_arrived` 按 **tag** 剪（`k[0] in live`）。`_tick` 顶部那段 + jump 清理段逐一对齐。
- **集结点 rally gate**（用户补充 + 评审确认）：BC 推进到 **stage waypoint**（倒数第二点）后**hold 在 stage**，
  直到**所有非-healing BC 都进 stage 的 `_RALLY_RADIUS`（收紧）**才放行各自进最后一段（behind）；超时 `_RALLY_TIMEOUT_S`
  兜底防掉队卡整群。cohesion 判定 = 「非-healing BC 到 stage 的最大距离 < radius」（确定性距离判定，不用质心当起点）。
- **推进不抖**（评审澄清）：idx 单调棘轮，距离噪声只延后不回退，不用过度防抖。**空集 guard**：无非-healing 成员 → 跳过。
- **单测补**：①连续帧下同 (did,key) 的 wps 不变（路径锁死回归）；②掉队 BC 不拖住主群（rally 超时放行）；
  ③新 BC 从家沿链走、不直穿 enemy_main 的 R 内。

### ① 单艘出门：保留通用公式（floor 限 alive≥2）+ 补 survivor 测试
- 不加特判，只把 `_ABS_RETREAT_FLOOR` 减员保护限 `alive≥2`；单艘走通用公式（满血95%出、掉40%回，滞回）。
- 评审关注「3→1 残血 survivor」：prev=HARASS 时 survivor（血>40%）靠滞回继续、per-BC jump-heal（有真 AA 威胁时）
  跳回家保命。**单测显式覆盖此 case**（记录这就是想要的行为：uptime 优先，jump-heal 兜底），别裸写魔法数、复用 `fit`/`able`。

### ③ 修理：单一归属，别三套并行抢农民
- 评审 P0：`_ensure_repair`(群内3SCV) + 新 `BcHomeRepairAct`(所有在家BC 3SCV) 同帧竞争（`is_repairing` 同帧还没置真 → 各派3 → 瞬时6抖）。
  **改**：**在家修理只留 `BcHomeRepairAct` 一个系统**——GroupHarassAct **删掉 `_ensure_repair` 的派工**（healing BC 的回家养血仍由状态机 move home，但**修理交给 BcHomeRepairAct**）。sharpy Repair 的 1 SCV 是加性小量、容忍。
- **「在家」半径 N 明确**：离任一 townhall < `_HOME_REPAIR_RADIUS`（复用 home 锚半径，确定性）。
- 判重用 `w.order_target == bc.tag`（`_ensure_repair` 现成模板；`order_target` 返回 tag(int) 需真机核对）。
- 「修完回采矿」靠 DistributeWorkers 自然收回 —— **真局终态验**：SCV 既不被提前拽走、修完也确实回矿（gas/mineral workers 恢复）。若滞留/被提前拽走再显式管 role。

### UNVERIFIED（真机核对，不背书）
`SCV.repair(unit)` / `is_repairing` / `is_mechanical`(BC是机械) / `order_target` 返回 tag —— 标 UNVERIFIED，真局终态 + 值核对。

## 第二轮评审处置（opus 2026-07-04，②③ 补关键边界，全采纳）

### MUST-FIX B（②最关键）：成员 churn → catch-up joined-cohort（不补则前排 stall + 晚到者穿心）
`recruit_new` 让 bc_rush **每 ~40s 新 BC 入同一 did**，churn 是常态。新 BC 在家会：①把群质心拽回家 →
`arrived` 判据(质心到 behind)永不满足 → **前排已贴脸 BC 永远进不了 near-micro、干等（stall）**；②`move(behind)`
SC2 直线寻路 **直穿敌方主基地**（违反贴边规则）。healing 归队同病。**修：引入 per-tag `_joined` 闩锁**：
- **质心 / gather-gate 的成员集 = 「非 healing 且 `_joined`」**（已并入本次接近的主体），**排除**未 joined 的新兵/刚归队者。
  → 质心稳定、arrived/gate 不被在家单位拽住、不 stall。
- **未 joined 的 BC** 走**自己的 per-tag `plan_harass_approach` edge 路径**（从 bc.position 起，贴边、不穿心）飞向 stage；
  一旦进 stage 的 `_RALLY_RADIUS` → 置 `_joined` → 之后才跟群 waypoint。→ 晚到者贴边追上、追上才并入。
- `_joined` 在 jump / 进 healing / target_key 变 时清（下次重新并入），随 per-tag 缓存一起 `_tick` 剪切。
- **empty-set fallback**：「非 healing 且 joined」为空（全养血/全新兵）→ 质心退回 `home` 或跳过推进，别对空集求质心崩。
- 这样：主体群走共享 did 路径抱团、晚到者各自贴边追、追上并入——"一起走"才真成立。

### MUST-FIX C（③）：单一修理权威（已在实现指令）
`Repair()`(1SCV) + `_ensure_repair`(群内3) + `BcHomeRepairAct`(在家3) 三处同帧竞争（`is_repairing` 同帧未置真 →
各派3→瞬时6抖）。**在家修理只留 `BcHomeRepairAct`**：删 `GroupHarassAct._ensure_repair` 的派工（healing BC 仍 move home、
修理交新 act）。sharpy Repair 的 1SCV 是加性小量容忍。

### MUST-FIX D（③）：释放残血 BC 未必自己回家
玩家解除骚扰→claim 撤销→BC 归 sharpy 战斗 plan，**未必回家**（可能仍被派 attack/gather）→ `BcHomeRepairAct` 的
"在家<N"门不触发。用户观察到"它会往家传送"→ 多半确实回了家，但**真局 ③ 测必须验终态**：注入解除骚扰 → 确认 BC 真进
home 半径 + telemetry ≥3 SCV `is_repairing` 该 tag + 血回升。**若真局发现释放 BC 滞留野外不回家 → 补一条"释放残血 BC
显式 move(home) 一次"**（director 释放时或轻量 act）。别只信"应该会回家"。

### ① 补充 + 测试修正
- ①绿灯。测试计划里旧的 "alive==1 且 hp>0.4→HARASS" 描述**作废**，改成："alive==1 STAGING 起步需 hp≥0.95 才 HARASS；
  HARASS 中 hp≤0.40 才回 STAGING"。
- 补一条 ① 真局测："3→1 残血单艘不直冲矿线送掉"（per-instance 看该艘 hp 曲线 + 是否触发 flee/jump 兜底；posture 不负责保命）。

### 测试判据强化（CLAUDE.md per-instance + 外部终态）
- ②"一起走"：**per-instance 断言每艘**到质心距离（不用 min/any 聚合——BC 骚扰二矿栽过聚合掩盖）；
  **新增 churn 两门**：(a) 新 BC 中途入伍时前排仍能进 near-micro（群 arrived 真置位、不 stall）；
  (b) 晚到 BC 轨迹**不穿敌方主基地**（它到 enemy_main_center 的最小距离 > 阈值）。这两条正是 MUST-FIX B 的验收门。
- ③：终态（BC 血回升 + gas/mineral workers 恢复）+ "释放 BC 确实进 home 半径"（兜 MUST-FIX D）。

### 候选（leader-follow，备选不采纳）
评审给的更省状态候选：确定性选 leader(`min(tag)` 非 healing)跑它自己的 edge 路径、其余 move 到 leader 当前 waypoint。
省质心空集/被拽问题，代价是 leader 死要换人重算。**本设计采「质心 + joined-cohort」（更稳）**，leader-follow 仅记录备选。

## 不做（YAGNI）
- ②「±1~2 格 tag 偏移散开」**砍**——同点即可（BC 空军可叠），偏移引入漂移风险且违背目标点锁定。
- 不做队形编排（阵型/间距优化）——同点或极小偏移够用，真局看要不要散开。
- 不动 jump-heal 阈值 / dodge / cheap-kill / P1 威胁规避（#557/#561/#580 验过）。
- ③不做"专门派 SCV 修完走回矿"的显式还 role（先靠 DistributeWorkers 自然收回；真局若滞留再加）。
