"""Web/API 入口：提供出题、答题、画像和往年试卷分析。

用法：
    python web/server.py
    python web/server.py --port 8080

产品入口统一为 web/index.html：
    POST /api/generate      → ExamGraph 出题
    POST /api/submit-exam   → JudgeGraph 批量判题并写入 attempts.db
    GET  /api/profile       → ProfileGraph/BKT/Bandit 画像与推荐
    POST /api/analyze-exam  → 上传 DOCX 并生成往年试卷分析
    GET  /api/books         → 教材列表
    POST /api/books/upload  → 上传 PDF 并异步解析为教材库
"""

import argparse
import json
import os
import sys
import glob
import logging
import mimetypes
import threading
import uuid
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

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
from exam.book_registry import (
    book_exists,
    build_book_paths,
    get_book,
    list_books,
    register_book,
    sanitize_book_id,
)
from exam.config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="[server] %(message)s")
logger = logging.getLogger(__name__)


PORT = 8765
DEFAULT_STUDENT_ID = "default"  # 单用户学习 Agent，内部归属键，非产品功能

# ── 模块级状态（启动时初始化）──
QUESTIONS = []
QUESTIONS_BY_BOOK = {}
JUDGE_GRAPH = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EVAL_REPORTS_DIR = os.path.join(PROJECT_ROOT, "evals", "reports")
BOOK_UPLOAD_JOBS = {}
BOOK_UPLOAD_LOCK = threading.Lock()
GENERATE_JOBS = {}
GENERATE_LOCK = threading.Lock()
MINERU_TOKEN_ENV_NAMES = (
    "MINERU_API_TOKEN",
    "MINERU_TOKEN",
    "BOOKTOEXAM_MINERU_TOKEN",
    "MINERU_API_KEY",
)


# ── 书籍上下文 ──

def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _query_book_id(parsed) -> str:
    qs = parse_qs(parsed.query or "")
    return (qs.get("book_id") or [""])[0]


def _resolve_book_context(book_id: str | None = None) -> dict:
    ctx = get_book(book_id)
    os.makedirs(os.path.dirname(ctx["attempts_db"]) or ".", exist_ok=True)
    os.makedirs(ctx["output_dir"], exist_ok=True)
    os.makedirs(ctx["analysis_dir"], exist_ok=True)
    return ctx


def _ensure_attempt_dbs(ctx: dict) -> None:
    init_attempts_db(ctx["attempts_db"])
    init_error_labels_db(ctx["attempts_db"])
    init_long_memory_db(ctx["attempts_db"])


def _job_update(job_id: str, **kwargs) -> None:
    with BOOK_UPLOAD_LOCK:
        job = BOOK_UPLOAD_JOBS.setdefault(job_id, {})
        job.update(kwargs)
        job["updated_at"] = _now_text()


def _generate_job_update(job_id: str, **kwargs) -> None:
    with GENERATE_LOCK:
        job = GENERATE_JOBS.setdefault(job_id, {})
        job.update(kwargs)
        job["updated_at"] = _now_text()


