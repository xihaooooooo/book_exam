from dataclasses import dataclass
from typing import Sequence


class ChiefEditorRequestError(ValueError):
    """Raised when a chief editor request is invalid."""


@dataclass(frozen=True, slots=True)
class ChiefEditorRequest:
    """Validated inputs for a chief editor planning request."""

    focuses: Sequence[str]
    target_count: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.focuses, (str, bytes)):
            raise ChiefEditorRequestError("focuses must be a sequence of strings")

        normalized_focuses: list[str] = []
        for focus in self.focuses:
            if not isinstance(focus, str):
                raise ChiefEditorRequestError("each focus must be a string")

            normalized_focus = focus.strip()
            if not normalized_focus:
                raise ChiefEditorRequestError("focuses must not contain blank values")
            normalized_focuses.append(normalized_focus)

        if not normalized_focuses:
            raise ChiefEditorRequestError("focuses must contain at least one value")

        if self.target_count is not None:
            if isinstance(self.target_count, bool) or not isinstance(self.target_count, int):
                raise ChiefEditorRequestError("target_count must be an integer or None")
            if self.target_count < len(normalized_focuses):
                raise ChiefEditorRequestError(
                    "target_count must be at least the number of focuses"
                )

        object.__setattr__(self, "focuses", tuple(normalized_focuses))
