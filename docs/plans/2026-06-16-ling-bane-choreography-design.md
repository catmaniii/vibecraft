# 狗毒爆 (ling_bane) 进攻编排设计（2026-06-16 用户真局反馈）

> 用户真局观察到 3 个战术问题。本文档是改法设计真理源，实现前过独立 Opus 评审。
> 相关代码：`src/vibecraft/bot/auto_combat/zerg/plans/ling_bane.py`（开局 plan + `_ForwardBanelingMorphUnit`）、
> `src/vibecraft/bot/auto_combat/opening_sustain_act.py`（build-aware sustain）、
> `vendor/sharpy/sharpy/plans/tactics/zone_attack.py` + `combat/default_micro_methods.py`（战斗 micro）。

## 问题与诊断

1. **第一波毒爆在家变**：`_ForwardBanelingMorphUnit._morph_target` 取 ling 群几何中心 + 护蛹 gate
   (≥6 ling、中心 8 格内 ≥4 ling)。但变形 gate(妖虫巢好 + ≥8 狗)在狗刚出、还在家时就满足 →
   群中心=家。**历史坑**：更早版本在敌方二矿变 → 孤狗 cocoon 裸奔必死，2026-05-23 才改群中心护蛹。
   → 前压变形**必须保留护蛹**，不能退回老坑。
2. **后续不补毒爆**：开局有第二/三波 `_BanelingMorph(12)`，但 build-aware sustain（2026-06-16 效率优化）
   给虫族接了"在**家** MorphBaneling 250"。opening_completed 后两套并存打架，sustain 那套在家变、
   爆虫不进攻。**沙盒最优(家堆 250 爆虫) ⊥ 真局战术(前压/分波/护蛹)** —— 核心矛盾。
3. **狗被拉扯、和毒爆脱节**：sharpy 战斗 regroup（`default_micro_methods`）把快狗/慢爆分组，
   regroup 阈值把快狗往回拽 = "拉扯"；无"狗贴毒爆团"约束。毒爆提速(CENTRIFICALHOOKS)前尤甚。

## 设计

### 抽出共享 forward-morph（治问题 1+2 的公共底座）

把 `_ForwardBanelingMorphUnit` 的「护蛹 morph-target」逻辑从 ling_bane.py 抽到共享模块
（如 `zerg/baneling_morph.py`），开局 plan 与 build-aware sustain **都用它**，统一"前压 + 护蛹"语义。
sustain 不再用裸 `MorphBaneling`(home) 出爆虫。

### 1. 前压变形（forward gate）

`_morph_target` 现有护蛹两 gate 之外，**加第三 gate：群已推进到前沿**。

- **前沿判据**：ling 群中心**离敌方主基地比离己方主基地近**（`center.distance_to(enemy_main) <
  center.distance_to(own_main)`），或群中心在敌方 natural 一定半径内。未到前沿 → 返回 None
  （本帧不变形）。
- **推进动力**：`PlanZoneAttack(start_attack_power=8)` 在 ≥8 狗(0 爆 power 8)时已 attack-move
  把狗压向敌方 → 狗群推进过半 → forward gate 满足 → 在**前沿群中心**变蛹（仍 ≥4 狗护卫）。
- 这样：狗先压 → 到前沿 → 原地护蛹变爆，省走路（用户诉求 1）。
- **兜底**：若推进受阻（被堵在家）超时 T（如 60s 仍未到前沿）→ 放宽回"群中心就地变"，
  避免永不变蛹卡死（防 all-in 节奏被无限拖）。

### 2. 持续补毒爆 + 护蛹（sustain 走 forward）

- ling_bane 的 build-aware sustain 用共享 forward-morph 出爆虫（**不在家变**），消除和开局打架。
- **分波 cadence**：to_count 持续（cap 高，如沿用 250），forward-morph 每帧只在"狗够+气够+群在前沿+
  护卫够"时变一只 → 自然形成"攒够一波 → 变一波"。气不够时 reserve、不强变（已有
  `reserve_costs` 逻辑）。
- 护蛹 = 现有 ≥4 ling near center gate，天然满足"变形时狗在旁保护"。

### 3. 狗毒爆协同（cohesion，治拉扯）

- **锚定毒爆团**：进攻 move 时，狗的推进目标不超出毒爆群中心 N 格（N 默认 ~8，可调）。
  实现走 sharpy combat 的 regroup：确保 `regroup` 开 + `regroup_threshold` 设到让快狗等慢爆，
  但加滞回/死区避免反复拽（"拉扯"= 阈值边界抖动）。
- **按 sharpy-patch 协议**：在 vendored 战斗 move call site 加 `# vibecraft:` hook —— 若本帧
  army 含 baneling，则把 zergling 的 move 目标 clamp 到 baneling 群中心 N 格内（faster 单位
  不脱离）。毒爆提速后 N 可放宽（或 real_speed 接近时自动不触发）。
- 风险：clamp 太紧 → 龟速跟随；太松 → 仍脱节。需真局截图调 N + 死区。

## 自验（真局，不靠肉眼）

realtime 注入 ling_bane（mock LLM 起局），加 greppable 日志：
- `BANETRACE morph at=(x,y) home_dist=.. enemy_dist=..`（验在前沿变非家里变）
- `COHESION ling_centroid=.. bane_centroid=.. gap=..` 每 N 帧（验间距）
- 截图关键帧（狗压出去、前沿变蛹、行军协同）判读。

