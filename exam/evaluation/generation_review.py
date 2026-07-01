"""LLM expert review for generated-question quality evals."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from exam.config import DEFAULT_CONFIG
from exam.evaluation.generation_eval import (
    _build_contexts,
    _filter_cases,
    _get_text,
    load_generation_cases,
    load_questions,
)
from exam.evaluation.schemas import EvalFailure, EvalMetric, EvalReport


REVIEW_MODEL_ENV = "BOOKTOEXAM_REVIEW_LLM"
REVIEW_PASS_THRESHOLD = 0.75
REVIEW_DIMENSION_THRESHOLD = 0.70
REVIEW_METRIC_THRESHOLDS = {
    "llm_review_pass_rate": 0.80,
    "llm_review_average_score": 0.75,
    "relevance_pass_rate": 0.80,
    "correctness_pass_rate": 0.80,
    "difficulty_fit_pass_rate": 0.80,
}


class GenerationReviewOutput(BaseModel):
    relevance: float = Field(description="题目与章节内容和 topic_hint 的相关性，0 到 1")
    correctness: float = Field(description="题干、答案、解析的事实正确性，0 到 1")
    clarity: float = Field(description="题干表达是否清晰、无歧义，0 到 1")
    answer_quality: float = Field(description="答案是否完整、可判定，0 到 1")
    explanation_quality: float = Field(description="解析是否能解释关键知识点，0 到 1")
    difficulty_fit: float = Field(description="题目难度是否匹配标注难度和 case 约束，0 到 1")
    exam_value: float = Field(description="作为考试题的区分度和考查价值，0 到 1")
    overall: float = Field(description="综合质量评分，0 到 1")
    verdict: str = Field(description="pass 或 fail")
    issues: list[str] = Field(description="发现的主要问题，若无问题则为空列表")
    suggestion: str = Field(description="简短修改建议")


def attach_generation_review(
    *,
    report: EvalReport,
    llm_client: Any,
    db_path: str | Path | None = None,
    llm_metadata: dict[str, Any] | None = None,
) -> EvalReport:
    """Run LLM expert review and append review metrics/failures to a generation report."""
    if llm_client is None:
        raise ValueError("llm_client is required when --llm-review is enabled")

    cases_path = Path(str(report.metadata.get("cases_file") or "evals/cases/generation_cases.json"))
    questions_path = Path(str(report.metadata.get("questions_file") or ""))
    if not questions_path.exists():
        raise FileNotFoundError(f"questions file not found for LLM review: {questions_path}")

    cases = _filter_cases(
        load_generation_cases(cases_path),
        str(report.metadata.get("case_id") or "") or None,
    )
    questions = load_questions(questions_path)
    limit = report.metadata.get("limit")
    if isinstance(limit, int):
        questions = questions[: max(0, limit)]

    contexts = _build_contexts(questions, cases)
    results: list[dict[str, Any]] = []
    review_failures: list[EvalFailure] = []

    for ctx in contexts:
        result = _review_one_question(
            llm_client=llm_client,
            question=ctx.question,
            case=ctx.case,
            db_path=db_path or DEFAULT_CONFIG["db_path"],
        )
        item = {
            "case_id": ctx.case_id,
            "item_id": ctx.item_id,
            **result,
        }
        results.append(item)
        if not _review_item_passed(item):
            review_failures.append(EvalFailure(
                case_id=ctx.case_id,
                item_id=ctx.item_id,
                reason="LLM 专家审稿未通过",
                evidence={
                    "overall": item.get("overall", 0.0),
                    "verdict": item.get("verdict", ""),
                    "issues": item.get("issues", []),
                    "suggestion": item.get("suggestion", ""),
                    "stem": _short(_get_text(ctx.question, "stem", "question")),
                },
            ))

    total = len(results)
    pass_count = sum(1 for item in results if _review_item_passed(item))
    relevance_pass = sum(1 for item in results if float(item.get("relevance", 0.0)) >= REVIEW_DIMENSION_THRESHOLD)
    correctness_pass = sum(1 for item in results if float(item.get("correctness", 0.0)) >= REVIEW_DIMENSION_THRESHOLD)
    difficulty_pass = sum(1 for item in results if float(item.get("difficulty_fit", 0.0)) >= REVIEW_DIMENSION_THRESHOLD)
    average_score = _mean(float(item.get("overall", 0.0)) for item in results)

    report.metrics.extend([
        _review_rate_metric("llm_review_pass_rate", pass_count, total, "LLM 专家审稿综合通过率"),
        EvalMetric(
            name="llm_review_average_score",
            value=average_score,
            threshold=REVIEW_METRIC_THRESHOLDS["llm_review_average_score"],
            passed=average_score >= REVIEW_METRIC_THRESHOLDS["llm_review_average_score"],
            detail=f"{total} 道题 LLM 专家综合评分均值",
        ),
        _review_rate_metric("relevance_pass_rate", relevance_pass, total, "相关性评分 >= 0.70"),
        _review_rate_metric("correctness_pass_rate", correctness_pass, total, "正确性评分 >= 0.70"),
        _review_rate_metric("difficulty_fit_pass_rate", difficulty_pass, total, "难度匹配评分 >= 0.70"),
    ])
    report.failures.extend(review_failures)
    report.metadata["llm_review"] = {
        "enabled": True,
        "reviewer_llm": llm_metadata or {},
        "review_pass_threshold": REVIEW_PASS_THRESHOLD,
        "dimension_pass_threshold": REVIEW_DIMENSION_THRESHOLD,
        "total_reviewed": total,
        "passed": pass_count,
        "average_score": average_score,
    }
    report.metadata["llm_review_results"] = results
    _refresh_summary(report)
    return report


def _review_one_question(
    *,
    llm_client: Any,
    question: dict[str, Any],
    case: Any,
    db_path: str | Path,
) -> dict[str, Any]:
    from exam.agents.utils.structured import invoke_structured

    section_id = case.section_id if case else _get_text(question, "source", "section_id")
    section_text = _load_section_excerpt(db_path, section_id)
    expected_keywords = ", ".join(case.expected_keywords) if case else ""
    allowed_types = ", ".join(sorted(case.allowed_types)) if case and case.allowed_types else ""
    allowed_difficulty = ", ".join(sorted(case.allowed_difficulty)) if case and case.allowed_difficulty else ""

    question_json = json.dumps(question, ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(content=(
            "你是嵌入式实时操作系统课程的试题审稿专家。"
            "请根据给定章节材料审查题目质量，分数必须使用 0 到 1 的小数。"
            "只依据材料和通用课程知识判断，不要因为题目格式完整就直接给高分。"
        )),
        HumanMessage(content=f"""
