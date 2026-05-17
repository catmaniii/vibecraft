# L2 战术执行器 + L4 done_when 扩词表 + 命令卡片统一 — 设计文档

> 状态：设计完成，待实施
> 日期：2026-05-17
> 关联：`docs/directive-coverage-report-2026-05-17.md`（暴露问题）/ `docs/adr/0010-four-layer-commands.md`（4 层 directive 顶层设计）

---

## 1. 目标

修掉 e2e 报告暴露的两个大缺口：
1. **L2 `tactical_objective` 无执行器** —— LLM 识别成功 + 进 Board，但 `_apply_to_facade` 缺分支，bot 完全无视（4 个死路 case）
2. **L4 done_when 词表不够 + 缺 structure_override** —— 运营类高频指令"补到 8 BG / 维持 6 不朽 / 阶段硬转"全部跑不通

同时统一一个高优先级 UX 约束：
3. **所有 L1/L2/L3/L4 命令在 PWA UI 必须有可见卡片 + 玩家点 X 可即时取消**

---

## 2. MVP 范围（已拍板）

### L2 战斗指令（5 类 in，5 类 out）

| in MVP | 候选路径 |
|---|---|
| A1 进攻（attack） | ① override flag（全军 target + intent） |
| A2 守家（defend/hold） | ① override flag |
| A3 撤退（retreat） | ① override flag |
| B1 凤凰骚扰（harass） | ② squad 抢占（按数量锁定） |
| C2 派 scout（scout） | ② squad 抢占（unit_count=1） |

| out MVP | 砍掉原因 |
|---|---|
| B2 DT 偷家 | 路径规划复杂，MVP 后 |
| C1 集火点名 | 依赖 PWA 单位点选 UX |
| C3 救兵 | 复用 A1 即可（target=own_main） |
| 软建议模式 | 全部 L2 = 硬覆盖 |

### L4 运营指令（4 类 in，3 类 out）

| in MVP |
|---|
| O1 建筑补到 N（"补 8 BG / ramp 1 cannon"） |
| O4 阶段硬转（"8 BG 起来转 IAC"） |
| O5 升级序列（"BC 完成升折跃"） |
| O9 暂停生产（"别造兵了"） |

| out MVP |
|---|
| O2 单位维持量（需"维持型"lifecycle） |
| O6 反制响应（需 enemy 事件流） |
| O7 资源约束（sharpy spend hook 太深） |
| O10/11/12 worker / cancel queue / 单建筑取消 |

### 五个需求边界

| | 决策 |
|---|---|
| A1 风险提示 | **完全听话**，bot 不反对 |
| B3 unit 冲突 | **squad 按数量锁定**："派 N 个 X 去骚扰"只锁这 N 个，剩余 X 留给 sharpy；后续指令照常能拿空闲 X。LLM 必须给数量，没给走 ambiguous |
| C1 集火点名 | **不做** |
| A2 解除条件 | **统一玩家点 X**（无 timeout，无 done_when 词组） |
| 软建议 | **不做**，全部硬覆盖 |

### 三个边界 case

| | 决策 |
|---|---|
| O1 "补到 8 BG" 被打掉 | **不补**（一次性 done，不扩 lifecycle） |
| L2 squad done_when | **保留**（A 类无 / B 类有，两条分流） |
| L1 cancel 统一 board | **代码层统一走 board.submit**，UI 给 StrategyCard 加 X |

---

## 3. L2 执行器：候选 4 hybrid

### 路径分流

```
LLM 解 tactical_objective(verb, target_area, ...)
                  ↓
       Director._exec_tactical_objective(d, payload)
                  ↓
        ┌─────────┴─────────┐
        ▼                   ▼
   verb ∈ A 系列         verb ∈ B 系列
   {attack, defend,      {harass, scout,
    retreat, vision}      raze, regroup, ...}
        ↓                   ↓
   ① override flag      ② squad 抢占
   facade.set_attack_   facade.set_unit_role(
   target_override(pt)    tag, LLM_CONTROLLED)
   facade.set_combat_      为这些 unit 注册
   intent_override(v)      TacticalSquad
        ↓                   ↓
   VibeCraftZoneAttack  每 tick：
   ._get_target() 读     sharpy.GroupCombatManager
   override                .execute(squad_tags,
                                    target, MoveType)
        ↓                   ↓
   done_when=None        done_when 满足时
   仅玩家点 X 清          release_unit_role +
   override               清 squad
```

