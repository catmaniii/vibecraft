# ADR 0005：LLM provider 切换到 DeepSeek V4（走 Anthropic 兼容端点）

日期：2026-05-15
状态：已采纳

## 背景

设计文档 §7.4 + CLAUDE.md 关键决策一直预留"留接口接 DeepSeekV4"。用户决定把
LLM 从官方 Claude 切到 DeepSeek V4。DeepSeek 提供两个兼容端点：

- OpenAI 兼容：`https://api.deepseek.com`
- Anthropic 兼容：`https://api.deepseek.com/anthropic`

模型：`deepseek-v4-flash`（快、便宜）/ `deepseek-v4-pro`（更强、更慢更贵）。

## 决策 1：走 Anthropic 兼容端点，复用 AnthropicProvider

**选择**：走 `https://api.deepseek.com/anthropic`，给 `AnthropicProvider` 加
`base_url` 参数（`AsyncAnthropic` SDK 原生支持），不新写 OpenAIProvider。

**理由**：

- 改动最小：消息结构 / tool_use 强制 JSON / usage 解析全部复用，零新 provider 实现
- 不增依赖：不需要引入 openai SDK
- 符合 provider 抽象意图：`LLMProvider` Protocol 本就是为屏蔽 provider 差异设计的

**代价 / 待验证**：DeepSeek 的 Anthropic 兼容端点对 `cache_control`、`tool_choice`
强制指定 tool、Anthropic 特有 usage 字段的兼容性未经真实 API 验证。usage 字段
缺失已有防御（`getattr(..., None) or 0`）；cache_control 见决策 3。

## 决策 2：provider 可配置切换，不删 anthropic

**选择**：`LLMConfig` 加 `base_url` / `api_key_env` 字段 + 模块级
`_PROVIDER_BASE_URL` / `_PROVIDER_API_KEY_ENV` 默认表。`build_provider` 对
`anthropic` / `deepseek` 都走 `_build_anthropic_compatible`，靠 base_url 区分。
`AnthropicProvider` 加 `provider_name` 参数，写进 `ProviderResponse.provider` +
`name`，让 `llm_calls/` 日志能区分实际 provider。

**理由**：用户重视架构灵活性，不绑死单一形态。切回官方 Claude 只需改
`config/llm.yaml` 三行（provider / model / base_url）。

**默认值**：pydantic 字段默认值保留 `anthropic` / `claude-sonnet-4-6` 作代码级
fallback；实际部署经 `config/llm.yaml`（已改为 deepseek）。`config/llm.yaml` 在
repo 里恒存在，`from_yaml_or_defaults` 的 defaults 分支实际很少触发。

## 决策 3：DeepSeek 端点默认关 prompt cache

**选择**：`config/llm.yaml` 设 `use_prompt_cache: false`。

**理由**：DeepSeek 文档明确 `cache_control` 为 "Ignored"（传了不报错也不生效）。
DeepSeek 自带 prefix 上下文自动缓存，不需要显式 `cache_control` 也能省成本。保持
`false` 即可，不必再传一个会被忽略的字段。

## 决策 4：禁用思考模式（真实 API 冒烟后的修正）

**问题**：真实调用 DeepSeek 端点返回 `400 - deepseek-reasoner does not support
this tool_choice`。`deepseek-v4-flash` 在 Anthropic 兼容端点**默认走思考模式**
（legacy 名 `deepseek-reasoner`），思考模式不兼容 `tool_choice` 强制指定 tool
（与 Anthropic 官方 extended thinking 的限制一致）。

**选择**：`AnthropicProvider` 加 `disable_thinking` 开关，为 True 时给
`messages.create` 传 `thinking={"type": "disabled"}`。`config/llm.yaml` 的 deepseek
配置设 `disable_thinking: true`。

**理由**：IntentParser 是结构化输出任务（玩家话语 → directives JSON），不需要推理。
禁用思考模式既解决 `tool_choice` 冲突，又更快、更便宜，完全契合 3s 超时 + 实时性
要求。`disable_thinking` 默认 False，不影响官方 Anthropic 路径。

**验证**：禁用思考后 `scripts/llm_smoke.py` 真实调用通过 —— "切1门Robo" →
`strategy_set` directive（`strategy_id=1g_robo_immortal`），置信度 0.9。

## API key

环境变量名按 provider 决定：`deepseek` → `DEEPSEEK_API_KEY`（也可在 yaml 里用
`api_key_env` 显式覆盖）。secret 不进 git、不进 yaml。spawn 子进程继承父进程
env，bot 子进程能拿到。

## 已验证（scripts/llm_smoke.py 真实 API 冒烟，2026-05-15）

- ✅ `tool_choice` 强制指定 `emit_directives` tool —— 禁用思考模式后生效（见决策 4）
- ✅ `cache_control` —— DeepSeek 文档明确为 "Ignored"（已设 `use_prompt_cache: false`）
- ✅ `deepseek-v4-flash` 基础解析质量 —— 单条指令解析正确、中文 interpretation
  准确、置信度合理

## 待验证（M1.6 真实 SC2 端到端时）

- 多指令 / 模糊指令 / 错误剧本名等复杂 case 的解析质量
- 真实游戏节奏下 3s 超时是否够（含网络往返）
