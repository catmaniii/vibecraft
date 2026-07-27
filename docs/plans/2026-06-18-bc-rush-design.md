# 人族速大舰快攻(二本速战巡)build + BC 骚扰小队设计（#549）— 定稿 v2

> 用户要的人族速大舰快攻。核心:战巡**飞**去对方矿区贴边骚扰打农民、残血 Tactical Jump 回家、
> SCV 自动修、修满血**立马再飞出去**(不等 jump CD)、无限循环**直到玩家喊"回归大部队"**。
> v2(2026-06-18 opus 评审 + 用户拍板):② 改 **reserve 独立小队**模型(非全局 attack_target_override)。

## 一、架构定稿：reserve 独立骚扰小队（仿 PhoenixSquadAct）
评审否决了"全局 attack_target_override 复用 PlanZoneAttack"(会把全军拽去敌矿、抢玩家指目标字段、
被 deathball 门卡起步、被撤退逻辑弹回横跳)。改用现成的 **`PhoenixSquadAct` reserve 小队范式**:
BC 编成独立骚扰小队、Act 自己全权驱动,主力(枪兵/旋风)留家防守互不干扰。**关键好处:无需 patch
sharpy**——骚扰 BC 被 reserve 后脱离 combat,jump 逃生/回血门全在 raid Act 里自己做(评审实测
`Repair` 不看 UnitTask,reserved BC 照样被修,所以 reserve 不影响 ④)。

**新写 3 块**:`bc_rush` build(plans+yaml)、`BcRaidSquadAct`(terran/bc_raid_act.py)、phoenix 式喊停卡。
**不碰**:sharpy vendor(不 patch MicroBattleCruisers——它只管非骚扰的 BC)、facade、bio_max/其他 build。

## 二、build：二本速战巡 opening（`terran/plans/bc_rush.py` + `strategies/terran/bc_rush.yaml`）
LotV 调研定稿(Liquipedia+spawningtool),二本先扩再过科技,**首舰 4:10-4:40**:
- 链:depot→rax→gas1(~0:43)→ orbital + reaper(~1:27 侦察)→ **CC2(~supply20,1:42-1:55)**→ Factory(~2:00)
  → gas2(~2:10)→ Starport(~2:50)→ **Fusion Core(~3:25,星港存在即可下)** + Starport 挂 TechLab
  → **首舰 ~4:10-4:40** → 持续出 BC。BC 双前置已核实:`TRAIN_INFO[STARPORT][BATTLECRUISER]` =
  requires_techlab + required_building FUSIONCORE(两个都要)。
- gas 瓶颈:gas1 ~0:43 / gas2 ~2:10 / CC2 上 gas3-4(~4:40) / 后续 5-6。SCV ramp 双矿饱和 ~40-44。
- **不研 Yamato**(用户):普攻打农民够。不研 → MicroBattleCruisers 也不放亚马托。Tactical Jump **自带不用研**。
- 早期防守(TvP):兵营反应堆 + 双枪兵(防 adept/追猎)、Cyclone 旋风(防神谕者/临时对空)、视侦察补地堡。撑到首舰。
- kind: **opening_build**;lategame_transitions 接 bc_late/persistent_skyterran(后期再研亚马托/攻防)。

## 三、`BcRaidSquadAct`（核心，仿 `protoss/plans/phoenix_squad_act.py`）
`ActBase`,execute 永远 return True,放 tactics SequentialList 的 PlanZoneAttack **前**。状态/缓存挂 self。

### 激活 / 喊停（phoenix 卡机制，默认 ON）
- 默认:有 BC + `_raid_active=True`(出 BC 就骚扰)。维护 phoenix 式标志 + `notify_*` 让 Director 建
  **玩家可见可×的持久指令卡**(直接复刻 phoenix_squad_act 的 `_harass_active`/`notify_phoenix_*`/
  Director 卡片那套,改名 bc_raid)。
- 玩家喊"停止骚扰/别骚扰了/回归大部队"(或 × 卡片)→ `_raid_active=False` → release 全部 BC 归队
  (回主力,跟 PlanZoneGather/PlanZoneAttack)。"继续骚扰/去骚扰"→ 重新 ON。
- 玩家显式 combat intent(全军进攻/撤退)→ 让位:停 raid + release(phoenix 同款,_harass_active False 即 release)。

### 每帧驱动（reserve 小队）
1. **选/维护骚扰 BC**:所有 BC 中,**非"回血中"**的 reserve 进骚扰队(每帧重设 `roles.set_task(Reserved)`,
   照 phoenix——role 每帧重置)。回血中的 BC 单独管(见下)。
2. **飞去敌矿区横跳**:当前袭扰目标 = 敌方**主矿 or 分矿**矿区点(`zone.behind_mineral_position_center`/
   `center_location`,确定性)。**目标一次锁定**(CLAUDE.md 强规则),缓存在 `self._raid_target`;满足
   切换条件才换:① 该矿农民清零(`enemy_workers near ==0`) 或 ② 停留超 `raid_dwell_s`(~25s)。切换后
   **强制最短停留 cooldown**(防两矿反复横跳抽搐)。骚扰 BC `attack`/`move` 向 `_raid_target`,打农民/建筑。
