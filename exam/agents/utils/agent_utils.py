import os
import sqlite3
from pathlib import Path
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, RemoveMessage
from langchain_openai import ChatOpenAI

from exam.config import DEFAULT_CONFIG


# ── 数据库 ──

import threading

_db_path: str | None = None


def init_sections(db_path: str = None):
    """打开 SQLite 库连接。"""
    global _db_path
    _db_path = db_path or ":memory:"


def _connect() -> sqlite3.Connection:
    """每次调用新建连接，避免多线程共享连接问题。"""
    return sqlite3.connect(_db_path or ":memory:")


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

    # 模糊匹配
    row = db.execute(
        "SELECT id, text FROM sections WHERE id LIKE ? LIMIT 1",
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
    """全书搜索关键词，找到包含该词的所有段落及所在章节。
    Args:
        keyword: 要搜索的关键词
    """
    db = _connect()
    rows = db.execute(
        "SELECT id, text FROM sections WHERE text IS NOT NULL AND text LIKE ? LIMIT 10",
        (f"%{keyword}%",)
    ).fetchall()

    if not rows:
        db.close()
        return f"未找到与 '{keyword}' 相关的内容"

    matches = []
    for section_id, text in rows:
        lines = text.split("\n")
        for line in lines:
            if keyword.lower() in line.lower() and len(line.strip()) > 10:
                matches.append(f"[{section_id}] {line.strip()[:200]}")
                if len(matches) >= 10:
                    break
        if len(matches) >= 10:
            break

    db.close()
    return "\n".join(matches) if matches else f"未找到与 '{keyword}' 相关的内容"


# ── 消息清理节点 ──

def create_msg_clear_node(context_text: str):
    """创建消息清理节点，清除旧消息并放入上下文锚点。"""
    def clear_messages(state):
        messages = state.get("messages", [])
        removal_ops = [RemoveMessage(id=m.id) for m in messages]
        placeholder = HumanMessage(content=context_text)
        return {"messages": removal_ops + [placeholder]}
    return clear_messages
