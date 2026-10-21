import sqlite3

from .book_context import BookContext


def get_book_toc(context: BookContext) -> dict[str, object]:
    """Read every stored section of one textbook in reading order."""
    database_path = context.database_path
    try:
        if not database_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "BOOK_NOT_READY",
                    "message": f"Textbook database does not exist: {database_path}",
                },
            }

        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro", uri=True
        )
    except (OSError, sqlite3.Error) as error:
        return {
            "ok": False,
            "error": {
                "code": "READ_FAILED",
                "message": f"Textbook database cannot be opened: {error}",
            },
        }

    try:
        try:
            sections_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sections'"
            ).fetchone()
        except sqlite3.Error as error:
            return {
                "ok": False,
                "error": {
                    "code": "READ_FAILED",
                    "message": f"Textbook database cannot be read: {error}",
                },
            }

        if sections_table is None:
            return {
                "ok": False,
                "error": {
                    "code": "BOOK_NOT_READY",
                    "message": "Textbook database is missing the sections table",
                },
            }

        try:
            rows = connection.execute(
                "SELECT id, chapter, title FROM sections ORDER BY sort_order"
            ).fetchall()
        except sqlite3.Error as error:
            return {
                "ok": False,
                "error": {
                    "code": "READ_FAILED",
                    "message": f"Textbook database cannot be read: {error}",
                },
            }

        if not rows:
            return {
                "ok": False,
                "error": {
                    "code": "BOOK_NOT_READY",
                    "message": "Textbook database contains no sections",
                },
            }

        return {
            "ok": True,
            "book_id": context.book_id,
            "sections": [
                {
                    "section_id": row[0],
                    "chapter": row[1],
                    "title": row[2],
                }
                for row in rows
            ],
        }
    finally:
        connection.close()
