import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .search import SimpleExtensionError, load_simple_extension


@dataclass(frozen=True, slots=True)
class BookContext:
    """Identifies the stored textbook that textbook tools operate on."""

    book_id: str
    database_path: Path


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


GET_SECTION_TEXT_TOOL = {
    "name": "get_section_text",
    "description": (
        "读取当前教材指定小节的完整原文，适用于详细阅读或核对出题依据。"
        "section_id 使用目录或搜索结果提供的原始 ID。"
        "返回章节、标题、页码和完整正文。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": (
                    '填写已有小节 ID，例如 "3.2"；'
                    "也支持章概述等非 N.M 格式的已有 ID。"
                ),
            }
        },
        "required": ["section_id"],
        "additionalProperties": False,
    },
    "execute": get_section_text,
}


PEEK_SECTION_TOOL = {
    "name": "peek_section",
    "description": (
        "查看当前教材指定小节的开头，最多返回 500 个字符，"
        "并用 truncated 标记说明是否截断。"
        "需要完整原文时请使用 get_section_text。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": (
                    '填写已有小节 ID，例如 "3.2"；'
                    "也支持章概述等非 N.M 格式的已有 ID。"
                ),
            }
        },
        "required": ["section_id"],
        "additionalProperties": False,
    },
    "execute": peek_section,
}


SEARCH_KEYWORD_TOOL = {
    "name": "search_keyword",
    "description": (
        "按词组搜索当前教材的小节标题和正文，最多返回 5 条结果，"
        "每条包含命中附近的片段，并用 has_more 表示是否还有更多。"
        "需要完整原文时请调用 get_section_text。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "要搜索的词组，去除首尾空白后为 1～100 个字符。",
            }
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    "execute": search_keyword,
}


GET_SURROUNDING_CONTEXT_TOOL = {
    "name": "get_surrounding_context",
    "description": (
        "按教材顺序读取目标小节前后各一节的完整原文，允许跨章；"
        "缺少邻节时返回 null。"
        "目标正文可调用 get_section_text 获取。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section_id": {
                "type": "string",
                "description": (
                    '填写已有小节 ID，例如 "3.2"；'
                    "也支持章概述等非 N.M 格式的已有 ID。"
                ),
            }
        },
        "required": ["section_id"],
        "additionalProperties": False,
    },
    "execute": get_surrounding_context,
}
