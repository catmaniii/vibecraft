# 人族 proxy_4rax 野兵营选址重设计（2026-07-06 用户）

## 问题（用户）
proxy_4rax（1家兵营 + 3野兵营偷家）的野兵营落点，现在**继承了神族 forward_proxy 的逻辑**（`_SLOT_OFFSETS`
把 3 个兵营往各方向分散 + 中线/环形候选 + 贴边评分）。两个毛病：
1. **人族三兵营应该挨在一起**：神族"先水晶再各方向分散"是为了防水晶/农民卡死才分散；**人族三个兵营完全可以
   紧挨着建**，不需要分散。现在的分散逻辑对人族是多余的、还占更大地方更难藏。
2. **位置太暴露**：现在拍在"农民/单位高频经过"的开阔点。应该**贴着矿区背后的墙**（矿脉挡路 → 矿背后是单位
   不会路过的死角），**更隐蔽**。

## 目标
给人族 proxy 兵营一套**独立选址逻辑**（不复用神族 forward_proxy 那套分散逻辑）：
- **3 个兵营紧凑成一簇**（挨在一起），不分散。
- 落点选在**离敌方较近的某个中立扩张点的矿区背后**（矿脉+地图边/崖当掩护），单位路不过、隐蔽。

## 落点算法（新，人族专属）
**候选点 = 前沿中立扩张的"矿背后死角"**：
1. **选候选扩张**：遍历 `self.ai.expansion_locations_list`，取距敌方主基地 `[MIN_DIST, MAX_DIST]`（30-65，沿用）
   内、**排除敌方 main + natural**（太近必被守家农民/建筑发现）的扩张点 E。
2. **算每个 E 的"矿背后"落点**：
   - `resources = self.ai.expansion_locations_dict[E]`（该扩张的矿+气 Units）；`mc = 矿脉质心`（只算 mineral_field）。
   - "矿背后方向"：矿脉相对 townhall 通常贴崖/贴边。落点 = `mc + (mc - E).normalized * BEHIND_OFFSET`
     （从 townhall 穿过矿脉再往外，落到矿脉的崖侧/背侧 = 单位绕不过去的死角）。
   - **落点必须在 playable/placement grid 内 + 可达**（`query_pathing(start, spot)` 非 None；SCV 得走得到）。
     矿背后若无可建地/走不到 → 弃这个 E。
3. **评分**（高分=更隐蔽 + timing 好）：
   - 贴地图边加分（沿用 `_edge_distance`，矿背后通常贴边）。
   - **远离扩张中心/矿脉正面加分**（低农民流量：农民聚在 townhall + 矿脉正面，背后没人）。
   - 距敌主基地适中（沿用，近一点 timing 好）。
   - 不在敌方当前视野（沿用 `_in_enemy_vision`）。
   - 可达（沿用 `_is_reachable`）。
4. **兜底**：无合格"矿背后"候选（小地图 / 扩张都太远太近）→ 回退到现有 forward_proxy 那套（中线+环形+贴边），
   保证总能选出一个可建点，不整局卡死。

## 紧凑排布（替换 `_SLOT_OFFSETS` 分散）
- 人族兵营 footprint 3×3。3 个兵营**紧挨成一排或一簇**：`_TERRAN_PACK_OFFSETS`，如
  `[(0,0),(3,0),(-3,0)]`（一排，间距 3 格刚好不重叠）或 `[(0,0),(3,0),(0,3)]`（L 形）。
- 每个兵营仍各自 `find_placement`/`can_place_single`（#543 教训：find_placement addon 网格分支不可信，用
  `can_place_single`）锁定 + 缓存（一次锁定，不每帧重选）。若紧凑落点某个放不下 → 就近微调（由近及远扫）。
- **锚点 = 选出的"矿背后"点**，3 个兵营围绕它紧凑展开。

