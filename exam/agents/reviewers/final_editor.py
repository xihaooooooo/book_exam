"""终审排版师：难度统计、排序、排版"""

DEFAULT_TYPE_ORDER = {"choice": 0, "fill_blank": 1, "short_answer": 2, "code_fill": 3, "comprehensive": 4}
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2}
TYPE_LABELS = {"choice": "选择题", "fill_blank": "填空题", "short_answer": "简答题", "code_fill": "代码填空题", "comprehensive": "综合题"}
CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八"]


def create_final_editor(config: dict = None):

    def final_editor_node(state):
        questions = state.get("all_questions", [])
        exam_plan = state.get("exam_plan") or {}
        toc = state.get("toc") or []

        if not questions:
            return {"final_exam": "# 试卷\n\n暂无题目生成。"}

        # 1. 难度统计
        stats = _difficulty_stats(questions, exam_plan)
        _print_stats(stats)

        # 题型排版顺序：优先用往年试卷的实际顺序，否则默认
        type_order = exam_plan.get("type_order") or DEFAULT_TYPE_ORDER

        # 2. 排序
        section_order = _build_section_order(toc)
        sorted_qs = sorted(questions, key=lambda q: _sort_key(q, section_order, type_order))

        # 3. 排版
        mode = state.get("mode", "exam")
        title = _infer_title(toc, sorted_qs, mode)
        final_exam = _format_exam(title, sorted_qs, type_order)

        return {
            "final_exam": final_exam,
        }

    return final_editor_node


def _build_section_order(toc: list) -> dict:
    """构建 section_id → 全局序号 的映射。"""
    order = {}
    idx = 0
    for ch in toc:
        for sec in ch.get("sections", []):
            order[sec["id"]] = idx
            idx += 1
    return order


def _sort_key(q: dict, section_order: dict, type_order: dict):
    source = q.get("source", "") or ""
    ch_num, sec_num = _parse_section(source)
    global_idx = section_order.get(source, 9999)
    type_rank = type_order.get(q.get("question_type", "short_answer"), 99)
    diff_rank = DIFF_ORDER.get(q.get("difficulty", "medium"), 1)
    return (global_idx, ch_num, sec_num, type_rank, diff_rank)


def _parse_section(source: str) -> tuple:
    """解析章节号，如 '2.1' → (2, 1)，解析失败返回 (9999, 9999)。"""
    try:
        parts = source.split(".")
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError):
        pass
    return (9999, 9999)


def _difficulty_stats(questions: list, exam_plan: dict) -> dict:
    """统计难度分布并与目标比例对比。"""
    total = len(questions)
    counts = {"easy": 0, "medium": 0, "hard": 0}
    for q in questions:
        d = q.get("difficulty", "medium")
        if d in counts:
            counts[d] += 1

    target_ratio = exam_plan.get("difficulty_ratio", (3, 4, 3))
    t_easy, t_mid, t_hard = target_ratio
    t_total = t_easy + t_mid + t_hard

    actual_pct = {
        "easy": round(counts["easy"] / total * 100) if total else 0,
        "medium": round(counts["medium"] / total * 100) if total else 0,
        "hard": round(counts["hard"] / total * 100) if total else 0,
    }
    target_pct = {
        "easy": round(t_easy / t_total * 100),
        "medium": round(t_mid / t_total * 100),
        "hard": round(t_hard / t_total * 100),
    }

    return {
        "total": total,
        "counts": counts,
        "actual_pct": actual_pct,
        "target_pct": target_pct,
    }


def _print_stats(stats: dict):
    """打印难度统计到控制台。"""
    print(f"\n[终审统计] 共 {stats['total']} 道题")
    print(f"  难度分布: 易 {stats['counts']['easy']} | 中 {stats['counts']['medium']} | 难 {stats['counts']['hard']}")
    print(f"  实际比例: 易 {stats['actual_pct']['easy']}% | 中 {stats['actual_pct']['medium']}% | 难 {stats['actual_pct']['hard']}%")
    print(f"  目标比例: 易 {stats['target_pct']['easy']}% | 中 {stats['target_pct']['medium']}% | 难 {stats['target_pct']['hard']}%")

    for level in ("easy", "medium", "hard"):
        diff = abs(stats["actual_pct"][level] - stats["target_pct"][level])
        if diff > 20:
            label = {"easy": "易", "medium": "中", "hard": "难"}[level]
            print(f"  ⚠ 难度偏差: {label} 实际 {stats['actual_pct'][level]}% vs 目标 {stats['target_pct'][level]}%（偏差 {diff}%）")


def _infer_title(toc: list, questions: list, mode: str = "exam") -> str:
    """从 TOC 和题目来源推断试卷标题。"""
    sources = set()
    for q in questions:
        src = q.get("source", "")
        if src:
            sources.add(src)

    # 模式后缀
    mode_suffix = {"diagnostic": "诊断测评卷", "practice": "定向练习卷"}.get(mode, "测试卷")

    if not sources:
        return mode_suffix

    # 找出涉及到的章节范围
    all_ids = []
    for ch in toc:
        for sec in ch.get("sections", []):
            all_ids.append((sec["id"], ch["chapter"]))

    matched_chapters = []
    for sid, ch_title in all_ids:
        if sid in sources:
            if ch_title not in matched_chapters:
                matched_chapters.append(ch_title)

    if matched_chapters:
        return f"{'、'.join(matched_chapters)} {mode_suffix}"

    return mode_suffix


def _format_exam(title: str, questions: list, type_order: dict) -> str:
    """排版为 Markdown 试卷。题型顺序按 type_order 动态排列。"""
    lines = [f"# {title}\n"]

    # 按 type_order 排序题型，只保留有题目的类型
    existing_types = {q.get("question_type", "") for q in questions}
    sorted_types = sorted(type_order.keys(), key=lambda t: type_order.get(t, 99))
    sorted_types = [t for t in sorted_types if t in existing_types]

    # 动态生成分组（一、二、三...）
    groups = []
    for i, qtype in enumerate(sorted_types):
        label = TYPE_LABELS.get(qtype, qtype)
        groups.append((f"{CN_NUM[i]}、{label}", qtype, questions))

    answer_lines = ["\n---\n", "# 参考答案\n"]
    global_num = 1

    for section_title, qtype, all_qs in groups:
        filtered = [q for q in all_qs if q.get("question_type") == qtype]
        if not filtered:
            continue

        lines.append(f"## {section_title}\n")

        answer_lines.append(f"## {section_title}\n")

        for q in filtered:
            lines.append(f"**{global_num}.** {q.get('stem', '')}")

            options = q.get("options") or []
            if options:
                for opt in options:
                    lines.append(f"  {opt}")
            lines.append("")

            # 答案
            answer_lines.append(f"**{global_num}.** {q.get('correct_answer', '')}")
            explanation = q.get("explanation", "")
            if explanation:
                answer_lines.append(f"  > {explanation}")
            answer_lines.append("")

            global_num += 1

    lines.extend(answer_lines)
    return "\n".join(lines)


