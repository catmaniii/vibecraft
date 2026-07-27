"""TelemetryLogger record 构造纯函数测试。"""

from __future__ import annotations

from types import SimpleNamespace

from vibecraft.bot.telemetry import (
    build_economy_block,
    build_enemy_block,
    build_event_record,
    build_game_start_record,
    build_snapshot_record,
)


def _pt(x, y):
    return SimpleNamespace(x=x, y=y)


def test_event_record_building():
    rec = build_event_record(
        t=18.3, kind="building_started", unit="GATEWAY", tag=123, pos=_pt(94.4, 104.4)
    )
    assert rec == {
        "t": 18.3,
        "kind": "building_started",
        "unit": "GATEWAY",
        "tag": 123,
        "pos": [94.4, 104.4],
    }


def test_event_record_upgrade_no_pos():
    rec = build_event_record(t=211.0, kind="upgrade_complete", upgrade="WARPGATERESEARCH")
    assert rec == {"t": 211.0, "kind": "upgrade_complete", "upgrade": "WARPGATERESEARCH"}


def test_game_start_record():
    rec = build_game_start_record(
        t=0.0,
        home=_pt(127.5, 119.5),
        enemy_main=_pt(48.5, 28.5),
        natural=_pt(145.5, 98.5),
        enemy_natural=_pt(30.5, 50.5),
        active_recipe="dt_drop_iac",
        my_race="Protoss",
    )
    assert rec["kind"] == "game_start"
    assert rec["home"] == [127.5, 119.5]
    assert rec["enemy_main"] == [48.5, 28.5]
    assert rec["natural"] == [145.5, 98.5]
    assert rec["enemy_natural"] == [30.5, 50.5]
    assert rec["active_recipe"] == "dt_drop_iac"
    # player_name 默认空串（旧局 / 沙盒兜底）
    assert rec["player_name"] == ""


def test_game_start_record_player_name():
    """player_name 填充后落进 record（admin 对局记录显示玩家名）。"""
    rec = build_game_start_record(
        t=0.0,
        home=_pt(1, 2),
        enemy_main=_pt(3, 4),
        natural=None,
        enemy_natural=None,
        active_recipe="4bg",
        my_race="Protoss",
        player_name="alice",
    )
    assert rec["player_name"] == "alice"


def test_game_start_record_no_enemy_natural():
    """enemy_natural 取不到（None）时落 None,不报错。"""
    rec = build_game_start_record(
        t=0.0,
        home=_pt(1, 2),
        enemy_main=_pt(3, 4),
        natural=None,
        enemy_natural=None,
        active_recipe="dt_rush",
        my_race="Protoss",
    )
    assert rec["natural"] is None
    assert rec["enemy_natural"] is None


def test_enemy_block():
    enemy = build_enemy_block(
        enemy_workers=12,
        enemy_army_count=5,
        enemy_army_center=_pt(40.5, 30.5),
        enemy_workers_harassed=3,
        enemy_workers_killed=2,
    )
    assert enemy == {
        "enemy_workers": 12,
        "enemy_army_count": 5,
        "enemy_army_center": [40.5, 30.5],
        "enemy_workers_harassed": 3,
        "enemy_workers_killed": 2,
    }


def test_enemy_block_no_vision():
    """视野内没有敌方军队时 enemy_army_center 落 None，击杀计数默认 0。"""
    enemy = build_enemy_block(
        enemy_workers=0,
        enemy_army_count=0,
        enemy_army_center=None,
    )
    assert enemy["enemy_army_center"] is None
    assert enemy["enemy_workers"] == 0
    assert enemy["enemy_workers_harassed"] == 0


def test_economy_block():
    eco = build_economy_block(
        mineral_workers=25,
        gas_workers=0,
        idle_workers=2,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[25, 16], [0, 16]],
    )
    assert eco == {
        "mineral_workers": 25,
        "gas_workers": 0,
        "idle_workers": 2,
        "mineral_ideal": 32,
        "gas_ideal": 6,
        "base_saturation": [[25, 16], [0, 16]],
    }


