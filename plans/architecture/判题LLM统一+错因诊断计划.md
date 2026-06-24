# JudgeGraph 全量 LLM 判题 + 错因诊断 详细设计（v3 — 已实现）

> 本版已通过红蓝对抗审查，所有致命/高风险/中风险缺陷均已修复并入设计。
> 审查报告见 `红蓝审查报告-判题LLM统一方案.md`

## Context

当前 JudgeGraph 判题分两条路径：choice/fill_blank/code_fill 走文本规则（字符串比较），short_answer/comprehensive 走 LLM 语义判定。LLM 只输出 `is_correct + reason`，不诊断错因。错因标签只能通过 CLI 手动录入。

本次改动目标：**所有题型统一产出错因诊断**，消除手动打标签。判对错逻辑保留现有分工（规则判确定性题、LLM 判语义题），错因诊断全部走 LLM。

## 核心设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 判对错 | choice / fill_blank 保留文本规则 | 确定性判定，100% 准确、0 延迟 |
| 判对错 | short_answer / comprehensive / code_fill 走 LLM | code_fill 有缩进/空格/注释差异，文本规则不可靠 |
| 错因诊断 | 全部题型，**只在答错时**调 LLM | 正确的不需要诊错因，节省调用 |
| 并发模型 | 双 Semaphore 隔离：判题 Semaphore(5) + 诊断 Semaphore(2) | 防止诊断任务抢占判题槽位，避免批量错题拖垮判题 |
| 诊断重试 | 诊断 1 次 retry（1s 间隔），判题无重试 | 诊断失败可容忍重试，判题超时直接降级更安全 |
| LLM 输出解析 | 三层兜底：JSON 结构化 → regex 提取 → 精确匹配降级 | 不依赖 `invoke_structured`（其 `_make_fallback` 吞异常），每层都可降级 |
| 输出方式 | error_label 信息并入 answers[i] 字典内部字段 | 不引入独立列表，消除 attempt_id 映射链 |
| 写入路径 | `record_attempts_batch` 内部同连接写 error_labels（try-except 包裹） | server.py 不改动，调用方无感知；error_labels 失败不影响 attempts |
| error_type 类型 | `ErrorTypeEnum` 枚举（str, Enum） | 防止 LLM 输出脏字符串污染画像管线 |
| 错因描述 | `ERROR_TYPE_PROMPT_DESC` 常量 | 两处 prompt（判题+诊断 / 纯诊断）共用，改分类不怕漏改 |
| 前端 | 不改 | 只消费 is_correct/reason/method，error 字段服务端内部消费 |

## 影响范围

| 文件 | 改动 |
|------|------|
| `exam/graph/judge_graph.py` | **完整重写**：ErrorTypeEnum、JudgeResult schema、`_call_llm`（判题+诊断）、`_diagnose_error_llm`（纯诊断）、双 Semaphore 并发、`_strip_label`/`_normalize` 增强、`_make_example` 本地副本 |
| `exam/student_profile/storage.py` | `record_attempts_batch`：INSERT 替代 INSERT OR IGNORE，同连接内联写 error_labels |
| `exam/agents/utils/structured.py` | `_make_example`：bool→false、int→0、float→0.0（不再生成字符串占位符） |
| `exam/agents/utils/agent_states.py` | JudgeState 删 `llm_client` 死字段，注释补 error_* 字段说明 |
| `web/server.py` | **不改** |
| `exam/student_profile/schemas.py` | **不改** |
| `exam/graph/strategy.py` | **不改** |
| `web/quiz.html` | **不改** |

## 详细设计

### 1. 数据结构

#### 1a. ErrorTypeEnum（防止脏数据）

```python
class ErrorTypeEnum(str, Enum):
    concept_confusion = "concept_confusion"   # 概念混淆
    memory_gap = "memory_gap"                 # 记忆缺失
    reasoning_error = "reasoning_error"       # 推理错误
    misread_question = "misread_question"     # 审题错误
    careless = "careless"                     # 粗心失误
    transfer_failure = "transfer_failure"     # 迁移失败
```

