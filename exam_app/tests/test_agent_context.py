import unittest

from book_exam.agent import (
    AgentTool,
    AssistantMessage,
    Context,
    ContextError,
    TextContent,
    ToolDefinition,
    ToolExecutionResult,
    ToolResultMessage,
    UserMessage,
)


async def _handler(arguments):
    return ToolExecutionResult(content=[])


def _definition(name: str = "read_section") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Read one section.",
        parameters={
            "type": "object",
            "properties": {"section_id": {"type": "string"}},
            "required": ["section_id"],
            "additionalProperties": False,
        },
    )


USER_MESSAGE = UserMessage(content=[TextContent(text="hello")])
ASSISTANT_MESSAGE = AssistantMessage(
    content=[TextContent(text="ok")],
    api="openai-completions",
    provider="openai",
    model="test-model",
    stop_reason="stop",
)
TOOL_RESULT_MESSAGE = ToolResultMessage(
    tool_call_id="call-1",
    tool_name="read_section",
    content=[TextContent(text="result")],
    is_error=False,
)
PENDING_MESSAGE = AssistantMessage(
    content=[TextContent(text="partial")],
    api="openai-completions",
    provider="openai",
    model="test-model",
    stop_reason="pending",
)


class ContextTests(unittest.TestCase):
    def test_normalizes_sequences_and_ignores_later_mutations(self) -> None:
        messages = [USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT_MESSAGE]
        tools = [_definition()]

        context = Context(
            messages=messages,
            system_prompt="You are a helpful assistant.",
            tools=tools,
        )

        self.assertIsInstance(context.messages, tuple)
        self.assertIsInstance(context.tools, tuple)
        self.assertEqual(
            context.messages,
            (USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT_MESSAGE),
        )
        self.assertEqual(context.tools, (tools[0],))
        self.assertEqual(
            context.system_prompt, "You are a helpful assistant."
        )

        messages.clear()
        tools.clear()

        self.assertEqual(len(context.messages), 3)
        self.assertEqual(len(context.tools), 1)

    def test_defaults_system_prompt_and_tools(self) -> None:
        context = Context(messages=[])

        self.assertIsNone(context.system_prompt)
        self.assertEqual(context.tools, ())

    def test_rejects_values_that_are_not_messages(self) -> None:
        for value in ({"role": "user"}, "hello", None):
            with self.subTest(value=value):
                with self.assertRaises(ContextError):
                    Context(messages=[value])

    def test_rejects_pending_assistant_messages(self) -> None:
        with self.assertRaises(ContextError):
            Context(messages=[PENDING_MESSAGE])

    def test_rejects_duplicate_tool_names(self) -> None:
        first = _definition("read_section")
        second = _definition("read_section")

        with self.assertRaises(ContextError):
            Context(messages=[], tools=[first, second])

    def test_rejects_agent_tools_and_functions_as_tool_definitions(self) -> None:
        agent_tool = AgentTool(definition=_definition(), handler=_handler)

        for tool in (agent_tool, lambda arguments: None):
            with self.subTest(tool=type(tool).__name__):
                with self.assertRaises(ContextError):
                    Context(messages=[], tools=[tool])

    def test_uses_the_existing_agent_tool_definition(self) -> None:
        agent_tool = AgentTool(definition=_definition(), handler=_handler)

        context = Context(messages=[], tools=[agent_tool.definition])

        self.assertEqual(len(context.tools), 1)
        self.assertIs(context.tools[0], agent_tool.definition)


if __name__ == "__main__":
    unittest.main()
