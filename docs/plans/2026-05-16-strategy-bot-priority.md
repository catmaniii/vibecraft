# 剧本 vs 兜底 bot 决策优先级机制设计

**日期**: 2026-05-16
**触发**: 用户反馈 Aristaeus 一直只出一种兵 + 不开矿。诊断:**voicecraft 现有 strategies 的 mid/lategame commitments 完全没接到任何 bot Manager**,bot 自己 hardcoded army comp 单调。

---

## 1. 现有 strategies 资产盘点

### `opening_build` (1g_robo_immortal / 4bg)

| 字段 | 含义 | 是否已接 bot |
|---|---|---|
| `steps` | build order(`<supply> <verb> <obj>`) | ✅ 已接 `build_order_runner` (config["Builds"]) |
| `phases` | UI 显示 4 阶段 | 仅 UI 展示,不影响 bot |
| `scout_at` | 侦察时机 | ❌ 未接 bot scout manager |
| `abort_signals` | 见敌方 X → 切剧本 | ❌ 未接(需要 Director 监听 enemy units) |
| `default_transitions` | 默认转中盘 | ❌ 未接(需要监听 "opening 跑完" 自动 transition) |

### `midgame_stance` (iac_2base)

| 字段 | 含义 | 是否已接 |
|---|---|---|
| `commitments.units` | 目标兵种数量 `{stalker:8, sentry:4, immortal:3, archon:3, zealot:8}` | ❌ **未接 ProductionController** ← **核心 gap** |
| `commitments.tech` | 必研升级 `[WarpGate, Charge, GroundWeapons]` | ❌ 未接 UpgradeController |
| `commitments.structures` | 目标结构 `{gateway:6, robo:2, twilight:1, templar_archives:1}` | ❌ 未接 |
| `commitments.expansions` | 目标分基地数 `2` | ❌ 未接 ExpansionController |
| `attack_window` | 时机 `9:30-11:30` | ❌ 未接 combat manager |
| `micro_doctrine` | 战术口令 | ❌ 未接(M3+ 战术微操) |
| `expire_action` / `lategame_transitions` | 转后期 | ❌ 未接 |

### `lategame_doctrine` (skytoss)

| 字段 | 含义 | 是否已接 |
|---|---|---|
| `target_composition` | 目标比例 `{carrier:12, tempest:3, ht:5, archon:4, mothership:1, observer:2}` | ❌ **未接 ProductionController** |
| `required_tech` / `required_structures` | 必研 / 必造 | ❌ 未接 |
| `engagement_doctrine` | 战术口令 | ❌ 未接 |
| `counters_against` / `weak_against` | 克制关系(meta info) | 不需要接 |

### 用户感受到的"bot 弱"的真实原因

```
voicecraft 现有路径:
  玩家说"切 IAC" → set_build(iac_2base) → switch_opening 失败(iac_2base 是 midgame 不是 opening)
                                          ↓
                                       commitment 数据完全没用到
                                          ↓
                              bot 依然按 Aristaeus 内置 army_comp 出兵
                              (Aristaeus 是 starter 模板,army_comp 单调)
```

**这是设计层 gap,不是 bot 选错**。即便换 ZerGreenBot/HarstemsAunt,如果不接 voicecraft commitments,bot 还是按自己默认决策。

---

## 2. 设计:三层注入 + 优先级仲裁

### 2.1 注入接口(StrategyApplier)

新增 `src/voicecraft/bot/strategy_applier.py`,**每个 stage slot 变化时翻译成 bot manager 的 override**:

```
opening_build  → build_order_runner.switch_opening(steps yaml)      [已有]
                 build_order_runner.set_chrono_targets(@chrono)       [新]
                 scout_manager.set_scout_target(scout_at)             [新]

midgame_stance → ProductionController.set_army_composition(units → proportion dict)
                 UpgradeController.set_upgrade_list(tech)
                 ExpansionController.set_target_count(expansions)
                 StructureController.set_target_counts(structures)

lategame_doctrine → ProductionController.set_army_composition(target_composition → dict)
                    UpgradeController.set_upgrade_list(required_tech)
                    StructureController.set_target_counts(required_structures)
```

兵种数 → ares ProductionController 接受的 proportion:
```python
# iac_2base 的 stalker=8 sentry=4 immortal=3 archon=3 zealot=8 → 总 26
# proportion = count / total
{Stalker: 8/26, Sentry: 4/26, Immortal: 3/26, Archon: 3/26, Zealot: 8/26}
```

### 2.2 优先级仲裁(用户要求:不冲突听 bot,冲突听剧本)

