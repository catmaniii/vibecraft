# 坑道虫落点 + 多波投送策略 — 设计文档

> 2026-07-12。用户拍板重构 nydus 落点/投送,把当前 49% 落地率天花板的**串行单点**结构
> 改成**并行多点 + 机会主义 + 可复用多波**。独立评审前的真理源。

---

## 背景:为什么现在卡 49% 落地率

现状(`nydus.py::_BuildNydusCanalAtEnemy` + `_SendOverlordToEnemy`)是**"锁一个点 → 等窗口 → 超时降级"**
的串行结构,三重单点故障:

- **只盯敌方主基地一个区**:那点被守/丢视野 → 整条链干等。
- **视野只 1 只 OL 贴主基 standoff,直线飞过去**:穿场中央挨打,OL 一死 → `BUILD_NYDUSWORM`
  对不可见点静默空放(踩过 170 次空放)。
- **窗口检测是硬门**(敌军离矿线远才落):不开 → timeout 降级到隐蔽点,而降级点又常恰好不可见。
- **单向 STRIKE**:坑道虫是一次性的,army 送死就没了;没有"打不好撤回来保实力再来"。

用户诊断:理解太窄。一个区不止一个落点;二矿易增援不该落;坑道是可复用投送不是一次性。

---

## 设计目标(用户 2026-07-12 拍板,逐条)

1. **落点 = 每个区一圈多方向边缘点**,不是每区一个点(左框边缘、右框边缘、矿后死角等多方向)。
   多方向的意义是**容错**——总有一个方向此刻有视野+可放,单方向被守死还有别的。
   **排序只看打击价值,与撤退方向无关**(有 canal,army 钻回家怎么都能撤,落点不考虑撤退):
   **矿后钻出屠农民 = 最高优先级**,打产能/科技次之。
2. **落点区 = 主基 + 三矿,不要二矿**(二矿离主基近,敌人一召唤增援就回防)。三矿:
   - 有矿开出来 → 直接打三矿(经济薄防)。
   - 还是空地 → 绝佳隐蔽落点(远离主力、不易被发现);钻出后**迂回侧后扑二矿**。
3. **视野贴边接近**(参考神族 warp prism 空投寻路):OL 沿地图边缘绕到矿后,不穿场中央;三矿更远
   → OL 更安全 → 视野更稳。**视野=候选合并**:每只活着靠近敌方的 OL 位置本身就是一个合法落点。
4. **第一时间落**:每 tick 扫当前目标区的多方向边缘点,`is_visible ∧ can_place_single ∧ 附近敌军≤阈值`
   任一通过就立刻落,不再单点干等 timeout。
5. **坑道可复用 + 多波**:canal 不被拆就是永久落点 + 永久视野,army 双向进出。打不好主动钻回保
   实力,攒够再来一波;下一波不必同点。
6. **战术意图分模式**:每波 PROBE(试探) / COMMIT(梭哈)。**默认 PROBE**;玩家可一句话把某波指为 COMMIT。
7. **续兵看局势**(不写死):打进去了 → 顺 canal 持续续兵压着打;被逼退 / canal 被拆 → 停续兵、憋
   下一波、**另选候选点**(拉黑刚失败的点)。

---

## 一、落点模型:候选点集合(每区 × 多方向边缘)

