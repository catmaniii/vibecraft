# Player Override E2E 测试 framework + 8 case 实施 Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans 实施。

**Goal**: 加 e2e 测试框架,验证玩家在游戏中按"全军撤退/进攻/防守"按钮真的让单位执行,不只是 UI 显示。

**Architecture**: 复用 build_acceptance 的 SC2 spawn / GameProcess / telemetry,扩 spec yaml 加 `player_actions` 时间线 + 新 check type `army_after_player_action`。子进程通过 GameConfig.player_actions(picklable) 拿到玩家时间线,Director 每 tick 检查到点就 submit_directive(模拟玩家按按钮)。

**Tech Stack**: 已有 build_acceptance framework / pydantic spec / SC2 GameProcess。

---

## 背景

本次 session(2026-05-26)修了 bug 12 在虫族 + 17 个人族 plan 失效问题(commit ab550fc 把它们都改成 VibeCraftZoneAttack)。但**只测了 build_acceptance(bot 默认行为不退步),没真的测"玩家按 retreat 后单位真撤"**。

需要新一类 e2e:**player override 行为验证**。

8 个 case 矩阵:

| verb | 验证 | strategy(种族) | 备注 |
|---|---|---|---|
| retreat | 出门 attack 中 → 30s 后单位距家 < N | 4bg(P) | bug 12 主要 path |
| retreat | 同上 | macro_hatch(Z) | 验虫族新 fix |
| retreat | 同上 | bio_stim(T) | 验人族 |
| attack all_in | 兵少时强制 attack(跳 power check) | 1g_robo_immortal(P) | 验 mode=all_in |
| attack all_in | 同上 | roach_hydra(Z) | |
| attack probe | 兵足时 attack 但劣势撤 | two_base_tanks(T) | 验 mode=probe |
| defend | 出门后 stance 守家 | phoenix_2base(P) | 验 stance_override |
| defend | 同上 | roach_ravager(Z) | |

---

## Task 1: Spec schema 扩展(PlayerAction model)

**Files**:
- 修改: `src/vibecraft/build_acceptance/spec.py`
- 测试: `tests/unit/test_build_acceptance_spec.py` (如不存在则创建)

**Step 1: 写 failing test**
```python
def test_player_action_parse() -> None:
    from vibecraft.build_acceptance.spec import load_spec
    spec = load_spec_dict({
        "base_strategy": "4bg",
        "race": "protoss",
        "opponent": "veryeasy",
        "player_actions": [
            {"at": "5:00", "verb": "retreat", "mode": None},
        ],
        "checks": [
            {"id": "retreated", "type": "army_after_player_action",
             "action_idx": 0, "after_s": 30, "near": "home",
             "within": 25.0, "op": "<="},
        ],
    })
    assert len(spec.player_actions) == 1
    assert spec.player_actions[0].at_s == 300.0
    assert spec.player_actions[0].verb == "retreat"
```

**Step 2: 实现 PlayerAction + 扩 Check**
```python
# spec.py
class PlayerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: str  # M:SS
    verb: Literal["attack", "defend", "retreat", "vision"]
    mode: Literal["all_in", "probe"] | None = None
    target_area: str | None = None  # named_spot, None=default

    @property
    def at_s(self) -> float:
        return parse_mmss(self.at)


class Check(BaseModel):
    # 已有字段 +
    action_idx: int | None = None  # army_after_player_action 用
    after_s: float | None = None
    op: Literal["<", "<=", ">", ">=", "==", "!="] = "<="


class Spec(BaseModel):  # 假设已存在,加字段
    player_actions: list[PlayerAction] = []


# Check.type 新增 "army_after_player_action"
# Check 验证:type == "army_after_player_action" 时 action_idx/after_s/near/within/op 必填
```

**Step 3: 跑 test 验过**
```
D:/code/claudecode/vibecraft/.venv/Scripts/python.exe -m pytest tests/unit/test_build_acceptance_spec.py -v
```

**Step 4: Commit**
```
test+feat(spec): PlayerAction + army_after_player_action check
```

---

## Task 2: GameConfig 加 player_actions 字段(picklable)

**Files**:
- 修改: `src/vibecraft/server/game_process.py` (line 52 `GameConfig`)

**Step 1: 加字段**
```python
@dataclass
class GameConfig:
    # 已有字段...
    # 2026-05-26 player_override e2e:子进程入口 wire 进 director._scheduled_actions
    # list of {"at_s": float, "verb": str, "mode": str|None, "target_area": str|None}
    player_actions: list[dict[str, Any]] = field(default_factory=list)
```

