"""历史 attempts topic 字段回填。

离线分析工具，不参与学生学习闭环。为 topic 为空的旧答题记录补齐 topic，
优先用确定性来源（已有同题记录 > 历史出题文件 > 教材章节标题），不做 LLM 猜测。

用法：
    python -m exam.student_profile.backfill_topics --db cache/attempts.db --sections cache/sections.db --dry-run
    python -m exam.student_profile.backfill_topics --db cache/attempts.db --sections cache/sections.db --apply
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

_project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from exam.student_profile.profile_engine import normalize_section_id


def _load_question_files(project_root: str) -> dict[tuple[str, str, str], str]:
    """Scan output/questions_*.json and build (section_id, stem, answer) -> topic lookup.

    Field mapping: JSON has 'source' (section_id) and 'topic' (may be empty).
    """
    lookup: dict[tuple[str, str, str], str] = {}
    pattern = os.path.join(project_root, "output", "questions_*.json")
    files = sorted(glob.glob(pattern))
    print(f"扫描历史出题文件: {len(files)} 个")
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                questions = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for q in questions:
            if not isinstance(q, dict):
                continue
            sid = (q.get("source") or q.get("section_id") or "").strip()
            stem = (q.get("stem") or "").strip()
            ans = (q.get("correct_answer") or "").strip()
            topic = (q.get("topic") or "").strip()
            if not sid or not stem or not ans or not topic:
                continue
            key = (normalize_section_id(sid), stem, ans)
            if key not in lookup:
                lookup[key] = topic
    print(f"  有效 (section_id, stem, answer) -> topic 映射: {len(lookup)}")
    return lookup


def _load_section_titles(sections_db: str) -> dict[str, str]:
    """Load {section_id: title} from sections.db."""
    if not sections_db or not os.path.exists(sections_db):
        print("sections.db 不存在，跳过章节标题回填")
        return {}
    db = sqlite3.connect(sections_db)
    rows = db.execute("SELECT id, title FROM sections WHERE title != ''").fetchall()
    db.close()
    titles: dict[str, str] = {}
    for section_id, title in rows:
        if section_id and title:
            sid = normalize_section_id(section_id)
            if sid not in titles:
                titles[sid] = title.strip()
    print(f"章节标题映射: {len(titles)} 个 (按 normalize 后去重)")
    return titles


def backfill(
    db_path: str,
    sections_db: str = "",
    dry_run: bool = False,
    sample_limit: int = 20,
) -> dict:
    """Run topic backfill.

    Returns:
        {"total": N, "empty": N, "by_peer": N, "by_history": N,
         "by_section_title": N, "still_empty": N, "samples": [...]}
    """
    if not os.path.exists(db_path):
        print(f"Error: 数据库 {db_path} 不存在")
        sys.exit(1)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))

    # Pre-load lookup tables
    peer_lookup = _build_peer_lookup(db_path)
    history_lookup = _load_question_files(project_root)
    section_titles = _load_section_titles(sections_db)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # Query empty-topic attempts
    empty_rows = db.execute(
        """SELECT id, student_id, section_id, stem, correct_answer, topic
           FROM attempts
           WHERE topic IS NULL OR TRIM(topic) = ''
           ORDER BY id"""
    ).fetchall()

    total_all = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]

    stats = {
        "total": total_all,
        "empty": len(empty_rows),
        "by_peer": 0,
        "by_history": 0,
        "by_section_title": 0,
        "still_empty": 0,
        "samples": [],
    }

    if not empty_rows:
        db.close()
        print(f"总 {total_all} 条 attempts，无需回填。")
        return stats

    updates: list[tuple[str, int]] = []  # (new_topic, attempt_id)

    for row in empty_rows:
        attempt_id = row["id"]
        section_id = normalize_section_id(row["section_id"] or "")
        stem = (row["stem"] or "").strip()
        correct_answer = (row["correct_answer"] or "").strip()
        found_from = None
        new_topic = ""

        # Priority 1: peer match (same DB, non-empty topic)
        if section_id and stem and correct_answer:
            key = (section_id, stem, correct_answer)
            if key in peer_lookup:
                new_topic = peer_lookup[key]
                found_from = "peer"
                stats["by_peer"] += 1

        # Priority 2: history question files
        if not new_topic and section_id and stem and correct_answer:
            key = (section_id, stem, correct_answer)
            if key in history_lookup:
                new_topic = history_lookup[key]
                found_from = "history"
                stats["by_history"] += 1

        # Priority 3: section title from textbook
        if not new_topic and section_id and section_id in section_titles:
            new_topic = section_titles[section_id]
            found_from = "section_title"
            stats["by_section_title"] += 1

        if new_topic:
            updates.append((new_topic, attempt_id))
            if len(stats["samples"]) < sample_limit:
                old_display = (row["topic"] or "").strip() or "(空)"
                stats["samples"].append({
                    "attempt_id": attempt_id,
                    "section_id": section_id,
                    "old_topic": old_display,
                    "new_topic": new_topic,
                    "source": found_from,
                })
        else:
            stats["still_empty"] += 1

    # Execute updates
    if updates and not dry_run:
        with db:
            db.executemany(
                "UPDATE attempts SET topic = ? WHERE id = ?",
                updates,
            )
        print(f"\n已写入 {len(updates)} 条更新。")

    db.close()

    # Print report
    _print_report(stats, dry_run)

    return stats


def _build_peer_lookup(db_path: str) -> dict[tuple[str, str, str], str]:
    """Build (section_id, stem, answer) -> topic from attempts that already have topic."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT section_id, stem, correct_answer, topic
           FROM attempts
           WHERE topic IS NOT NULL AND TRIM(topic) != ''
           GROUP BY section_id, stem, correct_answer"""
    ).fetchall()
    db.close()
    lookup: dict[tuple[str, str, str], str] = {}
    for row in rows:
        sid = normalize_section_id(row["section_id"] or "")
        stem = (row["stem"] or "").strip()
        ans = (row["correct_answer"] or "").strip()
        topic = (row["topic"] or "").strip()
        if sid and stem and ans and topic:
            key = (sid, stem, ans)
            if key not in lookup:
                lookup[key] = topic
    print(f"同题匹配(peer): {len(lookup)} 条非空 topic 记录")
    return lookup


def _print_report(stats: dict, dry_run: bool):
    """Print human-readable backfill report."""
    action = "[DRY RUN] 将回填" if dry_run else "已回填"
    total_filled = stats["by_peer"] + stats["by_history"] + stats["by_section_title"]
    print()
    print("=" * 60)
    print(f"  Topic 回填报告  ({action})")
    print("=" * 60)
    print(f"  总 attempts 数:          {stats['total']:>6d}")
    print(f"  topic 为空数量:          {stats['empty']:>6d}")
    print(f"    └ 优先级1(同题匹配):   {stats['by_peer']:>6d}")
    print(f"    └ 优先级2(历史题目):   {stats['by_history']:>6d}")
    print(f"    └ 优先级3(章节标题):   {stats['by_section_title']:>6d}")
    print(f"    └ 仍无法回填:          {stats['still_empty']:>6d}")
    print(f"  回填覆盖率:              {total_filled / max(stats['empty'], 1) * 100:.0f}%")
    print()

    if stats["samples"]:
        print(f"  前 {len(stats['samples'])} 条回填样例:")
        print(f"  {'ID':<6s} {'章节':<8s} {'来源':<14s} {'旧topic':<15s} 新topic")
        print(f"  {'-'*6} {'-'*8} {'-'*14} {'-'*15} {'-'*30}")
        for s in stats["samples"]:
            print(
                f"  {s['attempt_id']:<6d} "
                f"{s['section_id']:<8s} "
                f"{s['source']:<14s} "
                f"{s['old_topic']:<15s} "
                f"{s['new_topic']}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="回填历史 attempts 的 topic 字段"
    )
    parser.add_argument("--db", default="cache/attempts.db",
                        help="attempts 数据库路径")
    parser.add_argument("--sections", default="cache/sections.db",
                        help="教材章节数据库路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览回填计划，不实际写入")
    parser.add_argument("--apply", action="store_true",
                        help="确认并执行回填")
    args = parser.parse_args()

    db_path = args.db if os.path.isabs(args.db) else os.path.join(_project_root, args.db)
    sections_path = args.sections if os.path.isabs(args.sections) else os.path.join(_project_root, args.sections)

    if not args.dry_run and not args.apply:
        print("请指定 --dry-run（预览）或 --apply（执行）。")
        sys.exit(1)

    backfill(
        db_path,
        sections_db=sections_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
