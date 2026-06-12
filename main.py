"""Book-to-Exam Demo 入口

用法：
    python main.py

LangSmith 调试：设置以下环境变量或在 .env 中配置
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=lsv2_pt_xxx
    LANGCHAIN_PROJECT=book-to-exam
"""

import os
import sys

# Windows 终端中文编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from exam.mock_book import TOC, SECTIONS
from exam.graph.exam_graph import ExamGraph
from exam.config import DEFAULT_CONFIG


def main():
    print("Book-to-Exam Demo")
    print("=" * 60)

    config = DEFAULT_CONFIG.copy()

    # 从 mock 数据中提取第一个 task 的章节信息作为 current_task
    # 实际使用时，主编会从 toc 中生成任务清单
    # Demo 模式：主编根据 TOC 生成 ExamPlan，然后取第一个 task 跑流水线

    # 为 demo 准备第一个样例任务
    toc = TOC

    # 创建图
    exam = ExamGraph(config=config, debug=True)

    # 运行（toc 给主编，SECTIONS 给工具）
    final_state, questions = exam.propagate(
        mock_sections=SECTIONS,
        toc=toc,
    )

    # 输出结果
    print("\n" + "=" * 60)
    print(f"生成完成！共 {len(questions)} 道题")
    print("=" * 60)

    for i, q in enumerate(questions, 1):
        print(f"\n--- 题{i} ({q.get('question_type', '')}, {q.get('difficulty', '')}) ---")
        print(f"题干: {q.get('stem', '')}")
        if q.get("options"):
            for opt in q["options"]:
                print(f"  {opt}")
        print(f"答案: {q.get('correct_answer', '')}")
        if q.get("explanation"):
            print(f"解析: {q.get('explanation', '')}")


if __name__ == "__main__":
    main()
