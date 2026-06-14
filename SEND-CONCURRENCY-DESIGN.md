# Send 并发 + 汇总设计

## 概述

主编产出 `ExamPlan`（N 个 `QuestionTask`）后，通过 LangGraph `Send` API 将每个任务分发到一条独立的生成流水线，N 条流水线并发执行，完成自动汇总。

这是整个架构中最关键的技术点。

---

## 一、Send 工作原理

```python
from langgraph.types import Send

def fan_out_to_pipelines(state: AgentState):
    """主编完成后，每个 QuestionTask 发一条独立流水线"""
    tasks = state["exam_plan"].tasks
    return [
        Send("generation_pipeline", {"current_task": task})
        for task in tasks
    ]
```

`Send(target, extra_state)` 做了两件事：
1. `target` — 指定目标节点（"generation_pipeline"）
2. `extra_state` — 传给该分支的额外字段，会**合并到该分支的 state 副本**中

N 个 `Send` 同时发出，LangGraph 将它们全部并行执行。所有分支跑完后，state 自动合并回主图。

---

## 二、State 设计 — Reducer 模式

每个分支有自己的 state 副本，分支跑完后需要合并回主 state。如果两个分支都写 `all_questions`，默认会**覆盖**，最后一个分支的值会丢失前面的。

用 `operator.add` 做 reducer，累加而不是覆盖：

```python
from typing import Annotated
import operator
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    # ── 全局 ──
    pdf_path: str
    toc: list[dict]
    exam_plan: ExamPlan | None

    # ── 当前任务（每个分支不同）──
    current_task: QuestionTask | None

    # ── 流水线中间状态 ──
    knowledge_point: str
    generated_question: GeneratedQuestion | None

    # ── 最终收集 ──
    # 关键：operator.add 让每个分支的结果追加到列表，而不是覆盖
    all_questions: Annotated[list[GeneratedQuestion], operator.add]
```

每个分支在质检审核员节点返回时：

```python
return {
    "all_questions": [final_question],  # operator.add 会把这条追加到总列表
}
```

N 条分支跑完，`all_questions` 自动聚合成 `[题1, 题2, ..., 题N]`。

---

## 三、流水线子图

每条流水线内部是三步串行，需要封装为一个子图节点：

```python
def build_generation_subgraph() -> StateGraph:
    """构建单题生成流水线子图"""

    subgraph = StateGraph(AgentState)

    # 第一步：知识点提取 + 工具循环
    subgraph.add_node("knowledge_extractor", create_knowledge_extractor(quick_llm))
    subgraph.add_node("tools_knowledge", ToolNode([
        get_section_text, get_surrounding_context, search_keyword
    ]))
    subgraph.add_node("knowledge_clear", create_knowledge_clear_node())

    subgraph.add_edge(START, "knowledge_extractor")
    subgraph.add_conditional_edges(
        "knowledge_extractor",
        should_continue_knowledge_extraction,
        {"tools_knowledge": "tools_knowledge", "knowledge_clear": "knowledge_clear"}
    )
    subgraph.add_edge("tools_knowledge", "knowledge_extractor")

    # 第二步：题目生成（按题型路由）
    subgraph.add_node("choice_generator", create_choice_generator(deep_llm))
    subgraph.add_node("fill_blank_generator", create_fill_blank_generator(deep_llm))
    subgraph.add_node("short_answer_generator", create_short_answer_generator(deep_llm))

    subgraph.add_conditional_edges(
        "knowledge_clear",
        route_by_question_type,
        {
            "choice": "choice_generator",
            "fill_blank": "fill_blank_generator",
            "short_answer": "short_answer_generator",
        }
    )

    # 第三步：质检审核
    subgraph.add_node("quality_reviewer", create_quality_reviewer(quick_llm))

    for gen in ["choice_generator", "fill_blank_generator", "short_answer_generator"]:
        subgraph.add_edge(gen, "quality_reviewer")

    subgraph.add_edge("quality_reviewer", END)

    return subgraph.compile()
```

---

## 四、主图注册

