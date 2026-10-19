from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .mineru_page_parser import MinerUPage, parse_mineru_chunk_pages
from .mineru_page_writer import MinerUPageWriteResult, write_mineru_pages
from .pdf_chunker import PdfChunk


class MinerUBookImportError(RuntimeError):
    """Raised when a complete set of MinerU chunk results cannot be imported."""


def import_mineru_book_pages(
    book_directory: Path, chunks: Sequence[PdfChunk]
) -> MinerUPageWriteResult:
    """Parse every MinerU chunk result and write the complete book at once."""
    ordered_chunks = _validate_chunks(chunks)
    book_directory = book_directory.resolve()
    extracted_root = (book_directory / "mineru-extracted").resolve()

    pages: list[MinerUPage] = []
    for chunk in ordered_chunks:
        extracted_directory = (extracted_root / chunk.data_id).resolve()
        if extracted_directory.parent != extracted_root:
            raise ValueError("MinerU chunk data IDs must be directory names")
        pages.extend(parse_mineru_chunk_pages(extracted_directory, chunk))

    expected_page_numbers = list(range(1, ordered_chunks[-1].end_page + 1))
    actual_page_numbers = [page.page_number for page in pages]
    if actual_page_numbers != expected_page_numbers:
        raise MinerUBookImportError(
            "MinerU chunk results do not cover every textbook page exactly once"
        )

    return write_mineru_pages(book_directory, pages)


def _validate_chunks(chunks: Sequence[PdfChunk]) -> tuple[PdfChunk, ...]:
    chunks = tuple(chunks)
    if not chunks:
        raise ValueError("At least one PDF chunk is required")

    data_ids = [chunk.data_id for chunk in chunks]
    if len(data_ids) != len(set(data_ids)):
        raise ValueError("PDF chunk data IDs must be unique")

    ordered_chunks = tuple(sorted(chunks, key=lambda chunk: chunk.start_page))
    expected_start_page = 1
    for chunk in ordered_chunks:
        if chunk.start_page < 1 or chunk.end_page < chunk.start_page:
            raise ValueError("PDF chunk has an invalid page range")
        if chunk.start_page != expected_start_page:
            raise ValueError("PDF chunk page ranges must be contiguous from page 1")
        expected_start_page = chunk.end_page + 1

    return ordered_chunks
