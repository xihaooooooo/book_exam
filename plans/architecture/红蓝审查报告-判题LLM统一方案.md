# 红蓝对抗审查报告：JudgeGraph LLM 统一 + 错因诊断方案

> 审查目标：`判题LLM统一+错因诊断计划.md`（v2）
> 红方（攻击者）25 条缺陷 | 蓝方（防守者）28 条预判 + 修法

---

## 🔴 致命缺陷（3 条 — 上线即无法正常工作）

### F1. 选择题诊断 prompt 无 options → LLM 看的只是无意义字母

**现象**：选择题的 `correct_answer` 是 `"C"`，`student_answer` 是 `"A"`。`_diagnose_error_llm` 的 prompt 不传 options 列表，LLM 只知道两个字母，不知道每个选项的文本内容。

**原因**：`_judge_all` 构造 diagnosis_tasks 时未传 `options` 字段，prompt 模板也没有占位符。

**影响**：所有选择题的错因诊断全是盲猜，画像系统基于这些标签推荐练习策略会误导学生。

**修法**：
- diagnosis task tuple 增加 `options` 字段
- `_diagnose_error_llm` prompt 增加 `选项：{formatted_options}\n`
- 格式化：`"A. 进程管理\nB. 内存管理\nC. 编译程序\nD. 文件管理"`

**优先级：致命** | **改动量：+8 行** | **文件：judge_graph.py**

---

### F2. 诊断并发打爆线程池 → 判题也被拖垮

**现象**：50 道题考试，10 道简答 + 40 道选择，假设全错 = 50 个 LLM 调用共享 Semaphore(5)。简答判题和选择题诊断混在一起排队，线程池可能耗尽。

**原因**：计划把 diagnosis_tasks 和 llm_tasks（判题）合进同一个 asyncio.gather，共享 Semaphore(5)。诊断任务抢占判题任务的并发槽位。

**影响**：线上 30 道选择考试，学生答错一半 → 15 道诊断 + 5 道简答判题竞争 5 个槽位 → 整体延迟翻倍 → 超时 → 全部降级，非但没拿到错因诊断，LLM 判题也崩了。

**修法**（推荐）：诊断使用独立的 Semaphore(2)，与判题 Semaphore(5) 隔离。或者诊断任务在判题完成后再启动。

**优先级：致命** | **改动量：+15 行** | **文件：judge_graph.py**

---

### F3. `error_type` 无枚举约束 → LLM 输出脏数据静默污染画像

**现象**：`JudgeResult.error_type` 声明为 `Optional[str]`，不是 `Literal` 或枚举。LLM 可能输出 `"wrong_concept"`、`"概念错误"`、`"不理解"` 等不在 6 类内的字符串。

**原因**：Schema 只用 Field description 说明可选值，Pydantic 不强制校验。

**影响**：异常值写入 `attempt_error_labels`，下游 `profile_engine.py` 的 `ERROR_PRIORITY`、`ERROR_TYPE_LABELS` 全都不认识 → 错因分布为空、风险检测失效。**静默数据损坏，画像聚合无声崩溃。**

**修法**：
```python
from enum import Enum
class ErrorTypeEnum(str, Enum):
    concept_confusion = "concept_confusion"
    memory_gap = "memory_gap"
    reasoning_error = "reasoning_error"
    misread_question = "misread_question"
    careless = "careless"
    transfer_failure = "transfer_failure"

class JudgeResult(BaseModel):
    error_type: Optional[ErrorTypeEnum] = Field(default=None, ...)
```

**优先级：致命** | **改动量：+15 行** | **文件：judge_graph.py**

---

## 🟠 高风险（6 条 — 特定场景下功能不正确）

### H1. `_strip_label` 正则只匹配 A-D → 多选题/特殊标号全判错

- **修法**：增强正则为 `r'^[\(（\[【]?[A-Da-d一二三四]+[\)）\]】、.．\s]+'`
- **改动量**：1 行 | **文件**：judge_graph.py

### H2. `_normalize` 缺数学符号映射 → code_fill/公式填空全半角差异必判错

- **修法**：增加 Unicode 符号映射字典 `{"×":"x", "÷":"/", "≥":">=", "≤":"<=", ...}`
- **改动量**：+10 行 | **文件**：judge_graph.py

### H3. code_fill 走精确匹配 → 代码缩进/空格/注释差异必判错

- **修法**：code_fill 改走 LLM 判题路径（与 short_answer 一致）
- **改动量**：+10 行 | **文件**：judge_graph.py

### H4. 硬编码 confidence=0.85 → 诊断质量无法区分

- **修法**：`JudgeResult` 增加 `confidence: Optional[float]` 字段，prompt 要求 LLM 输出诊断置信度
- **改动量**：+7 行 | **文件**：judge_graph.py + storage.py

### H5. `_make_fallback` 吞掉异常 → "regex 再提取"路径永远不执行

- **现象**：`invoke_structured` 解析失败时返回 `_make_fallback()` 实例（`is_correct=False, error_type=None`），不抛异常。regex 兜底路径成了死代码。
- **修法**：在 `_call_llm` 中调用 `invoke_structured` 后检查是否是 fallback（如检查 `reason == ""`），若是则走 regex 提取，regex 也失败才降级
- **改动量**：+5 行 | **文件**：judge_graph.py

### H6. `_make_example` 对 bool 字段生成字符串占位符 → LLM 被误导输出非法 JSON

