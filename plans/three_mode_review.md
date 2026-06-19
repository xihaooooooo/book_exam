# 红蓝对抗：三模式扩展计划审查

> 红方 25 条攻击 · 蓝方 19 条防守 · 2026-06-18

---

## 核心结论

**plan 的方向正确，但"引擎完全不动"需要修正为"引擎改 ~15 行 prompt"**。

plan 说"三种模式的区别只是给引擎喂不同的输入"，但实际上"策略"（全覆盖/弱点多出/均匀覆盖）内化在 Chief Editor 的 prompt 里，只靠传 `--focus`/`--count`/`--types` 改变不了选题偏好。prompt 必须加一段模式感知的策略指令（~8 行），其余的图结构、路由、生成器、质检器确实可以不动。

---

## 最致命的 3 条

### 1. "引擎不动"在 prompt 层面不成立

**红方 1.1 / 蓝方 #1, #6, #11**：plan 的架构图标了三种策略（全覆盖/弱点多出/均匀覆盖），但 `chief_editor.py:153-195` 的 prompt 只有一套逻辑——永远是"优先覆盖核心概念，跳过概述/小结"。diagnostic 要"均匀覆盖每章"、practice 要"弱点多出"，但这两条语义在当前 prompt 里不存在。

**修法**：`chief_editor.py` system_message 拼接段前加策略指令（从 state 读 mode），diagnostic 时注入"均匀覆盖所有章节不跳过概述"，practice 时注入"弱点章节优先但其余章节仍需扫一遍"。

**改动**：`chief_editor.py` ~8 行 + `agent_states.py` 加 `mode` 字段 1 行。

### 2. Practice 用 topic 文本搜章节，不可靠

**红方 1.2, 3.1 / 蓝方 #2**：plan 写 `focus = ",".join(w["topic"] for w in weak[:8])`。topic 是自由文本（如"不理解优先级反转的概念"），`search_keyword` 做的是 FTS5 全文搜索——表述不一致就搜不到。搜不到时 prompt 写"扩展到相邻章节"，反而稀释了练习聚焦。`get_weak_sections` 本身返回了 `section_id`，但在映射链中被丢弃了。

**修法**：focus 改为传 `section:2.1,section:3.4` 格式，在 `focus_instruction` 中检测到 `section:` 前缀时跳过 search_keyword，直接用对应章节。

**改动**：`chief_editor.py` focus_instruction 加 ~5 行检测 + `derive_params` 用 section_id 拼 focus ~2 行。

### 3. Diagnostic 题数无上限，成本不可控

**红方 2.3 / 蓝方 #4**：40 章教材 → `chapter_count * 2` = 80 道选择题，每道 3 次 LLM 调用 = 240 次调用。"初次摸底"花几百次 LLM 调用不合理。

**修法**：`count = min(chapter_count * 2, 30)`。

**改动**：`derive_params` 1 行。

---

## 快速收益 Top 10

| # | 问题 | 原因 | 修法 | 改动 |
|---|------|------|------|------|
| 1 | 三种策略名不副实 | prompt 只有一套选题逻辑，不知道 mode | chief_editor.py 加策略指令段 | 8 行 |
| 2 | diagnostic 成本爆炸 | 题数 = 章数 × 2，无上限 | derive_params 加 `min(..., 30)` | 1 行 |
| 3 | practice 聚焦不准 | focus 用 topic 文本搜，不用 section_id | focus 改传 `section:2.1` 格式 + prompt 检测 | 7 行 |
| 4 | 参数冲突无声 | --mode diagnostic --types short_answer 不报错 | generate.py 加冲突检测 + 警告 | 8 行 |
| 5 | mode + --from-analysis 矛盾 | diagnostic 也加载往年分析注入 prompt | generate.py 按 mode 过滤 analysis | 6 行 |
| 6 | 输出不标模式 | 试卷标题永远叫"XX 测试卷" | AgentState 加 mode → final_editor 读 mode 调标题 | 5 行 |
| 7 | 空错题库静默退化 | get_weak_sections 返回 []，focus="" | 检测空结果 → 降级 exam 模式 + 警告 | 5 行 |
| 8 | section_id 映射链断裂 | get_weak_sections 有 section_id 但被丢弃 | focus 直接用 section_id（同 #3，已覆盖） | — |
| 9 | practice 未避免重复出题 | 不检查学生做过的题 | 传 excluded_stems → generator prompt 加去重提示 | 6 行 |
| 10 | target_count 非硬约束 | LLM 可能出多或出少 | 产出 != target 时打印 warning（v1 不做强制裁剪） | 2 行 |

---

## 完整改进清单

### 功能正确性

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 1 | 1.1 | prompt 无策略切换 | 成立，必须修 | chief_editor.py 加策略指令段 | 8 行 |
| 2 | 1.2 | practice focus 用 topic 搜不可靠 | 成立，必须修 | focus 改 section: 前缀 | 7 行 |
| 3 | 1.3 | diagnostic 题型限制只靠 prompt 无强制 | 成立，可接受 | v1 不做路由层校验（LLM 大概率遵守） | 0 |
| 4 | 1.4 | target_count 非硬约束 | 成立，可接受 | 产出偏差时打印 warning | 2 行 |
| 5 | 1.5 | focus 语义混用（锁定 vs 重点） | 成立，需修 | practice 加 focus_mode 参数区分语义 | 含在 #1 |
| 6 | 1.6 | get_weak_sections 未注册为工具 | 成立，可接受 | v1 只用 CLI 层注入，v2 注册为工具 | 0 |

