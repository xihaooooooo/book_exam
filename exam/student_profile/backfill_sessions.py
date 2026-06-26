"""历史数据回填：将存量 attempts 按 30 分钟间隔切分为合成 session。

内部维护工具，不属于学生学习闭环。仅在数据库迁移/修复历史数据时使用。

用法：
    python -m exam.student_profile.backfill_sessions
    python -m exam.student_profile.backfill_sessions --student S001
    python -m exam.student_profile.backfill_sessions --db cache/attempts.db --dry-run
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

_project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

SESSION_GAP_MINUTES = 30  # 与 profile_engine.py 保持一致


def _parse_ts(ts: str | None) -> datetime | None:
    """安全解析时间戳。"""
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def backfill(
    db_path: str,
    student_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """将 session_id IS NULL 的 attempts 按时间间隙分组并创建 historical session。

    Returns:
        {"students": N, "sessions": N, "attempts": N}
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    if student_id:
        rows = db.execute(
            "SELECT * FROM attempts WHERE session_id IS NULL AND student_id = ? "
            "ORDER BY student_id, created_at",
            (student_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM attempts WHERE session_id IS NULL "
            "ORDER BY student_id, created_at"
        ).fetchall()

    if not rows:
        db.close()
        print("没有需要回填的 attempts（session_id IS NULL）。")
        return {"students": 0, "sessions": 0, "attempts": 0}

    # 按 student_id 分组
    student_groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        sid = r["student_id"]
        student_groups.setdefault(sid, []).append(r)

    total_sessions = 0
    total_attempts = 0

    for sid, attempts in student_groups.items():
        # 按 30 分钟间隔切分为多个 session
        sessions: list[list[sqlite3.Row]] = []
        current: list[sqlite3.Row] = [attempts[0]]
        for i in range(1, len(attempts)):
            prev_ts = _parse_ts(attempts[i - 1]["created_at"])
            curr_ts = _parse_ts(attempts[i]["created_at"])
            if prev_ts and curr_ts:
                gap = (curr_ts - prev_ts).total_seconds() / 60.0
            else:
                gap = 0
            if gap > SESSION_GAP_MINUTES:
                sessions.append(current)
                current = [attempts[i]]
            else:
                current.append(attempts[i])
        sessions.append(current)

        for group in sessions:
            if not group:
                continue
            correct = sum(1 for a in group if a["is_correct"])
            total_att = len(group)
            acc = correct / total_att if total_att > 0 else 0.0

            start_ts = group[0]["created_at"]
            end_ts = group[-1]["created_at"]

            if dry_run:
                total_sessions += 1
                total_attempts += total_att
                print(
                    f"[DRY RUN] student={sid}, attempts={total_att}, "
                    f"accuracy={acc:.0%}, start={start_ts}, end={end_ts}"
                )
                continue

            # 创建 historical session
            cursor = db.execute(
                """INSERT INTO learning_sessions
                   (student_id, mode, status, started_at, ended_at,
                    attempt_count, correct_count, accuracy)
                   VALUES (?, 'historical', 'completed', ?, ?, ?, ?, ?)""",
                (sid, start_ts, end_ts, total_att, correct, acc),
            )
            session_id = cursor.lastrowid

            # 回填 attempts.session_id
            ids = [a["id"] for a in group]
            placeholders = ",".join("?" * len(ids))
            db.execute(
                f"UPDATE attempts SET session_id = ? WHERE id IN ({placeholders})",
                [session_id] + ids,
            )

            total_sessions += 1
            total_attempts += total_att

    db.commit()
    db.close()
    return {
        "students": len(student_groups),
        "sessions": total_sessions,
        "attempts": total_attempts,
    }


# 内部维护工具，不属于学生学习闭环。
def main():
    parser = argparse.ArgumentParser(
        description="回填历史 attempts 到 historical session"
    )
    parser.add_argument("--db", default="cache/attempts.db")
    parser.add_argument("--student", default=None, help="仅处理指定学生")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览即将创建的回填 session，不实际写入",
    )
    args = parser.parse_args()

    db_path = args.db if os.path.isabs(args.db) else os.path.join(_project_root, args.db)
    if not os.path.exists(db_path):
        print(f"Error: 数据库 {db_path} 不存在")
        sys.exit(1)

    stats = backfill(db_path, student_id=args.student, dry_run=args.dry_run)
    action = "将创建" if args.dry_run else "已创建"
    print(
        f"完成：{action} {stats['sessions']} 个 historical session，"
        f"覆盖 {stats['students']} 个学生，{stats['attempts']} 条 attempts"
    )
    return stats


if __name__ == "__main__":
    main()
