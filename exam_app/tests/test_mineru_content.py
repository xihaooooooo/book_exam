import json
import tempfile
import unittest
from pathlib import Path

from book_exam import MinerUContentParsingError
from book_exam.ingestion.mineru_content import (
    load_mineru_content_blocks,
    read_mineru_block_text,
)


class MinerUContentTests(unittest.TestCase):
    def test_reads_content_file_and_preserves_supported_block_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory)
            (extracted / "result_content_list.json").write_text(
                json.dumps(
                    [
                        {"type": "header", "text": "ignored"},
                        {"type": "text", "text": "正文段落"},
                        {"type": "equation", "text": "$$x=1$$"},
                        {"type": "list", "list_items": ["  first  ", "   ", "second"]},
                        {"type": "table", "table_caption": ["table"], "table_body": "<table/>"},
                        {"type": "code", "code_caption": ["示例"], "code_body": "print(1)"},
                        {"type": "image", "image_caption": ["图 1"], "content": "图像描述"},
                    ]
                ),
                encoding="utf-8",
            )

            blocks = tuple(load_mineru_content_blocks(extracted))

        self.assertEqual(read_mineru_block_text(blocks[0]), "")
        self.assertEqual(read_mineru_block_text(blocks[1]), "正文段落")
        self.assertEqual(read_mineru_block_text(blocks[2]), "$$x=1$$")
        self.assertEqual(read_mineru_block_text(blocks[3]), "first\nsecond")
        self.assertEqual(read_mineru_block_text(blocks[4]), "table\n<table/>")
        self.assertEqual(read_mineru_block_text(blocks[5]), "示例\nprint(1)")
        self.assertEqual(read_mineru_block_text(blocks[6]), "图 1\n图像描述")

    def test_rejects_non_string_list_items(self) -> None:
        with self.assertRaisesRegex(MinerUContentParsingError, "list of text"):
            read_mineru_block_text({"type": "list", "list_items": ["text", 3]})

    def test_rejects_missing_or_ambiguous_content_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory)
            with self.assertRaisesRegex(MinerUContentParsingError, "Expected one"):
                tuple(load_mineru_content_blocks(extracted))
            (extracted / "content_list.json").write_text("[]", encoding="utf-8")
            (extracted / "copy_content_list.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(MinerUContentParsingError, "Expected one"):
                tuple(load_mineru_content_blocks(extracted))
