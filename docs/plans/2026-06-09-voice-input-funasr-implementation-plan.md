# 语音指令输入（FunASR）实施 Plan

> **For Claude:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 逐任务执行。
> 所有 code-writing subagent 用 **sonnet**；本 session（Opus）做 brief + 两段 review + debug。

**Goal:** 手机按住麦克风 → 流式 FunASR 出字 → 喂现有 `command` 管线；保留文字、微信式切换 + 上滑取消。

**Architecture:** 见 `docs/plans/2026-06-09-voice-input-funasr-design.md`（真理源）。音频走现有 WS
（funnel HTTPS，不依赖 Tailscale）；FunASR 内嵌 Python（paraformer-zh-streaming 2pass + 热词）；
final 文字复用现有 `emit('send', {type:'command'})`，下游零改。

**Tech Stack:** Python/funasr/torch(CPU)/asyncio executor；Vue3/AudioWorklet/getUserMedia/Vitest；
pytest（mock funasr）。

**依赖关系:** Task 1 独立。Task 2 依赖 funasr 装好。Task 3 依赖 2+4。Task 4 独立(帧类型)。
Task 5 依赖 4。Task 6 依赖 5+4。Task 7 依赖 6。Task 8 全集成后端到端。按 1→8 顺序。

---

### Task 1: ASR 热词生成（后端，独立）

**Files:**
- Create: `scripts/gen_asr_hotwords.py`
- Create: `config/asr_hotwords.txt`（脚本产物，提交占位 + 生成）
- Test: `tests/unit/test_gen_asr_hotwords.py`

**做什么:** 读 `docs/aliases/*.yaml`（建筑 hotkey 别名、单位中文别名）+ `strategies/*.yaml`（id/显示名）
+ 一份内置战术黑话列表（4BG/IAC/12D/闪追/Skytoss/两矿凤凰/MMM…）→ 去重、输出 `词 权重` 每行
（建筑/单位权重 ~15，黑话权重 ~20）。

**Step 1: 失败测** —— `test_gen_asr_hotwords.py`：调 `build_hotwords(aliases_dir, strategies_dir)`
返回 `list[tuple[str,int]]`，断言含 `("闪追", 20)`、建筑/单位别名、无重复、权重正确；空目录不崩。
**Step 2: 跑测确认 fail**（函数未定义）。
**Step 3: 实现** `build_hotwords` + `main()` 写文件。复用现有 yaml 加载（参考 alias 加载代码）。
**Step 4: 跑测 PASS** + `.venv/Scripts/python.exe scripts/gen_asr_hotwords.py` 生成 `config/asr_hotwords.txt` 人工瞄一眼。
**Step 5: commit** `feat(asr): 热词生成脚本(别名表+黑话→hotwords.txt)`。

---

### Task 2: 依赖 + ASR 引擎/会话（后端，依赖 funasr 装好）

**Files:**
- Modify: `pyproject.toml`（加 `[project.optional-dependencies] asr = ["funasr", ...]`）
- Create: `src/vibecraft/server/asr.py`
- Test: `tests/unit/test_asr.py`

**先装:** `uv add --optional asr funasr`（**确认在 .venv**；torch 重，首次慢）。装不动/留待真机则
先只写代码 + mock 测，pyproject 占位。

**做什么:** `AsrEngine`（惰性加载 paraformer-zh-streaming 2pass + 读 `config/asr_hotwords.txt`；
funasr 缺失 → `available=False` graceful）。`AsrSession`：`feed(pcm:bytes)->str|None`(partial)、
`finalize()->str`(final)、`cancel()`。推理走 `asyncio.get_event_loop().run_in_executor`（CPU 阻塞不卡 loop）。

**Step 1: 失败测**（**mock funasr**：注入假 `AutoModel`，`generate` 返预设草稿/定稿）：
验 `feed` 累积出 partial、`finalize` 出 final、`cancel` 后 finalize 不出、惰性加载（未 feed 不初始化）、
`funasr` import 失败 → `AsrEngine.available is False` 不抛。
**Step 2: 跑测 fail。 Step 3: 实现**（mock 友好：模型工厂可注入，便于测）。
**Step 4: 跑测 PASS。 Step 5: commit** `feat(asr): FunASR 流式引擎/会话(惰性加载+热词+executor+graceful)`。

---

### Task 3: WS 音频帧接线（后端，依赖 2+4）

**Files:**
- Modify: `src/vibecraft/server/ws.py`
- Test: `tests/unit/test_server_ws.py`（扩）

**做什么:** 处理 `audio_chunk`(base64 PCM→`session.feed`→partial 则推 `transcript` is_final=false)、
`audio_end`(`session.finalize`→推 `transcript` is_final=true)、`audio_cancel`(`session.cancel`)。
每连接持一个活跃 `AsrSession`；新 chunk 序列开始前旧的清掉。`AsrEngine.available=False` → 收到音频帧回一条
`transcript`/错误提示让前端回退（或静默禁用）。**限频/APM 不在此层**（final→command 后才进现有限频）。

**Step 1: 失败测**（mock `AsrSession`）：audio_chunk→feed 被调 + partial 推 transcript；audio_end→finalize+推 final；
audio_cancel→cancel 被调、不推 final；engine 不可用→不崩、回退提示。
**Step 2 fail → Step 3 实现 → Step 4 PASS。 Step 5: commit** `feat(asr): WS audio_chunk/end/cancel 帧 → transcript 回推`。

---

### Task 4: 前端 WS 帧类型 + 发送 helper（独立）

