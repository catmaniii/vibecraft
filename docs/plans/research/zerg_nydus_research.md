# Zerg Nydus Worm 偷家：真实节奏 Deep Research

> 生成日期：2026-05-23
> 用途：sharpy bot 实现 Nydus all-in build 的参考真值，三个独立 reference 交叉验证。

---

## 一、Nydus Network 机制速查（LotV 版本）

| 参数 | 数值 |
|---|---|
| Nydus Network 费用 | 150 矿 + 150 气 |
| Nydus Network 前置 | **Lair**（不升就无法建） |
| Nydus Network 建造时间 | 36 秒 |
| Nydus Worm（虫洞出口）费用 | 75 矿 + 75 气 |
| Nydus Worm 出现时间 | 落地后 **14 秒** 钻出地面（期间有 6 甲护体） |
| Worm 冷却 | 14 秒（每条 Worm 之间） |
| 载量上限 | 255 地面单位（所有 Worm/Network 共享） |
| 装载速度 | 0.18 秒/单位 |
| 卸载速度 | 0.36 秒/单位（首单位额外 0.36 秒延迟） |

### 关键：如何在敌方家"打孔"（放 Nydus Worm）

**核心前提：你必须在目标位置有视野。不需要 Creep，不需要农民到达。**

方法：
1. **Overlord 提前飞到敌方主基地/自然分矿附近**，停在边缘提供视野（常规做法，建 Nydus Network 前 1-2 分钟就要预埋）
2. **Overseer 主动侦察**（需要 Lair → Overlord 变形，约 17 秒；Serral 版本在 4:37 同时开建 Network + Overseer）
3. 已有其他单位侦察到该区域也算

**Sharpy 实现关键**：bot 需要在 Nydus Network 开建时，同时或提前将 Overlord/Overseer 飞到目标坐标。不需要在敌方家有地面单位。Worm 一旦插入并钻出（14 秒），就可以从本方 Network 传送部队，几乎瞬间卸载。

---

## 二、Variant 1：1-Base Speedling Nydus（Railgan，极速 ~4 分钟）

**适用对阵**：ZvP（偶尔 ZvT）
**风格**：all-in 偷家，"30 只小狗在对面家，4 分钟前"

### Build Order

| Supply | Game Time | 动作 | 备注 |
|---|---|---|---|
| 12 | 0:00 | 农民 | |
| 13 | 0:14 | **BE**（气矿）| 立即满气 |
| 12 | 0:25 | **BS**（母池）| |
| 11-13 | 0:29-0:41 | 农民 ×2 | 停在 14 农 |
| 14 | 0:51 | Overlord | **派去神族自然分矿，提供视野** |
| 14 | 1:12 | 小狗 ×6 | 开始产出 |
| 17 | 1:20 | 女王 | |
| 19 | 1:29 | Metabolic Boost（小狗速度）| |
| 21 | 2:00 | **Lair** | 女王完成后开升 |
| 21 | 2:02 | Overlord | **藏在神族主基地旁，用于插 Worm** |
| 21-31 | 2:12-2:59 | 小狗持续产出 | 多数藏在本方家等待 |
| 28 | 2:59 | **VN**（Nydus Network）| |
| 31 | 3:37 | **第一条 Nydus Canal**（插入敌方家）| 14 秒钻出 ≈ 3:51 |
| 35 | 4:03 | **第二条 Nydus Canal** | |
| — | **< 4:00** | **首次攻击**：约 30 只速狗从 Worm 涌出 | |

### 关键特征
- 单矿，不开二矿，all-in 性质极强
- 不成功 = 直接输（作者原话："If you don't do any damage with the allin you are dead"）
- 第二个 Overlord 专门藏在敌方主基地边缘提供 Worm 落点视野
- 首波 14 秒 Worm 出地时刚好小狗攒够 30 只

---

## 三、Variant 2：Queen Roach Nydus（Serral，ZvP，~6 分钟）

**适用对阵**：ZvP（Serral 在职业赛使用）
**风格**：两矿经济，更厚实，首波攻击 ~6:00

### Build Order

| Supply | Game Time | 动作 | 备注 |
|---|---|---|---|
| 13 | 0:13 | Overlord | |
| 16 | 0:49 | **BH**（二矿孵化场）| 常规两矿开局 |
| 18 | 1:09 | **BE** 气矿 | |
| 17 | 1:14 | **BS** 母池 | |
| 19 | 1:41 | Overlord | |
| 20 | 2:01 | 女王 ×2 | |
| 20 | 2:02 | 小狗 ×2 | 侦察/干扰用 |
| 26 | 2:11 | Metabolic Boost | |
| 30 | 2:36 | **BH**（三矿孵化场）| |
| 44 | 3:36 | **Lair** | |
| 47 | 3:45 | **BR**（蟑螂窝）| |
| 47 | 3:46 | **BE ×2**（追加气矿）| |
| 56 | 4:37 | **VN**（Nydus Network）+ **Overseer** | 同时开，Overseer 用于视野/侦察 |
| 85 | **6:00** | **首次攻击** | |

### 攻击时单位组成
- 蟑螂（主力，6+ 只）
- 女王（多只，提供 Transfuse 续航）
- 速狗补充
- 攻击前变形 Ravager（视情况）