def test_snapshot_record():
    eco = build_economy_block(
        mineral_workers=20,
        gas_workers=2,
        idle_workers=0,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[12, 16], [8, 16]],
    )
    enemy = build_enemy_block(
        enemy_workers=14,
        enemy_army_count=3,
        enemy_army_center=_pt(50, 60),
    )
    rec = build_snapshot_record(
        t=120.0,
        supply_used=24,
        supply_cap=39,
        workers=22,
        army_supply=4,
        minerals=150,
        vespene=80,
        bases=2,
        army_center=_pt(100, 110),
        units={"STALKER": 2, "ZEALOT": 0},
        buildings={"GATEWAY": 3, "WARPGATE": 1},
        key_units={"WARPPRISM": [_pt(114, 115)]},
        active_recipe="dt_drop_iac",
        economy=eco,
        enemy=enemy,
    )
    assert rec["kind"] == "snapshot"
    assert rec["army_center"] == [100.0, 110.0]
    assert rec["units"] == {"STALKER": 2, "ZEALOT": 0}
    assert rec["buildings"] == {"GATEWAY": 3, "WARPGATE": 1}
    assert rec["key_units"] == {"WARPPRISM": [[114.0, 115.0]]}
    assert rec["economy"]["gas_workers"] == 2
    assert rec["enemy"]["enemy_workers"] == 14
    assert rec["enemy"]["enemy_army_center"] == [50.0, 60.0]


def test_snapshot_record_with_tactical():
    """2026-05-28 诊断:snapshot 含 tactical 字段(intent/stance/mode/plan_status)。"""
    eco = build_economy_block(
        mineral_workers=20,
        gas_workers=2,
        idle_workers=0,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[12, 16]],
    )
    enemy = build_enemy_block(
        enemy_workers=14,
        enemy_army_count=3,
        enemy_army_center=_pt(50, 60),
    )
    rec = build_snapshot_record(
        t=120.0,
        supply_used=24,
        supply_cap=39,
        workers=22,
        army_supply=4,
        minerals=150,
        vespene=80,
        bases=2,
        army_center=_pt(100, 110),
        units={},
        buildings={},
        key_units={},
        active_recipe="4bg",
        economy=eco,
        enemy=enemy,
        tactical={
            "intent": "retreat",
            "stance": "retreat",
            "mode": None,
            "target_set": False,
            "plan_status": "Retreat",
            "attack_retreat_started": 315.5,
        },
    )
    assert rec["tactical"]["intent"] == "retreat"
    assert rec["tactical"]["plan_status"] == "Retreat"
    assert rec["tactical"]["attack_retreat_started"] == 315.5


def test_snapshot_record_without_tactical_backcompat():
    """tactical=None(默认)→ 不加 tactical key(向后兼容旧 telemetry 验证脚本)。"""
    eco = build_economy_block(
        mineral_workers=20,
        gas_workers=2,
        idle_workers=0,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[12, 16]],
    )
    enemy = build_enemy_block(
        enemy_workers=14,
        enemy_army_count=3,
        enemy_army_center=_pt(50, 60),
    )
    rec = build_snapshot_record(
        t=120.0,
        supply_used=24,
        supply_cap=39,
        workers=22,
        army_supply=4,
        minerals=150,
        vespene=80,
        bases=2,
        army_center=_pt(100, 110),
        units={},
        buildings={},
        key_units={},
        active_recipe="4bg",
        economy=eco,
        enemy=enemy,
    )
    assert "tactical" not in rec


def test_snapshot_record_with_stealth_cells():
    """传 stealth_cells → record 带 stealth_cells key（offline 偷矿可观测）。"""
    eco = build_economy_block(
        mineral_workers=20,
        gas_workers=2,
        idle_workers=0,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[12, 16]],
    )
    enemy = build_enemy_block(enemy_workers=0, enemy_army_count=0, enemy_army_center=None)
    cells = [{"cell_id": 1, "state": "mining", "nexus_assigned": 5, "worker_count": 2}]
    rec = build_snapshot_record(
        t=300.0,
        supply_used=40,
        supply_cap=54,
        workers=44,
        army_supply=10,
        minerals=200,
        vespene=100,
        bases=2,
        army_center=None,
        units={},
        buildings={},
        key_units={},
        active_recipe="dt_drop_iac",
        economy=eco,
        enemy=enemy,
        stealth_cells=cells,
    )
    assert rec["stealth_cells"] == cells


