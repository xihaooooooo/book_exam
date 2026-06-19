"""错题录入工具。

用法：
    python record_mistake.py --student S001 --section 2.1 --stem "..." --wrong "..." --correct "..."
    python record_mistake.py --batch answers.json  # 批量导入
"""

import argparse
import json
import os
import sqlite3
import sys


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _ensure_db(db_path: str):
    """建表（幂等）。"""
    db = _connect(db_path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY,
            student_id TEXT,
            exam_title TEXT,
            stem TEXT,
            wrong_answer TEXT,
            correct_answer TEXT,
            section_id TEXT,
            topic TEXT,
            error_pattern TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(student_id, exam_title, stem)
        );
    """)
    db.commit()
    db.close()


def add_mistake(db_path: str, student_id: str, exam_title: str = "",
                stem: str = "", wrong_answer: str = "", correct_answer: str = "",
                section_id: str = "", topic: str = "", error_pattern: str = ""):
    db = _connect(db_path)
    try:
        db.execute(
            """INSERT OR IGNORE INTO mistakes
               (student_id, exam_title, stem, wrong_answer, correct_answer,
                section_id, topic, error_pattern)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, exam_title, stem, wrong_answer, correct_answer,
             section_id, topic, error_pattern)
        )
        db.commit()
        print(f"已录入错题: [{section_id}] {stem[:60]}...")
    except Exception as e:
        print(f"录入失败: {e}")
    finally:
        db.close()


def batch_import(db_path: str, json_path: str):
    """从 JSON 文件批量导入。格式：[{"student_id": "S001", "stem": "...", ...}, ...]"""
    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    db = _connect(db_path)
    count = 0
    for r in records:
        try:
            db.execute(
                """INSERT OR IGNORE INTO mistakes
                   (student_id, exam_title, stem, wrong_answer, correct_answer,
                    section_id, topic, error_pattern)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get("student_id", ""), r.get("exam_title", ""),
                 r.get("stem", ""), r.get("wrong_answer", ""),
                 r.get("correct_answer", ""), r.get("section_id", ""),
                 r.get("topic", ""), r.get("error_pattern", ""))
            )
            count += 1
        except Exception as e:
            print(f"跳过: {e}")
    db.commit()
    db.close()
    print(f"批量导入完成: {count}/{len(records)} 条")


def main():
    parser = argparse.ArgumentParser(description="错题录入工具")
    parser.add_argument("--db", default="cache/mistakes.db",
                        help="错题库路径（默认 cache/mistakes.db）")
    parser.add_argument("--batch", default=None,
                        help="批量导入 JSON 文件路径")
    parser.add_argument("--student", default=None, help="学生 ID")
    parser.add_argument("--exam", default="", help="考试名称")
    parser.add_argument("--section", default="", help="章节编号")
    parser.add_argument("--topic", default="", help="知识点")
    parser.add_argument("--stem", default="", help="题干")
    parser.add_argument("--wrong", default="", help="错误答案")
    parser.add_argument("--correct", default="", help="正确答案")
    parser.add_argument("--pattern", default="", help="错误类型（概念混淆/计算错误/...）")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    _ensure_db(args.db)

    if args.batch:
        if not os.path.exists(args.batch):
            print(f"错误：文件 {args.batch} 不存在")
            sys.exit(1)
        batch_import(args.db, args.batch)
        return

    if not args.student:
        print("错误：--student 为必填")
        sys.exit(1)

    add_mistake(
        db_path=args.db, student_id=args.student,
        exam_title=args.exam, stem=args.stem,
        wrong_answer=args.wrong, correct_answer=args.correct,
        section_id=args.section, topic=args.topic,
        error_pattern=args.pattern
    )


if __name__ == "__main__":
    main()
