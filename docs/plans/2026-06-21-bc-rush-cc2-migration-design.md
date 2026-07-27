# #560 bc_rush 二矿地堡迁移 — 设计方案

> 状态：设计草案，待独立评审 + 用户 go/no-go。2026-06-21。

## 需求（用户原话）

bc_rush 二矿地堡迁移：**CC2 家建 → 飞二矿 → 迁防御 → 回收主基地堡**。

拆解为 4 步：
1. **CC2 家建**：第二个指挥中心先在**主基地**附近建（不是直接在二矿建）。
2. **飞二矿**：CC2 建好后起飞（LIFT），飞到二矿（natural）落地（LAND）。
3. **迁防御**：在二矿建一个地堡（把防御从主基迁到二矿）。
4. **回收主基地堡**：主基那个早期地堡（RampBunkerAct 建的）此时多余，salvage 回收退资源。

**动机**：bc_rush 全压大舰，二矿早期无防御。若 CC2 直接在暴露的二矿建（71s 建造期），易被
对方 poke 打掉。家建更安全（躲在主基斜坡口地堡后），建好再飞过去；同时主基地堡完成历史使命
后回收退矿。

## 现状（explorer 已核实）

| 组件 | 现状 |
|---|---|
| 主基地堡 | ✅ `RampBunkerAct`（bc_rush.py:96-151），FusionCore 后建在主基斜坡口高地边缘 |
| CC2 | ✅ `Expand(2)`（bc_rush.py:223，3 大舰后）—— **直接在二矿建**，不是家建后飞 |
| 起降基础设施 | ⚠️ 仅挂件用（`_build_addon_on_parent`，BARRACKS/FACTORY/STARPORT）；**CC 起降未实现** |
| salvage | ✅ 可按 selector 程序化触发（director SALVAGE 分支 + `_tick_pending_salvage` 卸兵后拆）|
| 防御迁移 | ❌ 不存在 |

## 关键设计决策

### 决策 1：CC2 是否真的"家建再飞"？（核心，YAGNI 审视）

| 方案 | 描述 | 代价 | 收益 |
|---|---|---|---|
| **A 全量（用户原意）** | CC2 家建 → LIFT → 飞二矿 → LAND → 二矿建地堡 → 回收主基地堡 | 需新实现 CC 起降编排 + 防御迁移 + salvage 时序；改动 bc_rush expansion 行为；真局迭代成本高 | CC 建造期安全（躲主基）；退矿；防御跟随二矿 |
| **B 简化（同结果少飞行）** | CC2 仍 `Expand(2)` 直接二矿建，但二矿建好后建地堡 + 回收主基地堡 | 不动 CC 路径（低风险）；复用 salvage | 防御迁移 + 退矿；**但放弃"家建避骚扰"** = 丢了用户的核心动机 |
| **C 不做** | 维持现状（Expand(2) 二矿直建，主基地堡常驻） | 0 | 0；bc_rush 已通过验收 |

**权衡**：用户明确说"CC2 家建→飞二矿"，所以核心是 A 的飞行编排。B 放弃了避骚扰动机
（用户要的就是那个）。C 是 YAGNI 底线（现状能打）。

**风险点**：A 改了 bc_rush 的 expansion 行为。bc_rush 当前 7/7 验收通过、真局能赢。CC 起降编排
若有 bug（飞起来落不下 / 追移动靶抽搐 —— CLAUDE.md「目标点一次规划锁定」铁律正是这类坑），
会拖累二矿成型、影响整局。必须**严格 gate + 失败回退**：任何环节失败都退回"正常二矿直建"，
绝不卡死。

**推荐**：**A，但分阶段 + 强 gate**。先实现并真局验证「CC 起降」这一最高风险环节（独立 act +
落点起飞前锁定），通过后再接「二矿建地堡 + 回收主基地堡」（后两步复用现有 salvage + 地堡 act，
低风险）。若 CC 起降真局不稳，**降级到 B**（保住防御迁移 + 退矿的 80% 价值，放弃飞行）。

### 决策 2：CC 起降编排（最高风险，套用 #543 挂件起降的经验）

CLAUDE.md 强规则「目标坐标一次规划、锁定、别每帧重选」—— #543 挂件挪位楼起飞后每帧拿漂移坐标
重算落点 → 追移动靶落不下，就是这个坑。CC 飞二矿必须：
- **落点 = 二矿 expansion 的 `center_location`，起飞前就锁定**（确定性，不每帧 find_placement）。
- 状态机（per CC2 tag 缓存）：
  - `building` → CC2 在主基建造中，等 ready。
  - `ready_at_home` → 建好，发 `LIFT_COMMANDCENTER`，状态转 `lifting`，**此刻就把落点锁进缓存**。
  - `flying` → 是 `COMMANDCENTERFLYING`，每帧幂等发 `move(锁定落点)`；到落点附近发 `LAND_COMMANDCENTER`（对锁定点）。
  - `landed_at_natural` → 落地完成 → 触发后两步（建二矿地堡 + 回收主基地堡）。
