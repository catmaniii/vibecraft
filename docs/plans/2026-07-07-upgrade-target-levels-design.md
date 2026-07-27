# 攻防升级目标等级手动设定 — 设计文档

> 2026-07-07。用户需求 + 独立评审前的设计真理源。

## 评审处置（2026-07-07 opus，全部采纳 — 实现按这里的修正为准）

opus 真源核对，总评"需改后可行"（核心架构成立）。**6 个 must-fix 全采纳，实现时务必照做**：

1. **`ZERGFLYERATTACK` 是错名 → `ZERGFLYERWEAPONS`**（真机 enum：前者不存在）。这是既有 bug，
   连累 `_KNOWN_UPGRADE_NAMES`（`director.py:2224-2226`）+ zerg 空攻当前等级面板显示（一直 0 级）。
   **修 `_KNOWN_UPGRADE_NAMES` 一处同时修好 view + cap**；15 族白名单**从修正后的 `UpgradeId` enum 派生**
   （`hasattr(UpgradeId, f"{fam}LEVEL1")` 校验），别手抄。
2. **门伪码 `self.name` → `self.upgrade_type.name`**（`Tech` act 字段是 `self.upgrade_type: UpgradeId`，`tech.py:36`，无 `self.name`）。
3. **`set_upgrade_target` 走 facade → 必须 `FakeFacade` + `_SharpyFacadeBase` 两实现 + 跑 Protocol audit**
   （`test_facade_release_unit_role.py` 那条一致性 audit）。是带 `{family,level}` 结构化 payload 的新 action，
   dispatch 要新写解析 + 校验 `family ∈ 15 ∩ 本族`（参考 `set_mining_priority` 是 facade 方法 `director.py:4571`）。
4. **门必须顶置于 `Tech.execute`**：`if not self.enabled: return True` 之后、`builders`/`reserve`（`tech.py:89-91`）之前。
   否则被封顶升级每帧 `knowledge.reserve(cost)` 预留矿气 → 饿死其他 act 下单预算。
5. **`_parse_upgrade` 用 15 族精确白名单 + 复用 `^(.*)LEVEL([123])$` 正则**（同 view `director.py:2478`），
   门 key 与面板 `track_en` **同源**，杜绝键名漂移。非攻防升级（BLINK/CHARGE/兵种技能）`fam=None` 不拦。
6. **术语**：全文"返回 SUCCESS/FAILURE"= 代码里 **`return True`/`return False`（bool）**，`ActBase.execute` 返回 bool
   （`True`=完成/继续下一 act，`False`=阻塞不继续）。别去找不存在的 SUCCESS 常量。

**should-fix（采纳）**：① 门落 sharpy patch 三件套（`# vibecraft:` marker + `PATCHED_METHODS` audit + `docs/sharpy-patches.md`）。
② USER_GUIDE FAQ 记"降 target 取消不了已在研的那一级（升级不可撤销），只影响后续级"。③ 真局自验**把 zerg 空攻单列一个 case**（最易错线）。
**实现时顺手确认**：grep openings yaml 有没有 `research <攻防>` 步落到非 Tech 执行器（当前证据表明只走 .py plan 的 Tech，收口成立）。

核心机制已真源核对：`ActBase.execute` 返回 bool（`act_base.py:77-81`）；`SequentialList` 遇 False 停、门返 True 推进不卡后续
（`sequential_list.py:28`+`build_step.py:65`）；三族攻防全经 `Tech()`（protoss/terran/zerg plan 均核对，`ChronoTech` 只加速不启动、不绕过）。

## 需求（用户 2026-07-07）

玩家能在**科技/产能/工具面板**里，对**每一条攻防升级线**手动设目标等级：
- 设 `0` → bot **不主动升**这条线。
- 设 `N`（1/2/3）→ bot **自动升到 N 封顶**，不超过。
- 设 `自动` → 交给 bot 自行决定（默认，= 当前行为）。
- **三族所有攻防升级线**都要有设置 + 查看选项。粒度**细**（按 SC2 真实升级线分开，不合并）。

