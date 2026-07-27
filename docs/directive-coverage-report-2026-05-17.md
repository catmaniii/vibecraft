# 四层 Directive 全覆盖 e2e 测试报告

**日期**：2026-05-17
**测试驱动**：`scripts/e2e_4_directive_types.py`（14 case，默认 CheatMoney 难度，每 case 90s wall timeout，fast mode）
**LLM**：DeepSeek V4 Pro（与生产同配，max_validation_retries=1）
**Driver 总结**：14/14 PASS（按 driver 自带 verify 标准）
**真实结论**：**5/14 case 的执行层证实空响 / 不可观察**，根因集中在 L2 `tactical_objective` 缺执行器

---

## 0. TL;DR

| 维度 | 结果 |
|---|---|
| LLM 识别成功率 | 14/14（已 normalize 别名，含 `vibecraft-dev` 别名表 + system.yaml）|
| 进 Board（directive.committed）| 14/14 |
| **PWA UI 卡片真正显示数据** | **9/14** |
| **bot 真正执行（facade / bot.train / set_unit_role）** | **9/14（验证到 6/14，3 个路径有代码无 log 证据）** |
| **既 UI 空又 bot 不执行的死路** | **4/14（全部 LLM 解析为 `tactical_objective`）** |

**用户感知瓶颈完全验证**：
> 「LLM 识别成功了，但 PWA UI 上看不到这些指令形成"要优先执行的命令项"，bot 还是按自己的想法在指挥部队。」

→ 凡 LLM 解析成 `tactical_objective` 的指令（推/侦察/骚扰/侦察主基地），**snapshot.active_tactics 字段始终为空**（TacticsCard 不显示），**director 也无对应 `_exec_tactical_*` 方法**（bot 完全无视）。这是单一根因。

---

## 1. 四层证据矩阵

