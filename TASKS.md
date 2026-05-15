# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-voicecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-16，sharpy 迁移 M4+M5+M6 完成）

- **里程碑**：M1 代码层完成 + **sharpy 迁移 M4/M5/M6 完成（worktree，待合并）**
  auto-pilot + cockpit-sync + minimap 拖拽视野均已实现，PWA 驾驶舱架子按 §9.5 重排完成。
- **本次 session（2026-05-16）做了什么** —— sharpy 迁移 M4+M5+M6，在 worktree
  `D:\code\claudecode\voicecraft\.claude\worktrees\agent-a3d18f8aa016633f0` 实现：
  - **M4（LLM_CONTROLLED role 隔离）**：`_VoiceCraftProtossBot` 新增
    `_llm_controlled_tags: set[int]`；`_SharpyFacade.set_unit_role()` 在设
    `LLM_CONTROLLED` 时写入集合、设其他 role 时从集合移除；新增
    `_refresh_llm_controlled_roles()` 每 step 对集合中每个 unit 重新声明
    `UnitTask.Reserved`（防 `UnitRoleManager.update()` 每步 clear `had_task_set`）；
    新增 `is_voicecraft_controlled(unit) -> bool`；`on_unit_destroyed` 清理死亡 tag
  - **M5（attack_window / micro_doctrine 字段透传）**：`director.build_snapshot`
    `_slot_view()` 扩展 `MidgameStance.attack_window` / `MidgameStance.micro_doctrine` /
    `LategameDoctrine.engagement_doctrine`（as micro_doctrine）→ snapshot；
    `web/src/types.ts` `StrategySlotView` 加两个可选字段；`StrategyCard.vue` 显示
    「出门 9:30–11:30」和 doctrine 子弹点
  - **M6（文档）**：ADR 0009 `docs/adr/0009-sharpy-migration.md`；
    `ARCHITECTURE.md` 更新（模块图/不变量/hook 表/vendor 节）
  - 新增 13 个单测（M4: 9 个 `TestLLMControlledTags`；M5: 4 个 cockpit-sync 快照测试）
- **验证（无 SC2，mock）**：`uv run --no-sync pytest` **389 passed, 6 skipped**（+13）；
  前端 vitest **41 passed**；ruff + mypy strict 全干净；Vite build 通过
- **阻塞 / 等待**：worktree 变更待用户合并（`git merge` 或 cherry-pick）。
  合并后无需 SC2 即可完整验证；sharpy 真实 hook 验证需真实 SC2 对局
- **下一步（合并后）**：
  1. `git merge` 或 cherry-pick worktree → main
  2. 真实 SC2 验证：sharpy Reserved 隔离 + attack_window / micro_doctrine PWA 显示
  3. M2：midgame/lategame 剧本自动转（auto-pilot 按剧本切）
- **已知未做（非 bug，是 M2/M3 范围）**：
  - 完整驾驶舱：资源条/SO 区/快捷栏内容（3 个 `M3Placeholder` 已挂位）
    —— **M3**。phase stepper 精确进度、撤销机制 —— **M3**
  - 「按 midgame/lategame 剧本自动转」—— **M2**（当前 auto-pilot 只是通用兜底）
  - 造建筑指令（「造水晶和BG」）—— directive schema 没这个类型，**M2**
- **service 状态**：停掉了旧 service。用户直接 `.\scripts\start.ps1` 即可
- **模型**：真实验证若需 debug 用 Opus；M2 起写代码用 Sonnet
- **环境就绪情况**：
  - `SC2PATH` = `D:\StarCraft II`（user-level 永久）；地图
    `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位
  - `DEEPSEEK_API_KEY` 已设 user-level 永久。`start.ps1` 会自动从 user 级刷到
    进程 env（已运行的 shell 不自动刷新环境块）
  - `.venv` = Python 3.11.14；ares 全家桶在 `sc2` extra。⚠️ `uv sync` / `uv run`
    不带 `--extra sc2` 会**卸载** ares —— 跑 pytest / smoke 用 `uv run --no-sync`
  - `.venv/.../ares_sc2_src.pth`（内容 `src`）—— 修 ares src-layout 打包 bug；
    `uv sync` 不碰它，但**重建 venv 后需重新创建**（runbook §1.3）

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`（非默认路径），版本 `Base96883`。`SC2PATH`
  环境变量已永久设好（user-level）
