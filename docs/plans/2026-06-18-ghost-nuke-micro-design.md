# ghost_nuke 核弹微操设计（#547）

> 目标：让 ghost_nuke 的招牌"核弹骚扰"真正落地，与 bio_max 做出实质差异（用户 2026-06-18：
> 做核弹微操、不合并）。当前 ghost_nuke.py 只研隐身 + 出 8 幽灵，无任何核弹逻辑。

## 现状
- ghost_nuke.py：建 GhostAcademy×1 + 研 PERSONALCLOAKING + `TerranUnit(GHOST,8)`。无核弹。
- 已有 `MicroGhosts`(vendor/sharpy/.../micro_ghosts.py)：**仅战斗内** EMP/Snipe/Cloak，跟大军走。
- 核弹是**地图级战略动作**（脱离大军、潜入、对固定目标 calldown），不适合塞进 in-combat micro。

## 方案：新增 `AutoNukeAct`（vibecraft Act，不 patch sharpy）
仿 `SiegeIdleTanksAct`，作为一个 `ActBase` 加进 ghost_nuke.py 的 tactics `SequentialList`
（放 DistributeWorkers 之后、PlanZoneAttack 之前，与 SiegeIdleTanks 同位）。新文件
`src/vibecraft/bot/auto_combat/terran/nuke_act.py`。**不碰 sharpy vendor**（核弹是独立 Act，
不在 sharpy combat plan 内派单位，符合"加 Act 而非 patch"判据）。

### AbilityId（已确认存在于本项目 sc2）
- 造核弹：`AbilityId.BUILD_NUKE`（对 GhostAcademy 下）
- 幽灵发射：`AbilityId.TACNUKESTRIKE_NUKECALLDOWN`（target=Point2）
- 核弹单位：`UnitTypeId.NUKE`（`already_pending(NUKE)` 可数在造的）

### 职责一：维持核弹库存（每帧）
- 条件：GhostAcademy ready + `already_pending(NUKE)==0` + 当前无已就绪核弹 + `can_afford(NUKE)`(100/100)。
- 动作：`ghost_academy(AbilityId.BUILD_NUKE)`。
- "已就绪核弹"判定：任一幽灵的 `get_available_abilities` 含 `TACNUKESTRIKE_NUKECALLDOWN`
  ⇒ 有库存（python-sc2 无干净的"核弹库存计数"，用幽灵可用技能反推；get_available_abilities
  是 async/有开销，**节流**：每 ~22 帧查一次，结果缓存）。

### 职责二：核弹打击 run（一次一发，状态机挂 self）
状态：`IDLE → MOVING → ARMING → COOLDOWN`。
1. **触发**（IDLE→MOVING）：有就绪核弹 + 找到好目标 + 有可用幽灵。
   - **目标选择**（优先级）：① 敌方静态防御/生产建筑群（炮台/兵营/重工成簇）② 敌方**静止**
     兵团 clump（≥6 supply 聚在一起，如架起的坦克、矿口农民群）。取"半径 2 内单位数 × 价值"
     最高的点。避开己方单位 2.5 半径（核弹友伤）。无合格目标 → 不发。
   - **选幽灵**：能量无关（核弹不耗能）；选**有 TACNUKESTRIKE 技能**、离目标近、不在 Snipe 引导中的
     一只。**reserve 它**（`facade.set_unit_role` / roles → Reserved，或 vibecraft claim），
     使 PlanZoneAttack/combat 不把它拉进大军。记 `self._nuker_tag` + `self._target`。
2. **MOVING**：把 nuker 移向目标；途中若未隐身且无敌方 detector 在侧 → cloak。到达 cast 距离
   （nuke 射程 ~12，留余量用 10）→ ARMING。
3. **ARMING**：`Action(target, queue=False, ability=TACNUKESTRIKE_NUKECALLDOWN)`。发射后幽灵
   原地引导 ~3s 完成发射，随即**撤退**（核弹 14s 落地，幽灵不必留）。记发射时间。
4. **COOLDOWN**：幽灵 release role + 撤回大军；隔 ~10s 才允许下一发 run（防抖）。
- **中止**（任何阶段→COOLDOWN+release）：nuker 死亡 / 目标点已无 clump / cast 点 11 格内出现敌方
  detector（会被反隐杀 + 核弹被看见躲开）/ 核弹库存没了。

### 角色/控制权交互（关键，对照玩家控制权模型）
- nuker 幽灵走 **bot 自主 reserve**（不是玩家指令 claim）。要保证：①PlanZoneAttack 不抢它
  （Reserved 不在 free_units）②玩家若显式给该幽灵下指令/全军命令——按既有规则玩家单位级指令
  抢占，AutoNukeAct 检测到 nuker 不再属于自己（role 被夺/tag 消失）即放手、回 IDLE。
