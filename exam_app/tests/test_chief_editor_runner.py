import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import book_exam.chief_editor.runner as runner_module
from book_exam.agent import (
    AgentRun,
    AssistantMessage,
    Context,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from book_exam.book_context import BookContext
from book_exam.book_toc import get_book_toc
from book_exam.chief_editor import (
    CHIEF_EDITOR_SYSTEM_PROMPT,
    ChiefEditorRequest,
)
from book_exam.chief_editor.runner import (
    ChiefEditorStartError,
    run_chief_editor,
)

SECTIONS_TABLE_SQL = """
CREATE TABLE sections (
    id TEXT PRIMARY KEY,
    chapter TEXT NOT NULL,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL UNIQUE CHECK (sort_order > 0),
    page_start INTEGER NOT NULL CHECK (page_start > 0),
    page_end INTEGER NOT NULL CHECK (page_end >= page_start),
    text TEXT NOT NULL
)
"""

INSERT_SECTION_SQL = """
INSERT INTO sections (id, chapter, title, sort_order, page_start, page_end, text)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

TOOL_NAMES = (
    "get_section_text",
    "peek_section",
    "search_keyword",
    "get_surrounding_context",
)

SECTION_TEXT = "进程是资源分配的基本单位，线程是调度的基本单位。"

ROWS = (
    (
        "1.1",
        "第1章 操作系统概述",
        "操作系统的基本概念",
        1,
        1,
        2,
        "操作系统是管理计算机硬件与软件资源的程序。",
    ),
    (
        "1.2",
        "第1章 操作系统概述",
        "操作系统的发展",
        2,
        3,
        4,
        "操作系统经历了批处理、分时和实时阶段。",
    ),
    (
        "chapter-2-overview",
        "第2章 进程管理",
        "第2章概述",
        3,
        5,
        5,
        "本章讨论进程、线程与调度。",
    ),
    (
        "2.1",
        "第2章 进程管理",
        "进程与线程",
        4,
        6,
        8,
        SECTION_TEXT,
    ),
)


def create_sections_database(directory: Path, rows) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / "sections.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(SECTIONS_TABLE_SQL)
        connection.executemany(INSERT_SECTION_SQL, rows)
        connection.commit()
    return database_path


def assistant_message(content, stop_reason: str) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="deepseek",
        model="deepseek-flash",
        stop_reason=stop_reason,
    )


def requested_contexts(complete: mock.AsyncMock) -> list[Context]:
    return [call.args[0] for call in complete.await_args_list]


class ChiefEditorRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.request = ChiefEditorRequest(
            focuses=["操作系统概述", "进程管理"],
            target_count=5,
        )

    def _context(self, book_id: str = "book-a") -> BookContext:
        database_path = create_sections_database(
            self.root / book_id,
            ROWS,
        )
        return BookContext(book_id=book_id, database_path=database_path)

    async def test_starts_with_toc_and_task_then_serves_a_section_read(
        self,
    ) -> None:
        context = self._context()
        first_reply = assistant_message(
            (
                ToolCall(
                    id="call-1",
                    name="get_section_text",
                    arguments={"section_id": "2.1"},
                ),
            ),
            "toolUse",
        )
        final_reply = assistant_message(
            (TextContent(text="已完成出题计划。"),),
            "stop",
        )
        complete = mock.AsyncMock(side_effect=(first_reply, final_reply))

        with mock.patch.object(
            runner_module,
            "get_book_toc",
            wraps=get_book_toc,
        ) as toc_reader:
            run = await run_chief_editor(
                context,
                self.request,
                complete=complete,
            )

        toc_reader.assert_called_once_with(context)

        first_context, second_context = requested_contexts(complete)
        self.assertEqual(
            first_context.system_prompt,
            CHIEF_EDITOR_SYSTEM_PROMPT,
        )
        self.assertEqual(len(first_context.messages), 2)

        toc_message, task_message = first_context.messages
        self.assertIsInstance(toc_message, UserMessage)
        self.assertIsInstance(task_message, UserMessage)

        self.assertEqual(len(first_context.tools), len(TOOL_NAMES))
        self.assertEqual(
            tuple(tool.name for tool in first_context.tools),
            TOOL_NAMES,
        )
        self.assertNotIn(
            "get_book_toc",
            {tool.name for tool in first_context.tools},
        )

        self.assertEqual(len(toc_message.content), 1)
        self.assertIsInstance(toc_message.content[0], TextContent)
        self.assertEqual(
            toc_message.content[0].text,
            "教材标识：book-a\n"
            "教材目录（按阅读顺序）：\n"
            "第1章 操作系统概述\n"
            "- [1.1] 操作系统的基本概念\n"
            "- [1.2] 操作系统的发展\n"
            "\n"
            "第2章 进程管理\n"
            "- [chapter-2-overview] 第2章概述\n"
            "- [2.1] 进程与线程",
        )

        self.assertEqual(len(task_message.content), 1)
        self.assertIsInstance(task_message.content[0], TextContent)
        self.assertEqual(
            task_message.content[0].text,
            "本次出题任务\n"
            "出题重点（编号从 0 开始）：\n"
            "0. 操作系统概述\n"
            "1. 进程管理\n"
            "\n"
            "总题数：5\n"
            "各重点配额：\n"
            "0. 操作系统概述：3 题\n"
            "1. 进程管理：2 题",
        )

        self.assertEqual(complete.await_count, 2)
        self.assertEqual(len(second_context.messages), 4)
        self.assertIs(second_context.messages[0], toc_message)
        self.assertIs(second_context.messages[1], task_message)
        self.assertIs(second_context.messages[2], first_reply)

        tool_result = second_context.messages[3]
        self.assertIsInstance(tool_result, ToolResultMessage)
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertEqual(tool_result.tool_name, "get_section_text")
        self.assertIs(tool_result.is_error, False)
        self.assertEqual(len(tool_result.content), 1)
        self.assertIsInstance(tool_result.content[0], TextContent)

        payload = json.loads(tool_result.content[0].text)
        self.assertEqual(payload["book_id"], "book-a")
        self.assertEqual(payload["section"]["section_id"], "2.1")
        self.assertEqual(payload["section"]["chapter"], "第2章 进程管理")
        self.assertEqual(payload["section"]["title"], "进程与线程")
        self.assertEqual(payload["section"]["page_start"], 6)
        self.assertEqual(payload["section"]["page_end"], 8)
        self.assertEqual(payload["section"]["text"], SECTION_TEXT)
        self.assertEqual(tool_result.details, payload)

        self.assertIsInstance(run, AgentRun)
        self.assertIsNone(run.status)
        self.assertEqual(run.rounds, 2)
        self.assertEqual(len(run.messages), 3)
        self.assertIs(run.messages[0], first_reply)
        self.assertIs(run.messages[1], tool_result)
        self.assertIs(run.messages[2], final_reply)

    async def test_stops_before_model_when_toc_cannot_be_read(self) -> None:
        context = BookContext(
            book_id="missing-book",
            database_path=(
                self.root / "missing-book" / "sections.db"
            ),
        )
        complete = mock.AsyncMock()

        with self.assertRaises(ChiefEditorStartError) as caught:
            await run_chief_editor(
                context,
                self.request,
                complete=complete,
            )

        error = caught.exception
        self.assertEqual(error.code, "BOOK_NOT_READY")
        self.assertEqual(
            error.message,
            f"Textbook database does not exist: {context.database_path}",
        )
        self.assertIn("BOOK_NOT_READY", str(error))
        complete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
