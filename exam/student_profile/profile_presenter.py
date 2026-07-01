"""Build JSON-ready profile responses for the Web/API layer."""

import logging
import os
import re
import sqlite3
from dataclasses import asdict

from exam.student_profile.memory_engine import get_active_memory_facts
from exam.student_profile.profile_engine import (
    build_profile,
    compute_session_rewards as _old_session_rewards,
)
from exam.student_profile.recommendation import (
    build_recommendation_plan,
    init_bandit_states,
    recommendation_key,
)
from exam.student_profile.schemas import ERROR_TYPE_LABELS
from exam.student_profile.session_storage import get_recent_sessions
from exam.student_profile.trend_engine import (
    build_trend_summary,
    compute_explicit_session_rewards,
)

logger = logging.getLogger(__name__)


def _load_section_titles(sections_db: str) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (titles_plain, titles_rich)。

    titles_plain: 清洗 LaTeX 后的纯文本，画像页用
    titles_rich: 保留原始 LaTeX，答题区 KaTeX 渲染用
    """
    empty: dict[str, str] = {}
    if not sections_db or not os.path.exists(sections_db):
        return empty, empty

    try:
        conn = sqlite3.connect(sections_db)
        rows = conn.execute("SELECT id, title FROM sections").fetchall()
        conn.close()
    except Exception:
        logger.exception("章节标题读取失败: %s", sections_db)
        return empty, empty

    latex_re = re.compile(r"\$.*?\$|\\mathrm|\\mathbf|\\mathit|\\text|\\[a-z]+\{|\}|\\")
    space_re = re.compile(r"\s{2,}")
    titles_plain: dict[str, str] = {}
    titles_rich: dict[str, str] = {}
    for section_id, title in rows:
        if section_id and title:
            clean = latex_re.sub("", title)
            clean = space_re.sub(" ", clean).strip()
            titles_plain[section_id] = clean or title
            titles_rich[section_id] = title
    return titles_plain, titles_rich


def _extract_bkt_and_errors(profile) -> tuple[list, dict[str, str]]:
    bkt_states = []
    error_map: dict[str, str] = {}
    for topic in profile.topics:
        if topic.bkt_state is not None:
            bkt_states.append(topic.bkt_state)
        if topic.dominant_error_type:
            error_map[recommendation_key(topic.section_id, topic.topic)] = topic.dominant_error_type
    return bkt_states, error_map


def _confidence_meta(total_attempts: int) -> dict[str, str | int]:
    if total_attempts < 5:
        return {
            "evidence_count": total_attempts,
            "confidence_level": "low",
            "confidence_label": "数据不足",
            "confidence_reason": f"仅 {total_attempts} 次作答，结论仅供参考",
        }
    if total_attempts < 10:
        return {
            "evidence_count": total_attempts,
            "confidence_level": "medium",
            "confidence_label": "初步判断",
            "confidence_reason": f"{total_attempts} 次作答，已有初步依据",
        }
    return {
        "evidence_count": total_attempts,
        "confidence_level": "high",
        "confidence_label": "较可信",
        "confidence_reason": f"{total_attempts} 次作答，样本相对充分",
    }


def _build_topics_json(profile, bandit_states, section_titles: dict[str, str],
                        section_titles_rich: dict[str, str] | None = None) -> list[dict]:
    if section_titles_rich is None:
        section_titles_rich = {}
    bandit_map = {key: bs for key, bs in bandit_states.items()}

    def topic_sort_key(topic):
        bkt = topic.bkt_state
        return bkt.p_mastery if bkt else 1.0

    topics_json = []
    for topic in sorted(profile.topics, key=topic_sort_key):
        display_title = topic.topic or section_titles.get(topic.section_id, "")
        entry = {
            "section_id": topic.section_id,
            "topic": display_title,
            "total_attempts": topic.total_attempts,
            "accuracy": topic.accuracy,
            "recent_accuracy": topic.recent_accuracy,
            "mastery_level": topic.mastery_level,
            "dominant_error_type": ERROR_TYPE_LABELS.get(
                topic.dominant_error_type,
                topic.dominant_error_type,
            ),
            "streak_wrong": topic.streak_wrong,
            **_confidence_meta(topic.total_attempts),
            "topic_rich": section_titles_rich.get(topic.section_id, ""),
        }
        if topic.bkt_state is not None:
            entry["bkt"] = {
                "p_mastery": topic.bkt_state.p_mastery,
                "p_initial": topic.bkt_state.p_initial,
                "total_attempts": topic.bkt_state.total_attempts,
                "correct_count": topic.bkt_state.correct_count,
                "params": asdict(topic.bkt_state.params),
            }
        bs = bandit_map.get(
            recommendation_key(topic.section_id, topic.topic),
            bandit_map.get(topic.section_id),
        )
        if bs is not None:
            entry["bandit"] = {
                "alpha": bs.alpha,
                "beta": bs.beta,
            }
        topics_json.append(entry)
    return topics_json


def build_profile_response(
    student_id: str,
    attempts_db: str,
    sections_db: str = "",
    target_count: int = 20,
) -> dict:
    """Build the JSON response returned by /api/profile."""
    profile = build_profile(student_id, attempts_db, mastery_backend="bkt")
    bkt_states, error_map = _extract_bkt_and_errors(profile)

    session_rewards = compute_explicit_session_rewards(attempts_db, student_id)
    if session_rewards is None:
        session_rewards = _old_session_rewards(attempts_db, student_id)

    try:
        trend_summary = build_trend_summary(attempts_db, student_id, window=5)
    except Exception:
        logger.exception("趋势摘要构建失败 student=%s", student_id)
        trend_summary = {}

    try:
        memory_facts = get_active_memory_facts(attempts_db, student_id)
    except Exception:
        logger.exception("长期记忆读取失败 student=%s", student_id)
        memory_facts = []

    bandit_states = init_bandit_states(
        bkt_states,
        session_rewards,
        trend_summary=trend_summary,
        memory_facts=memory_facts,
    )
    plan = build_recommendation_plan(
        bkt_states,
        error_map,
        student_id,
        target_count=target_count,
        session_rewards=session_rewards,
        trend_summary=trend_summary,
        memory_facts=memory_facts,
        rank_strategy="mean",
    )

    error_dist = {
        ERROR_TYPE_LABELS.get(error_type, error_type): count
        for error_type, count in profile.error_distribution.items()
    }
    rec_json = {
        "items": [asdict(item) for item in plan.items],
        "target_count": plan.target_count,
        "reason": plan.reason,
    }
    section_titles, section_titles_rich = _load_section_titles(sections_db)

    return {
        "ok": True,
        "student_id": profile.student_id,
        "overall_accuracy": profile.overall_accuracy,
        "total_attempts": profile.total_attempts,
        "mastery_summary": profile.mastery_summary,
        "topics": _build_topics_json(profile, bandit_states, section_titles, section_titles_rich),
        "recommendation": rec_json,
        "error_distribution": error_dist,
        "risk_signals": profile.risk_signals,
        "recent_sessions": get_recent_sessions(attempts_db, student_id, limit=5),
        "trend_summary": trend_summary,
        "memory_facts": memory_facts,
    }
