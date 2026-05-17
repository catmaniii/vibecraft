# LLM 上下文管理 + 纯 LLM eval suite 设计

> **For Claude**：REQUIRED SUB-SKILL：用 superpowers:executing-plans 跑这个 plan。

**Goal**：把 IntentParser 从"单轮无状态 call" 升到 multi-iteration agent loop +
局内对话 memory，让 LLM 看到 schema 错误能自纠 + 看到本局历史。同时建纯 LLM
eval suite（不拉 SC2）做 Flash vs Pro accuracy 对比。

**Architecture**：
- 现有 `IntentParser.parse` 升级成 multi-iter agent loop（保留同名 + 同 signature）
- `LLMProvider` 接口加 `parse_messages` 方法支持 multi-turn
- 新 `IntentParser._history` buffer 持局内 message 历史
- 新 `tests/llm_eval/` 用 pytest marker `llm_eval` 默认 skip

**Tech Stack**：Python 3.11 / Pydantic v2 / anthropic SDK / pytest / pytest-asyncio

---

## §1 当前状态 + 痛点

### 当前架构（单轮无状态）

```
IntentParser.parse(user_text, ctx)
  ↓
1. build_system_prompt + build_strategy_catalog + build_few_shot (cached)
2. build_dynamic_context(ctx)（含 recent_commands 最近 3 句）
3. provider.parse(system, few_shot, dynamic, user_text, tool_schema)
4. validate Directive 列表
   ├─ done_when 字段错 → retry 1 次（feedback 回灌）
   └─ 其它字段错 → 直接 ParseError
```

### 14 case CheatMoney + Flash 实测痛点（2026-05-17 跑两次）

| FAIL case | Flash 错误 | 根因 |
|---|---|---|
| L3b `让那个探机移动到气矿` | `unit_claim.task.primary_action.verb='move'` (enum 需 `move_to`) | LLM 没记住上次错误，反复出 `move` / `scout` 等非法 verb |
| L3d `所有人原地待命别动` | `engagement_constraint.stance='hold_position'` (需 `hold`) | LLM 混淆 `Verb` 和 `stance` enum |
| L4c `马上去开三矿` | LLM 多生成一条 `build_at(point='natural_third')` (需 tuple[float,float]) | LLM 想"实际去造"自作主张加 directive |

**根本问题**：LLM 第一次 schema 错后，error message 没回灌让它纠正。同样 case
反复跑反复错。

---

## §2 LLM 上下文管理设计（L1 + L2 + L3 全做）

### L1 schema-retry feedback（最小一层）

把 `_is_done_when_error` 升成 `_is_validation_error`（**所有** ValidationError
都触发 retry，不只 done_when）。

```python
async def _try_validate(directives_raw, retry_ctx):
    try:
        return [Directive.model_validate(d) for d in directives_raw]
    except ValidationError as e:
        if retry_ctx.attempts_left > 0:
            raise _RetryNeeded(error=e, raw=directives_raw)
        raise  # 已 max retry,降级
```

### L2 multi-iter tool loop（middle）

`IntentParser.parse` 重写为 N 轮 tool loop（默认 max_iter=3）：

```python
messages = [{"role": "user", "content": dynamic_ctx + user_text}]
for iter in range(max_iter):
    response = await provider.parse_messages(messages, system, tools)
    tool_use_block = extract_tool_use(response)
    try:
        validated = validate(tool_use_block.input["directives"])
        return IntentParseResult(directives=validated, iterations=iter+1)
    except ValidationError as e:
        # 把 assistant 的 tool_use + user 的 tool_result(error) 拼回去
        messages.append({"role": "assistant", "content": [tool_use_block]})
        messages.append({"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": tool_use_block.id,
            "is_error": True,
            "content": f"schema validation 失败:\n{e}\n请用合法字段重新生成。"
        }])
        continue
return ParseError(kind=DIRECTIVE_INVALID, ...)
```

这把 L1 自动包含（schema error 就是 retry 的触发条件）。

### L3 局内 conversation memory（最大）

`IntentParser` 持 `self._history: list[Message]`，每次成功 parse 后把
`(user_text, assistant_tool_use)` pair 加进 history，FIFO 淘汰保留最近 K 条
（默认 K=6 = 3 轮对话）。

```python
class IntentParser:
    def __init__(self, ..., history_buffer_turns: int = 3):
        self._history: list[dict] = []  # cached 段之后的 message list
        self._history_buffer_turns = history_buffer_turns

    async def parse(self, user_text, ctx):
        # 把历史 + 当前 user message 拼成完整 messages
        messages = list(self._history) + [{"role": "user", "content": ...}]
        # ... agent loop ...
        if success:
            self._history.append({"role": "user", ...})
            self._history.append({"role": "assistant", ...})
            self._trim_history()
```

**注意**：失败重试不进 history（避免污染）。只成功 parse 才进。

**cache 失效管理**：cached 段（system + few_shot）保持不变 → cache 仍然命中。
history 在 user/assistant turn 里，每次 call 变化，但占总 token 比例小。

### 三层叠加效果

