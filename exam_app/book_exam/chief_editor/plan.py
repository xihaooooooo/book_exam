from dataclasses import dataclass
from typing import Sequence


class ChiefEditorPlanError(ValueError):
    """Raised when a chief editor plan has an invalid structure."""


@dataclass(frozen=True, slots=True)
class QuestionTask:
    """A brief for generating one broad-form short-answer question."""

    focus_index: int
    source_pages: Sequence[int]
    task_brief: str

    def __post_init__(self) -> None:
        if isinstance(self.focus_index, bool) or not isinstance(self.focus_index, int):
            raise ChiefEditorPlanError("focus_index must be an integer")
        if self.focus_index < 0:
            raise ChiefEditorPlanError("focus_index must not be negative")

        if isinstance(self.source_pages, (str, bytes)):
            raise ChiefEditorPlanError("source_pages must be a sequence of integers")

        normalized_pages: list[int] = []
        for page_number in self.source_pages:
            if isinstance(page_number, bool) or not isinstance(page_number, int):
                raise ChiefEditorPlanError("each source page must be an integer")
            if page_number < 1:
                raise ChiefEditorPlanError("source page numbers must be positive")
            normalized_pages.append(page_number)

        if not normalized_pages:
            raise ChiefEditorPlanError("source_pages must contain at least one page")

        if not isinstance(self.task_brief, str):
            raise ChiefEditorPlanError("task_brief must be a string")
        normalized_brief = self.task_brief.strip()
        if not normalized_brief:
            raise ChiefEditorPlanError("task_brief must not be blank")

        object.__setattr__(self, "source_pages", tuple(normalized_pages))
        object.__setattr__(self, "task_brief", normalized_brief)


@dataclass(frozen=True, slots=True)
class ChiefEditorPlan:
    tasks: Sequence[QuestionTask]

    def __post_init__(self) -> None:
        if isinstance(self.tasks, (str, bytes)):
            raise ChiefEditorPlanError("tasks must be a sequence of question tasks")

        normalized_tasks = tuple(self.tasks)
        if not normalized_tasks:
            raise ChiefEditorPlanError("tasks must contain at least one question task")
        if any(not isinstance(task, QuestionTask) for task in normalized_tasks):
            raise ChiefEditorPlanError("each task must be a QuestionTask")

        object.__setattr__(self, "tasks", normalized_tasks)
