"""学生画像聚合引擎。从 attempts + error_labels 实时聚合。

用法：
    from exam.student_profile.profile_engine import build_profile
    profile = build_profile("S001", "cache/attempts.db")
    print(profile.weakest_topics)
"""

import sqlite3
import logging
from dataclasses import dataclass, field
from exam.student_profile.schemas import (
    ERROR_TYPES, ERROR_TYPE_LABELS, ERROR_PRIORITY,
    BKTParams, BKTState,
)

logger = logging.getLogger(__name__)


RECENT_WINDOW = 10
MASTERED_MIN_ATTEMPTS = 5
DURATION_SLOW_FACTOR = 1.3
CONFIDENCE_LOW_THRESHOLD = 3.5


@dataclass
class TopicStat:
    """单个知识点的聚合统计。"""
    section_id: str
    topic: str
    total_attempts: int = 0
    wrong_count: int = 0
    accuracy: float = 0.0
    recent_accuracy: float = 0.0
    avg_duration_sec: float = 0.0
    avg_confidence: float = 0.0
    dominant_error_type: str = ""
    streak_wrong: int = 0
    mastery_level: str = "unknown"
    bkt_state: any = None  # BKTState | None — BKT 后端填充，阈值后端为 None


@dataclass
class StudentProfile:
    """学生完整画像。"""
    student_id: str
    topics: list[TopicStat] = field(default_factory=list)
    error_distribution: dict = field(default_factory=dict)   # {error_type: count}
    risk_signals: list[str] = field(default_factory=list)
    overall_accuracy: float = 0.0
    total_attempts: int = 0

    @property
    def weakest_topics(self) -> list[TopicStat]:
        """按薄弱程度排序：weak → unstable → familiar。"""
        order = {"weak": 0, "unstable": 1, "familiar": 2, "unknown": 3, "mastered": 4}
        return sorted(
            [t for t in self.topics if t.mastery_level in ("weak", "unstable", "familiar")],
            key=lambda t: (order.get(t.mastery_level, 9), -t.accuracy),
        )

    @property
    def mastery_summary(self) -> dict[str, int]:
        """各等级的 topic 数量。"""
        summary = {"mastered": 0, "familiar": 0, "unstable": 0, "weak": 0, "unknown": 0}
        for t in self.topics:
            summary[t.mastery_level] = summary.get(t.mastery_level, 0) + 1
        return summary


# ── 公共数据加载 ──

def normalize_section_id(section_id: str) -> str:
    """归一化章节编号到粗粒度（章.节）。

    "1.1.2" → "1.1", "2.3.1" → "2.3", "1.1" → "1.1"

    消除 LLM 出题（细粒度）和手写题（粗粒度）之间的粒度不一致。
    """
    if not section_id:
        return ""
    parts = section_id.split(".")
    if len(parts) > 2:
        return ".".join(parts[:2])
    return section_id


def _load_topic_groups(db: sqlite3.Connection, student_id: str
                       ) -> tuple[list, dict[tuple[str, str], list]]:
    """加载学生所有 attempts，按 (section_id, topic) 分组。

    BKT 回放和阈值评估共用此函数，避免重复的 DB 查询和分组逻辑。

    Returns:
        (all_attempts, groups) — all_attempts 按时间升序，
        groups 为 {(section_id, topic): [attempts]}
    """
    attempts = db.execute(
        """SELECT * FROM attempts
           WHERE student_id = ? AND section_id != ''
           ORDER BY created_at""",
        (student_id,),
    ).fetchall()

    if not attempts:
        return [], {}

    # 转 dict + 归一化 section_id，消除 1.1.2 vs 1.1 的粒度分裂
    normalized = []
    for a in attempts:
        d = dict(a)
        d["section_id"] = normalize_section_id(d["section_id"])
        normalized.append(d)

    groups: dict[tuple[str, str], list] = {}
    for d in normalized:
        key = (d["section_id"], d["topic"] or "")
        if key not in groups:
            groups[key] = []
        groups[key].append(d)

    return normalized, groups


# ── 主入口 ──

