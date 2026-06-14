# 单题生成流水线设计

## 概述

每条生成流水线是 3 步串行：**知识点提取 → 题目生成 → 质检审核**。N 条流水线通过 LangGraph Send API 并发执行。

---

## 一、流水线图结构

```
输入：单个 QuestionTask
  │
  ▼
┌──────────────────────────────────────────────┐
│  知识点提取器 (knowledge_extractor)            │
│  工具调用循环，读原文提炼知识点                    │
│  LLM: 快速思考模型                              │
│                                                │
│  工具: get_section_text                        │
│        get_surrounding_context                 │
│        search_keyword                          │
└───────────────────┬──────────────────────────┘
                    ▼
              消息清理节点
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  题目生成器 (question_generator)                │
│  无工具，纯 LLM 一步出题                        │
│  LLM: 深度思考模型                              │
│                                                │
│  按题型路由到不同生成器：                         │
│    choice     → 选择题生成器                    │
│    fill_blank → 填空题生成器                    │
│    short_answer → 简答题生成器                  │
└───────────────────┬──────────────────────────┘
                    ▼
              消息清理节点
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  质检审核员 (quality_reviewer)                  │
│  无工具，纯 LLM 审核修正                        │
│  LLM: 快速思考模型                              │
└───────────────────┬──────────────────────────┘
                    ▼
输出：最终 GeneratedQuestion
```

---

## 二、第一步 — 知识点提取器 (knowledge_extractor.py)

### 模式
工具调用循环（借鉴 TradingAgents 分析师）

### 工具

```python
@tool
def get_section_text(section_id: str) -> str:
    """获取指定章节的完整正文内容。"""

@tool
def get_surrounding_context(section_id: str, paragraphs: int = 3) -> str:
    """获取指定章节前后 N 段的上下文。"""

@tool
def search_keyword(keyword: str) -> str:
    """全书搜索关键词，找到首次出现或集中讲解的位置。"""
```

### 系统提示词

```
你是知识点提取专家。根据收到的出题任务，使用工具读取书本章节内容，提炼出结构化知识点。

输出内容：
- 核心概念：该知识点的准确定义和核心事实
- 关键细节：步骤、参数、条件、限制等
- 常见误区：初学者容易混淆或出错的地方
- 关联知识：与该知识点关联的其他概念（可用于设计干扰项或对比题）

可以多次调用工具直到信息足够。不要继续写题目，你的工作到此为止。
```

### 条件路由

```python
def should_continue_knowledge_extraction(state):
    if state["messages"][-1].tool_calls:
        return "tools_knowledge"     # 继续读原文
    return "Msg Clear Knowledge"     # 前进到题目生成
```

---

## 三、第二步 — 题目生成器 (question_generator.py)

### 模式
无工具，纯 LLM 一步生成

### 路由

```
知识点提取完成后，根据 QuestionTask.question_type 路由：

├── "choice"       → 选择题生成器
├── "fill_blank"   → 填空题生成器
└── "short_answer" → 简答题生成器
```

---

### 3.1 选择题生成器

**LLM**：深度思考模型（干扰项设计需要推理）

**输入**：知识点描述 + QuestionTask

**输出**：`GeneratedQuestion`（含 4 个选项 + 正确答案 + 解析）

**系统提示词要点**：
```
你是选择题出题专家。根据知识点描述，生成一道选择题。

要求：
- 题干清晰，问题指向明确
- 4 个选项（A/B/C/D），1 个正确 + 3 个干扰项
- 干扰项从以下角度设计：
  1. 概念混淆（相关但不同的概念）
  2. 顺序颠倒（参数顺序、步骤先后）
  3. 边界反例（特殊情况当作一般情况）
  4. 常见误解（初学者容易犯的错误）
- 干扰项必须"看起来合理但确实错误"，不能有明显扯淡的选项
- 正确答案唯一，可以合理排除其他 3 项
- 附带解析，说明为什么正确项对、每个干扰项为什么错
```

---

### 3.2 填空题生成器

**LLM**：深度思考模型

**输入**：知识点描述 + QuestionTask

**输出**：`GeneratedQuestion`（options 为空，题干含 `___` 占位符）

**系统提示词要点**：
```
你是填空题出题专家。根据知识点描述，生成一道填空题。

要求：
- 从知识点中选取一个不可替代的关键词/短语进行挖空
- 挖掉后题干仍能读通，能推断出在问什么
- 答案唯一，不能有歧义（换一种说法也算对就是不合格的）
- 答案应是简短的一个词、数字或短句
- 附带解析
```

---

### 3.3 简答题生成器

**LLM**：深度思考模型

**输入**：知识点描述 + QuestionTask

**输出**：`GeneratedQuestion`（含参考答案 + 评分要点）

**系统提示词要点**：
```
你是简答题出题专家。根据知识点描述，生成一道简答题。

要求：
- 设问具体，考查理解而非记忆
- 不能太宽（"谈谈你对 X 的理解"不合格）
- 也不能太窄（变成填空题）
- 给出参考答案（要有要点分解）
- 给出评分要点（每个要点分值，总分 5-10 分）
```

---

## 四、第三步 — 质检审核员 (quality_reviewer.py)

### 模式
无工具，纯 LLM 一步审核 + 修正

### LLM
快速思考模型

### 输入
知识点描述 + GeneratedQuestion

### 输出

```python
class QualityReview(BaseModel):
    verdict: Literal["pass", "fixed", "rejected"]
    issues: str                    # 发现的问题（如有）
    final_question: GeneratedQuestion  # 通过或修正后的最终题目
```

### 系统提示词要点
```
你是题目质检专家。审核生成的题目，检查以下维度：

- 答案正确性：答案是否与知识点描述一致
- 题干清晰度：题干是否清楚无歧义
- 选项合理性（选择题）：干扰项是否能合理排除，正确项是否唯一
- 答案唯一性（填空题）：挖空词是否唯一确定
- 设问质量（简答题）：设问是否具体可评分
- 难度匹配：题目难度是否接近目标难度

小问题直接修正（verdict=fixed），大问题（题干逻辑错误）退回（verdict=rejected），没问题通过（verdict=pass）。
```

---

## 五、状态定义

```python
class AgentState(MessagesState):
    # 全局
    pdf_path: str
    toc: list[dict]
    exam_plan: ExamPlan

    # 流水线内
    current_task: QuestionTask          # 当前正在处理的任务
    knowledge_point: str                 # 知识点提取器的输出
    generated_question: GeneratedQuestion # 题目生成器的输出
    final_question: GeneratedQuestion    # 质检通过后的最终题目

    # 汇总
    all_questions: list[GeneratedQuestion]
    final_exam: FinalExam
```

---

## 六、流水线子图构造

```python
def build_generation_subgraph() -> StateGraph:
    """构建单题生成流水线子图，被 Send 并发调用。"""
    
    subgraph = StateGraph(AgentState)
    
    # 第一步：知识点提取 + 工具循环
    subgraph.add_node("knowledge_extractor", create_knowledge_extractor(quick_llm))
    subgraph.add_node("tools_knowledge", ToolNode([
        get_section_text, get_surrounding_context, search_keyword
    ]))
    subgraph.add_node("clear_knowledge", create_knowledge_clear_node())
    
    subgraph.add_edge(START, "knowledge_extractor")
    subgraph.add_conditional_edges(
        "knowledge_extractor",
        should_continue_knowledge_extraction,
        {"tools_knowledge": "tools_knowledge", "clear_knowledge": "clear_knowledge"}
    )
    subgraph.add_edge("tools_knowledge", "knowledge_extractor")
    
    # 第二步：题目生成（按题型路由）
    subgraph.add_node("choice_generator", create_choice_generator(deep_llm))
    subgraph.add_node("fill_blank_generator", create_fill_blank_generator(deep_llm))
    subgraph.add_node("short_answer_generator", create_short_answer_generator(deep_llm))
    
    subgraph.add_conditional_edges(
        "clear_knowledge",
        route_by_question_type,
        {
            "choice": "choice_generator",
            "fill_blank": "fill_blank_generator",
            "short_answer": "short_answer_generator",
        }
    )
    
    # 第三步：质检审核
    subgraph.add_node("clear_question", create_generator_clear_node())
    subgraph.add_node("quality_reviewer", create_quality_reviewer(quick_llm))
    
    for gen in ["choice_generator", "fill_blank_generator", "short_answer_generator"]:
        subgraph.add_edge(gen, "quality_reviewer")
    
    subgraph.add_edge("quality_reviewer", END)
    
    return subgraph.compile()
```

---

## 七、各步骤总结

| 步骤 | Agent | 调用次数 | 工具 | LLM | 输入 | 输出 |
|------|-------|---------|------|-----|------|------|
| 知识点提取 | 提取器 | N 次工具循环 | 3 个 | 快 | QuestionTask | 知识点描述 |
| 题目生成 | 选择/填空/简答 | 1 次 LLM | 无 | 深 | 知识点 + Task | GeneratedQuestion |
| 质检审核 | 审核员 | 1 次 LLM | 无 | 快 | 知识点 + 题目 | 最终题目 |
