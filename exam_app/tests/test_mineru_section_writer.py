import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from book_exam import MinerUSectionWritingError, write_mineru_sections
from book_exam.ingestion import mineru_section_writer
from book_exam.ingestion.mineru_section_parser import MinerUSection
from book_exam.search import SimpleExtensionError, load_simple_extension


class MinerUSectionWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name)
        self.database_path = self.book_directory / "sections.db"

    def test_writes_sections_and_indexes_title_and_text(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "CREATE TABLE pages (page_number INTEGER PRIMARY KEY, text TEXT)"
            )
            connection.execute("INSERT INTO pages VALUES (1, 'old')")

        result = write_mineru_sections(self.book_directory, self._sections())

        self.assertEqual(result.section_count, 2)
        self.assertEqual(result.character_count, len("算法优化") + len("死锁处理"))
        self.assertEqual(result.database_path, self.database_path)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id, sort_order, text FROM sections ORDER BY sort_order"
                ).fetchall(),
                [("1.1", 1, "算法优化"), ("1.2", 2, "死锁处理")],
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='pages'"
                ).fetchone()
            )

        self.assertEqual(self._match_rows('"调度"'), [(1, "1.1")])
        self.assertEqual(self._match_rows('"算法"'), [(1, "1.1")])

    def test_reimport_updates_index_and_removes_old_terms(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (MinerUSection("old", "", "旧词标题", 1, 1, 1, "旧词正文"),),
        )

        write_mineru_sections(
            self.book_directory,
            (MinerUSection("new", "", "新词标题", 1, 1, 1, "新词正文"),),
        )

        self.assertEqual(self._match_ids('"新词"'), ["new"])
        self.assertEqual(self._match_ids('"旧词"'), [])

    def test_rebuild_failure_rolls_back_sections_and_existing_index(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (MinerUSection("old", "", "旧词标题", 1, 1, 1, "旧词正文"),),
        )
        rebuild = mineru_section_writer.rebuild_section_index

        def rebuild_then_fail(connection: sqlite3.Connection) -> None:
            rebuild(connection)
            raise sqlite3.OperationalError("rebuild rejected")

        with patch.object(
            mineru_section_writer, "rebuild_section_index", rebuild_then_fail
        ):
            with self.assertRaisesRegex(MinerUSectionWritingError, "rebuild rejected"):
                write_mineru_sections(
                    self.book_directory,
                    (MinerUSection("new", "", "新词标题", 1, 1, 1, "新词正文"),),
                )

        self.assertEqual(self._read_sections(), [("old", "旧词标题", "旧词正文")])
        self.assertEqual(self._match_ids('"旧词"'), ["old"])
        self.assertEqual(self._match_ids('"新词"'), [])

    def test_first_index_creation_failure_leaves_no_index_table(self) -> None:
        rebuild = mineru_section_writer.rebuild_section_index

        def rebuild_then_fail(connection: sqlite3.Connection) -> None:
            rebuild(connection)
            raise sqlite3.OperationalError("first rebuild rejected")

        with patch.object(
            mineru_section_writer, "rebuild_section_index", rebuild_then_fail
        ):
            with self.assertRaisesRegex(
                MinerUSectionWritingError, "first rebuild rejected"
            ):
                write_mineru_sections(self.book_directory, self._sections())

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='sections_fts'"
                ).fetchone()
            )

    def test_extension_failure_is_wrapped_and_preserves_existing_data(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (MinerUSection("old", "", "旧词标题", 1, 1, 1, "旧词正文"),),
        )
        with patch.object(
            mineru_section_writer,
            "load_simple_extension",
            side_effect=SimpleExtensionError("extension unavailable"),
        ):
            with self.assertRaisesRegex(
                MinerUSectionWritingError, "extension unavailable"
            ):
                write_mineru_sections(
                    self.book_directory,
                    (MinerUSection("new", "", "新词标题", 1, 1, 1, "新词正文"),),
                )

        self.assertEqual(self._read_sections(), [("old", "旧词标题", "旧词正文")])
        self.assertEqual(self._match_ids('"旧词"'), ["old"])

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

    def _read_sections(self) -> list[tuple[str, str, str]]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute(
                "SELECT id, title, text FROM sections"
            ).fetchall()

    def _match_ids(self, phrase: str) -> list[str]:
        return [section_id for _, section_id in self._match_rows(phrase)]

    def _match_rows(self, phrase: str) -> list[tuple[int, str]]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            load_simple_extension(connection)
            return [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT rowid, id FROM sections_fts WHERE sections_fts MATCH ? "
                    "ORDER BY rowid",
                    (phrase,),
                )
            ]

    def _section(self, section_id: str, order: int) -> MinerUSection:
        return MinerUSection(
            section_id, "第一章", f"节 {order}", order, order, order, "text"
        )

    def _sections(self) -> tuple[MinerUSection, ...]:
        return (
            MinerUSection("1.1", "第一章", "进程调度", 1, 1, 1, "算法优化"),
            MinerUSection("1.2", "第一章", "并发控制", 2, 2, 2, "死锁处理"),
        )
