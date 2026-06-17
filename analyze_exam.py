"""往年试卷分析 CLI

用法：
    python analyze_exam.py --file "试卷.docx"                      # 单文件
    python analyze_exam.py --dir ./papers/                        # 整个目录
    python analyze_exam.py --dir ./papers/ --output ./analysis/   # 指定输出
"""

import argparse
import os
import sys

# Windows 终端中文编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from exam.parsers import parse_docx, parse_docx_dir
from exam.analyzers import analyze_exam, generate_report
from exam.config import DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="往年试卷分析器")
    parser.add_argument("--file", default=None, help="单个 DOCX 试卷路径")
    parser.add_argument("--dir", default=None, help="试卷目录（批量分析）")
    parser.add_argument("--output", default="./analysis", help="输出目录（默认 ./analysis）")
    args = parser.parse_args()

    if not args.file and not args.dir:
        print("错误：请指定 --file 或 --dir")
        sys.exit(1)

    config = DEFAULT_CONFIG.copy()

    # ── 解析 ──
    if args.file:
        if not os.path.exists(args.file):
            print(f"错误：文件不存在 {args.file}")
            sys.exit(1)
        print(f"解析试卷: {args.file}")
        parsed = [parse_docx(args.file)]
    else:
        if not os.path.isdir(args.dir):
            print(f"错误：目录不存在 {args.dir}")
            sys.exit(1)
        print(f"解析目录: {args.dir}")
        parsed = parse_docx_dir(args.dir)
        if not parsed:
            print("未找到 DOCX 文件")
            sys.exit(1)
        print(f"  找到 {len(parsed)} 份试卷")

    # ── LLM 分析 ──
    print(f"\n开始 LLM 分析（共 {len(parsed)} 份试卷）...")
    analyzed = []
    for i, exam in enumerate(parsed):
        sec_count = len(exam.get("sections", []))
        print(f"\n[{i+1}/{len(parsed)}] {exam['title']} ({sec_count} 个分组)")
        result = analyze_exam(exam, config)
        analyzed.append(result)
        total_q = len(result.get("questions", []))
        print(f"  → 共 {total_q} 道题")

    # ── 报告 ──
    print(f"\n生成报告...")
    json_path = generate_report(analyzed, args.output)
    print(f"\n报告已保存到 {args.output}/")
    for f in sorted(os.listdir(args.output)):
        print(f"  {f}")


if __name__ == "__main__":
    main()
