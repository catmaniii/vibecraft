# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-voicecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-14）

- **里程碑**：M0c ✅ 完成并已 commit；**M1 已拆解，待开工**
- **HEAD**：`d917c30`（smoke_test.py `--realtime` 开关）
  - 上游：`03fb12f` M0c 完成（smoke 3 处校准 + `.python-version` + 文档）
  - 本地领先 origin 若干 commit，**未 push 未 tag**（按用户选择）
- **设计文档**：§3.3 / §9 已更新 —— 部署形态补「两阶段启动 + UI 拉起 SC2
  客户端 + 三段式连接状态链」，是 M1 拆解的依据
- **下一步**：开 M1，从 **M1.1 bot service 骨架**起；建议切 Sonnet 起 session
  （模型选择规则：写代码用 Sonnet）
- **环境就绪情况**（M1 直接复用，不用重来）：
  - `SC2PATH` = `D:\StarCraft II`（user-level 永久已设）
  - 地图：`D:\StarCraft II\Maps\DaybreakLE.SC2Map`（从 Battle.net Cache 提取）
  - `.venv` = Python 3.11.14；ares-sc2 3.7.2 / burnysc2 7.1.0 / sc2-helper 0.2.1 已装
  - `.venv/Lib/site-packages/ares_sc2_src.pth`（内容 `src`）—— 修 ares 打包问题，
    **重建 venv 后需重新创建**（详见 runbook §4）

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
| `0.1.0a3` | M1 完成 |
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

### M1 端到端骨架  ⬜ 未开始

把「无 SC2 模块」（M0b）和「真实 SC2 接管」（M0c 验证）接通成第一条端到端
链路。出口：**手机说一句话 → SC2 里 bot 真的切到对应 build**。完成 → `0.1.0a3`。

拆解依据：设计文档 §3.3「启动时序（两阶段）」+ §9（`start_game` / `game_status`
帧 + 三段式状态链）。按依赖排序：

**M1.1 bot service 骨架**
- [ ] HTTP server（serve PWA 静态资源）+ WS endpoint（`websockets`，listen
      `0.0.0.0:<port>`，**不硬编码 localhost**）
- [ ] room_token 生成 + 验证；一 token 一活跃连接（重连顶旧）
- [ ] PC 端二维码显示（弹窗 / 极简本地页）+ IP:port 明文
- [ ] 一键启动脚本（`.ps1`）：双击起 bot service

**M1.2 SC2 子进程生命周期管理**（依赖 M1.1）
- [ ] 收 `start_game` 帧 → 独立进程 / 线程调 `run_game()` 拉 SC2，WS 主循环不阻塞
- [ ] 检测启动阶段（launching / in_game / playing）/ 崩溃 / 结束
- [ ] `game_status` 帧上行（三段式状态链）

**M1.3 PWA 最小壳**（依赖 M1.1）
- [ ] Vue 3 + Tailwind 脚手架，扫码连 WS
- [ ] 三段式系统状态链 UI（手机 → 服务端 → SC2 → Bot）
- [ ] 「开始对局」按钮 → `start_game` 帧
- [ ] 录音 / 文本输入 → `command` 帧

**M1.4 IntentParser 接真实 anthropic**（可与 M1.1-1.3 并行）
- [ ] `AnthropicProvider` 接真实 API（secret 走环境变量 / config，不进 git）
- [ ] 单条话语 → directives（M0b 已有 parser 逻辑，这里接真 LLM）
- [ ] LLM 调用全量 JSONL 日志（设计文档 §11.4）

**M1.5 第 1 个剧本接通 set_build**（依赖 M0c 环境，已就绪）
- [ ] `ares_adapter` 真实实现 `set_build`（M0c 只验证了 role 隔离，set_build
      还是骨架）
- [ ] `1g_robo_immortal.yaml` → `StrategyLibrary.get()` → `Director` →
      `Facade.set_build()` → ares Build Runner

**M1.6 端到端串通 + 验证**（依赖全部）
- [ ] 完整链路跑通：手机说话 → `command` → IntentParser → Directive → Board →
      Director → Facade → ares → SC2 切 build
- [ ] 出口验证：手机说「切 1门Robo」→ SC2 里 bot 真的切到 `1g_robo_immortal`

### M2-M5

设计文档 §13 已有粗轮廓，到时候再展开拆。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft`，已废，与 GitHub repo 一致用
  `voicecraft`。备选 `Adjutant` 被用户否决（太 geek）。
