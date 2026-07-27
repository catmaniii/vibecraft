# 神族 7 策略 build order 审计

> 创建：2026-05-19
> 范围：1g_robo_immortal / dt_rush / phoenix_2base / blink_stalker / cannon_rush / iac_2base / skytoss
> 不含：4bg（用户已确认 OK）
> 状态：审计完成，等待 user 决定修哪些

---

## 1g_robo_immortal（1门 Robo 不朽开）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/robo_1gate.py`
**调研来源**：
- https://lotv.spawningtool.com/build/87940/ （PvX 1 Gate FE into 3 Gate Robo，2019）
- https://lotv.spawningtool.com/build/42817/ （PvX 1 Gate FE into 3 Gate Robo，早期标杆）

### 标准职业 build（关键节点 + supply）

| supply | time | action |
|---|---|---|
| 14 | 0:17 | BE |
| 15 | 0:38 | BG |
| 16 | 0:46 | BA x1 |
| 20 | 1:26 | BY |
| 20 | 1:34 | NX（二矿） |
| 21 | 1:46 | 第 2 BE |
| 21 | 1:53 | 第 2 BG |
| 22 | 1:56 | 折跃研究 @chrono |
| 22 | 2:02 | BA x2 |
| 24 | 2:31 | VR（Robo） |
| 24 | 2:43 | Adept x2 |
| 31 | 3:02 | 第 3 BG |
| 35 | 3:23 | Immortal @chrono |
| 54 | 4:41 | BG x4（共 5） |
| 64 | 5:09 | VT（TwilightCouncil） |
| 72 | 5:36 | Charge 研究 |
| 87 | 6:31 | Templar Archives |

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 14 PROBE → BE → 16 PROBE → BA x1 → BG → 20 PROBE |
| Step BG ready | BY + Expand(2) |
| Step BY ready | 折跃 @research + VR + VT |
| Step NX x2 exists | BA x2 |
| Step VT ready | Charge 研究 |
| Step VR ready | 1 Immortal → 1 OB → 持续 Immortal x20 |
| Time 5:00 | Expand(3) |
| Time 6:00 | BG x4 |
| Time 7:00 | VR x2 |

### 关键差异

1. **[严重]** VT（TwilightCouncil）在 BY ready 时就建（~2:30 左右），比标准早约 150 秒。标准 1g Robo build 中 VT 出现在 5:09，是经济三矿扩张阶段才补；vibecraft 版把 Charge 路线混入了早期 critical path，浪费了 2:30 左右的建造资源（150矿），挤压了 VR 的建造。实际结果是 VT 过早但 Charge 研究也过早（无矿），或者 VR 延后。
2. **[中等]** 标准 build BY 在 1:26 先于 NX（1:34），vibecraft 是 BG ready 时同时触发 BY+NX，timing 可能略有出入但不严重——顺序是对的（BY 先于或同时于 NX）。
3. **[中等]** 标准 build 中 VT 是 5:09 后、三矿阶段才建，且没有 Templar Archives（该 build 走 Stalker/Immortal 而非 HT）。vibecraft 的 Charge + VA（TemplarArchive）路线更像中期 IAC 转场，而不是标准 1g Robo Immortal 的防御运营定位。
4. **[中等]** vibecraft 没有早期 Adept 产出（标准 build 从 2:10 开始出 Adept 保家）。只有 `ProtossUnit(STALKER, 2)` 作为防身，无 Adept phase-shade 侦察。
5. **[微调]** 标准 build 折跃和 VR 在 BY ready 后几乎同时开始；vibecraft 正确实现了并行触发，逻辑正确。
6. **[微调]** `ProtossUnit(ZEALOT, 100)` target 设为 100 太激进——多矿多 BG 情况下会不断 warp-in 叉子排队。标准 1g Robo 主攻方向是 Immortal+Stalker，叉子是辅助。

### 建议（不实施，只列）

- 把 VT 的建造时机从 `UnitReady(BY)` 改为 `Time(60*4)` 或 `UnitExists(NEXUS, 3)` 触发，对齐标准 5:09 时序
- 早期加 Adept x2（BY ready 后出，保家侦察）
- Zealot target 降至 8-10，主力改为 Stalker+Immortal

---

