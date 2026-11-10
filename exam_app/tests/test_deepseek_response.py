import copy
import json
import unittest

from book_exam.agent import (
    AssistantMessage,
    Context,
    DeepSeekResponseError,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    build_deepseek_request,
    parse_deepseek_response,
)


def _response(
    message,
    finish_reason="stop",
    *,
    response_id="resp-1",
    response_model="deepseek-chat",
    usage=None,
):
    response = {
        "choices": [
            {
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }
    if response_id is not None:
        response["id"] = response_id
    if response_model is not None:
        response["model"] = response_model
    if usage is not None:
        response["usage"] = usage
    return response


def _message(**overrides):
    message = {"role": "assistant", "content": "回答"}
    message.update(overrides)
    return message


def _tool_call(
    call_id="call-1",
    name="lookup",
    arguments="{}",
    call_type="function",
):
    return {
        "id": call_id,
        "type": call_type,
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _tool_calls(message):
    return [
        block
        for block in message.content
        if isinstance(block, ToolCall)
    ]


class BasicContentTests(unittest.TestCase):
    def test_parses_text_and_thinking(self) -> None:
        response = _response(
            _message(content="正文", reasoning_content="思考")
        )

        message = parse_deepseek_response(response)

        self.assertIsInstance(message, AssistantMessage)
        self.assertEqual(message.api, "openai-completions")
        self.assertEqual(message.provider, "deepseek")
        self.assertEqual(message.model, "deepseek-flash")
        self.assertEqual(message.stop_reason, "stop")
        self.assertEqual(message.raw_stop_reason, "stop")
        self.assertEqual(message.response_id, "resp-1")
        self.assertEqual(message.response_model, "deepseek-chat")
        self.assertIsNone(message.usage)
        self.assertIsNone(message.error_message)
        self.assertEqual(len(message.content), 2)
        self.assertIsInstance(message.content[0], ThinkingContent)
        self.assertEqual(message.content[0].thinking, "思考")
        self.assertIsInstance(message.content[1], TextContent)
        self.assertEqual(message.content[1].text, "正文")

    def test_skips_null_missing_and_empty_content(self) -> None:
        cases = (
            {"role": "assistant"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": None,
            },
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "",
            },
        )
        for message_payload in cases:
            with self.subTest(message=message_payload):
                message = parse_deepseek_response(
                    _response(message_payload)
                )
                self.assertEqual(message.content, ())

    def test_preserves_non_empty_whitespace(self) -> None:
        message = parse_deepseek_response(
            _response(
                _message(
                    content="  \n",
                    reasoning_content="\t ",
                )
            )
        )

        self.assertEqual(len(message.content), 2)
        self.assertEqual(message.content[0].thinking, "\t ")
        self.assertEqual(message.content[1].text, "  \n")

    def test_rejects_non_string_content(self) -> None:
        for field in ("content", "reasoning_content"):
            for value in (1, True, [], {}):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(DeepSeekResponseError):
                        parse_deepseek_response(
                            _response(
                                {
                                    "role": "assistant",
                                    field: value,
                                }
                            )
                        )


class ToolCallResponseTests(unittest.TestCase):
    def test_parses_multiple_calls_in_order(self) -> None:
        response = _response(
            _message(
                content="查询",
                reasoning_content="思考",
                tool_calls=[
                    _tool_call(
                        "call-1",
                        "lookup",
                        '{"query": "上海", "limit": 3}',
                    ),
                    _tool_call(
                        "call-2",
                        "read_section",
                        '{"section_id": "2.1"}',
                    ),
                ],
            ),
            finish_reason="tool_calls",
        )

        message = parse_deepseek_response(response)

        self.assertEqual(message.stop_reason, "toolUse")
        self.assertEqual(message.raw_stop_reason, "tool_calls")
        self.assertEqual(
            [type(block) for block in message.content],
            [ThinkingContent, TextContent, ToolCall, ToolCall],
        )
        calls = _tool_calls(message)
        self.assertEqual(
            [call.id for call in calls],
            ["call-1", "call-2"],
        )
        self.assertEqual(
            [call.name for call in calls],
            ["lookup", "read_section"],
        )
        self.assertEqual(
            calls[0].arguments,
            {"query": "上海", "limit": 3},
        )
        self.assertEqual(
            calls[1].arguments,
            {"section_id": "2.1"},
        )
        for call in calls:
            self.assertIsNone(call.arguments_error)

    def test_treats_blank_arguments_as_empty_object(self) -> None:
        for raw in ("", "   ", "\n\t"):
            with self.subTest(raw=raw):
                message = parse_deepseek_response(
                    _response(
                        _message(
                            content=None,
                            tool_calls=[_tool_call(arguments=raw)],
                        ),
                        finish_reason="tool_calls",
                    )
                )
                call = _tool_calls(message)[0]
                self.assertEqual(call.arguments, {})
                self.assertIsNone(call.arguments_error)

    def test_rejects_duplicate_call_ids(self) -> None:
        response = _response(
            _message(
                content=None,
                tool_calls=[
                    _tool_call("call-1"),
                    _tool_call("call-1"),
                ],
            ),
            finish_reason="tool_calls",
        )

        with self.assertRaises(DeepSeekResponseError):
            parse_deepseek_response(response)

    def test_rejects_invalid_call_structures(self) -> None:
        invalid_calls = (
            {"id": "call-1", "type": "text", "function": {}},
            {"id": "call-1", "type": "function"},
            {
                "id": "",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            },
            {
                "id": 1,
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "", "arguments": "{}"},
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": 1, "arguments": "{}"},
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup"},
            },
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": {}},
            },
            "not an object",
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(
                        _response(
                            _message(
                                content=None,
                                tool_calls=[call],
                            ),
                            finish_reason="tool_calls",
                        )
                    )

    def test_rejects_tool_calls_that_are_not_a_list(self) -> None:
        for value in ("{}", {}, 1):
            with self.subTest(value=value):
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(
                        _response(
                            _message(content=None, tool_calls=value),
                            finish_reason="tool_calls",
                        )
                    )


class ArgumentRepairTests(unittest.TestCase):
    def _parse_call(self, raw):
        message = parse_deepseek_response(
            _response(
                _message(
                    content=None,
                    tool_calls=[_tool_call(arguments=raw)],
                ),
                finish_reason="tool_calls",
            )
        )
        return _tool_calls(message)[0]

    def test_keeps_valid_escapes_unchanged(self) -> None:
        raw = '{"text": "line\\nnext", "quote": "\\"", "unicode": "\\u4e2d"}'

        call = self._parse_call(raw)

        self.assertIsNone(call.arguments_error)
        self.assertEqual(
            call.arguments,
            {
                "text": "line\nnext",
                "quote": '"',
                "unicode": "中",
            },
        )

    def test_repairs_unescaped_control_characters(self) -> None:
        raw = '{"text": "line1\nline2", "tab": "a\tb", "control": "\x01"}'

        call = self._parse_call(raw)

        self.assertIsNone(call.arguments_error)
        self.assertEqual(
            call.arguments,
            {
                "text": "line1\nline2",
                "tab": "a\tb",
                "control": "\x01",
            },
        )

    def test_repairs_invalid_backslash_escapes(self) -> None:
        raw = '{"path": "C:\\path", "bad": "a\\qb"}'

        call = self._parse_call(raw)

        self.assertIsNone(call.arguments_error)
        self.assertEqual(
            call.arguments,
            {
                "path": "C:\\path",
                "bad": "a\\qb",
            },
        )

    def test_repairs_malformed_unicode_escapes_as_invalid_escapes(
        self,
    ) -> None:
        cases = (
            ('{"partial": "\\u12"}', "\\u12"),
            ('{"partial": "\\uZZZZ"}', "\\uZZZZ"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                call = self._parse_call(raw)
                self.assertIsNone(call.arguments_error)
                self.assertEqual(
                    call.arguments,
                    {"partial": expected},
                )

    def test_does_not_complete_truncated_or_wrapped_arguments(self) -> None:
        raw_values = (
            '{"query": "上海"',
            '{"text": "\\u12',
            '```json\n{"query": "x"}\n```',
            '{"a": 1,}',
        )
        for raw in raw_values:
            with self.subTest(raw=raw):
                call = self._parse_call(raw)
                self.assertEqual(call.arguments, {})
                self.assertIsNotNone(call.arguments_error)
                self.assertIn(raw, call.arguments_error)

    def test_rejects_non_object_and_non_finite_arguments(self) -> None:
        raw_values = (
            "[]",
            "1",
            "true",
            '"text"',
            "null",
            '{"x": NaN}',
            '{"x": Infinity}',
            '{"x": -Infinity}',
            '{"x": 1e999}',
            '{"x": {"y": [1e999]}}',
        )
        for raw in raw_values:
            with self.subTest(raw=raw):
                call = self._parse_call(raw)
                self.assertEqual(call.arguments, {})
                self.assertIsNotNone(call.arguments_error)
                self.assertIn(raw, call.arguments_error)

    def test_one_bad_argument_keeps_other_calls(self) -> None:
        response = _response(
            _message(
                content=None,
                tool_calls=[
                    _tool_call("call-1", arguments='{"query": "ok"}'),
                    _tool_call("call-2", arguments='{"query": "broken"'),
                ],
            ),
            finish_reason="tool_calls",
        )

        calls = _tool_calls(parse_deepseek_response(response))

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].id, "call-1")
        self.assertEqual(calls[0].arguments, {"query": "ok"})
        self.assertIsNone(calls[0].arguments_error)
        self.assertEqual(calls[1].id, "call-2")
        self.assertEqual(calls[1].arguments, {})
        self.assertIsNotNone(calls[1].arguments_error)


class FinishReasonTests(unittest.TestCase):
    def test_maps_finish_reasons(self) -> None:
        cases = (
            ("stop", "stop"),
            ("length", "length"),
            ("aborted", "aborted"),
            ("content_filter", "error"),
            ("insufficient_system_resource", "error"),
            ("unknown_reason", "error"),
            ("", "error"),
        )
        for raw, expected in cases:
            with self.subTest(finish_reason=raw):
                message = parse_deepseek_response(
                    _response(_message(), finish_reason=raw)
                )
                self.assertEqual(message.stop_reason, expected)
                self.assertEqual(message.raw_stop_reason, raw)
                if expected == "error":
                    self.assertTrue(message.error_message)
                    self.assertIn(raw, message.error_message)
                else:
                    self.assertIsNone(message.error_message)

    def test_maps_tool_calls_finish_reason(self) -> None:
        message = parse_deepseek_response(
            _response(
                _message(
                    content=None,
                    tool_calls=[_tool_call()],
                ),
                finish_reason="tool_calls",
            )
        )

        self.assertEqual(message.stop_reason, "toolUse")
        self.assertIsNone(message.error_message)

    def test_keeps_content_and_calls_for_abnormal_endings(self) -> None:
        cases = (
            ("length", "length"),
            ("content_filter", "error"),
            ("insufficient_system_resource", "error"),
            ("aborted", "aborted"),
            ("unknown_reason", "error"),
        )
        for raw, expected in cases:
            with self.subTest(finish_reason=raw):
                message = parse_deepseek_response(
                    _response(
                        _message(
                            content="部分回答",
                            reasoning_content="部分思考",
                            tool_calls=[_tool_call()],
                        ),
                        finish_reason=raw,
                    )
                )
                self.assertEqual(message.stop_reason, expected)
                self.assertIsInstance(
                    message.content[0],
                    ThinkingContent,
                )
                self.assertEqual(
                    message.content[1].text,
                    "部分回答",
                )
                self.assertEqual(len(_tool_calls(message)), 1)

    def test_rejects_finish_reason_call_mismatches(self) -> None:
        with self.assertRaises(DeepSeekResponseError):
            parse_deepseek_response(
                _response(
                    _message(content=None, tool_calls=[]),
                    finish_reason="tool_calls",
                )
            )

        with self.assertRaises(DeepSeekResponseError):
            parse_deepseek_response(
                _response(
                    _message(
                        content=None,
                        tool_calls=[_tool_call()],
                    ),
                    finish_reason="stop",
                )
            )

    def test_rejects_missing_or_invalid_finish_reason(self) -> None:
        base_choice = {
            "message": {"role": "assistant", "content": "回答"},
        }
        for choice in (
            base_choice,
            {**base_choice, "finish_reason": None},
            {**base_choice, "finish_reason": 1},
            {**base_choice, "finish_reason": ["stop"]},
        ):
            with self.subTest(choice=choice):
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response({"choices": [choice]})


class MetadataUsageTests(unittest.TestCase):
    def test_maps_metadata_and_request_model_separately(self) -> None:
        message = parse_deepseek_response(
            _response(_message()),
            model="custom-request-model",
        )

        self.assertEqual(message.model, "custom-request-model")
        self.assertEqual(message.response_model, "deepseek-chat")
        self.assertEqual(message.response_id, "resp-1")

    def test_keeps_missing_metadata_as_none(self) -> None:
        message = parse_deepseek_response(
            _response(
                _message(),
                response_id=None,
                response_model=None,
            )
        )

        self.assertIsNone(message.response_id)
        self.assertIsNone(message.response_model)

    def test_rejects_invalid_metadata(self) -> None:
        for field in ("id", "model"):
            for value in ("", None, 1, []):
                with self.subTest(field=field, value=value):
                    response = _response(_message())
                    response[field] = value
                    with self.assertRaises(DeepSeekResponseError):
                        parse_deepseek_response(response)

    def test_maps_usage_fields(self) -> None:
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 70,
            "prompt_tokens_details": {"cached_tokens": 99},
            "completion_tokens_details": {"reasoning_tokens": 7},
        }

        message = parse_deepseek_response(
            _response(_message(), usage=usage)
        )

        self.assertIsNotNone(message.usage)
        self.assertEqual(message.usage.input_tokens, 100)
        self.assertEqual(message.usage.output_tokens, 20)
        self.assertEqual(message.usage.cache_read_tokens, 30)
        self.assertIsNone(message.usage.cache_write_tokens)
        self.assertEqual(message.usage.reasoning_tokens, 7)
        self.assertEqual(message.usage.total_tokens, 120)

    def test_falls_back_to_cached_tokens_detail(self) -> None:
        usage = {
            "prompt_tokens": 50,
            "completion_tokens": 5,
            "total_tokens": 55,
            "prompt_tokens_details": {"cached_tokens": 15},
        }

        message = parse_deepseek_response(
            _response(_message(), usage=usage)
        )

        self.assertEqual(message.usage.cache_read_tokens, 15)

    def test_validates_cached_tokens_even_with_valid_primary(self) -> None:
        for cached in (-1, True, "15"):
            with self.subTest(cached=cached):
                usage = {
                    "prompt_cache_hit_tokens": 30,
                    "prompt_tokens_details": {
                        "cached_tokens": cached,
                    },
                }
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(
                        _response(_message(), usage=usage)
                    )

    def test_keeps_none_and_zero_distinction(self) -> None:
        missing = parse_deepseek_response(_response(_message()))
        self.assertIsNone(missing.usage)

        null_usage = _response(_message())
        null_usage["usage"] = None
        message = parse_deepseek_response(null_usage)
        self.assertIsNone(message.usage)

        zero_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        zero_message = parse_deepseek_response(
            _response(_message(), usage=zero_usage)
        )
        self.assertEqual(zero_message.usage.input_tokens, 0)
        self.assertEqual(zero_message.usage.output_tokens, 0)
        self.assertEqual(zero_message.usage.total_tokens, 0)
        self.assertIsNone(zero_message.usage.cache_read_tokens)
        self.assertIsNone(zero_message.usage.reasoning_tokens)

    def test_does_not_derive_total_tokens(self) -> None:
        usage = {"prompt_tokens": 10, "completion_tokens": 2}

        message = parse_deepseek_response(
            _response(_message(), usage=usage)
        )

        self.assertIsNone(message.usage.total_tokens)

    def test_rejects_invalid_usage_counts(self) -> None:
        invalid_counts = (
            ("prompt_tokens", -1),
            ("prompt_tokens", True),
            ("prompt_tokens", "10"),
            ("completion_tokens", 1.5),
            ("total_tokens", -1),
            ("prompt_cache_hit_tokens", False),
        )
        for field, value in invalid_counts:
            with self.subTest(field=field, value=value):
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(
                        _response(
                            _message(),
                            usage={field: value},
                        )
                    )

    def test_rejects_invalid_usage_details(self) -> None:
        responses = (
            {"usage": []},
            {"usage": "usage"},
            {"usage": {"prompt_tokens_details": []}},
            {"usage": {"completion_tokens_details": "details"}},
            {
                "usage": {
                    "completion_tokens_details": {
                        "reasoning_tokens": -1,
                    }
                }
            },
        )
        for overrides in responses:
            with self.subTest(overrides=overrides):
                response = _response(_message())
                response.update(overrides)
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(response)


