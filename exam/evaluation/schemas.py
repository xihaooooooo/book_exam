"""Shared schemas for offline evaluation reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvalMetric:
    name: str
    value: float
    threshold: float | None = None
    passed: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalFailure:
    case_id: str
    item_id: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    eval_type: str
    run_id: str
    created_at: str
    metrics: list[EvalMetric]
    failures: list[EvalFailure]
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_type": self.eval_type,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "failures": [failure.to_dict() for failure in self.failures],
            "summary": self.summary,
            "metadata": self.metadata,
        }
