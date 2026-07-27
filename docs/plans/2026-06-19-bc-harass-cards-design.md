# BC 骚扰重构：每艘新大件自动骚扰卡 + 贴边修复 + 脱离全军单退（设计）

> 2026-06-19。源自用户反馈：BC 骚扰（a）没贴边到矿区、跑去敌基地旁跟敌军来回拉扯打不到农民；
> （b）后期掉点血就一直后退、退太多、不和枪兵大部队抱团。用户要的重构：从"blanket 强控所有 BC"
> 改成 **per-BC 骚扰指令卡** + 一条**持续指令自动给每艘新 BC 建卡**。

---

## Goal（一句话）

把 BC 骚扰从"`BcRaidSquadAct` 默认强控**所有** BC"重构成"**只有持有骚扰卡的 BC** 才被骚扰微操控制，
未持卡的 BC 落到主力大军一起打"，并新增（1）一条持续指令自动给每艘新 BC 建骚扰卡、（2）语音
"派 N 个大件去骚扰[主矿/二矿/三矿]"建卡，顺带修掉贴边/单退两个 bug。

## 用户锁定的决策（AskUserQuestion 2026-06-19）

- **Q1 自动骚扰范围**：新建**一条持续指令**，给每一艘新出的 BC 创建一张骚扰卡。玩家 ❌ 这条持续
  指令 = 停止自动建卡。（原话还要求：每张 per-BC 卡能被**单独** ❌ 掉让那艘 BC 停止骚扰；语音也能建/释放。）
- **Q2 目标矿区**：**全动态轮换**（沿用现 `_update_raid_target` / `_pick_next_target`）。
- **Q3 删卡去向**：那艘 BC **归队主力大军**（release → free → PlanZoneAttack 听全军命令）。

---

## 现状（What is）

- `BcRaidSquadAct`（`terran/bc_raid_act.py`）：tactics 里的 blanket act，每帧把**所有** ready BC
  reserve 进骚扰队，飞敌矿、残血 Tactical Jump 回家、回血门满血再出、AoE 闪避、卡射程边缘绕圈。
  含**好用的微操引擎**（`_raid_move_point` 用 `plan_drop_path` 贴边、`_dodge_spot`、自适应
  `_jump_hp_threshold`、`_ensure_repair`）。玩家全军命令时 `_is_suppressed()` 整队让位。
- `execute_unit_action(verb="harass_workers", target)`（common_bot.py:696-699）：verb 非
  attack/attack_move → **只一次性 `unit.move(target)`**。**没有任何 BC 微操**。即现有 unit_claim
  harass 路径对 BC 只会"傻飞过去"。
- 持续征兵：`UnitClaimPayload.recruit_new`（models.py:450）+ `_tick_recruit_watchers`
  （director.py:7827）—— persistent claim 每 tick 把新出现的 selector 单位并入 standing order、
  对新单位 `execute_unit_action(verb)`。**已存在、已测**。
- standing order 生命周期：`standing_orders` + `_standing_order_tags`；`revoke_standing_order`
  （4310）→ `_release_standing_order_units`（4083）按 WP-C 恢复被抢占原主 + `release_unit_role`。
- 敌矿排序：`zone_manager.expansion_zones` 末尾=敌主、`enemy_main_zone`、`enemy_natural`；
  `zone.mineral_line_center`（农民工作线，**骚扰要打这里**）、`center_location`、
  `behind_mineral_position_center`（矿框后，防守位）。

### 两个 bug 的根因

1. **贴边没到矿区 / 跟敌军拉扯**：① 目标用 `behind_mineral_position_center`（矿框后、靠基地侧）
   → 实际飞到基地旁；② `_update_raid_target` 里 `enemy_army_near`（目标 10 格内 ≥2 非工兵）**优先**
   触发换矿 → 敌军一压上就跳去另一个矿 → 来回拉扯、从不在一个矿线打满农民；③ `plan_drop_path` 取
   `wps[1]`（下一个 waypoint），到 `_ENGAGE_RADIUS=9` 内才转绕圈，但绕圈中心是矿框后点 → 绕到基地侧。
