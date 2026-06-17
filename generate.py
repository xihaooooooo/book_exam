"""生成试卷：从 SQLite 读取教材数据，运行出题流水线。

用法：
    python generate.py                    # 从 SQLite 读数据出题
    python generate.py --db cache/my.db   # 指定数据库路径

前置步骤：
    python parse.py book.pdf --mineru-token TOKEN  # 先解析 PDF
"""

import argparse
import os
import sqlite3
import sys

# Windows 终端中文编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from exam.graph.exam_graph import ExamGraph
from exam.config import DEFAULT_CONFIG


def _build_toc_from_db(db_path: str) -> list[dict]:
    """从 SQLite sections 表重建 TOC 结构。"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, chapter, title FROM sections ORDER BY page_start, id"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    chapters = {}
    for section_id, chapter, title in rows:
        ch = chapter or "正文"
        if ch not in chapters:
            chapters[ch] = []
        chapters[ch].append({"id": section_id, "title": title or section_id})

    return [
        {"chapter": ch, "sections": secs}
        for ch, secs in chapters.items()
    ]


def main():
    parser = argparse.ArgumentParser(description="Book-to-Exam 试卷生成器")
    parser.add_argument("--db", default=None,
                        help="SQLite 数据库路径（默认 cache/sections.db）")
    parser.add_argument("--focus", default=None,
                        help="考试重点（关键词或自然语言，逗号分隔多个考点）")
    parser.add_argument("--count", type=int, default=0,
                        help="总题数（默认 6-12 自动适配）")
    parser.add_argument("--types", default=None,
                        help="题型限制 choice/fill_blank/short_answer（逗号分隔）")
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    db_path = args.db or config.get("db_path", "cache/sections.db")

    print("Book-to-Exam 试卷生成")
    print("=" * 60)

    if not os.path.exists(db_path):
        print(f"错误：数据库 {db_path} 不存在。")
        print(f"请先运行 python parse.py book.pdf --mineru-token TOKEN")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE ocr_status='done'"
    ).fetchone()[0]
    conn.close()

    if total == 0:
        print("错误：数据库为空，请先运行 python parse.py book.pdf")
        sys.exit(1)

    print(f"从数据库读取：{total} 节（{done} 节有正文）")
    toc = _build_toc_from_db(db_path)
    print(f"  解析出 {len(toc)} 章")

    exam = ExamGraph(config=config, debug=True)
    final_state, questions = exam.propagate(
        db_path=db_path, toc=toc,
        focus=args.focus or "",
        target_count=args.count,
        allowed_types=args.types or "",
    )

    print("\n" + "=" * 60)
    print(f"生成完成！共 {len(questions)} 道题")
    print("=" * 60)

    for i, q in enumerate(questions, 1):
        print(f"\n--- 题{i} ({q.get('question_type', '')}, {q.get('difficulty', '')}) ---")
        print(f"题干: {q.get('stem', '')}")
        if q.get("options"):
            for opt in q["options"]:
                print(f"  {opt}")
        print(f"答案: {q.get('correct_answer', '')}")
        if q.get("explanation"):
            print(f"解析: {q.get('explanation', '')}")


if __name__ == "__main__":
    main()
