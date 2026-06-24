"""Student Profile 数据结构定义。"""

from dataclasses import dataclass, field, asdict
from typing import Optional

# ── 作答记录 ──

@dataclass
class Attempt:
    """一次作答的完整记录。"""
    student_id: str
    section_id: str = ""
    topic: str = ""
    question_type: str = ""          # choice / fill_blank / short_answer / comprehensive / code_fill
    difficulty: str = ""             # easy / medium / hard
    stem: str = ""
    student_answer: str = ""
    correct_answer: str = ""
    explanation: str = ""
    is_correct: bool = False
    duration_sec: int = 0
    confidence: int = 3              # 1-5 学生自评把握度
    reason: str = ""                 # 判题理由
    method: str = "rule"             # rule / llm / fallback


# ── 错因标签 ──

ERROR_TYPES = [
    "concept_confusion",    # 概念混淆
    "memory_gap",           # 记忆缺失
    "reasoning_error",      # 推理错误
    "misread_question",     # 审题错误
    "careless",             # 粗心失误
    "transfer_failure",     # 迁移失败
]

ERROR_TYPE_LABELS = {
    "concept_confusion": "概念混淆",
    "memory_gap": "记忆缺失",
    "reasoning_error": "推理错误",
    "misread_question": "审题错误",
    "careless": "粗心失误",
    "transfer_failure": "迁移失败",
}

ERROR_PRIORITY = [
    "concept_confusion",
    "reasoning_error",
    "transfer_failure",
    "memory_gap",
    "misread_question",
    "careless",
]


@dataclass
class ErrorLabel:
    """一次错误的错因标签。"""
    attempt_id: int
    error_type: str                  # ERROR_TYPES 之一
    confidence: float = 1.0          # 诊断置信度 0-1
    source: str = "manual"           # student / teacher / manual / llm
    evidence: str = ""               # 诊断证据
    suggestion: str = ""             # 改善建议


# ── 练习计划 ──

@dataclass
class PracticePlan:
    """下一轮练习建议。"""
    student_id: str
    focus_sections: list[str] = field(default_factory=list)
    focus_topics: list[str] = field(default_factory=list)
    question_types: list[str] = field(default_factory=list)
    difficulty: str = "easy_to_medium"
    target_count: int = 8
    reason: str = ""


# ── BKT 模型 ──


@dataclass
class BKTParams:
    """BKT 模型超参数。文献默认值，后续可选 EM 拟合。"""
    p_L0: float = 0.30     # 初始掌握概率 P(L₀)
    p_T: float = 0.15      # 学习转移率 P(T) — 每次答题间的学习概率
    p_G: float = 0.20      # 猜测概率 P(G) — 没掌握但猜对的概率
    p_S: float = 0.10      # 失误概率 P(S) — 掌握了但做错的概率


@dataclass
class BKTState:
    """一个知识点经 BKT 回放后的状态。"""
    section_id: str
    topic: str
    p_mastery: float       # 当前掌握概率 P(L)
    p_initial: float       # 初始 P(L₀)（用于计算全局 ΔP(L) reward）
    total_attempts: int
    correct_count: int
    params: BKTParams = field(default_factory=BKTParams)


# ── Bandit 模型 ──


@dataclass
class BanditState:
    """一个知识点的 Thompson Sampling Beta 分布状态。"""
    section_id: str
    alpha: float = 1.0     # Beta(α, β) — 累积 ΔP(L) reward
    beta: float = 1.0      # Beta(α, β) — 累积 (1-reward)


# ── 推荐引擎输出 ──


# 错因 → 推荐题型映射
ERROR_TYPE_QUESTIONS: dict[str, list[str]] = {
    "concept_confusion":  ["choice", "short_answer"],
    "reasoning_error":    ["short_answer", "comprehensive"],
    "memory_gap":         ["choice", "fill_blank"],
    "transfer_failure":   ["choice", "short_answer", "comprehensive"],
    "misread_question":   ["choice"],
    "careless":           ["choice", "fill_blank"],
}


@dataclass
class RecommendationItem:
    """推荐计划中的一个条目（一个知识点）。"""
    section_id: str
    topic: str
    p_mastery: float           # BKT P(L)
    bandit_score: float        # Thompson 采样值
    difficulty: str            # 推荐起始难度
    question_types: list[str]  # 推荐题型
    recommended_count: int     # 建议题数
    dominant_error_type: str = ""


@dataclass
class RecommendationPlan:
    """完整的推荐计划，chief_editor 据此出题。"""
    student_id: str
    items: list[RecommendationItem] = field(default_factory=list)
    target_count: int = 0
    reason: str = ""

    def to_prompt_table(self) -> str:
        """格式化为 chief_editor prompt 中的 Markdown 表格。

        将格式化逻辑收归在数据结构自身，避免散落在各消费者中。
        """
        if not self.items:
            return "（无推荐计划数据）"

        header = (
            "| 优先级 | 章节 | 掌握度 P(L) | 主要错因 | 建议题型 | 建议难度 | 建议题数 |\n"
            "|--------|------|------------|----------|----------|----------|----------|"
        )
        rows = [header]
        for i, item in enumerate(self.items, 1):
            err = item.dominant_error_type or "-"
            types = ",".join(item.question_types)
            rows.append(
                f"| {i} | {item.section_id} {item.topic} | {item.p_mastery:.0%} | "
                f"{err} | {types} | {item.difficulty} | {item.recommended_count} |"
            )
        return "\n".join(rows)
