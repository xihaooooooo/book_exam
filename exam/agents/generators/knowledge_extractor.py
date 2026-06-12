"""知识点提取器：工具调用循环，读章节文本，提炼结构化知识点"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from exam.agents.utils.agent_utils import (
    create_llm_client,
    get_section_text,
    get_surrounding_context,
    search_keyword,
)


def create_knowledge_extractor(config: dict = None):

    def knowledge_extractor_node(state):
        task = state.get("current_task", {})
        section_id = task.get("section", "")
        topic = task.get("topic", "")
        question_type = task.get("question_type", "choice")
        difficulty = task.get("difficulty", "medium")

        tools = [get_section_text, get_surrounding_context, search_keyword]

        system_message = (
            """你是知识点提取专家。你会收到一道出题任务，包含章节名称、知识点评述、题型、难度。

你的工作：
1. 使用 get_section_text 读取目标章节的完整正文
2. 如果该章节引用了前面的概念，用 get_surrounding_context 获取上下文
3. 如果概念的定义在其他章节，用 search_keyword 搜索

从章节内容中提炼出以下结构化信息：

- **核心概念**：该知识点的准确定义和核心事实
- **关键细节**：步骤、参数、条件、限制等
- **常见误区**：初学者容易混淆或出错的地方
- **关联知识**：与该知识点关联的其他概念（可用于设计干扰项或对比题）

可以多次调用工具直到信息足够。你的输出只是知识点描述，不要接着写题目！"""
        )

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是知识点提取专家。可用的工具：{tool_names}。\n{system_message}"
                "\n\n当前任务：\n"
                "- 目标章节：{section_id}\n"
                "- 知识点评述：{topic}\n"
                "- 目标题型：{question_type}\n"
                "- 目标难度：{difficulty}"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(section_id=section_id)
        prompt = prompt.partial(topic=topic)
        prompt = prompt.partial(question_type=question_type)
        prompt = prompt.partial(difficulty=difficulty)

        llm = create_llm_client(config)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state.get("messages", [])})

        knowledge_point = ""
        if not result.tool_calls:
            knowledge_point = result.content

        return {
            "messages": [result],
            "knowledge_point": knowledge_point,
        }

    return knowledge_extractor_node