### Overseer 的作用
Serral 版本在 Nydus Network 开建的同时让 Overlord 变形成 **Overseer**，Overseer 飞到神族主基地寻找盲区落 Worm。Overseer 额外具备隐身侦测（可发现 DT 反制）和变形速度比 Overlord 快的优势。

---

## 四、Variant 3：Fast Queen Roach Nydus（ZvT，极速 ~4:27）

**适用对阵**：ZvT
**风格**：比 Serral ZvP 版本更激进，强攻人族防线

### Build Order

| Game Time | 动作 |
|---|---|
| 0:57 | **BH** 二矿孵化场 |
| 1:16 | **BS** 母池 |
| 2:05 | **BR** 蟑螂窝 |
| 2:27 | **Lair** |
| 3:08 | 蟑螂 ×3（首批）|
| 3:27 | **VN** Nydus Network |
| 3:51-4:15 | 蟑螂追加 ×2-3 |
| 4:12 | **Nydus Canal 插入敌方家** |
| **4:27** | **首次卸载攻击**：6-8 只蟑螂 + 2 只女王 |

### 特征
- 两矿 + Roach Warren 全在 2 分内完成
- 蟑螂比小狗血厚，ZvT 更能顶住 Marine 火力
- 附建 2 个孢子炮（BA ×2）防 Banshee 反制

---

## 五、各对阵 Variant 差异对比

| | 1-Base Speedling（Railgan）| Queen Roach ZvP（Serral）| Fast Roach ZvT |
|---|---|---|---|
| **对阵** | ZvP | ZvP | ZvT |
| **矿数** | 单矿 | 两矿 | 两矿 |
| **气矿开建** | 0:14（立即）| 1:09 | ~1:20 |
| **母池 BS** | 0:25 | 1:14 | 1:16 |
| **Lair** | 2:00 | 3:36 | 2:27 |
| **VN 开建** | 2:59 | 4:37 | 3:27 |
| **Worm 插入** | 3:37 | ~5:30 | 4:12 |
| **首次攻击** | **<4:00** | **~6:00** | **4:27** |
| **主力兵种** | 速狗（30+）| 蟑螂 + 女王 | 蟑螂（6-8）+ 女王 |
| **视野来源** | Overlord 预埋 | Overlord + Overseer | Overlord |
| **all-in 程度** | 极端（不成功直接输）| 中高（经济有支撑）| 高 |

### ZvZ Nydus
ZvZ 中 Nydus 并非主流 all-in 选择（双方速狗互冲更直接），但 2-Base Swarmhost Nydus（Railgan）在特定对位（对方刺蛹开局）也有使用，节奏较慢，约 8-10 分钟。

---

## 六、Nydus Canal 在敌方家"打孔"——Sharpy 实现要点

```
所需条件（按优先级排）：
1. Nydus Network 完成（Lair 前置）
2. 目标位置有视野（Overlord / Overseer 在场）
3. 发出 Build Nydus Worm 指令，指定目标坐标（敌方主基地 mineral line 旁或 ramp 内侧）
4. 等待 14 秒 Worm 钻出
5. 开始装载（本方 Nydus Network 处选中部队 → Right-click Network）
6. 部队自动传送，在 Worm 位置卸载
```

**Sharpy bot 实现注意**：
- 不需要在敌方家有农民或地面单位定位，Overlord 飞过去即可
- Worm 钻出 14 秒期间有 6 甲，但 HP 仍然脆，优先放在视野差的角落（矿线背后、建筑遮挡处）
- 单条 Worm 被摧毁后单位仍在 Network 内，不会死（除非所有 Network 也被摧毁）
- 多条 Worm 策略：第一条吸引注意，第二条从另一角度出
- 典型落点：敌方矿线与主基地边墙之间的死角，最难被集火

---

## References

1. **Spawning Tool — Railgan ZvP 1 Base Speedling Nydus**
   https://lotv.spawningtool.com/build/98762/

2. **Spawning Tool — Serral's Queen Roach Nydus (ZvP All-In)**
   https://lotv.spawningtool.com/build/140822/

3. **Spawning Tool — ZvT Fast Nydus Queen Roach**
   https://lotv.spawningtool.com/build/93631/

4. **Spawning Tool — Nydus Vibe/Lowko (ZvP/ZvT 2-Base)**
   https://lotv.spawningtool.com/build/97781/

5. **Liquipedia — Nydus Network (Legacy of the Void)**
   https://liquipedia.net/starcraft2/Nydus_Network_(Legacy_of_the_Void)

6. **Liquipedia — Nydus Worm (Legacy of the Void)**
   https://liquipedia.net/starcraft2/Nydus_Worm_(Legacy_of_the_Void)

7. **Liquipedia — 2 Base Offensive Nydus (vs. Protoss)**
   https://liquipedia.net/starcraft2/2_Base_Offensive_Nydus_(vs._Protoss)

8. **YouTube — StarCraft 2: NYDUS WORM ALL-IN! (Zerg Build Order Guide)**
   https://www.youtube.com/watch?v=Mn3Twb13398

9. **YouTube — ZvT/ZvP Swarmhost Nydus Guide (Grandmaster Zerg)**
   https://www.youtube.com/watch?v=c6qC6NNalTk

10. **YouTube — Serral's New MASS NYDUS Zerg vs Terran Late Game**
    https://www.youtube.com/watch?v=EF26WCo-IA4
