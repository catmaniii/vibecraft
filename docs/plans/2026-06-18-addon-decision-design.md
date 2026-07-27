# 人族产能挂件决策 + 3矿5bb 调序设计

> 2026-06-18 用户。两个产能效率问题:① 产能建筑(兵营/重工/机场)造好不第一时间挂科技/双倍;
> ② bio_stim(3矿5bb)开矿过激进(二矿一出就开三矿、没爆兵前压)。用户拍板的挂件决策规则见 §1。

## 0. 现状(调研确认)
- 挂件目前每个 build 在 plan 里硬写 `Step(建筑ready, BuildAddon(...))`,本意建好即挂,但 ① 枪兵
  `priority=True` 抢光 SCV,挂件 step 被饿死/拖延;② 有的 build 漏挂/排序不当。无统一默认 + 无"挂科技
  还是双倍"的智能决策。#542 另有"出兵时缺挂件才补"的按需路径,两套不同步 → 观感"造好半天不挂"。
- `StructureItem`(directives/models.py:371):`structure_type` + `target_count` XOR `delta`。可建
  `BarracksTechLab`/`BarracksReactor` 作为 structure_type,但**无法表达"4 兵营里 2 科技 2 双倍"的分配**。
- **bot 主动弹窗已支持**:director.py:3089 townhall build_at 模糊时,bot 自己 emit `ClarificationRequest`
  (≥2 `ClarificationOption`,每项带一份 directive 批)→ 复用 `_pending_clarification` 通道 → 前端
  `ClarificationOverlay`。挂件 3 选项直接复用。
- **build 目标兵种**已声明(`TerranUnit(MARINE,60)/MARAUDER,16/MEDIVAC,8/SIEGETANK,2...`)→ 需求信号现成。

## 1. 挂件决策规则(用户拍板,三分支)
- **① 玩家明确指定挂法 → 直接执行**:"补4bb,2科技2双倍" / "补5bb,3科技其它不挂" / "补3bb不挂附件"。
- **② 玩家"补Nbb"没说挂件 → 弹窗 3 选**:a)不挂　b)按 build 推荐(显示具体如"1科技+3双倍")　c)取消重说。
- **③ bot 自主 build 进度造产能建筑 → 静默"建好即挂",动态决定科技/双倍,不弹窗**(避免刷屏)。

挂件类型 = **需求驱动**:看要出什么兵。科技绑:光头/幽灵(兵营)、坦克/雷神/飓风(重工)、女妖/渡鸦/战巡/
解放者(机场)、+ 兴奋剂/护盾等研究;双倍绑:海量枪兵(兵营)、火蝙/地雷(重工)、医疗船/维京(机场)。

## 2. 方案(P1 挂件决策 + P2 3矿5bb 调序,正交)

### 2.1 schema:StructureItem 加挂件分配(P1)
`directives/models.py::StructureItem` 加可选字段:
```python
addon_plan: dict[str, int] | None  # {"techlab": N, "reactor": M, "none": K},和 ≤ 该建筑数
```
- 仅对产能建筑(Barracks/Factory/Starport)有意义;非产能填了忽略 + 校验告警。
- `None` = 未指定 → 走分支②(玩家指令弹窗)或③(autonomous 动态)。
- 别名:科技/科技附件/科技挂件/科技实验室=techlab;双倍/反应堆/反应炉=reactor;不挂/不挂附件/光=none。
- 按「新增字段三处同步」:schema(models) + Director 执行 + LLM prompt(rules+few_shot)+ dump。

### 2.2 LLM 解析(P1)
`docs/llm_prompt/rules.md` + `few_shot.md` 加规则/例子:
- "补4bb,2科技2双倍" → `StructureItem(Barracks, delta=4, addon_plan={techlab:2,reactor:2})`
- "补5bb,3科技其它不挂" → `delta=5, addon_plan={techlab:3, none:2}`
- "补3bb不挂附件" → `delta=3, addon_plan={none:3}`
- "补4bb"(没提挂件) → `delta=4, addon_plan=None`(触发弹窗)
- "给兵营挂俩双倍" → 纯挂件指令(对已有兵营),addon_plan 走"对现有无挂件建筑补挂"。

### 2.3 弹窗(分支②,P1)
Director submit 一条产能建筑 StructureOverride 且 `addon_plan is None` 且 `structure_type∈{兵营/重工/机场}`
→ 不直接执行,emit `ClarificationRequest`(复用 townhall 那套),3 个 `ClarificationOption`:
- a) `不挂附件` → 该 StructureItem `addon_plan={none: N}`
- b) `推荐:N科技+M双倍`(label 现算现填) → `addon_plan=_recommend_addon_mix(type, N)`
- c) `取消` → 空 directive 批(什么都不做)
玩家点选 → 现有 `submit_clarification_choice` 落地对应批;点 × = cancel。