### 3.1 ① override flag 实现

**facade 新增 2 方法**（`src/vibecraft/bot/facade.py`）：

```python
class Sc2Facade(Protocol):
    # ... 现有方法 ...

    # L2 全军方向覆盖（None = 清覆盖，恢复 sharpy 默认）
    def set_attack_target_override(self, point: Point2 | None) -> None: ...
    def set_combat_intent_override(
        self,
        intent: Literal["attack", "defend", "hold", "retreat", "vision"] | None,
    ) -> None: ...

    # 顺手把 set_engagement_stance 也实现了（之前 M1 noop）
    # 行为统一收敛到 combat_intent_override：
    # "defend" → intent_override="defend"
    # "hold"   → intent_override="hold"
    # "retreat"→ intent_override="retreat"
    # "free"   → intent_override=None
```

**SharpyFacade 实现**（`bot/auto_combat/protoss/bot.py` 或新增 `sharpy_facade.py`）：

```python
def set_attack_target_override(self, point):
    self._bot.knowledge.vibecraft.attack_target_override = point

def set_combat_intent_override(self, intent):
    self._bot.knowledge.vibecraft.combat_intent_override = intent
```

**新增 sharpy plan 子类**（`bot/auto_combat/protoss/plans/vibecraft_zone_attack.py`）：

```python
from sharpy.plans.tactics import PlanZoneAttack

class VibeCraftZoneAttack(PlanZoneAttack):
    """覆盖 sharpy 默认 attack target / should_attack 决策，
    优先读 knowledge.vibecraft.{attack_target_override, combat_intent_override}。
    无 override 时走原 sharpy 逻辑。"""

    def _get_target(self):
        pt = self.knowledge.vibecraft.attack_target_override
        if pt is not None:
            return pt
        return super()._get_target()

    def _should_attack(self):
        intent = self.knowledge.vibecraft.combat_intent_override
        if intent == "attack":
            return True
        if intent in ("defend", "hold", "retreat", "vision"):
            return False
        return super()._should_attack()

    # retreat / hold 时还要把已经在外面的兵召回；待实施时具体看 sharpy retreat path
```

**6 个 plan 文件改 1 行**（替换 `PlanZoneAttack` → `VibeCraftZoneAttack`）：
- `1g_robo_immortal.py` / `4bg.py` / `iac_2base.py` / `skytoss.py` / `sustain.py` / `forward_proxy.py` / `gate4_pressure.py`

### 3.2 ② squad 抢占实现

**Director 新增 squad 维护**（`src/vibecraft/bot/director.py`）：

```python
@dataclass
class TacticalSquad:
    directive_id: str
    unit_tags: set[int]
    target: Point2
    move_type: MoveType    # sharpy enum：Assault / Harass / DefensiveRetreat
    verb: TacticalVerb
    n_wanted: int          # 玩家说的目标数（"5 个凤凰" → 5）
    n_locked: int          # 实际锁到的数（短缺时 < n_wanted）

class Director:
    _tactical_squads: dict[str, TacticalSquad]  # directive_id → squad

    def _exec_tactical_objective(self, d, payload):
        verb = payload.verb
        if verb in {ATTACK, DEFEND, RETREAT, VISION}:
            self._exec_l2_global(d, payload)
        elif verb in {HARASS, SCOUT}:
            self._exec_l2_squad(d, payload)
        # raze / regroup / split / drop / expand / vision 暂不做，logger.warning("L2 verb %s 未实现", verb)

    def _exec_l2_squad(self, d, payload):
        # squad 按数量锁定：LLM 必给 unit_count_hint（已有字段，复用），
        # selector 只抽 N 个 free unit。已被其他 squad 锁的 unit 不抽
        # （resolve_selector 自动过滤 LLM_CONTROLLED role）
        n_wanted = payload.unit_count_hint   # LLM 契约保证非空（schema validation）
        unit_type = (payload.unit_type_hint or [self._infer_unit_type(payload)])[0]
        free_tags = self.facade.resolve_selector(unit_type=unit_type)
        # 短缺时抢能抓到的，directive 状态显示进度
        tags = free_tags[:n_wanted]
        if not tags:
            self._set_directive_status(d, "on_hold", f"无空闲 {unit_type}")
            return
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
        status_msg = (
            f"已接管 {len(tags)} 个 {unit_type}" if len(tags) == n_wanted
            else f"已接管 {len(tags)}/{n_wanted} 个 {unit_type}（短缺）"
        )
        target_pt = self._resolve_target_area(payload.target_area)
        self._tactical_squads[d.id] = TacticalSquad(
            directive_id=d.id,
            unit_tags=set(tags),
            target=target_pt,
            move_type=MoveType.Harass if verb == HARASS else MoveType.Assault,
            verb=verb,
            n_wanted=n_wanted,
            n_locked=len(tags),
        )
        self._set_directive_status(d, "active", status_msg)

    async def execute_tactics_step(self, now: float):
        """每 sharpy step 调用，给 active squad 派活。"""
        for squad in self._tactical_squads.values():
            # 用 sharpy 的 GroupCombatManager 微操（撤退/集火/风筝自动）
            self._bot.combat_manager.execute(
                list(squad.unit_tags),
                squad.target,
                squad.move_type,
            )

    def _on_directive_released(self, d_id):
        """done_when 满足或玩家点 X 时清理。"""
        squad = self._tactical_squads.pop(d_id, None)
        if squad:
            for tag in squad.unit_tags:
                self.facade.release_unit_role(tag)   # 还给 sharpy
        # override flag 类指令清掉 override
        if d_id == self._current_l2_global_id:
            self.facade.set_attack_target_override(None)
            self.facade.set_combat_intent_override(None)
            self._current_l2_global_id = None
```

