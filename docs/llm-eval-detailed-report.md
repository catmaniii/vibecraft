# LLM 指令解析详细测试报告

**测试时间**：自动生成


## 汇总

| 配置 | Accuracy | 平均耗时 |
|---|---|---|
| **Flash (retry=1)** | 41/42 = **97.6%** | **2688 ms** |
| **Pro (retry=1)** | 41/42 = **97.6%** | **7243 ms** |

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
| L3a_unit_claim_persistent | `探机巡逻自然别动` | ✓ 3/3 | ~ 2/3 |
| L3b_unit_claim_ephemeral | `让那个探机移动到气矿` | ✓ 3/3 | ✓ 3/3 |
| L3c_scout | `派探机侦察 11 点` | ✓ 3/3 | ✓ 3/3 |
| L3d_engagement_hold | `所有人原地待命别动` | ✓ 3/3 | ✓ 3/3 |
| L4a_production_override_count | `下个 BG 出俩哨兵` | ~ 2/3 | ✓ 3/3 |
| L4b_tech_override | `先研闪烁` | ✓ 3/3 | ✓ 3/3 |
| L4c_expansion_override | `马上去开三矿` | ✓ 3/3 | ✓ 3/3 |

## 每 case 详细数据

### L1a_strategy_set

**Inject**：`切叉球一波`

**Expected**：
- type: `strategy_set`
- must_have: `payload.stage`='midgame', `payload.strategy_id`='iac_2base'

