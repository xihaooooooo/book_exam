"""题目生成器：结构化输出选择题、填空题、简答题"""

import re

from langchain_core.prompts import ChatPromptTemplate
from exam.agents.utils.agent_utils import create_llm_client
from exam.agents.utils.structured import invoke_structured
from exam.agents.schemas import ChoiceQuestion, FillBlankQuestion, ShortAnswerQuestion, CodeFillQuestion, ComprehensiveQuestion


LATEX_REQUIREMENT = (
    "\n- 数学表达式和公式必须使用 LaTeX 语法：内联公式用 $...$ 包裹，"
    "单独成行的公式用 $$...$$ 包裹"
)
MEDIA_REQUIREMENT = (
    "\n- 如果知识点中包含 [插图: fig1] 等插图占位，只有题目确实依赖该图时才引用；"
    "引用时在题干中写 [media:fig1]，不要编造图片内容"
    "\n- 所有 media 都是题图或结构预览，学生仍通过选择、填空、简答、代码填空作答；"
    "不要要求学生点击、拖拽、连线、编辑或在图上标注"
    "\n- 使用任何 media 时，题干必须明确写出“根据图/结合图/观察图中信息”等看图作答语义，"
    "并包含对应 [media:id] 引用；解析应说明图中依据"
    "\n- 只有媒体描述明确、不是占位文本时才使用题图；如果描述包含“待生成”“占位”"
    "或“未提取到足够相邻文本”，不要围绕该图出题"
    "\n- 如果知识点适合流程图、结构图、状态转换图或时序图，可生成 Mermaid 媒体："
    "media 项使用 type=\"mermaid\"、id=\"diagram1\"、content 填合法 Mermaid 源码、"
    "description 用中文概括图意，并在题干中写 [media:diagram1]"
    "\n- Mermaid 只允许 flowchart、graph TD/LR、sequenceDiagram、gantt 子集；"
    "content 中不要使用 Markdown 代码围栏"
    "\n- 如需表达树、图、队列、内存块等结构化可视对象，可生成 Canvas 媒体："
    "media 项使用 type=\"canvas\"、id=\"canvas1\"、config 填结构化配置、description 概括图意，"
    "并在题干中写 [media:canvas1]"
    "\n- 当前 Canvas 只作为只读结构预览；题目必须仍可通过文本、选择、填空或简答作答，"
    "不要要求学生拖拽、连线、点击节点或直接编辑画布"
)
CONTENT_REQUIREMENTS = LATEX_REQUIREMENT + MEDIA_REQUIREMENT

FIGURE_BLOCK_RE = re.compile(r"\[插图:\s*([^\]]+)\]\s*(.*?)(?=\n\s*\[插图:|\Z)", re.S)
FIGURE_FILE_RE = re.compile(r"文件\s+([^\s。；;，,]+)")
GENERIC_FIGURE_TERMS = ("下图", "上图", "插图", "图片", "图示", "图中", "图表")
UNUSABLE_MEDIA_DESCRIPTION_TERMS = ("待生成", "占位", "未提取到足够相邻文本")


def _attach_media(question: dict, result) -> dict:
    media = getattr(result, "media", None)
    if not media:
        return question

    items = []
    for item in media:
        if hasattr(item, "model_dump"):
            item = item.model_dump(exclude_none=True)
        elif isinstance(item, dict):
            item = {k: v for k, v in item.items() if v is not None}
        else:
            continue
        if item.get("type"):
            items.append(item)

    if items:
        question["media"] = items
    return question


def _extract_media_from_knowledge(knowledge_point: str) -> list[dict]:
    items = []
    for match in FIGURE_BLOCK_RE.finditer(knowledge_point or ""):
        media_id = match.group(1).strip()
        block = match.group(2).strip()
        file_match = FIGURE_FILE_RE.search(block)
        if not media_id or not file_match:
            continue
        description = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if _unusable_media_description(description):
            continue
        items.append({
            "id": media_id,
            "type": "image",
            "src": file_match.group(1).strip(),
            "description": description,
        })
    return items


def _unusable_media_description(description: str) -> bool:
    text = str(description or "")
    return any(term in text for term in UNUSABLE_MEDIA_DESCRIPTION_TERMS)


def _attach_referenced_media(question: dict, knowledge_point: str) -> dict:
    candidates = _extract_media_from_knowledge(knowledge_point)
    if not candidates:
        return question

    stem = str(question.get("stem") or "")
    explicit = []
    for item in candidates:
        media_id = item["id"]
        if media_id in stem or f"[media:{media_id}]" in stem or f"[插图: {media_id}]" in stem:
            explicit.append(item)

    selected = explicit
    if not selected and any(term in stem for term in GENERIC_FIGURE_TERMS):
        selected = candidates[:1]
    if not selected:
        return question

    media = question.get("media")
    if not isinstance(media, list):
        media = []
    existing_ids = {item.get("id") for item in media if isinstance(item, dict)}
    for item in selected:
        if item["id"] not in existing_ids:
            media.append(item)
            existing_ids.add(item["id"])
    question["media"] = media
    return question


def _finalize_question(question: dict, result, knowledge_point: str) -> dict:
    _attach_media(question, result)
    _attach_referenced_media(question, knowledge_point)
    return question


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
                + CONTENT_REQUIREMENTS +
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
        _finalize_question(question, result, knowledge_point)

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
                + CONTENT_REQUIREMENTS +
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
        _finalize_question(question, result, knowledge_point)

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
                + CONTENT_REQUIREMENTS +
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
        _finalize_question(question, result, knowledge_point)

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
                + CONTENT_REQUIREMENTS +
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
        _finalize_question(question, result, knowledge_point)

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
                + CONTENT_REQUIREMENTS +
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
        _finalize_question(question, result, knowledge_point)

        return {
            "generated_question": question,
        }

    return comprehensive_generator_node