- 地图 `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位（M0c 从 Battle.net
  Cache 提取的 `(2)DaybreakLE`）。Maps 目录原本不存在，已建
- `.venv` = Python 3.11.14（**不能用 3.12**，sc2-helper 无 cp312 wheel）；
  ares 全家桶已装。重建 venv 的完整流程见 runbook §1
- 用户已装 uv；本地仓库目录是 `voicecraft`（旧名 `voice_craft` 已废弃）
- 用户 GitHub：`catmaniii`，gh CLI 已认证（HTTPS + keyring token），
  remote `origin = https://github.com/catmaniii/voicecraft`

---

## 版本号 / 里程碑映射（见 CHANGELOG.md）

| 版本 | 对应里程碑 |
|---|---|
| `0.1.0a1` | M0b 完成 |
| `0.1.0a2` | M0c 完成（已 commit `03fb12f`，**未打 tag** —— 按用户选择）|
| `0.1.0a3` | M1 完成（代码层 ✅；待真实 SC2 出口验证 + 打 tag）|
| `0.1.0` | M5 MVP RC |

---

## Roadmap（产品演进）

| 版本 | 内容 |
|---|---|
| **MVP (v0.1)** | 神族 3 剧本 vs 内置 AI |
| v0.5 | 神族 8+ 剧本 + Web Inspector |
| v1.0 | 神族完整 + 两笔电 PvP + 本地 LLM fallback |
| v1.5 | 加虫族 / 人族 |
| v2.0 | `compile_strategy` 玩家口述生成新剧本 |

---

## 里程碑拆解

### M0a 脚手架  ✅ done

pyproject / 目录 / lint / mypy / pytest / pre-commit / CI 模板。出口：
`pytest` 跑空通过。

### M0b smoke 代码  ✅ done

最小 VoiceCraftBot + Unit Role 排除 demo + mock 单测（126 个用例全过）+
一份给用户跑的 SC2 启动脚本。出口：mock 验证 role 隔离。

完成于 `44159e1`，后续 `47d2b1e` 修正 `LLM_CONTROLLED` 映射到 ares 的
`CONTROL_GROUP_ONE`（ares `UnitRole` 是固定 StrEnum 加不了成员，必须复用
留给用户的空槽）。

### M0c 端到端 smoke  ✅ done

真实 SC2 验证「不动的叉子」—— role 隔离机制成立，设计文档 §3.4「唯一存疑点」
核心假设确认。

- [x] runbook §0 重写（`SC2PATH` / Maps 目录 / 地图来源）
- [x] 环境搭建：`.venv` 重建为 Python 3.11、装 ares-sc2 全家桶、修 src-layout
      打包问题、装 sc2-helper
- [x] 地图：从 Battle.net Cache 提取 `(2)DaybreakLE` archive
- [x] `scripts/smoke_test.py` 端到端校准：`maps.get()` 传 Map 对象、enroll 后
      `unit.stop()` 清开局采矿 order
- [x] smoke 通过：`verdict=pass`，anomalies=0

**端到端结论**：把单位置入 ares 的 `CONTROL_GROUP_ONE` role 后，所有 ares
Manager 都 skip 它（`role_changed_away`=0，`in_role` 全程 true）。enroll 后
`stop()` 清掉 SC2 开局默认采矿 order，探机即保持完全 idle。Hook C (Unit Role)
方案成立，不需要回退到 Hook B OverrideMediator wrap。

### M1 端到端骨架  ✅ 代码层完成（M1.1-M1.6 ✅；出口验证待真实 SC2）

把「无 SC2 模块」（M0b）和「真实 SC2 接管」（M0c 验证）接通成第一条端到端
链路。出口：**手机说一句话 → SC2 里 bot 真的切到对应 build**。完成 → `0.1.0a3`。

拆解依据：设计文档 §3.3「启动时序（两阶段）」+ §9（`start_game` / `game_status`
帧 + 三段式状态链）。按依赖排序：

**M1.1 bot service 骨架** ✅ 全完成
- [x] `src/voicecraft/server/` 包骨架 + `tokens.py`：`room_token` 生成 / 验证 /
      `RoomRegistry` 单活跃连接顶旧（10 单测，ruff + mypy 干净）
