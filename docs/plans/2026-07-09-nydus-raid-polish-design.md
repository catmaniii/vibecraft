# 坑道虫突袭精修（Nydus Raid Polish）— 设计文档

> 2026-07-09。用户要把 nydus 打磨成开源演示级。独立评审前的真理源。

## 需求 + 中文名（用户 2026-07-09）
- 中文名 = **坑道虫**（Nydus Worm，伸到敌方家的出口）/ **坑道网络**（Nydus Network，自家入口 VN）。
  display + 别名改用"坑道虫"（保留"尼德斯/虫洞"兼容旧叫法）。
- 配兵拍板：**偷袭本体 = 狗蟑+女王**（快、炸、时机卡敌人最脆的 ~4:10）；all-in 没杀死 →
  自动转 **蟑螂刺蛇毒蛇**（已有 `persistent_roach_hydra_viper` lategame 转换）。
- 多轮打磨到稳定再通知（autonomous /goal）。

## 现状致命 gap（读代码取证）
`nydus.py` 两个 act 只做：①`_SendOverlordToEnemy` 飞 OL 给视野 ②`_BuildNydusCanalAtEnemy` 在敌方家下虫洞。
**没有任何"装载 army 进坑道网络 → 从坑道虫钻出"的投送逻辑**。army 靠末尾 `PlanZoneAttack(start_attack_power=10)`
**走地图正面攻过去**——坑道虫建出来没人用，偷袭的灵魂（军队瞬间出现在敌方家）完全没实现。落点也只是
`enemy_pos.towards(center, d)` 粗选，非矿线死角。

## 评审处置（2026-07-09 opus，全部采纳 — 实现按这里为准）

1. **UNLOAD ability 运行时探真名（最大风险）**：UNLOAD 系是"有 passenger 才出现"的**上下文能力**，静态命令卡里没有 → **不能硬编码 enum**。实现在"网络/坑道虫里有兵"那刻 `get_available_abilities(network)` / `(canal)` 拿到真实列出的 UNLOAD ability 再下（防 `UNLOADALL_NYDASNETWORK` 的 `NYDAS` 拼写坑 + 是否需 pos 参数）。装载侧 `LOAD_NYDUSNETWORK`/`SMART` 已在静态卡确认。
2. **装载照抄 `load_bunker`**：`common_bot.py:795-847` 的 `SMART(unit→建筑) + _vibecraft_bypass_actions` 范式直接复用，别发明。若新增 `load_nydus`/`unload_nydus` facade 方法 → **FakeFacade + `_SharpyFacadeBase` 两实现 + Protocol audit**（`test_facade_release_unit_role.py`）。删掉设计里"空 orders 被 filter 丢"的错误论据（评审读现装版 `bot_ai_internal.py:608-645` 确认空 orders 命令**保留**；salvage 真根因是 enum 名错→NotSupported，bypass 照用但别拿错论据）。每次装/卸记 `ActionResult`。
3. **落点 `can_place_single(NYDUSCANAL, pos)` 扫敌矿线死角**（不是只 `is_visible`、不是 `towards(center)` 朝斜坡）+ **选定即缓存一次锁定**（#543）。**不上 flood_fill**（YAGNI）。
4. **Reserve 释放纪律**：raid 波每帧 Reserved 独占**只圈这一波**；all-in 结束/转型后**显式 release**（否则撞 CLAUDE.md 规则3 gap，中后期 army 永久 Reserved 不打）。**macro-tail 的 roach30/queen 绝不 reserve**；**留 1-2 女王在家**（inject + 反 Banshee，不投送）。
5. **状态机瘦成 3 态**（照 bc STAGE/DIVE/HEAL）：**STAGE**（网络旁集结）/ **TRANSIT**（已装载、自动过管）/ **STRIKE**（坑道虫处卸出打击）+ bail 兜底。REINFORCE 归 STAGE（新 larva 兵重进 STAGE）、LOAD 是"进 TRANSIT 的动作"不占独立态。
6. **三条兜底**：① OL 死→改派 Overseer/多 OL 保视野（无视野下不了 worm）；② army 卡网络（worm 全死/久不 ready）→ 超时 UNLOAD 回自家 network 走正面，别烂在网络；③ worm 被秒→**（真机确认网络内兵留存后）**重下 worm 再卸。
7. **职责分层不合并**：三个正交 act —— `_SendOverlordToEnemy`（视野）/ `_BuildNydusCanalAtEnemy`（下 worm，落点改 can_place）/ **`NydusRaidAct`**（装/送/卸/增援）。NydusRaidAct 读 `structures(NYDUSCANAL).ready` 感知 worm 即可，不塞下 worm 逻辑。
8. **玩家控制权取舍（写死）**：本 build 走**纯 plan act = 自主，不受玩家全军按钮指挥**（Reserved 独占，符合演示/自主 /goal 需求）。要玩家可打断需 director 编排——本期不做，文档定死自主。
9. **unit type 铁律**：worm 本体 = `UnitTypeId.NYDUSCANAL`（**无 `NYDUSWORM` 类型，别引用会 AttributeError**）。

