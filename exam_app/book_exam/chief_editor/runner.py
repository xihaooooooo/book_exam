"""Start the chief editor with the textbook table of contents and request."""

from typing import Awaitable, Callable, Sequence, cast

from ..agent import (
    AgentRun,
    AssistantMessage,
    Context,
    TextContent,
    UserMessage,
    run_agent_loop,
)
from ..book_context import BookContext
from ..book_toc import get_book_toc
from ..textbook_agent_tools import build_textbook_tools
from .prompt import CHIEF_EDITOR_SYSTEM_PROMPT
from .quota import ChiefEditorQuota, calculate_quota
from .request import ChiefEditorRequest


class ChiefEditorStartError(RuntimeError):
    """Raised when the textbook table of contents cannot be read.

    ``code`` and ``message`` are copied unchanged from ``get_book_toc``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _format_toc(
    book_id: str,
    sections: Sequence[dict[str, object]],
) -> str:
    lines = [
        f"教材标识：{book_id}",
        "教材目录（按阅读顺序）：",
    ]
    current_chapter: object = None
    for section in sections:
        chapter = section["chapter"]
        if chapter != current_chapter:
            if len(lines) > 2:
                lines.append("")
            lines.append(str(chapter))
            current_chapter = chapter
        lines.append(f"- [{section['section_id']}] {section['title']}")
    return "\n".join(lines)


def _format_request(
    request: ChiefEditorRequest,
    quota: ChiefEditorQuota,
) -> str:
    lines = [
        "本次出题任务",
        "出题重点（编号从 0 开始）：",
    ]
    lines.extend(
        f"{index}. {focus}"
        for index, focus in enumerate(request.focuses)
    )
    lines.extend(
        [
            "",
            f"总题数：{quota.target_count}",
            "各重点配额：",
        ]
    )
    lines.extend(
        f"{index}. {focus}：{count} 题"
        for index, (focus, count) in enumerate(
            zip(request.focuses, quota.focus_counts)
        )
    )
    return "\n".join(lines)


async def run_chief_editor(
    book_context: BookContext,
    request: ChiefEditorRequest,
    *,
    complete: Callable[[Context], Awaitable[AssistantMessage]],
) -> AgentRun:
    """Start the chief editor with the textbook TOC and request."""
    toc = get_book_toc(book_context)
    if not toc["ok"]:
        error = cast(dict[str, str], toc["error"])
        raise ChiefEditorStartError(error["code"], error["message"])

    quota = calculate_quota(request)
    sections = cast(list[dict[str, object]], toc["sections"])
    messages = (
        UserMessage(
            content=(
                TextContent(
                    text=_format_toc(book_context.book_id, sections)
                ),
            )
        ),
        UserMessage(
            content=(TextContent(text=_format_request(request, quota)),)
        ),
    )

    return await run_agent_loop(
        messages,
        system_prompt=CHIEF_EDITOR_SYSTEM_PROMPT,
        tools=build_textbook_tools(book_context),
        complete=complete,
    )
