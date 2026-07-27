# Drop Act 设计 —— 空投指令的语义、路径、复合行为

> 2026-05-23 brainstorming session 产出。下一步由 writing-plans 出实施 plan。

## Goal

让玩家用一句话表达"出 4 叉子棱镜空投对面二矿"，bot 端：
1. 准确解析"二矿"= 敌方第二个扩张点的矿区
2. 自动补全依赖（建筑、单位、科技）
3. 规划安全空投路径（避开已知敌方基地）
4. 实例化 ActBase 子类微操执行

设计延续 vibecraft 的「LLM → Directive → tech_tree 自动补依赖 → ActBase」管线，**不引入任意代码执行**。

## 范围

- ✅ 空投目标识别（主矿/二矿/三矿 × 矿区/产能区，4+ 矿用钟点/地图位置）
- ✅ 路径规划（递归细分，避开已确定敌方基地）
- ✅ 矿区 drop_pos 优化（圆周贴地图边缘）
- ✅ Drop directive payload + 自动 chain
- ✅ 二段空投（棱镜前线 warp → 再深入空投）
- ❌ 完全通用的 compound_action DSL（留作 future，本期不做）
- ❌ LLM exec 任意 Python 代码（永不做）

## §1. 数据层（NamedSpotRegistry 扩展）

复用 sharpy `Zone` 现成属性：
- `zone.behind_mineral_position_center` — 矿区位置（python-sc2 expansion_locations_dict 预知,不依赖 vision）
- `zone.center_location` — 基地 nexus 位置（= 产能区）
- `zone.ramp` — 该扩张点斜坡
- `zone.height` — 地形高度

### Spec 字符串格式

```
<base_ref>:<zone_kind>

base_ref:
  enemy_main      → zone_manager.enemy_expansion_zones[0]
  enemy_natural   → zones[1]
  enemy_third     → zones[2]
  clock_{0..11}   → 钟点方向最近的 expansion(地图中心为原点)
  map_center      → 距 map_center 最近的 expansion

zone_kind:
  mineral         → 矿区(behind_mineral_position_center + 边缘优化)
  production      → 产能区(zone.center_location)
                    仅对 enemy_main / enemy_natural / enemy_third 有效;
                    clock_X / map_center 没有 production 概念
                    (用户原话:基本只有主矿有产能,少数野外产能不在 design v1)
```

### 钟点位置计算

```python
import math

def expansion_at_clock(clock: int, bot) -> Point2 | None:
    """clock ∈ [0..11]. 12点=正上方, 3点=右, 6点=正下, 9点=左."""
    center = bot.game_info.map_center
    target_angle = (math.pi / 2) - (clock * math.pi / 6)  # 12点=π/2
    best, best_d = None, float("inf")
    for p in bot.expansion_locations_list:
        angle = math.atan2(p.y - center.y, p.x - center.x)
        # 角度差(规范化到[-π, π])
        diff = abs((angle - target_angle + math.pi) % (2 * math.pi) - math.pi)
        if diff < best_d:
            best_d = diff
            best = p
    return best
```

### 矿区 drop_pos 优化

棱镜的飞行终点不是矿心 M 本身，而是**矿区圆周上向地图边缘最近的点**。
这样棱镜在地图边缘 = 远离敌方主力，DT 卸下走 R 格到矿区。

```python
def optimize_drop_pos_to_edge(M: Point2, R: float, playable: Rect) -> Point2:
    """M 到 4 个边距离最小的方向上,距 M = R 的圆周点."""
    dl = M.x - playable.x
    dr = playable.x + playable.width - M.x
    dt = playable.y + playable.height - M.y
    db = M.y - playable.y
    min_dist = min(dl, dr, dt, db)
    if min_dist == dl: return Point2((M.x - R, M.y))
    if min_dist == dr: return Point2((M.x + R, M.y))
    if min_dist == dt: return Point2((M.x, M.y + R))
    return Point2((M.x, M.y - R))
```

### DropTarget 数据类

```python
@dataclass(frozen=True)
class DropTarget:
    position: Point2      # 解析后的具体坐标(矿区已 optimize_drop_pos_to_edge)
    zone_kind: str        # "mineral" | "production"
    base_index: int       # 0/1/2/3+ (enemy_main=0)
    source_spec: str      # 原 spec 字符串(给日志/PWA 显示)
```

