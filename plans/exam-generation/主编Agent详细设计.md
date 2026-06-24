# 主编 Agent (chief_editor.py) 详细设计

## 概述

主编是前期唯一的 Agent，也是最核心的决策者。它**只读目录、不读正文**，根据章节标题判断哪些值得出题、出什么题型、设什么难度，产出一份最终出题任务清单。

---

## 一、位置

```
PDF解析器
  │
  │  toc（目录结构）
  ▼
[主编] ─── 读目录 → 选题 → 分配题型/难度 → ExamPlan
  │
  │  ExamPlan（N 个 QuestionTask）
  ▼
Send 并发 → N × 生成流水线
```

---

## 二、输入

PDF 解析器产出的 `toc`：

```python
[
    {
        "chapter": "第2章 变量和简单数据类型",
        "sections": [
            {"id": "2.1", "title": "2.1 变量"},
            {"id": "2.2", "title": "2.2 字符串"},
            {"id": "2.3", "title": "2.3 数字"},
        ]
    },
    {
        "chapter": "第3章 列表简介",
        "sections": [
            {"id": "3.1", "title": "3.1 列表是什么"},
            {"id": "3.2", "title": "3.2 修改、添加和删除元素"},
            {"id": "3.3", "title": "3.3 组织列表"},
        ]
    },
]
```

---

## 三、输出

`ExamPlan` — 任务清单：

```python
class ExamPlan(BaseModel):
    tasks: list[QuestionTask]
    difficulty_ratio: tuple[int, int, int]  # 易:中:难 比例，如 (3, 4, 3)
    total_score: int                        # 总分，如 100

class QuestionTask(BaseModel):
    chapter: str            # 章，如 "第3章 列表简介"
    section: str            # 节，如 "3.2"
    topic: str              # 知识点评述，如 "append()和insert()的区别"
    question_type: Literal["choice", "fill_blank", "short_answer"]
    difficulty: Literal["easy", "medium", "hard"]
    score: int              # 预计分值
```

---

## 四、工具

一个可选工具：`peek_section`。当章节标题太宽泛（如"列表是什么"、"概述"）无法判断时，看一眼前几段确认。

```python
@tool
def peek_section(section_id: str, paragraphs: int = 5) -> str:
    """预览章节前几段内容。当标题无法判断该节是否值得出题时使用。
    只返回该节的开头部分，不是全文。"""
    return pdf_manager.peek_section(section_id, paragraphs)
```

---

## 五、系统提示词

```python
system_message = """你是一份教材的试卷主编。你收到一本书的目录结构，需要规划一份覆盖全书重点的试卷。

你的工作：

### 1. 选题策略
- 浏览全部章节，选出值得考查的知识点
- 优先覆盖核心概念和操作性知识点（方法、流程、对比）
- 纯介绍性/背景性章节（如"本章概述"、"小结"）可以跳过
- 如果某节的标题太泛无法判断，用 peek_section 预览前几段确认

### 2. 题型分配
- 选择题：适合考定义、辨析、对比（如"A和B的区别"、"以下哪种说法正确"）
- 填空题：适合考关键词、参数、方法名（如"xxx方法用于在列表末尾添加元素"）
- 简答题：适合考理解、流程描述、分析对比（如"简述sort()和sorted()的区别"）

题型比例建议：
- 选择题约 50%（覆盖面广，快速检测多个知识点）
- 填空题约 25%（精准检测关键词记忆）
- 简答题约 25%（检测深度理解）

### 3. 难度设定
- 简单：基础概念记忆、单个方法的直接应用
- 中等：方法对比、概念辨析、常见场景分析
- 困难：综合应用、跨章节知识关联、易混淆细节

难度比例建议：简单 30%、中等 40%、困难 30%

### 4. 分值设定
- 选择题：每题 3-5 分
- 填空题：每题 3-5 分
- 简答题：每题 8-12 分
- 总分按需求设定（默认 100 分）

### 5. 章节覆盖
- 确保重要章节都有题目覆盖
- 同一节可以出 1-3 道题（不同题型），但不要过度集中在某几节
- 章节之间尽量均衡

列出最终的任务清单。topic 字段请用 10-20 字准确描述这个知识点在考什么，因为它是下一阶段题目生成器唯一的信息入口。"""
```