def test_snapshot_record_without_stealth_cells_backcompat():
    """stealth_cells=None(默认)→ 不加 key(向后兼容旧 telemetry 验证脚本)。"""
    eco = build_economy_block(
        mineral_workers=20,
        gas_workers=2,
        idle_workers=0,
        mineral_ideal=32,
        gas_ideal=6,
        base_saturation=[[12, 16]],
    )
    enemy = build_enemy_block(enemy_workers=0, enemy_army_count=0, enemy_army_center=None)
    rec = build_snapshot_record(
        t=120.0,
        supply_used=24,
        supply_cap=39,
        workers=22,
        army_supply=4,
        minerals=150,
        vespene=80,
        bases=2,
        army_center=None,
        units={},
        buildings={},
        key_units={},
        active_recipe="4bg",
        economy=eco,
        enemy=enemy,
    )
    assert "stealth_cells" not in rec


def test_extract_stealth_cells_reads_manager_with_drain_signal():
    """从 bot.director._stealth_manager.cells 抓 cell 状态 + nexus_assigned(SC2 引擎采矿数)。

    nexus_assigned(5) > worker_count(2) = 主矿农民倒灌 DRAIN，离线可直接判出。
    """
    from vibecraft.bot.telemetry import extract_stealth_cells

    cell = SimpleNamespace(
        cell_id=1,
        state=SimpleNamespace(value="mining"),
        point=(134.5, 28.5),
        worker_tags={101, 102},
        gas_worker_tags=set(),
        gas_tags=set(),
        nexus_tag=999,
    )
    bot = SimpleNamespace(
        director=SimpleNamespace(_stealth_manager=SimpleNamespace(cells={1: cell})),
        structures=SimpleNamespace(
            find_by_tag=lambda t: SimpleNamespace(assigned_harvesters=5) if t == 999 else None
        ),
    )
    out = extract_stealth_cells(bot)
    assert len(out) == 1
    c = out[0]
    assert c["cell_id"] == 1
    assert c["state"] == "mining"
    assert c["location"] == [134.5, 28.5]
    assert c["worker_count"] == 2
    assert c["nexus_assigned"] == 5
    # DRAIN 信号离线可判
    assert c["nexus_assigned"] > c["worker_count"]


def test_extract_stealth_cells_no_director_safe():
    """bot 没 director / 没 manager → 安全返 []（不抛）。"""
    from vibecraft.bot.telemetry import extract_stealth_cells

    assert extract_stealth_cells(SimpleNamespace()) == []
    assert extract_stealth_cells(SimpleNamespace(director=None)) == []
    assert (
        extract_stealth_cells(SimpleNamespace(director=SimpleNamespace(_stealth_manager=None)))
        == []
    )


def test_extract_tactical_state_no_knowledge_safe():
    """bot 没 knowledge / vibecraft → 安全返 None 占位 dict(不抛)。"""
    from vibecraft.bot.telemetry import extract_tactical_state

    class _DummyBot:
        pass

    state = extract_tactical_state(_DummyBot())
    assert state["intent"] is None
    assert state["stance"] is None
    assert state["mode"] is None
    assert state["target_set"] is False
    assert state["plan_status"] is None


