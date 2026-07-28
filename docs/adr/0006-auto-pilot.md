# ADR 0006：基础 bot 通用 auto-pilot

日期：2026-05-15
状态：已采纳（**决策仍然生效；下方"实现"一节描述的是当时的做法，已随框架迁移重写**）

## 背景

M1.6 真实启动 SC2 端到端时，用户（资深 SC2 玩家）反馈基础 bot「太弱」——闲置农民
不采矿、opening build 跑完后 bot 躺平。根因：bot 只接了 opening build runner，
**没接任何持续运营行为**（采矿 / 出兵 / 扩张等），所以 opening steps 之外没有自动运营。

设计文档 §6「基础 bot 能力标定」明确要求：经济/后勤 STRONG、建造/生产 STRONG、
玩家沉默时 auto-pilot。当前是实现缺口，不是设计问题。

## 决策

bot 每 tick 注册一套 macro 行为，**分两阶段**，以 opening build 是否跑完分界：

- **阶段一（opening 未跑完）**：只跑不和 build runner 抢资源的采矿 + 自动补给。
- **阶段二（opening 完成）**：追加会主动造建筑 / 出兵的行为（补农民、补气矿、
  扩张、产能控制、出兵）。opening 期间它们会和 build runner 的建筑/出兵节奏冲突。

通用军队组合（出兵与产能共用）：不朽 0.25 / 追猎 0.55 / 叉子 0.20。

## 范围标定

目标：**无玩家干预时 ≈ 普通电脑（Medium AI）级别**（用户明确接受这个标定）。
「按 midgame/lategame 剧本自动转」**不在此** —— 本 ADR 只管「通用兜底」，让 bot 不躺平。

> 这条标定后来固化成了一条更强的纪律：**玩家没确认，bot 绝不自动切战术 / doctrine /
> 兵种战略**（见 `CLAUDE.md`「玩家控制权模型」）。auto-pilot 只兜底运营，不替玩家做战略决策。

## role 隔离（关键约束）

auto-pilot 的所有行为**不碰被玩家点名接管的单位**。所有造建筑 / 扩张 / 补气类行为
选农民时只从「正在采矿」的那批里取；产能类行为只操作生产建筑、不按角色抓 army 单位；
自动派角色只对农民生效，auto-pilot 出的兵处于无角色 idle 状态。**全部天然隔离。**

这条约束在后来的框架迁移中保留了下来，现在的表达是
`UnitRole.LLM_CONTROLLED` → sharpy `UnitTask.Reserved`：被玩家 claim 的单位对
base bot 的所有自动行为**不可见**。

## 实现

> ⚠️ **原始实现已废弃。** 本 ADR 落地时 bot 建立在另一个框架上，auto-pilot 是靠每 tick
> 重新注册那个框架的 macro behavior 实现的。M1 全框架迁移到 sharpy（见
> [ADR 0009](0009-sharpy-migration.md)）之后，同样的决策改由 sharpy 的 Act 体系承载 ——
> 当前对应 `bot/auto_combat/opening_sustain_act.py`（opening 完成后的持续运营）与
> `bot/auto_combat/worker_saturation_floor.py`（农民饱和兜底）。
>
> **保留本 ADR 是因为决策本身仍然生效**：两阶段分界、Medium AI 的能力标定、
> 以及「不碰玩家接管的单位」这条隔离约束。实现细节以代码与 `ARCHITECTURE.md` 为准。

## 当时的验收点（历史记录）

- opening 跑完后 3-4 基地饱和采矿、农民补到 ~66、supply 不卡、有稳定兵流
- 被玩家接管的单位数不受 auto-pilot 影响
- 神族折跃门出兵走折跃路径
- 玩家中后期切新 long opening 时，"opening 已完成"标志的短暂冲突
