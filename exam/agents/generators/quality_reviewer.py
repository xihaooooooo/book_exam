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
                "\n- 公式格式：数学表达式和公式是否用 LaTeX 标记，内联公式用 $...$，块级公式用 $$...$$"
                "\n- 如果题目出现明确的数学表达式却未用 LaTeX 标记，应判为 fail 并说明位置"
                "\n- Mermaid 媒体：若 media.type=mermaid，content 应为 flowchart、graph TD/LR、sequenceDiagram 或 gantt 子集，且题干应引用对应 [media:id]"
                "\n- Canvas 媒体：若 media.type=canvas，config 应包含 canvas_type；当前阶段 Canvas 只能作为只读结构预览，题目必须能用文本、选择、填空或简答作答"
                "\n- 图文题质量：若题目带 media，题干必须明确要求根据图/结合图/观察图中信息作答，解析应说明图中依据"
                "\n- 禁止交互作答：不得要求学生点击图、拖拽、连线、编辑画布、移动节点或在图上标注；出现这类要求应判为 fail"
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
    if q.get("media"):
        lines.append("媒体:")
        for item in q["media"]:
            if not isinstance(item, dict):
                continue
            media_id = item.get("id", "")
            media_type = item.get("type", "")
            desc = item.get("description") or item.get("src") or item.get("content") or ""
            if media_type == "canvas" and not desc:
                desc = item.get("config") or ""
            if isinstance(desc, str) and len(desc) > 500:
                desc = desc[:500] + "..."
            elif not isinstance(desc, str):
                desc = str(desc)[:500]
            lines.append(f"- {media_id}/{media_type}: {desc}")
    return "\n".join(lines)
