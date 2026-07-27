# vendor/sharpy 改动记录

> vibecraft 在 sharpy 的 combat plan 里加了玩家覆盖（player override）hook，
> 让玩家 UI 战术按钮（retreat/defend/attack/hold/vision）真的覆盖 sharpy 默认决策。
> 2026-05-26 决策：vendor/sharpy 已 fork（非 git submodule），直接改源码 +
> 用 `# vibecraft:` marker 标记，是最 explicit + 调试自然的方式（方案 D）。

---

## 背景：为什么需要这些 hook

sharpy 的 `PlanZoneAttack` 用内部 power 启发式决定进攻 / 撤退。玩家按"全军撤退"
UI 按钮后，Director 通过 `SharpyFacade` 把 `combat_intent_override="retreat"` 写入
`knowledge.vibecraft` namespace，但 sharpy 原生逻辑感知不到这个字段 → 会出现：

- 玩家按"撤退"，sharpy 发现 power 够大，照常进攻
- sharpy 自身 `RETREAT_TIME=20s` 到期后 `_stop_retreat` 自动清 Retreat 状态 → 重新进攻
- `PlanFinishEnemy` 绕过 roles 系统直接 `unit.attack(target)`，把 idle 单位派出去

hook 的职责是在 **派单位 call site 之前** 读 `knowledge.vibecraft` 字段，让玩家意图
真正生效。

对应的上层包装类是 `src/vibecraft/bot/auto_combat/vibecraft_zone_attack.py`
中的 `VibeCraftZoneAttack`（继承自打了 patch 的 `PlanZoneAttack`）。

---

## 改动清单

### 1. `vendor/sharpy/sharpy/plans/tactics/zone_attack.py`

**类**：`PlanZoneAttack`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| 模块顶部 | 加 module-level `logger = logging.getLogger(__name__)` | — |
| `__init__` | 加 `self.force_attack: bool = False` 和 `self._logged_intent = "__sentinel__"` instance state | — |
| `_should_attack` | intent=attack → 跳过 power 检查直接 True（all_in/None）或走 sharpy 逻辑（probe）；intent=defend/hold/retreat/vision → 直接 False；intent=None 时检查 stance_override；最后再看 force_attack | `combat_intent_override` + `attack_mode_override` + `stance_override` + `force_attack` |
| `_should_retreat` | intent in {retreat,defend,hold} → 强制 Retreat，优先于 force_attack；intent 变化时 log 一次；mode=probe → 透传 sharpy（**豁免下方滞回，对等就退**）；mode=all_in 或 force_attack=True → NotActive；**own_local_power 计入 fight_center 30 格内正在赶来的 Moving 援军**（2026-06-02 skytoss 抖动修复，见下）；**2026-06-17 撤退滞回**：非 probe 实攻时,兵力撤退条件需**持续 ≥ `RETREAT_HYSTERESIS_S`(2.5 游戏秒)**才真退,瞬时散开抖动不退（修 bio 大军接敌散开→局部兵力瞬掉→进攻/撤退振荡“原地拉扯无法前进”，真局 32 attack/25 retreat）；时间戳 `_retreat_pending_since` 在 `_start_attack` 清零(每 episode 从干净计时)、条件不成立清零 | `combat_intent_override` + `attack_mode_override` + `force_attack` |
| `_stop_retreat` | intent="retreat" 时提前 return，不清 status / attack_retreat_started（阻止 sharpy RETREAT_TIME=20s 自动 stop 后重新进攻） | `combat_intent_override` |
| `_get_target` | 先读 `attack_target_override`，为 tuple 时转 Point2；为 Point2 直接返回；无覆盖走原逻辑 | `attack_target_override` |
| `execute`（retreat 分支, Issue 2 fix） | intent="attack" 时立即 reset retreat 状态（status=NotActive, attack_retreat_started=None, roles.attack_ended()）并 return False；intent="retreat" 时 retreat target 写死 home（`self.ai.start_location`），不读 dynamic gather_point；at_gather_point 距离判断也用 retreat_target；**intent="defend" 时优先调 `_vbc_defend_target()` 取威胁 zone center** | `combat_intent_override` |
| `execute`（else 分支, 零兵守卫, 2026-06-06） | 收集 free_units attackers 时用普通计数 `attacker_count`；`attacker_count==0` 时**不** `_start_attack`（即使 `_should_attack` 因 intent=attack/all_in/supply>190 返 True）| —（不读 vibecraft 字段，但属同 method 的 vibecraft patch）|
| `_vbc_defend_target` | defend intent 的撤退目标威胁感知版：遍历 `zone_manager.expansion_zones` 中 `is_ours`，取 `assaulting_enemy_power.power` 最大的 zone center；滞回同 `_vbc_threatened_zone`；无威胁 → None（回落到 hold_gather_point 或 **`_vbc_forward_defense_point`**）。2026-06-13 加。 | (zone 数据) |
| `_vbc_forward_defense_point` | **2026-06-17 新增**：无威胁 defend 的默认守点 = 距敌主基(`expansion_zones[-1]`)最近的己方 zone center(最前沿基地)。与 `PlanZoneGather` 同名方法一致，让主力部队和 idle 单位"无敌"时聚到同一最前沿点。**原 execute defend 分支 fallback 用 `gather_point_solver.gather_point`(natural rally)→ 主力守在 natural 而非最前沿、且与 gather 路径不一致 → 改成本方法**(用户"无敌→守最靠近敌方的己方基地")。min 按距离确定性，无己方分矿兜底 `start_location`。 | (zone 数据,非 vibecraft 字段) |

**marker 格式**：每处改动以 `# vibecraft: <说明>` 一行注释标记，共 14 处（+1: 2026-06-17 `_vbc_forward_defense_point` + execute defend fallback 改 forward）。

**修复的 bug**：
- bug 12（4bg__retreat）：玩家按"全军撤退"后，sharpy 20s 后 `_stop_retreat` 清状态，
  blink_harass 新单位被 `_start_attack` 重新派出 → 振荡。`_stop_retreat` hook 阻止此路径。
- Issue 2（retreat→attack 转换）：玩家 retreat→attack 时，`attack_retreat_started` 非 None
  导致 execute() 永远在 retreat 分支循环，最多等 20s RETREAT_TIME 才 exit。新 hook 在
  retreat 分支头检测 intent=attack 并立即 reset，下 tick 走 else 分支重新 `_should_attack`。
- 零兵 flip-flop（2026-06-06 虚空 dancing）：玩家把全军编队/claim 成 Reserved 后 free_units
  空，但全局 intent=attack/all_in（或 supply>190）让 `_should_attack` 仍返 True → `_start_attack`(0 兵)
  → 下 tick `handle_attack` "No attacking units" → retreat → 又 attack 的 1Hz 空转，把被 claim
  的单位也搅得抖动 + debug 线乱跳。else 分支 `attacker_count==0` 守卫断此循环。配套 Director 侧:
  "回家防守/撤退"（standby→home）顺手清掉过期全局 attack 意图（`_clear_global_attack_on_pullback`）。

**execute() retreat 分支 hook（T10，2026-05-27）的设计动机**：

