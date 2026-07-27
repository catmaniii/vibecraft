# 12 Pool（虫族极速母池）开局 Deep Research

> 调研日期：2026-05-23
> 用途：VibeCraft 虫族 bot 剧本参考 —— 12pool 节奏基准

---

## 核心定义

12 Pool 指在 **12 supply（农民）时建 BS（母池/Spawning Pool）**，是虫族最激进的正规开局之一。
目标：在对手完成防线（墙 / 第二只 Queen）之前用首批叉子（Zergling）打出经济伤害或击杀分矿建设。
与 6/8/9/10 Pool 相比，12 Pool 在农民数量与攻击速度之间取得较好折中，是阶梯上可见的实战 build（而非纯 cheese）。

---

## 基准 build order（ZvZ 进攻型 / 1-Hatch 首攻版）

以 Spawning Tool build #153110（ZvZ 12 pool 2 hatch）为基准，结合 Lambo ZvP（#166685）数据：

| 时间 | Supply | 动作 |
|---|---|---|
| 0:17 | 12 | **BS（母池）开建**；drone 继续生产 |
| 0:40 | 14 | **Overlord（霸主）**；送 drone 查对面 |
| ~1:43 | 14 | **BS 完成**（建造约 65 秒）|
| 1:04–1:06 | 14 | **首批 6 叉子**（3 对，BS 出来即排）|
| 1:05–1:07 | 14 | **Queen（女王）**开训（BS 完成即刻） |
| 1:17–1:27 | 17 | **第二批 4 叉子**（2+2，紧接首批） |
| 1:38–1:40 | 21 | **Hatchery 2（第二孵化场）**开建（首攻出发后） |
| 1:42 | 22 | 第二个 Overlord |
| — | — | **气矿（BE）**：延后到 18 supply 左右（首波叉子出门后） |

**Lambo ZvP（#166685）的精确时间戳**（供参照，结构与 ZvZ 版相近）：

- 0:17 @ 12 → BS
- 0:40 @ 14 → Overlord
- 1:04 @ 14 → 6 叉子
- 1:05 @ 17 → Queen（同时派 drone 查对面分矿）
- 1:40 @ 19 → 再出 4 叉子

---

## 关键节点总结

| 节点 | 数值 |
|---|---|
| BS 开建 | supply 12 / game-time ≈ 0:17 |
| BS 完成 | ≈ 1:43（建造 65 s） |
| 首批 6 叉子出门 | ≈ 1:04–1:06 |
| Queen 开训 | BS ready 后即刻（≈ 1:05） |
| Overlord 2 | ≈ 1:42（supply 22） |
| Hatchery 2 | ≈ 1:38–1:40（supply 21） |
| BE（气矿）| 进攻型：延后到 18 supply 后；经济型：更早（14 supply） |
| 首次出门攻击 | 6 叉子约 1:04 启程，**到对面主基地约 2:00–2:20**（距离依地图） |

---

## Zergling 提速（Metabolic Boost）

12 Pool 分两类变体：

### Gasless（无气版，ZvZ 最常见）
- **不研 Metabolic Boost**（至少前期不研）
- 优先：快速首攻、然后多孵化场经济
- BE 开建时间很晚，气矿不是第一优先级
- 首波 6 叉子是慢速叉子，靠数量和时机取胜
- 适合 ZvZ：对手也是 12pool 或 hatch-first 时，双方慢叉正面对拼

### Gas 版（经济型 / ZvP 变体，如 Railgan build #47534）
- **0:20 @ 12 → BS**，**0:44 @ 14 → BE（气矿）**（极早入气）
- 1:51 @ 18 → **Metabolic Boost**（提速）开研
- 6 叉子 @ 1:06 出门，但移动到对面时提速还未完成（提速约 3:51 完成，约 100 秒研发）
- 实际：首波叉子是压制/骚扰，提速后的第二波叉子才是主要攻击力
- 这个变体更偏"经济压制"而非"一波流"

---

## 各 matchup variant 对比

