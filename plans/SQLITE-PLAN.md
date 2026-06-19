# SQLite 存储改造计划

## 目标

把 `agent_utils.py` 中的全局字典 `_current_sections` + JSON 缓存 替换为 SQLite，一张库文件搞定存储和查询。

---

## 涉及文件

| 文件 | 改动 |
|---|---|
| `exam/agents/utils/agent_utils.py` | 核心：`init_sections` 改 SQLite，工具改 SQL 查询 |
| `exam/pdf_parser.py` | 不再返回全量 dict，改为直接写 SQLite |
| `exam/graph/exam_graph.py` | `propagate` 接口适配，不再传 `mock_sections` |
| `main.py` | 入口适配，db_path 和 pdf_path |
| `exam/config.py` | 加 `db_path` 配置项（默认 `cache/sections.db`） |
| `exam/mock_book.py` | 不用改，mock 数据导入库即可 |

---

## 数据库设计

一张表：

```sql
CREATE TABLE IF NOT EXISTS sections (
    id          TEXT PRIMARY KEY,    -- 节编号，如 "1.1"、"2.3"
    chapter     TEXT DEFAULT '',     -- 所属章名，如 "第1章 变量和数据类型"
    title       TEXT DEFAULT '',     -- 节标题，如 "1.1 什么是变量"
    page_start  INTEGER DEFAULT 0,
    page_end    INTEGER DEFAULT 0,
    text        TEXT DEFAULT '',     -- 正文内容
    ocr_status  TEXT DEFAULT 'pending'  -- pending / done / failed
);
```

TOC 可以直接查出来：
```sql
SELECT DISTINCT chapter FROM sections ORDER BY id;       -- 章列表
SELECT id, title FROM sections WHERE id LIKE '2.%';     -- 第 2 章所有节
```

---

## 实现步骤

### Step 1 — 改 `agent_utils.py`

**当前（全局字典）：**
```python
_current_sections = {}

def init_sections(sections: dict = None):
    global _current_sections
    if sections:
        _current_sections = sections
```

**改为（SQLite）：**
```python
import sqlite3

_db_conn = None

def init_sections(db_path: str = None, sections: dict = None):
    """打开库；如果传了 sections dict，导入进库（mock 模式或首次 OCR）。"""
    global _db_conn
    _db_conn = sqlite3.connect(db_path)

    _db_conn.execute("""CREATE TABLE IF NOT EXISTS sections (
        id TEXT PRIMARY KEY, chapter TEXT DEFAULT '',
        title TEXT DEFAULT '', page_start INTEGER DEFAULT 0,
        page_end INTEGER DEFAULT 0, text TEXT DEFAULT '',
        ocr_status TEXT DEFAULT 'pending'
    )""")

    if sections:
        data = [(k, "", "", 0, 0, v, "done") for k, v in sections.items()]
        _db_conn.executemany("INSERT OR REPLACE INTO sections VALUES (?,?,?,?,?,?,?)", data)
        _db_conn.commit()

def get_db():
    """获取当前数据库连接。"""
    return _db_conn
```

**工具函数改为 SQL 查询：**

```python
@tool
def get_section_text(section_id: str) -> str:
    row = _db_conn.execute(
        "SELECT text FROM sections WHERE id = ?", (section_id,)
    ).fetchone()
    return row[0] if row else f"未找到 {section_id} 章节的内容"

@tool
def search_keyword(keyword: str) -> str:
    rows = _db_conn.execute(
        "SELECT id, substr(text, 1, 200) FROM sections "
        "WHERE text LIKE ? LIMIT 10", (f"%{keyword}%",)
    ).fetchall()
    return "\n".join(f"[{r[0]}] ...{r[1]}" for r in rows)
```

### Step 2 — 改 `pdf_parser.py`

不再产出 `(toc, sections)` 元组，改为直接写 SQLite：

```python
class PdfParser:
    def __init__(self, pdf_path: str, db_path: str):
        self.pdf_path = pdf_path
        self.db_path = db_path

    def parse(self) -> list:  # 只返回 TOC
        raw_toc = self._read_toc()
        if not raw_toc:
            raw_toc = self._detect_toc_from_text()

        toc = self._build_toc(raw_toc)
        self._init_db(toc)      # 插入所有节，status=pending
        self._ocr_all(toc)      # 逐页 OCR，写完一行 commit 一行
        return toc

    def _init_db(self, toc):
        """建库，插入所有 TOC 条目，status=pending，先从 bookmark 提取章节标题。"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS sections (...)""")
        for ch in toc:
            for sec in ch["sections"]:
                conn.execute(
                    "INSERT OR IGNORE INTO sections (id, chapter, title, ocr_status) VALUES (?,?,?,'pending')",
                    (sec["id"], ch["chapter"], sec["title"])
                )
        conn.commit()
        conn.close()

    def _ocr_all(self, toc):
        """逐节 OCR，每完成一节就 UPDATE 一行 + commit。"""
        conn = sqlite3.connect(self.db_path)
        for ch in toc:
            for sec in ch["sections"]:
                # 跳过已完成的（中断续跑）
                row = conn.execute("SELECT ocr_status FROM sections WHERE id=?", (sec["id"],)).fetchone()
                if row and row[0] == "done":
                    continue

                text = self._ocr_pages(sec["page"], sec.get("page_end"))
                conn.execute(
                    "UPDATE sections SET text=?, ocr_status='done' WHERE id=?",
                    (text, sec["id"])
                )
                conn.commit()
        conn.close()
```

中断续跑天然支持：`ocr_status='pending'` 的是没跑的，`'done'` 的跳过。

### Step 3 — 改 `exam_graph.py`

`propagate` 不再传 `mock_sections`，改为传 `db_path`：

```python
# 之前
def propagate(self, mock_sections=None, toc=None):
    init_sections(mock_sections)
    ...

# 之后
def propagate(self, db_path=None, sections=None, toc=None):
    init_sections(db_path=db_path, sections=sections)
    ...
```

### Step 4 — 改 `main.py`

```python
# Mock 模式
exam.propagate(db_path="cache/sections.db", sections=SECTIONS, toc=TOC)

# PDF 模式
parser = PdfParser(pdf_path, db_path="cache/sections.db")
toc = parser.parse()
exam.propagate(db_path="cache/sections.db", toc=toc)
```

### Step 5 — 改 `config.py`

```python
"db_path": os.path.join(os.path.dirname(__file__), "..", "cache", "sections.db"),
```

---

## 数据流对比

**之前：**
```
PDF → PdfParser → (toc: list, sections: dict) → JSON 缓存
                                                → init_sections(dict) → 全局字典
                                                                       → 工具 dict.get()
```

**之后：**
```
PDF → PdfParser → SQLite（写 OCR 文本）
Mock → init_sections(db_path, sections=dict) → SQLite（写 mock 文本）
                                               → 工具 SELECT 查询
```

---

## 中断续跑示例

```
1. OCR 跑了 50 节，进程挂了
2. sections 表里 50 条 ocr_status='done'，267 条 'pending'
3. 重跑，PdfParser 看到 'done' 的跳过，从第 51 节继续
4. 不用从头来
```

---

## 改动量评估

- `agent_utils.py`：全局字典 → SQLite 连接 + SQL，改 ~30 行
- `pdf_parser.py`：返回 dict → 写 SQLite，改 ~20 行
- `exam_graph.py`：传参调整，改 ~5 行
- `main.py`：入口调整，改 ~5 行
- `config.py`：加一行 `db_path`

总计 ~60 行改动，不改 Agent、图结构、排版。Mock 模式行为完全不变。
