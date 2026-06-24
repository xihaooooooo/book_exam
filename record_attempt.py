"""命令行录入作答记录 + 错因标签。

用法：
    # 录入一次正确作答
    python record_attempt.py --student S001 --section 2.3 --topic 任务调度 \\
        --type choice --difficulty easy --stem "..." --answer C --correct true

    # 录入一次错误作答 + 错因
    python record_attempt.py --student S001 --section 2.3 --topic 任务调度 \\
        --type choice --difficulty easy --stem "..." --answer B --correct false \\
        --error concept_confusion

    # 查看错因类型列表
    python record_attempt.py --list-error-types
"""

import argparse
import os
import sys

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from exam.student_profile.storage import (
    init_attempts_db,
    init_error_labels_db,
    record_attempt,
    record_error_label,
)
from exam.student_profile.schemas import ERROR_TYPES, ERROR_TYPE_LABELS

DB_PATH = os.path.join(_project_root, "cache", "attempts.db")


def main():
    parser = argparse.ArgumentParser(description="命令行作答录入")
    parser.add_argument("--student", default="", help="学生 ID")
    parser.add_argument("--section", default="", help="章节编号，如 2.3")
    parser.add_argument("--topic", default="", help="知识点")
    parser.add_argument("--type", dest="qtype", default="choice",
                        help="题型: choice / fill_blank / short_answer / comprehensive / code_fill")
    parser.add_argument("--difficulty", default="medium",
                        help="难度: easy / medium / hard")
    parser.add_argument("--stem", default="", help="题干")
    parser.add_argument("--answer", default="", help="学生答案")
    parser.add_argument("--correct", default="true",
                        help="是否正确: true / false")
    parser.add_argument("--correct-answer", default="", help="标准答案")
    parser.add_argument("--duration", type=int, default=0, help="作答耗时（秒）")
    parser.add_argument("--confidence", type=int, default=3, help="把握度 1-5")
    parser.add_argument("--error", default="", help="错因类型（仅错误时使用）")
    parser.add_argument("--evidence", default="", help="错因证据")
    parser.add_argument("--suggestion", default="", help="改善建议")
    parser.add_argument("--list-error-types", action="store_true",
                        help="列出所有错因类型")
    parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    args = parser.parse_args()

    if args.list_error_types:
        print("错因类型（6 类）：")
        for etype in ERROR_TYPES:
            label = ERROR_TYPE_LABELS.get(etype, etype)
            print(f"  {etype:<25} {label}")
        return

    if not args.student:
        print("错误：需要 --student 参数")
        sys.exit(1)

    # 初始化
    init_attempts_db(args.db)
    init_error_labels_db(args.db)

    # 判断对错
    is_correct = args.correct.lower() in ("true", "1", "yes")

    # 写入作答
    record_attempt(
        args.db,
        student_id=args.student,
        section_id=args.section,
        topic=args.topic,
        question_type=args.qtype,
        difficulty=args.difficulty,
        stem=args.stem,
        student_answer=args.answer,
        correct_answer=args.correct_answer,
        is_correct=is_correct,
        duration_sec=args.duration,
        confidence=args.confidence,
        reason="手动录入",
        method="manual",
    )

    summary = "正确" if is_correct else "错误"
    print(f"已记录：{args.student} {args.section} {args.topic} — {summary}")

    # 如果答错了且指定了错因，打标签
    if not is_correct and args.error:
        if args.error not in ERROR_TYPES:
            print(f"警告：未知错因类型 '{args.error}'，可用类型见 --list-error-types")

        # 获取刚写入的 attempt_id
        import sqlite3
        db = sqlite3.connect(args.db)
        row = db.execute(
            "SELECT id FROM attempts WHERE student_id=? AND stem=? ORDER BY id DESC LIMIT 1",
            (args.student, args.stem),
        ).fetchone()
        db.close()

        if row:
            record_error_label(
                args.db,
                attempt_id=row[0],
                error_type=args.error,
                source="manual",
                evidence=args.evidence,
                suggestion=args.suggestion,
            )
            print(f"错因标签已记录：{ERROR_TYPE_LABELS.get(args.error, args.error)}")


if __name__ == "__main__":
    main()
