"""Convert an agent context into a DeepSeek Chat Completions request."""

import json
from copy import deepcopy
from typing import Any, Sequence

from .context import Context
from .messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from .tools import ToolDefinition


class DeepSeekRequestError(ValueError):
    """Raised when a context cannot be converted into a DeepSeek request."""


_MODEL = "deepseek-flash"
_STREAM = False
_THINKING = {"type": "enabled"}
_SUPPORTED_STOP_REASONS = frozenset({"stop", "toolUse"})


def _join_text(
    content: Sequence[TextContent | ImageContent],
    label: str,
) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            raise DeepSeekRequestError(
                f"{label} contains image content, which DeepSeek text requests "
                "do not support"
            )
        else:
            raise DeepSeekRequestError(
                f"{label} contains unsupported content: {type(block).__name__}"
            )
    return "".join(parts)


def _convert_user_message(
    message: UserMessage,
    message_index: int,
) -> dict[str, Any]:
    return {
        "role": "user",
        "content": _join_text(
            message.content,
            f"user message at index {message_index}",
        ),
    }


def _convert_tool_call(call: ToolCall, message_index: int) -> dict[str, Any]:
    try:
        arguments = json.dumps(
            call.arguments,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DeepSeekRequestError(
            f"tool call {call.id!r} in assistant message at index "
            f"{message_index} has arguments that cannot be serialized as JSON"
        ) from error

    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": arguments,
        },
    }


def _convert_assistant_message(
    message: AssistantMessage,
    message_index: int,
) -> tuple[dict[str, Any], list[str]]:
    if message.stop_reason not in _SUPPORTED_STOP_REASONS:
        raise DeepSeekRequestError(
            f"assistant message at index {message_index} has unsupported "
            f"stop_reason {message.stop_reason!r}"
        )

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    call_ids: list[str] = []

    for block in message.content:
        if isinstance(block, TextContent):
            text_parts.append(block.text)
        elif isinstance(block, ThinkingContent):
            if block.redacted:
                raise DeepSeekRequestError(
                    f"assistant message at index {message_index} contains "
                    "redacted thinking content"
                )
            thinking_parts.append(block.thinking)
        elif isinstance(block, ToolCall):
            tool_calls.append(_convert_tool_call(block, message_index))
            call_ids.append(block.id)
        else:
            raise DeepSeekRequestError(
                f"assistant message at index {message_index} contains "
                f"unsupported content: {type(block).__name__}"
            )

    content: str | None = "".join(text_parts)
    if not content:
        content = None if tool_calls else ""

    api_message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "reasoning_content": "".join(thinking_parts),
    }
    if tool_calls:
        api_message["tool_calls"] = tool_calls

    return api_message, call_ids


def _convert_tool_result_message(
    message: ToolResultMessage,
    message_index: int,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "content": _join_text(
            message.content,
            f"tool result message at index {message_index}",
        ),
    }


def _missing_results_error(
    pending_ids: list[str],
    label: str,
) -> DeepSeekRequestError:
    call_ids = ", ".join(repr(call_id) for call_id in pending_ids)
    return DeepSeekRequestError(
        f"tool results are missing for tool call ids {call_ids} before {label}"
    )


def _convert_messages(context: Context) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if context.system_prompt is not None:
        messages.append(
            {"role": "system", "content": context.system_prompt}
        )

    pending_ids: list[str] = []
    fulfilled_ids: set[str] = set()

    for index, message in enumerate(context.messages):
        label = f"message at index {index}"
        if isinstance(message, UserMessage):
            if pending_ids:
                raise _missing_results_error(pending_ids, label)
            messages.append(_convert_user_message(message, index))
        elif isinstance(message, AssistantMessage):
            if pending_ids:
                raise _missing_results_error(pending_ids, label)
            api_message, call_ids = _convert_assistant_message(message, index)
            messages.append(api_message)
            pending_ids = call_ids
            fulfilled_ids = set()
        elif isinstance(message, ToolResultMessage):
            call_id = message.tool_call_id
            if call_id in fulfilled_ids:
                raise DeepSeekRequestError(
                    f"duplicate tool result for tool call id {call_id!r} at "
                    f"index {index}"
                )
            if call_id not in pending_ids:
                raise DeepSeekRequestError(
                    f"tool result at index {index} has no matching tool call "
                    f"for id {call_id!r}"
                )
            pending_ids.remove(call_id)
            fulfilled_ids.add(call_id)
            messages.append(_convert_tool_result_message(message, index))
        else:
            raise DeepSeekRequestError(
                f"unsupported message type at index {index}: "
                f"{type(message).__name__}"
            )

    if pending_ids:
        raise _missing_results_error(pending_ids, "the end of the history")

    return messages


def _convert_tool_definition(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": deepcopy(tool.parameters),
        },
    }


def build_deepseek_request(context: Context) -> dict[str, Any]:
    """Build a DeepSeek Chat Completions request from one context."""
    messages = _convert_messages(context)
    if not messages:
        raise DeepSeekRequestError("request messages must not be empty")

    request: dict[str, Any] = {
        "model": _MODEL,
        "messages": messages,
        "stream": _STREAM,
        "thinking": dict(_THINKING),
    }
    if context.tools:
        request["tools"] = [
            _convert_tool_definition(tool) for tool in context.tools
        ]

    try:
        json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DeepSeekRequestError(
            "converted request contains data that cannot be serialized as JSON"
        ) from error

    return request
