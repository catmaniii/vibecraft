# Player Override Acceptance Runbook

> Task #311 player override e2e 测试 framework。验证玩家在游戏中按"全军撤退/
> 进攻/防守"等 UI 战术按钮真的让单位服从，不只是 UI 显示。

## 何时跑

- 改动玩家覆盖 path: UI 战术按钮 / `VibeCraftZoneAttack` / `combat_intent_override`
  / `attack_mode_override` / 任何 `_should_attack` / `_should_retreat` 相关
- 新增种族 plan 或新 plan: 防 bug 12 类回归（某 plan 用 sharpy 原生
  `PlanZoneAttack` 不接玩家覆盖；见 `tests/unit/test_attack_class_audit.py`）
- task #310 重构 `CombatIntentManager` 后

## 何时**不**跑

- 单测能 catch 的: `tests/unit/test_director.py::TestScheduledPlayerAction` 验
  director 触发机制 / `test_attack_class_audit.py` 防原生 `PlanZoneAttack`
- bot 默认行为改动（用 `build_acceptance.py` 验）
- LLM prompt / 解析改动（用 `voice_spot_check.py` 验）

## 命令

```bash
# 单 case (跑一局 ~5 min)
.venv/Scripts/python.exe scripts/override_acceptance.py 4bg__retreat \
  --opponent veryeasy

# 8 case 并行 4 (~30 min wall-clock)
.venv/Scripts/python.exe scripts/override_acceptance.py \
  4bg__retreat macro_hatch__retreat bio_stim__retreat \
  1g_robo_immortal__attack_all_in roach_hydra__attack_all_in \
  two_base_tanks__attack_probe \
  phoenix_2base__defend roach_ravager__defend \
  --opponent veryeasy --parallel 4
```

报告写到 `logs/override_acceptance/<case>_<opponent>_<ts>.txt`，每局 telemetry
在 `logs/game_<game_id>/telemetry.jsonl`。

## Spec 格式

`tests/override_acceptance/<case_id>.yaml`，复用 `AcceptanceSpec` schema，加两块:

```yaml
strategy_id: 4bg          # 跟 build_acceptance 同
my_race: Protoss

# 必填:玩家时间线
player_actions:
  - at: "5:00"            # M:SS,子进程 game_time 到此 fire
    verb: retreat         # attack|defend|retreat|vision
    mode: null            # attack 时可填 all_in|probe;其他 verb 用 null
    target_area: null     # 命名锚点(home/enemy_main/...);null=facade 默认

# 验收
checks:
  - id: army_back_home_after_retreat
    type: army_after_player_action
    action_idx: 0         # 关联到 player_actions[0]
    after_s: 30           # action 触发后等几秒查 army_center
    near: home            # 锚点 home/enemy_main/natural/enemy_natural
    within: 30.0          # 距锚点容差
    op: "<="              # </<=/>/>=/==/!= (默认 <=)
```

## 调参法则

**at 选哪**:
- retreat: build_acceptance 里 `attack_moveout` 的实际 timing 之后 0-30s
  (此时部队刚出门或在前线,验"按钮真撤")
