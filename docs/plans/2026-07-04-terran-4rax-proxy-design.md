# 人族单矿 4 兵营 proxy rush 设计（1 家 BB + 3 野 BB）

> 状态：**设计草案，未实现**。产出者：build order 设计员（Opus 4.8），2026-07-04。
> 本文只做 WHAT / WHY + 落地清单 + 风险取舍，**不含实现代码**。实现前须按 CLAUDE.md
> 规则派独立 subagent（Opus 4.8）评审。

## 用户拍板决策（2026-07-04，实现以此为准，覆盖下文草案里冲突处）

- **核心（必须）**：4 兵营全 pump 枪兵 = **家 1 兵营（正常出兵、兵源+迷惑）+ 野 3 兵营（隐藏 proxy 点，参考神族 4bg 野位；**始终隐藏**、不在斜坡）**。四个持续爆枪兵。
- **3 个野兵营 SCV 建完隐藏兵营 → 跟枪兵一起前压**（不回家采矿）。
- **可选开关（默认关）：激进封锁 = 前压途中那 3 个 SCV 到敌方斜坡下面修 3 个地堡(BUNKER)封锁斜坡口**，围死敌人（打神/虫非常有效）。**注意是地堡不是兵营；兵营一直隐藏。**
- **无气**（纯枪兵 all-in 最快一波）；**SCV 停产数留 build_acceptance 实测调**；配比锁 1家+3野（不做 0家+4野）。
- 落点两套：①野兵营=隐藏 proxy（藏、离敌不太远好增援）；②可选地堡=敌方主基**斜坡下方墙位**（堵口）。

---

---

## 0. 一句话

单矿不开矿、**1 个兵营在家正常修（迷惑 + 早期枪兵）+ 3 个野兵营修在敌方家门口贴边隐蔽点**
= 4 兵营枪兵 all-in。第 1 兵营一放下就派 **3 个 SCV** 提前出发走去野点，路上矿正好攒到
**450**（3×150），3 个 SCV 各修 1 个野兵营。枪兵在敌方门口成型，~2:30-3:00 一波打死；
**打不死就输，无转型**（all-in 本质，接受它）。

---

## 1. 调研：真实人族 proxy rax build order

用户要求"新增 build 前先查真实 build order"。查了 spawningtool 4 Rax All-In + Liquipedia
Double Proxy Barracks + osiris 教程。核心结论：

### 1.1 spawningtool《4 Rax All-In》(TvZ) — 最接近用户想要的

来源：https://lotv.spawningtool.com/build/82647/

- **只造 2 个 SCV**（开局 1 个修 depot，3 兵营下完后再补 1 个），其余全砍——纯 all-in。
- **3 个 SCV 依次派出去**修兵营：第 1 个立刻走，第 2 个 ~5s 后，第 3 个再 ~10s 后。
- 时间线（近似）：
  - 0:14 Supply Depot（家）
  - 0:36 / 0:46 / 1:02 Barracks #1 / #2 / #3（**全在野点**）
  - 1:22 第一个枪兵
  - 1:27 Barracks #4
  - 1:46 第二个 Supply Depot（**在野点**）
  - **~2:30 一波打出去：6-8 枪兵 + 3 SCV**，之后每次 4 枪兵持续补
- **不需要气**。natural 修一个 bunker。进攻前从家里多拉几个 SCV 加火力。
- **"这个 build 没有后续。第一波打死就赢，打不死就输。"**

### 1.2 Liquipedia《Double Proxy Barracks》— 野点选址权威

来源：https://liquipedia.net/starcraft2/Double_Proxy_Barracks

- 野兵营放在**敌方正常侦察路线之外 + 避开 Xel'Naga 瞭望塔视野**；修在**离自家相对近**的地方
  以便打不动时把兵营**浮空飞回来**。**打神族**（all-in）时可以修得离敌方更近。
- 10 房子 → 11 派 SCV → 11 野点开 2 兵营 → 13 轨道 + 首枪兵 → 16 第二房子 + 拉 1-3 SCV →
  17 敌方 natural 修 bunker。
- 打神族变体：拉 **5 个 SCV**、用叫补给代替 MULE、bunker 修在**敌方主矿内**而不是二矿。

### 1.3 常见变体对照

