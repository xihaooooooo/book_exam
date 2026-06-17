"""LLM 分析器：将试卷分组文本发送给 LLM 进行拆题 + 分类"""

from exam.agents.utils.agent_utils import create_llm_client
from exam.agents.utils.structured import invoke_structured
from exam.analyzers.schemas import SectionAnalysis

SYSTEM_PROMPT = """你是一位试卷分析专家。请将以下试卷部分拆分为单道题目，并对每道题进行分析。

## 题型（question_type）

- choice: 选择题（含选项 A/B/C/D）
- fill_blank: 填空题（含挖空）
- short_answer: 简答题/问答题（文字作答）
- code_fill: 代码填空题（补全代码中的空缺）
- comprehensive: 综合题（含编程实现、系统设计、代码分析等）

## 难度（difficulty）

- easy: 基础概念记忆、直接套用公式/方法
- medium: 需要理解原理、进行对比分析
- hard: 需要综合多章节知识、设计或推导

## 知识点（topic / knowledge_points）

- topic: 知识点所属章节或知识领域，用教材章节名表述（如"任务管理"、"中断处理"、"信号量"）
- knowledge_points: 具体考查的知识点，尽可能细化（如["任务状态转换", "就绪表查找"]）

## 注意

- 如果一道题跨多个段落（如题干+代码），将完整内容放在 stem 中
- 同一分组内的多道题要全部拆分出来
- topic 尽量与教材章节对应"""


def analyze_section(section_title: str, section_text: str, config: dict = None) -> list[dict]:
    """分析一个分组（Heading 2 下的所有题目文本），返回分析后的题目列表。

    Args:
        section_title: 分组标题，如 "填空题（5道*10分）"
        section_text: 该分组下所有题目文本（已拼接）
        config: LLM 配置

    Returns:
        [{"stem": "...", "question_type": "fill_blank", ...}, ...]
    """
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("user", "试卷部分：{section_title}\n\n{section_text}"),
    ])

    prompt = prompt.partial(section_title=section_title, section_text=section_text)

    llm = create_llm_client(config)
    result = invoke_structured(llm, SectionAnalysis, prompt.format_messages())

    return [q.model_dump() for q in result.questions]


def analyze_exam(parsed_exam: dict, config: dict = None) -> dict:
    """分析整份试卷，逐分组调用 LLM。

    Args:
        parsed_exam: parse_docx 的输出 {"title": ..., "sections": [...]}
        config: LLM 配置

    Returns:
        {"title": ..., "filename": ..., "questions": [...]}
    """
    all_questions = []
    sections = parsed_exam.get("sections", [])

    for i, sec in enumerate(sections):
        title = sec["title"]
        text = "\n\n".join(sec["texts"])

        if not text.strip():
            continue

        print(f"  [{i+1}/{len(sections)}] 分析: {title} ({len(sec['texts'])} 段文本) ...", end=" ")

        try:
            questions = analyze_section(title, text, config)
            all_questions.extend(questions)
            print(f"✓ {len(questions)} 题")
        except Exception as e:
            print(f"✗ 出错: {e}")
            continue

    return {
        "title": parsed_exam.get("title", ""),
        "filename": parsed_exam.get("filename", ""),
        "questions": all_questions,
    }
