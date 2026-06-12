import os
from pathlib import Path
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, RemoveMessage
from langchain_openai import ChatOpenAI

from exam.config import DEFAULT_CONFIG


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


# ── Mock 书本数据工具 ──

from exam.mock_book import SECTIONS

# 全局保存当前 PDF 对象的引用（demo 阶段用 mock 数据）
_current_sections = SECTIONS.copy()


def init_sections(sections: dict = None):
    """初始化章节文本（demo: 用 mock 数据；正式: 用 PDF 解析结果）。"""
    global _current_sections
    if sections:
        _current_sections = sections


@tool
def get_section_text(section_id: str) -> str:
    """获取指定章节的完整正文内容。
    Args:
        section_id: 章节编号，如 '2.2'
    """
    text = _current_sections.get(section_id)
    if text:
        return text
    # 尝试模糊匹配
    for key, val in _current_sections.items():
        if key.startswith(section_id) or section_id in key:
            return val
    return f"未找到 {section_id} 章节的内容"


@tool
def get_surrounding_context(section_id: str, paragraphs: int = 3) -> str:
    """获取指定章节前后相邻章节的上下文。
    Args:
        section_id: 章节编号
        paragraphs: 前后各取几个章节（不是段落数）
    """
    section_ids = list(_current_sections.keys())
    if section_id not in section_ids:
        return get_section_text.invoke({"section_id": section_id})

    idx = section_ids.index(section_id)
    start = max(0, idx - paragraphs)
    end = min(len(section_ids), idx + paragraphs + 1)

    result = []
    for i in range(start, end):
        sid = section_ids[i]
        text = _current_sections[sid]
        result.append(f"--- {sid} ---\n{text[:1000]}")
    return "\n\n".join(result)


@tool
def search_keyword(keyword: str) -> str:
    """全书搜索关键词，找到包含该词的所有段落及所在章节。
    Args:
        keyword: 要搜索的关键词
    """
    matches = []
    for section_id, text in _current_sections.items():
        lines = text.split("\n")
        for line in lines:
            if keyword.lower() in line.lower() and len(line.strip()) > 10:
                matches.append(f"[{section_id}] {line.strip()[:200]}")
                if len(matches) >= 10:
                    break
        if len(matches) >= 10:
            break

    if not matches:
        return f"未找到与 '{keyword}' 相关的内容"
    return "\n".join(matches)


# ── 消息清理节点 ──

def create_msg_clear_node(context_text: str):
    """创建消息清理节点，清除旧消息并放入上下文锚点。"""
    def clear_messages(state):
        messages = state.get("messages", [])
        removal_ops = [RemoveMessage(id=m.id) for m in messages]
        placeholder = HumanMessage(content=context_text)
        return {"messages": removal_ops + [placeholder]}
    return clear_messages