判据：① 首波 morph 的 enemy_dist < home_dist（前沿变）② 后续持续有新 cocoon（补给不断）
③ 行军中 ling-bane gap 大部分时间 < N+死区（不脱节）。

## 独立评审处理（2026-06-16 Opus，逐条 disposition → 修订设计）

**采纳（必须改 1）clamp 按 MoveType 门控**：cohesion clamp **只在 Assault/Push 生效**，
`DefensiveRetreat/PanicRetreat/ReGroup` 一律放行 —— 否则玩家"全军撤退"时狗被夹在爆群附近退不回家，
破坏玩家控制权模型（CLAUDE.md 规则 4）。配套加 `override_acceptance` zerg retreat case 验回归。

**采纳（必须改 2）cohesion 锚点含 BANELINGCOCOON**：狗/爆的锚定质心算"爆虫 **+ 蛹**"，蛹孵完才解除。
否则前压变形 + 前压推进在 14s 蛹期必把蛹甩散 → 2026-05-23 裸死坑软复现。这是问题 1 修复牢不牢的关键。

**采纳（必须改 3）forward-morph 按 build 身份/显式 flag，不按"core_units 含 baneling"数据特征**：
给 build 加显式 `baneling_morph_mode: forward|home`（默认 home），ling_bane 设 forward。
`_build_from_core_units` 的 ZERG 分支**特判 BANELING**：按该 flag 选 forward-morph 或默认 home `MorphBaneling`。
防将来宏观 build 塞个爆虫副兵就误继承 all-in 前压语义。（评审证实：muta_ling_bane 是 doctrine、无
core_units 且 Director 切 doctrine 时 sustain 不 trigger → 当前 forward-morph 实际只影响 ling_bane。）

**采纳（必须改 4）sharpy-patch 协议补全**：新 patched 方法（`default_micro_methods.handle_groups`）
进 `tests/unit/test_sharpy_patch_audit.py::PATCHED_METHODS` + `docs/sharpy-patches.md` 清单 + 新 hook
行为单测 `test_sharpy_vibecraft_hooks.py`。

**采纳（cohesion 先用 sharpy 原生，别自写 clamp）**：sharpy 已有 regroup（`micro_rules`:
regroup=True/regroup_percentage=0.75/own_group_distance=7）+ `default_micro_methods.py:88` **被注释掉的
`faster_group_should_regroup`**（正是"快组等慢组"原生实现）。**先解注释 + 调参**（同 vendored 协议、
改动小），实测不够再上 bespoke。clamp 做成 **"狗超出爆群质心 N 格就停步等"**（不发前进指令），
**不是 move-back**（反向位移加剧拉扯）。N≈6（对齐 own_group_distance=7）、死区 ±2~3。作用层级 =
`handle_groups` 给各组定 target 处（按组），非 per-unit。

**采纳（前沿判据收敛）**：① 用 **pather 寻路距离**非欧氏直线（与推进同源，绕路图不误判）；
② **只保留"过中点"下限闸**，砍掉"敌 natural 半径内"（标准 bane bust 在中线/视野外预变好再滚进，
不在敌 natural 门口现变 → 蛹必被 static D 秒）；③ 60s 超时回退"就地变" **latch**（本波保持放宽，
不随 `_should_retreat` 进退反复横跳）。

**不采纳（删除）"毒爆提速后 real_speed 接近自动不触发"**：狗带速 ~4.13、爆带离心钩 ~3.5 仍明显慢 →
不会接近，clamp 提速后**依然需要**。此条作为设计依据删除。

**采纳（自验补 3 项）**：① **蛹存活**：cocoon 发起数 vs baneling 孵出数（裸死=差值）+ 蛹孵化期 near-ling
计数；② 问题 2 自动判据：`build_acceptance` spec 加中后期 `BANELING 单调增/第二三波出现` check；
③ **玩家 override 回归**：跑 `override_acceptance` zerg retreat，证全军撤退狗能脱离爆群回家。

**集成接线（评审点名缺口）**：`_build_from_core_units` ZERG 分支当前统一 `ZergUnit(u, c)`，要在此
**特判 BANELING** 注入共享 forward-morph act（按 build 的 `baneling_morph_mode`）。

**实现顺序（按风险/价值）**：① 共享 forward-morph 模块 + build flag + sustain 特判（问题 1+2，自含）
→ 真局自验在前沿变 + 持续补；② cohesion：先解注释原生 `faster_group_should_regroup` + MoveType 门控 +
锚点含 cocoon（问题 3）→ 真局自验间距 + 撤退回归。每步真局截图/日志自验通过再下一步。

## 风险 / 取舍

- forward-morph 重蹈 cocoon 裸死：靠护蛹 gate(≥4 ling near) + 前沿判据(群已成团推进)双保险。
- cohesion clamp 影响其它含 baneling 的 build(muta_ling_bane doctrine)：hook 设计成"army 含 bane
  才触发"，纯狗/纯飞龙不受影响。
- all-in 节奏被前沿 gate 拖慢：加超时兜底回退就地变。
- 改 vendored 战斗：按 docs/sharpy-patches.md 协议(marker + audit + e2e)。