之前 `_should_retreat` hook 正确触发 retreat status，但 retreat 分支调
`combat.execute(gather_point_solver.gather_point, DefensiveRetreat)` 把单位拉
到 **dynamic** gather_point。vibecraft 自定义 act（如 ForwardRallyStalker）
每 tick 把 gather_point 改成 forward pylon（敌方一侧），retreat target 被
偷换，单位"撤"到敌方前线 — 实测 4bg__retreat 距 home 95.8 vs ≤30 FAIL。

T10 在 vendor 集中改：intent="retreat" 时 retreat target 写死
`self.ai.start_location`，完全不读 dynamic gather_point。这是方案 D
"集中 vendor 改"精神的体现：一处 vendor 改防御 N 个 vibecraft 自定义 act
改 gather_point，避免 O(acts) 的脆弱性（每加新 act 都要记得加 intent gate）。

**`_should_retreat` own_power 计入 Moving 援军（T?，2026-06-02 skytoss 抖动修复）**：

后期混速空军（虚空 3.5 > 航母/母舰/风暴 2.62）进攻时，`handle_attack` 把离群
>20 的单位标成 `UnitTask.Moving`（不算 `Attacking`）。原 `_should_retreat` 算
`own_local_power` 只用前排 `already_attacking` → 快速虚空冲前先接敌时只数到虚空 →
局部以少打多 → 触发撤退 → 慢速航母/母舰还没到 → 撤了又来恶性抖动（玩家观感：
航母/虚空/母舰严重脱节 + 不听"强制进攻"；切 all_in 关掉撤退才好）。

修复：own_power 计入 `fight_center` 30 格内正在赶来的 `Moving` 单位（马上到的援军，
不含满地图乱跑的）→ 大军原地顶住等主力到齐再打，而非前排先撤。根因诊断见
game_20260602_063425 server log（attack↔retreat 反复抖动）。

---

### 1b. `vendor/sharpy/sharpy/plans/acts/morph_building.py`（#582，2026-07-04，非 combat hook）

**类**：`MorphBuilding`（`MorphOrbitals` 的基类）

| Method | 改动摘要 | 动机 |
|---|---|---|
| `execute` | 升级循环里加 `if target.orders: continue` —— **只对空闲 building 尝试/预留升级**，正忙（造 SCV 等）则跳过、绝不 `reserve_costs`/`subtract_cost` | CC 造 SCV 时无法升轨道，若仍占 150 矿会把 priority 次序里排后面的科技链（工厂/星港）饿死 |

**marker**：`# vibecraft(#582): ...`，共 1 处。

**修复的 bug**：`MorphOrbitals(2)` 每帧对 ready 但正忙造 SCV 的 CC 尝试升轨道、升不了却每帧照占 150 矿 →
叠加其它预留把 bc_rush 工厂饿死（真机矿 float 到 445 才建工厂、晚 85s）。详见 `docs/pitfalls.md` 2026-07-04 条。
升级 checklist：升级 sharpy 后确认 `morph_building.py` 的 `if target.orders: continue` 仍在（grep `# vibecraft(#582)`）。

---

### 2. `vendor/sharpy/sharpy/managers/core/grids/build_grid.py`

**类**：`BuildGrid`

| Method | 改动摘要 | 动机 |
|---|---|---|
| `fill_line` | 矿区路径标记步数从固定 4 步改为 `max(4, int(total_dist) - 2)`，覆盖矿石到 nexus 的完整 probe 行走路径 | Issue #3：BG 建在 nexus 与矿线之间卡 probe |

**marker**：`# vibecraft: extend mineral-line exclusion to cover the full nexus→mineral probe path`，共 1 处。

**修复的 bug**：

- **Issue #3（BG 卡矿路径）**：原 `fill_line` 只向 nexus 标记 4 步（约 4 格），矿石到 nexus 通常 8-10 格。靠近 nexus 侧的空格仍为 `Empty`，被 `pylon_pair_*` / `massive_grid` 分配为建筑 slot，probe 建造后堵住 nexus→矿 通道。

**修复逻辑**：

```python
# 原（固定 4 步）
while i < 5: ...

# 修（走到距 nexus 2 格停，nexus 5x5 blocker 覆盖最后 2 格）
total_dist = neutral_unit.position.distance_to_point2(closest_expansion)
max_steps = max(4, int(total_dist) - 2)
while i <= max_steps: ...
```

---

### 3. `vendor/sharpy/sharpy/plans/tactics/attack_expansions.py`

**类**：`PlanFinishEnemy`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | intent in {retreat, defend, hold} → 提前 `return True`，不派单位 | `combat_intent_override` |

**marker 格式**：`# vibecraft: 玩家覆盖 intent 时不派单位 attack-move enemy expansions`，共 1 处。

**修复的 bug**：
- `PlanFinishEnemy.execute` 直接对 `self.ai.units.idle` 调 `unit.attack(target)`，
  完全绕过 sharpy roles 系统。玩家按"全军撤退"后，idle 单位（sharpy 撤退归位后
  变 idle）会被这里直接派去 attack-move，是 4bg__retreat bug 的关键路径之一。

### 5. `vendor/sharpy/sharpy/plans/acts/act_base.py`

**类**：`ActBase`

| Method | 改动摘要 | 动机 |
|---|---|---|
| `get_count` | 在 method 入口加 `_VBC_EQUIVALENTS` inline dict；原 `cache.own(unit_type)` 分支改为 sum over `types_to_count`；`related_count` 只在 unit_type 不在 `_VBC_EQUIVALENTS` 时调用（防双重计数） | Q3: `GridBuilding(GATEWAY, 4)` 在所有 GW 升 WG 后，`cache.own(GATEWAY)=0` → `count=0` → plan 重新触发造新 BG |

**marker**：`# vibecraft: Gateway/Warpgate/Hatchery/Lair/Hive/CC/OC/PF/Spire/GreaterSpire 同质化`，共 1 处。

**修复的 bug**：

- **Q3（BG 重复触发）**：`GridBuilding` act 调 `get_count(GATEWAY, include_pending=False, include_not_ready=True)`。全部 GW 升级为 WG 后，`cache.own(GATEWAY).amount=0`，`related_count` 虽然加了 WG 数量（通过现有 `EQUIVALENTS_FOR_TECH_PROGRESS`），但在 `include_pending=True` 分支里 `unit_pending_count(GATEWAY)=0` 且 `type_count.ready.amount=0` 先累加，随后 `related_count` 加 WG.amount——结果虽然正确，但 WG morph 期间（WG not_ready 时）若 `include_pending=False` 路径走到此 type_count.amount=0，`related_count` 加 WG.amount=WG not_ready 数，此时 WG 尚在 morph 但已计数正确。实际 bug 路径：明确声明 `_VBC_EQUIVALENTS` 使逻辑 explicit，防止未来 sharpy 升级改动 `related_count` 导致退化。

**升级 checklist 追加**：sharpy upstream 改 `get_count` 签名或内部 `related_count` 逻辑 → 重新合入 `_VBC_EQUIVALENTS` dict + `if unit_type not in _VBC_EQUIVALENTS` guard。

---

### 4. `vendor/sharpy/sharpy/plans/tactics/zone_gather.py`

