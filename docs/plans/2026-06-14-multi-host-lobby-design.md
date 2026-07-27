# 多主机大厅（Multi-Host Lobby / Game Browser）设计文档

> 状态：**设计稿，未实施**。用户先用当前单房间架构找朋友实测；本文档待用户回来确认
> 开放问题 → 定稿 → 独立 Opus 评审 → 出实施 plan → 分块实现。
> 阶段定位：多人阶段2（阶段1 = 云 TURN 中继 + 公网前门，已完成）。

---

## 1. 目标 / 非目标

**目标**：多台 PC 各自挂一个房间、全部注册到香港 VPS；手机打开一个大厅页，看到**所有
房间列表**，每个房间显示**状态**（空 / N 人 / 游戏中 / 满），支持**排序 + 筛选**（如"只看
有空位"），点一个有空的房间就进去。

**非目标（本期不做）**：跨房间匹配/天梯、观战、房间内聊天、跨 VPS 多地域。

---

## 2. 现状与差距

**现状 = 单房间**：每台 PC 跑一个 `BotService`，内含一个 `Room`(`room.py`，max_slots=2，
状态机 lobby→starting→in_game→lobby)。VPS 的 `app.*` 反向隧道当前**只指向一台 PC**。
所以 VPS 上只有一个房间，没有"房间列表"。

**已具备**：每台 PC 的 `RoomService` 已经维护完整 `room_state`（状态/slots/人数/种族/
host）并通过 WS 广播。**缺的不是房间数据，是"把各 PC 的房间状态汇总到 VPS + 一个浏览 UI
+ 按房间路由到对应 PC"**。

---

## 3. 架构总览

```
  手机 ──► https://lobby.<vps>/  (VPS 大厅目录页)
   │        GET /api/rooms → [{host_id, 名字, 状态, 1/2, 种族, 游戏中, url}...]
   │        浏览/排序/筛选 → 点"有空位"的房 → 跳到该房的公网 URL
   ▼
  ┌────────────── VPS 目录服务（新）──────────────┐
  │  注册表(内存/sqlite) + 心跳过期淘汰            │
  │  POST /api/register  POST /api/heartbeat       │
  │  GET  /api/rooms     GET /  (浏览页)           │
  └────────────────────────────────────────────────┘
     ▲注册+心跳        ▲注册+心跳        ▲
   PC-A(房间A)       PC-B(房间B)       PC-C(房间C)
   各自反向隧道(现有机制) → nginx 按房间路由回对应 PC
```

**三块新东西**：
1. **VPS 目录服务**：房间注册表 + 列表 API + 大厅浏览页。
2. **PC→VPS 注册/心跳**：每台 PC 启动报到、周期上报房间状态。
3. **按房间路由**：每台 PC 一条公网路径，大厅把手机导到选中的 PC（现有单房间流程不变）。

---

## 4. 组件设计

### 4.1 VPS 目录服务

- **形态**：VPS 上一个轻量服务（Python，参考现有 `deploy/turn/turn-testpage.py` 的
  stdlib http server，或 FastAPI；nginx 反代到它）。
- **注册表**：内存 dict 即可（重启丢失可接受；要持久化再上 sqlite）。每条：
  ```
  RoomEntry {
    host_id: str          # PC 唯一标识（注册时分配 / PC 自带）
    name: str             # 房间显示名（"老王的房" / PC 名）
    state: str            # lobby / starting / in_game
    players: int          # 已占真人 slot 数
    max_players: int      # 2
    races: list[str]      # 各 slot 种族（UI 显示）
    public_url: str       # 手机进这个房的 URL
    last_heartbeat: float # 过期淘汰用
    locked: bool          # 是否有密码（待定）
  }
  ```
- **派生状态（给 UI 的徽标）**：
  - `空闲`：state=lobby 且 players=0
  - `有空位`：state=lobby 且 0<players<max
  - `满`：state=lobby 且 players=max（进不去）
  - `游戏中`：state in (starting, in_game)（进不去，除非自己重连）
- **API**：
  - `POST /api/register {host_id, name, public_url, secret}` → 200 + 分配/确认
  - `POST /api/heartbeat {host_id, state, players, races, secret}` → 200
  - `GET /api/rooms?filter=open&sort=...` → `[RoomEntry...]`（脱敏，不含 secret）
  - `GET /` → 大厅浏览页（HTML/PWA）
- **过期淘汰**：`last_heartbeat` 超 N 秒（如 30s）的房间从列表移除（PC 关机/掉线自动消失）。

### 4.2 PC→VPS 注册 / 心跳

- PC `BotService` 启动后，后台协程向 VPS 目录 `POST /api/register`，之后每 `T` 秒
  （建议 5–10s）`POST /api/heartbeat` 上报当前 `room_state`（直接复用 `RoomService`
  已有的 room_state，映射成 RoomEntry）。
