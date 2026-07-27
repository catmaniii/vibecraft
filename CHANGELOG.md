# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循
[PEP 440](https://peps.python.org/pep-0440/)。

VibeCraft 的 milestone 与版本对应（详见 `docs/plans/2026-05-14-vibecraft-design.md` §12）：

| Milestone | 版本号 | 含义 |
|---|---|---|
| M0a/M0b 完成 | `0.1.0a1` | 脚手架 + 全部"无 SC2"模块 + 单测 |
| M0c 完成 | `0.1.0a2` | 真实 SC2 smoke 验证通过（"不动的叉子"）|
| M1 完成 | `0.1.0a3` | 手机说话 → bot 切 1 个剧本 |
| M2 完成 | `0.1.0a4` | Directive Board 完整 + 3 剧本 |
| M3 完成 | `0.1.0a5` | 手机驾驶舱完整 |
| M4 完成 | `0.1.0b1` | LLM 解析 > 90% 正确率 |
| M5 完成 | `0.1.0` | MVP RC：vs Hard AI 调优达标 |

---

## [Unreleased]

### 2026-07-27 英文 README + 仓库名统一为 vibecraft

**新增 (Added)**：
- `README.en.md`：完整英文版（不是逐句直译——按英文读者的习惯重写了措辞与例句，18 类语音指令
  各给了地道的英文说法）。两个 README 顶部互相链接（🇨🇳 中文 · 🇬🇧 English）。
- 英文版里补了一段中文版没有的说明：**本项目对"验证"的判据**（单测绿 + 内部 trace 绿不算数，
  给 SC2 下命令的改动要有世界终态证据），以及"多数设计文档是中文"的提示。

**变更 (Changed)**：
- **仓库名统一为 `vibecraft`**：`docs/QUICK_START.md` 的 clone 地址原本指向
  `catmaniii/openVibeCraft`，与实际仓库对不上。
- **`scripts/sync_to_opensource.py` 标记作废**：它实现的是"私有 vibecraft → 公开 openVibeCraft
  脱敏投影"的两仓模型；现在本仓库**自己就是公开仓**（资产合规、脱敏、历史重建都已就地完成），
  不存在要同步过去的目标仓。脚本保留存档（脱敏规则清单仍有参考价值），加了醒目横幅说明别再跑。

### 2026-07-27 开源前准备：资产合规 + 脱敏 + CI 转绿 + 社区文件

**修正 (Fixed)**：
- **CI 一直是红的**（开源后第一印象 ❌、任何 PR 都过不了、贡献者分不清"是我改坏的还是本来就红"）：
  `ruff check .` 264 条 → **0**；`ruff format --check .` 228 个文件待重排 → **全部格式化**；
  `mypy src/vibecraft` 279 条 → **0**；`pytest` 4 条失败 → **0**（3731 passed）。
  其中两条是真问题，不是噪声：
  - `test_llm_anthropic::test_from_yaml_loads_fields` 读的是 **`config/llm.yaml`——一个 gitignore
    掉的本地私有配置**（可能含明文 key）。CI 上根本没这文件、每个贡献者机器上的值还不一样。改读
    入库的 `config/llm.yaml.example`。
  - `test_upgrade_target` 三条**单跑绿、全量红**：runner 用 `asyncio.get_event_loop()
    .run_until_complete(...)`，3.11 起若之前的异步测试已把当前 loop 关掉，这句自己就抛 —— 而它恰好
    被下面的 `except Exception` 吞成 False，表现为"封顶门没生效"。改 `asyncio.run`（每次自建 loop，
    不依赖全局状态）。顺带修了另一个隐患：其他测试往 `sys.modules` 注入的**假 sharpy 存根**会让
    `Tech` 继承假 `ActBase`，导入前先清整个 sharpy 命名空间。
- **`is_worker` 之外的同类隐患**：`src/vibecraft/bot/director.py` 两处类型注解用了没导入的
  `UnitTypeId`/`UpgradeId`（F821）—— 补进 `TYPE_CHECKING` 块。

**变更 (Changed)**：
- **SC2 美术资源不再随仓库分发**：163 个单位/建筑/升级图标（×2 份，共 24MB）是暴雪版权美术，
  与 `THIRD_PARTY_NOTICES.md` 里"本仓库不分发任何 Blizzard 资源"的声明冲突 → 撤出版本控制，改由
  `scripts/download_sc2_icons.py` 在本地拉取（脚本新增自动同步到 server static，**不装 node 也能用**）。
  同时移除 vendored sharpy 自带的 ladder 地图 `Equilibrium513AIE.SC2Map`（5MB，也是地图包）。
  跟踪文件 1566 → 1238。
- **脱敏**：作者的 VPS 公网 IP（`CLAUDE.md` / `TASKS.md`，6 处）与 Tailscale 主机名（3 个文件，5 处）
  换成占位符。仓库当前私有、这些 commit 从未公开过。
- **mypy 历史欠账显式化**：279 条错误里 154 条 `union-attr` 集中在 `director.py`。没有把 mypy 从 CI
  里删掉（那样新代码也失去保护），而是**只对既有热点文件、只对它们实际触发的错误码**加
  per-module override（33 个模块 / 57 个 code-slot），其余代码与所有新文件仍 strict。还清一条删一条。
- `ruff` per-file-ignores 补齐"改了反而更差"的模式：测试里的 `assert`/magic number/可变类属性、
  自验脚本里"轮询真实外部状态只能 sleep"的 ASYNC110、`sys.path.insert` 先于 import 的 E402。

**新增 (Added)**：
- `SECURITY.md`：私密报漏洞渠道 + **本项目的攻击面**（房间 token 就是访问凭据、公网暴露的含义、
  admin token、`/rg` 路由刻意无鉴权、LLM key 与"玩家说的话会发给服务商"）。
- Issue 模板（bug / feature）与 PR 模板；PR 模板里写明本项目的验证硬要求：**内部自洽不算数**，
  涉及给 SC2 下命令的改动要给出世界终态证据。
- **secret 扫描双保险**：pre-commit 挂 `gitleaks`（本地拦下），CI 也加一道（扫完整历史，PR 里挡）。
- `pyproject.toml` 补 `[project.urls]`、keywords、classifiers；description 从"操作 SC2 神族"改为
  三族（早就支持三族了）。
- 删掉误入库的 `CLAUDE.md.bak`。

### 2026-07-27 集结点改成"设个点"：坑道/凤凰都不再贴主基地站、也不再每帧被拽回去

> 用户真机："对面都打到家里来了，就因为你一直移动到家里这个操作导致他们参与不了防守，你就设个
> 集结点就完了嘛"；"凤凰的集结点也不要放到主基地上面，机场出来在哪就在那个位置集结"。

**修正 (Fixed)**：
- **集结中的单位参与不了防守**（坑道 + 凤凰同一个毛病，两条叠加）：①每帧 `set_task(Reserved)`
  → sharpy 的 `PlanZoneDefense` 只用 `free_units`，**根本拿不到这批兵**；②每帧 `move(锚点)`
  → 就算被战斗推开一步，下一帧又被拽回去，任何"还手/躲"的行为都被覆盖。
  现在：**家里挨打就把它们交还 sharpy 防守**（`clear_task` + 停发移动），威胁解除后下一帧自动
  恢复集结、状态不清、不用重新招募；平时也不每帧发移动，只在单位闲着或隔 4 秒以上才重发一次。
- **集结点不再压在主基地上**：坑道从坑道网络本体**往外让开 8 格**（朝地图中心，不挡矿线和建筑，
  位置也更靠来敌方向，装载只多走 2-3 秒）；凤凰改成**星门旁**（产出处，新凤凰一出来就在集结点上）。
  星门取"离主基最近的那座"——不用 `.first`（依赖单位帧间顺序、目标点会跳，违反目标锁定规则）。

**变更 (Changed)**：
- 凤凰未 launch 期的"交还防守"用**独立阈值**（家门口 ≥2 个敌方战斗单位），**不复用
  `recall_threshold`**：后者是"骚扰中途要不要召回"的开关、玩家可关成 0，绑一起会退化成"任何一个
  侦查兵靠近就放手"。
- `unit_kind.is_army` 判建筑时改用带默认值的取法：真 `Unit` 一定有 `is_structure`，但"缺一个字段
  就让整条判据失效"正是刚踩过的那类坑。

**新增 (Added)**：
- `PHOENIXRALLY` 诊断行（集结点坐标 + 离主基距离），真机确认 `集结点=星门(114,116) 离主基 13.6 格`。
- `NYDUSRAID stage_yield_defense` 诊断行，真局触发 3 次；同局投送不受影响（虫落地 7 次、最长
  存活 133.9s、`tgt=worker` 33%）。
- 6 条新单测（集结点确实让开/在星门、冷却内不重发、挨打交给防守、单个侦查兵不触发）。

### 2026-07-27 修 `is_worker` 假属性：钻出的部队从不扑农民（16 处误用，连单测都跟着错）

**修正 (Fixed)**：
- `sc2.unit.Unit` **没有 `is_worker` 属性**，且无 `__getattr__` 兜底。代码里 16 处在用它：写成
  `getattr(u, "is_worker", False)` 的**恒 False**（找农民永远查不到人；靠它排除农民的计数则把农民
  当军队算），写成 `u.is_worker` 的（BC 骚扰 4 处）**每次 AttributeError**、被外层 suppress 吞掉。
  真机症状：坑道虫钻出敌方家后 `tgt=worker` 恒 0%，只拆建筑打军队；而 telemetry 的
  `enemy_workers_harassed` 走伤害回调、不看这个假属性，所以有几十 —— 两个数字互相矛盾正是信号。
- 新增 `bot/unit_kind.py`：判据统一走引擎真实存在的 `type_id`（`is_worker()` / `is_army()`），
  15 处调用点全换。真局验证 `tgt=worker` **0 → 66 次**（与 structure 各半），自验判 PASS。
- **单测当初按同一个错误假设建模**（mock 里写 `is_worker=True`），产品与测试共享同一个幻觉，所以
  一直绿。mock 改用真 `type_id` 后 5 条老测试立刻转红、暴露真实行为。
- 两道防复发的门：①钉住"上游确实没有这个属性"（哪天 python-sc2 加了会红，提醒复核）；②**静态审计**
  ——全仓 `src/` 不许再出现 `.is_worker` / `"is_worker"` 字面量。

### 2026-07-27 运营面板五按钮真机实测 + 下架开矿封顶 + 修四处会骗人的自验门

**变更 (Changed)**：
- **下架「开矿封顶」后端路径**:前端 MacroButton 的开矿维度早已只剩「多开一个矿」(one_more),
  封顶 `expand=N/max/clear` 的入口没了、后端却还留着一整套状态机。删掉 director 的三条分支 +
  `_macro_expand_dir_id`/`_macro_expand_target` 两个状态 + 快照字段 `macro_expand_target`,
  ws 校验白名单收成 `{"one_more"}`,连同 8 条专测封顶的单测。
  **保留** `ExpansionOverridePayload` / `facade.set_expansion_override` / vendor Expand 的封顶
  hook —— 「多开一个矿」自己就是提交一张 `expansion_override(current+1)` 卡,LLM 说「最多开三个
  矿」也走同一条,删了会连带打断这两个还在用的功能。

**新增 (Added)**：
- `macro_action_selftest.py` 补两个场景:`expand_one_more`(多开一个矿)与 `workers_max`(全力补
  农民)——这两个恰恰是面板上有、之前却没被任何自验覆盖的按钮。

**修正 (Fixed)**：
- **四处会骗人的自验门**(假阴性/假阳性比没有门更糟,发现即修):
  ① `nydus_selftest.py` 的 5 个匹配串在 2026-07-12 落点重构后就失效了,**明明建了虫也报
  「BUILD_NYDUSWORM 从未发出」并判 FAIL**;顺带窗口检测那条改串后组序对不上,直接
  `ValueError` 崩在打分卡之后。
  ② `mining_priority_selftest.py` 的 TEST3 门写成 `avg >= 1`(几乎恒真、等于没门),而文档写的是
  ≥3 —— 收紧到 ≥3(实测 8.8 照过)。
  ③ 新加的 macro 判据一开始**比峰值**,而各局时长差一倍(对照组 868s、别的 390s),等于在比
  「谁活得久」→ 改成取共同可比时刻横向比,并跳过已被打崩(基地归零)的局。
  ④ `expand=2` 那条判据补上「baseline 同期确实超过了 2」这个前提,否则局太短时「没超过 2」
  根本不算证据。

**实测结论(vs Medium,五局都没被打崩,同一游戏时刻横向比)**:面板五个按钮**全部有效** ——
多开一个矿(基地 3 vs 对照 1)、优先水晶(气农民 4.7,trace `max_gas=3`)、优先气(11.3,
`min_gas=12`)、恢复默认(覆写被清空)、停农民(19 vs 35)、全力补农民(37 vs 30)。

### 2026-07-26 坑道虫:开局 OL 定点供视野 + 小高台驻守 + 佯攻/坑道抢单位修复 + 二次投放

> 用户在可视化图上圈定 OL 站位后提的一串要求 + 真局观察到的「狗被反复拉扯」bug。
> 落点/站位的几何结论全部经真机探针实测(见推理图谱 nydus-landing 域 F151-F160)。

**新增 (Added)**：
- **坑道虫可视化探针 `scripts/nydus_vision_viz_probe.py`**:真机放真 OL 到候选站位、给可落格画框、
  逐扇区巡览供截图,并 dump 地形/点位;配套 `scripts/nydus_vision_map.py` 渲染俯视全局图。
- **瞭望塔探针 `scripts/nydus_watchtower_probe.py`**:一锤定音"瞭望塔只认地面单位"——塔上仅 OL 时
  可见 421 格、加一只小狗 1528 格(塔视野 22),故 OL 飞上去拿不到塔视野。
- **小高台驻守点 `small_plateau_perches()`**(D102):与敌方主基**不相连**的小高台(4 连通分块 + 面积
  上限筛出),要求离可落格 ≤ 视野-1。高地遮蔽低地视野,OL 停那儿比停开阔低地更难被发现。
- **3x3 可放静态判据 `fits_3x3()`**(I56/F153):自身+周围 8 格都在 placement grid 才算放得下。真机
  校验对 `can_place` **漏判 0、只误报 6/27**,不需视野、开局即可筛掉废点。
- **多余女王去处 `plans/spare_queen_act.py`**(D106):自家坑道网络就绪很久仍没在敌方立住虫时,超出
  注卵需要的女王往最外分矿方向铺菌毯,到位后 `clear_task` 交回 sharpy 当前线防守兵。
- **女王在敌方家铺菌毯**(D105):钻出的女王在保留一发输血能量的前提下,往脚下菌毯外沿种瘤——坑道虫
  落地自带菌毯,所以在对方家种得下;共用几何抽到 `auto_combat/zerg/creep.py`。

**变更 (Changed)**：
- **开局第 0 帧就派侦查 OL**:`_SendOverlordToEnemy` 从 `Step(UnitReady(LAIR,1), ...)` 提到 BuildOrder
  顶层,派遣下限从 0 改 1(只有 1 只 OL 时也派)。真机实测首只 OL **游戏时间 00:12** 出发(改前要等
  Lair ≈4-5 分钟),全程未被补位=未阵亡。理由:开局敌方还没有任何对空,是送 OL 到位的唯一安全窗口。
- **OL 站位改为"小高台优先"**(D102):`overlord_station_points()` 统一入口,高台在前、够不着才退回
  D1 的"高地边缘顺悬崖外推 9 格到低地"。
- **坑道虫被拆后隔 40s 才重投,且重投禁用"硬落"兜底**(D104):原来 2s 就重下、25s 后还会绕过"主力不在"
  硬落 → 第二个虫落回刚拆掉第一个的兵堆里再被秒。冷却期发布 `nydus_retry_pending`,佯攻队据此继续
  出去引主力。
- **招募封顶改为按当前存活数**(F160/I59):原先是终身累加、阵亡不减 → 第一波打满 cap 全灭后再也招不到
  新兵,第二波在数据结构上就不可能。

**修正 (Fixed)**：
- **虫被拆后再也落不下去(真局 server_20260726_230748)**:三处叠加的根因,窗口(`army_away=True`)
  开了一路却一次都没投出去。①**拉黑是永久的**:虫被拆后拉黑落点、半径 3 格连坐,把全场**唯一
  被验证过"看得见+放得下"的点** (57.5,20.5) 连同隔壁 (59.5,19.5) 永久废掉——而虫死的原因不是
  点不好、是钻出来没人接应。改成 **60s 限时拉黑**,且"一个候选都过不去"时**第二轮无视拉黑重用
  老点"(有个虫总比没有强)。②**`fits_3x3` 筛子没接进落点选择**:只接到了 OL 站位推导上,真正挑
  落点的 `_edge_landing_tiles` 仍用未过筛的边缘格 → 日志里唯二有视野的候选全是 `place=False`。
  ③**每只 OL 只贡献 1 个候选**(离它最近那个,恰恰最贴崖、最放不下)→ 改成贡献视野内最近的 6 个。
- **重投冷却从"门"降级为"防抖"(40s → 8s)**:用户真机反馈"狗去引了主力、虫却没跟着下"。根因是
  **定时器会否决佯攻自己创造的那个窗口**——虫一被拆佯攻立刻出去引,窗口常在 T+5~T+25 就开,却被
  压到 T+40 才准下,那时主力已回防。现在 8s 只防抖,之后每帧都试,**由 ④ 号门(主力不在落点区)
  决定何时下** —— 那一刻就等于"佯攻把人引走了",投放时刻自动与引开时刻对齐。
  `nydus_retry_pending` 的发布范围也从"冷却那几十秒"扩到**整个重投期**。
- **玩家喊「放坑道虫」毫无反应**:LLM 把它解析成 `cast_ability`,ability 名是**编的**
  (`NYDUSWORMLOCATION_NYDUSNETWORK`,SC2 无此枚举)→ 旧路径只打一条 unknown ability 警告、
  `cast 0 times`。而放坑道虫本就不是"对单位放技能",是 **NydusNetwork 的建筑能力 + 目标坐标**。
  改成 facade 按关键词认出这类 ability 名(不指望 LLM 拼对),翻译成**玩家强制投放意图**
  (`nydus_force_drop_until`,60s 时限):无视拉黑 + 按 COMMIT 放宽窗口去投。
  真局自验 `scripts/nydus_force_drop_selftest.py`(mock LLM 原样重放那条出错 directive)PASS。
- **佯攻队与坑道队抢同一批狗导致来回抽搐**(F159/D103):真局证据——首波装载让佯攻队门打开、它 2 秒后
  抓走 6 只狗,其中有坑道队早已认领的;两个 act 每帧对同一只狗下相反命令(一个 `move(家里网络)`、
  一个 poke 敌方分矿),`total_staged` 卡在 6 不动、装载指令每 1.8s 空发 70+ 秒。原来只有单向排除。
  改为**双向互斥 + 明确让渡**:坑道队每帧让出佯攻队认领的(坑道内的除外),佯攻队优先拿自由狗、不够
  才拿坑道队标记为可让渡的集结狗。
- **自验脚本会骗人的门**:`nydus_selftest.py` 的两个匹配串在 2026-07-12 落点重构时改了名没跟着改,
  明明建了虫也报"BUILD_NYDUSWORM 从未发出"并判 FAIL。这类假阴性门比没有门更糟,发现即修。
- **推理图谱 D1 长期漂移**:写的"外推 7 格",代码是 `_OL_PUSH=9.0`,按代码订正并补判据。

### 2026-07-26 凤凰地形感知重构:安全集结点(矿后悬崖口袋)+ 接近走口袋 + flee 退口袋

> 用户多轮真机反馈 + 心法:空军默认蹲安全区、伺机上去杀、敌来退安全区、一矿二矿腾挪。安全集结点=
> 出视野+地面够不到+高地外。经真机地形探针 + 独立 opus 评审(否掉全局 A*、改预计算走廊)后实现。

**新增 (Added)**：
- **地形基础层 `bot/terrain_harass.py`**(种族中性,所有空军骚扰复用):`find_mineback_pocket`
  (靠 terrain_height 突降=矿后悬崖找安全口袋——真机探针证:静态 pathing_grid 不编码矿脉,可靠屏障
  是悬崖)+ `bfs_ground_reachable`(从可走格起步,避"seed 落建筑卡死"坑)+ `point_ground_reachable`。
- **真机地形探针 `scripts/phoenix_terrain_probe.py`**:敲定判定基=靠悬崖不靠矿脉(F114)。

**变更 (Changed)**：
- **安全集结点=各敌方矿的矿后悬崖口袋**(`_safe_gather_points`,按局缓存):真机验证 3 矿全算出真悬崖
  口袋(落差 72-96)。`_regroup_point`/all_defended 退到它(不回家/不中场暴露)。
- **接近走安全口袋**:`_approach_waypoint` 目标从几何 stage 换成矿后悬崖口袋,路由 start→口袋→behind
  (先到地面够不到的安全矿后、再 dive 打);真机 air_frac 0.33-0.48(多数路径走地面够不到处)。
- **flee 退最近安全口袋**:打不过时优先退到最近的、不挨打的悬崖口袋(心法'敌来退安全区'),无口袋才
  穿梭/orbit。

**效果**:真机 build_acceptance vs VeryHard:被骚扰敌方农民 3 局中位 3(需≥3)PASS,验收全维度 PASS。
64+ 单测、ruff/mypy 干净。图谱 harass-doctrine 层(F109/F112/F114 + D70-88)完整记录,通用 doctrine
与地形层可供未来女妖/飞龙/BC 复用。

**再补(2026-07-26c/d 心法落地)**：
- **bail/回盾退最近安全悬崖口袋(不回家)+ 整队一起**(D82,修"没血还往家逃"):micro `_nearest_safe_point`
  选最近不挨打口袋,整队同一个=在一起。
- **主动多线拉扯**(D85/F118):harassable 只看对空(去对空少的矿),敌大军镇的矿靠 AA 门 + bail/flee→口袋
  处理,不原地等。
- **教训 F120(反向审查 D83)**:对凤凰"矿被镇/defended"= **有对空(AA)不是有地面军**——凤凰不能被地面
  打,地面军再多也 snipe 农民;曾加"数地面军"force 门,真机 KPI 4→2、53% 时间 regroup → 回退,只留 AA
  门。空军的"威胁"判据只算能打到它的(对空),别算打不到它的(地面军)。
- **待续(refinement,配真机肉眼评估调)**:D87 集结点/接近避敌静态建筑视野、D88 途中遇敌切矿后、
  D86 穿梭段全避敌绕行——这几块几何细节最好看实际走位再调。

### 2026-07-25 凤凰真机反馈:少数迁就多数 + 集火跨 posture(修 2/3,#2 路线待做)

> 用户 2026-07-25 真机三反常:①前线主群已集结却为等一只落后凤凰整体后拉(多数迁就少数);
> ②接近仍直穿对方主基地面可达处、没绕矿后贴边;③有凤凰在抬人时其他凤凰飞走没集火。
> 用户强调这是"始终遵守"的原则,并指出我没真正用推理图谱推演凤凰战术。

**修正 (Fixed)**：
- **少数迁就多数(#1,图谱 D57/D58)**:凤凰接近聚拢改用**主群(最大 cohesive 簇)中心**判定,
  不再用含落单者的全体质心(`phoenix_squad_micro._main_body_center` 替换 `_squad_cohesive`)。
  主群继续沿 approach_wp 推进/到点打,落单/新出的去追主群中心,**绝不让主群回头等落单者**。
  单测 `test_approach_majority_advances_minority_catches_up`(铁证:主群不回拽全体质心)+ 真机
  trace(approach danchor 单调降不回弹)。
- **集火跨 posture(#3,图谱 D59)**:被抬集合(GRAVITONBEAM buff 未结束 + 本 tick 抬的)每帧
  **无条件计算**,不再 gate 在 posture==fight;集合非空则非抬手凤凰一律 A 最近被抬单位,优先级
  高于 approach/flee、仅低于个体 bail。修"posture 一翻 flee/approach 就弃抬飞走"。单测
  `test_focus_fire_lifted_persists_when_posture_flee` + 真机 trace(73 个 lifted>0 tick 全部
  atk>0,含 approach 态 lifted=1/atk=9 的铁证)。
- trace 加 `lifted=` 字段(被抬集合大小),供 #3 真机验证。

- **地形感知空军接近选路(#2,图谱 D60)**:凤凰接近改走**地面部队去不了的地方**(悬崖/高地边缘外
  空域),不再直穿敌方主基附近地面可达开阔地被拦截。实现 `drop_path.plan_air_path`(snap 版):在已
  验证的几何路径(`plan_avoid_path`,保矿后切入)上把中间点局部 snap 到最近的**地面不可走(悬崖)**格,
  半径受限(不全局改道)+ 全局绕路守卫(snap 后 > base×1.35 弃 snap,防绕大圈)。极性坑规避:用
  `ai.in_pathing_grid` 封装判可走(不自己读 raw grid 值猜极性,见 pitfalls 2026-07-25)。**独立设计
  评审(opus)否掉全局 A***(会重演被否的 edge_path 绕大圈 / 丢矿后切入角 / 极性未验证)。真机验证:
  air_frac 0.36-0.43 << straight 0.75-1.0(接近路径少走地面 50-64%)。单测 `test_air_path_*`。

**推理图谱 (Reasoning Graph)**：加 D57(少数迁就多数原则,通用骚扰/机动小队)+ F96/F97/F98(三真机
反常)+ F99/F100/F101(代码机制根因,own-code)+ D58/D59/D60(三修正决策);反向审查重写 D53(原
"全体向质心等齐"条款=多数迁就少数,被 F96 证伪)。独立 subagent 复评并落地其 findings(补建被引用
的机制节点、剥掉 fact 里的推理链、精简 statement meta)。

### 2026-07-25 PWA 宽屏/PC 布局优化(视频左大 + 右面板独立滚 + 高分屏放大字号)

> 用户 2026-07-25:宽屏/PC 要视频左边尽量大、右操作面板独立上下滚(左视频固定)、面板宽度
> ≈手机竖屏(别太窄);PC 高分屏适当放大字体;竖屏 sticky 保持不变。

**变更 (Changed)**：
- **横屏/宽屏/PC**:左列(视频)尽量大(LiveView `landscape:flex-1` + 取消 `max-w-sm`/40vh 上限);
  右操作面板固定 `landscape:w-[22rem]`(比初版 26rem 收窄,给视频让位);左列固定不随右面板滚。
  科技/产能/兵种面板(TechProgressPanel)横屏改由 App.vue **左列视频下方**渲染并固定,充分利用左列面积
  (CockpitView 内那份加 `landscape:hidden` 避免重复),右面板因此更窄。
- **修复:横屏滑右面板带动左视频一起滚(2026-07-25 用户报)**。根因移动端 `h-screen`(100vh)含地址栏
  高度→整页溢出可滚,拖右面板滚动链传到 body 把视频带上去。改主容器 `landscape:h-[100dvh]`(动态视口
  高,排除地址栏)+ 单一滚动容器(去掉 App 右栏与 CockpitView 双层 `overflow-y-auto`,只留 CockpitView
  `min-h-0 overflow-y-auto`),左列 `overflow-hidden` 彻底不滚。视频改 `landscape:max-h-full`(跟随左列
  剩余高,不再 `max-h-screen` 超出)。
- **PC 高分屏放大字号**:`style.css` 按视口宽度阶梯放大 html 根字号(1280→17 / 1600→18 /
  1920→19 / 2560→21px),Tailwind rem 尺寸整体等比放大(视频 vh/面板固定 px 不受影响)。
- **竖屏保持不变**:sticky 视频 + 整页滚原样(仅加 `landscape:` 变体,portrait 默认路径未动)。

### 2026-07-22 凤凰限量抬人 + 多矿迁移绕矿后(真机反馈:抬人策略 + 路线)

> 用户 2026-07-22 真机反馈:凤凰堆叠 + 敌基内迁移路线不错;两个待改进——①抬人现在能抬的全
> 抬了只剩 1 个打→抬起来打不死又掉下去,平时最多抬 1-2 个留够攻击的;②主→二矿迁移应绕主矿
> 矿后再绕二矿矿后、贴高地边缘,别穿主基内部。推理图谱 D48(限量抬人)/ D49(绕矿后路线)。

**新增/变更 (Changed)**：
- **凤凰限量抬人**:`_assign_lifts` 每 tick 限量分配 lift——平时最多 `_MAX_LIFTERS_NORMAL=2` 只抬 +
  去重目标(不同凤凰不抬同一个),其余凤凰贴身 attack 把抬起来的打死再换目标;只有附近可抬对空兵
  `>= _AA_HEAVY_LIFT=3`(地对空火力猛)才放宽多抬(抬起对空兵压制保命)。修"能抬的全抬→只剩 1 个
  打→打不死又掉下去白费"。`_squad_can_fight` 判存活已改用凤凰总数(上一条)。trace5:fight 时 lift
  数恒 ≤2、另有 2-4 只攻击。
- **多矿迁移绕矿后**:`_avoid_centers_except` 让接近路径避开**所有**敌方基地中心(除目标矿自己)、
  不只主基 → 主↔二矿迁移绕外侧、贴高地边缘走,不穿基地内部(空军多借地面够不到处保命)。

**测试**：新增 test_lift_cap_normal_max_two(5凤凰5农民→恰 2 抬 3 打)+ test_max_lifters_scales_with_heavy_aa;34 单测全过。

### 2026-07-22 凤凰骚扰"全程判该不该打/跑"+ 护盾 recover 修(修真机反馈的两个病)

> 用户 2026-07-22 真机反馈:凤凰攒够一起走后,到敌人军队跟前**又不打又不走、一直往那点去**
> (最坏选择);"能不能打/该不该跑应该是骚扰状态全程判,不是到点才判"。trace 又扒出第二个病:
> 打一波掉血后整局躲家不出。推理图谱 phoenix-micro:F92/D46(全程判)/F93/D47(护盾recover)。

**修正 (Fixed)**：
- **只到矿后区才判 fight/flee → 飞到军队跟前既不打又不走反复拉扯(最坏选择)**:旧 `_squad_posture`
  把 can_fight 判定 gate 在 in_zone 之后 → squad 飞到军队跟前才发现打不过、flee 只 orbit 到区边缘
  又被 approach 拽回。改成**每帧先判 can_fight**(骚扰状态全程判,不是到点才判):打不过一律 flee
  绕敌撤(接近途中被拦也走)。配合 act 层 `_pick_harass_geom` 的 **harassable 预判**(对空 > 凤凰
  总数×gate 的矿根本不选、都打不过退安全待命点 `_hold_point`)——去之前 + 路上双层判"要么打要么走"。
- **凤凰打一波掉血后卡死撤退态、整局躲家不出**:`_should_bail` 的 recover 判据用'(血+护盾)总比 >=
  阈值',但神族**血不回只护盾回**,掉了血的凤凰护盾回满总比仍够不到阈值 → 永远"撤退中"躲家
  (trace 实测 danchor 卡 80-114=在家 250s、0 再交战)。改 recover 只看**护盾比 >= 阈值**(护盾才是
  会回的 buffer),血低不卡死。
- `_squad_can_fight` 判存活改用**凤凰总数**(不只 lift 能量的)——存活靠全队 DPS+血量 soak,与能量
  无关,且与 act 预判同口径,不会 act 说去 micro 又说打不过拉扯。

**验证**：trace4(全程判+护盾修后)vs trace3:posture fight 2→12 / flee 0→9,交战 atk 5→55,danchor
从"打一波后卡家 80-114"变"反复回落 28-37 再交战"(真 hit-run),0 异常;32 单测(新增 harassable
预判/all_defended退待命/护盾recover 等)全过。走位 trace `VIBECRAFT_PHOENIX_TRACE=1` 加 tgt 字段。

### 2026-07-20 凤凰骚扰按"保存实力 + 矿后侧切"6 规则重写微操(phoenix_2base/control 共用)

> 用户 2026-07-20:凤凰骚扰全按统一原则+状态机来。最高原则=**保存实力第一,绝不送**
> (活着的凤凰=威慑=己方开矿安全)。最大的病=凤凰直穿敌方主基几下打光。推理图谱
> harass-doctrine 域(F88/D42/D44 空军骚扰通用生存法则)+ phoenix-micro 域(D41 6规则/
> F87 精髓/F89/D43 凤凰只有护盾续命不照抄BC传送+修)。

**变更 (Changed)**：
- **`phoenix_squad_micro.py` 重写为整队 fight-or-flee 状态机**(替代旧"每只各自 lift/kite/bail"):
  每 tick 先算**整队一个 posture**再分派——①**approach**(没到矿后区)全队沿 caller 给的
  矿后侧切路径前进;②**fight**(到矿后区+对空够少 can_fight)整队一起打:能 lift 的抬(对空优先/
  农民次)、不能 lift 的**贴身 attack-move**(move-shot 自动开火,绝不 kite 保距——保距=DPS不够);
  ③**flee**(对空太多打不过)全队 **orbit 绕敌撤**(以对空为圆心绕到安全半径外的另一角,不原路
  返防来回拉扯)。个体血危(HP+护盾<bail)永远最高优先回家。fight gate=对空 <= 能lift凤凰×0.5。
- **`phoenix_squad_act.py` 加矿后侧切接近路径(rule 4,借鉴 BC GroupHarassAct)**:选目标矿区
  (主/二矿按"农民多、对空少"评分+切换滞回)→ 矿后锚点(矿线背基地侧偏移,地面军够不到)→
  `plan_avoid_path` 避开敌方主基地从矿背后切入(不再直穿主基直冲矿线)+ 末段矿后点,一次锁定缓存。
  micro 新签名 `solve_squad(phoenixes, harass_anchor, approach_wp, ai)` + 新 "attack" 动作类型。
- **续命机制不照抄 BC**:BC 有战术传送回家 + SCV 修,凤凰都没有,只有神族**护盾脱战慢回**→
  凤凰 heal=飞离交战晾护盾、护盾滞回回到 recover 阈值再上(无传送、无找农民修)。
- 保留:rule 1(wave_threshold=5 才出门)、rule 6(recall_threshold 大部队来攻召回归队)。
- **加 greppable 走位 trace**(`VIBECRAFT_PHOENIX_TRACE=1` 开):打 posture + squad 位置 + 到敌方
  主基距离 + 接近路径 waypoint,真局 grep 验矿后侧切几何(PHOENIXPATH/PHOENIXTRACE)。纪律化:
  今后所有空军骚扰 build 默认都要插这类 trace(CLAUDE.md「玩家指令链·真局自验法」新增条)。

**修正 (Fixed)**：
- **接近途中目标矿横跳导致凤凰永远进不了矿后区**(trace 抓的 bug,单测+telemetry 照不出):
  `_pick_harass_geom` 每帧按"农民多"重选主/二矿,接近途中哪个矿农民多就切哪个 → 接近路径每次从
  当前位置重算 → 凤凰追移动靶,posture 卡 approach(trace 实测 133 approach / **0 fight**)。修:照 BC
  的"到达门"——squad 距当前目标矿后锚点 > `_ARRIVE_DIST` 前**锁死当前目标不切**,只有已抵达矿后区
  才按评分+滞回穿梭。修后 trace 出现 fight(lift/atk 真交战)+ flee(打完撤保存实力)。同"目标坐标
  一次锁定别每帧重选"纪律(CLAUDE.md 实现纪律),这次漏在**目标矿区**层没锁。

**测试 (Tests)**：`test_phoenix_squad_micro.py` 重写(posture approach/fight/flee + orbit撤退 +
个体bail覆盖 + can_fight gate + _try_lift 两gate,29过);`test_phoenix_squad_act.py` 沿用(wave/
recall/reserve/notify)。

### 2026-07-19 phoenix_2base 靠运营提升胜率:四矿暴农 + 后期不朽收尾(33%→42%)

> 用户 2026-07-19:凤凰主要是控场杀农民、目标是保自己安全扩张,靠运营(经济)提升胜率,
> 后期加别的兵种收尾。推理图谱 phoenix-micro 域 D36 / F78。

**新增 (Added)**：
- **`phoenix_2base.py` 四矿 Expand(4)(gate 12凤凰)+ 六气 + 农民 60→76 + 后期不朽 Immortal(6)**:
  凤凰控场杀农民 = 压制对方经济 + 保自己扩张安全 → 多开矿暴农滚经济优势,后期不朽(现有重工出、
  强 vs 重甲)把经济变成能收尾的军队。真机 12局VH:**5/12(42%,基线 2/6=33%)**,不朽出 5-6、
  农民 76、组合=凤凰+叉子+追猎+不朽。残留两败因(F78):①早死(农民30崩,开局脆)②发展了却
  正面输(农民75+不朽5仍败,胜局凤凰24-45 vs 败局12-22)。

### 2026-07-19 phoenix_2base 加叉子吃富余矿 + 补地面军

> 用户 2026-07-19:凤凰不要光是凤凰,看哪个资源富余——矿富余就出叉子。推理图谱
> phoenix-micro 域 F77(纯气凤凰中期矿富余 600-870)/ D35(加叉子决策)。

**新增 (Added)**：
- **`phoenix_2base.py` 加叉子(Zealot)+ 议会 + 冲锋**:原版只出凤凰(150矿/100气)+
  追猎(气兵)、整局 ZEALOT=0,中期矿囤到 600-870(气出凤凰、矿花不掉)。加叉子(100矿/0气,
  纯矿不抢凤凰的气)soak 富余矿 + 补地面军(凤凰空军守不了地);议会+冲锋(一次性 100/100气)
  让叉子能贴上去。`ProtossUnit(ZEALOT,14)` 非 priority(只吃富余矿)、gate 在 8 凤凰后
  (不耽误凤凰开局)。真机 6局VH:叉子出到 13、中期矿压到 30-530、最终 42凤凰+13叉子+4追猎
  =200供给;胜率 2/5≈2/6(组合微调对 VeryHard 中性,价值在兵种组合完整 + 地面防守)。

### 2026-07-19 凤凰对空 kite 改集群绕飞(phoenix_2base 素凤凰骚扰微操)

> 用户 2026-07-19:凤凰要①集群群体一起移动 ②一直移动不停 + 边移动边攻击保持射程。
> 并指正凤凰是 move-shot(下 move、敌人进射程即自动开火,无需 attack)——记入推理图谱
> 新域 phoenix-micro(F74/F75/F76/D34)。

**变更 (Changed)**：
- **`phoenix_squad_micro.py` 对空 kite(step4)改全队共享绕敌群 orbit 点**:旧版每只凤凰
  对自己最近的空敌单独 `_kite_waypoint` → 交战就各自散;新增 `_group_kite_waypoint`——
  围绕 squad_center 扫敌空军群、全队共享一个绕敌群中心的 orbit 点(同 anchor + 同 ai.time
  角度 → 全队同一目标点、一起移动),半径=射程-0.5(停在射程边缘,靠凤凰 move-shot 自动开火)。
  实现集群 + 永动(绕圈不停)+ 保持射程。新增单测 `test_group_kite_air_shared_waypoint`
  断言多凤凰拿同一 kite 点。

### 2026-07-19 two_base_tanks 防守 macro(2/6→4/5) + 修 muta_ling_bane 的 Lair bug

> 用户拍板(2026-07-19)：two_base_tanks「纯 macro = 坦克防守 + 开矿，别进攻」；ling_bane
> 「打一波以后转运营」。

**修正 (Fixed)**：
- **`muta_ling_bane.py` 补 MorphLair(真机 bug)**：科技链 `母池→妖虫巢→进化腔→Spire` 里 Spire
  直接建、**没先升 Lair**——而 Spire 的硬 tech 前置是 Lair → GridBuilding(SPIRE) 永远放不下 →
  整局 **MUTALISK=0**(气囤到 1453 却造不出飞龙，ling_bane 转型自验暴露)。加
  `Step(UnitReady(POOL), MorphLair())` + Spire 门控到 Lair ready 后。修复后飞龙真部署(ling_bane
  转型胜局 MUTA 峰值 8-10)。

**变更 (Changed)**：
- **`two_base_tanks.py` 改防守 macro（别进攻）**：旧版强出门 timing push(attack_on_advantage=False)
  枪兵球正面崩、只 2/6。按用户改成龟防运营——`attack_on_advantage=True`(只经济+军队领先才出门、
  否则缩家展开坦克线)、start_attack_power 30→60(攒够一大坨再动)、加 Expand(3)/(4) 多矿、坦克上限
  8→16、农民 50→70、轨道 2→3。vs VeryHard **2/6 → 4/5(80%)**：靠 out-macro + 对面撞死在坦克线上。

**测试 (Testing)**：
- **ling_bane 触及 1 矿经济天花板 ~2/6**：muta Lair 修复后飞龙能出(2/8→2/6)，但 1 矿 all-in 经济
  太穷、军队封顶 ~150 供给打不过 VeryHard 后期。试过转 roach_hydra_viper(科技不匹配、从零建全套 tech
  太慢 0/6)、降触发(第一波变弱 0/6)均更差，均已还原。2/6 是其经济结构天花板。

### 2026-07-18b 「第一波出来就马上转型」原则落地：banshee 1/6→8/12，提炼早转型双条件(I35)

> 用户原则(2026-07-18)：这些 aggressive/harass build「运营出来第一波之后就要马上转型，
> 占到便宜或没占到都要转」。据此系统重测 4 个转型型 build，telemetry 逐个查"转型有没有真触发 +
> doctrine 有没有部署出招牌兵"。推理图谱 opening-winrate-opt 域反向审查(撤 I34/F61，立 I35/F64-F67)。

**修正 (Fixed)**：
- **`banshee_harass.py` 触发条件降到"第一波(≥1 女妖+6 兵)出即转"**(commit f1323ac)：根因(telemetry
  实证)——旧 `_opening_done` 要"隐形完成+2 女妖+6 兵"，但女妖产线被 priority 枪兵抢矿、整局只造出
  1 架女妖 → 条件永不满足 → `opening_completed` 从不触发 → 转 bio_max 从没发生(卡残废开局、MARAUDER
  全程 0、矿囤 3848)。所谓"banshee→bio_max 1/6"其实是转型从没发生。降触发后转型 t=402 触发、bio_max
  接管产 MARAUDER、军队 200 供给 → **vs VeryHard 1/6 → 8/12(67%)**。

**测试 (Testing)**：
- **roach_allin→roach_hydra_viper 大样本 3/6、widow_mine→bio_max 3/5**：这两个触发本就正常 fire、
  目标 doctrine 从继承经济成功部署出招牌兵(蟑刺蛇大军 / marine57+marauder16+medivac12 bio 大军)，
  无需改码，从旧小样本 0/3 上修。
- **ling_bane 反例(已还原)**：降触发到"狗≥8 即转"后转型确实触发，但目标 muta_ling_bane 整局
  MUTALISK=0——1 矿 all-in 经济喂不起 Spire 飞龙，只狂刷妖虫自杀 → 0/6 反比原 2/8 更差，还原。
- **提炼原则 I35**：早转型救 all-in/骚扰有效当且仅当 ①触发条件真能 fire ②目标 doctrine 能从继承经济
  部署出招牌兵。缺①转型不发生(banshee 旧版)、缺②转型有害(ling_bane)。此发现证伪并取代了前一条的 I34
  (原"产线复用"机制)。

### 2026-07-18 低胜率 build 优化收尾：最后 5 个 aggressive/harass build 触及实测天花板

> 推理图谱 domain `opening-winrate-opt`(F58-F61/I32/I33/**I34**)。承接"先 all-in 再转运营"体系
> (mass_reaper→bio_max 0/3→5/6),对剩余 5 个低胜率 build 逐个试到胜率不再提升。

**测试 (Testing)**：
- **`banshee_harass_macro.yaml` 新增 banshee→bio_max 转型评测**:6 局 VeryHard 得 **1/6**,与 banshee
  单独优化后持平——转型对它无改善。提出工作假设 **I34(unverified,n=2 溯因待正交验证)**:转型救
  all-in/骚扰的成败**可能**取决于**开局产线能否复用进目标 doctrine**。mass_reaper 死神走兵营、与
  bio_max 步兵产线共用(F62)→ 转型继承强经济,5/6;banshee 需星港+科技实验室的独立空军产线、与 bio_max
  不共享(F63)→ 那笔投入白费、经济落后,1/6。此假设未排除混淆项(banshee 骨架本更弱/6 局方差),要坐实
  须补第 3 个"产线可复用"的 build 也成功的正交实验;若成立则 I33"转型有效"有前提:开局产线要能复用进
  目标 doctrine。
- **最后 5 个 build 定性**(均 aggressive/harass/timing 型,vs VeryHard 结构性吃亏):two_base_tanks
  2/6(macro 坦克,4 兵营+L2 实验 1/4 无改善已还原)、banshee_harass 1/6、ling_bane 1/6、roach_allin
  0/3(转型太慢)、widow_mine_drop 0/3 单独 / 1/3 带 bio_max。已应用全部主杠杆(I31 门槛/关 advantage、
  I32 harass release_after+升级、I33 转型评测),触及实测天花板。

### 2026-07-16 ultralisk doctrine larva 闲置修复：小狗上限 30→80 当矿/larva 出口

> 推理图谱 domain `doctrine-reopt`(F49-F51/I28/D32)。原计划 re-opt 的两个 doctrine 中,brood_corruptor
> 的 §10 bank 1311 **不复现**(实测经 roach_hydra 是 233,健康,method-log 无测法背书)→ 无病、跳过;
> ultralisk 实测 `larva_idle 2.76`(larva 大量闲置)是真问题。

**修正 (Fixed)**：
- **`zerg/ultralisk.py` 小狗上限 30→80**:雷兽 gas 受限,富余矿/larva 无出口 → 小狗(mineral-only)当
  出口吸掉。实测(3 seed)`larva_idle 2.76→~1.28`(降 54%)、`avg_excess_bank 486→~358`(降 26%)。
  (排查中发现改 `target_composition.Zergling` 零效果——doctrine 产兵驱动在 plan 类的 `ZergUnit`,
  yaml target_composition 只是显示元数据;两处已同步。)

### 2026-07-16 zvp_macro 胜率优化：降暴农一处，vs VeryHard 0/3 → 6/6（推理图谱驱动）

> 走推理图谱 skill（新 domain `zvp-macro-opt`，F44-F48/I27/D31/Q6）诊断:zvp_macro 沙盒经济
> 健康(bank 201)但 vs VeryHard 全败——hatch-first 过贪,峰值 62 农民 vs 军队仅 48 supply、蜂后迟到
> 200s,军队没成型就被时机一波打死。

**变更 (Changed)**：
- **`zerg/zvp_macro.py` 降前期暴农**:parallel drone 目标 24→20、mid 66→48(base×16 饱和线)。空出的
  larva/矿更早转蟑螂,军队在 VeryHard 时机前成型。实测 vs VeryHard **0/3 → 6/6 全胜**(2 轮 3 局),
  supply 峰值 66→200、蟑螂 38、attack 出门 335s。(遗留:queen_pair/spore 仍 0/3,未影响当前胜负。)

### 2026-07-14 知识图谱可移植 skill 化（内部研发工具，通用件抽成全局 skill）

> 知识图谱（KG）子系统用了两个月，通用部分（方法论/检索/校验/可视化）跟本项目无关，抽成全局
> `~/.claude/skills/knowledge-graph/` skill，任意项目复用；数据（`docs/knowledge-graph.yaml`）留在
> 本 repo。顺带消灭一处文档双源漂移。

**变更 (Changed)**：
- **通用件迁全局 skill**：方法论文档、`kg_query.py`（检索）、`kg_validate.py`（一致性门）、
  `kg_render.py` + `kg-viewer.html`（可视化模板）迁到 `~/.claude/skills/knowledge-graph/`；repo 内
  旧 `.claude/skills/knowledge-graph/`、`scripts/build_kg_viz.py`、`scripts/kg_query.py`、
  `scripts/_templates/` 已删，repo 侧测试改薄壳调用全局 skill 脚本。
- **废弃人读 `docs/knowledge-graph.md` 第二源**：曾是 `knowledge-graph.yaml` 的人读复制品 + 独占
  变更日志，双源漂移，已删；变更日志迁回 yaml 的 `changelog` 段单源维护。
- **可视化统一 `kg-viewer.html`**：支持两种喂法——拖 `.yaml` 文件本地渲染（任意项目通用），或本项目
  `/kg`（`/knowledge-graph`）路由服务端注入实时数据；`/kg` 刻意无门控、公网可见（用户定：KG 非敏感，
  换裸 URL 便利，代码有 SECURITY 注释）+ 渲染失败错误信息脱敏（不回显主机路径）。
- **CLAUDE.md「知识图谱驱动决策」章**指向全局 skill 路径；新增「设计架构时维护单一数据源」实现纪律
  （静态数据源能免则免多份，需要就明确唯一真理源 + 一致性门 + 派生副本可重新生成不手维护）。

**详见** `docs/plans/2026-07-14-knowledge-graph-portable-skill-design.md`（设计）+
`docs/plans/2026-07-14-knowledge-graph-portable-skill.md`（实施计划）。

### 2026-07-13 人族「爆死神」(mass_reaper) build + 死神微操大改（harass 1→26 农民，vs VeryEasy Defeat→Victory）

> 用户 2026-07-13 拍板新 build：上来双气满采 → 兵营持续爆死神，微操 kite 保命、积累数量骚扰逼死
> 对方经济，余钱开气/开矿/补农民/扩张，气够就一直出死神。迭代中真机复盘挖出一串微操根因。

**新增 (Added)**：
- **`strategies/terran/mass_reaper.yaml` + `terran/plans/mass_reaper.py`「爆死神」opening**：双气满采 →
  兵营随气 scale（1 基 3 裸兵营 / 4 气→5 / 6 气→7）持续死神；死神 kite 保命积累；余钱持续扩张。
  core_units `[{REAPER, mass}]`；aliases 爆死神/死神海/…；matchup TvT/TvZ/TvP。

**修正 (Fixed) —— 死神微操 `harass_act.py`（惠及所有 reaper/banshee/hellion/muta 骚扰 build）**：
- **stutter-step 铁律修 harass 恒 0 的隐藏根因**：旧逻辑每帧都可能发撤退 `move`，把 `attack` 的**前摇
  打断** → 死神发一堆 attack 命令却**一枪都没打出去** → 46 分钟 harassed=0。改成**武器好 + 射程内有
  农民 → 必须开火，绝不用撤退取消这一枪**，只在冷却帧才 scoot。
- **双模式目标选择（用户拍板，harass 突破的关键）**：区分「有没有敌方部队」——**有敌军 → attack-move
  跟它拉扯对打**（reaper 射程+速度优势 kite 军队）；**兵少/没兵 → 重点关照农民，用 `move`（非
  attack-move）精准够农民**（不被沿途军队/建筑带偏白打）。这一改把 harass 从 1 → **26 个农民**、
  vs VeryEasy 从 Defeat → **Victory**（之前成群死神贴着 22 农民却只 harass 1，全 attack-move 撞在
  敌军身上）。
- **智能撤退落点（用户两点观察）**：① 撤退不再"等挨打才退"——**有单位靠近/被包围就退**（≥3 农民扑到
  3 格内触发，区分扑脸农民 vs 采矿农民，采矿农民在维持 5 射程时够不到不误触发）；② 撤退方向不再是
  "最近攻击者的纯反方向"（会退进死角卡住）——改**远离攻击者质心 + 朝家偏置 + 可走扇形校验**（落点不可
  走就 ±25°/±50°/±80°/±110° 转着找能走的），且"攻击者来自家方向"时自动丢弃朝家偏置。
- **攒够再打（"积累数量"）**：真机复盘发现 `prod_util=0.987`——兵营几乎满负荷产死神，数量上不去**不是
  产能问题，是死神 1 个 1 个扎进敌军 solo 送死、攒不起来**。加分级：存活死神 < 5 → 前沿集结点待命
  不裸送，≥5 才成群骚扰，被打到 ≤2 撤回重攒（滞回防抖）。reaper_mass 从 4 → **17**。

**修正 (Fixed) —— 经济 `mass_reaper.py`**：
- **持续扩张修"矿飘 3-5 万"**：死神 50 矿 50 气(1:1)，气永远瓶颈 → 矿花不掉飘到几万。改：矿存>400
  且无在建 CC → 再开一矿滚到 6 矿；气随基地 scale（3 矿→6 口气 / 4 矿→8 口气）。多矿=多气=多死神=
  矿花得掉的良性循环（真机到 5 矿）。

**新增 (Added) —— 正面部队交战微操 + 3 矿后攻防升级（2026-07-13 用户追加）**：
- **数量占优就压制不撤太快（`harass_act.py`）**：死神多了跟对方正面部队打时——我方死神数 ≥ 视野内
  敌方战斗单位数 × 1.3 判「数量占优」→ **压制不后撤**（武器冷却也不 scoot，顶住别撤太快降战斗力；
  战损靠死神数量多的特殊战斗力 + 超强回复扛），只让**个体低血的退一步回血再回**（`_retreat_pos` 只后
  撤 5 格，reaper 脱战回复快 → 满血就回来，不撤回家）；劣势才走标准 kite。修「既要保命又不能撤太快」。
- **3 矿后地面攻防升级（`mass_reaper.py`）**：开到 3 矿以上后补 Engineering Bay×2 + Armory，研步兵
  攻防 +1/+1→+2/+2（死神是轻甲步兵、直接吃这套 → 战斗力质变、战损再降）。**三矿前一分钱不分给科技、
  纯爆死神**（gate 在 townhalls.ready>=3）。注：这是**强化死神本兵种、非转兵种战略**；真正转枪兵/生化
  仍走**玩家确认的 lategame_transition**（persistent_bio_max，遵 ⛔ 铁律「没确认不自动切兵种」）。

**新增 (Added) —— 骚扰选目标（U5 第一步，`harass_act.py`）**：
- **软目标选择**：harass 目标从"死守的主基"改成 `zone_manager` 里**有农民、地面防守最弱**的敌方基地矿线
  （读各敌方 zone 的 `enemy_workers` + `known_enemy_power`，选最软的；5s 缓存锁目标防漂移）。对应用户
  "找以多打少 + 别光打正面"——绕开重兵主基、打防守薄的分矿。
- **有敌军也优先秒射程内农民**：修「vs 有防守基地 harass=0」的真根因——死神在防守矿线只 `attack` 军队/
  炮塔、无视身边农民。改成有敌军时也先秒射程内农民（harass 价值 > 跟防守换血），够不到才 skirmish 军队。

**变更 (Changed) —— 死神微操重写为「穿插-秒农民-逃跑回血」（用户 2026-07-13 洞察）**：
- 收回 army_near「attack-move brawl 军队」（硬打正面浪费死神）。改用死神**速度+回复优势**:受伤/敌军点脸
  先用速度逃回血 → 没事就冲进矿区秒农民（哪怕防守在 3-7 格旁，靠速度够到农民 + regen 扛一下）→ 秒完
  （冷却）遇敌逃远回血 → 回满再来。用移动而非硬扛，靠机动创造局部以多打少。

**⚠️ 回退说明 (Reverted) —— 死神 harass 微操整体回退到基线（2026-07-13 会话末）**：
上面这一串死神微操迭代（stutter-step/双模式/智能撤退/攒够再打/正面顶住/软目标/秒农民/mobility 穿插/dip
时序/cliff-jump 跳崖…）在会话中**反复 revert、最终把微操折腾进了无法干净还原的回归态**（vs VeryEasy 从
harass 25-26 掉到 0，死神狂堆 137 只却 harassed=0）。**已把 `harass_act.py` + `tests/unit/test_harass_act.py`
整体 `git checkout HEAD` 回退到会话前基线**（原版 hit-and-run），`mass_reaper.py` 的 `HarassWorkerLineAct`
调用同步去掉 `min_commit_count/regroup_count`。这是**过程教训**：违反了"3+ 次修复各自冒新问题 = 停下质疑
架构 / 别自己骗自己"的纪律，在没有干净 checkpoint 的情况下反复改。上面的 Added/Changed 条目保留为**尝试
记录**，但代码已不在。**保留的有价值改动**：economy 持续扩张 + 气/兵营 scale、3 矿后攻防升级（这些在
`mass_reaper.py`，未回退）。

**现状 (Status)**：mass_reaper 的**经济/科技骨架保留**（双气满采 / 持续扩张 / 攻防升级）；**harass 微操 = 基线
hit-and-run**。vs VeryHard 属**转型域天花板**（all-in 打不破强防守/封墙，同 nydus），真解是玩家确认的 bio
transition。真跳崖绕墙需专门的悬崖寻路（非简单换目标点），留作未来可选深水区（KG D19）。

### 2026-07-13 知识图谱可视化：domain 过滤隐藏+重排、形状区分度、文字换行

**变更 (Changed)** —— `scripts/_templates/kg-template.html`（交互式 KG 可视化，`/kg` 路由 + `build_kg_viz.py`）：
- **domain 过滤 = 隐藏 + 重排**（用户要求）：未选中 domain 的节点/边**完全不渲染**（不再灰度留原位），
  用可见子集**重算分层布局**（自研 Sugiyama 式，非 D3 force）+ fit 视图。kind/status 过滤也统一成隐藏+
  重排；搜索框仍仅淡化（打字时重排会乱跳）。
- **形状区分度**（用户「形状糊在一起看不出」）：kind 形状从 hex/圆/圆角矩形（都圆乎乎）换成**按角数拉开**——
  圆(fact)/三角(inference)/菱形(decision)/方框(open)；**kind 过滤栏画真形状**（不再灰方块）。
- **节点 caption 换行**（用户「相邻节点文字重叠」）：单行 18 字 → 每行 9 字、最多 2 行（tspan 换行、溢出省
  略号），每行宽压在 COL_GAP=132px 间距内，相邻节点文字不再横向撞。

### 2026-07-12 坑道虫经济根因修复（"坑道虫放了只过 2 个蟑螂" → larva 全被农民吃了）

> 真机反馈：坑道虫落地成功，但整波只过去 2 个蟑螂、气飘 700-800。telemetry 实锤根因**不是投送
> 机制**——是**蟑螂根本没产出来**：整局 DRONE 涨到 36、ROACH 卡 0-2、gas 飘 700。larva 全被农民
> 吃光，蟑螂饿死。这是「运营基础是第一评价标准」（memory `feedback_operation_baseline_first`）的
> 又一次踩坑：投送逻辑再好，没兵也是空。

**修正 (Fixed)**：
- **top-level `ActUnit(DRONE,20)` 排在含蟑螂的 SequentialList 之前 → 每帧先抢 larva，蟑螂
  priority 只锁矿不锁 larva → 蟑螂饿死**。删掉这个 top-level 农民目标，农民改由 Floor（非-priority
  软地板）吃军队剩的 larva 填到封顶。
- **sustain fallback@300s 强制解封农民封顶 → 农民饱和吃光 canal（~6min）前的攒兵 larva**（server log
  实锤 `opening_sustain_uncap_triggered now=300.0s via=fallback@300s`）。`_SetWorkerCap` 加
  `hard=True`：canal 落地前封顶不被 `sustain_uncap_active` 解除（`WorkerSaturationFloorAct._worker_cap_override`
  尊重 `worker_cap_hard` 标志），保住 all-in 攒兵窗口 larva；canal 落地（all-in 已投送）后自动清 hard、
  转运营再饱和。
- **农民封顶 30→20**（用户"蟑螂先出 8 个以上再考虑别的"，20≈二矿早期采矿够）；二矿前 DRONE 目标
  20→16（够开矿就让位蟑螂）；早期速狗 8→6。
- **第 2 口气延到 canal 落地后**（用户"气有一个在采就行了、最后余 700-800"）：1 气够 all-in 全程，
  2 气早开 → 气飘 700+ 浪费。
- **首个女王目标 4→6**（2 留家注卵 + 4 钻坑道，凑够用户要的"canal 好了 10 蟑螂 + 4 女王进去"）。

**真局验证（4 局 VeryHard）**：ROACH 峰（投送窗口）0-2→**8-11**；DRONE（all-in 期）36→**封死 20**；
gas（all-in 期）飘 700→**136-167 不飘**；第一波进坑道 2→**10-28 单位**（含 ~10 蟑螂 + 4 女王 + 狗）。

### 2026-07-12 坑道虫落地率诊断修复（"有视野却不放"根因 → 落地率 ~50% → 活到窗口 ~100%）

> 用户目标：把坑道虫落地率搞到 90%+（纯操作层，后面怎么打交给玩家转型）。真机观察"兵/坑道网络/
> 视野都有，坑道虫就是不放"。加 per-tile 诊断日志 + 真局 `nydus_landing_diag.py` 排查，定位到**四个
> 真根因**（都不是原先怀疑的 can_place bug），逐个修 + 真局验证。

**修正 (Fixed)**：
- **OL 供视野的落点距离超实测安全值**：`_OL_PUSH` 10→7。真机诊断 push=10 时 OL 稳定漂在离敌方中心
  27 格（离高地边缘 ~10-12），落点边缘格卡在 sight=11 外沿 → `is_visible` 绝大多数帧 False → 坑道
  永不落。回退到知识图谱 J3 实测验证过的 8.2-8.4 安全距离内（7）。
- **落点与 OL 视野几何解耦**：新增 `NydusLandingPlanner._ol_vision_edge_tiles`——落点**首选**对每只
  驻守 OL 取离它最近的可放边缘格（必在其视野内 + 可放），不再只挑"离矿最近"（那些格常落在没 OL
  驻守的扇区 → 无视野 → 永不落）。把落点绑到"当前真有视野的地方"，根治"有 OL 却无可落点"。
- **候选格坐标非格心**：worm 实际落点是 X.5/Y.5 格心，`can_place_single` 对整数格角返 False、对格心
  返 True → 候选边缘格 snap 到 `floor+0.5` 格心（原先 `vis=True place=False` 被误滤的 OLvis 主格转
  可放）。
- **④「主力不在」门在 vs 内置 AI 主力赖家时永不满足 → 坑道永不落**：加 ④ 时间兜底
  （`_ARMY_GATE_FALLBACK_S=25s`）——drop 就绪后 ④ 阻塞超 25s → 绕过 ④，按 per-tile 局部威胁挑最空
  格硬落（用户 2026-07-12「只要落地率、后面交给玩家」）。非战术/doctrine 自动切换，不违反「没确认
  不切」铁律。

**新增 (Added)**：
- `scripts/nydus_landing_diag.py`：坑道虫落地诊断 runner（真 Zerg VeryHard 局，non-realtime 可并行）。
  grep planner 每 ~4s 打的 `NYDUSDIAG` per-tile 判定分解（vis/place/威胁/离最近 OL 距离）+ ④ 门 +
  `BUILD_NYDUSWORM`（含 `get_available_abilities` 核对）+ telemetry `building_started NYDUSCANAL`
  终态落地统计。
- `NydusLandingPlanner` per-tile 诊断日志 + `_BuildNydusCanalAtEnemy` 发 worm 前核对
  `BUILD_NYDUSWORM ∈ get_available_abilities`（salvage 教训：验引擎真受理）。

**知识图谱 (Knowledge Graph)**：新增 `F13`（真机视野几何事实）/`D6`（落点耦合 OL 视野 + 格心
snap）/`D7`（④ 时间兜底）；反向复审 `D1`（push 10→7）；`U2` 由 open→verified（`building_started`
终态确认 can_place 可见格心 == worm 成功）。`docs/pitfalls.md` 记 5 连坑（尤其测量假阴性：诊断读
`snapshot.units.NYDUSCANAL` 恒 0，实际 canal 在 `buildings` 子字典）。

### 2026-07-12 通用知识图谱系统（机读数据源 + 动态 DAG 可视化 + 一致性门）

> 把原"坑道虫专用、分 L0/L1/L2/U 层"的依赖图升级成**通用、纯 DAG、按 domain 分子树**的项目知识
> 图谱系统，格式对齐新建的 `.claude/skills/knowledge-graph/SKILL.md`。坑道虫落点只是其中一个
> domain（`nydus-landing`）；以后别的领域加自己的 domain 自然并入。三件套围绕唯一机读源同步。

**新增 (Added)**：
- `docs/knowledge-graph.yaml`：唯一机读真理源。每节点 `{id, domain, kind, statement, deps,
  evidence, status}`（对齐 skill 格式）。`kind` = fact/inference/decision/open **软标签**（仅描述
  性质、非层级）。当前收录 domain `nydus-landing` 28 节点（F1-F12 / J1-J9 / D1-D5 / U1-U2）。
- 交互式可视化 Artifact（自包含 HTML，明暗主题自适应）：**层次化 DAG（Sugiyama 风格）**——按渲染时
  从依赖边算出的 `level`（level=0 若无 deps，否则 1+max(依赖 level)，天然处理"低 level + 高 level 推出
  更高 level"）把节点分成**离散的层**（L0 基础事实在底、越往上 level 越高），层内 barycenter 排序降交叉、
  留足间距不重叠；**入场一次性展开动画后位置固定、不再抖动**（去掉力导向弹簧——用户反馈"太弹、挤成一团"）。
  节点形状=`kind`、边框色+脉冲=`status`、`domain` 配色（metaball 融合背景）；左侧每层 L{n} 标签+计数；
  **动态效果保留**——依赖边按方向流动（marching-ants，下游→上游）、pending/unverified 节点呼吸脉冲、
  点选后依赖链**逐级点亮**动画（上游青/下游粉）；**点任意节点可靠展开详情面板**（domain + kind +
  status 徽标 + 结论全文 + 证据/来源 + 依赖谁/被谁依赖，均可点跳转；不用 setPointerCapture 以免 click
  被吞，jsdom 实测 28/28 节点点击均出面板、点空白关闭）；按 domain/kind/status 过滤 + 关键词搜索；
  **节点固定在各自层级、不可拖动**（去掉拖拽交互，避免拖拽吞点击）；拖空白平移 + 滚轮缩放 + 一键归位
  重排；`prefers-reduced-motion` 下静态直排。
- `scripts/build_kg_viz.py` + `scripts/_templates/kg-template.html`：构建步骤读 yaml 注入 HTML
  模板占位符生成 Artifact 源（数据源单一化，yaml 改了重跑即同步）。
- `tests/unit/test_knowledge_graph.py`：一致性门——①非根（非 fact）节点必有非空 deps；②deps 全部
  指向存在节点；③无环（DAG）；④kind/status/domain 取值合法 + 无残留 layer 字段；⑤引用
  pending/unverified 节点者自己不能标 verified（不确定性沿依赖传播）；⑥yaml↔md 节点 id 集合一致
  （防漂移）。10 个用例全绿。

**变更 (Changed)**：
- **去层级**：删 `layer` 字段（原 L0/L1/L2/U），改纯 DAG——节点只靠 deps 边组织，一个节点可依赖
  任意其他节点（含"底层事实"+"上层结论"混合推导），不再强分 level。
- **通用化改名**：`docs/nydus-knowledge-graph.{yaml,md}` → `docs/knowledge-graph.{yaml,md}`；
  `tests/unit/test_nydus_knowledge_graph.py` → `test_knowledge_graph.py`；build 脚本/模板/输出同步
  改名。旧 `nydus-` 前缀文件删除。

**知识内容更新（图谱本身）**：
- **U1 定解（2026-07-12 用户拍板）**：新增 F12（速狗机动骚扰空虚分矿）/ J8（骚扰=直接经济伤害）/
  J9（骚扰逼分兵回防、开 ④ 窗口，⚠️unverified 待真机）/ D5（U1 的解：主力赖家时**不硬下 canal**、
  改速狗骚扰分矿吃经济 + 引主力出来开窗）。U1 由 D5 回答（U1→D5 依赖边，问题保留、实解待真机验）。
- **反向复审 D4**：D5 定案后确认 canal 保持"主力不在才落"**不改硬下**，D4 依旧但不再是死结
  （D5 负责在赖家时创造窗口/占便宜），复审结论记进 D4 note。不确定性传播：D5 依赖 J9(unverified)
  → D5 标 unverified（未标 verified），一致性门通过。

### 2026-07-12 nydus OL 供视野 + 落点选择改对（知识图谱 D1/D2/D3 落地）

> 依据 `docs/nydus-knowledge-graph.md` 的 D1/D2/D3 决策，把坑道虫的 OL 站位与落点选择从
> "送进基地/贴前沿 + 固定矿后锚点环"改成"漂浮悬崖外低地 + 动态扫高地边缘可放格"。
> 只动 `nydus_landing_planner.py` + `nydus.py::_SendOverlordToEnemy`（不碰投送状态机、不改 ④ 门阈值）。

**变更 (Changed)**：
- **OL 站位（D1/D3）**：`_SendOverlordToEnemy` 不再把侦查 OL 送到 `enemy_pos.towards(我方基地)`（前沿）
  或矿后死角（基地里/高地上）——一被守军看见主力就回防、窗口关死。改成 OL 漂浮点 =
  高地边缘可放格顺 `off_cliff_dir`（地形下坡方向）外推 ~10 格（=sight−1）停在**悬崖外低地**，
  空军从低地仍能看见高地落点（F9）。对全部静态边缘可放格按角度分 5 扇区、每扇区取最外围格算一个
  漂浮点，派 2-3 只 OL 分散驻守做冗余（一只被打掉别的还供视野）。受威胁时顺同方向继续往低地外撤。
  侦查 OL 数 2→3。
- **落点选择（D2）**：`NydusLandingPlanner` 落点从"固定 behind_mineral/mineral_line 锚点环"改成
  **动态扫高地边缘可放格**——在「有视野 ∧ can_place ∧ 主力不在落点区（沿用现有 ④ 门，阈值不变）」的
  边缘格里挑**离敌方矿脉质心最近**的那个（近矿优先屠农民）。地形栅格算不出时（未接入）回退旧锚点环兜底。

**新增 (Added)**：
- `nydus_landing_planner.py` 加模块级地形几何函数 `enemy_plateau_edges` / `off_cliff_dir` /
  `overlord_float_points`（与 `scripts/nydus_terrain_probe.py` 同源、真机验证过的算法），
  planner 与 `_SendOverlordToEnemy` 共用。静态边缘候选 + 漂浮点一次算好缓存复用（#543，别每帧重扫 ~1369 格地形）。
- `scripts/nydus_ol_float_selftest.py`：OL 漂浮点**生产函数**真机终态自验——读 OL 真实站位
  `get_terrain_height` 断言在悬崖外低地（非中间 trace）。真机结果：base_h=223、5 个漂浮点全在悬崖外低地、
  起手 OL 飞到最近漂浮点 (73,41) 高度 207 < 211 阈值、离敌方主基 28 格 → PASS。

> 影响：坑道虫窗口检测的前提（OL 持续供视野而不招回防）更靠谱；落点跟着真实高地边缘走、
> 优先钻矿后屠农民。**canal 是否真落地仍受 U1（vs 内置 AI 主力赖家、④ 门可能永不满足）制约**，
> 那是待用户拍板的独立问题，不在本次范围。

### 2026-07-12 nydus 开局运营基础修复（真机反馈：先运营没毛病再看胜率）

> 真机实测暴露开局运营一堆执行 bug（用户强要求：评价一把好坏第一标准 = 决定停农民/一波之前
> 农民不停/人口不卡/女王注卵不停，见 CLAUDE.md ⓿ + memory feedback_operation_baseline_first）。
> telemetry 逐帧定位 + 修复：

**修正 (Fixed)**：
- **人口卡 14/14 达 30s（一切延迟的总根因）**：sharpy AutoOverLord 算法 `bonus=min(larva*2,
  (minerals-300)/50)` 开局 minerals<300 时 bonus 为负 → 算出只需 1 个 OL、不提前补 → 供应死卡。
  加开局保底 `ZergUnit(OVERLORD,2,priority)`，供应 cap 早到 22。
- **阶段0串行阻塞**：BR + 二矿 + 气全从阶段0串行队列移到阶段1并行（阶段0只留 DRONE14→BS）。
  原先 SequentialList 卡等每步 done 才进阶段1 → 女王1/Lair/VN 全被推迟。
- **二矿前农民卡 14**：plan 只有 `ActUnit(DRONE,14)` 维持 14 就停、Floor grace 门 base<2 不补 →
  农民卡 14 达 100s+。补 `ActUnit(DRONE,20)`（二矿前不停、排狗/BR 前优先吃 larva），
  `_SetWorkerCap` 40→24（二矿后停农民、larva 转蟑螂狗）。
- **女王注卵覆盖低（larva 荒）**：raid 留家注卵女王从"一次锁定前 2 只"改成 **动态 = max(2, 基地数)**
  （旧逻辑第 1 只女王出生时锁死成 1 只，之后新女王全被招募进坑道 → 留家永远 1 只注卵）。
  plan canal 前女王 2→4（2 留家注卵 + 2 钻坑道当兵，用户：女王也当兵、充分利用产能）。

**影响**：早期蟑螂 3-4→6-10（达 KPI 8-10）、农民不再卡 14（涨到 24 后按 cap 停）、供应卡 30s→34-66s、
VN 393s→250s、注卵 0.3→0.6。**仍待修**：女王 1 仍 ~127s 偏晚（母池 82s 后等矿）、供应仍有 34-66s 卡、
注卵未满 1.0。坑道虫落点贴边 + 佯攻同步 + 二波遛狗（用户 4 点战术）待做。

### 2026-07-11 nydus 转运营刺蛇混编（Point2 T4，VeryHard 29%→50%）

> T3 后败局气堆到 3000-5900 浮着——纯蟑螂吃不掉气、也缺射程/DPS 被 VeryHard 磨死。用户方向：走
> 蟑螂刺蛇（不要飞龙，后期可选毒蛇）。本次转运营出蟑螂+刺蛇混编，把堆积的气变战力。

**新增 (Added)**：
- **刺蛇混编**：nydus.yaml `core_units` 加 `{unit: HYDRALISK, policy: mass}`（排 ROACH 后 → 蟑螂
  优先吃 larva 当主体扛线、刺蛇次之补射程/DPS，~1:1，Fable5：绝不纯刺蛇）。build-aware sustain 据此
  转运营后自动出混编 + 自动建刺蛇巢；gas_per_base 随之升 2 正好消化 T3 堆积的气。
- **刺蛇巢 + 射程升级**：plan 里 `GridBuilding(HYDRALISKDEN, 1)` 门控 `time>420`(~7:00，提前建好让
  sustain kick 时刺蛇能及时成军) + `Tech(EVOLVEGROOVEDSPINES)`（射程 +1，Fable5：优先于移速，让刺蛇
  vs AI 的 a-move 部队白嫖齐射）。

**影响**：VeryHard 26 局 29%→50%（+21 点，~2 SE 显著）。机制确认：每局都建刺蛇巢，12 长局中 11 局
出刺蛇；**出刺蛇(hyd>0)几乎必赢**（13 胜全有刺蛇 3-24，13 负里 9 局 hyd=0）；胜局含 25 分钟级长局
（刺蛇混编磨赢）；`avg_excess_bank` 从数千降到 224（堆积气被消化）。累计轨迹 17%→29%→50%。遗留败
局两类：~5 短局"坑道虫没落地"(canal 可靠性) + ~4 长局刺蛇没及时成军（待查）。

### 2026-07-11 nydus 转运营宏观骨架：攻防升级 + 气矿曲线（Point2 T3，VeryHard 17%→29%）

> VeryHard 24 局基线诊断：坑道虫落地也骚扰到农民了，但游戏拖到 20-40 分钟，一波流 0/0 部队被 AI
> 用运营磨死——长局败占绝大多数（胜局全是 <15min 快杀）。用户方向"一波打不赢就转下一波，再不行
> 慢慢运营这样更稳"。本次加"慢慢运营"的宏观骨架。多波毒爆(T2)实测无效已回退（往坑道灌更多兵=零散
> 喂进绞肉机，没升级照样死；真杠杆是给长局部队升级）。

**新增 (Added)**：
- **双进化腔 + 攻防升级**：`GridBuilding(EVOLUTIONCHAMBER, 2)` 门控 `time>360`(~6:00，Fable5：别等
  7:30，VeryHard 那时已 +2)。升级序远程攻+1 → 地甲+1 → 远程攻+2 → 地甲+2（`ZERGMISSILEWEAPONSLEVEL1/2`
  + `ZERGGROUNDARMORSLEVEL1/2`，蟑螂/刺蛇都是远程 → 升 MISSILE 不升 MELEE；地甲 ARMORS 近战远程共享）。
- **气矿曲线**：`BuildGas(4)` @ `time>420`、`BuildGas(5)` @ `time>540`（Fable5：升级/后期兵种吃气，
  一波低气 build 撑不起三线；没气升级就是空门控）。

**实现说明**：全部时间门控用 `Step` 第 4 参 `skip_until`（wave1 快杀赢了这些门不触发，不抢首波）。
升级 enum 必带 LEVEL 后缀（裸名 `ZERGMISSILEWEAPONS` 不存在会构造崩，Opus 评审拦下）。

**影响**：VeryHard 24 局 17%→29%（+12 点）；15/15 长局都真建起双进化腔（机制确认非空门控），胜局
首次出现 20 分钟级长局胜（升级把"被磨死"翻成赢）。遗留：纯蟑螂吃不掉气 → 败局气堆到 3000-5900 浮
着 → 下一步 T4 加刺蛇消化堆积气 + 补射程/DPS。设计经 Fable5(节奏) + 独立 Opus(架构 enum) 双评审。

### 2026-07-11 nydus 侦查 Overlord 不再倾巢送死（用户点1：升速 + kite）

> 用户指出：第一只侦查 Overlord 送到敌方基地死后，代码每 6s 又补一只飞去贴脸傻等死、死了再补，
> 家里 OL 一只只喂进去送死浪费大量矿/供应。正解：升 Overlord 速度、用 hit-and-run，敌人来了能跑，
> 只留 1 只轮换侦查，不倾巢。

**变更 (Changed)**：
- **`_SendOverlordToEnemy` 改 hit-and-run 单侦查**：不再每 6s 补 OL 送死。改为只指定 **1 只**侦查
  OL（按 tag 轮换，死了才换、始终留 ≥1 只在家保供应）；敌方战斗单位逼近 `_SCOUT_FLEE_RADIUS`(9)
  → 撤到 `_SCOUT_FLEE_BACK`(24) kite 保命，威胁走再回 `_SCOUT_STANDOFF`(13) 驻守供视野。canal 落点
  仍走已有的 OL 兜底（落在侦查 OL 位置），驻守在安全 standoff 也能落 canal。
- **补 Overlord 速度升级（Pneumatized Carapace）**：`Tech(UpgradeId.OVERLORDSPEED)` 加在狗速之后。
  100/100 便宜，让侦查 OL 能 kite；省下的"OL 一只只送死"的矿/供应远超它。

**影响**：18 局 vs Hard 胜率 72%（对照旧版 24 局 79%，在小样本方差内、无回归；核心收益是**省钱 +
侦查存活更久**而非提胜率）。诊断发现赢局全靠骚扰农民（harass≥28 全胜、harass≈0 全负），剩余负局
= "raid 没落地" —— 正是下一步多波次要解决的失败模式。

### 2026-07-11 nydus 狗蟑快攻节奏重排（Fable5 实战 trace 复盘）

> 用户玩了一把 nydus 后指出：气踩太多太早鱼一大堆、农民没补满、坑道网络好了坑道虫却不落地反而去开矿补
> 农民、坑道虫好时只有 4 只蟑螂完全没爆兵——"节奏像一坨屎"。并定下流程：以后每个 build 思路 + 每局
> 打完 trace 都先让 Fable 5 独立复审再改。本次即按 Fable 5 复盘意见重排。

**修正 (Fixed)**：
- **蟑螂死卡 4 只（命门）**：`ZergUnit(ROACH,12)` / 早期女王 `ActUnit(QUEEN,...)` 原为 `priority=False`，
  sharpy `ActUnit` 缺矿时**只有 priority=True 才 reserve** → 蟑螂(75 矿)/女王(150 矿)每帧被 50 矿的狗/
  农民截胡、永远攒不起来。改 **蟑螂 + 女王 `priority=True`**，缺矿时锁住矿先造。实测蟑螂 4→19-20、
  7 蟑螂 ~5:00 成型。
- **气爆仓（鱼到 1108）**：serial 里 `BuildGas(2)` 连踩 → 13 农民时 6 人瞬间进气崩矿线；macro tail
  `BuildGas(3)` 无门 → 蟑螂 0 只时就踩第 3 气。改 **第 1 口 serial、第 2 口挂蟑螂窝后、第 3 口挂三矿后**。
  早期窗口余钱均值 1037→230-270。
- **坑道网络好了坑道虫不落地 + 出虫后堆农民**：真凶是 macro tail `Expand(3)` 只门控 `HATCHERY≥2` →
  二矿一好(2:47)就抢建三矿一口气抽 300 矿把 Lair 饿死、坑道链整体晚 2 分钟。改 **`Expand(3)` 挂
  `NYDUSCANAL` 落地后**（一波没投送绝不扩张）。三矿从 2:47 推到 canal 后，Lair/Network/Canal 各提前。
- **worker floor 6 分钟墙钟误解封**：`worker_saturation_floor.py` 原 `time>360 → 解封顶` → all-in 常
  6min 还没打出去、墙钟一到就误判转运营疯铺农民(20→33)。去掉墙钟，解封只认 `sustain`(一波结束)或 4 矿。

- **坑道链提速（Fable5 三修，纠正诊断：真凶是气不是矿）**：Lair 原卡在 ~3:30 morph、canal 8-10min
  太晚。Fable5 逐帧核对 3 局锤出真凶——**狗速 `Tech` 排在 MorphLair 前、缺气时每帧 reserve 100 气把
  第一个 100 气锁死**（Lair 是气 bound 不是矿 bound）+ **主基被 2 女王订单占用致 MorphBuilding 跳过不
  morph**。纯 children 重排（零经济代价）：女王拆 1（Lair 前主矿注卵）+ 1（挂二矿 ready）、MorphLair
  上移抢第一个 100 气、狗速下移让气。实测 **Lair morph 3:30→2:40、canal 500-629s→393-473s、经济偏差
  51%→25%**。另 `_WINDOW_TIMEOUT_S` 90→45（all-in 佯攻在位即窗口，别对乌龟对手等满 90s 死时间）。

**变更 (Changed)**：
- larva 分配按 Fable5 优先级重排：农民 `ActUnit(DRONE,20)` 从蟑螂前挪到**军队之后**（larva 争抢看
  children 顺序、priority 只锁矿不锁 larva，农民排军队前会抢光 larva 让蟑螂拿不到卵）。
- `NydusRaidAct`：首波兜底 `_WAVE1_SUPPLY_FALLBACK` 26→20（原 26 在招募 cap 下数学不可达）+ 新增
  网络就绪超时兜底（网络 ready 超 60s 有 ≥4 蟑螂就发，防产能被打断死等）；招募 cap 蟑螂 8→12、狗 16→24
  对齐 build 产量，让攒出的军队全加入一波。

### 2026-07-10 全 build 农民饱和治本：通用兜底 + 4 结构病解冻 + 防复发门

> 用户视角：开了矿农民却不涨（nydus 开二矿卡 14 农民、二矿没人采）= 基本 macro 错误。
> 定为基础规则"开多少矿最终就得多少农民"。审计三族全 build 揪出人族6+虫族8个同类病，一次治本。

**新增 (Added)**：
- **通用农民饱和兜底 `WorkerSaturationFloorAct`**（`worker_saturation_floor.py`）：种族无关顶层 act，
  挂三族 `_wrap()`，**所有 build（含 doctrine）恒生效**，农民始终拉向饱和目标
  `min(sum(己方 ready townhall+gas 的 ideal_harvesters), drone_budget)`（随基地/气井自调，开矿即涨）。
  子类化 sharpy `ActUnit` 复用其 worker 计数/larva/cooldown，`priority=False` 软地板（军队同帧仍能出）。
  **虫族封顶 66 / 神人 80**（虫族农民军队抢同一 200 人口池，满饱和会占满人口没空间出兵；常量与
  `opening_sustain_act` sustain cap 同源）。grace 门（base≥2 / time>100s / sustain flag）尊重早期"短暂停农民"。
  - 治本关键：`OpeningSustainAct` 靠 `sustain_uncap_active` flag，**切 doctrine 后 flag 永不 fire → doctrine
    整局零农民兜底**（`director.py:7277` persistent_set）。新 Floor 不依赖 flag、恒生效，补的是这个真缺口。
- **防复发门 `test_build_structure_audit.py`**：运行时走查每个 build 的 plan 树，抓"大数量军队塞进阻塞
  `SequentialList` 后面还有东西"的结构冻结反模式。含**有效性自证**（喂合成阻塞 plan 确认门真 FAIL）。当前全 build 绿、无需豁免。

**修正 (Fixed)**：
- **4 个结构冻结 build**（`roach_hydra_viper`/`ultralisk`/`bc_late`/`mech`）：军队/科技/农民全塞进单条阻塞
  `SequentialList`、前项不达标后面全冻结 → 抽成顶层并行 `BuildOrder`（Morph 类硬前置仍 `Step(UnitReady)` 门控）。
  真局验：roach_hydra_viper 首蟑螂从"等 Hive"→ ~4:50 早出；ultralisk 前中期有地面兵（首狗 ~1:49）；mech/bc_late 科技链不冻。
- **农民随基地爬升**（真局 telemetry）：doctrine 路径下 Floor 独力把农民从 12 拉到饱和封顶（切 doctrine 后 sustain 全程不 fire，
  400+ 秒零支援仍稳）；健康 all-in（proxy_4rax）不被乱铺农民；虫族 66 封顶未把 roach_hydra "200 人口无兵" 老病引回。

### 2026-07-09 坑道虫突袭（nydus）精修：补真投送 + 声东击西佯攻

> 用户视角：虫族"坑道虫突袭"（原尼德斯偷袭）以前坑道虫建出来没人用、army 走正面。现在
> army 真的灌过坑道网络、从敌方家的坑道虫钻出偷袭；并配小股速狗正面佯攻引敌军离矿区。
> **定位**：这是对**真人对手**成立的偷鸡（真人看到二矿被骚扰会回防→矿线空→坑道落地屠农民）；
> 内置 AI 不吃这套小规模佯攻（军队杵家不动），vs AI 坑道虫窗口难开、偏晚，别期待它正面赢强 AI。

**新增 (Added)**：
- **坑道虫真投送**（`NydusRaidAct`，`zerg/plans/nydus_raid_act.py`）：STAGE（网络旁 Reserve 集结预装）
  → TRANSIT（`SMART` 装进坑道网络、读 `passengers` 确认）→ STRIKE（运行时探 `UNLOADALL_NYDUSWORM`
  在敌方家卸出、优先扑农民）。装载走 `load_bunker` 同款 bypass；坑道 ability 全 venv 真机核对
  （含绕开 python-sc2 `NYDAS` 拼写 typo）。三兜底（视野保活/卡坑道超时/虫洞被秒重下）。
- **佯攻小队**（`FeintSquadAct`，`zerg/plans/feint_squad_act.py`）：6 速狗 POKE↔RETREAT 骚扰敌二矿，
  引敌军离矿区；与主力 raid 分池不重叠（`_vibecraft_nydus_feint_tags`）；残血 `move` 撤退回血再冲。
- **安全窗口检测**：数敌矿线附近敌方战斗单位，≤2（主力被引走）才下坑道、落点回矿线正中屠农民；
  窗口 90s 超时降级到矿后隐蔽点硬下。
- 改名"坑道虫突袭" + 别名"坑道虫"（保留尼德斯/虫洞兼容）。真局自验脚手架 `scripts/nydus_selftest.py`
  （六维记分卡 + per-opponent）。

**修正 (Fixed)**：
- 真局揪出 `_prune_dead` 抢在乘客判定前 → 刚装载单位被误判死亡、永远到不了 TRANSIT（单测测不出、
  只真局暴露，transit 0→13）。UNLOADALL 全量卸载但只清超时批 → 未超时单位被误判"已钻出"假阳性 STRIKE 自家。

### 2026-07-09 proxy_4rax 枪兵前向集结：头几个枪兵不回家，攒够一波再出发

> 用户视角：单bb+野3bb 偷家 build，以前头几个枪兵练成就被拉回家、来回拉扯、分批送死。
> 现在枪兵在野兵营（3 个 proxy 兵营）附近集结，家里出的也去那，攒到 ~6 个一起出发。

**新增 (Added)**：
- **MarineStagingAct**（`terran/plans/marine_staging_act.py`）：proxy_4rax 顶层兄弟 act。
  根因是 sharpy `PlanZoneGather()` 出门前把 idle 枪兵往家拉。照 proxy SCV 站桩机制
  （每帧重申 `Reserved` 防拉走 + `move`（非 attack_move）到锚点 + `hold_position` 幂等），
  把枪兵钉在野兵营锚点（3 proxy 兵营落点质心，`ProxyBarracksAct` 发布 `knowledge.vibecraft.proxy_anchor`）
  集结，攒到 threshold（默认 6，可调）或兜底 170s 或玩家下全军指令 → 一次性释放，
  `PlanZoneAttack` 接管全队一起冲。
  - 尊重玩家控制权：玩家单位级 claim 的枪兵不 stage/release；玩家一下全军 intent 立即释放。
- 真局自验（`scripts/marine_staging_selftest.py`）：枪兵在锚点从 1 稳定攒到 6、
  `released reason=threshold` 一起出发（非被 Gather 打散）；build_acceptance 6/6 仍过。

### 2026-07-08 人族新命令：主基地起飞/飞到某矿降落 + 农民基地调度

> 用户视角：4 句人族命令以前没反应，现在能用了——"主基地飞起来"、"主基地飞到二矿/三矿"
> （起飞→飞过去→降落在最优采矿位）、"主矿农民优先采水晶"、"主矿农民去二矿采矿"。
> 起飞后还能单独说"飞到三矿"、"降落在这里"（对已在飞的基地生效）。

**新增 (Added)**：
- **STRUCTURE_MOVE directive**（人族建筑起飞/移动/降落）：
  - "主基地飞起来" → 主基 townhall 起飞悬停；"飞到二矿/三矿/这片矿" → 飞过去降落。
  - **主基类型泛化**：覆盖 CommandCenter / OrbitalCommand（开局早升的常见形态）+ 飞行变体；
    PlanetaryFortress 友好拒绝（星球要塞不能起飞）。复用 #543 挂件挪位的 LIFT/FLY/LAND 状态机。
  - **降落对齐最优采矿位**：落"矿区/扩张点"时 snap 到该扩张的标准 townhall 采矿格位
    （`closest_expansion_location`），不偏；被占才退化就近扫。起飞后可对已在飞的基地单独下飞/降。
- **WORKER_TASK directive**（农民基地调度）：
  - "主矿农民优先采水晶/气" → 复用全局 `set_mining_priority`（持续）。
  - "主矿农民去二矿采矿" → 该基地全部采矿农民（Gathering + 非 Reserved + 非采气 + 非在建）持续
    钉去目标矿 8 游戏秒（对抗 sharpy DistributeWorkers 每帧再平衡），到期释放交还 bot。
- 真 LLM 解析 4 句 + 补充话术全 PASS；真局自验 4/4 终态确认（主基真变 FLYING、降落命中贴矿位、
  农民逐个到二矿、采矿优先 hook 生效）。

**修正 (Fixed)**：
- **既有测试污染**：`test_standby_orders.py` 有个类级 monkeypatch（`NamedSpotRegistry.resolve` 永久
  覆写不还原）→ 全量跑时污染后续文件的 named_spot 解析。加 autouse 还原 fixture 修掉。

### 2026-07-08 命令响应改进：LLM 超时放宽 + 命令气泡非阻塞队列

> 用户视角：连发几条指令时，每条都有独立气泡（识别中→绿✓/红✕后淡出），不再"发第二条时第一条状态
> 被顶掉"；复杂指令（如"修水晶+下两个VS"、多段升级）不再因解析慢而误报失败。

**变更 (Changed)**：
- **LLM 解析超时 3s → 8s**（`config/llm.yaml`）：复杂命令（4 卡链）解析需 >3s，旧值导致它们频繁
  超时误报"解析失败"。后端解析本就是 `asyncio.create_task` 并发、非阻塞，放宽超时不会拖慢简单命令
  （超时是上限非固定等待），只让复杂命令有时间解析完。

**修正 (Fixed)**：
- **命令气泡非阻塞队列**：前端原用单一 status 显示最后一条命令，且有个 `sending` 门槛**实际会阻塞
  第二条命令**直到第一条 resolve。改成气泡队列：每条命令一个气泡（`command_received`→琥珀识别中，
  `command_echo`→绿成功/红失败，停留后淡出），多条并发各自独立、互不覆盖，去掉阻塞门槛（保留 5s 限频）。

### 2026-07-07 攻防升级目标等级手动设定（科技面板每条攻防线设 0/N/自动）

> 用户视角：科技面板放大后，每条攻防升级线（神/虫/人各 5 条）都能设**目标等级**——设 `0` bot 就不主动
> 升这条线，设 `2` 就自动升到 2 封顶不超，设 `自动` 交给 bot（默认）。想省资源不升某条、或卡在某级不
> 浪费，都能手动控。

**新增 (Added)**：
- **攻防升级目标等级手动设定**：15 条攻防升级线（神族 地面攻/防/护盾/空攻/空防；虫族 近战攻/远程攻/
  地甲/空攻/空甲；人族 步兵攻/防/机械攻/舰船攻/机械舰船甲）每条可设 `0`(不升)/`1-3`(封顶)/`自动`(默认)。
  - 封顶门在 sharpy `Tech.execute` 一处收口（`# vibecraft:` patch）：研究前解析升级线+等级，超目标就跳过、
    不卡后续研究、也不预留矿气。真局验证：设 0 该线终态恒 0 级、同族其他线照常升（精准封单条）。
  - 面板：科技面板放大 modal 每条攻防线一行 `名称 + Lv当前 + 目标:[0][1][2][3][自动]`（`UpgradeTargetPanel`）。

**修正 (Fixed)**：
- **既有 bug**：`_KNOWN_UPGRADE_NAMES` 里虫族空攻用了不存在的 enum `ZERGFLYERATTACK`（真名
  `ZERGFLYERWEAPONS`）→ 导致 zerg 空攻的**面板当前等级一直显示 0 级**。改对 enum 一处同时修好显示 + 新封顶。

### 2026-07-07 修正：重建前端 bundle — 运营策略面板三维度改版终于真正上线

> 用户视角：运营策略面板一直显示旧的"开 1/2/3/4/5 框"，看不到「多开一个矿」+ 采矿策略。
> 根因是 2026-07-06 的面板改版只改了源码、**没重新构建前端 bundle**，线上一直 serve 旧包。
> 重建后新面板才真正部署（手机 PWA 需清缓存/隐私窗口才能看到新版）。

**修正 (Fixed)**：
- 重建 web bundle（`npm run build`），使 2026-07-06 运营策略面板三维度改版（多开一个矿 /
  采矿策略 优先水晶·气·默认 / 农民 停·补·默认）真正部署到线上。之前只提交了源码未重建，
  服务的 `index.html` 仍引用旧 bundle。**教训**：改 `web/src/` 必须 `npm run build` 并提交产物。

### 2026-07-07 三种偷家 build 落点规划器 + 真局全部打通（4bb / 4bg / 野2VS）

> 用户视角：三种"偷家"打法现在都稳了。**人族 4bb 野三兵营**——三个兵营挨在一起、藏在敌方
> 视野外，农民直奔直建不再原地试探；**神族 4bg 前置门**同理；**神族野双机场（野2VS）**——玩虚空流
> 时开局把镜头拉到偷家点、说"派农民修个水晶然后下两个 VS"，两个星门真能建出来了（以前只建水晶、
> 星门不出）。

**新增 (Added)**：
- **通用地形落点规划器 `placement_planner.py`**：从地图地形模型（placement_grid / pathing_grid）
  **离线规划**多个建筑的确切紧凑落点——引擎 `can_place` 真源 + 建后连通复核 + `query_pathing`
  确认农民走得到，农民只执行不现场试探。支持神族能量场约束（Pylon + 生产建筑）。

**修正 (Fixed)**：
- **人族 4bb 野三兵营真局打不出**：根因是侦查逻辑（ScoutWorker）抢"离敌最近"的农民去探路，而
  proxy 农民正朝敌方走 = 全场离敌最近 → 被反复拉扯到不了、还卡死顺序建造门。修：ScoutWorker
  排除 proxy 建造农民（`proxy_builder_tags`）+ 顺序门死锁保护。真局 5/5 通过（三兵营全建成）。
- **神族 4bg 前置门落点分散/藏不住**：接入落点规划器（Pylon 先规划、生产建筑等能量到位再规划）。
  放置确定性 3/3 + build_acceptance 13/13 通过。
- **神族野双机场（"修水晶+下2VS"玩家指令）只建水晶、两个星门永不建**：根因是水晶 `build_at` 卡
  的 `activate_when=unit_arrived`，代码只对 `chain_structure_ready` 提取 chain_id → 水晶卡
  chain_id 丢失 → 选了另一个随机农民建水晶 → 建好后反查链失败 → 星门后续卡的落点永不刷新。
  修：`BuildAtPayload` 加 `chain_id` 字段，水晶卡带上，settle 直接用它反查链（不靠农民 tag）。
  LLM prompt 同步（by_probe build_at 卡必须带 chain_id），真 LLM 确认输出；proxy_chain_selftest 5/5。

### 2026-07-06 运营策略面板重构：删"开几个框" + 加"多开一个矿"按钮 + 采矿策略

> 用户视角：运营策略面板里那个"开几个框 1 2 3 4 5"选择器不实用、老出错，删掉了；换成一个
> 「**多开一个矿**」按钮——点一下派农民开下一个矿，矿建好卡片自动消失。另外加了「**采矿策略**」：
> 优先水晶 / 优先气 / 默认，一键控制农民采矿偏好。

**变更 (Changed)**：
- 运营策略面板（手机 PWA）「开矿」维度：**删掉 1/2/3/4/5/不限/默认 chips**，换成单个「多开一个矿」
  按钮。每点一次发一张扩张指令卡（开当前基地数 +1 个矿），新矿建成后卡标记完成消失。
  - 注：旧 chips 兼做"扩张封顶"（限制 bot 自动扩张到 N 个），删除后此 UI 能力移除（语音说"最多开 N 矿"仍可）。

**新增 (Added)**：
- 运营策略面板新增「**采矿策略**」维度：优先水晶 / 优先气 / 默认。
  - **优先水晶**：先把水晶采满（每片矿 2 农），多出来的农民才去采气（农民少时全采水晶、0 采气）。
  - **优先气**：先把气采满（每井 3 农），剩下的才采水晶（如 10 农 2 井 → 6 采气 4 采水晶）。
  - **默认**：bot 自己配。**只在农民不足时起作用**（过饱和时水晶本就满、多的自然去气，改了无感）。
  - 实现：director `apply_macro_action(dim=mining)` → `facade.set_mining_priority` → sharpy
    `DistributeWorkers.execute` 按优先级动态设 `min_gas`/`max_gas`（默认时恢复剧本原值，不砸掉 `min_gas=6`）。
  - 真局验：mining=mineral → telemetry `gas_workers` 降到 3（19 农 / 主基地 ideal 16 → max_gas=3）；
    mining=gas → gas_workers 满；default → 恢复。

### 2026-07-05 proxy_4rax：SCV 出发太早 + 出去后被拉回家反复拉扯（两修）

**修正 (Fixed)**：
- **出发太早**：3 个野兵营 SCV 开局（~0-10s）就出去了，拖慢早期采矿。改成**等家里第一个兵营开始
  造之后**才派（`already_pending(BARRACKS)` 时机门）。真局验：SCV 现在 `game_t=42s` 才出发。
- **出去后被采矿拉回家反复拉扯**：SCV 到 proxy 等钱时若离锚点 <4 就不发命令 → 空闲 → 被采矿 manager
  抢回家采矿 → 下一帧又拉出去，来回拉扯（用户反馈"不停被拉回基地又拉过去"）。改成 **① 每帧重申
  Reserved（采矿抢不走）② 到位后 `hold_position()` 站桩不空闲**。真局验：3 野兵营全部建成、四营正常
  爆枪兵（SCV 真站住把兵营修完，没被拉回）。

### 2026-07-05 玩家手册英文版 + 内嵌 DeepSeek 聊天助手（#589）

**新增 (Added)**：
- **玩家手册英文版**：主界面切英文后，Player Guide 也是英文（`USER_GUIDE_EN.md` 全译；guide.html
  双语，按 `?lang=` / localStorage 与主 UI 联动 + 右上角"English/中文"切换钮）。
- **手册内嵌 AI 助手**：guide 页右下角聊天窗，接 DeepSeek V4 Flash，**把玩家手册的纯玩家部分当系统
  提示词 + 固定 FAQ**，专答"这游戏怎么玩/指令怎么下"。无关问题礼貌拒答。`GET /api/guide-chat`，每 IP
  每分钟 20 次限流（防滥用烧钱；当前无鉴权、敞开用，未来加）。
  - **只喂纯玩家内容**：手册里"起服务器 / 扫码部署 / 搭 1v1 / `--my-race` / 版本"等**主机/开发者**
    内容用 `<!-- chat-skip-start/end -->` 注释标记剥掉，不进系统提示词（否则玩家问"怎么玩"会被回一堆
    部署步骤——用户反馈；注释渲染不可见，不影响手册页本身）。系统提示词也强调"玩家已在游戏里，别讲部署"。
  - **聊天回复渲染 markdown**：LLM 输出的 `**加粗**`/列表/`代码`/标题在聊天窗里渲染成样式（先转义
    防 XSS），不再显示成一坨原始符号。
  - **架构描述更新到最新玩法**：手册（USER_GUIDE / EN）+ 系统提示词把老说法「你在 PC 大屏看战况」
    改成「**实时游戏画面推流到手机，玩家全程看手机 + 手机指挥**，PC 只是主机不用看」（LiveView 早已把
    SC2 画面推到手机；老手册没跟上）。否则问"介绍一下"会答成"你在电脑上看"——用户反馈。
  - **补文档「持续指令 / 工厂指令」**：手册（USER_GUIDE / EN §六）新增一节，教"以后新出的单位自动
    套指令"的玩法——持续编队（`auto_enroll`，新出的自动进 N 队）/ 出兵集结点（rally_point）/ 持续骚扰
    / 持续姿态。功能早已支持、LLM 也懂，但手册没记 → 聊天教不了、玩家学不到（用户问"介绍里有工厂指令吗"
    发现的 gap）。补后聊天能正确解释了。

### 2026-07-05 大件 → 大和舰（统一玩家常用叫法）

**变更 (Changed)**：
- Battlecruiser 面板/语音/卡片显示统一为 **"大和舰"**（`default_display` + 骚扰卡文案）。"大件"是
  ASR 把"大舰"(同音 dà jiàn)误转的错字。别名同时收 大和舰/大舰/大件（语音怎么转都能解析）。

### 2026-07-05 视频进游戏后要点框才有声 → 任意交互自动开声

**修正 (Fixed)**：
- 浏览器自动播放策略拦带声视频 → 原"首次手势解除静音"漏了两点：没用 capture 阶段（子组件
  stopPropagation 的手势收不到）、只在轨道到达后才挂监听。改成 **capture 阶段 + 进游戏即挂 +
  确认真解除才撤**——进游戏后**任意交互**（发指令/点按钮）就自动开声，不必专门点视频框。

### 2026-07-05 死神/通用单位「去骚扰农民」不执行 → 修（#588）

**修正 (Fixed)**：
- 玩家说"让死神去矿区骚扰农民"死神杵着不动：`harass_workers` verb 被 director skip_action（"由 act
  控制"）但**从没建执行器**（只有 BC 专用 group_harass 有）。现接上现成的 `HarassWorkerLineAct`
  proven hit-and-run 微操（走 director 侧 always-on，玩家 claim 的死神/女妖/恶火在任意 build 都生效）。
  连带修一个 `target=None`（不指明矿区的常见用法）在命令提交前裸崩、导致 claim 从没提交的真 bug。

### 2026-07-04 大和舰群骚扰状态机推倒重写（#587，改 4 次坏 4 次后重来）

**变更 (Changed)**：
- BC 群骚扰重写成**每艘恰好一个状态**的干净状态机（STAGE 奔集结点 → DIVE 扎矿 → HEAL 传送养血），
  删掉旧的一堆纠缠 latch + 双 posture。用户历次真机抱怨全部定位到真因并修：
  - **第一艘杵家 2 分钟**：真因是敌矿没侦察到时目标为 None、BC 原地待命（非"新兵拽回"）→ 加兜底集结点。
  - **去一半被拉回主基**：切矿时路径从家重算、半路 BC 从"第 0 点=家"重启 → 改从当前位置续算。
  - **"跳回家"是走回去的**：改真战术跳传送 + 到家 hold_position 停下等 SCV 修。
  - **路线乱/太绕**：改基本直奔、只绕敌方主基（`plan_avoid_path`，非绕整圈的 `plan_edge_path`）。
  - 跨 3 种族 × Medium/Hard/VeryHard **~24 局：13 胜 2 平 3 负、0 崩溃**（负全是 VeryHard build 强度）。

### 2026-07-04 人族新 build：四兵营偷家 rush（proxy_4rax）+ 野兵营选址参考 4bg

**新增 (Added)**：
- **`proxy_4rax`（四兵营偷家rush）**：1 家兵营 + 3 隐藏野兵营全爆枪兵，3 农民建完前压，可选地堡封锁
  敌斜坡（默认关）。野兵营落点**复用神族 4bg（forward_proxy）的选址算法**（候选点 + 评分 + 避开敌方
  natural + 贴边 + query_pathing 可达性），proxy 选在距敌 ~33 格、偏离攻击轴的隐蔽走廊。

### 2026-07-04 玩家选 Random 种族导致整局 crashed → 修（#585）

**修正 (Fixed)**：
- 加入玩家选 Random 种族 → `make_bot_class("Random")` 抛 NotImplementedError → bot 起不来整局崩。
  现在在上游一处把 Random 解析成随机具体种族，喂给 bot plan + SC2 Bot 两边一致。

### 2026-07-04 双人局 SC2 窗口自适应：改分辨率后不重叠 + 保持 4:3（#586）

**修正 (Fixed)**：
- 改桌面分辨率后双人局两个 SC2 窗口太大/重叠：根因是屏幕分辨率 server 启动时缓存一次。改成**每局重新
  检测当前分辨率**（DPI-aware）+ 按 **4:3** 横向平铺算显式尺寸，保证不重叠、不超屏、不变形。

### 2026-07-04 #582 修 bc_rush 工厂被「升不了的轨道升级」占矿饿死 + 农民会硬停

> 用户视角：bc_rush 里兵营好了、钱气都够，却迟迟不下工厂（真机里矿堆到 400+ 就是不建）；
> 而且农民造到一定数就停了。现在工厂第一时间就下、大件持续产，农民也不再停（无上限一直造）。

**修正 (Fixed)**：
- **工厂被 MorphOrbitals 占矿饿死（根因）**：sharpy `MorphBuilding.execute` 每帧对 ready 的 CC
  尝试升轨道——**能付就扣 150、否则预留 150，但不检查 CC 是否在忙**。bc_rush 里 CC 一直造 SCV、
  永远没空升轨道，却每帧照占 150 矿；叠加 SCV/房子预留把工厂饿死到矿 float 到 ~450 才建（晚 85s）。
  **修**：vendor patch `morph_building.py` —— **只对空闲 CC（无 orders）尝试/预留**，忙则跳过不占矿。
- **工厂/科技链资源预留次序错**：SCV(priority) + MorphOrbitals 被摆在科技链**之前**，违背 plan 自己
  「BC/科技链永远先吃钱、其余吃余量」的设计意图。**修**：把 SCV、MorphOrbitals 挪到 BC 步骤**之后**。

**变更 (Changed)** — 玩家能感知的：
- **农民无上限、永不硬停**（用户 2026-07-04 拍板）：SCV 从「priority + 封顶 23/44」改成
  **non-priority + cap 70(≈不封顶)** —— 只吃「大件/科技链之后」的余钱、**绝不占大件的钱**（大件永远先造），
  农民随便多少、一路涨（冲破旧 23 封顶）、永不硬停；开二矿后 `DistributeWorkers` 自动把过饱和农民分流到二矿。
  （推翻旧「农民优先级 > VF / 封顶 23」——用户新拍板：大件不停造是硬约束，其上农民不停+适量过饱和。）

**真局验收 (实时单人 vs Hard + build_acceptance)**：工厂 **177s→142s**（早 35s，矿不再 float：445→25-65 在花）；
农民 12→**24→25** 持续涨冲破 23 封顶、`idle 0.1` 不闲置；BC 从星港**持续 pump**（4:00/5:05/6:12/7:41…）；
build_acceptance 科技链除 factory「因变快低于旧下限」外全 PASS → 已 recenter factory spec 到新 timing(2:25±40)。

**代码/测试**：`vendor/.../morph_building.py`（`# vibecraft:` 空闲CC才占矿）；`plans/bc_rush.py`
（SCV→non-priority无上限 + SCV/MorphOrbitals 挪到 BC 后）；`tests/build_acceptance/bc_rush.yaml`
（factory 3:15→2:25、economy_profile 反映不再float+农民无上限）。复盘见 `docs/pitfalls.md`。

### 2026-07-03 #581 大件骚扰接近路径重构：直奔矿后 + 贴主基地边缘从背后切入 + 到位 move-attack

> 用户视角：大件群骚扰以前**绕地图外圈绕整整两条边**才到敌方（对角出生要 100+ 游戏秒），
> 大部分时间在自己这侧打转、几乎打不到敌方农民。现在**基本直奔敌方矿后点**，只在直线会从
> 敌方主基地头顶穿过时**贴着主基地高地边缘绕一下、从矿背后切进去**（对方反应窗口最短），
> 到位后**贴着农民边移动边开火**杀伤，遇地面部队才卡射程外游走。

**变更 (Changed)** — 玩家能感知的：
- **接近路线**：从「贴地图外围绕整圈」(`plan_edge_path`) 换成「直奔 + 必要时绕主基地」
  (`plan_harass_approach`)。连线判垂距：直线不撞主基地就直飞；会撞就沿垂直方向推一个拐点擦着
  主基地视野边缘绕过去，并走「场外集结点 → 矿后点」保证**从矿背后/外侧切入**（不从基地头顶压过）。
- **矿后落点更贴矿**：`_BEHIND_MINERAL_OFFSET` 3.5→0.5，几乎贴矿线，大件射程（6）能罩住整条矿线农民。
- **到位微操**：无对空威胁 → 对农民质心 **attack（a-move 动中开火追农民）**（原来只 move）；
  有地面对空威胁 → 保持 move 卡射程外（`_p1_threat_flee`）+ 评分器自然换没防空的矿。

**修正 (Fixed)**：
- **群骚扰去自己矿后不去对方矿后（根因链）**：① `_patrol_fallback_rank` 纯计时器每 11 游戏秒轮换目标矿、
  不管 BC 到没到 → 贴边路径每换目标从远角从头重算 → 横穿全图走不完就被换目标 → BC 卡自己侧三个角打转。
  **修**：轮换加「到达门」，任一 BC 真进当前矿 airspace 才计驻留/才轮换。② 接近段结构缺陷（opus 评审）：
  stage/behind 都落在旧 near/far 门(`_APPROACH_DIRECT_FROM_ZONE=24`)内 → near-micro 提前接管、丢弃接近
  waypoint → 「从背后切入」永不执行。**修**：near/far 交接改由 approach waypoint 是否走完(`_approach_arrived`
  闩锁)驱动，不再用「距中心<24」。

**真局验收 (vs Hard，BCRAID_TRACE)**：8/8 BC 全部到达敌方主矿（min_dmain 0.6~6.2，per-instance）；
16 次到达 behind_dot 全 >0（**全部从矿背后切入**，外部坐标验证，非中间 trace）；接近 dmain 123→21 仅 ~40 游戏秒
（旧版恒 120 绕角）；结果 Victory。

**代码/测试**：`drop_path.py` 加 `plan_avoid_path`（垂距避障，退化选贴边侧）+ `plan_harass_approach`
（场外集结点）；`bc_raid_act.py` `_edge_path_wp`→`_approach_wp`(闩锁) + `_raid_move_point` 返回 (点, move/attack)；
`plan_edge_path` 从 BC 路径退役（函数保留）。新 `tests/unit/test_harass_approach.py` + `test_bc_raid_act.py`
接近/到位/闩锁/patrol 到达门回归。设计 + opus 评审处置见 `docs/plans/2026-07-03-bc-harass-approach-micro-design.md`。

### 2026-06-29 #580 大件骚扰重构成「组队协同 + 贴边接近」（总览）

> 用户视角：大件骚扰从「每艘大件套一张独立卡、各自巡逻绕主矿圈」彻底重构成
> 「一条指令控制整个大件群、组队协同、贴边偷袭」。下面 Chunk A/B/C + AA 链条是分块实现细节。

**变更 (Changed)** — 玩家能感知的：
- **组队协同**：所有大件由**一条群指令**控制（一张「大件骚扰群 ×N」卡），新出大件自动入群。
  健康分状态机：够数（满血）才**一起出击**，残血**回家修**（普通飞、不浪费传送 CD），满血再一起出；
  减员太狠自动回撤重组。
- **贴边偷袭**：接近敌矿走**地图边界贴边**（`plan_edge_path`），晚被发现、多打农民——修掉老
  `plan_drop_path` 绕主矿中心画圈的 bug（实测绕角 3200°→683°，接近段离地图中心 57.6 格、0% 穿中央）。
- **自保优先于杀农民**（优先级行为树 P0-P3）：遇敌方大部队/防空，**先拉到射程外保命**再说杀农民；
  能秒的孤立孢子顺手集火打掉；矿后定位用矿体挡地面单位。
- **语音可控**：「所有大件去骚扰」/「派 3 个大件骚扰二矿」/「骚扰减到 2 艘」/「停止大件骚扰」。
- 副产品：通用「新出 X → 编组/执行某任务」ECA 能力（`unit_claim(recruit_new) + target_count` 上限）正经化。

### 2026-06-29 #580 Chunk C：语音控制 + 幂等更新 + UI 群卡 + i18n

**新增 (Added)**
- **group_harass 幂等更新**（`_try_upsert_group_harass`）：LLM 下达第二条 group_harass
  unit_claim 时，Director 检测已有同 verb claim → 更新 target_count + target，不新建重复 claim。
  幂等：submit N 次得到同一个结果（"减到2艘"第一次更新，第二次同样结果）。
- **partial-release**（`_partial_release_group`）：target_count 降低时立即从 claim tag 集
  释放多余 BC（优先满血的 / 在家待命的，opus D2）；从 recruit watcher seen 移除（支持稍后
  target_count 调高时重新入伍）；走 WP-C 恢复逻辑（尝试还 prior / 否则 release_unit_role）。
  target_count=0 → 释放全部，directive 留存（暂停态，✗ 才删）。
- **UI 群卡**（`_build_command_cards` L3）：group_harass claim 生成独立卡片
  `type="group_harass"`，display = i18n「大件骚扰群 ×{n} → {target}」（n=群内艘数，
  target=锁定矿/「自动」），`revokable=True`。其余 unit_claim 继续走原 `type="unit_claim"` 卡。
- **i18n 字符串**（`locales/strings.json`）：`card.groupHarass` + `harass.targetAuto`
  中英双语；display 走 `_i18n_t`，零硬编码中文（英文门通过）。
- **LLM few-shot**（`docs/llm_prompt/few_shot.md`）：新增例 62-65，覆盖 group_harass
  全部场景：所有大件去骚扰（target=null）/ 指定艘数+矿 / 减到N / 停止骚扰。
- **LLM rules**（`docs/llm_prompt/rules.md`）：新增「大件群骚扰（group_harass）」规则段，
  说明 target/target_count 映射 + 不支持相对操作。LLM prompt 重 dump 成功（108k chars）。
- **单测**（`tests/unit/test_group_harass_control.py`）：10 个新测试全部通过，覆盖
  幂等更新 / partial-release 优先满血 / seen 清除 / target_count=0 claim 留存 /
  UI 群卡 type+display / en locale 零中文泄漏。
- locale snapshot gate（`tests/unit/test_locale_snapshot_gate.py`）补 group_harass
  directive，门仍全通过（6/6）。

**后续（本次不做）**
- 相对"撤回 N"（`target_count -= N`）：Director 当前无上下文推断当前 target_count，
  LLM 需要拿到绝对值才能幂等更新；留待以后补 `context.current_group_count` 字段到 prompt。

### 2026-06-29 #580 GroupHarassAct P1 威胁规避细化：cheap-kill + 精确射程

**新增 (Added)**
- **P1 cheap-kill 静态防空**（`_p1_aa_cheap_kill`）：P1 规避前先判附近孤立静态防空建筑
  （SPORECRAWLER / MISSILETURRET / PHOTONCANNON）能否"顺手秒掉"。条件：① 建筑在 10 格内
  无 army / 无其它 AA 接力（孤立）；② `kill_time × building.air_dps < avg_bc_hp × 0.5`
  （群 ground_dps 够快杀）。成立 → 群集火打掉该建筑（不逃，解锁矿线）；不成立 → 走规避。
  trace: `BCRAIDTRACE aa_killkite tag=.. mode=kill dist=..`
- **P1 精确出射程**（`_p1_threat_flee` 补 2）：flee_dist 从固定 12 格改为
  `max(in-range 威胁的 air_range) + buffer(2)`，刚好出所有威胁射程外，避免过躲 / 欠躲。
  air_range 取不到退回保守 fallback 12 格。trace: `BCRAIDTRACE aa_killkite tag=.. mode=flee dist=..`

**变更 (Changed)**
- P1 链条明确为两层：帧级（cheap-kill → 精确射程躲避）+ 群级（picker score 暴跌 → relocate）。
  旧单一 flee 路径扩展为"先判能不能打、能就打、不能就精确拉开"。
- `_P1_FLEE_DIST(12)` 降级为 fallback 常量（air_range 缺失时用）；新增常量
  `_P1_FLEE_RANGE_BUFFER / _P1_CHEAP_KILL_ISOLATION_RADIUS / _P1_CHEAP_KILL_BUDGET_RATIO`。
- 设计文档 `docs/plans/2026-06-29-bc-harass-group-design.md` §3.6 P1 段补全两层链条描述。
- 单测 `tests/unit/test_bc_raid_act.py` 新增 7 条覆盖：cheap_kill 成立/不成立（army接力/火力不足）
  + 集成（tick 集火建筑）+ 精确射程 flee_dist + air_range fallback。

### 2026-06-29 #580 GroupHarassAct BC 群骚扰 — 招募 bug + 绕圈根因修复

**修正 (Fixed)**
- **`recruit_new` watcher 提前 return 导致永不征兵**（根因）：`_assign_standing_order_units` 在
  selector 无当前匹配单位时（`if not tags: return`）直接返回，把 watcher 注册代码绕过 → 之后
  新建好的 BC 永远不被加入 group_harass claim → `bc_harass_groups["tags"]` 恒为空 →
  `GroupHarassAct` 从未驱动任何 BC。修法：把 `recruit_new` watcher 注册提前到 `if not tags: return`
  之前（tags 为空也必须注册，因为之后建好的单位还要靠这个 watcher 入伍）。
- **BC 骚扰绕圈（旧 3730°+ → 不绕圈）**：`_raid_move_point` 远途分支用 `plan_drop_path`（Dropship
  绕道绕开 zone 的算法）→ BC 沿 zone 外围单向绕行永远进不去。BC 是重甲攻城单位，应直飞目标。
  修法：远途直接 `return target`（矿线锚点），不走 `plan_drop_path`。实测终态 d0=0.5 / d1=4.6（均 < 9），
  bc_rush 3 艘 debug BC + 开局真 BC 直飞敌矿线，2:22 SC2 时间 Victory。

**变更 (Changed)**
- `bc_harass_selftest.py` 绕圈指标改为双报（abs_total 含 sweep 振荡统计 / net_rotation 判真实绕圈），
  FAIL 门去除纯角度条件（功能 PASS 凭 d0/d1 终态铁证；角度作为诊断参考）。
- `test_bc_raid_act.py`：`test_raid_move_point_far_uses_path_avoidance` 改为
  `test_raid_move_point_far_flies_direct_to_target`，验证新直飞行为。

### 2026-06-29 i18n 英文模式「零中文」收尾 + 三道硬门

英文 locale 下后端仍有大量玩家可见中文绕过 i18n（命令卡片 display、条件/前置文本、产能
status_reason、命令反馈、错误/校验/回收/维修/空投提示、doctrine micro 要点）。本轮全部归位
+ 建机器门防回归。`locales/strings.json` 292→506 key。

**新增 (Added)**
- **三道 i18n 硬门**（`tests/unit/test_locale_snapshot_gate.py`）：①动态——en snapshot 跑全
  directive + 全剧本 + 条件文本构造器，断言零 CJK；②静态——AST 扫 `director.py` 断言无玩家可见
  中文字面量；③key 存在门——扫源码所有 `_i18n_t("k.k")` 断言 key 在 strings.json 且 en 非 None。
- `Localizer.race()`（种族名 神族/Protoss）+ `RACE_NAMES` 表；`structFull.*` 建筑全名键（澄清问句
  用全名「重工」而非 hotkey）。
- doctrine `engagement_doctrine_en` 字段 + 18 个 doctrine yaml 补 SC2 准确英文 micro 要点（en 模式
  剧本卡片 micro 要点不再显示中文）。

**变更 (Changed)**
- director 所有玩家可见 display/卡片/条件/状态原因/命令反馈改走 `_i18n_t(self._lang)` + `Localizer`，
  删 4 个冗余 zh 数据表（`_ARMY_UNIT_ZH_NAMES`/`_RACE_ZH`/`_PRODUCTION_BUILDING_ZH`/`_UNIT_ZH`），
  消费方统一走 `self._loc.unit/structure/race(...)`。
- parser 11 处 `ParseError` 中文 message + match.py「3+ 真人未支持」改走 i18n（en 模式解析失败/房间
  错误回显英文）。

**修正 (Fixed)**
- **`t()` 把「有意空译」误当缺译回退中文**：旧 `entry.get(lang) or entry.get('zh')` 让 en 空串回退
  zh「个」→ 产能卡进度条单位 en 模式泄漏中文。改成「locale 显式有该键(含空串)就用、缺键才回退」。
- **i18n 重构改变了 zh 行为**：澄清问句建筑名从全名「重工」变 hotkey「VF」（路由到了 hotkey 表）。
  用 `structFull.*` 还原 zh 全名 + 提供 en，zh 行为零回归。
- **覆盖门假阳性**：动态门构造的 Director 没建 task_monitor → 条件分支被短路跳过、门绿仍泄漏
  （opus 评审揪出）。门改为传 `event_bus` + 断言 task_monitor 非 None，让分支真被执行。

### 2026-06-28 i18n 浏览器 mock UI 验证 + 编队条英文数量被截断修复

**修正 (Fixed)** — 用浏览器 + mock 数据逐面板跑中英两套排版（不依赖真游戏）发现并修。
- **编队条（VoiceGroupBar）英文单位名 truncate 把数量也吃掉**：英文官方名长（"Void Ray×6"
  整串 truncate → 显示 "Void Ra…"，**×6 看不见**）。修法：名字与数量拆成两段渲染——名字
  `truncate min-w-0`、数量 `shrink-0` 永不裁。现显示 "Void… ×6"，数量始终可见。新增
  `unitEntryParts()`（util）；`VoiceGroupBar` 改用它。
- 扩 preview harness 注册表：加 VoiceGroupBar / TechProgressPanel / ClarificationOverlay /
  CommandHistoryItem 的 mock fixture（动态文案按 locale 出，模拟后端预渲染），`preview.html?c=X&locale=en`
  可单组件截图。Playwright 390px 实测这 4 个 + 入口页 + 原 6 组件中英**均无横向溢出**；
  澄清弹窗长英文问题/选项换行干净、数量 chip 不丢。

### 2026-06-28 i18n 文档同步（ARCHITECTURE / USER_GUIDE / README / CONTRIBUTING）

**变更 (Changed)** — 中英本地化 + 英文 ASR 落地后同步四类文档（功能交付 = 代码 + 测试 + 文档同源）。
- `ARCHITECTURE.md`：新增「i18n / 本地化」节（strings.json 单一真理源 + locale 全链路数据流 +
  双模型 ASR 表）；不变量加一条「玩家可见字符串必须进 strings.json，不硬编码」。
- `USER_GUIDE.md`：新增「切换语言 / English」小节（切换入口 + 英文指令真实话语示例 + 离线 ASR 行为）。
- `README.md`：功能表加「语言切换（中/EN）」；ASR 改双模型说明；部署加 `prefetch_asr_en.py` 预拉提示。
- `CONTRIBUTING.md`：新增「如何加一种语言」四步指引。

### 2026-06-28 英文语音识别（ASR）：SenseVoiceSmall 离线模型 + 按 locale 路由

**新增 (Added)** — 开源前 i18n 的英文语音输入支持（设计 §10，spike 实测 + opus 评审）。
- 英文 ASR 用 **SenseVoiceSmall**（多语、离线/非流式；spike 实测 SC2 英文指令近乎完美）。
  `AsrEngine` 改为**按 locale 双模型**：`zh`→`paraformer-zh-streaming`（流式，逐字 partial + 热词，
  现状不动）/ `en`→SenseVoice（离线，松手后整段一次解码）。各 locale 独立惰性加载/锁/可用性。
- 会话拆 `StreamingAsrSession`（zh）/ `OfflineAsrSession`（en）两类：en 的 feed 只累积 buffer
  （封顶 ~25s，超限丢最旧）、finalize 整段解码 + 剥 SenseVoice `<|en|>…` 标签/尾标点（优先官方
  `rich_transcription_postprocess`）。`AsrEngine.create_session(locale)` 路由；`warmup_en()` +
  ws 握手见 `locale=en` 后台预热（避免首句卡加载）。
- en 模型加载失败 → 前端 `asr_unavailable` toast（按 locale 本地化），不静默丢音频。
- 新增 `scripts/prefetch_asr_en.py`（部署期一次性预拉 ~1GB 模型）+ `scripts/asr_en_selftest.py`
  （**真模型** + 提交的 wav fixture 跑全链路自验，hermetic）。**实测 6/6 句正确**识别。
- 测试：`test_asr.py` 加路由/离线 feed-finalize-cancel/buffer-cap/标签剥离/en加载失败隔离等 11 例；
  `test_server_ws.py` fake engine 同步 `available_for`。strings.json +`voice.asrUnavailable`。

### 2026-06-28 i18n 英文指令解析精度：英文 few-shot 补充

**变更 (Changed)** — 提升英文语音/文字指令的解析一致性（设计 §2 Layer C）。
- 新增 `docs/llm_prompt/few_shot.en.md`：~11 条**英文话语 → 同一套 directives** 示例（build/train/
  research/defend/retreat/attack/scout/group/proxy-build/move/build_at），教 LLM 英文输入映射到
  规范 directive（enum id 仍是 Stalker/Gateway/VoidRay 等英文规范名）。**不翻译** 1325 行中文主示例，
  只做补充。
- `prompt.py` 加 `build_few_shot_en_supplement()`；`IntentParser` 在 `locale=="en"` 时把补充拼到
  静态 `_few_shot` 块尾（按 session locale 恒定 → cache 友好，**不污染**中文路径、不进每次 dynamic）。
- 别名表已是中英双语（Nexus/Gateway/Stalker/Immortal/VoidRay…），无需新增。
- 测试：`test_prompt.py` 加补充非空 + 含英文示例 + 仅 en locale 拼接（zh 不含）3 例。

### 2026-06-27 i18n 单位名 + 建筑/升级名 + 澄清弹窗本地化

**变更 (Changed)** — 中英全量本地化的专有名词 + 代码生成澄清消息部分。
- 前端单位名 locale-aware：`web/src/utils/unitNames.ts` 加 `UNIT_EN`（官方英文名 Zealot/Stalker/
  Void Ray/High Templar…）+ `unitName(id)` 按 `i18n.locale` 选 zh/en（切语言即时重渲）；
  `VoiceGroupBar.vue` 删掉内联重复表，统一走该共享工具。新增 `unitNames.test.ts`（zh/en + key 集一致）。
- 后端 `bot/localization.py` 5 张名称表（单位/升级/产能建筑/科技建筑/战术动词）补 `en` 列：
  单位用官方英文名、建筑保留 hotkey（en=zh，行星要塞→PF、VS(人族)→VS(T)、大刺翼→Greater Spire）、
  升级用简洁英文（+1 Atk/Stim/Charge…）、动词 title case。每表 en/zh key 集严格一致。
- `Director` 把玩家语言接进名称本地化：`self._lang = parser.locale`，`Localizer(self._lang)`
  （原来固定 zh）→ snapshot 面板的科技/产能/兵种名按玩家语言显示。
- 代码生成的两处澄清弹窗（基地贴矿 snap 确认、产能建筑挂件选择）改走 `vibecraft.i18n.t(key, lang)`：
  新增 `clarify.townhall.*`/`clarify.addon.*` 共 11 key（zh 与原文一字不差，既有 test_addon_confirm
  /test_clarification 断言不变；en 同步给出）。strings.json 总 325 key、0 缺译。

### 2026-06-27 i18n 后端服务端消息本地化（解析 echo + 大厅错误）

**变更 (Changed)** — 开源前中英全量本地化的后端消息部分（设计 `docs/plans/2026-06-27-i18n-localization-design.md` §2 Layer B）。
- 指令解析 echo 前缀 `[模糊]`/`[解析失败]`（`bot/auto_combat/common.py`）按玩家 locale 本地化：
  取 `director.parser.locale` → `vibecraft.i18n.t("echo.ambiguous"/"echo.parse_failed", lang)`。
  英文玩家看 `[Ambiguous] …` / `[Parse failed] …`；interpretation 正文本身已由 LLM 按语言生成。
- 大厅 / 对局 `room_error`（房满 / 非房主 / 未准备 / 认输时机 等 21 类）全部本地化：
  `RoomError` 改为可携带 i18n `key` + 参数（`str(e)` 仍返回 zh，日志与既有 `match=` 测试不变），
  WS 层 `e.localized(self._locale)` 按玩家语言回帧；`ws.py` 里直发的中文 room_error 文案也走 `t()`。
- strings.json 新增 `echo.*`（2）+ `room.err.*`（21）共 23 key（zh+en），总 314 key、0 缺译。
- 测试：`test_locale_penetration.py` 加 RoomError.localized（含参数/无 key 回退）、room.py 所有 key
  在 strings.json 有英文翻译的防漏译扫描、`run_command_with_echo` echo 前缀 en/zh 端到端 12 例。

### 2026-06-22 PWA 入口页 + 大厅加「分享二维码」按钮

**新增 (Added)** — 用户：想在游戏首页和大厅弹出首页 URL 二维码，扫一下就能访问游戏主页。
- 入口页底部（全宽按钮）+ 大厅顶栏（小药丸按钮）各加 [分享二维码]：点开弹窗显示**当前页面 URL**
  的二维码 + URL 文本 + 一键复制。URL = `window.location`（origin+path+search），即用户当前进来用的
  地址——经公网前门进来就是公网 URL、含 `?room=` 房间码（扫码后自动填入，与启动二维码一致）。
- 新增 server 端 `GET /api/qr?data=<url>` → 返回该串的**高清 PNG**（820×820，box_size=20；复用
  Python `qrcode`+Pillow）；data 长度上限 1024、只进 QR 矩阵不作文本（无注入）。
  **用 PNG 而非 SVG**：识别工具下载图片后常按 SVG 的 mm 固有尺寸栅格化成小图（~140px）判"太小"，
  PNG 直接给足像素。弹窗加「下载高清图片」链接。`qrcode[pil]` 写进 pyproject 声明 Pillow 依赖。
- 新增可复用组件 `web/src/components/QrShareButton.vue`（size full/sm 两态）；5 条 vitest 通过。
- 实测：`/api/qr` 本地 + 经公网 VPS 前门均返回 `image/png`（下载实测 820×820），超长 data 返回 400。

### 2026-06-22 修「地堡」被 ASR 听成「低保」误判补给站 + 别名按映射分组

**修正 (Fixed)** — 用户：人族语音「下一个地堡」常被识别成同音「低保」，LLM 拿到后误判成补给站(房子)。
- 别名表 `docs/aliases/terran.yaml` Bunker 加 ASR 同音误转：`低保/地保/碉保/堤坝`。
- **根因更深**：`build_race_block` 原来把建筑别名渲染成**扁平词表**，只告诉 LLM"这些是建筑词"、
  **不给 alias→建筑的映射** → LLM 对不认识的同音词(低保=地堡)凭先验乱猜(→补给站)。改成**分组**
  渲染 `别名1/别名2→规范名`（与剧本 catalog 同款），映射对 LLM 显式可见。这同时修复所有现/未来同音别名。
- 实测(真 LLM)：「在这里下一个低保」→ Bunker(原来→SupplyDepot)；无位置变体 8/9 → Bunker(原来几乎全错)。
  voice_spot_check 42-43/45（剩余 fail 是 compound-split 的 LLM 非确定性，两轮不同案，与本改动无关）。
- 加 prompt 回归单测（别名分组 + 低保归 Bunker）；122 prompt/parser/alias 单测通过。

### 2026-06-21 全开局 build 评审 + 修 widow_mine_drop 特别差（#564）

**新增 (Added)** — `docs/build-execution-review.md`：build 执行质量评审清单（8 维度 + 流程 + 诊断速查 + 评审坑）。

**修正 (Fixed)** — 按评审标准把 10 人族 + 9 虫族开局 build 全跑了一遍 triage（VeryHard 并行）+ VeryEasy
交叉验证，找出唯一**特别差**的 `widow_mine_drop` 并修复：
- 病：`TerranUnit(MARINE, 60, priority=True)` 让只吃矿的枪兵 reserve-ahead 占光矿，饿死 Factory/
  Starport/Expand/寡妇雷/医疗船（都要矿）→ 实测 t=357 gas 堆到 1069 没人用、**0 寡妇雷 0 医疗船**
  （drop build 的命根子全废）。同 bc_rush「枪兵优先于核心→核心饿死」。
- 修：枪兵降为非 priority 小量 filler（60→6）；寡妇雷升 `priority=True`（核心战斗单位优先拿产出）。
- 实测：VeryEasy 2/10→8/10（寡妇雷 0→10、starport 准点、stim 完成、后劲 supply 66→187）；
  VeryHard ×3 timing 12/15、寡妇雷 5-9、supply 150；余钱 1069→305/647（gas 不再堆=核心在消耗）。
- 已知小残留（非特别差，不过度调）：医疗船 0-2（drop 载具仍偏少，受 starport 节奏+VeryHard 战损）；
  CC2 扩张略晚。其余 18 个 build 经交叉验证均非特别差（macro_hatch 单局 VeryHard 噪声，VeryEasy 9/9 健康）。
- 附带发现（未修，记此）：并行 build_acceptance 时 telemetry `active_recipe` 字段会错标（不影响验收，
  因 verify 按 game_id 读对的局；但用 active_recipe 认 telemetry 目录不可靠）。

### 2026-06-21 LLM api_key 可在 config 文件配置（不再仅限环境变量）

**变更 (Changed)** — 用户：deepseek 模型 / API 端点 / api key 都要可配置。
- model 与 base_url（API 端点）本就可在 `config/llm.yaml` 配；本次补上 **api_key 也可在 yaml 配**。
- `LLMConfig` 新增可选 `api_key` 字段。key 解析优先级：`build_provider(api_key=)` 参数 >
  yaml `api_key` 明文 > `api_key_env` 指向的环境变量（默认 DEEPSEEK_API_KEY）。向后兼容：
  yaml 不填 api_key 时行为不变（仍读环境变量）。
- **安全**：明文 `api_key` 只能写进已 gitignore 的 `config/llm.yaml`；`config/llm.yaml.example`
  里只放注释示例，绝不提交真 key。
- 加 3 条单测（yaml key 被用 / yaml key 覆盖 env / 显式参数覆盖 yaml）；46 测通过，ruff+mypy clean。

### 2026-06-21 通用"空闲 CC 飞去开矿"（#560，用户 reframe）

**新增 (Added)** — 用户：可预先在家/任意处造一个额外指挥中心；bot 开矿时优先把这个空闲 CC 飞过去
落地开矿，没有空闲 CC 才新造。
- `SpareCcExpandAct`（注入所有 terran build 的 `_wrap`）：检测 spare CC（ready + idle + 周围无矿 =
  不在采矿点的停放 CC）→ 锁定最近未占扩张点（起飞前锁死，移动靶铁律）→ LIFT 起飞 → LAND 带落点
  （飞行建筑自动飞过去落地）。卡飞逃生（>25s 就地迫降）+ LIFT 发不出放弃门。**无 spare CC 即完全
  no-op**，对所有不造额外 CC 的现有 build 零影响（bc_rush 验收仍 7/7 + Victory）。
- 真机核对（cclift_probe）：`LIFT/LAND_COMMANDCENTER` 真机可用，**关键约束：CC 只有 idle（不产 SCV）
  才有 LIFT**。修坑：idle 单位直发 `cc(LIFT)` 被 python-sc2 prevent_double_actions 丢弃（同 salvage
  根因）→ 改走 `_vibecraft_bypass_actions`。
- 验证：cclift_probe PASS（CC 落到目标 dist=0）；spare_cc_expand_selftest PASS（终态 townhall 2 =
  spare CC 落到新扩张点开矿）；5 单测（no-op 契约 + 锁定 + bypass 发 LIFT）。经设计 + 独立 opus 评审
  （评审促成 reframe 去投机化）。
- 已知限制：与 plan 自身 Expand 的协调（理想抑制 plan 新造）列为后续细化。

### 2026-06-21 新增虫族 build：ZvP 运营流 zvp_macro（#550）

**新增 (Added)** — 补虫族对神族专用运营开局（spawningtool ZvP Standard Hatch First, build/199494 对标）。
- `zvp_macro`：hatch-first 经济 → 16 二矿 → 17 母池 → 快三矿 → **孢子匍匐者防空**（主+二矿各 1，
  防 Oracle 骚扰/DT 偷家/凤凰 + 反隐，ZvP 命根子）→ 蟑螂+刺蛇运营。多蜂后注卵防空。默认转
  roach_hydra 中期 / 后期 persistent_roach_hydra_viper（对天空神族转 persistent_lurker_hydra）。
- 新增 `plans/zvp_macro.py`(ZvpMacro) + `strategies/zerg/zvp_macro.yaml` + 验收 spec
  `tests/build_acceptance/zvp_macro.yaml` + 虫族 plan 构造审计 `tests/unit/test_zerg_plans_construct.py`
  (构造 + 占位/morph enum 拦截)。test_zerg_strategies openings 8→9。
- **调优**（实测发现 + 修，未放宽 spec）：首版孢子(4)+三矿+科技早投资把矿吃光 → drone 卡 ~15、
  蜂后拖到 5min。修：蜂后/早期暴农(24)优先级置于所有结构投资之前；孢子降到每矿 1 个且 gate 在
  双蜂后已出之后。修后 drone 16→23-25、蜂后准点。
- 实测：VeryEasy 13/13 PASS + Victory；VeryHard ×3 timing 23/24 PASS（多数票），经济强（drone 25、
  roach 17、全科技准点）；效率 bank 412 / larva idle 1.24（均健康）。

### 2026-06-21 通用维修指令真局自验 + 文档补全（#551）

**变更 (Changed)** — 通用维修指令（派 N 农民维修 XX，2026-06-19 已实现）补真局终态自验 + 文档。
- 新增 `scripts/repair_selftest.py`：bot 真实建地堡 → debug 持续打残一个窗口（120 游戏秒，避开
  sharpy 自带 Repair 抢修）→ 注入"派 3 农民修地堡" → 验 `REPAIRTRACE repair_dispatched`（首次
  dispatch hp≈0.13 = 真见残血）+ `repair_done_all_healthy`（修回满血 = 终态）。实测 PASS：建 1 座、
  dispatch 81 次、修满。证明真机路径 `facade.get_unit_health_percentage`/`ensure_repair`(SCV.repair) 生效。
- common_bot 加 env 门控测试钩子 `VIBECRAFT_REPAIR_SELFTEST`（持续打残窗口，仅自验用）。
- 文档补全：guide.html 加 ⑰维修 段（含 TOC）；USER_GUIDE 加维修话术（仅人族，只修机械/建筑）。

### 2026-06-21 修人族「下二气/补一个气矿」解析数量不稳（#553）

**修正 (Fixed)** — 用户：人族说「下二气」「补一个气矿」等气矿指令解析失败/数量乱。
- 真机复现：解析不再整条失败，但 few_shot 只有**神族 Assimilator** 气矿例、零人族例 → 终端 "二气"
  被当序数（+1）或 done_when.value=None，数量在 1/2/None 间 flaky。
- few_shot 加例 24c（人族气矿）：明确人族气矿 = `Refinery`；**"二气/两气/两个气/两口气" = 基数 2**
  （不是序数"第二个"、不是"二矿/natural"位置）；数量同时写进 `items[].delta` 和
  `done_when.value`（structure_count_built_since）。重 dump LLM prompt 快照。
- 实测（真 LLM ×3 轮稳定）：下二气/下两个气/补二气 → 2；补一个气矿/补个气/下个气 → 1，全部
  Refinery、delta=value 一致、跨轮确定。加 prompt 回归单测 `test_few_shot_has_terran_gas_example_553`。

### 2026-06-20 对局记录显示玩家用过的 build 序列（顺序 + 切换时间，滤 <10s 段）

**变更 (Changed)** — 用户：对局记录要显示玩家用过的所有 build，带切换时间和顺序；持续 <10s 的不算。
（取代当日早先的"前 5 分钟用时最长单个 build"方案。）
- `admin_games`：整局正向扫描 snapshot 的 `active_recipe`，切成连续同名段 `(build, 起始, 时长)`，
  过滤持续 < 10s 的段（开局默认值常几秒被玩家切掉 = 短段，正好滤掉），输出 `builds[]`（按时间序，
  含切换时间 `at_s`）。`active_recipe` 兼容字段 = 第一段；无段回退 game_start 默认。无缓存，重启后
  所有历史日志自动按新逻辑重新识别。
- admin 面板对局记录列改渲染 `builds[]` 序列：`bc_rush 0:06 → persistent_skyterran 7:45`（淡色显示
  切换时间）。注：开局自动切换到的 doctrine（如 persistent_*）也算一段 active_recipe，会出现在序列里。

### 2026-06-20 对局记录显示"前 5 分钟用时最长的 build"（不再显示开局默认 build）

**修正 (Fixed)** — 用户：对局记录里 build 显示的是开局默认 build，应显示前 5 分钟玩家实际用时最长的。
- 玩家常在开局几秒内切到真正想打的 build（snapshot 的 `active_recipe` 随之变），但 admin 对局记录
  只读 telemetry 首行 `game_start.active_recipe` → 一直显示开局默认（如切了 bc_rush 仍显示 reaper_expand）。
- `admin_games._extract_match_meta` 改为单次正向扫描：统计前 5 分钟（300s）各 snapshot 的
  `active_recipe` 出现次数（间隔近似均匀 → 次数最多 = 用时最长），显示该 build；无 snapshot 回退
  game_start。窗口外 snapshot 不计、超窗即停（不深读）。在记录生成（admin 扫描）时即算，retroactive
  覆盖所有历史日志。实测旧局 reaper_expand→bc_rush 现正确显示 bc_rush。

### 2026-06-20 玩家操作指南：详情表例子统一偏黄（同"四层力度速览"）

**修正 (Fixed)** — 用户：指南里举的例子应统一用"四层力度速览"那种偏黄色 `#ffd97a`。
- guide.html 详情表首列例子用 `td.say`，但被 `td:first-child{color:#cfe3ff}`（蓝）按 CSS 特异性压过 →
  显示成蓝色。改成 `.say, td.say { color:#ffd97a }` 提升特异性压回偏黄，与四层速览例子一致。

### 2026-06-20 admin 面板默认开 + admin token 下限 16→8

**变更 (Changed)** — 用户：起服务器要默认起 admin，admin token 长度下限降到 8。

- **admin token 硬下限 16 → 8**（`_ADMIN_TOKEN_MIN_LEN`；service.py 改用该常量，去重）。测试阶段 8 位够用。
- **start.ps1 默认开 admin**：`-AdminToken` 未传时按 `-AdminToken` → `$env:VIBECRAFT_ADMIN_TOKEN`
  → `.secrets/admin-token.txt`（gitignore）顺序自动取 token，无需每次手敲。取不到才关 admin。
  新增 `-NoAdmin` 显式关闭。机密只在 `.secrets/`，脚本不回显 token 明文。

### 2026-06-20 命名 server：首页服务器列表显示名称（不再显示完整 URL）

**新增 (Added)** — 用户：PWA 首页服务器列表目前显示完整 URL，要改成显示一个服务器名称，并有对应配置文件。

- **server 端命名**：新增 `config/servers/<name>.yaml` 配置（首个 `close_test.yaml`，字段 name/token/port/ip）；
  `vibecraft serve --config <path>` 加载，`scripts/start.ps1 -ServerName <name>` 解析到该文件。优先级
  默认值 < 配置文件 < 显式 CLI 参数；`-ServerName` 时 start.ps1 不再用默认 `-Token/-Ip/-Port` 覆盖文件值。
- **安全硬规则**：配置加载器遇到 yaml 含 `admin_token` **直接报错**——admin token 只能走 `--admin-token`/
  `VIBECRAFT_ADMIN_TOKEN`/`.secrets`，绝不入配置文件。故这份 yaml 可安全分享（"注册进公共列表"场景）。
- **公开 API** `GET /api/server-info` → `{"name": <name|null>}`，**只回 name，永不含 token/admin_token**
  （代码注释禁止扩展 payload）。
- **PWA 首页服务器列表**：条目以**服务器名称**为主，下面只附淡色 `主机:端口` 便于区分，**去掉完整 URL 和
  房间码**（避免肩窥）。扫码/带 `?room=` 打开时同源 `fetch('/api/server-info')` 取真名（async 失败回退
  `location.host`，防覆盖用户手动改名 / 条目已删除）；手动添加流程不变（跨源不 fetch）。
- 终端启动 banner 顶部加一行 `服务器: <name>`（仍保留 URL + 二维码给房主扫码）。

### 2026-06-20 BC 骚扰真凶修复：plan_drop_path 把大舰挡在矿外 ~20 格（二矿打不到农民）

**修正 (Fixed)** — 用户多次反馈"矿后点太远、二矿后面的点打不到农民、追农民没走几步就回去"。
前几次只调锚点（治标），这次定位到真正根因：

- **真凶：接近寻路用错工具**。`_raid_move_point` 在 BC 离锚点 >7 格时用 `plan_drop_path` 接近，而
  plan_drop_path 的职责是**绕开**敌方 zone（把 waypoint 推离 zone 中心 `R_MINERAL_AVOID 15 + PUSH 5
  = 20` 格）。但骚扰目标矿线**就在敌方 zone 内** → BC 被永远挡在矿外 ~20 格、距锚点恒 >7、永远进不了
  "贴农民"分支 = 坐在矿后打不到农民。二矿几何最不利所以最明显；主矿"还行"只是绕行点碰巧落得近。
- **修法**：一旦 BC 离**目标矿区中心**够近（≤ `_APPROACH_DIRECT_FROM_ZONE 24`，> 躲避气泡 20）就
  **直飞扎进矿线**，绝不用绕避工具躲开自己要打的那个矿；只有真正远途（跨图）才用 plan_drop_path 绕开
  **其它**矿区。`_harass_geom` 同时返回矿区中心给该判据用。
- **追农民改"以矿线锚点为圆心"**（原以 BC 为圆心半径 11）：农民逃出 BC 半径就掉出质心 → 追击缩回 =
  "没走几步就回去"。改以矿线锚点为圆心（半径 13）→ 沿整条矿线追，跟得住逃跑农民。
- **锚点回归 mineral_line_center**（工人真正站的采矿线，比 patch 质心更贴工人）。
- **真局自验（终态铁律）**：`bc_harass_selftest` 强化为**分矿验证**——记 BC 到三个敌矿矿线各自距离，
  断言主矿(d0)+二矿(d1)都被某 BC 真飞到（< 9）。旧自验只看单 BC best dist，主矿达标就 PASS、掩盖了
  二矿打不到的真 bug。实测：d0=1.3 / d1=1.7 / d2=3.0（三个矿都真扎进了农民堆）。

### 2026-06-20 语音"大舰传送/折跃回基地" → Tactical Jump（不再走回去）(#3)

**新增 (Added)** — 用户：说"所有大和舰传送回基地"结果它们慢慢走过去，识别不了传送技能。

- 根因：cast_ability 路径只支持**自施放**（archon 合体/风暴），EFFECT_TACTICALJUMP 是**对点施放**
  （传送到落点），director 没把落点传给 facade → 走 move 兜底 → 走回去。
- `facade.cast_ability_on_units` 加 `target_point` 参数（3 同步 FakeFacade/_SharpyFacadeBase/Protocol）：
  给了点 → `unit(ability, Point2(point))` 对点施放；None → 自施放（兼容 archon/风暴）。
- director cast_ability 路径解析 target named_spot → 落点坐标传 `target_point`。
- LLM few-shot 加例 39b：「所有大舰传送/折跃回基地」→ unit_claim(BattleCruiser, cast_ability,
  EFFECT_TACTICALJUMP, target=named_spot main)，明确"传送/折跃/跳"≠ move。
- test_cast_ability 加对点 Tactical Jump 断言（传 target_point、不走 move）。

### 2026-06-20 bc_rush 农民 23 防过饱和闲置 + BC 骚扰锚点改矿点质心(贴农民更近)

**修正 (Fixed)** — 用户真机反馈：

- **农民 24→23**（防过饱和闲置）：24 在不建造时主矿 18/16 过饱和 → 多出的农民显闲置/低效。23 =
  16 采矿 + 6 采气 + 1 建造缓冲：造建筑抽 1 个时仍 16 采，不建时只 1 个轻微过饱和（不闲置）。
- **BC 骚扰锚点改成矿点质心**（不再"矿后偏移"）：之前偏移把锚点推太远 → 采矿农民落在贴农民 chase
  半径(11)外、贴农民逻辑不触发 → 只打到 1-2 个。现在锚点 = 矿点质心（BC 飞进矿堆、本就在农民射程内），
  到点后再贴看得见的农民质心精确咬一堆。安全交给残血 Jump + 风筝。

（另：大舰"传送/折跃回基地"语音目前会走回去而非 Tactical Jump —— 需新增"对点施放 ability"路径，
单独实现，见下个提交。）

### 2026-06-20 bc_rush 早期再调：14 房 + 农民 24/优先 + VF 优先于枪兵 + 首舰前只 4 枪兵

**修正 (Fixed)** — 用户真机连续反馈：

- **第一个补给楼卡在 supply 14**（用户：14 农民才下第一个房子，否则提前花 100 矿停农民）：
  `bc_depot_target` 在 `supply_used<14` 返 0（BcAutoDepot 不早建），plan 里第一个 depot 加
  `Step(Supply(14), …)`。SCV 从 12 平滑爬到 24 不再被早 depot 卡停。
- **农民补满 24 且 priority=True**（用户：农民优先级 > VF；没满采别停农民）：1-base SCV 上限
  22→24（16 采矿 + 6 采气 + 2 建造/在途缓冲 → 实采全程 ≥16），priority 让农民排在 VF 前预留矿、
  不被卡停。
- **VF（重工）优先于枪兵**（用户：VF 之前出了好几个枪兵，不对）：首舰前枪兵只出 **4** 个保命，
  首舰出来后才放开枪兵海（`Step(BATTLECRUISER≥1, MARINE 90)`）。VF/核心链/BC 都 priority，
  绝不被枪兵抢矿。（实测首舰前枪兵 4 个、不再是 10 来个。）
- attack_moveout 放宽到 8:30（本 build 进攻靠 BC 持续骚扰、首舰前少枪兵 → 主力大军成军较晚）。

验证：build_acceptance veryeasy 科技链 timing 全 PASS（Factory 184s/Starport 224s/FusionCore 271s/
首舰 ~337s）；telemetry 实测 depot ~45s ready、SCV 平滑到 24、实采 16-17 + 采气 6、首舰前枪兵 4。

### 2026-06-20 BC 骚扰到矿区后主动贴农民 + 风筝（不坐死在几何锚点）

**修正 (Fixed)** — 用户：大舰到主矿后面那点还是离矿有点远、只打到 1 个农民；应主动去找更近的农民、
来回移动，有兵来了能风筝。

- `_raid_move_point` 到矿区后（≤射程）不再只绕几何锚点扫，而是：
  ① **主动贴农民**：harass 中心优先取**看得见的敌方农民质心**（_WORKER_SEEK_RADIUS=11 内），贴上去
     打、能打到更多农民；看不见农民才用几何锚点。
  ② **风筝**：附近有能打空军的敌方机动单位（_KITE_THREAT_RADIUS=10）→ harass 中心朝**远离它**偏
     _KITE_BACK，边打农民边躲。
  ③ **不停**：仍绕该中心沿矿线轴线小幅来回扫（大舰不能停）。
- test_bc_raid_act 加农民质心/风筝/贴农民断言。

验证：bc_harass_selftest PASS（BC 到矿区 dist 7.1）；全量 3196 passed。

### 2026-06-20 BC 骚扰矿后锚点改"覆盖算法"（二矿宽矿也打得到农民）

**修正 (Fixed)** — 用户：harass 矿后点不能无脑偏固定值，要算该点到各水晶矿的距离、保证打得到附近
所有矿的农民。主矿能打到、二矿(矿点铺得宽)就偏出射程打不到了。

- `_harass_geom` 从"质心朝外偏固定 2"改成**覆盖算法**：取该矿**每个水晶矿点的实际坐标**，从质心朝
  矿后逐步偏移（0→3，步长 1），选**到全部矿点距离都 ≤ 5（在 BC 射程内）**的**最靠后**那个点——
  既尽量躲矿后(安全)，又保证射程内打得到所有矿的农民。矿点铺太宽(质心都覆盖不全)→ 落回覆盖最多
  的点。几何无关，主矿/二矿/三矿都按各自矿点分布算。
- test_bc_raid_act 加覆盖断言（锚点到全部矿点 ≤ 射程、在矿后侧、确定性、宽矿不偏出射程）。

### 2026-06-19 bc_rush 早期经济再修：农民补满 22(16 矿+6 气) + 两口气背靠背开

**修正 (Fixed)** — 用户真机反馈：闲置农民 / 采矿没满 16 / 第二口气慢 / 兵营好了重工不第一时间下。

- **农民补满 22**：1-base SCV 上限 20 → **22**（主矿矿物 ideal 16 + 两口气 6）。原来停在 20、又有
  6 个采气 → 采矿只 ~14，永远到不了 16。22 不过饱和（防闲置农民）。
- **两口气背靠背开**：gas2 原 gate 在"兵营 exists"(~98s) → 实测 gas2 拖到 114s。改成早期 sequential
  里 `BuildGas(2)`，rax 一好就 gas1→立刻 gas2（实测 gas2 80s，提前 ~34s），气第一时间满。
- **重工第一时间下**：Factory 本就 priority=True；二口气提前 → 不再因缺气卡住 → 兵营 ready 后
  及时下重工（factory 184s PASS）。
- 闲置农民：上限设为正好 ideal(22) 不过饱和，idle ~0。

验证：build_acceptance veryeasy 7/7（农民 22、gas 6/6 满采且 gas2 80s、科技链 5/5 PASS）。

### 2026-06-19 修 BattleCruiser 别名：去掉"大件"(输入法误打)、加"大和舰"

**修正 (Fixed)** — 用户："大件"是输入法把"大舰"打错，不是真别名。BattleCruiser 别名去掉 `大件`、
加 `大和舰`（现为 战巡/战列巡洋舰/船长/大舰/大和/大和舰/航母/BC）。同步把自动骚扰工厂指令面板
显示、LLM few-shot、USER_GUIDE 里的"大件"全改成"大舰"并重 dump prompt。

### 2026-06-19 bc_rush 气优先满采 + 前 3 大件优先 + 大件矿后来回打农民 (#562)

**修正 (Fixed)** — 用户真机反馈四点：

- **气优先满采**：bc_rush 最缺气，但默认 `DistributeWorkers` 公式 `(free_workers-8)/2` 早期只放
  ~4 个 SCV 采气（2 口气 ideal 6）→ 气严重不足。改 `DistributeWorkers(min_gas=6)` 强制主矿两口气
  **满采**（telemetry 实测从 4/6 → 6/6）。
- **前 3 大件绝对优先、二矿/兵营推后**：原来出首舰后就狂补农民 + 二矿太早。现在 Expand(2) + 额外
  Barracks(4) + gas4 全 gate 到 `BATTLECRUISER>=3`（核心三建筑 priority=True 预留资源、BC
  priority=True 提前留够下一发的钱）→ 前 3 大件连续出、有余钱才扩张。SCV ramp 到 44 本就 gate 在
  CC2，自动随之延后（治"狂补农民"）。
- **大件在矿后来回移动**（原来到矿后坐死不动）：到矿后后**沿矿线轴线左右来回扫**（`sin` 振荡，
  幅度小到全程不出农民射程），大件不停。
- **矿后位置按实际矿点重算**（原来二矿"矿后"`behind_mineral_position_center` 离矿太远打不到）：
  锚点改成 `zone.mineral_fields.center`（实际矿点质心）朝远离基地偏 2 = 矿后但在采矿农民射程内，
  几何无关，主矿/二矿/三矿都打得到。

验证：build_acceptance veryeasy 7/7（gas 6/6 满采、Factory 168s/Starport 207s/FusionCore 256s/
首舰 ~300s）；bc_harass_selftest PASS（60 次巡逻轮换、BC 到矿后 dist 7.0 在射程内）。
build_acceptance spec 同步：移除 command_center_2 固定 timing check（二矿现在条件性后扩，非开局
固定步骤）、economy_profile 改 1-base。

### 2026-06-19 bc_rush 核心科技链绝对优先：二矿/兵营不再抢矿耽误大件 (#561)

**修正 (Fixed)** — 用户：核心链 兵营→重工→机场→聚变芯(VC) + 机场科技挂件 → 第一时间出大件，
这条路不能被任何事耽误；之前 VC 没好就先下二矿 + 一堆兵营，抢矿把核心链饿死。

- **核心三建筑 priority=True**：Factory / Starport / FusionCore 改 `priority=True` —— sharpy 会
  为它们**预留资源**，非 priority 的二矿/兵营/出兵再也抢不走它们的矿和气。Starport TechLab
  (BuildAddon) 本就无条件预留。
- **二矿 + 额外兵营 + gas4 推到 FusionCore ready 之后**：原来 CC2 gate=`Factory存在+矿350`
  (snapshot 350 会在核心建筑正要花矿那刻抢走)、额外兵营 gate=`Starport存在`(VC 前就出"一堆兵营")
  —— 全改成 `UnitReady(FusionCore)` 门控，核心链完成后才扩张/加产能。
- 验证(build_acceptance)：veryeasy 8/8 + veryhard 8/8 全胜；Factory 152s / Starport 192s /
  FusionCore 240s / 首舰 ~300s，科技链 timing 6/6 + 17/18 PASS（priority 后核心链更快了）。

### 2026-06-19 BC 骚扰重构：每艘新大件自动骚扰卡 + 贴边打农民 + 脱离全军单退 (#561)

**新增 (Added)**

- **每艘新大件自动获得骚扰卡**：bc_rush 开局自动建一条「自动骚扰」持续指令（面板可见、可 ❌）。
  它为每一艘新出的 BC 提交一张独立的 per-BC 骚扰卡。玩家 ❌ 工厂卡 → 停止给新 BC 建卡；
  ❌ 单张 per-BC 卡 → 那艘大件**归队主力大军**（和枪兵抱团）。
- **语音派大件骚扰指定矿区**：「派一个大件去骚扰对方二矿农民」/「派两个大件骚扰主矿」→ 建 N 张
  骚扰卡，矿区可指定主矿/二矿/三矿（enemy_main/natural/third），没指明 → 自动轮换找有农民的敌矿。
  「大件」已加入 BattleCruiser 别名。

**变更 (Changed)** / **修正 (Fixed)** — 用户反馈两个 bug：

- **贴边到"矿后"+ 各矿区之间来回巡逻骚扰农民**（原来跑到敌基地旁跟敌军拉扯打不到农民、且原地
  绕圈位置不对）：骚扰目标改成敌方矿区的**矿后**（`behind_mineral_position_center`，能打采矿农民
  又躲矿框/远离基地，确定性按距敌 start 排序索引），用 DT/棱镜空投同款 `plan_drop_path` safe-move
  **直接迁到矿区不绕圈**；到矿后坐下打农民、待够/农民清零就**巡逻到下一个矿**（主→二→三→主…
  来回），不原地绕圈、不乱跳。血低才传送回家。
- **后期大件不再单退太多**：重构成「`BcRaidSquadAct` 只控**持卡** BC」——未持卡的 BC 不再被强控，
  自动落到主力大军 PlanZoneAttack 和枪兵一起打，不再「掉点血就单独后退、不抱团」。
- **持卡 BC 不受全军 intent 影响**（CLAUDE.md 控制权规则 2）：`combat_intent_override` 平时常被
  bot 默认置 "defend"，旧逻辑据此喊停会让持卡 BC 整局压在家永不骚扰（#561 真局自验抓到）。
  现持卡 BC 完全不读它，要停 → ❌ 卡。
- doctrine 切换后骚扰不断档：`persistent_skyterran`(BcLate) 也加 `BcRaidSquadAct`（工厂指令在
  director 层跨 plan 存活，切 doctrine 后仍有 act 驱动持卡 BC）。

**实现**：新 `DirectiveType.BC_AUTO_HARASS` + `BcAutoHarassPayload`；director `_tick_bc_auto_harass`
（工厂建卡 + 死亡孤儿 revoke）+ 每 tick 发布 `knowledge.vibecraft.bc_harass_claims` map；act
per-tag 目标状态。独立 opus 评审通过（核实 reserve 不抖/内部卡不被拦/release 真机有/坐标不漂移）。
单测 test_bc_auto_harass(19) + test_bc_raid_act(9)；真局自验 `scripts/bc_harass_selftest.py`
（终态：BC 真飞到敌矿农民线 dist 1.5）；全量 3193 passed。

### 2026-06-19 修"打到一半异常退出" + 单帧异常全兜底（doctrine 占位 enum 崩整局）

**修正 (Fixed)** — 用户："刚才这把打到一半，不知道为什么异常退出了。"

- **根因**：bc_rush 开局完成 auto-switch 到 persistent_skyterran(BcLate) doctrine 后，`bc_late.py:69`
  的 `TerranUnit(UnitTypeId.VIKING, 4)` 训练**不可训练占位 enum** VIKING(id 1940,
  `creation_ability=None`)，sharpy `act_unit.py:131 calculate_ability_cost(None)` 抛 AssertionError
  冒泡到 `sc2.main:run_match` **杀整局**（~11:50 崩）。同 #534 但发生在 doctrine plan（绕过 Director
  归一层）。修：`VIKING → VIKINGFIGHTER`。
- **顶层兜底（用户强要求"所有异常都catch写log"）**：`common_bot.on_step` 现在整体包 try/except +
  `super().on_step()`（sharpy plan 执行）单独再包一层，**任何单帧异常都 catch + `logger.exception`
  落完整 traceback 到 game log**，游戏继续跑、事后靠日志定位根因——单帧出错只丢这帧、下帧重试，
  再不会"打到一半异常退出"。

**变更 (Changed)** — 防回归：

- `test_terran_plans_construct.py` 补 4 个 doctrine plan（bc_late/liberator/mech/bio_max）进构造测
  （之前只测 opening，漏了 auto-switch 进来的 doctrine）。
- 新增**静态占位 enum 审计** `test_terran_plan_no_placeholder_train_unit`：走 plan 树揪出 unit_type
  落在占位名（与 `Director._UNIT_NAME_MAP` 同源）上的 ActUnit/TerranUnit，单测阶段拦死运行时崩。
- 新增 `test_on_step_swallows_exceptions`：`_on_step_body` 抛异常时 `on_step` 必须吞掉不冒泡。

### 2026-06-19 通用维修指令 + build 目标校验（"农民修理大舰"不再误判成"建大舰" #551/#558）

**新增 (Added)** / **修正 (Fixed)** — 用户:"农民修理大舰"被误判成"农民建造大舰"还建卡成功。

- **#551 通用维修指令**：新增 `DirectiveType.REPAIR` + `RepairPayload(selector + worker_count)`。
  "修理/维修/修一下 XX" → repair（不是 build）。持续型：`_tick_repair_orders` 每帧对每个目标维持
  ≥worker_count 个 SCV 修理，目标满血/消失移除，全好置 done。新 facade `ensure_repair(tag, count)` +
  `get_unit_health_percentage`（三同步 + audit）。
- **#558 build 目标校验**：`_reject_if_invalid_structure_type`（紧跟 #523 跨族校验）——
  build_at / structure_override 的 structure_type **必须是本族真实建筑**；是单位（如大舰）→ 拒绝建卡，
  提示"不是农民能建造的建筑，你是想维修吗？"。建筑集合走 `aliases.group_of` 统一判定（覆盖三族，非硬编码）。
- LLM prompt 加维修规则 + 例子（派 N 农民修大舰 / 修地堡）+ 重 dump。test_repair_directive(25) +
  test_build_structure_validation(11)；86 单测全绿。

### 2026-06-19 bc_rush 地堡推到 FusionCore 后建（科技链优先）+ 一条反直觉教训 (#556)

**变更 (Changed)**

- 地堡 gate 从"兵营 ready(~1:30)"推到 **FusionCore 修好之后**（用户：科技链优先，别让地堡的 100 矿
  拖慢核心解锁链）；早期防守靠枪兵 + Cyclone。
- **教训（反直觉，已写 pitfalls）**：曾把早期枪兵 cap 砍到 4 想"省矿给科技"，真局反而把首舰从
  ~5:30 拖到 ~7:50（vs veryhard：枪兵太少→经济被骚扰/打掉→喂科技的钱反而更少）。**枪兵保护
  经济、经济喂科技**——砍枪兵 = 砍科技速度（只有 vs 被动 AI 才省矿）。已回退枪兵 cap=90。
  科技**建筑**本身完成很快（factory ~167s / starport ~206s / fusion ~271s）。

### 2026-06-19 BC 传送回家落点改到矿和基地之间 + ≥3 农民修理 (#557)

**变更 (Changed)**

- BC Tactical Jump 回家落点从"矿框后"改成**矿和基地之间**（`center_location.towards(behind_mineral, 4)`，
  工人采矿路径上），农民够得着、好修（2026-06-19 用户）。
- BC 回血 hold 时**保证至少 3 个 SCV 修这艘 BC**（`_ensure_repair`，已在修的不重发、不够补最近空闲
  SCV；按残血 BC 艘数 scale）—— 修得快、早回战场。与 tactics Repair() 叠加。

### 2026-06-19 地堡回收占用先卸兵 + 进兵/放兵语音指令 (#556 第一批)

**新增 (Added)** / **修正 (Fixed)**

- **回收占用地堡先卸兵再回收**（用户重复反馈，SC2 拒绝回收带兵地堡）：SALVAGE 分支检测地堡
  `has_cargo` → 先 `UNLOADALL_BUNKER` + 入 `_pending_salvage_tags`，`_tick_pending_salvage` 每帧
  检查、乘员清空后才发 `SALVAGEEFFECT_SALVAGE`；建筑消失则移除。
- **两个语音指令**：新增 `DirectiveType.BUNKER_CARGO`（`action: load|unload` + selector + count）。
  "往地堡塞兵/进兵" → load（找最近 Marine SMART 进堡）；"把地堡的兵放出来/卸载地堡" → unload
  （UNLOADALL_BUNKER）。LLM prompt rules L5b + few_shot 例 47j + 重 dump。
- 新 facade `bunker_has_cargo(tag)` + `load_bunker(tag, count)`（Protocol+FakeFacade+_SharpyFacadeBase
  三同步 + audit）。test_salvage_directive 加占用回收状态机测 + test_bunker_cargo.py；61 单测全绿。

### 2026-06-19 bc_rush 地堡改建在主基斜坡口高地边缘 (#556 第一批)

**修正 (Fixed)**

- bc_rush 地堡之前用 `GridBuilding` 落在基地网格里（不防口，用户两次反馈不对）。改成新 `RampBunkerAct`：
  落点 = `main_base_ramp.barracks_in_middle`（人族墙体中点，**紧贴高地斜坡边缘卡口**），一次
  `find_placement` 锁定不每帧漂。真局验证：placement=(131.5,104.5) 与 ramp_mid 完全吻合、贴着
  ramp top(132.5,102.5)，t=138 建起。

### 2026-06-19 BC 骚扰微操重写：纯 move 卡射程边缘 + AoE 闪避 + 早跳 + 贴边 (#557)

**变更 (Changed)** —— `BcRaidSquadAct` 按用户实测反馈重写（BC 之前正面交火、被砸/集火死、原地打）：

- **纯 move 卡射程边缘游走，零 attack/attack-move**：BC 是 move-shot 单位，移动中自动攻击射程内目标。
  改成只下 `bc.move()`，卡在 BC 射程边缘(~5.5)绕圈缓慢游走 → 一直移动不停、不贴脸、不正面对刚、
  发挥移动攻击优势。**经 weapon_cooldown 真机探针证实纯 move 下 BC 确实开火**（纠正了"move 不自动
  攻击"的错误先验——BC 是例外）。
- **砸地 AoE 闪避**（威胁模型补漏）：破坏者酸液/风暴/核弹/潜伏者/解放者/盲目云进 `ai.state.effects`
  危险集合，BC 在落点范围内立即向反方向挪开（真局证实检出 RAVAGERCORROSIVEBILECP 并闪避）。
- **Tactical Jump 回闪更早**（防集火死）：safety 4→6.5、`target_in_range` bonus 1→3.5（把即将进
  射程的刺蛇也算进来）、新增爆发护卫（一帧掉血 >18% 满血立即跳）。
- **贴边接近**：复用 `plan_drop_path`（DT/棱镜空投同款避敌寻路）绕开敌方基地飞到矿框后，不走正面；
  当前矿有 ≥2 非农民战斗单位 → 立即换矿（不受切换冷却抑制）。
- 验证：`bc_rush_selftest` PASS（flyout/jump_home/jump_burst/healing_hold/regroup/dodge 全触发）。
  enemy_army_near 换矿 + 强对手生存留玩家真机测。

### 2026-06-19 bc_rush 加碉堡兜底（早期防 all-in，#549）

**新增 (Added)**

- bc_rush 早期建 1 座碉堡（Bunker）+ `ManTheBunkers` 自动塞 4 枪兵进去，做**1 基地速大件的
  防 all-in 兜底**（用户批准）。碉堡 100 矿 / 0 气 / 0 人口 —— 不碰 BC 的气、不卡人口、不延后
  首舰节奏（只 ~10s tech 微延）。gate 兵营 ready 即建（~1:30 → 实测 t=136/2:16 建好，赶在
  veryhard all-in 窗口 ~4:40 前）。
- **验证**：veryhard×3 碉堡均 t=136 建起；游戏存活 14-17 分钟（先前 1 基地裸科技常 ~5min 被
  all-in 打死）；BC timing / supply / banking 仍 PASS（fusion 288s 仍在窗口）。实际防御价值
  留玩家真机测。

**修正 (Fixed)** — salvage directive 单测全绿、`salvaged=1` 也打了，但真局里地堡**根本没被拆掉**。
真局自验（建真地堡 + 注入"拆地堡" + 看 telemetry BUNKER 计数）揪出两个单测照不到的真机 bug：

- **idle 单位的 ability 被静默丢弃**：python-sc2 `prevent_double_actions` 在 `unit.orders==[]`（如刚建好
  闲置的地堡）时 fall through 到隐式 `return None`，被默认 `prevent_double=True` 的 `filter()` 丢掉
  → `bot.do(unit(ability))` 永远发不到 SC2。修法：`cast_unit_ability` 把 `UnitCommand` 收进 bypass 队列，
  在 `super().on_step()` 之后用 `_do_actions(..., prevent_double=False)` 直发，绕开该 filter。
- **salvage ability enum 用错**：地堡实际可用的回收 ability 是通用的 **`SALVAGEEFFECT_SALVAGE`**，不是
  望文生义的 `SALVAGEBUNKER_SALVAGE`（后者真机返回 `ActionResult.NotSupported`）。靠
  `get_available_abilities` 查真机才发现。地堡 + 感应塔都改走 `SALVAGEEFFECT_SALVAGE`。
- **验证**：`scripts/salvage_selftest.py`（真局 structure_override 建真地堡 → salvage → telemetry
  BUNKER 1→0）PASS（地堡 t=180 建起、t=252 前消失）。

### 2026-06-19 F1 镜头框选 selector（near_camera 字段 + _inject_camera_selectors）

**新增 (Added)**

- `Selector.near_camera: bool = False`（`directives/scope.py`）：LLM 填 True 表示
  "只选下达那刻镜头视口矩形框 ±12×±9 格内、匹配条件的单位/建筑"。守卫：`near_camera=True`
  必须同时有 `unit_type` 或 `role`，否则 ValidationError（防裸框选语义模糊）。
- `Sc2Facade.all_own_unit_tags(include_workers: bool = True) -> list[int]`（Protocol +
  FakeFacade + `_SharpyFacadeBase` 三处同步）：返回所有己方单位 tag 列表（不含建筑）；
  `include_workers=False` 排除 Probe/SCV/Drone，供 role=ARMY 路径使用。
- `Director._inject_camera_selectors`（`bot/director.py`）：在 `_inject_camera_point`
  调用后紧随触发。对所有带 `Selector` 的 payload（UnitClaim / Move / Scout / UnitRelease /
  GroupAssign / ProductionOverride.building_selector）遍历 selector，若 `near_camera=True`
  则一次固化：按 `unit_type`（走 `resolve_selector`）或 `role`（走 `all_own_unit_tags`）
  先取候选，再 `filter_tags_in_box` 做 ±12×±9 盒过滤，结果写回 `selector.tags`，
  清 `near_camera=False`。camera_point=None 时 tags=[] + warning，不崩。

**测试 (Tests)**

- `tests/unit/test_camera_select.py`（新建，15 条）：守卫拒绝裸 near_camera；
  unit_type 路径框内/框外/建筑 stub；role=ARMY 排农民；IDLE 含农民；count 截断；
  camera_point=None；非 near_camera selector 不被修改。
- Protocol audit（`test_facade_release_unit_role.py::test_sharpy_facade_implements_all_protocol_methods`）
  通过：`all_own_unit_tags` 三处同步无遗漏。

### 2026-06-19 bc_rush 不卡人口 + 二气提前 + 自动资源级联 (#549 调优)

**修正 (Fixed)**

- **绝不卡人口（用户强规则）**：新增 bc_rush 专用 `BcAutoDepot`（`bc_rush.py`，纯算术抽到
  `terran/bc_supply.py::bc_depot_target`）。sharpy 共享 `AutoDepot` 用平滑增速率预测补给、
  只留 ~3-4 人口冗余，扛不住 BC 离散 +6 爆发 + 中期多兵营枪兵 ramp（实测 5:24 卡 47/47）。
  `BcAutoDepot` 在父类预测之上叠加随产能放大的 buffer = `8 + 2×(兵营含反应堆+工厂+星港)`，
  取 max。**中期卡人口完全消除**，残留仅开局 15/15 首楼那 ~9s（所有 build 共有的物理下限）。
- **二气下太晚（用户实测）**：二气 gate 从"兵营 ready(~1:30)"改成"兵营 exists(~0:50)"，
  二气紧跟一气（实测 gas1@71s → gas2@105s），保证大件第一时间出（气是 BC 瓶颈）。
- **空军升级 KeyError 导致整个 build 静默瘫痪**：`TERRANVEHICLEANDSHIPWEAPONSLEVEL1` 不存在于
  `UPGRADE_RESEARCHED_FROM`（SC2 武器分车/空两条，没有合并版）→ `Tech(...)` 构造即 KeyError →
  `create_plan()` 抛异常 → bot 什么都不造。改用真实存在的 `TERRANSHIPWEAPONSLEVEL1`（战巡武器）。
  回归守卫：`test_terran_plans_construct.py` 已覆盖 bc_rush（动 plan 后必须跑它，不能只跑 catalog 测）。

**变更 (Changed)** — 无玩家干预时的自动资源级联（2026-06-19 用户拍板，按优先级花光资源）：

- **气**：① BC（priority 先吃）→ ② 气余了再开第二个星港 + TechLab（双星港出 BC，gate=首舰+气≥150）
  → ③ 气还发不出去 → 军火库 + 战巡空军攻防升级（最后兜底，gate=首舰+气≥300，门槛高于第二星港
  使气先喂星港）。**矿**：① BC → ② 有多的矿就下二矿（gate=Factory存在+矿≥350，或首舰兜底）→
  ③ 钱多了加兵营到 4 出枪兵海（gate 提前到星港存在，早 ramp 烧余矿）→ ④ 潜力→兴奋剂/盾牌/步兵攻防。
- 效果：banking（avg_excess_bank）855→535(veryeasy)/623(veryhard)；6 维自检 ①农民闲置 ④卡人口
  ⑤科技链 全 OK。acceptance `command_center_2` 校验时点从 2:30 改 4:30±90（对齐"先出大件再开矿"设计，
  非放宽掩盖——CC2 本就该晚）。veryeasy 8/8 PASS。
- **已知**：1 基地速大件 vs veryhard 早 all-in 较脆（3 局 2 局首舰前被打死，均已到 FusionCore），
  属该 build 风险画像，待用户定是否加早期防守（碉堡/更多枪兵）。

### 2026-06-18 人族「二本速战巡 + BC 骚扰小队」build (#549)

**新增 (Added)**

- `src/vibecraft/bot/auto_combat/terran/plans/bc_rush.py`：`BcRush` opening_build —— 二本速战巡。
  build 链：depot→rax→gas1→快扩 CC2(~1:50)→Factory→gas2→Starport→FusionCore(~3:25)+TechLab→
  首舰 ~4:10-4:40→持续 BC。兵营 Reactor 枪兵 + Cyclone 临时对空早期防守；不研 Yamato。
  lategame_transitions 接 persistent_skyterran。
- `src/vibecraft/bot/auto_combat/terran/bc_raid_act.py`：`BcRaidSquadAct(ActBase)` —— BC 骚扰小队。
  仿 PhoenixSquadAct reserve 范式：每帧把非回血中的 BC reserved 编入骚扰小队；BC **飞**(不 jump)向
  敌矿横跳骚扰农民/建筑；**残血自适应 jump 回家**——`jump_hp=clamp(当前受击对空 DPS×safety,
  9%×550 保底, 550)`(火力越猛越早跳、没人打耗到 9%);回血门(hp≥95%,只看血量不等 jump CD)满血
  立马再飞出，无限循环。目标一次锁定，切换:农民清零 / 停留超时25s + 切换冷却防抽搐。
  轻量喊停(Step A)：玩家下全军命令(`combat_intent_override`)→ release 全部 BC 归队(可视"停止骚扰"卡 = Step B)。
  env trace `VIBECRAFT_BCRAID_TRACE`(flyout/jump_home/healing_hold/target_switch/regroup)。
- `strategies/terran/bc_rush.yaml`：bc_rush catalog 注册，aliases 含"速战巡/大舰快攻/BC rush/速大和"等。
- `scripts/bc_rush_selftest.py` + `tests/build_acceptance/bc_rush.yaml`：真局自验。
  **验证**:build_acceptance 7/8(首舰/聚变芯/科技实验室/二矿 timing PASS);selftest 端到端 PASS
  (flyout + 残血 jump_home + healing_hold)。

**修正 (Fixed)** — bc_rush.yaml 两处 schema 坑(构造单测测不出、真局 catalog 校验直接崩):
误带 `gas_intensity`(OpeningBuild 不允许该字段)、`steps` 用了非法 verb(`orbital`/`expand`)+
两词 obj + 括号注释 → 都校正为合法 BuildStep。`test_terran_strategies` 开局数 9→10 + 加 bc_rush id 断言。

### 2026-06-18 战巡/亚马托民间叫法别名 + 修聚变芯错 hotkey

**新增 (Added)** / **修正 (Fixed)**

- 战巡(Battlecruiser)补民间叫法别名:**大舰 / 大和 / 航母 / BC**(用户)。"航母"在人族语境=战巡,
  神族语境=Carrier,按当前 `--my-race` 别名表消歧。
- 新增亚马托炮(Weapon Refit)别名:**大和炮 / 亚马托 / 亚马托炮 / 武器改装 / Yamato**;
  `director._UPGRADE_NAME_MAP` 加 `YAMATO/WEAPONREFIT → BATTLECRUISERENABLESPECIALIZATIONS`
  (真实 UpgradeId,`YAMATOCANNON` 是空壳会 KeyError)。
- **修历史遗留错 hotkey**:聚变芯(FusionCore)别名原 default_display="BC" + alias "BC" —— 与
  BC=战巡 冲突,且 CLAUDE.md 真值表聚变芯 hotkey 是 **VC**(V+C)。改成 VC + 删 "BC" alias
  (让给战巡)。`test_terran_aliases` 对应改:BC→Battlecruiser、新增 VC→FusionCore。重 dump LLM prompt。

### 2026-06-18 ghost_nuke 核弹微操落地 + 补 Armory(#547)

**新增 (Added)**

ghost_nuke 招牌"核弹骚扰"原在代码里无执行器(只研隐身出幽灵),实质退化成 bio_max+几个幽灵。
新增 `AutoNukeAct`(`terran/nuke_act.py`)真正落地核弹流(用户 2026-06-18:做核弹、不与 bio_max
合并),接进 ghost_nuke 的 tactics(PlanZoneAttack 之前)。状态机 IDLE→MOVING→ARMING→COOLDOWN:
- 维持核弹库存(GhostAcademy BUILD_NUKE);
- 目标优先**敌方建筑**(不会跑,14s 必中,选离我方幽灵最近的)、次选静止兵团簇;
- reserve 1 只幽灵每帧重设(照 phoenix_squad 模式)潜入(隐身)→ 到射程 calldown → 发射即撤;
- 友伤半径 9 规避、MOVING/ARMING 看门狗、玩家"全军撤退"即放手召回、建筑目标不受 detector 中止。
经独立 opus 评审(4 必改全采纳)+ 真局 selftest(`scripts/nuke_selftest.py`,经 reaper_expand →
auto_switch persistent_ghost_nuke 进 doctrine)验证 build_nuke + calldown 端到端 PASS。

**修正 (Fixed)**
- ghost_nuke 补 `GridBuilding(ARMORY)`(#548 审计发现漏建):步兵 +2/+3 升级原门控 Armory 但
  从没建 → 永远卡 +1。补上(三矿后建),攻防可上 3/3,面板 phase 副标题同步恢复。

### 2026-06-18 build YAML 对齐实现 + 宏观策略面板 phase 进展校正(#546/#548)

**修正 (Fixed)**

build 调研审计发现多个 build 的 YAML 声明 / PWA 宏观策略面板 phase stepper 与 plan.py 实际
行为不符(声明误导玩家/LLM)。逐个对照代码校正(只改 yaml 声明侧,plan.py 是真理源):

- **声明侧(#546，5 build)**:mech(兵种/建筑数同步实现:坦克 14/雷神 12/恶火 12/维京 8/工厂 5、补
  车辆攻防 3 升级)、macro_hatch(Ravager 注明由 sustain 产、opening 不 morph)、roach_allin /
  roach_ravager(删 Glial 研究步——一本/timing 不升 Lair 故意不研)、one_one_one(注明"拉农修地堡
  前压"未实现、当前骨架版)。
- **面板 phase 进展(#548，~19 build)**:phase stepper 的 display/subtitle/start_at_time 与 plan.py
  对齐。重点:**bio_stim** 三矿挪到"攒兵后~6:40"(不再标在开局阶段)、stim ~4:20、出门 6-8min、
  summary + attack_window 同步;另修 banshee_harass/bc_late/ghost_nuke/hellion_expand/marine_rush/
  mech/two_base_tanks/two_one_one + brood_corruptor/ling_bane/lurker_hydra/macro_hatch/
  muta_ling_bane/mutalisk_harass/nydus/roach_allin/roach_hydra_viper/roach_ravager 的过时阶段
  标签/时机(删除 plan 里不存在的单位/科技/建筑描述、按 build_acceptance spec 校准时机)。

**待跟进(代码侧,本次只改 yaml 未动 .py)**:① ghost_nuke plan.py 疑漏 `GridBuilding(ARMORY)`→
步兵 +2/+3 升级门永不满足(将随核弹微操一并修);② roach_hydra 出门时机 plan 设计值与真实表现有
偏差,phase 数字暂保守。

### 2026-06-18 人族 bio_stim(3矿5BB) 产能效率大修(#537)

**修正 (Fixed)**

用户报"3矿5bb 开局开矿过激进、没几个兵就开矿"。telemetry 实证(VeryHard 3 run 一致)挖出
更深的产能/科技级联病:出门那刻只有 1 兵营、5 枪兵、气 flood 840;TechLab 拖到 580s、stim 到
749s(9.6min!)→ 3-9min 全程无 stim/掠夺者的纯枪兵 → 2/3 局被中期(~6min)碾死。改了 plan 编排
(`bio_stim.py`),根因逐个修掉,build_acceptance 从 **5/14 → 13-14/14**(VeryEasy/VeryHard):

- **TechLab 永不挂 → stim 749s→247s**:`BuildAddon` 只在 `.ready.idle` 兵营挂挂件
  (`vendor/.../build_addon.py:46`),而 Marine(priority) 排在前面每帧把所有兵营塞满枪兵 →
  兵营永不空闲 → 挂不上。把 2 个 BB-TechLab 挂件**前置到 Marine 产线前**,兵营产完一发空闲
  那帧 BuildAddon 先抢到手挂上。
- **掠夺者/医疗船恒 0 + 气 flood 2000 → 掠夺者 8-15、医疗船 6-7**:同理 Marine 在前把
  TechLab 兵营也塞满枪兵 → 掠夺者抢不到档期、气无出口。把**全部产兵(掠夺者/医疗船/枪兵)
  下移到所有建筑步之后**(建筑先吃矿成型,再产兵;附带让 TechLab/Reactor 挂件在建筑期天然
  空闲的兵营上挂上),掠夺者/医疗船排在枪兵前抢专属建筑档期。
- **早期矿荒(1 兵营卡到 312s)→ 5 兵营 ~310s 稳定**:原 BB1-ready 同帧并发 Expand2(400矿)+
  Factory+gas2+gas3 把 BB2/TechLab 的矿抢光。gas3 推到 Factory-exists(3 口气喂枪兵海全程
  flood、延后让 SCV 采矿);并把 **SCV 第 2 波(爬 44 农)推迟到 3 兵营齐才放**——原来 CC2 一好
  (~190s)就爆农,SCV 产线排在兵营前把矿抽走 → 兵营卡 1 个;改成 22 农先把核心 3 兵营拉起来
  再爆农,兵营是产能命脉优先于第 2 波农民。
- **Starport 422s→243s、+1 攻击 487s→438s**:二者原排在 BB3-5/三矿后抢不到 SCV/矿。Starport
  紧跟 Factory(air-tech 路径)、工程湾改 Factory-exists 触发并提到 BB4/5 前。
- **二矿早开 + 三矿"有军队再开"(用户 2026-06-18 纠偏)**:用户指出"二矿可以早点,三矿不宜
  太早,二矿延后不是我的需求"。Expand2 回到 BB1-ready(CC2 ~3:10,command_center_2 达标);
  Expand3 改成 `5 兵营齐 + combat supply ≥ 6` 才开、且放高优先级建筑块保证 gate 一开就建,
  CC3 ~6:30-6:55 落地(故意比老 5:30 晚——"别没几个兵就开三矿"=#537 投诉本意)。用户三选一
  拍板"折中"(攒一小股兵)。`command_center_3` spec 的 `at` 同步后移到 6:40(测量这个**故意更晚**
  的军队优先三矿,非放宽掩盖)。

净效果:stim ~4:20 出、完整 MMM(枪兵+掠夺者+医疗船)、5 兵营 2 科技 3 双倍齐全、攻防 +1、
二矿 ~3:10 / 三矿 ~6:40(有军队),原"2/3 局 6min 暴毙"消失,survive 到 9min+ 带 60 农民。
build_acceptance **5/14 → 13-14/14**(VeryEasy/VeryHard;剩余偶发未过为 marine_24/pressure_reach
的 VeryHard 中后期战损噪声,非结构问题)。

### 2026-06-18 P1 人族产能挂件决策:弹窗 + 推荐算法

**新增 (Added)**

用户:人族"补 4bb"不挂附件 —— 造完孤楼挂件无决策。P1 引入玩家 voice 指令路径的挂件决策:
- **`StructureItem.addon_decided: bool = False`**:区分"玩家已决定挂法(给 mix 或说不挂)"vs
  "没说挂法→触发弹窗"。LLM 在玩家提到挂件(组合表达或"不挂")时置 True。
- **`Director._recommend_addon_mix(building_type, count)`**:推荐 `(techlab_n, reactor_n)` 分配。
  用 SC2 `UNIT_TRAINED_FROM` + `_unit_requires_techlab` 统计该楼 requires-techlab 兵种数(clamp
  到 `[≥1 if 任何需求 else 0, count]`);有 mass-mineral 兵(枪兵/医疗船/维京等)且余量→ reactor≥1。
  **减去场上已有同类 TechLab 挂件(增量推荐)**:已有 techlab 已满足需科技兵种 → 新楼只补差额
  (已有≥需求 → 新楼全挂 reactor),避免在已有科技的基础上重复多挂科技。
- **`Director._maybe_build_addon_confirm(directives)`**:仿 townhall confirm 弹窗模板,
  遍历 VOICE 来源的 StructureOverride 里 BARRACKS/FACTORY/STARPORT 且 addon_decided=False
  的条目 → 弹 3 选项(不挂 / 推荐 N 科技+M 双倍 / 取消)。`_clone_batch_for_addon_option`
  深拷贝整批 directive + 修改目标 item。
- 挂载到 `_submit_directives` townhall confirm 之后、delta 解算之前;单槽互斥:townhall 优先。
- auto_prereq/auto_addon bot 内部 StructureItem 统一设 `addon_decided=True` 防误触发弹窗。
- LLM 规则:`docs/llm_prompt/rules.md` 人族段新增挂件词表 + addon_decided 解析规则;
  `few_shot.md` 新增例 60a-d(mix/部分挂/不挂/没说四场景)。
- 单测:`tests/unit/test_addon_confirm.py`(28 case 覆盖 schema / 推荐算法约束 / 已有挂件增量减扣 / 弹窗触发)。

### 2026-06-18 科技/施法单位主动技能补全(鬼兵 EMP/狙击、女妖隐形等永不施放修复)

**修正 (Fixed) / 新增 (Added)**

用户:鬼兵不放 EMP/狙击、女妖被打不隐形,"所有科技兵种好好检查"。三族审计(并行 agent)+ 独立
opus 评审定位根因:**vibecraft 走 sharpy 默认 `MicroRules.unit_micros` 表派 per-unit 战斗微操,
没注册 micro 的单位 = 主动技能永不触发**。GHOST/BANSHEE 根本没注册(EMP/狙击/隐形全不放);
ROACH 的 `MicroRoaches.__init__(self, knowledge)` 构造签名异常(其余 micro 都无参)→ 注册时
TypeError 被漏(钻地回血永不工作)。

修复(P1,用户点名):
- **新** `combat/terran/micro_ghosts.py`(`MicroGhosts`):鬼兵 EMP/狙击/隐形,**能量分段**
  (≥75 先 EMP 后 Snipe / 50-74 只 Snipe / <50 才 Cloak,防 snipe 饿死 EMP)+ snipe 引导期短路
  (不自打断)+ 全路径 stay_safe(脆皮不冲前)+ cloak 查敌方探测器。
- **新** `combat/terran/micro_banshees.py`(`MicroBanshees`):接敌 + 未被探测 + energy>30 → 隐形。
- **改** `combat/zerg/micro_roaches.py`:修构造签名 `(self, knowledge)`→`(self)`(原因→无法注册)。
- **改** `combat/micro_rules.py`:注册 GHOST/BANSHEE/ROACH micro。

调优(P2):
- **改** `combat/protoss/micro_sentries.py`:守护护盾触发阈值 `range_power > 10`→`> 6`(小规模也开盾)。
- **改** `combat/zerg/micro_vipers.py`:Abduct 加 `engaged_power > 6` 威胁门 + 高价值目标
  (坦克/巨像/BC/不朽)豁免门(防小股 viper 乱拽,又保留偷拽高价值)。
- **改** `combat/terran/micro_ravens.py`:加自动炮台(`BUILDAUTOTURRET_AUTOTURRET`,排干扰矩阵后,
  落点 cd_manager 门控一次部署)。

机制定论(评审):`cd_manager.is_ready(tag, ability)` **两参形式**已含 tech 研究门 + 能量 + 冷却,
不需额外查 upgrade;红线是绝不给它传第 3 个 cooldown 参数(传了绕过研究门空放)。**scope 边界**:
micro 只覆盖"caster 跟大军一起参战";玩家 `unit_claim` 单独编队偷袭的 caster(Reserved)不走自动施法。
砍 Cyclone lock-on(build 库无飓风=死代码)+ Medivac afterburner(收益低)。

验证:`scripts/caster_ability_selftest.py`(debug 解锁研究门 + 生 caster/敌 + `VIBECRAFT_CASTER_TRACE`
grep `CASTERTRACE`)真局实测 **鬼兵狙击 9 / EMP 9 / 女妖隐形 6 全 PASS**;`test_sharpy_patch_audit` 33
全过(改的 method 均带 marker)。设计 + 评审留痕 `docs/plans/2026-06-18-caster-abilities-design.md`,
patch 清单 `docs/sharpy-patches.md` §13。

### 2026-06-17 点连接先闪上一把游戏残留界面 → 重连清状态 + "连接中"占位

**修正 (Fixed)**

- 用户:点连接后会先显示上一把游戏的界面,过一会才刷成房间。根因:`useWs` 的 connectNow()/close()
  都不重置 reactive 游戏状态(`status.sc2` / snapshot / minimap / recommendation / roomState 等),
  重连后旧数据滞留;App 在新 `room_state` 到达前回落主界面,用旧 `sc2='playing'` + 旧 snapshot
  渲染出上一把 cockpit。修法:① `useWs` 新增 `resetSessionState()`,connectNow()(用户主动连接)
  开头清空所有游戏内容 ref(保留 `status.link`,连接进度仍由 WS 事件管);**只在 connectNow 调,
  中途断线 auto-retry 走 connect() 不 reset → 不会闪掉对局中的 cockpit**。② `web/src/App.vue` 加
  `isConnecting` 门控(主动连接后、room_state/游戏状态到达前显示"连接中…"占位 spinner),不让主界面
  用已清空状态渲染;条件含 `roomState===null && !isPlaying`,中途重连(状态仍在)不触发。前端 224
  单测 + 构建通过。

### 2026-06-17 后加入玩家遇对局进行中 → "游戏进行中"提醒(不再生硬弹回入口)

**新增 (Added)**

- 用户:点进入点连上 server 后,若已有玩家正在进行游戏,显示提醒页让玩家稍后再试(不排队、不占位)。
  新增 `web/src/components/GameBusyNotice.vue`(全屏提醒:时钟图标 + "游戏进行中" + "当前已有玩家
  『XX』正在进行游戏,请稍后再试" + 「重试」/「返回入口」)。`web/src/App.vue` 兜底 watcher 分流:
  连上后未入房时,若 `room.state != 'lobby'`(对局进行中)→ 置 `gameBusy` + 抓在玩的人名(host slot)
  + 断连显示提醒;若 `state == 'lobby'`(刷新/被踢)→ 保持原"断连回入口"行为。「重试」=重连重查
  (游戏结束则正常进大厅),「返回入口」=回入口页。纯前端,复用 server 既有 `room_state` 预览推送 +
  `room_error`("对局进行中,无法加入")拒绝路径,后端零改;同 pid 重连(正在玩的人自己)走重连占回
  原位,不会误显示。前端 224 单测 + 构建(vue-tsc 类型检查)通过。

### 2026-06-17 全军防守大军"原地保持队形拉扯"修复(PlanZoneDefense 不抢主力)

**修正 (Fixed)**

玩家全军防守时,后期生化大军"出不了门、在家原地拉扯,像保持队形那种"。根因(真局复现 + 独立评审):
defend intent 下两个 plan 同时争抢主力且目标不同 —— `PlanZoneDefense` 把主力 claim 成 Defending
送 `enemy_center`(敌人实际位置,每帧重算的移动靶 + 敌散 3s 后 release),`PlanZoneGather` 把
release 回 Idle 的主力送威胁感知锚点;两目标不同 + role Idle↔Defending ~1Hz 翻转 → 大军每秒换
行进目标、原地横跳(确定性复现:army 中心 x/y 方向反转 18/16,且追逃敌一路冲到敌方主基)。
修法(评审推荐的"单一收口"):`vendor/sharpy/sharpy/plans/tactics/zone_defense.py::execute` 加
`# vibecraft:` hook —— **defend intent 下 PlanZoneDefense 不再 claim/dispatch 主力**(释放残留
非工人 Defending 交还 gather + 只把附近 free 主力 power 计入 defenders 防 worker 过度拉),主力防守
完全交给 `PlanZoneGather` 已有的威胁感知锚点(单一稳定点 + 滞回)→ 无 role 翻转 → 彻底消抖;敌入
射程仍由 combat 引擎交战(与 execute target 解耦)→ 守得住。严格 gate `combat_intent_override=="defend"`,
非 defend / dummies / 默认 AI 走原 `enemy_center` 路径不变。真局验证:反转率降 5.7x、大军稳定守家
不跨图追敌、存活翻倍;override_acceptance 三族 defend(VeryHard 多数票)全 PASS,零回归不改 spec。
走完整 vendor patch 流程(marker + `test_sharpy_patch_audit` PATCHED_METHODS + `docs/sharpy-patches.md`
新节 + `TestPlanZoneDefenseDefendHook` 3 条 hook 单测)。设计 + 评审留痕
`docs/plans/2026-06-17-defend-tug-fix-design.md`;新增确定性自验 `scripts/defend_tug_selftest.py`
(60 枪兵 + 周期 flicker 敌 + defend pin)+ 配套 env 门控 debug hook(common_bot)。

### 2026-06-17 坦克不动时自动架起(SiegeIdleTanksAct)

**新增 (Added)**

- 用户:坦克不动的时候尽量架着。`src/vibecraft/bot/auto_combat/terran/siege_idle_tanks.py`
  新增 `SiegeIdleTanksAct`(分层微操):未架坦克(`SIEGETANK`)处于 idle(无移动/攻击命令)→ 自动
  架起(`SIEGEMODE`);不架在主基斜坡口(挡自家进出)。坦克被 gather/attack plan 下移动/攻击命令时
  SC2 自动先解架再走,所以集结/进攻/撤退途中不被卡住,到位停下再架。挂进 5 个用坦克的人族 plan
  (mech / two_base_tanks / two_one_one / one_one_one / bio_stim),`PlanZoneGather()` 之后。
  背景:vibecraft Terran 用 generic `PlanZoneGather`(不是带 siege 逻辑的 `PlanZoneGatherTerran`,
  后者是 sharpy 给自带 dummy bot 用、vibecraft 没接),坦克原本不会自动架。真局验证:two_base_tanks
  局 SIEGETANKSIEGED 峰值 3(修前恒 0)→ 坦克 idle 时确实架起。

### 2026-06-17 人族大部队进攻"原地拉扯无法前进"修复(PlanZoneAttack 撤退滞回)

**修正 (Fixed)**

玩家人族 bio 大军下进攻指令后,部队+医疗艇原地反复拉扯、无法前进(真局日志:整局 PlanZoneAttack
**Attack started 32 次 / Retreat started 25 次**)。根因经真局日志定位:bio 接敌散开 → fight_center
局部兵力**瞬时**掉到撤退阈值下 → 立刻 Retreat → 退 RETREAT_TIME(~20s) → 兵力恢复 → 再 attack →
又散 → 进攻/撤退振荡。已有的"30 格内 Moving 援军计入 own_local_power"(2026-06-02)不够,瞬时散开仍触发。

- `PlanZoneAttack._should_retreat` 加**撤退滞回**:非 probe 实攻时,兵力撤退条件需**持续 ≥
  `RETREAT_HYSTERESIS_S`(2.5 游戏秒)**才真退;瞬时掉(散开抖动)不退、大军原地顶住等队形。
  时间戳 `_retreat_pending_since` 在 `_start_attack` 清零(每进攻 episode 从干净计时,防 stale 旁路)、
  撤退条件不成立时清零。**probe(火力侦查"对等就撤")豁免滞回保持立即退;intent=retreat/defend/hold +
  kite_retreat + all_in/force_attack 都在滞回前 early-return,不受影响**(三族共享,经独立 opus 评审)。

**新增 (Added)**

- `tests/unit/test_sharpy_vibecraft_hooks.py`:撤退滞回时序单测(< 阈值不退 / ≥ 阈值才退 / 劣势消失
  计时清零重置)。`docs/sharpy-patches.md` 记录;`test_sharpy_patch_audit` 仍过(改 patched 方法内部逻辑,无新 dispatch)。

### 2026-06-17 后加入的玩家不推送历史聊天(admin 仍可见)

**变更 (Changed)**

- 玩家连接时请求聊天历史(`chat_history_req`)**一律回空** —— 后加入的玩家只看得到自己进来
  之后的实时消息,不再回放之前的聊天(用户隐私/清爽)。`ws.py::_handle_chat_history_req`。
- ChatHub 仍照常累积全部历史;**admin 仍能看完整聊天记录** —— admin 走独立 HTTP 路径
  `GET /api/admin/chat`(SCRAM 鉴权),读同一个 ChatHub,不受玩家侧改动影响。

### 2026-06-17 全体防守智能选点:无威胁→守最前沿基地(主力路径补齐)

**修正 (Fixed)**

「全体防守」(intent=defend)的智能选点(用户:敌近某基地→优先守该基地;无敌→守距敌方主基最近的
己方基地)在**主力部队路径**有 gap:

- `PlanZoneAttack` 的 defend 撤防目标,无威胁且无玩家指定点时**回落到 `gather_point_solver.gather_point`
  (natural rally)**,而非"最前沿基地"——导致主力守在 natural、且与 `PlanZoneGather`(idle 单位路径,
  已用 `_vbc_forward_defense_point`)不一致。新增 `PlanZoneAttack._vbc_forward_defense_point`(同款:距敌主基
  最近的己方 zone,`min` 按距离确定性,无分矿兜底 home),把 fallback 改成它。两条路径现一致守最前沿。
- 规则1(敌近守该基地)经真局 trace 确认信号 `assaulting_enemy_power` 活、选点逻辑(`_vbc_defend_target`
  威胁感知 + 阈值 3.0 + 滞回)在主力/idle 两路径都在;本次只补规则2 的主力路径 gap。

**新增 (Added)**

- `scripts/defend_selftest.py` + `common_bot` 的 `VIBECRAFT_DEFEND_TRACE`/`VIBECRAFT_DEFEND_SPAWN_ENEMY`
  钩子:全体防守真局自验(逐帧记 intent + 各己方 zone 威胁值 + army 中心 + 最前沿基地;可 debug 生强敌验迁防)。
- `tests/unit/test_sharpy_vibecraft_hooks.py`:defend 无威胁 fallback 改"最前沿基地"的回归单测(更新原
  3 条 gather_point 断言)。`docs/sharpy-patches.md` + `test_sharpy_patch_audit.py` EXEMPT 行号同步。

### 2026-06-17 航母回家待命抽搐修复("main"解析非确定性 + standby 每帧重发)

**修正 (Fixed)**

玩家「所有航母回家待命」后航母在家门口不停抽搐(调试清单 #10)。真局自验
(`scripts/carrier_standby_selftest.py`,debug 生航母 + 注入待命 + 逐帧 trace)定位到两层根因:

- **真根因:`named_spot._own_main` 用 `townhalls.first.position`**——`bot.townhalls` 是 Units,
  开了分基地后**帧间顺序不保证稳定** → "main" 每帧解析到不同 Nexus → standby 目标点在多个基地点
  间跳变 → 航母每帧被指向不同点、追跳变目标 = 抽搐(role 全程 Reserved,**不是** sharpy 抢)。
  改成取**距 `start_location` 最近**的 townhall(真正的主基地),帧间确定性。修复"main"解析的同时
  修好所有用 "main" named_spot 的功能(build_at / move / standby 等)。
- **防御层:standby tick 每帧重发 `move`/`attack`**——慢速大单位(航母)每帧重发 move 会打断
  加速/寻路而卡顿。改为**已在朝同一点移动/已在打同一个敌就不重发**(用户「目标坐标锁定」规则)。

**新增 (Added)**

- `scripts/carrier_standby_selftest.py`:航母回家待命抽搐真局自验(orbiting 检测:抖→几千条
  move-branch trace / 安定→个位数)。修后整局仅 1 条 trace、pos 解析唯一稳定值。
- `tests/unit/test_named_spot.py`:`_own_main` 取距 start_location 最近 townhall 的确定性回归单测。

### 2026-06-17 多挂件命令支持 + 挂件起飞挪位分支单测

**新增 (Added)**

- 玩家可一条命令补多个挂件（用户：「我有很多兵营，补 N 个科技挂件 / M 个双倍挂件」）：
  `docs/llm_prompt/rules.md` 挂件段补「补 N 个〈挂件〉用 `delta:N`、一条命令多种挂件 → 多个 item」
  的明确例子。执行层早已支持（structure_override delta 解算 + 每帧给一座空闲父楼挂一个、多帧
  累积到 N）；真局自验确认 `structure_delta resolved: FACTORYTECHLAB delta=1 → target=1` 链路通。
  重 dump `docs/llm_system_prompt.md`。
- `tests/unit/test_auto_prereq.py`：`TestAddonRelocate` 4 条单测验证挂件位被占时起飞挪位的
  分支逻辑 + ability id（没空位→`LIFT_FACTORY` / 在飞→`LAND_FACTORY` / 有空位→`build` /
  没落点→不瞎飞）；mock 同步到新实现（飞行变体 structures + can_place_single 落点 + 落点缓存）。
- `scripts/addon_selftest.py --block-addon`：#543 挂件位被占→起飞挪位→落下→挂上的真局自验
  （debug 生孤立重工 + 堵挂件位 + 注入「重工下科技挂件」），断言 LIFT/LAND + FACTORYTECHLAB
  真挂上。**已跑通 PASS**（LIFT→定落点→LAND 同一点→挂件附着）。

**修正 (Fixed)**

挂件起飞挪位（#543）真局自验跑通过程中揪出三个真 bug（原以为只是测试搭建问题，实为产品缺陷）：

- **挂件位被占却挪不动**：`_find_relocate_spot` 原用 `find_placement(addon_place=True)`，其网格分支
  的挂件位检查走 `TERRANBUILDDROP_SUPPLYDEPOTDROP` query，对地形过度严格——明明有空地、
  `can_place_single` 也说放得下，它却恒返回 None → 挂件位被占时即便有空地也永远 LIFT 不起来。
  改为「普通楼位判定 + `can_place_single` 验右侧挂件位」由近及远确定性网格扫描（就近优先）。
- **起飞后永远不落地**：父楼一 LIFT 就变成 `<PARENT>FLYING`（另一个 UnitTypeId），原循环只遍历
  `structures(FACTORY)` 不含飞行变体 → LIFT 后这座楼从循环消失、LAND 分支永不触发。遍历改为
  同时含落地 + 飞行变体（三族通用 FACTORY/BARRACKS/STARPORTFLYING）。
- **降落抽搐**：飞行中每帧拿漂移中的飞行坐标重算落点 → 楼追移动靶、落不下。改为**起飞前**就基于
  稳定地面位置算好落点并按 tag 缓存，飞行中幂等重发同一落点，落地清缓存。

### 2026-06-17 SC2 崩溃弹窗 server 周期清理

**新增 (Added)**

- `scripts/cleanup_sc2_crash.ps1`：安全清理 SC2 崩溃弹窗的 PowerShell 脚本。
  跑 build_acceptance / addon_selftest 等真局自测反复起 SC2，崩溃时 Blizzard 会留一个
  `BlizzardError`（标题 "StarCraft II"）崩溃上报弹窗及 `WerFault*` 堆在桌面。
  脚本只杀这些崩溃弹窗类进程（靠窗口标题含 StarCraft/SC2/Blizzard 判定），
  **绝不碰正在跑的真局**（`SC2_x64` 进程不在清理范围）。清理结果 append 到
  `logs/sc2_crash_cleanup.log`。
- `BotService._loop_sc2_crash_cleanup()`：server 启动即挂上的后台 asyncio 循环，
  每 30 分钟自动调用上述脚本清理一次，不走大模型判断。
  **Windows-only**（`sys.platform != 'win32'` 时立即 no-op 返回）；
  脚本文件缺失时记 `sc2_crash_cleanup_disabled` 日志后 return，不影响 server 启动。
  任务随 `BotService.run()` 生命周期管理（启动时 create_task，finally 里 cancel）。
- `tests/unit/test_server_service.py` 新增 `TestLoopSc2CrashCleanup.test_noop_on_non_windows`：
  验证非 Windows 平台调用协程立即返回（monkeypatch sys.platform='linux'），不进死循环。

### 2026-06-17 人族挂件(addon)支持：语音下挂件 / 出兵自动补挂件 / 挂件位被占起飞挪位

**新增 (Added)**

- 玩家可语音/文字给产能楼下挂件，且出兵缺挂件会自动补（用户真局 3 连问）：
  - **#541 "重工下科技挂件"不再被误当建新重工**：`docs/llm_prompt/rules.md` 新增「人族挂件」
    段——「〈兵营/重工/机场〉下/挂 科技挂件/双倍/TechLab/Reactor」→ `structure_override`，
    structure_type 用挂件 enum（`FactoryTechLab` / `BarracksReactor` …），明确不是建新楼。
  - **#542 出坦克/掠夺者/女妖自动补 TechLab**：`_exec_production_override` 加挂件前置检查——
    用 python-sc2 `TRAIN_INFO[...]['requires_techlab']` 判该兵种是否需挂件，生产楼没挂 TechLab
    → 自动 emit 挂件卡（`_emit_addon_build`），兵种挂起等挂件好。坦克/掠夺者/女妖/雷神/渡鸦/
    BC/幽灵全覆盖（数据源 Blizzard，patch 自动跟随）。
  - **#543 挂件位被占 → 起飞挪位再挂**：`_build_addon_on_parent` 多帧推进——挂件位（楼右侧 2x2）
    被占时，先 `find_placement(addon_place=True)` 确认有「楼3x3+挂件2x2」都放得下的落点，
    有才 `LIFT` 起飞 → 飞行中找落点 `LAND` → 落地后挂 `build(addon)`。每帧只动一座 idle 楼，
    不打断在产的楼。
  - 挂件走专门执行路径（`builder.build(addon)`，附在父楼上），不是 SCV 盖的普通建筑。
  - `tests/unit/test_auto_prereq.py` 新增 `TestAddonPrereq` 3 条（helper / addon 名映射 /
    坦克无 TechLab 自动补 FACTORYTECHLAB）。

**变更 (Changed)**

- **#540 死神回血门 0.95→0.99**（`reaper_expand`）：用户「死神跳高地被枪兵打到血低后撤回，
  95% 就又冲上去被一波带走」→ 回满血才再出去。

### 2026-06-17 骚扰单位（死神等）撤退更及时、更自保

**变更 (Changed)**

- `HarassWorkerLineAct` 骚扰微操彻底改风筝（用户：死神撤退不够及时、容易死；farm 农民时
  农民 A 过来不会边打边退、站原地被围死，血到一丝才跑也跑不掉）：
  - **死神永不站撸，每打一枪就退**：hit-and-run 改为**武器冷却中 + 任何能打到它的敌人
    （含农民！）逼近 → 立刻后撤保持距离**。原来农民被排除在「威胁」外 → farm 农民时被
    A 过来的农民贴脸围死、跑不掉。现在射程内打完一枪进冷却就退一步，武器好了再贴上来打。
  - 受伤（血 < `recover_hp`）时即便武器好、遇战斗威胁也退（额外自保，宁可不打这枪）。
  - 风筝触发距离 `_KITE_TRIGGER` 9→11；新增 `_nearest_attacker`（含农民的最近攻击源）。
  - `reaper_expand` 死神 `bail_hp_ratio` 0.5→0.6（提前全撤回家回血）。原来 0.5 没起效的根因
    其实是「站撸被围 → move 回家被身体挡住跑不了」，现在主动风筝不被围，bail 才真生效。
  - 范围不变：只在骚扰（HarassWorkerLineAct）时生效，不动主力/全军战斗逻辑。
  - `tests/unit/test_harass_act.py` 新增 3 条：冷却+只有农民也 kite / 受伤+武器好仍 kite /
    满血则照常 farm 农民。

### 2026-06-17 面板显示产能楼挂件状态（无 / 科技 / 双倍）

**新增 (Added)**

- 宏观面板产能楼（兵营 / 重工 / 机场）现显示**挂件状态**（用户：看不出建筑是没挂件、
  挂了科技实验室、还是反应堆）：
  - `Director._build_production_buildings` 每个产能楼增 `addons` 字段
    `{none, techlab, reactor}`（按 ready 建筑的 `has_techlab` / `has_reactor` 统计；
    同时有则记 reactor）。神/虫建筑无挂件，恒 none，无害。
  - 前端 `TechRows.vue` 产能楼图标左下角加绿色小标签「科N / 双N」（全没挂件则不显示），
    tooltip 追加「挂件：无挂件 N、科技 N、双倍 N」。`types.ts` `ProductionBuildingItem`
    加可选 `addons` 字段。
  - `tests/unit/test_tech_progress_panel.py` 新增 2 条（挂件分类 / reactor 优先）；前端
    224 单测全过。

### 2026-06-17 人族扩张系开局早期出兵节奏（reaper/hellion expand）

**修正 (Fixed)**

- `reaper_expand` / `hellion_expand`（上轮列序修复漏掉的两个扩张系开局）早期
  **兵营建好后空转、死神/枪兵出不来**（真局 `match_20260617`：reaper_expand 兵营
  t=90 建好但 busy=0 到 t=137、~47s 空转，矿堆 400，第 1 个死神拖到 2:47）。根因同
  列序饿死：出兵（REAPER/HELLION/MARINE）排在 `Expand(2)`/Factory/Starport/BB2/gas3
  等重建筑**后面**，被它们每帧 `reserve()` 把矿吃光。叠加 `reaper_expand` 的 REAPER
  cap=1（出 1 个就停，`opening_done` 却要 ≥4）+ MARINE 卡在 CC2 后 → 中间兵营没活干。
  修（按用户拍板的死神开矿正确节奏：**先 1 死神 → 升星轨+开二矿 → 补齐 4 死神**）：
  - `reaper_expand`：REAPER 1（兵营一好第一时间出，barracks 不空转 + 早侦查）→
    MorphOrbitals/Expand（经济）→ REAPER 4（补满，对齐 `opening_done` 的 ≥4），连续
    MARINE 30 放 Expand 之后。早窗 `prod_util` 0.74→0.82，兵营建好即 busy。
    **关键调参**：死神**不能整组排星轨/Expand 前、更不能加 priority** —— sharpy
    priority=True 会让死神抢在 150 矿星轨 / 400 矿二矿前 reserve 把它们饿死
    （orbital_command / command_center_2 双 FAIL）。按"1 死神→经济→补满"两全。
    build_acceptance 10/11，与原版等价（仅既有 orbital_command @130s 边际 FAIL，
    原版也 FAIL，非本次引入）。
  - `hellion_expand`：REAPER(侦查)/HELLION 从 plan 底部上移到 Factory/Expand 之后、
    后续重建筑（Starport/BB2/gas3/Reactor）之前。早窗 `prod_util` 0.69→0.92，
    build_acceptance 9/11（hellion_4 / banshee_2 为既有边际失败：FACTORYREACTOR
    设计上等 2 恶火才挂，270s 凑 4 本就紧；恶火上移只会更多不会更少）。
    **不**在 hellion_expand 塞早期 Marine —— 试过 MARINE 24 抢光矿把恶火 4→2、
    拖崩 banshee/stim（它是恶火控图流，枪兵是 bio 转型后的事）。
  - 至此人族 9 opening 全部完成早期出兵列序修复（前 7 个上轮已修）。

### 2026-06-17 修"地雷埋地"指令解析失败（TargetKind 缺 self）

**修正 (Fixed)**

- 玩家"地雷埋一下 / 地雷到这里埋到地上"等寡妇雷埋地指令**间歇"识别失败"**（真局
  `match_20260617_000202`：同一类指令有时成、有时失败）。根因：寡妇雷埋地
  `BURROWDOWN_WIDOWMINE` 是**自施法**（无外部目标），LLM 自然吐 `target.kind="self"`，
  但 `TargetKind` 枚举**漏了 `self`** → pydantic 校验失败 → 整条 directive 被丢 →
  玩家看到"识别失败"。执行层 `facade.cast_ability_on_units` 本就默认且支持
  `target_kind="self"`（就地施法），纯粹是 schema 缺枚举值。修：`TargetKind` 加
  `SELF="self"`。
- `docs/llm_prompt/rules.md`：补 cast_ability `target.kind` 选择指引——就地施法
  （埋地/潜伏/架坦克/嗑药）用 `self`；"埋到某点"用 **move+burrow 两卡组合**
  （`move_to` + `activate_when=unit_arrived` 后 `cast_ability`），沿用代理建造串联模式。
  重 dump `docs/llm_system_prompt.md`。
- `tests/unit/test_directives.py`：新增 `test_self_cast_burrow_target_kind` 复刻真局
  被拒 payload，确认现在过校验。

### 2026-06-17 修玩家"出维京"静默失败（VIKING 占位 enum）

**修正 (Fixed)**

- 玩家语音/文字"出维京"`production_override` 解析成功但**永不出兵**（真局
  `match_20260617_000202`：2 个 Starport 在场，VIKING 全程恒 0，而"出鬼兵"正常）。
  根因：别名 canonical 名 `"Viking"` → `UnitTypeId["VIKING"]` = 1940 是**不可训练的
  占位 enum**（`trained_from=None`）→ `bot.train(VIKING)` 静默 no-op；真·可训练的
  飞行模式维京是 `VIKINGFIGHTER`（35，from STARPORT）。鬼兵正常是因为
  `GHOST`（50）本身就可训练。修：`Director._resolve_unit_type_id` 加 `_UNIT_NAME_MAP`
  归一 `VIKING→VIKINGFIGHTER`，且 prereq 检查 + 自动补建也用归一名（否则缺 Starport
  时占位 VIKING 无 prereq → 不会自动补机场）。三族别名 canonical 名全量审计确认仅
  Viking 一例有此占位碰撞（其余双形态单位 HELLION/SIEGETANK/WIDOWMINE/LIBERATOR/THOR
  的 canonical 名都落在可训练 enum 上）。
- `tests/unit/test_auto_prereq.py`：新增 `TestVikingNameNormalization` 3 条回归
  （resolve 归一 / 有 Starport train VIKINGFIGHTER / 缺 Starport 自动补 STARPORT 链）。

### 2026-06-16 人族 9 opening 早窗产能优化（廉价出兵列序前置）

**修正 (Fixed)**

- 人族开局 build 早窗（0-480s）兵营长时间空转：根因是廉价持续出兵
  （`TerranUnit(MARINE, …)`）在 `BuildOrder` child 列表里排在重建筑/扩张
  （`GridBuilding` / `Expand` / `BuildAddon`）**后面**——这些建筑每帧先 `reserve()`
  扣光矿，轮到枪兵时 `can_afford` 恒 False → 有兵营有矿却 0 产出半局。
  整局平均 `prod_util` 被晚期满产能稀释、掩盖了早期空转，之前没抓到。
  修法：把枪兵 `TerranUnit` 上移到 `AutoDepot()` 之后、所有建筑/扩张 Step **之前** +
  `priority=True`，每帧先填满兵营产线，余矿再给建筑（critical path 仍由
  `SequentialList` 保顺序；坦克/女妖/掠夺者走 gas，与枪兵抢的资源池不同，不受影响）。
  - 早窗 `prod_util`（`--seconds 480`，2 seed）修复前→后：
    `bio_stim` 0.30→0.79 / `two_base_tanks` 0.51→0.80 / `widow_mine_drop` 0.56→0.82 /
    `marine_rush` 0.54→0.70 / `one_one_one` 0.55→0.75 / `banshee_harass` 0.67→0.71 /
    `two_one_one` 0.65→0.94。`hellion_expand` 0.69 / `reaper_expand` 0.74 本就 OK，未动。
    9 个人族 opening 早窗产能现全 ≥0.69。

**变更 (Changed)**

- `docs/build-optimization-runbook.md`：新增 §4.5「早窗产能第二根因：廉价持续出兵
  被排在重建筑/扩张后面饿死」——记录机制（`BuildOrder.execute()` 按 child 顺序逐个
  reserve）、修法、判据，供 patch 后重跑照做。
- `docs/build-optimization-runbook.md` §6 新增 harness 陷阱「intent-gated 产能被
  forced-defend 关掉」：神族折跃流 all-in（4bg / dt_rush 走 `ForwardWarpStalker`）
  早窗 `prod_util` 低 + 囤资源是**沙盒假象**——沙盒每 tick 强制 defend，而
  `ForwardWarpStalker` 在 defend intent 下 noop → 折跃产兵引擎整局关闭。实测 4bg
  `--no-sandbox`（warp 引擎开）util 0.39→0.64、余钱 965→415。结论：神族/虫族 opening
  早窗审计后**无真·列序欠产能**（4bg/dt_rush = 沙盒口径假象，真局 util 健康；其余神族
  = 经济/科技固有；虫族 larva 堆 = 早期 OL 跟不上的 supply-block 症状），均未改 build，
  仅留审计留痕。只有人族 9 opening 有真欠产能（已修，见上）。

### 2026-06-16 admin dashboard 服务端 API + 鉴权

**新增 (Added)**

- `src/vibecraft/server/admin_games.py`：扫描 `logs/` 真人对局元数据的纯函数模块。
  白名单规则（评审 M1）：只收录 `match_*` 前缀目录；`game_*`（build_acceptance 沙盒）、
  `eff_*`、`e2e_*`、`*selftest*`、`*proof*` 全部排除。反向 seek 读末行、mtime 倒序、
  默认最近 50 局，不整文件读入内存。
- `src/vibecraft/server/http.py`：新增 admin 路由分发 + 独立鉴权中间件。
  - 5 个端点：`GET /admin`（serve admin.html）、`GET /api/admin/status`、
    `/api/admin/chat`、`/api/admin/chat-send?text=`、`/api/admin/games`、
    `/api/admin/feedback`。
  - 鉴权（评审 M2）：`X-Admin-Token` header only（API）；`?key=` 或 header（页面入口）；
    `secrets.compare_digest` 常数时间比较；鉴权失败统一 404（非披露）；未配 admin_token
    → 全部 404（secure by default）；admin token 最小 16 字符警告；进程内失败计数
    + 60s 锁定（连续 5 次失败触发）。
  - Admin 响应不发 `Access-Control-Allow-Origin: *`（收紧 CORS）；白名单字段输出
    （不 dump 整个 config/registry，防漏 room token）。
  - feedback IP 轻度脱敏（IPv4 保留前两段）；chat-send 复用同一 ChatHub 实例
    （id 连续，不割裂历史），fire-and-forget broadcast via `asyncio.create_task`。
- `src/vibecraft/cli.py`：`serve` 命令加 `--admin-token` option，同时读 env
  `VIBECRAFT_ADMIN_TOKEN` 兜底（click `envvar=`）。
- `src/vibecraft/server/service.py`：`ServiceConfig.admin_token: str | None = None`
  字段；`BotService.run()` 将 `admin_token` 和 `room_service` 注入 `make_process_request`。
- `scripts/start.ps1`：加 `-AdminToken` 参数，透传 `--admin-token`。
- `src/vibecraft/server/static/admin.html`：完整 admin dashboard 单文件页（状态 + 大厅 +
  聊天 + 对局记录 + 玩家留言），key 存内存、轮询走 header，games/feedback 按需加载。
- `tests/unit/test_admin_games.py`：26 条单测覆盖 `_is_match_dir` 白名单规则、
  `_read_last_line` 反向读、`scan_match_games` 正负样本 + 元数据提取 + 排序 + limit。
- `tests/unit/test_admin_auth.py`：26 条单测覆盖鉴权（无/错/对 token → 404/404/200）、
  失败计数+锁定（含过期恢复）、CORS 检查、chat-send id 连续+broadcast、
  feedback CSV 解析+IP脱敏、status 白名单字段。

### 2026-06-16 admin dashboard 前置：player_name telemetry + join 拒绝文案修正

**新增 (Added)**

- `GameConfig.player_name: str = ""`：玩家昵称字段（picklable，跨 spawn 子进程传递）。
  `match.py build_plan` solo/multi 两路均从 `Room.slot.name` 填入。子进程入口写到
  `VIBECRAFT_PLAYER_NAME` 环境变量，`common_bot.on_start` 读取后落进 `telemetry game_start`
  record 的 `player_name` 字段。存量旧局 / build_acceptance 沙盒无玩家，显示空串/"—"可接受。
- `build_game_start_record` 新增 `player_name: str = ""` 参数，向后兼容（旧调用方不传即空串）。

**变更 (Changed)**

- `Room.join()`：新玩家加入且房间非 lobby 态时，抛 `RoomError("对局进行中，无法加入")`
  （就地判断，不改 `_require_lobby()`，保留设置类操作文案"不能改房间设置"）。

### 2026-06-16 狗毒爆 Stage 1：毒爆前压变形 + 持续补给（用户真局反馈）

**变更 (Changed)**

- 用户真局反馈狗毒爆 3 问题（详见 `docs/plans/2026-06-16-ling-bane-choreography-design.md` + Opus 评审）。
  本次落地 **Stage 1（问题 1+2）**：
  - **抽共享毒爆 morph 模块** `zerg/baneling_morph.py`（开局 plan 与 build-aware sustain 共用）：
    护蛹 gate（≥6 ling + 中心 8 格内 ≥4 ling，防 2026-05-23 cocoon 裸死坑）+ **前压 gate**
    （ling 群已推进过中点、寻路距离离敌方主基地比离己方近才变 → 狗先压出去再在前沿变蛹）+
    **60s 超时 latch 兜底**（推不出去如 forced-defend 沙盒 → 回退就地变，防永不出爆/效率回归）。
  - build 加 `baneling_morph_mode: forward|home`（默认 home），ling_bane 设 forward。
    build-aware sustain 的 ZERG 分支**特判 BANELING**：按 flag 选共享 forward-morph 或默认 home。
    防将来宏观 build 误继承 all-in 前压语义。
- **真局自验（--no-sandbox 自然打）**：BANETRACE 日志显示首波 t=214 在前沿变（enemy_dist 43 <
  home_dist 81、fallback=False 真过 gate）、全局 7 波持续补给（后续在敌方家门口 enemy_dist 8-25 变）、
  Victory。**效率沙盒回归**：latch 兜底保住 bank 351/350、爆虫 250（无退步）。

**未完（Stage 2，问题 3 狗毒爆行军协同）**：解注释 sharpy 原生 `faster_group_should_regroup` +
MoveType 门控（只 Assault/Push clamp、retreat 放行）+ cohesion 锚点含 BANELINGCOCOON（防 14s 蛹期甩散）。

### 2026-06-16 修复：SC2 窗口被遮挡污染视频流 → 窗口置顶

**修正 (Fixed)**

- **问题**：视频推流用 `mss` 按 SC2 窗口的**屏幕矩形**抓屏 → 任何盖在 SC2 前面的窗口
  （报错框 / 其它应用）都会被抓进视频、推到手机。
- **修法**：抓屏定位时周期（每 2s，限频）把 SC2 窗口设 `HWND_TOPMOST` 置顶
  （`SetWindowPos` + `SWP_NOACTIVATE` 不抢焦点 → 不影响 per-window 音频抓取 / 输入）。
  PC 是"只当显示器、不交互"，遮挡窗口被压到 SC2 后面无副作用。多实例各自置顶（位置错开不重叠）。
  默认开，`VIBECRAFT_SC2_TOPMOST=0` 可关。**注**：真·模态系统框等极端遮挡仍可能盖住，
  彻底免遮挡需 WGC 窗口抓取（Windows.Graphics.Capture，留作后续若置顶不够）。

### 2026-06-16 修复：虫族 build-aware sustain morph 兵冻结（爆虫/飞蛇用 train 无效）

**修正 (Fixed)**

- **根因**（ling_bane 效率优化真局定位）：build-aware sustain 用裸 `ActUnit(BANELING, ZERGLING)`
  出爆虫，但爆虫是 zergling **morph**（变形）来的、不是 train —— `zergling.train(BANELING)`
  对 morph 无效 → 爆虫永远冻结在开局造的那几个（实测 ling_bane 卡 12，气浮 5733、狗涨到 231
  爆虫却不动）。同类影响所有 morph 兵（飞蛇/潜伏者/BL）。叠加 vendored `MorphBaneling` 用了
  当前 SC2 已失效的旧 ability `MORPHZERGLINGTOBANELING_BANELING`（引擎静默丢弃）。
- **修法**：① vendor 修 `MorphBaneling` ability → `MORPHTOBANELING_BANELING`（见
  `docs/sharpy-patches.md §6`）；② build-aware sustain 虫族路径改用 sharpy `ZergUnit`（按兵种
  dispatch：morph 兵走对应 Morph act + 从 larva 补源兵，larva 兵走 ActUnit）代替裸 `ActUnit`。
  顺带修好飞蛇（RAVAGER）等所有 morph 兵的 sustain 产量。larva 兵（狗/蟑/刺/飞龙）行为不变
  （ZergUnit 对它们等价 ActUnit(LARVA)，nydus 411→411 / mutalisk 264→264 回归吻合）。
- **ling_bane 调优**（用户授权"钱多多孵爆虫、可全变爆虫"）：爆虫 `ratio`(静态 20)→ `cap 250`。
  A/B（双 seed）：余钱 882/948→342/388（降 60%）、爆虫 12→250、气浮 5733→1890/2412（腰斩，
  浮气转成爆虫）、组合 ≈ 全爆虫（LING 1 + BANE 250，用户明确允许的极端）。
- 新增回归测试（MorphBaneling ability 已修 + ZergUnit 把爆虫 dispatch 到 morph）。

### 2026-06-16 修复：人族 mech doctrine 摆烂（产能写死低上限）+ 持续运营 doctrine 全类审计

**修正 (Fixed)**

- **持续运营 doctrine 全类审计**（18 个，新增 `build_efficiency.py --auto-switch-to` 驱动：开局 →
  opening 完成后切 doctrine，复用 director auto_switch）：之前"8 神族 doctrine 摆烂"是 harness
  回退默认开局（4bg）的假数据、作废。真测 13 个 → **17 健康 1 坏**：doctrine 靠"开局 build-aware
  sustain（本轮修好）+ doctrine plan"并行就能顶 200 人口、出对兵、余钱低。唯一摆烂 = **人族 mech**。
- **mech 根因**：mech 的 sharpy plan 把每种兵写死低上限（坦克 8 / 雷神 3 / 火车 6 / 维京 6），
  3 工厂造到上限就停 → supply 卡 178（<180 罚 bank）、余钱 5000+（双 seed 5139/5015 一致）。
  叠加切 mech 后通用 terran sustain 回退成 bio（对机械流错兵、不扩兵营）→ 两边都不顶人口。
- **mech 修法**（用户方向：多出雷神火车、前期防守、早升攻防）：工厂 3→5（VF4/5 裸厂出火车吞矿）；
  兵种上限 坦克 8→14 / 雷神 3→12 / 火车 6→12 / 地雷 4→6 / 维京 6→8；雷神设 priority + 生产 Step
  提到坦克前（列首抢气，否则便宜火车填满人口、雷神被挤成 0）；军火库提前到 VF1 之后（早升攻防）。
- **A/B（two_base_tanks→persistent_mech，双 seed）**：余钱 5139/5015→523/533、产能 0.31→0.45、
  supply 178→顶满 200、雷神 0→7（THOR7+TANK8+HELLION12+VIKING4 的机械流组合）。

### 2026-06-16 调优：dt_rush 早期卡人口（首水晶 14 农→13 农）

**变更 (Changed)**

- dt_rush 开局首水晶（PYLON）从 14 农提前到 13 农：原来 probe 15 先把 supply 顶到 15/15、
  水晶还没建好 → 早期卡人口 8.9s。提前一个农民给水晶留建造时间。A/B（双 seed）早期 block
  8.9s→4.5s，余钱/产能无回退（bank 697/710、util 0.47-0.48）。
- 注：dt_rush 整体"卡人口 31s"里 22.3s 是军队打到满人口（200/195，cap 因 pylon 凑不齐 200
  被 scorer 误计）的假卡点——是 build-aware 修好后军队能顶满的副产物，非退步；不动 scorer
  避免影响其它 build 评分。

### 2026-06-16 神族 9 个 opening_build 接 build-aware sustain（rollout）

**变更 (Changed)**

- 给 9 个神族 opening_build 加 `core_units`（中期续兵配比），全部走 build-aware sustain：
  1g_robo_immortal / 4bg / blink_stalker / cannon_rush / dt_drop_iac / dt_rush / iac_2base /
  phoenix_2base / void_ray_rush。每个按 build 既有身份声明主力（mass）+ 科技配菜（cap），
  不改打法、不增删兵种。
- **空军 producer 降档**：mass 是空军（STARGATE）时 `GridBuilding` 目标 8→4（`_AIR_MASS_PRODUCER_TARGET`）。
  航母/虚空极贵 + 气瓶颈，8 座星门必空转 / 气浮。实测 void_ray gas 仅 3154（降档生效）。
- **townhall producer 防御 skip**：`plan_from_core_units` 跳过 NEXUS/CC/HATCH/LAIR/HIVE 类 producer
  的 GridBuilding（MOTHERSHIP 从 NEXUS，扩楼会误盖基地），只续兵不扩楼。

**A/B 验证（vs 审计基线，单 seed + 空军/健康类多 seed）**：9 个 opening 余钱全线大降、产能全升、
健康 build 不退反进。代表值：blink_stalker 余钱 4295→158 / 产能 0.40→0.70；cannon_rush 6548→300 /
0.18→0.55；dt_rush 7672→709 / 0.066→0.45；phoenix_2base 6084→379（gas 不浮）；iac_2base 余钱
723→131 / 产能 0.67→0.85（健康 build 改后更优）。

**doctrine（8 个）未纳入本批**：审计发现 `build_efficiency.py` 的 `forced_opening` 只匹配
`OpeningBuild`，doctrine id 回退默认开局（4bg）→ 之前"doctrine 审计"实跑的是 4bg、结论作废。
且真局玩家切 doctrine 填 board slot → `OpeningSustainAct` 被 gate 不触发，doctrine 产兵归各自
`sharpy_dummy_class` plan。doctrine 是否摆烂需用填 slot 的真局路径单独测、修对应 plan，另立项。

### 2026-06-16 修复：神族 build-aware sustain 遇折跃门失效（ActUnit 不会 warp-in）

**修正 (Fixed)**

- **根因**（神族 17 build 效率审计 + 真局逐帧定位）：通用 `_build_protoss` sustain 用
  `ActUnit(STALKER, GATEWAY)` 出 gateway 兵，但 sharpy 的 `ActUnit` 只 `cache.own(GATEWAY)`，
  **不含 WARPGATE**（COMMANDCENTER/HATCHERY 有等价特判，GATEWAY 没有）。折跃门研究完成后
  所有 GATEWAY→WARPGATE → builders 恒空、`builder.train()` 对 warpgate 也无效 → **gateway 系
  神族 sustain 出到折跃前那几个兵就卡死**（4bg/dt_rush 卡 3 追猎、余钱冲 7000+、人口卡 28 摆烂）。
  叠加通用 macro **不补任何产能楼** → 8 个 doctrine（沙盒无具体建筑步骤）连 producer 都没有、
  全程 ~0.086 产能。审计结论：17 个神族 build 只有 3 个（robo 系，`ActUnit(IMMORTAL, ROBO)`
  不受 warpgate 影响）健康，14 个摆烂或余钱高。
- **修法**：build-aware sustain 神族路径改用 sharpy `ProtossUnit`（折跃研究完成后自动切
  `WarpUnit` warp-in，未研究/robo/星门兵则等价 `ActUnit` train）；`plan_from_core_units` 给
  神族 gateway 兵把 producer 显式规范成 `GATEWAY`（`UNIT_TRAINED_FROM` 给 `{GATEWAY, WARPGATE}`
  set 无序，曾可能选到 WARPGATE → `GridBuilding(WARPGATE)` 无效）。配合给 build 加 `core_units`
  路由到 build-aware 路径（`GridBuilding` 补产能楼 + 按配比续兵）。
- **proof（4bg，warpgate 最坏 case，A/B 单 seed）**：余钱 5789→857、产能 0.086→0.523、
  opening 完成 null→305s、追猎卡 3→涨到 60、人口卡 28→顶满 200。
- 新增 2 条回归单测（神族 gateway 兵 producer 必须 GATEWAY；robo/星门 producer 正确）。

### 2026-06-15 修复：虫族 build-aware sustain 永不出兵（from_building 用错科技楼）

**修正 (Fixed)**

- **根因**（真局逐帧定位）：`plan_from_core_units` 给虫族兵生成的 `ActUnit` 把
  `from_building` 设成了**科技楼**（ROACHWARREN/SPIRE/HYDRALISKDEN…），而虫族兵是从
  **LARVA** 孵的（飞蛇从 ROACH、爆虫从 ZERGLING）。`roachwarren.train(ROACH)` 是无效调用 →
  **虫族 build-aware sustain 开局后从不产兵**，军队冻结在 opening plan 的上限（蟑螂卡 28），
  84 larva / 12000+ 矿 / 5000+ 气全程闲置、人口卡在 161 摆烂。之前的"虫族机制验证通过"是
  假阳性——飞龙/macro_hatch 等靠开局本身就堆到接近满人口，掩盖了 sustain 不工作。
- **修法**：`ActUnit` 的 `from_building` 统一改用 `UNIT_TRAINED_FROM`（真正的孵化/训练来源：
  LARVA / ZERGLING / ROACH / GATEWAY / BARRACKS），三族一致；科技楼降级为**前置依赖**
  （`_ZERG_TECH_PREREQ`，GridBuilding 确保 1 座存在即可，不当训练者）。神/人路径行为不变。
  新增 2 条回归单测（虫族 ActUnit 必须从 LARVA/ROACH 孵、科技楼只作前置）。

### 2026-06-15 虫族效率优化：指标门统一 180 + 采气优先级 build-aware

**变更 (Changed)**

- **build 效率评分器虫族门统一回人口 180**：人口未到 180 → 余钱/余气/余 larva 都扣分；
  人口 ≥180 → 三者全不扣（买活储备 + 满编阶段，不算浪费）。修正前一版误把虫族门改成
  "人口未满（used<cap）才扣"，导致沙盒 200 上限前一直扣分、飞龙局虚高（237→4470）。三族口径统一。
- **虫族采气优先级 build-aware**（`MacroConfig.gas_per_base`，默认 2）：`OpeningSustainAct` 读本
  build 的 `core_units`——含气耗大的兵（飞蛇/刺蛇/雷兽/爆虫/感染/飞龙等）维持每矿 2 气矿；纯蟑/狗
  这类吃矿为主的 comp 降到每矿 1 气矿。**根因**（roach_hydra 时序铁证：矿被 morph 吃光、气浮 5471）：
  蟑螂吃矿为主（75 矿/25 气），气收入远超兵种气耗 → 气浮 5000+ + 矿荒卡人口。少造气矿把工蜂留在矿上，
  缓解矿不够。

### 2026-06-14 代理建造农民建完被拉去探路阵亡（修复）

**修正 (Fixed)**

- 修复代理建造（野水晶 + 后续建筑）农民"建完跑去对方基地探路阵亡"的 bug。**根因**（真局日志
  铁证）：玩家指令 claim 的农民恰好是当前 ScoutWorker 正在用的探路农民（`scout_tag` 已存下），
  而 `ScoutWorker._pick_scout` 的"排除已 claim 农民"只在**重新挑农民**时生效——已存进 `scout_tag`
  的农民后续每帧靠 `by_tag` 直接拿、绕过排除。于是 ScoutWorker 每帧把这个农民 `move` 去敌方，
  跟玩家的 build/standby 抢控制权，水晶建完后探路的 move 占了上风 → 农民走到对方基地阵亡、
  后续两个 VS 没建成。**修法**：ScoutWorker 每 tick 检查当前 `scout_tag` 是否已进
  `_llm_controlled_tags`/`stealth_worker_tags`（被玩家 claim），是则立即放手（`scout_tag=None`），
  下一 tick 另挑一个自由农民继续探路。新增 3 条单测覆盖（被 claim/偷矿 tag 放手、未被 claim 保留）。

### 2026-06-14 README 普通玩家指令大全

**变更 (Changed)**

- README"你能下哪些操作"大幅扩写：把原本压缩成 L1-L4 四行表的指令说明，展开成
  **16 类分类详表**（切剧本 / 全军战术 / 派兵打出去 / 待命集结守点 / 巡逻 / 出兵集结点 /
  语音编队 / 侦察视野 / 产能 / 野建筑代理建造 / 偷矿 / 开矿扩张 / 技能释放 / 镜头跟随 /
  连续指令 / 释放撤回），每条配真实玩家例句 + 效果说明。内容全部对齐 `docs/llm_prompt/few_shot.md`
  里 bot 真正能执行的指令，不臆造。重点补全用户反馈缺失的：出兵集结点（rally）、野建筑/代理
  建造链、偷矿、派兵攻击/骚扰/火力侦查、两点巡逻。开头说明"带'这里'的指令要先用小地图移镜头"。

### 2026-06-14 房间文字聊天

**新增 (Added)**

- 文字聊天功能：玩家进房后（**对战大厅 + 对局进行中**）可在右下角浮层收发文字消息，
  房间级广播（同房所有在线连接都收到），经 PC server / VPS 转发。入口页（未连接 /
  未进房）不显示，符合"点连接进房后才支持聊天"的约定。
- 后端 `ChatHub`（`src/vibecraft/server/chat.py`）：内存历史（默认 50 条）+ server 自增
  消息 id + server 时间戳；`WsConnection` 加 `chat_send` / `chat_history_req` 两个上行帧，
  消息经 `RoomRegistry.broadcast` 推所有连接。发送方自己也靠广播回显（不本地 append），
  确保各端 id/排序一致。
- 前端 `ChatPanel.vue`（平台级 sibling 浮层，`showMain && amIInRoom` 门控）：折叠为右下角
  气泡（带未读红点），展开为消息列表 + 输入框；本人消息右对齐高亮（靠 `pid===myPid` 判定）；
  进房/重连挂载时拉一次历史。`useWs` 加 `chatMessages` / `myPid` / `sendChat` /
  `requestChatHistory`，消息按 id 去重排序、上限 200 条防移动端内存涨。

**安全 (Security)**

- 聊天文本一律 `{{ }}` 渲染，绝不 `v-html`（name/text 来自不可信客户端，防 XSS）。
- 限频在 server 端（~2 条/秒，前端不可信）；单条 server 截断 500 字。

**修正 (Fixed)**

- 对局中聊天气泡抬高（`raised` prop，`bottom-24`）让位底部指令输入栏，不再遮挡
  语音/文字切换按钮；大厅无输入栏时仍贴底（`bottom-3`）。

### 2026-06-14 入口页信息反馈表单

**新增 (Added)**

- 入口页 `EntryView` 加"信息反馈 / 提建议"按钮 → 弹模态表单（昵称 / 分类[建议/Bug/其他] /
  反馈内容）→ 提交走 `GET /api/feedback` → server 追加到本地 **`logs/feedback.csv`**
  （UTF-8 BOM，Excel 直接打开不乱码），每条记：提交时间 / 昵称 / 分类 / 反馈内容 /
  **IP（经 nginx `X-Forwarded-For` 取真实客户端 IP，直连时取 remote_address）** / User-Agent。
  `logs/` 已 gitignore，反馈数据不进 git。方便未来迭代收集玩家反馈。

### 2026-06-14 开源准备：MIT 许可 + 第三方/Blizzard 合规声明

**新增 (Added)**

- `LICENSE`：MIT（© 2026 catmaniii）。`pyproject.toml` license 由 Proprietary 改 MIT。
- `THIRD_PARTY_NOTICES.md`：第三方合规声明。核查结论——
  - 唯一随仓库打包且被修改的第三方代码是 `vendor/sharpy`（DrInfy sharpy-sc2，**MIT**，
    © 2019），加了 15 处 `# vibecraft:` hook；原 LICENSE + ATTRIBUTION 已随仓库保留、改动
    已标注 → **修改后仍符合 MIT**。
  - 所有 pip / 前端依赖（python-sc2/ares-sc2/pydantic/aiortc/av/websockets/numpy/vue… ）
    均宽松协议（MIT/BSD/Apache），**无 copyleft** → 整体可 MIT。
  - **Blizzard**：StarCraft®/Blizzard® 商标声明 + 非官方粉丝项目 + 须自有正版 SC2 +
    AI/ML API 受 Blizzard "StarCraft II AI and Machine Learning License" 约束（**仅非商业**）。
- README 增"许可 / License"章节（链 LICENSE + THIRD_PARTY_NOTICES + Blizzard 非商业声明）。
- `.gitignore` 忽略 `vendor/aristaeus/`（早期试验残留的本地未授权 vendored bot，未跟踪、
  不发布）。

### 2026-06-14 一键本地部署脚本 + README/ARCHITECTURE 部署文档

**新增 (Added)**

- `scripts/setup-windows.ps1`：一键本地部署。**自动定位 StarCraft II**（SC2PATH →
  `ExecuteInfo.txt` → 注册表 → 常见目录）并持久化 `SC2PATH`；应用黑屏修复（永不关显示器/
  睡眠 + 关屏保）；检查 uv + LLM key。前提是已装 SC2（启动过一次）。
- README 重构为**三类人群**：① 普通玩家怎么玩（操作/指令/控制模型）② 开发者系统架构
  ③ 自行部署（本地一键脚本 + SC2 要求 + 黑屏解决；公网 VPS 买→配→部署全流程）。
- ARCHITECTURE.md 新增"部署架构"节：本地单机 + 公网（nginx 443 SNI 分流 + 反向隧道 +
  coturn）两套拓扑图 + 为什么这么设计（443 SNI / 反向隧道 / graceful）。

### 2026-06-14 修复 PC 闲置黑屏（直播全黑）

**修正 (Fixed)**

- **PC 闲置一段后 PWA 直播画面全黑**（CRD 远程登录唤醒才回画面）：根因是 Windows 闲置
  关显示器（实测 monitor-timeout=900s/15min）→ SC2 停止渲染 → 抓屏抓到黑帧。双保险修：
  ① 系统电源：`monitor-timeout-ac`/`standby-timeout-ac` 改 0（永不关显示器/睡眠）+ 关屏保；
  ② 服务端启动调 `SetThreadExecutionState(ES_CONTINUOUS|ES_DISPLAY_REQUIRED|
  ES_SYSTEM_REQUIRED)`（Windows）告诉系统"推流中别关显示器/别睡"，防电源设置被重置。
  启动日志 `keep_awake_enabled`。

### 2026-06-14 入口页 UX + 推流音量

**变更 (Changed)**

- 入口页 `EntryView`（真机反馈"不知道选没选中服务器/没填昵称为啥连不上"）：
  - **选中的服务器卡片做醒目**：粗边框(border-2)+底色(accent/20)+ring+**实心对勾**+
    "已选中"标；未选显示空心圈 + "点击选择"。
  - **用户名标"* 必填"**；空时输入框红边框（border-danger）提醒。
  - **连接按钮禁用时下方红字明确提示缺什么**（"请先输入昵称"/"请先点选一个服务器"/
    两者都缺）——不再是"按钮灰着点不下去又不知道为啥"。
  - `canConnect` 改用实时输入值（输了名字**即生效**，不必失焦）。
- SC2 推流**音量衰减**（默认 0.5/-6dB，env `VIBECRAFT_AUDIO_GAIN` 可调，用户反馈太吵）。
  做在服务端推流侧（iOS 媒体元素 volume 不可编程设置，客户端调不可靠）。

### 2026-06-14 阶段1：公网前门（nginx 443 SNI 分流 + PC 反向隧道，去 Tailscale 依赖）

**新增 (Added)**

- 让国内手机直连阿里云 VPS 公网地址，**不再依赖 Tailscale funnel**（funnel 国内入口不稳）：
  - VPS **nginx 443 SNI 分流**：`turn.<ip>.sslip.io`→透传 coturn(5349)；`app.<ip>.sslip.io`/
    base→nginx 终止 TLS→反向隧道→PC server。一个 443 同时服务媒体中继和控制面。
  - **PC→VPS SSH 反向隧道**（`deploy/turn/pc-tunnel.ps1`，断线自动重连）：把 PC:8080 暴露到
    VPS 127.0.0.1:18080，nginx 反代到公网。零新开端口（走现成 SSH 22）。
  - `deploy/turn/setup-frontdoor.sh`：扩证书(base/app/turn 三 SAN) + coturn 让出 443→5349 +
    装配 nginx（含 CAP_NET_BIND/SNI map/WS Upgrade/长连 timeout）。
  - 手机连 `https://app.<ip>.sslip.io/?room=...`（HK、国内可达、新 origin 绕过旧 PWA 缓存）；
    媒体 `turns:turn.<ip>.sslip.io:443` 经 nginx→coturn 中继。TURN_DOMAIN 改 `turn.*`。
- 验证：app.* 首页 HTTP 200（nginx→隧道→PC）；/api/turn-credential 经 app.* 下发 turn.* 凭证；
  aiortc 用该凭证 turns:443 经 nginx SNI→coturn 拿到 relay（完整 App 路径端到端 PASS）。

### 2026-06-14 阶段1：TURN 中继接入 App（P2P 自动回落）

**新增 (Added)**

- WebRTC 接入云 TURN 中继：P2P/Tailnet 打不通时（尤其中国手机）自动回落到 turns:443
  中继。两侧（PC aiortc + 手机 browser）各用同一 secret 现签的短期凭证配 iceServers。
  - `server/turn_config.py`：加载 TURN 配置（env 优先 / `.secrets/vibecraft-turn.env`
    回退，缺失 → graceful 无 TURN）+ 现签 coturn REST 短期凭证（HMAC，24h TTL）+ 组
    iceServers（coturn STUN + turn/turns）。
  - `webrtc.py`：`WebRtcManager(turn_config)` 每 offer 现签凭证组 aiortc `RTCIceServer`
    （替换原恒空的 `_ICE_SERVERS`）。
  - `http.py`：`GET /api/turn-credential`（`?room=<token>` 门控）给手机下发 iceServers +
    现签凭证；无配置/token 不符 → 空（手机回退 STUN）。
  - `LiveView.vue`：建 PeerConnection 前 fetch 凭证（带 room、`AbortSignal.timeout(2s)`）；
    有 TURN 只用 coturn STUN（中国可达），fetch 失败才回退 google STUN。
- **不变量**：未配置 TURN（`load_turn_config()→None`）时行为完全不变（纯 P2P，iceServers 空）。
- 独立 Opus 评审后采纳：凭证 TTL 1h→24h（长局 Refresh 不中途断）；有 TURN 不拼 google
  STUN（中国连不上它会拖满 5s gather）；`/api/turn-credential` 加 room-token 门控。

### 2026-06-13 阶段1：云 TURN 中继服务器部署 + 打通自测

**新增 (Added)**

- 多人会合阶段1基础设施：阿里云轻量·香港 部署 coturn（STUN/TURN + TURN over TLS:443
  穿中国防火墙）。`deploy/turn/setup-coturn.sh` 一键脚本（certbot 签证书 + NAT
  external-ip 映射 + 短期 HMAC 凭证 + 私网拒绝防 SSRF + CAP_NET_BIND_SERVICE 绑 443）。
- `deploy/turn/turn_selftest.py`：真实打通自测（aiortc 从 PC 向云申请 relay 候选，
  turn:3478/UDP + turns:443/TLS 两路各验 `typ relay`）。实测两路均 PASS。
- `docs/ops/vps-purchase-spec.md` 采购说明 + `deploy/turn/vibecraft-turn.env.example`
  配置模板（真实凭据走 `.secrets/`，已 gitignore）。
- `.gitignore` 补 `.secrets/` + `*.pem`（旧 `secrets/` 匹配不到带点的 `.secrets/`）。
- `deploy/turn/turn-testpage.py`：挂在 VPS:80 的**零输入手机测试页**（服务端现签短期
  凭证注入 HTML，手机打开网址即自动测 turns:443/turn:3478 是否拿到 relay，不依赖
  GitHub，中国可访问）。systemd 常驻 + certbot 续期 pre/post 钩子停起避免抢 80。

### 2026-06-13 telemetry plan_status 恒 null 真凶（walker 找错 plan 树根）

**修正 (Fixed)**

- **telemetry/面板 plan_status 恒 null（#526，"诊断信号又死了"）**：根因是
  `_walk_plan_tree` 从 `knowledge.ai.build_plan` 取 plan 树根 —— 这个属性**根本不存在**。
  sharpy 把 `create_plan()` 返回的 BuildOrder 包在 `knowledge.managers` 里的
  `ActManager._act`（见 vendor/sharpy `knowledge_bot.py:69 ActManager(self.create_plan)`
  + `act_manager.py self._act = await create_plan()`）。旧代码 `getattr(bot,"build_plan")`
  恒 None → 队列空 → 树根永远拿不到 → 找不到 PlanZoneAttack → plan_status 恒 None。
  **单/多人都坏**（不只多人；之前上一轮 `.act→.action` 修的是 walker 下探逻辑，但 walker
  连树根都拿不到；单测用 SimpleNamespace 手搭 `build_plan` 所以测试绿、真局黑）。
  修：从 `knowledge.managers` 找 `ActManager._act` 作树根（保留 `bot.build_plan` 兜底）。
  实测 solo 4bg：修前 `plan_status` 全 None，修后读到真实 `AttackStatus`（不再 null）。
- 诊断:`extract_tactical_state` 在 plan_status 仍为 None 时落 `plan_dbg` 面包屑
  （`k=/ai=/mgrs=/nodes=/pza=`），正是靠它在真局一眼定位到 `nodes=0`（树根没取到）。
- 鲁棒:多 PlanZoneAttack 的 build（skytoss/blink_harass）优先取 status 非 NotActive 的
  那个（真正在战斗的实例）；isinstance + 类名双判，防 sharpy 双重导入致 isinstance 失效。

### 2026-06-13 启动检查 SC2 后台播放（soundglobal）

**新增 (Added)**

- server 启动时检查 SC2 全局 `Variables.txt` 的 `soundglobal`（#522）：未开启 /
  文件未找到时落清晰**警告**日志（`sound_global_not_enabled` /
  `sound_global_variables_not_found`，带中文操作提示），引导去 SC2 选项→声音→开启
  后台播放。仅警告不阻塞启动（单人局 / 不关心音频时无害）。开源新用户不会知道
  "两玩家同时有声"依赖这个前提，靠这条日志兜底。路径推断含 OneDrive 重定向。

### 2026-06-13 语音间歇失效根治：麦克风 track 死亡自愈

**修正 (Fixed)**

- **语音输入一上来就静默失效**（真机：张三全程语音无效、李四正常，刷新后又好）：
  根因是 `useVoiceInput` 的 `arm()` 一旦置 `armed=true` 就**永不重建管线**，可手机端
  麦克风 track 会在**不触发 visibilitychange** 的情况下死掉（OS 回收 / 别的 app
  抢占 / 权限抖动 / 锁屏）—— 此时 `armed` 仍 true 但 `track.readyState='ended'`，
  按下说话只翻 `forwarding` 开关、worklet 再无帧 → 整条语音静默到刷新页面。
  - 修 A：新增 `isTrackHealthy()`（track `readyState==='live'` 且 ctx 没关）；`arm()`
    改成"armed 但 track 不健康就拆旧重建自愈"，并给 track 挂 `onended` 复位 `armed`。
  - 修 B：`start()` 由 `if (!armed) void arm()` 改无条件 `void arm()`——健康则秒返回，
    track 死了则按下即自愈（旧逻辑在 armed=true 但 track 已死时跳过修复 → 永久静默）。
  - 修 C：`VoiceInput.vue` 回前台的 visibilitychange→arm 靠修 A 真正能自愈（旧版
    armed 时是 no-op）。

**新增 (Added)**

- 服务端每段语音落 `ws_audio_segment_stats`（peak/peak_norm/silent/frames/final_len）
  诊断日志（#527）：真机排查语音失效时区分"客户端发静音（track 死了坐实）"vs
  "有声但 ASR 没解出"。end/cancel 各落一条。

### 2026-06-13 大厅未准备按钮红色高亮

**变更 (Changed)**

- 大厅里**自己未准备**的按钮改红色高亮 + 呼吸闪烁（原灰色看不清，用户要求突出
  提醒"该点准备了"）；别人的未准备仍是暗灰只读。

### 2026-06-13 第五轮反馈：跨种族校验 + 镜头平滑 + 防守灵活 + 自主进攻卡死真凶

**修正 (Fixed)**

- **玩家 × 防守/撤退后 bot 余生不再自主进攻**（真机日志实锤：538s intent 清了、
  stance 钉死 "defend" 到终局）：`_SharpyFacadeBase.set_engagement_stance(None)`
  把 None 当未知值 no-op → stance_override 永远清不掉 → sharpy `_should_attack`
  恒 False。修 = None/"free" 都清除；Protocol 签名改 `str | None`；新增直打真机
  facade 的回归测试（FakeFacade 只记录不判断，旧单测测不出——facade 双实现坑再录一例）。
- **镜头跟随大部队**（用户反馈 2）：焦点重算分频 8→16（频率减半）；镜头改为每
  tick 朝目标点 lerp 滑动（~0.7s 滑到位，离目标 <1.5 格防微抖），切换聚团/瞬跳
  生硬感消除。
- **指令跨种族校验**（用户反馈 1）：人族局说"招两个OB"不再建卡执行——指令涉及
  单位/建筑种族 ≠ 玩家种族时友好拒绝（卡片 failed +"XX是神族单位，人族造不了"）。
  例外（心灵控制等场景）：unit_claim/group_assign 看真实拥有该单位
  （resolve_selector 非空即放行），生产/建造类看拥有目标族农民或主基地。
  未知名词/种族未知/facade 异常一律放行（宁漏不误拦）。`aliases.race_of()`
  懒加载三族 yaml 做 canonical→race 映射。新增 19 条单测。
- **全军防守僵硬**（用户反馈 3a）：防守不再钉死一个点——`_vbc_threatened_zone`
  威胁感知：敌军（power>3，滤散兵/侦察）逼近任何己方基地 → 全军迎击威胁最大的
  zone（带 1.5x 滞回防抖）；无威胁回落玩家指定点/前沿分矿。zone_gather 站位 +
  zone_attack 撤退目标双路生效。新增 11 条 hook 单测 + defend e2e 验收。

### 2026-06-13 双玩家同时有声 + 破音修复 + 大厅状态灯定期刷新

**修正 (Fixed)**

- **SC2 失焦静音（双玩家不能同时有声）**：实证三条外部干预路全部无效
  （Variables.txt 候选变量 / 假激活消息 / WASAPI session 层——session 根本没被
  动过，静音在引擎内部）。最终解：SC2 游戏内 选项→声音 的后台播放选项
  （写入全局 `Variables.txt` 的 **`soundglobal=true`**，bot 实例共用全局配置）。
  用户手动开启后实测两手机同时有声。
- **音乐偶发破音（张三局）**：process loopback 在游戏静音期**不产包**（device
  loopback 是持续产流），缓冲被排空到 0 后贴 0 运行，Windows 调度抖动
  (~15.6ms) 一抖就欠载补零 → 持续音乐里听得见咔哒（人声短促不明显）。
  修=SystemAudioGrabber 起播预缓冲：攒够 60ms 才开始消费、欠载重新蓄水
  （代价 +60ms 音频延迟）；加 underrun/trim 诊断计数（audio_buffer_stats
  日志限频 10s，残余破音可定位是采集欠载还是网络）。
- **大厅四状态灯不更新**：game_status 原本只随对局 raw_events 推（大厅里
  一帧都没有 → 灯停旧态）。心跳循环（5s）顺带推一帧实时 game_status
  （无对局 = idle）。

### 2026-06-13 持续征兵指令（用户反馈 6）

**新增 (Added)**

- **持续征兵**：玩家说"以后新出的虚空都编入 1 队"/"后面新出的追猎都去二矿待命"，
  bot 持续监视新出单位并自动执行，直到玩家 × 取消。按指令组合约定**扩展现有类型**
  不开新类型：`group_assign` 加 `auto_enroll`（新单位自动 ADD 入队；× 停征兵、
  已入队保留；解散编队连带停征兵），`unit_claim` 加 `recruit_new`（standing order
  持续并入新单位并下发同一 task；recruit_new 自动隐含 persistent）。
  Director `_recruit_watchers` 每 tick 全量对比 resolve_selector 找新 tag；
  PWA 出"新XX自动编入N队"可撤销卡片。LLM prompt 例 60/61 + 字段说明 + 快照重 dump。
  单测 +20（全量 2790 passed）。

### 2026-06-13 第四轮反馈：lobby/面板 UI 三条

**变更 (Changed)**

- **宏观策略面板**（用户反馈 2/3）：标题与"切换"按钮同行不换行；右上角 ×
  挪到面板角落（absolute）给标题让位；面板高度固定 280px，内容超出在面板
  内部滚动。
- **大厅准备按钮**（用户反馈 4）：勾选框 → "已准备/未准备"文字按钮（自己的
  可点、实色；别人的只读变暗）；与"房主"徽标等宽（w-16）对齐；电脑行加
  "已就绪"占位对齐名字列。
- **大厅状态灯**（用户反馈 5）：StatusChain 加 `expanded`——大厅里全绿也不再
  折叠成"系统正常"单点，始终显示 手机/服务端/SC2/Bot 四节链。

### 2026-06-13 按 SC2 进程分音频（任务 #516，WebRTC 音频默认重新开启）

**新增 (Added)**

- **per-PID WASAPI process loopback 采集**（`server/process_loopback.py`，Win10
  20H1+）：`ActivateAudioInterfaceAsync` + PROCESS_LOOPBACK 激活参数（ctypes/comtypes
  实现，完成回调必须实现 IAgileObject 否则 E_ILLEGAL_METHOD_CALL——已踩实）。
  只采指定 PID（含子进程树）的渲染音频，直接以 48k/2ch/s16 采集零重采样。
  自验脚本 `scripts/spike_process_loopback.py`（有声/安静双子进程分别采集：
  RMS 2278 vs 0.5，分得干净）。
- `audio_grab` 子进程新增 `--pid` 模式（process loopback），初始化失败（老系统/
  PID 已死）自动回退整机 device loopback；`SystemAudioGrabber(pid=)` 透传。
- 新依赖 comtypes（纯 Python，仅音频子进程使用）。

**修正 (Fixed)**

- **多人局两手机音频混音 + 破音**（2026-06-12 用户反馈，曾默认关闭音频）：
  ① 混音根因 device loopback 抓整机输出 → 改按各自 SC2 实例 PID 分局采集，
  两手机各听各的；② 破音根因共享 SystemAudioGrabber 多消费者分帧 → 共享池
  （引用计数 _acquire/_release_audio）整个删除，每条音频轨**独享**一个 grabber
  子进程，破音结构上消失。`_AUDIO_ENABLED` 默认值恢复 "1"
  （VIBECRAFT_WEBRTC_AUDIO=0 可关）。单人路径 sc2_pid 同样可用，顺带不再混入
  系统其它声音。生产全链路自验（双 grabber 子进程并行按 PID 分离）PASS；
  单测 2770 全过。

### 2026-06-13 第三轮反馈四条 + 两个深层修复

**修正 (Fixed)**

- **#3 面板显示进攻但部队不动**（三层修复，Opus debug 实锤）：① telemetry plan 树
  walker 属性名用错（只下探 .act，sharpy Step/IfElse 实存 .action/.action_else）→
  plan_status 自上线起恒 None 的死信号，修复+回归测试；② 面板"进攻中"原是纯几何
  判定（质心离家>25），与作战层解耦——现以 PlanZoneAttack.status 为真值
  （Attacking/Moving/Protecting→进攻中；Retreat/Withdraw→撤退中；其余→前沿集结；
  取不到回退几何）；③ **真凶**：4bg 主攻闸门是裸 lambda 非锁存 AttackGate，追猎
  阵亡条件翻 False 整条进攻链冻结直到玩家手动进攻——换 AttackGate（对齐其余 15
  剧本既有模式）。干净双 bot 多人对照局已证 bot 自主进攻正常。
- **#4 首句语音必失败**：FunASR 惰性加载（首帧到达时模型未就绪）→ server 启动即
  后台预热（executor 加载不阻塞，失败 graceful 降级）。
- **#1 偷矿采气节奏**：原落地即偷气 → 改"矿优先 + 跟随主经济"：矿工 ≥12 且主经济
  已在采气才开闸（bot 纯矿阶段偷矿不抢先开气）；矿满 16 无条件开闸；STEALTHTRACE
  gas_gate 日志。
- **#2** 面板标题"实时战术决策"→"战术决策"。
- **测试基建炸弹**（排查损失约 1 小时）：测试驱动 bot.on_start 会泄漏 HangWatchdog
  线程到 pytest 进程，30s 墙钟后 os._exit(87) 杀掉整个 suite——以前 suite 短在收尾
  边缘开火（历史上诡异退出码 5/255 的来源），今日新增测试拉长 suite 后开始拦腰杀。
  conftest 全局设 VIBECRAFT_DISABLE_HANG_WATCHDOG=1。全量 2763 passed。
  已知残留：venv 重装后 torch/funasr 在解释器**退出时**原生段错误（不影响测试结果，
  退出码污染），待查。

### 2026-06-12 大厅行排版调整 + 入口页副标题

**变更 (Changed)**

- 大厅 slot 行布局（用户）：[准备按钮(自己,最左)][房主徽标(名前)] 名字 ……
  [×移除][种族下拉(最右)]；删"已准备/未准备"文字标和状态色点（按钮状态+青色填充
  已表达）。入口页副标题改"用嘴打星际在线对战版"。

### 2026-06-12 删旧开局页（三族按钮+开始对局），状态链复用进对局大厅

**变更 (Changed)**

- 用户反馈：开局统一走房间大厅（单人=自己+电脑），原 LaunchView（三族按钮+开始对局
  页）删除；其上的系统状态链（手机/服务器/SC2/bot）复用进对局大厅顶栏（StatusChain
  进 RoomLobby，可选 prop）。非 playing 的主界面兜底为简单等待文案。

### 2026-06-12 退出房间直接回入口页（删中间"加入房间"页）

**变更 (Changed)**

- 用户反馈：退出房间应回到服务器选择页，不要中间页。删 JoinRoomView；退出 =
  lobby_leave + 断连 + 回入口页；刷新后若不在房（含被踢/超时清位）也自动断连回
  入口页（排除"自动 join 在飞"窗口防止误弹，5s 超时兜底）。server 端解耦不变，
  刷新依然不会被拉回房。web 221 全绿。

### 2026-06-12 连接与入房解耦（修"退出房间刷新又回来"）+ 准备色语义

**变更 (Changed)**

- **WS 连接 ≠ 在房间**（用户实测：退出房间后刷新浏览器又被拉回——因握手自动 join）：
  握手只 attach + 下发房间快照；进房 = 显式 `lobby_join` 帧（入口页点 [连接] 自动发
  一次，之后手动）。PWA 新增**未入房视图**（只读名单 + [加入房间]/[切换服务器]，房满
  置灰）；退出房间不再断连，server 清 slot 后 gate 自动切到未入房视图——刷新后停在
  该视图，不会被拉回房。`Room.join` 对已有 slot 的 pid 任意状态幂等（对局中断线重连
  不受影响）。
- **超时踢出补全**：对局结束回大厅时，离线未归的玩家同样走 10s 宽限清位（对局进行中
  仍保位等重连）。
- **lobby 行填充色=准备状态**（用户）：青色填充=房主(点开始即就绪)/已准备/电脑，
  灰=未准备；"自己"靠加粗青边框+"(我)"区分。
- 新单测 5 条 + web 8 条；全量 2758 + web 231 全绿。

### 2026-06-12 多人第二轮真人实测反馈（8 条全处理）

**修正/变更 (Fixed/Changed)**

- **#1** lobby 自己行高亮（亮青边框+底色+"(我)"标）。
- **#2** 多人音频破音 → WebRTC 音频**默认关闭**（`VIBECRAFT_WEBRTC_AUDIO=1` 重开，
  代码保留）；根因=共享采集器双音轨分帧，连同"两手机各听各的"一起排队
  per-PID WASAPI loopback 方案（任务 #516）。
- **#3** 房主免准备：`Room.start` 校验排除房主（点开始=已就绪）；lobby 房主行金色
  徽标替代准备按钮。
- **#4** 视频只抓 SC2 **客户区**（GetClientRect+ClientToScreen，DPI-aware 懒初始化，
  失败回退外接矩形）——不再带标题栏/边框/空白。
- **#5** 竖屏下滚动可把页头挤出视口，但实时画面容器 sticky 置顶永不滚出（横屏回退
  原布局）。
- **#6** 非神族偷矿友好拒绝："偷矿暂只支持神族（人族/虫族支持开发中）"（此前人/虫族
  下令静默无反应——偷矿系统写死神族；完整支持记 TASKS.md 待办 + 任务 #515）。
- **#7** 对局中按钮分化：[结束本局] 仅房主（非房主发 end_game 收 room_error）；
  非房主 [认输]（确认后 surrender 帧 → 自己 bot 优雅退局 → 引擎判对方 Victory →
  自动收场回 lobby）。
- **#8** 房间默认 **2 个位**（引擎多 agent 仅 1v1；单人+多电脑 FFA 未实测，验证后
  再放开）。
- 新单测 16 条 + web 4 条；全量 2753 + web 221 全绿。

### 2026-06-12 多人首轮真人实测反馈修复（PWA 侧）

**修正 (Fixed)**

- **lobby 名单狂闪（客户端根因，反馈 #1）**：useWs 加 **socket 代际守卫**——
  `connect()` 捕获本代实例，四个事件处理器开头 `sock!==ws` 即忽略；`connectNow()`
  先置 null 再 close 旧 socket。旧代 onclose 不再排重连定时器，消除"两连接互顶"。

**新增/变更 (Added/Changed)**

- RoomLobby（反馈 #2/#3/#4/#5）：[退出房间] 按钮（发 lobby_leave → 断连回入口页）；
  空位行内 [+电脑]（房主，种族 + 10 档难度，发 lobby_add_computer 带 index，替代
  全局按钮）；点击空位换位（lobby_take_slot，hover 提示）；移除 realtime 开关 UI
  （帧保留给 selftest）。web 217 测试全绿。

### 2026-06-12 多人首轮真人实测反馈修复（server 侧）

**修正 (Fixed)**

- **lobby 名单狂闪**（用户反馈 #1，server 侧）：lobby 态断线从"立即 leave"改为
  **延迟 10s 宽限**（`_delayed_lobby_leave`：宽限内同 pid 重连则 slot 原封不动）——
  日志实锤客户端重连风暴时"顶旧→leave→join"每 2 秒一轮、全房间名单清空恢复狂闪；
  宽限同时护住手机网络抖动。（客户端根因 socket 代际守卫另修。）

**新增 (Added)**

- `Room.take_slot`（用户反馈 #4 可行部分）：玩家点空位自由换位，种族/ready 随身走，
  房主身份不变；`lobby_take_slot` 帧。组队（队伍1/队伍2）受引擎限制不做：SC2 API
  PlayerSetup 无 team 字段 + 多 agent 仅纯 1v1（spike 实锤），无法表达玩家组队。
- `Room.add_computer(index=...)`（用户反馈 #3）：房主可指定某个空位加电脑（PWA 空位
  行内加电脑 + 难度选择配套）。
- 新单测 7 条（换位/指定位加电脑/延迟 leave 重连保位）。

**说明（非 bug）**

- 用户反馈 #6"房主视频失败另一玩家正常"：日志显示失败设备的 ICE 候选只有手机热点
  网段 + 公网 srflx（无 Tailscale、与 PC 不同段）→ 无可达 UDP 路径，60s 超时。属已知
  网络矩阵（视频需同 WiFi 或装 Tailscale），阶段 1 coturn 即为此场景准备。

### 2026-06-12 多人阶段 0 收官：端到端 selftest 14/14 PASS + 三文档刷新

**新增 (Added)**

- `scripts/multiplayer_selftest.py`：端到端自验——真 BotService + 两个 ws 客户端模拟
  两部手机，完整链路 14 项断言全过：入房/房主、双人 lobby、种族广播、非房主拒开、
  双 SC2 跨进程成局 playing、in_game 广播、**指令路由隔离**（A 的 echo 绝不到 B）、
  双路 snapshot、end_game 收场回 lobby、零 SC2 孤儿。mock LLM + non-realtime。
- 文档：ARCHITECTURE.md 新增「多人联网（阶段 0）」节（架构图+多人不变量+spike 坑）；
  TASKS.md 当前状态/下一步/backlog 刷新；USER_GUIDE.md 新增「多人对战」玩家指引。
  PWA bundle 已重建（入口页+lobby 进 static/）。

### 2026-06-12 多人阶段 0：WS 层总装——RoomService + lobby 帧 + per-player 路由

**变更 (Changed)**

- **RoomService 聚合根**（room_service.py）：Room + MatchOrchestrator + Registry；
  monitor 回调 `_on_player_frame`（整体兜异常不杀 monitor）把帧经
  `registry.connection_of(player_id)` 推给对应玩家，room.state 变化自动广播；
  GameProcess 唯一 owner = orchestrator（M3 无 legacy 双轨）。
- **ws.py 重排**：握手解析 `player`/`pid` query → room.join + per-player attach +
  即发 room_state；新增 lobby_* 帧族（set_race/set_team/ready/set_realtime/
  add_computer/remove_slot/leave/start，RoomError → room_error 帧）；删 per-connection
  status pump（M2，帧下发全部走 monitor 回调，`build_downstream_frames` 抽成纯函数）；
  旧 `start_game` 帧改薄 shim（自动 join+加电脑+ready+start_match，旧流程不破）；
  end_game → stop_match 全停；断线 lobby 态自动 leave、对局中 slot 保留等重连；
  webrtc_offer 传 player_id+sc2_pid（S4），sc2_pid 未就绪回 "sc2 not ready, retry"，
  LiveView 收到后 2.5s 自动重试（最多 8 次）。
- BotService 装配 RoomService（ServiceConfig.max_players=4）。
- 新增 24 条单测（路由不变量"A 的指令绝不进 B 的 down_q"/lobby 流/shim/断线语义），
  全量 2732 + web 209 全绿。

### 2026-06-12 多人阶段 0：WebRTC per-player 化 + 按 SC2 窗口 PID 抓屏

**变更 (Changed)**

- **WebRtcManager 按 player_id 管理 PeerConnection**（M1）：`_pcs`/`_tracks` 改
  dict[player_id,...]，`handle_offer(..., player_id, sc2_pid)` 只 supersede 同玩家
  旧 PC（原"新 offer 关掉所有人"单客户端假设会让多人视频互踢）；每 PC 独立
  `SC2ScreenCapture(pid_filter=sc2_pid)` 只抓对应实例窗口；新增 `close_player`。
  音频共享 SystemAudioGrabber + 引用计数（系统回环本就全局混音，多 grabber 无意义）。
  player_id 默认 "default" + sc2_pid 默认 None → 单人路径行为零变化。
- 子进程无条件起 sc2_pid 上报线程（psutil 轮询子孙 SC2 PID ≤120s →
  `{"kind":"sc2_pid",...}` 上行），`GameProcess.sc2_pid` 记账不外漏；与 focus 线程
  共用 `_poll_own_sc2_pids`。

### 2026-06-12 多人阶段 0：PWA lobby 视图

**新增 (Added)**

- `RoomLobby.vue`：SC2 经典 lobby（中文 UI）——slot 表（状态点/名字/种族下拉/ready
  标）、自己行可改种族+[准备]、房主 [+电脑]/[×移除]/[开始对局]（全员 ready 才亮）、
  房间码+地图+realtime 开关（房主）、starting 全屏进度遮罩。引擎限制落 UI：双真人时
  加电脑置灰。team 字段保留类型不出 UI（引擎仅 1v1）。
- `useWs` 增量：`roomState`/`roomError`（红帯 5s 自清）/`sendLobby`；types.ts 加
  RoomStateFrame/RoomSlot/8 种 lobby 上行帧。App.vue gate：room_state 为
  lobby/starting → RoomLobby；null（旧 server）→ 原行为不变。web 测试 209 全绿。

### 2026-06-12 多人阶段 0：MatchOrchestrator + per-match monitor

**新增 (Added)**

- `src/vibecraft/server/match.py`：房间配置 → 多 GameProcess 编排。`build_plan` 纯函数
  （首个 bot slot=host 建局代填电脑、共享散点 portconfig、窗口横向均分屏宽、只有 host
  抢焦点；solo=原单人路径 mp_role=""，无 legacy 双轨）；`start_match` 为每个
  (player_id, gp) 建 connection 无关的 monitor task（每 GameProcess 恰一个消费者），
  首个 playing → room.mark_in_game，任一 ended/crashed → 全停收场回 lobby +
  on_match_ended 回调；spawn 失败立即清场 re-raise；stop_match 先 cancel monitor 再
  停进程（防递归触发），排除 self-cancel 保证清场逻辑完整。屏宽 DPI-aware 检测
  fallback 1920。11 条单测，全量 2708+ 绿。

### 2026-06-12 引擎硬限制落地：SC2 多 agent 局仅纯 1v1（不能带电脑）

**变更 (Changed)**

- spike 实测：create_game 对"2 个 bot 玩家 + 内置 AI"直接拒绝
  `InvalidPlayerSetup: Only 1v1 is supported when using multiple agents`（Blizzard
  引擎边界）。→ 双真人局不能加电脑（"2 人合作打电脑"在多实例路线不可行）；分队/FFA
  问题随之消失（双真人=纯 1v1）。Room 加两道校验：`add_computer` 拦"已有 2 真人"、
  `start` 拦"双真人+电脑位"（先加电脑后进人场景）。Spike 结论已回填实施 plan。

### 2026-06-12 多人阶段 0：GameProcess 多人分支（mp_role host/join）

**新增 (Added)**

- `GameConfig` 多人字段（全部 picklable）：`mp_role`（""=单人原路径 / host / join）、
  `mp_portconfig_json`、`mp_player_name`、`mp_guest_names`、`mp_computers`、
  `mp_game_time_limit`。`_child_entry` 多人分支走 sc2_multiplayer runner（host 建局
  代填电脑 / join 加入），整分支 try/except → crashed detail（S5）；单人路径零变化。
- **focus 抢窗按 PID 白名单**（S3）：多实例时 focus 线程先 psutil 轮询本子进程的
  SC2 子孙 PID（最多 60s），只 focus 自己的窗口；拿不到回退老行为（按标题）。

### 2026-06-12 多人 spike 排坑：跨进程 host/join 打通（端口选法是真凶）

**修正 (Fixed)**

- **跨进程双 SC2 实例 LAN 成局打通**（E1-E7 二分实验单变量锁定）：
  `Portconfig.contiguous_ports` 连号端口只做"空闲检查"不推进 Windows 顺序分配的
  临时端口游标 → 子进程里 SC2 自己的 websocket 端口被 OS 顺序分配到正好压在游戏
  P2P 端口上 → 引擎绑不了端口，join 被拒 `NetworkError(12) 'Failed to join game:
  537001988'`。修复 = 散点 `Portconfig()`（新 `new_portconfig_json` helper，坑写进
  docstring）。时序/栅栏/窗口参数/realtime 全部排除（E7 证明无栅栏也 PASS）。
- **join 静默失败补检查**：python-sc2 `client.join_game` 不检查响应 error 字段，
  引擎拒绝时静默返回 player_id=0、后续才炸 "A game has not been started yet"。
  runner 新增 `_checked_join_and_play`（player_id==0 → 重试 3 次 → 显式 raise）。
- runner 新增可选 `before_join` 会合钩子（非正确性必需，留给编排层）；smoke 脚本
  新增 --single-process 对照组 / --contiguous-ports 复现坑 / --no-barrier /
  --kill-host-after / --host-delay / --bare-sc2 诊断开关 + 残留 SC2 清理（finally
  scoped kill，不再留黑屏僵尸窗口）。

### 2026-06-12 修偷矿 pending 注册首帧多余调用（修 TestParseErrorIsNoop 红测）

**修正 (Fixed)**

- `StealthCellManager._pending_registered` 初始值 -1 → 0：SNS 字段默认就是 0，首帧
  注册一次 `register_stealth_pending(0)` 纯属多余，且污染"parse error 零副作用"断言
  （test_director::TestParseErrorIsNoop 自 db725e1 起红）。行为不变（出现偷矿 cell
  才注册，归零时数值不同照常重注册）。

### 2026-06-12 多人阶段 0：RoomRegistry 多连接化 + PWA 入口页

**变更 (Changed)**

- **RoomRegistry per-player 多连接**（tokens.py）：单 token 下多玩家各一条连接，同
  player_id 重连顶旧、不同玩家互不干扰；新增 `connection_of` / `player_ids` /
  `broadcast`（单点失败不阻断）；`Connection` Protocol 加 `send_text`。删
  `active_connection`。ws.py 握手暂以 `player_id="default"` 接入（完整玩家握手在
  WS lobby 任务），旧单人流程零变化。

**新增 (Added)**

- **PWA 入口页**（用户名 + 服务器列表）：`useProfile.ts`（localStorage 档案：用户名/
  设备指纹/服务器列表 CRUD/选中态；扫码 ?room= 自动注册当前 origin 并选中）+
  `EntryView.vue`（中文 UI；用户名 + 服务器卡片 + 添加表单 + 连接按钮）。`useWs` 连接
  地址改为优先取选中服务器（ws(s)://…/ws?room=…&player=…&pid=…），保留旧 ?room=
  扫码兼容路径；新增 `connectNow()` 由入口页触发连接。开源多服务器接入形态的落点。
  （bundle 未重建，npm run build 留到联调统一跑。）

### 2026-06-12 多人阶段 0：跨进程 host/join runner + smoke spike 脚本

**新增 (Added)**

- `src/vibecraft/server/sc2_multiplayer.py`：复刻 python-sc2 `_host_game/_join_game` 的
  带窗口参数（resolution/placement）变体 + `build_host_players`（host create_game 列表：
  本方 bot + guest 占位 + Computer）+ Portconfig json round-trip。多实例摆窗的根基。
- `scripts/multiplayer_smoke.py`：spike 脚本，两子进程各起一个 SC2 经共享 Portconfig
  host/join 成一局，SmokeBot 极简宏（16 农民 + BE + BG + 4 叉 a 中央）证明真实交战。
  五模式：基本 / --realtime / --with-computer（敌我观察）/ --kill-host-after /
  --kill-join-after / --join-delay（S1 非对称启动）。kill 模式判定只看存活方。

### 2026-06-12 多人阶段 0：Room 状态机 + slot 模型落地（纯逻辑层）

**新增 (Added)**

- `src/vibecraft/server/room.py`：房间状态机（lobby → starting → in_game → 回 lobby）+
  slot 模型（open/bot/computer/closed，队伍/种族/难度/ready）。首个进房玩家为房主
  （加电脑/踢人/开局/切 realtime）；同 pid 重连幂等不占新位；房主离开自动转移；局终
  slot 保留 ready 清零。M4：realtime 为房间配置；S1：3+ 真人玩家暂拦（spike 实测后放开）。
  19 条单测全绿（test_room.py），mypy/ruff 干净。

### 2026-06-12 多人 plan 通过 Opus 独立评审，4 个设计级问题修订进文档

**变更 (Changed)**

- 新工作约定（CLAUDE.md）：架构/方案设计产出后、实现前，必须派独立 Opus subagent 评审。
  本 plan 即首个评审对象，结论全部采纳进 plan「评审修订」节（优先级高于各 task 原文）：
  M1 WebRTC 按 player_id 管理 PC（修"新 offer 踢掉所有人视频"）；M2 match 生命周期归
  RoomService per-match monitor（修断线失管 + guest 无 pump）；M3 GameProcess 唯一
  owner=MatchOrchestrator（消除 solo 双轨矛盾）；M4 realtime 进房间配置不写死；
  S1-S8 task 内修正（kill-host spike、屏宽检测、focus 按 PID、sc2_pid 就绪门等）。

### 2026-06-12 多人联网阶段 0 实施 plan（文档，无代码改动）

**新增 (Added)**

- `docs/plans/2026-06-12-multiplayer-implementation-plan.md`：11 个 task 的 TDD 实施计划。
  Task 1 spike（跨进程 host/join 闸门）先行；核实的 API 事实——python-sc2 `_host_game/_join_game`
  不透传窗口参数（需自写带 resolution/placement 的 runner 变体）、`Portconfig.as_json` 可跨进程、
  `create_game` PlayerSetup 无 team 字段（组队可行性进 spike 观察）。含 Room 状态机 /
  RoomRegistry 多连接 / MatchOrchestrator / WS lobby 帧 + per-player 路由 / PWA 入口页+lobby /
  视频按 SC2 窗口 PID 分流 / multiplayer_selftest 的完整任务拆解与验收清单。

### 2026-06-12 多人联网设计定稿（设计文档，无代码改动）

**新增 (Added)**

- `docs/plans/2026-06-12-multiplayer-design.md`：多人联网设计真理源。关键拍板——
  单 PC 多 SC2 实例 host/join 成局、每实例一 bot、多手机接入；画面与单机一致 per-player
  WebRTC 推流；公网分三阶段（0 LAN/Tailscale → 1 新加坡轻量 VPS 会合服务（房间+WS 转发+
  信令+coturn）→ 2 会合服务可自托管）；否决 Vercel（serverless 无长连接/无 TURN、国内
  不可达）；通用分队 lobby（slot=玩家bot/内置AI）；PWA 入口页=用户名+服务器列表（无认证）。

### 2026-06-12 在建偷矿算进基地数：玩家下偷矿令后 bot 延后开自己分矿

**变更 (Changed)**

- **bot 运营不再无视在建的偷矿、开冗余分矿**（用户拍板）：真机定位——玩家 t=44 下偷矿令，
  bot t=122 仍开自然分矿，因为 `Expand` 的 `current_active_base_count` 只数 ready+采矿 的基地，
  偷矿 Nexus 那时还没建好（不在 `our_zones_with_minerals`）→ bot 当只有 1 矿、照常开自然。
  改法：Manager 注册 `stealth_pending_base_count`（PENDING/BUILDING 的 cell 数）到 SNS，`Expand`
  把它加进 `active_bases` → 玩家下偷矿令后，bot 把在建偷矿当一片基地、延后/不开自己对应的分矿。
  偷矿被取消/打掉（cell 出局）→ 计数自动减 → bot 补开。只作用于 build 的 `Expand(to_count)` 判断，
  不影响玩家开矿封顶。新增 facade `register_stealth_pending`（双实现 + Protocol audit）。
  真机自验：早下偷矿令 → bot 全程只 1 个自己的基地（自然分矿没开，偷矿 ready 后算第 2 矿），
  对照晚下偷矿令 → bot 照常开到 3 矿。

### 2026-06-12 偷气农民按缺口均分两个气矿（修 6 个堆一个）

**修正 (Fixed)**

- **偷矿 6 个采气农民全堆在一个气矿（应两个各 3）**：真机日志确认派工严重偏斜（一个 assim
  12 次、另一个 3 次）。根因：采气补员/重焊都用 `round-robin（本帧 index % assim 数）`，每帧
  待派列表都从 index 0 起 → 总偏向第一个 assim；超过其 3 个槽的农民挤不进、漂回矿口。
  修法：**按每个 assim 的实际缺口（`ideal - assigned`）建 slot 队列**派工（缺口大的先填），
  6 个农民自然 3+3 分到两个气矿；总数仍受 `gas_cap` 约束（不膨胀）。把补员与重焊合并成一次
  缺口分配。并行 2 局自验：每个 assim 正好派 3 个农民（不再 12 vs 3）。

### 2026-06-12 采气农民焊牢：漂走的重新派回气上（偷矿干净饱和到 16 矿+6 气）

**修正 (Fixed)**

- **6 个登记采气农民里 3 个漂回矿口、真气只 ~3、矿口超采到 19**：临时诊断（`GASTRACE`）
  统计 gas_worker_tags 实际状态，确认 `other_target: 3`（被登记采气、实际在采矿）。根因：
  `order_worker_gather_gas` 偶尔不生效（农民正钻在 assim / mid-cycle、cache-miss → 令被丢），
  但 cell 已乐观把它加进 `gas_worker_tags` → 它继续采矿 → 矿口超采。
  修法：新增 facade `gas_worker_drifted(tag, gas_tags)`（判农民是否漂走：排除"钻进 assim /
  carrying vespene / 正在采该气"等采气循环中状态，其余=漂走），`_tick_gas` 每帧把**真漂走**的
  重新焊回气上（不碰循环中的 → 不打断正常采气）。并行 8 局自验：全部 8 局 `na=16`（矿口正好
  16、不再超采）+ `gas_workers=6` = 干净 **16 矿 + 6 气 = 22**；drain 175→3、outflow=0、倒灌
  `to_kind=stealth` 仍 6。

### 2026-06-12 修主矿农民倒灌偷矿基地采气（偷矿 assimilator 没进 FENCE）

**修正 (Fixed)**

- **主矿/自然农民跑去偷矿基地采气（真机 381 次倒灌）**：临时诊断日志（`FENCETRACE
  drain_detail`）定位——倒灌的 26 条里 24 条 `work_type=ASSIMILATOR`，且该 assim **在
  work_queue 里**。根因：FENCE 集 `stealth_townhall_tags` **只含偷矿 Nexus tag，不含偷矿
  assimilator tag** → 偷矿气矿没被 `DistributeWorkers.generate_worker_queue` 排除 → 进了
  work_queue（有空采气位）→ sharpy 把主矿农民派去偷矿基地采气（走 91 格）。这也连带让
  `ActUnit.is_done` 把偷矿 6 个气位算进主矿 ideal → 主矿过量造农民 → 多的又去倒灌。
  修法两处：① `stealth_townhall_tags` property 并入 `cell.gas_tags`（Nexus + assim 都排除）；
  ② **每 tick 重注册** FENCE 集到 SNS（带变化门）——偷矿 assim 是 Nexus 建好后才陆续建的，
  原来只在 settle 注册一次时 `gas_tags` 还空 → assim 永远进不了 FENCE 集。
- **偷气 `gas_worker_tags` 膨胀到 22（矿口反被挤超采）**：assim 进 FENCE 后没了
  `DistributeWorkers` 帮填，旧 `_tick_gas` 按 `deficit=ideal-assigned` 派工，引擎 `assigned`
  滞后 → deficit 一直 >0 → 每帧把不同农民塞进 `gas_worker_tags`、永不收敛。改成**按总气位
  `gas_cap=Σideal` 封顶补员**（不看滞后的引擎 assigned，看 cell 自己计数），超额踢回采矿。
  并行 8 局自验：真·倒灌 `to_kind=stealth` **381→5**、`from_kind=stealth` 155→3、outflow=0，
  `gas_worker_tags` 封顶在 6（不再膨胀），8/8 cell 都到 22 农民。

### 2026-06-12 偷气 builder gate 改超时释放（修 231 次 churn → 两个气矿稳定建成）

**修正 (Fixed)**

- **偷气 assimilator 建不稳（并行 8 局有的 0 气、有的 3 气、有的 6 气）**：gate
  （`gas_builder_tag`）用 `_is_tag_alive(builder)` 判释放 —— 采气农民周期性钻进 assim 时
  builder 单帧从 `bot.units` 消失 → gate 被释放 → 每帧重派，真机一个 builder 朝同一 geyser
  重派 **231 次**、`order_probe_build_gas` 因 cache-miss 丢弃 **151 条**建造令 → assim 经常建不成。
  修法：① gate **只**在「assim 建好」或「超时 `_GAS_BUILD_TIMEOUT_S`(6 游戏秒)没建成」时释放，
  不再因 builder 单帧 cache-miss 释放；② builder 选「确认在 cache 且非采气」的农民（旧
  `next(iter(worker_tags))` 可能选到正钻在 assim 里的采气农民 → 一发 order 就 cache-miss）。
  `StealthCell` 加 `gas_builder_since`。并行 8 局自验：**8/8 cell 都爬满到 22**（16 矿 + 6 气），
  两个气矿都稳定建成。

### 2026-06-11 偷矿饱和：采气农民不再被误判死亡 + 两个气矿都建（16+6=22）

**修正 (Fixed)**

- **偷矿 cell 卡在 19（16 矿超采 + 3 气）到不了饱和 22**：并行 4 局饱和自验（全军防守拖长局）
  定位两个叠加 bug：
  1. **采气农民被当死亡误删**：Protoss 农民采气会周期性"钻进"assimilator，那几帧从
     `bot.units` 消失 → `_is_tag_alive` 判 False → 死亡清理把正常采气农民当死亡删掉 →
     `gas_worker_tags` 永远清零、采气补不满，多出的农民全堆去矿口（超采到 18-19）。
     修法：**grace-period 死亡判定** —— 农民连续消失超 `_DEAD_GRACE_S`(4 游戏秒) 才真判死，
     重现即清计时（采气钻出来 1-2 秒内就回来 → 不删）；也顺带兜住新孵化农民出生那帧的
     cache-miss。`StealthCell` 加 `worker_missing_since` 计时字段。
  2. **只建 1 个气矿（3 气封顶）**：`_GAS_RADIUS=8.0` 只够到基地两个 geyser 里的 1 个 →
     只建 1 个 assimilator = 最多 3 气，total=16+3=19。改 `_GAS_RADIUS=12.0`（罩住同基地两个
     geyser ~9-11 格，够不到 >20 格外的邻基地）。
  并行 4 局自验（`scripts/stealth_saturation_selftest.py`，新增）：3/4 局 cell 爬满到
  **22（16 矿 + 6 气）**，drain=0、outflow=0（农民全留在偷矿基地）；剩 1 局因 snap 到的角落
  基地只有 1 个可达 geyser 停在 19（基地几何，非逻辑 bug）。

### 2026-06-11 修回归：偷矿自产农民被主动 FENCE 误赶回主矿（cell 长不起来）

**修正 (Fixed)**

- **偷矿农民回主矿、cell 卡在 1-4 个长不起来**：真机 telemetry + 新加的 ECONTRACE 日志定位——
  偷矿自产农民**出生那一帧 `set_unit_role(LLM_CONTROLLED)` cache-miss**（newborn 还没进 bot 单位
  缓存，报 `not found in cache`）→ 被 `adopt_newborn` 加进 `worker_tags` 但**没 Reserve 上** →
  以 Gathering role 混进 `DistributeWorkers.worker_dict[stealth_nexus]` → 被上一条刚加的**主动
  FENCE 当主矿漂移农民赶回主矿**（真机 22 次 `ECONTRACE from_kind=stealth→main`，dist=86；83 次
  train 但 worker_count 卡在 4，nexus_assigned 0-2）。这是主动 FENCE 引入的回归（之前被动 FENCE
  不驱逐，un-Reserved 自产农民还能留在偷矿基地）。
  修法：主动 FENCE 改 **tag-aware** —— drifter = `worker_dict[stealth_nexus]` 里**不在
  `stealth_worker_tags`** 的 tag（自产农民含 cache-miss newborn 都在此集合），只赶这些；并改写
  `worker_dict[stealth_nexus]` 为只剩 drifter，让 `execute()` 的驱逐选择也不会误选自产农民。
  真机自验（`stealth_mine_selftest`）：cell worker_count 从卡死 1-4 → **稳定爬升到 12**，
  nexus_assigned 与 worker_count 同步（农民留在偷矿基地采矿）；stealth→main 驱逐 155→个位数残留
  （仅建立期 cache-miss 窗口短暂误赶，cell 自愈）。
  覆盖 `TestDistributeWorkersFence` +2 case（自产农民不被赶 / 全自产不进队列）。

### 2026-06-11 偷矿状态进 telemetry（offline 可观测，含 DRAIN 信号）

**新增 (Added)**

- **`telemetry.jsonl` 每帧快照现在带 `stealth_cells`**：systematic-debugging 定位发现偷矿 cell
  状态历来**只**存在于 director 的 UI 快照（走手机 websocket，易失）+ `STEALTHTRACE` server 日志
  （不在 per-game 目录、非结构化），**telemetry.jsonl 从来不带** → 真机偷矿没有离线结构化记录，
  排障只能靠 self-test（这也是之前"telemetry 里看不到偷矿"的真相：测量缺口，非功能 bug）。
  补 `telemetry.extract_stealth_cells(bot)` + `build_snapshot_record(stealth_cells=...)`，每个 cell 落：
  `cell_id / state / location / worker_count / mineral_workers / gas_workers / has_gas / nexus_assigned`。
  **`nexus_assigned`（SC2 引擎视角偷矿 Nexus 采矿农民数）是 FENCE 健康信号**：`> worker_count`
  = 主矿农民倒灌（DRAIN），`< worker_count` = 偷矿农民外流（OUTFLOW），离线直接判。
  真机自验（`stealth_mine_selftest --no-multi-cell`）确认 telemetry 全程记录 cell building→mining
  推进 + nexus_assigned（150/198 帧带 cell 状态）。

### 2026-06-11 经济可观测：worker 跨基地调度结构化日志（"主矿往分矿派农民"可观测）

**新增 (Added)**

- **`ECONTRACE worker_transfer` 结构化日志**：诊断"主矿是不是在往分矿派农民、派了几个、
  派去哪个分矿"时发现当前日志覆盖不足 —— 只有偷矿方向的 `DRAIN_ALARM` + 无标签的
  `base_saturation` 快照（每 ~2s、纯计数、位置列表无 tag、分不清主/自然/偷矿）。补一条事件级
  日志：在 `DistributeWorkers.assign_to_work`（该 plan 唯一的 worker 调度 chokepoint）加 hook，
  农民被调去的**目标基地 ≠ 来源基地**时打一行 `ECONTRACE worker_transfer tag=.. from_kind=..
  from_tag=.. from_pos=.. to_kind=.. to_tag=.. to_pos=.. dist=..`，`from_kind`/`to_kind` 分类成
  main/natural/expN/stealth（按到 `own_main_zone` 距离排序）。同基地内换矿点不打（避免噪音）；
  偷矿驱逐方向（`from_kind=stealth`）也顺带可观测。走 `logging.getLogger("vibecraft.econtrace")`
  （INFO，进 server 日志文件）。vendor sharpy patch（marker + audit + doc §10 + `TestWorkerTransferLog`
  三条 case）。

### 2026-06-11 偷矿主动 FENCE（双向隔离）：驱逐倒灌进偷矿基地的主矿农民

**修正 (Fixed)**

- **主矿农民倒灌进偷矿基地后卡死、没机制赶走（DRAIN）**：真机 `game_20260611_012836` 偷矿
  Nexus `assigned_harvesters=5` 但本 cell 自产只 2，持续 1152 帧告警（DRAIN_ALARM）。根因：
  旧 FENCE（`DistributeWorkers.generate_worker_queue` 把 stealth Nexus 从 work_queue 排除）**只是
  被动"不路由新农民进来"，却无法驱逐已经漂进来采矿的非 stealth 农民** —— stealth Nexus 一旦
  被排除出工作队列，平衡器的"负 available 驱逐"也不再覆盖它，倒灌的主矿农民就永久卡在偷矿
  矿区采矿（既不被认领进 cell，也不被赶回主矿；`_find_unclaimed_probes_near` 的 idle 过滤又让
  正在采矿的它们对认领逻辑不可见）。
  修法：把被动 FENCE 升级为**主动双向 FENCE**。利用 sharpy `calculate_workers` 的 `only_roles`
  过滤——Reserved（LLM_CONTROLLED）的 stealth 农民本就不进 `worker_dict`，故 `worker_dict[stealth
  Nexus]` 只剩"漂进来的非 Reserved 主矿农民"。有则发 `WorkStatus(building, -drifters*10000,
  force_exit=True)`（复用 enemy-zone 撤离机制）让平衡器逐帧把它们赶回主矿；没有则照旧跳过（不
  作为 add 目标，仍防路由）。`force_exit=True` 保证即使主矿满采也有去处。绝不碰 Reserved stealth
  农民（被 only_roles 过滤在 worker_dict 之外）。这样无论倒灌从哪条路径进来，偷矿基地都自愈成
  封闭经济（满足"无 倒灌 main→stealth"的双向隔离要求）。
  覆盖 `tests/unit/test_sharpy_vibecraft_hooks.py::TestDistributeWorkersFence`（新增有/无漂移农民
  两条 case）+ patch audit。

### 2026-06-11 偷矿农民彻底不被抓去探路（stealth_worker_tags 写进 SNS + 出生即注册）

**修正 (Fixed)**

- **偷矿农民被 ScoutWorker 抓去探路（残留）**：上次 scout 排除只查 `_llm_controlled_tags`，
  偷矿农民因瞬时 cache miss 被 `_refresh_llm_controlled_roles` 误删那一帧 → 被 scout 抢走、
  派去敌方阵亡（真机：偷矿农民 vs scout 重叠 3 个、cell 卡在 3）。`stealth_worker_tags` 本该
  作为更稳的排除信号，但**该字段从未写进 `knowledge.vibecraft`**（跟之前账目分离同一个坑）。
  修法：① 加 `stealth_worker_tags` 到 SNS + `facade.register_stealth_workers`，Manager 每帧注册；
  ② `on_unit_created` adopt 农民后**立即**写 SNS（不等下一帧 on_tick，关掉那 1 帧 race）。
  真机验证：偷矿农民被抓去探路 = **0**（3→1→0），ScoutWorker 只抓主矿农民。

### 2026-06-11 WP6 UI 需求1：偷矿显示为指令卡（含实时农民数）/ 需求2：release 弹通知

**新增 (Added)**

- **偷矿指令卡**（需求1）：`stealth_mine` directive 现在在指令卡列表（`CommandCardStack`）里
  显示一张 L2 卡，和其它玩家指令（切剧本/派单位/战术）一样，玩家可 × 撤销。卡片实时显示
  该偷矿点的**采矿农民数 / 采气农民数**（每帧 snapshot 刷新）。
  - 后端：`_build_command_cards` 加 `STEALTH_MINE` case；`_apply_to_facade` 存
    `_directive_to_cell_id` / `_cell_id_to_directive_id` 双向映射；`build_snapshot`
    stealth_cells 增加 `mineral_workers` / `gas_workers` 字段。
  - 前端：`CommandCard.vue` 加 `stealth_workers` 农民数行（采气=0 时只显示采矿）；
    `types.ts` 同步。
- **偷矿点被攻击/发现弹通知**（需求2）：偷矿基地被摧毁或撤离（`_release_cell`）时，
  通过 `stealth.cell_released` event 实时推送给手机 PWA，显示固定顶部 toast（5s 自动消失）。
  - 后端：`StealthCellManager.pending_release_events` 列表，`_release_cell` 填入；
    director on_tick 后 drain → `_push_event` + 清理对应 directive 卡。
  - 前端：`useWs.ts` 新增 `lastStealthRelease` ref；新建 `StealthReleaseToast.vue`
    （暗红边框 toast，位于顶部 fixed）。

**变更 (Changed)**

- **去掉 `StealthCellsCard.vue` 独立面板**：偷矿信息移入指令卡列表，独立面板多余，已删除。
  `CockpitView.vue` 相应更新（删除 `stealthCells` prop，改传 `lastStealthRelease`）。

### 2026-06-11 有偷矿基地时主矿满采就停产农民（修闲置 + 修账目分离空操作）

**修正 (Fixed)**

- **主矿满采还一直造农民、闲置**（`ActUnit.is_done` vendor patch）：bot 主力农民产线的
  staged_cap 是为"提前造农民转去新分矿"设计的、常 > 主矿当前 ideal。有偷矿基地时多产的农民
  **没法转去偷矿基地**（FENCE 隔离）→ 在主矿堆着闲置（玩家观察）。修法：有偷矿基地时主矿
  满采（非 stealth 基地 `assigned >= ideal`，随开矿/枯竭动态）即停产，偷矿基地自产自补；
  主矿扩张 → ideal 涨 → 自动接着产。
- **账目分离一直是空操作**：早先 `ActUnit.get_unit_count` 减 `stealth_worker_tags` 来排除
  偷矿农民——但 `stealth_worker_tags` 从未写进 `knowledge.vibecraft`（只是 Manager 的 property）
  → `getattr` 恒 None → 减法从不执行。已改用上面 `is_done` 方案（直接用 SNS 里的
  `stealth_townhall_tags` + 实时 ideal/assigned，不依赖 worker tag 集合）。

### 2026-06-11 偷矿农民不被抓去探路（ScoutWorker 排除偷矿/claim 农民）

**修正 (Fixed)**

- **偷矿农民被派去探路**（`scout_worker.py::_pick_scout`）：ScoutWorker 用
  `workers.closest_to(enemy_start)` 挑探路农民，偷矿点常在敌方侧 → 偷矿农民离敌最近 → 被抓去
  探路（玩家实测："偷矿农民偷完被派去探路"，被迫手动"探路农民回去吧"）。修法：`_pick_scout`
  排除 `_llm_controlled_tags`（含偷矿农民每帧 ensure 的 Reserved 集合）+ `stealth_worker_tags`
  → 偷矿农民只采偷矿基地的矿，不接探路等其它任务。只剩偷矿农民时不派探路。

### 2026-06-11 偷矿 builder 防被抢（主矿只出 1 个 founding builder）

**修正 (Fixed)**

- **主矿派多个农民去偷矿**（`_tick_building` 每帧 ensure builder Reserved）：偷矿 Nexus 的
  founding builder（建 Nexus 必需，从主矿派 1 个）走半张地图途中被 DistributeWorkers 抢回
  主矿 → 丢失 → 重新认领第 2 个（玩家观察"主矿派 2 个农民去偷矿"）。修法：BUILDING 阶段每帧
  把 builder 并回 `_llm_controlled_tags`（保持 Reserved）→ 不被抢 → 主矿只出 1 个。
  注：founding builder 是建 Nexus 的必需品（无 Nexus 无法本地产农民），建完即成偷矿基地第一个
  农民、不回主矿（最小化跨图暴露）。

### 2026-06-11 偷矿手机实测两修：农民防外流 + 偷矿基地星空自我加速

手机真机实测（逻辑对、无倒灌），两个新问题：

**修正 (Fixed)**

- **偷矿农民被拉回主矿（防外流）**（`facade.ensure_units_reserved` + Manager 每 tick 调）：
  偷矿农民若因瞬时 cache miss / 其它路径掉出 `_llm_controlled_tags`（不再被
  `_refresh_llm_controlled_roles` 每帧 re-Reserve）→ 被 DistributeWorkers 当空闲工人拉回
  主矿。修法：Manager 每 tick 对本 cell 全部农民 `ensure_units_reserved`（并回
  `_llm_controlled_tags`）。真机验证 OUTFLOW_ALARM=0（新增外流告警守卫）。

**新增 (Added)**

- **偷矿基地成长期星空自我加速**（`facade.cast_chrono_on_nexus` + 4 个 chrono plan
  vendor patch + `stealth_chrono_reserved_tags`）：偷矿 Nexus 成长期（农民 < total_target）
  用自己能量给自己加速产农民；满采后停止 → 能量释放回 bot 公共 chrono 池（家里科技/建筑用）。
  - bot 的 ChronoUnit / ChronoTech / ChronoBuilding / ChronoAnyTech 都不拿预留的偷矿 Nexus
    当**能量源**；ChronoUnit 额外不拿它当**加速目标**（否则用主矿能量先 boost 了偷矿基地 →
    偷矿 Nexus 自己能量闲置，玩家观察"星空要塞没用过"）。
  - 非偷矿局零影响（`stealth_chrono_reserved_tags` 空 → 各 plan 不跳过任何 Nexus）；
    `patch audit` + `docs/sharpy-patches.md §12` 同步。

### 2026-06-11 偷矿动态双 cap：16 采矿 + 6 采气，矿/气枯竭自动刷新（用户）

WP4b 原模型错（"worker_target=16 总额内重分配"）。改成动态双 cap：

**变更 (Changed)**

- **采矿封顶 = 偷矿 Nexus 实时 `ideal_harvesters`**（矿点数×2，采空自动降）；**采气封顶 =
  assimilator 实时 `ideal` 之和**（3/口，采空变 0）；**总额 = 采矿 + 采气**（满矿满气 ~16+6=22）。
  两个 cap 每帧实时查，矿/气枯竭时跟 SC2 ideal 机制**自动刷新**（停止再补农民）。
- `StealthCell` 加 `live_total_target`（adopt_newborn 封顶用，含气矿名额，否则采气农民
  超过 16 不被认领 → 被 DistributeWorkers 抢去主矿）。

**修正 (Fixed)**

- **气矿建造重复派工**（`_tick_gas` + `gas_builder_tag`/`gas_ready_baseline`）：assim 在路上
  （gas_buildings 还查不到）时每帧又派一个农民去建同一个 → 农民被反复抽走/路上阵亡 →
  cell 长不起来（实测 gas_build_started 8 次、cell 卡 11）。修法：一次只建一个，
  in-flight 时不重派；assim 建好释放 builder 并让它回去采矿。

### 2026-06-11 偷矿账目分离：主力农民产线排除 stealth（修主矿超产）

长局真机自验 + 玩家观察"主矿一直派农民"定位：bot 主力农民产线 `ActUnit(PROBE, NEXUS,
staged_cap)` 没排除 stealth → stealth Nexus 让 NEXUS 数 +1 解锁更高 cap 档 + stealth 农民
占 cap 名额 → 主矿超产堆农民（实测 1 基地堆到 35）。WP4 只修了 `_tick_worker_saturation`
（次要路径），漏了主力产线。

**修正 (Fixed)**

- **`ActUnit.get_unit_count`**（vendor，仅农民产线）：worker count 减 `len(stealth_worker_tags)`
  → cap 只约束主矿农民。
- **`ActUnit.builders`**（vendor，仅农民产线）：排除 stealth Nexus（不在那造农民，避免双产）。
- **`persistent_macro` 农民 cap 档 gate**：`UnitExists(NEXUS, n)` → `RequireCustom(非 stealth
  NEXUS >= n)`，stealth Nexus 不顶高农民 cap 档。
- 三处对**非偷矿局零影响**（stealth 集合空 → 全 no-op）。patch audit + `docs/sharpy-patches.md` §11 同步。

### 2026-06-11 偷矿建造可靠性：修"建出多个 Nexus" + "建不成"

真机两轮定位偷矿 Nexus 建造不稳：

**修正 (Fixed)**

- **建出多个 Nexus**（`StealthCellManager._tick_building`）：原"每帧重发建造令"+ order_probe_build
  落点缓存失效检测误触发 → 落点在 3 个坐标间抖 → 农民被反复改派建出 2-3 个 Nexus（bases 虚高，
  玩家观察"偷矿修两个基地"）。
- **建不成**（同上）：改"只在 builder idle 才重发"又过头 → builder 走路途中不重发 → 卡死建不成。
- **定稿**：每帧重发把 builder 推过去（远程建造可靠），但 `_any_nexus_near` 一旦检测到 Nexus
  在建就**停止重发** → 只建一个。builder 死/丢回 PENDING 续建。
- **倒灌告警守卫**（`_townhall_assigned` + DRAIN_ALARM）：偷矿 Nexus bot 视角采矿农民数 >
  cell 自产数时告警（FENCE 漏）。真机自验证实**无倒灌**（nexus_assigned 始终 ≤ cell_workers，
  偷矿农民全自产、主矿不倒灌）。
- `scripts/stealth_mine_selftest.py` 加 `--cap-expand`（封 bot 开矿便于干净测倒灌）。

### 2026-06-11 偷矿 WP4b：偷气（建 assimilator + 派农民采气，worker_target 内重分配）

**新增 (Added)**

- **`StealthCell.with_gas` + `gas_worker_tags` 字段**（`cell.py`）：`with_gas=True` 表示该
  cell 启用偷气；`gas_worker_tags` 跟踪已分配给采气的农民子集（防每帧矿/气抖动）。
  `create_cell` 从 payload 拷 `with_gas`。
- **4 个新 facade 方法**（`Sc2Facade` Protocol + `FakeFacade` + `_SharpyFacadeBase`
  三处同步，跑 Protocol audit 验证）：
  - `find_stealth_geysers(point, radius)` → 返回半径内**未建 assimilator** 的 geyser `(tag, pos)` 列表
    （真机：`vespene_geyser.closer_than` + 过滤已有 `gas_buildings.closer_than(1.0, pos)`）。
  - `order_probe_build_gas(probe_tag, geyser_tag)` → 命令 probe 在 geyser 上建 assimilator
    （真机：`worker.build(UnitTypeId.ASSIMILATOR, geyser)`）。
  - `find_stealth_gas_buildings(point, radius)` → 返回半径内 ready assimilator 的
    `(tag, assigned_harvesters, ideal_harvesters)` 列表。
  - `order_worker_gather_gas(worker_tag, gas_building_tag)` → 命令 worker 采气
    （真机：`worker.gather(gas_building)`）。
- **`_tick_mining` 气矿步骤**（`StealthCellManager._tick_gas`，仅 `with_gas=True`）：
  - 步骤 A：ready assimilator < 2 且有未建 geyser → 派一个 cell 农民建 assimilator。
  - 步骤 B：对每个 deficit（ideal > assigned）的 ready assimilator，从 `worker_tags -
    gas_worker_tags` 里挑 worker 派 `order_worker_gather_gas`，加入 `gas_worker_tags`（防抖）。
  - `gas_tags` 同步 ready assimilator tag；dead worker 清理同步清 `gas_worker_tags`。
  - 日志：`gas_build_started` / `gas_saturated`。
- **偷气不新增农民**：`gas_worker_tags` 是 `worker_tags` 子集，worker_target 仍是总上限，
  偷气只是把已有农民的一部分改去采气，不打破封顶逻辑。

### 2026-06-10 偷矿长局自验定位修复：产线交接 + 认领封顶

长局真机自验暴露偷矿经济链两个 bug（mock facade 单测抓不到）：

**修正 (Fixed)**

- **出生即认领（核心）**（`StealthCellManager.adopt_newborn` + `common_bot.on_unit_created`
  钩子）：偷矿 Nexus 训练的农民出生时是普通 role，bot 全局 DistributeWorkers **抢先**把它
  派去主矿采矿（走掉），偷矿 cell 每帧 `_tick_mining` 认领来不及（实测 train 77 次、认领
  仅 1，农民不增长）。修法：在 `on_unit_created`（出生那一刻、早于任何 plan）判断农民是否
  生在某 MINING cell 的 Nexus 旁 → 是则当场标 Reserved + 认领 + 下本地采矿令，
  DistributeWorkers 再跑就跳过它。真机验证：`newborn_adopted` 出现、偷矿农民从 1 增长到 10。
- **认领数封顶 worker_target + 只认领 idle**（`StealthCellManager._tick_mining` +
  `_find_unclaimed_probes_near` idle 过滤）：认领步骤原无上限 → 长局把附近所有未认领农民
  无脑全抓（实测涨到 43，一个矿堆 37、主矿被抽到 4）。修法：① 认领数封顶到
  `worker_target - 当前农民数`；② 只认领 idle 农民（新孵化的偷矿农民是 idle，bot 正在采矿
  的工人非 idle → 不偷 bot 工人，防 snapped expansion 撞 bot 分矿时误收）。

### 2026-06-10 偷矿真机自验定位修复：落点吸附 expansion + 远程建造每帧重发

真机自验（`scripts/stealth_mine_selftest.py`）跑出 `mining_started=0`（Nexus 永远建不成），
单测因 mock facade 抓不到。Opus debug 定位 + 修复：

**修正 (Fixed)**

- **偷矿 Nexus 落点吸附到最近 expansion**（`facade.nearest_expansion` 三处实现 +
  `StealthCellManager._tick_pending` + `StealthCell.point_snapped`）：偷矿 Nexus 是采矿
  基地，玩家点原始坐标常落在无矿/不可建处 → SC2 拒建（日志 `orders_after=[]`、农民
  没动）。PENDING 首帧把 `cell.point` 吸附到最近 expansion location（有矿 + 可放 Nexus）
  再建；吸附后下游 settle 检测 / 本地采矿都用同一坐标。真机验证：`point_snapped
  from=(75,150) to=(91.5,109.5)` → `mining_started`（修复前恒 0）。
- **远程建造每帧重发建造令**（`StealthCellManager._tick_building`）：偷矿是远程建造，
  builder 走半张地图，单次 `order_probe_build` 长途中被 sharpy 抢人/打断 → 建不成。
  BUILDING 态每帧重发（`cache_key=cell_id` 锁稳落点，与 CLAUDE.md 代理建造链"每帧重发
  压过 sharpy"约定一致）；builder 阵亡则回退 PENDING 重新认领续建。
- **`order_probe_build` Protocol 签名补 `cache_key`**（`facade.py` Protocol）：
  FakeFacade / `_SharpyFacadeBase` 早已支持，Protocol 声明补齐。

### 2026-06-10 偷矿 WP7：LLM prompt 支持 stealth_mine（含多片）+ 真机自验 harness

**新增 (Added)**

- **stealth_mine directive 说明**（`docs/llm_prompt/rules.md`）：新增
  `====== 偷矿（stealth_mine）======` 规则段，说明触发条件、`point=[0,0]` 占位
  + Director 注入 camera_point、字段含义（worker_target / with_gas / on_attack）、
  无 done_when/timeout、多片偷矿各发一条。

- **偷矿 few_shot 示例**（`docs/llm_prompt/few_shot.md`）：新增例 57-59：
  - 例 57：「在这偷矿」→ `stealth_mine(point=[0,0], worker_target=16, ...)`
  - 例 58：「多派点农民」→ `worker_target=20`；「不要偷气」→ `with_gas=false`
  - 例 59：「偷两个矿点」→ 两条 stealth_mine（不同 point 时需分两次说话）

- **`scripts/stealth_mine_selftest.py`**：真机自验 harness（照 proxy_chain_selftest
  模式）。mock LLM + non-realtime fast + STEALTHTRACE 日志 grep，验证：
  1. 单 cell：`stealth_mine_applied` + `cell_created` + `building_started` 出现。
  2. （长跑 ≥150s）`mining_started` 出现（Nexus 落地）。
  3. 多 cell：两条 stealth_mine 产生两个不同 cell_id，各自 `building_started`。
  验证点 3（主矿不倒灌）和验证点 5（受击交还）说明手动核查/单测覆盖。

**变更 (Changed)**

- **`_inject_camera_point`**（`src/vibecraft/bot/director.py`）：新增
  `StealthMinePayload` 分支——当 `payload.point == (0.0, 0.0)` 时注入
  `camera_point`（与 BuildAtPayload 的 `named_spot="camera"` 机制类似）。
  LLM 用 `[0, 0]` 占位表达"镜头处"，Director 运行时替换实际坐标。

- **`docs/llm_system_prompt.md`**：重 dump（`scripts/dump_llm_prompt.py`），
  包含新增的 stealth_mine rules 段 + 3 个 few_shot 例。

---

### 2026-06-10 偷矿 WP6：多 cell 并行验证 + snapshot stealth_cells + PWA 偷矿点显示

**新增 (Added)**

- **`TestMultiCellParallelNoConflict`**（`tests/unit/test_stealth_manager.py`）：5 条多 cell 并行无串台专项测试，覆盖：
  1. 同时 create 3 个不同 point 的 cell → cell_id {1,2,3} 不重复，point 各自正确。
  2. cell A BUILDING + cell B MINING 同帧 tick → A 进 MINING，B 保持 MINING，worker_tags 无重叠。
  3. cell A RELEASED → B 的 nexus_tag 仍在 stealth_townhall_tags，B 的农民不被 release。
  4. 3 个 MINING cell 的 stealth_townhall_tags / stealth_worker_tags 并集正确无重叠。
  5. PENDING / BUILDING / MINING 三 cell 同帧 tick，各自走各自分支，无互相影响。

- **`build_snapshot` 新字段 `stealth_cells`**（`src/vibecraft/bot/director.py`）：
  列表形式透传给 PWA，每个 cell 一项：`{cell_id, location:[x,y], worker_count, state, has_gas}`。
  无 cell 时 `stealth_cells: []`。`worker_count` 用 `len(worker_tags)`（与 WP4 pruning 节奏一致）。
  `has_gas` 预留字段，当前恒 `False`（gas 功能 WP4b 未实现）。

- **`StealthCellView` 接口**（`web/src/types.ts`）：PWA 端类型定义，对应后端 snapshot 字段。
  `state` 枚举固定为 `'pending' | 'building' | 'mining' | 'released' | 'destroyed'`。

- **`StealthCellsCard.vue`**（`web/src/components/StealthCellsCard.vue`）：偷矿点列表展示组件。
  有 cell 时显示，无 cell 时不渲染（`v-if cells.length > 0`）。每行显示：`#cell_id · (x,y) · 农民 N · 状态中文`。
  状态中文映射：pending=准备中 / building=建造中 / mining=采矿中 / released=已交还 / destroyed=已摧毁。
  `mining` 绿色 / `building` 黄色 / `released|destroyed` 危险红。`has_gas` 时额外显示「气矿」标记。

- **`StealthCellsCard.test.ts`**（`web/src/components/__tests__/StealthCellsCard.test.ts`）：
  13 条 vitest 单测，覆盖空态不渲染 / 各状态中文映射 / 多 cell 各行独立 / 气矿标记。

**变更 (Changed)**

- **`SnapshotFrame` 类型**（`web/src/types.ts`）：加可选字段 `stealth_cells?: StealthCellView[]`。
- **`useWs.ts`**（`web/src/composables/useWs.ts`）：加 `stealthCells` ref，snapshot case 从 `f.stealth_cells` 赋值，返回 `stealthCells: readonly(...)`。
- **`CockpitView.vue`**（`web/src/views/CockpitView.vue`）：加 `stealthCells?: readonly StealthCellView[] | null` prop，模板在 CommandCardStack 上方嵌入 `<StealthCellsCard>`（有 cell 时显示）。
- **`App.vue`**（`web/src/App.vue`）：从 `useWs` 解构 `stealthCells`，透传 `:stealth-cells="stealthCells"` 给 CockpitView。

**测试 (Tests)**

- 后端：新增 `TestMultiCellParallelNoConflict`（5 条）+ `TestSnapshotStealthCells`（4 条）= 共新增 9 条。
  `tests/unit/test_stealth_manager.py` + `tests/unit/test_director.py` 合计 294 passed。
- 前端：新增 13 条 vitest 单测（`StealthCellsCard.test.ts`），全部通过（17 文件 206 tests）。
- `npm run build`（PowerShell）成功，产物写入 `src/vibecraft/server/static/assets/`，
  `采矿中` / `stealth-cells-card` 关键词已打进 bundle 确认。

---

### 2026-06-10 偷矿 WP5：受击撤销 stealth 地位（解除 FENCE + 农民还 role），bot 自动接管

**新增 (Added)**

- **`StealthCellManager._release_cell(cell, facade, reason, new_state)`**（`bot/stealth/manager.py`）：
  撤销 stealth 地位的统一入口，严格按照设计 §8 顺序执行三件事：
  1. 农民还 role：`facade.release_unit_role(tag)` × 每个 `cell.worker_tags`。
     stealth 农民是 manager 直接认领的（只设了 role，没有 directive 卡），
     `release_unit_role` 即完全交还，无需额外撤销 directive（符合控制权模型规则 3）。
  2. `remove_cell(cell.cell_id)` → `stealth_townhall_tags` property 自动不再含该 Nexus。
  3. `facade.register_stealth_townhalls(self.stealth_townhall_tags)` → 推送缩小后的集合 →
     DistributeWorkers 不再排除该 Nexus（解除 FENCE），之后 `zone.is_enemys` /
     `needs_evacuation` 自动驱赶农民撤到安全矿区，**我们不手写任何 move 代码**。

- **`_enemy_near(bot, point, radius) -> bool`** 模块级 helper（`bot/stealth/manager.py`）：
  检测 point 半径 radius 内是否有敌方非农民单位（排除 PROBE / SCV / DRONE）。
  test hook → production 路径双模式（与 `_find_ready_nexus_near` 一致），异常 try/except 兜底。

- **`_is_structure_alive(bot, tag) -> bool`** 模块级 helper（`bot/stealth/manager.py`）：
  检测建筑 tag 是否仍在 `bot.structures` 中。test hook + `bot.structures.tags` 生产路径。

- **`_ATTACK_DETECT_RADIUS = 12.0`** 常量（`bot/stealth/manager.py`）：
  受击检测半径（tile）。12 格能在敌人到达采矿范围前预警，留出 DistributeWorkers 撤离时间。

- **`FakeFacade.release_unit_role_calls: list[int]`**（`bot/facade.py`）：
  追踪 `release_unit_role` 调用列表，WP5 单测断言用（与 `train_probe_calls` 等保持一致风格）。

**变更 (Changed)**

- **`StealthCellManager._tick_mining` 开头加 WP5 检测**（`bot/stealth/manager.py`）：
  - 检测 Nexus 摧毁：`cell.nexus_tag is not None and not _is_structure_alive(...)` → `_release_cell(..., DESTROYED)` + return。
  - 检测受击：`cell.on_attack == "flee" and _enemy_near(...)` → `_release_cell(..., RELEASED)` + return。
  - 两者均先于补农民逻辑（被攻击就别再 train），`on_attack="hold"` 时跳过受击检测。

- **模块 docstring / `on_tick` docstring 更新**：移除 WP5 TODO，补充实际实现说明。

**为什么不手写逃散**（设计 §8）：解除 FENCE + 农民还 role 后，bot 的 sharpy DistributeWorkers
`generate_worker_queue` 里 `zone.is_enemys → available=-current*10000`（强力驱赶）/
`needs_evacuation` 逻辑会自动把被攻击矿区农民撤到安全矿区采矿，零额外 move 代码。

**测试 (Tests)**

- `test_stealth_manager.py`：新增 `TestMiningReleaseOnAttack`（7 case）：
  - 受击 flee → cell 移除（RELEASED）；
  - 受击 flee → 每个 worker 的 `release_unit_role` 被调；
  - 受击 flee → `register_stealth_townhalls` 调用且集合不含该 Nexus；
  - `on_attack=hold` 敌近 → 不释放（state=MINING，无 release 调用）；
  - Nexus 被摧毁 → DESTROYED + worker 释放 + FENCE 更新；
  - 无敌、Nexus 在 → 正常 MINING，不误释放；
  - 多 cell：A 被攻击释放，B 不受影响，register 集合仍含 B 的 nexus_tag。
- 新增 `_BotWP5` / `_BotWP5MultiCell` mock bot 类（提供 `_enemy_near` + `_is_structure_alive`
  + 既有 `_is_unit_alive` + `_find_nearby_probes` hook）。
- 合计：303 tests passed（新增 7 条 WP5 + 继承 WP1/2/4 原有 296 条）。
- Protocol audit（`test_facade_release_unit_role.py::test_sharpy_facade_implements_all_protocol_methods`）通过。

---

### 2026-06-10 偷矿 WP4：本地产线（train + 认领 + 采矿）+ 账目分离（主矿饱和排除 stealth）

**新增 (Added)**

- **`StealthCellManager._tick_mining`**（`bot/stealth/manager.py`）：MINING 态每帧三步逻辑：
  1. **清理死亡农民**：`cell.alive_workers(is_alive_fn)` 过滤，死亡 tag 从 `cell.worker_tags` 移除，
     `_is_tag_alive(bot, tag)` 先找 `bot._is_unit_alive` 测试 hook，再走 `bot.units.tags` 生产路径。
  2. **认领新孵化农民**：`_find_unclaimed_probes_near(bot, cell.point, radius, exclude_tags)` 找
     Nexus 附近未被任何 cell 认领的 Probe（先找 `bot._find_nearby_probes` 测试 hook，再走 `bot.workers`
     距离+排除过滤）。认领 = `facade.set_unit_role(tag, LLM_CONTROLLED)` + 入 `cell.worker_tags` +
     `facade.order_worker_gather(tag, cell.point)`（就地采本地矿）。
  3. **补农民**：`alive_count < worker_target` 且 `nexus_tag` 存在 → `facade.train_probe_at(nexus_tag)`。

- **`_is_tag_alive` + `_find_unclaimed_probes_near` 模块级 helper**（`bot/stealth/manager.py`）：
  两个 helper 均采用"测试 hook → 生产路径"双路模式（与 `_find_ready_nexus_near` 一致），
  异常 try/except 兜底不影响帧率。

- **`facade.order_worker_gather(worker_tag, near_point)` 新方法**（双实现）：
  - **Protocol**（`bot/facade.py`）：命令 worker 采 near_point 附近最近矿（Reserved 农民不被
    DistributeWorkers 自动派矿，必须显式下令）。
  - **`FakeFacade`**（`bot/facade.py`）：记录到 `worker_gather_orders: list[tuple[int, tuple[float,float]]]`。
  - **`_SharpyFacadeBase`**（`auto_combat/common_bot.py`）：找 worker → `bot.mineral_field.closer_than(10, p2)`
    → `minerals.closest_to(p2)` → `worker.gather(mineral)`；异常 try/except 兜底。
  - Protocol audit（`test_facade_release_unit_role.py::test_sharpy_facade_implements_all_protocol_methods`）通过。

**变更 (Changed)**

- **`_tick_cell` MINING 分支**（`bot/stealth/manager.py`）：`pass # TODO WP4` →
  `self._tick_mining(cell, bot, facade)`，WP5 受击检测 TODO 备注保留。

- **`Director._tick_worker_saturation` 账目分离**（`bot/director.py`）：
  - `cap` = Σ `ideal_harvesters`（**非 stealth** `townhalls.ready`，`th.tag not in stealth_th_tags`）
    + Σ `ideal_harvesters`（`gas_buildings.ready`，gas 全部保留）。
  - `cur` = `supply_workers` - `len(stealth_worker_tags)`（stealth 农民已纳入 `supply_workers`，
    但属 cell 自产，减去防止主矿少补）。
  - 理由：偷矿农民就地自产由 StealthCellManager 管，若主矿同时按全部 ideal 对比 supply_workers
    → 双重生产 + 主矿欠饱和。账目分离让主矿只对主矿 ideal 负责。

**气矿（with_gas）**：**本 WP4 未实现**，仅做采矿。理由：偷气需要在 stealth 基地附近找
瓦斯泉 → 建 Assimilator → 派农民采气，涉及建造子链 + 采气令 + gas settle 检测，独立机制偏多，
不想与本 WP4 的核心产线逻辑混在一起导致质量下降。留 TODO，建议单独一个小 WP（WP4b）实现。

**测试 (Tests)**

- `test_stealth_manager.py`：新增 `TestMiningLocalProduction`（9 case）：
  - alive < target → train_probe_at(nexus_tag)；alive == target → 不调 train；alive > target → 不调 train。
  - 死亡 tag 从 worker_tags 移除，存活计数正确。
  - 未认领 Probe → LLM_CONTROLLED + 入 worker_tags + gather order；已认领 Probe 不被重复认领。
  - 更新 `test_mining_state_unchanged`：改用 `_BotWithAliveAndProbes` mock（alive==target，不再依赖旧 TODO 注释）。
  - 新增 `_BotWithAliveAndProbes` mock 类（提供 `_is_unit_alive` + `_find_nearby_probes` hook）。
- `test_director.py`：新增 `test_tick_worker_saturation_account_separation`（账目分离具体数字验证：
  主矿 ideal=32 / stealth ideal=16 / supply_workers=40 / stealth 农民=10 → need=2，不是 8）；
  `test_tick_worker_saturation_no_stealth_unchanged`（无 stealth 时行为与原来等价）。
- 合计：296 tests passed（新增 11 条）。Protocol audit 通过。

---

### 2026-06-10 偷矿 WP2：建造链 PENDING→BUILDING→MINING（复用代理建造 + Nexus settle 注册 FENCE）

**新增 (Added)**

- **`StealthCellManager.on_tick` 状态机推进（`bot/stealth/manager.py`）**：
  - 签名由 `on_tick(bot)` 扩展为 `on_tick(bot, facade, now)` 以接收 facade + 游戏时间。
  - **PENDING → BUILDING**：`facade.resolve_selector(unit_type="Probe")` 取第一个可用 Probe，
    `facade.set_unit_role(probe, LLM_CONTROLLED)` 认领（= sharpy Reserved，DistributeWorkers 途中不拉走），
    `facade.order_probe_build(probe, "Nexus", cell.point)` 下建造令，记 `cell.builder_tag`，state → BUILDING。
    无可用 Probe 时保持 PENDING（下一帧重试）。
  - **BUILDING → MINING**：每帧检测 `cell.point` 附近（`_NEXUS_SETTLE_RADIUS=8` tile）是否出现
    己方 ready NEXUS（`_find_ready_nexus_near` helper）；settle 则回填 `cell.nexus_tag`，
    `builder_tag` 加入 `cell.worker_tags`（转为本地农民），调
    `facade.register_stealth_townhalls(stealth_townhall_tags)` 注册 FENCE，state → MINING。
  - **MINING**：占位（WP4 补农民 + WP5 受击检测 TODO）。

- **`_find_ready_nexus_near` 模块级 helper**（`bot/stealth/manager.py`）：
  优先调用 `bot._find_nearby_nexus(point, radius)` 测试 hook（单测 mock 用，绕开 sc2 导入），
  否则走生产路径（`sc2.ids.unit_typeid.UnitTypeId.NEXUS` + `bot.structures(...).ready` 距离过滤）。
  异常全部 try/except 兜底，不影响帧率。

- **Director 接线更新**（`bot/director.py`）：
  `_stealth_manager.on_tick(self._bot)` → `_stealth_manager.on_tick(self._bot, self.facade, now)`，
  WP2 注释更新。

**建造方案选择（Option B：直接 facade）**：manager 直接通过 facade 操控 Probe，不提交 directive 链到
director。理由：避免 manager→director 循环依赖；manager 自己跟踪 builder_tag 做 BUILDING→MINING 转换
更清晰；`order_probe_build + LLM_CONTROLLED` 等价于 director 代理建造路径的核心操作，无需重复走完整路由。

**测试 (Tests)**

- `test_stealth_manager.py` 新增 `TestOnTickStateMachine`（12 case）：
  - PENDING + 有 Probe → BUILDING；记录 builder_tag；set_unit_role(LLM_CONTROLLED)；order_probe_build(Nexus, point)；
    无 Probe 保持 PENDING + 无副作用；第二帧幂等不重复下令。
  - BUILDING + 附近 NEXUS → MINING；nexus_tag 回填；builder 入 worker_tags；
    register_stealth_townhalls 含新 tag；无 NEXUS 保持 BUILDING；超出半径 NEXUS 不触发（误判保护）。
  - MINING → 状态不变（WP4 todo）。
- 现有 `TestOnTick` 两条测试更新为新签名（`on_tick(bot, facade, now)`）。
- 合计：278 tests passed（test_stealth_manager 46 + test_stealth_cell 11 + test_director 221）。

### 2026-06-10 偷矿 WP3：FENCE 双向隔离（facade 两方法 + DistributeWorkers vendor patch）

**新增 (Added)**

- **`Sc2Facade` Protocol 两个新方法**（`bot/facade.py`）：
  - `register_stealth_townhalls(tags: set[int]) -> None`：偷矿 FENCE 注册接口，整体覆盖 `stealth_townhall_tags` 集合。Manager 每 tick 传全集，`DistributeWorkers` / `Expand` 读此排除 stealth 基地。
  - `train_probe_at(nexus_tag: int) -> bool`：偷矿本地产线接口，在指定 Nexus 训练农民；条件 = ready + 空闲 + can_afford(PROBE)，返回 True/False 不抛异常。
  两处均已双实现（FakeFacade + `_SharpyFacadeBase`），Protocol audit 通过。

- **`FakeFacade` 新属性 + 方法**（`bot/facade.py`）：
  - `stealth_townhall_tags: set[int]`（整体覆盖语义，单测断言用）
  - `train_probe_calls: list[int]`（记录所有调用的 nexus_tag）
  - `train_probe_at_result: bool = True`（测试可覆盖，模拟资源不足 → False）

- **`_SharpyFacadeBase` 两方法**（`bot/auto_combat/common_bot.py`）：
  - `register_stealth_townhalls`：`self.bot.knowledge.vibecraft.stealth_townhall_tags = set(tags)`，JSONL 日志 `STEALTHTRACE`。
  - `train_probe_at`：`unit_cache.by_tag(nexus_tag)` 找 Nexus，检查 ready + orders 空 + `bot.can_afford(PROBE)` → `nexus.train(PROBE)`，完整 try-except 兜底。

- **`DistributeWorkers.generate_worker_queue` vendor fence patch**（§10，`vendor/sharpy/sharpy/plans/tactics/distribute_workers.py`）：
  在 `for building in gas_buildings + townhalls:` 循环体最顶部加 `# vibecraft:` hook，读 `self.knowledge.vibecraft.stealth_townhall_tags`，stealth Nexus tag 命中 → `continue`（排除出 work_queue，彻底阻断主矿农民倒灌）。
  - 读取路径：`self.knowledge`（`Component.start` 保证可用，确认路径见 `Component.start` → `self.knowledge = knowledge`）。
  - getattr 兜底：vibecraft namespace 不存在时返回空 set → hook 静默不生效，原逻辑不受影响。
  - 边界：只排除 `stealth_townhall_tags` 中的 Nexus tag；气矿 tag 不在此集合，照常全局调度（WP4 评估）。

**测试 (Tests)**

- `test_facade_release_unit_role.py` 新增 3 case：
  - `test_fake_facade_register_stealth_townhalls_records_tags`：整体覆盖语义 + calls 记录。
  - `test_fake_facade_train_probe_at_records_call`：记录 nexus_tag 列表 + calls 记录。
  - `test_fake_facade_train_probe_at_result_controllable`：`train_probe_at_result=False` 可控。
- `test_sharpy_vibecraft_hooks.py` 新增 `TestDistributeWorkersFence`（3 case）：
  - stealth tag 排除出 work_queue；无 stealth 时两个 townhall 都进队列；气矿不在集合中照常处理。
- `test_sharpy_patch_audit.py::PATCHED_METHODS` 加 `DistributeWorkers.generate_worker_queue` 审计条目。
- 101 tests passed（较 WP1 后增 7 case）。

**文档 (Docs)**

- `docs/sharpy-patches.md` 加 §10（DistributeWorkers fence patch 动机 + 边界 + 升级 checklist）。

### 2026-06-10 偷矿 WP1：schema + StealthCell/Manager 骨架 + Director 接线

**新增 (Added)**

- **`DirectiveType.STEALTH_MINE`**（`directives/types.py`）：新增偷矿 directive 枚举值 `"stealth_mine"`。

- **`StealthMinePayload`**（`directives/models.py`）：新增偷矿 directive payload schema，继承 `_PayloadBase`（`extra=forbid`），字段：
  - `type: Literal[DirectiveType.STEALTH_MINE]`（与现有 payload 模式一致，用 `type` 不用 `kind`）
  - `point: tuple[float, float]`（玩家指定锚点，tile 坐标）
  - `cell_id: int = 0`（Manager 分配回填，提交时占位）
  - `worker_target: int = 16`（目标农民数）
  - `with_gas: bool = True`（是否偷气）
  - `on_attack: Literal["flee", "hold"] = "flee"`（受击行为）
  已加入 `Payload` 判别联合 + `PAYLOAD_MODELS` 白名单。

- **`StealthState`**（`bot/stealth/cell.py`）：五状态枚举（PENDING / BUILDING / MINING / RELEASED / DESTROYED），继承 `str`。

- **`StealthCell`**（`bot/stealth/cell.py`）：偷矿经济单元运行时状态 dataclass；字段含 `cell_id / point / state / nexus_tag / worker_tags / gas_tags / worker_target / on_attack / builder_tag`。`point` 存 `tuple[float, float]`（tile 坐标，与 director 内部惯例一致，用到 sc2 API 时在调用侧转换）。提供 `alive_workers(is_alive: Callable[[int], bool]) -> set[int]` helper（接收判断函数，方便单测 mock，WP4/WP5 产线补员 + 受击逻辑用）。

- **`StealthCellManager`**（`bot/stealth/manager.py`）：偷矿 cell 生命周期管理器：
  - `create_cell(payload) -> int`：分配自增 cell_id（从 1 开始），创建 PENDING cell，返回 cell_id（payload 为 pydantic immutable，cell_id 从返回值取）。
  - `cells: dict[int, StealthCell]`
  - `stealth_townhall_tags` property：所有 cell 非 None nexus_tag 并集（喂给 FENCE patch + Expand gate）。
  - `stealth_worker_tags` property：所有 cell worker_tags 并集（喂给账目分离，WP4 用）。
  - `remove_cell(cell_id)`：从 cells 删除。
  - `on_tick(bot) -> None`：空壳（WP2-5 填入状态机推进逻辑）。

- **Director 接线**（`bot/director.py`）：
  - `__init__` 创建 `StealthCellManager` 实例（`self._stealth_manager`）。
  - `_submit_directives`：`STEALTH_MINE` 进 `_in_flight`（与 `VIEW_FOLLOW`/`PRODUCTION_BLOCK`/`RALLY_POINT` 同路由分支，persistent 全局状态 directive）。
  - `_apply_to_facade`：`STEALTH_MINE` case → `self._stealth_manager.create_cell(payload)`，日志 `STEALTHTRACE stealth_mine_applied`。
  - `on_tick`：`self._stealth_manager.on_tick(self._bot)` 调用（空壳无副作用）。

**测试 (Tests)**

- `test_stealth_models.py`（7 case）：payload 解析、Directive 信封包装、非法字段拒绝、PAYLOAD_MODELS 白名单覆盖。
- `test_stealth_cell.py`（11 case）：StealthState 枚举、StealthCell 默认值、worker_tags 独立不共享、`alive_workers` helper（全活/部分死/全死/空/不改原集合）。
- `test_stealth_manager.py`（22 case）：create_cell 自增 id、PENDING 状态、字段从 payload 正确取、stealth_townhall_tags/stealth_worker_tags 并集、remove_cell、on_tick 空壳不崩。
- `test_director.py` 新增 `TestStealthMineDirectorIntegration`（5 case）：apply directive → PENDING cell 创建、cell_id 分配、两条指令两 cell、`_stealth_manager` 属性存在、on_tick 调用不崩。
- 265 tests passed（test_stealth_cell + test_stealth_manager + test_director 合计）。

### 2026-06-10 偷矿 WP0：玩家开矿封顶 + stealth 基地排除（Expand vendor hook）

**新增 (Added)**

- **`Expand.execute` vendor hook（§9）**：在 `active_bases` 计算后立即读
  `knowledge.vibecraft.expansion_cap_override`；不为 None 时排除 `stealth_townhall_tags`
  中的 Nexus tag（偷矿 stealth 基地不计入"自然扩张账"），若非 stealth 基地数 `>= cap`
  则 `clear_worker()` 并提前 `return True`，阻止 sharpy bot 继续自动扩张。
  — marker：`# vibecraft: 玩家开矿封顶 + stealth 基地不计入自然扩张账`

- **`set_expansion_override(int | None)` 双实现**：`Sc2Facade` Protocol 新增此方法；
  `FakeFacade.expansion_overrides: list[int | None]` 记录所有调用；`_SharpyFacadeBase.set_expansion_override`
  写 `knowledge.vibecraft.expansion_cap_override`（None = 撤销封顶）。

- **SNS 两字段**：`common_bot.py` `knowledge.vibecraft` SimpleNamespace 加
  `expansion_cap_override=None` + `stealth_townhall_tags=set()`（为后续偷矿 WP0 做好对接点）。

- **Director 接线**：`apply_macro_action("expand", N/max/clear)` 在提交 directive 后
  立即调 `facade.set_expansion_override(N)` / `(None)`，绕过 production_overrides 不走
  `_apply_to_facade` 的限制，确保封顶立即生效。

**测试 (Tests)**

- `TestExpandCapOverride`（4 case）：cap 触发封顶 / None 时穿透 / stealth 排除阻止封顶 /
  stealth 排除后仍封顶的对照 case。加在 `test_sharpy_vibecraft_hooks.py`。
- `TestMacroAction` 补 3 case：`expand=N` / `expand=clear` / `expand=max` 各自调用
  `facade.set_expansion_override` 的正确值。
- `test_sharpy_patch_audit.py::PATCHED_METHODS` 加 `Expand.execute` 审计条目。
- `_clean_vendor_mods()` 扩展清理 `s2clientprotocol.*`，修复 expand fake 注入导致后续
  测试 `s2clientprotocol.common_pb2.Race` 缺失的跨 test 污染。

### 2026-06-10 语音：默认改文字 + 候选框加高 + 松手后变绿(成功)/变红(失败)再消失

**变更 (Changed)**

- **默认输入模式改为文字**（玩家：FunASR 体验不如原生输入法）：`inputMode` 默认值 voice→text，
  仍保留切换到语音；localStorage 记忆用户选择。
- **松手后候选窗口不立刻消失，等识别稳定再关**（玩家）：VoiceInput 浮层状态机
  `recording → finalizing → success/failed/cancelled`。松手进 **finalizing**：窗口保留、草稿继续
  更新、显"识别中…"脉动；**定稿有内容 → success 变绿"✓ 已识别"**+ 下发指令（识别成功）；
  **定稿空/4.5s 超时 → failed 变红"✕ 识别失败"**（不下发）；上滑/误触 → cancelled 变红"已取消"。
  各停留一会再消失。判定改为直接监听 `lastTranscript` 定稿帧（空定稿当即判失败，不必等超时）。
- **候选识别框加高一点点**（玩家）：浮层 `py-4→py-5`、波形/识别区 `h-20→h-24`、
  候选文字 `min-h 1.6em→1.8em`。

- **回退「离线 SeACo 模型 + 拼音矫正」**（玩家：真机体验反而下降）：本想用离线全量模型 +
  拼音同音矫正修"巨像→具象"，但加上后整体体验下降（松手延迟、partial/final 不一致），
  撤回到流式单模型。"巨像"同音问题暂回原状，后续走更轻量的路子。（两者从未发布，净变更为零。）

### 2026-06-10 语音波形响应优化：快攻慢放 + 缩短分析窗（降延迟、保顺滑）

**变更 (Changed)**

- **波形响应更跟手、延迟更低**（玩家：刷新率/延迟能否再改进）：刷新率本身已 = rAF = 屏幕刷新率
  （60/120Hz）到顶，无法再提；延迟来自上版**对称** `WAVE_SMOOTH=0.3`（上升也滞后 ~100ms）。
  改成**快攻慢放（attack/release）**：上升用 `WAVE_ATTACK=0.7`（几乎即时跟手）、下降用
  `WAVE_RELEASE=0.18`（缓慢回落顺滑）——出声立刻弹起、停了才慢落，同时拿到低延迟 + 不抖。
  另把 AnalyserNode `fftSize 1024→512`（分析窗 ~21ms→~10ms，数据更新鲜）。

### 2026-06-10 语音波形浮层优化：加高 + 候选文字上提避拇指 + 波形平滑

**变更 (Changed)**

- **浮层加高、波形加高、候选文字上提**（玩家：拇指容易挡候选文字）：浮层 `mb-2→mb-5`
  整块抬高远离按住的拇指、`py-3→py-4`；波形 canvas `h-10→h-20` 翻倍更直观；重排为
  **候选文字放最上方**（拇指够不到、字号 text-sm→text-base 更醒目）→ 波形 → 提示行放最底
  （离拇指最近但只是说明，被挡无妨）。
- **波形变化平滑**（玩家：刷新生硬）：原来每帧直接用 analyser 瞬时值画 → 跳变生硬。加
  **线性拟合缓动**（每帧显示高度向目标按 `WAVE_SMOOTH=0.3` 比例靠拢），并用 `roundRect`
  圆角条（不支持时回退 fillRect）；每次按下从平线起。观感顺滑不顿。

### 2026-06-10 语音浮层加实时波形（跟音量同步跳动，搜狗式收音反馈）

**新增 (Added)**

- **按住说话浮层里加实时波形**（玩家：缺"在收音"的反馈很难受）：在已有音频管线的 source 上旁挂
  一个 `AnalyserNode`，浮层里加 `<canvas>`，录音时 `requestAnimationFrame` 每帧读时域波形、按
  音量画上下对称的跳动竖条（48 条，取消区变红）。`useVoiceInput` 新增 `getLevels(barCount)`
  读当前波形条高（时域峰值=音量）。说话越大声条越高，静音是细线 → 直观确认语音被收到、正在识别。

### 2026-06-10 文字发送按钮微信式隐藏 + 集结点标记 6 层环/无限高竖线

**变更 (Changed)**

- **文字输入模式：发送按钮默认不显示，输入框有内容才出现**（玩家，对齐微信）：原来文字模式
  发送按钮常驻（空内容时灰禁用），改成 `v-if="文字模式 && 输入有内容"`。空时输入框 flex-1
  占满，发送按钮隐藏；打字才冒出来。发送后状态反馈仍走上方承载卡片。
- **游戏内出兵集结点标记更醒目**（玩家）：地面同心环从 **3 层加到 6 层**、层距 = 编队环层距的
  **2 倍**（向外铺开能分辨，不再挤成一团）；那根竖线从 6 格高拉到 **1000 格（≈无限高）**，
  地图大范围都能看到集结点在哪。常量 `_RALLY_RING_PASSES/_STEP/_BASE/_PILLAR_HEIGHT`。

### 2026-06-10 语音输入按住体验前后修：预热麦克风(不漏开头) + 补尾(不丢尾字)

**修正 (Fixed)**

- **按下要先按住一会才不漏开头**（真机：服务端 PTT 不如原生输入法）：根因是旧 `start()`
  异步（getUserMedia + 建 AudioContext + 加载 worklet，~100-500ms），这期间麦克风还没采集 →
  按下立刻说的前小半句丢。修：把管线搭建挪到新的 `arm()`，**进语音模式就预热麦克风常驻采集**，
  `start()` 改成同步只翻转发开关 → 按下即录、从按下起不漏。`disarm()` 在离开语音模式 / 卸载 /
  页面隐藏时释放麦克风（不长亮）。
- **松手早了尾字被丢**：旧 `stop()` 立刻停麦克风轨道 → worklet 残留帧丢 + paraformer 流式
  look-ahead（右文 300ms）断 → 最后一字解不出。修：松手后 UI 立即关、但**继续静默转发
  TAIL_MS(350ms)再发 audio_end**，把尾字 + 流式右文喂全。

**变更 (Changed)**

- 语音手势 `start()` 同步化后，去掉原先为异步 start 兜底的 promise 竞态处理（VoiceInput.vue）。
  新增麦克风预热生命周期（onMounted arm / onUnmounted disarm / visibilitychange）。重 build bundle。

### 2026-06-10 语音输入根因修复：喂模型的分片粒度错 → 整句只识别几个字

**修正 (Fixed)**

- **语音识别断断续续、整句只识别出几个字**（真机：说一整句最后只剩 5~7 字）：根因是
  **喂给 funasr 的音频分片粒度不对**，不是"非本地"（funasr 就跑在 PC 上，CPU，
  `asr_model_loaded` 已确认）。`paraformer-zh-streaming` 的 `chunk_size=[0,10,5]` 要求每次喂
  **600ms**（`chunk_size[1]×960 = 9600` 采样）的整块；而手机 `pcm-worklet.js` 每帧只发
  **100ms**（1600 采样），`AsrSession.feed()` 拿 100ms 就直接 `generate()` → 流式 chunk
  边界全乱 → 大部分帧识别为空、偶尔吐一两个字。修：`AsrSession` 内加 PCM 缓冲，攒够一个
  600ms stride 才喂模型，余量留到下帧；`finalize` 把剩余尾巴 + `is_final=True` flush。
  真模型验证（TTS "派一个农民出去探路" 按 100ms 帧喂）：修前 partial 长度 1/4、final 5~7；
  修后 partial 逐段累积 `派`→`派一个农`→`派一个农民出`→`派一个农民出去探`、final 完整。

**变更 (Changed)**

- **start.ps1 改 `--no-sync` + 加 `--extra asr`**：FunASR 的 torch/torchaudio 是手动
  `uv pip install` 装的、**不在 uv.lock 里**，原来的 `uv run`（会按 lock 同步）一启动就把
  torch 当多余包卸掉 → 语音直接废。改用 `--no-sync` 用现有 venv 原样跑（dev+sc2+asr+torch
  都已装好），并补 `--extra asr` 作文档兜底。

### 2026-06-09 语音输入再两修：点一下卡录音态 / 长句只剩最后一块

**修正 (Fixed)**

- **点一下就进录音、松手还卡在录音态**（真机报）：根因是 `start()` 异步（`await getUserMedia`
  + 加载 worklet），最后一行才把 `isRecording` 置 true；而手势收尾 handler 用 `isRecording`
  当门控。快速点按时 `pointerup` 先于 `start()` 完成到达 → guard 提前 return 啥都不做 →
  `start()` 随后置 true → **永久卡录音态，没有 stop 触发**。修：手势改用**同步 `pressing`
  标志**门控，并把 stop/cancel 挂到 `start()` 的 promise 后执行（松手时录音还没真正起来也能
  正确收尾）。现在松手必停、轻点（<300ms）当误触取消。
- **长句识别只保留最后一块**（真机："派一个农民出去探路" 只剩"探路"）：`paraformer-zh-streaming`
  每次 `generate` 只吐**当前 600ms 块**的增量文字，不是累计句；`AsrSession.feed()` 原样返回单块，
  ws 当 partial 整段替换 → 屏幕只剩最新一块，`finalize()` 拿空音频 flush 也只吐尾巴，全句从未拼起。
  修：`AsrSession` 内累积 `self._text`，feed 返回**累积全句**、finalize 返回**整句 + flush 尾巴**。

### 2026-06-09 语音输入真机三修：不显示文字 / 改微信式长条 / 必须按住 / 切换按钮挪右

**修正 (Fixed)**

- **说话不实时显示草稿、最后也没文字出来**（真机报）：后端 ASR 其实正常（日志有
  `ws_transcript_partial/final_sent`），是**前端响应式断了** + **手势过早结束录音**两个叠加：
  - 响应式：父组件模板 `:last-transcript="ref"` 被 Vue **自动解包成值**传下去，下游却当
    `Ref` 用 `.value` → 取到 undefined → partial/final 永不更新（单测直接传 ref 对象，绕过解包，
    所以测试假绿）。修：prop 链改成传**响应式值**（`TranscriptFrame | null`），VoiceInput 用
    `toRef(props,'lastTranscript')` 适配回 ref 给 useVoiceInput。
  - 手势：VoiceInput 原来 **touch + pointer 事件双绑**，手机上重复触发 + 轻点导致录音瞬间开关 →
    transcript 到达时浮层已关。修：**只用 pointer 事件**。

**变更 (Changed)**

- **语音输入改微信式长条 + 必须按住**（玩家）：麦克风从圆按钮改成**中间一根长条**（整条按住说话）；
  加 **MIN_HOLD 300ms** —— 轻点（<300ms）当误触不发，**必须按住**才录、松开才发、上滑取消。
- **输入栏布局重排**（玩家）：从左到右 = **历史 | 中间(文字输入框/语音长条) | 语音文字切换 | 发送(仅文字模式)**。
  切换按钮从左边挪到中间右侧，跟发送同侧，不再"切出去在左、识别在右"。重 build bundle。

### 2026-06-09 语音指令输入完成（FunASR，Task 6/7/8）

**新增 (Added)**

- **微信式麦克风 UI**（Task 6，`VoiceInput.vue`）：按住说话，浮层实时显示草稿；上滑 >60px 进
  取消区（变红"松开取消"）；松手——按钮区 `stop()` 定稿发送、取消区 `cancel()` 丢弃。touch+pointer
  双路径；非 HTTPS/无麦克风（`supported=false`）不渲染麦克风。
- **语音/文字微信式切换 + 接入现有 command**（Task 7）：CommandInput 加 toggle（默认语音，
  localStorage 记忆）；语音 `@recognized` → `submitCommand()` 复用承载 UI/冷却/历史，LLM/Director
  下游零改；非 HTTPS 自动回退文字 + 提示。重 build bundle。
- **ASR 引擎接入 server**（Task 8）：`service.py` 构造 `AsrEngine` 单例注入 ws handler；启动日志
  `asr_engine_init available=...`。`scripts/asr_smoke.py`（验模型加载 + feed/finalize 管线，带
  wav 可测真识别）+ `docs/voice-input-runbook.md`（起法/用法/排错）。
- **依赖**：funasr + torch/torchaudio（CPU）装进 venv；首次下 `paraformer-zh-streaming` 模型（~GB，
  ModelScope）。funasr 作可选 extra，缺失时语音禁用、文字照常。

**说明**：音频走现有 WS（funnel HTTPS，不依赖 Tailscale）；麦克风需 HTTPS → 走 funnel URL。
真机识别准确率以手机实测为准（runbook 有验收清单）。

### 2026-06-09 修"刷N兵到X"被误解析成"到X待命"（LLM prompt）

**修正 (Fixed)**

- **"刷两个叉子到前线"被解析成 unit_claim standby（到前线待命）、兵没折跃**（玩家报，真局两次）：
  rules.md 把"刷/折跃出兵"的触发词锚死在 **"在X刷N"** 句式，玩家说 **"刷N兵到X"**（用"到"）时
  漏匹配 → LLM 把"到前线"当成移动目的地、译成"到前线待命"（standby）。修：rules 改成
  **"刷/折跃 N 兵〈到/去/在〉地点" 一律 production_override + warp_at**，并显式声明"刷=折跃门出新兵,
  绝不是移现有兵待命",加反例；few_shot 例 11c 直接拿失败原话当例子。真 LLM 实测确认
  "刷两个叉子到前线"→production_override(warp_at=forward)、"虚空到前线集合"→unit_claim（区分正确）。
  重 dump prompt。

### 2026-06-09 WS 音频帧接线 + 录音 composable（ASR Task 3/5）

**新增 (Added)**

- **WS 音频帧接线**（ASR Task 3）：`ws.py` 处理 `audio_chunk`（base64 PCM→`AsrSession.feed`→partial
  回推 `transcript`）/`audio_end`（finalize→final transcript）/`audio_cancel`（丢弃 session）。
  每连接一个活跃 `AsrSession`，AsrEngine 单例共享；funasr 不可用时静默忽略不崩。
- **录音 composable + PCM worklet**（ASR Task 5）：`useVoiceInput`（getUserMedia + AudioWorklet
  线性插值降采样 48k→16kHz mono Int16，~100ms 分帧 base64 → `sendAudioChunk`；start/stop/cancel；
  `supported` 判 secure context）+ `web/public/pcm-worklet.js`。

### 2026-06-09 前端 WS 音频/transcript 帧类型 + 发送 helper（ASR Task 4）

**新增 (Added)**

- **`web/src/types.ts`**：新增 4 个 WS 帧 interface，与后端 ASR 协议对齐（snake_case 字段）。
  - **上行**（手机→server）：`AudioChunkFrame`（`type:'audio_chunk'` + `seq:number` + `pcm:string`）、
    `AudioEndFrame`（`type:'audio_end'`）、`AudioCancelFrame`（`type:'audio_cancel'`）
    —— 全部加入 `UpFrame` union。
  - **下行**（server→手机）：`TranscriptFrame`（`type:'transcript'` + `text:string` + `is_final:boolean`）
    —— 加入 `DownFrame` union。

- **`web/src/composables/useWs.ts`**：
  - 新增 `lastTranscript: Readonly<Ref<TranscriptFrame | null>>`（响应式，收到 transcript 帧即更新）。
  - 新增发送 helper：`sendAudioChunk(seq, pcm)` / `sendAudioEnd()` / `sendAudioCancel()`。
    三个 helper 复用现有 `send(frame: UpFrame)` 路径，结构类型安全。
  - `onmessage` switch 增加 `'transcript'` case → 更新 `lastTranscript`。

- **`web/src/__tests__/useWs.test.ts`**：新增 describe 块（ASR Task 4，4 个测试用例）。
  - 注入 MockWebSocket（捕获实例）验证三个发送 helper 的帧字段。
  - 模拟 `onmessage` 注入 transcript 帧验证 `lastTranscript` 更新及 `is_final` 字段。
  - 先写失败测（methods not a function），实现后全 PASS（共 141 测试全绿）。

### 2026-06-09 FunASR 流式引擎/会话（惰性加载 + 热词 + executor + graceful）

**新增 (Added)**

- **`src/vibecraft/server/asr.py`**：FunASR 流式 ASR 引擎 + 会话管理。
  - `AsrEngine`：funasr paraformer-zh-streaming（2pass）惰性加载引擎。
    - 首次 `create_session()` 时才初始化（不拖慢 server 启动）。
    - 读 `config/asr_hotwords.txt` 热词（格式 `词 权重`，graceful 文件不存在）。
    - `model_factory` 构造参数可注入，便于单测替换假模型。
    - `available` 属性：funasr 未安装 → `False`；加载失败 → `False`；server 不崩。
  - `AsrSession`：一段录音（按住~松手）的流式状态。
    - `feed(pcm: bytes) -> str | None`：PCM16 → float32 → paraformer online 推理，返回草稿或 None。
    - `finalize() -> str`：发空帧 + `is_final=True` 触发 2nd pass 定稿，返回最终文字。
    - `cancel()`：上滑取消，之后 feed 返回 None / finalize 返回空字符串。
    - 推理走 `asyncio.get_running_loop().run_in_executor(None, ...)` 不卡 event loop。
  - chunk 参数：`chunk_size=[0,10,5]`（约 600ms），`encoder_chunk_look_back=4`，`decoder_chunk_look_back=1`。
- **`tests/unit/test_asr.py`**：19 条 mock funasr 单测（TDD，全绿）。
  覆盖：feed partial / finalize final / cancel 丢弃 / 惰性加载（factory 未被调用到调用）/ funasr 缺失 graceful。
- **`pyproject.toml`** `[project.optional-dependencies]` 加 `asr = ["funasr"]`（仅声明，不强制安装）。

### 2026-06-09 ASR 热词生成脚本（别名表 + 黑话 → hotwords.txt）

**新增 (Added)**

- **`scripts/gen_asr_hotwords.py`**：FunASR 热词生成脚本。从三源汇总去重：
  - `docs/aliases/protoss|zerg|terran.yaml`（建筑/单位/升级别名，权重 15）
  - `strategies/**/*.yaml`（剧本 display_name_zh + aliases，权重 15）
  - 内置战术黑话硬编码列表（4BG/IAC/12D/闪追/Skytoss/两矿凤凰/MMM/12pool/DT偷家/两矿飞龙 等 41 条，权重 20）
  - 输出格式：每行 `词 权重`（FunASR hotwords.txt 标准格式）
  - 黑话权重 > 别名权重；同词取高权重（`闪追` 在两处 → 权重 20）
- **`config/asr_hotwords.txt`**：生成产物（共 924 条热词，含 41 条权重 20 黑话）
- **`tests/unit/test_gen_asr_hotwords.py`**：12 条单测（TDD），覆盖类型/权重/去重/空目录边界

---

### 2026-06-09 修"在这里造基地造歪了" + 连带新基地农民全 idle

**修正 (Fixed)**

- **玩家说"在这里造一个基地"，Nexus 造歪在矿区旁、远离矿**（玩家报）：`build_at` by_probe
  造 townhall 时直接对镜头点 `find_placement`，只找"最近能放下的点"，**不贴矿**。修：建
  townhall（NEXUS/CC/Hatchery）前先把目标点 **snap 到最近的 expansion 落点**（= bot 自己
  开矿用的 `zone_manager.expansion_zones[i].center_location` / `expansion_locations_list`，
  贴矿最优 townhall 位），再 find_placement。新增 `closest_expansion_location` helper
  （named_spot.py）+ 单测。
- **新造基地后，新产农民全站着不动、"空闲农民采矿"也没反应**（玩家报）：**与上同根因**。
  基地造歪→附近没矿→`DistributeWorkers` 没法把本地农民分配去采矿 → idle 农民越堆越多
  （telemetry 实锤：造基地后 total_probes 20→33 一路涨，但 mineral_workers 卡在 ~14、
  idle_workers 0→12）。落点修对后，新基地落在矿线上，DistributeWorkers 自动安置 → 不再
  堆 idle。（"空闲农民采矿"命令本身映射到 unit_release 是冗余的——落点正确时 idle 农民
  本就被自动接管。）

**修正 (Fixed)**

- **"刷两个叉子到前线"折跃到了家里、不是前线野水晶**（玩家报）：`production_override.warp_at`
  填的 `named_spot="forward"` 经 `_forward` 解析成**我方最靠前的基地**（在野水晶后方很远），
  facade `_nearest_power_source` 就选了离它最近的"家里"水晶（真局折跃到 (129,108)，而前线
  野水晶在 (89,29)）。修：玩家"刷到前线"时，能量场参考点改用**敌方主基地** → 选"离敌最近的
  能量场" = 最靠前的野水晶 **或**已展开的前压棱镜（`_nearest_power_source` 本就同时考虑
  PYLON 6.5 + WARPPRISMPHASING 3.75，棱镜自动纳入）。新增 `_forward_warp_reference_point`
  + 2 条单测。（注:仅"前线/forward"改参考点；"刷到这里/二矿"等仍按指定点找最近能量场。）

**内部 (Internal)**

- **WebRTC 加 ICE 候选诊断日志**：`handle_offer` 打印远端(手机 offer)+ 本地(server answer)的
  ICE 候选 `ip typ` 列表（`webrtc_remote_candidates` / `webrtc_answer_ready.local_candidates`）。
  排查"视频连不上"：本地候选应含 Tailnet `100.94.x`；远端若**没有** `100.94.x` → 手机不在
  tailnet（funnel 只代理 WS、不代理 media）→ ICE 无可达候选必 fail。下次失败一眼定位。

**变更 (Changed)**

- **造基地落点改三档 + 8-13 格模糊时弹确认**（玩家）：snap 原来无条件把所有 townhall 拽到最近
  expansion，但挡路/封路基地是真实战术（人虫神都有）。改成按指定点离最近 expansion 距离分三档：
  **≤ 8 格**（`TOWNHALL_SNAP_MAX_DIST`）静默 snap 贴矿；**8 ~ 13 格**（`TOWNHALL_CONFIRM_MAX_DIST`，
  13 = 攻城坦克 siege 射程）→ **弹二选一确认**「修正到矿区 / 就在原地」让玩家定；**> 13 格** 明显
  故意造偏 → 静默原地建。确认**复用现有 clarification 通道**（director submit 时动态构造
  `ClarificationRequest`，`BuildAtPayload` 加 `placement_confirmed` 防二次拦截），前端/WS 零改动。
  新增纯函数 `snap_townhall_point` + `_maybe_build_townhall_confirm` + 单测 11 条；真局自验
  `build_base_snap_selftest.py --mode near/confirm/far` 三档均 PASS。
- **"在这开矿 / 在这下主基地" 识别成在镜头处开矿**（玩家）：few_shot 例 47g —— 玩家看着矿区说
  "在这开矿 / 在这里开个矿 / 在这下主基地 / 这片矿开了 / 在这造个基地" → `build_at(Nexus,
  by_probe, camera)`（派农民去镜头点建主基地，落点走上面三档）；对比"再开个矿"（没指地点）→
  `expansion_override`（bot 自选）。重 dump LLM prompt。

### 2026-06-08 科技建筑也显示"有几个/几个在建造中" + 修"新增N个BG"中后期秒完成

**新增 (Added)**

- **科技建筑（VR/VS/VC/VT/VB/VD/BY/BF 等）也显示已建成数 + 建造中数**（玩家要求）：
  原来科技建筑行只体现"有/没有 + 单个进度%"，多个同类科技建筑（如 2 个机械工厂、
  2 个星门）数不出来。现在与产能建筑同款：**蓝色右上角标 = 已建成数（count）**，
  **黄色右下角标 = 建造中数（pending）**，底部黄条仍显示最接近完工那个的进度。
  后端 `_build_tech_progress` 的 `kind=building` 项新增 `count`/`pending` 字段，
  前端 `TechRows.vue` building 段重构成与产能建筑一致的角标结构（含 tooltip）。重 build bundle。

**修正 (Fixed)**

- **中后期说"新增 3 个 BG"（delta）秒完成、一个都没造**（玩家报）：LLM 正确发了
  `items=[{Gateway, delta:3}]`，但 `_resolve_structure_delta` 用
  `structures(GATEWAY).ready.amount` 数当前数 —— 中后期 BG 全升 WARPGATE 后这个值=0
  → 解算出 `target=0+3=3`；而执行层 `_exec_structure_override` 用 `_count_equivalent`
  （把 WARPGATE 算进 GATEWAY）数到已有 7 个 ≥ 3 → 立刻判 `structure_done` 不建造。
  **两处计数口径不一致**是根因（同类：Zerg HATCHERY→LAIR/HIVE 漏算）。修：delta 解算
  也改用 `_count_equivalent` → `target=7+3=10`，执行层补到 10 才停。**不是指令识别问题**
  （delta:3 识别正确），是执行层计数 bug。

### 2026-06-07 修 clarification 选项窗被"识别中"蓝条遮挡

**修正 (Fixed)**

- **LLM 不确定意图时弹的选项确认窗被下方"识别中"蓝条挡住**(玩家报):CommandInput 的
  "识别中/已识别"承载横幅是 `absolute bottom-full`(向上浮到输入框上方),正好飘进了上方
  `ClarificationOverlay` 的位置 → 盖住选项窗。改成**普通 flow**(它本就是 `flex flex-col` 第一个
  子元素,自然堆在输入框上方)→ 从上到下变成 **选项确认窗 → 识别中蓝条 → 输入框**,互不遮挡;
  没识别中窗时自动收拢、确认窗贴近输入框(符合玩家要求"可互相靠近、不互相遮挡")。重 build bundle。

### 2026-06-07 探路农民"改派"去占瞭望塔:不再放回采矿+抓新农民

**修正 (Fixed)**

- **"探路的农民回来吧，去占右边瞭望塔" → 探路农民跑回家采矿、换个新农民去占塔**(玩家报,问题3):
  LLM 把它拆成 `unit_release(Probe)` + `unit_claim(Probe, hold 瞭望塔)`,两条 selector 都是泛泛
  `{unit_type:Probe}`,没绑定"那个探路农民" → release 把探路农民放回采矿、claim 又随便抓个家里
  农民去占塔。还因为没生效、玩家重试 → 叠了两条占塔持久指令。
  修(prompt):新增 few_shot 例 7c —— **"探路农民去做新任务"= 改派那个探路农民,发一条**
  `unit_claim(selector={primary_verb_prefix:"scout"}, verb=hold_position, 目标点, persistent)`,
  按**探路身份**选回那个农民(Director 语意重选,同例 31c"守塔的追猎去X"的现成路径),
  **绝不**附带 unit_release("回来吧/别探了"只是口语前缀,改派本身就停了探路)。对比例 7b:
  "探路农民回来"**后面无新任务**才是纯 unit_release。真 LLM 验证:三句"探路农民去占塔"都 →
  单条 unit_claim+primary_verb_prefix=scout(无 release);对照"探路农民回来" → 仍 unit_release。

### 2026-06-07 续链追加建造:LLM 不再瞎编 chain_id

**修正 (Fixed)**

- **"(那个农民)再到这个位置修一个 VS"建不出来**(玩家报,问题2):LLM 想续上之前正在进行的
  代理建造链,但它**不知道之前命令的真实 chain_id**(那条链是 `proxy_here`),就凭印象瞎编了一个
  `chain_structure_ready(chain_id="d_131f")` → 引用了不存在的链 → 卡永不激活、农民不动。
  修(prompt):规则明确 **chain_id 只在「同一句话」内有效,绝不跨命令引用之前的链/自造 chain_id**;
  "追加一个建筑到某点"(接在正在进行的代理建造后)→ 发一张 `build_at(by_probe=true,
  target=camera, activate_when=null)`,`by_probe` 自动复用"当前持有的代理建造农民"(水晶早建好、
  不用再等链),也别发 structure_override(那是家里建)。改 `rules.md` + `few_shot.md`(例 47f)+ 重 dump。
  真 LLM 验证 3/3("再修一个VS/星门/BG"都 → build_at+by_probe+activate_when=null,不再瞎编 chain)。

### 2026-06-07 代理建造"两 VS 重合"修复 + 架构文档 + 自验提速

**修正 (Fixed)**

- **"在某点修水晶 + 两个 VS",两个 VS 位置重合、只造出一个**(玩家报):水晶建好后刷新两个 VS
  落点的算法(`_pick_open_cluster_spots`)用"贪心挑最空旷的 N 个点",结果把**两个 VS 都聚到同一侧**
  (真局日志:相距仅 ~5 格),`find_placement` 再把它们各自往水晶能量场中心拽 → 实际落点撞一起,
  第 2 个建不出。改成**最远点采样**(第一个选最空旷,之后每个选离已选最远的可走点):两侧都开 →
  自然对开、相距 ~2r 不重合;只一侧开 → 在该侧尽量拉开、不塞崖。真局自验
  `proxy_chain_selftest` 验通过(`vs_distinct==2`,两 VS 都建成)。加回归单测
  `test_pick_open_spots_spread_not_clustered`(全开地图两点 ≥7 格,不聚堆)。
  - 根因里 `find_placement` 只判"能放下 3x3"、**不判可达性**(`in_pathing_grid` 单格可走 ≠
    `can_place` ≠ 农民走得到);单测无 `find_placement` 盖不住,必须真局自验。

**文档 (Docs)**

- `ARCHITECTURE.md` 新增「代理建造链 + 出兵集结点」章节 + 关键不变量(Sc2Facade Protocol 三实现
  同步、释放单位必须从 `_llm_controlled_tags` 移除),把本批反复踩的架构点固化备查。
- `CLAUDE.md` 纠正 realtime/non-realtime 取舍:**mock-LLM 自验用 non-realtime(fast)**,只有
  真 LLM 注入才需 realtime(2026-06-07 又踩一次:给 mock 自验套 realtime 白等 4 分钟)。

### 2026-06-07 代理建造误判修复:途经家里旧建筑被当成"刚造好"

**修正 (Fixed)**

- **"在某点造一个 VS"卡片秒变已完成、VS 根本没造**(玩家报,虚空开矿场景):
  根因在 `_tick_proxy_build` 的 settle 检测——`closer_than(3.5, 农民位置)` 找该类型建筑判
  "造好了"。但农民刚被 claim、还在家没走到工地时,**家里已有的同类建筑**(虚空开矿家里就有
  2 个 VS)落在农民 3.5 格内 → 误判 settle → 卡片秒标"已完成"(实际没造)。
  修:build_at by_probe **发起时快照该类型已有建筑的 tag**(`info["preexisting"]`),settle
  时排除它们 —— 只认**新出现的**建筑。加 2 条回归单测(旧建筑被忽略不误判 / 新建筑正常 settle)。

### 2026-06-07 虚空开矿集结点:离敌最近 VS + 前期过后交回 bot + 玩家优先

**变更 (Changed)**

- **虚空开矿(void_ray_rush)集结点行为调整**(玩家):
  - 有野 VS 时,集结点设到**离对方主基地最近的 VS**(原来是离自己最近)——虚空集结在最前的野
    星门、就近出击骚扰。
  - **前期过了(>7 游戏分钟)默认集结点交回 bot 自己选**(如分矿外):VoidRayStageRallyAct latch
    条件从"仅第一波虚空出门"加上"时间兜底"(任一满足即交回)。
  - **玩家显式设了集结点 → 剧本让位**(玩家 > bot):新增 `knowledge.vibecraft.player_rally_point`,
    facade `set_rally_point` 写入(point/None),VoidRayStageRallyAct 检测到玩家设了就不覆盖。
    解决玩家 rally_point(全局集结点功能)与剧本集结每帧互抢的问题。
  - 新增 4 条 VoidRayStageRallyAct 单测(离敌最近 VS / 时间 latch / 玩家让位 / 出门 latch)。

### 2026-06-07 出兵集结点(玩家设全局 rally,新兵自动去)

**新增 (Added)**

- **出兵集结点**(玩家需求):玩家语音"集结点设在这里 / 出兵都到这里集合 / 以后出的兵都去这里"
  → 设一个**全局集结点**,之后**新出的兵**自动 rally 到该点,持续到玩家 × 或重设。
  - 底层:sharpy 本就有集结点机制(`GatherPointSolver` + `PlanZoneGather` 每帧把 idle 兵
    move 到 `gather_point`,默认家门口、随扩张前移),缺的只是玩家显式设这个点的入口。
  - 新 directive 类型 `RALLY_POINT`(payload 只有 target,无 selector)。**和"集中/集合"
    (unit_claim standby)严格区分**:rally_point 管"未来新出的兵去哪"、不碰现有兵、不占控制权;
    "〈兵种〉到这集中"是把现有兵拿走独占。真 LLM 验证 6/6(4 句 rally + 2 句 claim 都判对)。
  - 执行:Director 每帧 `facade.set_rally_point` 覆盖 sharpy `gather_point`(sharpy set 是
    一次性 flag,必须每帧续设);玩家 × → 恢复 bot 默认前移。命令卡片"出兵集结点 (x,y)"可撤销
    (前端通用渲染,无需改前端)。
  - facade `set_rally_point` 三处同步(Protocol + FakeFacade + _SharpyFacadeBase);
    详见 ADR 0014。新增 5 条 director 单测(set/每帧续设/revoke/覆盖/camera 注入/卡片)。

### 2026-06-07 运营策略面板:加标题 + "开矿"/"不限"措辞 + 缩窄

**变更 (Changed)**

- **运营策略面板(MacroButton)调整**(玩家):
  - 面板顶部加 **"运营策略"** 标题(原来无标题)。
  - 维度1(1/2/3/4/5)标题 **"扩张" → "开矿"**(aria-label 同步)。
  - 开矿档位 **"尽量" → "不限"**。
  - 面板**缩窄**:驾驶舱实时两栏从等宽(各 `flex-1`)改成左战术 `flex-[3]` / 右运营 `flex-[2]`,
    给左边战术面板更多空间。

### 2026-06-07 "集中/集合"统一成独占停留 + unit_arrived 单测修复

**变更 (Changed)**

- **"集中 / 集合 / 集结 / 聚集 / 都过来这里"统一解析成 `unit_claim` standby(独占停留)**
  (排查控制权 bug 时用真 LLM 发现):同义句之前被解析成不一致的 directive ——"集中"→
  `unit_claim`(move_to)、"集合"→ `move`、"过来"→ `move`(engage)。按玩家心智模型("拿走这批
  部队、聚到某点停那等我后续指令,锁到手动取消"),这类应一律 `unit_claim verb=standby
  persistent=true`(独占 + 每帧控位走到→停住→受敌打 + 锁到 ×),**绝不**用 `move`(一次性、
  到点交还 bot,部队不会停那)。改 `docs/llm_prompt/rules.md`(move vs standby 判别表加
  集中/集合/聚集关键词)+ `few_shot.md`(例 47d)+ 重 dump。真 LLM 验证 5/5("集中/集合/
  过来/都到这里来/聚到这里"全部 → unit_claim+standby+persistent;对照"看一眼"仍正确归 scout)。

**修正 (Fixed)**

- **`unit_arrived` done_when 单测假失败修复**:`test_done_when_unit_arrived` 的 mock
  `tags_in` 返回普通 list,而 checker 2026-06-06 改判**队伍重心**(`units.center`)——真实
  sc2 `Units` 有 `.center`、普通 list 没有 → `AttributeError` 被吞 → 静默 False
  (一条测试假红、另一条靠异常假绿)。修:给测试的 units 集合补 `.center`(贴合真实 Units),
  让单测真正走重心逻辑。(checker 本身正确,是 mock 过时。)

- **`test_tech_progress_panel` 3 条 chrono 测试全量跑假失败修复**(测试隔离污染):前面的
  fake-env 测试(conftest `fake_sharpy_bot_env`)会 `del` 真 sc2 模块致其重导 → 本文件顶部
  collection 时绑定的 `UnitTypeId`(旧 enum 类)与 `_build_tech_progress` 运行时 lazy import
  的 `UPGRADE_RESEARCHED_FROM` value(重导后新 enum 类)身份不等(连 `==` 都 False,不同 enum
  类)→ chrono 检测 `bt in chrono_building_types` 失配 → 只在**全量跑**时假失败,单独跑全过。
  诊断定位失配点是 `UnitTypeId` 身份(非 `UpgradeId`,后者 `.get` 仍命中)。修:给该文件加
  autouse fixture,每个测试前把模块全局 `UnitTypeId` 重绑成**当前 sys.modules** 版本,与被测
  代码 lazy import 同源(单独跑无副作用)。**只改测试、不碰生产代码**(生产里 sc2 只导一次、
  身份稳定,本就正确;曾试在 conftest 全局 save-restore 真模块,牵连重导连锁 → 反破坏 41 个
  测试,已回退)。

### 2026-06-07 取消指令后单位仍被锁定不听全军命令(控制权根因)

**修正 (Fixed)**

- **取消语音指令(集中/移动/编队/释放…)后,单位仍被永久锁定、不响应全军进攻/撤退**
  (玩家报"我说所有虚空到这里集中,取消那张指令卡之后,虚空还是不听'强制全体进攻'"):
  根因是真实游戏用的 facade `_SharpyFacadeBase` **漏实现 `release_unit_role`** 方法。
  `Sc2Facade` 是 `typing.Protocol`(运行时不强制实现),而单测一直用 `FakeFacade`
  (有此方法)→ **单测全绿、真局必炸**。后果链:`revoke_directive` →
  `_release_standing_order_units` → `hasattr(facade, "release_unit_role")` 在真实游戏
  恒为 False → 单位的 role 永不释放 → tag 留在 `_llm_controlled_tags` →
  `_refresh_llm_controlled_roles` **每帧把它 re-Reserve 回 `UnitTask.Reserved`** →
  `PlanZoneAttack` 只调度 `free_units`(排除 Reserved)→ 永远拿不到这些单位 →
  取消指令也放不掉、永久锁死。
  - 影响面远超"集中":真实对局里**所有**释放单位的路径全部失效 —— 解散编队
    (`group_clear`)、"释放农民"(`unit_release`)、取消任意 move/scout/claim、
    代理建造农民 × 放归 —— 修这一处方法,全部路径一起恢复。
  - 修:给 `_SharpyFacadeBase` 补 `release_unit_role` —— **无条件**从
    `_llm_controlled_tags` 移除(停止每帧 re-Reserve 的关键)+ 把 sharpy task 还原成
    `UnitTask.Idle`(下帧 UnitRoleManager 重新接管)。
  - 防回归:新增 `tests/unit/test_facade_release_unit_role.py` —— 一条 **facade Protocol
    一致性 audit**(`_SharpyFacadeBase` 必须实现 `Sc2Facade` Protocol 全部公开方法,
    把"mock 有/真实没有"的偏差挡在单测里)+ 两条 `release_unit_role` 行为测试
    (用真实 `_SharpyFacadeBase`,验 tag 从 `_llm_controlled_tags` 移除 + task 还原)。

### 2026-06-07 代理建造卡片完成判定 + 命令卡压缩三行

**修正 (Fixed)**

- **代理建造链卡片建好后不消失**(玩家报"派农民去外面修水晶 + 两个 VS,修好后任务卡都还在"):
  根因是链式 `build_at` 卡 `activate_when` 激活后进 `_committed_directives`(不是 `_in_flight`),
  而 `_tick_proxy_build` 的 settle/dead 收尾只查 `_in_flight` → 查不到 directive → 永不调
  `_release_directive_done` → 卡片一直停在 active。修:settle/dead 两处改查
  `_in_flight.get(did) or _committed_directives.get(did)`。加回归单测
  `test_committed_chain_card_released_on_settle`(committed 里的链卡 settle 后必须标 done)。

**变更 (Changed)**

- **命令卡片压缩成三行,降低高度**(玩家:卡太高):激活条件 / 完成条件 / 进展 各占**一行**
  (原来每条 condition 一行的 UL 列表 → 改成"标签 + 一颗聚合灯 + 多条文字、分隔合并"单行)。
  - **加回"进展"行**(之前压缩时被去掉):有计数/倒计时进度的完成条件,数字(如 `1/4 个`)
    搬到独立的"进展"行;无进度则整行隐藏。
  - 聚合灯取多条 condition 里"最该提醒"的状态(红缺前置 > 黄等资源 > 绿生产/完成 > 灰)。
  - i18n 加 `card.progress`=进展;`CommandCard.test.ts` 同步改(进展数字断言移到 `card-progress` 行)。

### 2026-06-06 战术正确性 / 可视化 / 视频迭代

**新增 (Added)**

- **"在〈地点〉刷 N 兵"折跃到最近能量场**(神族,玩家需求 2026-06-07):
  `production_override` 加 `warp_at`(落点)。玩家"在前线刷 4 追猎 / 在这里刷 3 叉子"→
  折跃门兵种(叉子/追猎/使徒/哨兵/电兵/DT)折跃在**离落点最近的能量场**(ready 水晶塔
  或展开棱镜),不再走家里(避免翻倍)。落点附近暂无能量场 → 指令挂起每帧重查,
  **等出现能量场再折跃**(不丢出兵)。机械/空军没折跃语义,带了 warp_at 也忽略走正常出兵。
  执行接在 `_exec_production_override`(production_override 的真实每帧执行器)里:折跃门兵种
  走 facade `request_warp`(幂等)+ `warp_status` 评估进度(与普通出兵同一套 item 状态机),
  facade `_drain_warps` 每帧找最近能量场 → can_place → warpgate.warp_in(复用 ForwardWarp 的
  CD/资源/can_place,cooldown=warp_cd 防误报)。与剧本无关、任意神族剧本通用。真局 e2e 自验:
  注入"在主基地刷4追猎",折跃门没好时挂起等待,折跃门好后折满 4 个 @ 主基地水晶。LLM prompt:
  "在X刷N兵"→ production_override + warp_at(named_spot/camera/坐标),只说"出N兵"不带地点→不加。
  (顺带确认:4bg 已是"折跃完成后全前线刷"——`_can_train_stalker` 折跃完成即关家里 ProtossUnit,
  只剩 ForwardWarpStalker 在野水晶折跃,无需改。)
- **移动 `engage` 字段**(`MovePayload`):到敌方移动沿途用 attack-move(遇敌就打),
  与 `safe`(绕路)叠加 —— safe 决定走哪条路、engage 决定怎么走。LLM:到对方/推进
  =engage true,回家/撤退=false。
- **视频画质优先模式**(`VIBECRAFT_VIDEO_QUALITY=1` / `scripts/start.ps1 -Quality`):
  网络差时降帧率(默认 15fps)换每帧更高码率 → 更清晰(代价更卡);抬高 vpx/h264
  码率上限。`VIBECRAFT_VIDEO_FPS` 显式覆盖。

**变更 (Changed)**

- **虚空骚扰开矿:虚空默认集结到 VS 附近(方便出门骚扰),第一波出动后再回家**(玩家 2026-06-07):
  void_ray_rush 加 `VoidRayStageRallyAct` —— 第一波骚扰前,把全局集结点设到 VS(星门)附近,
  虚空一出来就近待命、方便攒齐出门(不再被拉回家门口集结);第一波虚空真出门(离家 > 25)后
  latch,恢复 sharpy 默认家里集结,后续 VS 出的虚空回家防守/regroup。真局 smoke 验:集结点设到
  VS → 第一波出门 → 恢复家里集结,两阶段都对、无报错。

- **到达判定改"队伍重心 + 半径按点位分级"**(`task_monitor._check_unit_arrived`):
  不再要求每个单位都进圈,改判单位重心;大区域(主矿/分矿 ~16 格)宽、精确点
  (X后面/choke ~5 格)窄。修"虚空到大区域永远凑不齐全部进圈 → 赖到 timeout"。
- **编队框/圆环半径随单位大小**:`max(0.6, 单位碰撞半径+0.35)`,航母/母舰框变大
  套住,小单位不变(之前固定 0.7,大单位框比单位还小看不清)。
- **及时战术窗口与及时运营策略窗口等高对齐**(CockpitView,items-stretch + h-full)。
- **连续指令"路过 vs 留守"区分**(LLM prompt):"去X看一眼/路过X"用 `move`/`scout`、
  **persistent=false**(到点即走、交还单位,下一步靠 chain_id 接力);只有"守/占/待命X"
  才用 persistent unit_claim。修正旧 few_shot 把"先去右瞭望塔再走"也标 persistent 的别扭。
- **游戏内编队标签 `1`/`2` → `team1`/`team2`**;所有游戏内标签(team + attack/standby
  等任务名)字号 14 → 40,看得清(`_DEBUG_LABEL_SIZE`)。

**测试 (Tests)**

- **代理建造野外链测试覆盖**(玩家要求):
  - 单测(mock,无 SC2)`tests/unit/test_director.py::TestProxyChainBuild`:水晶建好刷新后续建筑
    坐标成不同点、只动本链 by_probe、链复用同一农民、锚点优先 Pylon、激活置 active 状态。
  - e2e(真 SC2)`tests/e2e/test_proxy_chain_e2e.py`(标 `e2e`,default 跳过,`-m e2e` 跑):
    以子进程跑 `scripts/proxy_chain_selftest.py`(绕开根 conftest 的同进程 SC2 拦截),A/B 真局
    验农民移动到野外、卡片创建/刷新坐标/完成、两个 VS 真修在野外不同点、家里让路。
  - 根 conftest 加 e2e default-skip hook(SC2PATH 已设时也不会误跑弹窗)。

**修正 (Fixed)**

- **代理建造落点地图感知:不把农民围死**(玩家真局极端 case:水晶+VS 把农民围在里面,第2个VS
  放不下):水晶建好规划后续建筑落点时,**用地图可寻路信息(`bot.in_pathing_grid`)算每个候选点
  "周围 8 方向 3 格有多少空地"**(矿/崖/已选建筑都算堵),贪心选最空旷、互相 ≥3.5 格分开的点 →
  农民始终有多个方向能走出去,不被"建筑+矿/崖"夹死。**提前规划**(选点时就避开),不是卡住后补救。
  尤其解决"修在矿后面被矿+建筑夹死"。地图信息缺失退回固定偏移(`Director._pick_open_cluster_spots`)。
- **代理建造:水晶建好即刷新后续 VS 卡坐标 + 卡片状态正确流转**(玩家真局:水晶和第1个VS
  建好了,但第2个VS"找不到位置"卡住,且卡片一直显示"未激活"):
  - **第2个 VS 找不到位置**:两个 VS 卡都锚到水晶、find_placement 给出同一个最近点 → 第1个
    占了、第2个撞上建不出来。改(按玩家建议):**水晶 settle 那一刻,把本链所有还在等的
    后续建筑卡(两个 VS)的落点坐标提前刷新成水晶周围不同的点**(每张占一个方向,±4 格仍在
    能量场内、互不重叠),写进卡 payload.point;卡激活时直接用该点 find_placement 贴该方向找位
    → 两个 VS 自然错开(`Director._assign_chain_followup_spots`)。锚点回退也优先锚到能量源
    **Pylon**(不是链上另一个 VS,否则锚到能量场边缘找不到位)。
  - **卡片状态没流转**:build_at 卡从"等激活"激活后没置 active、建好后没置 done → UI 一直显示
    "未激活"(即使已建好)。修:激活时置 `active`(执行中);建好(settle)时,链式卡(农民由
    standby 卡持有)标 `done` 消失,单卡(自己持有农民)保留待命。
- **野外代理建造"水晶→两个 VS"链彻底打通**(真局自验 3/3 PASS,玩家:"两个VS都在家里,
  没修在野外"):用远程野外点(参考 4bg 野水晶选点)做代理建造时,链条多处断裂导致 VS
  建不到野外。一次性修齐多个根因:
  - **农民被拽回家采矿、build 被取消(核心)**:① sharpy `UnitRoleManager.update` 每帧清
    Reserved,而 `pre_step_execute`(在 DistributeWorkers 之前跑)漏了重设 Reserved → 代理
    农民那帧不是 Reserved → 被拉去采矿;② 代理 build 在 `super().on_step()` 之前发,sharpy
    的 gather 在之后下、成最后一道命令覆盖 build。修:`pre_step_execute` 里补一次 Reserved
    refresh + `super().on_step()` 之后再 drain 一次代理建造队列(build 成最后命令)。
  - **不能用 `is_idle` 判"在不在建造"**(forward_proxy 同款坑):SC2 auto-mining 让空闲农民
    带 `HARVEST_GATHER` 订单、`is_idle=False`,旧代码把"正走回家挖矿的农民"当"在建造"跳过、
    永不重指挥。改判 `orders` 里有没有 `PROTOSSBUILD_*`。
  - **链式建造必须复用链上同一农民**:原农民漂走时 `_pick_proxy_build_probe` 会另选自由农民
    → 一条链两个农民(standby 管 A、build 管 B)互相打架。改成链式 build 直接用
    `_task_chains[chain_id]` 那个农民。
  - **链式建造锚定到链上建筑位置**:同名地点(natural/forward 等)不同卡 resolve 可能给不同
    坐标 → VS 被送到离水晶很远、没能量场的地方建不了。改用链上那个水晶的真实位置当锚点。
  - **第2个建筑不撞第1个**:同链两个 VS 第2张卡 settle 检测会撞上第1个刚建的(同类型、就旁边)
    → 误判"也建好了"只建一个。settle 时认领建筑 tag,后续卡排除已认领的。
  - **落点缓存 + 失效重找**:远程建造每帧重发压过 sharpy,落点必须稳(缓存),但 crowded 时
    (水晶+第1个建筑占地)第2个挤不下老缓存点 → 农民贴到点却建不出来。改:农民贴到落点却
    没接到 PROTOSSBUILD → 清缓存、重新 find_placement 找没被占的新点。
  - **自验工具**:`scripts/proxy_chain_selftest.py`(mock LLM 绕开真 LLM + non-realtime fast
    跑真局,抓 PROXYTRACE 日志判链是否打通)+ `VIBECRAFT_MOCK_LLM_JSON` 环境钩子(返回 canned
    directives)。A/B 对照(基线不下指令 vs 下指令)同时验证 (a) 野外建造 + (b) 家里让路
    (问题3):测试局家里让路 21 次、基线 0 次,证明"指令一下家里就让路"。详见 CLAUDE.md
    「玩家指令链·真局自验法」。
- **代理建造链断裂 → 后续 VS 不代理建造(建到家里)+ 农民干站 standby 被拉扯**(真局复现,
  问题1+2 同根因):find_placement 修复(696138d)把建筑放到"最近合法位",可能离原始点
  >3 格,但 `_tick_proxy_build` 仍用 `closer_than(3.0, 原始点)` 检测建好没 → 检测不到 →
  链(`_chain_structures`)永不绑定 → 后续 VS 的 `chain_structure_ready` 永不触发 →
  这些 VS 不走代理建造、被家里 macro 建掉(玩家报"VS 修到家里");农民没 VS 可建、
  干站 standby 被 DistributeWorkers 反复拉扯。修:settle 检测优先用**农民当前位置**
  (农民必站工地旁,最稳)半径 3.5 格找,农民走开了再放宽到原始点 8 格兜底。
- **void_ray_rush 折跃研究太早抢 chrono/钱、压慢虚空**(玩家:确保虚空尽早出来):
  原 build 在 supply 19(2 VS 之前)就 `research 折跃 @chrono`,跟 BY→VS→虚空舰抢
  chrono 和钱。虚空 rush 在 4 BG 上线(~supply 50)前用不到折跃 → 把折跃研究挪到
  supply 44(BN 之后、4 BG 之前),早期 chrono 全给 BY→VS→虚空舰,虚空更早出。
- **代理建造链"然后修 N 个 X"被 LLM 降级成"家里建"**(配合上面的锁钱修复):真 LLM
  实测"派农民去X修水晶,**然后修两个 VS**"——LLM 把两个 VS 发成了 `structure_override`
  (delta:2,**走 bot 自主 macro 在家里建**),不是 by_probe 代理建造 → 我的锁钱修复对它
  不生效 + VS 建在家里。改 LLM 系统提示词(rules.md + few_shot.md 例 50c):代理建造链里
  "然后修 N 个 X"= **N 张 by_probe build_at + chain_structure_ready(同代理点)**,
  明令**绝不**降级 structure_override;camera("这里")目标用 `named_spot:"camera"`(point
  留空,Director 注入坐标),不写 `point:{kind:camera}`(point 只接受坐标 tuple,写 dict
  校验失败)。真 LLM spot check 三种说法(我方分矿/这里/对方9点)现 3/3 正确发 by_probe 链。
- **代理建造被 bot 自主 macro 抢钱 → 两个 VS 都挤家里**(问题3,玩家指令优先花钱权):
  代理建造农民走 `u.build()` **直接花原始矿**,绕过 sharpy `knowledge.can_afford` 的
  reserved 扣减;bot 自主 macro(剧本本就出 VS)也抢同一笔钱,先在家里出 VS → 代理
  建造没钱。修:bot 每帧在 `pre_step_execute`(knowledge 刚清零 reserved、ActManager 跑
  build plan 花钱之前)把玩家未完成代理建造(by_probe)的 cost 登记进 `reserved` → 自主
  macro `can_afford` 看到的钱变少、让路攒矿 → 代理农民(花原始矿,不受 reserved 影响)
  拿到这笔钱。覆盖两态:正在代理建造(`_pending_proxy_build`)+ 链式等前一步的 by_probe
  build_at(挂 `_pending_activation`,如"等水晶好了修两 VS",等待期就开始锁钱)。只锁
  by_probe(structure_override 走 macro 自己花,锁了会死锁)。新方法
  `Director.pending_build_reservations`。
- **虚空 dancing(部队"一会往前一会回来")**:① 零兵 flip-flop —— sharpy `_should_attack`
  在无可攻击自由单位时仍被 intent=attack 逼着 `_start_attack(0 兵)` → 下 tick
  "No attacking units"→retreat→又 attack 的 1Hz 空转,加 `attacker_count==0` 守卫断掉;
  ② "回家防守/撤退"(standby→home)清掉过期的全局"强制全体进攻"意图(含 persistent
  `_assign_standing_order_units` 路径,真局自测补)。
- **新指令抢单位 → 取消旧冲突 move**(issue #3):claim/standing 抢到的 tag 从其它
  MOVE 的 `_safe_move_tags` 移除,旧 move 丢光单位 → 标已终止消失。
- **单位全死光的指令标"已终止"并消失**(superseded 也归入 terminated 终态)。
- **代理建造农民被 bot 反复拉扯**:`build_at by_probe` 的农民没脱离 bot 控制 → 走去
  建造途中被 DistributeWorkers 拉回采矿。改为认领农民(LLM_CONTROLLED)、整条链
  (去X→修水晶→水晶好了修bg)优先复用同一农民,建完继续待命,**直到玩家点 × 才放归**。
- **代理建造连锁 activate_when 三个 bug**(连续指令"修水晶→水晶好了修bg"):
  ① "修bg"卡没等水晶就当场激活 —— `_is_activation_satisfied` 对未知 kind
  (`structure_count_built_since`)默认立即激活 → 改成默认**不激活** + 退化成当前数判定;
  ② "修水晶"卡永不激活、农民干卡 standby —— activate 的 `unit_arrived` 半径(5)< standby
  停靠半径(10),农民停在 ~10 永远进不了 5 格 → activate 半径 floor 到 standby 半径+2;
  ③ gateway 等"全局 pylon>=1"会被家里的 pylon 当场放行 —— 新增 **`chain_structure_ready`**
  激活门:农民 build 出建筑瞬间后端抓住该建筑 **tag** 绑到 chain,后续步骤按 tag 精确
  等"那一个"建好(不靠全局计数/距离猜)。LLM prompt 写入神族能量场机制 + 该模式。
- **代理建造"找不到放建筑位置"**:`order_probe_build` 死磕精确点 `u.build(point)`,点被
  挡住/不平整就被游戏拒。改:入队 → `drain_pending_actions`(async)用 **`find_placement`
  以目标点为圆心找最近合法位**再 build,找不到才退回原点。
- **代理建造农民框标签"前往途中显示 standby"困惑**:standby/hold 指令在单位离目标点
  还远(> standby 半径)时,框标签显示 **"move"(去)**,到位才显示 "standby"。
- **camera 类连续指令("在这里修水晶then修VS")条件不解析、链断 + 每帧刷警告**:
  `_inject_camera_point` 只注入了指令 target,**没注入 done_when/activate_when 的 area,也没
  注入 build_at 的位置** → 条件里的 "camera" 永远解析不出(server log 每帧刷
  `camera unknown`),链断、农民被 bot 抢去乱跑。修:camera 点也注入进 done_when/activate_when
  的 area(含 all_of/any_of 嵌套)+ build_at 位置(named_spot=camera 或无位置时)。
- **代理建造链首步回退 persistent standby**:上一版把"去工地"改 move(一次性)导致农民到点
  释放、被 bot 抢去采矿/探路。改回 `unit_claim` standby(persistent,每帧 tick 稳稳持有,
  standby 会先移到工地)。问题2(standby 追敌)已修,standby 不再有副作用。move 只留给
  "路过/看一眼"。
- **群组命令的 activate_when=unit_arrived 永不激活、卡死灰卡**:`_is_activation_satisfied`
  的 unit_arrived **只查 `self._bot.workers`(农民)** → "一队虚空到X后进攻"这种群组命令
  永远不满足、卡片灰着。改:传入 directive 上下文,unit_arrived 用**该指令单位(群组/链)
  的重心**判到达(区域感知半径),拿不到再 fallback 农民。卡片文字"农民到达"→通用"到达"。
- **未激活灰卡点 × 撤不掉**:`revoke_directive` 漏了 `_pending_activation.pop` → 挂在激活门
  队列的卡撤不掉。补上。
- **standby 农民狂追敌方农民 + 被拉扯**:`_tick_standby_orders` 的"范围内有敌→attack"对
  农民(worker)也生效 → 建造/待命农民追敌方农民跑很远再回来。改:**worker standby 不
  追敌**(只 hold/回位),只军队才 engage 守点。
- **代理建造农民一开始就标 standby**(prompt):连续建造链第一步"去工地"改用 `move`
  (persistent=false,到点即完成),不再 standby 干站;build_at(by_probe) 接管去建造。
- **persistent unit_claim 绕过 activate_when 激活门**(链式指令第二步提前执行):带
  activate_when 的 persistent unit_claim 在 submit 时直接 `_assign_standing_order_units`
  立即执行,没过激活门。表现:"侦察农民到对方基地后去占右瞭望塔" —— 第二步(去瞭望塔)
  在提交瞬间就发了,被第一步(去基地)覆盖丢掉 → 农民到基地后干站不去瞭望塔。修:
  activate_when 未满足时挂 `_pending_activation`,满足才 assign;`_tick_pending_activation`
  对 persistent claim 走 standing 路径(`_assign_standing_order_units`)。
- **`chain_structure_ready` / `structure_ready_near` 漏加进 schema → 整条命令"解析失败"**:
  上一条只加了代码+prompt,没加进 done_when/activate_when 的 pydantic 判别联合 →
  LLM 一发这俩 kind,pydantic 校验失败、directives 全被丢弃。补 `ChainStructureReady` /
  `StructureReadyNear` 两个条件模型进 `DoneWhen` 联合。

M6 虫族/人族 bot 骨架完成。

### 新增 (Added)

- **M6.0 `VibeCraftBotBase`**（`common_bot.py`）：三族共用抽象基类，提取
  race-agnostic 生命周期 hooks（11 个 `_publish_xxx`）+ EventBus + hang watchdog
  + `_llm_controlled_tags` 等。新增种族只需覆盖 3 处（EXCLUDE_FROM_ARMY /
  DEFAULT_OPENING_ID / create_plan）
- **M6.1 `GameConfig.my_race`**：`GameConfig` + `ServiceConfig` + CLI `--my-race`
  参数，`sharpy_adapter.make_bot_class` 按种族 dispatch
- **M6.2a 虫族 alias 表**（`docs/aliases/zerg.yaml`）：17 建筑 / 19 单位 / 15
  升级完整别名，BS=母池 / BL=BroodLord / 小狗=Zergling 等玩家话语全覆盖
- **M6.2b 虫族 bot**（`auto_combat/zerg/`）：`make_zerg_bot_class` 工厂 +
  `_VibeCraftZergBot`（EXCLUDE={DRONE,OVERLORD,OVERSEER}，DEFAULT=12pool）+
  5 个剧本 plans + ZergSustain + ScoutOverlord
- **M6.2a 虫族策略文件**（`strategies/zerg/`）：12pool / macro_hatch / roach_hydra /
  mutalisk_harass / brood_corruptor，形成完整 opening→midgame→lategame 转移图
- **M6.3a 人族 alias 表**（`docs/aliases/terran.yaml`）：17 建筑 / 19 单位 / 15
  升级完整别名，BB=Barracks / BC=FusionCore / 枪兵=Marine / 船长=Battlecruiser 等
- **M6.3b 人族 bot**（`auto_combat/terran/`）：`make_terran_bot_class` 工厂 +
  `_VibeCraftTerranBot`（EXCLUDE={SCV,MULE}，DEFAULT=marine_rush）+
  5 个剧本 plans + TerranSustain + ScoutSCV
- **M6.3a 人族策略文件**（`strategies/terran/`）：marine_rush / reaper_expand /
  bio_stim / two_base_tanks / bc_late，形成完整转移图
- **`StrategyLibrary.openings / .midgames / .lategames`** 属性：方便测试和外部查询

---

<!-- previous Unreleased content below -->

M2 收尾补丁 + M3 准备。

### 新增 (Added)

- **`HangWatchdog`** (`vibecraft.bot.watchdog`)：子进程内 daemon thread,
  bot.time 30s 不前进 → 自动 kill SC2 + 子进程 `os._exit(87)`。
  on_start 自动启,on_end 关停。`VIBECRAFT_DISABLE_HANG_WATCHDOG=1` 禁用。
  4 个单测覆盖 advance / stall / stop idempotent / get_bot_time exception
- **`scripts/e2e_4_directive_types.py`**：4 类指令自动化端到端驱动,
  挨个跑 L1 strategy_set / L2 tactical_objective / L3 unit_claim (standing) /
  L4 production_override,每个 case 独立 SC2 子进程 + fast mode + VeryEasy。
  verify 同时看 snapshot 字段 + events 流(directive.committed 兜底,
  避免 task_monitor 立即 done 错过 snapshot 窗口)。4 case 实测全 PASS

### 修正 (Fixed)

- **`task_monitor` `time_elapsed_since` `.game_time` 属性错**：sharpy bot 暴露
  `self.time`（python-sc2 BotAI 标准），不是 `self.game_time`。原 silent fail
  让 timer-based directive 永远不触发 completed。getattr fallback 修
- **`Director.on_tick` 缺 RELEASED dispatch**：`board.complete` 把 RELEASED
  event push 进 `board._events`,但本 tick `board.tick()` 已返回（只含本 tick
  produced）→ events.jsonl 看不到 `directive.released`。改为 Director.on_tick
  直接 `_dispatch_event(BoardEvent(RELEASED,...))` 让事件立即落盘 + 推 ws

### 删除 (Removed)

- **`DirectiveType.VIEW_MOVE/VIEW_FOLLOW/VIEW_ZOOM` + `ViewMovePayload` 等 +
  `is_view_directive` + Director `_dispatch_view`**：PWA 已有小地图拖拽控视野
  （走 ws.py `view_move` 帧 → bot.facade.move_camera,不经 directive 系统）,
  LLM 文字解析视野指令的路径不再有用。**保留** WS frame `view_move`(PWA
  → ws → bot)路径不动 —— UI 拖小地图功能完整

### 验证

```bash
# 4 类指令自动 e2e（fast mode + VeryEasy + watchdog 兜底）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --seconds 75
# → 4/4 PASS
#   L1 strategy_set    切叉球一波      → snapshot stage=midgame id=iac_2base
#   L2 tactical_objective  进攻对方自然 → events directive.committed+released
#   L3 unit_claim      探机巡逻自然别动 → snapshot standing_orders 非空
#   L4 production_override  下个 BG 出俩哨兵 → events directive.committed+released

# Watchdog 单测
.venv/Scripts/python.exe -m pytest tests/unit/test_watchdog.py
# → 4 passed
```

M3：完整驾驶舱（剩余 PWA UI 精修）+ L4 production override sharpy 真出兵 wire +
phase stepper 精确进度。

---

## [0.1.0a4] - 2026-05-17

**M2 完成。** four-layer 指令架构（L1 宏观 / L2 战术 / L3 standing / L4 产能）
全套链路实施完成。done_when 8-kind discriminated union + task_monitor 完成判定 +
LLM prompt 教 4 层分类 + 4 个 PWA cards + EventBus + NamedSpotRegistry + sharpy
让位机制 全部 work。**`v0.1.0a4`** = M2 出口（设计文档 §13 / ADR 0010 phasing 表）。

本次发布单 session 一气呵成（2026-05-17，~5h wall-clock），主 agent (Opus) +
14 个 Sonnet subagent (worktree isolation) 协作模式 verified scalable。

### 新增 (Added)

- **L2 `TacticalObjective` directive type**（11 verb：attack/defend/scout/expand/
  harass/drop/vision/raze/retreat/regroup/split）
- **DoneWhen discriminated union（10-kind）**：`unit_count_built_since` /
  `tech_done` / `expansion_count` / `target_destroyed` / `own_army_size_ratio` /
  `vision_acquired` / `enemy_killed_in_area` / `time_elapsed_since` + 复合
  `any_of` / `all_of`
- **`task_monitor` 完整实现**：每 sharpy step 检查 in-flight directive 完成判定 +
  EventBus-driven (UNIT_CREATED/UPGRADE_COMPLETE) 高效累计 counter + game-state
  polling (vision/army_ratio/target_destroyed/expansion_count/time_elapsed)
- **`EventBus`**：vibecraft 自建独立 pub/sub 层，11 个 python-sc2 lifecycle hook
  publish 到统一 bus，task_monitor / DecisionWatcher 等 subscriber 用 filter 订阅
- **`NamedSpotRegistry`**：15 个已知 spot（natural/third/main/enemy_* + *_ramp +
  *_gas 变种）+ `resolve(name, bot)` 走 sharpy zone_manager + `closest_named_spot`
  反向查找（publisher area inference 用）
- **sharpy 让位机制**：persistent unit_claim (standing order) 在 Director 端
  resolve selector → set_unit_role(LLM_CONTROLLED) → revoke 时 release_unit_role
  归还。`board.revoke()` 扩支持 committed overlay
- **Director `production_overrides` / `standing_orders` lists**：按 directive type
  + persistent 字段路由
- **3 个新 PWA cards**：`StandingOrdersCard.vue` / `ProductionOverridesCard.vue` /
  `TacticsCard.vue`，每张含撤销按钮（emit revoke → ws revoke_directive 帧）
- **snapshot 4 新字段**：`standing_orders` / `production_overrides` /
  `active_tactics` / 各 directive 的 `done_when`
- **`revoke_directive` 上行帧** + ws/bot wire（玩家撤销路径完整）
- **LLM prompt 教 done_when**：System 段 加 11 verb + 10 kind 白名单 + 4 层分类
  规则 + 11 个 few-shot 例子（覆盖 done_when 典型 pattern + 边界 case：复合 L1+L3
  / L2 engagement+done / 撤销 / 含糊 / unit_count_hint）
- **IntentParser validate retry**：done_when 字段 ValidationError 时回灌 LLM
  重写 1 次；2 次仍失败降级 EPHEMERAL + echo 告诉玩家
- **directives.jsonl 生命周期落盘**：submitted / committed / released / rejected /
  revoked，加 JsonlSink `buffering=1` line-buffered 修子进程空 bug
- **ADR 0010 完整记录**：4 决策 + 30+ corner case Implementation Notes

### 修正 (Fixed)

- **M4 e2e schema gap**（v0.1.0a3 验证发现）：`UnitClaimPayload` 加 `persistent: bool`，
  `Target.kind` 接受 `building_tag` / `named_spot`，`Selector` `extra="forbid"`
  禁 `count` 字段。LLM prompt 同步用合法字段
- **vision_acquired 22x bug**：原用 step count 累加（sharpy step ≈ 0.045s），
  改用 wall-clock ts diff（`_vision_first_visible_ts[id]`）
- **enemy_killed_in_area filter 缺 payload.area**：`_publish_unit_destroyed`
  加 area inference（closest_named_spot max_distance=15）
- **target_destroyed natural/third/main P3 hardcoded 返回 False**：P5 改成走
  NamedSpotRegistry resolve `enemy_natural` 等 + enemy_structures.closer_than
- **CockpitView 资源条占位删除**（SC2 游戏内置 HUD 已有）
- **JsonlSink `buffering=1` line-buffered**：修子进程 spawn 时 jsonl 一直 0 字节
  bug（block buffered + kill 前没 flush）
- **顺手修 baseline RUF012**：`_UNIT_ZH` + `_TACTICAL_VERB_ZH` 改 ClassVar

### 已知未做 / known issues

- **3 个 cross-test pollution flaky tests**：`test_loads_real_strategies` /
  `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`
  单跑永远 PASS，full suite 偶发 fail。不阻塞产线。**未来用** pytest-forked
  或 grep module-level mutable state
- **真实长 SC2 对局 `directive_completed` event verify**：fast mode bot 在 30s
  内被 VeryHard AI 打死，timer-based directive 没机会触发。需要真实 SC2 +
  surviving 几分钟的对局验
- **L4 production override sharpy 真出兵 → M3 范围**：P3 task_monitor 检测
  L4 done_when，sharpy 端不主动响应 production_override。需要 wire
  `bot.facade.set_production_target`

### 验证

```bash
# 全单测
.venv/Scripts/python.exe -m pytest        # 597 passed, 6 skipped
cd web && npm test                         # 50 passed
cd web && npm run typecheck                # clean

# e2e schema gap fix（P1.6 verify）
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal --inject "那个农民守气矿别动" \
  --inject-after 5 --seconds 60
# → ECHO 不再 [解析失败]，LLM 输出 persistent=true + named_spot

# e2e jsonl content（P6 sink fix verify）
uv run --no-sync python scripts/headless_smoke.py --fast \
  --initial-opening 1g_robo_immortal --inject "30秒后撤" \
  --inject-after 5 --seconds 60
# → directives.jsonl 真有 submitted + committed 记录
```

---

## [0.1.0a3] - 2026-05-17

**M1 完成。** 真实 SC2 端到端验证通过 —— 切剧本端到端链路成立。**fast mode** smoke
跑 ~60s wall-clock，force `1g_robo_immortal` 默认 opening + inject「切 4BG」→
SNAPSHOT 从 `opening=1g_robo_immortal` → `opening=4bg`，配套 `strategy.set` +
`directive.committed` 两条事件全到位。inject「切叉球一波」→ `strategy.phase_change`
(opening→midgame) + `strategy.set` + SNAPSHOT `midgame.attack_window={6:15-7:30}` +
5 条 `micro_doctrine` 完整透传。链路 `down_q → IntentParser → LLM (DeepSeek V4) →
Director → board commit (1.5s grace) → STRATEGY_CHANGED → snapshot push` 全通。

### 新增 (Added)

- **sharpy 迁移完整**（M1-M6，全合并 main）：sharpy KnowledgeBot 替代 ares-sc2 作为 bot
  框架；LLM_CONTROLLED role 隔离（M4，9 个 mock 单测）；attack_window / micro_doctrine
  字段透传到 snapshot（M5）；ADR 0009 记录决策
- **WS 多路复用 + auto-pilot + cockpit-sync + minimap 拖拽视野**：view 通道（高频，
  view_move + minimap + drain）/ bot 通道（低频，sharpy super + ratio=5），iteration
  remap 给 sharpy 自己的 namespace；PWA 驾驶舱按 §9.5 重排 + 推荐 / 硬转确认 / 多卡片
- **4bg 流程优化**（gate4_pressure 自定义 plan）：3 BG 等折跃 ≥50% + 矿 ≥450 一次性下；
  ForwardSupportPylonGateway（农民前线修 PY+BG）；首波 4 追猎即出门火力侦察
- **iac_2base 数据对齐 Spawning Tool**（叉球一波 all-in）：6:15 timing + 7 BG +
  chargelot 主力 + 2 不朽 + 2-4 白球；加别名「叉球一波」「IAC一波」「白球冲锋叉一波」
- **四层指令架构设计**（M2/M3 蓝图）：
  `docs/plans/2026-05-16-four-layer-commands-design.md` 定义 L1 宏观 / L2 战术 /
  L3 standing / L4 产能 四层 directive；P1-P6 分期实施
- **headless_smoke 测试基础设施**：`--fast` / `--initial-opening <id>` /
  `--inject <text>` / `--inject-after N` / snapshot + event 帧解析
- **驾驶舱真实截图嵌入 USER_GUIDE**（780×1908，mock 数据演示）；
  md_to_pdf 加 base href + img CSS 让 PDF 正确渲染

### 修正 (Fixed)

- **项目改名 voicecraft → vibecraft**：源码包路径、import、CLI、pyproject、
  scripts、web build、PWA 资源、设计文档全部刷新；GitHub repo
  `catmaniii/voicecraft` → `catmaniii/vibecraft`
- **CockpitView 资源条占位删除**：SC2 内置 HUD 已有，手机端不重复占屏
- **README / USER_GUIDE 弱化"语音"主线**：VibeCraft 自己不做语音识别，录音/转字
  外包给手机系统输入法，文本框是核心
- **TASKS.md 顶部「当前状态」段刷新**：之前的"worktree 待合并"已 stale

### 已知未做 / known issues

- **LLM prompt ↔ Pydantic schema 不匹配**：M4 e2e 跑 inject standing order 类指令
  暴露 schema 拒绝 `selector.count` / `target.structure_type` 字段。属 M3/four-layer
  P1 standing order 实施范围
- **完整驾驶舱**：Standing Orders / 快捷栏 / phase stepper 精确进度 / 撤销机制 — M3
- **midgame/lategame 剧本自动转**：当前 auto-pilot 只是通用兜底，不按剧本切 — 转
  four-layer P3 范围
- **造建筑指令** schema — 同上，P1/P3 范围
- **Windows + retail SC2 不能真 headless**：D3D9 在 non-interactive desktop 立刻 Lost；
  Linux SC2 永久卡 4.10。本次 hidden 调研结论 — 接受 SC2 可见，smoke 走"弹窗 + 自动 kill"

---

## [0.1.0a2] - 2026-05-14

**M0c 完成。** 真实 SC2 客户端端到端 smoke 通过 ——「不动的叉子」验证成立：
2 个探机置入 `CONTROL_GROUP_ONE` role 并 `stop()` 后，60 秒监测窗口内零指令、
零移动、`in_role` 全程保持；ares 结算 `Idle worker time: 168.0` 反证 WorkerManager
没有重新接管。设计文档 §3.4 的「唯一存疑点」—— Hook C (Unit Role) 的 role 隔离
机制 —— 核心假设确认成立。

### 修正 (Fixed)

- **`LLM_CONTROLLED` 映射到 ares 的 `CONTROL_GROUP_ONE`**：ares 的 `UnitRole`
  是固定 StrEnum 无法动态加成员；先前假设可以直接传字符串 `"LLM_CONTROLLED"`
  会立即挂。`ares_adapter` 现内置 vibecraft UnitRole → ares UnitRole 映射表。
- **`scripts/smoke_test.py` 用真实 ares API**：`mediator.assign_role(tag=, role=)`
  + `mediator.get_units_from_role(role=, unit_type=)`（按 role 反查池），且 role
  传 ares 真实 enum 而非字符串。
- **`smoke_test.py` 传 Map 对象**：`run_game()` 在 burnysc2 7.1.0 要的是
  `maps.get(name)` 返回的 Map 对象，先前直接传字符串会
  `AttributeError: 'str' object has no attribute 'relative_path'`。
- **`smoke_test.py` enroll 后 `unit.stop()`**：探机开局 0s 自动采矿，不清掉这条
  SC2 引擎默认 order 会被误判成 `received_orders` 异常。enroll 进 role 后立刻
  stop，之后再出现的 order 才真正意味着有 Manager 主动接管。

### 环境校准（端到端踩坑记录）

- **Python 必须 3.11**：`sc2-helper`（ares 间接依赖）只发布到 `cp311` wheel，
  3.12 装不上。已加 `.python-version` 锁定 3.11。
- **ares-sc2 3.7.2 src-layout 打包问题**：`uv_build` backend 把包装进
  `site-packages/src/ares/` 而非 `site-packages/ares/`，`import ares` 找不到。
  修法：在 site-packages 放一个内容为 `src` 的 `.pth` 文件。
- **`sc2_helper` 需手动安装**：不在 ares-sc2 的依赖声明里，但 ares 的
  `combat_sim_manager` 直接 `import sc2_helper`。需 `uv pip install sc2-helper`。
- **Windows Defender 文件锁**：新解压的 `.exe` / `.dll` 会被实时扫描短暂锁住，
  紧接着的命令报 `os error 32` / `DLL load failed`，重试即可（非真错误）。

### 新增 (Added)

- **`.python-version`** —— 锁定 Python 3.11，避免 uv 误用 3.12。

详细安装 / smoke 流程见 `docs/m0-smoke-runbook.md`。

---

## [0.1.0a1] - 2026-05-14

**M0a / M0b 完成。** 所有不依赖真实 SC2 客户端的模块全部实现，126 个单测全过；
`ruff check` / `mypy strict` 干净；测试覆盖率 83.2%。

### 新增 (Added)

- **脚手架**：`pyproject.toml`（uv + hatchling）、`src/` layout、ruff + mypy strict
  + pytest + pre-commit + GitHub Actions CI 模板
- **`directives/`** —— Directive schema + Board
  - 13 种 DirectiveType（strategy_set / production_override / unit_claim / build_at /
    view_move 等）的 discriminated union payload
  - DirectiveBoard：1.5s 固定生效延迟、阶段单向（opening → midgame → lategame）、
    overlay 叠加、unit_claim 互斥、按 issued_by 优先级仲裁
  - ScopeSpec 四种 kind（ephemeral / until / duration / persistent）的过期判定
- **`dsl/`** —— 沙箱安全的条件 DSL（剧本 YAML 里 enter_when / abort_signals / reactions
  用）。手写 recursive descent parser，禁任意函数；支持 `>=`/`<=`/`AND`/`OR`/`NOT`/`in [...]`；
  `game.time` 字符串 (`'M:SS'`) 与浮点秒自动互转
- **`strategy/`** —— 剧本库
  - 三种 kind（OpeningBuild / MidgameStance / LategameDoctrine）的 pydantic schema
  - BuildStep 紧凑三段式 `"<supply> <verb> <object> [@modifier]"` 解析
  - AliasTable：建筑 / 单位 / 升级三组别名 + verb 消歧（如 `build VR` →
    RoboticsFacility，`train VR` → VoidRay）
  - 3 个 MVP 剧本 YAML：`1g_robo_immortal` / `iac_2base` / `skytoss`
  - StrategyLibrary 跨引用校验（opening → midgame → lategame）
- **`logging_/`** —— 结构化 JSONL 日志层
  - GameSession：一场对局一个 `logs/<game_id>/` 目录
  - 8 条 stream（events / commands / directives / decisions / sc2_actions /
    metrics / errors / ws_traffic），每条 JSONL
  - `llm_calls/call_NNN.json` 全量保留每次 LLM 调用的 prompt + response + tokens + latency
- **`llm/`** —— Intent Parser
  - `IntentParser`：4 段 prompt 拼装（System / Strategy Catalog / Few-shot / Dynamic
    context），通过 Anthropic tool_use 强制 JSON 输出 schema
  - `LLMProvider` Protocol + `AnthropicProvider`（`claude-sonnet-4-6` 默认）+
    `MockLLMProvider`（单测专用）
  - 错误处理：timeout / invalid_json / schema_mismatch / unknown_strategy /
    directive_invalid 全部返回 `ParseError`，**bot 状态绝不变**（设计文档 §7.6）
  - `AmbiguousParse`：confidence < 0.6 弹二次确认
  - prompt 缓存：system / catalog / few-shot 三段标记 `cache_control: ephemeral`
- **`bot/`** —— 编排层
  - `Sc2Facade` Protocol：定义 bot 对 SC2 的全部需求；ares-sc2 / python-sc2 完全
    隔离在 `ares_adapter.py` 里，主模块不依赖 ares
  - `FakeFacade`：单测专用全 mock 实现，记录所有调用
  - `Director`：串起 Parser + Board + Facade，每 tick 调度 committed directive 到
    facade；玩家话语入口 `on_player_command` / `confirm_ambiguous`
  - `ares_adapter.make_bot_class()`：工厂返回继承 AresBot 的 bot 类，运行时 lazy import ares
- **配置**：`config/llm.yaml`、`config/bot_difficulty.yaml`、`docs/aliases/protoss.yaml`
- **M0c smoke 脚本**：`scripts/smoke_test.py`，在真实 SC2 环境验证"不动的叉子"，
  输出 `smoke_report.json`（verdict pass/fail + anomaly 分类）
- **文档**：
  - `CLAUDE.md` —— Claude Code 启动指引（沟通约定 + 实现纪律 + 后续步骤）
  - `README.md` —— 开发者快速开始
  - `docs/m0-smoke-runbook.md` —— M0c 端到端测试玩家手册
  - `docs/adr/0001-tooling.md` —— 工具链选型记录

### 已知风险

- ares-sc2 实际 API 名（`mediator.assign_role` / `build_runner.set_build` 等）未端到端
  校准，M0c smoke 会暴露差异
- `anthropic_provider.py` 0% 测试覆盖（依赖真实 API；M1 实接时验证）

### 安装

```powershell
uv sync --extra dev
# 端到端 smoke 额外：
uv pip install "git+https://github.com/AresSC2/ares-sc2@main"
```
