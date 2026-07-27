# 镜头选择 selector + 通用建筑回收 设计方案

> 2026-06-19。两个用户功能：① 按当前镜头视口框选单位/建筑（"镜头内的叉子/地堡/兵…"）；
> ② 通用"回收建筑"指令（地堡卖掉/回收，按建筑类型自动选 salvage ability）。
> 两者组合即"镜头内的地堡卖掉"。

## 目标

- **F1 镜头选择**：玩家说"镜头内的 X 做 Y"（编队 / 回收 / 进攻 / 待命…）→ bot 只选**当前镜头
  视口矩形框 ±12×±9 格**内、匹配条件的单位/建筑。复用现有 `get_camera_center()`。
- **F2 通用回收建筑**：玩家说"（镜头内的）地堡回收/卖掉/拆了"→ bot 对选中建筑下对应
  salvage ability。通用：按建筑类型映射（地堡→SALVAGEBUNKER_SALVAGE，感应塔→refund…），
  非可回收建筑友好拒绝。

两者解耦但可组合：F1 是"选哪些"，F2 是"对它们做什么"。

## 现状（调研结论）

- `facade.get_camera_center()` 已可读玩家当前镜头中心（SC2 `observation_raw.player.camera`，每帧刷新）。
  视口固定 24×18 格（`minimap.py`）。
- `Selector`（`directives/scope.py`）已**定义** `near_point`/`near_radius` 但 `resolve_selector`
  **从未实现**距离过滤。
- `execute_unit_action`（facade）Protocol 签名**已有 `ability_id` 参数**，但两个实现都只接了
  move/attack，**ability_id 分支没接**。`AbilityId.SALVAGEBUNKER_SALVAGE` 等枚举存在。
- 没有任何"对建筑下 ability"的 directive 类型。

## 前置 Step 0【评审阻断点②】：`resolve_selector` 支持建筑

**致命前提**：vendored python-sc2 `_prepare_units` 把 `is_structure` 的进 `self.structures`、
其余进 `self.units`，**二者互斥**。真机 `resolve_selector`（`common_bot.py:1396`）只
`for u in self.bot.units` → `resolve_selector(unit_type="Bunker")` **恒返回 []** → "镜头内的
地堡"静默选空、F2 整体失效。

**修法**：扩 `resolve_selector`：当 `unit_type` 是建筑类型（或无脑合并）时，候选源并上
`self.bot.structures`。建议遍历 `self.bot.units + self.bot.structures` 统一匹配（idle/matched/
gathering 排序对建筑无意义，建筑直接归 matched）。**先落这步 + 单测**，它是 F2 硬前提、也是未来
任何"建筑 selector"的地基。注意：现有 `cast_ability_on_units` 也用 `self.bot.units`（同坑），
别误用它做 salvage。

## F1 设计：镜头矩形框 selector

### Schema（`directives/scope.py`）

`Selector` 加一个布尔字段：

```python
near_camera: bool = False  # True=只选下达那刻镜头视口矩形框(±12×±9格)内的匹配单位/建筑
```

不动 `near_point`/`near_radius`（仍未实现，留作未来通用空间过滤；本次不碰，避免扩面）。
**守卫【评审⑥】**：`near_camera=true` 必须与 `unit_type` 或 `role` 同 present（否则会把镜头内
**所有** own 单位框进来，语义模糊）→ 业务层校验拒绝裸 near_camera。

### 注入 + 求值（`director.py`）：一次固化成 tags（**不**每帧 anchor 重过滤）【评审④】

放弃"selector 挂私有 anchor 字段、每帧用固定框 re-filter"的方案（单位走出/走入框会被错误增删，
违背"我说话那刻屏幕上那些"语义）。改成 **submit 时一次性解析固化**：

- 仿 `_inject_camera_point`（`director.py:9845`，注意它**当前只碰 TargetSpec.point、完全不碰
  selector**）——新增对**所有带 selector 的 payload**（UnitClaim / Salvage / GroupAssign / Move…）
  的处理：parse 后、submit 前遍历每个 selector，若 `near_camera==True`：
  1. 采样 `facade.get_camera_center()`（一次，锁住）。
  2. 立即 `facade.filter_tags_in_box(候选tags, cx, cy, 12, 9)` 解析成具体 tags
     （候选 = 按 unit_type/role 先 resolve）。
  3. **写回 `selector.tags = 解析结果`，置 `near_camera=False`**。
  → 此后该 directive 就是普通 tags selector，持续/一次性指令**生命周期统一**，无每帧漂移问题。
