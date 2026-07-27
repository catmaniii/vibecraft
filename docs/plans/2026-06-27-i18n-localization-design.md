# i18n 本地化（中/英）+ 开源前剩余任务 — 设计与任务拆解

> 起草 2026-06-27。状态：**待用户拍板关键决策 + 独立 opus 评审后再实现**。
> 用户要求：设计清楚 → 之后由 Claude 自己实现（翻译 + 适配 + headless 手机分辨率浏览器逐页可视化测试）。

---

## 1. 需求（用户原话提炼）

开源前新增"换语言本地化"功能：
- 游戏里**所有面向用户的字符串**（含 `{n} 个实例` 这类 **format 模板字符串**）都要可本地化。
- 先支持**中文 + 英文**，架构要能轻松扩展第 N 种语言（换一套配置文件即可）。
- 字符串用 **字符串 ID** 管理：一个 ID → 各语言文本，按语言分文件（CSV / 数据库 等易编辑格式）。
- 覆盖面：**所有与"语言/显示"相关的**——UI 文案、服务端发给手机的用户可见消息、**语音识别**
  （中/英都要支持好，模型预热要考虑）、**语音/文字指令输入**。
- 实现后要用 **headless 浏览器模拟手机分辨率**，逐页检查中/英两套下**可视化有没有问题**
  （中英文长度差异大，界面风格会变）。

---

## 2. 关键认知：本地化分三层，难度递增

| 层 | 范围 | 难度 | 能否独立交付 |
|---|---|---|---|
| **A. UI 显示** | PWA 里所有硬编码文案（按钮/标题/状态/toast/弹窗/提示） | 中 | ✅ 可先交付 |
| **B. 服务端用户可见消息** | Python 发给手机的：指令解析反馈、错误（"解析失败"/"非本族单位"）、澄清提示、决策理由 | 中 | ✅ 跟 A 一起 |
| **C. 英文"指令输入"** | 玩家**说/打英文指令**也能懂：英文 ASR 模型 + 英文别名表 + LLM few-shot/catalog 英文化 | **高** | ⚠️ 独立 epic，建议后置 |

**Layer C 是真正的大头**：整条指令解析链路（`docs/aliases/*.yaml`、few_shot、build catalog、跨族校验）
现在是**中文术语为中心**的。让英文指令也能解析 = 要补英文别名（Stalker/Immortal…）、英文 few-shot、
LLM 按输入语言走对应 prompt。这块工作量 ≈ 甚至 > A+B 的 UI i18n。

**最终范围（2026-06-27 用户拍板）= 全量一起做**：A + B + C + ASR 中英，**不分先后、一批交付**。
用户原话："英文版要换，那肯定是指令和语音识别和各种显示文本全部要一批一起改，怎么可能只改一部分。"
分层表仍用于**实现时的依赖排序**（A/B 可先并行起步，C 与之并行推进），但**验收以"全英文可玩"为准**——
玩家切英文后：界面全英文、能说/打英文指令并被正确执行、英文语音能识别。

---

## 3. 设计

### 3.1 字符串目录：CSV 单一真理源 → 生成各端运行时文件

**决策点①（见 §5）。推荐方案：**

- **唯一可编辑源**：`locales/strings.csv`，列 = `id, context, zh, en`（将来加语言 = 加一列）。
  - `id`：点分命名空间，如 `lobby.ready`, `cmd.parse_failed`, `live.rally_set`。
  - `context`：给翻译者的说明（这串出现在哪、占位符含义）。
  - 模板串用**命名占位符** `{n}` / `{unit}` / `{group}`，各语言占位符名一致。
- **生成产物**（构建脚本 `scripts/gen_locales.py`，类似现有 alias/catalog 动态生成范式）：
  - 前端：`web/src/locales/zh.json` + `en.json`（喂 vue-i18n）。
  - 后端：`src/vibecraft/i18n/zh.json` + `en.json`（喂 Python `t()`）。
- 好处：翻译者只编辑**一个 CSV**（你的要求），运行时用 JSON（工具友好）；加第三语言 = CSV 加列 + 重生成。
- CSV 转义坑（逗号/换行/引号）→ 生成脚本用标准 `csv` 库读、校验"无缺翻译/无重复 id/占位符两语言一致"。

### 3.2 前端机制

- 引入 **vue-i18n**（Vue 3 标准）。`t('lobby.ready')` / `t('starting.count', {n})`。
- locale 状态：reactive + 持久化（`localStorage`，并入 `useProfile`）。默认按浏览器语言猜，可手动切。
- **语言切换 UI**：入口页放一个 中/EN 切换；游戏内设置里也放一个。
- 替换 67 个 web 组件里的硬编码中文为 `t(id)`。

### 3.3 后端机制

- `src/vibecraft/i18n/__init__.py`：`t(key, lang, **params) -> str`，加载生成的 zh/en JSON，缺 key 回退 zh + 告警。
- **每连接语言**：PWA 连接时上报 locale（握手帧 / 一个字段）；server 按 player/session 存；所有发给该手机的
  用户可见串先 `t(..., lang)` 再下发。
- 注意：**LLM-facing 的 prompt/alias/catalog 不属于"显示串"**，不进这套（它们是喂模型的，属于 Layer C）。

### 3.4 语音 / ASR（中英）— 含 2026-06-27 FunASR 调研