**bot.py 接入**（同 L4 那条路径并列调用）：

```python
async def _tick_bot_channel(self):
    # ... 现有 director.execute_overrides_step(now_s) ...
    await self.director.execute_tactics_step(now_s)
    await super().on_step()
```

### 3.3 done_when 分流

| verb | done_when | unit_count |
|---|---|---|
| A 系列（attack/defend/retreat/hold/vision） | **None**（仅玩家点 X 解除） | N/A（全军） |
| B harass | LLM 必生成（"打死 5 个就回" → enemy_killed_in_area） | **LLM 必给**（"派 5 个凤凰" → 5） |
| scout | LLM 必生成（"侦察到了就回" → vision_acquired） | **LLM 必给**（默认 1） |

LLM prompt 改：
- A 系列 done_when=None 显式写进 prompt 规则（避免之前死路 case 被 task_monitor 立即判 done）
- B 系列 `unit_count` 必填：玩家没给数量（"凤凰骚扰对面"）→ 走 ambiguous，二次确认"几个凤凰"。**不允许 LLM 默认值**，没说就是没想清楚

squad 短缺时（玩家说 5 个，家里只有 3）：抢现有 3 个先开干，status 显示 `已接管 3/5 Phoenix（短缺）`。后续不会自动补足 —— 跟"补 8 BG 被打掉不补"一致的一次性语义。

---

## 4. L4 done_when 词表扩展 + structure_override

### 4.1 新增 done_when kind

`src/vibecraft/directives/done_when.py`：

| kind | payload | 用途 | 玩家话术 |
|---|---|---|---|
| `structure_count` | structure_type, op, value | 当前建筑存量 | "8 BG 起来就转 IAC" |
| `own_unit_count` | unit_type, op, value | 当前单位存量 | "出 5 不朽就出门" |
| `supply_used` | op, value | 人口已用 | "70 人口前别打" |
| `supply_cap` | op, value | 人口上限 | "200 人口再硬转" |
| `minerals` | op, value | 当前晶矿 | "矿到 1000 再造 nx" |
| `gas` | op, value | 当前瓦斯 | "气憋够 200 再造 sentry" |
| `worker_count` | op, value | 工人数 | "农民到 50 再开矿" |

实现：在 `task_monitor.py` 的 done_when evaluator 加 7 个分支，全部用 `self._bot.{units, structures, minerals, gas, supply_used, supply_cap, workers}` 读 sc2 state。每个分支 < 10 行。

> 说明：选了 B（扩词表，不动 lifecycle），所以 done_when 满足后 directive 照常 released，**不"持续维持"**。"补 8 BG" = 一次性到 8 个就结束，被打掉一个不会自动补。玩家如果要"维持 8 BG" 体感，需要重新下指令 —— MVP 范围内可接受。

### 4.2 structure_override 新 directive type

