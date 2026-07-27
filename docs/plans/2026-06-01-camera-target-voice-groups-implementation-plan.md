# 镜头即目标 + 语音编队 + 4 修复 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. 写码 subagent 用 **sonnet**；本 session（Opus）编排 + 两段 review（spec 合规 → 代码质量）+ debug。

**Goal:** 给指令系统的 target 维度加 `camera`（"这里"）、selector 维度加 `group_id`(1-5 队)/`near=camera`，复用既有执行管线；新增 proxy_build / patrol 两个执行器；修 出-vs-出到 语义。

**Architecture:** 统一抽象——不重写执行层，只扩 schema（Task A）+ LLM prompt（Task B）+ Director 接线（C/D）+ 两个新 act（E/F）+ PWA（G）。设计真理源：`docs/plans/2026-06-01-camera-target-voice-groups-design.md`。

**Tech Stack:** pydantic v2 schema、ares-sc2/sharpy bot、python-sc2、Vue3 PWA、pytest（mock，无 SC2）。

**依赖图：** A 先行 → B/C/D/E/F 并行（仅依赖 A）→ G 依赖 D。

**通用约定（每个 subagent 必读）：**
- 测试 mock，**禁止拉起 SC2**（根 conftest 有保险）。跑测试用 `.venv/Scripts/python.exe -m pytest <path> -o addopts="" -q`（`-o addopts=""` 清掉 pyproject 的 filterwarnings=error 噪音；本机 PowerShell 管道会死锁，**用 Bash 工具 + `timeout`**）。
- LLM 改 prompt 改 `docs/llm_prompt/*.md`，**不改 .py 里的 string**；改完 `.venv/Scripts/python.exe scripts/dump_llm_prompt.py` 重生 `docs/llm_system_prompt.md`。
- 建筑用 hotkey、单位用中文（约定见 CLAUDE.md）。
- 每个 Task 独立 commit。mypy strict：`uv run mypy src/vibecraft`（改动文件 0 新增错误）。

---

## Task A: schema 扩展（基石，先行）

**Files:**
- Modify: `src/vibecraft/directives/scope.py`（Selector 加 group_id；TargetKind 加 CAMERA）
- Modify: `src/vibecraft/directives/models.py`（DirectiveType 加 group_assign/group_clear；新 payload GroupAssignPayload/GroupClearPayload；UnitClaimPayload 巡逻 waypoints）
- Modify: `src/vibecraft/llm/prompt.py`（ParseContext 加 camera_point）
- Test: `tests/unit/test_camera_group_schema.py`（新建）

**Step 1: 写失败测试** `tests/unit/test_camera_group_schema.py`：
```python
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.models import (
    DirectiveType, GroupAssignPayload, GroupClearPayload, Directive,
)
from vibecraft.llm.prompt import ParseContext
from vibecraft.directives.types import StageKind


def test_target_kind_camera_exists():
    t = TargetSpec(kind=TargetKind.CAMERA)
    assert t.kind.value == "camera"

def test_selector_group_id():
    s = Selector(group_id=1)
    assert s.group_id == 1
    # 越界拒绝（1-5）
    import pytest
    with pytest.raises(Exception):
        Selector(group_id=6)

def test_group_assign_payload_roundtrip():
    p = GroupAssignPayload(group_id=2, selector=Selector(unit_type="WarpPrism"))
    d = Directive(payload=p, issued_at=1.0)
    assert d.type == DirectiveType.GROUP_ASSIGN
    assert d.payload.group_id == 2

def test_group_clear_payload():
    p = GroupClearPayload(group_id=3)
    d = Directive(payload=p, issued_at=1.0)
    assert d.type == DirectiveType.GROUP_CLEAR

def test_parse_context_camera_point():
    ctx = ParseContext(game_time=1.0, current_stage=StageKind.MIDGAME, camera_point=(50.0, 60.0))
    assert ctx.camera_point == (50.0, 60.0)
    # 默认 None
    ctx2 = ParseContext(game_time=1.0, current_stage=StageKind.MIDGAME)
    assert ctx2.camera_point is None
```