2. **后期单退太多、不抱团**：blanket act 强控**所有** BC，连后期该和大军一起打的 BC 也被拉去单独
   骚扰 + 残血 Jump 回家 + 回血 hold → 表现为"掉点血就单独后退、不和枪兵抱团"。

---

## 架构（What will be）

### 控制权模型：从 blanket → per-BC 卡驱动

```
              ┌─────────────────────────────────────────────┐
              │  BC 自动骚扰工厂（1 条持续指令, bot 自动提交） │   玩家 ❌ → 停止建新卡
              │  bc_auto_harass (DirectiveType 新增, 见下)     │
              └───────────────┬─────────────────────────────┘
                              │ 每 tick 检测"新出且尚无骚扰卡"的 BC
                              ▼  为每艘新 BC 提交一张
              ┌─────────────────────────────────────────────┐
              │  per-BC 骚扰卡 (unit_claim, 1 BC, persistent) │   玩家 ❌ 单张 → 该 BC 归队主力
              │  selector=tag, verb=harass_workers, target=Z │
              └───────────────┬─────────────────────────────┘
                              │ director reserve 该 BC + 发布 harass-claim map
                              ▼
              ┌─────────────────────────────────────────────┐
              │  BcRaidSquadAct（微操执行器, 重构后）          │
              │  只对 harass-claim map 里的 BC 跑微操,         │
              │  按每艘的 target_zone 贴边到矿线打农民、        │
              │  残血 Jump、闪避、回血、绕圈。                  │
              └─────────────────────────────────────────────┘

   未持卡的 BC  ─────────────────────────────────────────────►  主力大军 PlanZoneAttack（和枪兵抱团）
```

**核心改动**：`BcRaidSquadAct` 不再 `cache.own(BATTLECRUISER)` 抓所有 BC，改成读
`knowledge.vibecraft.bc_harass_claims`（director 每 tick 发布的 `{bc_tag: target_zone|None}`），
只对其中的 BC 跑微操。未在 map 里的 BC 完全不碰 → 自然落到 sharpy free_units → PlanZoneAttack。

### 为什么不把微操塞进 `execute_unit_action(harass_workers)`

micro 是**每帧连续行为**（贴边寻路 + 闪避 + 自适应跳 + 绕圈 + 回血），`execute_unit_action` 是
**一次性下令**。把连续微操塞进一次性下令要么每 tick 重算（漂移、违反"目标点一次锁定"强规则），要么
在 facade 层重建一套帧循环 = 重复造 `BcRaidSquadAct`。**复用现成微操引擎**（只换"控制哪些 BC + 各自
目标"）成本最低、风险最小。harass claim 对 BC 的 `execute_unit_action` 一次性 move 无害（下一帧被
act 的 move 覆盖），但为干净起见：BC + verb=harass_workers 时 `execute_unit_action` **直接 return**
（让 act 全权驱动，标 `# vibecraft: BC harass 由 BcRaidSquadAct 接管`）。

### director 发布 harass-claim map（新数据流）

每 tick（在 standing order tick 附近）director 扫 `standing_orders` + 一次性 claims，挑出
`verb == harass_workers` 且 selector 命中 BC 的，解析每张卡的 target（main/natural/third/None）成
zone，写入 `knowledge.vibecraft.bc_harass_claims: dict[int, str | None]`（tag → "main"/"natural"/
"third"/None=dynamic）。`BcRaidSquadAct` 只读这个 map。卡撤销/ BC 死亡 → 该 tag 自动从 map 消失
（map 每 tick 重建，不残留）。

### 自动骚扰工厂指令：新 DirectiveType `bc_auto_harass`

- **为何不直接用 `recruit_new` 单条 standing order**：`recruit_new` 把所有新 BC 并入**同一条**
  standing order → 玩家只能 ❌ 整条（全释放），拿不到**per-BC 可单独 ❌ 的卡**。用户明确要"每艘
  一张卡、随手单独擦掉"。所以工厂指令的职责是**为每艘新 BC 提交一张独立的 unit_claim 卡**。