- 只 reserve **1 只**幽灵做 nuker，其余幽灵照常 EMP/snipe 跟大军（MicroGhosts 不变）。

### 参数（env 可调，便于自验）
- `nuke_min_cluster`（默认 6 supply / 4 单位）、`nuke_safe_radius`（己方友伤规避 2.5）、
  `run_cooldown_s`（10）、`avail_check_period`（22 帧）。

## 自验方案（不喊用户）
新增 `scripts/nuke_selftest.py`（仿 addon_selftest）：debug 生 GhostAcademy + 几只 Ghost +
debug_create 一簇敌方静止单位/建筑在远点，注入"切 ghost_nuke"或直接起 ghost_nuke 真局，
grep `NUKETRACE`（Act 内 env-gated 日志：build_nuke_issued / target_picked / calldown_issued /
aborted reason）断言：核弹被造出（already_pending NUKE 出现过）+ 至少一次 calldown_issued +
落点在敌簇而非己方。non-realtime + mock LLM 跑（参考 proxy_chain_selftest 取舍）。
另跑 `build_acceptance ghost_nuke`（VeryHard）确认主体生化体系没被 Act 拖坏。

## 不做（YAGNI）
- 不做核弹"读秒预警规避"博弈、不做多幽灵同点叠射、不做对移动兵团的预判提前量（只打静止簇）。
- 不动 bio_max（保持两 build 差异：ghost_nuke = EMP + **真核弹骚扰**；bio_max = 纯 MMM deathball）。

## 评审后定稿（2026-06-18 opus 评审，实现严格按本节，覆盖上文冲突处）
方向/骨架/API 名评审通过。**4 个必改 + 若干强化，已采纳：**

1. **⚠️ 友伤半径**：`nuke_safe_radius` 2.5 → **9**（核弹爆炸半径 ~8，2.5 会炸死自家 2.5~8 格内单位）。
   簇统计半径 2 → **6**（核弹覆盖 8 格，半径 2 严重低估目标价值）。
2. **⚠️ 每帧重 reserve**：role manager `update()` **每帧重置**单位角色（unit_role_manager.py:234-271）。
   nuker 必须 **MOVING/ARMING 期间每帧 `self.knowledge.roles.set_task(UnitTask.Reserved, ghost)`**
   （照抄 `protoss/plans/phoenix_squad_act.py` 的 `_reserve` 模式）。只 reserve 一次 → 下帧被
   PlanZoneAttack/MicroGhosts 当帧抢走、抽搐。**好处**：停止重 reserve 即自动回归大军，无泄漏。
   **不走 facade**（Act 有 `self.knowledge.roles` 直达；走 facade 触发 dual-impl+audit 负担）；
   **不用 vibecraft claim**（那是玩家指令簿记，Act 自主行为别污染）。设计里 "facade.set_unit_role" 删掉。
3. **⚠️ retreat 召回**：每帧读 `getattr(getattr(self.knowledge,"vibecraft",None),"combat_intent_override",None)`，
   `== "retreat"` → 立即 abort + 停 reserve（堵住"玩家全军撤退召不回 Reserved nuker"的洞——
   全军命令只动 free_units，不碰 Reserved）。**单发 run 只 ~20-30s，不做持久指令卡**（YAGNI）。
4. **⚠️ 状态看门狗**：MOVING / ARMING 各加 max-duration（MOVING 30s、ARMING 5s）超时 → abort+COOLDOWN，
   防幽灵被堵/cast 打不出而永久卡住 reserve 一只。
5. **发射即撤**：砍掉"原地引导 3s"。calldown 近瞬发、目标点已锁；确认 cast 成功
   （`unit.orders` 出现 nuke order 或可用技能不再含 TACNUKESTRIKE）→ 立刻 retreat。
6. **目标优先级：建筑绝对第一**（炮台/兵营/重工/矿口指挥中心——不会跑，14s 必中）＞静止兵团
   （架起坦克随时可能走位躲核）。无合格目标不发。
7. **目标只在 IDLE→MOVING 选一次**锁进 `self._target`，MOVING/ARMING **每帧幂等重发同一缓存点**，
   绝不每帧 re-pick（CLAUDE.md 目标点锁定强规则；否则追漂移目标到不了/cast 不出）。
8. **get_available_abilities 只对候选幽灵小集合调**（不对全军），22 帧节流。
9. `execute()` 永远 `return True`（non-blocking，否则 block 掉后面的 attack/PlanFinishEnemy）。

实现最佳范例直接照抄：`src/vibecraft/bot/auto_combat/protoss/plans/phoenix_squad_act.py`
（reserve/release + active-flag 模式）。自验 selftest 铁律见上文 + debug 没法直接造"已就绪核弹"
（要 debug 造 GhostAcademy+幽灵后让 BUILD_NUKE 真跑完 ~21s 游戏秒），inject_after < fast 压缩墙钟。