- ability：`LIFT_COMMANDCENTER` / `LAND_COMMANDCENTER`（需真机 `get_available_abilities` 核对，
  标 UNVERIFIED；CC 飞行变体 = `COMMANDCENTERFLYING`）。

### 决策 3：何时触发整条链

bc_rush 现在 `Step(UnitExists(BATTLECRUISER,3), Expand(2))`。改为：CC2 在**主基**建（GridBuilding
COMMANDCENTER 在 start_location 附近，而非 Expand 在二矿点），gate 同样在 3 大舰后。CC2 ready →
起飞飞二矿。这替换掉 Expand(2)。

**回退**：若 CC2 家建/起飞链任一步超时（如 CC ready 后 N 秒没起飞成功），降级直接把 CC 当二矿
（就地 land 在最近 expansion）或回退 sharpy Expand —— 保证二矿一定成型。

### 决策 4：迁防御 + 回收主基地堡（低风险，复用现有）

- **二矿地堡**：CC2 落地 natural 后，在 natural 建 1 地堡（复用 RampBunkerAct 模式，但落点=natural
  斜坡口 / 矿后）。或更简单：DefensiveBuilding 思路在 natural index 建 Bunker。
- **回收主基地堡**：二矿地堡 ready 后，对主基那个 bunker tag 发 salvage（复用 director SALVAGE
  路径 / `_tick_pending_salvage`，自动先卸兵）。退 75% 矿。

## 实现落点（初版，待评审确认）

1. 新 act `Cc2MigrateAct`（bc_rush 专用）：状态机驱动 CC2 家建→飞→落→建二矿地堡→回收主基地堡。
   per-tag 缓存落点（起飞前锁）。每环节 gate + 超时回退。
2. bc_rush.py：`Expand(2)` 替换为「主基建 CC2 + Cc2MigrateAct」；保留超时回退到 Expand。
3. facade：可能需 `lift_unit` / `land_unit`（CC）—— 检查 `cast_unit_ability` 是否已能发
   LIFT/LAND（addon 路径用的就是它）→ 复用，不新增 facade 方法（避免 3-处同步）。
4. 真局自验 `scripts/cc2_migrate_selftest.py`：bc_rush 开局 → 验**终态**：
   - CC2 起飞过（COMMANDCENTERFLYING 出现）；
   - telemetry natural 位置有 COMMANDCENTER（落地二矿）；
   - telemetry natural 有 BUNKER（防御迁移）；
   - telemetry 主基 BUNKER 计数回落（回收）。
   终态铁律：看 telemetry 建筑位置/计数变化，不只看 trace。

## 验证标准（DoD）

- bc_rush build_acceptance 仍 7/7（迁移不破坏开局 timing）。
- cc2_migrate_selftest 真局 PASS：CC2 飞到二矿落地 + 二矿地堡建起 + 主基地堡回收（终态）。
- 失败回退验证：CC 起降失败时二矿仍成型（不卡死）。
- 单测：act 状态机逻辑（mock facade）。

## 待评审的问题

1. 决策 1 选 A（全量 + 强 gate + 可降级 B）合理吗？还是 YAGNI 上直接 B / C？
2. CC 起降用现有 `cast_unit_ability(tag, "LIFT_COMMANDCENTER")` 够吗，还是要 facade 新方法？
3. 风险：改 bc_rush expansion 路径会不会拖累已通过的开局？gate/回退够不够稳？
4. 是否过度设计：用户要的核心价值（避骚扰 + 退矿）能否用更轻的方式达成？

## 独立评审结论（opus，2026-06-21）+ 逐条处理

**VERDICT：deliver design + ask user for go/no-go。** 评审核实了仓库事实并给出决断，逐条采纳：

1. **拆三块，价值/风险差异巨大**（采纳）：
   - 回收主基地堡 = 低风险、真实价值（退~75 矿）、纯复用 salvage（`SALVAGEEFFECT_SALVAGE` 已验证）。
   - 二矿建地堡（迁防御）= 低风险、真实价值（bc_rush 二矿确实裸防）、复用 RampBunkerAct 模式。
   - **CC 家建→飞二矿 = 最高风险、最投机**：现状 `Expand(2)` 二矿直建已 7/7 通过 + 真局赢，"避 poke"
     是**防御一个没证据发生的假想**；而飞行正是 #543 移动靶坑——飞起来落不下 = all-in 整局崩。
     B 和 A **终态相同**（CC+地堡在二矿、主基地堡回收），A 只多一段脆弱飞行去省一个投机的 poke 窗口。
2. **回退未拍死**（采纳为阻断项）：超时 N 未定；两个回退方案都没选定；**缺中途卡飞的逃生**
   （addon 先例会永久重发 LAND→CC 卡飞、二矿不成型）；build_acceptance 抓不住"二矿晚 40s/飞错"
   仍可能丢整局。→ A 若做，必须先把这些 pin 死。
3. **CC 起降可复用** `cast_unit_ability(LIFT/LAND_COMMANDCENTER)` 无需新 facade 方法（采纳）；但
   `LIFT_COMMANDCENTER`/`LAND_COMMANDCENTER`/`COMMANDCENTERFLYING` **UNVERIFIED**，做前必须真机
   `get_available_abilities` 核对 + selftest 断终态 + 记 `ActionResult`。
