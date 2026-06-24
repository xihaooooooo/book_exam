"""策略路由节点。根据 mode 推导 focus / target_count / allowed_types。

practice 模式调用 ProfileGraph Agent 获取完整画像，
根据掌握等级 + 主要错因推导出题策略。
"""

import logging
from exam.agents.utils.agent_states import AgentState
from exam.graph.profile_graph import ProfileGraph
from exam.student_profile.schemas import ERROR_TYPE_LABELS

logger = logging.getLogger(__name__)

# 错因 → 推荐题型映射
ERROR_TYPE_QUESTIONS = {
    "concept_confusion":  ["choice", "short_answer"],    # 辨析题
    "reasoning_error":    ["short_answer", "comprehensive"],  # 推理链
    "memory_gap":         ["choice", "fill_blank"],      # 记忆型
    "transfer_failure":   ["choice", "short_answer", "comprehensive"],  # 变体
    "misread_question":   ["choice"],                     # 审题
    "careless":           ["choice", "fill_blank"],      # 控制难度
}


def strategy_router(state: AgentState) -> dict:
    """出题策略路由。在 chief_editor 之前运行。"""
    mode = state.get("mode", "exam")
    focus = state.get("focus", "")
    target_count = state.get("target_count", 0)
    allowed_types = state.get("allowed_types", "")
    db_path = state.get("db_path", "cache/sections.db")
    practice_plan = None

    if mode == "diagnostic":
        toc = state.get("toc", [])
        chapter_count = len(toc)
        if target_count <= 0:
            target_count = min(chapter_count * 2, 30)
            target_count = max(target_count, 6)
        allowed_types = "choice"
        logger.info("strategy: diagnostic mode, %d chapters, target=%d",
                     chapter_count, target_count)

    elif mode == "practice":
        student_id = state.get("student_id", "")
        if student_id:
            # 调 ProfileGraph Agent 拿画像
            attempts_db = db_path.replace("sections.db", "attempts.db")
            pg = ProfileGraph()
            result = pg.invoke({"student_id": student_id, "db_path": attempts_db})
            profile = result.get("profile", {})

            topics = profile.get("topics", [])
            data_ok = profile.get("total_attempts", 0) > 0

            if data_ok:
                # 按掌握等级排序：weak > unstable > familiar
                weak_topics = [t for t in topics
                               if t.get("mastery_level") in ("weak", "unstable")]
                familiar = [t for t in topics
                           if t.get("mastery_level") == "familiar"]

                if weak_topics:
                    if not focus:
                        focus = ",".join(t["section_id"] for t in weak_topics)
                    if target_count <= 0:
                        target_count = min(len(weak_topics) * 2 + len(familiar), 30)

                    # 根据主要错因推导题型
                    dominant_errors = set(
                        t.get("dominant_error_type", "")
                        for t in weak_topics
                        if t.get("dominant_error_type")
                    )
                    if not allowed_types and dominant_errors:
                        qtypes = set()
                        for etype in dominant_errors:
                            qtypes.update(ERROR_TYPE_QUESTIONS.get(etype, ["choice"]))
                        allowed_types = ",".join(list(qtypes)[:3])

                    # 难度：看信心和连续错误
                    has_high_confidence_error = any(
                        t.get("streak_wrong", 0) >= 2
                        for t in weak_topics
                    )
                    difficulty = "easy" if has_high_confidence_error else "easy_to_medium"

                    practice_plan = {
                        "student_id": student_id,
                        "focus_sections": [t["section_id"] for t in weak_topics],
                        "focus_topics": [t.get("topic", "") for t in weak_topics],
                        "question_types": allowed_types.split(",") if allowed_types else [],
                        "difficulty": difficulty,
                        "target_count": target_count,
                        "reason": _build_reason(profile),
                    }

                    logger.info(
                        "strategy: practice mode via ProfileGraph, student=%s, "
                        "weak=%d, focus=%s, types=%s",
                        student_id, len(weak_topics), focus, allowed_types,
                    )

        # 没有数据时回退
        if not practice_plan and not focus:
            logger.warning("strategy: practice mode 无弱项数据，回退通用出题")

    else:  # exam
        logger.info("strategy: exam mode")

    return {
        "focus": focus,
        "target_count": target_count,
        "allowed_types": allowed_types,
        "practice_plan": practice_plan,
    }


def _build_reason(profile: dict) -> str:
    """基于画像生成推荐原因。"""
    mastery_summary = profile.get("mastery_summary", {})
    weak_count = mastery_summary.get("weak", 0)
    unstable_count = mastery_summary.get("unstable", 0)

    error_dist = profile.get("error_distribution", {})
    top_errors = sorted(error_dist.items(), key=lambda x: x[1], reverse=True)[:2]
    error_reason = ""
    if top_errors:
        labels = [ERROR_TYPE_LABELS.get(e[0], e[0]) for e in top_errors]
        error_reason = f"，主要错因：{'、'.join(labels)}"

    risks = profile.get("risk_signals", [])
    risk_note = ""
    if risks:
        risk_note = f"。风险：{risks[0]}" if risks else ""

    return (
        f"薄弱 {weak_count} 个、不稳定 {unstable_count} 个知识点"
        f"{error_reason}{risk_note}"
    )
