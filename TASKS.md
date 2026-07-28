# TASKS —— 接下来要做什么

> **中文** · [English](TASKS.en.md)

**这份文件只写「将来要做的」。**
已经做完的看 [`CHANGELOG.md`](CHANGELOG.md)，怎么实现的看 [`ARCHITECTURE.md`](ARCHITECTURE.md)，
为什么这样设计看 [`docs/plans/2026-05-14-vibecraft-design.md`](docs/plans/2026-05-14-vibecraft-design.md)。

想动手？先看 [`CONTRIBUTING.md`](CONTRIBUTING.md)，再看本文最后一节「从哪下手」。

---

## 现在到哪了

能用了，还很粗糙。三族都能打，玩家能用语音/文字下从"切剧本"到"叉子闪进去"的四层指令，
手机 PWA 看实时画面，多人同局跑通过真机。默认 bot 能赢 Medium AI、和 Hard 五五开。

| | 现状 |
|---|---|
| 剧本库 | 神族 9 开局 + 8 doctrine，人族 12 + 5，虫族 9 + 5（共 48） |
| 验收 spec | 47 份（`tests/build_acceptance/`） |
| 地图 | **只在 DaybreakLE 上验证过**（见方向 A） |
| 多人 | 单机多实例 host/join + 房间大厅 + 公网前门（云 TURN 中继）已打通 |
| CI | Windows + Python 3.11，全量 3731 个单测 |

**最大的三个缺口就是下面三个方向。**

---

## 三大方向

### 方向 A：多地图适配 ★ 目前最卡的一个

现在整个项目**只在 `DaybreakLE` 上验证过**。换张图很多东西会错，而难点**不是"加载新地图"**——
python-sc2 本来就支持任意地图——**是那些和地形绑死的指令**：

- **named_spot 随图变**："左边瞭望塔""对方 11 点分矿""斜坡口"在每张图的坐标都不同。
- **特色打法的落点**：4BG 的野水晶位置、代理建造点、坑道虫落点、偷矿的隐蔽矿区，换图可能全失效。
- **地形推理**：低地绕行、高地视野这些已经有实现（`bot/terrain_harass.py`、
  `zerg/plans/nydus_landing_planner.py`），但阈值是在一张图上调出来的。

**设计方向**（已定，未实现）：每张图一份「地图档案」——特色 spot 表 + 注入 LLM 的提示词片段——
按当前地图动态加载。`NamedSpotRegistry` 已经是基础设施，需要扩成 per-map。

**怎么算做完**：至少 3 张主流天梯图上，`build_acceptance` 与 `override_acceptance` 的通过率
和 DaybreakLE 持平；"左边瞭望塔"这类指令在每张图都指向正确位置。

---

### 方向 B：build 库持续优化 + 跟上游戏版本

**中后期 doctrine 是明显短板**：神族 8 个，人族和虫族各只有 5 个。开局已经够用，
**赢不下来的局大多输在中后期没有可切的成型打法**。

另外 —— **SC2 每次版本更新都会让所有 build 的节奏失准**。开局农民数、单位数值、建造时间
这类改动，会让 build 里按 supply 写的每一步 timing 全部偏移，`build_acceptance` 的 spec
也要跟着重新校准。**这不是一次性工作，是持续维护。**

**做法**已经成型，照 [`docs/process/new-opening-strategy.md`](docs/process/new-opening-strategy.md)：
先查真实职业 build → 写 yaml → `build_acceptance` 调优 → 六维自检（见 `CLAUDE.md`）。
**不需要读懂整个代码库**，会打 SC2 + 会看日志就能上手，是最适合新人的方向。

**怎么算做完**：三族各 8+ 个中后期 doctrine；版本更新后有一套能全量重跑的流程。

---

### 方向 C：多服务器 —— 志愿者贡献机器 + 房间目录

现在每个人只能连到**一台**主机。想让更多人玩，只能有人自己搭一套。

**卡点是钱，不是技术**：让手机在公网连上你家 PC，需要一台 VPS 做前门和媒体中继，
而 WebRTC 视频是**持续大流量**——维护者一个人的账号扛不住给所有人当中继。

**所以方案是把成本分摊出去**：

- 志愿者提供**自己的 Windows PC**（跑 SC2 + server）**和自己的 VPS**（前门 + TURN 中继），
  **各自的流量各自承担**。
- 维护者的 VPS 上只跑一个**服务器目录**：志愿者的机器启动后注册上来，玩家打开首页就能看到
  一个**可选服务器列表**，挑一台进去。
- **目录本身流量极小**——它只交换"有哪些服务器、在不在线、地址是什么"，
  真正费流量的控制面和媒体面**各走各家的 VPS**。这正是成本能分摊的原因。