## 【Round 4 战略重构，用户 2026-07-09 拍板】声东击西 —— 先引开敌军再下坑道

前 3 轮撞墙的根因是"虫洞钻出 14s 在敌方家太脆、对会防守的对手(VeryHard)立不住、0 存活"。
用户点破正解（=职业选手做法）：**别硬下坑道，先用小股部队正面骚扰/佯攻，把敌军主力从矿区引开，
等敌方矿线空了（主力远离）再下坑道** → 虫洞在无人防守的矿线钻出就能活、army 钻出就能屠农民。
把"虫洞存活"从"硬抗防守"变成"创造安全窗口再投送"。

**三个新部件**：
1. **佯攻小队（feint squad）**：一小股速狗（~4-8 只，可牺牲/可回撤）持续骚扰敌方正面/二矿，
   **引敌军主力离开矿区**（边打边撤、反复 poke，别一波送光）。这是"诱饵"，不是主力。
2. **安全窗口检测（enemy-army-away gate）**：靠敌方家的 OL 视野，数**敌方矿线附近的敌方战斗单位**
   （非农民）。少于阈值（主力被佯攻引走）→ 窗口开。`BUILD_NYDUSWORM` **只在窗口开时下**
   （不再盲下），虫洞落点直接选**敌方矿线正中**（此刻无人守，钻出即屠农民，不用再躲去矿后死角）。
3. **主力 raid army**：家里坑道网络旁预装待命（Reserved，同现状），窗口开 + 虫洞钻出 → 洪灌屠农民。
   佯攻小队在 14s 钻出期继续拖住敌军，给投送争取时间。

**这解决了两件事**：① 虫洞落点可以回到矿线正中（屠农民，修 Round 3 的 tgt=structure）——因为窗口期无人守；
② 对 VeryHard 也成立——不硬抗防守，而是等它离开。检测/佯攻是新战术层，落点/投送机制复用前 3 轮。

**新增执行质量点**（Round 4 记分卡重点）：佯攻是否真把敌军引离矿线（矿线敌军数下降）、窗口检测是否准
（别在敌军还在家时下洞）、佯攻小队别一波送光（可持续拖）、窗口没出现的兜底（超时还是下/放弃转运营）。

## 精修核心：NydusRaidAct 投送状态机（新增，替代"走正面"）

一个专门的突袭 act（照 `bc_raid_act` / `marine_staging_act` 成熟机制），把 army 真正"灌"过坑道虫。
每个 army 单位恰好一个状态：

| 状态 | 行为 | 转移 |
|---|---|---|
| **STAGE**（集结待命） | army（狗/蟑/女王）在**自家坑道网络旁** Reserve 集结，不走正面 | 坑道虫钻出 + army ≥ 阈值 → FLOOD |
| **LOAD**（装载） | 待命 army **一起** load 进坑道网络（不 trickle，≥80% 同窗口） | 全进网络 → 自动经坑道到虫洞侧 |
| **STRIKE**（钻出打击） | 从坑道虫 unload → **优先扑农民**（经济杀伤），再拆关键建筑，不追散敌军 | 农民清完/被赶 → 拆家 or 增援 |
| **REINFORCE**（增援） | 后续 larva 出的狗/蟑**继续从坑道网络灌过去**，不走正面 | 持续到 all-in 结束/转型 |

**五个执行质量点**（决定好坏，对应评价维度）：
1. **落点质量**：坑道虫下在**敌方矿线里/农民堆旁/防御死角**（不是空地白给/秒拆）。用类落点规划思路选高价值突袭点 +
   OL 视野保障（活着才下）。落点一次锁定别每帧重选（#543）。
2. **攒够再灌（不 trickle）**：army 在坑道网络旁提前集结待命，虫洞钻出**整波一起装载→一起钻出**
   （枪兵/BC 集结同一条命脉——salvage：别一个个漏送）。
3. **钻出打谁**：优先农民 target priority，别追散兵。
4. **持续增援**：新兵继续灌坑道，不走正面。
5. **坑道虫韧性**：虫洞被拆 → 重下；坑道链保持通。

