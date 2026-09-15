import io
import json
import tempfile
import unittest
from pathlib import Path

from book_exam.pdf_store import InvalidPdfError, PdfStore


class PdfStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = PdfStore(self.root)

    def test_import_pdf_persists_source_and_metadata(self) -> None:
        payload = b"%PDF-1.7\nfinal exam textbook"

        book = self.store.import_pdf(io.BytesIO(payload), "../Discrete Math.pdf")

        book_directory = self.root / book.book_id
        self.assertEqual((book_directory / "source.pdf").read_bytes(), payload)
        self.assertEqual(book.original_filename, "Discrete Math.pdf")
        self.assertEqual(book.byte_size, len(payload))
        metadata = json.loads((book_directory / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["book_id"], book.book_id)
        self.assertEqual(metadata["sha256"], book.sha256)
        for name in ("images", "output", "analysis"):
            self.assertTrue((book_directory / name).is_dir())

    def test_import_pdf_rejects_non_pdf_without_leaving_files(self) -> None:
        with self.assertRaisesRegex(InvalidPdfError, "PDF signature"):
            self.store.import_pdf(io.BytesIO(b"not a pdf"), "notes.pdf")

        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