| Case | inject | LLM 解析 DirectiveType | Board committed | snapshot 字段 | PWA 卡片可显示 | bot 执行证据 | 真实判定 |
|---|---|---|---|---|---|---|---|
| **L1a** | 切叉球一波 | `strategy_set` (iac_2base) | ✓ +12.9s | `strategy.midgame` 填充 iac_2base | ✓ StrategyCard | ✓ `set_build switched to iac_2base` (stdout) | **真 PASS** |
| **L1b** | 取消所有剧本 | (无 directive 流水) | ✗ 无 board.submit | opening 从 1g_robo_immortal → None | ✓ StrategyCard | ✓ `set_build switched to sustain` (stdout, fallback plan) | **真 PASS**（特殊 dispatch_cancel 路径，不走 board）|
| **L2a** | 进攻对方自然 | `tactical_objective` verb=attack | ✓ +13s 后 立即 released | **✗ active_tactics 始终空** | **✗ TacticsCard 不显示** | **✗ 无 `_exec_tactical_*`** | **死路** |
| **L2b** | 看一眼对方主基地 | `tactical_objective` verb=scout/vision | ✓ +13s | **✗ active_tactics 始终空** | **✗ TacticsCard 不显示** | **✗ 无 `_exec_tactical_*`** | **死路** |
| **L2c** | 凤凰打死对方 5 个农民就回 | `tactical_objective` verb=harass | ✓ +13s | **✗ active_tactics 始终空** | **✗ TacticsCard 不显示** | **✗ 无 `_exec_tactical_*`** | **死路** |
| **L2d** | 守家别出门 | `engagement_constraint` stance=defend | ✓ +12.3s | (无字段透传) | (无对应卡片) | ✓ 代码路径走 `facade.set_engagement_stance` | **路径在但无 log + 无 UI**（弱 PASS）|
| **L2e** | 30 秒后撤 | `engagement_constraint` stance=retreat | ✓ +11.6s 后 released | (无字段透传) | (无对应卡片) | ✓ 代码路径走 `facade.set_engagement_stance` | **路径在但无 log + 无 UI**（弱 PASS）|
| **L3a** | 探机巡逻自然别动 | `unit_claim` persistent=true verb=patrol | ✓ +12.5s | `standing_orders` 填充 Probe/patrol | ✓ StandingOrdersCard | ✓ 代码路径 `_apply_unit_claim → set_unit_role` | **真 PASS** |
| **L3b** | 让那个探机移动到气矿 | `unit_claim` ephemeral verb=move_to | ✓ +12.5s | (in_flight 字段不透传) | (无对应卡片) | ✓ 代码路径 `_apply_unit_claim → execute_unit_action` | **路径在但无 UI**（弱 PASS）|
| **L3c** | 侦察一下对方主基地 | `tactical_objective` verb=scout | ✓ +13.5s | **✗ active_tactics 始终空** | **✗ TacticsCard 不显示** | **✗ 无 `_exec_tactical_*`** | **死路**（被 LLM 路由到 L2）|
| **L3d** | 所有人原地待命别动 | `engagement_constraint` stance=hold | ✓ +11.9s | (无字段透传) | (无对应卡片) | ✓ 代码路径 `facade.set_engagement_stance` | **路径在但无 log + 无 UI**（弱 PASS）|
| **L4a** | 下个 BG 出俩哨兵 | `production_override` Sentry ×2 | ✓ +12.5s | `production_overrides` status=`active` reason="已下单等完成" | ✓ ProductionOverridesCard | ✓ `production_override TRAIN UnitTypeId.SENTRY ×2` (stdout × 4) | **真 PASS** |
| **L4b** | 先研闪烁 | `tech_override` BlinkTech | ✓ +12.4s | `production_overrides` status=`on_hold` reason="资源/building 不足" | ✓ ProductionOverridesCard | ⚠ `BotAI.research(BLINKTECH) 不可用(sharpy 限制), 由 sharpy plan 自带 research 路径接管` | **真 PASS（开局资源/前置不够，状态正确）** |
| **L4c** | 马上去开三矿 | `expansion_override` target_count=3 | ✓ +12.2s | `production_overrides` status=`on_hold` reason="资源不足(120/400 矿)" | ✓ ProductionOverridesCard | ✓ prereq 阻塞，未到 expand_now（资源不够正常）| **真 PASS** |

---

## 2. 死路（4 case）执行链路逐层定位

| 阶段 | 是否走通 | 证据 |
|---|---|---|
| LLM 解析 | ✓ | `response_raw.directives[0].type = "tactical_objective"`，confidence ≥ 0.85 |
| `IntentParseResult` 落地 | ✓ | `commands.jsonl` 有 entry，`directives.jsonl` 有 submitted |
| `board.submit()` | ✓ | `directives.jsonl` event=submitted/committed |
| `_in_flight` 记账 | ✓ | EventBus emit `directive.committed` |
| `snapshot.active_tactics` 透传 | **✗** | 14 case 全部 `active_tactics: []`，从未非空 |
| `_apply_to_facade` 路由 | **✗** | `director.py:1533-1617` 无 `t == DirectiveType.TACTICAL_OBJECTIVE` 分支 |
| `_exec_tactical_*` 执行器 | **✗** | grep `_exec_tactical` 全仓 0 命中 |
| sharpy 接管 | **✗** | 无回退路径，bot 完全无视 |

**为什么 `active_tactics` 也空**：`build_snapshot()` 从 `_in_flight` 筛 `type == TACTICAL_OBJECTIVE` 时，commit 后 task_monitor 立刻判 done（done_when 初始就满足，或 timeout 太短），committed→released 在同一 frame 完成，snapshot 永远抓不到 in-flight 窗口。即使抓到，也只有 UI 显示无 bot 执行。

---

## 3. 真 PASS（9 case）的执行证据