**现状**：`paraformer-zh-streaming`（**只中文**），用 **2pass 流式**（边说边出 partial）+ **热词**
（SC2 术语 地堡/虚空… 靠 `config/asr_hotwords.txt` 纠偏），`src/vibecraft/server/asr.py`。
本项目对 ASR 的硬需求 = **流式（实时 partial）+ 热词 + 中 & 英**。

**FunASR 多语言能力调研结论**：
- **`paraformer-zh-streaming`**：中文，流式 ✅，热词 ✅（当前在用，体验好，**不要回退**）。
- **`paraformer-en`**：英文，**离线**（未见官方 en **流式**打包模型），同家族、**支持热词**。
- **SenseVoiceSmall**：多语言一个模型覆盖**中/粤/英/日/韩**，比 whisper 快 15x，但**非流式**
  （只能伪流式/分块）、**无 Paraformer 式热词**（对 SC2 黑话纠偏不利）→ 对本项目是减分。
- **Fun-ASR / Fun-ASR-Nano**（2025 新）：31 语言、**流式 + 热词 + 实时**，最贴需求，但**新、需真机验证**
  是否在 funasr pip 包可用、延迟/准确度/集成是否顺。

**方案（决策③已定方向，细节待 spike）**：**按会话语言路由不同引擎**——
- **中文**：保持 `paraformer-zh-streaming`（不回退主力体验）。
- **英文**：**spike 评估** 三候选并真机对比（延迟/准确度/对 SC2 英文术语 Stalker/Immortal… 的热词纠偏）：
  ① `paraformer-en`（离线，同家族有热词；命令是短句，离线"松手即转写"可接受）
  ② SenseVoice（多语言但无热词/非流式）
  ③ Fun-ASR-Nano（流式+热词+多语，若验证通过可统一替换中英两端）。
- **预热**：启动只 warm 默认语言模型；其余按会话语言**懒加载 + 加载后缓存**。
- **离线 vs 流式影响前端 UX**：若英文走离线，前端语音条要支持"松手→转写"模式（中文保留实时 partial）。
- 与 Layer C 联动：ASR 出英文文本后，必须有英文指令理解（英文别名+few-shot）才闭环，否则只是"听写出英文却听不懂"。

### 3.5 可视化测试（headless 手机分辨率）

- **Playwright**（headless Chromium）：设手机视口（iPhone 390×844 + Android 360×800 两档）。
- 用 **mock WS 状态**把每个页面/组件/弹窗/toast 渲染出来 → zh + en 各截图 → Read 图判读
  **溢出/截断/换行/错位**（英文普遍更长，按钮/标签/状态链最易爆）。
- 待枚举的"页面/态"清单（逐个测）：
  入口页、大厅（含起局遮罩/错误/各 slot 态）、驾驶舱 LiveView 及其面板
  （宏观策略 / 战术 / 指令卡 / 状态链 / 语音条 / 小地图 / 科技面板 / 编队条 / 决策流 / 各 toast）、
  弹窗（反馈 / 二维码 / 澄清）。
- 发现问题 → 改样式（响应式/缩字/截断省略/换行）→ 重截重判，迭代到两语言都干净。

---

## 4. 任务表

### A. 开源前剩余（本 session 已完成大半，列剩余）

| # | 任务 | 谁 | 状态 |
|---|---|---|---|
| O1 | openVibeCraft 转 public | 用户点 | 待办（满意后） |
| O2 | 轮换 VPS root 密码 + TURN secret（IP 进过私有仓历史，纵深防御） | 用户(我给步骤) | 待办 |
| O3 | `docs/QUICK_START.md`（5 分钟跑通教程） | Claude | 待办 |
| O4 | THIRD_PARTY_NOTICES 补全（modelscope/torch 行；sc2pathlib 补 LICENSE；评估 `vendor/sharpy/libs/ic52.zip` 来源/是否需要） | Claude | 待办 |
| O5 | README/USER_GUIDE 加一句"ASR 模型运行时从 ModelScope 下载，受其模型许可约束" | Claude | 待办 |
| O6 | vibecraft→openVibeCraft 一键脱敏同步脚本 | Claude | 待办 |
| ✅ | 脱敏 deploy/机密入 .env / 删内部文档 / 全新历史 / CONTRIBUTING / README 前置清单 / 暴雪提示 / package.json license | Claude | **本 session 已完成** |

### B. i18n 本地化（新需求）

