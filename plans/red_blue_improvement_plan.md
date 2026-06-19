# 红蓝对抗：系统改进计划

> 红方 34 条攻击 · 蓝方 39 条防守方案 · 2026-06-17

---

## 快速收益 Top 10

### 1. 试卷没有分值

**现象**：生成的 Markdown 试卷每道题后面没有 `（8分）` 标记，试卷开头也没有总分栏。做出来的卷子不像试卷。

**原因**：`final_editor.py` 的 `_format_exam` 只输出题干文本，不附加任何分值信息。所有题型默认同权，但实际考试中简答题和选择题分值差距很大。

**修法**：在 `_format_exam` 加一个 `SCORE_MAP` 字典（choice=2, fill_blank=3, code_fill=6, short_answer=8, comprehensive=12），每道题题干后追加 `（{score}分）`，试卷标题下加一行 `总分：{sum} 分 | 共 {n} 道题`。

**改动**：`final_editor.py`，~10 行。

---

### 2. 往年题型可能被静默丢弃

**现象**：analyze_exam.py 的 LLM 可能输出"程序填空题"、"问答题"等中文名，而 generate.py 的 `_normalize_type` 只认识 "填空"、"选择" 等关键词。匹配不到的题型走 default → `choice`，综合题被当成选择题，用户不被告知。

**原因**：`_normalize_type` 用 if-else 字符串包含匹配，没有兜底。往年报告里的题型枚举和系统内部枚举未强制同步。

**修法**：在 `_normalize_type` 最后一行 `return "choice"` 之前，加编辑距离模糊匹配——对不认识的题型字符串，从已知枚举中找距离最近的映射，并打印 `⚠ 题型 "xxx" 已映射为 "yyy"`。

**改动**：`chief_editor.py`，~5 行。

---

### 3. 空分析报告会导致 0 题

**现象**：用户 `--from-analysis report.json`，但 report 是空的（0 题目），Chief Editor 读到的 `total_questions=0`，规划的 `target_count=0`，最终生成 0 道题。用户不知道发生了什么。

**原因**：`exam_graph.py` 加载 report 后直接放入 state，没有检查 `total_questions` 是否有效。

**修法**：加载 report 后检查 `aggregated.total_questions == 0`，若为空则 `analysis_report = None` 并打印 warning，随后走无分析报告的默认路径（按教材自动出题）。

**改动**：`exam_graph.py` propagate 方法，~10 行。

---

### 4. LLM 调用失败不重试，重试无退避

**现象**：API 限频或临时故障时，LLM 调用直接失败，某个生成器挂掉，最终试卷缺题型。当前无重试逻辑。

**原因**：`invoke_structured` 和 `llm.invoke` 未加 retry 装饰器。

**修法**：在 `invoke_structured` 上加 `@tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))`——失败后 1 秒、2 秒、4 秒…最多 30 秒重试 3 次，API 瞬时限频基本能扛住。

**改动**：`agent_utils.py` 或 `structured.py`，~8 行。

---

### 5. 同一个知识点可能出多道题

**现象**：教材 2.1 节讲"任务状态转换"，主编可能给它分配 3 道题（选择+填空+简答），三道题考查同一段原文。考生的感受是"怎么三道题都在问同一个东西"。

**原因**：Chief Editor 规划 task 清单时只看 TOC 标题和关键词匹配，不做语义去重。Fan-out 并发分支之间互不知道对方出了什么题。

**修法**：在 Chief Editor 产出 tasks 后，按 `(section, topic 前 20 字)` 做 set 去重——同章节同知识点只保留一个 task。去重掉的打印 `⚠ 已去重：{section} {topic}`。

**改动**：`chief_editor.py` chief_editor_node，~5 行。

---

### 6. 质检只看格式，不看答案对不对

**现象**：Quality Reviewer 当前只检查题干是否清晰、选项是否合理、答案格式是否统一。**不验证答案是否正确**。LLM 生成的错误答案（幻觉）直接流入试卷。

**原因**：`quality_reviewer.py` 的 system prompt 没有要求对照原文验证。它也不知道教材原文是什么——state 里没有传 `section_raw_text`。

**修法**：在 Knowledge Extractor 阶段把 `section_raw_text` 写入 state，Quality Reviewer 的 prompt 加指令："请对照以下教材原文，验证答案是否正确。如原文无依据则 verdict=rejected。" rejected 的题目打印日志（或在试卷中标记为待人工复核）。

**改动**：`quality_reviewer.py` + `knowledge_extractor.py` + `agent_states.py`，~15 行。

---

### 7. 没有 Heading 的 DOCX 整份卷当成一道题

**现象**：如果往年试卷 DOCX 没用 Word 的 Heading 样式（所有段落都是 Normal），`parse_docx` 返回 `sections=[]`，下游 LLM 收到空白分组无题可拆。反过来如果正文全在外部（无 heading 但有段落），所有段落归入一个组，可能把全文当一道题。

**原因**：`docx_parser.py` 没处理 sections 为空的降级路径。

**修法**：`parse_docx` 末尾检查 `sections` 是否为空。若为空，把所有 Normal 段落按每 5 段一组切分为若干临时 section（title="题目组 1"、"题目组 2"…），保证 LLM 不会收到空数据。

**改动**：`docx_parser.py`，~8 行。

---

### 8. 试卷没有总分和题数概览