## dt_rush（暗使偷家）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/dt_rush.py`
**调研来源**：
- https://lotv.spawningtool.com/build/47308/ （DT Rush，~4:10 首波 DT）
- https://lotv.spawningtool.com/build/82515/ （DT Rush w/ 4-Gate follow up）

### 标准职业 build（关键节点 + supply，build 47308）

| supply | time | action |
|---|---|---|
| 14 | 0:19 | BE |
| 15 | 0:38 | BG |
| 16 | 0:49 | BA x1 |
| 16 | 0:55 | BA x2（**双气同时**） |
| 19 | 1:29 | BY |
| 20 | 1:51 | 折跃研究（chrono） |
| 21 | 2:10 | VT（TwilightCouncil） |
| 23 | 2:34 | 第 2 BG |
| 24 | 2:49 | VB（DarkShrine，**无 Robo/Warp Prism**） |
| 25 | 3:08 | BG x2（共 4 门） |
| 28 | 3:44 | NX（二矿） |
| 29 | 4:09 | DT x3（VB 完成 warp） |

标准 build **没有 VR（Robo）和 Warp Prism**；build 82515 变体在 2:12 加了 Robo + Charge。

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 14 PROBE → BE → 15 PROBE → BG → BA x1 → BA x2 → 19 PROBE |
| Step BG ready | BY |
| Step BY ready | 折跃研究 + VT |
| Step VT ready | VB |
| Step BY ready | BG x3（补到 3） |
| Step VB exists | Expand(2) |
| Step VB ready | DT x8（target） |
| Step BG x3 ready | Stalker x10 |

### 关键差异

1. **[严重]** 标准 DT Rush 在 BY 前**不经过 BG**——直接 BE→BG→BA BA→BY，确保双气满采。vibecraft 在 SequentialList 先出 BG 再双气，逻辑正确；但 BY 触发时机是 `UnitReady(BG)` 而非 `UnitReady(BG, 1)`，实际效果一样，这里无问题。
2. **[严重]** 标准 build 中 VB 在 2:49 直接跟 VT，**没有 Warp Prism**。vibecraft 代码本身没有 Warp Prism，符合标准路线；但 docstring 第 8 行提到"Warp Prism 运载（效率更高）"，文字与代码不一致，会误导玩家理解。
3. **[中等]** vibecraft 的 DT target 是 8，标准 build 首波出 3 个就发动攻击（`_ready_to_pressure` 判定 ≥3 DT）。target=8 会让 bot 继续 warp 到 8 个才出门，推迟偷家 timing 约 60-90s。标准 DT Rush 核心在于 **4:10 首波 3 DT**，延迟意义不大。
4. **[中等]** 标准 build 共 4 门（BG x4）；vibecraft 补到 BG x3，偷家成功后追猎产能不足。
5. **[微调]** 二矿时机：标准 3:44（DT 出门后），vibecraft 是 `UnitExists(DARKSHRINE, 1)` 触发（VB 建造期间），时机相近（约 2:50），实际上比标准略早，但不算问题。
6. **[微调]** 折跃研究 chrono 用 `ChronoTech(RESEARCH_WARPGATE, BY)` 是对的，但 BY 结束后 chrono 没有切换到 DT 生产。标准 build 在 DT 出来后 chrono 加速 DT warp-in，vibecraft 缺少这个 ChronoUnit(DARKTEMPLAR)。

### 建议（不实施，只列）

- 修 docstring：删除"Warp Prism 运载"说法（与实现不符）
- DT target 从 8 降至 4-5（首波 3 出门，后续保持持续 warp-in）
- 补 4 门：`GridBuilding(BG, 4)` 而非 3
- 加 `ChronoUnit(DARKTEMPLAR, DARKSHRINE)` 在 VB ready 后加速 DT 产出

---

## phoenix_2base（两矿凤凰）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/phoenix_2base.py`
**调研来源**：
- https://lotv.spawningtool.com/build/126982/ （HuShang Double Stargate Phoenix PvZ）

### 标准职业 build（关键节点 + supply，build 126982）