- **盒过滤 facade 方法**（新增，Protocol + 两实现 + audit）：
  ```python
  def filter_tags_in_box(self, tags: list[int], cx: float, cy: float,
                         half_w: float, half_h: float) -> list[int]: ...
  ```
  `_SharpyFacadeBase`：按 tag 从 `self.bot.units + self.bot.structures` 取 position，
  `abs(p.x-cx)<=half_w and abs(p.y-cy)<=half_h` 留下。FakeFacade：用预置坐标表实现，供单测。
- **镜头中心 y 偏移【评审⑦，非阻断】**：`observation_raw.player.camera` 是相机锚点，视觉上略低于
  屏幕几何中心。先按原值，真局自验若感觉框偏下，把中心 y 上移 1-2 格微调。

### 适用面

`near_camera` 是 selector 通用字段 → 任何吃 selector 的 directive 自动支持（且都在 submit 时固化）：
unit_claim（镜头内的兵进攻/待命）、group_assign（镜头内的兵编 1 队）、salvage（镜头内的地堡回收）。

### LLM prompt

rules.md：加一条——"镜头内 / 这屏 / 视野里的 / 这些（指屏幕上）X" → `selector.near_camera=true`。
few_shot.md：加 1-2 例（"镜头内的追猎编成 2 队"、"把镜头里的地堡都回收了"）。

## F2 设计：通用回收建筑 directive

### Schema（`directives/models.py` + `directives/types.py`）【评审阻断点①——纠正写法】

**不是** `kind: Literal[...]`（那是 done_when 条件的判别字段）。Payload union 判别器是
`Discriminator("type")`，所有 payload 继承 `_PayloadBase`（带 `done_when/activate_when/
extra="forbid"`）。正确做法（**五处同步**，非 done_when 那条"三处"规矩）：

1. `types.py`：`DirectiveType` 枚举加 `SALVAGE = "salvage"`。
2. `models.py`：
   ```python
   class SalvagePayload(_PayloadBase):
       type: Literal[DirectiveType.SALVAGE] = DirectiveType.SALVAGE
       selector: Selector   # 选哪些建筑（near_camera / unit_type=Bunker / tags…）
   ```
3. `models.py`：加进 `Payload` 判别联合（`PAYLOAD_MODELS` 自动派生）。
4. `director.py` `_apply_to_facade`（`:8358` 起）：加 `if t == DirectiveType.SALVAGE` 分支，
   参照 `STEALTH_MINE`（`:8674`）那段，含 `_set_override_status` 状态回报。
5. LLM prompt（rules/few_shot + 重 dump，见下）。

### 求值（`director.py`）

- 解析 selector → 建筑 tags（near_camera 已在 submit 时固化成 tags；unit_type=Bunker 走
  **已扩 structures 的** resolve_selector，见 Step 0）。
- 对每个 tag：按建筑 type_id 查 salvage ability。**地堡有两个变体【评审③】**：
  `SALVAGEBUNKER_SALVAGE` 与 `SALVAGEBUNKERREFUND_SALVAGE`（刚建好几秒走退款版，之后走普通版），
  活体地堡当下只暴露其中一个 available → 硬编码单一可能被游戏忽略。
  ```python
  _SALVAGE_ABILITY = {
      UnitTypeId.BUNKER: [AbilityId.SALVAGEBUNKER_SALVAGE, AbilityId.SALVAGEBUNKERREFUND_SALVAGE],
      UnitTypeId.SENSORTOWER: [AbilityId.SALVAGESENSORTOWERREFUND_SALVAGE],
  }
  ```
  策略：**两个变体都发一次**（无效的那个被游戏自然忽略），或 `get_available_abilities(unit)` 取
  交集挑当前可用的。优先"两个都发"（简单、稳）。未命中映射（不可回收建筑）→ 跳过 + 友好提示。
- 地堡内有兵能否直接 salvage：SC2 允许（兵被弹出），实现时确认一次，不必先卸载。
- 映射表放 director（或单独小模块），只列确实可回收的人族建筑；缺的将来加。

### 执行层（`facade.py` + `common_bot.py` + FakeFacade）

接通 ability 调用。复用已有 `execute_unit_action` 的 `ability_id` 形参 **或** 新增专用方法。
**选新增专用方法**更清晰（execute_unit_action 当前语义是"移动/攻击到某点"，硬塞 ability 易乱）：

