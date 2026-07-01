"""Run Agent evals and build an aggregate report."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from exam.evaluation.all_eval import run_all_evals
from exam.evaluation.generation_review import REVIEW_MODEL_ENV
from exam.evaluation.llm_client import create_eval_llm_client, eval_llm_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run generation, judge, and recommendation evals, then aggregate them.",
    )
    parser.add_argument(
        "--cases-dir",
        default=str(PROJECT_ROOT / "evals" / "cases"),
        help="Directory containing generation/judge/recommendation case files.",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(PROJECT_ROOT / "evals" / "reports"),
        help="Directory where all reports and index.json are written.",
    )
    parser.add_argument(
        "--questions-file",
        default=None,
        help="Questions JSON for generation eval. If omitted, latest output/questions_*.json is used.",
    )
    parser.add_argument(
        "--generation-limit",
        type=int,
        default=None,
        help="Limit saved-question generation eval to N questions; in live generation mode, generate at most N questions.",
    )
    parser.add_argument(
        "--judge-limit",
        type=int,
        default=None,
        help="Limit judge eval to the first N cases.",
    )
    parser.add_argument(
        "--recommendation-limit",
        type=int,
        default=None,
        help="Limit recommendation eval to the first N cases.",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=8,
        help="Target question count for recommendation eval.",
    )
    parser.add_argument(
        "--use-llm-judge",
        action="store_true",
        help="Use the configured real LLM for JudgeGraph semantic judge cases and diagnosis.",
    )
    parser.add_argument(
        "--generate-with-llm",
        action="store_true",
        help="Use the real ExamGraph and configured LLM to generate fresh questions before generation eval.",
    )
    parser.add_argument(
        "--generated-output-dir",
        default=str(PROJECT_ROOT / "evals" / "generated"),
        help="Directory for live LLM generated questions.",
    )
    parser.add_argument(
        "--llm-review-generation",
        action="store_true",
        help="Use a real LLM as an expert reviewer for generation eval questions.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        llm_client = create_eval_llm_client(
            enabled=args.use_llm_judge or args.generate_with_llm,
        )
        generation_review_llm_client = create_eval_llm_client(
            enabled=args.llm_review_generation,
            model_env_var=REVIEW_MODEL_ENV,
        )
    except RuntimeError as exc:
        print(f"创建 LLM 客户端失败：{exc}", file=sys.stderr)
        return 2
    judge_llm_client = llm_client if args.use_llm_judge else None
    generation_llm_metadata = eval_llm_summary(enabled=args.generate_with_llm)
    generation_review_llm_metadata = eval_llm_summary(
        enabled=args.llm_review_generation,
        model_env_var=REVIEW_MODEL_ENV,
    )
    judge_llm_metadata = eval_llm_summary(enabled=args.use_llm_judge)

    result = run_all_evals(
        cases_dir=args.cases_dir,
        reports_dir=args.reports_dir,
        questions_file=args.questions_file,
        use_latest_output=True,
        generation_limit=args.generation_limit,
        judge_limit=args.judge_limit,
        recommendation_limit=args.recommendation_limit,
        recommendation_target_count=args.target_count,
        generate_with_llm=args.generate_with_llm,
        live_generation_output_dir=args.generated_output_dir,
        generation_llm_metadata=generation_llm_metadata,
        generation_review_llm_client=generation_review_llm_client,
        generation_review_llm_metadata=generation_review_llm_metadata,
        judge_llm_client=judge_llm_client,
        judge_llm_metadata=judge_llm_metadata,
    )

    print(result.report.summary)
    print(f"Aggregate JSON report: {result.json_path}")
    print(f"Aggregate Markdown report: {result.md_path}")
    print(f"Report index: {result.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
