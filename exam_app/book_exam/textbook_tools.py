import sqlite3

from .book_context import BookContext
from .search import SimpleExtensionError, load_simple_extension


def get_section_text(context: BookContext, section_id: str) -> dict[str, object]:
    """Read one section's complete text from the textbook database."""
    if not isinstance(section_id, str) or not section_id.strip():
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "section_id must be a non-empty string",
            },
        }

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
        sections_table = None
        row = None
        try:
            sections_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sections'"
            ).fetchone()
            if sections_table is not None:
                row = connection.execute(
                    "SELECT id, chapter, title, page_start, page_end, text "
                    "FROM sections WHERE id = ?",
                    (section_id,),
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

        if row is None:
            return {
                "ok": False,
                "error": {
                    "code": "SECTION_NOT_FOUND",
                    "message": (
                        f"Section '{section_id}' does not exist in textbook "
                        f"'{context.book_id}'"
                    ),
                },
            }

        return {
            "ok": True,
            "book_id": context.book_id,
            "section": {
                "section_id": row[0],
                "chapter": row[1],
                "title": row[2],
                "page_start": row[3],
                "page_end": row[4],
                "text": row[5],
            },
        }
    finally:
        connection.close()


def peek_section(context: BookContext, section_id: str) -> dict[str, object]:
    """Return the beginning of one section with a truncation flag."""
    result = get_section_text(context, section_id)
    if not result["ok"]:
        return result

    section = result["section"]
    text = section["text"]
    return {
        "ok": True,
        "book_id": result["book_id"],
        "section": {
            "section_id": section["section_id"],
            "chapter": section["chapter"],
            "title": section["title"],
            "page_start": section["page_start"],
            "page_end": section["page_end"],
            "text": text[:500],
            "truncated": len(text) > 500,
        },
    }


def search_keyword(context: BookContext, keyword: str) -> dict[str, object]:
    """Search one textbook's indexed titles and text for a literal phrase."""
    if not isinstance(keyword, str):
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "keyword must be a string",
            },
        }

    keyword = keyword.strip()
    if not 1 <= len(keyword) <= 100:
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "keyword must contain 1 to 100 characters after trimming",
            },
        }

    if "\x00" in keyword:
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "keyword must not contain NUL characters",
            },
        }

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
            index_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sections_fts'"
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

        if index_table is None:
            return {
                "ok": False,
                "error": {
                    "code": "INDEX_NOT_READY",
                    "message": "Textbook search index is missing the sections_fts table",
                },
            }

        try:
            load_simple_extension(connection)
        except (OSError, SimpleExtensionError) as error:
            return {
                "ok": False,
                "error": {
                    "code": "SEARCH_UNAVAILABLE",
                    "message": f"Keyword search is unavailable: {error}",
                },
            }

        match_expression = '"' + keyword.replace('"', '""') + '"'
        try:
            rows = connection.execute(
                "SELECT sections.id, sections.chapter, sections.title, "
                "sections.page_start, sections.page_end, "
                "snippet(sections_fts, -1, '', '', '…', 64) "
                "FROM sections_fts "
                "JOIN sections ON sections.rowid = sections_fts.rowid "
                "WHERE sections_fts MATCH ? "
                "ORDER BY bm25(sections_fts), sections.sort_order "
                "LIMIT 6",
                (match_expression,),
            ).fetchall()
        except sqlite3.Error as error:
            return {
                "ok": False,
                "error": {
                    "code": "READ_FAILED",
                    "message": f"Textbook database cannot be read: {error}",
                },
            }

        matches = [
            {
                "section_id": row[0],
                "chapter": row[1],
                "title": row[2],
                "page_start": row[3],
                "page_end": row[4],
                "snippet": row[5],
            }
            for row in rows[:5]
        ]
        return {
            "ok": True,
            "book_id": context.book_id,
            "keyword": keyword,
            "matches": matches,
            "has_more": len(rows) > 5,
        }
    finally:
        connection.close()


def get_surrounding_context(
    context: BookContext, section_id: str
) -> dict[str, object]:
    """Read the full sections immediately before and after one section."""
    if not isinstance(section_id, str) or not section_id.strip():
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "section_id must be a non-empty string",
            },
        }

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
                "SELECT 0 AS role, id, chapter, title, page_start, page_end, text "
                "FROM sections WHERE id = ? "
                "UNION ALL "
                "SELECT 1, id, chapter, title, page_start, page_end, text "
                "FROM sections WHERE sort_order = ("
                "SELECT MAX(neighbor.sort_order) FROM sections AS neighbor "
                "WHERE neighbor.sort_order < ("
                "SELECT target.sort_order FROM sections AS target "
                "WHERE target.id = ?"
                ")"
                ") "
                "UNION ALL "
                "SELECT 2, id, chapter, title, page_start, page_end, text "
                "FROM sections WHERE sort_order = ("
                "SELECT MIN(neighbor.sort_order) FROM sections AS neighbor "
                "WHERE neighbor.sort_order > ("
                "SELECT target.sort_order FROM sections AS target "
                "WHERE target.id = ?"
                ")"
                ")",
                (section_id, section_id, section_id),
            ).fetchall()
        except sqlite3.Error as error:
            return {
                "ok": False,
                "error": {
                    "code": "READ_FAILED",
                    "message": f"Textbook database cannot be read: {error}",
                },
            }

        sections = {
            row[0]: {
                "section_id": row[1],
                "chapter": row[2],
                "title": row[3],
                "page_start": row[4],
                "page_end": row[5],
                "text": row[6],
            }
            for row in rows
        }
        if 0 not in sections:
            return {
                "ok": False,
                "error": {
                    "code": "SECTION_NOT_FOUND",
                    "message": (
                        f"Section '{section_id}' does not exist in textbook "
                        f"'{context.book_id}'"
                    ),
                },
            }

        return {
            "ok": True,
            "book_id": context.book_id,
            "target_section_id": section_id,
            "previous": sections.get(1),
            "next": sections.get(2),
        }
    finally:
        connection.close()
