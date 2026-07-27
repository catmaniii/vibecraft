# 人族建筑起飞/移动 + 农民基地调度命令 — 设计文档

> 2026-07-08。用户 4 个人族命令现全不工作，实现之。独立评审前的真理源。

## 用户补充（2026-07-08，STRUCTURE_MOVE 增强）

1. **起飞后可单独指挥飞/降**（不只一条命令搞定）："基地飞到三矿"、"降落在这里/这片矿" 要能对**已在飞的**
   基地生效。→ STRUCTURE_MOVE 的 **FIND 要同时找已降落的 townhall 和已在飞的**（`structures(*FLYING)`）；
   建筑**已在飞** → 跳过 LIFT 直接 FLY/LAND。"降落在X"=找飞行 townhall + LAND 到 X；"飞到X"=飞行 townhall move 到 X（可后续再降）。
2. **降落对齐最优采矿位（关键，别偏）**：`to_spot` 是矿区/扩张点（"这片矿"/"三矿"/"二矿"/natural/镜头点）时，
   LAND 落点**必须 snap 到该扩张的最优 townhall 采矿位**，不是随便一个 can_place 空点。用已有
   **`snap_townhall_point(resolved_point, bot)`**（`common_bot.py:1445`，返回 `(snapped_pt, did_snap)`）
   或 **`closest_expansion_location(point, bot)`**（`director.py:3266`）先对齐到最近扩张的标准 townhall 格位，
   再 LAND 到那。"三矿"=named_spot `third`（别名已有）。**"这片矿"=镜头点 → closest_expansion_location → snap**。
   只有该 snap 点被占/不可建时才退化就近扫（#543），否则一律落在最优采矿位。

## 评审处置（2026-07-08 opus，全部采纳 — 实现按这里为准）

opus 真机核对，总评"需改后可行"。7 个 must-fix 全采纳：

1. **主基地类型泛化（致命，真机核对）**：人族主基开局后立刻升 **OrbitalCommand**（最常见）或 **PlanetaryFortress**，
   **不是 CommandCenter**。真机 enum：`LIFT_/LAND_COMMANDCENTER`✅、`LIFT_/LAND_ORBITALCOMMAND`✅、
   `COMMANDCENTERFLYING`/`ORBITALCOMMANDFLYING`✅、但 **`LIFT_PLANETARYFORTRESS` 不存在（星球不能起飞）**。
   → STRUCTURE_MOVE 的 FIND 取 `from_spot` 最近的 **townhall（CommandCenter∪OrbitalCommand∪PlanetaryFortress）**，
   按其**真实 type_id** 取 `LIFT_<真type>`；是 PlanetaryFortress → **友好拒绝**（"星球要塞不能起飞"）。
   `structure_type` 字段**不硬绑 CommandCenter**，可空/由实际选中的 townhall 决定。飞行变体两种都纳入。
2. **复用 #543 状态机**：`director.py:6651-6767` 的 `_build_addon_on_parent` 已是真机验证的 LIFT→FLY→LAND
   状态机（`land_targets` 按 **tag** 缓存、遍历 `structures(parent)+structures(flying)`、`is_flying`、
   `can_place_single` 双验）。**抽公共 helper 复用**，STRUCTURE_MOVE 调它，别平行新写。
3. **prioritize_minerals/gas 复用全局，不做 per-base**：项目已有全局 `facade.set_mining_priority("mineral"/"gas"/None)`
   （宏观面板 mining 维度 + DistributeWorkers patch，`value="mineral"`=`max_gas=0`=优先水晶）。WORKER_TASK 的
   `prioritize_minerals`/`prioritize_gas` **直接调它**（语音入口→全局），**删掉 `mineral_priority_bases` per-base set +
   第二处 sharpy patch**（YAGNI + 已有等价 + 避免第二 patch 点冲突）。备注给玩家：当前是全局优先（单基阶段等价"主矿"）。
4. **transfer 语义修正**：`DistributeWorkers` 每帧按 `ideal_harvesters` 再平衡 → 一次性 gather 会被拉回。
   改成**持续 tick 数帧**（或短暂 Reserved）把选中农民钉在二矿采矿，跑够 N 帧/若干秒后释放交还 bot 平衡。
   农民过滤：**只选 Gathering role + 非 Reserved + 非采气 + 非在建**的采矿农民（"全部"=该基地所有采矿农民）。
5. **飞行追踪：tag 不变、只变 type_id**（#543 真机坐实）。用 `land_targets` 同 tag 缓存 + 查询并入
   `structures(<TYPE>FLYING)`，**别用位置最近猜**（删设计里"换 tag/位置追踪"的错误描述）。
