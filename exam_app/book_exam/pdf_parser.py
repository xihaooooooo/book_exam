from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader


class PdfParsingError(RuntimeError):
    """Raised when a stored textbook cannot be parsed."""


@dataclass(frozen=True, slots=True)
class ParseResult:
    page_count: int
    character_count: int
    database_path: Path


class PdfParser:
    """Extracts PDF pages into an atomically published SQLite database."""

    def parse(self, book_directory: Path) -> ParseResult:
        book_directory = book_directory.resolve()
        source_path = book_directory / "source.pdf"
        database_path = book_directory / "sections.db"
        temporary_path = book_directory / f".{uuid4().hex}.parsing.db"

        if not source_path.is_file():
            raise PdfParsingError(f"Stored PDF is missing: {source_path}")

        page_count = 0
        character_count = 0
        try:
            reader = PdfReader(source_path)
            if reader.is_encrypted:
                raise PdfParsingError("Encrypted PDFs are not supported")

            with closing(sqlite3.connect(temporary_path)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE pages (
                        page_number INTEGER PRIMARY KEY CHECK (page_number > 0),
                        text TEXT NOT NULL
                    )
                    """
                )
                for page_count, page in enumerate(reader.pages, start=1):
                    text = (page.extract_text() or "").replace("\x00", "")
                    character_count += len(text)
                    connection.execute(
                        "INSERT INTO pages (page_number, text) VALUES (?, ?)",
                        (page_count, text),
                    )
            os.replace(temporary_path, database_path)
        except PdfParsingError:
            raise
        except Exception as error:
            raise PdfParsingError(f"Unable to parse PDF: {error}") from error
        finally:
            temporary_path.unlink(missing_ok=True)

        return ParseResult(page_count, character_count, database_path)
