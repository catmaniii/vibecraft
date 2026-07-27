# 大件骚扰群 · 组队协同重构设计

> 状态：设计待评审（opus 独立评审后再实现）。日期 2026-06-29。任务 #580。
> 取代：#561「每艘大件套一张骚扰卡」的 `_bc_auto_harass` 特例工厂。

## 1. 问题 + 目标

### 实测病征（`bc_harass_selftest` 录路径，848 轨迹点确认）
- **绕主矿中心**：target=None 的巡逻 BC 累计绕敌主矿中心 **3200°+**（≈9 圈），固定打一个矿的 BC 正常（绕 180°）。
- **刚到就掉头**：巡逻 BC 全程 rank 切换 **28~29 次**（dwell 10s 本该最多 ~60）。
- **永远在飞、never settle**：巡逻 BC 瞄点恒离自己 **~18 格**（`plan_drop_path` 每帧给个绕开 zone 的远点，没到又换矿 → 套娃绕圈）。
- 根因链：巡逻 BC（无固定矿）→ 看不见农民就 2.5s 换矿 + 远途走 `plan_drop_path`（避开 zone 的寻路）→ 绕着主矿画弧、不落地 farm。

### 目标
1. **干掉绕圈**：不再每艘独立巡逻；全群作为整体被状态机调度到「选定的一个矿」settle，不各自绕。
2. **组队协同**（用户核心需求）：所有大件由**一条指令**控制 → 健康分驱动「够数才一起出击、不够就在家等」的动态节奏。
3. **抱团一个矿**（候选②）：安全矿评分选最优矿，全群集火；威胁来了**整群边路转移**到次优矿；只有真危险才战术跳回家，普通回家用 move 不浪费 CD。
4. **架构通用化**：建在已有 `unit_claim(recruit_new=True)`「持续征兵」机制上，加一个新任务 verb `group_harass`；副产品是「新出 X → 编组/去某任务」这套 ECA 能力被正经化。

## 2. 架构：recruit-claim + 新任务 `group_harass`

### 现有可复用机制（已验证 #521，`test_recruit_watchers.py`）
- `unit_claim(recruit_new=True, selector.unit_type=X, task=<verb><target>)`：directive 留在 `standing_orders`，每帧 `_tick_recruit`（director.py:7905）`resolve_selector(X)` 比对 `seen`，新单位并入 `_standing_order_tags[did]` **并继承 claim 的 task**（7943-7964）。✗ 撤销停止征兵。
- 即「事件(新造 X) → 动作(执行某任务) → 持久 → 可撤」**已支持**。

### 本次新增
- **一个新任务 verb：`group_harass`**（`directives/task.py` `Verb` + LLM 可解析）。它的 target = 敌方矿区（main/natural/third/auto）。
- BC 骚扰群 = `unit_claim(recruit_new=True, selector.unit_type=Battlecruiser, task=group_harass(target), target_count=∞)`：
  - 新大件每帧自动并入该 claim（复用 recruit watcher 的「发现新单位」逻辑，**但加 `target_count` cap**——见 §5，这是新代码，不是零成本复用）。
  - 由新的 **`GroupHarassAct`**（重构自 `BcRaidSquadAct`）统一调度群内所有 tag —— 健康分状态机 + 抱团 + 边路转移。

