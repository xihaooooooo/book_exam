"""趋势分析：快照对比、趋势摘要、显式 session reward。

依赖 profile_snapshots + learning_sessions 表。
"""

import json
import sqlite3
import logging
from typing import Any

logger = logging.getLogger(__name__)


def compare_snapshots(pre: dict, post: dict) -> dict[str, Any]:
    """对比两个画像快照，返回 delta 信息。

    Returns:
        {
            "delta_mastery": {section_id: delta_PL},
            "delta_accuracy": {section_id: delta_accuracy},
            "new_mastered": [section_id, ...],
            "new_weak": [section_id, ...],
            "overall_delta": float,
        }
    """
    pre_topics = {t["section_id"]: t for t in pre.get("topics", [])}
    post_topics = {t["section_id"]: t for t in post.get("topics", [])}

    pre_mastered = {sid for sid, t in pre_topics.items() if t.get("mastery_level") == "mastered"}
    post_mastered = {sid for sid, t in post_topics.items() if t.get("mastery_level") == "mastered"}
    pre_weak = {sid for sid, t in pre_topics.items() if t.get("mastery_level") in ("weak", "unstable")}
    post_weak = {sid for sid, t in post_topics.items() if t.get("mastery_level") in ("weak", "unstable")}

    delta_mastery = {}
    delta_accuracy = {}
    all_sids = set(pre_topics.keys()) | set(post_topics.keys())
    for sid in all_sids:
        pre_t = pre_topics.get(sid, {})
        post_t = post_topics.get(sid, {})
        pre_bkt = pre_t.get("bkt_state") or {}
        post_bkt = post_t.get("bkt_state") or {}
        pre_pL = pre_bkt.get("p_mastery", 0.0) if isinstance(pre_bkt, dict) else 0.0
        post_pL = post_bkt.get("p_mastery", 0.0) if isinstance(post_bkt, dict) else 0.0
        if abs(post_pL - pre_pL) > 0.001:
            delta_mastery[sid] = round(post_pL - pre_pL, 4)
        pre_acc = pre_t.get("accuracy") or 0
        post_acc = post_t.get("accuracy") or 0
        if pre_acc != post_acc:
            delta_accuracy[sid] = round(post_acc - pre_acc, 4)

    return {
        "delta_mastery": delta_mastery,
        "delta_accuracy": delta_accuracy,
        "new_mastered": sorted(post_mastered - pre_mastered),
        "new_weak": sorted(post_weak - pre_weak),
        "overall_delta": round(
            (post.get("overall_accuracy", 0) or 0) - (pre.get("overall_accuracy", 0) or 0), 4
        ),
    }


def build_trend_summary(
    db_path: str,
    student_id: str,
    window: int = 5,
) -> dict[str, Any]:
    """从最近的 completed session 提取趋势信号。

    Returns:
        {
            "improving_topics": [{"section_id", "avg_delta", "recent_avg", "trend"}],
            "declining_topics": [...],
            "stalled_topics": [...],
            "overall_trend": "improving" | "declining" | "stable" | "insufficient_data",
            "session_count": int,
        }
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    sessions = db.execute(
        """SELECT id, mode, delta_mastery_json, accuracy
           FROM learning_sessions
           WHERE student_id = ? AND status = 'completed'
           ORDER BY ended_at DESC
           LIMIT ?""",
        (student_id, window),
    ).fetchall()
    db.close()

    if not sessions:
        return {"overall_trend": "insufficient_data", "session_count": 0}

    # 累计每个 topic 的 delta 轨迹（session 按 ended_at DESC，需要反转使旧→新排列）
    mastery_trajectory: dict[str, list[float]] = {}
    for s in reversed(sessions):
        try:
            deltas = json.loads(s["delta_mastery_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            deltas = {}
        for sid, delta in deltas.items():
            mastery_trajectory.setdefault(sid, []).append(delta)

    improving = []
    declining = []
    stalled = []

    for sid, deltas in mastery_trajectory.items():
        if len(deltas) < 2:
            continue
        avg_delta = sum(deltas) / len(deltas)
        recent = deltas[-3:] if len(deltas) >= 3 else deltas
        recent_avg = sum(recent) / len(recent)

        entry = {
            "section_id": sid,
            "avg_delta": round(avg_delta, 4),
            "recent_avg": round(recent_avg, 4),
            "evidence_count": len(deltas),
        }
        if recent_avg > 0.03 and avg_delta > 0:
            improving.append({**entry, "trend": "improving"})
        elif recent_avg < -0.03 and avg_delta < 0:
            declining.append({**entry, "trend": "declining"})
        elif abs(avg_delta) < 0.02 and abs(recent_avg) < 0.02:
            stalled.append({**entry, "trend": "stalled"})

    # Overall trend
    if len(improving) > len(declining) and len(improving) >= 2:
        overall = "improving"
    elif len(declining) > len(improving) and len(declining) >= 2:
        overall = "declining"
    else:
        overall = "stable"

    return {
        "improving_topics": sorted(improving, key=lambda x: -x["avg_delta"]),
        "declining_topics": sorted(declining, key=lambda x: x["avg_delta"]),
        "stalled_topics": sorted(stalled, key=lambda x: -abs(x["avg_delta"])),
        "overall_trend": overall,
        "session_count": len(sessions),
    }


def compute_explicit_session_rewards(
    db_path: str,
    student_id: str,
    window: int = 10,
) -> dict[str, float]:
    """从显式 session 的 delta_mastery_json 计算 Bandit reward。

    只使用 mode='practice' 且 completed 的 session，通过累加
    各 topic 在每轮练习后的 P(L) 正向变化来得到 reward。

    Returns:
        {section_id: cumulative_positive_delta_PL}
        与 profile_engine.compute_session_rewards() 相同 shape。
    """
    db = sqlite3.connect(db_path)
    rows = db.execute(
        """SELECT delta_mastery_json
           FROM learning_sessions
           WHERE student_id = ? AND mode = 'practice'
                 AND status = 'completed'
                 AND delta_mastery_json != ''
           ORDER BY ended_at DESC
           LIMIT ?""",
        (student_id, window),
    ).fetchall()
    db.close()

    cumulative: dict[str, float] = {}
    for (json_str,) in rows:
        try:
            deltas = json.loads(json_str or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for sid, delta in deltas.items():
            if delta > 0:
                cumulative[sid] = cumulative.get(sid, 0.0) + delta
    # 返回 None 表示没有任何显式 session 数据，调用方据此决定是否回退
    if not rows:
        return None
    return cumulative


# ── 便捷检测函数 ──

def detect_improving_topics(trends: dict) -> list[dict]:
    return trends.get("improving_topics", [])


def detect_declining_topics(trends: dict) -> list[dict]:
    return trends.get("declining_topics", [])


def detect_stalled_topics(trends: dict) -> list[dict]:
    return trends.get("stalled_topics", [])