**Step 2: 跑测试确认失败** `pytest tests/unit/test_camera_group_schema.py -o addopts="" -q` → ImportError/AttributeError。

**Step 3: 实现**：
- `scope.py`：`TargetKind` 加 `CAMERA = "camera"`。`Selector` 加
  ```python
  group_id: int | None = Field(default=None, ge=1, le=5,
      description="语音编队 1-5;指挥某队时填,Director 解析为该队 tags")
  ```
- `models.py`：`DirectiveType` 加 `GROUP_ASSIGN = "group_assign"` / `GROUP_CLEAR = "group_clear"`。新增（mirror 既有 `_PayloadBase` + `type` Literal + discriminator 注册方式，照 `ViewFollowPayload` 写）：
  ```python
  class GroupAssignPayload(_PayloadBase):
      type: Literal[DirectiveType.GROUP_ASSIGN] = DirectiveType.GROUP_ASSIGN
      group_id: int = Field(ge=1, le=5)
      selector: Selector
  class GroupClearPayload(_PayloadBase):
      type: Literal[DirectiveType.GROUP_CLEAR] = DirectiveType.GROUP_CLEAR
      group_id: int = Field(ge=1, le=5)
  ```
  **注意**：照搬 `ViewFollowPayload` 在 `Directive.payload` 的 Union/discriminator 注册处把两个新 payload 加进去（grep `ViewFollowPayload` 找所有注册点，全部补上）。
  - 巡逻 waypoints：在 `UnitClaimPayload.task.primary_action` 路径外加最小支持——给 `Action`(task.py) 或 TargetSpec 增加第二点表达。**首选**：`TargetSpec` 加 `waypoints: list[TargetSpec] | None = None`（patrol 时填 [A,B]，每个仍是 TargetSpec 可为 named_spot/point/camera）。配套测试：
    ```python
    def test_target_waypoints():
        t = TargetSpec(kind=TargetKind.NAMED_SPOT, waypoints=[
            TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_clock_11"),
            TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
        ])
        assert len(t.waypoints) == 2
    ```
- `prompt.py`：`ParseContext` 加 `camera_point: tuple[float, float] | None = Field(default=None, description="说话那刻镜头中心,LLM 把'这里'解析为 target.kind=camera")`。

**Step 4: 跑测试确认通过** + `pytest tests/unit/ -o addopts="" -q -k "directive or schema or scope"` 确认没破坏既有 schema 测试。

**Step 5: Commit** `feat(schema): camera target + selector.group_id + group payloads + patrol waypoints`

---

## Task B: LLM prompt 五块（依赖 A）

**Files:**
- Modify: `docs/llm_prompt/rules.md`、`docs/llm_prompt/few_shot.md`
- Run: `scripts/dump_llm_prompt.py`（重生 `docs/llm_system_prompt.md`）
- Modify: `scripts/voice_spot_check.py`（加 case）

**Step 1-2（验证以 spot_check 为主，无传统单测）**：先在 `voice_spot_check.py` 的 CASES 加 5 类断言 case（跑真 LLM 才验，CI 不跑）：
```python
SpotCase(text="派一个农民到这里待命", expected_types=["unit_claim"],
         check_fields={"selector": {"count": 1}}),  # target.kind 应为 camera(人工 review)
SpotCase(text="在这里修个水晶", expected_types=["unit_claim"]),  # verb=build target=camera
SpotCase(text="把运输机编成1队", expected_types=["group_assign"]),
SpotCase(text="清除1队", expected_types=["group_clear"]),
SpotCase(text="1队到这里待命", expected_types=["unit_claim"]),  # selector.group_id=1
SpotCase(text="在二矿修8个bg", expected_types=["structure_override"]),  # delta=8 非 target
SpotCase(text="补到14个bg", expected_types=["structure_override"]),  # target=14
SpotCase(text="农民在对方11点分矿和三矿之间巡逻", expected_types=["unit_claim"]),  # waypoints=[A,B]
```

