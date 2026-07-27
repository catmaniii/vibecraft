# BC 群骚扰重构：群体决策只算 joined cohort（治「新兵拽回前排」）

> 用户 2026-07-04 真机反馈 + 认可的方案。#583 的 ② 群体协同虽实现了共享路径/rally/joined 闩锁，
> 但**posture（出击/回家）仍用全体 alive 算** → 家里新出的大件把前排一起拽回家。本文重构状态机。

**Goal**：①第一艘及时骚扰（不等）；②持续出兵不停；③已骚扰的 squad 一起走；④**前排 squad 绝不被家里
新出的大件拉偏/拽回家**。

**Tech Stack**：`src/vibecraft/bot/auto_combat/terran/bc_raid_act.py`（`GroupHarassAct._tick_group` 状态机）。

---

## 病根（telemetry + 代码确认）
- 真机 `match_20260704_105112`：前排 1-2 艘杵在家 (47,42) 从 t=308 到 t=441（~130s）不出门骚扰，
  直到 3-4 艘出来才整群往敌方 (127,119) 动。
- `_tick_group` line 281-284：`fit`/`able`/`threshold` 全部从 **`alive`（全体）** 算。
  2 艘时 `threshold=2`，STAGING→HARASS 要**两艘都满血(fit)**；一艘残血/在外就全体等家；
  **每出一个新 BC → alive+1、threshold 变、fit 又不够 → 群体反复被重置回"等齐" → 前排被拽回家**。

## 核心思路（用户认可）
**病根不是"点太少"，是"谁算群成员"**。把群体决策从「全体 alive」改成「**只算已到前方集结点、并入队的 cohort**」。
配一个**前方集结点 = 入队线**（敌矿外安全点，即现有 `plan_harass_approach` 的 stage）：过了这条线才算 squad、
才参与群体决策；没过线的新兵/养血兵只是"在赶来"，不干扰前排。**砍掉家集结点**（冗余 + 让新兵在家等=延迟出门，
与"及时骚扰"冲突）。

---

## 状态模型（每艘 BC 两种身份）

```
未入队 (not in _joined_tags)：新出 / 刚养好归来
  ├─ 血 > _RETURN_BAR(0.40) → 自主贴边(plan_harass_approach 自身位置起) 赶去【前方集结点(stage)】
  │                            【不受 squad 决策门控】——squad 在扎矿它也照样往前方点赶
  │                            到 stage 的 _RALLY_RADIUS 内 → 置 _joined_tags = 入队
  └─ 血 ≤ _RETURN_BAR → 回家养（healing）；养满(≥_recover_hp_ratio)→ 清 healing，仍未入队 → 重新赶前方点

已入队 squad (_joined_tags ∩ alive ∩ 非 healing)：在前方集结点 / 矿线
  squad 健康分**只算 squad 成员**：
    fit_sq   = squad 中 血≥_SALLY_HP(0.95)
    able_sq  = squad 中 血>_RETURN_BAR
    thr_sq   = min(len(squad), max(2, len(squad)-1))   # 1→1,2→2,3→2  (单艘=1)
  posture(只两态，砍掉"全队回家 STAGING"):
    ├─ DIVE (扎矿骚扰): fit_sq ≥ thr_sq → 整个 squad 沿共享路径(did-key)扎进矿线、一起走/一起转移
    └─ HOLD (前方集结): fit_sq < thr_sq → squad 在【前方集结点】集合等（等更多入队/养好），够数再一起 DIVE
  个体脱队: squad 内某艘血 ≤ _RETURN_BAR → 从 _joined_tags 移除 = 脱队回家养（healing），养好重新赶前方点入队
```

**关键不变量**：`fit/able/threshold` 只从 **squad** 算，**绝不含未入队的新兵/在途兵** → 家里新出的大件
永远不影响前排 squad 的 DIVE/HOLD 决策 → **前排不被拽回**（目标④）。

