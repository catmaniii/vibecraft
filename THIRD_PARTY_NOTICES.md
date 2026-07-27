# Third-Party Notices

VibeCraft 自身源码以 **MIT** 协议发布（见 [`LICENSE`](LICENSE)）。**那份 MIT 只覆盖 VibeCraft
自己写的代码**；本项目还**打包 / 依赖**下列第三方软件，各自的协议适用于相应部分，并且在
Blizzard Entertainment 的条款下与《星际争霸 II》交互（见第四节）。所有依赖均为**宽松协议
（MIT / BSD / Apache-2.0）**，与 MIT 兼容；**无 copyleft（GPL/LGPL 等）依赖**，整体可以 MIT 发布。

> `LICENSE` 文件刻意只放 MIT 原文、不附加说明 —— GitHub 靠文本相似度识别协议，掺入额外段落
> 会让它判成 “Other”、仓库页不再显示 MIT 标签。作用域说明因此放在这里。

---

## 一、随仓库打包的第三方代码（vendored）

### `vendor/sharpy/` — sharpy-sc2

- **上游**：DrInfy / sharpy-sc2 — https://github.com/DrInfy/sharpy-sc2 （cloned commit `d9577a0`）
- **协议**：MIT，Copyright (c) 2019 DrInfy（完整文本见 [`vendor/sharpy/LICENSE`](vendor/sharpy/LICENSE)）
- **VibeCraft 对其做了修改**：在 15 个文件就地加入"玩家覆盖"hook（均以 `# vibecraft:`
  注释标记），用于把玩家语音/UI 指令接入 sharpy 的战斗 plan。改动清单见
  [`docs/sharpy-patches.md`](docs/sharpy-patches.md)，vendored 说明见
  [`vendor/sharpy/ATTRIBUTION.md`](vendor/sharpy/ATTRIBUTION.md)。
- **合规性**：MIT 授予修改、再分发的权利。原始版权声明与协议文本（`vendor/sharpy/LICENSE`）
  **已随仓库保留**，修改之处**已标注**。因此修改后的 vendored sharpy 仍完全符合 MIT。

> 注：早期曾本地 vendored `august-k/Aristaeus` 作神族基类试验，后迁移到 sharpy 弃用。该目录
> 从未纳入 git、当前代码零引用，**已删除**，故不涉及其授权。

---

## 二、运行时依赖（pip / uv 单独安装，不随本仓打包，各自协议）

| 包 | 用途 | 协议 |
|---|---|---|
| python-sc2（burnysc2，august-k fork） | SC2 bot API | MIT |
| ares-sc2（AresSC2） | bot 框架 | MIT |
| pydantic / PyYAML / structlog / anyio | 数据模型 / 配置 / 日志 / 异步 | MIT |
| mss / comtypes / pyaudiowpatch / qrcode | 抓屏 / COM / 音频 / 二维码 | MIT |
| anthropic（SDK） | LLM 调用 | MIT |
| aiortc / av(PyAV) / websockets / httpx / click / numpy / pygetwindow | WebRTC / 媒体 / 信令 / HTTP / CLI / 数值 / 窗口 | BSD |
| FunASR（可选，extra=asr） | 服务端语音识别 | MIT |
| ModelScope（FunASR 传递依赖，模型下载） | 模型仓库客户端 | Apache-2.0 |
| PyTorch（FunASR 传递依赖，ASR 推理） | 深度学习框架 | BSD-3-Clause |

> 以上为常见认定，**以各自上游 LICENSE 为最终依据**。这些包以独立分发形式安装，VibeCraft
> 未修改其源码。
>
> **vendored sharpy 内部再打包了次级第三方组件**（上游 DrInfy 即已 bundle，非本项目引入）：
> `jsonpickle`（BSD，`vendor/sharpy/jsonpickle/COPYING.txt` 已保留）、`sc2pathlib`
> （DrInfy 的 sc2-pathlib，MIT，编译产物 + py 包装）。
>
> **已移除**：`vendor/sharpy/libs/ic52.zip`（6 MB）。它是三个 ICU 的 Windows 预编译 DLL
> （`icudt52.dll` / `icuin52.dll` / `icuuc52.dll`，2019 年构建），**并非 MIT** —— ICU 有自己的
> Unicode/ICU License，来源与构建方式也无从核实。它在上游只被 `bot_loader/ladder_zip.py` 用于
> 把 bot 打包成 AI Arena ladder 的 PyInstaller 独立可执行文件；VibeCraft 从源码进程内跑 bot，
> **完全不走这条路径**。既然用不上、又会连带再分发一份来路不明、协议标注还是错的二进制，
> 直接删掉。（需要打 ladder 包的人请自行从 [ICU 官方](https://github.com/unicode-org/icu)
> 取对应版本。）
>
> **ASR 模型权重不随仓库分发**：FunASR 运行时从 ModelScope 自动下载（如 paraformer-zh-streaming /
> paraformer-en / SenseVoice 等），各模型受其在 ModelScope 上的**模型许可协议**约束，使用者请自行确认。

---

## 三、前端依赖（`web/`，构建产物入 `src/vibecraft/server/static`）

Vue 3 / Vite / Tailwind CSS 及其传递依赖 —— 均为 **MIT**。

---

## 四、StarCraft II / Blizzard Entertainment

**VibeCraft 是非官方的粉丝项目，与 Blizzard Entertainment, Inc. 无任何隶属、合作或背书关系。**

- **商标**：StarCraft®、StarCraft II®、Blizzard® 及相关名称、标志、形象是 Blizzard
  Entertainment, Inc. 在美国及/或其他国家的商标或注册商标。
- **正版前提**：运行 VibeCraft 需要你**自行拥有一份合法的 StarCraft II**。本仓库不分发、不
  包含任何 Blizzard 的游戏文件、地图包或回放包。
- **界面图标**：手机面板上的单位/建筑/升级图标是 Blizzard 的版权美术，**不随本仓库分发**。
  它们由 [`scripts/download_sc2_icons.py`](scripts/download_sc2_icons.py) 在**你本地**从
  Liquipedia 拉取，仅供个人非商业使用；仓库里只有那个下载脚本和文件名映射表。（2026-07-27
  开源前把已入库的 163 个图标撤出版本控制，同时移除了 vendored sharpy 自带的 ladder 地图
  `Equilibrium513AIE.SC2Map` —— 地图请用你自己 SC2 安装里的。）
- **AI/ML API 条款**：VibeCraft 通过 StarCraft II 的 AI/机器学习 API（s2client / SC2API）
  观测并操作游戏。**该接口及 Blizzard 提供的对战地图 / 回放包受 Blizzard 的
  "StarCraft II AI and Machine Learning License" 约束——仅授权用于非商业的研究与 AI 用途**，
  同时须遵守 StarCraft II 最终用户许可协议（EULA）。
- **范围澄清**：VibeCraft 的 MIT 协议**仅覆盖 VibeCraft 自身源码**，**不授予**任何对
  StarCraft II、Blizzard 知识产权或上述 Blizzard 许可的权利。**请勿**将本项目用于任何违反
  Blizzard 上述非商业条款 / EULA 的商业用途。

参考：Blizzard s2client-proto（https://github.com/Blizzard/s2client-proto，下载需同意 AI and
Machine Learning License）。
