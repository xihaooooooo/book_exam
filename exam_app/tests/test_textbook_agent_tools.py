import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from book_exam import textbook_agent_tools
from book_exam.agent import AgentTool, TextContent, ToolDefinition
from book_exam.book_context import BookContext
from book_exam.search import load_simple_extension, rebuild_section_index
from book_exam.textbook_agent_tools import build_textbook_tools

TOOL_NAMES = (
    "get_section_text",
    "peek_section",
    "search_keyword",
    "get_surrounding_context",
)

TOOL_CASES = (
    ("get_section_text", "section_id", "3.2"),
    ("peek_section", "section_id", "3.2"),
    ("search_keyword", "keyword", "死锁"),
    ("get_surrounding_context", "section_id", "3.2"),
)


def book_context(book_id: str) -> BookContext:
    return BookContext(
        book_id=book_id,
        database_path=Path("unused") / book_id / "sections.db",
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


def section_row(
    section_id: str,
    text: str,
    *,
    chapter: str = "第1章 总论",
    title: str | None = None,
    sort_order: int = 1,
    page_start: int = 1,
    page_end: int = 1,
) -> tuple[str, str, str, int, int, int, str]:
    return (
        section_id,
        chapter,
        title if title is not None else f"{section_id} 小节",
        sort_order,
        page_start,
        page_end,
        text,
    )


def create_sections_database(directory: Path, rows) -> Path:
    """Create a sections database and close the write connection before returning."""
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / "sections.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(SECTIONS_TABLE_SQL)
        connection.executemany(INSERT_SECTION_SQL, rows)
        connection.commit()
    return database_path


def create_search_database(directory: Path, rows) -> Path:
    """Create a sections database with the real FTS index used by search."""
    database_path = create_sections_database(directory, rows)
    with closing(sqlite3.connect(database_path)) as connection:
        load_simple_extension(connection)
        connection.execute("BEGIN IMMEDIATE")
        rebuild_section_index(connection)
        connection.commit()
    return database_path


class TextbookToolDefinitionTests(unittest.TestCase):
    def test_factory_returns_exactly_four_reading_tools(self) -> None:
        tools = build_textbook_tools(book_context("book-a"))
        names = tuple(tool.definition.name for tool in tools)

        self.assertEqual(names, TOOL_NAMES)
        self.assertNotIn("get_book_toc", names)
        for tool in tools:
            self.assertIsInstance(tool, AgentTool)
            self.assertIsInstance(tool.definition, ToolDefinition)

    def test_definitions_preserve_names_descriptions_and_parameters(self) -> None:
        tools = {
            tool.definition.name: tool
            for tool in build_textbook_tools(book_context("book-a"))
        }
        expectations = {
            "get_section_text": (
                "section_id",
                ("完整原文", "页码", "section_id"),
            ),
            "peek_section": (
                "section_id",
                ("500", "truncated", "get_section_text"),
            ),
            "search_keyword": (
                "keyword",
                ("5", "has_more", "get_section_text"),
            ),
            "get_surrounding_context": (
                "section_id",
                ("跨章", "null", "get_section_text"),
            ),
        }

        for name, (property_name, description_parts) in expectations.items():
            with self.subTest(tool=name):
                definition = tools[name].definition

                self.assertTrue(definition.description.strip())
                for part in description_parts:
                    self.assertIn(part, definition.description)

                parameters = definition.parameters
                self.assertEqual(parameters["type"], "object")
                self.assertEqual(set(parameters["properties"]), {property_name})
                self.assertEqual(parameters["required"], [property_name])
                self.assertIs(parameters["additionalProperties"], False)
                self.assertEqual(
                    parameters["properties"][property_name]["type"], "string"
                )
                for forbidden in ("context", "book_id", "database_path"):
                    self.assertNotIn(forbidden, parameters["properties"])
                    self.assertNotIn(forbidden, parameters["required"])

                metadata = {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": parameters,
                }
                self.assertEqual(
                    json.loads(json.dumps(metadata, ensure_ascii=False)),
                    metadata,
                )

        keyword_schema = tools["search_keyword"].definition.parameters[
            "properties"
        ]["keyword"]
        self.assertEqual(keyword_schema["minLength"], 1)
        self.assertEqual(keyword_schema["maxLength"], 100)


class TextbookToolFactoryTests(unittest.TestCase):
    def test_building_does_not_execute_readers(self) -> None:
        readers = {
            name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
            for name in TOOL_NAMES
        }

        with mock.patch.multiple(textbook_agent_tools, **readers):
            tools = build_textbook_tools(book_context("book-a"))

        self.assertEqual(len(tools), len(TOOL_NAMES))
        for reader in readers.values():
            reader.assert_not_called()


class TextbookToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_tool_calls_its_reader_and_wraps_the_result(self) -> None:
        calls = []
        business_results = {}
        readers = {}

        for name, argument_name, value in TOOL_CASES:
            business_result = {
                "ok": True,
                "book_id": "book-a",
                "tool": name,
                "value": value,
                "message": "中文内容",
            }
            business_results[name] = business_result

            def make_reader(name=name, business_result=business_result):
                def reader(context, argument):
                    calls.append((name, context.book_id, argument))
                    return business_result

                return reader

            readers[name] = make_reader()

        with mock.patch.multiple(textbook_agent_tools, **readers):
            tools = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-a"))
            }
            for name, argument_name, value in TOOL_CASES:
                with self.subTest(tool=name):
                    result = await tools[name].execute({argument_name: value})

                    self.assertIs(result.is_error, False)
                    self.assertIs(result.details, business_results[name])
                    self.assertEqual(len(result.content), 1)
                    self.assertIsInstance(result.content[0], TextContent)
                    self.assertEqual(
                        json.loads(result.content[0].text),
                        business_results[name],
                    )
                    self.assertIn("中文内容", result.content[0].text)

        self.assertEqual(
            calls,
            [
                (name, "book-a", value)
                for name, _, value in TOOL_CASES
            ],
        )

    async def test_business_errors_keep_code_message_and_raw_details(self) -> None:
        business_error = {
            "ok": False,
            "error": {
                "code": "SECTION_NOT_FOUND",
                "message": "Section '9.9' does not exist",
            },
        }

        def reader(context, section_id):
            return business_error

        with mock.patch.object(
            textbook_agent_tools, "get_section_text", new=reader
        ):
            tools = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-a"))
            }

        result = await tools["get_section_text"].execute({"section_id": "9.9"})

        self.assertIs(result.is_error, True)
        self.assertIs(result.details, business_error)
        self.assertIn("SECTION_NOT_FOUND", result.content[0].text)
        self.assertIn("does not exist", result.content[0].text)
        self.assertEqual(
            json.loads(result.content[0].text)["error"]["code"],
            "SECTION_NOT_FOUND",
        )

    async def test_number_section_id_is_rejected_before_reading(self) -> None:
        reader = mock.Mock(side_effect=AssertionError("reader must not run"))

        with mock.patch.object(
            textbook_agent_tools, "get_section_text", new=reader
        ):
            tools = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-a"))
            }

        result = await tools["get_section_text"].execute({"section_id": 3.2})

        self.assertIs(result.is_error, True)
        self.assertIn("section_id", result.content[0].text)
        reader.assert_not_called()

    async def test_readers_run_in_a_worker_thread(self) -> None:
        reader_threads = []

        def reader(context, section_id):
            reader_threads.append(threading.get_ident())
            return {"ok": True, "book_id": context.book_id}

        with mock.patch.object(
            textbook_agent_tools, "get_section_text", new=reader
        ):
            tools = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-a"))
            }

        result = await tools["get_section_text"].execute({"section_id": "3.2"})

        self.assertIs(result.is_error, False)
        self.assertEqual(len(reader_threads), 1)
        self.assertNotEqual(reader_threads[0], threading.get_ident())

    async def test_factories_do_not_share_book_context(self) -> None:
        def reader(context, section_id):
            return {
                "ok": True,
                "book_id": context.book_id,
                "section_id": section_id,
            }

        with mock.patch.object(
            textbook_agent_tools, "get_section_text", new=reader
        ):
            tools_a = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-a"))
            }
            tools_b = {
                tool.definition.name: tool
                for tool in build_textbook_tools(book_context("book-b"))
            }

        result_a = await tools_a["get_section_text"].execute(
            {"section_id": "3.2"}
        )
        result_b = await tools_b["get_section_text"].execute(
            {"section_id": "3.2"}
        )

        self.assertEqual(result_a.details["book_id"], "book-a")
        self.assertEqual(result_b.details["book_id"], "book-b")


class TextbookToolDatabaseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)

    def _tools(
        self, book_id: str, database_path: Path
    ) -> dict[str, AgentTool]:
        context = BookContext(book_id=book_id, database_path=database_path)
        return {
            tool.definition.name: tool
            for tool in build_textbook_tools(context)
        }

    def _assert_success(self, result, book_id: str) -> None:
        self.assertIs(result.is_error, False)
        self.assertIsInstance(result.details, dict)
        self.assertIs(result.details["ok"], True)
        self.assertEqual(result.details["book_id"], book_id)
        self.assertEqual(len(result.content), 1)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertEqual(json.loads(result.content[0].text), result.details)

    async def test_get_section_text_reads_real_database(self) -> None:
        text = "第3章概述正文。\n第二段：并发控制。"
        database_path = create_sections_database(
            self.root / "book-real",
            (
                section_row(
                    "chapter-3-overview",
                    text,
                    chapter="第3章 并发控制",
                    title="第3章概述",
                    sort_order=1,
                    page_start=30,
                    page_end=31,
                ),
            ),
        )
        tools = self._tools("book-real", database_path)

        result = await tools["get_section_text"].execute(
            {"section_id": "chapter-3-overview"}
        )

        self._assert_success(result, "book-real")
        section = result.details["section"]
        self.assertEqual(section["section_id"], "chapter-3-overview")
        self.assertIsInstance(section["section_id"], str)
        self.assertEqual(section["chapter"], "第3章 并发控制")
        self.assertEqual(section["title"], "第3章概述")
        self.assertEqual(section["text"], text)
        self.assertEqual(section["page_start"], 30)
        self.assertEqual(section["page_end"], 31)
        self.assertIn("第3章概述正文", result.content[0].text)

    async def test_peek_section_truncates_real_text(self) -> None:
        body = "开头" + "原" * 700 + "结尾标记"
        database_path = create_sections_database(
            self.root / "book-peek",
            (section_row("3.2", body, title="3.2 长正文"),),
        )
        tools = self._tools("book-peek", database_path)

        result = await tools["peek_section"].execute({"section_id": "3.2"})

        self._assert_success(result, "book-peek")
        section = result.details["section"]
        self.assertEqual(section["section_id"], "3.2")
        self.assertEqual(section["text"], body[:500])
        self.assertIs(section["truncated"], True)
        self.assertNotIn("结尾标记", section["text"])

    async def test_search_keyword_uses_real_database_index(self) -> None:
        database_path = create_search_database(
            self.root / "book-search",
            (
                section_row(
                    "3.1",
                    "进程与线程的基本概念",
                    title="3.1 进程",
                    sort_order=1,
                ),
                section_row(
                    "3.2",
                    "死锁的预防与避免",
                    title="3.2 死锁",
                    sort_order=2,
                ),
                section_row("3.3", "内存管理", title="3.3 内存", sort_order=3),
            ),
        )
        tools = self._tools("book-search", database_path)

        result = await tools["search_keyword"].execute({"keyword": "死锁"})

        self._assert_success(result, "book-search")
        matches = result.details["matches"]
        self.assertEqual(
            [match["section_id"] for match in matches], ["3.2"]
        )
        self.assertIn("死锁", matches[0]["snippet"])
        self.assertIs(result.details["has_more"], False)
        self.assertEqual(result.details["keyword"], "死锁")

    async def test_get_surrounding_context_uses_real_neighbors(self) -> None:
        database_path = create_sections_database(
            self.root / "book-neighbors",
            (
                section_row(
                    "1.1",
                    "首节正文",
                    title="1.1 首节",
                    sort_order=10,
                ),
                section_row(
                    "2.1",
                    "目标正文",
                    title="2.1 目标",
                    sort_order=20,
                ),
                section_row(
                    "3.1",
                    "末节正文",
                    title="3.1 末节",
                    sort_order=30,
                ),
            ),
        )
        tools = self._tools("book-neighbors", database_path)

        result = await tools["get_surrounding_context"].execute(
            {"section_id": "2.1"}
        )

        self._assert_success(result, "book-neighbors")
        self.assertEqual(result.details["target_section_id"], "2.1")
        self.assertEqual(
            result.details["previous"]["section_id"], "1.1"
        )
        self.assertEqual(
            result.details["previous"]["text"], "首节正文"
        )
        self.assertEqual(result.details["next"]["section_id"], "3.1")
        self.assertEqual(result.details["next"]["text"], "末节正文")

    async def test_same_section_id_in_two_databases_stays_isolated(
        self,
    ) -> None:
        first_text = "甲教材 3.2 正文"
        second_text = "乙教材 3.2 正文"
        first_database = create_sections_database(
            self.root / "book-first",
            (section_row("3.2", first_text),),
        )
        second_database = create_sections_database(
            self.root / "book-second",
            (section_row("3.2", second_text),),
        )
        first_tools = self._tools("book-first", first_database)
        second_tools = self._tools("book-second", second_database)

        first_result = await first_tools["get_section_text"].execute(
            {"section_id": "3.2"}
        )
        second_result = await second_tools["get_section_text"].execute(
            {"section_id": "3.2"}
        )

        self._assert_success(first_result, "book-first")
        self._assert_success(second_result, "book-second")
        self.assertEqual(
            first_result.details["section"]["text"], first_text
        )
        self.assertEqual(
            second_result.details["section"]["text"], second_text
        )
        self.assertNotEqual(
            first_result.details["section"]["text"],
            second_result.details["section"]["text"],
        )


if __name__ == "__main__":
    unittest.main()
