# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-voicecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-14）

- **里程碑**：M0c 端到端 smoke（等待用户在 SC2 客户端跑 `scripts/smoke_test.py`）
- **HEAD**：`fcb14aa` CLAUDE.md：加会话交接协议 + 标记当前 HANDOFF 块为临时
- **阻塞**：用户环境里 ladder 1v1 地图未下载到 `Documents\StarCraft II\Maps\`；
  smoke 必须先放地图才能跑
- **下一步**：用户跑 smoke → 看 `smoke_report.json` 的 `verdict`
  - `pass` → 打 tag `v0.1.0a2`，开 M1
  - `fail` → 看 `anomalies_by_kind` 决定回退方案（详见
    `docs/m0-smoke-runbook.md` §3 异常表）

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`，最新版本 `Base94137`。**必须设环境变量**
  `SC2PATH=D:\StarCraft II`，python-sc2 默认只找 `C:\Program Files` 路径
- 用户的 `Documents\StarCraft II\Maps\` 和 `D:\StarCraft II\Maps\` 都不存在，
  smoke 之前必须下 1v1 ladder 地图（runbook §0 写了具体步骤）
- 用户已装 uv + Python 3.11；本地仓库目录是 `voicecraft`（旧名 `voice_craft`
  已废弃；`.venv` 改名后需重建：`uv sync --extra dev`）
- 用户 GitHub：`catmaniii`，gh CLI 已认证（HTTPS + keyring token），
  remote `origin = https://github.com/catmaniii/voicecraft`

---

## 版本号 / 里程碑映射（见 CHANGELOG.md）

| 版本 | 对应里程碑 |
|---|---|
| `0.1.0a1` | 当前 HEAD（M0b 完成） |
| `0.1.0a2` | M0c smoke 通过后打 tag |
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

### M0c 端到端 smoke  🟡 in progress

用户启 SC2，验证"不动的叉子"。出口：4 个 ares Manager 都 skip
`LLM_CONTROLLED`，`smoke_report.json` `verdict=pass`。

待办：

- [ ] **修 runbook 里的旧路径**：`docs/m0-smoke-runbook.md` 第 33 行写的是
      `cd D:\code\claudecode\voice_craft`，应改成 `voicecraft`
- [ ] 在 runbook 里**显式记一段** `SC2PATH` 设置 + Maps 目录初始化（用户实际
      场景下 SC2 装在非默认路径，目录也不存在）
- [ ] 用户下 ladder 地图到 `Documents\StarCraft II\Maps\`
- [ ] 用户在 PowerShell 跑 `uv run python scripts/smoke_test.py`
- [ ] 看 `logs/<game_id>/smoke_report.json`
  - pass → 打 tag `v0.1.0a2`，更新 CHANGELOG，开 M1
  - fail → 按 runbook §3 异常表分类，看是单 Manager 不 respect role
    （加 wrap）还是全 fail（回退 Hook B OverrideMediator 方案）

### M1 端到端骨架  ⬜ 未开始

Bot service + WS endpoint + PWA 框架 + 1 剧本 + LLM 单条话语解析。
出口：手机说话 → bot 切 1门Robo build。

预拆解（待 M0c 通过后展开）：

- [ ] WS server 起骨架（`websockets`，listen 0.0.0.0:8765）
- [ ] PWA 最小壳（Vue 3 + Tailwind + 二维码扫描 + 麦克风按住说）
- [ ] `IntentParser` 接 anthropic provider 真跑（带 secret 注入）
- [ ] 第 1 个剧本 `1g_robo_immortal.yaml` 接通 `set_build`
- [ ] 端到端：手机录音 → 文字 → directive → board → facade → SC2 切 build

### M2-M5

设计文档 §13 已有粗轮廓，到时候再展开拆。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft`，已废，与 GitHub repo 一致用
  `voicecraft`。备选 `Adjutant` 被用户否决（太 geek）。