| 场景 | L1 | L2 | L3 |
|---|---|---|---|
| 单条 directive schema 错 | ✅ retry | ✅ tool_result 回灌 | ✅ |
| 多条 directive 部分错 | ❌（整批 reject） | ✅ LLM 看 error 改对 | ✅ |
| 玩家先说"切叉球" 后说"那个不朽举起来" | ❌ | ❌ | ✅ LLM 看到上轮 strategy_set 知道是叉球 timing |
| 玩家二次澄清"我说的是探机不是不朽" | ❌ | ❌ | ✅ LLM 看到上轮 unit_claim 知道改谁 |

---

## §3 纯 LLM eval suite 设计

### 痛点

当前 `scripts/e2e_4_directive_types.py` 必须拉 SC2，每 case 60-90s wall，14
case ~15 分钟，且每 case 只跑 1 次（无 accuracy 统计）。测的不只 LLM 还有
sharpy/director/board 集成。

### 设计

新 `tests/llm_eval/`：
- `__init__.py`
- `conftest.py`：pytest marker `llm_eval`、`mock_parse_context` fixture
- `expected_specs.py`：14 个 `ExpectedSpec` 数据类
- `test_directive_parse_accuracy.py`：参数化 case × N 次 × model

#### `ExpectedSpec` 数据类

```python
@dataclass
class ExpectedSpec:
    name: str
    inject: str
    expect_type: DirectiveType            # 必须的 directive type
    must_have_paths: dict[str, Any]       # JSON path → expected value
    forbidden_paths: dict[str, Any] | None = None  # 不能出现的字段值
    allow_extra_directives: bool = False   # True = 允许 LLM 多生成
```

例：

```python
ExpectedSpec(
    name="L3b unit_claim ephemeral",
    inject="让那个探机移动到气矿",
    expect_type=DirectiveType.UNIT_CLAIM,
    must_have_paths={
        "payload.selector.unit_type": "Probe",
        "payload.task.primary_action.verb": "move_to",
        "payload.persistent": False,
    },
    forbidden_paths={
        "payload.task.primary_action.verb": ["scout", "move", "gather"],
    },
)
```

#### `mock_parse_context` fixture

```python
@pytest.fixture
def mock_parse_context() -> ParseContext:
    return ParseContext(
        game_time=180.0,           # 3 min（mid-early）
        current_stage=StageKind.OPENING,
        active_strategies={StageKind.OPENING: "1g_robo_immortal"},
        minerals=500, gas=200, supply_used=30, supply_cap=40,
        expansion_count=2,
        army_summary={"Probe": 22, "Immortal": 1, "Stalker": 2},
        enemy_summary={"Marine": 8},
        recent_commands=[],
        standing_orders=[],
    )
```

#### 测试方法

```python
@pytest.mark.llm_eval
@pytest.mark.parametrize("spec", LLM_EVAL_CASES)
@pytest.mark.parametrize("trial", range(3))  # 每 case 3 次
async def test_parse_accuracy(spec, trial, mock_parse_context, llm_parser):
    outcome = await llm_parser.parse(spec.inject, mock_parse_context)
    score = score_outcome(outcome, spec)
    pytest.assert_logging(...)  # 累计 stats
    assert score.passed, f"failed: {score.reason}"
```

`score_outcome` 返回 `Score(passed: bool, reason: str, matched_paths: int,
total_paths: int)`。

#### 运行

```bash
# 默认 skip llm_eval marker
.venv/Scripts/python.exe -m pytest                        # 597 单测

# 跑 LLM eval（Flash）
VIBECRAFT_LLM_MODEL=deepseek-v4-flash .venv/Scripts/python.exe -m pytest tests/llm_eval -m llm_eval

# 跑 LLM eval（Pro）
VIBECRAFT_LLM_MODEL=deepseek-v4-pro .venv/Scripts/python.exe -m pytest tests/llm_eval -m llm_eval

# 输出对比表（脚本汇总两次 run）
.venv/Scripts/python.exe tests/llm_eval/compare_models.py
```

#### 汇总输出

```
Case                              Flash         Pro
L1a strategy_set                 3/3 100%      3/3 100%
L1b strategy_cancel              3/3 100%      3/3 100%
L2a tactical_attack              3/3 100%      3/3 100%
L2b tactical_scout               2/3  67%      3/3 100%
L3b unit_claim ephemeral         0/3   0%      3/3 100%
L3c scout                        1/3  33%      3/3 100%
L3d engagement_hold              0/3   0%      3/3 100%
L4c expansion_override           1/3  33%      3/3 100%
...
TOTAL                          25/42  60%    41/42  98%
平均 LLM 耗时                    2.1s          5.8s
平均 token (in/out)              4200/180      4350/220
```

---

## §4 实施 phasing

### Phase 1: LLM eval suite 基础设施（不依赖 LLM 改动）

