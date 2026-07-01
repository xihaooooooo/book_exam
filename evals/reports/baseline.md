# 当前离线 Agent 评测 Baseline

- Baseline ID：`offline_agent_eval_current`
- Run ID：`20260629_151029`
- 设置日期：`2026-06-29`
- 运行命令：`python scripts/run_all_evals.py --generation-limit 5`
- 运行环境：`conda:D:\conda\envs\book_exam`
- 运行模式：离线评测，关闭 LangSmith/LangChain tracing

## 汇总

| 指标 | 分数 | 状态 |
|---|---:|---|
| 总分 | 90.48% | PASS |
| 出题质量 | 100.00% | PASS |
| 判题一致性 | 71.43% | FAIL |
| 推荐策略 | 100.00% | PASS |

摘要：总分 90%，出题 100%，判题 71%，推荐 100%；失败项 2 条，回退指标 0 个。

## 报告文件

- 完整聚合报告：`evals/reports/agent_eval_20260629_151029.md`
- 完整聚合 JSON：`evals/reports/agent_eval_20260629_151029.json`
- 出题报告：`evals/reports/generation_eval_20260629_151029.md`
- 判题报告：`evals/reports/judge_eval_20260629_151029.md`
- 推荐报告：`evals/reports/recommendation_eval_20260629_151029.md`

## 已接受限制

当前基线不接入 LLM-as-judge，不使用真实学生数据，也不接入 CI。它的定位是本地离线回归对比锚点。

剩余 2 个失败项均来自判题一致性：

| Case | 原因 | 说明 |
|---|---|---|
| `judge:judge_008` | false negative：实际正确但判为错误 | 无 LLM 离线模式下，主观题语义等价无法由规则稳定识别。 |
| `judge:judge_013` | false negative：实际正确但判为错误 | 无 LLM 离线模式下，综合题语义等价无法由规则稳定识别。 |
