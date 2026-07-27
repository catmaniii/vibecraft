# Skytoss 后期剧本 build order 实施计划

> 日期: 2026-05-18
> 状态: 规划中（等用户拍板）
> 背景: 玩家测试发现 skytoss 没有真实的 build order，bot 切到 skytoss 后行为
>      跟 4bg 完全不同 —— 4bg 有 Gate4Pressure plan 严格按 build 跑，skytoss
>      只指向 sharpy 自带的 MacroVoidray dummy（暴虚空，不是真航母组合）。

## 一、现状盘点

### 文件 `strategies/protoss/skytoss.yaml` 内容

```yaml
kind: lategame_doctrine
id: skytoss
sharpy_dummy_class: "dummies.protoss.voidray:MacroVoidray"  # ← 问题在这

target_composition:
  carrier: 12
  tempest: 3
  high_templar: 5
  archon: 4
  mothership: 1
  observer: 2

required_tech: [FleetBeacon, ProtossAirWeapons, ProtossAirArmor, PsiStorm, GravitonCatapult]
required_structures: {stargate: 4, fleet_beacon: 1, templar_archives: 1}
engagement_doctrine: [...]
```

### 对比 4bg（标杆）

```yaml
kind: opening_build
sharpy_dummy_class: "vibecraft.bot.auto_combat.protoss.plans.gate4_pressure:Gate4Pressure"
                                                          ↑
                                              我们自己写的 plan class

phases: [opening, tech, massing, forward, pressuring]  # ← UI 阶段进度
steps: ["9 build BE", "13 build BG", ...]              # ← build 步骤
scout_at: "13 send_probe enemy_natural"
abort_signals: [...]
default_transitions: [...]
```

### 真实运行差异

- **4bg**：玩家切到 4bg → `Gate4Pressure.create_plan()` 接管，严格按
  `gate4_pressure.py` 里写的 BuildOrder 跑（BE/BG/BY/折跃/3 BG/出门）
- **skytoss**：玩家切到 skytoss → sharpy `MacroVoidray` 类接管，行为是
  "无脑暴虚空"，**不造航母、不造母舰、不出 HT/Archon、不出 Templar Archives**

两个问题：
1. plan class 不对（dummy 不是真 Skytoss）
2. schema 是 `lategame_doctrine` 没 `phases`/`steps`，**就算 plan 对了 UI 也显示不出阶段进度**

## 二、设计选择

### 选项 A：扩 LategameDoctrine schema 加 phases/steps

把 lategame_doctrine 改成跟 opening_build 一样含 phases/steps，自己写
`Skytoss` plan class。

**优点**：UI 一致性好，phases stepper 跟 4bg 表现一致。
**缺点**：lategame_doctrine 设计本意是"组合 + 交战原则"，加 build steps 跟
"后期持续运营 + 兵种切换"的语义有冲突。后期没有严格 supply 节点的 build order。

### 选项 B：分两层 —— phases 描述阶段，但不强制 build steps

```yaml
phases:
  - id: tech_up
    display: "上 VS + VX"
    subtitle: "升 ProtossAirWeapons/Armor 1，造 VX，3-4 VS"
  - id: carrier_massing
    display: "暴航母"
    subtitle: "VX 完成 → 持续造 carrier，HT 攒能量"
  - id: late_composition
    display: "完整组合"
    subtitle: "12 carrier + 3 tempest + HT 风暴 + 1 mothership"
```

无 `steps` 字段（lategame 没有严格 supply 步骤），但 phases 让 UI 显示阶段进度。

**推荐 B**：保持 lategame_doctrine 的"组合驱动"语义，只加 phases 让 UI 有内容显示。

### 选项 C：什么都不动，留 sharpy MacroVoidray

这就是当前状态。玩家明确说"错的"，否决。

## 三、Skytoss build 内容设计

参考 SC2 实战 Skytoss：

| 阶段 | 触发条件 | 关键动作 |
|---|---|---|
| **transition** | 切到 skytoss 时 | 立即升级 ProtossAirWeapons/Armor lv1，造 2nd Stargate，开始造 VX (Fleet Beacon) |
| **tech_up** | VX 在建 | 补 3-4 VS，TA (Templar Archives) 一好就研 Storm，开始造 HT |
| **carrier_massing** | VX 完成 + 第一个 Carrier 出 | 持续 train Carrier，HT 攒能量，按需出 Archon |
| **late_composition** | Carrier ≥ 8 | 加 Tempest 反 Lategame Air，造 Mothership，对方有 Viking/Corruptor 时切 Storm + Feedback |

### 不写"5 supply build BE"这种严格 step

后期玩家手里已经有完整经济 + 高科技，build order 不像开局那么 strict。剧本本质
是"按优先级生产 + 满足 target_composition"，不是"第 N supply 干什么"。

## 四、实施步骤

### Step 1: schema 扩展（30 min）

`src/vibecraft/strategy/models.py`：
```python
class LategameDoctrine(BaseModel):
    ...
    phases: list[Phase] = Field(default_factory=list)  # 新增，可选
```

无新 validator（lategame 不强制 phases）。

