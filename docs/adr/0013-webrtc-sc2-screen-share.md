# ADR 0013: WebRTC SC2 画面直播

**日期**: 2026-05-21
**状态**: 已实施
**决策者**: catmaniii

---

## 背景

玩家在 PC 跑 SC2，手机扫码进 PWA。手机端看不到 SC2 游戏画面，只能靠 bot 推送的
minimap 帧和策略卡片盲操。需要把 PC 上 SC2 的实时画面流式传输到手机浏览器，少量
延迟可接受（< 200ms RTT 局域网）。

---

## 候选传输方案

| 方案 | 延迟 | 带宽 | 远程可用 | 实现复杂度 |
|---|---|---|---|---|
| **A. WebRTC** | 低（< 100ms）| 低（H.264/VP8 编码）| 有（STUN/TURN）| 中（aiortc）|
| B. WS + JPEG | 中（200-500ms）| 高（无压缩）| 有 | 低（现有 WS）|
| C. WS + VP8 手动编码 | 低 | 低 | 有 | 高（手写 RTP）|
| D. HLS / DASH | 高（2-10s）| 低 | 有 | 高（需 ffmpeg 服务器）|

选择 **方案 A（WebRTC）**：

- 带宽最低：浏览器原生 H.264/VP8 硬件解码，mss 截屏→编码 pipeline 成熟
- 延迟最低：RTP over UDP，无 HTTP chunk 开销
- 远程友好：配 STUN（stun.l.google.com:19302），同网 / 跨网都有机会打洞
- aiortc 是 Python WebRTC 事实标准，API 稳定

---

## 信令端点设计

### 为什么用独立端口（port + 1）而非共享现有端口

现有服务器用 `websockets` 库的 `process_request` 钩子拦截 HTTP 请求（ADR 0001 决策）。

`process_request` 在 HTTP 握手阶段调用，此时 websockets 只读了请求行 + 头，
**POST body 尚未读取**。读 body 需要访问 `ServerConnection._transport` 的底层 socket，
与 websockets 内部实现深度耦合，升级风险高（websockets 16.0 没有公开 body 读取 API）。

选择在 **port + 1** 运行一个独立的 `asyncio.start_server`，框架零依赖，
信令 I/O 与 WS/静态文件 I/O 彻底分离，代码可独立测试。

前端用 `window.location.port + 1` 计算信令端口（BotService 固定约定）。

### 信令流程（非 Trickle ICE）

```
浏览器                                服务端
  |                                    |
  | new RTCPeerConnection              |
  | addTransceiver('video', recvonly)  |
  | addTransceiver('audio', recvonly)  |
  | createOffer                        |
  | setLocalDescription(offer)         |
  | [等 ICE gathering complete]        |
  |                                    |
  | POST /webrtc/offer                 |
  | {sdp: offer.sdp, type: 'offer'} →  |
  |                                    | setRemoteDescription(offer)
  |                                    | addTrack(SC2ScreenTrack + SC2AudioTrack)
  |                                    | createAnswer
  |                                    | setLocalDescription(answer)
  |                                    | [等 ICE gathering complete]
  | ← {sdp: answer.sdp, type:'answer'} |
  |                                    |
  | setRemoteDescription(answer)       |
  | ontrack → <video>.srcObject = ...  |
  |                                    |
  |==[ RTP video + audio stream ]=====|
```

双端都等 ICE gathering 完成再发送 SDP（inline candidates），关闭 Trickle ICE，
简化信令状态机。

---

## 截屏策略

### 窗口定位

枚举顶层窗口，**优先按进程名 `SC2_x64.exe` 匹配**（语言无关 —— 中文版客户端
窗口标题是《星际争霸II》、英文版是 StarCraft II，写死标题会漏匹配），进程名
拿不到时回退到多语言标题子串匹配。定位到的窗口对象被缓存：窗口在时每帧只读
一次坐标（自动跟随移动 / 缩放）；窗口消失后限频 1s 重新全量搜索。

**找不到窗口时**：产黑底灰中线占位帧（不崩溃），让浏览器端看到"流在传、等 SC2 启动"。

### 截屏库

`mss`：纯 Python，Windows/macOS/Linux 全支持，同步接口，每帧 < 5ms（1920×1080），
阻塞 asyncio event loop 可接受（帧率 30fps，每帧 < 1/30s 预算）。

### 帧格式