VN 一 ready 就**一次性算出排好序的候选列表并锁定**(#543,几何落点静态,不每帧重算;视野动态,留给三)。

### 区(zone)

| 区 | 何时用 | 落点锚 |
|---|---|---|
| **三矿(远/隐蔽,首选)** | 三矿有矿 → 打矿;三矿空 → 隐蔽落 + 迂回扑二矿 | 敌方第三 expansion zone,取不到则敌主基→我方向量拉远的贴边点 |
| **主基(兜底/COMMIT)** | 三矿都不可用 / 玩家指定强攻主基 | 主基 `behind_mineral_positions` + 主基**朝我方一侧**高地边缘 |

**为什么首选三矿不是主基**:三矿远离敌方主力、防守薄,OL 贴边到三矿最安全,落地不易被发现;
空三矿还能当迂回二矿的跳板。主基留给"三矿全不可用"或玩家点名 COMMIT 强攻。

### 每区的多方向边缘点(核心:一个区一圈候选)

对选定区,候选 = 该区锚点周围**多方向的边缘 tile**,**按打击价值排序(与撤退方向无关)**:

```
优先级  方向/点                       理由
 高    矿后死角(behind_mineral)       钻出即屠农民 —— 最高打击价值,所有落点第一优先
 中    产能/科技建筑一侧               打产能/科技也行,次于屠农民
 —     区左框 / 右框 / 其余方向        多方向 = 容错(总有一个方向此刻有视野可放),不是为撤退
 底    每个"活着靠近该区的 OL"位置      定义上可见兜底,只要 OL 活着 canal 必落得下(视野=候选)
```

**撤退方向不进排序**:army 从 canal 钻回家即撤,任何落点都方便撤退,落点只挑打击价值 + 当前安全窗口。
每个候选 tile 生成时过 `can_place_single(NYDUSCANAL, tile)`(几何可放,静态)。视野/威胁留到运行时(三)。

---

## 二、视野保持:贴边接近 + 视野即候选

**现状问题**:1 OL 直线飞、死了全瞎。

**设计**:
1. **贴边接近路径**:OL 移动目标不是"敌主基 towards 我方 standoff"(直线),而是**沿地图边缘绕行到
   目标区矿后**。落点在三矿(远)时这条路径天然更安全。实现:接近路径取"我方→沿最近地图边→目标区"
   的折线(可复用现有 `plan_avoid_path` / warp prism 空投那套贴边寻路思路)。
2. **视野跟随当前目标区**:OL 驻守**当前选定区**(默认主基)的 standoff。
3. **视野即候选合并**:任何活着靠近目标区的 OL 位置进候选表(定义上可见)。有 OVERLORDSPEED
   (build 已研),OL kite hit-and-run 保命(现 `_SendOverlordToEnemy` 的 flee 逻辑保留)。
4. **多派 OL 做视野冗余(用户 2026-07-12 提到核心,推翻原 YAGNI)**:视野是 49% 落地率的病根
   (OL 一死→静默空放),而 **OL 是供应提供者、0 供应占用**,多派 2-3 只只花矿(100/只)不吃人口。
   默认派 **2-3 只 OL** 到目标区做冗余视野(一只被打掉、别的还在供视野=下一 tick 仍可落),配
   OVERLORDSPEED kite 保命。死了自动补(现 `_SendOverlordToEnemy` 每帧巡查,单只死了才补 → 改成
   维持 N 只)。**将来若有更保命的手段(升级/走位)再叠**,先靠"多派 + 提速 kite"。
   代价:被打掉的 OL 丢的是供应(可能瞬时人口卡),但 all-in 局本就快、且 kite 下损耗可控。

---

## 三、可用判断 → 第一时间落(机会主义,打 49% 天花板)

**替换掉"锁一点等窗口 timeout"**。武装后(见四的门)每 tick:

```
扫当前区候选列表(一 already 排好序):
  for tile in candidates(含活OL位置):
     若 tile 在拉黑圈 → skip
     若 not is_visible(tile) → skip        # 每帧真下令前重查(踩过170次空放)
     若 not can_place_single(tile) → skip
     若 附近敌方战斗单位 > 阈值 → skip
     → 命中:立刻 BUILD_NYDUSWORM(tile),锁定该 tile(不漂移),return
  全 skip → 等佯攻继续引(节流 log),不 timeout 降级到瞎点
```

**分层挑点**(兼顾质量 vs 第一时间):优先高打击价值点(矿后屠农民);等了 T 秒仍无 → 接受任意
可见可放点(含活 OL 位置兜底)。门 = ②有视野 ∧ ④主力不在(局部威胁≤阈值),见 §三·五。

---

## 三·五、下坑道虫的判定(最本质:敌方主力不在落点区)

用户 2026-07-12 定死本质:**下不下坑道虫,最本质的唯一战术条件 = 敌方主力在不在落点区(家里)**。
佯攻到位、兵力够都**不是**核心门。门收敛成两条:

```
② 有视野    落点 tile is_visible(BUILD_NYDUSWORM 硬机制,每帧真下令前重查,踩过170次空放)
④ 主力不在  落点区附近敌方战斗单位 ≤ 阈值(复用现 _count_enemy_army_near_main)
   ② ∧ ④ → 下坑道虫
```

- 敌方主力**在家** → **即使有视野也别强下**(14s 钻出期必被秒,Round1-3 老死法)。
- 敌方主力**不在家** → **哪怕佯攻没到位、兵力没满也下**(主力不在 = 窗口天然开,先占住投送口+视野)。
- **③ 佯攻不是前置门,是手段**:仅当"敌方主力在家"时,才启动佯攻小队把主力**引出来**(声东击西),
  制造 ④。主力本来就不在家(去进攻/被别处牵制)→ 根本不需要佯攻,直接下。
- **① 兵力够**关系到 **STRIKE**(装载 army 灌进去打),不是 canal 落地的核心门:canal 可先落
  (占投送口+视野),army 够了再灌。
- **wave_intent**(玩家 `attack_mode_override`):`all_in`(COMMIT)可容忍落点区少量残敌硬下(压死);
  `probe`/None(PROBE,默认)严格等 ④。**但②(有视野)任何模式都不放宽**——不可见下了纯白下。

这直接解掉评审抓的"4 条硬 AND 会把落地率压到 49% 以下、且与'第一时间落'自相矛盾":门不再等"佯攻
到位",只等"主力不在 + 有视野",佯攻退化成"主力在家时才用的引离手段"。

---

## 四、投送 = 现有进退规则 + 坑道虫多一条短路径(用户 2026-07-12 定框)

**关键定框(用户)**:坑道虫**不是**一套专门的多波状态机,它就是**给移动路径规划多加一条短路径**。
**"该进该退"的判断规则完全不变**(用现有 army 强度/威胁那套),只是进攻/撤退时**多一个走坑道的路径选项**:

```
进攻路线选择:  地面正面路   ┐
              坑道短路(load家network→pop canal)  ┘── 现有进攻逻辑挑更优的那条
撤退路线选择:  地面撤回家   ┐
              坑道短路(load canal→pop家network)  ┘── 现有撤退逻辑挑更优的那条
```

- **多波是涌现的,不是写死的**:正常"进→(该退就退)→补充→(该进再进)"规则反复跑,配合坑道短路径,
  自然形成"打一波不好撤回、攒够再来"。**不新造 STAGE/STRIKE/RETREAT 专用 FSM 的复杂判定**,只在现有
  raid 投送里把"回家路径"加一个坑道选项。
- **该退阈值仍来自现有规则 + wave_intent 调**:`probe`/None(默认 PROBE)= 劣势就退(走坑道短路更快
  保实力);`all_in`(COMMIT,玩家 `attack_mode_override`)= 不退压死。这本就是"该进该退规则"的一部分,
  只是玩家能一句话调,规则本身不变。
- **续兵也是涌现的**:家里持续产的蟑螂/狗,只要判定"该进"(canal 存活、局势不崩),就顺坑道短路继续
  送;判定"该退/canal 没了"就不送。同一套进退规则,无需单独的续兵状态。
- **canal 被拆 = 这条短路径暂时没了** → 回[一]拉黑该点、重下坑道虫恢复路径;恢复前撤退走地面(路径降级)。

### ⚠ UNVERIFIED(P2 前置必验,评审 2 次点名):坑道**反向装载**

现状代码只跑过**单向**:load 家 network → pop canal。"撤退走坑道"要求**反向**:army load 进**敌方侧
canal** → 家 network 弹出。本仓从未跑过往 canal 装载。**P2 动手前先 spot-check**(`get_available_abilities`
真机看 canal 侧有无 LOAD/SMART,装一只兵进 canal→家 network `UNLOADALL_NYDASNETWORK` 验终态吐出)。
**证不成 → "撤退走坑道"这条路径选项直接去掉,撤退只走地面**(设计不塌,只是少一条路径,完全符合
"多一条路径选项"的定框)。

---

## 五、玩家指令 = 复用现有 `attack_mode_override` 字段(不新起 director 集成)

wave_intent **直接读现成字段** `knowledge.vibecraft.attack_mode_override`(玩家点 UI 进攻按钮的
probe/all_in 子模式,经 `_submit_tactical_action`→`facade.set_attack_mode_override`→`common_bot.py:1078`
写入):`"all_in"`→COMMIT(不退压死)、`"probe"`/`None`→PROBE(默认,劣势退)。

**不新增 directive 类型 / catalog / few_shot / prompt 同步 / knowledge 通道**——字段+UI+prompt 全已存在
(评审曾高估成"整条 director 集成大工程",实为读一个既有字段)。与 2026-07-09 #8 不冲突:#8 讲"全军按钮
不指挥 Reserved 单位",这里是 raid act **主动读**玩家战术子模式,自愿读、非被全军按钮指挥。

其余话术("换个点 / 打三矿")留后置占位,非核心;不做也能跑合理默认(主力不在就落主基矿后、PROBE、
涌现式进退)。

---

## 六、架构落地

```
NydusLandingPlanner(纯挑点模块 + latch 缓存;副作用薄,OL.move/BUILD_NYDUSWORM 留薄 act 调)
  ├ plan_candidates()     [一] 目标区(默认主基)一圈 can_place 候选,按打击价值(矿后屠农民)优先
  │                            + OL 位置兜底;"矿后"锚点几何自算,不用 behind_mineral_positions(敌方zone恒空)
  ├ pick_available_now()  [三] 每tick扫候选,②有视野 ④主力不在(局部威胁≤阈值)→ 第一时间可落即落
  │                            命中即锁坐标快照(非活OL实时pos),之后幂等重发同一坐标(is_visible仍每帧重查)
  └ (视野保活留一个 ActBase:保留现 _SendOverlordToEnemy 的 kite 逻辑,别重写)

NydusRaidLoop(改造现 NydusRaidAct;不造新 FSM,在现投送里加"坑道路径选项")
  ├ wave_intent = read attack_mode_override(all_in→不退 / probe|None→劣势退)
  ├ 进/退用现有规则,路径多一个"走坑道"选项(见 §四)
  └ canal_lost 事件 → 通知 Planner 拉黑该点 + 重下恢复路径(撤退期间走地面)
```

**接线契约(评审要求定死)**:canal-被拆检测 + blacklist + 选定 zone/point 都存 **Planner**;Loop 只向
Planner 发 `canal_lost` 事件,由 Planner 负责拉黑+重选(防"两个求值器分开→门控失效")。blacklist per-坐标、
换区不清黑。`_MAX_CANALS=1` 只挡"同时多 canal",**不挡换点重下**(旧 canal 已亡才换)。**分层不合并**:
落点/视野归 Planner,投送/路径归 Loop(延续 2026-07-09 #7 正交分层)。

---

## 七、必须原样保住的血泪教训(重构不得丢)

- **is_visible 每帧真下令前重查**(踩过 170 次空放的死锁定点)。
- **落点一次锁定不漂移**(#543);被拆点**拉黑**避免选回死亡点。
- **视野即候选/OL 位置兜底**(只要 OL 活着 canal 必落得下)。
- **UNLOAD/LOAD ability 运行时探真名**(上下文能力,不硬编码 enum;2026-07-09 评审 #1)。
- **facade 新方法 → FakeFacade + `_SharpyFacadeBase` 两实现 + Protocol audit**。
- **验终态非中间 trace**:自验断言 telemetry `NYDUSCANAL` 真出现/消失、agent 真到点,不只看 log;
  记 `ActionResult`,非 Success = 被 SC2 拒。
- **Reserved 释放纪律**:raid 波每帧只圈这一波;转型/结束显式 release;macro-tail roach/queen 不 reserve;
  留 1-2 女王在家注卵。

---

## 八、测试计划

1. **单测**:候选点按打击价值排序(矿后屠农民优先 / OL 位置兜底)、pick_available_now 过滤(②有视野
   ④主力不在)、wave_intent 读 attack_mode_override 分支、canal_lost 换点。mock ai/cache,同 `test_nydus_raid.py` 范式。
2. **真局自验**(`scripts/nydus_selftest.py` 已存在,或扩 `proxy_chain_selftest` 范式,mock LLM non-realtime 并行):
   验 telemetry 终态——canal 真落(NYDUSCANAL 1)、被拆后换点重落(消失→再出现)。
3. **build_acceptance**:1 VeryEasy + 3 VeryHard,看**落地率 KPI**(见下定义)、**经济杀伤**(2026-07-09
   记分卡④,别只看落地率绿)、胜率不退。
4. **落地率 KPI 分母定死**:= 有 NydusNetwork 完成的局里 telemetry 曾出现 `NYDUSCANAL≥1` 的比例(每局
   一票,非每次尝试),对齐现"49% 天花板"口径可比。

---

## 九、分阶段实施(评审重划范围)

- **P1 落点模型 + 视野冗余(核心,先破 49%)**:目标区(默认主基)一圈 can_place 候选 + OL 位置兜底 +
  机会主义扫描(②有视野 ∧ ④主力不在=局部威胁≤阈值,**无佯攻对侧门**)。替换现"锁一点等窗口 timeout"。
  **视野同步升级**:`_SendOverlordToEnemy` 从"维持 1 只"改"维持 2-3 只"冗余视野(用户提到核心,病根),
  配 OVERLORDSPEED kite。→ **单独验落地率破 49%**(不依赖 P2)。**验收同时看经济杀伤**(默认落主基
  矿后,STRIKE 能锚到农民)。佯攻(主力在家时才引离)也在 P1(FeintSquadAct 引主基防守者,与"落主基
  矿后"几何自洽,保留)。
- **P2 坑道路径 + 涌现多波(核心)**:**先做 canal 反向装载 spot-check**(§四 UNVERIFIED);闭环成立
  → 撤退/进攻加"坑道短路"路径选项 + wave_intent 读 attack_mode_override;不成立 → 撤退只走地面。
  → 验"打不好撤回保实力、攒够再来"涌现,canal 复用。
- **P3 话术精修(占位,非核心)**:"换个点 / 打三矿"等;wave_intent 已在 P2 接好(读现成字段)。
- **P4 视野/落点精修(条件触发)**:P1 的多派 OL 仍不稳 → 贴边接近路径(`plan_avoid_path`)+ OL 分区
  驻守;需要"落三矿迂回扑二矿"再加三矿 zone 选择。**佯攻"对侧"方位绑定(几何重)只在这里做,绝不进 P1**。

**多波终止 + Reserved 释放**:不能只靠 `release_after_s` 硬计时(多波循环会中途被砍)。释放由
`opening_completed`(canal≥1 且 roach≥6)/ 转 doctrine 触发。每阶段自验 PASS 再进下一阶段。

---

## 十、评审处置(2026-07-12 Opus 独立评审)

总评:方向对,分层正确,P1→P4 顺序成立;3 处硬伤 + 1 个战术冲突需先解决再动手。

### 采纳(动手前改/写死)

1. **P0 前置真机自验(最大盲点)**:多波 RETREAT 的"army 钻回 canal → 家 network 弹出"整条闭环
   **UNVERIFIED**——canal 侧 LOAD 能力(兵回流进敌方 canal)从没核对过。**P2 动手前先写 canal-load
   spot-check**:worm 落地→装家 network→钻出→对 canal `get_available_abilities` 看有无 LOAD、装一只兵
   进 canal→家 network `UNLOADALL_NYDASNETWORK` 验它真吐出(**telemetry 终态,非 trace**)。
   **闭环证不成 → RETREAT 降级为"兵直接 move 走地面撤离",不走 canal 回流**。
2. **RETREAT 期间暂停续兵**:Nydus 是共享乘客池,无法区分"撤退的兵"和"续兵进来的兵"。RETREAT 与
   续兵**串行不并发**(撤退时停续兵)。
3. **Planner = 纯挑点模块**(几何 + latch 缓存),**视野保活仍留一个 ActBase**(保留现
   `_SendOverlordToEnemy` 的 kite 逻辑不重写)——别造无 execute/无人每帧驱动的类。
4. **三矿边缘点不走 `behind_mineral_positions`**:该列表 zone 构造那刻算一次永久缓存,开局敌三矿
   矿脉不可见 → 对三矿基本恒空。三矿落点用 `center_location + 朝我方向量`自算边缘点;"三矿有矿/空"
   用 `zone.has_minerals`/视野判,**未侦查即当空**(保守走隐蔽落)。
5. **locked_pos 存坐标快照,非活 OL 实时 position**(现代码已对,写死防重写误改成每帧取 OL.position)。
6. **blacklist per-坐标、换区不清黑**(延续现 `_blacklisted` + `_WORM_BLACKLIST_RADIUS`)。
7. **装卸载延续 `_vibecraft_bypass_actions` 范式,不新开 facade 方法**(2026-07-09 #2)。类型统一
   `UnitTypeId.NYDUSCANAL`(无 `NYDUSWORM`,引用即 AttributeError,第 9 铁律)。
8. **落点排序砍薄**:实现 = 目标区一圈 `can_place` 候选 + OL 位置兜底,按打击价值(矿后屠农民)优先。
   不铺"左框/右框/死角"四层排序(落地率是"可见+可放"问题,不是点质量问题)。
9. **P1 验收同时看 2026-07-09 记分卡④(经济杀伤)**,不只报落地率绿(见下面战术冲突)。
10. **续兵/被逼退判定 per-instance**(canal 附近己方 supply + 敌农民计数),不聚合掩盖。

### 不采纳(评审建议保"朝我方一侧 tiebreak",与用户直接冲突)

- 评审建议落点排序保留"朝我方一侧"作 tiebreak(理由:对撤退路径有意义)。**不采纳**:用户
  2026-07-12 明确"有 canal 怎么都能撤,落点与撤退方向无关,只看屠农民打击价值"。排序按打击价值,
  **删掉撤退方向 tiebreak**。

### 用户已拍板(2026-07-12)

- **决策 A = 屠农民优先(主矿为主)**:默认走声东击西——佯攻在主基造窗口 → 落主基矿后屠农民
  (2026-07-09 Round 4 正解,**feint 语义保留**)。三矿降为**备选区**:三矿有矿则打三矿农民、
  或主矿实在开不了时才落三矿。**P1 默认目标区 = 主矿矿后**,不是三矿。空三矿迂回扑二矿归 P2+,不铺 P1。
- **决策 B = 走现有机制,不新起 director 集成**(评审高估了)。wave_intent 直接读现成字段
  `knowledge.vibecraft.attack_mode_override`(玩家点 UI 进攻按钮的 probe/all_in 子模式,经
  `_submit_tactical_action`→`facade.set_attack_mode_override`→`common_bot.py:1078` 写入):
  - `"all_in"` → COMMIT(不撤压死)
  - `"probe"` / `None` → PROBE(默认试探,劣势钻回)
  **不新增 directive 类型 / catalog / prompt 同步 / knowledge 通道**——字段+UI+prompt 全已存在。
  与 2026-07-09 #8 不冲突:#8 讲"全军按钮不指挥 Reserved 单位",这里是 raid act **主动读**一个
  玩家战术子模式字段,是自愿读、非被全军按钮指挥。**P3 因此坍缩成"读一个字段",并入 P2**。

### UNVERIFIED 清单(真机核对前不背书)

- **canal 侧 LOAD/装载能力**(兵回流进敌方 canal)—— P2 前置必验(处置 1)。
- **"canal 装载 → home network UNLOADALL 卸出"闭环** —— 家侧 UNLOAD 单独证过,整条闭环 UNVERIFIED。
- **三矿"有矿 vs 空"运行时判定** —— `zone.has_minerals`/`zone.is_enemys` 开局可用性未核对,按"未侦查
  即当空"保守处理。
- 已核可用:`enemy_expansion_zones[2]` 几何取三矿、`UpgradeId.OVERLORDSPEED`、`UNLOADALL_NYDUSWORM`/
  `UNLOADALL_NYDASNETWORK`、`LOAD_NYDUSNETWORK`(网络侧)。

---

## 十一、第二轮评审 + 用户澄清处置(2026-07-12)

第二轮 Opus 评审抓到一个**我自己引入的硬伤**:三·五 原写成"4 条硬 AND 门",与三"第一时间落"自相
矛盾,且会把落地率压到 49% 以下(与核心诉求反向)。用户同时给两条澄清,方向一致,已全部改进文档:

1. **门收敛成"② 有视野 ∧ ④ 主力不在落点区"**(§三·五 已重写)。用户定死:**最本质唯一战术条件 =
   敌方主力在不在家**;主力在家即使有视野也别强下,主力不在家哪怕佯攻没到位也下。**佯攻降为"主力
   在家时才用的引离手段",不是前置门**。这与评审"③④降软+超时地板"殊途同归,但更彻底(直接不把佯攻
   当门)。评审担心的"佯攻到位卡死落地率"随之消失。

2. **多波 = 现有进退规则 + 坑道多一条短路径**(§四 已重写)。用户定框:坑道**不是专用多波 FSM**,就是
   路径规划**多一个走坑道的选项**,"该进该退"判断规则**不变**。多波/续兵是**涌现**的,不写独立状态。
   评审担心的"bespoke FSM 复杂度 + 共享乘客池区分撤退/续兵"大幅减轻(不再有并发的续兵-vs-撤退两条流,
   只有'该进就走某路径、该退就走某路径')。反向装载仍 UNVERIFIED,证不成撤退只走地面(少一条路径,不塌)。

3. **几何自洽已恢复**(评审在"三矿默认"假设下担心佯攻二矿与落三矿同侧非对侧)。**决策 A=主基屠农民
   优先**后:佯攻正面/二矿 → 引主力离**主基矿线** → 落**主基矿后**,方位天然对立,Round 4 声东击西
   几何成立。故 P1 保留 FeintSquadAct 不悬空。

4. **评审其余 must-fix 已并入**:P1 重划(无佯攻对侧门,先破 49%)、"矿后"锚点几何自算不靠
   `behind_mineral_positions`、Planner↔Loop 接线契约(canal_lost 事件 + blacklist/zone 存 Planner)、
   落地率 KPI 分母(每局一票)、多波终止靠 `opening_completed` 非硬计时、`_MAX_CANALS=1` 不挡换点。
   均见 §四/§六/§八/§九 对应处。

**结论:两轮评审 + 用户三次澄清后,设计收敛。核心不变量**:门=②∧④(主力不在+有视野)、落点按屠农民
价值+OL兜底、路径多一条坑道选项(进退规则不变)、wave_intent 读现成 `attack_mode_override`。**P2 前置
硬门 = canal 反向装载 spot-check(验终态)**。可进入 P1 实现(先破 49% 落地率)。