### ZvZ — 1 Hatch 攻击流（最激进）
- 目标：用 8 叉子击杀对手分矿建设，让对手出不了矿
- **无气、无提速**，首波直接进攻
- 打法：叉子直奔对手天然矿区，优先狙击建第二孵化场的 drone
- 失败预案：撤回、建 BB（妖虫巢）或 BC（刺蛇匍匐者），转 2 矿经济再推 Roach
- 参考：build #153110（Fishycrackers）

### ZvZ — 2 Hatch 经济版
- 同样 12 BS，但首波叉子试压后立即建 Hatchery 2
- 首波叉子仅作骚扰，不孤注一掷
- 进入 2 矿经济，走小狗 + Ravager 中期

### ZvP — Lambo 压制版（build #166685）
- 目标：在神族水晶墙成型前把 6 叉子送进主基地
- 关键时机：神族墙通常 1:45–2:00 完成，6 叉子 1:04 出门，**地图小则有机会穿墙前到达**
- 注意：只和勇士（Zealot）交战才划算，追 BE（水晶）或 BG（折跃门）即可
- 如果神族用 BE 封口，考虑取消 Queen 改建 BH（第二孵化场）转经济

### ZvP — Railgan 经济型 12 Pool（build #47534）
- 12 BS + 14 BE（早气）→ 1:06 6 叉子骚扰 → 1:51 提速研究 → 1:21 Hatchery 2
- 更偏宏观：叉子施压但不指望一波打残，靠提速叉子骚扰 + 多孵化场经济过渡到 2:20 Hatchery 3
- 中期路线：蟑螂 + 孢子匍匐者防空，5:00 出蟑螂

### ZvT — Pool First 变体
- 12 BS，首波叉子约 1:04 出门，目标是 SCV 或阻止 BC（指挥中心）在天然矿区落地
- 人族 reaper 开局下，叉子可绕过 reaper 骚扰矿线 SCV
- 人族早墙完成后叉子价值急剧下降，需快速转 Queen + 多孵化场
- Metabolic Boost 时机：通常等首波叉子出门、Queen 和几只 drone 就位后才入气研提速
- 参考逻辑：首波 12 叉子到对面约 2:00–2:15，此时人族 Bunker 尚未就位是关键窗口

---

## vs Ladder AI 实战参考

| AI 难度 | 12 Pool 效果 |
|---|---|
| **VeryEasy** | 几乎必杀，AI 不会防挡也不撤 worker |
| **Easy / Medium** | 高概率一波推掉，AI 不会有效封墙 |
| **Hard** | 12 pool 仍有效但需执行到位；AI 会尝试撤 worker 但反应慢 |
| **VeryHard** | 对 AI 12 pool 依然可行；AI 封墙速度加快，Gasless 首波需精准时机；Gas 版经济型相对稳健 |
| **Elite / Cheater** | 12 pool 压制效果下降；对手 AI 会提前响应，需配合后续经济支撑 |

**VibeCraft bot 开发建议**：前期验收用 Hard，通过后上 VeryHard；12 pool 的 build_acceptance 指标重点看首波叉子到达时间（目标 <2:20）和对手分矿建设成功率。

---

## References

- [Spawning Tool: ZvZ 12 pool 2 hatch (build #153110)](https://lotv.spawningtool.com/build/153110/)
- [Spawning Tool: Lambo 12 Pool ZvP (build #166685)](https://lotv.spawningtool.com/build/166685/playable/)
- [Spawning Tool: Railgan ZvP - Economical 12 Pool (build #47534)](https://lotv.spawningtool.com/build/47534/)
- [Spawning Tool: 12 pool ZvP - TheZergLord (build #22826)](https://lotv.spawningtool.com/build/22826/playable/)
- [Liquipedia: 12 Pool](https://liquipedia.net/starcraft2/12_Pool)
- [Liquipedia: Aggressive Pool First](https://liquipedia.net/starcraft2/Aggressive_Pool_First)
- [Keevan Dance Medium: ZvZ Defending 12 Pool Expand (Gasless)](https://medium.com/keevan-dance-starcraft/zvz-defending-12-pool-expand-gasless-4a916a778e0d)
- [Spawning Tool: ZvZ build listing](https://lotv.spawningtool.com/build/zvz/)
