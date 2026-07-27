# 偷矿（Stealth Mining）实施 Plan

> **For Claude:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans / subagent-driven-development 逐 task 执行。
> 所有 code-writing subagent 用 **sonnet（Sonnet 4.6）**；本 session Opus 留作 brief + 两段 review + debug。

**Goal:** 玩家语音/文字指定地图一片区域，bot 在那偷偷开隐蔽基地、自给自足造农民采矿、纳入全局经济，可同时偷多片；偷矿点被攻击则撤销 stealth 地位交还 bot。

**Architecture:** 偷矿 cell = 隔离经济单元。承重墙是 `DistributeWorkers` 层 FENCE 双向隔离（stealth 农民 role=Reserved 防外流 + stealth townhall 从 work_queue 排除防倒灌）。建造复用代理建造链；农民就地自产（指定 stealth Nexus train）；账目分离（主矿管主矿 ideal）；受击解除 FENCE + 还 role，bot 既有撤离逻辑自动接管。设计真理源见 `docs/plans/2026-06-10-stealth-mining-design.md`。

**Tech Stack:** Python（pydantic directive schema / sharpy vendor patch / Sc2Facade Protocol 双实现）；pytest mock；真机 GameProcess 自验。

---

## 全局纪律（每个 WP 都适用，违反必炸真机/测不出）

1. **改 `Sc2Facade` 接口 → FakeFacade + `_SharpyFacadeBase` 双实现**（`facade.py` + `common_bot.py::_make_sharpy_facade_base_class`），改完跑 `tests/unit/test_facade_release_unit_role.py` 的 Protocol 一致性 audit。漏 `_SharpyFacadeBase` → 真机静默失效、单测全绿。
2. **新增 directive `kind` → 三处同步**：`directives/models.py`（payload class + 判别联合）、求值器（`task_monitor.py` done_when / `director.py::_is_activation_satisfied` activate_when，按用途）、LLM prompt（`docs/llm_prompt/rules.md` + `few_shot.md` + 重 dump）。
3. **sharpy vendor patch → 方案 D**：`# vibecraft:` marker + 进 `tests/unit/test_sharpy_patch_audit.py::PATCHED_METHODS` + `docs/sharpy-patches.md` + 行为单测。用 `getattr(getattr(self.knowledge,"vibecraft",None),"<field>",None)` 兜底。
4. **撤退/逃散用 `move` 不用 `attack_move`**（控制权模型规则 4）。
5. **commit message = CHANGELOG 同源**：每次 commit 前先写 `CHANGELOG.md [Unreleased]` 条目，commit message 用同样内容。
6. **结构化 JSONL 日志**：cell 状态变迁 / 产线 / FENCE 注册 / 释放，落 `logs/<game_id>/events.jsonl`，真机自验靠 greppable 前缀 `STEALTHTRACE`。

---

# WP0：修 `set_expansion_override` 真封顶（前置，独立可先合）

**为什么先做**：实测坐实它是 `pass` 空操作（expand=2 仍开到 3，`scripts/macro_action_selftest.py`）。偷矿要 bot 不自动开矿占偷矿点 + stealth Nexus 不污染自然扩张账。

### Task 0.1：`knowledge.vibecraft` 加字段

**Files:** Modify `src/vibecraft/bot/auto_combat/common_bot.py:1411`（`_SNS(...)` 块）

**Step 1-3:** 在 `_SNS(...)` 里加两个字段（无单测，纯数据声明，跟 Task 0.2 一起验）：
```python
# 2026-06-10 偷矿前置：玩家开矿封顶（None=不封，用剧本 expansion_cap）
expansion_cap_override=None,
# 偷矿 FENCE：所有 stealth cell 的 Nexus tag 集合（Expand 自然扩张账排除 + DistributeWorkers 排除）
stealth_townhall_tags=set(),
```

**Step 4:** `uv run pytest tests/unit/ -k facade -q`（确认没破现有）。
**Step 5:** 不单独 commit，跟 0.2 合。

### Task 0.2：`set_expansion_override` 写状态 + Expand gate 尊重封顶

**Files:**
- Modify `src/vibecraft/bot/auto_combat/common_bot.py:316`（`set_expansion_override`）
- Modify `vendor/sharpy/sharpy/plans/acts/expand.py:72`（`execute` 开头，`# vibecraft:` patch）
- Modify `tests/unit/test_sharpy_patch_audit.py`（PATCHED_METHODS 加 `Expand.execute`）
- Modify `docs/sharpy-patches.md`
- Test: `tests/unit/test_sharpy_vibecraft_hooks.py`（新增 gate 行为测）

