"""PDF 解析：提取目录 + 正文，写入 SQLite。

用法：
    python parse.py book.pdf                        # 解析并存入默认库
    python parse.py book.pdf --mineru-token TOKEN   # 扫描版 PDF 用 MinerU OCR
    python parse.py book.pdf --db cache/my.db       # 指定库路径
    python parse.py book.pdf --force                 # 强制重新解析
"""

import argparse
import os
import sqlite3
import sys

from exam.pdf_parser import PdfParser
from exam.config import DEFAULT_CONFIG
from exam.book_registry import build_book_paths, register_book, sanitize_book_id


MINERU_TOKEN_ENV_NAMES = (
    "MINERU_API_TOKEN",
    "MINERU_TOKEN",
    "BOOKTOEXAM_MINERU_TOKEN",
    "MINERU_API_KEY",
)


def _load_dotenv_value(key: str) -> str:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    return ""


def _resolve_mineru_token(explicit_token: str | None) -> str:
    if explicit_token:
        return explicit_token.strip()
    for name in MINERU_TOKEN_ENV_NAMES:
        value = os.environ.get(name) or _load_dotenv_value(name)
        if value:
            return value.strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="PDF 教材解析器")
    parser.add_argument("pdf", help="PDF 教材路径")
    parser.add_argument("--db", default=None,
                        help="SQLite 数据库路径（默认 cache/sections.db）")
    parser.add_argument("--book-id", default="",
                        help="注册为多教材书籍 ID（默认仍写入 cache/sections.db）")
    parser.add_argument("--book-title", default="",
                        help="多教材显示名称（配合 --book-id 使用）")
    parser.add_argument("--set-default", action="store_true",
                        help="将本次解析的书籍设为默认教材")
    parser.add_argument("--mineru-token", default=None,
                        help="MinerU API Token（默认也会读取环境变量或 .env）")
    parser.add_argument("--no-ocr", action="store_true",
                        help="关闭默认 OCR，改用 PDF 书签/文本解析")
    parser.add_argument("--force", action="store_true",
                        help="强制重新解析，忽略已有数据")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"错误：找不到文件 {args.pdf}")
        sys.exit(1)

    book_id = sanitize_book_id(args.book_id) if args.book_id else ""
    book_paths = build_book_paths(book_id) if book_id else {}
    db_path = args.db or book_paths.get("sections_db") or DEFAULT_CONFIG.get("db_path", "cache/sections.db")
    db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
    images_dir = book_paths.get("images_dir") or os.path.join(db_dir, "images")
    manifest_path = book_paths.get("media_manifest") or os.path.join(db_dir, "media_manifest.json")

    def register_current_book():
        if not book_id:
            return
        title = args.book_title or os.path.splitext(os.path.basename(args.pdf))[0] or book_id
        book = register_book(
            book_id=book_id,
            title=title,
            pdf_path=os.path.abspath(args.pdf),
            sections_db=os.path.abspath(db_path),
            attempts_db=book_paths.get("attempts_db", ""),
            output_dir=book_paths.get("output_dir", ""),
            analysis_dir=book_paths.get("analysis_dir", ""),
            set_default=args.set_default,
        )
        print(f"  已注册教材：{book['title']} ({book['book_id']})")

    # 检查是否已解析过
    if not args.force and os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        done = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE ocr_status = 'done'"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        conn.close()
        if total > 0 and done == total:
            print(f"该 PDF 已解析完成（{done} 节），跳过。")
            print(f"如需重新解析，加 --force")
            register_current_book()
            return

    force_ocr = not args.no_ocr
    mineru_token = _resolve_mineru_token(args.mineru_token)
    if force_ocr and not mineru_token:
        print("错误：默认启用 OCR，但未找到 MinerU Token。")
        print("请设置 MINERU_API_TOKEN / MINERU_TOKEN / BOOKTOEXAM_MINERU_TOKEN，或使用 --mineru-token。")
        print("如需临时关闭 OCR，可加 --no-ocr。")
        sys.exit(1)

    pdf_parser = PdfParser(
        args.pdf,
        db_path=db_path,
        mineru_token=mineru_token,
        force_ocr=force_ocr,
        assets_dir=images_dir,
        manifest_path=manifest_path,
    )
    toc = pdf_parser.parse()
    pdf_parser.close()

    # 打印摘要
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE ocr_status='done'"
    ).fetchone()[0]
    pending = total - done
    conn.close()

    print(f"\n解析完成：")
    print(f"  章：{len(toc)} 章")
    print(f"  节：{total} 节")
    print(f"  已完成：{done} 节")
    if pending:
        print(f"  待 OCR：{pending} 节")
    print(f"  数据库：{os.path.abspath(db_path)}")
    print(f"  媒体清单：{os.path.abspath(manifest_path)}")

    register_current_book()


if __name__ == "__main__":
    main()
