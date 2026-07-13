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
LEGAL_MEDIA_TYPES = {"image", "mermaid", "canvas"}
LEGAL_CANVAS_TYPES = {"tree", "graph", "state_machine", "queue", "memory", "timeline", "structure"}
LEGAL_CANVAS_JUDGE_MODES = {"structural_diff", "exact_match", "text_explanation", "rubric"}
MERMAID_START_RE = re.compile(
    r"^\s*(flowchart\s+(TD|TB|LR|RL|BT)|graph\s+(TD|TB|LR|RL|BT)|sequenceDiagram|gantt)\b"
)
MERMAID_FORBIDDEN_RE = re.compile(r"<\s*/?\s*(script|iframe|object|embed|style)\b|%%\{", re.I)
MEDIA_REF_RE = re.compile(r"\[media:\s*([^\]]+)\]")
LATEX_SPAN_RE = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
INTERACTIVE_MEDIA_RE = re.compile(
    r"(点击|拖拽|拖动|连线|编辑|移动).{0,8}(图|节点|边|画布)|"
    r"在图上|在画布|操作图|直接操作|标注到图|绘制到图"
)
LIKELY_MATH_RE = re.compile(
    r"(?<![\w$])(?:[A-Za-z]\s*(?:=|\+|-|\*|/|\^)\s*(?:[A-Za-z]|\d)|"
    r"\d+\s*(?:=|\+|-|\*|/|\^)\s*(?:[A-Za-z]|\d))(?![\w$])"
)
VISUAL_CUE_TERMS = (
    "根据图", "结合图", "观察图", "图中", "图示", "图表", "题图",
    "结构预览", "流程图", "状态图", "示意图", "结构图", "根据 [media:",
    "结合 [media:", "观察 [media:",
)
PLACEHOLDER_DESCRIPTION_TERMS = ("待生成", "尚未生成", "TODO", "占位", "未提取到足够相邻文本")