**关键洞察**:"冲突"= 剧本的 commitments 明确指定了某字段,且 bot 默认值与剧本目标不一致。

| 字段 | 剧本 specified | bot default | 听谁 |
|---|---|---|---|
| `army_composition[Stalker]` | iac_2base 指定 8 | Aristaeus default 0.55 | **听剧本**(override) |
| `army_composition[Phoenix]` | iac_2base **没说** | Aristaeus 不出 | **听 bot**(默认值生效) |
| `target_expansion_count` | iac_2base 指定 2 | bot 默认 4 | **听剧本** |
| 单位 micro 行为 | iac_2base 给 `micro_doctrine` 但 voicecraft 还没实现 | Aristaeus 自己微操 | **听 bot**(剧本未接) |

实现机制:`StrategyApplier` 持有 `Optional[Override]` 对象,每个 stage 一组。每 tick 在 `on_step` 把 override 注入到对应 manager;**override 为 None 的字段保持 bot 自己的默认**。

### 2.3 BoardEvent 接入

`board.slots[stage]` 变化 → `BoardEventKind.STRATEGY_CHANGED` → `Director._dispatch_event` → 调 `StrategyApplier.apply(stage, new_slot)` → 更新 override 表。下一次 `on_step` 用新 override re-register bot behaviors。

---

## 3. 卡点分析:Aristaeus 不友好

Aristaeus 的 `ProductionManager` 是它自己写的,**不是直接用 ares ProductionController**,我们 hook 不到 army_composition 注入点。两个出路:

- **A**. 调研找一个用 ares ProductionController(没自己包一层)的 bot,直接 hook ProductionController 的构造参数
- **B**. 抛弃 Aristaeus,换调研中 top-rated 的 bot(待 agent 报告)

调研结果决定 §4 实施细节。

---

## 4. 实施 plan(待调研结果填具体 bot)

| 步骤 | 内容 | 工时 |
|---|---|---|
| **R1** | 等 bot 调研报告,选 top1 替换 Aristaeus | (并行) |
| **A1** | 新增 `bot/strategy_applier.py`(~150 行) | 0.5d |
| **A2** | strategies yaml 加字段:opening 的 `army_composition_after_build`(opening 跑完后注入到 mid stage 的初始 army comp);midgame 的 `commitments.units` → proportion 翻译辅助函数 | 0.3d |
| **A3** | `Director` hook STRATEGY_CHANGED event → 调 StrategyApplier | 0.3d |
| **A4** | `_VoiceCraftProtossBot.on_step` 每 tick 调 `StrategyApplier.apply_to_bot_managers()` 注入到 Production / Upgrade / Expansion / Structure Controller | 0.5d |
| **A5** | 自动 transition:监听 `runner.build_completed` → submit `STRATEGY_SET(midgame)` directive(BOT_INTERNAL 来源,玩家可覆盖) | 0.3d |
| **A6** | strategy schema 升级 + 单测覆盖 commitments → proportion 翻译 | 0.5d |
| **B1** | 真实 SC2 端到端验证:切 IAC → 看到出叉子/不朽/白球;切 Skytoss → 看到航母 | 1d |

**总工时 3.4-4.4 天**(单 dev,不含 bot 替换工作量)。

---

## 5. 潜在阻断 / Trade-off

- **bot 自带 ArmyCompositionOverride 接口?** ares ProductionController 接收一个 `army_composition` dict,但 Aristaeus / QueenBot 自己写了 Manager 包了一层 → 接口可能不暴露。**接入新 bot 时第一件事:grep 是否能直接传 army_comp 进 Production Manager**
- **opening_build.steps 跑完时 → 自动 transition 到 midgame**:需要规则。`runner.build_completed=True` + 当前 stage=opening + 没有 voice 切到别的 → AUTO_TRANSITION submit midgame stage 的 default
- **多个剧本 commitments 合并**:voice 同时切 opening + midgame 时怎么 merge?走 IssuedBy 优先级:VOICE > AUTO_TRANSITION > BOT_INTERNAL

---

## 6. 决策点(待用户确认,但**等 bot 调研报告后再问**)

- **B1**:如果 top bot 是 ares 框架但 ProductionManager 自包了一层,愿意 patch vendor / 还是另选?
- **B2**:opening 跑完是否自动 transition 到 midgame?
  - 自动 = 玩家不操心,但可能违反"玩家显式 voice"的初衷
  - 不自动 = 玩家不发指令时 bot 就停在 opening 默认 army comp 上(就是现在的问题)
- **B3**:strategy yaml 字段是否要给 mid/lategame 加 explicit `chrono_targets` / `scout_pattern`,让兜底更稳?
