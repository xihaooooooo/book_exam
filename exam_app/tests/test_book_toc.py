import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from book_exam import book_toc
from book_exam.book_context import BookContext
from book_exam.book_toc import get_book_toc

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
    """Records connections opened by the TOC reader and probes write access."""

    instances: list["TrackingConnection"] = []
    fail_toc_query = False

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
            TrackingConnection.fail_toc_query
            and isinstance(sql, str)
            and "ORDER BY sort_order" in sql
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


class BookTocTestCase(unittest.TestCase):
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

    def _empty_toc_context(self, book_id: str) -> BookContext:
        directory = self.root / book_id
        directory.mkdir()
        database_path = directory / "sections.db"
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute(SECTIONS_TABLE_SQL)
            connection.commit()
        return BookContext(book_id=book_id, database_path=database_path)

    def _assert_error(self, result, code: str) -> None:
        self.assertIs(result["ok"], False)
        self.assertNotIn("sections", result)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["message"], str)
        self.assertTrue(result["error"]["message"].strip())


class GetBookTocSuccessTests(BookTocTestCase):
    def test_orders_by_sort_order_instead_of_id_or_pages(self) -> None:
        context = self._context(
            "book-order",
            (
                section_row(
                    "zzz-first",
                    "开篇正文",
                    chapter="第1章 开篇",
                    title="开篇",
                    sort_order=10,
                    page_start=30,
                    page_end=30,
                ),
                section_row(
                    "aaa-second",
                    "中段正文",
                    chapter="第2章 中段",
                    title="中段",
                    sort_order=20,
                    page_start=10,
                    page_end=10,
                ),
                section_row(
                    "mmm-third",
                    "收尾正文",
                    chapter="第3章 收尾",
                    title="收尾",
                    sort_order=30,
                    page_start=20,
                    page_end=20,
                ),
            ),
        )

        result = get_book_toc(context)

        self.assertIs(result["ok"], True)
        self.assertEqual(
            [section["section_id"] for section in result["sections"]],
            ["zzz-first", "aaa-second", "mmm-third"],
        )

    def test_preserves_non_numeric_ids_and_raw_field_values(self) -> None:
        context = self._context(
            "book-raw",
            (
                section_row(
                    "chapter-3-overview",
                    "章概述正文",
                    chapter="第 3 章　并发控制",
                    title="  第3章 概述（原文）  ",
                    sort_order=20,
                ),
                section_row(
                    "unsectioned-1",
                    "未分节正文",
                    chapter="未分节",
                    title="未分节内容",
                    sort_order=30,
                ),
                section_row(
                    "1.1",
                    "首节正文",
                    chapter="第1章 导论",
                    title="1.1 基本概念",
                    sort_order=10,
                ),
            ),
        )

        result = get_book_toc(context)

        self.assertIs(result["ok"], True)
        self.assertEqual(
            result["sections"],
            [
                {
                    "section_id": "1.1",
                    "chapter": "第1章 导论",
                    "title": "1.1 基本概念",
                },
                {
                    "section_id": "chapter-3-overview",
                    "chapter": "第 3 章　并发控制",
                    "title": "  第3章 概述（原文）  ",
                },
                {
                    "section_id": "unsectioned-1",
                    "chapter": "未分节",
                    "title": "未分节内容",
                },
            ],
        )

    def test_returns_every_section_with_only_toc_fields(self) -> None:
        rows = tuple(
            section_row(
                f"sec-{index:02d}",
                f"第{index}节正文" * 300,
                chapter=f"第{(index - 1) // 3 + 1}章 分组",
                title=f"{index}.1 小节标题",
                sort_order=index,
                page_start=index,
                page_end=index,
            )
            for index in range(1, 13)
        )
        context = self._context("book-full", rows)

        result = get_book_toc(context)

        self.assertIs(result["ok"], True)
        self.assertEqual(set(result), {"ok", "book_id", "sections"})
        self.assertEqual(result["book_id"], "book-full")
        sections = result["sections"]
        self.assertEqual(len(sections), len(rows))
        self.assertEqual(
            sections,
            [
                {
                    "section_id": f"sec-{index:02d}",
                    "chapter": f"第{(index - 1) // 3 + 1}章 分组",
                    "title": f"{index}.1 小节标题",
                }
                for index in range(1, 13)
            ],
        )
        for section in sections:
            self.assertEqual(set(section), {"section_id", "chapter", "title"})
            self.assertIsInstance(section["section_id"], str)
            self.assertIsInstance(section["chapter"], str)
            self.assertIsInstance(section["title"], str)

    def test_keeps_textbooks_isolated(self) -> None:
        first = self._context(
            "book-first",
            (
                section_row(
                    "1.1",
                    "甲正文",
                    chapter="甲第一章",
                    title="甲 1.1",
                    sort_order=1,
                ),
                section_row(
                    "1.2",
                    "甲正文二",
                    chapter="甲第一章",
                    title="甲 1.2",
                    sort_order=2,
                ),
            ),
        )
        second = self._context(
            "book-second",
            (
                section_row(
                    "1.1",
                    "乙正文",
                    chapter="乙第一章",
                    title="乙 1.1",
                    sort_order=1,
                ),
            ),
        )

        first_result = get_book_toc(first)
        second_result = get_book_toc(second)

        self.assertIs(first_result["ok"], True)
        self.assertIs(second_result["ok"], True)
        self.assertEqual(first_result["book_id"], "book-first")
        self.assertEqual(second_result["book_id"], "book-second")
        self.assertEqual(
            first_result["sections"],
            [
                {"section_id": "1.1", "chapter": "甲第一章", "title": "甲 1.1"},
                {"section_id": "1.2", "chapter": "甲第一章", "title": "甲 1.2"},
            ],
        )
        self.assertEqual(
            second_result["sections"],
            [{"section_id": "1.1", "chapter": "乙第一章", "title": "乙 1.1"}],
        )

    def test_reads_without_building_search_index(self) -> None:
        context = self._context(
            "book-no-index", (section_row("1.1", "正文", title="1.1 小节"),)
        )

        result = get_book_toc(context)

        self.assertIs(result["ok"], True)
        self.assertEqual(
            result["sections"],
            [{"section_id": "1.1", "chapter": "第1章 总论", "title": "1.1 小节"}],
        )
        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'sections_fts'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sections").fetchone(), (1,)
            )


