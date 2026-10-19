import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from book_exam import (
    MinerUContentParsingError,
    PdfChunk,
    import_mineru_book_sections,
    write_mineru_sections,
)
from book_exam.ingestion.mineru_section_parser import MinerUSection


class MinerUBookImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name)

    def test_imports_sections_across_chunks_in_book_order(self) -> None:
        first = self._chunk(1, 2, "first")
        second = self._chunk(3, 3, "second")
        self._write_content(
            first,
            [
                {"type": "text", "text": "第一章 引言", "text_level": 1, "page_idx": 0},
                {"type": "text", "text": "1.1 第一节", "text_level": 2, "page_idx": 0},
                {"type": "text", "text": "跨页小节", "page_idx": 1},
            ],
        )
        self._write_content(
            second,
            [{"type": "text", "text": "跨块小节", "page_idx": 0}],
        )

        result = import_mineru_book_sections(self.book_directory, (second, first))

        self.assertEqual(result.section_count, 1)
        self.assertEqual(self._read_sections(), [("1.1", 1, 3, "跨页小节\n\n跨块小节")])

    def test_reimport_replaces_all_old_sections(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (
                MinerUSection("old-a", "", "旧节 A", 1, 1, 1, "旧正文 A"),
                MinerUSection("old-b", "", "旧节 B", 2, 2, 2, "旧正文 B"),
            ),
        )
        chunk = self._chunk(1, 1, "new")
        self._write_content(
            chunk,
            [
                {"type": "text", "text": "1.1 新节", "text_level": 2, "page_idx": 0},
                {"type": "text", "text": "新正文", "page_idx": 0},
            ],
        )

        import_mineru_book_sections(self.book_directory, (chunk,))

        self.assertEqual(self._read_sections(), [("1.1", 1, 1, "新正文")])

    def test_parse_failure_preserves_existing_sections(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (MinerUSection("existing", "", "现有节", 1, 1, 1, "现有正文"),),
        )
        first = self._chunk(1, 1, "first")
        second = self._chunk(2, 2, "missing")
        self._write_content(first, [{"type": "text", "text": "正文", "page_idx": 0}])

        with self.assertRaisesRegex(MinerUContentParsingError, "directory is missing"):
            import_mineru_book_sections(self.book_directory, (first, second))

        self.assertEqual(self._read_sections(), [("existing", 1, 1, "现有正文")])

    def test_out_of_range_page_idx_preserves_existing_sections(self) -> None:
        write_mineru_sections(
            self.book_directory,
            (MinerUSection("existing", "", "现有节", 1, 1, 1, "现有正文"),),
        )
        chunk = self._chunk(1, 1, "invalid-page")
        self._write_content(
            chunk,
            [{"type": "text", "text": "错页", "page_idx": 1}],
        )

        with self.assertRaisesRegex(MinerUContentParsingError, "page_idx"):
            import_mineru_book_sections(self.book_directory, (chunk,))

        self.assertEqual(self._read_sections(), [("existing", 1, 1, "现有正文")])

    def test_rejects_noncontiguous_chunks_before_creating_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous"):
            import_mineru_book_sections(
                self.book_directory,
                (self._chunk(1, 1, "first"), self._chunk(3, 3, "third")),
            )
        self.assertFalse((self.book_directory / "sections.db").exists())

    def _chunk(self, start: int, end: int, data_id: str) -> PdfChunk:
        return PdfChunk(
            self.book_directory / "mineru-chunks" / f"{data_id}.pdf",
            start,
            end,
            data_id,
        )

    def _write_content(self, chunk: PdfChunk, content: list[dict[str, object]]) -> None:
        extracted = self.book_directory / "mineru-extracted" / chunk.data_id
        extracted.mkdir(parents=True)
        (extracted / "content_list.json").write_text(
            json.dumps(content, ensure_ascii=False), encoding="utf-8"
        )

    def _read_sections(self) -> list[tuple[str, int, int, str]]:
        with closing(sqlite3.connect(self.book_directory / "sections.db")) as connection:
            return connection.execute(
                "SELECT id, page_start, page_end, text FROM sections ORDER BY sort_order"
            ).fetchall()
