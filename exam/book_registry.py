"""Book registry and per-book storage paths."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(PROJECT_ROOT, "cache")
REGISTRY_PATH = os.path.join(CACHE_DIR, "books.json")
DEFAULT_BOOK_ID = "default"

_BOOK_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_book_id(value: str) -> str:
    """Return a filesystem and URL friendly book id."""
    book_id = _BOOK_ID_RE.sub("-", (value or "").strip().lower())
    book_id = book_id.strip("-_")
    if not book_id:
        book_id = f"book-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if book_id in {".", ".."}:
        raise ValueError("非法 book_id")
    return book_id


def _rel(path: str) -> str:
    path = os.path.abspath(path)
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


def _abs(path: str) -> str:
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(PROJECT_ROOT, path))


def build_book_paths(book_id: str) -> dict[str, str]:
    """Build canonical absolute paths for a book id."""
    book_id = sanitize_book_id(book_id)
    base = os.path.join(CACHE_DIR, "books", book_id)
    return {
        "book_id": book_id,
        "book_cache_dir": base,
        "sections_db": os.path.join(base, "sections.db"),
        "attempts_db": os.path.join(base, "attempts.db"),
        "images_dir": os.path.join(base, "images"),
        "media_manifest": os.path.join(base, "media_manifest.json"),
        "output_dir": os.path.join(PROJECT_ROOT, "output", "books", book_id),
        "analysis_dir": os.path.join(PROJECT_ROOT, "analysis", "books", book_id),
        "upload_dir": os.path.join(PROJECT_ROOT, "uploads", "books", book_id),
    }


def _default_book() -> dict[str, Any]:
    return {
        "book_id": DEFAULT_BOOK_ID,
        "title": "默认教材",
        "pdf_path": "",
        "sections_db": _rel(os.path.join(CACHE_DIR, "sections.db")),
        "attempts_db": _rel(os.path.join(CACHE_DIR, "attempts.db")),
        "images_dir": _rel(os.path.join(CACHE_DIR, "images")),
        "media_manifest": _rel(os.path.join(CACHE_DIR, "media_manifest.json")),
        "output_dir": _rel(os.path.join(PROJECT_ROOT, "output")),
        "analysis_dir": _rel(os.path.join(PROJECT_ROOT, "analysis")),
        "created_at": "",
        "updated_at": "",
    }


def _normalize_registry(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    books = data.get("books")
    if not isinstance(books, list):
        books = []

    seen = set()
    normalized = []
    for raw in books:
        if not isinstance(raw, dict):
            continue
        try:
            book_id = sanitize_book_id(str(raw.get("book_id") or ""))
        except ValueError:
            continue
        if not book_id or book_id in seen:
            continue
        seen.add(book_id)
        item = dict(raw)
        item["book_id"] = book_id
        item.setdefault("title", book_id)
        item.setdefault("pdf_path", "")
        item.setdefault("created_at", "")
        item.setdefault("updated_at", "")
        paths = build_book_paths(book_id)
        if book_id == DEFAULT_BOOK_ID:
            item.setdefault("images_dir", _rel(os.path.join(CACHE_DIR, "images")))
            item.setdefault("media_manifest", _rel(os.path.join(CACHE_DIR, "media_manifest.json")))
        else:
            item.setdefault("images_dir", _rel(paths["images_dir"]))
            item.setdefault("media_manifest", _rel(paths["media_manifest"]))
        item.setdefault("sections_db", _rel(paths["sections_db"]))
        item.setdefault("attempts_db", _rel(paths["attempts_db"]))
        item.setdefault("output_dir", _rel(paths["output_dir"]))
        item.setdefault("analysis_dir", _rel(paths["analysis_dir"]))
        normalized.append(item)

    if DEFAULT_BOOK_ID not in seen:
        normalized.insert(0, _default_book())

    default_book_id = sanitize_book_id(str(data.get("default_book_id") or DEFAULT_BOOK_ID))
    if default_book_id not in {b["book_id"] for b in normalized}:
        default_book_id = DEFAULT_BOOK_ID

    return {
        "version": 1,
        "default_book_id": default_book_id,
        "books": normalized,
    }


def load_registry() -> dict[str, Any]:
    """Load books.json, creating a compatible default registry if needed."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = None
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    registry = _normalize_registry(data)
    if not os.path.exists(REGISTRY_PATH) or registry != data:
        save_registry(registry)
    return registry