**Step 2: 子进程入口 wire 进 director**

找到 `def _child_entry(config: GameConfig, ...)` 或类似(grep `def _child_main` / `start_bot`)。
在 bot.director 创建之后,启动前:
```python
director._scheduled_player_actions = list(config.player_actions)
```

**Step 3: 验证**
跑现有 build_acceptance 1 局确保字段不破现有逻辑:
```
.venv/Scripts/python.exe scripts/build_acceptance.py 4bg --runs 1 --opponent veryeasy
```
期望:10/10 PASS(跟当前 baseline 一致)。

**Step 4: Commit**
```
feat(game_process): GameConfig.player_actions 字段(picklable)
```

---

## Task 3: Director 加 scheduled action 触发机制

**Files**:
- 修改: `src/vibecraft/bot/director.py`
- 测试: `tests/unit/test_director.py`

**Step 1: 加 _scheduled_player_actions + on_tick 检测**
```python
# director.py __init__
self._scheduled_player_actions: list[dict[str, Any]] = []
self._fired_player_actions: set[int] = set()  # 防止重复触发
```

**Step 2: on_tick 加触发**
```python
# Director.on_tick(now, ...) 末尾(_push_snapshot 之前)
for idx, action in enumerate(self._scheduled_player_actions):
    if idx in self._fired_player_actions:
        continue
    if now >= action["at_s"]:
        self._fire_scheduled_action(idx, action, now)
```

**Step 3: 实现 _fire_scheduled_action**
```python
def _fire_scheduled_action(self, idx: int, action: dict, now: float) -> None:
    """模拟玩家 UI 按按钮,等价 _submit_tactical_action(common_bot)。"""
    from vibecraft.directives.models import Directive, TacticalObjectivePayload
    from vibecraft.directives.types import IssuedBy
    verb = action["verb"]
    mode = action.get("mode")
    payload = TacticalObjectivePayload(
        verb=verb,
        target_area=action.get("target_area"),
        persistent=True,
        attack_mode=mode if mode in ("all_in", "probe") else None,
    )
    source = f"e2e scheduled: {verb}"
    if mode:
        source = f"{source} mode={mode}"
    directive = Directive(
        payload=payload, issued_at=now, issued_by=IssuedBy.VOICE, source_text=source,
    )
    # mode 在 submit 前 set facade(对齐 _submit_tactical_action 行为)
    if mode:
        set_mode = getattr(self.facade, "set_attack_mode_override", None)
        if set_mode is not None:
            set_mode(mode)
    self.submit_directive(directive, now)
    self._fired_player_actions.add(idx)
    # 记录 fire 时刻到 telemetry 让 verifier 能找
    logger.info(
        "e2e_player_action_fired idx=%d verb=%s mode=%s at=%.1f",
        idx, verb, mode, now,
    )
```

**Step 4: 写 unit test 验**
```python
def test_scheduled_player_action_fires_at_game_time(session):
    facade = FakeFacade()
    director = _make_director(...)
    director._scheduled_player_actions = [
        {"at_s": 100.0, "verb": "retreat", "mode": None, "target_area": None}
    ]
    # 99s 还没到
    director.on_tick(now=99.0)
    assert not facade.combat_intent_overrides  # 还没设
    # 100s 触发
    director.on_tick(now=100.0)
    # board commit_delay=0 → 立即生效
    assert "retreat" in facade.combat_intent_overrides
```

**Step 5: 跑 unit 全量**
```
.venv/Scripts/python.exe -m pytest tests/unit/ -q
```
期望 1532+ 全 PASS。

**Step 6: Commit**
```
feat(director): scheduled player action 触发机制(e2e 用)
```

---

## Task 4: Verifier 实现 army_after_player_action

**Files**:
- 修改: `src/vibecraft/build_acceptance/verifier.py`

**Step 1: 找 telemetry.jsonl 里的 e2e_player_action_fired 时刻**

不,更简单 —— 用 `action.at_s` 直接定位(spec 里有,不依赖 log)。

**Step 2: 实现 check**
```python
# verifier.py 加新 check
def _check_army_after_player_action(check: Check, spec: Spec, telemetry: list[dict]) -> bool:
    if check.action_idx is None or check.action_idx >= len(spec.player_actions):
        return False
    action = spec.player_actions[check.action_idx]
    target_time = action.at_s + (check.after_s or 0)
    # 找 telemetry 里最接近 target_time 的快照
    closest = min(telemetry, key=lambda r: abs(r.get("ts", 0) - target_time))
    if abs(closest.get("ts", 0) - target_time) > 5.0:
        return False  # telemetry 缺数据
    army_center = closest.get("army_center")
    if army_center is None:
        return False
    # 算 distance to near anchor (home / enemy_main)
    anchor_pos = _resolve_anchor(check.near, telemetry[0])  # home = start_location
    dist = ((army_center[0] - anchor_pos[0])**2 + (army_center[1] - anchor_pos[1])**2)**0.5
    return _compare(dist, check.op, check.within)
```

