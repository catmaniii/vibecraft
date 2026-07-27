# 全 build 农民饱和治本：通用饱和兜底 + 4 结构病 + 防复发门 — 设计文档

> 2026-07-10。用户定基础规则"开多少矿最终就得多少农民"，审计揪出人族6+虫族8个病。治本方案。评审前真理源。

## 基础规则（用户 2026-07-09/07-10）
开 N 矿，农民**最终目标 = 饱和数**（游戏自带 `ideal_harvesters`，随基地/气井自调）。中间顶多**短暂停农民**
（all-in timing），但**最终永远冲着填满饱和去，绝不永久写死低位**。纯一波 all-in（1-2 矿不再扩）农民低
**不算病**——判据是**基地数 vs 农民数匹配**。

## 审计结论（3 类病）
1. **结构冻结（4 个，最狠）**：`roach_hydra_viper`/`ultralisk`（虫）+ `bc_late`/`mech`（人）—— 军队/科技/农民
   全塞进单条阻塞 `SequentialList`，前项没达标后面全冻结（roach_hydra_viper 慢 3-4 分且是 5 开局默认后期落点；
   ultralisk 前中期零地面兵；bc_late/mech 科技链冻）。
2. **农民不饱和（埋葬/低目标，10+ 个）**：农民排军队后被 larva 抢空，或数字定太低。
3. **doctrine 无兜底**：`OpeningSustainAct` 靠 `knowledge.vibecraft.sustain_uncap_active` flag 触发，
   **切 persistent_doctrine 后 flag 失效 → doctrine 靠自身 plan、没安全网**（病 build 里 doctrine 占多数非偶然）。

## 评审处置（2026-07-10 opus，全部采纳 — 实现按这里为准）

1. **[阻塞] 虫族 Floor 目标必须封顶（否则把已修 bug 引回）**：`opening_sustain_act.py` 故意把虫族 drone 封 **66**
   （不是满饱和 80）——虫族农民与军队抢同一 200 人口池，80 drone 占满人口没空间出兵（roach_hydra 实测：
   75drone+28蟑+12刺=200、larva 堆 95、矿气全囤）。→ **虫族 Floor 目标 = `min(ideal_sum, 66)`**（≈4矿饱和封顶，
   2-3矿时 ideal_sum<66 正常填、4矿+封顶留人口给兵）。**神/人 Floor 目标 = `min(ideal_sum, 80)`**（80≈满饱和无冲突）。
   drone_budget 常量与现有 sustain cap **同源**（别各写一份）。设计原"职责正交"对虫族 drone **不成立**，以此为准。
2. **[阻塞] 挂载点纠正**：`common_bot` **无 create_plan**（`:1927` NotImplementedError）。Floor 落进三族各自
   **`_wrap()`**（含 `if not plans` 分支），**必须是顶层 BuildOrder 直接兄弟、绝不进任何 SequentialList**
   （否则 `return False` 阻塞后续）。抽 `make_worker_floor(race)` 共享。
3. **[强烈] Floor 子类化 sharpy `ActUnit` 覆写动态 `to_count`**，别新写 DRONE/SCV/PROBE dispatch——复用 ActUnit 已测的
   worker 计数（`max(count, supply_workers)`）、虫族 LARVA/pending-egg、cooldown、非-priority 不 reserve（=软地板，
   军队 sibling 同帧仍能 train，正是要的）。Floor ≈ `ActUnit(worker, townhall, to_count=动态 target)` + grace 门。
4. **[阻塞-验收] 外部终态门 per-base + 覆盖 doctrine 路径**：telemetry 断言**每个基地** assigned→ideal 收敛
   （**禁全局 min/best 聚合**，BC 骚扰假阳性教训）；**必须测"切 doctrine 后 sustain flag 不 fire"这条真实路径**
   （`director.py:7277` `persistent_set=True` → flag 永不 set），别只测 default opening。
5. **确认点结论**：`.ready` filter 挡在建 townhall（对，ideal 立即可信 `unit.py:1256`）；Floor 与 plan 自身 drone step
   不双重超产（都查全局 count、有效目标=max、填到 ideal 自限）；larva 竞争 2矿all-in doctrine 需真局量化（非阻塞，
   非-priority worker train 已缓解）；运营开局过量农民风险有限（软地板+运营型本就要饱和2矿）、抽查 hellion_expand SCV 曲线即可。
6. **4 结构病抽 SequentialList 的硬前置保留清单**（评审给全，实现照做）：
   - `roach_hydra_viper`: Pool→BR / Pool→MorphLair / Lair→VH / Lair→VI / VI→MorphHive。
   - `ultralisk`: Pool→MorphLair / Pool→BB / Lair→VI / VI→MorphHive / Hive→VU。
   - `bc_late`: Starport→StarportTechLab→FusionCore→BC / 2×Armory→攻防。
   - `mech`: Barracks(Starport前置) / Factory→FactoryTechLab→坦克·地雷 / Armory→攻防 / Factory→Thor(需Armory)。
   - 铁律：Morph 类（MorphLair/Hive/Orbitals）原地升级必须 `Step(UnitReady(前置))` 门控，别与 Expand 混；drone/SCV+Expand
     一律抽并行，骨架照 `roach_hydra`/`widow_mine_drop`（短 SequentialList 开局 + 大并行 BuildOrder(Step 门控)）。

