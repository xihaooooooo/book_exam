"""Base message protocol shared by the agent components.

Content sequences are normalized to tuples on construction. Nested values such
as tool-call argument dictionaries are stored as provided and are not deeply
frozen.
"""

from dataclasses import dataclass, field
from time import time_ns
from typing import Any, Literal, Sequence, TypeAlias, get_args


class MessageError(ValueError):
    """Raised when an agent message or content block is structurally invalid."""


StopReason: TypeAlias = Literal[
    "pending",
    "stop",
    "length",
    "toolUse",
    "error",
    "aborted",
]

_STOP_REASON_VALUES = get_args(StopReason)

_USAGE_COUNT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def _now_millis() -> int:
    """Return the current Unix timestamp in milliseconds."""
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts reported by a provider for one response."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name in _USAGE_COUNT_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MessageError(
                    f"{field_name} must be a non-negative integer or None"
                )


@dataclass(frozen=True, slots=True)
class TextContent:
    """A text content block."""

    type: Literal["text"] = field(init=False, default="text")
    text: str
    text_signature: str | None = None


@dataclass(frozen=True, slots=True)
class ImageContent:
    """A base64-encoded image content block."""

    type: Literal["image"] = field(init=False, default="image")
    data: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ThinkingContent:
    """A thinking content block, optionally redacted by the provider."""

    type: Literal["thinking"] = field(init=False, default="thinking")
    thinking: str
    thinking_signature: str | None = None
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call requested by an assistant message."""

    type: Literal["toolCall"] = field(init=False, default="toolCall")
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None
    namespace: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, dict):
            raise MessageError("arguments must be a parameter dictionary")


ContentBlock: TypeAlias = TextContent | ImageContent | ThinkingContent | ToolCall
UserContent: TypeAlias = TextContent | ImageContent
AssistantContent: TypeAlias = TextContent | ThinkingContent | ToolCall
ToolResultContent: TypeAlias = TextContent | ImageContent


def _normalize_content(
    content: Sequence[ContentBlock],
    allowed_types: tuple[type, ...],
    message_name: str,
) -> tuple[ContentBlock, ...]:
    if isinstance(content, (str, bytes)):
        raise MessageError(
            f"{message_name} content must be a sequence of content blocks"
        )

    try:
        blocks = tuple(content)
    except TypeError as error:
        raise MessageError(
            f"{message_name} content must be a sequence of content blocks"
        ) from error

    for block in blocks:
        if not isinstance(block, allowed_types):
            raise MessageError(
                f"{message_name} content does not allow "
                f"{type(block).__name__} blocks"
            )

    return blocks


@dataclass(frozen=True, slots=True)
class UserMessage:
    """A user message containing text and/or images."""

    role: Literal["user"] = field(init=False, default="user")
    content: Sequence[UserContent]
    timestamp: int = field(default_factory=_now_millis)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content",
            _normalize_content(
                self.content,
                (TextContent, ImageContent),
                "user message",
            ),
        )


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """A model response containing text, thinking, and/or tool calls."""

    role: Literal["assistant"] = field(init=False, default="assistant")
    content: Sequence[AssistantContent]
    api: str
    provider: str
    model: str
    stop_reason: StopReason
    usage: Usage | None = None
    error_message: str | None = None
    response_id: str | None = None
    response_model: str | None = None
    raw_stop_reason: str | None = None
    timestamp: int = field(default_factory=_now_millis)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content",
            _normalize_content(
                self.content,
                (TextContent, ThinkingContent, ToolCall),
                "assistant message",
            ),
        )

        if self.stop_reason not in _STOP_REASON_VALUES:
            raise MessageError(f"unsupported stop_reason: {self.stop_reason!r}")


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """The result of one tool call."""

    role: Literal["toolResult"] = field(init=False, default="toolResult")
    tool_call_id: str
    tool_name: str
    content: Sequence[ToolResultContent]
    is_error: bool
    details: Any = None  # Program-only data; not part of the default model input.
    timestamp: int = field(default_factory=_now_millis)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content",
            _normalize_content(
                self.content,
                (TextContent, ImageContent),
                "tool result message",
            ),
        )


Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage
