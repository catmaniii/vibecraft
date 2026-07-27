# 凤凰低地路由设计（F122 真解，2026-07-26）

> 目标：凤凰接近/撤退/多矿拉扯的路线走**低地**（敌方高地台面以外），不穿敌高地——
> 落地图谱 F122 的 low-ground routing，使 D94"安全多矿拉扯"成立（I50）。

## 1. 根因回顾（图谱）

- **F101/F142**：现 `plan_avoid_path` 只按几何绕敌方基地圆心，不读地形 → 路线穿敌高地台面
  （真机 trace `vis=1` 占 **55%**，一半以上时间在敌建筑视野/高地上）。
- **F122**（已证伪调半径路线）：调避障半径（r_avoid=16 → 三族 KPI 0；plateau 半径 → vis 更差、
  detour 1.1→2.2 爆炸）**不解决**——center+半径绕弧的本质是弧仍跨台面。真解 = **按 terrain_height
  走低地的栅格路由**（low-ground routing）。
- **I50**：D94 多矿拉扯要提得分，**前提**是拉扯/转移路线走低地；否则暴露拉扯送死（F142 实测腰斩）。
- **F143**：micro 层 flee 用自算 flee_wp 覆盖 act 的 approach_wp → act 层单独改撤退路由不生效，
  撤退要真走低地须 micro 层同改（本设计 Phase 2）。

## 2. 方案：敌方高地代价栅格 + A\* 选路

### 2.1 静态数据（F113/F102，一局一算，D79 强调别每帧重算）
- `get_terrain_height(Point2)->float`、`in_pathing_grid(Point2)->bool`（极性已验 F102，不自己读原值）。
- `h_enemy = get_terrain_height(enemy_start)`（敌方基地台面高度基准）。

### 2.2 静态地形代价栅格（precompute once per game，D79）
在 `playable_area` 上按整数格采样，每格标一个**静态**cost：
- **敌方高地格**（= 要避的）判定 = `terrain_height(cell) >= h_enemy - CLIFF_MARGIN` **且** `cell 在敌方半场`：
  - **敌方半场** = `dist(cell, enemy_start) < dist(cell, my_start)`（离敌 start 比离我 start 近）。**纯静态、
    无需 BFS**。
  - **评审必改①**：原设计用 `enemy_ground_reachable ∩ height` 是**错的**——`bfs_ground_reachable`
    从 enemy_start flood 会淹没整个连通地面（双方主基经斜坡全连通，>14400 格），我方高地也在敌可达集里
    → 交集退化成纯高度、还误罚自家台面。改用"高地 ∩ 敌方半场"才真正只针对敌方台面、不碰自家高地。
  - 空军能飞任何格：**非 pathable（悬崖/缺口）对空军是低代价好格**（地面够不到=安全），不惩罚。
- `static_cost(cell) = HIGH_PENALTY`（敌高地格） 或 `1`（其余）。
- **缓存 by map**（terrain 静态，F113）；一局第一次用时算一次，之后 O(1) 查。

### 2.3 A\* 选路（静态地形 cost + 动态 AA/军队惩罚叠加）
- **评审必改②：补回 AA/军队避让**（纯地形 A\* 丢了现有 `_avoid_enemy_centers` 的静态防空 D65 + 漫游军
  D86，会让凤凰从炮台/追猎头上低地穿过）。查询时在静态栅格上**叠加一层动态惩罚**：
  `cost(cell) = static_cost(cell) + AA_PENALTY × [cell 距任一静态防空/漫游对空军中心 < AA_R]`。
  AA/军队位置集小，不破坏静态栅格缓存（静态栅格照缓存，动态项按查询叠加）。
- 8 连通（对角 √2），路径代价 = Σ 进入格的 cost。`start = squad_center`，`goal = 目标点`
  （approach=矿后 behind；dodge=悬崖口袋 dp）。
- min-cost 路径天然**串低地格、避开 AA、只在省大弯时才穿短段高地**——A\* 平衡 length vs penalty，
  不会像 F122 的弧那样一律绕 2.2x。
- **评审改⑥：空军全格连通恒有解**，不存在"无解"。guard = ①`max_expand` 扩展节点上限（防病态卡顿）；
  ②**运行时 detour 守卫**（评审改⑤，照 `plan_air_path._AIR_MAX_DETOUR=1.35`：A\* 路径长 > 直线 ×
  MAX_DETOUR → 弃用回退）。超 max_expand / 超 detour → **回退现 `plan_air_path`(snap)**。
- 简化 waypoint（去共线 `_dedup_collinear`），**缓存 by goal + 粗量化敌军位**（评审改④：**不按 start
  量化**——照现有 `_approach_waypoint` 纪律，算一次整条路径、之后只推进 index，start 移动不触发重算；
  A\* 只在 goal/军队变时跑，非每帧）。
- 坐标取整/偏移**与 `bfs_ground_reachable` 的 `round(float(...))` 约定逐字一致**（评审风险：off-by-one，
  单测专门盖）。

### 2.4 整合（评审必改③：理清与 snap 版 `plan_air_path` 的关系）
- 新函数 `terrain_harass.plan_lowground_path(start, end, get_terrain_height, in_pathing_grid,
  playable_area, enemy_start, my_start, avoid_pts, cost_cache) -> list[Point2]`。