- [x] WS endpoint（`websockets`，listen `0.0.0.0:<port>`，**不硬编码 localhost**）
      `server/ws.py`：token 验证握手、帧收发循环（stub）、5s 心跳、重连顶旧
- [x] HTTP server（serve PWA 静态资源）`server/http.py`：`process_request` 钩子
      与 WS 共端口，SPA fallback，路径遍历防护
- [x] PC 端二维码显示 `server/qr.py`：ASCII 二维码（##/空格，绕 Windows GBK），
      UDP socket 局域网 IP 自动检测
- [x] 一键启动脚本 `scripts/start.ps1` + `voicecraft serve` CLI 子命令；
      `server/service.py` BotService + ServiceConfig

**M1.2 SC2 子进程生命周期管理**（依赖 M1.1）✅ 完成
- [x] 收 `start_game` 帧 → 独立 multiprocessing spawn 子进程调 `run_game()`，WS 主循环不阻塞
- [x] 检测启动阶段（launching / in_game / playing）/ 崩溃 / 结束
- [x] `game_status` 帧下行（三段式状态链：link / sc2 / bot）

**M1.3 PWA 最小壳**（依赖 M1.1）✅ 完成
- [x] Vue 3 + Tailwind + Vite PWA 脚手架（`web/`，build → `server/static/`）
- [x] 扫码连 WS（`useWs.ts`，指数退避 1→2→4→8s 重连）
- [x] 三段式系统状态链 UI（`StatusChain.vue`，全绿折叠 / 异常展开）
- [x] 「开始对局」按钮 → `start_game` 帧；录音（Web Speech API）/ 文本 → `command` 帧
- [x] 15 个 vitest 单测

**M1.4 IntentParser 接真实 anthropic**（可与 M1.1-1.3 并行）✅ 完成
- [x] `AnthropicProvider` 接真实 API（tool_use 强制 JSON、prompt 缓存；
      secret 走 `ANTHROPIC_API_KEY` 环境变量，不进 git）
- [x] 新增 `llm/config.py`（`LLMConfig` + `build_provider()` 工厂）
- [x] LLM 调用全量 JSONL 日志增强（`parser.py` 的 `_log_call`，符合 §11.4）
- [x] 29 个 mock 单测（`test_llm_anthropic.py`）
- [ ] **真实 API 验证待用户做**（需 `ANTHROPIC_API_KEY`；单测全 mock 不真调）

**M1.5 第 1 个剧本接通 set_build**（依赖 M0c 环境，已就绪）✅ 完成
- [x] spike：ares 真实 API —— `BuildOrderRunner.switch_opening(name)`，name 须
      预先在 `bot.config["Builds"]`；step 直接用 `UnitID` 大写名（ADR 0003）
- [x] `bot/build_translator.py`：voicecraft `opening_build` 剧本 → ares builds
      格式的纯函数翻译层（`@chrono` → 独立 CHRONO step、`send_probe` → WORKER_SCOUT）
- [x] `ares_adapter.set_build` 真实现 → `build_order_runner.switch_opening()`；
      `make_bot_class` 加 `strategy_library` 参数，`on_start` 在 `super()` 前注入
      `config["Builds"]`
- [x] 翻译层 + adapter 共 ~50 个单测（纯函数 + mock bot，不拉真 SC2）
- 范围边界：只接 `opening_build` kind；midgame/lategame ares build runner 管不了，
  留 M2+（见 ADR 0003 + 预研文档 §5）

**M1.6 端到端串通 + 验证**（依赖全部）✅ 代码层完成（真实 SC2 验证待用户）
- [x] 完整链路跑通（代码层）：手机说话 → `command` → IntentParser → Directive →
      Board → Director → Facade → ares → SC2 切 build；5 个串通 gap 见 ADR 0004
- [x] 单测：`test_m1_6_end_to_end.py`（21 用例，mock down_q / mock director / 子进程装配）
- [ ] 出口验证：手机说「切1门Robo」→ SC2 里 bot 真的切到 `1g_robo_immortal`
      —— **需真实 SC2 + `ANTHROPIC_API_KEY` + 用户在场**（同时验掉 M1.4 真实 API）

### M2-M5

设计文档 §13 已有粗轮廓，到时候再展开拆。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft`，已废，与 GitHub repo 一致用
  `voicecraft`。备选 `Adjutant` 被用户否决（太 geek）。