### 2.4 推荐 / autonomous 动态逻辑(P1 核心)
`Director::_recommend_addon_mix(building_type, count) -> {techlab, reactor, none}`:
1. **需求集** = build 目标兵种(plan 的 TerranUnit 列表)∪ 活跃 ProductionOverride 兵种 ∪(可选)最近一条出兵指令。
2. 对 building_type 把需求兵种分类:**需科技** vs **双倍友好**(查表 `_ADDON_BY_UNIT`)。
   + 若 build 会研究该建筑相关升级(兴奋剂/护盾等)→ 需科技 +1。
3. **保底**:每类产能建筑 ≥1 科技(若有任何需科技兵种/研究)。
4. **分配 count**:已有挂件计入 → 先补到"保底科技数",再按需求里"需科技 : 双倍友好"的兵种比例分剩余;
   纯 mass(只枪兵/只医疗船)→ 除保底外全双倍。
- autonomous(分支③):产能建筑建好且无挂件 → 调本函数定类型 → emit 挂件 build(见 2.5)。

### 2.5 建好即挂可靠性(分支③,P1)
Director 加默认策略循环(或挂在现有 production tick):产能建筑 complete + 无挂件 + 没在建挂件 + build 未
opt-out → 用 2.4 选类型,emit `StructureOverride(addon, priority=高)`,**优先级足够不被枪兵饿死 SCV**。
- 复用 #542 的 `_emit_addon_build` / `_build_addon_on_parent`(已有起飞挪位)。
- **opt-out**:极少数 build(如纯枪兵 rush 不要科技)plan 里标 `first_addon=False` → 不自动首挂。
- 去重:`_auto_prereq_emitted` / 类似集合防每帧重 emit。

### 2.6 3矿5bb 调序(P2,正交,empirical)
`auto_combat/terran/plans/bio_stim.py`:`Expand(3)` 现 `Step(UnitExists(CC2), Expand(3))` 太早。
改成"二矿采起来 + 爆一波兵前压"后再三矿:
- `Expand(3)` 门改为更晚的信号:如 `UnitReady(CC2)` + marine 数 ≥ 阈值(一波量)或 supply 门。
- 中间插一波兵 + 试探前压(可用现有 push/probe act 或 build step 出兵到位)。
- **用 build_acceptance 实测调**(1 VeryEasy + 3 VeryHard),对比 prod_util / 早期兵数 / 不掉后期。
  指标退步就调 plan 别放宽 spec(memory feedback_recover_metric_dont_relax_spec)。

## 3. 验证
- 单测:StructureItem.addon_plan schema 回归;`_recommend_addon_mix` 纯逻辑(mock build 目标+已有挂件
  → 断言科技/双倍数);弹窗触发(addon_plan None + 产能建筑 → 出 ClarificationRequest 3 选项)。
- 真局自验:`scripts/proxy_chain_selftest` 同款注入式——注入"补4bb 2科技2双倍"/"补4bb"(验弹窗)/
  让 bot 自主造兵营(验建好即挂),grep 挂件 build 日志断言科技/双倍数对。
- voice_spot_check 加挂件分配 case(真 LLM 验解析正确率)。
- 3矿5bb:build_acceptance 实测。

## 5. opus 评审结论处理 → P1 定稿(2026-06-18,用户拍板"先做 P1 看效果")

评审把方案砍简单 + 揪出致命冲突。采纳:

- **M1 砍 `addon_plan` 字段(采纳)**:"补4bb,2科技2双倍" 用**组合**表达 = 三个现有 StructureItem:
  `Barracks(delta=4)` + `BarracksTechLab(delta=2)` + `BarracksReactor(delta=2)`。挂件项靠
  `_build_addon_on_parent` 自然分摊到空闲无挂件兵营。"不挂"=不发挂件项。**零 addon_plan 字段**。
- **唯一新增(极小)**:`StructureItem` 加 `addon_decided: bool = False` —— 区分"补4bb"(未决定→弹窗)
  vs "补4bb不挂"(已决定不挂→直接执行)。LLM 在玩家提到挂件(给 mix 或说"不挂")时置 True。
  弹窗 gate = VOICE 来源 + 产能建筑(Barracks/Factory/Starport)+ `addon_decided==False`。
