"""查看学生画像。

用法：
    python show_profile.py --student S001
    python show_profile.py --student S001 --weak-only
    python show_profile.py --student S001 --json
    python show_profile.py --student S001 --db cache/attempts.db
"""

import argparse
import json
import os
import sys

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from exam.graph.profile_graph import ProfileGraph
from exam.student_profile.schemas import ERROR_TYPE_LABELS, ERROR_PRIORITY

DB_PATH = os.path.join(_project_root, "cache", "attempts.db")


def main():
    parser = argparse.ArgumentParser(description="学生画像查看")
    parser.add_argument("--student", required=True, help="学生 ID")
    parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--weak-only", action="store_true",
                        help="只显示薄弱知识点")
    args = parser.parse_args()

    pg = ProfileGraph()
    result = pg.invoke({"student_id": args.student, "db_path": args.db})
    profile = result["profile"]

    p = profile  # dict

    if args.json:
        output = {
            "student_id": p["student_id"],
            "overall_accuracy": p["overall_accuracy"],
            "total_attempts": p["total_attempts"],
            "mastery_summary": p["mastery_summary"],
            "weakest_topics": [
                {"section_id": t["section_id"], "topic": t["topic"],
                 "mastery_level": t["mastery_level"],
                 "accuracy": t["accuracy"],
                 "recent_accuracy": t["recent_accuracy"],
                 "dominant_error_type": t.get("dominant_error_type", ""),
                 "streak_wrong": t.get("streak_wrong", 0)}
                for t in p["weakest_topics"]
            ],
            "error_distribution": {
                ERROR_TYPE_LABELS.get(k, k): v
                for k, v in p["error_distribution"].items()
            },
            "risk_signals": p["risk_signals"],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # ── 文本输出 ──
    print(f"\n学生 {p['student_id']} 当前画像\n")
    print(f"总作答 {p['total_attempts']} 次，整体正确率 {p['overall_accuracy']:.0%}")

    summary = p["mastery_summary"]
    print(f"\n掌握概况：")
    print(f"  mastered:  {summary['mastered']} 个知识点")
    print(f"  familiar:  {summary['familiar']} 个知识点")
    print(f"  unstable:  {summary['unstable']} 个知识点")
    print(f"  weak:      {summary['weak']} 个知识点")
    print(f"  unknown:   {summary['unknown']} 个知识点")

    if args.weak_only:
        print(f"\n薄弱知识点：")
        for i, t in enumerate(p["weakest_topics"], 1):
            label = _mastery_label(t["mastery_level"])
            name = f"{t['section_id']} {t['topic']}" if t.get("topic") else t["section_id"]
            print(f"  {i}. {name} — {label}")
            print(f"     正确率 {t['accuracy']:.0%}（近期 {t['recent_accuracy']:.0%}）", end="")
            if t.get("streak_wrong", 0) >= 2:
                print(f"，连续错误 {t['streak_wrong']} 次", end="")
            if t.get("dominant_error_type"):
                err_label = ERROR_TYPE_LABELS.get(t["dominant_error_type"], t["dominant_error_type"])
                print(f"，主要错因: {err_label}", end="")
            print()
        if not p["weakest_topics"]:
            print("  （无）")
    else:
        order = {"weak": 0, "unstable": 1, "familiar": 2, "unknown": 3, "mastered": 4}
        sorted_topics = sorted(p["topics"],
                               key=lambda t: (order.get(t["mastery_level"], 9), -t["accuracy"]))
        print(f"\n知识点详情（按掌握程度）：")
        for t in sorted_topics:
            label = _mastery_label(t["mastery_level"])
            name = f"{t['section_id']} {t['topic']}" if t.get("topic") else t["section_id"]
            print(f"  [{label}] {name} — 正确率 {t['accuracy']:.0%}（{t['total_attempts']}次）", end="")
            if t.get("dominant_error_type"):
                err_label = ERROR_TYPE_LABELS.get(t["dominant_error_type"], t["dominant_error_type"])
                print(f"，错因: {err_label}", end="")
            print()

    # 错因分布
    if p["error_distribution"]:
        print(f"\n错因分布：")
        total = sum(p["error_distribution"].values())
        for etype in ERROR_PRIORITY:
            if etype in p["error_distribution"]:
                cnt = p["error_distribution"][etype]
                label = ERROR_TYPE_LABELS.get(etype, etype)
                print(f"  {label}: {cnt} 次 ({cnt/total:.0%})")
    else:
        print(f"\n错因分布：数据不足（暂无错因标签）")

    # 风险信号
    if p["risk_signals"]:
        print(f"\n风险信号：")
        for sig in p["risk_signals"]:
            print(f"  !! {sig}")

    print()


def _mastery_label(level: str) -> str:
    labels = {
        "mastered": "已掌握",
        "familiar": "熟悉",
        "unstable": "不稳定",
        "weak": "薄弱",
        "unknown": "未知",
    }
    return labels.get(level, level)


if __name__ == "__main__":
    main()