**Files**:
- 新 `tests/llm_eval/__init__.py`
- 新 `tests/llm_eval/conftest.py`：pytest marker + fixture
- 新 `tests/llm_eval/expected_specs.py`：14 case spec
- 新 `tests/llm_eval/score.py`：`score_outcome` 函数
- 新 `tests/llm_eval/test_directive_parse_accuracy.py`：参数化 test
- 改 `pyproject.toml`：加 `llm_eval` marker + 默认 skip

**Step**:
1. 写 ExpectedSpec 数据类
2. 写 14 case spec（mirror scripts/e2e_4_directive_types.py 的 CASES）
3. 写 score_outcome（用 nested dict path lookup）
4. 写 conftest fixture
5. 写参数化 test
6. 配 pyproject.toml marker
7. `pytest -m llm_eval` 跑通确认 mark 生效（Flash 当前 baseline）

### Phase 2: L1 schema-retry feedback loop

**Files**:
- 改 `src/vibecraft/llm/parser.py`：`_is_done_when_error` → `_is_validation_error`
- 改 `_DirectiveValidationFailed` 处理逻辑：所有 schema error 都 retry
- 改 `tests/unit/test_parser.py`：cover 新 retry 范围

**Step**:
1. `_is_done_when_error` 函数留下注释但行为变成 `_is_validation_error`（所有错都 retry）
2. parse 流程：max_iter 改 default 2，超过算 fail
3. 单测：verb error / stance error / point error 都能 retry
4. `pytest -m llm_eval` 跑 Flash 看是否 accuracy 提升

### Phase 3: L2 multi-iter tool loop

**Files**:
- 改 `src/vibecraft/llm/provider.py`：`LLMProvider` 加 `parse_messages` 方法
- 改 `src/vibecraft/llm/anthropic_provider.py`：实现 `parse_messages`
- 改 `src/vibecraft/llm/parser.py`：重写 `parse` 用 tool loop 而非 single call
- 改 `tests/unit/test_parser.py` + `tests/unit/test_provider.py`：cover

**Step**:
1. `LLMProvider` Protocol 加 `parse_messages(messages, system, tools, timeout) -> ProviderResponse`
2. `AnthropicProvider.parse_messages` 实现
3. `AnthropicProvider.parse` 保留作 backward-compat（内部调 parse_messages）
4. `MockLLMProvider.parse_messages` 实现
5. `IntentParser.parse` 用 tool loop（assistant tool_use → user tool_result(is_error=True)）
6. 单测：scripted multi-iter MockProvider 验证 loop 正确

### Phase 4: L3 局内 conversation memory

**Files**:
- 改 `src/vibecraft/llm/parser.py`：加 `_history` buffer + `_trim_history`
- 改 `tests/unit/test_parser.py`：cover 跨 turn memory

**Step**:
1. IntentParser `__init__` 加 `history_buffer_turns: int = 3` + `self._history`
2. parse 前缀把 history 拼进 messages
3. parse 成功后把 (user / assistant) pair 加 history
4. `_trim_history` FIFO 保留最近 K 轮
5. 失败重试不进 history
6. 单测：连续 2 次 parse，第 2 次 LLM 看到第 1 次的 history
7. 新加 `IntentParser.clear_history()` 用于新对局开始

### Phase 5: 跑 eval 对比 Flash vs Pro

**Step**:
1. config/llm.yaml model 切 flash → 跑 eval → 落盘 results_flash.json
2. config/llm.yaml model 切 pro → 跑 eval → 落盘 results_pro.json
3. 写 `tests/llm_eval/compare_models.py` 汇总输出 markdown 表
4. 把对比表加进 `docs/e2e-directive-tests.md` 或新建 `docs/llm-model-comparison.md`

---

## §5 验收标准

### Phase 1 验收

- `pytest -m llm_eval` 收集到 14 × 3 = 42 个 test
- 默认 `pytest` 不跑 llm_eval（skip）
- ExpectedSpec / score_outcome 单测 cover 数据类逻辑（不触 LLM）

### Phase 2 验收

- 14 case Flash baseline 在 L1 后 accuracy ≥ 80%（从 ~60% 提升）
- 单测 cover verb / stance / point 三类 schema error 都能 retry

### Phase 3 验收

- 单测 cover：MockProvider scripted [error, error, success] 三次 call → 第 3 次 PASS
- `parse_messages` provider 接口 + AnthropicProvider 实现都有单测

### Phase 4 验收

- 单测 cover：两次连续 parse，第 2 次 LLM 看到第 1 次 history
- 单测 cover：history buffer FIFO 淘汰，保留最近 K 轮
- 失败重试不进 history（即 retry call 不污染下次 parse）

### Phase 5 验收

- Flash + Pro 两次 eval 都跑通
- 对比表落 docs/

---

## 备注：与 e2e 测试关系

`scripts/e2e_4_directive_types.py` **保留**作为集成测试（验证 LLM + sharpy +
director + board 全栈通路）。`tests/llm_eval/` 是**单元层 accuracy 测试**
（只看 LLM 解析的对错率）。两个互补。

e2e 跑得慢但全栈；eval 跑得快但只验 LLM 层。CI 默认只跑单测 + e2e 跳过；
开发本地用 eval 调 LLM prompt 或对比 model 切换。