**类**：`PlanZoneGather`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | intent=retreat → effective gather_point 改 `start_location`;**intent=defend → 威胁感知优先(`_vbc_threatened_zone`) → 有 `hold_gather_point` 守该点 → 前沿分矿(`_vbc_forward_defense_point`)**;intent=hold → `hold_gather_point` 或 home | `combat_intent_override` / `hold_gather_point` |
| `_vbc_forward_defense_point` | 无目标 defend 的默认守点:遍历 `zone_manager.expansion_zones`,取 `is_ours` 中离 enemy main(`[-1]`)最近的 zone center;无己方分矿 → `start_location` 兜底 | (zone 数据,非 vibecraft 字段) |
| `_vbc_threatened_zone` | 威胁感知守点:遍历 `expansion_zones` 中 `is_ours` 的 zone，取 `assaulting_enemy_power.power > 3.0`（danger_radius 范围；阈值滤掉散兵游勇/侦察单位，2026-06-13 e2e 调优）最大的 zone center；滞回：旧 zone 仍有威胁时只有新 zone ≥ 1.5x 才切换；无威胁 → None | (zone 数据) |

**marker 格式**:`# vibecraft: 2026-05-27 玩家点全军撤退后...` / `# vibecraft: 2026-06-03 用户 — defend 不再缩回主基地...` / `# vibecraft: 2026-06-13 威胁感知守点...`,共 5 处。

**修复的 bug A**(2026-05-27 玩家反馈):
- 玩家点"全军撤退" → 已有部队归位 OK
- 新追猎从 Gateway spawn 后,自动按 RALLY_BUILDING 走到 natural 区域(sharpy
  默认 gather_point 是己方最前沿扩张)→ 玩家观感"新追猎还在前压"
- 修法:`PlanZoneGather.execute()` 入口读 vibecraft intent,retreat
  时把 effective gather point 改 `self.ai.start_location`。
  `current_gather_point != effective_gp` 触发 gather_set.clear() →
  下个 tick 对所有 Gateway/Robo 重新调 RALLY_BUILDING(指向 home)→
  新单位 rally 回家。

**修复的 bug B**(2026-06-03 玩家反馈"守右边瞭望塔部队却跑回主基地"):
- 原 `intent in {retreat, defend, hold}` 把 defend 和 retreat 一视同仁 →
  effective gather_point 写死 `start_location` → defend 永远回家,无视玩家
  指定的瞭望塔/分矿。
- 修法:defend 单独分支。有玩家指定点(Director 经 `set_hold_gather_point`
  写入 `hold_gather_point` = 瞭望塔/分矿) → 守该点;无指定 →
  `_vbc_forward_defense_point()` 挑离敌方主基地最近的己方分矿(前沿防守)。
- 配套:Director `_apply_l2_global` defend 分支 `set_hgp(point)`;
  zone_attack `execute` retreat 分支 attack→defend 切换时撤退目标也读
  `hold_gather_point`(没有则 gather_point,随后 PlanZoneGather 接管挪前沿)。

**修复的 bug C**(2026-06-13 玩家反馈"全军防守钉死三矿，二矿被打不响应"):
- 原 defend 无威胁感知 → 部队聚在静态前沿点(最近己方分矿或玩家指定点)，不响应
  敌军进攻其他己方基地。玩家在三矿防守时二矿被攻，部队不动。
- 修法:新增 `_vbc_threatened_zone()` — 遍历 `expansion_zones` 中 `is_ours` 的
  zone，用 `assaulting_enemy_power.power`（danger_radius 30/35 内的敌军 power）
  找到威胁最大的 zone，作为 effective_gp 最高优先级。有威胁就去迎，无威胁时
  回落到 hold_gather_point 或前沿点。
- 滞回防抖：旧 zone 仍有威胁时只有新 zone 强度 ≥ 旧 1.5x 才切换，避免敌军在
  两 zone 边界时聚团点反复跳动。

---

### 4b. `vendor/sharpy/sharpy/plans/tactics/zone_defense.py`（2026-06-17）

**类**：`PlanZoneDefense`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | **intent=="defend" 时不 claim/dispatch 主力**(非工人)：原"counting 既有 Defending + warp-in idle + `get_defenders` 补足"那段(把主力 set_task(Defending) + add_unit) 整段 gate 掉；改为①释放残留的非工人 Defending(`clear_tasks` + 从 `zone_tags` 移除非工人 tag)交还 PlanZoneGather，②把附近(zone.radius+10)free 主力的 power **只计入 `defenders` 不 claim**(让 worker 防守逻辑知道有兵守、不 panic 拉农民)。worker 防守块 + 末尾 `combat.execute(enemy_center)` 不变(defend 下 combat 组只剩被 worker_defence 拉的工人)。非 defend → 完全走原逻辑。 | `combat_intent_override` |

**marker 格式**：execute 内 1 处 `# vibecraft: 2026-06-17 玩家"全军防守"...` 块注释。

**修复的 bug（defend 大军"原地保持队形拉扯"）**：
- 现象（用户）：后期生化大军在"全军防守"下"出不了门，在家里原地拉扯，像保持队形那种"。
- 根因（真局复现 + 代码链）：defend intent 下两个 plan 同时管主力且目标不同 ——
  PlanZoneDefense 把主力 claim 成 Defending → 送 `enemy_center`(敌人**实际位置**，每帧重算的
  移动靶 + 敌散后 `ZONE_CLEAR_TIMEOUT=3s` release)；PlanZoneGather 把 release 回 Idle 的主力
  送 `effective_gp`(`_vbc_threatened_zone` 稳定锚点)。两目标不同 + role Idle↔Defending ~1Hz
  翻转 → 大军每秒换行进目标 → 原地横跳。真局 ARMYTRACE：effective_gp 稳定(CHG=2/67)但 role
  整支翻转(idle=24/defend=0 ↔ idle=0/defend=21)；确定性复现(60 枪兵+周期 flicker 敌)：army 中心
  x/y 方向反转 18/16、defend role 反复 `0→10→0→11→0→12`，且 enemy_center=SearchAndDestroy 追逃敌
  时大军一路追到敌方主基(defend 下不该跨图)。
- 修法（评审采纳的"单一收口"）：defend 下让主力**只受 PlanZoneGather 单一 plan、单一威胁感知
  锚点**(`_vbc_threatened_zone`，已含 power>3.0 阈值 + 1.5x 滞回 + 无威胁回前沿点)驱动 →
  根本不存在 Idle↔Defending 翻转 → 彻底消抖。敌入射程仍由 combat 引擎(GroupCombatManager
  按最近敌群交战，与 execute target 解耦)交战 → 守得住、不"站着挨打"。
- 设计 + 评审留痕：`docs/plans/2026-06-17-defend-tug-fix-design.md`。
- **升级 checklist**：sharpy upstream 升级覆盖 zone_defense.py 后，重新在 `execute` 的
  defenders 收集段(原 `for unit in zone_defenders` + warp-in + `get_defenders` 块)外面包
  `if vbc_defend: 释放非工人 Defending + 只计数 free 主力 power; else: <原逻辑>`，跑
  `test_sharpy_patch_audit.py` + `test_sharpy_vibecraft_hooks.py` 的 defend 用例确认。

