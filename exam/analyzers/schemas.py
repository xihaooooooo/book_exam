"""试卷分析 Pydantic Schemas"""

from typing import Literal

from pydantic import BaseModel, Field


class AnalyzedQuestion(BaseModel):
    """单道分析后的题目"""
    stem: str = Field(description="完整题干文本")
    question_type: Literal["choice", "fill_blank", "short_answer", "code_fill", "comprehensive"] = Field(
        description="题型"
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(description="难度")
    topic: str = Field(description="知识点所属章节或领域，用教材章节名表述")
    knowledge_points: list[str] = Field(description="具体考查的知识点列表")


class SectionAnalysis(BaseModel):
    """一个分组的分析结果（含多道题）"""
    questions: list[AnalyzedQuestion] = Field(description="该分组拆解出的题目列表")