---

## 六、提示词模板

```python
def create_chief_editor(llm):

    def chief_editor_node(state):
        toc = state["toc"]

        # 格式化目录为可读文本
        toc_text = self._format_toc(toc)

        tools = [peek_section]

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是教材试卷主编。你可以使用 peek_section 预览章节开头。"
                "可用的工具：{tool_names}。\n{system_message}"
            ),
            (
                "user",
                "以下是本书的目录结构，请规划一份试卷：\n\n{toc_text}"
                "\n\n请输出完整的出题任务清单（ExamPlan）。",
            ),
        ])

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(toc_text=toc_text)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": []})

        # 解析结构化输出
        exam_plan = parse_exam_plan(result.content)

        return {
            "messages": [result],
            "exam_plan": exam_plan,
        }

    return chief_editor_node


def _format_toc(toc: list[dict]) -> str:
    """格式化目录为 LLM 可读文本"""
    lines = []
    for chapter in toc:
        lines.append(f"\n## {chapter['chapter']}")
        for section in chapter["sections"]:
            lines.append(f"  - [{section['id']}] {section['title']}")
    return "\n".join(lines)
```

---

## 七、工具调用循环（可选）

主编也可能用到工具，所以跟知识点提取器一样有循环：

```python
# conditional_logic.py

def should_continue_chief_editor(state):
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools_editor"
    return "Msg Clear Editor"
```

```python
# setup.py

workflow.add_node("chief_editor", create_chief_editor(deep_llm))
workflow.add_node("tools_editor", ToolNode([peek_section]))
workflow.add_node("Msg Clear Editor", create_editor_clear_node())

workflow.add_conditional_edges(
    "chief_editor",
    should_continue_chief_editor,
    {"tools_editor": "tools_editor", "Msg Clear Editor": "Msg Clear Editor"}
)
workflow.add_edge("tools_editor", "chief_editor")
```

---

## 八、LLM

深度思考模型。选题策略和题型分配需要较好的判断力——什么值考、用什么方式考、设什么难度。

---

## 九、完整走一遍

假设输入目录：

```
第2章 变量和简单数据类型
  - [2.1] 2.1 变量
  - [2.2] 2.2 字符串
  - [2.3] 2.3 数字
第3章 列表简介
  - [3.1] 3.1 列表是什么
  - [3.2] 3.2 修改、添加和删除元素
  - [3.3] 3.3 组织列表
```

主编行为：

| 轮次 | LLM 行为 | 工具调用 |
|------|----------|----------|
| 1 | "2.1 变量、2.2 字符串、2.3 数字——标题很清楚，都是核心概念。3.1 列表是什么——标题有点泛，看一眼确认" | `peek_section("3.1")` |
| 2 | 确认 3.1 讲的是列表定义和基本操作，值得考。信息足够 | — |

输出 ExamPlan：

```
tasks:
  1. [2.1] 变量的命名规则 | 填空 | 易 | 3分
  2. [2.2] 字符串的常用方法 | 选择 | 易 | 4分
  3. [2.2] 字符串与数字的类型转换 | 填空 | 中 | 4分
  4. [3.1] 列表的定义和特点 | 选择 | 易 | 4分
  5. [3.2] append()和insert()的区别 | 选择 | 中 | 4分
  6. [3.2] pop()和remove()的区别 | 选择 | 中 | 4分
  7. [3.3] sort()和sorted()的区别 | 简答 | 难 | 10分
  ...

difficulty_ratio: (3, 4, 3)
total_score: 100
```

---

## 十、主编不做什么

- **不读全书正文**：那不是它的工作，留给知识点提取器
- **不出具体题目**：它只决定考什么，不写题干选项
- **不标页码**：给的 section id 已经足够定位
