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


def main():
    parser = argparse.ArgumentParser(description="PDF 教材解析器")
    parser.add_argument("pdf", help="PDF 教材路径")
    parser.add_argument("--db", default=None,
                        help="SQLite 数据库路径（默认 cache/sections.db）")
    parser.add_argument("--mineru-token", default=None,
                        help="MinerU API Token（扫描版 PDF 自动 OCR）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新解析，忽略已有数据")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"错误：找不到文件 {args.pdf}")
        sys.exit(1)

    db_path = args.db or DEFAULT_CONFIG.get("db_path", "cache/sections.db")

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
            return

    pdf_parser = PdfParser(args.pdf, db_path=db_path, mineru_token=args.mineru_token)
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


if __name__ == "__main__":
    main()
