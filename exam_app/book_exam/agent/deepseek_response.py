"""Parse a non-streaming DeepSeek Chat Completions response.

Tool argument parse failures do not reject a structurally valid response.
The affected tool call is kept with ``arguments_error`` set. The run loop must
check ``arguments_error is not None``, skip the handler, and produce a failed
tool result; execution blocking is not implemented here.
"""

import json
import math
from typing import Any

from .messages import (
    AssistantMessage,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)


class DeepSeekResponseError(ValueError):
    """Raised when a DeepSeek response is structurally invalid."""


_FINISH_REASON_MAP: dict[str, StopReason] = {
    "stop": "stop",
    "tool_calls": "toolUse",
    "length": "length",
    "content_filter": "error",
    "insufficient_system_resource": "error",
    "aborted": "aborted",
}

_VALID_JSON_ESCAPES = frozenset({'"', "\\", "/", "b", "f", "n", "r", "t"})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_CONTROL_ESCAPES = {
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeepSeekResponseError(
            f"{path}: expected an object, got {type(value).__name__}"
        )
    return value


def _optional_text(
    source: dict[str, Any],
    field: str,
    path: str,
) -> str | None:
    value = source.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeepSeekResponseError(
            f"{path}: expected a string or null, got {type(value).__name__}"
        )
    if not value:
        return None
    return value


def _optional_identifier(
    response: dict[str, Any],
    field: str,
) -> str | None:
    if field not in response:
        return None
    value = response[field]
    if not isinstance(value, str) or not value:
        raise DeepSeekResponseError(
            f"{field}: expected a non-empty string when present, got {value!r}"
        )
    return value


def _optional_count(
    source: dict[str, Any],
    field: str,
    path: str,
) -> int | None:
    value = source.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeepSeekResponseError(
            f"{path}: expected a non-negative integer or null, got {value!r}"
        )
    return value


def _optional_object(
    source: dict[str, Any],
    field: str,
    path: str,
) -> dict[str, Any] | None:
    value = source.get(field)
    if value is None:
        return None
    return _require_object(value, path)


def _convert_usage(raw: Any) -> Usage | None:
    if raw is None:
        return None

    usage = _require_object(raw, "usage")
    prompt_details = _optional_object(
        usage,
        "prompt_tokens_details",
        "usage.prompt_tokens_details",
    )
    completion_details = _optional_object(
        usage,
        "completion_tokens_details",
        "usage.completion_tokens_details",
    )

    cache_read_tokens = _optional_count(
        usage,
        "prompt_cache_hit_tokens",
        "usage.prompt_cache_hit_tokens",
    )
    cached_tokens = None
    if prompt_details is not None:
        cached_tokens = _optional_count(
            prompt_details,
            "cached_tokens",
            "usage.prompt_tokens_details.cached_tokens",
        )
    if cache_read_tokens is None:
        cache_read_tokens = cached_tokens

    reasoning_tokens = None
    if completion_details is not None:
        reasoning_tokens = _optional_count(
            completion_details,
            "reasoning_tokens",
            "usage.completion_tokens_details.reasoning_tokens",
        )

    return Usage(
        input_tokens=_optional_count(
            usage,
            "prompt_tokens",
            "usage.prompt_tokens",
        ),
        output_tokens=_optional_count(
            usage,
            "completion_tokens",
            "usage.completion_tokens",
        ),
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=None,
        reasoning_tokens=reasoning_tokens,
        total_tokens=_optional_count(
            usage,
            "total_tokens",
            "usage.total_tokens",
        ),
    )


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite number {value!r} is not valid JSON")


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False


def _repair_json_strings(text: str) -> str:
    """Repair only control characters and invalid escape characters in strings.

    Raw control characters are escaped. Backslashes before invalid escape
    characters, including malformed \\u escapes, are doubled so they become
    literal text. Unicode escapes are never padded or completed, and truncated
    input or missing brackets, quotes, and commas are left untouched. Content
    outside strings is never modified.
    """
    repaired: list[str] = []
    in_string = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if not in_string:
            if char == '"':
                in_string = True
            repaired.append(char)
            index += 1
            continue

        if char == '"':
            in_string = False
            repaired.append(char)
            index += 1
            continue

        if char == "\\":
            if index + 1 >= length:
                repaired.append("\\\\")
                index += 1
                continue

            escaped = text[index + 1]
            if escaped == "u":
                hex_digits = text[index + 2 : index + 6]
                if len(hex_digits) == 4 and all(
                    digit in _HEX_DIGITS for digit in hex_digits
                ):
                    repaired.append("\\u" + hex_digits)
                    index += 6
                    continue

                # Malformed \u escapes are repaired like any other invalid
                # escape: the backslash becomes literal text.
                repaired.append("\\\\")
                index += 1
                continue

            if escaped in _VALID_JSON_ESCAPES:
                repaired.append("\\" + escaped)
                index += 2
                continue

            repaired.append("\\\\")
            index += 1
            continue

        if ord(char) < 0x20:
            repaired.append(_CONTROL_ESCAPES.get(char, f"\\u{ord(char):04x}"))
            index += 1
            continue

        repaired.append(char)
        index += 1

    return "".join(repaired)


def _loads_json(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_constant)


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw.strip():
        return {}, None

    try:
        parsed = _loads_json(raw)
    except ValueError:
        try:
            parsed = _loads_json(_repair_json_strings(raw))
        except ValueError as error:
            return (
                {},
                f"invalid JSON arguments after limited repair: {error}; "
                f"raw arguments: {raw}",
            )

    if not isinstance(parsed, dict):
        return (
            {},
            "arguments must be a JSON object, got "
            f"{type(parsed).__name__}; raw arguments: {raw}",
        )

    if _contains_non_finite(parsed):
        return (
            {},
            "arguments contain non-finite numbers; "
            f"raw arguments: {raw}",
        )

    return parsed, None


def _convert_tool_calls(raw: Any) -> list[ToolCall]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DeepSeekResponseError(
            f"choices[0].message.tool_calls: expected a list, got "
            f"{type(raw).__name__}"
        )

    calls: list[ToolCall] = []
    seen_ids: set[str] = set()

    for index, value in enumerate(raw):
        path = f"choices[0].message.tool_calls[{index}]"
        call = _require_object(value, path)

        if call.get("type") != "function":
            raise DeepSeekResponseError(
                f"{path}.type: expected 'function', got {call.get('type')!r}"
            )

        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise DeepSeekResponseError(
                f"{path}.id: expected a non-empty string, got {call_id!r}"
            )
        if call_id in seen_ids:
            raise DeepSeekResponseError(
                f"{path}.id: duplicate tool call id {call_id!r}"
            )
        seen_ids.add(call_id)

        function = _require_object(call.get("function"), f"{path}.function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise DeepSeekResponseError(
                f"{path}.function.name: expected a non-empty string, "
                f"got {name!r}"
            )

        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise DeepSeekResponseError(
                f"{path}.function.arguments: expected a string, got "
                f"{type(arguments).__name__}"
            )

        parsed_arguments, arguments_error = _parse_arguments(arguments)
        # The run loop must block this call when arguments_error is not None.
        calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=parsed_arguments,
                arguments_error=arguments_error,
            )
        )

    return calls