def build_profile(student_id: str, db_path: str,
                  mastery_backend: str = "threshold") -> StudentProfile:
    """从数据库聚合学生画像。

    Args:
        student_id: 学生标识
        db_path: attempts.db 路径
        mastery_backend: 掌握评估后端 — "threshold"（硬阈值，默认）或 "bkt"
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    all_attempts, groups = _load_topic_groups(db, student_id)

    if not all_attempts:
        db.close()
        return StudentProfile(student_id=student_id)

    # 全局统计
    total = len(all_attempts)
    correct_count = sum(1 for a in all_attempts if a["is_correct"])
    overall_accuracy = correct_count / total if total > 0 else 0.0

    # 聚合每个 topic
    topics = []
    for (sid, topic), group in groups.items():
        stat = _compute_topic_stat(sid, topic, group, all_attempts=all_attempts)
        stat.dominant_error_type = _get_dominant_error(db, group)

        if mastery_backend == "bkt":
            bkt = _bkt_replay(group, BKTParams())
            stat.mastery_level = _compute_mastery_bkt(bkt.p_mastery, stat.total_attempts)
            # 附加 BKT 状态到 TopicStat（非标准字段，用于下游推荐引擎）
            stat.bkt_state = bkt  # type: ignore[attr-defined]
        else:
            stat.mastery_level = _compute_mastery(stat)

        topics.append(stat)

    # 错因分布
    error_dist = _compute_error_distribution(db, student_id)

    # 风险信号
    risks = _detect_risk_signals(topics, error_dist)

    db.close()

    logger.info("profile: student=%s, backend=%s, topics=%d, accuracy=%.0f%%",
                student_id, mastery_backend, len(topics), overall_accuracy * 100)

    return StudentProfile(
        student_id=student_id,
        topics=topics,
        error_distribution=error_dist,
        risk_signals=risks,
        overall_accuracy=overall_accuracy,
        total_attempts=total,
    )


# ── topic 聚合 ──

def _compute_topic_stat(section_id: str, topic: str,
                         group: list, all_attempts: list) -> TopicStat:
    n = len(group)
    wrong = sum(1 for a in group if not a["is_correct"])
    accuracy = (n - wrong) / n if n > 0 else 0.0

    # 近期正确率
    recent = group[-RECENT_WINDOW:]
    recent_n = len(recent)
    recent_correct = sum(1 for a in recent if a["is_correct"])
    recent_accuracy = recent_correct / recent_n if recent_n > 0 else 0.0

    # 平均耗时和信心
    avg_duration = sum(a["duration_sec"] or 0 for a in group) / n if n > 0 else 0.0
    avg_confidence = sum(a["confidence"] or 3 for a in group) / n if n > 0 else 3.0

    # 连续错误（从最近的作答往前数）
    streak = 0
    for a in reversed(group):
        if not a["is_correct"]:
            streak += 1
        else:
            break

    # 全局同类题平均耗时（用于风险检测）
    global_avg_duration = 0.0
    same_type = [a for a in all_attempts
                 if a["question_type"] == group[0]["question_type"]]
    if same_type:
        global_avg_duration = sum(a["duration_sec"] or 0 for a in same_type) / len(same_type)

    return TopicStat(
        section_id=section_id,
        topic=topic,
        total_attempts=n,
        wrong_count=wrong,
        accuracy=accuracy,
        recent_accuracy=recent_accuracy,
        avg_duration_sec=avg_duration,
        avg_confidence=avg_confidence,
        streak_wrong=streak,
    )


# ── 掌握等级 ──

def _compute_mastery(stat: TopicStat) -> str:
    if stat.total_attempts < 2:
        return "unknown"
    if (stat.recent_accuracy >= 0.85
            and stat.total_attempts >= MASTERED_MIN_ATTEMPTS
            and stat.avg_confidence >= 4
            and stat.streak_wrong == 0):
        return "mastered"
    if stat.recent_accuracy < 0.5 or stat.streak_wrong >= 2:
        return "weak"
    if 0.5 <= stat.recent_accuracy < 0.75:
        return "unstable"
    if stat.recent_accuracy >= 0.75:
        # familiar：对但不够熟练
        return "familiar"
    return "unknown"


# ── BKT 掌握评估 ──


def _bkt_replay(attempts: list, params: BKTParams) -> BKTState:
    """按时间序列回放 BKT，计算当前 P(L)。

    每次答题：
    1. 学习转移：P(L) = P(L) + (1-P(L)) × P(T)
    2. 贝叶斯更新：根据答对/答错更新 P(L)
    3. 钳制在 [0.001, 0.999] 防止数值坍缩

    Args:
        attempts: 按 created_at 升序的 attempt rows（单个 topic）
        params: BKT 超参数

    Returns:
        BKTState，含最终 P(L) 和轨迹信息
    """
    p_L = params.p_L0
    p_initial = p_L
    correct_count = 0

    for a in attempts:
        # 1. 学习转移
        p_L = p_L + (1.0 - p_L) * params.p_T

        # 2. 贝叶斯更新
        if a["is_correct"]:
            p_correct_given_known = 1.0 - params.p_S
            p_correct_given_unknown = params.p_G
            p_obs = p_L * p_correct_given_known + (1 - p_L) * p_correct_given_unknown
            if p_obs > 0:
                p_L = p_L * p_correct_given_known / p_obs
            correct_count += 1
        else:
            p_wrong_given_known = params.p_S
            p_wrong_given_unknown = 1.0 - params.p_G
            p_obs = p_L * p_wrong_given_known + (1 - p_L) * p_wrong_given_unknown
            if p_obs > 0:
                p_L = p_L * p_wrong_given_known / p_obs

        # 3. 钳制
        p_L = max(0.001, min(0.999, p_L))

    sid = attempts[0]["section_id"] if attempts else ""
    topic = attempts[0]["topic"] if attempts else ""

    return BKTState(
        section_id=sid,
        topic=topic or "",
        p_mastery=p_L,
        p_initial=p_initial,
        total_attempts=len(attempts),
        correct_count=correct_count,
        params=params,
    )


def _compute_mastery_bkt(p_mastery: float, total_attempts: int) -> str:
    """将 BKT P(L) 映射到 categorical 掌握等级（用于展示兼容）。

    映射规则（比硬阈值宽松，因为 P(L) 本身已编码了不确定性）：
      P(L) ≥ 0.85 → mastered
      P(L) ≥ 0.70 → familiar
      P(L) ≥ 0.50 → unstable
      P(L) <  0.50 → weak（或 unknown，如果 attempt 太少）
    """
    if total_attempts < 2:
        return "unknown"
    if p_mastery >= 0.85:
        return "mastered"
    if p_mastery >= 0.70:
        return "familiar"
    if p_mastery >= 0.50:
        return "unstable"
    return "weak"


# ── Session 奖励计算（Phase 2 闭环）──

SESSION_GAP_MINUTES = 30  # 两次作答间隔超过此值视为不同 session


def compute_session_rewards(
    db_path: str,
    student_id: str,
    params: BKTParams = None,
) -> dict[str, float]:
    """按时间窗口切分 session，计算每个 topic 的累计 ΔP(L) 奖励。

    流程：
    1. 加载学生所有 attempts，按 topic 分组，时间升序
    2. 间隔 > 30 分钟的切为一个 session
    3. 对每个 session：BKT 回放得到 pre/post P(L)
    4. reward = max(0, P(L)_post - P(L)_pre)  →  累加到 total

    Args:
        db_path: attempts.db 路径
        student_id: 学生标识
        params: BKT 参数，默认用文献值

    Returns:
        {section_id: cumulative_reward}  — reward ∈ [0, 1] per session，
        多个 session 的 reward 累加后可能 > 1
    """
    import sqlite3
    from datetime import datetime, timedelta

    if params is None:
        params = BKTParams()

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    all_attempts, groups = _load_topic_groups(db, student_id)
    db.close()

    if not groups:
        return {}

    def _parse_time(ts: str):
        """解析 SQLite datetime 字符串。"""
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return datetime.min

    rewards: dict[str, float] = {}

    for (sid, _), group in groups.items():
        if len(group) < 2:
            # 少于 2 题无法切 session，用全局 ΔP(L) = P(L) - P(L₀)
            state = _bkt_replay(group, params)
            rewards[sid] = max(0.0, state.p_mastery - state.p_initial)
            continue

        # 按时序切 session
        sessions: list[list] = []
        current_session: list = [group[0]]

        for i in range(1, len(group)):
            prev_time = _parse_time(group[i - 1]["created_at"])
            curr_time = _parse_time(group[i]["created_at"])
            gap = (curr_time - prev_time).total_seconds() / 60.0

            if gap > SESSION_GAP_MINUTES:
                sessions.append(current_session)
                current_session = [group[i]]
            else:
                current_session.append(group[i])
        sessions.append(current_session)  # 最后一个 session

        # 只有一个 session → 全局 reward
        if len(sessions) == 1:
            state = _bkt_replay(group, params)
            rewards[sid] = max(0.0, state.p_mastery - state.p_initial)
            continue

        # 多个 session → 每个 session 的 ΔP(L) 累加
        cumulative = 0.0
        for session in sessions:
            pre_state = params.p_L0  # 该 session 开始前的 P(L)
            if session[0] is not group[0]:
                # 回放 session 之前的所有 attempts 得到 pre-P(L)
                idx = group.index(session[0])
                pre_attempts = group[:idx]
                if pre_attempts:
                    pre_state = _bkt_replay(pre_attempts, params).p_mastery

            post_state = _bkt_replay(
                group[:group.index(session[-1]) + 1], params
            ).p_mastery

            reward = max(0.0, post_state - pre_state)
            cumulative += reward

        rewards[sid] = cumulative
        logger.debug("session_rewards: %s, sessions=%d, cumulative_reward=%.3f",
                     sid, len(sessions), cumulative)

    return rewards


# ── 错因分布 ──

def _get_dominant_error(db: sqlite3.Connection, group: list) -> str:
    """统计该 group 中错误作答的主要错因。"""
    attempt_ids = [a["id"] for a in group if not a["is_correct"]]
    if not attempt_ids:
        return ""

    placeholders = ",".join("?" * len(attempt_ids))
    rows = db.execute(
        f"""SELECT error_type, COUNT(*) as cnt
            FROM attempt_error_labels
            WHERE attempt_id IN ({placeholders})
            GROUP BY error_type
            ORDER BY cnt DESC""",
        attempt_ids,
    ).fetchall()

    if not rows:
        return ""

    # 按频次取最多，平局时按 ERROR_PRIORITY 优先
    max_count = rows[0]["cnt"]
    tied = [r["error_type"] for r in rows if r["cnt"] == max_count]
    if len(tied) == 1:
        return tied[0]

    for etype in ERROR_PRIORITY:
        if etype in tied:
            return etype
    return tied[0]


def _compute_error_distribution(db: sqlite3.Connection,
                                 student_id: str) -> dict[str, int]:
    """该学生全部错因频次分布。"""
    rows = db.execute(
        """SELECT el.error_type, COUNT(*) as cnt
           FROM attempt_error_labels el
           JOIN attempts a ON a.id = el.attempt_id
           WHERE a.student_id = ?
           GROUP BY el.error_type
           ORDER BY cnt DESC""",
        (student_id,),
    ).fetchall()

    return {r["error_type"]: r["cnt"] for r in rows}


# ── 风险信号 ──

def _detect_risk_signals(topics: list[TopicStat],
                          error_dist: dict) -> list[str]:
    signals = []

    for t in topics:
        # 连续错误
        if t.streak_wrong >= 2:
            # 检查是否含高信心错误（需要查 DB，这里先标连续错误）
            signals.append(
                f"{t.section_id} {t.topic}: 连续错误 {t.streak_wrong} 次"
            )

        # 伪掌握：正确率高但 avg_confidence 异常低
        if t.accuracy >= 0.8 and t.avg_confidence < 3:
            signals.append(
                f"{t.section_id} {t.topic}: 正确率高但信心偏低，可能存在猜测"
            )

    # 总体错因倾向
    if error_dist:
        total_errors = sum(error_dist.values())
        top_type = max(error_dist, key=error_dist.get)
        top_ratio = error_dist[top_type] / total_errors if total_errors > 0 else 0
        if top_ratio > 0.5:
            label = ERROR_TYPE_LABELS.get(top_type, top_type)
            signals.append(f"主要错因集中于：{label}（{top_ratio:.0%}）")

    return signals