- 鉴权：注册/心跳带**共享 registration secret**（防随机 PC 灌假房间）。
- 优雅缺省：VPS 目录不可达 → PC 本地照常单房间工作（只是不在大厅列表里），不阻断。

### 4.3 按房间路由（关键决策，见 §7）

每台 PC 一条公网路径。大厅 `public_url` 指向它。手机点房 → 跳该 URL → 连到那台 PC 的
`BotService`（现有单房间 WS/WebRTC/TURN 流程**完全不变**）。

---

## 5. 大厅浏览 UX

- **列表**：每行一个房间 = 名字 + 状态徽标（颜色区分：空闲灰/有空位绿/满橙/游戏中蓝）+
  `1/2` 人数 + 种族小图标 + 进入按钮（满/游戏中置灰）。
- **排序**：有空位优先 → 空闲 → 游戏中 → 满；或按人数/名字。
- **筛选**：开关"只看有空位"（默认开）、"隐藏游戏中"。
- **自动刷新**：每 3–5s 拉一次 `/api/rooms`（或 WS 推送，MVP 先轮询）。
- 复用现有 `RoomLobby.vue` 的徽标风格，新增一个上层"房间列表页"。

---

## 6. 数据流

```
PC: RoomService.room_state ──(每 T s 心跳)──► VPS 目录注册表
手机: GET /api/rooms ──► 渲染列表 ──► 点房 ──► 跳 public_url ──► 连那台 PC(现有流程)
```

---

## 7. 路由方案候选（影响复杂度，需定）

| 方案 | URL | VPS 复杂度 | PC 侧 | 备注 |
|---|---|---|---|---|
| **A 每台 PC 一个子域** | `https://pc-<id>.<vps>/?room=` | 低（隧道+nginx map 动态子域→隧道端口） | 各自隧道占一端口 | **MVP 首选**，复用现有隧道+nginx 模式 |
| B 单入口 + room-id 路由 | `https://<vps>/r/<id>/` | 中（目录服务兼做反代，按 id 转对应隧道） | 同上 | URL 干净，VPS 逻辑重 |
| C 全信令 rendezvous | `https://<vps>/` | 高（VPS 中转所有 WS 信令） | 只出站连 VPS | 最灵活，YAGNI |

**推荐**：MVP 走 **A**（目录服务在注册时给 PC 分配子域 + 隧道端口，写进 nginx 的
`map $host $upstream` + reload）。架构预留 B（目录服务已是天然的 room-id 入口，将来把
反代逻辑收进去即可）。

---

## 8. 安全

- **注册鉴权**：共享 registration secret（MVP，信任的小圈子）；将来可换 per-host token。
- **房间密码（待定）**：要不要支持私密房（进房验密码）？MVP 可先不做，预留 `locked` 字段。
- **room token**：现有每台 PC 的 room token 仍是进房门控（大厅 `public_url` 带上）。
- 目录 `/api/rooms` 脱敏（不暴露 secret / 内网 IP）。

---

## 9. 待用户确认的开放问题

1. **主机范围**：固定几台自己人的 PC（白名单），还是开放谁都能挂主机？→ 决定鉴权强度。
2. **房间密码**：要不要私密房？MVP 做不做？
3. **心跳/刷新频率**：状态多久刷一次能接受（5s？10s？）？
4. **房间命名**：PC 自报名字，还是房主在大厅自定义？
5. **满 / 游戏中的房**：列表里**灰掉**还是**隐藏**（默认筛掉）？
6. **路由**：接受方案 A 的 `pc-<id>.<vps>` 子域 URL 吗，还是要 B 的干净单入口？

---

## 10. 分期（MVP → 扩展）

- **MVP**：目录服务（内存注册表 + /rooms + 浏览页）+ PC 注册心跳 + 方案 A 路由 +
  状态徽标 + "只看有空位"筛选 + 轮询刷新。**够"多人挑房进"用**。
- **扩展**：WS 推送实时刷新、房间密码、持久化注册表、方案 B 单入口、观战、房间名自定义。

---

## 11. 风险 / 注意

- **nginx 动态子域→隧道端口**：方案 A 需要注册时改 nginx map + reload（或用 openresty/
  Lua 动态查注册表）。MVP 可先支持固定 N 台（预配 N 个子域+端口），再做动态。
- **隧道管理**：每台 PC 一条反向隧道；多台时端口分配 + 自动重连（现有 `pc-tunnel.ps1`
  扩展成带 host_id/端口参数）。
- **复用现有**：PC 侧单房间逻辑 / WebRTC / TURN / 前门 nginx 全不动；只加"注册心跳" +
  "目录服务" + "多隧道/路由"。改动面集中、风险可控。

---

## 12. 下一步

1. 用户测完单房间回来 → 回答 §9 开放问题。
2. 据此定稿设计 → **独立 Opus 评审**（架构/风险/YAGNI）。
3. 出任务级实施 plan（writing-plans）→ 分块实现 + 自测。
