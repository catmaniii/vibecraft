# 4 类指令端到端测试

VibeCraft directive 系统按 four-layer 架构分成 4 层（详 ADR 0010）。本文档记录
端到端自动化测试的具体用例：每层各一个 happy-path inject，跑真 SC2 验证整个
`down_q → IntentParser(LLM) → DirectiveBoard → Director → sharpy facade`
链路工作。

测试驱动：`scripts/e2e_4_directive_types.py`
首次实施：2026-05-17（M2 收尾，全 4/4 PASS）

---

## 测试用例

| Layer | 编号 | 注入文本 | 预期 directive | 验证字段 | 实测结果 |
|---|---|---|---|---|---|
| L1 宏观策略 | strategy_set | `切叉球一波` | `strategy_set(stage=midgame, strategy_id=iac_2base)` | `snapshot.strategy.midgame.id == "iac_2base"` 或 events 含 `strategy.set` 或 `pending_force_strategy` | ✅ stage=midgame id=iac_2base |
| L2 战术目标 | tactical_objective | `进攻对方自然` | `tactical_objective(verb=attack, target_area=enemy_natural, done_when=any_of(...))` | `snapshot.active_tactics` 非空 **或** events 含 `directive.committed` | ✅ events directive.committed + released |
| L3 单位/常驻 | unit_claim (persistent) | `探机巡逻自然别动` | `unit_claim(selector=Probe, task=patrol natural, persistent=true)` | `snapshot.standing_orders` 非空 | ✅ Probe patrol natural |
| L4 产能调整 | production_override | `下个 BG 出俩哨兵` | `production_override(unit_type=Sentry, count=2, done_when=unit_count_built_since)` | `snapshot.production_overrides` 非空 **或** events 含 `directive.committed` | ✅ events directive.committed + released |

---

## 用例选取逻辑

### L1 为什么不用 "切 4BG"（opening 切换）

`切 4BG` 触发 `_check_strategy_obsolete` 的 OpeningBuild 时机检测。fast mode 下
inject 时 game 内时间已数分钟，bot 已造 RoboticsFacility 等 4bg 不需要的科技
建筑 → directive 被拦下进 `_pending_force_strategy`，**不进 board**，
snapshot 看不到。

`切叉球一波` 走 midgame_stance（iac_2base），midgame 没有 obsolete 时机检测，
直接落 board。`叉球一波` 在 `strategies/protoss/iac_2base.yaml` aliases 里，
LLM 解析稳定。

driver 同时兼容 `pending_force_strategy` 出现也算 LLM 识别成功（玩家可点
"硬转确认" 强制落 board）。

### L2/L4 为什么靠 events 兜底而不只看 snapshot

L2 `进攻对方自然` 的 done_when 通常是 `any_of(target_destroyed natural,
own_army_size_ratio<0.3)`。当前 game state 中敌方 natural 可能不存在（还没
开），`target_destroyed` 立即满足 → directive 进 board → task_monitor 同 tick
判定 done → 从 `_in_flight` pop。snapshot 推送窗口（~2s）可能完全错过这条
in-flight 状态。

L4 `下个 BG 出俩哨兵` 的 done_when 是 `unit_count_built_since Sentry >=2`。
bot 可能已有 ≥2 哨兵 → 立即满足，同样错过 snapshot。

verify 改成 "snapshot 字段非空 **OR** events 流出现 `directive.committed`"。
后者证明 directive 真的进了 board + 触发 committed event，业务上等价 PASS。

### L3 为什么 inject 选 "探机巡逻自然别动"

- `探机`：开局任何时候都至少 12 个 → selector resolve 一定有结果
- `巡逻` → verb=patrol
- `自然` → target=`named_spot:natural`
- `别动` 强化 persistent=true 信号（standing order）

LLM prompt few-shot 例 5b 就是类似句式，解析稳定。standing order 不带
done_when，会持续在 `standing_orders` list 里直到 revoke，snapshot 一定看得到。

---

## 运行测试

```bash
# 全 4 case（每个独立 SC2 子进程，约 30-35s/case + 5s 间隔 = ~150s wall）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --seconds 75

# 单跑一个 case 调试
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --only L1 --seconds 75
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --only L4 --seconds 75

# 自定 map（默认 DaybreakLE）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --map "Goldenaura LE"

# 自定 force opening（默认 1g_robo_immortal）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --initial-opening 4bg
```

退出码：4 case 全过 = 0，任一失败 = 1。

### 输出例（4/4 PASS 时）

```
======================================================================
汇总:
  PASS L1 strategy_set       — stage=midgame id=iac_2base (snapshots=22, 32.1s)
  PASS L2 tactical_objective — events directive.committed+released (snapshots=24, 31.6s)
  PASS L3 unit_claim         — standing_orders[0]={Probe patrol natural} (snapshots=25, 34.0s)
  PASS L4 production_override— events directive.committed+released (snapshots=25, 32.9s)
======================================================================
结果: 4/4 通过
```

---

## 测试基础设施

### Fast mode + VeryEasy 难度

`GameConfig(realtime=False, opponent_difficulty="VeryEasy")`：

- fast mode：SC2 全速跑（~20x 实时），30-35s wall 跑完一局
- VeryEasy 对手：bot 不会被秒杀，有时间观察 directive lifecycle

### HangWatchdog 兜底

测试不能假设 SC2 永不卡死。`vibecraft.bot.watchdog.HangWatchdog`：

- 子进程内 daemon thread，每 5s 检查 `bot.time`
- bot.time wall-clock 30s 不前进 → 自动 `psutil` kill 所有 SC2_x64.exe +
  子进程 `os._exit(87)`
- driver 收到 `sc2=crashed` 把这个 case 直接判 FAIL（继续下一个）

测试期间 0 误报（bot 都正常打完赢了 VeryEasy AI，没 stall）。
真要禁用：`VIBECRAFT_DISABLE_HANG_WATCHDOG=1`。

### sc2=ended 后 drain 2s

SC2 ended 事件到达父进程后，子进程内的最后几条 directive event 可能还在
multiprocessing.Queue 里没被父进程取出。driver 看到 sc2=ended 后再 drain
2s 才退出 collect loop，避免漏抓 in-flight committed/released event。

---

## 加新测试 case 怎么改

`scripts/e2e_4_directive_types.py` 的 `CASES` 列表加一行 `Case(...)`：

```python
Case(
    name="L2 vision",  # 描述性 name（--only 子串匹配）
    inject="看一眼对方主基地",
    inject_after=3,
    verify_field="active_tactics",  # 或自定 verify_field
),
```

新 verify_field 走 `_verify_field_non_empty(snapshots, events, field)`，
默认看 `snapshot[field]` 非空 + events 兜底 `directive.committed`。

需要自定 verify 逻辑（如 L1 的 strategy_changed）→ 在 `run_one_case` 里加
分支调专门的 verify 函数。
