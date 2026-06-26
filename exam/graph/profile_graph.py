"""学生画像图。从 attempts + error_labels 聚合画像。

用法：
    from exam.graph.profile_graph import ProfileGraph
    pg = ProfileGraph()
    result = pg.invoke({"student_id": "S001", "db_path": "cache/attempts.db"})
    # 使用 BKT 后端：
    result = pg.invoke({"student_id": "S001", "db_path": "cache/attempts.db",
                        "mastery_backend": "bkt"})
    profile = result["profile"]
"""

import logging
from dataclasses import asdict
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional
from exam.student_profile.profile_engine import build_profile, StudentProfile
from exam.student_profile.schemas import ERROR_TYPE_LABELS

logger = logging.getLogger(__name__)


class ProfileState(TypedDict):
    student_id: str
    db_path: str
    mastery_backend: str          # "threshold"（默认）或 "bkt"
    profile: Optional[dict]


class ProfileGraph:
    """画像聚合 LangGraph 图。单节点 build_profile，只读不写。

    通过 mastery_backend 参数选择掌握评估算法：
    - "threshold"（默认）：硬阈值分类，兼容现有行为
    - "bkt"：贝叶斯知识追踪，产出连续 P(L) 概率
    """

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
        mastery_backend = state.get("mastery_backend", "threshold")

        profile = build_profile(student_id, db_path,
                                mastery_backend=mastery_backend)

        profile_dict = asdict(profile)
        profile_dict["weakest_topics"] = [
            asdict(t) for t in profile.weakest_topics
        ]
        profile_dict["mastery_summary"] = profile.mastery_summary
        return {"profile": profile_dict}