### ⚠️ 关键接线：`group_harass` 必须进 `skip_action`（opus 评审 A1，必须做）
现有 recruit-claim 对新单位会**逐 tag 下单体 action**（`_assign_standing_order_units` director.py:3946-3954 + `_tick_recruit_watchers` 7966-7974：`set_unit_role` + `execute_unit_action(verb, target)`）。现状 `harass_workers` 不冲突**纯属巧合**——它 target=None，`execute_unit_action`（common_bot.py:674-679）对 target_point=None 直接早返回不下令。但 `group_harass` 的 target = 固定矿（zone_center 可解析）→ director 会对每个被 claim/新征募的 BC 下一次 `unit.move(zone_center)`，**和 `GroupHarassAct` 当帧的 posture 调度（回家修/转移/跳）直接对冲**（尤其把正在 healing 的残血 BC 一次性 move 向敌矿）。
→ **修法**：把 `group_harass`（顺手 `harass_workers` 也纳入，去掉对 facade 早返回的隐式依赖）加进 `skip_action` 判定（director.py:3899 现只 skip cast_ability），**`_assign_standing_order_units` + `_tick_recruit_watchers` 两处都加**。让 director 只 `set_unit_role` + 维护 tag 集，**绝不下单体 action**，act 是唯一控制者。

### target=固定矿 vs auto 的 picker 关系（opus A2）
- 玩家**指定矿**（"骚扰三矿"，target=NAMED_SPOT enemy_third）→ **锁死该矿，不跑 §4 picker**。
- target=**auto**（"所有大件去骚扰"不指定）→ 才跑 §4 安全矿评分 picker 选最优矿。
- **废弃** `Director._bc_auto_harass` 那套「每艘建一张 per-BC 卡」+ `bc_harass_claims` per-tag map。改成群读 claim 的 `_standing_order_tags[did]`（整组 tag 集）。
- UI：从「N 张卡」变 **一张群卡「大件骚扰群 ×N」**（标控制艘数）。

### 数据流
```
语音"所有大件去骚扰[三矿]"
  → LLM → unit_claim(recruit_new=True, unit_type=BC, task=group_harass(third))
  → director.standing_orders + 注册 recruit watcher(kind=claim)
  → 每帧 _tick_recruit: 新 BC 并入 _standing_order_tags[did]
  → director 每 tick 把 group tag 集 + 群目标 + target_count 发布到 knowledge.vibecraft.bc_harass_group
  → GroupHarassAct.execute() 读 group → 跑健康分状态机 + 候选② → bc.move/jump
```
（保留把 group 信息发布到 knowledge 的方式，act 在 sharpy 侧读，与现状一致；只是从 per-tag map 变成「一个 group 描述」。）

## 3. 健康分状态机（核心）

### 定义
- `fit(bc)` = `hp_pct >= SALLY_HP(0.95)`（满血、可出击）。
- `group_alive` = 群内存活 BC 数。
- `group_fit` = 群内 `fit` 的 BC 数。
- `sally_threshold = min(group_alive, max(2, group_alive − 1))`（opus B1 修：避免 n=2 退化成"可单出"）。
  推演：alive=1→1（单艘也出，早期 OK）；alive=2→**2**（两艘都满血才出，不让 1 艘单冲被各个击破）；alive=3→2；alive=5→4（大群越严越抱团）。
- **绝对回撤地板**（opus B2 修，防"减员降门、添油送死"）：threshold 跟 group_alive 走会越打越少、门越低、越不肯撤。叠一条硬地板——
  群内「还能战(hp>RETURN_BAR)」**绝对数 < 2** → **强制 STAGING**（全队回家重组），不靠动态 threshold 续战。
- 群姿态 `posture`（每帧重算，**滞回 + 最小停留**防抖，opus B3/C3）：
  - 进入 `HARASS`：`group_fit >= sally_threshold`（fit=满血 95%，只有在家 repair 才到）。
  - 跌回 `STAGING`：群内「还能战」数 `< sally_threshold`（「还能战」= `hp_pct > RETURN_BAR(0.4)`，比出击门低 → 滞回，避免 95% 上下一帧一翻）**或** 触发绝对地板。
  - **posture 最小停留 3-5s**（opus 把 §9"可加"升为**必做**）：翻 posture 后锁 N 秒不再翻，挡住"出门-半路一艘修过线又掉头-到家又够又出门"的极限环。