- **payload**：`BcAutoHarassPayload`（persistent 隐含；可选 `target` 默认 None=动态）。
- **执行**：director 每 tick（`_tick_bc_auto_harass`）：`current = BC tags`；`carded = 已有骚扰卡
  覆盖的 BC tags`；`new = current - carded - 本工厂已建过`；对每个 new BC **提交一张** unit_claim
  （selector=该 tag、task.primary_action.verb=harass_workers、target=工厂的 target、persistent=True）。
  ❌ 工厂 → 停 tick（不再建新卡；已建的 per-BC 卡保留）。
- **谁提交工厂指令**：bc_rush 开局时由 bot 自动提交一条 `bc_auto_harass`（bot_internal source），
  替代当前"BcRaidSquadAct 默认 ON"。UI 显示这条持续指令（玩家可 ❌）。

> ⚠ **评审重点（YAGNI）**：工厂"为每艘新 BC 提交独立子卡"是本设计**唯一的新机制**（现有
> recruit_new 是"并入单条"）。请评审判断：per-BC 独立卡的收益（单独 ❌ + UI 可见）是否值得这点新
> 机制；若评审认为 recruit_new 单条 + "从编队移除单个 BC" 已够，则降级。**默认按用户原话实现 per-BC 卡。**

### 微操 bug 修复（在重构后的 `BcRaidSquadAct` 内）

1. **打农民工作线**：骚扰目标从 `behind_mineral_position_center` 改 **`mineral_line_center`**
   （农民采矿线），绕圈中心也用它 → BC 卡在农民头上，不飘去基地侧。
2. **不再一压上就乱跳换矿**：`enemy_army_near` 不再触发**换矿**。敌军压上的正确反应是
   **贴边游走 + 血低 Jump 回家**（已有），不是跳去另一个矿。换矿只保留：① 该矿农民清零
   （`workers_cleared`）② 停留超时（`dwell_timeout`，给个更长值）。这样 BC 在一条矿线咬住农民。
3. **target_zone 指定时不轮换**：per-BC 卡若指定了 main/natural/third → 该 BC 固定打那条矿线
   （农民清零/超时也不换，除非该矿没了）；target=None（动态）才走原轮换。
4. **贴边寻路保留 `plan_drop_path`**：远 → 取避敌 waypoint；`_ENGAGE_RADIUS` 内 → 绕 mineral_line_center。
   （Jump 阈值/闪避/回血/repair 维持现状，仅 #557 已调好的参数不动。）

### target spec：主矿/二矿/三矿

- 新 named_spot：`enemy_main` / `enemy_natural` / `enemy_third`（解析到对应 zone 的
  `mineral_line_center`，**确定性**：用 zone_manager 有序 expansion_zones 索引，非 `.first`）。
  enemy_main 可能已有，补 natural/third。
- 卡的 target = `named_spot` 之一 → director 发布 map 时记 "main"/"natural"/"third"；
  无 target → None（动态）。

### 生命周期

| 事件 | 行为 |
|---|---|
| bc_rush 开局 | bot 自动提交 1 条 `bc_auto_harass`（target=None 动态） |
| 新 BC 产出 | 工厂 tick 为它提交 1 张 per-BC 骚扰卡 → director reserve → act 微操 |
| 玩家 ❌ 工厂指令 | 停止建新卡；已有 per-BC 卡 + 其 BC 继续骚扰 |
| 玩家 ❌ 某张 per-BC 卡 | `revoke_standing_order` → release 该 BC → free → **归队主力大军**（Q3） |
| 玩家语音"派 2 个大件骚扰二矿" | 提交 2 张 per-BC 卡（selector=2 BC，target=enemy_natural） |
| 玩家全军命令（撤退/进攻/防守） | 沿用 `_is_suppressed()`：残血 BC 仍停可修锚点 + 修，满血交还听全军（#559 行为不变） |
| BC 死亡 | `_tick_bc_auto_harass` 检出其 tag 不在 `resolve_selector(BC)` → `revoke_directive(该 per-BC 卡)` + 从"已建过"集合移除（清指令/UI，不只是停微操）；map 下一帧重建自动剔除该 tag |

