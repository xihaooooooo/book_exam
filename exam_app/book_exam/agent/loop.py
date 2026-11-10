"""Drive the agent's model/tool loop with an injected completion callable."""

from typing import Awaitable, Callable, Sequence

from .context import Context
from .messages import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from .tools import AgentTool, ToolError, ToolExecutionResult


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


async def run_agent_loop(
    messages: Sequence[Message],
    *,
    system_prompt: str | None = None,
    tools: Sequence[AgentTool] = (),
    complete: Callable[[Context], Awaitable[AssistantMessage]],
) -> tuple[Message, ...]:
    """Run model/tool rounds until an assistant message ends the run.

    ``messages`` holds the initial conversation. ``complete`` is any async
    callable that takes a ``Context`` and returns an ``AssistantMessage``.
    ``tools`` holds bound tools; only their ``AgentTool.definition`` values
    are sent to the model, and each tool call is dispatched to the matching
    ``AgentTool`` through ``execute``.

    Every round builds a ``Context`` from the whole history. A reply whose
    ``stop_reason`` is ``"toolUse"`` is kept, one result message per tool
    call is appended after it, and the model is called again. Any other
    ``stop_reason`` ends the run with that reply unchanged; so does a
    ``"toolUse"`` reply that contains no tool call.

    A tool call whose ``arguments_error`` is set is not executed; it gets a
    failed result carrying the parse error. A call naming an unknown tool
    gets a failed result as well. Both keep the original ``tool_call_id``.

    Returns the messages produced by this run in order, ending with the
    assistant message that stopped it. The initial messages are not included.
    """
    tool_list = _bind_tools(tools)
    definitions = tuple(tool.definition for tool in tool_list)
    tools_by_name = {tool.definition.name: tool for tool in tool_list}

    history: list[Message] = list(messages)
    produced: list[Message] = []

    while True:
        context = Context(
            messages=history,
            system_prompt=system_prompt,
            tools=definitions,
        )
        reply = await complete(context)
        produced.append(reply)
        history.append(reply)

        if reply.stop_reason != "toolUse":
            return tuple(produced)

        calls = tuple(
            block for block in reply.content if isinstance(block, ToolCall)
        )
        if not calls:
            return tuple(produced)

        for call in calls:
            result = await _run_tool_call(call, tools_by_name)
            produced.append(result)
            history.append(result)
