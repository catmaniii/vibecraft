# 贡献指南

欢迎贡献。本仓库接受**中文或英文** PR，代码注释 / commit message 可用中文（项目以中文为主）。

## 本地开发环境

```bash
# 1. 装 uv（https://docs.astral.sh/uv/）和 Python 3.11+
# 2. 同步开发依赖
uv sync --extra dev
# 3.（可选，语音识别）装 SC2 / ASR 额外依赖
uv sync --extra dev --extra sc2     # python-sc2 / ares 等
#    ASR（FunASR + torch）较重，见下方「注意」
# 4.（可选）设置 LLM key —— 不设则指令解析会失败
export DEEPSEEK_API_KEY=sk-...       # 或在 config/llm.yaml 配置（见 config/llm.yaml.example）
```

> **注意（语音识别的坑）**：FunASR + torch 体积大、且本项目部分场景靠手动 `pip install` 装它们。
> 启动脚本 `scripts/start.ps1` 用 `--no-sync` 正是为了**防止 `uv sync` 把手动装的 torch/funasr 删掉**。
> 如果"语音识别突然不工作了"，先确认 torch/funasr 还在 venv 里。

## 测试

```bash
# 单元 + 集成测试（全程 mock，不需要真实 SC2）
uv run pytest                                   # 全部
uv run pytest -x                                # 首个失败即停
uv run pytest tests/unit/test_director.py       # 单个文件
uv run pytest tests/unit/test_director.py::test_xxx   # 单条用例
uv run pytest --cov=src/vibecraft --cov-report=term-missing   # 覆盖率

# 需要真实 SC2 客户端的端到端 / 真局自验（默认跳过；见 scripts/）
uv run pytest -m e2e
.venv/Scripts/python.exe scripts/build_acceptance.py <build_id> --opponent veryeasy
```

`tests/` 全部 mock python-sc2 / ares，**不允许在单测里真起 SC2**。真局验证走 `scripts/` 下的自验脚本。

## Lint / 类型检查

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # 格式
uv run mypy src/vibecraft      # 严格类型检查（strict mode）
uv run pre-commit run --all-files   # 一次性跑全部 hook
```

CI（`.github/workflows/ci.yml`）会跑 ruff + mypy + pytest，PR 需全绿。

## 提交 PR

- 标题：`feat(module): 简述` / `fix(module): 简述` / `docs:` / `chore:` 等
- 一个 PR 对应一个功能 / 修复，尽量小而聚焦
- 新功能 / 修复**带对应单测**；改了面向用户的能力，同步更新相关文档（README / USER_GUIDE / CHANGELOG）
- 改动外部接口 / 数据流，留意 `ARCHITECTURE.md` 的不变量

## 编码约定

- **代码符号（类名 / 函数名）用英文**；注释、文档、commit 可中文。
- **机密绝不入库**：API key / token / 证书 / 私钥放 `.secrets/` 或 gitignored 的配置文件，
  仓库里只提交去敏的 `*.example` 模板（见 `config/llm.yaml.example`、`deploy/turn/vibecraft-turn.env.example`）。
- **目标坐标一次规划、缓存、别每帧重选**（给单位 / 建筑下位置时）—— 每帧重算会让目标漂移、单位抽搐。
- 关键数据流（LLM 调用、directive 进出、hook 触发）落**结构化 JSONL 日志**，便于复盘。
- SC2 建筑在玩家话术里常用 hotkey 简称（如 BG=兵营、BE=水晶/补给站），单位用中文（追猎 / 不朽 / 小狗…）。
  这是面向玩家的领域术语约定，详见 `docs/`。

## 架构 / 文档

- `ARCHITECTURE.md` —— 当前代码结构 + 不变量 + 数据流（动代码前必看）
- `USER_GUIDE.md` —— 玩家入门 + 话术示例
- `docs/adr/` —— 关键架构决策记录
- `docs/pitfalls.md` —— 踩过的坑（开工前扫一眼相关条目）
- `docs/plans/*-design.md` —— 各大特性的设计文档

## 如何加一种语言

项目当前支持中/英双语，架构已预留扩展。加第 N 种语言需改四处：

1. **`locales/strings.json`**：每个 id 下加 `"<lang>": "译文"` 字段（如 `"ja": "..."`）。
   模板占位符名（如 `{name}`）必须与 zh/en 保持一致。
2. **前端 `web/src/i18n.ts`**：
   - `Locale` 类型加新 lang（如 `type Locale = 'zh' | 'en' | 'ja'`）。
   - `web/src/components/LanguageSwitcher.vue` 的选项数组加一项（`{ value: 'ja', label: '日本語' }` 之类）。
3. **后端 `bot/localization.py`**：`Localizer` 的名词表（UNIT / UPGRADE / PRODUCTION / TECH / VERB）
   加该 locale 列；若 SC2 官方英文名即可用（canonical），留空即 fallback，英文已基本免费。
4. **ASR（如需不同模型）**：参考 `src/vibecraft/server/asr.py` 的双模型模式——
   `AsrEngine` 新增 per-locale `_ensure_loaded` + `_model`/`_lock` + `warmup_<lang>()` 分支；
   `create_session(locale)` 路由到对应 session 类（流式/离线）；
   写对应 `scripts/prefetch_asr_<lang>.py` 供部署时预拉。

加完后：
```bash
cd web && npx vitest run                            # 前端翻译完整性
uv run pytest tests/unit/test_locale_penetration.py # locale 链路
.venv/Scripts/python.exe scripts/dump_llm_prompt.py # 刷新 LLM prompt 快照
```

有疑问开 Issue 讨论。感谢贡献！