**Step 3: 改 prompt**（`rules.md` 加 5 块，`few_shot.md` 加对应例）：
1. **镜头"这里"**：dynamic_context 会带 `camera_point=(x,y)`。玩家说"这里/这边/此处" → 任意 target 用 `kind="camera"`（不要自己填坐标，Director 注入）。
2. **语音编队**：
   - "把〈X〉编成N队" → `group_assign{group_id:N, selector:<按X>}`（镜头内→near=camera；全图同类→unit_type；N个→count；野外→near_point/claimed）。
   - "释放/取消/清除 N队" → `group_clear{group_id:N}`（三者同义）。
   - "N队〈做什么〉" → 正常 unit_claim/tactical，但 `selector.group_id=N`。
3. **出 vs 出到**（structure_override）：修/出/造/补/刷/加 N → `delta=N`；修到/出到/补到/补齐/到 N → `target_count=N`。**默认无"到"字 = delta**。
4. **代理建造**：派农民去〈点〉修〈建筑〉 → `unit_claim{selector(Probe,count=1), task.primary_action.verb=build, target=<点/camera>}`（Director 走 proxy_build_act，造完留原地）。
5. **巡逻两点**："在 A 和 B 之间巡逻" → `unit_claim{verb=patrol, target.waypoints=[A,B], persistent=true}`。

**Step 4: 重 dump** `.venv/Scripts/python.exe scripts/dump_llm_prompt.py`；（可选）`DEEPSEEK_API_KEY` 在则跑 `scripts/voice_spot_check.py` 人工 review camera/group/delta。

**Step 5: Commit** `feat(prompt): camera这里 + 语音编队 + 出vs出到 + 代理建造 + 巡逻两点`

---

## Task C: camera 注入（依赖 A）

**Files:**
- Modify: `src/vibecraft/bot/facade.py`（真 facade `get_camera_center` + FakeFacade stub）
- Modify: `src/vibecraft/bot/auto_combat/common_bot.py`（`_SharpyFacadeBase.get_camera_center` 读 `PlayerRaw.camera`；`_tick_view_channel` 收 command 时快照传入 `run_command_with_echo_fn`）
- Modify: `src/vibecraft/llm/parser.py` 或 prompt 构造处（把 `ctx.camera_point` 注入 dynamic_context）
- Modify: `src/vibecraft/bot/director.py`（submit 时把 `kind=camera` 的 target.point 用快照填实）
- Test: `tests/unit/test_camera_target.py`

**Step 1: 失败测试**：
```python
def test_camera_target_resolved_from_snapshot(session):
    facade = FakeFacade()
    director = _make_director(..., facade)
    # 模拟一条 unit_claim,target.kind=camera,Director 注入 camera_point=(40,50)
    d = _make_camera_claim_directive(camera_point=(40.0, 50.0))
    director._submit_directives([d], now=1.0)
    # 断言 facade 收到的 move/build 目标点 == (40,50)
    acts = [a for a in facade.unit_actions]
    assert any(a["target"]["point"] == [40.0, 50.0] or a["target"]["kind"]=="point" for a in acts)

def test_fake_facade_get_camera_center():
    f = FakeFacade()
    f.camera_center_stub = (12.0, 34.0)
    assert f.get_camera_center() == (12.0, 34.0)
```

**Step 3: 实现**：
- `FakeFacade`：加 `self.camera_center_stub = None` + `def get_camera_center(self): return self.camera_center_stub`。
- 真 facade（common_bot `_SharpyFacadeBase`）：
  ```python
  def get_camera_center(self):
      try:
          c = self.bot.state.observation.observation.raw_data.player.camera
          return (float(c.x), float(c.y))
      except Exception:
          return None
  ```
- `_tick_view_channel` 收到 `command` 时：`cam = self.facade.get_camera_center()`，传给 `run_command_with_echo_fn(... camera_point=cam)`，最终进 `ParseContext.camera_point`。
- Director：`kind=camera` 的 target 在 submit/执行时若 `point` 为空则用注入的 camera_point 填 `point`（kind 可保留 camera 供 UI 显示"这里"，或转 point）。**实现选择：parse 完成后 Director 把 camera_point 写进所有 `kind=camera` target 的 point**。

