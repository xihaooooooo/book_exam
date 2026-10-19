import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from book_exam import MinerUSectionWritingError, write_mineru_sections
from book_exam.ingestion.mineru_section_parser import MinerUSection


class MinerUSectionWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name)
        self.database_path = self.book_directory / "sections.db"

    def test_writes_sections_and_removes_old_pages_table(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "CREATE TABLE pages (page_number INTEGER PRIMARY KEY, text TEXT)"
            )
            connection.execute("INSERT INTO pages VALUES (1, 'old')")

        result = write_mineru_sections(self.book_directory, self._sections())

        self.assertEqual(result.section_count, 2)
        self.assertEqual(result.character_count, len("one") + len("two"))
        self.assertEqual(result.database_path, self.database_path)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id, sort_order, text FROM sections ORDER BY sort_order"
                ).fetchall(),
                [("1.1", 1, "one"), ("1.2", 2, "two")],
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='pages'"
                ).fetchone()
            )

    def test_database_failure_rolls_back_sections_and_pages_table_drop(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "CREATE TABLE pages (page_number INTEGER PRIMARY KEY, text TEXT)"
            )
            connection.execute("INSERT INTO pages VALUES (1, 'old page')")
            connection.execute(
                """CREATE TABLE sections (
                    id TEXT PRIMARY KEY, chapter TEXT NOT NULL, title TEXT NOT NULL,
                    sort_order INTEGER NOT NULL UNIQUE, page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL, text TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO sections VALUES ('existing', '', '现有节', 1, 1, 1, '现有正文')"
            )
            connection.execute(
                """CREATE TRIGGER reject_second_section BEFORE INSERT ON sections
                WHEN NEW.sort_order = 2 BEGIN SELECT RAISE(ABORT, 'rejected section'); END"""
            )

        with self.assertRaisesRegex(MinerUSectionWritingError, "rejected section"):
            write_mineru_sections(self.book_directory, self._sections())

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT id, text FROM sections").fetchall(),
                [("existing", "现有正文")],
            )
            self.assertEqual(
                connection.execute("SELECT text FROM pages").fetchall(),
                [("old page",)],
            )

    def test_rejects_invalid_ids_orders_and_page_ranges(self) -> None:
        for sections in (
            (self._section("duplicate", 1), self._section("duplicate", 2)),
            (self._section("first", 1), self._section("second", 1)),
            (self._section("invalid-order", 0),),
            (MinerUSection("bad-page", "", "title", 1, 3, 2, "text"),),
        ):
            with self.subTest(sections=sections), self.assertRaises(ValueError):
                write_mineru_sections(self.book_directory, sections)
        self.assertFalse(self.database_path.exists())

    def test_rejects_missing_book_directory(self) -> None:
        with self.assertRaisesRegex(MinerUSectionWritingError, "directory is missing"):
            write_mineru_sections(self.book_directory / "missing", self._sections())

    def _section(self, section_id: str, order: int) -> MinerUSection:
        return MinerUSection(section_id, "第一章", f"节 {order}", order, order, order, "text")

    def _sections(self) -> tuple[MinerUSection, ...]:
        return (
            MinerUSection("1.1", "第一章", "第一节", 1, 1, 1, "one"),
            MinerUSection("1.2", "第一章", "第二节", 2, 2, 2, "two"),
        )
