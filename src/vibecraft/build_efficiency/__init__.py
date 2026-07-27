"""build 持续运营效率评价（2026-06-15）。

设计：docs/plans/2026-06-15-build-efficiency-eval-design.md
方法论留痕：docs/plans/2026-06-15-build-optimization-method-log.md

三维度（仅在同一 build 的变体之间纵向比，绝不跨 build/跨族横向比）：
- M1 余钱：囤钱积分越低越好（钱花干净 = 兵出得多）。最看重。
- M2 产能利用率：神/人产能建筑 busy 占比、折跃门冷却占比越高越好；虫族 larva 闲置积分越低越好。
- M3 卡人口：有钱有产能却卡人口的时长越短越好（滤掉 <4s 的 JIT 健康重叠）。

决策用**原始指标配对比较**（同 seed 下变体 vs 变体）；0-100 合成总分仅供人读。
"""

from vibecraft.build_efficiency.scorer import (
    EfficiencyReport,
    ScoreConfig,
    score_snapshots,
)

__all__ = ["EfficiencyReport", "ScoreConfig", "score_snapshots"]