| # | 任务 | 依赖 | 备注 |
|---|---|---|---|
| **P0** | 拍板 §5 三个决策（格式 / 英文范围 / ASR 模型） | — | **先做，阻塞后续** |
| **P0'** | 设计独立 opus 评审（项目规则）→ 改稿 | P0 | 实现前必走 |
| **P1.1** | 全量提取硬编码用户串（前端 Vue + 后端 Python），出清单 | P0 | 估计前端数百条 |
| **P1.2** | 定 `locales/strings.csv` schema + `scripts/gen_locales.py`（→前后端 JSON）+ 校验（缺译/重复id/占位符一致） | P1.1 | |
| **P1.3** | 字符串 ID 命名规范 | P1.1 | |
| **P2.1** | 接入 vue-i18n + locale 持久化(useProfile) + 默认语言探测 | P1.2 | |
| **P2.2** | 前端所有硬编码串替换为 `t(id[,params])` | P2.1 | 量最大 |
| **P2.3** | 语言切换 UI（入口页 + 游戏内设置） | P2.1 | |
| **P2.4** | 英文翻译填充（含模板串） | P1.2 | |
| **P3.1** | 后端 `t(key,lang,**params)` 加载器 | P1.2 | |
| **P3.2** | 每连接语言上报 + 存储（握手帧加 locale 字段） | P3.1 | |
| **P3.3** | 服务端所有用户可见消息本地化（解析反馈/错误/澄清/理由） | P3.1,P3.2 | |
| **P4.0** | **ASR 英文模型 spike**：真机对比 `paraformer-en` / SenseVoice / Fun-ASR-Nano（延迟/准确/SC2 英文热词）→ 定型 | P0 | 决策依据，先做 |
| **P4.1** | 接入选定英文 ASR 引擎 | P4.0 | |
| **P4.2** | 按会话语言路由 ASR + 预热（默认warm+懒加载缓存）；前端语音条支持离线"松手即转写"模式（若英文离线） | P4.1 | |
| **P4.3** | **Layer C 英文指令理解（在范围内，与 UI 一起交付）**：英文别名表（`docs/aliases/*_en.yaml` 或加列）+ 英文 few-shot + 英文 rules + LLM 按玩家语言选 prompt + build catalog 英文显示名 | P4.0 | 大头，与 P2/P3 并行推进 |
| **P4.4** | 英文指令解析真 LLM 验证（仿 voice_spot_check 出英文用例集） | P4.3 | |
| **P6** | 文档：README/USER_GUIDE/CONTRIBUTING 加 i18n 说明 + "如何加一种语言"；英文 USER_GUIDE 话术示例 | P2,P3,P4 | |

### C. 可视化 / 验证（贯穿 B，实现后必做）

| # | 任务 | 依赖 |
|---|---|---|
| **T5.1** | Playwright headless 手机视口测试 harness（mock WS 渲染各页/态） | P2.2 |
| **T5.2** | 枚举所有页面/弹窗/toast，zh+en 各截图，判读溢出/截断/错位 | T5.1,P2.4 |
| **T5.3** | 修布局问题（响应式/缩字/省略/换行）迭代到两语言都干净 | T5.2 |
| **T5.4** | ASR 中英各跑一遍真识别 + 预热验证 | P4.2 |

---

## 5. 决策已敲定（2026-06-27 用户）

1. **字符串文件格式 = 单一 `locales/strings.json`（id → {context, zh, en}）**（2026-06-27 评审后修订）。
   用户诉求"打开一个文件并排对照编辑"+ "JSON 也行"。单 JSON 每个 key 下 zh/en 并排，**同样满足对照**，
   且**避免 CSV 在 600+ 串规模上的引号/逗号/换行/模板 `{n}` 转义地狱、git diff 更干净、前后端可直接读、
   省掉 CSV→JSON 生成器**（评审建议 #4）。要表格视图可加个**只读导出**脚本（json→csv）给翻译者看，
   但**真理源是 JSON**。加第 N 种语言 = 每 key 加一个 lang 字段。
2. **英文范围 = 全量一起做**（用户明确："指令 + 语音识别 + 各种显示文本全部一批一起改，不可能只改一部分"）。
   → **Layer A（UI）+ B（服务端消息）+ C（英文指令理解：英文别名/few-shot/LLM）+ ASR 中英** 作为**一个整体**交付，
   **不分先后**。之前"先显示后指令"的分层方案作废。
3. **ASR = 按会话语言路由**（详见 §3.4 + ASR 调研）。中英都要支持好；**其他语言留给社区**（架构可扩展即可）。

> 实现前仍需走**独立 opus 设计评审**（项目规则），评审通过再开工。

---

## 6. 实现纪律（落地时遵循）

- 本设计/方案先过**独立 opus subagent 评审**（架构合理性/风险/YAGNI/与现有约定冲突），再开工（项目规则）。
- 翻译 + 适配 + headless 可视化测试由 Claude 自验（截图判读，无需用户看手机）。
- 任何"显示串"硬编码 = 反模式；新功能落地即走 `t()`。
- 机密/配置仍走 gitignore + .example（与开源脱敏一致）。

---

## 8. 独立 Opus 评审意见处理（2026-06-27）

评审产出已逐条核实代码事实并处理。结论：**方向/范围对，但当前文档不能直接开工——必须先补"现状设施盘点"
和"locale 三类串三条路径 + 穿透子进程"两节，否则撞返工**。处理如下：

### 8.1 采纳（改设计）

- **【已存在两套 i18n 设施，必须先盘点再决定扩展 vs 重建】**（评审 #1，已核实）：
  - 前端 `web/src/i18n.ts`（52 行，自研 `t(key)`，`Locale='zh'|'en'`，36 key，**4 组件已用**：
    CommandCard/CommandHistoryItem/TechProgressPanel/TechRows）。
  - 后端 `src/vibecraft/bot/localization.py`（`Localizer`，UNIT/UPGRADE/PRODUCTION/TECH/VERB 名称表，
    **已在 `director.py:313` 实例化、:939 调用**）。`en` 表空时 fallback 到 canonical id，而 canonical
    **就是 SC2 官方英文名**（Zealot→Zealot）→ **英文单位/建筑名基本免费**。
  - **决定**：① 前端 **vue-i18n 取代 i18n.ts**（迁移 4 组件 + 36 key 进 `strings.json`，自研 t 退役）。
    ② 后端 **`Localizer` 保留并扩充**（管"id→专有名词"），新 `t(key,lang)` 管"key→句子模板"，
    **两者职责不同、共存**：句子里嵌专有名词时 `t()` 调 `Localizer`。设计补"现状盘点 + 收敛"小节。
