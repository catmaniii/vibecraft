# LLM 指令解析详细测试报告

**测试时间**：自动生成


## 汇总

| 配置 | Accuracy | 平均耗时 |
|---|---|---|
| **Flash (retry=1)** | 42/42 = **100.0%** | **2340 ms** |
| **Pro (retry=1)** | 42/42 = **100.0%** | **7512 ms** |

## per-case accuracy 矩阵

| Case | inject | Flash (retry=1) | Pro (retry=1) |
|---|---|---|---|
| L1a_strategy_set | `切叉球一波` | ✓ 3/3 | ✓ 3/3 |
| L1b_strategy_cancel | `取消所有剧本` | ✓ 3/3 | ✓ 3/3 |
| L2a_tactical_attack | `进攻对方自然` | ✓ 3/3 | ✓ 3/3 |
| L2b_tactical_scout_vision | `在对方主基地保持视野` | ✓ 3/3 | ✓ 3/3 |
| L2c_tactical_harass_killed | `凤凰打死对方 5 个农民就回` | ✓ 3/3 | ✓ 3/3 |
| L2d_engagement_defend | `守家别出门` | ✓ 3/3 | ✓ 3/3 |
| L2e_engagement_retreat_timer | `30 秒后撤` | ✓ 3/3 | ✓ 3/3 |
| L3a_unit_claim_persistent | `探机巡逻自然别动` | ✓ 3/3 | ✓ 3/3 |
| L3b_unit_claim_ephemeral | `让那个探机移动到气矿` | ✓ 3/3 | ✓ 3/3 |
| L3c_scout | `派探机侦察 11 点` | ✓ 3/3 | ✓ 3/3 |
| L3d_engagement_hold | `所有人原地待命别动` | ✓ 3/3 | ✓ 3/3 |
| L4a_production_override_count | `下个 BG 出俩哨兵` | ✓ 3/3 | ✓ 3/3 |
| L4b_tech_override | `先研闪烁` | ✓ 3/3 | ✓ 3/3 |
| L4c_expansion_override | `马上去开三矿` | ✓ 3/3 | ✓ 3/3 |

## 每 case 详细数据

### L1a_strategy_set

**Inject**：`切叉球一波`

**Expected**：
- type: `strategy_set`
- must_have: `payload.stage`='midgame', `payload.strategy_id`='iac_2base'

