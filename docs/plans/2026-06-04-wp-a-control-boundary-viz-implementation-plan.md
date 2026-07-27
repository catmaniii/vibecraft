# WP-A 控制边界可视化 实施计划

> **For Claude:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 或 subagent-driven-development 逐 task 落地。写码 subagent 用 **Sonnet**；本 session Opus 做 brief + 两段 review + debug。

**Goal:** 让玩家随时看清"哪些单位归我的指令控制 / 哪些 bot 自由调度"——(1) 手机数据面板（snapshot 驱动，稳）+ (2) 游戏内 debug draw 给受控单位画框飘字（已实测多人可用，零漂移）。

**Architecture:** 单一数据源 = `Director._standing_order_tags`(directive_id→tags) + `_voice_groups`(group_id→tags)，tag→兵种用 `self._bot.units.by_tag`，中文标签复用 `_build_command_cards` 的 `display`。后端 `build_snapshot` 加 `controlled_units` 字段透传给 web；同一份数据 Director 每 tick 转成"画框清单"推给 facade，bot 每帧 `_tick_view_channel` 调 `facade.draw_debug_marks()` 重画（debug draw 必须每帧重发）。

**Tech Stack:** Python(pydantic/pytest) 后端 · python-sc2 `client.debug_box2_out`/`debug_text_world` · Vue3+TS+vitest 前端。

**已验证前提:** debug draw 在单人 + 2bot 多人(host/join) 都渲染（`scripts/debug_draw_probe.py`，坑：**绝不手动调 `_send_debug`**，框架每帧自动发）。

---

## 数据契约（先定，全任务共用）

`build_snapshot()` 新增 key `controlled_units`：

```python
{
  "controlled": [          # 每个玩家指令/编队一组
    {
      "source": "command", # "command"(unit_claim/move/scout/standing) | "group"(语音编队指挥)
      "directive_id": "d_ab12cd",
      "group_id": None,    # source=group 时为 1-5
      "label": "守瞭望塔",  # 中文,来自 command card display；查不到回退 verb/type
      "color": "cyan",     # 渲染色键(见 _CONTROL_COLORS)
      "count": 2,
      "composition": {"STALKER": 2},  # 兵种→存活数
    },
  ],
  "bot_free": {            # 不归任何玩家指令的己方军队单位
    "count": 9,
    "composition": {"STALKER": 5, "IMMORTAL": 4},
  },
}
```

色键表（后端定，前端 + debug draw 共用语义）：

```python
# director.py 模块级
_CONTROL_COLORS: dict[str, tuple[int, int, int]] = {
    "cyan":   (0, 220, 255),   # 普通指令(command)
    "g1":     (255, 230, 0),   # 1 队
    "g2":     (255, 140, 0),   # 2 队
    "g3":     (255, 0, 200),   # 3 队
    "g4":     (150, 90, 255),  # 4 队
    "g5":     (0, 255, 120),   # 5 队
}
# source=group → 色键 f"g{group_id}"；source=command → "cyan"
# bot_free 不画框(留白=不是你的)
```

---

## Track 1 · 后端数据模型

### Task 1: `_build_controlled_units_view()`

**Files:**
- Modify: `src/vibecraft/bot/director.py`（新增方法 + 模块级 `_CONTROL_COLORS`；紧挨 `_build_voice_groups_view` 行 ~667 之后）
- Test: `tests/unit/test_director.py`（新增 `class TestControlledUnitsView`）

**Step 1 — 写失败测试**

