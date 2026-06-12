"""Pydantic schemas for structured output"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    CHOICE = "choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ChoiceQuestion(BaseModel):
    """选择题"""
    stem: str = Field(description="题干，清晰的问题描述")
    option_a: str = Field(description="选项A")
    option_b: str = Field(description="选项B")
    option_c: str = Field(description="选项C")
    option_d: str = Field(description="选项D")
    correct_answer: str = Field(description="正确选项的字母，如 A、B、C 或 D")
    explanation: str = Field(description="详细解析，说明为什么正确、每个错误选项为什么错")


class FillBlankQuestion(BaseModel):
    """填空题"""
    stem: str = Field(description="题干，用 ___ 表示空缺")
    correct_answer: str = Field(description="唯一正确的答案")
    explanation: str = Field(description="解析说明")


class ShortAnswerQuestion(BaseModel):
    """简答题"""
    stem: str = Field(description="具体明确的问题描述")
    correct_answer: str = Field(description="参考答案，分要点列出")
    explanation: str = Field(description="评分要点和各要点分值")


class QualityReview(BaseModel):
    """质检审核结果"""
    verdict: Literal["pass", "fixed", "rejected"] = Field(description="审核结论：pass通过/fixed已修正/rejected退回")
    issues: str = Field(description="发现的问题（如有）", default="")
    stem: str = Field(description="修正后的题干")
    option_a: Optional[str] = Field(default=None, description="修正后的选项A（选择题）")
    option_b: Optional[str] = Field(default=None, description="修正后的选项B（选择题）")
    option_c: Optional[str] = Field(default=None, description="修正后的选项C（选择题）")
    option_d: Optional[str] = Field(default=None, description="修正后的选项D（选择题）")
    correct_answer: str = Field(description="修正后的正确答案")
    explanation: str = Field(description="修正后的解析")


class ExamPlan(BaseModel):
    """出题计划"""
    tasks: list[dict] = Field(description="出题任务清单")
    difficulty_ratio: tuple[int, int, int] = Field(default=(3, 4, 3), description="易中难比例")
    total_score: int = Field(default=100, description="总分")
