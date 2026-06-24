"""批量判题图。JudgeGraph 类，judge_all 单节点，asyncio.gather 并发 LLM。

用法：
    from exam.graph.judge_graph import JudgeGraph
    jg = JudgeGraph(llm_client)
    result = jg.invoke({
        "student_id": "S001",
        "answers": [{"question_type":"choice","student_answer":"C",...}, ...],
    })

判题 + 错因诊断：
- choice / fill_blank：文本规则判对错 → 答错时 LLM 诊断错因（独立 Semaphore(2) 隔离）
- short_answer / comprehensive / code_fill：LLM 语义判定 + 错因诊断合并（Semaphore(5)）
- LLM 不可用 / 超时 / 异常：降级精确匹配，不诊错因
"""

import asyncio
import json
import logging
import re
import threading
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from exam.agents.utils.agent_states import JudgeState

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 30
LLM_CONCURRENCY = 5
DIAGNOSIS_CONCURRENCY = 5

# ── 错因枚举 ──


class ErrorTypeEnum(str, Enum):
    concept_confusion = "concept_confusion"
    memory_gap = "memory_gap"
    reasoning_error = "reasoning_error"
    misread_question = "misread_question"
    careless = "careless"
    transfer_failure = "transfer_failure"


ERROR_TYPE_PROMPT_DESC = (
    "从以下6类中选择最匹配的错因：\n"
    "- concept_confusion：概念混淆（混淆了不同概念的定义或适用范围）\n"
    "- memory_gap：记忆缺失（完全不知道或遗漏关键事实）\n"
    "- reasoning_error：推理错误（逻辑链断裂、因果倒置）\n"
    "- misread_question：审题错误（答非所问、漏看关键限制条件）\n"
    "- careless：粗心失误（计算错误、漏写符号、看错选项字母）\n"
    "- transfer_failure：迁移失败（知道概念但不会应用到具体场景）"
)


# ── 结构化输出 ──


class JudgeResult(BaseModel):
    """LLM 判题/诊断的结构化输出。"""

    is_correct: bool = Field(description="答案是否正确，true或false")
    reason: str = Field(description="判定或诊断理由，一句话")
    error_type: Optional[ErrorTypeEnum] = Field(
        default=None,
        description="错因类型，仅 is_correct=false 时填写",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="诊断置信度 0-1，仅 is_correct=false 时填写",
    )
    evidence: Optional[str] = Field(
        default=None,
        description="诊断证据，一句话指出具体错误，仅 is_correct=false 时填写",
    )
    suggestion: Optional[str] = Field(
        default=None,
        description="改善建议，一句话，仅 is_correct=false 时填写",
    )


# ── 图定义 ──