### 边界场景

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 7 | 2.1 | 空错题库静默退化 | 成立，需修 | 降级 exam + 警告 | 5 行 |
| 8 | 2.2 | 1 章教材 diagnostic 只出 2 题 | 成立，小问题 | 加 min_count=6 | 1 行 |
| 9 | 2.3 | 大错题量 count 膨胀 | 成立，需修 | count cap + 用 `len(weak)` 替代 `sum(error_count)` | 2 行 |
| 10 | 2.4 | 不存在的 student_id 无校验 | 成立，小问题 | SQL 查 students 表验证 | 3 行 |
| 11 | 2.5 | diagnostic + --from-analysis 不忽略 | 成立，需修 | 按 mode 过滤 analysis（同 Top10 #5） | 6 行 |
| 12 | 2.6 | user_focus 被 mode 推导覆盖 | 成立，需修 | 明确优先级：用户显式 > mode 推导 > 默认 | 3 行 |

### 数据一致性

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 13 | 3.1 | section_id 映射链断裂 | 成立（同 #2） | 已覆盖 | — |
| 14 | 3.3 | _normalize_type 静默 fallback choice | 成立，可接受 | v1 不改，fallback 到 choice 在 diagnostic 下反而安全 | 0 |
| 15 | 3.4 | _normalize_difficulty 文本匹配脆弱 | 成立，可接受 | 换模型时注意测试 | 0 |

### 性能与成本

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 16 | 4.1 | diagnostic 用昂贵模型 | 成立，可接受 | v2 用 quick_think_llm | 0 |
| 17 | 4.2 | knowledge_extractor 无缓存 | 成立，可接受 | v2 加 section_text_cache | 0 |
| 18 | 4.3 | fan-out 无并发限流 | 成立，可接受 | v2 分批 Send（与三模式独立的问题） | 0 |

### 用户体验

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 19 | 5.1 | 无 mode 进度反馈 | 成立，小问题 | generate.py 加 mode-aware 日志 | 4 行 |
| 20 | 5.4 | 题数推导对用户不透明 | 成立，小问题 | 打印推导过程 | 3 行 |
| 21 | — | 输出标题不标模式 | 蓝方 #9 | final_editor 读 mode 调标题 | 5 行 |
| 22 | — | practice 未利用错题细节 | 蓝方 #3 | v2 传 mistake_samples 到 knowledge_extractor | 15 行 |

### 可维护性

| # | 红方 | 问题 | 蓝方判断 | 修法 | 改动 |
|---|------|------|---------|------|------|
| 23 | 6.1 | plan 与 feasibility 文件矛盾 | 成立 | 本次审查即解决矛盾 | — |
| 24 | 6.2 | mistakes 表无唯一约束 | 成立，小问题 | 加 UNIQUE(student_id, exam_title, stem) | 1 行 |
| 25 | 6.4 | 硬编码魔法数字（top 8, ×2） | 成立，小问题 | 改为常量 + 注释 | 2 行 |

---

## 分批执行

### 第一批：打通最小链路（~50 行引擎改动 + ~80 行新增）

| 做啥 | 涉及文件 | 行数 |
|------|---------|------|
| AgentState 加 mode 字段 | `agent_states.py` | 1 |
| Chief Editor 加策略指令段 | `chief_editor.py` | 8 |
| focus_instruction 加 section: 前缀检测 | `chief_editor.py` | 5 |
| focus_instruction 加 focus_mode 区分锁定/重点 | `chief_editor.py` | 3 |
| final_editor 读 mode 调标题 | `final_editor.py` | 5 |
| CLI --mode + --student + derive_params | `generate.py` | 60 |
| get_weak_sections + init_mistakes_db | `agent_utils.py` | 40 |
| mistakes 表 DDL | SQL | 15 |
| record_mistake.py（含批量导入） | 新文件 | 60 |

**第一批做完 = exam 保持现有行为，practice 可用错题库出定向练习卷。**

### 第二批：鲁棒 + 体验（~40 行）

| 做啥 | 涉及文件 | 行数 |
|------|---------|------|
| 参数冲突检测 + 警告 | `generate.py` | 8 |
| mode-aware 进度日志 | `generate.py` | 4 |
| 空错题库降级 exam | `generate.py` | 5 |
| diagnostic count hard cap + min_count | `generate.py` | 2 |
| practice count 改用 len(weak) 替代 sum(error_count) | `generate.py` | 2 |
| mistakes 表 UNIQUE 约束 | SQL | 1 |
| 硬编码常量提取 | `generate.py` | 2 |
| student_id 存在性校验 | `agent_utils.py` | 3 |
| init_mistakes_db 在启动时自动调用 | `generate.py` | 1 |
| diagnostic 策略指令：不跳过概述章节 | `chief_editor.py` | 含在第一批 |

### 第三批：精准 + 性能（~50 行）

| 做啥 | 涉及文件 | 行数 |
|------|---------|------|
| practice 传 mistake_samples 到 knowledge_extractor | `generate.py` + `knowledge_extractor.py` | 20 |
| practice 传 excluded_stems 防重复 | `generate.py` + `knowledge_extractor.py` | 6 |
| diagnostic 用 quick_think_llm | `agent_utils.py` | 5 |
| knowledge_extractor 加 section_text_cache | `agent_states.py` + `setup.py` | 10 |
| fan-out 分批限流 | `setup.py` | 10 |

---

## 引擎改动清单（全部在第一批）

| 文件 | 改什么 | 行数 |
|------|--------|------|
| `agent_states.py` | 加 `mode: str` 字段 | 1 |
| `chief_editor.py` | 加策略指令段 + section: 前缀检测 + focus_mode | ~16 |
| `final_editor.py` | 读 mode 调标题 | 5 |
| **合计** | | **~22 行** |

其余引擎文件不动：`setup.py`、`conditional_logic.py`、`question_generator.py`、`quality_reviewer.py`、`knowledge_extractor.py`、`schemas.py`、`exam_graph.py`。
