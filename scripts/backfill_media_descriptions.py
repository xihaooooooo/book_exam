"""Backfill conservative media descriptions for an already parsed textbook."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from exam.book_registry import get_book, sanitize_book_id
from exam.config import DEFAULT_CONFIG
from exam.pdf_parser import PdfParser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill image descriptions in media_manifest.json using section/page text.",
    )
    parser.add_argument("--book-id", default="", help="Registered textbook ID.")
    parser.add_argument("--pdf", default="", help="PDF path. Defaults to book registry or manifest pdf_path.")
    parser.add_argument("--db", default="", help="sections.db path.")
    parser.add_argument("--manifest", default="", help="media_manifest.json path.")
    parser.add_argument("--images-dir", default="", help="Book images directory.")
    parser.add_argument("--force", action="store_true", help="Regenerate descriptions even when present.")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Only update media_manifest.json; do not rewrite sections text or FTS.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    book_id = sanitize_book_id(args.book_id) if args.book_id else ""

    if book_id:
        ctx = get_book(book_id)
    else:
        ctx = get_book(None)

    db_path = args.db or ctx.get("sections_db") or DEFAULT_CONFIG.get("db_path", "cache/sections.db")
    manifest_path = args.manifest or ctx.get("media_manifest") or _default_manifest_for_db(db_path)
    pdf_path = args.pdf or ctx.get("pdf_path") or _manifest_pdf_path(manifest_path)
    images_dir = args.images_dir or ctx.get("images_dir") or os.path.join(os.path.dirname(db_path), "images")

    parser = PdfParser(
        pdf_path=pdf_path or "",
        db_path=db_path,
        assets_dir=images_dir,
        manifest_path=manifest_path,
        extract_images=False,
    )
    try:
        count = parser.enrich_existing_media_descriptions(
            force=args.force,
            update_sections=not args.manifest_only,
        )
    finally:
        parser.close()

    print(f"已补全图片描述：{count} 条")
    print(f"媒体清单：{os.path.abspath(manifest_path)}")
    if not args.manifest_only:
        print(f"章节库：{os.path.abspath(db_path)}")
    return 0


def _default_manifest_for_db(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "media_manifest.json")


def _manifest_pdf_path(manifest_path: str) -> str:
    if not manifest_path or not os.path.exists(manifest_path):
        return ""
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get("pdf_path") or "")
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