- **`plan_lowground_path` 为主，snap 版 `plan_air_path` 降为回退**（超 max_expand/detour 时用），
  **不并存两个地形路由器当死代码**：现 `_approach_waypoint` 调的是 `plan_air_path`（2026-07-25 独立
  评审否掉全局 A\*、改的局部贴崖 snap，实测 vis 仍 55% 不够）；本方案 supersede 它当**主选路**，
  snap 版只在低地 A\* 超限时兜底。
- **Phase 1**：接入 `_approach_waypoint`——base 主选路换成 `plan_lowground_path`（末段仍 stage→behind
  矿后切入，保 D54 从矿后切）；`avoid_pts` 传现有 `_avoid_enemy_centers`（静态防空+漫游军中心）。
- **Phase 1**：dodge 藏匿的 `return dp, dp` 也让 act 走 `plan_lowground_path` 到 dp（act 层）。
- **Phase 2**（F143，本设计不做，留接口）：micro `_flee_waypoint`/`_solve_unit` flee 分支也吃低地路由
  → 撤退真走低地。需把 act 的低地 waypoint 喂进 micro flee 选点。**注（评审⑥）：Phase 1 上线后撤退段
  vis 仍会差（micro flee 覆盖，F143），验收只统计 approach 段 vis，别误判。**

### 2.5' 不采纳：从 goal 预算 Dijkstra 距离场（评审强建议④）
评审建议用"goal 反向 Dijkstra 距离场 + 梯度下降"替代 per-start A\*。**暂不采纳**：本设计按现有
`_approach_waypoint` 纪律缓存（键 goal+量化军队、算一次推进 index），A\* 已**非每帧**（只 goal/军队变时跑）
→ 零每帧 A\* 的目标已达到。距离场是更优但更复杂的实现（且动态 AA 叠加会打破纯静态距离场），YAGNI。
留注记：若真机 profile 显示 A\* 重算卡顿，再上距离场。

### 2.5 参数（初值，自测调）
- `CLIFF_MARGIN ≈ 8~12`（F114 实测悬崖落差 ~90，取保守判"和敌基同台面"）。
- `HIGH_PENALTY`：中等（如 **8**）。太大 → 绕大圈（重演 F122 教训）；太小 → 穿高地。按 vis%+detour+得分调。
  **评审⑦：先 commit checkpoint 再调此值**（memory 别追噪声指标）。
- `AA_PENALTY / AA_R`：静态防空/漫游军避让惩罚 + 半径（初值 penalty≈6、R≈8，≈ AA 射程 6-7 + buffer）。
- 栅格步长 = 1 格（开销大就降 2 格 = 3600 节点 4x 快）。

## 3. 验证（F122 教训：vis 降 + detour 不爆 + 得分不退，三条都过才算成）
- **单测**：合成 terrain grid（中间一块高地台面 + 敌可达集），验 ①路径绕开高地格、②低地格占比高、
  ③无解回退 plan_air_path、④缓存命中不重算。
- **真机**：`build_acceptance phoenix_2base` 三族各 12 局 + `VIBECRAFT_PHOENIX_TRACE=1`：
  - `posture=approach` 段 `vis=1` 占比应从 **55% 大降**（55% 是**当前 snap 版 `plan_air_path`** 下量的
    口径，评审⑦确认——本方案对比的是已部署基线，非未部署，改善数不高估）；
  - **新增指标（评审⑧）**：路径**落敌高地格比例**（直接对应优化目标，比"建筑视野 vis"更准——vis 是
    `_in_enemy_building_vision` 建筑 11 格，与"高地格"相关非等价）。PHOENIXPATH trace 加打此比例。
  - `detour`（路径长/直线长）< ~1.4（不绕大圈）；
  - 骚扰得分 vs I49 基线（神 -3.5 / 虫 -7.5 / 人 -16.9）**不退**（目标：借低地路由把损失压下去、拉扯能持续）。
- **判据**：①vis/高地格 段统计只算 approach（打矿时贴 mine 必 vis，不算路线暴露）；②**报均值+离散/CI**
  （评审⑧+memory 别追噪声指标，12 局仍有方差，别用单跑波动判回归反复改）。

## 4. 风险 / 未决
- **A\* 性能**：缓存代价栅格 + 路径缓存 + `max_expand` 上限兜底；`on_step` 全局 try/except 兜底不崩。
- **高地阈值**（terrain_height 相对敌基）：多层地图/斜坡边界易误判。**D80：先跑一次 terrain 探针**
  （`scripts/phoenix_terrain_probe.py` 已存在）打印敌基高度 + 高地格分布确认阈值，别猜（F102/salvage 教训）。
- **敌可达集随扩张变化**：敌开新矿后 reachable 扩大；一局一算可能过时。缓解：低频重算（如每 60s）或
  只用 terrain_height（不交 reachable）做保守版——评审定。
- **末段矿后切入必然 vis**：目标口袋在敌台面边缘下方，approach 末段贴 mine 必被看到，属正常（在打矿）。

## 5. 不做（YAGNI）
- 不做全图 nydus+phoenix 共享 `bot/terrain.py` 大重构（D79 的远期）——先在 `terrain_harass.py`
  加低地路由，跑通再谈抽共享。
- 不做 Phase 2 micro flee 低地路由（先验 Phase 1 approach 的 vis 降不降，值不值得再做 Phase 2）。
