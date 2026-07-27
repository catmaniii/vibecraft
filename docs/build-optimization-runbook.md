# Build 效率优化 Runbook（可复用方法论）

> **目的**：SC2 版本更新（patch）后**重跑全部 build 效率优化**的可复用方法论。
> 三族 **26 opening + 18 doctrine** 已优化一轮（2026-06-15/16，详见
> `docs/plans/2026-06-15-build-optimization-method-log.md` 的逐条findings）。本文档是过程**蒸馏**，
> 让下次重跑直接照做、避开所有已踩过的坑。
>
> **何时重跑**：① SC2 patch（兵种数值 / **ability id** / 建造时间 / 供给 变更）→ build 行为变；
> ② 改了 sustain / scorer / 共享 macro 逻辑 → 受影响 build 重跑。

---

## 1. 目标 + 三维度指标（不变量）

纯宏观沙盒（forced-defend，固定 random_seed 做配对 A/B）里，每个 build 要**把钱花干净、产能拉满、不卡人口**：

| 维度 | 信号 | 字段 |
|---|---|---|
| **M1 余钱** | bank 越低越好（钱有没有花出去） | `avg_excess_bank` |
| **M2 产能** | 生产建筑利用率（虫族=larva 闲置 + 注卵覆盖） | `prod_util` / `avg_larva_idle` |
| **M2-早 早窗口产能** | **前 8 分钟（0-480s game-time）的产能闲置**——单独切窗口看，不混进整局 | `prod_util`（跑 `--seconds 480` 或切窗重算） |
| **M3 卡人口** | supply_used==supply_cap 的时长 | `supply_block_time` |

> **★ 早窗口产能（2026-06-16 用户踩坑后加）：整局 prod_util 是平均值，会被晚期满产能稀释、
> 掩盖前期空转**。实测 bio_stim 前 8 分钟兵营空转 6 分钟（早窗 util 0.30），但 t=482 后拉到 1.0，
> 整局平均 ~0.58「看着过线」→ 之前没抓到。**验收必须单独跑 `--seconds 480` 看 0-480s 的 prod_util +
> 各产能楼 busy/total 时间线**（`busy=0 total>0` = 兵营建好不出兵），别只看整局。典型早期坑：
> opening plan 把主兵种 cap 太低（如 bio MARINE cap 4 等 TechLab）、副兵 priority 卡死主产线、
> 出一波到 to_count 就停产而 sustain 还没接上（opening_completed 前的空窗）。

**铁律**：
- **按 build 各自迭代，不跨 build 排名**（不同 build 的分不可比）。
- **不增删兵种**（variant 只调数量），**多维验收**（改善瓶颈不回退其它维度）。
- **180 门**：`supply_used ≥ 180` 进 banking 阶段 → **不罚** bank/gas/larva（满人口无处花钱是正常终态）。
- **指标变差 → 调 plan/core_units 把它做回来，绝不放宽 scorer/spec 数值**。
- **gas 不是评分项**（满人口浮气正常；只有当某兵种能吃气时才值得为它多孵——见 ling_bane）。

## 2. 工具 + 跑法

```bash
# 开局 build（OpeningBuild）
.venv/Scripts/python.exe scripts/build_efficiency.py run <opening_id> --seeds 1 --opponent veryeasy --seconds 900

# doctrine（PersistentDoctrine）—— 必须 --auto-switch-to（见 §6 陷阱）
.venv/Scripts/python.exe scripts/build_efficiency.py run <opening_id> --auto-switch-to <doctrine_id> --seeds 1 --opponent veryeasy --seconds 900

# 只对已有 telemetry 重打分（不重跑）
.venv/Scripts/python.exe scripts/build_efficiency.py score logs/eff_<...>/telemetry.jsonl
```

