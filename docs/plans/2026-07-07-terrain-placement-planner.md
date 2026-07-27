# 地形建模落点规划器 TerrainPlacementPlanner（2026-07-07 用户）

## 目标（用户）
建筑落点**从地形模型离线规划好确切格子**，农民只去**执行**（走到那格 + 建），**不是农民到了现场再随机
find_placement 试**。要能可靠地把建筑"卡在高地边缘 / 卡着矿区背后 / 紧凑成簇"而不失败。

**这是通用能力**（不只修 proxy_4rax）：任何"要把 N 个建筑放在某区域"的需求都走它。彻底解决 #590
（proxy 3 SCV 到点却建不全 3 兵营）的根因 = 现场临时试 + 紧凑簇互相挡。

## 根因回顾（#590 为什么崩）
上一版 SCV 到 proxy 后**逐 slot 独立 `find_placement`**：每个 slot 单独可建，但**三个兵营footprint 一起 +
先建的挡后建的落点/导航 → 后两个建不了**（timeout，无死亡）。**find_placement 是"现场单点可建"，没建模
"整簇一起放得下 + 每个都还走得到去建"。**

## 用到的 SC2 静态地形模型（已核实存在，`game_info`）
- **`placement_grid`**（PixelMap，1=可建 0=不可建）：某格能不能放建筑地基。
- **`pathing_grid`**（PixelMap，1=可走 0=不可走）：某格单位能不能站/走。
- **`terrain_height`**（PixelMap，0-255）：高度，判高地/斜坡/崖边。
- 资源位置：`mineral_field` / `vespene_geyser`（挡路、当掩护）。

## 核心：TerrainPlacementPlanner（离线规划整簇）
新模块 `src/vibecraft/bot/placement_planner.py`（纯函数 + 少量 async 查询），核心 API：

```
async def plan_building_cluster(
    ai, anchor: Point2, building: UnitTypeId, count: int,
    footprint: int = 3,        # 3×3 兵营
    max_search_radius: float = 14.0,
    require_reachable_from: Point2 | None = None,  # SCV 起点，验可达去建
) -> list[Point2] | None:      # 返回 count 个确切落点中心；放不下返回 None
```

**算法（离线，一次算完全部落点）**：
1. **可达集（reachable set）**：从 `require_reachable_from`（SCV 起点/proxy 附近）在 `pathing_grid` 上
   **BFS/flood-fill** → 得到"农民走得到"的格子集合。（或退化用 `query_pathing` 逐点，但 BFS 一次更省。）
2. **候选 footprint 位**：anchor 周围由近及远螺旋/网格扫。
3. **贪心逐个放（关键：考虑组合 + 建后仍可达）**：维护 `occupied`（已放建筑 footprint + margin 格子集）。
   对每个待放建筑，从候选里找第一个满足：
   - **a. 放得下**：footprint 全部 `placement_grid==1` + 无 mineral/geyser/已见单位 + **不与 `occupied` 重叠**（含 1 格 margin，别贴死）。
   - **b. 农民建得到（组合可达）**：footprint 至少 1 个相邻格 ∈ 可达集 **且不在 `occupied` 里**（先建的没把它的进场路堵死）。
   - 放下 → 把它 footprint + margin 加进 `occupied`，继续下一个。
4. **建后整体复核（防互相封路）**：全放完后，在 `pathing_grid − 所有 footprint` 上，**逐个建筑复核**其相邻格仍
   有可达格（没有哪个建筑被别的建筑围死进不去建）。任一被围死 → **整个 anchor 判失败返回 None**（换 anchor）。
5. 返回 count 个中心。放不下（任一步失败换不出）→ None。

**"卡高地边缘 / 卡矿区背后"作为偏好评分**（选 anchor / 排候选时）：
- 高地边缘：footprint 在高地（`terrain_height` 高）且相邻有高度落差格（崖边）→ 加分（贴崖隐蔽/难被冲）。
- 矿区背后：footprint 一侧贴 mineral_field（挡路挡视野）→ 加分。
- 但**这些只是偏好加分，硬约束永远是 3(a)+3(b)+4（放得下 + 建得到 + 不互相封路）**。