```python
class TestControlledUnitsView:
    """控制边界数据：哪些单位归玩家指令、哪些 bot 自由（WP-A）。"""

    def _director_with_units(self, session, owned):
        # owned: dict[tag] = "STALKER" 之类。构造带 fake bot.units 的 director。
        from tests.unit.test_director import _make_fake_bot_units  # 见下方 helper
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
        facade = FakeFacade()
        provider = MockLLMProvider(scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)])
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        d._bot = _make_fake_bot_units(owned)  # self._bot.units.by_tag / 可迭代
        return d

    def test_command_units_grouped_with_label_and_composition(self, session):
        d = self._director_with_units(session, {101: "STALKER", 102: "STALKER", 200: "IMMORTAL", 201: "IMMORTAL"})
        d._standing_order_tags["d_aa"] = {101, 102}        # 一条指令控 2 追猎
        d._override_status["d_aa"] = {"status": "active", "reason": ""}
        # label 来源：mock 一张 command card
        d._controlled_label_for = lambda did: "守瞭望塔" if did == "d_aa" else ""  # 见 impl 用的解析器
        view = d._build_controlled_units_view()
        ctrl = view["controlled"]
        assert len(ctrl) == 1
        assert ctrl[0]["directive_id"] == "d_aa"
        assert ctrl[0]["source"] == "command"
        assert ctrl[0]["color"] == "cyan"
        assert ctrl[0]["composition"] == {"STALKER": 2}
        assert ctrl[0]["count"] == 2
        # 200/201 不在任何指令 → bot_free
        assert view["bot_free"]["composition"] == {"IMMORTAL": 2}
        assert view["bot_free"]["count"] == 2

    def test_group_command_uses_group_color(self, session):
        d = self._director_with_units(session, {301: "VOIDRAY", 302: "VOIDRAY"})
        d._standing_order_tags["d_g"] = {301, 302}
        d._group_command_gid = {"d_g": 1}  # impl: directive→group_id 反查(见下)
        view = d._build_controlled_units_view()
        e = view["controlled"][0]
        assert e["source"] == "group" and e["group_id"] == 1 and e["color"] == "g1"

    def test_dead_tags_excluded(self, session):
        d = self._director_with_units(session, {101: "STALKER"})  # 102 已死(bot.units 无)
        d._standing_order_tags["d_aa"] = {101, 102}
        view = d._build_controlled_units_view()
        assert view["controlled"][0]["count"] == 1  # 只算存活
```

Run: `uv run pytest tests/unit/test_director.py::TestControlledUnitsView -x` → FAIL（无方法）。

**Step 2 — 实现**

在 director.py 加模块级 `_CONTROL_COLORS`（见数据契约）+ 方法：

```python
def _build_controlled_units_view(self) -> dict[str, Any]:
    """受控单位视图：每条玩家指令/编队一组 + bot_free 桶。WP-A。"""
    bot = getattr(self, "_bot", None)
    def _utype(tag: int) -> str | None:
        if bot is None:
            return None
        try:
            u = bot.units.by_tag(tag)
        except Exception:
            return None
        return str(u.type_id.name) if u is not None else None

    controlled: list[dict[str, Any]] = []
    claimed_tags: set[int] = set()
    for did, tags in self._standing_order_tags.items():
        comp: dict[str, int] = {}
        for t in tags:
            name = _utype(t)
            if name is None:  # 死了/不存在 → 跳过
                continue
            comp[name] = comp.get(name, 0) + 1
            claimed_tags.add(t)
        if not comp:
            continue
        gid = self._group_id_for_directive(did)  # 反查;无则 None
        source = "group" if gid is not None else "command"
        color = f"g{gid}" if gid is not None else "cyan"
        controlled.append({
            "source": source,
            "directive_id": did,
            "group_id": gid,
            "label": self._controlled_label_for(did),
            "color": color,
            "count": sum(comp.values()),
            "composition": comp,
        })

    # bot_free = 己方军队单位 − 受控(claimed)。只算非农民非建筑的军队?
    # MVP:算 self._bot.units 里所有非建筑、非农民单位中未被 claim 的(农民单独排除避免噪音)。
    free_comp: dict[str, int] = {}
    if bot is not None:
        for u in getattr(bot, "units", []):
            try:
                tag = int(u.tag); name = str(u.type_id.name)
            except Exception:
                continue
            if tag in claimed_tags:
                continue
            if name in _NON_ARMY_TYPES:  # PROBE/DRONE/SCV 等农民排除
                continue
            free_comp[name] = free_comp.get(name, 0) + 1
    return {
        "controlled": controlled,
        "bot_free": {"count": sum(free_comp.values()), "composition": free_comp},
    }
```

