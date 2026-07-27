# BC 群骚扰状态机 · 推倒重写设计

> 用户 2026-07-04 拍板重写。#580/#583/#584/pre-pass **改 4 次坏 4 次**——旧机器太乱，
> 靠读代码已推不动。本文重写成**每艘 BC 恰好一个状态**的干净状态机。

**Goal**：①第一艘 BC 及时骚扰 ②持续出兵 ③集结点在**敌方主基外安全点(stage)**、不在家 ④一起走
⑤前排骚扰**绝不被新兵/自身机制拉回家** ⑥贴边不走中路 ⑦低血**真战术跳(传送)回家**+到家**停下**让 SCV 修+修满归队。

**Tech Stack**：`src/vibecraft/bot/auto_combat/terran/bc_raid_act.py`（`GroupHarassAct` 整个重写）。

---

## 旧机器的病（要删掉的）
- **2 套状态纠缠**：群 posture(DIVE/RETREAT) + 每艘 BC 由 **4 个 latch 集合**拼身份
  (`_joined_tags`/`_healing_tags`/`_approach_arrived`/`_approach_wp_idx`)。
- **3 个 `move(home)` 走回家机制重叠**：①战术跳(step3/4)——跳 CD 71s，第 2 次触发 CD 没好 → 不跳 →
  落到 step5 **走**回家（用户骂的"走回去"）；②RETREAT(step5)；③healing(step1)。三者都往家堆 →
  "家里集结点"是垃圾堆出来的，不是设计的。
- **非实时(fast)自测假阳性**：`hard_bc_game.py realtime=False` 显示"squad engage、一起走"，但用户
  **实时**真局全坏（telemetry：第一艘杵家 (46,43) 2 分钟）。CLAUDE.md 明记这个坑，反复栽。
- **强疑外部干扰**：BC 若没被 recruit 进组 → 没 `_reserve` → sharpy `PlanZoneGather` 把 idle army
  拉回家。第一艘杵家很可能**根本不在骚扰 act 控制下**。

## Phase 0（实现第一步，先拿真相，别再猜）
加干净 trace 后，**开 `BCRAID_TRACE=1` + `realtime=True`** 真机跑一局，确认第一艘杵家的真因是哪个：
- (a) recruit 没把 BC 及时加进组 → 不 reserve → sharpy 拉回家？
- (b) 在组里、reserve 了，但内部状态卡在"回家"分支？
- (c) reserve 无效（`roles.set_task` 真机没生效，类似 salvage）→ sharpy 抢控制？
判据：trace 每帧打「tag / state / reserved? / in_group? / move_target / dist_home / dist_stage」。
**先看到 (a)/(b)/(c) 哪个，再定重写细节**（尤其 recruit+reserve 那段要不要一并重做）。

---

## 新状态机：每艘 BC 恰好一个状态

**状态存 `self._state: dict[tag, str]`，一艘 BC 任意时刻恰好一个状态。删掉所有 latch 集合。**

```
状态（5 个）:
  RALLY   奔赴集结点 : 沿贴边路径(plan_harass_approach)奔向 stage(敌方主基外安全点)
  GATHER  集结等待   : 到 stage，待群体 DIVE 令
  DIVE    扎矿骚扰   : 打农民(游走/贴/风筝/避AA/cheap-kill/threat-flee)
  HEAL    脱离养血   : 战术跳(传送)回家 → 到家 STOP 停住让 SCV 修 → 修满归队
  (无独立"全队回家 RETREAT"态：群体打不动 = 个体逐个进 HEAL，不再有整队 move(home))
```

**每艘 BC 的状态转移**
```
  (新出 BC / HEAL修满) ─────────────> RALLY
                                        │  到 stage(<_RALLY_RADIUS)
                                        ▼
                                      GATHER ──[群 DIVE 令]──> DIVE
                                        │                       │
   任意状态 血<_HEAL_FLOOR(40%)         │                       │ 目标矿清/切矿→仍 DIVE 打新矿
   或 一帧爆发掉血(>18%满血) ───────────┼───────────────────────┤
                                        ▼                       ▼
                                       HEAL <───────────────────┘
                                        │ 血满 ≥_RECOVER(90%)
                                        └──> RALLY (归队，重走贴边路奔 stage)
```

- **RALLY**：`bc.move(当前贴边 waypoint)`。路径 `plan_harass_approach(home→stage)` **一次锁定缓存**
  (CLAUDE.md 目标点别每帧重选)。到 stage → GATHER。
- **GATHER**：`bc.move(stage)` 停在 stage 待命（stage 是安全点，不掉血）。收到群 DIVE 令 → DIVE。
- **DIVE**：现有 `_raid_move_point` 那套矿后微操(游走/贴农民/风筝/避AA/cheap-kill/threat-flee) **保留**。
- **HEAL**：
  - 进入 HEAL 那一刻：**战术跳**(CD 好就 `bc(EFFECT_TACTICALJUMP, home)` 传送)；CD 没好才 `move(home)` 走（兜底，非常态）。
  - 到 home 范围内(`<_HOME_STOP_RADIUS`)：**不再发任何 move**（发一次 `bc.stop()` 或干脆这帧不下指令）→ BC 停住，SCV 来修（BcHomeRepairAct 派农民）。
  - 血满 ≥`_RECOVER` → RALLY。
  - **关键修正（用户强调）**：HEAL 不能每帧 `move(home)`——到家就 STOP，不然农民修的时候它还在挪，永远修不安稳。

