import unittest
from unittest import mock

from book_exam.agent import (
    AgentRunError,
    AgentTool,
    AssistantMessage,
    Context,
    TextContent,
    ToolCall,
    ToolDefinition,
    ToolExecutionResult,
    ToolResultMessage,
    UserMessage,
    run_agent_loop,
)

SECTION_TEXT = "3.2 死锁预防：破坏请求与保持条件、不可剥夺条件……"

SECTION_PARAMETERS = {
    "type": "object",
    "properties": {"section_id": {"type": "string"}},
    "required": ["section_id"],
    "additionalProperties": False,
}


def section_definition() -> ToolDefinition:
    return ToolDefinition(
        name="get_section_text",
        description="读取指定小节的完整原文。",
        parameters=SECTION_PARAMETERS,
    )


def assistant_message(
    content,
    stop_reason: str,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="deepseek",
        model="deepseek-flash",
        stop_reason=stop_reason,
    )


def requested_contexts(complete: mock.AsyncMock) -> list[Context]:
    return [call.args[0] for call in complete.await_args_list]


class RunAgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_a_tool_then_returns_the_follow_up_summary(
        self,
    ) -> None:
        handler = mock.AsyncMock(
            return_value=ToolExecutionResult(
                content=(TextContent(text=SECTION_TEXT),),
                details={"section_id": "3.2"},
            )
        )
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        first_reply = assistant_message(
            (
                ToolCall(
                    id="call-1",
                    name="get_section_text",
                    arguments={"section_id": "3.2"},
                ),
            ),
            "toolUse",
        )
        final_reply = assistant_message(
            (TextContent(text="3.2 节介绍死锁预防的四种条件。"),),
            "stop",
        )
        complete = mock.AsyncMock(side_effect=(first_reply, final_reply))

        run = await run_agent_loop(
            (initial,),
            system_prompt="你是教材助教。",
            tools=(tool,),
            complete=complete,
        )

        handler.assert_awaited_once_with({"section_id": "3.2"})
        self.assertEqual(complete.await_count, 2)

        first_context, second_context = requested_contexts(complete)
        self.assertEqual(first_context.messages, (initial,))

        self.assertEqual(len(second_context.messages), 3)
        self.assertIs(second_context.messages[0], initial)
        self.assertIs(second_context.messages[1], first_reply)

        tool_result = second_context.messages[2]
        self.assertIsInstance(tool_result, ToolResultMessage)
        self.assertIs(tool_result, run.messages[1])
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertEqual(tool_result.tool_name, "get_section_text")
        self.assertIs(tool_result.is_error, False)
        self.assertEqual(
            tool_result.content,
            (TextContent(text=SECTION_TEXT),),
        )

        for context in (first_context, second_context):
            self.assertEqual(context.tools, (tool.definition,))
            self.assertEqual(context.system_prompt, "你是教材助教。")

        self.assertIsNone(run.status)
        self.assertEqual(run.rounds, 2)
        self.assertEqual(len(run.messages), 3)
        self.assertIs(run.messages[0], first_reply)
        self.assertIs(run.messages[2], final_reply)

    async def test_returns_a_failed_result_for_unparseable_arguments(
        self,
    ) -> None:
        handler = mock.AsyncMock(
            return_value=ToolExecutionResult(
                content=(TextContent(text="不应执行"),),
            )
        )
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        parse_error = 'Expecting "," delimiter: line 1 column 26 (char 25)'
        first_reply = assistant_message(
            (
                ToolCall(
                    id="call-2",
                    name="get_section_text",
                    arguments={},
                    arguments_error=parse_error,
                ),
            ),
            "toolUse",
        )
        final_reply = assistant_message(
            (TextContent(text="参数无法解析，无法读取该节。"),),
            "stop",
        )
        complete = mock.AsyncMock(side_effect=(first_reply, final_reply))

        run = await run_agent_loop(
            (initial,),
            tools=(tool,),
            complete=complete,
        )

        handler.assert_not_awaited()
        self.assertEqual(complete.await_count, 2)

        second_context = requested_contexts(complete)[1]
        self.assertEqual(len(second_context.messages), 3)
        self.assertIs(second_context.messages[0], initial)
        self.assertIs(second_context.messages[1], first_reply)

        tool_result = second_context.messages[2]
        self.assertIsInstance(tool_result, ToolResultMessage)
        self.assertIs(tool_result, run.messages[1])
        self.assertEqual(tool_result.tool_call_id, "call-2")
        self.assertEqual(tool_result.tool_name, "get_section_text")
        self.assertIs(tool_result.is_error, True)
        self.assertEqual(len(tool_result.content), 1)
        self.assertIn(parse_error, tool_result.content[0].text)

        self.assertIsNone(run.status)
        self.assertEqual(run.rounds, 2)
        self.assertEqual(len(run.messages), 3)
        self.assertIs(run.messages[0], first_reply)
        self.assertIs(run.messages[2], final_reply)

    async def test_skips_tool_calls_on_the_last_allowed_round(self) -> None:
        handler = mock.AsyncMock(
            return_value=ToolExecutionResult(
                content=(TextContent(text=SECTION_TEXT),),
            )
        )
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        replies = tuple(
            assistant_message(
                (
                    ToolCall(
                        id=f"call-{index}",
                        name="get_section_text",
                        arguments={"section_id": "3.2"},
                    ),
                ),
                "toolUse",
            )
            for index in range(1, 3)
        )
        complete = mock.AsyncMock(side_effect=replies)

        run = await run_agent_loop(
            (initial,),
            tools=(tool,),
            complete=complete,
            max_rounds=2,
        )

        self.assertEqual(complete.await_count, 2)
        handler.assert_awaited_once_with({"section_id": "3.2"})

        self.assertEqual(run.status, "limitReached")
        self.assertEqual(run.rounds, 2)
        self.assertEqual(len(run.messages), 3)

        self.assertIs(run.messages[0], replies[0])
        tool_result = run.messages[1]
        self.assertIsInstance(tool_result, ToolResultMessage)
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertIs(run.messages[2], replies[1])

    async def test_rejects_a_pending_reply_before_it_is_recorded(self) -> None:
        handler = mock.AsyncMock()
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        incomplete_reply = assistant_message(
            (TextContent(text="3.2 节介绍死锁"),),
            "pending",
        )
        complete = mock.AsyncMock(return_value=incomplete_reply)

        with self.assertRaises(AgentRunError) as caught:
            await run_agent_loop(
                (initial,),
                tools=(tool,),
                complete=complete,
            )

        self.assertIn("pending", str(caught.exception))
        self.assertEqual(complete.await_count, 1)
        handler.assert_not_awaited()

    async def test_rejects_a_tool_use_reply_without_tool_calls(self) -> None:
        handler = mock.AsyncMock()
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        malformed_reply = assistant_message(
            (TextContent(text="我先读一下 3.2 节。"),),
            "toolUse",
        )
        complete = mock.AsyncMock(return_value=malformed_reply)

        with self.assertRaises(AgentRunError) as caught:
            await run_agent_loop(
                (initial,),
                tools=(tool,),
                complete=complete,
            )

        self.assertIn("toolUse", str(caught.exception))
        self.assertIn("tool call", str(caught.exception))
        self.assertEqual(complete.await_count, 1)
        handler.assert_not_awaited()

    async def test_rejects_empty_tool_use_on_the_last_round(self) -> None:
        handler = mock.AsyncMock()
        tool = AgentTool(definition=section_definition(), handler=handler)
        initial = UserMessage(
            content=(TextContent(text="读取教材 3.2 节并总结。"),)
        )
        malformed_reply = assistant_message(
            (TextContent(text="我先读一下 3.2 节。"),),
            "toolUse",
        )
        complete = mock.AsyncMock(return_value=malformed_reply)

        with self.assertRaises(AgentRunError) as caught:
            await run_agent_loop(
                (initial,),
                tools=(tool,),
                complete=complete,
                max_rounds=1,
            )

        self.assertIn("toolUse", str(caught.exception))
        self.assertEqual(complete.await_count, 1)
        handler.assert_not_awaited()

    async def test_rejects_invalid_max_rounds(self) -> None:
        initial = UserMessage(content=(TextContent(text="读取教材。"),))
        complete = mock.AsyncMock()

        for value in (0, -1, True, 1.5, "8"):
            with self.subTest(max_rounds=value):
                with self.assertRaises(ValueError):
                    await run_agent_loop(
                        (initial,),
                        complete=complete,
                        max_rounds=value,
                    )

        complete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