def save_registry(registry: dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    registry = _normalize_registry(registry)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=2)


def get_book(book_id: str | None = None) -> dict[str, Any]:
    registry = load_registry()
    target = sanitize_book_id(book_id or registry.get("default_book_id") or DEFAULT_BOOK_ID)
    for book in registry["books"]:
        if book["book_id"] == target:
            return _with_absolute_paths(book)
    raise KeyError(f"未知教材: {target}")


def list_books() -> dict[str, Any]:
    registry = load_registry()
    books = []
    for book in registry["books"]:
        item = _with_absolute_paths(book)
        books.append({
            "book_id": item["book_id"],
            "title": item.get("title") or item["book_id"],
            "pdf_path": item.get("pdf_path", ""),
            "has_sections": os.path.exists(item["sections_db"]),
            "has_attempts": os.path.exists(item["attempts_db"]),
            "sections_db": item["sections_db"],
            "attempts_db": item["attempts_db"],
            "images_dir": item["images_dir"],
            "media_manifest": item["media_manifest"],
            "output_dir": item["output_dir"],
            "analysis_dir": item["analysis_dir"],
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
        })
    return {
        "ok": True,
        "default_book_id": registry["default_book_id"],
        "books": books,
    }


def book_exists(book_id: str) -> bool:
    target = sanitize_book_id(book_id)
    return any(book["book_id"] == target for book in load_registry()["books"])


def register_book(
    book_id: str,
    title: str,
    pdf_path: str = "",
    sections_db: str = "",
    attempts_db: str = "",
    output_dir: str = "",
    analysis_dir: str = "",
    set_default: bool = False,
) -> dict[str, Any]:
    """Create or update a book registry entry."""
    book_id = sanitize_book_id(book_id)
    registry = load_registry()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    paths = build_book_paths(book_id)
    entry = {
        "book_id": book_id,
        "title": title or book_id,
        "pdf_path": _rel(pdf_path) if pdf_path else "",
        "sections_db": _rel(sections_db or paths["sections_db"]),
        "attempts_db": _rel(attempts_db or paths["attempts_db"]),
        "images_dir": _rel(paths["images_dir"]),
        "media_manifest": _rel(paths["media_manifest"]),
        "output_dir": _rel(output_dir or paths["output_dir"]),
        "analysis_dir": _rel(analysis_dir or paths["analysis_dir"]),
        "updated_at": now,
    }

    updated = False
    for i, book in enumerate(registry["books"]):
        if book["book_id"] == book_id:
            entry["created_at"] = book.get("created_at") or now
            registry["books"][i] = {**book, **entry}
            updated = True
            break
    if not updated:
        entry["created_at"] = now
        registry["books"].append(entry)

    if set_default:
        registry["default_book_id"] = book_id

    save_registry(registry)
    return get_book(book_id)


def _with_absolute_paths(book: dict[str, Any]) -> dict[str, Any]:
    item = dict(book)
    paths = build_book_paths(item["book_id"])
    item["sections_db"] = _abs(item.get("sections_db") or paths["sections_db"])
    item["attempts_db"] = _abs(item.get("attempts_db") or paths["attempts_db"])
    item["images_dir"] = _abs(item.get("images_dir") or paths["images_dir"])
    item["media_manifest"] = _abs(item.get("media_manifest") or paths["media_manifest"])
    item["output_dir"] = _abs(item.get("output_dir") or paths["output_dir"])
    item["analysis_dir"] = _abs(item.get("analysis_dir") or paths["analysis_dir"])
    item["pdf_path"] = _abs(item.get("pdf_path", "")) if item.get("pdf_path") else ""
    item["book_cache_dir"] = paths["book_cache_dir"]
    item["upload_dir"] = paths["upload_dir"]
    return item
