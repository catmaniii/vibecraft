# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-vibecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。
已完成里程碑的细节拆解不留在这里，去 `CHANGELOG.md` / `git log` / `docs/adr/`。

---

## 当前状态（最近更新：2026-05-20，分支 `m4-l2-l4-executor`，HEAD = `e70fff5`）

- **里程碑**：M6 三族 bot 完成后，本阶段建成 **build acceptance 验收框架**并完成
  神族 8 策略验收 + 修复。
- **build acceptance 框架**（2026-05-20 建成 + 加固）：
  - `telemetry.jsonl`（游戏内状态采集）+ acceptance spec（pydantic）+ verifier + runner
  - 跑法：`uv run python scripts/build_acceptance.py <strategy_id> --runs 3`
  - 计数类 check 用 `[at±tol]` 时间窗口判定；`--runs N` 多跑按 check 多数票
    （消除单跑帧抖动 / 交战随机性）。
  - 流程文档 `docs/process/new-opening-strategy.md`；设计
    `docs/plans/2026-05-20-build-acceptance-testing-design.md`
- **神族 8 策略基线**（VeryEasy 档，3 跑多数票，2026-05-20，spec 校准后）：

  | 策略 | 验收 | 结果 | 备注 |
  |---|---|---|---|
  | 4bg | 9/9 | Victory | 用户手调过，基线 |
  | phoenix_2base | 21/21 | Victory | 满分（双星门提前 + spec 校准）|
  | 1g_robo_immortal | 16/16 | Victory | 满分 |
  | iac_2base | 18/19 | Victory | 剩 dt_count_first_batch（单 warpgate 限速）|
  | dt_drop_iac | 18/20 | Victory | 拖局 Tie 已修；剩 2 个瞬态位置 check |
  | dt_rush | 15/16 | Victory | 剩 dt_at_enemy（瞬态位置 check）|
  | blink_stalker | 15/18 | Victory | 剩 3 个真·产能 FAIL（暴 BG / stalker）|
  | cannon_rush | 5/12 | Defeat | ForwardCannonProxy 未解决（立项 1）|

  7/8 Victory（无 Tie）；总 117/131。
- **下一步**：见下「build acceptance 待办」。

---

## build acceptance 待办

### 立项 1（深坑，需独立多轮迭代）

**cannon_rush — ForwardCannonProxy 不建 BF/BC**
- 现象：ForwardCannonProxy 从未建成 Forge/Cannon，验收卡 5/12、Defeat。
- 已知设计缺陷：plan 建 Forge→Cannon，但 **Forge 不提供 psi matrix**，proxy 点
  没 Pylon → 即便 BF 建成 BC 也建不了。需重新设计成 Pylon→Forge→Cannon。
- BF 没建成的根因仍未定位（两次 debug，ForwardCannon 运行日志诡异地一行不出）。
  下一步：用 `print()` 直接 instrument execute 各分支（不依赖 loguru）。
- 已修复部分：气矿 bug（原 plan 删气矿导致折跃/追猎瘫痪，已修，确定有效）。

> 立项 2（dt_drop_iac 拖局 Tie）已修复 —— 根因是两个 VibeCraftZoneAttack 串进
> 同一 SequentialList 互相 block + DT 产能瓶颈。见 git log `cbc5b2a` / `4b14dd4`。

> spec 校准已完成（2026-05-20，git log `e70fff5`）：6 个策略 ~12 处 spec
> 数值是 research 子agent 写错的（算术错误 / 偏晚偏早），3 跑实测后已校准。
> 「二矿系统性偏晚」经实测证伪 —— iac/phoenix 的 spec 算错了（plan 二矿正常）；
> iac 提前二矿实测会拖垮科技线，证明 plan 现状是对的。

### 真·plan 产能偏弱（剩余 FAIL，需深修或战术取舍）

- **blink_stalker（15/18）**：4 BG 偏慢 + stalker 产能不足 → 出门只 6-7 兵
  （spec 10）、warp_prism 没出。FAIL：four_gateways / stalker_count_moveout /
  warp_prism_ready。实测提前 cyber 触发无效，需深查 stalker 产线根因。