请审查下面这道题是否适合作为该章节的考试题。

评分口径：
- relevance：是否紧扣章节、topic_hint 和 expected_keywords。
- correctness：题干、答案、解析是否事实正确。
- clarity：题干是否清楚、没有多解或表述歧义。
- answer_quality：答案是否完整、可判定。
- explanation_quality：解析是否解释了关键知识点。
- difficulty_fit：是否符合题目标注难度和 case 难度约束。
- exam_value：是否有考试价值，而不是泛泛提问。
- overall：综合评分。
- verdict：overall >= 0.75 且 relevance/correctness/difficulty_fit 均 >= 0.70 时为 pass，否则为 fail。

章节 ID：{section_id}
章节标题：{case.section_title if case else ""}
topic_hint：{case.topic_hint if case else _get_text(question, "topic")}
expected_keywords：{expected_keywords}
允许题型：{allowed_types}
允许难度：{allowed_difficulty}

章节正文节选：
{section_text}

题目 JSON：
{question_json}
""".strip()),
    ]

    try:
        review = invoke_structured(llm_client, GenerationReviewOutput, messages)
        result = review.model_dump()
    except Exception as exc:
        result = {
            "relevance": 0.0,
            "correctness": 0.0,
            "clarity": 0.0,
            "answer_quality": 0.0,
            "explanation_quality": 0.0,
            "difficulty_fit": 0.0,
            "exam_value": 0.0,
            "overall": 0.0,
            "verdict": "fail",
            "issues": [f"LLM 审稿调用失败：{exc}"],
            "suggestion": "检查 LLM 配置或缩短输入后重试。",
        }

    for key in [
        "relevance",
        "correctness",
        "clarity",
        "answer_quality",
        "explanation_quality",
        "difficulty_fit",
        "exam_value",
        "overall",
    ]:
        result[key] = _normalise_score(result.get(key, 0.0))
    result["verdict"] = str(result.get("verdict", "")).strip().lower() or "fail"
    if result["verdict"] not in {"pass", "fail"}:
        result["verdict"] = "pass" if _review_item_passed(result) else "fail"
    issues = result.get("issues")
    if isinstance(issues, str):
        result["issues"] = [issues] if issues else []
    elif not isinstance(issues, list):
        result["issues"] = []
    result["suggestion"] = str(result.get("suggestion", "")).strip()
    return result


def _load_section_excerpt(db_path: str | Path, section_id: str, limit: int = 1800) -> str:
    if not section_id:
        return "未提供章节 ID。"

    path = Path(db_path)
    if not path.exists():
        return f"未找到章节数据库：{path}"

    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT title, text FROM sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT title, text FROM sections WHERE id LIKE ? AND text != '' ORDER BY id LIMIT 1",
                (f"{section_id}%",),
            ).fetchone()
    finally:
        conn.close()

    if row is None:
        return f"未找到 {section_id} 章节正文。"

    title, text = row
    excerpt = str(text or "").strip()
    if len(excerpt) > limit:
        excerpt = excerpt[:limit] + "..."
    return f"{title or section_id}\n{excerpt}".strip()


def _review_item_passed(item: dict[str, Any]) -> bool:
    return (
        str(item.get("verdict", "")).strip().lower() == "pass"
        and float(item.get("overall", 0.0)) >= REVIEW_PASS_THRESHOLD
        and float(item.get("relevance", 0.0)) >= REVIEW_DIMENSION_THRESHOLD
        and float(item.get("correctness", 0.0)) >= REVIEW_DIMENSION_THRESHOLD
        and float(item.get("difficulty_fit", 0.0)) >= REVIEW_DIMENSION_THRESHOLD
    )


def _review_rate_metric(name: str, passed_count: int, total: int, detail: str) -> EvalMetric:
    value = passed_count / total if total else 0.0
    threshold = REVIEW_METRIC_THRESHOLDS[name]
    return EvalMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        detail=f"{passed_count}/{total}，{detail}",
    )


def _normalise_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 1.0 < score <= 100.0:
        score = score / 100.0
    return min(1.0, max(0.0, score))


def _mean(values) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def _short(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _refresh_summary(report: EvalReport) -> None:
    total = int(report.metadata.get("total_questions") or 0)
    passed_metric_count = sum(1 for metric in report.metrics if metric.passed)
    report.summary = (
        f"共评测 {total} 道题，{passed_metric_count}/{len(report.metrics)} 个指标通过，"
        f"失败项 {len(report.failures)} 条。"
    )
