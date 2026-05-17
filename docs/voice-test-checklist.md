# 语音指令手测清单

> 系统性遍历当前所有 directive 类型 + done_when 完成路径 + 边界 case，便于一条
> 一条手测验证。**勾选符号**：✅ 通过 / ⚠️ 部分通过 / ❌ 失败 / 🟡 待测。
>
> 测试环境：神族 vs SC2 内置 AI VeryEasy 或 Easy（让自己活久点便于多发指令）。
> 推荐剧本：先 force `1g_robo_immortal` 开局（稳），到 5 分钟切 IAC。

## 0. 准备

- [ ] 服务起：`.\scripts\start.ps1 -Token vibecraft-dev`
- [ ] 手机扫码进 PWA
- [ ] SC2 开自定义 → 神族 Slot 1（你加入）+ 内置 AI Slot 2
- [ ] 开打，先静默等 30 秒让 bot 自己跑（验证基础工作）

每条 case 验证三件事：
1. **PWA 卡片出现** —— 看 layer 标签、display 文字
2. **状态/进度正确** —— 等待中/执行中色 + 条件清单 ✓/○
3. **完成/撤销表现** —— 自动消失（L4）或点 × 立即消失

---

## L1 宏观策略（共 ~6 case）

L1 卡片显示在右侧"当前宏观策略"框，**不在指令列表**。

### L1.1 开局剧本切换（opening 槽）

| # | 话语 | 期望 | 备注 |
|---|---|---|---|
| 🟡 L1.1a | "1门VR" / "速不朽" | 切到 1g_robo_immortal | 默认就是这个，可能无变化 |
| 🟡 L1.1b | "4BG 一波" / "4 折跃压制" | 切到 4bg | 框内剧本名变 4BG，phase 重置 |

### L1.2 中期剧本（midgame 槽）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L1.2a | "切 IAC" / "改 IAC" / "叉光不朽推" | midgame slot 出现 iac_2base |
| 🟡 L1.2b | "上闪烁打一波" / "闪追 timing" | midgame 切到双矿闪追 |

### L1.3 后期剧本（lategame 槽）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L1.3a | "上航母" / "航母收" / "Skytoss" | lategame slot 出现 skytoss |

### L1.4 L1 关闭（× 按钮）

| # | 操作 | 期望 |
|---|---|---|
| 🟡 L1.4 | 当前宏观策略框点右上角 × | 该 stage slot 清空，bot 回退到默认决策 |

---

## L2 战术指令（共 ~12 case）

L2 卡片进**指令列表**，标签 L2。

### L2.A 类（done_when=None，持续到 ×）

`attack / defend / retreat / vision` —— **不会自动消失**，必须手动 ×。

| # | 话语 | 期望卡片 |
|---|---|---|
| 🟡 L2.A1 | "进攻对方自然" | L2 `attack enemy_natural` 等待执行 → 绿色执行中 |
| 🟡 L2.A2 | "全军守家" | L2 `defend` 状态绿色 |
| 🟡 L2.A3 | "全员撤回基地" | L2 `retreat` 状态绿色 |
| 🟡 L2.A4 | "看住对方主基地" | L2 `vision enemy_main` 状态绿色 |

每条**测点 × 解除**，应立即从卡片堆消失。

### L2.B 类（done_when 必带，达成自动消失）

| # | 话语 | 期望 done_when | 期望进度 |
|---|---|---|---|
| 🟡 L2.B1 | "凤凰打死对方 5 个农民就回" | `enemy_killed_in_area(Probe, >=, 5, enemy_main)` | `歼敌 5 于 enemy_main` |
| 🟡 L2.B2 | "30 秒后撤" | `time_elapsed_since(30)` | `30 秒后` 倒计时进度 0→30 |
| 🟡 L2.B3 | "看一眼对方主基地" | `vision_acquired(enemy_main, hold=5)` | `侦察到 enemy_main` |
| 🟡 L2.B4 | "凤凰骚扰对方" | `harass` + done_when 或 None | 看 LLM 怎么解 |

### L2 engagement_constraint stance

| # | 话语 | 期望 stance |
|---|---|---|
| 🟡 L2.C1 | "所有人原地待命别动" | `hold`（全军 stance，不是 hold_position）|
| 🟡 L2.C2 | "随便打" / "自由开火" | `free` |
| 🟡 L2.C3 | "撤" / "全部撤" | `retreat` |
| 🟡 L2.C4 | "守家" / "防守" | `defend` |