class GetBookTocErrorTests(BookTocTestCase):
    def test_reports_missing_database_without_creating_it(self) -> None:
        database_path = self.root / "missing-book" / "sections.db"
        context = BookContext(book_id="book-missing", database_path=database_path)

        with mock.patch.object(
            book_toc.sqlite3,
            "connect",
            side_effect=AssertionError("database must not be opened"),
        ):
            result = get_book_toc(context)

        self._assert_error(result, "BOOK_NOT_READY")
        self.assertFalse(database_path.exists())
        self.assertFalse(database_path.parent.exists())

    def test_reports_database_without_sections_table(self) -> None:
        context = self._tableless_context("book-tableless")

        self._assert_error(get_book_toc(context), "BOOK_NOT_READY")

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

    def test_reports_empty_sections_table(self) -> None:
        context = self._empty_toc_context("book-empty")

        result = get_book_toc(context)

        self._assert_error(result, "BOOK_NOT_READY")
        self.assertIn("sections", result["error"]["message"])
        with closing(sqlite3.connect(context.database_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sections").fetchone(), (0,)
            )

    def test_reports_corrupt_database(self) -> None:
        directory = self.root / "corrupt-book"
        directory.mkdir()
        database_path = directory / "sections.db"
        database_path.write_bytes(b"this file is not a sqlite database" * 8)
        context = BookContext(book_id="book-corrupt", database_path=database_path)

        self._assert_error(get_book_toc(context), "READ_FAILED")

    def test_reports_unreadable_path_instead_of_raising(self) -> None:
        context = self._context("book-permission", (section_row("1.1", "正文"),))

        with mock.patch.object(Path, "exists", deny_exists):
            result = get_book_toc(context)

        self._assert_error(result, "READ_FAILED")

    def test_reports_open_failure_as_read_failed(self) -> None:
        context = self._context("book-open-failure", (section_row("1.1", "正文"),))

        with mock.patch.object(
            book_toc.sqlite3,
            "connect",
            side_effect=sqlite3.OperationalError("simulated open failure"),
        ):
            result = get_book_toc(context)

        self._assert_error(result, "READ_FAILED")
        self.assertIn("simulated open failure", result["error"]["message"])

    def test_opens_read_only_connection_and_closes_it_on_every_branch(self) -> None:
        context = self._context(
            "book-tracked",
            (
                section_row("1.1", "首节正文", sort_order=1),
                section_row("1.2", "次节正文", sort_order=2),
            ),
        )
        tableless = self._tableless_context("book-tracked-tableless")
        empty = self._empty_toc_context("book-tracked-empty")

        scenarios = (
            ("success", context, None, False),
            ("missing table", tableless, "BOOK_NOT_READY", False),
            ("empty table", empty, "BOOK_NOT_READY", False),
            ("query failure", context, "READ_FAILED", True),
        )
        for label, scenario_context, expected_code, fail_query in scenarios:
            with self.subTest(branch=label):
                result, connections = self._read_with_tracking(
                    scenario_context, fail_query=fail_query
                )

                if expected_code is None:
                    self.assertIs(result["ok"], True)
                    self.assertEqual(
                        [section["section_id"] for section in result["sections"]],
                        ["1.1", "1.2"],
                    )
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

    def _read_with_tracking(self, context: BookContext, *, fail_query: bool = False):
        TrackingConnection.instances.clear()
        TrackingConnection.fail_toc_query = fail_query
        try:
            with mock.patch.object(
                book_toc.sqlite3, "connect", new=tracking_connect
            ):
                result = get_book_toc(context)
        finally:
            TrackingConnection.fail_toc_query = False
        return result, list(TrackingConnection.instances)