---

### 6. `vendor/sharpy/sharpy/plans/acts/zerg/morph_units.py`

**类**：`MorphBaneling`（bug 修复，非 hook）

| 改动 | 摘要 |
|---|---|
| `MorphBaneling.__init__` 的 `ability_type` | `MORPHZERGLINGTOBANELING_BANELING` → `MORPHTOBANELING_BANELING` |

**marker**：`# vibecraft: 旧 ability ... 改用有效的 MORPHTOBANELING_BANELING`，共 1 处。

**修复的 bug**（2026-06-16 build 效率优化）：vendored `MorphBaneling` 用的旧 ability
`MORPHZERGLINGTOBANELING_BANELING` 在当前 SC2 游戏数据里对小狗已失效，引擎静默丢弃 →
小狗永不变蛹。ling_bane 开局 plan 早发现并自行 workaround（`_ForwardBanelingMorphUnit`
override `ability_type`），但 `ZergUnit(BANELING)` 直接用 `MorphBaneling` 时仍踩坑 →
build-aware sustain 的爆虫产量永远冻结在开局造的那几个（实测 ling_bane 卡 12）。
直接修根（vendored `MorphBaneling` 的 ability），使 `ZergUnit(BANELING)` /
build-aware sustain 能真正孵爆虫。回归测试 `tests/unit/test_sustain_core_units.py`
有一条断言 `MorphBaneling().ability_type == MORPHTOBANELING_BANELING`，sharpy 升级
若 revert 此修会被捕获。

---

### 7. `vendor/sharpy/sharpy/plans/tactics/distribute_workers.py`

**类**：`DistributeWorkers`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | 开头加采矿策略 hook：首帧缓存构造期 min/max_gas 原始值；每帧读 `mining_priority` 覆写 min/max_gas（两字段成对写防残留）；走完原 calc 逻辑 | `knowledge.vibecraft.mining_priority` |

**marker**：`# vibecraft: 2026-07-06 采矿策略 hook`，2 处（execute 开头 + MININGTRACE 日志行）。

**语义**（三种状态，每帧覆写）：
- `"mineral"`（优先水晶）：`max_gas = max(0, free_workers - mineral_ideal_total)`；`min_gas = None`。
  水晶填满 `ideal_harvesters` 后，多余农民才去采气。
- `"gas"`（优先气）：`min_gas = gas_buildings.ready.amount * 3`；`max_gas = None`。
  气井先采满（每井 3 农），剩下的才采水晶。
- `None`（默认）：恢复构造期缓存的 `_vc_orig_min_gas`/`_vc_orig_max_gas`
  （不写 None——否则砸掉剧本给的 `min_gas=6` 等）。

**升级 checklist**：sharpy upstream 升级覆盖 `distribute_workers.py::execute` 后，
在新 execute 开头重新添加首帧缓存 + mining_priority 读取 + min/max_gas 覆写块，
跑 `test_sharpy_patch_audit.py` + `test_sharpy_vibecraft_hooks.py` 的 mining 用例确认。

---

## sharpy upstream 升级 checklist

每次合并 sharpy upstream 时，按以下步骤操作：

**1. 找出有 marker 的代码块**

```bash
# 列出所有 vibecraft marker 位置
grep -rn "# vibecraft:" vendor/sharpy/
```

当前 marker 分布：
- `vendor/sharpy/sharpy/plans/tactics/zone_attack.py`：12 处（+`_vbc_defend_target` + execute defend 分支）
- `vendor/sharpy/sharpy/plans/tactics/attack_expansions.py`：1 处
- `vendor/sharpy/sharpy/plans/tactics/zone_gather.py`：5 处（`execute` defend/retreat/threatened 分支 + `_vbc_forward_defense_point` + `_vbc_threatened_zone` helper）
- `vendor/sharpy/sharpy/plans/tactics/distribute_workers.py`：2 处（§7，execute 采矿策略 hook + MININGTRACE 日志）
- `vendor/sharpy/sharpy/managers/core/grids/build_grid.py`：1 处
- `vendor/sharpy/sharpy/plans/acts/act_base.py`：1 处
- `vendor/sharpy/sharpy/combat/protoss/micro_hightemplars.py`：5 处（§6）
- `vendor/sharpy/sharpy/plans/acts/expand.py`：1 处（§9）
- `vendor/sharpy/sharpy/plans/acts/tech.py`：1 处（§14，execute 攻防升级封顶门）

**2. 对比新版 sharpy 的同文件**

```bash
# 查看 diff（新 upstream vs 当前 vendor）
git diff vendor/sharpy/ -- sharpy/plans/tactics/zone_attack.py
git diff vendor/sharpy/ -- sharpy/plans/tactics/attack_expansions.py
```

每个 marker 块须在新版里重新 apply（method 签名可能变，logic 可能要 adjust）。

**3. 跑 hook 行为单测**

```bash
uv run pytest tests/unit/test_sharpy_vibecraft_hooks.py -v
```

**4. 跑 marker 存在性审计（T6 会建）**

```bash
uv run pytest tests/unit/test_sharpy_patch_audit.py -v
```

**5. 跑 e2e 玩家覆盖验收**

```bash
# 最小验证（单 case，约 5 min）
.venv/Scripts/python.exe scripts/override_acceptance.py 4bg__retreat --opponent veryeasy

# 全量 8 case（约 30 min wall-clock）
.venv/Scripts/python.exe scripts/override_acceptance.py \
  4bg__retreat macro_hatch__retreat bio_stim__retreat \
  1g_robo_immortal__attack_all_in roach_hydra__attack_all_in \
  two_base_tanks__attack_probe \
  phoenix_2base__defend roach_ravager__defend \
  --opponent veryeasy --parallel 4
```

**6. 验证 vendor retreat 分支 home override 真生效**

```bash
.venv/Scripts/python.exe scripts/override_acceptance.py 4bg__retreat --opponent veryeasy
```

确认玩家按"全军撤退"后单位距 home ≤ 30 in 45s（`army_after_player_action` check PASS）。

**升级后若某处 marker 需要调整**：保留 `# vibecraft:` 注释 + 更新本文档"改动清单"中对应行的"改动摘要"。

---

## 加新 hook 的步骤

当发现某个 sharpy plan 在 `execute()` 内直接派单位（绕过玩家覆盖），需要加新 hook：

1. **改 vendor 文件**

   在派单位 call site 之前，读 `knowledge.vibecraft.combat_intent_override`：

   ```python
   # vibecraft: 玩家覆盖 intent 时不派单位
   intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
   if intent in ("retreat", "defend", "hold"):
       return True   # 或 return / break，视 method 语义
   ```

   加 `# vibecraft: <说明>` marker（一行注释）。

2. **加进 patch_audit 清单（T6 会建）**

   编辑 `tests/unit/test_sharpy_patch_audit.py`，在 `PATCHED_METHODS` 列表里加上：

   ```python
   ("sharpy.plans.tactics.<module>", "<ClassName>", "<method_name>"),
   ```

3. **更新本文档**

   在"改动清单"里加一节，填写类名、method、改动摘要、读取的字段。

