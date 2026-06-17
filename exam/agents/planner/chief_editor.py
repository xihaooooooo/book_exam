"""主编 Agent：读目录 → 选题、分配题型/难度 → 产出任务清单"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from exam.agents.utils.agent_utils import (
    create_llm_client,
    get_section_text,
    get_surrounding_context,
    search_keyword,
)
from exam.agents.utils.structured import invoke_structured


class TaskItem(BaseModel):
    chapter: str = Field(description="章名")
    section: str = Field(description="节编号")
    topic: str = Field(description="知识点评述")
    question_type: str = Field(description="题型：choice 或 选择题")
    difficulty: str = Field(description="难度：easy 或 简单")


class PlanOutput(BaseModel):
    tasks: list[TaskItem] = Field(description="出题任务清单")


@tool
def peek_section(section_id: str, paragraphs: int = 5) -> str:
    """预览章节开头几段内容。当章节标题太宽泛无法判断时使用。
    Args:
        section_id: 章节编号，如 '2.1'
        paragraphs: 预览前几段，默认 5 段
    """
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
        focus = state.get("focus", "")
        target_count = state.get("target_count", 0)
        allowed_types = state.get("allowed_types", "")

        # 格式化目录
        toc_lines = []
        for ch in toc:
            toc_lines.append(f"\n## {ch['chapter']}")
            for sec in ch["sections"]:
                toc_lines.append(f"  - [{sec['id']}] {sec['title']}")
        toc_text = "\n".join(toc_lines)

        tools = [peek_section, get_section_text, get_surrounding_context, search_keyword]

        # ── 往年试卷分析指令 ──
        analysis_instruction = ""
        analysis_report = state.get("analysis_report")
        if analysis_report:
            agg = analysis_report.get("aggregated", {})
            exams = analysis_report.get("exams", [])

            # 考点频率 top8
            topic_lines = []
            for t, c in sorted(agg.get("topic_frequency", {}).items(), key=lambda x: -x[1])[:8]:
                topic_lines.append(f"  - {t}: {c} 次")

            # 题型分布
            type_dist = agg.get("type_distribution", {})

            # 难度分布
            diff_dist = agg.get("difficulty_distribution", {})

            total_q = agg.get("total_questions", 0)

            # 往年样例（每份卷取 2 道）
            samples = []
            for exam in exams:
                for q in exam.get("questions", [])[:2]:
                    samples.append(f"  - [{q.get('question_type','')}/{q.get('difficulty','')}] {q.get('stem','')[:150]}")

            analysis_instruction = f"""
## 往年试卷分析数据

以下是往年真题的统计数据，请在规划时尽量贴合这些指标：

### 高频考点（按频次排列）
{chr(10).join(topic_lines)}

### 往年题型分布
{type_dist}

### 往年难度分布
{diff_dist}

### 往年题目样例（参考风格）
{chr(10).join(samples)}

### 参考
- 目标总题数：{total_q} 道
- 用 search_keyword 搜索高频考点关键词，定位到教材对应章节
- 题型和难度比例尽量贴合往年分布
- 题目风格参考上述样例

"""

        # ── focus 指令 ──
        focus_instruction = ""
        if focus:
            focus_instruction = f"""
## 考试重点（用户指定）

用户要求考试重点为：**{focus}**

请按以下步骤操作：
1. 用 search_keyword 分别搜索这些关键词，定位相关章节
2. 如果命中 < 3 节：用 get_surrounding_context 扩展到相邻章节
3. 如果命中 3-10 节：直接使用
4. 如果命中 > 10 节：用 peek_section 预览内容后精选最核心的 8 节
5. 如果 focus 含多个考点（逗号分隔），每个考点选 1-2 节代表作即可，确保每个考点至少出 1 道题
6. **只在命中的章节范围内出题，不要扩展到其他章节**