### LLM / 语音

- 复用 verb `harass_workers` + `unit_claim`（persistent）。few_shot 加：
  - "派一个大件去骚扰对方农民" → `unit_claim(BattleCruiser, 1, harass_workers, target=enemy_main?)`
    —— 无矿区 → 默认动态（target=None）或问？按 B 类 verb 规则"必填 unit_count_hint"，数量"一个"已给。
    矿区未给 → target=None 动态（不强制 ambiguous，矿区可选）。
  - "派两个大件去骚扰他二矿" → `unit_claim(BattleCruiser, 2, harass_workers, target=enemy_natural)`。
  - 工厂指令是 bot 自动提交、**不走 LLM**（bot_internal）。
- 重 dump prompt。

---

## 测试

- **单测**：
  - `bc_auto_harass` payload schema + DirectiveType（5 处同步：enum/Payload/union/_apply_to_facade/prompt）。
  - 工厂 tick：mock director，新 BC → 提交 per-BC 卡；❌ 工厂 → 不再建；per-BC 卡覆盖的 BC 不重复建。
  - director 发布 `bc_harass_claims` map：claim 命中 BC → map 有该 tag + target_zone；撤销/死亡 → 消失。
  - `BcRaidSquadAct` 只控 map 内 BC（map 空 → 不碰任何 BC）。
  - enemy_main/natural/third named_spot 解析确定性。
  - 重构后 plan 构造 + placeholder 审计（已有）。
- **真局自验**（`scripts/bc_harass_selftest.py` 新增，non-realtime + mock LLM）：
  - debug 生敌方多矿 + 农民 + 我方 BC；注入工厂指令 → 验每艘 BC 拿到卡、贴边到 mineral_line_center
    （BCRAIDTRACE flyout/target）、打到农民（enemy worker 掉血/数量降）。
  - 注入"派 1 个大件骚扰二矿" → 验该 BC 去 enemy_natural 矿线。
  - ❌ per-BC 卡 → 验该 BC role 从 Reserved → free（不再被 act 控）。
  - 终态铁律：验**敌农民数量下降**（外部终态），不只看 trace。
- **override_acceptance**：bc_rush 全军撤退/进攻时 carded BC 行为（#559 不回归）。

## 文档同步

ARCHITECTURE.md（新 DirectiveType + 数据流 bc_harass_claims）、USER_GUIDE.md（语音"派大件骚扰X矿" +
"❌ 自动骚扰"话术）、README.md（能力清单）、CHANGELOG/TASKS、docs/pitfalls.md（如踩坑）。

## 评审处理结论（独立 opus 评审 2026-06-19，逐条）

评审核实了 4 个最危险假设**全部成立**：①reserve 不帧间抖（`_refresh_llm_controlled_roles` 在
ActManager 前每帧 re-Reserve，持卡 BC 在 act 跑前已 Reserved，PlanZoneAttack free_units 拿不到它，
唯一态切换是"出兵→发卡"一次性 transition）②bot 内部卡不被跨族校验/弹窗拦（unit_claim 走
`is_selector_check` 命中己方 BC 放行；确认弹窗只对 StructureOverride/BuildAt+VOICE）③`release_unit_role`
真机 `_SharpyFacadeBase` 有（common_bot.py:603）④`mineral_line_center` 基于原始矿点算一次、不随采矿
漂移（满足目标点锁定强规则）。**架构通过，按下列 3 处改后实现**：

