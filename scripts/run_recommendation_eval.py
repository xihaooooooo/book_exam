"""Run the Phase 3 recommendation-strategy offline evaluation."""

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

from exam.evaluation.recommendation_eval import run_recommendation_eval
from exam.evaluation.report import save_eval_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate BKT + Bandit recommendation with synthetic playback cases.",
    )
    parser.add_argument(
        "--cases-file",
        default=str(PROJECT_ROOT / "evals" / "cases" / "recommendation_cases.json"),
        help="Path to recommendation_cases.json.",
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
        help="Run one recommendation case, for example rec_001.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N recommendation cases.",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=8,
        help="Target question count passed into build_recommendation_plan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    report = run_recommendation_eval(
        cases_file=args.cases_file,
        case_id=args.case_id,
        limit=args.limit,
        target_count=args.target_count,
    )
    json_path, md_path = save_eval_report(
        report,
        output_dir=args.output_dir,
        filename_prefix="recommendation_eval",
    )

    print(report.summary)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