**Step 1: 写失败测**（`test_sharpy_vibecraft_hooks.py`）：
```python
def test_expand_respects_cap_override(monkeypatch):
    """expansion_cap_override=2 且已有 2 非 stealth 基地 → Expand.execute 不再开矿（return True）。"""
    # mock Expand 实例：knowledge.vibecraft.expansion_cap_override=2,
    # current_active_base_count=2, stealth_townhall_tags=set()
    # 断言 execute() return True 且没调 build_expansion
```

**Step 2:** `uv run pytest tests/unit/test_sharpy_vibecraft_hooks.py::test_expand_respects_cap_override -x` → FAIL。

**Step 3: 实现**

`set_expansion_override`：
```python
def set_expansion_override(self, target_count: int | None) -> None:
    # None = 撤销封顶，回剧本默认 expansion_cap
    self.bot.knowledge.vibecraft.expansion_cap_override = target_count
```

`expand.py::execute` 开头（`active_bases = self.current_active_base_count` 之后）：
```python
# vibecraft: 玩家开矿封顶 + stealth 基地不计入自然扩张账
_vc = getattr(self.knowledge, "vibecraft", None)
_cap = getattr(_vc, "expansion_cap_override", None)
if _cap is not None:
    _stealth = getattr(_vc, "stealth_townhall_tags", set())
    _stealth_zones = sum(
        1 for z in self.zone_manager.our_zones_with_minerals
        if z.our_townhall is not None and z.our_townhall.tag in _stealth
    )
    _nonstealth_bases = active_bases - _stealth_zones
    if _nonstealth_bases >= _cap:
        self.clear_worker()
        return True
```
（`Zone.our_townhall` 若无此属性，改用 `z.center_location` 到 stealth Nexus 距离判定；实现时确认 Zone API。）

**Step 4:** 跑 `uv run pytest tests/unit/test_sharpy_vibecraft_hooks.py tests/unit/test_sharpy_patch_audit.py -v` → PASS。
**Step 5: Commit**（CHANGELOG 同源）：`fix(macro): 开矿封顶真生效 + stealth 基地不污染自然扩张账（set_expansion_override 不再空操作）`。

### Task 0.3：`apply_macro_action` 撤销路径 + director 喂 stealth_townhall_tags 占位

**Files:** Modify `src/vibecraft/bot/director.py`（`apply_macro_action` expand 分支；`_exec_expansion_override` 已 OK）；确认 expand=default/clear 调 `set_expansion_override(None)`。

**Step 1-4:** 单测 `tests/unit/test_director.py`：expand=N → facade.set_expansion_override(N) 被调；expand 撤销 → set_expansion_override(None)。用 FakeFacade 记录调用。
**Step 5: Commit** 合进 0.2 或单独。

### Task 0.4：真机回归（封顶有效）

**Files:** Modify `scripts/macro_action_selftest.py`（已存在）：判定逻辑改成"expand=2 注入后峰值 bases ≤ 2 = PASS"。

**Step 1-4:** 跑 `.venv/Scripts/python.exe scripts/macro_action_selftest.py --seconds 480 --inject-after 5 --opening iac_2base`，确认 expand2 局峰值 bases ≤ 2（修复前是 3）。
**Step 5:** 不 commit 代码（脚本是验证工具），结果记 CHANGELOG。

---

# WP1：schema + StealthCell / Manager 骨架 + 状态机（依赖 WP0）

### Task 1.1：`StealthMinePayload` schema（三处同步第 1 处）

**Files:** Modify `src/vibecraft/directives/models.py`；Test `tests/unit/test_done_when_models.py` 或新 `test_stealth_models.py`

**Step 1: 写失败测**：构造 `{"kind":"stealth_mine","point":[50,50],"cell_id":1}` 能被 directive payload 联合解析；非法（缺 point）报错。
**Step 2:** 跑 → FAIL（kind 未注册）。
**Step 3: 实现**：
```python
class StealthMinePayload(BaseModel):
    kind: Literal["stealth_mine"]
    point: tuple[float, float]
    cell_id: int = 0                 # Manager 分配回填
    worker_target: int = 16
    with_gas: bool = True
    on_attack: Literal["flee", "hold"] = "flee"
```
加进 directive payload 判别联合（找 `DirectivePayload = Union[...]` / `Annotated[..., Field(discriminator="kind")]`）。
**Step 4:** 跑 → PASS。
**Step 5: Commit**：`feat(stealth): stealth_mine directive schema`。

### Task 1.2：`StealthCell` dataclass + `StealthState` 枚举

