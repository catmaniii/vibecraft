# TASKS.md

任务拆解 + 进展。设计真理源 `docs/plans/2026-05-14-vibecraft-design.md`；代码现状
`ARCHITECTURE.md`；约定 `CLAUDE.md`；已发版历史 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」+「用户环境关键事实」两段**（取代了原 CLAUDE.md
HANDOFF 块）。已完成里程碑的细节去 `CHANGELOG.md` / `git log` / `docs/adr/`。

---

## 当前状态（最近更新：2026-06-28，分支 `main`）

### 已完成：#572 i18n 漏网之鱼清扫（残留中文全部归位，4 批全提交，server 实测）

4 批全落地：批1 前端 16 处 / 批2 后端 director+决策 / 批3 snapshot 兵种名走 Localizer(新增独立全族
`ARMY_UNIT_NAMES`)+TechRows / 批4 strategy catalog(46 yaml 英文翻译 + helper + /api/strategies?locale=
+ StrategyPicker)。**LLM 边界守住**(prompt.py 仍中文)。fresh server 实测 /api/strategies?locale=en 出
46 个英文策略名。详见设计 §11/§11.x/§11.y。剩余只有用户手机端到端真看。**server 已重启带全部新代码**。

### （历史）#572 进行中记录

用户实测发现一批切英文后仍中文的地方。排查 agent 全量扫出 ~150 处，分 4 批（详见
`docs/plans/2026-06-27-i18n-localization-design.md` §11 + 评审处置 §11.x）：
- **批1 前端 16 处**（聊天/附件角标/控制归属/偷矿农民）→ **已完成提交**。
- **批2 后端 director.py display ~17 处**（镜头跟随/产能封锁/生产队列/decision"运营中N矿N兵"）→
  待做，`_i18n_t(key, self._lang)` 模式套用 + common_bot decision(locale 取 director._lang)。
- **批3 snapshot 兵种名（架构）**：**不要**并入 UNIT_NAMES(黑话表)！新增独立 `ARMY_UNIT_NAMES`
  (localization.py,**全大写 key** 对齐 UnitTypeId.name,zh 官方名+en) + `Localizer.army_unit()`;
  tech/building/upgrade swap 成 `self._loc.*`(安全,已是别名);配套审计 `TechRows.vue` 硬编码中文
  (个/研究中/已完成/建造中/在产 + name_zh.slice fallback,部分在 title/aria 截图验不到)。
- **批4 strategy catalog 英文名（架构,46 yaml,最大块）**：models 加 optional display_name_en/summary_en
  + phase display_en(回退 zh) + helper `localized_name(strat,lang)`(放 models.py);`/api/strategies?locale=`
  + `StrategyPicker.vue:67` fetch 带 locale;director snapshot phase/slot display 走 helper;
  **严禁** helper 渗进 `prompt.py`/`gen_asr_hotwords.py`(catalog 保持中文喂 LLM,否则解析回归);
  46 yaml 翻译走 sonnet(给术语表+主校)。
- **第⑦点"英文 interpretation 还是中文"**：很可能是旧 server(本轮 i18n 没重启过 server),重启带新代码即好;真 bug 待确认。

执行顺序批2→批3→批4,每批 ruff+mypy(全包)+pytest+preview 截图验。

### 本轮(2026-06-27~28)：i18n 中英全量本地化 + 英文语音识别（#565，已完成并提交）

开源前的"换语言本地化"功能，**全量一批交付**（设计 `docs/plans/2026-06-27-i18n-localization-design.md`）。
6 个子任务全部完成 + 验证（commit `8258e21`→`6631412`）：
- **基础设施**（上一会话）：`locales/strings.json` 中英唯一真理源（326 key、0 缺译）+ 前端 `i18n.ts`
  `t()`（reactive + localStorage + 浏览器默认）+ 后端 `i18n/t()` + `LanguageSwitcher` + locale 全链路。
- **#566 后端服务端消息**：echo 前缀 `[模糊]/[解析失败]` + 大厅 `room_error`（21 类，RoomError 带 i18n key）
  + 澄清弹窗（townhall snap / addon 挂件）全本地化。
- **#567 单位/建筑/升级名**：前端 `unitName` locale-aware（官方英文名）+ 后端 `Localizer` 补 en 表
  （建筑保留 hotkey）+ Director 接 `parser.locale`。