| supply | time | action |
|---|---|---|
| 14 | 0:18 | BE |
| 15 | 0:38 | BG |
| 16 | 0:47 | BA x1 |
| 19 | 1:24 | NX（二矿） |
| 20 | 1:34 | BY |
| 21 | 1:43 | BA x2 |
| 22 | 2:01 | Adept（chrono） |
| 26 | 2:19 | **VS x1**（第一个星门） |
| 27 | 2:28 | 折跃研究 |
| 33 | 2:52 | Adept |
| 43 | 3:31 | Phoenix（第 1 个） |
| 49 | 3:47 | BA x3（共三气） |
| 51 | 3:59 | **VS x2**（第二星门） |
| 58 | 4:37 | NX（三矿） |
| 80 | 5:30 | **VR x2**（双 Robo，更偏 IAC 过渡） |
| 80 | 5:41 | VT |

**关键**：标准 build 只有 **1 个 VS 先出**（2:19），等 Phoenix 开始产出 3:31 后才加第 2 个 VS（3:59）；**无 VR 和 Blink**（这是 HuShang 的风格，后期转 Immortal/Archon）。

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 14→BE→15→BG→BA→19 PROBE |
| Step BG ready | Expand(2) + BY |
| Step NX x2 exists | BA x2 + BA x3 |
| Step BY ready | 折跃 + VT + VR |
| Step VT ready | Blink 研究 |
| Step BY ready | **VS x2**（一次建双星门） |
| Step VS ready | Phoenix x12 |
| Step VS ready | Observer x2 + Warp Prism x1 |
| Step NX x2 exists | Expand(3)（直接三矿） |

### 关键差异

1. **[严重]** vibecraft 在 BY ready（~2:00）同时建 VT + VR + VS x2。标准 build 2:19 才出第 1 VS，无 VT/VR。vibecraft 的三线并行（VT+VR+双 VS）在矿源不够的情况下会导致所有建筑延后。实际上 ~2:00 时矿约 600，同时开 VT(150)+VR(200)+2xVS(300)=650矿，必然卡矿排队。
2. **[严重]** Blink 研究在 Phoenix 开局完全不需要——浪费 VT+Blink 的资源（VT 150 + Blink 150 gas = 300 gas 白烧）。标准 Phoenix build 没有 VT 和 Blink。
3. **[中等]** VR（Robo）的时机：标准 build 5:30 才建 VR（收尾转型），振荡 vibecraft 在 BY ready 时就建（约 2:30），加速了 Observer 和 Warp Prism 产出但大量挤压凤凰产出资源。
4. **[中等]** Observer target x2 + Warp Prism x1 在 Robo 一好就建，比标准早约 200s，但不是关键错误——对 PvZ 来说 Observer 越早越好；问题是 Warp Prism 在两矿凤凰中基本用不上。
5. **[微调]** 标准 build 通过 Adept 做早期保家，vibecraft 用 `ProtossUnit(STALKER, 8)` 保家——两种都可以，但 Stalker 8 target 会持续消耗 BY 生产槽，可能延迟 Phoenix 生产。
6. **[微调]** 三矿时机：vibecraft `UnitExists(NEXUS, 2)` 触发 Expand(3)，等于一有二矿就开三矿，时间比标准 4:37 早约 60-90s。过早三矿在 Phoenix 还未集结的情况下守家压力大。

### 建议（不实施，只列）

- 移除 VT 和 Blink 研究路线（与 Phoenix 开局不符）
- 移除早期 VR；改为 `Time(60*5)` 或 `UnitExists(PHOENIX, 6)` 后再建 VR
- 双 VS 改为：第 1 VS 在 BY ready，第 2 VS 在 `UnitExists(PHOENIX, 4)` 后
- 三矿触发改为 `UnitExists(PHOENIX, 8)` 或 `Time(60*4.5)`
- Stalker target 降至 4，主力保家改用 Adept x2

---

## blink_stalker（闪追压制）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/blink_stalker.py`
**调研来源**：
- https://lotv.spawningtool.com/build/178931/ （Harstem 4 Gate Blink PvT）

### 标准职业 build（关键节点 + supply，build 178931）

