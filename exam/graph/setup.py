"""LangGraph 图构建：定义节点、边、条件路由 + Send 并发"""

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from exam.agents import (
    create_chief_editor,
    create_knowledge_extractor,
    create_choice_generator,
    create_fill_blank_generator,
    create_short_answer_generator,
    create_code_fill_generator,
    create_comprehensive_generator,
    create_quality_reviewer,
)
from exam.agents.reviewers.final_editor import create_final_editor
from exam.agents.planner.chief_editor import peek_section
from exam.agents.utils.agent_states import AgentState
from exam.agents.utils.agent_utils import (
    get_section_text,
    get_surrounding_context,
    search_keyword,
    create_msg_clear_node,
)

from .conditional_logic import ConditionalLogic
from .strategy import strategy_router


class GraphSetup:
    """负责构建和配置 LangGraph 图"""

    def __init__(self, config: dict = None):
        self.config = config
        self.conditional_logic = ConditionalLogic()

    def setup_graph(self):
        """构建完整图：策略路由 → 主编 → Send 并发流水线 → END"""
        workflow = StateGraph(AgentState)

        # ── 策略路由 ──
        workflow.add_node("strategy_router", strategy_router)

        # ── 主编及其工具 ──
        editor_tools = ToolNode([get_section_text, get_surrounding_context, search_keyword, peek_section])

        workflow.add_node("chief_editor", create_chief_editor(self.config))
        workflow.add_node("tools_editor", editor_tools)
        workflow.add_node("Msg Clear Editor", create_msg_clear_node("主编已完成选题规划。"))

        # ── 生成流水线（子图）──
        generation_subgraph = self._build_generation_subgraph()
        workflow.add_node("generation_pipeline", generation_subgraph)

        # ── 边 ──

        # 起点 → 策略路由 → 主编
        workflow.add_edge(START, "strategy_router")
        workflow.add_edge("strategy_router", "chief_editor")

        # 主编：工具循环
        workflow.add_conditional_edges(
            "chief_editor",
            self.conditional_logic.should_continue_chief_editor,
            {"tools_editor": "tools_editor", "Msg Clear Editor": "Msg Clear Editor"},
        )
        workflow.add_edge("tools_editor", "chief_editor")

        # 主编完成 → fan-out 并发分发
        workflow.add_conditional_edges(
            "Msg Clear Editor",
            self._fan_out_to_pipelines,
            ["generation_pipeline"],
        )

        # 全部流水线完成 → 终审排版
        workflow.add_node("final_editor", create_final_editor(self.config))
        workflow.add_edge("generation_pipeline", "final_editor")
        workflow.add_edge("final_editor", END)

        return workflow

    def _fan_out_to_pipelines(self, state: AgentState):
        """主编完成后，每个任务发一条独立流水线"""
        exam_plan = state.get("exam_plan", {})
        tasks = exam_plan.get("tasks", [])

        if not tasks:
            return []

        # 过滤掉疑似垃圾数据的 task
        valid_tasks = [t for t in tasks if t.get("section", "") not in ("", "------", "-----------")]

        if not valid_tasks:
            return []

        print(f"\n[并发分发] 共 {len(valid_tasks)} 道题的任务，并发执行...")
        return [
            Send("generation_pipeline", {
                "current_task": task,
                "retry_count": 0,
                "review_feedback": "",
            })
            for task in valid_tasks
        ]

    def _build_generation_subgraph(self):
        """构建单题生成流水线子图：知识提取 → 题目生成 → 质检"""
        subgraph = StateGraph(AgentState)

        knowledge_tools = ToolNode([get_section_text, get_surrounding_context, search_keyword])

        # 节点
        subgraph.add_node("knowledge_extractor", create_knowledge_extractor(self.config))
        subgraph.add_node("tools_knowledge", knowledge_tools)
        subgraph.add_node("Msg Clear Knowledge", create_msg_clear_node("知识点提取完成。"))

        subgraph.add_node("choice_generator", create_choice_generator(self.config))
        subgraph.add_node("fill_blank_generator", create_fill_blank_generator(self.config))
        subgraph.add_node("short_answer_generator", create_short_answer_generator(self.config))
        subgraph.add_node("code_fill_generator", create_code_fill_generator(self.config))
        subgraph.add_node("comprehensive_generator", create_comprehensive_generator(self.config))

        subgraph.add_node("quality_reviewer", create_quality_reviewer(self.config))

        # 边：START → 知识点提取
        subgraph.add_edge(START, "knowledge_extractor")

        # 知识点提取：工具循环
        subgraph.add_conditional_edges(
            "knowledge_extractor",
            self.conditional_logic.should_continue_knowledge,
            {"tools_knowledge": "tools_knowledge", "Msg Clear Knowledge": "Msg Clear Knowledge"},
        )
        subgraph.add_edge("tools_knowledge", "knowledge_extractor")

        # 按题型路由
        subgraph.add_conditional_edges(
            "Msg Clear Knowledge",
            ConditionalLogic.route_by_question_type,
            {
                "choice_generator": "choice_generator",
                "fill_blank_generator": "fill_blank_generator",
                "short_answer_generator": "short_answer_generator",
                "code_fill_generator": "code_fill_generator",
                "comprehensive_generator": "comprehensive_generator",
            },
        )

        # 五种生成器 → 质检
        subgraph.add_edge("choice_generator", "quality_reviewer")
        subgraph.add_edge("fill_blank_generator", "quality_reviewer")
        subgraph.add_edge("short_answer_generator", "quality_reviewer")
        subgraph.add_edge("code_fill_generator", "quality_reviewer")
        subgraph.add_edge("comprehensive_generator", "quality_reviewer")

        # 质检 → 按 retry 状态路由：pass/force_pass → END，fail → 回生成器重试
        subgraph.add_conditional_edges(
            "quality_reviewer",
            self.conditional_logic.route_after_review,
            {
                END: END,
                "choice_generator": "choice_generator",
                "fill_blank_generator": "fill_blank_generator",
                "short_answer_generator": "short_answer_generator",
                "code_fill_generator": "code_fill_generator",
                "comprehensive_generator": "comprehensive_generator",
            },
        )

        return subgraph.compile()
