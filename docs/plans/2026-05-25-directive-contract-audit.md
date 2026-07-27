# Directive Contract Test Audit Plan

> **2026-05-25** —— 玩家实测 1 局连暴 5+ silent bug (bug 1/4/5/6/7/8/9/10),根因都是
> "directive 经过 pipeline 但 facade 没收到预期调用"或"路径分支 spec 漂移"。
> 单测覆盖 1530 但**契约层**(submit → bot 端真做了 X)极稀疏。
> task #301 audit:每个 directive type 补"happy path / 缺 hint default / revoke"3 case。

## 痛点

当前测试金字塔倒挂:
- 单测多 (1530) 但多数验 helper 层 / mock 数据结构
- 集成测试停留在战术 (build_acceptance vs SC2 AI),不覆盖玩家 directive
- 真 e2e (玩家 voice → bot 单位真动) 完全没自动化

**典型 silent bug pattern**:
1. LLM/UI 给 directive ✓
2. board submit ✓
3. directive 进 _in_flight / standing_orders / production_overrides ✓
4. commit (1.5s 后) ✓
5. **应该调 facade.X 但没调** ← 没人验证
6. snapshot 看不到差异,玩家以为成功

例: bug 1 (persistent unit_claim 没下 execute_unit_action)、bug 4 (ephemeral
unit_claim 不 cap count)、bug 5 (commit 后卡片消失)、bug 9 (auto_prereq dedup)、
bug 10 (recon 缺 hint on_hold)。**所有这些都是单测+集成测试漏的**。

## 现有覆盖盘点 (2026-05-25)

| Directive Type | 现有 contract test | 缺什么 |
|---|---|---|
| STRATEGY_SET | `test_strategy_set_voice_calls_facade_set_build` (✓ commit→facade.set_build) | UI chip 路径(strategy_action ws frame);时机过期 pending_force 路径;auto_transition 路径 |
| STRATEGY_CANCEL | — | 全缺 (commit → board.slot 清 / sustain mode) |
| PRODUCTION_OVERRIDE | task_monitor 单测覆盖 unit_count_built_since | submit → production_overrides append;commit 后字段反映到 snapshot.command_cards;revoke 清 |
| TECH_OVERRIDE | parser auto-fill done_when test | commit → facade.set_tech_override;auto_prereq dedup (bug 9 漏修补)|
| EXPANSION_OVERRIDE | — | commit → facade.set_expansion_override;target_count default |
| STRUCTURE_OVERRIDE | `test_structure_count_*` (✓ bug 8 修后) | submit → production_overrides;commit → snapshot;done 条件 .ready 语义;auto_prereq dedup (bug 9 contract test ~) |
| ENGAGEMENT_CONSTRAINT | legacy + tests | (legacy, 可能 deprecate) |
| TACTICAL_OBJECTIVE | `test_persistent_retreat_card_visible_after_commit` (bug A);`test_attack_all_in_then_retreat_clears_mode_override` (bug B);`test_ui_button_recon_assigns_default_squad` (bug 10) | A 类各 verb (attack/defend/retreat/vision) × {persistent T/F} × {UI button/voice};B 类各 verb default;revoke A 清 _current_l2_global / B 清 squad |
| UNIT_CLAIM (persistent) | `test_persistent_claim_calls_set_unit_role_on_submit`;`test_persistent_claim_dispatches_primary_action` (bug 1) | hold_position / patrol / move_to / attack_move 各 verb;done_when 触发 done |
| UNIT_CLAIM (ephemeral) | `test_ephemeral_claim_respects_selector_count` (bug 4);`test_ephemeral_card_stays_after_commit` (bug 5) | 各 primary_action verb;done_when |
| SCOUT | `test_scout_directive_dispatches_execute_unit_action` | tag 显式 / tags 数组 / count 显式 三种 selector 路径;timeout 超时 |
| MOVE | `test_move_directive_respects_selector_count` (bug 4) | safe=True 路径 (_tick_safe_move_orders);unit_arrived done_when |
| BUILD_AT | `test_build_at_calls_facade_set_build_location_override` | named_spot 路径(vs point);structure_count_built_since done |
| UNIT_RELEASE | `test_unit_release_with_count_caps` (bug audit 发现的 submit 路径漏 cap,已修) | claimed=True 批量释放路径;commit 后立即 _release_directive_done |
| DROP_ACT | drop_act_director_chain test | warp_then_drop / simple style;_tick_drop_act 每 tick 推进 |

## 下次 session todo (按优先级)

### P0 (实战已暴露相关 path,优先补)
- [ ] auto_prereq dedup contract test (bug 9: refactor 抽 `_has_user_pending_override(struct_name) -> bool` helper 再单测,避免 mock prereq_chain/bot.race 整个 chain)
- [ ] STRUCTURE_OVERRIDE done 条件 `.ready` (bug 8 已加,但加 1 个 integration: structure 实际 build_progress<1 → 不 done)

### P1 (高风险 — 类似 bug pattern 容易隐藏)
- [ ] TACTICAL_OBJECTIVE A 类各 verb × persistent 矩阵:
  - attack persistent → facade.set_combat_intent_override("attack") + attack_target_override + (mode 设)
  - defend persistent → 同上 + set_engagement_stance("defend")
  - retreat persistent → 同上 + set_engagement_stance("retreat")
  - vision persistent → 同 attack 但不 set engagement_stance
- [ ] TACTICAL_OBJECTIVE B 类各 verb default 完整覆盖:
  - harass default 2 Phoenix
  - scout default 1 Probe
  - recon default 4 Stalker (已有)
- [ ] UNIT_CLAIM primary_action verb 矩阵: hold_position / patrol / move_to / attack_move / cast_ability
- [ ] MOVE safe=True 路径 (_tick_safe_move_orders + plan_drop_path 算路径)

### P2 (lower 风险但 audit 完整性)
- [ ] STRATEGY_SET UI chip 路径 (跟 voice 区分);时机过期 → pending_force_strategy
- [ ] STRATEGY_CANCEL 全套
- [ ] PRODUCTION_OVERRIDE / TECH_OVERRIDE / EXPANSION_OVERRIDE happy path
- [ ] SCOUT selector 3 种路径 (tag/tags/count)
- [ ] BUILD_AT named_spot 路径
- [ ] DROP_ACT warp_then_drop + simple 状态机推进

### P3 (架构改进)
- [ ] Director 内部抽 unified `_resolve_selector_with_count` helper 已做;考虑抽 `_resolve_named_spot(area_or_tuple)` helper
- [ ] 把 4 个分支共享逻辑 (`_apply_unit_claim` ephemeral / `_assign_standing_order_units` persistent /
      _claim_directive_units / _apply_unit_release) 重构成统一 helper,从源头消除 spec 漂移

### P4 (集成测 + e2e 框架)
- [ ] 写一个 "directive contract harness":parametrize 跑遍所有 type,统一 builder + assertion,新 type 加进来自动跑
- [ ] real SC2 e2e smoke: 启 SC2 + bot + 模拟 ws frame "tactical_action recon" → 看单位真去 enemy_natural

## 工作量估算

- P0: ~1h (2 个 test + refactor helper)
- P1: ~2-3h (矩阵覆盖,subagent fan-out)
- P2: ~2h
- P3: ~3-4h (refactor,需要 review)
- P4: ~4h+ (新框架,需要设计)

合计 12-15h。MVP 前至少 P0+P1 必做 (~3-4h)。
