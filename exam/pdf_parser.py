"""PDF 解析器：提取目录结构，写入 SQLite。扫描版 PDF 可用 MinerU OCR。"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime


class PdfParser:
    """解析 PDF 教材，TOC 树 + 正文写入 SQLite sections 表。"""

    def __init__(
        self,
        pdf_path: str,
        db_path: str,
        mineru_token: str = None,
        force_ocr: bool = False,
        assets_dir: str = None,
        manifest_path: str = None,
        extract_images: bool = True,
    ):
        self.pdf_path = pdf_path
        self.db_path = db_path
        self.mineru_token = mineru_token
        self.force_ocr = force_ocr
        db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
        self.assets_dir = assets_dir or os.path.join(db_dir, "images")
        self.manifest_path = manifest_path or os.path.join(db_dir, "media_manifest.json")
        self.extract_images = extract_images
        self._doc = None

    @property
    def doc(self):
        if self._doc is None:
            import fitz
            self._doc = fitz.open(self.pdf_path)
        return self._doc

    def parse(self) -> list[dict]:
        """解析 PDF，写入 SQLite，返回 TOC 列表。"""
        if self.force_ocr:
            if not self.mineru_token:
                raise RuntimeError("已启用 OCR 解析，但缺少 MinerU Token")
            print("[PDF] 已启用强制 OCR，跳过内置书签/文本目录解析")
            toc = self._ocr_via_mineru([])
            print(f"[PDF] 解析完成：{len(toc)} 章，{sum(len(ch['sections']) for ch in toc)} 节")
            return toc

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
        assets = self._extract_image_assets()
        self._append_image_placeholders(conn, assets)
        self._write_media_manifest(assets)

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
        conn.execute("DROP TABLE IF EXISTS sections")
        conn.execute("""CREATE TABLE sections (
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
        assets = self._extract_image_assets()
        self._enrich_image_assets_from_pages(assets)
        self._append_ocr_image_placeholders(conn, assets)
        self._write_media_manifest(assets)

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

    # ── 图片资产 ──

    def _extract_image_assets(self) -> list[dict]:
        """Extract embedded PDF images to the book image directory."""
        if not self.extract_images:
            return []

        os.makedirs(self.assets_dir, exist_ok=True)
        manifest_dir = os.path.dirname(os.path.abspath(self.manifest_path)) or "."
        items: list[dict] = []
        hash_to_src: dict[str, str] = {}

        for page_idx in range(self.doc.page_count):
            page = self.doc[page_idx]
            try:
                page_images = page.get_images(full=True)
            except Exception:
                continue
            for img in page_images:
                xref = img[0]
                try:
                    extracted = self.doc.extract_image(xref)
                except Exception:
                    continue

                image_bytes = extracted.get("image") or b""
                if not image_bytes:
                    continue

                sha256 = hashlib.sha256(image_bytes).hexdigest()
                ext = (extracted.get("ext") or "bin").lower()
                if ext == "jpeg":
                    ext = "jpg"
                src = hash_to_src.get(sha256)
                if not src:
                    filename = self._unique_asset_filename(sha256, ext)
                    image_path = os.path.join(self.assets_dir, filename)
                    if not os.path.exists(image_path):
                        with open(image_path, "wb") as fh:
                            fh.write(image_bytes)
                    src = os.path.relpath(image_path, manifest_dir).replace("\\", "/")
                    hash_to_src[sha256] = src

                try:
                    rects = page.get_image_rects(xref) or [None]
                except Exception:
                    rects = [None]
                for rect in rects:
                    item = {
                        "id": f"fig{len(items) + 1}",
                        "type": "image",
                        "src": src,
                        "sha256": sha256,
                        "page": page_idx + 1,
                        "bbox": self._rect_to_list(rect),
                        "width": int(extracted.get("width") or img[2] or 0),
                        "height": int(extracted.get("height") or img[3] or 0),
                        "description": "",
                    }
                    items.append(item)

        return items

    def _unique_asset_filename(self, sha256: str, ext: str) -> str:
        prefix_len = 8
        while True:
            filename = f"{sha256[:prefix_len]}.{ext}"
            path = os.path.join(self.assets_dir, filename)
            if not os.path.exists(path):
                return filename
            with open(path, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() == sha256:
                    return filename
            prefix_len += 2

    def _rect_to_list(self, rect) -> list[float]:
        if rect is None:
            return []
        return [round(float(rect.x0), 2), round(float(rect.y0), 2),
                round(float(rect.x1), 2), round(float(rect.y1), 2)]

    def _append_image_placeholders(self, conn: sqlite3.Connection, assets: list[dict]) -> None:
        """Append image markers to sections when page ranges are available."""
        if not assets:
            return

        rows = conn.execute(
            "SELECT id, chapter, title, page_start, page_end, text FROM sections ORDER BY page_start, id"
        ).fetchall()
        row_by_id = {row[0]: row for row in rows}
        by_section: dict[str, list[dict]] = {}
        for item in assets:
            section_id = self._section_for_page(rows, int(item.get("page") or 0))
            if not section_id:
                continue
            item["section_id"] = section_id
            section_row = row_by_id.get(section_id)
            if section_row:
                self._enrich_image_asset_from_section(item, section_row)
            by_section.setdefault(section_id, []).append(item)

        for section_id, items in by_section.items():
            placeholders = []
            for item in items:
                placeholders.append(self._image_placeholder_text(item))
            addition = "\n\n" + "\n\n".join(placeholders)
            conn.execute(
                "UPDATE sections SET text = COALESCE(text, '') || ? WHERE id = ?",
                (addition, section_id),
            )

    def _append_ocr_image_placeholders(self, conn: sqlite3.Connection, assets: list[dict]) -> None:
        """Append image markers for OCR sections that do not have page ranges."""
        if not assets:
            return

        rows = conn.execute(
            "SELECT id, chapter, title, page_start, page_end, text FROM sections ORDER BY id"
        ).fetchall()
        if not rows:
            return

        assigned_ids: set[str] = set()
        asset_index = 0
        for row in rows:
            text = str(row[5] or "")
            marker_count = len(re.findall(r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*>", text, re.I))
            for _ in range(marker_count):
                while asset_index < len(assets) and assets[asset_index].get("id") in assigned_ids:
                    asset_index += 1
                if asset_index >= len(assets):
                    break
                self._assign_image_asset_to_section(conn, assets[asset_index], row)
                assigned_ids.add(str(assets[asset_index].get("id") or ""))
                asset_index += 1

        for item in assets:
            media_id = str(item.get("id") or "")
            if media_id in assigned_ids:
                continue
            section_row = self._best_section_for_image_context(rows, item)
            if section_row:
                self._assign_image_asset_to_section(conn, item, section_row)
                assigned_ids.add(media_id)

    def _assign_image_asset_to_section(
        self,
        conn: sqlite3.Connection,
        item: dict,
        section_row: tuple,
    ) -> None:
        item["section_id"] = section_row[0]
        self._enrich_image_asset_from_section(item, section_row)
        self._replace_or_append_image_placeholder(conn, item)

    def _best_section_for_image_context(self, rows: list[tuple], item: dict) -> tuple | None:
        context = str(item.get("context") or "")
        if not context:
            page_text = self._safe_page_text(int(item.get("page") or 0))
            context = self._select_image_context("", page_text, "")
        terms = self._extract_context_terms(context)
        if not terms:
            return None

        best_row = None
        best_score = 0
        for row in rows:
            chapter = str(row[1] or "")
            title = str(row[2] or "")
            text = self._remove_existing_image_markers(str(row[5] or ""))
            heading = f"{chapter} {title}"
            score = 0
            for term in terms[:8]:
                if term in heading:
                    score += 4
                if term in text:
                    score += 1
            if score > best_score:
                best_row = row
                best_score = score

        return best_row if best_score >= 2 else None

    def _section_for_page(self, rows: list[tuple], page: int) -> str:
        for row in rows:
            section_id, page_start, page_end = row[0], row[3], row[4]
            if not page_start or not page_end:
                continue
            if int(page_start) <= page <= int(page_end):
                return section_id
        return ""

    def _enrich_image_assets_from_pages(self, assets: list[dict]) -> None:
        """Fill conservative image descriptions when section page ranges are unavailable."""
        for item in assets:
            if item.get("description"):
                continue
            page_text = self._safe_page_text(int(item.get("page") or 0))
            item["description"] = self._build_image_description(
                item=item,
                chapter="",
                title="",
                section_text="",
                page_text=page_text,
            )
            item["description_source"] = "page_context"
            item["context"] = self._select_image_context("", page_text, "")

    def _enrich_image_asset_from_section(self, item: dict, section_row: tuple) -> None:
        section_id, chapter, title, _page_start, _page_end, section_text = section_row
        page_text = self._safe_page_text(int(item.get("page") or 0))
        description = self._build_image_description(
            item=item,
            chapter=str(chapter or ""),
            title=str(title or section_id or ""),
            section_text=str(section_text or ""),
            page_text=page_text,
        )
        item["description"] = description
        item["description_source"] = "section_context"
        item["context"] = self._select_image_context(str(section_text or ""), page_text, str(title or ""))
        item["section_title"] = str(title or "")
        item["section_chapter"] = str(chapter or "")

    def _build_image_description(
        self,
        *,
        item: dict,
        chapter: str,
        title: str,
        section_text: str,
        page_text: str,
    ) -> str:
        context = self._select_image_context(section_text, page_text, title)
        terms = self._extract_context_terms(" ".join([chapter, title, context]))
        role = self._infer_image_role(" ".join([title, context]), item)

        location = f"教材第 {item.get('page') or '?'} 页"
        section_name = " / ".join(part for part in (chapter, title) if part)
        if section_name:
            location += f"，位于“{section_name}”"

        parts = [f"{location}中的{role}。"]
        if terms:
            parts.append("相关上下文包括：" + "、".join(terms[:6]) + "。")
        if context:
            parts.append("相邻文本摘要：" + self._short_text(context, 120) + "。")
        else:
            parts.append("未提取到足够相邻文本，出题时应谨慎使用该图。")
        return self._short_text("".join(parts), 260)

    def _fallback_image_description(self, item: dict) -> str:
        return f"教材第 {item.get('page') or '?'} 页中的教材插图，用于辅助理解所在章节内容。"

    def _safe_page_text(self, page: int) -> str:
        if page <= 0:
            return ""
        try:
            return self.doc[page - 1].get_text("text").strip()
        except Exception:
            return ""

    def _select_image_context(self, section_text: str, page_text: str, title: str) -> str:
        text = "\n".join(part for part in (page_text, section_text) if part).strip()
        text = self._remove_existing_image_markers(text)
        if not text:
            return self._short_text(title, 120)

        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        cue_words = (
            "图", "表", "示意", "结构", "流程", "状态", "转换", "关系",
            "步骤", "过程", "组成", "如图", "所示", "如下", "任务",
            "队列", "链表", "树", "指针", "中断", "信号量", "内存",
        )
        scored: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if len(line) > 180:
                line = self._short_text(line, 180)
            score = sum(2 for word in cue_words if word in line)
            if title and any(part and part in line for part in re.split(r"[\s　]+", title)):
                score += 1
            if score:
                scored.append((score * 1000 - idx, line))
        if scored:
            scored.sort(reverse=True)
            return self._short_text(" ".join(line for _score, line in scored[:3]), 260)
        return self._short_text(" ".join(lines[:3]), 260)

    def _remove_existing_image_markers(self, text: str) -> str:
        text = re.sub(r"\[插图:[\s\S]*?(?=\n\s*\[插图:|\Z)", " ", text or "")
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"<img\b[^>]*>", " ", text, flags=re.I)
        return text.strip()

    def _extract_context_terms(self, text: str) -> list[str]:
        known_terms = [
            "任务", "任务状态", "状态转换", "调度", "优先级", "就绪表", "位图",
            "任务控制块", "TCB", "事件控制块", "ECB", "信号量", "消息邮箱",
            "消息队列", "中断", "临界段", "内存分区", "内存控制块", "指针",
            "链表", "队列", "堆栈", "流程", "结构", "状态", "关系",
            "OSSemPend", "OSSemPost", "OSTaskCreate", "OSTimeDly", "OSIntEnter", "OSIntExit",
        ]
        terms: list[str] = []
        for term in known_terms:
            if term in text and term not in terms:
                terms.append(term)
        for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,8}", text or ""):
            term = match.group(0).strip()
            if term in terms or self._is_stop_term(term):
                continue
            terms.append(term)
            if len(terms) >= 8:
                break
        return terms

    def _is_stop_term(self, term: str) -> bool:
        return term in {
            "教材", "图片", "插图", "章节", "第章", "本节", "可以", "进行",
            "一个", "如果", "其中", "使用", "说明", "如下", "所示", "时候",
        }

    def _infer_image_role(self, context: str, item: dict) -> str:
        if any(word in context for word in ("状态", "转换", "迁移")):
            return "状态转换图"
        if any(word in context for word in ("流程", "步骤", "过程", "调用", "执行")):
            return "流程或过程示意图"
        if any(word in context for word in ("结构", "组成", "控制块", "队列", "链表", "树", "表")):
            return "结构示意图"
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if width and height and width / max(height, 1) > 1.8:
            return "横向图表或流程示意图"
        return "教材插图"

    def _short_text(self, text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _image_placeholder_text(self, item: dict) -> str:
        description = item.get("description") or self._fallback_image_description(item)
        return (
            f"[插图: {item['id']}]\n"
            f"媒体描述：{description}\n"
            f"文件 {item['src']}，教材第 {item['page']} 页。"
        )

    def enrich_existing_media_descriptions(self, *, force: bool = False, update_sections: bool = True) -> int:
        """Backfill image descriptions in an existing media manifest and sections DB."""
        if not self.manifest_path or not os.path.exists(self.manifest_path):
            return 0

        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return 0

        conn = None
        rows: list[tuple] = []
        row_by_id: dict[str, tuple] = {}
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT id, chapter, title, page_start, page_end, text FROM sections ORDER BY page_start, id"
                ).fetchall()
                row_by_id = {row[0]: row for row in rows}
            except sqlite3.Error:
                rows = []
                row_by_id = {}

        changed_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            if not force and not self._description_needs_update(item.get("description", "")):
                continue

            page = int(item.get("page") or 0)
            section_id = item.get("section_id") or self._section_for_page(rows, page)
            section_row = row_by_id.get(section_id)
            if section_row:
                item["section_id"] = section_id
                self._enrich_image_asset_from_section(item, section_row)
            else:
                page_text = self._safe_page_text(page)
                item["description"] = self._build_image_description(
                    item=item,
                    chapter="",
                    title="",
                    section_text="",
                    page_text=page_text,
                )
                item["description_source"] = "page_context"
                item["context"] = self._select_image_context("", page_text, "")
            item["description_updated_at"] = datetime.now().isoformat(timespec="seconds")
            changed_items.append(item)

        if conn is not None:
            if update_sections:
                for item in changed_items:
                    self._replace_or_append_image_placeholder(conn, item)
                if changed_items:
                    self._rebuild_sections_fts(conn)
            conn.commit()
            conn.close()

        if changed_items:
            data["items"] = items
            data["generated_at"] = datetime.now().isoformat(timespec="seconds")
            data["description_backfill"] = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "count": len(changed_items),
                "source": "section_or_page_context",
            }
            with open(self.manifest_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        return len(changed_items)

    def _description_needs_update(self, description: str) -> bool:
        text = str(description or "").strip()
        if not text:
            return True
        return any(term in text for term in ("待生成", "尚未生成", "TODO", "占位", "未提取到足够相邻文本"))

    def _replace_or_append_image_placeholder(self, conn: sqlite3.Connection, item: dict) -> None:
        section_id = item.get("section_id")
        media_id = item.get("id")
        if not section_id or not media_id:
            return
        row = conn.execute("SELECT text FROM sections WHERE id = ?", (section_id,)).fetchone()
        if not row:
            return
        text = row[0] or ""
        block = self._image_placeholder_text(item)
        pattern = re.compile(rf"\[插图:\s*{re.escape(str(media_id))}\][\s\S]*?(?=\n\s*\[插图:|\Z)")
        if pattern.search(text):
            text = pattern.sub(block, text)
        else:
            text = text.rstrip() + "\n\n" + block
        conn.execute("UPDATE sections SET text = ? WHERE id = ?", (text, section_id))

    def _rebuild_sections_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS sections_fts")
        conn.execute("CREATE VIRTUAL TABLE sections_fts USING fts5(id, text)")
        conn.execute("INSERT INTO sections_fts SELECT id, text FROM sections WHERE text != ''")

    def _write_media_manifest(self, assets: list[dict]) -> None:
        if not self.manifest_path:
            return
        os.makedirs(os.path.dirname(self.manifest_path) or ".", exist_ok=True)
        data = {
            "version": 1,
            "pdf_path": os.path.abspath(self.pdf_path),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "items": assets,
        }
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
