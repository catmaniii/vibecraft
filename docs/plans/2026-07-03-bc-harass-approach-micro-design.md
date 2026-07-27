# BC 群体骚扰：接近路径 + 到位微操重构（三层优先级）

> 用户 2026-07-03 拍板。修 #580 遗留：BC 骚扰用了绕整圈的 `plan_edge_path`（对角出生要绕
> ~2 条边、100+ 游戏秒才到，骚扰断续），且到位后落点太靠后打不到农民。
> 本文重构为「直奔 + 必要时贴敌方主基地高地边缘偷摸 + 到位贴脸 move-attack / 被赶卡射程游走」。

**Goal**：BC 群体骚扰按三层优先级行动，尽快到达目标矿的「矿后点」，去程尽量晚被发现，到位后
最大化杀农民、遇地面威胁不硬顶。

**Tech Stack**：`src/vibecraft/bot/drop_path.py`（新增避障接近函数）、
`src/vibecraft/bot/auto_combat/terran/bc_raid_act.py`（`GroupHarassAct` 接入 + 微操调参）。

---

## 0. 三层优先级（真理源，实现对照这个）

| # | 目标 | 硬/软 | 含义 |
|---|---|---|---|
| **①** | 到达目标矿的**「矿后点」**（矿线背基地一侧的锚点）| **硬**（永不为②③牺牲）| 终点固定；到不了不行 |
| **②** | 去程**别被敌方主基地建筑/兵种提前发现** | 软（不牺牲①）| 手段=贴敌方主基地**高地边缘**绕行，从矿**背后/外侧**切入 |
| **③** | 到位后杀伤最大化 + 不硬顶 | 软（不牺牲①保命）| 无威胁→贴农民 **move-attack**；有地面威胁→**卡其射程外**、借高地/空军在**一矿矿后↔二矿矿后**游走 |

关键认知（用户强调）：**即使目标就是敌方主矿本身，也不能直穿**——「我家→主矿矿后点」直线会从
主基地头顶压过，第一时间暴露、农民立刻躲。要绕高地边缘、从矿背后贴进 → 对方反应窗口最短。

---

## ② 接近路径：`plan_harass_approach`（drop-path 式垂距避障，替换 plan_edge_path）

### 思路（= 空投 `plan_drop_path` 逻辑）
默认走直线；只有当直线会从敌方主基地（视野半径 R 内）穿过时，才插一个拐点把路径**沿垂直方向
推到刚好擦着 R 边缘**，绕过去。绝不绕整圈。

### 保证「从矿背后/外侧切入」——场外集结点 stage
直接对「矿后点」做避障会退化（矿后点紧贴基地，避障与到达自相矛盾）。改为两段：

1. **算场外集结点 `stage`**：目标矿**矿线中心 `ML` 沿「远离其基地 `TH`」方向**往外推
   `_STAGE_OUT`（≈ 8 格，落在矿线外侧的开阔地/高地边缘外）。
   `stage = ML + normalize(ML − TH) * _STAGE_OUT`，clamp 进 playable。
   → stage 在矿的**外侧/背后**（field 侧），从 stage 切进矿后点必然是「从背后来」。
2. **`start → stage` 走垂距避障**（避 `enemy_main` 中心，R=`_MAIN_AVOID_R`）：
   因 stage 在主基地**背面**（相对 start 在主基地另一侧），直线 start→stage 会穿主基地
   → 垂距避障把路径**沿侧向推开、擦着主基地视野半径绕过去** = 贴高地边缘绕行。
3. **末段 `stage → 矿后点`**：短距、从外侧直插（此时已在矿旁，暴露也没关系，①优先）。

最终 waypoint 串：`[start, …避障拐点…, stage, 矿后点]`。

### 函数签名（drop_path.py，无状态，纯几何，好测）
```python
def plan_avoid_path(start, end, avoid_centers, playable_area,
                    r_avoid=15.0, push=5.0, max_depth=3) -> list[Point2]:
    """start→end 直线；遇 avoid_centers 里任一中心距线 < r_avoid → 垂直推拐点 C 绕过，
    递归细分（max_depth 防 loop），C clamp 进 playable。复用/抽出 plan_drop_path 的
    project_point_onto_segment + 垂直 push 逻辑（那份已被 drop 用真局验过）。"""

def plan_harass_approach(start, mineral_center, townhall, behind_point,
                         enemy_main_center, playable_area) -> list[Point2]:
    """② 骚扰接近：算 field 侧 stage → plan_avoid_path(start, stage, [enemy_main]) → 末段 behind。
    返回 [start, …, stage, behind_point]。"""
```