## 只改人族，不碰神族
- 神族 forward_proxy（`protoss/plans/forward_proxy.py`）**不动**（那套分散逻辑对神族先水晶后建筑是对的）。
- 只改 `terran/plans/proxy_rax_act.py`（`ProxyBarracksAct`）：加"矿背后"候选生成 + 紧凑 offsets，
  forward_proxy 那套降级为兜底。

## 验证（视觉为主，用户的核心诉求是"隐蔽 + 挨一起"）
- **真局截图判读（我自己截 PC 屏，不喊用户）**：起 realtime proxy_4rax 局，等 3 兵营建出来，截 SC2 屏
  裁到兵营区放大，肉眼确认：① 3 个兵营**紧挨成一簇** ② 落在**矿区背后/贴墙的死角**、不在农民高频路径上。
  （判读铁律：找"线框/建筑轮廓"这种非天然形状，别把矿脉绿当建筑。）
- **build_acceptance proxy_4rax**（veryeasy + veryhard）：3 兵营仍按时建成 + 枪兵产出（选址改了别把 build 搞坏）。
- **落点 trace 日志**：`ProxyRax picked proxy=(x,y)` + 3 兵营坐标，grep 确认紧凑（互相距离 <6）+ 距敌合理。

## 方案 B 第二次实测结论（2026-07-06，**安全评分成功，但 build 可靠性未解 → 用户选 C 不提交、回退基线**）

按模型驱动实现（走廊安全评分 + 可达 + find_placement 缓存 + 选最安全不随机 + 紧凑 + tag 锁定）真局跑 6+ 局：

**✅ 成功、下次直接复用（已 VERIFIED）**：
- **走廊安全评分**（anchor 到 敌主→我主 / 敌主→敌natural 两条线段的垂距，越大越安全，**选最安全不 `random.choice`**）
  → **6+ 局全部 0 SCV 死亡**。彻底解决了"选到暴露点 SCV 被打死"（方案 B 第一次的主崩因）。
- SCV 选取改"离 proxy 最近"（不是离 CC 最近，避开抢家兵营那个 SCV）、find_placement 选址时一次性验证+缓存
  （不每帧 find_placement）——这两个也是对的。

**❌ 核心未解问题（下次专门 debug）**：**3 野兵营可靠建成**。多局 = 2/3 ~ 3/3（offset 4）、1/3（offset 5、
timeout）。**关键判断：超时跟落点/紧凑度 offset 无关**（改 offset 4↔5 没规律、都会偶发 timeout(B)），
真因是 **"3 个 SCV 到了 proxy 点、却建不全 3 个兵营"** 的**并行多兵营簇建造执行可靠性**问题：
- 症状：`done (B: timeout 241s)`、只建成 1~2 个 proxy 兵营，**无 SCV 死亡**（不是被打死）、有 slot "build patience expired"。
- 假设方向（下次查）：① find_placement 选址时各 slot 独立可建，但**先建的那个兵营 + SCV 挤占了后建 slot 的落点/导航** →
  后 slot 建不了；② SCV 到点后 `worker.build` 命令被 sharpy build planner / 采矿抢断（reserve 每帧重申够不够）；
  ③ patience/timeout 参数是否给并行 3 建足够时间。**要单独起 realtime 局盯着 3 个 SCV 逐帧看谁卡在哪**（截图/逐帧 trace）。

**下次任务**：单独 debug "proxy 3 SCV 到点却建不全 3 兵营"（build 执行可靠性），把上面 ✅ 的安全评分那套一起带上落地。
这跟落点/隐蔽/紧凑都无关，是个独立的并行建造执行 bug，值得查透而非硬塞。

---

## 方案 B 实施：模型驱动选址（2026-07-06 用户拍板"按这个思路做"）

**建筑学通用原则（用户）**：① 放得下（不被卡）② 不把农民卡死 —— 无非这几条。加 proxy 特有的③别被打死。
**全部用地图静态格子提前算，选最优、不 `random.choice` 随机。**

