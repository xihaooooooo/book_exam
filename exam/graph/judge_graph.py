"""批量判题图。JudgeGraph 类，judge_all 单节点，asyncio.gather 并发 LLM。

用法：
    from exam.graph.judge_graph import JudgeGraph
    jg = JudgeGraph(llm_client)
    result = jg.invoke({
        "student_id": "S001",
        "answers": [{"question_type":"choice","student_answer":"C",...}, ...],
    })
"""

import asyncio
import logging
import re
import threading
from langgraph.graph import StateGraph, END
from exam.agents.utils.agent_states import JudgeState

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 30
LLM_CONCURRENCY = 5


class JudgeGraph:
    """批量判题 LangGraph 图。

    - choice/fill_blank/code_fill：纯文本规则
    - short_answer/comprehensive：asyncio.gather 并发 LLM 语义判定
    - LLM 不可用/超时/异常：降级为精确匹配
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.graph = self._build()

    def invoke(self, state: dict) -> dict:
        return self.graph.invoke(state)

    def _build(self):
        graph = StateGraph(JudgeState)
        graph.add_node("judge_all", self._judge_all)
        graph.set_entry_point("judge_all")
        graph.add_edge("judge_all", END)
        return graph.compile()

    # ── 节点 ──

    def _judge_all(self, state: JudgeState) -> dict:
        answers = state["answers"]
        llm_tasks = []

        for i, ans in enumerate(answers):
            ans.setdefault("is_correct", False)
            ans.setdefault("reason", "未判定")

            given = ans.get("student_answer", "").strip()
            correct = ans.get("correct_answer", "").strip()

            if not given:
                ans["is_correct"] = False
                ans["reason"] = "未作答"
                ans["method"] = "rule"
                continue

            qtype = ans.get("question_type", "")

            if qtype == "choice":
                ok = _strip_label(given).upper() == _strip_label(correct).upper()
                ans["is_correct"] = ok
                ans["reason"] = "选项匹配" if ok else f"正确答案为 {correct}"
                ans["method"] = "rule"

            elif qtype in ("short_answer", "comprehensive"):
                llm_tasks.append((
                    i, given, correct,
                    ans.get("stem", ""),
                    ans.get("explanation", ""),
                ))

            else:  # fill_blank / code_fill / 其他
                ok = _normalize(given) == _normalize(correct)
                ans["is_correct"] = ok
                ans["reason"] = "答案匹配" if ok else f"正确答案为 {correct}"
                ans["method"] = "rule"

        if llm_tasks:
            logger.info(
                "judge_all: %d 道规则判定，%d 道 LLM 判定",
                len(answers) - len(llm_tasks), len(llm_tasks),
            )
            _run_llm_batch(answers, llm_tasks, self.llm_client)

        return {"answers": answers}


# ── LLM 并发 ──

def _run_llm_batch(answers, tasks, llm_client):
    """asyncio.gather + Semaphore 并发调 LLM，30s 超时，异常降级。"""
    if llm_client is None:
        for i, given, correct, _, _ in tasks:
            answers[i]["is_correct"] = (given == correct)
            answers[i]["reason"] = "降级（LLM 未配置）"
            answers[i]["method"] = "fallback"
        return

    async def _gather():
        sem = asyncio.Semaphore(LLM_CONCURRENCY)

        async def _one(i, given, correct, stem, expl):
            async with sem:
                try:
                    ok, reason = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: _call_llm(llm_client, stem, correct, given, expl),
                        ),
                        timeout=LLM_TIMEOUT,
                    )
                    answers[i]["is_correct"] = ok
                    answers[i]["reason"] = reason
                    answers[i]["method"] = "llm"
                except asyncio.TimeoutError:
                    answers[i]["is_correct"] = (given == correct)
                    answers[i]["reason"] = "降级（LLM 超时）"
                    answers[i]["method"] = "fallback"
                    logger.warning("LLM 超时: idx=%d", i)
                except Exception as e:
                    answers[i]["is_correct"] = (given == correct)
                    answers[i]["reason"] = "降级（LLM 异常）"
                    answers[i]["method"] = "fallback"
                    logger.warning("LLM 异常: idx=%d, err=%s", i, e)

        await asyncio.gather(*[_one(*t) for t in tasks])

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            t = threading.Thread(target=asyncio.run, args=(_gather(),))
            t.start()
            t.join()
        else:
            asyncio.run(_gather())
    except RuntimeError:
        for i, given, correct, _, _ in tasks:
            answers[i]["is_correct"] = (given == correct)
            answers[i]["reason"] = "降级（事件循环异常）"
            answers[i]["method"] = "fallback"
        logger.exception("LLM 批量判定事件循环异常")


def _call_llm(llm_client, stem, correct, given, expl):
    """单次 LLM 语义判定调用，返回 (is_correct, reason)。"""
    from langchain_core.messages import HumanMessage

    prompt = (
        f"题目：{stem}\n"
        f"参考答案：{correct}\n"
        f"学生答案：{given}\n"
        f"题解（供参考）：{expl}\n\n"
        f"学生答案在语义上是否正确？只回复 true 或 false，然后一句话理由。"
    )
    response = llm_client.invoke([HumanMessage(content=prompt)])
    text = response.content.strip()
    match = re.search(r'\b(true|false)\b', text, re.IGNORECASE)
    ok = match and match.group(1).lower() == "true"
    reason = re.sub(
        r'\btrue\b|\bfalse\b', '', text, flags=re.IGNORECASE
    ).strip(" ,.，。:")
    return ok, reason or "LLM 判定"


# ── 文本工具 ──

def _normalize(s: str) -> str:
    """全角→半角，空格压缩。"""
    s = s.strip().replace('　', ' ').replace('\xa0', ' ')
    s = ' '.join(s.split())
    result = []
    for c in s:
        if '０' <= c <= '９':
            result.append(chr(ord(c) - 0xFEE0))
        elif 'Ａ' <= c <= 'Ｚ':
            result.append(chr(ord(c) - 0xFEE0))
        elif 'ａ' <= c <= 'ｚ':
            result.append(chr(ord(c) - 0xFEE0))
        else:
            result.append(c)
    return ''.join(result)


def _strip_label(s: str) -> str:
    """去掉选项前标号，'A.' / 'A、' → 'A'。"""
    return re.sub(r'^[A-Da-d][.、．\s]+', '', s).strip()
