"""出题 LangGraph 骨架。"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from .generation import (
    CHOICE_ANSWERS,
    ExamPlan,
    GenerationError,
    GenerationRequest,
    QuestionDraft,
    QuestionTask,
)
from .models import Question, QuestionType


class GenerationState(TypedDict, total=False):
    """出题图状态。"""

    request: GenerationRequest
    messages: Annotated[list[BaseMessage], operator.add]
    plan: ExamPlan
    current_task: QuestionTask
    draft: QuestionDraft
    drafts: Annotated[tuple[QuestionDraft, ...], operator.add]
    questions: tuple[Question, ...]


class ExamGraph:
    """出题图门面。"""

    def __init__(self, content_tools: Sequence[BaseTool]):
        self.graph = build_exam_graph(content_tools)

    def generate(self, request: GenerationRequest) -> tuple[Question, ...]:
        final_state = self.graph.invoke({
            "request": request,
            "messages": [],
            "drafts": (),
        })
        return final_state["questions"]


def build_exam_graph(content_tools: Sequence[BaseTool]):
    """构建出题图：主编计划 -> 单题并发流水线 -> 终审整理。"""
    workflow = StateGraph(GenerationState)
    workflow.add_node("chief_editor", _chief_editor)
    workflow.add_node("content_tools", ToolNode(content_tools))
    workflow.add_node("question_pipeline", _build_question_pipeline())
    workflow.add_node("final_editor", _final_editor)

    workflow.add_edge(START, "chief_editor")
    workflow.add_conditional_edges(
        "chief_editor",
        _route_after_chief_editor,
        ["content_tools", "question_pipeline"],
    )
    workflow.add_edge("content_tools", "chief_editor")
    workflow.add_edge("question_pipeline", "final_editor")
    workflow.add_edge("final_editor", END)
    return workflow.compile()


def _build_question_pipeline():
    pipeline = StateGraph(GenerationState)
    pipeline.add_node("question_writer", _question_writer)
    pipeline.add_node("quality_reviewer", _quality_reviewer)

    pipeline.add_edge(START, "question_writer")
    pipeline.add_edge("question_writer", "quality_reviewer")
    pipeline.add_edge("quality_reviewer", END)
    return pipeline.compile()


def _route_after_chief_editor(state: GenerationState):
    messages = state.get("messages") or []
    if messages and getattr(messages[-1], "tool_calls", None):
        return "content_tools"
    return _fan_out_tasks(state)


def _fan_out_tasks(state: GenerationState):
    plan = state.get("plan")
    request = state.get("request")
    if plan is None:
        raise GenerationError("主编节点没有产出 ExamPlan")
    if request is None:
        raise GenerationError("出题图缺少 GenerationRequest")

    for task in plan.tasks:
        if request.question_types and task.question_type not in request.question_types:
            raise GenerationError(f"任务 {task.task_id} 的题型不在请求范围内")
        if request.difficulty and task.difficulty != request.difficulty:
            raise GenerationError(f"任务 {task.task_id} 的难度不符合请求")

    return [
        Send(
            "question_pipeline",
            {
                "request": request,
                "plan": plan,
                "current_task": task,
            },
        )
        for task in plan.tasks
    ]


def _final_editor(state: GenerationState) -> dict[str, Any]:
    plan = state.get("plan")
    if plan is None:
        raise GenerationError("终审节点缺少 ExamPlan")

    drafts = tuple(state.get("drafts") or ())
    drafts_by_task: dict[str, QuestionDraft] = {}
    for draft in drafts:
        if draft.task_id in drafts_by_task:
            raise GenerationError(f"任务 {draft.task_id} 生成了重复题目")
        drafts_by_task[draft.task_id] = draft

    plan_task_ids = {task.task_id for task in plan.tasks}
    extra_task_ids = set(drafts_by_task) - plan_task_ids
    if extra_task_ids:
        raise GenerationError(f"存在未在计划中的题目任务: {sorted(extra_task_ids)}")

    questions: list[Question] = []
    for index, task in enumerate(plan.tasks, start=1):
        draft = drafts_by_task.get(task.task_id)
        if draft is None:
            raise GenerationError(f"任务 {task.task_id} 没有生成题目")
        if draft.question_type != task.question_type:
            raise GenerationError(f"任务 {task.task_id} 生成题型不一致")
        if draft.difficulty != task.difficulty:
            raise GenerationError(f"任务 {task.task_id} 生成难度不一致")

        question = draft.to_question(f"q-{index:03d}")
        if plan.request.question_types and question.question_type not in plan.request.question_types:
            raise GenerationError(f"题目 {question.id} 的题型不在请求范围内")
        if plan.request.difficulty and question.difficulty != plan.request.difficulty:
            raise GenerationError(f"题目 {question.id} 的难度不符合请求")
        if question.question_type == QuestionType.CHOICE:
            if len(question.options) != 4:
                raise GenerationError(f"选择题 {question.id} 必须有 4 个选项")
            if question.correct_answer.strip().upper() not in CHOICE_ANSWERS:
                raise GenerationError(f"选择题 {question.id} 的答案必须是 A/B/C/D")
        questions.append(question)

    return {"questions": tuple(questions)}


def _chief_editor(state: GenerationState) -> dict[str, Any]:
    raise NotImplementedError("主编节点尚未接入：需要根据请求、教材和画像产出 ExamPlan")


def _question_writer(state: GenerationState) -> dict[str, Any]:
    raise NotImplementedError("单题生成节点尚未接入：需要根据教材上下文产出 QuestionDraft")


def _quality_reviewer(state: GenerationState) -> dict[str, Any]:
    raise NotImplementedError("质检节点尚未接入：通过后需要返回 drafts=(QuestionDraft,)")
