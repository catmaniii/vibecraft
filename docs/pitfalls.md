# 踩过的坑（pitfalls log）

> 这是一个**持续追加**的踩坑记录。每次踩到非显而易见的坑（尤其是"单测绿 / 看着对、真机/真局却炸"
> 那种），就来这里加一条：**症状 → 根因 → 修法/教训 → ref**。CLAUDE.md 只放指针，细节都在这。
>
> 用途：开工前扫一眼相关条目，别重复踩；调试卡住时翻一翻，可能正中。新坑往**最上面**加（倒序，新的在前）。

---

## 2026-07-27 · `unit.is_worker` 这个属性 python-sc2 根本没有 —— 恒 False / 静默抛异常,单测还跟着一起错

**症状**：坑道虫钻出敌方家后**从不扑农民**,`NYDUSRAID strike tgt=worker` 恒 0%(只有 structure /
army),而同一局 telemetry 的 `enemy_workers_harassed` 却有几十。两个数字互相矛盾。

**根因**：`sc2.unit.Unit` **没有 `is_worker` 属性**(只有 `is_mine` / `is_mineral_field` /
`is_carrying_minerals`),而且它**没有 `__getattr__` 兜底**。代码里 16 处在用它,两种写法两种死法:

- `getattr(u, "is_worker", False)` → **恒 False**。找农民的逻辑永远查不到人,静默走"拆建筑"分支;
  反过来,靠它**排除**农民的地方(如"主力在不在落点区"的计数)会把农民**当成军队**算进去。
- `u.is_worker`(直接访问,人族 BC 骚扰里 4 处)→ 每次 **AttributeError**,被外层
  `contextlib.suppress` / `on_step` 全局兜底吞掉,整段逻辑静默失效、日志里什么都看不到。

telemetry 那个数字来自 `on_unit_took_damage` 回调、不看这个假属性,所以它是对的 —— **两个数据源
对不上,正是"其中一个走了假属性"的信号**。

**为什么单测照不出来**:那几条相关单测的 mock 里写着 `is_worker=True/False` —— 它们**按同一个
错误假设建模**,于是产品代码和测试共享同一个幻觉,单测绿得很。改用真 `type_id` 建模后,5 条老
测试立刻转红,暴露出真实行为。

**修法**：新建 `src/vibecraft/bot/unit_kind.py`,判据统一走引擎真实存在的 `type_id`
(`is_worker()` / `is_army()`),15 处调用点全换掉;mock 也改用 `type_id`。两道防复发的门:
① 钉住"上游确实没有这个属性"(哪天 python-sc2 加了会红,提醒复核);② **静态审计**:全仓 `src/`
不许再出现 `.is_worker` / `"is_worker"` 字面量。

**教训**：外部库的**属性名**和 enum 名一样,都属于"望文生义就会错"的外部符号 —— 而且属性比 enum
更阴险:enum 名写错还会抛 AttributeError,属性配上 `getattr(..., 默认值)` 连异常都没有,**代码看着
在工作、其实分支从没走到过**。凡是判断外部对象"是不是某类"的写法,先 `dir()`/查源码确认字段真实
存在;拿不准就走**引擎一定有的字段**(如 `type_id`)自己组判据,别赌属性名。

**ref**：真局 `tgt=worker` 0 → 66 次(修后);`tests/unit/test_unit_kind.py` 的静态门;
推理图谱 F176/F177/I69/D115。

---

## 2026-07-25 · SC2 pathing_grid 原始值极性有歧义(sharpy 注释 ==0 可走 / python-sc2 ==1),硬编码=赌 50% 且测不出

**症状**：给凤凰做地形感知空军选路(D60,"只走地面部队去不了的地方"),要读 `game_info.pathing_grid`
判"这格地面能不能走"。我读 python-sc2 `bot_ai.py in_pathing_grid` 得出"`==1` 可走、`0`=悬崖",
就想直接读原值 `pathing_grid[(x,y)]==1` 判地面、给地面格加惩罚让路径贴悬崖。**独立设计评审(opus)拦下**:
repo 内 **sharpy vendor `grids/grid.py:29` 官方注释写的是 `is_pathable = pathing_grid[...] == 0`**——
跟 python-sc2 **相反**。

**根因**：pathing_grid 原始格值的极性在不同封装层/版本有相反约定(sharpy `==0 可走` vs python-sc2
`==1 可走`)。**只读源码就硬编码某个极性 = 赌 50%**;若赌反,ground_penalty 会去惩罚悬崖、奖励开阔地
→ 凤凰专挑地面军能走的地方飞,**比现状更糟,且 air_frac 指标也同步反向解读、自证"没问题"**——单测
(合成 grid)全绿也照不出(合成 grid 是我按自己以为的极性造的,回声)。属 CLAUDE.md「读源码≠运行时真值」
坑(同 salvage `SALVAGEBUNKER` 错 enum)。

**修法/教训**：**别自己读原值解读极性——用 python-sc2 的封装 `ai.in_pathing_grid(Point2)→bool`**
(其"True=地面可走"已被 proxy 放置 / nydus 落点等**真机功能**在用、真机验证过,等于借了已验证的极性)。
drop_path 层不碰 raw grid,由调用方传 `is_pathable=ai.in_pathing_grid` 回调。**真机终态反证极性**:
snap 版跑真局 `air_frac 0.36-0.43 < straight_frac 0.75-1.0`(路径真贴到地面够不到处)——极性若反 air_frac
会更高。**通用教训:引擎 grid/mask 的位/值极性,凡有现成"语义封装函数"就用它(它把极性验证过了),别读原始
数组自己猜哪个值是哪个意思。** ref: 图谱 F102 / D60,`drop_path.py plan_air_path`,独立设计评审 F1。

## 2026-07-25 · 独立评审"实现前"派、我却"边等边实现"→ A* 白写一版返工

**症状**：#2 地形选路,我按纪律派了独立设计评审 subagent(动手前),但**没等它回来就并行把全局 A* 版
实现 + 单测 + 接线全写了**(想给用户抢节奏)。评审回来判"别上 A*、改更简单的 snap 版"(A* 会重演被否的
edge_path 绕大圈 / 丢矿后切入角 / 极性未验证)——我那版 A* 整个作废重写成 snap。

**根因**：CLAUDE.md「架构/详细方案设计**动手实现前**独立评审」的意义就是**先评审再实现、省返工**;我把它
做成了"边评审边实现",评审的价值(挡掉错误方向)被我自己抵消,白写一版。

**修法/教训**：**"实现前评审"就是字面意思——设计评审 subagent 在跑时,实现侧最多写"评审怎么判都不会变"
的脚手架(如与算法无关的测试夹具),别写核心算法/主体实现。** 派了实现前评审就等它,别抢那几分钟换来整版
返工。ref: 本次 A*→snap 返工。

## 2026-07-14 · 前端模板重构 node --check 过、运行时 boot() 因命名冲突崩(交互全死)

**症状**：推理图谱可视化模板从"仅注入"改造成"可重入 `boot(nodes)` + drag-drop"(kg-template.html →
rg-viewer.html)后,`node --check` 语法检查全过、注入版**静态图看着渲染出来了**,但 boot() 运行时抛
`TypeError: Cannot create property 'onSearchInput' on number '1150'`,导致**所有交互(平移/缩放/点击/
搜索/键盘)全死**——图画出来了但点不动。