---

## L3 单位 standing / 一次性（共 ~10 case）

L3 卡片进**指令列表**，标签 L3 或显示在 standing orders 区。

### L3.a 一次性夺权（任务完成归还）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L3.a1 | "派一个农民去对方主基地探路" | L3 scout（Probe → enemy_main）；探机走到位置 |
| 🟡 L3.a2 | "追猎去对面家火力侦察" | L3 unit_claim verb=attack_move（Stalker → enemy_main）|
| 🟡 L3.a3 | "派 Obs 飞过对面主基地" | L3 scout（Observer → enemy_main）|

### L3.b Standing Order（persistent=true，永久占住）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L3.b1 | "那个叉子守这里别动" | L3 unit_claim persistent verb=hold_position |
| 🟡 L3.b2 | "DT 守对面气矿别动" | L3 unit_claim persistent verb=hold_position（DT，target=enemy_main_gas）|
| 🟡 L3.b3 | "3 凤凰巡逻自然分矿" | L3 unit_claim persistent verb=patrol（Phoenix → natural）|

撤销验证：点 × → 单位 release 回 base bot。

### L3.c 调动 / 移动

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L3.c1 | "追猎集结主路口" | L3 unit_claim verb=move_to / regroup |
| 🟡 L3.c2 | "全军回家" | 走 L2 retreat 而不是 L3 move_to（看 LLM 分流）|

### L3.d 指定点盖建筑

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L3.d1 | "11 点放个水晶" | L3 build_at structure=Pylon point=[x,y]（或 ambiguous 让你点地图）|
| 🟡 L3.d2 | "斜坡架光子炮" | L3 build_at PhotonCannon point=ramp 或 location_hint |

---

## L4 产能 override（共 ~14 case）

**完成自动消失**是 L4 的关键语义。每条都要观察"造满后卡片消失"。

### L4.1 production_override 单兵种

| # | 话语 | 期望 done_when | 完成判据 |
|---|---|---|---|
| 🟡 L4.1a | "出 1 个叉子" | `unit_count_built_since(Zealot, >=1)` | 卡片造完消失 |
| 🟡 L4.1b | "下个 BG 出 2 哨兵" | `unit_count_built_since(Sentry, >=2)` | `[0/2] → [2/2] → 卡片消失` |
| 🟡 L4.1c | "出 4 个不朽" | `unit_count_built_since(Immortal, >=4)` | 长进度（Robo 慢）|
| 🟡 L4.1d | "持续出叉子" | `done_when=None` | 永不自动消失，必须手动 × |

### L4.2 production_override **多兵种合并**（关键新功能）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L4.2a | **"出 2 个叉子加 3 个追猎"** | **1 张卡**，两条进度条 `造 2 个 叉子 [0/2]` + `造 3 个 追猎 [0/3]`，两个都满才整体消失 |
| 🟡 L4.2b | "造 2 不朽 1 棱镜" | 1 张卡两条进度 |
| 🟡 L4.2c | "1 哨兵 1 使徒 1 追猎" | 1 张卡三条进度 |

### L4.3 tech_override

| # | 话语 | 期望 done_when | 完成判据 |
|---|---|---|---|
| 🟡 L4.3a | "先研闪烁" | `tech_done(BlinkTech)` | 闪烁研完卡片消失 |
| 🟡 L4.3b | "升攻 1" | `tech_done(ProtossGroundWeaponsLevel1)` | — |
| 🟡 L4.3c | "上风暴" | `tech_done(PsiStormTech)` | — |
| 🟡 L4.3d | "上闪烁加冲锋" | 两条 tech_override？或单条 all_of？看 LLM |

### L4.4 expansion_override

| # | 话语 | 期望 done_when |
|---|---|---|
| 🟡 L4.4a | "现在开三矿" | `expansion_count(>=, 3)` |
| 🟡 L4.4b | "马上点矿" | expansion_override target_count+1 |

### L4.5 structure_override 单建筑

| # | 话语 | 期望 done_when |
|---|---|---|
| 🟡 L4.5a | "家里补到 8 BG" | `structure_count(Gateway, >=, 8)` location_hint=main |
| 🟡 L4.5b | "ramp 放 1 cannon" | `structure_count(PhotonCannon, >=, 1)` location_hint=ramp |

