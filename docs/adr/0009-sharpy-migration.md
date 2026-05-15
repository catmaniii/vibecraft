# ADR 0009: ares-sc2 → sharpy-sc2 全框架迁移

**日期**: 2026-05-16
**状态**: 已实施（M0-M5 完成）
**决策者**: catmaniii

---

## 背景

voicecraft M1 完成后，底层 bot 框架仍沿用 Aristaeus（ares-sc2 的 starter 模板）。
问题：

1. Aristaeus 没有完整战术（cannon rush + oracle，与 voicecraft 4 剧本毫无重叠）
2. voicecraft 4 剧本（1门Robo / 4BG / IAC / Skytoss）在 ares 框架里完全没有接对应
   Manager，所有剧本口令只停留在 IntentParser → Director，到 Facade 就断了
3. ares `BuildOrderRunner.switch_opening()` 是唯一运行时切剧本的 API，但它只支持
   opening 阶段，midgame / lategame 无 API

## 调研结论（spike，2026-05-16）

| 维度 | ares / Aristaeus | sharpy-sc2 |
|---|---|---|
| 战术覆盖与 voicecraft 4 剧本对应度 | ~10% | ~70%（robo/gate4/macro_stalkers/voidray 直接对应）|
| 单 dummy 完整度 | starter 模板 | 真实完整对战 bot（robo.py 95 行就有 build + 扩张 + 攻防 + 微操）|
| Combat/Production Manager | 各 bot 自包，质量参差 | 框架级共享，12 dummy 共用同一套成熟 manager |
| Ladder 战绩 | Aristaeus 没成绩 | SharpenedEdge 长期前 10，MMR 1953 |

## 决策

整体迁移到 sharpy-sc2，vendor 在 `vendor/sharpy/`（sharpy 不在 PyPI）。
`_VoiceCraftProtossBot` 继承 `sharpy.knowledges.KnowledgeBot`。

## Hook A 问题与解法

**问题**：sharpy 无运行时切剧本的原生 API（ares 有 `switch_opening()`，sharpy 没有）。

**POC 验证**（M0）：sharpy `IfElse` 的 `RequireCustom.check()` 每 step 重新求值
lambda —— 因此只要把 4 个剧本的 `BuildOrder` 挂进嵌套 `IfElse` 树，再用
`active_recipe` flag 控制路由，就能实现运行时切换。

**实施**（M3）：`create_plan()` 返回：
```
BuildOrder(
  IfElse(lambda k: active_recipe == "1g_robo_immortal", robo_plan,
    IfElse(lambda k: active_recipe == "4bg", gate4_plan,
      IfElse(lambda k: active_recipe == "iac_2base", macro_stalkers_plan,
        voidray_plan  # fallback
      )
    )
  )
)
```
`_SharpyFacade.set_build(name)` → `bot.active_recipe = name` → 下一个 step
IfElse 立即走新分支。

## Hook C 问题与解法（M4）

**问题**：sharpy `UnitTask.Reserved` 并非框架级 skip —— `UnitRoleManager.update()`
每帧清空 `had_task_set`，下帧 update 时未声明的单位会被重置为 Idle/Gathering。

**解法**：在 `_VoiceCraftProtossBot` 维护 `_llm_controlled_tags: set[int]`，
每 step `super().on_step()` 之后调 `_refresh_llm_controlled_roles()`，对集合里
的每个存活单位重新调 `roles.set_task(UnitTask.Reserved, unit)`，确保当帧
`had_task_set` 已登记。

**哪些 manager 自然尊重 Reserved**（grep 结论）：
- `PlanZoneAttack`：第 114 行 `for unit in self.roles.free_units`（free_units =
  Idle + Moving），Reserved 不在 free_units → **自然不会被拉去出门攻击**
- `UnitRoleManager.get_defenders()`：只查 Idle/Moving/Fighting/Attacking，
  Reserved → **不会被拉去守基地**
- `GroupCombatManager`：按显式 `add_unit()` 执行，不自动拉 Reserved 单位

**哪些 manager 还会"误调"**（文档化，留后续处理）：
- 任何直接迭代 `bot.units` 而不经 roles 过滤的 tactics（M5+ 逐一 hook）
- `zone_gather.py`：独立设 Reserved 覆盖（§1.3 矿工采集逻辑），不干扰我们
  LLM_CONTROLLED 的 Reserved 单位

## M5 attack_window / micro_doctrine 字段穿透

voicecraft YAML 的 `attack_window` / `micro_doctrine` 字段在 `director.build_snapshot`
里穿透到 snapshot 帧，供手机 PWA 剧本卡片展示"出门 9:30–11:30"等信息。
bot 行为本身不变（仍用 sharpy dummy 自带 timing），这是纯信息透明层。

## 不变量更新

- `_VoiceCraftProtossBot` 继承 `KnowledgeBot`，通过 `create_plan()` 返回
  `BuildOrder(IfElse(...))` 树，由 `active_recipe` flag 路由
- `_llm_controlled_tags` 持久化 LLM_CONTROLLED 单位 tag，每 step refresh Reserved
- sharpy vendor 在 `vendor/sharpy/`，`sys.path` 注入（lazy，单测 mock sys.modules 绕开）
- `StrategySlotView` 加 `attack_window?` / `micro_doctrine?` 字段（M5）

## 风险 / 迁移残留

- sharpy 12 dummy 里只 vendor 了 4 个（robo/gate4/macro_stalkers/voidray）；
  剩余 8 个作为后续战术库扩充
- sharpy `config.ini`：sharpy 从 CWD 读配置，game_process.py 启动时需确认 CWD
  有 config.ini 或显式 chdir（端到端验证时排查）
- M4 未做 GroupCombatManager subclass 级过滤，只靠 free_units 链路兜底；
  若未来出现 bot 直接 for unit in ai.units 迭代的 tactics，需逐个 hook

## 实施里程碑

M0 POC → M1 框架替换 → M2 4 dummy 映射 → M3 IfElse 路由 → M4 role 隔离 →
M5 字段穿透 → M6 文档

## License

sharpy-sc2 MIT license。vendor 路径 `vendor/sharpy/`，许可证见
`vendor/sharpy/LICENSE`，ATTRIBUTION 见 `vendor/sharpy/ATTRIBUTION.md`。
