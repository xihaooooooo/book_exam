"""PDF 解析器：提取目录结构，写入 SQLite。扫描版 PDF 可用 MinerU OCR。"""

import os
import re
import sqlite3

import fitz


class PdfParser:
    """解析 PDF 教材，TOC 树 + 正文写入 SQLite sections 表。"""

    def __init__(self, pdf_path: str, db_path: str, mineru_token: str = None):
        self.pdf_path = pdf_path
        self.db_path = db_path
        self.mineru_token = mineru_token
        self._doc = None

    @property
    def doc(self):
        if self._doc is None:
            self._doc = fitz.open(self.pdf_path)
        return self._doc

    def parse(self) -> list[dict]:
        """解析 PDF，写入 SQLite，返回 TOC 列表。"""
        raw_toc = self._read_toc()

        if raw_toc:
            print(f"[PDF] 从内置书签读取到 {len(raw_toc)} 条目录")
        else:
            print(f"[PDF] 无内置书签，从文本检测标题...")
            raw_toc = self._detect_toc_from_text()

        toc = self._build_toc(raw_toc)
        self._init_db(toc)

        # 检查是否需要 OCR
        pending = self._count_pending()
        if pending and self.mineru_token:
            print(f"[PDF] {pending} 节无文字，启动 MinerU OCR ...")
            toc = self._ocr_via_mineru(toc)
        elif pending:
            print(f"[PDF] {pending} 节无文字内容，需要 OCR（可设置 --mineru-token 启用 MinerU）")

        print(f"[PDF] 解析完成：{len(toc)} 章，{sum(len(ch['sections']) for ch in toc)} 节")
        return toc

    def close(self):
        if self._doc:
            self._doc.close()
            self._doc = None

    # ── TOC 读取 ──

    def _read_toc(self) -> list[dict]:
        """读取 PDF 内置书签。"""
        builtin = self.doc.get_toc()
        if not builtin:
            return []
        entries = []
        for level, title, page in builtin:
            title = title.strip()
            if not title:
                continue
            entries.append({"level": level, "title": title, "page": page})
        return entries

    def _detect_toc_from_text(self) -> list[dict]:
        """扫描全文，检测标题模式（无书签时的降级方案）。"""
        entries = []
        seen = set()

        for page_num in range(self.doc.page_count):
            text = self.doc[page_num].get_text("text")
            if not text:
                continue
            for line in text.split("\n"):
                line = line.strip()
                if not line or len(line) > 80:
                    continue
                ch_match = re.match(r"^(第[一二三四五六七八九十\d]+章)\s*(.*)", line)
                sec_match = re.match(r"^(\d+\.\d+(?:\.\d+)?)\s+(.*)", line)
                if ch_match:
                    key = ch_match.group(1)
                    if key not in seen:
                        seen.add(key)
                        entries.append({"level": 1, "title": line, "page": page_num + 1})
                elif sec_match:
                    key = sec_match.group(1)
                    if key not in seen:
                        seen.add(key)
                        entries.append({"level": 2, "title": line, "page": page_num + 1})

        entries.sort(key=lambda e: (e["page"], e["level"]))
        return entries

    # ── 构建 TOC 树 ──

    def _build_toc(self, raw_entries: list[dict]) -> list[dict]:
        """将原始目录条目组装为 TOC 树。"""
        if not raw_entries:
            return []

        levels = {e["level"] for e in raw_entries}

        # 全是 level 1：每个条目单独当一节，自动分组为章
        if levels == {1}:
            return self._build_flat_toc(raw_entries)

        # 混合 level：level 1 = 章，level >= 2 = 节
        chapters = []
        current_chapter = None
        auto_ch_idx = 0

        for entry in raw_entries:
            level = entry["level"]
            title = entry["title"]
            page = entry["page"]

            if level == 1:
                current_chapter = {"chapter": title, "sections": []}
                chapters.append(current_chapter)
            elif level >= 2:
                if current_chapter is None:
                    auto_ch_idx += 1
                    sec_num = self._parse_section_number(title)
                    ch_num = sec_num.split(".")[0] if "." in sec_num else str(auto_ch_idx)
                    current_chapter = {"chapter": f"第{ch_num}章", "sections": []}
                    chapters.append(current_chapter)

                section_id = self._infer_section_id(title)
                current_chapter["sections"].append({
                    "id": section_id,
                    "title": title,
                    "page": page,
                })

        return [ch for ch in chapters if ch["sections"]]

    def _build_flat_toc(self, raw_entries: list[dict]) -> list[dict]:
        """全 level 1 的扁平书签：自动按数字分组为章。"""
        # 过滤掉非正文的前置页面
        skip_keywords = {"封面", "书名", "版权", "前言", "目录", "序", "参考文献", "附录"}
        body_entries = [e for e in raw_entries
                        if e["title"] not in skip_keywords and "前言" not in e["title"]]

        if not body_entries:
            return []

        chapters = []
        current_chapter = None
        current_ch_num = 1

        for entry in body_entries:
            title = entry["title"]
            page = entry["page"]

            # 检测是否是新章开头（标题含 "第X章" 或页面上有大标题）
            ch_match = re.match(r"^第([一二三四五六七八九十\d]+)章", title)
            if ch_match:
                current_chapter = {"chapter": title, "sections": []}
                chapters.append(current_chapter)
                continue

            # 没有章时自动建章
            if current_chapter is None:
                current_chapter = {"chapter": f"第{current_ch_num}章", "sections": []}
                chapters.append(current_chapter)

            section_id = self._infer_section_id(title)
            current_chapter["sections"].append({
                "id": section_id,
                "title": title,
                "page": page,
            })

            # 每 10 节自动切一章（无章节标题时的降级策略）
            if re.match(r"^\d+$", title) and len(current_chapter["sections"]) >= 10:
                ch_num = int(title) // 10
                # 检查是否应该在当前位置切章（基于数字范围）
                sec_num = int(title)
                if sec_num % 10 == 0:
                    current_ch_num += 1
                    current_chapter = {"chapter": f"第{current_ch_num}章", "sections": []}
                    chapters.append(current_chapter)

        return [ch for ch in chapters if ch["sections"]]

    def _infer_section_id(self, title: str) -> str:
        match = re.match(r"^(\d+\.\d+(?:\.\d+)?)", title)
        if match:
            return match.group(1)
        return title[:20]

    def _parse_section_number(self, title: str) -> str:
        match = re.match(r"^(\d+\.\d+(?:\.\d+)?)", title)
        return match.group(1) if match else "0.0"

    # ── SQLite 写入 ──

    def _init_db(self, toc: list[dict]):
        """建库并初始化 TOC 条目，同时提取每节的文本（有则写，无则标记 pending）。"""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE IF EXISTS sections")
        conn.execute("""CREATE TABLE sections (
            id TEXT PRIMARY KEY,
            chapter TEXT DEFAULT '',
            title TEXT DEFAULT '',
            page_start INTEGER DEFAULT 0,
            page_end INTEGER DEFAULT 0,
            text TEXT DEFAULT '',
            ocr_status TEXT DEFAULT 'pending'
        )""")

        # 计算每节的页码范围
        all_sections = []
        for ch in toc:
            for sec in ch["sections"]:
                all_sections.append((ch["chapter"], sec))

        for i, (chapter, sec) in enumerate(all_sections):
            section_id = sec["id"]
            start_page = sec["page"] - 1  # 0-based

            # 结束页 = 下一节的起始页，或文档末尾
            if i + 1 < len(all_sections):
                end_page = all_sections[i + 1][1]["page"] - 1
            else:
                end_page = self.doc.page_count

            # 提取文本
            text = self._extract_text(start_page, end_page)

            conn.execute(
                "INSERT OR IGNORE INTO sections (id, chapter, title, page_start, page_end, text, ocr_status) "
                "VALUES (?,?,?,?,?,?,?)",
                (section_id, chapter, sec["title"], start_page + 1, end_page,
                 text, "done" if text else "pending")
            )
        # 建 FTS5 全文索引
        conn.execute("DROP TABLE IF EXISTS sections_fts")
        conn.execute("CREATE VIRTUAL TABLE sections_fts USING fts5(id, text)")
        conn.execute("INSERT INTO sections_fts SELECT id, text FROM sections WHERE text != ''")
        conn.commit()
        conn.close()

    def _extract_text(self, start_page: int, end_page: int) -> str:
        """提取指定页码范围（0-based）的文本。"""
        parts = []
        for p in range(start_page, min(end_page, self.doc.page_count)):
            page_text = self.doc[p].get_text("text")
            if page_text.strip():
                parts.append(page_text.strip())
        return "\n\n".join(parts)

    def _count_pending(self) -> int:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE ocr_status = 'pending'"
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    # ── MinerU OCR ──

    def _ocr_via_mineru(self, old_toc: list[dict]) -> list[dict]:
        """用 MinerU 解析全文 Markdown，重建 TOC + 分节存入 SQLite。"""
        from exam.mineru import MinerUClient

        client = MinerUClient(self.mineru_token)
        print("[MinerU] 提交 PDF 解析（vlm 模型，异步轮询）...")
        full_md = client.parse_pdf(self.pdf_path)
        print(f"[MinerU] 解析完成，Markdown 共 {len(full_md)} 字")

        # 解析 Markdown 标题 → 重建 TOC
        toc, section_texts = self._parse_markdown_to_sections(full_md)

        # 写入 SQLite
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM sections")  # 清空旧 bookmark 数据
        conn.execute("""CREATE TABLE IF NOT EXISTS sections (
            id TEXT PRIMARY KEY, chapter TEXT DEFAULT '',
            title TEXT DEFAULT '', page_start INTEGER DEFAULT 0,
            page_end INTEGER DEFAULT 0, text TEXT DEFAULT '',
            ocr_status TEXT DEFAULT 'pending'
        )""")
        for sec_id, (chapter, title, text) in section_texts.items():
            conn.execute(
                "INSERT INTO sections (id, chapter, title, text, ocr_status) "
                "VALUES (?,?,?,?,'done')",
                (sec_id, chapter, title, text)
            )
        # 删除空文本的父级标题（正文在子节里）
        deleted = conn.execute("DELETE FROM sections WHERE text = ''").rowcount

        # 建 FTS5 全文索引
        conn.execute("DROP TABLE IF EXISTS sections_fts")
        conn.execute("CREATE VIRTUAL TABLE sections_fts USING fts5(id, text)")
        conn.execute("INSERT INTO sections_fts SELECT id, text FROM sections WHERE text != ''")
        conn.commit()
        conn.close()

        if deleted:
            for ch in toc:
                ch["sections"] = [
                    s for s in ch["sections"]
                    if s["id"] in section_texts and section_texts[s["id"]][2].strip()
                ]
            toc = [ch for ch in toc if ch["sections"]]

        print(f"[MinerU] 已写入 {len(section_texts)} 节（{deleted} 个空标题已清理，FTS5 索引已建）")
        return toc

    def _parse_markdown_to_sections(self, md_text: str) -> tuple[list, dict]:
        """从 MinerU Markdown 中解析章节结构。"""
        lines = md_text.split("\n")
        toc = []
        sections = {}
        current_chapter = None
        current_section = None
        current_text = []
        ch_idx = 0
        sec_idx = 0

        for line in lines:
            # 检测章标题（# 或 ## 开头 + "第X章"）
            ch_match = re.match(r"^(#{1,3})\s*(第[一二三四五六七八九十\d]+章)\s*(.*)", line)
            sec_match = re.match(r"^(#{2,4})\s*(\d+\.\d+(?:\.\d+)?)\s*(.*)", line)

            if ch_match:
                # 保存上一节
                if current_section:
                    sections[current_section["id"]] = (
                        current_chapter["chapter"] if current_chapter else "",
                        current_section["title"],
                        "\n".join(current_text).strip()
                    )
                    current_text = []

                ch_title = f"{ch_match.group(2)} {ch_match.group(3)}".strip()
                current_chapter = {"chapter": ch_title, "sections": []}
                toc.append(current_chapter)
                current_section = None
                continue

            if sec_match and current_chapter:
                # 保存上一节
                if current_section:
                    sections[current_section["id"]] = (
                        current_chapter["chapter"],
                        current_section["title"],
                        "\n".join(current_text).strip()
                    )
                    current_text = []

                sec_id = sec_match.group(2)
                sec_title = f"{sec_id} {sec_match.group(3)}".strip()
                current_section = {"id": sec_id, "title": sec_title}
                current_chapter["sections"].append(current_section)
                continue

            # 普通文本
            if current_section:
                current_text.append(line)
            elif current_chapter and not line.startswith("#"):
                # 章开头无节的正文（前言/概述等）
                if not current_section:
                    ch_idx += 1
                    sec_id = f"{current_chapter['chapter'][:10]}_intro"
                    current_section = {"id": sec_id, "title": "概述"}
                    current_chapter["sections"].append(current_section)
                current_text.append(line)

        # 保存最后一节
        if current_section:
            sections[current_section["id"]] = (
                current_chapter["chapter"] if current_chapter else "",
                current_section["title"],
                "\n".join(current_text).strip()
            )

        # 如果没有解析到结构，整篇当一个节
        if not toc:
            toc = [{"chapter": "正文", "sections": [{"id": "_full", "title": "全文"}]}]
            sections["_full"] = ("正文", "全文", md_text)

        return toc, sections