| supply | time | action |
|---|---|---|
| 14 | 0:17 | BE |
| 15 | 0:35 | BG |
| 16 | 0:47 | BA x1 |
| 20 | 1:24 | BY |
| 20 | 1:35 | NX（二矿） |
| 21 | 1:49 | BA x2 |
| 21 | 1:57 | Adept（chrono） |
| 21 | 2:00 | 折跃研究（chrono） |
| 28 | 2:32 | VT（TwilightCouncil） |
| 34 | 2:59 | VR（Robotics） |
| 37 | 3:19 | Blink（chrono，**关键！**） |
| 38 | 3:23 | BG x2（补到 3 门） |
| 40 | 3:39 | BG x4（第 4 门） |
| 40 | 3:47 | Observer |
| 46 | 4:08 | Warp Prism（chrono） |
| 55 | 4:30 | BA x3 |
| 72 | 5:28 | NX（三矿） |
| **5:07** | **benchmark** | **68 supply / 11 Stalker / 1 Adept / 1 Warp Prism** |

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 14→BE→15→BG→BA→19 PROBE |
| Step BG ready | Expand(2) + BY |
| Step NX x2 exists | BA x2 |
| Step BY ready | 折跃 + VT + VR |
| Step VT ready | Blink 研究（chrono） |
| Step BY ready | BG x4（一次补到 4 门） |
| Step NX x2 exists | BA x3 |
| Step NX x2 exists | Expand(3) |
| Step VR ready | Observer x2 + Warp Prism x1 |
| Step BY ready | Adept x1 |
| Step BY ready | Stalker x14 |

### 关键差异

1. **[严重]** 标准 build 的 BY 顺序是 **BY 在前，NX 在后**（1:24 BY → 1:35 NX）；vibecraft 是 `UnitReady(BG)` 同时触发 `Expand(2)` 和 `GridBuilding(BY)`，实际上两者同时排队，哪个先建取决于探机位置，通常 NX 会先完成。这导致 BY 延后约 10s，进而 VT→Blink 链整体延后。
2. **[中等]** VR 的触发条件是 `UnitReady(BY)` 而非标准的独立时序（标准 2:59 VR，在 BY 完成后约 30s）。vibecraft 逻辑正确，但因为 VT 和 VR 同时触发（BY ready 时），矿资源竞争可能导致两者都延后。
3. **[中等]** 标准 build 只有 **1 BA** 到 BY 完成前，2:02 才开第 2 BA（二矿）。vibecraft 在 `UnitExists(NEXUS, 2)` 就补 BA x2，时机更早（约 1:35），不算差但与标准略有不同。
4. **[微调]** 标准 build 出 Adept x1 在 1:57（BY ready 之前，用 BY 训练），vibecraft 在 BY ready 后才出 Adept，时序稍晚约 20-30s。
5. **[微调]** Stalker target=14 合理（标准 5:07 有 11 个，14 是正常延续目标）。
6. **[微调]** 三矿触发 `UnitExists(NEXUS, 2)` 而非明确时间，会导致三矿过早——标准 5:28 才三矿，vibecraft 可能在 ~2:30 就触发 Expand(3)（二矿存在后立刻）。这和 phoenix_2base 一样的问题。

### 建议（不实施，只列）

- BG ready 时只触发 `Expand(2)`，BY 改为 `Step(None, GridBuilding(BY), skip=UnitExists(BY,1), skip_until=UnitExists(NEXUS,2))` 但早于 NX——或者用 SequentialList 先下 BY 再下 NX
- `Expand(3)` 触发条件改为 `Time(60*5)` 或 `UnitExists(STALKER, 8)` 对齐 5:28 benchmark
- VT 和 VR 分离触发（VT 在 BY ready，VR 在 VT exists 后约 30s）

---

## cannon_rush（炮塔速攻）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/cannon_rush.py`
**调研来源**：
- https://lotv.spawningtool.com/build/111586/ （PvZ Cannon Rush / Proxy Stalkers）

### 标准职业 build（关键节点 + supply，build 111586）

| supply | time | action |
|---|---|---|
| 14 | 0:17 | BE（家） |
| 16 | 0:38 | **BF（前线，forward proxy）** |
| 16 | 0:47 | BA x1 |
| 18 | 1:07 | BE（第 2 个） |
| 18 | 1:27 | **BC x2（forward，压矿线）** |
| 18 | 1:32 | BG（家） |
| 19 | 1:53 | **BC x3** |
| 20 | 2:02 | BE |
| 20 | 2:19 | BG x2（家，共 2 门） |
| 20 | 2:22 | BY |
| 20 | 2:29 | **BC x4** |
| 21 | 2:58 | Stalker（chrono） |
| 23 | 3:07 | **BB（Shield Battery）**，Stalker（chrono） |
| 25 | 3:17 | BB |
| 27 | 3:35 | Stalker |
| 27 | 3:40 | 折跃研究（chrono） |
| 31 | 4:26 | BG x3（家） |

