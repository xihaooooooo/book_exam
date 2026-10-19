from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .mineru_section_parser import parse_mineru_book_sections
from .mineru_section_writer import MinerUSectionWriteResult, write_mineru_sections
from .pdf_chunker import PdfChunk


def import_mineru_book_sections(
    book_directory: Path, chunks: Sequence[PdfChunk]
) -> MinerUSectionWriteResult:
    """Parse the complete book before replacing its stored sections."""
    sections = parse_mineru_book_sections(book_directory, chunks)
    return write_mineru_sections(book_directory, sections)
