from dataclasses import dataclass

from .request import ChiefEditorRequest


@dataclass(frozen=True, slots=True)
class ChiefEditorQuota:
    target_count: int
    focus_counts: tuple[int, ...]


def calculate_quota(request: ChiefEditorRequest) -> ChiefEditorQuota:
    """Calculate deterministic focus quotas for a request."""

    focus_count = len(request.focuses)
    target_count = request.target_count
    if target_count is None:
        target_count = focus_count * 2

    per_focus, extra_focuses = divmod(target_count, focus_count)
    focus_counts = tuple(
        per_focus + (1 if index < extra_focuses else 0)
        for index in range(focus_count)
    )
    return ChiefEditorQuota(
        target_count=target_count,
        focus_counts=focus_counts,
    )