- **per-BC 动作 = f(当前 posture)**（opus B4 澄清）：下面两个清单是**按 posture 分列**的同一艘 BC 的行为，不是并存规则——同一艘中间血 BC，posture=HARASS 时留前线打、posture=STAGING 时 move 回家。

### 每帧 per-BC 行为（给定 posture）
```
posture == HARASS:
  fit BC(≥95%)        → 去抱团目标矿 settle/farm/转移(候选②)
  被打到 ≤ jump 阈值   → 战术跳回家(紧急, 用 CD) → 进 healing
  中间血(阈值<hp<95%) → 继续打(不算 fit, 但没到紧急, 留在前线输出), 直到掉到 jump 阈值
posture == STAGING:
  所有群 BC          → 普通 move 飞回家锚点 + 修(不跳, 省 CD; 除非正被集火走紧急跳)
  在家修到 ≥95%      → 变 fit; 等 group_fit 够 threshold → 翻 HARASS 一起涌出
```

### 新 BC / 动态变化（用户第1点）
- 新 BC 只是「群成员」，行为完全跟 posture 走：
  - 当前 HARASS 且它 fit → 直接去前线汇合（不用等下一轮）。
  - 当前 STAGING → 在家等。
  - **去前线途中前方塌了（posture 翻 STAGING）→ 它也掉头回家**（move）。
- 这套「新 BC 看前方够不够强决定去/等、途中变了就回头」是**posture 的自然结果**，无需特判。

### 为什么不死锁 / 不浪费 CD
- 没有「全部回家才能再出击」的硬门 → 不会因一艘卡在外面永远 STAGING。
- 回家默认 **move**（不跳），只有「被集火/血低到紧急」才跳；「抱团优势不足就回家重组」靠 posture 的 fit-count 门自然触发，不强制召回。

## 3.6 大舰骚扰优先级行为树（2026-06-29 用户细化，**最高准则，不可偏离**）

骚扰本质 = **「隐蔽/自保」永远压过「杀农民」的博弈**。每艘 BC 每帧从高到低判，命中即执行，**全程不停移动**（move-shot，绝不站定）：

```
P0 生存底线
   · 踩 AoE effect → 闪开（_dodge_spot）
   · 血≤自适应跳阈值 / 一帧爆发掉血 → 战术跳回家（紧急，用 CD）

P1 威胁规避 ＞ 杀农民  ← 核心，最高优先于杀农民
   威胁 = 敌方大部队(成群 can_attack_air 战斗单位) + 防空建筑(spore/turret/cannon 的射程)

   【P1 链条 —— 帧级 + 群级两层，钉死不可简化（2026-06-29 补全，refs §3.6 补 1/2）】

   ── 帧级（每艘 BC，每帧判，命中即执行）────────────────────────────────────────────
   ① cheap-kill 预检（§3.6 补 1）：
      P1 触发前先判附近「孤立静态防空建筑」能否"顺手秒掉"：
        cheap_kill(building) 成立 = 同时满足：
          · 孤立：建筑 10 格内无 army（can_attack_air）/ 无其它 AA 建筑接力
          · kill viability：kill_time × building.air_dps < avg_bc_hp × 0.5
                             （kill_time = building.health / group_ground_dps）
            用游戏实时值（building.health / air_dps / bc.ground_dps），不硬编 SC2 数字。
        → cheap_kill 成立 → 不躲，群集火该建筑（move 到建筑位置，打掉解锁矿线）
                             trace: BCRAIDTRACE aa_killkite tag=.. mode=kill dist=..
        → cheap_kill 不成立（有 army 接力 / 火力不足）→ 进入 ②

   ② 精确射程规避（§3.6 补 2）：
      打不过（累计对空 DPS ≥ _P1_THREAT_DPS_FLOOR = 20）→ 规避：
        flee_dist = max(in-range 威胁的 air_range) + buffer(2)
        （避免过躲──白离太远不打农民；避免欠躲──固定 12 格还在远射程 AA 内）
        air_range 取不到退回保守 fallback 12 格。
        BC 朝远离威胁质心方向 move，刚好出所有 in-range 威胁射程外（不停）。
        trace: BCRAIDTRACE aa_killkite tag=.. mode=flee dist=..
      # 此刻不贴农民──保命优先于杀农民

   (轻威胁 DPS < floor 不触发 P1，继续 P2/P3)

   ── 群级（仅 target=auto picker 时）───────────────────────────────────────────────
      杀不掉的防空锁死的矿 → anti_air_dps_near 拉高 → score = workers - W_AA×aa_dps 暴跌
      → picker 切换到分数更高的矿 → 整群 relocate（§4 切换滞回）
      这一层是"长期避开有防空的矿"，帧级 ①②是"当下遇到了怎么处理"，两层不替代彼此。

P2 接近阶段  ← §3.6 + §4 贴边
   离目标矿区还远(未进入骚扰位) → plan_edge_path 贴地图边接近（晚被发现）

P3 骚扰阶段(已在矿区且当前安全)
   · 锚点 = **矿后**(矿线背离基地侧、贴地图边那侧)
     → 用矿体限制地面远程能打到的范围 = 自保几何 + 留逃跑路
   · 一直 sweep 移动(边打边走，绝不停)
   · 没农民可打 → move 去找农民/换更优矿(也一直动，找机会)
```