| 变体 | 兵营 | 特点 | 优 | 劣 |
|---|---|---|---|---|
| Proxy 2rax | 2 野 | 最经典，早枪兵压 | 经济损失小、可 float 回家转型 | 火力弱，被守住就废 |
| **Proxy 4rax all-in** | 4（全野或 3 野+1 家）| 火力猛、~2:30 一波 | 一波兵多、打死就赢 | 纯 all-in、无转型、被发现即崩 |
| Proxy bunker rush | 2 野 + 敌矿 bunker | bunker 卡矿 + 枪兵 | 卡住敌方运营 | 需精细 SCV 微操、bunker 被拆就亏 |

**用户方案定位**：1.1 的 4rax all-in，但**把第 1 个兵营留在家里正常修**（真实打法多是 4 个全野）。
这是用户的差异化：家兵营 = 迷惑（敌方 scout 看到家里有兵营会以为是标准开局，不去搜野点）
+ 早期枪兵守家/凑一波。

---

## 2. 现有代码能复用什么 / 缺什么

### 2.1 现成的 proxy 建造机制（神族，直接可借）

| 文件 | act | 可借的东西 |
|---|---|---|
| `protoss/plans/forward_proxy.py` | `ForwardSupportPylonGateway` | **野点选址全套**：候选点生成（敌方外环 30/40/50/55 + 中线偏敌方侧）、评分（贴边权重 50、偏离进攻轴 ×1.5、避开敌方 natural 22 格、避开当前敌方视野）、寻路可达性检查、无进展 25s re-pick、worker 保命撤退/复出、tag 跟踪建筑状态、5 重完成判定（建好/超时/农民死太多/主力出门）|
| `protoss/plans/forward_cannon_proxy.py` | `ForwardCannonProxy` | **多 worker 并行建造**（`_ProxyWorker` per-worker pending_build 落点匹配、`_ensure_workers`、贴边赶路航点 `_compute_edge_route` / `_travel_step`、赶路总放弃兜底、静态敌方视野预测 `_static_enemy_vision_sources` 排除开局看不见但必照亮的矿/气/Nexus）|

这两个 act 把"派农民贴边走到敌方门口隐蔽点建东西、保命、被发现容错、并行多农民"的**全部难点**都
解过了。**人族 4rax proxy 的落点选址 + 送农民 + 赶路 + 保命逻辑几乎可以照抄**，只需替换建筑类型
（Pylon/Gateway/Cannon → Barracks）和去掉神族特有的供电/科技前置逻辑。

### 2.2 现成的 plan 骨架（人族）

- `terran/plans/bc_rush.py` / `marine_rush.py`：`BuildOrder` + `SequentialList` + `Step` +
  `GridBuilding` / `ActUnit` / `TerranUnit` + `AutoDepot` + tactics（`DistributeWorkers` /
  `PlanZoneGather` / `PlanZoneAttack` / `PlanFinishEnemy` / `CallMule` / `ManTheBunkers`）。
- `marine_rush.py` 的枪兵 all-in 结构（`TerranUnit(MARINE, priority=True)` 排在建筑前抢矿、
  `PlanZoneAttack(start_attack_power=6)` 低阈值出门、`PlanZoneDefense` skip when supply_army≥12）
  **直接是本 build 的家兵营 + 出兵 + 出门骨架**。
- `RampBunkerAct`（bc_rush.py）：一次 `find_placement` 锁死落点不每帧重选 → **本 build 的野落点
  锁定套路参照它**（CLAUDE.md 强规则：目标坐标一次规划、锁定、别每帧重选）。
- `EmitOpeningCompleteAct(self._opening_done)`：开局完成信号 → Director 推荐转 doctrine。

### 2.3 缺什么：需要新写一个 `ProxyBarracksAct`

**没有**现成的"人族多 SCV 并行去野点各建 1 个兵营"的 act（`GridBuilding` 只在家附近找位置放，
不会送农民去敌方门口、不会并行 3 农民、不做贴边隐蔽赶路）。所以要新写一个
`ProxyBarracksAct`（`terran/plans/proxy_rax_act.py`），**结构 = `ForwardCannonProxy` 的人族改写版**：

- **目标**：在选定野点周围建 `_TARGET_RAX` 个（默认 3）Barracks，用 `_MAX_PROXY_WORKERS`（=3）
  个 SCV **并行**建。
- **借 `ForwardSupportPylonGateway` 的**：`_pick_proxy_location`（候选生成 + 评分 + 可达性）、
  `_score_pos`（贴边 + 偏轴 + 避 natural + 避视野）、re-pick、保命撤退。
- **借 `ForwardCannonProxy` 的**：多 worker（`_ProxyWorker` per-worker pending_build 落点匹配）、
  贴边赶路航点、`_static_enemy_vision_sources` 视野预测、完成判定。