def test_extract_tactical_state_reads_vibecraft_namespace():
    """有 vibecraft 命名空间 → 正确读 intent/stance/mode/target_set。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import extract_tactical_state

    bot = SimpleNamespace(
        knowledge=SimpleNamespace(
            vibecraft=SimpleNamespace(
                combat_intent_override="retreat",
                stance_override="defend",
                attack_mode_override="probe",
                attack_target_override=(50.0, 60.0),
            )
        )
    )
    state = extract_tactical_state(bot)
    assert state["intent"] == "retreat"
    assert state["stance"] == "defend"
    assert state["mode"] == "probe"
    assert state["target_set"] is True


def test_extract_tactical_state_name_fallback_finds_plan_zone_attack():
    """#526:多人局 isinstance 可能因 sharpy 双重导入身份不一致而恒 False。构造一个
    类名为 'PlanZoneAttack' 但**非**真 sharpy 实例的节点,验名字兜底仍能命中并读到
    status,且 plan_dbg 记录 pza=name(区分身份不一致 vs 没找到)。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import extract_tactical_state

    class PlanZoneAttack:  # 同名异类(模拟双重导入身份不一致)
        def __init__(self) -> None:
            self.status = SimpleNamespace(name="Attacking")
            self.attack_retreat_started = 123.456

    fake_pza = PlanZoneAttack()
    step = SimpleNamespace(action=fake_pza)
    plan = SimpleNamespace(orders=[step])
    bot = SimpleNamespace(knowledge=SimpleNamespace(ai=SimpleNamespace(build_plan=plan)))

    state = extract_tactical_state(bot)
    assert state["plan_status"] == "Attacking"
    assert state["attack_retreat_started"] == 123.46
    assert "plan_dbg" not in state  # 命中 → 不落诊断面包屑


def test_extract_tactical_state_prefers_active_plan_zone_attack():
    """#526:多 PlanZoneAttack 的 build(skytoss/blink_harass)里,信号应取真正在战斗的
    那个(status 非 NotActive),而不是 BFS 第一个(可能是 NotActive 的空闲实例)。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import extract_tactical_state

    class PlanZoneAttack:
        def __init__(self, status_name: str) -> None:
            self.status = SimpleNamespace(name=status_name)
            self.attack_retreat_started = None

    idle = PlanZoneAttack("NotActive")  # BFS 更靠前的空闲实例
    fighting = PlanZoneAttack("Attacking")  # 真正在战斗的实例
    plan = SimpleNamespace(orders=[SimpleNamespace(action=idle), SimpleNamespace(action=fighting)])
    bot = SimpleNamespace(knowledge=SimpleNamespace(ai=SimpleNamespace(build_plan=plan)))

    state = extract_tactical_state(bot)
    assert state["plan_status"] == "Attacking", "应优先取 status 非 NotActive 的 PZA"


def test_extract_tactical_state_plan_dbg_when_not_found():
    """#526:plan_status 还是 None 时落 plan_dbg 面包屑,记录哪层断的(无 PlanZoneAttack
    节点 → pza=none,且 bp/nodes 反映 walk 结果)。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import extract_tactical_state

    leaf = SimpleNamespace()  # 不是 PlanZoneAttack
    plan = SimpleNamespace(orders=[leaf])
    bot = SimpleNamespace(knowledge=SimpleNamespace(ai=SimpleNamespace(build_plan=plan)))

    state = extract_tactical_state(bot)
    assert state["plan_status"] is None
    assert "plan_dbg" in state
    assert "pza=none" in state["plan_dbg"]
    assert "nodes=" in state["plan_dbg"]