**要做的**：目录服务（注册 / 心跳 / 下线）、志愿者一侧的注册客户端与鉴权（防止乱注册）、
PWA 首页从"单服务器"改成"服务器列表"（`web/src/components/EntryView.vue` 已经能显示多台，
但列表现在是本地配置的）、以及给志愿者的一键部署文档。

**怎么算做完**：一个陌生人按文档接入自己的 PC + VPS，其他玩家在首页能看到并连上他的服务器。

---

## 具体待办

按"要不要真机 SC2"分组。带 🎮 的需要一台装了 SC2 的 Windows 机器，其余任何人都能做。

### 不需要 SC2

- **mypy 历史欠账**：`pyproject.toml` 里有 33 个模块的 per-module `disable_error_code`
  （154 条 `union-attr` 集中在 `director.py`）。还清一个删一个，全清完就能去掉整块 override。
- **flaky cross-test**：`test_loads_real_strategies` / `test_transitions_of` /
  `test_not_triggered_when_visible_but_insufficient_duration` —— 单跑永远过，全量偶发失败。
  典型是测试间共享了全局状态。
- **`scripts/sync_to_opensource.py` 已废弃**：它实现的是"私有仓 → 公开仓脱敏投影"的两仓模型，
  而 vibecraft 现在**自己就是公开仓**。可以直接删掉。

### 🎮 需要 SC2

- **人族 / 虫族偷矿支持**：偷矿系统写死了神族（`StealthCellManager` 全链路
  NEXUS/PROBE/ASSIMILATOR/星空加速）。人虫下达偷矿指令目前会被友好拒绝。
  按族抽象即可：虫族 Drone→BH→BE（drone 变建筑会消失、产能走 larva）、
  人族 SCV→BC→BR（SCV 持续建造、有 MULE）。各自要写 selftest 验证饱和。
- **两个 build 的验收残留 FAIL**：
  - `blink_stalker` 15/18 —— 4BG 偏慢 + 追猎产能不足（出门 6-7 个 vs spec 的 10），运输机没出。
  - `iac_2base` 18/19 —— DT 首批只有 2 个（spec 要 4），单折跃门 warp DT 限速。
    注意：iac 科技密集，**提前暴 BG 会拖垮科技线**（实测过），不能照搬 `dt_drop_iac` 的修法。
- **虫族开局验收 spec 补全**：12pool / macro_hatch / mutalisk_harass / roach_hydra /
  brood_corruptor 等还没有 spec。
- **骚扰微操深挖**：寡妇雷逐个卸雷、贴边飞；死神单兵操作方差偏大。
- **e2e smoke**：三族各跑 1 局 vs VeryEasy。

---

## 已知问题 / 技术债

- **验收 check 的固有限制**：瞬态位置类判定（`dt_at_enemy` / `warp_prism_at_enemy` /
  `army_gather`）靠单张快照判，天然 flaky；verifier 的时间窗口判定目前只覆盖计数类。
- **多数功能是 selftest + 截图验的**，长期实战行为还缺真人对局的积累。
- **CI 只覆盖 Windows + Python 3.11**——这不是偷懒，是硬约束（见下）。

---

## 明确不做的

省得有人白忙：

- **跨平台（Linux / macOS）**：vendored sharpy 的核心 manager 无条件 `import sc2pathlib`，
  而上游只提供了 `cp311-win_amd64` 一个编译产物。要跨平台得先自己把 sc2-pathlib（Rust）
  编出对应平台的产物——那是另一个项目的活。
- **Python 3.12+**：同上，那个 `.pyd` 是 cp311 专用的。
- **把 server 搬到云上**：server 必须和 SC2 在同一台机器（要启动游戏、按窗口 PID 抓屏）。
  这是架构不变量，不是待办。
- **天梯 / 排位对战**：本项目是 AI 代打，上天梯违反游戏条款。只面向和 AI 打、和朋友打。
- **本地 LLM**：曾经考虑过，当前明确走云端 + provider 可切换（见 `docs/adr/0005`）。

---

## 从哪下手

三条不同门槛的入口：

1. **会打 SC2，不想读代码** → **方向 B**。挑一个你熟的中后期打法，照
   `docs/process/new-opening-strategy.md` 写成 yaml，跑 `build_acceptance` 调到过。
   全程只碰 yaml 和日志。
2. **会写 Python，没装 SC2** → 「不需要 SC2」那组待办。mypy 欠账和 flaky 测试都是自包含的，
   跑 `uv run pytest` 就能验证。
3. **想啃硬的** → **方向 A（多地图）** 或 **方向 C（服务器目录）**。两个都得先读
   `ARCHITECTURE.md`；A 偏地形算法，C 偏分布式与部署。

动手前建议先开个 Issue 说一声，免得撞车。