### 常量
- `_MAIN_AVOID_R = 13.0`（主基地建筑视野约 9-11 + buffer；比空投 15 略小，贴得更紧、路更短）
- `_STAGE_OUT = 8.0`（矿线外侧集结点外推距离）
- `_APPROACH_PUSH = 5.0`、`max_depth = 3`（沿用空投）

### 接入 GroupHarassAct
- `_edge_path_wp` → 改名 `_approach_wp`，内部调 `plan_harass_approach`，**一次锁定缓存**
  （key=(tag, target_key)），中途幂等重发同一串（CLAUDE.md 强规则，原逻辑保留）。
- `plan_edge_path` 从 BC 路径**退役**（drop_path.py 里函数留着但 BC 不再调；避免误用可加注释）。
- `enemy_main_center` = `_enemy_zone_by_rank(0).center_location`（已有、确定性排序）。
- `mineral_center/townhall/behind_point` 来自 `_harass_geom(zone)`（已返回矿线/基地/矿后）。

---

## ① 矿后点锚点：贴近矿线（大件射程能罩住农民）

- `_BEHIND_MINERAL_OFFSET` 3.5 → **0.5**（几乎贴在矿线上；大件地面射程 6，农民就在矿线上，
  锚点贴矿线 → 到位后 BC 在射程内能覆盖整条矿线农民）。
- 矿后点仍在「矿线背基地一侧」（`+dir(ML−TH)*offset`），保证是「矿后」不是「矿前压基地」。

## ③ 到位微操（进 airspace 后，`_raid_move_point` 的 near 分支）

现状已有：`_nearby_worker_center`（贴农民质心）+ sweep（沿矿线来回）+ `_p1_threat_flee`
（卡对空威胁射程外）+ cheap-kill。本次调整：

1. **贴矿射程锚点**：near 分支 base 优先 `_nearby_worker_center`（矿线农民质心），无农民时用
   矿后点（已贴矿线）。
2. **无威胁 → move-attack**（边移动边攻击）：安全时对农民质心用 **`bc.attack(worker_centroid)`**
   （a-move，动中开火追农民），替换纯 `bc.move`。**判据**：`_nearby_threat`（能打空的地面/空中
   战斗单位）为 None 且非 healing/dodge。
   - 风险控制：attack 目标恒为「农民质心」这一个缓存点，不 attack-move 到敌方基地深处（避免
     over-commit）；每帧幂等重发同一质心。
3. **有地面威胁 → 卡射程外 + 矿间游走**：
   - `_p1_threat_flee` 已算「出所有对空威胁射程」的 flee 点，保留（这是「卡射程外」）。
   - **新增矿间游走**：当当前矿 rank 附近对空威胁 DPS 超阈值（`_anti_air_dps_near > _P1_THREAT_DPS_FLOOR`）
     且持续 `_ROAM_TRIGGER_S`（≈3s）→ 把该 group 目标矿切到**相邻 rank**（主矿↔二矿：0↔1）的矿后点，
     borrow scored/patrol picker 的滞回避免抖。人在飞过去途中仍走 `_p1_threat_flee` 保命。
   - 效果：一矿被地面部队赶 → 游到二矿矿后继续骚扰，始终卡在地面对空射程外。

---

## 数据流 / 每帧决策（到位后 per-BC）

```
healing? → 回家修（最高优先，不变）
dodge AoE? → 闪避（不变）
burst/jump 阈值? → 跳回家（不变）
posture==STAGING → 回 home（不变）
posture==HARASS:
    far(离目标矿中心 > _APPROACH_DIRECT_FROM_ZONE)?
        → _approach_wp（plan_harass_approach 贴主基地边缘接近）   ← ② 新
    near(已进 airspace):
        地面对空威胁 DPS 高 & 持续? → 触发矿间游走(切相邻 rank) + _p1_threat_flee 卡射程外   ← ③ 新
        有对空威胁(未触发游走)? → _p1_threat_flee / cheap-kill（不变）
        无威胁? → bc.attack(worker_centroid) move-attack 贴农民杀   ← ③ 新(move→attack)
```

---

## 测试计划

### 单测（tests/unit/test_bc_raid_act.py + 新 test_harass_approach.py）
- `plan_avoid_path`：①直线不撞→原样返回 [start,end]；②撞中心→插拐点、拐点距中心≈r_avoid、
  路径不再穿中心；③max_depth 兜底不 loop；④clamp 进 playable。
