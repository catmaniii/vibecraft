# LLM 指令解析详细测试报告

**测试时间**：自动生成


## 汇总

| 配置 | Accuracy | 平均耗时 |
|---|---|---|
| **Flash (retry=1)** | 42/42 = **100.0%** | **2538 ms** |

## per-case accuracy 矩阵

| Case | inject | Flash (retry=1) |
|---|---|---|
| L1a_strategy_set | `切叉球一波` | ✓ 3/3 |
| L1b_strategy_cancel | `取消所有剧本` | ✓ 3/3 |
| L2a_tactical_attack | `进攻对方自然` | ✓ 3/3 |
| L2b_tactical_scout_vision | `在对方主基地保持视野` | ✓ 3/3 |
| L2c_tactical_harass_killed | `凤凰打死对方 5 个农民就回` | ✓ 3/3 |
| L2d_engagement_defend | `守家别出门` | ✓ 3/3 |
| L2e_engagement_retreat_timer | `30 秒后撤` | ✓ 3/3 |
| L3a_unit_claim_persistent | `探机巡逻自然别动` | ✓ 3/3 |
| L3b_unit_claim_ephemeral | `让那个探机移动到气矿` | ✓ 3/3 |
| L3c_scout | `派探机侦察 11 点` | ✓ 3/3 |
| L3d_engagement_hold | `所有人原地待命别动` | ✓ 3/3 |
| L4a_production_override_count | `下个 BG 出俩哨兵` | ✓ 3/3 |
| L4b_tech_override | `先研闪烁` | ✓ 3/3 |
| L4c_expansion_override | `马上去开三矿` | ✓ 3/3 |

## 每 case 详细数据

### L1a_strategy_set

**Inject**：`切叉球一波`

**Expected**：
- type: `strategy_set`
- must_have: `payload.stage`='midgame', `payload.strategy_id`='iac_2base'

**Flash (retry=1)**：✓ 3/3 (avg 2349 ms)
- trial 1 PASS (1953 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 2 PASS (2969 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 3 PASS (2125 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")

### L1b_strategy_cancel

**Inject**：`取消所有剧本`

**Expected**：
- type: `strategy_cancel`
- must_have: `payload.stage`='all'

**Flash (retry=1)**：✓ 3/3 (avg 2151 ms)
- trial 1 PASS (2235 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 2 PASS (2109 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 3 PASS (2109 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")

### L2a_tactical_attack

**Inject**：`进攻对方自然`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='attack', `payload.target_area`='enemy_natural'

**Flash (retry=1)**：✓ 3/3 (avg 2713 ms)
- trial 1 PASS (2593 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 2 PASS (3297 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 3 PASS (2250 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})

### L2b_tactical_scout_vision

**Inject**：`在对方主基地保持视野`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`=['vision', 'scout'], `payload.target_area`='enemy_main'

**Flash (retry=1)**：✓ 3/3 (avg 2412 ms)
- trial 1 PASS (2234 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 2 PASS (2516 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 3 PASS (2485 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})

### L2c_tactical_harass_killed

**Inject**：`凤凰打死对方 5 个农民就回`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='harass'

**Flash (retry=1)**：✓ 3/3 (avg 2511 ms)
- trial 1 PASS (2469 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 2 PASS (2657 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 3 PASS (2406 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})

### L2d_engagement_defend

**Inject**：`守家别出门`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='defend'
- forbidden: `payload.stance` ∉ ['hold_position', '守家', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 2521 ms)
- trial 1 PASS (2313 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 2 PASS (2343 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 3 PASS (2906 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")

### L2e_engagement_retreat_timer

**Inject**：`30 秒后撤`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='retreat'

**Flash (retry=1)**：✓ 3/3 (avg 2250 ms)
- trial 1 PASS (2203 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 2 PASS (2406 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 3 PASS (2141 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})

### L3a_unit_claim_persistent

**Inject**：`探机巡逻自然别动`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.persistent`=True, `payload.task.primary_action.verb`=['patrol', 'hold_position', 'guard_position']

**Flash (retry=1)**：✓ 3/3 (avg 2703 ms)
- trial 1 PASS (2937 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 2 PASS (2844 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 3 PASS (2328 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)

### L3b_unit_claim_ephemeral

**Inject**：`让那个探机移动到气矿`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.task.primary_action.verb`='move_to', `payload.persistent`=False
- forbidden: `payload.task.primary_action.verb` ∉ ['scout', 'move', 'gather', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 3672 ms)
- trial 1 PASS (3906 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 2 PASS (4391 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 3 PASS (2719 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)

### L3c_scout

**Inject**：`派探机侦察 11 点`

**Expected**：
- type: `scout`
- must_have: `payload.selector.unit_type`='Probe'

**Flash (retry=1)**：✓ 3/3 (avg 2328 ms)
- trial 1 PASS (2813 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 2 PASS (2109 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 3 PASS (2062 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})

### L3d_engagement_hold

**Inject**：`所有人原地待命别动`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='hold'
- forbidden: `payload.stance` ∉ ['hold_position', 'guard', 'defend']

**Flash (retry=1)**：✓ 3/3 (avg 2083 ms)
- trial 1 PASS (2015 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 2 PASS (2234 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 3 PASS (2000 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")

### L4a_production_override_count

**Inject**：`下个 BG 出俩哨兵`

**Expected**：
- type: `production_override`
- must_have: `payload.unit_type`='Sentry', `payload.count`=2

**Flash (retry=1)**：✓ 3/3 (avg 2422 ms)
- trial 1 PASS (2500 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 2 PASS (2485 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 3 PASS (2282 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})

### L4b_tech_override

**Inject**：`先研闪烁`

**Expected**：
- type: `tech_override`
- must_have: `payload.upgrade_id`=['BlinkTech', 'Blink', 'BLINKTECH']

**Flash (retry=1)**：✓ 3/3 (avg 2396 ms)
- trial 1 PASS (2891 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 2 PASS (2093 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 3 PASS (2203 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})

### L4c_expansion_override

**Inject**：`马上去开三矿`

**Expected**：
- type: `expansion_override`
- must_have: `payload.target_count`=3

**Flash (retry=1)**：✓ 3/3 (avg 3016 ms)
- trial 1 PASS (3609 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3) | tactical_objective(verb="expand", target_area="natural_third", done_when={kind='expansion_count', op='>=', value=3})
- trial 2 PASS (2844 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3)
- trial 3 PASS (2594 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3) | tactical_objective(verb="expand", target_area="third_base", done_when={kind='expansion_count', op='>=', value=3})
