# Mutalisk 骚扰（飞龙骚扰）节奏 Deep Research

> 调研日期：2026-05-23
> 版本：LotV 当前 patch（2024-2025）
> 数据来源：Spawning Tool build orders / Liquipedia LotV / SC2 社区

---

## 单位基础数据

| 属性 | 数值 |
|------|------|
| 费用 | 100 矿 / 100 气 |
| 造兵时间 | 24 秒 |
| 占人口 | 2 |
| 血量 | 120 HP（不含护盾） |
| 护甲 | 0（+1/级） |
| 移速 | 5.6（空中最快之一） |
| 攻击 | Glaive Wurm，弹射 3 目标：9/3/1 伤害（可升级） |
| 攻速冷却 | 1.09 秒 |
| 被动 | Tissue Regeneration：1.4 HP/秒自回血 |

前置：Spire（需 Lair 完成后建），造时 **71 秒**。
每只飞龙 100 气，6 只需 600 气，双气全开后约 60 秒积累一批。

---

## Variant 1：ZvT 2-Base Mutalisk Speed Bane（Railgan 经典版）

**来源**：[Spawning Tool #66228 — Railgan ZvT 2 Base Mutalisk Speed Banelings](https://lotv.spawningtool.com/build/66228/)

### 核心逻辑

利用人族在双矿阶段**没升级 + 坦克 / 雷神数量极少**的窗口，用飞龙骚扰分散注意力，隐藏妖虫（Bane）主力推线。Bio / Mech 都有此软肋窗口。

### 开局路线（17 BS → 18 Hatch → Lair）

| 补给 | 时间 | 动作 |
|-----|------|------|
| 17 | 0:46 | **BS（母池）** |
| 18 | 1:12 | 二矿 Hatchery + 气矿 |
| 18 | 1:33 | 女王 + 小狗 ×2 |
| 26 | 2:20 | **ling speed（代谢加速）** |
| 36 | **3:05** | **Lair** ← 关键节点 |
| 41 | 3:21 | 第 3、4 气矿 |
| 41 | 3:25 | **妖虫巢（BB）** —— 防地狱犬 |
| 57 | **4:24** | **Spire（刺翼）** —— Lair 约 4:10 完成，完成后立即开建 |
| 61 | 5:23 | **妖虫速（Centrifugal Hooks）** |
| 59 | 5:07 | **三矿 Hatchery** |
| 64 | **6:05** | **首批飞龙 ×5** |
| 74 | 6:14 | 飞龙 ×3（追加）|
| 79 | 6:28 | 飞龙 ×1 |
| 104 | 8:36 | 进化腔 ×2（升级开始）|

**关键时间轴汇总**：

```
0:46  BS（母池）
1:12  二矿
2:20  ling speed
3:05  Lair
3:21  开三四气矿
3:25  BB（妖虫巢，防地狱犬）
4:24  Spire（刺翼）
5:07  三矿
5:23  妖虫速
6:05  首批 5 飞龙出门骚扰
6:14  +3 飞龙，首波共 8 只
```

### 骚扰执行要点

- **飞龙骚扰 + 妖虫推线二选一**：飞龙 hit-and-run 吸引防空注意力，隐藏妖虫（walk-in 或 drop）拆墙，敌人如果主力出去追飞龙，妖虫砸经济。
- 防守侧：3:30 后保持 8+ 小狗在本阵防地狱犬；4:30 加女王 + 孢子（防女妖 Banshee）。
- 对阵 Mech：后期转 蟑螂→蟑螂刺蛇毒爆→BL；对阵 Bio：ling-bane-hydra 或蟑螂假动作 + 快升级。

### 为何 17 BS 不是 Hatch First

该 build 是 **17 BS → 18 二矿**（母池先于二矿），牺牲少量经济换取 ling speed 更早、对 early aggression 有更好自保；Hatch first 版本（通常 16~17 先二矿）经济更强但对侵略性更敏感。两种路线目标一致，Railgan 版选 17 BS 因为 ZvT 中人族早期骚扰（海盗船 / 地狱犬）威胁大。

---

## Variant 2：ZvT Fast Muta Pressure（Timing Attack）

**来源**：[Spawning Tool #30326 — Fast Muta Pressure ZvT Timing Attack](https://lotv.spawningtool.com/build/30326/)

比 Railgan 版**更激进**，无妖虫巢，直接拔 Spire 做 timing：

| 节点 | 时间 |
|------|------|
| BS | 17 供应 |
| 二矿 + 气矿 | 1:17 |
| ling speed | 2:10 |
| Lair | **2:50**（更快！） |
| 3 气矿 | 3:20-3:30 |
| Spire | **3:30**（Lair 完即建）|
| 第三矿 | 4:30 |
| 首批 6-7 飞龙 | **~5:00** |
| 进化腔 ×2 | 5:00 |

**适合场景**：对手明显是 macro-oriented Terran，无早期侵略压力时走此路线，飞龙出门更早（约 5:00），骚扰窗口更大；代价是防御薄弱，遇到早期侵略必须切路线。

---

## Variant 3：ZvP 2-Base Mutalisk Gambit（Railgan）

**来源**：[Spawning Tool #59057 — Railgan ZvP Mutalisk Gambit / 2 Hatch Opener](https://lotv.spawningtool.com/build/59057/)

### 核心逻辑

神族星门（VS）开局盛行后，很多人认为刺翼开局在 ZvP 已死。此 build 的思路是**双路线骗局**：Spire + 刺蛇巢同时建，根据对手侦察结果临场选路。

### 时间轴

| 节点 | 时间 |
|------|------|
| 二矿 Hatchery（Hatch first） | 0:51 |
| 气矿 + BS | 1:10 / 1:21 |
| 女王 ×2 | 2:08 |
| ling speed | 2:10 |
| **Lair** | **2:49**（比 ZvT 版还快）|
| 气矿 ×2 | 3:23 |
| **Spire** | **4:01** |
| 孢子（防 Oracle） | 3:45 |
| 三矿 Hatchery | 4:38 |
| **首批 7 飞龙** | **5:14** |
| 刺蛇巢 | 5:30 |
| 刺蛇（骑墙）| 6:24 |

### 骗局决策树

```
侦察到对手没发现 Spire → 走飞龙骚扰路线
侦察到对手已知 Spire / 造了 Stargate → 切刺蛇，用刺蛇打神族凤凰/追猎
```

### vs Stargate 具体对策

- **Oracle 防御**：3:45 前在主基地放 1 个孢子，女王卡在自然矿门口
- **Phoenix**：刺蛇（Armored 属性）克凤凰（Light 额外伤害），飞龙反之（Light 被凤凰克）
- **Blink Stalker**：飞龙 hit-and-run 节奏必须在追猎研究闪现前结束或转型

---

## Variant 4：ZvZ 2-Base Mutalisk（2020 Railgan 版）

**来源**：[Spawning Tool #119736 — Railgan ZvZ 2 Base Mutalisk 2020](https://lotv.spawningtool.com/build/119736/)

### 时间轴

| 节点 | 时间 |
|------|------|
| BS | 标准 |
| 妖虫巢（BB） | 2:36 |
| **Lair** | **3:20**（44 供应）|
| 进化腔（BV） | 4:02 |
| **Spire** | **4:25**（58 供应）|
| 近战升一级 | 4:27 |
| **首批 7 飞龙** | **5:46**（69 供应）|
| 后续 ×3 飞龙 | 6:26 |

### ZvZ 特殊要点

- **气甲优先于气攻**（与其他对阵截然不同），因为飞龙镜面对打护甲价值更高
- 7 只一组、14 只一组最高效（正好击杀对手飞龙一击 / 两击，避免溢出伤害）
- 女王 ×4 + 孢子防守，妖虫压力保 Spire 不被骚扰
- 对手蟑螂路线时：飞龙骚扰 + 地面小狗妖虫对拆，蟑螂无法有效防空

---

## 通用节奏汇总表（跨 variant）

| 节点 | ZvT Railgan | ZvT Fast | ZvP Gambit | ZvZ |
|------|------------|----------|-----------|-----|
| BS   | 0:46 | 0:46 | 1:21 | 标准 |
| 二矿 | 1:12 | 1:17 | 0:51 | 标准 |
| Lair | **3:05** | **2:50** | **2:49** | **3:20** |
| Spire | 4:24 | 3:30 | 4:01 | 4:25 |
| 首批飞龙 | **6:05** | **~5:00** | **5:14** | **5:46** |
| 骚扰开始 | ~6:10 | ~5:10 | ~5:20 | ~5:50 |

---

## 骚扰执行通则

### Hit-and-Run 微操

飞龙弹射攻击 cooldown 1.09 秒，标准节奏：
1. 移入攻击范围（range 3）打一轮
2. 立刻后退（cooldown 期间后撤）
3. cooldown 结束再移入攻击

"叠飞龙"（stack）：整组飞龙叠成一个单位大小移动，对手无法 focus 打最低血的那只；同时叠攻击时全组同步输出，最大化 burst。

### 骚扰目标优先级

1. **工人（矿工 / 气矿工人）** —— 最高价值
2. **暴露的单位**（出门骚扰、没回基的零散部队）
3. **防御建筑拆除**（需要 24-30 只飞龙才能快速拆导弹塔）

### 反制侦察 & 转型

| 对手反应 | 对策 |
|---------|------|
| 导弹塔大量铺开（人族）| 绕开重防守点，打另一条线；后期转蟑螂刺蛇 |
| 雷神（Thor）出现 | 分散飞龙（Magicboxing）避开溅射；派 Overseer 或腐化者（Armored）吸雷神伤害 |
| 北欧海盗（Viking）| 飞龙本身是 Light 单位，Viking 对 Light 有加成；数量上碾压对手再打 |
| 凤凰（ZvP）| 立刻切刺蛇路线，飞龙收回防守 |
| 追猎研 Blink（ZvP）| 骚扰窗口关闭，转地面或加航母路线 |
| 对手飞龙（ZvZ）| 气甲优先；7/14 只一组 focus 打 |

---

## 参考来源

- [Spawning Tool #66228 — Railgan ZvT 2 Base Mutalisk Speed Banelings](https://lotv.spawningtool.com/build/66228/)
- [Spawning Tool #59057 — Railgan ZvP Mutalisk Gambit / 2 Hatch Opener](https://lotv.spawningtool.com/build/59057/)
- [Spawning Tool #119736 — Railgan ZvZ 2 Base Mutalisk 2020](https://lotv.spawningtool.com/build/119736/)
- [Spawning Tool #30326 — Fast Muta Pressure ZvT Timing Attack](https://lotv.spawningtool.com/build/30326/)
- [Spawning Tool #30001 — 2 Base Muta](https://lotv.spawningtool.com/build/30001/)
- [Liquipedia — Mutalisk (Legacy of the Void)](https://liquipedia.net/starcraft2/Mutalisk_(Legacy_of_the_Void))
- [Liquipedia — Muta/Ling/Bane vs. Terran](https://liquipedia.net/starcraft2/Muta/Ling/Bane_(vs._Terran))
- [Liquipedia — General ZvT Strategy](https://liquipedia.net/starcraft2/General_ZvT_Strategy)
- [Liquipedia — Anti-Muta Play (PvZ)](https://liquipedia.net/starcraft2/Anti-Muta_Play_(PvZ))
- [SC2 Forums — 2 base muta build order (ZvT)](https://us.forums.blizzard.com/en/sc2/t/2-base-muta-build-order-zvt/14993)
