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
