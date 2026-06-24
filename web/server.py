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
from exam.student_profile.storage import init_attempts_db, init_error_labels_db, record_attempts_batch
from exam.agents.utils.agent_utils import create_llm_client

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
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/submit-exam":
            self._handle_submit_exam()
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

            # ① 修复字段映射 + 下沉 student_id
            for ans in data.get("answers", []):
                ans["section_id"] = ans.pop("source", ans.get("section_id", ""))
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