**群决策（唯一的群级逻辑）：GATHER→DIVE 令**
- 每编队(did) 统计 **GATHER 状态**的健康 BC 数 `n_gather`（只算 GATHER，**不算 DIVE 中的前排**）。
- `n_gather ≥ commit_min` 或 GATHER 池等待 > `_GATHER_WINDOW_S` → 令这批 GATHER **全部**转 DIVE(一起扎、共享目标矿)。
- `commit_min`：**当前无 DIVE 前排** → 1（第一艘/首波到 stage 立即扎，**及时**，不等）；
  **已有 DIVE 前排** → 2（增援波等一个伴一起扎，**一起走**），或等 `_GATHER_WINDOW_S` 超时也走（防干等）。
- **前排不被拉偏的根本保证**：群决策只看 GATHER 池、只改 GATHER→DIVE；**对 DIVE 中的 BC 零操作**。
  新兵永远只进 GATHER 池、自己够数自己 DIVE，碰不到前排。

**recruit + reserve（每帧，不再 40s 一次）**
- 每帧：把所有「本编队应含、但还没状态」的 BATTLECRUISER **立即**赋 RALLY 状态 + `_reserve`（防 sharpy 拉走）。
- 每帧对**所有**本组 BC `_reserve`（独占）。Phase 0 若发现 reserve 真机无效(c)，改用更强的 claim（如 director 的 `_standing_order` 独占路径），别只靠 `roles.set_task`。

## 保留不动（验过的微操，只搬不改）
`_raid_move_point`(游走/贴农民/风筝) / `_dodge_spot`(避AA) / `_p1_threat_flee` / `_p1_aa_cheap_kill` /
`plan_harass_approach`(贴边路) / `_pick_group_zone`(选最软矿) / `BcHomeRepairAct`(派农民修) /
`_get_home_anchor`(回家落点)。这些是叶子函数，DIVE/RALLY/HEAL 里直接调。

## 删掉
`_joined_tags`/`_healing_tags`/`_approach_arrived`/`_approach_wp_idx`/`_rally_since`/`_group_posture`/
`_posture_since`/`_group_target_key` 等所有 latch + posture 双状态；`_approach_wp` 的 rally-gate/joined-latch
逻辑并进 RALLY(单纯沿路走到 stage，到了转 GATHER)。

---

## 验证（**必须 realtime + trace**，这是血泪教训）
- **Phase 0 realtime trace** 先定位真因（见上）。
- 实现后 **realtime 真局** + `BCRAID_TRACE`：
  - ①第一艘出现后**很快**进 DIVE、dist 到某敌矿线 <8（不再杵家 2 分钟）。
  - ⑤新 BC 出现/进 GATHER 那一刻，**前排 DIVE 中那几艘的 dist_home 不回升**（per-instance 按 tag 追，禁聚合）。
  - ⑦某 BC 血<40% → trace 有 `EFFECT_TACTICALJUMP` cast **且 telemetry 位置从矿线瞬移到 home**（验传送真生效，不是走）；到家后位置**不再变**(停住) + hp 回升(SCV 修)。
  - ⑥RALLY 路径点不穿地图中心（trace waypoint 坐标不落中路矩形）。
- **禁非实时自测下结论**（fast 不复现）。禁只看中间 trace，要看 telemetry 世界终态(位置/血)。
- per-instance 断言（每艘、每目标分开），禁 best/min-over-all 聚合掩盖。

## 评审处置（opus 独立评审 2026-07-04，6 条全采纳，本节为最终权威、覆盖上文冲突处）

### 必修1【头号真因，改 4 次没瞄准的】未侦察→无 stage/target→第一艘杵家
- 真因：未入队 BC 只有 `target_anchor != None` 才 move，否则原地待命（旧 line 624）。`target_anchor`/stage
  都依赖 `zone.mineral_line_center`，**要侦察到敌矿才有** → 第一艘出来时敌矿没揭开 → 站家里 2 分钟。
- **修**：加**兜底 stage**——敌方矿线未知时，用 `self.ai.enemy_start_locations[0]`（2 人图开局即知）推一个
  **临时 stage**（朝敌方出生点、贴边、主基外安全距），让第一艘 BC **立刻出门奔敌方主基外待命/侦察**；
  等真矿线揭开再把 stage/target 刷成真值。**永远有目的地，绝不因未侦察杵家。**
- **DIVE 无目标**（矿未揭开 / 全 score≤0 / 切矿间隙）：走 `_patrol_fallback` 在敌方半场巡逻揭视野，
  **绝不 `move(home)`**（旧 line 499-504 的 move(home) 是违反 Goal 3 的 bug，删）。矿一揭开就转打真矿。

