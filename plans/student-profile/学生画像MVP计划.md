# Student Profile MVP Plan

## 1. 背景与目标

当前项目已经具备教材解析、自动出题、历年试卷分析、错题录入和 `practice` 模式等基础能力。下一步如果只继续提升通用出题能力，容易被通用 Agent 替代；更有独特性的方向是建立学生长期画像，让系统能够回答：

- 学生最近薄弱在哪里
- 学生为什么会错
- 下一轮最应该练什么

本计划的目标是设计并落地 `StudentProfile v0.1`，把现有错题库思路升级为“作答记录 + 错因诊断 + 画像聚合 + 个性化练习推荐”的闭环。

## 2. MVP 边界

第一版只做能支撑 `practice` 模式的最小闭环，不做复杂知识图谱、群体对比和长期预测。

必须包含：

- 每次作答的结构化记录
- 固定错因分类
- 学生在章节/考点维度的掌握状态
- 下一轮练习推荐计划
- `generate.py --mode practice` 能读取画像推荐

暂不包含：

- 精细知识图谱依赖推理
- 班级群体平均水平对比
- 长期学习曲线预测
- 多轮对话式 AI 教练
- 高精度相似题迁移模型

## 3. 核心闭环

目标闭环：

```text
学生作答
  -> 记录 attempt
  -> 标注/诊断错因
  -> 聚合 student profile
  -> 生成 practice plan
  -> 下一轮 practice 按画像出题
```

这个闭环的重点不是展示画像，而是让画像真正影响下一次出题策略。

## 4. 数据模型设计

### 4.1 attempts

记录每一次作答事实，作为学生画像的事实来源。

建议字段：

```text
id INTEGER PRIMARY KEY
student_id TEXT NOT NULL
question_id TEXT
section_id TEXT
topic TEXT
question_type TEXT
difficulty TEXT
stem TEXT
student_answer TEXT
correct_answer TEXT
is_correct INTEGER
score REAL
max_score REAL
duration_sec INTEGER
confidence INTEGER
changed_answer INTEGER
created_at TEXT DEFAULT (datetime('now'))
```

字段说明：

- `confidence` 使用 1-5 分，表示学生自评把握度。
- `changed_answer` 表示是否修改过答案，可作为犹豫的早期代理指标。
- `duration_sec` 用于区分“不熟练”和“不会”。

### 4.2 attempt_error_labels

记录一次错误的错因标签。错因可以来自学生、老师、人工录入或 LLM。

建议字段：

```text
id INTEGER PRIMARY KEY
attempt_id INTEGER NOT NULL
error_type TEXT NOT NULL
confidence REAL
source TEXT
evidence TEXT
suggestion TEXT
created_at TEXT DEFAULT (datetime('now'))
```

字段说明：

- `source` 可选值：`student`、`teacher`、`manual`、`llm`。
- 同一次 attempt 可以允许多条标签，但 MVP 阶段优先使用置信度最高的一条作为主错因。

### 4.3 student_skill_stats

存储学生在章节/考点维度的聚合画像。

建议字段：

```text
student_id TEXT NOT NULL
section_id TEXT NOT NULL
topic TEXT
attempt_count INTEGER
wrong_count INTEGER
accuracy REAL
recent_accuracy REAL
avg_duration_sec REAL
avg_confidence REAL
dominant_error_type TEXT
streak_wrong INTEGER
mastery_level TEXT
needs_review INTEGER
updated_at TEXT DEFAULT (datetime('now'))
PRIMARY KEY (student_id, section_id, topic)
```

### 4.4 practice_plans

保存下一轮练习建议，供 `practice` 模式读取。

建议字段：

```text
id INTEGER PRIMARY KEY
student_id TEXT NOT NULL
focus_sections TEXT
focus_topics TEXT
question_types TEXT
difficulty TEXT
target_count INTEGER
reason TEXT
created_at TEXT DEFAULT (datetime('now'))
used_at TEXT
```

字段说明：

- `focus_sections`、`focus_topics`、`question_types` 可先用 JSON 字符串保存。
- `used_at` 用于标记该计划是否已经被一次 practice 使用。

## 5. 模块拆分

建议新增目录：

```text
exam/student_model/
```

建议模块：

```text
schemas.py
attempt_recorder.py
diagnosis_agent.py
profile_engine.py
recommender.py
storage.py
```

模块职责：

- `schemas.py`：定义作答记录、错因标签、画像统计、练习计划的数据结构。
- `storage.py`：负责建表、读写 attempts、error labels、skill stats、practice plans。
- `attempt_recorder.py`：对外提供记录作答的入口。
- `diagnosis_agent.py`：可选 LLM 错因诊断，只做辅助判断。
- `profile_engine.py`：用规则和统计聚合学生画像。
- `recommender.py`：根据画像生成下一轮练习计划。

