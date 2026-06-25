"""Session / Snapshot / Memory 存储层。

管理三张新表：
  learning_sessions   — 学习活动边界
  profile_snapshots   — 画像快照
  student_memory_facts — 长期记忆事实

所有表与 attempts 同库（cache/attempts.db）。
"""

import json
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

# ── 表初始化 ──

def init_long_memory_db(db_path: str) -> None:
    """初始化 learning_sessions / profile_snapshots / student_memory_facts 三张表。幂等。"""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS learning_sessions (
            id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            started_at TEXT DEFAULT (datetime('now')),
            ended_at TEXT,
            pre_snapshot_id INTEGER,
            post_snapshot_id INTEGER,
            recommendation_json TEXT DEFAULT '',
            focus_sections_json TEXT DEFAULT '',
            focus_topics_json TEXT DEFAULT '',
            question_types_json TEXT DEFAULT '',
            target_count INTEGER DEFAULT 0,
            attempt_count INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            accuracy REAL DEFAULT 0.0,
            delta_mastery_json TEXT DEFAULT '',
            effect_summary TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS profile_snapshots (
            id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            session_id INTEGER,
            snapshot_type TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            total_attempts INTEGER DEFAULT 0,
            overall_accuracy REAL DEFAULT 0.0,
            profile_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS student_memory_facts (
            id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            value_json TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            evidence_json TEXT DEFAULT '',
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(student_id, memory_type, memory_key)
        );
    """)
    db.commit()
    db.close()


# ── learning_sessions ──

def create_learning_session(
    db_path: str,
    student_id: str,
    mode: str,
    focus_sections: list[str] | None = None,
    focus_topics: list[str] | None = None,
    question_types: list[str] | None = None,
    target_count: int = 0,
    recommendation_json: str = "",
) -> int:
    """创建一条 status='active' 的学习 session。返回 session_id。"""
    db = sqlite3.connect(db_path)
    cursor = db.execute(
        """INSERT INTO learning_sessions
           (student_id, mode, status, started_at,
            focus_sections_json, focus_topics_json,
            question_types_json, target_count,
            recommendation_json)
           VALUES (?, ?, 'active', datetime('now'), ?, ?, ?, ?, ?)""",
        (
            student_id,
            mode,
            json.dumps(focus_sections or [], ensure_ascii=False),
            json.dumps(focus_topics or [], ensure_ascii=False),
            json.dumps(question_types or [], ensure_ascii=False),
            target_count,
            recommendation_json,
        ),
    )
    session_id = cursor.lastrowid
    db.commit()
    db.close()
    logger.info("session created: id=%s, student=%s, mode=%s", session_id, student_id, mode)
    return session_id


def update_session_field(db_path: str, session_id: int, **kwargs) -> None:
    """便捷更新 session 的单个或多个字段。"""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [session_id]
    db = sqlite3.connect(db_path)
    db.execute(
        f"UPDATE learning_sessions SET {sets}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    db.commit()
    db.close()


def finish_learning_session(
    db_path: str,
    session_id: int,
    summary: dict,
) -> None:
    """将 session 标记为 completed，写入结果统计。"""
    db = sqlite3.connect(db_path)
    db.execute(
        """UPDATE learning_sessions
           SET status = 'completed',
               ended_at = datetime('now'),
               attempt_count = ?,
               correct_count = ?,
               accuracy = ?,
               delta_mastery_json = ?,
               effect_summary = ?,
               post_snapshot_id = ?,
               updated_at = datetime('now')
           WHERE id = ?""",
        (
            summary.get("attempt_count", 0),
            summary.get("correct_count", 0),
            summary.get("accuracy", 0.0),
            summary.get("delta_mastery_json", "{}"),
            summary.get("effect_summary", ""),
            summary.get("post_snapshot_id"),
            session_id,
        ),
    )
    db.commit()
    db.close()
    logger.info("session completed: id=%s, accuracy=%.0f%%", session_id, summary.get("accuracy", 0) * 100)


def abort_learning_session(
    db_path: str,
    session_id: int,
    reason: str = "",
) -> None:
    """将未完成的 session 标记为 aborted。不会覆盖 completed session。"""
    db = sqlite3.connect(db_path)
    db.execute(
        """UPDATE learning_sessions
           SET status = 'aborted',
               ended_at = COALESCE(ended_at, datetime('now')),
               effect_summary = ?,
               updated_at = datetime('now')
           WHERE id = ? AND status != 'completed'""",
        (reason, session_id),
    )
    db.commit()
    db.close()
    logger.warning("session aborted: id=%s, reason=%s", session_id, reason)


def get_session(db_path: str, session_id: int) -> dict | None:
    """获取单条 session。"""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM learning_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    db.close()
    return dict(row) if row else None


def get_recent_sessions(
    db_path: str,
    student_id: str,
    limit: int = 5,
) -> list[dict]:
    """获取学生最近 completed 的 session 列表。"""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT id, mode, status, started_at, ended_at,
                  attempt_count, correct_count, accuracy,
                  effect_summary, delta_mastery_json
           FROM learning_sessions
           WHERE student_id = ? AND status = 'completed'
           ORDER BY ended_at DESC
           LIMIT ?""",
        (student_id, limit),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def attach_attempts_to_session(
    db_path: str,
    session_id: int,
    attempt_ids: list[int],
) -> None:
    """将一批 attempt 归属到指定 session。"""
    if not attempt_ids:
        return
    db = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(attempt_ids))
    db.execute(
        f"UPDATE attempts SET session_id = ? WHERE id IN ({placeholders})",
        [session_id] + attempt_ids,
    )
    db.commit()
    db.close()


# ── profile_snapshots ──

def save_profile_snapshot(
    db_path: str,
    student_id: str,
    profile_dict: dict,
    profile_version: str = "bkt-v1",
    snapshot_type: str = "post",
    session_id: int | None = None,
) -> int:
    """保存一次画像快照。返回 snapshot_id。"""
    profile_json = json.dumps(profile_dict, ensure_ascii=False, default=str)
    db = sqlite3.connect(db_path)
    cursor = db.execute(
        """INSERT INTO profile_snapshots
           (student_id, session_id, snapshot_type, profile_version,
            total_attempts, overall_accuracy, profile_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            student_id,
            session_id,
            snapshot_type,
            profile_version,
            profile_dict.get("total_attempts", 0),
            profile_dict.get("overall_accuracy", 0.0),
            profile_json,
        ),
    )
    snapshot_id = cursor.lastrowid
    db.commit()
    db.close()
    logger.info("snapshot saved: id=%s, student=%s, type=%s", snapshot_id, student_id, snapshot_type)
    return snapshot_id


def get_snapshot(db_path: str, snapshot_id: int) -> dict | None:
    """获取单条快照（含解析后的 profile_json）。"""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM profile_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    db.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d.get("profile_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        d["profile"] = {}
    return d


# ── student_memory_facts ──

def get_active_memory_facts(
    db_path: str,
    student_id: str,
) -> list[dict]:
    """获取学生所有 active 状态的长期记忆事实。"""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT id, memory_type, memory_key, value_json, confidence, evidence_json,
                  first_seen, last_seen, status
           FROM student_memory_facts
           WHERE student_id = ? AND status = 'active'
           ORDER BY confidence DESC, last_seen DESC""",
        (student_id,),
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["value_json"] = json.loads(d.get("value_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["value_json"] = {}
        try:
            d["evidence_json"] = json.loads(d.get("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["evidence_json"] = {}
        result.append(d)
    return result
