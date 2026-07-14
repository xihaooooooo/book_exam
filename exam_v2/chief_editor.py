"""主编计划阶段的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .errors import GenerationError
from .models import Difficulty, QuestionType


class GenerationMode(StrEnum):
    """出题模式。"""

    EXAM = "exam"
    PRACTICE = "practice"


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """一次出题请求。"""

    mode: GenerationMode = GenerationMode.EXAM
    count: int = 10
    question_types: tuple[QuestionType, ...] = ()
    difficulty: Difficulty | None = None
    book_id: str = "default"
    source_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("出题数量必须大于 0")
        object.__setattr__(self, "question_types", tuple(self.question_types))
        object.__setattr__(self, "source_scope", tuple(self.source_scope))


class ChiefEditorTask(BaseModel):
    """LLM 主编输出的一道计划项。"""

    source: str = Field(description="主来源小节 ID，例如 2.3")
    topic: str = Field(description="知识点名称或短语")
    question_type: str = Field(description="题型，可用中文或英文")
    difficulty: str = Field(description="难度，可用中文或英文")
    requirements: list[str] = Field(
        default_factory=list,
        description="0-3 条短要求，可包含考法、避坑、干扰项方向",
    )


class ChiefEditorPlan(BaseModel):
    """LLM 主编输出的整体计划。"""

    notes: str = Field(default="", description="1-3 句计划说明")
    tasks: list[ChiefEditorTask] = Field(description="出题任务清单")


@dataclass(frozen=True, slots=True)
class QuestionTask:
    """主编分配给单题流水线的一道题任务。"""

    task_id: str
    question_type: QuestionType
    source: str
    difficulty: Difficulty = Difficulty.MEDIUM
    topic: str = ""
    requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("题目任务必须有 task_id")
        object.__setattr__(self, "requirements", tuple(self.requirements))


@dataclass(frozen=True, slots=True)
class ExamPlan:
    """主编产出的出题计划。"""

    request: GenerationRequest
    tasks: tuple[QuestionTask, ...]
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(self.tasks))
        if len(self.tasks) != self.request.count:
            raise GenerationError(
                f"出题计划需要 {self.request.count} 个任务，实际有 {len(self.tasks)} 个",
            )
        task_ids = [task.task_id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise GenerationError("出题计划里存在重复 task_id")


def build_exam_plan(request: GenerationRequest, chief_plan: ChiefEditorPlan) -> ExamPlan:
    """把 LLM 主编计划转换成系统内部正式计划。"""
    if request.mode != GenerationMode.EXAM:
        raise GenerationError(f"v2 第一阶段暂不支持出题模式: {request.mode.value}")

    effective_types = request.question_types or (QuestionType.CHOICE,)
    tasks = tuple(
        _to_question_task(index, raw_task, request, effective_types)
        for index, raw_task in enumerate(chief_plan.tasks, start=1)
    )
    return ExamPlan(
        request=request,
        tasks=tasks,
        notes=chief_plan.notes.strip(),
    )


def _to_question_task(
    index: int,
    raw_task: ChiefEditorTask,
    request: GenerationRequest,
    effective_types: tuple[QuestionType, ...],
) -> QuestionTask:
    source = raw_task.source.strip()
    topic = raw_task.topic.strip()
    if not source:
        raise GenerationError(f"任务 {index} 缺少 source")
    if not topic:
        raise GenerationError(f"任务 {index} 缺少 topic")
    if request.source_scope and source not in request.source_scope:
        raise GenerationError(f"任务 {index} 的 source 不在请求范围内: {source}")

    question_type = normalize_question_type(raw_task.question_type)
    if question_type not in effective_types:
        allowed = ", ".join(item.value for item in effective_types)
        raise GenerationError(f"任务 {index} 的题型不在允许范围内: {question_type.value}，允许: {allowed}")

    difficulty = normalize_difficulty(raw_task.difficulty)
    if request.difficulty and difficulty != request.difficulty:
        raise GenerationError(f"任务 {index} 的难度不符合请求: {difficulty.value}")

    requirements = tuple(
        item.strip()
        for item in raw_task.requirements
        if isinstance(item, str) and item.strip()
    )

    return QuestionTask(
        task_id=f"task-{index:03d}",
        question_type=question_type,
        source=source,
        difficulty=difficulty,
        topic=topic,
        requirements=requirements,
    )


def normalize_question_type(raw: str) -> QuestionType:
    """把主编输出的中文/英文题型归一化。"""
    value = raw.strip().lower()
    if value in {"choice", "single_choice", "选择", "选择题", "单选", "单选题"}:
        return QuestionType.CHOICE
    if value in {"fill_blank", "fill", "blank", "填空", "填空题"}:
        return QuestionType.FILL_BLANK
    if value in {"short_answer", "short", "简答", "简答题", "问答", "问答题"}:
        return QuestionType.SHORT_ANSWER
    if value in {"code_fill", "code_blank", "代码填空", "代码填空题"}:
        return QuestionType.CODE_FILL
    if value in {"comprehensive", "综合", "综合题"}:
        return QuestionType.COMPREHENSIVE
    raise GenerationError(f"未知题型: {raw}")


def normalize_difficulty(raw: str) -> Difficulty:
    """把主编输出的中文/英文难度归一化。"""
    value = raw.strip().lower()
    if value in {"easy", "简单", "容易", "基础"}:
        return Difficulty.EASY
    if value in {"medium", "normal", "中等", "普通", "适中"}:
        return Difficulty.MEDIUM
    if value in {"hard", "difficult", "困难", "较难", "难"}:
        return Difficulty.HARD
    raise GenerationError(f"未知难度: {raw}")


def create_chief_editor_plan_messages(
    request: GenerationRequest,
    research_summary: str,
) -> list[BaseMessage]:
    """构造主编结构化计划节点的消息。"""
    return [
        SystemMessage(content=(
            "你是考试出题主编。你将根据研究摘要输出结构化出题计划。"
            "只输出 JSON，不要用 ``` 包裹，不要输出说明文字。"
            "不要写题干、选项、答案或解析。"
            "tasks 数量必须等于请求 count。"
            "每个 task 只包含 source、topic、question_type、difficulty、requirements。"
            "source 必须是一个主来源小节 ID。"
            "topic 必须是知识点名称或短语。"
            "requirements 为 0-3 条短要求，可包含考法、避坑、干扰项方向。"
        )),
        HumanMessage(content=(
            f"{format_generation_request(request)}\n"
            f"\n研究摘要：\n{research_summary}\n"
            "\n请输出 JSON 格式：\n"
            "{\n"
            '  "notes": "1-3 句计划说明",\n'
            '  "tasks": [\n'
            "    {\n"
            '      "source": "2.3",\n'
            '      "topic": "知识点名称",\n'
            '      "question_type": "choice",\n'
            '      "difficulty": "medium",\n'
            '      "requirements": ["短要求"]\n'
            "    }\n"
            "  ]\n"
            "}"
        )),
    ]


def create_chief_editor_research_messages(request: GenerationRequest) -> list[BaseMessage]:
    """构造主编研究节点的消息。"""
    return [
        SystemMessage(content=(
            "你是考试出题主编的研究员。你负责查询教材，为后续计划节点准备研究摘要。"
            "只能围绕当前请求中的 book_id 查阅教材；需要了解教材时调用工具。"
            "当你已经查够资料时，停止调用工具，只输出研究摘要。"
            "研究摘要必须包含候选 source、候选 topic、难度建议和 source_scope 约束。"
            "不要输出题目计划，不要输出题干、选项、答案或解析。"
        )),
        HumanMessage(content=format_generation_request(request)),
    ]


def parse_chief_editor_plan(content: str) -> ChiefEditorPlan:
    """解析主编结构化计划 JSON。"""
    import json

    text = content.strip()
    try:
        return ChiefEditorPlan(**json.loads(text, strict=False))
    except Exception as exc:
        raise GenerationError(f"无法解析主编结构化计划: {text[:300]}") from exc


def format_generation_request(request: GenerationRequest) -> str:
    """把出题请求格式化给主编节点。"""
    question_types = ", ".join(item.value for item in request.question_types) or "不限"
    difficulty = request.difficulty.value if request.difficulty else "不限"
    source_scope = ", ".join(request.source_scope) or "不限"
    return (
        "请为以下请求制定出题计划：\n"
        f"- book_id: {request.book_id}\n"
        f"- mode: {request.mode.value}\n"
        f"- count: {request.count}\n"
        f"- question_types: {question_types}\n"
        f"- difficulty: {difficulty}\n"
        f"- source_scope: {source_scope}\n"
    )
