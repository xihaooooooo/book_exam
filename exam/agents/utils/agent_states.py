from typing import Annotated, Any
import operator
from langgraph.graph import MessagesState


def _keep_first(a, b):
    """并发冲突时保留第一个值"""
    return a if a else b


class AgentState(MessagesState):
    # 全局（单节点写入，不需要 reducer）
    pdf_path: str
    toc: list[dict]
    exam_plan: dict | None

    # 出题模式
    mode: str

    # 用户参数
    focus: str
    target_count: int
    allowed_types: str

    # 往年试卷分析报告（--from-analysis 加载）
    analysis_report: dict | None

    # 分支内变量（多分支并发写入，需要 reducer）
    current_task: Annotated[dict | None, _keep_first]
    knowledge_point: Annotated[str, _keep_first]
    generated_question: Annotated[dict | None, _keep_first]
    final_question: Annotated[dict | None, _keep_first]

    # 最终收集（operator.add 做累加）
    all_questions: Annotated[list[dict], operator.add]

    # 最终试卷
    final_exam: str
