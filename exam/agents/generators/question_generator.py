"""题目生成器：结构化输出选择题、填空题、简答题"""

from langchain_core.prompts import ChatPromptTemplate
from exam.agents.utils.agent_utils import create_llm_client
from exam.agents.utils.structured import invoke_structured
from exam.agents.schemas import ChoiceQuestion, FillBlankQuestion, ShortAnswerQuestion, CodeFillQuestion, ComprehensiveQuestion


def create_choice_generator(config: dict = None):
    """选择题生成器 —— 结构化输出"""

    def choice_generator_node(state):
        knowledge_point = state.get("knowledge_point", "")
        fb = state.get("review_feedback", "")
        if fb:
            knowledge_point = knowledge_point + f"\n\n上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。"
        task = state.get("current_task", {})
        difficulty = task.get("difficulty", "medium")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是选择题出题专家。根据知识点描述，生成一道高质量的单选题。"
                "\n\n要求："
                "\n- 题干清晰，问题指向明确"
                "\n- 4 个选项，1 个正确 + 3 个干扰项"
                "\n- 干扰项从概念混淆、参数颠倒、边界反例、常见误解等角度设计"
                "\n- 干扰项必须看起来合理但确实错误"
                "\n- 正确答案唯一，可以合理排除其他 3 项"
                "\n- 附带详细解析"
                "\n\n目标难度：{difficulty}"
            ),
            (
                "user",
                "请根据以下知识点生成一道选择题：\n\n{knowledge_point}"
            ),
        ])

        prompt = prompt.partial(difficulty=difficulty)
        prompt = prompt.partial(knowledge_point=knowledge_point)

        llm = create_llm_client(config)
        from exam.agents.utils.structured import invoke_structured
        result = invoke_structured(llm, ChoiceQuestion, prompt.format_messages())

        # 转为下游兼容的 dict 格式
        question = {
            "question_type": "choice",
            "difficulty": difficulty,
            "source": task.get("section", ""),
            "topic": task.get("topic", ""),
            "stem": result.stem,
            "options": [
                f"A. {result.option_a}",
                f"B. {result.option_b}",
                f"C. {result.option_c}",
                f"D. {result.option_d}",
            ],
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        return {
            "generated_question": question,
        }

    return choice_generator_node


def create_fill_blank_generator(config: dict = None):
    """填空题生成器 —— 结构化输出"""

    def fill_blank_generator_node(state):
        knowledge_point = state.get("knowledge_point", "")
        fb = state.get("review_feedback", "")
        if fb:
            knowledge_point = knowledge_point + f"\n\n上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。"
        task = state.get("current_task", {})
        difficulty = task.get("difficulty", "medium")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是填空题出题专家。根据知识点描述，生成一道高质量的填空题。"
                "\n\n要求："
                "\n- 从知识点中选取一个不可替代的关键词/短语进行挖空"
                "\n- 挖掉后题干仍能读通"
                "\n- 答案唯一，不能有歧义"
                "\n- 答案应是简短的一个词、数字或短句"
                "\n- 用 ___ 表示空缺"
                "\n\n目标难度：{difficulty}"
            ),
            (
                "user",
                "请根据以下知识点生成一道填空题：\n\n{knowledge_point}"
            ),
        ])

        prompt = prompt.partial(difficulty=difficulty)
        prompt = prompt.partial(knowledge_point=knowledge_point)

        llm = create_llm_client(config)
        result = invoke_structured(llm, FillBlankQuestion, prompt.format_messages())

        question = {
            "question_type": "fill_blank",
            "difficulty": difficulty,
            "source": task.get("section", ""),
            "topic": task.get("topic", ""),
            "stem": result.stem,
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        return {
            "generated_question": question,
        }

    return fill_blank_generator_node


def create_short_answer_generator(config: dict = None):
    """简答题生成器 —— 结构化输出"""

    def short_answer_generator_node(state):
        knowledge_point = state.get("knowledge_point", "")
        fb = state.get("review_feedback", "")
        if fb:
            knowledge_point = knowledge_point + f"\n\n上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。"
        task = state.get("current_task", {})
        difficulty = task.get("difficulty", "medium")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是简答题出题专家。根据知识点描述，生成一道高质量的简答题。"
                "\n\n要求："
                "\n- 设问具体，考查理解而非记忆"
                "\n- 不能太宽（如'谈谈你对X的理解'不合格）"
                "\n- 也不能太窄（变成填空题）"
                "\n- 参考答案要有要点分解"
                "\n- 评分要点说明各要点分值"
                "\n\n目标难度：{difficulty}"
            ),
            (
                "user",
                "请根据以下知识点生成一道简答题：\n\n{knowledge_point}"
            ),
        ])

        prompt = prompt.partial(difficulty=difficulty)
        prompt = prompt.partial(knowledge_point=knowledge_point)

        llm = create_llm_client(config)
        result = invoke_structured(llm, ShortAnswerQuestion, prompt.format_messages())

        question = {
            "question_type": "short_answer",
            "difficulty": difficulty,
            "source": task.get("section", ""),
            "topic": task.get("topic", ""),
            "stem": result.stem,
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        return {
            "generated_question": question,
        }

    return short_answer_generator_node


def create_code_fill_generator(config: dict = None):
    """代码填空题生成器 —— 结构化输出"""

    def code_fill_generator_node(state):
        knowledge_point = state.get("knowledge_point", "")
        fb = state.get("review_feedback", "")
        if fb:
            knowledge_point = knowledge_point + f"\n\n上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。"
        task = state.get("current_task", {})
        difficulty = task.get("difficulty", "medium")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是代码填空题出题专家。根据知识点描述，生成一道高质量的代码填空题。"
                "\n\n要求："
                "\n- 题干包含完整的代码上下文（函数/代码段）"
                "\n- 从代码中选取 1-2 个关键的逻辑位置进行挖空"
                "\n- 挖掉的内容应是理解算法/逻辑的关键（函数名、参数、条件表达式等）"
                "\n- 答案唯一，不能有多种合理填法"
                "\n- 用 ___ 表示空缺"
                "\n- 附带解析，说明代码逻辑和考点"
                "\n\n目标难度：{difficulty}"
            ),
            (
                "user",
                "请根据以下知识点生成一道代码填空题：\n\n{knowledge_point}"
            ),
        ])

        prompt = prompt.partial(difficulty=difficulty)
        prompt = prompt.partial(knowledge_point=knowledge_point)

        llm = create_llm_client(config)
        result = invoke_structured(llm, CodeFillQuestion, prompt.format_messages())

        question = {
            "question_type": "code_fill",
            "difficulty": difficulty,
            "source": task.get("section", ""),
            "topic": task.get("topic", ""),
            "stem": result.stem,
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        return {
            "generated_question": question,
        }

    return code_fill_generator_node


def create_comprehensive_generator(config: dict = None):
    """综合题生成器 —— 结构化输出"""

    def comprehensive_generator_node(state):
        knowledge_point = state.get("knowledge_point", "")
        fb = state.get("review_feedback", "")
        if fb:
            knowledge_point = knowledge_point + f"\n\n上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。"
        task = state.get("current_task", {})
        difficulty = task.get("difficulty", "medium")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是综合题出题专家。根据知识点描述，生成一道高质量的综合题。"
                "\n\n要求："
                "\n- 设问考查综合能力：代码分析、运行推演、方案设计、多知识点串联等"
                "\n- 题干可以包含代码段、场景描述等"
                "\n- 参考答案分要点列出，逻辑清晰"
                "\n- 附带评分要点，说明各要点分值"
                "\n- 不能太泛（如'谈谈对X的理解'），要有明确的考查目标"
                "\n\n目标难度：{difficulty}"
            ),
            (
                "user",
                "请根据以下知识点生成一道综合题：\n\n{knowledge_point}"
            ),
        ])

        prompt = prompt.partial(difficulty=difficulty)
        prompt = prompt.partial(knowledge_point=knowledge_point)

        llm = create_llm_client(config)
        result = invoke_structured(llm, ComprehensiveQuestion, prompt.format_messages())

        question = {
            "question_type": "comprehensive",
            "difficulty": difficulty,
            "source": task.get("section", ""),
            "topic": task.get("topic", ""),
            "stem": result.stem,
            "correct_answer": result.correct_answer,
            "explanation": result.explanation,
        }

        return {
            "generated_question": question,
        }

    return comprehensive_generator_node
