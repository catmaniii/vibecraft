# ADR 0014: 出兵集结点（RALLY_POINT directive）

2026-06-07 · Accepted

## 背景

玩家要"设一个出兵集结点：之后所有新出的兵都自动去那集结，直到我改/清"。

调研 sharpy 底层：**已有完整集结点机制**——`GatherPointSolver` manager（`IGatherPointSolver`
接口）维护全局 `gather_point`（默认家门口 ramp，随扩张自动前移），`PlanZoneGather` 每帧把
idle 兵 combat-move 到该点。即"新兵自动 rally"本就存在，缺的只是**玩家显式设这个点**的入口。

## 决策

新增独立 directive 类型 `RALLY_POINT`（不复用现有类型）。

**为什么新类型而非组合现有**（CLAUDE.md「能组合就不新增」的例外判断）：
- 它是**全新执行语义**——持续覆盖 sharpy 全局 `gather_point`，影响"未来新出的兵"，
  现有 directive 都覆盖不了：
  - `unit_claim`(集中/待命) 拿走**现有**单位独占控制（占控制权）；rally_point **不碰任何现有
    单位、不占控制权**，只改"新兵默认去哪"。
  - `move` 移动现有单位一次性；`tactical_objective` 是战斗意图；都不是"设一个持久的全局点"。

**形态**（玩家 2026-06-07 确认）：全局单点 + 语音"集结点设这里"(target=camera)。
- `RallyPointPayload { target: TargetSpec }`，无 selector（不针对具体兵）。
- persistent 全局态：Director `_rally_point` + `_rally_point_id`，单条生效（新点覆盖旧卡）。
- 玩家 × → 清，恢复 bot 默认前移逻辑。

## 实现

- **schema**：`DirectiveType.RALLY_POINT` + `RallyPointPayload` + 加进 Payload 判别联合。
- **facade**：`set_rally_point(point|None)` 三处同步（Sc2Facade Protocol + FakeFacade +
  `_SharpyFacadeBase`，见 CLAUDE.md facade 一致性约定 + audit）。真实版调
  `IGatherPointSolver.set_gather_point`。
- **Director**：
  - submit 路由进 `_in_flight`（同 view_follow / production_block，persistent 全局态）。
  - `_apply_to_facade` RALLY_POINT 分支：解析 target → `_rally_point`；旧 rally 卡标 done。
  - **on_tick 每帧**续调 `facade.set_rally_point`——sharpy `set_gather_point` 是**一次性
    flag**（只生效 1 tick，不每帧重设会被 `_find_gather_point` 重算回默认；forward_rally 同款坑）。
  - revoke / 卡过期 → 清 `_rally_point` + `set_rally_point(None)`，恢复默认。
  - `_inject_camera_point` 覆盖 RallyPointPayload.target。
  - `_build_command_cards` 出 "出兵集结点 (x,y)" L2 卡片（前端通用渲染 + ×，无需改前端）。
- **LLM prompt**：rules.md + few_shot 例 47e，**重点和"集中"(unit_claim) 区分**——"集结点/
  出兵都去/新兵去哪"→ rally_point；"〈兵种〉到这集中"→ unit_claim。真 LLM 验证 6/6。

## 代价 / 备选

- 代价：又一个 directive 类型 + 每帧一次 set_gather_point 调用（开销可忽略）。
- 备选（未采）：复用 `engagement_constraint.rally_point` 旧字段——那是废弃的"撤退集结点"
  语义、且和战斗 stance 耦合，不清晰。独立类型更直接。
- 未做：per-建筑 rally（每个 BG 单独设）—— 玩家明确要全局单点，YAGNI。