```python
# setup.py

workflow = StateGraph(AgentState)

# 前期
workflow.add_node("pdf_parser", pdf_parser_node)
workflow.add_node("chief_editor", create_chief_editor(deep_llm))
workflow.add_node("tools_editor", ToolNode([peek_section]))

# 中期 — 注册子图
generation_subgraph = build_generation_subgraph()
workflow.add_node("generation_pipeline", generation_subgraph)

# 后期
workflow.add_node("final_editor", create_final_editor(deep_llm))

# ─── 边 ───

# 前期
workflow.add_edge(START, "pdf_parser")
workflow.add_edge("pdf_parser", "chief_editor")
workflow.add_conditional_edges(
    "chief_editor",
    should_continue_chief_editor,
    {"tools_editor": "tools_editor", "generation_pipeline": "generation_pipeline"}
)
workflow.add_edge("tools_editor", "chief_editor")

# 中期 — 并发分发
workflow.add_conditional_edges(
    "chief_editor",
    fan_out_to_pipelines,          # 返回 N 个 Send("generation_pipeline", ...)
    ["generation_pipeline"]         # 所有 Send 的目标
)

# 后期 — 汇总后进入终审
# 所有分支完成后，state.all_questions 已自动汇总
workflow.add_edge("generation_pipeline", "final_editor")
workflow.add_edge("final_editor", END)
```

---

## 五、数据流全景

```
                                     ┌──────────────────┐
                                     │   主编产出 N 个    │
                                     │   QuestionTask    │
                                     └────────┬─────────┘
                                              │
                              fan_out_to_pipelines(state)
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              ▼                               ▼                               ▼
   ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
   │  流水线分支 1         │    │  流水线分支 2         │    │  流水线分支 N         │
   │  current_task=任务1   │    │  current_task=任务2   │    │  current_task=任务N   │
   │                     │    │                     │    │                     │
   │  知识点提取  → 题目   │    │  知识点提取  → 题目   │    │  知识点提取  → 题目   │
   │  生成 → 质检审核      │    │  生成 → 质检审核      │    │  生成 → 质检审核      │
   │                     │    │                     │    │                     │
   │  返回:               │    │  返回:               │    │  返回:               │
   │  all_questions=[题1]  │    │  all_questions=[题2]  │    │  all_questions=[题N]  │
   └─────────┬───────────┘    └─────────┬───────────┘    └─────────┬───────────┘
              │                          │                          │
              └──────────────────────────┼──────────────────────────┘
                                         │
                              operator.add 追加合并
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │  all_questions =     │
                              │  [题1, 题2, ..., 题N] │
                              └──────────┬──────────┘
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │     终审排版师        │
                              └─────────────────────┘
```

---

## 六、并发数控制

如果任务太多（比如 50 道题），同时全部并发可能超出 API 速率限制。LangGraph 支持通过配置限制并发：

```python
# 编译时设置最大并发数
graph = workflow.compile()

# 运行时通过 config 控制
config = {
    "max_concurrency": 8,       # 最多 8 条流水线同时跑
    "recursion_limit": 200,     # 总节点调用上限
}
result = graph.invoke(initial_state, config)
```

---

## 七、汇总后处理

所有分支完成后，`state.all_questions` 自动聚合。在终审排版师节点中直接读取：

```python
def final_editor_node(state):
    questions = state["all_questions"]  # [题1, 题2, ..., 题N]
    # 去重、平衡、排序、排版...
```

不需要额外的汇总节点——`Annotated[..., operator.add]` 已经自动处理了。

---

## 八、关键点总结

| 要点 | 做法 |
|------|------|
| 并发分发 | `Send("pipeline", {"current_task": task})` |
| 结果不覆盖 | `Annotated[list, operator.add]` reducer |
| 每分支独立 | 各自拥有 state 副本，用 `current_task` 区分任务 |
| 并发控制 | `max_concurrency` 配置 |
| 汇总时机 | 所有分支跑完自动进入下游节点 |
| 流水线封装 | 子图 `StateGraph.compile()` 注册为主图的一个节点 |
