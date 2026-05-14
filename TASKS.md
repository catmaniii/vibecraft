# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-voicecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-14）

- **里程碑**：M0c **已通过** ✅ —— 真实 SC2 smoke「不动的叉子」验证成立
  （`logs/game_20260514_070745_c5332c/smoke_report.json` verdict=pass，
  anomalies=0，2 探机 60s 零指令零移动，`Idle worker time: 168.0`）
- **HEAD**：`3af28e0`（M0c 的代码/文档改动待 commit）
- **待提交**：`scripts/smoke_test.py`（3 处端到端修复）、`.python-version`、
  `CHANGELOG.md`、`docs/m0-smoke-runbook.md`、`TASKS.md`
- **待办**：commit M0c 改动 → 打 tag `v0.1.0a2` → 开 M1
- **环境就绪情况**（M1 直接复用，不用重来）：
  - `SC2PATH` = `D:\StarCraft II`（user-level 永久已设）
  - 地图：`D:\StarCraft II\Maps\DaybreakLE.SC2Map`（从 Battle.net Cache 提取）
  - `.venv` = Python 3.11.14；ares-sc2 3.7.2 / burnysc2 7.1.0 / sc2-helper 0.2.1 已装
  - `.venv/Lib/site-packages/ares_sc2_src.pth`（内容 `src`）—— 修 ares 打包问题，
    **重建 venv 后需重新创建**（详见 runbook §4）

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