- **现象**：`_make_example` 对 `bool` 类型的 `is_correct` 字段生成了 `"<答案是否正确>"`（字符串），而非 `true`/`false`
- **修法**：`_make_example` 中增加对 `bool`/`int`/`float` 类型的类型匹配示例值生成
- **改动量**：+8 行 | **文件**：structured.py

---

## 🟡 中等风险（7 条 — 可用性和健壮性问题）

### M1. 选择题和简答题的 6 类错因描述在两处 prompt 硬编码 → 未来改分类必漏改

- **修法**：提取 `ERROR_TYPE_PROMPT_DESC` 常量，两处 prompt 共用
- **改动量**：+10 行 | **文件**：judge_graph.py

### M2. diagnosis_source 字段混乱 → method / diagnosis_source / error_labels.source 三处表意

- **修法**：method 字段扩展为 `"rule+llm"` 表示"规则判题 + LLM 诊断"，删掉 diagnosis_source，error_labels.source 从 method 派生
- **改动量**：+4 行 | **文件**：judge_graph.py + storage.py

### M3. 无诊断重试 → 网络抖动一次诊断数据永久丢失

- **修法**：诊断 LLM 调用加 1 次 retry（1s 后重试），判题调用保留现有无重试逻辑
- **改动量**：+10 行 | **文件**：judge_graph.py

### M4. `_judge_all` 当 llm_client is None 时 diagnosis_tasks 无降级处理

- **修法**：`_judge_all` 开头加 `if llm_client is None: 跳过 diagnosis_tasks 构建`
- **改动量**：+3 行 | **文件**：judge_graph.py

### M5. record_attempts_batch 事务粒度过粗 → error_labels 写入失败会回滚 attempts

- **修法**：分两阶段——先提交 attempts，再独立写 error_labels
- **改动量**：+20 行 | **文件**：storage.py

### M6. evidence/suggestion 字段无长度限制 → LLM 可能输出数百字导致 UI 溢出

- **修法**：写入时 `[:500]` 截断
- **改动量**：+3 行 | **文件**：storage.py

### M7. JudgeState 的 llm_client 字段从不用 → 死字段

- **修法**：删除 JudgeState.llm_client
- **改动量**：-1 行 | **文件**：agent_states.py

---

## 🔵 低风险（5 条 — 可后续迭代）

| # | 问题 | 修法 | 文件 | 行数 |
|---|------|------|------|------|
| L1 | 选择题全错时 LLM 调用量过大（50 道 = 50 次诊断） | 后续考虑批量诊断 prompt（3-5 道错题合批） | judge_graph.py | future |
| L2 | 前端不展示错因诊断结果 | quiz.html 加错因展示 | quiz.html | future |
| L3 | `_make_fallback` 有 verdict/issues 硬编码特判 | 特判逻辑移到调用方 | structured.py | +8 |
| L4 | 缺少幂等键 → 重复提交写入重复记录 | 后续加 (student_id, question_id) UNIQUE 约束 | storage.py | future |
| L5 | 考前无"`_diagnose_error_llm` prompt 需要 options"的验证 | 加单元测试或 CI 检查 | tests | future |

---

## 📊 综合评估

| 维度 | 评级 | 说明 |
|------|------|------|
| 方案方向 | ✅ 正确 | 错因诊断并入 JudgeGraph、不另建 Agent |
| 架构耦合 | ✅ 控制好 | server.py 不改、schemas.py 不改、前端不改 |
| 致命缺陷 | 🔴 3 条 | F1(options)/F2(并发)/F3(枚举) 不修不能上线 |
| 高风险 | 🟠 6 条 | 边界场景下功能不正确 |
| 中风险 | 🟡 7 条 | 健壮性和可维护性 |
| 低风险 | 🔵 5 条 | 可后续迭代 |

---

## 🛠️ 分批执行建议

### 第一批：堵致命缺陷（~38 行）

| # | 修复项 | 文件 | 行数 |
|---|--------|------|------|
| F1 | 选择题诊断 prompt 传入 options | judge_graph.py | +8 |
| F2 | 诊断独立 Semaphore(2) 隔离 | judge_graph.py | +15 |
| F3 | error_type 改用枚举 | judge_graph.py | +15 |

### 第二批：修高风险（~41 行）

| # | 修复项 | 文件 | 行数 |
|---|--------|------|------|
| H1 | `_strip_label` 正则增强 | judge_graph.py | +1 |
| H2 | `_normalize` 数学符号映射 | judge_graph.py | +10 |
| H3 | code_fill 改走 LLM | judge_graph.py | +10 |
| H4 | confidence 由 LLM 输出 | judge_graph.py + storage.py | +7 |
| H5 | fallback 后走 regex 兜底 | judge_graph.py | +5 |
| H6 | `_make_example` bool 类型修复 | structured.py | +8 |

### 第三批：补中等风险（~50 行）

| # | 修复项 | 文件 | 行数 |
|---|--------|------|------|
| M1 | 错因描述提取常量 | judge_graph.py | +10 |
| M2 | method 字段收敛 | judge_graph.py + storage.py | +4 |
| M3 | 诊断加 retry | judge_graph.py | +10 |
| M4 | llm_client None 时跳过诊断 | judge_graph.py | +3 |
| M5 | 事务分离 | storage.py | +20 |
| M6 | 字段截断 | storage.py | +3 |
| M7 | 删死字段 | agent_states.py | -1 |

---

**总改动量约 130 行**（不含低风险的 future 项），即可把方案从"有致命缺陷"推到"可上线"。