## 验收目标：三个偷家 build 都测到通过（2026-07-07 用户 goal）
规划器做成后，**这三个都要 build_acceptance 多局稳定通过（建筑全部按规划建成）**：
1. **人族 4bb 野 3bb**（`proxy_4rax`）：3 兵营，无 pylon 约束。
2. **神族 4bg**（`forward_proxy` gateway 版）：**先 1 Pylon，再 N 个 Gateway 全部在能量场半径 6.5 内**。
3. **神族野两 VS**（`forward_proxy` stargate 版 / 相关）：先 1 Pylon，再 2 Stargate 在能量场内。

**神族变体（关键差异）**：规划器要支持"**先放 anchor 建筑(Pylon)，其余 N 个建筑约束在距 Pylon ≤ POWER_RADIUS(6.5)
内**"。即 `plan_building_cluster` 加可选 `power_source: Point2 | None` + `power_radius`：非 None 时，后续建筑
footprint 必须整体在 power_radius 内。人族传 None（无约束），神族传 Pylon 位 + 6.5。**Pylon 本身的落点也要先经
规划器选一个"放得下 + 周围能放下 N 个 gateway + 都走得到"的位**（否则 Pylon 放了但周围放不下 N 个 = 老问题重演）。
- 神族现有 `forward_proxy.py` 的分散逻辑（`_SLOT_OFFSETS` 各方向）→ 换成规划器输出的确切落点，probe 只执行。

## proxy_4rax 接入（#590 落地）
`proxy_rax_act.py::_pick_proxy_location`：
- 沿用上轮**验证有效的走廊安全评分**（选离敌军必经路最远的 anchor，不 random）。
- anchor 定后，**调 `plan_building_cluster(anchor, BARRACKS, 3, require_reachable_from=start_location)`**
  拿到 **3 个确切落点**（离线规划好，保证放得下 + 每个都走得到 + 不互相封路）。规划失败 → 换下一个安全 anchor。
- 缓存这 3 个落点，`_step_scv` **直接建缓存落点**，**删掉现场 find_placement**。SCV 只走过去 + 建。
- 完成判定用 tag 锁定（上轮已实现）。

## 评审处置（2026-07-07 opus 评审，API 全 VERIFIED；4 必改采纳，算法据此定稿）

**API 定案（评审读 sc2 源码核实，不再 UNVERIFIED）**：
- 格子用**公开下标 `grid[(x,y)]` 或 `ai.in_placement_grid(pt)`/`in_pathing_grid(pt)`（(x,y) 语义，内部转 [y][x]）**，
  **绝不碰 `.data_numpy`**（那是 [y][x] 摸了必错位）。世界→格 = `.rounded`（封装内部处理，别自己算 0.5 偏移）。
- **`PixelMap.flood_fill(start.rounded, pred) -> set[Point2]` 内置**（8 连通，(x,y)）——**直接用，别自写 BFS**。
- **`can_place_single(building, pos)` async bool = 引擎真源**（资源+建筑+地形+footprint 全算）——"放得下"用它。

**MUST-FIX 1（可达必须资源感知，否则矿背后假可达 = 原样复发上次崩因）**：静态 `pathing_grid` 不含矿脉/气矿阻挡。
→ 静态 flood_fill **只做候选快筛 + 建后互相封路复核**；**每个 final 落点用 `await ai._client.query_pathing(scv_origin, spot)`
引擎级最终确认**（资源+建筑感知，返回距离 float/None）。None → 弃该落点/该 anchor。

**MUST-FIX 2（放得下用 can_place_single，别手写 footprint 迭代）**：手读格子要自己处理 3×3 中心→tile 映射易错位（#543）。
→ 最终"放得下"用 **`can_place_single`**（批量 `can_place`）；`placement_grid` 手读只用于快筛。**`occupied`（还没建的虚拟楼
footprint + margin）仍要自己维护**（引擎不知道你没建的楼）。

**MUST-FIX 3（建后复核收紧为"从起点在缩减网格重 flood_fill 验连通"）**：不是"存在一个 pathable 邻格"（会漏"局部可达但
走廊被别的楼切断"）。→ 在 `pathing_grid.copy()` 里把全部 footprint 格置 0，从 `scv_origin.rounded` **重 flood_fill 得
R'**，要求**每栋楼的 footprint 相邻格里 ≥1 个 ∈ R'**。**充分性依据（写进文档）**：建造中半成品楼立即挡 pathing，而"全部
footprint 都摆上" = 占用最坏态；最坏态每栋楼仍连通起点 → 任意建造顺序都不互相封路（单调性）。