## 怎么达成 4 目标
| 目标 | 机制 |
|---|---|
| ①及时骚扰 | 每艘未入队 BC **自主**贴边赶前方点(不等群体)、到线入队；单艘 squad thr=1 立即 DIVE 单独扎 |
| ②持续出兵 | recruit-claim 不动，新兵照出、照自主赶前方点 |
| ③一起走 | 入队 squad 走**共享 did-key 路径** DIVE/转移；rally gate 在前方点先收紧(所有 squad 成员进 _RALLY_RADIUS)再一起扎 |
| ④前排不被拽回 | **posture 只算 squad**；新兵/在途兵不算 → 前排 squad 的 DIVE 不受新兵影响，持续骚扰 |

---

## 与现有实现（#583 sonnet 版）的 delta
现有已具：`_joined_tags` 闩锁、rally gate、共享 did-key 路径、per-tag idx/arrived。**主要改 3 处**：
1. **posture 从 squad 算**（不是 alive）：`fit`/`able`/`threshold` 用 `squad = [bc for bc in alive if bc.tag in _joined_tags and bc.tag not in healing]` 算。
2. **未入队 BC 的移动与 squad posture 解耦**：未入队且健康 → **无条件走自身 edge 路径赶前方点**（不看 squad 是 DIVE 还是 HOLD）。现在是"posture==HARASS 才 approach"，要改成"未入队健康就赶前方点"。
3. **posture 砍成 DIVE/HOLD 两态**（去掉"全队回家 STAGING"）：squad 不整体回家，只个体脱队养血；不够数就 HOLD 在前方点（不是回家）。
   - HOLD 时 squad 成员 move 到 stage（前方集结点）等，不 move home。
4. **空 squad**：`_joined_tags ∩ alive ∩ 非healing` 为空（全新兵/全养血）→ 无 squad、无 DIVE/HOLD，等有人入队。
5. **首次入队门**：单艘首舰要能入队——它未入队时自主赶前方点、到 stage 入队 → squad=1 → DIVE。链路自洽（前方点是安全点，赶过去不送）。

## 保留不动（#557/#561/#580/#583 验过）
per-BC jump-heal / dodge / _p1_threat_flee / cheap-kill / plan_harass_approach 本身 / 共享路径+剪切谓词 /
BcHomeRepairAct（③修理）/ ①单艘特判(其实并入 squad thr=1 自然成立)。

---

## 测试
### 单测（test_bc_raid_act.py）
- posture 只算 squad：3 艘在家未入队 + 1 艘入队 → squad=1、thr=1、DIVE（不被 3 个未入队拽成 HOLD）。
- 未入队健康 BC：squad 在 DIVE 时，未入队 BC 仍走自身 edge 赶前方点（不被"回家"）。
- 个体脱队：squad 内一艘掉 <40% → 移出 _joined_tags（healing）→ squad 缩小、posture 重算。
- DIVE/HOLD：squad fit<thr → HOLD 在 stage（move stage 不 move home）；fit≥thr → DIVE。
- 空 squad 不崩。

### 真局（hard_bc_game.py + BCRAID_TRACE）—— 外部终态 per-instance
- **①第一艘及时**：第一艘出现后**很快** dmain 下降到 <8（不再杵家 130s）；posture trace 单艘即 DIVE。
- **④不拽回**：新 BC 生成时刻，前排已 DIVE 的 squad 成员 dmain **不回升**（没被拽回家）——per-instance 看前排那几艘。
- **③一起走**：joined squad 转移矿线时抱团（per-instance squad 成员间距，排除未入队/养血）。
- 新兵到前方点才入队（trace joined 置位时刻的位置 ≈ stage）。

## 评审处置（opus 独立评审 2026-07-04，3 必修全采纳）