**关键**：BF 在 **0:38**（远早于 BG），BC 在 **1:27**（对方自然矿区附近），BB 在追猎出来后才建。

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 12 PROBE → BE → 14 PROBE → BF → BA → BG → 18 PROBE |
| Step BF ready | BC x3（家附近网格） |
| Step BG ready | BY |
| Step BY ready | BG x3（补到 3 门）+ 折跃 |
| Step BF ready | BB x2 |
| Step BF ready | BC x6（继续扩张） |
| Step BC x3 exists | Expand(2) |
| Step BY ready | Stalker x12 |

### 关键差异

1. **[严重]** **前线 proxy 完全缺失**。vibecraft 的 BF + BC 用 `GridBuilding`（sharpy 放在家附近网格），不会放到对方矿线。代码注释本身已标注"TODO 真前线 proxy 需 forward_cannon_proxy"，但这是整个战术的核心——炮塔必须在对方自然矿区才能压制，否则是无效 cannon rush。
2. **[严重]** 标准 build BF 在 **0:38**（甚至早于 BG），vibecraft 在 SequentialList 里 BF 在第 4 步（先 12 PROBE→BE→14 PROBE→BF），实际时间约 1:00-1:10，比标准晚 20-30s。这导致 BC 建造也延后到 1:50+，对方有更多时间反应。
3. **[严重]** 标准 build 在 BC 建造阶段没有 BA（气矿），BF + BC 是纯矿建筑；vibecraft 在 BF 之后加了 BA，实际上让探机离开了前线去建气矿，BF timing 进一步延误。
4. **[中等]** 标准 build BC 4 个以上在 2:30 内完成（压制窗口），vibecraft 的 `GridBuilding(BC, 6)` 是两阶段（先 3 后 6），但都在家附近，根本不压线。
5. **[中等]** BB 顺序：标准 3:07 追猎出来后才建 BB，vibecraft 在 BF ready 就建 BB x2，顺序提前，但因为不在前线意义不大。
6. **[微调]** 早期探机数量：标准 build 12 探机前出（去前线），vibecraft 也是 `ActUnit(PROBE, 12)` 停手，节奏一致。

### 建议（不实施，只列）

- 实现 `ForwardCannonProxy`（或复用 `ForwardProxyPylon` 类），让 BF + BC 在对方自然矿线附近建造——这是 cannon rush 存在的必要条件
- 把 BF 从 SequentialList 移出，改为 `Step(None, ...)` 在 12 探机时独立触发
- 移除 BA（炮塔速攻阶段不需要气矿，推迟到 BG 完成后）

---

## iac_2base（叉球一波）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/iac_2base.py`
**调研来源**：
- https://lotv.spawningtool.com/build/196674/ （2 Base Chargelot + Immortal + Archon，2025）
- https://lotv.spawningtool.com/build/164221/ （Probe Beginner 2-base IAC）

### 标准职业 build（关键节点 + supply，build 196674）

| supply | time | action |
|---|---|---|
| 16 | 0:50 | BG |
| 17 | 0:52 | BA x1 |
| 20 | 1:25 | NX（二矿） |
| 20 | 1:35 | BY |
| 21 | 1:40 | BA x2 |
| 23 | 2:10 | 折跃研究 + Adept |
| 26 | 2:22 | VR（Robo） |
| 26 | 2:25 | BB（Shield Battery） |
| 29 | 2:32 | Stalker（防身） |
| 42 | 3:25 | Immortal（第 1） |
| 49 | 3:40 | VT（TwilightCouncil） |
| 49 | 3:42 | BF（Forge） |
| 60 | 4:23 | +1 地面攻击 |
| 60 | 4:27 | Charge 研究 |
| 60 | 4:35 | BG x6（+5 门，共 7） |
| 53 | 4:05 | Immortal（第 2） |
| 62 | 5:10 | Warp Prism |
| **88** | **6:15** | **出门 attack** |

