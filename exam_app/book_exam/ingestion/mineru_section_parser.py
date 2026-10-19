from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .mineru_page_parser import (
    MinerUPageParsingError,
    _load_content_blocks,
    _read_block_text,
)
from .pdf_chunker import PdfChunk

_CHAPTER = re.compile(r"^第([0-9一二三四五六七八九十百零〇两]+)章(?:\s+|[:：、.]?)(.*)$")
_SECTION = re.compile(r"^(\d+)\.(\d+)(?!\.\d)(?:\s+|[、.．])(.+)$")
_DEEP_SECTION = re.compile(r"^(\d+\.\d+\.\d+(?:\.\d+)*)(?:\s+|[、.．])(.+)$")


class MinerUSectionParsingError(RuntimeError):
    """Raised when a book's MinerU results cannot be grouped into sections."""


@dataclass(frozen=True, slots=True)
class MinerUSection:
    section_id: str
    chapter: str
    title: str
    sort_order: int
    page_start: int
    page_end: int
    text: str


def parse_mineru_book_sections(
    book_directory: Path, chunks: Sequence[PdfChunk]
) -> tuple[MinerUSection, ...]:
    """Parse N.M headings with positive text_level; deep headings join their parent.

    Readable text without reliable headings is retained as overview or unsectioned
    content. Directory entries are not automatically distinguished from body text.
    """
    ordered_chunks = _validate_chunks(chunks)
    book_directory = book_directory.resolve()
    extracted_root = (book_directory / "mineru-extracted").resolve()
    blocks: list[tuple[int, dict[str, object]]] = []

    for chunk in ordered_chunks:
        extracted_directory = (extracted_root / chunk.data_id).resolve()
        if extracted_directory.parent != extracted_root:
            raise ValueError("MinerU chunk data IDs must be directory names")
        page_count = chunk.end_page - chunk.start_page + 1
        for block in _load_content_blocks(extracted_directory):
            page_idx = block.get("page_idx")
            if type(page_idx) is not int or not 0 <= page_idx < page_count:
                raise MinerUPageParsingError("MinerU content block has an invalid page_idx")
            blocks.append((chunk.start_page + page_idx, block))

    records: list[MinerUSection] = []
    chapter = ""
    chapter_number = ""
    current_kind = ""
    current_id = ""
    current_title = ""
    current_start = 0
    current_end = 0
    current_text: list[str] = []
    section_numbers: set[str] = set()
    last_block_page = 0

    def flush() -> None:
        nonlocal current_kind, current_text
        if not current_kind:
            return
        if current_kind == "overview" and not current_text:
            current_kind = ""
            return
        records.append(
            MinerUSection(
                section_id=current_id,
                chapter=chapter,
                title=current_title,
                sort_order=len(records) + 1,
                page_start=current_start,
                page_end=current_end,
                text="\n\n".join(current_text),
            )
        )
        current_kind = ""
        current_text = []

    def begin(kind: str, section_id: str, title: str, page: int) -> None:
        nonlocal current_kind, current_id, current_title, current_start, current_end, current_text
        current_kind, current_id, current_title = kind, section_id, title
        current_start = current_end = page
        current_text = []

    for page, block in blocks:
        if page < last_block_page:
            raise MinerUSectionParsingError("MinerU content blocks are not in page order")
        last_block_page = page
        block_type = block.get("type")
        text = _read_block_text(block)
        if not text:
            continue
        is_heading = (
            block_type == "text"
            and type(block.get("text_level")) is int
            and block["text_level"] > 0
        )
        normalized = " ".join(text.split())

        chapter_match = _CHAPTER.match(normalized) if is_heading else None
        if chapter_match:
            flush()
            chapter_number = chapter_match.group(1)
            chapter = normalized
            continue

        deep_match = _DEEP_SECTION.match(normalized) if is_heading else None
        if deep_match:
            parent = ".".join(deep_match.group(1).split(".")[:2])
            if current_kind != "section" or current_id != parent:
                raise MinerUSectionParsingError(
                    f"Deep section {deep_match.group(1)} does not belong to the current parent section {parent}"
                )
            current_text.append(text)
            current_end = page
            continue

        section_match = _SECTION.match(normalized) if is_heading else None
        if section_match:
            number = f"{section_match.group(1)}.{section_match.group(2)}"
            if number in section_numbers:
                raise MinerUSectionParsingError(f"Duplicate section number {number}")
            flush()
            section_numbers.add(number)
            begin("section", number, section_match.group(3), page)
            continue

        if not current_kind:
            if chapter:
                begin("overview", f"chapter-{chapter_number}-overview", "章概述", page)
            else:
                begin("unsectioned", "unsectioned-1", "未分节正文", page)
        current_text.append(text)
        current_end = page

    flush()
    return tuple(records)


def _validate_chunks(chunks: Sequence[PdfChunk]) -> tuple[PdfChunk, ...]:
    chunks = tuple(chunks)
    if not chunks:
        raise ValueError("At least one PDF chunk is required")
    data_ids = [chunk.data_id for chunk in chunks]
    if len(data_ids) != len(set(data_ids)):
        raise ValueError("PDF chunk data IDs must be unique")
    ordered = tuple(sorted(chunks, key=lambda chunk: chunk.start_page))
    expected_start_page = 1
    for chunk in ordered:
        if chunk.start_page < 1 or chunk.end_page < chunk.start_page:
            raise ValueError("PDF chunk has an invalid page range")
        if chunk.start_page != expected_start_page:
            raise ValueError("PDF chunk page ranges must be contiguous from page 1")
        if not chunk.data_id or Path(chunk.data_id).name != chunk.data_id:
            raise ValueError("PDF chunk data IDs must be directory names")
        expected_start_page = chunk.end_page + 1
    return ordered
