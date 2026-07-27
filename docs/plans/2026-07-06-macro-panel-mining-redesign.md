# 运营策略面板重构 + 采矿策略（2026-07-06 用户）

## 需求（用户）
运营策略面板（`MacroButton.vue`）当前两维度：开矿（1-5/max/默认 chips）+ 农民生产（停/补满/默认）。改：
1. **删掉"开几个框 1-5"chips**（不实用、易出错）→ 换**一个「多开一个矿」按钮**：点一下发一张扩张
   指令卡（`expansion_override` current+1），新矿建成后卡标记完成消失。
2. **新增「采矿策略」维度**：优先水晶 / 优先气 / 默认。
3. **保留「补农民策略」**（停/补满/默认，即现有 workers 维度）。

## 采矿策略语义（用户明确，2026-07-06）
优先级分配气矿农民数，**只在农民不足时起作用**（过饱和时水晶本来就满、多的自然去气，改了没用）：
- **优先水晶**：先把水晶采满（每基地 `ideal_harvesters` = 矿片×2），**多出来的农民才去采气**。
  - 例：10 农民、矿容量 16 → 全 10 采水晶、0 采气。
- **优先气**：先把气采满（每井 3 农 = 2 井 6 个），**剩下的才采水晶**。
  - 例：10 农民、2 井 → 6 采气、4 采水晶。
- **默认**：bot 自己配（清除 override，走 sharpy 默认 aggressive_gas_fill）。

## 执行机制（sharpy 集成）
`DistributeWorkers`（`vendor/sharpy/.../distribute_workers.py`）已有 `min_gas`/`max_gas` 参数，
`calc_gas_workers_target()` 按它算气矿农民目标。**不新写 worker 分配，只按采矿策略动态设 min/max_gas**：

| 采矿策略 | 设置 | 效果 |
|---|---|---|
| 优先水晶 | `max_gas = max(0, 总采矿农民 - 水晶ideal总和)` | 水晶填满 2/片，气只拿溢出 |
| 优先气 | `min_gas = 气井总数 × 3` | 气先填满，剩下采水晶 |
| 默认 | 不设（None） | sharpy 默认 |

**其中**：水晶ideal总和 = Σ `townhall.ideal_harvesters`（ready、非 stealth）；总采矿农民 = supply_workers
（减 stealth 农民，账目分离，同 `_tick_worker_saturation`）；气井总数 = `gas_buildings.ready.amount`。

**传递方式（走 knowledge.vibecraft，同 combat_intent_override 模式，需 sharpy patch）**：
- director `apply_macro_action(dim="mining")` → 设 `knowledge.vibecraft.mining_priority ∈ {"mineral","gas",None}`。
- **patch `DistributeWorkers.execute`（vendor）**：开头 read `getattr(knowledge.vibecraft,"mining_priority",None)`，
  据此**每帧覆写** `self.min_gas`/`self.max_gas`（用上表公式，读 townhall/gas ideal 实时算），再走原逻辑。
  - `# vibecraft:` marker + 加进 `test_sharpy_patch_audit.py::PATCHED_METHODS` + `docs/sharpy-patches.md`。

## 前端（MacroButton.vue + useWs.ts + types.ts + i18n）
1. 维度1「开矿」：删 EXPAND_OPTIONS chips → 一个「多开一个矿」按钮 → `macroAction('expand','one_more')`。
   - 高亮/状态：按钮不需要持久高亮（一次性发卡）；发完可短暂反馈。
2. 新维度「采矿策略」：chips 优先水晶(`mineral`)/优先气(`gas`)/默认(`default`) → `macroAction('mining', v)`。
   高亮跟 snapshot 回传的 `mining_priority`（同 workerMode 模式）。
3. 维度2「补农民」：不动（停/补满/默认）。
4. `types.ts`：`MacroActionFrame.dim` 加 `'mining'`；expand value 加 `'one_more'`。
5. i18n：新增 `macro.expandOneMore` / `macro.miningLabel` / `macro.miningMineral` / `macro.miningGas` 等 key（中英）。

## 后端（ws.py + director.py + snapshot）
1. `ws.py::_handle_macro_action`：`_VALID_EXPAND_VALUES` 加 `"one_more"`；加 `dim=="mining"` 校验
   （`{"mineral","gas","default"}`）。
2. `director.py::apply_macro_action`：
   - `expand=="one_more"`：建 `expansion_override`，target_count = 当前基地数(含在建) + 1（复用 parser 的 current+1 逻辑；查 `_current_base_count`）。**每按一次 +1**（发新卡；或同一张卡 target+1，二选一——**倾向每次发一张新卡**，简单、卡完成独立消失）。
   - `dim=="mining"`：`mineral/gas` → 设 `self._mining_priority` + `knowledge.vibecraft.mining_priority`；`default` → 清 None。**不发指令卡**（它是持续状态，像 worker_mode，snapshot 回传高亮，不进卡片堆）。
3. snapshot：加 `mining_priority` 字段（同 `worker_mode`），前端读它高亮采矿策略 chip。

