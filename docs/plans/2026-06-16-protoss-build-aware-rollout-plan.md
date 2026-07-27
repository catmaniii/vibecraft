# 神族 16 build build-aware sustain rollout 实施方案

> 前置已完成并验证：`opening_sustain_act.py` 神族路径改用 `ProtossUnit`（warp-in 健壮）+
> `plan_from_core_units` gateway 兵 producer 规范成 GATEWAY。4bg proof：余钱 5789→857、
> 产能 0.086→0.523、追猎卡 3→60、人口卡 28→200。本方案把同一修复 rollout 到其余 16 个神族 build。

**目标**：17 个神族 build 全部走 build-aware sustain（GridBuilding 补产能楼 + 按配比续兵），
消除"opening 后停产摆烂 / 余钱高"。审计基线：仅 3 个 robo 系健康，14 个坏。

**约束（不变）**：不新增/删兵种（只调数量）；按 build 各自迭代不跨 build 排名；多维验收
（改善瓶颈不回退其它维度）；180 人口门（supply≥180 不罚 bank/gas/larva）。

---

## 一、设计决策：doctrine 的 core_units 来源（需评审重点）

8 个 `persistent_doctrine` 的 schema 用 `target_composition: dict[str,int]`（用户已手调的目标兵力），
**没有 `core_units` 字段**。两个方案：

- **方案 A（选这个）：运行时从 `target_composition` 派生 core_units**，不改 schema、不手写。
  在 `OpeningSustainAct._active_core_units()` 加：若 build 无显式 `core_units` 但有
  `target_composition` → 合成 `[CoreUnit(mass=最高数量兵), CoreUnit(cap@count for 其余)]`。
  - **mass 启发式**：`target_composition` 里**数量最高**的兵 = mass（喂满产能的钱口）。
    实测 8 个 doctrine 全部命中正确主力：blink_harass→STALKER(20)、colossus_immortal→STALKER(12)、
    colossus_no_ht→STALKER(12)、immortal_archon→ZEALOT(14)、immortal_archon_no_ht→ZEALOT(14)、
    phoenix_control→PHOENIX(12)、skytoss→CARRIER(12)、skytoss_no_ht→CARRIER(12)。
  - 其余兵 → `cap` @ 各自 target count。
  - ARCHON：`UNIT_TRAINED_FROM[ARCHON]=∅` → `plan_from_core_units` 已自动 skip（archon 靠
    bot 的 HT/DT merge 逻辑，sustain 不直接产）。MOTHERSHIP（NEXUS 产，cap 1）/ OBSERVER /
    WARPPRISM / SENTRY：作 cap 无害（数量小、不抢主产线）。
  - **优点**：DRY、零手写、用已 curated 数据、heuristic 已验证全对。**缺点**：mass 由数量推断
    （但已验证 8/8 正确）。
- 方案 B（不选）：给 `PersistentDoctrine` 加 `core_units` 字段 + 手写 8 份。重复 target_composition、
  多 8 个出错点。

**tie-break**（评审请确认）：若最高数量并列，取"更便宜/更核心的 gateway 兵"。当前 8 个无并列，
实现里用稳定顺序（如按 UnitTypeId 名）兜底即可。

## 二、9 个 opening_build 的 core_units（显式写 yaml）

无 `target_composition`，按 build 身份显式声明。mass = 主力钱口，cap = 科技/配菜。

| build | core_units | 依据 |
|---|---|---|
| 1g_robo_immortal | STALKER mass, IMMORTAL cap 6 | 1 BG robo 双追猎不朽 |
| 4bg | STALKER mass（已完成） | 4 门追猎压制 |
| blink_stalker | STALKER mass, IMMORTAL cap 4 | 6 BG 闪追 + robo |
| cannon_rush | STALKER mass | rush 后转 gateway 运营 |
| dt_drop_iac | ZEALOT mass, IMMORTAL cap 6, DARKTEMPLAR cap 4 | DT drop 转 IAC（不朽-电球-冲锋狂热） |
| dt_rush | ZEALOT mass, DARKTEMPLAR cap 8 | DT rush 后转冲锋狂热 |
| iac_2base | ZEALOT mass, IMMORTAL cap 6 | 不朽-archon-冲锋狂热（HT→archon 战术 merge） |
| phoenix_2base | PHOENIX mass, STALKER cap 8 | 双 VS 凤凰开局 |
| void_ray_rush | VOIDRAY mass, STALKER cap 6 | 1 BG 2 VS 虚空 rush |

