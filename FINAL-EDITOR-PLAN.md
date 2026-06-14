# 终审排版师 实现计划

## 目标

将 `final_editor.py` 从空壳实现为终审排版节点，负责：难度统计 → 排序 → 排版。**纯代码实现，不调用 LLM。**

---

## 涉及文件

| 文件 | 改动 |
|---|---|
| `exam/agents/reviewers/final_editor.py` | **核心**：实现排序、统计、排版逻辑 |
| `exam/graph/setup.py` | 插入 `final_editor` 节点到主图 |
| `exam/graph/exam_graph.py` | 简化 `_export_markdown`，由终审负责排版 |
| `exam/agents/schemas.py` | 无需改动 |

---

## 实现步骤

### Step 1 — 实现 `final_editor.py`

函数签名保持现有模式：`create_final_editor(config)` 返回 `final_editor_node(state)`。

#### 1.1 难度统计

- 从 `state["all_questions"]` 统计易/中/难各多少道
- 从 `state["exam_plan"]` 获取目标比例
- 计算偏差，生成统计报告文本
- 偏差 >20% 时在控制台打印警告

#### 1.2 排序

三级排序规则：
1. **章节顺序**：按题目所属 `source_section`（如 "2.1"）在 TOC 中的出现顺序
2. **题型优先级**：选择题 > 填空题 > 简答题
3. **难度**：易 > 中 > 难

用 `sort(key=lambda q: (chapter_order, type_rank, diff_rank))` 一行搞定。

#### 1.3 排版

生成 `final_exam` 字符串（Markdown 格式）：

```
# 《书本标题》测试卷

> 共 X 道题 | 易:中:难 = a:b:c

## 一、选择题（每题 X 分，共 XX 分）
1. (易) 题干...
   A. ...  B. ...  C. ...  D. ...

## 二、填空题（每题 X 分，共 XX 分）
6. (中) 题干...

## 三、简答题（每题 X 分，共 XX 分）
11. (难) 题干...

---

# 参考答案

## 一、选择题
1. C | 解析: ...

## 二、填空题
6. answer | 解析: ...

## 三、简答题
11. 参考答案: ... | 评分要点: ...
```

---

### Step 2 — 接入图（`setup.py`）

当前主图结尾：
```
generation_pipeline → END
```

改为：
```
generation_pipeline → final_editor → END
```

---

### Step 3 — 简化 `exam_graph.py`

- 删掉 `ExamGraph._export_markdown`（排版逻辑移到终审）
- `_save_results` 改为直接写入 `final_exam` 的值到 `.md` 文件
- 如果 `final_exam` 为空（兜底），回退到简单的 JSON dump

---

## 数据流

```
all_questions (排序前)
    │
    ▼
[难度统计]  →  控制台输出 + 报告文本
    │
    ▼
[代码排序]  →  sorted_questions（更新 all_questions 顺序）
    │
    ▼
[排版输出]  →  final_exam (Markdown 字符串)
    │
    ▼
exam_graph.py 写入 .md 文件
```

---

## 降级处理

| 情况 | 处理 |
|---|---|
| `all_questions` 为空 | `final_exam` = "暂无题目生成"，不崩溃 |
| `exam_plan` 为 None | 用默认比例 (3,4,3) |
| 题目缺 `source_section` | 排序时当空字符串处理，排到最后 |
| 题型字段缺失 | 按 "short_answer" 处理 |

---

## 验证方式

1. 运行 `python main.py`，检查控制台是否打印难度统计
2. 查看输出的 `exam_*.md`：题目是否按章节→题型→难度排列
3. 对比改动前后的题目顺序