**Files:** Create `src/vibecraft/bot/stealth/cell.py`；Test `tests/unit/test_stealth_cell.py`

**Step 1-4:** TDD：`StealthState = Enum(PENDING|BUILDING|MINING|RELEASED|DESTROYED)`；`StealthCell` dataclass（§5.2 字段）；测构造默认值 + `alive_workers(bot)` helper（过滤死亡 tag）。
**Step 5: Commit**：`feat(stealth): StealthCell 状态容器`。

### Task 1.3：`StealthCellManager` 骨架 + cell_id 分配 + 状态机 driver 空壳

**Files:** Create `src/vibecraft/bot/stealth/manager.py`；Test `tests/unit/test_stealth_manager.py`

**Step 1-4:** TDD：`create_cell(payload) -> cell_id`（自增 id，回填 payload.cell_id）；`cells: dict[int, StealthCell]`；`stealth_townhall_tags` / `stealth_worker_tags` property（所有 cell 并集）；`on_tick(bot)` 空壳（后续 WP 填）。测多 cell id 不重复、并集正确。
**Step 5: Commit**：`feat(stealth): StealthCellManager 骨架 + cell_id 分配 + tag 并集`。

### Task 1.4：Director 接线 Manager + apply stealth_mine → 建 cell（PENDING）

**Files:** Modify `src/vibecraft/bot/director.py`（持 manager；apply 时 `stealth_mine` → `manager.create_cell`；`on_tick` 调 `manager.on_tick`）；Test `tests/unit/test_director.py`

**Step 1-4:** TDD：apply 一条 stealth_mine directive → manager 多一个 PENDING cell；on_tick 调到 manager。
**Step 5: Commit**：`feat(stealth): Director 接线 StealthCellManager`。

---

# WP2：建造链——复用代理建造起 stealth Nexus（依赖 WP1）

### Task 2.1：PENDING → BUILDING（claim 农民 + 派去 point + 下 Nexus by_probe）

**Files:** Modify `src/vibecraft/bot/stealth/manager.py`（状态机 PENDING 分支）；可能 Modify `director.py`（复用代理建造：claim probe + `build_at(by_probe, point, structure_type=NEXUS)`）；Test `tests/unit/test_stealth_manager.py`

**实现要点**（复用已有代理建造机制，不新写建造执行器）：
- PENDING：claim 1 农民（`unit_claim` persistent）→ 派去 `cell.point`（记 `builder_tag`）。
- 下一张 `build_at(by_probe=True, point=cell.point, structure_type="Nexus", activate_when=unit_arrived(point))` → 农民到点用 `near_point` 选到它下 Nexus。
- cell → BUILDING。

**Step 1-4:** TDD（mock facade）：PENDING cell on_tick → facade 收到 claim + build_at(by_probe, NEXUS, point)；state→BUILDING。
**Step 5: Commit**：`feat(stealth): 代理建造起 stealth Nexus（claim probe + build_at by_probe）`。

### Task 2.2：BUILDING → MINING（Nexus settle 回填 nexus_tag + 注册 FENCE）

**Files:** Modify `manager.py`（监听 Nexus settle：在 cell.point 附近出现己方 ready NEXUS → 回填 `nexus_tag`，builder 转本地农民）；Test 同上

**Step 1-4:** TDD：BUILDING cell，bot 在 point 附近有 ready NEXUS → `nexus_tag` 回填，builder_tag 农民加入 `worker_tags` + role=Reserved，state→MINING，调 `facade.register_stealth_townhalls(stealth_townhall_tags)`。
**Step 5: Commit**：`feat(stealth): Nexus settle → MINING + 注册 FENCE`。

---

# WP3：FENCE——双向隔离（依赖 WP1，可与 WP2 并行）

### Task 3.1：facade `register_stealth_townhalls` + `train_probe_at`（双实现）

**Files:** Modify `src/vibecraft/bot/facade.py`（Protocol + FakeFacade）；Modify `src/vibecraft/bot/auto_combat/common_bot.py`（`_SharpyFacadeBase`）；Test `tests/unit/test_facade_release_unit_role.py`（Protocol audit）+ 新行为测

**实现**：
```python
# Protocol + 双实现
def register_stealth_townhalls(self, tags: set[int]) -> None: ...
    # _SharpyFacadeBase: self.bot.knowledge.vibecraft.stealth_townhall_tags = set(tags)
def train_probe_at(self, nexus_tag: int) -> bool: ...
    # _SharpyFacadeBase: 找 tag 对应 NEXUS，nexus.train(PROBE) if can_afford & idle
```
**Step 1-4:** TDD + 跑 Protocol audit（`_SharpyFacadeBase` 实现全部公开方法）。
**Step 5: Commit**：`feat(stealth): facade register_stealth_townhalls + train_probe_at（双实现）`。