4. **自主执行判断**（核心，采纳）：这是**改一个能赢的 all-in 的结构性扩张路径**、价值投机、用户
   不在、且 #560 标 `需独立设计` = 决策点。"能自验就别问"只覆盖**测试**，不覆盖"要不要拿一个赢的
   build 冒结构性风险"这个**价值/风险取舍**——那是用户保留的决策（CLAUDE.md「结构性=用户主导」）。
5. **过度耦合**（采纳）：把 家建→飞→落→建堡→回收 揉成一个大状态机 = 飞行 bug 会连带杀掉防御迁移。
   应**解耦**：安全两步（二矿地堡 + 回收）做成独立 plan Step；只把飞行隔离进 act，失败自我包含。
   补：缺"卡飞逃生"、缺回退落哪个 townhall、缺"主基地堡"的确定性 selector（如 nearest start_location）。

**结论与下一步**：把决策交给用户（见 TASKS / 对话）。给出 A/B/C 三选 + 推荐。**任何方案动手前**：
①真机核对 CC LIFT/LAND ability ②若选 A，先 pin 死所有回退 + 卡飞逃生 ③解耦安全两步与飞行。

## 用户 reframe（2026-06-21）+ 真机核对结果

**用户拍板：改成通用 expand 逻辑（取代 bc_rush 专属 A/B/C）**：玩家可预先在家/任意处造一个额外
CC（不在采矿点）；bot 开矿时**优先把这个空闲 CC 飞到新基地点**；家里没空闲 CC 才新造。
→ 这是去投机化的版本（飞行由**玩家主动**造 spare CC 触发，不是 bot 猜测），回应了评审"价值投机"。

**真机核对 CC LIFT/LAND（#560 linchpin，scripts/cclift_probe.py，PASS）**：
- `LIFT_COMMANDCENTER` / `LAND_COMMANDCENTER` / `COMMANDCENTERFLYING` enum 存在**且真机可用**。
- **关键约束（真机才发现）**：**CC 只有 idle（不在产 SCV）时才有 LIFT** ability。主基 CC 常年
  产兵 → `is_idle=False, orders=1` → available abilities 里**没有 LIFT**（只有 CANCEL/LOADALL/
  RALLY/SMART）。debug 生一个不产兵的 idle CC（= spare CC 场景）→ has_lift=True → LIFT 成
  COMMANDCENTERFLYING → move 到锁定落点 → LAND → **落到目标 dist=0.0**（终态验证）。
- **设计影响**：spare-CC-fly 实现**必须先确保 spare CC idle**（停产/取消队列）才能 lift；落点
  起飞前锁死（CLAUDE.md 移动靶铁律）；landing 精确（dist=0）。

**实现取向（低风险、玩家驱动、对现有 build 零影响）**：做成**无 spare CC 即完全 no-op** 的逻辑——
只有玩家显式造了额外 idle CC 时才触发，现有所有 build（不造 spare CC）完全不受影响。检测 spare CC
= ready + idle + 不在矿区（near 无 mineral）；飞到最近未占 expansion；卡飞逃生 + 落点被占回退。

## 实现 + 验证（2026-06-21 完成）

`SpareCcExpandAct`（terran/spare_cc_expand_act.py），注入 terran bot `_wrap`（所有 terran build 共享，
无 spare CC 即 no-op）。检测 spare = ready+idle+周围无矿；锁定最近未占扩张点（起飞前锁死）；
LIFT→LAND(带落点，飞行建筑自动飞过去落地)；卡飞逃生（>25s 就地迫降）；放弃门（LIFT 20s 发不出放弃）。

**踩坑 + 修（关键）**：直接 `cc(LIFT)` / `f(LAND)` **永不生效**——spare CC `orders==[]` 命中
python-sc2 `prevent_double_actions` 丢弃 idle 单位 UnitCommand 的坑（与 salvage 同根，common_bot.py:740
已记）。修法：走 `_vibecraft_bypass_actions`（UnitCommand prevent_double=False，common_bot on_step
super 后 `_do_actions(bypass, prevent_double=False)` 发出）。这也是真机才暴露的（cclift_probe 在
on_step 直发碰巧成功，act 内直发被丢）。

**验证**：cclift_probe（真机核对 LIFT/LAND ability，CC idle 才有 LIFT，落到目标 dist=0）PASS；
spare_cc_expand_selftest（spare CC 检测→LIFT→飞→LAND，终态 townhall 2 = 落到新扩张点开矿）PASS；
5 单测（no-op 契约 + 锁定 + bypass 发 LIFT + 矿区 CC 不当 spare + LIFT 不可用不发）；bc_rush 验收
7/7 + Victory（act 在 wrap 里对现有 build 零回归）。

**已知限制（后续可优化）**：与 plan 自身 sharpy Expand 的协调——理想是有 spare CC 时抑制 plan 新造 CC，
当前是 spare CC 抢先飞到最近未占扩张点、plan Expand 看到占用后另选；少数情况可能短暂双开。玩家显式
造 spare CC 才触发，实际可控；列为后续细化。