**这棵树取代旧的"kite 退 3 格"** —— P1 威胁规避是高优先级、主动拉到射程外，不是小退一步挨打（旧"被打掉血早回去"的真因）。**矿后(P3)用矿体挡地面单位**是关键自保几何。

## 4. 候选②：抱团一个矿 + 安全矿评分 + 边路接近（贴边，**不直飞、不绕圈**）

### 抱团目标选择（picker，仅 target=auto 时跑；指定矿则锁死不跑）
- `score(zone) = workers_present − W_AA × anti_air_dps_near(zone)`（有农民可打加分，防空火力减分）。
- **`anti_air_dps_near(zone)` 定义**（opus C2，需新写，现状无 zone 级 helper）：zone 矿线中心半径内**可见**敌方能打空军单位 `air_dps` 之和 + 静态防空（SPORECRAWLER/MISSILETURRET/PHOTONCANNON）。
- **未侦察矿的兜底**（opus C2）：没视野的矿 `workers=0` 且 `aa=0` → `score=0`，picker **永不主动去未侦察矿**。兜底：当所有已知矿 `score<=0`（农民都藏了/换矿了）→ 回退到「按 rank 巡逻揭视野」（飞最近未侦察矿照一眼），不在家发呆。**这个局限写进文档**。
- 选 `score` 最高的**一个**敌方矿区，全群 fit BC 去那。
- **切换滞回 + 最小停留（硬约束，opus C3，不是"低频"含糊带过）**：只有 `score(new) > score(current) × 1.3` **且** 当前矿已停留 ≥ 最小时长（如 8s）才整群转移；否则锁当前矿。防 workers/aa 帧间抖动导致整群在两矿间反复 transfer（比单艘绕圈更壮观）。
- **抱团判据**：全群去**同一个** zone（用户：人多了不抱团占不到便宜）。玩家手动「派一个大件去别处」= 另开单独 `unit_claim`，不属于本群。

### Settle（不再 eager 换矿）
- 到目标矿 → 沿矿线 farm 农民（保留现有 sweep/贴农民/风筝逻辑，**整段搬运不改数值**，opus E3）。
- **不再 2.5s 看不见农民就换矿**；只有满足上面「切换滞回」才整群转移。

### 边路接近 `plan_edge_path`（**必做**，2026-06-29 用户拍板：贴边是骚扰的命，直飞不行）
> 背景：subagent 一度改成「直飞矿线」消了绕圈，但**违背"贴边晚被发现"核心目标**（走中央=秒被发现），用户否决。`plan_drop_path` 又因"斥力远离 zone **中心**"绕主矿画弧（3200°）。两者都错。正解 = 沿**地图矩形边界**走（沿四条边≠绕一个点画圆）。

