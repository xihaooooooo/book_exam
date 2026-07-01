"""Run generation-quality evaluation over saved or live-generated questions."""

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

from exam.config import DEFAULT_CONFIG
from exam.evaluation.generation_eval import run_generation_eval, run_live_generation_eval
from exam.evaluation.generation_review import REVIEW_MODEL_ENV, attach_generation_review
from exam.evaluation.llm_client import create_eval_llm_client, eval_llm_summary
from exam.evaluation.report import save_eval_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate saved questions or live LLM generated questions with quality metrics.",
    )
    parser.add_argument(
        "--cases-file",
        default=str(PROJECT_ROOT / "evals" / "cases" / "generation_cases.json"),
        help="Path to generation_cases.json.",
    )
    parser.add_argument(
        "--questions-file",
        default=None,
        help="Path to a questions JSON file. If omitted, latest output/questions_*.json is used.",
    )
    parser.add_argument(
        "--use-latest-output",
        action="store_true",
        help="Evaluate the latest output/questions_*.json file.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "evals" / "reports"),
        help="Directory where JSON and Markdown reports are written.",
    )
    parser.add_argument(
        "--case",
        dest="case_id",
        default=None,
        help="Restrict evaluation constraints to one generation case, for example gen_001.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N saved questions. Live mode uses --live-max-questions.",
    )
    parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=0.90,
        help="SequenceMatcher threshold for duplicate stem detection.",
    )
    parser.add_argument(
        "--generate-with-llm",
        action="store_true",
        help="Use the real ExamGraph and configured LLM to generate fresh questions before evaluation.",
    )
    parser.add_argument(
        "--generated-output-dir",
        default=str(PROJECT_ROOT / "evals" / "generated"),
        help="Directory for live LLM generated questions.",
    )
    parser.add_argument(
        "--live-max-questions",
        type=int,
        default=5,
        help="Maximum questions to generate in --generate-with-llm mode.",
    )
    parser.add_argument(
        "--llm-review",
        action="store_true",
        help="Use a real LLM as an expert reviewer for generated questions.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        create_eval_llm_client(enabled=args.generate_with_llm)
        review_llm_client = create_eval_llm_client(
            enabled=args.llm_review,
            model_env_var=REVIEW_MODEL_ENV,
        )
    except RuntimeError as exc:
        print(f"创建 LLM 客户端失败：{exc}", file=sys.stderr)
        return 2

    if args.generate_with_llm:
        report = run_live_generation_eval(
            cases_file=args.cases_file,
            generated_output_dir=args.generated_output_dir,
            db_path=DEFAULT_CONFIG["db_path"],
            case_id=args.case_id,
            max_questions=args.live_max_questions,
            duplicate_threshold=args.duplicate_threshold,
            llm_metadata=eval_llm_summary(enabled=True),
        )
    else:
        report = run_generation_eval(
            cases_file=args.cases_file,
            questions_file=args.questions_file,
            output_dir=DEFAULT_CONFIG["results_dir"],
            use_latest_output=args.use_latest_output,
            case_id=args.case_id,
            limit=args.limit,
            duplicate_threshold=args.duplicate_threshold,
        )
    if args.llm_review:
        report = attach_generation_review(
            report=report,
            llm_client=review_llm_client,
            db_path=DEFAULT_CONFIG["db_path"],
            llm_metadata=eval_llm_summary(
                enabled=True,
                model_env_var=REVIEW_MODEL_ENV,
            ),
        )
    json_path, md_path = save_eval_report(
        report,
        output_dir=args.output_dir,
        filename_prefix="generation_eval",
    )

    print(report.summary)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
