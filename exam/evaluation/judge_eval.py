"""Offline JudgeGraph agreement evaluation over fixed golden cases."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from exam.evaluation.schemas import EvalFailure, EvalMetric, EvalReport
from exam.graph.judge_graph import JudgeGraph
from exam.student_profile.schemas import ERROR_TYPES


OBJECTIVE_TYPES = {"choice", "fill_blank"}
SUBJECTIVE_TYPES = {"short_answer", "comprehensive", "code_fill"}

DEFAULT_THRESHOLDS = {
    "objective_accuracy": 0.98,
    "subjective_agreement": 0.80,
    "false_positive_rate": 0.05,
    "false_negative_rate": 0.05,
    "error_type_valid_rate": 0.95,
    "diagnosis_completeness": 0.80,
    "fallback_pass_rate": 1.00,
}

@dataclass(frozen=True)
class JudgeCase:
    case_id: str
    question_type: str
    stem: str
    correct_answer: str
    student_answer: str
    expected_correct: bool
    expected_error_type: str | None
    accepted_error_types: set[str]
    options: list[Any]
    explanation: str
    difficulty: str
    judge_path: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JudgeCase":
        expected_error_type = data.get("expected_error_type")
        accepted = set(data.get("accepted_error_types") or [])
        if expected_error_type:
            accepted.add(str(expected_error_type))
        return cls(
            case_id=str(data.get("case_id", "")),
            question_type=str(data.get("question_type", "")),
            stem=str(data.get("stem", "")),
            correct_answer=str(data.get("correct_answer", "")),
            student_answer=str(data.get("student_answer", "")),
            expected_correct=bool(data.get("expected_correct", False)),
            expected_error_type=str(expected_error_type) if expected_error_type else None,
            accepted_error_types=accepted,
            options=list(data.get("options") or []),
            explanation=str(data.get("explanation", "")),
            difficulty=str(data.get("difficulty", "")) or "medium",
            judge_path=str(data.get("judge_path", "")),
        )

    def to_answer(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question_type": self.question_type,
            "stem": self.stem,
            "options": copy.deepcopy(self.options),
            "correct_answer": self.correct_answer,
            "student_answer": self.student_answer,
            "explanation": self.explanation,
            "difficulty": self.difficulty,
        }

    @property
    def is_objective(self) -> bool:
        return self.question_type in OBJECTIVE_TYPES

    @property
    def is_subjective(self) -> bool:
        return self.question_type in SUBJECTIVE_TYPES


def run_judge_eval(
    *,
    cases_file: str | Path = "evals/cases/judge_cases.json",
    llm_client: Any = None,
    case_id: str | None = None,
    limit: int | None = None,
) -> EvalReport:
    """Run JudgeGraph against golden cases and return metrics."""
    cases_path = Path(cases_file)
    cases = load_judge_cases(cases_path)
    cases = _filter_cases(cases, case_id)
    if limit is not None:
        cases = cases[: max(0, limit)]

    results, invoke_error = _invoke_judge_graph(cases, llm_client=llm_client)
    fallback_pass_rate, fallback_error = _run_fallback_probe(cases)

    return evaluate_judge_results(
        cases=cases,
        results=results,
        cases_path=cases_path,
        invoke_error=invoke_error,
        fallback_pass_rate=fallback_pass_rate,
        fallback_error=fallback_error,
        case_id=case_id,
        limit=limit,
        llm_configured=llm_client is not None,
    )


def load_judge_cases(path: str | Path) -> list[JudgeCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"judge cases must be a list: {path}")
    return [JudgeCase.from_dict(item) for item in data]


def evaluate_judge_results(
    *,
    cases: list[JudgeCase],
    results: list[dict[str, Any]],
    cases_path: Path,
    invoke_error: str,
    fallback_pass_rate: float,
    fallback_error: str,
    case_id: str | None,
    limit: int | None,
    llm_configured: bool,
) -> EvalReport:
    total = len(cases)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    created_at = datetime.now().isoformat(timespec="seconds")
    failures: list[EvalFailure] = []

    if invoke_error:
        failures.append(EvalFailure(
            case_id=case_id or "judge",
            item_id="JudgeGraph",
            reason="JudgeGraph 执行异常",
            evidence={"error": invoke_error},
        ))

    pairs = list(zip(cases, results))
    if total and len(results) != total:
        failures.append(EvalFailure(
            case_id=case_id or "judge",
            item_id="answers",
            reason="判题结果数量与样本数量不一致",
            evidence={"cases": total, "results": len(results)},
        ))

    objective_pairs = [(case, result) for case, result in pairs if case.is_objective]
    subjective_pairs = [(case, result) for case, result in pairs if case.is_subjective]

    objective_ok = _count_prediction_matches(objective_pairs)
    subjective_ok = _count_prediction_matches(subjective_pairs)

    expected_wrong_pairs = [(case, result) for case, result in pairs if not case.expected_correct]
    expected_correct_pairs = [(case, result) for case, result in pairs if case.expected_correct]
    false_positive = [
        (case, result) for case, result in expected_wrong_pairs
        if _predicted_correct(result)
    ]
    false_negative = [
        (case, result) for case, result in expected_correct_pairs
        if not _predicted_correct(result)
    ]

    diagnosis_expected = llm_configured
    valid_error_types = [
        (case, result) for case, result in expected_wrong_pairs
        if _predicted_error_type(result) in ERROR_TYPES
    ]
    complete_diagnoses = [
        (case, result) for case, result in expected_wrong_pairs
        if _has_complete_diagnosis(result)
    ]

    _collect_prediction_failures(failures, pairs)
    if diagnosis_expected:
        _collect_error_type_failures(failures, expected_wrong_pairs)
        _collect_diagnosis_failures(failures, expected_wrong_pairs)

    if fallback_error:
        failures.append(EvalFailure(
            case_id=case_id or "judge",
            item_id="fallback",
            reason="LLM 不可用降级路径异常",
            evidence={"error": fallback_error},
        ))

    metrics = [
        _metric_higher(
            "objective_accuracy",
            _safe_div(objective_ok, len(objective_pairs)),
            f"{objective_ok}/{len(objective_pairs)} 道客观题判定与 golden 一致",
        ),
        _metric_higher(
            "subjective_agreement",
            _safe_div(subjective_ok, len(subjective_pairs)),
            f"{subjective_ok}/{len(subjective_pairs)} 道主观题判定与 golden 一致",
        ),
        _metric_lower(
            "false_positive_rate",
            _safe_div(len(false_positive), len(expected_wrong_pairs)),
            f"{len(false_positive)}/{len(expected_wrong_pairs)} 道实际错误样本被判为正确",
        ),
        _metric_lower(
            "false_negative_rate",
            _safe_div(len(false_negative), len(expected_correct_pairs)),
            f"{len(false_negative)}/{len(expected_correct_pairs)} 道实际正确样本被判为错误",
        ),
        _diagnosis_metric(
            "error_type_valid_rate",
            _safe_div(len(valid_error_types), len(expected_wrong_pairs)),
            f"{len(valid_error_types)}/{len(expected_wrong_pairs)} 道错误样本产出合法错因",
            diagnosis_expected,
        ),
        _diagnosis_metric(
            "diagnosis_completeness",
            _safe_div(len(complete_diagnoses), len(expected_wrong_pairs)),
            f"{len(complete_diagnoses)}/{len(expected_wrong_pairs)} 道错误样本含 evidence 和 suggestion",
            diagnosis_expected,
        ),
        _metric_higher(
            "fallback_pass_rate",
            fallback_pass_rate,
            "LLM 未配置时 JudgeGraph 能稳定返回结果",
        ),
    ]

    passed_metric_count = sum(1 for metric in metrics if metric.passed)
    summary = (
        f"共评测 {total} 道判题样本，{passed_metric_count}/{len(metrics)} 个指标通过，"
        f"失败项 {len(failures)} 条。"
    )

    return EvalReport(
        eval_type="judge",
        run_id=run_id,
        created_at=created_at,
        metrics=metrics,
        failures=failures,
        summary=summary,
        metadata={
            "cases_file": str(cases_path),
            "case_id": case_id or "",
            "limit": limit,
            "total_cases": total,
            "llm_configured": llm_configured,
            "objective_cases": len(objective_pairs),
            "subjective_cases": len(subjective_pairs),
            "confusion_matrix": {
                "true_positive": sum(
                    1 for case, result in expected_correct_pairs
                    if _predicted_correct(result)
                ),
                "false_positive": len(false_positive),
                "true_negative": sum(
                    1 for case, result in expected_wrong_pairs
                    if not _predicted_correct(result)
                ),
                "false_negative": len(false_negative),
            },
        },
    )


def _invoke_judge_graph(
    cases: list[JudgeCase],
    *,
    llm_client: Any,
) -> tuple[list[dict[str, Any]], str]:
    answers = [case.to_answer() for case in cases]
    try:
        result = JudgeGraph(llm_client=llm_client).invoke({
            "student_id": "eval_judge_student",
            "answers": answers,
        })
        judged = result.get("answers", []) if isinstance(result, dict) else []
        return judged if isinstance(judged, list) else [], ""
    except Exception as exc:
        return [], str(exc)


def _run_fallback_probe(cases: list[JudgeCase]) -> tuple[float, str]:
    results, error = _invoke_judge_graph(cases, llm_client=None)
    if error:
        return 0.0, error
    if not cases:
        return 0.0, ""

    stable = 0
    for result in results:
        if "is_correct" in result and result.get("method") and result.get("reason"):
            stable += 1
    return _safe_div(stable, len(cases)), ""


def _filter_cases(cases: list[JudgeCase], case_id: str | None) -> list[JudgeCase]:
    if not case_id:
        return cases
    selected = [case for case in cases if case.case_id == case_id]
    if not selected:
        raise ValueError(f"judge case not found: {case_id}")
    return selected


def _collect_prediction_failures(
    failures: list[EvalFailure],
    pairs: list[tuple[JudgeCase, dict[str, Any]]],
) -> None:
    for case, result in pairs:
        predicted = _predicted_correct(result)
        if predicted == case.expected_correct:
            continue

        reason = "false positive：实际错误但判为正确"
        if case.expected_correct:
            reason = "false negative：实际正确但判为错误"

        failures.append(EvalFailure(
            case_id=case.case_id,
            item_id=case.case_id,
            reason=reason,
            evidence=_result_evidence(case, result),
        ))


def _collect_error_type_failures(
    failures: list[EvalFailure],
    pairs: list[tuple[JudgeCase, dict[str, Any]]],
) -> None:
    for case, result in pairs:
        predicted_error_type = _predicted_error_type(result)
        if predicted_error_type in ERROR_TYPES:
            if case.accepted_error_types and predicted_error_type not in case.accepted_error_types:
                failures.append(EvalFailure(
                    case_id=case.case_id,
                    item_id=case.case_id,
                    reason="错因类型不在 golden 可接受集合内",
                    evidence={
                        **_result_evidence(case, result),
                        "accepted_error_types": sorted(case.accepted_error_types),
                    },
                ))
            continue

        failures.append(EvalFailure(
            case_id=case.case_id,
            item_id=case.case_id,
            reason="错误样本缺少合法错因类型",
            evidence={
                **_result_evidence(case, result),
                "legal_error_types": ERROR_TYPES,
            },
        ))


def _collect_diagnosis_failures(
    failures: list[EvalFailure],
    pairs: list[tuple[JudgeCase, dict[str, Any]]],
) -> None:
    for case, result in pairs:
        if _has_complete_diagnosis(result):
            continue
        failures.append(EvalFailure(
            case_id=case.case_id,
            item_id=case.case_id,
            reason="错误样本诊断不完整",
            evidence=_result_evidence(case, result),
        ))


def _count_prediction_matches(pairs: list[tuple[JudgeCase, dict[str, Any]]]) -> int:
    return sum(1 for case, result in pairs if _predicted_correct(result) == case.expected_correct)


def _predicted_correct(result: dict[str, Any]) -> bool:
    return bool(result.get("is_correct", False))


def _predicted_error_type(result: dict[str, Any]) -> str:
    return str(result.get("error_type") or "").strip()


def _has_complete_diagnosis(result: dict[str, Any]) -> bool:
    return bool(
        _predicted_error_type(result)
        and str(result.get("error_evidence") or "").strip()
        and str(result.get("error_suggestion") or "").strip()
    )


def _metric_higher(name: str, value: float, detail: str) -> EvalMetric:
    threshold = DEFAULT_THRESHOLDS[name]
    return EvalMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        detail=detail,
    )


def _metric_lower(name: str, value: float, detail: str) -> EvalMetric:
    threshold = DEFAULT_THRESHOLDS[name]
    return EvalMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=value <= threshold,
        detail=detail,
    )


def _diagnosis_metric(
    name: str,
    value: float,
    detail: str,
    diagnosis_expected: bool,
) -> EvalMetric:
    if not diagnosis_expected:
        return EvalMetric(
            name=name,
            value=value,
            threshold=None,
            passed=True,
            detail=f"{detail}；LLM 未配置，本轮仅记录不扣分",
        )
    return _metric_higher(name, value, detail)


def _result_evidence(case: JudgeCase, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_type": case.question_type,
        "expected_correct": case.expected_correct,
        "predicted_correct": _predicted_correct(result),
        "expected_error_type": case.expected_error_type,
        "predicted_error_type": _predicted_error_type(result),
        "method": result.get("method", ""),
        "reason": result.get("reason", ""),
        "stem": _short(case.stem),
        "student_answer": _short(case.student_answer),
        "correct_answer": _short(case.correct_answer),
        "error_evidence": _short(str(result.get("error_evidence") or "")),
        "error_suggestion": _short(str(result.get("error_suggestion") or "")),
    }


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _short(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
