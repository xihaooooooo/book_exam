# PDF 解析层设计

## 概述

PDF 解析层是整个系统的第一站。它是**非 LLM 节点**，纯代码逻辑。职责：输入 PDF 文件 → 输出目录结构 + 章节文本存取能力。

采用 PyMuPDF（fitz），因为提取质量好、CJK 支持完善、API 简洁。

解析结果持久化到本地缓存目录，同一份 PDF 只需解析一次。

---

## 一、持久化策略

缓存目录：`~/.book-to-exam/cache/`

每个 PDF 用文件内容的 SHA-256 作为唯一标识，防止同名文件覆盖：

```
~/.book-to-exam/cache/
├── index.json                          # PDF 路径 → 缓存目录映射
└── <hash>/                             # 一个 PDF 一个目录
    ├── toc.json                        # 目录结构
    ├── sections.json                   # {section_id: 全文}
    └── index.json                      # 倒排索引
```

---

## 二、核心类

```python
import hashlib
import json
import os
from pathlib import Path

CACHE_DIR = Path.home() / ".book-to-exam" / "cache"


class PDFManager:
    """PDF 解析管理器，提供目录提取和章节文本存取"""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc_hash = self._compute_hash(pdf_path)
        self.cache_dir = CACHE_DIR / self.doc_hash

        self.toc: list[dict] = []
        self.sections: dict[str, str] = {}
        self.search_index: dict[str, list[str]] = {}

        if self._cache_exists():
            self._load_from_cache()
        else:
            self.doc = fitz.open(pdf_path)
            self._build()
            self._save_to_cache()
            self.doc.close()

    def _compute_hash(self, pdf_path: str) -> str:
        """计算文件 SHA-256"""
        h = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()[:16]

    def _cache_exists(self) -> bool:
        return (self.cache_dir / "toc.json").exists()

    def _load_from_cache(self):
        with open(self.cache_dir / "toc.json", "r", encoding="utf-8") as f:
            self.toc = json.load(f)
        with open(self.cache_dir / "sections.json", "r", encoding="utf-8") as f:
            self.sections = json.load(f)
        # 倒排索引运行时重建（轻量）
        self.search_index = self._build_index()

    def _save_to_cache(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_dir / "toc.json", "w", encoding="utf-8") as f:
            json.dump(self.toc, f, ensure_ascii=False, indent=2)
        with open(self.cache_dir / "sections.json", "w", encoding="utf-8") as f:
            json.dump(self.sections, f, ensure_ascii=False, indent=2)
        # 更新全局索引
        self._update_index_file()

    def _update_index_file(self):
        """记录 PDF 路径 → 缓存目录映射"""
        index_file = CACHE_DIR / "index.json"
        index = {}
        if index_file.exists():
            with open(index_file, "r", encoding="utf-8") as f:
                index = json.load(f)
        index[self.pdf_path] = str(self.cache_dir)
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _build(self):
        """解析 PDF，提取目录和章节文本"""
        self.toc = self._extract_toc()
        self.sections = self._split_sections()
        self.search_index = self._build_index()

    def get_section(self, section_id: str) -> str:
        """获取指定章节的正文"""

    def get_context_window(self, section_id: str, paragraphs: int) -> str:
        """获取指定章节前后 N 段上下文"""

    def search(self, keyword: str) -> str:
        """全书搜索关键词"""
```

---

## 二、目录提取

### 2.1 优先读 PDF 内嵌目录

```python
def _extract_toc(self) -> list[dict]:
    # PyMuPDF 的内嵌目录 API
    raw_toc = self.doc.get_toc()  # 返回 [[level, title, page], ...]

    if raw_toc and len(raw_toc) >= 3:
        return self._parse_embedded_toc(raw_toc)
    else:
        return self._auto_detect_toc()
```

### 2.2 格式转换

`get_toc()` 返回的是 `[层级, 标题, 页码]`，转为树形结构：

```python
def _parse_embedded_toc(self, raw_toc):
    """
    raw_toc 示例:
      [1, "第2章 变量和简单数据类型", 25]
      [2, "2.1 变量", 26]
      [2, "2.2 字符串", 30]
      [1, "第3章 列表简介", 45]
      [2, "3.1 列表是什么", 46]
      [2, "3.2 增删元素", 50]

    输出:
      [
        {
          "chapter": "第2章 变量和简单数据类型",
          "page_start": 25,
          "sections": [
            {"id": "2.1", "title": "2.1 变量", "page": 26},
            {"id": "2.2", "title": "2.2 字符串", "page": 30},
          ]
        },
        ...
      ]
    """
    result = []
    current_chapter = None

    for level, title, page in raw_toc:
        if level == 1:
            current_chapter = {
                "chapter": title.strip(),
                "page_start": page,
                "sections": []
            }
            result.append(current_chapter)
        elif level == 2 and current_chapter is not None:
            section_id = self._extract_section_id(title)
            current_chapter["sections"].append({
                "id": section_id,
                "title": title.strip(),
                "page": page,
            })

    return result
```

### 2.3 自动检测（无内嵌目录时）

