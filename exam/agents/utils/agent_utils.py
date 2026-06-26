import os
import sqlite3
import threading
from pathlib import Path
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, RemoveMessage
from langchain_openai import ChatOpenAI

from exam.config import DEFAULT_CONFIG


# ── 数据库 ──

_db_path: str | None = None


def init_sections(db_path: str = None):
    """打开 SQLite 库连接。"""
    global _db_path
    _db_path = db_path or ":memory:"


def _connect() -> sqlite3.Connection:
    """每次调用新建连接，避免多线程共享连接问题。"""
    return sqlite3.connect(_db_path or ":memory:")


# ── TOC 重建 ──

def build_toc_from_db(db_path: str) -> list[dict]:
    """从 SQLite sections 表重建目录结构，供 ExamGraph 使用。

    Returns:
        [{"chapter": "第1章", "sections": [{"id": "1.1", "title": "..."}, ...]}, ...]
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, chapter, title FROM sections ORDER BY page_start, id"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    chapters = {}
    for section_id, chapter, title in rows:
        ch = chapter or "正文"
        if ch not in chapters:
            chapters[ch] = []
        chapters[ch].append({"id": section_id, "title": title or section_id})

    return [
        {"chapter": ch, "sections": secs}
        for ch, secs in chapters.items()
    ]


# ── LLM 客户端 ──

def create_llm_client(config: dict = None):
    """创建 LLM 客户端，支持通过配置切换模型。"""
    cfg = config or DEFAULT_CONFIG
    provider = cfg.get("llm_provider", "openai")

    if provider == "openai":
        return ChatOpenAI(
            model=cfg.get("deep_think_llm", "gpt-4.1"),
            temperature=cfg.get("temperature"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL", None),
        )

    if provider == "deepseek":
        return ChatOpenAI(
            model=cfg.get("deep_think_llm", "deepseek-v4-pro"),
            temperature=cfg.get("temperature"),
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com",
        )

    raise ValueError(f"不支持的 LLM 提供商: {provider}")


# ── 书本数据工具（基于 SQLite）──

@tool
def get_section_text(section_id: str) -> str:
    """获取指定章节的完整正文内容。
    Args:
        section_id: 章节编号，如 '2.2'
    """
    db = _connect()
    # 精确匹配
    row = db.execute(
        "SELECT text FROM sections WHERE id = ?", (section_id,)
    ).fetchone()
    if row and row[0]:
        db.close()
        return row[0]

    # 模糊匹配（跳过空文本的父级标题）
    row = db.execute(
        "SELECT id, text FROM sections WHERE id LIKE ? AND text != '' ORDER BY id LIMIT 1",
        (f"{section_id}%",)
    ).fetchone()
    if row and row[1]:
        db.close()
        return row[1]

    db.close()
    return f"未找到 {section_id} 章节的内容"


@tool
def get_surrounding_context(section_id: str, paragraphs: int = 3) -> str:
    """获取指定章节前后相邻章节的上下文。
    Args:
        section_id: 章节编号
        paragraphs: 前后各取几个章节
    """
    db = _connect()
    rows = db.execute(
        "SELECT id, substr(text, 1, 1000) FROM sections ORDER BY id"
    ).fetchall()
    db.close()

    ids = [r[0] for r in rows]
    if section_id not in ids:
        return get_section_text.invoke({"section_id": section_id})

    idx = ids.index(section_id)
    start = max(0, idx - paragraphs)
    end = min(len(rows), idx + paragraphs + 1)

    result = []
    for i in range(start, end):
        result.append(f"--- {rows[i][0]} ---\n{rows[i][1]}")
    return "\n\n".join(result)


@tool
def search_keyword(keyword: str) -> str:
    """全书全文搜索（FTS5），支持多词查询。
    Args:
        keyword: 搜索词，如 "任务就绪表" 或 "prio 就绪表"
    """
    db = _connect()
    try:
        rows = db.execute(
            """SELECT id, snippet(sections_fts, 1, '', '', '...', 40)
               FROM sections_fts WHERE sections_fts MATCH ? LIMIT 10""",
            (keyword,)
        ).fetchall()
    except Exception:
        db.close()
        return f"未找到与 '{keyword}' 相关的内容"

    db.close()

    if not rows:
        return f"未找到与 '{keyword}' 相关的内容"
    return "\n".join(f"[{r[0]}] {r[1]}" for r in rows)


# ── 错题库 ──

_mistakes_db_path: str | None = None


def init_mistakes_db(db_path: str = "cache/mistakes.db"):
    """初始化错题库（建表，幂等）。"""
    global _mistakes_db_path
    _mistakes_db_path = db_path
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY,
            student_id TEXT,
            exam_title TEXT,
            stem TEXT,
            wrong_answer TEXT,
            correct_answer TEXT,
            section_id TEXT,
            topic TEXT,
            error_pattern TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(student_id, exam_title, stem)
        );
    """)
    db.commit()
    db.close()


@tool
def get_weak_sections(student_id: str) -> list[dict]:
    """返回该学生按章节聚合的弱点统计，按错误次数降序。
    优先从 attempts 表查，无数据时回退 mistakes 表。

    Args:
        student_id: 学生标识，如 'S001'
    """
    import os
    attempts_db = os.path.join(
        os.path.dirname(_mistakes_db_path or "cache/mistakes.db"), "attempts.db"
    )

    # 优先从 attempts 查
    if os.path.exists(attempts_db):
        db = sqlite3.connect(attempts_db)
        rows = db.execute(
            """SELECT section_id, topic, COUNT(*) as error_count
               FROM attempts WHERE student_id = ? AND is_correct = 0
               AND section_id != ''
               GROUP BY section_id ORDER BY error_count DESC""",
            (student_id,),
        ).fetchall()
        db.close()
        if rows:
            return [
                {"section_id": r[0], "topic": r[1], "error_count": r[2]}
                for r in rows
            ]

    # 回退 mistakes 表
    db = sqlite3.connect(_mistakes_db_path or "cache/mistakes.db")
    rows = db.execute(
        """SELECT section_id, topic, COUNT(*) as error_count
           FROM mistakes WHERE student_id = ? AND section_id != ''
           GROUP BY section_id ORDER BY error_count DESC""",
        (student_id,),
    ).fetchall()
    db.close()
    return [
        {"section_id": r[0], "topic": r[1], "error_count": r[2]}
        for r in rows
    ]


# ── 消息清理节点 ──

def create_msg_clear_node(context_text: str):
    """创建消息清理节点，清除旧消息并放入上下文锚点。"""
    def clear_messages(state):
        messages = state.get("messages", [])
        removal_ops = [RemoveMessage(id=m.id) for m in messages]
        placeholder = HumanMessage(content=context_text)
        return {"messages": removal_ops + [placeholder]}
    return clear_messages
