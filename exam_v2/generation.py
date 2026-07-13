"""出题流程的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Difficulty, MediaItem, Question, QuestionType


CHOICE_ANSWERS = frozenset({"A", "B", "C", "D"})


class GenerationMode(StrEnum):
    """出题模式。"""

    EXAM = "exam"
    PRACTICE = "practice"


class GenerationError(ValueError):
    """出题计划或结果不满足请求约束。"""


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
    """主编图产出的出题计划。"""

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


@dataclass(frozen=True, slots=True)
class QuestionDraft:
    """生成器产出的题目草稿，尚未分配最终题号。"""

    task_id: str
    question_type: QuestionType
    stem: str
    correct_answer: str
    source: str = ""
    topic: str = ""
    difficulty: Difficulty = Difficulty.MEDIUM
    options: tuple[str, ...] = ()
    explanation: str = ""
    media: tuple[MediaItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "media", tuple(self.media))

    def to_question(self, question_id: str) -> Question:
        return Question(
            id=question_id,
            question_type=self.question_type,
            stem=self.stem,
            correct_answer=self.correct_answer,
            source=self.source,
            topic=self.topic,
            difficulty=self.difficulty,
            options=self.options,
            explanation=self.explanation,
            media=self.media,
        )
