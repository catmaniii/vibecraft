# 语音指令输入 runbook（FunASR）

> 本文是"怎么起、怎么用、怎么排错"。

## 一句话

手机按住麦克风说话 → 流式 FunASR 出字 → 喂进现有 `command` 管线（LLM 解析 / 承载 UI / 历史
全复用）。保留文字输入，微信式切换 + 上滑取消。音频走 WS（funnel HTTPS，不依赖 Tailscale）。

## 启用（一次性）

1. **装 funasr + torch（虚拟环境，别污染全局）**：
   ```
   uv pip install funasr
   uv pip install torch torchaudio        # CPU 版,funasr 不会自动带 torch
   ```
   （pyproject 里 funasr 已声明为可选 extra `[asr]`。没装时 server 照常跑，语音功能禁用、回退文字。）
2. **首次下模型**：`paraformer-zh-streaming`（~GB，从 ModelScope 下一次，之后本地缓存）。
   验证 + 触发下载：
   ```
   .venv/Scripts/python.exe scripts/asr_smoke.py
   # 看到 AsrEngine.available = True + "PASS: 模型加载成功 + 管线跑通" 即 OK
   # 真识别测:.venv/Scripts/python.exe scripts/asr_smoke.py <16kHz_mono.wav>
   ```
3. **热词**：`config/asr_hotwords.txt` 已由 `scripts/gen_asr_hotwords.py` 从别名表+黑话生成
   （924 条，治 4BG/闪追/IAC/VR/VS）。别名/剧本改了重跑该脚本刷新。
4. **起 server + funnel**（同常规，见 CLAUDE.md「PWA 连接」）：
   ```
   .\scripts\start.ps1 -Token vibecraft-dev
   & "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8080
   ```
   server 启动日志会打 `asr_engine_init available=True/False`——False 说明 funasr/torch/模型没就绪。

## 用（手机）

- **必须走 funnel 的 HTTPS URL**：`https://<your-host>.<your-tailnet>.ts.net/?room=vibecraft-dev`。
  浏览器麦克风（getUserMedia）只在 HTTPS 下可用——`http://192.168.x` / `http://100.94.x` 用不了麦克风。
- 默认**语音模式**：按住麦克风说话 → 上方浮层实时显示草稿 → **松手发送**；**上滑取消**（浮层变红）。
- 左下角 toggle 切**文字模式**（现有输入框）；选择记 localStorage。
- 非 HTTPS 进去 → 自动回退文字模式 + 提示"语音需 HTTPS，请用 funnel 链接"。

## 排错

| 现象 | 查 / 解 |
|---|---|
| 手机没有麦克风按钮 | 不是 HTTPS（用了 192.168/100.94 的 http URL）→ 换 funnel HTTPS URL |
| 说了没反应、出不了字 | server 日志 `asr_engine_init available=False` → funasr/torch 没装或模型没下；跑 `asr_smoke.py` 定位 |
| 识别成乱码/黑话听不准 | 热词没生效：确认 `config/asr_hotwords.txt` 存在且 server 起时加载了；别名改了重跑 gen 脚本 |
| 草稿出但松手没指令 | final 为空（噪音/没说清）→ 重说；或看承载 UI 是否报"解析失败"（那是 LLM 层，不是 ASR） |
| available=False 但装了 funasr | 多半缺 torch：`uv pip install torch torchaudio` |

## 自验脚本

- `scripts/gen_asr_hotwords.py` —— 重生热词表。
- `scripts/asr_smoke.py [wav]` —— 验模型加载 + 管线（带 16kHz mono wav 可测真识别）。

## 范围 / 已知限制

- 麦克风需 HTTPS → 只能走 funnel URL（LAN http 不行）。
- funasr+torch CPU 版，单人够用；不抢 SC2 的 GPU。
- v1 不做：VAD 连续模式（不用按住）、端上 ASR、非 HTTPS 下语音（需自签证书）。真机识别准确率以手机实测为准。
