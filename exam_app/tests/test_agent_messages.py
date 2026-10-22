import time
import unittest
from functools import partial

from book_exam.agent import (
    AssistantMessage,
    ImageContent,
    Message,
    MessageError,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def assistant_message(content, **overrides):
    fields = {
        "content": content,
        "api": "openai-completions",
        "provider": "openai",
        "model": "test-model",
        "stop_reason": "stop",
    }
    fields.update(overrides)
    return AssistantMessage(**fields)


def tool_result_message(content, **overrides):
    fields = {
        "tool_call_id": "call-1",
        "tool_name": "lookup",
        "content": content,
        "is_error": False,
    }
    fields.update(overrides)
    return ToolResultMessage(**fields)


class ContentCompositionTests(unittest.TestCase):
    def test_rejects_disallowed_content_blocks(self) -> None:
        image = ImageContent(data="Zm9v", mime_type="image/png")
        thinking = ThinkingContent(thinking="推理")
        call = ToolCall(id="call-1", name="lookup", arguments={})

        cases = (
            ("user with thinking", partial(UserMessage, content=[thinking])),
            ("user with tool call", partial(UserMessage, content=[call])),
            ("assistant with image", partial(assistant_message, [image])),
            (
                "tool result with thinking",
                partial(tool_result_message, [thinking]),
            ),
            (
                "tool result with tool call",
                partial(tool_result_message, [call]),
            ),
        )
        for label, build in cases:
            with self.subTest(case=label):
                with self.assertRaises(MessageError):
                    build()

    def test_rejects_non_sequence_content(self) -> None:
        for content in ("text", b"text", None, 3, TextContent(text="text")):
            with self.subTest(content=content):
                with self.assertRaises(MessageError):
                    UserMessage(content=content)

    def test_normalizes_content_and_isolates_it_from_source_lists(self) -> None:
        cases = (
            (
                "user",
                UserMessage,
                [
                    TextContent(text="甲"),
                    ImageContent(data="Zm9v", mime_type="image/png"),
                ],
            ),
            (
                "assistant",
                assistant_message,
                [
                    TextContent(text="乙"),
                    ThinkingContent(thinking="推理"),
                    ToolCall(id="call-1", name="lookup", arguments={}),
                ],
            ),
            (
                "tool result",
                tool_result_message,
                [
                    TextContent(text="丙"),
                    ImageContent(data="YmFy", mime_type="image/jpeg"),
                ],
            ),
        )
        for label, build, initial_blocks in cases:
            with self.subTest(message=label):
                source = list(initial_blocks)
                message = build(source)
                expected = tuple(source)

                self.assertIsInstance(message.content, tuple)
                self.assertEqual(message.content, expected)

                source.append(TextContent(text="后加"))
                source.clear()

                self.assertEqual(message.content, expected)


class FixedDiscriminatorTests(unittest.TestCase):
    def test_roles_and_types_are_fixed(self) -> None:
        self.assertEqual(UserMessage(content=[]).role, "user")
        self.assertEqual(assistant_message([]).role, "assistant")
        self.assertEqual(tool_result_message([]).role, "toolResult")
        self.assertEqual(TextContent(text="").type, "text")
        self.assertEqual(ImageContent(data="", mime_type="image/png").type, "image")
        self.assertEqual(ThinkingContent(thinking="").type, "thinking")
        self.assertEqual(
            ToolCall(id="call-1", name="lookup", arguments={}).type, "toolCall"
        )

        attempts = (
            partial(UserMessage, role="assistant", content=[]),
            partial(
                AssistantMessage,
                role="user",
                content=[],
                api="openai-completions",
                provider="openai",
                model="test-model",
                stop_reason="stop",
            ),
            partial(
                ToolResultMessage,
                role="user",
                tool_call_id="call-1",
                tool_name="lookup",
                content=[],
                is_error=False,
            ),
            partial(TextContent, type="image", text=""),
            partial(ImageContent, type="text", data="", mime_type="image/png"),
            partial(ThinkingContent, type="text", thinking=""),
            partial(ToolCall, type="text", id="call-1", name="lookup", arguments={}),
        )
        for build in attempts:
            with self.assertRaises(TypeError):
                build()


class ToolCallArgumentTests(unittest.TestCase):
    def test_requires_a_parameter_dictionary(self) -> None:
        for arguments in ('{"city": "上海"}', "{}", b"{}", None, ["city"], "city"):
            with self.subTest(arguments=arguments):
                with self.assertRaises(MessageError):
                    ToolCall(id="call-1", name="lookup", arguments=arguments)

    def test_keeps_valid_arguments_as_given(self) -> None:
        arguments = {"city": "上海", "limit": 3, "nested": {"x": [1, 2]}}
        call = ToolCall(id="call-1", name="lookup", arguments=arguments)

        self.assertIs(call.arguments, arguments)
        self.assertEqual(call.arguments["nested"], {"x": [1, 2]})


class UsageTests(unittest.TestCase):
    def test_keeps_zero_and_unknown_counts(self) -> None:
        usage = Usage(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=None,
            cache_write_tokens=None,
            reasoning_tokens=None,
            total_tokens=0,
        )

        self.assertEqual(usage.input_tokens, 0)
        self.assertEqual(usage.total_tokens, 0)
        self.assertIsNone(usage.cache_read_tokens)

        unknown = Usage()
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            with self.subTest(field=field_name):
                self.assertIsNone(getattr(unknown, field_name))

    def test_rejects_invalid_counts(self) -> None:
        valid_counts = {
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
            "reasoning_tokens": 5,
            "total_tokens": 15,
        }
        for field_name in valid_counts:
            for invalid_value in (-1, True, False, 1.5, "1"):
                with self.subTest(field=field_name, value=invalid_value):
                    with self.assertRaises(MessageError):
                        Usage(**{**valid_counts, field_name: invalid_value})


class StopReasonTests(unittest.TestCase):
    def test_accepts_first_version_stop_reasons(self) -> None:
        for stop_reason in (
            "pending",
            "stop",
            "length",
            "toolUse",
            "error",
            "aborted",
        ):
            with self.subTest(stop_reason=stop_reason):
                message = assistant_message([], stop_reason=stop_reason)
                self.assertEqual(message.stop_reason, stop_reason)

    def test_rejects_unknown_or_deferred_stop_reasons(self) -> None:
        for stop_reason in ("deferred", "", "tool_use", None):
            with self.subTest(stop_reason=stop_reason):
                with self.assertRaises(MessageError):
                    assistant_message([], stop_reason=stop_reason)


class TimestampTests(unittest.TestCase):
    def test_defaults_to_unix_milliseconds_and_keeps_explicit_timestamp(self) -> None:
        before = time.time_ns() // 1_000_000
        message = UserMessage(content=[])
        after = time.time_ns() // 1_000_000

        self.assertIsInstance(message.timestamp, int)
        self.assertGreaterEqual(message.timestamp, before)
        self.assertLessEqual(message.timestamp, after)

        explicit = assistant_message([], timestamp=1_700_000_000_123)
        self.assertEqual(explicit.timestamp, 1_700_000_000_123)


class EmptyValueTests(unittest.TestCase):
    def test_allows_empty_content_text_and_arguments(self) -> None:
        self.assertEqual(UserMessage(content=[]).content, ())
        self.assertEqual(assistant_message([]).content, ())
        self.assertEqual(tool_result_message([]).content, ())
        self.assertEqual(TextContent(text="").text, "")
        self.assertEqual(ThinkingContent(thinking="").thinking, "")
        self.assertEqual(
            ToolCall(id="call-1", name="lookup", arguments={}).arguments, {}
        )


class SignatureTests(unittest.TestCase):
    def test_preserves_signatures_verbatim(self) -> None:
        text_signature = '{"v": 1, "id": "text-1", "phase": "final_answer"}'
        thinking_signature = "opaque-thinking-signature"
        thought_signature = '{"opaque": true}'

        text = TextContent(text="回答", text_signature=text_signature)
        thinking = ThinkingContent(
            thinking="推理",
            thinking_signature=thinking_signature,
            redacted=True,
        )
        call = ToolCall(
            id="call-1",
            name="lookup",
            arguments={"city": "上海"},
            thought_signature=thought_signature,
            namespace="weather",
        )

        self.assertEqual(text.text_signature, text_signature)
        self.assertEqual(thinking.thinking_signature, thinking_signature)
        self.assertIs(thinking.redacted, True)
        self.assertEqual(call.thought_signature, thought_signature)
        self.assertEqual(call.namespace, "weather")


class MessageUnionTests(unittest.TestCase):
    def test_matches_all_three_message_kinds(self) -> None:
        messages = (
            UserMessage(content=[]),
            assistant_message([]),
            tool_result_message([]),
        )
        for message in messages:
            with self.subTest(role=message.role):
                self.assertIsInstance(message, Message)


if __name__ == "__main__":
    unittest.main()
