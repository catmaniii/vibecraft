# ADR 0001：工具链选型

| 字段 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 状态 | Accepted |
| 范围 | 整个项目 |

## 决策

| 类别 | 选择 | 备选 | 理由 |
|---|---|---|---|
| 包管理 | **uv** (主推) + 兼容 pip/venv | poetry / hatch / pdm | 安装 10x 快，PEP 621 兼容；用户运行 smoke 时单条 `uv sync --extra sc2` 比手动 venv 顺 |
| Python 版本 | **3.11+** (3.13 排除) | 3.10 / 3.12 only | ares-sc2 文档建议 3.11；3.13 部分依赖未跟上 |
| 包布局 | **src/ layout** | flat layout | 防 import shadow，开发期 editable install 必须显式 |
| Lint + format | **ruff** | flake8 + black + isort | 一个工具替代三个，速度差 100x |
| 类型检查 | **mypy strict** + pydantic plugin | pyright | mypy 与 pydantic v2 plugin 集成更好 |
| 测试 | **pytest + pytest-asyncio + pytest-cov** | unittest / nose2 | 行业默认 |
| 数据建模 | **pydantic v2** | dataclasses / attrs | 设计文档已声明用 `BaseModel.model_validate`，且 LLM JSON 校验有现成 schema 输出 |
| 异步 | **anyio** (优先) → asyncio | trio only | anyio 兼容 asyncio + trio，未来好换 |
| WS server | **websockets**（M1 起用）| aiohttp / FastAPI ws | 轻量、纯 asyncio、无 HTTP framework 包袱（HTTP static 单独最简实现） |
| 日志 | **structlog** | loguru / 内置 logging | async 友好 + 原生 JSON 输出 |
| LLM | **anthropic** SDK | openai SDK | 设计文档默认 Claude；OpenAI / DeepSeek 走 provider 抽象层 |
| CLI | **click** | argparse / typer | 子命令组织方便，typer 依赖 pydantic 旧版 |
| 配置 | **PyYAML + pydantic** | tomli / 纯 yaml | 设计文档全部 YAML；pydantic 验证 schema |

## ares-sc2 安装策略

ares-sc2 **不发布到 PyPI**（仅 GitHub），其依赖 burnysc2 / map-analyzer 也是 git source。pyproject 的 optional-dependencies 不能用 git URL（pip 标准不允许），所以：

- 主 `pyproject.toml` **不声明 ares-sc2 依赖**，CI / 单测全程 mock
- M0c 端到端 / M1 真实集成的用户走 README 文档手动跑 `uv pip install "git+https://github.com/AresSC2/ares-sc2@main"`
- mypy override 已配 `ignore_missing_imports` 给 `ares.*` / `sc2.*` 等，没装也能过类型检查
- 未来若用 uv 专属语法，可在 `[tool.uv.sources]` 加 git，但会绑死 uv —— 暂不做

## 不做的事

- **不用 poetry**：lockfile 行为变化太频繁，uv.lock 更稳
- **不用 black**：ruff format 已 100% 兼容
- **不引 FastAPI**：M1 的 HTTP 只发静态文件 + WS upgrade，websockets + 极简 ASGI 已够
- **不引 nox/tox**：CI 矩阵两个 Python 版本就够，uv 直接切

## 影响

- 开发依赖小（~50 包），CI <30s
- mypy strict 会迫使所有 schema 完整标注 —— 这是 feature，不是 bug
- 切换 Python 版本走 `uv python install 3.12`，无 pyenv

## 后续

- 若 mypy strict 在某层（如 bot/ares 子类）成本过高，可在该模块加 `[[tool.mypy.overrides]]` 局部放宽，不全局放
- WS server 实现复杂度超预期则再评估 starlette