**用到的 SC2 静态模型**（已核实存在，`game_info`）：`placement_grid`(每格能不能修)、`pathing_grid`(每格能不能走)、
`terrain_height`。

**选址算法**：
1. **候选**：沿用 `_generate_candidates`（中线 60-75% + 敌主外环）——或更密的网格。
2. **硬过滤（放得下 + 不被卡住）**：
   - **放得下**：anchor + 紧凑簇 3 个 offset **全部 `can_place_single(BARRACKS)`**。
   - **SCV 走得到去建（不卡）**：`query_pathing(start, anchor)` 返回距离（可达）**且** anchor 周围有 `in_pathing_grid`
     的相邻格（SCV 能站旁边下建造）。**这条专治"矿背后能修但走不到"**——离线就排掉，不用真局才发现。
   - 避开敌方 natural / 当前敌视野（沿用）。
3. **安全评分（别被打死，← 这次崩的真因）**：
   - **避开敌方军队必经走廊**：算敌主→我主（进攻主路）、敌主→敌 natural 两条线段，anchor 到这些线的**垂距越大越安全**（加分）。SCV 走过去/站桩不在敌军路上就不会被 veryeasy 都打死。
   - 贴地图边加分（沿用，边角流量低）。
   - 距敌主适中（沿用，timing）。
4. **选最安全的**（安全分最高），**不 `random.choice`**。全不合格才 sharpy 兜底。
5. **紧凑排布 + tag 锁定 + 排除自家扩张**：复用上次实现（`_TERRAN_PACK_OFFSETS` L 形间距 4、`ws.barracks_tag`
   按 tag 找本 slot 兵营、排除距自家 townhall < 25 的点）——这些代码本来就对，只是没安全性选址托底。
6. **不堵农民**：proxy 远离自家矿线，本不堵我方农民；只需保证紧凑簇不把**建它的 SCV** 围死（簇留缝，offset 4 已够）。

**验证（关键：多局看稳定，别一局定）**：build_acceptance proxy_4rax 连跑 **3-5 局**（选址不再随机，但存活仍有方差），
每局都要 **3 野兵营建成（done A，非 timeout/deaths）+ SCV 死亡数 ≤1**。再真局截图判读：3 兵营紧凑 + 落在低流量安全角。

---

## 实测结论（2026-07-06，**未落地、已回退基线，待下次迭代**）

按评审处置实现后真局跑 build_acceptance，**两条路都崩、已回退**：
1. **矿背后口袋（behind-minerals）不可行**：口袋选在 (100,115) `can_place_single` 说能放，但 SCV 到点
   **"build patience expired" 反复、兵营一个没建成**——矿脉挡路 SCV 到不了那个 tile 建造，且贴前沿中立扩张
   **SCV 被打死**（评审预测的硬伤全中）。`done (B: timeout)`，0 proxy 兵营。
2. **禁用口袋、回落 fallback 候选 + 紧凑排布也崩**：两局 = 2/3（1 SCV 死）、**0/3（4 SCV 全死光 `done C`）**。
   根因 = **`random.choice(reachable)` 随机选点经常选到 SCV 走过去/待命被打死的暴露点**（正是用户抱怨的
   "太偏太暴露"）。紧凑排布本身 OK（slot 1/2 紧凑建成了），**崩在 SCV 存活，不在落点几何**。

**下次迭代的正确认知（核心）**：这不是"落点几何/矿背后"问题，是 **SCV 选点安全性 + 存活**问题。要做的是：
- 选点评分**加入"SCV 安全性"**（避开敌方初始军队/侦查会路过的点、避开会被 veryeasy 都能打到的暴露走廊），
  **不用 `random.choice` 随机选**——选**最安全**的，而非贴边分最高的随机一个。
- **真局截图 + SCV 存活率**迭代（起 realtime 局看 SCV 走哪条路、死在哪、兵营建没建成），不是靠单测/build_acceptance
  一次就定（选点随机 + 存活方差大，要多局看稳定性）。