### Task 3.2：`DistributeWorkers` vendor fence patch（防倒灌）

**Files:** Modify `vendor/sharpy/sharpy/plans/tactics/distribute_workers.py:213`（`generate_worker_queue` 遍历 townhall 处）；Modify `tests/unit/test_sharpy_patch_audit.py`；Modify `docs/sharpy-patches.md`；Test `tests/unit/test_sharpy_vibecraft_hooks.py`

**实现**（`# vibecraft:` marker）：
```python
for building in self.ai.gas_buildings + self.ai.townhalls:
    # vibecraft: 偷矿基地不进全局工作队列（防主矿农民倒灌 + 其农民不被全局调度）
    _stealth = getattr(getattr(self.ai, "vibecraft", None), "stealth_townhall_tags", set())
    if building.tag in _stealth:
        continue
    ...
```
（注意：fence 读 `self.ai.vibecraft`，但 vibecraft namespace 挂在 `knowledge.vibecraft`。确认 DistributeWorkers 能拿到 knowledge —— sharpy Act 有 `self.knowledge`，但这里是 `self.ai`。实现时改用 `self.knowledge.vibecraft` 或在 ai 上加镜像。优先 `self.knowledge`。）

**Step 1-4:** TDD：mock 一个 stealth townhall tag ∈ stealth_townhall_tags → 它不进 `work_queue`；普通 townhall 照常进。跑 patch audit。
**Step 5: Commit**：`feat(stealth): DistributeWorkers fence patch（stealth 基地排除出全局工作队列，防倒灌）`。

---

# WP4：本地产线 + 账目分离（依赖 WP2、WP3）

### Task 4.1：MINING 态本地补农民（< target → train_probe_at + role=Reserved + 本地采矿）

**Files:** Modify `manager.py`（MINING 分支）；Test `tests/unit/test_stealth_manager.py`

**Step 1-4:** TDD：MINING cell，`len(alive worker_tags) < worker_target` 且 stealth Nexus 空闲 → `facade.train_probe_at(nexus_tag)`；新农民 role=Reserved + 入 worker_tags + gather 本地最近矿。气矿（with_gas）同理补到 3。
**Step 5: Commit**：`feat(stealth): MINING 本地自产农民（指定 Nexus train + Reserved + 就地采矿）`。

### Task 4.2：账目分离——主矿产线/饱和排除 stealth（方案①）

**Files:** Modify `src/vibecraft/bot/director.py:3974`（`_tick_worker_saturation`：cap 用 `Σ ideal(非 stealth townhall)`，need 用 `非 stealth 农民`）；考虑 PersistentMacro `ActUnit(PROBE)` cap（若用总农民数，stealth 农民会顶掉主矿产能 → 评估是否需排除，记 ADR）。Test `tests/unit/test_director.py`

**Step 1-4:** TDD：有 stealth townhall + stealth 农民时，`_tick_worker_saturation` 的 cap/need 不含 stealth 部分（主矿仍补到主矿 ideal，不因 stealth 农民被算进总数而少补）。
**Step 5: Commit**：`feat(stealth): 账目分离——主矿农民饱和计算排除 stealth cell（防双重生产/主矿欠饱和）`。

---

# WP5：受攻击 → 撤销 stealth 地位、交还 bot（依赖 WP2）

### Task 5.1：MINING → RELEASED（敌近检测 + 解除 FENCE + 还 role + cell 出局）

**Files:** Modify `manager.py`（MINING 检测敌近；RELEASED 处理）；Test `tests/unit/test_stealth_manager.py`

**实现**（§8 定稿）：
- 检测：stealth Nexus 半径 R 内有敌方非农民单位 → RELEASED。
- RELEASED 三件事：① `stealth_townhall_tags` 去掉该 Nexus（下个 tick `register_stealth_townhalls` 刷新）；② `worker_tags` 全部 `facade.set_unit_role(tag, default)` + 撤销在身指令（规则 3）；③ cell 从 manager 移除。
- **不手写逃散**：解除 FENCE 后 bot `DistributeWorkers` 的 `zone.is_enemys`/`needs_evacuation` 自动撤农民。
- Nexus 被摧毁（nexus_tag 不在 bot.structures）→ DESTROYED（同样释放残余农民）。