**Files:**
- Modify: `web/src/types.ts`（加 `AudioChunkFrame`/`AudioEndFrame`/`AudioCancelFrame`/`TranscriptFrame`）
- Modify: `web/src/composables/useWs.ts`（`sendAudioChunk/sendAudioEnd/sendAudioCancel` + 暴露 `lastTranscript`）
- Test: `web/src/composables/__tests__/useWs.test.ts`（扩）/ `web/src/__tests__/types.test.ts`

**Step 1: 失败测** —— 调 `sendAudioChunk(seq,pcm)` → ws.send 收到正确 JSON 帧；收 `transcript` 帧 → `lastTranscript` 更新。
**Step 2 fail → Step 3 实现 → Step 4 PASS（vitest）。 Step 5: commit** `feat(asr): 前端 WS 音频/transcript 帧类型+发送 helper`。

---

### Task 5: 录音 composable useVoiceInput（依赖 4）

**Files:**
- Create: `web/src/composables/useVoiceInput.ts`
- Create: `web/public/pcm-worklet.js`（AudioWorklet：Float32→16kHz mono Int16 降采样分帧）
- Test: `web/src/composables/__tests__/useVoiceInput.test.ts`

**做什么:** `start()`(getUserMedia+AudioContext+worklet→每帧经 useWs `sendAudioChunk`)、`stop()`(发 audio_end)、
`cancel()`(发 audio_cancel)、暴露 `partial`/`final`(来自 lastTranscript)、`isRecording`、`supported`
(HTTPS+getUserMedia 可用性)。

**Step 1: 失败测**（mock `navigator.mediaDevices.getUserMedia`/`AudioContext`/useWs）：start→getUserMedia 调 + chunk 发；
stop→audio_end；cancel→audio_cancel；非 secure context→`supported=false`。
**Step 2 fail → Step 3 实现 → Step 4 PASS。 Step 5: commit** `feat(asr): useVoiceInput 录音 composable + PCM worklet`。

---

### Task 6: 微信式麦克风 UI VoiceInput.vue（依赖 5+4）

**Files:**
- Create: `web/src/components/VoiceInput.vue`
- Test: `web/src/components/__tests__/VoiceInput.test.ts`

**做什么:** 按住麦克风(touchstart/pointerdown→`start()`)；touchmove 跟 Y，上滑越阈值(~60px)进取消区
(浮层红"松开取消")；touchend/up：取消区→`cancel()`、否则→`stop()`。录音浮层实时显示 `partial`；
`final` 非空 → `emit('recognized', text)`。`supported=false` → 不渲染麦克风(交给上层回退文字)。

**Step 1: 失败测**（mock useVoiceInput）：touchstart→start 调 + 浮层出；touchmove 上滑越阈值→取消态(testid);
touchend 取消区→cancel、按钮区→stop；final→emit recognized；上滑后松开不 emit。
**Step 2 fail → Step 3 实现 → Step 4 PASS。 Step 5: commit** `feat(asr): VoiceInput 微信式麦克风(按住说/上滑取消/松开发)`。

---

### Task 7: 语音/文字 toggle + 接入现有 command（依赖 6）

**Files:**
- Modify: `web/src/components/CommandInput.vue`（或上层 CockpitView）：微信式 toggle
- Test: `web/src/components/__tests__/CommandInput.test.ts`（扩）

**做什么:** 加语音/文字模式 toggle（默认语音，localStorage 记忆）。语音模式渲染 `VoiceInput`，
`@recognized` → 走现有 `sendText` 等价路径 emit `command` 帧（复用承载 UI/历史）。文字模式 = 现有 input。
`VoiceInput.supported=false` → 强制文字模式 + 一次性提示（需 HTTPS）。

**Step 1: 失败测**：默认语音模式渲染 VoiceInput；toggle→文字模式渲染 input + 记 localStorage；
VoiceInput recognized 事件 → emit 现有 command 帧（text 正确）；non-secure→回退文字。
**Step 2 fail → Step 3 实现 → Step 4 PASS。 Step 5:** `cd web && npm run build`（**PowerShell 工具**，看到 ✓ built），
commit `feat(asr): 语音/文字微信式切换 + 语音 final 接入现有 command 管线`。

---

### Task 8: 端到端真机验（需 SC2+手机+funnel+funasr）

**Files:**
- Create: `docs/voice-input-runbook.md`（起法 + 验收清单）
- Modify: `CHANGELOG.md` / `TASKS.md`

**做什么（人工 + 我截图/日志辅助）:**
1. 装 funasr + 下模型；生成 hotwords；起 server + funnel。
2. 手机走 **funnel HTTPS URL**，按住说"切4bg" → 草稿实时刷 → 松手出指令（承载 UI 走通）。
3. "刷两个叉子到前线" / "闪追闪进去" → 验黑话热词命中、指令正确解析。
4. 上滑取消 → 不发。非 HTTPS URL → 回退文字 + 提示。
5. 记 runbook + CHANGELOG + TASKS「当前状态」。
**commit** `docs(asr): 语音输入 runbook + CHANGELOG/TASKS`。

---

## 执行交接

8 个 task 相对独立，按 1→8。Task 2 的 funasr 装不动时先 mock 测、真机阶段补。
每个 task 已含完整 5-step TDD + 文件路径 + commit message → 直接 brief sonnet subagent。
后端先行（1-3）可与前端（4-7）并行（4 独立于后端）。Task 8 全集成后我主导真机验。
