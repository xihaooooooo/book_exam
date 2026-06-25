"""Session lifecycle orchestration for the Web/API layer.

The storage module owns raw DB writes. This service owns the cross-table
workflow around a learning activity: session creation, snapshots, deltas,
memory updates, and abort handling.
"""

import json
import logging
import sqlite3
from dataclasses import asdict
from typing import Any

from exam.student_profile.memory_engine import update_memory_facts
from exam.student_profile.profile_engine import build_profile
from exam.student_profile.schemas import ERROR_TYPE_LABELS
from exam.student_profile.session_storage import (
    abort_learning_session,
    create_learning_session,
    finish_learning_session,
    get_session,
    save_profile_snapshot,
    update_session_field,
)
from exam.student_profile.trend_engine import build_trend_summary

logger = logging.getLogger(__name__)


def _profile_to_snapshot_dict(profile) -> dict[str, Any]:
    """Convert StudentProfile dataclasses into JSON-friendly snapshot data."""
    profile_dict = asdict(profile)
    profile_dict["weakest_topics"] = [asdict(t) for t in profile.weakest_topics]
    profile_dict["mastery_summary"] = profile.mastery_summary
    return profile_dict


def _build_effect_summary(delta_mastery: dict[str, float]) -> str:
    """Build one short human-readable summary from per-topic mastery deltas."""
    improved = {k: v for k, v in delta_mastery.items() if v > 0.01}
    declined = {k: v for k, v in delta_mastery.items() if v < -0.01}
    parts = []
    if improved:
        top = sorted(improved.items(), key=lambda x: -x[1])[:3]
        detail = " ".join(f"{sid}+{d:.0%}" for sid, d in top)
        parts.append(f"提升 {len(improved)} 个知识点 {detail}")
    if declined:
        top = sorted(declined.items(), key=lambda x: x[1])[:2]
        detail = " ".join(f"{sid}{d:.0%}" for sid, d in top)
        parts.append(f"下降 {len(declined)} 个知识点 {detail}")
    return "；".join(parts) if parts else "无明显变化"


def _compute_delta_mastery(
    db_path: str,
    pre_snapshot_id: int | None,
    post_profile_dict: dict[str, Any],
) -> dict[str, float]:
    """Compare pre snapshot and post profile by BKT P(L)."""
    if not pre_snapshot_id:
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pre_row = conn.execute(
        "SELECT profile_json FROM profile_snapshots WHERE id = ?",
        (pre_snapshot_id,),
    ).fetchone()
    conn.close()
    if not pre_row:
        return {}

    pre_data = json.loads(pre_row["profile_json"])
    pre_map = {t["section_id"]: t for t in pre_data.get("topics", [])}
    delta_mastery = {}
    for topic in post_profile_dict.get("topics", []):
        sid = topic["section_id"]
        pre_bkt = pre_map.get(sid, {}).get("bkt_state") or {}
        post_bkt = topic.get("bkt_state") or {}
        pre_pL = pre_bkt.get("p_mastery", 0.0) if isinstance(pre_bkt, dict) else 0.0
        post_pL = post_bkt.get("p_mastery", 0.0) if isinstance(post_bkt, dict) else 0.0
        if abs(post_pL - pre_pL) > 0.001:
            delta_mastery[sid] = round(post_pL - pre_pL, 4)
    return delta_mastery


def start_learning_session(
    db_path: str,
    student_id: str,
    mode: str,
    target_count: int = 0,
) -> dict[str, int | None]:
    """Create a session and, for practice, save a pre-session snapshot."""
    if not student_id:
        return {"session_id": None, "pre_snapshot_id": None}

    session_id = create_learning_session(
        db_path,
        student_id=student_id,
        mode=mode,
        target_count=target_count,
    )
    pre_snapshot_id = None

    if mode == "practice":
        try:
            pre_profile = build_profile(student_id, db_path, mastery_backend="bkt")
            pre_snapshot_id = save_profile_snapshot(
                db_path,
                student_id=student_id,
                profile_dict=_profile_to_snapshot_dict(pre_profile),
                profile_version="bkt-v1",
                snapshot_type="pre",
                session_id=session_id,
            )
            update_session_field(db_path, session_id, pre_snapshot_id=pre_snapshot_id)
            logger.info(
                "pre_snapshot saved: id=%s for session=%s",
                pre_snapshot_id,
                session_id,
            )
        except Exception:
            logger.exception("保存 pre_snapshot 失败，继续出题")

    return {"session_id": session_id, "pre_snapshot_id": pre_snapshot_id}


