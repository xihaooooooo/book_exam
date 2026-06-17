"""主编排器：初始化配置、创建图、运行传播"""

import json
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

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

    def propagate(self, db_path: str = None, toc: list[dict] = None,
                  focus: str = "", target_count: int = 0, allowed_types: str = "",
                  analysis_report_path: str = ""):
        """运行完整流程。"""
        init_sections(db_path=db_path)

        # 加载分析报告
        analysis_report = None
        if analysis_report_path and os.path.exists(analysis_report_path):
            with open(analysis_report_path, "r", encoding="utf-8") as f:
                analysis_report = json.load(f)
            total_q = analysis_report.get("aggregated", {}).get("total_questions", 0)
            print(f"已加载往年试卷分析: {analysis_report_path}")
            print(f"  往年总题数: {total_q}")

        graph_setup = GraphSetup(config=self.config)
        workflow = graph_setup.setup_graph()
        graph = workflow.compile()

        initial_state = self._create_initial_state(toc, focus, target_count, allowed_types, analysis_report)

        print(f"ExamGraph 开始运行, 共 {sum(len(ch.get('sections', [])) for ch in (toc or []))} 节")
        if focus:
            print(f"  考试重点: {focus}")
        if analysis_report:
            print(f"  出题策略: 基于往年试卷分析")
        print("请耐心等待所有题目并发生成...")

        final_state = graph.invoke(initial_state, {"recursion_limit": 500})

        all_questions = final_state.get("all_questions", [])

        saved_files = self._save_results(final_state)
        print(f"生成完成！共 {len(all_questions)} 道题")
        for f in saved_files:
            print(f"  -> {f}")

        return final_state, all_questions

    def _create_initial_state(self, toc: list[dict], focus: str = "",
                               target_count: int = 0, allowed_types: str = "",
                               analysis_report: dict = None) -> dict:
        """创建初始 state"""
        return {
            "pdf_path": "",
            "toc": toc or [],
            "exam_plan": None,
            "focus": focus,
            "target_count": target_count,
            "allowed_types": allowed_types,
            "analysis_report": analysis_report,
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

        all_questions = state.get("all_questions", [])

        if all_questions:
            # Markdown 试卷（由终审排版师生成）
            final_exam = state.get("final_exam", "")
            if final_exam:
                md_path = os.path.join(results_dir, f"exam_{timestamp}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(final_exam)
                saved.append(md_path)

            # JSON 数据
            json_path = os.path.join(results_dir, f"questions_{timestamp}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(all_questions, f, ensure_ascii=False, indent=2)
            saved.append(json_path)

        return saved

