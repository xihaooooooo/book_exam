import json
import unittest

from book_exam.agent import (
    AssistantMessage,
    Context,
    DeepSeekRequestError,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
    build_deepseek_request,
)


def _assistant(content, stop_reason="stop"):
    return AssistantMessage(
        content=content,
        api="deepseek",
        provider="deepseek",
        model="deepseek-flash",
        stop_reason=stop_reason,
    )


def _tool_result(
    call_id,
    content,
    tool_name="lookup",
    is_error=False,
    details=None,
):
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name=tool_name,
        content=content,
        is_error=is_error,
        details=details,
    )


def _definition(name="lookup"):
    return ToolDefinition(
        name=name,
        description="Look up one section.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "options": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )


class RequestShapeTests(unittest.TestCase):
    def test_builds_fixed_settings_and_system_message(self) -> None:
        user = UserMessage(content=[TextContent(text="问题")])
        assistant = _assistant([TextContent(text="回答")])
        context = Context(
            messages=[user, assistant],
            system_prompt="系统提示",
        )

        request = build_deepseek_request(context)

        self.assertEqual(
            set(request),
            {"model", "messages", "stream", "thinking"},
        )
        self.assertEqual(request["model"], "deepseek-flash")
        self.assertIs(request["stream"], False)
        self.assertEqual(request["thinking"], {"type": "enabled"})
        self.assertEqual(
            request["messages"],
            [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "问题"},
                {
                    "role": "assistant",
                    "content": "回答",
                    "reasoning_content": "",
                },
            ],
        )
        self.assertEqual(context.messages, (user, assistant))

    def test_omits_system_message_and_tools_when_absent(self) -> None:
        request = build_deepseek_request(
            Context(messages=[UserMessage(content=[TextContent(text="嗨")])])
        )

        self.assertEqual(
            request["messages"],
            [{"role": "user", "content": "嗨"}],
        )
        self.assertNotIn("tools", request)

    def test_allows_system_prompt_without_history(self) -> None:
        request = build_deepseek_request(Context(messages=[], system_prompt=""))

        self.assertEqual(
            request["messages"],
            [{"role": "system", "content": ""}],
        )

    def test_rejects_empty_request_messages(self) -> None:
        with self.assertRaises(DeepSeekRequestError):
            build_deepseek_request(Context(messages=[]))


class MessageConversionTests(unittest.TestCase):
    def test_preserves_history_order_and_empty_text(self) -> None:
        context = Context(
            messages=[
                UserMessage(content=[]),
                _assistant([]),
                UserMessage(
                    content=[
                        TextContent(text="甲"),
                        TextContent(text="乙  "),
                    ]
                ),
            ]
        )

        messages = build_deepseek_request(context)["messages"]

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user"],
        )
        self.assertEqual(messages[0]["content"], "")
        self.assertEqual(messages[1]["content"], "")
        self.assertEqual(messages[1]["reasoning_content"], "")
        self.assertEqual(messages[2]["content"], "甲乙  ")

    def test_carries_thinking_as_separate_reasoning_content(self) -> None:
        context = Context(
            messages=[
                UserMessage(content=[TextContent(text="第一问")]),
                _assistant(
                    [
                        ThinkingContent(thinking="先分析"),
                        ThinkingContent(thinking="再检查"),
                        TextContent(text="第一答"),
                    ]
                ),
                UserMessage(content=[TextContent(text="第二问")]),
                _assistant([TextContent(text="第二答")]),
            ]
        )

        assistant_messages = [
            message
            for message in build_deepseek_request(context)["messages"]
            if message["role"] == "assistant"
        ]

        self.assertEqual(len(assistant_messages), 2)
        self.assertEqual(assistant_messages[0]["content"], "第一答")
        self.assertEqual(
            assistant_messages[0]["reasoning_content"],
            "先分析再检查",
        )
        self.assertEqual(assistant_messages[1]["content"], "第二答")
        self.assertEqual(assistant_messages[1]["reasoning_content"], "")

    def test_uses_none_for_empty_text_with_tool_calls(self) -> None:
        call = ToolCall(id="call-1", name="lookup", arguments={})
        context = Context(
            messages=[
                _assistant(
                    [
                        TextContent(text=""),
                        TextContent(text=""),
                        call,
                    ],
                    stop_reason="toolUse",
                ),
                _tool_result("call-1", []),
            ]
        )

        assistant = build_deepseek_request(context)["messages"][0]

        self.assertIsNone(assistant["content"])
        self.assertEqual(len(assistant["tool_calls"]), 1)

    def test_keeps_empty_string_for_empty_text_without_tool_calls(
        self,
    ) -> None:
        context = Context(
            messages=[_assistant([TextContent(text="")])]
        )

        assistant = build_deepseek_request(context)["messages"][0]

        self.assertEqual(assistant["content"], "")
        self.assertNotIn("tool_calls", assistant)

    def test_preserves_whitespace_text_with_tool_calls(self) -> None:
        call = ToolCall(id="call-1", name="lookup", arguments={})
        context = Context(
            messages=[
                _assistant(
                    [
                        TextContent(text="  "),
                        TextContent(text="\n\t"),
                        call,
                    ],
                    stop_reason="toolUse",
                ),
                _tool_result("call-1", []),
            ]
        )

        assistant = build_deepseek_request(context)["messages"][0]

        self.assertEqual(assistant["content"], "  \n\t")
        self.assertEqual(len(assistant["tool_calls"]), 1)