4. **写 hook 行为单测**

   在 `tests/unit/test_sharpy_vibecraft_hooks.py` 加测试，覆盖：
   - intent=retreat/defend/hold → 不派单位
   - intent=None → 走原逻辑

5. **验证三个测试都绿 + e2e 真生效**

   ```bash
   uv run pytest tests/unit/test_sharpy_patch_audit.py tests/unit/test_sharpy_vibecraft_hooks.py -v
   .venv/Scripts/python.exe scripts/override_acceptance.py <relevant_case> --opponent veryeasy
   ```

---

## 判断是否需要 hook

| 场景 | 需要 hook？ | 原因 |
|---|---|---|
| `execute()` 内直接 `unit.attack(target)` 或 `unit.move(target)` | **是** | 绕过 roles 系统，玩家覆盖无效 |
| `execute()` 内只调 `roles.set_task(UnitTask.Attacking, unit)` | 通常不需要 | roles 系统会在下游 check intent |
| `PlanZoneGather.execute` rally Gateway → gather_point | **已 patch** | intent=retreat 时新单位仍朝前 rally(Gateway 默认 RALLY_BUILDING 到 natural)。intent in (retreat/defend/hold) 时 effective gather point 改 start_location |
| `PlanZoneAttack._should_attack` / `_should_retreat` | 已 patch，不要重复加 | 见本文档改动清单 §1 |
| `PlanFinishEnemy.execute` | 已 patch | 见本文档改动清单 §3 |
| `BuildGrid.fill_line`（矿区路径） | 已 patch | 见本文档改动清单 §2 |

---

## 设计原则：vibecraft 自定义 act vs vendor fix 的分工

| 场景 | 处理方式 |
|---|---|
| vibecraft 自定义 act 改 sharpy 全局状态（gather_point / zone state / 其他影响 sharpy 主流程的 mutable state） | **走 vendor fix**，在 vendor 用到这个 state 的地方加 intent override（参考 T10 案例） |
| vibecraft 自定义 act 自己的行为决策（如 ForwardWarpStalker 在 retreat 期间不在敌方一侧 spawn 新兵） | **act 自己 read intent**，这是 act 自己的 layer responsibility |
| vibecraft 自定义 act 操作 Reserved 单位（harass / drop / DT raid 等 layer-2 micro） | **不需要读 intent**，layer-1 retreat 不应覆盖 layer-2 micro |

---

## 相关文件索引

| 文件 | 说明 |
|---|---|
| `vendor/sharpy/sharpy/plans/tactics/zone_attack.py` | 本文档 §1 改动 |
| `vendor/sharpy/sharpy/managers/core/grids/build_grid.py` | 本文档 §2 改动（矿区路径 Issue #3） |
| `vendor/sharpy/sharpy/plans/tactics/attack_expansions.py` | 本文档 §3 改动 |
| `src/vibecraft/bot/auto_combat/vibecraft_zone_attack.py` | PlanZoneAttack 的 vibecraft 子类（上层包装） |
| `docs/override-acceptance-runbook.md` | e2e 验收 spec 格式 + 调参法则 |
| `tests/unit/test_sharpy_vibecraft_hooks.py` | hook 行为单测（T4 任务） |
| `tests/unit/test_sharpy_patch_audit.py` | marker 存在性审计（T6 任务） |
| `tests/unit/test_mineral_line_exclusion.py` | 矿区路径 Issue #3 patch 单测 |
| `tests/override_acceptance/*.yaml` | e2e 验收 case spec |

---

### 6. `vendor/sharpy/sharpy/combat/protoss/micro_hightemplars.py`

**类**：`MicroHighTemplars`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `unit_solve_combat` | 入口读 `knowledge.vibecraft.ht_safe_micro`：True 时转入 `_vbc_safe_unit_solve`；False 时走原始 sharpy 逻辑 | `ht_safe_micro` |
| `unit_solve_combat`（原始路径 Feedback 修复）| energy 阈值从 `> 74` 修正为 `>= 50`（Feedback 消耗 50 能量，50-74 段施法者不再漏掉） | — |
| `_vbc_safe_unit_solve`（新增 + 2026-05-29 修复） | 电兵安全 micro，修复 Feedback 不发问题：**(1) Feedback 优先**：9 格内有 energy>=50 的施法者 → 立即放 Feedback（不等撤退）；(2) 15 格内有普通战斗单位 → 后撤；(3) energy≥75 + 敌群密集 → 放 Storm；(4) 默认 → 跟随大部队 | — |

**2026-05-29 Feedback 修复说明**：

旧优先级：step1=近敌后撤 → step2=Storm → step3=Feedback。
**问题**：战斗中始终有敌在 15 格内 → step1 直接 return 后撤 → Feedback 永远不触发。

新优先级：step1=Feedback（spellcaster 在 9 格内即刻发）→ step2=近敌后撤 → step3=Storm → step4=随队。
**理由**：Feedback 是 9 格范围内的即发技能，施放后无论如何都会走下一帧后撤；放完再跑比先跑更划算。
普通战斗单位（Marine/Zealot/Roach 等 energy=0）不会触发 Feedback（filter: energy>=50）。

**marker 格式**：每处改动以 `# vibecraft:` 注释标记：
- 模块顶部常量 `_VBC_HT_FEEDBACK_RADIUS / _VBC_HT_FEEDBACK_MIN_ENERGY` 声明处
- `unit_solve_combat` 入口路由处 + 原始路径 Feedback 阈值修正处
- `_vbc_safe_unit_solve` 步骤 1/2/3/4 各 1 处

**设计原则**：
- 不改变 `ht_safe_micro=False` 时的行为（除 Feedback 阈值修正 `>74→>=50` 影响所有路径）
- 新逻辑通过 `_vbc_safe_unit_solve` 独立方法，和原始路径物理隔离

**激活机制**：
- `common_bot.py` 的 `knowledge.vibecraft` SimpleNamespace 初始化时默认 `ht_safe_micro=False`
- `ArchonAfterStorm.start(knowledge)` 在 iac_2base plan 激活时把 `ht_safe_micro` 设为 `True`
- 其他 plan 不使用 `ArchonAfterStorm`，不会触发激活 → 默认行为不变

**升级 checklist 追加**：sharpy upstream 改 `MicroHighTemplars.unit_solve_combat` 签名或 Storm/Feedback 逻辑 → 重新检查 `_vbc_safe_unit_solve` 步骤顺序 + `>=50` 阈值是否仍与 SC2 Feedback cost 一致（当前 cost=50 energy，SC2 patch 不会改）。

---

---

### 7. `vendor/sharpy/sharpy/combat/protoss/micro_zealots.py` —— 已移除（2026-06-02）

**状态**：~~MicroZealots 的 `zealot_hold_until_archon` patch~~ 已移除，文件回退到**原版 sharpy**。

用户 2026-06-02 决定去掉"放电期间维持队形（叉子等第 1 个白球才冲）"功能 —— 叉子立刻顶上去当肉盾保护电兵放电，电兵少死。一并移除：`_vbc_hold_unit_solve` 方法、`_VBC_ZEALOT_HOLD_RADIUS` 常量、`unit_solve_combat` 入口路由、`ArchonAfterStorm` 的 start/execute flag 管理、`common_bot.py` 的 `zealot_hold_until_archon` 初始化、`test_micro_zealot_hold.py`、patch audit 条目。`MicroZealots.unit_solve_combat` 现与 upstream 一致，无需 marker。

