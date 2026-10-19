from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .mineru_section_parser import MinerUSection


class MinerUSectionWritingError(RuntimeError):
    """Raised when MinerU sections cannot be written to the textbook database."""


@dataclass(frozen=True, slots=True)
class MinerUSectionWriteResult:
    section_count: int
    character_count: int
    database_path: Path


def write_mineru_sections(
    book_directory: Path, sections: Sequence[MinerUSection]
) -> MinerUSectionWriteResult:
    """Atomically replace the complete set of sections for one stored book."""
    sections = tuple(sections)
    if not sections:
        raise ValueError("At least one MinerU section is required")

    section_ids = [section.section_id for section in sections]
    sort_orders = [section.sort_order for section in sections]
    if any(not isinstance(section_id, str) or not section_id for section_id in section_ids):
        raise ValueError("MinerU section IDs must be non-empty strings")
    if any(type(order) is not int or order < 1 for order in sort_orders):
        raise ValueError("MinerU section sort orders must be positive integers")
    if len(section_ids) != len(set(section_ids)):
        raise ValueError("MinerU section IDs must be unique")
    if len(sort_orders) != len(set(sort_orders)):
        raise ValueError("MinerU section sort orders must be unique")
    for section in sections:
        if (
            type(section.page_start) is not int
            or type(section.page_end) is not int
            or section.page_start < 1
            or section.page_end < section.page_start
        ):
            raise ValueError("MinerU section has an invalid page range")
        if not all(
            isinstance(value, str)
            for value in (section.chapter, section.title, section.text)
        ):
            raise ValueError("MinerU section text fields must be strings")

    book_directory = book_directory.resolve()
    if not book_directory.is_dir():
        raise MinerUSectionWritingError("Stored textbook directory is missing")
    database_path = book_directory / "sections.db"

    try:
        with closing(
            sqlite3.connect(database_path, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sections (
                        id TEXT PRIMARY KEY,
                        chapter TEXT NOT NULL,
                        title TEXT NOT NULL,
                        sort_order INTEGER NOT NULL UNIQUE CHECK (sort_order > 0),
                        page_start INTEGER NOT NULL CHECK (page_start > 0),
                        page_end INTEGER NOT NULL CHECK (page_end >= page_start),
                        text TEXT NOT NULL
                    )
                    """
                )
                connection.execute("DROP TABLE IF EXISTS pages")
                connection.execute("DELETE FROM sections")
                connection.executemany(
                    """
                    INSERT INTO sections
                        (id, chapter, title, sort_order, page_start, page_end, text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            section.section_id,
                            section.chapter,
                            section.title,
                            section.sort_order,
                            section.page_start,
                            section.page_end,
                            section.text,
                        )
                        for section in sections
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
    except sqlite3.Error as error:
        raise MinerUSectionWritingError(
            f"Unable to write MinerU sections: {error}"
        ) from error

    return MinerUSectionWriteResult(
        section_count=len(sections),
        character_count=sum(len(section.text) for section in sections),
        database_path=database_path,
    )