## 升级线清单（15 条，取自 `_KNOWN_UPGRADE_NAMES`）

| 族(family, 无 LEVEL 后缀) | 中文 | 种族 |
|---|---|---|
| `PROTOSSGROUNDWEAPONS` | 地面攻 | 神 |
| `PROTOSSGROUNDARMORS` | 地面防 | 神 |
| `PROTOSSSHIELDS` | 护盾 | 神 |
| `PROTOSSAIRWEAPONS` | 空中攻 | 神 |
| `PROTOSSAIRARMORS` | 空中防 | 神 |
| `ZERGMELEEWEAPONS` | 近战攻 | 虫 |
| `ZERGMISSILEWEAPONS` | 远程攻 | 虫 |
| `ZERGGROUNDARMORS` | 地面甲 | 虫 |
| `ZERGFLYERWEAPONS` | 空中攻 | 虫 |
| `ZERGFLYERARMORS` | 空中甲 | 虫 |
| `TERRANINFANTRYWEAPONS` | 步兵攻 | 人 |
| `TERRANINFANTRYARMORS` | 步兵防 | 人 |
| `TERRANVEHICLEWEAPONS` | 机械攻 | 人 |
| `TERRANSHIPWEAPONS` | 舰船攻 | 人 |
| `TERRANVEHICLEANDSHIPARMORS` | 机械/舰船甲 | 人 |

（人族机械甲 + 舰船甲在 SC2 里是同一条线 `VEHICLEANDSHIPARMORS`，故人族 5 条不是 6 条。）
面板按 `--my-race` 只显示本族 5 条。

## 数据模型

`knowledge.vibecraft.upgrade_targets: dict[str, int | None]`
- key = family（上表第一列，无 LEVEL 后缀）。
- value = `0` / `1` / `2` / `3` / `None`(自动，默认)。
- 初始化（`common_bot._SNS(...)`）：`upgrade_targets={}`（空 = 全部 auto）。
- 只存**非 auto** 的 family（省内存 + 语义清晰）：设成"自动"= 从 dict 删除该 key。

## 封顶门（唯一触发点：sharpy `Tech` act）

**取证结论**：三族攻防升级**全部**经 sharpy `Tech(UpgradeId.<FAMILY>LEVEL<N>)`（plan 里
`Step(UnitReady(FORGE,1), Tech(...))` + build-order `research` step 都归到 Tech act 执行）。
→ 封顶门加 **`vendor/sharpy/sharpy/plans/acts/tech.py::Tech.execute`** 一处即全封住。

**门逻辑**（`# vibecraft:` marker，`getattr` 兜底）：
```
在 Tech.execute 真正下 research 命令前：
  fam, lvl = _parse_upgrade(self.name)   # UpgradeId → (family, level) 或 (None,None) 非攻防
  if fam is not None:
      tgt = getattr(getattr(knowledge,'vibecraft',None), 'upgrade_targets', {}).get(fam, None)
      if tgt is not None and lvl > tgt:   # 手动封顶且本级超标
          return SUCCESS(不研究，视作"已满足"让 plan 继续，不卡后续 step)
      # tgt=0 → 任何 lvl>=1 都 > 0 → 全跳；tgt=None(auto) → 不拦
```
- **返回 SUCCESS 而非 FAILURE**：Tech step 卡在 BuildOrder 里，返回 FAILURE 会**卡死后续 step**
  （sharpy BuildOrder 顺序执行，一个 act 没 done 就不往下）。返回 SUCCESS = "这级不用做，跳过继续"。
- `_parse_upgrade`：按 15 个 family 前缀匹配 + 提取末尾 `LEVEL<N>` 的 N。非攻防升级
  （BLINKTECH/CHARGE/兵种技能）`fam=None` → 门不拦（这些不在本功能范围）。

