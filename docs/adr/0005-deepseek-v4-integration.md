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

**理由**：给 DeepSeek 端点传 Anthropic 特有的 `cache_control` 字段，兼容性未验证
（可能报错）。DeepSeek 自带 prefix 上下文自动缓存，不需要显式 `cache_control` 也能
省成本。真实 API 验证确认兼容后可改回 true。

## API key

环境变量名按 provider 决定：`deepseek` → `DEEPSEEK_API_KEY`（也可在 yaml 里用
`api_key_env` 显式覆盖）。secret 不进 git、不进 yaml。spawn 子进程继承父进程
env，bot 子进程能拿到。

## 待验证（M1.6 真实 SC2 端到端时一起做）

- `tool_choice` 强制指定 `emit_directives` tool 在 DeepSeek 兼容端点是否生效
- `cache_control` 传过去是报错还是被忽略
- `deepseek-v4-flash` 在 3s 超时内的解析质量 / 准确率（不够再换 `deepseek-v4-pro`）
