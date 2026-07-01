"""Run the Phase 2 JudgeGraph agreement offline evaluation."""

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

from exam.evaluation.judge_eval import run_judge_eval
from exam.evaluation.llm_client import create_eval_llm_client, eval_llm_summary
from exam.evaluation.report import save_eval_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate JudgeGraph against fixed golden judge cases.",
    )
    parser.add_argument(
        "--cases-file",
        default=str(PROJECT_ROOT / "evals" / "cases" / "judge_cases.json"),
        help="Path to judge_cases.json.",
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
        help="Run one judge case, for example judge_001.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N judge cases.",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use the configured real LLM for semantic judge cases and diagnosis.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        llm_client = create_eval_llm_client(enabled=args.use_llm)
    except RuntimeError as exc:
        print(f"创建 LLM 客户端失败：{exc}", file=sys.stderr)
        return 2
    llm_summary = eval_llm_summary(enabled=args.use_llm)

    report = run_judge_eval(
        cases_file=args.cases_file,
        case_id=args.case_id,
        limit=args.limit,
        llm_client=llm_client,
    )
    report.metadata["llm"] = llm_summary
    json_path, md_path = save_eval_report(
        report,
        output_dir=args.output_dir,
        filename_prefix="judge_eval",
    )

    print(report.summary)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
