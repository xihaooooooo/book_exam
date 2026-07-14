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

from .chief_editor import (
    ExamPlan,
    GenerationRequest,
    QuestionTask,
    build_exam_plan,
    create_chief_editor_plan_messages,
    create_chief_editor_research_messages,
    parse_chief_editor_plan,
)
from .errors import GenerationError
from .generation import CHOICE_ANSWERS, QuestionDraft
from .models import Question, QuestionType


class GenerationState(TypedDict, total=False):
    """出题图状态。"""

    request: GenerationRequest
    messages: Annotated[list[BaseMessage], operator.add]
    research_summary: str
    plan: ExamPlan
    current_task: QuestionTask
    draft: QuestionDraft
    drafts: Annotated[tuple[QuestionDraft, ...], operator.add]
    questions: tuple[Question, ...]


class ExamGraph:
    """出题图门面。"""

    def __init__(self, chief_editor_model: Any, content_tools: Sequence[BaseTool]):
        self.graph = build_exam_graph(chief_editor_model, content_tools)

    def generate(self, request: GenerationRequest) -> tuple[Question, ...]:
        final_state = self.graph.invoke({
            "request": request,
            "messages": [],
            "drafts": (),
        })
        return final_state["questions"]


def build_exam_graph(chief_editor_model: Any, content_tools: Sequence[BaseTool]):
    """构建出题图：主编计划 -> 单题并发流水线 -> 终审整理。"""
    chief_editor_research_model = chief_editor_model.bind_tools(list(content_tools))

    workflow = StateGraph(GenerationState)
    workflow.add_node(
        "chief_editor_research",
        lambda state: _chief_editor_research(state, chief_editor_research_model),
    )
    workflow.add_node("content_tools", ToolNode(content_tools))
    workflow.add_node(
        "chief_editor_plan",
        lambda state: _chief_editor_plan(state, chief_editor_model),
    )
    workflow.add_node("question_pipeline", _build_question_pipeline())
    workflow.add_node("final_editor", _final_editor)

    workflow.add_edge(START, "chief_editor_research")
    workflow.add_conditional_edges(
        "chief_editor_research",
        _route_after_chief_editor_research,
        ["content_tools", "chief_editor_plan"],
    )
    workflow.add_edge("content_tools", "chief_editor_research")
    workflow.add_conditional_edges(
        "chief_editor_plan",
        _fan_out_tasks,
        ["question_pipeline"],
    )
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


def _route_after_chief_editor_research(state: GenerationState):
    messages = state.get("messages") or []
    if messages and getattr(messages[-1], "tool_calls", None):
        return "content_tools"
    return "chief_editor_plan"


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

    accepted: list[tuple[QuestionTask, QuestionDraft]] = []
    for task in plan.tasks:
        draft = drafts_by_task.get(task.task_id)
        if draft is None:
            raise GenerationError(f"任务 {task.task_id} 没有生成题目")
        if draft.question_type != task.question_type:
            raise GenerationError(f"任务 {task.task_id} 生成题型不一致")
        if draft.difficulty != task.difficulty:
            raise GenerationError(f"任务 {task.task_id} 生成难度不一致")
        accepted.append((task, draft))

    questions: list[Question] = []
    for index, (task, draft) in enumerate(_sort_final_drafts(accepted), start=1):
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


def _sort_final_drafts(items: list[tuple[QuestionTask, QuestionDraft]]) -> list[tuple[QuestionTask, QuestionDraft]]:
    difficulty_order = {
        "easy": 0,
        "medium": 1,
        "hard": 2,
    }
    return sorted(
        items,
        key=lambda item: (
            item[0].source,
            difficulty_order.get(item[0].difficulty.value, 99),
            item[0].task_id,
        ),
    )


def _chief_editor_research(state: GenerationState, chief_editor_model: Any) -> dict[str, Any]:
    request = state.get("request")
    if request is None:
        raise GenerationError("主编节点缺少 GenerationRequest")

    messages = state.get("messages") or create_chief_editor_research_messages(request)
    response = chief_editor_model.invoke(messages)
    if state.get("messages"):
        result = {"messages": [response]}
    else:
        result = {"messages": [*messages, response]}
    if not getattr(response, "tool_calls", None):
        result["research_summary"] = _message_content(response)
    return result


def _message_content(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _chief_editor_plan(state: GenerationState, chief_editor_model: Any) -> dict[str, Any]:
    request = state.get("request")
    if request is None:
        raise GenerationError("主编计划节点缺少 GenerationRequest")
    research_summary = state.get("research_summary", "").strip()
    if not research_summary:
        raise GenerationError("主编计划节点缺少 research_summary")

    response = chief_editor_model.invoke(create_chief_editor_plan_messages(request, research_summary))
    chief_plan = parse_chief_editor_plan(_message_content(response))
    return {"plan": build_exam_plan(request, chief_plan)}


def _question_writer(state: GenerationState) -> dict[str, Any]:
    task = state.get("current_task")
    if task is None:
        raise GenerationError("单题生成节点缺少 QuestionTask")
    if task.question_type != QuestionType.CHOICE:
        raise GenerationError(f"临时单题生成器只支持 choice: {task.question_type.value}")

    draft = QuestionDraft(
        task_id=task.task_id,
        question_type=task.question_type,
        stem=f"以下关于“{task.topic}”的说法，哪一项是正确的？",
        options=(
            "A. 这是与该知识点直接相关的正确表述",
            "B. 这是一个常见混淆项",
            "C. 这是一个无关概念",
            "D. 这是一个过度泛化的说法",
        ),
        correct_answer="A",
        source=task.source,
        topic=task.topic,
        difficulty=task.difficulty,
        explanation=f"本题根据 {task.source} 的“{task.topic}”生成，当前为临时模板题。",
    )
    return {"draft": draft}


def _quality_reviewer(state: GenerationState) -> dict[str, Any]:
    draft = state.get("draft")
    if draft is None:
        raise GenerationError("质检节点缺少 QuestionDraft")
    _review_question_draft(draft)
    return {"drafts": (draft,)}


def _review_question_draft(draft: QuestionDraft) -> None:
    if not draft.task_id.strip():
        raise GenerationError("题目草稿缺少 task_id")
    if not draft.stem.strip():
        raise GenerationError(f"任务 {draft.task_id} 的题干为空")
    if not draft.source.strip():
        raise GenerationError(f"任务 {draft.task_id} 的 source 为空")
    if not draft.topic.strip():
        raise GenerationError(f"任务 {draft.task_id} 的 topic 为空")
    if draft.question_type == QuestionType.CHOICE:
        if len(draft.options) != 4:
            raise GenerationError(f"任务 {draft.task_id} 的选择题必须有 4 个选项")
        if draft.correct_answer.strip().upper() not in CHOICE_ANSWERS:
            raise GenerationError(f"任务 {draft.task_id} 的选择题答案必须是 A/B/C/D")
