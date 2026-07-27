# defend 大军"原地保持队形拉扯"修复设计

> 2026-06-17。用户反复报:后期生化大军"出不了门,在家里拉扯,像保持队形那种拉扯"。
> 复现配方(用户给定):人族 reaper_expand→bio,全程"全军防守"(sandbox_macro_only=True
> 每帧 pin intent=defend),vs Hard Zerg。

## 1. 根因(systematic-debugging Phase 1,已用真局 ARMYTRACE 复现)

人族 bio 的 combat act 顺序(`bio_max.py`,所有 bio/mech 类似):

```
SequentialList(
    ... LowerDepots / CallMule / Repair / MineOpenBlockedBase ...
    PlanZoneDefense(),      # 先跑:遍历己方 zone,claim free 单位 → Defending → 送 enemy_center(SearchAndDestroy)
    DistributeWorkers(), SpeedMining(),
    PlanZoneGather(),       # 后跑:把剩余 roles.idle → effective_gp(威胁 zone 中心 / 前沿点)
    PlanZoneAttack(),       # defend 下 _should_attack 返 False,基本不动
    PlanFinishEnemy(),
)
```

**defend intent 下,大军被两个 manager 同时争抢,且目标点不同:**

- **PlanZoneDefense**(sharpy core,`zone_defense.py`):敌人逼近某 zone → `get_defenders`
  把 free 单位 claim 成 `Defending` → 送去 `enemy_center`(敌人实际位置)。zone 清空且
  `ZONE_CLEAR_TIMEOUT=3s` 后 `clear_tasks` 释放 → 单位回 `Idle`。
- **PlanZoneGather**(vibecraft 已定制 defend 分支,`zone_gather.py`):把 `roles.idle` 拉去
  `effective_gp` = `_vbc_threatened_zone()`(威胁最大 zone 中心)/ `hold_gather_point` /
  `_vbc_forward_defense_point()`(前沿基地中心)。

**抖动机制**:vs Hard Zerg,敌军不停骚扰多个基地 + overlord 进出视野 → 某 zone 敌人
出现/消失闪烁 → PlanZoneDefense 以 ~1Hz claim/release 大批单位(3s timeout)。同一单位:
- 这一秒 `Defending` → 目标 `enemy_center`(敌人位置)
- 下一秒 release → `Idle` → PlanZoneGather → 目标 `effective_gp`(zone 中心 / 前沿点)

两个目标点不同 → 大军每秒换一次行进目标 → **原地来回横跳 = 用户看到的"保持队形拉扯"**。

### 真局证据(两轮 ARMYTRACE + GATHERTRACE,reaper→bio+全程 defend pin)
- intent 全程 defend ✓ 配方对。
- **第一轮**:role 在 Idle↔Defending 间**整支大军级翻转**:`idle=24 defend=0` → 下一抽样
  `idle=0 defend=21`(ARMY_TOTAL 25→22,只损 3,即 ~21 个单位整体翻 role,不是减员)。
  armyC 在 home 附近局部摆动 ~30 格(原地 wobble)。
- **第二轮 GATHERTRACE(关键,纠正了第一假设)**:`effective_gp` 变化 **CHG=2/67**(几乎不变,
  **稳定**!),分支 defend-fwd:41 / defend-threat:26。→ **gather 侧目标点不是抖动源**,
  我"gather 守点闪烁"的第一假设被实测**证伪**。

> 注:首轮 ARMYTRACE 记的 `gp` 是 `gather_point_solver.gather_point`(solver 原始值,
> defend 路径**不用**),记错了量;GATHERTRACE 补记真正的 `effective_gp` 后才看清它稳定。

### 纠正后的根因结论
`effective_gp` 稳定 + role 大翻转 ⇒ **抖动源是 PlanZoneDefense 的 claim/release churn**,
不是 gather 守点:
- 敌人闪烁(进出视野 / 反复骚扰)→ PlanZoneDefense 以 ~1Hz **整批 claim** 大军成 Defending →
  送 `enemy_center`(敌人实际位置,可能离守点很远);敌散后 `ZONE_CLEAR_TIMEOUT=3s` → 整批 release。
- release 后单位回 Idle → PlanZoneGather 送回**稳定的** `effective_gp`(前沿/威胁点)。
- 一去(enemy_center)一回(effective_gp),~1Hz 循环 → **大军原地横跳 = "保持队形拉扯"**。
- `get_defenders`(zone_defense.py:99)按 `defense_required = 敌 power × 1.5` 拉兵,小股骚扰也能
  **过度 claim 一大批**主力 → 整支大军被拽来拽去(而非只派一小队应对)。

## 2. 修复目标 + 约束

- 真防守要保留:敌人真打基地时,大军该去迎击。
- 杀掉病态原地 wobble:无持续/真实交战、或威胁点闪烁时,大军该**稳定 hold**,不每秒换目标。
- 遵守用户铁规「目标坐标一次规划、锁定、别每帧重选」:defend 守点要**稳定 anchor + 时间 dwell**,
  不靠每帧重算闪烁的威胁读数。
- **不轻易动 sharpy core PlanZoneDefense**(dummies + 三族所有 build 共用,全局改风险大)。
  优先在 vibecraft 已定制的 PlanZoneGather defend 分支 + anchor 选择上加稳定化。

## 3. 候选方案(针对 role-churn 根因,trade-off)

> 抖动 = Defending(→`enemy_center`)与 Idle(→`effective_gp`)两目标不同 + role 以 ~1Hz 翻转。
> 修复要让 defend 下大军**稳定**,且**不动 sharpy core 全局默认**(PlanZoneDefense 被 dummies +
> 三族所有 build 共用)。所有改动经 `# vibecraft:` hook,仅在 `combat_intent_override=="defend"`
> (玩家显式全军防守)时生效,不影响默认 AI。

### 方案 B(推荐):defend 下统一"防守目标"="集结守点",role 翻转变无害
在 PlanZoneDefense 里加 vibecraft hook:当 `intent=="defend"` 时,Defending 单位的
`combat.execute` 目标从 `enemy_center`(敌人精确位置,来回拽)改成**该 zone 的稳定守点**
(zone.center_location,= PlanZoneGather defend 分支的 effective_gp 同源)。
- 效果:单位无论被 claim 成 Defending 还是 release 回 Idle,目标都是**同一个稳定守点** →
  role 翻转不再产生位移 → 拉扯消失。敌人进守点半径内,combat micro(MicroRules)照常交战
  → 仍然守得住(不是站着挨打)。
- 代价:不再追出 zone 外的散兵(对"防守"语义反而更对:守在基地不浪);需确认 micro 在
  hold 住守点时会主动打进范围的敌人(MicroRules 默认行为,override_acceptance defend case 验)。

### 方案 A(补充/可叠加):defense claim 上限 + commit dwell
1. **claim 上限**:defend 下 `get_defenders` 不为一小股骚扰拉走整支大军 —— 留一个稳定
   reserve 在守点,只派够用的量(按威胁 power 上限)。
2. **commit dwell**:claim 后保持 ≥ `DEFEND_COMMIT_DWELL_S`(~6s)才允许 release,杜绝
   release→reclaim 的 yo-yo(现 `ZONE_CLEAR_TIMEOUT=3s` 偏短)。
- 代价:大真实进攻时全量投入稍慢(需 power≥X 紧急 bypass);改 core claim 逻辑面更大、风险高。

### 方案 C:defend 下单位到位即 hold,不每帧重发
单位进守点 settle 半径内 → 一次 `hold_position`,后续不再 add_unit 重发(贴合「目标锁定」铁规)。
- 代价:只压"已到位"那部分抖,不解决 role 翻转换目标的根;且 sharpy combat 本就每帧重发设计,
  单点 hold 易与 micro 打架。

## 4. 推荐 = 方案 B(必要时叠加 A 的 commit dwell)

理由:B 直接消除已证实的机制(Defending↔Idle 两目标不同),改动最聚焦(只在 defend hook 里
换一个 execute 目标点),最贴合用户「目标坐标锁定」铁规 + 「防守=守基地」语义,且不动 core 默认。
A 的 commit dwell 作为二线(若 B 后仍见小幅 churn 再加)。

