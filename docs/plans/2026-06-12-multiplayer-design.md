# 多人联网设计（2026-06-12 brainstorming 定稿）

> 上游决策见 `docs/plans/2026-05-14-vibecraft-design.md` §1.3（产品边界）。本文档是
> 「开源前三大方向」之三——多人联网——的设计真理源。用户逐项拍板于 2026-06-12 session。

---

## 1. 目标与用户拍板的关键决策

| 决策点 | 拍板结果 |
|---|---|
| 对局形态 | **一台 PC（或未来云主机）跑多个 SC2 实例，LAN host/join 成一局**；每实例一个 bot，多个玩家各用手机接入指挥 |
| 画面分发 | **与单机一致**：每路 SC2 默认视频+音频 WebRTC 推流到对应玩家手机，可关 |
| 公网组件 | ~~Vercel~~ → **分阶段**：阶段 0 零成本（LAN/Tailscale）；阶段 1 一台新加坡轻量 VPS 跑「会合服务」（房间目录 + WS 转发 + WebRTC 信令 + coturn TURN）；阶段 2 会合服务可自托管（开源用户自己跑） |
| 局型 | **通用 lobby**：支持分队，1v1 / 多人混战 / 组队；slot 可填真人指挥的 bot 或内置 AI |
| 玩家身份 | **v1 不做真实认证**：PWA 入口页输入用户名 + 选择服务器即可进 |
| 规模 | 先 2 人，逐步到 4-8 人 |

### 1.1 为什么不是 Vercel（决策记录）

Vercel 是 serverless：**不能托管长连接 WebSocket、不能跑 UDP（TURN）**——恰好是多人实时
链路需要的 90%。且 `*.vercel.app` 国内不可达，与「未来国内朋友也能连」冲突。一台新加坡
轻量 VPS（~US$5/月）三样全包（长连接转发 / 信令 / TURN），国内可达、本地（新加坡）延迟
几 ms，且与「开源后用户自托管」形态一致。

### 1.2 网络原理速记（为什么必须「PC 主动连出去」）

- 用户家用**移动网络 = CGNAT**：无公网 IP、无法端口转发，外部「主动连进来」被运营商 NAT
  直接丢弃；NAT 不挡「主动连出去」。
- 三种基本姿势：① **反向隧道**（PC 主动连公网中转机并保持长连接，流量倒灌——Tailscale
  funnel / cloudflared / frp 同类，代价是全部流量过中转）② **P2P 打洞**（双方互发包在各自
  NAT 凿洞后直连，需信令服务牵线，~80-90% 成功，CGNAT/蜂窝网最难打）③ **TURN 中继**
  （打洞失败的标准兜底，WebRTC 自动切换）。
- 终态架构 = ② 为主 + ③ 兜底 + 小信令服务，即所有视频会议软件的标准做法。
- **控制消息量小 → 直接全走 VPS WS 转发（姿势①），现有 WS 协议不用改成 DataChannel**；
  视频量大 → 维持现有 WebRTC（姿势②），TURN 兜底（姿势③）。

---

## 2. 分阶段路线

| 阶段 | 内容 | 网络要求 |
|---|---|---|
| **0（本设计主体）** | 服务端多人化：房间/slot 模型、多 SC2 实例 host/join 编排、按玩家路由、PWA 入口页+lobby | 零——同 WiFi 或现有 Tailscale |
| **1** | 会合服务上 VPS（房间目录 + WS 转发 + 信令 + coturn）；PC 端加「反向注册」（开机主动连 VPS 的一条 WS 长连接） | 新加坡轻量 VPS |
| **2（开源前）** | 会合服务可自托管化（Docker 一键 / 公网 Windows 直跑）+ 简单账号 | — |

阶段 0 与公网方案**完全解耦**：房间层消息走统一 `PlayerChannel` 抽象，阶段 1 把「本地 WS
连接」换成「经 VPS 转发的连接」时房间层零改动（落实 CLAUDE.md「服务端协议必须假定可能被
远程客户端连接」纪律）。

---

## 3. 阶段 0 架构

```
玩家手机 A ──┐                       ┌─ GameProcess #1 (bot A + SC2 实例1, host)
玩家手机 B ──┤── server (一个进程) ───┤─ GameProcess #2 (bot B + SC2 实例2, join)
玩家手机 C ──┘   ├─ RoomManager      └─ (内置 AI 不占进程,由 host 实例建局时代填)
                └─ MatchOrchestrator
```

现有「一部手机 ↔ 一个 GameProcess」链路（指令/快照/视频）整体保留，从单例变成**按
player_id 路由**。新增两个组件，其余不动：

- **RoomManager**：房间状态机 `LOBBY → STARTING → IN_GAME → ENDED` + slot 模型。
- **MatchOrchestrator**：把房间配置翻译成 SC2 启动计划——1 个 host 实例 + N-1 个 join
  实例（共享 PortConfig 组成一局多人游戏），窗口按现有 tiling offset 摆放，局终统一回收。

### 3.1 PWA 入口页（用户名 + 服务器选择）