- **人族简化掉**：不需要 Pylon 供电（兵营不吃电）、不需要 Forge 前置（兵营前置 = Supply Depot，
  由**家里的第一个 depot** 满足，全图共享）、不需要蛙跳（3 兵营一次性并排落在野点即可）。
- **落点排布**：3 个兵营在野点周围**互不重叠**排开（各 3×3，留 1 格间距；参照 director.py 的
  `_CHAIN_SPOT_OFFSETS` 定向偏移思路，或每个 SCV 落点带 `find_placement` + 小抖动像 cannon proxy
  那样避免抢同一格）。**每个落点一次算好锁住**（CLAUDE.md 强规则）。
- **建完 SCV 干嘛**（可配置）：默认**回家采矿**（凑经济继续补枪兵）；变体可留在野点当第一波
  肉盾/修 bunker（见 §6 变体）。

---

## 3. Build 时间线（用户方案：1 家 BB + 3 野 BB，单矿无气）

> supply / game_time 均为**目标值**，实测后按 build_acceptance 校准（marine_rush 就实测出门
> 263s 而非教程 3:20，本 build 同样以实测为准）。

| supply | 动作 | 说明 |
|---|---|---|
| — | SCV 持续到 ~15-16 停 | 单矿 all-in，要够快攒 450 + 撑 4 兵营枪兵产能，但别过量拖慢兵营。比纯 4rax（只 2 SCV）略经济，因为家兵营让节奏稍缓 |
| 14 | Supply Depot（家）| 兵营前置 + 供给。全图共享 → 满足所有（含野）兵营的科技前置 |
| ~15 | **Barracks #1（家，正常位置）** | 迷惑 + 早枪兵。放下即触发 §4 送 3 SCV |
| ~15（紧接）| **派 3 个 SCV 出发去野点** | 依次出发（错开 ~2-5s）。走路 ~15-25s，期间矿从 ~150 攒向 450 |
| — | 矿攒到 ~450 时 3 SCV 到位 | 3×150=450，一次性 3 兵营各占一个野落点 |
| ~17-20 | **Barracks #2/#3/#4（野点，3 个并行）** | `ProxyBarracksAct` 并行 3 SCV 各建 1 个 |
| ~1:20 | 家兵营第一个枪兵 | 早枪兵守家 + 攒一波 |
| ~1:46 | （可选）野点第二个 Supply Depot | 供 4 兵营枪兵产能。**或**靠家 `AutoDepot`（supply 全局，家里加房也够）|
| ~2:00+ | 4 兵营持续出枪兵，rally 到野点集结区 | 兵在敌方门口成型 → 出门距离近、timing 早 |
| ~2:30-3:00 | **第一波 ~8-12 枪兵（+ 拉家里 SCV）一波出门** | 打不死就输 |

**关键取舍：SCV 数量 vs 攒 450 速度 vs 兵营产能**
- 纯 4rax 教程只造 2 SCV（把矿全砸兵营/枪兵）。用户版留家兵营 = 略微经济，建议 SCV 停在 ~15-16：
  够快到 450、够 4 兵营不断枪兵，又不过量。**这个数值留给 build_acceptance 实测调**（memory：
  战术摸索自驱动，别凭印象定死）。

**气**：默认**无气**（纯枪兵 all-in，匹配真实 4rax）。变体可上 1 气研 Stim（§6）。

---

## 4. 送农民机制（核心，用户重点）

用户原话："第 1 个兵营下来之后，那 3 个农民提前出发——因为他们走去野点的路上，你的矿会逐渐攒到
450，正好到点下 3 个 BB。"

**这正是 `ProxyBarracksAct` 要实现的"提前量 = 走路时间 ≈ 攒 450 时间"**：

1. **触发时机**：`ProxyBarracksAct` 与 `GridBuilding(BARRACKS,1)`（家兵营）并列在 BuildOrder 里，
   act 内部**第一次 execute 就选野点 + 派 3 SCV 出发**（不等矿够 450）。提前量天然来自"选点→赶路"
   这段 act 自己就会走的时间。
2. **赶路**：借 `ForwardCannonProxy._compute_edge_route` / `_travel_step` 贴边走（memory：骚扰/
   接近贴边走、晚被发现）。3 个 SCV 各自独立赶路。
3. **到点建造**：SCV 快到野点（`distance_to(proxy) < 9`）后，`_next_job` 给它分配一个还没被认领的
   兵营落点。**建造前 `can_afford(BARRACKS)` 门控** → 矿不够 150 就在野点待命（`_redirect_worker_to_anchor`
   防 auto-mining 走回家）。所以"正好 450 到点"不需要精确计时——**到了没钱就等，有钱就建**，
   3 个 SCV 各等各的钱，矿一到 150 就有一个开建，450 到齐时 3 个都能开建。
