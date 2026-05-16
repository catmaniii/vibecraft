# VibeCraft

动动嘴 / 打打字指挥 AI 替你操作 SC2 神族 —— 给操作不动的老 SC2 玩家。

## 现在是什么状态

设计完成，实现进行中。详见：

- [`CLAUDE.md`](CLAUDE.md) —— Claude Code 启动指引，包含完整项目上下文
- [`docs/plans/2026-05-14-vibecraft-design.md`](docs/plans/2026-05-14-vibecraft-design.md) —— 14 节设计真理源
- [`USER_GUIDE.md`](USER_GUIDE.md) —— 玩家入门手册

## 快速开始（开发）

```bash
# 1. 装 uv（如果还没有）
#    Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
#    Mac:     curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 同步开发依赖
uv sync --extra dev

# 3. 跑单元测试
uv run pytest

# 4. lint + 类型检查
uv run ruff check .
uv run ruff format --check .
uv run mypy src/vibecraft

# 5. 装 pre-commit hook
uv run pre-commit install
```

`pip` 用户走 `python -m venv .venv` → `pip install -e ".[dev]"` 也可以。

## 端到端 smoke（需要 Windows + SC2 客户端）

ares-sc2 / burnysc2 / map-analyzer 都不在 PyPI，需要单独从 GitHub 装：

```bash
uv sync --extra dev
uv pip install \
  "git+https://github.com/AresSC2/ares-sc2@main"
uv run python scripts/smoke_test.py
```

详见 [`docs/m0-smoke-runbook.md`](docs/m0-smoke-runbook.md)（M0c 阶段写）。

## 目录布局

```
src/vibecraft/         # Python 包源码
  directives/           # Directive 数据模型 + Board
  strategy/             # 剧本库 + YAML schema
  dsl/                  # 条件 DSL parser/evaluator
  llm/                  # Intent Parser + Provider 抽象
  logging_/             # 结构化 JSONL 日志
  bot/                  # VibeCraftBot (ares-sc2 子类) + hooks
  server/               # WebSocket service + PWA static (M1+)
tests/
  unit/                 # 单元测试 (默认 mock，无 SC2)
  integration/          # 集成测试 (mock python-sc2)
  e2e/                  # 端到端 (需 SC2 客户端, default 跳过)
strategies/protoss/     # 剧本 YAML
aliases/                # 别名表 YAML
config/                 # llm.yaml / bot_difficulty.yaml
scripts/                # 一次性脚本（smoke / 工具）
docs/
  plans/                # 设计文档
  adr/                  # Architecture Decision Records
```

## 许可

私有。
