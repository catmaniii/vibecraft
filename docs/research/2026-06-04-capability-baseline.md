# VibeCraft 当前能力基准（2026-06-04）

> 由能力盘点 agent 扫码生成，作为产品批判性审视的"我们已支持什么"权威基准。
> 只记事实 + file:line，不含评价。缺口分析见同目录 gap-analysis 报告。

## 1. 指令类型全集（DirectiveType，src/vibecraft/directives/types.py，17 种）

| 类型 | 玩家能做什么 |
|---|---|
| STRATEGY_SET | 切到某阶段剧本（opening/midgame/lategame） |
| STRATEGY_CANCEL | 取消剧本，bot 降级待命/经济 |
| PRODUCTION_OVERRIDE | 强制出兵：指定兵种数量 |
| TECH_OVERRIDE | 优先研究某科技/升级 |
| EXPANSION_OVERRIDE | 设分基目标数 |
| STRUCTURE_OVERRIDE | 指定建筑数量 + 位置提示（可多建筑） |
| ENGAGEMENT_CONSTRAINT | 全局交战策略（弃用→TACTICAL_OBJECTIVE） |
| TACTICAL_OBJECTIVE | 中粒度战术：全军进攻/撤退/防守/侦察/骚扰（可 persistent） |
| UNIT_CLAIM | 占住单位执行 Task（暂时/持久） |
| SCOUT | 派侦察单位去某点 |
| MOVE | 单位移动（可 safe 避敌） |
| BUILD_AT | 指定位置建造（可代理农民 by_probe） |
| UNIT_RELEASE | 归还占用单位 |
| DROP_ACT | 复合空投（兵种+运输+投放点+下船行为） |
| VIEW_FOLLOW | 镜头跟随单位/部队/小队/任务 |
| PRODUCTION_BLOCK | 暂停造某兵（持久） |
| GROUP_ASSIGN / GROUP_CLEAR | 语音编队 1-N（可配置上限）/ 解散 |

## 2. 单位动作 verb（Task.Verb，task.py，15 种）

静止/移动：HOLD_POSITION / GUARD_POSITION / MOVE_TO / PATROL / FOLLOW / RETREAT / STANDBY（到点驻守+受敌自动战斗+超距返回）
战斗：ATTACK_MOVE / FOCUS_FIRE / KITE / HARASS_WORKERS / LIFT_TARGET
技能：CAST_ABILITY（Psi Storm / Force Field 等）
工人/建筑：GATHER / BUILD / CANCEL

## 3. 战术 verb（TacticalVerb，models.py L239-263，13 种）

A 类（全军级，持续到玩家点×）：attack / defend / retreat / vision / hold
B 类（小队级，必带 done_when）：scout（纯视野）/ recon（火力侦查 3-8 单位）/ harass（骚扰经济）
MVP on_hold：expand / drop / raze / regroup / split

## 4. 选择器 Selector（scope.py，12 字段）—— 玩家如何"指代单位"

tag（单个）/ tags（一组）/ unit_type（兵种）/ role（系统级）/ count（限 N 个）/ claimed（系统冲突检测）/
near_point+near_radius（"这里附近"）/ primary_verb_prefix（任务前缀）/ assigned_spot（"守瞭望塔的那个"）/
group_id（"1 队"）/ chain_id（同单位接力）

## 5. 掌控/覆盖机制（重点）

**全军意图覆盖**（写 knowledge.vibecraft）：combat_intent_override（attack/defend/hold/retreat/vision）/
attack_mode_override（all_in/probe）/ attack_target_override（点）/ stance_override（兼容旧接口）
**sharpy vendor hook**：PlanZoneAttack（_should_attack/_should_retreat/_stop_retreat/_get_target/execute）/
PlanZoneGather / PlanFinishEnemy / MicroHighTemplars（ht_safe_micro）—— 玩家意图直接压制 sharpy 决策树
**撤销/终止**：玩家点×卡片 revoke / strategy_cancel（"停下"）/ group_clear（"解散N队"）/ unit_release
**确认/澄清**：pending_clarification / ClarificationRequest（"我理解为 A/B/C？"）/ confidence 阈值（低于则等确认）/
pending_force_strategy（硬转剧本等玩家确认）

## 6. 镜头/视野

VIEW_FOLLOW.target_kind：unit / army（全军质心）/ squad（侦查骚扰小队）/ task（执行某持久任务的单位）
camera-as-target：TargetKind.CAMERA（"这里/这边"=说话那刻镜头中心）
小地图拖拽：view_move WS frame → move_camera（不走 directive）
前瞻偏移三规则（telemetry.py compute_follow_focus）：移动看前方 7 格 / 停止看自身 / 停止交战看双方交战团重心

## 7. 编队

GROUP_ASSIGN/CLEAR 1-N（默认 5，可配上限 9，DEFAULT_MAX_VOICE_GROUPS 单点）
编队指挥："N 队进攻/火力侦查/待命/撤退" → unit_claim(selector.group_id=N) + verb(attack_move/standby)
web VoiceGroupBar 显示每队兵种构成

## 8. 复合/高级玩法

DROP_ACT：simple / warp_then_drop（棱镜二段投），下船行为 attack_workers/attack_production/retreat/siege
代理建造：BUILD_AT.by_probe（派最近农民去某点造）
巡逻：PATROL + waypoints 两点循环
连续指令链：chain_id（"派农民造 BG 然后回来"，同农民接力）
激活门：activate_when（DoneWhen，"1 攻好了再进攻"，等条件满足才激活，顺序编排）
持久指令：ScopeKind.PERSISTENT（standing order，玩家点×才释放）

## 9. 宏观/经济/科技

L1 剧本库 StrategyLibrary：OpeningBuild（supply-keyed）/ MidgameStance（科技+timing+扩张）/
LategameDoctrine（兵种组合+条令）/ PersistentDoctrine（不自动切）
Override：PRODUCTION_OVERRIDE（多兵种一条）/ TECH_OVERRIDE / EXPANSION_OVERRIDE /
STRUCTURE_OVERRIDE（target_count vs delta，多建筑一条）/ PRODUCTION_BLOCK（机制级拦截 ActUnit/WarpUnit）

## 10. 反馈/可观测（玩家能看到 bot 在干嘛）

Command Cards 4 层（L1 剧本 / L2 全军战术 / L3 override / L4 单位任务）+ 状态机
（等待生效 / 执行中 / 已完成 / 已终止 / 已手动取消 / 识别失败）
Snapshot（~2s）：supply/workers/army/资源/bases/army_center/units/buildings/key_units/economy/enemy/tactical(intent/stance/mode/plan_status)
历史指令三层：原话 → 识别解读 → 卡片+状态
Board Events：submitted/committed/revoked/released/rejected/superseded + strategy.changed/transitioned

## 总体形状

四大类覆盖：**宏观剧本层（L1）**剧本切换 + override 微调；**战术覆盖层（L2）**全军意图经 vendor hook 压制
sharpy 决策树；**单位微操层（L3）**Selector 占单位执行 verb，支持持久/链式/反应；**系统联动层（L4）**
空投/代理建造/产能封锁/编队/镜头跟随。所有指令统一为 DirectiveBoard 的 Directive 对象，经
pending→committed→active/done 生命周期，由 Sc2Facade 影响 bot。LLM 解析 + 1.5s 撤销窗 + 卡片状态机 + snapshot 透明度。