### 必修1（headline）：posture 保留滞回双门（别写成单阈值）
上文「状态模型」把 posture 写成单阈值 `fit_sq≥thr` 是**错的**——正在扎矿的 BC 几乎从不 ≥0.95，单阈值会一挨打
就翻 HOLD、骚扰彻底失效。**必须保留 #557 现有滞回**（在 squad 上）：
```python
squad = [bc for bc in alive if bc.tag in _joined_tags and bc.tag not in healing_tags]
fit_sq  = [bc for bc in squad if hp >= _SALLY_HP(0.95)]
able_sq = [bc for bc in squad if hp >  _RETURN_BAR(0.40)]
thr_sq  = min(len(squad), max(2, len(squad)-1))   # 单艘=1
if len(squad) >= 2 and len(able_sq) < _ABS_RETREAT_FLOOR:   # 减员保护改吃 squad
    want = "RETREAT"
elif prev != "DIVE":
    want = "DIVE" if len(fit_sq) >= thr_sq else "RETREAT"   # 进 DIVE 用 fit(满血)
else:  # prev == "DIVE"
    want = "DIVE" if len(able_sq) >= thr_sq else "RETREAT"  # 留 DIVE 用 able(>40%)
```
加了滞回后「入队瞬间扰动前排」不发生：1st 残血在矿线 + 2nd 入队 → prev=DIVE、able_sq=2≥thr(2) → 保持 DIVE，1st 不被拉回（评审推演确认）。

### 必修2（headline）：砍掉「HOLD-在stage干等」→ 打不动就回家养血（补集体养血路径）
只有两态：**DIVE**（扎矿，rally gate 自动在 stage 聚拢再扎）/ **RETREAT**（打不动 → squad 成员**脱队回家养血**，
变回"未入队-healing"，养满 ≥0.95 再贴边赶 stage 重入队）。**没有"HOLD 在 stage 干等"这个中间态**
（那会让 41-94% 的成员既不 DIVE 也不养血、被磨死/卡死——正是原"全队回家 STAGING"在防的，不能裸砍）。
- 成员天生 fit 入队（要么出厂满血、要么养满 ≥0.95 才回来）→ FORMING 的 squad 天然全 fit → 立即 DIVE；
  只有 DIVE 中挨打才掉血 → able_sq<thr 时整队 RETREAT 回家奶。→ 无"60% 卡 stage"死角。
- "集合再一起扎"靠 rally gate（DIVE 时在 stage 收紧），不靠单独的 HOLD 态。

### 必修3（整合）：离开 DIVE 必须 clear `_approach_arrived` + 重置 idx
`_approach_arrived[tag_key]` 是 sticky 闩锁；posture 翻出 DIVE（→RETREAT）时若不清它，BC 继续赖在矿线
near-micro（空操作）。**RETREAT 时对该 squad 成员 clear `_approach_arrived` + 重置 `_approach_wp_idx` + discard `_joined_tags`**
（复用 jump 回家 line 394-399 那套清理）→ 它变回未入队、move home 养血。

### 采纳的建议（非阻塞）
- **zone_switch 时 discard 该 group 的 joined**（`_joined_tags` 是全 act 单集合、不分 target_key → 换矿后旧 joined 离新 stage 远 → rally all_close 永 False → 每次换矿吃 4s 停顿）。换矿时清 joined，让成员在新 stage 重入队。
- **测试补 2 case**：①join-moment（某 tag joined 置位那帧、另一个已 dmain<8 的 tag 的 dmain 不回升）；②集体养血/防磨死（持续挨打 → squad 要么 HP 回升要么 dmain 涨回 home，不允许长期卡 stage 且均血 41-94%）。
- **未入队单兵赶路被前压 AA 逮**：jump 自保（posture 分支前无条件对所有 alive 执行）覆盖多数 → 列 monitor、真机看，不加"squad 退却时暂停新兵赶路"（那会重新耦合、违背本次目标）。

## 不做（YAGNI）
- 不做家集结点（冗余 + 延迟）。可选开关"新兵等伴再走"暂不做（优先及时）。
- 不做队形/阵型。
