# Zerg Roach Hydra 中期 Timing 深度调研

> 调研日期：2026-05-23  
> 涵盖：ZvP 三矿标准型、ZvT 1/1 timing 型、升级链优先级、出门时机

---

## 开局选择：Hatch First vs Pool First

现代 LoTV 环境下，**Roach Hydra 体系几乎全走 Hatch First（17 Hatch）**，原因是三矿经济基础对 Roach Hydra 的高气矿消耗是必要条件。Pool First（14-15 BS）偶见于 ZvP 要补打窗口或对付早攻，但主流 Roach Hydra 不走这条路。

**标准 17 Hatch 开局（+1+1 Roach Hydra ZvP，Spawning Tool #46015）：**

| Supply | 时间 | 动作 |
|--------|------|------|
| 13 | 0:13 | 孵化 Overlord（侦察对手自然扩张） |
| 17 | 0:55 | **BH**（自然矿区） |
| 18 | 1:05 | **BE**（第一气矿） |
| 18 | 1:21 | **BS**（母池） |
| 19 | 1:39 | Overlord（暂停探机生产） |
| 19 | 2:06 | 女王 ×2 |
| 25 | 2:13 | 代谢加速（小狗速） |
| 31 | 2:43 | 第三女王 |
| 34 | 3:00 | **BH**（第三矿——确认对手已占二矿后建） |

