# Admin Dashboard 设计（2026-06-16 用户）

> 只有 admin 能登录的只读运维面板。用户决策（2026-06-16）：**独立 admin token 鉴权 / 不做 VPS 层（二期）/
> 只读现有内存+日志（不加持久化）**。实现前过独立 Opus 评审。
> 相关现有基建：`server/room.py`（房间状态机+slot）、`server/tokens.py`（RoomRegistry 在线连接）、
> `server/chat.py`（ChatHub 内存 50 条）、`server/http.py`（feedback CSV + API 路由）、`logs/<game_id>/`。

## 范围（一期）

1. **家里 PC / server 运行情况**：server 在线、是否游戏中、房间状态（lobby/starting/in_game）、
   slot 情况、大厅在线玩家、聊天记录（内存 50 条），**admin 可发聊天**。
2. **对局记录**：扫 `logs/` 真人对局（game_*/match_*，排除 eff_*/build_* 沙盒），提取
   玩家姓名 / 种族 / 时长 / 大致过程（recipe + 关键 events）。
3. **玩家留言**：读 `logs/feedback.csv`。
4. **顺带修 #4 文案**：游戏已开始第三人加入的拒绝文案 "对局进行中，不能改房间设置" →
   分场景，加入被拒用 "对局进行中，无法加入"（用户早记的小瑕疵）。

**不做（二期）**：VPS 层状态（coturn/nginx/隧道/负载）、聊天/对局持久化落盘、ban/踢人等管理操作。

## 鉴权（独立 admin token）

- `vibecraft serve --admin-token <token>`（+ env `VIBECRAFT_ADMIN_TOKEN` 透传，start.ps1 加 `-AdminToken`）。
- **默认不设 = admin dashboard 整体关闭**（/admin 与 /api/admin/* 返 404）——secure by default。
- admin token 与 room token **独立**（更强）。校验复用 `secrets.compare_digest`（常数时间）。
- 访问：`/admin?key=<admin-token>`。页面 + 所有 `/api/admin/*` 端点都校验 key（query 或 header）。
  校验失败返 403。

## 交付形态

- **独立静态页** `server/static/admin.html`（单文件 HTML+JS，**不进 Vue PWA build**）——admin 工具不需
  PWA 打磨，自包含页省掉 web build 链路。轮询 `/api/admin/*`（每 ~3s）刷新，admin 发聊天走 POST。
- server 路由：`GET /admin` → 校验 key → serve admin.html；key 错/未配 → 404/403。

## Admin API（HTTP，全部校验 admin token）

| 端点 | 返回 |
|---|---|
| `GET /api/admin/status` | server 在线、room 状态机、slots（含 name/race/ready/kind）、在线玩家列表（RoomRegistry.player_ids + 在线/离线）、是否游戏中、当前 match_id、realtime |
| `GET /api/admin/chat` | ChatHub 当前内存历史（复用现有 `_history`） |
| `POST /api/admin/chat-send` | body `{text}` → `ChatHub.add(name="管理员", pid="__admin__", text)` → 广播给房间（admin 加入聊天） |
| `GET /api/admin/games` | 扫 `logs/` 真人对局目录，每局元数据（见下） |
| `GET /api/admin/feedback` | 解析 `logs/feedback.csv` → JSON 行 |

**对局元数据提取**（`/api/admin/games`，纯读 logs/）：
- 目录筛选：`game_*` / `match_*`（排除 `eff_*` / 含 build_acceptance 标记的沙盒局）。
- 每局读 `telemetry.jsonl`：首行 `game_start`（my_race / active_recipe / home / enemy）+ 末 snapshot 的 `t`（时长）+
  胜负（末尾 Victory/Defeat/Tie 若有）。
- **玩家姓名**：现有 game log **不含**玩家名。一期best-effort：① match_* 局从 match_id 关联（match.py 有
  slot→game_id），② 单人局 game_* 无玩家名 → 显示昵称未知/“—”。**评审确认**：要不要在 game_start 落一个
  玩家名字段（极小写入、不算"加持久化层"）让记录可用，还是一期接受姓名缺失。
- "大致过程"：events.jsonl 里的策略切换（strategy.* / auto_switch）+ opening_completed + 关键 directive 计数。

## 安全 / 隐私

- admin token 不写进任何前端 bundle / 日志；校验失败不泄露"是否配了 admin"（统一 404）。
- feedback.csv 含玩家 IP/UA → 只对 admin 可见（本就只 admin 端点读）。
- admin 发聊天的 pid 固定 `__admin__`，前端标"管理员"，防伪。

## 自验

- 单测：admin token 校验（对/错/未配 → 200/403/404）、/api/admin/* 全部门控、games 扫描的元数据提取
  纯函数（喂假 logs 目录断言）、feedback CSV 解析、chat-send 经 ChatHub 广播。
- 手动（起 server）：`/admin?key=` 看 status/games/feedback/chat 出数；admin 发聊天玩家端能收到。
  无 key → 404；错 key → 403。

## 独立评审处理（2026-06-16 Opus，逐条 → 修订）

**采纳（必须改 M1）对局筛选规则反了**：真人局 game_id 恒为 **`match_<ts>_p<slot>`**（solo 也是 `match_*_p0`，
见 match.py），而 **`game_*` 恰是 build_acceptance 沙盒**（同 session 默认前缀）。原稿"含 game_* 排除 eff_*"
会把全部沙盒灌进真人列表。→ **改成白名单：真人局 = `match_*` 目录前缀精确匹配**，其余（game_/eff_/e2e_/
*selftest*/*proof* 等）一律沙盒排除。games 筛选单测**必须**放一个 `game_*` 负样本断言被排除。