4. **提前量对不齐怎么办**：走太快先到 → 在野点等钱（不暴露地回家）；走太慢矿先到 → 矿 float 一点点，
   到了立刻建。**两头都有兜底，不依赖精确 timing**（符合 CLAUDE.md"别每帧重选、锁死目标"精神：
   落点锁死、到点等钱）。
5. **现有机制够不够**：选址/赶路/保命/多 worker/完成判定**全部够**（照抄两个神族 act）。**唯一缺**
   的就是把它们组装成"3 SCV 各建 1 兵营"的人族 act，即 `ProxyBarracksAct`。工作量 = 中等（主要是
   裁剪神族逻辑 + 落点排布 + 建完 SCV 去向）。

---

## 5. 迷惑（家兵营的作用）

- **视觉迷惑**：敌方 scout 进你家看到"1 depot + 1 barracks 在正常位置" → 判定为标准 1rax 开局，
  不会去搜地图边缘找野兵营 → 野点更晚被发现 → 争取到 all-in 的时间窗（memory：晚被发现争取骚扰
  时间，同理）。真实纯 4rax（家里空）反而容易让敌方"家里没兵营 = 有 proxy"起疑。
- **早期枪兵**：家兵营从 ~1:20 就出枪兵，能守住敌方的侦察骚扰 / 一波前压，不用等野兵营。
- **成本**：家兵营 150 矿本可以变成"第 4 个野兵营"。取舍 = 迷惑价值 + 守家 vs 野点火力密度。
  用户明确要 1 家 + 3 野，采纳。

---

## 6. 风险 / 取舍 + 候选变体（给用户选）

### 6.1 固有风险

| 风险 | 缓解 |
|---|---|
| 野点被 scout 提前发现 → all-in 崩 | 贴边选址（评分权重 50）+ 家兵营迷惑 + 避开敌方 natural 22 格 + 避开瞭望塔/预测视野。被发现容错：`ForwardCannonProxy` 的多 SCV 补建 + 保命撤退已解 |
| 3 SCV 路上被拦截打死 | 借 `_MAX_WORKER_DEATHS`（默认 4）容错 + 保命撤退 + 贴边绕开进攻轴。死太多 → 完成判定 D 放弃，兵营 float 回家（Liquipedia 打法）|
| 一波打不下 → 无转型 | **接受它**（all-in 本质，marine_rush 同款）。兜底：`EmitOpeningCompleteAct` 触发后转 `persistent` doctrine 补运营（打不死但没全崩时）。默认 doctrine 走 `bio_stim` / 现成人族 persistent |
| 单矿无气纯枪兵局限 | 打不下就是输。这是 all-in 定价，不是 bug |

### 6.2 三个候选变体（让用户拍板）

**A. 兵营配比：1 家 + 3 野（用户方案，推荐）**
- 优：迷惑 + 早枪兵守家 + 3 野点火力。劣：家兵营 150 矿没进野点火力。
- **推荐**。符合用户原话，迷惑价值 + 守家兜底值这 150 矿。

**B. 兵营配比：0 家 + 4 野（纯教程 4rax）**
- 优：火力密度最高、一波最猛。劣：家里空 → 敌方 scout 一看没兵营立刻起疑搜野点；无早期守家枪兵。
- 备选。若用户想要极限一波、赌敌方 scout 晚，选它。

**C. 加一个敌方矿区 bunker（proxy bunker rush 混合）**
- 在敌方 natural / main 修 1 个 bunker（`ManTheBunkers` 已现成塞兵），枪兵进 bunker 卡矿。
- 优：卡住敌方采矿、bunker 硬 + 枪兵输出、逼敌方先出反 bunker 单位。劣：需 SCV 微操修 bunker、
  bunker 被集火拆掉就亏 100 矿 + 位置暴露。
- 可作为 A 的**加料开关**（`proxy_bunker=True`），不冲突。

**要不要气 / Stim（正交选项）**
- 默认无气（最快一波）。可选 1 气 + BB1 TechLab 研 Stim → 一波更强但晚 ~30-40s、被守住风险↑。
- **推荐默认无气**（匹配真实 4rax all-in，"打死就赢"靠 timing 早不靠 Stim）。用户想要更硬的
  一波再加 Stim 变体。

---

## 7. 落地清单（按 CLAUDE.md 纪律，实现时逐条办）