---

### 8. `vendor/sharpy/sharpy/plans/acts/act_unit.py` + `protoss/warp_unit.py` —— 产能封锁机制级拦截（2026-06-02）

**patched method**：`ActUnit.execute`、`WarpUnit.execute`（各在方法顶部加 `# vibecraft:` 拦截）。

**动机**：玩家"停止出追猎/叉子"产生 `production_block` directive，Director 把兵种名加入
`knowledge.vibecraft.production_blocked` set。原设计靠一个"每 tick 遍历 set 取消队列"的
`ProductionBlockAct` —— **但该 Act 从未实现**（只在注释里），所以封锁完全无效，bot 继续刷兵。

**修法（用户要求"机制级拦截"而非事后取消）**：在产兵 act 真正下训练/折跃指令**之前**检查
封锁集，命中就 `return True`（当作已满足，不下令、不阻塞 build order 后续步骤）。
- `ActUnit.execute`：覆盖兵营/机械/星门 train + ProtossUnit 非折跃路径 + Zerg/Terran 单位
  （都继承/走 ActUnit）。
- `WarpUnit.execute`：覆盖折跃路径（ProtossUnit 在折跃研究完成后委派给它）。

匹配大小写不敏感（封锁存 canonical 名 "Stalker"，act 的 `unit_type.name` 是 "STALKER"）。
行为测试见 `tests/unit/test_production_block_intercept.py`。

---

---

### 9. `vendor/sharpy/sharpy/plans/acts/expand.py` —— 玩家开矿封顶 + stealth 基地排除（2026-06-10）

**类**：`Expand`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | ① 计算 `active_bases` 后读 `expansion_cap_override`；不为 None 时排除 `stealth_townhall_tags` 中的基地计数；若非 stealth 基地数 `>= _cap` → `clear_worker()` + `return True`。② 紧接着把 `stealth_pending_base_count`（在建偷矿基地数）加进 `active_bases`，供后面 build 的 `active_bases >= to_count` 判断 → 玩家下了偷矿令、偷矿基地还在建时，bot 也当它是一片基地、延后开自己分矿 | `expansion_cap_override` / `stealth_townhall_tags` / `stealth_pending_base_count` |

**marker 格式**：`# vibecraft: 玩家开矿封顶 + stealth 基地不计入自然扩张账`（1 处）
+ `# vibecraft: 在建/待建偷矿基地也算进基地数`（1 处，紧接着，作用于 build 的 `Expand(to_count)` 判断）。

**在建偷矿算基地（2026-06-12 用户）**：`current_active_base_count = len(our_zones_with_minerals)`
只数 **ready+采矿** 的基地 → 偷矿基地建好前不算 → bot 照常按 build 开自己的自然分矿（真机：
玩家 t=44 下偷矿令、bot t=122 仍开自然，因 t=122 偷矿 Nexus 没 ready）。修：`Expand` 把
`stealth_pending_base_count`（Manager 注册的 PENDING/BUILDING cell 数；ready 的 MINING cell 已被
`our_zones_with_minerals` 计入，不重复）加进 `active_bases` → 玩家下偷矿令后 bot 延后/不开对应
分矿。偷矿被取消/打掉（cell 出局）→ 计数自动减 → bot 补开。**只作用于 build 的 `to_count` 判断，
不影响玩家开矿封顶那段**（封顶用原始 active_bases - stealth_zones）。

**动机**：

玩家按"停止扩张/封顶 N 矿"UI 按钮 → Director 调 `facade.set_expansion_override(N)` →
写入 `knowledge.vibecraft.expansion_cap_override`。若 bot 正在运行 `Expand(to_count=8)` 等
高目标 plan，玩家的封顶指令被无视，bot 继续开矿。此 hook 在 `Expand.execute()` 入口处
读 `expansion_cap_override`，让玩家可以实时限制 bot 的扩张上限。

`stealth_townhall_tags`：偷矿功能（Stealth Mining，WP0）的 Nexus tag 集合。偷矿基地属
"秘密基地"，不计入玩家的"正常扩张账"，否则偷了 2 矿后玩家再按"封 3 矿"反而让 bot
不再正常开自然扩张。`stealth_townhall_tags` 里的 Nexus 被排除后，`active_bases - stealth_zones`
才是对照封顶的有效计数。

**写入路径**：

`Director.apply_macro_action("expand", N/max/clear)` → `facade.set_expansion_override(N|None)` →
`_SharpyFacadeBase.set_expansion_override` 写 `knowledge.vibecraft.expansion_cap_override`。
EXPANSION_OVERRIDE directive 走 `production_overrides` 列表（不入 `_in_flight`），
`_apply_to_facade` 路径对它无效，所以 override **必须从 `apply_macro_action` 直接调 facade**。

**升级 checklist 追加**：sharpy upstream 改 `Expand.execute` 内 `active_bases` 计算逻辑
（如引入新的 base count 方式）→ 确认 vibecraft hook 插入位置仍在 `active_bases` 确定之后、
`zones = self.zone_manager.expansion_zones` 之前；`clear_worker()` 语义未变。

---

---

### 10. `vendor/sharpy/sharpy/plans/tactics/distribute_workers.py` —— 偷矿 FENCE（WP3，2026-06-10）

**类**：`DistributeWorkers`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `generate_worker_queue` | 在 `for building in gas_buildings + townhalls:` 循环体最顶部加 `# vibecraft:` 块：stealth tag → 取 `worker_dict[tag]` 里**不在 `stealth_worker_tags`** 的才算 drifter（tag-aware，保护自产农民含 cache-miss 未 Reserve 的）；有 drifter → 改写 `worker_dict[tag]` 为只剩 drifter + 发 `force_exit` 驱逐；无 → `pop` 掉、`continue` | `stealth_townhall_tags` + `stealth_worker_tags`（set of int） |
| `assign_to_work` | 方法首行加 `# vibecraft:` + 调 `self._vibecraft_log_transfer(worker, work)`（新增 helper）：农民被调去的目标基地 ≠ 来源基地时，打一行 `ECONTRACE worker_transfer`（from/to 基地分类 main/natural/expN/stealth + tag + 坐标 + 距离）。配套新增 `_vibecraft_base_kind`（按到 `own_main_zone` 距离排序分类）。纯诊断、`try/except` 包裹永不抛错。 | `stealth_townhall_tags`（分类用） |

**marker 格式**：`# vibecraft: 偷矿基地主动 FENCE（双向隔离...`（generate_worker_queue，1 处）
+ `# vibecraft: 经济可观测 —— 农民被调去 work...`（assign_to_work，1 处）。

