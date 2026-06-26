"""长期记忆事实引擎：生成、更新、衰减、查询稳定的学生画像结论。

依赖 trend_engine 输出的 trend_summary，沉淀到 student_memory_facts 表。
LLM 只负责表达层（未来接入），事实是否成立由引擎根据证据判定。
"""

import json
import sqlite3
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# 最少需要多少次证据才生成一条弱项记忆
MIN_EVIDENCE_FOR_WEAK = 3
# 超过多少天未更新标记为 stale
STALE_DAYS = 30


def update_memory_facts(
    db_path: str,
    student_id: str,
    trend_summary: dict[str, Any],
    error_distribution: dict[str, int] | None = None,
) -> list[dict]:
    """根据趋势摘要和错因分布更新长期记忆事实。

    生成的记忆类型：
    - weak_topic: 长期薄弱知识点（需多次证据）
    - trend: 稳定趋势（提升/下降）
    - error_pattern: 固定错因模式

    Returns:
        [{memory_type, memory_key, status, ...}] 新创建或更新的记忆。
    """
    db = sqlite3.connect(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    # ① 提升中的 topic → 记录趋势，如果此前有 weak_topic 记忆则降低置信度
    for topic in trend_summary.get("improving_topics", []):
        sid = topic["section_id"]
        _upsert_fact(
            db, student_id, "trend", f"improving:{sid}",
            value_json={"section_id": sid, "avg_delta": topic["avg_delta"], "trend": "improving"},
            confidence=min(0.5 + abs(topic["avg_delta"]) * 8, 0.95),
            evidence_json={"source": "trend_engine", "delta": topic["avg_delta"]},
            now=now_str,
        )
        results.append({"memory_type": "trend", "memory_key": f"improving:{sid}", "status": "active"})

        # 如果存在 weak_topic 记忆且持续改善 → 降权
        _downgrade_fact(db, student_id, "weak_topic", sid, factor=0.7)

    # ② 下降中的 topic → 记录趋势，强化或创建 weak_topic
    for topic in trend_summary.get("declining_topics", []):
        sid = topic["section_id"]
        _upsert_fact(
            db, student_id, "trend", f"declining:{sid}",
            value_json={"section_id": sid, "avg_delta": topic["avg_delta"], "trend": "declining"},
            confidence=min(0.5 + abs(topic["avg_delta"]) * 8, 0.95),
            evidence_json={"source": "trend_engine", "delta": topic["avg_delta"]},
            now=now_str,
        )
        results.append({"memory_type": "trend", "memory_key": f"declining:{sid}", "status": "active"})

        # 创建或强化 weak_topic（需要证据积累）
        evidence_count = topic.get("evidence_count", 1)
        existing = db.execute(
            "SELECT id, confidence FROM student_memory_facts "
            "WHERE student_id = ? AND memory_type = 'weak_topic' AND memory_key = ?",
            (student_id, sid),
        ).fetchone()
        if existing:
            # 强化：下降越多置信度增量越大
            boost = min(abs(topic["avg_delta"]) * 3, 0.2)
            new_conf = min(existing[1] + boost, 0.95)
            _upsert_fact(
                db, student_id, "weak_topic", sid,
                value_json={"section_id": sid, "reason": "declining_trend"},
                confidence=new_conf,
                evidence_json={"source": "trend_engine", "pattern": "declining"},
                now=now_str,
            )
            results.append({"memory_type": "weak_topic", "memory_key": sid, "status": "active"})
        elif evidence_count >= MIN_EVIDENCE_FOR_WEAK:
            _upsert_fact(
                db, student_id, "weak_topic", sid,
                value_json={"section_id": sid, "reason": "declining_trend"},
                confidence=min(0.3 + abs(topic["avg_delta"]) * 5, 0.65),
                evidence_json={
                    "source": "trend_engine",
                    "pattern": "declining",
                    "evidence_count": evidence_count,
                },
                now=now_str,
            )
            results.append({"memory_type": "weak_topic", "memory_key": sid, "status": "active"})

    # ③ 卡住 topic → 如果已有 weak_topic 记忆则强化
    for topic in trend_summary.get("stalled_topics", []):
        sid = topic["section_id"]
        evidence_count = topic.get("evidence_count", 1)
        existing = db.execute(
            "SELECT id, confidence FROM student_memory_facts "
            "WHERE student_id = ? AND memory_type = 'weak_topic' AND memory_key = ?",
            (student_id, sid),
        ).fetchone()
        if existing and existing[1] < 0.8:
            _upsert_fact(
                db, student_id, "weak_topic", sid,
                value_json={"section_id": sid, "reason": "stalled_topic"},
                confidence=min(existing[1] + 0.08, 0.85),
                evidence_json={"source": "trend_engine", "pattern": "stalled"},
                now=now_str,
            )
            results.append({"memory_type": "weak_topic", "memory_key": sid, "status": "active"})
        elif evidence_count >= MIN_EVIDENCE_FOR_WEAK:
            _upsert_fact(
                db, student_id, "weak_topic", sid,
                value_json={"section_id": sid, "reason": "stalled_topic"},
                confidence=0.3,
                evidence_json={
                    "source": "trend_engine",
                    "pattern": "stalled",
                    "evidence_count": evidence_count,
                },
                now=now_str,
            )
            results.append({"memory_type": "weak_topic", "memory_key": sid, "status": "active"})

    # ④ 错因模式：如果有主导错因且累计次数多 → 沉淀为 error_pattern 记忆
    if error_distribution:
        total_errors = sum(error_distribution.values())
        for etype, cnt in error_distribution.items():
            if cnt >= 3 and (cnt / max(total_errors, 1)) > 0.3:
                _upsert_fact(
                    db, student_id, "error_pattern", etype,
                    value_json={"error_type": etype, "count": cnt},
                    confidence=min(0.4 + cnt * 0.1, 0.9),
                    evidence_json={"source": "attempt_error_labels", "count": cnt},
                    now=now_str,
                )
                results.append({"memory_type": "error_pattern", "memory_key": etype, "status": "active"})

    db.close()
    if results:
        logger.info("memory_engine: %d facts updated for student %s", len(results), student_id)
    return results


def get_active_memory_facts(
    db_path: str,
    student_id: str,
) -> list[dict]:
    """获取所有 status='active' 的长期记忆事实。"""
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
        for field in ("value_json", "evidence_json"):
            try:
                d[field] = json.loads(d.get(field) or "{}")
            except (json.JSONDecodeError, TypeError):
                d[field] = {}
        result.append(d)
    return result


def decay_stale_memories(db_path: str, student_id: str) -> int:
    """将超过 STALE_DAYS 未更新的 active 记忆标记为 stale。"""
    db = sqlite3.connect(db_path)
    cursor = db.execute(
        """UPDATE student_memory_facts
           SET status = 'stale', updated_at = datetime('now')
           WHERE student_id = ?
             AND status = 'active'
             AND last_seen < datetime('now', ?)""",
        (student_id, f"-{STALE_DAYS} days"),
    )
    count = cursor.rowcount
    db.commit()
    db.close()
    if count > 0:
        logger.info("memory_engine: decayed %d stale memories for %s", count, student_id)
    return count


def mark_resolved_if_recovered(
    db_path: str,
    student_id: str,
    topic_state: dict,
) -> int:
    """如果某知识点持续改善达到 mastered，将对应 weak_topic 记忆标记为 resolved。"""
    count = 0
    db = sqlite3.connect(db_path)
    for sid, level in topic_state.items():
        if level == "mastered":
            cursor = db.execute(
                """UPDATE student_memory_facts
                   SET status = 'resolved', updated_at = datetime('now')
                   WHERE student_id = ? AND memory_type = 'weak_topic'
                         AND memory_key = ? AND status = 'active'""",
                (student_id, sid),
            )
            if cursor.rowcount > 0:
                count += cursor.rowcount
    db.commit()
    db.close()
    return count


# ── 内部辅助 ──

def _upsert_fact(
    db: sqlite3.Connection,
    student_id: str,
    memory_type: str,
    memory_key: str,
    value_json: dict,
    confidence: float,
    evidence_json: dict,
    now: str,
) -> None:
    """插入或更新一条记忆。UNIQUE(student_id, memory_type, memory_key) 冲突时更新。"""
    existing = db.execute(
        "SELECT id, confidence, evidence_json FROM student_memory_facts "
        "WHERE student_id = ? AND memory_type = ? AND memory_key = ?",
        (student_id, memory_type, memory_key),
    ).fetchone()

    if existing:
        # 合并 evidence
        try:
            old_ev = json.loads(existing[2] or "{}")
        except (json.JSONDecodeError, TypeError):
            old_ev = {}
        if isinstance(old_ev.get("instances"), list) and isinstance(evidence_json.get("instances"), list):
            evidence_json["instances"] = old_ev["instances"] + evidence_json["instances"]

        # 置信度加权平均（70% 旧 + 30% 新）
        new_conf = min(existing[1] * 0.7 + confidence * 0.3, 0.95)

        db.execute(
            """UPDATE student_memory_facts
               SET value_json = ?, confidence = ?, evidence_json = ?,
                   status = 'active', last_seen = ?, updated_at = ?
               WHERE id = ?""",
            (
                json.dumps(value_json, ensure_ascii=False),
                new_conf,
                json.dumps(evidence_json, ensure_ascii=False),
                now, now, existing[0],
            ),
        )
    else:
        db.execute(
            """INSERT INTO student_memory_facts
               (student_id, memory_type, memory_key, value_json, confidence, evidence_json,
                first_seen, last_seen, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                student_id, memory_type, memory_key,
                json.dumps(value_json, ensure_ascii=False),
                confidence,
                json.dumps(evidence_json, ensure_ascii=False),
                now, now, now, now,
            ),
        )
    db.commit()


def _downgrade_fact(
    db: sqlite3.Connection,
    student_id: str,
    memory_type: str,
    memory_key: str,
    factor: float = 0.7,
) -> None:
    """降权一条活跃记忆的置信度。如果低于阈值则标记为 resolved。"""
    db.execute(
        """UPDATE student_memory_facts
           SET confidence = confidence * ?, updated_at = datetime('now')
           WHERE student_id = ? AND memory_type = ? AND memory_key = ?
                 AND status = 'active'""",
        (factor, student_id, memory_type, memory_key),
    )
    # 置信度过低 → resolved
    db.execute(
        """UPDATE student_memory_facts
           SET status = 'resolved', updated_at = datetime('now')
           WHERE student_id = ? AND memory_type = ? AND memory_key = ?
                 AND status = 'active' AND confidence < 0.25""",
        (student_id, memory_type, memory_key),
    )
    db.commit()