## 6. 错因分类体系

MVP 固定 6 类错因：

```text
concept_confusion
memory_gap
reasoning_error
misread_question
careless
transfer_failure
```

分类说明：

- `concept_confusion`：概念混淆，把两个相近概念、条件、流程混在一起。
- `memory_gap`：记忆缺失，定义、步骤、条件、公式或关键事实记不住。
- `reasoning_error`：推理错误，知道概念但推理链、计算过程或步骤展开错误。
- `misread_question`：审题错误，漏看限制条件、否定词、问法或题干关键词。
- `careless`：粗心失误，知识上会，但抄错、算错、选错或提交失误。
- `transfer_failure`：迁移失败，单点题会，换题型、换场景、综合应用就不会。

## 7. 错因诊断 Agent 设计

错因诊断 Agent 只负责辅助判断“这次为什么错”，不直接维护画像。

输入：

```json
{
  "stem": "题干",
  "student_answer": "学生答案",
  "correct_answer": "标准答案",
  "explanation": "解析",
  "section_id": "2.3",
  "topic": "任务调度",
  "question_type": "choice",
  "difficulty": "medium",
  "duration_sec": 95,
  "confidence": 5
}
```

输出：

```json
{
  "error_type": "concept_confusion",
  "confidence": 0.82,
  "evidence": "学生把任务状态切换条件和调度触发条件混在一起。",
  "suggestion": "先练任务状态转换与调度触发条件的辨析题。"
}
```

约束：

- 只能输出固定错因类型之一。
- 必须给出简短证据。
- 置信度低于阈值时，允许输出 `unknown` 或回退为人工标注。
- Agent 输出写入 `attempt_error_labels`，画像聚合仍由 `profile_engine.py` 完成。

## 8. 学生画像计算规则

### 8.1 掌握等级

建议先使用 5 档：

```text
unknown
weak
unstable
familiar
mastered
```

初版规则：

- `unknown`：attempt 数少于 2。
- `weak`：recent accuracy 低于 0.5，或连续错误不少于 2 次。
- `unstable`：recent accuracy 在 0.5 到 0.75 之间，或高信心错误较多。
- `familiar`：recent accuracy 不低于 0.75，但耗时偏长或信心偏低。
- `mastered`：recent accuracy 不低于 0.85，平均信心较高，且最近没有连续错误。

### 8.2 风险信号

建议识别以下信号：

- 高信心错误：`confidence >= 4` 且作答错误，说明存在伪掌握风险。
- 慢速正确：答对但 `duration_sec` 高于该学生同类题平均，说明会但不熟。
- 快速错误：耗时很短但错误，结合错因判断可能是粗心或审题问题。
- 连续错误：同一 section/topic 连续错 2 次以上，进入重点复练。
- 迁移失败：基础题正确但综合题错误，或同知识点换题型后错误。

### 8.3 主要错因

每个学生、每个知识点统计错因频次，选择频次最高且置信度足够的错因作为 `dominant_error_type`。

如果错因分布接近，优先级建议为：

```text
concept_confusion
reasoning_error
transfer_failure
memory_gap
misread_question
careless
```

优先级越靠前，越应该进入下一轮系统性训练。

## 9. 下一轮练习推荐策略

`recommender.py` 根据 `student_skill_stats` 生成 `practice_plan`。

推荐规则：

- 优先选择 `mastery_level` 为 `weak` 的知识点。
- 其次选择 `unstable` 且最近练习时间较早的知识点。
- 每轮保留少量 `familiar` 知识点做保持性复习。
- 如果主要错因是 `memory_gap`，优先选择 easy、choice、blank。
- 如果主要错因是 `concept_confusion`，优先选择 choice、short_answer，加入辨析题。
- 如果主要错因是 `reasoning_error`，优先选择 short_answer、comprehensive，从 easy 到 medium。
- 如果主要错因是 `misread_question`，优先选择带限制条件和反向问法的选择题。
- 如果主要错因是 `careless`，控制难度，加入限时和复核提示。
- 如果主要错因是 `transfer_failure`，加入同知识点不同题型或小综合题。

practice plan 示例：

```json
{
  "student_id": "S001",
  "focus_sections": ["2.3", "3.1"],
  "focus_topics": ["任务调度", "临界区互斥"],
  "question_types": ["choice", "short_answer"],
  "difficulty": "easy_to_medium",
  "target_count": 8,
  "reason": "任务调度连续错误 3 次，主要错因为 reasoning_error；临界区互斥 recent accuracy 偏低。"
}
```

