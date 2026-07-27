# 语音指令输入（FunASR）设计

> 2026-06-09 brainstorming 产出。真理源：本文件。实施 plan 见
> `docs/plans/2026-06-09-voice-input-funasr-implementation-plan.md`。

## Goal

给手机 PWA 加**语音指令输入**：按住麦克风说话 → 流式 ASR 出字 → 喂进现有 `command`
管线（LLM 解析 / 承载 UI / 历史全复用）。保留文字输入，微信式可切换。

## 问题

当前唯一输入是 `CommandInput.vue` 的文字 `<input>`。手机上点输入框弹出**软键盘（输入法）
会把游戏画面顶上去**，对局中很难受。语音输入根除这个问题（按住说话，无文字框、不弹输入法）。

## 锁定决策（2026-06-09 用户拍板）

| 维度 | 决策 | 理由 |
|---|---|---|
| ASR 引擎 | **FunASR**（paraformer-zh-streaming，2pass）| 中文强 + **热词**治 SC2 黑话；MIT；CPU 即可不抢 SC2 的 GPU；自托管。详见 FunASR 调研。 |
| 音频通道 | **WS 发 PCM**（不走 WebRTC）| 走现有控制 WS（funnel HTTPS），**不依赖 Tailscale**（视频要 Tailscale，语音不用，更稳）。16kHz mono ~32KB/s。 |
| FunASR 部署 | **内嵌 Python 库**（funasr 跑 server 进程内）| 单进程最简，跟项目 Python 栈一致。代价：torch ~GB 依赖 + 首次下模型。单人够用。 |
| 松手流程 | **松手直接发** | 微信发语音感，靠现有承载 UI / clarification 兜错。不放输入框（改字又弹输入法）。 |
| 出字方式 | **流式**（边说边出草稿，句末 2pass 定稿）| 体验好、有反馈。 |
| 手势 | 按住说话；**上滑取消**；停在按钮区松开 = 发送 | 微信式三态。 |
| 切换 | 微信式 toggle：默认**语音模式**，选择记 localStorage | 默认语音（避输入法）；要打字切一下。 |

## 架构

```
[手机 PWA · 微信式输入]
  toggle: 语音模式(默认,无文字框) | 文字模式(现有 CommandInput input)
  ── 语音模式 ──
  按住麦克风  → getUserMedia → AudioWorklet 降 16kHz/mono/PCM16 → 每 ~100ms 一帧
     │ ws.send({type:'audio_chunk', seq, pcm:<base64 int16>})
     │ 手指上滑越阈值 → 浮层红"松开取消"
     │ 松手(按钮区)→ ws.send({type:'audio_end'})   松手(取消区)→ {type:'audio_cancel'}
     ▼ 现有控制 WS(funnel HTTPS)
[PC server · 内嵌 funasr]
  ws 收 audio_chunk → AsrSession.feed(pcm) → paraformer-zh-streaming(2pass) + 热词
     │ partial(草稿)/final(定稿)
     │ ws.send({type:'transcript', text, is_final, client_seq})
     │ audio_cancel → 丢弃该 session(不出 final、不进 LLM)
     ▼
[手机 PWA]
  录音浮层实时显示 partial → 松手收 final → **直接** emit 现有 command 帧(type:command,text)
     → 现有 IntentParser/Director/承载 UI/历史(零改下游)
```

## 数据流 · WS 帧（新增）

上行（手机 → server）：
- `{type:'audio_chunk', seq:int, pcm:str}` —— 一帧 16kHz mono PCM16，base64。`seq` 递增。
- `{type:'audio_end'}` —— 说完，正常结束 → server 出 final。
- `{type:'audio_cancel'}` —— 上滑取消 → server 丢弃该段，不出 final。

下行（server → 手机）：
- `{type:'transcript', text:str, is_final:bool}` —— partial（is_final=false，刷草稿）/
  final（is_final=true，PWA 据此 emit command 帧）。

**不改下游**：final 文字进 PWA 后，复用现有 `emit('send', {type:'command', text, client_id,
issued_at})`，后端 IntentParser → Director 全不动。

## 组件拆解

### 后端

1. **热词生成** `scripts/gen_asr_hotwords.py` → `config/asr_hotwords.txt`：
   读 `docs/aliases/*.yaml`（建筑 hotkey / 单位中文）+ `strategies/*.yaml` id/别名 + 战术黑话，
   去重、加权（`词 权重` 每行，黑话权重高）。治"4BG/闪追/IAC/VR/VS"被听岔。**复用现有别名数据源**。

