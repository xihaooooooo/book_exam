# 推荐策略评测报告

## 总览

- 运行编号：`20260629_144637`
- 创建时间：`2026-06-29T14:46:37`
- 摘要：共回放 5/5 个推荐样本，5/6 个指标通过，失败项 5 条。

## 元数据

- `cases_file`: `D:\trae\book_exam\evals\cases\recommendation_cases.json`
- `case_id`: ``
- `limit`: `None`
- `target_count`: `8`
- `total_cases`: `5`
- `completed_cases`: `5`
- `rank_strategy`: `mean`
- `temp_db_policy`: `TemporaryDirectory; never writes cache/attempts.db`
- `top_items`: `{"rec_001": [{"section_id": "3.1", "topic": "任务状态", "p_mastery": 0.0334, "bandit_score": 0.78, "question_types": ["choice", "short_answer"], "reason_text": "掌握概率偏低；样本仅 2 次；主要错因：概念混淆"}, {"section_id": "5.3", "topic": "信号量操作", "p_mastery": 0.7539, "bandit_score": 0.3477, "question_types": ["choice", "short_answer"], "reason_text": "样本仅 1 次"}], "rec_002": [{"section_id": "3.4", "topic": "任务调度", "p_mastery": 0.0784, "bandit_score": 0.753, "question_types": ["short_answer", "comprehensive"], "reason_text": "掌握概率偏低；样本仅 1 次；主要错因：推理错误"}, {"section_id": "5.5", "topic": "消息邮箱", "p_mastery": 0.9891, "bandit_score": 0.2065, "question_types": ["choice", "short_answer"], "reason_text": "样本仅 3 次"}], "rec_003": [{"section_id": "3.4", "topic": "任务调度", "p_mastery": 0.0264, "bandit_score": 0.7841, "question_types": ["short_answer", "comprehensive"], "reason_text": "掌握概率偏低；样本仅 3 次；主要错因：推理错误"}, {"section_id": "4.3", "topic": "任务延时", "p_mastery": 0.7539, "bandit_score": 0.3477, "question_types": ["choice", "short_answer"], "reason_text": "样本仅 1 次"}], "rec_004": [{"section_id": "5.3", "topic": "信号量操作函数", "p_mastery": 0.0334, "bandit_score": 0.78, "question_types": ["choice", "fill_blank"], "reason_text": "掌握概率偏低；样本仅 2 次；主要错因：记忆缺失"}, {"section_id": "5.4", "topic": "优先级反转", "p_mastery": 0.7539, "bandit_score": 0.3477, "question_types": ["choice", "short_answer"], "reason_text": "样本仅 1 次"}], "rec_005": [{"section_id": "7.2", "topic": "创建动态内存分区", "p_mastery": 0.0784, "bandit_score": 0.753, "question_types": ["choice", "short_answer", "comprehensive"], "reason_text": "掌握概率偏低；样本仅 1 次；主要错因：迁移失败"}, {"section_id": "5.6", "topic": "消息队列", "p_mastery": 0.9445, "bandit_score": 0.2333, "question_types": ["choice", "short_answer"], "reason_text": "样本仅 2 次"}]}`

## 指标

| 指标 | 数值 | 阈值 | 结果 | 说明 |
|---|---:|---:|---|---|
| `bkt_monotonic_pass` | 100.00% | >= 100.00% | PASS | 2/2 个 BKT 方向检查通过 |
| `weak_topic_hit_rate` | 100.00% | >= 80.00% | PASS | 5/5 个薄弱章节进入 TopK |
| `mastered_retire_rate` | 0.00% | >= 80.00% | FAIL | 0/5 个低优先级章节退出 TopK |
| `recommendation_reason_rate` | 100.00% | >= 90.00% | PASS | 10/10 个推荐条目包含原因 |
| `error_to_type_match_rate` | 100.00% | >= 80.00% | PASS | 5/5 个推荐题型匹配错因预期 |
| `delta_mastery_valid_rate` | 100.00% | >= 100.00% | PASS | 10/10 个 BKT delta 在合理范围内 |

## 失败明细

| Case | 题目 | 原因 | 证据 |
|---|---|---|---|
| `rec_001` | `5.3.2` | 低优先级章节仍进入推荐 TopK | {"scenario": "连续答错的任务状态应进入推荐 Top3", "top_k": 3, "top_sections": ["3.1", "5.3"]} |
| `rec_002` | `5.5.1` | 低优先级章节仍进入推荐 TopK | {"scenario": "全对序列的 BKT P(L) 应上升，已掌握 topic 应退场", "top_k": 3, "top_sections": ["3.4", "5.5"]} |
| `rec_003` | `4.3.1` | 低优先级章节仍进入推荐 TopK | {"scenario": "全错序列的 BKT P(L) 应下降并被优先推荐", "top_k": 3, "top_sections": ["3.4", "4.3"]} |
| `rec_004` | `5.4.1` | 低优先级章节仍进入推荐 TopK | {"scenario": "主要错因为记忆缺失时，推荐题型应偏 choice/fill_blank", "top_k": 3, "top_sections": ["5.3", "5.4"]} |
| `rec_005` | `5.6.1` | 低优先级章节仍进入推荐 TopK | {"scenario": "样本不足但低掌握的知识点应保留探索机会", "top_k": 3, "top_sections": ["7.2", "5.6"]} |
