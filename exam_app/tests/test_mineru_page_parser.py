import json
import tempfile
import unittest
from pathlib import Path

from book_exam import (
    MinerUPageParsingError,
    PdfChunk,
    parse_mineru_chunk_pages,
)


class MinerUPageParserTests(unittest.TestCase):
    def test_converts_chunk_content_to_original_book_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory) / "chunk-0201-0202"
            result = extracted / "chunk-0201-0202" / "vlm"
            result.mkdir(parents=True)
            content_list = [
                {"type": "text", "text": "第一段", "page_idx": 0},
                {"type": "equation", "text": "$$x=1$$", "page_idx": 0},
                {"type": "header", "text": "教材页眉", "page_idx": 1},
                {"type": "list", "list_items": ["甲", "乙"], "page_idx": 1},
                {
                    "type": "table",
                    "table_caption": ["表 1"],
                    "table_body": "<table><tr><td>值</td></tr></table>",
                    "page_idx": 1,
                },
                {"type": "code", "code_caption": ["示例"], "code_body": "print(1)", "page_idx": 1},
                {"type": "image", "image_caption": ["图 1"], "page_idx": 1},
            ]
            (result / "chunk-0201-0202_content_list.json").write_text(
                json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
            )
            chunk = PdfChunk(
                Path(directory) / "chunk-0201-0202.pdf", 201, 202, "chunk-0201-0202"
            )

            pages = parse_mineru_chunk_pages(extracted, chunk)

        self.assertEqual(
            [(page.page_number, page.text) for page in pages],
            [
                (201, "第一段\n\n$$x=1$$"),
                (
                    202,
                    "甲\n乙\n\n表 1\n<table><tr><td>值</td></tr></table>"
                    "\n\n示例\nprint(1)\n\n图 1",
                ),
            ],
        )

    def test_rejects_content_assigned_outside_chunk_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory)
            (extracted / "content_list.json").write_text(
                json.dumps([{"type": "text", "text": "错页", "page_idx": 2}]),
                encoding="utf-8",
            )
            chunk = PdfChunk(extracted / "chunk.pdf", 201, 202, "chunk-0201-0202")

            with self.assertRaisesRegex(MinerUPageParsingError, "page_idx"):
                parse_mineru_chunk_pages(extracted, chunk)

    def test_keeps_pages_without_readable_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory)
            (extracted / "content_list.json").write_text(
                json.dumps([{"type": "text", "text": "第一页", "page_idx": 0}]),
                encoding="utf-8",
            )
            chunk = PdfChunk(extracted / "chunk.pdf", 401, 402, "chunk-0401-0402")

            pages = parse_mineru_chunk_pages(extracted, chunk)

        self.assertEqual(
            [(page.page_number, page.text) for page in pages],
            [(401, "第一页"), (402, "")],
        )


if __name__ == "__main__":
    unittest.main()
