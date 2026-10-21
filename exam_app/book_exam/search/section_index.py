from __future__ import annotations

import sqlite3


def rebuild_section_index(connection: sqlite3.Connection) -> None:
    """Create and rebuild the index inside the caller's active transaction.

    The caller must first load the simple extension and start a transaction.
    FTS rowids correspond to ``sections.rowid``; this helper does not commit or
    roll back the connection.
    """
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
            id UNINDEXED,
            title,
            text,
            content='sections',
            tokenize='simple 0'
        )
        """
    )
    connection.execute(
        "INSERT INTO sections_fts(sections_fts) VALUES ('rebuild')"
    )
