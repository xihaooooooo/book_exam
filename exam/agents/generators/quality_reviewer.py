"""质检审核员：两档制审核（pass/fail），只做裁判不修题"""

from langchain_core.prompts import ChatPromptTemplate
from exam.agents.utils.agent_utils import create_llm_client
from exam.agents.utils.structured import invoke_structured
from exam.agents.schemas import QualityReview


def create_quality_reviewer(config: dict = None):

    def quality_reviewer_node(state):
        question = state.get("generated_question")
        if not question or not question.get("stem"):
            print("  ⚠ 题目为空，跳过审核")
            return {
                "retry_count": state.get("retry_count", 0) + 1,
                "review_feedback": "题目为空或缺少题干，请重新生成",
            }

        knowledge_point = state.get("knowledge_point", "")
        question_text = _format_question(question)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是题目质检专家。审核下面的题目，只做裁判，不做编辑。"
                "\n- 答案正确性：答案是否与知识点一致"
                "\n- 题干清晰度：是否清楚无歧义"
                "\n- 选项合理性（选择题）：干扰项可否合理排除"
                "\n- 答案唯一性（填空题）：答案是否唯一确定"
                "\n- 设问质量（简答题）：是否具体可评分"
                "\n\n审核结论只有两种："
                "\n- pass：题目完全合格"
                "\n- fail：题目有问题，在 issues 中具体说明哪里不行"
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
        try:
            result = invoke_structured(llm, QualityReview, prompt.format_messages())
        except Exception:
            print("  ⚠ 质检员输出解析失败，退回重试")
            return {
                "retry_count": state.get("retry_count", 0) + 1,
                "review_feedback": "质检员输出格式异常，请重新生成",
            }

        if result.verdict == "pass":
            print(f"  ✓ [{question.get('question_type', '')}] 审核通过")
            return {"all_questions": [question], "review_feedback": ""}

        retry = state.get("retry_count", 0) + 1
        if retry >= 2:
            print(f"  ⚠ [{question.get('question_type', '')}] 已达最大重试次数，强制放行")
            return {"all_questions": [question], "review_feedback": ""}

        feedback = result.issues.strip() or "审稿员认为题目不合格，请重新改进"
        print(f"  ✗ [{question.get('question_type', '')}] 不通过（第{retry}次重试），原因：{feedback[:80]}")
        return {
            "retry_count": retry,
            "review_feedback": feedback,
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
