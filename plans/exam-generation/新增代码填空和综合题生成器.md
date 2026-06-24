# 新增 code_fill 和 comprehensive 题型生成器

## 目标

出题系统目前只支持 3 种题型（choice / fill_blank / short_answer），但往年试卷分析会识别出 5 种题型（含 code_fill / comprehensive）。新增两个生成器，让系统能出代码填空题和综合题，与往年题型体系对齐。

## 题型定义

| 题型 | 说明 | 示例 |
|------|------|------|
| `code_fill` | 代码填空题 | 给一段源码，挖掉关键逻辑，让考生补全 |
| `comprehensive` | 综合题 | 含代码分析、运行推演、设计等多种能力的综合考查 |

## 改动清单

### 1. `exam/agents/schemas.py` — 加 Schema

- `QuestionType` 枚举加 `CODE_FILL = "code_fill"`、`COMPREHENSIVE = "comprehensive"`
- 新增 `CodeFillQuestion(BaseModel)`：stem（含代码的题干）、correct_answer、explanation
- 新增 `ComprehensiveQuestion(BaseModel)`：stem（完整题目，可能含代码）、correct_answer（参考答案/要点）、explanation（评分要点）

### 2. `exam/agents/generators/question_generator.py` — 加生成器

- `create_code_fill_generator`：
  - system prompt 要点：代码上下文 + 挖关键逻辑（函数名、参数、算法步骤），答案唯一，用 `___` 表示空缺
  - 结构化输出 `CodeFillQuestion`

- `create_comprehensive_generator`：
  - system prompt 要点：设问具体（可含代码分析、运行推演、方案设计），参考答案分要点，附评分要点
  - 结构化输出 `ComprehensiveQuestion`

### 3. `exam/agents/__init__.py` — 导出

- 加 `create_code_fill_generator`、`create_comprehensive_generator` 导入

### 4. `exam/agents/planner/chief_editor.py` — 题型映射

- `_normalize_type`：加 `code_fill`、`comprehensive` 的识别规则
- `analysis_instruction`：往年题型名 → 系统题型名，直接一一对应展示

### 5. `exam/graph/conditional_logic.py` — 路由

- `route_by_question_type`：加 `code_fill` → `code_fill_generator`、`comprehensive` → `comprehensive_generator`

### 6. `exam/graph/setup.py` — 图节点

- `_build_generation_subgraph`：加 `code_fill_generator`、`comprehensive_generator` 节点
- 路由映射加两条新路由
- 两个新生成器 → `quality_reviewer` 加边

### 7. `exam/agents/reviewers/final_editor.py` — 排版

- `TYPE_ORDER`：`code_fill` 排在 `fill_blank` 之后（序号 2），`comprehensive` 排最后（序号 4）
- `TYPE_LABELS`：`code_fill` → "代码填空题"、`comprehensive` → "综合题"

## 不改动的文件

- `exam/analyzers/llm_analyzer.py`：已支持识别 code_fill 和 comprehensive
- `exam/analyzers/schemas.py`：`AnalyzedQuestion` 已枚举这 5 种题型
- `exam/agents/generators/quality_reviewer.py`：质检逻辑与题型无关，通用
- 其余文件

## 执行顺序

1 → 2 → 3 → 4 → 5 → 6 → 7（按依赖关系）
