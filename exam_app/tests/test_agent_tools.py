import asyncio
import unittest
from unittest import mock

from book_exam.agent import (
    AgentTool,
    TextContent,
    ThinkingContent,
    ToolDefinition,
    ToolError,
    ToolExecutionResult,
)

SECTION_ID_PARAMETERS = {
    "type": "object",
    "properties": {"section_id": {"type": "string"}},
    "required": ["section_id"],
    "additionalProperties": False,
}


def section_definition(name: str = "read_section") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Read one section.",
        parameters=SECTION_ID_PARAMETERS,
    )


class ToolDefinitionTests(unittest.TestCase):
    def test_rejects_non_object_root_schema(self) -> None:
        with self.assertRaises(ToolError):
            ToolDefinition(
                name="read_section",
                description="Read one section.",
                parameters={"type": "array"},
            )

    def test_rejects_invalid_json_schema(self) -> None:
        with self.assertRaises(ToolError):
            ToolDefinition(
                name="read_section",
                description="Read one section.",
                parameters={"type": "object", "required": "section_id"},
            )

    def test_rejects_blank_name_or_description(self) -> None:
        for name, description in (("", "Read one section."), ("  ", "desc"), ("x", "  ")):
            with self.subTest(name=name, description=description):
                with self.assertRaises(ToolError):
                    ToolDefinition(
                        name=name,
                        description=description,
                        parameters=SECTION_ID_PARAMETERS,
                    )


class ToolExecutionResultTests(unittest.TestCase):
    def test_normalizes_content_and_keeps_details(self) -> None:
        text = TextContent(text="ok")
        details = {"value": 1}

        result = ToolExecutionResult(
            content=[text],
            details=details,
            is_error=False,
        )

        self.assertIsInstance(result.content, tuple)
        self.assertEqual(result.content, (text,))
        self.assertIs(result.details, details)
        self.assertIs(result.is_error, False)

    def test_rejects_blocks_other_than_text_or_image(self) -> None:
        with self.assertRaises(ToolError):
            ToolExecutionResult(content=[ThinkingContent(thinking="hidden")])

    def test_rejects_non_boolean_error_flag(self) -> None:
        with self.assertRaises(ToolError):
            ToolExecutionResult(content=[], is_error=1)


class AgentToolTests(unittest.IsolatedAsyncioTestCase):
    def _tool(self, handler, name: str = "read_section") -> AgentTool:
        return AgentTool(definition=section_definition(name), handler=handler)

    def test_rejects_invalid_definition_or_handler(self) -> None:
        async def handler(arguments):
            return ToolExecutionResult(content=[])

        with self.assertRaises(ToolError):
            AgentTool(definition={"name": "read_section"}, handler=handler)
        with self.assertRaises(ToolError):
            AgentTool(definition=section_definition(), handler=None)

    async def test_invalid_arguments_do_not_call_the_handler(self) -> None:
        handler = mock.AsyncMock(return_value=ToolExecutionResult(content=[]))
        tool = self._tool(handler)

        for arguments in (
            {},
            {"section_id": 123},
            {"section_id": "1.1", "extra": True},
            {"section_id": None},
            "section_id=1.1",
            None,
        ):
            with self.subTest(arguments=arguments):
                result = await tool.execute(arguments)

                self.assertIs(result.is_error, True)
                self.assertEqual(len(result.content), 1)
                self.assertIsInstance(result.content[0], TextContent)
                self.assertIn("invalid", result.content[0].text)

        handler.assert_not_awaited()

    async def test_passes_valid_arguments_and_returns_handler_result(self) -> None:
        expected = ToolExecutionResult(
            content=[TextContent(text="ok")],
            details={"value": 1},
        )
        handler = mock.AsyncMock(return_value=expected)
        tool = self._tool(handler)
        arguments = {"section_id": "1.1"}

        result = await tool.execute(arguments)

        self.assertIs(result, expected)
        handler.assert_awaited_once_with(arguments)

    async def test_wraps_handler_exceptions_as_failed_results(self) -> None:
        async def handler(arguments):
            raise RuntimeError("read failed")

        result = await self._tool(handler).execute({"section_id": "1.1"})

        self.assertIs(result.is_error, True)
        self.assertIn("read failed", result.content[0].text)

    async def test_propagates_cancellation(self) -> None:
        started = asyncio.Event()

        async def handler(arguments):
            started.set()
            await asyncio.sleep(3600)

        tool = self._tool(handler)
        task = asyncio.create_task(tool.execute({"section_id": "1.1"}))
        await asyncio.wait_for(started.wait(), timeout=1)

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
