"""Model request context shared by agent components."""

from dataclasses import dataclass
from typing import Sequence

from .messages import AssistantMessage, Message
from .tools import ToolDefinition


class ContextError(ValueError):
    """Raised when an agent context is structurally invalid."""


def _normalize_sequence(value, label: str) -> tuple:
    if isinstance(value, (str, bytes)):
        raise ContextError(f"{label} must be a sequence")

    try:
        return tuple(value)
    except TypeError as error:
        raise ContextError(f"{label} must be a sequence") from error


@dataclass(frozen=True, slots=True)
class Context:
    """A snapshot of messages and tool definitions for one model request."""

    messages: Sequence[Message]
    system_prompt: str | None = None
    tools: Sequence[ToolDefinition] = ()

    def __post_init__(self) -> None:
        messages = _normalize_sequence(self.messages, "messages")
        for message in messages:
            if not isinstance(message, Message):
                raise ContextError(
                    "messages must contain user, assistant, or tool result messages"
                )
            if (
                isinstance(message, AssistantMessage)
                and message.stop_reason == "pending"
            ):
                raise ContextError(
                    "pending assistant messages cannot enter a context"
                )

        if self.system_prompt is not None and not isinstance(
            self.system_prompt, str
        ):
            raise ContextError("system_prompt must be a string or None")

        tools = _normalize_sequence(self.tools, "tools")
        tool_names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, ToolDefinition):
                raise ContextError("tools must contain ToolDefinition values")
            if tool.name in tool_names:
                raise ContextError(f"duplicate tool name: {tool.name}")
            tool_names.add(tool.name)

        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
