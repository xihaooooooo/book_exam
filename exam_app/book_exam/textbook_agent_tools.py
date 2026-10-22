"""Model-facing textbook tools backed by one stored textbook."""

import asyncio
import json
from typing import Any, Callable

from .agent import AgentTool, TextContent, ToolDefinition, ToolExecutionResult
from .book_context import BookContext
from .textbook_tools import (
    get_section_text,
    get_surrounding_context,
    peek_section,
    search_keyword,
)

_SECTION_ID_DESCRIPTION = (
    '填写已有小节 ID，例如 "3.2"；'
    "也支持章概述等非 N.M 格式的已有 ID。"
)


def _section_id_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": _SECTION_ID_DESCRIPTION,
            }
        },
        "required": ["section_id"],
        "additionalProperties": False,
    }


def _keyword_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "要搜索的词组，去除首尾空白后为 1～100 个字符。",
            }
        },
        "required": ["keyword"],
        "additionalProperties": False,
    }


GET_SECTION_TEXT_TOOL = ToolDefinition(
    name="get_section_text",
    description=(
        "读取当前教材指定小节的完整原文，适用于详细阅读或核对出题依据。"
        "section_id 使用目录或搜索结果提供的原始 ID。"
        "返回章节、标题、页码和完整正文。"
    ),
    parameters=_section_id_parameters(),
)


PEEK_SECTION_TOOL = ToolDefinition(
    name="peek_section",
    description=(
        "查看当前教材指定小节的开头，最多返回 500 个字符，"
        "并用 truncated 标记说明是否截断。"
        "需要完整原文时请使用 get_section_text。"
    ),
    parameters=_section_id_parameters(),
)


SEARCH_KEYWORD_TOOL = ToolDefinition(
    name="search_keyword",
    description=(
        "按词组搜索当前教材的小节标题和正文，最多返回 5 条结果，"
        "每条包含命中附近的片段，并用 has_more 表示是否还有更多。"
        "需要完整原文时请调用 get_section_text。"
    ),
    parameters=_keyword_parameters(),
)


GET_SURROUNDING_CONTEXT_TOOL = ToolDefinition(
    name="get_surrounding_context",
    description=(
        "按教材顺序读取目标小节前后各一节的完整原文，允许跨章；"
        "缺少邻节时返回 null。"
        "目标正文可调用 get_section_text 获取。"
    ),
    parameters=_section_id_parameters(),
)


def _make_textbook_tool(
    definition: ToolDefinition,
    reader: Callable[[BookContext, str], dict[str, object]],
    book_context: BookContext,
    argument_name: str,
) -> AgentTool:
    async def handle(arguments: dict[str, Any]) -> ToolExecutionResult:
        business_result = await asyncio.to_thread(
            reader,
            book_context,
            arguments[argument_name],
        )
        return ToolExecutionResult(
            content=(
                TextContent(
                    text=json.dumps(business_result, ensure_ascii=False)
                ),
            ),
            details=business_result,
            is_error=not business_result["ok"],
        )

    return AgentTool(definition=definition, handler=handle)


def build_textbook_tools(book_context: BookContext) -> tuple[AgentTool, ...]:
    """Build the four textbook reading tools bound to one stored textbook."""
    return (
        _make_textbook_tool(
            GET_SECTION_TEXT_TOOL,
            get_section_text,
            book_context,
            "section_id",
        ),
        _make_textbook_tool(
            PEEK_SECTION_TOOL,
            peek_section,
            book_context,
            "section_id",
        ),
        _make_textbook_tool(
            SEARCH_KEYWORD_TOOL,
            search_keyword,
            book_context,
            "keyword",
        ),
        _make_textbook_tool(
            GET_SURROUNDING_CONTEXT_TOOL,
            get_surrounding_context,
            book_context,
            "section_id",
        ),
    )