辅助：
- `_group_id_for_directive(did)`：遍历 `_voice_groups` 找哪个 group 的 tags == `_standing_order_tags[did]`（或交集非空）；测试里可直接喂 `_group_command_gid` dict，impl 优先读它再回退遍历。**简化**：编队指挥的 directive payload.selector.group_id 就是答案——`did` 对应的 directive 在 `_in_flight`/`_committed_directives`/`standing_orders` 里查 payload.selector.group_id。
- `_controlled_label_for(did)`：从 `_build_command_cards()` 结果里按 id 找 `display`；找不到回退到该 directive 的 verb/type 中文。为省开销，可在 `build_snapshot` 里先建好 `{card["id"]: card["display"]}` 传进来（见 Task 2）。
- `_NON_ARMY_TYPES`：模块级 frozenset，含 `PROBE/DRONE/SCV` + 建筑（建筑本就不在 bot.units，主要排农民）。

需要 `_make_fake_bot_units` test helper（模块级，test_director.py）：

```python
def _make_fake_bot_units(owned: dict[int, str]):
    from types import SimpleNamespace
    def mk(tag, name):
        return SimpleNamespace(tag=tag, type_id=SimpleNamespace(name=name))
    units = [mk(t, n) for t, n in owned.items()]
    by = {t: mk(t, n) for t, n in owned.items()}
    coll = SimpleNamespace()
    coll.__iter__ = lambda self=coll: iter(units)   # 可迭代
    coll.by_tag = lambda t: by.get(t)               # by_tag 不存在返回 None
    # SimpleNamespace 不直接支持 __iter__ 赋值生效 → 用一个小类
    class _Coll:
        def __init__(self, items, bytag): self._i=items; self._b=bytag
        def __iter__(self): return iter(self._i)
        def by_tag(self, t): return self._b.get(t)
    c = _Coll(units, by)
    return SimpleNamespace(units=c)
```

Run → PASS。

**Step 3 — Commit**
```
git add -A && git commit -m "feat(wp-a): Director._build_controlled_units_view 受控单位视图(指令组+bot_free)"
```

---

### Task 2: snapshot 透传 `controlled_units`

**Files:** Modify `src/vibecraft/bot/director.py`（`build_snapshot` ~646，紧挨 `voice_groups`）；Test 同文件加用例。

**Step 1 — 测试**
```python
def test_snapshot_includes_controlled_units(self, session):
    d = self._director_with_units(session, {101: "STALKER"})
    d._standing_order_tags["d_aa"] = {101}
    snap = d.build_snapshot(now=10.0)
    assert "controlled_units" in snap
    assert "controlled" in snap["controlled_units"] and "bot_free" in snap["controlled_units"]
```

**Step 2 — 实现**：在 `build_snapshot` 里，命令卡建好后（`cmd_cards = self._build_command_cards(now)`，行 ~647）传 label 映射：
```python
snapshot["voice_groups"] = self._build_voice_groups_view()
snapshot["max_voice_groups"] = _scope.MAX_VOICE_GROUPS
cmd_cards = self._build_command_cards(now)
snapshot["command_cards"] = cmd_cards
self._card_label_index = {c["id"]: c.get("display", "") for c in cmd_cards}  # 给 _controlled_label_for 用
snapshot["controlled_units"] = self._build_controlled_units_view()
```
`_controlled_label_for(did)` 读 `self._card_label_index.get(did) or <verb/type 回退>`。

**Step 3 — Commit**: `feat(wp-a): snapshot 透传 controlled_units`

---

## Track 2 · 手机数据面板（web）

### Task 3: 类型 + useWs 数据流

**Files:** `web/src/types.ts` · `web/src/composables/useWs.ts`（仿 `max_voice_groups`/`voice_groups` 那条链）

**Step 1 — 实现（无独立测试，类型管线；下个 task 的组件测试覆盖）**
- types.ts：`SnapshotFrame` 加 `controlled_units?: ControlledUnitsView`，并定义：
```typescript
export interface ControlGroupView {
  source: 'command' | 'group'
  directive_id: string
  group_id: number | null
  label: string
  color: string          // 'cyan' | 'g1'..'g5'
  count: number
  composition: Record<string, number>
}
export interface ControlledUnitsView {
  controlled: ControlGroupView[]
  bot_free: { count: number; composition: Record<string, number> }
}
```
- useWs.ts：`const controlledUnits = ref<ControlledUnitsView | null>(null)`；snapshot case `controlledUnits.value = f.controlled_units ?? null`；return 加 `controlledUnits: readonly(controlledUnits)`。