"""

        # 如果提供了往年分析但未手动指定题数，自动设为往年题数
        if target_count == 0 and analysis_report:
            target_count = analysis_report.get("aggregated", {}).get("total_questions", 0) or 0

        # ── 题数指令 ──
        count_instruction = ""
        if target_count > 0:
            count_instruction = f"\n总题数要求：**恰好出 {target_count} 道题**。\n"
        else:
            count_instruction = "\n根据命中的章节数自动决定题数：≤4 节每节 2 道，5-8 节每节 1-2 道，>8 节每节 1 道。总题数控制在 6-12 道。\n"

        # ── 题型指令 ──
        types_instruction = ""
        if allowed_types:
            types_instruction = f"\n题型限制：**只允许 {allowed_types}**，不出其他题型。\n"

        system_message = (
            """你是一份教材的试卷主编。你收到一本书的目录结构，需要规划一份覆盖全书重点的试卷。

你的工作：

### 1. 选题策略
- 浏览全部章节，选出值得考查的知识点
- 优先覆盖核心概念和操作性知识点（方法、流程、对比）
- 纯介绍性/背景性章节（如"概述"、"小结"、"本章回顾"）可以跳过
- 如果某节的标题太泛无法判断，用 peek_section 预览前几段确认
"""
            + analysis_instruction
            + focus_instruction +
            """
### 2. 题型分配
- 选择题：适合考定义、辨析、对比（如"A和B的区别"、"以下哪种说法正确"）
- 填空题：适合考关键词、方法名、参数名（如"用___方法在列表末尾添加元素"）
- 简答题：适合考理解、流程描述、分析对比（如"简述sort()和sorted()的区别"）
"""
            + types_instruction +
            """
### 3. 难度设定
- 简单：基础概念记忆、单个方法的直接应用
- 中等：方法对比、概念辨析、常见场景分析
- 困难：综合应用、跨章节知识关联、易混淆细节


### 4. 章节覆盖
- 确保重要章节都有题目覆盖
- 同一节可以出 1-3 道题（不同题型）
- 但不要过度集中在某几节，各章节尽量均衡
"""
            + count_instruction +
            """
输出格式：直接列出任务清单，每行一条：
```
task_id | 章 | 节 | 知识点评述(10-20字) | 题型 | 难度
```

最后汇总：总题数、难度分布、题型分布。"""
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

        report = ""
        if not result.tool_calls:
            report = result.content

        # 结构化输出（不再用正则解析）
        if report:
            try:
                from langchain_core.messages import SystemMessage, HumanMessage
                clean_msgs = [
                    SystemMessage(content="将以下出题计划转为 JSON 格式。"),
                    HumanMessage(content=report)
                ]
                plan = invoke_structured(llm, PlanOutput, clean_msgs)
                tasks = [{
                    "id": i + 1,
                    "chapter": t.chapter,
                    "section": t.section,
                    "topic": t.topic,
                    "question_type": _normalize_type(t.question_type),
                    "difficulty": _normalize_difficulty(t.difficulty),
                } for i, t in enumerate(plan.tasks)]
            except Exception as e:
                print(f"[主编] 结构化输出失败: {e}")
                tasks = []
        else:
            tasks = []

        # 难度目标比例：优先用往年数据，否则用默认 3:4:3
        diff_ratio = (3, 4, 3)
        if analysis_report:
            diff_dist = analysis_report.get("aggregated", {}).get("difficulty_distribution", {})
            if diff_dist:
                e = diff_dist.get("easy", 0)
                m = diff_dist.get("medium", 0)
                h = diff_dist.get("hard", 0)
                if e + m + h > 0:
                    diff_ratio = (e, m, h)

        exam_plan = {
            "tasks": tasks,
            "difficulty_ratio": diff_ratio,
            "total_score": 100,
        }

        return {
            "messages": [result],
            "exam_plan": exam_plan,
        }

    return chief_editor_node


def _normalize_type(t: str) -> str:
    t = t.lower()
    if "选择" in t or "choice" in t:
        return "choice"
    if "填空" in t or "fill" in t or "blank" in t:
        return "fill_blank"
    if "简答" in t or "short" in t or "问答" in t:
        return "short_answer"
    return "choice"


def _normalize_difficulty(d: str) -> str:
    d = d.lower()
    if "易" in d or "简单" in d or "easy" in d:
        return "easy"
    if "难" in d or "困难" in d or "hard" in d:
        return "hard"
    return "medium"