- **#568 英文解析精度**：别名表本就中英双语 + 新增 `few_shot.en.md`（locale=en 拼接）。
- **#569 英文 ASR**（最大块）：spike 选定 **SenseVoiceSmall**（离线/非流式，真模型自验 6/6）；`AsrEngine`
  双模型按 locale 路由（zh 流式不动 / en 离线）+ `warmup_en` + 失败提示 + `prefetch_asr_en.py` /
  `asr_en_selftest.py`。独立 opus 评审 4 gating 全采纳。
- **#570 文档**：ARCHITECTURE/USER_GUIDE/README/CONTRIBUTING 同步。
- **视觉**：Playwright 入口页 + 6 组件 en 截图 390px 无溢出。

**需用户做的端到端**：手机麦克风真说英文（模型+session+后处理链已 hermetic 自验；只剩真麦那一步）。
**首次部署英文 ASR 前**：跑 `.venv/Scripts/python.exe scripts/prefetch_asr_en.py` 预拉 ~1GB 模型。
**测试**：全量 3274 passed（1 个 `test_spare_cc_expand_act` 失败是**既存** test-ordering 污染，单独跑 PASS，与 i18n 无关）。

### 本轮(2026-06-18)已完成并提交的：
- **bio_stim(3矿5BB)产能效率大修 #537**(已完成):build_acceptance **5/14→12/14**。telemetry 实证根因
  (推翻早期"181s 自杀"错判,start_attack_power 是 red herring):① BuildAddon 只在空闲兵营挂、Marine
  (priority)在前塞满兵营→**TechLab 永不挂→stim 749s、0 掠夺者、气 flood 2000**;② BB1-ready 并发
  Expand2(400)+Factory+2gas **早期矿荒→兵营卡 1 个到 312s**。修法(全在 `bio_stim.py` 编排,无新执行器):
  TechLab 挂件前置到 Marine 前 + **全部产兵下移到建筑步后**(掠夺者/医疗船排枪兵前抢专属档期)+
  Expand2 推 BB2-ready/gas3 延后 + Starport/工程湾提优先级。结果:stim 247s、掠夺者 8-15/医疗船 6-7、
  5 兵营 2 科技 3 双倍、攻防 +1,原"2/3 局 6min 暴毙"消失,survive 9min+ 带 60 农民。剩余未过项
  command_center_2(故意去贪婪,符用户"别太激进"为保 stim 取舍)+ weapons/marine/pressure(中后期战损噪声)。
  详见 `docs/plans/2026-06-18-bio_stim-efficiency-findings.md`。
- **defend 大军"原地保持队形拉扯"修复**(`be3e8e6`):defend 下 PlanZoneDefense 不抢主力(交给
  PlanZoneGather 单一锚点),消除 enemy_center↔锚点双目标 churn。真局反转率降5.7x,三族 defend VeryHard 全PASS。
- **"游戏进行中"提醒**(`57be564`)+ **点连接清残留状态**(`4587643`):后加入者遇对局中显示提醒不弹回入口;
  connectNow 加 resetSessionState 清旧 status/snapshot + "连接中"占位,不再闪上一把残留。
- **科技单位主动技能补全**(`ef7885e`):新建 MicroGhosts(EMP/狙击/隐形)+MicroBanshees(隐形)+修
  MicroRoaches注册+P2(sentry/viper/raven)。真局自验 鬼狙9/EMP9/女妖隐形6 全PASS。**scope:只覆盖
  caster 跟大军一起参战;玩家单独编队(unit_claim→Reserved)的 caster 不走自动施法**。
- **人族产能挂件决策 P1**(`7d081b5`→`81b3394`):语音指令能精准指定挂件(组合"补4bb,2科技2双倍"→
  Barracks+2TechLab+2Reactor)+"补Nbb没说挂件"弹窗3选(不挂/推荐/取消)+ 需求驱动推荐算法。
  StructureItem 加 `addon_decided` 布尔。真局自验 PASS。**P2(bot 自主建好即挂)未做**——评审证明全局
  循环会和现有 build addon step 打架,要单独修既有步骤可靠性。
- **CLAUDE.md 记 VPS 启动**(`4d01578`):server 起来后跑 `deploy/turn/pc-tunnel.ps1` 连香港 VPS 公网前门。