DEFAULT_THRESHOLDS = {
    "format_pass_rate": 0.95,
    "type_adherence_rate": 0.90,
    "difficulty_adherence_rate": 0.90,
    "answer_presence_rate": 0.98,
    "explanation_presence_rate": 0.90,
    "keyword_coverage_rate": 0.70,
    "latex_format_pass_rate": 0.95,
    "media_contract_pass_rate": 0.95,
    "mermaid_contract_pass_rate": 0.95,
    "canvas_contract_pass_rate": 0.95,
    "expected_media_presence_rate": 0.98,
    "visual_reference_pass_rate": 0.95,
    "readonly_media_pass_rate": 0.98,
    "media_description_pass_rate": 0.90,
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
    expected_media_types: set[str]
    requires_latex: bool

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
            expected_media_types=set(data.get("expected_media_types") or []),
            requires_latex=bool(data.get("requires_latex") or False),
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
    keyword_pass = 0
    keyword_total = 0
    latex_pass = 0
    media_pass = 0
    mermaid_pass = 0
    canvas_pass = 0
    expected_media_pass = 0
    expected_media_total = 0
    visual_reference_pass = 0
    visual_reference_total = 0
    readonly_media_pass = 0
    readonly_media_total = 0
    media_description_pass = 0
    media_description_total = 0

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

        keywords = _expected_keywords(ctx.case)
        if keywords:
            keyword_total += 1
            if _has_expected_keyword(question, keywords):
                keyword_pass += 1
            else:
                failures.append(_failure(ctx, "未命中期望关键词", {
                    "expected_keywords": keywords,
                    "stem": _short(_get_text(question, "stem", "question")),
                }))

        latex_issues = _latex_issues(question, requires_latex=bool(ctx.case and ctx.case.requires_latex))
        if latex_issues:
            failures.append(_failure(ctx, "LaTeX 格式不合格", {
                "issues": latex_issues,
                "stem": _short(_get_text(question, "stem", "question")),
            }))
        else:
            latex_pass += 1

        media_issues = _media_issues(question)
        if media_issues:
            failures.append(_failure(ctx, "media 契约不合格", {
                "issues": media_issues,
                "stem": _short(_get_text(question, "stem", "question")),
            }))
        else:
            media_pass += 1

        mermaid_issues = _mermaid_media_issues(question.get("media"))
        if mermaid_issues:
            failures.append(_failure(ctx, "Mermaid 媒体不合格", {
                "issues": mermaid_issues,
                "stem": _short(_get_text(question, "stem", "question")),
            }))
        else:
            mermaid_pass += 1

        canvas_issues = _canvas_media_issues(question.get("media"))
        if canvas_issues:
            failures.append(_failure(ctx, "Canvas 媒体不合格", {
                "issues": canvas_issues,
                "stem": _short(_get_text(question, "stem", "question")),
            }))
        else:
            canvas_pass += 1

        expected_media_types = _expected_media_types(ctx.case)
        if expected_media_types:
            expected_media_total += 1
            actual_media_types = _question_media_types(question)
            if actual_media_types & expected_media_types:
                expected_media_pass += 1
            else:
                failures.append(_failure(ctx, "缺少期望媒体类型", {
                    "expected_media_types": sorted(expected_media_types),
                    "actual_media_types": sorted(actual_media_types),
                    "stem": _short(_get_text(question, "stem", "question")),
                }))

        if _has_media(question):
            visual_reference_total += 1
            visual_issues = _visual_reference_issues(question)
            if visual_issues:
                failures.append(_failure(ctx, "图文题引用不合格", {
                    "issues": visual_issues,
                    "stem": _short(_get_text(question, "stem", "question")),
                }))
            else:
                visual_reference_pass += 1

            readonly_media_total += 1
            readonly_issues = _readonly_media_issues(question)
            if readonly_issues:
                failures.append(_failure(ctx, "图文题要求操作图", {
                    "issues": readonly_issues,
                    "stem": _short(_get_text(question, "stem", "question")),
                }))
            else:
                readonly_media_pass += 1

            media_description_total += 1
            description_issues = _media_description_issues(question)
            if description_issues:
                failures.append(_failure(ctx, "媒体描述不合格", {
                    "issues": description_issues,
                    "stem": _short(_get_text(question, "stem", "question")),
                }))
            else:
                media_description_pass += 1

    duplicate_failures = _find_duplicates(contexts, duplicate_threshold)
    failures.extend(duplicate_failures)

    duplicate_count = len(duplicate_failures)
    metrics = [
        _rate_metric("format_pass_rate", format_pass, total, "必填字段和选择题选项完整"),
        _rate_metric("type_adherence_rate", type_pass, total, "题型满足 case 约束或合法题型集合"),
        _rate_metric("difficulty_adherence_rate", difficulty_pass, total, "难度满足 case 约束或合法难度集合"),
        _rate_metric("answer_presence_rate", answer_pass, total, "correct_answer 非空"),
        _rate_metric("explanation_presence_rate", explanation_pass, total, "explanation 非空"),
        _optional_rate_metric("keyword_coverage_rate", keyword_pass, keyword_total, "命中 case.expected_keywords 中至少一个关键词"),
        _rate_metric("latex_format_pass_rate", latex_pass, total, "数学表达式使用 LaTeX 标记且分隔符完整"),
        _rate_metric("media_contract_pass_rate", media_pass, total, "media 为数组且图片/Mermaid/Canvas 字段满足契约"),
        _rate_metric("mermaid_contract_pass_rate", mermaid_pass, total, "Mermaid 内容属于受支持子集且无明显危险片段"),
        _rate_metric("canvas_contract_pass_rate", canvas_pass, total, "Canvas 配置满足只读预览和结构化判题协议"),
        _optional_rate_metric("expected_media_presence_rate", expected_media_pass, expected_media_total, "命中 case.expected_media_types 要求"),
        _optional_rate_metric("visual_reference_pass_rate", visual_reference_pass, visual_reference_total, "带 media 的题干明确要求根据图文信息作答"),
        _optional_rate_metric("readonly_media_pass_rate", readonly_media_pass, readonly_media_total, "题目不要求学生点击、拖拽、连线或编辑题图"),
        _optional_rate_metric("media_description_pass_rate", media_description_pass, media_description_total, "media 描述存在且不是占位描述"),
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


def _expected_keywords(case: GenerationCase | None) -> list[str]:
    if not case:
        return []
    return [str(keyword).strip() for keyword in case.expected_keywords if str(keyword).strip()]


def _expected_media_types(case: GenerationCase | None) -> set[str]:
    if not case:
        return set()
    return {media_type for media_type in case.expected_media_types if media_type in LEGAL_MEDIA_TYPES}


def _has_expected_keyword(question: dict[str, Any], keywords: list[str]) -> bool:
    text = _question_text_blob(question).lower()
    return any(keyword.lower() in text for keyword in keywords)


def _question_text_blob(question: dict[str, Any]) -> str:
    parts = []
    for value in _question_text_values(question):
        parts.append(value)
    return "\n".join(parts)


def _question_text_values(question: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("stem", "question", "correct_answer", "answer", "explanation", "analysis", "topic", "source"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    options = question.get("options")
    if isinstance(options, list):
        values.extend(str(item) for item in options if str(item).strip())
    for key in ("option_a", "option_b", "option_c", "option_d"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    media = question.get("media")
    if isinstance(media, list):
        for item in media:
            if not isinstance(item, dict):
                continue
            for key in ("description", "content"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value)
    return values


def _latex_issues(question: dict[str, Any], *, requires_latex: bool = False) -> list[str]:
    issues: list[str] = []
    has_latex = False
    qtype = _get_text(question, "question_type", "type")
    for label, text in _question_labeled_text(question):
        if LATEX_SPAN_RE.search(text):
            has_latex = True
        if _has_unbalanced_dollars(text):
            issues.append(f"{label} has unbalanced LaTeX dollar delimiters")
            continue
        if qtype == "code_fill" and label in {"correct_answer", "answer"}:
            continue
        plain = _strip_latex_and_code(text)
        if LIKELY_MATH_RE.search(plain):
            issues.append(f"{label} contains a likely math expression outside LaTeX")
    if requires_latex and not has_latex:
        issues.append("case requires LaTeX but no LaTeX expression was found")
    return issues


def _question_labeled_text(question: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for key in ("stem", "question", "correct_answer", "answer", "explanation", "analysis"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append((key, value))
    options = question.get("options")
    if isinstance(options, list):
        for index, item in enumerate(options):
            text = str(item)
            if text.strip():
                values.append((f"options[{index}]", text))
    for key in ("option_a", "option_b", "option_c", "option_d"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append((key, value))
    return values


def _has_unbalanced_dollars(text: str) -> bool:
    return len(re.findall(r"(?<!\\)\$", text or "")) % 2 == 1


def _strip_latex_and_code(text: str) -> str:
    text = LATEX_SPAN_RE.sub(" ", text or "")
    text = INLINE_CODE_RE.sub(" ", text)
    return text


def _media_issues(question: dict[str, Any]) -> list[str]:
    value = question.get("media")
    stem = _get_text(question, "stem", "question")
    refs = _media_refs(stem)
    if value is None:
        return [f"stem references [media:{media_id}] but media item is missing" for media_id in sorted(refs)]
    if not isinstance(value, list):
        return ["media must be a list of objects"]

    issues: list[str] = []
    item_ids: set[str] = set()
    for index, item in enumerate(value):
        label = f"media[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        media_id = str(item.get("id") or "").strip()
        if not media_id:
            issues.append(f"{label}.id is required")
        else:
            item_ids.add(media_id)
        media_type = str(item.get("type") or "").strip()
        if media_type not in LEGAL_MEDIA_TYPES:
            issues.append(f"{label}.type must be one of {sorted(LEGAL_MEDIA_TYPES)}")
            continue
        if media_id and media_type in {"image", "mermaid", "canvas"} and media_id not in refs:
            issues.append(f"{label}.id is not referenced by [media:{media_id}] in stem")
        if media_type == "image" and not str(item.get("src") or "").strip():
            issues.append(f"{label}.src is required for image")
        if media_type == "mermaid":
            content = str(item.get("content") or "").strip()
            if not content:
                issues.append(f"{label}.content is required for mermaid")
            elif not _valid_mermaid_content(content):
                issues.append(f"{label}.content is not in the supported Mermaid subset")
        if media_type == "canvas":
            issues.extend(_canvas_item_issues(item, label))
    missing_refs = sorted(ref for ref in refs if ref not in item_ids)
    for media_id in missing_refs:
        issues.append(f"stem references [media:{media_id}] but media item is missing")
    return issues


def _mermaid_media_issues(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    issues: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or item.get("type") != "mermaid":
            continue
        content = str(item.get("content") or "").strip()
        if not content or not _valid_mermaid_content(content):
            issues.append(f"media[{index}].content is not in the supported Mermaid subset")
    return issues


def _canvas_media_issues(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    issues: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or item.get("type") != "canvas":
            continue
        issues.extend(_canvas_item_issues(item, f"media[{index}]"))
    return issues


def _canvas_item_issues(item: dict[str, Any], label: str) -> list[str]:
    issues: list[str] = []
    config = item.get("config")
    if not isinstance(config, dict):
        return [f"{label}.config is required for canvas"]

    canvas_type = str(config.get("canvas_type") or "").strip()
    if not canvas_type:
        issues.append(f"{label}.config.canvas_type is required")
    elif canvas_type not in LEGAL_CANVAS_TYPES:
        issues.append(f"{label}.config.canvas_type must be one of {sorted(LEGAL_CANVAS_TYPES)}")

    initial_state = config.get("initial_state")
    if initial_state is not None and not isinstance(initial_state, dict):
        issues.append(f"{label}.config.initial_state must be an object")
    if initial_state is not None:
        issues.extend(_canvas_state_issues(initial_state, f"{label}.config.initial_state"))

    tools = config.get("tools")
    if tools is not None and not isinstance(tools, list):
        issues.append(f"{label}.config.tools must be a list")

    expected_answer = item.get("expected_answer")
    if expected_answer is not None and not isinstance(expected_answer, dict):
        issues.append(f"{label}.expected_answer must be an object")

    judge_schema = item.get("judge_schema")
    if judge_schema is not None:
        if not isinstance(judge_schema, dict):
            issues.append(f"{label}.judge_schema must be an object")
        else:
            mode = str(judge_schema.get("mode") or "").strip()
            if mode and mode not in LEGAL_CANVAS_JUDGE_MODES:
                issues.append(f"{label}.judge_schema.mode must be one of {sorted(LEGAL_CANVAS_JUDGE_MODES)}")
    return issues


def _canvas_state_issues(state: dict[str, Any], label: str) -> list[str]:
    issues: list[str] = []
    nodes = state.get("nodes")
    if nodes is not None and not isinstance(nodes, list):
        issues.append(f"{label}.nodes must be a list")
    edges = state.get("edges")
    if edges is not None and not isinstance(edges, list):
        issues.append(f"{label}.edges must be a list")
    return issues


def _media_refs(text: str) -> set[str]:
    return {match.group(1).strip() for match in MEDIA_REF_RE.finditer(text or "") if match.group(1).strip()}


def _question_media_types(question: dict[str, Any]) -> set[str]:
    media = question.get("media")
    if not isinstance(media, list):
        return set()
    return {
        str(item.get("type") or "").strip()
        for item in media
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }


def _has_media(question: dict[str, Any]) -> bool:
    media = question.get("media")
    return isinstance(media, list) and any(isinstance(item, dict) for item in media)


def _visual_reference_issues(question: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    stem = _get_text(question, "stem", "question")
    refs = _media_refs(stem)
    if not refs:
        issues.append("stem must contain [media:id] when media is present")
    if not _has_visual_cue(stem):
        issues.append("stem should explicitly say the question is based on the figure/diagram")
    return issues


def _readonly_media_issues(question: dict[str, Any]) -> list[str]:
    text = "\n".join(_student_facing_text_values(question))
    if INTERACTIVE_MEDIA_RE.search(text):
        return ["student-facing text asks the learner to operate the figure/canvas"]
    return []


def _media_description_issues(question: dict[str, Any]) -> list[str]:
    media = question.get("media")
    if not isinstance(media, list):
        return []
    issues: list[str] = []
    for index, item in enumerate(media):
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("type") or "").strip()
        if media_type not in {"image", "mermaid", "canvas"}:
            continue
        desc = str(item.get("description") or "").strip()
        if not desc:
            issues.append(f"media[{index}].description is required for read-only media questions")
            continue
        if any(term.lower() in desc.lower() for term in PLACEHOLDER_DESCRIPTION_TERMS):
            issues.append(f"media[{index}].description appears to be a placeholder")
    return issues


def _has_visual_cue(text: str) -> bool:
    text = text or ""
    if any(term in text for term in VISUAL_CUE_TERMS):
        return True
    return "图" in text and any(term in text for term in ("根据", "结合", "观察"))


def _student_facing_text_values(question: dict[str, Any]) -> list[str]:
    values = []
    for key in ("stem", "question"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    options = question.get("options")
    if isinstance(options, list):
        values.extend(str(item) for item in options if str(item).strip())
    for key in ("option_a", "option_b", "option_c", "option_d"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    return values


def _valid_mermaid_content(content: str) -> bool:
    text = str(content or "").strip()
    if not text or len(text) > 4000:
        return False
    if "```" in text:
        return False
    if MERMAID_FORBIDDEN_RE.search(text):
        return False
    return bool(MERMAID_START_RE.search(text))


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


def _optional_rate_metric(name: str, passed_count: int, total: int, detail: str) -> EvalMetric:
    threshold = DEFAULT_THRESHOLDS[name]
    if total <= 0:
        return EvalMetric(
            name=name,
            value=1.0,
            threshold=threshold,
            passed=True,
            detail=f"0/0，无适用样本；{detail}",
        )
    value = passed_count / total
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
