"""生成试卷：从 SQLite 读取教材数据，运行出题流水线。

用法：
    python generate.py                                    # 从 SQLite 读数据出题
    python generate.py --db cache/my.db                   # 指定数据库路径
    python generate.py --from-analysis analysis/report.json  # 基于往年试卷分析出题

前置步骤：
    python parse.py book.pdf --mineru-token TOKEN         # 第一步：解析 PDF
    python analyze_exam.py --dir ./past_papers/           # 第二步（可选）：分析往年试卷
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
from exam.agents.utils.agent_utils import init_mistakes_db, get_weak_sections
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


def derive_params(mode, student_id, db_path, user_focus, user_count, user_types):
    """根据 mode 推导 focus、count、types。用户显式传入优先。"""
    if mode == "exam":
        return user_focus or "", user_count or 0, user_types or ""

    if mode == "diagnostic":
        toc = _build_toc_from_db(db_path)
        chapter_count = len(toc)
        count = user_count if user_count > 0 else min(chapter_count * 2, 30)
        count = max(count, 6)
        return "", count, "choice"

    if mode == "practice":
        weak = get_weak_sections(student_id)
        if not weak:
            print("警告：该学生错题库为空，降级为 exam 模式")
            return user_focus or "", user_count or 0, user_types or ""

        focus = user_focus if user_focus else ",".join(w["section_id"] for w in weak)
        count = user_count if user_count > 0 else min(len(weak) * 2, 30)
        return focus, count, user_types or ""

    return user_focus or "", user_count or 0, user_types or ""


def validate_args(args, mode):
    """参数冲突检测。"""
    if mode == "practice" and not args.student:
        print("错误：practice 模式需要 --student 参数")
        sys.exit(1)
    if mode == "diagnostic":
        if args.count and args.count > 30:
            print("警告：diagnostic 模式题数过大，已缩减为 30")
        if args.types and args.types != "choice":
            print("警告：diagnostic 模式强制 choice 题型，忽略 --types")
        if args.from_analysis:
            print("警告：diagnostic 模式忽略 --from-analysis")
    if mode == "practice" and args.from_analysis:
        print("警告：practice 模式忽略 --from-analysis")


def main():
    parser = argparse.ArgumentParser(description="Book-to-Exam 试卷生成器")
    parser.add_argument("--db", default=None,
                        help="SQLite 数据库路径（默认 cache/sections.db）")
    parser.add_argument("--mode", default="exam",
                        choices=["exam", "practice", "diagnostic"],
                        help="出题模式（默认 exam）")
    parser.add_argument("--student", default=None,
                        help="学生 ID（practice 模式必填）")
    parser.add_argument("--focus", default=None,
                        help="考试重点（关键词或自然语言，逗号分隔多个考点）")
    parser.add_argument("--count", type=int, default=0,
                        help="总题数（默认 6-12 自动适配）")
    parser.add_argument("--types", default=None,
                        help="题型限制 choice/fill_blank/short_answer（逗号分隔）")
    parser.add_argument("--from-analysis", default=None,
                        help="往年试卷分析报告 JSON 路径（如 analysis/report_xxx.json）")
    args = parser.parse_args()

    mode = args.mode
    validate_args(args, mode)

    config = DEFAULT_CONFIG.copy()
    db_path = args.db or config.get("db_path", "cache/sections.db")

    print("Book-to-Exam 试卷生成")
    print("=" * 60)
    if mode != "exam":
        print(f"出题模式: {mode}")

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

    # 初始化错题库（幂等，所有模式都建表）
    mistakes_db = os.path.join(os.path.dirname(db_path), "mistakes.db")
    init_mistakes_db(mistakes_db)

    print(f"从数据库读取：{total} 节（{done} 节有正文）")
    toc = _build_toc_from_db(db_path)
    print(f"  解析出 {len(toc)} 章")

    if args.from_analysis and not os.path.exists(args.from_analysis):
        print(f"错误：分析报告 {args.from_analysis} 不存在")
        sys.exit(1)

    # 推导参数
    focus, count, types = derive_params(
        mode, args.student, db_path,
        args.focus, args.count, args.types
    )
    # diagnostic/practice 忽略 analysis
    analysis_path = args.from_analysis if mode == "exam" else ""

    exam = ExamGraph(config=config, debug=True)
    final_state, questions = exam.propagate(
        db_path=db_path, toc=toc,
        focus=focus,
        target_count=count,
        allowed_types=types,
        analysis_report_path=analysis_path,
        mode=mode,
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
