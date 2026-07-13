"""把教材仓库包装成 LangChain/LangGraph 工具。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

if TYPE_CHECKING:
    from .sqlite_content import SQLiteContentRepository


def create_content_tools(repository: SQLiteContentRepository) -> tuple[BaseTool, ...]:
    """创建主编节点可绑定的教材查询工具。"""

    @tool
    def list_sections(book_id: str) -> list[dict[str, str]]:
        """列出指定教材的小节目录。

        Args:
            book_id: 教材标识。
        """
        return repository.list_sections(book_id)

    @tool
    def get_section(book_id: str, section_id: str) -> dict[str, str]:
        """读取指定教材的小节正文。

        Args:
            book_id: 教材标识。
            section_id: 小节编号。
        """
        return repository.get_section(book_id, section_id)

    @tool
    def search_sections(book_id: str, query: str) -> list[dict[str, str]]:
        """按关键词搜索指定教材的小节。

        Args:
            book_id: 教材标识。
            query: 搜索关键词。
        """
        if not query.strip():
            return []
        return repository.search_sections(book_id, query)

    return (list_sections, get_section, search_sections)