**Step 3: Anchor 解析 helper**
- home: bot start_location(从首个 telemetry 帧 / GameConfig 拿)
- enemy_main: enemy_start_locations[0]
- natural / enemy_natural: 跟 NamedSpotRegistry 类似(可能 telemetry 没记,先用 hardcode 或读 spec.race + 地图)

**Step 4: 单测**
```python
def test_army_after_player_action_pass_when_close_to_home(tmp_path):
    spec = load_spec_dict({...player_actions=[at=5:00 retreat]..., checks=[...near=home within=25 op="<="]})
    telemetry = [
        {"ts": 0, "army_center": [50, 50], "_home": [50, 50], "_enemy_main": [200, 200]},
        {"ts": 330, "army_center": [55, 52], "_home": [50, 50], "_enemy_main": [200, 200]},  # 5:30 距家 ~5
    ]
    report = verify(spec, telemetry)
    assert any(c.passed for c in report.checks if c.id == "retreated")
```

**Step 5: Commit**
```
feat(verifier): army_after_player_action check 实现
```

---

## Task 5: Runner script `override_acceptance.py`

**Files**:
- 创建: `scripts/override_acceptance.py`(copy `scripts/build_acceptance.py` 改 spec 路径 + GameConfig.player_actions wire)

**Step 1: copy + 改 spec 路径**
```python
# 大部分 copy build_acceptance.py
# 关键差异:
SPEC_DIR = _ROOT / "tests" / "override_acceptance"

# load_spec → 新 spec model(含 player_actions)
spec = load_spec(SPEC_DIR / f"{strategy_id}.yaml")

# GameConfig 创建时传 player_actions
config = GameConfig(
    ...,
    player_actions=[
        {"at_s": a.at_s, "verb": a.verb, "mode": a.mode, "target_area": a.target_area}
        for a in spec.player_actions
    ],
)
```

**Step 2: 跑帮助命令验**
```
.venv/Scripts/python.exe scripts/override_acceptance.py --help
```

**Step 3: Commit**
```
feat(scripts): override_acceptance.py runner(复用 build_acceptance)
```

---

## Task 6: 创建 8 个 yaml case

**Files (创建)**:
- `tests/override_acceptance/4bg__retreat.yaml`
- `tests/override_acceptance/macro_hatch__retreat.yaml`
- `tests/override_acceptance/bio_stim__retreat.yaml`
- `tests/override_acceptance/1g_robo_immortal__attack_all_in.yaml`
- `tests/override_acceptance/roach_hydra__attack_all_in.yaml`
- `tests/override_acceptance/two_base_tanks__attack_probe.yaml`
- `tests/override_acceptance/phoenix_2base__defend.yaml`
- `tests/override_acceptance/roach_ravager__defend.yaml`

**Yaml schema 示例**(retreat):
```yaml
# tests/override_acceptance/4bg__retreat.yaml
base_strategy: 4bg
race: protoss
opponent: veryeasy

# 玩家 timeline
player_actions:
  - at: "5:00"           # 4bg attack 通常 3-4 分钟出门,5:00 单位已在前线
    verb: retreat
    mode: null

checks:
  # 撤退指令后 30s,主力距家 <= 25(撤回成功)
  - id: army_back_home_after_retreat
    type: army_after_player_action
    action_idx: 0
    after_s: 30
    near: home
    within: 25.0
    op: "<="
  # 同时验:主力离 enemy_main 远了(确实撤了不在敌方)
  - id: army_left_enemy_after_retreat
    type: army_after_player_action
    action_idx: 0
    after_s: 30
    near: enemy_main
    within: 30.0
    op: ">"
```

**attack all_in 例**:
```yaml
# 1g_robo_immortal__attack_all_in.yaml
base_strategy: 1g_robo_immortal
race: protoss
opponent: veryeasy

player_actions:
  - at: "4:00"          # 不朽刚出 1-2 个时强制冲(power 不够)
    verb: attack
    mode: all_in
    target_area: enemy_main

checks:
  - id: army_at_enemy_after_all_in
    type: army_after_player_action
    action_idx: 0
    after_s: 60
    near: enemy_main
    within: 30.0
    op: "<="
```