**根因**：新加的事件委托对象命名 `var H = {}`,撞了 boot 内**原模板就有的布局变量** `H`(高度:
`var ...W = 0, H = 0, CX = 0...`,后被赋成数字 1150)。局部 `H`(数字)遮蔽了模块级 `H`(对象),
所有 `H.onXxx = fn` 都落到数字上 → 第一个赋值就抛 TypeError,boot 在"连线监听"那段中断(静态渲染在此
之前已完成,所以图出来了、交互没接上)。

**修法/教训**：委托对象改名 `HANDLERS`(**别用单字母命名撞既有代码**)。**核心教训:前端模板/大函数
重构后必须做运行时验证,`node --check` 只验语法、照不出运行时命名冲突/作用域 bug。** 本次用 **jsdom
无头实跑**注入版 HTML(`new JSDOM(html,{runScripts:"dangerously"})` + VirtualConsole 抓 jsdomError +
数 `#nodeLayer` 子节点)逮到,并顺带验了 client-parse(直接调 `loadFromYamlText`)+ re-drop 清场
(二次加载 #nodeLayer 不翻倍、arrow marker 不重复)。Chrome 扩展没连时,jsdom 是"真浏览器截图"之外
最强的运行时自验手段。ref：推理图谱 skill 化批 D。

---

## 2026-07-13 · 自定义战斗微操注入自 2026-05-19 起一直静默失败（`self.combat.rules` 在 on_start 不存在）

**背景**：给爆死神军队注册 sharpy 自带 `MicroReaper`（死神手雷 KD8 + kite），照抄现有神族 DT/Zealot
微操注入的写法 `self.combat.rules.unit_micros[REAPER] = MicroReaper()`（common_bot on_start）。

**坑 1（真根因·属性不存在）**：真机 game log 里 `WARNING ... 注入失败: 'GroupCombatManager' object
has no attribute 'rules'`。`GroupCombatManager.__init__` 只设 `self.default_rules = MicroRules()`；
`self.rules` **要到 `execute()` 才赋值**（vendor `group_combat_manager.py:99`
`self.rules = rules if rules else self.default_rules`）。on_start 时 combat 还没 execute 过 →
`self.combat.rules` **AttributeError**，被注入块的 `except ... logger.warning` 吞掉。→ **神族 DT
智能撤退 / Zealot 去聚团微操从 2026-05-19 起从没真正生效**（每局 on_start 抛一次 warning，没人注意，
DT/Zealot 一直走 sharpy 默认 micro）。**正是 salvage 那类"看着对、有兜底日志、静默失败"反模式。**
修：注入目标改 `self.combat.default_rules`（execute 无显式 rules 时 `self.rules` 就 = default_rules）。

**坑 2（真根因·事后注入的实例没 start）**：改成 default_rules 后 MicroReaper **每帧崩**
`'MicroReaper' object has no attribute 'engaged_power'`（768 次/局，被 `on_step` 全局兜底刷屏，log
从 496 行涨到 34914 行）。根因：sharpy micro 是 `Component`，`engaged_power`/`cache`/`pather`/
`cd_manager` 等在 `await micro.start(knowledge)` 里才设（`micro_step.py:57`）。`default_rules.start()`
**只批量 start 了它当时持有的默认 micro**（`micro_rules.py:48-49`）；on_start 里事后 new 出来替换/
新增的实例**没经过 start**。修：注入后**手动 `await inst.start(self.knowledge)`** 再放进
`unit_micros`（on_start 是 async，`super().on_start()` 后 `self.knowledge` 已就绪）。DT/Zealot 同理。

**处置（2026-07-13，重要）**：修复**验证可行但最终 revert、未上线**，原因两条：① 给死神注册
`MicroReaper` 实测**拖累已确认胜局**——vs Medium **6/6 → 3/6**（`run_percentage=0.15` 让死神血低于
15% 才退、在战斗里待太久多送死，破坏了赢下 Medium 的『放手攒军队』），vs Hard 仍 0/6 无改善 → 死神
军队正面被 Hard 坦克/AoE **兵种硬克**是天花板，微操救不了；② 修 injection 机制会**顺带激活神族
DT/Zealot 微操**（它们随这个 bug 一起沉睡一年多，~10 个神族 build 都是围绕『它没生效』调出来的）→
激活是**神族行为变更**，需独立跑全套神族 build 回归，属另一个任务，不该塞进死神任务里当副作用冒险。
→ **common_bot.py 整块 revert 到 HEAD**（零 diff），bug 作为**已知待修**记在这里,将来专门开任务修
（修时务必：先跑全神族 build 回归基线 → 应用 default_rules+await start → 逐 build 对比不回归再上）。

**教训（正向纪律）**：
- **注入/替换框架内部对象前，先确认目标属性此刻真的存在**：`self.combat.rules` 是延迟赋值属性，
  写代码时想当然它一直在。凡是往框架管理器里塞东西（`xxx.rules` / `xxx.unit_micros` / 任何 late-bound
  属性），先 grep 该属性在哪、何时赋值，别照抄一个"看着能用"的现有写法——那个写法本身可能就是坏的。
- **"注入成功"必须有生效实锤，不能只看没报错**：这次两层假象——① `.rules` 版本连 log 都打了
  "注入成功"？不，它走 except 打了 warning，但 warning 淹没在真机 log 里没人查；② default_rules 版本
  log 也会打"注入成功"但每帧崩。**判据要用行为实锤**：MicroReaper 生效 = 游戏里真有 KD8 手雷施放
  （grep `KD8`）；DT 微操生效 = 遇 detector 真回撤。别信"注入成功"字样。
- **异常兜底 warning 要当"有真 bug"对待**（CLAUDE.md 已有纪律）：`注入失败` warning 每局都打，却因为
  被 except 降级成 warning + 游戏照跑而被无视了一年多。game log 里任何 `注入失败`/`捕获异常` 都要查根因。

---

## 2026-07-12 · 坑道虫落地诊断"0% 落地"是假阴性：读错 telemetry 字段（units vs buildings）＋ push=10 超出实测视野安全距离

**背景**：用户报"兵/坑道网络/视野都有，坑道虫就是不放"，怀疑 can_place 判定有 bug。加 per-tile 诊断
日志（`NYDUSDIAG` 打每个候选格 vis/place/威胁/离最近 OL 距离）+ 真局 `nydus_landing_diag.py` 排查。

**坑 1（假设错）**：一开始笃定是 `can_place_single(NYDUSCANAL)` 把合法落点误判 False（"虫族建筑查
菌毯"）。诊断日志实锤：**所有候选格 `vis=False`、`place=None`（没视野连 can_place 都没触发）**，
`vis但放不了=0`。**can_place 根本没参与失败**——是**视野**问题。幸好先加日志没瞎改（反向审查纪律）。

**坑 2（真根因·视野几何）**：`dOL_center`（最近 OL 离敌方中心）**191 帧稳定在 27**。OL 活着、稳定
漂在其漂浮点（边缘格 + 顺悬崖外推 `_OL_PUSH=10`）；敌方中心到高地边缘 ~15 → OL 在边缘外 ~10-12 →
**落点边缘格离 OL ~10，卡在 sight=11 极限外沿 → is_visible 绝大多数帧 False**。而推理图谱 J3 **实测
验证过的安全距离是 8.2-8.4**，代码却推了 **10**——**push 超出验证范围**。修：`_OL_PUSH 10→7`。

**坑 3（真根因·视野与落点解耦）**：落点候选按"离矿最近"排序，但那些格常在**没有 OL 驻守的扇区**
（dOL 28-34）→ 永不可见。修：新增 `_ol_vision_edge_tiles`——对每只驻守 OL 取**离它最近的可放边缘格**
作首选（必在其视野 + 可放），OL 飘哪落哪，把落点耦合到"当前真有视野的地方"。

**坑 4（格心坐标）**：worm 实际落点全是 **X.5/Y.5 格心**（114.5,114.5…），而扫描出的边缘格是**整数
坐标（格角）**→ `can_place_single(整数)` 返回 False、`can_place_single(格心)` 返回 True。修：候选格
`snap` 到 `floor+0.5` 格心（`vis=True place=False` 的 OLvis 主格由此转 True）。

**坑 5（测量假阴性——最坑的一条）**：诊断脚本判"canal 落地"读的是
`snapshot["units"]["NYDUSCANAL"]` → **恒 0**，报"落地 0/4 = 0%"。但 telemetry 里 **NYDUSCANAL 明明
出现几十次**——它是 **building**，记录在 snapshot 的 **`buildings`** 子字典 + 独立
`building_started`/`building_complete` 事件里，**`units` 字典只放机动单位、不放建筑**。**读错字段 →
真在落地的局被判成 0% → 差点又朝错误方向猛修。** 修正字段（`building_started` distinct tag 数 = 真
落地次数）后，同一批日志实际是 **6/8 落地**，修复前后 2/4→4/4。

**教训（正向纪律）**：
- **"0%/全失败"的自验结果先质疑指标本身**，尤其读结构化日志某个嵌套字段时——先 `grep` 原始
  telemetry 确认那个信号到底在哪个 key（units? buildings? 独立事件?），别拿一个恒 0 的字段当真相
  猛修。这是"覆盖类硬门要断言分支真被触达"的姊妹坑：**度量口径错 = 整个结论错**。
- **加日志再定位，别凭假设改**：can_place 假设错、真根因是视野几何——是诊断日志（非猜测）救的场。
- 外部引擎符号/坐标要按**真机实测口径**：worm 落点 X.5 格心、OL 视野安全距离 8（非 10），都以真机
  数据为准，别信 statement 里写的约数。ref: `nydus_landing_planner.py`、`scripts/nydus_landing_diag.py`。

---

## 2026-07-12 · 侦查 OL 在家↔敌方来回震荡：路径跟随用 `next(dist>3)` 把起点当目标 + 我连续两次误诊

**症状**（真机用户报"去补视野的 overlord 一直被某个力量拉回基地、又派出去，反复拉扯"）：侦查 OL
飞不到敌方，卡在家门口来回抖。**看着像"别的 act 在抢我的单位"**。

**我连续两次误诊（这才是真教训）**：
1. 第一次凭"代码里 PlanZoneGather 拉 free_units"**脑补**根因是"OL 没 Reserve 被 gather 拉走"，加了
   `set_task(Reserved)` 就宣布修好。
2. 验证时只数"`指定侦查 OL` 日志出现 2 次"（= OL 只被指派 2 次，没反复重派），就断言"震荡消失"——
   **这个指标根本没测位置有没有震荡**，是拿一个不相干的代理指标自欺。用户再打一把，照样震荡。

**真根因（加 OLDIAG 每帧记"我下发的目标 vs OL 实际 order"才抓到）**：是**我自己的路径跟随 bug**，
跟别的 act 毫无关系。`plan_avoid_path` 返回 `path=[起点P0, 漂浮点fp]`，我用
`wp = next((p for p in path if scout.distance_to(p) > 3.0), path[-1])` 选目标——它取"第一个离我>3 格的
点"。OL 一离开起点 P0，`distance_to(P0)` 就 >3 → **把起点(=家)当目标拉回去**；回到 P0 附近 <3 → 又转去
fp；如此在**家↔漂浮点之间自己来回震荡**。OLDIAG 实锤：`issue` 目标在 `dEnemy=27`(去敌方) 和
`dHome=5`(回家) 之间交替，OL 位置 `dHome` 死死卡在 1-5。

**修法**：路径跟随要**按顺序推进索引**，绝不回头指起点：`idx=min(self._path_idx.get(tag,1), len-1)`
（从 1 起、跳过 path[0]=起点），`while idx<len-1 and dist(path[idx])<=3: idx+=1`，target=`path[idx]`。
修完 OLDIAG 验证：dEnemy 从 122 平滑降到 27 后**稳在 27 不动**，震荡消失。

**教训（正向纪律，已进 CLAUDE.md 自验章节精神）**：
- **"拉扯/被抢"类现象先怀疑自己每帧下发的命令自相矛盾**，别第一反应甩锅给别的 act。加一行"记录我这帧
  实际下发的目标坐标"就能秒辨是不是自己的 bug。
- **验证必须盯真症状本身**（位置有没有震荡 = 逐帧 pos/order 轨迹），**别拿代理指标**（"指派次数"）自欺——
  这是本会话"骗过自己"的又一例，同 salvage『验终态非中间 trace』。
- 通用坑：**"沿路径飞"绝不能用"第一个离我远的点"选 waypoint**（起点永远满足"离我远"→ 把你拽回起点）；
  必须维护单调前进的 waypoint 索引。ref: `nydus.py::_SendOverlordToEnemy`。

---

## 2026-07-09 · 坑道虫突袭：`_prune_dead` 抢在乘客判定之前跑，把"刚装载成功"误判成"死了"

**症状**（nydus raid polish 第一轮真局首跑）：`NydusRaidAct` 单测全绿（16/16）、构造回归
过、ruff/mypy 干净；真局自验 `scripts/nydus_selftest.py` 却显示 `NYDUSRAID load` 正常
触发（38 个单位、`wave=1` 打出）、`canal.cargo_used>0` 也确认过（`_issue_unload_canal`
真的探到 `UNLOADALL_NYDUSWORM` 并发了指令）——但 `NYDUSRAID transit`/`strike` 事件**永远
是 0**。单测测不出，因为单测手动摆好 `_state`/`army` 快照分别调用 `_tick_stage`/
`_tick_transit`，从没测过"同一帧内乘客判定和剪切互相抢跑"这个时序。

**根因**：`_tick()` 里 `_prune_dead(army)` 排在 `_tick_transit`（乘客判定）**之前**。
SMART 装载生效那一刻，单位在**同一帧**内"从 `self.cache.own(ROACH)`（army 快照）消失 +
出现在 `network.passengers_tags` 里"——这是 SC2 侧的正常行为，不是 bug。但
`_prune_dead` 看到这个 STAGE tag 不在 army 里，直接当"死了"删掉它的全部状态（`_state`/
`_state_since`/`_loading_since` 等），**在 `_tick_transit` 还没来得及检查 passenger_tags
之前**。于是这个 tag 永久从追踪里消失，既不是 STAGE 也不是 TRANSIT，`_tick_transit`
的 `if self._state[tag]=="STAGE" and tag in passenger_tags` 判定根本轮不到它——单位
真的进了坑道，代码却"查无此 tag"，永远等不到 TRANSIT/STRIKE。

**修法**：把"STAGE→TRANSIT"的 passenger_tags 判定拆成独立方法 `_promote_stage_to_transit`，
挪到 `_prune_dead` **之前**调用（`_tick()` 开头）。剪切时机永远要让"这一帧刚发生的合法
状态转移"先跑完、稳定下来，再判定"剩下没转移的是不是真死了"——不能反过来。真局验证：
修前 `transit=0`，修后同款自验 `transit=13`（真实观察到装载→坑道内确认的转移）。

**教训（可推广）**：任何"单位可能在**同一帧**内从一个可见集合消失、同时出现在另一个
数据源里"的场景（本例：从 `ai.units` 消失 + 出现在 `passengers_tags`；同类还有传送/
载具/变形），**判定"合法转移"的代码必须排在"判定死亡并剪除状态"的代码之前**，否则
剪除逻辑会把"正在发生的转移"误吃成"死亡"。这类 bug **单测测不出**（除非专门构造
"army 缺失 + 目标数据源命中"的组合场景，见新增回归测试
`test_promote_stage_to_transit_runs_before_prune_dead_in_full_tick`），只有真局/真数据
流才暴露——再次印证"验终态、真局自验，不能只信单测+中间 trace"（同 salvage 铁律）。

**ref**：`nydus_raid_act.py::_tick`（`_promote_stage_to_transit` 调用顺序）+
`_promote_stage_to_transit`；回归测试 `tests/unit/test_nydus_raid.py`；
设计 `docs/plans/2026-07-09-nydus-raid-polish-design.md`。

---

## 2026-07-07 · 两个"真局/真 LLM 才暴露、我在 sandbox 空转追错方向"的坑

**共同教训**：这两个 bug 的真根因**都只在最接近真实的环境才出现**（有对手的真局 / 真 LLM），
我却一度在**复现不了根因的 sandbox / mock**里空转调参数。判据：**同一现象在受限环境反复调参不收敛
（间距 2/3↔3/3↔2/5 跳），就是环境错了、不是参数错了**——立刻换最接近真实的自验手段。

1. **人族 4bb 野三兵营真局建不出 = 侦查农民(ScoutWorker)把 proxy 偷家农民抢去探路。**
   - 症状：真局(有对手)里三个偷家农民有一个反复被拉去探路、到不了 proxy 点建不出兵营，还卡死顺序
     建造门；无敌方 sandbox 自验里完全复现不了。
   - 根因：`ScoutWorker._pick_scout` 选"离敌最近"的农民探路，而 proxy 农民正朝敌方走 = 全场离敌最近
     → 被抢。它排除了偷矿农民 + 玩家 claim 农民，**漏了 proxy 建造农民**。
   - **我追错方向的过程**：先在 sandbox `proxy_placement_selftest`(无敌方)里追"3 农民并行建造方差"，
     调间距 4/5/6/7、边缘净空、部分/完全顺序门、重规划一大堆，结果 2/3↔3/3↔2/5 跳、久久不收敛
     ——因为 sandbox 无敌方、根本没有 scout 抢农民这个真根因。直到**用户真机反馈"农民被拉去探路"**
     才转向，用**有对手的 `build_acceptance`** 一验就中，真局 5/5 过。
   - 修法/教训：`_pick_scout`/`_scout_claimed_by_player` 排除 `proxy_builder_tags`（proxy act 每帧发布
     自己农民 tag）。**"某现象只在真实对战才出现"→ 别用无敌方 sandbox 追，用 `build_acceptance`。**
     ref: `scout_worker.py::_pick_scout`, `proxy_rax_act.py`(发布 tag), commit 0501052。

2. **神族野2VS"修水晶+下两个VS"只建水晶不建星门 = 代理链 chain_id 在水晶卡上丢失。**
   - 症状：玩家指令代理链，水晶建成、两个星门永不建（占钱不建）；单测 / mock 全绿照不出，真局
     `proxy_chain_selftest` + 真 LLM 才暴露。
   - 根因：水晶 `build_at` 卡 `activate_when=unit_arrived`，`director.py` 只对 `chain_structure_ready`
     提取 chain_id → 水晶卡 chain_id 丢失 → 选了另一个随机农民建水晶 → settle 靠"建水晶农民 tag 在
     `_task_chains` 里"反查失败(链绑定=False) → 后续星门卡落点永不刷新。
   - 修法/教训：`BuildAtPayload` 加 `chain_id` 字段、水晶卡带上、settle 直接用它反查(不靠农民 tag)。
     且真 LLM 要**真的输出** chain_id 才生效（改了 prompt）→ 必须**真 DeepSeek 跑一遍确认**，不能只
     看 mock 测过。**内部自洽(单测+mock)≠真机生效**（同 salvage 纪律）。
     ref: `models.py::BuildAtPayload.chain_id`, `director.py`(settle 用 info["chain_id"]), commit 8678ec6。

---

## 2026-07-04 · BC 群骚扰"改 4 次坏 4 次"——反应式状态机的三个隐形坑

**背景**：BC 群骚扰 #580/#583/#584/pre-pass 连改 4 次，用户真机每次都发现新毛病（第一艘杵家、去一半被拉回、走回家不传送、路径乱），最后**推倒重写**成单状态机（#587）才理顺。三个隐形坑：

1. **"第一艘杵家 2 分钟"的头号真因 = `target_anchor is None` 时 BC 原地待命，不是"新兵拽回前排"。**
   - 症状：真机第一艘 BC 出来后在家附近杵 ~2 分钟不去骚扰，直到第 3 艘才整群动。我前 3 次都往
     "群体决策把前排拽回"上修，全打偏。
   - 根因：未入队 BC 只在 `target_anchor is not None` 时才 move，否则 `else: 原地待命`。而
     `target_anchor`/stage 都依赖 `zone.mineral_line_center`——**要侦察到敌矿线才有值**。BC 第一艘
     ~5-6min 出来时敌矿没揭开 → target None → **站家里**；它自己不动就永远揭不开 → 死锁，直到别的
     单位揭开（时间上≈第 3 艘）。是**时间/侦察相关，不是数量相关**。
   - 教训：**"给单位一个位置目标"的逻辑，必须保证目标永远算得出**——依赖侦察/动态数据的目标要有
     **兜底**（这里用 `enemy_start_locations[0]` 开局即知，推兜底 stage 让第一艘立刻出门揭视野）。
     opus 评审读代码一眼揪出来的，我盯着"群体决策"3 次没看到——**同一现象反复修不好，就是假设错了，
     回 Phase 1 重新定位**（CLAUDE.md 已有此条，又验证一次）。ref: `bc_raid_act.py::_stage_for_group`。

2. **"去一半被拉回主基地" = 切矿时 STAGE 路径从 home 重算、per-BC idx 归 0 → 半路 BC 从"路径第 0 点=家"重启 = 飞回家。**
   - 症状：两个大件去一半，一个突然飞回主基地（没掉多少血）。
   - 根因：STAGE 贴边路径按 `(did, stage_key)` 缓存、**从 home 起算**；picker 切目标矿 → stage_key 变 →
     路径从 home 重算 + `(tag,stage_key)` idx 归 0 → 正在半路的 BC 被塞回"从家出发"的 waypoint[0] → 飞回家。
   - 教训：**"目标点/路径缓存"在目标变化时，重算的起点要用单位当前位置、不能用固定原点**（CLAUDE.md
     "目标坐标一次锁定"的延伸：变了要从当前位置续，别从头来）。修法：路径改 `(tag, stage_key)` 键、
     `plan_avoid_path(bc.position, ...)` 从当前位置算。ref: `bc_raid_act.py::_stage_wp`。

3. **自测假信号 + 分析器误报**：
   - **非实时(fast)自测反复给假信心**：fast 显示"squad engage、一起走"，realtime/真机全坏——CLAUDE.md
     明记这个坑我又栽（第 4 次）。**反应式微操/时序敏感的东西，非实时自测不作数**。
   - **分析器用错度量导致误报**：批量分析器用 `dstage`(到 stage 距离)判"回飞"，但切矿时 stage 点变了
     `dstage` 会跳变 → 把"BC 继续前进但目标换了"误判成"回飞 x5-8"。改用 `dmain`(到敌主矿**固定点**距离)
     才对——**判"单位有没有往回走"要用到固定参考点的距离，别用到移动目标的距离**。
   - 教训：搭自动判读工具时，**度量必须对移动目标免疫**；拿到异常信号先质疑度量本身。ref: `scratchpad/bc_analyze.py`。

## 2026-07-04 · 「升不了却照占矿」的 priority 预留把科技链饿死（矿 float 到爆也不建）

**症状**（#582，真机 bc_rush）：兵营好了、钱气都够，工厂却迟迟不下；真机矿 float 到 **445** 就是不建，
工厂晚 85s。build_acceptance（非实时、spec 松）没报，掩盖了它。

**根因（读 sharpy `MorphBuilding.execute` + 算术 + isolation 确认）**：`MorphOrbitals(2)` 每帧对
**ready 的** CC 尝试升轨道——能付钱就 `subtract_cost` 扣 150、否则 `reserve_costs` 预留 150，**但不检查
CC 是否正忙**。bc_rush 里 CC 一直在造 SCV（priority、目标没到），**CC 永远没空升轨道、却每帧照占 150 矿**。
这 150 + SCV 50 + 房子 100 + 工厂自己 150 ≈ **450** = 工厂实际建出时的矿量（真机 t=177 M=445，算术精确吻合）。
本质：**一个「当前根本执行不了的动作」（忙 CC 升不了轨道）却持续 reserve 资源，把 priority 次序里排在它
后面的科技链饿死**。

**修法/教训**：
1. vendor patch `MorphBuilding`：**只对空闲 building（`not target.orders`）尝试/预留**，忙则跳过——绝不为
   「此刻执行不了的动作」占资源。
2. plan 层：把 SCV、MorphOrbitals 挪到 BC/科技链**之后**（预留次序 = BuildOrder 列表次序；越靠前越先占钱）。
   SCV 改 non-priority 无上限（只吃余钱、绝不占大件的钱）。
3. **通用教训**：任何 `reserve_costs`/`subtract_cost` 前先确认「这个动作此刻真能执行」——建筑在忙、前置没到、
   目标不可达时**别占资源**，否则它会静默饿死 priority 次序里的其它东西，表现为「钱堆着不花、后面的建筑不下」。
4. **诊断教训**：build_acceptance（非实时 + 松 spec）**掩盖**了这个 bug（工厂 157s 在 195±45 内 PASS）。
   真机是**实时**——realtime 复现才暴露（工厂 177s + 矿 float 445）。**凡是「钱堆着不花/建筑该下不下」的疑似
   资源预留 bug，用 realtime 单人复现（`rt_factory_probe.py` 那种）＋读 telemetry 的矿/production 轨迹**，
   别只信非实时 build_acceptance。

**ref**：`vendor/.../morph_building.py`（`# vibecraft:` 空闲CC才占矿）/ `plans/bc_rush.py`（SCV/MorphOrbitals 挪到 BC 后）/
CHANGELOG #582。

---

## 2026-07-03 · 接近 waypoint 全落在 near/far 门内 → 「从背后接近」整段永不执行（结构缺陷）

**症状**（#581 BC 骚扰接近重构）：设计了「场外集结点 stage + 绕主基地垂距避障 + 从矿背后切入」的
`plan_harass_approach`，单测全绿、几何函数本身正确。但接进 `GroupHarassAct` 后，"从矿背后切入"这一
用户拍板的核心目标**根本不会发生**——BC 仍从侧面/正面直插矿线。

**根因**：`_raid_move_point` 用「BC 距目标矿区中心 > `_APPROACH_DIRECT_FROM_ZONE`(24) = far → 走接近路径，
否则 near-micro 直扑农民」这个**距离门**决定 near/far。而接近路径的关键点——矿后点(~7)、stage(~14.5)、
避障拐点(~18)——**全部 < 24**，落在 near 环内。于是 BC 一进 24 环就被 near-micro 接管、把 stage/behind/
避障 waypoint 全丢弃，接近路径实际只把 BC 送到「24 环边界」就交棒 → 避障几乎从不生效、从背后切入永不执行。
这不是调参能盖过的，是**门的判据（距中心）与 waypoint 距离范围的结构冲突**。

**修法/教训**：near/far 交接**不能用「距某中心的距离」当判据**，当接近路径的落点本就靠近该中心时必然冲突。
改为**由「接近 waypoint 链是否消费完」驱动**——加 `_approach_arrived` 闩锁，只有 BC 沿路径走到最后一点
（矿后点）且 `dist<_ENGAGE_RADIUS` 才置 True、才放行 near-micro（闩锁 sticky，防到位后追农民跑远又被判
「未到达」反复重接近抖动）。**通用教训**：凡是「先走一段路径再切换行为模式」的逻辑，模式切换要**用
"路径是否走完"当门，不要用"距终点/某锚点的距离"当门**——当路径的中间点/终点本就在那个距离阈值内时，
距离门会提前触发、把路径吃掉。这坑单测抓不到（几何函数单独测都对），只有把接近函数接进"距离门"的
宿主里、看真局 BC 是否真按 waypoint 飞（外部坐标 / behind_dot 方向断言）才暴露。opus 设计评审揪出的。

**ref**：`bc_raid_act.py` `_raid_move_point` / `_approach_wp`(`_approach_arrived` 闩锁)；真局验证用
`BCRAIDTRACE arrived ... behind_dot`（>0=从矿背后到达）+ per-BC `dmain`；设计
`docs/plans/2026-07-03-bc-harass-approach-micro-design.md` 评审处置 #1。

---

## 2026-06-29 · i18n 覆盖门「假阳性」：把分支列进 case 却被前置条件短路跳过，门绿仍泄漏

**症状**（方案 A 英文本地化收尾）：写了「英文 snapshot 零中文」动态硬门，`test_en_condition_text_no_chinese`
的 done_when case 列表里**明明列了** `unit_count_built_since` / `time_elapsed_since`，门跑 5/5 PASS。
但 opus 评审实测发现 en 模式下产能 override 卡进度条仍显示中文「个」——门绿 + 真局泄漏。

**根因（两层，叠加）**：
1. **门把分支喂进去 ≠ 分支被执行**：测试用的 `_en_director` 构造 Director **没传 event_bus** →
   `self.task_monitor is None`。而 `_describe_condition` 里那两个 kind 的入口是 `if kind=="..." and tm is not None:`，
   tm 为 None → **整段被短路跳过**，直接落到通用分支返回 ASCII kind 名（纯 ASCII，CJK 门当然抓不到）。
   于是 `cond.buildN/unitCount/afterSec/unitSec` 这几个 key **实际零覆盖**，门却"宣称"覆盖全分支。
2. **`t()` 把"有意空译"当缺译回退中文**：`cond.unitCount` 英文有意留空（"个"英文无量词，前端 preview
   也用 `L('个','')`），但 `t()` 旧实现 `entry.get(lang) or entry.get('zh') or key` —— 空串 falsy →
   回退 zh「个」。即使分支被执行，这条也会泄漏。

**修法/教训**：
- **门修**：`_en_director` 必须传 `event_bus=EventBus()`，并 `assert d.task_monitor is not None`——
  让被声称覆盖的分支**真的执行**。**正向规则见 CLAUDE.md**「覆盖类门要断言分支真被执行」。
- **`t()` 修**：改成 `entry[lang] if lang in entry else 回退` —— locale **显式存在该键**（哪怕空串）就用它，
  不回退。否则"某语言该省略的量词/助词"全会回退中文。
- **额外加门**：`test_all_referenced_i18n_keys_exist` 静态扫源码所有 `_i18n_t("k.k")`，断言 key 在
  strings.json 且 en 非 None（堵"缺 key→t() 回退 ASCII key→玩家看到生字符串"，这类 CJK 门也抓不到）。
**ref**：`tests/unit/test_locale_snapshot_gate.py`（`_en_director` event_bus + 三道门）；`src/vibecraft/i18n/__init__.py` `t()` 回退；#578；opus 评审。

---

## 2026-06-21 · sharpy act 内直接 `unit(ability)` 对 idle 单位被 prevent_double_actions 静默丢弃

**症状**（#560 spare CC 飞去开矿）：act 里检测到 idle spare CC，`get_available_abilities` 确认
`LIFT_COMMANDCENTER` 可用，`cc(AbilityId.LIFT_COMMANDCENTER)` 也调了、trace 也打了，但 CC **永远
不起飞**（never COMMANDCENTERFLYING）。诡异的是同样的 `cc(LIFT)` 在 common_bot `on_step` 的探针里
**能成功**。

**根因**：与 salvage 同一个坑——python-sc2 `prevent_double_actions` 对 `orders==[]`（idle）的单位
丢弃 UnitCommand（隐式返回 None 被 filter 滤掉），ability 永不发到 SC2。spare CC orders=0 必中。
on_step 探针"碰巧"成功是时序巧合，不能作为 act 内直发可靠的证据（典型"同源回声"假阳性）。

**修法/教训**：**任何"对可能 idle 的单位发 ability"的代码（不论在 director 还是 sharpy act），
都不能直接 `unit(ability)`**——走 `_vibecraft_bypass_actions`（构造 `UnitCommand(ab, unit, target,
False)` 入队，common_bot on_step 在 super 后 `_do_actions(bypass, prevent_double=False)` 串行发出，
并记 ActionResult）。**正向规则**：发 ability 给 idle 单位 = 默认用 bypass，别信直发；自验必看
**外部终态**（单位真起飞/建筑真消失），别信"调了 + trace 打了"。
**ref**：`spare_cc_expand_act.py::_bypass`；common_bot.py:740（cast_unit_ability 同款）；#560。

---

## 2026-06-20 · `pip install -U funasr` 把 protobuf 降级 → 每次开局子进程崩（runtime_version ImportError）

**症状**：server 起得来、PWA 连得上，但**一开局**子进程立刻 `sc2=crashed`，detail=
`ImportError: cannot import name 'runtime_version' from 'google.protobuf'`。诡异的是同一 venv 几小时前
自验还能跑完整局。

**根因**：有人跑了**裸 `pip install -U funasr`**（log 里 funasr 自己提示的那句）→ 它升级 `modelscope`，
而 `modelscope[nlp]` 死锁 `protobuf<3.21` → pip 把 protobuf 从 lock 的 **7.34.1 降到 3.19.6**（连带拖进
一大堆 ML 包：transformers/tokenizers/keras 系 optree/ml_dtypes…）。但 SC2 的 `burnysc2`+`pys2clientprotocol`
要 `protobuf>=6`（`s2clientprotocol/*_pb2.py` 是 protoc 5.x 生成的，开头 `from google.protobuf import
runtime_version`，3.19 没这东西）→ 每个**新开的游戏子进程** import `sc2api_pb2` 即崩。**已在跑的 server
进程不崩**（它启动时 import 的是旧内存里的 7.x），只有 import-from-disk 的新子进程崩 → "server 活着但
一开局就 crash"。

**修法/教训**：`.venv/Scripts/python.exe -m pip install "protobuf==7.34.1"`（恢复 lock 版本）即可——
ASR/funasr **不受影响**（funasr 跑的是 audio 路径，protobuf 无所谓；`<3.21` 只是 modelscope 的 **nlp
extra** 声明，运行时用不到）。**正向规则**：本项目**严禁裸 `pip install -U <pkg>`**（CLAUDE.md 已有，
这次实锤后果）——它会按别的包的 pin 静默降级共享依赖（protobuf 是重灾区：SC2 要新、modelscope[nlp]
要旧，天然冲突）。装 ASR 相关一律 `uv`，或装完立刻 `pip install protobuf==7.34.1` 救回。诊断手法：
`pip show protobuf` 看版本 + `ls site-packages/*.dist-info` 按 mtime 找"刚被动过的一批包"。

**ref**：burnysc2 需 `protobuf>=6,<8`；modelscope[nlp] 锁 `<3.21`；lock 版本 7.34.1；commit（仅环境修复，无代码改动）。

---

## 2026-06-20 · BC 骚扰"二矿打不到农民"：避敌寻路 plan_drop_path 把单位挡在它要打的矿外；+ 自验聚合判据掩盖了 per-instance 失败

**症状**：BC 自动骚扰，主矿"还行"，但**二矿(natural)后面的点打不到农民**；追农民"没走几步就回去"。
连续几把反馈同一问题，我前几次都只调**锚点**（矿后偏移 → patch 质心 …），每次都没解决。

**根因（真凶，前几次全没找对）**：`_raid_move_point` 在 BC 离锚点 >7 格时用 `plan_drop_path` 接近。
`plan_drop_path` 的设计职责是**绕开**敌方 zone——把 waypoint 推离 zone 中心 `R_MINERAL_AVOID(15)
+ PUSH(5) = 20` 格。但骚扰目标矿线**就在敌方 zone 内** → plan_drop_path 把 BC 永远推到离矿 ~20 格
的地方，距锚点恒 > `_ENGAGE_RADIUS(7)` → **永远走"远程接近"分支、永远进不了"贴农民"分支** → 坐在
矿外打不到农民。二矿几何最不利所以最明显；主矿"还行"只是绕行点碰巧落得近。**锚点根本不是病根**——
病根是"用一个**避开 zone** 的寻路工具去接近一个**在 zone 内**的目标"，方向自相矛盾。

附带第二个根因（追不够远）：`_nearby_worker_center` 以 **BC** 为圆心半径 11 找农民 → 农民一逃出 11
就掉出质心 → 追击目标缩回 → "没走几步就回去"。

**修法/教训**：
- **接近一个"在敌方 zone 内"的目标，不能用"避开 zone"的寻路**。一旦离**目标 zone 中心**够近
  （≤24，> 躲避气泡 20）就**直飞扎进去**，绝不躲自己要打的那个 zone；只有真正远途才用 plan_drop_path
  绕开**其它** zone。
- 追逐类微操的搜索圈以**目标锚点**为圆心（沿目标区域追），别以**自己**为圆心（逃出半径就丢）。
- **自验聚合判据不能用 best/min-over-all 掩盖 per-instance 失败**：旧 `bc_harass_selftest` 只断言
  `best_min_dist < 15`（所有 BC 里**最近的那一个**）。主矿 BC 达标 → 整测 PASS → **完全掩盖了二矿
  那艘 BC 被挡在外面**。多实例/多目标场景，必须 **per-instance / per-target 分别断言**（改成记 BC
  到三个矿矿线各自距离，主矿+二矿都得 <9）。实测修后 d0=1.3 / d1=1.7 / d2=3.0。

**ref**：`bc_raid_act.py` `_raid_move_point`（`_APPROACH_DIRECT_FROM_ZONE` 直飞门）/ `_harass_geom`
（返回矿区中心 + 锚点回 mineral_line_center）/ `_nearby_worker_center(anchor)`；`bc_harass_selftest.py`
（分矿 d0/d1/d2 断言）；commit 54519d1。

---

## 2026-06-19 · combat_intent_override 平时常默认 "defend"，据此"全军喊停"会误停被 claim 单位

**症状**：BC 自动骚扰重构后，真局自验里 factory 建卡正常、map 发布正常、act 也看到 carded=3，
但 BC **整局压在家、零骚扰**（flyout=0）。

**根因**：act 的"全军喊停"读 `knowledge.vibecraft.combat_intent_override`，treat
`in {retreat,attack,defend,hold}` 为"玩家接管 → 让位"。但 `combat_intent_override` **平时就常被
bot 自己置成 "defend"**（不是玩家指令，是 bot 默认战斗姿态）→ 持卡 BC 每帧命中 suppressed → 飞回家、
永不骚扰。我误以为它只在玩家按全军按钮时才有值。

**修法/教训**：**被 claim 的独占单位不该读 `combat_intent_override` 做喊停**（CLAUDE.md 控制权
规则 2：全军命令只作用自由单位，本就不碰被 claim 的）。要停某个被 claim 单位 → ❌ 它的指令卡
（release）。**正向规则**：凡是"被 claim/Reserved 的单位"的微操，**不要**用 `combat_intent_override`
判断玩家是否接管——它对 free 单位才有意义，且常有 bot 默认值；claim 的生杀只看"卡在不在"。
**ref**：`bc_raid_act.py` `_tick`（删掉 _is_suppressed）；#561；真局自验 `bc_harass_selftest.py`
（终态 dist 1.5 PASS）。

---

## 2026-06-19 · 打到一半异常退出：doctrine plan 里占位 enum 训练崩整局（一坑两层）

**症状**：真局打到 ~11:50（bc_rush 开局完成、auto-switch 到 persistent_skyterran doctrine 之后）
**整局异常退出**。日志：`AssertionError: Ability is not of type 'AbilityData', but was NoneType`
@ `act_unit.py:131 calculate_ability_cost(unit_data.creation_ability)`，loguru traceback 变量
`UnitTypeData(name=Viking)`。

**根因（第 1 层，具体）**：`bc_late.py:69` 写了 `TerranUnit(UnitTypeId.VIKING, 4)`。`VIKING`(id 1940)
是**不可训练的占位 enum**（`creation_ability=None`），星港只能训 `VIKINGFIGHTER`(35)。这是 #534
的同类 bug——只是 #534 修的是**玩家指令/auto-prereq 路径**（Director `_UNIT_NAME_MAP` 归一），而
**sharpy doctrine plan 里硬编码的 `TerranUnit(VIKING)` 完全绕过那层归一**，直接走到 `execute()`
的 `calculate_ability_cost(None)` → assert 崩。bc_rush 开局完成 → 切 persistent_skyterran(BcLate)
→ 这条卡跑到 → 杀整局。

**根因（第 2 层，为什么单测没拦）**：① `test_terran_plans_construct.py` 只测了 **opening plan**，
**没测 doctrine plan**（bc_late/liberator/mech/bio_max）——auto-switch 进来的恰恰是 doctrine。
② 即使测了构造也拦不住：占位 enum 的 `TerranUnit(...)` **构造期不报错**，只有 `execute()` 运行时才崩。

**修法/教训**：
1. `bc_late.py` `VIKING → VIKINGFIGHTER`。
2. **doctrine plan 必须和 opening 一起进 construct 测**（已补 4 个 doctrine 进 `_TERRAN_OPENINGS`）。
3. **加静态占位 enum 审计**（`test_terran_plan_no_placeholder_train_unit`）：走 plan 树揪出 unit_type
   落在占位名（与 `Director._UNIT_NAME_MAP` 同源）上的 ActUnit/TerranUnit，单测阶段拦死运行时崩。
4. **顶层兜底：单帧任何异常不许杀整局**（用户强要求）。`common_bot.on_step` 现在整体包 try/except +
   `super().on_step()`（sharpy plan）单独再包一层，全 catch + `logger.exception` 落完整 traceback 到
   game log，游戏继续跑、事后靠日志定位。**单帧出错只丢这一帧、下一帧重试**，再不会"打到一半退出"。

**ref**：`bc_late.py:69`、`common_bot.py on_step/_on_step_body`、`test_terran_plans_construct.py`
（construct + placeholder 审计）、`test_terran_bot_smoke.py::test_on_step_swallows_exceptions`。

---

## 2026-06-19 · "砍兵省矿给科技"vs 强攻 AI 反而拖慢科技

**现象**：bc_rush 想让科技链更快，把早期枪兵 cap 从 90 砍到 4（"省矿喂科技"）。真局 vs veryhard
首舰反而从 ~5:30 拖到 ~7:50、还输。

**根因**：枪兵太少 → 早期军队弱 → 被 veryhard 骚扰/打掉农民 → 经济受损 → **喂科技的钱反而更少**
→ 科技/首舰更慢。**军队保护经济，经济喂科技**；砍军队 = 砍科技速度（只有 vs **被动**不骚扰的 AI 才
真省矿、科技才更快）。

**教训**：build 优化里"砍 X 省资源给 Y"的直觉，在**真空里成立、vs 强攻 AI 常反转**——因为被砍的 X
往往在保护产出 Y 的经济。验收别只跑被动对手(veryeasy)，必须 vs 强攻(veryhard)看真账。**ref**：
bc_rush.py 枪兵 cap 注释；commit (#556)。

---

## 2026-06-19 · SC2 move 命令"是否自动攻击"是单位相关的（差点搞错）

**事实**：通常 SC2 单位收到纯 `move` 命令**不**自动攻击（只有 attack-move/hold/idle 才自动开火）。
但 **Battlecruiser 是 move-shot 单位，纯 `bc.move()` 下也会自动攻击射程内目标**（真机
`weapon_cooldown` 探针证实：射程内有敌时 wcd 在 0↔1.6 循环 = 在开火）。

**教训（正向，复用纪律）**：当时我有强先验"move 不自动攻击"，用户坚持"BC 纯 move 会自动攻击"。
**没有谁对谁错地争，加了个 `weapon_cooldown` 探针真机验** → 证实用户对、我先验错。**验证终态这条
纪律是双向的**：不只防用户/我望文生义，也防自己拿"常识先验"否定别人。下次遇到"X 在游戏里到底会不会
Y"的分歧，别争，加探针看 terminal state（这里是 wcd>0）一局就清楚。**ref**：`bc_raid_act.py` Fix3；commit (#557)。

---

## 2026-06-19 · salvage 地堡真机拆不掉（一个症状下三个坑）

**症状**：建筑回收（salvage）directive 单测全绿、真 LLM 解析正确、日志里 `SALVAGETRACE salvaged=1`、
`cast_unit_ability cast fail=False` —— 一切看着都对，但真局里地堡**根本没被拆掉**（telemetry BUNKER
计数一直 = 1）。

**根因（三层，逐个剥）**：

1. **`bot.do(unit(ability))` 对 idle 单位被静默丢弃**。python-sc2 `prevent_double_actions`
   （`bot_ai_internal.py`）在 `unit.orders == []`（如刚建好闲置的地堡）时跳过整个 `if action.unit.orders`
   块、**fall through 到隐式 `return None`**；默认 `prevent_double=True` 的 `filter()` 把 None 当 falsy
   丢掉 → 命令永远发不到 SC2，**且不报错**。
   - 修法：`cast_unit_ability` 把 `UnitCommand` 收进 `_vibecraft_bypass_actions`，在 `super().on_step()`
     之后用 `_do_actions(prevent_double=False)` 直发，绕开该 filter。
   - 通用教训：**任何给可能 idle 的单位/建筑施法的新路径都要走 bypass，别用裸 `bot.do(unit(ab))`。**

2. **ability enum 望文生义用错**。地堡回收，"显然"该是 `SALVAGEBUNKER_SALVAGE` —— 错。真机
   `get_available_abilities(bunker)` 显示实际可用的是通用的 **`SALVAGEEFFECT_SALVAGE`**；发
   `SALVAGEBUNKER_SALVAGE` 真机返回 `ActionResult.NotSupported`。单测/LLM/设计/独立评审**全都**假定了
   BUNKER 版还都"通过"，只有真局查 available abilities + 看 ActionResult 才暴露。
   - 教训：**ability enum 名必须 `get_available_abilities` 真机核对，别靠名字猜。** 发命令后看
     `_do_actions` 返回的 `ActionResult`，非 Success = 被 SC2 拒。

3. **自验只断言中间 trace、不验终态**（最致命的方法论坑）。第一版自验看 `salvaged=1` 就报 PASS —— 假阳性。
   `salvaged=1` 只证明"director 代码走到了那一步并打了日志"，**不证明 SC2 真执行了**。
   - 教训：**验世界真实终态**（telemetry 建筑/单位计数真变化），不是 bot 自己打的"我做了 X"。
     单测绿 + 中间 trace 绿 ≠ 真机生效。

**附带坑**：`debug_create_unit` 生出来的地堡，SC2 **拒绝** salvage（engine 限制）→ 自验必须让 bot
**真实建造**地堡（structure_override）再验，不能图省事 debug 生。

**ref**：commit `0a6272f`；`scripts/salvage_selftest.py`；ARCHITECTURE.md 关键不变量。

### 流程复盘（流程层教训，独立 opus 复盘 2026-06-19）

- **质量门全是"内部自洽"检查，缺一个"外部终态"门**：单测 / 真 LLM 解析 / 独立评审都只验"我方代码、解析、设计互相自洽"，没有一个验"外部引擎真改了世界状态"。验收里必须至少有一个**不依赖任何内部假设**的黑盒终态断言，否则全绿只是内部回声。
- **同源的多个验证不是独立证据，是同一假设的回声**：单测 mock、LLM prompt、设计文档全从同一个错误 enum（`SALVAGEBUNKER`）长出来 → 一致通过只证明它们彼此一致，不增加置信度。靠"叠加同源验证"刷绿是假安全感。
- **"代码走到了"≠"真机生效了"——自己打的成功日志是 unreliable narrator**：`salvaged=1` / `fail=False` 只证明我方代码路径执行完、命令发出去了，不证明被外部引擎接受/执行。凡是把命令发给外部系统（SC2 / LLM / API / DB）的功能，自己的成功 trace 不算终态证据。
- **"风险低可以发布，用户下一把自己试就知道"= 把终态验证 outsource 给用户的危险信号**：一旦结论里出现"应该没问题 / 残留风险低 / 用户自己试试就能确认"而这功能我本可自验终态，那不是发布信号，是"我还没验终态"的自白——立刻自验，别等用户质疑（这次等了两次）。
- **评审不得给"望文生义的外部符号"背书**：设计/评审里任何外部引擎的 enum / 常量 / ability / API 名，必须标注来源（`get_available_abilities` 真机核对 / 官方文档），未核对的标 `UNVERIFIED`；评审清单加一条"外部符号是否已核对"，对 UNVERIFIED 符号评审只能提"需核对"，不能背书其正确性（这次评审反而加固了错误假设）。
- **发 ability/命令给外部引擎的功能，DoD 固定两条**：① 断言外部世界终态变化（telemetry 计数/状态真变）；② 核对引擎返回（`ActionResult==Success` / 目标 ability 在 `get_available_abilities` 里）。两条都过才算完，中间 trace 一律不顶 DoD。

> **最重要的一条**：质量门全是内部自洽检查、缺一个外部终态门——这是根因级盲区，其余几条都是它的具体表现（trace 充数、同源回声、把验证甩给用户）。补上"外部终态黑盒断言"这一个门，这次的假阳性当场就挂。

---

## 2026-06-19 · 新加的 build / plan create_plan 期异常 → bot 静默瘫痪（啥都不造）

**症状**：bc_rush 加了空军攻防升级后，真局里 bot **什么都不造**——supply 卡 15/15 一整局、矿堆到几千、
0 建筑。但单测（`test_terran_strategies` catalog 测）绿。

**根因**：`Tech(UpgradeId.TERRANVEHICLEANDSHIPWEAPONSLEVEL1)` —— 这个 upgrade **不在**
`UPGRADE_RESEARCHED_FROM`（SC2 武器分车/空两条、没有合并版，这个 ID 不存在于映射）→ `Tech.__init__`
直接 `KeyError` → `create_plan()` 抛异常 → 整个 BuildOrder 没建起来 → bot 跑空。

**教训**：
- 改任何 plan `.py` 后**必须跑 `tests/unit/test_terran_plans_construct.py`**（它真调 `create_plan()`，
  拦构造期 KeyError/TypeError），**别只跑 catalog 测**（那个测不到 plan 构造）。我当时跑错了测试。
- 战巡武器是 `TERRANSHIPWEAPONSLEVEL1`（Armory），护甲才是合并的 `TERRANVEHICLEANDSHIPARMORS`。

**ref**：commit `a28542d` 链路；`test_terran_plans_construct.py`。

---

## 历史坑（详情散见 CLAUDE.md / CHANGELOG，这里只列指针）

- **`Sc2Facade` 是 `typing.Protocol`，运行时不强制实现** → 新增 facade 方法漏在 `_SharpyFacadeBase`
  实现时，单测（用 FakeFacade）绿、真机静默失效。改 facade 必同步两实现 + 跑 Protocol audit。
  （CLAUDE.md「改 Sc2Facade 接口」条；踩坑实例 `release_unit_role`。）
- **debug draw 不渲染中文（CJK）**：游戏内标签必须 ASCII/数字。（CLAUDE.md「SC2 debug draw 硬限制」。）
- **GBK console 崩在 emoji/✓**：脚本 `print()` 出 ⚠️/✓ 等非 GBK 字符会 `UnicodeEncodeError`。
  面向 console 的脚本输出用 ASCII（`[OK]`/`WARN`）。
- **目标坐标每帧重选 → 单位追移动靶抽搐**：建筑落点/待命点/挂件落点等一次算好锁住，别每帧 `find_placement`
  重选。（CLAUDE.md「目标坐标一次规划、锁定」铁律。）
- **新 done_when/activate_when kind 要三处同步**；**新 directive type 要五处同步**（types 枚举 +
  models Payload + `_apply_to_facade` 分支 + LLM prompt + 重 dump）。（CLAUDE.md 对应条。）
