"""Book-to-Exam Demo 入口

用法：
    python parse.py book.pdf --mineru-token TOKEN   # 第一步：解析 PDF → SQLite
    python generate.py                              # 第二步：从 SQLite 出题
"""

if __name__ == "__main__":
    print("Book-to-Exam 自动出题系统")
    print()
    print("用法：")
    print("  python parse.py book.pdf --mineru-token TOKEN   解析 PDF 教材 → SQLite")
    print("  python generate.py                              从 SQLite 生成试卷")
    print()
    print("示例：")
    print("  python parse.py mybook.pdf --mineru-token eyJ...")
    print("  python generate.py")