**Step 4-5:** 跑测试通过 + `pytest tests/unit/test_camera_target.py -o addopts="" -q`；Commit `feat(camera): get_camera_center + 收指令快照注入 + camera target 解析`。

---

## Task D: 语音编队 Director（依赖 A）

**Files:**
- Modify: `src/vibecraft/bot/director.py`（`_voice_groups` 状态；GROUP_ASSIGN/GROUP_CLEAR 处理；selector.group_id→tags 解析；snapshot 透传 `voice_groups`）
- Modify: `src/vibecraft/server/ws.py`（如需，snapshot 已是 Director 产出则免）
- Test: `tests/unit/test_voice_groups.py`

**Step 1: 失败测试**：
```python
def test_group_assign_stores_tags(session):
    facade = FakeFacade(); facade.selector_stub["WarpPrism"] = [7001]
    director = _make_director(..., facade)
    director._submit_directives([_group_assign(1, Selector(unit_type="WarpPrism"))], now=1.0)
    assert director._voice_groups[1] == {7001}

def test_group_clear_releases(session):
    ... director._voice_groups[1] = {7001}; facade.unit_roles[7001]=LLM_CONTROLLED
    director._submit_directives([_group_clear(1)], now=2.0)
    assert director._voice_groups.get(1) in (None, set())
    # release_unit_role 被调

def test_command_by_group_id_resolves_tags(session):
    director._voice_groups[2] = {8001, 8002}
    # unit_claim selector.group_id=2 → 解析到 {8001,8002}
    tags = director._resolve_selector_with_count(Selector(group_id=2))
    assert set(tags) == {8001, 8002}

def test_dead_units_filtered(session):
    director._voice_groups[1] = {9001, 9002}
    facade.alive = {9001}  # 9002 死
    tags = director._resolve_selector_with_count(Selector(group_id=1))
    assert set(tags) == {9001}

def test_snapshot_includes_voice_groups(session):
    director._voice_groups[1] = {7001}
    snap = director.build_snapshot(now=1.0)
    assert "voice_groups" in snap
```

**Step 3: 实现要点**：
- `__init__`：`self._voice_groups: dict[int, set[int]] = {}`。
- `_submit_directives` 路由：GROUP_ASSIGN → 解析 selector → `_voice_groups[gid]=set(tags)`；GROUP_CLEAR → release tags + `_voice_groups.pop(gid)`。
- `_resolve_selector_with_count`（director.py:1766）开头加：`if sel.group_id is not None: tags=list(self._voice_groups.get(sel.group_id,set())); 过滤存活; 再按 count 截断`。存活过滤用 `facade.resolve_selector(tags=...)` 或新 `facade.alive`。
- `build_snapshot`：加 `snapshot["voice_groups"]=self._build_voice_groups_view()`，按 tag 查 unit_type 聚合成 `{group_id, units:{TYPE:count}}`（需 `_bot` 查 type，None-safe）。

**Step 4-5:** 测试通过；Commit `feat(groups): 语音编队1-5 assign/clear/group_id解析/snapshot透传`。

---

## Task E: 代理建造执行器（依赖 A）

**Files:**
- Create: `src/vibecraft/bot/auto_combat/protoss/plans/proxy_build_act.py`（参考既有 act：grep `class.*Act` 找最简单的派单位 act 模式，如 squad/drop act）
- Modify: `src/vibecraft/bot/director.py`（`unit_claim verb=build` → 注册 proxy build 状态，每 tick 驱动）
- Test: `tests/unit/test_proxy_build.py`

**行为契约（造完留原地待命）：** claim 农民 → move 到 target_point → 距离 < ε 时下 `build(structure_type, near=target_point)` → 建造开始后农民转 standby（停在原地，不放回采矿）→ 卡片 done。