`src/vibecraft/directives/types.py` 加 enum value `STRUCTURE_OVERRIDE`。

`src/vibecraft/directives/models.py`：

```python
class StructureOverridePayload(BaseModel):
    structure_type: str       # "Gateway" / "PhotonCannon" / "Pylon"
    target_count: int         # 8（"补到 8 BG"）
    location_hint: str | None = None  # "ramp" / "natural" / "front" / None=bot 自选
    done_when: DoneWhen | None = None  # 默认 structure_count(>=, target_count)
```

`Director._exec_structure_override`（director.py 加方法）：

```python
def _exec_structure_override(self, d, payload):
    type_id = self._resolve_structure_id(payload.structure_type)
    if type_id is None:
        self._set_override_status(d, "on_hold", f"未知建筑 {payload.structure_type}")
        return
    current = (
        self._bot.structures(type_id).amount
        + int(self._bot.already_pending(type_id))
    )
    remaining = payload.target_count - current
    if remaining <= 0:
        self._set_override_status(d, "active", f"{current}/{payload.target_count} 已达成")
        return
    # mineral / gas / prereq check（同 _exec_production_override 的模式）
    ok, missing = self._check_prereq_ready(payload.structure_type.upper())
    if not ok:
        self._set_override_status(d, "on_hold", f"需要 {missing}")
        return
    # 直接调 sharpy build：knowledge.build_orders 或 bot.build()
    # 用 location_hint 解出位置（None → 让 sharpy 自选 placement）
    pos = self._resolve_location_hint(payload.location_hint, type_id)
    try:
        await self._bot.build(type_id, near=pos)
        self._set_override_status(d, "active", f"造 {payload.structure_type} ({current+1}/{payload.target_count})")
    except Exception as exc:
        logger.debug("structure_override build fail: %s", exc)
```

注意：建筑只下一个 build call；后续 tick 重新进 _exec 时由 `current` 重新计数，多个建筑串行造（不是一次性塞 N 个 queue）。

### 4.3 LLM prompt 改动

加新 directive type 介绍 + 新 done_when kind 表 + 4-5 个 few_shot 例子：

```
例 23：「家里补到 8 BG」
→ structure_override: structure_type="Gateway", target_count=8, location_hint="main"
   done_when={kind:"structure_count", structure_type:"Gateway", op:">=", value:8}
   timeout_s: 180

例 24：「8 BG 起来就转 IAC」
→ 两条 directive，LLM 自己排序：
   1. structure_override: Gateway target_count=8
      done_when=structure_count(Gateway, >=, 8)
   2. strategy_set: stage=midgame, strategy_id="iac_2base"
   （注：MVP 不做 directive chain，第二条立刻提交跟第一条并行，靠 LLM
    在生成时给"等 8 BG"加 trigger_after_id 字段；或更简单：第二条不
    生成，玩家看到 1 满足后自己说"切 IAC"。MVP 选后者。）

例 25：「闪烁好了升 +1 攻」
→ 两条独立 directive：
   1. tech_override: BlinkTech
      done_when=tech_done(BlinkTech)
   2. tech_override: ProtossGroundWeapons
      （第二条 LLM 不生成，玩家手动二次确认。MVP 不做链。）

例 26：「别造兵了」
→ production_pause(duration_s=60 或 until="player_revoke")
   （新原语，本文档先占位，实施推迟）

例 27：「ramp 放 2 cannon 1 BF」
→ 两条 directive：
   1. structure_override: PhotonCannon target_count=2, location_hint="ramp"
   2. structure_override: Forge target_count=1, location_hint="ramp"
```

**MVP 不做 directive chain**。O4/O5 拆成"玩家分两步说"。

---

## 5. 命令卡片 + revoke UI 统一协议

### 5.1 后端 snapshot 字段

所有四层 directive 透传到 PWA 一个统一 array：

```python
snapshot.command_cards: list[CommandCardView]

class CommandCardView(BaseModel):
    id: str                   # directive_id
    layer: Literal["L1", "L2", "L3", "L4"]
    type: str                 # "strategy_set" / "tactical_objective" / ...
    display: str              # 中文摘要（"进攻自然" / "补 8 BG"）
    issued_at: float          # 游戏时间
    status: Literal["pending", "active", "on_hold", "done"]
    status_reason: str        # "已下单等完成" / "资源不足(120/400 矿)" / ""
    revokable: bool           # MVP 全部 true
```