### 更早大块产出：多人阶段1 — 云 TURN 中继 + 公网前门（真机端到端打通，无需 Tailscale）

香港阿里云轻量 VPS（2C2G/200M 不限流量，免备案）部署完成，国内手机直连验证通过：
- **coturn TURN 中继**：`turns:443`（穿中国防火墙）+ STUN/TURN 3478；REST 短期凭证；
  私网拒绝防 SSRF；CAP_NET_BIND 绑 443。部署脚本 `deploy/turn/setup-coturn.sh`。
- **接进 App**（独立 Opus 评审后实现）：`server/turn_config.py`（现签凭证+graceful）+
  `webrtc.py` aiortc iceServers + `http.py` `/api/turn-credential`(room 门控) +
  `LiveView.vue` fetch+回退。P2P 优先、打不通走 turns:443。
- **公网前门**（去 Tailscale 依赖，用户实测单人打电脑通）：VPS **nginx 443 SNI 分流**
  （`turn.*`→coturn / `app.*`→反向隧道→PC）+ **PC→VPS SSH 反向隧道**
  （`deploy/turn/pc-tunnel.ps1` 自动重连）。手机连
  **`https://app.<VPS_IP>.sslip.io/?room=vibecraft-dev`**（HK 国内可达）。
- 音频推流音量衰减（默认 0.5，env `VIBECRAFT_AUDIO_GAIN` 可调，用户反馈太吵）。
- **VPS 凭据/配置在 `.secrets/vibecraft-turn.env`（gitignore）**；TURN_DOMAIN=`turn.*`。
  零输入手机测试页：`http://<VPS_IP>.sslip.io/`（80）。

**已写未实施**：多主机大厅（多 PC 挂房间 + 浏览列表 + 状态/排序/筛选）设计稿
`docs/plans/2026-06-14-multi-host-lobby-design.md` —— 用户先用单房间找朋友实测，回来
确认开放问题（主机范围/密码/刷新频率/路由方案）后走"评审→实施"。

**待办：临时邀请权限（2026-06-14 用户提，不急）**：当前单固定房间码 = 谁拿到链接永久能
连。需求 = 给测试者发**会过期的邀请链接**，到期自动失效。推荐方案 = 签名式过期邀请码
（复用 TURN 那套 HMAC，无状态）：房主用固定码当管理员，给测试者发 `?room=inv.<exp>.<name>.<sig>`，
`RoomRegistry.verify` 升级成"固定码 OR 验签+未过期的邀请码"。开放问题：每人专属 vs 共享、
要不要中途撤销（要存状态）、命令行发码 vs PWA 按钮。属改鉴权，做前先方案+Opus 评审。

**待办小瑕疵**：已开局时新人连接回的文案是通用"对局进行中，不能改房间设置"，对"加入"
场景措辞不贴切，可改"对局进行中，无法加入"（用户已知，未拍板）。

### 更早大块产出：多人联网阶段 0 完整落地（端到端 selftest 14/14 PASS）

一台 PC 跑多个 SC2 实例 LAN host/join 成局，每实例一个 bot，多个玩家各用手机
（入口页用户名+服务器列表 → SC2 式 lobby 选位/ready → 对局）接入指挥。架构见
`ARCHITECTURE.md`「多人联网」节；设计/实施/spike 结论见
`docs/plans/2026-06-12-multiplayer-{design,implementation-plan}.md`。

落地要点（全部 Opus 独立评审 M1-M4 + S 系列修订后实现）：
- Room 状态机 + per-player RoomRegistry + MatchOrchestrator（per-match monitor，
  connection 无关）+ RoomService 聚合 + WS lobby 帧族 + per-player 路由
- PWA：入口页（用户名+多服务器，开源多服务器接入形态）+ RoomLobby + LiveView
  not-ready 自动重试；WebRTC per-player PC + 按 SC2 窗口 PID 抓屏
- spike 排坑实锤：①contiguous 端口被 Windows 顺序端口游标撞上（修=散点 Portconfig）
  ②python-sc2 吞 join 错误（修=检查+重试）③引擎硬限制多 agent 仅纯 1v1
  （双真人不能加电脑，"2人合作打电脑"此路线不可行）④对方进程死=己方 Victory
- 旧单人流程零破坏（start_game 薄 shim；roomState null 时 PWA 旧行为）

