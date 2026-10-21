import dataclasses
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest import mock

from book_exam import textbook_tools
from book_exam.book_context import BookContext
from book_exam.search import (
    SimpleExtensionError,
    load_simple_extension,
    rebuild_section_index,
)
from book_exam.textbook_tools import (
    GET_SECTION_TEXT_TOOL,
    GET_SURROUNDING_CONTEXT_TOOL,
    PEEK_SECTION_TOOL,
    SEARCH_KEYWORD_TOOL,
    get_section_text,
    get_surrounding_context,
    peek_section,
    search_keyword,
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

_ORIGINAL_CONNECT = sqlite3.connect


class TrackingConnection(sqlite3.Connection):
    """Records connections opened by the tool and probes their write access."""

    instances: list["TrackingConnection"] = []
    fail_section_query = False
    fail_search_query = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        TrackingConnection.instances.append(self)
        self.write_rejected = False
        try:
            self.execute("CREATE TABLE probe_write (id INTEGER)")
        except sqlite3.OperationalError as error:
            self.write_rejected = "readonly" in str(error).lower()

    def execute(self, sql, parameters=()):
        if (
            TrackingConnection.fail_search_query
            and isinstance(sql, str)
            and "sections_fts MATCH" in sql
        ):
            raise sqlite3.OperationalError("simulated search failure")
        if (
            TrackingConnection.fail_section_query
            and isinstance(sql, str)
            and "FROM sections WHERE" in sql
        ):
            raise sqlite3.OperationalError("simulated read failure")
        return super().execute(sql, parameters)


def tracking_connect(*args, **kwargs):
    kwargs["factory"] = TrackingConnection
    return _ORIGINAL_CONNECT(*args, **kwargs)


def deny_exists(*args, **kwargs):
    raise PermissionError("simulated permission error")


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
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / "sections.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(SECTIONS_TABLE_SQL)
        connection.executemany(INSERT_SECTION_SQL, rows)
        connection.commit()
    return database_path


def create_search_database(directory: Path, rows) -> Path:
    database_path = create_sections_database(directory, rows)
    with closing(sqlite3.connect(database_path)) as connection:
        load_simple_extension(connection)
        connection.execute("BEGIN IMMEDIATE")
        rebuild_section_index(connection)
        connection.commit()
    return database_path


class TextbookToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)

    def _context(self, book_id: str, rows) -> BookContext:
        database_path = create_sections_database(self.root / book_id, rows)
        return BookContext(book_id=book_id, database_path=database_path)

    def _tableless_context(self, book_id: str) -> BookContext:
        directory = self.root / book_id
        directory.mkdir()
        database_path = directory / "sections.db"
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("CREATE TABLE books (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO books VALUES ('other')")
            connection.commit()
        return BookContext(book_id=book_id, database_path=database_path)

    def _assert_error(self, result, code: str) -> None:
        self.assertIs(result["ok"], False)
        self.assertNotIn("section", result)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["message"], str)
        self.assertTrue(result["error"]["message"].strip())