### L4.6 structure_override **多建筑合并**（关键新功能）

| # | 话语 | 期望 |
|---|---|---|
| 🟡 L4.6a | **"ramp 放 2 cannon 1 BF"** | **1 张卡**两条进度条，两个都建够才整体消失 |
| 🟡 L4.6b | "二矿放 2 PY 1 BF" | 1 张卡两条进度 |

---

## 复合 / 边界 case（共 ~10 case）

### C.1 复合句拆多条 directive

| # | 话语 | 期望 |
|---|---|---|
| 🟡 C.1a | "4BG 一波，凤凰骚扰对面" | 2 条 directive：L1 strategy_set + L2 tactical_objective |
| 🟡 C.1b | "切 IAC，先研闪烁" | 2 条：L1 + L4 tech_override |
| 🟡 C.1c | "守家，出 2 哨兵" | 2 条：L2 + L4 |

### C.2 撤销

| # | 操作 | 期望 |
|---|---|---|
| 🟡 C.2a | 发任意指令 1.5 秒内点 [↩] | 撤销，不进卡片 |
| 🟡 C.2b | 卡片右上角 × | 立刻从卡片堆消失，directive.revoked 事件入 events.jsonl |
| 🟡 C.2c | 当前宏观策略框 × | L1 slot 清空 |

### C.3 LLM 容错

| # | 话语 | 期望 |
|---|---|---|
| 🟡 C.3a | "嗯…那个…就是…" | parse error 或 ambiguous，最近指令区显示 ❌ |
| 🟡 C.3b | "造一个虫族飞龙" | ambiguous 或 parse error（不是神族）|
| 🟡 C.3c | "你给我赢" | ambiguous（不知该怎么办）|

### C.4 视野（不进卡片堆）

| # | 操作 | 期望 |
|---|---|---|
| 🟡 C.4a | 拖小地图 | SC2 大屏视野跟着切，无 cooldown |
| 🟡 C.4b | "看一下对方家" | view_move 帧，SC2 视野切到 enemy_main |
| 🟡 C.4c | "锁定那个母舰" | view_follow（如果母舰在场）|

### C.5 cooldown

| # | 操作 | 期望 |
|---|---|---|
| 🟡 C.5a | 连续两条战略指令 < 10s | 第二条阻挡，UI 显示倒计时 |

---

## 后台日志验证（每次 case 后可选检查）

每局对应 `logs/game_<timestamp>_<id>/` 目录：

| 文件 | 看啥 |
|---|---|
| `directives.jsonl` | 每条 directive 的 submitted → committed → released / revoked 生命周期 |
| `events.jsonl` | 重要事件（directive.committed / .released / .revoked / strategy.set / tech_complete）|
| `llm_calls/call_NNN.json` | LLM 原始 prompt + 响应，看 directive 怎么解析的 |
| `sc2_actions.jsonl` | execute_unit_action / set_build 真实下发的 SC2 命令 |
| `commands.jsonl` | 玩家原话 |

---

## 已知盲区 / 已知 bug

- LLM 偶尔把 production_override 多兵种拆成多条（prompt 已强调单条 + all_of，验证 LLM 是否真听）
- L4 production override 在 sharpy 端的真出兵 wire（execute_overrides_step）还在 production_overrides loop 跑，需观察 `_exec_production_override` 日志是否真调 `bot.train`
- BOT 决策卡片目前**没有**根据 L2 active 自动隐藏（设计文档 §5 写过但未实现）—— 不影响 case 测试

---

## 验证流程建议

按这个顺序覆盖最快：
1. 起服 → 进对局，先静默看 bot 自动跑（验基础工作）
2. **L1 全跑一遍**（验 4 个 stage slot 都能切 + ×）
3. **L4 全跑一遍**（验完成自动消失 + 多兵种/多建筑合并）—— 最容易看进度
4. **L2.A 类** + **L2.C stance**（验持续到 × 的语义）
5. **L2.B 类** + **L3 standing**（耗时长，最后跑）
6. **C 系列复合 / 撤销 / 错话**（穿插测）

每 case 走完顺手记下结果（✅/⚠️/❌ + 一行问题描述），跑完一轮汇总能看出系统性 bug。