**Flash (retry=1)**：✓ 3/3 (avg 2380 ms)
- trial 1 PASS (2031 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 2 PASS (2375 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 3 PASS (2735 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")

**Pro (retry=1)**：✓ 3/3 (avg 6599 ms)
- trial 1 PASS (6203 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 2 PASS (7078 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")
- trial 3 PASS (6516 ms)
  - outcome: ✓ IntentParseResult: strategy_set(stage="midgame", strategy_id="iac_2base")

### L1b_strategy_cancel

**Inject**：`取消所有剧本`

**Expected**：
- type: `strategy_cancel`
- must_have: `payload.stage`='all'

**Flash (retry=1)**：✓ 3/3 (avg 2380 ms)
- trial 1 PASS (2844 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 2 PASS (2250 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 3 PASS (2047 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")

**Pro (retry=1)**：✓ 3/3 (avg 6323 ms)
- trial 1 PASS (6656 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 2 PASS (5906 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")
- trial 3 PASS (6406 ms)
  - outcome: ✓ IntentParseResult: strategy_cancel(stage="all")

### L2a_tactical_attack

**Inject**：`进攻对方自然`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='attack', `payload.target_area`='enemy_natural'

**Flash (retry=1)**：✓ 3/3 (avg 2484 ms)
- trial 1 PASS (2922 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 2 PASS (2312 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 3 PASS (2219 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})

**Pro (retry=1)**：✓ 3/3 (avg 8761 ms)
- trial 1 PASS (9391 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 2 PASS (8641 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})
- trial 3 PASS (8250 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="attack", target_area="enemy_natural", done_when={kind='any_of', conditions=[{'kind': 'target_destroyed', 'target_kind': 'natural'}, {'kind': 'own_army_size_ratio', 'op': '<=', 'value': 0.3}]})

### L2b_tactical_scout_vision

**Inject**：`在对方主基地保持视野`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`=['vision', 'scout'], `payload.target_area`='enemy_main'

**Flash (retry=1)**：✓ 3/3 (avg 2312 ms)
- trial 1 PASS (2218 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 2 PASS (2250 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 3 PASS (2469 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})

**Pro (retry=1)**：✓ 3/3 (avg 7625 ms)
- trial 1 PASS (8062 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 2 PASS (7344 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})
- trial 3 PASS (7469 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="vision", target_area="enemy_main", done_when={kind='vision_acquired', area='enemy_main', hold_seconds=5.0})

### L2c_tactical_harass_killed

**Inject**：`凤凰打死对方 5 个农民就回`

**Expected**：
- type: `tactical_objective`
- must_have: `payload.verb`='harass'

**Flash (retry=1)**：✓ 3/3 (avg 3073 ms)
- trial 1 PASS (2641 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 2 PASS (3359 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'}) | unit_claim(selector={unit_type='Phoenix'}, task={primary_action={'verb': 'harass_workers', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main'}}, reactions=[], role_hint='none'}, done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'}, persistent=True)
- trial 3 PASS (3219 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})

**Pro (retry=1)**：✓ 3/3 (avg 8328 ms)
- trial 1 PASS (8438 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 2 PASS (8485 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})
- trial 3 PASS (8062 ms)
  - outcome: ✓ IntentParseResult: tactical_objective(verb="harass", target_area="enemy_main", done_when={kind='enemy_killed_in_area', area='enemy_main', unit_type='Probe'})

### L2d_engagement_defend

**Inject**：`守家别出门`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='defend'
- forbidden: `payload.stance` ∉ ['hold_position', '守家', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 2703 ms)
- trial 1 PASS (3000 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend") | strategy_cancel(stage="all")
- trial 2 PASS (2110 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 3 PASS (3000 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend") | engagement_constraint(stance="hold")

**Pro (retry=1)**：✓ 3/3 (avg 5932 ms)
- trial 1 PASS (6078 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 2 PASS (5687 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")
- trial 3 PASS (6031 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="defend")

### L2e_engagement_retreat_timer

**Inject**：`30 秒后撤`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='retreat'

**Flash (retry=1)**：✓ 3/3 (avg 2260 ms)
- trial 1 PASS (1906 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 2 PASS (2188 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 3 PASS (2687 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat") | tactical_objective(verb="retreat", target_area="own_main", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})

**Pro (retry=1)**：✓ 3/3 (avg 6896 ms)
- trial 1 PASS (6938 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 2 PASS (6703 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})
- trial 3 PASS (7047 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="retreat", done_when={kind='time_elapsed_since', seconds=30.0, ref='directive_issued'})

### L3a_unit_claim_persistent

**Inject**：`探机巡逻自然别动`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.persistent`=True, `payload.task.primary_action.verb`=['patrol', 'hold_position', 'guard_position']

**Flash (retry=1)**：✓ 3/3 (avg 4193 ms)
- trial 1 PASS (3094 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 2 PASS (7140 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'hold_position', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 3 PASS (2344 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)

**Pro (retry=1)**：~ 2/3 (avg 9714 ms)
- trial 1 **FAIL** (13687 ms)
  - outcome: ⚠️ AmbiguousParse confidence=0.45 interp=让一个探机在二矿（natural）区域巡逻并保持不动（但"巡逻"和"别动"语义冲突——巡逻是移动的，"别动"是静止的）。按字面理解为：派探机去 natural 
  - reason: AmbiguousParse: 让一个探机在二矿（natural）区域巡逻并保持不动（但"巡逻"和"别动"语义冲突——巡逻是移动的，"别动"是静止的）。按字面理解为：派探机去 natural 巡逻（persistent standing order）。如果您的本意是"探机守 natural 别动"，请重新说"探机守自然别动"。
- trial 2 PASS (7766 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)
- trial 3 PASS (7688 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'patrol', 'target': {'kind': 'named_spot', 'named_spot': 'natural'}}, reactions=[], role_hint='none'}, persistent=True)

### L3b_unit_claim_ephemeral

**Inject**：`让那个探机移动到气矿`

**Expected**：
- type: `unit_claim`
- must_have: `payload.selector.unit_type`='Probe', `payload.task.primary_action.verb`='move_to', `payload.persistent`=False
- forbidden: `payload.task.primary_action.verb` ∉ ['scout', 'move', 'gather', 'guard']

**Flash (retry=1)**：✓ 3/3 (avg 2401 ms)
- trial 1 PASS (2250 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 2 PASS (2719 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 3 PASS (2234 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)

**Pro (retry=1)**：✓ 3/3 (avg 7380 ms)
- trial 1 PASS (7860 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 2 PASS (7062 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)
- trial 3 PASS (7219 ms)
  - outcome: ✓ IntentParseResult: unit_claim(selector={unit_type='Probe'}, task={primary_action={'verb': 'move_to', 'target': {'kind': 'named_spot', 'named_spot': 'enemy_main_gas'}}, reactions=[], role_hint='none'}, persistent=False)

### L3c_scout

**Inject**：`派探机侦察 11 点`

**Expected**：
- type: `scout`
- must_have: `payload.selector.unit_type`='Probe'

**Flash (retry=1)**：✓ 3/3 (avg 2161 ms)
- trial 1 PASS (2125 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 2 PASS (2156 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 3 PASS (2203 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})

**Pro (retry=1)**：✓ 3/3 (avg 6609 ms)
- trial 1 PASS (6375 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 2 PASS (6437 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})
- trial 3 PASS (7015 ms)
  - outcome: ✓ IntentParseResult: scout(selector={unit_type='Probe'}, target={kind='named_spot', named_spot='11_oclock'})

### L3d_engagement_hold

**Inject**：`所有人原地待命别动`

**Expected**：
- type: `engagement_constraint`
- must_have: `payload.stance`='hold'
- forbidden: `payload.stance` ∉ ['hold_position', 'guard', 'defend']

**Flash (retry=1)**：✓ 3/3 (avg 2067 ms)
- trial 1 PASS (1937 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 2 PASS (2109 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 3 PASS (2156 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")

**Pro (retry=1)**：✓ 3/3 (avg 6011 ms)
- trial 1 PASS (6219 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 2 PASS (5828 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")
- trial 3 PASS (5985 ms)
  - outcome: ✓ IntentParseResult: engagement_constraint(stance="hold")

### L4a_production_override_count

**Inject**：`下个 BG 出俩哨兵`

**Expected**：
- type: `production_override`
- must_have: `payload.unit_type`='Sentry', `payload.count`=2

**Flash (retry=1)**：~ 2/3 (avg 4021 ms)
- trial 1 PASS (2422 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 2 PASS (2078 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 3 **FAIL** (7563 ms)
  - outcome: ❌ ParseError(directive_invalid): 第 1 条 directive 非法：1 validation error for Directive
payload.build_at.point
  Input should be a valid tuple [type=tuple_type, input_value=None, input_type=NoneType]
    For further information visit ht
  - reason: ParseError: 第 1 条 directive 非法：1 validation error for Directive
payload.build_at.point
  Input should be a valid tuple [type=tuple_type, input_value=None, input_type=NoneTy

**Pro (retry=1)**：✓ 3/3 (avg 7490 ms)
- trial 1 PASS (7594 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 2 PASS (7906 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})
- trial 3 PASS (6969 ms)
  - outcome: ✓ IntentParseResult: production_override(unit_type="Sentry", count=2, done_when={kind='unit_count_built_since', unit_type='Sentry', op='>='})

### L4b_tech_override

**Inject**：`先研闪烁`

**Expected**：
- type: `tech_override`
- must_have: `payload.upgrade_id`=['BlinkTech', 'Blink', 'BLINKTECH']

**Flash (retry=1)**：✓ 3/3 (avg 2458 ms)
- trial 1 PASS (2500 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 2 PASS (2485 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink")
- trial 3 PASS (2390 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink") | engagement_constraint(stance="defend")

**Pro (retry=1)**：✓ 3/3 (avg 6765 ms)
- trial 1 PASS (6609 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='BlinkTech'})
- trial 2 PASS (7281 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='Blink'})
- trial 3 PASS (6406 ms)
  - outcome: ✓ IntentParseResult: tech_override(upgrade_id="Blink", done_when={kind='tech_done', upgrade_id='Blink'})

### L4c_expansion_override

**Inject**：`马上去开三矿`

**Expected**：
- type: `expansion_override`
- must_have: `payload.target_count`=3

**Flash (retry=1)**：✓ 3/3 (avg 2729 ms)
- trial 1 PASS (2860 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3)
- trial 2 PASS (2015 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3)
- trial 3 PASS (3313 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3) | tactical_objective(verb="expand", target_area="third_base", done_when={kind='expansion_count', op='>=', value=3})

**Pro (retry=1)**：✓ 3/3 (avg 6974 ms)
- trial 1 PASS (6891 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 2 PASS (7250 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
- trial 3 PASS (6781 ms)
  - outcome: ✓ IntentParseResult: expansion_override(target_count=3, done_when={kind='expansion_count', op='>=', value=3})
