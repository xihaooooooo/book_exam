"""Offline recommendation evaluation over synthetic attempt playback cases."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from exam.evaluation.schemas import EvalFailure, EvalMetric, EvalReport
from exam.student_profile.profile_engine import build_profile, normalize_section_id
from exam.student_profile.recommendation import (
    build_recommendation_plan,
    recommendation_key,
)
from exam.student_profile.schemas import ERROR_TYPE_QUESTIONS
from exam.student_profile.storage import (
    init_attempts_db,
    init_error_labels_db,
    record_attempts_batch,
)


DEFAULT_THRESHOLDS = {
    "bkt_monotonic_pass": 1.00,
    "weak_topic_hit_rate": 0.80,
    "mastered_retire_rate": 0.80,
    "recommendation_reason_rate": 0.90,
    "error_to_type_match_rate": 0.80,
    "delta_mastery_valid_rate": 1.00,
}


@dataclass(frozen=True)
class RecommendationAttempt:
    section_id: str
    topic: str
    question_type: str
    difficulty: str
    is_correct: bool
    error_type: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecommendationAttempt":
        error_type = data.get("error_type")
        return cls(
            section_id=str(data.get("section_id", "")),
            topic=str(data.get("topic", "")),
            question_type=str(data.get("question_type", "")) or "choice",
            difficulty=str(data.get("difficulty", "")) or "medium",
            is_correct=bool(data.get("is_correct", False)),
            error_type=str(error_type) if error_type else None,
        )


@dataclass(frozen=True)
class RecommendationCase:
    case_id: str
    student_id: str
    scenario: str
    top_k: int
    attempts: list[RecommendationAttempt]
    expected_top_sections: set[str]
    expected_low_priority_sections: set[str]
    expected_question_types: dict[str, set[str]]
    expected_bkt_direction: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecommendationCase":
        return cls(
            case_id=str(data.get("case_id", "")),
            student_id=str(data.get("student_id", "")) or "eval_student",
            scenario=str(data.get("scenario", "")),
            top_k=int(data.get("top_k", 3) or 3),
            attempts=[
                RecommendationAttempt.from_dict(item)
                for item in data.get("attempts", [])
                if isinstance(item, dict)
            ],
            expected_top_sections=set(data.get("expected_top_sections") or []),
            expected_low_priority_sections=set(data.get("expected_low_priority_sections") or []),
            expected_question_types={
                str(section): set(types or [])
                for section, types in (data.get("expected_question_types") or {}).items()
            },
            expected_bkt_direction={
                str(section): str(direction)
                for section, direction in (data.get("expected_bkt_direction") or {}).items()
            },
        )


@dataclass
class RecommendationCaseResult:
    case: RecommendationCase
    bkt_states: list[Any]
    plan_items: list[Any]
    top_sections: list[str]
    error_map: dict[str, str]


def run_recommendation_eval(
    *,
    cases_file: str | Path = "evals/cases/recommendation_cases.json",
    case_id: str | None = None,
    limit: int | None = None,
    target_count: int = 8,
) -> EvalReport:
    """Run recommendation playback cases through BKT + Bandit ranking."""
    cases_path = Path(cases_file)
    cases = load_recommendation_cases(cases_path)
    cases = _filter_cases(cases, case_id)
    if limit is not None:
        cases = cases[: max(0, limit)]

    case_results: list[RecommendationCaseResult] = []
    failures: list[EvalFailure] = []

    for case in cases:
        try:
            case_results.append(_run_one_case(case, target_count=target_count))
        except Exception as exc:
            failures.append(EvalFailure(
                case_id=case.case_id,
                item_id=case.case_id,
                reason="推荐回放执行异常",
                evidence={"error": str(exc), "scenario": case.scenario},
            ))

    return evaluate_recommendation_results(
        cases=cases,
        case_results=case_results,
        setup_failures=failures,
        cases_path=cases_path,
        case_id=case_id,
        limit=limit,
        target_count=target_count,
    )


def load_recommendation_cases(path: str | Path) -> list[RecommendationCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"recommendation cases must be a list: {path}")
    return [RecommendationCase.from_dict(item) for item in data]


def evaluate_recommendation_results(
    *,
    cases: list[RecommendationCase],
    case_results: list[RecommendationCaseResult],
    setup_failures: list[EvalFailure],
    cases_path: Path,
    case_id: str | None,
    limit: int | None,
    target_count: int,
) -> EvalReport:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    created_at = datetime.now().isoformat(timespec="seconds")
    failures = list(setup_failures)

    bkt_checks = 0
    bkt_pass = 0
    weak_checks = 0
    weak_hits = 0
    retire_checks = 0
    retire_hits = 0
    reason_checks = 0
    reason_hits = 0
    type_checks = 0
    type_hits = 0
    delta_checks = 0
    delta_hits = 0

    for result in case_results:
        case = result.case
        bkt_by_section = _bkt_by_normalized_section(result.bkt_states)
        plan_by_section = _items_by_normalized_section(result.plan_items)
        top_sections = result.top_sections[: case.top_k]

        for raw_section, direction in case.expected_bkt_direction.items():
            bkt_checks += 1
            section = normalize_section_id(raw_section)
            bkt = bkt_by_section.get(section)
            if bkt is None:
                failures.append(_failure(case, raw_section, "缺少 BKT 状态", {
                    "expected_direction": direction,
                    "available_sections": sorted(bkt_by_section),
                }))
                continue
            delta = bkt.p_mastery - bkt.p_initial
            passed = delta > 0 if direction == "increase" else delta < 0
            if passed:
                bkt_pass += 1
            else:
                failures.append(_failure(case, raw_section, "BKT P(L) 方向不符合预期", {
                    "expected_direction": direction,
                    "p_initial": round(bkt.p_initial, 4),
                    "p_mastery": round(bkt.p_mastery, 4),
                    "delta": round(delta, 4),
                }))

        for raw_section in case.expected_top_sections:
            weak_checks += 1
            section = normalize_section_id(raw_section)
            if section in top_sections:
                weak_hits += 1
            else:
                failures.append(_failure(case, raw_section, "薄弱章节未进入推荐 TopK", {
                    "top_k": case.top_k,
                    "top_sections": top_sections,
                }))

        top_rank = _best_expected_rank(case.expected_top_sections, result.plan_items)
        for raw_section in case.expected_low_priority_sections:
            retire_checks += 1
            section = normalize_section_id(raw_section)
            low_rank = _section_rank(section, result.plan_items)
            if low_rank is not None and (top_rank is None or low_rank > top_rank):
                retire_hits += 1
            else:
                failures.append(_failure(case, raw_section, "低优先级章节排序未低于薄弱章节", {
                    "top_k": case.top_k,
                    "top_sections": top_sections,
                    "low_priority_rank": low_rank,
                    "best_expected_top_rank": top_rank,
                }))

        for item in result.plan_items:
            reason_checks += 1
            if str(item.reason_text or "").strip():
                reason_hits += 1
            else:
                failures.append(_failure(case, item.section_id, "推荐原因为空", {
                    "topic": item.topic,
                    "p_mastery": round(item.p_mastery, 4),
                }))

        for raw_section, expected_types in case.expected_question_types.items():
            type_checks += 1
            section = normalize_section_id(raw_section)
            item = plan_by_section.get(section)
            if item is None:
                failures.append(_failure(case, raw_section, "缺少推荐条目，无法检查错因题型匹配", {
                    "expected_question_types": sorted(expected_types),
                }))
                continue
            actual = set(item.question_types)
            if actual & expected_types:
                type_hits += 1
            else:
                failures.append(_failure(case, raw_section, "推荐题型不匹配主要错因", {
                    "expected_question_types": sorted(expected_types),
                    "actual_question_types": sorted(actual),
                    "dominant_error_type": item.dominant_error_type,
                    "canonical_types": ERROR_TYPE_QUESTIONS.get(item.dominant_error_type, []),
                }))

        for bkt in result.bkt_states:
            delta_checks += 1
            delta = bkt.p_mastery - bkt.p_initial
            if -1.0 <= delta <= 1.0 and 0.0 <= bkt.p_mastery <= 1.0:
                delta_hits += 1
            else:
                failures.append(_failure(case, bkt.section_id, "掌握度增量超出合理范围", {
                    "p_initial": bkt.p_initial,
                    "p_mastery": bkt.p_mastery,
                    "delta": delta,
                }))

    metrics = [
        _metric(
            "bkt_monotonic_pass",
            _safe_ratio(bkt_pass, bkt_checks, default=1.0),
            f"{bkt_pass}/{bkt_checks} 个 BKT 方向检查通过",
        ),
        _metric(
            "weak_topic_hit_rate",
            _safe_ratio(weak_hits, weak_checks, default=1.0),
            f"{weak_hits}/{weak_checks} 个薄弱章节进入 TopK",
        ),
        _metric(
            "mastered_retire_rate",
            _safe_ratio(retire_hits, retire_checks, default=1.0),
            f"{retire_hits}/{retire_checks} 个低优先级章节排序低于薄弱章节",
        ),
        _metric(
            "recommendation_reason_rate",
            _safe_ratio(reason_hits, reason_checks, default=1.0),
            f"{reason_hits}/{reason_checks} 个推荐条目包含原因",
        ),
        _metric(
            "error_to_type_match_rate",
            _safe_ratio(type_hits, type_checks, default=1.0),
            f"{type_hits}/{type_checks} 个推荐题型匹配错因预期",
        ),
        _metric(
            "delta_mastery_valid_rate",
            _safe_ratio(delta_hits, delta_checks, default=1.0),
            f"{delta_hits}/{delta_checks} 个 BKT delta 在合理范围内",
        ),
    ]

    passed_metric_count = sum(1 for metric in metrics if metric.passed)
    summary = (
        f"共回放 {len(case_results)}/{len(cases)} 个推荐样本，"
        f"{passed_metric_count}/{len(metrics)} 个指标通过，失败项 {len(failures)} 条。"
    )

    return EvalReport(
        eval_type="recommendation",
        run_id=run_id,
        created_at=created_at,
        metrics=metrics,
        failures=failures,
        summary=summary,
        metadata={
            "cases_file": str(cases_path),
            "case_id": case_id or "",
            "limit": limit,
            "target_count": target_count,
            "total_cases": len(cases),
            "completed_cases": len(case_results),
            "rank_strategy": "mean",
            "temp_db_policy": "TemporaryDirectory; never writes cache/attempts.db",
            "top_items": {
                result.case.case_id: [
                    {
                        "section_id": item.section_id,
                        "topic": item.topic,
                        "p_mastery": round(item.p_mastery, 4),
                        "bandit_score": round(item.bandit_score, 4),
                        "question_types": item.question_types,
                        "reason_text": item.reason_text,
                    }
                    for item in result.plan_items[: result.case.top_k]
                ]
                for result in case_results
            },
        },
    )


def _run_one_case(
    case: RecommendationCase,
    *,
    target_count: int,
) -> RecommendationCaseResult:
    with tempfile.TemporaryDirectory(prefix="book_exam_eval_") as tmp_dir:
        db_path = str(Path(tmp_dir) / "attempts.db")
        init_attempts_db(db_path)
        init_error_labels_db(db_path)
        _write_case_attempts(db_path, case)

        profile = build_profile(case.student_id, db_path, mastery_backend="bkt")
        bkt_states = [
            topic.bkt_state
            for topic in profile.topics
            if topic.bkt_state is not None
        ]
        error_map: dict[str, str] = {}
        for topic in profile.topics:
            if topic.dominant_error_type:
                error_map[recommendation_key(topic.section_id, topic.topic)] = topic.dominant_error_type
                error_map[topic.section_id] = topic.dominant_error_type

        plan = build_recommendation_plan(
            bkt_states,
            error_map,
            case.student_id,
            target_count=target_count,
            rank_strategy="mean",
        )
        top_sections = [normalize_section_id(item.section_id) for item in plan.items]

        return RecommendationCaseResult(
            case=case,
            bkt_states=bkt_states,
            plan_items=plan.items,
            top_sections=top_sections,
            error_map=error_map,
        )


def _write_case_attempts(db_path: str, case: RecommendationCase) -> None:
    records = []
    for index, attempt in enumerate(case.attempts, start=1):
        records.append({
            "student_id": case.student_id,
            "section_id": attempt.section_id,
            "topic": attempt.topic,
            "question_type": attempt.question_type,
            "difficulty": attempt.difficulty,
            "stem": f"{case.case_id} synthetic question {index}",
            "student_answer": "synthetic",
            "correct_answer": "synthetic",
            "explanation": "offline recommendation evaluation synthetic attempt",
            "is_correct": attempt.is_correct,
            "duration_sec": 60,
            "confidence": 3 if not attempt.is_correct else 4,
            "reason": "synthetic playback",
            "method": "eval",
            "error_type": attempt.error_type or "",
            "error_evidence": "synthetic error evidence" if attempt.error_type else "",
            "error_suggestion": "synthetic suggestion" if attempt.error_type else "",
            "diagnosis_confidence": 0.9,
        })
    record_attempts_batch(db_path, records)
    _stabilize_created_at(db_path)


def _stabilize_created_at(db_path: str) -> None:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT id FROM attempts ORDER BY id").fetchall()
        for offset, (attempt_id,) in enumerate(rows):
            db.execute(
                "UPDATE attempts SET created_at = datetime('2026-01-01 00:00:00', ?) WHERE id = ?",
                (f"+{offset} minutes", attempt_id),
            )
        db.commit()
    finally:
        db.close()


def _filter_cases(
    cases: list[RecommendationCase],
    case_id: str | None,
) -> list[RecommendationCase]:
    if not case_id:
        return cases
    selected = [case for case in cases if case.case_id == case_id]
    if not selected:
        raise ValueError(f"recommendation case not found: {case_id}")
    return selected


def _bkt_by_normalized_section(bkt_states: list[Any]) -> dict[str, Any]:
    return {
        normalize_section_id(bkt.section_id): bkt
        for bkt in bkt_states
    }


def _items_by_normalized_section(items: list[Any]) -> dict[str, Any]:
    return {
        normalize_section_id(item.section_id): item
        for item in items
    }


def _section_rank(section: str, items: list[Any]) -> int | None:
    for index, item in enumerate(items, start=1):
        if normalize_section_id(item.section_id) == section:
            return index
    return None


def _best_expected_rank(expected_sections: set[str], items: list[Any]) -> int | None:
    ranks = [
        rank for rank in (
            _section_rank(normalize_section_id(section), items)
            for section in expected_sections
        )
        if rank is not None
    ]
    return min(ranks) if ranks else None


def _metric(name: str, value: float, detail: str) -> EvalMetric:
    threshold = DEFAULT_THRESHOLDS[name]
    return EvalMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        detail=detail,
    )


def _failure(
    case: RecommendationCase,
    item_id: str,
    reason: str,
    evidence: dict[str, Any],
) -> EvalFailure:
    return EvalFailure(
        case_id=case.case_id,
        item_id=item_id,
        reason=reason,
        evidence={
            "scenario": case.scenario,
            **evidence,
        },
    )


def _safe_ratio(numerator: int, denominator: int, *, default: float = 0.0) -> float:
    if denominator <= 0:
        return default
    return numerator / denominator
