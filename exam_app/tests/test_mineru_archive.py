import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from book_exam import (
    MinerUArchiveError,
    extract_mineru_archive,
    extract_mineru_results,
)


class MinerUArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.archive_path = self.root / "mineru-result.zip"

    def test_extracts_batch_results_in_chunk_order(self) -> None:
        result_directory = self.root / "mineru-results"
        result_directory.mkdir()
        for data_id, markdown in (
            ("chunk-0201-0400", "# 第二片"),
            ("chunk-0001-0200", "# 第一片"),
        ):
            with ZipFile(result_directory / f"{data_id}.zip", "w") as archive:
                archive.writestr("full.md", markdown)

        destinations = extract_mineru_results(self.root)

        self.assertEqual(
            [destination.name for destination in destinations],
            ["chunk-0001-0200", "chunk-0201-0400"],
        )
        self.assertEqual(
            [
                (destination / "full.md").read_text(encoding="utf-8")
                for destination in destinations
            ],
            ["# 第一片", "# 第二片"],
        )
        extracted_root = self.root / "mineru-extracted"
        self.assertEqual(list(extracted_root.glob(".*.extracting")), [])

    def test_extracts_markdown_and_images(self) -> None:
        with ZipFile(self.archive_path, "w") as archive:
            archive.writestr("full.md", "# 第一章")
            archive.writestr("images/figure.png", b"image")

        destination = extract_mineru_archive(self.archive_path, self.root / "mineru")

        self.assertEqual((destination / "full.md").read_text(encoding="utf-8"), "# 第一章")
        self.assertEqual((destination / "images" / "figure.png").read_bytes(), b"image")

    def test_rejects_paths_outside_destination(self) -> None:
        with ZipFile(self.archive_path, "w") as archive:
            archive.writestr("../escaped.txt", "unsafe")

        with self.assertRaisesRegex(MinerUArchiveError, "unsafe path"):
            extract_mineru_archive(self.archive_path, self.root / "mineru")

        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertFalse((self.root / "mineru").exists())


if __name__ == "__main__":
    unittest.main()
