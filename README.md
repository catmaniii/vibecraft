# VibeCraft

> 🇨🇳 中文 · [🇬🇧 English](README.en.md)

**动动嘴 / 打打字，AI 替你操作 StarCraft II —— 给操作不动、战略还在线的老玩家。**

你脑子里还能打 SC2，但手已经跟不上节奏了。VibeCraft 让你用**手机下达战略和微操指令**
（打字 / 手机输入法语音转字 / App 内按住说话），AI（基础 bot）替你执行所有鼠标键盘操作：
补农、补给、扩张、造兵、出兵、基础战斗。你只管看战况、做判断、关键时刻发令——**你是指挥官，
AI 是参谋副官**。

> 适合：战略还在线但手不行了的老玩家、想跟老朋友再开几把又不拼操作的人。
> 不适合：想拼 APM 的天梯玩家、完全没玩过 SC2 的新手。

> **非官方粉丝项目**：本项目与暴雪无关，需自备正版 StarCraft II，不分发任何游戏文件；
> 使用受 Blizzard "StarCraft II AI and Machine Learning License"（仅限非商业研究/AI 用途）
> 与 EULA 约束。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本仓库 MIT 许可仅覆盖自身源码。

> **想自己跑起来？先过这份前置检查**（缺一项都会卡住）：
> - **Windows 10/11** PC（音频分流 / PowerShell 脚本目前是 Windows-only）
> - 已安装并启动过一次 **正版 StarCraft II**
> - **Python 3.11+** 和 **uv**（包管理器，https://docs.astral.sh/uv/）
> - **DEEPSEEK_API_KEY** 环境变量（LLM 解析指令用，需自备 DeepSeek API key，**按量付费**；
>   不设它语音/文字指令会"解析失败"。详见 [二、自行部署](#二自行部署)）
> - 开发/贡献者另见 [CONTRIBUTING.md](CONTRIBUTING.md)

本文档按三类人群组织：

- [一、普通玩家：怎么玩](#一普通玩家怎么玩) —— 你只想上手用
- [二、自行部署](#二自行部署) —— 你想自己搭一套（本地一键脚本 / 公网）
- [三、开发者：系统架构](#三开发者系统架构) —— 你想了解它怎么实现的

---

# 一、普通玩家：怎么玩

### 最简情况：别人已经搭好了服务器

你只需要：**手机浏览器打开主办方给你的链接** → 输入昵称 → 选服务器 → 连接。然后就进了
驾驶舱，开始指挥。不需要装任何东西。（想自己当主机搭一套 → 见 [二、自行部署](#二自行部署)。）

### 游戏体验

```
  手机（唯一控制器，不碰 PC 键鼠）
   │  ① 入口页：输昵称 + 选服务器 → 连接
   │  ② 房间大厅：选种族 / 准备 / 房主开局（多人）
   ▼  ③ 驾驶舱（Cockpit）
  ┌──────────────────────────────────────────────┐
  │ 实时画面（SC2 直播）+ 战况快照                  │
  │ 语音长条（按住说话）/ 文字输入                  │
  │ 全军战术按钮：进攻 · 防守 · 撤退                 │
  │ 指令卡片列表（每条可 × 取消）                    │
  │ 小地图（拖拽切镜头）+ 宏观策略面板 + 编队条      │
  └──────────────────────────────────────────────┘
```

### 分工：你管战略，AI 管执行

| | 你 | AI（基础 bot） |
|---|---|---|
| 后勤（补农 / 补给 / 气矿 / 扩张） | — | 100% 包了 |
| 建造 / 兵种生产 | 决定大方向 | 按剧本严格执行 |
| 基础战斗（集火 / stutter / 撤退） | — | 基础水平自动 |
| 战略决策（攻击 timing / 转科技 / 扩张点） | **核心价值在这** | 默认保守 |
| 高难微操（举重 / 力场 / 风暴 / 闪烁切入） | **必须手动下令** | 默认不主动用 |

bot 默认能赢 Medium AI、vs Hard 五五开；你参与越深，能打越高难度。

### 你能下哪些操作

**UI 操作（驾驶舱）**

| 操作 | 说明 |
|---|---|
| 全军战术按钮 | 进攻 / 防守 / 撤退（持续姿态，作用于自由单位） |
| 指令卡片 × | 每条已下达指令一张卡，右上角 × 随时取消 |
| 小地图拖拽 | 切游戏镜头视角 |
| 语音长条 | 按住说话（App 内语音识别）/ 也可用手机输入法语音转字 |
| 文字输入 | 直接打字下指令 |
| **语言切换（中/EN）** | 右上角切换；界面 + 指令 + 语音识别全英文，持久化 |
| 宏观策略面板 / 编队条 / 产能面板 | 看 / 切剧本、看 1-5 队、看调产能 |

**语音 / 文字指令 —— 你能说的所有命令**

下面是**当前 bot 真正能执行**的全部指令类型，每类给真实例句。直接说人话即可，不用背格式；
说不清 bot 会反问澄清或提示。**带"这里 / 这边 / 这个位置"的指令，要先用小地图把镜头移到目标点**
（"这里"= 你当前镜头中心，bot 自动读坐标）。每条下达后在驾驶舱生成一张**指令卡片，右上角 × 随时取消**。

先看四层粒度速览，再往下是分类详表：

| 层 | 管什么 | 例子 |
|---|---|---|
| L1 宏观策略 | 整局打法（剧本） | "上 4BG"、"切双矿凤凰"、"转 Skytoss" |
| L2 全军战术 | 整支军队的姿态 | "进攻二矿"、"全军撤退"、"原地坚守" |
| L3 单位 / 编队任务 | 指定单位干具体活 | "DT 守气矿别动"、"虚空一队进攻三矿"、"派农民去前线修水晶" |
| L4 产能 / 经济 | 出兵·补建筑·升级·扩张 | "造 4 个叉子"、"补 8 BG"、"先研闪烁"、"在这偷矿" |

---

**① 切剧本 / 宏观打法（L1）**

| 你这样说 | 效果 |
|---|---|
| "上 4BG"、"切双矿凤凰"、"转 Skytoss"、"打航母" | 切到对应剧本，bot 按它出兵造建筑（三族 40+ 剧本，见下方剧本库） |
| "取消剧本"、"先别按剧本走"、"停止刷兵" | bot 转保守运营：只补农守家，不主动出门 |

**② 全军战术姿态（L2，对应驾驶舱三个大按钮）**

| 你这样说 | 效果 |
|---|---|
| "进攻对方二矿"、"A 上三矿"、"全军压上去" | 全军 committed 进攻指定区域（打到底） |
| "试探进攻二矿"、"推上去看看"、"前压试试" | 全军试探：占便宜就占，打不动就撤（见势不妙自动退） |
| "强攻 / 一波流 / 不要命冲" | all-in 强冲，不撤 |
| "守家"、"全部回家防守"、"守一波" | 全军回主基地一线防守 |
| "持续守家姿态"、"保持防守" | 一直保持守家姿态（打完一波继续守） |
| "全军撤退"、"全部撤回基地"、"回家" | 全军撤回（普通移动，遇敌不恋战） |
| "原地坚守"、"守住别动"、"到斜坡堵口"、"守三矿别走" | 聚团到指定点站住不回家 |

> 全军命令只作用**自由单位**；被你单独点名控制 / 编了队的单位不受影响（要它听全军命令先取消那条独占）。

**③ 派兵打出去：进攻 / 骚扰 / 火力侦查（L2-L3）**

| 你这样说 | 效果 |
|---|---|
| "派 5 个凤凰去骚扰对方主基地"、"飞龙骚扰对面上边分矿" | 指定数量的部队去骚扰（**骚扰必须说数量**，否则 bot 反问） |
| "凤凰打死对方 5 个农民就回" | 骚扰带退出条件（杀够就撤） |
| "火力侦查对方三矿"、"派 4 个追猎前压看看" | 小股部队前压试探，占便宜 / 损耗大 / 超时 任一就退 |
| "残血的追猎撤回来"、"盾破的虚空拉回基地" | 按状态选单位撤退（血量低 / 护盾破） |
| "前线那个追猎撤退"、"最前面的叉子退回来"、"后面那个不朽顶上去" | 按当前**物理位置**选最前 / 最后那个单位 |

**④ 单位待命 / 集结 / 守点 / 站桩（L3）**

| 你这样说 | 效果 |
|---|---|
| "派一个农民到这里待命"、"叉子到对方三矿待命" | 单位移到目标点留守，受敌自动还击、战后归位（持续到 ×） |
| "所有虚空到这里集中"、"叉子都聚过来这里" | 把**现有**这批兵聚到一点独占停留（等你后续指令） |
| "DT 守气矿别动"、"探机守瞭望塔别动" | 钉在原地守住（hold，不主动出击） |
| "2 个追猎去守 5 点分矿"、"叉子在 7 点守" | 守某区域（受敌还击并归位，guard） |
| "派一个追猎站左边瞭望塔"、"使徒去右边瞭望塔" | 占瞭望塔（watchtower / 左 / 右） |
| "让农民蹲对方右下分矿"、"探机去对方 11 点盯着" | 蹲点放视野（方位词 / 钟点都认） |

**⑤ 巡逻（两点往返，L3）**

| 你这样说 | 效果 |
|---|---|
| "农民在对方 11 点分矿和三矿之间巡逻" | 单位在两点之间持续往返巡逻 |
| "3 个凤凰在二矿和对方主基地之间巡逻" | 多单位巡逻线 |
| "追猎在这里和对方三矿之间巡逻" | 第一个点用"这里"（当前镜头） |

**⑥ 出兵集结点（rally，管未来新出的兵，L3）**

| 你这样说 | 效果 |
|---|---|
| "集结点设在这里"、"以后出的兵都到这里集合"、"新兵集结点放这" | 设**全局集结点**：之后新造出来的兵默认去那（不动现有兵） |

> 区别："〈某兵种〉到这里集中"是把**现有**的兵聚过去（④）；"集结点 / 出兵都去"是管**未来新兵**去哪（⑥）。

**⑦ 语音编队（1-5 队，L3）**

| 你这样说 | 效果 |
|---|---|
| "把运输机编成 1 队"、"运输机 1 队" | 把这批单位编进 1 队（编队条会显示各队成员） |
| "把 2 个农民编成 3 队" | 只编指定数量进队 |
| "以后新出的虚空都编入 1 队"、"将来造的追猎自动加 2 队" | 持续征兵：新出的兵自动入队 |
| "1 队进攻对方三矿"、"2 队火力侦查这里"、"3 队撤退" | 直接对某队下指令（进攻 / 侦查 / 回防） |
| "释放 2 队"、"取消 2 队"、"清除 2 号队" | 解散编队，单位交还 bot |

**⑧ 侦察 / 视野（L2-L3）**

| 你这样说 | 效果 |
|---|---|
| "看一眼对方主基地"、"侦察一下二矿"、"扫一下三矿" | 派 1 单位走一趟，到了拿到视野即收工 |
| "盯着对方主基地"、"保持二矿视野"、"持续看对方升科技" | 派单位持续保持某区域视野 |
| "派探机侦察 11 点"、"派一个探机去 11 点看看" | 指定单位去某方位 / 钟点侦察 |
| "让探路农民回来"、"探路兵别探了" | 撤回正在探路的农民（回家采矿） |
| "探路农民别探了，去占右边瞭望塔" | 把探路农民**改派**去做新任务（不回家） |

**⑨ 出兵 / 补建筑 / 升级 / 暂停产线（L4 产能）**

| 你这样说 | 效果 |
|---|---|
| "出 2 个哨兵"、"造 4 个叉子"、"出 2 叉子加 3 追猎" | 加产指定兵种数量（出齐即消） |
| "刷两个叉子到前线"、"折跃 3 追猎去二矿" | 折跃门直接把新兵生产到指定落点 |
| "补一个 BF"、"补两个 VS"、"再来一个气矿" | 增量补建筑（在 N 个基础上 +N） |
| "补到 8 个 BG"、"凑齐 14 个 BG" | 补到绝对总数 |
| "二矿补 2 气矿"、"ramp 放 2 炮 1 BF" | 指定地点补建筑 |
| "先研闪烁"、"升级地面攻击"、"补个 BY 然后升空军攻防" | 研究升级（"攻防"= 攻击 + 护甲两条） |
| "暂时不出追猎"、"停止造叉子"、"先别造哨兵" | 暂停某兵种产线（× 才恢复） |

**⑩ 野建筑 / 代理建造（派农民出去建，L3-L4）**

| 你这样说 | 效果 |
|---|---|
| "派农民去前线修个水晶" | 派 1 农民去目标点修水晶，全程不被拉走，建完留原地待命 |
| "派农民去对方 6 点分矿修个水晶，水晶好了在旁边修个 BG" | 链式：先水晶，水晶建好后在能量场内接着修后续建筑 |
| "派个农民去这里修个水晶，然后修两个 VS" | "这里"= 当前镜头；水晶 + N 个建筑用同一个农民依次建 |
| "再到这里修一个 VS" | 复用正在外面建造的那个农民，追加一个建筑 |

> 神族机制：后续建筑必须在水晶能量场内，所以代理建造总是**先水晶、再建筑**，bot 自动排好位置不撞格。

**⑪ 偷矿（隐蔽采矿点，L4）**

| 你这样说 | 效果 |
|---|---|
| "在这偷矿"、"在这里偷一个矿"、"开隐蔽基地" | 先把镜头移到目标矿区，再说 —— 派约 16 农民去隐蔽采矿，受攻击自动逃 |
| "在这偷矿，多派点农民"、"偷个矿给 20 个农民" | 调农民数（默认 16，最多 24） |
| "在这偷矿，不要偷气" | 只偷矿不偷气（默认连气一起偷） |
| "偷这里和对方三矿各一个" | 多片需分两次说、各自先移镜头 |

> 区别："开三矿 / 再开个矿"是 bot 常规扩张（⑫）；"偷矿"是去对方盲区 / 远矿藏一个隐蔽采矿点。

**⑫ 开矿 / 扩张（L4）**

| 你这样说 | 效果 |
|---|---|
| "在这开矿"、"在这下主基地"、"这片矿开了" | 看着一片矿区说 —— 派农民去那下基地开矿（落点 bot 自动摆正，太歪会弹确认） |
| "再开个矿"、"扩一个"、"开三矿" | 不指地点 —— bot 自己选下一个分矿点扩张 |

**⑬ 高难技能释放（必须你手动下令，L3）**

| 你这样说 | 效果 |
|---|---|
| "给两个 BF 星空加速"、"给星门加速" | Nexus 对建筑放星空加速（chrono） |
| "所有电兵合成白球"、"合 2 个白球" | 高坦两两合体成白球（Archon） |
| "电兵放心灵风暴到对方主基地" | HT 放风暴（要给落点） |
| "叉子闪到对方主基地"、"闪追闪进去" | 追猎闪烁（需 Blink 已研） |
| "枪兵嗑药冲"（人族）、"飞蛇拉对面航母"（虫族） | 兴奋剂 / abduct 等各族技能 |

**⑭ 镜头跟随（L3，只动镜头不动兵）**

| 你这样说 | 效果 |
|---|---|
| "镜头跟着大部队"、"看主力部队" | 镜头跟全军质心 |
| "镜头跟着追猎"、"盯住那个凤凰"、"跟随母舰" | 跟某兵种 |
| "跟着火力侦查那波"、"跟骚扰小队" | 跟当前侦查 / 骚扰小队 |
| "镜头跟随探路农民"、"跟守瞭望塔的"、"跟巡逻的" | 按任务身份跟（不是跟采矿的同名单位） |

**⑮ 连续指令 / 多步串联（L3）**

| 你这样说 | 效果 |
|---|---|
| "农民先去右瞭望塔，再去对方 11 点，然后在对方二矿修个水晶，最后回家采矿" | 一个农民按顺序走多步，每步完成自动触发下一步 |

**⑯ 释放 / 撤回单位**

| 你这样说 | 效果 |
|---|---|
| "那个叉子回来"、"释放所有虚空" | 撤销该单位身上所有在途指令，完全交还 bot |

**⑰ 回收 / 拆建筑（人族，拿回部分矿，L5）**

| 你这样说 | 效果 |
|---|---|
| "把地堡卖了"、"回收那个碉堡"、"拆掉地堡" | 对自己的建筑下回收技能（地堡 / 感应塔），拿回部分矿；不能回收的会友好提示 |

**⑱ 镜头框选（"镜头内的 X …"，跨指令的选择方式）**

| 你这样说 | 效果 |
|---|---|
| "把镜头内的追猎编成 2 队"、"镜头里的兵全部进攻这里"、"镜头内的地堡都回收了" | 只对**说话那一刻镜头里看到的**那批单位/建筑动手（不波及画面外同类） |

> 先用小地图把镜头挪到目标区域再说。和落点"这里/这边"不同——"镜头内的 X"是**框选一批**，
> 两者可同句组合。必须说清是哪种兵/建筑或"兵"（军队）。

**别名约定**：建筑用 hotkey 缩写（BG=兵营 / BE=水晶 / VR=机械台 / VS=星门 / BY=控制核心 …），
单位用中文黑话（叉子 / 不朽 / 追猎 / 闪追 / DT / 小狗 / 刺蛇 / 飞龙 …），战术黑话保留
（4BG / IAC / Skytoss / 12pool / 两矿凤凰 …）。说了非本族单位 / 建筑会被友好拒绝。

### 玩家控制权（四条规则）

1. **单位级指令 = 独占 + 最新覆盖**：给已被控制的单位下新指令会抢占它。
2. **全军命令不碰被独占的单位**：只作用自由单位；要它听全军命令先取消独占 / 解散编队。
3. **释放 / 解散单位 → 撤销它身上所有在途指令**，还给 bot。
4. **撤退用普通 move（遇敌不恋战），不 attack-move。**

**优先级金字塔**：没你的指令 → bot 全自决；你锁住的部分 bot 不动、其余仍自主；你撤销 →
自决策权浮回来。

### 剧本库（三族 40+）

- 神族：4bg, 1g_robo_immortal, iac_2base, immortal_archon, colossus_immortal, blink_stalker,
  blink_harass, dt_rush, phoenix_2base, skytoss, void_ray_rush, cannon_rush …
- 人族：one_one_one, two_one_one, bio_stim, mech, two_base_tanks, banshee_harass, marine_rush,
  liberator, widow_mine_drop, ghost_nuke, bc_late …
- 虫族：12pool, macro_hatch, zvp_macro(ZvP 运营流), ling_bane, muta_ling_bane, roach_hydra,
  roach_ravager, lurker_hydra, mutalisk_harass, nydus, ultralisk, brood_corruptor …

---

# 二、自行部署

你想自己当主机给朋友玩。分两块：**本地单机**（你和朋友同 wifi / 装 Tailscale 就够）和
**公网**（国内手机直连、无需 Tailscale）。server 代码同一套，区别只是手机怎么连到你 PC。

## A. 本地单机（Windows）—— 一键脚本最省事

### 第 0 步：前置

- **装好 StarCraft II** 并**至少启动过一次**（这样 `Documents\StarCraft II\ExecuteInfo.txt`
  会记录安装位置，一键脚本能自动找到）。
- 把对战地图（默认 `DaybreakLE`）放进 `<SC2安装目录>\Maps\`。
- 装 [`uv`](https://docs.astral.sh/uv/)（包管理器）；配一个 LLM API key（解析指令用）。

### 第 1 步：一键配置脚本 ⭐

仓库根目录开 PowerShell，跑：

```powershell
.\scripts\setup-windows.ps1
```

它一把搞定（幂等，可重复跑）：
1. **自动定位 StarCraft II** 安装位置（SC2PATH → `ExecuteInfo.txt` → 注册表 → 常见目录），
   找到后**持久化 `SC2PATH`** 环境变量。找不到会提示你先启动一次 SC2 或手动设。
2. **修"闲置后直播黑屏"**：插电时**永不关显示器 / 永不睡眠 + 关屏保**（根因：Windows 闲置
   关显示器 → SC2 停渲染 → 抓屏抓到黑帧，远程登录唤醒才回画面）。
3. 检查 `uv` 和 `DEEPSEEK_API_KEY`，缺了给安装/设置指引。

> 若黑屏依旧，可能是 Windows **锁屏**（不是关显示器），需另外关掉自动锁屏。

### 第 2 步：装依赖 + 启动

```powershell
uv sync --extra dev                       # 首次 / 依赖变更
.\scripts\start.ps1 -Token vibecraft-dev  # 启动 server（打印二维码 + URL）
# 可选：有英文玩家时提前预拉英文 ASR 模型（~1 GB，首次约 6 分钟，之后缓存秒载）
.venv\Scripts\python.exe scripts\prefetch_asr_en.py
```

手机**同 wifi**扫码 / 打开 URL 即玩。要外网但不搭公网：两端装 **Tailscale**，手机走 Tailnet 连。

## B. 公网（买了云 VPS 之后，国内手机直连）

让一台**香港云 VPS**（免备案、对华网络好）做**媒体中继 + 公网前门**，你 PC 在 NAT 后**主动
出站**连 VPS。部署架构图见 [`ARCHITECTURE.md` → 部署架构](ARCHITECTURE.md#部署架构)。

**1) 买 VPS** —— 按 [`docs/ops/vps-purchase-spec.md`](docs/ops/vps-purchase-spec.md)：香港、
2C2G、Ubuntu 22.04、带宽 ≥30Mbps / 不限流量、公网 IP、免备案（约 ¥60-110/月）。

**2) VPS 上装中继 + 前门**（脚本拷到 VPS，按顺序跑，IP/域名按你的改）：

```bash
bash setup-coturn.sh        # coturn：STUN/TURN + turns:443 + 短期凭证 + 防 SSRF
bash setup-frontdoor.sh     # nginx 443 SNI 分流（turn.*→coturn / app.*→反向隧道）+ 证书
```

**3) PC 配置 + 起反向隧道**（在你 PC 上）：

```powershell
# a. 填 .secrets\vibecraft-turn.env（从 deploy\turn\vibecraft-turn.env.example 复制）：
#    TURN_DOMAIN / TURN_STATIC_SECRET（VPS 上 cat /etc/vibecraft-turn-secret）/ 端口
# b. 起 PC→VPS 反向隧道（断线自动重连）：
.\deploy\turn\pc-tunnel.ps1
# c. 起 server（自动读 .secrets 的 TURN 配置；缺失则降级纯 P2P，行为不变）：
.\scripts\start.ps1 -Token <房间码>
```

手机连 `https://app.<你的IP>.sslip.io/?room=<房间码>`：控制面经 nginx→反向隧道→你 PC，
媒体面 P2P 优先、打不通走 `turns:443` 中继。详细方案
[`docs/plans/2026-06-14-turn-integration-plan.md`](docs/plans/2026-06-14-turn-integration-plan.md)。

**4) 成本估算（重点：流量）**

VPS 上跑的是 coturn(TURN/STUN) + nginx 前门 + SSH 反向隧道，**CPU/内存几乎不吃**（中继 1-2 路
~2Mbps 视频对 2C2G 是富余，规格不是瓶颈）。真正的变量成本是**公网流量**，而流量大小**完全取决于
视频走 P2P 直连还是 TURN 中继**：

| 媒体路径 | 触发条件 | VPS 流量 |
|---|---|---|
| **P2P 直连**（不过 VPS） | 手机和 PC 同 wifi，或**手机装了 Tailscale**，或 NAT 穿透成功 | **≈ 0**（只过信令 HTTP/WS，KB 级，可忽略） |
| **TURN 中继**（全程过 VPS） | 双方都在家宽 CGNAT 后、P2P 穿不过（**国内家庭网络常见**） | 视频码率 × 时长 |

**TURN 中继时的流量量级**（控制面/信令可忽略，几乎全是视频）：

- 视频默认 ~1-2 Mbps（`-Quality` 模式 15fps 更省、坏网自动降帧）。单人观看（1 部手机）→ VPS
  **出向 ≈ 0.7-1 GB / 小时**；多人同屏（2 部手机各收一路）≈ **1.5-2 GB / 小时**。
- 换算月成本（按每天玩 2 小时、单人）：≈ **40-60 GB / 月**。
  - **按量计费**（公网流量约 ¥0.5-1/GB，看服务商/区域）：≈ **¥20-60 / 月** 流量费。
  - **不限流量 / 固定带宽包月**（`vps-purchase-spec.md` 推荐）：流量已含在 ¥60-110/月 实例里，
    **多玩不额外加钱**，省心；轻度用按量可能更便宜，重度用包月更划算。

**省流量 / 省钱建议**：
- **手机装 Tailscale** → 媒体走 Tailnet 直连、**绕开 TURN**，VPS 流量几乎归零（只剩免费信令）。
  这是最省的路子，VPS 退化成纯信令前门。
- 和 PC **同一个 wifi** 时直接用局域网 URL（`http://<PC内网IP>:8080`），**根本不用 VPS**。
- 真要靠 VPS 中继（手机在外网 + 没装 Tailscale），用 `-Quality` 降帧率压流量。

一句话：**VPS 月租 ¥60-110 是固定的；流量只有"手机外网 + 没装 Tailscale + P2P 穿不过"时才显著
（~1 GB/小时），其余情况几乎零流量。**

---

# 三、开发者：系统架构

一句话：**手机指令 → LLM 解析成 Directives JSON（唯一中间表示）→ Director 每帧仲裁 → 通过
ares/sharpy hook 操作 SC2；SC2 画面/声音经 WebRTC 推回手机。**

```
手机 PWA ──WS──► server（PC 上）
   │  指令文本/语音 → LLM 解析 → Directives JSON
   ▼
Director（每帧 tick）──► Directive Board（仲裁/优先级/激活门）
   ├─► ares-sc2 6 hook 点（Build Runner / OverrideMediator / Unit Role /
   │     Rationale Log / ViewController / BuildLocationOverride）
   ├─► sharpy combat plan（vendor fork，玩家覆盖 hook 直接加在 plan 内）
   └─► Sc2Facade ──► python-sc2 / ares ──► SC2 客户端
   ▲
   └── SC2 画面/声音 ──WebRTC（按进程 PID 抓屏+音频）──► 手机
```

**技术栈**

| 层 | 用什么 |
|---|---|
| Bot / 引擎 | ares-sc2, python-sc2(BurnySc2), sharpy(fork), Python 3.11 |
| 中间表示 | pydantic Directives + 别名 YAML + 剧本 YAML |
| 实时流 | aiortc（WebRTC 视频+音频，按 PID 抓屏 / WASAPI process loopback） |
| 信令 / Web | websockets（HTTP+WS 同端口）, Vue 3 + Tailwind PWA |
| 语音 ASR | 中文：FunASR `paraformer-zh-streaming`（流式）；英文：`SenseVoiceSmall`（离线，~1 GB，`scripts/prefetch_asr_en.py` 预拉）|
| 中继 / 公网 | coturn（TURN over TLS:443）+ nginx（SNI 分流前门）+ SSH 反向隧道 |
| LLM | 云端，provider 可配（当前 DeepSeek V4 走 Anthropic 兼容端点） |
| 日志 | structlog JSONL（每次 LLM 调用 / directive 进出 Board / hook 触发全量落盘） |

**要点**：`LLM_CONTROLLED` role 让 base bot 默认 skip、玩家指令优先；复杂动作靠"现有 directive +
`activate_when` 条件门"串联，不轻易新增类型；多人是单 SC2 客户端多实例 host/join + `Room`
状态机 + per-player WS 路由 + WebRTC per-player PC（按 SC2 窗口 PID 抓屏，各看各的）。

> **完整模块图、运行时数据流、关键不变量、6 hook 映射、偷矿/代理建造/多人/部署架构图**：
> 见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。设计真理源见
> [`docs/plans/2026-05-14-vibecraft-design.md`](docs/plans/2026-05-14-vibecraft-design.md)。

### 开发命令

```bash
uv sync --extra dev                  # 同步开发依赖
uv run pytest                        # 全部单测（mock，无需 SC2）
uv run pytest -m integration         # 集成层（mock python-sc2）
uv run ruff check . && uv run mypy src/vibecraft   # lint + 严格类型
uv run python scripts/download_sc2_icons.py   # 拉 SC2 图标（首次；版权美术不入库，见 THIRD_PARTY_NOTICES）
cd web && npm run build              # 构建 PWA（写入 server/static）
```

ares-sc2 / burnysc2 不在 PyPI：`uv pip install "git+https://github.com/AresSC2/ares-sc2@main"`。

**真局自验**（mock LLM，non-realtime，可并行多开）：

```bash
.venv/Scripts/python.exe scripts/build_acceptance.py <strategy_id> --opponent veryeasy
.venv/Scripts/python.exe scripts/override_acceptance.py <case_id> --opponent veryeasy
.venv/Scripts/python.exe scripts/multiplayer_selftest.py
```

### 目录布局

```
src/vibecraft/
  directives/  # Directive 数据模型 + Board（唯一中间表示）
  strategy/    # 剧本库 + YAML schema + 别名解析
  dsl/         # 条件 DSL（activate_when / done_when）
  llm/         # Intent Parser + Provider 抽象
  bot/         # VibeCraftBot（ares-sc2 子类）+ Director + 6 hook + auto_combat
  server/      # WS+HTTP service + WebRTC + ASR + 多人 Room + PWA static
  logging_/    # 结构化 JSONL 日志
web/           # Vue 3 + Tailwind PWA 源码（构建后入 server/static）
strategies/    # 剧本 YAML（protoss / terran / zerg）
deploy/turn/   # 公网部署脚本（coturn / nginx 前门 / 反向隧道）
scripts/       # setup-windows / start / 自验脚本
vendor/sharpy/ # sharpy fork（玩家覆盖 hook 直接加在 combat plan 内）
tests/{unit,integration,e2e}/
```

### 文档导航

| 文档 | 内容 |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 模块图 / 数据流 / 不变量 / 6 hook / **部署架构图** |
| [`docs/plans/2026-05-14-vibecraft-design.md`](docs/plans/2026-05-14-vibecraft-design.md) | 14 节完整设计真理源 |
| [`USER_GUIDE.md`](USER_GUIDE.md) | 玩家入门 + 话语示例 + FAQ |
| [`CLAUDE.md`](CLAUDE.md) | 约定 + 指针（AI 协作上下文） |
| [`TASKS.md`](TASKS.md) / [`CHANGELOG.md`](CHANGELOG.md) | 当前状态 / 版本历史 |

---

## 许可 / License

- **VibeCraft 自身源码：MIT**（见 [`LICENSE`](LICENSE)）。
- **第三方组件**（含随仓库打包、且被加了 hook 的 sharpy-sc2，以及各 pip / 前端依赖）的协议
  与合规说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。所有依赖均为宽松协议
  （MIT / BSD / Apache-2.0），**无 copyleft**；vendored 的 sharpy 是 MIT（© 2019 DrInfy），
  修改后仍合规（保留原协议 + 标注改动）。
- **StarCraft II / Blizzard**：VibeCraft 是**非官方粉丝项目**，与 Blizzard 无隶属/背书。
  StarCraft®、Blizzard® 是 Blizzard Entertainment 的商标。运行需你**自有正版 StarCraft II**；
  通过其 AI/ML API 操作游戏受 Blizzard **"StarCraft II AI and Machine Learning License"** 约束，
  **仅限非商业的研究 / AI 用途**（并遵守 SC2 EULA）。MIT 仅覆盖 VibeCraft 源码，不授予对
  StarCraft II / Blizzard 知识产权的任何权利。

---

> 状态：神族完整、人/虫族剧本库齐、多人联网 + 公网部署（云中继 + 反向隧道）已真机验证
> （国内手机直连）。开源中（MIT）。