三轮真人实测反馈已全部修复（2026-06-12/13，详见 CHANGELOG）：lobby 名单闪烁（socket
代际守卫+延迟 leave）、连接与入房解耦（退出/刷新语义）、lobby UI 整轮打磨、视频抓
客户区、4bg 主攻闸门换锁存 AttackGate（修"面板显示进攻但部队不动"，acceptance
VE1+VH3 全过）、面板进攻真值读作战层、FunASR 启动预热（修首句语音必败）、偷矿
矿优先+跟随主经济。

**音频已修复（2026-06-13 任务 #516 完成）**：WebRTC 音频默认重新开启 —— 每条音频轨
独享 grabber 子进程，按各自 SC2 实例 PID 用 WASAPI process loopback 分局采集
（`server/process_loopback.py`，Win10 20H1+，失败回退整机 device loopback）。
多人局两手机各听各的；破音（旧共享 grabber 分帧）结构上消失。自验
`scripts/spike_process_loopback.py` + 生产链路双 grabber 并行分离 PASS。
**待真机人耳复核**（手机听 SC2 game 声）。

**SC2 失焦静音（任务 #517，2026-06-13 已解决）**：多人局原本只有聚焦的 SC2 窗口
出声（引擎内部静音，外部干预三路实证无效——Variables.txt 候选变量/假激活消息/
WASAPI session）。**解：SC2 游戏内 选项→声音 的后台播放选项 = 全局
`Variables.txt` 的 `soundglobal=true`**，bot 实例共用全局配置，开一次永久生效。
用户实测两手机同时有声。残余"音乐偶发破音"已修（grabber 60ms 起播预缓冲 +
underrun/trim 诊断计数）。待办 #522：server 启动检查 soundglobal 缺失时提示
（开源后新用户不会知道这个前提）。

**第五轮反馈已闭环（2026-06-13）**：①跨种族指令校验（人族说造OB → 友好拒绝；
拥有他族单位/农民例外放行）②镜头跟随焦点重算减半 + 每 tick lerp 平滑滑动
③防守威胁感知（敌军 power>3 逼近任何己方基地 → 全军迎击，1.5x 滞回）
④**自主进攻卡死真凶**：`set_engagement_stance(None)` 真机 no-op → 玩家 × 防守后
stance 永久钉死 → bot 不再自主进攻（facade 双实现坑再录一例，已修 + 直打真机
facade 的回归测试）。defend e2e 双 case PASS；全量 2830 passed。

**第六轮收尾已闭环（2026-06-13）**：①**#527 语音间歇失效**：麦克风 track 死亡
（OS 回收/锁屏，不触发 visibilitychange）后 `armed` 仍 true、`arm()` 永不重建 →
整条语音静默到刷新。修 = `isTrackHealthy()` + arm() 自愈重建 + track.onended 复位 +
start() 无条件 arm；服务端加 `ws_audio_segment_stats`（peak/silent）诊断。②**#522**
server 启动检查 SC2 后台播放（`soundglobal=true`），缺失给中文警告（开源新用户前提）。
③**#526 真凶**：telemetry plan_status 恒 null（**单/多人都坏**，非仅多人）= `_walk_plan_tree`
从不存在的 `bot.build_plan` 取树根；真根在 `knowledge.managers` 的 `ActManager._act`。
靠新加的 `plan_dbg` 面包屑在真局 build_acceptance 一眼定位（nodes=0）→ 修后读到真实
AttackStatus。

**已知限制 / 待办**：人/虫族偷矿（任务 #515，延后）；3+ 真人未实测（双重拦截）；
torch/funasr 在解释器**退出时**原生段错误（venv 重装后出现，不影响测试结果只污染
退出码，待查）；阶段 1（新加坡轻量 VPS 会合服务）未开工。
**需用户真机确认**：#527 语音修复（让张三复现"上来语音失效"，看 server log
`ws_audio_segment_stats` 的 `silent=`：true=track 死了在发静音且现已自愈）；
其余 #522/#526/#523/#524/#525 均已自验。
**下一步：用户继续多人真人实测**，然后阶段 1。

### 更早产出：偷矿（Stealth Mining）系统完整落地