def test_walk_plan_tree_seeds_from_actmanager(monkeypatch):
    """#526 回归:sharpy 真实结构里 plan 根在 knowledge.managers 的 ActManager._act,
    **不在** bot.build_plan。walker 必须从 managers 的 _act 取根 —— 否则真局拿不到树根、
    plan_status 恒 None(单测之前用 build_plan 手搭所以测试绿真局黑)。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import _walk_plan_tree

    leaf = SimpleNamespace()
    step = SimpleNamespace(action=leaf)
    plan = SimpleNamespace(orders=[step])  # BuildOrder 根
    act_manager = SimpleNamespace(_act=plan)  # ActManager._act 持有 plan
    other_manager = SimpleNamespace()  # 无 _act 的普通 manager
    # 注意:ai 上**没有** build_plan(模拟真实 sharpy bot),只能靠 managers 找到
    knowledge = SimpleNamespace(ai=SimpleNamespace(), managers=[other_manager, act_manager])
    seen = _walk_plan_tree(knowledge)
    assert plan in seen, "应从 ActManager._act 取到 plan 根"
    assert step in seen
    assert leaf in seen


def test_walk_plan_tree_normal_shallow_tree():
    """正常浅树:BuildOrder.orders → Step.act 链,守卫不影响,正确收集所有 node。"""
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import _walk_plan_tree

    act_leaf = SimpleNamespace()  # 叶 act,无 orders/act
    step = SimpleNamespace(act=act_leaf)
    plan = SimpleNamespace(orders=[step])
    knowledge = SimpleNamespace(ai=SimpleNamespace(build_plan=plan))

    seen = _walk_plan_tree(knowledge)
    assert plan in seen
    assert step in seen
    assert act_leaf in seen
    assert len(seen) == 3


def test_walk_plan_tree_descends_step_action_and_ifelse():
    """回归(2026-06-13):sharpy Step / IfElse 把内层 act 存 `.action` / `.action_else`,
    **不是** `.act`。旧 walker 只查 `.act` → 进不去 Step/IfElse → PlanZoneAttack
    (总是 `Step(gate, PlanZoneAttack)` 包在 `IfElse` 分支里)永远找不到 → plan_status
    恒 None。这里构造 BuildOrder→IfElse(action / action_else)→Step(action)→leaf,断言
    两个分支的叶子都被收集到。
    """
    from types import SimpleNamespace

    from vibecraft.bot.telemetry import _walk_plan_tree

    attack_leaf = SimpleNamespace()  # 模拟 PlanZoneAttack
    else_leaf = SimpleNamespace()
    step = SimpleNamespace(action=attack_leaf)  # Step 用 .action,不是 .act
    ifelse = SimpleNamespace(action=step, action_else=else_leaf)  # IfElse 分支
    seq = SimpleNamespace(orders=[ifelse])
    plan = SimpleNamespace(orders=[seq])
    knowledge = SimpleNamespace(ai=SimpleNamespace(build_plan=plan))

    seen = _walk_plan_tree(knowledge)
    assert ifelse in seen
    assert step in seen
    assert attack_leaf in seen, "Step.action(PlanZoneAttack 所在)必须被遍历到"
    assert else_leaf in seen, "IfElse.action_else 分支也要遍历"


def test_walk_plan_tree_pathological_input_terminates_bounded():
    """畸形对象图(裸 MagicMock:getattr(.,'act') 恒返新 auto-child → 无限深链)
    必须被 depth/node 守卫砍断并有界返回,不得 hang / OOM。

    回归:2026-05-28 引入的 _walk_plan_tree 无守卫,单测塞 MagicMock bot 调
    build_snapshot → extract_tactical_state → 此处无限增长队列 → 进程 OOM(EXIT=87)。
    """
    from unittest.mock import MagicMock

    from vibecraft.bot.telemetry import _MAX_PLAN_NODES, _walk_plan_tree

    seen = _walk_plan_tree(MagicMock())
    # 终止且有界即证守卫生效(无守卫则此调用 hang,测试超时而非通过)。
    assert 0 < len(seen) <= _MAX_PLAN_NODES


def test_extract_tactical_state_magicmock_bot_safe():
    """端到端:裸 MagicMock bot 喂 extract_tactical_state 不 hang,安全返 dict。"""
    from unittest.mock import MagicMock

    from vibecraft.bot.telemetry import extract_tactical_state

    state = extract_tactical_state(MagicMock())
    assert "intent" in state
    assert "plan_status" in state


def test_telemetry_logger_snapshot_throttle():
    """maybe_write_snapshot 每 ~2s 才真正写一次。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append, snapshot_interval_s=2.0)
    snap = {"kind": "snapshot", "t": 0.0}
    tl.maybe_write_snapshot(now=0.0, record=snap)  # 第一次:写
    tl.maybe_write_snapshot(now=1.0, record=snap)  # 1s:节流跳过
    tl.maybe_write_snapshot(now=2.5, record=snap)  # 2.5s:写
    assert len(written) == 2


