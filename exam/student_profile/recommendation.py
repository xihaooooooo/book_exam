"""Thompson Sampling Bandit + 推荐计划构建。

基于 BKT 产出的 P(L) 值，用 Thompson Sampling 对知识点排序，
按掌握度和错因推导难度、题型、题数，产出结构化的 RecommendationPlan。

用法：
    from exam.student_profile.recommendation import build_recommendation_plan
    plan = build_recommendation_plan(bkt_states, error_map, student_id, target_count)
"""

import random
import logging
from exam.student_profile.schemas import (
    BanditState,
    RecommendationItem,
    RecommendationPlan,
    ERROR_TYPE_QUESTIONS,
)

logger = logging.getLogger(__name__)

# ── 难度阶梯 ──


def _recommend_difficulty(p_mastery: float, error_type: str) -> str:
    """根据 BKT P(L) 和错因类型推荐起始难度。

    P(L) < 0.3  → easy        （基础缺陷，从简单开始）
    0.3 - 0.5   → easy_to_medium
    0.5 - 0.7   → medium       （中等掌握，巩固）
    > 0.7        → medium       （已经不错，维持即可）

    错因修正：
      memory_gap / careless → 保持 easy 直到 P(L) > 0.5
      concept_confusion / reasoning_error → P(L) > 0.5 时升到 medium
    """
    if p_mastery < 0.3:
        base = "easy"
    elif p_mastery < 0.5:
        base = "easy_to_medium"
    else:
        base = "medium"

    if error_type in ("memory_gap", "careless") and p_mastery < 0.5:
        return "easy"
    if error_type in ("concept_confusion", "reasoning_error") and p_mastery >= 0.5:
        return "medium"
    return base


def _recommend_question_types(error_type: str) -> list[str]:
    """根据主要错因推荐题型。"""
    if not error_type:
        return ["choice", "short_answer"]
    return ERROR_TYPE_QUESTIONS.get(error_type, ["choice"])


# ── Bandit 核心 ──


def init_bandit_states(
    bkt_states: list,  # list of BKTState
    session_rewards: dict[str, float] = None,  # {section_id: cumulative ΔP(L)}
    trend_summary: dict = None,   # Phase 4: trend signals
    memory_facts: list[dict] = None,  # Phase 4: long-term memory facts
) -> dict[str, BanditState]:
    """初始化 Thompson Sampling Beta 分布。

    Phase 1 先验（提升潜力）：
      potential = 1 - P(L)
      α_base = 1 + k × potential, β_base = 1 + k × P(L)

    Phase 2 闭环（session 奖励叠加）：
      α = α_base + Σ ΔP(L)_session      — 累计提升越多，越倾向继续练
      β = β_base + Σ (1 - ΔP(L)_session) — 练了没提升，自然降权

    Phase 4 长期记忆增强：
      declining/stalled topics → α 额外加成
      long-term weak topics → 根据置信度加成

    k 控制先验强度，默认 3。
    """
    if session_rewards is None:
        session_rewards = {}
    if trend_summary is None:
        trend_summary = {}
    if memory_facts is None:
        memory_facts = []

    k = 3.0
    bandit_states: dict[str, BanditState] = {}

    for bkt in bkt_states:
        sid = bkt.section_id

        if bkt.total_attempts == 0:
            bandit_states[sid] = BanditState(
                section_id=sid, alpha=1.0, beta=1.0)
            continue

        potential = max(0.01, 1.0 - bkt.p_mastery)
        alpha = 1.0 + k * potential
        beta = 1.0 + k * bkt.p_mastery

        # 叠加 session 奖励（用 improvement room 缩放）
        # 避免 mastered topic 的 BKT 冷启动 artifact reward 干扰排序
        reward = session_rewards.get(sid, 0.0)
        if reward > 0:
            effective = reward * potential  # reward × (1-P(L))
            alpha += effective
            beta += (1.0 - effective)

        # Phase 4: trend/memory bonuses
        # declining topics → boost α to bring them up
        for t in trend_summary.get("declining_topics", []):
            if t.get("section_id") == sid:
                alpha += 0.5
                break
        # stalled topics → slight boost
        for t in trend_summary.get("stalled_topics", []):
            if t.get("section_id") == sid:
                alpha += 0.3
                break

        # long-term weak topic → persistent boost by confidence
        for fact in memory_facts:
            if fact.get("memory_type") == "weak_topic" and fact.get("memory_key") == sid:
                alpha += fact.get("confidence", 0.5) * 2.0
                break

        bandit_states[sid] = BanditState(
            section_id=sid, alpha=alpha, beta=beta)

    return bandit_states


def _thompson_sample(
    bandit_states: dict[str, BanditState],
) -> list[tuple[str, float]]:
    """Thompson Sampling：每个 topic 从 Beta(α, β) 采样，按值降序排列。

    自然处理 explore vs exploit：
    - 高 reward + 大 N → Beta 集中在高值 → 大概率排在前面（exploit）
    - 低 N → Beta 分布宽 → 偶尔采样到高值 → 被探索（explore）
    - 低 reward + 大 N → Beta 集中在低值 → 排在后面（弃疗）
    """
    samples = []
    for sid, bs in bandit_states.items():
        theta = random.betavariate(bs.alpha, bs.beta)
        samples.append((sid, theta))
    samples.sort(key=lambda x: -x[1])
    return samples