#### 1b. JudgeResult（结构化输出 Schema）

```python
class JudgeResult(BaseModel):
    is_correct: bool
    reason: str
    error_type: Optional[ErrorTypeEnum] = None    # 枚举约束，非自由文本
    confidence: Optional[float] = None            # 诊断置信度 0-1，由 LLM 输出
    evidence: Optional[str] = None                # 诊断证据（写入时截断 500 字符）
    suggestion: Optional[str] = None              # 改善建议（写入时截断 500 字符）
```

#### 1c. answers[i] 新增字段（仅答错时有值）

| 字段 | 类型 | 含义 |
|------|------|------|
| `error_type` | str | 错因类型枚举值（如 `"concept_confusion"`） |
| `error_evidence` | str | 诊断证据，截断 500 字符 |
| `error_suggestion` | str | 改善建议，截断 500 字符 |
| `diagnosis_confidence` | float | LLM 输出的诊断置信度 |
| `method` | str | 扩展为 `"rule+llm"`（规则判题 + LLM 诊断） |

### 2. 判题 + 诊断流程

```
_judge_all(state):
  llm_tasks = []          # code_fill / short_answer / comprehensive → 判题+诊断合并
  diagnosis_tasks = []    # choice / fill_blank 答错 → 仅诊断

  for each answer:
    if 未作答:
      → 规则：is_correct=False, reason="未作答", method="rule"

    elif choice:
      → 规则判对错（_strip_label 增强版）
      if 正确: 完成（method="rule"）
      else: → 加入 diagnosis_tasks（带 options 列表）

    elif fill_blank / 其他:
      → 规则判对错（_normalize 增强版）
      if 正确: 完成（method="rule"）
      else: → 加入 diagnosis_tasks

    elif short_answer / comprehensive / code_fill:
      → 加入 llm_tasks（带 options）

  # 并发执行（双 Semaphore 隔离）
  _run_llm_batch(answers, llm_tasks, diagnosis_tasks, llm_client)
```

### 3. LLM 调用细节

#### 3a. 两个 prompt 模板

`_build_judge_prompt`（判题+诊断合并，用于 code_fill/short_answer/comprehensive）：
- 含 `{qtype}`, `{difficulty}`, `{stem}`, `{correct}`, `{given}`, `{expl}`, `{options}`
- 要求：正确时 error_type/confidence/evidence/suggestion 均 null；错误时填写

`_build_diagnosis_prompt`（纯诊断，用于 choice/fill_blank 答错后）：
- 含 `{qtype}`, `{stem}`, `{correct}`, `{given}`, `{expl}`, `{options}`
- is_correct 固定 false，LLM 只诊错因

两个 prompt 共用 `ERROR_TYPE_PROMPT_DESC` 常量（6 类错因描述）。

#### 3b. 三层解析兜底

每次 LLM 调用后：
1. **JSON 结构化解析**：`json.loads` → `JudgeResult(**dict)`，支持 ```json 代码块提取
2. **Regex 兜底**：提取 `true/false`、`error_type` 枚举值、`confidence`/`evidence`/`suggestion` 字段
3. **最终降级**：`is_correct=(given==correct)`，无错因标签

#### 3c. 并发隔离

```
_run_llm_batch:
  judge_sem = Semaphore(5)       # 判题+诊断合并调用
  diagnosis_sem = Semaphore(2)   # 纯诊断调用（独立隔离）

  _judge_one(task):      Semaphore(5) + 30s 超时，无重试
  _diagnose_one(task):   Semaphore(2) + 30s 超时，1 次 retry（1s 间隔）