**Step 1-4:** TDD：MINING cell 注入"敌方单位近 Nexus" → state→RELEASED，facade 收到 set_unit_role(default) ×N，cell 从 manager 移除，stealth_townhall_tags 不再含该 Nexus。`on_attack="hold"` → 不释放。
**Step 5: Commit**：`feat(stealth): 受击撤销 stealth 地位（解除 FENCE + 还 role），bot 自动接管`。

---

# WP6：多 cell 并行 + snapshot + PWA（依赖 WP4、WP5）

### Task 6.1：多 cell 并行验证（无串台）

**Files:** Test 强化 `tests/unit/test_stealth_manager.py`（2+ cell 各自独立 FENCE/产线/释放，worker_tags 不重叠）。
**Step 1-5:** TDD only（逻辑应已支持，补覆盖）；Commit：`test(stealth): 多 cell 并行不串台`。

### Task 6.2：snapshot 带 stealth_cells

**Files:** Modify `src/vibecraft/bot/director.py::build_snapshot`（加 `stealth_cells: [{cell_id, location, worker_count, state, has_gas}]`）；Test `tests/unit/test_director.py`
**Step 1-5:** TDD；Commit：`feat(stealth): snapshot 透传 stealth_cells（喂 PWA）`。

### Task 6.3：PWA 显示偷矿点

**Files:** Modify `web/src/`（snapshot 类型 + 一个小面板/地图标记列 stealth cells）；`web/src/types.ts`；对应 vitest。**build 用 PowerShell `npm run build`**（Bash 会杀 vite → 白屏）。
**Step 1-5:** TDD（vitest）+ `cd web; npm run build`（PowerShell）；Commit：`feat(stealth): PWA 显示偷矿点列表（cell_id/位置/农民数/状态）`。

---

# WP7：LLM prompt + 真机自验（依赖 WP6）

### Task 7.1：LLM prompt（三处同步第 3 处）

**Files:** Modify `docs/llm_prompt/rules.md`（stealth_mine kind 说明 + 当前镜头点用法）；`docs/llm_prompt/few_shot.md`（"去对方三矿偷个矿"/"在这偷矿"/"偷两个点"示例，含 cell_id 多片）；重 dump `.venv/Scripts/python.exe scripts/dump_llm_prompt.py`
**Step 1-5:** 改文件 → dump → `.venv/Scripts/python.exe scripts/voice_spot_check.py` 加偷矿 case 验解析；Commit：`feat(stealth): LLM prompt 支持 stealth_mine（含多片偷矿 + 镜头点）`。

### Task 7.2：真机自验 harness

**Files:** Create `scripts/stealth_mine_selftest.py`（proxy_chain_selftest 式：mock LLM + non-realtime 并行）

**验证点**（grep `STEALTHTRACE`）：
- 注入"去 X 偷矿" → Nexus 在 X settle；
- 本地农民自产到 target（train_probe_at 命中、role=Reserved）；
- **主矿农民数不掉**（无倒灌，对比 baseline）；
- 注入第二个点 → 两 cell 独立；
- 模拟敌近 stealth Nexus → 农民 role 还 default + bot 撤离（基地从 stealth_townhall_tags 移除）。

**Step 1-5:** 写 harness → 跑（non-realtime 并行，~2-3 min）→ 全 PASS；结果记 CHANGELOG。Commit：`test(stealth): 代理偷矿链真机自验 harness`。

---

## 执行顺序与依赖图

```
WP0（前置，独立先合）
 └─ WP1（schema+骨架）
     ├─ WP2（建造链）──┐
     └─ WP3（FENCE）───┼─ WP4（产线+账目分离）─┐
                       │                        ├─ WP6（多cell+UI）─ WP7（prompt+自验）
                       └─ WP5（受击释放）────────┘
```

- WP2、WP3 可并行（都只依赖 WP1）。
- WP4 依赖 WP2+WP3；WP5 依赖 WP2。
- 每个 WP 跑完跑 `uv run pytest tests/unit/ -q` + `uv run ruff check . && uv run mypy src/vibecraft`。
- WP0 / WP7.2 各有真机自验；中间 WP 全 mock 单测，无需起 SC2。

## 最终验收（全 WP 完成后）

1. `uv run pytest`（全绿）+ `ruff` + `mypy`。
2. `scripts/macro_action_selftest.py`（开矿封顶 expand=2 ≤ 2）。
3. `scripts/stealth_mine_selftest.py`（偷矿链全 PASS + 无倒灌 + 多 cell + 受击交还）。
4. 真 server + 手机 PWA 端到端：语音"在对方三矿偷个矿" → 看 Nexus 起、农民自产、显示偷矿点；模拟骚扰 → 交还 bot。（喊用户做这步。）
