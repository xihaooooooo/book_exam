import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from book_exam import (
    MinerUPage,
    MinerUPageParsingError,
    PdfChunk,
    import_mineru_book_pages,
    write_mineru_pages,
)


class MinerUBookImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name)

    def test_imports_out_of_order_chunks_as_one_complete_book(self) -> None:
        first = self._chunk(1, 2)
        second = self._chunk(3, 3)
        self._write_content(
            first,
            [
                {"type": "text", "text": "page one", "page_idx": 0},
                {"type": "text", "text": "page two", "page_idx": 1},
            ],
        )
        self._write_content(
            second,
            [{"type": "text", "text": "page three", "page_idx": 0}],
        )

        result = import_mineru_book_pages(
            self.book_directory, (second, first)
        )

        self.assertEqual(result.page_count, 3)
        self.assertEqual(
            self._read_pages(),
            [(1, "page one"), (2, "page two"), (3, "page three")],
        )

    def test_parse_failure_leaves_existing_database_unchanged(self) -> None:
        write_mineru_pages(
            self.book_directory, (MinerUPage(99, "existing text"),)
        )
        first = self._chunk(1, 1)
        second = self._chunk(2, 2)
        self._write_content(
            first,
            [{"type": "text", "text": "page one", "page_idx": 0}],
        )

        with self.assertRaisesRegex(MinerUPageParsingError, "directory is missing"):
            import_mineru_book_pages(self.book_directory, (first, second))

        self.assertEqual(self._read_pages(), [(99, "existing text")])

    def test_rejects_noncontiguous_chunk_ranges_before_parsing(self) -> None:
        first = self._chunk(1, 1)
        third = self._chunk(3, 3)

        with self.assertRaisesRegex(ValueError, "contiguous"):
            import_mineru_book_pages(self.book_directory, (first, third))

        self.assertFalse((self.book_directory / "sections.db").exists())

    def _chunk(self, start_page: int, end_page: int) -> PdfChunk:
        data_id = f"chunk-{start_page:04d}-{end_page:04d}"
        return PdfChunk(
            self.book_directory / "mineru-chunks" / f"{data_id}.pdf",
            start_page,
            end_page,
            data_id,
        )

    def _write_content(
        self, chunk: PdfChunk, content: list[dict[str, object]]
    ) -> None:
        extracted = self.book_directory / "mineru-extracted" / chunk.data_id
        extracted.mkdir(parents=True)
        (extracted / "content_list.json").write_text(
            json.dumps(content), encoding="utf-8"
        )

    def _read_pages(self) -> list[tuple[int, str]]:
        database_path = self.book_directory / "sections.db"
        with closing(sqlite3.connect(database_path)) as connection:
            return connection.execute(
                "SELECT page_number, text FROM pages ORDER BY page_number"
            ).fetchall()


if __name__ == "__main__":
    unittest.main()
