"""DOCX 试卷解析器：提取段落 → 按 Heading 分组"""

import os
from docx import Document


def parse_docx(filepath: str) -> dict:
    """解析单个 DOCX 试卷文件。

    返回:
        {"title": "试卷标题", "filename": "xxx.docx",
         "sections": [{"title": "填空题（5道*10分）", "texts": ["题文1", "题文2"]}]}
    """
    doc = Document(filepath)
    paragraphs = doc.paragraphs

    title = ""
    sections = []
    current_section = None

    for p in paragraphs:
        text = p.text.strip()
        if not text:
            continue

        style_name = p.style.name if p.style else ""

        if "Heading 1" in style_name:
            title = text
        elif "Heading 2" in style_name:
            if current_section and current_section["texts"]:
                sections.append(current_section)
            current_section = {"title": text, "texts": []}
        elif "Heading" in style_name:
            if current_section is not None:
                current_section["texts"].append(f"[{style_name}] {text}")
        elif current_section is not None:
            current_section["texts"].append(text)

    if current_section and current_section["texts"]:
        sections.append(current_section)

    return {
        "title": title or os.path.splitext(os.path.basename(filepath))[0],
        "filename": os.path.basename(filepath),
        "sections": sections,
    }


def parse_docx_dir(dirpath: str) -> list[dict]:
    """遍历目录，解析所有 DOCX 文件。

    返回: [{"title": ..., "filename": ..., "sections": [...]}, ...]
    """
    exams = []
    for fname in sorted(os.listdir(dirpath)):
        if fname.endswith(".docx") and not fname.startswith("~$"):
            filepath = os.path.join(dirpath, fname)
            exams.append(parse_docx(filepath))
    return exams