**现象**：试卷标题下直接就是第一题，没有 `总分：100分 | 共 20 道题` 这样的考试信息栏。排版不完整。

**原因**：`_format_exam` 的标题处理只加 `# {title}`，没有概览信息。

**修法**：标题后加一行概览栏，汇总总分、总题数、题型构成。

**改动**：`final_editor.py`，~3 行。

---

### 9. 并发生成没有限流，可能触发 API 限频

**现象**：`--count 20` 时 Fan-out 同时发起 20 条并发流水线，每条流水线至少调用 2 次 LLM（Knowledge Extractor + Generator + Reviewer），瞬时并发可能达到 60 个请求。DeepSeek 等 API 有 RPM 限制，触发后大量失败。

**原因**：`_fan_out_to_pipelines` 一次性返回所有 `Send`，LangGraph 并发执行无上限。

**修法**：将 `Send` 列表分批返回，每批最多 5 个。或在 LangGraph 外层用 `ThreadPoolExecutor(max_workers=5)` 包裹。

**改动**：`setup.py`，~10 行。

---

### 10. 生成器失败无声，试卷缺题型也不报

**现象**：某个题目生成器出错（LLM 返回格式错误、超时等），异常被 LangGraph 内部吞掉，最终试卷默默少了一道题。用户不知道有题没生成。

**原因**：各生成器节点未 try/except 收集错误信息，没有错误汇总机制。

**修法**：`AgentState` 加 `errors: list` 字段。各生成器节点 try/except，捕获异常写入 `state["errors"]`。`propagate` 末尾打印全部 errors。不用改流程控制，只加可见性。

**改动**：`agent_states.py` + `question_generator.py` + `exam_graph.py`，~8 行。

---

## 完整改进清单

### 题目质量

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 11 | 有些章节完全没覆盖 | 主编只看高频考点，冷门章节跳过 | 检测未覆盖的 section，自动补 choice/easy 保底题 | 中 |
| 12 | 难度全看 LLM 心情 | 没有客观标准，不同题难度标尺不一致 | 用规则（关键词+题型）重打分，与 LLM 评分加权 | 中 |
| 13 | 纯概念知识点被分配 code_fill | 主编 prompt 没约束题型-知识点匹配 | prompt 加"纯概念不分配 code_fill/comprehensive" | 低 |

### 边界场景

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 14 | 单章教材题型溢出 | 只有 1 章但按 5 种题型分配 | tasks < 5 时收缩为 choice+short_answer | 中 |
| 15 | 题目数超过知识点数 | --count 50 但只有 15 个知识点 | target_count > sections*3 时自动缩减 | 中 |
| 16 | 跨年混合时过时考点权重仍高 | 简单聚合不区分年份 | 按年份加权，每年衰减 0.15 | 低 |

### 上下文工程

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 17 | 消息清零切断上下文 | RemoveMessage 全清后 Knowledge Extractor 只看到锚点 | 保留末尾 2 条 non-tool 消息 | 中 |
| 18 | 生成器不回溯原文 | 只依赖 Knowledge Extractor 的提炼 | State 传 section_raw_text，生成器 prompt 尾部追加 | 中 |
| 19 | 产出远少于预期无预警 | --count 11 但只生成 4 题 | 产出 < 预期 50% 时回退 choice-only 重生成 | 中 |

### 数据一致性

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 20 | 题型枚举多处硬编码 | chief_editor / analyze_exam 各自定义题型 | 统一引用 schemas.py QuestionType | 中 |
| 21 | 难度标签中英混用 | 分析报告用中文，系统用英文 | stats.py 写入前统一 normalize | 中 |
| 22 | SQLite schema 升级后旧库不兼容 | 无版本号 | PRAGMA user_version + 启动检查 | 低 |

### 性能与成本

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 23 | 同章节被多个生成器重复发送 | Knowledge Extractor 读了原文，生成器没缓存 | AgentState 加 section_text_cache | 中 |
| 24 | 大 PDF 一次加载 OOM | parse.py 全文读入内存 | 逐页读取 + gc.collect() | 中 |
| 25 | 同教材+focus 重复出题无缓存 | 每次重新调 LLM | MD5(db+focus+types+count) 查缓存 | 中 |

### 用户体验

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 26 | --types choice --count 50 无警告 | 参数不与教材容量对照 | generate.py 加合理性检查 + warning | 中 |
| 27 | 等的时候不知道进度 | Fan-out 无声 | 每个质量审核完成后打印进度 | 中 |
| 28 | 中间产物全丢了 | exam_plan / knowledge_point 不持久化 | 存为 cache/plan_{ts}.json + trace_{ts}.jsonl | 低 |

### 试卷感

| # | 问题 | 原因 | 修法 | 改动量 |
|---|------|------|------|--------|
| 29 | 答案格式按题型不统一 | 选择题 A/B/C/D、填空题一个词、简答题一大段 | 每种题型答案加前缀格式化 | 中 |
| 30 | 排版缺少题型统计 | 直接分组不写概览 | 每个题型标题后加"（共5题，每题2分，小计10分）" | 中 |

---

## 分三批执行

**第一批**（改 3 个文件，~35 行）：#1~5 → 试卷感 + 数据安全 + 不重复出题

**第二批**（改 5 个文件，~60 行）：#6~10 → 幻觉防控 + 边界降级 + 错误可见

**第三批**（改 5 个文件，~100 行）：#11~30 → 覆盖面 + 性能 + 长期可维护性
