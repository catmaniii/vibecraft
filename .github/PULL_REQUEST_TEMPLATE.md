## 改了什么 / 为什么

<!-- 一段话说清动机。行为变更请说明"改前 → 改后"。 -->

## 怎么验的

- [ ] `uv run pytest`（新增/改动行为有对应单测）
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy src/vibecraft`
- [ ] 改了 bot 行为 → 跑过真局自验（贴关键日志行或 `scripts/build_acceptance.py` 结果）

> 本项目对"验证"有个硬要求：**内部自洽不算数**。单测绿 + 中间 trace 绿 ≠ 真机生效 ——
> 涉及给 SC2 下命令的改动，请给出**世界终态**的证据（telemetry 计数变化 / 日志里的实际结果）。
> 详见 `CLAUDE.md`。

## 文档

- [ ] `CHANGELOG.md` 加了条目（玩家能感知的改动必须加）
- [ ] 需要的话更新了 `README.md` / `USER_GUIDE.md` / `ARCHITECTURE.md`
