"""统一 content.db 的 SQLite 教材仓库。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BookRecord:
    """教材记录。"""

    book_id: str
    title: str
    source_path: str = ""


class SQLiteContentRepository:
    """从统一 content.db 读取教材和章节。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists books (
                    book_id text primary key,
                    title text not null,
                    source_path text not null default '',
                    created_at text not null default current_timestamp
                );

                create table if not exists sections (
                    id integer primary key autoincrement,
                    book_id text not null,
                    section_id text not null,
                    chapter text not null default '',
                    title text not null default '',
                    text text not null default '',
                    page_start integer not null default 0,
                    page_end integer not null default 0,
                    foreign key (book_id) references books(book_id),
                    unique (book_id, section_id)
                );

                create virtual table if not exists sections_fts
                using fts5(book_id, section_id, title, text);
                """
            )

    def list_books(self) -> tuple[BookRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select book_id, title, source_path
                from books
                order by title, book_id
                """,
            ).fetchall()
        return tuple(
            BookRecord(
                book_id=row["book_id"],
                title=row["title"],
                source_path=row["source_path"] or "",
            )
            for row in rows
        )

    def list_sections(self, book_id: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select section_id, title, chapter
                from sections
                where book_id = ?
                order by section_id
                """,
                (book_id,),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def get_section(self, book_id: str, section_id: str) -> dict[str, str]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select section_id, title, text, chapter
                from sections
                where book_id = ? and section_id = ?
                """,
                (book_id, section_id),
            ).fetchone()

        if row is None:
            raise KeyError(f"未找到教材小节: {book_id}/{section_id}")
        return {
            "section_id": row["section_id"],
            "title": row["title"] or "",
            "chapter": row["chapter"] or "",
            "text": row["text"] or "",
        }

    def search_sections(self, book_id: str, query: str) -> list[dict[str, str]]:
        query = query.strip()
        if not query:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                select sections.section_id, sections.title, sections.chapter
                from sections_fts
                join sections
                  on sections.book_id = sections_fts.book_id
                 and sections.section_id = sections_fts.section_id
                where sections_fts match ?
                  and sections_fts.book_id = ?
                order by bm25(sections_fts)
                limit 20
                """,
                (query, book_id),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        return conn


def _summary_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "section_id": row["section_id"],
        "title": row["title"] or "",
        "chapter": row["chapter"] or "",
    }
