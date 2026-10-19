from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import BinaryIO
from uuid import uuid4

_CHUNK_SIZE = 1024 * 1024
_BOOK_DIRECTORIES = ("images", "output", "analysis")


class InvalidPdfError(ValueError):
    """Raised when an uploaded file is not a recognizable PDF."""


@dataclass(frozen=True, slots=True)
class BookRecord:
    book_id: str
    original_filename: str
    byte_size: int
    sha256: str
    imported_at: str


class PdfStore:
    """Atomically stores original textbooks and their import metadata."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def import_pdf(self, content: BinaryIO, filename: str) -> BookRecord:
        safe_filename = self._validate_filename(filename)
        first_chunk = content.read(_CHUNK_SIZE)
        if not first_chunk.startswith(b"%PDF-"):
            raise InvalidPdfError("File does not contain a PDF signature")

        self._root.mkdir(parents=True, exist_ok=True)
        book_id = uuid4().hex
        staging_directory = self._root / f".{book_id}.uploading"
        book_directory = self._root / book_id
        staging_directory.mkdir()

        try:
            byte_size, digest = self._write_source(
                staging_directory / "source.pdf", first_chunk, content
            )
            record = BookRecord(
                book_id=book_id,
                original_filename=safe_filename,
                byte_size=byte_size,
                sha256=digest,
                imported_at=datetime.now(timezone.utc).isoformat(),
            )
            self._write_metadata(staging_directory, record)
            for directory_name in _BOOK_DIRECTORIES:
                (staging_directory / directory_name).mkdir()
            os.replace(staging_directory, book_directory)
        except BaseException:
            shutil.rmtree(staging_directory, ignore_errors=True)
            raise

        return record

    @staticmethod
    def _validate_filename(filename: str) -> str:
        safe_filename = PurePath(filename.replace("\\", "/")).name.strip()
        if not safe_filename or Path(safe_filename).suffix.lower() != ".pdf":
            raise InvalidPdfError("Filename must end with .pdf")
        return safe_filename

    @staticmethod
    def _write_source(path: Path, first_chunk: bytes, content: BinaryIO) -> tuple[int, str]:
        digest = hashlib.sha256()
        byte_size = 0
        with path.open("xb") as destination:
            chunk = first_chunk
            while chunk:
                destination.write(chunk)
                digest.update(chunk)
                byte_size += len(chunk)
                chunk = content.read(_CHUNK_SIZE)
        return byte_size, digest.hexdigest()

    @staticmethod
    def _write_metadata(directory: Path, record: BookRecord) -> None:
        metadata = json.dumps(asdict(record), ensure_ascii=False, indent=2, sort_keys=True)
        (directory / "metadata.json").write_text(f"{metadata}\n", encoding="utf-8")
