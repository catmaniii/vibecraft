"""ASR 热词生成脚本（FunASR voice input plan Task 1）。

从以下数据源生成 FunASR 热词表（config/asr_hotwords.txt）：
1. docs/aliases/{protoss,zerg,terran}.yaml —— 建筑/单位/升级别名（权重 15）
2. strategies/**/*.yaml —— 剧本 display_name_zh + aliases（权重 15）
3. 内置战术黑话（硬编码，权重 20）

输出格式（FunASR hotwords.txt）：每行 "词 权重"，如 "闪追 20"。
权重约定：别名/剧本词 = 15；战术黑话 = 20（黑话更要压住，优先级高）。
去重：同词只留一条；若黑话表和别名表都有同一词，取黑话权重（20 > 15）。

用法：
    .venv/Scripts/python.exe scripts/gen_asr_hotwords.py
    # 输出到 config/asr_hotwords.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# 添加 src/ 到 sys.path，让脚本可以 import vibecraft 包（同其他脚本惯例）
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from vibecraft.strategy.aliases import AliasTable  # noqa: E402

_ALIASES_DIR = _ROOT / "docs" / "aliases"
_STRATEGIES_DIR = _ROOT / "strategies"
_OUTPUT_PATH = _ROOT / "config" / "asr_hotwords.txt"

# 别名/剧本词权重
_ALIAS_WEIGHT = 15
# 战术黑话权重（高于别名，优先让 ASR 命中）
_JARGON_WEIGHT = 20

# 内置战术黑话列表（SC2 黑话 + 常见玩家口语）
_TACTICAL_JARGON: list[str] = [
    # 经典开局/战术名
    "4BG",
    "IAC",
    "12D",
    "闪追",
    "Skytoss",
    "两矿凤凰",
    "MMM",
    "12pool",
    "DT偷家",
    "两矿飞龙",
    # 常见战术口语
    "一波流",
    "一波timing",
    "代理建造",
    "前置建筑",
    "双矿",
    "三矿",
    "四矿",
    "开二矿",
    "开三矿",
    "换家",
    "龟缩",
    "all-in",
    "rush",
    "压制",
    "骚扰",
    "偷家",
    "闪烁追猎",
    "虚空骚扰",
    "凤凰骚扰",
    "DT骚扰",
    "不朽叉球",
    "chargelot",
    "archon",
    "blink",
    "charge",
    "Psi Storm",
    "灵能风暴",
    "叉球一波",
    "电兵叉球",
    "skytoss",
    "天空神族",
]

# 只加载建筑/单位/升级三组别名的 YAML 文件（protoss/zerg/terran 格式）
_ALIAS_RACE_FILES = ["protoss.yaml", "zerg.yaml", "terran.yaml"]


def _is_noise(word: str) -> bool:
    """过滤噪音词：单字符（含单个英文字母或单个中文字），或空字符串。"""
    return len(word.strip()) < 2


def _collect_from_aliases(aliases_dir: Path) -> list[str]:
    """从 docs/aliases/protoss|zerg|terran.yaml 收集建筑/单位/升级别名词条。

    使用 AliasTable.from_yaml 加载（复用现有别名加载器）。
    收集 default_display + aliases 列表（不含 canonical 内部 id，因为
    canonical 通常是 PascalCase 英文标识符，玩家不会说）。
    """
    words: list[str] = []
    for fname in _ALIAS_RACE_FILES:
        fpath = aliases_dir / fname
        if not fpath.exists():
            continue
        table = AliasTable.from_yaml(fpath)
        for group in (table.buildings, table.units, table.upgrades):
            for entry in group.values():
                words.append(entry.default_display)
                words.extend(entry.aliases)
    return words


def _collect_from_strategies(strategies_dir: Path) -> list[str]:
    """从 strategies/**/*.yaml 收集剧本 display_name_zh + aliases。"""
    words: list[str] = []
    if not strategies_dir.exists():
        return words
    for path in sorted(strategies_dir.rglob("*.yaml")):
        try:
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # 剧本中文名
        display = data.get("display_name_zh", "")
        if display and isinstance(display, str):
            words.append(display)
        # 剧本别名列表
        aliases = data.get("aliases", [])
        if isinstance(aliases, list):
            words.extend(str(a) for a in aliases if a)
    return words


def build_hotwords(
    aliases_dir: Path,
    strategies_dir: Path,
) -> list[tuple[str, int]]:
    """汇总三源热词，去重，返回 (词, 权重) 列表（按权重降序、词字典序排列）。

    权重策略：
    - 内置战术黑话优先（权重 20），先写进 seen 字典。
    - 别名表/剧本词（权重 15）只在 seen 中不存在时才写入
      （即同一词同时在黑话和别名表里 → 取高权重 20）。
    - 过滤：len < 2 的词（单字符英文字母 / 单个中文字等）跳过。
    """
    seen: dict[str, int] = {}  # word → weight，高权重覆盖低

    # 1. 内置战术黑话（权重 20，先写入，后续不被覆盖）
    for word in _TACTICAL_JARGON:
        w = word.strip()
        if not _is_noise(w):
            seen[w] = _JARGON_WEIGHT

    # 2. 别名表（权重 15）
    for word in _collect_from_aliases(aliases_dir):
        w = word.strip()
        if _is_noise(w):
            continue
        if w not in seen:
            seen[w] = _ALIAS_WEIGHT

    # 3. 剧本（权重 15）
    for word in _collect_from_strategies(strategies_dir):
        w = word.strip()
        if _is_noise(w):
            continue
        if w not in seen:
            seen[w] = _ALIAS_WEIGHT

    # 按权重降序 + 词字典序升序排列，保持输出确定性
    return sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))


def main() -> int:
    """生成 config/asr_hotwords.txt，每行格式：词 权重。"""
    hotwords = build_hotwords(_ALIASES_DIR, _STRATEGIES_DIR)
    lines = [f"{word} {weight}" for word, weight in hotwords]
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"生成热词表 → {_OUTPUT_PATH}")
    print(f"  共 {len(lines)} 条（其中权重 20 黑话 {sum(1 for _, w in hotwords if w == 20)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
