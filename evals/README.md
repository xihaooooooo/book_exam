# 离线评测基线

本目录保存 Agent 评测的固定样本和口径。评测分为两类：

- `offline_saved_questions`：读取已有 `output/questions_*.json` 做离线质检，不现场调用大模型。
- `live_llm_generation` / `live_llm_judge`：显式传入真实大模型开关后，使用固定样本现场调用 LLM，再用同一套指标验收。
- `llm_generation_review`：显式传入审稿开关后，使用真实 LLM 作为专家评委，对出题结果做内容质量评分。

默认入口仍保持本地离线降级/离线质检模式，不接入主流程，不写入现有 `cache/attempts.db`。只有显式传入真实大模型开关时才会调用 LLM。

## 目录

```text
evals/
  cases/
    generation_cases.json
    judge_cases.json
    recommendation_cases.json
```

后续阶段会新增：

```text
evals/
  reports/
    eval_YYYYMMDD_HHMMSS.json
    eval_YYYYMMDD_HHMMSS.md
```

## 当前离线 baseline

当前离线 Agent 评测 baseline 固定为 `20260629_151029`：

- 总分：`90.48%`
- 出题质量：`100.00%`
- 判题一致性：`71.43%`
- 推荐策略：`100.00%`
- 失败项：`2`

基线指针文件：

- `evals/reports/baseline.json`
- `evals/reports/baseline.md`

完整报告：

- `evals/reports/agent_eval_20260629_151029.json`
- `evals/reports/agent_eval_20260629_151029.md`

这份 baseline 用作 Prompt、模型、判题规则或推荐策略改动后的本地离线回归对比锚点。剩余 2 个失败项均为无 LLM 模式下主观题/综合题语义等价的规则判题限制。

## generation_cases.json

用于出题质量评测。第一版固定 20 个章节样本，覆盖概念、数据结构、API、算法流程、通信、内存等考试高频点。

字段约定：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定样本编号 |
| `section_id` | 对应 `cache/sections.db` 中 `sections.id` |
| `section_title` | 章节标题，仅作报告展示 |
| `topic_hint` | 出题主题提示 |
| `mode` | 出题模式，第一版固定为 `exam` |
| `target_count` | 该 case 期望生成题数 |
| `allowed_types` | 允许题型：`choice`、`fill_blank`、`short_answer`、`code_fill`、`comprehensive` |
| `allowed_difficulty` | 允许难度：`easy`、`medium`、`hard` |
| `expected_keywords` | 教材依据或主题命中关键词 |
| `coverage_tag` | 覆盖类型标签，用于报告分组 |

Phase 1 MVP 优先评测自动指标：

- `format_pass_rate`
- `type_adherence_rate`
- `difficulty_adherence_rate`
- `answer_presence_rate`
- `explanation_presence_rate`
- `duplicate_rate`

启用 LLM 专家审稿后，会追加以下指标：

- `llm_review_pass_rate`
- `llm_review_average_score`
- `relevance_pass_rate`
- `correctness_pass_rate`
- `difficulty_fit_pass_rate`

结构质检只判断题目 JSON 是否完整、题型/难度是否满足约束、答案解析是否存在、题干是否重复；LLM 专家审稿会进一步判断题目是否贴合章节、答案是否正确、解析是否有教学价值、难度是否合理。建议审稿模型与出题模型分开，例如出题使用 `BOOKTOEXAM_DEEP_THINK_LLM`，审稿使用 `BOOKTOEXAM_REVIEW_LLM`。如果未设置 `BOOKTOEXAM_REVIEW_LLM`，审稿会回退到当前评测 LLM 配置。

## judge_cases.json

用于判题一致性评测。样本覆盖客观题规则判题、主观题 LLM 判题、空答案、大小写、空白和 LaTeX 归一化边界。

字段约定：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定样本编号 |
| `question_type` | 题型 |
| `stem` | 题干 |
| `options` | 选择题选项，非选择题可省略 |
| `correct_answer` | 标准答案 |
| `student_answer` | 学生答案 |
| `expected_correct` | 期望判题结果 |
| `expected_error_type` | 期望主要错因，正确样本为 `null` |
| `accepted_error_types` | 可接受错因集合，用于避免语义诊断过窄 |
| `judge_path` | 期望覆盖路径：`rule`、`rule+diagnosis`、`llm` |
| `notes` | 人工说明 |

合法错因枚举与 `exam.student_profile.schemas.ERROR_TYPES` 保持一致：

- `concept_confusion`
- `memory_gap`
- `reasoning_error`
- `misread_question`
- `careless`
- `transfer_failure`

## recommendation_cases.json

用于推荐策略离线回放。样本使用合成 attempts，后续实现必须写入临时数据库或内存结构，不能污染 `cache/attempts.db`。

