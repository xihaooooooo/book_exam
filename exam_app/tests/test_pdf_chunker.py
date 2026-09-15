import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from book_exam import PdfChunker


class PdfChunkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.book_directory = Path(self.temporary_directory.name) / "book-123"
        self.book_directory.mkdir()

    def test_chunks_401_pages_into_200_200_and_1(self) -> None:
        writer = PdfWriter()
        for _ in range(401):
            writer.add_blank_page(width=100, height=100)
        writer.write(self.book_directory / "source.pdf")

        chunks = PdfChunker().chunk(self.book_directory)

        self.assertEqual(
            [
                (chunk.start_page, chunk.end_page, chunk.data_id, chunk.path.name)
                for chunk in chunks
            ],
            [
                (1, 200, "chunk-0001-0200", "chunk-0001-0200.pdf"),
                (201, 400, "chunk-0201-0400", "chunk-0201-0400.pdf"),
                (401, 401, "chunk-0401-0401", "chunk-0401-0401.pdf"),
            ],
        )
        self.assertEqual(
            [len(PdfReader(chunk.path).pages) for chunk in chunks],
            [200, 200, 1],
        )
        self.assertEqual(
            sorted(
                path.name
                for path in (self.book_directory / "mineru-chunks").iterdir()
            ),
            ["chunk-0001-0200.pdf", "chunk-0201-0400.pdf", "chunk-0401-0401.pdf"],
        )
        self.assertEqual(list(self.book_directory.glob(".*.chunking")), [])


if __name__ == "__main__":
    unittest.main()
