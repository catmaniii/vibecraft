# 快速上手（5 分钟跑通）

从零把 VibeCraft 跑起来，用手机说一句话指挥 SC2。面向**自己搭一套**的人（玩家只需主办方给的链接，见 README）。

## 0. 前置检查（缺一项都会卡）

- **Windows 10/11** PC（音频分流 / 启动脚本目前 Windows-only）
- 已安装并**至少启动过一次**正版 **StarCraft II**
- **Python 3.11**（只支持 3.11，不是 3.11+）和 **uv**（https://docs.astral.sh/uv/）
- **DeepSeek API key**（解析指令用，按量付费，platform.deepseek.com 申请）

## 1. 拉代码 + 装依赖

```bash
git clone https://github.com/catmaniii/vibecraft.git
cd vibecraft
uv sync --extra dev --extra sc2-lib      # 核心 + bot 运行所需（python-sc2 / sharpy 依赖）
# 注：--extra sc2（不带 -lib）是给旧的 M0 ares smoke 用的，跑 bot 本身不需要
# （可选）语音识别：FunASR + torch（体积大，见末尾「语音识别」注意）
```

## 2. 配 LLM key（二选一）

```bash
# A. 环境变量
setx DEEPSEEK_API_KEY "sk-..."           # Windows，新开终端生效
# B. 配置文件（cp 模板后填 key；config/llm.yaml 已 gitignore，不会入库）
cp config/llm.yaml.example config/llm.yaml
```

## 2.5 拉 SC2 图标（首次，约 1 分钟）

```bash
uv run python scripts/download_sc2_icons.py
```

面板上的建筑/单位/升级图标是**暴雪版权美术**，本仓库不分发（见
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) 第四节），所以 clone 下来是没有的 —— 跑
一次脚本从 Liquipedia 拉到本地即可（会自动同步到 server 的 static 目录，不装前端工具链也能用）。
**不跑也能玩**，只是面板上会缺图。

## 3. 构建前端（首次 / web 改动后）

```bash
cd web && npm install && npm run build   # 产物写入 src/vibecraft/server/static/
cd ..
```

## 4. 起服务器

```powershell
.\scripts\start.ps1                       # 默认端口 8080，启动后打印二维码 + URL
# 想命名 server / 固定房间码等见 README「二、自行部署」
```

## 5. 手机连接 + 第一条指令

1. 手机和 PC 同 WiFi → 浏览器扫终端二维码（或访问 `http://<PC局域网IP>:8080/?room=<token>`）。
   - 异地：见 README「公网连接」（Tailscale / VPS 中继）。
2. 输昵称 → 选服务器 → 连接 → 进驾驶舱。
3. 在游戏里（bot 接管你的 SC2）说一句，例如：
   - 「**造两个兵营**」 / 「**全军进攻**」 / 「**派个农民去侦察**」
4. 看 bot 执行。指令体系详见 [USER_GUIDE.md](../USER_GUIDE.md)。

---

## 常见卡点

| 现象 | 排查 |
|---|---|
| 指令"解析失败" | `DEEPSEEK_API_KEY` 没设 / 网络不通 / key 余额 |
| 手机打开白屏 | 没跑 `npm run build`；或 PWA 缓存旧版（用隐私窗口） |
| 找不到 SC2 | 设 `SC2PATH` 环境变量指向 SC2 安装目录 |
| 语音识别不工作 | 见下「语音识别」 |

## 语音识别（可选）

- 默认用 **FunASR**（中文流式 `paraformer-zh-streaming` + SC2 术语热词 `config/asr_hotwords.txt`）。
- **坑**：FunASR + torch 体积大、常需手动 `pip install`；`scripts/start.ps1` 用 `--no-sync` 正是
  **防止 `uv sync` 把手动装的 torch/funasr 删掉**。"语音突然不工作"先确认它们还在 venv 里。
- **模型权重不入库**：FunASR 运行时从 **ModelScope** 自动下载，受其各自**模型许可协议**约束，请自行确认。
- 不装语音也能用——**打字指令**全程可用。
