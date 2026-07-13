"""Pydantic schemas for structured output"""

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    CHOICE = "choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    CODE_FILL = "code_fill"
    COMPREHENSIVE = "comprehensive"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class MediaItem(BaseModel):
    """题目关联的非文本媒体资产。LaTeX 不放入 media。"""
    id: str = Field(description="题内媒体引用 ID，例如 fig1")
    type: Literal["image", "mermaid", "canvas"] = Field(description="媒体类型：图片、Mermaid 图或交互 Canvas")
    src: Optional[str] = Field(default=None, description="图片等外部资产路径")
    content: Optional[str] = Field(default=None, description="Mermaid 源码等内联内容，图片题通常留空")
    description: Optional[str] = Field(default=None, description="供检索和判题使用的文字描述")
    config: Optional[dict[str, Any]] = Field(default=None, description="Canvas 只读预览或交互配置")
    expected_answer: Optional[dict[str, Any]] = Field(default=None, description="Canvas 交互题的结构化标准答案")
    judge_schema: Optional[dict[str, Any]] = Field(default=None, description="Canvas 交互题的结构化判题规则")


class ChoiceQuestion(BaseModel):
    """选择题"""
    stem: str = Field(description="题干，清晰的问题描述")
    option_a: str = Field(description="选项A")
    option_b: str = Field(description="选项B")
    option_c: str = Field(description="选项C")
    option_d: str = Field(description="选项D")
    correct_answer: str = Field(description="正确选项的字母，如 A、B、C 或 D")
    explanation: str = Field(description="详细解析，说明为什么正确、每个错误选项为什么错")
    media: Optional[list[MediaItem]] = Field(default=None, description="非文本媒体资产，纯文本题留空")


class FillBlankQuestion(BaseModel):
    """填空题"""
    stem: str = Field(description="题干，用 ___ 表示空缺")
    correct_answer: str = Field(description="唯一正确的答案")
    explanation: str = Field(description="解析说明")
    media: Optional[list[MediaItem]] = Field(default=None, description="非文本媒体资产，纯文本题留空")


class ShortAnswerQuestion(BaseModel):
    """简答题"""
    stem: str = Field(description="具体明确的问题描述")
    correct_answer: str = Field(description="参考答案，分要点列出")
    explanation: str = Field(description="评分要点和各要点分值")
    media: Optional[list[MediaItem]] = Field(default=None, description="非文本媒体资产，纯文本题留空")


class CodeFillQuestion(BaseModel):
    """代码填空题"""
    stem: str = Field(description="题干，含代码上下文，用 ___ 表示空缺")
    correct_answer: str = Field(description="空缺处应填入的代码")
    explanation: str = Field(description="解析，说明该段代码的逻辑和考点")
    media: Optional[list[MediaItem]] = Field(default=None, description="非文本媒体资产，纯文本题留空")


class ComprehensiveQuestion(BaseModel):
    """综合题"""
    stem: str = Field(description="完整题目描述，可能含代码、图表等")
    correct_answer: str = Field(description="参考答案，分要点列出")
    explanation: str = Field(description="评分要点和各要点分值")
    media: Optional[list[MediaItem]] = Field(default=None, description="非文本媒体资产，纯文本题留空")


class QualityReview(BaseModel):
    """质检审核结果（两档制：pass/fail）"""
    verdict: Literal["pass", "fail"] = Field(description="审核结论：pass通过/fail退回")
    issues: str = Field(description="发现的问题（fail 时必填）", default="")


class ExamPlan(BaseModel):
    """出题计划"""
    tasks: list[dict] = Field(description="出题任务清单")
    difficulty_ratio: tuple[int, int, int] = Field(default=(3, 4, 3), description="易中难比例")
    total_score: int = Field(default=100, description="总分")