def update_generated_session_plan(
    db_path: str,
    session_id: int | None,
    practice_plan: dict | None,
) -> None:
    """Persist the generated practice plan on a session."""
    if not session_id or not practice_plan:
        return
    update_session_field(
        db_path,
        session_id,
        focus_sections_json=json.dumps(
            practice_plan.get("focus_sections", []), ensure_ascii=False
        ),
        focus_topics_json=json.dumps(
            practice_plan.get("focus_topics", []), ensure_ascii=False
        ),
        question_types_json=json.dumps(
            practice_plan.get("question_types", []), ensure_ascii=False
        ),
        recommendation_json=practice_plan.get("recommendation_table", ""),
    )


def complete_learning_session_after_submit(
    db_path: str,
    student_id: str,
    session_id: int | None,
    answers: list[dict],
) -> dict | None:
    """Finish session bookkeeping after attempts have already been recorded.

    If post-processing fails, attempts stay recorded and the session is marked
    aborted so the client can still receive the judging result.
    """
    if not session_id:
        return None

    try:
        post_profile = build_profile(student_id, db_path, mastery_backend="bkt")
        post_profile_dict = _profile_to_snapshot_dict(post_profile)
        post_snapshot_id = save_profile_snapshot(
            db_path,
            student_id=student_id,
            profile_dict=post_profile_dict,
            profile_version="bkt-v1",
            snapshot_type="post",
            session_id=session_id,
        )

        session_row = get_session(db_path, session_id)
        pre_snapshot_id = session_row.get("pre_snapshot_id") if session_row else None
        delta_mastery = _compute_delta_mastery(
            db_path, pre_snapshot_id, post_profile_dict
        )

        correct = sum(1 for a in answers if a.get("is_correct"))
        total = len(answers)
        accuracy = correct / total if total > 0 else 0.0
        effect = _build_effect_summary(delta_mastery)

        finish_learning_session(
            db_path,
            session_id,
            {
                "attempt_count": total,
                "correct_count": correct,
                "accuracy": accuracy,
                "delta_mastery_json": json.dumps(
                    delta_mastery, ensure_ascii=False
                ),
                "effect_summary": effect,
                "post_snapshot_id": post_snapshot_id,
            },
        )
        logger.info(
            "session completed: id=%s, accuracy=%.0f%%, delta_topics=%d",
            session_id,
            accuracy * 100,
            len(delta_mastery),
        )

        session_mode = session_row.get("mode", "") if session_row else ""
        if session_mode in ("practice", "diagnostic"):
            try:
                trend = build_trend_summary(db_path, student_id, window=5)
                err_dist = {
                    ERROR_TYPE_LABELS.get(k, k): v
                    for k, v in post_profile.error_distribution.items()
                }
                update_memory_facts(
                    db_path,
                    student_id,
                    trend,
                    error_distribution=err_dist,
                )
            except Exception:
                logger.exception("长期记忆更新失败(session=%s)", session_id)

        return {
            "id": session_id,
            "accuracy": accuracy,
            "effect_summary": effect,
        }
    except Exception:
        logger.exception("session 收尾失败(session=%s)，判题结果仍返回", session_id)
        try:
            abort_learning_session(
                db_path,
                session_id,
                "submit post-processing failed after attempts were recorded",
            )
        except Exception:
            logger.exception("标记 session=%s 为 aborted 失败", session_id)
        return {
            "id": session_id,
            "status": "aborted",
            "warning": "判题已完成，但学习记录收尾失败",
        }


def abort_session(db_path: str, session_id: int | None, reason: str) -> None:
    """Abort a session if one exists."""
    if session_id:
        abort_learning_session(db_path, session_id, reason)
