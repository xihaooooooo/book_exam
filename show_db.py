"""快速查看 SQLite 数据，UTF-8 输出"""
import sqlite3, sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "cache/sections.db"
conn = sqlite3.connect(db_path)

total = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
done = conn.execute(
    "SELECT COUNT(*) FROM sections WHERE ocr_status='done'").fetchone()[0]

print(f"总: {total} 节 | 有正文: {done} | 待OCR: {total - done}\n")

# 章列表
print("=" * 70)
for row in conn.execute(
    "SELECT chapter, COUNT(*) as cnt FROM sections "
    "GROUP BY chapter ORDER BY MIN(page_start)"
):
    print(f"  {row[0]:20s}  {row[1]} 节")

# 每章抽 2 条
print("\n" + "=" * 70)
print(f"{'id':8s} {'章':20s} {'页':>5s} {'字数':>6s} {'状态'}")
print("-" * 70)
for row in conn.execute("""
    SELECT id, chapter, page_start, length(text), ocr_status
    FROM sections ORDER BY page_start LIMIT 60
"""):
    text_len = row[3] or 0
    print(f"{row[0]:8s} {row[1]:20s} {row[2]:>5d} {text_len:>6d} {row[4]}")

conn.close()