**Flash (retry=1)**：✓ 3/3 (avg 2156 ms)
- trial 1 PASS (1781 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 2 PASS (2640 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 3 PASS (2047 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")

**Pro (retry=1)**：✓ 3/3 (avg 7469 ms)
- trial 1 PASS (7406 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 2 PASS (6812 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 3 PASS (8188 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")

### L1b_strategy_cancel

**Inject**：`取消所有剧本`

**Expected**：
- type: `strategy_cancel`
- must_have: `payload.stage`='all'

**Flash (retry=1)**：✓ 3/3 (avg 1958 ms)
- trial 1 PASS (2031 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 2 PASS (1828 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 3 PASS (2016 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")

**Pro (retry=1)**：✓ 3/3 (avg 7011 ms)
- trial 1 PASS (6766 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 2 PASS (7688 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 3 PASS (6578 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")

### L2a_tactical_attack

**Inject**：`进攻对方自然`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='attack', `payload.target_area`='enemy_natural'

**Flash (retry=1)**：✓ 3/3 (avg 2437 ms)
- trial 1 PASS (2484 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 2 PASS (2469 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 3 PASS (2359 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})

**Pro (retry=1)**：✓ 3/3 (avg 9271 ms)
- trial 1 PASS (8609 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 2 PASS (8156 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 3 PASS (11047 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})

### L2b_tactical_scout_vision

**Inject**：`在对方主基地保持视野`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`=['vision', 'scout'], `payload.target_area`='enemy_main'

**Flash (retry=1)**：✓ 3/3 (avg 2755 ms)
- trial 1 PASS (2812 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 2 PASS (3297 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 3 PASS (2156 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})

**Pro (retry=1)**：✓ 3/3 (avg 7661 ms)
- trial 1 PASS (7406 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 2 PASS (7484 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 3 PASS (8093 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})

### L2c_tactical_harass_killed

**Inject**：`凤凰打死对方 5 个农民就回`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='harass'

**Flash (retry=1)**：✓ 3/3 (avg 2453 ms)
- trial 1 PASS (2313 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 2 PASS (2437 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 3 PASS (2610 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})

**Pro (retry=1)**：✓ 3/3 (avg 8474 ms)
- trial 1 PASS (8297 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 2 PASS (8500 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 3 PASS (8625 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})

### L2d_engagement_defend

**Inject**：`守家别出门`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='defend'
- forbidden: `payload.stance` ∉ ['hold_position', '守家', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 2031 ms)
- trial 1 PASS (1953 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 2 PASS (2188 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 3 PASS (1953 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")

**Pro (retry=1)**：✓ 3/3 (avg 5641 ms)
- trial 1 PASS (5719 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 2 PASS (5750 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 3 PASS (5453 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")

### L2e_engagement_retreat_timer

**Inject**：`30 秒后撤`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='retreat'

**Flash (retry=1)**：✓ 3/3 (avg 2037 ms)
- trial 1 PASS (2000 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 2 PASS (2063 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat")
- trial 3 PASS (2047 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})

**Pro (retry=1)**：✓ 3/3 (avg 7323 ms)
- trial 1 PASS (7062 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 2 PASS (7562 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 3 PASS (7344 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})

### L3a_unit_claim_persistent

**Inject**：`探机巡逻自然别动`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.persistent`=True, `payload.task.primary_action.verb`=['patrol', 'hold_position', 'guard_position']

**Flash (retry=1)**：✓ 3/3 (avg 2693 ms)
- trial 1 PASS (2781 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 2 PASS (2844 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 3 PASS (2453 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)

**Pro (retry=1)**：✓ 3/3 (avg 8709 ms)
- trial 1 PASS (9469 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 2 PASS (7485 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 3 PASS (9172 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)

### L3b_unit_claim_ephemeral

**Inject**：`让那个探机移动到气矿`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.task.primary_action.verb`='move_to', `payload.persistent`=False
- forbidden: `payload.task.primary_action.verb` ∉ ['scout', 'move', 'gather', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 3046 ms)
- trial 1 PASS (4531 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 2 PASS (2218 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 3 PASS (2390 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)

**Pro (retry=1)**：✓ 3/3 (avg 8859 ms)
- trial 1 PASS (9547 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 2 PASS (7531 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 3 PASS (9500 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)

### L3c_scout

**Inject**：`派探机侦察 11 点`

**Expected**：
- type: `scout`
- must_have: `payload.selector.unit_type`='Probe'

**Flash (retry=1)**：✓ 3/3 (avg 2198 ms)
- trial 1 PASS (2079 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 2 PASS (2203 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 3 PASS (2313 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})

**Pro (retry=1)**：✓ 3/3 (avg 7151 ms)
- trial 1 PASS (6985 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 2 PASS (7219 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 3 PASS (7250 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})

### L3d_engagement_hold

**Inject**：`所有人原地待命别动`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='hold'
- forbidden: `payload.stance` ∉ ['hold_position', 'guard', 'defend']

**Flash (retry=1)**：✓ 3/3 (avg 1990 ms)
- trial 1 PASS (2000 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 2 PASS (1938 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 3 PASS (2031 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")

**Pro (retry=1)**：✓ 3/3 (avg 6036 ms)
- trial 1 PASS (5922 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 2 PASS (5937 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 3 PASS (6250 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")

### L4a_production_override_count

**Inject**：`下个 BG 出俩哨兵`

**Expected**：
- type: `production_override`
- must_have: `payload.unit_type`='Sentry', `payload.count`=2

**Flash (retry=1)**：✓ 3/3 (avg 2364 ms)
- trial 1 PASS (2515 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 2 PASS (2375 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 3 PASS (2203 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})

**Pro (retry=1)**：✓ 3/3 (avg 7787 ms)
- trial 1 PASS (7860 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 2 PASS (8110 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 3 PASS (7390 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})

### L4b_tech_override

**Inject**：`先研闪烁`

**Expected**：
- type: `tech_override`
- must_have: `payload.upgrade_id`=['BlinkTech', 'Blink', 'BLINKTECH']

**Flash (retry=1)**：✓ 3/3 (avg 2307 ms)
- trial 1 PASS (2141 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 2 PASS (2156 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 3 PASS (2625 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})

**Pro (retry=1)**：✓ 3/3 (avg 7172 ms)
- trial 1 PASS (6953 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='Blink'})
- trial 2 PASS (7516 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 3 PASS (7047 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink")

### L4c_expansion_override

**Inject**：`马上去开三矿`

**Expected**：
- type: `expansion_override`
- must_have: `payload.target_count`=3

**Flash (retry=1)**：✓ 3/3 (avg 2333 ms)
- trial 1 PASS (2032 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 2 PASS (2390 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 3 PASS (2578 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3) | tactical_objective(verb="expand", target_area="own_third", done_when={kind='expansion_count', op='>=', value=3})

**Pro (retry=1)**：✓ 3/3 (avg 6609 ms)
- trial 1 PASS (6313 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 2 PASS (6640 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 3 PASS (6875 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