`plan_edge_path(start, target, playable_area)`：
1. **确定性沿边界**：把 start、target 投影到 `playable_area` 矩形周长；沿周长（4 角为节点）从 start 投影到 target 投影，取**离敌方主矿更远的那条弧**（最隐蔽，用户默认 ⓐ）。
2. waypoint = `[start, 入边界点, 沿途角点…, target 边侧点, target]`，BC 逐点飞，**全程贴地图外围** → 避开敌方中央军队视野 → 晚被发现。
3. 末段从 target 的**边/角侧**切进矿线（矿后），不从中央侧进。
4. **绝不绕 zone 中心**；**waypoint 一次锁定缓存**（按 target key），到位再清，中途幂等重发同一串，别每帧重投影翻角漂移（CLAUDE.md 强规则）。
- `playable_area` 现成（drop_path.py:110-115）。空投(drop)接近复用同一函数（同目标：晚被发现）。
- **自验**（取代单纯"绕角"）：量①接近段轨迹离地图中线平均距离（贴边=远离中线）②敌方首次看见 BC 的游戏时刻（越晚越好）③威胁来时 BC 是否拉到威胁射程外而非原地挨打。

### 早跳缓解
- 保留自适应跳跃阈值，但抱团后**每艘承受的 incoming DPS 被分摊** + 集火能先秒掉单点防空 → 自然少早跳。可选：抱团时把 `_JUMP_SAFETY_S` 调小一点（评审/实测定）。

## 5. 成员管理（`target_count` 旋钮）—— ⚠️ 全新机制，非"复用不改"（opus D1）

事实：`UnitClaimPayload` **没有 `target_count` 字段**，`_tick_recruit_watchers`（director.py:7927-7945）**无条件 enroll 所有 new_tags、无任何 cap**；`_release_standing_order_units`（4155）**只能整条 directive 全释放**。所以下表每一行都是新代码，§11 切分单列。

| 事件 | 处理 |
|---|---|
| 新大件出生 | recruit watcher 入伍前判 `len(group) < target_count` 才并入（**新增 cap 判断**） |
| 群内大件死 | 从 `_standing_order_tags[did]` 移除（count−1）；target 允许则下帧自动替补 |
| 玩家「撤回 N / 少派 N」 | `target_count −= N`，立即**部分释放** `(count − target_count)` 艘——**新写 partial-release**（从 tag 集移 N 个 + 逐个 `release_unit_role` + 处理 `_displaced`）。**优先释放「满血在家待命」的**（opus D2 修：不是"血最低"——血低可能=前线挨打，release 即送菜；满血在家的最健康、对 farm 扰动也最小） |
| 玩家「减到 K」 | `target_count = K`，释放多余 |
| 玩家「停止骚扰」 | `target_count = 0`，释放全部，**工厂(claim)暂停**（留着、不再征兵），只有 ✗ 卡才真删 |
| 玩家「所有大件去骚扰」 | `target_count = ∞`，把主力里的大件也并入 |

- **释放后清 `seen`**（opus D2）：partial-release 的 BC 仍活着且在 `watcher["seen"]` 里 → 默认不会被重新征募。**从 `seen` 移除**它们 → 支持"骚扰减到3 → 再加回来"（target 调高时这些 BC 重新入伍）；cap 防止释放当帧立即被重抓（count 已等于 target）。
- **暂停态短路**（opus D3）：`target_count=0`（或 group 满）时，watcher **短路掉 enroll**（甚至跳过 `resolve_selector`），别让"暂停"变成每帧空扫全图。
- 释放 = `release_unit_role` 还给 sharpy free_units（归 PlanZoneAttack 跟主力抱团）。控制权语义不变：群内 BC 是被 claim 的独占单位，全军撤退/进攻不碰（规则2）。
- **`target_count` 做成 `UnitClaimPayload` 通用 recruit 上限字段**（不是 BC 专用计数，opus E2）——顺手补上"recruit 无上限"的现有缺陷，且是将来 ECA 泛化的天然子集，不返工。

