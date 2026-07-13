"""题目、作答和判题相关的核心领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class QuestionType(StrEnum):
    CHOICE = "choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    CODE_FILL = "code_fill"
    COMPREHENSIVE = "comprehensive"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class MediaItem:
    """题目中图片、图表或交互素材的文本化描述。"""

    id: str
    type: str
    src: str = ""
    description: str = ""
    content: str = ""
    metadata: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
    )


@dataclass(frozen=True, slots=True)
class Question:
    """学生作答前的一道已生成题目。"""

    id: str
    question_type: QuestionType
    stem: str
    correct_answer: str
    source: str = ""
    topic: str = ""
    difficulty: Difficulty = Difficulty.MEDIUM
    options: tuple[str, ...] = ()
    explanation: str = ""
    media: tuple[MediaItem, ...] = ()

    @property
    def is_objective(self) -> bool:
        return self.question_type in {
            QuestionType.CHOICE,
            QuestionType.FILL_BLANK,
        }


@dataclass(frozen=True, slots=True)
class SubmittedAnswer:
    """学生答案，以及判题所需的题目上下文。"""

    question_id: str
    question_type: QuestionType
    student_answer: str
    correct_answer: str
    stem: str = ""
    source: str = ""
    topic: str = ""
    difficulty: Difficulty = Difficulty.MEDIUM
    options: tuple[str, ...] = ()
    explanation: str = ""
    media: tuple[MediaItem, ...] = ()


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """单道题作答的判题结果。"""

    question_id: str
    is_correct: bool
    reason: str
    method: str = "rule"
    correct_answer: str = ""
    error_type: str | None = None
    error_evidence: str = ""
    error_suggestion: str = ""
    confidence: float | None = None