```python
def cast_unit_ability(self, unit_tag: int, ability_id: str, target: dict|None = None) -> None: ...
```

- `_SharpyFacadeBase`（真机）：建筑在 structures，必须 `unit = self.bot.structures.by_tag(tag)`
  兜底 `self.bot.units.by_tag(tag)`；`ab = AbilityId[ability_id]`；BurnySc2 语法
  `self.bot.do(unit(ab))`（自施法）或 `self.bot.do(unit(ab, target_point))`（带点），与现有 chrono
  代码（`common_bot.py:746`）一致。找不到 unit / 非法 ability → log warning return（静默吞错但记日志）。
- `FakeFacade`（单测）：记录 `(tag, ability_id, target)` 到一个 list 供断言。
- **必跑 Protocol 一致性 audit**（`test_facade_release_unit_role.py` 那条）确认两实现齐。

### verb / alias（`docs/aliases/terran.yaml` + verb 白名单）

- Bunker 别名补："回收/拆/卖"相关不归到单位别名（那是建筑名），而是**动词**。
- 动词消歧：新增 tactical/操作动词 "salvage"（回收/拆/拆掉/卖/卖掉/拆迁）→ 映射 kind="salvage"。
  加进 rules.md 的 verb 白名单 + 消歧表。

### LLM prompt（rules + few_shot + 重 dump）

- rules.md：salvage directive 说明（"玩家说回收/拆/卖某建筑 → kind=salvage + selector"）。
- few_shot.md：例"把镜头里的地堡都回收了" → `{kind:salvage, selector:{unit_type:Bunker, near_camera:true}}`。
- 重跑 `scripts/dump_llm_prompt.py`。

## 玩家控制权 / 安全

- salvage 是一次性动作（非持续 claim），对建筑执行即结束，不占 Reserved。
- 跨种族校验：salvage 只对己方建筑；非本族/非己方建筑走现有拒绝。
- near_camera 采样一次锁定（不每帧重采），符合"目标一次规划锁定"铁律。

## 测试

- `test_scope.py`：near_camera 字段解析 + selector 模型。
- `director` 单测：near_camera 盒过滤（mock facade.filter_tags_in_box）；salvage 解析→建筑 tag→
  正确 ability 映射（地堡→SALVAGEBUNKER；非可回收→跳过+提示）。
- `test_facade_*`：cast_unit_ability 两实现 + Protocol audit。
- `test_terran_strategies` 无关；`test_done_when_models`/schema 回归（新 payload）。
- LLM 真解析抽测：`voice_spot_check.py` 加 2 case（镜头编队 / 镜头回收地堡）。

## 不做（YAGNI）

- 不实现通用 near_point/near_radius 圆形过滤（本次只做 near_camera 矩形框）。
- 不做"卖/拆任意建筑"（raze 自己建筑）——只做有 salvage ability 的（地堡/感应塔）。
- 不做镜头框的可视化描边（将来要再说）。

## 实施顺序（评审⑧微调：Step 0 先行，F1 可独立交付）

0. **`resolve_selector` 支持 structures**（F2 硬前提 + 未来建筑 selector 地基）+ 单测。先落、独立验。
1. facade 两个新方法 `filter_tags_in_box` + `cast_unit_ability`（Protocol + FakeFacade +
   _SharpyFacadeBase）+ 跑 `test_facade_release_unit_role.py` Protocol audit。
2. Schema：`Selector.near_camera`（+ 守卫校验）。**F1 先端到端跑通**（near_camera 固化成 tags +
   _inject_camera_point 扩 selector 遍历 + 盒过滤）——F1 本身可独立交付（"镜头内的兵编队/进攻"）。
3. Schema：`DirectiveType.SALVAGE` + `SalvagePayload`（_PayloadBase + type 判别 + union +
   `_apply_to_facade` 分支）。
4. Director：salvage 求值 + 地堡双变体 ability 映射 + 不可回收提示。
5. alias + verb 白名单（salvage：回收/拆/卖）+ LLM prompt（rules/few_shot）+ 重 dump。
6. 单测全绿 + voice_spot_check 抽测（镜头编队 / 镜头回收地堡）+ 真局自验（注入"镜头内地堡回收"，
   debug create 一个地堡 + 注入指令，grep 验真发 salvage ability）。
