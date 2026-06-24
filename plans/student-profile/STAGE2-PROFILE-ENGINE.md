# 阶段 2：画像聚合

## 1. 背景

阶段 1 已完成：attempts 表 + attempt_error_labels 表 + 判题管道 + CLI。数据在库里，但散的——每次作答一条记录，看不出学生整体状态。

## 2. 目标

从 attempts 和 error_labels 中实时聚合学生画像，回答：

- 学生各知识点的掌握等级是什么？
- 薄弱知识点 Top 5？
- 主要错因是什么？
- 存在哪些风险信号（伪掌握、连续错误等）？

## 3. 核心设计

不做物化表。每次查画像时从 attempts + error_labels 实时聚合。原因：单学生数据量几十到几百条，SQL 聚合毫秒级够用，省掉同步逻辑。

## 4. 掌握等级（5 档）

| 等级 | 判定规则 |
|------|----------|
| `unknown` | total_attempts < 2 |
| `weak` | recent_accuracy < 0.5，或连续错误 ≥ 2 |
| `unstable` | recent_accuracy 0.5 ~ 0.75 |
| `familiar` | recent_accuracy ≥ 0.75，且 (avg_duration > 同类题平均的 1.3 倍 或 avg_confidence < 3.5) |
| `mastered` | total_attempts ≥ 5，recent_accuracy ≥ 0.85，avg_confidence ≥ 4，且最近无连续错误 |

recent_accuracy = 最近 10 次作答的正确率（不足 10 次用全部）。

## 5. 风险信号

| 信号 | 条件 |
|------|------|
| 高信心错误 | confidence ≥ 4 且 is_correct=0 |
| 慢速正确 | is_correct=1 但 duration_sec 超过该生同类题平均的 1.5 倍 |
| 连续错误 | 同一 topic 按时间排序，最近连续错误 ≥ 2 次 |
| 持续低信心 | 最近 5 次作答 avg_confidence < 3 |

## 6. 画像输出示例

```
学生 S001 当前画像

掌握概况：
  mastered:  2 个知识点
  familiar:  5 个知识点
  unstable:  3 个知识点
  weak:      2 个知识点
  unknown:   8 个知识点（作答不足）

最弱知识点：
  1. 2.3 任务调度 — weak（正确率 25%，连续错误 3 次）
     主要错因: reasoning_error
  2. 3.1 临界区互斥 — unstable（正确率 60%）
     主要错因: concept_confusion

错因分布：
  reasoning_error:   45%
  concept_confusion: 30%
  careless:          15%
  memory_gap:        10%

风险信号：
  ⚠️ 任务调度: 连续错误 3 次，含 2 次高信心错误（伪掌握风险）
  ⚠️ 临界区互斥: 慢速正确 2 次（会但不熟）
```

## 7. 模块结构

`exam/student_profile/profile_engine.py`：

```python
# 核心函数
def build_profile(student_id, db_path) -> StudentProfile
def compute_mastery(section_id, topic, attempts) -> str
def detect_risk_signals(attempts) -> list[RiskSignal]
def compute_error_distribution(student_id, db_path) -> dict
def get_weakest_topics(profile, top_n=5) -> list

# 数据结构
@dataclass
class TopicStat:
    section_id: str
    topic: str
    total_attempts: int
    wrong_count: int
    accuracy: float
    recent_accuracy: float
    avg_duration_sec: float
    avg_confidence: float
    dominant_error_type: str
    streak_wrong: int
    mastery_level: str

@dataclass
class StudentProfile:
    student_id: str
    topics: list[TopicStat]
    error_distribution: dict[str, float]
    risk_signals: list[str]
```

## 8. CLI 设计

`show_profile.py`：

```bash
# 查看学生画像
python show_profile.py --student S001

# 只看薄弱点
python show_profile.py --student S001 --weak-only

# 输出 JSON（供 generate.py 读取）
python show_profile.py --student S001 --json
```

## 9. 与现有代码的关系

- 只读 `attempts.db` 和 `attempt_error_labels` 表，不写
- 复用 `schemas.py` 的 ERROR_TYPE_LABELS、ERROR_PRIORITY
- `dominent_error_type` 按次数取最多，平局时按 ERROR_PRIORITY 优先
- `recent` 定义为最近 10 条记录（按 created_at 降序）

## 10. 已知局限

- **网页学生的错因数据为空**：当前只有 CLI `record_attempt.py` 会写入 `attempt_error_labels`，网页交卷只写 attempts。对于纯网页答题的学生，画像中的 `dominent_error_type` 和错因分布将显示"数据不足"。阶段 5（LLM 错因诊断）上线后自动补齐。
- **`get_weak_sections()` 待替换**：当前 `agent_utils.py` 的 `get_weak_sections()` 读的是 mistakes.db。阶段 4 将该函数内部改为读 attempts + profile_engine 的聚合结果，两个"弱点"概念合一。

## 11. 暂不包含

- LLM 错因诊断（阶段 5）
- 练习推荐（阶段 3）
- 班级群体对比
- 学习曲线预测

## 12. 实现顺序

```
Step 1: profile_engine.py — TopicStat + StudentProfile 数据结构 + build_profile()
Step 2: show_profile.py — CLI 入口
```