`build_snapshot()` 改：原 4 个独立字段（`strategy` / `active_tactics` / `standing_orders` / `production_overrides`）保留向后兼容，**新增 `command_cards` 统一 array**。前端用新 array 渲染卡片栈，旧字段后续可弃。

### 5.2 active_tactics 透传修复

之前 L2 死路根因之一是 `active_tactics` 永远空（task_monitor 立即判 done）。本次按 A 系列 done_when=None 改完后：
- A 类 directive 进 board → committed → 立刻进 `_tactical_overrides` dict → snapshot 持续透传，直到玩家 X
- B 类有 done_when，正常逻辑，但要确保 commit 后至少出现 1 帧 snapshot 才允许 done（snapshot publish 跟 task_monitor.tick 顺序问题，实施时调整 tick 顺序）

### 5.3 WS revoke 协议

PWA 点 X →

```
{
  "type": "revoke_directive",
  "directive_id": "d_a73bd3",
  "client_id": "phone-1",
  "issued_at": 1716543210.5
}
```

→ `ws.py::_handle_revoke()` → `GameProcess.send_revoke(id)` → 子进程 `Director.revoke_directive(id)`：

```python
def revoke_directive(self, d_id):
    # 1. 调用 _on_directive_released 清 squad / override
    self._on_directive_released(d_id)
    # 2. 从 board 移除（不管在哪个 stage：in_flight / committed / scheduled）
    self.board.revoke(d_id)
    # 3. emit directive.revoked event
    self._push_event({"kind": "directive.revoked", "ts": now,
                      "payload": {"directive_id": d_id, "reason": "player_x"}})
    # 4. 立刻 push snapshot（卡片消失）
    self._push_snapshot(now)
```

### 5.4 L1 cancel 走 board.submit

之前特殊路径 `_dispatch_cancel` 直接 push snapshot，不进 board 流水。改：

```python
# 旧：on_player_command → strategy_cancel → _dispatch_cancel → push_snapshot
# 新：on_player_command → strategy_cancel → board.submit(STRATEGY_CANCEL)
#     → 1.5s commit → _apply_to_facade(STRATEGY_CANCEL) → facade.clear_stage(stage)
```

`_apply_to_facade` 加 STRATEGY_CANCEL 分支即可。`directives.jsonl` 自动有流水，UI 卡片消失自动靠 snapshot 透传清掉。

### 5.5 PWA UI

新 `web/src/components/CommandCardStack.vue`：

```html
<div class="card-stack">
  <CommandCard v-for="c in snapshot.command_cards" :key="c.id"
               :card="c" @revoke="onRevoke" />
</div>
```

`CommandCard.vue`：

```
┌────────────────────────────────────────┐
│ [L2]  进攻对方自然              [X]   │  ← 点 X 发 revoke 帧
│ ───────────────────────────────────── │
│ status: active                         │
│ issued at: 5:24                        │
└────────────────────────────────────────┘
```

按 status 染色：
- `pending` 灰
- `active` 绿
- `on_hold` 黄 + tooltip(reason)
- `done` 立刻消失（不停留）

`CockpitView.vue` 把现有 4 个 Card（Strategy/Tactics/StandingOrders/ProductionOverrides）替换为单一 `<CommandCardStack>`。原 4 个 Card 组件保留实现，备用。

---

## 6. 实施分阶段

| 阶段 | 工作量 | 内容 |
|---|---|---|
| **P0a** 后端 facade + override flag | 1d | `set_attack_target_override` / `set_combat_intent_override` / 实现 `set_engagement_stance`；`VibeCraftZoneAttack` 替换 6 plan |
| **P0b** 后端 squad 抢占 | 1d | `_exec_tactical_objective` 分流，`_exec_l2_squad`，`execute_tactics_step` 接 sharpy `GroupCombatManager.execute` |
| **P0c** L1 cancel 走 board | 0.5d | 加 STRATEGY_CANCEL 分支到 `_apply_to_facade`，迁移 `_dispatch_cancel` 调用点 |
| **P0d** L4 done_when 扩词表 | 0.5d | 7 个新 kind 加到 done_when evaluator |
| **P0e** structure_override | 1d | 新 enum value + payload + `_exec_structure_override` + prereq table 扩 |
| **P0f** snapshot.command_cards | 0.5d | `build_snapshot` 改，统一 array 字段 |
| **P0g** WS revoke 全链路 | 0.5d | `_handle_revoke` + `Director.revoke_directive` + 各层清理 |
| **P0h** PWA CommandCardStack | 1d | 新组件 + CockpitView 替换 |
| **P0i** LLM prompt 改 | 0.5d | 加 structure_override + 7 done_when + A 类 done_when=None 规则 + B 类 unit_count_hint 必填规则 + 5 个 few_shot |
| **P0j** llm_eval 跑全套 | 0.5d | 含新 case：补 8 BG / 8 BG 转 IAC / ramp 2 cannon / 等闪烁 + scout |
| **P0k** e2e_4 重跑 + 加 verify_log | 1d | driver 强化 verify（grep stdout 找执行 log），新增 O 系列 case |

