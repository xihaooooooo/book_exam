# 质检重试闭环计划

## 目标

让质检从"形同虚设"变成有效的把关——不合格的题退回生成器重做，而不是无视 verdict 直接放行。

## 当前问题

- `QualityReview.verdict` 有三个值：`pass` / `fixed` / `rejected`
- 但 `quality_reviewer_node` 完全不读 verdict，无条件 `return {"all_questions": [reviewed]}`
- 子图边是 `quality_reviewer → END`，直通到底
- 结论：LLM 填了 verdict 但没人看，`rejected` 的题照样进最终试卷

## 方案：两档制（pass / fail）

审稿员只做裁判，不兼职编辑。

| verdict | 行为 |
|---------|------|
| `pass` | 题目入库，流程结束 |
| `fail` | 不入库，带上 `issues` 回退生成器重做，最多重试 2 次；超限强制放行 |

## 改动

### 改动 1：`_keep_first` 修复（agent_states.py，1 行）

当前：
```python
generated_question: Annotated[dict | None, _keep_first]
```

`_keep_first` 逻辑是 `return a if a else b`（旧值非空就保留旧值）。重试时生成器产出新题，写入 `generated_question` 时被 `_keep_first` 丢弃——审稿员永远审同一道旧题，闭环废了。

修法：去掉 reducer，直接覆盖。子图内生成器→审稿员串行，无并发冲突。

```python
generated_question: dict | None
```

同时新增两个字段：
```python
retry_count: int = 0
review_feedback: str = ""
```

### 改动 2：Schema + 审稿员逻辑（schemas.py + quality_reviewer.py，~35 行）

**schemas.py** — `QualityReview`：
- `verdict` 改为 `Literal["pass", "fail"]`
- 删除 `stem/option_a~d/correct_answer/explanation`（审稿员不再输出修正后题目）
- `issues` 保留默认空字符串即可（结构化输出下 LLM 几乎不会留空，万一空了后面用 `or` 兜底）

**quality_reviewer.py** — 重写节点逻辑：
- prompt 去掉 "小问题直接修正（verdict=fixed）"、"输出修正后的完整题目"，改为 "只做裁判，不做编辑"
- 分支处理：`pass` → 写 `all_questions`；`fail` → 不写题，设 `review_feedback`，`retry_count += 1`
- 加一行兜底：`feedback = result.issues.strip() or "审稿员认为题目不合格，请重新改进"`
- 加 2 个 `print`：pass 时 "✓ 通过" / fail 时 "✗ 不通过（原因 + 重试次数）"

### 改动 3：子图加重试环路（setup.py + conditional_logic.py，~15 行）

**setup.py** — `quality_reviewer` 不再直连 END，改为条件边：
- pass → END
- fail 且 retry_count < 2 → 回到对应生成器
- fail 且 retry_count >= 2 → END（强制放行）
- Send payload 显式带 `retry_count: 0, review_feedback: ""`

**conditional_logic.py** — 新增 `route_after_review`：
- 复用已有的 `route_by_question_type` 做重试路由，不重复代码
- 判断逻辑：有 `review_feedback` 且未超限 → 路由到生成器；否则 → END

### 改动 4：生成器读 feedback（question_generator.py，5 × 2 行）

5 个生成器开头各加 2 行：
```python
fb = state.get("review_feedback", "")
if fb:
    prompt = prompt.partial(extra_feedback=f"上一版未通过审稿，原因：{fb}。请针对这些问题重新出题。")
```

不做公共函数——2 行代码不值得多一层抽象。

### 改动 5：invoke_structured 异常兜底（structured.py，~15 行）

改 `invoke_structured` 本身，解析失败时 catch 异常返回 fallback 对象而不是崩全场。一次改动所有调用者受益。

## 不改的部分

- 主编排 `exam_graph.py` 不变
- knowledge_extractor 不变
- final_editor 不变
- 不新建公共函数
- 不搞 messages 清理（生成器和审稿员是纯 prompt 调用，不读 state 中的消息历史，不会累积）
- 不加 `review_status` 字段和 Markdown 标记（你自己用的工具，看 JSON 就行）

## 边界情况

- **重试上限**：`retry_count >= 2` 强制放行，避免死循环
- **issues 为空**：`or` 兜底一句话
- **并发安全**：子图内串行，字段无 reducer 也不冲突