### Step 2: 写 Skytoss plan（2-3h）

新文件 `src/vibecraft/bot/auto_combat/protoss/plans/skytoss.py`：

```python
class Skytoss(KnowledgeBot):
    def __init__(self):
        super().__init__("VibeCraft Skytoss")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 持续运营（chrono、农民、扩张）
            ProbeProduction(),
            AutoPylon(),
            # 升级 chain
            ChronoTech(AbilityId.RESEARCH_PROTOSSAIRWEAPONSLEVEL1, ...),
            # VS / VX 建造
            Step(UnitReady(NEXUS, 3), GridBuilding(STARGATE, 4)),
            Step(UnitReady(STARGATE, 1), GridBuilding(FLEETBEACON, 1)),
            # TA + Storm
            Step(UnitReady(TWILIGHTCOUNCIL, 1), GridBuilding(TEMPLARARCHIVES, 1)),
            Step(UnitReady(TEMPLARARCHIVES, 1), Tech(PSISTORMTECH)),
            # Carrier 持续训练
            Step(UnitReady(FLEETBEACON, 1), ProtossUnit(CARRIER, 12)),
            Step(UnitReady(TEMPLARARCHIVES, 1), ProtossUnit(HIGHTEMPLAR, 5)),
            Step(self._after_8_carriers, ProtossUnit(TEMPEST, 3)),
            # 战斗
            DistributeWorkers(), PlanZoneDefense(),
            VibeCraftZoneAttack(8),  # 8 个航母再出门
        )

    @staticmethod
    def _after_8_carriers(ai): return ai.units(CARRIER).amount >= 8
```

### Step 3: skytoss.yaml 改 dummy 指向 + 加 phases（10 min）

```yaml
sharpy_dummy_class: "vibecraft.bot.auto_combat.protoss.plans.skytoss:Skytoss"

phases:
  - id: tech_up
    display: "上 VS + VX"
    subtitle: "升空军武器，多 VS，开造 VX/TA"
  - id: carrier_massing
    display: "暴航母"
    subtitle: "VX 完成 → 持续 Carrier，HT 攒能量"
  - id: late_composition
    display: "完整组合"
    subtitle: "8+ Carrier + Tempest + HT Storm + 母舰"
```

### Step 4: PhaseTracker 适配（30 min）

`src/vibecraft/bot/auto_combat/protoss/bot.py` 或 phase_tracker 加 skytoss 的
phase 推断规则（基于建筑/单位存量判断当前 phase）。可能现有 PhaseTracker 框架
已经足够，只需要补 skytoss 的 phase definitions。

### Step 5: 单测（1h）

- `Skytoss.create_plan` 不抛异常（fake_sharpy fixture）
- skytoss.yaml schema validate 通过
- StrategyLibrary 能 get("skytoss") 拿到带 phases 的对象

### Step 6: e2e smoke（30 min）

真实 SC2 起一局，opening=1g_robo_immortal → 5 分钟切 skytoss →
观察是否真的造 VS/VX/Carrier/HT，PWA 是否显示 phase 进度。

## 五、工时合计

| Step | 工时 |
|---|---|
| Schema 扩展 | 0.5h |
| Skytoss plan class | 2-3h |
| yaml 改 + phases | 0.2h |
| PhaseTracker 适配 | 0.5h |
| 单测 | 1h |
| e2e smoke | 0.5h |
| **总计** | **5-6h** |

## 六、风险

1. **sharpy `ProtossUnit(CARRIER, 12)` 可能不会自动管 Interceptor 数量** —
   航母满载需要 8 Interceptor，sharpy 是否自动 train 要验证。如果不，需要
   每 step 检查 `unit.interceptor_count` 手工 train。
2. **VX (Fleet Beacon) 升级时间长**（~60s），LLM 可能误判 "skytoss 转太慢"
   而切回 IAC。需要在剧本 `expire_action` 或 `abort_signals` 处加保护。
3. **后期 supply 卡** —— Carrier 8 supply / Tempest 5 supply，12 Carrier 就
   96 supply，加上 HT/Archon 必然 supply 卡，要 AutoPylon 持续补。
4. **测试不易**：lategame 必须打到 12+ 分钟才能验证，fast mode 也要几分钟。
   可能要先在测试环境 force `1g_robo_immortal` 配合 `set_initial_strategy`
   skip 到 lategame。

## 七、范围外（后续）

- Skytoss 的 abort_signals（什么时候 lategame 也撑不住 → 投降？）
- Skytoss vs 不同种族的兵种调整（vs Z 加 Tempest，vs T 加 Mothership）
- Skytoss 的 micro_doctrine 真接 sharpy（feedback / storm / kite）—— ADR 0011
  L2 战术执行器已有部分基建，可复用
- 母舰技能 mass recall 真实施

## 八、决策清单

- [ ] 选项 A vs B：是否给 lategame_doctrine 加 phases？建议 B
- [ ] phases 是否需要 supply 触发条件？建议否（lategame 没严格 supply）
- [ ] 工时优先级：跟 recon (火力侦查) RIF 哪个先？
- [ ] 是否 v0.1 必须？还是 v0.5 才做？