**合计 8 天**（5-6 工作日）。

实施顺序建议：P0a → P0d → P0c → P0e → P0f → P0g → P0b → P0h → P0i → P0j → P0k。
理由：先把 override flag 路径走通（A 类先动），再补 L4 扩词表（独立，互不阻塞），再做 L1 统一，最后做 squad（B 类）和 UI。任何一段独立可 ship。

---

## 7. 不在 MVP 范围（明确写出来防漂移）

| 项 | 推迟原因 |
|---|---|
| 维持型 directive lifecycle | 用户决策 "不补"，O2 后续单独议 |
| directive chain / on_done callback | O4/O5 拆两步玩家自说，MVP 不做 |
| enemy 事件触发 reactive directive (O6) | 需要 enemy_observation 事件流，独立大议题 |
| sharpy spend hook (O7) | 改动太深，价值密度低 |
| worker_distribute hook (O3/O12) | M5 范畴 |
| 集火点名 (C1) | 依赖 PWA 单位点选 UX |
| 软建议加权模式 | 用户决策 "不做" |
| L1 stage scope 玩家手动指定 cancel | 当前 cancel 默认 stage=all，OK |
| production_pause (O9) | 占位 in MVP 表但本设计未细化，实施时单独 ADR |

---

## 8. Open questions（实施时再敲定）

1. **VibeCraftZoneAttack.\_should\_attack 返回 False 时 sharpy 自己会把外面兵召回吗？** 还是要 director 主动 micro 召回？需查 sharpy retreat path
2. **GroupCombatManager.execute() 抢占了 LLM_CONTROLLED 的 unit 会不会报错？** sharpy 自己 add_unit() 时已过滤 LLM_CONTROLLED；execute() 是否独立 path 需测
3. **structure_override 的 location_hint 解析** —— "ramp" / "natural" / "front" 怎么算到 Point2？复用 sharpy 的 zone 系统？还是自己维护一张 hint → Point2 表？
4. **L4 done_when=structure_count 跟原 unit_count_built_since 的区分** —— prompt 怎么让 LLM 选对？"造 2 哨兵" vs "维持 2 哨兵" 玩家话术其实经常混用
4a. **squad 短缺时是否要"补足"** —— "派 5 个凤凰" 现在只有 3 个，先抢 3 个开干；后来新出 2 个凤凰是否自动加入这个 squad？MVP 决策：**不自动加入**（一致性：要补玩家二次说）。但 status 在新凤凰出现时是否提示"现在凑得齐 5 个了，要不要全派"是个 UX 议题
5. **revoke 频率限制** —— 玩家点 X spam 怎么防？同一 directive_id 第二次 revoke 静默 OK
6. **command_cards 跟现有 4 个独立字段并存还是直接替换** —— 前端兼容性

---

## 9. 引用

- `docs/adr/0010-four-layer-commands.md` — 4 层 directive 顶层
- `docs/directive-coverage-report-2026-05-17.md` — 现状评估 + 死路证据
- `src/vibecraft/bot/director.py:1533-1617` — `_apply_to_facade`
- `src/vibecraft/bot/facade.py` — Sc2Facade Protocol
- `src/vibecraft/bot/auto_combat/protoss/bot.py:333-387` — facade 实现
- `vendor/sharpy/sharpy/plans/tactics/zone_attack.py:79-329` — sharpy 攻击决策入口
- `vendor/sharpy/sharpy/combat/group_combat_manager.py:62-111` — 兵团 micro
