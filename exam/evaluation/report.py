"""Report rendering helpers for offline evaluations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from exam.evaluation.schemas import EvalReport


def save_eval_report(
    report: EvalReport,
    output_dir: str | Path,
    filename_prefix: str | None = None,
) -> tuple[Path, Path]:
    """Save one report as JSON and Markdown."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    prefix = filename_prefix or report.eval_type
    stem = f"{prefix}_{report.run_id}"
    json_path = output_path / f"{stem}.json"
    md_path = output_path / f"{stem}.md"

    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, md_path


def render_markdown_report(report: EvalReport) -> str:
    """Render an EvalReport into a compact Markdown artifact."""
    if report.eval_type == "all":
        return _render_all_markdown_report(report)

    title_map = {
        "generation": "# 出题质量评测报告",
        "judge": "# 判题一致性评测报告",
        "recommendation": "# 推荐策略评测报告",
        "all": "# Agent 离线评测报告",
    }
    lines: list[str] = [
        title_map.get(report.eval_type, "# Agent 离线评测报告"),
        "",
        "## 总览",
        "",
        f"- 运行编号：`{report.run_id}`",
        f"- 创建时间：`{report.created_at}`",
        f"- 摘要：{report.summary}",
    ]

    if report.metadata:
        lines.extend(["", "## 元数据", ""])
        for key, value in report.metadata.items():
            if key == "llm_review_results" and isinstance(value, list):
                lines.append(f"- `{key}`: `{len(value)} items`")
            else:
                lines.append(f"- `{key}`: `{_compact_value(value)}`")

    lines.extend([
        "",
        "## 指标",
        "",
        "| 指标 | 数值 | 阈值 | 结果 | 说明 |",
        "|---|---:|---:|---|---|",
    ])

    for metric in report.metrics:
        threshold = _format_threshold(metric.name, metric.threshold)
        result = "PASS" if metric.passed else "FAIL"
        lines.append(
            f"| `{metric.name}` | {_format_metric_value(metric.name, metric.value)} | "
            f"{threshold} | {result} | {_escape_table(metric.detail)} |"
        )

    if report.eval_type == "generation" and report.metadata.get("llm_review_results"):
        lines.extend(_render_generation_review_section(report.metadata["llm_review_results"]))

    lines.extend(["", "## 失败明细", ""])
    if not report.failures:
        lines.append("无失败项。")
    else:
        lines.extend([
            "| Case | 题目 | 原因 | 证据 |",
            "|---|---|---|---|",
        ])
        for failure in report.failures:
            evidence = json.dumps(failure.evidence, ensure_ascii=False)
            lines.append(
                f"| `{failure.case_id}` | `{failure.item_id}` | "
                f"{_escape_table(failure.reason)} | {_escape_table(evidence[:500])} |"
            )

    lines.append("")
    return "\n".join(lines)


def _render_generation_review_section(results: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## LLM 审稿明细",
        "",
        "| Case | 题目 | 结论 | 总分 | 相关性 | 正确性 | 难度匹配 | 主要问题 | 建议 |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for item in results:
        issues = item.get("issues", [])
        if isinstance(issues, list):
            issue_text = "；".join(str(issue) for issue in issues[:3])
        else:
            issue_text = str(issues)
        lines.append(
            f"| `{item.get('case_id', '')}` | `{item.get('item_id', '')}` | "
            f"{_escape_table(item.get('verdict', ''))} | "
            f"{_format_percent(item.get('overall', 0.0))} | "
            f"{_format_percent(item.get('relevance', 0.0))} | "
            f"{_format_percent(item.get('correctness', 0.0))} | "
            f"{_format_percent(item.get('difficulty_fit', 0.0))} | "
            f"{_escape_table(issue_text[:300])} | "
            f"{_escape_table(str(item.get('suggestion', ''))[:300])} |"
        )
    return lines


def _compact_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _format_metric_value(name: str, value: float) -> str:
    if name.endswith("_rate") or name.endswith("_pass") or name.endswith("_score"):
        return f"{value:.2%}"
    return f"{value:.4f}"


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "-"


def _format_threshold(name: str, threshold: float | None) -> str:
    if threshold is None:
        return "-"
    op = "<=" if name in {"duplicate_rate", "false_positive_rate", "false_negative_rate"} else ">="
    return f"{op} {threshold:.2%}"


def _escape_table(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def _render_all_markdown_report(report: EvalReport) -> str:
    sections = report.metadata.get("sections", {})
    diffs = report.metadata.get("metric_diffs", [])
    lines: list[str] = [
        "# Agent 离线评测报告",
        "",
        "## 总览",
        "",
        f"- 运行编号：`{report.run_id}`",
        f"- 创建时间：`{report.created_at}`",
        f"- 摘要：{report.summary}",
        "",
        "| 指标 | 数值 | 阈值 | 结果 | 说明 |",
        "|---|---:|---:|---|---|",
    ]

    for metric in report.metrics:
        result = "PASS" if metric.passed else "FAIL"
        lines.append(
            f"| `{metric.name}` | {_format_metric_value(metric.name, metric.value)} | "
            f"{_format_threshold(metric.name, metric.threshold)} | {result} | "
            f"{_escape_table(metric.detail)} |"
        )

    for eval_type, title in [
        ("generation", "出题质量"),
        ("judge", "判题质量"),
        ("recommendation", "推荐质量"),
    ]:
        section = sections.get(eval_type, {})
        lines.extend([
            "",
            f"## {title}",
            "",
            f"- 摘要：{section.get('summary', '未运行')}",
            f"- JSON：`{section.get('json_path', '')}`",
            f"- Markdown：`{section.get('md_path', '')}`",
            "",
            "| 指标 | 数值 | 阈值 | 结果 | 说明 |",
            "|---|---:|---:|---|---|",
        ])
        for metric in section.get("metrics", []):
            result = "PASS" if metric.get("passed") else "FAIL"
            name = metric.get("name", "")
            value = float(metric.get("value", 0.0))
            threshold = metric.get("threshold")
            lines.append(
                f"| `{name}` | {_format_metric_value(name, value)} | "
                f"{_format_threshold(name, threshold)} | {result} | "
                f"{_escape_table(metric.get('detail', ''))} |"
            )

    lines.extend(["", "## 回归对比", ""])
    if not diffs:
        lines.append("无上一轮记录，暂不生成 diff。")
    else:
        lines.extend([
            "| 指标 | 本次 | 上次 | 变化 | 状态 |",
            "|---|---:|---:|---:|---|",
        ])
        for item in diffs:
            name = str(item.get("name", ""))
            current = item.get("current", "")
            previous = item.get("previous", "")
            delta = item.get("delta", "")
            lines.append(
                f"| `{name}` | {_format_diff_number(current)} | "
                f"{_format_diff_number(previous)} | {_format_diff_number(delta, signed=True)} | "
                f"{item.get('status', '')} |"
            )

    lines.extend(["", "## 失败明细", ""])
    if not report.failures:
        lines.append("无失败项。")
    else:
        lines.extend([
            "| Case | 题目 | 原因 | 证据 |",
            "|---|---|---|---|",
        ])
        for failure in report.failures:
            evidence = json.dumps(failure.evidence, ensure_ascii=False)
            lines.append(
                f"| `{failure.case_id}` | `{failure.item_id}` | "
                f"{_escape_table(failure.reason)} | {_escape_table(evidence[:500])} |"
            )

    lines.append("")
    return "\n".join(lines)


def _format_diff_number(value: Any, signed: bool = False) -> str:
    if value == "" or value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if signed:
        return f"{number:+.2%}"
    return f"{number:.2%}"