## §2. 路径规划（递归细分）

### 算法

```python
R_MINERAL_AVOID = 15  # zone 影响半径(含矿区+建筑区)
PUSH_DIST = 5          # 转折点额外 buffer 避免再穿
MAX_DEPTH = 3          # 最多插入 3 个新点(C/D/E),共 4 段路径

def plan_drop_path(
    A: Point2,           # 棱镜起点
    B: Point2,           # drop_pos(已 optimize)
    bot,
    depth: int = 0,
) -> list[Point2]:
    """A→B 路径细分。返回 waypoint list(含 A 和 B)."""
    if depth >= MAX_DEPTH:
        return [A, B]  # 兜底:原直线

    blockers = _confirmed_enemy_zones(bot)  # 已确定基地(有 known townhall)
    M = first_blocking_zone(A, B, blockers, R=R_MINERAL_AVOID)
    if M is None:
        return [A, B]  # 安全

    # C = M.position + (P-M).normalized * (R+push), P 是 M 在 AB 上垂足
    P = project_point_onto_segment(M.position, A, B)
    direction = (P - M.position).normalized()
    C = M.position + direction * (R_MINERAL_AVOID + PUSH_DIST)

    left = plan_drop_path(A, C, bot, depth + 1)
    right = plan_drop_path(C, B, bot, depth + 1)
    return left[:-1] + right  # 去重 C


def _confirmed_enemy_zones(bot) -> list[Zone]:
    """已确定敌方基地 = sharpy enemy zones 里有 known townhall 的."""
    zones = bot.knowledge.zone_manager.enemy_expansion_zones
    enemy_th = {UnitTypeId.NEXUS, UnitTypeId.HATCHERY, UnitTypeId.LAIR,
                UnitTypeId.HIVE, UnitTypeId.COMMANDCENTER,
                UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS}
    return [z for z in zones
            if z.known_enemy_units.of_type(enemy_th).exists]
```

### 替换现有算法

应用：替换 `dt_prism_harass:_choose_path` 的 `edge_y/edge_x` 二选一算法。
新算法对任意 drop_target 通用，不需要硬编码二分。

## §3. 语义识别（LLM prompt + alias）

LLM 解析话语 → `DropActPayload.drop_target` 字符串。

| 玩家话语 | spec |
|---|---|
| 投对面主矿 / 主矿矿区 | `enemy_main:mineral` |
| 投对面主矿产能 / 主基地建筑区 | `enemy_main:production` |
| 投二矿 | `enemy_natural:mineral`（**默认矿区**） |
| 投二矿产能 / 二矿基地 | `enemy_natural:production` |
| 投三矿 | `enemy_third:mineral` |
| 投 11 点钟那矿 / 11 点四矿 | `clock_11:mineral` |
| 投地图中间那矿 | `map_center:mineral` |

### LLM prompt 加：

```
DropTarget spec 格式: <base_ref>:<zone_kind>
  base_ref:
    enemy_main / enemy_natural / enemy_third
    clock_{0..11}  (钟点方向最近的 expansion)
    map_center     (距地图中心最近的 expansion)
  zone_kind:
    mineral (默认)
    production (仅 enemy_main/enemy_natural/enemy_third 有效)

规则:
- 玩家只说"X 矿"不带后缀 = X 矿 + mineral
- 玩家说"X 矿产能/基地/建筑" = X 矿 + production
- clock_X / map_center 没有 production 概念(只能 mineral)
```

## §4. Directive Payload (B+ 复合指令)

新 directive type：`drop_act`。不走 exec，参数化的 ActBase 子类。

```python
class DropActPayload(_PayloadBase):
    """L4 空投复合指令(2026-05-23 brainstorming)."""

    type: Literal[DirectiveType.DROP_ACT] = DirectiveType.DROP_ACT
    style: Literal["simple", "warp_then_drop"] = "simple"
    cargo_unit: str                     # "Zealot" / "DarkTemplar"
    cargo_count: int                    # 4
    transport: str = "WarpPrism"        # 默认棱镜(神族),可改 Medivac(人族)
    drop_target: str                    # "enemy_natural:mineral" (最终目的地)
    warp_at: str | None = None          # 仅 style=warp_then_drop 用
                                        # 第一站 warp 位置 (如 "enemy_main:ramp_outside")
    after_unload: Literal[
        "attack_workers", "attack_production", "retreat", "siege"
    ] = "attack_workers"
    priority: int = 60
```