### vibecraft 实现 build

| step | action |
|---|---|
| SequentialList | 13 PROBE → BE → 14 PROBE → BG → BA → 16 PROBE |
| Step BG ready | Expand(2) + BY |
| Step NX x2 exists | BA x2 |
| Step BY ready | 折跃 + VR + VT |
| Step VT ready | Charge @chrono |
| Step NX x2 exists | BF → +1 攻击 |
| Step VT ready | VA（Templar Archives） |
| Step VA ready | HT x4 + Psi Storm |
| Step BG ready | Sentry x2 |
| Step VR ready | Immortal x2 → OB → Immortal x3 |
| Time 4:00 | BG x7（暴产能） |
| Step BG ready | Zealot x18 |

### 关键差异

1. **[中等]** 标准 build VT（TwilightCouncil）在 **3:40**；vibecraft 在 BY ready（~2:10）就并行建 VT。过早的 VT 会抢占矿源（VT 150 矿）——标准 build 这 150 矿在 2:10 要给 VR（Robo，200 矿），如果矿不够两者会互相延后。幸运的是 vibecraft 有 Immortal chrono，Robo 产出速度不是主要瓶颈。
2. **[中等]** 标准 build 无 VA（Templar Archives）——IAC 2-base all-in 是短平快，不等 HT 和 Storm。vibecraft 额外建了 VA + HT x4 + Psi Storm，这些在 6:15 出门时根本来不及完成（Storm 研究需要 TA+VA 各 60s，总 120s，加上 HT 训练），只会分散资源。
3. **[中等]** 标准 build 2:10 出 Adept（保家侦察）；vibecraft 只有 `ProtossUnit(STALKER, 2)`，无 Adept 阶段。
4. **[中等]** 标准 build 在 2:25 建 BB（Shield Battery）防早期骚扰；vibecraft 没有 BB。
5. **[微调]** `Time(4:00)` 触发 BG x7 比标准（4:35+4:56 暴兵）约早 35s——更早有更多生产时间到 6:15，可接受。
6. **[微调]** vibecraft 有 Sentry x2（力场），标准 build 有 Sentry x1（2:51）。Sentry 在 IAC 里的作用是切阵，2 个合理。

### 建议（不实施，只列）

- 移除 VA（Templar Archives）、HT x4、Psi Storm——IAC 2-base all-in 来不及用
- 加 `GridBuilding(SHIELDBATTERY, 1)` 在 VR ready 后（保自然矿区）
- 加 Adept x1 在 BY ready 后出（保家 + 侦察）
- VT 触发改为 `Step(UnitExists(NEXUS, 2), GridBuilding(VT, 1))` 或 `Time(60*3)`，避免过早抢矿

---

## skytoss（Skytoss 航母流）

**vibecraft 当前实现**：`src/vibecraft/bot/auto_combat/protoss/plans/skytoss.py`
**调研来源**：
- https://lotv.spawningtool.com/build/157942/ （Zest Skytoss build）
- https://lotv.spawningtool.com/build/187114/ （Probe Beginner Skytoss 2024）

### 标准职业 build（关键节点，综合 Zest build + Probe 2024）

| time | action |
|---|---|
| ~2:17 | VS x1（第 1 星门，Void Ray 过渡） |
| ~3:31 | Phoenix/Void Ray 开始产出（早期骚扰） |
| ~4:20 | Air Weapons Level 1（约 54 supply） |
| ~4:34 | VS x2（第 2 星门） |
| ~4:35 | BF（Forge）|
| ~5:34 | VS x3（第 3 星门） |
| ~5:35 | VX（Fleet Beacon） |
| ~6:27 | Carrier 开始生产 |
| ~7:03 | Air Weapons Level 2 |
| 无 | Air Armor（Zest build 全程未研 Air Armor） |

**关键**：标准 Skytoss 是 **3 VS + 1 VX**，**空军武器先研**（Weapons 1 在 4:20），**护甲不一定研**（或后期才研）。Void Ray 作为过渡单位填充空档。

### vibecraft 实现 build

