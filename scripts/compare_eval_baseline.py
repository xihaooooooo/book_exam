"""Compare an offline Agent eval report against the current baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "evals" / "reports"
TOP_METRIC_ORDER = [
    "overall_score",
    "generation_score",
    "judge_score",
    "recommendation_score",
]
LOWER_IS_BETTER_SUFFIXES = (
    "duplicate_rate",
    "false_positive_rate",
    "false_negative_rate",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare an offline Agent eval report against evals/reports/baseline.json.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--latest",
        action="store_true",
        help="Compare the latest run in evals/reports/index.json against baseline.",
    )
    target.add_argument(
        "--report",
        help="Path to an agent_eval_*.json report to compare against baseline.",
    )
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_REPORTS_DIR / "baseline.json"),
        help="Path to baseline.json.",
    )
    parser.add_argument(
        "--index",
        default=str(DEFAULT_REPORTS_DIR / "index.json"),
        help="Path to report index.json, used with --latest.",
    )
    parser.add_argument(
        "--show-details",
        action="store_true",
        help="Print detailed metric diffs from metadata.metric_snapshot.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 1 when a metric regresses or new failures appear.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-9,
        help="Float tolerance for metric comparisons.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.latest and not args.report:
        args.latest = True

    try:
        baseline_path = resolve_path(args.baseline)
        baseline_pointer = load_json(baseline_path)
        baseline_report_path = resolve_report_path(
            baseline_pointer["reports"]["agent_json"],
            baseline_path.parent,
        )
        baseline_report = load_json(baseline_report_path)

        current_report_path = (
            latest_report_path(resolve_path(args.index))
            if args.latest
            else resolve_path(args.report)
        )
        current_report = load_json(current_report_path)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"读取 baseline 对比输入失败：{exc}", file=sys.stderr)
        return 2

    comparison = compare_reports(
        baseline_report=baseline_report,
        current_report=current_report,
        tolerance=args.tolerance,
    )
    print(
        render_comparison(
            baseline_pointer=baseline_pointer,
            baseline_report=baseline_report,
            baseline_report_path=baseline_report_path,
            current_report=current_report,
            current_report_path=current_report_path,
            comparison=comparison,
            show_details=args.show_details,
        )
    )

    if args.fail_on_regression and comparison["has_regression"]:
        return 1
    return 0


def resolve_path(value: str | Path | None) -> Path:
    if value is None:
        raise ValueError("缺少报告路径")
    path = Path(value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def resolve_report_path(value: str | Path, fallback_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = (PROJECT_ROOT / path).resolve()
    if project_path.exists():
        return project_path
    return (fallback_dir / path).resolve()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 JSON object")
    return data


def latest_report_path(index_path: Path) -> Path:
    index = load_json(index_path)
    runs = index.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"{index_path} 没有可用 runs")
    latest = runs[-1]
    if not isinstance(latest, dict):
        raise ValueError(f"{index_path} 最新 run 格式无效")
    report_path = latest.get("agent_report_json")
    if not report_path:
        raise ValueError(f"{index_path} 最新 run 缺少 agent_report_json")
    return resolve_report_path(report_path, index_path.parent)


def compare_reports(
    baseline_report: dict[str, Any],
    current_report: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    top_diffs = diff_metrics(
        metric_map(baseline_report.get("metrics", [])),
        metric_map(current_report.get("metrics", [])),
        tolerance,
        TOP_METRIC_ORDER,
    )
    detail_diffs = diff_metrics(
        detail_metric_map(baseline_report),
        detail_metric_map(current_report),
        tolerance,
    )

    baseline_failures = failure_map(baseline_report)
    current_failures = failure_map(current_report)
    baseline_failure_ids = set(baseline_failures)
    current_failure_ids = set(current_failures)
    new_failure_ids = sorted(current_failure_ids - baseline_failure_ids)
    fixed_failure_ids = sorted(baseline_failure_ids - current_failure_ids)
    persistent_failure_ids = sorted(current_failure_ids & baseline_failure_ids)

    score_regressions = [
        item for item in top_diffs
        if item["status"] == "下降" and not is_lower_better(item["name"])
    ]
    detail_regressions = [
        item for item in detail_diffs
        if item["status"] == "下降"
    ]
    has_regression = bool(score_regressions or detail_regressions or new_failure_ids)

    return {
        "top_diffs": top_diffs,
        "detail_diffs": detail_diffs,
        "baseline_failures": baseline_failures,
        "current_failures": current_failures,
        "new_failure_ids": new_failure_ids,
        "fixed_failure_ids": fixed_failure_ids,
        "persistent_failure_ids": persistent_failure_ids,
        "score_regressions": score_regressions,
        "detail_regressions": detail_regressions,
        "has_regression": has_regression,
    }


def metric_map(metrics: Any) -> dict[str, float]:
    if not isinstance(metrics, list):
        return {}
    result: dict[str, float] = {}
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name = str(metric.get("name", ""))
        if not name:
            continue
        value = metric.get("value")
        if isinstance(value, (int, float)):
            result[name] = float(value)
    return result


def detail_metric_map(report: dict[str, Any]) -> dict[str, float]:
    metadata = report.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    snapshot = metadata.get("metric_snapshot")
    if not isinstance(snapshot, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in snapshot.items():
        if isinstance(value, (int, float)):
            result[str(name)] = float(value)
    return result


def diff_metrics(
    baseline_metrics: dict[str, float],
    current_metrics: dict[str, float],
    tolerance: float,
    preferred_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    names = sorted(set(baseline_metrics) | set(current_metrics))
    if preferred_order:
        order = {name: idx for idx, name in enumerate(preferred_order)}
        names.sort(key=lambda name: (order.get(name, len(order)), name))

    diffs: list[dict[str, Any]] = []
    for name in names:
        baseline_value = baseline_metrics.get(name)
        current_value = current_metrics.get(name)
        delta = None
        status = "缺失"
        if baseline_value is not None and current_value is not None:
            delta = current_value - baseline_value
            status = classify_delta(name, delta, tolerance)
        elif baseline_value is None:
            status = "新增"

        diffs.append({
            "name": name,
            "baseline": baseline_value,
            "current": current_value,
            "delta": delta,
            "status": status,
        })
    return diffs


def classify_delta(name: str, delta: float, tolerance: float) -> str:
    if abs(delta) <= tolerance:
        return "持平"
    improved = delta < 0 if is_lower_better(name) else delta > 0
    return "提升" if improved else "下降"


def is_lower_better(name: str) -> bool:
    return name.endswith(LOWER_IS_BETTER_SUFFIXES)


def failure_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    failures = report.get("failures", [])
    if not isinstance(failures, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for idx, failure in enumerate(failures):
        if not isinstance(failure, dict):
            continue
        case_id = str(failure.get("case_id") or failure.get("item_id") or f"failure_{idx}")
        result[case_id] = failure
    return result


def render_comparison(
    baseline_pointer: dict[str, Any],
    baseline_report: dict[str, Any],
    baseline_report_path: Path,
    current_report: dict[str, Any],
    current_report_path: Path,
    comparison: dict[str, Any],
    show_details: bool,
) -> str:
    lines: list[str] = [
        "# 离线 baseline 对比",
        "",
        f"- Baseline Run：`{baseline_report.get('run_id', '')}`",
        f"- 当前 Run：`{current_report.get('run_id', '')}`",
        f"- Baseline 报告：`{baseline_report_path}`",
        f"- 当前报告：`{current_report_path}`",
        f"- Baseline 摘要：{baseline_pointer.get('summary', baseline_report.get('summary', ''))}",
        f"- 当前摘要：{current_report.get('summary', '')}",
    ]

    if baseline_report.get("run_id") == current_report.get("run_id"):
        lines.append("- 说明：当前比较对象就是 baseline 本身。")

    lines.extend([
        "",
        "## 核心指标",
        "",
        "| 指标 | Baseline | 当前 | 变化 | 状态 |",
        "|---|---:|---:|---:|---|",
    ])
    for item in comparison["top_diffs"]:
        lines.append(metric_row(item))

    lines.extend([
        "",
        "## 失败项",
        "",
        f"- Baseline 失败项：`{len(comparison['baseline_failures'])}`",
        f"- 当前失败项：`{len(comparison['current_failures'])}`",
        f"- 新增失败项：`{len(comparison['new_failure_ids'])}`",
        f"- 已修复失败项：`{len(comparison['fixed_failure_ids'])}`",
        f"- 仍然存在：`{len(comparison['persistent_failure_ids'])}`",
    ])
    append_failure_group(lines, "新增失败项", comparison["new_failure_ids"], comparison["current_failures"])
    append_failure_group(lines, "已修复失败项", comparison["fixed_failure_ids"], comparison["baseline_failures"])
    append_failure_group(lines, "仍然存在的失败项", comparison["persistent_failure_ids"], comparison["current_failures"])

    if show_details:
        lines.extend([
            "",
            "## 详细指标",
            "",
            "| 指标 | Baseline | 当前 | 变化 | 状态 |",
            "|---|---:|---:|---:|---|",
        ])
        for item in comparison["detail_diffs"]:
            lines.append(metric_row(item))

    lines.extend(["", "## 结论", ""])
    if comparison["has_regression"]:
        lines.append("存在相对 baseline 的回归风险：核心分数、详细指标下降，或出现了新增失败项。")
    elif baseline_report.get("run_id") == current_report.get("run_id"):
        lines.append("当前最新结果就是 baseline，未发现差异。")
    else:
        lines.append("未发现相对 baseline 的回归风险。")

    return "\n".join(lines)


def metric_row(item: dict[str, Any]) -> str:
    return (
        f"| `{item['name']}` | {format_metric(item['baseline'])} | "
        f"{format_metric(item['current'])} | {format_delta(item['delta'])} | "
        f"{item['status']} |"
    )


def append_failure_group(
    lines: list[str],
    title: str,
    failure_ids: list[str],
    failures: dict[str, dict[str, Any]],
) -> None:
    lines.extend(["", f"### {title}", ""])
    if not failure_ids:
        lines.append("无。")
        return
    lines.extend(["| Case | 原因 |", "|---|---|"])
    for case_id in failure_ids:
        failure = failures.get(case_id, {})
        reason = str(failure.get("reason", ""))
        lines.append(f"| `{case_id}` | {escape_table(reason)} |")


def format_metric(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2%}"


def format_delta(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.2f}pp"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