# ── 推荐计划构建 ──


def _suggest_count(
    rank: int,
    p_mastery: float,
    total_topics: int,
    target_count: int,
) -> int:
    """根据排名和 P(L) 建议该 topic 的出题数量。

    规则：
    - 排名前 1/3 → 3 题（重点攻坚）
    - 排名中 1/3 → 2 题
    - 排名后 1/3 → 1 题
    - P(L) > 0.8 → 1 题（扫一下即可）
    - 确保总题数不超过 target_count
    """
    if p_mastery > 0.8:
        return 1

    if total_topics <= 2:
        return min(3, target_count // total_topics)

    tier = rank / total_topics
    if tier <= 0.33:
        return 3
    elif tier <= 0.67:
        return 2
    else:
        return 1


def build_recommendation_plan(
    bkt_states: list,       # list of BKTState
    error_map: dict[str, str],  # section_id → dominant_error_type
    student_id: str,
    target_count: int = 20,
    session_rewards: dict[str, float] = None,  # Phase 2: {section_id: cumulative ΔP(L)}
    trend_summary: dict = None,   # Phase 4: trend context
    memory_facts: list[dict] = None,  # Phase 4: long-term memory context
) -> RecommendationPlan:
    """从 BKT 状态构建推荐计划。

    Args:
        bkt_states: BKT 回放后的各知识点状态
        error_map: {section_id: dominant_error_type}
        student_id: 学生标识
        target_count: 目标总题数
        session_rewards: Phase 2 闭环奖励，叠加到 Bandit Beta 上
        trend_summary: Phase 4 趋势上下文，影响 Bandit 先验
        memory_facts: Phase 4 长期记忆，影响 Bandit 先验

    Returns:
        RecommendationPlan，items 按 bandit_score 降序
    """
    if not bkt_states:
        return RecommendationPlan(
            student_id=student_id,
            items=[],
            target_count=0,
            reason="无作答数据，无法生成推荐",
        )

    # 1. 初始化 Bandit 状态（先验 + session 奖励 + trend/memory 加成）
    bandit_states = init_bandit_states(
        bkt_states, session_rewards,
        trend_summary=trend_summary,
        memory_facts=memory_facts,
    )

    # 2. Thompson Sampling 排序
    ranked = _thompson_sample(bandit_states)
    rank_map = {sid: i for i, (sid, _) in enumerate(ranked)}

    # 3. 构建推荐条目
    items: list[RecommendationItem] = []
    total_topics = len(bkt_states)
    running_count = 0

    # BKT state lookup
    bkt_map = {b.section_id: b for b in bkt_states}

    for sid, score in ranked:
        bkt = bkt_map.get(sid)
        if bkt is None:
            continue

        rank = rank_map[sid]
        error_type = error_map.get(sid, "")

        difficulty = _recommend_difficulty(bkt.p_mastery, error_type)
        qtypes = _recommend_question_types(error_type)
        count = _suggest_count(rank, bkt.p_mastery, total_topics, target_count)

        # 上限控制
        remaining = target_count - running_count
        if count > remaining and remaining > 0:
            count = remaining
        if count <= 0:
            continue

        running_count += count

        items.append(RecommendationItem(
            section_id=sid,
            topic=bkt.topic,
            p_mastery=bkt.p_mastery,
            bandit_score=score,
            difficulty=difficulty,
            question_types=qtypes,
            recommended_count=count,
            dominant_error_type=error_type,
        ))

    # 4. 构建 reason
    reason = _build_reason(items, bkt_states)

    logger.info(
        "recommendation: student=%s, topics=%d, items=%d, total=%d",
        student_id, total_topics, len(items), running_count,
    )

    return RecommendationPlan(
        student_id=student_id,
        items=items,
        target_count=running_count,
        reason=reason,
    )


def _build_reason(items: list[RecommendationItem],
                  bkt_states: list) -> str:
    """生成推荐原因的文本描述。"""
    if not items:
        return "无足够数据生成推荐"

    weak = [i for i in items if i.p_mastery < 0.5]
    unstable = [i for i in items if 0.5 <= i.p_mastery < 0.75]
    top_errors: dict[str, int] = {}
    for i in items:
        if i.dominant_error_type:
            top_errors[i.dominant_error_type] = \
                top_errors.get(i.dominant_error_type, 0) + 1

    parts = []
    if weak:
        parts.append(f"薄弱 {len(weak)} 个知识点（P(L)<0.5）")
    if unstable:
        parts.append(f"不稳定 {len(unstable)} 个知识点（0.5≤P(L)<0.75）")

    if top_errors:
        sorted_errors = sorted(top_errors.items(), key=lambda x: -x[1])[:2]
        from exam.student_profile.schemas import ERROR_TYPE_LABELS
        labels = [ERROR_TYPE_LABELS.get(e, e) for e, _ in sorted_errors]
        parts.append(f"主要错因：{'、'.join(labels)}")

    return "，".join(parts) if parts else "基于 BKT + Bandit 生成"
