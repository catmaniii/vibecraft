---
marp: true
theme: default
class: lead
paginate: true
---

# VibeCraft 的架构启发
## **BOT Loop + LLM 顾问** vs 主流 **Agent Loop**

—— 一个从 RTS 实战中长出来的反 Agent-Loop 观察

2026-05-25

---

# 起点:一个真实的痛点

- 玩家是**资深 SC2 老选手**,操作不动了
- 想"**用嘴打**" —— 语音 + 文字指挥 AI 替自己手操
- 4bg 一波、占瞭望塔、试探性进攻、全军撤退、给两个 BF 升级攻防...
- **本质矛盾**:RTS 是毫秒级实时博弈,
  LLM 响应 1-10 秒,**做主体根本来不及**

---

# 主流 Agent Loop:LLM 是主体

```
┌──────────────────────────────────┐
│        LLM Agent (主体)           │
│                                   │
│   user → LLM → tool call          │
│           ↑       ↓               │
│           └───observe──┘          │
│                                   │
│   每个决策回合都等 LLM (1-10s)    │
└──────────────────────────────────┘

适用:工作流自动化、代码编辑、研究助手
不适用:RTS / 机器人控制 / 高频交易 / 自动驾驶
```

**LLM 是主体 ⇒ 系统延迟 = LLM 延迟**

---

# VibeCraft 的选择:BOT Loop 是主体

```
┌─────────────────────────────────────────────┐
│   BOT Loop (主体,确定性规则系统)             │  ← 22 tick/秒
│   perceive → decide → act                    │   毫秒级
│             ↑________|                       │
│   ares-sc2 + sharpy + vibecraft acts         │
└──────────────────┬──────────────────────────┘
                   ↑ 异步注入(不阻塞)
┌──────────────────┴──────────────────────────┐
│   LLM (顾问,非主体)                          │
│   • 解析自然语言指令 → directive             │
│   • 推荐宏观战略切换                         │
│   • 跨局总结 / heuristic 维护                │
└─────────────────────────────────────────────┘
```

**LLM 像领导/顾问** —— 给方向,不亲自执行

---

# 两种架构的并排对比

```
        主流 Agent Loop                  vibecraft (BOT-centric)
   ━━━━━━━━━━━━━━━━━━━━━━━           ━━━━━━━━━━━━━━━━━━━━━━━
   ┌─────────────────────┐           ┌─────────────────────┐
   │     LLM (CPU)        │           │   BOT Loop (CPU)     │
   │      ┌───┐           │           │   22 tick/s 毫秒级   │
   │  →  │   │ →          │           │   ┌───┐              │
   │      └───┘           │           │   │   │ 决策执行      │
   │       ↕              │           │   └───┘              │
   │     等 LLM           │           │     ↑ 异步建议        │
   │     (秒级)           │           │   ┌─────┐            │
   │                      │           │   │ LLM │ 顾问(秒级) │
   └─────────────────────┘           │   └─────┘            │
   响应 = LLM 延迟                    └─────────────────────┘
   工作流场景 ✓                       响应 = BOT tick 延迟
   实时场景 ✗                         实时 + 自然语言 ✓
```

---

# 这思路有人系统论证过 —— Jiayi Weng

> "HS 超过一个孤立的 `policy.py`:它至少包含程序策略、状态表示、
> 反馈入口、实验记录、回放或测试、memory,**以及由 coding agent
> 执行的更新机制**。"
>
> —— Jiayi Weng (OpenAI),《Learning Beyond Gradients》

**核心提法:Heuristic Learning / Heuristic System**
- 规则系统不是"过时",而是"过去没人养得起"
- LLM/coding agent 是 **维护者**,不是主体
- "coding agent 更像插进系统里的更新管线"

---

# 关键差异:LLM 是 CPU 还是顾问

| 维度 | LLM 当主体 (Agent Loop) | LLM 当顾问 (BOT Loop + LLM) |
|------|------------------------|-----------------------------|
| 响应延迟 | 受 LLM 响应限制(秒级) | 毫秒级 (BOT 自跑) |
| 鲁棒性 | LLM 错一次毁全局 | 规则系统兜底 |
| 可调试 | 黑盒,prompt 调参 | 代码 + 规则可逐行追溯 |
| 跨局学习 | 靠新 prompt / 微调 | LLM 帮改代码 / 规则 / 测试 |
| 适合场景 | 工作流、对话、研究 | 实时控制、游戏、机器人 |

---

# Heuristic System ≠ Expert System 复活