**Step 2 — 编译验证**: `cd web && npx vue-tsc --noEmit` → PASS。
**Step 3 — Commit**: `feat(wp-a): web 类型 + useWs 透传 controlled_units`

### Task 4: `ControlBoundaryPanel.vue` 组件

**Files:** Create `web/src/components/ControlBoundaryPanel.vue` · Test `web/src/components/__tests__/ControlBoundaryPanel.test.ts`

**Step 1 — 失败测试（vitest）**
```typescript
import { mount } from '@vue/test-utils'
import ControlBoundaryPanel from '@/components/ControlBoundaryPanel.vue'

const data = {
  controlled: [
    { source:'command', directive_id:'d1', group_id:null, label:'守瞭望塔', color:'cyan', count:2, composition:{STALKER:2} },
    { source:'group', directive_id:'d2', group_id:1, label:'1队进攻', color:'g1', count:3, composition:{VOIDRAY:3} },
  ],
  bot_free: { count:9, composition:{STALKER:5, IMMORTAL:4} },
}
it('渲染每条受控指令 + 兵种构成', () => {
  const w = mount(ControlBoundaryPanel, { props:{ data } })
  expect(w.text()).toContain('守瞭望塔')
  expect(w.text()).toContain('1队进攻')
  expect(w.text()).toContain('追猎')   // STALKER 中文(复用 VoiceGroupBar 的 UNIT_ZH)
  expect(w.find('[data-testid="ctrl-group-d1"]').exists()).toBe(true)
})
it('bot自由桶显示总数', () => {
  const w = mount(ControlBoundaryPanel, { props:{ data } })
  expect(w.find('[data-testid="bot-free"]').text()).toContain('9')
})
it('data 为 null 时整条隐藏', () => {
  const w = mount(ControlBoundaryPanel, { props:{ data: null } })
  expect(w.find('[data-testid="control-boundary"]').exists()).toBe(false)
})
```

**Step 2 — 实现**：组件渲染 `controlled` 每条为一行（左侧色点用 `color` 映射到 tailwind/inline 颜色，标签 + 兵种中文×数量），底部 `bot_free` 一行灰显。复用 VoiceGroupBar 的 `UNIT_ZH` 映射（抽到 `web/src/lib/unitNames.ts` 共享，或先复制）。色点颜色表对齐后端 `_CONTROL_COLORS`。`v-if="data && (data.controlled.length || data.bot_free.count)"`。

**Step 3 — Commit**: `feat(wp-a): ControlBoundaryPanel 控制边界数据面板`

### Task 5: 接进 App.vue / CockpitView

**Files:** `web/src/App.vue` · `web/src/views/CockpitView.vue`（仿 voiceGroups：解构 + `:controlled-units` 传入 + CockpitView props + 放进模板，建议在 VoiceGroupBar 附近）。
- Test：`cd web && npm test` 全绿 + `npm run build` 通过。
- Commit: `feat(wp-a): 控制边界面板接入座舱`

---

## Track 3 · 游戏内 debug draw

### Task 6: facade debug draw 接口

**Files:** `src/vibecraft/bot/facade.py`（Protocol + FakeFacade 加方法）· 真实现（`src/vibecraft/bot/auto_combat/common_bot.py` 的 facade 实现类，搜 `def execute_unit_action` 那个类）· Test `tests/unit/test_director.py` 用 FakeFacade 验调用。

**Step 1 — 测试（FakeFacade 记录调用）**
```python
def test_set_debug_marks_records(self, session):
    d = self._director_with_units(session, {101:"STALKER"})
    d.facade.set_debug_marks([{"tag":101,"color":(0,220,255),"label":"守塔"}])
    assert d.facade.debug_marks == [{"tag":101,"color":(0,220,255),"label":"守塔"}]
```