- 紧凑排布（`_TERRAN_PACK_OFFSETS`）、tag 锁定、排除自家扩张、query_pathing 距离限——这些代码**是对的**，
  下次可复用（在 git 历史/本设计里）。只是选点安全性没解决前，紧凑也白搭。

---

## 评审处置（2026-07-06 opus 评审，API 全 VERIFIED 无幽灵；3 必改采纳）

**MUST-FIX 1（几何算法，否则白改）**：**放弃 `mc + dir*OFFSET` 单点公式**——"矿背后"绝大多数是不可建地
（矿脉本就贴崖/边），单点几乎全被 `can_place_single` 否 → 所有 E 弃 → 静默退化成兜底 = 这次白做。
改成**扫可建口袋**：以"矿背后方向"（`(mc-E).normalized`）为**搜索起点**，从 mc 沿该方向**由近及远 + 左右扇形**
扫 `can_place_single`，找到**第一个能容纳 3 兵营簇**（L 形/方块）的可建落点即用；扫不到才弃这个 E。
偏好落点**贴不可建地形（墙根：邻居 tile `in_pathing_grid==False` 或 `in_placement_grid==False`）+ 远离该扩张
对外 pathing 出口（低农民流量）**。

**MUST-FIX 2（完成判定串味）**：`_barracks_at_slot` 现用 `distance_to(barracks_placed) < 5.0` 找本 slot 兵营；
紧凑间距 3 下**相邻兵营互在 5.0 内会串味误判**。改成**建造下达时记住那栋兵营的 tag**（`ws.barracks_tag`，仿
protoss 版 `_proxy_tags`）来锁定本 slot 兵营；或关联半径降到 <1.5。用 tag 锁定更稳。

**MUST-FIX 3（排除我方自家扩张）**：候选扩张除距敌 [30,65]，**还要排除距任一自家 townhall/start_location
< `_MIN_HOME_DIST`(25) 的扩张**。否则近点/小图上选到自家 natural → `_is_proxy_building`(距自家≥25 才算 proxy)
判它为"家里建筑"→ proxy 计数永远 <3 → 任务永不完成卡超时。

**采纳的建议**：
- **query_pathing 返回的是路径距离(float)不是 bool**：用它做 **SCV 赶路上限**过滤（矿背后要绕路，>阈值弃 E），
  防"选到得绕小半张图的点 timing 崩"。
- **紧凑改 L 形/方块 + 留 1 格缝**：`_TERRAN_PACK_OFFSETS=[(0,0),(4,0),(0,4)]`（间距 4，方形占地，比一字条 9 宽
  更容易塞进窄口袋；留缝给 SCV 转身下命令）。
- **`expansion_locations_dict` 慢（docstring 明写 slow）**：**只在 `_pick_proxy_location` 调一次**，绝不进每帧 scoring。
- **兜底措辞澄清**：回退 = 回到**本文件已有的** `_generate_candidates`/`_score_pos`（中线+环形+贴边），**不 import**
  protoss forward_proxy 模块，无跨模块状态打架。
- **战术张力（已向用户点明）**：藏中立扩张矿背后 → **敌方一旦朝该点扩张、盖 townhall 就地揭穿 + 秒三兵营**
  （protoss forward_proxy v3 就因此刻意去掉了 expansion 候选）。选点**偏向敌方 rush timing 内不太可能开的扩张**
  （离敌 main 稍远的 3rd/4th、不在敌扩张顺序上的点），哪怕 timing 略亏。

**VERIFIED（评审已核对 python-sc2 源码）**：`expansion_locations_dict: dict[Point2, Units]`（value=矿+气）、
`Units.mineral_field`、`Units.center`、`query_pathing`(返回距离 float/None)、`can_place_single`(bool)、
`in_placement_grid`/`in_pathing_grid`——全真实存在、签名一致。**无 UNVERIFIED 符号**。唯一需真机验的是
**几何效果本身**（口袋是否真隐蔽/紧凑）→ 靠截图判读迭代。