玩家镜头对准远端隐蔽点说"在这偷矿" → bot 派农民去建一片**隐蔽自给的偷矿基地**：自产农民、
自己采矿采气、满采到 **22（16 矿 + 6 气）**、跟主矿**双向隔离**（互不串农民）、支持多片同时偷、
受击自动撤销交还 bot + 弹通知。核心 `bot/stealth/`（`StealthCellManager` 状态机
PENDING→BUILDING→MINING→RELEASED/DESTROYED）+ 一串 vendor sharpy FENCE patch。
**架构见 `ARCHITECTURE.md`「偷矿系统」节。**

2026-06-12 上午一串真机定位+修复（FENCE tag-aware / 采气 grace / 倒灌修复 / 焊牢均分 /
telemetry+ECONTRACE / 在建偷矿算基地数，详见 CHANGELOG）。自验：
`scripts/stealth_saturation_selftest.py` / `scripts/stealth_mine_selftest.py`。

### 更早产出（均已完成，详见 CHANGELOG / git log）

- **语音输入（FunASR）**：手机按住说话 → 流式 ASR → 现有 command 管线（微信式：默认语音可切
  文字、上滑取消；音频走 WS funnel）。
- **语音编队（≤5 队）+ 镜头定位指令**（"以镜头位置下令"）+ **代理建造链**（"派农民去 X 修水晶再修两个 VS"）。
- **控制边界可视化**（游戏内方框=指令卡 / 圆环=编队 + 手机面板）+ **双栏实时运营**（扩张矿数 chips / 农民停补）。
- **bot 自评旁白**（丢分矿/损兵）+ **撤销恢复栈**（被抢占单位 per-tag 恢复）。
- **玩家覆盖 path 方案 D**（vendor sharpy 集中 hook，override_acceptance e2e）+ NamedSpotRegistry 钟点/方位。
- **三族 bot**（神族 8 开局 / 人族·虫族各 5）+ build_acceptance 验收框架。

### 下一步候选

1. **多人真人验收 + 阶段 1**：用户两台手机实测多人；然后会合服务上 VPS（房间目录+
   WS 转发+信令+coturn，设计见 multiplayer-design.md §2）。
2. **开源前另两大方向**（见下 backlog）：人族/虫族 build 库补全、多地图适配。
3. **build_acceptance 残留 FAIL**：blink_stalker 15/18、iac_2base 18/19（见下）。

### 未做但已知

- 偷矿 + 各功能的真实游戏 e2e 大多靠 self-test + 截图验过，纯实战长期行为待真局确认。

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`（非默认）。`SC2PATH` 已永久设好（user-level）。地图
  `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位（**当前只有这一张图**）。
- `.venv` = Python 3.11.14（**不能用 3.12**，sc2-helper 无 cp312 wheel）。
- ⚠️ `uv sync` / `uv run` 不带 `--extra sc2` 会**卸载** ares；server 用 `start.ps1`（内含 `--no-sync`），
  跑 pytest / smoke 用 `uv run --no-sync`。`.venv/.../ares_sc2_src.pth` 修 ares 打包 bug，重建 venv 后需重建（见 `docs/m0-smoke-runbook.md`）。
- 开发用固定 token `vibecraft-dev`：`.\scripts\start.ps1 -Token vibecraft-dev`（前台跑，Ctrl+C 停）。
- **server + funnel 一起开**（外网手机测）：funnel host 固定 `<your-host>.<your-tailnet>.ts.net`，
  URL `https://<your-host>.<your-tailnet>.ts.net/?room=vibecraft-dev`（funnel 配置持久，重启 server 不用重开 funnel）。详见 CLAUDE.md「PWA 连接」节。
- web 源码改了要 `cd web; npm run build`（**PowerShell 工具**，Bash 会中途杀 vite）；手机 PWA 缓存用隐私窗口刷。
- 用户 GitHub `catmaniii`，gh 已认证，remote `origin = github.com/catmaniii/vibecraft`。
- Windows + retail SC2 不能真 headless（D3D9 device Lost）；SC2 窗口可见。non-realtime 可并行 4-8 实例。
- **多人功能测试方式（2026-06-12 用户）**：用户在本地 WiFi、或一台开了 Tailscale 的手机上，
  用**多个不同浏览器窗口 / 多台手机**登录为不同玩家来测。我方先自测（多 ws 客户端模拟多玩家 +
  真局 selftest）把各场景全部跑通，再喊用户人工测。