- **non-realtime 沙盒**，机器实测可 **4-8 并行**（各自 GameProcess 子进程 + 独立 game_id）。nohup 后台批跑 + 守候 grep `avg_excess_bank|Traceback|error:`。
- **t_end = 900**（`--seconds 900`）：抓中后期 sustain 囤钱（600 太短，sustain 摆烂在 600 后才爆）。
- **flake（n_snapshots=0）= infra 抖动，直接重跑**，不是 bug。
- **doctrine id ≠ 文件名**：文件 `skytoss.yaml` 的 `id:` 是 `persistent_skytoss`。`--auto-switch-to` 用 **id**；`run <opening>` 的 strategy_id 用**文件名**（`_detect_race` 按文件名找）。

## 3. 两层流程（每个 build）

- **Tier 1 结构性修复**：build 摆烂（opening 后停产 / 余钱爆 / 卡某数）→ 先修结构（接 build-aware sustain、修 act 错配、加 core_units）。
- **Tier 2 参数调优**：结构 OK 但指标偏高 → 调 core_units 数量 / 采气 / 上限。

## 4. ★★★ 最大教训：sustain 的 act 必须匹配兵种的「生产机制」

**这是本轮反复踩的同一个根因，三族各栽一次**。build-aware sustain 给某兵种配错 act → `cache.own(from_building)` 恒空 或 `builder.train()` 对非 train 机制无效 → **该兵种静默冻结在开局造的那几个，永不增长**（余钱随之爆）。

| 生产机制 | 兵种举例 | **正确的 act** | 错配后果 |
|---|---|---|---|
| larva train | 狗/蟑/刺/飞龙/农民/OL/女王 | `ActUnit(unit, LARVA)`（虫族 from_building 必须是 LARVA，**不是科技楼**） | 用科技楼当 from → `roachwarren.train(ROACH)` 无效 → 冻结 |
| 建筑 train | 神族 robo/星门兵、人族全部 | `ActUnit(unit, building)` | 一般 OK（建筑不 morph） |
| **warp-in** | 神族 gateway 兵（折跃门研究后） | **`ProtossUnit(unit, n)`**（折跃完成自动切 WarpUnit） | `ActUnit(STALKER, GATEWAY)` 折跃后 GATEWAY→WARPGATE、builders 空 → 冻结 |
| **morph** | 爆虫←狗 / 飞蛇←蟑 / 潜伏←刺 / BL←腐化 / 雷兽 / Overseer | **`ZergUnit(unit, n)`**（按兵种 dispatch 到 MorphX + 从 larva 补源兵） | `ActUnit(BANELING, ZERGLING)` 的 `zergling.train(BANELING)` 对 morph 无效 → 冻结 |
| 建筑 morph | Lair/Hive、Orbital/PF、GreaterSpire | 专用 morph act | — |

**三次实例（patch 后优先复查这些脆弱点）**：
1. **虫族 from_building**：`ActUnit` from 设成科技楼（ROACHWARREN…）而非 LARVA → 蟑螂卡 28 摆烂。
2. **神族 warpgate**：`ActUnit(STALKER, GATEWAY)` 折跃后失效 → 追猎卡 3。修：build-aware 神族用 `ProtossUnit`。
3. **虫族 morph**：`ActUnit(BANELING, ZERGLING)` train 无效 → 爆虫卡 12。修：build-aware 虫族用 `ZergUnit`。
   - **叠加坑**：vendored `MorphBaneling` 用了 SC2 已失效的旧 ability `MORPHZERGLINGTOBANELING_BANELING`
     → 引擎静默丢弃。已修成 `MORPHTOBANELING_BANELING`（`docs/sharpy-patches.md §6`）。
     **★ patch 后特别查 ability id**：SC2 改 ability 枚举 → morph 类静默失效，单测
     `test_morph_baneling_ability_fixed` 会抓但其它 morph（飞蛇/潜伏/BL）没单测保护，手动复查。

**当前三族 sustain act 分派**（`opening_sustain_act.py::_build_from_core_units`）：
神族 `ProtossUnit` / 虫族 `ZergUnit` / 人族 `ActUnit`。**给任何族新接兵种前，先查它的生产机制对不对得上。**