- **iac_2base（18/19）**：dt_count_first_batch —— DT 首批只 2（spec 4），
  单 warpgate warp DT 限速。注：iac 科技密集，提前暴 BG 会拖垮科技线（实测
  验证过），不能照搬 dt_drop_iac 的「提前暴 BG」修法。

### 验收 check 固有限制（非 plan 问题，低优先级）

- 瞬态位置 check（dt_rush `dt_at_enemy`、dt_drop_iac `warp_prism_at_enemy`
  / `army_gather`）：验单位"某刻在某地"，但 DT / 棱镜边打边死、主力分裂成
  骚扰 + 本队 → 单快照判定天然 flaky。verifier 的窗口判定目前只覆盖计数类。
  选项：给位置类 check 也加窗口判定，或接受这几个 check 不稳。

### 框架 / 流程

- **验收跑 SC2 必须串行** —— 两个 build_acceptance 实例并发会撞，结果污染。
- 虫族 / 人族策略验收 —— 神族跑完后按 `docs/process/new-opening-strategy.md` 流程做。

---

## 其它待办

- **PWA strategy picker** —— 玩家宏观策略选择界面看不到全部策略（中后期 persistent
  doctrine 不可见）。
- **M7 候选**（三族 bot 之后）：
  1. PR merge → main，打 `v0.6.0` tag
  2. M7a PWA race selector（手机端种族选择，免 CLI flag）
  3. M7b random race + opponent_race counter（按对手种族推荐剧本）
  4. M7c 虫族 creep tumor / M7d 人族 lift building
  5. backlog：3 个 flaky cross-test（`test_loads_real_strategies` /
     `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`，
     full suite 偶发 fail、单跑永远 PASS）
- **e2e 待做**（需 SC2 客户端）：三族各跑 1 局 vs VeryEasy smoke。

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`（非默认路径）。`SC2PATH` 环境变量已永久设好（user-level）。
- 地图 `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位。
- `.venv` = Python 3.11.14（**不能用 3.12**，sc2-helper 无 cp312 wheel）。
- ⚠️ `uv sync` / `uv run` 不带 `--extra sc2` 会**卸载** ares —— 跑 pytest / smoke
  用 `uv run --no-sync`。
- `.venv/.../ares_sc2_src.pth` 修 ares src-layout 打包 bug；重建 venv 后需重新创建
  （见 `docs/m0-smoke-runbook.md` §1.3）。
- 用户 GitHub `catmaniii`，gh CLI 已认证，remote `origin = github.com/catmaniii/vibecraft`。
- 开发用固定 token：`vibecraft-dev`（启 server 用 `.\scripts\start.ps1 -Token vibecraft-dev`）。
- Windows + retail SC2 不能真 headless（D3D9 device Lost）；接受 SC2 窗口可见。

---

## Roadmap（产品演进）

| 版本 | 内容 |
|---|---|
| MVP (v0.1) | 神族 3 剧本 vs 内置 AI |
| v0.5 | 神族 8+ 剧本 + Web Inspector |
| v1.0 | 神族完整 + 两笔电 PvP + 本地 LLM fallback |
| v1.5 | 加虫族 / 人族 |
| v2.0 | `compile_strategy` 玩家口述生成新剧本 |

版本号 ↔ 里程碑映射详见 `CHANGELOG.md`。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft` → `VoiceCraft` → 2026-05-16 改 `VibeCraft`
  （不再绑死语音输入）。备选 `Adjutant` 被用户否决（太 geek）。
- ares-sc2 → sharpy-sc2 全框架迁移（2026-05-16，ADR 0009）：vibecraft 4 剧本在 ares
  几乎无对应 Manager，sharpy dummy 直接覆盖其中 3 个。
- M2 four-layer 指令架构（P0-P6）已全部完成，详见 `docs/adr/0010-four-layer-commands.md`
  + `git log`。
- SC2 卡死检测 W3 简化版已实施（子进程内 `HangWatchdog`，bot.time 30s 不前进则
  kill SC2）；W2 PWA banner / 父进程兜底留到玩家在驾驶舱的阶段。