6. **落点/降落语义**：`to_spot` 有 → 飞到 natural（=named_spot natural 解析点）**降落采矿位**；`can_place_single`
   由近及远扫落点（复用 #543 `_find_relocate_spot`）；二矿已被占/扫不到贴矿空位 → 退化为**悬停 + 提示**（不硬降到采不到矿的远点）。
7. **三处同步 + 命名纠正**：`models.py` 判别联合真名是 **`Payload`**（不是设计写的 `AnyDirectivePayload`，`PAYLOAD_MODELS` 自动派生）。
   加新类型四处：① `types.py` enum ② `models.py` `Payload` 联合 ③ director 执行分支 ④（无 done_when → 不用 task_monitor）。

**UNVERIFIED 纠正**：`ai.expansion_locations` **不是可用属性**（只有 `expansion_locations_list` 无序不能 [1]、
`expansion_locations_dict` 慢）。→ **不用它**，二矿 townhall 格位直接用已验证的 `NamedSpotRegistry.resolve("natural")`
（=`expansion_zones[1].center_location`，就是可建 townhall 格位）。**需真机 spot-check**（低风险）：CC/Orbital 起飞后 tag
不变（#543 已在兵营/重工/星港坐实，CC 类推，自验脚本确认终态）。

## 需求（用户 2026-07-08 + 拍板）

4 句人族命令现在全不工作（真 LLM 解析确认：全误解析或空）：
1. **"主基地飞起来"** → 现误解析成 unit_claim。要：CommandCenter 起飞悬停。
2. **"主基地飞到二矿"** → 现误解析成 build_at Nexus（想新建）。要：主基 CC 起飞 → 飞到**二矿扩张点(台阶上同主基位)** → 降落。
3. **"主矿的农民优先采水晶矿"** → 现返回空（LLM 明说没对应类型）。要：主矿农民**持续**优先采水晶（不采气），直到取消。
4. **"主矿的农民去二矿采矿"** → 现半吊子。要：把主矿**全部**采矿农民调去二矿采矿。

## 现有可复用基础设施（取证确认）
- **LIFT/LAND ability**：`AbilityId[f"LIFT_{name}"]`/`LAND_{name}`（#543 挂件挪位已用，`director.py:6672`）。建筑起飞变 `<TYPE>FLYING`（另一 UnitTypeId）。
- **`facade.cast_unit_ability(unit_tag, ability_id, target)`**：两实现都有（`facade.py:496` FakeFacade + `946` _SharpyFacadeBase）。
- **`facade.order_worker_gather(tag, near_point)`** / `order_worker_gather_gas`。
- **named_spot 解析**：`_named_spot_point`（main/natural → Point2）。二矿扩张点用 `ai.expansion_locations`（真townhall格位，非 `_resolve_hint` 的"远离矿区"点）。
- **全局 `set_mining_priority`**（`DistributeWorkers.execute` patch 读 `knowledge.vibecraft.mining_priority`）——**per-base 版要在此 patch 上加"某基地排除采气"**（见下 WORKER_TASK 执行）。
- 目标点一次锁定别每帧重选（#543）。

## 两个新 directive 类型

### A. `STRUCTURE_MOVE`（人族建筑起飞/飞/降）

```python
class StructureMovePayload(_PayloadBase):
    type: Literal[DirectiveType.STRUCTURE_MOVE]
    structure_type: str          # "CommandCenter" / "Barracks"...(人族可起飞建筑)
    from_spot: str               # named_spot：起飞哪座（"main"）
    to_spot: str | None = None   # named_spot 目标；None=原地起飞悬停
```
- "主基地飞起来" → `(CommandCenter, from=main, to=None)`。
- "主基地飞到二矿" → `(CommandCenter, from=main, to=natural)`。

**执行**（director 持续 tick，每指令一个小状态机，目标点一次锁定）：
1. **FIND**：`structures(structure_type).closest_to(from_spot点)` → 缓存 tag。校验人族+可起飞（`LIFT_<TYPE>` enum 存在），否则友好拒绝。
2. **LIFT**：`cast_unit_ability(tag, LIFT_<TYPE>)`。等它变 `<TYPE>FLYING`（换 tag，用位置/最近追踪飞行体）。
3. **to_spot=None** → 悬停即完成（done，卡留待玩家后续，× 取消）。
4. **to_spot 有** → **FLY**：目标 = `ai.expansion_locations` 里距 natural 解析点最近的扩张 townhall 格位（一次锁定缓存）。move 飞行体过去。
5. **LAND**：飞行体到目标附近 + `can_place_single(<TYPE>, 目标)` 为真 → `cast_unit_ability(飞行tag, LAND_<TYPE>, 目标)`。落成 → done。落点被占（如二矿已有基地）→ 落到最近空位（`can_place` 由近及远扫，同 #543 `_find_relocate_spot`）。
- **纪律**：飞行建筑换 tag（`structures(type)` 不含 flying），追踪要带飞行变体。每帧幂等重发同一目标（别漂移）。