**Step 1: 失败测试**（mock facade，验状态机派的动作序列）：
```python
def test_proxy_build_moves_then_builds(session):
    # 农民远离目标 → 应只 move
    # 农民到达目标(facade 报 pos≈target) → 应下 build
    # 验 facade.unit_actions 出现 verb=build / move_to 序列 + 造完后不 release 回采矿
```
（具体断言按 act 接口设计；subagent 先读一个现有 act 的测试风格对齐。）

**Step 3: 实现**：proxy_build_act 持有 (probe_tag, target_point, structure_type, phase∈{moving,building,done})；Director 每 tick（execute_overrides_step 链）调 act.tick。到位用 `bot.units.by_tag(probe).distance_to(point) < 3`。build 用 `bot.build(UnitTypeId, near=Point2)` 或现有 builder。造完 phase=done，**不** release（留原地）。

**Step 4-5:** 测试通过；`override_acceptance` 留待真机；Commit `feat(proxy-build): 派农民去远点建造,造完留原地待命`。

---

## Task F: 巡逻执行器（依赖 A）

**Files:**
- Create: `src/vibecraft/bot/auto_combat/.../patrol_act.py`
- Modify: `src/vibecraft/bot/director.py`（`unit_claim verb=patrol` + `target.waypoints=[A,B]` → 注册 patrol，每 tick 驱动）
- Test: `tests/unit/test_patrol.py`

**行为契约（无限往返）：** 解析 waypoints=[A,B] 为两个点；每 tick：若单位到达当前目标点（dist<ε）→ 切换到另一点并下 move；否则保持。直到玩家 × 卡片 / 重派。

**Step 1: 失败测试**：
```python
def test_patrol_toggles_between_two_points(session):
    # 单位在 A → 到达 A → 应朝 B move
    # 下一拍单位在 B → 应朝 A move
    # 验 facade.unit_actions 的 move 目标在 A/B 间切换
def test_patrol_needs_two_waypoints(session):
    # 只有一个点 → 不崩,标失败/降级
```

**Step 3: 实现**：patrol_act 持有 (tag, [A,B], current_idx)；tick：`u=by_tag; if u.distance_to(points[idx])<3: idx^=1; facade.execute_unit_action(tag, "move_to", points[idx])`。Director 在 `_assign_standing_order_units` 检测 verb=patrol → 注册 patrol_act 而非一次性 move。

**Step 4-5:** 测试通过；Commit `feat(patrol): 两点无限往返执行器`。

---

## Task G: PWA 编队条（依赖 D）

**Files:**
- Create: `web/src/components/VoiceGroupBar.vue`
- Modify: `web/src/`（主界面挂载该组件，读 snapshot.voice_groups）
- Build: `cd web && npm run build`（产出写入 `src/vibecraft/server/static/assets/`）
- Test: 组件渲染（若有前端测试框架则加；否则人工 + build 通过）

**Step 1-3:** VoiceGroupBar 渲染 1-5 五格：每格显示 `N队` + 兵种构成（`运输机×1` / `叉子×8 不朽×2`），空队灰显。数据来自 `snapshot.voice_groups`。兵种中文名复用既有 `_UNIT_ZH` 映射（前端若无则加最小映射）。

**Step 4:** `cd web && npm run build` 成功，`grep -l VoiceGroupBar src/vibecraft/server/static/assets/*.js` 有结果。

**Step 5: Commit** `feat(pwa): 语音编队条 UI 显示1-5队构成`。

---

## 收尾（Opus 本 session）

- 全部 Task 合并后跑全 unit 套件 `pytest tests/unit -o addopts="" -q`（基线 2119+，新增测试全绿）+ `uv run mypy src/vibecraft` 改动文件 0 新增。
- 真机验证清单（喊用户）：派农民到"这里"修水晶（留原地）/ "把运输机编成1队" UI 显示 / "1队到这里待命" / "出8个bg"=新增8 / "11点和三矿之间巡逻"往返。
- 不 commit 到此为止；最终 PR 描述汇总 7 包。