```

#### 3d. 降级策略

| 场景 | 判题降级 | 诊断降级 |
|------|---------|---------|
| llm_client is None | 简答/综合/code_fill 降级精确匹配 | 跳过所有诊断（不写 error_labels） |
| 单题 LLM 超时（30s） | 降级精确匹配，method="fallback" | 静默跳过，不覆盖已有 is_correct |
| 单题 LLM 异常 | 同上 | 同上 |
| JSON 解析失败 | → regex 兜底 | → regex 兜底 |
| regex 也失败 | → 精确匹配降级 | → 无错因标签 |

### 4. 文本工具增强

#### 4a. `_strip_label`（选择题选项标签剥离）

```python
# 旧：r'^[A-Da-d][.、\s]+'
# 新：支持括号、中文标号、多字母
r'^[\(（\[【]?[A-Da-d一二三四]+[\)）\]】、.．\s]+'
```

示例：`"A."`, `"(B)"`, `"C、"`, `"（D）"`, `"①"`, `"AB"` → 均正确剥离。

#### 4b. `_normalize`（文本归一化）

新增 20+ Unicode 符号映射：`×→x`, `÷→/`, `≥→>=`, `≤→<=`, `≠→!=`, `→→->`, `；→;`, `：→:`, `，→,`, `。→.` 等。

### 5. storage.py 改动

`record_attempts_batch`：
1. `INSERT OR IGNORE` → `INSERT`
2. 每条 INSERT 后 `SELECT last_insert_rowid()` 获取 attempt_id
3. 如果 record 含 `error_type`，同连接写入 `attempt_error_labels`（try-except 包裹，失败不影响 attempts）
4. `confidence` 用 LLM 输出的 `diagnosis_confidence`，fallback 为 0.85
5. `evidence`/`suggestion` 写入时 `[:500]` 截断
6. `source` 固定 `"llm"`

### 6. 红蓝审查修复清单

| 编号 | 问题 | 修法 | 状态 |
|------|------|------|------|
| F1 | 选择题诊断无 options | prompt 增加 `{options}`，`_format_options` 格式化 | ✅ |
| F2 | 诊断并发打爆线程池 | 双 Semaphore 隔离：判题 5 + 诊断 2 | ✅ |
| F3 | error_type 无枚举约束 | `ErrorTypeEnum(str, Enum)` | ✅ |
| H1 | `_strip_label` 脆弱 | 正则增强：括号、中文标号、多字母 | ✅ |
| H2 | `_normalize` 缺数学符号 | 20+ Unicode 符号映射表 | ✅ |
| H3 | code_fill 走精确匹配 | 改走 LLM 判题路径 | ✅ |
| H4 | confidence 硬编码 0.85 | `JudgeResult.confidence` 由 LLM 输出 | ✅ |
| H5 | `_make_fallback` 吞异常 | 三层解析：JSON → regex → 降级 | ✅ |
| H6 | `_make_example` bool 占位符 | bool→false, int→0, float→0.0 | ✅ |
| M1 | 错因描述两处硬编码 | `ERROR_TYPE_PROMPT_DESC` 常量 | ✅ |
| M2 | method/diagnosis_source 混乱 | 统一 "rule+llm" 合并写法 | ✅ |
| M3 | 无诊断重试 | 诊断 1 次 retry（1s 间隔） | ✅ |
| M4 | llm_client None 时诊断无降级 | `_run_llm_batch` 开头检查并跳过 | ✅ |
| M5 | 事务粒度过粗 | error_labels 写入 try-except 包裹 | ✅ |
| M6 | evidence 无长度限制 | 写入时 `[:500]` 截断 | ✅ |
| M7 | JudgeState 死字段 | 删除 `llm_client` | ✅ |

## 验证方法

1. 启动 web server：`python web/server.py`
2. 浏览器打开 `localhost:8080`，用 demo 题目答题：
   - 故意选错 1 道选择题
   - 简答题写一个错误答案
3. 检查 `cache/attempts.db`：
   ```sql
   SELECT id, question_type, is_correct, reason, method FROM attempts ORDER BY id DESC LIMIT 5;
   SELECT * FROM attempt_error_labels ORDER BY id DESC LIMIT 5;
   ```
4. 确认：
   - 选择题正确的 method = "rule"，无 error_label
   - 选择题错误的 method = "rule+llm"，有 error_label 且 source = "llm"
   - code_fill/short_answer/comprehensive method = "llm"，错的有 error_label
5. 跑 `python show_profile.py --student test_demo` 确认画像消费 llm 标签
