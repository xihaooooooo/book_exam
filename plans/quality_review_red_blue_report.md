# 质检重试闭环 — 红蓝对抗审查报告

> 审查日期：2026-06-19
> 红方：攻击者视角（Explore agent）
> 蓝方：防守者视角（general-purpose agent）

## 审查对象

`plans/quality_review_retry_plan.md` — 质检重试闭环方案

## 总体结论

方案方向正确，但有一个**致命缺陷**和一个**关键遗漏**需要优先解决。修复后在 4-5 个文件中改动约 80-100 行代码，不改变原有架构。

---

## 致命发现：`_keep_first` reducer 让重试机制完全废掉

**现象：** 第一次生成后若审稿 fail，生成器重做产生新题，但新题被 `_keep_first` 静默丢弃，审稿员永远审同一道旧题。

**根因：** `agent_states.py` 第 31 行 `generated_question: Annotated[dict | None, _keep_first]`，逻辑是 `return a if a else b`，旧值非空就保留旧值。

**修法：** 去掉 `_keep_first`，改为无 reducer（子图内串行写入不存在并发冲突）。改动 1 行。

---

## Quick Wins Top 10

| # | 问题 | 修法 | 文件 | 行数 |
|---|------|------|------|------|
| 1 | `_keep_first` 丢弃新题 | 去掉 reducer | agent_states.py | 1 |
| 2 | prompt 仍为 3 档含 fixed | 改为两档 pass/fail，删 schema 中修正字段 | quality_reviewer.py + schemas.py | 30 |
| 3 | all_questions 无条件写入 | 只在 verdict=pass 时写 all_questions | quality_reviewer.py | 5 |
| 4 | generated_question 为空时静默降级 | 加 None/空守卫，直接返回 fail | quality_reviewer.py | 5 |
| 5 | invoke_structured 解析崩溃崩全场 | 节点内 try/except 兜底 | question_generator.py + quality_reviewer.py | 20-30 |
| 6 | issues 为空时 fallback 缺失 | QualityReview 加 model_validator | schemas.py | 6 |
| 7 | 重试时 messages 累积 token 爆炸 | fail 分支加 `"messages": []` | quality_reviewer.py | 1 |
| 8 | 重试过程完全黑盒 | quality_reviewer + 生成器加 print 日志 | quality_reviewer.py + question_generator.py | 10-15 |
| 9 | Markdown 不显示 review_status | _format_exam 加 status_tag | final_editor.py | 4 |
| 10 | 5 个生成器 feedback 逻辑重复 | 提取 _maybe_inject_feedback 公共函数 | question_generator.py | 20 |

---

## 完整改进清单

### 模块 1：State（agent_states.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| `_keep_first` 丢弃新题 | 去掉 `_keep_first`，无 reducer | 1 |
| 缺 retry_count / review_feedback 字段 | 声明两个新字段，不加 reducer | 2 |
| 缺 review_status 字段 | 题目 dict 中新增 `review_status` 键 | 嵌入在各节点中 |
| reducer 假设无显式保障 | 加注释说明隔离策略 | 1 |

### 模块 2：Schema（schemas.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| QualityReview 含修正字段 | 删除 stem/option_a~d/correct_answer/explanation | 7 |
| verdict 为 3 值 | 改为 `Literal["pass", "fail"]` | 1 |
| issues 为空时无 fallback | 加 `@model_validator` 非空兜底 | 6 |

### 模块 3：质检审核员（quality_reviewer.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| prompt 含 fixed 指令 | 改为两档制纯裁判 prompt | 15 |
| 无条件写入 all_questions | pass 写 all_questions，fail 只写 feedback | 10 |
| 不读 verdict | 根据 verdict 分支处理 | 同上 |
| generated_question 为空 | 加 None/空守卫 | 5 |
| 无异常兜底 | try/except invoke_structured | 5 |
| 无日志 | pass/fail/force_pass 时 print | 6 |

### 模块 4：生成器（question_generator.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| 5 个生成器重复 feedback 逻辑 | 提取 _maybe_inject_feedback 公共函数 | 15 |
| 无异常兜底 | try/except invoke_structured | 5×5 |
| 无重试日志 | 读取 retry_count + feedback 时 print | 5 |

### 模块 5：图结构（setup.py + conditional_logic.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| quality_reviewer → END 直连 | 改为条件边：pass→END, fail→生成器 | 20 |
| Send payload 缺字段 | payload 加 retry_count + review_feedback | 1 |
| 重试路由需新建函数 | 复用 ConditionalLogic.route_by_question_type | 0 |
| 新增 route_after_review | 根据 retry_count + feedback 判断继续/结束 | 15 |

### 模块 6：终审排版（final_editor.py）

| 问题 | 修法 | 行数 |
|------|------|------|
| review_status 不渲染 | _format_exam 加 status_tag | 4 |

---

## 分批执行建议

### 第一批（必做，约 50 行）

解决致命缺陷和方案核心逻辑，不修则方案不成立：

1. `_keep_first` → 无 reducer
2. 加 retry_count / review_feedback 字段
3. 改 QualityReview schema 为两档 + validator
4. quality_reviewer 改为分支逻辑（pass 入库 / fail 记 feedback）
5. 子图加条件边 `route_after_review`

### 第二批（应做，约 30 行）

边界安全和用户体验：

6. generated_question 空守卫
7. invoke_structured try/except
8. fail 时清 messages
9. 重试日志 print

### 第三批（建议做，约 20 行）

工程优化：

10. 提取 _maybe_inject_feedback 公共函数
11. _format_exam 加 status_tag
12. Send payload 显式传 retry_count

---

## 不改的部分

- 主编排 `exam_graph.py` 不变
- knowledge_extractor 不变
- 外部接口不变
- Send 并发机制不变

---

## 预估总改动量

| 文件 | 改动行数 |
|------|----------|
| agent_states.py | ~5 |
| schemas.py | ~15 |
| quality_reviewer.py | ~40 |
| question_generator.py | ~25 |
| setup.py | ~15 |
| conditional_logic.py | ~15 |
| final_editor.py | ~5 |
| **合计** | **~120 行** |