字段约定：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定样本编号 |
| `student_id` | 评测学生编号 |
| `scenario` | 样本意图 |
| `top_k` | 推荐命中检查范围 |
| `attempts` | 合成作答记录 |
| `expected_bkt_direction` | 可选，期望 BKT P(L) 方向：`increase` 或 `decrease` |
| `expected_top_sections` | 期望进入 TopK 的薄弱章节 |
| `expected_low_priority_sections` | 期望降低优先级或退出 TopK 的章节 |
| `expected_question_types` | 按错因期望推荐出的题型集合 |

推荐评测必须使用稳定排序：

```python
build_recommendation_plan(..., rank_strategy="mean")
```

不要在离线评测里使用 Thompson Sampling 随机采样排序。随机策略可留到后续 Monte Carlo 版本。

## 第一版不做

- 不接入 CI。
- 当前 baseline 默认不引入 LLM-as-judge。
- 不跑大规模样本。
- 不修改 Web 出题、答题、画像流程。
- 不使用真实学生数据作为评测输入。

## 运行入口

出题质量评测：

```powershell
python scripts/run_generation_eval.py --use-latest-output --limit 5
```

上面的命令只检查已有题目文件，不现场出题。使用真实大模型现场出题并立即质检：

```powershell
$env:DEEPSEEK_API_KEY="你的 key"
$env:BOOKTOEXAM_LLM_PROVIDER="deepseek"
$env:BOOKTOEXAM_DEEP_THINK_LLM="deepseek-v4-flash"
python scripts/run_generation_eval.py --generate-with-llm --live-max-questions 5
```

真实出题评测会走完整 `ExamGraph` 出题链路，生成文件写入 `evals/generated/`，不会污染正式 `output/` 题库。

对已有题目文件追加 LLM 专家审稿：

```powershell
$env:DEEPSEEK_API_KEY="你的 key"
$env:BOOKTOEXAM_LLM_PROVIDER="deepseek"
$env:BOOKTOEXAM_REVIEW_LLM="deepseek-v4-pro"
python scripts/run_generation_eval.py --use-latest-output --limit 5 --llm-review
```

真实出题后立即追加 LLM 专家审稿：

```powershell
$env:DEEPSEEK_API_KEY="你的 key"
$env:BOOKTOEXAM_LLM_PROVIDER="deepseek"
$env:BOOKTOEXAM_DEEP_THINK_LLM="deepseek-v4-flash"
$env:BOOKTOEXAM_REVIEW_LLM="deepseek-v4-pro"
python scripts/run_generation_eval.py --generate-with-llm --live-max-questions 5 --llm-review
```

判题一致性评测：

```powershell
python scripts/run_judge_eval.py
```

使用真实大模型跑判题一致性评测：

```powershell
$env:DEEPSEEK_API_KEY="你的 key"
$env:BOOKTOEXAM_LLM_PROVIDER="deepseek"
$env:BOOKTOEXAM_DEEP_THINK_LLM="deepseek-v4-flash"
python scripts/run_judge_eval.py --use-llm
```

推荐策略评测：

```powershell
python scripts/run_recommendation_eval.py
```

完整 Agent 离线评测：

```powershell
python scripts/run_all_evals.py --generation-limit 5
```

完整评测中只给判题环节接入真实大模型：

```powershell
python scripts/run_all_evals.py --generation-limit 5 --use-llm-judge
```

完整评测中同时启用真实出题和真实判题：

```powershell
python scripts/run_all_evals.py --generation-limit 5 --generate-with-llm --use-llm-judge
```

完整评测中同时启用真实出题、出题专家审稿和真实判题：

```powershell
python scripts/run_all_evals.py --generation-limit 5 --generate-with-llm --llm-review-generation --use-llm-judge
```

完整评测会顺序运行出题、判题、推荐三类任务，输出各自 JSON/Markdown 报告，再生成 `agent_eval_*.json`、`agent_eval_*.md` 和 `evals/reports/index.json`。`index.json` 会记录历史运行和指标 diff，用于 Prompt、模型或策略改动后的回归对比。

不带 `--generate-with-llm`、`--llm-review`、`--llm-review-generation`、`--use-llm` 或 `--use-llm-judge` 时，评测仍保持本地离线降级/离线质检模式。启用真实大模型后，样本和报告仍在本地，但对应环节会联网调用 API，结果可能受模型输出波动影响。

Baseline 对比：

```powershell
python scripts/compare_eval_baseline.py --latest
```

如需对比指定报告：

```powershell
python scripts/compare_eval_baseline.py --report evals/reports/agent_eval_YYYYMMDD_HHMMSS.json
```

对比器只读取 `evals/reports/baseline.json`、`evals/reports/index.json` 和目标报告，不会重新运行评测。需要在自动化场景中发现回归时返回非零退出码，可追加 `--fail-on-regression`。
