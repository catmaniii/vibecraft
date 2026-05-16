# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-vibecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-17，HEAD `68f1ec5`，tag `v0.1.0a3`）

- **里程碑**：M1 出口已 verify + `v0.1.0a3` tag 已 push。M2（four-layer 实施）待开始，
  P0 ADR skeleton 已写，P1-P6 待跑。
- **本次 session 关键节点**：
  - **M1 端到端真实 SC2 verify**（M1.6 切剧本 ✅ + M5 字段透传 ✅ + M4 mock ✅，
    M4 e2e 发现 schema gap → 归 P1）
  - voicecraft → vibecraft 全局改名（包路径 + GitHub repo + 文档 + PDF）
  - four-layer 指令架构 plan + ADR 0010 skeleton 写完（P0）
  - 4 个决策拍板（plan §8）：verb 11 个 / override 隐藏 / persistent:bool / ADR 先写
- **最近几个 commit（按时间倒序）**：
  - `68f1ec5` ADR 0010 skeleton：四层指令架构（P0）
  - `3572ff3` four-layer plan §8 4 个决策拍板 + §5 override 语义
  - `8e264ca` CHANGELOG 0.1.0a3 M1 完成 + tag `v0.1.0a3` push
  - `d03654e` iac_2base 叉球一波 数据对齐 spawning tool
  - `1e1dd34` headless_smoke `--initial-opening` + snapshot 解析 + `--fast`
  - `12d88b4` plan：四层指令架构设计
  - `faba795` TASKS.md 刷新
  - `8d46b99` CockpitView 删资源条 + headless_smoke `--inject`
- **GitHub repo**：`catmaniii/vibecraft`，远端跟本地 sync，tag `v0.1.0a3` 在 GitHub release
- **阻塞 / 等待**：无，M2 P1 可开工
- **下一步**（按 ADR 0010 / plan §7 次序）：
  1. **M2 P1**：L3 standing orders 实施（~1d，见下 7 个 sub-task）
  2. **M2 P2**：L4 production overrides（~1d）
  3. **M2 P3**：L2 tactics + `TACTICAL_OBJECTIVE`（~3d）
  4. **M2 P5**：sharpy plan 让位机制泛化（~1d）
  5. **M2 P4**：LLM prompt 重写（~0.5d）
  6. **M2 P6**：收尾 + ADR 补 corner case（~0.5d）
- **Hidden SC2 调研结论（2026-05-16）**：Windows + retail SC2 **不能真 headless**。
  D3D9 在 non-interactive desktop 立刻 Lost；ShowWindow 来不及第一帧前 hide；
  `-windowx -5000` 被 SC2 clamp。Linux native 永久卡 4.10。**项目设计接受 SC2 可见**；
  `headless_smoke.py` 用 `--fast` 跑 ~60s wall-clock + 自动 kill。
- **service 状态**：用户 Ctrl+C 了。重启 `.\scripts\start.ps1 -Token vibecraft-dev`
- **模型**：M2 写代码用 Sonnet，debug 用 Opus
- **环境就绪情况**：
  - `SC2PATH` = `D:\StarCraft II`（user-level 永久）；地图 DaybreakLE 已就位
  - `DEEPSEEK_API_KEY` 已设 user-level 永久。`start.ps1` 会自动从 user 级刷到进程 env
  - `.venv` = Python 3.11.14；sharpy + ares 全家桶在 `sc2` extra。⚠️ `uv sync` /
    `uv run` 不带 `--extra sc2` 会**卸载** ares —— 跑 pytest / smoke 用 `uv run --no-sync`
  - `.venv/.../ares_sc2_src.pth`（内容 `src`）—— 修 ares src-layout 打包 bug；
    `uv sync` 不碰它，但**重建 venv 后需重新创建**（runbook §1.3）

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`（非默认路径），版本 `Base96883`。`SC2PATH`
  环境变量已永久设好（user-level）
- 地图 `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位
- `.venv` = Python 3.11.14（**不能用 3.12**，sc2-helper 无 cp312 wheel）
- 用户 GitHub：`catmaniii`，gh CLI 已认证。
  remote `origin = https://github.com/catmaniii/vibecraft`

---

## 版本号 / 里程碑映射（详 CHANGELOG.md）

| 版本 | 对应里程碑 |
|---|---|
| `0.1.0a1` | M0a + M0b 完成 |
| `0.1.0a2` | M0c 完成 |
| `0.1.0a3` | M1 完成（M1.6 切剧本端到端 verify ✅，已 tag）|
| `0.1.0a4` | M2 完成（four-layer P1-P6）|
| `0.1.0a5` | M3 完成（完整驾驶舱）|
| `0.1.0b1` | M4 完成（LLM 解析 > 90% 正确率）|
| `0.1.0` | M5 MVP RC（vs Hard AI 调优达标）|

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

### M0 / M1 历史 — ✅ done

详 CHANGELOG.md `0.1.0a1` / `0.1.0a2` / `0.1.0a3`。最关键结论：
- Hook C（Unit Role）方案成立：把单位置入 ares `CONTROL_GROUP_ONE` role → 所有
  ares Manager 都 skip 它。sharpy 迁移后用同样机制（M4 `LLM_CONTROLLED` 隔离）。
- M1 端到端 verify（2026-05-17 fast smoke）：force `1g_robo_immortal` → inject
  「切 4BG」→ SNAPSHOT 切 4bg + 两 event；inject「切叉球一波」→ phase_change +
  midgame slot 的 `attack_window` / `micro_doctrine` 完整透传。
- M4 e2e 发现 LLM prompt ↔ Pydantic schema gap（`structure_type` / `selector.count`），
  归入 M2 P1 范围（下面）。

### M2 four-layer 指令架构  🔄 进行中（P0 done，P1 待开）

总览：`docs/plans/2026-05-16-four-layer-commands-design.md` + `docs/adr/0010-four-layer-commands.md`。

#### P0 ADR 0010 skeleton  ✅ done（commit `68f1ec5`）

固化 4 个决策，P1-P6 实施基线。

#### P1 L3 Standing Orders  🔄 待开（~1d）

7 个 sub-task：

- [ ] **P1.1** schema 改 + 修 M4 e2e schema gap（~1h）
  - `directives/models.py`：`UnitClaimPayload` 加 `persistent: bool = False`
  - 修 `target.kind` 接受 `'building_tag'` / `'named_spot'`，去 `selector.count`
  - LLM `prompt.py` 对应例子改用 schema 合法字段
- [ ] **P1.2** Director state（~2h）
  - `self.standing_orders: list[StandingOrder]`
  - `_submit_directives` 按 `persistent` 路由（true 进 standing，false 进 in_flight）
- [ ] **P1.3** Snapshot 加 `standing_orders` 字段 + 单测（~1h）
- [ ] **P1.4** `revoke_directive {id}` 上行帧 + ws handler（~30min）
- [ ] **P1.5** PWA `StandingOrdersCard.vue` + CockpitView 装载（~2h）
  - 替换 `M3Placeholder` "Standing Orders" 占位
  - 每条 standing order + × 撤销按钮
- [ ] **P1.6** e2e smoke verify（~30min）
  - 重跑 inject「那个农民守气矿别动」case，验 schema 不再 fail + 进 standing list
- [ ] **P1.7** 更新 ADR 0010 Implementation Notes corner case（~10min）

#### P2 L4 Production Overrides  ⏸️ blocked by P1（~1d）

state + snapshot + UI

#### P3 L2 Tactics（`TACTICAL_OBJECTIVE` + `ObjectiveExecutor` 框架）  ⏸️（~3d）

11 verb enum：`attack`/`defend`/`scout`/`expand`/`harass`/`drop`/`vision`/`raze`/
`retreat`/`regroup`/`split`

#### P5 sharpy plan 让位机制扩展  ⏸️ blocked by P1+P3（~1d）

`reserved_tags` 通用化：从只 reserve unit tag 扩成 reserve unit selector +
production / build slot。

#### P4 LLM prompt 重写  ⏸️ blocked by P1+P2+P3（~0.5d）

4 层例子 + 分类规则。

#### P6 收尾  ⏸️ blocked by P5+P4（~0.5d）

测试 + headless 验证 + ADR 0010 Implementation Notes 补 corner case。

### M3 / M4 / M5

设计文档 §13 已有粗轮廓，到时候再展开拆。M3 完整驾驶舱（剩余 M3Placeholder /
phase stepper 精确进度 / 撤销机制）部分被 M2 P1/P2 接管。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft`，后改 `VoiceCraft`，2026-05-16 又改
  `VibeCraft`（因为不再绑死语音输入）。备选 `Adjutant` 被用户否决（太 geek）。
- ares-sc2 → sharpy-sc2 全框架迁移（2026-05-16，ADR 0009）。原因：vibecraft 4 剧本
  在 ares 框架几乎无对应 Manager；sharpy dummy 直接覆盖 4 剧本中的 3 个。
- Windows + retail SC2 不能真 headless（2026-05-16 调研）；接受 SC2 可见，设计本来
  就是 PC 当显示器。