class GetSectionTextTests(TextbookToolsTestCase):
    def test_returns_complete_section_text(self) -> None:
        text = (
            "第 3.2 节 极限的定义\n\n"
            + "当自变量无限接近某一点时，函数值趋于某个确定的值。" * 60
            + "\n\n结束标记：EOF"
        )
        context = self._context(
            "book-long",
            (
                section_row(
                    "3.2",
                    text,
                    chapter="第3章 极限",
                    title="3.2 极限的定义",
                    page_start=10,
                    page_end=12,
                ),
            ),
        )

        result = get_section_text(context, "3.2")

        self.assertIs(result["ok"], True)
        self.assertEqual(result["book_id"], "book-long")
        section = result["section"]
        self.assertEqual(section["section_id"], "3.2")
        self.assertEqual(section["chapter"], "第3章 极限")
        self.assertEqual(section["title"], "3.2 极限的定义")
        self.assertEqual(section["page_start"], 10)
        self.assertEqual(section["page_end"], 12)
        self.assertEqual(section["text"], text)
        self.assertIn("\n\n", section["text"])
        self.assertGreater(len(section["text"]), 1000)

    def test_keeps_textbooks_isolated(self) -> None:
        first = self._context(
            "book-first", (section_row("2.1", "甲教材正文", title="2.1 甲节"),)
        )
        second = self._context(
            "book-second", (section_row("2.1", "乙教材正文", title="2.1 乙节"),)
        )

        first_result = get_section_text(first, "2.1")
        second_result = get_section_text(second, "2.1")

        self.assertIs(first_result["ok"], True)
        self.assertIs(second_result["ok"], True)
        self.assertEqual(first_result["book_id"], "book-first")
        self.assertEqual(second_result["book_id"], "book-second")
        self.assertEqual(first_result["section"]["text"], "甲教材正文")
        self.assertEqual(second_result["section"]["text"], "乙教材正文")
        self.assertNotEqual(
            first_result["section"]["text"], second_result["section"]["text"]
        )

    def test_accepts_existing_non_numeric_section_ids(self) -> None:
        context = self._context(
            "book-special",
            (
                section_row(
                    "chapter-3-overview",
                    "章概述正文",
                    chapter="第3章",
                    title="第3章概述",
                    sort_order=1,
                ),
                section_row(
                    "unsectioned-1",
                    "未分节正文",
                    chapter="未分节",
                    title="未分节内容",
                    sort_order=2,
                ),
            ),
        )

        for section_id, expected_text in (
            ("chapter-3-overview", "章概述正文"),
            ("unsectioned-1", "未分节正文"),
        ):
            with self.subTest(section_id=section_id):
                result = get_section_text(context, section_id)
                self.assertIs(result["ok"], True)
                self.assertEqual(result["section"]["section_id"], section_id)
                self.assertEqual(result["section"]["text"], expected_text)

    def test_rejects_invalid_section_ids(self) -> None:
        context = self._context("book-invalid", (section_row("3.2", "正文"),))

        for value in ("", "   ", "\t\r\n", None, 3.2, 3, b"3.2", ["3.2"]):
            with self.subTest(value=value):
                self._assert_error(
                    get_section_text(context, value), "INVALID_ARGUMENT"
                )

    def test_reports_missing_section(self) -> None:
        context = self._context("book-missing", (section_row("3.2", "正文"),))

        self._assert_error(get_section_text(context, "4.4"), "SECTION_NOT_FOUND")

    def test_treats_injection_like_ids_as_literal_text(self) -> None:
        context = self._context(
            "book-injection",
            (
                section_row("3.2", "真实正文", sort_order=1),
                section_row("3.3", "另一节正文", sort_order=2),
            ),
        )

        for section_id in (
            "3.2' OR '1'='1",
            "3.2'--",
            "'; DROP TABLE sections; --",
            "1; DELETE FROM sections",
        ):
            with self.subTest(section_id=section_id):
                self._assert_error(
                    get_section_text(context, section_id), "SECTION_NOT_FOUND"
                )

        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id, text FROM sections ORDER BY id"
                ).fetchall(),
                [("3.2", "真实正文"), ("3.3", "另一节正文")],
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections'"
                ).fetchone()
            )
        self.assertIs(get_section_text(context, "3.2")["ok"], True)

    def test_reports_missing_database_without_creating_it(self) -> None:
        database_path = self.root / "missing-book" / "sections.db"
        context = BookContext(book_id="book-missing", database_path=database_path)

        result = get_section_text(context, "3.2")

        self._assert_error(result, "BOOK_NOT_READY")
        self.assertFalse(database_path.exists())
        self.assertFalse(database_path.parent.exists())

    def test_reports_database_without_sections_table(self) -> None:
        context = self._tableless_context("book-tableless")

        self._assert_error(get_section_text(context, "3.2"), "BOOK_NOT_READY")

        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute("SELECT id FROM books").fetchall(), [("other",)]
            )

    def test_reports_corrupt_database(self) -> None:
        directory = self.root / "corrupt-book"
        directory.mkdir()
        database_path = directory / "sections.db"
        database_path.write_bytes(b"this file is not a sqlite database" * 8)
        context = BookContext(book_id="book-corrupt", database_path=database_path)

        self._assert_error(get_section_text(context, "3.2"), "READ_FAILED")

    def test_reports_unreadable_path_instead_of_raising(self) -> None:
        context = self._context("book-permission", (section_row("3.2", "正文"),))

        with mock.patch.object(Path, "exists", deny_exists):
            result = get_section_text(context, "3.2")

        self._assert_error(result, "READ_FAILED")

    def test_reads_database_under_awkward_directory_name(self) -> None:
        database_path = create_sections_database(
            self.root / "教材 目录 #1",
            (section_row("3.2", "含井号路径正文", title="3.2 标题"),),
        )
        context = BookContext(book_id="book-awkward", database_path=database_path)

        result = get_section_text(context, "3.2")

        self.assertIs(result["ok"], True)
        self.assertEqual(result["section"]["text"], "含井号路径正文")

    def test_book_context_is_immutable_and_slot_based(self) -> None:
        context = self._context("book-context", (section_row("3.2", "正文"),))

        self.assertEqual(
            [field.name for field in dataclasses.fields(context)],
            ["book_id", "database_path"],
        )
        self.assertFalse(hasattr(context, "__dict__"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.book_id = "other"

    def test_opens_read_only_connection_and_closes_it_on_every_branch(self) -> None:
        context = self._context(
            "book-tracked", (section_row("3.2", "正文", title="3.2 极限"),)
        )
        tableless_context = self._tableless_context("book-tracked-empty")

        scenarios = (
            ("success", context, "3.2", None),
            ("missing table", tableless_context, "3.2", "BOOK_NOT_READY"),
            ("missing section", context, "9.9", "SECTION_NOT_FOUND"),
            ("query failure", context, "3.2", "READ_FAILED"),
        )
        for label, scenario_context, section_id, expected_code in scenarios:
            with self.subTest(branch=label):
                result, connections = self._read_with_tracking(
                    scenario_context,
                    section_id,
                    fail_query=label == "query failure",
                )

                if expected_code is None:
                    self.assertIs(result["ok"], True)
                else:
                    self._assert_error(result, expected_code)

                self.assertEqual(len(connections), 1)
                connection = connections[0]
                self.assertTrue(
                    connection.write_rejected,
                    "textbook database must be opened read-only",
                )
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_tool_definition_binds_get_section_text_and_reads(self) -> None:
        self.assertEqual(GET_SECTION_TEXT_TOOL["name"], "get_section_text")
        self.assertIs(GET_SECTION_TEXT_TOOL["execute"], get_section_text)
        self.assertIsInstance(GET_SECTION_TEXT_TOOL["description"], str)
        self.assertTrue(GET_SECTION_TEXT_TOOL["description"].strip())

        parameters = GET_SECTION_TEXT_TOOL["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertEqual(set(parameters["properties"]), {"section_id"})
        self.assertEqual(parameters["properties"]["section_id"]["type"], "string")
        self.assertEqual(parameters["required"], ["section_id"])
        self.assertIs(parameters["additionalProperties"], False)
        for forbidden in ("context", "book_id", "database_path"):
            self.assertNotIn(forbidden, parameters["properties"])
            self.assertNotIn(forbidden, parameters["required"])

        metadata = {
            "name": GET_SECTION_TEXT_TOOL["name"],
            "description": GET_SECTION_TEXT_TOOL["description"],
            "parameters": GET_SECTION_TEXT_TOOL["parameters"],
        }
        self.assertEqual(
            json.loads(json.dumps(metadata, ensure_ascii=False)), metadata
        )

        context = self._context("book-tool", (section_row("3.2", "工具正文"),))
        arguments = {"section_id": "3.2"}

        result = GET_SECTION_TEXT_TOOL["execute"](context, **arguments)

        self.assertIs(result["ok"], True)
        self.assertEqual(result["section"]["text"], "工具正文")

    def _read_with_tracking(
        self, context: BookContext, section_id: str, *, fail_query: bool = False
    ):
        TrackingConnection.instances.clear()
        TrackingConnection.fail_section_query = fail_query
        try:
            with mock.patch.object(
                textbook_tools.sqlite3, "connect", new=tracking_connect
            ):
                result = get_section_text(context, section_id)
        finally:
            TrackingConnection.fail_section_query = False
        return result, list(TrackingConnection.instances)


class PeekSectionTests(TextbookToolsTestCase):
    def test_previews_lengths_and_sets_truncation_flag(self) -> None:
        lengths = (0, 499, 500, 501, 1200)
        rows = tuple(
            section_row(
                f"len-{length}",
                "文" * length,
                title=f"{length} 字小节",
                sort_order=index + 1,
                page_start=index + 1,
                page_end=index + 1,
            )
            for index, length in enumerate(lengths)
        )
        context = self._context("book-lengths", rows)

        for index, length in enumerate(lengths):
            with self.subTest(length=length):
                body = "文" * length

                result = peek_section(context, f"len-{length}")

                self.assertIs(result["ok"], True)
                self.assertEqual(result["book_id"], "book-lengths")
                section = result["section"]
                self.assertEqual(section["section_id"], f"len-{length}")
                self.assertEqual(section["chapter"], "第1章 总论")
                self.assertEqual(section["title"], f"{length} 字小节")
                self.assertEqual(section["page_start"], index + 1)
                self.assertEqual(section["page_end"], index + 1)
                self.assertEqual(section["text"], body[:500])
                self.assertIs(section["truncated"], length > 500)

    def test_preserves_whitespace_newlines_and_unicode(self) -> None:
        body = "  行首空格\n\n第二段\t制表符\n🙂 café Ω\n" + "填充" * 300
        self.assertGreater(len(body), 500)
        context = self._context("book-unicode", (section_row("3.2", body),))

        result = peek_section(context, "3.2")

        self.assertIs(result["ok"], True)
        section = result["section"]
        self.assertEqual(section["text"], body[:500])
        self.assertTrue(section["text"].startswith("  行首空格\n\n第二段"))
        self.assertIn("\t制表符", section["text"])
        self.assertIn("🙂", section["text"])
        self.assertIn("café", section["text"])
        self.assertNotIn("…", section["text"])
        self.assertIs(section["truncated"], True)

    def test_keeps_metadata_isolation_and_non_numeric_ids(self) -> None:
        section_id = "chapter-3-overview"
        first = self._context(
            "book-overview-a",
            (
                section_row(
                    section_id,
                    "短正文",
                    chapter="第3章 概述",
                    title="第3章概述",
                    page_start=30,
                    page_end=31,
                ),
            ),
        )
        second = self._context(
            "book-overview-b",
            (
                section_row(
                    section_id,
                    "长" * 600,
                    chapter="第3章 概述",
                    title="第3章概述",
                    page_start=30,
                    page_end=31,
                ),
            ),
        )

        first_result = peek_section(first, section_id)
        second_result = peek_section(second, section_id)

        self.assertIs(first_result["ok"], True)
        self.assertIs(second_result["ok"], True)
        self.assertEqual(first_result["book_id"], "book-overview-a")
        self.assertEqual(second_result["book_id"], "book-overview-b")
        first_section = first_result["section"]
        self.assertEqual(first_section["section_id"], section_id)
        self.assertEqual(first_section["chapter"], "第3章 概述")
        self.assertEqual(first_section["title"], "第3章概述")
        self.assertEqual(first_section["page_start"], 30)
        self.assertEqual(first_section["page_end"], 31)
        self.assertEqual(first_section["text"], "短正文")
        self.assertIs(first_section["truncated"], False)
        self.assertEqual(second_result["section"]["text"], "长" * 500)
        self.assertIs(second_result["section"]["truncated"], True)

    def test_passes_through_errors_unchanged(self) -> None:
        context = self._context("book-errors", (section_row("3.2", "正文"),))
        tableless = self._tableless_context("book-errors-tableless")
        missing = BookContext(
            book_id="book-errors-missing",
            database_path=self.root / "missing-book" / "sections.db",
        )
        corrupt_directory = self.root / "book-errors-corrupt"
        corrupt_directory.mkdir()
        corrupt_path = corrupt_directory / "sections.db"
        corrupt_path.write_bytes(b"this file is not a sqlite database" * 8)
        corrupt = BookContext(
            book_id="book-errors-corrupt", database_path=corrupt_path
        )

        scenarios = (
            ("invalid argument", context, "   ", "INVALID_ARGUMENT"),
            ("missing section", context, "9.9", "SECTION_NOT_FOUND"),
            ("missing database", missing, "3.2", "BOOK_NOT_READY"),
            ("missing table", tableless, "3.2", "BOOK_NOT_READY"),
            ("read failure", corrupt, "3.2", "READ_FAILED"),
        )
        for label, scenario_context, section_id, expected_code in scenarios:
            with self.subTest(case=label):
                expected = get_section_text(scenario_context, section_id)

                result = peek_section(scenario_context, section_id)

                self._assert_error(result, expected_code)
                self.assertEqual(result, expected)

    def test_does_not_modify_the_reused_result(self) -> None:
        body = "原" * 800
        context = self._context("book-reuse", (section_row("3.2", body),))
        captured: dict[str, dict[str, object]] = {}
        real_get_section_text = textbook_tools.get_section_text

        def recording_get_section_text(scenario_context, scenario_section_id):
            result = real_get_section_text(scenario_context, scenario_section_id)
            captured["result"] = result
            return result

        with mock.patch.object(
            textbook_tools, "get_section_text", recording_get_section_text
        ):
            result = peek_section(context, "3.2")

        original = captured["result"]
        self.assertEqual(original["section"]["text"], body)
        self.assertNotIn("truncated", original["section"])
        self.assertEqual(len(original["section"]), 6)
        self.assertIsNot(result, original)
        self.assertIsNot(result["section"], original["section"])
        self.assertEqual(result["section"]["text"], body[:500])
        self.assertIs(result["section"]["truncated"], True)

    def test_tool_definition_binds_peek_section_and_reads(self) -> None:
        self.assertEqual(PEEK_SECTION_TOOL["name"], "peek_section")
        self.assertIs(PEEK_SECTION_TOOL["execute"], peek_section)
        description = PEEK_SECTION_TOOL["description"]
        self.assertIsInstance(description, str)
        self.assertIn("500", description)
        self.assertIn("截断", description)
        self.assertIn("get_section_text", description)

        parameters = PEEK_SECTION_TOOL["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertEqual(set(parameters["properties"]), {"section_id"})
        self.assertEqual(parameters["properties"]["section_id"]["type"], "string")
        self.assertEqual(parameters["required"], ["section_id"])
        self.assertIs(parameters["additionalProperties"], False)
        for forbidden in ("context", "book_id", "database_path"):
            self.assertNotIn(forbidden, parameters["properties"])
            self.assertNotIn(forbidden, parameters["required"])

        metadata = {
            "name": PEEK_SECTION_TOOL["name"],
            "description": description,
            "parameters": parameters,
        }
        self.assertEqual(
            json.loads(json.dumps(metadata, ensure_ascii=False)), metadata
        )

        body = "工" * 600
        context = self._context("book-peek-tool", (section_row("3.2", body),))
        arguments = {"section_id": "3.2"}

        result = PEEK_SECTION_TOOL["execute"](context, **arguments)

        self.assertIs(result["ok"], True)
        self.assertEqual(result["section"]["text"], body[:500])
        self.assertIs(result["section"]["truncated"], True)


class SearchKeywordTests(TextbookToolsTestCase):
    def test_returns_matches_with_metadata(self) -> None:
        context = self._search_context(
            "book-metadata",
            (
                section_row(
                    "chapter-3-overview",
                    "本章讨论死锁与资源分配。",
                    chapter="第3章 并发",
                    title="第3章概述",
                    page_start=30,
                    page_end=31,
                ),
            ),
        )

        result = search_keyword(context, "死锁")

        self.assertIs(result["ok"], True)
        self.assertEqual(
            set(result), {"ok", "book_id", "keyword", "matches", "has_more"}
        )
        self.assertEqual(result["book_id"], "book-metadata")
        self.assertEqual(result["keyword"], "死锁")
        self.assertIs(result["has_more"], False)
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(
            set(match),
            {"section_id", "chapter", "title", "page_start", "page_end", "snippet"},
        )
        self.assertEqual(match["section_id"], "chapter-3-overview")
        self.assertEqual(match["chapter"], "第3章 并发")
        self.assertEqual(match["title"], "第3章概述")
        self.assertEqual(match["page_start"], 30)
        self.assertEqual(match["page_end"], 31)
        self.assertIn("死锁", match["snippet"])
        self.assertNotIn("text", match)

    def test_matches_contiguous_chinese_phrase_only(self) -> None:
        context = self._search_context(
            "book-phrase",
            (
                section_row("phrase-hit", "进程调度算法用于避免死锁", title="调度总览"),
                section_row("scattered", "进程与调度", title="分散字符", sort_order=2),
                section_row("reversed", "调度进程", title="顺序颠倒", sort_order=3),
            ),
        )

        result = search_keyword(context, "进程调度")

        self.assertIs(result["ok"], True)
        self.assertEqual(
            [match["section_id"] for match in result["matches"]], ["phrase-hit"]
        )

    def test_handles_english_case_and_internal_whitespace(self) -> None:
        context = self._search_context(
            "book-english",
            (
                section_row(
                    "os",
                    "Process Scheduling Algorithm",
                    chapter="Chapter 2",
                    title="Operating Systems",
                ),
            ),
        )

        for keyword in (
            "PROCESS",
            "process    scheduling",
            "process\tscheduling",
            "Algorithm",
        ):
            with self.subTest(keyword=keyword):
                result = search_keyword(context, keyword)
                self.assertIs(result["ok"], True)
                self.assertEqual(
                    [match["section_id"] for match in result["matches"]], ["os"]
                )

        for keyword in ("scheduling process", "Scheduling Algorithms"):
            with self.subTest(keyword=keyword):
                result = search_keyword(context, keyword)
                self.assertIs(result["ok"], True)
                self.assertEqual(result["matches"], [])

    def test_builds_snippet_from_the_matching_region(self) -> None:
        late_body = "开头标记" + "填充" * 100 + "死锁"
        context = self._search_context(
            "book-snippet",
            (
                section_row("title-only", "无关正文内容", title="死锁处理"),
                section_row("late-body", late_body, title="正文靠后", sort_order=2),
            ),
        )

        result = search_keyword(context, "死锁")

        self.assertIs(result["ok"], True)
        matches = {match["section_id"]: match for match in result["matches"]}
        self.assertEqual(set(matches), {"title-only", "late-body"})
        self.assertIn("死锁", matches["title-only"]["snippet"])
        self.assertIn("死锁", matches["late-body"]["snippet"])
        self.assertNotIn("开头标记", matches["late-body"]["snippet"])

    def test_treats_fts_syntax_as_literal_text(self) -> None:
        context = self._search_context(
            "book-syntax",
            (
                section_row("literal-bool", "进程 OR 调度", title="布尔字面量"),
                section_row("plain", "进程调度", title="普通顺序", sort_order=2),
                section_row("wildcard", "调度算法", title="通配字面量", sort_order=3),
                section_row(
                    "title-only", "无关正文内容", title="死锁处理", sort_order=4
                ),
                section_row(
                    "literal-colon", "title:死锁", title="列过滤字面量", sort_order=5
                ),
                section_row("quoted", 'say "hello"', title="引号字面量", sort_order=6),
            ),
        )

        for keyword, expected_ids in (
            ("进程 OR 调度", ["literal-bool"]),
            ("调度*", []),
            ("title:死锁", ["literal-colon"]),
            ('say "hello"', ["quoted"]),
            ("NEAR(死锁 调度)", []),
            ("'; DROP TABLE sections; --", []),
        ):
            with self.subTest(keyword=keyword):
                result = search_keyword(context, keyword)
                self.assertIs(result["ok"], True)
                self.assertEqual(
                    [match["section_id"] for match in result["matches"]],
                    expected_ids,
                )

        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sections").fetchone(), (6,)
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections_fts'"
                ).fetchone()
            )

        surviving_ids = {
            match["section_id"]
            for match in search_keyword(context, "死锁")["matches"]
        }
        self.assertEqual(surviving_ids, {"title-only", "literal-colon"})

    def test_limits_results_and_reports_has_more(self) -> None:
        five = self._search_context(
            "book-five",
            tuple(
                section_row(
                    f"five-{index}", "死锁", title=f"小节{index}", sort_order=index
                )
                for index in range(1, 6)
            ),
        )

        five_result = search_keyword(five, "死锁")

        self.assertIs(five_result["ok"], True)
        self.assertEqual(
            [match["section_id"] for match in five_result["matches"]],
            [f"five-{index}" for index in range(1, 6)],
        )
        self.assertIs(five_result["has_more"], False)

        seven = self._search_context(
            "book-seven",
            tuple(
                section_row(
                    f"seven-{index}", "死锁", title=f"小节{index}", sort_order=index
                )
                for index in range(1, 8)
            ),
        )

        seven_result = search_keyword(seven, "死锁")

        self.assertIs(seven_result["ok"], True)
        self.assertEqual(len(seven_result["matches"]), 5)
        self.assertEqual(
            [match["section_id"] for match in seven_result["matches"]],
            [f"seven-{index}" for index in range(1, 6)],
        )
        self.assertIs(seven_result["has_more"], True)

        empty = self._search_context(
            "book-empty", (section_row("empty-1", "死锁"),)
        )

        empty_result = search_keyword(empty, "没有出现过的词")

        self.assertIs(empty_result["ok"], True)
        self.assertEqual(empty_result["matches"], [])
        self.assertIs(empty_result["has_more"], False)

    def test_orders_by_relevance_then_sort_order(self) -> None:
        context = self._search_context(
            "book-ranking",
            (
                section_row("single", "死锁", title="一次命中"),
                section_row("repeated", "死锁" * 5, title="多次命中", sort_order=2),
            ),
        )

        result = search_keyword(context, "死锁")

        self.assertIs(result["ok"], True)
        self.assertEqual(
            [match["section_id"] for match in result["matches"]],
            ["repeated", "single"],
        )

    def test_keeps_textbooks_isolated(self) -> None:
        first = self._search_context(
            "book-a",
            (
                section_row(
                    "chapter-3-overview",
                    "甲教材死锁处理",
                    chapter="第3章",
                    title="甲教材概述",
                    page_start=30,
                    page_end=31,
                ),
            ),
        )
        second = self._search_context(
            "book-b",
            (
                section_row(
                    "chapter-3-overview",
                    "乙教材死锁处理",
                    chapter="第3章",
                    title="乙教材概述",
                    page_start=30,
                    page_end=31,
                ),
            ),
        )

        first_result = search_keyword(first, "死锁")
        second_result = search_keyword(second, "死锁")

        self.assertIs(first_result["ok"], True)
        self.assertIs(second_result["ok"], True)
        self.assertEqual(first_result["book_id"], "book-a")
        self.assertEqual(second_result["book_id"], "book-b")
        self.assertEqual(
            first_result["matches"][0]["section_id"], "chapter-3-overview"
        )
        self.assertEqual(
            second_result["matches"][0]["section_id"], "chapter-3-overview"
        )
        self.assertEqual(first_result["matches"][0]["title"], "甲教材概述")
        self.assertEqual(second_result["matches"][0]["title"], "乙教材概述")
        self.assertIn("甲教材", first_result["matches"][0]["snippet"])
        self.assertIn("乙教材", second_result["matches"][0]["snippet"])

    def test_rejects_invalid_keywords_without_opening_database(self) -> None:
        context = BookContext(
            book_id="book-invalid",
            database_path=self.root / "not-created" / "sections.db",
        )

        with mock.patch.object(
            textbook_tools.sqlite3,
            "connect",
            side_effect=AssertionError("database must not be opened"),
        ):
            for value in (
                None,
                3,
                3.2,
                b"keyword",
                ["keyword"],
                {"keyword": "x"},
                "",
                "   ",
                "\t\n",
                "x" * 101,
                "abc\x00def",
                "\x00",
            ):
                with self.subTest(value=value):
                    self._assert_error(
                        search_keyword(context, value), "INVALID_ARGUMENT"
                    )

        self.assertFalse(context.database_path.exists())

    def test_accepts_length_boundaries_and_trims_keyword(self) -> None:
        context = self._search_context(
            "book-bounds", (section_row("hit", "死锁处理"),)
        )

        trimmed = search_keyword(context, "  死锁  ")

        self.assertIs(trimmed["ok"], True)
        self.assertEqual(trimmed["keyword"], "死锁")
        self.assertEqual(
            [match["section_id"] for match in trimmed["matches"]], ["hit"]
        )

        longest = search_keyword(context, "x" * 100)

        self.assertIs(longest["ok"], True)
        self.assertEqual(longest["keyword"], "x" * 100)
        self.assertEqual(longest["matches"], [])

    def test_reports_error_codes_for_unready_databases(self) -> None:
        missing = BookContext(
            book_id="book-missing",
            database_path=self.root / "missing-book" / "sections.db",
        )
        tableless = self._tableless_context("book-tableless")
        unindexed = self._context(
            "book-unindexed", (section_row("3.2", "死锁"),)
        )

        for label, scenario_context, expected_code in (
            ("missing database", missing, "BOOK_NOT_READY"),
            ("missing table", tableless, "BOOK_NOT_READY"),
            ("missing index", unindexed, "INDEX_NOT_READY"),
        ):
            with self.subTest(case=label):
                self._assert_error(
                    search_keyword(scenario_context, "死锁"), expected_code
                )

        self.assertFalse(missing.database_path.exists())
        self.assertFalse(missing.database_path.parent.exists())
        with closing(sqlite3.connect(unindexed.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections_fts'"
                ).fetchone()
            )

    def test_reports_corrupt_database(self) -> None:
        directory = self.root / "corrupt-book"
        directory.mkdir()
        database_path = directory / "sections.db"
        database_path.write_bytes(b"this file is not a sqlite database" * 8)
        context = BookContext(book_id="book-corrupt", database_path=database_path)

        self._assert_error(search_keyword(context, "死锁"), "READ_FAILED")

    def test_reports_unreadable_path_instead_of_raising(self) -> None:
        context = self._search_context(
            "book-permission", (section_row("3.2", "死锁"),)
        )

        with mock.patch.object(Path, "exists", deny_exists):
            result = search_keyword(context, "死锁")

        self._assert_error(result, "READ_FAILED")

    def test_reports_search_unavailable_when_extension_fails(self) -> None:
        context = self._search_context(
            "book-extension", (section_row("3.2", "死锁"),)
        )

        for error in (
            SimpleExtensionError("simulated extension failure"),
            OSError("simulated load failure"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    textbook_tools, "load_simple_extension", side_effect=error
                ):
                    result = search_keyword(context, "死锁")
                self._assert_error(result, "SEARCH_UNAVAILABLE")

    def test_previous_tools_work_without_search_extension(self) -> None:
        context = self._context(
            "book-plain", (section_row("3.2", "完整正文", title="3.2 小节"),)
        )

        with mock.patch.object(
            textbook_tools,
            "load_simple_extension",
            side_effect=SimpleExtensionError("simulated extension failure"),
        ):
            full = get_section_text(context, "3.2")
            preview = peek_section(context, "3.2")

        self.assertIs(full["ok"], True)
        self.assertEqual(full["section"]["text"], "完整正文")
        self.assertIs(preview["ok"], True)
        self.assertEqual(preview["section"]["text"], "完整正文")
        self.assertIs(preview["section"]["truncated"], False)

    def test_opens_read_only_connection_and_closes_it_on_every_branch(self) -> None:
        indexed = self._search_context(
            "book-tracked", (section_row("3.2", "死锁处理", title="3.2 死锁"),)
        )
        tableless = self._tableless_context("book-tracked-tableless")
        unindexed = self._context(
            "book-tracked-unindexed", (section_row("3.2", "死锁"),)
        )

        scenarios = (
            ("success", indexed, None, None),
            ("missing table", tableless, "BOOK_NOT_READY", None),
            ("missing index", unindexed, "INDEX_NOT_READY", None),
            (
                "extension failure",
                indexed,
                "SEARCH_UNAVAILABLE",
                SimpleExtensionError("simulated extension failure"),
            ),
            ("query failure", indexed, "READ_FAILED", None),
        )
        for label, scenario_context, expected_code, extension_error in scenarios:
            with self.subTest(branch=label):
                result, connections = self._search_with_tracking(
                    scenario_context,
                    "死锁",
                    fail_query=label == "query failure",
                    fail_extension=extension_error,
                )

                if expected_code is None:
                    self.assertIs(result["ok"], True)
                else:
                    self._assert_error(result, expected_code)

                self.assertEqual(len(connections), 1)
                connection = connections[0]
                self.assertTrue(
                    connection.write_rejected,
                    "textbook database must be opened read-only",
                )
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_tool_definition_binds_search_keyword_and_reads(self) -> None:
        self.assertEqual(SEARCH_KEYWORD_TOOL["name"], "search_keyword")
        self.assertIs(SEARCH_KEYWORD_TOOL["execute"], search_keyword)
        description = SEARCH_KEYWORD_TOOL["description"]
        self.assertIsInstance(description, str)
        self.assertIn("5", description)
        self.assertIn("has_more", description)
        self.assertIn("get_section_text", description)

        parameters = SEARCH_KEYWORD_TOOL["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertEqual(set(parameters["properties"]), {"keyword"})
        keyword_schema = parameters["properties"]["keyword"]
        self.assertEqual(keyword_schema["type"], "string")
        self.assertEqual(keyword_schema["minLength"], 1)
        self.assertEqual(keyword_schema["maxLength"], 100)
        self.assertEqual(parameters["required"], ["keyword"])
        self.assertIs(parameters["additionalProperties"], False)
        for forbidden in ("context", "book_id", "database_path"):
            self.assertNotIn(forbidden, parameters["properties"])
            self.assertNotIn(forbidden, parameters["required"])

        metadata = {
            "name": SEARCH_KEYWORD_TOOL["name"],
            "description": description,
            "parameters": parameters,
        }
        self.assertEqual(
            json.loads(json.dumps(metadata, ensure_ascii=False)), metadata
        )

        context = self._search_context(
            "book-tool", (section_row("3.2", "死锁处理"),)
        )
        arguments = {"keyword": "死锁"}

        result = SEARCH_KEYWORD_TOOL["execute"](context, **arguments)

        self.assertIs(result["ok"], True)
        self.assertEqual(
            [match["section_id"] for match in result["matches"]], ["3.2"]
        )

    def _search_context(self, book_id: str, rows) -> BookContext:
        database_path = create_search_database(self.root / book_id, rows)
        return BookContext(book_id=book_id, database_path=database_path)

    def _search_with_tracking(
        self, context, keyword, *, fail_query=False, fail_extension=None
    ):
        TrackingConnection.instances.clear()
        TrackingConnection.fail_search_query = fail_query
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.object(
                        textbook_tools.sqlite3, "connect", new=tracking_connect
                    )
                )
                if fail_extension is not None:
                    stack.enter_context(
                        mock.patch.object(
                            textbook_tools,
                            "load_simple_extension",
                            side_effect=fail_extension,
                        )
                    )
                result = search_keyword(context, keyword)
        finally:
            TrackingConnection.fail_search_query = False
        return result, list(TrackingConnection.instances)


class GetSurroundingContextTests(TextbookToolsTestCase):
    def test_returns_previous_and_next_by_sort_order(self) -> None:
        context = self._context(
            "book-order",
            (
                section_row(
                    "zzz-middle",
                    "中间正文",
                    chapter="第2章 中间",
                    title="中间小节",
                    sort_order=20,
                    page_start=21,
                    page_end=22,
                ),
                section_row(
                    "aaa-first",
                    "首节正文",
                    chapter="第1章 开头",
                    title="首节",
                    sort_order=10,
                    page_start=1,
                    page_end=2,
                ),
                section_row(
                    "mmm-last",
                    "末节正文",
                    chapter="第3章 结尾",
                    title="末节",
                    sort_order=30,
                    page_start=31,
                    page_end=32,
                ),
            ),
        )

        middle = get_surrounding_context(context, "zzz-middle")

        self.assertIs(middle["ok"], True)
        self.assertEqual(
            set(middle),
            {"ok", "book_id", "target_section_id", "previous", "next"},
        )
        self.assertEqual(middle["book_id"], "book-order")
        self.assertEqual(middle["target_section_id"], "zzz-middle")
        self.assertEqual(middle["previous"]["section_id"], "aaa-first")
        self.assertEqual(middle["previous"]["chapter"], "第1章 开头")
        self.assertEqual(middle["previous"]["title"], "首节")
        self.assertEqual(middle["previous"]["page_start"], 1)
        self.assertEqual(middle["previous"]["page_end"], 2)
        self.assertEqual(middle["previous"]["text"], "首节正文")
        self.assertEqual(middle["next"]["section_id"], "mmm-last")
        self.assertEqual(middle["next"]["chapter"], "第3章 结尾")
        self.assertEqual(middle["next"]["text"], "末节正文")

        first = get_surrounding_context(context, "aaa-first")

        self.assertIs(first["ok"], True)
        self.assertIsNone(first["previous"])
        self.assertEqual(first["next"]["section_id"], "zzz-middle")

        last = get_surrounding_context(context, "mmm-last")

        self.assertIs(last["ok"], True)
        self.assertEqual(last["previous"]["section_id"], "zzz-middle")
        self.assertIsNone(last["next"])

        single = self._context(
            "book-single", (section_row("only", "唯一正文", sort_order=5),)
        )
        only = get_surrounding_context(single, "only")

        self.assertIs(only["ok"], True)
        self.assertEqual(only["target_section_id"], "only")
        self.assertIsNone(only["previous"])
        self.assertIsNone(only["next"])

    def test_returns_complete_neighbor_text(self) -> None:
        long_text = "长" * 600 + "\n结尾标记"
        spaced_text = "  行首空格\n\n第二段\t制表符\n🙂 é Ω\n结尾"
        context = self._context(
            "book-text",
            (
                section_row("empty-prev", "", sort_order=10),
                section_row("target-a", "目标甲", sort_order=20),
                section_row("long-next", long_text, sort_order=30),
                section_row("target-b", "目标乙", sort_order=40),
                section_row("unicode-next", spaced_text, sort_order=50),
            ),
        )

        first = get_surrounding_context(context, "target-a")

        self.assertIs(first["ok"], True)
        self.assertEqual(first["previous"]["text"], "")
        self.assertEqual(first["next"]["text"], long_text)
        self.assertGreater(len(first["next"]["text"]), 500)
        self.assertTrue(first["next"]["text"].endswith("\n结尾标记"))
        self.assertEqual(
            set(first["next"]),
            {"section_id", "chapter", "title", "page_start", "page_end", "text"},
        )

        second = get_surrounding_context(context, "target-b")

        self.assertIs(second["ok"], True)
        self.assertEqual(second["previous"]["section_id"], "long-next")
        self.assertEqual(second["previous"]["text"], long_text)
        self.assertEqual(second["next"]["section_id"], "unicode-next")
        self.assertEqual(second["next"]["text"], spaced_text)
        self.assertIn("\t制表符", second["next"]["text"])
        self.assertIn("🙂", second["next"]["text"])

    def test_queries_exact_ids_without_trimming(self) -> None:
        context = self._context(
            "book-exact",
            (
                section_row("chapter-3-overview", "目标正文", sort_order=10),
                section_row("chapter-3-detail", "后节正文", sort_order=20),
            ),
        )

        found = get_surrounding_context(context, "chapter-3-overview")

        self.assertIs(found["ok"], True)
        self.assertEqual(found["target_section_id"], "chapter-3-overview")
        self.assertIsNone(found["previous"])
        self.assertEqual(found["next"]["section_id"], "chapter-3-detail")

        spaced = get_surrounding_context(context, " chapter-3-overview ")

        self._assert_error(spaced, "SECTION_NOT_FOUND")

    def test_keeps_textbooks_isolated(self) -> None:
        first = self._context(
            "book-a",
            (
                section_row("prev", "甲前", title="甲前节", sort_order=10),
                section_row("shared", "甲目标", sort_order=20),
                section_row("next", "甲后", title="甲后节", sort_order=30),
            ),
        )
        second = self._context(
            "book-b",
            (
                section_row("prev", "乙前", title="乙前节", sort_order=10),
                section_row("shared", "乙目标", sort_order=20),
                section_row("next", "乙后", title="乙后节", sort_order=30),
            ),
        )

        first_result = get_surrounding_context(first, "shared")
        second_result = get_surrounding_context(second, "shared")

        self.assertIs(first_result["ok"], True)
        self.assertIs(second_result["ok"], True)
        self.assertEqual(first_result["book_id"], "book-a")
        self.assertEqual(second_result["book_id"], "book-b")
        self.assertEqual(first_result["previous"]["title"], "甲前节")
        self.assertEqual(second_result["previous"]["title"], "乙前节")
        self.assertEqual(first_result["previous"]["text"], "甲前")
        self.assertEqual(second_result["previous"]["text"], "乙前")
        self.assertEqual(first_result["next"]["text"], "甲后")
        self.assertEqual(second_result["next"]["text"], "乙后")

    def test_rejects_invalid_ids_without_opening_database(self) -> None:
        context = BookContext(
            book_id="book-invalid",
            database_path=self.root / "not-created" / "sections.db",
        )

        with mock.patch.object(
            textbook_tools.sqlite3,
            "connect",
            side_effect=AssertionError("database must not be opened"),
        ):
            for value in (None, 3, 3.2, b"3.2", ["3.2"], "", "   ", "\t\r\n"):
                with self.subTest(value=value):
                    self._assert_error(
                        get_surrounding_context(context, value), "INVALID_ARGUMENT"
                    )

        self.assertFalse(context.database_path.exists())

    def test_reports_missing_target(self) -> None:
        context = self._context(
            "book-target-missing", (section_row("3.2", "正文"),)
        )

        self._assert_error(
            get_surrounding_context(context, "9.9"), "SECTION_NOT_FOUND"
        )

    def test_reports_unready_and_unreadable_databases(self) -> None:
        missing = BookContext(
            book_id="book-missing",
            database_path=self.root / "missing-book" / "sections.db",
        )
        tableless = self._tableless_context("book-tableless")
        corrupt_directory = self.root / "corrupt-book"
        corrupt_directory.mkdir()
        corrupt_path = corrupt_directory / "sections.db"
        corrupt_path.write_bytes(b"this file is not a sqlite database" * 8)
        corrupt = BookContext(book_id="book-corrupt", database_path=corrupt_path)
        context = self._context(
            "book-permission", (section_row("3.2", "正文"),)
        )

        self._assert_error(
            get_surrounding_context(missing, "3.2"), "BOOK_NOT_READY"
        )
        self._assert_error(
            get_surrounding_context(tableless, "3.2"), "BOOK_NOT_READY"
        )
        self._assert_error(
            get_surrounding_context(corrupt, "3.2"), "READ_FAILED"
        )
        self.assertFalse(missing.database_path.exists())
        self.assertFalse(missing.database_path.parent.exists())

        with mock.patch.object(Path, "exists", deny_exists):
            result = get_surrounding_context(context, "3.2")

        self._assert_error(result, "READ_FAILED")

    def test_works_without_search_index_or_extension(self) -> None:
        context = self._context(
            "book-no-index",
            (
                section_row("prev", "前节", sort_order=10),
                section_row("target", "目标", sort_order=20),
                section_row("next", "后节", sort_order=30),
            ),
        )

        with mock.patch.object(
            textbook_tools,
            "load_simple_extension",
            side_effect=SimpleExtensionError("simulated extension failure"),
        ):
            result = get_surrounding_context(context, "target")

        self.assertIs(result["ok"], True)
        self.assertEqual(result["previous"]["text"], "前节")
        self.assertEqual(result["next"]["text"], "后节")

        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections_fts'"
                ).fetchone()
            )

    def test_opens_read_only_connection_and_closes_it_on_every_branch(self) -> None:
        context = self._context(
            "book-tracked",
            (
                section_row("prev", "前节", sort_order=10),
                section_row("target", "目标", sort_order=20),
            ),
        )
        tableless = self._tableless_context("book-tracked-tableless")

        scenarios = (
            ("success", context, "target", None),
            ("missing table", tableless, "target", "BOOK_NOT_READY"),
            ("missing section", context, "9.9", "SECTION_NOT_FOUND"),
            ("query failure", context, "target", "READ_FAILED"),
        )
        for label, scenario_context, section_id, expected_code in scenarios:
            with self.subTest(branch=label):
                result, connections = self._surrounding_with_tracking(
                    scenario_context,
                    section_id,
                    fail_query=label == "query failure",
                )

                if expected_code is None:
                    self.assertIs(result["ok"], True)
                else:
                    self._assert_error(result, expected_code)

                self.assertEqual(len(connections), 1)
                connection = connections[0]
                self.assertTrue(
                    connection.write_rejected,
                    "textbook database must be opened read-only",
                )
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_tool_definition_binds_surrounding_context_and_reads(self) -> None:
        self.assertEqual(
            GET_SURROUNDING_CONTEXT_TOOL["name"], "get_surrounding_context"
        )
        self.assertIs(
            GET_SURROUNDING_CONTEXT_TOOL["execute"], get_surrounding_context
        )
        description = GET_SURROUNDING_CONTEXT_TOOL["description"]
        self.assertIsInstance(description, str)
        self.assertIn("跨章", description)
        self.assertIn("null", description)
        self.assertIn("get_section_text", description)

        parameters = GET_SURROUNDING_CONTEXT_TOOL["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertEqual(set(parameters["properties"]), {"section_id"})
        self.assertEqual(parameters["properties"]["section_id"]["type"], "string")
        self.assertEqual(parameters["required"], ["section_id"])
        self.assertIs(parameters["additionalProperties"], False)
        for forbidden in ("context", "book_id", "database_path"):
            self.assertNotIn(forbidden, parameters["properties"])
            self.assertNotIn(forbidden, parameters["required"])

        metadata = {
            "name": GET_SURROUNDING_CONTEXT_TOOL["name"],
            "description": description,
            "parameters": parameters,
        }
        self.assertEqual(
            json.loads(json.dumps(metadata, ensure_ascii=False)), metadata
        )

        context = self._context(
            "book-tool", (section_row("only", "唯一正文", sort_order=10),)
        )
        arguments = {"section_id": "only"}

        result = GET_SURROUNDING_CONTEXT_TOOL["execute"](context, **arguments)

        self.assertIs(result["ok"], True)
        self.assertEqual(result["target_section_id"], "only")
        self.assertIsNone(result["previous"])
        self.assertIsNone(result["next"])

    def _surrounding_with_tracking(
        self, context, section_id, *, fail_query=False
    ):
        TrackingConnection.instances.clear()
        TrackingConnection.fail_section_query = fail_query
        try:
            with mock.patch.object(
                textbook_tools.sqlite3, "connect", new=tracking_connect
            ):
                result = get_surrounding_context(context, section_id)
        finally:
            TrackingConnection.fail_section_query = False
        return result, list(TrackingConnection.instances)
