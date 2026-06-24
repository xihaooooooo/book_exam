"""答题前端桥梁：提供题目 + 批量判题。

用法：
    python web/server.py
    python web/server.py --port 8080

题目来源：优先读 output/questions_*.json（generate.py 产物），没有则用内置 demo。
判题：POST /api/submit-exam → JudgeGraph 批量判定 → attempts.db。
"""

import argparse
import json
import os
import sys
import glob
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

# 确保项目根在 sys.path 中
_project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from exam.graph.judge_graph import JudgeGraph
from exam.graph.exam_graph import ExamGraph
from exam.student_profile.storage import init_attempts_db, init_error_labels_db, record_attempts_batch
from exam.student_profile.profile_engine import build_profile, compute_session_rewards, normalize_section_id
from exam.student_profile.recommendation import init_bandit_states, build_recommendation_plan
from exam.student_profile.schemas import ERROR_TYPE_LABELS
from exam.agents.utils.agent_utils import create_llm_client, build_toc_from_db
from exam.config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="[server] %(message)s")
logger = logging.getLogger(__name__)

PORT = 8765

# ── 模块级状态（启动时初始化）──
QUESTIONS = []
JUDGE_GRAPH = None
ATTEMPTS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "attempts.db")


# ── 题目加载 ──

def _load_latest_output():
    pattern = os.path.join(os.path.dirname(__file__), "..", "output", "questions_*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and len(data) > 0:
                logger.info("加载题目: %s (%d 题)", f, len(data))
                return data
        except Exception:
            continue
    return None


def _demo_questions():
    """内置 demo 题。"""
    return [
        {
            "id": "demo-0", "question_type": "choice", "difficulty": "easy",
            "source": "1.1", "topic": "操作系统定义",
            "stem": "以下哪项不是操作系统的核心功能？",
            "options": ["A. 进程管理", "B. 内存管理", "C. 编译程序", "D. 文件管理"],
            "correct_answer": "C",
            "explanation": "编译程序属于编程工具，不是操作系统内核的核心功能。",
        },
        {
            "id": "demo-1", "question_type": "choice", "difficulty": "medium",
            "source": "2.1", "topic": "进程状态转换",
            "stem": "当一个进程从运行态变为就绪态时，可能的原因是？",
            "options": ["A. 进程完成了I/O操作", "B. 时间片用完", "C. 进程请求I/O", "D. 进程被创建"],
            "correct_answer": "B",
            "explanation": "时间片用完后，进程从运行态回到就绪态等待下一次调度。",
        },
        {
            "id": "demo-2", "question_type": "choice", "difficulty": "hard",
            "source": "2.3", "topic": "任务调度",
            "stem": "在μC/OS-II中，以下哪个函数会引起任务调度？",
            "options": ["A. OSTimeDly()", "B. OSSemPend()", "C. OSFlagPend()", "D. 以上都可以"],
            "correct_answer": "D",
            "explanation": "这三个函数都可能使当前任务挂起，从而触发一次任务调度。",
        },
        {
            "id": "demo-3", "question_type": "choice", "difficulty": "medium",
            "source": "3.1", "topic": "临界区互斥",
            "stem": "下列关于临界区的描述，错误的是？",
            "options": [
                "A. 临界区是访问共享资源的代码段",
                "B. 多个进程可以同时进入同一个临界区",
                "C. 临界区需要互斥机制保护",
                "D. 关中断是实现临界区的一种方式",
            ],
            "correct_answer": "B",
            "explanation": "临界区必须互斥访问，同一时刻只允许一个进程进入。",
        },
        {
            "id": "demo-4", "question_type": "choice", "difficulty": "easy",
            "source": "4.1", "topic": "内存管理",
            "stem": "虚拟内存技术的主要目的是？",
            "options": [
                "A. 提高CPU速度", "B. 扩展可用的物理内存容量",
                "C. 使程序可以运行在比物理内存大的地址空间", "D. 减少缺页中断",
            ],
            "correct_answer": "C",
            "explanation": "虚拟内存让程序可以使用超过物理内存大小的地址空间。",
        },
        {
            "id": "demo-5", "question_type": "fill_blank", "difficulty": "easy",
            "source": "1.2", "topic": "操作系统特征",
            "stem": "操作系统最基本的特征包括并发、共享、虚拟和____。",
            "options": [],
            "correct_answer": "异步",
            "explanation": "操作系统的四大基本特征是并发、共享、虚拟、异步。",
        },
        {
            "id": "demo-6", "question_type": "short_answer", "difficulty": "medium",
            "source": "2.2", "topic": "进程同步",
            "stem": "请简述信号量机制的基本原理。",
            "options": [],
            "correct_answer": "信号量是一个整型变量，通过P操作（wait）和V操作（signal）实现进程同步。P操作检查信号量值，若大于0则减1继续执行，否则阻塞；V操作将信号量加1并唤醒一个等待进程。",
            "explanation": "信号量用于解决临界区互斥和进程同步问题。",
        },
    ]


def _list_analysis_reports() -> list[dict]:
    """列出 analysis/ 目录下所有可用的往年试卷分析报告。"""
    analysis_dir = os.path.join(os.path.dirname(__file__), "..", "analysis")
    if not os.path.isdir(analysis_dir):
        return []
    reports = []
    for f in sorted(os.listdir(analysis_dir)):
        if f.endswith(".json"):
            fpath = os.path.join(analysis_dir, f)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                exams = meta.get("exams", [])
                agg = meta.get("aggregated", {})
                reports.append({
                    "filename": f,
                    "path": os.path.abspath(fpath),
                    "exam_count": len(exams),
                    "total_questions": agg.get("total_questions", 0),
                })
            except Exception:
                reports.append({"filename": f, "path": os.path.abspath(fpath), "exam_count": 0, "total_questions": 0})
    return reports


def get_questions():
    loaded = _load_latest_output()
    if loaded:
        return loaded
    logger.info("未找到 output 产物，使用内置 demo 题")
    return _demo_questions()


# ── HTTP Handler ──

class QuizHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        web_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=web_dir, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/questions":
            self._serve_json(QUESTIONS)
            return
        if parsed.path == "/api/questions/demo":
            self._serve_json(_demo_questions())
            return
        if parsed.path == "/api/profile":
            self._handle_profile()
            return
        if parsed.path == "/api/analysis-reports":
            self._serve_json(_list_analysis_reports())
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/submit-exam":
            self._handle_submit_exam()
            return
        if parsed.path == "/api/generate":
            self._handle_generate()
            return
        if parsed.path == "/api/analyze-exam":
            self._handle_analyze_exam()
            return
        self.send_error(404)

    def _handle_submit_exam(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._serve_json({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
            return

        try:
            student_id = data.get("student_id", "")

            # ① 修复字段映射 + 下沉 student_id + 归一化章节编号
            for ans in data.get("answers", []):
                ans["section_id"] = ans.pop("source", ans.get("section_id", ""))
                ans["section_id"] = normalize_section_id(ans["section_id"])
                ans["student_id"] = student_id

            # ② 调判题图（answers 原地填充 is_correct / reason / method）
            state = {"student_id": student_id, "answers": data["answers"]}
            result = JUDGE_GRAPH.invoke(state)

            # ③ 批量写入 attempts（事务保护）
            record_attempts_batch(ATTEMPTS_DB, result["answers"])

            # ④ 返回结果
            results = [{
                "is_correct": a["is_correct"],
                "reason": a["reason"],
                "method": a.get("method", "rule"),
                "correct_answer": a["correct_answer"],
                "explanation": a.get("explanation", ""),
            } for a in result["answers"]]

            logger.info("submit-exam: student=%s, %d 题", student_id, len(results))
            self._serve_json({"ok": True, "results": results})

        except Exception as e:
            logger.exception("submit-exam 失败")
            self._serve_json({"ok": False, "error": str(e)}, status=400)

    def _handle_profile(self):
        from urllib.parse import parse_qs
        from dataclasses import asdict

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        student_id = params.get("student_id", [""])[0].strip()

        if not student_id:
            self._serve_json({"ok": False, "error": "缺少 student_id 参数"}, status=400)
            return

        try:
            # 1. 构建 BKT 画像
            profile = build_profile(student_id, ATTEMPTS_DB, mastery_backend="bkt")

            # 2. 提取 BKT states + error_map
            bkt_states = []
            error_map: dict[str, str] = {}
            for t in profile.topics:
                if t.bkt_state is not None:
                    bkt_states.append(t.bkt_state)
                if t.dominant_error_type:
                    error_map[t.section_id] = t.dominant_error_type

            # 3. Session 奖励（Phase 2）
            session_rewards = compute_session_rewards(ATTEMPTS_DB, student_id)

            # 4. Bandit 状态
            bandit_states = init_bandit_states(bkt_states, session_rewards)

            # 5. 推荐计划
            plan = build_recommendation_plan(
                bkt_states, error_map, student_id, target_count=20,
                session_rewards=session_rewards,
            )

            # 6. 从 sections.db 查章节标题，丰富 topic 展示
            section_titles: dict[str, str] = {}
            sections_db = os.path.join(os.path.dirname(__file__), "..", "cache", "sections.db")
            if os.path.exists(sections_db):
                try:
                    import sqlite3 as _sql
                    import re as _re
                    _conn = _sql.connect(sections_db)
                    _rows = _conn.execute("SELECT id, title FROM sections").fetchall()
                    _conn.close()
                    # 去 LaTeX 标记（$...$ 和 \mathrm{...} 等），合并多余空格
                    _latex_re = _re.compile(r"\$.*?\$|\\mathrm|\\mathbf|\\mathit|\\text|\\[a-z]+\{|\}|\\")
                    _space_re = _re.compile(r"\s{2,}")
                    for r in _rows:
                        if r[0] and r[1]:
                            clean = _latex_re.sub("", r[1])
                            clean = _space_re.sub(" ", clean).strip()
                            section_titles[r[0]] = clean or r[1]
                except Exception:
                    pass

            # 7. 组装 topics（BKT + Bandit 合并，按 P(L) 升序）
            bandit_map = {bs.section_id: bs for bs in bandit_states.values()}

            def _topic_sort_key(t):
                bkt = t.bkt_state
                return bkt.p_mastery if bkt else 1.0

            sorted_topics = sorted(profile.topics, key=_topic_sort_key)

            topics_json = []
            for t in sorted_topics:
                # 优先用 attempt 里的 topic，其次用 sections.db 的标题
                display_title = t.topic or section_titles.get(t.section_id, "")
                entry = {
                    "section_id": t.section_id,
                    "topic": display_title,
                    "total_attempts": t.total_attempts,
                    "accuracy": t.accuracy,
                    "recent_accuracy": t.recent_accuracy,
                    "mastery_level": t.mastery_level,
                    "dominant_error_type": ERROR_TYPE_LABELS.get(t.dominant_error_type, t.dominant_error_type),
                    "streak_wrong": t.streak_wrong,
                }
                if t.bkt_state is not None:
                    entry["bkt"] = {
                        "p_mastery": t.bkt_state.p_mastery,
                        "p_initial": t.bkt_state.p_initial,
                        "total_attempts": t.bkt_state.total_attempts,
                        "correct_count": t.bkt_state.correct_count,
                        "params": asdict(t.bkt_state.params),
                    }
                bs = bandit_map.get(t.section_id)
                if bs is not None:
                    entry["bandit"] = {
                        "alpha": bs.alpha,
                        "beta": bs.beta,
                    }
                topics_json.append(entry)

            # 7. 错因分布（中文 key）
            error_dist = {}
            for etype, cnt in profile.error_distribution.items():
                label = ERROR_TYPE_LABELS.get(etype, etype)
                error_dist[label] = cnt

            # 8. 推荐计划
            rec_json = {
                "items": [asdict(item) for item in plan.items],
                "target_count": plan.target_count,
                "reason": plan.reason,
            }

            result = {
                "ok": True,
                "student_id": profile.student_id,
                "overall_accuracy": profile.overall_accuracy,
                "total_attempts": profile.total_attempts,
                "mastery_summary": profile.mastery_summary,
                "topics": topics_json,
                "recommendation": rec_json,
                "error_distribution": error_dist,
                "risk_signals": profile.risk_signals,
            }
            logger.info("profile: student=%s, topics=%d, accuracy=%.0f%%",
                        student_id, len(topics_json), profile.overall_accuracy * 100)
            self._serve_json(result)

        except Exception:
            logger.exception("profile API 失败")
            self._serve_json({"ok": False, "error": "画像构建失败，查看服务器日志"}, status=500)

    def _handle_generate(self):
        global QUESTIONS

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._serve_json({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
            return

        mode = data.get("mode", "exam")
        student_id = data.get("student_id", "default").strip()
        focus = data.get("focus", "").strip()
        target_count = data.get("count", 0)
        allowed_types = data.get("types", "").strip()
        analysis_report = data.get("analysis_report", "").strip()

        if mode == "practice" and not student_id:
            self._serve_json({"ok": False, "error": "practice 模式需要 student_id"}, status=400)
            return

        config = DEFAULT_CONFIG.copy()
        db_path = config.get("db_path", "cache/sections.db")

        if not os.path.exists(db_path):
            self._serve_json({"ok": False, "error": f"数据库 {db_path} 不存在"}, status=500)
            return

        try:
            toc = build_toc_from_db(db_path)
            exam = ExamGraph(config=config, debug=False)
            _, questions = exam.propagate(
                db_path=db_path, toc=toc,
                focus=focus, target_count=target_count,
                allowed_types=allowed_types,
                analysis_report_path=analysis_report,
                mode=mode, student_id=student_id,
            )
            QUESTIONS = get_questions()
            logger.info("generate: mode=%s, student=%s, count=%d, reloaded=%d",
                        mode, student_id, len(questions), len(QUESTIONS))
            self._serve_json({
                "ok": True,
                "count": len(questions),
                "mode": mode,
            })
        except Exception:
            logger.exception("generate API 失败")
            self._serve_json({"ok": False, "error": "出题失败，查看服务器日志"}, status=500)

    def _handle_analyze_exam(self):
        import base64, tempfile
        from exam.parsers import parse_docx
        from exam.analyzers import analyze_exam, generate_report

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._serve_json({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
            return

        filename = data.get("filename", "exam.docx")
        b64 = data.get("data_base64", "")
        if not b64:
            self._serve_json({"ok": False, "error": "缺少文件数据"}, status=400)
            return

        try:
            raw = base64.b64decode(b64)
        except Exception:
            self._serve_json({"ok": False, "error": "文件数据解码失败"}, status=400)
            return

        # 写入临时文件
        tmpdir = tempfile.mkdtemp(prefix="exam_upload_")
        tmp_path = os.path.join(tmpdir, filename)
        with open(tmp_path, "wb") as f:
            f.write(raw)

        try:
            # 解析 → LLM 分析 → 生成报告
            parsed = parse_docx(tmp_path)
            logger.info("analyze-exam: 解析完成 %s (%d 分组)", filename,
                        len(parsed.get("sections", [])))
            result = analyze_exam(parsed, DEFAULT_CONFIG.copy())
            q_count = len(result.get("questions", []))
            logger.info("analyze-exam: LLM 分析完成, %d 道题", q_count)

            analysis_dir = os.path.join(os.path.dirname(__file__), "..", "analysis")
            os.makedirs(analysis_dir, exist_ok=True)
            json_path = generate_report([result], analysis_dir)
            report_file = os.path.basename(json_path)

            self._serve_json({
                "ok": True,
                "filename": report_file,
                "path": os.path.abspath(json_path),
                "questions": q_count,
            })
        except Exception as e:
            logger.exception("analyze-exam 失败")
            self._serve_json({"ok": False, "error": str(e)[:200]}, status=500)
        finally:
            # 清理临时文件
            try:
                os.remove(tmp_path)
                os.rmdir(tmpdir)
            except Exception:
                pass

    def _serve_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        logger.info(args[0])


# ── 入口 ──

def main():
    global QUESTIONS, JUDGE_GRAPH

    parser = argparse.ArgumentParser(description="答题前端桥梁")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    cache_dir = os.path.join(os.path.dirname(__file__), "..", "cache")
    os.makedirs(cache_dir, exist_ok=True)

    # 启动初始化
    QUESTIONS = get_questions()
    init_attempts_db(ATTEMPTS_DB)
    init_error_labels_db(ATTEMPTS_DB)
    llm_client = create_llm_client()
    JUDGE_GRAPH = JudgeGraph(llm_client)
    logger.info("已加载 %d 道题目，判题图已编译", len(QUESTIONS))

    server = HTTPServer(("0.0.0.0", args.port), QuizHandler)
    logger.info("启动: http://localhost:%s/quiz.html", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
