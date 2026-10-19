import json
import tempfile
import unittest
from pathlib import Path

from book_exam import (
    MinerUSectionParsingError,
    PdfChunk,
    parse_mineru_book_sections,
)


class MinerUSectionParserTests(unittest.TestCase):
    def _write_chunk(
        self, book: Path, data_id: str, blocks: list[dict[str, object]]
    ) -> None:
        result = book / "mineru-extracted" / data_id / "result"
        result.mkdir(parents=True)
        (result / "content_list.json").write_text(
            json.dumps(blocks, ensure_ascii=False), encoding="utf-8"
        )

    def test_groups_across_pages_chunks_and_same_page_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [
                    {"type": "text", "text": "第二章 引言", "text_level": 1, "page_idx": 0},
                    {"type": "text", "text": "章前说明", "page_idx": 0},
                    {"type": "text", "text": "2.1 第一节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "2.1在这段文字中不是标题。", "page_idx": 0},
                    {"type": "text", "text": "跨页正文", "page_idx": 1},
                ],
            )
            self._write_chunk(
                book,
                "chunk-b",
                [
                    {"type": "text", "text": "2.1.1 深层标题", "text_level": 3, "page_idx": 0},
                    {"type": "text", "text": "深层标题仍属于第一节", "page_idx": 0},
                    {"type": "text", "text": "2.2 第二节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "第二节正文", "page_idx": 0},
                ],
            )
            chunks = (
                PdfChunk(book / "a.pdf", 1, 2, "chunk-a"),
                PdfChunk(book / "b.pdf", 3, 3, "chunk-b"),
            )

            sections = parse_mineru_book_sections(book, chunks)

        self.assertEqual([section.section_id for section in sections], ["chapter-二-overview", "2.1", "2.2"])
        self.assertEqual(sections[0].title, "章概述")
        self.assertIn("章前说明", sections[0].text)
        self.assertEqual((sections[1].page_start, sections[1].page_end), (1, 3))
        self.assertIn("2.1在这段文字中不是标题。", sections[1].text)
        self.assertIn("2.1.1 深层标题", sections[1].text)
        self.assertEqual((sections[2].page_start, sections[2].page_end), (3, 3))

    def test_keeps_unsectioned_readable_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [{"type": "text", "text": "没有可靠标题的正文 2.1", "page_idx": 0}],
            )

            sections = parse_mineru_book_sections(
                book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
            )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].section_id, "unsectioned-1")
        self.assertEqual(sections[0].title, "未分节正文")
        self.assertEqual(sections[0].text, "没有可靠标题的正文 2.1")

    def test_bare_deep_number_does_not_create_parent_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [{"type": "text", "text": "2.1.1", "text_level": 3, "page_idx": 0}],
            )

            sections = parse_mineru_book_sections(
                book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
            )

        self.assertEqual([section.section_id for section in sections], ["unsectioned-1"])

    def test_chapter_heading_followed_by_section_has_no_empty_overview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [
                    {"type": "text", "text": "第1章 引言", "text_level": 1, "page_idx": 0},
                    {"type": "text", "text": "1.1 第一节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "正文", "page_idx": 0},
                ],
            )

            sections = parse_mineru_book_sections(
                book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
            )

        self.assertEqual([section.section_id for section in sections], ["1.1"])

    def test_rejects_blocks_with_page_order_reversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [
                    {"type": "text", "text": "后页", "page_idx": 1},
                    {"type": "text", "text": "前页", "page_idx": 0},
                ],
            )

            with self.assertRaisesRegex(MinerUSectionParsingError, "not in page order"):
                parse_mineru_book_sections(
                    book, (PdfChunk(book / "a.pdf", 1, 2, "chunk-a"),)
                )

    def test_rejects_deep_heading_without_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [{"type": "text", "text": "2.1.1 深层标题", "text_level": 3, "page_idx": 0}],
            )

            with self.assertRaisesRegex(MinerUSectionParsingError, "current parent"):
                parse_mineru_book_sections(
                    book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
                )

    def test_rejects_deep_heading_after_parent_section_has_ended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [
                    {"type": "text", "text": "2.1 第一节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "2.2 第二节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "2.1.1 错属标题", "text_level": 3, "page_idx": 0},
                ],
            )

            with self.assertRaisesRegex(MinerUSectionParsingError, "current parent"):
                parse_mineru_book_sections(
                    book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
                )

    def test_rejects_duplicate_section_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory)
            self._write_chunk(
                book,
                "chunk-a",
                [
                    {"type": "text", "text": "2.1 第一节", "text_level": 2, "page_idx": 0},
                    {"type": "text", "text": "2.1 重复节", "text_level": 2, "page_idx": 0},
                ],
            )

            with self.assertRaisesRegex(MinerUSectionParsingError, "Duplicate section"):
                parse_mineru_book_sections(
                    book, (PdfChunk(book / "a.pdf", 1, 1, "chunk-a"),)
                )


if __name__ == "__main__":
    unittest.main()