mss 输出 BGRA（每像素 4 字节），直接传给 `av.VideoFrame.from_ndarray(bgra, format='bgra')`，
aiortc 内部做 BGRA→YUV→H.264/VP8 编码。

---

## 音频直播（系统回环采集）

把 SC2 的游戏声音一并直播：抓**系统输出**（默认扬声器正在播放的音频），
而非麦克风 —— PC 喇叭里响什么，手机就听到什么。

### 采集库

`soundcard`：跨平台音频库，Windows 上走 WASAPI **loopback**（`include_loopback=True`
取到的「麦克风」即对应扬声器的回环）。纯 Python + cffi，无系统级依赖，`uv add` 即可。

候选对比：`sounddevice`（PortAudio）的 WASAPI loopback 取决于打包的 PortAudio
是否带该特性，不确定；`pyaudiowpatch` 是 PyAudio 的小众 fork。`soundcard` API
最干净，是「录扬声器输出」的常用选择。

### 线程亲和

WASAPI 有 COM 线程亲和：recorder 的开启 / 录制 / 关闭必须在同一线程。故
`SC2AudioTrack` 自带一个 `max_workers=1` 的 ThreadPoolExecutor，所有 soundcard
调用都派到这唯一线程；连 `close()` 也提交给它执行后再 `shutdown`。

### 帧格式与节流

每帧 20ms = 48kHz × 960 采样（Opus 原生帧长）。soundcard 录到的 float32
→ clip + 转 s16 立体声打包 → `av.AudioFrame` → aiortc 编 Opus。
`record(960)` 本身阻塞到够帧（天然实时节流）；退化为静音时由 wall-clock
sleep 兜底，不空转。

### 浏览器自动播放

带声音的媒体流会被浏览器自动播放策略拦截。前端处理：`play()` 失败 → 先静音
播画面，浮出「点按开启声音」按钮，用户点击（构成手势）后解除静音。

---

## 生命周期 / 零开销保证

| 事件 | 前端 | 服务端 |
|---|---|---|
| 组件挂载 + WS 已连 | connect() → createOffer | 建 PC + SC2ScreenTrack + 开截屏 |
| 直播正常 | <video> 展示 | 每 1/30s grab_frame() |
| 折叠 LiveView | pc.close() | connectionstatechange → _close_pc() → 停截屏 |
| 展开 LiveView | 重新 connect() | 新建 PC + SC2ScreenTrack |
| WS 断连 | disconnect() | 不受影响（WebRTC 独立） |
| BotService 关闭 | — | WebRtcManager.close_all() |

折叠 = `pc.close()` → 浏览器发 DTLS close → 服务端 `connectionstatechange` 收到
`"closed"` → `_close_pc()` → SC2ScreenTrack GC → 截屏停止。**零 CPU / 带宽开销**。

---

## 实现文件

| 文件 | 内容 |
|---|---|
| `src/vibecraft/server/webrtc.py` | SC2ScreenCapture / SC2ScreenTrack / SC2AudioCapture / SC2AudioTrack / WebRtcManager / WebRtcSignalServer |
| `src/vibecraft/server/service.py` | BotService 集成 WebRtcManager + WebRtcSignalServer |
| `web/src/components/LiveView.vue` | 前端 WebRTC 客户端 + 折叠 UI |
| `web/src/App.vue` | 响应式布局集成（竖屏上/横屏左）|
| `web/tailwind.config.js` | 新增 portrait / landscape variant |
| `tests/unit/test_webrtc.py` | 截屏器 / 占位帧 / 窗口定位 / HTTP 解析单测 |

---

## 新增依赖

```
aiortc      # WebRTC Python 实现（含 aioice / pyee）
av          # FFmpeg Python binding（aiortc 用于编码）
mss         # 跨平台截屏
pygetwindow # Windows 窗口枚举（定位 SC2 区域）
psutil      # 由窗口句柄查进程名（语言无关定位 SC2_x64.exe）
soundcard   # 系统回环音频采集（Windows WASAPI loopback）
```

---

## 结论

WebRTC（方案 A）最佳：低延迟、低带宽、远程友好，aiortc + mss 实现简洁。
信令用独立端口（port+1）绕过 websockets 不缓冲 POST body 的限制，代码可独立测试。
前端折叠即 `pc.close()`，服务端 zero-overhead 停截屏，不污染现有 WS / GameProcess 架构。