def _parse_multipart_form(handler) -> tuple[dict[str, str], dict[str, dict]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("请求必须是 multipart/form-data")

    length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(length) if length else b""
    header = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8")
    message = BytesParser(policy=email_policy).parsebytes(header + body)

    fields: dict[str, str] = {}
    files: dict[str, dict] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files[name] = {
                "filename": os.path.basename(filename),
                "content": payload,
                "content_type": part.get_content_type(),
            }
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace").strip()
    return fields, files


def _load_dotenv_value(key: str) -> str:
    env_path = os.path.join(PROJECT_ROOT, ".env")
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


def _resolve_mineru_token(explicit_token: str = "") -> str:
    if explicit_token:
        return explicit_token.strip()
    for name in MINERU_TOKEN_ENV_NAMES:
        value = os.environ.get(name) or _load_dotenv_value(name)
        if value:
            return value.strip()
    return ""


def _safe_upload_filename(filename: str) -> str:
    name = os.path.basename(filename or "book.pdf").strip()
    if not name.lower().endswith(".pdf"):
        raise ValueError("只支持上传 PDF 教材")
    return name or "book.pdf"


def _run_book_parse_job(
    job_id: str,
    book_id: str,
    title: str,
    pdf_path: str,
    mineru_token: str = "",
) -> None:
    parser = None
    try:
        paths = build_book_paths(book_id)
        for path in (paths["book_cache_dir"], paths["images_dir"], paths["output_dir"], paths["analysis_dir"]):
            os.makedirs(path, exist_ok=True)

        _job_update(job_id, status="running", stage="OCR 解析教材")
        from exam.pdf_parser import PdfParser

        token = _resolve_mineru_token(mineru_token)
        if not token:
            raise RuntimeError("缺少 MinerU Token，无法进行 OCR 解析")
        parser = PdfParser(
            pdf_path,
            db_path=paths["sections_db"],
            mineru_token=token,
            force_ocr=True,
            assets_dir=paths["images_dir"],
            manifest_path=paths["media_manifest"],
        )
        toc = parser.parse()

        book = register_book(
            book_id=book_id,
            title=title,
            pdf_path=pdf_path,
            sections_db=paths["sections_db"],
            attempts_db=paths["attempts_db"],
            output_dir=paths["output_dir"],
            analysis_dir=paths["analysis_dir"],
        )
        _ensure_attempt_dbs(book)

        _job_update(
            job_id,
            status="done",
            stage="完成",
            book_id=book_id,
            title=title,
            section_count=sum(len(ch.get("sections", [])) for ch in toc),
            book=book,
        )
    except Exception as exc:
        logger.exception("教材上传解析失败 job=%s book=%s", job_id, book_id)
        _job_update(job_id, status="failed", stage="失败", error=str(exc)[:500])
    finally:
        if parser is not None:
            try:
                parser.close()
            except Exception:
                pass


def _run_generate_job(
    job_id: str,
    ctx: dict,
    config: dict,
    db_path: str,
    mode: str,
    student_id: str,
    focus: str,
    target_count: int,
    allowed_types: str,
    allowed_difficulty: str,
    analysis_report: str,
    session_id: int | None,
) -> None:
    """Run ExamGraph in the background and update the in-memory job state."""
    global QUESTIONS

    try:
        _generate_job_update(job_id, status="running", stage="构建目录")
        toc = build_toc_from_db(db_path)

        _generate_job_update(job_id, stage="Agent 出题中")
        exam = ExamGraph(config=config, debug=False)
        final_state, questions = exam.propagate(
            db_path=db_path, toc=toc,
            focus=focus, target_count=target_count,
            allowed_types=allowed_types,
            allowed_difficulty=allowed_difficulty,
            analysis_report_path=analysis_report,
            mode=mode, student_id=student_id,
        )

        QUESTIONS = questions
        QUESTIONS_BY_BOOK[ctx["book_id"]] = questions
        logger.info(
            "generate job done: job=%s, book=%s, mode=%s, student=%s, generated=%d",
            job_id, ctx["book_id"], mode, student_id, len(questions),
        )

        if session_id and mode == "practice":
            _generate_job_update(job_id, stage="更新练习计划")
            update_generated_session_plan(
                ctx["attempts_db"],
                session_id,
                final_state.get("practice_plan") or {},
            )

        _generate_job_update(
            job_id,
            status="done",
            stage="完成",
            count=len(questions),
            mode=mode,
            session_id=session_id,
            book_id=ctx["book_id"],
        )
    except Exception:
        logger.exception("generate job failed: job=%s", job_id)
        try:
            abort_session(ctx["attempts_db"], session_id, "generate failed")
        except Exception:
            logger.exception("标记 session=%s 为 aborted 失败", session_id)
        _generate_job_update(
            job_id,
            status="failed",
            stage="失败",
            error="出题失败，查看服务器日志",
            mode=mode,
            session_id=session_id,
            book_id=ctx.get("book_id", ""),
        )


# ── 题目加载 ──

def _load_latest_output(output_dir: str | None = None):
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "output")
    pattern = os.path.join(output_dir, "questions_*.json")
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