class JudgeGraph:
    """批量判题 LangGraph 图。

    - choice / fill_blank：文本规则判对错，答错时 LLM 诊断错因
    - short_answer / comprehensive / code_fill：LLM 语义判定 + 错因诊断合并
    - LLM 不可用 / 超时 / 异常：降级为精确匹配，不诊错因
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
        # task tuple: (idx, given, correct, stem, expl, options_str, qtype, difficulty)
        llm_tasks = []
        diagnosis_tasks = []

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
            options_str = _format_options(ans.get("options", []))

            if qtype == "choice":
                ok = _strip_label(given).upper() == _strip_label(correct).upper()
                ans["is_correct"] = ok
                ans["reason"] = "选项匹配" if ok else f"正确答案为 {correct}"
                ans["method"] = "rule"
                if not ok:
                    diagnosis_tasks.append((
                        i, given, correct,
                        ans.get("stem", ""),
                        ans.get("explanation", ""),
                        options_str,
                        qtype,
                        ans.get("difficulty", ""),
                    ))

            elif qtype in ("short_answer", "comprehensive", "code_fill"):
                llm_tasks.append((
                    i, given, correct,
                    ans.get("stem", ""),
                    ans.get("explanation", ""),
                    options_str,
                    qtype,
                    ans.get("difficulty", ""),
                ))

            else:  # fill_blank / 其他
                ok = _normalize(given) == _normalize(correct)
                ans["is_correct"] = ok
                ans["reason"] = "答案匹配" if ok else f"正确答案为 {correct}"
                ans["method"] = "rule"
                if not ok:
                    diagnosis_tasks.append((
                        i, given, correct,
                        ans.get("stem", ""),
                        ans.get("explanation", ""),
                        options_str,
                        qtype,
                        ans.get("difficulty", ""),
                    ))

        if llm_tasks or diagnosis_tasks:
            logger.info(
                "judge_all: %d 道规则判定，%d 道 LLM 判题，%d 道 LLM 诊断",
                len(answers) - len(llm_tasks) - len(diagnosis_tasks),
                len(llm_tasks),
                len(diagnosis_tasks),
            )
            _run_llm_batch(answers, llm_tasks, diagnosis_tasks, self.llm_client)

        return {"answers": answers}


# ── LLM 调用 ──

def _build_judge_prompt(stem, correct, given, expl, options_str, qtype, difficulty):
    """构建判题 + 错因诊断合并 prompt。"""
    parts = [
        f"题型：{qtype}",
        f"难度：{difficulty}",
        f"题目：{stem}",
        f"参考答案：{correct}",
        f"学生答案：{given}",
        f"题解（供参考）：{expl}",
    ]
    if options_str:
        parts.append(f"选项：\n{options_str}")
    parts.append(
        "\n判断学生答案是否正确。如果正确，error_type/confidence/evidence/suggestion 均设为 null。"
        f"如果错误，{ERROR_TYPE_PROMPT_DESC}\n"
        "并给出 confidence（诊断置信度 0-1）、evidence（一句话诊断证据）、suggestion（一句话改善建议）。"
    )
    return "\n".join(parts)


def _build_diagnosis_prompt(stem, correct, given, expl, options_str, qtype):
    """构建纯错因诊断 prompt（已知答错）。"""
    parts = [
        f"题型：{qtype}",
        f"题目：{stem}",
        f"参考答案：{correct}",
        f"学生答案：{given}",
        f"题解（供参考）：{expl}",
    ]
    if options_str:
        parts.append(f"选项：\n{options_str}")
    parts.append(
        f"\n学生这道题答错了，请诊断错因。is_correct 固定为 false。"
        f"{ERROR_TYPE_PROMPT_DESC}\n"
        "并给出 confidence（诊断置信度 0-1）、evidence（一句话诊断证据）、suggestion（一句话改善建议）。"
    )
    return "\n".join(parts)


def _call_llm(llm_client, stem, correct, given, expl, options_str, qtype, difficulty):
    """LLM 判题 + 错因诊断合并，返回 JudgeResult。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = _build_judge_prompt(stem, correct, given, expl, options_str, qtype, difficulty)
    example_json = json.dumps(_make_example(JudgeResult), ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(content=(
            "你是一个大学期末考试的判题老师。直接输出 JSON，不要用 ``` 包裹，不要先写说明文字。\n"
            f"输出格式：{example_json}"
        )),
        HumanMessage(content=prompt),
    ]

    try:
        result = llm_client.invoke(messages)
        content = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        logger.warning("LLM 判题调用失败: %s", e)
        return JudgeResult(is_correct=(given == correct), reason="降级（LLM 异常）")

    # 1) 结构化解析
    try:
        return _parse_judge_json(content)
    except ValueError:
        pass

    # 2) regex 兜底
    try:
        return _regex_parse_judge_result(content, given, correct)
    except Exception:
        pass

    # 3) 最终降级
    logger.warning("无法解析 LLM 判题输出，降级精确匹配: %s", content[:200])
    return JudgeResult(is_correct=(given == correct), reason="降级（解析失败）")