**MUST-FIX 4（接入回路防死循环 + 防每帧重规划）**：`_pick_proxy_location` 返回 None 会每帧重进 → 每帧重跑昂贵规划/整局卡死。
→ **一次性遍历有限个安全 anchor（top-N），第一个规划成功即锁缓存**；全失败 → 落**终态兜底**（本文件已有逐 slot
`find_placement` 松散排布，宁松散别卡死）；失败尝试**记账不每帧重算**。落点锁定后建造时被占 → 允许对该 slot **单独**重规划
一次（有次数上限，别漂移）。

**采纳的建议（定稿算法）**：
- **灵活增量贴簇摆放（2026-07-07 用户拍板，替代固定形状）**：不用固定 L 形整块平移——窄口袋（贴崖/矿背后）塞不进。
  改成**逐个贴着已放的簇滑动摆放**：兵营1 放 anchor 附近能放点；兵营2 找**紧挨兵营1**（相邻、优先并排）的最近能放点，
  放不下往旁边滑一格；兵营3 找**紧挨 1 或 2**的最近能放点，滑到哪能放放哪。**始终贴着簇（不散开），但每个可滑动适配地形**。
  放完整体验"3 can_place_single + 建后 3 相邻格 ∈ R' 连通 + 3 点 query_pathing"。某兵营滑遍近邻都放不下 → 换 anchor。
  兼顾"挨一起"+"窄地形容错高"。
- **可达集全局只 flood_fill 一次**（与 anchor 无关），所有 anchor 复用。
- **v1 砍掉 planner 内的高地/矿背后偏好评分**——隐蔽性由**上层 anchor 走廊安全评分**负责，planner 只管硬约束
  （放得下 + 建得到 + 不互相封路）。
- 每个 slot 尽量**独立进场邻格**（别共用 1 宽缝），降 SCV 本体互堵。
- 神族 power_source 约束：与硬约束并列（footprint 整体在 Pylon ≤6.5）。

**验证拆两级（评审强调，别把放置确定性和 SCV 存活混一个指标）**：
1. **隔离放置确定性**（受控局，sandbox_macro_only / 安全 anchor 无敌方干扰）：自验脚本断言 **telemetry 终态真出现 N 个
   目标建筑（黑盒终态，per-instance，不是"下了 build 命令"的中间 trace）**——稳定 N/N 才算放置修好。
2. **再 5 局 vs veryeasy/veryhard 看存活方差**：允许 SCV 阵亡致 <N/N，但日志要能区分"没建成=死了" vs "=放不下"。
- **planning 完美后的残留失败模式（写进文档别当没修好）**：①SCV 中途被打死 ②规划→建造时间差落点被占（重规划回路）
  ③1 宽缝 SCV 本体互堵 ④敌方朝中立点扩张揭穿。

## 不做 / YAGNI
- 不做完整 base 布局规划（只做"某 anchor 附近放 N 个建筑"这一原语）。
- 不做墙（wall-in）——那是另一个专题。
- BFS 可达集若太慢，退化成对少数候选 `query_pathing`（先量 BFS 成本再定）。

## 验证（彻底解决 = 多局稳定 + 视觉）
- 单测：`plan_building_cluster` 用 mock grid（构造几种地形：开阔/贴崖/矿区背后/死角）验：放得下返回 N 个不重叠 +
  每个有可达邻格；放不下返回 None；互相封路的布局被 4 复核挡掉。
- **build_acceptance proxy_4rax 连跑 5 局，每局都要 done(A) 3 兵营建成**（不再 2/3 偶发）——这是"彻底解决"的判据。
- 真局截图判读：3 兵营紧凑 + 落在规划好的隐蔽点，农民直奔直建、无现场试探徘徊。

## UNVERIFIED（实现时真机核对）
- `PixelMap` 的索引/坐标系（`grid[y][x]` vs `grid[x][y]`、世界坐标↔格子映射）——查 `sc2/pixel_map.py` + 现有
  `in_placement_grid`/`in_pathing_grid` 用法对齐。
- BFS 可达集的性能（全图 flood-fill 每局一次是否可接受；只在选点调一次，不进每帧）。
- "footprint 相邻格可达 = 农民能建"是否等价（SC2 建造 SCV 实际站位）——真局验：规划的落点农民是否 100% 建成。