def _infer_topic_from_questions(answer: dict, book_id: str = "") -> str:
    """Recover topic from the in-memory generated question list when omitted."""
    stem = answer.get("stem", "")
    correct = answer.get("correct_answer", "")
    section_id = answer.get("section_id", "")
    source_questions = QUESTIONS_BY_BOOK.get(book_id) or QUESTIONS
    for q in source_questions:
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


def _list_analysis_reports(analysis_dir: str | None = None) -> list[dict]:
    """列出 analysis/ 目录下所有可用的往年试卷分析报告。"""
    analysis_dir = analysis_dir or os.path.join(os.path.dirname(__file__), "..", "analysis")
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


def _list_exams(output_dir: str | None = None) -> list[dict]:
    """列出 output/ 下所有历史试卷。"""
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "output")
    pattern = os.path.join(output_dir, "questions_*.json")
    exams = []
    for f in sorted(glob.glob(pattern), reverse=True):
        fname = os.path.basename(f)
        try:
            with open(f, "r", encoding="utf-8") as fh:
                qs = json.load(fh)
            count = len(qs) if isinstance(qs, list) else 0
            # extract timestamp from filename: questions_YYYYMMDD_HHMMSS.json
            ts = fname.replace("questions_", "").replace(".json", "")
            exams.append({
                "filename": fname,
                "timestamp": ts,
                "count": count,
            })
        except Exception:
            exams.append({"filename": fname, "timestamp": "", "count": 0})
    return exams


def _load_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_eval_report_path(value: str) -> str:
    """Resolve an eval report path and ensure it stays inside evals/reports."""
    if not value:
        raise FileNotFoundError("empty report path")

    raw = os.path.normpath(value)
    if os.path.isabs(raw):
        path = raw
    else:
        path = os.path.join(PROJECT_ROOT, raw)

    path = os.path.abspath(path)
    reports_root = os.path.abspath(EVAL_REPORTS_DIR)
    if os.path.commonpath([reports_root, path]) != reports_root:
        raise PermissionError("report path is outside evals/reports")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path


def _load_eval_index() -> dict:
    index_path = os.path.join(EVAL_REPORTS_DIR, "index.json")
    if not os.path.isfile(index_path):
        return {"version": 1, "updated_at": "", "runs": []}
    try:
        data = _load_json_file(index_path)
    except Exception:
        return {"version": 1, "updated_at": "", "runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        data["runs"] = []
    return data


def _eval_run_summary(run: dict) -> dict:
    diffs = run.get("diffs") or []
    child_reports = run.get("child_reports") or {}
    return {
        "run_id": run.get("run_id", ""),
        "created_at": run.get("created_at", ""),
        "summary": run.get("summary", ""),
        "score": run.get("score", 0),
        "agent_report_json": run.get("agent_report_json", ""),
        "agent_report_md": run.get("agent_report_md", ""),
        "metrics": run.get("metrics") or {},
        "diffs": diffs,
        "child_reports": child_reports,
        "failure_count": sum(
            int(section.get("failure_count") or 0)
            for section in child_reports.values()
            if isinstance(section, dict)
        ),
        "regression_count": sum(1 for item in diffs if item.get("status") == "regressed"),
        "improvement_count": sum(1 for item in diffs if item.get("status") == "improved"),
    }


def _list_eval_runs() -> dict:
    index = _load_eval_index()
    runs = [_eval_run_summary(run) for run in index.get("runs", [])]
    runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {
        "ok": True,
        "updated_at": index.get("updated_at", ""),
        "count": len(runs),
        "runs": runs,
    }


def _find_eval_run(run_id: str) -> dict | None:
    index = _load_eval_index()
    for run in index.get("runs", []):
        if str(run.get("run_id", "")) == run_id:
            return run
    return None


def _load_eval_report_for_run(run: dict) -> dict:
    report_path = _resolve_eval_report_path(str(run.get("agent_report_json", "")))
    report = _load_json_file(report_path)
    return {
        "ok": True,
        "run": _eval_run_summary(run),
        "report": report,
        "report_path": report_path,
    }


def _load_latest_eval_report() -> dict:
    index = _load_eval_index()
    runs = index.get("runs", [])
    if not runs:
        return {"ok": False, "error": "暂无评测报告"}
    return _load_eval_report_for_run(runs[-1])


def _serve_exam_detail(self, filename: str, output_dir: str | None = None):
    """返回指定试卷文件的完整题目列表（含答案）。"""
    # 防止路径穿越
    if ".." in filename or "/" in filename or "\\" in filename:
        self._serve_json({"error": "非法文件名"}, status=400)
        return
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "output")
    fpath = os.path.join(output_dir, filename)
    if not os.path.isfile(fpath):
        self._serve_json({"error": "试卷不存在"}, status=404)
        return
    try:
        with open(fpath, "r", encoding="utf-8") as fh:
            questions = json.load(fh)
        self._serve_json(questions)
    except Exception:
        self._serve_json({"error": "读取失败"}, status=500)