def test_telemetry_logger_event_passthrough():
    """write_event 直接落 sink,不节流。"""
    from vibecraft.bot.telemetry import TelemetryLogger

    written: list[dict] = []
    tl = TelemetryLogger(sink_fn=written.append)
    tl.write_event({"kind": "building_started", "t": 1.0})
    tl.write_event({"kind": "building_complete", "t": 2.0})
    assert len(written) == 2


# ---------------------------------------------------------------------------
# compute_army_center —— role-based 排除持久任务单位(2026-05-31)
# ---------------------------------------------------------------------------


class _FakeUnit:
    def __init__(self, tag, name, x, y, is_structure=False):
        self.tag = tag
        self.type_id = SimpleNamespace(name=name)
        self.position = _pt(float(x), float(y))
        self.is_structure = is_structure


class _FakeUnits(list):
    """最小 Units mock:支持 filter() + center。"""

    def filter(self, fn):
        return _FakeUnits([u for u in self if fn(u)])

    @property
    def center(self):
        n = len(self)
        return _pt(sum(u.position.x for u in self) / n, sum(u.position.y for u in self) / n)

    def __bool__(self):
        return len(self) > 0


def _fake_bot(units, *, reserved_tags=(), scouting_tags=()):
    """构造 fake bot,roles.all_from_task(3/8) 返回 Scouting/Reserved 单位。"""
    by_tag = {u.tag: u for u in units}

    def all_from_task(task_id):
        tags = scouting_tags if task_id == 3 else (reserved_tags if task_id == 8 else ())
        return [by_tag[t] for t in tags if t in by_tag]

    roles = SimpleNamespace(all_from_task=all_from_task)
    return SimpleNamespace(units=_FakeUnits(units), knowledge=SimpleNamespace(roles=roles))


def test_compute_army_center_excludes_workers_and_support():
    """工人 / 非战斗支援按兵种排除,不进质心。"""
    from vibecraft.bot.telemetry import compute_army_center

    units = [
        _FakeUnit(1, "STALKER", 50, 50),
        _FakeUnit(2, "PROBE", 0, 0),  # 工人,排
        _FakeUnit(3, "OBSERVER", 0, 0),  # 支援,排
        _FakeUnit(4, "STALKER", 50, 50),
    ]
    c = compute_army_center(_fake_bot(units))
    assert (c.x, c.y) == (50.0, 50.0)  # 只剩两 stalker


def test_compute_army_center_excludes_persistent_task_units():
    """role∈{Reserved,Scouting} 的单位排除 —— 12 凤凰骚扰不拖偏地面军质心。"""
    from vibecraft.bot.telemetry import compute_army_center

    # 4 stalker 在家 [50,30],12 凤凰在敌方家 [120,115] 但被 Reserve
    units = [_FakeUnit(i, "STALKER", 50, 30) for i in range(1, 5)]
    phoenix = [_FakeUnit(100 + i, "PHOENIX", 120, 115) for i in range(12)]
    units += phoenix
    phx_tags = tuple(u.tag for u in phoenix)

    # 不排除 → 质心被 12 凤凰按 3:1 拖向敌方家
    c_no = compute_army_center(_fake_bot(units))
    assert c_no.x > 100

    # Reserve 凤凰(持久骚扰)→ 质心 = 地面军 [50,30]
    c_yes = compute_army_center(_fake_bot(units, reserved_tags=phx_tags))
    assert (c_yes.x, c_yes.y) == (50.0, 30.0)


def test_compute_army_center_returns_unit_after_task_cleared():
    """任务清除(role 归还)→ 凤凰重新计入主力质心。"""
    from vibecraft.bot.telemetry import compute_army_center

    units = [_FakeUnit(1, "STALKER", 50, 30), _FakeUnit(2, "PHOENIX", 150, 30)]
    # Reserved 时只算 stalker
    assert compute_army_center(_fake_bot(units, reserved_tags=(2,))).x == 50.0
    # 任务清除(Attacking,不在 Reserved/Scouting)→ 凤凰计入 → 质心 = (50+150)/2
    assert compute_army_center(_fake_bot(units)).x == 100.0