```python
def _auto_detect_toc(self) -> list[dict]:
    """从正文中匹配章节标题模式"""
    patterns = [
        r'第[一二三四五六七八九十\d]+章\s+',  # 第X章
        r'\d+\.\d+\s+[^\d]',                 # 2.1 xxx
    ]

    toc = []
    for page_num in range(min(10, len(self.doc))):
        text = self.doc[page_num].get_text()
        for line in text.split('\n'):
            if matches_patterns(line):
                toc.append({...})

    return toc
```

---

## 三、章节文本提取

### 3.1 按目录切分

有了目录和每节的起始页码，把全书文本按节切分：

```python
def _split_sections(self) -> dict[str, str]:
    sections = {}

    for chapter in self.toc:
        for i, section in enumerate(chapter["sections"]):
            section_id = section["id"]

            # 确定当前节的页码范围
            start_page = section["page"] - 1  # PyMuPDF 页码从 0 开始

            # 结束页 = 下一节的起始页 - 1；如果是最后一节，取下一章的第一页 - 1
            if i + 1 < len(chapter["sections"]):
                end_page = chapter["sections"][i + 1]["page"] - 1
            else:
                end_page = self._next_chapter_start(chapter) - 1

            # 提取该页范围的文本
            text = ""
            for page_num in range(start_page, end_page + 1):
                text += self.doc[page_num].get_text() + "\n"

            # 去掉前一节残留的尾行，去掉下一节标题的首行
            text = self._trim_boundaries(text, section)
            sections[section_id] = text.strip()

    return sections
```

### 3.2 核心方法：get_section

```python
def get_section(self, section_id: str) -> str:
    """工具直接调用"""
    return self.sections.get(section_id, f"未找到 {section_id} 章节")
```

### 3.3 核心方法：get_context_window

```python
def get_context_window(self, section_id: str, paragraphs: int = 3) -> str:
    """获取章节前后的上下文"""
    # 找到该章节的相邻章节
    adjacent_ids = self._get_adjacent_section_ids(section_id)

    result = ""
    for adj_id in adjacent_ids:
        result += f"--- {adj_id} ---\n"
        result += self.sections.get(adj_id, "")[:paragraphs * 500]  # 每段约 500 字符
        result += "\n"

    # 包含目标章节本身
    result += f"--- {section_id} ---\n"
    result += self.sections.get(section_id, "")
    return result
```

### 3.4 核心方法：search

```python
def search(self, keyword: str) -> str:
    """倒排索引搜索"""
    matches = []
    for section_id, paragraphs in self.search_index.items():
        for para in paragraphs:
            if keyword.lower() in para.lower():
                matches.append(f"[{section_id}] {para.strip()[:200]}...")

    if not matches:
        return f"未找到 '{keyword}'"
    return "\n\n".join(matches[:10])  # 最多 10 条
```

---

## 四、倒排索引

```python
def _build_index(self) -> dict[str, list[str]]:
    """为每个章节建立段落倒排索引"""
    index = {}
    for section_id, text in self.sections.items():
        paragraphs = [p for p in text.split('\n\n') if len(p.strip()) > 20]
        if paragraphs:
            index[section_id] = paragraphs
    return index
```

---

## 五、工具绑定

三个工具是 LangChain `@tool` 装饰的函数，内部调用 `PDFManager`：

```python
# 全局单例
pdf_manager: PDFManager | None = None

def init_pdf_manager(pdf_path: str):
    global pdf_manager
    pdf_manager = PDFManager(pdf_path)
    return pdf_manager.toc


@tool
def get_section_text(section_id: str) -> str:
    """获取指定章节的完整正文内容。"""
    if pdf_manager is None:
        return "错误：PDF 未加载"
    return pdf_manager.get_section(section_id)


@tool
def get_surrounding_context(section_id: str, paragraphs: int = 3) -> str:
    """获取指定章节前后 N 段的上下文。"""
    if pdf_manager is None:
        return "错误：PDF 未加载"
    return pdf_manager.get_context_window(section_id, paragraphs)


@tool
def search_keyword(keyword: str) -> str:
    """全书搜索关键词，找到该概念所在位置和上下文。"""
    if pdf_manager is None:
        return "错误：PDF 未加载"
    return pdf_manager.search(keyword)
```

---

## 六、在 LangGraph 中的位置

```python
# setup.py

def pdf_parser_node(state):
    """非 LLM 节点：解析 PDF，提取目录"""
    pdf_path = state["pdf_path"]
    toc = init_pdf_manager(pdf_path)
    return {"toc": toc}

workflow.add_node("pdf_parser", pdf_parser_node)
workflow.add_edge(START, "pdf_parser")
workflow.add_edge("pdf_parser", "chief_editor")  # 下一步：主编
```

---

## 七、目录输出格式

最终给到主编的 `toc` 结构：

```python
[
    {
        "chapter": "第2章 变量和简单数据类型",
        "page_start": 25,
        "sections": [
            {"id": "2.1", "title": "2.1 变量"},
            {"id": "2.2", "title": "2.2 字符串"},
            {"id": "2.3", "title": "2.3 数字"},
        ]
    },
    {
        "chapter": "第3章 列表简介",
        "page_start": 45,
        "sections": [
            {"id": "3.1", "title": "3.1 列表是什么"},
            {"id": "3.2", "title": "3.2 修改、添加和删除元素"},
            {"id": "3.3", "title": "3.3 组织列表"},
        ]
    },
]
```

---

## 八、依赖

```python
# requirements.txt
PyMuPDF>=1.24.0
```