> PiG B2GM (#158017) 走同一骨架：17 Hatch / 17 Pool，孵化场比母池略早约 13 秒，代谢加速 2:18 拿。

---

## 气矿（Extractor）节奏

气矿扩张节奏是 Roach Hydra 体系的核心节拍——气矿早晚直接决定升级和技术建筑的完成时间。

| 阶段 | 气矿数 | 时机 |
|------|--------|------|
| 开局 | 1 | BS 完成前（≈1:05–1:10） |
| 科技启动 | 3 | ≈4:00，在加 **BR**（蟑螂窝）同步开两口气矿 |
| Hydra 产线 | 5 | ≈5:38，**VH**（刺蛇巢）完成前后，主矿加第 4-5 口 |
| 满气生产 | 6 | 三矿饱和后（探机约 60），第六口 |
| 大后期 | 7-8 | 有四矿后上潜伏者/BL 时才需要 |

**关键原则**：Roach Hydra 是高气矿体系（刺蛇 50 矿 100 气），前期不要过早烧气在蟑螂上——等 Lair 和 VH 完成后才大量切刺蛇。

---

## 科技建筑链（核心 timing）

### 标准 ZvP 三矿时间线

```
3:36–4:00   BR（蟑螂窝）  ←  母池完成后尽早
4:00        BE ×2（同步开三口气矿→三口）
4:10–4:25   Lair         ←  VH 前置，越早越好
5:15–5:20   BV ×2        （进化腔 ×2，同步开双腔）
5:28–5:30   VH（刺蛇巢）  ←  Lair 完成后约 1 分钟
```

**PiG B2GM 数据点**（精确到秒）：
- **51 supply / 4:00** → BR（蟑螂窝）
- **47 supply / 4:10** → Lair（同时补三口气工人）
- **80 supply / 5:20** → VH + BV ×2

**Winter 版本（#122388）**略进取：
- 4:00 同步开 BR + 第一个 BV（进化腔）
- 4:15 升 Lair

---

## 蟑螂（Roach）生产窗口

BR 完成后（≈4:30–4:50）**立刻开 Glial Reconstitution（蟑螂速）**，同时开始出蟑螂。标准做法：

- BR 完成 → 立刻研蟑螂速 + 出 **8-10 只蟑螂**作为进攻前锋
- 不能在 VH 完成前把气全部烧在蟑螂上，否则刺蛇产线会断气
- 蟑螂生产节拍：约 2:1 蟑螂:刺蛇 的比例持续生产至出门

---

## 升级链优先级

这是 Roach Hydra 体系最需要判断力的部分，各来源结论一致：

### ZvP 优先级

1. **代谢加速**（Metabolic Boost，小狗速）—— 开局必拿，约 2:13–2:18
2. **Glial Reconstitution（蟑螂速）** —— BR 完成后即研，约 4:30–4:50
3. **+1 地面攻击**（BV 完成后立刻开，约 5:20–5:30）
4. **Grooved Spines（刺蛇射程 +1）** —— VH 完成后立刻开（约 5:30–6:00）
5. **+1 地面护甲** —— 与攻击 +1 同步（双腔并研）
6. **Muscular Augments（刺蛇速）** —— 优先级低于射程，射程研完后上
7. **+2 攻击 / +2 护甲** —— 延续到 8 分钟后

**关键判断**：Grooved Spines（射程）> Muscular Augments（速度）——射程让刺蛇在保持距离的情况下持续输出，对 ZvP 追猎/巨像阵型价值更高；速度是 ZvT 对线坦克时更重要（方便快速展开和撤退）。

### ZvT 变体

- 攻击 +1 / 护甲 +1（1/1 timing）节奏更激进，约 6:30–7:00 出门
- Muscular Augments 优先级在 ZvT 提升，因为需要快速展开围坦克
- Grooved Spines 同样要拿但可以稍晚

---

## 出门 timing 和主力阵容

### ZvP 三矿标准型（主流）

| 数据点 | 数值 |
|--------|------|
| 出门时间 | **7:30–7:45** |
| 目标 supply | **150+ supply** |
| 主力组成 | **约 28 蟑螂 + 9-14 刺蛇 + 1-2 监察者** |
| 升级状态 | +1 攻 / +1 甲 完成；蟑螂速完成；刺蛇射程完成或接近完成 |
| 探机数 | 约 55 只（跨 2.5 矿） |

这个 timing 是 **"供应压迫窗口"**：7:45 前神族通常还没有足量的不朽 + 高坦阵容，Roach Hydra 1/1 升级的物量可以直接压垮。

### ZvP 更贪型（三矿四气 + Lurker 转型）

部分打法（SC2 Swarm 指南，winter 版本后期）在 7:45 timing 打完后：

- **8:30–9:00** 开 VD（潜伏者巢）
- 主力切换为刺蛇 + 潜伏者，蟑螂退出主力
- 同步升 Hive + Spire，向 BL 过渡

### ZvT 1/1 Timing 型

- 出门更早：**6:30–7:00**
- 阵容更蟑螂重：蟑螂 35+ / 刺蛇 8-10
- 攻击 +1 + 蟑螂速完成即出门，不等刺蛇射程
- 目标：在人族坦克阵地形成前施压，或打医疗船数量不足时的窗口

---

## ZvP 变体对比

| 变体 | 二矿 / 三矿 | 出门时间 | 升级 | 特点 |
|------|-------------|----------|------|------|
| 二矿 +1 Roach timing（进攻型） | 二矿 | 5:00–5:30 | 仅 +1 攻 + 蟑螂速 | 快速骚扰，经济换时间；刺蛇少或无 |
| 三矿 +1+1 标准型 | 三矿 | 7:30–7:45 | +1/+1 完成 + 蟑螂速 + 刺蛇射程 | 当前主流；economic + timing 均衡 |
| 三矿 Roach Hydra → Lurker | 三矿 | 7:45 出门后转型 | 同上 + 潜伏者 | 后期保险更强，不怕 Colossus |
| 三矿无 Burrow | 三矿 | 7:30–7:45 | 同标准型 | 简化版，不开掘地 |
| 三矿带 Burrow | 三矿 | 7:45 出门 | 同上 + Burrow | 对高坦/HT 炸弹有韧性；掘地蟑螂微操加成 |

**Burrow**：ZvP 标准 Roach Hydra timing 通常**不带 Burrow**（省 Lair 完成后的研究时间和气矿），留给 Lurker 体系。ZvT 偶尔会带，用于蟑螂掘地在坦克射程外回血。

---

## 综合 timing 速查（三矿 +1+1 标准型）

```
0:55    17 BH（自然扩张）
1:05    第一 BE（气矿）
1:21    BS（母池）
2:13    代谢加速
3:00    第三 BH
3:36    BR（蟑螂窝）
4:00    BE ×2（三口气）
4:10    Lair
4:30    Glial Reconstitution（蟑螂速）+ 开始出蟑螂
5:15    BV ×2（双进化腔）
5:20    +1 地面攻击 + +1 护甲（同步开双腔研究）
5:30    VH（刺蛇巢）
5:38    BE ×2（五口气）
5:50    Grooved Spines（刺蛇射程）
6:00    开始混产蟑螂+刺蛇（2:1）
7:00    Muscular Augments（刺蛇速）
7:30    +2 攻击 / +2 护甲（若腔还空）
7:45    出门——150+ supply，≈28 蟑螂 + 12 刺蛇 + 监察者
```

---

## 参考来源

- [Spawning Tool #122388：3 Hatch Roach Hydra (Winter-inspired ZvX Timing)](https://lotv.spawningtool.com/build/122388/)
- [Spawning Tool #46015：+1+1 Roach Hydra Timing (Silver/Gold)](https://lotv.spawningtool.com/build/46015/)
- [Spawning Tool #158017：PiG B2GM Dia 3 - Roach/Hydra](https://lotv.spawningtool.com/build/158017/)
- [Spawning Tool #124997：Roach Speed +1 Timing Attack by Lowko](https://lotv.spawningtool.com/build/124997/)
- [Liquipedia：PvZ Guide Midgame - Zerg Strategies](https://liquipedia.net/starcraft2/PvZ_Guide/Midgame/Zerg_Strategies)
- [Liquipedia：Roach Build](https://liquipedia.net/starcraft2/Roach_build)
- [SC2 Swarm Blog：Standard Roach Hydra Guide ZvP](https://swarmscblog.wordpress.com/2018/04/14/roach-hydra-guide-zvp/)
- [Spawning Tool ZvP Build Index](https://lotv.spawningtool.com/build/zvp/)