**worker 跨基地调度可观测（2026-06-11）**：现有日志只有偷矿方向的 `DRAIN_ALARM` + 无标签的
`base_saturation` 快照（每 ~2s、位置列表无 tag、是计数不是移动事件），**读不出"主矿往普通自然
分矿派农民、派了几个、派去哪"**。`assign_to_work` 是该 plan 唯一的 worker 调度 chokepoint
（`set_work` 调用它下 `worker.gather`）。在此打 `ECONTRACE worker_transfer`：来源基地 = 离 worker
当前位置最近 townhall，目标基地 = 离 work 最近 townhall，**只在两者 tag 不同时打**（同基地换矿点
不打，避免噪音）。日志走 `logging.getLogger("vibecraft.econtrace")`（INFO；vibecraft namespace 已
`setLevel(INFO)` → 进 server FileHandler）。偷矿驱逐方向（`from_kind=stealth`）也顺带可观测。
分析时配合 `game_start` record 的 home/natural/enemy_main 坐标可进一步精确归因。

**主动 FENCE 升级（2026-06-11）**：旧逻辑只 `continue` 跳过 stealth Nexus —— 只防"路由新农民
进来"，**无法驱逐**已经漂进来采矿的非 stealth 农民（真机 `assigned=5 > 自产=2` 持续 1152 帧
DRAIN：主矿农民倒灌进偷矿基地后卡死，没机制赶走）。升级后：stealth 农民是 Reserved
（LLM_CONTROLLED），`calculate_workers` 的 `only_roles` 已把它们排除在 `worker_dict` 外，故
`worker_dict[stealth_nexus]` 只含"漂进来的非 Reserved 主矿农民"。有则发
`WorkStatus(building, -drifters*10000, force_exit=True)`（复用 enemy-zone 撤离机制）让平衡器逐帧
把它们赶回主矿；没有则跳过（不作为 add 目标，仍防路由）。`force_exit=True` 保证即使主矿满采
也有去处（`get_new_work` fallback）。绝不碰 Reserved stealth 农民。

**tag-aware 修回归（2026-06-11，真机 22 次 from_kind=stealth→main 定位）**：只靠 only_roles
（Reserved 过滤）不够 —— stealth 自产农民**出生那帧 `set_unit_role` cache-miss、没 Reserve 上**，
会以 Gathering role 混进 `worker_dict[stealth_nexus]` → 被当 drifter 赶回主矿（cell 长不起来、
83 train 卡在 wc=4）。修法：drifter = `worker_dict[stealth_nexus]` 里**不在 `stealth_worker_tags`**
的 tag（自产农民含 cache-miss newborn 都在此集合，`adopt_newborn` 即时注册）。并**改写
`worker_dict[stealth_nexus]` 为只剩 drifter**，让 `execute()` 的驱逐**选择**（`furthest_to`）也只
挑 drifter，不会误选到 stealth 农民。真机自验：cell worker_count 从卡死 1-4 → 稳定爬升到 12，
nexus_assigned 与 worker_count 同步（农民留在偷矿基地采矿）。

**读取路径**：`getattr(getattr(self.knowledge, "vibecraft", None), "stealth_townhall_tags", set())`

- `self.knowledge` 由 `Component.start(knowledge)` 保证已赋值（`DistributeWorkers → ActBase → Component`）。
- 用 `getattr` 兜底：vibecraft namespace 不存在时返回空 set → hook 静默不生效，原逻辑不受影响。

**动机（防倒灌）**：

stealth Nexus 刚建好时 `worker_dict[nexus.tag]` 为空，`ideal_harvesters=16`。
原逻辑：`available = 16 - 0 = +16` 大缺口 → `DistributeWorkers` 把主矿农民抽来补 → 主矿空缺触发补员 → 经济振荡。
修复：stealth Nexus 在 `work_queue` 不出现 → 不被分配农民。偷矿农民由 `StealthCellManager.train_probe_at` 独立补员（WP4）。

**边界**：`stealth_townhall_tags` 只含 stealth Nexus tag，不含气矿 tag（气矿排除问题留 WP4 评估）。

**升级 checklist 追加**：
- sharpy upstream 改 `generate_worker_queue` 的 `for building in ...` 循环结构 → 确认 `# vibecraft:`
  块仍在循环体**最顶部**（在现有 `is_ready/build_progress` filter 之前），`continue` 语义不变。
- sharpy upstream 改 `assign_to_work` 签名/`worker.gather` call site → 确认首行 `self._vibecraft_log_transfer(worker, work)`
  仍在、`_vibecraft_log_transfer` / `_vibecraft_base_kind` 两个 helper 还在类里、模块顶部
  `_vc_transfer_logger` 还在。跑 `TestWorkerTransferLog`。

### 11. `vendor/sharpy/sharpy/plans/acts/act_unit.py` —— 偷矿农民账目分离（2026-06-11）

**类**：`ActUnit`（仅农民产线生效，`unit_type == my_worker_type`）

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `is_done` | 有偷矿基地时，主矿（非 stealth 基地）`assigned_harvesters >= ideal_harvesters`（满采）→ 直接 done（不再产农民） | `stealth_townhall_tags` |
| `builders` | worker 产线的 builders 过滤掉 stealth Nexus（不在 stealth Nexus 造农民，避免双产） | `stealth_townhall_tags` |

**marker 格式**：`# vibecraft: 有偷矿基地时主矿满采就停...` / `# vibecraft: 主力农民产线不在 stealth Nexus 造农民...`

**动机（防主矿超产/闲置）**：bot 主力农民产线 `ActUnit(PROBE, NEXUS, staged_cap)` 的 staged_cap
是为"提前造农民转去新分矿"设计的，常 > 主矿当前 ideal。有偷矿基地时，多产的农民**没法转去
偷矿基地**（FENCE 隔离）→ 在主矿堆着闲置（玩家观察"主矿满采还一直造农民、很多农民没事干"）。
2026-06-11 改：`is_done` 在有偷矿基地时，主矿满采（assigned>=ideal，随开矿/枯竭动态）即停产，
偷矿基地由 StealthCellManager 自产自补。主矿扩张 → ideal 涨 → 自动接着产。
（注：早先 `get_unit_count` 减 `stealth_worker_tags` 的写法是**空操作**——该字段从未写进 SNS；
已改为本 `is_done` 方案，直接用 SNS 里的 `stealth_townhall_tags` + 实时 ideal/assigned。）
两处配合修复：
1. 本 patch `is_done` 主矿满采就停（不为偷矿提前多产）；
2. 本 patch `builders` 排除 stealth Nexus（不在那造）；
3. `persistent_macro.py` 农民 cap 档 gate 改 `RequireCustom(非 stealth NEXUS >= n)`（stealth
   Nexus 不顶高 cap 档，**非 vendor，在 vibecraft 代码内**）。

**非偷矿局零影响**：stealth 集合为空 → count 不减、builders 不过滤、gate 等价 `townhalls.ready >= n`。

**升级 checklist 追加**：sharpy upstream 改 `ActUnit.get_unit_count` / `builders` 结构 →
确认 `# vibecraft:` 块仍在（count 在 return 前、builders 在 `_builders` 构造后）。

---

### 12. `vendor/sharpy/sharpy/plans/acts/protoss/chrono_unit.py` —— 偷矿星空加速预留（2026-06-11）

**类**：`ChronoUnit`

| Method | 改动摘要 | 读取的 vibecraft 字段 |
|---|---|---|
| `execute` | 选能量源 Nexus 时跳过 `stealth_chrono_reserved_tags` 里的 Nexus（成长期偷矿 Nexus 能量预留给自我加速） | `stealth_chrono_reserved_tags` |

