"""学生画像聚合引擎。从 attempts + error_labels 实时聚合。

用法：
    from exam.student_profile.profile_engine import build_profile
    profile = build_profile("S001", "cache/attempts.db")
    print(profile.weakest_topics)
"""

import sqlite3
from dataclasses import dataclass, field
from exam.student_profile.schemas import ERROR_TYPES, ERROR_TYPE_LABELS, ERROR_PRIORITY


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


# ── 主入口 ──

def build_profile(student_id: str, db_path: str) -> StudentProfile:
    """从数据库聚合学生画像。"""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # 所有作答（按时间排序）
    attempts = db.execute(
        """SELECT * FROM attempts
           WHERE student_id = ? AND section_id != ''
           ORDER BY created_at""",
        (student_id,),
    ).fetchall()

    if not attempts:
        db.close()
        return StudentProfile(student_id=student_id)

    # 全局统计
    total = len(attempts)
    correct_count = sum(1 for a in attempts if a["is_correct"])
    overall_accuracy = correct_count / total if total > 0 else 0.0

    # 按 (section_id, topic) 分组
    groups: dict[tuple[str, str], list] = {}
    for a in attempts:
        key = (a["section_id"], a["topic"] or "")
        if key not in groups:
            groups[key] = []
        groups[key].append(a)

    # 聚合每个 topic
    topics = []
    for (sid, topic), group in groups.items():
        stat = _compute_topic_stat(sid, topic, group, all_attempts=attempts)
        stat.dominant_error_type = _get_dominant_error(db, group)
        stat.mastery_level = _compute_mastery(stat)
        topics.append(stat)

    # 错因分布
    error_dist = _compute_error_distribution(db, student_id)

    # 风险信号
    risks = _detect_risk_signals(topics, error_dist)

    db.close()
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
