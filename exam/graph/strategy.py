"""策略路由节点。根据 mode 推导 focus / target_count / allowed_types。

practice 模式调用 ProfileGraph Agent（BKT 后端）+ 推荐引擎（Thompson Sampling），
根据掌握概率 + 主要错因推导结构化出题策略。
"""

import logging
from exam.agents.utils.agent_states import AgentState
from exam.graph.profile_graph import ProfileGraph

logger = logging.getLogger(__name__)


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
            attempts_db = db_path.replace("sections.db", "attempts.db")

            # 调 ProfileGraph Agent，使用 BKT 后端获取连续掌握概率
            pg = ProfileGraph()
            result = pg.invoke({
                "student_id": student_id,
                "db_path": attempts_db,
                "mastery_backend": "bkt",
            })
            profile = result.get("profile", {})

            topics = profile.get("topics", [])
            data_ok = profile.get("total_attempts", 0) > 0

            if data_ok:
                # 提取 BKT 状态和错因映射
                from exam.student_profile.schemas import BKTState, BKTParams
                from exam.student_profile.recommendation import build_recommendation_plan

                bkt_states: list[BKTState] = []
                error_map: dict[str, str] = {}

                for t in topics:
                    bkt_dict = t.get("bkt_state")
                    if bkt_dict and isinstance(bkt_dict, dict):
                        # ProfileGraph asdict() 序列化 → 还原为 BKTState
                        params_dict = bkt_dict.get("params", {})
                        bkt_states.append(BKTState(
                            section_id=bkt_dict["section_id"],
                            topic=bkt_dict.get("topic", ""),
                            p_mastery=bkt_dict["p_mastery"],
                            p_initial=bkt_dict["p_initial"],
                            total_attempts=bkt_dict["total_attempts"],
                            correct_count=bkt_dict["correct_count"],
                            params=BKTParams(
                                p_L0=params_dict.get("p_L0", 0.3),
                                p_T=params_dict.get("p_T", 0.15),
                                p_G=params_dict.get("p_G", 0.2),
                                p_S=params_dict.get("p_S", 0.1),
                            ),
                        ))
                    error_map[t["section_id"]] = t.get("dominant_error_type", "")

                # Phase 2：计算 session 奖励（优先显式 session，回退时间窗口）
                from exam.student_profile.profile_engine import compute_session_rewards as _old_rewards
                from exam.student_profile.trend_engine import compute_explicit_session_rewards, build_trend_summary
                from exam.student_profile.memory_engine import get_active_memory_facts
                session_rewards = compute_explicit_session_rewards(
                    attempts_db, student_id)
                if session_rewards is None:
                    # 尚无显式 session 数据，回退时间窗口算法
                    from exam.student_profile.profile_engine import BKTParams as _BKTParams
                    session_rewards = _old_rewards(
                        attempts_db, student_id, _BKTParams())
                    logger.info("strategy: 回退到时间窗口 session reward")

                # Phase 4：加载趋势和长期记忆上下文
                trend_summary = build_trend_summary(attempts_db, student_id, window=5)
                memory_facts = get_active_memory_facts(attempts_db, student_id)

                # 调推荐引擎：BKT P(L) → Bandit 排序 → RecommendationPlan
                if not target_count or target_count <= 0:
                    target_count = min(len(bkt_states) * 3, 20)

                plan = build_recommendation_plan(
                    bkt_states=bkt_states,
                    error_map=error_map,
                    student_id=student_id,
                    target_count=target_count,
                    session_rewards=session_rewards,
                    trend_summary=trend_summary,
                    memory_facts=memory_facts,
                )

                if plan.items:
                    focus = ",".join(
                        f"{item.section_id} ({item.topic})"
                        for item in plan.items
                    )
                    target_count = plan.target_count

                    # 收集所有推荐题型
                    all_types: set[str] = set()
                    for item in plan.items:
                        all_types.update(item.question_types)
                    allowed_types = ",".join(list(all_types)[:3])

                    # 难度取第一个（最高优先级）的推荐
                    first_difficulty = plan.items[0].difficulty if plan.items else "easy"

                    practice_plan = {
                        "student_id": student_id,
                        "focus_sections": [item.section_id for item in plan.items],
                        "focus_topics": [item.topic for item in plan.items],
                        "question_types": list(all_types),
                        "difficulty": first_difficulty,
                        "target_count": target_count,
                        "reason": plan.reason,
                        "p_mastery": {
                            item.section_id: f"{item.p_mastery:.0%}"
                            for item in plan.items
                        },
                        "recommendation_table": plan.to_prompt_table(),
                    }

                    logger.info(
                        "strategy: practice mode via BKT+Bandit, student=%s, "
                        "topics=%d, items=%d, target=%d",
                        student_id, len(topics), len(plan.items), target_count,
                    )
                else:
                    logger.warning("strategy: 推荐引擎无产出，回退阈值模式")
                    focus, target_count, allowed_types = _fallback_threshold(
                        topics, focus, target_count, allowed_types)

            else:
                logger.warning("strategy: practice mode 无数据，回退通用出题")

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


def _fallback_threshold(topics: list, focus: str, target_count: int,
                        allowed_types: str):
    """BKT+Bandit 无产出时，回退阈值模式的出题策略。"""
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
            from exam.student_profile.schemas import ERROR_TYPE_QUESTIONS
            qtypes = set()
            for etype in dominant_errors:
                qtypes.update(ERROR_TYPE_QUESTIONS.get(etype, ["choice"]))
            allowed_types = ",".join(list(qtypes)[:3])

    logger.info("strategy: fallback threshold, weak=%d", len(weak_topics))
    return focus, target_count, allowed_types