**采纳（必须改 M2）公网 admin token 传输 + 暴破**：① `/api/admin/*` 调用**强制走 header `X-Admin-Token`，
不接受 query**（query 会进 VPS nginx access log，每 3s 轮询反复写）。页面入口 `/admin?key=` 可保留（URL 难免），
JS 拿到存内存、轮询只用 header。② admin token **最小长度校验**（如 <16 字符拒启动 / 或未配时给强随机默认打印一次），
杜绝弱口令。③ 进程内**失败计数 + 限速/短时锁定**（公网必备）。compare_digest 保留。

**采纳（消歧）鉴权失败统一 404**（不分"未配"与"key 错"），非披露；删掉稿里"403"的自相矛盾。

**采纳（chat-send 用 GET+query 不用 POST）**：现有 `process_request` 走 websockets Request、**无 method/body**
（所有 API 含 feedback 都是 GET+query）。chat-send 也走 **GET+query**（`/api/admin/chat-send?key=..&text=..`，
随项目惯例；token 仍走 header）。sync handler 里发广播用 `asyncio.create_task(registry.broadcast(...))`
fire-and-forget（响应先返回）。**复用 `room_service.chat` 同一 ChatHub 实例**（不新建，id 不割裂），pid=`__admin__`。

**采纳（games 不进 3s 轮询）**：status/chat 读内存 cheap、可 3s 轮询；**games 扫描是同步文件 IO，绝不进轮询**
（按需点开 / 低频）。默认只列**最近 N=50 局**、按目录 mtime 倒序、读末行用反向 seek（不整文件读）、mtime 缓存。

**采纳（玩家姓名补 game_start 字段，一期拍死不留开放问题）**：现有 game log 不含玩家名（solo 路径连
mp_player_name 都不设）。→ 一期**给 `build_game_start_record` 加一个昵称字段**（属"丰富已有日志"非"加持久化层"，
不违背用户决策）：昵称从 Room.slot.name → GameConfig（solo 路径也传）→ bot → game_start record，约 3 处小改。
存量旧局回填不了 → 历史列表旧局显示"—"，**当前在跑的局 /status 直接显示 Room 活 slot 名**（实时有名）。

**采纳（安全收紧）**：admin 端点**白名单字段输出**（绝不 dump 整个 config/registry，防漏 room token/TURN/.secrets）；
admin 响应**不发 `Access-Control-Allow-Origin: *`**（收紧 CORS）；feedback IP 展示轻度脱敏。

**采纳（#4 文案，别动共享 `_require_lobby`）**：在 `Room.join()` 里**就地**独立判断抛"对局进行中，无法加入"
（`_require_lobby` 被 set_race/set_ready/add_computer 共用，改它污染所有设置类拒绝）。现有单测只断言
`type=="room_error"` 不校验字符串 → 改 join 文案不破单测，低风险。

**自验补**：games 筛选含 `game_*` 负样本（锁 M1）；chat-send 断言复用同一 ChatHub + 经 broadcast 广播
（id 连续、pid=`__admin__`）；admin token 失败计数/锁定单测。

## 风险 / 取舍

- 静态页 vs Vue：选静态页省 build，代价是 UI 朴素（admin 工具可接受）。
- games 扫描性能：logs/ 可能很多局 → 加分页/上限（默认最近 N 局）+ 缓存 mtime，别每次全扫深读。
- 玩家姓名缺失：见上，评审定一期是否补 game_start 写入。
- admin token 泄露 = 全量只读 + 可发聊天（无破坏性操作，一期不做 ban/踢人，降风险）。