> 这些是按 build **既有身份**声明续兵配比（不是新 build / 不改打法），与各 build 的
> aliases/summary/doctrine 一致。评审请核对兵种是否贴合 build 真实意图。

## 三、实施步骤

1. **doctrine 派生**（代码）：`_active_core_units` 加 target_composition→core_units 合成 +
   单测（8 doctrine 各验 mass 命中 + cap 数量）。
2. **9 opening yaml**：按上表加 `core_units`（4bg 已完成，余 8 个）。
3. **A/B 验证**：每个 build 单 seed VeryEasy 900s 跑 build_efficiency，对比审计基线，断言
   余钱↓ / 产能↑ / 人口能上 180（摆烂的）。并行 4-8 局。flake(0 snapshot) 重跑。
4. **回归**：`pytest tests/unit/test_sustain_core_units.py` + facade audit；`ruff`。
5. **留痕**：方法日志记 rollout 前后对照表；CHANGELOG + commit 同源。

## 三·五、独立评审处理（2026-06-16 Opus 评审，逐条 disposition）

**★ 致命发现（采纳，必须改）：doctrine 审计基线无效 + sustain 在真局 doctrine 路径被 gate。**
- 核实坐实：`common_bot.py:1875-1899` 的 `forced_opening` 只匹配 `OpeningBuild`，doctrine id
  匹配不到 → 回退 `DEFAULT_OPENING_ID="4bg"`。8 个 doctrine 的"审计"**全跑成了 4bg**（故余钱
  5789/5792/5789… 与 4bg 5788.8 雷同）。→ **doctrine 审计结论作废，它们从没被当 doctrine 测过。**
- 且真局玩家切 doctrine = `strategy_set` 填 board slot → Director `persistent_set=True` →
  `OpeningSustainAct` 不触发（sustain 只在 `not persistent_set` 时启动）。真局 doctrine 产兵归
  各自 `sharpy_dummy_class` plan（BlinkHarass/Skytoss…），**不是 sustain**。给 doctrine 派生
  core_units 只会改审计的 auto_switch 路径 → 真局红/审计绿的假阳性。
- **处理：doctrine（8 个）整体移出本 rollout**。方案 A 派生不做。doctrine 是否摆烂要用**填 slot
  的真局路径**单独测；若摆烂，修对应 `sharpy_dummy_class` plan，不是 sustain。另立项 + 单独评审。

**采纳（空军 producer 降档）**：mass 是空军（producer=STARGATE）时 `GridBuilding(STARGATE, 8)`
过量（航母极贵 + 气瓶颈 → 8 星门空转、气浮）。给空军 producer 单独一档 `_AIR_MASS_PRODUCER_TARGET=4`。
影响 phoenix_2base / void_ray_rush；A/B 必须盯 gas float。

**采纳（防御性，townhall/utility producer skip）**：`plan_from_core_units` 跳过 townhall 类
producer（NEXUS/COMMANDCENTER/HATCHERY/LAIR/HIVE）做 GridBuilding（MOTHERSHIP→NEXUS 会误盖基地）。
当前 9 opening 无 MOTHERSHIP/OBSERVER 当 core_unit，不咬本批，但加廉价防御防未来踩。

**采纳（多 seed）**：空军（phoenix_2base/void_ray_rush）≥3 seed + 盯 gas；健康类
（1g_robo_immortal/iac_2base/dt_drop_iac，目标"不退步"）≥2 seed 确认持平非噪声；摆烂修复类
（4bg 已验/dt_rush）delta 巨大，1-2 seed 即可。

**采纳（9 opening core_units 配比）**：评审逐个核对 summary/aliases，主力选择全部贴合身份，
直接落地。提醒：all-in 类（cannon_rush/dt_rush）的 efficiency 分非设计目标，别为分数扭曲 build 性格。

**本批最终范围：9 个 opening_build（4bg 已完成，余 8 个）。doctrine 8 个移出，另议。**

## 四、验收判据（多维，按 build 各自基线）

- **摆烂类**（4bg/dt_rush/8 doctrine）：opening 能完成或人口能上 180、产能从 ~0.086 显著上升、
  余钱大幅下降。
- **余钱高类**（blink_stalker/void_ray_rush/phoenix_2base/cannon_rush）：余钱下降、产能上升，
  不回退卡人口。
- **健康类**（1g_robo_immortal/iac_2base/dt_drop_iac）：加 core_units 后**不退步**（至少持平）。
- 任一 build 改后某维度回退 → 调 core_units 配比（不放宽 spec/scorer）。
