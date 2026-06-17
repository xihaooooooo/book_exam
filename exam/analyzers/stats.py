"""统计报告生成器：汇总分析结果 → JSON + Markdown"""

import json
import os
from datetime import datetime

TYPE_LABELS = {
    "choice": "选择题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
    "code_fill": "代码填空题",
    "comprehensive": "综合题",
}

DIFF_LABELS = {"easy": "简单", "medium": "中等", "hard": "困难"}


def _per_exam_stats(exam: dict) -> dict:
    """单份试卷的统计"""
    questions = exam.get("questions", [])
    total = len(questions)

    type_dist = {}
    diff_dist = {}
    topic_freq = {}

    for q in questions:
        t = q["question_type"]
        type_dist[t] = type_dist.get(t, 0) + 1
        d = q["difficulty"]
        diff_dist[d] = diff_dist.get(d, 0) + 1
        topic = q["topic"]
        topic_freq[topic] = topic_freq.get(topic, 0) + 1

    return {
        "title": exam.get("title", ""),
        "filename": exam.get("filename", ""),
        "question_count": total,
        "type_distribution": type_dist,
        "difficulty_distribution": diff_dist,
        "topic_frequency": topic_freq,
        "questions": questions,
    }


def _aggregate_stats(per_exam_list: list[dict]) -> dict:
    """跨试卷聚合统计"""
    all_questions = []
    for exam in per_exam_list:
        all_questions.extend(exam.get("questions", []))

    total = len(all_questions)
    if total == 0:
        return {"total_questions": 0}

    type_dist = {}
    diff_dist = {}
    topic_freq = {}

    for q in all_questions:
        t = q["question_type"]
        type_dist[t] = type_dist.get(t, 0) + 1
        d = q["difficulty"]
        diff_dist[d] = diff_dist.get(d, 0) + 1
        topic = q["topic"]
        topic_freq[topic] = topic_freq.get(topic, 0) + 1

    return {
        "total_questions": total,
        "exam_count": len(per_exam_list),
        "type_distribution": type_dist,
        "difficulty_distribution": diff_dist,
        "topic_frequency": topic_freq,
    }


def _markdown_report(report: dict) -> str:
    lines = ["# 往年试卷分析报告\n"]

    aggregated = report.get("aggregated", {})
    lines.append(f"## 总览\n")
    lines.append(f"- 试卷数：{aggregated.get('exam_count', 0)} 份")
    lines.append(f"- 总题数：{aggregated.get('total_questions', 0)} 道\n")

    # 题型分布
    lines.append("## 题型分布\n")
    lines.append("| 题型 | 数量 | 占比 |")
    lines.append("|------|------|------|")
    total = aggregated.get("total_questions", 1) or 1
    for t, label in TYPE_LABELS.items():
        count = aggregated.get("type_distribution", {}).get(t, 0)
        if count:
            lines.append(f"| {label} | {count} | {count/total*100:.0f}% |")

    # 难度分布
    lines.append("\n## 难度分布\n")
    lines.append("| 难度 | 数量 | 占比 |")
    lines.append("|------|------|------|")
    for d, label in DIFF_LABELS.items():
        count = aggregated.get("difficulty_distribution", {}).get(d, 0)
        if count:
            lines.append(f"| {label} | {count} | {count/total*100:.0f}% |")

    # 考点频率（按频次降序）
    lines.append("\n## 考点频率\n")
    lines.append("| 考点 | 出现次数 |")
    lines.append("|------|----------|")
    topic_freq = aggregated.get("topic_frequency", {})
    for topic, count in sorted(topic_freq.items(), key=lambda x: -x[1]):
        lines.append(f"| {topic} | {count} |")

    # 逐卷详情
    for exam in report.get("exams", []):
        qc = exam.get("question_count", 0)
        lines.append(f"\n## {exam.get('title', '未知试卷')}\n")
        lines.append(f"- 文件：{exam.get('filename', '')}")
        lines.append(f"- 题数：{qc} 道\n")

        # 题型
        td = exam.get("type_distribution", {})
        if td:
            parts = [f"{TYPE_LABELS.get(k, k)} {v} 道" for k, v in td.items()]
            lines.append(f"- 题型：{' / '.join(parts)}")

        # 难度
        dd = exam.get("difficulty_distribution", {})
        if dd:
            parts = [f"{DIFF_LABELS.get(k, k)} {v} 道" for k, v in dd.items()]
            lines.append(f"- 难度：{' / '.join(parts)}")

        # 考点 top5
        tf = exam.get("topic_frequency", {})
        if tf:
            top = sorted(tf.items(), key=lambda x: -x[1])[:5]
            parts = [f"{t}（{c}次）" for t, c in top]
            lines.append(f"- 高频考点：{'、'.join(parts)}")

    return "\n".join(lines) + "\n"


def generate_report(analyzed_exams: list[dict], output_dir: str) -> str:
    """生成 JSON 和 Markdown 报告。

    Args:
        analyzed_exams: analyze_exam 的输出列表
        output_dir: 输出目录

    Returns:
        报告 JSON 的文件路径
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    per_exam = [_per_exam_stats(e) for e in analyzed_exams]

    report = {
        "generated_at": datetime.now().isoformat(),
        "exams": per_exam,
        "aggregated": _aggregate_stats(per_exam),
    }

    # JSON
    json_path = os.path.join(output_dir, f"report_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Markdown
    md_path = os.path.join(output_dir, f"report_{timestamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_markdown_report(report))

    return json_path