- `plan_harass_approach`：stage 在矿背基地外侧（`dist(stage,TH) > dist(ML,TH)` 且方向背 TH）；
  末点=behind_point；start 在主基地另一侧时中间有避障拐点、整条不穿 enemy_main 的 R。
- 微操：无威胁 → 发 `attack`（不是 move）到农民质心；有地面对空威胁持续 → 目标 rank 切到相邻。
- 锚点：`_harass_geom` 的 behind 距矿线 ≈ 0.5。

### 真局 trace 验（scripts + hard_bc_game.py，BCRAID_TRACE）
断言**世界终态**（不只 trace）：
1. **①到达**：每艘 BC 曾 `dmain`(到目标矿) < 8（per-instance，全部 BC，不用聚合 min）。
2. **②贴边不直穿**：接近段 BC 轨迹**不进 enemy_main 中心 `_MAIN_AVOID_R` 内**直到末段；
   且总路程明显短于旧 plan_edge_path（对比 dmain 下降速度）。
3. **③杀伤**：到位后敌方 worker 计数随时间下降（telemetry enemy worker count，终态铁证，
   非 trace）；有地面威胁时 BC 与最近对空威胁距离 ≥ 其射程（卡射程外）。
4. 战术响应：注入地面 AA → BC 切相邻矿（rank 变化 trace）。

判据全过才算完（外部终态黑盒门，salvage 复盘纪律）。

---

## 评审处置（opus 独立评审 2026-07-03，全部采纳）

- **#1 致命结构缺陷**：stage/behind/避障拐点（距 TH ~7/14.5/18）全部 < `_APPROACH_DIRECT_FROM_ZONE=24`
  → near-micro 在 24 环就接管、丢弃 approach waypoint → ②「从背后切入」永不执行。
  **改**：near/far 门废弃「距中心<24」判据；改为 **approach waypoint 链驱动** —— `_approach_wp` 建
  `[start,…避障拐点…,stage,behind]` 串，逐点推进；**只有走到最后一点 behind 且 `dist(bc,behind)<_ENGAGE_RADIUS`
  才置 `_approach_arrived[ck]=True`**，此后才放行 near-micro。加 `_approach_arrived` 闩锁（sticky，防到位后
  追农民跑远又被判「未到达」反复重接近抖动）；target_key 变 / jump 回家 时连带清。
- **#2 push 方向退化**：`start→stage` 穿主基地中心 = 本场景**常态**（非边界），退化分支固定 90° 旋转可能推向
  地图中央（更暴露）。**改**：`plan_avoid_path` 退化分支从 `+perp`/`−perp` 两个候选拐点里选**离地图中心更远**
  （更贴边）那个；补单测断言。
- **#3 砍③独立游走 + attack 用 swept 点**：安全矿评分 `score=workers−8*aa_dps`+1.3x+8s 已天然把高 AA 矿切走，
  新增「矿间游走状态机」与之重复且双写抖动 → **砍掉**，靠 `_p1_threat_flee`（卡射程外）+ 现有评分器自然切矿。
  move→attack 的目标用**已叠 sweep 的点**（`bc.attack(swept_point)`），保留 #561/#557 验过的 sweep 微动，别静立送靶。
- **#4 兜底**：zone 无 `mineral_line_center` / 该矿无敌方结构农民 → 视 score<=0 走 patrol（`_harass_geom` 异常已返回 None）。
- **#5 真局判据补方向门**：BC 首次进矿线 airspace（`dist(bc,ML)<8`）时断言 `dot(bc−ML, ML−TH) > 0`
  （BC 在矿线背 TH 一侧）——这条才真验「从背后」，#1 未修时会 FAIL 逼出 bug。别只 grep trace 行数。
- **分矿隐蔽性不保证**（记录）：avoid 只含 enemy_main；分矿「矿后」方向不一定朝地图边缘，②对分矿是降级尽力，
  不保证最隐蔽。主矿（贴地图角）才有保证。
- **常量标注**：`_MAIN_AVOID_R=13`（主基地视野 9-11 + buffer）属**调参常量，UNVERIFIED**，非事实引用。

## 不做（YAGNI）
- 不做多矿同时分兵（群仍抱团一个目标，游走是整群切）。
- 不做复杂高地 pathfind（用 playable clamp + 垂距避障近似「贴高地边缘」，真局验够用即可）。
- 不动 STAGING/健康分状态机 / jump 回血 / dodge（#557/#561 验过，不改）。
