import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pypdf import PdfWriter

from book_exam import PdfParser, PdfParsingError, PdfStore


class PdfParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_parse_extracts_every_page_into_sqlite(self) -> None:
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        pdf.seek(0)
        book = PdfStore(self.root).import_pdf(pdf, "calculus.pdf")

        result = PdfParser().parse(self.root / book.book_id)

        self.assertEqual(result.page_count, 2)
        with closing(sqlite3.connect(result.database_path)) as connection:
            pages = connection.execute(
                "SELECT page_number, text FROM pages ORDER BY page_number"
            ).fetchall()
        self.assertEqual(pages, [(1, ""), (2, "")])

    def test_parse_rejects_missing_source(self) -> None:
        with self.assertRaisesRegex(PdfParsingError, "missing"):
            PdfParser().parse(self.root / "unknown-book")


if __name__ == "__main__":
    unittest.main()