---

## 开源前大方向 backlog（2026-06-08 用户列，逐个拆 design+plan）

> **背景**：迭代到满意后**开源**。以下三块是当前缺的大方向（细节待办见下「其它待办」）。

1. **人族 / 虫族 build 库补全**：build 库目前以**神族**为主。人族、虫族都要补到开局 ≥ 7-8 +
   中后期 doctrine ≥ 7-8。走 `docs/process/new-opening-strategy.md`（先 web search 真实 build →
   写 yaml → build_acceptance 调优）。虫族部分开局还缺 spec，一起做。
2. **多地图支持 + 指令随图适配**：难点不是加图，是 named_spot / 特色指令随图变（左右瞭望塔位置、
   4bg 野水晶位置换图可能 bug）。设计要点：每张图一份「地图档案」（特色 spot 表 + 提示词片段）
   按当前地图动态注入；`NamedSpotRegistry` 已是基础，需扩成 per-map。
3. **多人联网**：~~① 单机多 bot host/join 链路 ② PWA 房间/对局视图~~（**阶段 0 已完成**
   2026-06-12，见「当前状态」）。剩余：阶段 1 会合服务上 VPS（新加坡轻量，房间目录+
   WS 转发+信令+coturn）→ 阶段 2 会合服务可自托管 + 简单账号（设计已定：
   `docs/plans/2026-06-12-multiplayer-design.md` §2）。

**开源排除项（2026-07-14）**：推理图谱子系统（RG）是**内部研发工具**，不进开源交付物。开源前需排除
`docs/reasoning-graph.yaml`、`tests/unit/{test_reasoning_graph,test_rg_scripts,test_serve_rg}.py`、
`/rg` 路由（`server/http.py::_serve_rg`）、以及全局 skill（`~/.claude/skills/reasoning-graph/`，本就
不在 repo 内）。落地方式（发布时 strip 掉这几项 / 单独维护发布分支）届时再定。

---

## 其它待办

- **人族/虫族偷矿支持**（2026-06-12 用户真机发现，将来再做）：偷矿系统当前写死神族
  （StealthCellManager 全链路 NEXUS/PROBE/ASSIMILATOR/星空加速），人/虫族下偷矿令
  cell 创建后 PENDING 静默卡死。已加友好拒绝（"暂只支持神族"）。完整支持要按族抽象：
  虫族 Drone→BH(Hatchery)→BE(Extractor)（drone 变建筑消失、产能走 larva）、人族
  SCV→BC(CC)→BR(Refinery)（SCV 持续建造、MULE 加成）；各自要 selftest 验饱和。

- **build_acceptance 残留 FAIL（神族）**：
  - `blink_stalker`（15/18）：4 BG 偏慢 + stalker 产能不足（出门 6-7 兵 vs spec 10）、warp_prism 没出。
  - `iac_2base`（18/19）：DT 首批只 2（spec 4），单 warpgate warp DT 限速。注：iac 科技密集，
    提前暴 BG 会拖垮科技线（实测过），不能照搬 dt_drop_iac 修法。
- **虫族开局验收补全**：12pool / macro_hatch / mutalisk_harass / roach_hydra / brood_corruptor 等还缺 spec。
- **骚扰微操可深挖**：寡妇雷逐个卸雷 / 贴边飞、reaper 单兵方差。
- **验收 check 固有限制**（低优先级）：瞬态位置 check（dt_at_enemy / warp_prism_at_enemy / army_gather）
  单快照判定天然 flaky；verifier 窗口判定目前只覆盖计数类。
- **flaky cross-test**（full suite 偶发、单跑永 PASS）：`test_loads_real_strategies` /
  `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`。
- **e2e smoke**（需 SC2）：三族各 1 局 vs VeryEasy。

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

## 历史 / 已废决策（指针）

- 命名：`speech_craft` → `VoiceCraft` → 2026-05-16 `VibeCraft`（不再绑死语音输入）。
- ares-sc2 → sharpy-sc2 全框架迁移（2026-05-16，ADR 0009）。
- M2 four-layer 指令架构（P0-P6）已全完成，见 `docs/adr/0010-four-layer-commands.md`。
- SC2 卡死检测：子进程内 `HangWatchdog`（bot.time 30s 不前进则 kill）。