2. **ASR 服务** `src/vibecraft/server/asr.py`：
   - `AsrEngine`：进程内加载 `paraformer-zh-streaming`（2pass）+ 热词，**惰性加载**（首次用才下模型/初始化，
     不拖 server 启动）。`funasr` 缺失时 graceful（log + 语音功能禁用，不崩 server）。
   - `AsrSession`：一段录音的状态（cache、chunk 累积）。`feed(pcm)->partial|None`、`finalize()->final`、
     `cancel()`。每个手机连接一个活跃 session。
   - 跑在 executor（ASR 推理是 CPU-bound 阻塞调用，不能卡 event loop）。

3. **WS 接线** `src/vibecraft/server/ws.py`：
   `audio_chunk/audio_end/audio_cancel` 帧 handler → 喂 `AsrSession` → 回推 `transcript` 帧。
   限频/APM 不变（在 final → command 后才进现有限频）。

### 前端

4. **录音 composable** `web/src/composables/useVoiceInput.ts`：
   `getUserMedia` + AudioWorklet 降采样 16kHz/mono/PCM16 → 分帧 → 经 `useWs` 发 `audio_chunk`；
   start/stop/cancel；收 `transcript` 暴露响应式 `partial`/`final`。

5. **微信式麦克风 UI** `web/src/components/VoiceInput.vue`：
   按住录音（touchstart）；touchmove 跟 Y，上滑越阈值进取消区（红"松开取消"）；touchend 分流
   发送/取消。录音浮层实时显示 partial。pointer 事件兜底鼠标（桌面调试）。

6. **语音/文字 toggle** 改 `CommandInput.vue` / 上层：微信式切换（默认语音），localStorage 记忆；
   语音 final → 调现有 `sendText` 等价路径 emit command。

## 约束 · 边界

- **getUserMedia 需 HTTPS**：麦克风只在 secure context 可用 → 语音**必须走 funnel 的 HTTPS URL**
  （`https://<host>.ts.net/?room=...`）。`http://192.168.x` / `http://100.94.x` 用不了麦克风
  （浏览器禁）。PWA 在非 HTTPS 下隐藏语音、回退文字 + 提示。
- **funasr 重依赖**：torch ~GB。作 `[asr]` 可选 extra，核心不强依赖；缺失时语音禁用、文字照常。
- **首次下模型**：paraformer-zh-streaming 从 ModelScope 下一次（~GB），之后本地。
- **录音并发**：单手机单 session；新录音开始前旧的先 finalize/cancel。

## 错误处理

| 情况 | 行为 |
|---|---|
| 无麦克风权限 / 非 HTTPS | 隐藏语音、回退文字模式 + 一次性提示 |
| funasr 未装 / 模型加载失败 | server log，`transcript` 不出；PWA 超时回退文字；语音按钮禁用态 |
| ASR 出空 / 噪音 | final 为空 → 不 emit command（等同没说），浮层提示"没听清" |
| 网络抖动丢帧 | seq 不连续容忍（流式本就容错）；final 兜底取已有 |
| 上滑取消 | audio_cancel → server 丢 session；PWA 不 emit |

## 测试策略

- **后端单测**：热词生成（别名→hotwords.txt 去重/加权）；`AsrEngine`/`AsrSession` 用**mock funasr**
  （注入假 AutoModel，验 feed→partial、finalize→final、cancel 丢弃、惰性加载、funasr 缺失 graceful）；
  ws 帧 handler（audio_chunk/end/cancel → 正确调 session + 推 transcript，mock session）。
- **前端单测**：`useVoiceInput`（mock getUserMedia/AudioWorklet/ws，验分帧/取消）；`VoiceInput.vue`
  手势（touchstart→录音、上滑→取消区、touchend 分流发送/取消）；toggle（默认语音/记忆/HTTPS 回退）。
- **真机验**（需 SC2 + 手机 + funnel + funasr 装好）：按住说"切4bg"/"刷两个叉子到前线"→ 看草稿实时刷、
  松手出指令；上滑取消不发；黑话（闪追/IAC）热词命中。**ASR 真实识别准确率**靠真机。

## 不做（v1 范围外，记 backlog）

- VAD 连续模式（不用按住，自动断句）—— 押后。
- 棱镜"可展开但未展开"时自动 morph 再折跃（warp 那块的延伸）—— 另开。
- 端上/离线 ASR、多语言 —— 不需要。
- 非 HTTPS（LAN）下语音 —— 需自签证书，押后；现走 funnel HTTPS。