## 6. 语音映射

| 玩家话语 | directive |
|---|---|
| 所有大件去骚扰 / 大件去骚扰[主矿/三矿] | 创建/恢复 group_harass claim，target=∞，[目标矿] |
| 派 N 个大件去骚扰 / 出 N 个大件骚扰 | target_count = N |
| 撤回两个 / 少派两个大件骚扰 | target_count −= 2 + 释放 2 |
| 骚扰的大件减到三个 | target_count = 3 |
| 停止骚扰 / 大件别骚扰了 / 都别烧了 / 大件回来 | target_count=0 + 释放全部 + 工厂暂停 |
| （手动单飞）派一个大件去[别处]骚扰 | 另开单艘 unit_claim，不入群 |

LLM 解析这些 → `group_harass` task + target_count 字段。需补 few-shot + rules（CLAUDE.md：改 `docs/llm_prompt/`，重 dump）。

## 7. UI

- 一张群卡 `command_cards`：`type=group_harass`、`display="大件骚扰群 ×N → <矿>"`（N=控制艘数，i18n）、`revokable`。
- 玩家 ✗ → 释放全部 + 删 claim（停征兵）。

## 8. 自验方案

复用 `scripts/bc_harass_selftest.py` + `BCRAIDPATH` 轨迹 trace（已加），新增断言：
1. **绕圈消失**：每艘 BC 绕敌主矿中心累计角度 **< 720°**（旧 3200°+）；瞄点-自身距离中位数收敛（旧恒 18）。
2. **抱团**：任一 HARASS 时刻，fit BC 两两间距 < 抱团半径（不散开）。
3. **出击/回收节奏**：posture 转换日志；`group_fit >= threshold` 时出击、跌破时回家；回家以 **move** 为主（紧急 jump 计数低）。
4. **成员管理**：debug 生 N 艘 → 注入「撤回 2」→ 验 telemetry 群 count 减 2、释放的归 free。
- 跑 VeryEasy（几何/绕圈）+ VeryHard（出防空 → 触发转移/早跳缓解）。

## 9. 风险 / 边角

- **posture 抖动**：滞回（出击门 95% / 留场门 40%）+ 可加最小停留时间。
- **边路寻路**：`plan_edge_path` 别每帧重算漂移；目标锁定缓存。
- **claim re-Reserve churn**：recruit-claim 每帧 re-Reserve 群 tag（现有机制，不变）。
- **群空但未停**：claim 留着（用户：留着），新大件继续并入，协同状态连续。
- **跟主力抢人**：target=∞ 时所有 BC 进群骚扰；玩家要 BC 守家/参战 → 降 target 或 ✗。

## 10. 不做 / 延后（YAGNI）

- **recruit 的「条件」层**（用户提的「可能加个条件」：只征前 N / game_time 后 / 满足某条件才编入）：本次只做 `target_count` cap，复杂条件门（`activate_when` on recruit）延后。
- **完整 ECA 泛化**（「新出任意兵种 → 任意任务」一条命令）：本次只落 BC `group_harass` 一个实例 + 复用已有 recruit-claim；泛化成统一「production-trigger」指令类型延后。
- **分散骚扰**（多 BC 各打不同矿自动协同）：本次默认抱团；分散靠玩家手动单飞。

## 11. 实现切分（opus 评审已过，按此写实施 plan）

> opus 三个"必须改"已并入下面 1/2/3，全程注意 **CLAUDE.md「目标点一次锁定别每帧重选」+「重构搬运已验证微操不改数值」(E3)**。