### B. `WORKER_TASK`（农民基地调度）

```python
class WorkerTaskPayload(_PayloadBase):
    type: Literal[DirectiveType.WORKER_TASK]
    from_base: str               # named_spot：哪个基地的农民（"main"）
    action: Literal["prioritize_minerals", "prioritize_gas", "transfer_to_base"]
    to_base: str | None = None   # transfer 用
```
- "主矿农民优先采水晶" → `(from=main, action=prioritize_minerals)`（**持续**）。
- "主矿农民去二矿采矿" → `(from=main, action=transfer_to_base, to=natural)`（一次性，全部）。

**执行**：
- **transfer_to_base（一次性，全部）**：`from_base` townhall 附近的**所有采矿农民** → `order_worker_gather(tag, to_base 矿patch点)`。下完即 done（不持续 claim；农民到二矿后归 bot 采矿池）。
- **prioritize_minerals（持续）**：**关键 = per-base 采气排除**。记 `knowledge.vibecraft.mineral_priority_bases: set[townhall_tag]`（from_base 的 townhall tag）。
  - `DistributeWorkers.execute` patch（vendor，已有 mining_priority hook 处）**加**：分配采气时，跳过 `mineral_priority_bases` 里 townhall 附近的农民（不把它们塞进气矿）→ 该基地气工自然回流采矿。`# vibecraft:` marker + audit + docs/sharpy-patches。
  - director 持续 tick：把该 townhall 附近**正在采气**的农民 `order_worker_gather` 回矿（加速回流，不等 DistributeWorkers）。× 取消 → 从 set 移除，恢复默认。

## LLM prompt（三处同步）
- `docs/aliases/*`：verb 消歧——"飞起来/起飞/飞到" → structure_move；"优先采水晶/优先采矿/去X采矿/农民调去" → worker_task。人族建筑名（主基地=CommandCenter）。
- `rules.md`：两新 directive 说明 + from_spot/to_spot/action 字段 + 只人族建筑可起飞。
- `few_shot.md`：4 句示例（起飞/飞二矿/优先水晶/转二矿）。重 dump `scripts/dump_llm_prompt.py`。

## facade 纪律
新增 facade 方法（若需，如 `lift_structure`/`land_structure` 封装，或直接用 `cast_unit_ability`）→ **两实现 + Protocol audit**。倾向直接用现有 `cast_unit_ability`（已两实现），不新增 facade 方法。

## 验证
1. **单测**：payload schema（新类型进 `AnyDirectivePayload` 判别联合）、解析（真 LLM 4 句 → 正确类型 + 字段，见诊断脚本）、执行器状态机（mock：LIFT→FLY→LAND；transfer 下 gather；prioritize 写 set）、DistributeWorkers patch audit + hook 行为单测。
2. **真局自验**（新脚本 `terran_cmd_selftest.py`，mock LLM 注入 4 句，realtime 或 fast）：
   - 起飞：注入"主基地飞起来" → telemetry/日志确认 CC 变 FLYING（**终态**，非 trace）。
   - 飞二矿：确认 CC settle 在二矿扩张点（离二矿点近 + 变回非 flying）。
   - 优先水晶：注入后跑一段 → telemetry 该基地 `gas_workers` 降到 ~0（**终态**）。
   - 转二矿：注入后 → 二矿 townhall 采矿农民数上升、主矿降。
   - per-instance 断言，别聚合。
3. `ruff`/`mypy` + 四文档同步（USER_GUIDE 加 4 句话术 + ARCHITECTURE 数据流）。

## 待评审确认点
1. `ai.expansion_locations` 是否给的是**可降落的 townhall 格位**（真机核对，别望文生义——salvage 纪律）。二矿扩张点已被自己基地占时的降落回退。
2. DistributeWorkers patch 加"per-base 排除采气"是否会跟全局 mining_priority / 偷矿农民排除冲突（读 patch 现状）。
3. 建筑起飞后 tag 变 FLYING 的追踪：靠位置最近还是 `structures(<TYPE>FLYING)`（#543 教训）。
4. transfer "全部农民" 会不会把在建/在修的农民也抓走（应只抓采矿的 idle/mining 农民）。
