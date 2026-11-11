"""Drive the agent's model/tool loop with an injected completion callable."""

import math
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable, Literal, Sequence

from .context import Context
from .messages import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from .tools import AgentTool, ToolError, ToolExecutionResult

AgentRunStatus = Literal["limitReached", "timedOut"]

_STATUS_VALUES = frozenset({"limitReached", "timedOut"})


class AgentRunError(ValueError):
    """Raised when a run value or a model reply is structurally invalid."""


def _bind_tools(tools: Sequence[AgentTool]) -> tuple[AgentTool, ...]:
    try:
        bound = tuple(tools)
    except TypeError as error:
        raise ToolError(
            "tools must be a sequence of AgentTool values"
        ) from error

    for tool in bound:
        if not isinstance(tool, AgentTool):
            raise ToolError("tools must contain AgentTool values")
    return bound


def _validate_max_rounds(max_rounds: object) -> int:
    if (
        isinstance(max_rounds, bool)
        or not isinstance(max_rounds, int)
        or max_rounds < 1
    ):
        raise ValueError("max_rounds must be a positive integer")
    return max_rounds


def _validate_timeout(timeout_seconds: object) -> float | None:
    if timeout_seconds is None:
        return None
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        raise ValueError(
            "timeout_seconds must be a finite positive number or None"
        )

    try:
        timeout = float(timeout_seconds)
    except OverflowError as error:
        raise ValueError(
            "timeout_seconds must be a finite positive number or None"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(
            "timeout_seconds must be a finite positive number or None"
        )
    return timeout


def _error_result(call: ToolCall, message: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=(TextContent(text=message),),
        is_error=True,
    )


def _result_message(
    call: ToolCall,
    result: ToolExecutionResult,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=result.content,
        is_error=result.is_error,
        details=result.details,
    )


async def _run_tool_call(
    call: ToolCall,
    tools_by_name: dict[str, AgentTool],
) -> ToolResultMessage:
    if call.arguments_error is not None:
        detail = (
            call.arguments_error.strip() or "arguments could not be parsed"
        )
        return _error_result(
            call,
            f"Tool '{call.name}' was not executed because its arguments "
            f"could not be parsed: {detail}",
        )

    tool = tools_by_name.get(call.name)
    if tool is None:
        return _error_result(
            call,
            f"Tool '{call.name}' is not available",
        )

    return _result_message(call, await tool.execute(call.arguments))


@dataclass(frozen=True, slots=True)
class AgentRun:
    """The outcome of one run of the agent loop.

    ``status`` is set only when a bound stopped the run. The model ending the
    run is not a status: the caller reads ``messages[-1].stop_reason`` for
    that. ``rounds`` counts completed model calls, and equals the number of
    assistant messages in ``messages``.

    A run that ends with ``"limitReached"`` may have ``messages`` ending in
    an assistant message whose tool calls were never executed: those calls
    have no result messages, and the last reply is kept as received.
    """

    status: AgentRunStatus | None
    messages: tuple[Message, ...]
    rounds: int

    def __post_init__(self) -> None:
        if self.status is not None and self.status not in _STATUS_VALUES:
            raise AgentRunError(
                f"unsupported run status: {self.status!r}"
            )

        if isinstance(self.rounds, bool) or not isinstance(self.rounds, int):
            raise AgentRunError("rounds must be an integer")
        if self.rounds < 0:
            raise AgentRunError("rounds must not be negative")

        if isinstance(self.messages, (str, bytes)):
            raise AgentRunError("messages must be a sequence of messages")
        try:
            messages = tuple(self.messages)
        except TypeError as error:
            raise AgentRunError(
                "messages must be a sequence of messages"
            ) from error
        if any(not isinstance(message, Message) for message in messages):
            raise AgentRunError("messages must contain agent messages")

        replies = sum(
            isinstance(message, AssistantMessage) for message in messages
        )
        if replies != self.rounds:
            raise AgentRunError(
                "rounds must equal the number of assistant messages"
            )

        object.__setattr__(self, "messages", messages)


async def run_agent_loop(
    messages: Sequence[Message],
    *,
    system_prompt: str | None = None,
    tools: Sequence[AgentTool] = (),
    complete: Callable[[Context], Awaitable[AssistantMessage]],
    max_rounds: int = 8,
    timeout_seconds: float | None = None,
) -> AgentRun:
    """Run model/tool rounds until the model ends the run or a bound is hit.

    ``messages`` holds the initial conversation. ``complete`` is any async
    callable that takes a ``Context`` and returns an ``AssistantMessage``.
    ``tools`` holds bound tools; only their ``AgentTool.definition`` values
    are sent to the model, and each tool call is dispatched to the matching
    ``AgentTool`` through ``execute``.

    Every round builds a ``Context`` from the whole history. A reply whose
    ``stop_reason`` is ``"toolUse"`` must carry at least one tool call: the
    reply is kept, one result message per call is appended after it, and the
    model is called again, unless the round budget is already spent. Any
    ``stop_reason`` other than ``"pending"`` and ``"toolUse"`` ends the run
    with that reply unchanged.

    A ``"pending"`` reply is unfinished and raises ``AgentRunError`` before
    the reply is recorded: that check runs right after ``complete`` returns,
    so such a reply never increments ``rounds`` and never enters
    ``messages``. A ``"toolUse"`` reply is recorded first, like any other
    reply; its tool calls are extracted afterwards, and one that carries none
    raises ``AgentRunError`` at that point, before the round budget is
    checked, so a malformed tool reply is never reported as
    ``"limitReached"``.

    ``max_rounds`` counts model calls: the first call is round one, and all
    tool calls in one reply belong to that round. ``timeout_seconds`` bounds
    the wall-clock time of the whole run and is checked before each model
    request. A reply that ends the run normally is honoured even on the last
    allowed round. A ``"toolUse"`` reply carrying tool calls on that round is
    not executed: the run stops with ``"limitReached"`` and the reply is kept
    as received, so ``messages`` can end with unexecuted tool calls that have
    no result messages. A single model request or tool call is not
    interrupted mid-round; bounding those is the injected callable's job.

    A tool call whose ``arguments_error`` is set is not executed; it gets a
    failed result carrying the parse error. A call naming an unknown tool
    gets a failed result as well. Both keep the original ``tool_call_id``.

    Returns an ``AgentRun``. A run that hit a bound carries that bound as
    ``status``: ``"limitReached"`` for ``max_rounds`` and ``"timedOut"`` for
    the deadline. A run the model ended carries no status: the caller reads
    ``messages[-1].stop_reason`` instead. Bounds are never reported as
    silently successful runs.
    """
    tool_list = _bind_tools(tools)
    definitions = tuple(tool.definition for tool in tool_list)
    tools_by_name = {tool.definition.name: tool for tool in tool_list}

    round_limit = _validate_max_rounds(max_rounds)
    timeout = _validate_timeout(timeout_seconds)
    if timeout is None:
        deadline = None
    else:
        deadline = monotonic() + timeout

    history: list[Message] = list(messages)
    produced: list[Message] = []
    rounds = 0

    while True:
        if deadline is not None and monotonic() >= deadline:
            return AgentRun("timedOut", tuple(produced), rounds)

        context = Context(
            messages=history,
            system_prompt=system_prompt,
            tools=definitions,
        )
        reply = await complete(context)
        if reply.stop_reason == "pending":
            raise AgentRunError(
                "complete returned a pending reply; an unfinished reply "
                "cannot be recorded"
            )
        rounds += 1
        produced.append(reply)
        history.append(reply)

        if reply.stop_reason != "toolUse":
            return AgentRun(None, tuple(produced), rounds)

        calls = tuple(
            block for block in reply.content if isinstance(block, ToolCall)
        )
        if not calls:
            raise AgentRunError(
                "stop_reason 'toolUse' requires at least one tool call"
            )

        if rounds >= round_limit:
            return AgentRun("limitReached", tuple(produced), rounds)

        for call in calls:
            result = await _run_tool_call(call, tools_by_name)
            produced.append(result)
            history.append(result)