- **【locale 三类串走三条路径 + 必须穿透子进程】**（评审 #2/#3，核心漏洞，已核实）：
  - (a) **静态 UI 串** → 前端 vue-i18n（`strings.json` 的前端子集）。
  - (b) **后端代码硬编码串** → `t(key,lang)` 在 ws 序列化层。需审计：`auto_combat/common.py:70-74`
    (`[模糊]/[解析失败]`)、`director.py:3192/3197/3379/3392`(代码生成的中文 interpretation)、
    `ws.py`(`操作失败/启动失败` room_error)。
  - (c) **LLM 输出串**（`interpretation` / `ClarificationRequest.question/label`）→ **不能在下发前 t()**
    （无 key），必须在 **parse 那一刻于子进程 director/IntentParser 内按玩家 locale 生成**。
  - **决定**：locale 必须从 PWA → ws → **GameProcess.config → director → IntentParser** 穿透下去；
    工具 schema 字段 `interpretation_zh`（`prompt.py:291`/`schema.py:25`）改语言中性 `interpretation`
    或按 locale 切。设计补"locale 数据流图"。**这是最深、最易被't() before send'错误心智坑到的点。**
- **【格式改单 JSON】**（评审 #4）：已采纳，见 §5 决策①修订。
- **【ASR 路由 infra 依赖 spike，可能取消】**（评审 #5）：P4.2 标注"依赖 P4.0：若 Fun-ASR-Nano 单模型
  中英流式+热词通过，则**无需双引擎路由层**"。不在 spike 出结果前建路由。
- **【前端离线 ASR 的 UX 是新状态分支，单列任务】**（评审 #6）：`VoiceInput.vue` 状态机围绕流式 partial
  建（L255-260）；英文若离线，"松手即转写"是新分支，单列 T 任务，不埋在 P4.2。

### 8.2 采纳（补遗漏任务，并入任务表）

- **P3.0 locale 穿透子进程**：PWA→ws(WsConnection 加 locale 字段)→GameProcess.config→director→IntentParser。
- **P4.5 strategy 双语**：`models.py` 4 模型加 `display_name_en/summary_en`；**46 个 yaml**(P17/T15/Z14)补英文；
  按 CLAUDE.md 铁律**同步改 `test_<race>_strategies.py` 计数/id + 重启 server 刷面板 + 重 dump prompt**（量大）。
- **P4.6 catalog 双语**：`build_strategy_catalog`(`prompt.py:166`)按玩家 locale 出英文（喂 LLM 的目录也要英文，
  否则英文 build 名 LLM 对不上）。
- **P4.7 英文热词链路**：`config/asr_hotwords.txt` 由 `gen_asr_hotwords.py` 从中文别名生成；英文 ASR 需
  **独立英文热词文件 + 英文别名数据源**，生成脚本出双语。
- **决策④ 专有名词英文策略**：建筑显示当前用中文 hotkey（Localizer `GATEWAY→BG`）。英文下 **hotkey 保留**
  （键位与语言无关，老玩家通用）；单位用官方英文名。**待用户确认**（倾向保留 hotkey）。
- **T5.0 Playwright 是全新前端依赖**（package.json 无），mock WS 渲染 67 组件+弹窗+toast 的 fixture 是真活，单列。
- **P4.4 / 测试**：`voice_spot_check.py` 出英文用例集；文档写清哪些 e2e 双语跑；USER_GUIDE 英文话术要**真实英文玩家话语**（非机翻）。

### 8.3 采纳（YAGNI 砍掉）

- **不做** date/number/RTL/字体本地化：游戏时间 `mm:ss` 自格式化(locale 无关)、资源纯数字、中英都 LTR、
  字体浏览器自带。明确写"不做"，不为"将来"预留 Intl/RTL 抽象。
- **砍掉 CSV→JSON 生成器**（改单 JSON 后不需要）。
- **不抽象** locale negotiation / plural 引擎（vue-i18n 自带 plural 够用）。

### 8.4 开工前必做的两个 Spike（评审强调，gating）

1. **ASR 英文 spike（P4.0）= 最大未知，决定下游架构**：真机对比 paraformer-en / SenseVoice /
   Fun-ASR-Nano，重点验 **Fun-ASR-Nano 能否中英单模型流式+热词**。结论决定要不要双引擎路由 + 前端离线 UX。
   **此前 ASR 全链路不动。**（需英文语音样本，可能要用户配合录几句或用 TTS 合成测试音频。）
2. **interpretation/clarification locale 端到端 PoC**：拿**一条英文指令**走通 PWA(en)→穿透子进程→
   LLM 英文 few-shot 解析→产出**英文** interpretation→echo 显示英文。证明数据流成立再铺量。

### 8.5 修订后的实现门槛

**当前状态：设计已按评审补强，但实现仍 gating 在 §8.4 两个 spike + 决策④（专有名词）确认。**
spike 通过 + 决策④定 → 才进 P1 批量实现。两个 spike 都属"我可自验"范畴（真局/真 LLM），
但 ASR spike 需英文音频样本（待定来源）。

---

## 9. 实现进度 + 交接记录（2026-06-27，/compact 前）

> **续作起手**：读本节 + `git log --oneline | grep i18n`（最新到 `d6fc6cd`）+ `locales/strings.json`（真理源，292 key）。
> 基础设施 + 模式都已建好，剩余按"已建立模式"批量推进即可。

### 9.1 已完成并提交（约 70%，全部 build+测试验过）