| Case | stdout 证据 |
|---|---|
| L1a | `INFO:vibecraft.bot.auto_combat.protoss.bot:set_build switched to iac_2base` |
| L1b | `INFO:vibecraft.bot.auto_combat.protoss.bot:set_build switched to sustain`（cancel → fallback sustain plan）|
| L4a | `INFO:vibecraft.bot.director:production_override TRAIN UnitTypeId.SENTRY ×2 (count=2, done=0, in_flight=0, id=d_e112f8)` ×4 次 |
| L4b | `WARNING:vibecraft.bot.director:tech_override BotAI.research(UpgradeId.BLINKTECH) 不可用(sharpy 限制), 由 sharpy plan 自带 research 路径接管: You have used self.do(). This is no longer allowed in sharpy`（已知 sharpy 限制，fallback 到 on_hold 状态显示正确）|
| L4c | snapshot status=on_hold reason="资源不足(120/400 矿)"（prereq 阻塞在 mineral check，未到 expand_now）|
| L2d / L2e / L3d | `_apply_to_facade` 代码路径有 ENGAGEMENT_CONSTRAINT 分支调 `facade.set_engagement_stance(stance)`，但**该分支无 logger.info**，stdout 无可观察证据，需补 log 才能 verify |
| L3a / L3b | `_apply_unit_claim` 代码路径调 `set_unit_role + execute_unit_action`，**同样无 logger.info**，但 L3a 的 snapshot.standing_orders 有 entry 间接证实路径走过 |

---

## 4. UI 卡片透传完整度

| 卡片 | 字段 | 透传情况 |
|---|---|---|
| StrategyCard | `strategy.opening/midgame/lategame` | ✓ L1a/L1b 都正确反映 |
| StandingOrdersCard | `standing_orders[]` | ✓ L3a 透传带 selector/task_summary |
| **TacticsCard** | `active_tactics[]` | **✗ 4 个 tactical case 全空，卡片永远不显示** |
| ProductionOverridesCard | `production_overrides[]` 含 status / status_reason | ✓ L4 三连完整透传 |
| EngagementStanceCard | (不存在) | **✗ L2d/e/L3d 无对应 UI 卡片** |
| EphemeralActionCard | (不存在) | **✗ L3b（一次性 move/scout）无 UI 反馈** |

---

## 5. 修复优先级建议

### 🔴 P0：L2 `tactical_objective` 执行器（影响 4 个死路 case + 用户核心抱怨）

**两个独立子问题**：
1. **执行层**：`_apply_to_facade` 加 `TACTICAL_OBJECTIVE` 分支 → 路由到 sharpy 的 attack_command / squad route。设计需先讨论：是单独抽 squad？还是 override sharpy plan 的 attack_target？还是 push 一个 combat_constraint？
2. **UI 显示**：保证 `active_tactics` snapshot 字段不为空 —— 当前 task_monitor 立即判 done 的问题需要单独看，可能是 done_when initial check 误判，也可能是 commit→release 同 tick 完成 snapshot frame 抓不到。

### 🟡 P1：补 `_apply_to_facade` 执行 log（影响 4 case 的可观察性）

`director.py:1563-1633` 的 ENGAGEMENT_CONSTRAINT / UNIT_CLAIM / MOVE / SCOUT / BUILD_AT 分支都缺 `logger.info`，导致 stdout 无法 verify 执行路径。补一句 `logger.info("apply_to_facade %s payload=%s", t.name, payload)` 即可后续 e2e 自动 verify。

### 🟡 P1：补 EngagementStance UI 卡片 + ephemeral action 反馈

当前 `engagement_constraint`（守家/原地/撤）和 `unit_claim ephemeral`（一次性 move/scout）都没 UI 反馈，玩家无法感知"我刚说的话被收到了"。建议加：
- EngagementStanceCard 显示当前 stance + 剩余 timeout
- ToastCard 短时显示最近一次 ephemeral action（"已派 1 个 Probe 移动到气矿"）

### 🟢 P2：strategy_cancel 走 board 流水（一致性）

