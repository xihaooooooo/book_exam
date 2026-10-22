"""Tool protocol shared by agent adapters."""

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence, TypeAlias

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .messages import ImageContent, TextContent


class ToolError(ValueError):
    """Raised when a tool definition or tool protocol value is invalid."""


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A model-facing tool definition without a bound implementation."""

    name: str
    description: str
    parameters: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolError("tool name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ToolError("tool description must be a non-empty string")
        if not isinstance(self.parameters, dict):
            raise ToolError("tool parameters must be a JSON Schema object")
        if self.parameters.get("type") != "object":
            raise ToolError("tool parameters must have root type 'object'")

        try:
            Draft202012Validator.check_schema(self.parameters)
        except SchemaError as error:
            raise ToolError(
                f"tool parameters are not a valid JSON Schema: {error.message}"
            ) from error


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """The result of one tool call, before it is wrapped as a message."""

    content: Sequence[TextContent | ImageContent]
    details: Any = None
    is_error: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.content, (str, bytes)):
            raise ToolError(
                "tool result content must be a sequence of content blocks"
            )

        try:
            content = tuple(self.content)
        except TypeError as error:
            raise ToolError(
                "tool result content must be a sequence of content blocks"
            ) from error

        for block in content:
            if not isinstance(block, (TextContent, ImageContent)):
                raise ToolError(
                    "tool result content must contain only TextContent or "
                    "ImageContent blocks"
                )

        if not isinstance(self.is_error, bool):
            raise ToolError("is_error must be a boolean")

        object.__setattr__(self, "content", content)


ToolHandler: TypeAlias = Callable[
    [dict[str, Any]], Awaitable[ToolExecutionResult]
]


def _error_result(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=(TextContent(text=message),),
        is_error=True,
    )


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool definition paired with its asynchronous handler."""

    definition: ToolDefinition
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ToolDefinition):
            raise ToolError("definition must be a ToolDefinition")
        if not callable(self.handler):
            raise ToolError("handler must be callable")

    async def execute(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Validate one call and execute it, or return a failed tool result.

        Cancellation is not converted into a failed result. Cancelling this
        coroutine does not forcibly stop synchronous work already running in a
        worker thread.
        """
        if not isinstance(arguments, dict):
            return _error_result(
                f"Tool '{self.definition.name}' arguments are invalid: "
                "expected a JSON object"
            )

        try:
            validator = Draft202012Validator(self.definition.parameters)
            validator.validate(arguments)
        except ValidationError as error:
            location = ".".join(str(part) for part in error.absolute_path)
            suffix = f" at '{location}'" if location else ""
            return _error_result(
                f"Tool '{self.definition.name}' arguments are invalid"
                f"{suffix}: {error.message}"
            )
        except SchemaError as error:
            raise ToolError(
                f"Tool '{self.definition.name}' has an invalid JSON Schema: "
                f"{error.message}"
            ) from error

        try:
            pending = self.handler(arguments)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _error_result(
                f"Tool '{self.definition.name}' execution failed: {error}"
            )

        if not inspect.isawaitable(pending):
            raise ToolError(
                f"Tool '{self.definition.name}' handler must return an awaitable"
            )

        try:
            result = await pending
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _error_result(
                f"Tool '{self.definition.name}' execution failed: {error}"
            )

        if not isinstance(result, ToolExecutionResult):
            raise ToolError(
                f"Tool '{self.definition.name}' handler must return "
                "a ToolExecutionResult"
            )
        return result