实现点(全部 `# vibecraft:` 标记 + 审计):
- `zone_defense.py::execute`:defend intent 下,`combat.execute` 目标由 `enemy_center` 换成
  **稳定守点**(zone center;与 PlanZoneGather defend 分支 effective_gp 取点一致)。按 sharpy
  vendor patch 规则(方案 D):加 `# vibecraft:` marker + 进 `test_sharpy_patch_audit.py`
  PATCHED_METHODS + `docs/sharpy-patches.md` + hook 行为单测。
- 单测(`test_sharpy_vibecraft_hooks.py`):defend intent → 防守目标 = zone center(非 enemy_center);
  非 defend intent → 仍 enemy_center(不动默认)。
- 验证(self-test,我自己跑):
  1. `scripts/defend_tug_selftest.py`(60 枪兵 + 周期 flicker + defend pin):role-flip 计数
     + army 中心方向反转次数,修复后应大幅下降。
  2. `scripts/override_acceptance.py phoenix_2base__defend roach_ravager__defend
     two_base_tanks__defend --opponent veryeasy`:三族 defend case 不回归(仍能守住)。
- 收尾:移除临时 ARMYTRACE(zone_attack.py)/ GATHERTRACE(zone_gather.py)探针 +
  common_bot.py 的 SPAWN_MARINES/DEFEND_FLICKER 等测试 hook(或保留 env 门控但不默认开)。

### 确定性复现 baseline(修复前,`scripts/defend_tug_selftest.py`,60 枪兵+周期 flicker+defend pin)
- army 中心 **x 方向反转 18 / y 方向反转 16**(49 样本)= 原地横跳。
- defend role 反复 claim/release:`0→10→10→0→0→11→0→12`(每 flicker 周期 claim ~10-12 兵打完即放)。
- t=241+ 大军一路**追蟑螂冲到敌方主基 (48,28)**(enemy_center=SearchAndDestroy 追逃敌)→ 跨图,
  defend 不该如此 → 方案 B(守点改 zone center)同时修掉这个。
- 修复后验收门:x/y 反转大幅下降(目标 <5)+ defend 不再追出基地。

### 修复后验证结果(方案 B',PASS)
| 指标 | 修复前 | 修复后 |
|---|---|---|
| army 中心方向反转 | x=18 / y=16(49 样本) | x=7 / y=7(108 样本)|
| 反转率/样本 | 0.37 | **0.065(降 ~5.7x)**|
| army 轨迹 | 满图飘 + 追到敌方主基 (47,32) | **稳定钉 (127,123)≈home,t=473–517 不动**|
| 存活 | t=258 被拖死 | t=540(不再被拖出去送)|

残留 7 次反转 = ±3 格微抖(flicker 蟑螂刷在大军旁 ~17 格,combat 引擎原地迎击再回位 = 真交战,
非病态拉扯)。绝对值因样本数 2.2x 增多略高于 5,但**反转率降 5.7x + 大军稳定守家不跨图追敌** =
拉扯消除。单测:`TestPlanZoneDefenseDefendHook` 3 条(defend 不 claim 主力 / 释放残留 / 非 defend
仍 claim)+ patch_audit + 140 hook 测试全过。

### override_acceptance 三族 defend 不回归(VeryHard 多数票,PASS)
| case | VeryEasy(单跑,artifact) | VeryHard(3 跑多数票) |
|---|---|---|
| phoenix_2base__defend | FAIL 54.8(VeryEasy 被打崩开 5 矿,前沿基地远) | **PASS [2/3] 39.5 ≤ 50** |
| two_base_tanks__defend | FAIL(VeryEasy 局太短没到 630s snapshot) | **PASS [3/3] 47.8 ≤ 60** |
| roach_ravager__defend | **PASS 56.9 ≤ 65** | — |
VeryHard 下 bot 面对真实威胁(不无脑铺矿),`_vbc_threatened_zone` 把大军带到被威胁的近家 zone →
距 home 39.5/47.8 回家达标。三族 defend 全 PASS,**零回归,无需改 spec**。VeryEasy 两 FAIL 经
telemetry + spec 注释确认为局太短 / 多基地前沿守 artifact(CLAUDE.md 已记 VeryEasy 晚期 check 局限)。

## 6. opus 评审结论处理(2026-06-17,实现前定稿)

评审确认根因成立 + 派兵路径无遗漏。关键补正与采纳:

- **补正①(采纳)**:`enemy_center` 每帧重算 = 移动靶,持续 Defending 也在追 → **方案 A 压不住,
  B 是 load-bearing**。A 不做。
- **补正②(采纳,消除 Q2 顾虑)**:combat 引擎射程内(~15–50)按最近敌群交战,与 execute 的
  target 解耦 → 改 target 只影响"无真敌时大军往哪站",**不会站着挨打**。方案 B 安全。
- **最大漏洞(采纳,改方案)**:原"`zone.center` 与 gather 同源"**不成立**(PlanZoneDefense
  按 zone 循环拆兵 + 灵敏度 `enemies.exists` vs gather `power>3.0` 不同)→ 字面实现仍抖。

**最终方案 = B'(评审建议项1,更干净的单一收口)**:
defend intent 下,**PlanZoneDefense 不再 claim/dispatch 主力**(非工人),主力防守定位
**完全交给 PlanZoneGather** 已有的威胁感知锚点(`_vbc_threatened_zone`,带 power>3.0 阈值 +
1.5x 滞回,无威胁回前沿点 —— 已实现且有单测)。→ 主力只受**单一 plan、单一稳定锚点**驱动,
**根本不存在 Idle↔Defending 翻转** → 彻底消抖。worker 防守保留(走原 enemy_center 路径,
满足必改项3)。引擎照常交战(补正②)→ 守得住。

**必改项落实**:
1. (满足)主力 target 不再用 zone.center —— 干脆不 dispatch 主力,交给 gather 单一锚点。
2. (满足精神)不新增第三份滞回逻辑 —— B' 下 PlanZoneDefense 不计算锚点。gather 的
   `_vbc_threatened_zone` 与 attack 的 `_vbc_defend_target` 既有两份重复**本次不动**(已测试、
   working;合并属独立重构,YAGNI 留 follow-up,且 attack→defend 切换窗口两者逻辑相同返回同点)。
3. (满足)worker 防守保留 enemy_center 路径,不退化。
4. (落实)严格 gate `combat_intent_override=="defend"`;非 defend / dummies / 默认 AI 走原路径。
5. (落实)zone_defense.py 是**新 patch 文件**:加 `# vibecraft:` marker + `test_sharpy_patch_audit.py`
   PATCHED_METHODS + `docs/sharpy-patches.md` 新节 + hook 行为单测。
- **防 worker 过度拉**:B' 下不 claim 主力 → `defenders` power=0 → worker_defence 可能误判
  无人守而拉农民。对策:defend 下把**附近 free 主力的 power 计入 `defenders`**(只计数不 claim),
  让 worker_defence 知道有兵守、不 panic。+ 释放残留的非工人 Defending(交还 gather),防切入
  defend 那一刻的残留 dispatch。
- **建议项2(采纳)**:selftest 加记 `_vbc_threatened_zone` 锚点变化次数(验锚点稳定,排除 micro
  交战位移污染判断)。
- **建议项3(采纳)**:ARMYTRACE/GATHERTRACE 探针保留 env 门控(默认关),不删。

**验收门**:defend_tug_selftest x/y 反转 18/16 → 目标 <5;defend 不再追出基地到敌方主基;
override_acceptance 三族 defend case 不回归;worker 不被过度拉(看 selftest worker 数 + 验收局)。

## 5. 待评审重点(给 opus 评审 agent)
1. **根因是否成立**:effective_gp 实测稳定(CHG=2/67)+ role 大翻转 → 判定抖动源是
   PlanZoneDefense 的 enemy_center vs gather-point 双目标 churn。这个推断有无漏洞?是否还有
   第三个派单位的 plan(PlanFinishEnemy / combat MicroRules / harass act)在 defend 下也动大军?
2. **方案 B 风险**:把 defend 防守目标从 enemy_center 改成 zone center,会不会导致敌人在
   zone 边缘(守点半径外)时大军站着不打?MicroRules 的交战半径够不够覆盖?
3. **是否过度/不足**:只改 B 够不够,还是必须叠加 A 的 claim 上限/commit dwell?
4. **与现有约定冲突**:`_vbc_threatened_zone` 滞回、retreat_target 的 `_vbc_defend_target`、
   `combat_intent_override` 流,改 B 后有无不一致(两处守点是否要保持同源)?