- **[采纳·中] 死亡孤儿清理（问题1）**：per-BC 卡 selector=固定 tag，BC 死后该卡永不再解析、不自清
  → 僵尸卡 + `_standing_order_tags` 泄漏。"map 每 tick 重建自动剔除"只停了**微操**，没清**指令/UI**。
  → `_tick_bc_auto_harass` 每 tick 额外扫自己建过的 per-BC 卡，BC tag 不在 `resolve_selector(BC)`
  里 → `revoke_directive(did)` + 从"已建过"集合移除。（已写进下方生命周期表 + 数据流。）
- **[采纳·中] act 状态 per-tag 迁移（问题2，本次真正工作量）**：现 `BcRaidSquadAct` 是**单目标**
  状态机（`_raid_target`/`_raid_dwell_start`/`_raid_switch_cooldown_until`/`_last_flyout_pos_key`
  全单值）。per-BC 卡后不同 BC 打不同矿 → 这些必须改 **per-tag dict**（`_raid_target_by_tag` 等），
  每 tag 第一次锁目标、满足条件才换（沿用现逻辑）；死 tag 每 tick 按 `live_tags` 过滤（照抄现有
  `_healing_tags`/`_last_hp` 清理）。**`_home_anchor` 保持全局单值**（回家点不分 BC）。
- **[采纳·中] 矿点解析源钉死一个（问题3）**：现有三套来源不一致——named_spot 的 `enemy_main`=
  `start_locations[0]`（基地点）、`enemy_natural/third`=`center_location`；act 现用
  `behind_mineral_position_center`。**统一钉死**：act 把 map 里的字符串 main/natural/third 映射到
  **`zone_manager.enemy_expansion_zones[0/1/2].mineral_line_center`**（有序索引、确定性）。map 里
  **只存字符串**，微操坐标一律 act 这一处解析（named_spot 的 Point2 链路不参与微操坐标）。
  **fallback**：指定 third 但该 zone 未知/不足 → 退 main；main 也无 → dynamic；都无 → 该帧不动。
- **[采纳·低] 指定矿农民清零（问题4）**：指定矿 workers 清零 → **自动转 dynamic** 找下一个有农民
  的敌矿（不空转绕空矿线）；该矿重新有农民不强制回去。比"空转/回家"强。
- **[采纳·低] map 发布走 knowledge.vibecraft 直写（问题5）**：director 直接写
  `bot.knowledge.vibecraft.bc_harass_claims`（**不**新增 facade setter，省双实现负担，同
  `combat_intent_override` 先例）；act 读侧 `getattr(getattr(knowledge,"vibecraft",None),
  "bc_harass_claims",{})` 兜底。一帧延迟无害（目标本就锁定）。单测 FakeFacade 场景可注入该 map。
- **[采纳·低] 归队断言（问题6）**：override_acceptance #559 不回归 case 顺带断言"❌ 卡后该 BC tag
  进入主力 attack 路径"（确认 BC 空军被 PlanZoneAttack 带飞抱团，防 attack 目标挑食）。
- **不动 PlanZoneAttack（确认）**：非持卡 BC 自然落 free_units，不加 vendor patch。
- **开放问题回答**：#1 factory 层保留（两种 ❌ 语义 recruit_new 表达不了，非过度设计）；#2 开局提交
  工厂、卡在 BC 真出现时发；#3 一帧延迟无害不追求同帧；#4 矿区选填默认 dynamic、不强制 ambiguous。

## 开放问题（给评审）

1. **YAGNI：工厂"提交 per-BC 独立子卡" vs `recruit_new` 单条 standing order**（见上 ⚠）。
2. **工厂提交时机**：bc_rush 开局即提交，还是首艘 BC 出现时？（倾向开局即提交，UI 早可见可 ❌。）
3. **bc_harass_claims map 发布位置**：复用哪个 director tick 钩子最稳（避免与 standing order
   reserve 的顺序竞争）。
4. **语音"骚扰"无矿区是否 ambiguous**：倾向矿区可选（默认动态），不强制追问。请确认与现有 B 类
   verb"必填 unit_count_hint"规则不冲突（数量仍必填，矿区选填）。