- attack with all_in: bot 默认 attack 时机之前 1-2 min (验"绕过 power
  check 强制出门")
- attack with probe: bot 默认 attack 时机之前 1-2 min (验"兵足时 attack
  但劣势会撤")
- defend: 出门时机之后 (验"切回守家")

**after_s 选多大**:
- retreat: ≥30s — sharpy 内部 `RETREAT_TIME=20s`,撤完一波 sharpy 自动
  stop_retreat,需要给单位 idle 后回 gather_point 的时间
- attack: 60-120s (单位行军到 enemy_main 要时间;远地图 ~30s/100格)
- defend: 30-60s (单位回家比 retreat 更直接)

**within 选多宽**:
- retreat 后 `near: home` → 50-80(部队完全回到主基地+主矿圈)
- retreat 后 `near: enemy_main, op: ">"` → 35-50(单位至少离开敌方主矿圈)
- attack 后 `near: enemy_main` → 60(部队推进到敌方主矿区)
- probe attack: within 100(probe 模式会自动评估劣势撤回,可能没真到 enemy_main)
- defend 后 `near: home` → 40-60(回家防守圈)

**game 长度限制**:
- VeryEasy 4bg ~5-6 min 就 victory,fire at 4:30 + after_s 90 = 6:00 可能
  game 已结束(verifier 查不到 snapshot → FAIL detail "无 snapshot 数据")
- 短 build (4bg / bio_stim / roach_ravager): 给紧的 after_s
- 中后期 build (1g_robo_immortal / roach_hydra / two_base_tanks): 充足 after_s

## 调试

诊断"check FAIL 究竟是 bug 还是 yaml timing 不对":

1. **看 directives.jsonl**:确认 fire 真发生
   ```bash
   cat logs/game_<id>/directives.jsonl | grep "e2e scheduled"
   ```

2. **看 events.jsonl**:确认 directive.committed
   ```bash
   cat logs/game_<id>/events.jsonl | grep directive.committed
   ```

3. **跑日志里抓 VibeCraftZoneAttack intent change**(诊断 plan 接收 retreat
   是否成功):
   ```bash
   .venv/Scripts/python.exe scripts/override_acceptance.py 4bg__retreat \
     --opponent veryeasy 2>&1 | grep "intent change\|e2e_player_action_fired"
   ```
   期望看到:
   ```
   e2e_player_action_fired idx=0 verb=retreat ...
   VibeCraftZoneAttack intent change: None → retreat
   VibeCraftZoneAttack _should_retreat → Retreat (intent=retreat)
   ```

4. **看 telemetry army_center 轨迹**:
   ```python
   import json
   p = "logs/game_<id>/telemetry.jsonl"
   for r in [json.loads(l) for l in open(p).readlines()]:
       if r.get("kind") == "snapshot" and 270 <= r.get("t", 0) <= 330:
           print(r["t"], r.get("army_center"))
   ```
   如果 fire 后 30s army 几乎不动,可能是 sharpy retreat / PlanZoneGather
   行为问题(retreat→stop 后单位 stuck),非 yaml 错。

## 已知 baseline (2026-05-27 方案 D 完成后 9/9 多数票 PASS)

| case | result | 备注 |
|---|---|---|
| 4bg__retreat | PASS | 距 home 19.4-26.3 vs <=30 (5/26 expected FAIL → T10 vendor execute retreat 分支 home target override 修复) |
| macro_hatch__retreat | PASS (--runs 3 majority 2/3) | 距 home 26.7 (game variance ±2 在 30 boundary 反复,跑 --runs 3 稳定) |
| bio_stim__retreat | PASS | 距 home 24.3 vs <=35 (5/27 after_s 30→45 物理校准,army 出门深度 game variance 67-81) |
| 1g_robo_immortal__attack_all_in | PASS | 距 enemy_main 17.5-25.6 vs <=60 |
| roach_hydra__attack_all_in | PASS | 距 enemy_main 36.5-58.6 vs <=60 |
| two_base_tanks__attack_probe | PASS | 距 enemy_main 21.1-29.7 vs <=100 |
| phoenix_2base__defend | PASS | 距 home 20.0-23.6 vs <=50 |
| roach_ravager__defend | PASS | 距 home 31.0-32.4 vs <=40 |
| **dt_drop_iac__retreat_during_drop** | PASS | 距 home 27.7 vs <=50 (cross-plan layer-1/2 分工验证:主力撤回家,Reserved DT/棱镜继续 drop micro) |

## Cross-plan 验证 (2026-05-27)

dt_drop_iac__retreat_during_drop 验证 layer-1 (vibecraft 玩家覆盖) vs layer-2
(Reserved 单位自定义 micro) 分工 design contract:

- **主力**(stalker/immortal/sentry,非 Reserved)→ vendor PlanZoneAttack
  retreat 拉回 home
- **Reserved 单位**(棱镜+DT,GenericDropAct/PrismWarpDropAct 独占 micro)→
  sharpy free_units 不含 Reserved,layer-1 retreat 不接管,继续 drop micro

实测 fire @ 8:30 + 60s 距 home 27.7 ≤ 50 PASS,加权 army_center 主要反映
主力位置(主力数量 > Reserved,加权主导)。验证 vendor + audit 设计的 layer
分工真生效。

**未 cover 的 cross-plan 场景**(follow-up):
- phoenix_2base 凤凰骚扰中 retreat:主力 stalker 数量小 (4) → army_center
  game variance 噪音大,需新 check type 严格验 "Reserved 凤凰在 enemy 不被
  layer-1 干扰"(加 framework `reserved_units_at_enemy` check)
- mutalisk_harass:全部飞龙 Reserved,无主力,普通 army_center check 不适用
- 4bg auto_switch 后 retreat:VeryEasy 5-7 min victory,plan switch 难触发,
  需 VeryHard wall clock 长
- sequential player_actions(attack → 60s 后 retreat):framework 已支持,
  低 priority 验证
- vision verb (attack_target_override):比 retreat 简单,留 follow-up

**单 case 边缘 FAIL 怎么办**:`macro_hatch__retreat` 距离稳定在 30±2,单跑容易跨
boundary。如果单跑 FAIL,跑 `--runs 3 --parallel 3` 看多数票(实测多数票稳定 PASS)。
其他 case 单跑稳定。

**bot 决策 game variance 范围**:army 出门深度 game-to-game ±10-15 距离常见
(VeryEasy 对手反应不同 → bot 推进时机不同)。spec after_s 要给足撤回物理时间,
经验值:出门 50 距离 → after_s=30 足;出门 80 距离 → after_s=45。

## 已知行为

- **sharpy retreat 默认 RETREAT_TIME=20s**:撤一波 sharpy 自动 `_stop_retreat`,
  status 重置 NotActive 后 plan 又 _start_attack。**2026-05-27 vendor hook 修复**:
  `combat_intent_override="retreat"` 时 `_stop_retreat` 直接 return 不重置 status,
  retreat 持续到玩家清 intent。
- **vendor retreat 分支 target 写死 home**(2026-05-27 T10):intent="retreat" 时
  `combat.execute(...)` target 用 `self.ai.start_location` 不读 dynamic gather_point,
  防御 vibecraft 自定义 act(ForwardRallyStalker 等)改 gather_point 偷换 retreat
  target。详见 `docs/sharpy-patches.md`。
- **plan auto_switch 后继承**:bot 完成 opening (`opening_completed`) 后
  auto_switch 到 lategame doctrine plan,新 plan 用 sharpy 原生 `PlanZoneAttack`
  (T3 撤回了 VibeCraftZoneAttack swap,改 vendor 接管) 仍读
  `knowledge.vibecraft.combat_intent_override` —— 玩家覆盖 cross-plan 自动生效。
- **BlinkStalkerAct 类 Reserved 单位**:blink_harass 等 plan 把闪追 set
  Reserved(LLM_CONTROLLED),sharpy `PlanZoneAttack/Defense/Gather` 看 free_units
  不含 Reserved → 不接管。Reserved 是 layer-2 micro 独占,玩家 layer-1 retreat
  不覆盖(刻意分工)。

## 与 build_acceptance 的关系

| 维度 | build_acceptance | override_acceptance |
|---|---|---|
| 目标 | bot 自主行为符合 build 设计 | 玩家覆盖按钮单位真服从 |
| spec | tests/build_acceptance/<sid>.yaml | tests/override_acceptance/<case>.yaml |
| 触发玩家动作 | 无 | spec.player_actions Director 到点 fire |
| check 类型 | building_complete/unit_count/attack_moveout/... | army_after_player_action |
| 何时跑 | bot 默认行为改动后 | 玩家覆盖 path 改动后 |

## 与单测的关系

| 单测 | 验什么 |
|---|---|
| `test_director.py::TestScheduledPlayerAction` | Director.on_tick 到点 fire + 写 facade |
| `test_build_acceptance_spec.py::test_player_action_parse` | spec yaml 解析 |
| `test_build_acceptance_verifier.py::test_army_after_player_action_*` | verifier 算距离 + op 比较 |
| `test_attack_class_audit.py` | 所有 plan 用 VibeCraftZoneAttack 不是原生 PlanZoneAttack |

单测保证 framework / 数据流;override_acceptance 保证 SC2 内真实行为。
