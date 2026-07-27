# 火力侦查（Recon-in-Force）实施计划

> 日期: 2026-05-18
> 状态: 规划中（等用户拍板）
> 背景: TacticalObjective verb `recon` 已加入 schema 和 LLM 路由，但执行路径退化
>      到 `MoveType.Assault`，等同于 verb=attack 的 committed 大军行为。本计划
>      把"真正的火力侦查"作为完整 L2 旗舰行为补齐。

## 一、语义定义（军事术语 Reconnaissance in Force / RIF）

```
RIF(squad, candidate_points 按预期价值排序):
  for point in candidates:
    move_to(point)
    短促接火
    收集敌情（兵力 / 兵种 / 防守密度 / 关键建筑）
    评估：
      - 发现弱点 → 升级为 harass，停在该点继续打
      - 不划算 / 信息够了 / 反包风险 → 切下一个点
      - 整体战损超阈值 → 撤（兜底，不是主驱动）
  所有点扫完无弱点 → release directive（task_monitor 兜底）
```

关键区别（用户原话纠正过）：**"A 处占不到便宜就去 B 处，到处找机会"** ——
不是单点 hit-and-run（hit-and-run = harass 单点反复磨）。

## 二、现状盘点

### 已通

| 组件 | 状态 |
|---|---|
| `TacticalVerb` enum 含 `recon` | ✅ `models.py:189-198` |
| LLM prompt verb 白名单 + few-shot 例 10 | ✅ `prompt.py:166-178, 350-364` |
| director `_B_VERBS` 含 `recon` | ✅ `director.py:104` |
| `_exec_l2_squad` 抢占 N 个单位 → LLM_CONTROLLED | ✅ `director.py:2212-2253` |
| 中文显示 "火力侦查" | ✅ `director.py:696` |
| done_when 释放路径（task_monitor） | ✅ |

### 退化的部分

| 问题 | 位置 | 影响 |
|---|---|---|
| MoveType 退化到 Assault | `director.py:2236` | 行为 = committed attack，不是试探 |
| 没有 squad 级状态机 | — | 缺切点逻辑 |
| 没有候选点派生 | — | LLM 只给单点 |
| 没有"短促接火"评估 | — | 无法判断 advantage/disadvantage |
| 没有"发现弱点升级 harass" | — | RIF 的核心收益缺失 |

## 三、架构设计

### 数据结构

```python
@dataclass
class ReconState:
    """单个 recon directive 的状态机（attach 到 TacticalSquad）。"""
    directive_id: str
    candidates: list[Point2]            # bot 派生的候选点序列（按价值排序）
    current_idx: int = 0                # 当前在哪个候选点
    dwell_started_at: float = 0.0       # 在当前点停留起始 game_time
    engaged_at: float | None = None     # 首次接火 game_time（None = 还没接火）
    weak_point_found_at: int | None = None  # 发现弱点的 candidate_idx
    # 战损评估初始 snapshot
    initial_squad_hp_sum: float = 0.0
```

新文件 `src/vibecraft/bot/auto_combat/recon_controller.py`，定义
`ReconSquadController`（不是 ActBase；由 director 在 `execute_tactics_step`
里调度）。

### 调度路径

```
director.execute_tactics_step(now)               每 sharpy step
  for squad in _tactical_squads:
    if squad.verb == "recon":
      recon_controller.tick(squad, now)          ← 新增分支
    else:
      cm.add_units + cm.execute(...)             原 harass/scout 路径不变
```

### 切点决策（三轴）

| 触发 | 条件 | 优先级 |
|---|---|---|
| **发现弱点** | 视野内敌方兵力总价值 < squad 兵力总价值 × 0.7 | 最高（不切，升级 harass）|
| **战损超阈值** | squad HP 整体 < initial × 0.6 | 立刻切 / 撤 |
| **短促接火超时** | engaged_at 后 8s + 评估不划算 | 切下个点 |
| **dwell 超时** | 在该点停留 > 15s 仍未接火 | 切下个点（信息已采集） |
| **反包风险** | TODO P2（敌方视野有大军逼近） | 立刻撤 |
| **所有点扫完** | current_idx == len(candidates) | release directive |

### 候选点派生

LLM 给 `target_area`（单 named_spot 或坐标）作为**锚点**，bot 派生候选序列：

```python
def _derive_candidates(anchor: str, bot) -> list[Point2]:
    """
    anchor=enemy_natural → [enemy_natural, enemy_third, enemy_main_ramp, 矿区]
    anchor=enemy_main → [enemy_main, enemy_natural, enemy_main_gas]
    anchor=Point2(x,y) → 以坐标为中心搜半径 10 内的命名点
    """
    registry = bot.named_spots
    anchor_pos = registry.resolve(anchor, bot)
    # 按"距离 squad 当前位置 + 防守稀薄度启发式"排序
```

### 弱点升级 harass

```python
def _is_weak_point(self, squad, bot) -> bool:
    """squad DPS vs 当前视野内敌方 DPS。"""
    squad_dps_sum = sum(u.dps for u in bot.units.tags_in(squad.unit_tags))
    enemy_in_sight = bot.enemy_units.closer_than(15, self.current_point)
    enemy_dps_sum = sum(u.dps for u in enemy_in_sight)
    return enemy_dps_sum < squad_dps_sum * 0.7
```