## 4.5 ★★★ 早窗产能第二根因：廉价持续出兵被排在重建筑/扩张**后面**饿死

**2026-06-16 人族早窗（0-480s）优化时新挖出的根因，与 §4 的 act 错配是两回事**。
症状：兵营/产能楼**早早建好却长时间空转**（早窗 `prod_util` 低到 0.30），但有矿有兵营、
中后期又拉满 → 整局平均掩盖。

**机制（sharpy `BuildOrder.execute()`）**：`BuildOrder`（非阻塞）每帧**按 child 列表顺序**
依次 `execute()` 每个 child。建筑/扩张类 act（`GridBuilding` / `Expand` / `BuildAddon`）执行时
调 `knowledge.reserve()` **预扣**自己那份矿。如果廉价的持续出兵（`TerranUnit(MARINE, …)`，枪兵 50 矿）
**排在这些重建筑/扩张 child 的后面**，每帧轮到它时矿已被前面的 reserve 扣光 → `can_afford(MARINE)`
恒 False → **兵营空转半局**。3 矿扩张（400）+ 多兵营（150×N）+ add-on 把早期矿全占住，枪兵根本排不上。

**修法（零风险，已验 5 个 build）**：把**廉价持续出兵的 `TerranUnit` 上移到 `AutoDepot()` 之后、
所有建筑/扩张 Step 之前**，加 `priority=True`。这样每帧先 reserve 满兵营产线，剩下的矿再给建筑。
**气兵不受影响**（坦克/女妖/掠夺者走 gas + priority reserve，与枪兵抢的是不同资源池）；
建筑只是稍晚拿到矿、不会饿死（critical path 的 depot→BB→gas 仍在 `SequentialList` 里保顺序）。

**人族 9 opening 早窗 prod_util 修复前→后**（`--seconds 480`，2 seed）：
bio_stim 0.30→0.79 / two_base_tanks 0.51→0.80 / widow_mine_drop 0.56→0.82 /
marine_rush 0.54→0.70 / one_one_one 0.55→0.75 / banshee_harass 0.67→0.71 / two_one_one 0.65→0.94。
（hellion_expand 0.69 / reaper_expand 0.74 本就 OK，未动。9 个 opening 早窗 prod_util 现全 ≥0.69。）

**判据**：早窗 `prod_util` 低 + 时间线里某产能楼 `busy=0 total>0`（建好不出兵）+ 余钱不爆
（钱在但没花到该楼）→ 八成是出兵 child 排在重建筑后面。先查列序，再考虑抬 cap。

**只对 ground-production 直接成立**（人族兵营 train / 神族 robo·stargate train / 虫族 larva —— 都不
gate on combat intent）。**神族折跃流 all-in（4bg / dt_rush 走 `ForwardWarpStalker`）不适用**：它们
早窗 util 低是 forced-defend 沙盒把折跃产兵引擎关掉的假象（见 §6 新增行），不是列序 bug，**别改**。
2026-06-16 三族早窗审计结论：**只有人族 9 opening 有真·列序欠产能（已修）**；神族 4bg/dt_rush =
沙盒口径假象（真局 util 健康），其余神族（phoenix/void/dt_drop/cannon/blink）+ 虫族 = 经济/科技
固有或 supply-block 派生（虫族 larva 堆多半是早期 OL 跟不上的 supply block 症状），均非干净浪费，未改。

## 5. 诊断 playbook（怎么定位「冻结」类 bug）

1. **读 telemetry 单位时间线**：`snaps = [r for r in recs if r['kind']=='snapshot']`，`for s in snaps[::40]: print(t, supply, 各兵种数, minerals, gas)`。
2. **信号**：某兵种**冻结在 N 不动** + 余钱单调涨 + supply 卡某数（≠200）。
3. **确认是哪个 act**：读该兵种的生产 act 源码 —— `cache.own(from_building)` 是不是空？`builder.train()` 对它是 train 还是 morph/warp？
4. **找正确的 act**：sharpy 里通常已有（`ProtossUnit` / `ZergUnit` / `MorphX`）—— **优先用框架自带的，别裸 ActUnit**。
5. **真局 A/B**：同 seed 配对，改前 vs 改后。这是 behavior change，**重跑而非重打分**（无既有 telemetry）。

