"""Run and aggregate all offline Agent evaluation tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from exam.config import DEFAULT_CONFIG
from exam.evaluation.generation_eval import run_generation_eval, run_live_generation_eval
from exam.evaluation.generation_review import attach_generation_review
from exam.evaluation.judge_eval import run_judge_eval
from exam.evaluation.recommendation_eval import run_recommendation_eval
from exam.evaluation.report import save_eval_report
from exam.evaluation.schemas import EvalFailure, EvalMetric, EvalReport


LOWER_IS_BETTER = {
    "generation.duplicate_rate",
    "judge.false_positive_rate",
    "judge.false_negative_rate",
}


@dataclass
class SavedEvalReport:
    report: EvalReport
    json_path: Path
    md_path: Path


@dataclass
class AllEvalResult:
    report: EvalReport
    json_path: Path
    md_path: Path
    child_reports: dict[str, SavedEvalReport]
    index_path: Path


def run_all_evals(
    *,
    cases_dir: str | Path = "evals/cases",
    reports_dir: str | Path = "evals/reports",
    questions_file: str | Path | None = None,
    use_latest_output: bool = True,
    generation_limit: int | None = None,
    judge_limit: int | None = None,
    recommendation_limit: int | None = None,
    recommendation_target_count: int = 8,
    generate_with_llm: bool = False,
    live_generation_output_dir: str | Path | None = None,
    generation_llm_metadata: dict[str, Any] | None = None,
    generation_review_llm_client: Any = None,
    generation_review_llm_metadata: dict[str, Any] | None = None,
    judge_llm_client: Any = None,
    judge_llm_metadata: dict[str, Any] | None = None,
) -> AllEvalResult:
    """Run generation, judge, and recommendation evals and save an aggregate report."""
    cases_path = Path(cases_dir)
    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    previous_snapshot = load_latest_metric_snapshot(reports_path / "index.json")

    child_reports: dict[str, SavedEvalReport] = {}
    def run_generation() -> EvalReport:
        report = (
            run_live_generation_eval(
                cases_file=cases_path / "generation_cases.json",
                generated_output_dir=live_generation_output_dir or reports_path.parent / "generated",
                db_path=DEFAULT_CONFIG["db_path"],
                max_questions=generation_limit or 5,
                llm_metadata=generation_llm_metadata or {},
            )
            if generate_with_llm
            else run_generation_eval(
                cases_file=cases_path / "generation_cases.json",
                questions_file=questions_file,
                output_dir=DEFAULT_CONFIG["results_dir"],
                use_latest_output=use_latest_output,
                limit=generation_limit,
            )
        )
        if generation_review_llm_client is not None:
            attach_generation_review(
                report=report,
                llm_client=generation_review_llm_client,
                db_path=DEFAULT_CONFIG["db_path"],
                llm_metadata=generation_review_llm_metadata or {},
            )
        return report

    eval_specs: list[tuple[str, str, Callable[[], EvalReport]]] = [
        (
            "generation",
            "generation_eval",
            run_generation,
        ),
        (
            "judge",
            "judge_eval",
            lambda: run_judge_eval(
                cases_file=cases_path / "judge_cases.json",
                limit=judge_limit,
                llm_client=judge_llm_client,
            ),
        ),
        (
            "recommendation",
            "recommendation_eval",
            lambda: run_recommendation_eval(
                cases_file=cases_path / "recommendation_cases.json",
                limit=recommendation_limit,
                target_count=recommendation_target_count,
            ),
        ),
    ]

    for eval_type, prefix, run_eval in eval_specs:
        report = _run_child_eval(eval_type, run_eval)
        if eval_type == "judge" and judge_llm_metadata:
            report.metadata["llm"] = judge_llm_metadata
        json_path, md_path = save_eval_report(report, reports_path, filename_prefix=prefix)
        child_reports[eval_type] = SavedEvalReport(
            report=report,
            json_path=json_path,
            md_path=md_path,
        )

    aggregate = build_all_eval_report(
        child_reports=child_reports,
        previous_snapshot=previous_snapshot,
        cases_dir=cases_path,
        reports_dir=reports_path,
        questions_file=questions_file,
        generation_limit=generation_limit,
        judge_limit=judge_limit,
        recommendation_limit=recommendation_limit,
        recommendation_target_count=recommendation_target_count,
        generate_with_llm=generate_with_llm,
        generation_llm_metadata=generation_llm_metadata or {},
        generation_review_llm_configured=generation_review_llm_client is not None,
        generation_review_llm_metadata=generation_review_llm_metadata or {},
        judge_llm_configured=judge_llm_client is not None,
        judge_llm_metadata=judge_llm_metadata or {},
    )
    json_path, md_path = save_eval_report(
        aggregate,
        reports_path,
        filename_prefix="agent_eval",
    )
    index_path = update_reports_index(
        index_path=reports_path / "index.json",
        report=aggregate,
        json_path=json_path,
        md_path=md_path,
    )

    return AllEvalResult(
        report=aggregate,
        json_path=json_path,
        md_path=md_path,
        child_reports=child_reports,
        index_path=index_path,
    )


def build_all_eval_report(
    *,
    child_reports: dict[str, SavedEvalReport],
    previous_snapshot: dict[str, float],
    cases_dir: Path,
    reports_dir: Path,
    questions_file: str | Path | None,
    generation_limit: int | None,
    judge_limit: int | None,
    recommendation_limit: int | None,
    recommendation_target_count: int,
    generate_with_llm: bool,
    generation_llm_metadata: dict[str, Any],
    generation_review_llm_configured: bool,
    generation_review_llm_metadata: dict[str, Any],
    judge_llm_configured: bool,
    judge_llm_metadata: dict[str, Any],
) -> EvalReport:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    created_at = datetime.now().isoformat(timespec="seconds")

    category_scores = {
        eval_type: _score_report(saved.report)
        for eval_type, saved in child_reports.items()
    }
    overall_score = _mean(category_scores.values())
    metric_snapshot = build_metric_snapshot(
        {eval_type: saved.report for eval_type, saved in child_reports.items()}
    )
    diffs = build_metric_diffs(metric_snapshot, previous_snapshot)

    failures: list[EvalFailure] = []
    for eval_type, saved in child_reports.items():
        for failure in saved.report.failures:
            failures.append(EvalFailure(
                case_id=f"{eval_type}:{failure.case_id}",
                item_id=failure.item_id,
                reason=failure.reason,
                evidence={
                    "eval_type": eval_type,
                    **failure.evidence,
                },
            ))

    metrics = [
        EvalMetric(
            name="overall_score",
            value=overall_score,
            threshold=0.80,
            passed=overall_score >= 0.80,
            detail="三类评测通过指标比例的平均值",
        ),
        EvalMetric(
            name="generation_score",
            value=category_scores.get("generation", 0.0),
            threshold=0.80,
            passed=category_scores.get("generation", 0.0) >= 0.80,
            detail="出题质量评测通过指标比例",
        ),
        EvalMetric(
            name="judge_score",
            value=category_scores.get("judge", 0.0),
            threshold=0.80,
            passed=category_scores.get("judge", 0.0) >= 0.80,
            detail="判题一致性评测通过指标比例",
        ),
        EvalMetric(
            name="recommendation_score",
            value=category_scores.get("recommendation", 0.0),
            threshold=0.80,
            passed=category_scores.get("recommendation", 0.0) >= 0.80,
            detail="推荐策略评测通过指标比例",
        ),
    ]

    regression_count = sum(1 for item in diffs if item.get("status") == "regressed")
    summary = (
        f"总分 {overall_score:.0%}，"
        f"出题 {category_scores.get('generation', 0.0):.0%}，"
        f"判题 {category_scores.get('judge', 0.0):.0%}，"
        f"推荐 {category_scores.get('recommendation', 0.0):.0%}；"
        f"失败项 {len(failures)} 条，回退指标 {regression_count} 个。"
    )

    return EvalReport(
        eval_type="all",
        run_id=run_id,
        created_at=created_at,
        metrics=metrics,
        failures=failures,
        summary=summary,
        metadata={
            "cases_dir": str(cases_dir),
            "reports_dir": str(reports_dir),
            "questions_file": str(questions_file or ""),
            "generation_limit": generation_limit,
            "judge_limit": judge_limit,
            "recommendation_limit": recommendation_limit,
            "recommendation_target_count": recommendation_target_count,
            "generation_eval_mode": "live_llm_generation" if generate_with_llm else "offline_saved_questions",
            "generation_llm": generation_llm_metadata,
            "generation_review_llm_configured": generation_review_llm_configured,
            "generation_review_llm": generation_review_llm_metadata,
            "judge_llm_configured": judge_llm_configured,
            "judge_llm": judge_llm_metadata,
            "metric_snapshot": metric_snapshot,
            "metric_diffs": diffs,
            "sections": {
                eval_type: {
                    "summary": saved.report.summary,
                    "json_path": str(saved.json_path),
                    "md_path": str(saved.md_path),
                    "metrics": [metric.to_dict() for metric in saved.report.metrics],
                    "failure_count": len(saved.report.failures),
                }
                for eval_type, saved in child_reports.items()
            },
        },
    )


def build_metric_snapshot(reports: dict[str, EvalReport]) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for eval_type, report in reports.items():
        for metric in report.metrics:
            snapshot[f"{eval_type}.{metric.name}"] = metric.value
    return snapshot


def build_metric_diffs(
    current: dict[str, float],
    previous: dict[str, float],
) -> list[dict[str, float | str]]:
    diffs: list[dict[str, float | str]] = []
    for name in sorted(current):
        current_value = current[name]
        previous_value = previous.get(name)
        if previous_value is None:
            diffs.append({
                "name": name,
                "current": current_value,
                "previous": "",
                "delta": "",
                "status": "new",
            })
            continue

        delta = current_value - previous_value
        effective_delta = -delta if name in LOWER_IS_BETTER else delta
        if effective_delta > 0.0001:
            status = "improved"
        elif effective_delta < -0.0001:
            status = "regressed"
        else:
            status = "stable"

        diffs.append({
            "name": name,
            "current": current_value,
            "previous": previous_value,
            "delta": delta,
            "status": status,
        })
    return diffs


def load_latest_metric_snapshot(index_path: str | Path) -> dict[str, float]:
    path = Path(index_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    runs = data.get("runs") or []
    if not runs:
        return {}
    metrics = runs[-1].get("metrics") or {}
    return {
        str(name): float(value)
        for name, value in metrics.items()
        if isinstance(value, (int, float))
    }


def update_reports_index(
    *,
    index_path: str | Path,
    report: EvalReport,
    json_path: str | Path,
    md_path: str | Path,
) -> Path:
    path = Path(index_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    runs = data.get("runs")
    if not isinstance(runs, list):
        runs = []

    metric_snapshot = report.metadata.get("metric_snapshot", {})
    runs.append({
        "run_id": report.run_id,
        "created_at": report.created_at,
        "summary": report.summary,
        "score": _metric_value(report, "overall_score"),
        "agent_report_json": str(json_path),
        "agent_report_md": str(md_path),
        "metrics": metric_snapshot,
        "diffs": report.metadata.get("metric_diffs", []),
        "child_reports": {
            eval_type: {
                "json_path": section.get("json_path", ""),
                "md_path": section.get("md_path", ""),
                "failure_count": section.get("failure_count", 0),
            }
            for eval_type, section in report.metadata.get("sections", {}).items()
        },
    })

    data = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "runs": runs,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _run_child_eval(eval_type: str, run_eval: Callable[[], EvalReport]) -> EvalReport:
    try:
        return run_eval()
    except Exception as exc:
        created_at = datetime.now().isoformat(timespec="seconds")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return EvalReport(
            eval_type=eval_type,
            run_id=run_id,
            created_at=created_at,
            metrics=[
                EvalMetric(
                    name=f"{eval_type}_available",
                    value=0.0,
                    threshold=1.0,
                    passed=False,
                    detail="评测执行失败",
                )
            ],
            failures=[
                EvalFailure(
                    case_id=eval_type,
                    item_id=eval_type,
                    reason="评测执行失败",
                    evidence={"error": str(exc)},
                )
            ],
            summary=f"{eval_type} 评测执行失败：{exc}",
        )


def _score_report(report: EvalReport) -> float:
    if not report.metrics:
        return 0.0
    passed = sum(1 for metric in report.metrics if metric.passed)
    return passed / len(report.metrics)


def _mean(values) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def _metric_value(report: EvalReport, name: str) -> float:
    for metric in report.metrics:
        if metric.name == name:
            return metric.value
    return 0.0
