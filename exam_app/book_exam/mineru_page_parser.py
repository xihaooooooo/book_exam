from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .pdf_chunker import PdfChunk

_IGNORED_TYPES = {"header", "footer", "page_number"}
_LIST_FIELDS = {
    "code_caption",
    "code_footnote",
    "image_caption",
    "image_footnote",
    "list_items",
    "table_caption",
    "table_footnote",
    "chart_caption",
    "chart_footnote",
}
_FIELDS_BY_TYPE = {
    "code": ("code_caption", "code_body", "code_footnote"),
    "list": ("list_items",),
    "table": ("table_caption", "table_body", "table_footnote"),
    "image": ("image_caption", "content", "image_footnote"),
    "chart": ("chart_caption", "content", "chart_footnote"),
}


class MinerUPageParsingError(RuntimeError):
    """Raised when a chunk result cannot be converted to original PDF pages."""


@dataclass(frozen=True, slots=True)
class MinerUPage:
    page_number: int
    text: str


def parse_mineru_chunk_pages(
    extracted_directory: Path, chunk: PdfChunk
) -> tuple[MinerUPage, ...]:
    """Group one chunk's readable blocks by their original PDF page number."""
    if chunk.start_page < 1 or chunk.end_page < chunk.start_page:
        raise ValueError("PDF chunk has an invalid page range")

    extracted_directory = extracted_directory.resolve()
    if not extracted_directory.is_dir():
        raise MinerUPageParsingError("MinerU chunk result directory is missing")

    candidates = [
        path
        for path in extracted_directory.rglob("*.json")
        if path.name == "content_list.json" or path.name.endswith("_content_list.json")
    ]
    if len(candidates) != 1:
        raise MinerUPageParsingError("Expected one MinerU content_list.json file")

    try:
        with candidates[0].open("r", encoding="utf-8") as source:
            content_value: object = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MinerUPageParsingError("Unable to read MinerU content_list.json") from error
    if not isinstance(content_value, list):
        raise MinerUPageParsingError("MinerU content_list.json must be a list")

    page_count = chunk.end_page - chunk.start_page + 1
    fragments: list[list[str]] = [[] for _ in range(page_count)]
    for block_value in cast(list[object], content_value):
        if not isinstance(block_value, dict):
            raise MinerUPageParsingError("MinerU content block must be an object")
        block = cast(dict[str, object], block_value)
        page_idx = block.get("page_idx")
        if type(page_idx) is not int or not 0 <= page_idx < page_count:
            raise MinerUPageParsingError("MinerU content block has an invalid page_idx")

        block_text = _read_block_text(block)
        if block_text:
            fragments[page_idx].append(block_text)

    return tuple(
        MinerUPage(chunk.start_page + index, "\n\n".join(page_fragments))
        for index, page_fragments in enumerate(fragments)
    )


def _read_block_text(block: dict[str, object]) -> str:
    block_type = block.get("type")
    if not isinstance(block_type, str):
        raise MinerUPageParsingError("MinerU content block has no valid type")
    if block_type in _IGNORED_TYPES:
        return ""

    fields = _FIELDS_BY_TYPE.get(block_type, ("text",))
    parts: list[str] = []
    for field in fields:
        value = block.get(field)
        if value is None:
            continue
        if field in _LIST_FIELDS:
            if not isinstance(value, list):
                raise MinerUPageParsingError(f"MinerU {field} must be a list of text")
            lines: list[str] = []
            for item in cast(list[object], value):
                if not isinstance(item, str):
                    raise MinerUPageParsingError(f"MinerU {field} must be a list of text")
                if item.strip():
                    lines.append(item.strip())
            if lines:
                parts.append("\n".join(lines))
        else:
            if not isinstance(value, str):
                raise MinerUPageParsingError(f"MinerU {field} must be text")
            text = value.replace("\x00", "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)
