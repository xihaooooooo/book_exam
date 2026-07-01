"""MVP generation-quality evaluation over saved question JSON files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from exam.config import DEFAULT_CONFIG
from exam.evaluation.schemas import EvalFailure, EvalMetric, EvalReport


LEGAL_QUESTION_TYPES = {
    "choice",
    "fill_blank",
    "short_answer",
    "code_fill",
    "comprehensive",
}
LEGAL_DIFFICULTIES = {"easy", "medium", "hard"}

DEFAULT_THRESHOLDS = {
    "format_pass_rate": 0.95,
    "type_adherence_rate": 0.90,
    "difficulty_adherence_rate": 0.90,
    "answer_presence_rate": 0.98,
    "explanation_presence_rate": 0.90,
    "duplicate_rate": 0.15,
}


@dataclass(frozen=True)
class GenerationCase:
    case_id: str
    section_id: str
    section_title: str
    topic_hint: str
    mode: str
    target_count: int
    allowed_types: set[str]
    allowed_difficulty: set[str]
    expected_keywords: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GenerationCase":
        return cls(
            case_id=str(data.get("case_id", "")),
            section_id=str(data.get("section_id", "")),
            section_title=str(data.get("section_title", "")),
            topic_hint=str(data.get("topic_hint", "")),
            mode=str(data.get("mode", "")) or "exam",
            target_count=int(data.get("target_count", 1) or 1),
            allowed_types=set(data.get("allowed_types") or []),
            allowed_difficulty=set(data.get("allowed_difficulty") or []),
            expected_keywords=list(data.get("expected_keywords") or []),
        )


@dataclass
class QuestionContext:
    item_id: str
    question: dict[str, Any]
    case: GenerationCase | None = None

    @property
    def case_id(self) -> str:
        return self.case.case_id if self.case else "output"


def run_generation_eval(
    *,
    cases_file: str | Path = "evals/cases/generation_cases.json",
    questions_file: str | Path | None = None,
    output_dir: str | Path | None = None,
    use_latest_output: bool = False,
    case_id: str | None = None,
    limit: int | None = None,
    duplicate_threshold: float = 0.90,
) -> EvalReport:
    """Evaluate saved generated questions and return a structured report."""
    cases_path = Path(cases_file)
    cases = load_generation_cases(cases_path)
    selected_cases = _filter_cases(cases, case_id)

    if questions_file is None:
        if not use_latest_output:
            use_latest_output = True
        questions_path = find_latest_questions(output_dir or DEFAULT_CONFIG["results_dir"])
    else:
        questions_path = Path(questions_file)

    questions = load_questions(questions_path)
    if limit is not None:
        questions = questions[: max(0, limit)]

    contexts = _build_contexts(questions, selected_cases)
    return evaluate_generation_questions(
        contexts=contexts,
        cases_path=cases_path,
        questions_path=questions_path,
        case_id=case_id,
        limit=limit,
        duplicate_threshold=duplicate_threshold,
    )


def run_live_generation_eval(
    *,
    cases_file: str | Path = "evals/cases/generation_cases.json",
    generated_output_dir: str | Path = "evals/generated",
    db_path: str | Path | None = None,
    case_id: str | None = None,
    max_questions: int = 5,
    duplicate_threshold: float = 0.90,
    llm_metadata: dict[str, Any] | None = None,
) -> EvalReport:
    """Generate questions with the real ExamGraph, then evaluate the generated output."""
    cases_path = Path(cases_file)
    cases = _filter_cases(load_generation_cases(cases_path), case_id)
    generated_path = Path(generated_output_dir)
    generated_path.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    max_questions = max(1, int(max_questions or 1))
    all_questions: list[dict[str, Any]] = []
    generation_errors: list[EvalFailure] = []
    remaining = max_questions

    for case in cases:
        if remaining <= 0:
            break
        target_count = min(max(1, case.target_count), remaining)
        try:
            questions = _generate_case_questions(
                case=case,
                target_count=target_count,
                generated_output_dir=generated_path / f"{run_id}_{case.case_id}",
                db_path=db_path or DEFAULT_CONFIG["db_path"],
            )
        except Exception as exc:
            generation_errors.append(EvalFailure(
                case_id=case.case_id,
                item_id="live_generation",
                reason="真实大模型出题失败",
                evidence={
                    "section_id": case.section_id,
                    "topic_hint": case.topic_hint,
                    "target_count": target_count,
                    "error": str(exc),
                },
            ))
            continue

        for question in questions[:remaining]:
            question.setdefault("source", case.section_id)
            question.setdefault("topic", case.topic_hint)
            question["eval_case_id"] = case.case_id
            all_questions.append(question)
        remaining = max_questions - len(all_questions)

    aggregate_questions_path = generated_path / f"questions_live_{run_id}.json"
    aggregate_questions_path.write_text(
        json.dumps(all_questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    contexts = _build_contexts(all_questions, cases)
    report = evaluate_generation_questions(
        contexts=contexts,
        cases_path=cases_path,
        questions_path=aggregate_questions_path,
        case_id=case_id,
        limit=max_questions,
        duplicate_threshold=duplicate_threshold,
    )
    report.metadata.update({
        "eval_mode": "live_llm_generation",
        "generation_source": "live_exam_graph",
        "generated_output_dir": str(generated_path),
        "max_questions": max_questions,
        "llm": llm_metadata or {},
    })

    report.failures.extend(generation_errors)
    _refresh_generation_summary(report)
    return report


def load_generation_cases(path: str | Path) -> list[GenerationCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"generation cases must be a list: {path}")
    return [GenerationCase.from_dict(item) for item in data]


def _generate_case_questions(
    *,
    case: GenerationCase,
    target_count: int,
    generated_output_dir: Path,
    db_path: str | Path,
) -> list[dict[str, Any]]:
    from exam.graph.exam_graph import ExamGraph

    config = dict(DEFAULT_CONFIG)
    config["results_dir"] = str(generated_output_dir)
    generated_output_dir.mkdir(parents=True, exist_ok=True)

    graph = ExamGraph(config=config, debug=False)
    toc = [{
        "chapter": "离线评测样本",
        "sections": [{
            "id": case.section_id,
            "title": case.section_title or case.topic_hint or case.section_id,
        }],
    }]
    _, questions = graph.propagate(
        db_path=str(db_path),
        toc=toc,
        focus=case.topic_hint or case.section_title,
        target_count=target_count,
        allowed_types=",".join(sorted(case.allowed_types)),
        allowed_difficulty=",".join(sorted(case.allowed_difficulty)),
        mode=case.mode or "exam",
    )
    return [q for q in questions if isinstance(q, dict)]


def load_questions(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        questions = data
    elif isinstance(data, dict):
        questions = data.get("questions") or data.get("all_questions") or []
    else:
        questions = []

    if not isinstance(questions, list):
        raise ValueError(f"questions payload must be a list: {path}")
    return [q for q in questions if isinstance(q, dict)]


def find_latest_questions(output_dir: str | Path) -> Path:
    output_path = Path(output_dir)
    candidates = sorted(
        output_path.glob("questions_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no questions_*.json found in {output_path}")
    return candidates[0]


def evaluate_generation_questions(
    *,
    contexts: list[QuestionContext],
    cases_path: Path,
    questions_path: Path,
    case_id: str | None,
    limit: int | None,
    duplicate_threshold: float,
) -> EvalReport:
    total = len(contexts)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    created_at = datetime.now().isoformat(timespec="seconds")
    failures: list[EvalFailure] = []

    if total == 0:
        failures.append(EvalFailure(
            case_id=case_id or "output",
            item_id="questions",
            reason="没有可评测的题目",
            evidence={"questions_file": str(questions_path)},
        ))

    format_pass = 0
    type_pass = 0
    difficulty_pass = 0
    answer_pass = 0
    explanation_pass = 0

    for ctx in contexts:
        question = ctx.question
        format_issues = _format_issues(question)
        if format_issues:
            failures.append(_failure(ctx, "格式字段不完整", {
                "issues": format_issues,
                "stem": _short(_get_text(question, "stem", "question")),
            }))
        else:
            format_pass += 1

        allowed_types = _allowed_types(ctx.case)
        qtype = _get_text(question, "question_type", "type")
        if qtype in allowed_types:
            type_pass += 1
        else:
            failures.append(_failure(ctx, "题型不在允许集合内", {
                "question_type": qtype,
                "allowed_types": sorted(allowed_types),
            }))

        allowed_difficulty = _allowed_difficulty(ctx.case)
        difficulty = _get_text(question, "difficulty")
        if difficulty in allowed_difficulty:
            difficulty_pass += 1
        else:
            failures.append(_failure(ctx, "难度不在允许集合内", {
                "difficulty": difficulty,
                "allowed_difficulty": sorted(allowed_difficulty),
            }))

        correct_answer = _get_text(question, "correct_answer", "answer")
        if correct_answer:
            answer_pass += 1
        else:
            failures.append(_failure(ctx, "缺少正确答案", {
                "stem": _short(_get_text(question, "stem", "question")),
            }))

        explanation = _get_text(question, "explanation", "analysis")
        if explanation:
            explanation_pass += 1
        else:
            failures.append(_failure(ctx, "缺少解析", {
                "stem": _short(_get_text(question, "stem", "question")),
            }))

    duplicate_failures = _find_duplicates(contexts, duplicate_threshold)
    failures.extend(duplicate_failures)

    duplicate_count = len(duplicate_failures)
    metrics = [
        _rate_metric("format_pass_rate", format_pass, total, "必填字段和选择题选项完整"),
        _rate_metric("type_adherence_rate", type_pass, total, "题型满足 case 约束或合法题型集合"),
        _rate_metric("difficulty_adherence_rate", difficulty_pass, total, "难度满足 case 约束或合法难度集合"),
        _rate_metric("answer_presence_rate", answer_pass, total, "correct_answer 非空"),
        _rate_metric("explanation_presence_rate", explanation_pass, total, "explanation 非空"),
        EvalMetric(
            name="duplicate_rate",
            value=_safe_div(duplicate_count, total),
            threshold=DEFAULT_THRESHOLDS["duplicate_rate"],
            passed=_safe_div(duplicate_count, total) <= DEFAULT_THRESHOLDS["duplicate_rate"],
            detail=f"{duplicate_count}/{total} 道题被判定为重复或高度相似",
        ),
    ]

    passed_metric_count = sum(1 for metric in metrics if metric.passed)
    summary = (
        f"共评测 {total} 道题，{passed_metric_count}/{len(metrics)} 个指标通过，"
        f"失败项 {len(failures)} 条。"
    )

    matched = sum(1 for ctx in contexts if ctx.case is not None)
    return EvalReport(
        eval_type="generation",
        run_id=run_id,
        created_at=created_at,
        metrics=metrics,
        failures=failures,
        summary=summary,
        metadata={
            "eval_mode": "offline_saved_questions",
            "generation_source": "saved_questions_file",
            "cases_file": str(cases_path),
            "questions_file": str(questions_path),
            "case_id": case_id or "",
            "limit": limit,
            "total_questions": total,
            "matched_case_questions": matched,
            "duplicate_threshold": duplicate_threshold,
        },
    )


def _filter_cases(cases: list[GenerationCase], case_id: str | None) -> list[GenerationCase]:
    if not case_id:
        return cases
    selected = [case for case in cases if case.case_id == case_id]
    if not selected:
        raise ValueError(f"generation case not found: {case_id}")
    return selected


def _build_contexts(
    questions: list[dict[str, Any]],
    cases: list[GenerationCase],
) -> list[QuestionContext]:
    contexts: list[QuestionContext] = []
    case_by_section = {case.section_id: case for case in cases if case.section_id}
    single_case = cases[0] if len(cases) == 1 else None

    for index, question in enumerate(questions, start=1):
        source = _get_text(question, "source", "section_id")
        matched_case = case_by_section.get(source)
        if matched_case is None:
            matched_case = _match_by_prefix(source, cases)
        if matched_case is None and single_case is not None:
            matched_case = single_case
        item_id = str(question.get("id") or question.get("question_id") or f"q{index:03d}")
        contexts.append(QuestionContext(item_id=item_id, question=question, case=matched_case))
    return contexts


def _match_by_prefix(source: str, cases: list[GenerationCase]) -> GenerationCase | None:
    if not source:
        return None
    matches = [
        case for case in cases
        if case.section_id and (source.startswith(case.section_id + ".") or case.section_id.startswith(source + "."))
    ]
    if not matches:
        return None
    return max(matches, key=lambda case: len(case.section_id))


def _format_issues(question: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not _get_text(question, "question_type", "type"):
        issues.append("missing question_type")
    if not _get_text(question, "difficulty"):
        issues.append("missing difficulty")
    if not _get_text(question, "stem", "question"):
        issues.append("missing stem")

    qtype = _get_text(question, "question_type", "type")
    if qtype == "choice" and not _has_choice_options(question):
        issues.append("choice options incomplete")
    return issues


def _has_choice_options(question: dict[str, Any]) -> bool:
    options = question.get("options")
    if isinstance(options, list):
        return len([opt for opt in options if str(opt).strip()]) >= 4

    option_fields = ["option_a", "option_b", "option_c", "option_d"]
    return all(str(question.get(field, "")).strip() for field in option_fields)


def _allowed_types(case: GenerationCase | None) -> set[str]:
    if case and case.allowed_types:
        return case.allowed_types
    return LEGAL_QUESTION_TYPES


def _allowed_difficulty(case: GenerationCase | None) -> set[str]:
    if case and case.allowed_difficulty:
        return case.allowed_difficulty
    return LEGAL_DIFFICULTIES


def _find_duplicates(
    contexts: list[QuestionContext],
    threshold: float,
) -> list[EvalFailure]:
    failures: list[EvalFailure] = []
    seen: list[tuple[QuestionContext, str]] = []

    for ctx in contexts:
        stem = _normalize_for_similarity(_get_text(ctx.question, "stem", "question"))
        if not stem:
            seen.append((ctx, stem))
            continue

        duplicate_of = None
        duplicate_score = 0.0
        for prev_ctx, prev_stem in seen:
            if not prev_stem:
                continue
            score = 1.0 if stem == prev_stem else SequenceMatcher(None, stem, prev_stem).ratio()
            if score >= threshold and score > duplicate_score:
                duplicate_of = prev_ctx
                duplicate_score = score

        if duplicate_of is not None:
            failures.append(_failure(ctx, "题干重复或高度相似", {
                "duplicate_of": duplicate_of.item_id,
                "similarity": round(duplicate_score, 4),
                "stem": _short(_get_text(ctx.question, "stem", "question")),
                "duplicate_stem": _short(_get_text(duplicate_of.question, "stem", "question")),
            }))

        seen.append((ctx, stem))

    return failures


def _rate_metric(name: str, passed_count: int, total: int, detail: str) -> EvalMetric:
    value = _safe_div(passed_count, total)
    threshold = DEFAULT_THRESHOLDS[name]
    return EvalMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        detail=f"{passed_count}/{total}，{detail}",
    )


def _failure(ctx: QuestionContext, reason: str, evidence: dict[str, Any]) -> EvalFailure:
    source = _get_text(ctx.question, "source", "section_id")
    if source:
        evidence = {**evidence, "source": source}
    return EvalFailure(
        case_id=ctx.case_id,
        item_id=ctx.item_id,
        reason=reason,
        evidence=evidence,
    )


def _get_text(question: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = question.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_for_similarity(text: str) -> str:
    lowered = text.lower().strip()
    return re.sub(r"[\s，。！？；：,.!?;:'\"“”‘’（）()\[\]【】<>《》、`~\-_=+*/\\|]+", "", lowered)


def _safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _short(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _refresh_generation_summary(report: EvalReport) -> None:
    total = int(report.metadata.get("total_questions") or 0)
    passed_metric_count = sum(1 for metric in report.metrics if metric.passed)
    report.summary = (
        f"共评测 {total} 道题，{passed_metric_count}/{len(report.metrics)} 个指标通过，"
        f"失败项 {len(report.failures)} 条。"
    )
