from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

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


class MinerUContentParsingError(RuntimeError):
    """Raised when a MinerU content list cannot be read or interpreted."""


def load_mineru_content_blocks(extracted_directory: Path) -> Iterator[dict[str, object]]:
    extracted_directory = extracted_directory.resolve()
    if not extracted_directory.is_dir():
        raise MinerUContentParsingError("MinerU chunk result directory is missing")
    candidates = [
        path
        for path in extracted_directory.rglob("*.json")
        if path.name == "content_list.json"
        or path.name.endswith("_content_list.json")
    ]
    if len(candidates) != 1:
        raise MinerUContentParsingError("Expected one MinerU content_list.json file")
    try:
        with candidates[0].open("r", encoding="utf-8") as source:
            content_value: object = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MinerUContentParsingError("Unable to read MinerU content_list.json") from error
    if not isinstance(content_value, list):
        raise MinerUContentParsingError("MinerU content_list.json must be a list")
    for block_value in cast(list[object], content_value):
        if not isinstance(block_value, dict):
            raise MinerUContentParsingError("MinerU content block must be an object")
        yield cast(dict[str, object], block_value)


def read_mineru_block_text(block: dict[str, object]) -> str:
    block_type = block.get("type")
    if not isinstance(block_type, str):
        raise MinerUContentParsingError("MinerU content block has no valid type")
    if block_type in _IGNORED_TYPES:
        return ""
    parts: list[str] = []
    for field in _FIELDS_BY_TYPE.get(block_type, ("text",)):
        value = block.get(field)
        if value is None:
            continue
        if field in _LIST_FIELDS:
            if not isinstance(value, list):
                raise MinerUContentParsingError(f"MinerU {field} must be a list of text")
            lines: list[str] = []
            for item in cast(list[object], value):
                if not isinstance(item, str):
                    raise MinerUContentParsingError(
                        f"MinerU {field} must be a list of text"
                    )
                if item.strip():
                    lines.append(item.strip())
            if lines:
                parts.append("\n".join(lines))
        else:
            if not isinstance(value, str):
                raise MinerUContentParsingError(f"MinerU {field} must be text")
            text = value.replace("\x00", "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)