def _diagnose_error_llm(llm_client, stem, correct, given, expl, options_str, qtype):
    """纯错因诊断 LLM 调用（已知答错），返回 JudgeResult。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = _build_diagnosis_prompt(stem, correct, given, expl, options_str, qtype)
    example_json = json.dumps(_make_example(JudgeResult), ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(content=(
            "你是一个大学期末考试的判题老师。直接输出 JSON，不要用 ``` 包裹，不要先写说明文字。\n"
            f"输出格式：{example_json}"
        )),
        HumanMessage(content=prompt),
    ]

    try:
        result = llm_client.invoke(messages)
        content = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        logger.warning("LLM 诊断调用失败: %s", e)
        return JudgeResult(is_correct=False, reason="降级（LLM 异常）")

    # 1) 结构化解析
    try:
        parsed = _parse_judge_json(content)
        parsed.is_correct = False  # 强制 false，已知答错
        return parsed
    except ValueError:
        pass

    # 2) regex 兜底
    try:
        parsed = _regex_parse_judge_result(content, given, correct)
        parsed.is_correct = False
        return parsed
    except Exception:
        pass

    # 3) 最终降级 — 无错因标签
    logger.warning("无法解析 LLM 诊断输出: %s", content[:200])
    return JudgeResult(is_correct=False, reason="降级（解析失败）")


# ── JSON 解析 ──


def _parse_judge_json(content: str) -> JudgeResult:
    """从 LLM 输出中解析 JudgeResult，失败抛 ValueError。"""
    text = content.strip()

    # 直接解析
    try:
        return JudgeResult(**json.loads(text, strict=False))
    except (json.JSONDecodeError, Exception):
        pass

    # 提取 JSON 代码块
    for marker in ["```json", "```"]:
        if marker in text:
            parts = text.split(marker, 1)
            if len(parts) > 1:
                json_str = parts[1].split("```", 1)[0].strip()
                try:
                    return JudgeResult(**json.loads(json_str, strict=False))
                except (json.JSONDecodeError, Exception):
                    pass

    raise ValueError(f"无法解析 JSON: {text[:300]}")


def _regex_parse_judge_result(text: str, given: str, correct: str) -> JudgeResult:
    """Regex 兜底：从非结构化文本提取判题/诊断字段。"""
    match = re.search(r"\b(true|false)\b", text, re.IGNORECASE)
    is_correct = match and match.group(1).lower() == "true"

    reason = re.sub(r"\btrue\b|\bfalse\b", "", text, flags=re.IGNORECASE).strip(" ,.，。:\"'\n")
    if not reason:
        reason = "LLM 判定" if is_correct else f"正确答案为 {correct}"

    error_type = None
    confidence = None
    evidence = None
    suggestion = None

    if not is_correct:
        for etype in ErrorTypeEnum:
            if etype.value in text.lower():
                error_type = etype
                break

        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        if conf_match:
            try:
                confidence = float(conf_match.group(1))
            except ValueError:
                pass

        ev_match = re.search(r'"evidence"\s*:\s*"([^"]+)"', text)
        if ev_match:
            evidence = ev_match.group(1)[:500]

        sug_match = re.search(r'"suggestion"\s*:\s*"([^"]+)"', text)
        if sug_match:
            suggestion = sug_match.group(1)[:500]

    return JudgeResult(
        is_correct=is_correct,
        reason=reason[:200],
        error_type=error_type,
        confidence=confidence,
        evidence=evidence,
        suggestion=suggestion,
    )


# ── 并发执行 ──


def _run_llm_batch(answers, llm_tasks, diagnosis_tasks, llm_client):
    """asyncio.gather + 双 Semaphore 并发调 LLM。
    llm_tasks（判题+诊断）：Semaphore(5)，30s 超时
    diagnosis_tasks（仅诊断）：Semaphore(2)，30s 超时，1 次重试
    """
    if llm_client is None:
        for i, given, correct, stem, expl, options_str, qtype, difficulty in llm_tasks:
            answers[i]["is_correct"] = (given == correct)
            answers[i]["reason"] = "降级（LLM 未配置）"
            answers[i]["method"] = "fallback"
        # diagnosis_tasks: 无 LLM 则跳过诊断
        return

    async def _gather():
        judge_sem = asyncio.Semaphore(LLM_CONCURRENCY)
        diagnosis_sem = asyncio.Semaphore(DIAGNOSIS_CONCURRENCY)

        async def _judge_one(task):
            i, given, correct, stem, expl, options_str, qtype, difficulty = task
            async with judge_sem:
                try:
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: _call_llm(
                                llm_client, stem, correct, given, expl,
                                options_str, qtype, difficulty,
                            ),
                        ),
                        timeout=LLM_TIMEOUT,
                    )
                    answers[i]["is_correct"] = result.is_correct
                    answers[i]["reason"] = result.reason
                    answers[i]["method"] = "llm"
                    _fill_error_fields(answers[i], result)
                except asyncio.TimeoutError:
                    answers[i]["is_correct"] = (given == correct)
                    answers[i]["reason"] = "降级（LLM 超时）"
                    answers[i]["method"] = "fallback"
                    logger.warning("LLM 判题超时: idx=%d", i)
                except Exception as e:
                    answers[i]["is_correct"] = (given == correct)
                    answers[i]["reason"] = "降级（LLM 异常）"
                    answers[i]["method"] = "fallback"
                    logger.warning("LLM 判题异常: idx=%d, err=%s", i, e)

        async def _diagnose_one(task):
            i, given, correct, stem, expl, options_str, qtype, difficulty = task
            async with diagnosis_sem:
                for attempt in range(2):
                    try:
                        result = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: _diagnose_error_llm(
                                    llm_client, stem, correct, given, expl,
                                    options_str, qtype,
                                ),
                            ),
                            timeout=LLM_TIMEOUT,
                        )
                        _fill_error_fields(answers[i], result)
                        answers[i]["method"] = _method_combine(
                            answers[i].get("method", "rule"), "llm",
                        )
                        return  # 成功
                    except asyncio.TimeoutError:
                        if attempt == 0:
                            await asyncio.sleep(1)
                        else:
                            logger.warning("LLM 诊断超时（重试后仍失败）: idx=%d", i)
                    except Exception as e:
                        if attempt == 0:
                            await asyncio.sleep(1)
                        else:
                            logger.warning("LLM 诊断异常（重试后仍失败）: idx=%d, err=%s", i, e)

        await asyncio.gather(
            *[_judge_one(t) for t in llm_tasks],
            *[_diagnose_one(t) for t in diagnosis_tasks],
        )

    try:
        # Python 3.10+：get_event_loop() 已废弃，改用 get_running_loop()
        try:
            asyncio.get_running_loop()
            # 有正在运行的事件循环 → 在新线程中执行
            t = threading.Thread(target=asyncio.run, args=(_gather(),))
            t.start()
            t.join()
        except RuntimeError:
            # 没有运行中的事件循环 → 直接 run
            asyncio.run(_gather())
    except Exception:
        for i, given, correct, stem, expl, options_str, qtype, difficulty in llm_tasks:
            answers[i]["is_correct"] = (given == correct)
            answers[i]["reason"] = "降级（事件循环异常）"
            answers[i]["method"] = "fallback"
        logger.exception("LLM 批量判定事件循环异常")


# ── 辅助函数 ──


def _fill_error_fields(ans_dict: dict, result: JudgeResult):
    """将 JudgeResult 的错因字段写入 answers[i]（仅当答错且有错因时）。"""
    if not result.is_correct and result.error_type:
        ans_dict["error_type"] = (
            result.error_type.value
            if isinstance(result.error_type, ErrorTypeEnum)
            else str(result.error_type)
        )
        ans_dict["error_evidence"] = (result.evidence or "")[:500]
        ans_dict["error_suggestion"] = (result.suggestion or "")[:500]
        if result.confidence is not None:
            ans_dict["diagnosis_confidence"] = result.confidence


def _method_combine(base: str, new: str) -> str:
    """合并 method 字段，"rule" + "llm" → "rule+llm"。"""
    if base and base != new:
        return f"{base}+{new}"
    return new


def _format_options(options: list) -> str:
    """格式化选项列表为 prompt 文本。"""
    if not options:
        return ""
    lines = []
    for opt in options:
        if isinstance(opt, dict):
            label = opt.get("label", "")
            text = opt.get("text", "")
            lines.append(f"{label}. {text}" if label else text)
        elif isinstance(opt, str):
            lines.append(opt)
    return "\n".join(lines)


# ── 文本工具 ──


# Unicode 符号 → ASCII 映射（补全全角标点和数学符号）
_UNICODE_NORMALIZE_MAP = {
    "×": "x", "÷": "/", "≥": ">=", "≤": "<=", "≠": "!=",
    "≈": "~=", "→": "->", "←": "<-", "⇒": "=>", "⇐": "<=",
    "；": ";", "：": ":", "，": ",", "。": ".", "！": "!",
    "？": "?", "（": "(", "）": ")", "【": "[", "】": "]",
    "《": "<", "》": ">", "＂": '"', "＇": "'",
}


def _normalize(s: str) -> str:
    """全角→半角，空格压缩，常见 Unicode 符号统一。"""
    s = s.strip().replace("　", " ").replace("\xa0", " ")
    s = " ".join(s.split())
    for k, v in _UNICODE_NORMALIZE_MAP.items():
        s = s.replace(k, v)
    result = []
    for c in s:
        if "０" <= c <= "９":   # ０-９
            result.append(chr(ord(c) - 0xFEE0))
        elif "Ａ" <= c <= "Ｚ":  # Ａ-Ｚ
            result.append(chr(ord(c) - 0xFEE0))
        elif "ａ" <= c <= "ｚ":  # ａ-ｚ
            result.append(chr(ord(c) - 0xFEE0))
        else:
            result.append(c)
    return "".join(result)


def _strip_label(s: str) -> str:
    """去掉选项前标号：'(A)' / 'A.' / '①' / 'A、' → 'A'，支持多字母 AB/ACD。"""
    return re.sub(
        r"^[\(（\[【]?[A-Da-d一二三四]+[\)）\]】、.．\s]+", "", s,
    ).strip()


def _make_example(schema_cls) -> dict:
    """生成示例 JSON，原生类型用类型匹配的示例值。"""
    from pydantic import BaseModel
    import typing

    example = {}
    for field_name, field_info in schema_cls.model_fields.items():
        annotation = field_info.annotation
        # list 类型
        if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
            args = typing.get_args(annotation)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                example[field_name] = [_make_example(args[0])]
            else:
                example[field_name] = [f"<{field_info.description or field_name}>"]
        # 嵌套 BaseModel
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            example[field_name] = _make_example(annotation)
        # bool: 用 false 而非字符串占位符
        elif annotation is bool:
            example[field_name] = False
        # int
        elif annotation is int:
            example[field_name] = 0
        # float
        elif annotation is float:
            example[field_name] = 0.0
        else:
            example[field_name] = f"<{field_info.description or field_name}>"
    return example