**defend 例**:
```yaml
# phoenix_2base__defend.yaml
base_strategy: phoenix_2base
race: protoss
opponent: veryeasy

player_actions:
  - at: "6:00"          # 凤凰出门后切防守
    verb: defend
    mode: null

checks:
  - id: army_back_home_after_defend
    type: army_after_player_action
    action_idx: 0
    after_s: 30
    near: home
    within: 25.0
    op: "<="
```

**Step 1: 写 8 yaml**
**Step 2: 一个一个跑验时机参数**
```
.venv/Scripts/python.exe scripts/override_acceptance.py 4bg__retreat --opponent veryeasy
```
看 telemetry 里 5:00 时 army_center 在哪、5:30 时在哪。如果 retreat 单位还没到家(距离 > 25),调 `after_s` 加长或 `within` 放宽。

**Step 3: 全 8 case 跑通**
```
.venv/Scripts/python.exe scripts/override_acceptance.py \
  4bg__retreat macro_hatch__retreat bio_stim__retreat \
  1g_robo_immortal__attack_all_in roach_hydra__attack_all_in \
  two_base_tanks__attack_probe \
  phoenix_2base__defend roach_ravager__defend \
  --opponent veryeasy --parallel 4
```

期望:全 8 PASS。可能有 1-2 个 timing 边缘 fail,调 yaml 参数(at / after_s / within)。

**Step 4: Commit**
```
test(override_acceptance): 8 case yaml(三族 retreat/attack/defend 全覆盖)
```

---

## Task 7: CI 集成 + 文档

**Files**:
- 修改: `CLAUDE.md`(加 `override_acceptance` 命令到验证段)
- 创建: `docs/override-acceptance-runbook.md` (短文,说明 spec 格式 + 调参法则)

**Step 1: CLAUDE.md 加**
```bash
.venv/Scripts/python.exe scripts/override_acceptance.py <strategy>__<verb> --opponent veryeasy
# 玩家覆盖 e2e:验证按"全军撤退/进攻/防守"按钮单位真执行
```

**Step 2: 写 runbook**
- 8 case 列表
- 何时跑(玩家 override 路径改动后)
- 何时不必跑(单测能 catch 的就不要)
- 时机调参:at 是 game-time M:SS,after_s 是 action 后等多久查

**Step 3: Commit**
```
docs(override_acceptance): runbook + CLAUDE.md 命令
```

---

## 总工作量估算

| Task | 工作量 |
|---|---|
| 1 spec schema | 30 min |
| 2 GameConfig | 15 min |
| 3 director scheduled action | 45 min |
| 4 verifier check | 45 min |
| 5 runner script | 30 min |
| 6 8 yaml + 跑 | 90-120 min(timing 调) |
| 7 CI + 文档 | 20 min |

合计 4-5h。

---

## 风险点

1. **GameConfig 跨 sub-process picklable**: list of dict 应该 ok,但要测
2. **Director.on_tick 触发时机**: commit_delay 已经是 0,submit_directive 立即 process,但 build_acceptance 跑 non-realtime,game_time 推进很快,可能 100s 跨过 at_s 而没 tick — 加 `if now >= action["at_s"] and idx not in fired` 兜底
3. **telemetry frame rate**: 1Hz 可能不够 — `closest = min(...key=lambda r: abs(r["ts"] - target_time))` 容差 5s 应该 ok
4. **anchor 解析 home/enemy_main**: 需要从 GameConfig 或首帧 telemetry 拿 start_location,可能要 telemetry 增字段或读 spec.race + 地图 hard-code(DaybreakLE)
5. **action 时机 vs game timing**: 4bg 5:00 出门 vs 凤凰 6:00 出门 vs muta_ling_bane 不一定有 attack act 触发 — 写 yaml 时要根据 build_acceptance attack_moveout 实际 timing 设 at,留 30-60s buffer

---

## 完成定义

- 8 个 case 全 PASS(parallel 跑)
- 单测覆盖 schema + director scheduled action + verifier check
- runner 集成 CLAUDE.md 命令
- 一篇 docs/override-acceptance-runbook.md

**完成后**:这是 bug 12 类(玩家 override 失效)的最终防线 —— 任何新族 / 新 plan 漏接玩家 override,任何 attack-class refactor(task #310),CI 跑这 8 case 立刻 catch。

---

## 后续(不在本 plan)

- task #310: 抽 CombatIntentManager 全局单例(架构层)
- 扩 case: vision verb / scout / harass / recon 都加 e2e 验
- attack target_area override case(玩家"打他二矿"应该让单位去 enemy_natural)