### 必修2【切矿卡顿老坑】DIVE 内重定向别从 home 重算路径
- 贴边 approach（`plan_harass_approach`/`plan_edge_path`）**只在 STAGE 阶段用**（home→stage）。
- **DIVE 内换矿 = 短程直飞新矿后点**（起点用 BC **当前位置**，不用 home）——BC 已在敌方半场，不需要再贴边绕主基。
  旧代码路径 `key=(did,target_key)` 且 `start=home`，换矿从 home 重算 → DIVE 兵被塞回"从家出发"的 waypoint →
  往回飞找 home 侧点 = 换矿抽搐。**DIVE 态不跑全 approach 路径。**

### 必修3【Goal 7 传送养血】CD 没好退 stage 等，别走回家；STOP 用 hold_position + 验真
- HEAL 两条腿：**跳 CD 好 → `EFFECT_TACTICALJUMP(home)` 传送**；**CD 没好 → move 到 stage（安全点）等 CD**，
  CD 一好立刻跳。**绝不 move(home) 穿中路送死**（走穿全图 71s 必死）。
- 到家（`<_HOME_STOP_RADIUS`）：**发一次 `bc.hold_position()`，之后每帧不发任何指令**（BC 是 Reserved，不发=静止，
  最干净；hold_position 比 stop 更防推挤漂移）。**不每帧重发。**
- **验真生效（salvage 铁律）**：① trace HEAL 后**逐帧位置**，断言传送=位置**瞬移**（矿线→home 非连续）、STOP 后**帧间位置不变**；
  ② 记 `EFFECT_TACTICALJUMP` / `hold_position` 的 `_do_actions` **ActionResult**，非 Success = 被 SC2 拒（tactical jump
  有 ~2s channel，看"发起后短暂延迟再瞬移"，别因非同帧就误判）。

### 必修4【边界，防两处征兵新乱麻】director 是唯一 recruiter，不重写
- recruit 入组是 **director `_tick_recruit_watchers`（已每 tick、~1 tick 无延迟）**的活；reserve 也已每帧。
  设计里"recruit 不再 40s"是**不存在的靶子**，删。
- act **只**对 `group["tags"]` 里还没状态的 BC 赋 STAGE 状态 + 每帧 `_reserve`，**绝不在 act 里另写一套 resolve_selector 征兵**。
- 若 Phase 0 发现需要比 `roles.set_task(Reserved)` 更强的 claim（新增 facade 能力）→ **必须同步 `FakeFacade`+`_SharpyFacadeBase`+跑 audit**（CLAUDE.md）。

### 必修5【状态精简 + 确定性】
- **状态压到 3 个：`STAGE` / `DIVE` / `HEAL`**（RALLY+GATHER 合并成 STAGE：行为=`move(stage)` 幂等；
  "到没到"是谓词 `dist(stage)<_RALLY_RADIUS`，不存独立状态、去掉一层 latch）。群决策用 `n_ready = count(STAGE 且 dist(stage)<r)`。
- **GATHER/commit 等待改 per-BC 计时**（每艘进 STAGE 记进入时刻），不 per-(did) 池计时（新兵持续滴入会反复重置池计时→单艘饿死）。
- **stage 只随 committed 切矿更新**，别每帧追 `_pick_group_zone` picker 瞬时 best（picker 抖动→stage 跳→STAGE 兵追移动靶）。用现有切换滞回（1.3x+8s dwell）后的 committed zone 的 stage。
- **贴边路径函数确认**：`plan_harass_approach` 只绕 enemy_main 中心、非全程贴图边；memory `feedback_harass_drop_edge_approach` 要"贴地图边晚暴露"，可能该用 `plan_edge_path`。实现前定清 STAGE 用哪条，验证⑥（waypoint 不落中路矩形）测它。

### 必修6【前排不被拉偏的两个前置，钉死】
- **HEAL 触发必须纯自身**（hp<40% 或一帧爆发掉血），**绝不"群不利就整队 HEAL"**。
- 群决策**只读 n_ready、只写 STAGE→DIVE**，对 DIVE 中的 BC **零写入**（实现后 review 一遍"这帧对 DIVE 兵只调了 `_raid_move_point`、无其它写入"）。commit_min 动态 `1(无DIVE前排)/2(有前排)` 是同时满足 Goal 1+4 的最小机制，**保留**。

### Phase 0 判据补强（照不到真因就白跑）
- 候选加 **(d) 第一艘出现时 `target_anchor`/`stage` 为 None**（头号嫌疑，见必修1）。
- trace 每帧加：`target_anchor?` / `stage?` / `bc.orders`（BC 当前 order 目标点）/ `in_group?` / `reserved?`。
  **用"act 发的点 vs BC 实际 order"一次性区分**：order 指向 home 而 act 发了别的点=外部 plan 抢(c)；order 空/act 没发点=自己没目的地(d)。
- recruit 侧打一行：新 BC ready 那帧 director 有没有当 tick 放进 `group["tags"]`（验 ~1 tick、排除 recruit 延迟）。

## 不做（YAGNI）
- 不做家集结点。不做队形阵型。不重写 director recruit（已每 tick）。不做"新兵等伴再走"以外的复杂集结策略。