## 不做 / YAGNI
- 采矿策略**不做每基地独立**（全局一个优先级即可）。
- 「多开一个矿」**不做指定矿点**（bot 自选下一个，同现有 expansion_override）。
- 过饱和保护：公式天然处理（农民多时 max_gas 溢出大、min_gas 填满后剩余去矿），无需特判。

## 验证
- 前端：MacroButton 单测（按钮 emit one_more、mining chips emit、无 1-5 chips）+ 浏览器截图判读排版。
- 后端：ws macro_action 校验单测 + director apply_macro_action(mining/one_more) 单测 + snapshot 字段。
- sharpy patch：`test_sharpy_patch_audit.py` + 新 hook 行为单测（mining_priority 设 min/max_gas）。
- 真局自验：起局注入 `macro_action mining=mineral`，读 telemetry `gas_workers` 应→0（农民少时）；
  `mining=gas` → gas_workers→6（2井满）；`expand one_more` → 新基地建成 + 卡消失。
- 面板/prompt 无关（这是 UI 直接控制，不走 LLM）。

## 文档
- 功能面向玩家 → 更新 USER_GUIDE（运营策略面板说明：多开一个矿 + 采矿策略）+ ARCHITECTURE（mining_priority 数据流 + sharpy patch）+ CHANGELOG。

---

## 评审处置（2026-07-06 opus 评审，5 必改全部采纳）

1. **走 facade，不直碰 knowledge（facade 纪律）**：director **不**直写 `knowledge.vibecraft.mining_priority`，
   新增 **`facade.set_mining_priority(v)`**，**三处齐全**：`Sc2Facade` Protocol + `FakeFacade` + `_SharpyFacadeBase`
   （common_bot.py，真机跑的）。改完跑 `tests/unit/test_facade_release_unit_role.py` 的 Protocol 一致性 audit。
   `knowledge.vibecraft` 初始化（common_bot.py ~1982）加 `mining_priority=None`。
2. **两字段成对确定性写**：优先水晶 → `max_gas=公式, min_gas=None`；优先气 → `min_gas=公式, max_gas=None`。
   每种优先级两字段都显式定死，不残留（多个 Terran 剧本构造 `DistributeWorkers(min_gas=6)`，只写一个字段会打架）。
3. **「默认」= 恢复构造期缓存的原始 min/max_gas，不是写 None**：patch 在 `execute` **首帧缓存** `self.min_gas`/
   `self.max_gas` 原始值（`_vc_orig_min_gas`/`_vc_orig_max_gas`）；`mining_priority is None` 时**恢复这对原值**
   （否则永久砸掉剧本给的 `min_gas=6`）。
4. **base count 用真实符号**：apply_macro_action 里 one_more 的 current = `len(self._bot.townhalls.ready) +
   int(self._bot.already_pending(NEXUS))`（`_current_base_count` 是幽灵符号，不存在，删掉）。
5. **one_more 绕开旧 expand 分支的两个副作用**：**不**撤旧卡（不占用 `_macro_expand_dir_id` 单槽 → 连按发多张独立卡）、
   **不**调 `facade.set_expansion_override`（那是给自然扩张封顶 `expansion_cap_override`，会把 bot 运营扩张冻死在 current+1）。
   one_more 只 `_submit_directives` 一张 `expansion_override(target_count=current+1)` 卡触发扩张，fire-and-forget。

**采纳的建议**：
- **前端连线补全**：`mining_priority?: string|null` 加进 Snapshot 帧类型（types.ts，同 worker_mode 处）+ useWs 加 `miningPriority`
  ref 读 `f.mining_priority` + CockpitView 透传给 MacroButton 高亮。
- **死代码清理**：删 chips 后清 `useWs.ts` `macroExpandTarget`、`MacroButton` `expandTarget` prop/`expandActiveCls`、
  `types.ts` `macro_expand_target`、director snapshot `macro_expand_target` 写入、i18n `macro.expandMax`/`macro.expandAria`。
- **max_gas 公式基准**：用 sharpy 自身的 `roles.free_workers.amount`（排除 Reserved）对齐，而非 supply_workers（注在 patch 注释）。
- **连按可靠性**：one_more 连按快可能只 +1（首扩 on_hold 时 pending=0，多张卡 target 同值）——**作为"想一下点一下"的单点按钮可接受**，
  不承诺"连按 N = N 矿"。（如需可靠连按再改递增期望值，YAGNI 暂不做。）

**VERIFIED（2026-07-06 真局自验，mining_priority_selftest.py PASS）**：
`townhall.ideal_harvesters == 矿片×2 且不含气、随采空实时减`——
1 主基地 `ideal_harvesters = 16`（8 矿片 × 2），不含 gas 工人；
free_workers=19 → max_gas = max(0,19-16)=3，gas_workers 真降到 3，telemetry 后40%均值 4.5 < 5.5 ✓。

**用户已确认**：删 1-5 chips = 移除 UI 层"扩张封顶/解封"能力（旧 chips 兼做 cap）。用户明说"有这一个按钮就够了"，
接受无 cap（语音 LLM 路径仍可封顶）。
