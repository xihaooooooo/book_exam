"""质检审核员：结构化输出审核结果"""

from langchain_core.prompts import ChatPromptTemplate
from exam.agents.utils.agent_utils import create_llm_client
from exam.agents.utils.structured import invoke_structured
from exam.agents.schemas import QualityReview


def create_quality_reviewer(config: dict = None):

    def quality_reviewer_node(state):
        question = state.get("generated_question", {})
        knowledge_point = state.get("knowledge_point", "")

        # 格式化题目供审核
        question_text = _format_question(question)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是题目质检专家。审核下面的题目："
                "\n- 答案正确性：答案是否与知识点一致"
                "\n- 题干清晰度：是否清楚无歧义"
                "\n- 选项合理性（选择题）：干扰项可否合理排除"
                "\n- 答案唯一性（填空题）：答案是否唯一确定"
                "\n- 设问质量（简答题）：是否具体可评分"
                "\n\n小问题直接修正（verdict=fixed），大问题退回（verdict=rejected），"
                "通过则 verdict=pass。输出修正后的完整题目。"
                "\n\n原知识点：\n{knowledge_point}"
            ),
            (
                "user",
                "请审核以下题目：\n\n{question_text}"
            ),
        ])

        prompt = prompt.partial(knowledge_point=knowledge_point)
        prompt = prompt.partial(question_text=question_text)

        llm = create_llm_client(config)
        result = invoke_structured(llm, QualityReview, prompt.format_messages())

        # 构建最终题目
        reviewed = {
            "question_type": question.get("question_type", "choice"),
            "difficulty": question.get("difficulty", "medium"),
            "source": question.get("source", ""),
            "stem": result.stem,
            "options": [],
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        # 选择题：还原选项列表
        if result.option_a:
            reviewed["options"] = [
                f"A. {result.option_a}",
                f"B. {result.option_b}",
                f"C. {result.option_c}",
                f"D. {result.option_d}",
            ]

        return {
            "all_questions": [reviewed],
        }

    return quality_reviewer_node


def _format_question(q: dict) -> str:
    lines = [f"题型: {q.get('question_type', '')}", f"难度: {q.get('difficulty', '')}"]
    if q.get("stem"):
        lines.append(f"题干: {q['stem']}")
    if q.get("options"):
        for opt in q["options"]:
            lines.append(opt)
    if q.get("correct_answer"):
        lines.append(f"正确答案: {q['correct_answer']}")
    if q.get("explanation"):
        lines.append(f"解析: {q['explanation']}")
    return "\n".join(lines)