## 6. ★★★ Harness 保真陷阱（别被假数据骗）

| 陷阱 | 现象 | 正解 |
|---|---|---|
| **doctrine 静默回退** | `run skytoss` → `forced_opening` 只匹配 `OpeningBuild`、doctrine id 配不到 → **回退默认开局**（神族=4bg）。8 个 doctrine 曾全跑成 4bg、余钱雷同 → **审计结论作废** | doctrine **必须** `--auto-switch-to <doctrine_id>`（配对应科技的开局切入） |
| **"健康"假阳性** | 开局本身堆到接近满人口 → 掩盖 sustain 失效（macro_hatch/mutalisk 曾假阳性） | 看 **sustain 接管后**核心兵种**有没有继续涨**，不只看末态 supply |
| **满人口假 block** | `supply 200/195`（cap 凑不齐 200）被 scorer 计成卡人口；army 真打到 200 反而 block 变高 | 这是**成功的副产物**不是退步；只追 supply<180 段的真 block（如早期首水晶延迟） |
| **虫族 doctrine util=null** | scorer 的 larva-util 在 auto-switch 路径没算 | 读 **bank + supply + 末兵种**判定，别等 util |
| **doctrine 真局 ≠ sustain** | 真局玩家切 doctrine 填 board slot → `persistent_set` gate 掉 sustain；产兵归 `sharpy_dummy_class` plan。`--auto-switch-to` 测的是 **doctrine plan + 开局 sustain 并行**（真局主流场景，但若要测纯 doctrine plan 摆烂与否，得填 slot 路径） | doctrine 摆烂要修 **对应 sharpy plan**（如 mech），不是 sustain |
| **单 seed 方差** | 健康/持平判定单局不可靠 | 摆烂修复（delta 大）1-2 seed；持平判定、空军（gas 敏感）≥2-3 seed |
| **★ intent-gated 产能被 forced-defend 关掉** | 神族**折跃流 all-in**（4bg / dt_rush 等走 `ForwardWarpStalker`）早窗 `prod_util` 极低（0.39-0.41）+ 余钱/浮气暴涨（4bg 囤矿 1560 / dt_rush 浮气 2110）。根因：`ForwardWarpStalker.execute()` 在 `combat_intent_override in (retreat/defend/hold)` 时 **noop**，而沙盒**每 tick 强制 defend**（director.py `_sandbox_macro_only`）→ 折跃产兵引擎整局关闭 → 折跃门空转囤资源。**这是沙盒假象，不是 build 缺陷**：真局（attack intent）折跃门持续 warp。2026-06-16 实测 4bg `--no-sandbox` util 0.39→**0.64**、余钱 965→415。 | 看到"warp-in 流 build 早窗 util 低 + 囤资源" → **先跑 `--no-sandbox` 对照**确认是不是 intent-gate 关掉了产能引擎。是 → **不要改 build**（改了是为坏指标优化、且可能害真局）；记为沙盒口径限制。只有 ground-production（人族兵营 / 神族 robo·stargate train / 虫族 larva，都不 gate on intent）的早窗 util 才能直接信沙盒值。 |

## 7. Build 类型 → 机制覆盖清单

- **26 opening_build**（全有 `core_units` → 走 build-aware sustain）：人族 9 / 虫族 8 / 神族 9。
- **18 persistent_doctrine**（无 core_units；真局靠各自 `sharpy_dummy_class` plan，sustain 并行兜底）：人族 5 / 虫族 5 / 神族 8。
  - **17 真测健康**；唯一摆烂 = **人族 mech**（plan 把每种兵写死低上限 + 通用 sustain 回退 bio 错兵）→ 修 plan（抬上限 + 扩工厂 + 兵种生产 Step 列序抢资源）。