**Step 2 — 实现**
- facade.py Protocol 加：
```python
def set_debug_marks(self, marks: list[dict[str, object]]) -> None: ...
def draw_debug_marks(self) -> None: ...
```
- FakeFacade：`self.debug_marks=[]`；`set_debug_marks` 存下；`draw_debug_marks` no-op（或记次数）。
- 真实现（持有 `self.bot`/`self._bot`）：
```python
def set_debug_marks(self, marks): self._debug_marks = list(marks)
def draw_debug_marks(self):
    # 每帧调:对每个存活 mark 画框+飘字。绝不调 _send_debug,框架自动发。
    bot = self._bot
    for m in getattr(self, "_debug_marks", []):
        try:
            u = bot.units.by_tag(int(m["tag"]))
        except Exception:
            u = None
        if u is None:
            continue
        c = m.get("color", (0,220,255))
        bot.client.debug_box2_out(u, 0.6, color=c)
        lbl = m.get("label")
        if lbl:
            bot.client.debug_text_world(str(lbl), u, color=c, size=12)
```

**Step 3 — Commit**: `feat(wp-a): facade.set_debug_marks/draw_debug_marks(游戏内画框)`

### Task 7: Director 每 tick 算 mark 清单推给 facade

**Files:** `src/vibecraft/bot/director.py`（`on_tick` 末尾或 `_build_controlled_units_view` 旁加 `_push_debug_marks`）· `src/vibecraft/bot/director.py` DirectorConfig 加开关 · Test 同文件。

**Step 1 — 测试**
```python
def test_push_debug_marks_from_controlled(self, session):
    d = self._director_with_units(session, {101:"STALKER",102:"STALKER"})
    d._standing_order_tags["d_aa"] = {101,102}
    d._push_debug_marks()
    tags = sorted(m["tag"] for m in d.facade.debug_marks)
    assert tags == [101,102]
    assert all(isinstance(m["color"], tuple) for m in d.facade.debug_marks)
```

**Step 2 — 实现**：`_push_debug_marks()` 复用受控数据，把每个受控 tag 转 `{tag, color=_CONTROL_COLORS[色键], label}`，调 `self.facade.set_debug_marks(...)`。受 `DirectorConfig.debug_draw_control_boundary: bool = True` 控制（False 时推空清屏）。在 `on_tick` 末尾调用（每 tick 刷新期望 marks）。

**Step 3 — Commit**: `feat(wp-a): Director 每tick 推受控单位画框清单 + 开关`

### Task 8: bot 每帧 flush debug draw

**Files:** `src/vibecraft/bot/auto_combat/common_bot.py`（`_tick_view_channel` 末尾，行 ~1220，`director.on_tick` 之后加 `self.facade.draw_debug_marks()`）

**Step 1 — 实现**：
```python
if self.director is not None:
    self.director.on_tick(now=now_s)
try:
    self.facade.draw_debug_marks()   # 每帧重画(debug draw 必须每帧重发)
except Exception as exc:
    logger.debug("draw_debug_marks fail: %s", exc)
```
**Step 2 — 验证**：单测保证不崩（facade 有方法）；真效果走 e2e。
**Step 3 — Commit**: `feat(wp-a): bot 每帧 flush 受控单位 debug draw`

---

## 验收

1. **单测**：`uv run pytest tests/unit/test_director.py -q` 全绿；`cd web && npm test` 全绿、`npm run build` 通过。
2. **e2e 真效果**（需 SC2，Opus 截图判读法，参考 `scripts/debug_draw_probe.py`）：
   - 起一局，语音下 "3 个追猎守左边瞭望塔" + "所有虚空编 1 队、1 队进攻对方三矿"。
   - 截 SC2 窗口：守塔的 3 追猎身上 **cyan 框 + "守瞭望塔"**，1 队虚空 **黄(g1)框 + "1队进攻"**，其余军队**无框**。
   - 手机 PWA：ControlBoundaryPanel 显示两条受控组（兵种中文×数量）+ bot 自由桶计数。
3. **回归**：跑一局 `build_acceptance <sid> --opponent veryeasy` 确认画框逻辑不拖垮 tick（marks 空时近零开销）。

## 注意
- **绝不在 facade.draw_debug_marks 里调 `_send_debug`**（框架每帧自动发；手动发会被框架的空发擦掉——已踩坑，见 debug_draw_probe.py 注释）。
- 农民(PROBE)默认不算进 bot_free（避免噪音）；若玩家 claim 了农民(代理建造/侦查)，它在 controlled 里照常显示。
- debug draw 仅 realtime 有人看时有意义；non-realtime(build_acceptance) 下 marks 照推但无害（开关可关）。