## 10. 与现有 practice 模式集成

当前 `practice` 模式主要依赖错题库弱点章节。升级后建议采用兼容策略：

```text
优先读取 student profile 最新 practice plan
  -> 如果存在可用 plan，使用其中 focus/count/types/difficulty
  -> 如果没有 plan，回退到现有 mistakes.db 的 weak sections
  -> 如果仍无数据，回退到 exam 模式或提示先做诊断练习
```

建议改造点：

- 在 `generate.py` 的 `derive_params()` 中增加读取 practice plan 的逻辑。
- 新增 `get_latest_practice_plan(student_id)` 工具函数。
- 保留现有 `get_weak_sections(student_id)` 作为兼容回退。
- 后续可把 `difficulty` 也传入 `ExamGraph`，用于更精确控制任务规划。

## 11. 开发阶段

### 阶段 1：结构化记录

目标：

- 建立 attempts 和 attempt_error_labels 表。
- 提供命令行录入一次作答。
- 支持手动传入错因类型。

建议新增：

```text
record_attempt.py
exam/student_model/storage.py
exam/student_model/attempt_recorder.py
exam/student_model/schemas.py
```

验收：

- 能录入一次作答。
- 能录入错因标签。
- 数据能在 SQLite 中查询到。

### 阶段 2：画像聚合

目标：

- 根据 attempts 聚合 `student_skill_stats`。
- 输出学生画像摘要。

建议新增：

```text
show_profile.py
exam/student_model/profile_engine.py
```

验收：

- 能看到薄弱知识点 Top 5。
- 能看到主要错因分布。
- 能看到每个知识点的 mastery level。

### 阶段 3：练习推荐

目标：

- 根据画像生成下一轮 practice plan。
- 保存到 `practice_plans`。

建议新增：

```text
exam/student_model/recommender.py
```

验收：

- 能生成 focus sections。
- 能生成 question types。
- 能生成 target count。
- 能解释推荐原因。

### 阶段 4：接入 practice 模式

目标：

- `generate.py --mode practice --student S001` 优先读取 practice plan。
- 没有画像时保持原有错题库逻辑。

验收：

- 有 practice plan 时按画像推荐出题。
- 无 practice plan 时不破坏现有行为。

### 阶段 5：错因诊断 Agent

目标：

- 对错误作答自动给出候选错因。
- 人工仍可覆盖。

建议新增：

```text
exam/student_model/diagnosis_agent.py
```

验收：

- Agent 输出固定错因类型。
- 输出包含置信度、证据和建议。
- 低置信度时不强行覆盖人工标签。

## 12. 命令行设计草案

记录一次作答：

```bash
python record_attempt.py --student S001 --section 2.3 --topic 任务调度 --type choice --difficulty easy --correct false --duration 80 --confidence 5 --error concept_confusion
```

查看学生画像：

```bash
python show_profile.py --student S001
```

生成下一轮练习计划：

```bash
python show_profile.py --student S001 --recommend
```

按画像出题：

```bash
python generate.py --mode practice --student S001
```

## 13. 画像输出示例

```text
学生 S001 当前画像

最弱知识点：
1. 2.3 任务调度：weak，recent accuracy 0.33，主要错因 reasoning_error
2. 3.1 临界区互斥：unstable，recent accuracy 0.60，主要错因 concept_confusion

主要错因：
- reasoning_error: 40%
- concept_confusion: 30%
- misread_question: 15%

风险提示：
- 任务调度出现连续错误，且有高信心错误，存在伪掌握风险。
- 临界区互斥在选择题中表现尚可，但短答题不稳定。

下一轮建议：
- focus: 2.3, 3.1
- question types: choice, short_answer
- difficulty: easy_to_medium
- target count: 8
- reason: 先补任务调度推理链，再用临界区互斥辨析题巩固概念边界。
```

## 14. 验收标准

MVP 完成后，系统至少应满足：

- 能记录一次学生作答，而不只是记录错题。
- 能标注或自动诊断一次错误的错因。
- 能按学生、章节、考点聚合正确率、近期正确率、连续错误和主要错因。
- 能输出学生薄弱知识点、主要错因和下一轮练习建议。
- `practice` 模式能优先使用学生画像推荐，并保留旧错题库回退逻辑。

## 15. 后续演进

MVP 稳定后可继续扩展：

- 引入题目相似度与变体关系，评估迁移能力。
- 引入知识点前置依赖，定位更深层的基础缺口。
- 引入班级群体数据，判断学生是个体问题还是普遍问题。
- 引入练习后复测机制，验证推荐是否真正提升掌握度。
- 引入教师确认反馈，让错因诊断 Agent 逐步贴近真实教学判断。