## 8. 重跑 checklist（patch 后照做）

1. **先跑单测**：`uv run pytest tests/unit/test_sustain_core_units.py` —— ability/producer 回归会抓到 SC2 数据变更（尤其 morph ability id）。
2. **跑 1 个代表 opening 验 harness 通**：`run 1g_robo_immortal`（神）/ `run macro_hatch`（虫）—— 确认出数、opening_completed 落地、sustain 接管。
3. **全量 26 opening 并行**（8/批），对比 §10 上轮基线，挑退步的。
4. **全量 18 doctrine**：`run <opening> --auto-switch-to <doctrine_id>`，配对应科技的开局（航母系←void_ray_rush/phoenix_2base；robo 系←1g_robo_immortal；IAC←iac_2base；mech←two_base_tanks；…）。
5. **退步的逐个定位**（§5 playbook）：先看是不是 §4 的 act 错配（patch 改了兵种 = 高发）。
6. **留痕**：method log 记前后对照 + CHANGELOG + commit（commit message 与 changelog 同源）。

## 9. 调参法则速查（Tier-2）

- **采气优先级**（虫族 `_zerg_gas_per_base`）：按**主力（mass）兵种**判 —— 吃矿主力（蟑/纯狗）→ 1（减气防浮）；含气耗大兵（飞蛇/爆虫/飞龙）→ 2（满气）。
- **空军 producer 降档**：mass 是空军（STARGATE）→ GridBuilding 目标 4（不是 8；航母/虚空贵+气瓶颈，8 座空转/气浮）。
- **morph 兵当气钱口**：浮气大的 build，morph 兵（爆虫）拉高 cap 把气榨成军队（ling_bane 爆虫 cap 250 → 气浮 5733→1900）。用户授权范围内可到"全 morph"。
- **同 priority 多兵种抢资源按 Step 列序**：要"多出某兵"→ 它的生产 Step 排前面 + priority（mech 雷神列首 → 0→7）。
- **早期首水晶/补给延迟**：rush 类开局在 15/15 卡几秒 → 首 Pylon/Depot 提前 1 个农民（dt_rush 14→13 农，早期 block 8.9→4.5s）。

## 10. 上轮基线（patch 后作对比；满分=低 bank + 高 util + 低 block）

> 单位：`avg_excess_bank`（越低越好）。详细 util/block + 兵种组合见 method log。这是 patch 后**回归对比的锚**。

**神族 opening**（修后）：1g_robo 273 / 4bg 857 / blink_stalker 158 / cannon_rush 300 / dt_drop_iac 295 / dt_rush 709 / iac_2base 131 / phoenix_2base 379 / void_ray_rush 470。
**神族 doctrine**：skytoss 453 / colossus_immortal 261 / blink_harass 251 / colossus_no_ht 483 / immortal_archon 134 / immortal_archon_no_ht 149 / phoenix_control 380 / skytoss_no_ht 424。
**虫族 opening**：macro_hatch ~170 / roach_hydra 330 / roach_ravager 220-248 / nydus 411 / mutalisk 264 / roach_allin 306 / 12pool 617（纯狗，近地板，未改） / ling_bane 342-388（爆虫 cap 250）。
**虫族 doctrine**：ultralisk 837 / lurker_hydra 390 / brood_corruptor 1311 / muta_ling_bane 446 / roach_hydra_viper 327。
**人族**：mech 523/533（修后）/ bio_max 523 / skyterran 469 / ghost_nuke 530 / liberator 297。人族 9 opening 上轮（更早 session）已优化，本轮未重测 —— patch 后重测。

---

**一句话方法论**：*先用 telemetry 单位时间线抓「冻结」信号定位结构 bug（多半是 act 没匹配兵种的生产机制 train/morph/warp/larva），改用框架自带的对应 act 修结构；再用同-seed A/B + 180 门指标调参；doctrine 必走 `--auto-switch-to`、当心满人口假 block 和开局掩盖的假阳性。*
