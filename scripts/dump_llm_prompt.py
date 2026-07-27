"""导出当前提供给 LLM 的完整 system prompt（3 段静态部分）。

包含：
1. System prompt — 角色 / 任务 / 输出 schema / 别名表 / 规则
2. Strategy catalog — 全部剧本一览（按 my_race 过滤）
3. Few-shot examples — 典型话语 → directives 示例

不含动态 context（每次解析才生成的 game state 摘要）。

用法::

    .venv/Scripts/python.exe scripts/dump_llm_prompt.py [my_race]

    my_race: Protoss(default) / Terran / Zerg

输出到 docs/llm_system_prompt.md。
"""

from __future__ import annotations

import sys
from pathlib import Path

from vibecraft.llm.prompt import (
    build_few_shot,
    build_race_block,
    build_system_prompt,
)
from vibecraft.strategy.aliases import AliasTable
from vibecraft.strategy.library import StrategyLibrary

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    my_race = sys.argv[1] if len(sys.argv) > 1 else "Protoss"
    aliases_path = _ROOT / "docs" / "aliases" / f"{my_race.lower()}.yaml"
    library = StrategyLibrary.from_directories(
        strategies_dir=_ROOT / "strategies",
        aliases_path=aliases_path,
    )
    aliases = AliasTable.from_yaml(aliases_path)

    sys_prompt = build_system_prompt(aliases)
    race_block = build_race_block(aliases, library, my_race=my_race)
    few_shot = build_few_shot()

    out_path = _ROOT / "docs" / "llm_system_prompt.md"
    parts = [
        f"# LLM System Prompt (my_race={my_race})\n",
        "> 自动生成自 `src/vibecraft/llm/prompt.py`，由 `scripts/dump_llm_prompt.py` 导出。\n",
        "> 2026-05-25 cache 优化:rules 单独 cache 块(永久)+ race_block + few_shot",
        "> 合并第 2 cache 块(同族命中,切族 invalid 但 rules 仍命中)。",
        "> 实际每次 parse 还会追加 §4 动态 context（game state 摘要 / 最近 N 句 / 等）。\n",
        "\n---\n",
        "## §1 System prompt (rules,永久 cache)\n",
        "```\n" + sys_prompt + "\n```\n",
        "\n---\n",
        "## §2a Race block (alias + catalog,同族 cache)\n",
        "```\n" + race_block + "\n```\n",
        "\n---\n",
        "## §2b Few-shot examples (race-specific 例,同族 cache,和 §2a 合并发 LLM)\n",
        "```\n" + few_shot + "\n```\n",
    ]
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"written to {out_path}")
    print(f"  total chars: {sum(len(p) for p in parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
