"""纯 LLM 解析正确率测试套件。

不拉 SC2,直接 mock ParseContext 喂 IntentParser,看 LLM 输出对错率。
默认 skip(真调 LLM API 烧钱);用 `-m llm_eval` 或 `--runeval` 才跑。

设计见 docs/plans/2026-05-17-llm-context-mgr-and-eval.md §3。
"""