def test_compute_army_center_no_army_returns_none():
    """只有工人 / 无单位 → None。"""
    from vibecraft.bot.telemetry import compute_army_center

    assert compute_army_center(_fake_bot([_FakeUnit(1, "PROBE", 0, 0)])) is None
    assert compute_army_center(_fake_bot([])) is None


def test_compute_army_center_no_roles_falls_back_to_type_only():
    """无 roles manager(异常)→ 退化纯兵种排除,不抛。"""
    from vibecraft.bot.telemetry import compute_army_center

    bot = SimpleNamespace(
        units=_FakeUnits([_FakeUnit(1, "STALKER", 10, 10)]),
        knowledge=SimpleNamespace(roles=None),
    )
    c = compute_army_center(bot)
    assert (c.x, c.y) == (10.0, 10.0)


# ── build 效率评价系统 Phase 0 埋点（2026-06-15）─────────────────────────────

from vibecraft.bot.telemetry import TelemetryLogger, build_production_block  # noqa: E402


class _ProdStruct:
    def __init__(self, busy: bool) -> None:
        self.orders = [object()] if busy else []


class _ProdUnits:
    """最小 Units 替身：.ready 返回自身（测试里都当 ready）、.amount、可迭代。"""

    def __init__(self, items):
        self._items = list(items)

    @property
    def ready(self):
        return self

    @property
    def amount(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


class _ProdBot:
    def __init__(self, race, structs, larva=0):
        self.race = SimpleNamespace(name=race)
        self._structs = structs  # {type_name: _ProdUnits}
        self.larva = SimpleNamespace(amount=larva)

    def structures(self, ut):
        return self._structs.get(ut.name, _ProdUnits([]))


def test_production_block_protoss_with_warpgate():
    bot = _ProdBot(
        "Protoss",
        {
            "GATEWAY": _ProdUnits([_ProdStruct(True), _ProdStruct(False)]),  # 2, busy 1
            "ROBOTICSFACILITY": _ProdUnits([_ProdStruct(True)]),  # 1, busy 1
            "STARGATE": _ProdUnits([]),  # 0
        },
    )
    blk = build_production_block(bot, warpgate_total=3, warpgate_busy=2)
    assert blk["gateway"] == {"total": 2, "busy": 1}
    assert blk["robo"] == {"total": 1, "busy": 1}
    assert blk["stargate"] == {"total": 0, "busy": 0}
    assert blk["warpgate"] == {"total": 3, "busy": 2}
    # busy 1+1+0+2=4, total 2+1+0+3=6 → 0.6667
    assert blk["util"] == round(4 / 6, 4)


def test_production_block_terran_orders_based():
    bot = _ProdBot(
        "Terran",
        {
            "BARRACKS": _ProdUnits([_ProdStruct(True), _ProdStruct(False)]),
            "FACTORY": _ProdUnits([_ProdStruct(False)]),
            "STARPORT": _ProdUnits([]),
        },
    )
    blk = build_production_block(bot)
    assert blk["barracks"] == {"total": 2, "busy": 1}
    assert blk["factory"] == {"total": 1, "busy": 0}
    assert "warpgate" not in blk  # 人族无折跃门
    assert blk["util"] == round(1 / 3, 4)


def test_production_block_zerg_larva():
    bot = _ProdBot("Zerg", {}, larva=5)
    blk = build_production_block(bot)
    assert blk["larva"] == 5
    assert blk["util"] is None  # 虫族 util 由 scorer 用 larva 闲置积分另算


def test_production_block_util_none_when_no_buildings():
    bot = _ProdBot("Protoss", {})
    blk = build_production_block(bot, warpgate_total=0, warpgate_busy=0)
    assert blk["util"] is None  # total=0 → 不除零


def test_snapshot_record_with_production_and_opening_completed():
    eco = build_economy_block(
        mineral_workers=16,
        gas_workers=6,
        idle_workers=0,
        mineral_ideal=16,
        gas_ideal=6,
        base_saturation=[[16, 16]],
    )
    enemy = build_enemy_block(enemy_workers=0, enemy_army_count=0, enemy_army_center=None)
    prod = {"gateway": {"total": 2, "busy": 2}, "util": 1.0}
    rec = build_snapshot_record(
        t=300.0,
        supply_used=60,
        supply_cap=70,
        workers=40,
        army_supply=20,
        minerals=120,
        vespene=80,
        bases=2,
        army_center=_pt(90, 100),
        units={"STALKER": 8},
        buildings={"GATEWAY": 4},
        key_units={},
        active_recipe="4bg",
        economy=eco,
        enemy=enemy,
        production=prod,
        opening_completed_at=240.0,
    )
    assert rec["production"] == prod
    assert rec["opening_completed_at"] == 240.0


def test_snapshot_record_without_production_backcompat():
    eco = build_economy_block(
        mineral_workers=16,
        gas_workers=6,
        idle_workers=0,
        mineral_ideal=16,
        gas_ideal=6,
        base_saturation=[[16, 16]],
    )
    enemy = build_enemy_block(enemy_workers=0, enemy_army_count=0, enemy_army_center=None)
    rec = build_snapshot_record(
        t=10.0,
        supply_used=14,
        supply_cap=15,
        workers=14,
        army_supply=0,
        minerals=50,
        vespene=0,
        bases=1,
        army_center=None,
        units={},
        buildings={},
        key_units={},
        active_recipe="4bg",
        economy=eco,
        enemy=enemy,
    )
    assert "production" not in rec
    assert "opening_completed_at" not in rec


def test_telemetry_logger_due():
    written: list = []
    tel = TelemetryLogger(sink_fn=written.append, snapshot_interval_s=2.0)
    assert tel.due(0.0) is True  # 初始 _last=-1000 → 立刻 due
    tel.maybe_write_snapshot(0.0, {"kind": "snapshot", "t": 0.0})
    assert len(written) == 1
    assert tel.due(1.0) is False  # 距上次 1s < 2s
    assert tel.due(2.0) is True  # 到 2s


# ── 虫族助卵埋点（2026-06-15）────────────────────────────────────────────────


class _Q:
    """fake queen units 集合：只需 .amount。"""

    def __init__(self, n):
        self._n = n

    @property
    def amount(self):
        return self._n


class _TH:
    def __init__(self, injected):
        self._inj = injected

    def has_buff(self, _b):
        return self._inj


class _Townhalls:
    def __init__(self, items):
        self._items = items

    @property
    def amount(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


class _ZergBot:
    def __init__(self, larva, queens, hatches_injected):
        self.race = SimpleNamespace(name="Zerg")
        self.larva = SimpleNamespace(amount=larva)
        self._queens = _Q(queens)
        self.townhalls = _Townhalls([_TH(i) for i in hatches_injected])

    def units(self, _ut):
        return self._queens

    def structures(self, _ut):
        return _ProdUnits([])


def test_production_block_zerg_inject_coverage():
    # 3 基地，2 个被注卵 → coverage 2/3；larva 6；女王 3
    bot = _ZergBot(larva=6, queens=3, hatches_injected=[True, True, False])
    blk = build_production_block(bot)
    assert blk["larva"] == 6
    assert blk["queens"] == 3
    assert blk["hatches"] == 3
    assert blk["injected_hatches"] == 2
    assert blk["inject_coverage"] == round(2 / 3, 3)
    assert blk["util"] is None  # 虫族 util 由 scorer 助卵×卵消耗 另算


def test_production_block_zerg_inject_full():
    bot = _ZergBot(larva=2, queens=2, hatches_injected=[True, True])
    blk = build_production_block(bot)
    assert blk["inject_coverage"] == 1.0  # 全注卵