**基础设施（地基，别重建）**
- `locales/strings.json` = 中英唯一真理源，结构 `{id:{zh,en,context?}}`，**292 key、0 缺译**。前后端共读、无生成器。
- 前端 `web/src/i18n.ts`：`t(key, params?)`，**reactive locale**（切语言即时重渲）+ `{name}` 插值 + localStorage 持久化 + 浏览器语言默认。`@locales` 别名（vite/vitest/tsconfig 已配）。
- 后端 `src/vibecraft/i18n/__init__.py`：`t(key, lang, **params)`，读同一 strings.json。
- `web/src/components/LanguageSwitcher.vue`（中/EN 段控）。
- vitest setup `web/src/__tests__/setup.ts` 固定 locale=zh（旧中文断言测试靠 t() 返回 zh 通过，**别改这些测试**）。

**视觉测试工具（debug视觉效果用）**
- `web/scripts/visual_shots.mjs`（入口页直接截）+ `web/scripts/preview_shot.mjs`（单组件）。
- `web/preview.html` + `web/src/preview/main.ts`：vite 第二入口，mock fixture 挂载任意注册组件 → `/preview.html?c=<Name>&locale=<zh|en>` 截图。已注册 RoomLobby/GameBusyNotice/StatusChain/TacticsButton/CommandInput。**加新组件视觉检查 = 往 REGISTRY 加 fixture + `node scripts/preview_shot.mjs <Name> en`**。
- 跑法：server 在 8080 serve 最新 bundle（`npm run build` 后无需重启 server）→ 跑 mjs 截到 `%TEMP%\vibecraft-i18n-shots\` → Read 判读溢出/截断。

**已迁移组件（~30 个，所有主要 UI）**：入口页/二维码/语言切换 · 大厅/状态链/GameBusyNotice · 指令输入/语音/宏观/战术(Button/Card/Line)/编队 · 策略卡/选择器/决策流/BotDecision/推荐/AutoSwitch+Stealth toast/澄清弹层 · 指令卡堆叠/PendingForce/Production·StandingOrders/DropAct/Minimap(Nudge+Trackpad)/M3Placeholder/App.vue/LiveView。（CommandCard/CommandHistoryItem/TechRows/TechProgressPanel 本就用 t()。）

**locale 数据流（全链路打通 + 测试）**：PWA `i18n.locale` → `useWs` WS URL `&locale=` → ws 握手解析 → `WsConnection._locale` → `room.join(locale)` → `Slot.locale` → `match.py` per-player `GameConfig.locale` → 子进程 env `VIBECRAFT_LOCALE` → `IntentParser.locale`。测试 `tests/unit/test_locale_penetration.py`。

**Layer C lite（英文指令可用）**：`parser.parse` 在 locale=en 时给 LLM 追加英文指示 → interpretation 用英文 + 正常解析英文指令。**真 LLM 实测**："build two gateways"→"Build 2 extra Gateways"+StructureOverride 正确。

### 9.2 已全部完成（2026-06-28，原"未完成"项逐条收口）

> 下表是 /compact 时的剩余项，**现已全部实现 + 验证**（只剩"用户手机麦克风说英文"端到端需用户）。

1. **英文 ASR** ✅ — spike 选定 `SenseVoiceSmall`（实测近乎完美）；`AsrEngine` 双模型 + 离线
   `OfflineAsrSession` + 按 locale 路由 + warmup_en + 失败提示 + prefetch/selftest 脚本。
   `asr_en_selftest.py` 真模型 6/6 通过。opus 评审 4 gating 全采纳。
2. **英文别名/few-shot** ✅ — 别名表本就中英双语；新增 `few_shot.en.md`（locale=en 时拼到 few_shot）。
3. **后端服务端消息** ✅ — echo 前缀 + 大厅 room_error（21 类）+ 澄清弹窗（townhall/addon）全本地化。
4. **单位/建筑/升级名** ✅ — 前端 `unitName` locale-aware；后端 `Localizer` 补 en 表；Director 接 locale。
5. **文档** ✅ — ARCHITECTURE/USER_GUIDE/README/CONTRIBUTING 同步。
6. **视觉 zh/en 检查** ✅ — Playwright 入口页 + 6 组件 390px 无溢出/截断（en 实测 scrollWidth=390）。

**唯一剩余（需用户）**：手机麦克风真说英文的端到端（模型+session+后处理链已 hermetic 自验过）。
**可选未做**：build catalog 英文 display name（LLM 现靠别名+few-shot 已能解析英文 build，非阻塞）。

### 9.2（历史）原"未完成（剩 ~30%，按优先级）"

1. **英文 ASR（最大缺口，英文语音输入）**：现 `paraformer-zh-streaming` 仅中文。需 spike paraformer-en/SenseVoice/Fun-ASR-Nano（见 §3.4/§8.4），按会话 locale 路由（`asr.py` + 子进程 env 已有 locale）。需英文测试音频（TTS 合成：Windows SAPI / edge-tts）。**ASR 路由 infra 等 spike 结论再建**。
2. **英文别名/few-shot 精度**：`docs/aliases/*.yaml` 补英文别名（Stalker/Immortal…）；`docs/llm_prompt/few_shot.md` 加英文例（或 few_shot.en.md，prompt.py 按 locale 选）；build catalog 英文 display name（`models.py` 加 `display_name_en` + 46 yaml + 同步 test_*_strategies 计数/重 dump prompt——量大）。LLM 现裸跑已凑合，这是提精度。
3. **后端服务端消息本地化**：`auto_combat/common.py:70`（[模糊]/[解析失败]）、`director.py`(代码生成的中文 interpretation)、`ws.py` room_error。用 `vibecraft.i18n.t(key, lang)`，lang 取 `WsConnection._locale`/`parser.locale`。需加 strings.json key + 把 locale 喂到发送处。
4. **单位名本地化**：前端 `VoiceGroupBar.vue` 的 `UNIT_ZH`（叉子/追猎…）；后端 `bot/localization.py` Localizer en 表（canonical 已是官方英文，多数免费）。
5. **文档**：USER_GUIDE/README 英文版 + CONTRIBUTING 加"如何加一种语言"。

### 9.3 续作纪律（沿用）

- 加组件 i18n = 顶部 `import { t } from '@/i18n'` → 中文字面量换 `t('ns.key')` → strings.json 加 key（zh+en）→ `cd web; npx vitest run`（237 应全过）+ `npm run build` → 视觉 preview 截图判读 → 提交（带 strings.json + static/ bundle）。
- 大批量组件可 fan-out sonnet subagent（一次一批、串行避免 strings.json 冲突），主 agent 验证。Python 后端工作与前端 subagent 并行安全（无文件重叠）。
- 后端改动走 ruff+mypy（全包 `mypy src/vibecraft`，单文件有既存假阳性）+ pytest。

---

## 10. 英文 ASR：spike 结论 + 实现设计（2026-06-28）

### 10.1 Spike 结论（已实测，定论）

候选模型对比（edge-tts 合成 10 句 SC2 英文指令音频 → funasr AutoModel 识别）：

| 模型 | 语言 | 流式 | 热词 | SC2 英文指令实测 |
|---|---|---|---|---|
| paraformer-zh-streaming（现用） | 仅中文 | ✅ | ✅ | 英文不可用 |
| **SenseVoiceSmall** | 中/英/日/韩/粤 多语 | ❌ 离线 | ❌ | **9/10 完全一致**（剥标点后），第10句仅 ITN(one→1)/复数(rays→ray)，**LLM 解析无碍 = 实质 10/10** |

**定论：英文走 `iic/SenseVoiceSmall`**。识别质量对 SC2 英文指令近乎完美。代价：**非流式**（无逐字
partial，需松手后整段一次性解码）+ 输出带 `<\|en\|><\|EMO\|>…` 标签和尾标点（需后处理剥掉）+ 模型
~1GB（首次下载 ~6min，之后缓存秒载）。

### 10.2 实现设计

**双模型 + 按会话 locale 路由**（不替换 zh 流式，新增 en 离线）：

- `AsrEngine`：保留 `paraformer-zh-streaming`（zh，流式，现状不动）；**新增惰性加载第二模型**
  `SenseVoiceSmall`（en，离线）。两模型各自 `_ensure_loaded`/缓存。`create_session(locale)` 按 locale
  路由：`zh`→流式 session（现状）；`en`→**离线 session**。
- `AsrSession` 加**离线模式**（`offline=True` + `language="en"` + 持 SenseVoice 模型）：
  - `feed(chunk)`：离线模式只把采样**追加进 buffer**，不每块 generate，partial 返回空（或固定提示）。
  - `finalize()`：把整段 buffer 一次 `model.generate(input=buf, language="en", use_itn=True)` →
    `_strip_sensevoice(text)`（剥 `<\|…\|>` 标签 + 尾标点/首尾空白）→ 返回纯指令文本喂 LLM。
  - `cancel()`：清 buffer，同 zh。
- **预热**：zh 模型 server 启动即 warmup（现状不动）；**en 模型在 ws 握手见 `locale=en` 时**触发
  `engine.warmup_en()`（lazy，后台 executor，不阻塞），避免首句英文必失败（沿用 zh 那条教训）。
  不在启动时盲目 warm en（省 ~1GB 内存，多数局是 zh）。
- **路由接线**：ws.py 创建 AsrSession 处传 `self._locale` → `engine.create_session(self._locale)`。
- **后处理** `_strip_sensevoice`：`re.sub(r"<\|[^|]*\|>", "", text)` + `.strip()` + 去尾句号。
  （funasr 有 `rich_transcription_postprocess`，但只需剥标签，自己写更轻、零额外依赖。）

**依赖**：`funasr`/`torch`/`librosa` 已装；`SenseVoiceSmall` 走 modelscope 自动下载（首次）。
**不新增 pip 依赖**（edge-tts 仅 spike 用，不进 runtime）。

### 10.3 自验（不需手机）

`scripts/asr_en_selftest.py`（待建）：edge-tts 合成英文指令 wav → 经 `AsrEngine.create_session("en")`
的离线 session feed+finalize → 断言识别文本与原句**剥标点/ITN 后一致**（≥9/10）。真机英文语音（手机麦
克风）仍需用户端到端，但模型+session+后处理逻辑这条链可全自验。

### 10.4 未决（实现时定，非阻塞）

- 离线 en session 的 partial UX：松手前面板显示什么（"识别中…" vs 空）。倾向显示
  `voice.recognizing` 占位（已可 i18n）。
- en 热词：SenseVoice 不支持热词；SC2 英文专有名词靠模型本身 + LLM 别名兜底（#568）。

### 10.5 独立 opus 评审处置（2026-06-28，开工前）

评审结论"方向对，离线模式反而让 warmup 竞态比流式更安全"，4 条 gating 全采纳：
1. **首次下载 6min 悬崖** → 不只靠 handshake-lazy。加 `scripts/prefetch_asr_en.py`（部署期一次性
   预拉 SenseVoiceSmall）+ ws 握手见 locale=en 后台 `warmup_en()`。文档写明首次需预拉。【采纳】
2. **buffer 封顶 ~25s**（理由=SenseVoice 30s 设计上限 + 解码延迟，非 OOM）。超限丢最旧。【采纳】
3. **AsrEngine 双模型重构**：per-locale `_model/_lock/_loaded/_available`；`warmup()` 留 zh、加
   `warmup_en()`；per-locale `available_for(locale)`；en 加载失败 → 前端 `asr_unavailable` 提示帧
   （不静默丢音频）。【采纳】
4. **`create_session(locale)` 签名 + 同步唯一调用点 ws.py + test fake**。【采纳】

建议项：5. 拆 `StreamingAsrSession`/`OfflineAsrSession` 两类（不用 offline flag）【采纳】；
6. 后处理优先用 funasr `rich_transcription_postprocess`，regex 兜底【采纳】；7. selftest 用**提交的
wav fixture**（hermetic，不每次 live edge-tts）+ 补路由/offline/buffer-cap 单测【采纳】；
8. 前端"识别中…"离线状态 + asr_unavailable toast【采纳】；9. 文档写明混合 locale 多人局额外 RAM【采纳】。

---

## 11. i18n 漏网之鱼清扫（2026-06-28，#572）

排查 agent 全量扫出 ~150 处残留中文，分 4 批。**批1（前端 16 处）已完成提交**（chat/addon 角标/控制归属/偷矿农民标签）。剩 3 批设计如下，批3/批4 涉及架构，**实现前发独立 opus 评审**。

### 批2：后端 display 硬编码 → `i18n.t(key, self._lang)`（~17 处，纯模式套用）

`director.py` 多处把中文字面量塞进 snapshot `display`/`interpretation_zh`/condition `text`：镜头跟随
(1479/1481/1488)、产能封锁(1509)、偷矿 display(1558)、凤凰骚扰(1669/1676/1681)、生产队列反馈
(5087/5089/5091)、自定(2050)、卸载/装兵(8918)、被攻击/已摧毁(7005)、strategy 转移 reason(8267)。
+ `common_bot.py:1845-1860` decision 三元组（运营中/进攻中/前沿集结/N矿N兵）。

做法：director.py 各处用 `_i18n_t(key, self._lang, **params)`（self._lang 已有）。common_bot 的
decision 在 bot 类里——locale 取 `getattr(self.knowledge, "vibecraft", None)` 或 director._lang（接线时定，
优先复用 director._lang）。新增 strings.json key（camera./override./queue./harass./decision./time.seconds…）。

### 批3：snapshot 名词 `name_zh` → 走 `Localizer(self._lang)`（架构）

**根因**：snapshot 建 tech/building/unit 用 `self._TECH_ZH_NAMES`/`_BUILDING_ZH_NAMES`/`_ARMY_UNIT_ZH_NAMES`
/`_TECH_BUILDING_ZH_NAMES` ClassVar（= `["zh"]` 表），EN 模式仍出中文。
**坑**：`_ARMY_UNIT_ZH_NAMES`(director.py:2284) 是**自定义全族** dict（含 zerg/terran），而 Localizer
`UNIT_NAMES` **只有神族 19 个**。直接 swap 成 `self._loc.unit()` 会让虫/人单位在 **zh 模式也回退英文 id**（回归）。
**方案**：① 把 `_ARMY_UNIT_ZH_NAMES` 全族条目并入 `localization.py` `UNIT_NAMES`（zh 补全 + en 官方名），
director 改用 `self._loc.unit()/.upgrade()/.structure()`；② 各 `*_ZH_NAMES.get(x,x)` swap 成 `self._loc.*`。
snapshot 字段名 `name_zh` 保留（遗留，现装本地化文本），前端不改。
**验证**：preview TechProgressPanel en/zh 截图 + 单测 name 表 zh/en key 一致。

### 批4：strategy catalog 英文名（架构，46 yaml，最大块）

**根因**：`strategy/models.py` 的 `display_name_zh`/`summary_zh` + 各 yaml phases `display` 无英文。
喂：宏观面板 build 名、推荐"建议切换 X"、决策、command card strategy display。
**方案**：
- models 加 **optional** `display_name_en: str = ""`/`summary_en: str = ""`（phase 同加 `display_en`），
  缺省回退 zh（**不破坏现有 yaml**，渐进翻译）。
- 取值统一走一个 helper `localized_name(strat, lang)`：`en and strat.display_name_en or strat.display_name_zh`。
- 消费端按 locale：`http.py /api/strategies` 加 `?locale=`（宏观面板 fetch 带 `i18n.locale`）；
  `director.py` 所有 `display_name_zh` 取值点改走 helper(self._lang)。
- 46 yaml 翻译（display_name_en/summary_en/phase display_en）→ sonnet subagent 批量（像 Localizer en 表），
  主 agent 校 SC2 术语。同步 `test_*_strategies` 计数不变（只加字段）。
**验证**：`/api/strategies?locale=en` 返回英文名；preview 宏观面板/推荐卡 en 截图；真 LLM 不受影响（catalog 仍中文喂 LLM，display 仅给玩家看）。

### 批次顺序 + 验证

批2(后端 display，我做) → 批3(name_zh，我做，需先扩 Localizer) → 批4(strategy catalog，设计+sonnet 翻译)。
每批：ruff+mypy(全包) + pytest + preview 截图判读。**批3/批4 实现前 opus 评审**（全族 Localizer 合并风险 + strategy schema/locale plumbing）。

### 11.x 独立 opus 评审处置（2026-06-28，批3/批4 实现前）

评审"方向对、批4 plumbing 干净"，但批3 原方案会回归、批4 有 LLM 边界暗坑。**必改全采纳**：

**批3 改方案（关键）**：
1. **不要并入 `UNIT_NAMES`**。`UNIT_NAMES`=黑话(指令卡用,叉子/追猎)，`_ARMY_UNIT_ZH_NAMES`=官方正式名
   (面板用,狂热者/追猎者)，**两套寄存器故意不同**(director:2281-2283 注释明写)，合并必破一方。
   → 新增独立表 `ARMY_UNIT_NAMES`(localization.py)：**全大写 key**(对齐 `UnitTypeId.name`) + zh 官方名
   (照搬 director dict) + en 官方英文名；加 `Localizer.army_unit()`(upper=True)。`_ARMY_UNIT_ZH_NAMES`
   改成 `ARMY_UNIT_NAMES["zh"]` 别名。snapshot 兵种(2778)改 `self._loc.army_unit()`。**UNIT_NAMES 黑话表不动。**
2. **key 大小写**：UNIT_NAMES 是 PascalCase，snapshot `tid_name`=全大写 → army 表必须全大写,否则全 miss 回退英文 id。
3. tech/building/upgrade swap **安全**(本就是 Localizer 别名,en 已有)：`_TECH_ZH_NAMES.get→_loc.upgrade`、
   `_BUILDING_ZH_NAMES→_loc.structure`、`_TECH_BUILDING_ZH_NAMES→_loc.structure`。
4. **批3 配套审计 `TechRows.vue`**：name_zh 周围还有硬编码中文(`个`/`研究中`/`已完成`/`建造中`/`在产`)
   + `name_zh.slice(0,3)` 图标 fallback(英文会切成 Voi/Imm)；**部分进 title/aria → 截图验不到**,必须单独 i18n 审计。
5. 注：zerg/terran **黑话**(指令卡)源缺失是**既存 gap**(UNIT_NAMES 仅神族),非批3 引入；批3 只解决面板官方名,
   别顺手"全族化"误改神族黑话。

**批4 改方案（关键）**：
6. **LLM catalog 边界**：`prompt.py:187/194/201/209` 用 display_name_zh/summary_zh 拼**喂 LLM 的 catalog**——
   必须**保持中文**(Layer C lite 方案：catalog 中文 + 别名/few_shot.en 解析英文)。**`localized_name` helper 严禁
   渗进 `prompt.py` 和 `gen_asr_hotwords.py`(中文热词源)**。一刀切 sweep `display_name_zh→helper` 会误喂英文给
   LLM → 解析回归。§8.2 P4.6"喂 LLM 英文 catalog"**作废**(被 Layer C lite 取代)。
7. helper `localized_name(strat, lang)` 放 `strategy/models.py`(父子进程共用)。
8. `/api/strategies?locale=` query 参数(对,catalog 父进程只加载一次,不能 bake locale)；
   `_serve_strategies_api` 取 `?locale=`(http.py 已有 parse_qs 设施)，`StrategyPicker.vue:67` fetch 带 `?locale=${i18n.locale}`。
9. **phase `display_en` 要做**(StrategyCard:97 渲染 phase.display,玩家可见)；director snapshot 的 **phase.display
   + slot.display** 也走 helper(别只改 /api/strategies,否则 live 面板 phase 仍中文)。
10. 46 yaml sonnet 翻译给**术语表**(Localizer en 表 + memory `feedback_sc2_chinese_terms`)约束 + 主 agent 校 SC2 术语 + en 截图判读。
11. `useWs.test.ts:141` 断言 display=='双矿 IAC 重装地面'：locale 固定 zh,helper(zh) 仍回 zh,不破——但改 display 来源后确认仍绿。

**执行顺序**：批2(director display,我) → 批3(新增 ARMY_UNIT_NAMES + swap + TechRows 审计,我) → 批4(models helper + /api/strategies locale + 46 yaml sonnet 翻 + 主校,排除 prompt.py)。

### 11.y 完成（2026-06-28，全部 4 批落地 + server 实测）

#572 漏网清扫**全部完成并提交**：
- **批1** 前端 16 处（聊天/附件角标/控制归属/偷矿农民）✓
- **批2** 后端 director 18 + common_bot 决策 13（运营中N矿N兵 等）→ i18n.t(self._lang) ✓
- **批3** snapshot 兵种/科技/建筑名 → Localizer(self._lang)（新增独立全族 `ARMY_UNIT_NAMES`，
  不动黑话表 UNIT_NAMES）+ TechRows 提示残留中文审计 ✓
- **批4** strategy catalog：models 加 optional en 字段 + helper + http.py `?locale=` + StrategyPicker
  fetch 带 locale + director snapshot strategy/phase display 走 helper + **46 yaml 全部英文翻译**
  （sonnet 译 + 主校，SC2 术语准）✓。**LLM 边界守住**：prompt.py 仍中文喂 LLM。
- **实测**：fresh server `/api/strategies?locale=en` 返回 46 个英文策略名（'4-Gate Stalker Rush' 等）；
  各批 ruff/mypy 无新增、pytest 全过、web 241 全过。

**剩余只有"用户手机端到端"**：切 EN 进游戏真看决策/面板/策略全英文（后端逻辑已自验，真机交互靠用户）。