### Director 执行 (`_exec_drop_act`)

1. **自动补依赖**(复用 #210 tech_tree 机制)
   - 缺 Stargate/Robo/Pylon → emit prereq chain（structure_override）
2. **自动出兵**(复用 production_override 机制)
   - 缺 cargo_unit/transport → emit ProductionOverride
3. **实例化 ActBase 子类注入 sharpy plan 树**
   - `style=simple` → `GenericDropAct` (DTPrismHarass 改造，参数化)
   - `style=warp_then_drop` → `PrismWarpDropAct` (2 stage:先到 warp_at warp,再二段 drop)
4. **DropAct 内部用 §2 路径算法**

### PWA 卡片显示

玩家说 "4 叉子棱镜空投对面二矿" → PWA 上看到一组关联卡片：

```
L4 出 叉子×4              [pending → producing → done]
L4 出 棱镜×1              [pending → producing → done]
L4 造 ROBOTICSFACILITY    [auto_prereq:DROPACT]
L4 造 CYBERNETICSCORE     [auto_prereq:DROPACT]
L4 造 GATEWAY             [auto_prereq:DROPACT]
L4 空投 4 叉子 → 二矿矿区  [waiting:等单位齐 → executing → done(自动释放)]
```

每张卡按依赖顺序进度。L4 空投卡完成时自动释放(参考 #213 _release_directive_done)。

## §5. 二段空投 (style="warp_then_drop")

例：棱镜前线 warp 4 DT → DT 上棱镜 → 棱镜进对方主基地深处空投。

```python
class PrismWarpDropAct(ActBase):
    """2-stage drop: warp at frontline → load → fly deeper → unload."""

    class State(Enum):
        IDLE = "idle"
        FLY_TO_WARP_SPOT = "fly_to_warp_spot"   # 飞到 warp_at
        DEPLOY_PHASING = "deploy_phasing"        # 展开
        WARP_UNITS = "warp_units"                # warp cargo_unit × cargo_count
        WAIT_WARP_COMPLETE = "wait_warp"         # 折跃 build_progress 完成
        MORPH_TRANSPORT = "morph_transport"      # 收起,换回 transport mode
        LOAD_CARGO = "load_cargo"                # smart-cast 新 warp 出的 cargo 上船
        FLY_TO_FINAL = "fly_to_final"            # 飞到 drop_target
        UNLOAD_FINAL = "unload_final"            # 卸下
        DONE = "done"

    # 配置(从 DropActPayload)
    cargo_unit: UnitTypeId         # DARKTEMPLAR
    cargo_count: int               # 4
    warp_pos: Point2               # resolve("enemy_main:ramp_outside")
    final_drop_pos: Point2         # resolve("enemy_main:production") + optimize
    after_unload: str              # "attack_workers"|...

    async def execute(self) -> bool:
        # State machine 类似 DTPrismHarass 但多了 stage 2
        # 路径用 §2 plan_drop_path(home, warp_pos) 飞到 warp_at
        # 再用 plan_drop_path(warp_pos, final_drop_pos) 飞到 final
        ...
```

### Spec 示例

```json
{
  "type": "drop_act",
  "style": "warp_then_drop",
  "cargo_unit": "DarkTemplar",
  "cargo_count": 4,
  "transport": "WarpPrism",
  "warp_at": "enemy_main:ramp_outside",
  "drop_target": "enemy_main:production",
  "after_unload": "attack_workers"
}
```

`warp_at` 需要新 spec：`enemy_main:ramp_outside` = `zone.ramp.bottom_center.towards(zone.center_location, -5)`（高地外低地，DT warp 出来不会被高地建筑直接攻击）。

## §6. 神族空投策略统一（2026-05-23 用户补充）

用户决策：**所有神族空投默认 `style=warp_then_drop`**。
- 神族棱镜的优势就是 warpgate power field —— 不充分利用是浪费
- 单段 drop 神族只比人族 medivac 多一个 "可 warp" 功能,不发挥
- 标准战术（pro 实战）：棱镜飞到对方高地前 → phasing warp 4 兵 → 收回 transport → load 这 4 兵 → 飞到高地内深处 → drop

### Default style 矩阵

| 种族 | transport | default style | 备注 |
|---|---|---|---|
| Protoss | WarpPrism | **`warp_then_drop`** | 用 warpgate power field 优势 |
| Terran | Medivac | `simple` | 医疗船不能 warp,只能装载 |
| Zerg | Overlord (with cargo upgrade) / NydusWorm | `simple` | 没 warp 概念 |

LLM 出 directive 时如果没显式指定 style，按 race 默认。

### DTPrismHarass 重做

`dt_drop_iac` 当前用的 `DTPrismHarass` (LOAD_AT_HOME → FLY → UNLOAD → WARP_DT)
**反向**：旧版从家里装 DT 跑过去 → 新版空船飞过去 → 前线 warp → 装上船 → 深入。

替换计划：
- 删除 `DTPrismHarass`
- 重写 `dt_drop_iac.py` plan 用 `PrismWarpDropAct(cargo=DarkTemplar, count=4 或 8, warp_at=enemy_main:ramp_outside, drop_target=enemy_main:production)`
- 现有 `_DT_RAID_HOME_DIST` / `DtRaidAct` 微操逻辑移到 PrismWarpDropAct 内部（卸下 DT 后接管）

### 影响范围审计（需在实施时遍历）

- `src/vibecraft/bot/auto_combat/protoss/plans/dt_prism_harass.py` — 删除
- `src/vibecraft/bot/auto_combat/protoss/plans/dt_drop_iac.py` — 改用 PrismWarpDropAct
- `src/vibecraft/bot/auto_combat/protoss/plans/warp_dt_at_prism.py` — 可能复用或合并进 PrismWarpDropAct
- 其他神族 plan 如有 simple drop 调用都改 warp_then_drop

## 测试策略

- `test_named_spot_drop_target.py` — spec 解析（含钟点 / map_center）+ optimize_drop_pos_to_edge
- `test_drop_path_planning.py` — 递归路径算法（含 0 blocker / 1 blocker / 2 blocker / 超过 depth fallback）
- `test_drop_act_payload.py` — DropActPayload schema + LLM 解析（mock LLM 输出）
- `test_drop_act_director_chain.py` — auto-prereq + production_override + 实例化 ActBase 整链
- `test_prism_warp_drop_act.py` — state machine 状态转换
- 单测全部用 sharpy mock，不拉起 SC2

## 边界 & 已知 trade-off

- **不做 compound_action DSL**：未来 4-5 个 specialized act 都不够表达时再考虑（YAGNI）
- **不做 LLM exec 任意代码**：安全 + 调试代价不可接受
- **路径算法不考虑 detector**：detector 影响 cloaked unit 不影响 prism 飞行；warp 阶段 detector 检测分支由 DTPrismHarass `_detector_nearby_raid` 接管
- **二段空投 warp_at 必须 own warpgate 在范围**：sharpy WarpDTAtPrism 已处理；prism 在 warp_at 必须在 own pylon power field 7 格内 或 prism phasing 自带 power
- **clock_X 钟点位置不区分 zone_kind**：用户原话 4+ 矿只用 mineral；production 仅主基地（enemy_main/natural/third）

## 实施顺序建议

1. NamedSpotRegistry 加 resolve_drop_target + 钟点 + optimize_drop_pos
2. drop_path 路径算法（独立模块 src/vibecraft/bot/drop_path.py + 单测）
3. DropActPayload + DirectiveType.DROP_ACT
4. LLM prompt 加 spec 例子 + parser 验证
5. GenericDropAct (style=simple) 实例化(从 DTPrismHarass 改造，参数化 + 用新路径算法)
6. PrismWarpDropAct (style=warp_then_drop)
7. Director._exec_drop_act 接线 + auto-chain (production_override + auto_prereq)
8. PWA 卡片显示
9. 端到端验证（real SC2，先 simple 再 warp_then_drop）

下一步由 writing-plans skill 出每步详细任务。