3. **残血 jump 回家**(每 BC,自己做,不靠 micro)——**自适应阈值**(用户 2026-06-18 拍板):
   `jump_hp = clamp(incoming_air_dps × safety_s,  floor=9%×550≈50,  cap=550)`;`bc.health <= jump_hp`
   且 `cd_manager.is_ready(tag, EFFECT_TACTICALJUMP)` → 跳。**9% 保底必跳**(BC 血厚,没人/一个打时
   耗到 9%);**火力越猛(incoming DPS 越高)阈值越高、提前跳**(撑不过 safety~4s 就别恋战)。
   incoming = 当前能打到这艘 BC 的敌方对空单位 DPS 之和(`target_in_range` + `can_attack_air`,含静态防空)。→ 直接
   `bc(EFFECT_TACTICALJUMP, home)`(home=我方主基 `behind_mineral_position_center`,安全锚点,缓存锁定)。
   标记该 BC 进"回血中"。
4. **回血门**(用户:修满就出,**不等 jump CD**):回血中的 BC **hold 在 home 锚点**(reserve + move 到锚点,
   不送出去),直到 `health_percentage >= ~0.95` → 移出"回血中"、下帧重新进骚扰队飞出去。**只 gate 血量**,
   不 gate jump CD(BC 飞得慢、飞过去路上 CD 自然好,且飞到要先骚扰一会才掉血,时间够——用户)。
   **不依赖 SCV 在身边**做 hold 条件(解耦,评审:Repair 派单有时序,别耦合成竞态)。
5. **修理**:靠现成 sharpy `Repair`(已在 tactics;不看 role,reserved BC 照修)。家里安全时 `solve_scv_count`
   只派 1 SCV(550 血修得慢,cadence ~分钟级,可接受;评审已确认)。修好 SCV 由 DistributeWorkers 归队采矿。
6. **无限循环自然涌现**:飞出骚扰→残血 jump 回家→hold 回血→修满→飞出…直到玩家喊停。

## 四、③④ 复用确认
- ③ jump 逃生:reserve 模型下由 raid Act 自己调 `EFFECT_TACTICALJUMP`(阈值 ~40%,提前跳)。非骚扰的
  BC(防守/玩家接管)仍走现成 MicroBattleCruisers(不改它)。
- ④ Repair:现成 tactics `Repair()`(不看 role)。jump 落点/home hold 锚点 = 主基 `behind_mineral_position_center`
  (评审核实 expansion_zones[0]=主基,安全)。

## 五、玩家控制权 / 约定
- 骚扰 BC 走 **reserve(bot 自主 claim)**:全军命令(combat_intent_override / 全军 attack_target_override)
  只动 free_units → 不会误带骚扰 BC(符合控制权规则2)。玩家显式 intent → raid Act 让位 release(规则1/3)。
- **不 patch sharpy**(raid 逻辑全在 Act;jump/yamato 直接调 ability)、**不改 facade**、目标点/home 锚点锁定。
- 玩家 × 喊停卡 → release squad 归队(phoenix 现成交互一致)。

## 六、自验
- `build_acceptance bc_rush`(新 spec:首舰 timing ~4:20、Fusion Core、Starport TechLab、BC 数、二矿时机、
  早防守撑住 vs veryhard)。
- 真局 selftest(仿 proxy_chain/caster):起 bc_rush,greppable `BCRAIDTRACE`(flyout/target_switch/
  jump_home/healing_hold/regroup),断言:BC **飞**到敌矿(非 jump 进)、残血 jump 回家、SCV 修、修满再飞出、
  玩家喊停 release 归队。non-realtime + mock LLM + 真对手。截图/ trace 确认真 jump(不站撸)。

## 七、评审已采纳的修正(对照 v1)
1. ②: 全局 override → **reserve 独立小队**(头号改动,用户已拍板)。
2. Yamato UpgradeId = `BATTLECRUISERENABLESPECIALIZATIONS`(YAMATOCANNON 会 KeyError)——本 build 不研,
   别名已修对(0908a81)。
3. jump 阈值 → **自适应**(用户拍板):9% 保底必跳 + `incoming_air_dps × safety` 抬高(火力猛提前跳)。
   (评审建议的固定 ~40% 被用户进一步细化成按受击火力估算的公式。)
4. 回血门:只 gate 血量(≥0.95)、**不 gate jump CD**(用户)、**解耦 SCV 近邻**。
5. **不 patch sharpy**(reserve 模型让 raid Act 自含,MicroBattleCruisers 不动)——v1 的"enhance
   MicroBattleCruisers"作废,§三矛盾消除。
6. 喊停 = 复刻 PhoenixSquadAct 卡片机制(非新开 flag 框架)。
