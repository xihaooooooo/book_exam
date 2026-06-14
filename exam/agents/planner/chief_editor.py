"""主编 Agent：读目录 → 选题、分配题型/难度 → 产出任务清单"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from exam.agents.utils.agent_utils import create_llm_client


@tool
def peek_section(section_id: str, paragraphs: int = 5) -> str:
    """预览章节开头几段内容。当章节标题太宽泛无法判断时使用。
    Args:
        section_id: 章节编号，如 '2.1'
        paragraphs: 预览前几段，默认 5 段
    """
    from exam.agents.utils.agent_utils import get_section_text
    text = get_section_text.invoke({"section_id": section_id})
    lines = text.split("\n")
    preview_lines = []
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped:
            preview_lines.append(stripped)
            if not stripped.startswith(("1.", "2.", "3.")):
                count += 1
        if count >= paragraphs:
            break
    return "\n".join(preview_lines)


def create_chief_editor(config: dict = None):

    def chief_editor_node(state):
        toc = state["toc"]

        # 格式化目录
        toc_lines = []
        for ch in toc:
            toc_lines.append(f"\n## {ch['chapter']}")
            for sec in ch["sections"]:
                toc_lines.append(f"  - [{sec['id']}] {sec['title']}")
        toc_text = "\n".join(toc_lines)

        tools = [peek_section]

        system_message = (
            """你是一份教材的试卷主编。你收到一本书的目录结构，需要规划一份覆盖全书重点的试卷。

你的工作：

### 1. 选题策略
- 浏览全部章节，选出值得考查的知识点
- 优先覆盖核心概念和操作性知识点（方法、流程、对比）
- 纯介绍性/背景性章节（如"概述"、"小结"、"本章回顾"）可以跳过
- 如果某节的标题太泛无法判断，用 peek_section 预览前几段确认

### 2. 题型分配
- 选择题：适合考定义、辨析、对比（如"A和B的区别"、"以下哪种说法正确"）
- 填空题：适合考关键词、方法名、参数名（如"用___方法在列表末尾添加元素"）
- 简答题：适合考理解、流程描述、分析对比（如"简述sort()和sorted()的区别"）

题型比例建议：选择题约50%，填空题约25%，简答题约25%

### 3. 难度设定
- 简单：基础概念记忆、单个方法的直接应用
- 中等：方法对比、概念辨析、常见场景分析
- 困难：综合应用、跨章节知识关联、易混淆细节

难度比例建议：简单30%、中等40%、困难30%

### 4. 分值设定
- 选择题：每题 4-5 分
- 填空题：每题 4-5 分
- 简答题：每题 8-12 分
- 总分建议 100 分

### 5. 章节覆盖
- 确保重要章节都有题目覆盖
- 同一节可以出 1-3 道题（不同题型）
- 但不要过度集中在某几节，各章节尽量均衡

输出格式：直接列出任务清单，每行一条：
```
task_id | 章 | 节 | 知识点评述(10-20字) | 题型 | 难度 | 分值
```

**总题数控制在 6-12 道**，不要超过 15 道。3 章的内容，题太多会冗余。

最后汇总：总题数、总分、难度分布、题型分布。"""
        )

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是教材试卷主编。可用的工具：{tool_names}。\n{system_message}"
            ),
            MessagesPlaceholder(variable_name="messages"),
            (
                "user",
                "以下是本书的目录结构，请规划一份试卷：\n\n{toc_text}"
                "\n\n请输出完整的出题任务清单。"
            ),
        ])

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(toc_text=toc_text)

        llm = create_llm_client(config)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state.get("messages", [])})

        # 判定是否产出报告
        report = ""
        if not result.tool_calls:
            report = result.content

        # 解析任务清单（简易解析，正式版可用 Pydantic）
        tasks = _parse_task_list(report) if report else []

        exam_plan = {
            "tasks": tasks,
            "difficulty_ratio": (3, 4, 3),
            "total_score": sum(t.get("score", 0) for t in tasks),
        }

        return {
            "messages": [result],
            "exam_plan": exam_plan,
        }

    return chief_editor_node


def _parse_task_list(report: str) -> list[dict]:
    """从主编输出中解析任务清单。"""
    import re
    tasks = []
    for line in report.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("task_id"):
            continue
        # 跳过分隔线
        chars = set(line.replace("|", "").strip())
        if chars <= {"-", "─", "━", "═", " "}:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue

        chapter_raw = parts[1] if len(parts) > 1 else ""
        section_raw = parts[2] if len(parts) > 2 else ""

        # 过滤垃圾行：section 含中文量词/括号明显是汇总行
        if re.search(r"[道题分）\)]", section_raw):
            continue
        # section 不能只是章节名（如 "第1章"）
        if re.match(r"^第\d+章$", section_raw):
            continue
        # section 不能为空或过短
        if len(section_raw) < 3:
            continue
        # chapter 不能含 markdown 或模板关键词
        if re.search(r"\*\*|task_id|合计|总计", chapter_raw):
            continue
        # chapter 不能是难度标签
        if chapter_raw in ("容易", "简单", "中等", "困难", "难", "易", "中"):
            continue

        try:
            tasks.append({
                "id": len(tasks) + 1,
                "chapter": chapter_raw,
                "section": section_raw,
                "topic": parts[3] if len(parts) > 3 else "",
                "question_type": _normalize_type(parts[4]) if len(parts) > 4 else "choice",
                "difficulty": _normalize_difficulty(parts[5]) if len(parts) > 5 else "medium",
                "score": int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 5,
            })
        except (ValueError, IndexError):
            continue
    return tasks


def _normalize_type(t: str) -> str:
    t = t.lower()
    if "选择" in t or "choice" in t:
        return "choice"
    if "填空" in t or "fill" in t or "blank" in t:
        return "fill_blank"
    if "简答" in t or "short" in t or "问答题" in t:
        return "short_answer"
    return "choice"


def _normalize_difficulty(d: str) -> str:
    d = d.lower()
    if "易" in d or "简单" in d or "easy" in d:
        return "easy"
    if "难" in d or "困难" in d or "hard" in d:
        return "hard"
    return "medium"