- **M2/M4 致命冲突(采纳:P1 不做分支③)**:全局"建好即挂"循环会和 build 既有 `BuildAddon` step 抢
  同一空闲楼池(配比互搏/抢SCV/LIFT抽搐,且 step 不绑物理楼无法逐栋判定)→ **P1 不做分支③**。
  用户主诉求"bot 造好不挂"的根因(plan step 被枪兵饿死 + #542 不同步)→ **P2 单独修既有 step 可靠性**。
- **Q2 弹窗零改复用(采纳)**:`ClarificationOption.label` 普通字段(townhall 已现算现填),每 option 带
  不同 directive 批,cancel=空批。仿 `_maybe_build_townhall_confirm` 写 `_maybe_build_addon_confirm`,
  挂在 `_resolve_structure_delta` **之前**。注意:① `_pending_clarification` 单槽 → 和 townhall 互斥
  (同帧都命中定优先级);② 只对 `issued_by==VOICE` 的产能项弹,`_emit_addon_build`(auto)绝不弹。
- **Q3 推荐算法改(采纳)**:别按兵种数量比例。
  `techlab_need = (该楼生产、requires_techlab 的**不同兵种数**) + (1 if 该楼 techlab 有待研究升级 else 0)`,
  clamp `[≥1 if 有任何需求 else 0, count]`;`reactor = count - techlab_need`;有 mass-mineral 兵
  (枪兵/火蝙/医疗船/维京)且还有余量 → `reactor ≥ 1`。**减去已有挂件**(增量)。techlab 侧用现成
  `_unit_requires_techlab`(数据源 TRAIN_INFO),只新建 reactor 侧 `_ADDON_BY_UNIT` 表 + 单测。
- **Q7 分期(采纳)**:**P1 = 分支①(组合)+ 分支②(弹窗)+ 推荐算法**,作用于玩家额外加的产能楼,
  与 plan step 零冲突。**P2** = 修 bot 自主建好即挂可靠性(单独)。**3矿5bb 调序拆独立 task**。
- **Q8 跨族(采纳)**:人族专属。弹窗 gate 走 Barracks/Factory/Starport(人族专属 UnitTypeId)天然成立,
  Director 执行补一条 `race != Terran → 忽略 + warn` 兜底。挂件词表放 rules.md 人族段。

### P1 实现清单
1. `directives/models.py::StructureItem` 加 `addon_decided: bool = False`(+ 字段说明)。
2. `bot/director.py`:`_maybe_build_addon_confirm`(仿 townhall,VOICE 产能项 + addon_decided=False →
   3 选项 ClarificationRequest:不挂/推荐(现算 label)/取消)+ `_recommend_addon_mix(type,count)`(上述算法)。
   挂在 submit 流 `_resolve_structure_delta` 之前;`_pending_clarification` 与 townhall 单槽互斥处理。
3. LLM:`docs/llm_prompt/rules.md`(挂件词表:科技/双倍/不挂 + addon_decided 规则,人族段)+ `few_shot.md`
   (4 例:mix/部分挂/不挂/没说)+ `scripts/dump_llm_prompt.py` 重 dump。
4. 单测:StructureItem.addon_decided schema 回归;`_recommend_addon_mix` 三组(纯枪兵+stim=1科技其余双倍 /
   bio 4bb≥2科技 / 单factory坦克=1科技);弹窗触发(VOICE 产能 + addon_decided=False → 3 选项)+
   `submit_clarification_choice` 三 index 落地。
5. 自验:proxy_chain 同款注入"补4bb,2科技2双倍"(验组合落地 techlab2/reactor2)+ "补4bb"(验弹窗)+
   `voice_spot_check` 加挂件 mix case(真 LLM 解析正确率)。

## 4. 待评审重点(给 opus 评审 agent)
1. **schema 选型**:addon_plan 放 StructureItem 合理吗?还是该独立 directive?和 target_count/delta 怎么
   共存(delta=4 + addon_plan 和 ≤4 的一致性校验)?
2. **弹窗复用**:townhall ClarificationRequest 机制能否承载"option label 现算现填(推荐 N 科技 M 双倍)"
   + 每 option 带不同 addon_plan 的 directive 批?有没有坑(label 写死 vs 动态)?
3. **_recommend_addon_mix 算法**:需求信号取 build 目标兵种是否够?"保底1科技 + 比例分配"会不会在
   纯枪兵 build 上多挂科技、或在坦克 build 上少挂科技?给更稳的分配规则。
4. **建好即挂优先级**:给挂件高优先级会不会饿死该出的兵 / 该开的矿(本来枪兵优先是有意的)?平衡点?
5. **autonomous vs build plan 既有 addon step 冲突**:很多 build 已有 `BuildAddon` step。2.5 的默认循环
   会不会和既有 step **双挂/抢**?要不要"build 已显式管这栋的挂件就不插手"。
6. **opt-out 机制** + 哪些 build 该 opt-out(纯 rush?)。
7. YAGNI:3矿5bb 调序要不要这次一起做,还是单独 task。
8. 跨族:本期只人族;schema/弹窗是否要预留虫族(虫族无挂件)/神族(无挂件)——大概率人族专属,确认。