1. **新 `Verb.GROUP_HARASS` 的多处同步**（不止 verb 枚举，opus E1）：
   - `task.py` `Verb` 枚举 + LLM rules/few-shot + 重 dump。
   - **`skip_action` 接线（必须，opus A1）**：`group_harass`（+ `harass_workers`）加进 skip 判定，`_assign_standing_order_units` + `_tick_recruit_watchers` **两处**都加，director 不下单体 action。
   - `_VERB_COLORS`(director.py:129) + `_VERB_LABELS`(143) 补 group_harass 条目（有兜底、低危，但补）。
2. **`target_count` 通用 recruit 上限（全新，opus D1）**：① `UnitClaimPayload` 加 `target_count` 字段（通用，非 BC 专用）；② recruit watcher 入伍前判 cap + 暂停态短路 resolve；③ **partial-release**（释放 claim 内 N 个 tag 子集，优先满血在家；清 seen 支持召回）—— 现有只能整条全释放，要新写。
3. **彻底废 `_bc_auto_harass`（展开，opus D4/E1.5）**：删 `DirectiveType.BC_AUTO_HARASS` payload 类型 + `_tick_bc_auto_harass`(9087-)/`_all_harnessed_bc_tags`/`_publish_bc_harass_claims` 三 helper + 改 `bc_rush` 自动建卡（9102-9116 once-flag 防复活语义保留，改成自动提交 group_harass claim）+ **清所有 BC_AUTO_HARASS 引用**（submit 路由 / UI 卡片 / i18n / `verb==HARASS_WORKERS` 的 `_publish_*` 过滤 9194/9214 同步换 verb），别留悬空路由。
4. **`GroupHarassAct`**（重构 `BcRaidSquadAct`，已验证微操整段搬运不改数值）：① **§3.6 优先级行为树**（P0 生存 → P1 威胁规避＞杀农民 → P2 plan_edge_path 贴边接近 → P3 矿后骚扰，全程不停 move）② 健康分状态机 posture（滞回 + 绝对回撤地板 + 最小停留）③ 抱团 picker（仅 auto，切换滞回）+ `anti_air_dps_near` ④ **`plan_edge_path` 必做**（用户拍板，贴边接近，不直飞不绕圈）⑤ P1 威胁规避：算敌方大部队 + AA 建筑射程，打不过就拉到射程外/横向逃，不原地 kite 挨打 ⑥ 矿后锚点（矿线背基侧）。
5. UI 群卡（标控制艘数）+ i18n。
6. **自验**：`bc_harass_selftest` 断言扩展（绕角 < 720° / 抱团间距 / 出击回收节奏 / 撤回释放）；先量绕角决定要不要 edge-path；VeryEasy + VeryHard。
7. 文档：ARCHITECTURE（新 verb/数据流/skip_action 不变量）+ USER_GUIDE（话语示例）+ CHANGELOG + pitfalls（如有）。

## 12. opus 评审已采纳项小结（2026-06-29）
- **必须改（已并入设计）**：A1 skip_action 真冲突（§2 ⚠️ + §11.1）/ D1 target_count 是新机制（§5 + §11.2）/ D4 废旧路径范围（§11.3）。
- **建议改（已并入）**：B1 threshold 下限（§3）/ B2 绝对回撤地板（§3）/ B3+C3 滞回+最小停留（§3/§4）/ C1 edge-path 先缓做（§4/§11.4）/ C2 aa 定义+未侦察兜底（§4）/ D2 释放满血在家+清 seen（§5）。
- **低危（已并入）**：B4 per-BC=f(posture)（§3）/ D3 暂停短路（§5）/ E1.4 verb 配色（§11.1）/ E3 搬运不改数值（§11 抬头）。
- **外部符号**：评审确认无新引入 SC2 ability/enum（沿用 `bc_raid_act.py` 已验证的 tactical jump/effect 闪避），通过。