```
PWA 首屏
  ├─ 用户名输入（localStorage 持久化；player_id = 用户名 + 设备指纹，无密码）
  ├─ 服务器列表（localStorage 持久化）：每条 = { 名称, 地址(ws/https URL), 房间码 }
  │    ├─ 手动添加（输地址）
  │    └─ 扫码 = 快捷「添加并选中」（现有二维码流程降级为录入方式之一）
  └─ [连接] → 进所选服务器的房间 lobby
```

开源多服务器形态的落点：任何人跑一个 server，朋友在入口页加一条记录即可连，PWA 与具体
服务器解耦。用户名仅做显示与 slot 绑定，真实账号留阶段 2；同名用设备指纹区分。

### 3.2 房间 / slot 模型（SC2 经典 lobby）

```
房间 = { 房间码(沿用现有 token), 地图, 模式, slots[2..8] }
slot = { 类型: 玩家bot | 内置AI(难度) | 关闭,
         队伍: 1/2/3..., 种族: P/T/Z/随机,
         绑定: player_id(玩家bot 时) }
```

- 手机连上 server 即进房间；自己挑 slot/队伍/种族；房主（第一个进的）可加内置 AI、踢人、
  改图、点开始。
- 全员 ready → 房主 start → STARTING（实例拉起+互联约 30-60s，PWA 显示进度）→ IN_GAME。
- **v0 简化**：一个 server 同时只跑一个房间（一台 PC 只扛得动一局）；多房间留给阶段 1。

### 3.3 对局编排（host/join，关键技术点）

python-sc2 跨进程联机：两个 SC2 实例共享一组 **PortConfig**，实例 1 `host_game` 建局、
实例 2 `join_game` 加入（debug draw 验证时跑过 2-bot versus，但当时是单脚本进程；这次要
跨 GameProcess 子进程——TASKS.md 标的「链路验证」项）。

- **Spike #1（先行）**：`scripts/multiplayer_smoke.py`——两个 GameProcess 子进程
  host/join 打一局 bot vs bot 跑 5 分钟，验证：稳定性、HangWatchdog 在多人局的行为、
  一方崩溃对另一方的影响。通过后才动房间层。
- 内置 AI：host 建局时塞 `Computer(race, difficulty)`，零额外进程。
- 资源预算：1v1 = 2 realtime SC2 + 2 bot + 2 路编码，游戏 PC 可行；4 人局起实测上限
  （non-realtime 8 实例验证过，realtime 渲染+推流另算），slot 数不设硬上限、实测为准。

### 3.4 数据与媒体路由（按 player_id 分流）

> **2026-06-12 Opus 评审修订**：match 生命周期由 RoomService 的 **per-match monitor**
> （connection 无关的 asyncio task，每 GameProcess 恰一个消费者）驱动，WS 连接只订阅
> 下行帧——断线不失管、重连自动续上；GameProcess 唯一 owner = MatchOrchestrator（solo
> 也走它，旧 start_game 帧为薄 shim）；WebRTC PeerConnection 按 player_id 管理（新 offer
> 只 supersede 同玩家）；realtime 是房间配置不写死。详见实施 plan「评审修订」节。

| 通道 | 现状 | 改动 |
|---|---|---|
| 指令（文字/语音） | 手机 → 唯一 GameProcess | 手机 → **自己 slot 绑定的** GameProcess |
| 快照/旁白/面板 | 唯一 GameProcess → 手机 | 各 GameProcess → 各自玩家手机 |
| 视频 | 抓唯一 SC2 窗口推流 | **按实例窗口（PID→HWND）分别抓屏**，各推各的玩家 |
| 音频 | 抓系统音频 | ⚠️ 两实例同机出声会混；v0：**只有 host 玩家的实例开声音，其余静音启动**；后续研究 per-process loopback（Win10+ 支持按进程抓音频） |
| 日志 | `logs/<game_id>/` | 一局一个 match_id，按 player 分目录；公平性回放（commands.jsonl）天然分开 |

公平性机制（同 LLM provider、APM cap、10s 限频）均为 per-bot 配置；房间层保证「同房间
所有玩家 bot 用同一套公平性配置」，设为房间配置项，v0 写死默认值。

### 3.5 风险（按优先级）

1. **跨进程 host/join 稳定性**（spike #1，最大未知数）；
2. **一方掉局/崩溃**：多人局里一个实例崩，SC2 判负还是整局挂？spike 专测；兜底 = 崩溃方
   判负、房间回 LOBBY；
3. **双实例 realtime 性能**（渲染 ×2 + 编码 ×2），spike 顺带测帧率；
4. 音频混流（v0 简化方案见 3.4）。

### 3.6 测试策略

- 单测：RoomManager 状态机/slot 仲裁（纯逻辑 mock）；MatchOrchestrator 启动计划生成
  （给房间配置断言端口/参数，不真起 SC2）。
- 自验脚本：`multiplayer_smoke.py`（2 bot host/join）+ 注入指令版（两个 mock-LLM 玩家
  各下指令，验路由不串线——A 的指令绝不能跑到 B 的 bot 上）。
- 真人测试：用户 + 本地朋友，同 WiFi 两台手机。