```
传统 Expert System (1980s):
  人手写规则 → 规则爆炸 → 维护成本失控 → 弃疗

Jiayi Weng 的 Heuristic Learning (2026):
  规则 + 状态 + 测试 + 回放 + log + memory
              ↓
   ┌──────────────────────┐
   │  Coding Agent (LLM)  │  ← 当"营养管道"持续浇灌
   │  • 读 fail log        │
   │  • 改 policy / 测试   │
   │  • 跑回放验证         │
   │  • patch + 提交       │
   └──────────────────────┘
              ↓
   规则系统持续演化,维护成本骤降
```

---

# vibecraft 实战印证 (1):bug 分布

**统计:本周修了 12+ 个 silent bug,几乎全是规则系统的契约漂移**

- bug 1: persistent unit_claim 路径漏调 `execute_unit_action`
- bug 4: ephemeral unit_claim / MOVE 不 cap `selector.count`
- bug 5: ephemeral 卡片 commit 后 pop 消失
- bug 8: `structure_count` 算 building 当 done
- bug 9: auto_prereq 不看 `production_overrides` 重复派
- bug 10: UI button recon 没 default hint → on_hold
- bug 11: sharpy 抢回 LLM_CONTROLLED probe (role 刷新时机错)
- bug 12: plan force_attack 没被玩家 retreat intent 强制 override

**没有一个是"LLM 输出乱"** —— LLM 给的 directive 都对,
**是规则系统多分支 spec 漂移**

---

# vibecraft 实战印证 (2):Heuristic Learning 路径在用

修 bug 的方法跟 Jiayi Weng 思路一致:
1. **加 contract test** —— 让规则系统有可执行 spec
2. **抽 helper 消除分支漂移** —— `_resolve_selector_with_count`
3. **加诊断 log** —— 让下次实测能定位
4. **LLM (coding agent) 持续修维** —— 这次 session 修 12+ bug

> Jiayi Weng 原话:"过去维护成本曲线高,coding agent 改变的是这条曲线"

vibecraft 一个对话内修 12 bug + 加 7+ contract test —— **正在验证这条曲线**

---

# 系统分层:System 1 + System 2

借用 Jiayi Weng 的提法:

**System 1 (毫秒级,确定性,vibecraft BOT 的本体)**
- sharpy/ares 战术执行 (3000+ LOC vendor)
- 单位 micro、生产队列、攻防判定
- vibecraft 自定义 acts (DT drop / IAC midgame / 4bg pressure / ...)

**System 2 (秒级,LLM 顾问)**
- 解析"两个 BF 升级攻防" → 3 条 directive
- 推荐"opening 完成 → 切持续骚扰"
- 跨局总结策略 (未来:cross-session heuristic 维护)

---

# 实战收益:为什么这个架构能 work

1. **可玩** —— LLM 失败时玩家还能用 UI 按钮兜底,BOT 不"瘫痪"
2. **可学** —— 玩家口语指令的边界 case 持续教 LLM(prompt + few_shot)
3. **可调** —— 每个 bug 都能定位到具体规则/分支,
              不是"LLM 又抽风"
4. **可扩** —— 加新指令 = 加 directive type + handler + 契约测试,
              **不需要重训模型**

---

# 后续路线 (跟 Heuristic Learning 一脉相承)

**短期 (已在做)**
- 补完 14 directive type 契约测试 (audit plan: `docs/plans/2026-05-25-directive-contract-audit.md`)
- 修玩家实测发现的 silent bug
- 改 prompt 让 LLM 不瞎猜不存在的 named_spot

**中期**
- coding agent 自动 propose patch:读对局 log + 玩家反馈 → 改 prompt/规则
- 自动跑 build_acceptance 回归 → 自动 commit / 自动 PR

**长期**
- 把"BOT Loop + LLM 顾问"抽象成通用框架
- 适用:**机器人、自动驾驶辅助、交易系统、IDE 助手**

---

# 一句话总结

> **LLM 不是 CPU,是顾问。**
>
> 给确定性规则系统配一个 LLM 顾问 + coding agent 维护管线,
> 比把 LLM 当主体跑 Agent Loop 更适合实时场景。

—— vibecraft 实战 + Jiayi Weng《Learning Beyond Gradients》共同印证

---

# 致谢与参考

- **Jiayi Weng (OpenAI)**, *Learning Beyond Gradients*
  <https://trinkle23897.github.io/learning-beyond-gradients/>
- **Bojie Li**, 访谈感悟《人和模型一样,最重要的是 Context》
  <https://01.me/2026/01/jiayi-weng-interview-insights/>
- **vibecraft** 项目实战经验
  本 repo, 2026-05-14 起

2026-05-25

---

<!-- _class: lead -->

# 谢谢
