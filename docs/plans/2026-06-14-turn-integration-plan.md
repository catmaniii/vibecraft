# TURN 接入 VibeCraft App 实施方案（阶段1 · P2-D）

**目标**：让真实 App 的 WebRTC（PC SC2 画面/声音 → 手机）在 P2P 打不通时自动回落到
已部署的云 coturn 中继（turns:443 / turn:3478），实现中国侧也能连。

**前置已完成**：云 coturn 部署 + 真机打通验证（SG + 国内 4G 均通，turns:443 OK）。
secret/域名在 `.secrets/vibecraft-turn.env`（gitignore）。

**不变量**：未配置 TURN 时行为**完全不变**（`_ICE_SERVERS` 空、纯 P2P/Tailscale），
graceful。TURN 是叠加的回落，不改现有 P2P 优先逻辑。

---

## 架构

```
手机(browser) ──GET /api/turn-credential──► PC server (http.py)
   │  ← {iceServers:[stun, turn:3478, turns:443], 现签 username/password}
   │
   ├─ new RTCPeerConnection({iceServers})  ← 拿到再建 PC
   │
   └═══ ICE: P2P 优先(host/srflx/Tailnet) → 不通则 turns:443 中继 ═══► PC(aiortc)
                                                                         ▲
        PC 侧 aiortc 同样用现签凭证建 iceServers(handle_offer)───────────┘
```

两侧（手机 browser + PC aiortc）各自用**同一 secret 现签的短期凭证**配置 iceServers；
coturn `use-auth-secret` 校验。P2P 候选优先胜出，relay 仅兜底（aiortc 无
iceTransportPolicy，但 host/srflx 天然优先，不会无谓走中继）。

---

## 组件与改动

### 1. 新模块 `src/vibecraft/server/turn_config.py`

```python
@dataclass(frozen=True)
class TurnConfig:
    domain: str
    secret: str
    port: int = 3478
    tls_port: int = 443

def load_turn_config() -> TurnConfig | None:
    # 优先 env(VIBECRAFT_TURN_DOMAIN/SECRET/PORT/TLS_PORT)，
    # 缺失则读 .secrets/vibecraft-turn.env(TURN_DOMAIN/TURN_STATIC_SECRET/...)。
    # 任一必填(domain/secret)缺 → 返回 None(无 TURN，graceful)。

def mint_credential(secret: str, ttl_s: int = 3600, name: str = "vibecraft") -> tuple[str, str]:
    # coturn REST: username=f"{expiry}:{name}", password=base64(HMAC-SHA1(secret, username))

def build_ice_servers(cfg: TurnConfig, ttl_s: int = 3600) -> list[dict]:
    # 返回 [{"urls":["stun:dom:3478"]},
    #       {"urls":["turn:dom:3478?transport=udp","turn:dom:3478?transport=tcp",
    #                "turns:dom:443?transport=tcp"],
    #        "username":u, "credential":p}]
```

- 纯函数 + 可注入，单测覆盖 mint/build/load（含缺失→None）。
- **复用已验证的 HMAC 方案**（deploy/turn/turn_selftest.py 同款，真机已验证）。

### 2. `webrtc.py`

- `WebRtcManager.__init__(turn_config: TurnConfig | None = None)` 存配置。
- `handle_offer` 里 `config = RTCConfiguration(iceServers=_ICE_SERVERS)`（:681）
  → `iceServers = self._build_ice_servers_aiortc()`：有 turn_config 就用现签凭证建
  `RTCIceServer` 列表，否则空（现行为）。
- `make_webrtc_manager(turn_config=...)` 透传。aiortc `RTCIceServer(urls=[...], username, credential)`。

### 3. `http.py`

- `make_process_request(..., turn_config=None)` 新增参数。
- 加路由 `GET /api/turn-credential` → `_json_response({"iceServers": build_ice_servers(cfg)})`；
  无 cfg → `{"iceServers": []}`（手机回退 Google STUN）。
- TTL 1h；凭证短期、仅供已连入的玩家，滥用面可控。

### 4. `service.py` 接线

- `BotService.run()`：`turn_config = load_turn_config()`；启动 log
  `turn_enabled=bool(turn_config)`（observability）。
- 透传给 `make_webrtc_manager` 与 `make_process_request`。

### 5. `web/src/components/LiveView.vue`

- `connect()` 里 `new RTCPeerConnection` 之前：
  ```ts
  let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }]  // 兜底
  try {
    const r = await fetch('/api/turn-credential')
    const j = await r.json()
    if (j.iceServers?.length) iceServers = [...j.iceServers, ...iceServers]
  } catch { /* 网络/无 TURN → 用兜底 STUN */ }
  const peerConn = new RTCPeerConnection({ iceServers })
  ```
