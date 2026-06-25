"""答题记录存储：attempts 表 + attempt_error_labels 表。"""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def init_attempts_db(db_path: str = "cache/attempts.db"):
    """建表，幂等。包含新列 explanation / reason / method。"""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            section_id TEXT,
            topic TEXT,
            question_type TEXT,
            difficulty TEXT,
            stem TEXT,
            student_answer TEXT,
            correct_answer TEXT,
            explanation TEXT DEFAULT '',
            is_correct INTEGER,
            duration_sec INTEGER,
            confidence INTEGER DEFAULT 3,
            reason TEXT DEFAULT '',
            method TEXT DEFAULT 'rule',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # 存量表补齐新列（幂等）
    for col, col_type in [("explanation", "TEXT DEFAULT ''"),
                          ("reason", "TEXT DEFAULT ''"),
                          ("method", "TEXT DEFAULT 'rule'"),
                          ("session_id", "INTEGER")]:
        try:
            db.execute(f"ALTER TABLE attempts ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    db.commit()
    db.close()


def record_attempt(db_path: str, session_id: int | None = None, **kwargs) -> int:
    """写入一条作答记录。返回 attempt_id。"""
    db = sqlite3.connect(db_path)
    cursor = db.execute(
        """INSERT INTO attempts
           (student_id, section_id, topic, question_type, difficulty,
            stem, student_answer, correct_answer, explanation,
            is_correct, duration_sec, confidence, reason, method, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kwargs.get("student_id", ""),
            kwargs.get("section_id", ""),
            kwargs.get("topic", ""),
            kwargs.get("question_type", ""),
            kwargs.get("difficulty", ""),
            kwargs.get("stem", ""),
            kwargs.get("student_answer", ""),
            kwargs.get("correct_answer", ""),
            kwargs.get("explanation", ""),
            1 if kwargs.get("is_correct") else 0,
            kwargs.get("duration_sec", 0),
            kwargs.get("confidence", 3),
            kwargs.get("reason", ""),
            kwargs.get("method", "rule"),
            session_id,
        ),
    )
    attempt_id = cursor.lastrowid
    db.commit()
    db.close()
    return attempt_id


def record_attempts_batch(
    db_path: str,
    records: list[dict],
    session_id: int | None = None,
) -> list[int]:
    """批量写入 attempts，事务保护。同时写入 error_labels（错时）。

    Args:
        db_path: 数据库路径
        records: 作答记录列表
        session_id: 可选，将这批 attempts 归属到指定 session

    Returns:
        list[int]: 本轮新增的 attempt IDs
    """
    db = sqlite3.connect(db_path)
    attempt_ids: list[int] = []
    try:
        with db:
            for r in records:
                cursor = db.execute(
                    """INSERT INTO attempts
                       (student_id, section_id, topic, question_type, difficulty,
                        stem, student_answer, correct_answer, explanation,
                        is_correct, duration_sec, confidence, reason, method, session_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r.get("student_id", ""),
                        r.get("section_id", ""),
                        r.get("topic", ""),
                        r.get("question_type", ""),
                        r.get("difficulty", ""),
                        r.get("stem", ""),
                        r.get("student_answer", ""),
                        r.get("correct_answer", ""),
                        r.get("explanation", ""),
                        1 if r.get("is_correct") else 0,
                        r.get("duration_sec", 0),
                        r.get("confidence", 3),
                        r.get("reason", ""),
                        r.get("method", "rule"),
                        session_id,
                    ),
                )
                attempt_id = cursor.lastrowid
                attempt_ids.append(attempt_id)

                # 如果有 LLM 错因诊断结果，同连接写入 error_labels
                if r.get("error_type"):
                    try:
                        db.execute(
                            """INSERT INTO attempt_error_labels
                               (attempt_id, error_type, confidence, source,
                                evidence, suggestion)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                attempt_id,
                                r["error_type"],
                                r.get("diagnosis_confidence", 0.85),
                                "llm",
                                (r.get("error_evidence") or "")[:500],
                                (r.get("error_suggestion") or "")[:500],
                            ),
                        )
                    except Exception:
                        logger.exception("error_labels 写入失败 attempt_id=%s", attempt_id)
    finally:
        db.close()
    return attempt_ids


# ── 错因标签表 ──

def init_error_labels_db(db_path: str = "cache/attempts.db"):
    """建 error_labels 表，幂等。"""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS attempt_error_labels (
            id INTEGER PRIMARY KEY,
            attempt_id INTEGER NOT NULL,
            error_type TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            source TEXT DEFAULT 'manual',
            evidence TEXT DEFAULT '',
            suggestion TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    db.close()


def record_error_label(db_path: str, attempt_id: int, error_type: str,
                       confidence: float = 1.0, source: str = "manual",
                       evidence: str = "", suggestion: str = ""):
    """为一次错误作答打上错因标签。"""
    db = sqlite3.connect(db_path)
    db.execute(
        """INSERT INTO attempt_error_labels
           (attempt_id, error_type, confidence, source, evidence, suggestion)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (attempt_id, error_type, confidence, source, evidence, suggestion),
    )
    db.commit()
    db.close()


def get_error_labels_for_attempt(db_path: str, attempt_id: int) -> list[dict]:
    """获取某次作答的所有错因标签。"""
    db = sqlite3.connect(db_path)
    rows = db.execute(
        """SELECT id, attempt_id, error_type, confidence, source, evidence, suggestion
           FROM attempt_error_labels WHERE attempt_id = ?""",
        (attempt_id,),
    ).fetchall()
    db.close()
    return [
        {"id": r[0], "attempt_id": r[1], "error_type": r[2],
         "confidence": r[3], "source": r[4], "evidence": r[5], "suggestion": r[6]}
        for r in rows
    ]
