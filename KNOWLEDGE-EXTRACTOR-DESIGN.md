# 知识点提取器（knowledge_extractor）详细设计

## 概述

知识点提取器是中期生成流水线的第一步。采用 **工具调用循环模式**（借鉴 TradingAgents 分析师），LLM 自主决定读取哪些章节文本、调用几次工具，直到信息足够后输出结构化的知识点描述。

---

## 一、工作流程

```
输入：单个 QuestionTask
  │
  ▼
[知识点提取器 Agent] ─── LLM 绑定工具 ─── 自主决定读哪些章节
  │                                │
  │    ←─── [tools_knowledge] ←────┘  (有 tool_calls，循环)
  │
  ▼  (无 tool_calls，输出完成)
[消息清理] ─── 清除历史消息，放入上下文锚点
  │
  ▼
输出：结构化知识点描述 ─── 传给题目生成器
```

---

## 二、工具定义

### 2.1 get_section_text

```python
@tool
def get_section_text(section_id: str) -> str:
    """获取指定章节的完整正文内容。
    
    Args:
        section_id: 章节编号，如 '3.2' 或 '3.2.1'
    Returns:
        该章节的完整文本内容
    """
    return pdf_manager.get_section(section_id)
```

### 2.2 get_surrounding_context

```python
@tool
def get_surrounding_context(section_id: str, paragraphs: int = 3) -> str:
    """获取指定章节前后 N 段的上下文。当章节内容引用了前文概念时使用。
    
    Args:
        section_id: 章节编号
        paragraphs: 前后各取几段，默认 3
    Returns:
        目标章节及其前后各 N 段的文本
    """
    return pdf_manager.get_context_window(section_id, paragraphs)
```

### 2.3 search_keyword

```python
@tool
def search_keyword(keyword: str) -> str:
    """全书搜索关键词，找到该概念在书中首次出现或集中讲解的位置。
    
    Args:
        keyword: 要搜索的关键词、概念名
    Returns:
        匹配到的段落及所在章节
    """
    return pdf_manager.search(keyword)
```

---

## 三、Agent 系统提示词

```python
system_message = """你是知识点提取专家。你会收到一道出题任务，包含章节名称、知识点评述、题型、难度。

你的工作是：
1. 使用 get_section_text 读取目标章节的完整正文
2. 如果该章节引用了前文的概念，使用 get_surrounding_context 获取上下文
3. 如果概念的定义在前文、本章只是应用，使用 search_keyword 找到首次定义的位置

从章节内容中提炼出：
- **核心概念**：该知识点的准确定义和核心事实
- **关键细节**：步骤、参数、条件、限制等
- **常见误区**：初学者容易混淆或出错的地方
- **关联知识**：与该知识点关联的其他概念（可用于出干扰项或对比题）

读完信息足够后，输出结构化的知识点描述。不要再写后续的题目，你的工作到此为止。
"""
```

---

## 四、提示词模板结构

```python
def create_knowledge_extractor(llm):

    def knowledge_extractor_node(state):
        task = state.get("current_task")  # 当前正在处理的 QuestionTask
        section_id = task.chapter         # 如 "3.2"
        topic = task.topic                # 如 "append和insert的区别"
        question_type = task.question_type
        difficulty = task.difficulty

        tools = [get_section_text, get_surrounding_context, search_keyword]

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是知识点提取专家，负责从书本章节中提炼知识点。"
                "你可以使用工具来读取章节文本、获取上下文、搜索关键词。"
                "可用的工具：{tool_names}。\n{system_message}"
                "\n\n当前任务：\n"
                "- 目标章节：{section_id}\n"
                "- 知识点评述：{topic}\n"
                "- 目标题型：{question_type}\n"
                "- 目标难度：{difficulty}"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(section_id=section_id)
        prompt = prompt.partial(topic=topic)
        prompt = prompt.partial(question_type=question_type)
        prompt = prompt.partial(difficulty=difficulty)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        knowledge_output = ""
        if len(result.tool_calls) == 0:
            knowledge_output = result.content

        return {
            "messages": [result],
            "knowledge_point": knowledge_output,
        }

    return knowledge_extractor_node
```

---

## 五、条件路由

```python
# conditional_logic.py

def should_continue_knowledge_extraction(state: AgentState):
    """知识点提取器：有 tool_calls → 继续取数据，无 → 前进"""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools_knowledge"
    return "Msg Clear Knowledge"
```

---

## 六、消息清理

```python
# 提取完成后，清除工具调用的中间消息，只保留知识点的输出
def create_knowledge_clear_node():
    def clear_knowledge_messages(state):
        messages = state["messages"]
        removal_ops = [RemoveMessage(id=m.id) for m in messages]
        
        section_id = state.get("current_task", {}).get("chapter", "")
        placeholder = HumanMessage(
            content=f"知识点提取完成。下一个阶段：为 {section_id} 章节的知识点生成题目。"
        )
        return {"messages": removal_ops + [placeholder]}
    
    return clear_knowledge_messages
```

---

## 七、图结构（知识点提取部分）

```python
# setup.py 中加入

# 添加节点
workflow.add_node("knowledge_extractor", create_knowledge_extractor(llm))
workflow.add_node("tools_knowledge", ToolNode([get_section_text, get_surrounding_context, search_keyword]))
workflow.add_node("Msg Clear Knowledge", create_knowledge_clear_node())

# 条件边：工具调用循环
workflow.add_conditional_edges(
    "knowledge_extractor",
    should_continue_knowledge_extraction,
    {
        "tools_knowledge": "tools_knowledge",
        "Msg Clear Knowledge": "Msg Clear Knowledge",
    },
)
workflow.add_edge("tools_knowledge", "knowledge_extractor")

# 下一站：题目生成器
workflow.add_edge("Msg Clear Knowledge", "question_generator")
```

---

## 八、完整走一遍

假设任务：`{章节: "3.2", 知识点评述: "append和insert的区别", 题型: "选择", 难度: "中"}`

| 轮次 | LLM 行为 | 工具调用 | 工具返回 |
|------|----------|----------|----------|
| 1 | "我需要读第 3.2 节" | `get_section_text("3.2")` | 3.2 节完整文本（~500字） |
| 2 | "文本提到列表的增删方法基于 3.1 的概念，我需要确认列表的定义在 3.1 是怎么讲的" | `get_surrounding_context("3.2", paragraphs=2)` | 3.1 末尾 + 3.2 + 3.3 开头 |
| 3 | "够了，append 和 insert 的区别已经清晰" | — | — |

LLM 输出：

```
核心概念：
- append(x)：在列表末尾添加元素 x，参数只有一个
- insert(i, x)：在索引 i 处插入元素 x，参数有两个

关键细节：
- append 时间复杂度 O(1)，insert 时间复杂度 O(n)，因为插入后需要移动后续元素
- insert(0, x) 等效于在开头插入
- append 只能加到最后，insert 可以插到任意位置

常见误区：
- 容易把 insert 的参数顺序搞反，第二个才是值
- 以为 append 可以插到任意位置

关联知识：
- 3.1 列表定义 → 可出对比题
- 3.2 的 pop()、remove() → 可出综合题
```