## 治本三件套

### A. 通用农民饱和兜底 `WorkerSaturationFloorAct`（核心，新增）
一个**种族无关的顶层兜底 act**，挂进三族每个 bot（`common_bot` 基类 create_plan 或各 bot），**所有 build（开局+doctrine）
恒生效**，把农民**始终拉向饱和**：
- **目标 = `sum(th.ideal_harvesters for 己方 townhall.ready) + sum(g.ideal_harvesters for 己方 gas.ready)`**
  （sc2 `unit.ideal_harvesters` 真值，已核对 `unit.py:1256`；随基地/气井自调，开矿即涨——**直接实现规则**）。
- **触发（尊重"短暂停农民"）**：**过早期宽限期**才填——`base_count >= 2` **或** `ai.time > _GRACE_S`（~90-120s）
  **或** `sustain_uncap_active`。纯 1 矿早期 all-in（base=1、grace 内）不碰 → 不扰早期节奏。
- **行为**：`worker_count < target` 且 能买单 且（虫族 larva 可用）→ 造 1 农民（race dispatch：DRONE/SCV/PROBE）。
  只填到 `ideal_harvesters`（游戏饱和），不过量。
- **优先级取舍（关键）**：兜底是**农民地板**不是"农民优先于一切"。放在**顶层 BuildOrder 靠前**（军队 plan 之前）
  → 每帧先保证农民往饱和补 1 个、再让 plan 爆兵。因目标有限（饱和即停），不会无限抢军队 larva；grace 后
  填农民本就正确（macro 期）。这一 act 同时治好"埋葬"（不管 plan 内部怎么排，兜底独立补农民）+ "低目标"
  （兜底目标是饱和不是 plan 写死的低数）+ "doctrine 无兜底"（恒生效不看 flag）。
- **与现有 `OpeningSustainAct` 关系**：Sustain 管"续兵 + 加产能楼"（保留不动）；新 Floor **只管农民饱和**，
  职责正交、可并存。Floor 不依赖 `sustain_uncap_active`。
- **facade**：只用 `ai.train`/现有造农民路径（无新 facade 方法，不触发两实现 audit）。造农民对 Zerg 走 larva。

### B. 4 个结构病修复（抽阻塞 SequentialList → 顶层并行兄弟）
`roach_hydra_viper`/`ultralisk`/`bc_late`/`mech`：把军队/扩张/农民从单条 `SequentialList` 抽出来改**顶层并行
`BuildOrder` 兄弟项**（参照已验证骨架：虫 `roach_hydra.py`/`lurker_hydra.py`、人 `widow_mine_drop.py`）。
保留必要的硬前置（如 tech 楼在兵之前用 `Step(UnitReady(...), ...)` 门控，不是整条 SequentialList 冻结）。
**这是结构 bug（生产线冻结），Floor 兜底治不了，必须单独修。**

### C. 防复发 AST 静态门（新增 `tests/unit/test_worker_saturation_audit.py`）
静态扫所有 build plan（不跑 SC2），对每个 `create_plan` 检测反模式并断言不复发：
- **埋葬**：`ActUnit/ZergUnit(DRONE/SCV/PROBE, ...)` 排在大数军队 `ActUnit/ZergUnit(<army>, N大)` 之后（同层）。
- **单条 SequentialList 塞军队+农民**（结构冻结模式）。
- 有 `Expand(n≥2)` 的 build，最终农民目标 `< n×14`（低目标兜底另有 Floor，但门也提示）。
（门是启发式，允许 `# noqa: worker-audit <理由>` 标注豁免纯 all-in 特例。）

## 验证（telemetry 验终态，per-build）
- **Floor 生效**：跑几个病 build（roach_hydra_viper/ultralisk/macro_hatch/bc_late…）真局，telemetry
  `DRONE/SCV/PROBE` 数 **随基地数爬到 ~饱和**（对比 `ideal_harvesters` 应 ≥90%），不再卡低位。
- **健康 build 不回归**：跑 proxy_4rax/12pool(纯 all-in) 确认 Floor **没**乱铺农民（base=1 目标低、grace 内不碰）。
- **结构病修复**：roach_hydra_viper 蟑螂/刺蛇出兵 timing 恢复（对比 roach_hydra）；ultralisk 前中期有地面兵。
- build_acceptance 保底不崩；单测（Floor 单元 + AST 门）+ 构造回归 + ruff/mypy。

## 待评审确认点
1. Floor 放"军队之前"会不会让**运营型开局**（hellion_expand/reaper_expand/zvp_macro）早期过量铺农民、
   拖慢该有的压制？grace 阈值 + base_count 门够不够挡住？还是要更细（按 build 类型/阶段）？
2. `ideal_harvesters` 对**在建/刚爆的 townhall** 返回值靠谱吗（未 ready 的 base 该不该计入目标）？真机核对。
3. Floor 与各 build **plan 内部自己的农民目标**并存会不会打架（都造农民 → 超饱和？）——Floor 只填到 ideal 应自限，核对。
4. 虫族 larva 竞争：Floor 每帧抢 1 larva 造农民，会不会拖垮 all-in doctrine 的爆兵 timing？grace 后应可接受，需真局确认。
5. 4 结构病抽 SequentialList 时，哪些硬前置必须保留（Lair→Hive、Factory→TechLab）别抽断。
