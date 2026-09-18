from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .mineru_page_parser import MinerUPage


class MinerUPageWritingError(RuntimeError):
    """Raised when MinerU pages cannot be written to the textbook database."""


@dataclass(frozen=True, slots=True)
class MinerUPageWriteResult:
    page_count: int
    character_count: int
    database_path: Path


def write_mineru_pages(
    book_directory: Path, pages: Sequence[MinerUPage]
) -> MinerUPageWriteResult:
    """Create or update textbook pages from MinerU output in one transaction."""
    pages = tuple(pages)
    if not pages:
        raise ValueError("At least one MinerU page is required")

    page_numbers = [page.page_number for page in pages]
    if any(
        type(page_number) is not int or page_number < 1
        for page_number in page_numbers
    ):
        raise ValueError("MinerU page numbers must be positive")
    if len(page_numbers) != len(set(page_numbers)):
        raise ValueError("MinerU page numbers must be unique")

    book_directory = book_directory.resolve()
    if not book_directory.is_dir():
        raise MinerUPageWritingError("Stored textbook directory is missing")
    database_path = book_directory / "sections.db"

    try:
        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pages (
                    page_number INTEGER PRIMARY KEY CHECK (page_number > 0),
                    text TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO pages (page_number, text) VALUES (?, ?)
                ON CONFLICT(page_number) DO UPDATE SET text = excluded.text
                """,
                ((page.page_number, page.text) for page in pages),
            )
    except MinerUPageWritingError:
        raise
    except sqlite3.Error as error:
        raise MinerUPageWritingError(
            f"Unable to write MinerU pages: {error}"
        ) from error

    return MinerUPageWriteResult(
        page_count=len(pages),
        character_count=sum(len(page.text) for page in pages),
        database_path=database_path,
    )
