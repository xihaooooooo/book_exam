"""条件路由逻辑：决定节点间如何跳转"""

from exam.agents.utils.agent_states import AgentState


class ConditionalLogic:
    """处理所有条件边路由"""

    def should_continue_chief_editor(self, state: AgentState):
        """主编：有 tool_calls → 继续取数据；无 → 前进"""
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Editor"
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools_editor"
        return "Msg Clear Editor"

    def should_continue_knowledge(self, state: AgentState):
        """知识点提取器：有 tool_calls → 继续取数据；无 → 前进"""
        messages = state.get("messages", [])
        if not messages:
            return "Msg Clear Knowledge"
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools_knowledge"
        return "Msg Clear Knowledge"

    @staticmethod
    def route_by_question_type(state: AgentState):
        """根据 task 的题型路由到对应生成器"""
        task = state.get("current_task", {})
        q_type = task.get("question_type", "choice")
        if q_type == "fill_blank":
            return "fill_blank_generator"
        elif q_type == "short_answer":
            return "short_answer_generator"
        return "choice_generator"