def get_questions(book_id: str | None = None):
    try:
        ctx = _resolve_book_context(book_id)
        loaded = _load_latest_output(ctx["output_dir"])
    except Exception:
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
        if parsed.path == "/api/books":
            self._serve_json(list_books())
            return
        if parsed.path.startswith("/api/books/jobs/"):
            job_id = unquote(parsed.path[len("/api/books/jobs/"):])
            self._handle_book_job(job_id)
            return
        if parsed.path == "/api/media":
            self._handle_media(parsed)
            return
        if parsed.path == "/api/questions":
            try:
                ctx = _resolve_book_context(_query_book_id(parsed))
                book_id = ctx["book_id"]
                if book_id not in QUESTIONS_BY_BOOK:
                    QUESTIONS_BY_BOOK[book_id] = get_questions(book_id)
                self._serve_json(QUESTIONS_BY_BOOK[book_id])
            except KeyError as e:
                self._serve_json({"ok": False, "error": str(e)}, status=404)
            return
        if parsed.path == "/api/questions/demo":
            self._serve_json(_demo_questions())
            return
        if parsed.path == "/api/profile":
            self._handle_profile(_query_book_id(parsed))
            return
        if parsed.path == "/api/analysis-reports":
            try:
                ctx = _resolve_book_context(_query_book_id(parsed))
                self._serve_json(_list_analysis_reports(ctx["analysis_dir"]))
            except KeyError as e:
                self._serve_json({"ok": False, "error": str(e)}, status=404)
            return
        if parsed.path == "/api/exams":
            try:
                ctx = _resolve_book_context(_query_book_id(parsed))
                self._serve_json(_list_exams(ctx["output_dir"]))
            except KeyError as e:
                self._serve_json({"ok": False, "error": str(e)}, status=404)
            return
        if parsed.path.startswith("/api/exams/"):
            filename = parsed.path[len("/api/exams/"):]
            try:
                ctx = _resolve_book_context(_query_book_id(parsed))
                _serve_exam_detail(self, filename, ctx["output_dir"])
            except KeyError as e:
                self._serve_json({"ok": False, "error": str(e)}, status=404)
            return
        if parsed.path.startswith("/api/generate/jobs/"):
            job_id = unquote(parsed.path[len("/api/generate/jobs/"):])
            self._handle_generate_job(job_id)
            return
        if parsed.path == "/api/evals":
            self._handle_evals_list()
            return
        if parsed.path == "/api/evals/latest":
            self._handle_eval_latest()
            return
        if parsed.path.startswith("/api/evals/"):
            run_id = unquote(parsed.path[len("/api/evals/"):])
            self._handle_eval_run(run_id)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/books/upload":
            self._handle_book_upload()
            return
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

    def _handle_book_job(self, job_id: str):
        if not job_id:
            self._serve_json({"ok": False, "error": "缺少 job_id"}, status=400)
            return
        with BOOK_UPLOAD_LOCK:
            job = dict(BOOK_UPLOAD_JOBS.get(job_id) or {})
        if not job:
            self._serve_json({"ok": False, "error": "上传任务不存在"}, status=404)
            return
        job["ok"] = True
        self._serve_json(job)

    def _handle_media(self, parsed):
        qs = parse_qs(parsed.query or "")
        src = (qs.get("src") or [""])[0].strip()
        if not src:
            self._serve_json({"ok": False, "error": "缺少 src"}, status=400)
            return

        raw_src = src.replace("\\", "/")
        parts = [part for part in raw_src.split("/") if part]
        if (
            os.path.isabs(raw_src)
            or raw_src.startswith("/")
            or ":" in raw_src
            or any(part == ".." for part in parts)
        ):
            self._serve_json({"ok": False, "error": "非法媒体路径"}, status=400)
            return

        try:
            ctx = _resolve_book_context((qs.get("book_id") or [""])[0])
        except KeyError as e:
            self._serve_json({"ok": False, "error": str(e)}, status=404)
            return

        manifest_root = os.path.abspath(os.path.dirname(ctx["media_manifest"]) or ctx["book_cache_dir"])
        images_root = os.path.abspath(ctx["images_dir"])
        media_path = os.path.abspath(os.path.join(manifest_root, raw_src))
        try:
            if os.path.commonpath([images_root, media_path]) != images_root:
                self._serve_json({"ok": False, "error": "媒体路径越界"}, status=403)
                return
        except ValueError:
            self._serve_json({"ok": False, "error": "媒体路径越界"}, status=403)
            return

        if not os.path.isfile(media_path):
            self._serve_json({"ok": False, "error": "媒体不存在"}, status=404)
            return

        content_type = mimetypes.guess_type(media_path)[0] or "application/octet-stream"
        try:
            with open(media_path, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            logger.exception("媒体读取失败: %s", media_path)
            self._serve_json({"ok": False, "error": "媒体读取失败"}, status=500)

    def _handle_generate_job(self, job_id: str):
        if not job_id:
            self._serve_json({"ok": False, "error": "缺少 job_id"}, status=400)
            return
        with GENERATE_LOCK:
            job = dict(GENERATE_JOBS.get(job_id) or {})
        if not job:
            self._serve_json({"ok": False, "error": "出题任务不存在"}, status=404)
            return
        job["ok"] = True
        self._serve_json(job)

    def _handle_book_upload(self):
        try:
            fields, files = _parse_multipart_form(self)
            pdf_file = files.get("pdf")
            if not pdf_file or not pdf_file.get("content"):
                self._serve_json({"ok": False, "error": "缺少 PDF 文件"}, status=400)
                return

            raw_title = fields.get("title") or os.path.splitext(pdf_file.get("filename", ""))[0]
            title = raw_title.strip() or "未命名教材"
            book_id = sanitize_book_id(fields.get("book_id") or title)
            if book_exists(book_id):
                self._serve_json({"ok": False, "error": f"教材 ID 已存在: {book_id}"}, status=409)
                return

            filename = _safe_upload_filename(pdf_file.get("filename", "book.pdf"))
            paths = build_book_paths(book_id)
            upload_dir = paths["upload_dir"]
            os.makedirs(upload_dir, exist_ok=True)
            pdf_path = os.path.join(upload_dir, filename)
            with open(pdf_path, "wb") as fh:
                fh.write(pdf_file["content"])

            job_id = uuid.uuid4().hex[:12]
            with BOOK_UPLOAD_LOCK:
                BOOK_UPLOAD_JOBS[job_id] = {
                    "ok": True,
                    "job_id": job_id,
                    "book_id": book_id,
                    "title": title,
                    "status": "queued",
                    "stage": "等待 OCR 解析",
                    "created_at": _now_text(),
                    "updated_at": _now_text(),
                }

            thread = threading.Thread(
                target=_run_book_parse_job,
                args=(job_id, book_id, title, pdf_path, fields.get("mineru_token", "")),
                daemon=True,
            )
            thread.start()
            self._serve_json({
                "ok": True,
                "job_id": job_id,
                "book_id": book_id,
                "title": title,
                "status": "queued",
            })
        except ValueError as e:
            self._serve_json({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            logger.exception("教材上传失败")
            self._serve_json({"ok": False, "error": str(e)[:200]}, status=500)

    def _handle_submit_exam(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._serve_json({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
            return

        try:
            ctx = _resolve_book_context(data.get("book_id", ""))
            _ensure_attempt_dbs(ctx)
            book_id = ctx["book_id"]
            student_id = DEFAULT_STUDENT_ID
            session_id = data.get("session_id")

            # 校验 session_id：存在 + 归属 + 状态为 active
            if session_id is not None:
                session_row = get_session(ctx["attempts_db"], session_id)
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
                    ans["topic"] = _infer_topic_from_questions(ans, book_id)
                ans["student_id"] = student_id

            # ② 调判题图（answers 原地填充 is_correct / reason / method）
            state = {"student_id": student_id, "answers": data["answers"]}
            result = JUDGE_GRAPH.invoke(state)

            # ③ 批量写入 attempts（事务保护），带 session_id
            attempt_ids = record_attempts_batch(
                ctx["attempts_db"], result["answers"],
                session_id=session_id,
            )

            # ④ 长期记忆闭环：post-session 处理
            session_effect = complete_learning_session_after_submit(
                ctx["attempts_db"],
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

            logger.info("submit-exam: book=%s, student=%s, %d 题", book_id, student_id, len(results))
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
            ctx = _resolve_book_context(data.get("book_id", ""))
            _ensure_attempt_dbs(ctx)
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
                ctx["attempts_db"],
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

    def _handle_profile(self, book_id: str = ""):
        try:
            ctx = _resolve_book_context(book_id)
            _ensure_attempt_dbs(ctx)
            student_id = DEFAULT_STUDENT_ID
            result = build_profile_response(student_id, ctx["attempts_db"], ctx["sections_db"])
            logger.info("profile: book=%s, student=%s, topics=%d, accuracy=%.0f%%",
                        ctx["book_id"], student_id, len(result.get("topics", [])), result.get("overall_accuracy", 0) * 100)
            self._serve_json(result)

        except Exception:
            logger.exception("profile API 失败")
            self._serve_json({"ok": False, "error": "画像构建失败，查看服务器日志"}, status=500)

    def _handle_generate(self):
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
        allowed_difficulty = data.get("difficulty", "").strip()
        analysis_report = data.get("analysis_report", "").strip()

        try:
            ctx = _resolve_book_context(data.get("book_id", ""))
            _ensure_attempt_dbs(ctx)
        except Exception as e:
            self._serve_json({"ok": False, "error": str(e)}, status=400)
            return

        config = DEFAULT_CONFIG.copy()
        config["db_path"] = ctx["sections_db"]
        config["results_dir"] = ctx["output_dir"]
        config["data_cache_dir"] = ctx.get("book_cache_dir") or os.path.dirname(ctx["sections_db"])
        db_path = ctx["sections_db"]

        if not os.path.exists(db_path):
            self._serve_json({"ok": False, "error": f"数据库 {db_path} 不存在"}, status=500)
            return

        # ── 长期记忆闭环：创建 session ──
        session = start_learning_session(
            ctx["attempts_db"],
            student_id=student_id,
            mode=mode,
            target_count=target_count,
        )
        session_id = session.get("session_id")

        job_id = uuid.uuid4().hex[:12]
        with GENERATE_LOCK:
            GENERATE_JOBS[job_id] = {
                "ok": True,
                "job_id": job_id,
                "status": "queued",
                "stage": "等待出题",
                "mode": mode,
                "session_id": session_id,
                "book_id": ctx["book_id"],
                "created_at": _now_text(),
                "updated_at": _now_text(),
            }

        thread = threading.Thread(
            target=_run_generate_job,
            args=(
                job_id,
                ctx,
                config,
                db_path,
                mode,
                student_id,
                focus,
                target_count,
                allowed_types,
                allowed_difficulty,
                analysis_report,
                session_id,
            ),
            daemon=True,
        )
        thread.start()

        logger.info("generate job queued: job=%s, book=%s, mode=%s, student=%s",
                    job_id, ctx["book_id"], mode, student_id)
        self._serve_json({
            "ok": True,
            "async": True,
            "job_id": job_id,
            "status": "queued",
            "stage": "等待出题",
            "mode": mode,
            "session_id": session_id,
            "book_id": ctx["book_id"],
        }, status=202)

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
            ctx = _resolve_book_context(data.get("book_id", ""))
        except Exception as e:
            self._serve_json({"ok": False, "error": str(e)}, status=400)
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
            config = DEFAULT_CONFIG.copy()
            config["db_path"] = ctx["sections_db"]
            config["results_dir"] = ctx["output_dir"]
            config["data_cache_dir"] = ctx.get("book_cache_dir") or os.path.dirname(ctx["sections_db"])
            result = analyze_exam(parsed, config)
            q_count = len(result.get("questions", []))
            logger.info("analyze-exam: LLM 分析完成, %d 道题", q_count)

            analysis_dir = ctx["analysis_dir"]
            os.makedirs(analysis_dir, exist_ok=True)
            json_path = generate_report([result], analysis_dir)
            report_file = os.path.basename(json_path)

            self._serve_json({
                "ok": True,
                "filename": report_file,
                "path": os.path.abspath(json_path),
                "questions": q_count,
                "book_id": ctx["book_id"],
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

    def _handle_evals_list(self):
        try:
            self._serve_json(_list_eval_runs())
        except Exception:
            logger.exception("evals list API 失败")
            self._serve_json({"ok": False, "error": "评测列表读取失败"}, status=500)

    def _handle_eval_latest(self):
        try:
            data = _load_latest_eval_report()
            status = 200 if data.get("ok") else 404
            self._serve_json(data, status=status)
        except Exception:
            logger.exception("eval latest API 失败")
            self._serve_json({"ok": False, "error": "最新评测报告读取失败"}, status=500)

    def _handle_eval_run(self, run_id: str):
        try:
            if not run_id:
                self._serve_json({"ok": False, "error": "缺少 run_id"}, status=400)
                return
            run = _find_eval_run(run_id)
            if not run:
                self._serve_json({"ok": False, "error": "未找到评测 run"}, status=404)
                return
            self._serve_json(_load_eval_report_for_run(run))
        except PermissionError:
            self._serve_json({"ok": False, "error": "报告路径不允许访问"}, status=403)
        except FileNotFoundError:
            self._serve_json({"ok": False, "error": "报告文件不存在"}, status=404)
        except Exception:
            logger.exception("eval run API 失败")
            self._serve_json({"ok": False, "error": "评测报告读取失败"}, status=500)

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
    default_ctx = _resolve_book_context("")
    QUESTIONS = get_questions(default_ctx["book_id"])
    QUESTIONS_BY_BOOK[default_ctx["book_id"]] = QUESTIONS
    _ensure_attempt_dbs(default_ctx)
    llm_client = create_llm_client()
    JUDGE_GRAPH = JudgeGraph(llm_client)
    logger.info("已加载默认教材 %s 的 %d 道题目，判题图已编译",
                default_ctx["book_id"], len(QUESTIONS))

    server = HTTPServer(("0.0.0.0", args.port), QuizHandler)
    logger.info("启动: http://localhost:%s/index.html", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