class ResponseStructureTests(unittest.TestCase):
    def test_rejects_invalid_response_shapes(self) -> None:
        choice = {
            "message": {"role": "assistant", "content": "回答"},
            "finish_reason": "stop",
        }
        cases = (
            None,
            "not an object",
            [],
            {},
            {"choices": {}},
            {"choices": []},
            {"choices": [choice, choice]},
            {"choices": ["not an object"]},
            {
                "choices": [
                    {
                        "message": "not an object",
                        "finish_reason": "stop",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "user",
                            "content": "回答",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {"content": "回答"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )
        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(DeepSeekResponseError):
                    parse_deepseek_response(response)

    def test_does_not_modify_the_input(self) -> None:
        response = _response(
            _message(
                content="回答",
                reasoning_content="思考",
                tool_calls=[
                    _tool_call(
                        arguments='{"query": "上海"}',
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        )
        before = copy.deepcopy(response)

        parse_deepseek_response(response)

        self.assertEqual(response, before)


class RoundTripTests(unittest.TestCase):
    def test_converted_message_can_be_sent_back(self) -> None:
        response = _response(
            _message(
                content="调用工具",
                reasoning_content="思考",
                tool_calls=[
                    _tool_call(
                        "call-1",
                        "lookup",
                        '{"query": "上海"}',
                    )
                ],
            ),
            finish_reason="tool_calls",
        )

        assistant = parse_deepseek_response(response)
        result = ToolResultMessage(
            tool_call_id="call-1",
            tool_name="lookup",
            content=[TextContent(text="结果")],
            is_error=False,
        )
        request = build_deepseek_request(
            Context(messages=[assistant, result])
        )

        sent_assistant = request["messages"][0]
        self.assertEqual(sent_assistant["reasoning_content"], "思考")
        self.assertEqual(sent_assistant["content"], "调用工具")
        sent_call = sent_assistant["tool_calls"][0]
        self.assertEqual(sent_call["id"], "call-1")
        self.assertEqual(
            json.loads(sent_call["function"]["arguments"]),
            {"query": "上海"},
        )
        self.assertEqual(
            request["messages"][1]["tool_call_id"],
            "call-1",
        )


if __name__ == "__main__":
    unittest.main()