## SC2 坑道机制（已 venv 真机核对 2026-07-09 — 实现照此，salvage 铁律满足）
- **unit type**：`NYDUSNETWORK=95`（自家入口建筑）、**`NYDUSCANAL=142`**（坑道虫/敌方家出口，现有代码用对）。
- **装载进网络**：`unit(AbilityId.LOAD_NYDUSNETWORK, network)`（=1437；或 right-click SMART 到 network）。单位进网络后经坑道链到 canal 侧。
- **在敌方家卸出**：`canal(AbilityId.UNLOADALL_NYDUSWORM)`（=2371，对 NYDUSCANAL 下，把 passengers 全倒在虫洞位置）。
- **读装载进度**：`network.passengers` / `canal.passengers` / `.passengers_tags` / `.cargo_used`（python-sc2 `unit.py:1215` 明确支持 Nydus）。
- **⚠️ enum 拼写 typo**：python-sc2 里网络侧卸载是 `UNLOADALL_NYDASNETWORK`(=1438)/`UNLOADUNIT_NYDASNETWORK`(=1440)——**NYDAS 不是 NYDUS**（用得着时别拼错）。虫洞侧 `UNLOADALL_NYDUSWORM` 拼写正常。
- **bypass 施法**：装载/卸载对建筑（可能 idle）下令，走 `cast_unit_ability` 的 `_vibecraft_bypass_actions`（ARCHITECTURE 不变量），别被 python-sc2 double-action filter 丢；施法后记 `ActionResult`，非 Success = 被拒（salvage 铁律）。
- **仍需真局 spot-check**（低风险）：`get_available_abilities` 确认这一刻 network/canal 真列出该 ability；`UNLOADALL_NYDUSWORM` 是否需单位先真到 canal 侧（装进 network 后到 canal 的传输延迟）——自验脚本看 passengers 流转终态。

## 兵种配比（狗蟑+女王，微调现有）
现有 roach 8 + zergling 16 + queen ~ 大体对。偷袭波强调：
- **蟑螂**扛线拆家核心（血厚抗 marine/枪兵）；**小狗**海铺人扑农民（视觉最炸 + 绕后）；**女王**扛血 + transfuse 续航。
- 阈值：坑道虫钻出时待命 army ≥ ~10 supply（现 start_attack_power=10 类比）才灌，攒够一波。可调。

## 评价标准：坑道虫突袭记分卡（用户要的"拿什么评好坏"）
全部**真局验终态、per-instance 断言**（不聚合掩盖——salvage/BC 骚扰教训）：

| 维度 | 病征 | 信号/阈值（`nydus_selftest.py` 测） |
|---|---|---|
| **① 投送时机** | 太晚(敌防好)/太早(没兵) | 首虫洞钻出 game_time（目标 ~4:10-4:40）+ 那刻待命 army supply（≥阈值） |
| **② 投送完整性(不trickle)** | 出一个送一个 | 虫洞钻出后 **N 秒内经坑道投送单位 ≥ 待命 army 80%** |
| **③ 落点质量** | 空地白给/秒拆 | 虫洞离敌农民/主基距离（近）+ 虫洞**存活时长**（够灌完一波） |
| **④ 经济杀伤(终态铁证)** | 钻进去没杀农民 | **敌农民数投送前后净掉 Δkilled**（telemetry/侦查）+ 拆建筑数 |
| **⑤ 兵力效率** | 白送一波 | 损失 army supply vs 摧毁敌方价值（农民+建筑），净赚 |
| **⑥ 反应/转型** | all-in 没死就摆烂 | 首波后持续增援投送数;没杀死干净转蟑刺毒蛇(supply 单调涨不卡) |

叠加常规宏观三维（build_efficiency：larva 不闲、资源不堆、不卡人口）管家里。

## 验证（多轮迭代到稳）
- **`scripts/nydus_selftest.py`**：真局 vs **真对手**（偷袭必须有敌人才复现——CLAUDE.md 环境纪律），
  grep 投送事件（`NYDUSRAID stage/load/strike/reinforce` greppable 前缀）+ 读 telemetry 敌农民数前后差，
  逐维记分。**验终态**（敌农民真掉、单位真钻出敌方家坐标），非中间 trace。
- 每轮：跑记分卡 → 找最差维度 → 修（改 act 不放宽阈值）→ 重跑，直到 6 维 + 宏观三维稳定过。
- 单测：NydusRaidAct 状态机（mock）；构造回归（进 `_ZERG_OPENINGS`）；ruff/mypy。
- build_acceptance 保底（build 没崩）。

## 待评审确认点
1. 坑道装载/卸载的真实 ability enum + 语义（passengers/UNLOADALL）——最大风险，真机核对。
2. NydusRaidAct 与现有 `_BuildNydusCanalAtEnemy` 职责划分：合并还是分层（一个下虫洞、一个管投送）？
3. army Reserve 集结与 `PlanZoneAttack`/`PlanZoneGather` 的交互（同枪兵集结：Reserve 排除、玩家 intent 立即释放）。
4. 落点"矿线死角"选择：需不需要类落点规划器 flood_fill，还是敌方矿点附近 can_place 扫一圈够用。
5. 投送失败兜底：OL 被打死没视野 / 虫洞被秒 / army 卡在网络里 —— 各自兜底。
6. 演示 vs 胜率平衡：全 all-in 投送 vs 留点家底防反补——偷袭没成的转型别裸家。
