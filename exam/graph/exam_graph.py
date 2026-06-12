"""主编排器：初始化配置、创建图、运行传播"""

import json
import os
import sys
import logging
from datetime import datetime

# Windows 终端中文编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger(__name__)

from exam.config import DEFAULT_CONFIG
from exam.agents.utils.agent_states import AgentState
from exam.agents.utils.agent_utils import init_sections

from .setup import GraphSetup


class ExamGraph:
    """主编排器，对应 TradingAgents 的 TradingAgentsGraph"""

    def __init__(self, config: dict = None, debug: bool = True):
        self.config = config or DEFAULT_CONFIG
        self.debug = debug

        os.makedirs(self.config.get("results_dir", "./output"), exist_ok=True)
        os.makedirs(self.config.get("data_cache_dir", "./cache"), exist_ok=True)

    def propagate(self, mock_sections: dict = None, toc: list[dict] = None):
        """运行完整流程。
        Args:
            mock_sections: mock 章节文本（demo 用）
            toc: 目录结构
        Returns:
            (final_state, all_questions)
        """
        init_sections(mock_sections)

        graph_setup = GraphSetup(config=self.config)
        workflow = graph_setup.setup_graph()
        graph = workflow.compile()

        # 创建初始状态
        initial_state = self._create_initial_state(toc)

        task_count = sum(len(t.get("tasks", [])) for t in [{}] if t)  # placeholder
        print(f"ExamGraph 开始运行, 共 {sum(len(ch.get('sections', [])) for ch in (toc or []))} 节")
        print("请耐心等待所有题目并发生成...")

        final_state = graph.invoke(initial_state, {"recursion_limit": 500})

        all_questions = final_state.get("all_questions", [])

        # 保存结果
        saved_files = self._save_results(final_state)
        print(f"生成完成！共 {len(all_questions)} 道题")
        for f in saved_files:
            print(f"  -> {f}")

        return final_state, all_questions

    def _create_initial_state(self, toc: list[dict]) -> dict:
        """创建初始 state"""
        return {
            "pdf_path": "",
            "toc": toc or [],
            "exam_plan": None,
            "current_task": None,
            "knowledge_point": "",
            "generated_question": None,
            "all_questions": [],
            "final_exam": "",
            "messages": [],
        }

    def _save_results(self, state: dict) -> list[str]:
        """保存结果到文件，返回保存的文件路径列表"""
        results_dir = self.config.get("results_dir", "./output")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = []

        exam_plan = state.get("exam_plan", {})
        all_questions = state.get("all_questions", [])

        if all_questions:
            # Markdown 试卷
            md_path = os.path.join(results_dir, f"exam_{timestamp}.md")
            self._export_markdown(all_questions, exam_plan, md_path)
            saved.append(md_path)

            # JSON 数据
            json_path = os.path.join(results_dir, f"questions_{timestamp}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(all_questions, f, ensure_ascii=False, indent=2)
            saved.append(json_path)

        return saved

    def _export_markdown(self, questions: list, exam_plan: dict, path: str):
        """导出为 Markdown 试卷"""
        lines = ["# 试卷\n"]

        # 按题型分组
        choices = [q for q in questions if q.get("question_type") == "choice"]
        fill_blanks = [q for q in questions if q.get("question_type") == "fill_blank"]
        short_answers = [q for q in questions if q.get("question_type") == "short_answer"]

        num = 1

        if choices:
            lines.append("## 一、选择题\n")
            for q in choices:
                lines.append(f"**{num}.** ({q.get('difficulty', '')}) {q.get('stem', '')}")
                for opt in q.get("options", []):
                    lines.append(f"  {opt}")
                lines.append("")
                num += 1

        if fill_blanks:
            lines.append("## 二、填空题\n")
            for q in fill_blanks:
                lines.append(f"**{num}.** ({q.get('difficulty', '')}) {q.get('stem', '')}")
                lines.append("")
                num += 1

        if short_answers:
            lines.append("## 三、简答题\n")
            for q in short_answers:
                lines.append(f"**{num}.** ({q.get('difficulty', '')}) {q.get('stem', '')}")
                lines.append("")
                num += 1

        # 答案页
        lines.append("\n---\n")
        lines.append("# 答案\n")

        num = 1
        for q in questions:
            lines.append(f"**{num}.** {q.get('correct_answer', '')}")
            if q.get("explanation"):
                lines.append(f"  > {q.get('explanation', '')}")
            lines.append("")
            num += 1

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