class ToolConversionTests(unittest.TestCase):
    def test_converts_multiple_tool_calls_and_pairs_results(self) -> None:
        first = ToolCall(
            id="call-1",
            name="lookup",
            arguments={"query": "上海", "limit": 3},
        )
        second = ToolCall(
            id="call-2",
            name="lookup",
            arguments={"query": "北京"},
        )
        context = Context(
            messages=[
                UserMessage(content=[TextContent(text="查两处")]),
                _assistant(
                    [
                        TextContent(text="开始查询"),
                        first,
                        second,
                    ],
                    stop_reason="toolUse",
                ),
                _tool_result(
                    "call-1",
                    [
                        TextContent(text="上海"),
                        TextContent(text="结果"),
                    ],
                ),
                _tool_result("call-2", []),
            ]
        )

        messages = build_deepseek_request(context)["messages"]

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "tool", "tool"],
        )
        assistant = messages[1]
        self.assertEqual(assistant["content"], "开始查询")
        self.assertEqual(
            [call["id"] for call in assistant["tool_calls"]],
            ["call-1", "call-2"],
        )
        self.assertEqual(
            [call["type"] for call in assistant["tool_calls"]],
            ["function", "function"],
        )
        self.assertEqual(
            [call["function"]["name"] for call in assistant["tool_calls"]],
            ["lookup", "lookup"],
        )

        arguments = assistant["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(
            json.loads(arguments),
            {"query": "上海", "limit": 3},
        )
        self.assertIn("上海", arguments)

        self.assertEqual(messages[2]["tool_call_id"], "call-1")
        self.assertEqual(messages[2]["content"], "上海结果")
        self.assertEqual(messages[3]["tool_call_id"], "call-2")
        self.assertEqual(messages[3]["content"], "")
        json.dumps(messages, ensure_ascii=False, allow_nan=False)

    def test_hides_internal_metadata_and_keeps_failed_call(self) -> None:
        call = ToolCall(
            id="call-1",
            name="lookup",
            arguments={},
            thought_signature="opaque-signature",
            namespace="weather",
            arguments_error="Expecting value: line 1 column 1",
        )
        context = Context(
            messages=[
                UserMessage(content=[TextContent(text="查")]),
                _assistant([call], stop_reason="toolUse"),
                _tool_result(
                    "call-1",
                    [TextContent(text="参数解析失败")],
                    is_error=True,
                    details={"trace": "internal"},
                ),
            ]
        )

        request = build_deepseek_request(context)
        assistant = request["messages"][1]
        tool_message = request["messages"][2]

        self.assertIsNone(assistant["content"])
        self.assertEqual(assistant["reasoning_content"], "")
        self.assertEqual(
            set(assistant),
            {"role", "content", "reasoning_content", "tool_calls"},
        )
        self.assertEqual(
            set(assistant["tool_calls"][0]),
            {"id", "type", "function"},
        )
        self.assertEqual(
            set(assistant["tool_calls"][0]["function"]),
            {"name", "arguments"},
        )
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
        self.assertEqual(
            assistant["tool_calls"][0]["function"]["arguments"],
            "{}",
        )
        self.assertEqual(
            set(tool_message),
            {"role", "tool_call_id", "content"},
        )
        self.assertEqual(tool_message["tool_call_id"], "call-1")
        self.assertEqual(tool_message["content"], "参数解析失败")

        serialized = json.dumps(request, ensure_ascii=False)
        for leaked in (
            "arguments_error",
            "thought_signature",
            "namespace",
            "is_error",
            "details",
            "tool_name",
            "opaque-signature",
            "internal",
        ):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, serialized)

    def test_preserves_tool_schema_and_isolates_nested_values(self) -> None:
        definition = _definition()
        request = build_deepseek_request(
            Context(
                messages=[UserMessage(content=[TextContent(text="查")])],
                tools=[definition],
            )
        )

        tool = request["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "lookup")
        self.assertEqual(
            tool["function"]["description"],
            definition.description,
        )
        self.assertEqual(
            tool["function"]["parameters"],
            definition.parameters,
        )
        parameters = tool["function"]["parameters"]
        self.assertIsNot(parameters, definition.parameters)
        self.assertIsNot(
            parameters["properties"],
            definition.parameters["properties"],
        )

        parameters["properties"]["query"]["type"] = "integer"
        parameters["additionalProperties"] = True

        self.assertEqual(
            definition.parameters["properties"]["query"]["type"],
            "string",
        )
        self.assertIs(definition.parameters["additionalProperties"], False)


class RequestErrorTests(unittest.TestCase):
    def test_rejects_images_in_user_and_tool_messages(self) -> None:
        image = ImageContent(data="Zm9v", mime_type="image/png")
        call = ToolCall(id="call-1", name="lookup", arguments={})

        cases = (
            (
                "user",
                Context(
                    messages=[
                        UserMessage(
                            content=[TextContent(text="看图"), image]
                        )
                    ]
                ),
            ),
            (
                "tool result",
                Context(
                    messages=[
                        _assistant([call], stop_reason="toolUse"),
                        _tool_result("call-1", [image]),
                    ]
                ),
            ),
        )
        for label, context in cases:
            with self.subTest(case=label):
                with self.assertRaises(DeepSeekRequestError):
                    build_deepseek_request(context)

    def test_rejects_unfinished_or_failed_assistant_states(self) -> None:
        for stop_reason in ("length", "error", "aborted"):
            with self.subTest(stop_reason=stop_reason):
                with self.assertRaises(DeepSeekRequestError):
                    build_deepseek_request(
                        Context(
                            messages=[
                                _assistant([], stop_reason=stop_reason)
                            ]
                        )
                    )

    def test_rejects_redacted_thinking(self) -> None:
        with self.assertRaises(DeepSeekRequestError):
            build_deepseek_request(
                Context(
                    messages=[
                        _assistant(
                            [
                                ThinkingContent(
                                    thinking="secret",
                                    redacted=True,
                                )
                            ]
                        )
                    ]
                )
            )

    def test_rejects_orphaned_duplicate_and_missing_tool_results(
        self,
    ) -> None:
        call = ToolCall(id="call-1", name="lookup", arguments={})
        assistant = _assistant([call], stop_reason="toolUse")
        result = _tool_result("call-1", [TextContent(text="结果")])
        mismatched = _tool_result("call-2", [TextContent(text="结果")])

        cases = (
            ("orphan", [result]),
            ("duplicate", [assistant, result, result]),
            ("missing", [assistant]),
            ("mismatched", [assistant, mismatched]),
            (
                "missing before next message",
                [
                    assistant,
                    UserMessage(content=[TextContent(text="继续")]),
                ],
            ),
        )
        for label, messages in cases:
            with self.subTest(case=label):
                with self.assertRaises(DeepSeekRequestError):
                    build_deepseek_request(Context(messages=messages))

    def test_rejects_non_json_arguments(self) -> None:
        for value in (float("nan"), object()):
            with self.subTest(value=type(value).__name__):
                call = ToolCall(
                    id="call-1",
                    name="lookup",
                    arguments={"query": value},
                )
                context = Context(
                    messages=[
                        _assistant([call], stop_reason="toolUse"),
                        _tool_result("call-1", []),
                    ]
                )
                with self.assertRaises(DeepSeekRequestError):
                    build_deepseek_request(context)

    def test_rejects_non_json_tool_schemas(self) -> None:
        definition = ToolDefinition(
            name="lookup",
            description="Look up one section.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "default": float("nan"),
                    },
                },
            },
        )

        with self.assertRaises(DeepSeekRequestError):
            build_deepseek_request(
                Context(
                    messages=[
                        UserMessage(content=[TextContent(text="查")])
                    ],
                    tools=[definition],
                )
            )


if __name__ == "__main__":
    unittest.main()