**为什么门放 vendor patch 而非 build-order 层**：build-order 的 `research` step + 各 plan 的
`Tech()` step 分散在 15+ 处，逐个加门会漏；Tech act 是**唯一执行收口**，一处封死最稳
（同 CLAUDE.md sharpy patch 规则：execute 内派命令的 call site 前加 marker）。

## 后端 action + 穿透

- 新 macro/strategy action `set_upgrade_target(family: str, level: int | 'auto')`：
  - `level='auto'` → `upgrade_targets.pop(family, None)`；否则 `upgrade_targets[family]=int(level)`。
  - 走现有 `sendStrategyAction` 通道（同"宏观策略"面板 action 路径）。校验 family ∈ 15 条 + 本族。
- 落 JSONL 日志（`upgrade_target_set family=.. level=..`）。

## View（面板显示当前 + 目标）

`_build_tech_progress` 现已枚举本族攻防线 + 当前等级（`leveled` track：`level` / `researching_level`）。
**加 `target` 字段**：`target = upgrade_targets.get(family, None)`（None=自动）。前端据此高亮当前选中的 chip。

## UI（科技/产能/工具面板，每族一行）

TechProgressPanel（现只读显示）加**目标设定控件**。每条攻防线一行：
```
地面攻   [当前 2 级 ●●○]   目标: [0][1][2][3][自动]
```
- 左：中文名 + 当前等级（已有的 leveled 显示，复用）。
- 右：一排 chips `[0][1][2][3][自动]`，高亮当前 target（`target===null`→自动高亮）。
- 点 chip → `emit('strategyAction','set_upgrade_target', {family, level})`。
- 只渲染本族 5 条（`race` prop 过滤）。兵种科技（BLINK/CHARGE 等）**不加**目标控件（不在范围）。

## 验证

1. **单测**：
   - `_parse_upgrade` 15 族 + 非攻防 → 正确 (family, level) / (None,None)。
   - 门逻辑：target=0 全跳；target=2 时 lvl3 跳 lvl1/2 过；target=None 不拦。（mock knowledge）
   - `set_upgrade_target` action 写/删 upgrade_targets（含 'auto' → pop）。
   - `_build_tech_progress` target 字段。
   - sharpy patch audit（`Tech.execute` 进 `PATCHED_METHODS`）。
2. **真局自验**（新脚本 `upgrade_target_selftest.py`，mock LLM 注入 set_upgrade_target）：
   - 注入"地面攻设 0" → 跑 build 带 +攻的 → 断言 telemetry **终态** PROTOSSGROUNDWEAPONS 恒 0 级
     （**外部终态黑盒门**，非中间 trace）。
   - 注入"地面攻设 1" → 断言升到 1 后**不再升 2**（跑够时长）。
   - per-family 断言，别聚合。
3. 前端组件单测 + preview 截图（TechProgressPanel 带目标控件，中英）。

## 文档同步（功能交付四文档）

ARCHITECTURE（新 knowledge 字段 + Tech 门 + 数据流）、USER_GUIDE（玩家话术 + 面板用法）、
README（能力）、CHANGELOG。sharpy patch 清单（`docs/sharpy-patches.md`）+ audit。

## 待评审确认点

1. 门返回 SUCCESS（跳过不卡后续）是否正确 —— 需确认 sharpy BuildOrder 对 Tech act SUCCESS 的语义
   （SUCCESS 是否推进到下一 step 且不重试本 step）。**评审重点**。
2. `Tech.name`（UpgradeId）→ family/level 解析：确认 sharpy `Tech` act 存的是 `UpgradeId` 且
   `.name` 是 `PROTOSSGROUNDWEAPONSLEVEL1` 这种全大写（真机核对，别望文生义 —— salvage 教训）。
3. 语音/LLM 指令入口要不要一起做（"地面攻升到2"），还是先只做面板？（建议先面板，LLM 后续）。
