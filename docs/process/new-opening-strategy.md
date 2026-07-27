# 引入 / 审计一个开局策略 — 可复用流程

> 配套框架设计见 `docs/plans/2026-05-20-build-acceptance-testing-design.md`。
> 本文档是引入新开局策略、或审计一个现存 build 是否合理的**标准流程**。

每个 build order 走这 7 步。既用于新策略，也用于回头审计现存 build
（审计 = 把现存 build 当"待验收对象"跑一遍同样流程）。

---

## 第 1 步 — Deep research 标准节奏

上网搜该 build 的权威 timing，收集标准节点：

- **来源**：spawningtool.com（pro 录像 build order）、Liquipedia、TeamLiquid
  build guide、高手 YouTube 教学
- **要提取的节点**：关键建筑几分钟下 / 几分钟完成、关键科技几分钟研究好、
  第几分钟累积多少农民、部队几分钟集结、在哪集结、空投棱镜几分钟到位、
  部队几分钟出门压制
- 交叉验证 2-3 个来源，取一个合理的代表值

把来源链接记下来（第 7 步要写进 spec 文件头）。

## 第 2 步 — 写 acceptance spec

新建 `tests/build_acceptance/<strategy_id>.yaml`。格式见
`docs/plans/2026-05-20-build-acceptance-testing-design.md` §3。

- 文件头注释写 research 来源链接
- 每条 check：`{id, type, at+tol 或 by, 目标参数}`
- 时间用 `M:SS`
- 位置用命名锚点 `home` / `enemy_main` / `natural` + `within`
- check 类型：`building_started` / `building_complete` / `upgrade_complete` /
  `worker_count` / `unit_count` / `building_count` / `key_unit_at` /
  `army_gather` / `attack_moveout`
- 计数类 check（`worker_count` / `unit_count` / `building_count`）的
  `at±tol` 是**时间窗口**：窗口内计数达到过 `min` 即 PASS（窗口取最大值，
  `by` 模式窗口为 `[0, by]`）。`tol` 按"这个数量能稳定达成的时间范围"给。
- **经济曲线**（推荐每个 build 都写）：`economy_profile` 段，列几个关键
  时间点的标准农民数 / 标准余钱（`{at, workers, minerals, vespene}`，后三
  者至少填一个）。verifier 算实测与标准值的偏差分——**纯分数、不做
  pass/fail**。标准值是**迭代改进**的：先填粗估，观察跑得好的 run 后回写
  校准。每个 build 自己的标准曲线天然编码意图（all-in 标准就是低农民），
  不需要额外区分 all-in / macro。

## 第 3 步 — 写 / 改 plan 代码

- sharpy plan：`src/vibecraft/bot/auto_combat/<race>/plans/<strategy_id>.py`
- strategy 定义：`strategies/<race>/<strategy_id>.yaml`
- 审计现存 build 时，这一步是"对比 research 出的标准节奏，找 plan 代码里的
  不合理处并改正"（例：dt_drop_iac 曾在出隐刀前出叉子+哨兵 —— 跟标准节奏不符）

## 第 4 步 — 跑 runner（VeryEasy 档，3 跑取多数）

```
uv run python scripts/build_acceptance.py <strategy_id> --runs 3
```

默认 `--opponent veryeasy`：VeryEasy 内置 AI 几乎不干扰，验"无压力下 build
骨架时序"。`--runs 3`：non-realtime SC2 单局仍有方差（帧抖动、单位交战
随机性），边界 check 会在不同局间 PASS/FAIL 翻转 —— 跑 3 局按 check 多数票
判定，消除单跑噪声。**多局必须串行**（两个 SC2 实例并发会撞）。

## 第 5 步 — 读报告，修循环

runner 输出 `N/M passed` + 每条 check 的多数票结果 `[pass/run]` + 代表性
`actual vs expected`，并写 `logs/build_acceptance/<id>_<档>_<ts>.txt`。

报告末尾还有一段**经济曲线偏差分**（spec 配了 `economy_profile` 才有）：
百分比越小越贴近标准曲线，逐时间点列 `实测/标准(±偏差)`。这是纯分数、
**不计入 pass/fail** —— 里程碑可能全 PASS 但经济偏差分很高（典型：build
浮钱严重，产能喂不饱收入），据此判断要不要加产能 / 调采矿 / 转型。

- **acceptance-fail**（build 时序不符）：读本局 `logs/game_*/telemetry.jsonl`
  分析实际发生了什么 → 判断是 plan 真有问题 还是 spec 数值不准（research 偏差）
  → 改 plan（或修正 spec）→ 重跑。循环到全 PASS。
- **infra-fail**（SC2 崩溃 / `hang_watchdog` 触发 / 进程异常）：runner 已自动
  retry ≤3 次。连续 3 次 infra-fail → runner 报 "INFRA BROKEN"，这要人工
  排查（不是 build 的问题）。

判断 fail 性质的依据：infra-fail 是基础设施（runner 自动处理）；
acceptance-fail 是 build 逻辑（要改 plan）。两者别混。

## 第 6 步 — 跑 CheatMoney 档（3 跑取多数）

VeryEasy 全过后：

```
uv run python scripts/build_acceptance.py <strategy_id> --opponent cheatmoney --runs 3
```

CheatMoney 验抗压。verifier 自动放宽：`tol×2` + 跳过位置类断言 + 主要验
"科技/建筑/出门最终都达成、骨架没崩"。抗压本身方差更大，3 跑多数票尤其必要。

## 第 7 步 — 沉淀

- 确认 spec 文件头的 research 来源链接完整
- 该 build 的 acceptance spec 进库，以后任何改动都能用 `scripts/build_acceptance.py`
  回归验证

---

## 首次落地：审计所有现存 build

框架建成后，用本流程逐个审计现存 build，按 race 分批，先 protoss opening：

`4bg` / `iac_2base` / `dt_drop_iac` / `1g_robo_immortal` / `blink_stalker` /
`dt_rush` / `phoenix_2base` / `cannon_rush` → 然后 zerg / terran。

每个 build = 一次完整 7 步。