def parse_deepseek_response(
    response: dict[str, Any],
    *,
    model: str = "deepseek-flash",
) -> AssistantMessage:
    """Convert one non-streaming DeepSeek response into an assistant message."""
    root = _require_object(response, "response")

    choices = root.get("choices")
    if not isinstance(choices, list):
        raise DeepSeekResponseError(
            f"choices: expected a list, got {type(choices).__name__}"
        )
    if len(choices) != 1:
        raise DeepSeekResponseError(
            f"choices: expected exactly one choice, got {len(choices)}"
        )

    choice = _require_object(choices[0], "choices[0]")
    message = _require_object(choice.get("message"), "choices[0].message")

    if message.get("role") != "assistant":
        raise DeepSeekResponseError(
            "choices[0].message.role: expected 'assistant', got "
            f"{message.get('role')!r}"
        )

    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        raise DeepSeekResponseError(
            "choices[0].finish_reason: expected a string, got "
            f"{type(finish_reason).__name__}"
        )

    stop_reason = _FINISH_REASON_MAP.get(finish_reason, "error")
    tool_calls = _convert_tool_calls(message.get("tool_calls"))

    if finish_reason == "tool_calls" and not tool_calls:
        raise DeepSeekResponseError(
            "choices[0].message.tool_calls: finish_reason is 'tool_calls' "
            "but no tool calls were returned"
        )
    if finish_reason == "stop" and tool_calls:
        raise DeepSeekResponseError(
            "choices[0].message.tool_calls: finish_reason is 'stop' but "
            "tool calls were returned"
        )

    content: list[ThinkingContent | TextContent | ToolCall] = []
    reasoning_content = _optional_text(
        message,
        "reasoning_content",
        "choices[0].message.reasoning_content",
    )
    if reasoning_content is not None:
        content.append(ThinkingContent(thinking=reasoning_content))

    text = _optional_text(
        message,
        "content",
        "choices[0].message.content",
    )
    if text is not None:
        content.append(TextContent(text=text))

    content.extend(tool_calls)

    error_message = None
    if stop_reason == "error":
        error_message = (
            "DeepSeek response ended with finish_reason "
            f"{finish_reason!r}"
        )

    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="deepseek",
        model=model,
        stop_reason=stop_reason,
        usage=_convert_usage(root.get("usage")),
        error_message=error_message,
        response_id=_optional_identifier(root, "id"),
        response_model=_optional_identifier(root, "model"),
        raw_stop_reason=finish_reason,
    )
