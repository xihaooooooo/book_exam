"""Web/API 入口：提供出题、答题、画像和往年试卷分析。

用法：
    python web/server.py
    python web/server.py --port 8080

产品入口统一为 web/index.html：
    POST /api/generate      → ExamGraph 出题
    POST /api/submit-exam   → JudgeGraph 批量判题并写入 attempts.db
    GET  /api/profile       → ProfileGraph/BKT/Bandit 画像与推荐
    POST /api/analyze-exam  → 上传 DOCX 并生成往年试卷分析
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
from exam.student_profile.storage import (
    apply_attempt_correction,
    init_attempts_db,
    init_error_labels_db,
    record_attempts_batch,
)
from exam.student_profile.profile_engine import normalize_section_id
from exam.student_profile.profile_presenter import build_profile_response
from exam.student_profile.schemas import ERROR_TYPES
from exam.student_profile.session_service import (
    abort_session,
    complete_learning_session_after_submit,
    start_learning_session,
    update_generated_session_plan,
)
from exam.student_profile.session_storage import (
    get_session,
    init_long_memory_db,
)
from exam.agents.utils.agent_utils import create_llm_client, build_toc_from_db
from exam.config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="[server] %(message)s")
logger = logging.getLogger(__name__)


PORT = 8765
DEFAULT_STUDENT_ID = "default"  # 单用户学习 Agent，内部归属键，非产品功能

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


def _infer_topic_from_questions(answer: dict) -> str:
    """Recover topic from the in-memory generated question list when omitted."""
    stem = answer.get("stem", "")
    correct = answer.get("correct_answer", "")
    section_id = answer.get("section_id", "")
    for q in QUESTIONS:
        if not q.get("topic"):
            continue
        if stem and q.get("stem") == stem:
            return q.get("topic", "")
        if (
            section_id
            and q.get("source") == section_id
            and correct
            and q.get("correct_answer") == correct
        ):
            return q.get("topic", "")
    return ""


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
        if parsed.path == "/api/attempt-correction":
            self._handle_attempt_correction()
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
            student_id = DEFAULT_STUDENT_ID
            session_id = data.get("session_id")

            # 校验 session_id：存在 + 归属 + 状态为 active
            if session_id is not None:
                session_row = get_session(ATTEMPTS_DB, session_id)
                if not session_row:
                    logger.warning("submit-exam: session_id=%s 不存在，降级为 null", session_id)
                    session_id = None
                elif session_row.get("student_id") != student_id:
                    logger.warning(
                        "submit-exam: session_id=%s 归属 %s，不匹配 %s，降级为 null",
                        session_id, session_row.get("student_id"), student_id,
                    )
                    session_id = None
                elif session_row.get("status") != "active":
                    logger.warning(
                        "submit-exam: session_id=%s 状态=%s 非 active，降级为 null",
                        session_id, session_row.get("status"),
                    )
                    session_id = None

            # ① 修复字段映射 + 下沉 student_id + 归一化章节编号
            for ans in data.get("answers", []):
                ans["section_id"] = ans.pop("source", ans.get("section_id", ""))
                ans["section_id"] = normalize_section_id(ans["section_id"])
                if not ans.get("topic"):
                    ans["topic"] = _infer_topic_from_questions(ans)
                ans["student_id"] = student_id

            # ② 调判题图（answers 原地填充 is_correct / reason / method）
            state = {"student_id": student_id, "answers": data["answers"]}
            result = JUDGE_GRAPH.invoke(state)

            # ③ 批量写入 attempts（事务保护），带 session_id
            attempt_ids = record_attempts_batch(
                ATTEMPTS_DB, result["answers"],
                session_id=session_id,
            )

            # ④ 长期记忆闭环：post-session 处理
            session_effect = complete_learning_session_after_submit(
                ATTEMPTS_DB,
                student_id,
                session_id,
                result["answers"],
            )

            # ⑤ 返回结果
            results = [{
                "attempt_id": attempt_ids[i] if i < len(attempt_ids) else None,
                "is_correct": a["is_correct"],
                "reason": a["reason"],
                "method": a.get("method", "rule"),
                "correct_answer": a["correct_answer"],
                "explanation": a.get("explanation", ""),
                "error_type": a.get("error_type", ""),
            } for i, a in enumerate(result["answers"])]

            response = {"ok": True, "results": results}
            if session_effect:
                response["session"] = session_effect

            logger.info("submit-exam: student=%s, %d 题", student_id, len(results))
            self._serve_json(response)

        except Exception as e:
            logger.exception("submit-exam 失败")
            self._serve_json({"ok": False, "error": str(e)}, status=400)

    def _handle_attempt_correction(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._serve_json({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
            return

        try:
            attempt_id = int(data.get("attempt_id") or 0)
            is_correct = bool(data.get("is_correct"))
            error_type = (data.get("error_type") or "").strip()
            if not attempt_id:
                self._serve_json({"ok": False, "error": "缺少 attempt_id"}, status=400)
                return
            if not is_correct and error_type and error_type not in ERROR_TYPES:
                self._serve_json({"ok": False, "error": "未知错因类型"}, status=400)
                return

            ok = apply_attempt_correction(
                ATTEMPTS_DB,
                attempt_id=attempt_id,
                student_id=DEFAULT_STUDENT_ID,
                is_correct=is_correct,
                error_type=error_type,
                reason="用户手动修正",
            )
            if not ok:
                self._serve_json({"ok": False, "error": "未找到可修正的作答记录"}, status=404)
                return
            self._serve_json({"ok": True})
        except Exception as e:
            logger.exception("attempt correction 失败")
            self._serve_json({"ok": False, "error": str(e)}, status=400)

    def _handle_profile(self):
        try:
            student_id = DEFAULT_STUDENT_ID
            sections_db = os.path.join(os.path.dirname(__file__), "..", "cache", "sections.db")
            result = build_profile_response(student_id, ATTEMPTS_DB, sections_db)
            logger.info("profile: student=%s, topics=%d, accuracy=%.0f%%",
                        student_id, len(result.get("topics", [])), result.get("overall_accuracy", 0) * 100)
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
        student_id = DEFAULT_STUDENT_ID
        focus = data.get("focus", "").strip()
        target_count = data.get("count", 0)
        allowed_types = data.get("types", "").strip()
        analysis_report = data.get("analysis_report", "").strip()

        config = DEFAULT_CONFIG.copy()
        db_path = config.get("db_path", "cache/sections.db")

        if not os.path.exists(db_path):
            self._serve_json({"ok": False, "error": f"数据库 {db_path} 不存在"}, status=500)
            return

        # ── 长期记忆闭环：创建 session ──
        session = start_learning_session(
            ATTEMPTS_DB,
            student_id=student_id,
            mode=mode,
            target_count=target_count,
        )
        session_id = session.get("session_id")

        try:
            toc = build_toc_from_db(db_path)
            exam = ExamGraph(config=config, debug=False)
            final_state, questions = exam.propagate(
                db_path=db_path, toc=toc,
                focus=focus, target_count=target_count,
                allowed_types=allowed_types,
                analysis_report_path=analysis_report,
                mode=mode, student_id=student_id,
            )
            QUESTIONS = questions
            logger.info("generate: mode=%s, student=%s, generated=%d",
                        mode, student_id, len(QUESTIONS))

            # ③ 提取 practice_plan 回写 session
            if session_id and mode == "practice":
                update_generated_session_plan(
                    ATTEMPTS_DB,
                    session_id,
                    final_state.get("practice_plan") or {},
                )

            self._serve_json({
                "ok": True,
                "count": len(questions),
                "mode": mode,
                "session_id": session_id,
            })
        except Exception:
            logger.exception("generate API 失败")
            # 出题失败 → 将 session 标记为 aborted，避免残留 active 记录
            try:
                abort_session(ATTEMPTS_DB, session_id, "generate failed")
            except Exception:
                logger.exception("标记 session=%s 为 aborted 失败", session_id)
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
    init_long_memory_db(ATTEMPTS_DB)
    llm_client = create_llm_client()
    JUDGE_GRAPH = JudgeGraph(llm_client)
    logger.info("已加载 %d 道题目，判题图已编译", len(QUESTIONS))

    server = HTTPServer(("0.0.0.0", args.port), QuizHandler)
    logger.info("启动: http://localhost:%s/index.html", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