**marker**：`# vibecraft: 偷矿成长期 Nexus 的能量预留给它自我加速产农民...`

**动机**：bot 全局 ChronoUnit 把任意 Nexus（含偷矿 Nexus）当能量源给任意目标加速，导致偷矿
Nexus 能量被抽去给主矿加速、偷矿基地自己用不上（玩家观察"星空要塞一直没用过"，实测能量被抽到
10-16）。偷矿成长期把它的 Nexus 标进 `stealth_chrono_reserved_tags`（Manager 每 tick 注册），
ChronoUnit 不拿它当能量源 → 能量留给 `Manager.cast_chrono_on_nexus` 自我加速产农民。满采后
Manager 移出 → 能量释放回公共池（ChronoUnit/ChronoTech 给家里科技用）。

**非偷矿局零影响**：集合空 → 不跳过任何 Nexus。

---

### 13. 科技/施法单位主动技能补全（combat micro，2026-06-18）

**背景**：vibecraft 用 sharpy 默认 `MicroRules.unit_micros` 表派 per-unit 战斗微操;**没注册
micro 的单位 = 主动技能永不触发**。审计发现 GHOST/BANSHEE 没注册(EMP/狙击/隐形全不放)、ROACH 的
`MicroRoaches.__init__(self, knowledge)` 构造签名异常(其余 micro 都无参)导致注册时 TypeError 被漏。
用户痛点:鬼兵不狙击、女妖被打不隐形。

**新增文件**(无 upstream 对应,sharpy 升级不会覆盖;文件头 `# vibecraft:` marker):
- `combat/terran/micro_ghosts.py` — `MicroGhosts(MicroStep)`:EMP/狙击/隐形,**能量分段**
  (≥75 先 EMP 后 Snipe / 50-74 只 Snipe / <50 才 Cloak,防 snipe 饿死 EMP)+ snipe 引导期短路
  (不自打断 2s 引导)+ 全路径 `stay_safe`(脆皮不冲前)+ cloak 查敌方探测器。
- `combat/terran/micro_banshees.py` — `MicroBanshees(GenericMicro)`:接敌 + 未被探测 + energy>30
  → 隐形;否则普攻打地面。

**改既有 upstream**(各带 `# vibecraft:` marker,进 `test_sharpy_patch_audit.py::PATCHED_METHODS`):
| 文件 | Method | 改动 |
|---|---|---|
| `combat/micro_rules.py` | `load_default_micro` | 注册 `GHOST=MicroGhosts() / BANSHEE=MicroBanshees() / ROACH=MicroRoaches()` |
| `combat/zerg/micro_roaches.py` | `__init__` | `(self, knowledge)`→`(self)` 修异常签名(原因→无法注册) |
| `combat/protoss/micro_sentries.py` | `group_solve_combat` | Guardian Shield 触发阈值 `range_power > 10`→`> 6`(小规模也开盾;**不碰** shield_percentage 行 = FF/幻象分支) |
| `combat/zerg/micro_vipers.py` | `unit_solve_combat` | Abduct 加 `engaged_power.power > 6` 门 + 高价值目标(坦克/巨像/BC/不朽)豁免门 |
| `combat/terran/micro_ravens.py` | `unit_solve_combat` | 加 Auto-Turret(`BUILDAUTOTURRET_AUTOTURRET`,排干扰矩阵/反装甲之后,落点由 cd_manager 门控一次部署) |

**施法门机制(评审定论)**:`cd_manager.is_ready(tag, ability)` **两参形式**已含 tech 研究门 + 能量 +
冷却(底层 `get_available_abilities`)→ **不需**额外查 upgrade/abilities。**红线:绝不给 is_ready 传
第 3 个 `cooldown` 参数**(传了退化成纯时间比较、绕过 available 查询 → 对没研究的技能空放)。

**scope 边界**:micro 只跑在进了 combat group 的 `free_units`。**玩家 `unit_claim` 单独编队偷袭的
caster = Reserved,不进 group → 不走这套自动施法**。本方案只覆盖"caster 跟大军一起参战"。

**砍项**:Cyclone lock-on(build 库无飓风=死代码)、Medivac afterburner(大军 combat 收益低)——本期不做。

**验证**:`scripts/caster_ability_selftest.py`(debug `debug_upgrade()` 解锁研究门 + 生 caster/敌 +
`VIBECRAFT_CASTER_TRACE` grep `CASTERTRACE`)真局实测:鬼兵狙击 9 / EMP 9 / 女妖隐形 6 / ghost cloak 3
**全 PASS**。设计 + 评审留痕 `docs/plans/2026-06-18-caster-abilities-design.md`。

**升级 checklist**:sharpy 升级覆盖以上文件后,重新加回 micro_rules 注册 + 各 micro 改动(对照本节表),
跑 `test_sharpy_patch_audit.py` 确认 marker 在 + `caster_ability_selftest.py` 确认技能仍触发。

---

### 14. `vendor/sharpy/sharpy/plans/acts/tech.py` —— 攻防升级目标等级封顶门（2026-07-07）

**类**：`Tech`

**改动**：`execute` 方法 —— 在 `if not self.enabled: return True` 之后、
`builders = self.cache.own(...)` 之前加封顶门。

| 方法 | 改动 |
|---|---|
| `execute` | 顶置门：`_vibecraft_parse_upgrade(self.upgrade_type.name)` 解析 (family, level)；family∈15族白名单 + `upgrade_targets.get(family)` 是手动值 T + `level > T` → `return True`（跳过、不 reserve、推进 BuildOrder）；T=None(auto) 或非攻防升级 → 不拦 |

**marker**：`# vibecraft: 攻防升级目标等级封顶门（2026-07-07）`

**helper 常量/函数**（module 级，同文件）：
- `_VIBECRAFT_UPGRADE_CAP_FAMILIES: frozenset[str]` — 15 族白名单
- `_vibecraft_parse_upgrade(upg_name)` — 名称 → (family, level) 或 (None, None)

**读取路径**：`getattr(self.knowledge, "vibecraft", None)` → `upgrade_targets: dict[str, int]`
（写入路径：`Director.apply_macro_action(dim="upgrade_target")` → `facade.set_upgrade_target`
→ `knowledge.vibecraft.upgrade_targets`）

**为什么在 reserve 之前**：被封顶的升级若仍执行 `knowledge.reserve(cost.minerals, cost.vespene)` 会
预占矿气 → 饿死其他 act 下单预算。门在 reserve 前返回 True 完全跳过该级。

**升级 checklist**：sharpy 升级覆盖 `tech.py::Tech.execute` 后，把 `_VIBECRAFT_UPGRADE_CAP_FAMILIES`
常量 + `_vibecraft_parse_upgrade` 函数 + 封顶门代码块重新加回（标 `# vibecraft:` marker），
跑 `test_sharpy_patch_audit.py` 确认 marker 存在，跑 `scripts/upgrade_target_selftest.py` 确认封顶有效。

---

> 最后更新：2026-07-07（§14 Tech.execute 攻防升级封顶门）
