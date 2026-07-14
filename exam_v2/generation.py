"""单题生成阶段的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Difficulty, MediaItem, Question, QuestionType


CHOICE_ANSWERS = frozenset({"A", "B", "C", "D"})


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
