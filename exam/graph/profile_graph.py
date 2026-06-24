"""学生画像图。从 attempts + error_labels 聚合画像。

用法：
    from exam.graph.profile_graph import ProfileGraph
    pg = ProfileGraph()
    result = pg.invoke({"student_id": "S001", "db_path": "cache/attempts.db"})
    profile = result["profile"]
"""

import sqlite3
import logging
from dataclasses import asdict, is_dataclass
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional
from exam.student_profile.profile_engine import _compute_topic_stat, _compute_mastery, \
    _get_dominant_error, _compute_error_distribution, _detect_risk_signals, \
    StudentProfile, RECENT_WINDOW
from exam.student_profile.schemas import ERROR_TYPE_LABELS

logger = logging.getLogger(__name__)


class ProfileState(TypedDict):
    student_id: str
    db_path: str
    profile: Optional[dict]


class ProfileGraph:
    """画像聚合 LangGraph 图。单节点 build_profile，只读不写。"""

    def __init__(self):
        self.graph = self._build()

    def invoke(self, state: dict) -> dict:
        return self.graph.invoke(state)

    def _build(self):
        graph = StateGraph(ProfileState)
        graph.add_node("build_profile", self._build_profile_node)
        graph.set_entry_point("build_profile")
        graph.add_edge("build_profile", END)
        return graph.compile()

    def _build_profile_node(self, state: ProfileState) -> dict:
        student_id = state["student_id"]
        db_path = state.get("db_path", "cache/attempts.db")

        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row

        attempts = db.execute(
            """SELECT * FROM attempts
               WHERE student_id = ? AND section_id != ''
               ORDER BY created_at""",
            (student_id,),
        ).fetchall()

        if not attempts:
            db.close()
            return {"profile": asdict(StudentProfile(student_id=student_id))}

        total = len(attempts)
        correct_count = sum(1 for a in attempts if a["is_correct"])
        overall_accuracy = correct_count / total if total > 0 else 0.0

        groups: dict[tuple[str, str], list] = {}
        for a in attempts:
            key = (a["section_id"], a["topic"] or "")
            if key not in groups:
                groups[key] = []
            groups[key].append(a)

        topics = []
        for (sid, topic), group in groups.items():
            stat = _compute_topic_stat(sid, topic, group, all_attempts=attempts)
            stat.dominant_error_type = _get_dominant_error(db, group)
            stat.mastery_level = _compute_mastery(stat)
            topics.append(stat)

        error_dist = _compute_error_distribution(db, student_id)
        risks = _detect_risk_signals(topics, error_dist)

        db.close()

        profile = StudentProfile(
            student_id=student_id,
            topics=topics,
            error_distribution=error_dist,
            risk_signals=risks,
            overall_accuracy=overall_accuracy,
            total_attempts=total,
        )

        logger.info("profile: student=%s, topics=%d, accuracy=%.0f%%",
                     student_id, len(topics), overall_accuracy * 100)

        profile_dict = asdict(profile)
        profile_dict["weakest_topics"] = [
            asdict(t) for t in profile.weakest_topics
        ]
        profile_dict["mastery_summary"] = profile.mastery_summary
        return {"profile": profile_dict}
