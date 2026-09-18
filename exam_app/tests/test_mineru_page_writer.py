import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from book_exam import MinerUPage, MinerUPageWritingError, write_mineru_pages


class MinerUPageWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name)
        self.database_path = self.book_directory / "sections.db"

    def test_creates_database_and_inserts_mineru_pages(self) -> None:
        result = write_mineru_pages(
            self.book_directory,
            (MinerUPage(1, "MinerU one"), MinerUPage(2, "")),
        )

        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.character_count, len("MinerU one"))
        self.assertEqual(result.database_path, self.database_path)
        self.assertEqual(
            self._read_pages(),
            [(1, "MinerU one"), (2, "")],
        )

    def test_replaces_existing_page_and_keeps_other_pages(self) -> None:
        write_mineru_pages(
            self.book_directory,
            (MinerUPage(1, "MinerU one"), MinerUPage(2, "MinerU two")),
        )

        write_mineru_pages(self.book_directory, (MinerUPage(2, "Updated two"),))

        self.assertEqual(
            self._read_pages(),
            [(1, "MinerU one"), (2, "Updated two")],
        )

    def test_database_failure_rolls_back_all_pages(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE pages (
                    page_number INTEGER PRIMARY KEY CHECK (page_number > 0),
                    text TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER reject_second_page
                BEFORE INSERT ON pages
                WHEN NEW.page_number = 2
                BEGIN
                    SELECT RAISE(ABORT, 'rejected page');
                END
                """
            )

        with self.assertRaisesRegex(MinerUPageWritingError, "rejected page"):
            write_mineru_pages(
                self.book_directory,
                (MinerUPage(1, "MinerU one"), MinerUPage(2, "MinerU two")),
            )

        self.assertEqual(self._read_pages(), [])

    def test_rejects_duplicate_page_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            write_mineru_pages(
                self.book_directory,
                (MinerUPage(1, "first"), MinerUPage(1, "second")),
            )

    def test_rejects_missing_book_directory(self) -> None:
        missing_directory = self.book_directory / "missing"

        with self.assertRaisesRegex(MinerUPageWritingError, "directory is missing"):
            write_mineru_pages(missing_directory, (MinerUPage(1, "text"),))

    def _read_pages(self) -> list[tuple[int, str]]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute(
                "SELECT page_number, text FROM pages ORDER BY page_number"
            ).fetchall()


if __name__ == "__main__":
    unittest.main()