| step | action |
|---|---|
| NX x2 exists | Expand(3) → Expand(4) |
| Gas x6 | 建 6 个气矿 |
| BG ready | BY |
| BY ready | VS x1 |
| VS x1 ready | VX（Fleet Beacon）**同时** VS x4（补到 4 门） |
| BY ready | VR（Robo） |
| BY ready | VT + TA |
| VX ready | Carrier x12 |
| SequentialList | Air Weapons 1 → Air Armor 1 → Air Weapons 2 → Air Armor 2 → Air Weapons 3 → Air Armor 3 |
| VX ready | Tempest x3 |
| Carrier x8 | Mothership x1 |
| FleetBeacon ready | Chrono Carrier |

### 关键差异

1. **[中等]** 标准 Skytoss 有 **Void Ray** 过渡期（VS 一好就出 Void Ray 骚扰或防守），vibecraft 完全没有 Void Ray——VS x1 建好后直到 VX 完成前不产任何空军单位。这是一段数分钟的空档期，经济运营时守家困难。
2. **[中等]** 升级顺序：vibecraft 用 SequentialList `Weapons1→Armor1→Weapons2→Armor2→Weapons3→Armor3`；标准职业打法（Zest）只研了 **Air Weapons（不研 Air Armor）**，把资源留给更快堆 Carrier。对于 all-in Skytoss，Armor 升级收益较低；但对于运营 Skytoss，Armor 也需要。此差异属于风格差异，不算严重 bug，但全 SequentialList 意味着 Armor 1 研完才能开始 Weapons 2，这比并行慢。
3. **[中等]** vibecraft 目标 **4 VS**（`GridBuilding(STARGATE, 4)`），标准 Zest build 是 **3 VS**。4 VS 产 Carrier 速度快但需要更多 supply 和矿石支撑，振荡 vibecraft 同时还要养 HT+Tempest，supply 压力大。
4. **[微调]** VX 和 VS x2-4 在 VS x1 ready 时同时触发——与标准 build VS 先逐步加，VX 在三矿后再建的节奏不同，但对后期剧本影响不大。
5. **[微调]** `BuildGas(6)` 在切入时触发（三矿才能放 6 个气矿）；如果切入时仅 2 矿，会先排队等三矿开好。逻辑上 Step 的 Expand(3) 会先触发，然后 BuildGas(6) 慢慢填，不会报错。
6. **[微调]** `PlanCancelBuilding` 在 skytoss.py 的 SequentialList 中缺失（其他 plan 都有）。后期被 EMP 或 Ravager 胆汁打中正在建的 VS，bot 不会自动取消重建。

### 建议（不实施，只列）

- 加 Void Ray 过渡：`Step(UnitReady(STARGATE, 1), ProtossUnit(VOIDRAY, 3))` 在 VX 完成前填充产出
- Armor 升级改为与 Weapons 并行（分开两条 SequentialList），或降优先级到 VX 后才开始
- VS 目标降至 3，VX 建造时机延后到 NX x3 exists
- 补 `PlanCancelBuilding()` 进战术 SequentialList

---

## 优先级总结

| 策略 | 严重 bug 数 | 中等 bug 数 | 微调数 | 推荐优先级 | 核心问题 |
|---|---|---|---|---|---|
| 1g_robo_immortal | 1 | 3 | 2 | P2 | VT 过早，缺 Adept，Zealot target 太高 |
| dt_rush | 2 | 2 | 2 | P2 | DT target 过大延迟 timing，缺 4 门，缺 DT chrono |
| phoenix_2base | 2 | 2 | 2 | **P1** | Blink/VT 路线完全多余，双 VS 同时建挤矿，三矿过早 |
| blink_stalker | 1 | 2 | 2 | P2 | BY/NX 顺序偏差，三矿过早 |
| cannon_rush | 3 | 2 | 1 | **P1** | 前线 proxy 完全缺失（战术核心失效），BF timing 偏晚 |
| iac_2base | 0 | 4 | 2 | P2 | VA/HT/Storm 路线不必要，缺 BB，VT 过早 |
| skytoss | 0 | 3 | 2 | P3 | 缺 Void Ray 过渡，升级顺序可优化 |

**建议优先修：**

1. **cannon_rush（P1）**：前线 proxy 是整个战术的存在基础，不修就等于没有 cannon rush。
2. **phoenix_2base（P1）**：Blink+VT 路线浪费约 300 gas，会严重推迟凤凰产出，实战极难跑通。