L1b 不走 board.submit，导致 directives.jsonl 无记录、events 无 emit。建议统一改成 board.submit(STRATEGY_CANCEL)，与 SET 对称，便于 audit。

---

## 6. driver verify 标准过弱（独立问题）

当前 driver 14/14 PASS 的核心矛盾是 verify 标准太宽容：

| verify_field | 实际判定 | 问题 |
|---|---|---|
| `strategy_changed` | 看 snapshot slot 变化 OR pending_force_strategy OR strategy.set event | ✓ 强 |
| `strategy_cleared` | 看 opening 从 initial → None | ✓ 强 |
| `production_overrides` / `standing_orders` / `active_tactics` | snapshot 字段非空 OR fallback 看 events 有 committed | **fallback 太宽**：L2 三个死路 case 因为 fallback 而 PASS |
| `any_directive_committed` | events 有 directive.committed | **过宽**：只证明 directive 进 board，不证明 bot 执行 |

**建议改造**：
- 死掉 fallback，verify 必须 snapshot 字段真有数据
- 加 `verify_log_pattern` 字段，去 game_log 或 stdout 中 grep 执行 marker（如 `production_override TRAIN` / `set_build switched`）才算 PASS

---

## 7. 测试方法学问题

1. **每 case 单独拉 SC2 太慢**：14 case × 90s + 启动 ~25s ≈ 27 分钟。可改成同一 SC2 game session 多 case 串行（M3 已知问题 #56）。本次没改。
2. **CheatMoney 难度对 L4 不友好**：bot 资源溢出后自动出兵，prereq 满足太快，无法验证 on_hold → active 状态切换。建议 L4 单独跑一次 VeryEasy 难度对照。
3. **LLM 偶尔把 L3 scout 路由到 L2**：L3c 期望走顶层 SCOUT directive，LLM 实际给 tactical_objective verb=scout。这是 prompt 的歧义（"侦察一下" 既可 L2 也可 L3）。L2 死路修好后这条自动得救。

---

## 附录 A：测试日志位置

```
logs/game_20260517_102947_ddef07/  ← L1a
logs/game_20260517_103041_8208b9/  ← L1b
logs/game_20260517_103213_fd10b9/  ← L2a
logs/game_20260517_103330_c81f19/  ← L2b
logs/game_20260517_103407_ae2e8e/  ← L2c
logs/game_20260517_103535_fa9a39/  ← L2d
logs/game_20260517_103655_01b416/  ← L2e
logs/game_20260517_103818_7101d8/  ← L3a
logs/game_20260517_103937_8460b8/  ← L3b
logs/game_20260517_104031_c3c68a/  ← L3c
logs/game_20260517_104206_d0d015/  ← L3d
logs/game_20260517_104346_794f97/  ← L4a
logs/game_20260517_104519_6d1e71/  ← L4b
logs/game_20260517_104624_a3a67f/  ← L4c
```

每个目录含 `commands.jsonl` / `directives.jsonl` / `events.jsonl` / `llm_calls/call_*.json`。`sc2_actions.jsonl` 和 `decisions.jsonl` 在所有 case 中均为空 —— 这两个 sink 没人写，是独立的 logging 漏洞。

## 附录 B：相关文件行号

- `src/vibecraft/bot/director.py:1533-1617` — `_apply_to_facade`（缺 TACTICAL_OBJECTIVE 分支）
- `src/vibecraft/bot/director.py:1619-1633` — `_apply_unit_claim`（缺 logger.info）
- `src/vibecraft/bot/director.py:991-1112` — `_exec_production_override / _exec_tech_override / _exec_expansion_override`（有完整 log）
- `src/vibecraft/server/snapshot.py` 或 director.py `build_snapshot` — `active_tactics` 从 `_in_flight` 筛 TACTICAL_OBJECTIVE 的实现
- `web/src/views/CockpitView.vue:154-157` — TacticsCard 渲染入口
- `docs/adr/0010-four-layer-commands.md` — 四层 directive 设计真理源