发现弱点后：把 squad 的 move_type 改成 `MoveType.Harass`，停在 current_point
不再切。directive 继续 active 直到 done_when 触发或玩家点 ×。

## 四、实施分期

### Phase 0：短期 stub（**10 min**）

**目标**：让 verb=recon 至少不退化成 committed Assault。

- 改 `director.py:2236` 把 recon 映射到 `MoveType.Harass`
- prompt 注释里说明"当前 recon 行为 = sharpy Harass，真 RIF 在 P1"
- 实际效果：4 追猎走 Harass MoveType（边打边走，sharpy 自动），单点不再死磕，
  但不切目标点。比 Assault 强一点点。

**Ship 标准**：测试 "试探一下对方二矿"，追猎不会一直撞死在 cannon 上。

### Phase 1：候选点派生 + 切点状态机（**3-4h**）

**目标**：A→B→C 序列扫描真的工作起来。

- `recon_controller.py` 新建，含 `ReconSquadController` + `ReconState`
- `_derive_candidates` 实现（先简单版：anchor + named_spot 表的 enemy_* 串）
- 切点条件实现：dwell 超时 / 战损 / 接火超时三个触发
- director `execute_tactics_step` 加 recon 分支调 controller
- `_exec_l2_squad` 在 verb=recon 时初始化 ReconState 写到 squad
- 单测：候选点序列、切点条件、释放路径

**Ship 标准**：
- 派 4 追猎 recon enemy_natural → 试探 8s → 没占便宜 → 自动切去 enemy_third → ...
- 全扫完没弱点 → directive 自动释放
- 战损到 60% → 立即撤

### Phase 2：弱点升级 harass（**2h**）

**目标**：找到弱点不浪费机会。

- `_is_weak_point` 实现（DPS 比较）
- 发现弱点后 squad.move_type 改 Harass，停在 current_point
- task_monitor 自然处理（done_when=enemy_killed_in_area 满足时释放）

**Ship 标准**：试探到对方某点防御稀疏，追猎不切走，原地打农民/拆建筑直到
被反扑或杀够。

### Phase 3：反包风险检测（**1h，可选**）

**目标**：感知到对方主力来支援时撤离。

- 算"敌方视野内单位逼近 squad 的速度"
- 触发条件：敌方军队规模 > squad × 1.5 且距离 < 15
- 触发后撤回最近 townhall

### Phase 4：unit_type 智能（**可选，灵感性优化**）

LLM 默认给 `unit_type_hint=["Stalker"]`，但 RIF 实际可能：
- 凤凰 RIF：远程不接触，纯收集信息（变 scout 强化版）
- 追猎闪烁 RIF：带逃生技能，dwell 时间可拉长
- DT RIF：隐形，敌方无侦察时直接升级 raze

后续考虑 ReconState 加 `unit_class_strategy` 字段。

## 五、Schema / Prompt 影响

### Schema 不变

`TacticalObjectivePayload` 保持 `target_area: str | tuple | None` 单点。
候选点派生是 bot 端职责，LLM 不需要给数组。

### Prompt 调整（Phase 1 同期）

- 例 10 改写：明确"target_area = 锚点，bot 自动派生候选序列"
- 加边界 case：「试探对方所有矿点」 → target_area="enemy_main"（bot 自然会扫 natural/third）
- 强调 unit_count_hint=3-6 区间（再多就接近 attack）

## 六、风险 / 已知盲区

1. **sharpy MoveType 不够细**：sharpy 的 Search/Push/Patrol 几个 MoveType 行为
   细节文档少，可能需要直接 `unit.move(point)` 绕开 GroupCombatManager。
2. **视野判断**：`bot.enemy_units` 只含可见单位，"没看到 = 安全"可能误判
   （高地藏兵）。Phase 1 先不管，Phase 3 反包检测加。
3. **DPS 估算**：python-sc2 unit 没现成 .dps 属性，要自己算 weapon damage /
   cooldown。简化方案：用 supply cost 做粗代理。
4. **测试可观察性**：FakeFacade 不真模拟 sharpy GroupCombatManager，Phase 1
   单测要 mock 控制器的 add_units/execute，e2e 才能真验切点行为。
5. **done_when 跟 controller 的关系**：候选点扫完 vs LLM 给的 done_when 触发
   谁先谁后？设计：两者并行评估，**任一触发都 release**。

## 七、开放问题（等用户拍）

- [ ] Phase 0 stub 现在 ship 吗？还是等 Phase 1 一起？
- [ ] Phase 1 候选点最大数量？默认 3 或 5？
- [ ] dwell 超时和接火超时的具体秒数（建议 15s / 8s，可能调）
- [ ] 弱点判定阈值（建议 enemy DPS < squad × 0.7，可能调）
- [ ] Phase 3 反包检测必须 MVP 含吗？

## 八、不在范围

- LLM 主动判断要不要发起 RIF（玩家显式说才进 recon）
- 多 squad 协同（一队牵制一队偷家）—— 远未来
- RIF 历史信息持久化（"上次扫过 enemy_third 防得很厚，30s 内不再去"）—— 优化项
- 对方 RIF 检测（bot 自己被火力侦查时识别并反扑）—— 完全不同的话题