1. **新 act**：`src/vibecraft/bot/auto_combat/terran/plans/proxy_rax_act.py`
   → `ProxyBarracksAct`（多 SCV 并行野点建兵营，结构照抄 `ForwardCannonProxy` + `ForwardSupportPylonGateway`）。
2. **新 plan**：`src/vibecraft/bot/auto_combat/terran/plans/proxy_4rax.py`
   → `class Proxy4Rax(KnowledgeBot)`：家兵营（`GridBuilding(BARRACKS,1)`）+ `ProxyBarracksAct(3)` +
   `AutoDepot` + `TerranUnit(MARINE, priority=True)` + SCV ramp（~15-16 封顶）+ tactics
   （`PlanZoneAttack(start_attack_power≈6)` 低阈值 + `PlanZoneGather` 集结点设野点 + `ManTheBunkers`
   如启 bunker + `CallMule` + `PlanFinishEnemy`）+ `EmitOpeningCompleteAct(self._opening_done)`。
   `_opening_done` = "≥8 枪兵 或 time≥180"（照 marine_rush）。
3. **strategy yaml**：`strategies/terran/proxy_4rax.yaml`（`kind: opening_build`，`sharpy_dummy_class`
   指向 `Proxy4Rax`，`steps` 用合法 BuildStep，`aliases` 含"4兵营rush/野兵营/proxy rax/偷兵营"等，
   `matchup: [TvP,TvZ,TvT]`，`default_transitions` → 某 persistent doctrine）。**过 catalog 校验**
   （opening_build 不接受 gas_intensity 等 doctrine 字段）。
4. **构造测试**：`tests/unit/test_terran_plans_construct.py` 的 `_TERRAN_OPENINGS` 加
   `("proxy_4rax", "Proxy4Rax")`（占位 enum 审计自动 parametrize 扫）。
5. **strategy 计数测试**：`tests/unit/test_terran_strategies.py` openings 计数 10→11 + 断言
   `"proxy_4rax" in ids`。
6. **facade 一致性**：本 act 若新增/改 facade 方法（大概率不需要，直接用 `worker.build` / roles）→
   同步 `FakeFacade` + `_SharpyFacadeBase` + 跑 audit。若只用现有能力则免。
7. **刷面板**：动了 build → 重启 server（`/api/strategies` 才出现新 build）。
8. **重 dump LLM prompt**：`scripts/dump_llm_prompt.py`（catalog 动态生成，无需硬编码）。
9. **build_acceptance spec**：`scripts/build_acceptance.py` 加 proxy_4rax 的 check（兵营 count timing、
   首枪兵、出门时间 ~2:30-3:00）。**1 VeryEasy + 3 VeryHard 混跑**。
10. **真局自验**：`ProxyBarracksAct` 属"玩家看不到但 bot 派农民去野点"类 → 写一个 proxy 自验脚本
    （仿 `proxy_chain_selftest.py`）：起真局，**断言世界终态**——3 个兵营真的在敌方附近落成
    （telemetry BARRACKS count 且位置远离自家 townhall）、SCV 真到野点、~2:30 真出门。**per-instance
    断言每个野兵营都建成**（CLAUDE.md：别用 best/any 聚合掩盖单个失败）。
11. **文档同步**：`ARCHITECTURE.md`（新 act + 数据流）、`USER_GUIDE.md`（玩家话语示例："4 兵营偷
    家 rush" 怎么喊）、`README.md`（build 清单）、`CHANGELOG.md` + `TASKS.md`。
12. **build 质量六维自检**：按 CLAUDE.md「Build 执行质量自检标准」跑 build_efficiency（农民不闲置、
    资源不堆积、产能利用、不卡人口/资源、科技链到位、后劲——注：all-in 无后劲维度按 all-in 定义豁免）。

---

## 8. 参考来源

- spawningtool《4 Rax All-In》：https://lotv.spawningtool.com/build/82647/
- Liquipedia《Double Proxy Barracks》：https://liquipedia.net/starcraft2/Double_Proxy_Barracks
- osiris《Double Proxy Rax Build Order (TvZ)》：https://osirissc2guide.com/double-proxy-rax-build-order-tvz.html
- osiris《Triple Bunker Rush Build Order》：https://osirissc2guide.com/triple-bunker-rush-build-order.html
- Liquipedia《12 Marines @4:30》：https://liquipedia.net/starcraft2/12_Marines_@4:30
- 现有代码参照：`protoss/plans/forward_proxy.py`、`protoss/plans/forward_cannon_proxy.py`、
  `terran/plans/bc_rush.py`、`terran/plans/marine_rush.py`