- fetch 失败/超时（2s）→ 回退现有 Google STUN，不阻断连接。

---

## 配置来源

`.secrets/vibecraft-turn.env`（已存在，gitignore）= 单一真值源。`load_turn_config`
先读 env var、再读该文件。start.ps1 无需改（server 自己读文件）。prod 可用 env 覆盖。

---

## 自测策略（到手机边界）

1. **单测**：`turn_config`（mint/build/load 缺失）；`http` 端点返回结构；`webrtc` 有/无
   turn_config 的 iceServers 构造；web `LiveView` fetch + 回退（vitest mock fetch）。
2. **真局自测**（我能做）：起 realtime server（带 .secrets TURN 配置）→ `curl
   http://127.0.0.1:<port>/api/turn-credential` 验证返回现签 iceServers → 抽出凭证用
   aiortc gather 验 relay 命中（复用 turn_selftest 逻辑）。
3. **PC aiortc 侧**：构造一个 offer 走 handle_offer，确认 answer SDP 含 relay 候选
   （证明 PC 侧也会用 TURN）。
4. **手机边界**（需用户）：手机连真 App，断 Tailscale/用蜂窝制造 P2P 失败，看视频是否
   经 relay 仍能播（server log `webrtc_connection_state=connected` + coturn 有分配）。

---

## 风险 / 注意

- **凭证下发安全**：`/api/turn-credential` 无鉴权即可拿短期 TURN 凭证 → 任何能访问
  server 的人可用中继 1h。可接受（中继本就服务连入玩家；流量不限）；如要收紧，后续
  可绑 room token。本期不做（YAGNI）。
- **不破坏 P2P 优先**：iceServers 只是候选来源，host/srflx 优先；不强制 relay。
- **aiortc turns 支持**：aioice 0.10.2 已验证支持 turns（真机 PASS）。
- **CORS**：`/api/turn-credential` 同源（PWA 与 server 同端口），`_json_response` 已带
  `Access-Control-Allow-Origin:*` 兜底。

---

## 评审修订（2026-06-14，独立 Opus 评审后逐条采纳）

1. **gathering 延迟权衡（显式记录）**：PC/手机两侧加 TURN 后，gathering 要等 coturn
   relay 候选。coturn 可达（HK ~30ms）时仅**亚秒**级；仅当 coturn **不可达**才吃满两侧
   各自的 5s gather cap。接受此权衡（relay 是兜底刚需，且 coturn 已部署可达）。
2. **凭证 TTL 1h → 24h**：coturn allocation 的 Refresh 会重校验 username 里的 expiry，
   1h 过期后长局（SC2 可 >1h）中继会静默断。`mint_credential` 默认 `ttl_s=86400`。
3. **有 TURN 就不拼 google STUN**：中国手机连不上 google STUN，非 trickle gathering 会
   等满 5s。`build_ice_servers` 已含 coturn 自己的 `stun:dom:3478`（中国可达）。客户端：
   fetch 成功（有 iceServers）→ **只用返回的**（含 coturn STUN）；fetch 失败/无 TURN →
   才回退 `stun:stun.l.google.com`。
4. **fetch 超时用 `AbortSignal.timeout(2000)`**（fetch 无内建超时）。
5. **`build_ice_servers` 每请求调用**（路由处理函数内现签，不在闭包创建时缓存）。
6. **所有新增参数默认 `None`**（`make_webrtc_manager`/`make_process_request`/
   `WebRtcManager.__init__`/`handle_offer` 向后兼容，直接构造的单测不断）。
7. **`/api/turn-credential` 加 room-token 门控**：校验 `?room=<token>` 与 server token
   一致，不一致返回 `{"iceServers": []}`（或 403）。挡随机扫描拿中继凭证，与现有 PWA/WS
   的 room-token 门控模型一致。`make_process_request` 接收 `room_token` 参数。

## 任务顺序（TDD，每步单测 + 自测）

1. `turn_config.py` + 单测（mint/build/load）
2. `http.py` `/api/turn-credential` + 单测
3. `webrtc.py` handle_offer 用现签 iceServers + 单测
4. `service.py` 接线 + 启动 log
5. `LiveView.vue` fetch + 回退 + vitest
6. 真局自测（curl 端点 + aiortc relay 验证 + handle_offer answer 含 relay）
7. build web bundle；交用户做手机蜂窝回落验证
