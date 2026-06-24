# 往年试卷分析功能 实现计划

## 背景

现有系统能从教材 PDF 出题，但缺少对**往年真题**的分析能力。用户希望分析 DOCX 格式的历年试卷，提取：
- 考点频率（各知识点/章节考了多少次）
- 题型分布（选择/填空/简答/代码/综合题的比例）
- 难度评估（每道题的难度标签）

分析结果先作为独立 JSON 输出，后续可喂给出题系统做精准组卷。

## 试卷格式（已验证）

以 `25年春嵌入式操作系统（回忆版）.docx` 为例：

```
[Heading 1]  25年春 嵌入式操作系统（回忆版）
[Heading 2]  填空题（5道*10分）
[Normal]     题1文本...
[Normal]     题2文本...
[Heading 2]  代码填空题（4小题*5空*1分）
[Normal]     题1...
[Normal]     题2...
[Heading 2]  综合题（10+20）
[Normal]     题1（含大段代码）...
```

**规律**：
- **Heading 1** = 试卷标题
- **Heading 2** = 大题分组（含题型名和分值信息）
- **Normal** = 题目内容，同一分组的连续 Normal 段落各自是一道题
- 代码类题目会跨多个 Normal 段落

## 架构

```
analyze_exam.py --dir ./papers/ [--output ./analysis/]
    │
    ├── 模块1: DOCX 解析器 (exam/parsers/docx_parser.py)
    │     遍历目录, python-docx 提取段落 → 按 Heading 分组
    │     输出: [{ exam_title, sections: [{ section_title, questions: ["题文1", "题文2"] }] }]
    │
    ├── 模块2: LLM 分析器 (exam/analyzers/llm_analyzer.py)  
    │     输入: 一道题文本 + 分组标题
    │     输出: 结构化分类
    │     复用: exam.agents.utils.agent_utils.create_llm_client
    │     复用: exam.agents.utils.structured.invoke_structured
    │
    └── 模块3: 统计报告器 (exam/analyzers/stats.py)
          汇总所有分类结果 → JSON + Markdown 双输出
```

### 粗切策略：利用 DOCX Heading 2

1. **粗切**：Heading 2 → 下一 Heading 2 之间的 Normal 段落归为一组
2. **LLM 细拆 + 分类**：每组文本 + 分组标题一起给 LLM，一次性拆成单题 + 分类

### LLM Prompt 设计

```
你是一位试卷分析专家。下面是一份试卷中"{section_title}"部分的全部题目文本。
请将此部分拆分为单道题目，并对每道题进行以下分析：

1. question_type: choice / fill_blank / short_answer / code_fill / comprehensive
2. difficulty: easy / medium / hard
3. topic: 知识点所属章节或领域
4. knowledge_points: 具体考查的知识点列表

输出每道题的完整题干文本和分析结果。
```

### 输出格式

`analysis/report.json`:
```json
{
  "exams": [
    {
      "title": "25年春嵌入式操作系统（回忆版）",
      "question_count": 11,
      "type_distribution": { "fill_blank": 5, "code_fill": 4, "comprehensive": 2 },
      "difficulty_distribution": { "easy": 3, "medium": 6, "hard": 2 },
      "topic_frequency": { "任务管理": 4, "中断处理": 2, "信号量": 3 },
      "questions": [{ "stem": "...", "question_type": "fill_blank", ... }]
    }
  ],
  "aggregated": { "total_questions": 11, ... }
}
```

`analysis/report.md`: 人类可读的 Markdown 统计概览。

## 文件清单

| 文件 | 作用 | 状态 |
|------|------|------|
| `exam/parsers/__init__.py` | 解析器包 | 新建 |
| `exam/parsers/docx_parser.py` | DOCX → 分组文本 | 新建 |
| `exam/analyzers/__init__.py` | 分析器包 | 新建 |
| `exam/analyzers/llm_analyzer.py` | LLM 拆分 + 分类 | 新建 |
| `exam/analyzers/schemas.py` | 分析结果 Pydantic Schema | 新建 |
| `exam/analyzers/stats.py` | 汇总统计 + 报告生成 | 新建 |
| `analyze_exam.py` | CLI 入口 | 新建 |

## 与现有系统的复用

| 复用项 | 来源 |
|--------|------|
| `create_llm_client(config)` | `exam.agents.utils.agent_utils` |
| `invoke_structured(llm, schema, messages)` | `exam.agents.utils.structured` |
| `DEFAULT_CONFIG` | `exam.config` |

不修改出题流水线的任何文件。

## CLI

```bash
python analyze_exam.py --file "试卷.docx"          # 单文件
python analyze_exam.py --dir ./papers/             # 整个目录
python analyze_exam.py --dir ./papers/ --output ./analysis/  # 指定输出
```
