# 推荐策略评测报告

## 总览

- 运行编号：`20260701_163320`
- 创建时间：`2026-07-01T16:33:20`
- 摘要：共回放 0/5 个推荐样本，6/6 个指标通过，失败项 5 条。

## 元数据

- `cases_file`: `D:\trae\book_exam\evals\cases\recommendation_cases.json`
- `case_id`: ``
- `limit`: `None`
- `target_count`: `8`
- `total_cases`: `5`
- `completed_cases`: `0`
- `rank_strategy`: `mean`
- `temp_db_policy`: `TemporaryDirectory; never writes cache/attempts.db`
- `top_items`: `{}`

## 指标

| 指标 | 数值 | 阈值 | 结果 | 说明 |
|---|---:|---:|---|---|
| `bkt_monotonic_pass` | 100.00% | >= 100.00% | PASS | 0/0 个 BKT 方向检查通过 |
| `weak_topic_hit_rate` | 100.00% | >= 80.00% | PASS | 0/0 个薄弱章节进入 TopK |
| `mastered_retire_rate` | 100.00% | >= 80.00% | PASS | 0/0 个低优先级章节排序低于薄弱章节 |
| `recommendation_reason_rate` | 100.00% | >= 90.00% | PASS | 0/0 个推荐条目包含原因 |
| `error_to_type_match_rate` | 100.00% | >= 80.00% | PASS | 0/0 个推荐题型匹配错因预期 |
| `delta_mastery_valid_rate` | 100.00% | >= 100.00% | PASS | 0/0 个 BKT delta 在合理范围内 |

## 失败明细

| Case | 题目 | 原因 | 证据 |
|---|---|---|---|
| `rec_001` | `rec_001` | 推荐回放执行异常 | {"error": "[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\20659\\\\AppData\\\\Local\\\\Temp\\\\book_exam_eval_wpddmd54'", "scenario": "连续答错的任务状态应进入推荐 Top3"} |
| `rec_002` | `rec_002` | 推荐回放执行异常 | {"error": "[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\20659\\\\AppData\\\\Local\\\\Temp\\\\book_exam_eval_8yes5kfy'", "scenario": "全对序列的 BKT P(L) 应上升，已掌握 topic 应退场"} |
| `rec_003` | `rec_003` | 推荐回放执行异常 | {"error": "[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\20659\\\\AppData\\\\Local\\\\Temp\\\\book_exam_eval_we2czl_o'", "scenario": "全错序列的 BKT P(L) 应下降并被优先推荐"} |
| `rec_004` | `rec_004` | 推荐回放执行异常 | {"error": "[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\20659\\\\AppData\\\\Local\\\\Temp\\\\book_exam_eval_1xor39zg'", "scenario": "主要错因为记忆缺失时，推荐题型应偏 choice/fill_blank"} |
| `rec_005` | `rec_005` | 推荐回放执行异常 | {"error": "[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\20659\\\\AppData\\\\Local\\\\Temp\\\\book_exam_eval_gpaxeszm'", "scenario": "样本不足但低掌握的知识点应保留探索机会"} |
